"""型ライブラリ (S6): 種族ごとの合法な型候補を列挙 → 制約 → 評価関数 → 上位。

- 代表型: meta_sets (整合規則つき) をそのまま候補 0 とする
- 代替: 使用率 BUILD_SET_ALT_MIN_PCT 以上の持ち物 / 技 / 性格×配分の単独入替 (組合せ爆発を避ける)
- 制約: 性格×配分の整合 (build_meta.nature_fits)、チーム内の持ち物重複 (アイテムクローズ)、
  Showdown validate-team (レギュレーションの合法性)
- 評価: Interaction Matrix の被覆 (脅威ごとの max(lead, switch_in, revenge) の和)
LLM は「この個体に speed control を担当させる」のような役割指定までで、型は探索器が決める。
"""
from __future__ import annotations

import itertools
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from champions_agent.config import USAGE_TARGET_FORMAT
from champions_agent.data import database as db
from champions_agent.data.build_meta import move_categories, nature_fits, parse_points

REPO = Path(__file__).resolve().parent.parent.parent
SHOWDOWN_DIR = REPO / "pokemon-showdown"
ALT_MIN_PCT = 5.0          # 代替候補に採る最低使用率 (%) — config 化候補
MAX_ALTERNATIVES = 12      # 種族ごとの型候補の上限
REP_MARGIN = 0.05          # 代替が代表型をこの被覆差以上で上回らなければ代表型を採る
CHOICE_ITEMS = ("choicescarf", "choiceband", "choicespecs")
SETUP_OR_HAZARD_OR_RECOVERY = ("swordsdance", "nastyplot", "dragondance", "calmmind", "bulkup", "irondefense",
                               "stealthrock", "spikes", "toxicspikes", "stickyweb", "roost", "recover",
                               "slackoff", "softboiled", "rest", "protect", "substitute")


@dataclass
class SetCandidate:
    species_id: str
    ability: Optional[str]
    item: Optional[str]
    nature: Optional[str]
    evs: Optional[str]           # "2/32/0/0/0/32" (能力ポイント)
    moves: list
    source: str = "representative"   # representative / alt:item / alt:move / alt:spread
    score: float = 0.0
    notes: list = field(default_factory=list)

    def as_row(self) -> dict:
        return {"item": self.item, "nature": self.nature, "evs": self.evs, "moves": list(self.moves),
                "ability": self.ability}

    def key(self) -> tuple:
        return (self.species_id, self.ability, self.item, self.nature, self.evs, tuple(self.moves))


def _rows(conn, table: str, col: str, snapshot_id: int, name: str, min_pct: float) -> list:
    return [(r[0], r[1]) for r in conn.execute(
        f"SELECT {col}, usage_percent FROM {table} WHERE snapshot_id=? AND pokemon_name=? "
        f"AND usage_percent >= ? ORDER BY usage_percent DESC", (snapshot_id, name, min_pct))]


def representative_set(conn, snapshot_id: int, species_id: str) -> Optional[SetCandidate]:
    r = conn.execute(
        "SELECT ability_name, item_name, nature, evs, move1, move2, move3, move4 FROM meta_sets "
        "WHERE snapshot_id=? AND pokemon_name=?", (snapshot_id, species_id)).fetchone()
    if r is None:
        return None
    moves = [m for m in (r[4], r[5], r[6], r[7]) if m]
    return SetCandidate(species_id, r[0], r[1], r[2], r[3], moves, "representative")


