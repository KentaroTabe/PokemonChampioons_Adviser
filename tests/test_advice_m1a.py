"""操縦提案の精度改善 M1a (docs/ADVICE_ACCURACY_PLAN_1009.md 段 A) のテスト。

段 1 メガ形態の整合 / 段 2 技の固定 (アンコール・こだわり・ちょうはつ・PP・初手限定) / 段 3 部分的に読めた技の登録による補完 /
段 4 据え置き (apply_advice_hysteresis) の誤った保持。実ログ (battle_20261009_*.jsonl) の局面は、ログの簡約状態の値を
ここに写して合成する (ログはコミットしない)。登録の型は合成 (当時の登録は不明なので、現在の config/my_team.json は読まない)。

    python -m tests.test_advice_m1a
"""
from __future__ import annotations

import copy
import os

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import my_team as mt
from advisor.dex import calc_stat
from advisor.engine import (build_mon_view, complete_moves_from_registration, evaluate, first_turn_passed,
                            illegal_move_reason)
from advisor.gimmick import resolve_form
from vision.normalize import NameResolver

resolver = NameResolver()

# 合成の登録 (ミミロップ: 能力ポイント H1 A32 S32 ようき 相当 = 努力値 H8 A252 S252、素早さ↑特攻↓)
LOPUNNY_BUILD = {"ev": {"hp": 8, "atk": 252, "spe": 252}, "nature": {"spe": 1.1, "spa": 0.9},
                 "item_ja": None, "ability_ja": None}


class _Patch:
    """my_team の参照を合成の登録に差し替える (テストの間だけ)"""

    def __init__(self, builds=None, moves=None):
        self.builds, self.moves = builds or {}, moves or {}

    def __enter__(self):
        self.orig = (mt.get_my_build, mt.get_my_moves)
        mt.get_my_build = lambda ja: self.builds.get(ja)
        mt.get_my_moves = lambda ja: list(self.moves.get(ja) or [])
        return self

    def __exit__(self, *a):
        mt.get_my_build, mt.get_my_moves = self.orig


def _mon(sid, ja, types, moves=(), **kw):
    d = {"species_id": sid, "species_ja": ja, "types": list(types), "hp_percent": 100.0, "hp_current": None,
         "hp_max": None, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
         "moves": [{"name_ja": ja_m, "move_id": mid, "pp": pp, "max_pp": None, "effectiveness": None}
                   for ja_m, mid, pp in moves],
         "revealed_moves": [], "is_picked": True}
    d.update(kw)
    return d


def _state(my, opp, my_bench=(), **kw):
    side = lambda party: {"active_index": 0, "remaining": 3, "tailwind": False,
                          "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
                          "screens": {"reflect": False, "light_screen": False, "aurora_veil": False},
                          "party": party}
    st = {"field": {"weather": None, "terrain": None, "trick_room": False},
          "mega_used": {"player": False, "opponent": False},
          "player": side([my, *my_bench]), "opponent": side([opp])}
    st.update(kw)
    return st


# 113830:171 の簡約状態 (自分のミミロップ: mega=true なのに species=lopunny、タイプ ノーマル、特性 scrappy)
LOPUNNY_171 = dict(_mon("lopunny", "ミミロップ", ["ノーマル"],
                        [("インファイト", "closecombat", None), ("つるぎのまい", "swordsdance", 20),
                         ("トリプルアクセル", "tripleaxel", 12), ("かみなりパンチ", "thunderpunch", 16)]),
                   is_mega=True, item_id="lopunnite", ability_id="scrappy", hp_current=142, hp_max=142)
PAWMOT_171 = _mon("pawmot", "パーモット", ["でんき", "かくとう"], revealed_moves=["マッハパンチ"])


