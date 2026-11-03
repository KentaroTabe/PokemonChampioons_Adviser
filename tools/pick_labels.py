"""相手の選出ラベル 3 値の読み手 (2026-10-07 段 0、docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2・§10)。

対戦ログの opp_picks 行 (battle_logger が対戦の終わりに書く) を読み、相手の枠ごとの pick_status
(picked_confirmed / unpicked_confirmed / unknown) と appeared (場に出たか) を返す。
opp_picks 行の無い古いログは None を返し、読み手 (analyze_battles / party_improvements / real_opponents) は従来の推定
(ロースター − 場に出た = 選出外) にフォールバックする。

「3 体すべて判明した対戦だけ」の集計 (complete_subset) は、部分集合の件数も一緒に返す
(全体の成績と取り違えないため。報告は「その部分集合の成績」として出す)。
選出の予測 (opp_pick_pred) と候補 (candidates) は selection_record 行にあり、選出の advice 行と advice_id で結ぶ
(selection_records_by_advice / last_selection_prediction)。
"""
from __future__ import annotations

from typing import Optional

from champions_agent.config import BSS_PICK_COUNT

PICK_CONFIRMED = "picked_confirmed"
PICK_UNPICKED = "unpicked_confirmed"
PICK_UNKNOWN = "unknown"


def last_opp_picks(records: list) -> Optional[dict]:
    """最後の opp_picks 行 (無ければ None、純粋)"""
    row = None
    for d in records:
        if d.get("type") == "opp_picks":
            row = d
    return row


def status_by_ja(row: Optional[dict]) -> Optional[dict]:
    """opp_picks 行 → {和名: pick_status} (純粋)。選出画面の推定 (guess) の枠と和名の無い行は除く (読み手のロースターに入らない)"""
    if not row:
        return None
    out = {}
    for s in row.get("slots") or []:
        if s.get("guess") or not s.get("ja"):
            continue
        out[s["ja"]] = s.get("pick_status") or PICK_UNKNOWN
    return out


def split_status(status: dict) -> tuple:
    """{和名: pick_status} → (選出確定, 非選出確定, 不明) の和名のソート済みリスト (純粋)"""
    picked = sorted(ja for ja, st in status.items() if st == PICK_CONFIRMED)
    unpicked = sorted(ja for ja, st in status.items() if st == PICK_UNPICKED)
    unknown = sorted(ja for ja, st in status.items() if st not in (PICK_CONFIRMED, PICK_UNPICKED))
    return picked, unpicked, unknown


def picks_complete(battle: dict, pick_count: int = BSS_PICK_COUNT) -> bool:
    """相手の選出 3 体がすべて判明した対戦か (純粋)。ラベルがあればその complete、無い古いログは場に出た種が pick_count 体"""
    if battle.get("opp_picks_complete") is not None:
        return bool(battle["opp_picks_complete"])
    return len(battle.get("opp_fielded") or []) == pick_count


def selection_records_by_advice(records: list) -> dict:
    """selection_record 行 (選出の助言の記録用の欄、助言を送った後に別の行で書く) を advice_id → 行 で (純粋。同じ id は後の行)。
    行の無い古いログは空"""
    out = {}
    for d in records:
        if d.get("type") == "selection_record" and d.get("advice_id"):
            out[str(d["advice_id"])] = d
    return out


def last_selection_prediction(records: list) -> Optional[dict]:
    """最後の選出の助言 (ok) と、advice_id で結んだ selection_record の欄 (純粋)。
    {"advice_id", "candidates", "opp_pick_pred"}。助言が無ければ None、記録の行が無ければ欄は None"""
    sel = None
    for d in records:
        if d.get("type") == "advice" and d.get("kind") == "selection" and (d.get("advice") or {}).get("ok"):
            sel = d
    if sel is None:
        return None
    rec = selection_records_by_advice(records).get(str(sel.get("advice_id"))) or {}
    return {"advice_id": sel.get("advice_id"), "candidates": rec.get("candidates"), "opp_pick_pred": rec.get("opp_pick_pred")}


def complete_subset(battles: list, key: str = "opp_fielded", pick_count: int = BSS_PICK_COUNT) -> dict:
    """相手の選出 3 体がすべて判明した対戦だけの成績 (純粋)。
    戻り値 {"n_battles": 全体の対戦数, "n_complete": 部分集合の対戦数, "n_decided": 部分集合のうち勝敗確定,
            "stats": {種: [勝, 負]} (key の欄の種ごと、部分集合の勝敗確定の対戦だけ)}"""
    sub = [b for b in battles if picks_complete(b, pick_count)]
    decided = [b for b in sub if b.get("outcome") in ("win", "loss")]
    stats: dict = {}
    for b in decided:
        for sp in b.get(key) or []:
            st = stats.setdefault(sp, [0, 0])
            st[0 if b["outcome"] == "win" else 1] += 1
    return {"n_battles": len(battles), "n_complete": len(sub), "n_decided": len(decided), "stats": stats}
