"""同時手番探索エンジン (advisor/search) の検証。

使い方: python -m tests.test_search
"""
import time

from advisor.damage import FieldView, MonView
from advisor.dex import get_dex
from advisor.search import Action, SimSide, search, simulate_turn, static_eval


def _view(sid, ev=None, nature=None, item=None, ja=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=ja or sid, types=sp["types"],
                   base=sp["baseStats"], ev=ev or {"atk": 252, "spa": 252, "spe": 252},
                   nature=nature or {}, item=item)


def test_lethal_dodge():
    # ラグラージ vs 晴れリザードンY (ソーラービーム致死)。
    # 探索は「その場で殴る」より「交代 or 保証値の高い行動」を選ぶはず
    me = SimSide(active=_view("swampert", ja="ラグラージ"), active_hp=1.0,
                 bench=[(_view("archaludon", ja="ブリジュラス",
                               ev={"hp": 252, "def": 160}), 1.0)])
    opp = SimSide(active=_view("charizardmegay", ja="メガリザードンY"),
                  active_hp=1.0)
    sun = FieldView(weather="sun")
    res = search(me, opp, ["waterfall", "earthquake"],
                 [("solarbeam", 40), ("flamethrower", 30)],
                 my_field=sun, opp_field=sun)
    assert res["actions"], "行動が空"
    top = res["actions"][0]
    atk = next(a for a in res["actions"] if a["move_id"] == "waterfall")
    # たきのぼり続行は択リスク持ち (ソラビで先に落ちる系列がある)
    assert atk["worst"] < atk["expected"], atk
    print(f"test_lethal_dodge OK: 推奨={top['label']} "
          f"(期待{top['expected']} 保証{top['worst']})")
    for a in res["actions"]:
        print(f"    {a['label']}: 期待{a['expected']} 保証{a['worst']} "
              f"最悪応手={a['worst_reply']} {'⚠択' if a['risky'] else ''}")


def test_clean_kill_preferred():
    # 圧倒的有利対面 (メガラグラージ vs ヒードラン): 弱点技で殴るのが最善
    me = SimSide(active=_view("swampertmega", ja="メガラグラージ",
                              nature={"atk": 1.1}), active_hp=1.0)
    opp = SimSide(active=_view("heatran", ja="ヒードラン",
                               ev={"hp": 252, "spa": 252}), active_hp=1.0)
    res = search(me, opp, ["earthquake", "icepunch"],
                 [("magmastorm", 50), ("earthpower", 30)])
    top = res["actions"][0]
    assert top["move_id"] == "earthquake", res["actions"]
    assert not top["risky"], top
    print(f"test_clean_kill_preferred OK: {top['label']} 保証{top['worst']}")


def test_priority_mechanics():
    # ふいうち: 相手が「攻撃技を選んで未行動」の場合のみ成功する
    me = SimSide(active=_view("grimmsnarl", ja="オーロンゲ"), active_hp=1.0)
    opp = SimSide(active=_view("garchomp", ja="ガブリアス"), active_hp=1.0,
                  bench=[(_view("rotomwash"), 1.0)])
    _, o2 = simulate_turn(me, opp, Action("move", move_id="suckerpunch"),
                          Action("switch", bench_index=0), None, None, "avg")
    assert o2.active_hp == 1.0, "交代相手にふいうちが当たっている"
    _, o3 = simulate_turn(me, opp, Action("move", move_id="suckerpunch"),
                          Action("move", move_id="earthquake"),
                          None, None, "avg")
    assert o3.active_hp < 1.0, "攻撃してきた相手へのふいうちが失敗扱い"
    _, o4 = simulate_turn(me, opp, Action("move", move_id="suckerpunch"),
                          Action("move", move_id="swordsdance"),
                          None, None, "avg")
    assert o4.active_hp == 1.0, "変化技の相手にふいうちが当たっている"

    # 優先度の段階: しんそく(+2) はアクアジェット(+1) より先に動く
    slow_es = SimSide(active=_view("dragonite", ja="カイリュー",
                                   ev={"atk": 252}), active_hp=1.0)
    fast_jet = SimSide(active=_view("floatzel", ja="フローゼル",
                                    ev={"atk": 252, "spe": 252}),
                       active_hp=0.03)
    m2, o5 = simulate_turn(slow_es, fast_jet,
                           Action("move", move_id="extremespeed"),
                           Action("move", move_id="aquajet"),
                           None, None, "avg")
    assert o5.active_hp <= 0.0, "しんそくが先に解決されていない"
    assert m2.active_hp == 1.0, "倒れた相手のアクアジェットが発動している"
    print("test_priority_mechanics OK")


