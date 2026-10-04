"""並びと型の同時探索 (再設計の S5 統合段、docs/TEAM_BUILD_REDESIGN_1002.md §5)。

核 (S4 の構想、2〜3 体) は型まで同時に決め (型の組 ≤ 27 を評価して上位 BUILD_CORE_BEAM)、補完の枠は「今の並びに足りないもの」
(穴 = 最良の 3 体でも値が低い相手の系統、未充足の役割) で 1 枠ずつ、小さなビーム (BUILD_COMPLEMENT_BEAM) で決める。
核が 2 体の構想は 3 体目を順に選んでから核 3 体の型を一度だけ選び直す (D-24)。

評価は「6 体から 3 体を出す」前提 (§5.3):
    V(T) = Σ_F w_F · max_{S ⊂ T, |S| = 3} v(S, F) / Σ_F w_F − 穴の罰則
    v(S, F) = 平均_{o ∈ F の個体} [0.7 · max_{s ∈ S} cov(s, o) + 0.3 · 次善]
cov は (型, 場) × 相手の型 の行列で、必要になった行だけ計算して記憶する (row_fn = interaction_row + coverage_value)。
持ち物の一意性・メガ石 (構想の mega_id / 指定エース) ・除外・固定枠は候補の段階の制約 (§5.4、差し替えはしない)。

このモジュールは純粋 (numpy だけ): 型の候補の供給 (candidates_fn)、行の計算 (row_fn)、種の事前の絞り込み (prefilter) は
呼び出し側 (joint_stage) が渡す。テストは合成の候補と行列で閉じる。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from champions_agent.config import (BUILD_ACE_MAX_MEGA_STONES, BUILD_ATTACKER_EXCESS_PENALTY, BUILD_COMPLEMENT_BEAM,
                                    BUILD_COMPLEMENT_SPECIES_K, BUILD_CORE_BEAM, BUILD_DUP_ROLE_PENALTY,
                                    BUILD_LINEUP_HOLE_THRESHOLD, BUILD_LINEUP_HOLE_WEIGHT, BUILD_LINEUP_MAX_MEGA_STONES,
                                    BUILD_MAX_ATTACKERS, BUILD_ROLE_FULFIL_BONUS, BUILD_ROLE_UTILITY_MOVES,
                                    BUILD_SET_CANDIDATES_PER_ROLE, BUILD_TRIO_MIX_BONUS, BUILD_UTILITY_BONUS,
                                    BUILD_WEATHER_CONFLICT_PENALTY)
from tools.team_build.role_sets import TERRAINS, WEATHER_FIELD_NAME, WEATHERS, template_of

TEAM_SIZE = 6
TRIO = 3
COVERAGE_BEST_W, COVERAGE_SECOND_W = 0.7, 0.3      # 現行の team_coverage と同じ (相手ごとの最良 0.7 + 次善 0.3)
FIELD_KINDS = ("terrain", "weather")


# ------------------------------------------------------------------ データ
@dataclass
class OppSet:
    """相手の型 1 つ (系統のチームの個体)。key で同じ型をまとめる"""
    key: tuple
    species_id: str
    view: object
    moves: list


@dataclass
class OppPool:
    """相手プール: sets = 一意な相手の型、families = [(family_id, 重み, [行 ...])]"""
    sets: list
    families: list

    @classmethod
    def build(cls, teams: dict, families: list) -> "OppPool":
        """teams = {team_id: [OppSet]}、families = [(family_id, 重み, [team_id ...])]。同じ key の型は 1 行にまとめる"""
        sets: list = []
        index: dict = {}
        rows_by_team: dict = {}
        for tid, lst in teams.items():
            rows = []
            for s in lst:
                if s.key not in index:
                    index[s.key] = len(sets)
                    sets.append(s)
                rows.append(index[s.key])
            rows_by_team[tid] = rows
        fams = []
        for fid, w, tids in families:
            rows = sorted({r for t in tids for r in rows_by_team.get(t, [])})
            if rows:
                fams.append((fid, float(w), rows))
        return cls(sets, fams)

    def family_matrix(self) -> tuple:
        """(系統 × 相手の型 の平均化行列, 系統の重み)"""
        fm = np.zeros((len(self.families), len(self.sets)), dtype=np.float32)
        w = np.zeros(len(self.families), dtype=np.float32)
        for i, (_fid, weight, rows) in enumerate(self.families):
            fm[i, rows] = 1.0 / len(rows)
            w[i] = weight
        return fm, w

    def species_of_families(self, fam_idxs: list) -> list:
        """系統 (index の列) に居る相手の種 (重複なし、系統の順)"""
        out: list = []
        for i in fam_idxs:
            for r in self.families[i][2]:
                sid = self.sets[r].species_id
                if sid not in out:
                    out.append(sid)
        return out


@dataclass
class SetEntry:
    """型 1 つ (役割つき) と評価の場。key = (型のキー, 場のキー) で行列の行を共有する"""
    key: tuple
    species_id: str
    role: str
    cand: object                 # SetCandidate
    view: object                 # MonView (メガ石なら メガ後)
    moves: list
    item: Optional[str]
    stone: bool
    field: dict                  # 評価の場 {"terrain", "weather"} (並びの始動源 + 自分の特性・技)
    own_field: dict              # 自分が張る場 (特性・技)
    locked: bool = False


@dataclass
class Library:
    """型の候補と被覆の行列 (必要になった行だけ計算)"""
    entries: list = field(default_factory=list)
    by_key: dict = field(default_factory=dict)
    cov: Optional[np.ndarray] = None
    done: Optional[np.ndarray] = None

    def add(self, entry: SetEntry) -> int:
        if entry.key in self.by_key:
            return self.by_key[entry.key]
        idx = len(self.entries)
        self.entries.append(entry)
        self.by_key[entry.key] = idx
        return idx

    def ensure_matrix(self, n_opp: int) -> None:
        n = len(self.entries)
        if self.cov is None:
            self.cov = np.zeros((max(n, 1), n_opp), dtype=np.float32)
            self.done = np.zeros(max(n, 1), dtype=bool)
        elif self.cov.shape[0] < n:
            extra = max(n - self.cov.shape[0], 64)
            self.cov = np.vstack([self.cov, np.zeros((extra, n_opp), dtype=np.float32)])
            self.done = np.concatenate([self.done, np.zeros(extra, dtype=bool)])


@dataclass
class LineupResult:
    members: tuple               # 種 id (ソート)
    entries: list                # [SetEntry] (核 → 補完の順)
    roles: dict                  # 種 → 役割
    assignments: dict            # 種 → 選出計画でその種を出す系統 id の一覧 (担当)
    fills: dict                  # 種 → 補完で埋めた穴の系統 id (選ばれた理由)
    selection_plan: dict         # 系統 id → 出す 3 体 (種 id)
    score: float                 # 並びの点 (被覆 − 穴 − 衝突 + 役割の充足 + 補助の価値 − 攻撃役の過多 − 重複)
    parts: dict
    concept: str = ""
    tag: str = ""
    origin: dict = field(default_factory=dict)
    family_values: dict = field(default_factory=dict)    # 系統 id → 最良 3 体の値 (予測。実測との較正に使う)


# ------------------------------------------------------------------ 評価 (純粋)
def trio_values(rows: np.ndarray) -> np.ndarray:
    """rows (k × n_opp、k ≤ 3) → 相手ごとの値 (最良 0.7 + 次善 0.3)"""
    if rows.shape[0] == 1:
        return rows[0] * COVERAGE_BEST_W
    s = np.sort(rows, axis=0)
    return COVERAGE_BEST_W * s[-1] + COVERAGE_SECOND_W * s[-2]


def team_eval(rows: np.ndarray, fam_matrix: np.ndarray, fam_w: np.ndarray,
              hole_threshold: float = BUILD_LINEUP_HOLE_THRESHOLD, hole_weight: float = BUILD_LINEUP_HOLE_WEIGHT,
              classes: Optional[list] = None, trio_bonus: float = 0.0) -> dict:
    """6 体から 3 体を出す前提の並びの評価。rows = 並びの各個体の被覆行 (k × n_opp)。
    classes = 個体ごとの役割の種類 ("offense" / "support")。渡せば、攻撃役と補助・受け役の両方が入る 3 体の値に trio_bonus を足す
    (役割の充足: 先発・勝ち筋・受けが揃うか。設計文書 §5.3)。
    戻り値 {"value": 被覆 − 穴の罰則, "coverage", "hole", "plans": 系統ごとの最良 3 体の index 配列, "best": 系統ごとの最良の値}"""
    n_fam = fam_matrix.shape[0]
    k = rows.shape[0]
    if k == 0 or n_fam == 0:
        return {"value": 0.0, "coverage": 0.0, "hole": 0.0, "plans": np.zeros((n_fam, 0), dtype=int),
                "best": np.zeros(n_fam, dtype=np.float32)}
    combos = list(itertools.combinations(range(k), min(TRIO, k)))
    vals = np.stack([fam_matrix @ trio_values(rows[list(c)]) for c in combos])      # (n_trios × n_fam)
    if classes is not None and trio_bonus > 0 and k >= 2:
        mix = np.array([1.0 if len({classes[i] for i in c}) >= 2 else 0.0 for c in combos], dtype=np.float32)
        vals = vals + trio_bonus * mix[:, None]
    best_idx = np.argmax(vals, axis=0)
    best = vals[best_idx, np.arange(n_fam)]
    total = float(fam_w.sum()) or 1.0
    coverage = float((best * fam_w).sum() / total)
    hole = hole_weight * float((np.clip(hole_threshold - best, 0.0, None) * fam_w).sum() / total)
    plans = np.array([combos[i] for i in best_idx], dtype=int)
    return {"value": coverage - hole, "coverage": coverage, "hole": hole, "plans": plans, "best": best}


def hole_families(best: np.ndarray, fam_w: np.ndarray, threshold: float = BUILD_LINEUP_HOLE_THRESHOLD) -> list:
    """穴 (最良 3 体でも値が threshold 未満の系統) を 重み × 不足 の大きい順に (index の列)"""
    idx = [i for i in range(len(best)) if best[i] < threshold]
    return sorted(idx, key=lambda i: -(float(fam_w[i]) * (threshold - float(best[i]))))


def team_field_from(entries: list) -> dict:
    """並びの始動源 (各個体が張る場。先の個体が優先)"""
    out = {k: None for k in FIELD_KINDS}
    for e in entries:
        for k in FIELD_KINDS:
            if out[k] is None and (e.own_field or {}).get(k):
                out[k] = e.own_field[k]
    return out


def field_conflicts(entries: list) -> int:
    """始動源の衝突数 (天候・フィールドそれぞれ、異なる値が 2 つ以上あれば 1 つにつき 1)"""
    n = 0
    for k in FIELD_KINDS:
        vals = {(e.own_field or {}).get(k) for e in entries} - {None}
        n += max(0, len(vals) - 1)
    return n


def role_matches(have: str, want: str) -> bool:
    """役割の一致: 同じ id、または同じ雛形で同じ場 (setup_ace ↔ sweeper_setup、sun_setter ↔ weather_setter+sun)"""
    if have == want:
        return True
    try:
        h, w = template_of(have), template_of(want)
    except KeyError:
        return False
    return h[0] == w[0] and h[2] == w[2]


def unmet_roles(entries: list, required: list) -> list:
    """要求された役割 [(role, n)] のうち満たしていないもの (不足 1 つにつき role を 1 つ)"""
    out: list = []
    for role, n in required:
        have = sum(1 for e in entries if role_matches(e.role, role))
        out.extend([role] * max(0, int(n) - have))
    return out


def role_fulfillment(entries: list, required: list) -> float:
    """要求された役割の充足率 (要求が無ければ 1)"""
    tot = sum(int(n) for _r, n in required)
    if tot <= 0:
        return 1.0
    return 1.0 - len(unmet_roles(entries, required)) / tot


OFFENSE_TEMPLATES = ("breaker", "sweeper_setup", "cleaner", "tr_ace", "weather_ace", "terrain_ace")
UTILITY_KINDS = ("hazard", "removal", "priority", "speed_control")


def role_class(role: str) -> str:
    """役割 → "offense" (攻撃役) / "support" (補助・受け役)。未知の役割は offense"""
    try:
        tname = template_of(role)[0]
    except KeyError:
        return "offense"
    return "offense" if tname in OFFENSE_TEMPLATES else "support"


def speed_tier(cand) -> str:
    """型の速度帯 (配分と性格から): fast (素早さ投資 ≥ 24) / tr (−Spe の性格で素早さ 0) / bulky"""
    try:
        pts = [int(x) for x in str(getattr(cand, "evs", "") or "").split("/")]
    except ValueError:
        pts = []
    spe = pts[5] if len(pts) == 6 else 0
    if spe >= 24:
        return "fast"
    if (getattr(cand, "nature", "") or "").lower() in ("brave", "quiet", "relaxed", "sassy") and spe == 0:
        return "tr"
    return "bulky"


def utility_kinds(entries: list, pools: dict = BUILD_ROLE_UTILITY_MOVES) -> set:
    """並びにある補助の種類 (設置 / 除去 / 先制 / 速度操作)。技から機械的に"""
    out: set = set()
    for e in entries:
        ms = set(e.moves or [])
        for k in UTILITY_KINDS:
            if ms & set(pools.get(k, ())):
                out.add(k)
    return out


def composition_terms(entries: list, utility_bonus: dict = BUILD_UTILITY_BONUS, max_attackers: int = BUILD_MAX_ATTACKERS,
                      attacker_penalty: float = BUILD_ATTACKER_EXCESS_PENALTY, dup_penalty: float = BUILD_DUP_ROLE_PENALTY) -> dict:
    """並びの構成の項 (純粋。設計文書 §5.3 の「役割の充足」と「重複の減点」):
    utility = 設置 / 除去 / 先制 / 速度操作があることの価値 (種類ごとに 1 回)、
    attackers = 攻撃役の数、excess = 上限を超えた攻撃役 1 体あたりの減点、
    dup = 同じ仕事 (同じ雛形 × 同じ速度帯) の個体が 2 体以上あるときの減点。
    戻り値 {"utility", "attackers", "excess", "dup", "kinds", "value" (= utility − excess − dup)}"""
    kinds = utility_kinds(entries)
    util = sum(float(utility_bonus.get(k, 0.0)) for k in kinds)
    n_att = sum(1 for e in entries if role_class(e.role) == "offense")
    excess = attacker_penalty * max(0, n_att - int(max_attackers))
    groups: dict = {}
    for e in entries:
        try:
            tname = template_of(e.role)[0]
        except KeyError:
            tname = e.role
        key = (tname, speed_tier(e.cand))
        groups[key] = groups.get(key, 0) + 1
    dup = dup_penalty * sum(max(0, n - 1) for n in groups.values())
    return {"utility": round(util, 4), "attackers": n_att, "excess": round(excess, 4), "dup": round(dup, 4),
            "kinds": sorted(kinds), "value": round(util - excess - dup, 4)}


def constraints_ok(entries: list, ace: Optional[str], max_stones: int, base_of: Optional[Callable] = None) -> tuple:
    """並びの制約 (§5.4): 持ち物の一意性、メガ石の上限 (エース指定ならエースだけ)、同じ種 (Species Clause: base_of(種 id) が同じ
    フォルム違い、例 ヌメルゴン と ヒスイヌメルゴン) を 2 体入れない。戻り値 (OK, 理由)"""
    items = [e.item for e in entries if e.item]
    if len(items) != len(set(items)):
        return False, "持ち物の重複"
    if base_of is not None:
        bases = [base_of(e.species_id) for e in entries]
        if len(bases) != len(set(bases)):
            return False, "同じ種 (Species Clause)"
    stones = [e for e in entries if e.stone]
    if ace:
        if any(e.species_id != ace for e in stones):
            return False, "エース以外がメガ石"
        if len(stones) > BUILD_ACE_MAX_MEGA_STONES:
            return False, "メガ石の上限"
    elif len(stones) > max_stones:
        return False, "メガ石の上限"
    return True, ""


def mega_allowed_for(species_id: str, ace: Optional[str], mega_id: Optional[str], has_stone: bool) -> bool:
    """この枠がメガ石を持てるか: エース指定ならエースだけ、構想の mega_id があればその種だけ、無ければ最初の石持ちだけ"""
    if ace:
        return species_id == ace
    if has_stone:
        return False
    return mega_id in (None, "", species_id)


def concept_field(concept: dict, rule_field: Optional[dict] = None) -> dict:
    """構想が前提にする場: 規則の場 + 計画 (plan.field) + 役割 id (sun_setter / psychic_abuser …) から。
    天候は場の名前 (sand → sandstorm)"""
    out = {k: (rule_field or {}).get(k) for k in FIELD_KINDS}
    plan_field = (concept.get("plan") or {}).get("field") or {}
    if plan_field.get("terrain") and not out["terrain"]:
        out["terrain"] = plan_field["terrain"]
    if plan_field.get("weather") and not out["weather"]:
        out["weather"] = WEATHER_FIELD_NAME.get(plan_field["weather"], plan_field["weather"])
    for role in (concept.get("roles") or {}):
        for w in WEATHERS:
            if role in (f"{w}_setter", f"{w}_abuser") and not out["weather"]:
                out["weather"] = WEATHER_FIELD_NAME[w]
        for t in TERRAINS:
            if role in (f"{t}_setter", f"{t}_abuser") and not out["terrain"]:
                out["terrain"] = t
    return out


def concept_core_specs(concept: dict, favorites: tuple = (), banned=()) -> list:
    """構想の核 → [(species_id, role or None)]。固定枠を足し、除外を外す。役割は concept["core"] (新スキーマ) か
    concept["roles"] (軸: 役割 → [種]) から"""
    specs: list = []
    seen: set = set()
    role_of: dict = {}
    for role, sids in (concept.get("roles") or {}).items():
        for s in sids or []:
            role_of.setdefault(s, role)
    for row in concept.get("core") or []:
        sid = row.get("species_id")
        if sid and sid not in seen:
            specs.append((sid, row.get("role") or role_of.get(sid)))
            seen.add(sid)
    for sid in concept.get("core_ids") or []:
        if sid not in seen:
            specs.append((sid, role_of.get(sid)))
            seen.add(sid)
    for sid in favorites:
        if sid not in seen:
            specs.append((sid, role_of.get(sid)))
            seen.add(sid)
    return [(s, r) for s, r in specs if s not in set(banned)]


def concept_requirements(concept: dict, branch_roles: Optional[list] = None) -> list:
    """構想が要求する役割 [(role, n)]: 新スキーマの complement_requirements と、軸の分岐の最小数 (branch_roles)"""
    req: list = []
    for r in concept.get("complement_requirements") or []:
        role = r.get("role") if isinstance(r, dict) else r
        if role:
            req.append((role, int((r.get("n") if isinstance(r, dict) else 1) or 1)))
    for role, n in branch_roles or []:
        req.append((role, int(n)))
    return req


def round_robin(idxs: list, role_of: Callable, n: int) -> list:
    """役割ごとの列から順に 1 つずつ取って n 個 (各役割の最良を先に)"""
    groups: dict = {}
    for i in idxs:
        groups.setdefault(role_of(i), []).append(i)
    out: list = []
    while len(out) < n and any(groups.values()):
        for g in list(groups.values()):
            if g and len(out) < n:
                out.append(g.pop(0))
    return out


# ------------------------------------------------------------------ 探索
@dataclass
class SearchConfig:
    core_beam: int = BUILD_CORE_BEAM
    complement_beam: int = BUILD_COMPLEMENT_BEAM
    species_k: int = BUILD_COMPLEMENT_SPECIES_K
    max_stones: int = BUILD_LINEUP_MAX_MEGA_STONES
    ace: Optional[str] = None
    favorites: tuple = ()
    banned: frozenset = frozenset()
    required_roles: list = field(default_factory=list)      # 規則などの要求 [(role, n)] (構想の要求は別に足す)
    rule_field: dict = field(default_factory=dict)
    speed_plan: str = "neutral"
    hole_threshold: float = BUILD_LINEUP_HOLE_THRESHOLD
    hole_weight: float = BUILD_LINEUP_HOLE_WEIGHT
    field_penalty: float = BUILD_WEATHER_CONFLICT_PENALTY
    role_bonus: float = BUILD_ROLE_FULFIL_BONUS
    base_of: Optional[Callable] = None       # 種 id → Species Clause の同一視キー (図鑑番号)。None なら種 id そのもの
    trio_bonus: float = BUILD_TRIO_MIX_BONUS         # 3 体選出に攻撃役と補助・受け役の両方が入る系統の値への加点
    composition: bool = True                         # 補助の価値 / 攻撃役の過多 / 重複の減点を点に入れる (composition_terms)


class LineupSearch:
    """1 run 分の探索器。
    candidates_fn(species_id, role, used_items: frozenset, mega_allowed, team_field: dict, speed_plan, targets=None) → [SetEntry]
        (targets = 担当の相手の種。仕上げ (refine) だけが渡す。探索中は None = 全部の脅威)
    row_fn(entry, opp: OppSet) → 被覆 0..1
    prefilter(species_ids, hole_species: list, unmet_roles: list, k) → 種の順 (None なら先頭 k)"""

    def __init__(self, pool: OppPool, candidates_fn: Callable, row_fn: Callable, prefilter: Optional[Callable] = None,
                 log: Optional[Callable] = None, capable: Optional[Callable] = None, row_post: Optional[Callable] = None):
        self.pool = pool
        self.fam_matrix, self.fam_w = pool.family_matrix()
        self.lib = Library()
        self.candidates_fn = candidates_fn
        self.row_fn = row_fn
        self.prefilter = prefilter
        self.row_post = row_post             # row_post(entry, 行ベクトル, pool) → 行ベクトル (自爆技の 1 回だけの費用など、任意)
        self.capable = capable               # capable(sid, role) → 役割の指定が無い核に、要求された役割を試すか (None なら試さない)
        self.log = log or (lambda m: None)
        self.n_rows_computed = 0
        self.n_evals = 0

    # ---- 行列
    def rows_for(self, idxs: list) -> np.ndarray:
        self.lib.ensure_matrix(len(self.pool.sets))
        for i in idxs:
            if not self.lib.done[i]:
                e = self.lib.entries[i]
                vec = np.array([float(self.row_fn(e, opp)) for opp in self.pool.sets], dtype=np.float32)
                if self.row_post is not None:
                    vec = np.asarray(self.row_post(e, vec, self.pool), dtype=np.float32)
                self.lib.cov[i, :] = vec
                self.lib.done[i] = True
                self.n_rows_computed += len(self.pool.sets)
        return self.lib.cov[idxs]

    def evaluate(self, idxs: list, cfg: SearchConfig) -> dict:
        self.n_evals += 1
        classes = [role_class(self.lib.entries[i].role) for i in idxs] if cfg.trio_bonus > 0 else None
        return team_eval(self.rows_for(idxs), self.fam_matrix, self.fam_w, cfg.hole_threshold, cfg.hole_weight,
                         classes=classes, trio_bonus=cfg.trio_bonus)

    def score_of(self, idxs: list, required: list, cfg: SearchConfig) -> tuple:
        """並びの点 = 被覆 − 穴 − 始動源の衝突 + 役割の充足 + 補助の価値 − 攻撃役の過多 − 同じ仕事の重複。戻り値 (点, 評価の dict)"""
        info = self.evaluate(idxs, cfg)
        entries = [self.lib.entries[i] for i in idxs]
        conflicts = field_conflicts(entries)
        fulfil = role_fulfillment(entries, required)
        info["conflicts"] = conflicts
        info["roles"] = fulfil
        score = info["value"] - cfg.field_penalty * conflicts + (cfg.role_bonus * fulfil if required else 0.0)
        if cfg.composition:
            comp = composition_terms(entries)
            info["composition"] = comp
            score += comp["value"]
        return score, info

    # ---- 候補
    def entries_for(self, species_id: str, roles: list, used_items: frozenset, mega_allowed: bool, team_field: dict,
                    speed_plan: str) -> list:
        out: list = []
        for role in roles:
            for e in self.candidates_fn(species_id, role, used_items, mega_allowed, team_field, speed_plan):
                if e.item and e.item in used_items:
                    continue
                i = self.lib.add(e)
                if i not in out:
                    out.append(i)
        return out

    def _extend(self, beam: list, cfg: SearchConfig, species_pool: list, roles_of: Callable, required: list,
                speed_plan: str, mega_id: Optional[str], width: int, ace: Optional[str] = None) -> list:
        """1 枠足す。beam = [(点, combo, fills)]。候補は穴 (系統) と未充足の役割で絞った種 × 役割 × 型"""
        next_beam: list = []
        for _sc, combo, fills in beam:
            entries = [self.lib.entries[i] for i in combo]
            members = {e.species_id for e in entries}
            used = frozenset(e.item for e in entries if e.item)
            has_stone = any(e.stone for e in entries)
            info = self.evaluate(combo, cfg)
            holes = hole_families(info["best"], self.fam_w, cfg.hole_threshold)
            hole_species = self.pool.species_of_families(holes)
            unmet = unmet_roles(entries, required)
            tfield = team_field_from(entries)
            bases = {cfg.base_of(s) for s in members} if cfg.base_of else set(members)
            cands = [s for s in species_pool if s not in members and s not in cfg.banned
                     and (cfg.base_of(s) if cfg.base_of else s) not in bases]
            cands = self.prefilter(cands, hole_species, unmet, cfg.species_k) if self.prefilter else cands[:cfg.species_k]
            tried: list = []
            for sid in cands:
                allowed = mega_allowed_for(sid, ace, mega_id, has_stone)
                roles = list(dict.fromkeys(unmet + list(roles_of(sid))))
                for i in self.entries_for(sid, roles, used, allowed, tfield, speed_plan):
                    e = self.lib.entries[i]
                    ok, _why = constraints_ok(entries + [e], ace, cfg.max_stones, cfg.base_of)
                    if not ok:
                        continue
                    new = combo + [i]
                    nsc, ninfo = self.score_of(new, required, cfg)
                    filled = [self.pool.families[f][0] for f in holes if ninfo["best"][f] >= cfg.hole_threshold]
                    tried.append((nsc, new, sid, filled))
            tried.sort(key=lambda x: -x[0])
            seen: set = set()
            for nsc, new, sid, filled in tried:
                if sid in seen:
                    continue
                seen.add(sid)
                nf = dict(fills)
                nf[sid] = filled
                next_beam.append((nsc, new, nf))
                if len(seen) >= width:
                    break
        uniq: dict = {}
        for item in sorted(next_beam, key=lambda x: -x[0]):
            key = tuple(sorted(self.lib.entries[i].species_id for i in item[1]))
            if key not in uniq:
                uniq[key] = item
            if len(uniq) >= width:
                break
        return list(uniq.values())

    def _core_combos(self, core_specs: list, cfg: SearchConfig, roles_of: Callable, tfield: dict, speed_plan: str,
                     mega_id: Optional[str], fixed: list, required: list) -> list:
        """核の型の組を列挙して評価 (fixed = 既に決まった個体の index)。戻り値 [(点, combo, {})] 降順"""
        per_species: list = []
        fixed_entries = [self.lib.entries[i] for i in fixed]
        has_stone = any(e.stone for e in fixed_entries)
        used = frozenset(e.item for e in fixed_entries if e.item)
        for sid, role in core_specs:
            if role:
                roles = [role]
            else:
                # 役割の指定が無い核 (LLM / historical の構想): 要求された役割のうち満たせそうなものを先に、次に種の既定の役割
                want = [r for r, _n in required if self.capable is not None and self.capable(sid, r)]
                roles = list(dict.fromkeys(want + list(roles_of(sid))))
            allowed = mega_allowed_for(sid, cfg.ace, mega_id, has_stone)
            idxs = self.entries_for(sid, roles, used, allowed, tfield, speed_plan)
            if not idxs and role:
                # 指定の役割の型が作れない核 (例: 壁の速さの上限に当たる) は、種の既定の役割で代える。構想ごと捨てない
                # (2026-10-04: 1003 では核の型が作れずに捨てた構想が 99 のうち 5)
                alt = [r for r in roles_of(sid) if r != role]
                idxs = self.entries_for(sid, alt, used, allowed, tfield, speed_plan)
                if idxs:
                    self.log(f"S5 核 {sid}: 役割 {role} の型が作れない → {[self.lib.entries[i].role for i in idxs][:3]} で代える")
            if not idxs:
                return []
            per_species.append(round_robin(idxs, lambda i: self.lib.entries[i].role, BUILD_SET_CANDIDATES_PER_ROLE))
        out: list = []
        for combo in itertools.product(*per_species):
            idxs = list(combo) + list(fixed)
            entries = [self.lib.entries[i] for i in idxs]
            ok, _why = constraints_ok(entries, cfg.ace, cfg.max_stones, cfg.base_of)
            if not ok:
                continue
            sc, _info = self.score_of(idxs, required, cfg)
            out.append((sc, idxs, {}))
        out.sort(key=lambda x: -x[0])
        return out

    def search(self, concept: dict, cfg: SearchConfig, species_pool: list, roles_of: Callable,
               branch_roles: Optional[list] = None, max_results: Optional[int] = None) -> list:
        """1 構想の探索 → [LineupResult] (点の降順、種の組が異なるもの)。
        concept: {"family_id", "core_ids" or "core": [{"species_id", "role"}], "mega_id", "roles": {role: [sid]},
                  "complement_requirements": [{"role", "n"}]}。roles_of(sid) → 役割の指定が無い種に試す役割の列"""
        core_specs = concept_core_specs(concept, cfg.favorites, cfg.banned)
        if not core_specs or len(core_specs) > TEAM_SIZE:
            return []
        core_ids = [s for s, _r in core_specs]
        mega_id = concept.get("mega_id")
        if cfg.ace:
            mega_id = cfg.ace
        tfield0 = concept_field(concept, cfg.rule_field)
        speed_plan = (concept.get("plan") or {}).get("speed_plan") or cfg.speed_plan
        if speed_plan == "neutral" and any(role_matches(r, "tr_setter") for r in (concept.get("roles") or {})):
            speed_plan = "trick_room"
        required = list(cfg.required_roles) + concept_requirements(concept, branch_roles)
        beam = self._core_combos(core_specs, cfg, roles_of, tfield0, speed_plan, mega_id, [], required)[:cfg.core_beam]
        if not beam:
            self.log(f"S5 {concept.get('family_id')}: 核の型が作れない {core_ids}")
            return []
        refine = len(core_specs) == 2
        n_fill = TEAM_SIZE - len(core_specs)
        for slot in range(n_fill):
            beam = self._extend(beam, cfg, species_pool, roles_of, required, speed_plan, mega_id, cfg.complement_beam, cfg.ace)
            if not beam:
                self.log(f"S5 {concept.get('family_id')}: 補完 {slot + 1} 枠目で候補が尽きた")
                return []
            if refine and slot == 0:
                # 核が 2 体: 3 体目に合わせて核の型を一度だけ選び直す (3 体目の役割は固定、型の組 ≤ 27)
                refined: list = []
                for sc, combo, fills in beam:
                    third = combo[-1]
                    third_e = self.lib.entries[third]
                    specs3 = list(core_specs) + [(third_e.species_id, third_e.role)]
                    tfield = team_field_from([self.lib.entries[i] for i in combo])
                    combos = self._core_combos(specs3, cfg, roles_of, tfield, speed_plan, mega_id, [], required)
                    if combos and combos[0][0] > sc:
                        refined.append((combos[0][0], combos[0][1], fills))
                    else:
                        refined.append((sc, combo, fills))
                beam = sorted(refined, key=lambda x: -x[0])
                refine = False
        n = max_results if max_results is not None else cfg.complement_beam
        return [self._finalize(sc, combo, fills, concept.get("family_id", ""), required, cfg) for sc, combo, fills in beam[:n]]

    def _finalize(self, score: float, combo: list, fills: dict, concept_id: str, required: list, cfg: SearchConfig,
                  tag: str = "", origin: Optional[dict] = None) -> LineupResult:
        entries = [self.lib.entries[i] for i in combo]
        info = self.evaluate(combo, cfg)
        fams = self.pool.families
        plan = {fams[f][0]: [entries[j].species_id for j in info["plans"][f]] for f in range(len(fams))}
        assign: dict = {e.species_id: [] for e in entries}
        for f in range(len(fams)):
            for j in info["plans"][f]:
                assign[entries[j].species_id].append(fams[f][0])
        conflicts = field_conflicts(entries)
        fulfil = role_fulfillment(entries, required)
        comp = composition_terms(entries) if cfg.composition else {"utility": 0.0, "attackers": 0, "excess": 0.0, "dup": 0.0, "kinds": []}
        return LineupResult(
            members=tuple(sorted(e.species_id for e in entries)), entries=entries,
            roles={e.species_id: e.role for e in entries}, assignments=assign, fills=dict(fills), selection_plan=plan,
            score=round(float(score), 4),
            parts={"coverage": round(float(info["coverage"]), 4), "hole": round(float(info["hole"]), 4),
                   "roles": round(fulfil, 3), "field_conflicts": conflicts,
                   "n_holes": int(sum(1 for b in info["best"] if b < cfg.hole_threshold)),
                   "utility": comp["utility"], "attackers": comp["attackers"], "attacker_excess": comp["excess"],
                   "dup": comp["dup"], "utility_kinds": ",".join(comp["kinds"])},
            concept=concept_id, tag=tag, origin=dict(origin or {}),
            family_values={fams[f][0]: round(float(info["best"][f]), 4) for f in range(len(fams))})

    # ---- 仕上げ: 担当に合わせて型を作り直す
    def refine(self, result: LineupResult, cfg: SearchConfig, required: list, targets_of: Callable) -> tuple:
        """並びが決まった後、各個体 (指定の型を除く) の型を担当 (選出計画でその個体を出す系統の相手) に合わせて作り直し、
        並びの点が下がらないときだけ置き換える (D-04: 1 発化の加点は担当する相手だけ)。メガ石は今の持ち主のまま。
        targets_of(result, species_id) → 担当の相手の種 (None なら全部)。candidates_fn は targets= を受ける。
        戻り値 (新しい LineupResult, 置き換えた種の一覧)"""
        combo = [self.lib.by_key[e.key] for e in result.entries]
        changed: list = []
        score, _info = self.score_of(combo, required, cfg)
        for k, e in enumerate(list(result.entries)):
            if e.locked:
                continue
            others = [self.lib.entries[i] for j, i in enumerate(combo) if j != k]
            used = frozenset(o.item for o in others if o.item)
            tfield = team_field_from(others)
            targets = targets_of(result, e.species_id)
            best_i, best_sc = combo[k], score
            for c in self.candidates_fn(e.species_id, e.role, used, e.stone, tfield, cfg.speed_plan, targets=targets):
                if c.stone != e.stone or (c.item and c.item in used):
                    continue
                i = self.lib.add(c)
                if i == combo[k]:
                    continue
                trial = combo[:k] + [i] + combo[k + 1:]
                ok, _why = constraints_ok([self.lib.entries[x] for x in trial], cfg.ace, cfg.max_stones, cfg.base_of)
                if not ok:
                    continue
                sc, _ = self.score_of(trial, required, cfg)
                if sc > best_sc:
                    best_i, best_sc = i, sc
            if best_i != combo[k]:
                combo[k] = best_i
                score = best_sc
                changed.append(e.species_id)
        if not changed:
            return result, []
        new = self._finalize(score, combo, result.fills, result.concept, required, cfg, tag=result.tag, origin=result.origin)
        return new, changed

    # ---- 現行チーム枝
    def incumbent(self, reg_entries: list, cfg: SearchConfig, species_pool: list, roles_of: Callable, n_neighbors: int,
                  mega_id: Optional[str] = None) -> tuple:
        """登録チーム (型は固定、役割は型から逆引き済み) とその近傍 (1 枠入替) を同じ評価で測る。
        戻り値 (現行の LineupResult or None, 近傍の列)。現行は全員が所持プールにあり除外に当たらないときだけ。
        近傍は固定枠を入替えず、除外種を入れず、入替枠を散らして (枠ごとの最良を先に) 点の順に n_neighbors まで"""
        if len(reg_entries) != TEAM_SIZE:
            return None, []
        ids = sorted(e.species_id for e in reg_entries)
        idxs = [self.lib.add(e) for e in reg_entries]
        inc = None
        if all(s in species_pool for s in ids) and not (set(ids) & set(cfg.banned)):
            sc, _info = self.score_of(idxs, [], cfg)
            inc = self._finalize(sc, idxs, {}, "INC", [], cfg, tag="incumbent")
        banned_in = set(ids) & set(cfg.banned)
        per_slot: dict = {}
        for k, out_e in enumerate(reg_entries):
            if out_e.species_id in cfg.favorites or (banned_in and out_e.species_id not in banned_in):
                continue
            rest = idxs[:k] + idxs[k + 1:]
            sc0, _i0 = self.score_of(rest, [], cfg)
            ext = self._extend([(sc0, rest, {})], cfg, species_pool, roles_of, [], cfg.speed_plan, mega_id, n_neighbors, None)
            res = []
            for sc, combo, fills in ext:
                in_sid = self.lib.entries[combo[-1]].species_id
                res.append(self._finalize(sc, combo, fills, "INC", [], cfg, tag="incumbent_mut",
                                          origin={"kind": "mutation", "parent_concept": "INC", "swap": [out_e.species_id, in_sid]}))
            res.sort(key=lambda r: -r.score)
            if res:
                per_slot[out_e.species_id] = res
        first = sorted((lst[0] for lst in per_slot.values()), key=lambda r: -r.score)
        rest_all = sorted((r for lst in per_slot.values() for r in lst[1:]), key=lambda r: -r.score)
        neigh, seen = [], set()
        for r in first + rest_all:
            if r.members in seen:
                continue
            seen.add(r.members)
            neigh.append(r)
            if len(neigh) >= n_neighbors:
                break
        return inc, neigh
