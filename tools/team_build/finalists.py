"""複数の方向性の構築を最終候補に残す (2026-09-17 ユーザー決定)。

1 つの勝者ではなく、参照に劣らない (再現性の門を通った) 候補の中から「方向性が違う」ものを K 並び選び、それぞれに
学習 (選出モデルの適応・行動 adapter) と封印 holdout を与えて、ユーザーが選べる形で提案する。
方向性の違い = 既に選んだ候補との共通メンバーが max_shared 体以下 (6 体中)。勝ち筋・軸・メガはラベルとして添える。
純粋関数 (テスト対象)。
"""
from __future__ import annotations

import re
from typing import Optional

# 勝ち筋 (concepts.WIN_CONDITIONS) と出どころ (rule:<framing> / llm:<framing> / historical 等) の日本語。表に無い値はそのまま出す
WIN_CONDITION_JA = {
    "setup_sweep": "積んで全抜き", "offense_trade": "対面で殴り勝つ", "cycle_pressure": "サイクルで圧をかける",
    "hazard_chip": "設置技で削る", "speed_control": "素早さ操作で上を取る", "bulky_attrition": "受けて削る",
    "priority_cleanup": "先制技で詰める", "anti_meta": "環境の上位への対策",
}
SOURCE_JA = {
    "rule:mega": "メガ軸", "rule:favorites": "固定枠軸", "rule:offense": "対面重視", "rule:bulky": "後投げ重視",
    "rule:speed": "素早さ重視", "rule:anti_meta": "環境メタ", "historical": "過去の勝者系統", "mutation": "近傍",
    "crossover": "交配", "novelty": "新規性", "llm:offense": "攻め (LLM)", "llm:balance": "バランス (LLM)",
    "llm:bulky_offense": "耐久寄りの攻め (LLM)", "llm:cycle": "サイクル (LLM)", "llm:setup": "積み (LLM)",
    "llm:speed_control": "素早さ操作 (LLM)", "llm:anti_meta": "環境メタ (LLM)", "llm:specific_core": "指定の軸 (LLM)",
}
_CID_RE = re.compile(r"^L\d+_(C\d+)$")


def family_of(candidate_id: str, families: list) -> Optional[dict]:
    """candidate_id (L07_C026) の系統。持ち込んだ並び (…_from_<run>) は別 run の系統なので None"""
    m = _CID_RE.match(candidate_id or "")
    if not m:
        return None
    return next((f for f in (families or []) if f.get("family_id") == m.group(1)), None)


def shared_members(a, b) -> int:
    return len(set(a or []) & set(b or []))


def direction_of(family: Optional[dict], members) -> dict:
    fam = family or {}
    return {"win_condition": fam.get("win_condition"), "source": fam.get("source"),
            "core_ids": list(fam.get("core_ids") or []), "mega_id": fam.get("mega_id"),
            "family_id": fam.get("family_id"), "members": list(members or []),
            "archetype": fam.get("archetype"), "branch": fam.get("branch"), "switching": fam.get("switching")}


def _species_ja(sid: str) -> str:
    try:
        from advisor.ja_names import species_ja
        return species_ja(sid) or sid
    except Exception:
        return sid


def direction_label_ja(direction: dict, ja=None) -> str:
    """方向性の日本語ラベル: 勝ち筋 / 出どころ / 軸 / メガ (無いものは省く)"""
    ja = ja or _species_ja
    parts = []
    if direction.get("archetype"):
        # 構築の軸 (2026-09-18): 軸 / 分岐 と交代方針を先頭に
        try:
            from tools.team_build.archetypes import SWITCHING_JA, label_ja
            lab = label_ja(direction.get("archetype"), direction.get("branch"))
        except Exception:
            lab, SWITCHING_JA = "", {}
        if lab:
            sw = SWITCHING_JA.get(direction.get("switching") or "", "")
            parts.append(f"軸 {lab}" + (f" ({sw})" if sw else ""))
    wc = direction.get("win_condition")
    if wc:
        parts.append(WIN_CONDITION_JA.get(wc, str(wc)))
    src = direction.get("source") or ""
    if src and not direction.get("archetype"):
        parts.append(SOURCE_JA.get(src) or SOURCE_JA.get(src.split(":")[0]) or src)
    core = [c for c in (direction.get("core_ids") or []) if c][:3]
    if core:
        parts.append("軸: " + "+".join(ja(c) for c in core))
    mega = direction.get("mega_id")
    if mega:
        parts.append("メガ: " + ja(mega))
    return " / ".join(parts) or "方向性ラベルなし"


def pick_finalists(eligible: list, deltas: dict, members_by: dict, families_by: dict, k: int, max_shared: int) -> list:
    """eligible (Δ の降順、再現性の門を通った候補) から、既に選んだ候補との共通メンバーが max_shared 以下のものを
    順に k 並び選ぶ。戻り値: [{rank, candidate_id, delta_s10, direction, direction_ja, skipped_similar_to}]"""
    picks = []
    for cid in eligible:
        if len(picks) >= max(0, k):
            break
        members = members_by.get(cid) or []
        similar = [p["candidate_id"] for p in picks
                   if shared_members(members, members_by.get(p["candidate_id"]) or []) > max_shared]
        if similar:
            continue
        d = direction_of(families_by.get(cid), members)
        picks.append({"rank": len(picks) + 1, "candidate_id": cid, "delta_s10": deltas.get(cid),
                      "direction": d, "direction_ja": direction_label_ja(d)})
    return picks


def skipped_as_similar(eligible: list, picks: list, members_by: dict, max_shared: int) -> list:
    """記録用: 方向性が近いために最終候補から外れた候補と、近い相手"""
    chosen = {p["candidate_id"] for p in picks}
    out = []
    for cid in eligible:
        if cid in chosen:
            continue
        near = [p["candidate_id"] for p in picks
                if shared_members(members_by.get(cid) or [], members_by.get(p["candidate_id"]) or []) > max_shared]
        if near:
            out.append({"candidate_id": cid, "similar_to": near})
    return out
