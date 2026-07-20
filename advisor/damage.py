"""第9世代 (SV) 準拠のダメージ計算。

ポケモンチャンピオンズはSVの計算式ベース (Lv50固定・個体値31固定)。
主要な補正 (天候/フィールド/壁/やけど/ランク/STAB/主要な特性・持ち物) を実装する。
乱数幅 0.85〜1.00 を考慮し (最小%, 最大%, 平均%) を返す。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from advisor.dex import get_dex, calc_hp, calc_stat, BOOST_MULT


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


def _is_grounded(mon: MonView) -> bool:
    if "Flying" in mon.types or mon.ability == "levitate":
        return False
    if mon.item == "airballoon":
        return False
    return True


def effective_speed(mon: MonView, fieldv: Optional["FieldView"] = None) -> int:
    """特性・持ち物・状態異常・天候/フィールドを考慮した実効素早さ。

    すいすい/ようりょくそ等の天候特性を必ず考慮する (先手判定の要)。
    """
    fv = fieldv or FieldView()
    spe = mon.stat("spe")
    ab = mon.ability or ""
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
    return int(spe)


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


def calc_damage(attacker: MonView, defender: MonView, move_id: str,
                fieldv: Optional[FieldView] = None,
                override_type_mult: Optional[float] = None) -> dict:
    """ダメージを計算して割合 (%表記) で返す。

    戻り値: {"min": %, "max": %, "avg": %, "type_mult": x, "category": ...,
             "notes": [...]}  計算不能 (変化技等) なら {"min":0,...}
    """
    dex = get_dex()
    move = dex.move(move_id)
    fv = fieldv or FieldView()
    notes = []

    if not move or move["category"] == "Status" or not move["power"]:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "type_mult": 1.0,
                "category": move["category"] if move else "Status", "notes": []}

    mtype = move["type"]
    power = float(move["power"])
    category = move["category"]

    # --- 特性による無効化 ---
    imm = IMMUNITY_ABILITIES.get(defender.ability or "")
    if imm and mtype in imm:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "type_mult": 0.0,
                "category": category, "notes": [f"特性{defender.ability}で無効"]}

    # --- タイプ相性 ---
    if override_type_mult is not None:
        type_mult = override_type_mult
    else:
        dtypes = defender.types or (dex.species(defender.species_id) or {}).get("types", [])
        type_mult = dex.effectiveness(mtype, dtypes)
        if mtype == "Ground" and not _is_grounded(defender):
            type_mult = 0.0
    if type_mult == 0.0:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "type_mult": 0.0,
                "category": category, "notes": ["無効"]}

    # --- 攻撃/防御実数値 ---
    if category == "Physical":
        atk_key, def_key = "atk", "def"
    else:
        atk_key, def_key = "spa", "spd"
    # 攻撃側の不利ランクは無視しない/急所は考慮しない (通常時想定)
    atk = attacker.stat(atk_key)
    dfn = defender.stat(def_key)

    # --- 攻撃側の補正 ---
    a_ab = attacker.ability or ""
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

    # --- 防御側の実数補正 ---
    d_ab = defender.ability or ""
    # 砂嵐時の岩タイプ特防1.5倍
    if fv.weather == "sandstorm" and "Rock" in defender.types and def_key == "spd":
        dfn = int(dfn * 1.5)
    if d_ab == "furcoat" and def_key == "def":
        dfn *= 2
    if d_ab == "marvelscale" and defender.status and def_key == "def":
        dfn = int(dfn * 1.5)
    if d_ab == "grasspelt" and fv.terrain == "grassy" and def_key == "def":
        dfn = int(dfn * 1.5)

    # --- 威力補正 ---
    if a_ab == "technician" and power <= 60:
        power *= 1.5
    tb = TYPE_BOOST_ABILITIES.get(a_ab)
    if tb and mtype == tb[0]:
        power *= tb[1]
        notes.append(f"特性{a_ab}で強化")
    if PINCH_ABILITIES.get(a_ab) == mtype and attacker.hp_frac <= 1 / 3:
        power *= 1.5
        notes.append("ピンチ特性発動圏")
    if a_ab == "sandforce" and fv.weather == "sandstorm" and \
            mtype in ("Rock", "Ground", "Steel"):
        power *= 1.3
    if a_ab == "flareboost" and attacker.status == "burn" and category == "Special":
        power *= 1.5
    if a_ab == "toxicboost" and attacker.status in ("poison", "toxic") and \
            category == "Physical":
        power *= 1.5
    if a_ab == "analytic":
        pass   # 行動順依存のため探索側では未適用 (保守的に無視)
    if fv.terrain == "electric" and mtype == "Electric" and _is_grounded(attacker):
        power *= 1.3
    if fv.terrain == "grassy" and mtype == "Grass" and _is_grounded(attacker):
        power *= 1.3
    if fv.terrain == "psychic" and mtype == "Psychic" and _is_grounded(attacker):
        power *= 1.3
    if fv.terrain == "misty" and mtype == "Dragon" and _is_grounded(defender):
        power *= 0.5

    # --- 基本ダメージ ---
    base = (2 * attacker.level // 5 + 2) * power * atk / max(1, dfn)
    base = base / 50 + 2

    mult = 1.0
    # 天候
    if fv.weather == "sun":
        if mtype == "Fire":
            mult *= 1.5
        elif mtype == "Water":
            mult *= 0.5
    elif fv.weather == "rain":
        if mtype == "Water":
            mult *= 1.5
        elif mtype == "Fire":
            mult *= 0.5

    # STAB
    atypes = attacker.types or (dex.species(attacker.species_id) or {}).get("types", [])
    if mtype in atypes:
        mult *= 2.0 if attacker.ability == "adaptability" else 1.5

    # タイプ相性
    mult *= type_mult

    # やけど
    if attacker.status == "burn" and category == "Physical" and attacker.ability != "guts":
        mult *= 0.5
        notes.append("やけどで半減")

    # 壁
    if category == "Physical" and (fv.reflect or fv.aurora_veil):
        mult *= 0.5
        notes.append("リフレクターで半減")
    if category == "Special" and (fv.light_screen or fv.aurora_veil):
        mult *= 0.5
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

    # 防御側の軽減/攻撃側の相性補正特性
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
    if a_ab == "tintedlens" and type_mult < 1.0:
        mult *= 2.0
    if a_ab == "neuroforce" and type_mult > 1.0:
        mult *= 1.25

    dmg_max = base * mult
    dmg_min = dmg_max * 0.85
    hp = defender.max_hp()

    return {
        "min": round(100.0 * dmg_min / hp, 1),
        "max": round(100.0 * dmg_max / hp, 1),
        "avg": round(100.0 * (dmg_min + dmg_max) / 2 / hp, 1),
        "type_mult": type_mult,
        "category": category,
        "notes": notes,
    }
