"""映像解析パイプラインの処理性能を実測する (負荷条件の比較用)。

デバッグフレームを実際に VisionPipeline へ通し、1フレームあたりの処理時間を
測る。学習ループを動かした状態/止めた状態で実行して比べることで、
「接続テスト中に学習を止める必要があるか」を定量的に判断できる。

    python -m tools.bench_pipeline                 # 直近30フレーム
    python -m tools.bench_pipeline --frames 50
    python -m tools.bench_pipeline --label "学習ON"  # 出力に条件名を付ける

判断の目安: フロントエンドは約10fps (100ms間隔) でフレームを送る。
処理が追いつかない分は最新優先で捨てられる (server.py の _busy 制御) ため、
平均処理時間が100msを大きく超えるほど「間引き率」が上がり、
アドバイスの反映が遅れる。
"""
from __future__ import annotations

import argparse
import glob
import statistics
import time
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parent.parent
FRAME_DIR = REPO / "debug_frames"


def _load_frames(n: int) -> list:
    """直近n枚のデバッグフレーム (対戦中のものを優先)"""
    paths = sorted(glob.glob(str(FRAME_DIR / "frame_*.png")))[-n:]
    out = []
    for p in paths:
        img = cv2.imread(p)
        if img is not None:
            out.append((Path(p).name, img))
    return out


def _stats(times: list) -> dict:
    s = sorted(times)
    return {
        "mean_ms": statistics.mean(s) * 1000,
        "median_ms": statistics.median(s) * 1000,
        "p95_ms": (s[int(len(s) * 0.95) - 1] if len(s) >= 20 else s[-1]) * 1000,
        "max_ms": s[-1] * 1000,
    }


def run(n_frames: int, label: str, repeats: int = 1) -> dict:
    """10fps送信を模した連続再生で1フレームあたりの処理コストを測る。

    実運用と同じく single_shot=False で、フレームは毎回変わる
    (メッセージOCRのマスク変化検知・抽出の間引きが実運用どおり働く)。
    100msの予算を超えたフレームはサーバー側で後続が捨てられる。
    """
    from vision.pipeline import VisionPipeline

    frames = _load_frames(n_frames)
    if not frames:
        raise SystemExit(f"デバッグフレームがありません: {FRAME_DIR}")

    # ブラウザは JPEG(品質0.8) をbase64で送るので、サーバー側のデコード
    # コストも含めて測る (server.handle_frame と同じ経路)
    import base64
    import numpy as np
    encoded = []
    for _, img in frames:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        encoded.append(base64.b64encode(buf).decode() if ok else None)

    all_times, scenes = [], {}
    for _ in range(repeats):
        pipe = VisionPipeline()
        pipe.process(frames[0][1], single_shot=True)   # OCR初期化を除外
        for (_, raw), b64 in zip(frames, encoded):
            t0 = time.perf_counter()
            if b64 is not None:      # base64 -> JPEGデコード (サーバーと同じ)
                nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            else:
                img = raw
            state, _ = pipe.process(img)
            dt = time.perf_counter() - t0
            all_times.append(dt)
            scenes[state["scene"]] = scenes.get(state["scene"], 0) + 1
            rest = 0.1 - dt          # 10fps送信の間隔を模す
            if rest > 0:
                time.sleep(rest)

    s = _stats(all_times)
    over = sum(1 for t in all_times if t > 0.1) / len(all_times)
    res = {"label": label, "n": len(all_times), **s, "over_budget": over,
           "scenes": scenes}
    print(f"=== パイプライン性能 [{label}] {res['n']}フレーム "
          f"(10fps再生 x{repeats}周) ===")
    print(f"1フレーム処理時間: 平均{s['mean_ms']:.0f}ms / "
          f"中央{s['median_ms']:.0f}ms / p95 {s['p95_ms']:.0f}ms / "
          f"最大{s['max_ms']:.0f}ms")
    print(f"予算100ms超過フレーム: {over:.0%} "
          f"(この間に届くフレームがサーバーで捨てられる)")
    print(f"シーン内訳: {res['scenes']}")
    return res


def bench_advice(n_frames: int) -> None:
    """アドバイス計算 (engine.evaluate: ダメ計+探索+RL) のコストを測る。

    サーバーはフレーム処理に加えてこれをコマンド/選出画面で実行するため、
    実効フレームレートの支配項になり得る。
    """
    from advisor.service import Advisor
    from vision.pipeline import VisionPipeline

    frames = _load_frames(n_frames)
    pipe = VisionPipeline()
    state = None
    for _, img in frames:      # 対戦中の状態を作る (最後のcommand系を使う)
        st, _ = pipe.process(img, single_shot=True)
        if st["scene"] in ("command", "move_select", "battle_hud"):
            state = st
    if state is None:
        print("(対戦中シーンのフレームが無くアドバイス計測をスキップ)")
        return
    advisor = Advisor(resolver=pipe.resolver)
    advisor.advise(state)      # ウォームアップ (モデルロード等)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        advisor.advise(state)
        times.append(time.perf_counter() - t0)
    s = _stats(times)
    print(f"アドバイス計算: 平均{s['mean_ms']:.0f}ms / 最大{s['max_ms']:.0f}ms "
          f"(コマンド/技選択画面で実行)")


def main() -> None:
    ap = argparse.ArgumentParser(description="映像解析パイプラインの性能実測")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--label", default="現在の負荷")
    ap.add_argument("--repeats", type=int, default=3,
                    help="再生周回数 (ばらつきを均す)")
    ap.add_argument("--advice", action="store_true",
                    help="アドバイス計算のコストも測る")
    args = ap.parse_args()
    run(args.frames, args.label, args.repeats)
    if args.advice:
        bench_advice(args.frames)


if __name__ == "__main__":
    main()
