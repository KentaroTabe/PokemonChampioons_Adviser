"""実験 14: 軽い適応 (S8a cheap、1,000 戦で 1 回学習) の選出モデルが、起点の汎用モデルからどう離れたか (2026-10-06)。

改善 run improve_20261006_0128 の S8a で、参照 (登録の 6 体) の cheap は 0.313 と、汎用 0.613 より 0.30 低かった (配布版 0.703、規則 0.457)。
仮説 (コードから): cheap は 単一チームの 1,000 戦 (半分は乱択の選出、残りはタイプ相性の簡易規則の選出) を、汎用モデルを起点に
lr 1e-4 で最大 200 epoch 微調整し、同じ収集の 2 割 (約 200 件、0/1 の勝敗) の検証 MSE が最小の epoch を採る
(train_selection.adapt_candidate)。学習の目標は「この 6 体 × 相手 × 選出 → 勝敗」だが、相手の特徴は同じ run の中で似通い、
選出 120 通りの違いは特徴量の一部なので、ノイズの多い少数の標本では**そのチームの平均勝率**を学ぶ方が損失を下げやすい。
その結果、120 通りの予測勝率の差 (順位の情報) が平らになるか、ノイズで入れ替わる。S7 の適応にある独立 fold の実測での
checkpoint 選択 (adapt.select_checkpoint) が cheap には無いので、崩れたモデルがそのまま測られる。検証 MSE の改善 (gain_pct) は
選択に使った同じ 200 件で測るので楽観的で、崩れを検出しない。

このスクリプトは仮説を数字で確かめる。チーム × 相手 (分割の層の構築) の各対面で、汎用と cheap の 120 通りの点を出して比べる:
  spread_ratio = 予測勝率の標準偏差の比 (cheap / 汎用)。1 より小さいほど平ら
  rho          = 120 通りの点の Spearman 順位相関 (順位の入れ替わり。1 なら同じ順位)
  top1_same    = 汎用の最良と同じ選出を cheap も最良にするか、top3_overlap = 上位 3 の重なり
  shift        = 予測勝率の平均の差 (cheap − 汎用。基準率への引き寄せ)
  python -m tools.team_build.experiments.cheap_drift --run-id R [--arm reference] [--n 60] [--tier selection]
  python -m tools.team_build.experiments.cheap_drift --base M0 --adapted M1 --team-file F --split opponent_families.json
結果は logs/build_search/experiments/cheap_drift_<run>.json。純粋関数 (perm_stats / summarize) は tests/test_cheap_drift で検査する。
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Optional

from tools.team_build.experiments import RUNS, load_json, write_result
from tools.team_build.review_run import spearman


# ------------------------------------------------------------------ 純粋
def perm_stats(base: list, adapted: list) -> Optional[dict]:
    """base / adapted = [(perm, 予測勝率)] (同じ対面)。perm の集合が揃っていなければ共通部分で比べる。3 通り未満なら None"""
    b = {tuple(p): float(v) for p, v in base or []}
    a = {tuple(p): float(v) for p, v in adapted or []}
    perms = sorted(set(b) & set(a))
    if len(perms) < 3:
        return None
    xb = [b[p] for p in perms]
    xa = [a[p] for p in perms]
    sb = statistics.pstdev(xb)
    sa = statistics.pstdev(xa)
    top_b = sorted(perms, key=lambda p: -b[p])
    top_a = sorted(perms, key=lambda p: -a[p])
    return {"n_perms": len(perms), "spread_base": round(sb, 4), "spread_adapted": round(sa, 4),
            "spread_ratio": (round(sa / sb, 3) if sb > 1e-9 else None),
            "rho": (round(r, 3) if (r := spearman(xb, xa)) is not None else None),
            "top1_same": top_a[0] == top_b[0], "top3_overlap": len(set(top_a[:3]) & set(top_b[:3])),
            "shift": round(statistics.fmean(xa) - statistics.fmean(xb), 4),
            "mean_base": round(statistics.fmean(xb), 4), "mean_adapted": round(statistics.fmean(xa), 4)}


def _median(xs: list) -> Optional[float]:
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def summarize(rows: list) -> dict:
    """対面ごとの perm_stats → 中央値と割合。verdict: flattened (広がりが半分以下) / reshuffled (順位相関の中央値 < 0.5) / similar"""
    rows = [r for r in rows or [] if r]
    if not rows:
        return {"n": 0, "verdict": "no_data"}
    ratio = _median([r.get("spread_ratio") for r in rows])
    rho = _median([r.get("rho") for r in rows])
    top1 = sum(1 for r in rows if r.get("top1_same")) / len(rows)
    verdict = "similar"
    if ratio is not None and ratio < 0.5:
        verdict = "flattened"
    elif rho is not None and rho < 0.5:
        verdict = "reshuffled"
    return {"n": len(rows), "spread_ratio_median": ratio, "rho_median": rho, "top1_same_share": round(top1, 3),
            "top3_overlap_median": _median([r.get("top3_overlap") for r in rows]), "shift_median": _median([r.get("shift") for r in rows]),
            "share_rho_below_0_5": round(sum(1 for r in rows if (r.get("rho") or 0.0) < 0.5) / len(rows), 3), "verdict": verdict}


def interpret(summary: dict, report: Optional[dict]) -> str:
    v = summary.get("verdict")
    gain = (report or {}).get("gain_pct")
    head = {"flattened": "cheap の 120 通りの点の広がりが汎用の半分以下: 順位の情報が平らになっている (基準率の学習)",
            "reshuffled": "広がりは残るが順位が入れ替わっている (ノイズへの当てはめ)",
            "similar": "汎用と大きく変わらない: 勝率の差は選出モデル以外 (相手列・操縦) を疑う",
            "no_data": "対面が無い"}.get(v, str(v))
    tail = f"。学習の報告の検証 MSE の改善は {gain:+.1f}% (同じ収集の 2 割で測った値)" if isinstance(gain, (int, float)) else ""
    return head + tail


# ------------------------------------------------------------------ 配線
def _species_of(team_file: Path) -> list:
    from tools.team_build.opponents import parse_team_text
    ids, _mega = parse_team_text(Path(team_file).read_text(encoding="utf-8"))
    return sorted(ids)


def _opponents(split: dict, tier: str, n: int) -> list:
    ids = list((split.get("tiers") or {}).get(tier) or [])
    teams = split.get("teams") or {}
    out = []
    for tid in ids[:n]:
        sp = (teams.get(tid) or {}).get("species") or []
        if len(sp) >= 3:
            out.append((tid, list(sp)))
    return out


def compare_models(base_path: Path, adapted_path: Path, my_species: list, opponents: list) -> list:
    from champions_agent.agent import selection_dispatch as SD
    rows = []
    for tid, opp in opponents:
        sb = SD.score_all(my_species, opp, Path(base_path))
        sa = SD.score_all(my_species, opp, Path(adapted_path))
        st = perm_stats(sb, sa)
        if st:
            st["opponent"] = tid
            rows.append(st)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="軽い適応 (cheap) の選出モデルと汎用モデルの 120 通りの点の比較")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--arm", default="reference", help="run の中の腕 (既定 reference。候補なら candidate_id)")
    ap.add_argument("--n", type=int, default=60, help="比べる相手の構築の数")
    ap.add_argument("--tier", default="selection", help="相手の層 (search / selection / holdout)")
    ap.add_argument("--base", default=None, help="起点のモデル (省略時は汎用 general_model_path)")
    ap.add_argument("--adapted", default=None, help="比べるモデル (省略時は run の advisors_screen/<arm>_screen/selection_model.pt)")
    ap.add_argument("--team-file", default=None, help="自分のチーム (省略時は run の reference_team.txt か s06_sets/<arm>.txt)")
    ap.add_argument("--split", default=None, help="opponent_families.json (省略時は run のもの)")
    args = ap.parse_args()
    from champions_agent.agent import selection_dispatch as SD
    run_dir = (RUNS / args.run_id) if args.run_id else None
    base = Path(args.base) if args.base else SD.general_model_path()
    # cheap のモデルは pipeline._screen_adapt_all が run_dir/advisors_screen/<arm>_screen/ に書く (advisors/ は S7 の適応)
    adapted = Path(args.adapted) if args.adapted else (run_dir / "advisors_screen" / f"{args.arm}_screen" / "selection_model.pt")
    team_file = Path(args.team_file) if args.team_file else (
        run_dir / "reference_team.txt" if args.arm == "reference" else run_dir / "s06_sets" / f"{args.arm}.txt")
    split_path = Path(args.split) if args.split else (run_dir / "opponent_families.json")
    split = load_json(split_path) or {}
    report = load_json(adapted.parent / "adapt_report.json") if adapted else None
    for p, what in ((base, "起点のモデル"), (adapted, "比べるモデル"), (team_file, "チーム"), (split_path, "分割")):
        if not Path(p).exists():
            raise SystemExit(f"{what}が無い: {p}")
    my = _species_of(team_file)
    opps = _opponents(split, args.tier, args.n)
    rows = compare_models(base, adapted, my, opps)
    summary = summarize(rows)
    doc = {"run_id": args.run_id, "arm": args.arm, "base": str(base), "adapted": str(adapted), "team": my, "tier": args.tier,
           "n_opponents": len(opps), "adapt_report": report, "summary": summary, "rows": rows, "interpretation": interpret(summary, report)}
    out = write_result(f"cheap_drift_{args.run_id or 'adhoc'}_{args.arm}", doc)
    print(f"対面 {summary.get('n')}: 広がりの比 {summary.get('spread_ratio_median')}、順位相関 {summary.get('rho_median')}、"
          f"最良が同じ {summary.get('top1_same_share')}、平均の差 {summary.get('shift_median')} → {summary.get('verdict')}")
    print(doc["interpretation"])
    print(f"記録: {out}")


if __name__ == "__main__":
    main()
