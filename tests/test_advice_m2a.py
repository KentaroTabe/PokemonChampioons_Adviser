"""操縦提案の精度改善 M2a (入力の不一致の解消) のテスト。2026-10-09 レビューの指示 1。

段 1 補完した技と合法手を全経路 (直接の採点・探索・終盤評価・メガの注記・RL の合法手) で共有する (legal_move_slots) /
段 2 メガの誤発火 (command / move_select の「メガシンカ」ボタンの文字で mega_evolve が発火していた) /
段 3 終盤評価 (advisor/endgame の 1 対 1 判定) で自滅技・HP の消費の技を「繰り返し撃てる最大打点」にしない。
登録の型は合成 (本体の config/my_team.json は読まない: tests.test_advice_m1a の _Patch)。

    python -m tests.test_advice_m2a
"""
from __future__ import annotations

import copy
import os

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import engine
from advisor import rl_bridge as rb
from advisor.damage import MonView
from advisor.dex import get_dex
from advisor.engine import evaluate, legal_move_slots, rl_input_state
from tests.test_advice_m1a import _Patch, _mon, _state, resolver


# ==============================================================================
# 段 1: 補完した技と合法手を全経路で共有する
# ==============================================================================
INDEEDEE_MOVES = [("ワイドフォース", "expandingforce", 12), ("マジカルフレイム", "mysticalfire", 12),
                  ("アンコール", "encore", None), ("みがわり", "substitute", 12)]


def _encored_state():
    """084212 ターン 3 相当: 自分のイエッサンはアンコール (固定 = マジカルフレイム)。控えにカメックス"""
    me = _mon("indeedee", "イエッサン", ["エスパー", "ノーマル"], INDEEDEE_MOVES,
              volatiles=["encore"], encore_move="mysticalfire")
    bench = _mon("blastoise", "カメックス", ["みず"], [("ハイドロポンプ", "hydropump", 8), ("れいとうビーム", "icebeam", 16)])
    opp = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], revealed_moves=["じしん"])
    return _state(me, opp, my_bench=[bench], last_move={"player": "mysticalfire"}, turn=3)


class _Capture:
    """engine の後段 (メガの注記・終盤評価の行列・RL) が受け取った入力を記録する (テストの間だけ差し替える)"""

    def __enter__(self):
        import advisor.endgame as eg
        self.eg = eg
        self.seen = {}
        self.orig = (eg.matchup_matrix, rb.policy_hint, engine._mega_timing_note)
        orig_mm, orig_note = eg.matchup_matrix, engine._mega_timing_note

        def mm(my_mons, opp_mons, *a, **kw):
            self.seen["endgame_my"] = {n: list(mv) for n, _v, _hp, mv in my_mons}
            return orig_mm(my_mons, opp_mons, *a, **kw)

        def hint(state, my_spe_actual=None):
            self.seen["rl_state"] = state
            return None

        def note(*a, **kw):
            self.seen["mega_moves"] = list(kw.get("move_ids") or [])
            return orig_note(*a, **kw)

        eg.matchup_matrix, rb.policy_hint, engine._mega_timing_note = mm, hint, note
        return self

    def __exit__(self, *a):
        self.eg.matchup_matrix, rb.policy_hint, engine._mega_timing_note = self.orig


def _search_moves(adv) -> set:
    return {a["move_id"] for a in ((adv.get("gtheory") or {}).get("actions") or []) if a.get("kind") == "move"}


