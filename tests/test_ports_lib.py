"""scripts/lib/ports.sh (常駐のポート解決) を bash 越しに検査する。

- 待ち受け無し → free、別の作業ディレクトリのプロセス → foreign (空きポートは次へ)、
  このリポジトリから起動した同じコマンド → ours (前回のポートが稼働中なら既定より優先)
- save_ports / load_ports の状態ファイルと、フロントが読む config/ports.local.js の内容

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


if __name__ == "__main__":
    test_port_state_and_choose()
    test_save_and_load_ports()
