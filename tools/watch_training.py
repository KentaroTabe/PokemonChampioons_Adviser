"""学習経過の観察ツール。

    python -m tools.watch_training               # サマリー (状態+性格別の推移)
    python -m tools.watch_training --history 20  # 評価履歴の一覧 (直近20サイクル)
    python -m tools.watch_training --follow      # ライブ追尾 (Ctrl+Cで終了)

nightly_*.log の評価結果 ([evaluate] 行) とSB3の進捗テーブルを解析し、
性格 (play_style) ごとの vs Random / vs ベンチマーク勝率の推移を表示する。
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "champions_agent" / "train" / "logs"
FOREVER_LOG = REPO / "logs" / "train_forever.log"

STYLE_RE = re.compile(r"^--- \[(\w+)\] 学習開始: (.+) ---")
EVAL_RE = re.compile(r"\[evaluate\] (\{.+\})")
STEPS_RE = re.compile(r"\|\s+total_timesteps\s+\|\s+(\d+)")
FPS_RE = re.compile(r"\|\s+fps\s+\|\s+(\d+)")

BARS = "▁▂▃▄▅▆▇█"


def _spark(values: list) -> str:
    """0..1 の値列を8段ブロックで表示"""
    return "".join(BARS[min(7, int(v * 8))] for v in values)


def parse_logs():
    """全nightlyログから (サイクル時刻, style, 指標) を時系列で集める"""
    cycles = []   # [{"file", "ts", "styles": {style: {...}}}]
    for f in sorted(LOG_DIR.glob("nightly_*.log")):
        m = re.match(r"nightly_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", f.name)
        label = f"{m.group(1)} {m.group(2)}:{m.group(3)}" if m else f.name
        entry = {"file": f.name, "ts": label, "styles": {}}
        style = None
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            sm = STYLE_RE.match(line)
            if sm:
                style = sm.group(1)
                entry["styles"].setdefault(
                    style, {"steps": None, "fps": None,
                            "random": None, "benchmark": None})
                continue
            if style is None:
                continue
            st = entry["styles"][style]
            xm = STEPS_RE.search(line)
            if xm:
                st["steps"] = int(xm.group(1))
                continue
            fm = FPS_RE.search(line)
            if fm:
                st["fps"] = int(fm.group(1))
                continue
            em = EVAL_RE.search(line)
            if em:
                try:
                    d = ast.literal_eval(em.group(1))
                except (ValueError, SyntaxError):
                    continue
                if d.get("opponent") in ("random", "benchmark"):
                    st[d["opponent"]] = d.get("win_rate")
        if entry["styles"]:
            cycles.append(entry)
    return cycles


def _proc_status() -> str:
    """学習プロセスの稼働状態 (launchd + プロセス実在)"""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=5).stdout
    except OSError:
        out = ""
    launchd = "?"
    for line in out.splitlines():
        if "com.championsadviser.train" in line:
            pid = line.split()[0]
            launchd = f"launchd管理 (PID {pid})" if pid != "-" else "launchd登録済み (停止中)"
    try:
        ps = subprocess.run(["pgrep", "-f", "train_nightly.sh"],
                            capture_output=True, text=True, timeout=5).stdout
        running = bool(ps.strip())
    except OSError:
        running = False
    mtime = ""
    if FOREVER_LOG.exists():
        age = time.time() - FOREVER_LOG.stat().st_mtime
        mtime = f" / ログ最終更新 {int(age // 60)}分前"
        if age > 1800:
            mtime += " ⚠ 30分以上更新なし (ハングの疑い)"
    return f"{launchd} / サイクル実行{'中' if running else 'なし'}{mtime}"


def show_summary(cycles):
    print(f"学習状態: {_proc_status()}")
    if not cycles:
        print("評価ログがまだありません")
        return
    styles = sorted({s for c in cycles for s in c["styles"]})
    print(f"完了サイクル: {len(cycles)} (最新: {cycles[-1]['ts']})\n")
    header = f"{'性格':<8} {'steps':>9} {'fps':>5} {'vsRandom':>9} {'vsBench':>8}  推移(vsBench)"
    print(header)
    print("-" * len(header))
    for style in styles:
        hist = [c["styles"][style] for c in cycles if style in c["styles"]]
        latest = hist[-1]
        # 評価は進行中サイクルではまだ無いので、最新の完了済み評価を表示する
        last_r = next((h["random"] for h in reversed(hist)
                       if h["random"] is not None), None)
        last_b = next((h["benchmark"] for h in reversed(hist)
                       if h["benchmark"] is not None), None)
        bench_hist = [h["benchmark"] for h in hist if h["benchmark"] is not None]
        spark = _spark(bench_hist[-20:]) if bench_hist else ""
        best = f" 最高{max(bench_hist):.2f}" if bench_hist else ""
        print(f"{style:<8} "
              f"{latest['steps'] or 0:>9,} "
              f"{latest['fps'] or '-':>5} "
              f"{('%.2f' % last_r) if last_r is not None else '-':>9} "
              f"{('%.2f' % last_b) if last_b is not None else '-':>8}  "
              f"{spark}{best}")
    print("\n(vsBench=上位構築ヒューリスティクス相手の勝率。目標: 0.5超の安定)")


def show_history(cycles, n: int):
    for c in cycles[-n:]:
        parts = []
        for style, st in c["styles"].items():
            r = f"{st['random']:.2f}" if st["random"] is not None else "-"
            b = f"{st['benchmark']:.2f}" if st["benchmark"] is not None else "-"
            parts.append(f"{style}: R{r}/B{b}")
        print(f"{c['ts']}  " + "  ".join(parts))


def _moving_avg(xs, ys, window: int = 5):
    """欠測 (None) を飛ばした移動平均"""
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    out_x, out_y = [], []
    for i in range(len(pts)):
        seg = pts[max(0, i - window + 1):i + 1]
        out_x.append(pts[i][0])
        out_y.append(sum(v for _, v in seg) / len(seg))
    return out_x, out_y


def plot_history(cycles, n: int, out: Path):
    """全性格の vsRandom / vsBenchmark 勝率を1枚のグラフに描画する"""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = [
        "Hiragino Sans", "Arial Unicode MS", "sans-serif"]
    import matplotlib.pyplot as plt

    cycles = cycles[-n:]
    if not cycles:
        print("プロットするデータがありません")
        return
    styles = sorted({s for c in cycles for s in c["styles"]})
    xs = list(range(len(cycles)))
    colors = {"balance": "tab:blue", "offense": "tab:red",
              "cycle": "tab:green", "stall": "tab:purple"}

    fig, ax = plt.subplots(figsize=(12, 6))
    for style in styles:
        color = colors.get(style, None)
        bench = [c["styles"].get(style, {}).get("benchmark") for c in cycles]
        rand = [c["styles"].get(style, {}).get("random") for c in cycles]
        # 生データは薄く、ベンチマークの移動平均を太線で
        bx = [x for x, v in zip(xs, bench) if v is not None]
        by = [v for v in bench if v is not None]
        ax.plot(bx, by, ".", color=color, alpha=0.25, markersize=4)
        mx, my = _moving_avg(xs, bench)
        ax.plot(mx, my, "-", color=color, linewidth=2,
                label=f"{style} vsBench(移動平均)")
        rx = [x for x, v in zip(xs, rand) if v is not None]
        ry = [v for v in rand if v is not None]
        ax.plot(rx, ry, "--", color=color, alpha=0.35, linewidth=1,
                label=f"{style} vsRandom")

    ax.axhline(0.5, color="gray", linestyle=":", linewidth=1)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("サイクル")
    ax.set_ylabel("勝率")
    ax.set_title(f"学習推移 (直近{len(cycles)}サイクル: "
                 f"{cycles[0]['ts']} 〜 {cycles[-1]['ts']})")
    # x軸に日時ラベルを間引いて表示
    step = max(1, len(cycles) // 10)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels([cycles[i]["ts"][5:] for i in xs[::step]],
                       rotation=45, ha="right", fontsize=8)
    ax.legend(fontsize=8, ncol=2, loc="lower left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print(f"グラフを保存しました: {out}")


def follow():
    print("ライブ追尾 (Ctrl+Cで終了):")
    proc = subprocess.Popen(
        ["tail", "-F", "-n", "20", str(FOREVER_LOG)],
        stdout=subprocess.PIPE, text=True)
    keep = re.compile(
        r"サイクル|学習開始|evaluate|opponent_pool|total_timesteps|fps|エラー|done:")
    try:
        for line in proc.stdout:
            if keep.search(line):
                print(line.rstrip())
    except KeyboardInterrupt:
        proc.terminate()


def main():
    ap = argparse.ArgumentParser(description="学習経過の観察")
    ap.add_argument("--history", type=int, metavar="N",
                    help="直近Nサイクルの評価履歴を一覧表示")
    ap.add_argument("--plot", nargs="?", const="", metavar="PATH",
                    help="勝率推移を1枚のグラフにPNG出力 "
                         "(--history Nと併用で直近Nサイクル。既定の保存先は "
                         "logs/training_history.png)")
    ap.add_argument("--follow", action="store_true",
                    help="train_forever.log をライブ追尾")
    args = ap.parse_args()
    if args.follow:
        follow()
        return
    cycles = parse_logs()
    if args.plot is not None:
        out = Path(args.plot) if args.plot else REPO / "logs" / "training_history.png"
        plot_history(cycles, args.history or len(cycles), out)
        return
    if args.history:
        show_history(cycles, args.history)
    else:
        show_summary(cycles)


if __name__ == "__main__":
    main()
