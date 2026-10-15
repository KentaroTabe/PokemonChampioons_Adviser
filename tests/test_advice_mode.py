"""実機の採点方式の設定 (方式 C: config/advice.local.env、tools/advice_mode.py + scripts/advice_env.sh。2026-10-16) のテスト。

- 解決の規則 (純粋): 未設定 → legacy / default、ファイルのみ → file、環境変数のみ → env、両方一致 → 続行 (file)、
  矛盾・不正値・認めない鍵・構文の誤り・重複 → エラー
- 表示 (起動ログの 1 行、操作パネルの設定値・設定元・食い違い)、server の起動時の検査 (不正値は SystemExit)
- champions_agent/config.py はファイルを読まない (環境変数だけが効く)。測定の道具・学習はこの設定を参照しない
- 起動スクリプトの写し (uvicorn・lsof・pkill 等をダミーに差し替えた砂場) で、値が uvicorn に届くこと・不正なら起動しないこと・
  deploy.sh は不正なら止める前に中止すること・同じスクリプトが起こす学習ループには渡らないこと (常駐は起動しない)

    python -m tests.test_advice_mode
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from tools import advice_mode as AM
from tools import control_panel as CP

REPO = Path(__file__).resolve().parent.parent
START_SCRIPTS = ("start_connection_test.sh", "start_all_nohup.sh", "start_servers.sh", "deploy.sh")


def _err(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except AM.AdviceModeError as e:
        return str(e)
    raise AssertionError(f"エラーにならなかった: {fn.__name__}{a}")


# ------------------------------------------------------------------ 純粋な部分
def test_resolve_rules():
    r = AM.resolve
    assert r(None, None) == {"mode": "legacy", "source": "default", "file_mode": None, "env_mode": None}
    assert r("", "")["source"] == "default"                                              # 空のファイル・空の環境変数 = 未設定
    assert r("# コメントだけ\n\n", None)["mode"] == "legacy"
    assert r("ADVICE_SCORING_MODE=\n", None)["source"] == "default"                      # 空の値 = 未設定
    assert r("ADVICE_SCORING_MODE=transition\n", None)["mode"] == "transition"
    assert r("ADVICE_SCORING_MODE=transition\n", None)["source"] == "file"
    assert r(None, "transition") == {"mode": "transition", "source": "env", "file_mode": None, "env_mode": "transition"}
    assert r(None, " Legacy ")["mode"] == "legacy"                                        # config と同じ正規化
    assert r("ADVICE_SCORING_MODE=transition", "transition")["source"] == "file"         # 一致 → 続行
    assert r("ADVICE_SCORING_MODE='transition'\n", None)["mode"] == "transition"
    # 矛盾 → エラー (黙って片方を採用しない)
    e = _err(r, "ADVICE_SCORING_MODE=transition\n", "legacy")
    assert "矛盾" in e and "transition" in e and "legacy" in e, e
    assert "矛盾" in _err(r, "ADVICE_SCORING_MODE=legacy\n", "transition")
    # 不正値 → エラー (ファイル・環境変数とも)
    assert "fast" in _err(r, "ADVICE_SCORING_MODE=fast\n", None)
    assert "環境変数" in _err(r, None, "fast")
    assert "fast" in _err(r, "ADVICE_SCORING_MODE=fast\n", "fast")
    # 認めない鍵・構文の誤り・重複 → エラー
    assert "認めない鍵" in _err(r, "ADVICE_SCORING_MODE=legacy\nRL_ADVICE_STYLE=balance\n", None)
    assert "認めない鍵" in _err(r, "CHAMPIONS_MODELS_DIR=/x\n", None)
    assert "KEY=VALUE" in _err(r, "export ADVICE_SCORING_MODE=transition\n", None)
    assert "KEY=VALUE" in _err(r, "transition\n", None)
    assert "2 回" in _err(r, "ADVICE_SCORING_MODE=legacy\nADVICE_SCORING_MODE=legacy\n", None)
    assert ":2:" in _err(r, "# x\nFOO=1\n", None)                                          # 行番号を出す
    print("test_resolve_rules OK")


def test_quoted_and_commented_value():
    # 値の後ろのコメントは値の一部として扱わない形式なので、不正値としてエラーにする (曖昧な書き方を黙って通さない)
    assert "使えない値" in _err(AM.resolve, "ADVICE_SCORING_MODE=transition # 試験中\n", None)
    assert AM.parse_local_env('ADVICE_SCORING_MODE="transition"') == {"ADVICE_SCORING_MODE": "transition"}
    assert AM.resolve('ADVICE_SCORING_MODE="Transition"', "transition")["mode"] == "transition"
    print("test_quoted_and_commented_value OK")


def test_views_and_startup_line():
    v_none = AM.file_view(None)
    assert v_none == {"path": "config/advice.local.env", "exists": False, "mode": None, "configured": "legacy", "error": None}
    v_tr = AM.file_view("ADVICE_SCORING_MODE=transition\n")
    assert v_tr["mode"] == v_tr["configured"] == "transition" and v_tr["exists"]
    v_bad = AM.file_view("ADVICE_SCORING_MODE=fast\n")
    assert v_bad["configured"] is None and "fast" in v_bad["error"]
    assert AM.file_view("# なし\n")["configured"] == "legacy"
    assert not AM.mismatch(v_tr, "transition") and AM.mismatch(v_tr, "legacy") and AM.mismatch(v_none, "transition")
    assert not AM.mismatch(v_tr, None) and not AM.mismatch(v_bad, "legacy")
    assert AM.source_of_env({}) == "default"
    assert AM.source_of_env({"ADVICE_SCORING_MODE": "transition"}) == "env"
    assert AM.source_of_env({"ADVICE_SCORING_MODE": "transition", "ADVICE_SCORING_MODE_SOURCE": "file"}) == "file"
    assert AM.source_of_env({"ADVICE_SCORING_MODE": "", "ADVICE_SCORING_MODE_SOURCE": "bogus"}) == "default"
    line = AM.startup_line("transition", "file", v_tr)
    assert line == "採点方式: 実効値 transition / 設定元 file / 設定値 transition (config/advice.local.env)", line
    assert "⚠" in AM.startup_line("legacy", "default", v_tr)                             # ファイルを変えたが通さずに起動
    assert "未設定 = legacy" in AM.startup_line("legacy", "default", v_none) and "⚠" not in AM.startup_line("legacy", "default", v_none)
    assert "読めない" in AM.startup_line("legacy", "default", v_bad)
    print("test_views_and_startup_line OK")


def test_server_startup_check():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "advice.local.env"
        assert "実効値 legacy / 設定元 default" in AM.server_startup_check({}, p)
        p.write_text("ADVICE_SCORING_MODE=transition\n", encoding="utf-8")
        ok = AM.server_startup_check({"ADVICE_SCORING_MODE": "transition", "ADVICE_SCORING_MODE_SOURCE": "file"}, p)
        assert "実効値 transition / 設定元 file" in ok and "⚠" not in ok, ok
        # ファイルは表示だけ: 環境変数が無ければ実効値は legacy のまま (食い違いを警告)
        assert "実効値 legacy" in AM.server_startup_check({}, p) and "⚠" in AM.server_startup_check({}, p)
        try:
            AM.server_startup_check({"ADVICE_SCORING_MODE": "fast"}, p)
            raise AssertionError("不正値で止まらない")
        except SystemExit as e:
            assert "fast" in str(e)
    print("test_server_startup_check OK")


def test_cli_resolve():
    py = sys.executable
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "a.env"
        env = {k: v for k, v in os.environ.items() if not k.startswith("ADVICE_SCORING_MODE")}
        env["PYTHONPATH"] = str(REPO)
        run = lambda e: subprocess.run([py, "-m", "tools.advice_mode", "--resolve", "--file", str(f)], cwd=str(REPO),
                                       env=e, capture_output=True, text=True)
        out = run(env)
        assert out.returncode == 0 and out.stdout.splitlines() == ["ADVICE_SCORING_MODE=legacy",
                                                                    "ADVICE_SCORING_MODE_SOURCE=default"], out
        f.write_text("ADVICE_SCORING_MODE=transition\n", encoding="utf-8")
        assert run(env).stdout.splitlines()[-1] == "ADVICE_SCORING_MODE_SOURCE=file"
        bad = run(dict(env, ADVICE_SCORING_MODE="legacy"))
        assert bad.returncode == 2 and "矛盾" in bad.stderr and "ADVICE_SCORING_MODE=" not in bad.stdout, bad
    print("test_cli_resolve OK")


# ------------------------------------------------------------------ 測定の子プロセス・config に持ち込まないこと
def test_config_does_not_read_file():
    """config.py はファイルを読まない (静的: 参照が無い / 動的: import 中に開いたファイルに無い)。環境変数だけが効く"""
    src = (REPO / "champions_agent" / "config.py").read_text(encoding="utf-8")
    assert "advice.local" not in src and "advice_mode" not in src
    probe = ("import sys\n"
             "opened = []\n"
             "sys.addaudithook(lambda ev, args: opened.append(str(args[0])) if ev == 'open' and args else None)\n"
             "import champions_agent.config as c\n"
             "print(c.ADVICE_SCORING_MODE)\n"
             "print(any('advice.local' in p for p in opened))\n")
    base = {k: v for k, v in os.environ.items() if not k.startswith("ADVICE_SCORING_MODE")}
    for env_val, want in ((None, "legacy"), ("transition", "transition"), ("legacy", "legacy")):
        e = dict(base) if env_val is None else dict(base, ADVICE_SCORING_MODE=env_val)
        out = subprocess.run([sys.executable, "-c", probe], cwd=str(REPO), env=e, capture_output=True, text=True)
        lines = out.stdout.strip().splitlines()
        assert out.returncode == 0 and lines[-2:] == [want, "False"], (env_val, out.stdout, out.stderr)
    print("test_config_does_not_read_file OK")


def test_only_start_scripts_use_advice_env():
    """実機用の設定を読むのは 4 本の起動スクリプトだけ (end_connection_test・学習・測定の道具は通さない)"""
    users = set()
    for p in list((REPO / "scripts").rglob("*.sh")) + list((REPO / "champions_agent").rglob("*.sh")):
        if p.name != "advice_env.sh" and "advice_env.sh" in p.read_text(encoding="utf-8", errors="replace"):
            users.add(p.name)
    assert users == set(START_SCRIPTS), users
    for mod in ("usefulness_p1.py", "arc_remeasure.py", "check_advisor_player.py"):
        s = (REPO / "tools" / mod).read_text(encoding="utf-8")
        assert "advice_mode" not in s and "advice.local" not in s, mod
    print("test_only_start_scripts_use_advice_env OK")


def test_start_scripts_syntax():
    for name in START_SCRIPTS + ("advice_env.sh",):
        out = subprocess.run(["bash", "-n", str(REPO / "scripts" / name)], capture_output=True, text=True)
        assert out.returncode == 0, (name, out.stderr)
    print("test_start_scripts_syntax OK")


# ------------------------------------------------------------------ 起動スクリプトの写し (砂場)
STUB = "#!/bin/sh\n{body}\n"


class Sandbox:
    """起動スクリプトの写しを動かす一時ディレクトリ。uvicorn・lsof・ps・pgrep・pkill・node・python3・sleep・学習ループは
    ダミー (PATH の先頭)。uvicorn と学習ループは受け取った ADVICE_SCORING_MODE* をファイルに書いて終わる。常駐は起動しない"""

    def __init__(self, td: Path, advisor_running: bool = False, training_mode: str = "on_demand"):
        self.root = td
        sc = td / "scripts"
        (sc / "lib").mkdir(parents=True)
        for name in START_SCRIPTS + ("advice_env.sh",):
            shutil.copy(REPO / "scripts" / name, sc / name)
        shutil.copy(REPO / "scripts" / "lib" / "ports.sh", sc / "lib" / "ports.sh")
        (td / "config").mkdir()
        shutil.copy(REPO / "config" / "ports.env", td / "config" / "ports.env")
        (td / "config" / "training.env").write_text(f"TRAINING_MODE={training_mode}\n", encoding="utf-8")
        (td / "logs").mkdir()
        (td / "champions_agent" / "scripts").mkdir(parents=True)
        (td / "champions_agent" / "train" / "logs").mkdir(parents=True)
        self.bin = td / "stubbin"
        self.bin.mkdir()
        self.calls = td / "calls.txt"
        rec = f'echo "$(basename "$0") $*" >> "{self.calls}"'
        envdump = 'env | grep "^ADVICE_SCORING_MODE" | sort > "{out}.tmp"; mv "{out}.tmp" "{out}"'
        stubs = {
            "uvicorn": rec + "\n" + envdump.format(out=td / "uvicorn_env.txt"),
            "pkill": rec,
            "pgrep": rec + "\nexit 1",
            "node": rec,
            "python3": rec,
            "sleep": ":",
            "ipconfig": "echo 192.0.2.1",
            # 待ち受け: advisor_running なら 8000 を「自分たち」(cwd = 砂場) が待ち受けている
            "lsof": (rec + "\ncase \"$*\" in\n"
                     + (f'  *-iTCP:8000*) echo 99999;;\n  *"-d cwd"*) echo "n{td.resolve()}";;\n' if advisor_running else "")
                     + "  *) :;;\nesac"),
            "ps": 'echo "python uvicorn server:app_asgi"',
        }
        for name, body in stubs.items():
            p = self.bin / name
            p.write_text(STUB.format(body=body), encoding="utf-8")
            p.chmod(0o755)
        venv = td / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text(f'export PATH="{self.bin}:$PATH"\n', encoding="utf-8")
        tf = td / "champions_agent" / "scripts" / "train_forever.sh"
        tf.write_text(STUB.format(body=envdump.format(out=td / "train_env.txt")), encoding="utf-8")

    def write_local(self, text: str) -> None:
        (self.root / "config" / "advice.local.env").write_text(text, encoding="utf-8")

    def env(self, extra: dict = None, minimal: bool = False) -> dict:
        """minimal = launchd と同じく PATH だけ (+ 判定用の python の指定)"""
        if minimal:
            e = {"PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin"}
        else:
            e = {k: v for k, v in os.environ.items() if not k.startswith("ADVICE_SCORING_MODE")}
            e["PATH"] = f"{self.bin}:{e.get('PATH', '/usr/bin:/bin')}"
        e["ADVICE_ENV_PYTHON"] = sys.executable
        e["PYTHONPATH"] = str(REPO)
        e.update(extra or {})
        return e

    def run(self, script: str, *args, env: dict) -> subprocess.CompletedProcess:
        # start_servers.sh の終了処理は kill 0 (プロセスグループごと) なので、別セッションで動かす
        # 出力は replace で読む (start_connection_test.sh の表示に途中で切れたマルチバイト文字が混ざることがある)
        return subprocess.run(["bash", f"scripts/{script}", *args], cwd=str(self.root), env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60, start_new_session=True)

    def received(self, name: str = "uvicorn_env.txt", wait: float = 10.0) -> dict:
        p = self.root / name
        end = time.time() + wait
        while not p.exists() and time.time() < end:
            time.sleep(0.05)
        if not p.exists():
            return {}
        return dict(ln.split("=", 1) for ln in p.read_text(encoding="utf-8").splitlines() if "=" in ln)

    def called(self, prog: str) -> bool:
        return self.calls.exists() and any(ln.split(" ", 1)[0] == prog for ln in self.calls.read_text().splitlines())


def _sandbox_case(fn):
    with tempfile.TemporaryDirectory() as td:
        return fn(Path(td))


def test_start_all_nohup_passes_value_only_to_uvicorn():
    def case(td):
        sb = Sandbox(td, training_mode="continuous")
        sb.write_local("ADVICE_SCORING_MODE=transition\n")
        out = sb.run("start_all_nohup.sh", env=sb.env())
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert sb.received() == {"ADVICE_SCORING_MODE": "transition", "ADVICE_SCORING_MODE_SOURCE": "file"}, out.stdout
        # 同じスクリプトが起こす学習ループには実機用の設定を持ち込まない
        assert sb.received("train_env.txt") == {}, (td / "train_env.txt").read_text()
        assert (td / "train_env.txt").exists()
        assert "採点方式: transition (設定元 file)" in out.stdout
    _sandbox_case(case)
    print("test_start_all_nohup_passes_value_only_to_uvicorn OK")


def test_start_all_nohup_refuses_bad_config():
    def case(td):
        sb = Sandbox(td)
        sb.write_local("ADVICE_SCORING_MODE=transition\n")
        out = sb.run("start_all_nohup.sh", env=sb.env({"ADVICE_SCORING_MODE": "legacy"}))       # 矛盾
        assert out.returncode != 0 and "矛盾" in out.stderr, (out.stdout, out.stderr)
        assert not sb.called("uvicorn") and sb.received(wait=0.3) == {}
    _sandbox_case(case)

    def case2(td):
        sb = Sandbox(td)
        sb.write_local("ADVICE_SCORING_MODE=transition\nRL_ADVICE_STYLE=attack\n")               # 認めない鍵
        out = sb.run("start_all_nohup.sh", env=sb.env())
        assert out.returncode != 0 and "認めない鍵" in out.stderr and not sb.called("uvicorn"), out.stderr
    _sandbox_case(case2)
    print("test_start_all_nohup_refuses_bad_config OK")


def test_deploy_like_launchd():
    """daily-deploy / パネルの「更新の反映」と同じく PATH だけの環境で deploy.sh --force: ファイルの値が渡る"""
    def case(td):
        sb = Sandbox(td, advisor_running=True)
        sb.write_local("ADVICE_SCORING_MODE=transition\n")
        out = sb.run("deploy.sh", "--force", env=sb.env(minimal=True))
        assert out.returncode == 0 and "反映完了" in out.stdout, (out.stdout, out.stderr)
        assert sb.called("pkill")                                                         # ダミーの pkill が使われた
        assert sb.received() == {"ADVICE_SCORING_MODE": "transition", "ADVICE_SCORING_MODE_SOURCE": "file"}
    _sandbox_case(case)

    def case_none(td):
        sb = Sandbox(td, advisor_running=True)
        out = sb.run("deploy.sh", "--force", env=sb.env(minimal=True))
        assert out.returncode == 0, out.stderr
        assert sb.received() == {"ADVICE_SCORING_MODE": "legacy", "ADVICE_SCORING_MODE_SOURCE": "default"}
    _sandbox_case(case_none)

    def case_bad(td):
        sb = Sandbox(td, advisor_running=True)
        sb.write_local("ADVICE_SCORING_MODE=fast\n")
        out = sb.run("deploy.sh", "--force", env=sb.env(minimal=True))
        assert out.returncode == 1 and "止めずに中止" in out.stdout, (out.stdout, out.stderr)
        assert not sb.called("pkill") and not sb.called("uvicorn")                       # 止める前に中止
    _sandbox_case(case_bad)
    print("test_deploy_like_launchd OK")


def test_start_connection_test_and_start_servers():
    def case(td):
        sb = Sandbox(td)
        out = sb.run("start_connection_test.sh", env=sb.env({"ADVICE_SCORING_MODE": "transition"}))
        assert out.returncode == 0, (out.stdout, out.stderr)
        assert sb.received() == {"ADVICE_SCORING_MODE": "transition", "ADVICE_SCORING_MODE_SOURCE": "env"}
    _sandbox_case(case)

    def case_bad(td):
        sb = Sandbox(td)
        sb.write_local("ADVICE_SCORING_MODE=legacy\n")
        out = sb.run("start_connection_test.sh", env=sb.env({"ADVICE_SCORING_MODE": "transition"}))
        assert out.returncode == 1 and "矛盾" in out.stderr and not sb.called("node"), (out.stdout, out.stderr)
        assert not (td / "logs" / ".connection_test_start").exists()                      # 何もしないうちに止まる
    _sandbox_case(case_bad)

    def case_fg(td):
        sb = Sandbox(td)
        sb.write_local("# 試験\nADVICE_SCORING_MODE=transition\n")
        out = sb.run("start_servers.sh", env=sb.env({"ADVICE_SCORING_MODE": "transition"}))      # 一致 → 続行
        assert sb.received() == {"ADVICE_SCORING_MODE": "transition", "ADVICE_SCORING_MODE_SOURCE": "file"}, out.stderr
    _sandbox_case(case_fg)
    print("test_start_connection_test_and_start_servers OK")


# ------------------------------------------------------------------ 操作パネル
def test_control_panel_scoring_config():
    run_tr = CP.scoring_mode_from_ps("uvicorn server:app_asgi ADVICE_SCORING_MODE=transition ADVICE_SCORING_MODE_SOURCE=file")
    assert run_tr == {"mode": "transition", "env": "transition", "source": "env"}                 # 既存の判定は変えない
    v = CP.scoring_config_view("ADVICE_SCORING_MODE=transition\n",
                               "uvicorn server:app_asgi ADVICE_SCORING_MODE=transition ADVICE_SCORING_MODE_SOURCE=file", run_tr)
    assert v["file"]["configured"] == "transition" and v["running_source"] == "file" and not v["mismatch"]
    assert CP.scoring_config_warnings(v) == []
    # ファイルを変えたが再起動していない → 食い違い
    run_lg = CP.scoring_mode_from_ps("uvicorn server:app_asgi PATH=/bin")
    v2 = CP.scoring_config_view("ADVICE_SCORING_MODE=transition\n", "uvicorn server:app_asgi PATH=/bin", run_lg)
    assert v2["mismatch"] and v2["running_source"] is None
    assert "再起動" in CP.scoring_config_warnings(v2)[0]
    v3 = CP.scoring_config_view("ADVICE_SCORING_MODE=fast\n", "", None)
    assert not v3["mismatch"] and "読めません" in CP.scoring_config_warnings(v3)[0]
    v4 = CP.scoring_config_view(None, "", None, file_error="config/advice.local.env: Permission denied")
    assert v4["file"]["error"] and v4["file"]["configured"] is None
    assert CP.scoring_config_view(None, "", None) == {"file": AM.file_view(None), "running_source": None, "mismatch": False}

    class Probe:
        def listener(self, port):
            return {"pid": 11, "command": "python uvicorn server:app_asgi --port 8000"} if port == 8000 else None

        def pgrep(self, pattern):
            return []

        def lan_ip(self):
            return "localhost"

        def process_env(self, pid):
            return "python uvicorn server:app_asgi PATH=/bin"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "logs").mkdir()
        (root / "config").mkdir()
        s = CP.Settings(port=0, bind="127.0.0.1", poll_sec=1.0, tail_lines=10, path_prefix="", server_log_tail_bytes=1024,
                        advisor_port=8000, frontend_port=3000, showdown_port=8100, smoke_battles=20)
        st = CP.Panel(root, s, probe=Probe()).status()
        assert st["scoring"] == {"mode": "legacy", "env": None, "source": "default"}
        assert not st["scoring_config"]["mismatch"] and not any("採点方式" in w for w in st["warnings"])
        (root / "config" / "advice.local.env").write_text("ADVICE_SCORING_MODE=transition\n", encoding="utf-8")
        st = CP.Panel(root, s, probe=Probe()).status()
        assert st["scoring_config"]["mismatch"] and any("採点方式の設定値 (transition)" in w for w in st["warnings"])
    page = CP.render_page(s, CP.ACTIONS)
    assert "採点方式 (アドバイザー)" in page and "設定値 (ファイル)" in page and "設定元" in page
    print("test_control_panel_scoring_config OK")


if __name__ == "__main__":
    test_resolve_rules()
    test_quoted_and_commented_value()
    test_views_and_startup_line()
    test_server_startup_check()
    test_cli_resolve()
    test_config_does_not_read_file()
    test_only_start_scripts_use_advice_env()
    test_start_scripts_syntax()
    test_start_all_nohup_passes_value_only_to_uvicorn()
    test_start_all_nohup_refuses_bad_config()
    test_deploy_like_launchd()
    test_start_connection_test_and_start_servers()
    test_control_panel_scoring_config()
    print("ALL OK")
