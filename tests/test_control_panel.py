"""操作パネル (tools/control_panel) のテスト: 設定ファイルの読み込み、状態の組み立て (擬似プローブ + 一時ディレクトリの
マーカー・対戦ログ・registry・server ログ)、固定の操作表 (未知の操作・不正な Package id を弾く)、ジョブの逐次実行と
再起動後の前回出力、HTTP の往復 (ヘッダ無しの POST は弾く)。

    python -m tests.test_control_panel
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools import control_panel as CP

PKG = "package-0123456789abcdef"
RETIRED = "package-fedcba9876543210"
LAN_IP = "192.0.2.10"


class FakeProbe:
    def __init__(self, listeners=None, procs=None):
        self.listeners = listeners or {}
        self.procs = procs or {}

    def listener(self, port):
        return self.listeners.get(port)

    def pgrep(self, pattern):
        return self.procs.get(pattern, [])

    def lan_ip(self):
        return LAN_IP


def settings() -> CP.Settings:
    return CP.Settings(port=0, bind="127.0.0.1", poll_sec=1.0, tail_lines=50, path_prefix="", server_log_tail_bytes=4096,
                       advisor_port=8000, frontend_port=3000, showdown_port=8100, smoke_battles=20)


def _battle(path: Path, outcome, n_scenes: int = 3, mtime: float = None) -> None:
    t = time.time()
    rows = [{"type": "session", "source": "experiment", "package_id": PKG, "t": t}]
    rows += [{"type": "scene", "scene": "command", "t": t + i, "state": {}} for i in range(n_scenes)]
    if outcome:
        rows.append({"type": "outcome", "outcome": outcome, "t": t + n_scenes})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _row(pid: str, status: str, history: list, created: str = "2026-09-19 19:43:11") -> dict:
    return {"id": pid, "kind": "package", "status": status, "created": created, "run_id": "arch_0918",
            "meta": {"candidate_id": "L69_C029", "species": ["basculegion", "cinderace"], "direction_ja": "ふいうち軸",
                     "holdout": {"verdict": "PASS", "delta": 0.17}}, "history": history}


def make_root(d: Path) -> Path:
    (d / "config").mkdir()
    (d / "logs" / "battles").mkdir(parents=True)
    (d / "logs" / "registry").mkdir()
    marker = time.time() - 60
    (d / "logs" / ".connection_test_start").write_text(str(marker), encoding="utf-8")
    (d / "logs" / ".experiment_package").write_text(PKG + "\n", encoding="utf-8")
    (d / "logs" / "ports.env").write_text("ADVISOR_PORT=8000\nFRONTEND_PORT=3001\n", encoding="utf-8")
    rows = [_row(PKG, "candidate", ["candidate"]),
            _row(PKG, "canary", ["candidate", "validation", "canary"]),          # 同じ id は最後の行が最新
            _row(RETIRED, "retired", ["candidate", "retired"], created="2026-09-10 00:00:00"),
            {"id": "selection_model-0000000000000000", "kind": "selection_model", "status": "candidate",
             "created": "2026-09-19 00:00:00", "meta": {}, "history": ["candidate"]}]
    (d / "logs" / "registry" / "index.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    _battle(d / "logs" / "battles" / "battle_20260923_000000.jsonl", "win", mtime=marker - 100)   # マーカーより前
    _battle(d / "logs" / "battles" / "battle_20260923_000001.jsonl", "win")
    _battle(d / "logs" / "battles" / "battle_20260923_000002.jsonl", "loss")
    _battle(d / "logs" / "battles" / "battle_20260923_000003.jsonl", None, n_scenes=1)             # 断片 (対戦とみなさない)
    (d / "logs" / "server_nohup.log").write_text(
        "[server] scene=field 受信=100 処理=60 破棄=40 救出=1\n[server] scene=field 受信=200 処理=150 破棄=50 救出=2\nINFO: done\n",
        encoding="utf-8")
    return d


def test_read_env_file_and_settings():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "config").mkdir()
        (d / "config" / "ports.env").write_text(
            "# コメント\nADVISOR_PORT_DEFAULT=8000\nFRONTEND_PORT_DEFAULT=3000\nSHOWDOWN_PORT_DEFAULT=8100\nCONTROL_PORT_DEFAULT=8010\n",
            encoding="utf-8")
        (d / "config" / "control_panel.env").write_text(
            'CONTROL_BIND="127.0.0.1"\nCONTROL_POLL_SEC=2.5\n\nCONTROL_LOG_TAIL_LINES=80\n'
            "CONTROL_PATH_PREFIX=/opt/homebrew/bin\nCONTROL_SERVER_LOG_TAIL_BYTES=1024\n", encoding="utf-8")
        s = CP.Settings.load(d)
        assert (s.port, s.bind, s.poll_sec, s.tail_lines, s.path_prefix, s.server_log_tail_bytes) == \
            (8010, "127.0.0.1", 2.5, 80, "/opt/homebrew/bin", 1024)
        assert (s.advisor_port, s.frontend_port, s.showdown_port, s.smoke_battles) == (8000, 3000, 8100, 20)
        assert CP.read_env_file(d / "config" / "none.env") == {}
        (d / "config" / "control_panel.env").write_text("CONTROL_BIND=0.0.0.0\n", encoding="utf-8")
        try:
            CP.Settings.load(d)
            raise AssertionError("設定の欠落を通した")
        except SystemExit as e:
            assert "CONTROL_POLL_SEC" in str(e)
    print("test_read_env_file_and_settings OK")


def test_status_from_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(Path(tmp))
        probe = FakeProbe(
            listeners={8000: {"pid": 11, "command": "python uvicorn server:app_asgi --host 0.0.0.0 --port 8000"},
                       3001: {"pid": 12, "command": "python3 -m http.server 3001"},
                       8100: {"pid": 13, "command": "node pokemon-showdown start 8100 --no-security"}},
            procs={CP.MEASURING_PATTERN: ["123 python -m tools.team_build.run --run-id x"]})
        st = CP.Panel(root, settings(), probe=probe).status()
        pr = st["processes"]
        assert pr["advisor"] == {"state": "ours", "port": 8000, "pid": 11}
        assert pr["frontend"] == {"state": "ours", "port": 3001, "pid": 12}        # 前回ずらしたポート (logs/ports.env) を実測
        assert pr["showdown"]["state"] == "ours" and pr["training"] is False and pr["measuring"] is True
        assert st["urls"] == {"frontend": f"http://{LAN_IP}:3001", "frontend_local": "http://localhost:3001"}
        t = st["test"]
        assert t["active"] is True and t["target"] == 20
        assert t["battles"] == {"n": 2, "decided": 2, "wins": 1, "losses": 1, "last_file": "battle_20260923_000002.jsonl"}
        assert st["frames"] == {"received": 200, "processed": 150, "dropped": 50, "drop_rate": 0.25}
        e = st["experiment"]
        assert e["package_id"] == PKG and e["package"]["status"] == "canary" and e["package"]["candidate_id"] == "L69_C029"
        assert e["package"]["history"] == ["candidate", "validation", "canary"]
        assert e["package"]["species_ja"] == ["イダイトウ", "エースバーン"]           # 表引き (advisor.ja_names)
        assert [p["id"] for p in st["packages"]] == [PKG]                          # retired と selection_model は候補に出ない
        assert any("測定" in w for w in st["warnings"]) and not any("マーカー" in w for w in st["warnings"])
        assert st["job"] is None

        # 何も動いていない + マーカー無し + 既定ポートを別のプロセスが使用中
        (root / "logs" / ".connection_test_start").unlink()
        (root / "logs" / ".experiment_package").unlink()
        st = CP.Panel(root, settings(), probe=FakeProbe(listeners={3000: {"pid": 9, "command": "node next dev"}})).status()
        assert st["processes"]["advisor"] == {"state": "free", "port": 8000, "pid": None}
        assert st["processes"]["frontend"] == {"state": "foreign", "port": 3000, "pid": 9}
        assert st["urls"] == {"frontend": None, "frontend_local": None}            # 停止中は別のプロセスの URL を出さない
        assert st["test"] == {"active": False, "started": None, "target": 20, "battles": None}
        assert st["experiment"] == {"package_id": None, "package": None} and st["warnings"] == []

        # マーカーがあるのにアドバイザーが止まっている → 警告
        (root / "logs" / ".connection_test_start").write_text(str(time.time()), encoding="utf-8")
        st = CP.Panel(root, settings(), probe=FakeProbe()).status()
        assert any("マーカー" in w for w in st["warnings"])
    print("test_status_from_files OK")


def test_actions_are_fixed():
    with tempfile.TemporaryDirectory() as tmp:
        p = CP.Panel(make_root(Path(tmp)), settings(), probe=FakeProbe())
        for action, pkg in [("rm -rf /", None), ("", None), ("experiment_on", None), ("experiment_on", "bad id"),
                            ("experiment_on", "package-0000000000000000")]:            # registry に無い
            try:
                p.run(action, pkg)
                raise AssertionError(f"通してしまった: {action} {pkg}")
            except ValueError:
                pass
        assert p.runner.argv("experiment_on", PKG) == ["bash", "scripts/experiment_label.sh", "on", PKG]
        assert p.runner.argv("end_no_audit") == ["bash", "scripts/end_connection_test.sh", "--no-audit"]
        assert p.runner.argv("start", "package-0000000000000000") == ["bash", "scripts/start_connection_test.sh"]  # 不要な id は無視
        for act in CP.ACTIONS.values():
            assert act.argv[0] == "bash" and act.argv[1].startswith("scripts/")
    print("test_actions_are_fixed OK")


def _wait_done(runner: CP.JobRunner, timeout: float = 10.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = runner.snapshot(50)
        if snap and not snap["running"]:
            return snap
        time.sleep(0.1)
    raise AssertionError("ジョブが終わらない")


def test_jobs_run_one_at_a_time():
    actions = {"slow": CP.Action("遅い", ("bash", "-c", "echo start; sleep 1; echo done")),
               "fail": CP.Action("失敗", ("bash", "-c", "echo boom; exit 3"))}
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(Path(tmp))
        p = CP.Panel(root, settings(), probe=FakeProbe(), actions=actions)
        j = p.run("slow")
        assert j["running"] and j["action"] == "slow" and j["label"] == "遅い"
        try:
            p.run("fail")
            raise AssertionError("同時実行を許した")
        except CP.JobBusy:
            pass
        snap = _wait_done(p.runner)
        assert snap["rc"] == 0 and snap["tail"].splitlines() == ["start", "done"]
        assert Path(snap["log"]).name.endswith(f"{CP.LOG_NAME_SEP}slow.log") and Path(snap["log"]).parent == root / "logs" / "control_panel"
        time.sleep(1.1)                                    # ログ名の秒が変わるまで待つ
        p.run("fail")
        snap = _wait_done(p.runner)
        assert snap["rc"] == 3 and "boom" in snap["tail"]
        # パネルを作り直しても (launchd の再起動後でも) 直前の出力が読める
        p2 = CP.Panel(root, settings(), probe=FakeProbe(), actions=actions)
        snap = p2.runner.snapshot(50)
        assert snap["action"] == "fail" and snap["running"] is False and snap["previous"] is True and "boom" in snap["tail"]
    print("test_jobs_run_one_at_a_time OK")


def _post(base: str, body: dict, headers: dict) -> tuple:
    req = urllib.request.Request(base + "/api/run", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_http_roundtrip():
    actions = {"echo": CP.Action("echo", ("bash", "-c", "echo hello"))}
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(Path(tmp))
        panel = CP.Panel(root, settings(), probe=FakeProbe(), actions=actions)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), CP.make_handler(panel))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            html = urllib.request.urlopen(base + "/").read().decode("utf-8")
            assert "接続テスト開始" in html and "__POLL_MS__" not in html and "1000" in html
            st = json.loads(urllib.request.urlopen(base + "/api/status").read().decode("utf-8"))
            assert st["test"]["active"] is True and st["packages"][0]["id"] == PKG
            code, j = _post(base, {"action": "echo"}, {})                       # ヘッダ無し → 拒否
            assert code == 403, (code, j)
            code, j = _post(base, {"action": "nope"}, {CP.POST_HEADER: "1"})     # 未知の操作
            assert code == 400 and "未知" in j["error"]
            code, j = _post(base, {"action": "echo"}, {CP.POST_HEADER: "1"})
            assert code == 200 and j["job"]["action"] == "echo" and j["job"]["running"] is True
            snap = _wait_done(panel.runner)
            assert snap["rc"] == 0 and snap["tail"] == "hello"
            st = json.loads(urllib.request.urlopen(base + "/api/status").read().decode("utf-8"))
            assert st["job"]["action"] == "echo" and st["job"]["running"] is False and st["job"]["tail"] == "hello"
            assert urllib.request.urlopen(base + "/api/status").headers.get("Cache-Control") == "no-store"
            try:
                urllib.request.urlopen(base + "/nope")
                raise AssertionError("404 にならない")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()
    print("test_http_roundtrip OK")


def main() -> None:
    test_read_env_file_and_settings()
    test_status_from_files()
    test_actions_are_fixed()
    test_jobs_run_one_at_a_time()
    test_http_roundtrip()
    print("\nALL OK")


if __name__ == "__main__":
    main()