# ==============================================================================
# 段 1: メガ形態の整合
# ==============================================================================
def test_resolve_form_cases():
    """形態の解決 (純粋): メガの印 + 石 → メガ形態。反例 (メガ前・石以外・X/Y で決まらない・権利消費は無関係) は基本種のまま"""
    f = resolve_form({"species_id": "lopunny", "is_mega": True, "item_id": "lopunnite", "types": ["ノーマル"]})
    assert (f.base_species, f.species, f.types, f.is_mega, f.ability) == \
        ("lopunny", "lopunnymega", ("Normal", "Fighting"), True, "scrappy"), f
    # 既にメガ形態の id (相手側の取り込み経路) も同じ結果
    f2 = resolve_form({"species_id": "lopunnymega", "is_mega": True, "types": ["ノーマル"]})
    assert (f2.species, f2.types, f2.ability) == ("lopunnymega", ("Normal", "Fighting"), "scrappy"), f2
    # 反例 1: メガ前 (is_mega なし) は石を持っていても基本種
    f3 = resolve_form({"species_id": "lopunny", "is_mega": False, "item_id": "lopunnite", "types": ["ノーマル"]})
    assert (f3.species, f3.types, f3.is_mega) == ("lopunny", ("Normal",), False), f3
    # 反例 2: メガ石なし (持ち物が石以外と判明) は矛盾として基本種のまま (注記を残す)
    f4 = resolve_form({"species_id": "lopunny", "is_mega": True, "item_id": "sitrusberry", "types": ["ノーマル"]})
    assert f4.species == "lopunny" and not f4.is_mega and "矛盾" in f4.note, f4
    # 反例 3: X/Y があり石が分からなければ形態を強制確定しない
    f5 = resolve_form({"species_id": "charizard", "is_mega": True, "types": ["ほのお", "ひこう"]})
    assert f5.species == "charizard" and f5.note, f5
    f6 = resolve_form({"species_id": "charizard", "is_mega": True, "item_id": "charizarditex", "types": []})
    assert (f6.species, f6.types) == ("charizardmegax", ("Fire", "Dragon")), f6
    # 一時的なタイプ変化 (type_changed) は観測値を使う
    f7 = resolve_form({"species_id": "lopunnymega", "is_mega": True, "types": ["ほのお"], "type_changed": True})
    assert f7.types == ("Fire",) and f7.types_temporary, f7
    # メガでない個体は従来どおり観測タイプ (無ければ図鑑)
    assert resolve_form({"species_id": "meowscarada", "types": ["ほのお"]}).types == ("Fire",)
    assert resolve_form({"species_id": "meowscarada", "types": []}).types == ("Grass", "Dark")
    assert resolve_form({"species_id": "nosuchmon"}) is None
    print("test_resolve_form_cases OK")


def test_mega_lopunny_from_log_171():
    """113830:171 の再現: 旧実装は通常形態 (攻撃 128 / 素早さ 172、ノーマル単) で評価していた。
    修正後はメガ形態 (種族値 攻撃 136 / 素早さ 135 → 合成の登録配分で 188 / 205、ノーマル/かくとう、スキン無しの scrappy)"""
    with _Patch(builds={"ミミロップ": LOPUNNY_BUILD}):
        v = build_mon_view(copy.deepcopy(LOPUNNY_171), resolver, side="player")
        atk = calc_stat(v.base["atk"], v.ev.get("atk", 0), v.nature.get("atk", 1.0))
        spe = calc_stat(v.base["spe"], v.ev.get("spe", 0), v.nature.get("spe", 1.0))
        assert v.species_id == "lopunnymega" and v.types == ["Normal", "Fighting"] and v.ability == "scrappy", v
        assert (v.base["atk"], v.base["spe"], atk, spe) == (136, 135, 188, 205), (v.base, atk, spe)
        # 旧挙動の再現 (独立の根拠): 通常形態の種族値 (攻撃 76 / 素早さ 105) で同じ配分なら 128 / 172 (計画書 §1.3 の値)
        assert (calc_stat(76, 252, 1.0), calc_stat(105, 252, 1.1)) == (128, 172)
        # 状態 → engine: 素早さの判定 (speed_note) もメガ形態の 205 を使う。インファイトはタイプ一致になる
        adv = evaluate(_state(copy.deepcopy(LOPUNNY_171), copy.deepcopy(PAWMOT_171),
                              mega_used={"player": True, "opponent": True}), resolver, rl_blend_weight=0)
        assert "自分205" in adv["speed_note"], adv["speed_note"]
        assert not adv["mega_note"], adv["mega_note"]
    print("test_mega_lopunny_from_log_171 OK")


