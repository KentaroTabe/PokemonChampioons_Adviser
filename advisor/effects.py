"""技と特性の効果表 (advisor/data/move_effects.json / ability_effects.json) の読み出しと、計算に使う純粋な評価関数。

docs/TEAM_BUILD_REDESIGN_1002.md §7.5 / §7.6 / §9.3。ダメージ計算 (advisor.damage)、助言、型生成 (tools/team_build) が共有する。
表の生成は tools/build_move_data.py / tools/build_ability_data.py。評価関数は ctx (dict) だけを見る純粋関数で、
ctx の語彙は when_matches の条件語と同じ (move_type / category / move_flags / stab / user_hp_frac / user_status / target_hp_frac /
type_mult / weather / terrain / target_switched_in / moves_last / recoil / has_secondary / power / item_consumed / target_status /
user_confused / fainted_allies)。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent / "data"
MOVE_EFFECTS_PATH = DATA_DIR / "move_effects.json"
ABILITY_EFFECTS_PATH = DATA_DIR / "ability_effects.json"

MULTIHIT_2_5_EXPECTED = 2 * 0.35 + 3 * 0.35 + 4 * 0.15 + 5 * 0.15     # 3.1 回 (35/35/15/15 %)
CRIT_MULT = 1.5
# 急所段階 → 急所率 (第 9 世代: 1/24, 1/8, 1/2, 必ず)
CRIT_STAGE_CHANCE = (1 / 24, 1 / 8, 1 / 2, 1.0)
POISON_STATUSES = ("psn", "tox", "poison", "toxic")
SLEEP_STATUSES = ("slp", "sleep")
WEATHER_ALIASES = {"sand": "sandstorm", "sandstorm": "sandstorm", "sun": "sun", "sunnyday": "sun", "rain": "rain",
                   "raindance": "rain", "snow": "snow", "hail": "snow", "snowscape": "snow"}
# 使えるかどうかを型・場・相手から判定できる条件 (ctx の鍵 → 成立の判定)
HARD_CONDITIONS = ("terrain_required", "berry_eaten", "target_asleep", "user_asleep", "user_type_fire", "user_type_electric")


# ------------------------------------------------------------------ 表の読み出し
@lru_cache(maxsize=1)
def move_effects() -> dict:
    try:
        return json.loads(MOVE_EFFECTS_PATH.read_text(encoding="utf-8")).get("moves") or {}
    except Exception:
        return {}


def move_entry(move_id: Optional[str]) -> dict:
    return move_effects().get(move_id or "") or {}


@lru_cache(maxsize=1)
def ability_effects() -> dict:
    try:
        return json.loads(ABILITY_EFFECTS_PATH.read_text(encoding="utf-8")).get("abilities") or {}
    except Exception:
        return {}


def ability_entry(ability_id: Optional[str]) -> dict:
    return ability_effects().get(str(ability_id or "").lower()) or {}


def ability_known(ability_id: Optional[str]) -> bool:
    """効果表に載っている特性か (載っていなければ呼び出し側が従来の表で補う)"""
    return bool(ability_id) and str(ability_id).lower() in ability_effects()


def ability_formulas(ability_id: Optional[str]) -> list:
    return [f for f in (ability_entry(ability_id).get("formula") or []) if f.get("kind") not in (None, "none")]


def has_effect(ability_id: Optional[str], effect: str) -> bool:
    """misc の効果名 (ignore_target_ability / ignore_foe_boosts / self_weather 等) を持つか"""
    return any(f.get("kind") == "misc" and f.get("effect") == effect for f in ability_formulas(ability_id))


def misc_value(ability_id: Optional[str], effect: str, key: str):
    for f in ability_formulas(ability_id):
        if f.get("kind") == "misc" and f.get("effect") == effect:
            return f.get(key)
    return None


# ------------------------------------------------------------------ 条件
def _status_is(status, names) -> bool:
    return str(status or "").lower() in names


def when_matches(when: Optional[dict], ctx: dict) -> bool:
    """式の条件 (when) が ctx で成立するか。知らない条件語は不成立 (保守的)"""
    for key, val in (when or {}).items():
        if key == "move_type":
            ok = ctx.get("move_type") == val
        elif key == "move_types":
            ok = ctx.get("move_type") in (val or [])
        elif key == "category":
            ok = ctx.get("category") == val
        elif key == "move_flag":
            ok = val in (ctx.get("move_flags") or ())
        elif key == "stab":
            ok = bool(ctx.get("stab")) == bool(val)
        elif key == "user_hp_below":
            ok = float(ctx.get("user_hp_frac", 1.0)) <= float(val)
        elif key == "user_status":
            ok = bool(ctx.get("user_status")) == bool(val)
        elif key == "user_poisoned":
            ok = _status_is(ctx.get("user_status"), POISON_STATUSES) == bool(val)
        elif key == "full_hp":
            ok = (float(ctx.get("target_hp_frac", 1.0)) >= 0.999) == bool(val)
        elif key == "super_effective":
            ok = (float(ctx.get("type_mult", 1.0)) > 1.0) == bool(val)
        elif key == "weather":
            ok = WEATHER_ALIASES.get(str(ctx.get("weather") or ""), ctx.get("weather")) == WEATHER_ALIASES.get(val, val)
        elif key == "terrain":
            ok = ctx.get("terrain") == val
        elif key == "target_switched_in":
            ok = bool(ctx.get("target_switched_in")) == bool(val)
        elif key == "moves_last":
            ok = bool(ctx.get("moves_last")) == bool(val)
        elif key == "recoil_move":
            ok = bool(ctx.get("recoil")) == bool(val)
        elif key == "has_secondary":
            ok = bool(ctx.get("has_secondary")) == bool(val)
        elif key == "power_max":
            ok = float(ctx.get("power", 0)) <= float(val)
        elif key == "item_consumed":
            ok = bool(ctx.get("item_consumed")) == bool(val)
        elif key == "target_poisoned":
            ok = _status_is(ctx.get("target_status"), POISON_STATUSES) == bool(val)
        elif key == "target_status":
            ok = bool(ctx.get("target_status")) == bool(val)
        elif key == "user_confused":
            ok = bool(ctx.get("user_confused")) == bool(val)
        else:
            ok = False
        if not ok:
            return False
    return True


# ------------------------------------------------------------------ 特性の式の評価
def offense_multiplier(ability_id: Optional[str], ctx: dict) -> tuple:
    """攻撃側の特性による倍率 (offense_mult / secondary_none / extra_hit) と理由。stab_any と type_change は別関数"""
    mult, notes = 1.0, []
    for f in ability_formulas(ability_id):
        kind = f.get("kind")
        if kind == "offense_mult":
            if "per_fainted_ally" in f:
                n = min(int(ctx.get("fainted_allies", 0) or 0), int(f.get("max_allies", 5)))
                if n > 0:
                    m = 1.0 + float(f["per_fainted_ally"]) * n
                    mult *= m
                    notes.append(f"{ability_id}×{m:g}")
            elif when_matches(f.get("when"), ctx):
                mult *= float(f.get("mult", 1.0))
                notes.append(f"{ability_id}×{float(f.get('mult', 1.0)):g}")
        elif kind == "secondary_none" and when_matches(f.get("when"), ctx):
            mult *= float(f.get("mult", 1.3))
            notes.append(f"{ability_id}×{float(f.get('mult', 1.3)):g}")
        elif kind == "extra_hit":
            m = 1.0 + float(f.get("mult", 0.25))
            mult *= m
            notes.append(f"{ability_id}×{m:g}")
    return mult, notes


def type_change(ability_id: Optional[str], move_type: Optional[str], move_flags=()) -> tuple:
    """タイプ付与・変更の特性 → (変化後のタイプ or None, 倍率)。from が "sound" なら音技が対象"""
    for f in ability_formulas(ability_id):
        if f.get("kind") != "type_change":
            continue
        src = f.get("from")
        if (src == "sound" and "sound" in (move_flags or ())) or (src == move_type):
            return f.get("to"), float(f.get("mult", 1.0))
    return None, 1.0


def stab_any(ability_id: Optional[str]) -> bool:
    return any(f.get("kind") == "stab_any" for f in ability_formulas(ability_id))


def defense_multiplier(ability_id: Optional[str], ctx: dict) -> tuple:
    """防御側の特性による被ダメ倍率と理由"""
    mult, notes = 1.0, []
    for f in ability_formulas(ability_id):
        if f.get("kind") == "defense_mult" and when_matches(f.get("when"), ctx):
            mult *= float(f.get("mult", 1.0))
            notes.append(f"{ability_id}×{float(f.get('mult', 1.0)):g}")
    return mult, notes


def is_immune(ability_id: Optional[str], move_type: Optional[str], move_flags=(), is_status: bool = False) -> bool:
    """特性で技が無効になるか (タイプ / 技のフラグ / 変化技)"""
    for f in ability_formulas(ability_id):
        if f.get("kind") != "immune":
            continue
        if f.get("move_type") and f["move_type"] == move_type:
            return True
        if f.get("move_flag") and f["move_flag"] in (move_flags or ()):
            return True
        if f.get("status_moves") and is_status:
            return True
    return False


def accuracy_multiplier(attacker_ability: Optional[str], defender_ability: Optional[str], ctx: dict) -> tuple:
    """(命中倍率, 必中か)。攻撃側の accuracy_mult と防御側の evasion_mult を掛ける。ノーガードはどちらが持っても必中"""
    no_miss = any(f.get("kind") == "no_miss" for ab in (attacker_ability, defender_ability) for f in ability_formulas(ab))
    mult = 1.0
    for f in ability_formulas(attacker_ability):
        if f.get("kind") == "accuracy_mult" and when_matches(f.get("when"), ctx):
            mult *= float(f.get("mult", 1.0))
    for f in ability_formulas(defender_ability):
        if f.get("kind") == "evasion_mult" and when_matches(f.get("when"), ctx):
            mult *= float(f.get("mult", 1.0))
    return mult, no_miss


def speed_multiplier(ability_id: Optional[str], ctx: dict) -> float:
    mult = 1.0
    for f in ability_formulas(ability_id):
        if f.get("kind") == "speed_mult" and when_matches(f.get("when"), ctx):
            mult *= float(f.get("mult", 1.0))
    return mult


def crit_chance(attacker_ability: Optional[str], defender_ability: Optional[str], entry: dict, ctx: dict) -> float:
    """急所率 (0..1)。確定急所の技は 1、急所を受けない特性なら 0、急所段階の特性は段数で"""
    if any(f.get("kind") == "no_crit_taken" for f in ability_formulas(defender_ability)):
        return 0.0
    if entry.get("will_crit"):
        return 1.0
    # Showdown の critRatio は 1 = 通常 (段階 0)、2 = 段階 +1 (1/8)、3 = 段階 +2 (1/2)
    stage = max(0, int(entry.get("crit_ratio") or 1) - 1)
    for f in ability_formulas(attacker_ability):
        if f.get("kind") == "crit_stage" and when_matches(f.get("when"), ctx):
            stage += int(f.get("delta", 0))
    return CRIT_STAGE_CHANCE[min(max(stage, 0), len(CRIT_STAGE_CHANCE) - 1)]


def crit_multiplier(attacker_ability: Optional[str]) -> float:
    for f in ability_formulas(attacker_ability):
        if f.get("kind") == "crit_mult":
            return float(f.get("mult", CRIT_MULT))
    return CRIT_MULT


# ------------------------------------------------------------------ 技の効果の評価
def expected_hits(entry: dict, skill_link: bool = False) -> float:
    """連続技の期待回数 (2〜5 回は 3.1 回、スキルリンクで 5 回)。連続技でなければ 1。命中は掛けない"""
    mh = entry.get("multihit")
    if mh is None:
        return 1.0
    if isinstance(mh, list):
        lo, hi = mh
        if skill_link:
            return float(hi)
        if (lo, hi) == (2, 5):
            return MULTIHIT_2_5_EXPECTED
        return (lo + hi) / 2.0
    return float(mh)


def hit_expectation(entry: dict, accuracy: Optional[float], acc_mult: float = 1.0, skill_link: bool = False,
                    no_miss: bool = False) -> float:
    """命中込みの「1 発分の威力に掛ける期待係数」。1 発ごとに命中判定する技 (multiaccuracy) は外した時点で止まるので
    Σ_k w_k × acc^k (威力が発ごとに増える技は w_k = k)、それ以外は 期待回数 × acc。必中 (accuracy None) は acc = 1"""
    acc_f = 1.0 if (accuracy is None or no_miss) else min(1.0, float(accuracy) / 100.0 * float(acc_mult))
    mh = entry.get("multihit")
    if entry.get("multiaccuracy") and isinstance(mh, int):
        grow = entry.get("variable_power") == "hit_count"
        return sum((k if grow else 1) * (acc_f ** k) for k in range(1, mh + 1))
    return expected_hits(entry, skill_link) * acc_f


def expected_power(entry: dict, skill_link: bool = False, accuracy_mult: float = 1.0) -> float:
    """命中込みの期待威力 (補正前)。確定急所は 1.5 倍"""
    power = float(entry.get("power") or 0)
    crit = CRIT_MULT if entry.get("will_crit") else 1.0
    return power * hit_expectation(entry, entry.get("accuracy"), accuracy_mult, skill_link) * crit


def move_usable(entry: dict, ctx: dict) -> tuple:
    """型・場・相手から判定できる条件 (HARD_CONDITIONS) が成立するか → (使えるか, 理由)。判定できない条件は使える扱い"""
    cond = entry.get("condition")
    if cond == "terrain_required":
        return (bool(ctx.get("terrain")), "フィールドが無いと失敗")
    if cond == "berry_eaten":
        return (bool(ctx.get("berry_eaten")), "きのみを食べた後しか出せない")
    if cond == "target_asleep":
        return (_status_is(ctx.get("target_status"), SLEEP_STATUSES), "相手が眠っていないと失敗")
    if cond == "user_asleep":
        return (_status_is(ctx.get("user_status"), SLEEP_STATUSES), "自分が眠っていないと失敗")
    if cond == "user_type_fire":
        return ("Fire" in (ctx.get("user_types") or ()), "ほのおタイプでないと失敗")
    if cond == "user_type_electric":
        return ("Electric" in (ctx.get("user_types") or ()), "でんきタイプでないと失敗")
    return True, ""


def _weight_power(ratio_or_kg: float, kind: str) -> float:
    if kind == "target_weight":           # けたぐり / くさむすび (相手の体重 kg)
        w = ratio_or_kg
        return 120 if w >= 200 else 100 if w >= 100 else 80 if w >= 50 else 60 if w >= 25 else 40 if w >= 10 else 20
    r = ratio_or_kg                        # ヘビーボンバー / ヒートスタンプ (自分 / 相手)
    return 120 if r >= 5 else 100 if r >= 4 else 80 if r >= 3 else 60 if r >= 2 else 40


def variable_power(entry: dict, power: float, ctx: dict) -> float:
    """可変威力の技の威力 (計算できる種類だけ。できなければ power のまま)。ctx: user_weight / target_weight / user_speed /
    target_speed / user_hp_frac / target_hp_frac / target_status / user_status / user_item / target_item / user_boost_total /
    fainted_allies / times_hit"""
    kind = entry.get("variable_power")
    if not kind:
        return power
    if kind == "target_weight" and ctx.get("target_weight"):
        return _weight_power(float(ctx["target_weight"]), kind)
    if kind == "weight_ratio" and ctx.get("user_weight") and ctx.get("target_weight"):
        return _weight_power(float(ctx["user_weight"]) / max(0.1, float(ctx["target_weight"])), kind)
    if kind == "speed_ratio_slower" and ctx.get("user_speed") and ctx.get("target_speed"):
        return min(150.0, max(1.0, 25.0 * float(ctx["target_speed"]) / max(1.0, float(ctx["user_speed"]))))
    if kind == "speed_ratio_faster" and ctx.get("user_speed") and ctx.get("target_speed"):
        r = float(ctx["user_speed"]) / max(1.0, float(ctx["target_speed"]))
        return 150 if r >= 4 else 120 if r >= 3 else 80 if r >= 2 else 60 if r >= 1 else 40
    if kind in ("target_status", "target_poisoned"):
        st = ctx.get("target_status")
        if kind == "target_status" and st:
            return power * 2
        if kind == "target_poisoned" and _status_is(st, POISON_STATUSES):
            return power * 2
        return power
    if kind == "user_status":
        return power * 2 if ctx.get("user_status") else power
    if kind == "user_no_item":
        return power * 2 if not ctx.get("user_item") else power
    if kind == "target_item":
        return power * 1.5 if ctx.get("target_item", True) else power
    if kind == "user_boosts":
        return power + 20 * max(0, int(ctx.get("user_boost_total", 0) or 0))
    if kind == "user_hp_ratio":
        return max(1.0, power * float(ctx.get("user_hp_frac", 1.0)))
    if kind == "user_hp_low":
        p = float(ctx.get("user_hp_frac", 1.0))
        return 200 if p < 0.0417 else 150 if p < 0.1042 else 100 if p < 0.2084 else 80 if p < 0.3542 else 40 if p < 0.6875 else 20
    if kind == "target_hp_half":
        return power * 2 if float(ctx.get("target_hp_frac", 1.0)) <= 0.5 else power
    if kind == "target_hp_ratio":
        return max(1.0, 120 * float(ctx.get("target_hp_frac", 1.0)))
    if kind == "fainted_allies":
        return power + 50 * min(5, int(ctx.get("fainted_allies", 0) or 0))
    if kind == "times_hit":
        return power + 50 * min(6, int(ctx.get("times_hit", 0) or 0))
    if kind == "friendship":
        return 102.0
    return power
