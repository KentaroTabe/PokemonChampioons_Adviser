"""コンセプト生成 (concepts) と LLMProvider のテスト (モック)。

    python -m tests.test_team_build_concepts
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import subprocess

from tools.team_build import concepts as K
from tools.team_build.candidates import SpeciesFeature
from tools.team_build.llm import provider as P
from tools.team_build.llm.provider import OUTPUT_SCHEMA, ClaudeCLIProvider, MockProvider, extract_json
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
    # 使わないポケモン (config/banned_species.txt) が core にあれば、所持にあっても差し戻す
    banned_probs = K.validate_concepts(ok, owned, legal, mega, banned={"b"})
    assert len(banned_probs) == 1 and "使わない" in banned_probs[0] and "b" in banned_probs[0], banned_probs
    assert K.validate_concepts(ok, owned, legal, mega, banned={"zzz"}) == []
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
        assert rec["prompt_hash"] and rec["model"] == prov.model_for("s04_concepts", "opus") and rec["provider"] == "mock"
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


def test_structured_output_is_preferred():
    """2026-09-24: schema を渡すと構造化出力 (schema 準拠が保証された dict) を使い、本文の JSON 抽出は後備えになる"""
    body = {"authoritative": {"ok": True}, "display": {"markdown": "本文 } に括弧"}}
    with tempfile.TemporaryDirectory() as tmp:
        prov = MockProvider([json.dumps(body)], log_dir=Path(tmp))
        res = prov.call("s13_report", "sonnet", "sys", {"q": 1}, schema=OUTPUT_SCHEMA)
        assert res["ok"] and res["display"] == body["display"]
        rec = json.loads(next(Path(tmp).glob("s13_report_*.json")).read_text(encoding="utf-8"))
        assert rec["structured"] is True and rec["schema_hash"] == P.schema_hash(OUTPUT_SCHEMA) and rec["cost_usd"] is None
        # schema を渡しても応答がフェンスつき (構造化出力なし) なら本文から取り出す (structured=False)
        prov2 = MockProvider(["```json\n" + json.dumps(body) + "\n```"], log_dir=Path(tmp))
        res2 = prov2.call("s13_report", "sonnet", "sys", {"q": 2}, schema=OUTPUT_SCHEMA)
        assert res2["ok"] and res2["display"] == body["display"]
        recs = sorted(Path(tmp).glob("s13_report_*.json"))
        assert json.loads(recs[-1].read_text(encoding="utf-8"))["structured"] is False
    # schema 無し (MockProvider の既定) は従来どおり
    assert MockProvider.default_schema is None and ClaudeCLIProvider.default_schema == OUTPUT_SCHEMA
    assert json.loads(json.dumps(OUTPUT_SCHEMA))["required"] == ["authoritative"]
    print("test_structured_output_is_preferred OK")


def test_claude_cli_provider_uses_json_schema():
    """ClaudeCLIProvider は --json-schema を付け、応答の structured_output と total_cost_usd を使う。
    structured_output が無い応答 (古い CLI) では result の本文から取り出す"""
    seen: list = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if "--json-schema" in cmd:
            out = {"result": "説明つき {\"authoritative\": {\"from\": \"text\"}}",
                   "structured_output": {"authoritative": {"from": "structured"}, "display": {"t": 1}},
                   "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.0123}
        else:
            out = {"result": "前置き\n{\"authoritative\": {\"from\": \"text\"}}", "usage": {"input_tokens": 1}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out, ensure_ascii=False), stderr="")

    orig = P.subprocess.run
    P.subprocess.run = fake_run
    try:
        # 段ごとの設定 (config) が効く: S4 は BUILD_LLM_STAGE_MODELS / BUILD_LLM_EFFORT のとおり
        prov0 = ClaudeCLIProvider()
        prov0.call("s04_concepts", "opus", "sys", {"x": 0})
        cmd = seen[-1]
        # config の現在値に結び付けない: 段の指定があればそれ、無ければ tier の対応 (2026-09-25 に S4 を Opus 5 既定へ戻した)
        expected = P.BUILD_LLM_STAGE_MODELS.get("s04_concepts") or P.MODELS["opus"]
        assert cmd[cmd.index("--model") + 1] == prov0.model_for("s04_concepts", "opus") == expected
        if P.BUILD_LLM_EFFORT.get("s04_concepts"):
            assert cmd[cmd.index("--effort") + 1] == P.BUILD_LLM_EFFORT["s04_concepts"]
        else:
            assert "--effort" not in cmd
        # 段の設定を外して tier の対応で呼ぶ
        prov = ClaudeCLIProvider(stage_models={"s04_concepts": None}, effort={"s04_concepts": None})
        res = prov.call("s04_concepts", "opus", "sys", {"x": 1})
        cmd = seen[-1]
        assert cmd[:2] == ["claude", "-p"] and "--json-schema" in cmd and "--max-turns" in cmd
        assert json.loads(cmd[cmd.index("--json-schema") + 1]) == OUTPUT_SCHEMA
        assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
        assert cmd[cmd.index("--tools") + 1] == "" and "--disallowedTools" not in cmd          # ツール定義を載せない
        assert cmd[cmd.index("--max-budget-usd") + 1] == str(P.BUILD_LLM_MAX_BUDGET_USD) and "--effort" not in cmd
        assert res["ok"] and res["authoritative"] == {"from": "structured"} and res["display"] == {"t": 1}
        assert prov.calls[-1]["structured"] is True and prov.calls[-1]["cost_usd"] == 0.0123 and prov.calls[-1]["effort"] is None
        res2 = prov.call("s04_concepts", "opus", "sys", {"x": 2}, schema=None)   # None = 既定 schema (付く)
        assert "--json-schema" in seen[-1] and res2["authoritative"] == {"from": "structured"}
        # 構造化出力が無い応答 → 本文から
        prov.default_schema = None
        res3 = prov.call("s04_concepts", "opus", "sys", {"x": 3})
        assert "--json-schema" not in seen[-1] and res3["ok"] and res3["authoritative"] == {"from": "text"}
        assert prov.calls[-1]["structured"] is False and prov.calls[-1]["cost_usd"] is None
        # モデルの上書き (regression の腕: 段の指定 > tier の対応) と段ごとの effort (config / 呼び出し) と費用上限
        prov2 = ClaudeCLIProvider(models={"opus": "claude-opus-5-5"}, effort={"s04_concepts": "xhigh"}, max_budget_usd=2.5,
                                  stage_models={"s04_concepts": None})
        prov2.call("s04_concepts", "opus", "sys", {"x": 4})
        cmd = seen[-1]
        assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5" and cmd[cmd.index("--effort") + 1] == "xhigh"
        assert cmd[cmd.index("--max-budget-usd") + 1] == "2.5" and prov2.calls[-1]["effort"] == "xhigh"
        prov4 = ClaudeCLIProvider(models={"opus": "claude-opus-5-5"}, stage_models={"s04_concepts": "claude-fable-5-1"})
        prov4.call("s04_concepts", "opus", "sys", {"x": 8})
        assert seen[-1][seen[-1].index("--model") + 1] == "claude-fable-5-1"                  # 段の指定が優先
        prov2.call("s13_report", "sonnet", "sys", {"x": 5}, effort="low")           # 呼び出しの指定が優先、他の段は既定
        assert seen[-1][seen[-1].index("--effort") + 1] == "low" and seen[-1][seen[-1].index("--model") + 1] == "claude-sonnet-5"
        prov2.call("s13_report", "sonnet", "sys", {"x": 6})
        assert "--effort" not in seen[-1]
        prov3 = ClaudeCLIProvider(max_budget_usd=None)
        prov3.call("s04_concepts", "opus", "sys", {"x": 7})
        assert "--max-budget-usd" not in seen[-1]
    finally:
        P.subprocess.run = orig
    print("test_claude_cli_provider_uses_json_schema OK")


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


def test_archetype_full_pass_ignores_coverage_stop_until_all_axes():
    """2026-09-24: 軸ごとの framing では、1 軸の重複率 (coverage 条件) で残りの軸を打ち切らない (BUILD_ARCHETYPE_FULL_PASS)。
    回帰測定で Opus 5.5 が stall の軸で重複 75% を出し、special を含む 4 軸が回らずに止まった"""
    feats, th = _feats()
    spec = BuildSpec(owned=["a", "b", "c", "d"])
    legal, mega = {"a", "b", "c", "d", "t1", "t2", "t3"}, {"a"}
    base = K.rule_baseline_concepts(feats, th, mega, favorites=spec.favorites)
    dup_core = list(base[0]["core_ids"])[:3]                     # ルール baseline と同じ core = 重複 100%
    axes = [{"id": "setup_sweep", "label": "積み展開", "description": "d", "switching": "no_switch", "switching_ja": "交代しない",
             "special": False, "branches": []},
            {"id": "special", "label": "特殊な勝ち筋", "description": "d", "switching": "mixed", "switching_ja": "状況次第",
             "special": True, "branches": []}]
    dup = json.dumps({"authoritative": {"concepts": [{"name": "same", "core_ids": dup_core, "mega_id": None,
                                                      "win_condition": "setup_sweep", "support_roles": [], "weak_to": [],
                                                      "archetype": "setup_sweep", "branch": None}]}})
    new = json.dumps({"authoritative": {"concepts": [{"name": "perish", "core_ids": ["c", "d"], "mega_id": None,
                                                      "win_condition": "anti_meta", "support_roles": [], "weak_to": [],
                                                      "archetype": "special", "branch": None}]}})
    res = K.generate_concepts(spec, feats, th, legal, mega, provider=MockProvider([dup, new]), rounds=2, per_round=1,
                              archetypes=axes)
    assert res["rounds"] == 2 and res["stop_reason"] == "max_rounds"                   # 一巡は止めない
    assert any(f.get("archetype") == "special" for f in res["families"])
    orig = K.BUILD_ARCHETYPE_FULL_PASS
    K.BUILD_ARCHETYPE_FULL_PASS = False
    try:
        res2 = K.generate_concepts(spec, feats, th, legal, mega, provider=MockProvider([dup, new]), rounds=2, per_round=1,
                                   archetypes=axes)
    finally:
        K.BUILD_ARCHETYPE_FULL_PASS = orig
    assert res2["rounds"] == 1 and res2["stop_reason"] == "coverage"                   # 従来: 1 軸目の重複で停止
    # 軸ではない framing (style) は従来どおり coverage で止まる
    res3 = K.generate_concepts(spec, feats, th, legal, mega, provider=MockProvider([dup, new]), rounds=2, per_round=1)
    assert res3["rounds"] == 1 and res3["stop_reason"] == "coverage"
    print("test_archetype_full_pass_ignores_coverage_stop_until_all_axes OK")


if __name__ == "__main__":
    test_validate_and_cluster()
    test_generate_with_mock_provider()
    test_extract_json_braces_in_strings_and_record_numbering()
    test_structured_output_is_preferred()
    test_claude_cli_provider_uses_json_schema()
    test_generate_with_archetypes()
    test_archetype_full_pass_ignores_coverage_stop_until_all_axes()
