"""選出データセットの中身を要約する。

    python -m tools.selection_stats [npzのパス]

収集 (tools/collect_selection_data.py) の進捗確認と、学習前の健全性チェックに使う。
「自チームが何種類あるか」「相手チームが記録されているか」を見ないと、
単一チーム専用のモデルを汎用モデルと誤認してしまう。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

from champions_agent.train.train_selection import DATA_PATH


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_PATH
    if not path.exists():
        print(f"データがありません: {path}")
        return
    d = np.load(path)
    n = len(d["action"])
    team_key = ["|".join(sorted(str(s) for s in t)) for t in d["team"]]
    reward = d["reward"]

    print(f"■ 選出データ {path}")
    print(f"  件数        : {n}")
    print(f"  自チーム種類: {len(set(team_key))}")
    if "opp_team" in d.files:
        opp_key = ["|".join(sorted(str(s) for s in t if str(s)))
                   for t in d["opp_team"]]
        n_blank = sum(1 for k in opp_key if not k)
        print(f"  相手チーム種類: {len(set(opp_key))}"
              + (f" (未記録 {n_blank}件)" if n_blank else ""))
    else:
        print("  相手チーム  : 未記録 (古い形式。収集し直しを推奨)")
    print(f"  全体勝率    : {reward.mean():.3f}")
    print(f"  選出パターン: {len(set(d['action'].tolist()))}/120 種を網羅")

    # 1チームあたりの件数が少なすぎると、そのチームの選出を学べない
    per_team = Counter(team_key)
    counts = np.array(sorted(per_team.values()))
    print(f"  1チームあたり件数: 中央値{int(np.median(counts))} "
          f"/ 最小{counts.min()} / 最大{counts.max()}")
    if np.median(counts) < 120:
        print("  ※ 1チーム120通りに対し件数が少ない。"
              "個別チームの丸暗記ではなく、埋め込み経由の汎化に頼る形になる")


if __name__ == "__main__":
    main()