def test_mega_counterexamples_in_engine():
    """反例: メガ前 / 陣営のメガ権利消費済みでも個体はメガ前 / 相手側がメガでも自分の形態は変わらない"""
    with _Patch(builds={"ミミロップ": LOPUNNY_BUILD}):
        pre = dict(copy.deepcopy(LOPUNNY_171), is_mega=False, ability_id=None)
        assert build_mon_view(pre, resolver, side="player").species_id == "lopunny"
        st = _state(pre, copy.deepcopy(PAWMOT_171), mega_used={"player": True, "opponent": True})
        assert "自分172" in evaluate(st, resolver, rl_blend_weight=0)["speed_note"]
        # 相手側のメガ (相手の個体は形態 id) は相手の MonView だけを変える
        opp = _mon("golisopod", "グソクムシャ", ["むし", "みず"], is_mega=True, item_id="golisopite")
        ov = build_mon_view(opp, resolver)
        assert ov.species_id == "golisopodmega" and ov.types == ["Bug", "Steel"], ov
        assert build_mon_view(pre, resolver, side="player").species_id == "lopunny"
    print("test_mega_counterexamples_in_engine OK")


def test_rl_input_uses_resolved_form():
    """RL の入力 (rl_bridge.resolve_state_forms) も同じ形態の解決を使い、入力の状態は書き換えない"""
    from advisor.rl_bridge import resolve_state_forms
    st = _state(copy.deepcopy(LOPUNNY_171), copy.deepcopy(PAWMOT_171))
    frozen = copy.deepcopy(st)
    out = resolve_state_forms(st)
    me = out["player"]["party"][0]
    assert me["species_id"] == "lopunnymega" and me["types"] == ["Normal", "Fighting"], me
    assert out["opponent"]["party"][0]["species_id"] == "pawmot"
    assert st == frozen
    print("test_rl_input_uses_resolved_form OK")


def test_hud_name_does_not_revert_mega():
    """状態の更新の原因経路: 自分側は HUD の通常名 (ミミロップ) の読みで adopt_my_hud_species → merge_species が
    lopunnymega を lopunny に戻していた (相手側は extract_battle_hud / switch_to_species の前方一致の保護で戻らない)。
    メガ後の個体は基本種の読みで形態を戻さない。メガ前・別の種は従来どおり上書きする"""
    from vision.extractors import adopt_my_hud_species
    from vision.state import BattleStateV2, PokemonState, keeps_mega_form
    st = BattleStateV2()
    st.player.party = [PokemonState(species_ja="ミミロップ", species_id="lopunnymega", is_mega=True,
                                    item_id="lopunnite"),
                       PokemonState(species_ja="ペロリーム", species_id="slurpuff")]
    st.player.active_index = 0
    me = adopt_my_hud_species(st, ("ミミロップ", "lopunny", 1.0))
    assert me is st.player.party[0] and me.species_id == "lopunnymega", me.species_id
    # 旧挙動の条件: is_mega でなければ (メガ前) 読みで上書きされる
    assert keeps_mega_form("lopunnymega", True, "lopunny")
    assert not keeps_mega_form("lopunnymega", False, "lopunny")
    assert not keeps_mega_form("lopunnymega", True, "slurpuff")
    assert not keeps_mega_form("charizardmegax", True, "charizardmegay")
    p = PokemonState(species_ja="ミミロップ", species_id="lopunny")
    p.merge_species("ミミロップ", "lopunny")
    assert p.species_id == "lopunny"
    print("test_hud_name_does_not_revert_mega OK")


