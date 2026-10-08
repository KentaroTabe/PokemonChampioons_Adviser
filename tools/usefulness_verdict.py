"""有用性検証の対応比較の集計と §0.5 の 3 値判定 (docs/USEFULNESS_VERIFICATION_PLAN_1007.md §0.5、2026-10-09)。

    python -m tools.usefulness_verdict --out logs/usefulness/p1_20261009_2215

入力は tools/usefulness_p1.py の出力ディレクトリ: conditions.json (条件表) と各条件の rl<W>.json の outcomes (対戦の並び = 対応)。
rl<W>.json が無い (時間上限で止めた等) 条件は rl<W>.battles.jsonl の won を順に使う (出所を記録する)。

判定 (§0.5、基準条件に対する比較が k 本なら区間の水準を 1 − α / k に上げる):
  - 採用候補 (adopt_candidate): 差の推定値 ≥ MDE かつ 補正区間の下限 > 0
  - 実用的改善を支持しない (not_supported): 補正区間の上限 < MDE
  - 判定不能 (inconclusive): それ以外 (現状維持)
n が最終対戦数に満たない、または条件表が incomplete: true なら採否を出さない (中間確認: 推定値・区間・費用の更新だけ)。
各条件の勝率と Wilson 区間は参考値 (対応比較の代わりにはならない)。

純粋な部分 (corrected_z / decide / compare / wilson / align / build_verdict / render) は tests/test_usefulness_verdict.py で確かめる。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Optional, Sequence

from champions_agent.config import (BUILD_CI_Z, P1_ALPHA, P1_BASELINE_WEIGHT, P1_FINAL_BATTLES, P1_MDE, P1_WEIGHTS)
from tools.team_build.verdict import paired_diff

ADOPT = "adopt_candidate"
NOT_SUPPORTED = "not_supported"
INCONCLUSIVE = "inconclusive"
DECISION_LABELS = {ADOPT: "採用候補", NOT_SUPPORTED: "実用的改善を支持しない", INCONCLUSIVE: "判定不能 (現状維持)"}
MODE_FINAL = "final"
MODE_INTERIM = "interim"


# ------------------------------------------------------------------ 純粋
def cond_name(weight: float) -> str:
    """重み → 条件名 (0.0 → rl0、5.0 → rl5、25.0 → rl25、2.5 → rl2.5)"""
    return f"rl{float(weight):g}"


def corrected_z(k: int, alpha: float = P1_ALPHA) -> float:
    """比較 k 本の多重比較補正 (Bonferroni) をした両側の z: 水準 1 − α / k の両側区間 → inv_cdf(1 − α / (2k))。
    k = 1 → 1.96、k = 2 → 2.2414"""
    k = max(1, int(k))
    return NormalDist().inv_cdf(1.0 - float(alpha) / (2.0 * k))


def decide(mean: Optional[float], ci_low: Optional[float], ci_high: Optional[float], mde: float = P1_MDE) -> str:
    """§0.5 の 3 値判定 (純粋)。推定値・区間が無ければ判定不能"""
    if mean is None or ci_low is None or ci_high is None:
        return INCONCLUSIVE
    if mean >= mde and ci_low > 0:
        return ADOPT
    if ci_high < mde:
        return NOT_SUPPORTED
    return INCONCLUSIVE


def compare(cond_outcomes: Sequence[int], base_outcomes: Sequence[int], z: float, mde: float = P1_MDE,
            final: bool = True) -> dict:
    """条件 − 基準 の対応比較 (d_i ∈ {−1, 0, +1})。final=False (中間確認) なら decision は None"""
    n, mean, se = paired_diff(list(cond_outcomes), list(base_outcomes))
    lo = hi = None
    if mean is not None:
        lo, hi = mean - z * se, mean + z * se
    return {"n": n, "mean_diff": mean, "se": se, "ci_low": lo, "ci_high": hi, "z": z,
            "decision": decide(mean, lo, hi, mde) if final else None}


def wilson(wins: int, n: int, z: float = BUILD_CI_Z) -> tuple:
    """勝率の Wilson 区間 (参考値)。n = 0 なら (None, None)"""
    if n <= 0:
        return None, None
    p = wins / n
    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def align(outcomes_by_cond: dict) -> tuple:
    """条件間で並びの長さが違えば共通の先頭 n だけを使う → (切りそろえた dict, n, 注記のリスト)"""
    lens = {c: len(v or []) for c, v in outcomes_by_cond.items()}
    n = min(lens.values()) if lens else 0
    notes = []
    if lens and len(set(lens.values())) > 1:
        notes.append(f"並びの長さが条件間で違う ({', '.join(f'{c}={m}' for c, m in lens.items())}) → 共通の先頭 {n} 戦だけを使う")
    return {c: list(v or [])[:n] for c, v in outcomes_by_cond.items()}, n, notes


def opponent_mismatches(opp_ids_by_cond: dict, n: int) -> Optional[int]:
    """各条件の対戦 i の相手 id (battles.jsonl の opponent_team_id) が条件間で食い違う数 (条件の取り違えの検出)。
    相手 id が 2 条件以上で取れなければ None"""
    seqs = [list(v) for v in opp_ids_by_cond.values() if v]
    if len(seqs) < 2:
        return None
    m = min([n] + [len(s) for s in seqs])
    return sum(1 for i in range(m) if len({s[i] for s in seqs}) > 1)


def build_verdict(conditions: dict, outcomes_by_cond: dict, weights: Sequence[float] = P1_WEIGHTS,
                  baseline_weight: float = P1_BASELINE_WEIGHT, final_battles: int = P1_FINAL_BATTLES,
                  mde: float = P1_MDE, alpha: float = P1_ALPHA, sources: Optional[dict] = None,
                  opp_ids_by_cond: Optional[dict] = None, elapsed_by_cond: Optional[dict] = None) -> dict:
    """集計 (純粋)。conditions = 条件表 (incomplete を見る)。outcomes_by_cond = 条件名 → 勝敗列 (1/0)"""
    base = cond_name(baseline_weight)
    others = [cond_name(w) for w in weights if float(w) != float(baseline_weight)]
    k = len(others)
    z = corrected_z(k, alpha)
    notes = []
    missing = [c for c in [base] + others if c not in outcomes_by_cond]
    if missing:
        notes.append(f"結果の無い条件: {', '.join(missing)}")
    present = {c: outcomes_by_cond[c] for c in [base] + others if c in outcomes_by_cond}
    aligned, n, align_notes = align(present)
    notes += align_notes
    incomplete = bool((conditions or {}).get("incomplete"))
    reasons = []
    if incomplete:
        reasons.append("条件表が incomplete: true (時間上限か異常終了)")
    if n < final_battles:
        reasons.append(f"n = {n} < 最終対戦数 {final_battles}")
    if missing:
        reasons.append("条件が揃っていない")
    mode = MODE_INTERIM if reasons else MODE_FINAL
    comparisons = []
    for c in others:
        if c in aligned and base in aligned:
            r = compare(aligned[c], aligned[base], z, mde, final=(mode == MODE_FINAL))
        else:
            r = {"n": 0, "mean_diff": None, "se": None, "ci_low": None, "ci_high": None, "z": z, "decision": None}
        comparisons.append(dict(r, cond=c, baseline=base))
    per_cond = {}
    for c, outs in aligned.items():
        wins = int(sum(outs))
        lo, hi = wilson(wins, len(outs))
        per_cond[c] = {"n": len(outs), "wins": wins, "win_rate": (wins / len(outs)) if outs else None,
                       "wilson_low": lo, "wilson_high": hi, "source": (sources or {}).get(c)}
    cost = {}
    for c, e in (elapsed_by_cond or {}).items():
        m = len(present.get(c) or [])
        if e is not None and m > 0:
            spb = float(e) / m
            cost[c] = {"elapsed_sec": round(float(e), 1), "n": m, "sec_per_battle": round(spb, 2),
                       "projected_final_sec": round(spb * final_battles, 0)}
    mism = opponent_mismatches(opp_ids_by_cond or {}, n)
    if mism:
        notes.append(f"相手 id が条件間で食い違う対戦が {mism} 件 (条件の取り違えか相手列の不一致。対応比較が成り立たない)")
    return {"mode": mode, "interim_reasons": reasons, "k": k, "alpha": alpha, "level": 1.0 - alpha / max(1, k), "z": z,
            "mde": mde, "final_battles": final_battles, "n_used": n, "baseline": base, "comparisons": comparisons,
            "per_condition": per_cond, "cost": cost, "opponent_mismatches": mism, "notes": notes}


def _f(x, nd: int = 3, signed: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}"


def condition_summary(conditions: dict) -> list:
    """条件表の要約 (純粋): 表示用の (項目, 値) の列"""
    c = conditions or {}
    team = c.get("team") or {}
    sel = c.get("selection_model") or {}
    rl = c.get("rl_checkpoint") or {}
    opp = c.get("opponents") or {}
    git = c.get("git") or {}
    return [
        ("実験", f"{c.get('experiment', '-')}{' (予備)' if c.get('prelim') else ''}"),
        ("開始", str(c.get("started_at") or "-")),
        ("重み / 基準 / k", f"{c.get('weights')} / ×{c.get('baseline_weight')} / {c.get('k')}"),
        ("対戦数 (この測定 / 最終)", f"{c.get('battles')} / {c.get('final_battles')}"),
        ("登録チーム sha16", f"{team.get('sha16')} (期待 {team.get('expected_sha16')})"),
        ("選出モデル", f"{sel.get('path')} sha16={sel.get('sha16')}"),
        ("RL checkpoint", f"{rl.get('file')} sha16={rl.get('sha16')} pin={rl.get('pin_dir')}"),
        ("相手", f"{opp.get('tier')} fold {opp.get('fold_label')} seed={opp.get('seed')} offset={opp.get('offset')} "
                 f"操縦={opp.get('pilot')}/{opp.get('pick_policy')} 封印 id={opp.get('sealed_id')}"),
        ("git", f"{git.get('commit')}"),
        ("未完了", str(c.get("incomplete"))),
    ]


def render(verdict: dict, conditions: Optional[dict] = None) -> str:
    """verdict.md / 標準出力の本文 (純粋)"""
    v = verdict
    lines = ["# P1 対応比較の集計", ""]
    if conditions is not None:
        lines += ["## 条件表の要約", "", "| 項目 | 値 |", "|---|---|"]
        lines += [f"| {k} | {val} |" for k, val in condition_summary(conditions)]
        lines.append("")
    if v["mode"] == MODE_INTERIM:
        lines += ["## 中間確認 (採否は出さない)", "", "理由: " + " / ".join(v["interim_reasons"]), ""]
    else:
        lines += ["## 採否 (§0.5、最終対戦数で 1 回だけ)", ""]
    lines += [f"比較 k = {v['k']}、区間の水準 {v['level']:.4f} (両側 z = {v['z']:.4f})、MDE = {v['mde']}、使った n = {v['n_used']}", "",
              "| 比較 | n | 平均差 | SE | 補正区間 | 判定 |", "|---|---|---|---|---|---|"]
    for r in v["comparisons"]:
        dec = DECISION_LABELS.get(r["decision"], "-") if r["decision"] else ("中間確認" if v["mode"] == MODE_INTERIM else "-")
        lines.append(f"| {r['cond']} − {r['baseline']} | {r['n']} | {_f(r['mean_diff'], signed=True)} | {_f(r['se'])} | "
                     f"[{_f(r['ci_low'], signed=True)}, {_f(r['ci_high'], signed=True)}] | {dec} |")
    lines += ["", "### 各条件の勝率 (参考値、Wilson 95%)", "", "| 条件 | n | 勝 | 勝率 | Wilson 区間 | 出所 |", "|---|---|---|---|---|---|"]
    for c, s in v["per_condition"].items():
        lines.append(f"| {c} | {s['n']} | {s['wins']} | {_f(s['win_rate'])} | [{_f(s['wilson_low'])}, {_f(s['wilson_high'])}] | "
                     f"{s.get('source') or '-'} |")
    if v["cost"]:
        lines += ["", "### 費用", "", "| 条件 | n | 経過秒 | 秒/戦 | 最終対戦数までの見込み (秒) |", "|---|---|---|---|---|"]
        for c, s in v["cost"].items():
            lines.append(f"| {c} | {s['n']} | {s['elapsed_sec']} | {s['sec_per_battle']} | {s['projected_final_sec']:.0f} |")
    if v["opponent_mismatches"] is not None:
        lines += ["", f"相手 id の条件間の食い違い: {v['opponent_mismatches']} 件"]
    if v["notes"]:
        lines += ["", "### 注記", ""] + [f"- {x}" for x in v["notes"]]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 読み込み (副作用: ファイル)
def _read_jsonl(path: Path) -> list:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass   # 終了間際に書きかけた行は数えない
    except OSError:
        pass
    return out


def load_outcomes(out_dir: Path, weights: Sequence[float]) -> tuple:
    """各条件の (勝敗列, 出所, 相手 id 列, 経過秒) を読む。rl<W>.json の outcomes が無ければ battles.jsonl の won を使う"""
    outcomes, sources, opp_ids, elapsed = {}, {}, {}, {}
    for w in weights:
        c = cond_name(w)
        js, jl = out_dir / f"{c}.json", out_dir / f"{c}.battles.jsonl"
        recs = _read_jsonl(jl) if jl.exists() else []
        if recs:
            opp_ids[c] = [r.get("opponent_team_id") for r in recs]
        doc = None
        if js.exists():
            try:
                doc = json.loads(js.read_text(encoding="utf-8"))
            except ValueError:
                doc = None
        if doc and isinstance(doc.get("outcomes"), list):
            outcomes[c] = [int(x) for x in doc["outcomes"]]
            sources[c] = f"{js.name} outcomes"
            elapsed[c] = doc.get("elapsed_s")
        elif recs:
            outcomes[c] = [1 if r.get("won") else 0 for r in recs]
            sources[c] = f"{jl.name} won ({js.name} なし)"
    return outcomes, sources, opp_ids, elapsed


def run(out_dir: Path) -> dict:
    cpath = out_dir / "conditions.json"
    try:
        conditions = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        conditions = {}
        print(f"[verdict] 条件表が読めない ({cpath}) → 未完了として扱う")
        conditions["incomplete"] = True
    weights = tuple(conditions.get("weights") or P1_WEIGHTS)
    baseline = float(conditions.get("baseline_weight", P1_BASELINE_WEIGHT))
    final_battles = int(conditions.get("final_battles") or P1_FINAL_BATTLES)
    mde = float(conditions.get("mde", P1_MDE))
    alpha = float(conditions.get("alpha", P1_ALPHA))
    outcomes, sources, opp_ids, elapsed = load_outcomes(out_dir, weights)
    # 時間上限で止めた等で rl<W>.json に経過秒が無ければ、条件表の所要秒で代用する (並列なので全条件で同じ)
    for c in outcomes:
        if elapsed.get(c) is None and conditions.get("elapsed_sec") is not None:
            elapsed[c] = conditions["elapsed_sec"]
    v = build_verdict(conditions, outcomes, weights, baseline, final_battles, mde, alpha, sources, opp_ids, elapsed)
    text = render(v, conditions)
    (out_dir / "verdict.json").write_text(json.dumps(v, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "verdict.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"保存: {out_dir / 'verdict.json'} / {out_dir / 'verdict.md'}")
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description="P1 (RL 加点) の対応比較の集計と §0.5 の 3 値判定")
    ap.add_argument("--out", required=True, help="tools/usefulness_p1.py の出力ディレクトリ")
    args = ap.parse_args()
    run(Path(args.out))


if __name__ == "__main__":
    main()
