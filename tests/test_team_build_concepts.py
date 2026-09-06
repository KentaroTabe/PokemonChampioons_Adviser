"""コンセプト生成 (concepts) と LLMProvider のテスト (モック)。

    python -m tests.test_team_build_concepts
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.team_build import concepts as K
from tools.team_build.candidates import SpeciesFeature
from tools.team_build.llm.provider import MockProvider, extract_json
from tools.team_build.spec import BuildSpec


def _feats():
    th = ["t1", "t2", "t3"]
    def f(s, cov, roles, mega=False):
        return SpeciesFeature(s, dict(zip(th, cov)), roles, ("x",), mega)
    return {"a": f("a", [0.9, 0.2, 0.2], {"setup": 1.0}, True),
            "b": f("b", [0.1, 0.9, 0.1], {"priority": 1.0}),
            "c": f("c", [0.1, 0.1, 0.9], {}),
            "d": f("d", [0.3, 0.3, 0.3], {})}, th


def test_validate_and_cluster():
    owned, legal, mega = {"a", "b", "c", "d"}, {"a", "b", "c", "d", "t1", "t2", "t3"}, {"a"}
    ok = {"concepts": [{"name": "x", "core_ids": ["a", "b"], "mega_id": "a", "win_condition": "setup_sweep",
                        "support_roles": ["priority"], "weak_to": ["t3"]}]}
    assert K.validate_concepts(ok, owned, legal, mega) == []
    bad = {"concepts": [{"name": "y", "core_ids": ["a", "zzz"], "mega_id": "b", "win_condition": "nope",
                         "support_roles": ["fly"], "weak_to": ["qq"]}]}
    probs = K.validate_concepts(bad, owned, legal, mega)
    assert len(probs) >= 4, probs
    fams = K.cluster_concepts([{"name": "1", "core_ids": ["a", "b"]}, {"name": "2", "core_ids": ["b", "a"]},
                               {"name": "3", "core_ids": ["c", "d"]}])
    assert len(fams) == 2 and fams[0]["members"] == 2 and fams[0]["family_id"] == "C001"
    print("test_validate_and_cluster OK")


def test_generate_with_mock_provider():
    feats, th = _feats()
    spec = BuildSpec(owned=["a", "b", "c", "d"], favorites=["a"])
    legal, mega = {"a", "b", "c", "d", "t1", "t2", "t3"}, {"a"}
    base = K.rule_baseline_concepts(feats, th, mega, favorites=spec.favorites)
    assert base and base[0]["core_ids"][0] == "a" and base[0]["mega_id"] == "a"
    good = json.dumps({"authoritative": {"concepts": [
        {"name": "cd", "core_ids": ["c", "d"], "mega_id": None, "win_condition": "cycle_pressure",
         "support_roles": ["pivot"], "weak_to": ["t1"]}]}, "display": {"explanations": {"cd": "説明"}}})
    dup = json.dumps({"authoritative": {"concepts": [
        {"name": "cd2", "core_ids": ["d", "c"], "mega_id": None, "win_condition": "cycle_pressure",
         "support_roles": [], "weak_to": []}]}})
    with tempfile.TemporaryDirectory() as d:
        prov = MockProvider(["これは前置き\n" + good, "```json\n" + dup + "\n```"], log_dir=Path(d))
        res = K.generate_concepts(spec, feats, th, legal, mega, provider=prov, rounds=4, per_round=1)
        assert res["stop_reason"] == "coverage", res["stop_reason"]     # 2 回目が重複のみで停止
        assert any(f["core_ids"] == ["c", "d"] for f in res["families"])
        assert res["rounds"] == 2 and all(c["ok"] for c in res["llm_calls"])
        assert len(list(Path(d).glob("s04_concepts_opus_*.json"))) == 2   # 全入出力を保存
        rec = json.loads(next(Path(d).glob("s04_concepts_opus_*.json")).read_text())
        assert rec["prompt_hash"] and rec["model"] == "claude-opus-5" and rec["provider"] == "mock"
    # 不正出力 → 差し戻し → 再試行で通る
    bad = json.dumps({"authoritative": {"concepts": [{"name": "z", "core_ids": ["zzz", "a"], "win_condition": "nope"}]}})
    prov = MockProvider([bad, good])
    res = prov.call("s04_concepts", "opus", "sys", {"x": 1},
                    validator=lambda a: K.validate_concepts(a, {"a", "b", "c", "d"}, legal, mega))
    assert res["ok"] and res["attempts"] == 2 and "cd" in json.dumps(res["authoritative"])
    assert extract_json("no json here") is None
    print("test_generate_with_mock_provider OK")


if __name__ == "__main__":
    test_validate_and_cluster()
    test_generate_with_mock_provider()
