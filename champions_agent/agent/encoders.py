"""ポケモン/技/盤面を固定長特徴ベクトルへ変換するエンコーダ群。

主役は encode_battle(battle): poke-env の AbstractBattle から直接、
技・タイプ相性・ランク・場の状態まで含む観測ベクトルを構築する
(DBアクセスなし。学習ループのホットパスで呼ばれるため)。

旧来の encode_own_pokemon / encode_opponent_pokemon / encode_field は
選出方策 (policy_selection) 用に残置している (DB参照ベース)。
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from champions_agent.agent.spaces import (
    POKEMON_FEATURE_DIM, OPPONENT_POKEMON_FEATURE_DIM, FIELD_FEATURE_DIM,
    BATTLE_OBS_DIM, N_MOVE_SLOTS, MOVE_FEAT_DIM, MOVE_EFFECT_DIM,
)

ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison",
    "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark",
    "steel", "fairy",
]
TYPE_TO_IDX = {t: i for i, t in enumerate(ALL_TYPES)}

STATUS_LIST = ["none", "brn", "par", "slp", "frz", "psn", "tox"]
STATUS_TO_IDX = {s: i for i, s in enumerate(STATUS_LIST)}

BOOST_KEYS = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"]
BASE_STAT_KEYS = ["hp", "atk", "def", "spa", "spd", "spe"]


# ==============================================================================
# battleオブジェクトベースの新エンコーダ (学習・推論の本線)
# ==============================================================================
def _types_multihot(pokemon) -> np.ndarray:
    vec = np.zeros(len(ALL_TYPES), dtype=np.float32)
    try:
        for t in (pokemon.types or []):
            if t is None:
                continue
            name = t.name.lower()
            if name in TYPE_TO_IDX:
                vec[TYPE_TO_IDX[name]] = 1.0
    except Exception:
        pass
    return vec


def _base_stats_vec(pokemon) -> np.ndarray:
    vec = np.zeros(len(BASE_STAT_KEYS), dtype=np.float32)
    try:
        bs = pokemon.base_stats or {}
        for i, k in enumerate(BASE_STAT_KEYS):
            vec[i] = (bs.get(k) or 0) / 200.0
    except Exception:
        pass
    return vec


def _boosts_vec(pokemon) -> np.ndarray:
    vec = np.zeros(len(BOOST_KEYS), dtype=np.float32)
    try:
        boosts = pokemon.boosts or {}
        for i, k in enumerate(BOOST_KEYS):
            vec[i] = (boosts.get(k) or 0) / 6.0
    except Exception:
        pass
    return vec


def _status_vec(pokemon) -> np.ndarray:
    vec = np.zeros(len(STATUS_LIST), dtype=np.float32)
    idx = 0
    try:
        if pokemon.status is not None:
            idx = STATUS_TO_IDX.get(pokemon.status.name.lower(), 0)
    except Exception:
        pass
    vec[idx] = 1.0
    return vec


def _hp_frac(pokemon) -> float:
    try:
        return float(pokemon.current_hp_fraction or 0.0)
    except Exception:
        return 0.0


# タイプ無効化特性 (使用率上位: ふゆう900/もらいび358等)。
# 特性が確定している場合のみ相性を0にする
IMMUNITY_ABILITIES = {
    "levitate": "ground", "eartheater": "ground",
    "flashfire": "fire", "wellbakedbody": "fire",
    "waterabsorb": "water", "stormdrain": "water", "dryskin": "water",
    "voltabsorb": "electric", "lightningrod": "electric",
    "motordrive": "electric",
    "sapsipper": "grass", "windrider": "flying",
}

# 天候で素早さが倍になる特性
WEATHER_SPEED_ABILITIES = {
    "swiftswim": "rain", "chlorophyll": "sun",
    "sandrush": "sand", "slushrush": "snow",
}


def _known_ability(pokemon) -> Optional[str]:
    """確定している特性ID (判明済み or 種族の可能特性が1つのみ)"""
    try:
        ab = getattr(pokemon, "ability", None)
        if ab:
            return str(ab).lower().replace(" ", "")
        poss = getattr(pokemon, "possible_abilities", None) or {}
        vals = list({str(v).lower().replace(" ", "")
                     for v in (poss.values() if isinstance(poss, dict) else poss)})
        if len(vals) == 1:
            return vals[0]
    except Exception:
        pass
    return None


def _possible_abilities(pokemon) -> set:
    try:
        ab = getattr(pokemon, "ability", None)
        if ab:
            return {str(ab).lower().replace(" ", "")}
        poss = getattr(pokemon, "possible_abilities", None) or {}
        return {str(v).lower().replace(" ", "")
                for v in (poss.values() if isinstance(poss, dict) else poss)}
    except Exception:
        return set()


def _eff_mult(defender, move_or_type) -> float:
    """タイプ相性倍率 (0/0.25/0.5/1/2/4)。取得失敗時は1.0。

    防御側の確定特性による無効化 (ふゆう=じめん無効等) を反映する
    """
    try:
        mult = float(defender.damage_multiplier(move_or_type))
        if mult > 0:
            tname = None
            t = getattr(move_or_type, "type", move_or_type)
            tname = getattr(t, "name", None)
            if tname:
                ab = _known_ability(defender)
                if ab and IMMUNITY_ABILITIES.get(ab) == tname.lower():
                    return 0.0
        return mult
    except Exception:
        return 1.0


def _encode_move(move, opponent) -> np.ndarray:
    """1技分の特徴量 (MOVE_FEAT_DIM=9)"""
    vec = np.zeros(MOVE_FEAT_DIM, dtype=np.float32)
    if move is None:
        return vec
    try:
        power = float(move.base_power or 0)
        acc = float(move.accuracy if move.accuracy is not None else 1.0)
        if acc > 1.0:
            acc /= 100.0
        cat = move.category.name.lower() if move.category else "status"
        pp_frac = 0.0
        if move.max_pp:
            pp_frac = max(0.0, float(move.current_pp or 0) / move.max_pp)
        eff = _eff_mult(opponent, move) if (opponent is not None and power > 0) else 1.0

        vec[0] = min(power, 150.0) / 150.0
        vec[1] = acc
        vec[2] = (float(move.priority or 0) + 5.0) / 10.0
        vec[3] = 1.0 if cat == "physical" else 0.0
        vec[4] = 1.0 if cat == "special" else 0.0
        vec[5] = 1.0 if cat == "status" else 0.0
        vec[7] = eff / 4.0
        vec[8] = pp_frac
    except Exception:
        return vec
    return vec


def _encode_active(pokemon, opponent, with_moves: bool) -> np.ndarray:
    """場に出ているポケモンの特徴量。

    with_moves=True: 技4スロット分の特徴 (自分側。poke-envの行動indexと同じ
    `list(pokemon.moves.values())` 順で並べる)
    """
    parts = [
        _types_multihot(pokemon),
        _base_stats_vec(pokemon),
        _boosts_vec(pokemon),
        np.array([_hp_frac(pokemon)], dtype=np.float32),
        _status_vec(pokemon),
    ]
    if with_moves:
        moves = []
        try:
            moves = list(pokemon.moves.values())
        except Exception:
            pass
        for i in range(N_MOVE_SLOTS):
            mv = moves[i] if i < len(moves) else None
            stab_vec = _encode_move(mv, opponent)
            # STABフラグ (index 6) はここで付与
            try:
                if mv is not None and mv.type is not None and pokemon.types:
                    if any(t is not None and t.name == mv.type.name for t in pokemon.types):
                        stab_vec[6] = 1.0
            except Exception:
                pass
            parts.append(stab_vec)
    return np.concatenate(parts)


def _encode_bench_slot(pokemon, with_seen_flag: bool) -> np.ndarray:
    """控え1体分: タイプ18 + HP1 + ひんし1 (+視認済み1)"""
    dim = 20 + (1 if with_seen_flag else 0)
    vec = np.zeros(dim, dtype=np.float32)
    if pokemon is None:
        return vec
    vec[:18] = _types_multihot(pokemon)
    vec[18] = _hp_frac(pokemon)
    try:
        vec[19] = 1.0 if pokemon.fainted else 0.0
    except Exception:
        pass
    if with_seen_flag:
        vec[20] = 1.0
    return vec


def _side_conditions_vec(conditions) -> np.ndarray:
    """陣営の場: [SR, まきびし層/3, どくびし層/2, ネット, リフレクター, 光の壁, ベール, おいかぜ]"""
    vec = np.zeros(8, dtype=np.float32)
    try:
        for cond, value in (conditions or {}).items():
            name = cond.name.lower()
            if name == "stealth_rock":
                vec[0] = 1.0
            elif name == "spikes":
                vec[1] = min(int(value or 1), 3) / 3.0
            elif name == "toxic_spikes":
                vec[2] = min(int(value or 1), 2) / 2.0
            elif name == "sticky_web":
                vec[3] = 1.0
            elif name == "reflect":
                vec[4] = 1.0
            elif name == "light_screen":
                vec[5] = 1.0
            elif name == "aurora_veil":
                vec[6] = 1.0
            elif name == "tailwind":
                vec[7] = 1.0
    except Exception:
        pass
    return vec


def _field_vec(battle) -> np.ndarray:
    """天候4 + フィールド4 + トリックルーム1 + ターン1"""
    vec = np.zeros(10, dtype=np.float32)
    try:
        for w in (battle.weather or {}):
            name = w.name.lower()
            if "sand" in name:
                vec[0] = 1.0
            elif "rain" in name:
                vec[1] = 1.0
            elif "sun" in name:
                vec[2] = 1.0
            elif "hail" in name or "snow" in name:
                vec[3] = 1.0
    except Exception:
        pass
    try:
        for f in (battle.fields or {}):
            name = f.name.lower()
            if "electric" in name:
                vec[4] = 1.0
            elif "grassy" in name:
                vec[5] = 1.0
            elif "psychic" in name:
                vec[6] = 1.0
            elif "misty" in name:
                vec[7] = 1.0
            elif "trick_room" in name:
                vec[8] = 1.0
    except Exception:
        pass
    try:
        vec[9] = min(int(battle.turn or 0), 50) / 50.0
    except Exception:
        pass
    return vec


# v2拡張観測用の定義
VOLATILE_EFFECTS = ["confusion", "leech_seed", "substitute", "taunt",
                    "encore", "yawn"]
ITEM_CATEGORIES = ["choicescarf", "choiceband", "choicespecs",
                   "lifeorb", "leftovers"]  # 6枠目=その他判明


def _volatiles_vec(pokemon) -> np.ndarray:
    """揮発状態6フラグ (VOLATILE_EFFECTS 順)"""
    vec = np.zeros(len(VOLATILE_EFFECTS), dtype=np.float32)
    try:
        for eff in (pokemon.effects or {}):
            name = eff.name.lower()
            if name in VOLATILE_EFFECTS:
                vec[VOLATILE_EFFECTS.index(name)] = 1.0
    except Exception:
        pass
    return vec


def _item_vec(pokemon) -> np.ndarray:
    """持ち物カテゴリ6: こだわり3種/珠/残飯/その他判明 (不明は全0)"""
    vec = np.zeros(6, dtype=np.float32)
    try:
        item = pokemon.item
        if item and item not in ("unknown_item",):
            iid = str(item).lower().replace(" ", "")
            if iid in ITEM_CATEGORIES:
                vec[ITEM_CATEGORIES.index(iid)] = 1.0
            else:
                vec[5] = 1.0
    except Exception:
        pass
    return vec


def _best_move_eff(attacker, defender) -> float:
    """attackerの判明技のうちdefenderに最も通る相性倍率 (攻撃技のみ)"""
    best = 0.0
    try:
        for mv in attacker.moves.values():
            if (mv.base_power or 0) > 0:
                best = max(best, _eff_mult(defender, mv))
    except Exception:
        pass
    return best


def _stab_threat_eff(attacker, defender) -> float:
    """attackerのタイプ一致打点がdefenderへ通る最大倍率 (技非依存の脅威推定)"""
    best = 0.0
    try:
        for t in (attacker.types or []):
            if t is not None:
                best = max(best, _eff_mult(defender, t))
    except Exception:
        pass
    return best


def _mega_in_team(team) -> bool:
    try:
        return any("mega" in (p.species or "") for p in team.values())
    except Exception:
        return False


def _move_self_boosts(move) -> dict:
    """技が自分に与えるランク変化 {stat: 段数} (副次効果は確率加重)"""
    out: dict = {}
    if move is None:
        return out
    try:
        boosts = getattr(move, "boosts", None) or {}
        target = str(getattr(move, "target", "") or "").lower()
        if "self" in target:
            for k, v in boosts.items():
                out[k] = out.get(k, 0.0) + v
        sb = getattr(move, "self_boost", None) or {}
        items = (sb.get("boosts") or {}).items() if "boosts" in sb else sb.items()
        for k, v in items:
            out[k] = out.get(k, 0.0) + v
        sec_raw = getattr(move, "secondary", None)
        secs = sec_raw if isinstance(sec_raw, list) else ([sec_raw] if sec_raw else [])
        for sec in secs:
            chance = float(sec.get("chance") or 100) / 100.0
            for k, v in ((sec.get("self") or {}).get("boosts") or {}).items():
                out[k] = out.get(k, 0.0) + v * chance
    except Exception:
        pass
    return out


def _has_contrary(pokemon) -> bool:
    """あまのじゃく (ランク変化反転) 持ちか。

    特性が判明していればそれを、未判明でも種族の可能特性が
    あまのじゃくのみ (メガムクホーク等) なら真とみなす
    """
    if pokemon is None:
        return False
    try:
        ab = getattr(pokemon, "ability", None)
        if ab:
            return str(ab).lower().replace(" ", "") == "contrary"
        poss = getattr(pokemon, "possible_abilities", None) or {}
        vals = [str(v).lower().replace(" ", "")
                for v in (poss.values() if isinstance(poss, dict) else poss)]
        return bool(vals) and all(v == "contrary" for v in vals)
    except Exception:
        return False


def _move_effect_vec(move, contrary: bool = False,
                     target_contrary: bool = False) -> np.ndarray:
    """技の付随効果8次元:
    [A/B/C/D/S別の符号付き自己ブースト(各/2), 相手ランク低下, 状態異常率, 回復率]

    合計スカラーではなくステータス別に分解する: りゅうのまい[A+S]と
    てっぺき[B]は価値の文脈 (自分の型・相手の攻撃プロファイル) が異なる。
    contrary=使用者があまのじゃくなら自己ブーストの符号を反転
    (メガムクホークのインファイト=B/D上昇)。target_contrary=対象が
    あまのじゃくなら相手ランク低下は逆に強化になるため0にする
    """
    from champions_agent.agent.spaces import BOOST_STAT_KEYS
    vec = np.zeros(MOVE_EFFECT_DIM, dtype=np.float32)
    if move is None:
        return vec
    try:
        self_boosts = _move_self_boosts(move)
        sign = -1.0 if contrary else 1.0
        for i, k in enumerate(BOOST_STAT_KEYS):
            vec[i] = max(-1.0, min(1.0, sign * self_boosts.get(k, 0.0) / 2.0))
        targ_down = 0.0
        boosts = getattr(move, "boosts", None) or {}
        target = str(getattr(move, "target", "") or "").lower()
        if "self" not in target:
            for v in boosts.values():
                targ_down += max(-v, 0)
        status_chance = 1.0 if getattr(move, "status", None) else 0.0
        sec_raw = getattr(move, "secondary", None)
        secs = sec_raw if isinstance(sec_raw, list) else ([sec_raw] if sec_raw else [])
        for sec in secs:
            chance = float(sec.get("chance") or 100) / 100.0
            for v in (sec.get("boosts") or {}).values():
                targ_down += max(-v, 0) * chance
            if sec.get("status"):
                status_chance = max(status_chance, chance)
        heal = 0.0
        h = getattr(move, "heal", None)
        if h:
            heal = float(h[0]) / float(h[1]) if isinstance(h, (list, tuple)) \
                else float(h)
        d = getattr(move, "drain", None)
        if d:
            heal = max(heal, (float(d[0]) / float(d[1]) if
                              isinstance(d, (list, tuple)) else float(d)) * 0.75)
        if target_contrary:
            targ_down = 0.0   # あまのじゃく相手にはデバフが強化になる
        vec[5] = min(targ_down, 4.0) / 4.0
        vec[6] = min(status_chance, 1.0)
        vec[7] = min(heal, 1.0)
    except Exception:
        pass
    return vec


def _attack_profile(pokemon, fallback_stats: bool = True) -> tuple:
    """(物理シェア, 特殊シェア)。判明技の威力加重、なければ種族値A/C比"""
    phys = spec = 0.0
    try:
        for mv in pokemon.moves.values():
            p = float(mv.base_power or 0)
            if p <= 0:
                continue
            cat = mv.category.name.lower() if mv.category else ""
            if cat == "physical":
                phys += p
            elif cat == "special":
                spec += p
    except Exception:
        pass
    if phys + spec <= 0 and fallback_stats:
        try:
            bs = pokemon.base_stats or {}
            phys = float(bs.get("atk") or 0)
            spec = float(bs.get("spa") or 0)
        except Exception:
            pass
    total = phys + spec
    if total <= 0:
        return 0.5, 0.5
    return phys / total, spec / total


def _boost_utility(move, own_phys: float, own_spec: float,
                   opp_phys: float, opp_spec: float,
                   is_slower: bool, contrary: bool = False) -> float:
    """ランク技の文脈つき効用。

    B上げは相手の物理脅威シェア、D上げは特殊脅威シェアで重み付け
    (相手が特殊型ならB上げの効用は0に近づく)。A/C上げは自分の攻撃
    プロファイル、S上げは「相手より遅い」ときに高価値。
    contrary=あまのじゃくなら反転後のブーストで評価する
    (インファイトのB/D上昇が効用として観測される)
    """
    sb = _move_self_boosts(move)
    if not sb:
        return 0.0
    sign = -1.0 if contrary else 1.0
    w = {"atk": own_phys, "spa": own_spec,
         "def": opp_phys, "spd": opp_spec,
         "spe": 0.9 if is_slower else 0.2}
    util = sum(max(sign * v, 0.0) * w.get(k, 0.3) for k, v in sb.items())
    return min(util, 4.0) / 4.0


def _field_remaining_vec(battle) -> np.ndarray:
    """天候/フィールドの残りターン概算 (/8)。正確な残数は岩/持ち物依存の
    ため、開始からの経過で 8-経過 を上限推定する"""
    vec = np.zeros(2, dtype=np.float32)
    try:
        turn = int(battle.turn or 0)
        for _w, start in (battle.weather or {}).items():
            vec[0] = max(0.0, 8.0 - (turn - int(start or turn))) / 8.0
            break
        for _f, start in (battle.fields or {}).items():
            name = _f.name.lower()
            if "trick_room" in name:
                continue
            vec[1] = max(0.0, 8.0 - (turn - int(start or turn))) / 8.0
            break
    except Exception:
        pass
    return vec


def encode_battle(battle) -> np.ndarray:
    """AbstractBattle -> 固定長観測ベクトル (BATTLE_OBS_DIM)"""
    own = battle.active_pokemon
    opp = battle.opponent_active_pokemon

    # --- 自分の場のポケモン (技情報つき) ---
    if own is not None:
        own_vec = _encode_active(own, opp, with_moves=True)
    else:
        own_vec = np.zeros(18 + 6 + 7 + 1 + 7 + N_MOVE_SLOTS * MOVE_FEAT_DIM,
                           dtype=np.float32)

    # --- 相手の場のポケモン + 判明技情報 ---
    if opp is not None:
        opp_vec = _encode_active(opp, None, with_moves=False)
        revealed = []
        try:
            revealed = list(opp.moves.values())
        except Exception:
            pass
        max_eff = 0.0
        if own is not None:
            for mv in revealed:
                if (mv.base_power or 0) > 0:
                    max_eff = max(max_eff, _eff_mult(own, mv))
        opp_extra = np.array([min(len(revealed), 4) / 4.0, max_eff / 4.0],
                             dtype=np.float32)
    else:
        opp_vec = np.zeros(18 + 6 + 7 + 1 + 7, dtype=np.float32)
        opp_extra = np.zeros(2, dtype=np.float32)

    # --- 控え (自分: 最大2体 / 相手: 最大2体 + 視認フラグ + 残数) ---
    own_bench = []
    try:
        own_bench = [p for p in battle.team.values() if own is None or p is not own]
    except Exception:
        pass
    own_bench_vecs = [_encode_bench_slot(own_bench[i] if i < len(own_bench) else None,
                                          with_seen_flag=False) for i in range(2)]

    opp_bench = []
    try:
        opp_bench = [p for p in battle.opponent_team.values()
                     if opp is None or p is not opp]
    except Exception:
        pass
    opp_bench_vecs = [_encode_bench_slot(opp_bench[i] if i < len(opp_bench) else None,
                                          with_seen_flag=True) for i in range(2)]
    try:
        opp_remaining = sum(1 for p in battle.opponent_team.values() if not p.fainted)
    except Exception:
        opp_remaining = 3
    opp_count_vec = np.array([opp_remaining / 3.0], dtype=np.float32)

    # --- 場 (自陣営/敵陣営/全体) ---
    my_side = _side_conditions_vec(getattr(battle, "side_conditions", None))
    opp_side = _side_conditions_vec(getattr(battle, "opponent_side_conditions", None))
    field = _field_vec(battle)

    # --- 素早さ比較 (天候特性すいすい等 + こだわりスカーフを反映) ---
    def _speed_mult(pokemon) -> float:
        mult = 1.0
        try:
            ab = _known_ability(pokemon)
            if ab in WEATHER_SPEED_ABILITIES:
                need = WEATHER_SPEED_ABILITIES[ab]
                for w in (battle.weather or {}):
                    wn = w.name.lower()
                    if (need == "rain" and "rain" in wn) or \
                       (need == "sun" and "sun" in wn) or \
                       (need == "sand" and "sand" in wn) or \
                       (need == "snow" and ("snow" in wn or "hail" in wn)):
                        mult *= 2.0
            item = getattr(pokemon, "item", None)
            if item and "choicescarf" in str(item).lower().replace(" ", ""):
                mult *= 1.5
            try:
                if pokemon.status is not None and \
                        pokemon.status.name.lower() == "par":
                    mult *= 0.5
            except Exception:
                pass
        except Exception:
            pass
        return mult

    speed_vec = np.zeros(2, dtype=np.float32)
    try:
        my_spe = ((own.stats or {}).get("spe")
                  or (own.base_stats or {}).get("spe") or 0) * _speed_mult(own)
        opp_spe = ((opp.base_stats or {}).get("spe") or 0) * _speed_mult(opp)
        if opp_spe > 0:
            ratio = my_spe / opp_spe
            speed_vec[0] = min(ratio, 2.0) / 2.0
            speed_vec[1] = 1.0 if ratio >= 1.0 else 0.0
    except Exception:
        pass

    # ================= v2拡張観測 (末尾追記。v1プレフィックスは不変) =========
    # 相手の判明技4スロット (自分を防御側とした技特徴 + 相手視点STAB)
    opp_move_vecs = []
    opp_revealed = []
    if opp is not None:
        try:
            opp_revealed = list(opp.moves.values())
        except Exception:
            pass
    for i in range(N_MOVE_SLOTS):
        mv = opp_revealed[i] if i < len(opp_revealed) else None
        mvec = _encode_move(mv, own)
        try:
            if mv is not None and mv.type is not None and opp is not None and opp.types:
                if any(t is not None and t.name == mv.type.name for t in opp.types):
                    mvec[6] = 1.0
        except Exception:
            pass
        opp_move_vecs.append(mvec)

    # 揮発状態 (自分/相手)
    own_vol = _volatiles_vec(own) if own is not None else np.zeros(6, dtype=np.float32)
    opp_vol = _volatiles_vec(opp) if opp is not None else np.zeros(6, dtype=np.float32)

    # メガ進化 (1試合1回の権利の管理)
    mega_vec = np.zeros(3, dtype=np.float32)
    try:
        mega_vec[0] = 1.0 if getattr(battle, "can_mega_evolve", False) else 0.0
    except Exception:
        pass
    mega_vec[1] = 1.0 if _mega_in_team(getattr(battle, "team", {}) or {}) else 0.0
    mega_vec[2] = 1.0 if _mega_in_team(
        getattr(battle, "opponent_team", {}) or {}) else 0.0

    # 持ち物カテゴリ (自分/相手判明分)
    own_item = _item_vec(own) if own is not None else np.zeros(6, dtype=np.float32)
    opp_item = _item_vec(opp) if opp is not None else np.zeros(6, dtype=np.float32)

    # 自分の残数 + 相手判明技の最大優先度
    misc = np.zeros(2, dtype=np.float32)
    try:
        misc[0] = sum(1 for p in battle.team.values() if not p.fainted) / 3.0
    except Exception:
        misc[0] = 1.0
    try:
        pri = max((float(mv.priority or 0) for mv in opp_revealed), default=0.0)
        misc[1] = (pri + 5.0) / 10.0
    except Exception:
        misc[1] = 0.5

    # 控えの戦術情報 (交代判断用):
    # 自分控え: その子の打点が相手アクティブへ通るか / 相手STABをどれだけ受けるか
    bench_tactics = np.zeros(6, dtype=np.float32)
    for i in range(2):
        b = own_bench[i] if i < len(own_bench) else None
        if b is not None and opp is not None:
            bench_tactics[i * 2] = _best_move_eff(b, opp) / 4.0
            bench_tactics[i * 2 + 1] = _stab_threat_eff(opp, b) / 4.0
    # 相手控え: 自分アクティブの打点がその子へ通るか
    for i in range(2):
        b = opp_bench[i] if i < len(opp_bench) else None
        if b is not None and own is not None:
            bench_tactics[4 + i] = _best_move_eff(own, b) / 4.0

    # ========== v3拡張 (技効果/脅威プロファイル/効用/天候残り/控え同士) ======
    own_moves_list = []
    if own is not None:
        try:
            own_moves_list = list(own.moves.values())
        except Exception:
            pass
    # あまのじゃく (ランク反転) は技効果の意味を根本的に変えるため反映する
    own_contrary = _has_contrary(own)
    opp_contrary = _has_contrary(opp)
    own_move_effects = [
        _move_effect_vec(own_moves_list[i] if i < len(own_moves_list) else None,
                         contrary=own_contrary, target_contrary=opp_contrary)
        for i in range(N_MOVE_SLOTS)]
    opp_move_effects = [
        _move_effect_vec(opp_revealed[i] if i < len(opp_revealed) else None,
                         contrary=opp_contrary, target_contrary=own_contrary)
        for i in range(N_MOVE_SLOTS)]

    # 攻撃プロファイル: 相手の物理/特殊脅威シェア + 自分の物理/特殊シェア
    opp_phys, opp_spec = _attack_profile(opp) if opp is not None else (0.5, 0.5)
    own_phys, own_spec = _attack_profile(own) if own is not None else (0.5, 0.5)
    profile_vec = np.array([opp_phys, opp_spec, own_phys, own_spec],
                           dtype=np.float32)

    # 自分の各技のランク技効用 (文脈重み付き。speed_vec[1]=1なら自分が速い)
    is_slower = speed_vec[1] < 0.5
    utility_vec = np.array([
        _boost_utility(own_moves_list[i] if i < len(own_moves_list) else None,
                       own_phys, own_spec, opp_phys, opp_spec, is_slower,
                       contrary=own_contrary)
        for i in range(N_MOVE_SLOTS)], dtype=np.float32)

    field_remaining = _field_remaining_vec(battle)

    # 自分控え2 x 相手控え2: 突破後の詰め筋 (自分の控えの打点が相手の控えに通るか)
    bench_matchup = np.zeros(4, dtype=np.float32)
    for i in range(2):
        mb = own_bench[i] if i < len(own_bench) else None
        for j in range(2):
            ob = opp_bench[j] if j < len(opp_bench) else None
            if mb is not None and ob is not None:
                bench_matchup[i * 2 + j] = _best_move_eff(mb, ob) / 4.0

    # ========== v4拡張: 環境使用率上位の特殊要素フラグ ======================
    def _guard_flag(pokemon) -> float:
        """次の一撃を無効/確定耐えする可能性:
        ばけのかわ未破壊 (HP不問) / 満タン+きあいのタスキ / 満タン+がんじょう
        """
        if pokemon is None:
            return 0.0
        try:
            # ミミッキュのばけのかわ: 破壊されるとフォルムが mimikyubusted になる
            species = str(getattr(pokemon, "species", "") or "").lower()
            if "mimikyu" in species and "busted" not in species \
                    and not pokemon.fainted:
                return 1.0
        except Exception:
            pass
        if _hp_frac(pokemon) < 0.999:
            return 0.0
        try:
            item = str(getattr(pokemon, "item", "") or "").lower().replace(" ", "")
            if "focussash" in item:
                return 1.0
        except Exception:
            pass
        return 1.0 if "sturdy" in _possible_abilities(pokemon) else 0.0

    def _side_has_ability(team, ability: str) -> float:
        try:
            for p in (team or {}).values():
                if not p.fainted and ability in _possible_abilities(p):
                    return 1.0
        except Exception:
            pass
        return 0.0

    # 連続まもるカウンタ (成功率減衰の学習用。poke-envが追跡)
    def _protect_count(pokemon) -> float:
        try:
            return min(int(getattr(pokemon, "protect_counter", 0) or 0), 3) / 3.0
        except Exception:
            return 0.0

    protect_vec = np.array([_protect_count(own), _protect_count(opp)],
                           dtype=np.float32)

    special_vec = np.array([
        _guard_flag(own),
        _guard_flag(opp),
        _side_has_ability(getattr(battle, "team", {}), "intimidate"),
        _side_has_ability(getattr(battle, "opponent_team", {}), "intimidate"),
        1.0 if own is not None and "prankster" in _possible_abilities(own) else 0.0,
        1.0 if opp is not None and "prankster" in _possible_abilities(opp) else 0.0,
    ], dtype=np.float32)

    vec = np.concatenate([own_vec, opp_vec, opp_extra,
                          *own_bench_vecs, *opp_bench_vecs, opp_count_vec,
                          my_side, opp_side, field, speed_vec,
                          *opp_move_vecs, own_vol, opp_vol, mega_vec,
                          own_item, opp_item, misc,
                          bench_tactics,
                          *own_move_effects, *opp_move_effects,
                          profile_vec, utility_vec,
                          field_remaining, bench_matchup,
                          special_vec, protect_vec]).astype(np.float32)

    # 固定長を保証
    if len(vec) < BATTLE_OBS_DIM:
        padded = np.zeros(BATTLE_OBS_DIM, dtype=np.float32)
        padded[:len(vec)] = vec
        return padded
    return vec[:BATTLE_OBS_DIM]


# ==============================================================================
# 旧エンコーダ (選出方策 policy_selection 用・DB参照ベース)
# ==============================================================================
from champions_agent.data import database as db


def _type_onehot(type_name: str | None) -> np.ndarray:
    vec = np.zeros(len(ALL_TYPES), dtype=np.float32)
    if type_name and type_name in TYPE_TO_IDX:
        vec[TYPE_TO_IDX[type_name]] = 1.0
    return vec


def _normalize_stat(v: int | None, scale: float = 255.0) -> float:
    return (v or 0) / scale


def get_pokemon_static(name: str) -> dict | None:
    """DBから種族値/タイプ等の静的データを引く。未収集の場合はNoneを返す。"""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pokemon WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None


def encode_own_pokemon(name: str, hp_percent: float, status: str = "none") -> np.ndarray:
    """自分側ポケモン(完全情報)を固定長ベクトルへ変換する。"""
    static = get_pokemon_static(name) or {}

    stat_vec = np.array([
        _normalize_stat(static.get("hp")),
        _normalize_stat(static.get("attack")),
        _normalize_stat(static.get("defense")),
        _normalize_stat(static.get("sp_attack")),
        _normalize_stat(static.get("sp_defense")),
        _normalize_stat(static.get("speed")),
    ], dtype=np.float32)

    type1_vec = _type_onehot(static.get("type1"))
    type2_vec = _type_onehot(static.get("type2"))

    status_vec = np.zeros(len(STATUS_LIST), dtype=np.float32)
    status_vec[STATUS_TO_IDX.get(status, 0)] = 1.0

    hp_vec = np.array([hp_percent], dtype=np.float32)

    vec = np.concatenate([stat_vec, type1_vec, type2_vec, status_vec, hp_vec])
    return _pad_or_trim(vec, POKEMON_FEATURE_DIM)


def encode_opponent_pokemon(name: str | None, hp_percent: float = 1.0,
                             status: str = "none",
                             fmt: str = "gen9ou", source: str = "dummy") -> np.ndarray:
    """相手側ポケモンを、見えている情報で埋めたベクトルへ変換する。"""
    static = get_pokemon_static(name) if name else None
    static = static or {}

    stat_vec = np.array([
        _normalize_stat(static.get("hp")),
        _normalize_stat(static.get("attack")),
        _normalize_stat(static.get("defense")),
        _normalize_stat(static.get("sp_attack")),
        _normalize_stat(static.get("sp_defense")),
        _normalize_stat(static.get("speed")),
    ], dtype=np.float32)

    type1_vec = _type_onehot(static.get("type1"))
    type2_vec = _type_onehot(static.get("type2"))

    status_vec = np.zeros(len(STATUS_LIST), dtype=np.float32)
    status_vec[STATUS_TO_IDX.get(status, 0)] = 1.0

    hp_vec = np.array([hp_percent], dtype=np.float32)

    vec = np.concatenate([stat_vec, type1_vec, type2_vec, status_vec, hp_vec])
    return _pad_or_trim(vec, OPPONENT_POKEMON_FEATURE_DIM)


def encode_field(weather: str = "none", turn: int = 0) -> np.ndarray:
    weather_list = ["none", "sandstorm", "rain", "sun", "hail", "snow"]
    weather_vec = np.zeros(len(weather_list), dtype=np.float32)
    idx = weather_list.index(weather) if weather in weather_list else 0
    weather_vec[idx] = 1.0
    turn_vec = np.array([min(turn, 100) / 100.0], dtype=np.float32)
    vec = np.concatenate([weather_vec, turn_vec])
    return _pad_or_trim(vec, FIELD_FEATURE_DIM)


def _pad_or_trim(vec: np.ndarray, target_dim: int) -> np.ndarray:
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    padded = np.zeros(target_dim, dtype=np.float32)
    padded[:len(vec)] = vec
    return padded
