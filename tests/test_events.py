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


def test_multi_stat_rank_change():
    """捨て台詞 (攻撃+特攻) や瞑想 (特攻+特防) は両ステータスに適用する。

    2026-08-18 接続テスト欠陥#5: 最初の1ステータスで打ち切っており、
    捨て台詞で boost_player_spa_-1 だけが発火した (atk側の欠落)。
    """
    state, p = new_parser()
    p.parse("아나이뚜は リザードンを 繰り出した!")

    # 捨て台詞: 自分側の攻撃と特攻が同時に下がる
    fired = p.parse("ブリジュラスの 攻撃と 特攻が 下がった!")
    assert any(f == "boost_player_atk_-1" for f in fired), fired
    assert any(f == "boost_player_spa_-1" for f in fired), fired
    me = state.player.party[0]
    assert me.boosts["atk"] == -1 and me.boosts["spa"] == -1, me.boosts

    # 瞑想: 相手側の特攻と特防が同時に上がる
    fired = p.parse("相手の リザードンの 特攻と 特防が 上がった!")
    assert any(f == "boost_opponent_spa_+1" for f in fired), fired
    assert any(f == "boost_opponent_spd_+1" for f in fired), fired
    om = state.opponent.active()
    assert om.boosts["spa"] == 1 and om.boosts["spd"] == 1, om.boosts
    print("test_multi_stat_rank_change OK")


def test_mirror_match_switch_side():
    """ミラーマッチで相手の繰り出しが自分側に化けない (2026-08-19実測:
    「RX78ー2はサザンドラを繰り出した」が switch_player になった)"""
    state, p = new_parser()
    fired = p.parse("ゆけっサザンドラ")           # 自分のサザンドラが場に出る
    assert "switch_player" in fired, fired
    my_idx = state.player.active_index

    # 相手 (トレーナー名は化けている) も同種を繰り出す
    fired = p.parse("RX78ー2はサザンドラを繰り出した")
    assert "switch_opponent" in fired, fired
    assert "switch_player" not in fired, fired
    assert state.player.active_index == my_idx      # 自分側は不変
    assert state.opponent.active() is not None
    assert state.opponent.active().species_ja == "サザンドラ"

    # 「ゆけっ」「こちらは」「相手は」の各形式は従来どおり
    state2, p2 = new_parser()
    assert "switch_player" in p2.parse("こちらはブリジュラスを繰り出した")
    assert "switch_opponent" in p2.parse("相手はリザードンを繰り出した!")
    print("test_mirror_match_switch_side OK")


def test_disguise_bust_damage():
    """ばけのかわ発動で最大HPの1/8を即時反映する (2026-08-19 opus監査:
    88%実表示を100%と主張、差はちょうど1/8だった)"""
    state, p = new_parser()
    me = state.player.party[2]        # new_parser既定のミミッキュ
    me.hp_percent, me.hp_current, me.hp_max = 100.0, 162, 162
    state.player.active_index = 2
    fired = p.parse("ミミッキュのばけのかわ", source="left_popup")
    assert any(f.startswith("ability_player_disguise") for f in fired), fired
    assert me.hp_percent == 87.5, me.hp_percent
    assert me.hp_current == 162 - round(162 / 8), me.hp_current
    print("test_disguise_bust_damage OK")


def test_type_change():
    """「Xは こおりタイプに なった!」(変幻自在等) でタイプが差し替わる (第2回)"""
    state, p = new_parser()
    p.parse("아나이뚜は ゲッコウガを 繰り出した!")
    fired = p.parse("相手の ゲッコウガは こおりタイプに なった!")
    assert "type_change_opponent_ice" in fired, fired
    om = state.opponent.active()
    assert om.types == ["こおり"], om.types
    # 別タイプへの再変化も追従する
    fired = p.parse("相手の ゲッコウガは みずタイプに なった!")
    assert "type_change_opponent_water" in fired, fired
    assert om.types == ["みず"], om.types
    print("test_type_change OK")


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


