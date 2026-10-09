"""有用性検証 P1 の §0.5 の 3 値判定 (tools/usefulness_verdict) のテスト。合成データだけで閉じる。

    python -m tests.test_usefulness_verdict
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import usefulness_verdict as V


def _pair(n_win_only_cond: int, n_win_only_base: int, n_both: int, n_neither: int) -> tuple:
    """d_i = +1 が n_win_only_cond 件、−1 が n_win_only_base 件、0 が残り になる (条件, 基準) の勝敗列"""
    cond = [1] * n_win_only_cond + [0] * n_win_only_base + [1] * n_both + [0] * n_neither
    base = [0] * n_win_only_cond + [1] * n_win_only_base + [1] * n_both + [0] * n_neither
    return cond, base


def test_corrected_z_by_k():
    assert abs(V.corrected_z(1, 0.05) - 1.959964) < 1e-5
    assert abs(V.corrected_z(2, 0.05) - 2.241403) < 1e-5     # 1 − 0.05/2 の両側
    assert abs(V.corrected_z(3, 0.05) - 2.393980) < 1e-5
    assert V.corrected_z(0, 0.05) == V.corrected_z(1, 0.05)   # k は 1 未満にしない
    assert V.corrected_z(2, 0.05) > V.corrected_z(1, 0.05)
    print("test_corrected_z_by_k OK")


def test_decide_boundaries():
    # 推定値が MDE ちょうど + 下限 > 0 → 採用候補 (≥ MDE)
    assert V.decide(0.05, 0.001, 0.099, 0.05) == V.ADOPT
    # 推定値が MDE 未満 → 下限 > 0 でも採用候補にしない
    assert V.decide(0.0499, 0.001, 0.099, 0.05) == V.INCONCLUSIVE
    # 下限 0 ちょうど → 採用候補にしない (> 0)
    assert V.decide(0.08, 0.0, 0.16, 0.05) == V.INCONCLUSIVE
    # 上限が MDE 未満 → 支持しない。上限 MDE ちょうどは支持しないにならない (< MDE)
    assert V.decide(0.0, -0.04, 0.0499, 0.05) == V.NOT_SUPPORTED
    assert V.decide(0.0, -0.05, 0.05, 0.05) == V.INCONCLUSIVE
    # 負の差で区間全体が 0 未満も「支持しない」
    assert V.decide(-0.1, -0.15, -0.05, 0.05) == V.NOT_SUPPORTED
    assert V.decide(None, None, None, 0.05) == V.INCONCLUSIVE
    print("test_decide_boundaries OK")


def test_compare_mean_exactly_mde():
    # 600 戦: +1 が 30 件、−1 が 0 件 → 平均差 30/600 = 0.05 ちょうど。分散が小さく下限 > 0
    cond, base = _pair(30, 0, 300, 270)
    r = V.compare(cond, base, V.corrected_z(2), 0.05, final=True)
    assert r["n"] == 600 and r["mean_diff"] == 0.05, r
    assert r["ci_low"] > 0 and r["decision"] == V.ADOPT, r
    # 同じ平均差でも分散が大きい (+1 が 165、−1 が 135) → 下限 ≤ 0 → 判定不能
    cond, base = _pair(165, 135, 150, 150)
    r = V.compare(cond, base, V.corrected_z(2), 0.05, final=True)
    assert r["mean_diff"] == 0.05 and r["ci_low"] <= 0 and r["decision"] == V.INCONCLUSIVE, r
    # 差がほぼ無く区間が狭い → 上限 < MDE → 支持しない
    cond, base = _pair(10, 10, 300, 280)
    r = V.compare(cond, base, V.corrected_z(2), 0.05, final=True)
    assert r["ci_high"] < 0.05 and r["decision"] == V.NOT_SUPPORTED, r
    print("test_compare_mean_exactly_mde OK")


def test_k_changes_decision():
    """同じデータでも k (多重比較) で z が広がり、k=1 なら採用候補・k=2 なら判定不能になる境界の例"""
    cond, base = _pair(166, 130, 150, 154)     # 平均差 0.06、SE ≈ 0.0286 (0.06/2.2414 < SE < 0.06/1.96)
    r1 = V.compare(cond, base, V.corrected_z(1), 0.05)
    r2 = V.compare(cond, base, V.corrected_z(2), 0.05)
    assert abs(r1["mean_diff"] - 0.06) < 1e-12
    assert r1["ci_low"] > 0 and r1["decision"] == V.ADOPT, r1
    assert r2["ci_low"] <= 0 and r2["decision"] == V.INCONCLUSIVE, r2
    assert (r2["ci_high"] - r2["ci_low"]) > (r1["ci_high"] - r1["ci_low"])
    print("test_k_changes_decision OK")


def _ids(n: int) -> list:
    return [f"t{i % 33}" for i in range(n)]


def _outcomes_600():
    """3 条件で基準 (rl25) の列を共有する合成データ: rl0 は平均差 +0.05 (分散小 → 採用候補)、rl5 は平均差 0 (→ 支持しない)"""
    c0, b = _pair(30, 0, 300, 270)      # b = 基準: 先頭 30 敗、続く 300 勝、残り 270 敗
    c5 = list(b)
    for i in range(10):
        c5[i] = 1                       # 基準が負けた対戦で 10 勝 (d = +1)
    for i in range(30, 40):
        c5[i] = 0                       # 基準が勝った対戦で 10 敗 (d = −1)
    return {"rl0": c0, "rl5": c5, "rl25": b}


def test_build_verdict_final_and_interim():
    outs = _outcomes_600()
    ids = {c: _ids(600) for c in outs}          # 対応の確認には全条件の相手 id の列が要る (2026-10-09)
    v = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=ids)
    assert v["mode"] == V.MODE_FINAL and v["k"] == 2 and abs(v["z"] - 2.241403) < 1e-5, v
    d = {r["cond"]: r for r in v["comparisons"]}
    assert d["rl0"]["decision"] == V.ADOPT and d["rl5"]["decision"] == V.NOT_SUPPORTED, d
    assert v["per_condition"]["rl25"]["n"] == 600 and v["per_condition"]["rl25"]["wilson_low"] is not None
    # 未完了 (incomplete: true) → n が揃っていても採否を出さない
    vi = V.build_verdict({"incomplete": True}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=ids)
    assert vi["mode"] == V.MODE_INTERIM and all(r["decision"] is None for r in vi["comparisons"]), vi
    assert all(r["mean_diff"] is not None and r["ci_low"] is not None for r in vi["comparisons"])   # 推定値と区間は出す
    # n が最終対戦数に満たない (300 戦の中間確認) → 採否を出さない (--interim の指定なしで自動)
    half = {c: o[:300] for c, o in outs.items()}
    vh = V.build_verdict({"incomplete": False}, half, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=ids)
    assert vh["mode"] == V.MODE_INTERIM and vh["n_used"] == 300 and all(r["decision"] is None for r in vh["comparisons"])
    assert any("300" in x for x in vh["interim_reasons"])
    text = V.render(vh, {"incomplete": False})
    assert "中間確認" in text and "採用候補" not in text, text
    print("test_build_verdict_final_and_interim OK")


def test_length_mismatch_uses_common_prefix():
    outs = _outcomes_600()
    outs["rl5"] = outs["rl5"][:580]
    ids = {c: _ids(600) for c in outs}
    v = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=ids)
    assert v["n_used"] == 580 and all(r["n"] == 580 for r in v["comparisons"]), v
    assert any("共通の先頭 580" in x for x in v["notes"]), v["notes"]
    assert v["mode"] == V.MODE_INTERIM          # 共通の n が 600 未満なので採否を出さない
    # 条件が欠けている → 採否を出さない
    v2 = V.build_verdict({"incomplete": False}, {"rl0": outs["rl0"], "rl25": outs["rl25"]}, (0.0, 5.0, 25.0), 25.0, 600)
    assert v2["mode"] == V.MODE_INTERIM and any("rl5" in x for x in v2["notes"])
    print("test_length_mismatch_uses_common_prefix OK")


def test_correspondence_failure_blocks_final_verdict():
    """2026-10-09 レビュー指摘 1: 対応が確認できなければ (相手 id の不一致・欠落・不足) 最終判定を出さない (mode invalid)。
    統計的な判定不能 (interim) とは別の「比較条件の不成立」"""
    outs = _outcomes_600()
    good = {c: _ids(600) for c in outs}
    v = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=good)
    assert v["mode"] == V.MODE_FINAL and v["invalid_reasons"] == [] and v["opponent_mismatches"] == 0, v["invalid_reasons"]
    assert [r["decision"] for r in v["comparisons"]] == [V.ADOPT, V.NOT_SUPPORTED]
    # 全条件で相手 id を食い違わせた 600 戦 → 不成立 (以前は注記だけで adopt_candidate が返った)
    bad = {"rl0": _ids(600), "rl5": [f"x{i}" for i in range(600)], "rl25": [f"y{i}" for i in range(600)]}
    vb = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=bad)
    assert vb["mode"] == V.MODE_INVALID and all(r["decision"] is None for r in vb["comparisons"]), vb
    assert any("食い違う対戦が 600 件" in x for x in vb["invalid_reasons"]), vb["invalid_reasons"]
    # 1 件でも食い違えば不成立
    one = {c: _ids(600) for c in outs}
    one["rl5"] = list(one["rl5"])
    one["rl5"][10] = "zz"
    v1 = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=one)
    assert v1["mode"] == V.MODE_INVALID and any("1 件" in x for x in v1["invalid_reasons"]), v1["invalid_reasons"]
    # 相手 id の列が欠落 (条件 1 つ / 全部) → 不成立
    for ids in ({"rl0": _ids(600), "rl25": _ids(600)}, {}, None):
        vm = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=ids)
        assert vm["mode"] == V.MODE_INVALID and any("列が無い" in x for x in vm["invalid_reasons"]), vm["invalid_reasons"]
    # 列が対象の 600 戦に足りない → 不成立
    short = {c: _ids(600) for c in outs}
    short["rl25"] = _ids(590)
    vs = V.build_verdict({"incomplete": False}, outs, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=short)
    assert vs["mode"] == V.MODE_INVALID and any("足りない" in x for x in vs["invalid_reasons"]), vs["invalid_reasons"]
    # 中間確認 (300 戦) で対応が成り立たないときも不成立として出す (推定値は出す)
    half = {c: o[:300] for c, o in outs.items()}
    vh = V.build_verdict({"incomplete": False}, half, (0.0, 5.0, 25.0), 25.0, 600, 0.05, 0.05, opp_ids_by_cond=bad)
    assert vh["mode"] == V.MODE_INVALID and vh["interim_reasons"] and vh["comparisons"][0]["mean_diff"] is not None
    text = V.render(vb, {"incomplete": False})
    assert "比較条件の不成立" in text and "採用候補" not in text and "不成立" in text, text
    print("test_correspondence_failure_blocks_final_verdict OK")


def test_wilson_and_opponent_mismatch():
    lo, hi = V.wilson(50, 100, 1.96)
    assert abs(lo - 0.4038) < 1e-3 and abs(hi - 0.5962) < 1e-3, (lo, hi)
    assert V.wilson(0, 0) == (None, None)
    assert V.opponent_mismatches({"a": ["x", "y", "z"], "b": ["x", "q", "z"]}, 3) == 1
    assert V.opponent_mismatches({"a": ["x"]}, 1) is None
    print("test_wilson_and_opponent_mismatch OK")


def test_run_reads_json_and_jsonl_fallback():
    """rl<W>.json が無い条件は battles.jsonl の won を使い、verdict.json / verdict.md を書く"""
    outs = _outcomes_600()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "conditions.json").write_text(json.dumps({"experiment": "P1", "weights": [0.0, 5.0, 25.0], "baseline_weight": 25.0,
                                                         "final_battles": 600, "mde": 0.05, "alpha": 0.05, "k": 2,
                                                         "incomplete": False, "elapsed_sec": 3000.0}), encoding="utf-8")
        for c in ("rl0", "rl25"):
            (out / f"{c}.json").write_text(json.dumps({"outcomes": outs[c], "elapsed_s": 3000.0}), encoding="utf-8")
        # 対応の確認には全条件の相手 id の列 (battles.jsonl) が要る (2026-10-09 レビュー指摘 1)
        for c in ("rl0", "rl5", "rl25"):
            with (out / f"{c}.battles.jsonl").open("w", encoding="utf-8") as f:
                for i, w in enumerate(outs[c]):
                    f.write(json.dumps({"won": bool(w), "opponent_team_id": f"t{i % 33}"}) + "\n")
                if c == "rl5":
                    f.write('{"won": tr')          # 書きかけの行は数えない
        v = V.run(out)
        assert v["mode"] == V.MODE_FINAL and v["n_used"] == 600, v["interim_reasons"]
        assert "battles.jsonl" in v["per_condition"]["rl5"]["source"]
        assert (out / "verdict.json").exists() and (out / "verdict.md").exists()
        assert v["cost"]["rl5"]["sec_per_battle"] == 5.0
    print("test_run_reads_json_and_jsonl_fallback OK")


SC_BASE, SC_TR = "scoring:legacy", "scoring:transition"


def _scoring_conditions(incomplete=False, team=None) -> dict:
    """tools.usefulness_p1 --compare scoring の条件表の該当部分"""
    return {"experiment": "P1", "factor": "scoring", "weights": [25.0, 25.0], "baseline_weight": 25.0,
            "baseline": SC_BASE, "comparisons": [{"cond": SC_TR, "mode": "transition", "vs": SC_BASE}], "k": 1,
            "final_battles": 600, "mde": 0.05, "alpha": 0.05, "incomplete": incomplete, "elapsed_sec": 3000.0,
            "team": team or {"source": "ranked", "label": "一般的なチーム", "meta_snapshot_id": 58, "seed": 20261010,
                             "n_teams": 120, "pool_sha16": "pppp"}}


def test_scoring_verdict_final_interim_invalid():
    """採点方式の比較 (2026-10-09): 基準 scoring:legacy、比較 transition − legacy の 1 本 (k = 1、z = 1.96)。
    §0.5 の判定 (final / interim / invalid) は RL の比較と同じ規則で働く"""
    base, cond = _pair(166, 130, 150, 154)     # 平均差 0.06。k = 1 なら採用候補 (k = 2 なら判定不能、test_k_changes_decision)
    outs = {SC_BASE: base, SC_TR: cond}
    ids = {c: _ids(600) for c in outs}
    assert V.scoring_conds(_scoring_conditions()) == (SC_BASE, [SC_TR])
    assert V.scoring_conds({"factor": "scoring"}) == (SC_BASE, [SC_TR])        # 条件表に無ければ config の既定の方式
    kw = dict(baseline_cond=SC_BASE, compare_conds=[SC_TR])
    v = V.build_verdict(_scoring_conditions(), {SC_BASE: base, SC_TR: cond}, final_battles=600, mde=0.05, alpha=0.05,
                        opp_ids_by_cond=ids, **kw)
    # 注意: _pair は (条件, 基準) の順。ここでは base = 条件側の列を legacy に入れたので transition − legacy = −0.06
    assert v["mode"] == V.MODE_FINAL and v["k"] == 1 and abs(v["z"] - 1.959964) < 1e-5, v
    assert v["baseline"] == SC_BASE and [r["cond"] for r in v["comparisons"]] == [SC_TR]
    assert abs(v["comparisons"][0]["mean_diff"] + 0.06) < 1e-12 and v["comparisons"][0]["decision"] == V.NOT_SUPPORTED
    outs = {SC_BASE: cond, SC_TR: base}                                          # transition − legacy = +0.06
    v = V.build_verdict(_scoring_conditions(), outs, final_battles=600, mde=0.05, alpha=0.05, opp_ids_by_cond=ids, **kw)
    assert v["mode"] == V.MODE_FINAL and v["comparisons"][0]["decision"] == V.ADOPT, v["comparisons"]
    assert set(v["per_condition"]) == {SC_BASE, SC_TR} and "scoring_stats" not in v
    # 未完了 (incomplete: true) → 採否を出さない (推定値・区間は出す)
    vi = V.build_verdict(_scoring_conditions(incomplete=True), outs, final_battles=600, opp_ids_by_cond=ids, **kw)
    assert vi["mode"] == V.MODE_INTERIM and vi["comparisons"][0]["decision"] is None, vi
    assert vi["comparisons"][0]["mean_diff"] is not None and any("incomplete" in x for x in vi["interim_reasons"])
    # n < 600 → 中間確認
    half = {c: o[:300] for c, o in outs.items()}
    vh = V.build_verdict(_scoring_conditions(), half, final_battles=600, opp_ids_by_cond=ids, **kw)
    assert vh["mode"] == V.MODE_INTERIM and vh["n_used"] == 300 and vh["comparisons"][0]["decision"] is None
    # 条件が欠ける → 中間確認 (採否を出さない)
    vm = V.build_verdict(_scoring_conditions(), {SC_BASE: outs[SC_BASE]}, final_battles=600, opp_ids_by_cond=ids, **kw)
    assert vm["mode"] == V.MODE_INTERIM and any(SC_TR in x for x in vm["notes"])
    # 相手 id の食い違い (1 件) / 欠落 → 比較条件の不成立 (invalid)
    one = {c: _ids(600) for c in outs}
    one[SC_TR] = list(one[SC_TR])
    one[SC_TR][5] = "zz"
    vb = V.build_verdict(_scoring_conditions(), outs, final_battles=600, opp_ids_by_cond=one, **kw)
    assert vb["mode"] == V.MODE_INVALID and vb["comparisons"][0]["decision"] is None
    assert any("1 件" in x for x in vb["invalid_reasons"]), vb["invalid_reasons"]
    vn = V.build_verdict(_scoring_conditions(), outs, final_battles=600, opp_ids_by_cond={SC_BASE: _ids(600)}, **kw)
    assert vn["mode"] == V.MODE_INVALID and any(SC_TR in x and "列が無い" in x for x in vn["invalid_reasons"])
    # 未完了でも対応が成り立たなければ invalid (RL の比較と同じ優先)
    vbi = V.build_verdict(_scoring_conditions(incomplete=True), outs, final_battles=600, opp_ids_by_cond=one, **kw)
    assert vbi["mode"] == V.MODE_INVALID and vbi["interim_reasons"]
    text = V.render(vb, _scoring_conditions())
    assert "比較条件の不成立" in text and "scoring:transition − scoring:legacy" in text and "一般的なチーム" in text, text
    print("test_scoring_verdict_final_interim_invalid OK")


def test_scoring_fallback_summary_and_team_rows():
    s = V.scoring_fallback_summary({"advice_scoring_mode": "transition",
                                    "stats": {"decide": 900, "scoring_transition": 1000, "scoring_fallback": 50,
                                              "scoring_fallback:unsupported:volatile:protect": 30, "scoring_fallback:no_opp": 20}})
    assert s["advice_scoring_mode"] == "transition" and s["scoring_transition"] == 1000 and s["scoring_fallback"] == 50
    assert abs(s["fallback_rate"] - 0.05) < 1e-12
    assert s["fallback_by_reason"] == {"unsupported:volatile:protect": 30, "no_opp": 20}, s
    # legacy (transition の決定が 0) は退避率なし、json が無ければ None
    s0 = V.scoring_fallback_summary({"advice_scoring_mode": "legacy", "stats": {"decide": 900}})
    assert s0["scoring_transition"] == 0 and s0["fallback_rate"] is None and s0["fallback_by_reason"] == {}
    assert V.scoring_fallback_summary(None) is None
    # 条件表の要約: 自チームの出所
    rows = dict(V.condition_summary(_scoring_conditions()))
    assert "一般的なチーム" in rows["自チーム"] and "58" in rows["自チーム"] and "比較 / 基準 / k" in rows, rows
    reg = dict(V.condition_summary(_scoring_conditions(team={"source": "registered", "sha16": "fae570ac86ab02fd",
                                                             "expected_sha16": "fae570ac86ab02fd"})))
    assert "最新の登録チーム" in reg["自チーム"] and "fae570ac86ab02fd" in reg["自チーム"]
    # RL の比較の条件表 (source なし) は従来の表示
    old = dict(V.condition_summary({"weights": [0.0, 5.0, 25.0], "team": {"sha16": "a", "expected_sha16": "b"}}))
    assert "登録チーム sha16" in old and "自チーム" not in old and "重み / 基準 / k" in old
    print("test_scoring_fallback_summary_and_team_rows OK")


def test_run_scoring_reads_files():
    """run: factor scoring の条件表なら scoring_legacy.json / scoring_transition.json (+ battles.jsonl) を読み、
    退避率と自チームの出所を verdict.md に載せる。結果の無い RL の条件 (rl<W>) は読まない"""
    base, cond = _pair(166, 130, 150, 154)
    outs = {SC_BASE: cond, SC_TR: base}
    stats = {SC_BASE: {"decide": 1000}, SC_TR: {"decide": 1000, "scoring_transition": 1000, "scoring_fallback": 40,
                                                 "scoring_fallback:no_opp": 40}}
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "conditions.json").write_text(json.dumps(_scoring_conditions()), encoding="utf-8")
        for c in (SC_BASE, SC_TR):
            f = V.cond_file(c)
            (out / f"{f}.json").write_text(json.dumps({"outcomes": outs[c], "elapsed_s": 3000.0, "stats": stats[c],
                                                       "advice_scoring_mode": c.split(":")[1]}), encoding="utf-8")
            with (out / f"{f}.battles.jsonl").open("w", encoding="utf-8") as fh:
                for i, w in enumerate(outs[c]):
                    fh.write(json.dumps({"won": bool(w), "opponent_team_id": f"t{i % 33}"}) + "\n")
        v = V.run(out)
        assert v["mode"] == V.MODE_FINAL and v["k"] == 1 and v["comparisons"][0]["decision"] == V.ADOPT, v
        assert v["per_condition"][SC_TR]["source"] == "scoring_transition.json outcomes"
        assert abs(v["scoring_stats"][SC_TR]["fallback_rate"] - 0.04) < 1e-12
        assert v["scoring_stats"][SC_BASE]["advice_scoring_mode"] == "legacy"
        md = (out / "verdict.md").read_text(encoding="utf-8")
        assert "退避率" in md and "0.040" in md and "一般的なチーム" in md and "no_opp 40" in md, md
        # 相手 id が食い違えば invalid (ファイル経由でも同じ)
        with (out / "scoring_transition.battles.jsonl").open("w", encoding="utf-8") as fh:
            for i, w in enumerate(outs[SC_TR]):
                fh.write(json.dumps({"won": bool(w), "opponent_team_id": f"x{i}"}) + "\n")
        assert V.run(out)["mode"] == V.MODE_INVALID
    print("test_run_scoring_reads_files OK")


def main():
    test_corrected_z_by_k()
    test_decide_boundaries()
    test_compare_mean_exactly_mde()
    test_k_changes_decision()
    test_build_verdict_final_and_interim()
    test_length_mismatch_uses_common_prefix()
    test_wilson_and_opponent_mismatch()
    test_correspondence_failure_blocks_final_verdict()
    test_run_reads_json_and_jsonl_fallback()
    test_scoring_verdict_final_interim_invalid()
    test_scoring_fallback_summary_and_team_rows()
    test_run_scoring_reads_files()


if __name__ == "__main__":
    main()
