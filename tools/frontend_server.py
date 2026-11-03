"""助言ページ (index.html) の配信。`python3 -m http.server` と同じ挙動で、全応答に `Cache-Control: no-store` を付ける。

    python3 -m tools.frontend_server 3000            # リポジトリのルートで起動 (作業ディレクトリを配信する)
    python3 -m tools.frontend_server 3000 --bind 127.0.0.1 --directory /path/to/dir

2026-10-07 段 0 の実機確認: `http.server` はキャッシュの指示を付けないため、ブラウザがキャッシュの index.html
(段 0 より前の版 = page_visibility を送らない) を使った疑いがあった (frontend_nohup.log に index.html の GET が無く、
config/ports.local.js だけ取得されていた)。no-store にして毎回ディスクの版を取らせる。

bind の既定 (全インターフェース、IPv4/IPv6 の両方)・作業ディレクトリ・ログの形式 (標準エラーへのアクセスログと、起動時の
"Serving HTTP on ...") は `python3 -m http.server` と同じ。起動スクリプトは scripts/lib/ports.sh の FRONTEND_MODULE で起動し、
FRONTEND_PATTERN (このモジュール名) で「自分たち」を判定する。標準ライブラリだけを使う (起動は venv ではない python3)。
"""
from __future__ import annotations

import argparse
import contextlib
import os
import socket
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer, test as serve

NO_STORE_HEADERS = (("Cache-Control", "no-store"),)


class NoStoreHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler の全応答 (エラー・304 を含む) に NO_STORE_HEADERS を足す"""

    def end_headers(self):
        for k, v in NO_STORE_HEADERS:
            self.send_header(k, v)
        super().end_headers()


class DualStackServer(ThreadingHTTPServer):
    """`python3 -m http.server` と同じ: IPv6 で待ち受けるときも IPv4 を受ける"""

    def server_bind(self):
        with contextlib.suppress(Exception):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        return super().server_bind()


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="index.html の配信 (Cache-Control: no-store)")
    p.add_argument("--bind", "-b", metavar="ADDRESS", help="待ち受けるアドレス (既定: 全インターフェース)")
    p.add_argument("--directory", "-d", default=os.getcwd(), help="配信するディレクトリ (既定: 作業ディレクトリ)")
    p.add_argument("port", action="store", default=8000, type=int, nargs="?", help="ポート (既定: 8000)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    serve(HandlerClass=partial(NoStoreHandler, directory=args.directory), ServerClass=DualStackServer,
          port=args.port, bind=args.bind)


if __name__ == "__main__":
    main()
