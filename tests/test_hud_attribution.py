"""相手HUDのHP帰属ガードの検証 (監査2026-07-26の修正)。

実戦事故: 交代を見逃した状態でHUD名が照合できないフレームのHP%が、
古いactive個体 (ドリュウズ) に書き込まれた (実際はゲッコウガの23%)。
- 名前不一致を検知したフレーム → HPを書かない
- 名前が読めないフレーム → 大きな変化 (>15pt) は書かない、小変化は許容

使い方: scripts/run_test.sh test_hud_attribution
"""
import numpy as np

from vision import ocr, zones
from vision.state import BattleStateV2, PokemonState


def _run_hud(state, opp_name, opp_hp_text):
    """OCRをモックして extract_battle_hud を1回実行する"""
    from vision import extractors
    from vision.normalize import NameResolver
    orig_read, orig_bar = ocr.read_zone_text, ocr.hp_bar_ratio

    def fake_read(img, zone, **kw):
        if zone is zones.BATTLE["opp_name"]:
            return opp_name
        if zone is zones.BATTLE["opp_hp_text"]:
            return opp_hp_text
        return ""

    ocr.read_zone_text = fake_read
    ocr.hp_bar_ratio = lambda img: None
    try:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        extractors.extract_battle_hud(img, state, NameResolver())
    finally:
        ocr.read_zone_text = orig_read
        ocr.hp_bar_ratio = orig_bar


def _state_with_opp(ja, sid, hp):
    st = BattleStateV2()
    mon = PokemonState(species_ja=ja, species_id=sid, display_name=ja)
    mon.hp_percent = hp
    st.opponent.party.append(mon)
    st.opponent.active_index = 0
    return st, mon


def test_mismatched_name_blocks_hp():
    st, mon = _state_with_opp("ドリュウズ", "excadrill", 33.0)
    # 読めたが誰とも照合できない名前 + 23% (2回で定着を試みる)
    for _ in range(2):
        _run_hud(st, "ホゲホゲ", "23%")
    assert mon.hp_percent == 33.0, mon.hp_percent
    print("test_mismatched_name_blocks_hp OK")


def test_unread_name_blocks_large_change_only():
    st, mon = _state_with_opp("ドリュウズ", "excadrill", 33.0)
    for _ in range(2):
        _run_hud(st, "", "90%")     # 名前未読 + 大変化 → 見送り
        mon._hp_stable_since = 0.0   # 600ms安定条件を満たしたことにする
    assert mon.hp_percent == 33.0, mon.hp_percent
    for _ in range(3):
        _run_hud(st, "", "25%")     # 名前未読 + 小変化 (8pt) → 許容
        mon._hp_stable_since = 0.0
    assert mon.hp_percent == 25.0, mon.hp_percent
    print("test_unread_name_blocks_large_change_only OK")


def test_unread_name_blocks_small_heal():
    """名前未確認の小さな「回復」は誤帰属とみなして書かない (欠陥#7)。

    2026-08-18 opus視覚監査: HUDはフラエッテ30%なのに、交代見逃しで
    activeに残っていたオーロング23%へ +7% の回復として記録された。
    減少方向の小変化 (削りダメージ) は従来どおり許容する。
    """
    st, mon = _state_with_opp("オーロンゲ", "grimmsnarl", 23.0)
    for _ in range(3):
        _run_hud(st, "", "30%")     # 名前未読 + 小さな回復 → 見送り
        mon._hp_stable_since = 0.0
    assert mon.hp_percent == 23.0, mon.hp_percent
    print("test_unread_name_blocks_small_heal OK")


