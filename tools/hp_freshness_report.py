"""対戦ログからHP表示の固着 (鮮度) を実測する (P10 の後続観察用)。

    python -m tools.hp_freshness_report [--logs N] [--stale-sec 3.0]

各対戦ログの scene 行 (command / move_select) について、アクティブ個体の
hp_read_ts (最後に実読みで確定した時刻) と行の時刻 t の差を「固着経過秒」とし、
stale-sec を超える決定点の割合と最長を出す。hp_read_ts が無い古いログは
「計測不能」として数える。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "battles"
DECISION_SCENES = ("command", "move_select")


def summarize_rows(rows: list, stale_sec: float) -> dict:
    """ログ行 (dict) の列 -> {decisions, measurable, stale, max_age, per_side}"""
    out = {"decisions": 0, "measurable": 0, "stale": 0, "max_age": 0.0,
           "per_side": {"player": {"measurable": 0, "stale": 0},
                        "opponent": {"measurable": 0, "stale": 0}}}
    for r in rows:
        if r.get("type") != "scene" or r.get("scene") not in DECISION_SCENES:
            continue
        state = r.get("state") or {}
        t = r.get("t")
        out["decisions"] += 1
        for side in ("player", "opponent"):
            s = state.get(side) or {}
            idx = s.get("active_index")
            party = s.get("party") or []
            if idx is None or idx >= len(party) or t is None:
                continue
            ts = party[idx].get("hp_read_ts")
            if ts is None:
                continue
            age = max(0.0, float(t) - float(ts))
            out["measurable"] += 1
            out["per_side"][side]["measurable"] += 1
            out["max_age"] = max(out["max_age"], age)
            if age > stale_sec:
                out["stale"] += 1
                out["per_side"][side]["stale"] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="HP表示の固着 (鮮度) の実測")
    ap.add_argument("--logs", type=int, default=12)
    ap.add_argument("--stale-sec", type=float, default=3.0)
    args = ap.parse_args()
    paths = sorted(LOG_DIR.glob("battle_*.jsonl"))[-args.logs:]
    total = {"decisions": 0, "measurable": 0, "stale": 0, "max_age": 0.0}
    print(f"{'ログ':<32} {'決定点':>5} {'計測可':>5} {'固着':>4} {'固着率':>6} {'最長s':>6}")
    for p in paths:
        rows = []
        for line in p.open(encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        s = summarize_rows(rows, args.stale_sec)
        rate = s["stale"] / s["measurable"] if s["measurable"] else 0.0
        print(f"{p.name:<32} {s['decisions']:>5} {s['measurable']:>5} {s['stale']:>4} "
              f"{rate:>6.0%} {s['max_age']:>6.1f}")
        for k in ("decisions", "measurable", "stale"):
            total[k] += s[k]
        total["max_age"] = max(total["max_age"], s["max_age"])
    rate = total["stale"] / total["measurable"] if total["measurable"] else 0.0
    print(f"合計: 決定点{total['decisions']} / 計測可{total['measurable']} / "
          f"固着{total['stale']} ({rate:.0%}) / 最長{total['max_age']:.1f}s"
          + ("  ※ hp_read_ts の無い古いログは計測不能" if total["measurable"] == 0 else ""))


if __name__ == "__main__":
    main()
