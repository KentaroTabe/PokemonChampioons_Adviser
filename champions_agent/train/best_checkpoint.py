"""ベンチマーク勝率による最良チェックポイントの保持。

学習は振動するため、最新チェックポイントが過去最良とは限らない
(実測: vsベンチマーク勝率が 0.3〜0.87 で振動し、最高値の世代が
次サイクルの劣化版で上書きされていた)。ベンチマーク評価のたびに
過去最良と比較し、更新していれば battle_policy_{style}_best.zip へ
スナップショットする。アドバイザーは _best を優先ロードする。

CLI:
    python -m champions_agent.train.best_checkpoint --update-from-eval balance
    python -m champions_agent.train.best_checkpoint --list
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from champions_agent.config import MODELS_DIR

EVAL_DIR = Path(__file__).resolve().parent / "logs"
STATE_PATH = MODELS_DIR / "best_state.json"
MIN_BATTLES = 30   # 1回の評価として最低限必要な対戦数

# 1回50戦の評価は標準誤差0.071あり、単発の勝率で最良を選ぶと
# 「たまたま勝った世代」が居座る。実測 (2026-07-29, 各400戦):
#   記録値 balance 0.76 / offense 0.76 / cycle 0.74
#   実測   balance 0.63 / offense 0.60 / cycle 0.61
# 記録は約0.14上振れしており、これは評価回数ぶんの最大値バイアスと一致する。
# その結果 _best は到達不能な記録に守られて更新されなくなり、balance では
# 512化(2026-07-27)より前の256幅スナップショットが配布され続けていた。
# 対策: 直近WINDOW回の平均で推定し、過去最良を標準誤差MARGIN_SE倍ぶん
# 明確に上回ったときだけ更新する。
WINDOW = 5          # まとめて1つの推定にする評価回数 (5x50戦 = SE 0.031)
MARGIN_SE = 2.0     # 上回ったと認めるのに必要な標準誤差の倍数


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def best_path(style: str) -> Path:
    return MODELS_DIR / f"battle_policy_{style}_best.zip"


def update_from_eval(style: str) -> bool:
    """直近のベンチマーク評価を読み、過去最良を上回っていれば保存する"""
    eval_path = EVAL_DIR / f"last_eval_{style}_benchmark.json"
    if not eval_path.exists():
        print(f"[best_checkpoint] ベンチマーク評価がありません: {eval_path}")
        return False
    result = json.loads(eval_path.read_text())
    win_rate = float(result.get("win_rate", 0.0))
    n = int(result.get("n_battles", 0))
    if n < MIN_BATTLES:
        print(f"[best_checkpoint] [{style}] 対戦数{n} < {MIN_BATTLES} のため見送り")
        return False
    ckpt = MODELS_DIR / f"battle_policy_{style}.zip"
    if not ckpt.exists():
        print(f"[best_checkpoint] チェックポイントがありません: {ckpt}")
        return False

    state = _load_state()
    rec = state.setdefault(style, {})

    # 直近WINDOW回を1つの推定にまとめる (単発50戦はSE 0.071で判定に使えない)
    recent = (rec.get("recent") or []) + [[win_rate, n]]
    recent = recent[-WINDOW:]
    rec["recent"] = recent
    total_n = sum(int(x[1]) for x in recent)
    cur = sum(float(x[0]) * int(x[1]) for x in recent) / total_n

    if len(recent) < WINDOW:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print(f"[best_checkpoint] [{style}] 評価{len(recent)}/{WINDOW}回目 "
              f"(直近平均{cur:.3f})。窓が埋まるまで判定を保留")
        return False

    prev = rec.get("win_rate")
    if prev is None:
        prev, prev_n = -1.0, total_n
    else:
        prev_n = int(rec.get("n_battles") or total_n)
    se = ((cur * (1 - cur) / total_n)
          + (max(prev, 0.0) * (1 - max(prev, 0.0)) / max(prev_n, 1))) ** 0.5
    need = prev + MARGIN_SE * se
    if cur <= need:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print(f"[best_checkpoint] [{style}] 直近平均{cur:.3f} ({total_n}戦) "
              f"<= 過去最良{prev:.3f} + {MARGIN_SE:.0f}SE({se:.3f}) "
              f"= {need:.3f} のため据え置き")
        return False

    shutil.copy(ckpt, best_path(style))
    rec.update({"win_rate": cur, "n_battles": total_n,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "recent": []})   # 更新後は窓を空にして次の推定を独立させる
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"[best_checkpoint] [{style}] 最良更新: {prev:.3f} -> {cur:.3f} "
          f"({total_n}戦, {best_path(style).name})")
    return True


def reset(styles: list) -> None:
    """記録をリセットする (上振れした過去記録で更新が止まったときに使う)。

    _best のファイル自体は消さない。次に窓が埋まった時点で、その時の
    チェックポイントが正直な推定値とともに最良として記録し直される。
    """
    state = _load_state()
    for style in styles:
        if style in state:
            old = state[style].get("win_rate")
            state[style] = {"recent": []}
            print(f"[best_checkpoint] [{style}] 記録をリセット "
                  f"(旧記録 {old if old is None else f'{old:.2f}'})")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description="最良チェックポイントの保持")
    parser.add_argument("--update-from-eval", type=str, default=None,
                        metavar="STYLE")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--reset", type=str, default=None, metavar="STYLES",
                        help="記録をリセット (カンマ区切り / all)")
    args = parser.parse_args()
    if args.update_from_eval:
        update_from_eval(args.update_from_eval)
        return
    if args.reset:
        styles = (list(_load_state().keys()) if args.reset == "all"
                  else [s for s in args.reset.split(",") if s])
        reset(styles)
        return
    state = _load_state()
    if not state:
        print("最良記録なし")
    for style, rec in sorted(state.items()):
        exists = "✓" if best_path(style).exists() else "✗(ファイル無し)"
        wr = rec.get("win_rate")
        shown = "未記録" if wr is None else f"{wr:.3f}"
        pend = len(rec.get("recent") or [])
        print(f"{style:<8} ベンチ勝率 {shown} "
              f"({rec.get('n_battles', '?')}戦, {rec.get('updated_at', '?')}) "
              f"{exists}  判定待ち{pend}/{WINDOW}")


if __name__ == "__main__":
    main()