def test_roster_prior_resolves_ocr_garble():
    """判明済みロスターを事前分布に、OCR揺れのHUD名を低閾値で解決する (欠陥#10)。

    実測: オーロンゲ→「オーロング」(類似度0.8) が通常閾値0.85で解決できず、
    満枠のため ensure_active が limbo 行き → active=None のままHPが幽霊へ
    流れ、助言が相手不明で劣化していた。
    """
    st = BattleStateV2()
    for ja, sid in (("ガブリアス", "garchomp"), ("カイロス", "pinsir"),
                    ("オーロンゲ", "grimmsnarl"), ("ギルガルド", "aegislash"),
                    ("アシレーヌ", "primarina"), ("ライチュウ", "raichu")):
        st.opponent.party.append(PokemonState(species_ja=ja, species_id=sid))
    assert st.opponent.active_index is None
    for _ in range(3):
        _run_hud(st, "オーロング", "100%")
        if st.opponent.active_index is not None:
            st.opponent.active()._hp_stable_since = 0.0
    assert st.opponent.active_index is not None, "limboに吸われた (欠陥#10再発)"
    active = st.opponent.active()
    assert active.species_ja == "オーロンゲ", active.species_ja
    assert active.hp_percent == 100.0, active.hp_percent

    # ロスターに居ない種族へは低閾値で飛びつかない (事前分布の意味)
    st2, mon2 = _state_with_opp("ドリュウズ", "excadrill", 50.0)
    for _ in range(2):
        _run_hud(st2, "オーロング", "77%")
    assert all(p.species_ja != "オーロンゲ" for p in st2.opponent.party), \
        [p.species_ja for p in st2.opponent.party]
    print("test_roster_prior_resolves_ocr_garble OK")


def test_new_species_needs_two_reads():
    """ロスターに無い初登場種は連続2回の一致で受け入れる (第2回: 幽霊スロット)。

    1回の化けHUD読みが実在しないオーロンゲ9.8%の枠を作り、activeを
    乗っ取って以後の相手状態を汚染した。
    """
    st = BattleStateV2()
    for ja, sid in (("ジジーロン", "drampa"), ("ハラバリー", "bellibolt")):
        st.opponent.party.append(PokemonState(species_ja=ja, species_id=sid))
    st.opponent.active_index = 0

    _run_hud(st, "オーロンゲ", "10%")   # 1回目: まだ受け入れない
    assert all(p.species_ja != "オーロンゲ" for p in st.opponent.party), \
        [p.species_ja for p in st.opponent.party]

    _run_hud(st, "ジジーロン", "100%")  # 別種が読めたら保留はリセット
    _run_hud(st, "オーロンゲ", "10%")   # 仕切り直しの1回目
    assert all(p.species_ja != "オーロンゲ" for p in st.opponent.party)

    _run_hud(st, "オーロンゲ", "10%")   # 連続2回目: 受け入れ (本物の初登場)
    assert any(p.species_ja == "オーロンゲ" for p in st.opponent.party), \
        [p.species_ja for p in st.opponent.party]
    print("test_new_species_needs_two_reads OK")


def test_missed_switch_marks_prev_uncertain():
    """switchイベント無しでHUD名からactiveが替わったら、前のactiveを
    HP不明としてマークする (第2回: 取り逃したひんしが100%のまま残った)"""
    from vision import extractors
    st = BattleStateV2()
    prev = PokemonState(species_ja="サザンドラ", species_id="hydreigon")
    prev.hp_percent = 100.0
    nxt = PokemonState(species_ja="キラフロル", species_id="glimmora")
    st.player.party.extend([prev, nxt])
    st.player.active_index = 0

    orig_read, orig_bar = ocr.read_zone_text, ocr.hp_bar_ratio

    def fake_read(img, zone, **kw):
        if zone is zones.BATTLE["my_name"]:
            return "キラフロル"
        return ""

    ocr.read_zone_text = fake_read
    ocr.hp_bar_ratio = lambda img: None
    try:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        from vision.normalize import NameResolver
        extractors.extract_battle_hud(img, st, NameResolver())
    finally:
        ocr.read_zone_text = orig_read
        ocr.hp_bar_ratio = orig_bar

    assert st.player.active().species_ja == "キラフロル"
    assert prev.hp_uncertain is True, "見逃し交代の前activeが不明化されていない"
    print("test_missed_switch_marks_prev_uncertain OK")


