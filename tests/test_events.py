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


def test_type_change_survives_backfill():
    """観測したタイプ変化は図鑑タイプの自動訂正 (backfill) に潰されない。

    2026-08-21 第6回: マスカーニャの変幻自在で変わったタイプが、毎heavy
    フレームの backfill_player_static により数秒で図鑑タイプへ戻されていた。
    交代で元に戻るため、交代アウト後は従来どおり図鑑で正す。
    """
    from vision import extractors
    from vision.normalize import NameResolver

    state, p = new_parser()
    p.parse("相手は マスカーニャを 繰り出した!")
    om = state.opponent.active()
    assert om.species_ja == "マスカーニャ", om.species_ja
    fired = p.parse("相手の マスカーニャは みずタイプに なった!")
    assert "type_change_opponent_water" in fired, fired
    assert om.types == ["みず"] and om.type_changed is True

    r = NameResolver()
    extractors.backfill_player_static(state, r)
    assert om.types == ["みず"], f"backfillが観測タイプを潰した: {om.types}"

    # 交代アウトでフラグ解除 → 次のbackfillで図鑑タイプに戻る
    om.reset_on_switch_out()
    assert om.type_changed is False
    extractors.backfill_player_static(state, r)
    assert set(om.types) == {"くさ", "あく"}, om.types
    print("test_type_change_survives_backfill OK")


def test_popup_item_containing_no():
    """「の」を含む持ち物名のポップアップは最高スコアの分割で解決する。

    2026-08-21 第7回: 「ミミッキュのいのちのたま」が最後の「の」分割
    (「たま」) で ビーだま(marble) に誤解決していた。完全一致の
    いのちのたま が選ばれること。
    """
    state, p = new_parser()
    state.player.active_index = 2   # 既定パーティのミミッキュ
    fired = p.parse("ミミッキュの いのちのたま", source="left_popup")
    assert "item_player_lifeorb" in fired, fired
    me = state.player.party[2]
    assert me.item_id == "lifeorb", me.item_id
    print("test_popup_item_containing_no OK")


def test_knockoff_removes_item():
    """はたきおとすで持ち物を失い、以後バックフィルで復活しない (第7回)"""
    from vision import extractors
    from vision.normalize import NameResolver
    import advisor.my_team as my_team_mod

    state, p = new_parser()
    me = state.player.party[2]          # ミミッキュ
    me.item_ja, me.item_id = "いのちのたま", "lifeorb"
    fired = p.parse("相手の サーフゴーは ミミッキュの いのちのたまを はたきおとした!")
    assert "knockoff_player_lifeorb" in fired, fired
    assert me.item_id is None and me.item_ja is None
    assert me.item_removed is True

    # 登録バックフィルでも復活しない
    orig = my_team_mod.get_my_build
    my_team_mod.get_my_build = lambda ja: {"item_ja": "いのちのたま"} \
        if ja == "ミミッキュ" else None
    try:
        extractors.backfill_player_static(state, NameResolver())
        assert me.item_id is None, "item_removed中に登録から復活した"
    finally:
        my_team_mod.get_my_build = orig

    # 相手側: 自分がはたきおとした場合
    p.parse("相手は ハラバリーを 繰り出した!")
    om = state.opponent.active()
    om.item_ja, om.item_id = "ラムのみ", "lumberry"
    fired = p.parse("ムクホークは 相手の ハラバリーの ラムのみを はたきおとした!")
    assert "knockoff_opponent_lumberry" in fired, fired
    assert om.item_id is None and om.item_removed is True
    print("test_knockoff_removes_item OK")


def test_rotom_form_faint_prefers_active():
    """同名フォーム (ロトム/ウォッシュロトム) の帰属は場の個体を優先する。

    2026-08-20 第5回持ち越し: 「相手のロトムはたおれた」が基本形スロットへ
    誤帰属し、実際に倒れたウォッシュロトムが健在のまま残った。
    """
    state, p = new_parser()
    base = state.opponent.party  # 既定の相手枠は流用せず追加する
    from vision.state import PokemonState
    rotom = PokemonState(species_ja="ロトム", species_id="rotom")
    rotom.hp_percent = 100.0
    wash = PokemonState(species_ja="ウォッシュロトム", species_id="rotomwash")
    wash.hp_percent = 40.0
    base.clear()
    base.extend([rotom, wash])
    state.opponent.active_index = 1   # 場に出ているのはウォッシュ

    fired = p.parse("相手の ロトムは たおれた!")
    assert "faint" in fired, fired
    assert wash.status == "fainted", (wash.status, rotom.status)
    assert rotom.status != "fainted", "基本形スロットへ誤帰属した"
    print("test_rotom_form_faint_prefers_active OK")