# ==============================================================================
# 段 2: 技の固定
# ==============================================================================
def _indeedee_battle():
    """084212 の局面: 自分のイエッサン (ワイドフォース/マジカルフレイム/アンコール/みがわり) vs 相手のイエッサン"""
    from vision.events import EventParser
    from vision.state import BattleStateV2, MoveSlot, PokemonState
    st = BattleStateV2()
    me = PokemonState(species_ja="イエッサン", species_id="indeedee", types=["エスパー", "ノーマル"],
                      hp_percent=100.0, is_picked=True, is_active=True,
                      moves=[MoveSlot("ワイドフォース", "expandingforce", 12), MoveSlot("マジカルフレイム", "mysticalfire", 12),
                             MoveSlot("アンコール", "encore", None), MoveSlot("みがわり", "substitute", 12)])
    st.player.party = [me, PokemonState(species_ja="カメックス", species_id="blastoise", is_picked=True)]
    st.player.active_index = 0
    st.opponent.party = [PokemonState(species_ja="イエッサン", species_id="indeedee", types=["エスパー", "ノーマル"],
                                      hp_percent=100.0, is_active=True)]
    st.opponent.active_index = 0
    return st, EventParser(st, resolver)


def test_encore_from_move_use_084212():
    """084212 ターン 2〜3 の再現: 相手のアンコールは使用文「相手のイエッサンのアンコール」だけが読めていた。旧実装は
    encore_on が使用者 (相手) に付き、自分の技の固定が取れず、ターン 3 で選べない ワイドフォース を第一候補にした。
    修正後: 自分のイエッサンがアンコール状態 (固定 = 直前の マジカルフレイム)、候補は マジカルフレイム だけ"""
    st, p = _indeedee_battle()
    st.turn = 1
    p.parse("イエッサンのマジカルフレイム")
    st.turn = 2
    fired = p.parse("相手のイエッサンのアンコール")
    assert "encore_inferred_player" in fired and "encore_on" not in fired, fired
    me, opp = st.player.active(), st.opponent.active()
    assert "encore" in me.volatiles and me.encore_move == "mysticalfire", (me.volatiles, me.encore_move)
    assert "encore" not in opp.volatiles, opp.volatiles
    # 自分はまだこのターン行動していない → このターンが固定され、残りは次の 2 回の行動選択
    assert me.volatile_turns == {"encore": 2}, me.volatile_turns
    p.parse("イエッサンのマジカルフレイム")
    # ターン 3 の行動選択
    st.turn = 3
    st.tick_move_locks()
    adv = evaluate(st.to_dict(), resolver, rl_blend_weight=0)
    moves = [a["id"] for a in adv["actions"] if a["kind"] == "move"]
    assert moves == ["mysticalfire"], moves
    ill = {a["id"]: a["reason"] for a in adv["illegal_actions"]}
    assert set(ill) == {"expandingforce", "encore", "substitute"} and "アンコール" in ill["expandingforce"], ill
    assert "アンコール中: マジカルフレイム しか選べません" in adv["speed_note"], adv["speed_note"]
    # ターン 4 も固定、ターン 5 の行動選択で切れる (行動前に受けた: 3 − 1 = 2 回)
    st.turn = 4
    st.tick_move_locks()
    assert "encore" in st.player.active().volatiles
    st.turn = 5
    expired = st.tick_move_locks()
    assert expired == [("player", "イエッサン", "encore")], expired
    adv5 = evaluate(st.to_dict(), resolver, rl_blend_weight=0)
    assert {a["id"] for a in adv5["actions"] if a["kind"] == "move"} == \
        {"expandingforce", "mysticalfire", "encore", "substitute"}, adv5["actions"]
    print("test_encore_from_move_use_084212 OK")


