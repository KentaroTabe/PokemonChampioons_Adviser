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
MIN_BATTLES = 30   # 信頼できる勝率とみなす最小対戦数


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
    prev = state.get(style, {}).get("win_rate", -1.0)
    if win_rate <= prev:
        print(f"[best_checkpoint] [{style}] ベンチ勝率{win_rate:.2f} <= "
              f"過去最良{prev:.2f} のため据え置き")
        return False
    shutil.copy(ckpt, best_path(style))
    state[style] = {"win_rate": win_rate, "n_battles": n,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"[best_checkpoint] [{style}] 最良更新: ベンチ勝率 {prev:.2f} -> "
          f"{win_rate:.2f} ({best_path(style).name})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="最良チェックポイントの保持")
    parser.add_argument("--update-from-eval", type=str, default=None,
                        metavar="STYLE")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.update_from_eval:
        update_from_eval(args.update_from_eval)
        return
    state = _load_state()
    if not state:
        print("最良記録なし")
    for style, rec in sorted(state.items()):
        exists = "✓" if best_path(style).exists() else "✗(ファイル無し)"
        print(f"{style:<8} ベンチ勝率 {rec['win_rate']:.2f} "
              f"({rec.get('n_battles', '?')}戦, {rec.get('updated_at', '?')}) {exists}")


if __name__ == "__main__":
    main()
