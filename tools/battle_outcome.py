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

# 勝敗を確定する強い根拠: 勝負の文言 (battle_win / battle_lose) と、WIN / LOSE の画面 (battle_*_screen。2026-10-06、vision/win_lose)
TEXT_OUTCOME = {"battle_win": "win", "battle_lose": "loss", "battle_win_screen": "win", "battle_lose_screen": "loss"}
# これを過ぎたら以後の文言は次の対戦のもの (連結ログ)
END_MARK = ("battle_end_rank", "battle_end_result")


def text_outcome_of(fired) -> Optional[str]:
    """発火イベントに勝負の文言 (または WIN / LOSE の画面) があればその勝敗"""
    for f in fired or []:
        if f in TEXT_OUTCOME:
            return TEXT_OUTCOME[f]
    return None


def text_outcome_basis(fired) -> Optional[str]:
    """text_outcome_of が採った根拠の名前: "battle_text" (勝負の文言) / "win_lose_screen" (WIN / LOSE の画面) / None"""
    for f in fired or []:
        if f in TEXT_OUTCOME:
            return "win_lose_screen" if f.endswith("_screen") else "battle_text"
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


def rate_reads_of(records) -> list:
    """レコード列 → その対戦で読めたレート (値が変わるたびに 1 つ、時刻順)。読み手が rate_chain に渡す"""
    reads: list = []
    for d in records:
        if d.get("type") == "rate" and d.get("value") is not None:
            v = float(d["value"])
            if not reads or reads[-1] != v:
                reads.append(v)
    return reads


def solve_rate_chain(battles: list, min_delta: float, max_delta: float, max_vars: int = 16) -> list:
    """同じ起動の対戦列 (レートの読みが続く) について、レートの読みの並びだけから決まる勝敗を解く (純粋)。
    battles (時系列順): [{"outcome": "win" | "loss" | None (確定した勝敗。推定・不明は None), "reads": [レートの読み]}]
    戻り値 (同じ長さ): [{"by_rate": "win" | "loss" | None, "n_solutions": int, "suspect_reads": bool}]

    モデル: 対戦 i の前後のレートを ρ_{i-1}, ρ_i とし、勝ちなら +[min_delta, max_delta]、負けなら −[min_delta, max_delta] 動く。
    終了画面で 1 回だけ読めた値は ρ_{i-1} (対戦前) か ρ_i (対戦後) のどちらか分からない (2026-10-06 第18回)。2 回以上読めたら
    最初が ρ_{i-1}、最後が ρ_i。読みの種別と不明な勝敗の全組み合わせを数え上げ、読みで決まる値どうしの差が勝敗の数で
    説明できる組み合わせだけを残す。不明な対戦の勝敗が、残った全組み合わせで同じならそれを by_rate にする。
    組み合わせが 1 つも残らなければ (数字の誤読など)、読みを 1 対戦分だけ除いて解き直し、それで残るならその対戦の読みを
    suspect_reads にする。変数 (1 回だけの読み + 不明な勝敗) が max_vars を超えるときは解かない (全部 None)。
    第18回の 15 戦では、2 戦目 (記録は負けの推定、実際は勝ち) と 10・14 戦目 (記録は不明、実際は勝ち) が決まる"""
    n = len(battles)
    out = [{"by_rate": None, "n_solutions": 0, "suspect_reads": False} for _ in range(n)]
    if n == 0:
        return out

    def _solve(skip: Optional[int]):
        singles, fixed_pins = [], {}
        for i, b in enumerate(battles):
            reads = [float(v) for v in (b.get("reads") or [])] if i != skip else []
            if len(reads) == 1:
                singles.append((i, reads[0]))
            elif len(reads) >= 2:
                fixed_pins.setdefault(i, []).append(reads[0])          # 位置 i = 対戦 i (0 始まり) の前のレート
                fixed_pins.setdefault(i + 1, []).append(reads[-1])     # 位置 i + 1 = 対戦 i の後のレート
        # 読みが 1 つも掛からない範囲 (最初の読みより前 / 最後の読みより後) の不明な対戦は、どの差にも効かないので変数にしない
        spots = [p for p in fixed_pins] + [p for i, _v in singles for p in (i, i + 1)]
        if not spots:
            return []
        lo_pos, hi_pos = min(spots), max(spots)
        unknowns = [i for i, b in enumerate(battles)
                    if b.get("outcome") not in ("win", "loss") and lo_pos <= i < hi_pos]
        if len(singles) + len(unknowns) > max_vars:
            return None
        base_pins: dict = {}
        for pos, vals in fixed_pins.items():
            if any(abs(v - vals[0]) >= min_delta for v in vals):
                return []           # 同じ位置の読みが食い違う
            base_pins[pos] = vals[0]
        solutions = []
        n_single, n_unk = len(singles), len(unknowns)
        for mask in range(1 << (n_single + n_unk)):
            pins = dict(base_pins)
            ok = True
            for k, (i, v) in enumerate(singles):
                pos = i if (mask >> k) & 1 else i + 1          # 0 = 対戦後 (ρ_i)、1 = 対戦前 (ρ_{i-1})
                if pos in pins and abs(pins[pos] - v) >= min_delta:
                    ok = False
                    break
                pins[pos] = v
            if not ok:
                continue
            outcomes = [b.get("outcome") for b in battles]
            for k, i in enumerate(unknowns):
                outcomes[i] = "win" if (mask >> (n_single + k)) & 1 else "loss"
            positions = sorted(pins)
            for a, bpos in zip(positions, positions[1:]):
                seg = outcomes[a:bpos]              # 対戦 a+1 .. bpos (0 始まりでは a .. bpos-1)
                w = sum(1 for o in seg if o == "win")
                lo_sum, hi_sum = w * min_delta - (len(seg) - w) * max_delta, w * max_delta - (len(seg) - w) * min_delta
                s = pins[bpos] - pins[a]
                if not (lo_sum - 1e-9 <= s <= hi_sum + 1e-9):
                    ok = False
                    break
            if ok:
                solutions.append(tuple(outcomes[i] for i in unknowns))
        return [(unknowns, sol) for sol in solutions]

    found = _solve(None)
    suspect = None
    if found is not None and not found:
        for i, b in enumerate(battles):
            if b.get("reads"):
                alt = _solve(i)
                if alt:
                    found, suspect = alt, i
                    break
    if not found:
        return out
    unknowns = found[0][0]
    sols = [sol for _u, sol in found]
    for k, i in enumerate(unknowns):
        vals = {sol[k] for sol in sols}
        out[i]["by_rate"] = vals.pop() if len(vals) == 1 else None
    for i in range(n):
        out[i]["n_solutions"] = len(sols)
    if suspect is not None:
        out[suspect]["suspect_reads"] = True
    return out


