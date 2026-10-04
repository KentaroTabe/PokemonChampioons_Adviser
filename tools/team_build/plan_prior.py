"""選出計画 (S5 の selection_plan: 相手の系統 → 出す 3 体) を選出モデルの初期値にする (docs/TEAM_BUILD_REDESIGN_1002.md §5.3)。

2026-10-04: 計画と実際の選出が 3 体とも一致するのは 3〜18% で、晴れ構築ではコータスとリザードンが一度も選出されなかった。
計画はこれまで記事に出すだけだった。ここでは
  - 測定 (check_advisor_player --selection-plan): 選出モデルの予測勝率に、計画の 3 体と一致する選出への加点 (BUILD_PLAN_PRIOR_MIX) を
    足して選ぶ。モデルが無い / 分布外のときは計画そのものを使う (相性順より先)
  - cheap adaptation の収集 (collect_selection_data --selection-plan): 探索枠のうち BUILD_PLAN_EXPLORE_SHARE の割合で計画の選出を
    踏ませ、学習データに計画の選出の勝敗が入るようにする
計画ファイルは joint_stage.write_plan_file が s06_sets/<cid>.plan.json に書く。純粋関数はテスト対象 (tests/test_plan_prior.py)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_PLAN_PRIOR_MIX


def _to_id(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def load_plan(path) -> dict:
    """plan.json → {"selection_plan": {family_id: [3 種 id]}, "members": [...]}。読めなければ空"""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return doc if isinstance(doc, dict) and doc.get("selection_plan") else {}


def plan_for_family(plan: dict, family_id: Optional[str]) -> list:
    """系統の計画の 3 体 (無ければ空)"""
    if not plan or not family_id:
        return []
    return list((plan.get("selection_plan") or {}).get(family_id) or [])


def plan_indices(planned: list, my_species: list) -> Optional[tuple]:
    """計画の 3 体 → 自分の 6 体の index (計画の順)。3 体とも見つからなければ None (純粋)"""
    ids = [_to_id(s) for s in my_species]
    out = []
    for sp in planned:
        sid = _to_id(sp)
        if sid not in ids or ids.index(sid) in out:
            return None
        out.append(ids.index(sid))
    return tuple(out) if len(out) == 3 else None


def apply_plan_prior(scored: list, plan_idx: Optional[tuple], mix: float = BUILD_PLAN_PRIOR_MIX) -> list:
    """選出モデルの [(perm, 予測勝率)] に計画の事前を足す (純粋): 計画の 3 体と同じ集合の perm に mix を足し、降順に並べ直す。
    先発の順はモデルに任せる (計画は 3 体の集合だけを言う)。計画が無ければそのまま"""
    if not plan_idx or mix <= 0:
        return list(scored)
    want = set(plan_idx)
    out = [(perm, float(p) + (mix if set(perm) == want else 0.0)) for perm, p in scored]
    out.sort(key=lambda x: -x[1])
    return out


def plan_perm(plan_idx: Optional[tuple], rng=None) -> Optional[tuple]:
    """計画の 3 体の index → 先発の順を乱択した perm (収集の探索用)。rng が無ければ計画の順"""
    if not plan_idx:
        return None
    idx = list(plan_idx)
    if rng is not None:
        rng.shuffle(idx)
    return tuple(idx)


def plan_agreement(records: list, plan: dict) -> dict:
    """対戦記録 (our_selection / opponent_family_id) と計画の一致 (純粋): 3 体一致の割合と、計画に出るのに一度も選出されない個体"""
    n = n3 = 0
    planned_species: set = set()
    picked: set = set()
    for r in records:
        fam = r.get("opponent_family_id")
        want = plan_for_family(plan, fam)
        sel = [_to_id(s) for s in (r.get("our_selection") or [])]
        picked |= set(sel)
        if not want:
            continue
        n += 1
        planned_species |= {_to_id(s) for s in want}
        if set(_to_id(s) for s in want) == set(sel):
            n3 += 1
    return {"n": n, "match3": n3, "match3_rate": round(n3 / n, 3) if n else None,
            "planned_never_picked": sorted(planned_species - picked)}
