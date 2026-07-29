"""構築の進化探索 (6体を考える層) の進捗を集計する。

    python -m tools.evolution_status

各実行の最優秀チームと適応度、世代内の改善幅、アーカイブ (PSRO) の
たまり具合を並べる。「探索が進んでいるのか、同じ構築に収束して
止まっているのか」を見るのが目的。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "logs" / "team_evolution"


def _ja(species: list) -> str:
    from advisor.infer import species_ja_name
    return "/".join(species_ja_name(s) or s for s in species)


def main() -> None:
    runs = sorted(OUT_DIR.glob("run_*.json"))
    if not runs:
        print("進化探索の実行記録がありません")
        return

    print(f"■ 実行記録 {len(runs)}件 → {OUT_DIR}")
    prev_key = None
    for p in runs:
        d = json.loads(p.read_text(encoding="utf-8"))
        stamp = p.stem.replace("run_", "")
        when = datetime.strptime(stamp, "%Y%m%d_%H%M%S").strftime("%m-%d %H:%M")
        best = d.get("best") or {}
        hist = d.get("history") or []
        fit = best.get("fitness", d.get("fitness"))
        # history は世代ごとの集団 (各個体に fitness)
        gens = " → ".join(
            f"{max(e.get('fitness', -1) for e in gen):.2f}"
            for gen in hist if isinstance(gen, list) and gen)
        species = []
        text = best.get("text") or d.get("best_text") or ""
        if text:
            import re
            species = [s.strip() for s in
                       re.findall(r"^([^@\n]+?)(?: @ |$)", text, flags=re.M)[:6]]
        key = tuple(sorted(s.strip().lower() for s in species))
        same = " (前回と同じ構築)" if key and key == prev_key else ""
        prev_key = key or prev_key
        print(f"\n  {when}  適応度 {fit if fit is None else f'{fit:.2f}'}{same}")
        if gens:
            print(f"    世代推移: {gens}")
        if species:
            print(f"    最優秀  : {_ja([s.strip() for s in species])}")
        for k in ("population", "generations", "battles"):
            if k in d:
                print(f"    {k}={d[k]}", end="  ")
        if any(k in d for k in ("population", "generations", "battles")):
            print()

    arc_path = OUT_DIR / "archive.json"
    if arc_path.exists():
        arc = json.loads(arc_path.read_text(encoding="utf-8"))
        print(f"\n■ PSROアーカイブ {len(arc)}件 (次回の相手分布に混ざる)")
        for e in arc:
            when = datetime.fromtimestamp(e["t"]).strftime("%m-%d %H:%M")
            print(f"  {when} 適応度{e['fitness']:.2f}  {_ja(e['species'])}")
        if len(arc) <= 1:
            print("  ⚠ 1件以下。同じ構築に収束し続けると追加されない "
                  "(種族集合が既出だと追加をスキップする実装)")


if __name__ == "__main__":
    main()