def test_encore_messages_and_release():
    """「<対象>は アンコールを 受けた」は対象に付け、技の使用文として扱わない (相手の固定技が「アンコール」になっていた)。
    技の説明文「相手を アンコール状態に する」では付けない。解除の文言・交代で外れる。直前の技が無い対象への使用は失敗"""
    st, p = _indeedee_battle()
    st.turn = 1
    p.parse("相手のイエッサンのマジカルフレイム")       # 相手が先に行動
    p.parse("イエッサンのアンコール")
    opp = st.opponent.active()
    assert "encore" in opp.volatiles and opp.encore_move == "mysticalfire", (opp.volatiles, opp.encore_move)
    assert opp.volatile_turns == {"encore": 3}, opp.volatile_turns   # 相手は行動済み → 次の 3 回
    fired = p.parse("相手のイエッサンはアンコールを受けた")
    assert "encore_on" in fired and not any(f.startswith("move_") for f in fired), fired
    assert st.last_move.get("opponent") == "mysticalfire", st.last_move
    assert opp.encore_move == "mysticalfire"
    # 説明文では付かない
    st2, p2 = _indeedee_battle()
    assert "encore_on" not in p2.parse("相手をアンコール状態にする")
    assert "encore" not in st2.player.active().volatiles
    # 直前の技が無い対象へのアンコール (使用文だけ) は失敗として付けない
    st3, p3 = _indeedee_battle()
    p3.parse("相手のイエッサンのアンコール")
    assert "encore" not in st3.player.active().volatiles
    # 解除の文言 (技の使用文として拾わない: 拾うと相手の直前の技が「アンコール」になり、自分にアンコールを推定していた)
    fired = p.parse("相手のイエッサンのアンコール状態が解けた")
    assert fired == ["encore_off"], fired
    assert "encore" not in opp.volatiles and opp.encore_move is None and not opp.volatile_turns
    assert st.last_move.get("opponent") == "mysticalfire" and "encore" not in st.player.active().volatiles
    # 交代で外れる
    st4, p4 = _indeedee_battle()
    st4.turn = 1
    p4.parse("イエッサンのマジカルフレイム")
    p4.parse("相手のイエッサンのアンコール")
    me = st4.player.active()
    assert "encore" in me.volatiles
    p4.parse("ゆけっカメックス")
    assert "encore" not in me.volatiles and me.encore_move is None and not me.volatile_turns
    assert st4.player.active().species_id == "blastoise" and st4.player.active().entry_turn == 1
    print("test_encore_messages_and_release OK")


def test_choice_lock_release_and_pp():
    """こだわり: 技を使うと固定、交代 (文言) で解除。PP 0 の技は候補に出ない (理由つきで illegal_actions)"""
    st, p = _indeedee_battle()
    me = st.player.active()
    me.item_id = "choicespecs"
    st.turn = 1
    p.parse("イエッサンのワイドフォース")
    adv = evaluate(st.to_dict(), resolver, rl_blend_weight=0)
    assert [a["id"] for a in adv["actions"] if a["kind"] == "move"] == ["expandingforce"], adv["actions"]
    assert "こだわり中" in adv["speed_note"]
    p.parse("ゆけっカメックス")
    p.parse("ゆけっイエッサン")
    adv2 = evaluate(st.to_dict(), resolver, rl_blend_weight=0)
    assert len([a for a in adv2["actions"] if a["kind"] == "move"]) == 4, adv2["actions"]
    # PP 0
    d = st.to_dict()
    d["player"]["party"][d["player"]["active_index"]]["moves"][0]["pp"] = 0
    adv3 = evaluate(d, resolver, rl_blend_weight=0)
    assert "expandingforce" not in {a["id"] for a in adv3["actions"]}
    assert {a["id"]: a["reason"] for a in adv3["illegal_actions"]} == {"expandingforce": "PP が 0"}
    print("test_choice_lock_release_and_pp OK")


def test_lock_target_not_in_moves_is_unconfirmed():
    """固定する技が今の個体の技に無い (交代の取り逃しで前の個体の技が残った等) なら絞らず、合法性未確認の印を残す
    (旧実装は全技を選べないにしていた)"""
    my = _mon("staraptor", "ムクホーク", ["ノーマル", "ひこう"],
              [("ブレイブバード", "bravebird", 15), ("でんこうせっか", "quickattack", 20)], item_id="choicescarf")
    opp = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"])
    st = _state(my, opp, last_move={"player": "earthquake"})
    adv = evaluate(st, resolver, rl_blend_weight=0)
    assert {a["id"] for a in adv["actions"] if a["kind"] == "move"} == {"bravebird", "quickattack"}
    assert adv["legality_unconfirmed"] and not adv["illegal_actions"], adv["legality_unconfirmed"]
    print("test_lock_target_not_in_moves_is_unconfirmed OK")


