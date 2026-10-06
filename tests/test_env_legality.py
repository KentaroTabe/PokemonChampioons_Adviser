"""学習に流すチームの技の合法性 (champions_agent/env/legality) のテスト。表は引数で渡し learnsets.ts を読まない。

    python -m tests.test_env_legality
"""
from __future__ import annotations

from champions_agent.env import legality as L

TABLE = {"sirfetchd": {"leafblade", "closecombat", "firstimpression", "bravebird", "swordsdance", "knockoff"},
         "rotom": {"thunderbolt", "voltswitch", "willowisp", "protect"},
         "rotomwash": {"thunderbolt", "voltswitch", "hydropump", "willowisp"}}


def test_legal_and_fill():
    # mod の learnset に無い技 (メテオアサルト) は落ち、使用率順の合法な技で 4 本に埋める
    moves = ["leafblade", "meteorassault", "firstimpression", "closecombat"]
    assert L.legal_moves("sirfetchd", moves, TABLE) == ["leafblade", "firstimpression", "closecombat"]
    filled = L.fill_moves("sirfetchd", moves, ["meteorassault", "closecombat", "bravebird", "swordsdance"], table=TABLE)
    assert filled == ["leafblade", "firstimpression", "closecombat", "bravebird"]
    # 重複と空は落とす、順序は保つ
    assert L.legal_moves("sirfetchd", ["closecombat", "", "closecombat", "leafblade"], TABLE) == ["closecombat", "leafblade"]
    # フォルムは前方一致 (rotomwash は自分の行があればそれ、無い形は rotom に倒す)
    assert L.legal_moves("rotomwash", ["hydropump", "overheat"], TABLE) == ["hydropump"]
    assert L.legal_moves("rotomheat", ["thunderbolt", "overheat"], TABLE) == ["thunderbolt"]
    # learnset に無い種は判定できないのでそのまま
    assert not L.known_species("nosuchmon", TABLE) and L.legal_moves("nosuchmon", ["a", "b"], TABLE) == ["a", "b"]
    assert L.fill_moves("nosuchmon", ["a"], ["b", "c"], n=2, table=TABLE) == ["a", "b"]
    # 合法な技が 1 本も無ければ空 (呼び出し側が型を捨てる)
    assert L.fill_moves("sirfetchd", ["meteorassault"], ["meteorassault"], table=TABLE) == []
    print("test_legal_and_fill OK")


def test_real_table_sirfetchd():
    """実データ: champions mod の learnset でネギガナイトはメテオアサルトを覚えない (2026-09-13 の障害の再現)"""
    moves = L.legal_moves("sirfetchd", ["leafblade", "meteorassault", "firstimpression", "closecombat"])
    if L.known_species("sirfetchd"):
        assert "meteorassault" not in moves and "leafblade" in moves
    print("test_real_table_sirfetchd OK")


if __name__ == "__main__":
    test_legal_and_fill()
    test_real_table_sirfetchd()
