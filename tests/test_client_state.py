"""助言ページの接続ごとの状態 (client_state.py) と配信 (tools/frontend_server) の検証 (2026-10-07 段 0 の実機確認)。

- ページの版 (index.html の CLIENT_HTML_VERSION) の読み取りと照合 (stale)、対応機能
- 接続 (sid) ごとの可視状態: 通知の無い接続は unknown、hello の visibilityState、page_visibility、切断で消える
- 対戦ファイルを開いたときの client / visibility 行、hello の無い接続 (hello=false)
- 表示通知の hidden=true の連続の警告 (同じ対戦で N 件続いたら 1 回、hidden=false で切れる、対戦が変わると数え直す)
- 配信の全応答に Cache-Control: no-store (localhost に実際に立てて確かめる)

    python -m tests.test_client_state
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import client_state as CS
from client_state import ClientRegistry, HiddenDisplayWatch

REPO = Path(__file__).resolve().parent.parent


def test_html_version_and_stale():
    assert CS.parse_html_version('const CLIENT_HTML_VERSION = "2026-10-07a";') == "2026-10-07a"
    assert CS.parse_html_version("const CLIENT_HTML_VERSION='x1'") == "x1"
    assert CS.parse_html_version("<html></html>") is None and CS.parse_html_version(None) is None
    assert CS.served_html_version(Path(tempfile.gettempdir()) / "no_such_index_20261007.html") is None
    # 配信する index.html は版と、最低限の対応機能を持つ (サーバーは正規表現でこの行を読む)
    html = (REPO / "index.html").read_text(encoding="utf-8")
    v = CS.served_html_version(REPO / "index.html")
    assert v and v == CS.parse_html_version(html)
    feats = re.search(r"const CLIENT_FEATURES = \[([^\]]*)\]", html).group(1)
    for f in ("page_visibility", "advice_shown_hidden", "client_hello", "server_warning"):
        assert f"'{f}'" in feats, f
    assert "socket.emit('client_hello'" in html and "window.FRONTEND_GIT_COMMIT" in html
    assert html.index("emitHello();") < html.index("emitVisibility();\n        });")      # 順序: client_hello → page_visibility
    assert CS.stale_of("a", "a") is False and CS.stale_of("a", "b") is True
    assert CS.stale_of(None, "a") is None and CS.stale_of("a", None) is None
    assert CS.short_sid("abcdefghijkl") == "abcdefgh" and CS.short_sid(None) is None
    assert CS.hidden_of_visibility_state("hidden") is True and CS.hidden_of_visibility_state("visible") is False
    assert CS.hidden_of_visibility_state("prerender") is None
    print("test_html_version_and_stale OK")


def test_registry_visibility_per_sid():
    reg = ClientRegistry()
    reg.on_connect("sidA-123456789", 100.0)
    reg.on_connect("sidB-123456789", 100.0)
    assert reg.frame_state("sidA-123456789") == CS.VIS_UNKNOWN == "unknown"       # 通知なし = 不明 (見えているとはしない)
    assert reg.frame_state("never-connected") == "unknown"
    assert reg.visibility_row("sidA-123456789") is None
    # hello の visibilityState が可視状態になる (source client_hello)
    row = reg.on_hello("sidA-123456789", {"html_version": "v1", "features": ["client_hello"], "visibility": "hidden",
                                          "user_agent": "UA", "href": "http://x/", "served_commit": "abc1234"}, 101.0)
    assert row == {"type": "visibility", "sid": "sidA-123", "hidden": True, "source": "client_hello", "t_notified": 101.0}
    assert reg.frame_state("sidA-123456789") == "hidden" and reg.frame_state("sidB-123456789") == "unknown"
    # page_visibility で更新 (接続ごと)
    row = reg.on_visibility("sidA-123456789", False, 102.0)
    assert row["source"] == "page_visibility" and row["hidden"] is False
    assert reg.frame_state("sidA-123456789") == "visible"
    reg.on_visibility("sidB-123456789", True, 103.0)
    assert reg.frame_state("sidB-123456789") == "hidden" and reg.frame_state("sidA-123456789") == "visible"
    # visibilityState が分からない hello は可視状態を変えない
    assert reg.on_hello("sidB-123456789", {"html_version": "v1", "visibility": "prerender"}, 104.0) is None
    assert reg.frame_state("sidB-123456789") == "hidden"
    reg.on_disconnect("sidB-123456789")
    assert reg.frame_state("sidB-123456789") == "unknown" and "sidB-123456789" not in reg.clients
    reg.on_disconnect("unknown-sid")                                               # 未知の切断は無視
    print("test_registry_visibility_per_sid OK")


def test_client_rows_and_open_rows():
    reg = ClientRegistry()
    reg.on_connect("old-page-sid-1", 10.0)                                         # hello を送らない古い版のページ
    reg.on_hello("new-page-sid-2", {"html_version": "v2", "features": ["client_hello", "page_visibility"],
                                     "visibility": "visible", "user_agent": "UA", "served_commit": None}, 11.0)
    new = reg.client_row("new-page-sid-2", "v2")
    assert new == {"type": "client", "sid": "new-page", "hello": True, "html_version": "v2",
                   "features": ["client_hello", "page_visibility"], "served_version": "v2", "stale": False,
                   "visibility": "visible", "user_agent": "UA", "served_commit": None}
    assert reg.client_row("new-page-sid-2", "v3")["stale"] is True                # ディスクの版と違う
    assert reg.client_row("new-page-sid-2", None)["stale"] is None                # ディスクの版が分からない
    old = reg.client_row("old-page-sid-1", "v2")
    assert old["hello"] is False and old["html_version"] is None and old["stale"] is None and old["features"] is None
    assert reg.client_row("nobody", "v2") is None
    rows = reg.open_rows("v2")
    assert [r["type"] for r in rows] == ["client", "client", "visibility"]          # client 行 (全接続) → 分かっている可視状態
    assert {r["sid"] for r in rows if r["type"] == "client"} == {"old-page", "new-page"}
    assert rows[-1] == {"type": "visibility", "sid": "new-page", "hidden": False, "source": "client_hello", "t_notified": 11.0}
    assert reg.has_hello("new-page-sid-2") and not reg.has_hello("old-page-sid-1")
    print("test_client_rows_and_open_rows OK")


def test_hidden_display_watch():
    w = HiddenDisplayWatch(3)
    assert [w.on_display(True, 1) for _ in range(2)] == [False, False]
    assert w.on_display(True, 1) is True and w.streak == 3                          # 3 件続いたら 1 回
    assert "3 件" in w.warning_text()
    assert w.on_display(True, 1) is False and w.on_display(True, 1) is False        # 同じ連続では 1 回だけ
    assert w.on_display(None, 1) is False and w.streak == 5                         # hidden 欄の無い通知は数えない
    assert w.on_display(False, 1) is False and w.streak == 0                        # 見える表示で切れる
    assert [w.on_display(True, 1) for _ in range(3)] == [False, False, True]        # 次の連続でまた知らせる
    assert w.on_display(True, 2) is False and w.streak == 1                         # 対戦が変わると数え直す
    assert [w.on_display(True, 2) for _ in range(2)] == [False, True]
    w2 = HiddenDisplayWatch(3)
    assert [w2.on_display(h, 1) for h in (True, True, False, True, True)] == [False] * 5   # 連続でなければ知らせない
    assert w2.on_display(True, 9) is False and w2.on_display(True, 9) is False and w2.on_display(True, 9) is True
    print("test_hidden_display_watch OK")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


def test_frontend_server_no_store():
    """tools/frontend_server は http.server と同じく作業ディレクトリ (--directory) を配信し、全応答に no-store を付ける"""
    from tools import frontend_server as FS
    assert ("Cache-Control", "no-store") in FS.NO_STORE_HEADERS
    a = FS.parse_args(["3005"])
    assert a.port == 3005 and a.bind is None
    with tempfile.TemporaryDirectory() as d:
        Path(d, "index.html").write_text('<script>const CLIENT_HTML_VERSION = "t1";</script>', encoding="utf-8")
        port = _free_port()
        srv = subprocess.Popen([sys.executable, "-m", "tools.frontend_server", str(port), "--bind", "127.0.0.1",
                                "--directory", d], cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            t0 = time.time()
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        break
                except OSError:
                    assert time.time() - t0 < 10, "配信が待ち受けにならない"
                    time.sleep(0.2)
            st, h, body = _get(f"http://127.0.0.1:{port}/index.html")
            assert st == 200 and h.get("Cache-Control") == "no-store" and b"t1" in body
            st, h, _ = _get(f"http://127.0.0.1:{port}/")                            # ディレクトリ → index.html
            assert st == 200 and h.get("Cache-Control") == "no-store"
            st, h, _ = _get(f"http://127.0.0.1:{port}/no_such.js")                  # 404 にも付ける
            assert st == 404 and h.get("Cache-Control") == "no-store"
        finally:
            srv.terminate()
            srv.wait()
    print("test_frontend_server_no_store OK")


def main():
    test_html_version_and_stale()
    test_registry_visibility_per_sid()
    test_client_rows_and_open_rows()
    test_hidden_display_watch()
    test_frontend_server_no_store()
    print("ALL OK")


if __name__ == "__main__":
    main()
