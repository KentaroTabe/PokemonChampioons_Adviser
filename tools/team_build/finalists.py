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
_CID_PARTS_RE = re.compile(r"^L\d+_((?:C\d+)(?:x(?:C\d+))+)$")      # 交配 (L07_C028xC026)


def family_of(candidate_id: str, families: list) -> Optional[dict]:
    """candidate_id (L07_C026) の系統。持ち込んだ並び (…_from_<run>) は別 run の系統なので None (imported_family で引く)"""
    m = _CID_RE.match(candidate_id or "")
    if not m:
        return None
    return next((f for f in (families or []) if f.get("family_id") == m.group(1)), None)


def parent_families(candidate_id: str, families: list) -> list:
    """交配 (L07_C028xC026) の親系統の列 (見つかったものだけ)。交配でなければ []"""
    m = _CID_PARTS_RE.match(candidate_id or "")
    if not m:
        return []
    out = []
    for fid in m.group(1).split("x"):
        f = next((f for f in (families or []) if f.get("family_id") == fid), None)
        if f:
            out.append(f)
    return out


def imported_family(imported_from: Optional[dict], load_families) -> tuple:
    """持ち込んだ並び ({run_id, candidate_id}) の系統を元 run の families から引く。
    load_families(run_id) → families (読めなければ [] を返す)。戻り値: (family or None, 交配なら親系統の列)"""
    imp = imported_from or {}
    src_cid, run_id = imp.get("candidate_id") or "", imp.get("run_id") or ""
    if not src_cid or not run_id:
        return None, []
    try:
        fams = load_families(run_id) or []
    except Exception:
        fams = []
    return family_of(src_cid, fams), parent_families(src_cid, fams)


def mega_holders(sets: list, stones) -> list:
    """S6 の型の列から、実際にメガ石を持つ個体 (種族 id) を並び順に返す"""
    stones = set(stones or ())
    return [s.get("species") for s in (sets or []) if (s.get("item") or "") in stones and s.get("species")]


def shared_members(a, b) -> int:
    return len(set(a or []) & set(b or []))


def direction_of(family: Optional[dict], members, megas=None, origin: Optional[dict] = None, parents=None) -> dict:
    """方向性。軸 (core_ids) とメガは系統の記録ではなく **この並びに実際に居る個体** で書く (2026-09-25)。
    S5 の近傍 (1 体入替) と交配は元の concept id を引き継ぐので、系統の core が並びに居ないことがある
    (arch_0924 の 1 位 L06_C020 は C020 のカイリューをリザードンに入れ替えた近傍だった)。
    megas = 実際にメガ石を持つ個体 (mega_holders)、origin = 探索の由来 (Lineup.origin)、parents = 交配の親系統。
    core_missing = 系統の軸のうち居ない種、mega_ids = 石を持つ個体 (2 体以上なら 1 試合 1 体)"""
    fam = family or {}
    parents = list(parents or [])
    members = list(members or [])
    mset = set(members)
    core_all = list(fam.get("core_ids") or [])
    if not core_all:
        for p in parents:
            for c in p.get("core_ids") or []:
                if c not in core_all:
                    core_all.append(c)
    fam_mega = fam.get("mega_id") or next((p.get("mega_id") for p in parents if p.get("mega_id") in mset), None)
    family_id = fam.get("family_id") or ("x".join(str(p.get("family_id") or "?") for p in parents) if parents else None)
    return {"win_condition": fam.get("win_condition"), "source": fam.get("source") or ("crossover" if parents and not fam else None),
            "core_ids": [c for c in core_all if c in mset], "core_missing": [c for c in core_all if c not in mset],
            "mega_id": fam_mega if fam_mega in mset else None, "mega_ids": [m for m in (megas or []) if m in mset],
            "family_id": family_id, "parents": [p.get("family_id") for p in parents], "origin": dict(origin or {}),
            "members": members,
            "archetype": fam.get("archetype"), "branch": fam.get("branch"), "switching": fam.get("switching"),
            "special_branch": fam.get("special_branch")}


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
            lab = label_ja(direction.get("archetype"), direction.get("branch"), direction.get("special_branch"))
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
        lab = SOURCE_JA.get(src) or SOURCE_JA.get(src.split(":")[0]) or src
        if src == "crossover" and direction.get("parents"):
            lab += " (" + "×".join(str(p) for p in direction["parents"]) + ")"
        parts.append(lab)
    core = [c for c in (direction.get("core_ids") or []) if c][:3]
    missing = [c for c in (direction.get("core_missing") or []) if c][:3]
    if core or missing:
        s = "軸: " + ("+".join(ja(c) for c in core) if core else "なし")
        if missing:
            # 系統の軸が並びに居ない: 近傍の入替なら「出 → 入」、それ以外は居ない種を明記 (系統のラベルで誤解させない)
            swap = (direction.get("origin") or {}).get("swap") if (direction.get("origin") or {}).get("kind") == "mutation" else None
            if swap and len(swap) == 2 and swap[0] in missing:
                s += f" (近傍: {ja(swap[0])} → {ja(swap[1])})"
            else:
                s += " (系統の " + "+".join(ja(c) for c in missing) + " は含まない)"
        parts.append(s)
    megas = [m for m in (direction.get("mega_ids") or []) if m]
    if len(megas) > 1:
        parts.append("メガ石: " + "+".join(ja(m) for m in megas) + " (1 試合 1 体)")
    elif megas:
        parts.append("メガ: " + ja(megas[0]))
    elif direction.get("mega_id"):
        parts.append("メガ: " + ja(direction["mega_id"]))
    return " / ".join(parts) or "方向性ラベルなし"


def pick_finalists(eligible: list, deltas: dict, members_by: dict, families_by: dict, k: int, max_shared: int,
                   megas_by: Optional[dict] = None, origins_by: Optional[dict] = None,
                   parents_by: Optional[dict] = None) -> list:
    """eligible (Δ の降順、再現性の門を通った候補) から、既に選んだ候補との共通メンバーが max_shared 以下のものを
    順に k 並び選ぶ。戻り値: [{rank, candidate_id, delta_s10, direction, direction_ja, skipped_similar_to}]
    megas_by / origins_by / parents_by: 候補 → 実際のメガ石持ち / 探索の由来 / 交配の親系統 (無ければ系統の記録だけで書く)"""
    megas_by, origins_by, parents_by = megas_by or {}, origins_by or {}, parents_by or {}
    picks = []
    for cid in eligible:
        if len(picks) >= max(0, k):
            break
        members = members_by.get(cid) or []
        similar = [p["candidate_id"] for p in picks
                   if shared_members(members, members_by.get(p["candidate_id"]) or []) > max_shared]
        if similar:
            continue
        d = direction_of(families_by.get(cid), members, megas=megas_by.get(cid), origin=origins_by.get(cid),
                         parents=parents_by.get(cid))
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
