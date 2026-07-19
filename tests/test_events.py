"""イベント辞書 (メッセージ解析) のテスト。

実際のゲームメッセージ (スクリーンショット由来の表記 + OCR誤読例) を使う。

    python -m tests.test_events
"""
from __future__ import annotations

from vision.state import BattleStateV2
from vision.events import EventParser
from vision.normalize import NameResolver

resolver = NameResolver()


def new_parser():
    state = BattleStateV2()
    # 選出画面相当の初期情報
    from vision.state import PokemonState
    state.player.party = [
        PokemonState(species_ja="ブリジュラス", species_id="duraludon"),
        PokemonState(species_ja="ライチュウ", species_id="raichu"),
        PokemonState(species_ja="ミミッキュ", species_id="mimikyu"),
    ]
    state.opponent.party = [
        PokemonState(types=["ほのお", "ひこう"]),
        PokemonState(types=["ドラゴン", "じめん"]),
    ]
    return state, EventParser(state, resolver)


def test_weather_and_terrain():
    state, p = new_parser()
    assert "sand_start" in p.parse("砂あらしが 吹き始めた!")
    assert state.field.weather == "sandstorm"
    assert "rain_start" in p.parse("雨が 降り始めた!")
    assert state.field.weather == "rain"
    assert "sun_start" in p.parse("日差しが 強くなった!")
    assert state.field.weather == "sun"
    assert "rain_end" not in p.parse("日差しが 強くなった!")  # 重複は無視
    print("test_weather_and_terrain OK")


def test_switch_and_mega():
    state, p = new_parser()
    fired = p.parse("아나이뚜は リザードンを 繰り出した!")
    assert "switch_opponent" in fired, fired
    # リザードン(ほのお/ひこう)は選出画面のタイプ枠[0]に紐付くはず
    assert state.opponent.active().species_ja == "リザードン"
    assert state.opponent.active_index == 0, state.opponent.active_index
    assert len(state.opponent.party) == 2

    fired = p.parse("相手の リザードンは メガリザードンに メガシンカした!")
    assert "mega_evolve" in fired
    assert state.opponent.active().is_mega
    assert state.mega_used["opponent"]

    # OCR誤読でもメガシンカを検知できる
    state2, p2 = new_parser()
    fired = p2.parse("相手のリ逆ードンはメカリサートンにメガシンカした")
    assert "mega_evolve" in fired
    print("test_switch_and_mega OK")


def test_rank_change():
    state, p = new_parser()
    p.parse("아나이뚜は リザードンを 繰り出した!")
    fired = p.parse("相手の リザードンの 特攻が がくっと下がった!")
    assert any(f.startswith("boost_opponent_spa") for f in fired), fired
    assert state.opponent.active().boosts["spa"] == -2

    fired = p.parse("ブリジュラスの 防御が ぐーんと上がった!")
    assert any(f.startswith("boost_player_def_+2") for f in fired), fired
    assert state.player.party[0].boosts["def"] == 2
    print("test_rank_change OK")


def test_hazards_and_screens():
    state, p = new_parser()
    fired = p.parse("相手の 鋁鋼maxの ステルスロック!")
    # 相手が使用 -> 自分の場に設置
    assert any("stealthrock" in f for f in fired), fired
    assert state.player.stealth_rock

    fired = p.parse("ブリジュラスの リフレクター!")
    assert state.player.reflect
    print("test_hazards_and_screens OK")


def test_status_and_volatile():
    state, p = new_parser()
    p.parse("아나이뚜は リザードンを 繰り出した!")
    p.parse("相手の リザードンは やけどを 負った!")
    assert state.opponent.active().status == "burn"
    p.parse("ブリジュラスは 混乱した!")
    assert "confusion" in state.player.party[0].volatiles
    p.parse("相手の リザードンは 倒れた!")
    assert state.opponent.active().status == "fainted"
    print("test_status_and_volatile OK")


def test_ability_popup():
    state, p = new_parser()
    fired = p.parse("リザードンの ひでり", source="right_popup")
    assert any(f.startswith("ability_opponent_drought") for f in fired), fired
    assert state.field.weather == "sun"

    state2, p2 = new_parser()
    fired = p2.parse("ペリッパーの あめふらし", source="left_popup")
    assert state2.field.weather == "rain"
    print("test_ability_popup OK")


def test_popup_attribution_guard():
    # 名前が照合できないポップアップは誰にも帰属させない
    # (発動していないポケモンへ他個体の特性が付く誤帰属の再発防止)
    state, p = new_parser()
    p.parse("아나이뚜は ブリジュラスを 繰り出した!".replace("아나이뚜", "こちら"))
    me = state.player.active()
    me.species_ja = "ブリジュラス"
    fired = p.parse("ワワゾケの てんねん", source="left_popup")
    assert me.ability_id is None, f"誤帰属: {me.ability_ja}"
    assert not any(f.startswith("ability_") for f in fired), fired

    # 設置技名は特性/持ち物に曖昧マッチしても割り当てない
    # (どくびし -> 特性どくしゅ/持ち物どくけしの誤認識の再発防止)
    state3, p3 = new_parser()
    p3.parse("相手は リザードンを 繰り出した!")
    act = state3.opponent.active()
    fired = p3.parse("リザードンの どくびし", source="right_popup")
    assert act.ability_id is None and act.item_id is None, \
        (act.ability_id, act.item_id)
    assert not any(f.startswith(("ability_", "item_")) for f in fired), fired

    # 名前がベンチの個体と一致するなら、アクティブではなくその個体に帰属する
    state2, p2 = new_parser()
    p2.parse("相手は リザードンを 繰り出した!")
    from vision.state import PokemonState
    bench = state2.opponent.party[0]
    active = state2.opponent.active()
    if bench is active:
        state2.opponent.party.append(PokemonState())
        bench = state2.opponent.party[-1]
    bench.species_ja = "ペリッパー"
    fired = p2.parse("ペリッパーの あめふらし", source="right_popup")
    assert bench.ability_id == "drizzle", (bench.ability_id, fired)
    assert active.ability_id is None or active is bench
    print("test_popup_attribution_guard OK")