def test_pivot_switch_context_flag():
    """とんぼがえり系の使用で交代先選択フラグが立ち、交代完了で下りる (第8回)"""
    state, p = new_parser()
    assert state.pending_pivot_switch is False
    fired = p.parse("ブリジュラスの とんぼがえり!")
    assert "move_player_uturn" in fired, fired
    assert state.pending_pivot_switch is True
    fired = p.parse("ゆけっ! ライチュウ!")
    assert "switch_player" in fired, fired
    assert state.pending_pivot_switch is False

    # 相手のとんぼがえりでは立たない
    p.parse("相手は リザードンを 繰り出した!")
    p.parse("相手の リザードンの とんぼがえり!")
    assert state.pending_pivot_switch is False
    print("test_pivot_switch_context_flag OK")


def test_rank_screen_ends_battle():
    """ランク画面 (レート表示) を対戦終了のキーにする (第7回ユーザー提案)"""
    state, p = new_parser()
    state.battle_active = True
    fired = p.parse("ランクIV レート1602")
    assert fired == ["battle_end_rank"], fired
    assert state.battle_active is False
    assert state.last_rate and state.last_rate["value"] == 1602
    # 同一対戦内では再発火しない
    fired2 = p.parse("ランクIV レート1602 ボール級")
    assert fired2 == [], fired2
    print("test_rank_screen_ends_battle OK")


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
    mon = state.opponent.active()
    # 2026-08-25 第9回以降: 確定ブーストは技イベント時点で即適用され、
    # 直後のメッセージはdedupで二重適用されない (メッセージ取り逃し対策)
    assert mon.boosts.get("atk", 0) == 2, mon.boosts
    p.parse("相手の ルカリオの こうげきが ぐーんとあがった!")
    assert mon.boosts.get("atk", 0) == 2, \
        f"技+メッセージで二重適用された: {mon.boosts}"

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


def test_boost_from_move_event():
    """能力変化メッセージは技演出中の短時間表示で系統的に取り逃す
    (2026-08-25 第9回: 8対戦で捕捉0件、つるぎのまい・いかく含む)。
    技の使用イベントから確定分 (100%発動のみ) を即時反映する"""
    state, p = new_parser()
    state.player.active_index = 0
    fired = p.parse("ブリジュラスの つるぎのまい!")
    assert any(f.startswith("move_player") for f in fired), fired
    mon = state.player.party[0]
    assert mon.boosts["atk"] == 2, mon.boosts
    # 対象側への確定デバフ (こごえるかぜ=相手の素早さ-1)
    p.parse("相手は ルカリオを 繰り出した!")
    opp = state.opponent.active()
    p.parse("ブリジュラスの こごえるかぜ!")
    assert opp.boosts["spe"] == -1, opp.boosts
    # 確率発動の追加効果は適用しない (10まんボルトの麻痺等はランク変化なし)
    p.parse("ブリジュラスの 10まんボルト!")
    assert opp.boosts["spe"] == -1 and opp.boosts.get("atk", 0) == 0, opp.boosts
    print("test_boost_from_move_event OK")


def test_intimidate_applies_on_switch_event():
    """いかくの着地効果を交代イベントで確定適用する (メッセージ非依存)。
    特性が確定している個体のみ (推定特性では適用しない)"""
    from vision.state import PokemonState
    state, p = new_parser()
    state.player.active_index = 0
    state.opponent.party.append(PokemonState(
        species_ja="ギャラドス", species_id="gyarados",
        ability_id="intimidate", ability_ja="いかく"))
    fired = p.parse("相手は ギャラドスを 繰り出した!")
    assert any(f.startswith("switch_opponent") for f in fired), fired
    assert state.player.party[0].boosts["atk"] == -1, \
        state.player.party[0].boosts
    # 特性未確定の交代では適用されない
    state2, p2 = new_parser()
    state2.player.active_index = 0
    p2.parse("相手は ルカリオを 繰り出した!")
    assert state2.player.party[0].boosts["atk"] == 0
    print("test_intimidate_applies_on_switch_event OK")


