"""生成チームをShowdownのチーム検証器に通すチェックツール。

    python -m tools.validate_teams [チーム数] [フォーマット]

例:
    python -m tools.validate_teams 20
    python -m tools.validate_teams 5 gen9customgame
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter

from champions_agent.config import TRAINING_BATTLE_FORMAT, TRAINING_TEAM_SIZE
from champions_agent.env.team_builder import build_random_team_text


def validate_team_text(text: str, battle_format: str) -> tuple[bool, str]:
    from poke_env.teambuilder import ConstantTeambuilder
    packed = ConstantTeambuilder(text).yield_team()
    r = subprocess.run(
        ["node", "pokemon-showdown", "validate-team", battle_format],
        input=packed, capture_output=True, text=True,
        cwd="pokemon-showdown", timeout=60)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    battle_format = sys.argv[2] if len(sys.argv) > 2 else TRAINING_BATTLE_FORMAT

    ok = 0
    error_counter: Counter = Counter()
    for i in range(n):
        text = build_random_team_text(size=TRAINING_TEAM_SIZE)
        passed, msg = validate_team_text(text, battle_format)
        if passed:
            ok += 1
        else:
            first_line = msg.splitlines()[0] if msg else "(不明)"
            error_counter[first_line] += 1
            if error_counter[first_line] == 1:
                print(f"NG (team {i}): {msg[:300]}")
    print(f"\n合格: {ok}/{n} (format={battle_format})")
    for err, cnt in error_counter.most_common():
        print(f"  x{cnt}: {err}")
    sys.exit(0 if ok == n else 1)


if __name__ == "__main__":
    main()
