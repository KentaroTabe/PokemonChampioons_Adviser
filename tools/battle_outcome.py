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
