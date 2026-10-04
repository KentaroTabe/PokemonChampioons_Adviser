"""実験 12: 環境チーム (相手) の選出方策の妥当性 (対戦なし)。

実戦ログで「相手の 6 体が選出画面で読めた」対戦について、相手の 6 体と自分の 6 体から各方策 (matchup / rule / model / prior) が
出す 3 体を、対戦中に実際に見えた相手の種 (部分的な観測) と突き合わせ、一致率 |予測 ∩ 観測| / |観測| を方策ごとに出す。
最も高い方策を config BUILD_OPP_PICK_POLICY の既定にする (環境チームの選出を実戦に寄せる)。

  python -m tools.team_build.experiments.opponent_pick_validity [--days N] [--policies matchup,rule,model,prior] [--seed 0]
"""
from __future__ import annotations

import argparse
import random
import statistics
from pathlib import Path

from tools.team_build.experiments import REPO, write_result
from tools.team_build.experiments.env_validity import read_real_battle
from tools.team_build.pilot import pick_perm, pick_precision

BATTLES = REPO / "logs" / "battles"


def consistent(b: dict) -> bool:
    """選出画面で相手 6 体が読めていて、対戦中に見えた相手の種が全部その 6 体に入っている (読み取りの整合)"""
    return bool(b.get("opp_full")) and set(b.get("opp_seen") or []) <= set(b.get("opp_species") or []) and len(b.get("our_species") or []) == 6


def summarize(rows: list, policies: list) -> dict:
    out = {}
    for pol in policies:
        vals = [r["precision"][pol] for r in rows if r["precision"].get(pol) is not None]
        out[pol] = {"n": len(vals), "mean": round(sum(vals) / len(vals), 4) if vals else None,
                    "median": round(statistics.median(vals), 4) if vals else None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="実験 12: 相手の選出方策と実戦の相手の選出の一致率")
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--policies", default="matchup,rule,model,prior")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--battles-dir", default=str(BATTLES))
    args = ap.parse_args()
    import time
    from advisor.dex import get_dex
    dex = get_dex()
    policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    prior_of = None
    if "prior" in policies:
        try:
            from advisor.real_prior import load_bank, species_pick_prior
            bank = load_bank()
            prior_of = (lambda s: species_pick_prior(s, bank)) if bank else (lambda s: None)
        except Exception:
            prior_of = lambda s: None       # noqa: E731
    model_path = None
    if "model" in policies:
        from champions_agent.agent import selection_dispatch as SD
        model_path = SD.general_model_path()
    since = time.time() - args.days * 86400 if args.days else None
    rows = []
    for p in sorted(Path(args.battles_dir).glob("battle_*.jsonl")):
        b = read_real_battle(p)
        b["opp_seen"] = b.get("opp_seen") or []
        if since and b["ts"] < since:
            continue
        if not consistent(b):
            continue
        observed = set(b["opp_seen_in_battle"]) if b.get("opp_seen_in_battle") else set()
        if not observed:
            continue
        opp6, our6 = list(b["opp_species"]), list(b["our_species"])
        prec = {}
        for pol in policies:
            try:
                perm = pick_perm(pol, opp6, our6, dex=dex, rng=random.Random(args.seed), prior_of=prior_of, model_path=model_path,
                                 use_registered=False)
                pred = {opp6[i] for i in perm} if perm else set()
            except Exception:
                pred = set()
            prec[pol] = pick_precision(tuple(pred), observed) if pred else None
        rows.append({"file": b["file"], "opp": opp6, "observed": sorted(observed), "precision": prec})
    result = {"n_battles": len(rows), "policies": policies, "summary": summarize(rows, policies), "rows": rows}
    out = write_result("opponent_pick_validity", result)
    print(f"整合した対戦 {len(rows)} 戦。方策ごとの一致率 (平均 / 中央値): " +
          ", ".join(f"{k}={v['mean']}/{v['median']}" for k, v in result["summary"].items()))
    print(f"保存: {out}")


if __name__ == "__main__":
    main()
