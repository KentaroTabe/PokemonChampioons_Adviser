"""population-based selfplay のためのチェックポイントプール管理。

- 勝率ゲート: vs Random の勝率が閾値 (既定0.75) を超えた性格のチェックポイントを
  プール (train/checkpoints/pool/) へスナップショットする
- 学習時: プールにエントリがあれば、対戦相手を RandomPlayer から
  「過去チェックポイントの方策 (バトルごとにランダム抽選)」へ自動で切り替える
  (ε=一定確率でRandomも混ぜ、基礎的な相手への忘却を防ぐ)

CLI:
    python -m champions_agent.train.opponent_pool --list
    python -m champions_agent.train.opponent_pool --update-from-eval balance
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from pathlib import Path

from champions_agent.config import MODELS_DIR

POOL_DIR = MODELS_DIR / "pool"
STATE_PATH = POOL_DIR / "pool_state.json"
EVAL_DIR = Path(__file__).resolve().parent / "logs"

WIN_RATE_GATE = 0.75      # vs Random 勝率がこれ以上でプール入り
MAX_ENTRIES_PER_STYLE = 5  # 性格ごとの保持世代数
EPSILON_RANDOM = 0.25      # selfplay時もこの確率でランダム相手を混ぜる


class OpponentPool:
    def __init__(self):
        POOL_DIR.mkdir(parents=True, exist_ok=True)
        self.state = {"entries": []}
        if STATE_PATH.exists():
            try:
                self.state = json.loads(STATE_PATH.read_text())
            except Exception:
                pass
        # 実在しないファイルのエントリを掃除
        self.state["entries"] = [
            e for e in self.state.get("entries", [])
            if (POOL_DIR / e["file"]).exists()
        ]

    def _save(self):
        STATE_PATH.write_text(json.dumps(self.state, ensure_ascii=False, indent=1))

    def entries(self) -> list:
        return self.state.get("entries", [])

    def has_entries(self) -> bool:
        return len(self.entries()) > 0

    def add(self, style: str, checkpoint: Path, win_rate: float) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{style}_{ts}.zip"
        shutil.copy(checkpoint, POOL_DIR / fname)
        self.state["entries"].append({
            "file": fname, "style": style,
            "win_rate": win_rate, "added_at": ts,
        })
        # 性格ごとに古い世代を間引く
        same = [e for e in self.state["entries"] if e["style"] == style]
        if len(same) > MAX_ENTRIES_PER_STYLE:
            for old in sorted(same, key=lambda e: e["added_at"])[:len(same) - MAX_ENTRIES_PER_STYLE]:
                (POOL_DIR / old["file"]).unlink(missing_ok=True)
                self.state["entries"].remove(old)
        self._save()
        return POOL_DIR / fname

    def sample(self, rng: random.Random | None = None) -> Path | None:
        """プールからチェックポイントを1つ抽選する (新しい世代ほど高確率)"""
        rng = rng or random
        entries = self.entries()
        if not entries:
            return None
        ordered = sorted(entries, key=lambda e: e["added_at"])
        weights = [i + 1 for i in range(len(ordered))]  # 新しいほど重い
        chosen = rng.choices(ordered, weights=weights, k=1)[0]
        return POOL_DIR / chosen["file"]


class PoolOpponentPlayer:
    """バトルごとにプールから方策を抽選して指す対戦相手プレイヤー。

    poke-env の Player を継承する動的クラスとして生成する
    (make_pool_opponent を使うこと)。
    """


def make_pool_opponent(pool: OpponentPool, epsilon_random: float = EPSILON_RANDOM,
                       **player_kwargs):
    """PoolOpponentPlayer のインスタンスを生成する。

    epsilon_random: この確率でそのバトルはランダム行動になる。
    """
    from poke_env.player import Player
    from champions_agent.agent.policy_battle import BattlePolicy

    class _PoolOpponent(Player):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pool = pool
            self._eps = epsilon_random
            self._assign: dict = {}      # battle_tag -> BattlePolicy | None(=random)
            self._policy_cache: dict = {}

        def _policy_for(self, battle):
            tag = battle.battle_tag
            if tag not in self._assign:
                if random.random() < self._eps:
                    self._assign[tag] = None
                else:
                    path = self._pool.sample()
                    if path is None:
                        self._assign[tag] = None
                    else:
                        key = str(path)
                        if key not in self._policy_cache:
                            try:
                                self._policy_cache[key] = BattlePolicy(model_path=path)
                            except Exception:
                                self._policy_cache[key] = None
                        self._assign[tag] = self._policy_cache[key]
                # 古いバトルの割り当てを掃除
                if len(self._assign) > 50:
                    for k in list(self._assign.keys())[:-25]:
                        del self._assign[k]
            return self._assign[tag]

        def choose_move(self, battle):
            policy = self._policy_for(battle)
            if policy is not None and policy.model is not None:
                try:
                    return policy.choose_order(battle)
                except Exception:
                    pass
            return self.choose_random_move(battle)

    return _PoolOpponent(**player_kwargs)


# ------------------------------------------------------------------
def update_from_eval(style: str) -> bool:
    """evaluate.py が出力した直近の評価結果を読み、ゲートを超えていればプール入り"""
    eval_path = EVAL_DIR / f"last_eval_{style}.json"
    if not eval_path.exists():
        print(f"[opponent_pool] 評価結果がありません: {eval_path}")
        return False
    result = json.loads(eval_path.read_text())
    win_rate = float(result.get("win_rate", 0.0))
    ckpt = MODELS_DIR / f"battle_policy_{style}.zip"
    if not ckpt.exists():
        print(f"[opponent_pool] チェックポイントがありません: {ckpt}")
        return False
    if win_rate < WIN_RATE_GATE:
        print(f"[opponent_pool] [{style}] 勝率{win_rate:.2f} < ゲート{WIN_RATE_GATE} "
              "のためプール追加を見送り (相手はRandomのまま)")
        return False
    pool = OpponentPool()
    path = pool.add(style, ckpt, win_rate)
    print(f"[opponent_pool] [{style}] 勝率{win_rate:.2f} でプール入り: {path.name} "
          f"(計{len(pool.entries())}件) -> 次回学習からselfplay相手に使われます")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="selfplay相手プールの管理")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update-from-eval", type=str, default=None,
                         metavar="STYLE", help="直近評価の勝率でゲート判定しプールへ追加")
    args = parser.parse_args()

    if args.update_from_eval:
        update_from_eval(args.update_from_eval)
        return
    pool = OpponentPool()
    print(f"pool entries: {len(pool.entries())}")
    for e in pool.entries():
        print(f"  {e['file']}  style={e['style']} win_rate={e['win_rate']}")


if __name__ == "__main__":
    main()
