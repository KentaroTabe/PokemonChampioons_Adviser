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


def test_extract_json_braces_in_strings_and_record_numbering():
    """2026-09-22: 文字列の中の括弧で JSON が途中で切れ、内側の {"ok": true} だけを拾って本文を捨てていた (arch_0918 の記事)。
    フェンスつき・前置きつき・文字列内の { } を正しく扱い、記録の連番は既存ファイルを上書きしない"""
    fenced = ('```json\n{\n  "authoritative": { "ok": true },\n  "display": {\n    "markdown": "# 記事\\n\\n本文に {x} と } を含む。'
              '`L69_C029` の話。"\n  }\n}\n```')
    d = extract_json(fenced)
    assert d and d["authoritative"] == {"ok": True} and "本文に {x} と } を含む" in d["display"]["markdown"], d
    assert extract_json('前置き {壊れた} のあと {"a": 1, "b": {"c": "}"}} 後置き') == {"a": 1, "b": {"c": "}"}}
    assert extract_json('{"a": 1}') == {"a": 1} and extract_json("[1, 2]") is None and extract_json("") is None
    # 文字列の中の生の改行 (厳密な JSON では不正) も本文として読む (長い記事で実際に起きた)
    raw_nl = '{"authoritative": {"ok": true}, "display": {"markdown": "# 記事\n\n1 行目\n2 行目 }"}}'
    d2 = extract_json(raw_nl)
    assert d2 and d2["display"]["markdown"] == "# 記事\n\n1 行目\n2 行目 }", d2
    with tempfile.TemporaryDirectory() as tmp:
        p1 = MockProvider(['{"authoritative": {"x": 1}}'], log_dir=Path(tmp))
        p1.call("s13_report", "sonnet", "sys", {"q": 1})
        p2 = MockProvider(['{"authoritative": {"x": 2}}'], log_dir=Path(tmp))    # 別プロセスの再生成を模す
        r2 = p2.call("s13_report", "sonnet", "sys", {"q": 2})
        files = sorted(Path(tmp).glob("s13_report_sonnet_*.json"))
        assert len(files) == 2 and files[0].name == "s13_report_sonnet_001_a1.json" and files[1].name == "s13_report_sonnet_002_a1.json"
        rec0 = json.loads(files[0].read_text(encoding="utf-8"))
        assert '"x": 1' in rec0["raw_text"] and r2["record"] == str(files[1])       # 1 つ目の記録が残っている
    print("test_extract_json_braces_in_strings_and_record_numbering OK")


def test_generate_with_archetypes():
    """構築の軸 (2026-09-18): framing は軸ごと (archetype:<id>)、concept の archetype / branch を検証し、系統に残す。
    特殊な勝ち筋の軸も 1 回にまとまる"""
    feats, th = _feats()
    spec = BuildSpec(owned=["a", "b", "c", "d"])
    legal, mega = {"a", "b", "c", "d", "t1", "t2", "t3"}, {"a"}
    axes = [{"id": "setup_sweep", "label": "積み展開", "description": "積んで抜く", "switching": "no_switch", "switching_ja": "交代しない",
             "special": False, "branches": [{"id": "screens_dual", "label": "2 枚壁", "fit": 0.7, "notes": ["崩し手 30%"],
                                             "roles": [{"role": "screens_dual", "label": "壁役", "min": 1, "candidates": ["c"]},
                                                       {"role": "setup_ace", "label": "積みエース", "min": 2, "candidates": ["a", "b"]}]}]},
            {"id": "special", "label": "特殊な勝ち筋", "description": "ほろびのうた等", "switching": "mixed", "switching_ja": "状況次第",
             "special": True, "branches": [{"id": "perish_trap", "label": "ほろびのうた + 交代封じ", "fit": 0.6, "notes": [],
                                            "roles": [{"role": "perish_singer", "label": "ほろびのうた役", "min": 1, "candidates": ["d"]}]}]}]
    good = json.dumps({"authoritative": {"concepts": [
        {"name": "walls", "core_ids": ["c", "d"], "mega_id": None, "win_condition": "setup_sweep", "support_roles": ["setup"],
         "weak_to": ["t3"], "archetype": "setup_sweep", "branch": "screens_dual"}]}, "display": {"explanations": {"walls": "壁"}}})
    bad = json.dumps({"authoritative": {"concepts": [
        {"name": "x", "core_ids": ["c", "d"], "mega_id": None, "win_condition": "setup_sweep", "support_roles": [], "weak_to": [],
         "archetype": "nope", "branch": "screens_dual"}]}})
    dup = json.dumps({"authoritative": {"concepts": [
        {"name": "perish", "core_ids": ["d", "c"], "mega_id": None, "win_condition": "anti_meta", "support_roles": [], "weak_to": [],
         "archetype": "special", "branch": "perish_trap"}]}})
    with tempfile.TemporaryDirectory() as d:
        prov = MockProvider([bad, good, dup], log_dir=Path(d))
        res = K.generate_concepts(spec, feats, th, legal, mega, provider=prov, rounds=5, per_round=1, archetypes=axes)
    calls = res["llm_calls"]
    assert calls[0]["framing"] == "archetype:setup_sweep" and calls[0]["attempts"] == 2 and calls[0]["ok"]   # 未知の軸は差し戻し
    assert calls[1]["framing"] == "archetype:special" and res["stop_reason"] == "coverage" and len(calls) == 2
    fam = next(f for f in res["families"] if f.get("archetype") == "setup_sweep")
    assert fam["branch"] == "screens_dual" and fam["source"] == "llm:archetype:setup_sweep" and fam["core_ids"] == ["c", "d"]
    assert K.validate_concepts({"concepts": [{"name": "y", "core_ids": ["a", "b"], "win_condition": "setup_sweep",
                                             "archetype": "setup_sweep", "branch": "zzz"}]},
                               {"a", "b"}, legal, mega, archetypes={"setup_sweep": ["screens_dual"]})
    assert not K.validate_concepts({"concepts": [{"name": "y", "core_ids": ["a", "b"], "win_condition": "setup_sweep"}]},
                                   {"a", "b"}, legal, mega, archetypes={"setup_sweep": ["screens_dual"]})   # 軸なしは可
    print("test_generate_with_archetypes OK")


if __name__ == "__main__":
    test_validate_and_cluster()
    test_generate_with_mock_provider()
    test_extract_json_braces_in_strings_and_record_numbering()
    test_generate_with_archetypes()
