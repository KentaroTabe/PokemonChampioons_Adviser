"""敗因分析: 実戦ログ (logs/battles/*.jsonl) の集計。

「なかなか勝てない」の原因を実データから特定する。統計は4つの母集団
ベースで全件出力する (件数フィルタなし。少数サンプルは件数で判断):
  - 自分の選出3匹ベースの勝率
  - 相手パーティ6匹ベースの負け寄与ランキング
  - 相手の「選出された3匹」ベースの成績 (実際に場に出てきた相手)
  - 相手の「選出されなかった3匹」ベースの成績 (選出誘導の検出)
ほか勝敗/レート推移/選出トリオ/ローカルメタ。
レポートは logs/battle_analysis/analysis_<時刻>.md にも保存される。

    python -m tools.analyze_battles                # 全対戦
    python -m tools.analyze_battles --last 30      # 直近30戦
    python -m tools.analyze_battles --days 7       # 直近7日
    python -m tools.analyze_battles --json         # 機械可読出力 (保存なし)
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
MARKER = REPO / "logs" / ".connection_test_start"   # 接続テスト開始時刻

_BATTLE_SCENES = {"command", "move_select", "watch",
                  "field_check", "battle_hud", "field"}


def _parse_battle(path: str) -> dict:
    outcome, inferred, rates = None, False, []
    opp_species: set = set()      # 相手ロースター (選出画面の6匹)
    opp_fielded: set = set()      # 実際に選出された相手 (対戦中にHP観測/場に出た)
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
            in_battle = d.get("scene") in _BATTLE_SCENES
            if in_battle:
                n_battle_scenes += 1
            opp = st.get("opponent") or {}
            for i, p in enumerate(opp.get("party", [])):
                if not p.get("ja"):
                    continue
                opp_species.add(p["ja"])
                # 対戦中シーンでHPが観測された/場に出ていた個体 = 選出された
                if in_battle and (p.get("hp") is not None
                                  or i == opp.get("active")):
                    opp_fielded.add(p["ja"])
            for p in (st.get("player") or {}).get("party", []):
                if p.get("picked") and p.get("ja"):
                    my_picked.add(p["ja"])
    return {"file": Path(path).name, "t0": t0 or 0.0,
            "outcome": outcome, "inferred": inferred,
            "rate": rates[-1] if rates else None,
            "opp_species": sorted(opp_species),
            "opp_fielded": sorted(opp_fielded),
            "opp_benched": sorted(opp_species - opp_fielded),
            "my_picked": sorted(my_picked),
            "n_battle_scenes": n_battle_scenes}


def session_start_ts() -> float | None:
    """接続テスト開始マーカーの時刻 (無ければ None)"""
    try:
        return float(MARKER.read_text().strip())
    except (OSError, ValueError):
        return None


def load_battles(days: float | None = None, last: int | None = None,
                 since_ts: float | None = None) -> list:
    """対戦ログを新しい順に読み込む (対戦シーンが無いログは除外)"""
    files = sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))
    if since_ts:
        files = [f for f in files if Path(f).stat().st_mtime >= since_ts]
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

    def tally(key: str) -> dict:
        out: dict = {}
        for b in decided:
            for sp in b.get(key) or []:
                st = out.setdefault(sp, [0, 0])   # [win, loss]
                st[0 if b["outcome"] == "win" else 1] += 1
        return out

    opp_stats = tally("opp_species")        # 相手ロースター6匹ベース
    opp_fielded_stats = tally("opp_fielded")  # 相手の選出された3匹ベース
    opp_benched_stats = tally("opp_benched")  # 相手の選出されなかった3匹
    pick_stats = tally("my_picked")           # 自分の選出3匹ベース
    trio_stats: Counter = Counter()
    for b in decided:
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
            "opp_fielded_stats": opp_fielded_stats,
            "opp_benched_stats": opp_benched_stats,
            "pick_stats": pick_stats, "trio_stats": trio_stats,
            "encounters": encounters}


def _stat_lines(stats: dict, sort: str = "loss") -> list:
    """[(種族, W, L)] を整形。sort: 'loss'=勝率昇順 / 'win'=勝率降順。
    件数フィルタは掛けない (全件を出す。少数サンプルは件数で判断できる)"""
    rows = [(sp, w, l) for sp, (w, l) in stats.items()]
    if sort == "loss":
        rows.sort(key=lambda x: (x[1] / (x[1] + x[2]), -(x[1] + x[2])))
    else:
        rows.sort(key=lambda x: (-(x[1] / (x[1] + x[2])), -(x[1] + x[2])))
    return [f"  {sp}: {w}勝{l}敗 ({w / (w + l):.0%})" for sp, w, l in rows]


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

    if s["pick_stats"]:
        lines.append("\n🎯 自分の選出3匹ベースの勝率 (全件):")
        lines += _stat_lines(s["pick_stats"], sort="win")

    if s["opp_stats"]:
        lines.append("\n⚠ 相手パーティ6匹ベースの負け寄与ランキング (全件):")
        lines += _stat_lines(s["opp_stats"], sort="loss")

    if s["opp_fielded_stats"]:
        lines.append("\n⚔ 相手の「選出された3匹」ベースの成績 "
                     "(実際に場に出てきた相手):")
        lines += _stat_lines(s["opp_fielded_stats"], sort="loss")

    if s["opp_benched_stats"]:
        lines.append("\n🪑 相手の「選出されなかった3匹」ベースの成績 "
                     "(居るだけで選出を歪められた相手の検出用):")
        lines += _stat_lines(s["opp_benched_stats"], sort="loss")

    if s["trio_stats"]:
        agg: dict = {}
        for (trio, outcome), n in s["trio_stats"].items():
            st = agg.setdefault(trio, [0, 0])
            st[0 if outcome == "win" else 1] += n
        top = sorted(agg.items(), key=lambda x: -(x[1][0] + x[1][1]))[:8]
        lines.append("\n👥 自分の選出トリオ別 (登場回数順):")
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


def run_report(days: float | None = None, last: int | None = None,
               session: bool = False):
    """分析を実行し (レポート文字列, 保存先Path|None) を返す。

    session=True: 接続テスト開始マーカー以降の全対戦を対象にする
    (件数上限なし。マーカーが無ければ last/days にフォールバック)
    """
    since_ts = None
    scope = ""
    if session:
        since_ts = session_start_ts()
        if since_ts is not None:
            last, days = None, None
            scope = " (接続テストセッション全体)"
    battles = load_battles(days=days, last=last, since_ts=since_ts)
    if not battles:
        return "対象の対戦ログがありません", None
    text = report(summarize(battles))
    out_dir = REPO / "logs" / "battle_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"analysis_{time.strftime('%Y%m%d_%H%M')}.md"
    header = (f"# 対戦ログ分析 ({time.strftime('%Y-%m-%d %H:%M')})\n"
              f"対象: {len(battles)}戦" + scope
              + (f" (直近{last}戦)" if last else "")
              + (f" (直近{days}日)" if days else "") + "\n\n")
    path.write_text(header + text + "\n", encoding="utf-8")
    return text + f"\n\n保存: {path}", path


def main() -> None:
    ap = argparse.ArgumentParser(description="対戦ログの敗因分析")
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--last", type=int, default=None)
    ap.add_argument("--session", action="store_true",
                    help="接続テスト開始マーカー以降の全対戦を対象 (件数上限なし)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.json:
        battles = load_battles(days=args.days, last=args.last)
        if not battles:
            print("対象の対戦ログがありません")
            return
        s = dict(summarize(battles))
        s["trio_stats"] = {f"{'/'.join(k[0])}|{k[1]}": v
                           for k, v in s["trio_stats"].items()}
        s["encounters"] = dict(s["encounters"])
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        text, _ = run_report(days=args.days, last=args.last,
                             session=args.session)
        print(text)


if __name__ == "__main__":
    main()
