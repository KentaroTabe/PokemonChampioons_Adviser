"""実戦評価 (遵守率つき): 接続テストの実戦ログ (logs/battles、由来ラベルつき) から
実勝率の CI、助言への遵守率、実戦/合成の重みづけ Score を出す。

- 200 戦は early signal。w_N = real_weight(有効標本, CI 半幅) で実戦の重みを増やす (§9-5)
- 遵守率: decision_audit の「従えたか」(助言と実行の一致) を使う。ユーザー行動を模倣教師にはしない
- 反事実評価 (助言 vs 実行を探索器で再評価) は advice_replay の経路を使う (後続)

CLI (試用中 Package の実戦サマリー。scripts/canary_summary.sh と end_connection_test.sh が使う):

    python -m tools.team_build.real_eval                     # experiment ラベル (logs/.experiment_package) の Package
    python -m tools.team_build.real_eval --package <id>
    python -m tools.team_build.real_eval --session           # + 接続テストのマーカー以降だけの集計 (決定監査もその範囲)
    python -m tools.team_build.real_eval --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from tools.team_build.verdict import binomial_halfwidth, blended_score, real_weight

REPO = Path(__file__).resolve().parent.parent.parent
BATTLES_DIR = REPO / "logs" / "battles"
EXPERIMENT_MARK = REPO / "logs" / ".experiment_package"      # promote --experiment が書く
SESSION_MARKER = REPO / "logs" / ".connection_test_start"    # start_connection_test.sh が書く


def read_battle_labels(path: Path) -> dict:
    """1 対戦ログの由来ラベル (session 行) と勝敗、助言/実行の件数"""
    out = {"file": Path(path).name, "path": str(path), "source": "organic", "package_id": None, "outcome": None,
           "n_advice": 0, "n_manual_fix": 0}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            t = d.get("type")
            if t == "session":
                out["source"] = d.get("source", "organic")
                out["package_id"] = d.get("package_id")
            elif t == "outcome" and d.get("outcome") in ("win", "loss"):
                out["outcome"] = d["outcome"]
            elif t == "advice":
                out["n_advice"] += 1
            elif t == "manual_fix":
                out["n_manual_fix"] += 1
    except Exception:
        pass
    return out


def labeled_rows(battles_dir: Path = BATTLES_DIR, package_id: Optional[str] = None, source: Optional[str] = None,
                 since_ts: Optional[float] = None) -> list:
    """由来ラベルで絞った対戦ログの行 (since_ts はファイルの更新時刻で絞る。analyze_battles --session と同じ規則)"""
    rows = []
    for p in sorted(Path(battles_dir).glob("battle_*.jsonl")):
        if since_ts is not None and p.stat().st_mtime < since_ts:
            continue
        r = read_battle_labels(p)
        if package_id and r["package_id"] != package_id:
            continue
        if source and r["source"] != source:
            continue
        rows.append(r)
    return rows


def summarize_rows(rows: list) -> dict:
    decided = [r for r in rows if r["outcome"]]
    n = len(decided)
    wins = sum(1 for r in decided if r["outcome"] == "win")
    wr = wins / n if n else None
    hw = binomial_halfwidth(wr, n) if n else None
    return {"n_logs": len(rows), "n_decided": n, "wins": wins, "win_rate": wr,
            "ci_halfwidth": (round(hw, 4) if hw is not None else None),
            "weight": (real_weight(n, hw) if n else 0.0),
            "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in ("organic", "recommended", "experiment")}}


def real_summary(package_id: Optional[str] = None, source: Optional[str] = None,
                 battles_dir: Path = BATTLES_DIR, since_ts: Optional[float] = None) -> dict:
    return summarize_rows(labeled_rows(battles_dir, package_id=package_id, source=source, since_ts=since_ts))


def compliance_from_audit(audit_rows: list) -> Optional[float]:
    """decision_audit --json の行 (各決定の followed: bool) から遵守率"""
    vals = [bool(r.get("followed")) for r in audit_rows if "followed" in r]
    return round(sum(vals) / len(vals), 3) if vals else None


def blended(real: dict, wr_synthetic: float) -> dict:
    w = real.get("weight", 0.0)
    return {"w_real": round(w, 3), "wr_real": real.get("win_rate"), "wr_synthetic": wr_synthetic,
            "score": round(blended_score(real.get("win_rate"), wr_synthetic, w), 4)}


def _ratio(a: int, b: int) -> Optional[float]:
    return round(a / b, 3) if b else None


def audit_aggregate(paths: list) -> Optional[dict]:
    """決定監査 (tools.decision_audit) を対戦ログに掛けて集計する: 決定数 / 助言あり / 一致 (= 遵守率) / 時間内 / 欠陥。
    decision_audit は画面認識 (cv2) を読み込むので遅延 import。読み込めない環境では None"""
    try:
        from tools.decision_audit import _load, audit_battle
    except ImportError:
        return None
    agg = {"n_battles": 0, "n_decisions": 0, "n_with_advice": 0, "n_agree": 0,
           "n_latency_known": 0, "n_timely": 0, "n_defects": 0}
    for p in paths:
        a = audit_battle(_load(str(p)))
        agg["n_battles"] += 1
        for k in ("n_decisions", "n_with_advice", "n_agree", "n_latency_known", "n_timely"):
            agg[k] += int(a.get(k) or 0)
        agg["n_defects"] += len(a.get("defects") or [])
    agg["advice_rate"] = _ratio(agg["n_with_advice"], agg["n_decisions"])
    agg["compliance"] = _ratio(agg["n_agree"], agg["n_with_advice"])
    agg["timely_rate"] = _ratio(agg["n_timely"], agg["n_latency_known"])
    return agg


def format_summary(s: dict, title: str) -> str:
    lines = [f"[{title}] 対戦ログ {s['n_logs']} / 勝敗確定 {s['n_decided']} (勝 {s['wins']} 敗 {s['n_decided'] - s['wins']})"]
    if s["win_rate"] is not None:
        lines.append(f"  勝率 {s['win_rate']:.1%} ± {s['ci_halfwidth']:.1%} (実戦の重み w={s['weight']:.2f})")
    else:
        lines.append("  勝率: 勝敗確定の対戦がまだ無い")
    bs = s["by_source"]
    lines.append(f"  由来: experiment {bs['experiment']} / recommended {bs['recommended']} / organic {bs['organic']}")
    return "\n".join(lines)


def format_audit(a: Optional[dict]) -> str:
    if a is None:
        return "  決定監査: 実行できない環境 (tools.decision_audit を読み込めない)"
    if not a["n_decisions"]:
        return f"  決定監査: 決定なし ({a['n_battles']} 対戦)"

    def pct(x: Optional[float]) -> str:
        return "-" if x is None else f"{x:.0%}"

    return (f"  決定監査 ({a['n_battles']} 対戦): 決定 {a['n_decisions']} / 助言あり {pct(a['advice_rate'])} / "
            f"一致 (遵守率) {pct(a['compliance'])} / 時間内 {pct(a['timely_rate'])} / 欠陥 {a['n_defects']} 件")


def _read_ts(path: Path) -> Optional[float]:
    try:
        return float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="試用中 Package の実戦サマリー (由来ラベルつき対戦ログ)")
    ap.add_argument("--package", default=None, help="Package id (既定: logs/.experiment_package の値)")
    ap.add_argument("--session", action="store_true", help="接続テストのマーカー以降だけの集計も出す (決定監査もその範囲)")
    ap.add_argument("--no-audit", action="store_true", help="決定監査の集計を省く")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--battles-dir", default=str(BATTLES_DIR))
    args = ap.parse_args()
    package_id = args.package or (EXPERIMENT_MARK.read_text(encoding="utf-8").strip() if EXPERIMENT_MARK.exists() else "")
    if not package_id:
        raise SystemExit("experiment ラベルが無い (logs/.experiment_package)。--package <id> を指定してください")
    bdir = Path(args.battles_dir)
    out: dict = {"package_id": package_id, "all": real_summary(package_id=package_id, battles_dir=bdir)}
    since = None
    if args.session:
        since = _read_ts(SESSION_MARKER)
        out["session_start"] = since
        out["session"] = real_summary(package_id=package_id, battles_dir=bdir, since_ts=since) if since else None
    if not args.no_audit:
        rows = labeled_rows(bdir, package_id=package_id, since_ts=since)
        out["audit"] = audit_aggregate([r["path"] for r in rows])
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"Package {package_id} の実戦 (session 行の package_id が一致する対戦ログ)")
    print(format_summary(out["all"], "全期間"))
    if args.session:
        if out["session"] is None:
            print("  接続テストのマーカーが無い (今回分の集計は省略)")
        else:
            print(format_summary(out["session"], "今回の接続テスト (マーカー以降)"))
    if "audit" in out:
        print(format_audit(out["audit"]))


if __name__ == "__main__":
    main()
