"""環境モデルの整合 (2026-10-05 判断 #1): run の相手プールが実戦の相手をどれだけ覆うか (構築単位の一致率) を run ごとに記録する。

実験 3 (experiments/env_validity) の純粋関数を使い、S2 の直後に s02_env_match.json を書く。対象は「選出画面で相手 6 体が読めて、
対戦中に見えた相手がその 6 体に含まれ、自分の 6 体も記録されている」整合した実戦だけ (認識の誤りをプールの不一致と混ぜない)。
目標は季節内に 40% (現状 17.8%)。門ではなく記録 (triggers が基準線との比で引き金に使う)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_POOL_MATCH_THRESHOLD, BUILD_POOL_REAL_DAYS


def match_rate(teams: dict, battles: list, threshold: float = BUILD_POOL_MATCH_THRESHOLD) -> dict:
    """teams = {team_id: [species]}、battles = [{"opp_species", "opp_full", "consistent"}] → 一致率 (純粋)。
    整合した対戦 (opp_full かつ consistent) だけで数える。重なりは env_validity.overlap (6 体読めていれば Jaccard)"""
    from tools.team_build.experiments.env_validity import overlap
    cons = [b for b in battles or [] if b.get("opp_full") and b.get("consistent") and b.get("opp_species")]
    matched = 0
    best_hist: dict = {}
    for b in cons:
        obs = set(b["opp_species"])
        best = max((overlap(obs, set(sp)) for sp in teams.values()), default=0.0)
        if best >= threshold:
            matched += 1
        key = f"{int(best * 10) / 10:.1f}"
        best_hist[key] = best_hist.get(key, 0) + 1
    return {"n_real": len(battles or []), "n_consistent": len(cons), "n_matched": matched,
            "covered_share": (round(matched / len(cons), 3) if cons else None), "threshold": threshold,
            "overlap_hist": dict(sorted(best_hist.items()))}


def load_real_battles(days: Optional[float] = BUILD_POOL_REAL_DAYS, battles_dir: Optional[Path] = None) -> list:
    """実戦ログ (logs/battles/battle_*.jsonl) を読む。無ければ空"""
    from tools.team_build.experiments.env_validity import BATTLES, read_real_battle
    d = Path(battles_dir) if battles_dir else BATTLES
    if not d.exists():
        return []
    since = time.time() - float(days) * 86400 if days else None
    out = []
    for p in sorted(d.glob("battle_*.jsonl")):
        try:
            b = read_real_battle(p)
        except Exception:
            continue
        if since is None or b.get("ts", 0) >= since:
            out.append(b)
    return out


def record_env_match(run_dir: Path, split_doc: dict, days: Optional[float] = BUILD_POOL_REAL_DAYS,
                     battles_dir: Optional[Path] = None, threshold: float = BUILD_POOL_MATCH_THRESHOLD) -> Optional[dict]:
    """run_dir/s02_env_match.json を書いて返す。実戦ログが無ければ None"""
    battles = load_real_battles(days, battles_dir)
    if not battles:
        return None
    teams = {tid: list(t.get("species") or []) for tid, t in (split_doc.get("teams") or {}).items()}
    res = match_rate(teams, battles, threshold)
    res.update({"days": days, "pool_source": split_doc.get("pool_source"), "n_pool_teams": len(teams),
                "n_real_teams": split_doc.get("n_real_teams"), "target": 0.4})
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "s02_env_match.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return res
