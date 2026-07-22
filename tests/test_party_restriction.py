"""選出3体制限 (未選出ポケモンへの交代提案の防止) のテスト。

    scripts/run_test.sh test_party_restriction
"""
from __future__ import annotations

from advisor.party import battle_party_indices, is_switchable
from advisor.rl_bridge import _legal_actions


def _party6(picked_idx=(0, 1, 2), appeared_idx=()):
    party = []
    for i in range(6):
        p = {"species_id": f"mon{i}", "species_ja": f"モン{i}", "status": None}
        if i in picked_idx:
            p["is_picked"] = True
        if i in appeared_idx:
            p["hp_percent"] = 100.0
        party.append(p)
    return {"active_index": 0, "party": party}


def test_indices():
    # 選出3体確定 -> その3体のみ
    st = _party6(picked_idx=(0, 2, 4))
    assert battle_party_indices(st) == {0, 2, 4}
    assert is_switchable(st, 2) and not is_switchable(st, 1)
    # 選出フラグ一部 + 登場済みで補完
    st = _party6(picked_idx=(0,), appeared_idx=(3,))
    assert battle_party_indices(st) == {0, 3}
    # フラグ皆無 + 登場3体 -> 登場組
    st = _party6(picked_idx=(), appeared_idx=(1, 2, 5))
    assert battle_party_indices(st) == {1, 2, 5}
    # 情報不足 -> None (制限なし)
    st = _party6(picked_idx=(), appeared_idx=(0,))
    assert battle_party_indices(st) is None
    print("test_indices OK")


def test_legal_actions_respect_picks():
    # 実戦相当: 選出0/1/2、未選出3-5は交代候補に出ない
    st = {
        "scene": "command", "mega_used": {},
        "field": {}, "player": _party6(picked_idx=(0, 1, 2)),
        "opponent": {"active_index": None, "party": []},
    }
    # active=0 なので交代候補は 1, 2 のみのはず
    labels = [l for _i, l, k in _legal_actions(st) if k == "switch"]
    assert sorted(labels) == ["交代:モン1", "交代:モン2"], labels
    print("test_legal_actions_respect_picks OK")


def test_engine_switch_restriction():
    # エンジンのアドバイスに未選出への交代が含まれないこと
    from advisor.service import Advisor
    from tests.test_rl_bridge import _state
    st = _state()
    # パーティを6体に拡張し、後半3体を未選出にする
    st["player"]["party"] += [
        {"species_id": "duraludon", "species_ja": "ブリジュラス",
         "types": ["はがね", "ドラゴン"], "status": None},
        {"species_id": "raichu", "species_ja": "ライチュウ",
         "types": ["でんき"], "status": None},
        {"species_id": "rotomheat", "species_ja": "ヒートロトム",
         "types": ["でんき", "ほのお"], "status": None},
    ]
    for i, p in enumerate(st["player"]["party"]):
        p["is_picked"] = i < 3
    result = Advisor().advise(st)
    assert result.get("ok"), result.get("reason")
    switch_names = [a["name"] for a in result["actions"]
                    if a.get("kind") == "switch"]
    for banned in ("ブリジュラス", "ライチュウ", "ヒートロトム"):
        assert banned not in switch_names, switch_names
    print(f"test_engine_switch_restriction OK (交代候補: {switch_names})")


if __name__ == "__main__":
    test_indices()
    test_legal_actions_respect_picks()
    test_engine_switch_restriction()
    print("\nALL OK")
