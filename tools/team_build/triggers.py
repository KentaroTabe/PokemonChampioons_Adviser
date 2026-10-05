"""再構築の引き金 (docs/TEAM_BUILD_PLAN_1005.md Phase 5): 季節の切替 / 登録チームの実戦勝率の悪化 / 相手プールの一致率の低下。

閾値は**基準線との差**で測る (2026-10-05 ユーザー指摘: 絶対値の閾値は季節や相手プールの作り方で意味が変わる)。基準線は
採用した run の時点の値 (`--set-baseline` で logs/build_search/baseline.json に書く)。基準線が無い項目は記録だけで引き金にしない。

    python -m tools.team_build.triggers [--run-id R] [--set-baseline] [--real-days 30]
入力 (2026-10-05 判断 #8 で直した): 実戦の勝率は登録チームと同じ 6 体の対戦記録を直接数える (env_match.registered_real_record、
      直近 BUILD_TRIGGER_REAL_DAYS 日)、シムの参照の勝率は run の summary (reference_variant) か experiments/env_validity.json、
      一致率は runs/<R>/s02_env_match.json (プールに入れた対戦を除いた値) か env_validity.json、季節は config の規制 id。
      一致率は BUILD_TRIGGER_POOL_MATCH_FIRE が偽なら記録だけ。純粋関数 evaluate はテスト対象 (tests/test_theme_check.py)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_TRIGGER_MIN_REAL_BATTLES, BUILD_TRIGGER_POOL_MATCH_DROP, BUILD_TRIGGER_POOL_MATCH_FIRE,
                                    BUILD_TRIGGER_REAL_DAYS, BUILD_TRIGGER_REAL_GAP, TRAINING_BATTLE_FORMAT)

REPO = Path(__file__).resolve().parent.parent.parent
BASELINE = REPO / "logs" / "build_search" / "baseline.json"
EXPERIMENTS = REPO / "logs" / "build_search" / "experiments"
RUNS = REPO / "logs" / "build_search" / "runs"


def evaluate(current: dict, baseline: Optional[dict], real_gap: float = BUILD_TRIGGER_REAL_GAP,
             pool_drop: float = BUILD_TRIGGER_POOL_MATCH_DROP, min_real: int = BUILD_TRIGGER_MIN_REAL_BATTLES,
             pool_fire: bool = BUILD_TRIGGER_POOL_MATCH_FIRE) -> dict:
    """current / baseline = {"season", "real_win_rate", "real_n", "sim_reference_win_rate", "pool_match_share"} (純粋)。
    戻り値 {"fire": bool, "reasons": [...], "checks": {name: {...}}}。
      season      規制 id (季節) が基準線と違えば引き金
      real_gap    (実戦 − シムの参照) が基準線の差より real_gap 以上悪化 (実戦 n ≥ min_real が基準線・現在の両方にあるときだけ)
      pool_match  構築単位の一致率が基準線の pool_drop 倍を割ったら (pool_fire が偽なら記録だけで引き金にしない)
    基準線が無い項目は "no_baseline" として記録だけ"""
    b = baseline or {}
    checks: dict = {}
    reasons: list = []
    cs, bs = current.get("season"), b.get("season")
    if cs and bs:
        fire = cs != bs
        checks["season"] = {"current": cs, "baseline": bs, "fire": fire}
        if fire:
            reasons.append(f"季節 (規制) が変わった: {bs} → {cs}")
    else:
        checks["season"] = {"current": cs, "baseline": bs, "fire": False, "note": "no_baseline" if not bs else "no_current"}
    rw, sw, n = current.get("real_win_rate"), current.get("sim_reference_win_rate"), int(current.get("real_n") or 0)
    if rw is not None and sw is not None and n >= min_real:
        gap = float(rw) - float(sw)
        brw, bsw = b.get("real_win_rate"), b.get("sim_reference_win_rate")
        if brw is not None and bsw is not None and int(b.get("real_n") or 0) < min_real:
            checks["real_gap"] = {"gap": round(gap, 3), "baseline_gap": None, "n": n, "fire": False, "note": "baseline_few_battles"}
        elif brw is not None and bsw is not None:
            base_gap = float(brw) - float(bsw)
            fire = gap < base_gap - real_gap
            checks["real_gap"] = {"gap": round(gap, 3), "baseline_gap": round(base_gap, 3), "n": n, "fire": fire}
            if fire:
                reasons.append(f"実戦 − シムの差が基準線より {base_gap - gap:+.3f} 悪化 ({gap:+.3f} vs {base_gap:+.3f}、n={n})")
        else:
            checks["real_gap"] = {"gap": round(gap, 3), "baseline_gap": None, "n": n, "fire": False, "note": "no_baseline"}
    else:
        checks["real_gap"] = {"gap": None, "n": n, "fire": False, "note": ("few_battles" if n < min_real else "no_data")}
    pm, bpm = current.get("pool_match_share"), b.get("pool_match_share")
    if pm is not None and bpm:
        below = float(pm) < float(bpm) * pool_drop
        fire = below and pool_fire
        checks["pool_match"] = {"share": pm, "baseline": bpm, "floor": round(float(bpm) * pool_drop, 3), "below_floor": below,
                                "fire": fire, **({} if pool_fire else {"note": "record_only"})}
        if fire:
            reasons.append(f"相手プールの一致率 {pm} が基準線 {bpm} の {pool_drop:.0%} を割った")
    else:
        checks["pool_match"] = {"share": pm, "baseline": bpm, "fire": False, "note": "no_baseline" if pm is not None else "no_data"}
    return {"fire": bool(reasons), "reasons": reasons, "checks": checks}


def gather(run_id: Optional[str] = None, real_days: float = BUILD_TRIGGER_REAL_DAYS) -> dict:
    """手元の記録から current を組む。実戦の勝率は登録チームと同じ 6 体の対戦記録を直接数える (直近 real_days 日)"""
    from tools.team_build.env_match import registered_real_record
    cur = {"season": TRAINING_BATTLE_FORMAT, "real_win_rate": None, "real_n": 0, "sim_reference_win_rate": None, "pool_match_share": None,
           "real_days": real_days}
    rec = registered_real_record(real_days)
    cur.update({"real_win_rate": rec.get("win_rate"), "real_n": rec.get("n") or 0, "registered_species": rec.get("species")})
    try:
        ev = json.loads((EXPERIMENTS / "env_validity.json").read_text(encoding="utf-8"))
        cur["sim_reference_win_rate"] = ev.get("sim_reference_win_rate")
        cur["pool_match_share"] = (ev.get("coverage") or {}).get("covered_share")
    except Exception:
        pass
    if run_id:
        try:
            summ = json.loads((RUNS / run_id / "evaluation" / "summary.json").read_text(encoding="utf-8"))
            rv = summ.get("reference_variant") or {}
            wr = (rv.get("win_rates") or {}).get(rv.get("variant"))
            if wr is not None:
                cur["sim_reference_win_rate"] = round(float(wr), 3)
                cur["sim_source"] = f"{run_id}:reference_variant:{rv.get('variant')}"
        except Exception:
            pass
        try:
            em = json.loads((RUNS / run_id / "s02_env_match.json").read_text(encoding="utf-8"))
            if em.get("covered_share") is not None:
                cur["pool_match_share"] = em["covered_share"]
        except Exception:
            pass
    return cur


def main() -> None:
    ap = argparse.ArgumentParser(description="再構築の引き金 (基準線との比)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--set-baseline", action="store_true", help="今の値を基準線として保存する (採用した run の時点で)")
    ap.add_argument("--real-days", type=float, default=BUILD_TRIGGER_REAL_DAYS, help="実戦の勝率の窓 (日)")
    args = ap.parse_args()
    cur = gather(args.run_id, args.real_days)
    if args.set_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(cur, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"基準線を保存: {BASELINE} {cur}")
        return
    base = None
    try:
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
    except Exception:
        pass
    res = evaluate(cur, base)
    print(json.dumps({"current": cur, "baseline": base, "result": res}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
