"""日本語名の逆引き (Showdown id → 日本語)。表は vision/data/jp_names.json (種族 / 技 / 特性 / 持ち物 / 性格 / タイプ)。

ユーザーへの表示 (構築レポート、チャットの報告) はここを通し、手書きの翻訳をしない (2026-09-11 ユーザー指示:
「ダイレクトクロー」と書いた誤り。正しくは表の「フェイタルクロー」)。表に無い id はそのまま返す。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Optional

STAT_LETTERS = (("hp", "H"), ("atk", "A"), ("def", "B"), ("spa", "C"), ("spd", "D"), ("spe", "S"))
_EV_NAMES = {"HP": "hp", "Atk": "atk", "Def": "def", "SpA": "spa", "SpD": "spd", "Spe": "spe"}


@lru_cache(maxsize=1)
def _reverse_tables() -> dict:
    from vision.normalize import JP_NAMES_PATH
    raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
    rev: dict = {}
    for cat in ("moves", "abilities", "items", "natures", "types"):
        table: dict = {}
        for ja, v in (raw.get(cat) or {}).items():
            table.setdefault(v, ja)        # 同じ id に複数の表記があれば先に載っている方
        rev[cat] = table
    return rev


def _toid(name: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def species_ja(species_id: Optional[str]) -> str:
    """種族 id → 日本語 (フォルム・メガは advisor.infer.species_ja_name が組み立てる)"""
    if not species_id:
        return ""
    from advisor.infer import species_ja_name
    return species_ja_name(_toid(species_id))


def move_ja(move_id: Optional[str]) -> str:
    return _reverse_tables()["moves"].get(_toid(move_id), move_id or "") if move_id else ""


def ability_ja(ability_id: Optional[str]) -> str:
    return _reverse_tables()["abilities"].get(_toid(ability_id), ability_id or "") if ability_id else ""


def item_ja(item_id: Optional[str]) -> str:
    return _reverse_tables()["items"].get(_toid(item_id), item_id or "") if item_id else ""


def nature_ja(nature_id: Optional[str]) -> str:
    return _reverse_tables()["natures"].get(_toid(nature_id), nature_id or "") if nature_id else ""


def type_ja(type_name: Optional[str]) -> str:
    """英語タイプ名 (Fire) → 日本語 (ほのお)"""
    if not type_name:
        return ""
    table = _reverse_tables()["types"]
    return table.get(type_name, table.get(type_name.capitalize(), type_name))


def evs_ja(evs) -> str:
    """能力ポイント ("2/32/0/0/0/32" か {"hp": 2, ...}) → "H2 A32 S32" (0 は省く)"""
    if not evs:
        return ""
    if isinstance(evs, str):
        parts = evs.split("/")
        if len(parts) != 6:
            return evs
        pts = {k: int(p) for (k, _l), p in zip(STAT_LETTERS, parts)}
    else:
        pts = {k: int(evs.get(k, 0) or 0) for k, _l in STAT_LETTERS}
    return " ".join(f"{letter}{pts[k]}" for k, letter in STAT_LETTERS if pts.get(k))


def set_ja(row: dict) -> dict:
    """型の行 ({"species"/"species_id", "item", "ability", "nature", "evs", "moves"}) → 日本語の辞書"""
    sid = row.get("species") or row.get("species_id") or ""
    return {"species": species_ja(sid), "species_id": sid, "item": item_ja(row.get("item")),
            "ability": ability_ja(row.get("ability")), "nature": nature_ja(row.get("nature")),
            "evs": evs_ja(row.get("evs")), "moves": [move_ja(m) for m in (row.get("moves") or [])]}


def set_line_ja(row: dict) -> str:
    """1 行表示: 種族 @ 持ち物 / 特性 / 性格 配分 / 技1 / 技2 ..."""
    j = set_ja(row)
    return (f"{j['species']} @ {j['item'] or 'なし'} / {j['ability'] or '?'} / {j['nature']} {j['evs']} / "
            + " / ".join(j["moves"]))


def parse_showdown_text(text: str) -> list:
    """Showdown 形式の本文 → [{"species", "item", "ability", "nature", "evs" (能力ポイント文字列), "moves"}]"""
    out: list = []
    cur: Optional[dict] = None
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln:
            if cur:
                out.append(cur)
            cur = None
            continue
        if cur is None:
            head = ln.split("@")
            cur = {"species": _toid(head[0]), "item": _toid(head[1]) if len(head) > 1 else None,
                   "ability": None, "nature": None, "evs": None, "moves": []}
            continue
        if ln.startswith("Ability:"):
            cur["ability"] = _toid(ln.split(":", 1)[1])
        elif ln.startswith("EVs:"):
            pts = {k: 0 for k, _l in STAT_LETTERS}
            for part in ln.split(":", 1)[1].split("/"):
                m = re.match(r"\s*(\d+)\s+(\w+)", part)
                if m and m.group(2) in _EV_NAMES:
                    pts[_EV_NAMES[m.group(2)]] = int(m.group(1))
            cur["evs"] = "/".join(str(pts[k]) for k, _l in STAT_LETTERS)
        elif ln.endswith("Nature"):
            cur["nature"] = _toid(ln[: -len("Nature")])
        elif ln.startswith("- "):
            cur["moves"].append(_toid(ln[2:]))
    if cur:
        out.append(cur)
    return out


def team_text_ja(text: str) -> list:
    """Showdown 形式の本文 → 日本語の 1 行表示のリスト"""
    return [set_line_ja(r) for r in parse_showdown_text(text)]


def team_table_ja(rows: list) -> str:
    """型の行のリスト → Markdown の表 (日本語)"""
    lines = ["| ポケモン | 持ち物 | 特性 | 性格 / 配分 | 技 |", "|---|---|---|---|---|"]
    for r in rows:
        j = set_ja(r)
        lines.append(f"| {j['species']} | {j['item'] or 'なし'} | {j['ability'] or '?'} | {j['nature']} {j['evs']} | "
                     f"{' / '.join(j['moves'])} |")
    return "\n".join(lines)
