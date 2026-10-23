"""実機の採点方式の設定 (方式 C: config/advice.local.env、tools/advice_mode.py + scripts/advice_env.sh。2026-10-16) のテスト。

- 解決の規則 (純粋): 未設定 → legacy / default、ファイルのみ → file、環境変数のみ → env、両方一致 → 続行 (file)、
  矛盾・不正値・認めない鍵・構文の誤り・重複 → エラー
- 表示 (起動ログの 1 行、操作パネルの設定値・設定元・食い違い)、server の起動時の検査 (不正値は SystemExit)
- champions_agent/config.py はファイルを読まない (環境変数だけが効く)。測定の道具・学習はこの設定を参照しない
- 起動スクリプトの写し (uvicorn・lsof・pkill 等をダミーに差し替えた砂場) で、値が uvicorn に届くこと・不正なら起動しないこと・
  deploy.sh は不正なら止める前に中止すること・同じスクリプトが起こす学習ループには渡らないこと (常駐は起動しない)
- 2026-10-18 transition の感度 (TRANSITION_SENSITIVITY、値 0 / 1): 解決の規則 (無ければ 0 = 実機の既定 off、ファイル 1 で on、
  矛盾・不正値はエラー)、CLI --resolve --sensitivity、起動ログ・操作パネルの設定値・実効値・設定元、4 本の起動スクリプトで
  uvicorn に同じ値と設定元が届き、学習ループには渡らないこと
- 2026-10-23 データの固定 (ADVICE_USAGE_SNAPSHOT / ADVICE_REAL_BANK / ADVICE_REAL_BANK_SHA16): 解決の規則 (正常・不正値・矛盾・
  sha16 だけ)、CLI --resolve --data-pin (固定したデータの起動前の検査を含む)、4 本の起動スクリプトで値のある鍵だけが uvicorn に
  届き学習ループには漏れないこと、検査に通らなければ起動しない (deploy.sh は止める前に中止)、操作パネルの設定値・実効値・設定元
- 2026-10-23 起動の確認 (start_connection_test.sh): 判定 launch_state (純粋)、今回起動した pid の待受と DEBUG_DUMP_FRAMES=1 を
  待って確認 (遅い起動でも誤警告しない)、時間切れは「確認できず」

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
        # 感度 (2026-10-18) は別のファイルに書く (既存の照合の対象 = ADVICE_SCORING_MODE* はそのまま)
        sensdump = 'env | grep "^TRANSITION_SENSITIVITY" | sort > "{out}.tmp"; mv "{out}.tmp" "{out}"'
        stubs = {
            "uvicorn": (rec + "\n" + sensdump.format(out=td / "uvicorn_sens.txt") + "\n"
                        + envdump.format(out=td / "uvicorn_env.txt")),
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
        tf.write_text(STUB.format(body=sensdump.format(out=td / "train_sens.txt") + "\n" + envdump.format(out=td / "train_env.txt")),
                      encoding="utf-8")

    def write_local(self, text: str) -> None:
        (self.root / "config" / "advice.local.env").write_text(text, encoding="utf-8")

    def env(self, extra: dict = None, minimal: bool = False) -> dict:
        """minimal = launchd と同じく PATH だけ (+ 判定用の python の指定)"""
        if minimal:
            e = {"PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin"}
        else:
            e = {k: v for k, v in os.environ.items()
                 if not k.startswith("ADVICE_SCORING_MODE") and not k.startswith("TRANSITION_SENSITIVITY")}
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


# ------------------------------------------------------------------ transition の感度 (2026-10-18)
def test_sensitivity_resolve_rules():
    r = AM.resolve_sensitivity
    assert r(None, None) == {"value": "0", "source": "default", "file_value": None, "env_value": None}   # 実機の既定 off
    assert r("ADVICE_SCORING_MODE=transition\n", None)["value"] == "0"
    assert r("TRANSITION_SENSITIVITY=\n", None)["source"] == "default"
    assert r("TRANSITION_SENSITIVITY=1\n", None) == {"value": "1", "source": "file", "file_value": "1", "env_value": None}
    assert r("TRANSITION_SENSITIVITY='0'\n", None)["source"] == "file"
    assert r(None, "1") == {"value": "1", "source": "env", "file_value": None, "env_value": "1"}
    assert r(None, " 0 ")["value"] == "0"
    assert r("TRANSITION_SENSITIVITY=1\n", "1")["source"] == "file"                         # 一致 → 続行
    e = _err(r, "TRANSITION_SENSITIVITY=1\n", "0")
    assert "矛盾" in e and "TRANSITION_SENSITIVITY" in e, e
    assert "矛盾" in _err(r, "TRANSITION_SENSITIVITY=0\n", "1")
    for bad in ("on", "true", "2", "yes", "1 # 試験"):
        assert "使えない値" in _err(r, f"TRANSITION_SENSITIVITY={bad}\n", None), bad
        assert "環境変数" in _err(r, None, bad), bad
    assert "2 回" in _err(r, "TRANSITION_SENSITIVITY=1\nTRANSITION_SENSITIVITY=1\n", None)
    # 採点方式と同じファイルに書ける。採点方式の解決は感度の鍵があっても変わらない
    both = "ADVICE_SCORING_MODE=transition\nTRANSITION_SENSITIVITY=1\n"
    assert AM.resolve(both, None)["mode"] == "transition" and r(both, None)["value"] == "1"
    assert "認めない鍵" in _err(r, "TRANSITION_SENSITIVITY_SOURCE=file\n", None)
    print("test_sensitivity_resolve_rules OK")


def test_sensitivity_cli():
    py = sys.executable
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "a.env"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("ADVICE_SCORING_MODE") and not k.startswith("TRANSITION_SENSITIVITY")}
        env["PYTHONPATH"] = str(REPO)
        run = lambda e, *extra: subprocess.run([py, "-m", "tools.advice_mode", "--resolve", *extra, "--file", str(f)],
                                               cwd=str(REPO), env=e, capture_output=True, text=True)
        out = run(env, "--sensitivity")
        assert out.returncode == 0 and out.stdout.splitlines() == [
            "ADVICE_SCORING_MODE=legacy", "ADVICE_SCORING_MODE_SOURCE=default",
            "TRANSITION_SENSITIVITY=0", "TRANSITION_SENSITIVITY_SOURCE=default"], out
        assert len(run(env).stdout.splitlines()) == 2                                       # 従来の出力は 2 行のまま
        f.write_text("TRANSITION_SENSITIVITY=1\n", encoding="utf-8")
        assert run(env, "--sensitivity").stdout.splitlines()[-2:] == ["TRANSITION_SENSITIVITY=1",
                                                                      "TRANSITION_SENSITIVITY_SOURCE=file"]
        bad = run(dict(env, TRANSITION_SENSITIVITY="0"), "--sensitivity")
        assert bad.returncode == 2 and "矛盾" in bad.stderr and "=" not in bad.stdout, bad
        f.write_text("TRANSITION_SENSITIVITY=on\n", encoding="utf-8")
        bad2 = run(env, "--sensitivity")
        assert bad2.returncode == 2 and "使えない値" in bad2.stderr, bad2
    print("test_sensitivity_cli OK")


def test_sensitivity_views_and_startup():
    v_none = AM.sens_file_view(None)
    assert v_none == {"path": "config/advice.local.env", "exists": False, "value": None, "configured": "0", "error": None}
    v_on = AM.sens_file_view("TRANSITION_SENSITIVITY=1\n")
    assert v_on["value"] == v_on["configured"] == "1"
    v_bad = AM.sens_file_view("TRANSITION_SENSITIVITY=2\n")
    assert v_bad["configured"] is None and v_bad["error"]
    assert AM.sens_source_of_env({}) is None
    assert AM.sens_source_of_env({"TRANSITION_SENSITIVITY": "0"}) == "env"
    assert AM.sens_source_of_env({"TRANSITION_SENSITIVITY": "0", "TRANSITION_SENSITIVITY_SOURCE": "default"}) == "default"
    assert AM.sens_onoff("1") == "on" and AM.sens_onoff(False) == "off" and AM.sens_onoff(None) is None
    line = AM.sens_startup_line(False, "default", v_none)
    assert "実効値 off / 設定元 default" in line and "未設定 = 0" in line and "⚠" not in line, line
    assert "⚠" in AM.sens_startup_line(True, None, v_none)          # advice_env.sh を通さない起動 (config の既定 = on)
    assert "⚠" not in AM.sens_startup_line(True, "file", v_on)
    assert "読めない" in AM.sens_startup_line(False, "default", v_bad)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "advice.local.env"
        assert "設定元 default" in AM.sens_startup_check({"TRANSITION_SENSITIVITY_SOURCE": "default"}, p)
    print("test_sensitivity_views_and_startup OK")


def test_sensitivity_start_scripts():
    """4 本の起動スクリプトで uvicorn に同じ値・設定元が届く。学習ループには渡らない。矛盾・不正値は起動しない"""
    want_default = {"TRANSITION_SENSITIVITY": "0", "TRANSITION_SENSITIVITY_SOURCE": "default"}
    want_file = {"TRANSITION_SENSITIVITY": "1", "TRANSITION_SENSITIVITY_SOURCE": "file"}
    runs = (("start_all_nohup.sh", (), {}), ("start_connection_test.sh", (), {}), ("start_servers.sh", (), {}),
            ("deploy.sh", ("--force",), {"advisor_running": True, "minimal": True}))
    for script, args, opt in runs:
        for local, extra, want in ((None, {}, want_default), ("TRANSITION_SENSITIVITY=1\n", {}, want_file),
                                   ("ADVICE_SCORING_MODE=transition\nTRANSITION_SENSITIVITY=1\n",
                                    {"TRANSITION_SENSITIVITY": "1"}, want_file),
                                   (None, {"TRANSITION_SENSITIVITY": "1"},
                                    {"TRANSITION_SENSITIVITY": "1", "TRANSITION_SENSITIVITY_SOURCE": "env"})):
            def case(td):
                sb = Sandbox(td, advisor_running=opt.get("advisor_running", False),
                             training_mode="continuous" if script == "start_all_nohup.sh" else "on_demand")
                if local is not None:
                    sb.write_local(local)
                out = sb.run(script, *args, env=sb.env(extra, minimal=opt.get("minimal", False)))
                if script != "start_servers.sh":
                    assert out.returncode == 0, (script, out.stdout, out.stderr)
                assert sb.received().get("ADVICE_SCORING_MODE"), (script, out.stdout, out.stderr)
                got = sb.received("uvicorn_sens.txt", wait=2.0)
                assert got == want, (script, local, extra, got, out.stdout)
                if script == "start_all_nohup.sh":
                    assert sb.received("train_env.txt") == {} and (td / "train_env.txt").exists()   # 学習ループが動いた
                    # 実機用に決めた値 (ファイル・既定) と設定元は学習ループに持ち込まない。起動したシェルが自分で export した
                    # 環境変数はそのまま継承される (advice_env.sh が足したものではない)
                    assert (td / "train_sens.txt").exists()
                    assert sb.received("train_sens.txt", wait=2.0) == dict(extra), (td / "train_sens.txt").read_text()
            _sandbox_case(case)

        def case_bad(td):
            sb = Sandbox(td, advisor_running=opt.get("advisor_running", False))
            sb.write_local("TRANSITION_SENSITIVITY=1\n")
            out = sb.run(script, *args, env=sb.env({"TRANSITION_SENSITIVITY": "0"}, minimal=opt.get("minimal", False)))
            assert out.returncode != 0 and not sb.called("uvicorn"), (script, out.stdout, out.stderr)
            assert "矛盾" in out.stderr, (script, out.stderr)
            if script == "deploy.sh":
                assert not sb.called("pkill")                                                # 止める前に中止
        _sandbox_case(case_bad)

        def case_invalid(td):
            sb = Sandbox(td, advisor_running=opt.get("advisor_running", False))
            sb.write_local("TRANSITION_SENSITIVITY=on\n")
            out = sb.run(script, *args, env=sb.env(minimal=opt.get("minimal", False)))
            assert out.returncode != 0 and not sb.called("uvicorn") and "使えない値" in out.stderr, (script, out.stderr)
        _sandbox_case(case_invalid)
    print("test_sensitivity_start_scripts OK")


def test_control_panel_sensitivity():
    ps_file = "uvicorn server:app_asgi TRANSITION_SENSITIVITY=1 TRANSITION_SENSITIVITY_SOURCE=file"
    assert CP.sensitivity_from_ps(ps_file) == {"enabled": True, "env": "1", "source": "file"}
    assert CP.sensitivity_from_ps("uvicorn server:app_asgi TRANSITION_SENSITIVITY=0 TRANSITION_SENSITIVITY_SOURCE=default") == \
        {"enabled": False, "env": "0", "source": "default"}
    assert CP.sensitivity_from_ps("uvicorn server:app_asgi PATH=/bin") == {"enabled": True, "env": None, "source": None}
    assert CP.sensitivity_from_ps("") is None
    v = CP.sensitivity_config_view("TRANSITION_SENSITIVITY=1\n", ps_file)
    assert v["file"]["configured"] == "1" and v["running"]["source"] == "file" and not v["mismatch"]
    assert CP.sensitivity_config_warnings(v) == []
    # ファイルを書き換えたが再起動していない / advice_env.sh を通さずに起動した (config の既定 = on) → 食い違い
    v2 = CP.sensitivity_config_view(None, "uvicorn server:app_asgi PATH=/bin")
    assert v2["mismatch"] and "再起動" in CP.sensitivity_config_warnings(v2)[0]
    assert not CP.sensitivity_config_view(None, "")["mismatch"]                             # 停止中
    v3 = CP.sensitivity_config_view("TRANSITION_SENSITIVITY=x\n", ps_file)
    assert v3["file"]["error"] and not v3["mismatch"] and CP.sensitivity_config_warnings(v3) == []

    class Probe:
        def listener(self, port):
            return {"pid": 11, "command": "python uvicorn server:app_asgi --port 8000"} if port == 8000 else None

        def pgrep(self, pattern):
            return []

        def lan_ip(self):
            return "localhost"

        def process_env(self, pid):
            return "python uvicorn server:app_asgi TRANSITION_SENSITIVITY=0 TRANSITION_SENSITIVITY_SOURCE=default"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "logs").mkdir()
        (root / "config").mkdir()
        s = CP.Settings(port=0, bind="127.0.0.1", poll_sec=1.0, tail_lines=10, path_prefix="", server_log_tail_bytes=1024,
                        advisor_port=8000, frontend_port=3000, showdown_port=8100, smoke_battles=20)
        st = CP.Panel(root, s, probe=Probe()).status()
        sc = st["sensitivity_config"]
        assert sc["running"] == {"enabled": False, "env": "0", "source": "default"} and not sc["mismatch"], sc
        assert not any("感度" in w for w in st["warnings"]), st["warnings"]
        (root / "config" / "advice.local.env").write_text("TRANSITION_SENSITIVITY=1\n", encoding="utf-8")
        st = CP.Panel(root, s, probe=Probe()).status()
        assert st["sensitivity_config"]["mismatch"] and any("感度の設定値 (on)" in w for w in st["warnings"])
    page = CP.render_page(s, CP.ACTIONS)
    assert "transition の感度 (アドバイザー)" in page and "sensRows" in page
    print("test_control_panel_sensitivity OK")


def test_config_default_unchanged():
    """config.TRANSITION_SENSITIVITY_ENABLED の既定 (環境変数なし = 計算する) は変えない (測定の子プロセス・診断ツール・テストは従来どおり)"""
    probe = "import champions_agent.config as c\nprint(c.TRANSITION_SENSITIVITY_ENABLED)\n"
    base = {k: v for k, v in os.environ.items() if not k.startswith("TRANSITION_SENSITIVITY")}
    for env_val, want in ((None, "True"), ("0", "False"), ("1", "True")):
        e = dict(base) if env_val is None else dict(base, TRANSITION_SENSITIVITY=env_val)
        out = subprocess.run([sys.executable, "-c", probe], cwd=str(REPO), env=e, capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.strip().splitlines()[-1] == want, (env_val, out.stdout, out.stderr)
    from champions_agent.config import transition_sensitivity_from_value as tv
    assert tv(None) and tv("") and tv("1") and not tv("0") and not tv(" OFF ") and not tv("false")
    print("test_config_default_unchanged OK")


# ------------------------------------------------------------------ データの固定 (2026-10-23)
PIN_ENV_PREFIXES = ("ADVICE_USAGE_", "ADVICE_REAL_BANK", "ADVICE_DATA_PIN")
SHA_A = "0123456789abcdef"


def test_data_pin_resolve_rules():
    r = AM.resolve_data_pin
    none = r(None, {})
    assert none == {"values": {"ADVICE_USAGE_SNAPSHOT": None, "ADVICE_REAL_BANK": None, "ADVICE_REAL_BANK_SHA16": None},
                    "sources": {"ADVICE_USAGE_SNAPSHOT": None, "ADVICE_REAL_BANK": None, "ADVICE_REAL_BANK_SHA16": None},
                    "source": "default", "pinned": False}
    assert r("ADVICE_SCORING_MODE=transition\n", {})["source"] == "default"
    full = f"ADVICE_USAGE_SNAPSHOT=67\nADVICE_REAL_BANK=/p/bank.json\nADVICE_REAL_BANK_SHA16={SHA_A.upper()}\n"
    got = r(full, {})
    assert got["values"] == {"ADVICE_USAGE_SNAPSHOT": "67", "ADVICE_REAL_BANK": "/p/bank.json", "ADVICE_REAL_BANK_SHA16": SHA_A}
    assert got["source"] == "file" and got["pinned"]
    assert r(None, {"ADVICE_USAGE_SNAPSHOT": " 67 "})["values"]["ADVICE_USAGE_SNAPSHOT"] == "67"
    assert r(None, {"ADVICE_USAGE_SNAPSHOT": "67"})["source"] == "env"
    assert r("ADVICE_USAGE_SNAPSHOT=67\n", {"ADVICE_USAGE_SNAPSHOT": "067"})["source"] == "file"          # 一致 → 続行
    assert r("ADVICE_USAGE_SNAPSHOT=67\n", {"ADVICE_REAL_BANK": "/p/b.json"})["source"] == "file+env"
    assert r("ADVICE_REAL_BANK='/p/a b.json'\n", {})["values"]["ADVICE_REAL_BANK"] == "/p/a b.json"      # 引用符は外す
    assert "矛盾" in _err(r, "ADVICE_USAGE_SNAPSHOT=67\n", {"ADVICE_USAGE_SNAPSHOT": "68"})
    assert "矛盾" in _err(r, "ADVICE_REAL_BANK=/p/a.json\n", {"ADVICE_REAL_BANK": "/p/b.json"})
    for bad in ("0", "-1", "abc", "1.5", "latest"):
        assert "正の整数" in _err(r, f"ADVICE_USAGE_SNAPSHOT={bad}\n", {}), bad
        assert "環境変数" in _err(r, None, {"ADVICE_USAGE_SNAPSHOT": bad}), bad
    for bad in ("xyz", SHA_A[:15], SHA_A + "0", "g" * 16):
        assert "16 進" in _err(r, f"ADVICE_REAL_BANK=/p/b.json\nADVICE_REAL_BANK_SHA16={bad}\n", {}), bad
    assert "一緒に" in _err(r, f"ADVICE_REAL_BANK_SHA16={SHA_A}\n", {})                                 # sha16 だけ
    assert "2 回" in _err(r, "ADVICE_USAGE_SNAPSHOT=1\nADVICE_USAGE_SNAPSHOT=1\n", {})
    # 他の鍵は従来どおりエラー (測定の子プロセス用の ADVICE_USAGE_DB・設定元の鍵も書けない)
    for key in ("ADVICE_USAGE_DB", "ADVICE_DATA_PIN_SOURCE"):
        assert "認めない鍵" in _err(r, f"{key}=x\n", {}), key
    # 採点方式・感度の解決はデータの固定の鍵があっても変わらない
    assert AM.resolve(full, None)["mode"] == "legacy" and AM.resolve_sensitivity(full, None)["value"] == "0"
    # 表示用
    assert AM.pin_file_view(full)["pinned"] and AM.pin_file_view("ADVICE_USAGE_SNAPSHOT=x\n")["error"]
    assert "なし" in AM.pin_values_text(None) and "snapshot 67" in AM.pin_values_text(got["values"])
    assert AM.pin_source_of_env({}) is None and AM.pin_source_of_env({"ADVICE_USAGE_SNAPSHOT": "1"}) == "env"
    assert AM.pin_source_of_env({"ADVICE_DATA_PIN_SOURCE": "default"}) == "default"
    v = AM.pin_file_view(full)
    assert not AM.pin_mismatch(v, AM.pin_env_values({"ADVICE_USAGE_SNAPSHOT": "67", "ADVICE_REAL_BANK": "/p/bank.json",
                                                     "ADVICE_REAL_BANK_SHA16": SHA_A}))
    assert AM.pin_mismatch(v, AM.pin_env_values({}))                       # 設定を書いたが再起動していない
    assert not AM.pin_mismatch(AM.pin_file_view(None), AM.pin_env_values({})) and not AM.pin_mismatch(v, None)
    print("test_data_pin_resolve_rules OK")


def _pin_root(td: Path) -> dict:
    """検査の基準 (リポジトリのルートの形): 合成の使用率 DB (snapshot 1・2)・元のバンク・写し"""
    from tests.test_inputs_pin import _bank, _make_db
    db_dir = td / "champions_agent" / "data" / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    _make_db(db_dir / "champions.sqlite3")
    (td / "logs" / "real_opponents").mkdir(parents=True, exist_ok=True)
    live = _bank(td / "logs" / "real_opponents" / "bank.json", 45)
    (td / "logs" / "pinned_data" / "t1").mkdir(parents=True, exist_ok=True)
    copy = _bank(td / "logs" / "pinned_data" / "t1" / "bank.json", 20)
    from advisor.data_pin import sha16_bytes
    return {"live": live, "copy": copy, "sha": sha16_bytes(copy.read_bytes())}


def test_data_pin_cli():
    py = sys.executable
    with tempfile.TemporaryDirectory() as tdn:
        td = Path(tdn)
        fx = _pin_root(td)
        f = td / "a.env"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_SCORING_MODE", "TRANSITION_SENSITIVITY")
                                                                         + PIN_ENV_PREFIXES)}
        env["PYTHONPATH"] = str(REPO)
        run = lambda e, *extra: subprocess.run([py, "-m", "tools.advice_mode", "--resolve", "--sensitivity", *extra, "--file",
                                                str(f), "--root", str(td)], cwd=str(REPO), env=e, capture_output=True, text=True)
        out = run(env, "--data-pin")
        assert out.returncode == 0 and out.stdout.splitlines()[4:] == [
            "ADVICE_USAGE_SNAPSHOT=", "ADVICE_REAL_BANK=", "ADVICE_REAL_BANK_SHA16=", "ADVICE_DATA_PIN_SOURCE=default"], out
        f.write_text(f"ADVICE_USAGE_SNAPSHOT=2\nADVICE_REAL_BANK=logs/pinned_data/t1/bank.json\nADVICE_REAL_BANK_SHA16={fx['sha']}\n",
                     encoding="utf-8")
        assert len(run(env).stdout.splitlines()) == 4                                       # --data-pin なしは従来の 4 行
        out = run(env, "--data-pin")
        assert out.returncode == 0 and out.stdout.splitlines()[4:] == [
            "ADVICE_USAGE_SNAPSHOT=2", "ADVICE_REAL_BANK=logs/pinned_data/t1/bank.json", f"ADVICE_REAL_BANK_SHA16={fx['sha']}",
            "ADVICE_DATA_PIN_SOURCE=file"], out
        # 起動前の検査: snapshot が DB に無い / sha16 が違う / 写しが無い / 元のバンクそのもの → 終了コード 2、何も出さない
        for text, frag in (("ADVICE_USAGE_SNAPSHOT=9\n", "snapshot 9"),
                           (f"ADVICE_REAL_BANK=logs/pinned_data/t1/bank.json\nADVICE_REAL_BANK_SHA16={SHA_A}\n", "sha16"),
                           ("ADVICE_REAL_BANK=logs/pinned_data/none.json\n", "無い"),
                           ("ADVICE_REAL_BANK=logs/real_opponents/bank.json\n", "写し")):
            f.write_text(text, encoding="utf-8")
            bad = run(env, "--data-pin")
            assert bad.returncode == 2 and frag in bad.stderr and "=" not in bad.stdout, (text, bad)
        f.write_text("ADVICE_USAGE_SNAPSHOT=2\n", encoding="utf-8")
        bad = run(dict(env, ADVICE_USAGE_SNAPSHOT="1"), "--data-pin")
        assert bad.returncode == 2 and "矛盾" in bad.stderr, bad
    print("test_data_pin_cli OK")


class PinSandbox(Sandbox):
    """Sandbox に合成の使用率 DB・元のバンク・写しを置き、uvicorn と学習ループのダミーがデータの固定の環境変数も書き出す"""

    def __init__(self, td: Path, **kw):
        super().__init__(td, **kw)
        self.fx = _pin_root(td)
        pindump = ('env | grep -E "^(ADVICE_USAGE_|ADVICE_REAL_BANK|ADVICE_DATA_PIN)" | sort > "{out}.tmp"; '
                   'mv "{out}.tmp" "{out}"')
        for name, out in (("stubbin/uvicorn", "uvicorn_pin.txt"), ("champions_agent/scripts/train_forever.sh", "train_pin.txt")):
            sp = td / name
            body = sp.read_text(encoding="utf-8").rstrip("\n")
            sp.write_text(body.replace("#!/bin/sh\n", "#!/bin/sh\n" + pindump.format(out=td / out) + "\n", 1) + "\n",
                          encoding="utf-8")

    def env(self, extra: dict = None, minimal: bool = False) -> dict:
        e = super().env(extra=None, minimal=minimal)
        e = {k: v for k, v in e.items() if not k.startswith(PIN_ENV_PREFIXES)}
        e.update(extra or {})
        return e


def test_data_pin_start_scripts():
    """4 本の起動スクリプトで、値のある鍵だけが uvicorn に届き (設定元つき)、学習ループには漏れない。検査に通らなければ起動しない
    (deploy.sh は止める前に中止)"""
    runs = (("start_all_nohup.sh", (), {}), ("start_connection_test.sh", (), {}), ("start_servers.sh", (), {}),
            ("deploy.sh", ("--force",), {"advisor_running": True, "minimal": True}))
    for script, args, opt in runs:
        def case(td):
            sb = PinSandbox(td, advisor_running=opt.get("advisor_running", False),
                            training_mode="continuous" if script == "start_all_nohup.sh" else "on_demand")
            sb.write_local(f"ADVICE_SCORING_MODE=transition\nADVICE_USAGE_SNAPSHOT=2\n"
                           f"ADVICE_REAL_BANK=logs/pinned_data/t1/bank.json\nADVICE_REAL_BANK_SHA16={sb.fx['sha']}\n")
            out = sb.run(script, *args, env=sb.env(minimal=opt.get("minimal", False)))
            if script != "start_servers.sh":
                assert out.returncode == 0, (script, out.stdout, out.stderr)
            assert sb.received().get("ADVICE_SCORING_MODE") == "transition", (script, out.stdout, out.stderr)
            got = sb.received("uvicorn_pin.txt", wait=2.0)
            assert got == {"ADVICE_USAGE_SNAPSHOT": "2", "ADVICE_REAL_BANK": "logs/pinned_data/t1/bank.json",
                           "ADVICE_REAL_BANK_SHA16": sb.fx["sha"], "ADVICE_DATA_PIN_SOURCE": "file"}, (script, got, out.stdout)
            if script != "start_servers.sh":
                assert "データの固定: snapshot 2" in out.stdout, (script, out.stdout)
            if script == "start_all_nohup.sh":
                assert sb.received("train_env.txt") == {} and (td / "train_env.txt").exists()            # 学習ループが動いた
                assert sb.received("train_pin.txt", wait=2.0) == {} and (td / "train_pin.txt").exists()  # 学習ループには漏れない
        _sandbox_case(case)

        def case_none(td):
            sb = PinSandbox(td, advisor_running=opt.get("advisor_running", False))
            out = sb.run(script, *args, env=sb.env(minimal=opt.get("minimal", False)))
            assert sb.received().get("ADVICE_SCORING_MODE"), (script, out.stdout, out.stderr)
            assert sb.received("uvicorn_pin.txt", wait=2.0) == {"ADVICE_DATA_PIN_SOURCE": "default"}, script   # 何も足さない
        _sandbox_case(case_none)

        for local, frag in (("ADVICE_USAGE_SNAPSHOT=9\n", "snapshot 9"),
                            ("ADVICE_REAL_BANK=logs/pinned_data/t1/bank.json\nADVICE_REAL_BANK_SHA16=0123456789abcdef\n", "sha16"),
                            ("ADVICE_REAL_BANK_SHA16=0123456789abcdef\n", "一緒に")):
            def case_bad(td):
                sb = PinSandbox(td, advisor_running=opt.get("advisor_running", False))
                sb.write_local(local)
                out = sb.run(script, *args, env=sb.env(minimal=opt.get("minimal", False)))
                assert out.returncode != 0 and not sb.called("uvicorn") and frag in out.stderr, (script, local, out.stderr)
                if script == "deploy.sh":
                    assert not sb.called("pkill") and "止めずに中止" in out.stdout                       # 止める前に中止
                if script == "start_connection_test.sh":
                    assert not (td / "logs" / ".connection_test_start").exists()
            _sandbox_case(case_bad)
    print("test_data_pin_start_scripts OK")


def test_control_panel_data_pin():
    sha = SHA_A
    ps = (f"uvicorn server:app_asgi ADVICE_USAGE_SNAPSHOT=2 ADVICE_REAL_BANK=/p/pinned/bank.json ADVICE_REAL_BANK_SHA16={sha} "
          "ADVICE_DATA_PIN_SOURCE=file")
    run = CP.data_pin_from_ps(ps)
    assert run == {"values": {"ADVICE_USAGE_SNAPSHOT": "2", "ADVICE_REAL_BANK": "/p/pinned/bank.json",
                              "ADVICE_REAL_BANK_SHA16": sha}, "source": "file"}, run
    assert CP.data_pin_from_ps("uvicorn server:app_asgi PATH=/bin") == {
        "values": {"ADVICE_USAGE_SNAPSHOT": None, "ADVICE_REAL_BANK": None, "ADVICE_REAL_BANK_SHA16": None}, "source": None}
    assert CP.data_pin_from_ps("") is None
    text = f"ADVICE_USAGE_SNAPSHOT=2\nADVICE_REAL_BANK=/p/pinned/bank.json\nADVICE_REAL_BANK_SHA16={sha}\n"
    v = CP.data_pin_config_view(text, ps)
    assert v["file"]["pinned"] and v["running"]["source"] == "file" and not v["mismatch"] and CP.data_pin_config_warnings(v) == []
    v2 = CP.data_pin_config_view(text, "uvicorn server:app_asgi ADVICE_DATA_PIN_SOURCE=default")       # 再起動していない
    assert v2["mismatch"] and "再起動" in CP.data_pin_config_warnings(v2)[0]
    assert not CP.data_pin_config_view(text, "")["mismatch"]                                          # 停止中
    assert not CP.data_pin_config_view(None, "uvicorn server:app_asgi PATH=/bin")["mismatch"]          # どちらも固定なし
    v3 = CP.data_pin_config_view("ADVICE_USAGE_SNAPSHOT=x\n", ps)
    assert v3["file"]["error"] and not v3["mismatch"] and CP.data_pin_config_warnings(v3) == []

    class Probe:
        def listener(self, port):
            return {"pid": 11, "command": "python uvicorn server:app_asgi --port 8000"} if port == 8000 else None

        def pgrep(self, pattern):
            return []

        def lan_ip(self):
            return "localhost"

        def process_env(self, pid):
            return "python uvicorn server:app_asgi ADVICE_DATA_PIN_SOURCE=default"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "logs").mkdir()
        (root / "config").mkdir()
        s = CP.Settings(port=0, bind="127.0.0.1", poll_sec=1.0, tail_lines=10, path_prefix="", server_log_tail_bytes=1024,
                        advisor_port=8000, frontend_port=3000, showdown_port=8100, smoke_battles=20)
        st = CP.Panel(root, s, probe=Probe()).status()
        assert st["data_pin_config"]["running"]["source"] == "default" and not st["data_pin_config"]["mismatch"]
        assert not any("データの固定" in w for w in st["warnings"])
        (root / "config" / "advice.local.env").write_text(text, encoding="utf-8")
        st = CP.Panel(root, s, probe=Probe()).status()
        assert st["data_pin_config"]["mismatch"] and any("データの固定" in w for w in st["warnings"])
    page = CP.render_page(s, CP.ACTIONS)
    assert "データの固定 (アドバイザー)" in page and "pinRows" in page
    print("test_control_panel_data_pin OK")


# ------------------------------------------------------------------ 起動の確認 (2026-10-23)
def _launch_state(*args) -> str:
    """scripts/lib/ports.sh の launch_state (純粋) を呼ぶ"""
    cmd = '. scripts/lib/ports.sh; launch_state "$@"'
    out = subprocess.run(["bash", "-c", cmd, "x", *args], cwd=str(REPO), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_launch_state_pure():
    table = "100 1\n200 100\n300 200\n400 1"
    dump = "PID TT STAT TIME COMMAND\n200 ?? S 0:01 python uvicorn server:app_asgi PATH=/bin DEBUG_DUMP_FRAMES=1 RL_ADVICE_STYLE=balance"
    assert _launch_state("", "200", table, dump) == "no_pid"
    assert _launch_state("100", "", table, dump) == "no_listener"
    assert _launch_state("100", "200", table, dump) == "ok"                    # nohup bash -c の子の python が待ち受け
    assert _launch_state("100", "300", table, dump) == "ok"                    # 孫
    assert _launch_state("200", "200", "", dump) == "ok"                       # 起動した pid そのもの
    assert _launch_state("100", "400", table, dump) == "other_process"         # 前のプロセス・別のプロセス
    assert _launch_state("100", "200", table, dump.replace("DEBUG_DUMP_FRAMES=1", "DEBUG_DUMP_FRAMES=10")) == "no_dump"
    assert _launch_state("100", "200", table, "python uvicorn server:app_asgi") == "no_dump"
    assert _launch_state("100", "200", table, "python uvicorn XDEBUG_DUMP_FRAMES=1") == "no_dump"
    assert _launch_state("100", "200", table, "python uvicorn DEBUG_DUMP_FRAMES=1") == "ok"   # 行末
    print("test_launch_state_pure OK")


def test_start_connection_test_waits_for_launch():
    """今回起動した pid の待受と DEBUG_DUMP_FRAMES=1 を待って確かめる。uvicorn が遅れて待ち受けても誤警告しない。
    時間切れは「確認できず」(起動していないとは言わない)"""
    def stubs(sb, ready_after: int, dump: bool):
        cnt = sb.root / "lsof_count"
        pidf = sb.root / "logs" / ".advisor_launch_pid"
        rec = f'echo "$(basename "$0") $*" >> "{sb.calls}"'
        lsof = (rec + "\ncase \"$*\" in\n"
                f'  *-iTCP:8000*)\n    [ -f "{pidf}" ] || exit 0\n'
                f'    n=$(cat "{cnt}" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{cnt}"\n'
                f'    [ "$n" -ge {ready_after} ] && cat "{pidf}";;\n'
                "  *) :;;\nesac")
        envtxt = "python uvicorn server:app_asgi PATH=/bin" + (" DEBUG_DUMP_FRAMES=1" if dump else "")
        ps = ('case "$1" in\n  eww) echo "' + envtxt + '";;\n  -A) :;;\n  *) echo "python uvicorn server:app_asgi";;\nesac')
        for name, body in (("lsof", lsof), ("ps", ps)):
            (sb.bin / name).write_text(STUB.format(body=body), encoding="utf-8")

    def case_slow(td):
        sb = Sandbox(td)
        stubs(sb, ready_after=4, dump=True)                                   # 3 回目までは待ち受けていない
        out = sb.run("start_connection_test.sh", env=sb.env())
        assert out.returncode == 0, (out.stdout, out.stderr)
        pid = (td / "logs" / ".advisor_launch_pid").read_text().strip()
        assert pid.isdigit() and f"今回起動したアドバイザー (pid {pid})" in out.stdout and "DEBUG_DUMP_FRAMES=1 を確認" in out.stdout, out.stdout
        assert "⚠" not in out.stdout.split("=== 準備完了 ===")[0].split("フレーム保存")[-1], out.stdout
    _sandbox_case(case_slow)

    def case_no_dump(td):
        sb = Sandbox(td)
        stubs(sb, ready_after=1, dump=False)
        out = sb.run("start_connection_test.sh", env=sb.env())
        assert out.returncode == 0 and "確認できませんでした" in out.stdout and "DEBUG_DUMP_FRAMES=1 が無い" in out.stdout, out.stdout
        assert "30 秒以内" in out.stdout and "起動していません" not in out.stdout
    _sandbox_case(case_no_dump)

    def case_timeout(td):
        sb = Sandbox(td)                                                       # 既定のダミー: 誰も待ち受けない
        out = sb.run("start_connection_test.sh", env=sb.env())
        assert out.returncode == 0 and "確認できませんでした" in out.stdout and "待ち受けていない" in out.stdout, out.stdout
        assert "起動していません" not in out.stdout
    _sandbox_case(case_timeout)

    def case_stale_pid(td):
        sb = Sandbox(td, advisor_running=True)                                # deploy.sh 経由。前回の pid ファイルは消してから
        (td / "logs" / ".advisor_launch_pid").write_text("1\n", encoding="utf-8")
        # deploy.sh の対戦中の判定 (python3 -m tools.check_battle_active) を「対戦中でない」(終了コード 1) にする
        (sb.bin / "python3").write_text(STUB.format(body=f'echo "$(basename "$0") $*" >> "{sb.calls}"\nexit 1'),
                                        encoding="utf-8")
        out = sb.run("start_connection_test.sh", env=sb.env())
        pid = (td / "logs" / ".advisor_launch_pid").read_text().strip()
        assert out.returncode == 0 and pid != "1" and pid.isdigit(), (out.stdout, out.stderr)
        # 待ち受けているのは前からいるプロセス (ダミーの lsof は 99999) → 今回起動したプロセスではない
        assert "今回起動したプロセス" in out.stdout and "確認できませんでした" in out.stdout, out.stdout
    _sandbox_case(case_stale_pid)
    print("test_start_connection_test_waits_for_launch OK")


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
    test_sensitivity_resolve_rules()
    test_sensitivity_cli()
    test_sensitivity_views_and_startup()
    test_sensitivity_start_scripts()
    test_control_panel_sensitivity()
    test_config_default_unchanged()
    test_data_pin_resolve_rules()
    test_data_pin_cli()
    test_data_pin_start_scripts()
    test_control_panel_data_pin()
    test_launch_state_pure()
    test_start_connection_test_waits_for_launch()
    print("ALL OK")
