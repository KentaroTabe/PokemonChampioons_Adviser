"""助言ページ (index.html) の接続ごとの状態: ページの版と対応機能 (client_hello)、可視状態 (page_visibility)、
隠れたままの表示通知の警告 (2026-10-07 段 0 の実機確認で見つかった 3 件の修正)。

- 10/7 の試験運転では、配信の http.server がキャッシュの指示を付けず、ブラウザがキャッシュの古い index.html を使った疑いがあった。
  ページは接続時に client_hello {html_version, features, visibility, user_agent, href, served_commit} を送り、サーバーはディスクの
  index.html の CLIENT_HTML_VERSION と照合する (stale)。hello を送らないページは client_hello より前の版。
- 可視状態は接続 (sid) ごとに持つ。通知の無い接続から届いたフレームは unknown (「前面を確認した」ことにはしない)。
- 表示通知 (advice_shown) の hidden=true はフレームの数字に使わず、同じ対戦で続いたら警告する。

server.py から副作用 (socket.io・対戦ログ・ファイルの読み) を外した純粋な部分。テストは tests/test_client_state.py。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# index.html の `const CLIENT_HTML_VERSION = "2026-10-07a";` (運用側が HTML を変えるたびに更新する)
HTML_VERSION_RE = re.compile(r"""\bCLIENT_HTML_VERSION\s*=\s*["']([^"']+)["']""")
SID_PREFIX_LEN = 8          # 対戦ログに残す接続 ID の長さ (先頭)
VIS_HIDDEN, VIS_VISIBLE, VIS_UNKNOWN = "hidden", "visible", "unknown"
SOURCE_PAGE_VISIBILITY = "page_visibility"
SOURCE_CLIENT_HELLO = "client_hello"
DISPLAY_HIDDEN_WARNING = ("助言ページが隠れたまま助言を受け取っています (表示通知が {n} 件続けて hidden)。"
                          "ブラウザの窓をゲーム画面に完全には覆われない位置に置いてください "
                          "(macOS の Chrome は完全に覆われた窓を hidden 扱いにし、送信も 1〜2 fps に落ちます)")


def parse_html_version(text: Optional[str]) -> Optional[str]:
    """index.html の中身 → CLIENT_HTML_VERSION の値 (無ければ None)"""
    m = HTML_VERSION_RE.search(text or "")
    return m.group(1) if m else None


def served_html_version(path: Path) -> Optional[str]:
    """配信する index.html (ディスク) の CLIENT_HTML_VERSION。ファイルが無い・読めなければ None (読むだけ)"""
    try:
        return parse_html_version(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def short_sid(sid) -> Optional[str]:
    return str(sid)[:SID_PREFIX_LEN] if sid else None


def stale_of(html_version: Optional[str], served_version: Optional[str]) -> Optional[bool]:
    """ページの版がディスクの版と違うか。どちらかが分からなければ None"""
    if not html_version or not served_version:
        return None
    return html_version != served_version


def hidden_of_visibility_state(state) -> Optional[bool]:
    """document.visibilityState → hidden か ("hidden" → True / "visible" → False / それ以外 (prerender 等) → None)"""
    if state == "hidden":
        return True
    if state == "visible":
        return False
    return None


class ClientRegistry:
    """接続 (sid) ごとの hello と可視状態。切断で消す"""

    def __init__(self):
        self.clients: dict = {}    # sid → {"t_connect", "hello": dict|None, "t_hello", "vis": {"hidden","t","source"}|None}

    def _get(self, sid, now: float) -> dict:
        c = self.clients.get(sid)
        if c is None:      # connect より前に届いた通知 (順序の入れ替わり) でも記録する
            c = self.clients[sid] = {"t_connect": now, "hello": None, "t_hello": None, "vis": None}
        return c

    def on_connect(self, sid, now: float) -> None:
        self._get(sid, now)

    def on_disconnect(self, sid) -> None:
        self.clients.pop(sid, None)

    def on_hello(self, sid, data: Optional[dict], now: float) -> Optional[dict]:
        """client_hello を保持する。hello が可視状態 (visibilityState) を持っていれば、それを sid の状態にして visibility 行を返す"""
        c = self._get(sid, now)
        d = data if isinstance(data, dict) else {}
        c["hello"] = {"html_version": d.get("html_version"), "features": list(d.get("features") or []),
                      "visibility": d.get("visibility"), "user_agent": d.get("user_agent"), "href": d.get("href"),
                      "served_commit": d.get("served_commit")}
        c["t_hello"] = now
        hidden = hidden_of_visibility_state(d.get("visibility"))
        if hidden is None:
            return None
        return self.on_visibility(sid, hidden, now, SOURCE_CLIENT_HELLO)

    def on_visibility(self, sid, hidden, now: float, source: str = SOURCE_PAGE_VISIBILITY) -> dict:
        """可視状態の通知 → sid の状態を更新して visibility 行 (type 付き、t は書くときに付く) を返す"""
        c = self._get(sid, now)
        c["vis"] = {"hidden": bool(hidden), "t": round(float(now), 2), "source": source}
        return self.visibility_row(sid)

    def frame_state(self, sid) -> str:
        """sid から届いたフレームの数え方: hidden / visible / unknown (通知なし)"""
        vis = (self.clients.get(sid) or {}).get("vis")
        if vis is None:
            return VIS_UNKNOWN
        return VIS_HIDDEN if vis["hidden"] else VIS_VISIBLE

    def has_hello(self, sid) -> bool:
        return bool((self.clients.get(sid) or {}).get("hello"))

    def visibility_row(self, sid) -> Optional[dict]:
        vis = (self.clients.get(sid) or {}).get("vis")
        if vis is None:
            return None
        return {"type": "visibility", "sid": short_sid(sid), "hidden": vis["hidden"], "source": vis["source"],
                "t_notified": vis["t"]}

    def client_row(self, sid, served_version: Optional[str]) -> Optional[dict]:
        """対戦ログの client 行。hello の無い接続は html_version / stale が None で hello=False (client_hello より前の版の疑い)"""
        c = self.clients.get(sid)
        if c is None:
            return None
        h = c.get("hello") or {}
        return {"type": "client", "sid": short_sid(sid), "hello": bool(c.get("hello")),
                "html_version": h.get("html_version"), "features": h.get("features"),
                "served_version": served_version, "stale": stale_of(h.get("html_version"), served_version),
                "visibility": h.get("visibility"), "user_agent": h.get("user_agent"),
                "served_commit": h.get("served_commit")}

    def open_rows(self, served_version: Optional[str]) -> list:
        """対戦ファイルを開いたときに書く行: 接続中の全クライアントの client 行と、可視状態の分かっている接続の最新の visibility 行"""
        rows = [self.client_row(sid, served_version) for sid in self.clients]
        rows += [r for r in (self.visibility_row(sid) for sid in self.clients) if r is not None]
        return [r for r in rows if r is not None]


class HiddenDisplayWatch:
    """表示通知 (advice_shown) の hidden=true が同じ対戦で warn_count 件続いたら、その連続につき 1 回だけ知らせる。
    hidden=false で連続が切れ (ページの帯もそこで消える)、次に続いたらまた知らせる。対戦 (battle_key) が変わると数え直す"""

    def __init__(self, warn_count: int):
        self.warn_count = max(1, int(warn_count))
        self._key = None
        self.streak = 0
        self._warned = False

    def on_display(self, hidden, battle_key) -> bool:
        """警告を出すときだけ True。hidden が None (hidden 欄の無い古いページ) は数えない"""
        if battle_key != self._key:
            self._key = battle_key
            self.streak = 0
            self._warned = False
        if hidden is None:
            return False
        if not hidden:
            self.streak = 0
            self._warned = False
            return False
        self.streak += 1
        if self.streak >= self.warn_count and not self._warned:
            self._warned = True
            return True
        return False

    def warning_text(self) -> str:
        return DISPLAY_HIDDEN_WARNING.format(n=self.streak)
