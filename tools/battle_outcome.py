"""対戦ログの勝敗の解決 (純粋計算)。書き手 (battle_logger) と読み手 (analyze_battles / party_improvements / real_eval /
decision_audit / review_battle) が同じ規則を使う。

規則:
- outcome 行は最後のものを採る (書き手が勝負の文言で訂正した行 `corrected_from` が後に足される)。
- 勝負の文言 (「〜との勝負に勝った / 負けた」「相手が降参した」) は最も強い根拠。outcome 行の後、**その対戦のランク画面 /
  リザルト画面を過ぎる前**に読めた文言が記録と食い違えば文言を採る。ランク画面を過ぎた後の文言は次の対戦のもの
  (2026-09-29 第16回の 2 戦連結ログ: 先の対戦の「負け」の記録の後に、後の対戦の「勝った」が同じファイルにある)。
2026-09-29 第17回 15:53: 誤読で生えた 7 体目が 3 体目のひんしに数えられ「負け」と記録された後、ランク画面の前に
「勝負に勝った」を読んだが記録は負けのままだった (レートは +15.6 で勝ち)。
"""
from __future__ import annotations

from typing import Optional

TEXT_OUTCOME = {"battle_win": "win", "battle_lose": "loss"}
# これを過ぎたら以後の文言は次の対戦のもの (連結ログ)
END_MARK = ("battle_end_rank", "battle_end_result")


def text_outcome_of(fired) -> Optional[str]:
    """発火イベントに勝負の文言があればその勝敗"""
    for f in fired or []:
        if f in TEXT_OUTCOME:
            return TEXT_OUTCOME[f]
    return None


def outcome_record_fields(d: dict) -> tuple:
    """outcome 行 → (勝敗, 推定か, 訂正の行か (corrected_from つき: 書き手が勝負文言で先の記録を訂正した))"""
    return d.get("outcome"), bool(d.get("inferred")), bool(d.get("corrected_from"))


def resolve_outcome(recorded: Optional[str], text: Optional[str]) -> tuple:
    """記録の勝敗と (訂正の候補になる) 勝負文言の勝敗から最終値を決める。
    戻り値: (勝敗, 訂正したか)。訂正 = 記録が確定値 (win/loss) で文言と食い違った場合"""
    if text in ("win", "loss") and text != recorded:
        return text, recorded in ("win", "loss")
    return recorded, False


class OutcomeTracker:
    """レコードを順に feed して最終の勝敗を決める (純粋)。outcome 行の後、ランク/リザルト画面の前の勝負文言だけを
    訂正の候補にする"""

    def __init__(self) -> None:
        self.recorded: Optional[str] = None
        self.inferred = False
        self.corrected_row = False
        self.text: Optional[str] = None      # 訂正の候補 (記録の後、ランク画面の前の文言)
        self.closed = False                  # 記録の後にランク/リザルト画面を過ぎた

    def feed(self, d: dict) -> None:
        typ = d.get("type")
        if typ == "outcome":
            self.recorded, self.inferred, self.corrected_row = outcome_record_fields(d)
            # 推定の更新の行 (revised_from: 後から読めたレートで推定し直した。ランク画面の後に書かれる) は、文言による訂正の
            # 候補と「ランク画面を過ぎた」の印をそのまま引き継ぐ (ここで戻すと、次の対戦の文言を訂正に使ってしまう)
            if "revised_from" not in d:
                self.text, self.closed = None, False
        elif typ == "events":
            fired = d.get("fired") or []
            t = text_outcome_of(fired)
            if t and self.recorded is not None and not self.closed:
                self.text = t
            if self.recorded is not None and any(f in END_MARK for f in fired):
                self.closed = True

    def result(self) -> tuple:
        """(勝敗, 推定か, 訂正されたか)。訂正 = 読み手が文言で上書きした、または訂正の行が記録されている"""
        out, corrected = resolve_outcome(self.recorded, self.text)
        corrected = corrected or self.corrected_row
        return out, (self.inferred and not corrected), corrected


def outcome_from_records(records) -> tuple:
    """レコード列 → (勝敗, 推定か, 訂正されたか)"""
    ot = OutcomeTracker()
    for d in records:
        ot.feed(d)
    return ot.result()


def rate_inference(reads: list, rate_open: Optional[float], open_fresh: bool, open_is_post: bool,
                   prev_outcome: Optional[str], min_delta: float, max_delta: float) -> Optional[dict]:
    """ランク画面のレートの読みから、この対戦の勝敗を推定する (純粋)。推定できなければ None。
    戻り値 {"outcome": "win" | "loss", "from": 対戦前の値, "to": 対戦後の値}。推定できたとき、最後の読みは対戦後の値。

    reads: この対戦の終了画面で読めたレート (値が変わるたびに 1 つ、時刻順)。rate_open: この対戦に入る前の最後の読み。
    open_fresh: rate_open が直前の対戦の終了画面で読めた値か (間に読めなかった対戦が無い)。open_is_post: rate_open が直前の
    対戦の「後」の値だと分かっているか。prev_outcome: 直前の対戦の勝敗 (文言などで確定したものだけ。推定・不明は None)。

    ランク画面のレートは対戦前の値で表示が始まり、数秒で対戦後の値に変わる。1 回だけ読めた値はどちらか分からない
    (2026-10-06 第18回接続テスト: 1 回だけ読めた 9 戦で、対戦前の値 5 / 対戦後の値 4)。対戦前の値どうしの差は直前の対戦の
    増減なので、この対戦の勝敗にしてはいけない (第18回の 2 戦目: 1 戦目の負けの −19.0 を 2 戦目の負けと記録した。実際は勝ち)。
    - 2 つ以上読めた: 最初 (対戦前) → 最後 (対戦後) の増減
    - 1 つだけ読めて rate_open と違い、rate_open が直前の対戦で読めた値のとき:
        rate_open が直前の対戦の後の値だと分かっている → その差
        直前の対戦の勝敗が確定していて、差の向きがそれと逆 → その差の向き (直前の対戦の増減ではあり得ない)
        それ以外 (差の向きが直前の対戦と同じ / 直前の勝敗が不明) → 推定しない (直前の対戦の増減かもしれない)
    差の大きさが min_delta 未満 (小数の読み違い) / max_delta 超 (数字の誤読) なら推定しない"""
    def _of(a: float, b: float) -> Optional[dict]:
        d = b - a
        if abs(d) < min_delta or abs(d) > max_delta:
            return None
        return {"outcome": "win" if d > 0 else "loss", "from": a, "to": b}

    reads = [float(v) for v in (reads or [])]
    if len(reads) >= 2:
        return _of(reads[0], reads[-1])
    if len(reads) == 1 and rate_open is not None and open_fresh:
        got = _of(float(rate_open), reads[0])
        if got and (open_is_post or (prev_outcome in ("win", "loss") and prev_outcome != got["outcome"])):
            return got
    return None