def test_first_turn_only_with_unable_turn():
    """初手限定 (ねこだまし): 場に出たターンの次のターンだけ選べる。行動できなかったターン (ひるみ等で技の文言なし、
    last_move なし) の後も選べない (旧実装は last_move だけで判定し、選べるままだった)"""
    assert not first_turn_passed(0, 1) and first_turn_passed(0, 2) and not first_turn_passed(None, 5)
    my = _mon("lopunny", "ミミロップ", ["ノーマル"], [("ねこだまし", "fakeout", 10), ("インファイト", "closecombat", 5)],
              entry_turn=3)
    opp = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"])
    adv1 = evaluate(_state(copy.deepcopy(my), copy.deepcopy(opp), turn=4), resolver, rl_blend_weight=0)
    assert "fakeout" in {a["id"] for a in adv1["actions"]}
    adv2 = evaluate(_state(copy.deepcopy(my), copy.deepcopy(opp), turn=5), resolver, rl_blend_weight=0)
    assert "fakeout" not in {a["id"] for a in adv2["actions"]}
    assert any(a["id"] == "fakeout" and "最初のターン" in a["reason"] for a in adv2["illegal_actions"])
    print("test_first_turn_only_with_unable_turn OK")


def test_illegal_move_reason_pure():
    from advisor.dex import get_dex
    dex = get_dex()
    assert illegal_move_reason("protect", dex.move("protect"), 8, taunted=True) == "ちょうはつ中は変化技を選べない"
    assert illegal_move_reason("closecombat", dex.move("closecombat"), 5, taunted=True) is None
    assert illegal_move_reason("closecombat", dex.move("closecombat"), 0) == "PP が 0"
    assert illegal_move_reason("a", None, None, encore_locked="b") == "アンコール中は直前の技しか選べない"
    assert illegal_move_reason("b", None, None, encore_locked="b") is None
    print("test_illegal_move_reason_pure OK")


def test_move_locks_logged_and_replayed():
    """advice 行の move_locks (digest の外の欄) に技の固定を残し、tools.advice_replay で復元すると同じ候補になる"""
    from battle_logger import _compact_state, move_locks_of, state_digest
    from tools.advice_replay import compact_to_engine_state
    st, p = _indeedee_battle()
    st.turn = 1
    p.parse("イエッサンのマジカルフレイム")
    st.turn = 2
    p.parse("相手のイエッサンのアンコール")
    st.turn = 3
    st.tick_move_locks()
    d = st.to_dict()
    locks = move_locks_of(d)
    assert locks["player"]["encore_move"] == "mysticalfire" and locks["turn"] == 3, locks
    compact = _compact_state(d)
    assert "encore_move" not in str(compact)                                   # 簡約状態 (digest) は変えない
    assert state_digest(compact) == state_digest(_compact_state(st.to_dict()))
    restored = compact_to_engine_state(compact, resolver, move_locks=locks)
    adv = evaluate(restored, resolver, rl_blend_weight=0)
    assert [a["id"] for a in adv["actions"] if a["kind"] == "move"] == ["mysticalfire"], adv["actions"]
    # 欄が無い (M1a 以前の行) と固定は復元できない
    adv0 = evaluate(compact_to_engine_state(compact, resolver), resolver, rl_blend_weight=0)
    assert len([a for a in adv0["actions"] if a["kind"] == "move"]) == 4
    assert move_locks_of({"player": {"active_index": 0, "party": [{}]}}) == {}
    print("test_move_locks_logged_and_replayed OK")


# ==============================================================================
# 段 3: 部分的に読めた技の登録による補完
# ==============================================================================
def test_complete_moves_pure():
    reg = [("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")]
    obs = [{"name_ja": "A", "move_id": "a", "pp": 3}, {"name_ja": "C", "move_id": "c", "pp": 0}]
    out = complete_moves_from_registration(obs, reg)
    assert [s["move_id"] for s in out] == ["a", "c", "b", "d"], out
    assert out[1]["pp"] == 0 and not out[1].get("from_registration")         # 観測の PP 0 は補完で復活しない
    assert out[2]["from_registration"] and out[2]["pp"] is None
    assert obs == [{"name_ja": "A", "move_id": "a", "pp": 3}, {"name_ja": "C", "move_id": "c", "pp": 0}]   # 入力は不変
    # 登録に無い技を観測 (矛盾) → 観測を残し、空いた 2 枠だけ登録の順で埋める
    out2 = complete_moves_from_registration([{"move_id": "x"}, {"move_id": "a"}], reg)
    assert [s["move_id"] for s in out2] == ["x", "a", "b", "c"], out2
    assert complete_moves_from_registration([{"move_id": m} for m in "wxyz"], reg) == [{"move_id": m} for m in "wxyz"]
    print("test_complete_moves_pure OK")