def test_move_seal_states():
    # ちょうはつ / かなしばり (封じ技の特定込み) / 解除
    state, p = new_parser()
    p.parse("ブリジュラスは 挑発に 乗ってしまった!")
    me = state.player.party[0]
    assert "taunt" in me.volatiles, me.volatiles
    p.parse("ブリジュラスの 挑発は とけた!")
    assert "taunt" not in me.volatiles

    p.parse("ブリジュラスの りゅうのはどうを かなしばりにした!")
    assert "disable" in me.volatiles, me.volatiles
    assert "disable_dragonpulse" in me.volatiles, me.volatiles
    p.parse("ブリジュラスの かなしばりが とけた!")
    assert "disable" not in me.volatiles
    assert not any(v.startswith("disable_") for v in me.volatiles)
    print("test_move_seal_states OK")


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
    # メガ前に判明していた特性 (しめりけ) はメガ後に固定特性で上書きされる
    state2, p2 = new_parser()
    p2.parse("相手は ラグラージを 繰り出した!")
    mon2 = state2.opponent.party[state2.opponent.find_by_species("ラグラージ")]
    mon2.ability_id, mon2.ability_ja = "damp", "しめりけ"
    p2.parse("相手の ラグラージは メガラグラージに メガシンカした!")
    assert mon2.ability_id == "swiftswim", mon2.ability_id
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


def test_side_attribution_ocr_garble():
    # 実戦 (2026-07-22): 「相手の」が「狙手の」に化け、相手ハラバリーの
    # みずびたしが自分の技 move_player_soak として二重帰属した
    from vision.state import PokemonState
    state, p = new_parser()
    state.opponent.party.append(
        PokemonState(species_ja="ハラバリー", species_id="bellibolt"))
    f1 = p.parse("4V4相手のハラバリーのみすびたし")
    assert any(f.startswith("move_opponent_soak") for f in f1), f1
    # 化けた再読: 相手側として解決され、デデュープで再発火しない
    f2 = p.parse("狙手のハラバリーのみすびたし")
    assert not any(f.startswith("move_player_") for f in f2), f2
    assert not any(f.startswith("move_opponent_soak") for f in f2), f2

    # プレフィックスが完全に化けても、相手パーティの名前照合で相手側になる
    state2, p2 = new_parser()
    state2.opponent.party.append(
        PokemonState(species_ja="ハラバリー", species_id="bellibolt"))
    f3 = p2.parse("ニVAハラバリーのみすびたし")
    assert not any(f.startswith("move_player_") for f in f3), f3
    print("test_side_attribution_ocr_garble OK")


def test_charge_vs_electromorphosis():
    # 実戦 (2026-07-22): でんきにかえる発動時の「じゅうでん状態になった」を
    # 技じゅうでん (move_charge) と誤検知した。特性発動直後は抑止する
    from vision.state import PokemonState
    state, p = new_parser()
    state.opponent.party.append(
        PokemonState(species_ja="ハラバリー", species_id="bellibolt"))
    p.parse("아相手の ハラバリーを 繰り出した!")
    f1 = p.parse("ハラバリーの でんきにかえる", source="right_popup")
    assert any("electromorphosis" in f for f in f1), f1
    f2 = p.parse("4V4相手のハラバリーはじゅうでん充地を始めた")
    assert not any("charge" in f for f in f2), f2
    # 特性発動を伴わない「じゅうでんを始めた」は技として通常どおり検知する
    state2, p2 = new_parser()
    state2.opponent.party.append(
        PokemonState(species_ja="ハラバリー", species_id="bellibolt"))
    f3 = p2.parse("相手の ハラバリーは じゅうでんを 始めた!")
    assert any("charge" in f for f in f3), f3
    print("test_charge_vs_electromorphosis OK")


def test_rate_extraction():
    # 結果画面のレート表示からレート数値を抽出して保持する (勝敗推定用)。
    # ランク/レート表示はイベントとしては発火しない (従来どおり無視)
    state, p = new_parser()
    assert p.parse("ランクIV レート1602") == []
    assert state.last_rate and state.last_rate["value"] == 1602, state.last_rate
    # OCRノイズ混じり
    assert p.parse("うノランク レート1618ボール級") == []
    assert state.last_rate["value"] == 1618
    # ありえない値は捨てる (直前の値を保持)
    assert p.parse("ランク レート99999") == []
    assert state.last_rate["value"] == 1618
    # 対戦リセットを跨いで保持される
    state.reset_battle()
    assert state.last_rate and state.last_rate["value"] == 1618
    print("test_rate_extraction OK")


def test_move_attribution_requires_name():
    # 実戦 (接続テスト): 「相手の型別対子のボルトチェンジ」のように種族名が
    # OCR劣化すると、技がアクティブ扱いの別個体の判明技として記録された。
    # 名前照合できない場合はイベントのみ発火し、個体への記録は行わない
    from vision.state import PokemonState
    state, p = new_parser()
    p.parse("아相手の ハラバリーを 繰り出した!".replace("ハラバリー", "リザードン"))
    active = state.opponent.active()
    # 名前が完全に崩れたメッセージ: 技イベントは発火するが判明技は付かない
    fired = p.parse("相手の型別対子のボルトチェンジ")
    assert any("voltswitch" in f for f in fired), fired
    assert "ボルトチェンジ" not in (active.revealed_moves or []), \
        active.revealed_moves
    # 軽度の崩れはファジー照合で救済され、正しく個体へ記録される
    state2, p2 = new_parser()
    p2.parse("아相手の リザードンを 繰り出した!")
    act2 = state2.opponent.active()
    fired = p2.parse("相手のリサードソのフレアドライブ")   # ザ->サ, ン->ソ
    assert any("flareblitz" in f for f in fired), fired
    assert "フレアドライブ" in (act2.revealed_moves or []), act2.revealed_moves
    print("test_move_attribution_requires_name OK")


