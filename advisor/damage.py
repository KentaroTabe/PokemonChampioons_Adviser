"""第9世代 (SV) 準拠のダメージ計算。

ポケモンチャンピオンズはSVの計算式ベース (Lv50固定・個体値31固定)。
主要な補正 (天候/フィールド/壁/やけど/ランク/STAB/特性・持ち物) を実装する。
乱数幅 0.85〜1.00 を考慮し (最小%, 最大%, 平均%) を返す。

2026-10-02 (docs/TEAM_BUILD_REDESIGN_1002.md §7.5 / §9.3): 特性は効果表 advisor/data/ability_effects.json (参戦種の 226 種類、
advisor.effects が評価) を一次情報にし、表に無い特性 (参戦外) だけ従来の辞書で補う。技は advisor/data/move_effects.json から
連続 (期待回数)、1 発ごとの命中判定、確定急所、参照する能力の上書き (ボディプレス / イカサマ / サイコショック)、可変威力
(けたぐり / ジャイロボール / たたりめ 等)、条件 (アイアンローラーのフィールド必須 等)、天候で変わる命中 を取る。
戻り値の min / max / avg は 1 回の使用 (連続技は期待回数分) の割合、expected は命中 (天候・特性込み) まで掛けた期待値。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from advisor.dex import get_dex, calc_hp, calc_stat, BOOST_MULT

DAMAGE_MODIFIERS_PATH = Path(__file__).resolve().parent / "data" / "damage_modifiers.json"


@lru_cache(maxsize=1)
def damage_modifiers() -> dict:
    """整数の計算で使う Showdown の補正値 (advisor/data/damage_modifiers.json)"""
    try:
        return json.loads(DAMAGE_MODIFIERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _integer_rounding_enabled() -> bool:
    from champions_agent import config as _cfg
    return bool(getattr(_cfg, "DAMAGE_INTEGER_ROUNDING", False))


# ------------------------------------------------------------------ Showdown の整数の計算 (純粋)
def _mod4096(mult: float) -> int:
    """倍率 → 4096 分率の整数 (表の 1.3333 / 0.6667 のような近似値も最も近い分率に寄せる)"""
    return int(round(float(mult) * 4096))


def chain_mod(mults) -> int:
    """倍率の列を Showdown の chainModify と同じ規則でつなぐ → 4096 分率 (M'' = (M × M' + 0x800) >> 12)"""
    m = 4096
    for x in mults:
        m = (m * _mod4096(x) + 2048) >> 12
    return m


def poke_round_mod(value: int, mod: int) -> int:
    """Showdown の modify: 4096 分率の倍率を掛けて五捨五超入 (tr((tr(value × mod) + 2048 − 1) / 4096))"""
    return (int(value) * int(mod) + 2047) // 4096


def showdown_rolls(base_damage: int, weather_mod: int, crit_mult: Optional[float], stab_mod: int, type_mult: float,
                   burn: bool, final_mod: int, rnd=(85, 100)) -> list:
    """Showdown の modifyDamage と同じ順序 (+2 の後の値 base_damage から: 天候 → 急所 → 乱数 → タイプ一致 → 相性 → やけど
    → 最終補正 → 最小 1) で、乱数 rnd[0]〜rnd[1] の各値のダメージ (整数の列) を返す。相性が 2 のべき乗なら 2 倍 / 切り捨ての半分を
    繰り返し、それ以外 (画面のヒントの上書き等) は掛けて切り捨てる"""
    d0 = int(base_damage)
    if weather_mod != 4096:
        d0 = poke_round_mod(d0, weather_mod)
    if crit_mult:
        d0 = int(d0 * crit_mult)
    lg = math.log2(type_mult) if type_mult > 0 else None
    pow2 = lg is not None and abs(lg - round(lg)) < 1e-9
    out = []
    for r in range(int(rnd[0]), int(rnd[1]) + 1):
        d = d0 * r // 100
        if stab_mod != 4096:
            d = poke_round_mod(d, stab_mod)
        if pow2:
            k = int(round(lg))
            for _ in range(max(0, k)):
                d *= 2
            for _ in range(max(0, -k)):
                d //= 2
        else:
            d = int(d * type_mult)
        if burn:
            d = poke_round_mod(d, int(damage_modifiers().get("burn", 2048)))
        if final_mod != 4096:
            d = poke_round_mod(d, final_mod)
        out.append(max(1, d))
    return out


@dataclass
class MonView:
    """ダメージ計算用のポケモンビュー (画面抽出状態から構築する)"""
    species_id: str
    name_ja: str = ""
    level: int = 50
    types: list = field(default_factory=list)      # 英語タイプ名
    base: dict = field(default_factory=dict)
    hp_frac: float = 1.0            # 残りHP割合 0..1
    status: Optional[str] = None
    boosts: dict = field(default_factory=dict)
    ability: Optional[str] = None
    item: Optional[str] = None
    # EV仮定 (不明時は攻撃系/耐久系に252振り想定)
    ev: dict = field(default_factory=dict)
    nature: dict = field(default_factory=dict)     # stat -> 0.9/1.0/1.1
    # 先後観測から狭めた実効素早さの範囲 (lower, upper)。
    # effective_speed がこの範囲へクランプする (ev_infer.observe_speed 由来)
    spe_bounds: Optional[tuple] = None

    def stat(self, key: str, ignore_boost: bool = False) -> int:
        b = self.base.get(key, 80)
        if key == "hp":
            return calc_hp(b, self.ev.get("hp", 0), self.level)
        val = calc_stat(b, self.ev.get(key, 0), self.nature.get(key, 1.0), self.level)
        if not ignore_boost:
            val = int(val * BOOST_MULT.get(self.boosts.get(key, 0), 1.0))
        return val

    def max_hp(self) -> int:
        return calc_hp(self.base.get("hp", 80), self.ev.get("hp", 0), self.level)


@dataclass
class FieldView:
    weather: Optional[str] = None       # sun / rain / sandstorm / snow
    terrain: Optional[str] = None       # electric / grassy / psychic / misty
    trick_room: bool = False
    # 防御側の壁
    reflect: bool = False
    light_screen: bool = False
    aurora_veil: bool = False


def _is_grounded(mon: MonView, ignore_ability: bool = False) -> bool:
    """接地判定。ignore_ability=True (かたやぶり) ならふゆうを無視する"""
    if "Flying" in mon.types:
        return False
    if not ignore_ability and mon.ability in ("levitate", "eelevate"):
        return False
    if mon.item == "airballoon":
        return False
    return True


def field_move_effect(move_id: Optional[str], fv: Optional[FieldView], user_grounded: bool = True,
                      target_grounded: bool = True) -> tuple:
    """技固有のフィールド/天候の効果 → (威力倍率, 変化後のタイプ or None, 条件が成立したか)。
    表は advisor/data/field_effects.json の moves (ワイドフォース/ライジングボルト/ダイチノハドウ/ウェザーボール/
    ソーラービーム/じしん (グラスフィールドで半減) 等)。条件外は (1.0, None, False)。
    ソーラービーム系は晴れ以外の天候では other_weather_mult (0.5) 倍で不成立"""
    from advisor.dex import field_effects
    spec = (field_effects().get("moves") or {}).get(move_id or "")
    if not spec or fv is None:
        return 1.0, None, False
    cur = fv.terrain if spec.get("kind") == "terrain" else fv.weather
    cond = spec.get("cond")
    if not cur or not (cond == "any" or cond == cur):
        if cur and spec.get("kind") == "weather" and spec.get("other_weather_mult") is not None:
            return float(spec["other_weather_mult"]), None, False
        return 1.0, None, False
    who = spec.get("grounded", "none")
    if (who == "user" and not user_grounded) or (who == "target" and not target_grounded):
        return 1.0, None, False
    return float(spec.get("mult", 1.0)), (spec.get("types") or {}).get(cur), True


_FLAG_MOVES: dict = {}


def moves_with_flag(flag: str) -> set:
    """技フラグ (slicing/punch等) を持つ技ID集合。効果表 (advisor.effects) を優先し、無ければ poke_env のデータ"""
    if flag not in _FLAG_MOVES:
        from advisor.effects import move_effects
        table = move_effects()
        if table:
            _FLAG_MOVES[flag] = {mid for mid, e in table.items() if flag in (e.get("flags") or ())}
        else:
            try:
                from poke_env.data import GenData
                _FLAG_MOVES[flag] = {
                    mid for mid, m in GenData.from_gen(9).moves.items()
                    if (m.get("flags") or {}).get(flag)}
            except Exception:
                _FLAG_MOVES[flag] = set()
    return _FLAG_MOVES[flag]


def effective_speed(mon: MonView, fieldv: Optional["FieldView"] = None, item_consumed: bool = False) -> int:
    """特性・持ち物・状態異常・天候/フィールドを考慮した実効素早さ。

    すいすい/ようりょくそ等の天候特性を必ず考慮する (先手判定の要)。特性は効果表 (speed_mult) から、表に無い特性は従来の分岐。
    item_consumed = かるわざ の発動 (消費アイテムを使った後) を呼び出し側が知っているとき
    """
    from advisor import effects as E
    fv = fieldv or FieldView()
    spe = mon.stat("spe")
    ab = mon.ability or ""
    if E.ability_known(ab):
        spe = int(spe * E.speed_multiplier(ab, {"weather": fv.weather, "terrain": fv.terrain, "user_status": mon.status,
                                              "item_consumed": item_consumed}))
    else:
        if (ab == "swiftswim" and fv.weather == "rain") or \
           (ab == "chlorophyll" and fv.weather == "sun") or \
           (ab == "sandrush" and fv.weather == "sandstorm") or \
           (ab == "slushrush" and fv.weather == "snow") or \
           (ab == "surgesurfer" and fv.terrain == "electric"):
            spe *= 2
        if ab == "quickfeet" and mon.status:
            spe = int(spe * 1.5)
    if mon.item == "choicescarf":
        spe = int(spe * 1.5)
    if mon.status == "paralysis" and ab != "quickfeet":
        spe = int(spe * 0.5)
    # 先後観測から狭めた範囲へクランプ (最良仮説が観測と矛盾する場合の補正。
    # 例: 仮説S=105でも「自分の112より速かった」観測があれば113以上とする)
    if mon.spe_bounds:
        lo, hi = mon.spe_bounds
        if lo:
            spe = max(spe, int(lo) + 1)
        if hi:
            spe = min(spe, max(1, int(hi) - 1))
    return int(spe)


# --- 効果表に無い特性 (参戦外) のための従来の辞書 (表にある特性はこちらを見ない) ---
# タイプ強化特性: ability -> (タイプ, 倍率)
TYPE_BOOST_ABILITIES = {
    "transistor": ("Electric", 1.3),
    "dragonsmaw": ("Dragon", 1.5),
    "rockypayload": ("Rock", 1.5),
    "steelworker": ("Steel", 1.5),
    "steelyspirit": ("Steel", 1.5),
    "waterbubble": ("Water", 2.0),
}

# ピンチ特性 (HP1/3以下で該当タイプ1.5倍)
PINCH_ABILITIES = {"blaze": "Fire", "torrent": "Water",
                   "overgrow": "Grass", "swarm": "Bug"}

# 天候の表示名 (助言の理由文用)
_WEATHER_JA = {"sun": "晴れ", "rain": "雨", "sandstorm": "砂あらし", "snow": "雪"}

# 攻撃を無効化する特性
IMMUNITY_ABILITIES = {
    "levitate": ("Ground",),
    "flashfire": ("Fire",),
    "waterabsorb": ("Water",),
    "stormdrain": ("Water",),
    "dryskin": ("Water",),
    "voltabsorb": ("Electric",),
    "lightningrod": ("Electric",),
    "motordrive": ("Electric",),
    "sapsipper": ("Grass",),
    "eartheater": ("Ground",),
    "wellbakedbody": ("Fire",),
}
LEGACY_MOLD_BREAKERS = ("moldbreaker", "teravolt", "turboblaze")
# 急所の期待値を掛ける最小の急所率 (確定急所と 1/2 以上だけ。基礎の 1/24 は従来どおり無視する)
CRIT_APPLY_MIN_CHANCE = 0.5


def _effectiveness(dex, move_id: str, mtype: str, def_types) -> float:
    """タイプ相性。技ごとの例外 (フリーズドライはみずに抜群。advisor/data/damage_modifiers.json の type_effectiveness_overrides)
    があれば、そのタイプの分だけ表の倍率に置き換える"""
    ov = (damage_modifiers().get("type_effectiveness_overrides") or {}).get(move_id or "")
    if not ov:
        return dex.effectiveness(mtype, def_types)
    m = 1.0
    for t in def_types:
        m *= float(ov[t]) if t in ov else dex.effectiveness(mtype, [t])
    return m


def type_item_mult(item: Optional[str], move_type: Optional[str]) -> float:
    """タイプ強化の持ち物 (とけないこおり等) の威力倍率 (技のタイプが一致すれば 4915/4096、それ以外 1)"""
    dm = damage_modifiers()
    t = (dm.get("type_items") or {}).get(item or "")
    if not t or str(item).startswith("_") or t != move_type:
        return 1.0
    return int(dm.get("type_item_mod", 4915)) / 4096.0


def _zero(category: str, notes: list) -> dict:
    return {"min": 0.0, "max": 0.0, "avg": 0.0, "expected": 0.0, "type_mult": 0.0, "category": category,
            "hits": 1.0, "accuracy": None, "notes": notes}


def _effective_accuracy(entry: dict, move: dict, fv: FieldView):
    """技の命中 (0..100、None = 必中)。天候で変わる技 (ぼうふう / かみなり / ふぶき) は表から"""
    if entry:
        acc = entry.get("accuracy")
    else:
        acc = move.get("accuracy") or None
    w = fv.weather
    aw = (entry.get("accuracy_weather") or {}) if entry else {}
    key = {"sandstorm": "sand"}.get(w or "", w)
    if w and (w in aw or key in aw):
        v = aw.get(w, aw.get(key))
        acc = None if v is True else v
    return acc


def calc_damage(attacker: MonView, defender: MonView, move_id: str,
                fieldv: Optional[FieldView] = None,
                override_type_mult: Optional[float] = None,
                override_move_type: Optional[str] = None,
                ctx: Optional[dict] = None) -> dict:
    """ダメージを計算して割合 (%表記) で返す。

    戻り値: {"min": %, "max": %, "avg": %, "expected": % (命中込み), "type_mult": x, "category": ..., "hits": 期待回数,
             "accuracy": 実効命中 (None = 必中), "notes": [...]}  計算不能 (変化技等) なら 0
    override_move_type: 条件でタイプが変わる技 (ウェザーボール/ダイチノハドウ) の実際のタイプ。
    ctx: 盤面の文脈 (任意): target_switched_in / moves_last / fainted_allies / times_hit / berry_eaten / target_item
    """
    from advisor import effects as E
    from advisor.dex import field_effects
    dex = get_dex()
    move = dex.move(move_id)
    fv = fieldv or FieldView()
    fe = field_effects()
    notes: list = []
    entry = E.move_entry(move_id)

    if not move or move["category"] == "Status":
        return _zero(move["category"] if move else "Status", [])
    if not move["power"] and not entry.get("variable_power") and not entry.get("fixed_damage"):
        return _zero(move["category"], [])

    mtype = override_move_type or move["type"]
    # 威力は効果表 (move_effects.json、Showdown の mod champions から生成) を優先する。図鑑 (dex.json) は SV の値のままの技がある
    # (2026-10-07 ダメージ照合 1000 手: であいがしら 図鑑 90 / チャンピオンズ 100。食い違いは威力だけで 25 技ほど)
    base_power = float(entry.get("power") or move["power"] or 0)
    power = base_power
    category = move["category"]
    # 整数の計算 (DAMAGE_INTEGER_ROUNDING) 用に、倍率を Showdown の段階ごとに集める (小数の計算の値は変えない)。
    # bp = 威力、atk / def = 実数値、stab = タイプ一致、final = 最終補正、hits = 回数の追加 (小数のまま最後に掛ける)
    stages: dict = {"bp": [], "atk": [], "def": [], "stab": [], "final": [], "hits": []}
    flags = tuple(entry.get("flags") or ())
    atypes = attacker.types or (dex.species(attacker.species_id) or {}).get("types", [])
    dtypes = defender.types or (dex.species(defender.species_id) or {}).get("types", [])
    cx = dict(ctx or {})

    # かたやぶり系: 防御側の特性 (無効化/軽減/てんねん等) をすべて無視する
    a_ab = attacker.ability or ""
    a_known = E.ability_known(a_ab)
    mold = E.has_effect(a_ab, "ignore_target_ability") if a_known else (a_ab in LEGACY_MOLD_BREAKERS)
    if mold:
        notes.append("かたやぶりで相手特性無視")
    d_ab = "" if mold else (defender.ability or "")
    d_known = E.ability_known(d_ab)

    # --- 条件つき技 (アイアンローラーのフィールド必須、ゲップのきのみ 等): 成立しなければ 0 ---
    usable, why = E.move_usable(entry, {"terrain": fv.terrain, "target_status": defender.status, "user_status": attacker.status,
                                        "user_types": atypes, "berry_eaten": cx.get("berry_eaten", False)})
    if not usable:
        return _zero(category, [why])

    # --- タイプ付与の特性 (フェアリースキン等。天候・フィールドでタイプが変わる技は対象外) ---
    if override_move_type is None and not entry.get("field_type"):
        new_type, t_mult = E.type_change(a_ab, mtype, flags)
        if new_type:
            mtype = new_type
            power *= t_mult
            stages["bp"].append(t_mult)
            notes.append(f"特性{a_ab}で{new_type}タイプ")

    # --- 技固有のフィールド/天候の効果 (威力倍率・タイプ変化。表は advisor/data/field_effects.json) ---
    a_grounded = _is_grounded(attacker)
    d_grounded = _is_grounded(defender, ignore_ability=mold)
    f_mult, f_type, _f_active = field_move_effect(move_id, fv, a_grounded, d_grounded)
    if f_type and override_move_type is None:
        mtype = f_type
        notes.append(f"{fv.terrain or fv.weather}で{f_type}タイプ")
    if f_mult != 1.0:
        power *= f_mult
        stages["bp"].append(f_mult)
        notes.append(f"フィールド/天候で威力×{f_mult:g}")

    # --- 可変威力 (計算できる種類だけ) ---
    # 整数の計算の威力の元 (Showdown は威力の式 (basePowerCallback) の後に威力の補正を掛ける)
    power_raw = base_power
    if entry.get("variable_power"):
        a_sp = dex.species(attacker.species_id) or {}
        d_sp = dex.species(defender.species_id) or {}
        vp_ctx = {
            "user_weight": a_sp.get("weightkg"), "target_weight": d_sp.get("weightkg"),
            "user_speed": effective_speed(attacker, fv), "target_speed": effective_speed(defender, fv),
            "user_hp_frac": attacker.hp_frac, "target_hp_frac": defender.hp_frac,
            "target_status": defender.status, "user_status": attacker.status,
            "user_item": attacker.item, "target_item": cx.get("target_item", defender.item or True),
            "user_boost_total": sum(max(0, int(v)) for v in (attacker.boosts or {}).values()),
            "fainted_allies": cx.get("fainted_allies", 0), "times_hit": cx.get("times_hit", 0)}
        power = E.variable_power(entry, power, vp_ctx)
        power_raw = E.variable_power(entry, power_raw, vp_ctx)
        if power <= 0:
            return _zero(category, notes)

    # --- 特性による無効化 ---
    if d_ab:
        immune = E.is_immune(d_ab, mtype, flags, False) if d_known else (mtype in IMMUNITY_ABILITIES.get(d_ab, ()))
        if immune:
            return _zero(category, [f"特性{d_ab}で無効"])

    # --- タイプ相性 ---
    if override_type_mult is not None:
        type_mult = override_type_mult
    else:
        eff_types = list(dtypes)
        if "Ghost" in eff_types and mtype in ("Normal", "Fighting") and E.has_effect(a_ab, "hit_ghost_with_normal_fighting"):
            eff_types = [t for t in eff_types if t != "Ghost"]
            notes.append("きもったまでゴーストに当たる")
        type_mult = _effectiveness(dex, move_id, mtype, eff_types)
        if mtype == "Ground" and not d_grounded:
            type_mult = 0.0
    if type_mult == 0.0:
        return _zero(category, ["無効"])

    # --- 固定ダメージ (ナイトヘッド / ちきゅうなげ) ---
    hp = defender.max_hp()
    if entry.get("fixed_damage"):
        fd = entry["fixed_damage"]
        dmg = float(attacker.level) if "level" in str(fd) else float(str(fd).strip("'"))
        pct = round(100.0 * dmg / hp, 1)
        return {"min": pct, "max": pct, "avg": pct, "expected": pct, "type_mult": type_mult, "category": category,
                "hits": 1.0, "accuracy": _effective_accuracy(entry, move, fv), "notes": notes + ["固定ダメージ"]}

    # --- 攻撃/防御実数値 (参照する能力の上書き: ボディプレスは自分の防御、イカサマは相手の攻撃、サイコショックは相手の防御) ---
    if category == "Physical":
        atk_key, def_key = "atk", "def"
    else:
        atk_key, def_key = "spa", "spd"
    atk_key = entry.get("override_offensive_stat") or atk_key
    def_key = entry.get("override_defensive_stat") or def_key
    # てんねん: 防御側がてんねんなら攻撃側のランクを無視、攻撃側がてんねんなら防御側のランクを無視する
    d_unaware = E.has_effect(d_ab, "ignore_foe_boosts") if d_known else (d_ab == "unaware")
    a_unaware = E.has_effect(a_ab, "ignore_foe_boosts") if a_known else (a_ab == "unaware")
    atk_src = defender if entry.get("override_offensive_pokemon") == "target" else attacker
    atk = atk_src.stat(atk_key, ignore_boost=d_unaware)
    dfn = defender.stat(def_key, ignore_boost=a_unaware)

    stab = (mtype in atypes) or E.stab_any(a_ab)
    recoil_move = bool(entry.get("recoil") or entry.get("crash") or entry.get("mindblown"))
    sec = entry.get("secondary") or {}
    has_secondary = bool(sec and (sec.get("chance") or 0) > 0)
    off_ctx = {"move_type": mtype, "category": category, "move_flags": flags, "stab": stab,
               "user_hp_frac": attacker.hp_frac, "user_status": attacker.status, "target_hp_frac": defender.hp_frac,
               "weather": fv.weather, "terrain": fv.terrain, "recoil": recoil_move, "has_secondary": has_secondary,
               "power": power, "target_switched_in": cx.get("target_switched_in", False),
               "moves_last": cx.get("moves_last", False), "fainted_allies": cx.get("fainted_allies", 0),
               "target_status": defender.status}

    # --- 攻撃側の補正 (効果表。表に無い特性は従来の分岐) ---
    a_mult = 1.0
    power_before_legacy = power
    if a_known:
        a_mult, a_notes = E.offense_multiplier(a_ab, off_ctx)
        notes.extend(a_notes)
        for st, m, _n in E.offense_modifiers(a_ab, off_ctx):
            stages[st].append(m)
    else:
        if a_ab in ("hugepower", "purepower") and atk_key == "atk":
            atk *= 2
        if a_ab == "guts" and attacker.status and atk_key == "atk":
            atk = int(atk * 1.5)
            notes.append("こんじょう補正")
        if a_ab in ("gorillatactics", "hustle") and atk_key == "atk":
            atk = int(atk * 1.5)
        if a_ab == "solarpower" and fv.weather == "sun" and atk_key == "spa":
            atk = int(atk * 1.5)
            notes.append("サンパワー補正")
        if a_ab == "technician" and power <= 60:
            power *= 1.5
        if a_ab == "sharpness" and move_id in moves_with_flag("slicing"):
            power *= 1.5
            notes.append("きれあじ補正")
        if a_ab == "ironfist" and move_id in moves_with_flag("punch"):
            power *= 1.2
        tb = TYPE_BOOST_ABILITIES.get(a_ab)
        if tb and mtype == tb[0]:
            power *= tb[1]
            notes.append(f"特性{a_ab}で強化")
        if PINCH_ABILITIES.get(a_ab) == mtype and attacker.hp_frac <= 1 / 3:
            power *= 1.5
            notes.append("ピンチ特性発動圏")
        if a_ab == "sandforce" and fv.weather == "sandstorm" and mtype in ("Rock", "Ground", "Steel"):
            power *= 1.3
        if a_ab == "flareboost" and attacker.status == "burn" and category == "Special":
            power *= 1.5
        if a_ab == "toxicboost" and attacker.status in ("poison", "toxic") and category == "Physical":
            power *= 1.5
    if power != power_before_legacy and power_before_legacy:
        stages["bp"].append(power / power_before_legacy)

    # --- 防御側の実数補正 (天候: 砂嵐はいわの特防 1.5 倍、ゆきはこおりの防御 1.5 倍。表から) ---
    weather_def = []
    for w_type, stat_mults in ((fe.get("weather_defense") or {}).get(fv.weather or "") or {}).items():
        if w_type in dtypes and def_key in stat_mults:
            dfn = int(dfn * float(stat_mults[def_key]))
            weather_def.append(float(stat_mults[def_key]))
    if not d_known:
        if d_ab == "furcoat" and def_key == "def":
            dfn *= 2
        if d_ab == "marvelscale" and defender.status and def_key == "def":
            dfn = int(dfn * 1.5)
        if d_ab == "grasspelt" and fv.terrain == "grassy" and def_key == "def":
            dfn = int(dfn * 1.5)

    # フィールドのタイプ別補正 (接地した使用者だけ) と、接地した相手への減衰 (ミストフィールドのドラゴン技)。表から
    type_boost = fe.get("type_boost") or {}
    if fv.terrain and a_grounded:
        t_boost = float((type_boost.get("terrain") or {}).get(fv.terrain, {}).get(mtype, 1.0))
        power *= t_boost
        if t_boost != 1.0:
            stages["bp"].append(t_boost)
    if fv.terrain and d_grounded:
        t_nerf = float((fe.get("terrain_target_nerf") or {}).get(fv.terrain, {}).get(mtype, 1.0))
        power *= t_nerf
        if t_nerf != 1.0:
            stages["bp"].append(t_nerf)
    # タイプ強化の持ち物 (威力の補正。2026-10-07 ダメージ照合 1000 手で とけないこおり・くろいメガネ の手が幅の外)
    ti_mult = type_item_mult(attacker.item, mtype)
    if ti_mult != 1.0:
        power *= ti_mult
        stages["bp"].append(ti_mult)

    # --- 基本ダメージ ---
    base = (2 * attacker.level // 5 + 2) * power * atk / max(1, dfn)
    base = base / 50 + 2

    mult = a_mult
    # 天候のタイプ別補正 (晴れ/雨。表から)。メガメガニウムの特性 Mega Sol は自分の攻撃を常に晴れ扱いにする
    self_weather = E.misc_value(a_ab, "self_weather", "weather") if a_known else ("sun" if a_ab == "megasol" else None)
    weather = self_weather or fv.weather
    weather_int = 1.0
    if weather:
        w_mult = float((type_boost.get("weather") or {}).get(weather, {}).get(mtype, 1.0))
        if w_mult != 1.0:
            mult *= w_mult
            weather_int = w_mult
            # 理由に残す: 晴れ下で「いまひとつのフェアリー技」が「等倍の水技」を上回るのは正しいが、補正を書かないと
            # 相性だけ見た読み手には誤りに見える (2026-09-29 第17回: 晴れ下のアシレーヌ vs ハッサム でムーンフォース推奨)
            from advisor.ja_names import type_ja
            notes.append(f"{_WEATHER_JA.get(weather, weather)}で{type_ja(mtype)}技×{w_mult:g}")

    # STAB (てきおうりょくは効果表の offense_mult (stab) で 2 倍になる。表に無ければ従来の 2.0)
    dm = damage_modifiers()
    stab_int = 4096
    if stab:
        mult *= 1.5 if (a_known or a_ab != "adaptability") else 2.0
        stab_int = int(dm.get("stab", 6144)) if (a_known or a_ab != "adaptability") else 8192
        if E.stab_any(a_ab) and mtype not in atypes:
            notes.append(f"特性{a_ab}でタイプ一致")

    # タイプ相性
    mult *= type_mult

    # やけど
    no_burn_drop = E.has_effect(a_ab, "no_burn_attack_drop") if a_known else (a_ab == "guts")
    burn_int = False
    if attacker.status == "burn" and category == "Physical" and not no_burn_drop:
        mult *= 0.5
        burn_int = True
        notes.append("やけどで半減")

    # 壁
    screen_mult = int(dm.get("screen", 2048)) / 4096.0
    if category == "Physical" and (fv.reflect or fv.aurora_veil):
        mult *= 0.5
        stages["final"].append(screen_mult)
        notes.append("リフレクターで半減")
    if category == "Special" and (fv.light_screen or fv.aurora_veil):
        mult *= 0.5
        stages["final"].append(screen_mult)
        notes.append("ひかりのかべで半減")

    # 持ち物
    if attacker.item in ("choiceband",) and category == "Physical":
        mult *= 1.5
    if attacker.item in ("choicespecs",) and category == "Special":
        mult *= 1.5
    if attacker.item == "lifeorb":
        mult *= 1.3
    if attacker.item == "expertbelt" and type_mult > 1.0:
        mult *= 1.2
    if defender.item == "assaultvest" and category == "Special":
        mult *= 1 / 1.5
    for item_id, spec in ((dm.get("items") or {}).items()):
        holder = defender if spec.get("holder") == "defender" else attacker
        if holder.item != item_id:
            continue
        if spec.get("category") and spec["category"] != category:
            continue
        if spec.get("super_effective") and not type_mult > 1.0:
            continue
        stages[spec.get("stage", "final")].append(int(spec["mod"]) / 4096.0)

    # 防御側の軽減 (効果表。表に無い特性は従来の分岐)
    def_ctx = dict(off_ctx, type_mult=type_mult)
    if d_known:
        d_mult, d_notes = E.defense_multiplier(d_ab, def_ctx)
        mult *= d_mult
        notes.extend(d_notes)
        for st, m, _n in E.defense_modifiers(d_ab, def_ctx):
            # 防御の実数値に掛ける特性 (ファーコート等) は被ダメ倍率の逆数を防御に掛ける
            stages[st].append((1.0 / m) if (st == "def" and m) else m)
    mult_before_legacy = mult
    if not d_known:
        if d_ab == "thickfat" and mtype in ("Fire", "Ice"):
            mult *= 0.5
        if d_ab in ("multiscale", "shadowshield") and defender.hp_frac >= 0.999:
            mult *= 0.5
        if d_ab == "heatproof" and mtype == "Fire":
            mult *= 0.5
        if d_ab == "waterbubble" and mtype == "Fire":
            mult *= 0.5
        if d_ab == "purifyingsalt" and mtype == "Ghost":
            mult *= 0.5
        if d_ab == "fluffy" and mtype == "Fire":
            mult *= 2.0
        if d_ab == "icescales" and category == "Special":
            mult *= 0.5
        if d_ab in ("filter", "solidrock", "prismarmor") and type_mult > 1.0:
            mult *= 0.75
            notes.append(f"特性{d_ab}で抜群軽減")
    if not a_known:
        if a_ab == "tintedlens" and type_mult < 1.0:
            mult *= 2.0
        if a_ab == "neuroforce" and type_mult > 1.0:
            mult *= 1.25
    if mult != mult_before_legacy and mult_before_legacy:
        stages["final"].append(mult / mult_before_legacy)

    # 急所 (確定急所と 1/2 以上の急所率だけ期待値に入れる)
    crit_p = E.crit_chance(a_ab, d_ab, entry, off_ctx)
    crit_int, crit_float = None, 1.0
    if crit_p >= CRIT_APPLY_MIN_CHANCE:
        cm = E.crit_multiplier(a_ab)
        mult *= 1.0 + crit_p * (cm - 1.0)
        if crit_p >= 1.0:
            crit_int = cm
        else:
            crit_float = 1.0 + crit_p * (cm - 1.0)
        notes.append("急所" if crit_p >= 1.0 else f"急所{crit_p:.0%}")

    # 連続技: min/max/avg は期待回数分、expected は命中 (天候・特性込み) まで掛ける
    skill_link = any(f.get("kind") == "multihit_max" for f in E.ability_formulas(a_ab))
    hits_noacc = E.hit_expectation(entry, None, 1.0, skill_link, True)
    acc = _effective_accuracy(entry, move, fv)
    acc_mult, no_miss = E.accuracy_multiplier(a_ab, d_ab, off_ctx)
    hits_acc = E.hit_expectation(entry, acc, acc_mult, skill_link, no_miss)
    if hits_noacc != 1.0:
        notes.append(f"連続技 期待{hits_noacc:g}回")

    if _integer_rounding_enabled():
        # Showdown と同じ整数の計算 (2026-10-07 ダメージ照合: 小数の計算は各段階の切り捨てを無視するため、実ダメージが
        # 乱数幅の下に 1〜3 HP はみ出していた)。乱数 85〜100 の 16 通りの最小・最大・平均
        atk_i, dfn_i = atk, dfn
        if crit_int and a_known and d_known:
            # 急所: 攻撃側の下がったランクと防御側の上がったランクを無視する (Showdown の getDamage)
            if int((atk_src.boosts or {}).get(atk_key, 0) or 0) < 0 and not d_unaware:
                atk_i = atk_src.stat(atk_key, ignore_boost=True)
            if int((defender.boosts or {}).get(def_key, 0) or 0) > 0 and not a_unaware:
                dfn_i = defender.stat(def_key, ignore_boost=True)
                for m in weather_def:
                    dfn_i = int(dfn_i * m)
        p_i = max(1, poke_round_mod(max(1, int(power_raw)), chain_mod(stages["bp"])))
        a_i = max(1, poke_round_mod(int(atk_i), chain_mod(stages["atk"])))
        d_i = max(1, poke_round_mod(int(dfn_i), chain_mod(stages["def"])))
        base_i = ((2 * attacker.level // 5 + 2) * p_i * a_i // d_i) // 50 + 2
        rolls = showdown_rolls(base_i, _mod4096(weather_int), crit_int, chain_mod([stab_int / 4096.0] + stages["stab"]),
                               type_mult, burn_int, chain_mod(stages["final"]),
                               (dm.get("random_min", 85), dm.get("random_max", 100)))
        extra = crit_float
        for m in stages["hits"]:
            extra *= m
        dmg_min = rolls[0] * extra * hits_noacc
        dmg_max = rolls[-1] * extra * hits_noacc
        avg = sum(rolls) / len(rolls) * extra * hits_noacc
    else:
        dmg_max = base * mult * hits_noacc
        dmg_min = dmg_max * 0.85
        avg = (dmg_min + dmg_max) / 2
    eff_acc = None if (acc is None or no_miss) else round(min(100.0, float(acc) * acc_mult), 1)

    return {
        "min": round(100.0 * dmg_min / hp, 1),
        "max": round(100.0 * dmg_max / hp, 1),
        "avg": round(100.0 * avg / hp, 1),
        "expected": round(100.0 * avg * (hits_acc / hits_noacc if hits_noacc else 0.0) / hp, 1),
        "type_mult": type_mult,
        "category": category,
        "hits": round(hits_noacc, 3),
        "accuracy": eff_acc,
        "notes": notes,
    }