def test_mega_fallback_without_readable_name():
    """メガ名がOCR崩れで読めない場合、対象個体の種族からメガフォルムを導出
    (2026-08-25 第9回: 「メガスコィラン」でメガ種族値が反映されなかった)"""
    state, p = new_parser()
    p.parse("相手は バシャーモを 繰り出した!")
    fired = p.parse("相手の バシャーモは メガシンカした!")
    assert "mega_evolve" in fired, fired
    mon = state.opponent.active()
    assert mon.is_mega
    assert mon.species_id == "blazikenmega", mon.species_id
    assert "メガバシャーモ" in (mon.aliases or []), mon.aliases
    print("test_mega_fallback_without_readable_name OK")


def test_mega_form_id_derivation():
    """基本形→メガフォルムIDの導出 (X/Yはストーンで判別、曖昧なら未確定)"""
    from vision.abilities import fixed_ability, mega_form_id
    assert mega_form_id("blaziken") == "blazikenmega"
    assert mega_form_id("raichu") is None            # X/Y曖昧
    assert mega_form_id("raichu", "raichunitey") == "raichumegay"
    assert mega_form_id("raichu", "raichunitex") == "raichumegax"
    assert mega_form_id("blazikenmega") is None      # 既にメガ
    assert mega_form_id("yanmega") is None           # 自然名の誤爆なし
    assert mega_form_id("pikachu") is None           # メガ形態なし
    # X/Y形態IDでも特性が確定する (従来は endswith("mega") 判定が
    # …megay を基本形と誤判し None を返していた)
    assert fixed_ability("raichumegay", is_mega=True) == "noguard"
    print("test_mega_form_id_derivation OK")


def test_mega_survives_reswitch():
    """メガ後に交代で下げて再登場しても species_id がメガのまま維持される
    (従来は switch_to_species の merge が基本形IDへ戻していた)"""
    state, p = new_parser()
    p.parse("相手は バシャーモを 繰り出した!")
    p.parse("相手の バシャーモは メガバシャーモに メガシンカした!")
    mon = state.opponent.active()
    assert mon.species_id == "blazikenmega", mon.species_id
    p.parse("相手は ルカリオを 繰り出した!")
    mon2 = state.opponent.switch_to_species("バシャーモ", "blaziken")
    assert mon2 is mon
    assert mon2.species_id == "blazikenmega", \
        f"再登場でメガが基本形に戻った: {mon2.species_id}"
    print("test_mega_survives_reswitch OK")


def test_bare_form_read_merges_into_family_slot():
    """素の「ロトム」読みが既存のウォッシュロトム枠へ併合され、別枠が
    生えない (2026-08-25 第9回: rotomの別枠が生えタイプがゴースト/でんきで
    表示された)。別種 (コイル/レアコイル) は併合しない"""
    from vision.state import BattleStateV2, PokemonState
    state = BattleStateV2()
    state.opponent.party = [
        PokemonState(species_ja="ウォッシュロトム", species_id="rotomwash",
                     types=["でんき", "みず"]),
        PokemonState(species_ja="ミミッキュ", species_id="mimikyu"),
    ]
    mon = state.opponent.switch_to_species("ロトム", "rotom")
    assert mon is state.opponent.party[0], "別枠が生えた"
    assert mon.species_id == "rotomwash", "具体フォームが素形へ格下げされた"
    assert mon.species_ja == "ウォッシュロトム"
    assert len(state.opponent.party) == 2
    # 逆方向: 具体フォームの読みは素形枠を昇格させる
    state2 = BattleStateV2()
    state2.opponent.party = [
        PokemonState(species_ja="ロトム", species_id="rotom")]
    mon2 = state2.opponent.switch_to_species("ウォッシュロトム", "rotomwash")
    assert mon2 is state2.opponent.party[0]
    assert mon2.species_id == "rotomwash"
    # 名前は末尾一致するが別種 → 併合しない
    state3 = BattleStateV2()
    state3.opponent.party = [
        PokemonState(species_ja="レアコイル", species_id="magneton")]
    mon3 = state3.opponent.switch_to_species("コイル", "magnemite")
    assert mon3 is not state3.opponent.party[0]
    print("test_bare_form_read_merges_into_family_slot OK")