def test_legal_move_slots_pure():
    """切り出した判定 (純粋): アンコール中は固定技以外に理由が付き、選べる技は固定技だけ。入力は書き換えない"""
    st = _encored_state()
    my_p = st["player"]["party"][0]
    before = copy.deepcopy(my_p)
    leg = legal_move_slots(st, my_p, item=None, registered=None)
    assert leg.legal_move_ids == ["mysticalfire"], leg.legal_move_ids
    assert leg.encore_locked == "mysticalfire" and leg.illegal_move_ids == {"expandingforce", "encore", "substitute"}
    assert [a["id"] for a in leg.illegal_actions] == ["expandingforce", "encore", "substitute"]
    assert my_p == before
    # 交代しか選べない決定: 補完しない・illegal_actions は空 (技の候補を作らない。M1a までと同じ)
    leg2 = legal_move_slots(st, my_p, registered=[("じしん", "earthquake")], switch_only=True)
    assert leg2.illegal_actions == [] and len(leg2.slots) == 4
    # 補完: 読めた 2 技 + 登録 → 4 枠。こだわり (持ち物 id で判定) は直前の技だけ
    my2 = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake", 16), ("げきりん", "outrage", 16)])
    reg = [("じしん", "earthquake"), ("ストーンエッジ", "stoneedge"), ("ほのおのキバ", "firefang")]
    leg3 = legal_move_slots({"last_move": {"player": "outrage"}}, my2, item="choicescarf", registered=reg)
    assert [s["move_id"] for s in leg3.slots] == ["earthquake", "outrage", "stoneedge", "firefang"]
    assert leg3.choice_locked == "outrage" and leg3.legal_move_ids == ["outrage"], leg3.legal_move_ids
    print("test_legal_move_slots_pure OK")


def test_encore_shared_by_search_endgame_rl_mega():
    """受入: アンコール中は 直接の採点・探索の行動集合・終盤評価の技・RL の合法手・メガの注記が固定技だけになる。
    旧実装 (M1a) は探索・終盤評価・メガの注記が状態の 4 技、RL は PP 0 だけを除いた 4 技を見ていた"""
    st = _encored_state()
    st["player"]["party"][0]["item_ja"] = "メガストーン"
    st["player"]["party"][0]["item_id"] = "megastone"
    with _Capture() as cap:
        adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0)
    assert {a["id"] for a in adv["actions"] if a["kind"] == "move"} == {"mysticalfire"}
    assert _search_moves(adv) == {"mysticalfire"}, _search_moves(adv)
    assert cap.seen["mega_moves"] == ["mysticalfire"], cap.seen
    assert cap.seen["endgame_my"]["イエッサン"] == ["mysticalfire"], cap.seen["endgame_my"]
    # 控えの個体は状態の技 (固定は交代で解ける)
    assert cap.seen["endgame_my"]["カメックス"] == ["hydropump", "icebeam"], cap.seen["endgame_my"]
    rl_moves = [label for _i, label, kind in rb._legal_actions(cap.seen["rl_state"]) if kind == "move"]
    assert rl_moves == ["マジカルフレイム"], rl_moves
    # 観測 (RL の入力) の技の並びは 4 枠のまま (選べない枠は legal=False)
    rl_slots = cap.seen["rl_state"]["player"]["party"][0]["moves"]
    assert [m["move_id"] for m in rl_slots] == ["expandingforce", "mysticalfire", "encore", "substitute"]
    assert [m.get("legal") for m in rl_slots] == [False, None, False, False], rl_slots
    # 旧挙動の再現 (状態の技をそのまま渡した場合): RL は 4 技すべてに確率を配っていた
    assert len([1 for *_x, k in rb._legal_actions(st) if k == "move"]) == 4
    # 入力の状態は書き換えない
    assert "legal" not in str(st["player"]["party"][0]["moves"])
    print("test_encore_shared_by_search_endgame_rl_mega OK")


