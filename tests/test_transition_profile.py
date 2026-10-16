"""tools.transition_profile (transition の処理時間の内訳の計測、2026-10-16) のテスト。

    python -m tests.test_transition_profile

- 純粋な部分: median_run (中央値・揺れた鍵) / parts_of / spearman / summarize_profile (全体の割合・遅い局面・p95 以上・感度が主因かの
  判定・感度 on / off の順位の食い違い) / render_markdown
- 計測の包み (_Probe / measure_one): 合成局面 (tests.test_advice_variant) で、内訳の各部分が埋まり、感度 off で sens の状態が
  disabled になり、計測の後に advisor.scoring_transition の build_inputs / score が元に戻る。感度 on / off で順位が同じ
"""
from __future__ import annotations

import copy

from advisor import scoring_transition as ST
from champions_agent import config as C
from tools import transition_profile as TP
from tests.test_advice_variant import _FakePolicy, _fixtures


def _m(eval_ms, pre, build, main, sens, post, **kw):
    out = {"eval_ms": eval_ms, "pre_ms": pre, "build_ms": build, "main_ms": main, "sens_ms": sens, "post_ms": post,
           "total_ms": None if main is None else round(main + (sens or 0.0), 1), "score_ms": None, "ok": True,
           "fallback": None, "n_branches": 100, "n_responses": 6, "n_candidates": 5, "n_alts": 2, "sens_status": "run",
           "sens_run": ["scarf"], "sens_omitted": {}, "error": None, "sig": {"best": ("move", "a")}, "unstable": []}
    out.update(kw)
    return out


def test_median_run_pure():
    runs = [_m(10.0, 1, 1, 5, 3, 0), _m(30.0, 1, 1, 15, 13, 0), _m(20.0, 1, 1, 10, 8, 0, sens_status="partial")]
    m = TP.median_run(runs)
    assert m["eval_ms"] == 20.0 and m["main_ms"] == 10.0 and m["sens_ms"] == 8.0 and m["n_runs"] == 3
    assert m["sens_status"] == "run" and m["unstable"] == ["sens_status"], m["unstable"]
    # None は除いて中央値、全部 None なら None
    m2 = TP.median_run([_m(10.0, 1, 1, None, None, 0), _m(12.0, 1, 1, None, None, 0)])
    assert m2["main_ms"] is None and m2["eval_ms"] == 11.0 and m2["unstable"] == []
    assert TP.median_run([]) == {}
    assert TP.parts_of(_m(10.0, 1.0, 0.5, None, None, 2.0)) == {"pre": 1.0, "build": 0.5, "main": 0.0, "sens": 0.0, "post": 2.0}
    print("test_median_run_pure OK")


def test_spearman_pure():
    assert TP.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert TP.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert TP.spearman([1, 2], [1, 2]) is None                 # 組が 3 未満
    assert TP.spearman([1, 1, 1], [1, 2, 3]) is None           # 定数
    assert TP.spearman([1, None, 2, 3], [1, 5, 2, 3]) == 1.0   # None の組は除く
    r = TP.spearman([1, 2, 2, 3], [1, 2, 3, 4])                # 同順位は平均順位
    assert r is not None and 0.9 < r < 1.0, r
    print("test_spearman_pure OK")


def _rows(sens_heavy: bool):
    """20 決定。遅い 2 件 (p95 以上) は sens_heavy なら感度が 7 割、そうでなければ 2 割"""
    rows = []
    for i in range(20):
        if i < 2:
            main, sens = (60.0, 140.0) if sens_heavy else (160.0, 40.0)
        else:
            main, sens = 10.0 + i, 5.0
        on = _m(main + sens + 5.0, 4.0, 0.5, main, sens, 0.5, n_branches=1000 - i * 10)
        off = _m(main + 5.0, 4.0, 0.5, main, 0.0, 0.5, sens_status="disabled", sens_run=[], n_branches=1000 - i * 10)
        rows.append({"log": "a.jsonl", "advice_id": f"a-{i}", "turn": i, "on": on, "off": off})
    return rows


