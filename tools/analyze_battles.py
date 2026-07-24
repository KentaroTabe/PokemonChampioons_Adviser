"""敗因分析: 実戦ログ (logs/battles/*.jsonl) の集計。

「なかなか勝てない」の原因を実データから特定する:
  - 勝敗とレート推移
  - 負け寄与ランキング (どの相手ポケモンがいる対戦で負けているか)
  - 自分の選出別勝率 (誰を出した対戦で勝てて/負けているか)
  - ローカルメタ (自分のレート帯で実際に当たる相手の頻度。
    使用率DBは上位帯の集計なので、自分の環境とはズレることがある)

    python -m tools.analyze_battles                # 全対戦
    python -m tools.analyze_battles --last 30      # 直近30戦
    python -m tools.analyze_battles --days 7       # 直近7日
    python -m tools.analyze_battles --json         # 機械可読出力
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"

_BATTLE_SCENES = {"command", "move_select", "watch",
                  "field_check", "battle_hud", "field"}


def _parse_battle(path: str) -> dict:
    outcome, inferred, rates = None, False, []
    opp_species: set = set()
    my_picked: set = set()
    n_battle_scenes = 0
    t0 = None
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t0 = t0 or d.get("t")
        typ = d.get("type")
        if typ == "outcome":
            outcome = d.get("outcome")
            inferred = bool(d.get("inferred"))
        elif typ == "rate":
            rates.append(d.get("value"))
        elif typ == "scene":
            st = d.get("state") or {}
            if d.get("scene") in _BATTLE_SCENES:
                n_battle_scenes += 1
            for p in (st.get("opponent") or {}).get("party", []):
                if p.get("ja"):
                    opp_species.add(p["ja"])
            for p in (st.get("player") or {}).get("party", []):
                if p.get("picked") and p.get("ja"):
                    my_picked.add(p["ja"])
    return {"file": Path(path).name, "t0": t0 or 0.0,
            "outcome": outcome, "inferred": inferred,
            "rate": rates[-1] if rates else None,
            "opp_species": sorted(opp_species),
            "my_picked": sorted(my_picked),
            "n_battle_scenes": n_battle_scenes}


def load_battles(days: float | None = None, last: int | None = None) -> list:
    """対戦ログを新しい順に読み込む (対戦シーンが無いログは除外)"""
    files = sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))
    if days:
        cutoff = time.time() - days * 86400
        files = [f for f in files if Path(f).stat().st_mtime >= cutoff]
    battles = [_parse_battle(f) for f in files]
    battles = [b for b in battles if b["n_battle_scenes"] >= 3]
    if last:
        battles = battles[-last:]
    return battles


def summarize(battles: list) -> dict:
    decided = [b for b in battles if b["outcome"] in ("win", "loss")]
    wins = sum(1 for b in decided if b["outcome"] == "win")
    rates = [(b["t0"], b["rate"]) for b in battles if b["rate"] is not None]

    # 相手種族ごとの成績 (その種族がいた対戦の勝敗)
    opp_stats: dict = {}
    for b in decided:
        for sp in b["opp_species"]:
            st = opp_stats.setdefault(sp, [0, 0])   # [win, loss]
            st[0 if b["outcome"] == "win" else 1] += 1
    # 自分の選出ごとの成績
    pick_stats: dict = {}
    trio_stats: Counter = Counter()
    for b in decided:
        for sp in b["my_picked"]:
            st = pick_stats.setdefault(sp, [0, 0])
            st[0 if b["outcome"] == "win" else 1] += 1
        if len(b["my_picked"]) == 3:
            trio_stats[(tuple(b["my_picked"]), b["outcome"])] += 1
    # 遭遇頻度 (ローカルメタ)
    encounters = Counter()
    for b in battles:
        for sp in b["opp_species"]:
            encounters[sp] += 1

    return {"n": len(battles), "n_decided": len(decided), "wins": wins,
            "losses": len(decided) - wins,
            "win_rate": wins / len(decided) if decided else None,
            "rates": rates, "opp_stats": opp_stats,
            "pick_stats": pick_stats, "trio_stats": trio_stats,
            "encounters": encounters}


def report(s: dict) -> str:
    lines = [f"📊 対戦ログ分析: {s['n']}戦 (勝敗確定 {s['n_decided']}戦)"]
    if s["win_rate"] is not None:
        lines.append(f"勝敗: {s['wins']}勝{s['losses']}敗 "
                     f"(勝率 {s['win_rate']:.0%})")
    if s["rates"]:
        vals = [r for _, r in s["rates"]]
        lines.append(f"レート: {vals[0]:.0f} → {vals[-1]:.0f} "
                     f"(最高{max(vals):.0f} / 最低{min(vals):.0f}, "
                     f"観測{len(vals)}回)")

    hard = [(sp, w, l) for sp, (w, l) in s["opp_stats"].items()
            if w + l >= 3]
    hard.sort(key=lambda x: (x[1] / (x[1] + x[2]), -(x[1] + x[2])))
    if hard:
        lines.append("\n⚠ 負け寄与ランキング (この相手がいた対戦の成績、遭遇3戦以上):")
        for sp, w, l in hard[:8]:
            lines.append(f"  {sp}: {w}勝{l}敗 (勝率 {w / (w + l):.0%})")

    picks = [(sp, w, l) for sp, (w, l) in s["pick_stats"].items()
             if w + l >= 3]
    picks.sort(key=lambda x: -(x[1] / (x[1] + x[2])))
    if picks:
        lines.append("\n🎯 自分の選出別勝率 (選出3戦以上):")
        for sp, w, l in picks:
            lines.append(f"  {sp}: {w}勝{l}敗 ({w / (w + l):.0%})")

    if s["trio_stats"]:
        agg: dict = {}
        for (trio, outcome), n in s["trio_stats"].items():
            st = agg.setdefault(trio, [0, 0])
            st[0 if outcome == "win" else 1] += n
        top = sorted(agg.items(), key=lambda x: -(x[1][0] + x[1][1]))[:5]
        lines.append("\n👥 選出トリオ別 (登場回数順):")
        for trio, (w, l) in top:
            lines.append(f"  {'/'.join(trio)}: {w}勝{l}敗")

    if s["encounters"]:
        total = s["n"]
        lines.append("\n🌍 ローカルメタ (実際に当たった相手の頻度):")
        for sp, n in s["encounters"].most_common(12):
            lines.append(f"  {sp}: {n}戦 ({n / total:.0%})")
        lines.append("  ※使用率DB (上位帯) と違う顔ぶれなら、対策は"
                     "こちらを優先する価値がある")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="対戦ログの敗因分析")
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--last", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    battles = load_battles(days=args.days, last=args.last)
    if not battles:
        print("対象の対戦ログがありません")
        return
    s = summarize(battles)
    if args.json:
        s = dict(s)
        s["trio_stats"] = {f"{'/'.join(k[0])}|{k[1]}": v
                           for k, v in s["trio_stats"].items()}
        s["encounters"] = dict(s["encounters"])
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print(report(s))


if __name__ == "__main__":
    main()
