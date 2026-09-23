"""固定入力での LLM 段の回帰測定 (tools/team_build/llm/regression) のテスト: 指標の計算 (純粋関数)、run の成果物からの入力の
組み立て (species_features の往復)、MockProvider での腕の実行と metrics.json、記録の再生 (replay) の順序、比較表。

    python -m tests.test_team_build_regression
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.team_build.candidates import SpeciesFeature
from tools.team_build.features import features_from_json, features_to_json
from tools.team_build.llm import regression as R
from tools.team_build.llm.provider import MockProvider


def _record(k: int, attempt: int, concepts: list, problems=None, cost=None, elapsed=10.0, model="claude-opus-5") -> dict:
    return {"stage": "s04_concepts", "tier": "opus", "model": model, "attempt": attempt, "problems": problems or [],
            "elapsed_s": elapsed, "cost_usd": cost, "effort": None,
            "usage": {"cache_creation_input_tokens": 1000 * k, "output_tokens": 100 * k},
            "raw_text": json.dumps({"authoritative": {"concepts": concepts}})}


def test_metrics_pure():
    fams = [{"core_ids": ["a", "b"], "source": "rule", "archetype": None},
            {"core_ids": ["c", "d"], "source": "llm:archetype:setup_sweep", "archetype": "setup_sweep"},
            {"core_ids": ["c", "e"], "source": "llm:archetype:trick_room", "archetype": "trick_room"},
            {"core_ids": ["f", "g", "h"], "source": "llm:archetype:trick_room", "archetype": "trick_room"}]
    res = {"families": fams, "rounds": 2, "stop_reason": "coverage"}
    recs = [_record(1, 1, [{"core_ids": ["x"]}], problems=["bad"], cost=0.5),
            _record(2, 2, [{"core_ids": ["c", "d"]}, {"core_ids": ["c", "e"]}], cost=0.6),
            _record(3, 1, [{"core_ids": ["f", "g", "h"]}], cost=0.7, elapsed=20.0)]
    m = R.metrics(res, recs, owned={"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"}, label="t")
    assert m["families_total"] == 4 and m["families_llm"] == 3 and m["concepts_returned"] == 3
    assert m["species_used"] == 6 and m["species_used_share"] == 0.6 and m["archetypes_covered"] == 2
    # core 距離: {c,d}-{c,e} = 1-1/3, {c,d}-{f,g,h} = 1, {c,e}-{f,g,h} = 1 → 平均 0.889
    assert abs(m["core_mean_jaccard_distance"] - 0.889) < 1e-3
    assert m["calls"] == 3 and m["retries"] == 1 and m["first_attempt_ok_rate"] == 0.5     # 2 ラウンド中、1 回目で通ったのは 1 つ
    assert m["cost_usd"] == 1.8 and m["elapsed_total_s"] == 40.0 and m["elapsed_mean_s"] == 13.3
    assert m["tokens"]["cache_creation_input_tokens"] == 6000 and m["model"] == "claude-opus-5"
    assert R.metrics({"families": [], "rounds": 0}, [], label="e")["cost_usd"] is None
    print("test_metrics_pure OK")


def _feats() -> dict:
    return {"a": SpeciesFeature("a", {"t1": 0.9, "t2": 0.1}, {"setup": 1.0}, ("Fire",), True, speed=100, usage=12.5,
                                teammates={"b": 30.0}, coverage_base={"t1": 0.5, "t2": 0.1}),
            "b": SpeciesFeature("b", {"t1": 0.2, "t2": 0.8}, {"priority": 1.0}, ("Water",)),
            "c": SpeciesFeature("c", {"t1": 0.1, "t2": 0.1}), "d": SpeciesFeature("d", {"t1": 0.3, "t2": 0.3})}


def test_features_roundtrip_and_load_inputs():
    feats = _feats()
    back = features_from_json(features_to_json({"features": feats, "missing": []}))
    assert set(back) == set(feats) and back["a"].mega is True and back["a"].coverage_base == {"t1": 0.5, "t2": 0.1}
    assert back["a"].types == ("Fire",) and back["a"].teammates == {"b": 30.0} and back["b"].coverage_base is None
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp)
        from tools.team_build.spec import BuildSpec, save_spec
        save_spec(BuildSpec(owned=["a", "b", "c", "d"], favorites=["a"]), run)
        (run / "species_features.json").write_text(json.dumps(features_to_json({"features": feats, "missing": []})),
                                                   encoding="utf-8")
        (run / "s03_archetypes.json").write_text(json.dumps({"fits": {}, "qualified": {}, "cores": [], "skipped": [],
                                                             "threat_info": {"t1": {"weight": 0.7}, "t2": {"weight": 0.2}}}),
                                                 encoding="utf-8")
        inp = R.load_inputs(run)
        assert inp["threats"] == ["t1", "t2"] and inp["threat_weights"] == {"t1": 0.7, "t2": 0.2}
        assert inp["spec"].owned == ["a", "b", "c", "d"] and set(inp["feats"]) == set(feats) and inp["rules"] is None
        assert inp["archetypes"] and all("branches" in a for a in inp["archetypes"])   # 軸の一覧 (適合は空でも組み立つ)
    print("test_features_roundtrip_and_load_inputs OK")


def test_run_arm_with_mock_and_replay():
    feats = _feats()
    good = json.dumps({"authoritative": {"concepts": [
        {"name": "cd", "core_ids": ["c", "d"], "mega_id": None, "win_condition": "cycle_pressure",
         "support_roles": ["pivot"], "weak_to": ["t1"]}]}, "display": {"explanations": {"cd": "説明"}}})
    dup = json.dumps({"authoritative": {"concepts": [
        {"name": "cd2", "core_ids": ["d", "c"], "mega_id": None, "win_condition": "cycle_pressure",
         "support_roles": [], "weak_to": []}]}})
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "runs" / "r1"
        run.mkdir(parents=True)
        from tools.team_build.spec import BuildSpec, save_spec
        save_spec(BuildSpec(owned=["a", "b", "c", "d"], favorites=["a"]), run)
        (run / "species_features.json").write_text(json.dumps(features_to_json({"features": feats, "missing": []})),
                                                   encoding="utf-8")
        (run / "meta_snapshot.json").write_text(json.dumps({"threats": [{"id": "t1"}, {"id": "t2"}]}), encoding="utf-8")
        # legal / mega はデータに依存するので差し替える
        orig_legal, orig_mega = R.legal_species_ids, R.mega_capable_ids
        R.legal_species_ids = lambda: {"a", "b", "c", "d", "t1", "t2"}
        R.mega_capable_ids = lambda ids: {"a"}
        try:
            out = Path(tmp) / "reg" / "mock"
            prov = MockProvider(["前置き\n" + good, "```json\n" + dup + "\n```"], log_dir=out / "llm")
            m = R.run_s04(run, out, prov, label="mock", log=lambda s: None)
            assert (out / "s04_concepts.json").exists() and json.loads((out / "metrics.json").read_text())["label"] == "mock"
            assert m["families_llm"] == 1 and m["calls"] == 2 and m["stop_reason"] == "coverage" and m["rounds"] == 2
            assert m["first_attempt_ok_rate"] == 1.0 and m["retries"] == 0 and m["cost_usd"] is None
            # 記録の再生: run/llm に記録を置く (k 順 = 呼び出し順。再試行は別番号)
            (run / "llm").mkdir()
            for name, text in [("s04_concepts_opus_001_a1.json", "壊れた"), ("s04_concepts_opus_002_a2.json", good),
                               ("s04_concepts_opus_003_a1.json", dup)]:
                (run / "llm" / name).write_text(json.dumps({"stage": "s04_concepts", "raw_text": text}), encoding="utf-8")
            rp = R.replay_provider(run, log_dir=Path(tmp) / "reg" / "replay" / "llm")
            assert rp.responses == ["壊れた", good, dup]
            m2 = R.run_s04(run, Path(tmp) / "reg" / "replay", rp, label="replay", log=lambda s: None)
            assert m2["families_llm"] == 1 and m2["retries"] == 1 and m2["first_attempt_ok_rate"] == 0.5
            # 比較表
            R.REG_DIR = Path(tmp) / "reg_root"
            (R.REG_DIR / "r1" / "mock").mkdir(parents=True)
            (R.REG_DIR / "r1" / "mock" / "metrics.json").write_text(json.dumps(m), encoding="utf-8")
            table = R.compare("r1")
            assert "| mock |" in table and (R.REG_DIR / "r1" / "compare.md").exists()
        finally:
            R.legal_species_ids, R.mega_capable_ids = orig_legal, orig_mega
    print("test_run_arm_with_mock_and_replay OK")


if __name__ == "__main__":
    test_metrics_pure()
    test_features_roundtrip_and_load_inputs()
    test_run_arm_with_mock_and_replay()