def test_summarize_pure():
    s = TP.summarize_profile(_rows(True), top=3, dominant_share=0.5)
    assert s["n"] == 20 and s["n_measured"] == 20
    ov = s["overall"]
    assert abs(sum(ov["parts_ms"].values()) - ov["eval_ms"]) < 1e-6
    assert ov["parts_ms"]["sens"] == 140.0 * 2 + 5.0 * 18
    assert [d["advice_id"] for d in s["slow_top"]] == ["a-0", "a-1", "a-19"]
    assert s["p95_set"]["decisions"] == ["a-0", "a-1"], s["p95_set"]
    assert s["sens_dominant"] is True and s["sens_share_p95"] == round(280.0 / 410.0, 3)
    assert s["rank_mismatch"]["n"] == 0
    assert s["relation"]["n_branches"]["main_ms"] is not None
    assert s["dist"]["eval_ms"]["on"]["n"] == 20 and s["delta_eval_ms"]["p50"] == 5.0
    s2 = TP.summarize_profile(_rows(False), top=3, dominant_share=0.5)
    assert s2["sens_dominant"] is False and s2["sens_share_p95"] == round(80.0 / 410.0, 3)
    # 感度 on / off の順位の食い違いを数える
    rows = _rows(False)
    rows[5]["off"]["sig"] = {"best": ("move", "b")}
    assert TP.summarize_profile(rows)["rank_mismatch"] == {"n": 1, "decisions": ["a-5"]}
    # 計測できない決定 (例外) は数えない、空なら判定不能
    rows = _rows(False)
    rows[3]["on"]["error"] = "ValueError: x"
    assert TP.summarize_profile(rows)["n_measured"] == 19
    e = TP.summarize_profile([])
    assert e["sens_dominant"] is None and e["slow_top"] == []
    print("test_summarize_pure OK")


def test_render_pure():
    s = TP.summarize_profile(_rows(True), top=3)
    cond = {"at": "2026-10-16 12:00", "code": {"commit": "abc", "dirty": False, "engine": "/x/engine.py"}, "repeat": 3,
            "transition_budget_ms": 300.0, "transition_sensitivity": True, "display_search": "off", "gap_sec": 8.0,
            "logs": [{"name": "a.jsonl", "n_decisions": 20}]}
    md = TP.render_markdown({"conditions": cond, "summary": s})
    assert "**感度が主因**" in md and "| 本採点 |" in md and "a-0" in md and "p95 以上" in md
    md2 = TP.render_markdown({"conditions": cond, "summary": TP.summarize_profile(_rows(False), top=3)})
    assert "**感度は主因でない**" in md2
    print("test_render_pure OK")


def test_measure_one_synthetic():
    orig_b, orig_s = ST.build_inputs, ST.score
    with _FakePolicy():
        st = dict(_fixtures())["duraludon"]
        on = TP.measure_one(copy.deepcopy(st), None, True)
        off = TP.measure_one(copy.deepcopy(st), None, False)
    assert ST.build_inputs is orig_b and ST.score is orig_s           # 包みは元に戻る
    for m in (on, off):
        assert m["error"] is None and m["ok"], m
        assert m["build_ms"] is not None and m["pre_ms"] is not None and m["post_ms"] is not None
        assert m["n_candidates"] and m["n_candidates"] > 0
    if on["fallback"] is None:
        assert on["main_ms"] is not None and on["total_ms"] is not None and on["n_branches"]
        assert off["sens_status"] == "disabled", off["sens_status"]
        assert on["sens_status"] in ("run", "partial", "omitted", "none"), on["sens_status"]
    assert on["sig"] == off["sig"]                                     # 感度は順位に使わない
    assert C.TRANSITION_PROFILE_REPEAT >= 1 and C.TRANSITION_PROFILE_TOP >= 1
    print("test_measure_one_synthetic OK")


def main() -> None:
    test_median_run_pure()
    test_spearman_pure()
    test_summarize_pure()
    test_render_pure()
    test_measure_one_synthetic()
    print("\nALL OK")


if __name__ == "__main__":
    main()
