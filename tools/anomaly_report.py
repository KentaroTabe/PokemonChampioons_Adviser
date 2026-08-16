"""対戦ログ内の矛盾 (誤認候補) の一覧をフレーム無しで作る。

    python -m tools.anomaly_report --last 10

audit_session はフレーム画像との突き合わせ (sonnet) が前提だが、
フレーム未保存のセッションでも「ログ内部の整合性」からは誤認を検出できる:
ひんし後のHP再表示・急回復・7匹超過・種族の入れ替わりなど。
画面と照合していないため「候補」であり確定ではない (視覚監査の代替ではない)。
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from collections import Counter
from pathlib import Path

from tools.audit_session import detect_anomalies

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
OUT_DIR = REPO / "logs" / "audit_reports"


def species_flips(battle_log: str) -> list:
    """同じ枠の種族名が入れ替わった箇所 (視覚同定ミスの痕跡)"""
    out = []
    prev: dict = {}
    for line in open(battle_log, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "scene":
            continue
        st = d.get("state") or {}
        for side in ("player", "opponent"):
            party = (st.get(side) or {}).get("party") or []
            for i, p in enumerate(party):
                ja = p.get("ja")
                if not ja:
                    continue
                key = (side, i)
                if key in prev and prev[key] != ja:
                    out.append((d.get("t", 0.0),
                                f"{side}[{i}] {prev[key]} → {ja} へ変化"))
                prev[key] = ja
    # 同一組み合わせは1件に圧縮
    seen, dedup = set(), []
    for t, desc in out:
        if desc in seen:
            continue
        seen.add(desc)
        dedup.append((t, desc))
    return dedup


def main() -> None:
    ap = argparse.ArgumentParser(description="ログ内矛盾による誤認候補一覧")
    ap.add_argument("--last", type=int, default=10)
    args = ap.parse_args()

    files = sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))[-args.last:]
    lines = [f"# 誤認候補一覧 (ログ内矛盾ベース / {time.strftime('%Y-%m-%d %H:%M')})",
             "",
             f"- 対象: 直近{len(files)}戦",
             "- ⚠ フレーム未保存のため画面との突き合わせ (視覚監査) は未実施。",
             "  ここに挙がるのはログ内部の整合性から機械検出した「候補」であり、",
             "  実際の画面がどうだったかは確認していない。",
             ""]

    total = Counter()
    for f in files:
        name = Path(f).name
        anoms = detect_anomalies(f)
        flips = species_flips(f)
        if not anoms and not flips:
            continue
        lines.append(f"## {name}")
        for t, desc in anoms:
            ts = time.strftime("%H:%M:%S", time.localtime(t))
            lines.append(f"- [{ts}] {desc}")
            total[desc.split(" (")[0].split(":")[-1].strip()[:20]] += 1
        for t, desc in flips:
            ts = time.strftime("%H:%M:%S", time.localtime(t))
            lines.append(f"- [{ts}] 種族の入れ替わり: {desc}")
            total["種族の入れ替わり"] += 1
        lines.append("")

    lines.append("## 集計")
    if total:
        for k, c in total.most_common():
            lines.append(f"- {k}: {c}件")
    else:
        lines.append("- 検出なし")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"anomalies_{time.strftime('%Y%m%d_%H%M')}.md"
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"保存: {out}")


if __name__ == "__main__":
    main()