def enumerate_sets(conn, snapshot_id: int, species_id: str, min_pct: float = ALT_MIN_PCT,
                   limit: int = MAX_ALTERNATIVES) -> list:
    """代表型 + 単独入替の代替 (持ち物 / 技 1 本 / 性格×配分)。整合しない性格×配分は落とす"""
    rep = representative_set(conn, snapshot_id, species_id)
    if rep is None:
        return []
    out = [rep]
    seen = {rep.key()}
    items = [i for i, _ in _rows(conn, "item_usage", "item_name", snapshot_id, species_id, min_pct)]
    moves = [m for m, _ in _rows(conn, "move_usage", "move_name", snapshot_id, species_id, min_pct)]
    natures = [n for n, _ in _rows(conn, "spread_usage", "nature", snapshot_id, species_id, min_pct) if n]
    spreads = [e for e, _ in _rows(conn, "spread_usage", "evs", snapshot_id, species_id, min_pct) if e]
    cats = move_categories(rep.moves)

    def add(c: SetCandidate) -> None:
        if c.key() not in seen and len(out) < limit:
            seen.add(c.key())
            out.append(c)

    stones = _mega_stones()
    rep_is_mega = (rep.item or "") in stones
    for it in items:
        if it == rep.item:
            continue
        if it in stones and not rep_is_mega:
            continue      # メガ石は代表型がメガ石のときだけ (メガ後で評価すると常に強く見えて偏る)
        # 代表型がメガ石なら、非メガ石の代替も残す (1 並びにメガ石 2 個のときの差し替え先)
        cand = SetCandidate(species_id, rep.ability, it, rep.nature, rep.evs, list(rep.moves), "alt:item")
        if not set_sanity(cand):
            add(cand)
    for mv in moves:
        if mv in rep.moves:
            continue
        for k in range(len(rep.moves)):
            new_moves = list(rep.moves)
            new_moves[k] = mv
            cand = SetCandidate(species_id, rep.ability, rep.item, rep.nature, rep.evs, new_moves, "alt:move")
            if not set_sanity(cand):
                add(cand)
    for nv in natures:
        for ev in spreads:
            if (nv, ev) == (rep.nature, rep.evs) or not nature_fits(nv, ev, cats):
                continue
            add(SetCandidate(species_id, rep.ability, rep.item, nv, ev, list(rep.moves), "alt:spread"))
    return out


def _mega_stones() -> set:
    try:
        from tools.check_mega_items import mega_stones
        return {sid for (_a, _b, _c, sid) in mega_stones() if sid}
    except Exception:
        return set()


def set_sanity(c: "SetCandidate") -> list:
    """型の常識フィルタ (問題の一覧、空なら OK)。データ由来の単独入替が作る不整合だけを落とす"""
    from champions_agent.config import OFFENSIVE_ITEM_IDS
    from champions_agent.data.build_meta import _is_offensive_spread
    problems = []
    item = (c.item or "").lower()
    if item in OFFENSIVE_ITEM_IDS and c.evs and not _is_offensive_spread(c.evs) \
            and move_categories(c.moves).count("status") < len(c.moves):
        problems.append("攻撃的持ち物 + 耐久配分 (build_meta の整合規則と同じ)")
    if item in CHOICE_ITEMS and any(m in SETUP_OR_HAZARD_OR_RECOVERY for m in c.moves):
        problems.append("こだわり系 + 積み/設置/回復/まもる")
    if item == "chestoberry" and "rest" not in c.moves:
        problems.append("カゴのみ + ねむる無し")
    if item == "lumberry" and c.source.startswith("alt:item") and "rest" in c.moves:
        problems.append("ラムのみ + ねむる (カゴが本来)")
    return problems


def enforce_single_mega(team: list, alternatives: dict, keep: Optional[str] = None) -> list:
    """メガ石を持つ型が 2 体以上なら 1 体 (keep か被覆が最大の種) だけ残し、他はメガ石以外の最良代替に差し替える。
    alternatives: {species_id: [SetCandidate (被覆降順)]}"""
    stones = _mega_stones()
    megas = [c for c in team if (c.item or "") in stones]
    if len(megas) <= 1:
        return team
    keeper = keep if keep in {c.species_id for c in megas} else max(megas, key=lambda c: c.score).species_id
    out = []
    for c in team:
        if (c.item or "") in stones and c.species_id != keeper:
            alt = next((a for a in alternatives.get(c.species_id, []) if (a.item or "") not in stones), None)
            if alt is not None:
                alt = SetCandidate(alt.species_id, alt.ability, alt.item, alt.nature, alt.evs, list(alt.moves),
                                   alt.source, alt.score, list(alt.notes) + [f"single_mega:{c.item}->{alt.item}"])
                out.append(alt)
                continue
            c = SetCandidate(c.species_id, c.ability, None, c.nature, c.evs, list(c.moves), c.source, c.score,
                             list(c.notes) + [f"single_mega:{c.item}->none"])
        out.append(c)
    return out


