"""config/my_team.json をShowdownチームテキストへ書き出す (人間対AI用)。

もっと見る画面の自動読み取りで登録した自パーティを、ローカルShowdownの
チームビルダーへ貼り付けられる形式に変換する (human_battle と併用)。

    python -m tools.export_my_team_showdown              # 標準出力に表示
    python -m tools.export_my_team_showdown --out my_team_showdown.txt
"""
from __future__ import annotations

import argparse

from advisor import my_team
from vision.normalize import NameResolver

_EV_LABELS = {"hp": "HP", "atk": "Atk", "def": "Def",
              "spa": "SpA", "spd": "SpD", "spe": "Spe"}


def _nature_en(nature_ja: str) -> str:
    """日本語性格名 -> 英語名 (上昇/下降ペアの一致で対応付ける)"""
    pair = my_team._NATURES.get(nature_ja)
    if pair is None:
        return "Serious"   # 無補正
    for name, p in my_team._NATURES.items():
        if name.isascii() and p == pair:
            return name.capitalize()
    return "Serious"


def export_team() -> str:
    from tools.evaluate_team import current_team_entries
    resolver = NameResolver()
    sets, skipped = [], []
    for ja, entry in current_team_entries().items():
        moves_ja = list(entry.get("技") or entry.get("moves") or [])
        sp = resolver.resolve_species(ja, cutoff=0.9)
        if sp is None or len(moves_ja) < 4:
            skipped.append(ja)
            continue
        build = my_team.get_my_build(ja) or {}
        lines = [sp[1]]
        item = build.get("item_ja")
        if item:
            it = resolver.resolve(item, "items", cutoff=0.85)
            if it:
                lines[0] += f" @ {it[1]}"
        ability = build.get("ability_ja")
        if ability:
            ab = resolver.resolve(ability, "abilities", cutoff=0.85)
            if ab:
                lines.append(f"Ability: {ab[1]}")
        lines.append("Level: 50")
        ev = build.get("ev") or {}
        ev_parts = [f"{v} {_EV_LABELS[k]}" for k, v in ev.items() if v]
        if ev_parts:
            lines.append("EVs: " + " / ".join(ev_parts))
        nat = entry.get("性格") or entry.get("nature")
        if nat:
            lines.append(f"{_nature_en(nat)} Nature")
        for mv in moves_ja[:4]:
            r = resolver.resolve(mv, "moves", cutoff=0.85)
            lines.append(f"- {r[1] if r else mv}")
        sets.append("\n".join(lines))
    if skipped:
        print(f"[export_my_team] 技未登録のためスキップ: {', '.join(skipped)} "
              "(もっと見る画面の能力タブを開くと登録されます)")
    return "\n\n".join(sets) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="my_team.json -> Showdownチームテキスト")
    ap.add_argument("--out", default=None, help="出力ファイル (省略時は標準出力)")
    args = ap.parse_args()
    text = export_team()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[export_my_team] 書き出し: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