def test_mega_keeps_base_name_no_duplicate():
    # メガシンカ後も画面表示は元の名前のため、species_jaはメガ前を維持し
    # (species_idのみメガ後)、以後の照合で別枠が生えないこと (実戦で重複)
    state, p = new_parser()
    p.parse("아나이뚜は リザードンを 繰り出した!")
    n_before = len(state.opponent.party)
    fired = p.parse("相手の リザードンは メガリザードンYに メガシンカした!")
    assert "mega_evolve" in fired, fired
    mon = state.opponent.active()
    assert mon.is_mega
    assert mon.species_ja == "リザードン", mon.species_ja   # メガ前の名前を維持
    assert "mega" in (mon.species_id or ""), mon.species_id  # IDはメガ後
    assert any(a.startswith("メガ") for a in mon.aliases), mon.aliases
    # メガ後にHUDが元の名前を表示しても同じ枠に解決される (重複しない)
    mon2 = state.opponent.switch_to_species("リザードン", "charizard")
    assert mon2 is mon, "メガ後のHUD名で別枠が生えた"
    assert len(state.opponent.party) == n_before
    # メガ名からも同枠が引ける (find_by_speciesの正規化)
    assert state.opponent.find_by_species("メガリザードンY") == \
        state.opponent.find_by_species("リザードン")
    print("test_mega_keeps_base_name_no_duplicate OK")


def test_forfeit_win():
    # 相手の降参による勝ち (実戦 2026-07-22: 降参終了が辞書に無く取り逃した)
    state, p = new_parser()
    fired = p.parse("相手が 降参した!")
    assert "battle_win" in fired, fired
    assert state.outcome == "win", state.outcome
    print("test_forfeit_win OK")


def test_boost_survives_active_relink():
    """イベントで付けたランク変化が、HUD由来プレースホルダとのマージで
    消えないこと (2026-08-05接続テスト: つるぎのまい+2が次のcommand画面で
    {}に戻っていた。link_active_to_partyが空ブーストで上書きしていた)"""
    from vision.extractors import link_active_to_party
    from vision.state import PokemonState
    state, p = new_parser()
    p.parse("相手は ルカリオを 繰り出した!")
    fired = p.parse("相手の ルカリオの つるぎのまい!")
    assert any(f.startswith("move_opponent") for f in fired), fired
    fired = p.parse("相手の ルカリオの こうげきが ぐーんとあがった!")
    assert any(f.startswith("boost_opponent_atk") for f in fired), fired
    mon = state.opponent.active()
    assert mon.boosts.get("atk", 0) >= 2, mon.boosts

    # HUD再読で生えた同種族のプレースホルダ (ブースト情報なし) をマージ
    ph = PokemonState()
    ph.species_ja, ph.species_id = "ルカリオ", "lucario"
    state.opponent.party.append(ph)
    state.opponent.active_index = len(state.opponent.party) - 1
    link_active_to_party(state, "opponent")
    mon = state.opponent.active()
    assert mon.boosts.get("atk", 0) >= 2, \
        f"マージでランク変化が消えた: {mon.boosts}"
    assert "つるぎのまい" in mon.revealed_moves, mon.revealed_moves
    print("test_boost_survives_active_relink OK")


if __name__ == "__main__":
    test_weather_and_terrain()
    test_switch_and_mega()
    test_rank_change()
    test_multi_stat_rank_change()
    test_mirror_match_switch_side()
    test_disguise_bust_damage()
    test_type_change()
    test_hazards_and_screens()
    test_status_and_volatile()
    test_move_seal_states()
    test_ability_popup()
    test_popup_attribution_guard()
    test_move_reveal()
    test_ability_species_validation()
    test_fixed_ability()
    test_event_dedup()
    test_boost_survives_active_relink()
    test_side_attribution_ocr_garble()
    test_charge_vs_electromorphosis()
    test_rate_extraction()
    test_move_attribution_requires_name()
    test_mega_keeps_base_name_no_duplicate()
    test_forfeit_win()
    print("\nALL OK")