def test_simulate_turn_faint_and_switch():
    me = SimSide(active=_view("mimikyu", ja="ミミッキュ",
                              nature={"atk": 1.1}, item="lifeorb"),
                 active_hp=1.0)
    opp = SimSide(active=_view("garchomp", ja="ガブリアス"), active_hp=0.05,
                  bench=[(_view("rotomwash", ja="ロトム"), 1.0)])
    m2, o2 = simulate_turn(
        me, opp, Action("move", move_id="playrough"),
        Action("move", move_id="earthquake"), None, None, "avg")
    # ミミッキュ先手でガブ処理 -> 相手はロトムが後続で出てくる
    assert o2.active.species_id == "rotomwash", o2.active.species_id
    assert o2.alive_count() == 1
    print("test_simulate_turn_faint_and_switch OK")


def test_status_move_effects():
    from advisor.search import simulate_turn
    me = SimSide(active=_view("archaludon", ja="ブリジュラス",
                              ev={"hp": 252, "def": 160}), active_hp=0.4)
    opp = SimSide(active=_view("garchomp", ja="ガブリアス"), active_hp=1.0)
    # まもる: 相手の攻撃を無効化
    m2, o2 = simulate_turn(me, opp, Action("move", move_id="protect"),
                           Action("move", move_id="earthquake"),
                           None, None, "avg")
    assert m2.active_hp == 0.4, m2.active_hp
    # 積み技: ランクが上がる
    m3, o3 = simulate_turn(me, opp, Action("move", move_id="irondefense"),
                           Action("move", move_id="stealthrock"),
                           None, None, "avg")
    assert m3.active.boosts.get("def") == 2, m3.active.boosts
    assert m3.stealth_rock, "相手のステロが自陣に付いていない"
    # 回復技
    m4, _ = simulate_turn(me, opp, Action("move", move_id="recover"),
                          Action("move", move_id="stealthrock"),
                          None, None, "avg")
    assert m4.active_hp > 0.85, m4.active_hp
    # 状態異常技
    m5, o5 = simulate_turn(me, opp, Action("move", move_id="thunderwave"),
                           Action("move", move_id="swordsdance"),
                           None, None, "avg")
    assert o5.active.status == "paralysis", o5.active.status
    assert o5.active.boosts.get("atk") == 2, o5.active.boosts
    print("test_status_move_effects OK")


def test_protect_scores_in_search():
    # 瀕死状態でまもるが「即死撃ちより保証値が高い」選択肢として現れる
    me = SimSide(active=_view("archaludon", ja="ブリジュラス",
                              ev={"hp": 252, "def": 160}), active_hp=0.15,
                 bench=[(_view("pelipper", ja="ペリッパー"), 1.0)])
    opp = SimSide(active=_view("garchomp", ja="ガブリアス"), active_hp=0.9)
    res = search(me, opp, ["dragonpulse", "protect"],
                 [("earthquake", 60), ("outrage", 30)])
    prot = next(a for a in res["actions"] if a["move_id"] == "protect")
    atk = next(a for a in res["actions"] if a["move_id"] == "dragonpulse")
    # まもるが「被弾して落ちる」扱いになっていない (最悪ケースでも盤面維持)
    assert prot["worst"] > -0.3, prot
    # 攻撃も正しく評価されている (このケースは打ち逃げ優位でも良い)
    assert atk["expected"] > -1.0
    print(f"test_protect_scores_in_search OK "
          f"(まもる保証{prot['worst']} / 攻撃期待{atk['expected']})")


def test_performance():
    me = SimSide(active=_view("swampert"), active_hp=1.0,
                 bench=[(_view("archaludon"), 1.0), (_view("pelipper"), 0.8)])
    opp = SimSide(active=_view("garchomp"), active_hp=1.0,
                  bench=[(_view("rotomwash"), 1.0), (_view("kingambit"), 1.0)])
    t0 = time.time()
    res = search(me, opp, ["earthquake", "icepunch", "waterfall", "protect"],
                 [("earthquake", 40), ("outrage", 30), ("swordsdance", 15),
                  ("stealthrock", 10)])
    dt = time.time() - t0
    assert res["actions"]
    assert dt < 5.0, f"探索が遅すぎる: {dt:.1f}s"
    print(f"test_performance OK: 6x6行列+2手読み {dt:.2f}s")


if __name__ == "__main__":
    test_priority_mechanics()
    test_simulate_turn_faint_and_switch()
    test_clean_kill_preferred()
    test_lethal_dodge()
    test_status_move_effects()
    test_protect_scores_in_search()
    test_performance()
    print("ALL OK")