def test_partial_moves_from_log_112839():
    """112839 ターン 4 の再現: セグレイブの技は 3 つ (きょけんとつげき・つららおとし・かわらわり) だけ読め、登録の じしん が
    候補から欠けていた (旧実装は 1 つも読めていないときだけ補完)。合成の登録 4 技で じしん が「登録からの補完」で候補に入る"""
    my = _mon("baxcalibur", "セグレイブ", ["こおり", "ドラゴン"],
              [("きょけんとつげき", "glaiverush", None), ("つららおとし", "iciclecrash", 12), ("かわらわり", "brickbreak", 16)],
              item_id="focussash", ability_id="thermalexchange", hp_percent=None)
    opp = _mon("ninetales", "キュウコン", ["ほのお"], revealed_moves=["ウェザーボール"])
    with _Patch(moves={"セグレイブ": ["きょけんとつげき", "つららおとし", "じしん", "かわらわり"]}):
        adv = evaluate(_state(my, opp), resolver, rl_blend_weight=0)
    moves = {a["id"]: a for a in adv["actions"] if a["kind"] == "move"}
    assert set(moves) == {"glaiverush", "iciclecrash", "brickbreak", "earthquake"}, set(moves)
    assert moves["earthquake"].get("from_registration") and "登録からの補完" in moves["earthquake"]["reason"]
    assert not moves["iciclecrash"].get("from_registration")
    assert [m["move_id"] for m in my["moves"]] == ["glaiverush", "iciclecrash", "brickbreak"]   # 状態は書き換えない
    print("test_partial_moves_from_log_112839 OK")


def test_partial_moves_keep_observation():
    """読めた 2 技 + 登録 4 技 → 候補 4 技。登録に無い観測技は残る。PP 0 の観測は補完で復活しない"""
    my = _mon("lopunny", "ミミロップ", ["ノーマル"], [("インファイト", "closecombat", 0), ("ねこだまし", "fakeout", 10)])
    opp = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"])
    with _Patch(moves={"ミミロップ": ["インファイト", "トリプルアクセル", "かみなりパンチ", "つるぎのまい"]}):
        adv = evaluate(_state(my, opp), resolver, rl_blend_weight=0)
    moves = {a["id"] for a in adv["actions"] if a["kind"] == "move"}
    assert moves == {"fakeout", "tripleaxel", "thunderpunch"}, moves        # 4 枠目 (つるぎのまい) は空きが無いので入らない
    assert [a["id"] for a in adv["illegal_actions"]] == ["closecombat"]     # PP 0 の観測を補完が上書きしない
    print("test_partial_moves_keep_observation OK")


# ==============================================================================
# 段 4: 据え置きの誤った保持
# ==============================================================================
def _res(actions, position=("p",), illegal=()):
    return {"ok": True, "actions": [dict(a) for a in actions], "best": dict(actions[0]), "position_key": position,
            "illegal_actions": [dict(a) for a in illegal]}


