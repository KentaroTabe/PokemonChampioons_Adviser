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
    test_simulate_turn_faint_and_switch()
    test_clean_kill_preferred()
    test_lethal_dodge()
    test_performance()
    print("ALL OK")