def apply_rate_chain(battles: list, gap_sec: float, min_delta: float, max_delta: float) -> int:
    """読み手の対戦の一覧 (時系列順。各要素に t0 / t1 / outcome / inferred / reads) に rate_chain の結果を当てる (副作用: 一覧を書き換える)。
    同じ起動の区切りは「前のログの最後から次のログの最初まで gap_sec 以内」。
    勝敗が不明、または推定で、レートの並びで決まる値と違う対戦は outcome を置き換え、inferred=True と by_rate=True を付ける。
    確定した勝敗 (文言・画面・ひんし) は変えない。戻り値: 置き換えた数"""
    changed = 0
    groups: list = []
    for b in battles:
        if groups and b.get("t0") is not None and groups[-1][-1].get("t1") is not None \
                and (b["t0"] - groups[-1][-1]["t1"]) <= gap_sec:
            groups[-1].append(b)
        else:
            groups.append([b])
    for g in groups:
        known = [{"outcome": (b.get("outcome") if (b.get("outcome") in ("win", "loss") and not b.get("inferred")) else None),
                  "reads": b.get("reads") or []} for b in g]
        res = solve_rate_chain(known, min_delta, max_delta)
        for b, r in zip(g, res):
            b["rate_chain"] = r
            if r["by_rate"] and (b.get("outcome") not in ("win", "loss") or b.get("inferred")) and b.get("outcome") != r["by_rate"]:
                b["outcome_recorded"] = b.get("outcome")
                b["outcome"] = r["by_rate"]
                b["inferred"] = True
                b["by_rate"] = True
                changed += 1
    return changed


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
