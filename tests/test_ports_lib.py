"""scripts/lib/ports.sh (常駐のポート解決) を bash 越しに検査する。

- 待ち受け無し → free、別の作業ディレクトリのプロセス → foreign (空きポートは次へ)、
  このリポジトリから起動した同じコマンド → ours (前回のポートが稼働中なら既定より優先)
- save_ports / load_ports の状態ファイルと、フロントが読む config/ports.local.js の内容 (アドバイザーのポートと配信する版の git commit)
- フロントの配信のパターン (FRONTEND_PATTERN = tools.frontend_server、2026-10-07) で「自分たち」を判定する。操作パネルも同じ値を読む

    python -m tests.test_ports_lib
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "ports.sh"


def sh(snippet: str, env: dict = None) -> str:
    res = subprocess.run(["bash", "-c", f". '{LIB}'\n{snippet}"], capture_output=True, text=True, cwd=str(REPO),
                         env={**os.environ, **(env or {})})
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_listen(port: int, timeout: float = 10.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"port {port} が待ち受けにならない")


def serve(port: int, cwd: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=cwd,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_port_state_and_choose():
    p = free_port()
    assert sh(f"port_state {p} http.server") == "free"
    assert sh(f'choose_port {p} "" http.server') == f"{p} start"
    with tempfile.TemporaryDirectory() as d:
        srv = serve(p, d)                       # 別の作業ディレクトリ = 別プロジェクトのサーバー
        try:
            wait_listen(p)
            assert sh(f"port_state {p} http.server") == "foreign"
            nxt = int(sh(f"pick_free_port {p}"))
            assert nxt > p
            assert sh(f'choose_port {p} "" http.server') == f"{nxt} start"      # 既定が他人 → 次の空きで起動
            assert "pid" in sh(f"port_owner {p}") and "http.server" in sh(f"port_owner {p}")
        finally:
            srv.terminate()
            srv.wait()
    srv = serve(p, str(REPO))                   # このリポジトリから起動 = 自分たち
    try:
        wait_listen(p)
        assert sh(f"port_state {p} http.server") == "ours"
        assert sh(f"port_state {p} uvicorn") == "foreign"                     # コマンドが違えば自分たちではない
        assert sh(f'choose_port {p} "" http.server') == f"{p} ours"
        q = free_port()
        assert sh(f'choose_port {q} {p} http.server') == f"{p} ours"           # 前回のポートが稼働中なら既定より優先
    finally:
        srv.terminate()
        srv.wait()
    assert sh(f"port_owner {p}") == "(未使用)"
    print("test_port_state_and_choose OK")


def test_save_and_load_ports():
    with tempfile.TemporaryDirectory() as d:
        env = {"PORTS_STATE": f"{d}/ports.env", "PORTS_JS": f"{d}/ports.local.js"}
        assert sh('load_ports; echo "$ADVISOR_PORT $FRONTEND_PORT"', env) == "8000 3000"       # 状態が無ければ既定
        sh("save_ports 8001 3002", env)
        assert sh('load_ports; echo "$ADVISOR_PORT $FRONTEND_PORT"', env) == "8001 3002"
        js = Path(d, "ports.local.js").read_text(encoding="utf-8")
        assert "window.ADVISOR_PORT = 8001;" in js
        assert Path(d, "ports.env").read_text(encoding="utf-8") == "ADVISOR_PORT=8001\nFRONTEND_PORT=3002\n"
    print("test_save_and_load_ports OK")


def test_save_ports_git_commit():
    """ports.local.js に配信する版の git commit (短縮) を書く。git が使えなければ null"""
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as d:
        env = {"PORTS_STATE": f"{d}/ports.env", "PORTS_JS": f"{d}/ports.local.js"}
        sh("save_ports 8001 3002", env)
        js = Path(d, "ports.local.js").read_text(encoding="utf-8")
        assert head and f'window.FRONTEND_GIT_COMMIT = "{head}";' in js, js
        fake = Path(d, "bin")
        fake.mkdir()
        (fake / "git").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (fake / "git").chmod(0o755)
        sh("save_ports 8001 3002", {**env, "PATH": f"{fake}:{os.environ.get('PATH', '')}"})
        js = Path(d, "ports.local.js").read_text(encoding="utf-8")
        assert "window.FRONTEND_GIT_COMMIT = null;" in js and "window.ADVISOR_PORT = 8001;" in js, js
    print("test_save_ports_git_commit OK")


def test_frontend_pattern():
    """フロントは tools.frontend_server で起動し、FRONTEND_PATTERN で判定する (http.server は自分たちではない)。
    操作パネル (tools/control_panel) は ports.sh の同じ行を読む"""
    pat = sh('echo "$FRONTEND_PATTERN"')
    assert pat == "tools.frontend_server" and sh('echo "$FRONTEND_MODULE"') == pat
    from tools import control_panel as CP
    assert CP.FRONTEND_PATTERN == pat
    assert CP.shell_assignments('A="x"\nB="$A.y"  # c\n  C="no"\nD="${A}z"\n') == {"A": "x", "B": "x.y", "D": "xz"}
    p = free_port()
    srv = subprocess.Popen([sys.executable, "-m", pat, str(p), "--bind", "127.0.0.1"], cwd=str(REPO),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_listen(p)
        assert sh(f'port_state {p} "$FRONTEND_PATTERN"') == "ours"
        assert sh(f'choose_port {p} "" "$FRONTEND_PATTERN"') == f"{p} ours"
        assert sh(f'pgrep -f "$FRONTEND_PATTERN {p}"') == str(srv.pid)      # end_connection_test.sh の pkill と同じ照合
    finally:
        srv.terminate()
        srv.wait()
    q = free_port()
    old = serve(q, str(REPO))                   # 以前の http.server はこのリポジトリから起動していても自分たちではない
    try:
        wait_listen(q)
        assert sh(f'port_state {q} "$FRONTEND_PATTERN"') == "foreign"
    finally:
        old.terminate()
        old.wait()
    print("test_frontend_pattern OK")


if __name__ == "__main__":
    test_port_state_and_choose()
    test_save_and_load_ports()
    test_save_ports_git_commit()
    test_frontend_pattern()
