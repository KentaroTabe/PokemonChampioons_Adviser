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

    for it in items:
        if it != rep.item:
            add(SetCandidate(species_id, rep.ability, it, rep.nature, rep.evs, list(rep.moves), "alt:item"))
    for mv in moves:
        if mv in rep.moves:
            continue
        for k in range(len(rep.moves)):
            new_moves = list(rep.moves)
            new_moves[k] = mv
            add(SetCandidate(species_id, rep.ability, rep.item, rep.nature, rep.evs, new_moves, "alt:move"))
    for nv in natures:
        for ev in spreads:
            if (nv, ev) == (rep.nature, rep.evs) or not nature_fits(nv, ev, cats):
                continue
            add(SetCandidate(species_id, rep.ability, rep.item, nv, ev, list(rep.moves), "alt:spread"))
    return out


def resolve_item_clause(team: list, item_usage: dict) -> list:
    """チーム内で持ち物が重複したら、使用率順の未使用品に差し替える (代表型を優先して残す)。
    team: [SetCandidate]、item_usage: {species_id: [item ids (使用率順)]}"""
    used = set()
    out = []
    for c in team:
        if c.item and c.item in used:
            alt = next((i for i in item_usage.get(c.species_id, []) if i not in used and i != c.item), None)
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
    return sorted(scored, key=lambda c: -c.score)


def item_usage_map(conn, snapshot_id: int, species_ids: list, min_pct: float = 0.0) -> dict:
    return {sid: [i for i, _ in _rows(conn, "item_usage", "item_name", snapshot_id, sid, min_pct)]
            for sid in species_ids}