def test_registration_completion_enters_search():
    """受入: 登録から補完した技 (from_registration) が探索の行動集合・終盤評価・RL の観測に入る (112839 ターン 4 のセグレイブ。
    旧実装の探索は読めた 3 技だけ)"""
    my = _mon("baxcalibur", "セグレイブ", ["こおり", "ドラゴン"],
              [("きょけんとつげき", "glaiverush", None), ("つららおとし", "iciclecrash", 12), ("かわらわり", "brickbreak", 16)],
              ability_id="thermalexchange")
    opp = _mon("ninetales", "キュウコン", ["ほのお"], revealed_moves=["ウェザーボール"])
    with _Patch(moves={"セグレイブ": ["きょけんとつげき", "つららおとし", "じしん", "かわらわり"]}), _Capture() as cap:
        adv = evaluate(_state(my, opp), resolver, rl_blend_weight=0)
    assert _search_moves(adv) == {"glaiverush", "iciclecrash", "brickbreak", "earthquake"}, _search_moves(adv)
    assert cap.seen["endgame_my"]["セグレイブ"] == ["glaiverush", "iciclecrash", "brickbreak", "earthquake"]
    rl_slots = cap.seen["rl_state"]["player"]["party"][0]["moves"]
    assert [m["move_id"] for m in rl_slots][-1] == "earthquake" and rl_slots[-1]["from_registration"]
    assert [m["move_id"] for m in my["moves"]] == ["glaiverush", "iciclecrash", "brickbreak"]   # 状態は書き換えない
    print("test_registration_completion_enters_search OK")


