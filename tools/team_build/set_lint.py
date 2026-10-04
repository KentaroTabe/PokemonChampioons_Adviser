"""型の常識規則 set_lint (2026-10-05 判断 #14: 判定は規則、LLM は規則の提案)。

定義 5 項目はユーザーの集計 (1003 の生成型の 27.7%、10/4 の探索 run の 13.3% が該当。除外を適用した数) から受け取ったもの
(docs/TEAM_BUILD_PLAN_1005.md §7-1)。誤り (error) は生成の最終検査で落とし、警告 (warning) は記録だけ残す。

  誤り
    nature_move   性格が下げる側 (攻撃 / 特攻) を使う攻撃技を持つ。自分の能力を使わない技は除く: イカサマ (相手の攻撃)、ボディプレス
                  (自分の防御)、ちきゅうなげ / ナイトヘッド (固定ダメージ)、一撃技、カウンター系、いかりのまえば等の威力 0 の割合技。
                  とんぼがえり / クイックターン / ボルトチェンジ / こうそくスピン / キラースピン / ねこだまし も除く (NATURE_RULE_EXCLUDED)
    item          持ち物なし / タイプ強化の持ち物でそのタイプの攻撃技なし / カゴのみで ねむる なし / こだわり系と変化技 /
                  アクロバットと消費しない持ち物
    field_dup     特性で張る場 (ひでり等) と同じ場を技 (にほんばれ等) でも張る
    few_moves     技が 4 つ未満 (重複を除く)
  警告
    no_stab       自分のタイプの攻撃技が 1 つも無い。ウェザーボールは自分で張る天候 (特性か技) のタイプが一致すれば一致とみなす
                  (しぜんのちから系のだいちのはどうも同じ扱い)。-ate 系の特性で変わるタイプ、へんげんじざい、フォルムでタイプが決まる技
                  (めざめるダンス等) は一致とみなす

純粋関数: 技の情報は info (MoveInfo 互換: move(id) → dict、types_of(species_id) → [タイプ]) で渡す。既定は図鑑 + 効果表 (DexInfo)。
生成 (role_sets / joint_stage の代表型 / gen_sets) は has_errors で候補を落とし、S6 は lint_rows で run ごとの該当率を記録する。
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Optional

from champions_agent.config import BUILD_CONSUMABLE_ITEMS, BUILD_SET_LINT_GATE, BUILD_TYPE_ITEMS

# 性格 → 下げる能力 (無補正の性格は載せない)
NATURE_MINUS = {"adamant": "spa", "jolly": "spa", "impish": "spa", "careful": "spa", "brave": "spe", "relaxed": "spe",
                "quiet": "spe", "sassy": "spe", "modest": "atk", "timid": "atk", "bold": "atk", "calm": "atk",
                "lonely": "def", "naughty": "spd", "mild": "def", "rash": "spd", "hasty": "def", "naive": "spd",
                "lax": "spd", "gentle": "def"}
# 性格の規則から除く技 (ユーザー定義): 交代技・除去・ねこだまし (威力より役割で入れる技)
NATURE_RULE_EXCLUDED = frozenset({"uturn", "flipturn", "voltswitch", "rapidspin", "mortalspin", "fakeout"})
CHOICE_ITEMS = frozenset({"choiceband", "choicespecs", "choicescarf"})
# タイプ強化の持ち物 → タイプ (config の 18 種 + プレート / おこう)
_PLATES = {"flameplate": "Fire", "splashplate": "Water", "zapplate": "Electric", "meadowplate": "Grass", "icicleplate": "Ice",
           "fistplate": "Fighting", "toxicplate": "Poison", "earthplate": "Ground", "skyplate": "Flying", "mindplate": "Psychic",
           "insectplate": "Bug", "stoneplate": "Rock", "spookyplate": "Ghost", "dracoplate": "Dragon", "dreadplate": "Dark",
           "ironplate": "Steel", "pixieplate": "Fairy", "seaincense": "Water", "waveincense": "Water", "roseincense": "Grass",
           "oddincense": "Psychic", "rockincense": "Rock"}
TYPE_BOOST_ITEMS = dict({v: k for k, v in BUILD_TYPE_ITEMS.items()}, **_PLATES)
CONSUMABLE_ITEMS = frozenset(BUILD_CONSUMABLE_ITEMS)
# 場を張る特性 / 技 (advisor/data/field_effects.json の sources と同じ。info が無くても動くように持つ)
FIELD_ABILITIES = {"psychicsurge": ("terrain", "psychic"), "electricsurge": ("terrain", "electric"), "grassysurge": ("terrain", "grassy"),
                   "mistysurge": ("terrain", "misty"), "drought": ("weather", "sun"), "drizzle": ("weather", "rain"),
                   "sandstream": ("weather", "sandstorm"), "snowwarning": ("weather", "snow"),
                   "orichalcumpulse": ("weather", "sun"), "hadronengine": ("terrain", "electric")}
FIELD_MOVES = {"psychicterrain": ("terrain", "psychic"), "electricterrain": ("terrain", "electric"), "grassyterrain": ("terrain", "grassy"),
               "mistyterrain": ("terrain", "misty"), "sunnyday": ("weather", "sun"), "raindance": ("weather", "rain"),
               "sandstorm": ("weather", "sandstorm"), "snowscape": ("weather", "snow"), "chillyreception": ("weather", "snow")}
WEATHER_BALL_TYPE = {"sun": "Fire", "rain": "Water", "sandstorm": "Rock", "snow": "Ice"}
TERRAIN_PULSE_TYPE = {"electric": "Electric", "grassy": "Grass", "psychic": "Psychic", "misty": "Fairy"}
ATE_ABILITIES = {"pixilate": "Fairy", "aerilate": "Flying", "refrigerate": "Ice", "galvanize": "Electric", "normalize": "Normal"}
ANY_STAB_ABILITIES = frozenset({"protean", "libero"})
OWN_TYPE_MOVES = frozenset({"revelationdance", "ivycudgel", "ragingbull", "multiattack", "judgment", "technoblast"})
ERROR_CODES = ("nature_move", "item", "field_dup", "few_moves")
WARNING_CODES = ("no_stab",)

# 生成の最終検査で落とした型の数 (code → n)。stage_s5_joint が run の記録に写す
LINT_REJECTS: Counter = Counter()


# ------------------------------------------------------------------ 技の情報
class DexInfo:
    """図鑑 (advisor.dex) + 効果表 (advisor.effects.move_entry) から規則に要る項目だけを引く (遅延 import)"""

    def __init__(self):
        from advisor.dex import get_dex
        self._dex = get_dex()
        self._cache: dict = {}

    def move(self, move_id: str) -> Optional[dict]:
        if move_id in self._cache:
            return self._cache[move_id]
        mi = self._dex.move(move_id)
        if not mi:
            self._cache[move_id] = None
            return None
        try:
            from advisor.effects import move_entry
            e = move_entry(move_id) or {}
        except Exception:
            e = {}
        out = {"type": mi.get("type"), "category": str(mi.get("category") or "").lower(),
               "power": int(e.get("power") if e.get("power") is not None else (mi.get("basePower") or 0)),
               "variable_power": e.get("variable_power"), "fixed_damage": e.get("fixed_damage"), "ohko": bool(e.get("ohko")),
               "target_stat": e.get("override_offensive_pokemon") == "target", "other_stat": e.get("override_offensive_stat"),
               "condition": e.get("condition"), "flags": set((mi.get("flags") or {}).keys()) if isinstance(mi.get("flags"), dict)
               else set(e.get("flags") or [])}
        self._cache[move_id] = out
        return out

    def types_of(self, species_id: str) -> list:
        sp = self._dex.species(species_id) or {}
        return list(sp.get("types") or [])


_INFO: Optional[DexInfo] = None


def default_info() -> DexInfo:
    global _INFO
    if _INFO is None:
        _INFO = DexInfo()
    return _INFO


# ------------------------------------------------------------------ 純粋な述語
def damaging(m: Optional[dict]) -> bool:
    """ダメージを自分の能力か相手の能力で計算する攻撃技 (固定ダメージ・一撃・カウンター系・威力 0 の割合技は含まない)"""
    if not m or m.get("category") not in ("physical", "special"):
        return False
    if m.get("fixed_damage") or m.get("ohko") or str(m.get("condition") or "").startswith("hit_by"):
        return False
    return int(m.get("power") or 0) > 0 or bool(m.get("variable_power"))


def uses_own_offense(move_id: str, m: Optional[dict]) -> bool:
    """性格の規則で数える攻撃技: 自分の攻撃 / 特攻を使い、除外の技でない"""
    if move_id in NATURE_RULE_EXCLUDED or not damaging(m):
        return False
    return not m.get("target_stat") and not m.get("other_stat")


def nature_move_mismatch(nature: Optional[str], moves: list, move_of: Callable) -> list:
    """性格が下げる側を使う攻撃技 (純粋)。戻り値 = 該当する技"""
    minus = NATURE_MINUS.get((nature or "").lower())
    if minus not in ("atk", "spa"):
        return []
    want = "physical" if minus == "atk" else "special"
    return [mv for mv in moves if uses_own_offense(mv, move_of(mv)) and (move_of(mv) or {}).get("category") == want]


def item_problems(item: Optional[str], moves: list, move_of: Callable) -> list:
    """持ち物の規則 (純粋)。戻り値 = 問題の列 ("none" / "type_no_move" / "chesto_no_rest" / "choice_status" / "acrobatics")"""
    it = (item or "").lower()
    ms = [str(x) for x in moves]
    out: list = []
    if not it:
        out.append("none")
    if it in TYPE_BOOST_ITEMS:
        want = TYPE_BOOST_ITEMS[it]
        if not any(damaging(move_of(mv)) and (move_of(mv) or {}).get("type") == want for mv in ms):
            out.append("type_no_move")
    if it == "chestoberry" and "rest" not in ms:
        out.append("chesto_no_rest")
    if it in CHOICE_ITEMS and any((move_of(mv) or {}).get("category") == "status" for mv in ms):
        out.append("choice_status")
    if "acrobatics" in ms and it and it not in CONSUMABLE_ITEMS and not it.endswith("berry") and not it.endswith("gem"):
        out.append("acrobatics")
    return out


def own_fields(ability: Optional[str], moves: list) -> tuple:
    """(特性で張る場 (kind, value) or None, 技で張る場の列 [(kind, value, move)])"""
    ab = FIELD_ABILITIES.get((ability or "").lower())
    mv = [(FIELD_MOVES[m][0], FIELD_MOVES[m][1], m) for m in moves if m in FIELD_MOVES]
    return ab, mv


def field_dup(ability: Optional[str], moves: list) -> list:
    """特性で張る場と同じ場を張る技 (純粋)"""
    ab, mv = own_fields(ability, moves)
    if not ab:
        return []
    return [m for kind, val, m in mv if (kind, val) == ab]


def effective_type(move_id: str, m: dict, ability: Optional[str], moves: list, own_types: list) -> Optional[str]:
    """型の文脈でその攻撃技が実際に出すタイプ (ウェザーボール / だいちのはどう は自分で張る場、-ate 特性、フォルム依存の技)"""
    t = m.get("type")
    ab, mv_fields = own_fields(ability, moves)
    if move_id == "weatherball":
        w = ab[1] if ab and ab[0] == "weather" else next((v for k, v, _m in mv_fields if k == "weather"), None)
        return WEATHER_BALL_TYPE.get(w or "", t)
    if move_id == "terrainpulse":
        tr = ab[1] if ab and ab[0] == "terrain" else next((v for k, v, _m in mv_fields if k == "terrain"), None)
        return TERRAIN_PULSE_TYPE.get(tr or "", t)
    if move_id in OWN_TYPE_MOVES:
        return own_types[0] if own_types else t
    a = (ability or "").lower()
    if a in ATE_ABILITIES and t == "Normal":
        return ATE_ABILITIES[a]
    if a == "liquidvoice" and "sound" in (m.get("flags") or set()):
        return "Water"
    return t


def stab_missing(own_types: list, ability: Optional[str], moves: list, move_of: Callable) -> bool:
    """自分のタイプの攻撃技が 1 つも無い (純粋)。へんげんじざい系は常に一致"""
    if (ability or "").lower() in ANY_STAB_ABILITIES or not own_types:
        return False
    for mv in moves:
        m = move_of(mv)
        if damaging(m) and effective_type(mv, m, ability, moves, own_types) in own_types:
            return False
    return True


def lint_set(species_id: str, ability: Optional[str], item: Optional[str], nature: Optional[str], moves: list,
             info=None, own_types: Optional[list] = None) -> dict:
    """型 1 つの検査 → {"errors": [code], "warnings": [code], "detail": {code: 根拠}}。info 省略時は図鑑"""
    info = info or default_info()
    move_of = info.move
    moves = [str(m) for m in (moves or []) if m]
    errors, warnings, detail = [], [], {}
    bad = nature_move_mismatch(nature, moves, move_of)
    if bad:
        errors.append("nature_move")
        detail["nature_move"] = {"nature": nature, "moves": bad}
    ip = item_problems(item, moves, move_of)
    if ip:
        errors.append("item")
        detail["item"] = {"item": item, "problems": ip}
    fd = field_dup(ability, moves)
    if fd:
        errors.append("field_dup")
        detail["field_dup"] = {"ability": ability, "moves": fd}
    if len(set(moves)) < 4:
        errors.append("few_moves")
        detail["few_moves"] = {"n": len(set(moves))}
    types = list(own_types) if own_types is not None else list(info.types_of(species_id) or [])
    if stab_missing(types, ability, moves, move_of):
        warnings.append("no_stab")
        detail["no_stab"] = {"types": types}
    return {"errors": errors, "warnings": warnings, "detail": detail}


def lint_candidate(c, info=None) -> dict:
    """SetCandidate (species_id / ability / item / nature / moves) の検査"""
    return lint_set(c.species_id, c.ability, c.item, c.nature, list(c.moves or []), info=info)


def has_errors(result: dict) -> bool:
    return bool(result.get("errors"))


def gate_rejects(c, info=None, source: str = "generated", gate: bool = BUILD_SET_LINT_GATE) -> Optional[dict]:
    """生成の最終検査: 誤りがあれば LINT_REJECTS に数えて検査結果を返す (呼び出し側はその候補を捨てる)。
    gate が偽なら数えるだけで None (落とさない)。誤りが無ければ None"""
    res = lint_candidate(c, info)
    if not res["errors"]:
        return None
    for code in res["errors"]:
        LINT_REJECTS[f"{source}:{code}"] += 1
    return res if gate else None


def rejects_snapshot(reset: bool = False) -> dict:
    out = dict(sorted(LINT_REJECTS.items()))
    if reset:
        LINT_REJECTS.clear()
    return out


# ------------------------------------------------------------------ 技の補充 (4 つ未満を作らない)
def fill_to_four(moves: list, attack_order: list, utility_order: list, learnset, *, item: Optional[str] = None,
                 ability: Optional[str] = None, exclude=(), move_of: Optional[Callable] = None) -> list:
    """技が 4 つ未満の型を補充する (純粋)。順に 攻撃技 (attack_order: 点の順、分類は呼び出し側で絞る) → 補助技 (utility_order) →
    learnset の残り (変化技を先に)。こだわり系なら変化技は足さない。特性で張る場と同じ場の技、exclude、重複は足さない"""
    out = [m for m in moves if m]
    seen = set(out)
    ex = set(exclude or ())
    ab = FIELD_ABILITIES.get((ability or "").lower())
    choice = (item or "").lower() in CHOICE_ITEMS

    def ok(m: str) -> bool:
        if m in seen or m in ex or m not in learnset:
            return False
        if ab and FIELD_MOVES.get(m) == ab:
            return False
        if choice and move_of is not None and (move_of(m) or {}).get("category") == "status":
            return False
        return True
    pools = [list(attack_order), list(utility_order)]
    if move_of is not None:
        rest = sorted(m for m in learnset if m not in seen)
        pools.append([m for m in rest if (move_of(m) or {}).get("category") == "status"])
        pools.append([m for m in rest if damaging(move_of(m))])
    for pool in pools:
        for m in pool:
            if len(out) >= 4:
                return out
            if ok(m):
                out.append(m)
                seen.add(m)
    return out


# ------------------------------------------------------------------ run の記録 (S6)
GENERATED_SOURCES_EXCLUDED = ("custom", "registered", "representative")


def lint_rows(rows: list, info=None) -> dict:
    """s06_sets.json の行 (sets: [{species, ability, item, nature, moves, source}]) を検査し、行に lint を付けて集計を返す。
    集計: 全型 / 生成型 (custom・registered・representative を除く) の誤り率と警告率、code ごとの数、source ごとの数"""
    info = info or default_info()
    n_sets = n_err = n_warn = 0
    gen_n = gen_err = 0
    by_code: Counter = Counter()
    by_source: dict = {}
    examples: list = []
    for row in rows or []:
        row_res = {"n_error_sets": 0, "n_warning_sets": 0, "sets": {}}
        for st in row.get("sets") or []:
            sid = st.get("species")
            try:
                res = lint_set(sid, st.get("ability"), st.get("item"), st.get("nature"), st.get("moves") or [], info=info)
            except Exception as e:  # 図鑑に無い id 等
                res = {"errors": [], "warnings": [], "detail": {"error": repr(e)}}
            src = str(st.get("source") or "").split(":")[0] or "unknown"
            n_sets += 1
            bs = by_source.setdefault(src, {"n": 0, "errors": 0, "warnings": 0})
            bs["n"] += 1
            if res["errors"]:
                n_err += 1
                bs["errors"] += 1
                row_res["n_error_sets"] += 1
                if len(examples) < 20:
                    examples.append({"candidate_id": row.get("candidate_id"), "species": sid, "errors": res["errors"],
                                     "detail": res["detail"]})
            if res["warnings"]:
                n_warn += 1
                bs["warnings"] += 1
                row_res["n_warning_sets"] += 1
            for code in res["errors"] + res["warnings"]:
                by_code[code] += 1
            if src not in GENERATED_SOURCES_EXCLUDED:
                gen_n += 1
                gen_err += 1 if res["errors"] else 0
            row_res["sets"][sid] = {"errors": res["errors"], "warnings": res["warnings"]}
        row["lint"] = row_res
    return {"n_rows": len(rows or []), "n_sets": n_sets, "n_error_sets": n_err, "n_warning_sets": n_warn,
            "error_rate": round(n_err / n_sets, 4) if n_sets else None, "warning_rate": round(n_warn / n_sets, 4) if n_sets else None,
            "generated": {"n_sets": gen_n, "n_error_sets": gen_err, "error_rate": round(gen_err / gen_n, 4) if gen_n else None},
            "by_code": dict(sorted(by_code.items())), "by_source": by_source, "examples": examples,
            "rejected_in_generation": rejects_snapshot()}


def write_lint_report(run_dir, rows: list, log: Optional[Callable] = None, info=None) -> dict:
    """S6 の行を検査して run_dir/s06_lint.json に書き、1 行ログを出す。行には lint が付く (呼び出し側が s06_sets.json に書く)"""
    import json
    from pathlib import Path
    rep = lint_rows(rows, info=info)
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "s06_lint.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if log:
        g = rep["generated"]
        log(f"S6 lint: 誤り {rep['n_error_sets']}/{rep['n_sets']} 型 ({rep['error_rate']})、生成型 {g['n_error_sets']}/{g['n_sets']} "
            f"({g['error_rate']}、目標 0)、警告 {rep['n_warning_sets']} ({rep['warning_rate']})、code {rep['by_code']}、"
            f"生成で落とした型 {rep['rejected_in_generation']}")
    return rep
