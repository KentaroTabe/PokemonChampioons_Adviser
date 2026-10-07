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

from champions_agent.config import RATE_CHAIN_GAP_SEC, RATE_INFER_MIN_DELTA, RATE_MAX_DELTA_PER_BATTLE
from tools.battle_outcome import OutcomeTracker, apply_rate_chain
from tools.pick_labels import complete_subset, split_status, status_by_ja

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
MARKER = REPO / "logs" / ".connection_test_start"   # 接続テスト開始時刻

_BATTLE_SCENES = {"command", "move_select", "watch",
                  "field_check", "battle_hud", "field"}
# 対戦シーンがこれ未満のログは断片 (対戦の合間の誤分類など) とみなして集計から外す
MIN_BATTLE_SCENES = 3


def _parse_battle(path: str) -> dict:
    rates = []
    ot = OutcomeTracker()         # 勝敗: outcome 行 (最後) + ランク画面前の勝負文言 (tools.battle_outcome)
    opp_species: set = set()      # 相手ロースター (選出画面の6匹)
    opp_fielded: set = set()      # 実際に選出された相手 (対戦中にHP観測/場に出た)
    my_picked: set = set()
    slot_last: dict = {}          # 相手の枠 index → 最後に見えた種 (途中で置き換わった枠の前の種は誤同定)
    n_battle_scenes = 0
    t0 = None
    t1 = None
    picks_row = None              # 選出ラベル 3 値 (opp_picks 行、2026-10-07 段 0)。無い古いログは従来の推定
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t0 = t0 or d.get("t")
        t1 = d.get("t") or t1
        typ = d.get("type")
        ot.feed(d)
        if typ == "opp_picks":
            picks_row = d
        elif typ == "rate":
            if d.get("value") is not None and (not rates or rates[-1] != float(d["value"])):
                rates.append(float(d["value"]))
        elif typ == "scene":
            st = d.get("state") or {}
            in_battle = d.get("scene") in _BATTLE_SCENES
            if in_battle:
                n_battle_scenes += 1
            opp = st.get("opponent") or {}
            for i, p in enumerate(opp.get("party", [])):
                if not p.get("ja"):
                    continue
                slot_last[i] = p["ja"]
                if p.get("guess"):
                    # guess = 選出画面の推定 (タイプアイコン + スプライト照合、未確定)。相手の 6 体には数えない
                    # (2026-09-29 第17回: 推定の誤り (セグレイブ→カイリュー、ゴリランダー→メガニウム) が
                    # 「相手パーティ 7 種」として集計に混ざった)
                    continue
                opp_species.add(p["ja"])
                # 対戦中シーンでHPが観測された/場に出ていた個体 = 選出された
                if in_battle and (p.get("hp") is not None
                                  or i == opp.get("active")):
                    opp_fielded.add(p["ja"])
            for p in (st.get("player") or {}).get("party", []):
                if p.get("picked") and p.get("ja"):
                    my_picked.add(p["ja"])
    # 枠の種が途中で別の種に置き換わったら前の種は誤同定 (2026-09-29 第17回: カイリュー と推定した枠の実体が
    # セグレイブ)。場に出た種は残す。guess の印が無い古いログにも効く
    final = set(slot_last.values())
    opp_species = {ja for ja in opp_species if ja in final or ja in opp_fielded}
    # 勝負の文言は最も強い根拠 (2026-09-29 第17回 15:53: 3 体目のひんしからの誤った「負け」の後に「勝負に勝った」)
    outcome, inferred, corrected = ot.result()
    # 相手の「選出されなかった」: 選出ラベル 3 値があれば非選出確定だけ (選出されたが場に出なかった個体を選出外にしない)、
    # 無ければ従来の推定 (ロースター − 場に出た)
    status = status_by_ja(picks_row)
    if status is not None:
        _picked, unpicked, unknown = split_status(status)
        benched = sorted(set(unpicked) & opp_species)
        pick_unknown = sorted(set(unknown) & opp_species)
        complete = bool(picks_row.get("complete"))
    else:
        benched, pick_unknown, complete = sorted(opp_species - opp_fielded), None, None
    return {"file": Path(path).name, "t0": t0 or 0.0, "t1": t1 or t0 or 0.0,
            "outcome": outcome, "inferred": inferred, "corrected": corrected,
            "rate": rates[-1] if rates else None,
            "reads": rates,     # 読めたレート (値が変わるたびに 1 つ)。対戦をまたいだ並びの解決に使う (load_battles)
            "opp_species": sorted(opp_species),
            "opp_fielded": sorted(opp_fielded),
            "opp_benched": benched,
            "opp_pick_status": status,           # {和名: pick_status} (ラベルの無い古いログは None)
            "opp_pick_unknown": pick_unknown,    # 選出が分からない個体 (ラベルの無い古いログは None)
            "opp_picks_complete": complete,      # 選出 3 体がすべて判明 (ラベルの無い古いログは None → picks_complete が推定)
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
    battles = [b for b in battles if b["n_battle_scenes"] >= MIN_BATTLE_SCENES]
    # レートの読みの並びを対戦をまたいで解き、不明・推定の勝敗を埋める / 直す (2026-10-06 第18回: 推定の 1 戦が誤り、
    # 不明の 2 戦が勝ちだった。確定した勝敗は変えない。tools.battle_outcome.apply_rate_chain)
    apply_rate_chain(battles, RATE_CHAIN_GAP_SEC, RATE_INFER_MIN_DELTA, RATE_MAX_DELTA_PER_BATTLE)
    if last:
        battles = battles[-last:]
    return battles


def rate_flags(battles: list, max_delta: float = RATE_MAX_DELTA_PER_BATTLE) -> list:
    """連続する対戦 (時系列順、レート観測ありのもの) のレート差から、読み違いの疑い (|Δ| が 1 戦の変動 max_delta を超える) と
    勝敗との矛盾 (記録は勝ちなのに下がった / 負けなのに上がった) を出す (純粋)。自動では直さない。
    (2026-09-29 第17回: 15:25 → 15:34 の差 −38 は数字の誤読、15:53 の +15.6 は「負け」の誤記録を示していた)
    2026-10-06: ランク画面の読みは対戦前の値のことがあり、最後の読みどうしの差は 1 戦の増減とは限らない (2 戦分や 0 のこともある)。
    対戦をまたいだ並びの解決 (apply_rate_chain の rate_chain) が付いていて、読みが並びで説明できている (組み合わせが残り、
    誤読の疑いの印が無い) 対戦には、この差からの印を出さない (誤検出を避ける)。
    戻り値: [{"file", "delta", "outcome", "flag"}] (flag は None か説明文)"""
    out, prev = [], None
    for b in battles:
        r = b.get("rate")
        if r is None:
            continue
        if prev is not None:
            delta = r - prev
            flag = None
            chain = b.get("rate_chain") or {}
            explained = chain.get("n_solutions", 0) >= 1 and not chain.get("suspect_reads")
            if abs(delta) > max_delta and not explained:
                flag = f"読み違いの疑い (1 戦の変動 {max_delta:.0f} を超える)"
            elif not explained and b.get("outcome") == "win" and delta < 0:
                flag = "勝敗と矛盾 (記録は勝ちだがレートが下がった)"
            elif not explained and b.get("outcome") == "loss" and delta > 0:
                flag = "勝敗と矛盾 (記録は負けだがレートが上がった)"
            out.append({"file": b.get("file"), "delta": round(delta, 1), "outcome": b.get("outcome"), "flag": flag})
        prev = r
    return out


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
            "rates": rates, "rate_flags": rate_flags(battles),
            "n_corrected": sum(1 for b in battles if b.get("corrected")),
            # レートの読みの並びで埋めた / 直した勝敗 (apply_rate_chain): [(ファイル, 記録, 並びから)]
            "by_rate": [(b["file"], b.get("outcome_recorded"), b["outcome"]) for b in battles if b.get("by_rate")],
            "suspect_reads": [b["file"] for b in battles if (b.get("rate_chain") or {}).get("suspect_reads")],
            "opp_stats": opp_stats,
            "opp_fielded_stats": opp_fielded_stats,
            "opp_benched_stats": opp_benched_stats,
            # 相手の選出 3 体がすべて判明した対戦だけの「選出された 3 匹」ベースの成績と、その部分集合の件数 (2026-10-07 段 0)
            "opp_complete": complete_subset(battles, "opp_fielded"),
            "n_pick_labels": sum(1 for b in battles if b.get("opp_pick_status") is not None),
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
        for r in s.get("rate_flags") or []:
            if r.get("flag"):
                lines.append(f"  ⚠ {r['file']}: 前戦比 {r['delta']:+.1f}: {r['flag']}")
    if s.get("n_corrected"):
        lines.append(f"勝敗の訂正: {s['n_corrected']}戦 (勝負の文言が先の記録と食い違い、文言を採用)")
    for fname, recorded, got in s.get("by_rate") or []:
        lines.append(f"  レートの並びから勝敗を決定: {fname}: 記録 {recorded or '不明'} → {got}")
    for fname in s.get("suspect_reads") or []:
        lines.append(f"  ⚠ {fname}: レートの読みが並びと合わない (数字の誤読の疑い。この対戦の読みを除いて解いた)")

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

    oc = s.get("opp_complete") or {}
    if oc.get("n_complete"):
        lines.append(f"\n🧩 相手の選出 3 匹がすべて判明した対戦だけの「選出された3匹」ベースの成績 "
                     f"(部分集合 {oc['n_complete']}/{oc['n_battles']}戦、勝敗確定 {oc['n_decided']}戦):")
        lines += _stat_lines(oc["stats"], sort="loss")

    if s["opp_benched_stats"]:
        lines.append("\n🪑 相手の「選出されなかった3匹」ベースの成績 "
                     "(居るだけで選出を歪められた相手の検出用"
                     + (f"。選出ラベルのある {s.get('n_pick_labels')}戦は非選出確定だけ、無い対戦は場に出なかった個体"
                        if s.get("n_pick_labels") else "") + "):")
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
