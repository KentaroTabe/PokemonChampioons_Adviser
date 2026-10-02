"""Showdown の技データ (pokemon-showdown/data/moves.ts + mods/champions/moves.ts) から、構築と助言が使う技の効果表
advisor/data/move_effects.json を起こす (docs/TEAM_BUILD_REDESIGN_1002.md §7.1 / §7.5 / §7.6)。

    python -m tools.build_move_data               # 生成 (advisor/data/move_effects.json、move_priority.json)
    python -m tools.build_move_data --dry-run     # 書かずに要約だけ

項目 (技 id ごと): type / category / power / accuracy (None = 必中) / priority / flags / multihit (回数 or [最小, 最大]) /
multiaccuracy (1 発ごとに命中判定) / self_boosts (使用後に確定で変わる自分の能力) / setup_boosts (自分を対象にする変化技の上昇) /
secondary (追加効果: 確率・状態・能力変化・自分の能力変化) / recoil / drain / crash (外すと自傷) / locked (数ターン固定) /
charge (ため) / recharge (反動で動けない) / selfdestruct / will_crit / crit_ratio / override_* (参照する能力の上書き) /
variable_power (可変威力の種類、表 VARIABLE_POWER_KIND) / condition (条件の種類、表 CONDITION_KIND) /
accuracy_weather (天候で命中が変わる) / field_power (フィールド・天候で威力が変わる) / field_type (天候・フィールドでタイプが変わる) /
nonstandard (M-C で使えない印)。

人手の訂正は advisor/data/move_effects_overrides.json ({技 id: {項目: 値}}) に書く (生成のたびに上書きで合成する)。
basePowerCallback / onTry を持つが表に無い技は要約に列挙する (表を育てる)。純粋な解析関数はテストから使う。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOVES_TS = REPO / "pokemon-showdown" / "data" / "moves.ts"
MOD_TS = REPO / "pokemon-showdown" / "data" / "mods" / "champions" / "moves.ts"
OUT_PATH = REPO / "advisor" / "data" / "move_effects.json"
OVERRIDES_PATH = REPO / "advisor" / "data" / "move_effects_overrides.json"
PRIORITY_PATH = REPO / "advisor" / "data" / "move_priority.json"

# 可変威力の種類 (basePowerCallback を持つ技)。値は構築・助言側の計算の分岐名
VARIABLE_POWER_KIND = {
    "lowkick": "target_weight", "grassknot": "target_weight",
    "heavyslam": "weight_ratio", "heatcrash": "weight_ratio",
    "gyroball": "speed_ratio_slower", "electroball": "speed_ratio_faster",
    "hex": "target_status", "venoshock": "target_poisoned", "infernalparade": "target_status", "bitterblade": None,
    "facade": "user_status", "ragefist": "times_hit", "lastrespects": "fainted_allies",
    "acrobatics": "user_no_item", "knockoff": "target_item", "poltergeist": None,
    "storedpower": "user_boosts", "powertrip": "user_boosts", "punishment": "target_boosts",
    "eruption": "user_hp_ratio", "waterspout": "user_hp_ratio", "dragonenergy": "user_hp_ratio",
    "flail": "user_hp_low", "reversal": "user_hp_low",
    "brine": "target_hp_half", "wringout": "target_hp_ratio", "crushgrip": "target_hp_ratio", "hardpress": "target_hp_ratio",
    "boltbeak": "moves_first", "fishiousrend": "moves_first",
    "assurance": "target_damaged_this_turn", "avalanche": "user_hit_first", "revenge": "user_hit_first", "payback": "moves_last",
    "stompingtantrum": "last_move_failed", "temperflare": "last_move_failed",
    "rollout": "consecutive", "iceball": "consecutive", "furycutter": "consecutive", "echoedvoice": "consecutive",
    "round": "ally_used", "fusionbolt": "ally_used", "fusionflare": "ally_used",
    "tripleaxel": "hit_count", "triplekick": "hit_count",
    "fling": "user_item", "naturalgift": "user_berry", "spitup": "stockpile", "beatup": "party",
    "magnitude": "random", "present": "random", "trumpcard": "pp", "wakeupslap": "target_asleep", "smellingsalts": "target_paralyzed",
    "pursuit": "target_switching", "retaliate": "ally_fainted_last_turn", "risingvoltage": "terrain", "terrainpulse": "terrain",
    "expandingforce": None, "mistyexplosion": None, "psyblade": None,
    "barbbarrage": "target_poisoned", "ragingbull": None, "ivycudgel": None, "collisioncourse": "super_effective",
    "electrodrift": "super_effective", "hydrosteam": None, "psychicnoise": None, "upperhand": None,
    "watershuriken": None, "return": "friendship", "frustration": "friendship", "pikapapow": "friendship",
    "veeveevolley": "friendship", "firepledge": "ally_used", "grasspledge": "ally_used", "waterpledge": "ally_used",
    "terablast": None,
}
# 条件つき技 (onTry 等で失敗する、または前提が要る)。値は条件の種類。None = 条件を無視して良い (他の項目で表せる)
CONDITION_KIND = {
    "steelroller": "terrain_required", "belch": "berry_eaten", "dreameater": "target_asleep", "snore": "user_asleep",
    "sleeptalk": "user_asleep", "fakeout": "first_turn", "firstimpression": "first_turn", "suckerpunch": "opponent_attacking",
    "thunderclap": "opponent_attacking", "upperhand": "opponent_priority", "lastresort": "other_moves_used",
    "poltergeist": "target_item", "burnup": "user_type_fire", "doubleshock": "user_type_electric",
    "synchronoise": "shared_type", "stuffcheeks": "user_berry", "aurawheel": "species", "clangoroussoul": "hp_cost",
    "bellydrum": "hp_cost", "filletaway": "hp_cost", "curse": "user_type_ghost_variant", "rest": "full_heal_sleep",
    "swallow": "stockpile", "spitup": "stockpile", "counter": "hit_by_physical", "mirrorcoat": "hit_by_special",
    "metalburst": "hit_this_turn", "bide": "wait", "focuspunch": "not_hit", "shellsidearm": None, "comeuppance": "hit_this_turn",
    "mefirst": None, "copycat": None, "mirrormove": None, "sketch": None, "naturepower": None, "assist": None,
    "fling": "user_item", "naturalgift": "user_berry", "quash": None, "afteryou": None, "instruct": None,
    "teleport": None, "batonpass": None, "allyswitch": None, "healbell": None, "aromatherapy": None,
    "steelbeam": "hp_cost", "mindblown": "hp_cost", "chloroblast": "hp_cost", "finalgambit": "selfko", "memento": "selfko",
    "healingwish": "selfko", "lunardance": "selfko", "explosion": "selfko", "selfdestruct": "selfko", "mistyexplosion": "selfko",
    "stockpile": None, "powershift": None, "shedtail": "hp_cost", "substitute": "hp_cost", "ragepowder": None, "followme": None,
    "spotlight": None, "doomdesire": "delayed", "futuresight": "delayed", "wish": "delayed", "skydrop": None,
    "revivalblessing": "ally_fainted", "endeavor": "target_hp_higher", "painsplit": None,
    "destinybond": None, "grudge": None, "spite": None, "encore": None, "disable": None, "torment": None,
    "roost": None, "defog": None, "rapidspin": None, "courtchange": None, "trickroom": None,
    "nightmare": "target_asleep", "electrify": None, "iondeluge": None, "psychup": None,
    "stealthrock": None, "spikes": None, "toxicspikes": None, "stickyweb": None, "lockon": None, "mindreader": None,
    "struggle": None, "metronome": None, "attract": None, "captivate": None, "flowershield": None, "terastarstorm": None,
    "terablast": None, "lastrespects": None,
    "auroraveil": "snow_required", "craftyshield": None, "quickguard": None, "wideguard": None, "matblock": None,
    "darkvoid": "species", "hyperspacefury": "species", "magnetrise": None, "noretreat": None, "round": None,
    "splash": None, "telekinesis": None,
}
SELF_TARGETS = ("self", "adjacentAllyOrSelf", "allies", "allySide")


# ------------------------------------------------------------------ 解析 (純粋)
def parse_blocks(text: str) -> dict:
    """moves.ts の本文 → {id: ブロック本文}。ブロックは 1 タブの `id: {` から 1 タブの `},` まで"""
    return {m.group(1): m.group(2) for m in re.finditer(r"^\t([a-z0-9]+): \{\n(.*?)^\t\},", text, flags=re.M | re.S)}


def _grab(body: str, pat: str, conv=str):
    m = re.search(pat, body, flags=re.M)
    return conv(m.group(1)) if m else None


def _boost_dict(block: str) -> dict:
    return {k: int(v) for k, v in re.findall(r"(\w+): (-?\d+),", block)}


def _fraction(body: str, key: str):
    m = re.search(rf"^\t\t{key}: \[(\d+), (\d+)\],", body, flags=re.M)
    return round(int(m.group(1)) / int(m.group(2)), 4) if m else None


def _hook(body: str, name: str) -> str:
    """フック (onModifyMove 等) の本文。無ければ空"""
    m = re.search(rf"^\t\t{name}\([^)]*\) \{{\n(.*?)^\t\t\}},", body, flags=re.M | re.S)
    return m.group(1) if m else ""


def _case_map(hook_body: str, assign_pat: str) -> dict:
    """switch の case ラベル群 → 直後の代入値。assign_pat は値を 1 群で捕まえる正規表現"""
    out: dict = {}
    pending: list = []
    for line in hook_body.splitlines():
        s = line.strip()
        m = re.match(r"case '(\w+)':", s)
        if m:
            pending.append(m.group(1))
            continue
        m = re.match(assign_pat, s)
        if m and pending:
            for label in pending:
                out[label] = m.group(1)
            pending = []
            continue
        if s == "break;":
            pending = []
    return out


WEATHER_NORM = {"raindance": "rain", "primordialsea": "rain", "sunnyday": "sun", "desolateland": "sun", "sandstorm": "sand",
                "hail": "snow", "snowscape": "snow"}
TERRAIN_NORM = {"electricterrain": "electric", "grassyterrain": "grassy", "psychicterrain": "psychic", "mistyterrain": "misty"}


def extract(move_id: str, body: str) -> dict:
    """1 ブロック → 項目の辞書 (純粋)"""
    d: dict = {}
    d["name"] = _grab(body, r'^\t\tname: "([^"]+)",')
    d["type"] = _grab(body, r'^\t\ttype: "(\w+)",')
    d["category"] = _grab(body, r'^\t\tcategory: "(\w+)",')
    d["power"] = _grab(body, r"^\t\tbasePower: (\d+),", int)
    acc = _grab(body, r"^\t\taccuracy: (\w+),")
    d["accuracy"] = None if acc in (None, "true") else int(acc)
    d["priority"] = _grab(body, r"^\t\tpriority: (-?\d+),", int)
    d["pp"] = _grab(body, r"^\t\tpp: (\d+),", int)
    d["target"] = _grab(body, r'^\t\ttarget: "(\w+)",')
    d["nonstandard"] = _grab(body, r'^\t\tisNonstandard: "(\w+)",')
    fl = _grab(body, r"^\t\tflags: \{([^}]*)\}")
    d["flags"] = sorted(re.findall(r"(\w+): 1", fl or ""))
    mh = re.search(r"^\t\tmultihit: (?:(\d+)|\[(\d+), (\d+)\]),", body, flags=re.M)
    d["multihit"] = (int(mh.group(1)) if mh and mh.group(1) else ([int(mh.group(2)), int(mh.group(3))] if mh else None))
    d["multiaccuracy"] = bool(re.search(r"^\t\tmultiaccuracy: true,", body, flags=re.M))
    sb = re.search(r"^\t\tself: \{\n\t\t\tboosts: \{\n(.*?)\n\t\t\t\},", body, flags=re.M | re.S)
    d["self_boosts"] = _boost_dict(sb.group(1)) if sb else {}
    sb2 = re.search(r"^\t\tselfBoost: \{\n\t\t\tboosts: \{\n(.*?)\n\t\t\t\},", body, flags=re.M | re.S)
    if sb2:       # 連続技の全発の後に自分に掛かる能力変化 (スケイルショット: 防御 −1 素早さ +1)
        d["self_boosts"] = {**d["self_boosts"], **_boost_dict(sb2.group(1))}
    bb = re.search(r"^\t\tboosts: \{\n(.*?)\n\t\t\},", body, flags=re.M | re.S)
    boosts = _boost_dict(bb.group(1)) if bb else {}
    if boosts and d["target"] in SELF_TARGETS:
        d["setup_boosts"] = boosts
        d["target_boosts"] = {}
    else:
        d["setup_boosts"] = {}
        d["target_boosts"] = boosts
    sec = re.search(r"^\t\tsecondary: \{\n(.*?)\n\t\t\},", body, flags=re.M | re.S)
    if sec:
        s = sec.group(1)
        chance = _grab(s, r"^\t\t\tchance: (\d+),", int)
        status = _grab(s, r"^\t\t\tstatus: '(\w+)',")
        volatile = _grab(s, r"^\t\t\tvolatileStatus: '(\w+)',")
        tb = re.search(r"^\t\t\tboosts: \{\n(.*?)\n\t\t\t\},", s, flags=re.M | re.S)
        selfb = re.search(r"^\t\t\tself: \{\n\t\t\t\tboosts: \{\n(.*?)\n\t\t\t\t\},", s, flags=re.M | re.S)
        d["secondary"] = {"chance": chance, "status": status, "volatile": volatile,
                          "target_boosts": _boost_dict(tb.group(1)) if tb else {},
                          "self_boosts": _boost_dict(selfb.group(1)) if selfb else {}}
    else:
        d["secondary"] = None
    sv = re.search(r"^\t\tself: \{\n\t\t\tvolatileStatus: '(\w+)',", body, flags=re.M)
    # 使用後に自分に付く状態 (グレイブラッシュ: 次のターン被ダメ 2 倍)。数ターン固定 (lockedmove) は locked で表す
    d["self_volatile"] = sv.group(1) if (sv and sv.group(1) != "lockedmove") else None
    d["recoil"] = _fraction(body, "recoil")
    d["drain"] = _fraction(body, "drain")
    d["heal"] = _fraction(body, "heal")
    d["crash"] = "hasCrashDamage: true" in body
    d["locked"] = "volatileStatus: 'lockedmove'" in body
    d["charge"] = "charge" in d["flags"]
    d["recharge"] = "recharge" in d["flags"]
    d["selfdestruct"] = _grab(body, r'^\t\tselfdestruct: "(\w+)",')
    d["mindblown"] = "mindBlownRecoil: true" in body
    d["will_crit"] = bool(re.search(r"^\t\twillCrit: true,", body, flags=re.M))
    d["crit_ratio"] = _grab(body, r"^\t\tcritRatio: (\d+),", int)
    d["override_offensive_stat"] = _grab(body, r"^\t\toverrideOffensiveStat: '(\w+)',")
    d["override_offensive_pokemon"] = _grab(body, r"^\t\toverrideOffensivePokemon: '(\w+)',")
    d["override_defensive_stat"] = _grab(body, r"^\t\toverrideDefensiveStat: '(\w+)',")
    d["ohko"] = bool(re.search(r"^\t\tohko: (true|'\w+'),", body, flags=re.M))
    d["fixed_damage"] = _grab(body, r"^\t\tdamage: (\d+|'level'),")
    d["sleep_usable"] = bool(re.search(r"^\t\tsleepUsable: true,", body, flags=re.M))
    d["force_switch"] = bool(re.search(r"^\t\tforceSwitch: true,", body, flags=re.M))
    d["self_switch"] = bool(re.search(r"^\t\tselfSwitch: (true|'\w+'),", body, flags=re.M))
    d["thaws_target"] = bool(re.search(r"^\t\tthawsTarget: true,", body, flags=re.M))
    d["breaks_protect"] = bool(re.search(r"^\t\tbreaksProtect: true,", body, flags=re.M))
    has_callback = bool(re.search(r"^\t\tbasePowerCallback\(", body, flags=re.M))
    d["has_power_callback"] = has_callback
    d["variable_power"] = VARIABLE_POWER_KIND.get(move_id) if (has_callback or move_id in VARIABLE_POWER_KIND) else None
    has_try = bool(re.search(r"^\t\tonTry\(", body, flags=re.M))
    d["has_on_try"] = has_try
    d["condition"] = CONDITION_KIND.get(move_id) if (has_try or move_id in CONDITION_KIND) else None
    # 天候で命中が変わる (ぼうふう / かみなり は switch、ふぶき は isWeather([...]) の if)
    mm = _hook(body, "onModifyMove")
    acc_map = _case_map(mm, r"move\.accuracy = (true|\d+);") if "effectiveWeather" in mm else {}
    for ws, val in re.findall(r"isWeather\(\[([^\]]+)\]\)\) move\.accuracy = (true|\d+);", mm):
        for w in re.findall(r"'(\w+)'", ws):
            acc_map[w] = val
    d["accuracy_weather"] = {WEATHER_NORM.get(k, k): (True if v == "true" else int(v)) for k, v in acc_map.items()}
    # フィールド・天候で威力が変わる (onBasePower の chainModify)
    bp = _hook(body, "onBasePower")
    fp: dict = {}
    if bp:
        mult = re.search(r"chainModify\(\[?(\d+(?:\.\d+)?)", bp)
        for t in re.findall(r"isTerrain\('(\w+)'\)", bp):
            fp.setdefault("terrain", []).append(TERRAIN_NORM.get(t, t))
        for w in re.findall(r"'(raindance|sunnyday|sandstorm|snowscape|hail|primordialsea|desolateland)'", bp):
            fp.setdefault("weather", []).append(WEATHER_NORM.get(w, w))
        if fp:
            val = float(mult.group(1)) if mult else None
            if val is not None and val > 16:          # [6144, 4096] 形式
                val = round(val / 4096, 4)
            fp["mult"] = val
            fp["grounded"] = "isGrounded()" in bp
            for k in ("terrain", "weather"):
                if k in fp:
                    fp[k] = sorted(set(fp[k]))
    if "basePower *= 2" in mm and "effectiveWeather" in mm:
        fp["weather_double"] = True
    d["field_power"] = fp
    # 天候・フィールドでタイプが変わる (ウェザーボール / しぜんのちから系)
    mt = _hook(body, "onModifyType")
    type_map = _case_map(mt, r"move\.type = '(\w+)';") if mt else {}
    d["field_type"] = {WEATHER_NORM.get(k, TERRAIN_NORM.get(k, k)): v for k, v in type_map.items()}
    return d


def build_table(base_text: str, mod_text: str, overrides: dict | None = None) -> dict:
    """本体 + mod (inherit は上書き合成、新規はそのまま) + 人手の訂正 → {id: 項目}。純粋"""
    base = parse_blocks(base_text)
    mod = parse_blocks(mod_text) if mod_text else {}
    out: dict = {}
    for mid, body in base.items():
        out[mid] = extract(mid, body)
    for mid, body in mod.items():
        if mid in out and "inherit: true" in body:
            over = extract(mid, body)
            for k, v in over.items():
                present = re.search(rf"^\t\t{re.escape(_ts_key(k))}\b", body, flags=re.M) is not None
                if present:
                    out[mid][k] = v
            ns = re.search(r"^\t\tisNonstandard: (null|\"\w+\"),", body, flags=re.M)
            if ns:
                out[mid]["nonstandard"] = None if ns.group(1) == "null" else ns.group(1).strip('"')
        else:
            out[mid] = extract(mid, body)
    for mid, patch in (overrides or {}).items():
        if mid.startswith("_"):
            continue
        out.setdefault(mid, {})
        out[mid].update(patch)
    return out


_TS_KEYS = {"power": "basePower", "nonstandard": "isNonstandard", "will_crit": "willCrit", "crit_ratio": "critRatio",
            "override_offensive_stat": "overrideOffensiveStat", "override_offensive_pokemon": "overrideOffensivePokemon",
            "override_defensive_stat": "overrideDefensiveStat", "fixed_damage": "damage", "sleep_usable": "sleepUsable",
            "force_switch": "forceSwitch", "self_switch": "selfSwitch", "thaws_target": "thawsTarget",
            "breaks_protect": "breaksProtect", "self_boosts": "self", "setup_boosts": "boosts", "target_boosts": "boosts",
            "accuracy_weather": "onModifyMove", "field_power": "onBasePower", "field_type": "onModifyType",
            "has_power_callback": "basePowerCallback", "variable_power": "basePowerCallback", "has_on_try": "onTry",
            "condition": "onTry", "crash": "hasCrashDamage", "locked": "volatileStatus", "charge": "flags", "recharge": "flags",
            "mindblown": "mindBlownRecoil"}


def _ts_key(field: str) -> str:
    return _TS_KEYS.get(field, field)


def expected_hits(entry: dict, skill_link: bool = False) -> float:
    """連続技の期待回数 (2〜5 回は 35/35/15/15 %、スキルリンクで 5 回)。連続技でなければ 1。命中は掛けない"""
    mh = entry.get("multihit")
    if mh is None:
        return 1.0
    if isinstance(mh, list):
        lo, hi = mh
        if skill_link:
            return float(hi)
        if (lo, hi) == (2, 5):
            return 2 * 0.35 + 3 * 0.35 + 4 * 0.15 + 5 * 0.15
        return (lo + hi) / 2.0
    return float(mh)


def expected_power(entry: dict, skill_link: bool = False, accuracy_mult: float = 1.0) -> float:
    """命中込みの期待威力 (補正前)。1 発ごとに命中判定する技 (multiaccuracy) は外した時点で止まるので Σ_k 威力_k × acc^k、
    威力が発ごとに増える技 (hit_count: トリプルアクセル / トリプルキック) は 威力_k = 威力 × k。それ以外は 威力 × 回数 × acc。
    確定急所は 1.5 倍。必中 (accuracy None) は acc = 1"""
    power = float(entry.get("power") or 0)
    acc = entry.get("accuracy")
    acc_f = 1.0 if acc is None else min(1.0, acc / 100.0 * accuracy_mult)
    crit = 1.5 if entry.get("will_crit") else 1.0
    mh = entry.get("multihit")
    if entry.get("multiaccuracy") and isinstance(mh, int):
        grow = entry.get("variable_power") == "hit_count"
        total = sum(power * (k if grow else 1) * (acc_f ** k) for k in range(1, mh + 1))
        return total * crit
    return power * expected_hits(entry, skill_link) * acc_f * crit


DEMERIT_TAGS = ("selfdrop", "recoil", "crash", "locked", "charge", "recharge", "selfdestruct", "lowacc", "condition", "hp_cost",
                "selfvolatile")
STABLE_MIN_ACCURACY = 90


def demerit_tags(entry: dict) -> list:
    """その技のデメリットの印 (優先表の「それ以外」の理由)。可変威力は印にしない (計算で扱う)"""
    tags = []
    sec = entry.get("secondary") or {}
    sec_self = sec.get("self_boosts") or {}
    if any(v < 0 for v in (entry.get("self_boosts") or {}).values()) \
            or (sec.get("chance") == 100 and any(v < 0 for v in sec_self.values())):
        tags.append("selfdrop")
    if entry.get("self_volatile"):
        tags.append("selfvolatile")
    if entry.get("recoil") or entry.get("mindblown"):
        tags.append("recoil")
    if entry.get("crash"):
        tags.append("crash")
    if entry.get("locked"):
        tags.append("locked")
    if entry.get("charge"):
        tags.append("charge")
    if entry.get("recharge"):
        tags.append("recharge")
    if entry.get("selfdestruct") or entry.get("condition") == "selfko":
        tags.append("selfdestruct")
    acc = entry.get("accuracy")
    if acc is not None and acc < STABLE_MIN_ACCURACY:
        tags.append("lowacc")
    cond = entry.get("condition")
    if cond and cond not in ("selfko", "hp_cost"):
        tags.append("condition")
    if cond == "hp_cost":
        tags.append("hp_cost")
    return tags


def build_priority(table: dict, learnable: set | None = None) -> dict:
    """物理/特殊 × タイプごとの優先表の下書き: 安定技 (デメリット無し・命中 STABLE_MIN_ACCURACY 以上) / それ以外。
    base = 命中込みの期待威力 (expected_power)。可変威力・固定ダメージは印 (variable) を付けるが階層は変えない
    (計算で扱う)。learnable (M-C で誰かが覚える技) があればそれだけ"""
    out: dict = {}
    for mid, e in table.items():
        if e.get("category") not in ("Physical", "Special") or e.get("nonstandard") or not e.get("type"):
            continue
        if learnable is not None and mid not in learnable:
            continue
        power = e.get("power") or 0
        if power <= 0 and not e.get("variable_power") and not e.get("fixed_damage") and not e.get("ohko"):
            continue
        tags = demerit_tags(e)
        tier = "stable" if not tags else "other"
        info = list(tags)
        if (e.get("variable_power") and e.get("variable_power") != "hit_count") or e.get("fixed_damage") or e.get("ohko"):
            info.append("variable")
        out.setdefault(e["category"], {}).setdefault(e["type"], []).append(
            {"move": mid, "tier": tier, "base": round(expected_power(e), 1), "power": power, "accuracy": e.get("accuracy"),
             "tags": info})
    for cat in out.values():
        for lst in cat.values():
            lst.sort(key=lambda r: (0 if r["tier"] == "stable" else 1, -r["base"], r["move"]))
    return out


# ------------------------------------------------------------------ 副作用 (ファイル)
def learnable_moves() -> set:
    from tools.team_build.learnsets import learnsets
    return {m for moves in learnsets().values() for m in moves}


def main() -> None:
    ap = argparse.ArgumentParser(description="Showdown の技データ → advisor/data/move_effects.json / move_priority.json")
    ap.add_argument("--dry-run", action="store_true", help="書かずに要約だけ出す")
    args = ap.parse_args()
    base_text = MOVES_TS.read_text(encoding="utf-8")
    mod_text = MOD_TS.read_text(encoding="utf-8") if MOD_TS.exists() else ""
    overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")) if OVERRIDES_PATH.exists() else {}
    table = build_table(base_text, mod_text, overrides)
    unknown_cb = sorted(m for m, e in table.items() if e.get("has_power_callback") and m not in VARIABLE_POWER_KIND)
    unknown_try = sorted(m for m, e in table.items() if e.get("has_on_try") and m not in CONDITION_KIND)
    learn = learnable_moves()
    usable = {m for m, e in table.items() if not e.get("nonstandard") and m in learn}
    priority = build_priority(table, learnable=usable)
    n_pri = sum(len(v) for cat in priority.values() for v in cat.values())
    print(f"技 {len(table)} (本体 + mod)、M-C で誰かが覚える使える技 {len(usable)}、優先表の攻撃技 {n_pri}")
    print(f"basePowerCallback で表に無い技 {len(unknown_cb)}: {unknown_cb}")
    print(f"onTry で表に無い技 {len(unknown_try)}: {unknown_try}")
    print(f"multiaccuracy: {sorted(m for m, e in table.items() if e.get('multiaccuracy'))}")
    print(f"天候で命中が変わる: {sorted(m for m, e in table.items() if e.get('accuracy_weather'))}")
    print(f"フィールド・天候で威力が変わる: {sorted(m for m, e in table.items() if e.get('field_power'))}")
    if args.dry_run:
        return
    doc = {"_meta": {"source": [str(MOVES_TS.relative_to(REPO)), str(MOD_TS.relative_to(REPO))],
                     "overrides": str(OVERRIDES_PATH.relative_to(REPO)), "n_moves": len(table),
                     "unknown_power_callback": unknown_cb, "unknown_on_try": unknown_try,
                     "note": "python -m tools.build_move_data で生成。人手の訂正は overrides に書く"},
           "moves": {k: table[k] for k in sorted(table)}}
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    pri = {"_meta": {"note": "物理/特殊 × タイプの優先表の下書き (tools/build_move_data.py が生成、安定技 = デメリット無し・命中 90 以上)。"
                             "人手の修正は move_effects_overrides.json 経由 (この表は再生成で上書きされる)",
                     "tags": list(DEMERIT_TAGS) + ["variable"]},
           "table": priority}
    PRIORITY_PATH.write_text(json.dumps(pri, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"書き出し: {OUT_PATH.relative_to(REPO)} / {PRIORITY_PATH.relative_to(REPO)}")


if __name__ == "__main__":
    main()
