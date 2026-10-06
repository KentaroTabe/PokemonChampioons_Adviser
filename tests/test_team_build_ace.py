"""指定エース (BuildSpec.ace、run.py --ace) の純粋関数テスト。2026-10-02 ユーザー依頼「メガミミロップをエースとする
パーティの構築提案」を機械的な制約にしたもの: エース = 固定枠 + (メガ石を持てる種なら) 探索の並びで唯一のメガ石の持ち主、
S4 の concept は core_ids にエースを含み mega_id もエース。

    scripts/run_test.sh test_team_build_ace
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from champions_agent.config import BUILD_ACE_MAX_MEGA_STONES
from tools.team_build import concepts as K
from tools.team_build import spec as SP
from tools.team_build.sets import SetCandidate, enforce_max_megas, has_mega_stone, prefer_mega_set


def test_parse_form_ace_is_favorite():
    legal = {"a", "b", "c", "d", "e", "f", "g", "scizor"}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "banned.txt"
        p.write_text("scizor\n", encoding="utf-8")
        spec = SP.parse_form({"ace": "b"}, owned=list("abcdefg"), banned_path=p, legal=legal)
        assert spec.ace == "b" and spec.favorites == ["b"] and spec.locked == ["b"], (spec.ace, spec.favorites)
        assert spec.provenance["ace"] == "resolved" and spec.provenance.get("favorites") != "resolved"
        assert spec.objective == "favorites" and spec.provenance["objective"] == "inferred"   # 固定枠制約下の max_wr
        assert SP.validate_spec(spec, legal) == [], SP.validate_spec(spec, legal)
        # 固定枠と併用: 和集合 (重複なし)
        spec2 = SP.parse_form({"ace": "b", "favorites": "c,b"}, owned=list("abcdefg"), banned_path=p, legal=legal)
        assert spec2.favorites == ["b", "c"] and spec2.ace == "b"
        # 除外に入っている / 使える種にない → validate で止まる
        spec3 = SP.parse_form({"ace": "scizor"}, owned=list("abcdefg"), banned_path=p, legal=legal)
        probs = SP.validate_spec(spec3, legal)
        assert any("エース scizor" in x and "除外" in x for x in probs), probs
        spec4 = SP.parse_form({"ace": "zzz"}, owned=list("abcdefg"), banned_path=p, legal=legal)
        assert any("エース zzz" in x and "使える種にない" in x for x in SP.validate_spec(spec4, legal))
        # 空・非文字列は無視
        assert SP.parse_form({"ace": ""}, owned=list("abc"), banned_path=p, legal=legal).ace == ""
        assert SP.parse_form({"ace": ["b"]}, owned=list("abc"), banned_path=p, legal=legal).ace == ""
        # request.json の往復
        SP.save_spec(spec, Path(d))
        back = SP.load_spec(Path(d) / "request.json")
        assert back.ace == "b" and back.favorites == ["b"]
        # 古い request.json (ace 無し) は空
        assert SP.BuildSpec().ace == ""
    # 日本語名の解決 (名前表)
    assert SP.resolve_species_token("ミミロップ") == "lopunny"
    print("test_parse_form_ace_is_favorite OK")


def _cand(sid, item, score, source="representative", moves=("tackle",)):
    return SetCandidate(sid, "ability", item, "jolly", "2/32/0/0/0/32", list(moves), source, score)


def test_prefer_mega_set():
    lop_lo = _cand("lopunny", "lifeorb", 0.70)
    lop_stone = _cand("lopunny", "lopunnite", 0.65, "alt:item")
    lop_sash = _cand("lopunny", "focussash", 0.60, "alt:item")
    other = _cand("primarina", "leftovers", 0.5)
    alts = {"lopunny": [lop_lo, lop_sash, lop_stone], "primarina": [other]}
    team, holds = prefer_mega_set([other, lop_lo], alts, "lopunny")
    assert holds and [c.item for c in team] == ["leftovers", "lopunnite"], [c.item for c in team]
    assert team[1].notes == ["ace_mega:lifeorb->lopunnite"] and team[1].score == 0.65 and lop_lo.item == "lifeorb"
    # 既に石を持っていればそのまま (notes も付かない)
    team2, holds2 = prefer_mega_set([lop_stone, other], alts, "lopunny")
    assert holds2 and team2[0] is lop_stone and team2[0].notes == []
    # 石持ちの型が無ければそのまま (holds=False)
    team3, holds3 = prefer_mega_set([lop_lo, other], {"lopunny": [lop_lo, lop_sash]}, "lopunny")
    assert not holds3 and team3[0] is lop_lo
    # エースが並びに居なければ何もしない
    team4, holds4 = prefer_mega_set([other], alts, "lopunny")
    assert not holds4 and team4 == [other]
    assert has_mega_stone("lopunnite") and has_mega_stone("garchompitez") and not has_mega_stone("leftovers")
    assert not has_mega_stone(None)
    print("test_prefer_mega_set OK")


def test_ace_is_only_mega_holder():
    """エースを keep、上限 BUILD_ACE_MAX_MEGA_STONES (=1): 他の石持ちは石以外の最良代替へ"""
    assert BUILD_ACE_MAX_MEGA_STONES == 1
    lop = _cand("lopunny", "lopunnite", 0.65)
    chomp = _cand("garchomp", "garchompitez", 0.9)
    chomp_alt = _cand("garchomp", "choicescarf", 0.8, "alt:item")
    zard = _cand("charizard", "charizarditey", 0.95)
    alts = {"lopunny": [lop], "garchomp": [chomp, chomp_alt], "charizard": [zard]}
    team = enforce_max_megas([chomp, lop, zard], alts, keep="lopunny", max_n=BUILD_ACE_MAX_MEGA_STONES)
    assert [c.item for c in team] == ["choicescarf", "lopunnite", None], [c.item for c in team]
    assert team[0].notes == ["mega_cap:garchompitez->choicescarf"] and team[2].notes == ["mega_cap:charizarditey->none"]
    # 被覆が低くてもエースが残る (keep が最優先)
    print("test_ace_is_only_mega_holder OK")


def test_validate_concepts_with_ace():
    owned, legal, mega = {"a", "b", "c", "d"}, {"a", "b", "c", "d", "t1"}, {"a", "b"}
    ok = {"concepts": [{"name": "x", "core_ids": ["a", "b"], "mega_id": "a", "win_condition": "setup_sweep",
                        "support_roles": ["priority"], "weak_to": ["t1"]}]}
    assert K.validate_concepts(ok, owned, legal, mega, ace="a", ace_mega=True) == []
    # mega_id が別のメガ候補 → 差し戻し
    probs = K.validate_concepts(ok, owned, legal, mega, ace="b", ace_mega=True)
    assert len(probs) == 1 and "mega_id はエース b" in probs[0], probs
    # エースが core に無い → 差し戻し
    probs = K.validate_concepts(ok, owned, legal, mega, ace="c")
    assert len(probs) == 1 and "エース c を core_ids" in probs[0], probs
    # エースがメガ石を持てない種なら mega_id は自由
    assert K.validate_concepts(ok, owned, legal, mega, ace="b", ace_mega=False) == []
    # apply_ace: 系統の mega_id をエースにそろえる (ace_mega のときだけ)
    fams = [{"family_id": "C001", "core_ids": ["c", "d"], "mega_id": "a"}, {"family_id": "C002", "core_ids": ["b"], "mega_id": None}]
    out = K.apply_ace(fams, "b", True)
    assert [f["mega_id"] for f in out] == ["b", "b"] and fams[0]["mega_id"] == "a"
    assert K.apply_ace(fams, "b", False) is fams and K.apply_ace(fams, None, True) is fams
    print("test_validate_concepts_with_ace OK")


def test_generate_concepts_passes_ace_to_llm():
    """LLM への入力に ace と制約文が入り、エースを含まない出力は差し戻される"""
    import json
    from tools.team_build.candidates import SpeciesFeature
    from tools.team_build.llm.provider import MockProvider
    from tools.team_build.spec import BuildSpec
    th = ["t1", "t2"]

    def f(s, cov, roles, mg=False):
        return SpeciesFeature(s, dict(zip(th, cov)), roles, ("x",), mg)
    feats = {"a": f("a", [0.9, 0.2], {"setup": 1.0}, True), "b": f("b", [0.1, 0.9], {}), "c": f("c", [0.5, 0.5], {})}
    spec = BuildSpec(owned=["a", "b", "c"], favorites=["a"], ace="a")
    legal, mega = {"a", "b", "c", "t1", "t2"}, {"a"}
    bad = json.dumps({"authoritative": {"concepts": [
        {"name": "bc", "core_ids": ["b", "c"], "mega_id": None, "win_condition": "cycle_pressure", "support_roles": [], "weak_to": []}]}})
    good = json.dumps({"authoritative": {"concepts": [
        {"name": "ab", "core_ids": ["a", "b"], "mega_id": "a", "win_condition": "setup_sweep", "support_roles": [], "weak_to": []}]}})
    prov = MockProvider([bad, good])
    res = K.generate_concepts(spec, feats, th, legal, mega, provider=prov, rounds=1, per_round=1)
    call = res["llm_calls"][0]
    assert call["ok"] and call["attempts"] == 2, call                          # 1 回目 (エース無し) は差し戻し
    first, second = prov.calls[0], prov.calls[1]
    assert '"ace": "a"' in first["prompt"] and "エースだけがメガ石を持ち" in first["prompt"], first["prompt"][-600:]
    assert any("エース a を core_ids" in p for p in first["problems"]), first["problems"]
    assert second["problems"] == []
    assert any(fm["core_ids"] == ["a", "b"] and fm["mega_id"] == "a" for fm in res["families"])
    print("test_generate_concepts_passes_ace_to_llm OK")


def test_ace_stone_violations():
    from tools.team_build.run import ace_stone_violations
    rows = [
        {"candidate_id": "L00", "ok": True, "ace": "lopunny",
         "sets": [{"species": "lopunny", "item": "lopunnite"}, {"species": "garchomp", "item": "choicescarf"}]},
        {"candidate_id": "L01", "ok": True, "ace": "lopunny",
         "sets": [{"species": "lopunny", "item": "lopunnite"}, {"species": "garchomp", "item": "garchompitez"}]},
        {"candidate_id": "L02", "ok": True, "ace": "lopunny",
         "sets": [{"species": "lopunny", "item": "lifeorb"}, {"species": "garchomp", "item": "leftovers"}]},
        {"candidate_id": "L03", "ok": True, "ace": None,                       # 現行枝 (非適用)
         "sets": [{"species": "lopunny", "item": "lopunnite"}, {"species": "garchomp", "item": "garchompitez"}]},
        {"candidate_id": "L04", "ok": False, "ace": "lopunny", "sets": []},    # 不合法の行は見ない
    ]
    bad = ace_stone_violations(rows, max_stones=1)
    assert [b[0] for b in bad] == ["L01", "L02"], bad
    assert bad[0][1] == ["lopunny", "garchomp"] and bad[1][1] == []
    print("test_ace_stone_violations OK")


def test_restore_locked_sets():
    """指定の型 (custom_sets) は S6 で変えない: 規則・軸の差し込みで変わった型を元に戻す。エースの規則で外した石は戻さない"""
    from tools.team_build.run import restore_locked_sets
    lop = SetCandidate("lopunny", "limber", "lopunnite", "jolly", "2/32/0/0/0/32",
                       ["closecombat", "swordsdance", "tripleaxel", "thunderpunch"], "custom", 0.6)
    lop_arch = SetCandidate("lopunny", "limber", "lopunnite", "jolly", "2/32/0/0/0/32",
                            ["closecombat", "swordsdance", "tripleaxel", "batonpass"], "custom+arch", 0.6, ["arch:baton"])
    other = SetCandidate("primarina", "torrent", "leftovers", "modest", "32/0/0/32/0/2", ["moonblast"], "representative", 0.5)
    other2 = SetCandidate("primarina", "torrent", "leftovers", "modest", "32/0/0/32/0/2", ["moonblast", "yawn"], "representative+arch", 0.5)
    originals = {"lopunny": lop}
    team, restored = restore_locked_sets([lop_arch, other2], originals, {"lopunny"}, stone_keeper="lopunny")
    assert restored == ["lopunny"] and team[0].moves == lop.moves and team[0].item == "lopunnite"
    assert team[0].notes == ["locked:restored<-custom+arch"] and team[1] is other2      # 指定でない種は触らない
    # 変わっていなければそのまま (印も付かない)
    team2, restored2 = restore_locked_sets([lop, other], originals, {"lopunny"})
    assert restored2 == [] and team2[0] is lop
    # 指定の型が別のメガ (エースでない) で、エースの規則で石を外されていた → 技は戻すが石は戻さない
    chomp = SetCandidate("garchomp", "roughskin", "garchompitez", "naive", "0/2/0/32/0/32", ["dracometeor"], "custom", 0.7)
    chomp_now = SetCandidate("garchomp", "roughskin", None, "naive", "0/2/0/32/0/32", ["dracometeor", "stealthrock"], "custom+arch", 0.7)
    team3, restored3 = restore_locked_sets([chomp_now], {"garchomp": chomp}, {"garchomp"}, stone_keeper="lopunny")
    assert restored3 == ["garchomp"] and team3[0].moves == ["dracometeor"] and team3[0].item is None
    print("test_restore_locked_sets OK")


def main() -> None:
    test_parse_form_ace_is_favorite()
    test_restore_locked_sets()
    test_prefer_mega_set()
    test_ace_is_only_mega_holder()
    test_validate_concepts_with_ace()
    test_generate_concepts_passes_ace_to_llm()
    test_ace_stone_violations()
    print("ALL OK")


if __name__ == "__main__":
    main()
