"""構築 run の候補チーム (または Showdown 形式のファイル) を日本語の表で表示する。

    python -m tools.team_build.team_ja --run-id rule_0910 --candidate L26_C003
    python -m tools.team_build.team_ja --file logs/build_search/runs/rule_0910/reference_team.txt
    python -m tools.team_build.team_ja --run-id rule_0910            # 勝者 (final/team.json) を表示

名前は vision/data/jp_names.json の表からの逆引き (advisor.ja_names)。手書きの翻訳はしない。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from advisor.ja_names import parse_showdown_text, species_ja, team_table_ja

RUNS = Path(__file__).resolve().parent.parent.parent / "logs" / "build_search" / "runs"


def candidate_text(run_id: str, candidate_id: str | None) -> tuple:
    run = RUNS / run_id
    if candidate_id is None:
        doc = json.loads((run / "final" / "team.json").read_text(encoding="utf-8"))
        return doc["candidate_id"], doc["text"]
    return candidate_id, (run / "s06_sets" / f"{candidate_id}.txt").read_text(encoding="utf-8")


def render(text: str, title: str = "") -> str:
    rows = parse_showdown_text(text)
    head = f"## {title}\n\n" if title else ""
    ids = ", ".join(f"{species_ja(r['species'])} ({r['species']})" for r in rows)
    return head + team_table_ja(rows) + f"\n\n(id: {ids})\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="候補チームを日本語で表示")
    ap.add_argument("--run-id")
    ap.add_argument("--candidate")
    ap.add_argument("--file")
    args = ap.parse_args()
    if args.file:
        print(render(Path(args.file).read_text(encoding="utf-8"), Path(args.file).name))
        return
    if not args.run_id:
        ap.error("--run-id か --file を指定")
    cid, text = candidate_text(args.run_id, args.candidate)
    print(render(text, f"{args.run_id} / {cid}"))


if __name__ == "__main__":
    main()