def test_rl_legal_actions_fallback():
    """RL の合法手: legal=False の技を除く。除いて合法手が 0 なら従来の扱い (PP 0 だけを除く)"""
    me = _mon("indeedee", "イエッサン", ["エスパー", "ノーマル"], INDEEDEE_MOVES)
    st = _state(me, _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"]))
    st["player"]["party"][0]["moves"] = [dict(m, legal=False) for m in st["player"]["party"][0]["moves"]]
    labels = [label for _i, label, _k in rb._legal_actions(st)]
    assert labels == ["ワイドフォース", "マジカルフレイム", "アンコール", "みがわり"], labels   # 控えなし → 従来の扱い
    st2 = copy.deepcopy(st)
    st2["player"]["party"].append(_mon("blastoise", "カメックス", ["みず"]))
    assert [k for *_x, k in rb._legal_actions(st2)] == ["switch"]          # 交代が残れば技は全部除く
    # rl_input_state: 補完も選べない技も無ければ同じ状態を返す (コピーしない)
    plain = _state(_mon("indeedee", "イエッサン", ["エスパー", "ノーマル"], INDEEDEE_MOVES),
                   _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"]))
    assert rl_input_state(plain, legal_move_slots(plain, plain["player"]["party"][0])) is plain
    print("test_rl_legal_actions_fallback OK")


# ==============================================================================
# 段 2: メガの誤発火
# ==============================================================================
# battle_202610*.jsonl で mega_evolve が発火した 64 件のうち、「メガシンカした」が続かない 18 件の文言 (重複を除く 16 種)。
# command / move_select の「メガシンカ」ボタンの文字 (名前の末尾 + HP + ボタン)。場面の誤分類で field / battle_hud のものもある
BUTTON_LABEL_TEXTS = [
    "ックス0622メガシンカ", "ックス0600メガシンカ", "ックス0618メガシンカ", "06848マンダメガシンカ", "ロップ0641メガシンカ",
    "ロップ0638閉しるメガシンカ", "0605ロップメガシンカ", "0638ロップメガシンカ", "マンダ0620メガシンカ", "ックス0559メガシンカ",
    "ックス0625メガシンカ", "ックス0651メガシンカ", "0617マンタメガシンカ", "ックス06日24メガシンカ", "ックス0636メガシンカ",
    "ックス645メガシンカ",
]


def _mega_state():
    from vision.events import EventParser
    from vision.state import BattleStateV2, PokemonState
    st = BattleStateV2()
    st.player.party = [PokemonState(species_ja="カメックス", species_id="blastoise", types=["みず"],
                                    item_ja="カメックスナイト", item_id="blastoisinite", is_picked=True, is_active=True),
                       PokemonState(species_ja="ミミロップ", species_id="lopunny", types=["ノーマル"],
                                    item_ja="ミミロップナイト", item_id="lopunnite", is_picked=True)]
    st.player.active_index = 0
    st.opponent.party = [PokemonState(species_ja="グソクムシャ", species_id="golisopod", types=["むし", "みず"],
                                      is_active=True)]
    st.opponent.active_index = 0
    st.battle_active = True
    return st, EventParser(st, resolver)


def test_button_label_does_not_fire_mega():
    """受入: ボタンの文字 16 種で mega_evolve が発火せず、is_mega / mega_used が立たない。
    旧実装の判定 (語「メガシンカ」だけ) はこれらすべてに合致していた (旧挙動の再現)"""
    from vision.events import _match_keywords
    from vision.normalize import loose_key
    for text in BUTTON_LABEL_TEXTS:
        assert _match_keywords(loose_key(text), [["メガシンカ", "めかしんか"]]), text     # 旧判定は発火
        st, p = _mega_state()
        fired = p.parse(text, source="message")
        assert "mega_evolve" not in fired, (text, fired)
        assert not any(m.is_mega for m in st.player.party + st.opponent.party), text
        assert st.mega_used == {"player": False, "opponent": False}, (text, st.mega_used)
    print("test_button_label_does_not_fire_mega OK")


def test_real_mega_messages_still_fire():
    """本物のメガの文言 (ログの OCR 崩れ「シン力した」「…したム」を含む) では発火し、形態と権利消費が入る"""
    st, p = _mega_state()
    assert "mega_evolve" in p.parse("カメックスはメガカメックスにメガシンカした")
    assert st.player.party[0].is_mega and st.player.party[0].species_id == "blastoisemega" and st.mega_used["player"]
    # 別の parser (同じ parser では mega_evolve の重複抑止 EVENT_DEDUP_SEC が陣営を区別せず 10 秒内の 2 回目を落とす。既存の挙動)
    st1, p1 = _mega_state()
    assert "mega_evolve" in p1.parse("相手のグリクムシャはメガグリクムシャにメガシン力した")
    assert st1.opponent.party[0].is_mega and st1.opponent.party[0].species_id == "golisopodmega"
    assert st1.mega_used == {"player": False, "opponent": True}
    st2, p2 = _mega_state()
    assert "mega_evolve" in p2.parse("相手のグソクムシャはメガグソクムシャにメガシンカしたム")
    # ボタンの文字の後に本物の文言が来ても (ボタンを押した後のメガ) 正しく 1 回だけ立つ
    st3, p3 = _mega_state()
    p3.parse("ックス0618メガシンカ")
    assert not st3.player.party[0].is_mega and not st3.mega_used["player"]
    assert "mega_evolve" in p3.parse("カメックスはメガカメックスにメガシンカした") and st3.player.party[0].is_mega
    print("test_real_mega_messages_still_fire OK")


# ==============================================================================
# 段 3: 終盤評価の自滅技
# ==============================================================================
def _view(sid, ev=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=sid, types=sp["types"], base=sp["baseStats"],
                   ev=ev or {"atk": 252, "spa": 252, "spe": 252}, nature={})


ELECTRODE = _view("electrode")                                    # 素早さ 150 族、だいばくはつ持ち
BULKY_CHOMP = _view("garchomp", ev={"hp": 252, "def": 252, "spd": 4})


def test_split_self_cost_moves():
    """外すのは自滅と HP の 1/2 を失う技。反動技は繰り返し撃てるので外さない"""
    from advisor.endgame import split_self_cost_moves
    rep, ex = split_self_cost_moves(["bravebird", "explosion", "steelbeam", "closecombat", "mistyexplosion", "chloroblast"])
    assert rep == ["bravebird", "closecombat"], rep
    assert ex == [("explosion", "selfko"), ("steelbeam", "half_hp"), ("mistyexplosion", "selfko"),
                  ("chloroblast", "half_hp_on_damage")], ex
    print("test_split_self_cost_moves OK")


def test_explosion_is_not_repeated_in_endgame():
    """受入: だいばくはつ持ちの終盤判定が「連発で勝ち」にならない。
    マルマイン (だいばくはつ + たいあたり) 対 耐久配分のガブリアス (たいあたり): だいばくはつは 1 回 約 33% で倒せない。
    旧 duel はだいばくはつを 3 回撃って勝ち (True)。撃てば自分がひんしで負けるので、外した判定 (False) が正しい"""
    from advisor.endgame import _best_dmg, duel, duel_judgement, matchup_matrix
    ex = _best_dmg(ELECTRODE, BULKY_CHOMP, ["explosion"])
    assert 25 < ex < 50, ex                                                      # 1 回では倒せない (独立の根拠)
    assert duel(ELECTRODE, 1.0, ["explosion", "tackle"], BULKY_CHOMP, 1.0, ["tackle"]) is True      # 旧挙動の再現
    assert duel_judgement(ELECTRODE, 1.0, ["explosion", "tackle"], BULKY_CHOMP, 1.0, ["tackle"]) == (False, [])
    r = matchup_matrix([("マルマイン", ELECTRODE, 1.0, ["explosion", "tackle"])],
                       [("ガブリアス", BULKY_CHOMP, 1.0, ["tackle"])])
    assert r["matrix"][("マルマイン", "ガブリアス")] is False and r["win_conditions"] == [], r
    assert r["lose_threats"] == ["ガブリアス"] and r["unsupported"] == [], r
    # 相手側のだいばくはつも同じ: 旧判定では自分のガブリアスが負け (負け筋) だった
    r2 = matchup_matrix([("ガブリアス", BULKY_CHOMP, 1.0, ["tackle"])],
                        [("マルマイン", ELECTRODE, 1.0, ["explosion", "tackle"])])
    assert duel(BULKY_CHOMP, 1.0, ["tackle"], ELECTRODE, 1.0, ["explosion", "tackle"]) is False     # 旧挙動の再現
    assert r2["matrix"][("ガブリアス", "マルマイン")] is True and r2["lose_threats"] == [], r2
    print("test_explosion_is_not_repeated_in_endgame OK")


def test_lethal_explosion_is_unsupported():
    """1 回で倒せるだいばくはつ (相手の残り HP 30%) は相打ちで、1 対 1 の勝ちではない: 判定保留 (None) と印。
    旧 duel は 1 ターンで勝ち (True) で、勝ち筋に数えていた"""
    from advisor.endgame import duel, matchup_matrix
    assert duel(ELECTRODE, 1.0, ["explosion", "tackle"], BULKY_CHOMP, 0.3, ["tackle"]) is True       # 旧挙動の再現
    r = matchup_matrix([("マルマイン", ELECTRODE, 1.0, ["explosion", "tackle"])],
                       [("ガブリアス", BULKY_CHOMP, 0.3, ["tackle"])])
    assert r["matrix"][("マルマイン", "ガブリアス")] is None and r["win_conditions"] == [], r
    assert r["unsupported"] == [{"my": "マルマイン", "opp": "ガブリアス", "side": "my", "move_id": "explosion",
                                 "kind": "selfko", "lethal_once": True}], r["unsupported"]
    # 自滅技が勝敗を左右しないとき (繰り返し撃てる技だけで勝てる) は勝ちのまま、印なし
    r2 = matchup_matrix([("マルマイン", ELECTRODE, 1.0, ["explosion", "thunderbolt"])],
                        [("ギャラドス", _view("gyarados", ev={"hp": 252, "def": 252}), 0.3, ["waterfall"])])
    assert r2["matrix"][("マルマイン", "ギャラドス")] is True and r2["unsupported"] == [], r2
    print("test_lethal_explosion_is_unsupported OK")


if __name__ == "__main__":
    with _Patch():
        test_legal_move_slots_pure()
        test_encore_shared_by_search_endgame_rl_mega()
        test_registration_completion_enters_search()
        test_rl_legal_actions_fallback()
        test_button_label_does_not_fire_mega()
        test_real_mega_messages_still_fire()
        test_split_self_cost_moves()
        test_explosion_is_not_repeated_in_endgame()
        test_lethal_explosion_is_unsupported()
    print("ALL OK")