def test_opponent_zero_read_needs_corroboration():
    """生存中の相手の突然の0-1%読みは、連続2回まで書かない (2026-08-19:
    演出中の空バー誤読で生存ドリュウズが偽ひんし化した)。
    タスキで1%残った個体はひんし扱いにしない。"""
    st, mon = _state_with_opp("ドリュウズ", "excadrill", 100.0)
    _run_hud(st, "ドリュウズ", "0%")     # 1回目: 見送り
    assert mon.hp_percent == 100.0 and mon.status != "fainted", \
        (mon.hp_percent, mon.status)
    _run_hud(st, "ドリュウズ", "0%")     # 連続2回目: ひんし確定
    assert mon.hp_percent == 0.0 and mon.status == "fainted", \
        (mon.hp_percent, mon.status)

    # きあいのタスキの1%: 2連続で受理するがひんしにはしない
    st2, mon2 = _state_with_opp("サザンドラ", "hydreigon", 100.0)
    _run_hud(st2, "サザンドラ", "1%")
    _run_hud(st2, "サザンドラ", "1%")
    assert mon2.hp_percent == 1.0 and mon2.status != "fainted", \
        (mon2.hp_percent, mon2.status)
    print("test_opponent_zero_read_needs_corroboration OK")


def test_opponent_zero_read_on_unknown_hp_needs_corroboration():
    """HP未知 (登場直後) の相手への0%読みも裏付けが揃うまで書かない。

    2026-08-20 第5回: 従来のガードは既知HP>5%の個体のみ対象で、
    HP未知の新規スロットは登場演出中の空バー1フレームで0%が素通りし、
    満タンのロトムがひんし扱いになった。
    """
    st = BattleStateV2()
    mon = PokemonState(species_ja="ロトム", species_id="rotom",
                       display_name="ロトム")
    st.opponent.party.append(mon)
    st.opponent.active_index = 0
    assert mon.hp_percent is None
    _run_hud(st, "ロトム", "0%")     # 1回目: 見送り
    assert mon.hp_percent is None and mon.status != "fainted", \
        (mon.hp_percent, mon.status)
    _run_hud(st, "ロトム", "0%")     # 連続2回目: 受理
    assert mon.hp_percent == 0.0 and mon.status == "fainted", \
        (mon.hp_percent, mon.status)
    print("test_opponent_zero_read_on_unknown_hp_needs_corroboration OK")


def test_my_max_hp_adoption_after_consistent_reads():
    """登録から計算した最大HPと食い違っても、バー割合と一致する同じ実測が
    続けば実測を採用する (2026-08-20 第5回: ムクホーク 登録161 vs 実測181
    で全読取が棄却されHPが100%固着 → 交代助言の被ダメ前提が崩れた)。"""
    from vision import extractors
    from vision.normalize import NameResolver

    st = BattleStateV2()
    mon = PokemonState(species_ja="ムクホーク", species_id="staraptor",
                       display_name="ムクホーク")
    mon.hp_percent = 100.0
    st.player.party.append(mon)
    st.player.active_index = 0

    orig_read = ocr.read_zone_text
    orig_bar = ocr.hp_bar_ratio
    orig_expected = extractors._expected_my_max

    def fake_read(img, zone, **kw):
        if zone is zones.BATTLE["my_name"]:
            return "ムクホーク"
        if zone is zones.BATTLE["my_hp_text"]:
            return "161/181"
        return ""

    ocr.read_zone_text = fake_read
    ocr.hp_bar_ratio = lambda img: 161.0 / 181.0
    extractors._expected_my_max = lambda m: 161   # 登録側が古い想定
    try:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        for i in range(extractors.MY_MAX_ADOPT_READS - 1):
            extractors.extract_my_hud(img, st, NameResolver())
            assert mon.hp_max != 181, f"{i + 1}回目で早期採用された"
        extractors.extract_my_hud(img, st, NameResolver())  # 採用回
        assert getattr(mon, "_my_max_adopted", None) == 181
        assert mon.hp_max == 181 and mon.hp_current == 161, \
            (mon.hp_current, mon.hp_max)
        assert abs(mon.hp_percent - 89.0) < 1.0, mon.hp_percent
        assert any("実測を採用" in e.get("text", "")
                   for e in st.events), st.events[-3:]
    finally:
        ocr.read_zone_text = orig_read
        ocr.hp_bar_ratio = orig_bar
        extractors._expected_my_max = orig_expected
    print("test_my_max_hp_adoption_after_consistent_reads OK")


