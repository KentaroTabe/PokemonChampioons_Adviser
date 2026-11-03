"""I-10 の合成の観測状態 (live の BattleStateV2.to_dict の形) を作って標準出力に JSON で書く (i10_state.json の生成元、768bae44)。

    PYTHONPATH=. python tests/fixtures/inheritance/make_i10_state.py > tests/fixtures/inheritance/i10_state.json
"""
import json
import sys

from vision.state import BattleStateV2, PokemonState, MoveSlot

T0 = 1_792_000_000.0


def mon(sid, ja, types, moves=(), **kw):
    p = PokemonState(species_ja=ja, species_id=sid, types=list(types))
    p.moves = [MoveSlot(name_ja=a, move_id=b, pp=pp, max_pp=mx) for a, b, pp, mx in moves]
    for k, v in kw.items():
        setattr(p, k, v)
    return p


st = BattleStateV2()
st.scene = "command"
st.turn = 4
st.command_no = 37
st.battle_active = True
st.selection_picked = 3
st.mega_used = {"player": True, "opponent": False}
st.field.weather, st.field.weather_turns = "rain", 3
st.field.terrain = None
st.player.party = [
    mon("lopunnymega", "ミミロップ", ["ノーマル", "かくとう"],
        [("インファイト", "closecombat", 6, 8), ("つるぎのまい", "swordsdance", 30, 32),
         ("トリプルアクセル", "tripleaxel", 15, 16), ("かみなりパンチ", "thunderpunch", 22, 24)],
        hp_percent=62.0, hp_current=87, hp_max=140, hp_read_ts=T0 - 3.0, is_mega=True, item_id="lopunnite",
        item_ja="ミミロップナイト", ability_id="scrappy", ability_ja="きもったま", is_active=True, is_picked=True, pick_order=1,
        boosts={"atk": 2, "def": 0, "spa": 0, "spd": 0, "spe": 0, "acc": 0, "eva": 0}, entry_turn=2,
        volatiles=["encore"], encore_move="swordsdance", volatile_turns={"encore": 2}, display_name="ミミロップ"),
    mon("slurpuff", "ペロリーム", ["フェアリー"],
        [("ひかりのかべ", "lightscreen", 30, 32), ("ミストバースト", "mistyexplosion", 5, 8), ("あくび", "yawn", 10, 16),
         ("ねばねばネット", "stickyweb", 20, 32)],
        hp_percent=18.5, hp_current=28, hp_max=151, hp_uncertain=True, hp_estimated=True, hp_source="bar",
        hp_read_ts=T0 - 40.0, status="paralysis", ability_id="sweetveil", item_id="mentalherb", item_consumed=True,
        is_picked=True, pick_order=2),
    mon("baxcalibur", "セグレイブ", ["こおり", "ドラゴン"], [("つららばり", "iciclespear", 30, 32)],
        hp_percent=0.0, status="fainted", is_picked=True, pick_order=3),
]
st.player.active_index, st.player.remaining = 0, 2
st.player.reflect, st.player.reflect_turns = True, 3
st.opponent.party = [
    mon("pawmot", "パーモット", ["でんき", "かくとう"], hp_percent=55.0, hp_read_ts=T0 - 2.0, revealed_moves=["マッハパンチ"],
        is_active=True, item_removed=True, boosts={"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": -1, "acc": 0, "eva": 0},
        last_seen_ts=T0 - 1.0, type_changed=False, entry_turn=3),
    mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], species_guess=True, guess_score=0.81),
    mon("slowking", "ヤドキング", ["みず", "エスパー"], species_candidates=["slowking", "slowkinggalar"]),
    mon(None, None, ["ほのお"]),
    mon("raichu", "ライチュウ", ["でんき"], hp_percent=0.0, status="fainted"),
    mon("kingambit", "ドドゲザン", ["あく", "はがね"]),
    mon("dragonite", "カイリュー", ["ドラゴン", "ひこう"], pending=True, t_first_seen=T0 - 30.0,
        species_candidates=[]),
]
st.opponent.active_index, st.opponent.remaining = 0, 2
st.opponent.stealth_rock = True
st.opponent.tailwind = True
st.last_move = {"player": "swordsdance", "opponent": "machpunch"}
st.moved_turn = {"player": 3, "opponent": 3}
st.protect_streak = {"player": 0, "opponent": 1}
st.pending_pivot_switch = False
st.battle_seq = 7
d = st.to_dict()
d["now"] = T0
json.dump(d, sys.stdout, ensure_ascii=False, indent=1)
