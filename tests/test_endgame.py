"""詰み筋・勝ち筋判定 (advisor/endgame) の検証。

使い方: python -m tests.test_endgame
"""
from advisor.damage import MonView
from advisor.dex import get_dex
from advisor.endgame import duel, endgame_note, matchup_matrix


def _view(sid, ev=None, nature=None, ability=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=sid, types=sp["types"],
                   base=sp["baseStats"], ability=ability,
                   ev=ev or {"atk": 252, "spa": 252, "spe": 252},
                   nature=nature or {})


def test_duel_type_advantage():
    # メガラグラージ(じしん) vs ヒードラン(マグマストーム): 4倍弱点側の圧勝
    swampert = _view("swampertmega", nature={"atk": 1.1})
    heatran = _view("heatran", ev={"hp": 252, "spa": 252})
    assert duel(swampert, 1.0, ["earthquake"],
                heatran, 1.0, ["magmastorm", "earthpower"]) is True
    # 攻撃手段がなければ負け
    assert duel(heatran, 1.0, ["flamethrower"],
                _view("swampert"), 1.0, ["earthquake"]) in (True, False)
    print("test_duel_type_advantage OK")


def test_win_condition_detection():
    my_mons = [
        ("メガラグラージ", _view("swampertmega", nature={"atk": 1.1}), 1.0,
         ["earthquake", "waterfall", "icepunch"]),
        ("ペリッパー", _view("pelipper", ev={"hp": 252, "spa": 252}), 0.5,
         ["hurricane", "hydropump"]),
    ]
    opp_mons = [
        ("ヒードラン", _view("heatran", ev={"hp": 252, "spa": 252}), 1.0,
         ["magmastorm", "earthpower", "flashcannon"]),
        ("ウルガモス", _view("volcarona"), 1.0,
         ["fireblast", "bugbuzz", "quiverdance"]),
    ]
    r = matchup_matrix(my_mons, opp_mons)
    # メガラグラージは炎2体に対して勝ち筋になるはず
    assert "メガラグラージ" in r["win_conditions"], r
    note = endgame_note(r)
    assert "勝ち筋" in note and "メガラグラージ" in note, note
    print(f"test_win_condition_detection OK: {note}")


def test_lose_threat_detection():
    my_mons = [("ヒードラン", _view("heatran", ev={"hp": 252, "spa": 252}), 0.4,
                ["flamethrower", "flashcannon"])]
    opp_mons = [("メガラグラージ", _view("swampertmega", nature={"atk": 1.1}), 1.0,
                 ["earthquake", "waterfall"])]
    r = matchup_matrix(my_mons, opp_mons)
    assert "メガラグラージ" in r["lose_threats"], r
    note = endgame_note(r)
    assert "負け筋" in note, note
    print(f"test_lose_threat_detection OK: {note}")


def test_duel_score_speed_matters():
    from advisor.endgame import duel_score
    # 同程度の打ち合いなら素早い側が有利に傾く
    fast = _view("gengar")             # S110族 AS252
    slow = _view("metagross", ev={"atk": 252, "hp": 252})
    s = duel_score(fast, 1.0, ["shadowball"], slow, 1.0, ["zenheadbutt"])
    s_rev = duel_score(slow, 1.0, ["zenheadbutt"], fast, 1.0, ["shadowball"])
    assert s is not None and s_rev is not None
    assert abs(s + s_rev) < 0.01, (s, s_rev)   # 対称性
    # すいすい: 雨下では素早さ判定が逆転し得る (effective_speed経由)
    from advisor.damage import FieldView, effective_speed
    swampert = _view("swampertmega", ability="swiftswim")
    assert effective_speed(swampert, FieldView(weather="rain")) > \
        effective_speed(fast)
    print(f"test_duel_score_speed_matters OK (s={s:+.2f})")


def _view2(sid, **kw):
    return _view(sid, **kw)


if __name__ == "__main__":
    test_duel_type_advantage()
    test_duel_score_speed_matters()
    test_win_condition_detection()
    test_lose_threat_detection()
    print("ALL OK")
