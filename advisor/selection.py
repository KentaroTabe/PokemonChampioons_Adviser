"""選出画面のアドバイス: 6体から最適な3体+先発を提案する。

情報の非対称性を前提にした評価:
- 自分側: 種族が判明している -> 使用率DBの予測技構成で「各相手タイプへの最大打点」を計算
- 相手側: タイプアイコンのみ判明 -> タイプ一致技 (STAB) で殴ってくると仮定した被弾倍率

チーム評価は C(6,3)=20通りの総当たり:
    Σ_j max_i score(i,j)  … 相手の全員に対して「誰かが有利」であること (補完性)
  + 0.3 Σ_i avg_j score(i,j) … 個々の平均的な強さ
先発は選出3体のうち平均スコア最大の個体。
"""
from __future__ import annotations

import json
from itertools import combinations
from typing import Optional

from advisor.dex import get_dex
from advisor.sets import get_predictor

_TYPE_JA2EN = None


def _ja2en() -> dict:
    global _TYPE_JA2EN
    if _TYPE_JA2EN is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _TYPE_JA2EN = dict(raw.get("types", {}))
    return _TYPE_JA2EN


def _my_attack_profile(species_id: str) -> list:
    """自分のポケモンの攻撃プロファイル: [(攻撃タイプ, 実効威力係数)]

    使用率DBの予測技 (上位) から、攻撃技のタイプと
    威力x採用率x攻撃種族値の係数を作る。DBに無い場合はタイプ一致80として扱う。
    """
    dex = get_dex()
    sp = dex.species(species_id)
    if sp is None:
        return []
    atk_stat = max(sp["baseStats"].get("atk", 80), sp["baseStats"].get("spa", 80))

    profile = []
    pred = get_predictor().predict(species_id)
    for mid, pct in pred["moves"]:
        mv = dex.move(mid)
        if not mv or mv["category"] == "Status" or not mv["power"]:
            continue
        stab = 1.5 if mv["type"] in sp["types"] else 1.0
        coeff = (mv["power"] / 100.0) * stab * (atk_stat / 120.0) * min(1.0, pct / 30.0 + 0.5)
        profile.append((mv["type"], coeff))
    if not profile:
        # 予測が無い場合はタイプ一致技 (威力90相当) を仮定
        for t in sp["types"]:
            profile.append((t, 0.9 * 1.5 * (atk_stat / 120.0)))
    return profile


def _matchup_score(my_profile: list, my_types: list, opp_types_en: list) -> float:
    """自分1体 vs 相手1体 (タイプのみ) のマッチアップスコア"""
    dex = get_dex()
    # 与ダメージ側: 予測技の中でその相手に最も通る打点
    offense = 0.0
    for atk_type, coeff in my_profile:
        eff = dex.effectiveness(atk_type, opp_types_en)
        offense = max(offense, coeff * eff)
    # 被ダメージ側: 相手がタイプ一致で殴ってきたときの最大倍率
    threat = 0.0
    for t in opp_types_en:
        threat = max(threat, dex.effectiveness(t, my_types))
    return offense - 0.8 * threat


def advise_selection(state: dict, resolver=None) -> dict:
    """状態辞書から選出提案を作る。

    戻り値: {"ok": bool, "picked": N, "done": bool,
             "recommend": [{"name","index","lead"}], "reason": str,
             "matrix": [[score]]}
    """
    dex = get_dex()
    ja2en = _ja2en()

    my_party = [p for p in state["player"]["party"][:6]]
    opp_party = [p for p in state["opponent"]["party"][:6]]

    picked = state.get("selection_picked")
    done = picked == 3

    # 自分側: 種族IDのあるものだけ評価対象
    mine = []
    for i, p in enumerate(my_party):
        sid = p.get("species_id")
        sp = dex.species(sid) if sid else None
        if sp is None:
            continue
        my_types = sp["types"]
        mine.append({
            "index": i,
            "name": p.get("species_ja") or p.get("display_name") or sid,
            "types": my_types,
            "profile": _my_attack_profile(sid),
            "picked": p.get("is_picked", False),
        })

    opps = []
    for j, p in enumerate(opp_party):
        types_en = [ja2en.get(t, t) for t in (p.get("types") or [])]
        if not types_en and p.get("species_id"):
            sp = dex.species(p["species_id"])
            if sp:
                types_en = sp["types"]
        if types_en:
            opps.append({"index": j, "types": types_en,
                         "label": p.get("species_ja") or "/".join(p.get("types") or [])})

    if len(mine) < 3 or not opps:
        return {"ok": False, "picked": picked, "done": done,
                "reason": "選出評価に必要な情報が不足しています "
                          f"(自分{len(mine)}/6体, 相手{len(opps)}/6枠)"}

    # スコア行列
    matrix = {}
    for m in mine:
        for o in opps:
            matrix[(m["index"], o["index"])] = _matchup_score(
                m["profile"], m["types"], o["types"])

    # C(n,3) 総当たり
    best = None
    for combo in combinations(mine, 3):
        coverage = sum(max(matrix[(m["index"], o["index"])] for m in combo)
                       for o in opps)
        individual = sum(
            sum(matrix[(m["index"], o["index"])] for o in opps) / len(opps)
            for m in combo)
        total = coverage + 0.3 * individual
        if best is None or total > best[0]:
            best = (total, combo)

    _, combo = best
    # 先発: 平均スコア最大
    lead = max(combo, key=lambda m: sum(matrix[(m["index"], o["index"])]
                                         for o in opps))
    ordered = [lead] + [m for m in combo if m is not lead]

    # 理由文の生成: 各選出が誰に対して有利か
    reasons = []
    for m in ordered:
        best_opps = sorted(opps, key=lambda o: -matrix[(m["index"], o["index"])])[:2]
        vs = "、".join(f"{o['label']}に有利" for o in best_opps
                       if matrix[(m["index"], o["index"])] > 0.3)
        reasons.append(f"{m['name']}" + (f" ({vs})" if vs else ""))

    return {
        "ok": True,
        "picked": picked,
        "done": done,
        "recommend": [{"index": m["index"], "name": m["name"],
                       "lead": m is lead} for m in ordered],
        "reason": " / ".join(reasons),
    }


def format_selection_advice(advice: dict) -> str:
    if not advice.get("ok"):
        base = f"[選出評価不可] {advice.get('reason')}"
    else:
        rec = advice["recommend"]
        names = " → ".join(("★" if r["lead"] else "") + r["name"] for r in rec)
        base = f"◎ 推奨選出: {names} (★=先発)\n  {advice['reason']}"
    picked = advice.get("picked")
    if advice.get("done"):
        return f"✅ 選出完了 (3/3)\n{base}"
    if picked is not None:
        return f"選出中 ({picked}/3)\n{base}"
    return base
