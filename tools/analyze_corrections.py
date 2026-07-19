"""手動修正ログの分析: どの要素がどのシーンでどれくらい直されたか。

誤認識ランキングを出し、以後の認識改善の対象をデータで決める。

使い方: python -m tools.analyze_corrections [logs/battles]
"""
import glob
import json
import sys
from collections import Counter


def main(log_dir="logs/battles"):
    fixes = []
    for path in sorted(glob.glob(f"{log_dir}/*.jsonl")):
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            if r.get("type") == "manual_fix":
                d = r.get("detail") or {}
                fixes.append({
                    "battle": path.split("/")[-1],
                    "turn": r.get("turn"),
                    "scene": r.get("scene"),
                    "target": d.get("target"),
                    "field": d.get("field"),
                    "label": d.get("label"),
                    "before": d.get("before"),
                    "after": d.get("after"),
                })
    if not fixes:
        print("手動修正の記録はまだありません")
        return

    print(f"手動修正 合計 {len(fixes)}件 ({len({f['battle'] for f in fixes})}試合)\n")
    print("== 要素別 (誤認識ランキング) ==")
    for (target, field), n in Counter(
            (f["target"], f["field"]) for f in fixes).most_common():
        print(f"  {target}/{field}: {n}件")
    print("\n== シーン別 ==")
    for scene, n in Counter(f["scene"] for f in fixes).most_common():
        print(f"  {scene}: {n}件")
    print("\n== 直近の修正 ==")
    for f in fixes[-10:]:
        print(f"  [{f['battle']} T{f['turn']}] {f['label']}: "
              f"{f['before']} -> {f['after']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/battles")
