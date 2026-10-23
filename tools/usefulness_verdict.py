"""有用性検証の対応比較の集計と §0.5 の 3 値判定 (docs/USEFULNESS_VERIFICATION_PLAN_1007.md §0.5、2026-10-09)。

    python -m tools.usefulness_verdict --out logs/usefulness/p1_20261009_2215

入力は tools/usefulness_p1.py の出力ディレクトリ: conditions.json (条件表) と各条件の rl<W>.json の outcomes (対戦の並び = 対応)。
rl<W>.json が無い (時間上限で止めた等) 条件は rl<W>.battles.jsonl の won を順に使う (出所を記録する)。
採点方式の比較 (条件表の factor が scoring、tools.usefulness_p1 --compare scoring): 条件名 scoring:legacy / scoring:transition
(ファイル名は「:」を「_」に: scoring_legacy.json 等) を読み、基準 = 条件表の baseline (scoring:legacy)、比較 = transition − legacy の
1 本 (k = 1) で下の判定を出す (未完了・中間確認・対応の確認は重みの比較と同じ)。報告には自チームの出所 (条件表の team.source:
ranked = 一般的なチーム / registered = 最新の登録チーム) と、各条件の json の stats の退避率 (scoring_fallback / scoring_transition、
理由別) を載せる。重みの比較 (rl<W>) の読み方は変えない。

判定 (§0.5、基準条件に対する比較が k 本なら区間の水準を 1 − α / k に上げる):
  - 採用候補 (adopt_candidate): 差の推定値 ≥ MDE かつ 補正区間の下限 > 0
  - 実用的改善を支持しない (not_supported): 補正区間の上限 < MDE
  - 判定不能 (inconclusive): それ以外 (現状維持)
n が最終対戦数に満たない、または条件表が incomplete: true なら採否を出さない (中間確認: 推定値・区間・費用の更新だけ)。
全条件の対象 n 戦ぶんの相手 id (battles.jsonl の opponent_team_id) が揃い条件間で一致することを確かめられなければ、比較条件の不成立 (mode invalid) として採否を出さない (統計的な判定不能とは別。2026-10-09 レビュー指摘 1)。
採点方式の比較で条件表が own_check.required を持てば (2026-10-09 M2d、usefulness_p1 が書く)、自チームの対応も確かめる: 対戦 i の
送った自チームの本文の sha16 (battles.jsonl の own_team_sha16) が条件間で一致し、各条件で助言が参照した型と送った型の不一致
(ref_check.mismatch) が 0 で、確認できない対戦と助言が評価できない個体 (ref_check.unbuilt、2026-10-09 M2e) を含む対戦が無いときだけ
最終判定を出す (満たさなければ mode invalid、理由と件数を残す)。
各条件の勝率と Wilson 区間は参考値 (対応比較の代わりにはならない)。
2 夜分割 (2026-10-09 M2e、usefulness_p1 --part k/n): --out に全 part の出力 dir を渡し、--merged-out に結合した判定を書く。
各 part の条件表の part の範囲の順に、勝敗・相手 id・battles.jsonl の記録を条件ごとに連結して 1 回だけ判定する。結合の検査
(merge_parts): 全 part が同じ n の別々の k、範囲が重複しない、各 part に全条件の結果がある、相手列の offset が範囲の先頭、
比較の条件 (seed・fold・分割・自チームのプール・比較の設計・最終対戦数・git と Showdown の commit) が全 part で同じ。満たさなければ
比較条件の不成立 (invalid)。範囲の抜け・part の未完了は対戦数の不足として中間確認 (interim)。全 part の所要秒の合計が採点比較の
全体の上限 (条件表の scoring_time.time_limit_total_sec) を超えたら未完了。
封印 holdout 層での確認 (2026-10-15 レビュー、usefulness_p1 --tier holdout。条件表の opponents.tier が holdout): 報告に
「封印 holdout 層 (N 構築) での 1 回の確認、search の結果とは合算しない」と出し、fold の表示を出さない。事後の検査として、
条件表の分割ファイルを読み直し (sha16 が条件表と一致すること)、battles.jsonl の相手 id が全件 holdout 層に属することを確かめる。
満たさなければ比較条件の不成立 (invalid)。判定は §0.5 のまま (k は条件表の比較の本数)。
入力の実体の固定 (2026-10-23、usefulness_p1 の条件表の inputs_pin): ピンの選出モデル一組・使用率の bundle・バンクの写しの sha256 が
manifest と一致し、各条件の結果 JSON の inputs_pin (子プロセスが起動時に照合した snapshot・バンクの sha16) がピンと同じことを
事後に確かめる (inputs_pin_post_check)。欠落・不一致は比較条件の不成立 (invalid)。inputs_pin の無い条件表は注記だけ。

純粋な部分 (corrected_z / decide / compare / wilson / align / build_verdict / render) は tests/test_usefulness_verdict.py で確かめる。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Optional, Sequence

from champions_agent.config import (BUILD_CI_Z, P1_ALPHA, P1_BASELINE_WEIGHT, P1_FINAL_BATTLES, P1_MDE, P1_SCORING_BASELINE,
                                    P1_SCORING_MODES, P1_WEIGHTS)
from tools.team_build.verdict import paired_diff

ADOPT = "adopt_candidate"
NOT_SUPPORTED = "not_supported"
INCONCLUSIVE = "inconclusive"
DECISION_LABELS = {ADOPT: "採用候補", NOT_SUPPORTED: "実用的改善を支持しない", INCONCLUSIVE: "判定不能 (現状維持)"}
MODE_FINAL = "final"
MODE_INTERIM = "interim"
# 比較条件の不成立 (対応の確認ができない: 相手 id の不一致・欠落・不足。統計的な「判定不能」とは別。2026-10-09 レビュー指摘 1)
MODE_INVALID = "invalid"
# 自チームの出所 (条件表の team.source) の表示名
TEAM_LABELS = {"ranked": "一般的なチーム", "registered": "最新の登録チーム",
               "pool": "一般的なチーム (実験専用の構築プール)"}
# 封印 holdout 層での確認 (2026-10-15 レビュー。usefulness_p1 --tier holdout の条件表の opponents.tier)
HOLDOUT_TIER = "holdout"


def is_holdout(conditions: Optional[dict]) -> bool:
    """条件表が封印 holdout 層での確認か (純粋)"""
    return ((conditions or {}).get("opponents") or {}).get("tier") == HOLDOUT_TIER


# ------------------------------------------------------------------ 純粋
def cond_name(weight: float) -> str:
    """重み → 条件名 (0.0 → rl0、5.0 → rl5、25.0 → rl25、2.5 → rl2.5)"""
    return f"rl{float(weight):g}"


def cond_file(cond: str) -> str:
    """条件名 → ファイル名の部分 (「:」を「_」に。rl<W> はそのまま。scoring:legacy → scoring_legacy)"""
    return str(cond).replace(":", "_")


def scoring_conds(conditions: dict) -> tuple:
    """採点方式の比較の条件表 → (基準の条件名, 比較の条件名の列) (純粋)。条件表に無ければ config の既定の方式"""
    c = conditions or {}
    base = c.get("baseline") or f"scoring:{P1_SCORING_BASELINE}"
    others = [x.get("cond") for x in (c.get("comparisons") or []) if x.get("cond")]
    if not others:
        others = [f"scoring:{m}" for m in P1_SCORING_MODES if f"scoring:{m}" != base]
    return base, others


def scoring_fallback_summary(doc: Optional[dict]) -> Optional[dict]:
    """check_advisor_player の json → 採点方式と transition の退避率 (純粋)。stats の scoring_transition (transition で採点した
    決定数) と scoring_fallback (そのうち legacy に退避した数)、理由別 (scoring_fallback:<理由>)。json が無ければ None"""
    if not doc:
        return None
    st = doc.get("stats") or {}
    n_tr = int(st.get("scoring_transition") or 0)
    n_fb = int(st.get("scoring_fallback") or 0)
    by_reason = {k.split(":", 1)[1]: int(v) for k, v in st.items() if str(k).startswith("scoring_fallback:")}
    # 2026-10-09 M2d: 本採点 (時間と予算超過の退避) / 感度 (有効か・状態・省略の理由) / 全処理時間 (感度込み) を分ける。
    # 未評価の応手が出た決定 (区間で順位が決まった・決まらず退避の両方) の数と技も載せる
    timing = doc.get("transition_timing") or {}
    n_budget = int(st.get("scoring_fallback:budget") or 0)
    sens_status = {k.split(":", 1)[1]: int(v) for k, v in st.items() if str(k).startswith("sens:")}
    sens_omit = {k.split(":", 1)[1]: int(v) for k, v in st.items() if str(k).startswith("sens_omitted:")}
    une_by = {k.split(":", 1)[1]: int(v) for k, v in st.items() if str(k).startswith("scoring_unevaluated:")}
    return {"advice_scoring_mode": doc.get("advice_scoring_mode"), "scoring_transition": n_tr, "scoring_fallback": n_fb,
            "fallback_rate": (n_fb / n_tr) if n_tr else None, "fallback_by_reason": by_reason,
            "main": {"budget_fallback": n_budget, "budget_fallback_rate": (n_budget / n_tr) if n_tr else None,
                     "p50_ms": (timing.get("main") or {}).get("p50_ms"), "p95_ms": (timing.get("main") or {}).get("p95_ms")},
            "sens": {"enabled": doc.get("transition_sensitivity"), "status": sens_status, "omitted": sens_omit},
            "total": {"p50_ms": (timing.get("total") or {}).get("p50_ms"), "p95_ms": (timing.get("total") or {}).get("p95_ms")},
            "unevaluated": {"n": int(st.get("scoring_unevaluated") or 0),
                            "by_move": dict(sorted(une_by.items(), key=lambda kv: (-kv[1], kv[0])))}}


def own_correspondence_check(records_by_cond: Optional[dict], conds: Sequence[str], n: int) -> tuple:
    """自チームの対応の検査 (純粋。2026-10-09 M2d、レビュー 6)。records_by_cond = 条件名 → battles.jsonl の記録の列。
    対戦 i (先頭 n 戦) について (a) 送った自チームの本文の sha16 (own_team_sha16) が条件間で一致、(b) 各条件で助言が参照した型と
    送った型の不一致 (ref_check.mismatch) が 0、(c) 確認できない対戦 (own_team_sha16 が無い・ref_check が無いか検査 0 件) が無い、
    (d) 助言が評価できない個体 (図鑑に無い種、ref_check.unbuilt) を含む対戦が無い (2026-10-09 M2e、レビュー: 両条件で同じ
    評価不能が起きても比較が正しくなるわけではない)、(e) 対戦中に選出が分からず全個体を選出扱いにした決定 (入力不成立、
    accept.picked_unknown) を含む対戦が無い (2026-10-15 M3a。accept の欄が無い記録 = 修正前は数えない: 受入監査の検査が別に見る)。
    戻り値 (成り立たない理由の list, 要約)。理由が空なら成立"""
    recs = records_by_cond or {}
    unverifiable, ref_bad, unbuilt, picked_unknown = {}, {}, {}, {}
    unbuilt_species: dict = {}
    hash_mismatch = 0
    for c in conds:
        rows = list(recs.get(c) or [])[:n]
        if len(rows) < n:
            unverifiable[c] = unverifiable.get(c, 0) + (n - len(rows))
        for r in rows:
            rc = r.get("ref_check") if isinstance(r.get("ref_check"), dict) else None
            if not r.get("own_team_sha16") or rc is None or int(rc.get("n") or 0) == 0:
                unverifiable[c] = unverifiable.get(c, 0) + 1
            if rc is not None and int(rc.get("mismatch") or 0) > 0:
                ref_bad[c] = ref_bad.get(c, 0) + 1
            if rc is not None and int(rc.get("unbuilt") or 0) > 0:
                unbuilt[c] = unbuilt.get(c, 0) + 1
                for sid in rc.get("unbuilt_species") or []:
                    unbuilt_species[str(sid)] = unbuilt_species.get(str(sid), 0) + 1
            acc = r.get("accept") if isinstance(r.get("accept"), dict) else None
            if acc is not None and int(acc.get("picked_unknown") or 0) > 0:
                picked_unknown[c] = picked_unknown.get(c, 0) + 1
    for i in range(n):
        shas = {(list(recs.get(c) or [])[i].get("own_team_sha16") if i < len(recs.get(c) or []) else None) for c in conds}
        shas.discard(None)
        if len(shas) > 1:
            hash_mismatch += 1
    reasons = []
    if hash_mismatch:
        reasons.append(f"自チームの本文の sha16 が条件間で食い違う対戦が {hash_mismatch} 件")
    if ref_bad:
        reasons.append(f"助言が参照した型が送った型と違う対戦: {ref_bad}")
    if unverifiable:
        reasons.append(f"自チームの対応を確認できない対戦: {unverifiable}")
    if unbuilt:
        sp = ", ".join(f"{k} {v}" for k, v in sorted(unbuilt_species.items(), key=lambda kv: (-kv[1], kv[0]))) or "種の記録なし"
        reasons.append(f"助言が評価できない個体 (図鑑に無い種) を含む対戦: {unbuilt} (種: {sp})")
    if picked_unknown:
        reasons.append(f"選出が分からず全個体を選出扱いにした決定 (入力不成立) を含む対戦: {picked_unknown}")
    summary = {"n": n, "hash_mismatch": hash_mismatch, "ref_mismatch_battles": ref_bad, "unverifiable": unverifiable,
               "unbuilt_battles": unbuilt, "unbuilt_species": unbuilt_species, "picked_unknown_battles": picked_unknown}
    return reasons, summary


def accept_audit_check(records_by_cond: Optional[dict], conds: Sequence[str], n: int,
                       versions: Optional[dict] = None, limits: Optional[dict] = None) -> tuple:
    """測定の受入監査の検査 (純粋。2026-10-15 M3a、champions_agent.env.sim_accept)。records_by_cond = 条件名 → battles.jsonl の
    記録の列 (先頭 n 戦を見る)。各条件で (a) 第一候補が不合法 0、(b) 候補に未選出の個体 0、(c) 説明できない飛ばし 0、
    (d) ランダム退避 0、(e) 選出不明 0 (上限は config.SIM_ACCEPT_LIMITS)、受入監査の欄 (accept) の無い対戦が無い。
    versions = 名前 → 結果の inputs_version (条件 / part ごと)。今の版 (config.SIM_INPUTS_VERSION) でない・違う版が混ざれば不成立。
    戻り値 (成り立たない理由の list, 要約)"""
    from champions_agent.env.sim_accept import accept_violations, sum_battle_accept, version_warnings
    recs = records_by_cond or {}
    reasons, per = [], {}
    for c in conds:
        rows = list(recs.get(c) or [])[:n]
        tot = sum_battle_accept(rows)
        per[c] = tot
        if len(rows) < n:
            reasons.append(f"{c}: 受入監査を確かめる対戦記録が {len(rows)} / {n} 件しか無い")
        if tot["battles_missing"]:
            reasons.append(f"{c}: 受入監査の欄 (accept) の無い対戦 {tot['battles_missing']} 件 (修正前の版の記録の可能性)")
        reasons += accept_violations(tot, limits, label=c)
    if versions is not None:
        reasons += version_warnings(versions)
    return reasons, {"n": n, "per_condition": per, "versions": dict(versions or {})}


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


def correspondence_check(opp_ids_by_cond: Optional[dict], conds: Sequence[str], n: int) -> list:
    """対応比較が成り立つかの検査 (純粋)。全条件について相手 id の列が n 戦ぶん揃い、対戦 i の相手 id が条件間で一致する
    ことを確かめる。成り立たない理由の list (空なら成立)。統計的な判定不能とは別の「比較条件の不成立」で、最終判定を出さない"""
    reasons = []
    ids = opp_ids_by_cond or {}
    missing = [c for c in conds if not ids.get(c)]
    if missing:
        reasons.append(f"相手 id の列が無い条件: {', '.join(missing)} (battles.jsonl の opponent_team_id)")
    short = {c: len(ids[c]) for c in conds if ids.get(c) and len(ids[c]) < n}
    if short:
        reasons.append(f"相手 id の列が {n} 戦に足りない: {short}")
    if not missing and not short:
        present = {c: ids[c] for c in conds}
        mism = opponent_mismatches(present, n)
        if mism:
            reasons.append(f"相手 id が条件間で食い違う対戦が {mism} 件 (条件の取り違えか相手列の不一致)")
    return reasons


def holdout_banner(conditions: Optional[dict]) -> str:
    """封印 holdout 層での確認の 1 行 (純粋。usefulness_p1 の条件表の purpose と報告で同じ文言)"""
    n = ((conditions or {}).get("opponents") or {}).get("n_teams_in_tier")
    return f"封印 holdout 層 ({n if n is not None else '?'} 構築) での 1 回の確認、search の結果とは合算しない"


def holdout_tier_check(opp_ids_by_cond: Optional[dict], conds: Sequence[str], holdout_ids: Optional[Sequence[str]]) -> tuple:
    """相手 id が全件 holdout 層に属するかの事後の検査 (純粋。2026-10-15)。opp_ids_by_cond = 条件名 → battles.jsonl の
    opponent_team_id の列 (記録された全件を見る)。holdout_ids が None (分割ファイルが読めない) なら確かめられないので不成立。
    戻り値 (成り立たない理由の list, 要約)"""
    ids = opp_ids_by_cond or {}
    if holdout_ids is None:
        return ["holdout 層の id を読めず、相手 id が holdout 層に属することを確かめられない"], {"checked": 0, "outside": {}}
    hs = set(holdout_ids)
    outside: dict = {}
    checked = 0
    for c in conds:
        for x in ids.get(c) or []:
            checked += 1
            if x not in hs:
                outside.setdefault(c, {})
                outside[c][str(x)] = outside[c].get(str(x), 0) + 1
    reasons = []
    if outside:
        reasons.append(f"holdout 層に無い相手 id の対戦: "
                       + ", ".join(f"{c} {sum(v.values())} 件 ({', '.join(sorted(v)[:5])})" for c, v in outside.items()))
    return reasons, {"checked": checked, "outside": outside, "n_holdout_ids": len(hs)}


def build_verdict(conditions: dict, outcomes_by_cond: dict, weights: Sequence[float] = P1_WEIGHTS,
                  baseline_weight: float = P1_BASELINE_WEIGHT, final_battles: int = P1_FINAL_BATTLES,
                  mde: float = P1_MDE, alpha: float = P1_ALPHA, sources: Optional[dict] = None,
                  opp_ids_by_cond: Optional[dict] = None, elapsed_by_cond: Optional[dict] = None,
                  baseline_cond: Optional[str] = None, compare_conds: Optional[Sequence[str]] = None,
                  scoring_stats: Optional[dict] = None, own_records_by_cond: Optional[dict] = None,
                  extra_invalid: Sequence[str] = (), accept_records_by_cond: Optional[dict] = None,
                  inputs_versions: Optional[dict] = None) -> dict:
    """集計 (純粋)。conditions = 条件表 (incomplete を見る)。outcomes_by_cond = 条件名 → 勝敗列 (1/0)。
    baseline_cond / compare_conds (採点方式の比較: scoring:legacy / [scoring:transition]) を渡せばその条件名で比べる
    (無ければ重みの条件 rl<W>)。scoring_stats (条件名 → scoring_fallback_summary) があれば結果に残す。
    own_records_by_cond (条件名 → battles.jsonl の記録。2026-10-09 M2d) を渡せば自チームの対応 (own_correspondence_check) も
    確かめ、成り立たなければ比較条件の不成立 (invalid)。渡さなければ確かめない (RL の比較・M2d より前の条件表)。
    accept_records_by_cond (条件名 → battles.jsonl の記録。2026-10-15 M3a) を渡せば受入監査 (accept_audit_check、inputs_versions =
    名前 → 結果の版) も確かめ、成り立たなければ invalid。渡さなければ確かめない (2026-10-15 より前の条件表)"""
    if baseline_cond is not None:
        base = baseline_cond
        others = [c for c in (compare_conds or []) if c != base]
    else:
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
    # 対応の確認 (全条件の対象 n 戦ぶんの相手 id が揃い、条件間で一致する)。できなければ比較条件の不成立 → 採否を出さない
    invalid_reasons = correspondence_check(opp_ids_by_cond, [base] + others, n) if not missing else []
    own_summary = None
    if own_records_by_cond is not None and not missing:
        own_reasons, own_summary = own_correspondence_check(own_records_by_cond, [base] + others, n)
        invalid_reasons = invalid_reasons + own_reasons
    accept_summary = None
    if accept_records_by_cond is not None and not missing:
        acc_reasons, accept_summary = accept_audit_check(accept_records_by_cond, [base] + others, n, inputs_versions)
        invalid_reasons = invalid_reasons + acc_reasons
    # 2 夜分割の結合の検査 (2026-10-09 M2e、merge_parts) で成り立たなかった理由
    invalid_reasons = invalid_reasons + list(extra_invalid or [])
    if invalid_reasons:
        mode = MODE_INVALID
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
    out = {"mode": mode, "interim_reasons": reasons, "invalid_reasons": invalid_reasons, "k": k, "alpha": alpha, "level": 1.0 - alpha / max(1, k), "z": z,
           "mde": mde, "final_battles": final_battles, "n_used": n, "baseline": base, "comparisons": comparisons,
           "per_condition": per_cond, "cost": cost, "opponent_mismatches": mism, "notes": notes}
    if scoring_stats is not None:
        out["scoring_stats"] = dict(scoring_stats)
    if own_summary is not None:
        out["own_correspondence"] = own_summary
    if accept_summary is not None:
        out["accept_audit"] = accept_summary
    return out


def _f(x, nd: int = 3, signed: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}"


def team_summary(team: dict) -> tuple:
    """条件表の team → 表示用の (項目, 値) (純粋)。source が無い (RL の比較の登録チーム) なら従来の表示"""
    src = (team or {}).get("source")
    if src == "pool":
        return ("自チーム", f"{TEAM_LABELS['pool']} ({team.get('path')}、使用率スナップショット {team.get('snapshot_id')}、"
                            f"{team.get('battle_format')}、{team.get('n_teams')} 件 sha16={team.get('sha16')}、抽選 seed {team.get('seed')}、"
                            f"登録参照は無効化)")
    if src == "ranked" and team.get("pinned"):
        return ("自チーム", f"{TEAM_LABELS['ranked']} 【旧固定プール上の比較】 (使用率スナップショット {team.get('meta_snapshot_id')}、"
                            f"抽選 seed {team.get('seed')}、プール {team.get('n_teams')} 件 sha16={team.get('pool_sha16')}、登録参照は無効化)")
    if src == "ranked":
        return ("自チーム", f"{TEAM_LABELS['ranked']} (使用率スナップショット {team.get('meta_snapshot_id')}、"
                            f"抽選 seed {team.get('seed')}、プール {team.get('n_teams')} 件 sha16={team.get('pool_sha16')}、登録参照は無効化)")
    if src == "registered":
        return ("自チーム", f"{TEAM_LABELS['registered']} sha16={team.get('sha16')} (期待 {team.get('expected_sha16')})")
    return ("登録チーム sha16", f"{(team or {}).get('sha16')} (期待 {(team or {}).get('expected_sha16')})")


def condition_summary(conditions: dict) -> list:
    """条件表の要約 (純粋): 表示用の (項目, 値) の列"""
    c = conditions or {}
    team = c.get("team") or {}
    sel = c.get("selection_model") or {}
    rl = c.get("rl_checkpoint") or {}
    opp = c.get("opponents") or {}
    git = c.get("git") or {}
    if c.get("factor") == "scoring":
        base, others = scoring_conds(c)
        design = ("比較 / 基準 / k", f"{others} − {base} / {base} / {c.get('k')} (RL 加点の重みは両条件とも ×{c.get('baseline_weight')})")
    else:
        design = ("重み / 基準 / k", f"{c.get('weights')} / ×{c.get('baseline_weight')} / {c.get('k')}")
    if is_holdout(c):
        # 封印 holdout 層 (2026-10-15): fold は使わないので表示しない
        opp_row = ("相手", f"封印 holdout 層 ({opp.get('n_teams_in_tier')} 構築) seed={opp.get('seed')} offset={opp.get('offset')} "
                          f"操縦={opp.get('pilot')}/{opp.get('pick_policy')} 封印 id={opp.get('sealed_id')}")
    else:
        opp_row = ("相手", f"{opp.get('tier')} fold {opp.get('fold_label')} seed={opp.get('seed')} offset={opp.get('offset')} "
                          f"操縦={opp.get('pilot')}/{opp.get('pick_policy')} 封印 id={opp.get('sealed_id')}")
    return [
        ("実験", f"{c.get('experiment', '-')}{' (予備)' if c.get('prelim') else ''}"),
        ("開始", str(c.get("started_at") or "-")),
        design,
        ("対戦数 (この測定 / 最終)", f"{c.get('battles')} / {c.get('final_battles')}"),
        team_summary(team),
        ("選出モデル", f"{sel.get('path')} sha16={sel.get('sha16')}"),
        ("RL checkpoint", f"{rl.get('file')} sha16={rl.get('sha16')} pin={rl.get('pin_dir')}"),
        opp_row,
        ("git", f"{git.get('commit')}"),
        ("未完了", str(c.get("incomplete"))),
    ]


def render(verdict: dict, conditions: Optional[dict] = None) -> str:
    """verdict.md / 標準出力の本文 (純粋)"""
    v = verdict
    lines = ["# P1 対応比較の集計", ""]
    if is_holdout(conditions):
        lines += [f"**{holdout_banner(conditions)}**", ""]
    if conditions is not None:
        lines += ["## 条件表の要約", "", "| 項目 | 値 |", "|---|---|"]
        lines += [f"| {k} | {val} |" for k, val in condition_summary(conditions)]
        lines.append("")
    if v["mode"] == MODE_INVALID:
        lines += ["## 比較条件の不成立 (採否は出さない)", "", "理由: " + " / ".join(v["invalid_reasons"]), ""]
        if v["interim_reasons"]:
            lines += ["(統計的な中間確認の理由も併記: " + " / ".join(v["interim_reasons"]) + ")", ""]
    elif v["mode"] == MODE_INTERIM:
        lines += ["## 中間確認 (採否は出さない)", "", "理由: " + " / ".join(v["interim_reasons"]), ""]
    else:
        lines += ["## 採否 (§0.5、最終対戦数で 1 回だけ)", ""]
    lines += [f"比較 k = {v['k']}、区間の水準 {v['level']:.4f} (両側 z = {v['z']:.4f})、MDE = {v['mde']}、使った n = {v['n_used']}", "",
              "| 比較 | n | 平均差 | SE | 補正区間 | 判定 |", "|---|---|---|---|---|---|"]
    for r in v["comparisons"]:
        dec = DECISION_LABELS.get(r["decision"], "-") if r["decision"] else (
            "中間確認" if v["mode"] == MODE_INTERIM else ("不成立" if v["mode"] == MODE_INVALID else "-"))
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
    if v.get("scoring_stats"):
        lines += ["", "### 採点方式と退避率 (transition で採点した決定のうち legacy に退避した割合)", "",
                  "| 条件 | 方式 | transition の決定数 | 退避数 | 退避率 | 退避の理由 (上位) |", "|---|---|---|---|---|---|"]
        for c, s in v["scoring_stats"].items():
            if not s:
                lines.append(f"| {c} | - | - | - | - | (json なし) |")
                continue
            top = sorted((s.get("fallback_by_reason") or {}).items(), key=lambda kv: -kv[1])[:3]
            reasons = ", ".join(f"{k} {n}" for k, n in top) or "-"
            lines.append(f"| {c} | {s.get('advice_scoring_mode') or '-'} | {s.get('scoring_transition')} | {s.get('scoring_fallback')} | "
                         f"{_f(s.get('fallback_rate'))} | {reasons} |")
    if v.get("scoring_stats"):
        lines += ["", "### 本採点 / 感度 / 全処理時間 (transition。2026-10-09 M2d)", "",
                  "| 条件 | 本採点 p50 / p95 ms | 本採点の予算超過の退避 | 感度 (有効 / 状態 / 省略) | 全処理 p50 / p95 ms | 未評価の応手が出た決定 (技の上位) |",
                  "|---|---|---|---|---|---|"]
        for c, s in v["scoring_stats"].items():
            if not s or not s.get("scoring_transition"):
                continue
            m, sn, t, u = s.get("main") or {}, s.get("sens") or {}, s.get("total") or {}, s.get("unevaluated") or {}
            top = ", ".join(f"{k} {n}" for k, n in list((u.get("by_move") or {}).items())[:3]) or "-"
            lines.append(f"| {c} | {_f(m.get('p50_ms'), 1)} / {_f(m.get('p95_ms'), 1)} | {m.get('budget_fallback')} "
                         f"({_f(m.get('budget_fallback_rate'))}) | {sn.get('enabled')} / {sn.get('status') or '-'} / "
                         f"{sn.get('omitted') or '-'} | {_f(t.get('p50_ms'), 1)} / {_f(t.get('p95_ms'), 1)} | {u.get('n')} ({top}) |")
    if v.get("own_correspondence") is not None:
        oc = v["own_correspondence"]
        lines += ["", f"自チームの対応 (送った本文の sha16 の条件間の一致・参照した型の一致): 食い違い {oc['hash_mismatch']} 件 / "
                      f"参照の不一致 {oc['ref_mismatch_battles'] or 0} / 確認できない {oc['unverifiable'] or 0} / "
                      f"助言の図鑑に無い個体を含む対戦 {oc['unbuilt_battles'] or 0}"]
    if v.get("accept_audit") is not None:
        # 受入監査の違反でない件数の表示 (2026-10-16 E-holdout-1: 蘇生先の選択は違反に数えない)
        from champions_agent.env.sim_accept import INFO_LABELS
        per = v["accept_audit"].get("per_condition") or {}
        for k, label in INFO_LABELS.items():
            lines += ["", f"{label} (受入の違反ではない): " +
                      (" / ".join(f"{c} {int((t or {}).get(k) or 0)} 件" for c, t in per.items()) or "-")]
    if v["opponent_mismatches"] is not None:
        lines += ["", f"相手 id の条件間の食い違い: {v['opponent_mismatches']} 件"]
    if v.get("holdout") is not None:
        h = v["holdout"]
        n_out = sum(sum(x.values()) for x in (h.get("outside") or {}).values())
        lines += ["", f"相手 id の層の検査 (封印 holdout 層 {h.get('n_holdout_ids', '-')} 構築): 検査 {h.get('checked', 0)} 件 / "
                      f"層の外 {n_out} 件 / 分割ファイルの sha16 {h.get('split_sha16_now') or '-'} (条件表 {h.get('split_sha16') or '-'})"]
    if v.get("inputs_pin") is not None:
        ips = v["inputs_pin"]
        ips = ips if "pin_dir" not in ips else {"": ips}
        for k, s in ips.items():
            lines += ["", f"入力の実体の固定{(' (' + k + ')') if k else ''}: 選出モデル {s.get('selection_sha16') or '-'} / "
                          f"使用率 snapshot {s.get('usage_snapshot')} (bundle {s.get('bundle_sha16') or '-'}) / "
                          f"バンク {s.get('bank_sha16') or '-'} / 照合 {'OK' if not s.get('reasons') else 'NG ' + str(len(s['reasons'])) + ' 件'}"]
    if v["notes"]:
        lines += ["", "### 注記", ""] + [f"- {x}" for x in v["notes"]]
    return "\n".join(lines) + "\n"


# 2 夜分割の part の間で同じでなければならない条件表の項目 (2026-10-09 M2e。merge_parts)
PART_SAME_KEYS = (("factor",), ("baseline",), ("comparisons",), ("final_battles",), ("opponents", "seed"),
                  ("opponents", "tier"), ("opponents", "fold"), ("opponents", "split_sha16"), ("team", "source"),
                  ("team", "sha16"), ("git", "commit"), ("showdown", "commit"), ("scoring_time", "time_limit_total_sec"),
                  ("holdout_check", "candidate_key"))


def _dig(d: Optional[dict], path: tuple):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def merge_parts(parts: Sequence[dict], conds: Sequence[str]) -> dict:
    """2 夜分割の part の結合 (純粋。2026-10-09 M2e)。parts = [{"dir", "conditions", "outcomes", "sources", "opp_ids",
    "elapsed", "records"}] (load_part の結果)。part の範囲 (conditions.part.start) の順に条件ごとに連結する。
    戻り値 {"conditions" (結合した条件表), "outcomes", "sources", "opp_ids", "elapsed", "records", "invalid" (比較条件の不成立の
    理由), "notes"}。範囲の抜けと part の未完了は incomplete (対戦数の不足として中間確認)、範囲の重複・条件の欠け・比較の条件の
    食い違いは invalid"""
    invalid, notes, incomplete_reasons = [], [], []
    infos = []
    for i, pt in enumerate(parts):
        c = pt.get("conditions") or {}
        pr = c.get("part")
        if not isinstance(pr, dict):
            invalid.append(f"{pt.get('dir')}: 条件表に part が無い (usefulness_p1 --part で測った出力ではない)")
            continue
        infos.append((int(pr.get("start", 0)), int(pr.get("end", 0)), i, pr))
    infos.sort()
    ns = {pr.get("n") for _s, _e, _i, pr in infos}
    ks = [pr.get("k") for _s, _e, _i, pr in infos]
    if len(ns) > 1:
        invalid.append(f"part の分割数 n が違う: {sorted(ns)}")
    if len(set(ks)) != len(ks):
        invalid.append(f"同じ part (k) が重複している: {ks}")
    prev_end = None
    total = None
    for start, end, i, pr in infos:
        total = pr.get("total_battles", total)
        if prev_end is not None and start < prev_end:
            invalid.append(f"part の範囲が重複する: {start} < 前の part の終わり {prev_end}")
        elif prev_end is not None and start > prev_end:
            incomplete_reasons.append(f"part の範囲に抜けがある: {prev_end}〜{start - 1}")
        prev_end = end if prev_end is None else max(prev_end, end)
    if infos and infos[0][0] != 0:
        incomplete_reasons.append(f"part の範囲が 0 から始まらない (最初は {infos[0][0]})")
    if infos and total is not None and prev_end is not None and prev_end < int(total):
        incomplete_reasons.append(f"part の範囲が {total} 戦に届かない (最後は {prev_end})")
    if infos and ns and len(ns) == 1 and len(infos) < int(next(iter(ns)) or 0):
        incomplete_reasons.append(f"part が {len(infos)} / {next(iter(ns))} 個しか無い")
    base_c = (parts[infos[0][2]].get("conditions") or {}) if infos else {}
    for path in PART_SAME_KEYS:
        vals = {repr(_dig(parts[i].get("conditions"), path)) for _s, _e, i, _p in infos}
        if len(vals) > 1:
            invalid.append(f"比較の条件が part の間で違う ({'.'.join(path)}): {sorted(vals)}")
    outcomes = {c: [] for c in conds}
    opp_ids = {c: [] for c in conds}
    records = {c: [] for c in conds}
    elapsed = {c: 0.0 for c in conds}
    sources = {c: [] for c in conds}
    part_rows = []
    elapsed_total = 0.0
    for start, end, i, pr in infos:
        pt = parts[i]
        c_doc = pt.get("conditions") or {}
        want = end - start
        if int(_dig(c_doc, ("opponents", "offset")) or 0) != int(pr.get("opp_offset", start)):
            invalid.append(f"part {pr.get('k')}/{pr.get('n')}: 相手列の offset {_dig(c_doc, ('opponents', 'offset'))} が範囲の先頭 "
                           f"{pr.get('opp_offset', start)} と違う")
        lacking = [c for c in conds if c not in (pt.get("outcomes") or {})]
        if lacking:
            invalid.append(f"part {pr.get('k')}/{pr.get('n')} ({pt.get('dir')}) に条件の結果が無い: {lacking}")
        got = {c: len((pt.get("outcomes") or {}).get(c) or []) for c in conds}
        if c_doc.get("incomplete") or any(v < want for v in got.values()):
            incomplete_reasons.append(f"part {pr.get('k')}/{pr.get('n')} が未完了 (到達 {got} / {want} 戦"
                                      + (f"、{' / '.join(c_doc.get('incomplete_reasons') or [])}" if c_doc.get("incomplete") else "") + ")")
        for c in conds:
            # 範囲の対戦数で切りそろえる (条件間で長さが違えば、短い方に合わせるのは build_verdict の align)
            m = min(want, min(got.values()) if got else 0)
            outcomes[c] += list((pt.get("outcomes") or {}).get(c) or [])[:m]
            opp_ids[c] += list((pt.get("opp_ids") or {}).get(c) or [])[:m]
            records[c] += list((pt.get("records") or {}).get(c) or [])[:m]
            e = (pt.get("elapsed") or {}).get(c)
            if e is None:
                e = c_doc.get("elapsed_sec")
            elapsed[c] += float(e or 0.0)
            sources[c].append(f"part {pr.get('k')}/{pr.get('n')}: {(pt.get('sources') or {}).get(c)}")
        el = c_doc.get("elapsed_sec")
        elapsed_total += float(el or 0.0)
        part_rows.append({"k": pr.get("k"), "n": pr.get("n"), "start": start, "end": end, "dir": pt.get("dir"),
                          "elapsed_sec": el, "incomplete": c_doc.get("incomplete"), "started_at": c_doc.get("started_at")})
    limit = _dig(base_c, ("scoring_time", "time_limit_total_sec"))
    if limit is not None and elapsed_total > float(limit):
        incomplete_reasons.append(f"全 part の所要秒の合計 {elapsed_total:.0f} が全体の上限 {float(limit):.0f} 秒を超える")
    merged = dict(base_c)
    merged.update(battles=sum(len(outcomes[c]) for c in conds[:1]) if conds else 0,
                  incomplete=bool(incomplete_reasons),
                  incomplete_reasons=incomplete_reasons, elapsed_sec=round(elapsed_total, 1),
                  parts=part_rows, merged_from=[pt.get("dir") for pt in parts])
    merged.pop("part", None)
    ranges = ", ".join("%s/%s [%s, %s)" % (r["k"], r["n"], r["start"], r["end"]) for r in part_rows)
    notes.append(f"2 夜分割の結合: {len(part_rows)} part ({ranges})")
    return {"conditions": merged, "outcomes": {c: v for c, v in outcomes.items() if v},
            "sources": {c: " + ".join(v) for c, v in sources.items()}, "opp_ids": {c: v for c, v in opp_ids.items() if v},
            "elapsed": {c: (v if v > 0 else None) for c, v in elapsed.items()},
            "records": {c: v for c, v in records.items() if v}, "invalid": invalid, "notes": notes}


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
    return load_outcomes_conds(out_dir, [cond_name(w) for w in weights])


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_outcomes_conds(out_dir: Path, conds: Sequence[str]) -> tuple:
    """条件名の列で (勝敗列, 出所, 相手 id 列, 経過秒) を読む。ファイル名は cond_file (scoring:legacy → scoring_legacy.json)"""
    outcomes, sources, opp_ids, elapsed = {}, {}, {}, {}
    for c in conds:
        f = cond_file(c)
        js, jl = out_dir / f"{f}.json", out_dir / f"{f}.battles.jsonl"
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


def load_records_conds(out_dir: Path, conds: Sequence[str]) -> dict:
    """条件名 → battles.jsonl の記録の列 (自チームの対応の検査用。ファイルが無い条件は入れない)"""
    out = {}
    for c in conds:
        jl = out_dir / f"{cond_file(c)}.battles.jsonl"
        if jl.exists():
            out[c] = _read_jsonl(jl)
    return out


def load_versions_conds(out_dir: Path, conds: Sequence[str]) -> dict:
    """条件名 → 結果 JSON の inputs_version (2026-10-15 M3a)。JSON が無ければ battles.jsonl の accept.inputs_version (全対戦で
    1 つならそれ、混ざれば "mixed:..."、無ければ None)"""
    out = {}
    for c in conds:
        doc = _read_json(out_dir / f"{cond_file(c)}.json")
        if doc is not None:
            out[c] = doc.get("inputs_version")
            continue
        vs = {((r.get("accept") or {}).get("inputs_version") if isinstance(r.get("accept"), dict) else None)
              for r in _read_jsonl(out_dir / f"{cond_file(c)}.battles.jsonl")}
        out[c] = (next(iter(vs)) if len(vs) == 1 else ("mixed:" + ",".join(sorted(str(v) for v in vs)) if vs else None))
    return out


def accept_required(conditions: dict) -> bool:
    """条件表が受入監査を指定しているか (usefulness_p1 が 2026-10-15 M3a から書く accept_check.required)"""
    return bool((conditions.get("accept_check") or {}).get("required"))


ACCEPT_MISSING_NOTE = ("条件表に受入監査の指定 (accept_check.required) が無い (2026-10-15 より前の条件表): 第一候補の合法性・"
                       "未選出の候補・ランダム退避・入力の版は未確認")


def holdout_post_check(conditions: dict, opp_ids_by_cond: Optional[dict], conds: Sequence[str]) -> tuple:
    """封印 holdout 層での確認の事後の検査 (副作用: 分割ファイルの読み取り。2026-10-15)。条件表の分割ファイルを読み直し、
    sha16 が条件表の split_sha16 と一致することと、相手 id が全件 holdout 層に属すること (holdout_tier_check) を確かめる。
    戻り値 (成り立たない理由の list, 要約)"""
    from advisor.versions import sha256_file
    opp = (conditions or {}).get("opponents") or {}
    sf = opp.get("split_file")
    path = Path(sf) if sf else None
    if path is not None and not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    reasons, holdout_ids, sha_now = [], None, None
    if path is None:
        reasons.append("条件表に分割ファイル (opponents.split_file) が無い")
    else:
        sha_now = sha256_file(path)
        if sha_now is None:
            reasons.append(f"分割ファイル {path} が読めない")
        elif opp.get("split_sha16") and sha_now != opp.get("split_sha16"):
            reasons.append(f"分割ファイル {path} の sha16 {sha_now} が条件表の {opp.get('split_sha16')} と違う")
        else:
            try:
                from tools.team_build.opponents import load_split, tier_ids
                holdout_ids = tier_ids(load_split(path), HOLDOUT_TIER)
            except Exception as e:
                reasons.append(f"分割ファイル {path} の holdout 層が読めない ({e!r})")
    tier_reasons, summary = holdout_tier_check(opp_ids_by_cond, conds, holdout_ids)
    if holdout_ids is not None:
        reasons += tier_reasons
    summary.update(split_file=str(path) if path else None, split_sha16=opp.get("split_sha16"), split_sha16_now=sha_now,
                   banner=holdout_banner(conditions))
    return reasons, summary


INPUTS_PIN_MISSING_NOTE = ("条件表に入力の実体の固定 (inputs_pin) が無い (2026-10-23 より前の条件表): 選出モデルの実体・使用率の "
                           "snapshot・バンクは sha16 の記録だけで、ピンからの復元は確かめていない")


def inputs_pin_post_check(conditions: dict, out_dir: Path, conds: Sequence[str]) -> tuple:
    """入力の実体の固定の事後の検査 (副作用: ピンと結果 JSON の読み取り。2026-10-23、tools.inputs_pin)。条件表に inputs_pin
    (ピン作成後の manifest) があれば、ピンのファイルの sha256 が manifest と一致すること (verify_pin) と、各条件の結果 JSON の
    inputs_pin (子プロセスが起動時に照合した版) がピンと同じことを確かめる。欠落・sha 不一致は比較条件の不成立 (採否を出さない。
    最新のデータで代わりに確かめない)。結果 JSON の無い条件 (時間上限で止めた等) は子プロセスの版を確かめない (要約に残す)。
    戻り値 (成り立たない理由の list, 要約 or None (条件表に inputs_pin が無い))"""
    from tools import inputs_pin as IP
    rec = (conditions or {}).get("inputs_pin")
    if not isinstance(rec, dict) or rec.get("planned"):
        return [], None
    pin = rec.get("pin_dir")
    reasons = IP.verify_pin(Path(pin), rec) if pin else ["条件表の inputs_pin にピンの dir が無い"]
    docs = {c: _read_json(Path(out_dir) / f"{cond_file(c)}.json") for c in conds}
    got = {c: d.get("inputs_pin") for c, d in docs.items() if d is not None}
    child_reasons, missing = IP.child_inputs_reasons(rec, got)
    reasons = list(reasons) + child_reasons + [f"{c}: 結果 JSON に子プロセスの入力の版 (inputs_pin) の記録が無い" for c in missing]
    summary = {"pin_dir": pin, "selection_sha16": (rec.get("selection_model") or {}).get("sha16"),
               "usage_snapshot": (rec.get("usage_bundle") or {}).get("snapshot_id"),
               "bundle_sha16": (rec.get("usage_bundle") or {}).get("sha16"), "bank_sha16": (rec.get("real_bank") or {}).get("sha16"),
               "unchecked_conditions": [c for c, d in docs.items() if d is None], "reasons": list(reasons)}
    return reasons, summary


def load_part(out_dir: Path) -> dict:
    """1 part の出力 dir → merge_parts の入力 (採点方式の比較の条件で読む)"""
    cpath = out_dir / "conditions.json"
    conditions = _read_json(cpath) or {}
    base_c, other_c = scoring_conds(conditions)
    conds = [base_c] + other_c
    outcomes, sources, opp_ids, elapsed = load_outcomes_conds(out_dir, conds)
    return {"dir": str(out_dir), "conditions": conditions, "outcomes": outcomes, "sources": sources, "opp_ids": opp_ids,
            "elapsed": elapsed, "records": load_records_conds(out_dir, conds), "conds": conds,
            "versions": load_versions_conds(out_dir, conds),
            "sstats": {c: scoring_fallback_summary(_read_json(out_dir / f"{cond_file(c)}.json")) for c in conds}}


def run_merged(out_dirs: Sequence[Path], merged_out: Path) -> dict:
    """2 夜分割の全 part を結合して 1 回だけ判定する (2026-10-09 M2e)。結果は merged_out に書く"""
    parts = [load_part(Path(d)) for d in out_dirs]
    conds = parts[0]["conds"] if parts else []
    m = merge_parts(parts, conds)
    conditions = m["conditions"]
    final_battles = int(conditions.get("final_battles") or P1_FINAL_BATTLES)
    mde = float(conditions.get("mde", P1_MDE))
    alpha = float(conditions.get("alpha", P1_ALPHA))
    base_c, other_c = scoring_conds(conditions)
    # 退避率は part ごとの json の stats を足し合わせない (part ごとに残す。決定単位の集計は別の道具)
    sstats = {f"{c} (part {p['conditions'].get('part', {}).get('k')})": p["sstats"].get(c) for p in parts for c in conds}
    own_recs = m["records"] if (conditions.get("own_check") or {}).get("required") else None
    acc_req = accept_required(conditions)
    versions = {f"{c} (part {p['conditions'].get('part', {}).get('k')})": (p.get("versions") or {}).get(c)
                for p in parts for c in conds} if acc_req else None
    # 封印 holdout 層 (2026-10-15): 相手 id が全件 holdout 層に属することを事後にも確かめる
    ho_reasons, ho_summary = holdout_post_check(conditions, m["opp_ids"], conds) if is_holdout(conditions) else ([], None)
    # 入力の実体の固定 (2026-10-23): part ごとのピン (part ごとに別のピン) と子プロセスの版
    pin_reasons, pin_summaries = [], {}
    for p in parts:
        r, s = inputs_pin_post_check(p["conditions"], Path(p["dir"]), conds)
        k = (p["conditions"].get("part") or {}).get("k")
        pin_reasons += [f"part {k}: {x}" for x in r]
        if s is not None:
            pin_summaries[f"part {k}"] = s
    v = build_verdict(conditions, m["outcomes"], tuple(conditions.get("weights") or P1_WEIGHTS),
                      float(conditions.get("baseline_weight", P1_BASELINE_WEIGHT)), final_battles, mde, alpha, m["sources"],
                      m["opp_ids"], m["elapsed"], baseline_cond=base_c, compare_conds=other_c, scoring_stats=sstats,
                      own_records_by_cond=own_recs, extra_invalid=list(m["invalid"]) + ho_reasons + pin_reasons,
                      accept_records_by_cond=m["records"] if acc_req else None, inputs_versions=versions)
    if ho_summary is not None:
        v["holdout"] = ho_summary
    if pin_summaries:
        v["inputs_pin"] = pin_summaries
    if len(pin_summaries) < len(parts):
        v["notes"].append(INPUTS_PIN_MISSING_NOTE)
    v["notes"] = list(m["notes"]) + list(v["notes"])
    if not acc_req:
        v["notes"].append(ACCEPT_MISSING_NOTE)
    v["parts"] = conditions.get("parts")
    text = render(v, conditions)
    merged_out.mkdir(parents=True, exist_ok=True)
    (merged_out / "conditions_merged.json").write_text(json.dumps(conditions, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
    (merged_out / "verdict.json").write_text(json.dumps(v, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (merged_out / "verdict.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"保存: {merged_out / 'verdict.json'} / {merged_out / 'verdict.md'}")
    return v


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
    scoring = conditions.get("factor") == "scoring"
    base_c, other_c, sstats, own_recs = None, None, None, None
    if scoring:
        # 採点方式の比較: 基準 scoring:legacy、比較 scoring:transition − legacy (k = 1)。退避率は各条件の json の stats
        base_c, other_c = scoring_conds(conditions)
        outcomes, sources, opp_ids, elapsed = load_outcomes_conds(out_dir, [base_c] + other_c)
        sstats = {c: scoring_fallback_summary(_read_json(out_dir / f"{cond_file(c)}.json")) for c in [base_c] + other_c}
        # 自チームの対応 (2026-10-09 M2d): 条件表が検査を指定していれば (usefulness_p1 --compare scoring の own_check.required)
        if (conditions.get("own_check") or {}).get("required"):
            own_recs = load_records_conds(out_dir, [base_c] + other_c)
    else:
        outcomes, sources, opp_ids, elapsed = load_outcomes(out_dir, weights)
    # 受入監査 (2026-10-15 M3a): 条件表が指定していれば battles.jsonl の accept と結果の版を確かめる
    acc_recs, versions = None, None
    if accept_required(conditions):
        conds_all = ([base_c] + other_c) if scoring else [cond_name(w) for w in weights]
        acc_recs = load_records_conds(out_dir, conds_all)
        versions = load_versions_conds(out_dir, conds_all)
    # 時間上限で止めた等で rl<W>.json に経過秒が無ければ、条件表の所要秒で代用する (並列なので全条件で同じ)
    for c in outcomes:
        if elapsed.get(c) is None and conditions.get("elapsed_sec") is not None:
            elapsed[c] = conditions["elapsed_sec"]
    # 封印 holdout 層 (2026-10-15): 相手 id が全件 holdout 層に属することを事後にも確かめる
    ho_reasons, ho_summary = [], None
    if is_holdout(conditions):
        conds_all = ([base_c] + other_c) if scoring else [cond_name(w) for w in weights]
        ho_reasons, ho_summary = holdout_post_check(conditions, opp_ids, conds_all)
    # 入力の実体の固定 (2026-10-23): ピンのファイルと子プロセスの版
    conds_all = ([base_c] + other_c) if scoring else [cond_name(w) for w in weights]
    pin_reasons, pin_summary = inputs_pin_post_check(conditions, out_dir, conds_all)
    v = build_verdict(conditions, outcomes, weights, baseline, final_battles, mde, alpha, sources, opp_ids, elapsed,
                      baseline_cond=base_c, compare_conds=other_c, scoring_stats=sstats, own_records_by_cond=own_recs,
                      accept_records_by_cond=acc_recs, inputs_versions=versions, extra_invalid=ho_reasons + pin_reasons)
    if ho_summary is not None:
        v["holdout"] = ho_summary
    if pin_summary is not None:
        v["inputs_pin"] = pin_summary
    else:
        v["notes"].append(INPUTS_PIN_MISSING_NOTE)
    if acc_recs is None:
        v["notes"].append(ACCEPT_MISSING_NOTE)
    if scoring and own_recs is None:
        v["notes"].append("条件表に自チームの対応の検査の指定 (own_check.required) が無い (M2d より前の条件表): 自チームの型の対応は未確認")
    if isinstance(conditions.get("part"), dict):
        pr = conditions["part"]
        v["notes"].append(f"この出力は 2 夜分割の part {pr.get('k')}/{pr.get('n')} (対戦 {pr.get('start')}〜{pr.get('end', 0) - 1}) だけ。"
                          "最終判定は全 part を --out に並べて --merged-out で結合して 1 回だけ出す")
    text = render(v, conditions)
    (out_dir / "verdict.json").write_text(json.dumps(v, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "verdict.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"保存: {out_dir / 'verdict.json'} / {out_dir / 'verdict.md'}")
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description="P1 (RL 加点) の対応比較の集計と §0.5 の 3 値判定")
    ap.add_argument("--out", required=True, nargs="+",
                    help="tools/usefulness_p1.py の出力ディレクトリ (2 夜分割は全 part の dir を並べる)")
    ap.add_argument("--merged-out", default=None, help="2 夜分割の結合した判定の書き込み先 (--out が複数のとき必須)")
    args = ap.parse_args()
    if len(args.out) > 1:
        if not args.merged_out:
            raise SystemExit("--out を複数渡すとき (2 夜分割の結合) は --merged-out が要る")
        run_merged([Path(d) for d in args.out], Path(args.merged_out))
    else:
        run(Path(args.out[0]))


if __name__ == "__main__":
    main()