def test_hysteresis_position_and_legality():
    from advisor.service import apply_advice_hysteresis
    a = {"kind": "move", "id": "a", "name": "A", "score": 50.0}
    b = {"kind": "move", "id": "b", "name": "B", "score": 47.0}
    _, last = apply_advice_hysteresis(_res([b, a]), None, 3)
    # 同じ局面: 差 3 < 6 なので前回の b を据え置く (既存の挙動)
    r, _ = apply_advice_hysteresis(_res([a, b]), last, 3)
    assert r["best"]["id"] == "b"
    # 場の個体が変わった (局面の鍵が違う) → 据え置かない
    r2, _ = apply_advice_hysteresis(_res([a, b], position=("q",)), last, 3)
    assert r2["best"]["id"] == "a"
    # 旧 best が選べなくなった (illegal_actions へ移った) → 据え置かない
    c = {"kind": "move", "id": "c", "name": "C", "score": 45.0}
    r3, _ = apply_advice_hysteresis(_res([a, c], illegal=[dict(b, legal=False)]), last, 3)
    assert r3["best"]["id"] == "a"
    # 合法手の集合が変わった → 据え置かない (局面の鍵が同じでも)
    r4, _ = apply_advice_hysteresis(_res([a, b, c]), last, 3)
    assert r4["best"]["id"] == "a"
    # 最終の第一候補にも合法性の検査 (旧形式の −99 点や legal=False は best にしない)
    bad = {"kind": "move", "id": "z", "name": "Z", "score": 99.0, "legal": False}
    r5, last5 = apply_advice_hysteresis(_res([bad, a]), None, 4)
    assert r5["best"]["id"] == "a" and last5["id"] == "a"
    print("test_hysteresis_position_and_legality OK")


def test_service_does_not_hold_after_switch():
    """状態 → engine → service: 同じターンでも場の個体が替われば局面の鍵が変わり、Advisor.advise は前回の第一候補を
    据え置かない (同じ技 id が両方の個体の候補にある場合でも)"""
    from advisor import service
    my1 = _mon("staraptor", "ムクホーク", ["ノーマル", "ひこう"],
               [("ブレイブバード", "bravebird", 15), ("インファイト", "closecombat", 5)])
    my2 = _mon("lucario", "ルカリオ", ["かくとう", "はがね"],
               [("インファイト", "closecombat", 5), ("コメットパンチ", "meteormash", 10)])
    opp = _mon("heatran", "ヒードラン", ["ほのお", "はがね"])
    st1 = _state(copy.deepcopy(my1), copy.deepcopy(opp), turn=2)
    st2 = _state(copy.deepcopy(my2), copy.deepcopy(opp), turn=2)
    k1 = evaluate(st1, resolver, rl_blend_weight=0)["position_key"]
    k2 = evaluate(st2, resolver, rl_blend_weight=0)["position_key"]
    assert k1 != k2 and k1 == evaluate(copy.deepcopy(st1), resolver, rl_blend_weight=0)["position_key"]
    cc = {"kind": "move", "id": "closecombat", "name": "インファイト", "score": 50.0}
    mm = {"kind": "move", "id": "meteormash", "name": "コメットパンチ", "score": 53.0}
    bb = {"kind": "move", "id": "bravebird", "name": "ブレイブバード", "score": 40.0}
    fake = {id(st1): _res([cc, bb], position=k1), id(st2): _res([mm, cc], position=k2)}
    orig = service.evaluate
    service.evaluate = lambda state, resolver=None, rl_blend_weight=None: copy.deepcopy(fake[id(state)])
    try:
        adv = service.Advisor(resolver)
        assert adv.advise(st1)["best"]["id"] == "closecombat"
        r2 = adv.advise(st2)
    finally:
        service.evaluate = orig
    # 旧実装は turn が同じで差 3 < 6 なので インファイト (前の個体の第一候補) を据え置いていた
    assert r2["best"]["id"] == "meteormash", r2["best"]
    print("test_service_does_not_hold_after_switch OK")


if __name__ == "__main__":
    test_resolve_form_cases()
    test_mega_lopunny_from_log_171()
    test_mega_counterexamples_in_engine()
    test_rl_input_uses_resolved_form()
    test_hud_name_does_not_revert_mega()
    test_encore_from_move_use_084212()
    test_encore_messages_and_release()
    test_choice_lock_release_and_pp()
    test_lock_target_not_in_moves_is_unconfirmed()
    test_first_turn_only_with_unable_turn()
    test_illegal_move_reason_pure()
    test_move_locks_logged_and_replayed()
    test_complete_moves_pure()
    test_partial_moves_from_log_112839()
    test_partial_moves_keep_observation()
    test_hysteresis_position_and_legality()
    test_service_does_not_hold_after_switch()