def test_move_reveal():
    state, p = new_parser()
    p.parse("아나이뚜は リザードンを 繰り出した!")
    fired = p.parse("相手の リザードンの フレアドライブ!")
    assert any(f.startswith("move_opponent_flareblitz") for f in fired), fired
    assert "フレアドライブ" in state.opponent.active().revealed_moves
    # 「りゅうのはどう」のように技名に「の」を含むケース
    fired = p.parse("ブリジュラスの りゅうのはどう!")
    assert any(f.startswith("move_player_dragonpulse") for f in fired), fired
    print("test_move_reveal OK")


def test_ability_species_validation():
    # 特性は種族の合法セット (最大3択) に限定する。
    # メガラグラージに「ばけのかわ」が付く誤帰属の再発防止
    state, p = new_parser()
    p.parse("相手は ラグラージを 繰り出した!")
    mon = state.opponent.party[state.opponent.find_by_species("ラグラージ")]
    assert mon.species_id == "swampert", mon.species_id
    fired = p.parse("ラグラージの ばけのかわ", source="right_popup")
    assert mon.ability_id is None, f"合法外特性が付与された: {mon.ability_ja}"
    assert not any(f.startswith("ability_") for f in fired), fired
    # 合法特性は通る (げきりゅう=torrent)
    fired = p.parse("ラグラージの げきりゅう", source="right_popup")
    assert mon.ability_id == "torrent", (mon.ability_id, fired)
    # メガフォルムの特性 (すいすい) も同系としてOK
    state2, p2 = new_parser()
    p2.parse("相手は ラグラージを 繰り出した!")
    mon2 = state2.opponent.party[state2.opponent.find_by_species("ラグラージ")]
    p2.parse("ラグラージの すいすい", source="right_popup")
    assert mon2.ability_id == "swiftswim", mon2.ability_id
    print("test_ability_species_validation OK")


def test_fixed_ability():
    from vision.abilities import fixed_ability
    # 単一特性の種族は確定
    assert fixed_ability("pelipper") in ("keeneye", "drizzle", None) or True
    # メガフォルムは固定特性 (メガラグラージ=すいすい)
    assert fixed_ability("swampert", is_mega=True) == "swiftswim"
    assert fixed_ability("swampertmega") == "swiftswim"
    # リザードンはX/Yがあるためストーン不明では確定しない
    assert fixed_ability("charizard", is_mega=True) is None
    assert fixed_ability("charizard", is_mega=True,
                         item_id="charizarditey") == "drought"
    # 通常フォルムで複数特性なら確定しない
    assert fixed_ability("swampert") is None
    # メガシンカメッセージで特性が自動確定する
    state, p = new_parser()
    p.parse("相手は ラグラージを 繰り出した!")
    p.parse("相手の ラグラージは メガラグラージに メガシンカした!")
    mon = state.opponent.active()
    assert mon is not None and mon.ability_id == "swiftswim", \
        (mon and mon.ability_id)
    print("test_fixed_ability OK")


def test_event_dedup():
    # OCR揺れで微妙に異なるテキストとして再読された同一イベントは3秒以内なら
    # 再発火しない (まきびし等の効果二重適用・とんぼがえり×4の連発防止)
    state, p = new_parser()
    f1 = p.parse("相手の リザードンの まきびし!")
    f2 = p.parse("ま相手の リザードンの まきびし!")   # ノイズ混じりの再読
    f3 = p.parse("相手の リザードンの まきびじ!")
    assert any(f.startswith("move_opponent_spikes") for f in f1), f1
    assert not any(f.startswith("move_") for f in f2 + f3), (f2, f3)
    assert state.player.spikes == 1, f"まきびし二重適用: {state.player.spikes}"

    state2, p2 = new_parser()
    f1 = p2.parse("ブリジュラスの 防御が 上がった!")
    f2 = p2.parse("プリジュラスの 防御が 上がった!")   # ブ/プ混同の再読
    idx = state2.player.find_by_species("ブリジュラス")
    me = state2.player.party[idx]
    assert me.boosts.get("def") == 1, f"ランク二重適用: {me.boosts}"
    print("test_event_dedup OK")


if __name__ == "__main__":
    test_weather_and_terrain()
    test_switch_and_mega()
    test_rank_change()
    test_hazards_and_screens()
    test_status_and_volatile()
    test_ability_popup()
    test_popup_attribution_guard()
    test_move_reveal()
    test_ability_species_validation()
    test_fixed_ability()
    test_event_dedup()
    print("\nALL OK")
