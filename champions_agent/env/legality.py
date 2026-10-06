"""学習・評価に流すチームの技の合法性 (champions mod の learnset)。

2026-09-13 障害: 使用率 DB (cbd) の M-C データにネギガナイトの「メテオアサルト」が入り、champions mod の learnset に無い
ため Showdown が「can't learn」でチームを拒否 → poke-env は受理を待ち続け → 学習が毎サイクル タイムアウト (06:22〜18:xx、
12 時間分)。使用率データはゲームの実態、mod の learnset はシミュレータの現実で、両者は食い違い得る。
シミュレータに流す型は mod の learnset で技を検査し、覚えない技は同じ種の使用率上位の合法な技で埋める。
純粋関数 (table を渡せば DB も learnsets.ts も読まない)。
"""
from __future__ import annotations

from typing import Optional

_TABLE: Optional[dict] = None


def _learnset_table() -> dict:
    global _TABLE
    if _TABLE is None:
        try:
            from tools.team_build.learnsets import learnsets
            _TABLE = learnsets()
        except Exception:
            _TABLE = {}
    return _TABLE


def known_species(species_id: str, table: Optional[dict] = None) -> bool:
    """mod の learnset にその種 (フォルムは前方一致) があるか。無い種は判定できないので検査しない"""
    from tools.team_build.learnsets import resolve_species
    t = table if table is not None else _learnset_table()
    return bool(t) and resolve_species(species_id, t) is not None


def legal_moves(species_id: str, moves, table: Optional[dict] = None) -> list:
    """技のうち mod の learnset で覚えるものだけ (順序と重複排除を保つ)。種が learnset に無ければそのまま返す"""
    from tools.team_build.learnsets import learnset_of
    t = table if table is not None else _learnset_table()
    ms = [m for m in (moves or []) if m]
    if not known_species(species_id, t):
        return list(dict.fromkeys(ms))
    ok = learnset_of(species_id, t)
    return list(dict.fromkeys(m for m in ms if m in ok))


def fill_moves(species_id: str, moves, candidates, n: int = 4, table: Optional[dict] = None) -> list:
    """legal_moves の結果を、候補 (使用率順の技) の合法なもので n 本まで埋める"""
    out = legal_moves(species_id, moves, table)
    if len(out) >= n:
        return out[:n]
    for m in legal_moves(species_id, candidates, table):
        if m not in out:
            out.append(m)
            if len(out) >= n:
                break
    return out