def resolve_item_clause(team: list, item_usage: dict) -> list:
    """チーム内で持ち物が重複したら、使用率順の未使用品に差し替える (代表型を優先して残す)。
    team: [SetCandidate]、item_usage: {species_id: [item ids (使用率順)]}"""
    used = set()
    stones = _mega_stones()
    out = []
    for c in team:
        if c.item and c.item in used:
            # 差し替え先にメガ石は使わない (メガ枠は 1 体)
            alt = next((i for i in item_usage.get(c.species_id, [])
                        if i not in used and i != c.item and i not in stones), None)
            c = SetCandidate(c.species_id, c.ability, alt, c.nature, c.evs, list(c.moves),
                             c.source, c.score, c.notes + [f"clause:{c.item}->{alt}"])
        if c.item:
            used.add(c.item)
        out.append(c)
    return out


def to_showdown_text(team: list, level: int = 50) -> str:
    """[SetCandidate] → Showdown 形式 (能力ポイント表記のまま EVs 行に書く: 既存ツールと同じ規約)"""
    from tools.evaluate_team import build_team_text  # noqa: F401  (規約の参照)
    stat_names = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
    blocks = []
    for c in team:
        head = c.species_id + (f" @ {c.item}" if c.item else "")
        lines = [head, f"Level: {level}"]
        if c.ability:
            lines.append(f"Ability: {c.ability}")
        pts = parse_points(c.evs) if c.evs else None
        if pts:
            ev_parts = [f"{pts[k]} {n}" for k, n in zip(("hp", "atk", "def", "spa", "spd", "spe"), stat_names) if pts[k]]
            if ev_parts:
                lines.append("EVs: " + " / ".join(ev_parts))
        if c.nature:
            lines.append(f"{c.nature.capitalize()} Nature")
        lines += [f"- {m}" for m in c.moves]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def validate_team_text(text: str, fmt: str, timeout: int = 60) -> tuple:
    """Showdown の validate-team で合法性を検査する。(ok, errors)"""
    try:
        res = subprocess.run(["node", "pokemon-showdown", "validate-team", fmt],
                             input=text, capture_output=True, text=True,
                             cwd=str(SHOWDOWN_DIR), timeout=timeout)
    except Exception as e:
        return False, [f"validate-team 実行失敗: {e!r}"]
    if res.returncode == 0:
        return True, []
    errs = [l for l in (res.stderr or res.stdout).splitlines() if l.strip()]
    return False, errs


def coverage_score(rows: dict) -> float:
    """{opp_id: row} → 被覆スコア (脅威ごとの max(lead, switch_in, revenge) の平均)"""
    from tools.team_build.interaction import coverage_value
    vals = [coverage_value(r) for r in rows.values() if "error" not in r]
    return sum(vals) / len(vals) if vals else 0.0


def rank_sets(candidates: list, threat_sets: dict) -> list:
    """各型候補を Interaction Matrix の被覆で採点して降順に返す (candidates は同一種族)"""
    from tools.team_build.interaction import matrix, view_from_set
    scored = []
    for c in candidates:
        try:
            view, moves = view_from_set(c.species_id, c.as_row())
        except Exception as e:
            c.notes.append(f"view: {e!r}")
            continue
        rows = matrix({c.species_id: (view, moves)}, threat_sets)[c.species_id]
        c.score = coverage_score(rows)
        scored.append(c)
    scored.sort(key=lambda c: -c.score)
    # 代表型を優先: 代替は被覆が REP_MARGIN 以上で上回るときだけ先頭に残す
    rep = next((c for c in scored if c.source == "representative"), None)
    if rep is not None and scored and scored[0] is not rep and scored[0].score - rep.score < REP_MARGIN:
        scored.remove(rep)
        scored.insert(0, rep)
    return scored


def item_usage_map(conn, snapshot_id: int, species_ids: list, min_pct: float = 0.0) -> dict:
    return {sid: [i for i, _ in _rows(conn, "item_usage", "item_name", snapshot_id, sid, min_pct)]
            for sid in species_ids}