def test_my_max_mismatch_without_bar_agreement_is_discarded():
    """バー割合と食い違う読み ("135/178"→"35/78"型の桁落ち) は採用しない"""
    from vision import extractors
    from vision.normalize import NameResolver

    st = BattleStateV2()
    mon = PokemonState(species_ja="ムクホーク", species_id="staraptor",
                       display_name="ムクホーク")
    mon.hp_percent = 100.0
    st.player.party.append(mon)
    st.player.active_index = 0

    orig_read = ocr.read_zone_text
    orig_bar = ocr.hp_bar_ratio
    orig_expected = extractors._expected_my_max

    def fake_read(img, zone, **kw):
        if zone is zones.BATTLE["my_name"]:
            return "ムクホーク"
        if zone is zones.BATTLE["my_hp_text"]:
            return "35/78"      # 桁落ち誤読 (実際は135/178=76%)
        return ""

    ocr.read_zone_text = fake_read
    ocr.hp_bar_ratio = lambda img: 0.76   # バーは本当の割合を示す
    extractors._expected_my_max = lambda m: 178
    try:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        for _ in range(extractors.MY_MAX_ADOPT_READS + 2):
            extractors.extract_my_hud(img, st, NameResolver())
        assert getattr(mon, "_my_max_adopted", None) is None
        assert mon.hp_max != 78, mon.hp_max
    finally:
        ocr.read_zone_text = orig_read
        ocr.hp_bar_ratio = orig_bar
        extractors._expected_my_max = orig_expected
    print("test_my_max_mismatch_without_bar_agreement_is_discarded OK")


def test_field_check_skips_unresolved_or_foreign_species():
    """場の状況抽出は、種族行が既存パーティに解決できない画面では
    何も書かない (2026-08-20 第5回: 不参加のムクホークが生成され
    161/161が混入。activeへのフォールバックも汚染源だった)。"""
    from vision import extractors
    from vision.normalize import NameResolver

    st = BattleStateV2()
    mon = PokemonState(species_ja="サザンドラ", species_id="hydreigon")
    mon.hp_percent = 100.0
    st.player.party.append(mon)
    st.player.active_index = 0

    orig_lines = ocr.apple_ocr_lines
    orig_text = ocr.apple_ocr_text
    # 画面はムクホーク (パーティ外) の詳細ページという想定
    ocr.apple_ocr_lines = lambda img, scale=1.0: [
        ("効果と場の状態", (0.5, 0.05, 0.8, 0.09)),
        ("ムクホーク", (0.20, 0.22, 0.35, 0.26)),
        ("0/159", (0.20, 0.28, 0.30, 0.32)),
    ]
    ocr.apple_ocr_text = lambda img, scale=1.0, langs=None: "0/159"
    try:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        extractors.extract_field_check(img, st, NameResolver())
        assert len(st.player.party) == 1, [p.species_ja for p in st.player.party]
        assert st.player.party[0].hp_percent == 100.0
    finally:
        ocr.apple_ocr_lines = orig_lines
        ocr.apple_ocr_text = orig_text
    print("test_field_check_skips_unresolved_or_foreign_species OK")


def test_verified_name_updates_hp():
    st, mon = _state_with_opp("ドリュウズ", "excadrill", 33.0)
    for _ in range(3):
        _run_hud(st, "ゲッコウガ", "23%")   # 種族解決 → 正しい枠へ帰属
        st.opponent.active()._hp_stable_since = 0.0
    active = st.opponent.active()
    assert active.species_ja == "ゲッコウガ", active.species_ja
    assert active.hp_percent == 23.0, active.hp_percent
    assert mon.hp_percent == 33.0   # ドリュウズは汚染されない
    print("test_verified_name_updates_hp OK")


def main() -> None:
    test_mismatched_name_blocks_hp()
    test_unread_name_blocks_large_change_only()
    test_unread_name_blocks_small_heal()
    test_roster_prior_resolves_ocr_garble()
    test_new_species_needs_two_reads()
    test_missed_switch_marks_prev_uncertain()
    test_opponent_zero_read_needs_corroboration()
    test_opponent_zero_read_on_unknown_hp_needs_corroboration()
    test_my_max_hp_adoption_after_consistent_reads()
    test_my_max_mismatch_without_bar_agreement_is_discarded()
    test_field_check_skips_unresolved_or_foreign_species()
    test_verified_name_updates_hp()
    print("ALL OK")


if __name__ == "__main__":
    main()
