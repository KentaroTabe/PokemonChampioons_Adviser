"""推奨構築 (logs/build_search/final_team.json) やチーム本文から config/my_team.json を手入力ベースで更新する。

    python -m tools.register_my_team                    # final_team.json の text を登録
    python -m tools.register_my_team --team-file x.txt  # Showdown 形式のチーム本文 (能力ポイント表記)
    python -m tools.register_my_team --dry-run          # 書き込まずに内容を表示

- 登録キーは HUD に表示される種族名 = フォルム名を省いた基本種名 (ウォッシュロトム → 「ロトム」)。
  フォルムは「種族ID」(showdown id) に保持し、vision の自分側解決が種族値・タイプに使う。
- 既存の他種のエントリは残す。同じキーは丸ごと置き換える (画面読みの部分登録を引きずらない)。
- 2026-09-06 ユーザー決定「型登録は手入力ベース、画面認識は諦める」に基づく登録経路。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FINAL_TEAM = ROOT / "logs" / "build_search" / "final_team.json"
CHAMPIONS_DEX = ROOT / "champions_agent" / "data" / "champions_dex.json"

# チーム本文の能力名 -> my_team.json の HABCDS キー
_STAT_KEYS = {"hp": "h", "atk": "a", "def": "b", "spa": "c", "spd": "d", "spe": "s"}


def _toid(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def parse_team_text(text: str) -> list:
    """Showdown 形式のチーム本文 -> [{species_id, item, ability, nature, points, moves}] (純粋関数)。

    EVs 行はこのプロジェクトのチーム本文ではゲーム内の能力ポイント (0-32) が入っている。
    """
    out = []
    for block in text.strip().split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        head = lines[0]
        if "@" in head:
            sp, item = [x.strip() for x in head.split("@", 1)]
        else:
            sp, item = head, None
        entry = {"species_id": _toid(sp), "item": _toid(item) if item else None,
                 "ability": None, "nature": None, "points": {}, "moves": []}
        for l in lines[1:]:
            if l.startswith("Ability:"):
                entry["ability"] = _toid(l.split(":", 1)[1])
            elif l.startswith("EVs:"):
                for part in l.split(":", 1)[1].split("/"):
                    m = re.match(r"\s*(\d+)\s+(\w+)", part)
                    if m:
                        key = _STAT_KEYS.get(m.group(2).lower())
                        if key:
                            entry["points"][key] = int(m.group(1))
            elif l.endswith("Nature"):
                entry["nature"] = l.split()[0].lower()
            elif l.startswith("- "):
                entry["moves"].append(_toid(l[2:]))
        out.append(entry)
    return out


def display_key(species_id: str, dex: dict) -> str:
    """HUD に出る名前 (基本種の日本語名) を登録キーにする。無ければその種の日本語名。"""
    from advisor.infer import species_ja_name
    sp = (dex.get("species") or dex).get(species_id) or {}
    base = _toid(sp.get("baseSpecies") or "")
    if base and base != species_id:
        ja = species_ja_name(base)
        if ja:
            return ja
    return species_ja_name(species_id) or species_id


def to_entry(parsed: dict, dex: dict, resolver) -> tuple:
    """解析済みブロック -> (登録キー, my_team エントリ)。名前は日本語化 (引けなければ id のまま)"""
    from advisor.ev_infer import _NATURE_JA

    def ja(cat, value):
        return (resolver.ja_of(cat, value) or value) if value else None

    key = display_key(parsed["species_id"], dex)
    entry = {
        "種族ID": parsed["species_id"],
        "能力ポイント": dict(parsed["points"]),
        "性格": _NATURE_JA.get(parsed["nature"], parsed["nature"]) if parsed["nature"] else None,
        "持ち物": ja("items", parsed["item"]),
        "特性": ja("abilities", parsed["ability"]),
        "技": [ja("moves", m) for m in parsed["moves"]],
    }
    return key, {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--team-file", type=Path, default=None,
                    help="チーム本文 (既定: final_team.json の text)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.team_file:
        text = args.team_file.read_text(encoding="utf-8")
        src = str(args.team_file)
    else:
        d = json.loads(FINAL_TEAM.read_text(encoding="utf-8"))
        text = d["text"]
        src = f"{FINAL_TEAM.name} ({d.get('version')})"
    dex = json.loads(CHAMPIONS_DEX.read_text(encoding="utf-8"))
    from vision.normalize import NameResolver
    from advisor import my_team
    resolver = NameResolver()

    print(f"[register_my_team] 入力: {src}")
    for parsed in parse_team_text(text):
        key, entry = to_entry(parsed, dex, resolver)
        pts = " ".join(f"{k.upper()}{v}" for k, v in entry.get("能力ポイント", {}).items())
        print(f"  {key:10s} ({entry.get('種族ID')}) {entry.get('性格')} {pts} @{entry.get('持ち物')} "
              f"特性={entry.get('特性')} 技={entry.get('技')}")
        if not args.dry_run:
            my_team.set_build(key, entry)
    if args.dry_run:
        print("[register_my_team] dry-run: 書き込みなし")
    else:
        print(f"[register_my_team] 保存: {my_team.CONFIG_PATH}")


if __name__ == "__main__":
    main()
