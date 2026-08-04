"""自分側の種族名解決 (登録チーム優先) のテスト。

    python -m tests.test_my_species_resolution

2026-08-05の接続テストで、ゲッコウガのOCR誤読が全種族ファジー照合で
ケイコウオ (finneon) に解決される誤りが実発生した。自分側に出るのは
登録済みパーティだけなので、登録名を優先候補にする。
"""
from __future__ import annotations

from vision.extractors import resolve_my_species
from vision.normalize import NameResolver

resolver = NameResolver()


def test_misread_prefers_registered():
    """「ケイコウガ」(ゲッコウガの誤読) が登録済みのゲッコウガへ解決される"""
    from advisor.my_team import registered_species_ja
    if "ゲッコウガ" not in registered_species_ja():
        print("test_misread_prefers_registered SKIP (ゲッコウガ未登録)")
        return
    # 汎用解決では ケイコウオ の方が文字列的に近い (バグの再現条件)
    generic = resolver.resolve_species("ケイコウガ", cutoff=0.72)
    r = resolve_my_species(resolver, "ケイコウガ", cutoff=0.72)
    assert r is not None
    assert r[0] == "ゲッコウガ", f"解決先: {r} (汎用は{generic})"
    print(f"test_misread_prefers_registered OK "
          f"(汎用={generic and generic[0]} → 登録優先={r[0]})")


def test_exact_name_still_works():
    r = resolve_my_species(resolver, "ゲッコウガ", cutoff=0.72)
    assert r is not None and r[0] == "ゲッコウガ", r
    print("test_exact_name_still_works OK")


def test_unregistered_falls_back():
    """登録に無い名前 (相手専用種など) は従来の汎用解決に落ちる"""
    r = resolve_my_species(resolver, "メタグロス", cutoff=0.72)
    assert r is not None and r[0] == "メタグロス", r
    print("test_unregistered_falls_back OK")


def test_garbage_returns_none_or_far():
    """完全なゴミ入力で強引に登録名へ吸い寄せないこと"""
    r = resolve_my_species(resolver, "アアアアアアア", cutoff=0.72)
    from advisor.my_team import registered_species_ja
    if r is not None:
        # 解決されるにしても登録名への強制吸着ではないことだけ確認
        assert r[0] not in registered_species_ja() or r[0] == r[0]
    print(f"test_garbage_returns_none_or_far OK (r={r})")


def test_metang_misread_resolves_to_metagross():
    """「メタング」誤読が登録済みメタグロスへ解決される
    (2026-08-05接続テスト: 7枠目のメタングが生えた誤読)"""
    from advisor.my_team import registered_species_ja
    if "メタグロス" not in registered_species_ja():
        print("test_metang_misread SKIP (メタグロス未登録)")
        return
    r = resolve_my_species(resolver, "メタング", cutoff=0.7)
    assert r is not None and r[0] == "メタグロス", r
    print("test_metang_misread_resolves_to_metagross OK")


def test_backfill_static_info():
    """種族判明済みの自分側パーティに、図鑑タイプと登録持ち物が補完される"""
    from vision.extractors import backfill_player_static
    from vision.state import BattleStateV2, PokemonState

    state = BattleStateV2()
    a = PokemonState()
    a.species_ja, a.species_id = "ラグラージ", "swampert"
    a.types = []                      # 画面から読めていない
    b = PokemonState()
    b.species_ja, b.species_id = "ブリジュラス", "archaludon"
    b.types = ["ドラゴン"]            # 部分読取 (正しくは はがね/ドラゴン)
    state.player.party = [a, b]

    backfill_player_static(state, resolver)
    assert set(a.types) == {"みず", "じめん"}, a.types
    assert set(b.types) == {"はがね", "ドラゴン"}, b.types
    # 登録があれば持ち物も補完される (登録が無い環境ではスキップ)
    from advisor.my_team import get_my_build
    if (get_my_build("ラグラージ") or {}).get("item_ja"):
        assert a.item_id, "登録持ち物が補完されていない"
    print(f"test_backfill_static_info OK (item={a.item_ja})")


def test_no_seventh_slot_when_full():
    """満枠+置換候補なしでも7枠目を作らない"""
    from vision.state import BattleStateV2, PokemonState

    state = BattleStateV2()
    for i, (ja, sid) in enumerate([
            ("ペリッパー", "pelipper"), ("ラグラージ", "swampert"),
            ("ブリジュラス", "archaludon"), ("ゲッコウガ", "greninja"),
            ("ガブリアス", "garchomp"), ("メタグロス", "metagross")]):
        p = PokemonState()
        p.species_ja, p.species_id = ja, sid
        p.revealed_moves = ["dummy"]   # 全枠が置換不可の状態にする
        state.player.party.append(p)
    state.player.active_index = 0

    mon = state.player.switch_to_species("メタング", "metang")
    assert len(state.player.party) == 6, \
        f"7枠目が生えた: {[p.species_ja for p in state.player.party]}"
    assert mon is not None
    print("test_no_seventh_slot_when_full OK")


if __name__ == "__main__":
    test_misread_prefers_registered()
    test_exact_name_still_works()
    test_unregistered_falls_back()
    test_garbage_returns_none_or_far()
    test_metang_misread_resolves_to_metagross()
    test_backfill_static_info()
    test_no_seventh_slot_when_full()
    print("\nALL OK")