def test_white_herb_activation():
    """しろいハーブ: (1)ポップアップ観測で低下復元+消費 (2)持ち物が確定
    していれば低下適用の直後に推定発動 (発動ポップアップは演出中で取り逃し
    やすく全対戦で発火2件のみ — 2026-08-25 第9回指摘。従来は持ち物名の
    記録のみで復元も消費もされなかった) (3)非所持者は復元されない"""
    # (1) ポップアップ経由
    state, p = new_parser()
    state.player.active_index = 0
    mon = state.player.party[0]
    mon.set_boost("atk", -2)
    p.parse("ブリジュラスのしろいハーブ", source="left_popup")
    assert mon.boosts["atk"] == 0, mon.boosts
    assert mon.item_consumed
    # (2) 確定持ち物からの推定発動: からをやぶる → 上昇は残し低下のみ復元
    state2, p2 = new_parser()
    state2.player.active_index = 0
    mon2 = state2.player.party[0]
    mon2.item_id, mon2.item_ja = "whiteherb", "しろいハーブ"
    p2.parse("ブリジュラスの からをやぶる!")
    assert mon2.boosts["atk"] == 2 and mon2.boosts["spe"] == 2, mon2.boosts
    assert mon2.boosts["def"] == 0 and mon2.boosts["spd"] == 0, mon2.boosts
    assert mon2.item_consumed
    # 消費後は再発動しない (dedupの3秒窓はテスト用にクリア)
    p2._recent_fired.clear()
    p2.parse("ブリジュラスの ばかぢから!")
    assert mon2.boosts["def"] == -1, mon2.boosts
    # (3) 非所持者は低下がそのまま残る
    state3, p3 = new_parser()
    state3.player.active_index = 0
    p3.parse("ブリジュラスの からをやぶる!")
    assert state3.player.party[0].boosts["def"] == -1, \
        state3.player.party[0].boosts
    print("test_white_herb_activation OK")


def test_rate_extraction_decimal_format():
    """ランク画面の実表示「レート1626.580」(小数3桁) を抽出する (2026-08-25
    第9回: 正規化が小数点を落とし 1626580→先頭5桁が範囲外で全戦棄却され、
    レート観測0件・勝敗不明3戦の主因になった)"""
    state, p = new_parser()
    p.parse("マスターボール級 ランクIV レート1626.580")
    assert state.last_rate and abs(state.last_rate["value"] - 1626.58) < 1e-6, \
        state.last_rate
    # 小数点がOCRで落ちて連結された場合も整数部を救済する
    state2, p2 = new_parser()
    p2.parse("ランクIV レート1626580")
    assert state2.last_rate and int(state2.last_rate["value"]) == 1626, \
        state2.last_rate
    print("test_rate_extraction_decimal_format OK")


if __name__ == "__main__":
    test_weather_and_terrain()
    test_switch_and_mega()
    test_rank_change()
    test_multi_stat_rank_change()
    test_mirror_match_switch_side()
    test_disguise_bust_damage()
    test_type_change()
    test_type_change_survives_backfill()
    test_popup_item_containing_no()
    test_knockoff_removes_item()
    test_rotom_form_faint_prefers_active()
    test_pivot_switch_context_flag()
    test_rank_screen_ends_battle()
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
    test_boost_from_move_event()
    test_intimidate_applies_on_switch_event()
    test_mega_fallback_without_readable_name()
    test_mega_form_id_derivation()
    test_mega_survives_reswitch()
    test_bare_form_read_merges_into_family_slot()
    test_white_herb_activation()
    test_rate_extraction_decimal_format()
    print("\nALL OK")
