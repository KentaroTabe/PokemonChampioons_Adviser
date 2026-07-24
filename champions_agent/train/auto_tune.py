"""報酬設定の自律チューニングループ。

train_forever の各サイクル後に --step で呼ばれ、
  1. 直近の評価履歴 (vsベンチマーク勝率) を読む
  2. 現在の設定で MIN_CYCLES 分たまったら平均を記録
  3. 改善していなければ次の候補設定へ切替 (auto_env.sh に書き出し)
  4. 全候補を試したら最良設定に定着する
を行う。設定は環境変数 (REWARD_SHAPE_SCALE / TRAIN_ENT_COEF / TRAIN_LR)
なので学習プロセスの再起動は不要 (次の性格の学習から反映される)。

CLI:
    python -m champions_agent.train.auto_tune --step     # 1判定 (nightlyから呼ぶ)
    python -m champions_agent.train.auto_tune --status   # 現在の状態表示
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

TRAIN_DIR = Path(__file__).resolve().parent
STATE_PATH = TRAIN_DIR / "auto_tune_state.json"
ENV_PATH = TRAIN_DIR / "auto_env.sh"
LOG_PATH = TRAIN_DIR.parent.parent / "logs" / "auto_tune.log"

MIN_CYCLES = 7        # 1設定あたりの最低評価サイクル数 (≒2.3時間)
IMPROVE_EPS = 0.02    # 「改善」とみなす平均ベンチ勝率の上げ幅

# 試行する設定のラダー (順に試し、最後は最良へ定着)
LADDER = [
    {"REWARD_SHAPE_SCALE": "0.3", "TRAIN_ENT_COEF": "0.01", "TRAIN_LR": "3e-4"},
    {"REWARD_SHAPE_SCALE": "0.15", "TRAIN_ENT_COEF": "0.01", "TRAIN_LR": "3e-4"},
    {"REWARD_SHAPE_SCALE": "0.5", "TRAIN_ENT_COEF": "0.01", "TRAIN_LR": "3e-4"},
    {"REWARD_SHAPE_SCALE": "0.3", "TRAIN_ENT_COEF": "0.03", "TRAIN_LR": "3e-4"},
    {"REWARD_SHAPE_SCALE": "0.15", "TRAIN_ENT_COEF": "0.03", "TRAIN_LR": "3e-4"},
    {"REWARD_SHAPE_SCALE": "0.3", "TRAIN_ENT_COEF": "0.01", "TRAIN_LR": "1.5e-4"},
]


def _log(msg: str) -> None:
    line = f"[auto_tune] {time.strftime('%m-%d %H:%M')} {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"ladder_index": 0, "applied_at": 0.0, "trials": [], "settled": False}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _write_env(config: dict) -> None:
    lines = ["# auto_tune が管理する学習設定 (train_nightly.sh が source する)"]
    for k, v in config.items():
        lines.append(f"export {k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def recent_cycle_means(since_ts: float) -> list:
    """since_ts 以降に完了したサイクルの「平均ベンチ勝率」リスト"""
    from tools.watch_training import parse_logs, LOG_DIR
    means = []
    for c in parse_logs():
        f = LOG_DIR / c["file"]
        try:
            if f.stat().st_mtime < since_ts:
                continue
        except OSError:
            continue
        vals = [st["benchmark"] for st in c["styles"].values()
                if st.get("benchmark") is not None]
        if len(vals) >= 3:   # 4性格中3件以上評価済みのサイクルのみ
            means.append(sum(vals) / len(vals))
    return means


def decide(state: dict, means: list) -> dict:
    """判定ロジック (純粋関数: テスト可能)。stateを更新して返す"""
    if state.get("settled"):
        return state
    if len(means) < MIN_CYCLES:
        return state   # データ不足: 継続

    mean = sum(means) / len(means)
    cfg = LADDER[state["ladder_index"]]
    state["trials"].append({"config": cfg, "mean_bench": round(mean, 4),
                            "n_cycles": len(means)})
    best = max(state["trials"], key=lambda t: t["mean_bench"])
    _log(f"設定{state['ladder_index']} {cfg} の平均ベンチ={mean:.3f} "
         f"({len(means)}サイクル) / 過去最良={best['mean_bench']:.3f}")

    if state["ladder_index"] + 1 < len(LADDER):
        # 「この試行より前の最良」を明確に上回っていれば現設定を延長
        # (改善が続く限り観測を続け、頭打ちになったら次の設定へ)
        prev_best = max((t["mean_bench"] for t in state["trials"][:-1]),
                        default=None)
        if prev_best is None or mean >= prev_best + IMPROVE_EPS:
            _log("改善傾向のため現設定を延長")
            state["applied_at"] = time.time()
            return state
        state["ladder_index"] += 1
        state["applied_at"] = time.time()
        _log(f"次の設定へ切替: {LADDER[state['ladder_index']]}")
        return state

    # ラダー終了: 最良設定に定着
    state["settled"] = True
    state["ladder_index"] = LADDER.index(best["config"]) \
        if best["config"] in LADDER else state["ladder_index"]
    state["applied_at"] = time.time()
    _log(f"全設定を試行済み。最良 (平均{best['mean_bench']:.3f}) に定着: "
         f"{best['config']}")
    return state


def step() -> None:
    state = _load_state()
    if state["applied_at"] == 0.0:
        state["applied_at"] = time.time()
        _write_env(LADDER[state["ladder_index"]])
        _log(f"初期設定を適用: {LADDER[state['ladder_index']]}")
        _save_state(state)
        return
    means = recent_cycle_means(state["applied_at"])
    before_idx, before_settled = state["ladder_index"], state.get("settled")
    state = decide(state, means)
    if state["ladder_index"] != before_idx or state.get("settled") != before_settled:
        _write_env(LADDER[state["ladder_index"]])
    _save_state(state)


def status() -> None:
    state = _load_state()
    print(f"設定index: {state['ladder_index']} "
          f"{'(定着)' if state.get('settled') else ''}")
    print(f"現設定: {LADDER[state['ladder_index']]}")
    means = recent_cycle_means(state.get("applied_at", 0))
    print(f"現設定での評価: {len(means)}/{MIN_CYCLES}サイクル "
          + (f"平均{sum(means)/len(means):.3f}" if means else ""))
    for t in state.get("trials", []):
        print(f"  試行済み: {t['config']} -> 平均{t['mean_bench']:.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="報酬設定の自律チューニング")
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.step:
        step()
    else:
        status()


if __name__ == "__main__":
    main()
