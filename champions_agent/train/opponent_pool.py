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
EPSILON_RANDOM = 0.10      # ランダム相手を混ぜる確率 (基礎相手の忘却防止)
# 頭打ち対策: 評価指標 (vsベンチマーク) と学習分布を近づけるため、
# ヒューリスティクス強敵と上位構築チームの比率を引き上げた
# (旧: heuristic 0.25 / ranked 0.30 でベンチ勝率0.3-0.7の振動が続いた)
EPSILON_HEURISTIC = 0.25  # 上位構築ヒューリスティクス相手を混ぜる確率 (常設の強敵)
# 探索エンジン相手 (advisor/searchのダメ計+択読み)。実測強度はベンチ勝率
# 0.41で学習済み方策と同格だが、ヒューリスティクスとは読み筋・交代傾向が
# 異なるため相手多様性として混入する (2026-07-24導入。heuristic 0.35→0.25
# に減らして枠を捻出、selfplayプール枠は0.45)
EPSILON_SEARCH = 0.20
RANKED_TEAM_PROB = 0.50   # 相手チームを上位構築の実物にする確率
OWN_RANKED_TEAM_PROB = 0.50  # 自分チームを上位構築の実物にする確率
# (生成チームだけで学習すると「上位構築を操縦する」経験が積めず、
#  アドバイザーの実用途=ユーザーの実チームでの助言と分布がズレる)


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

    def add(self, style: str, checkpoint: Path, win_rate: float,
            bench_rate: float | None = None) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{style}_{ts}.zip"
        shutil.copy(checkpoint, POOL_DIR / fname)
        self.state["entries"].append({
            "file": fname, "style": style,
            "win_rate": win_rate, "bench_rate": bench_rate, "added_at": ts,
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
        """プールからチェックポイントを1つ抽選する。

        新しい世代ほど高確率、かつベンチマーク勝率が高い世代ほど高確率
        (頭打ち対策: 強い過去世代を優先して当てることでselfplayに
        上達圧力をかける。bench_rate未記録の旧エントリは0.4扱い)
        """
        rng = rng or random
        entries = self.entries()
        if not entries:
            return None
        ordered = sorted(entries, key=lambda e: e["added_at"])
        weights = [
            (i + 1) * (0.3 + (e.get("bench_rate") if e.get("bench_rate")
                              is not None else 0.4))
            for i, e in enumerate(ordered)
        ]
        chosen = rng.choices(ordered, weights=weights, k=1)[0]
        return POOL_DIR / chosen["file"]


class PoolOpponentPlayer:
    """バトルごとにプールから方策を抽選して指す対戦相手プレイヤー。

    poke-env の Player を継承する動的クラスとして生成する
    (make_pool_opponent を使うこと)。
    """


def make_pool_opponent(pool: OpponentPool, epsilon_random: float = EPSILON_RANDOM,
                       epsilon_heuristic: float = EPSILON_HEURISTIC,
                       epsilon_search: float = EPSILON_SEARCH,
                       **player_kwargs):
    """混合対戦相手を生成する。バトルごとに以下から抽選:

    - "heuristic": SimpleHeuristicsPlayer (ダメージ計算+交代判断の固定強敵)
    - "search":    探索エンジン (advisor/search、深さ1の択読み)
    - "random":    ランダム行動 (基礎相手の忘却防止)
    - それ以外:    selfplayプールの過去チェックポイント方策
    プールが空の場合は heuristic / search / random で構成される (カリキュラム初期)。
    """
    from poke_env.player import SimpleHeuristicsPlayer
    from champions_agent.agent.policy_battle import BattlePolicy

    class _PoolOpponent(SimpleHeuristicsPlayer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pool = pool
            self._assign: dict = {}      # battle_tag -> "random"|"heuristic"|BattlePolicy
            self._policy_cache: dict = {}

        def _policy_for(self, battle):
            tag = battle.battle_tag
            if tag not in self._assign:
                r = random.random()
                if r < epsilon_heuristic:
                    self._assign[tag] = "heuristic"
                elif r < epsilon_heuristic + epsilon_search:
                    self._assign[tag] = "search"
                elif r < epsilon_heuristic + epsilon_search + epsilon_random \
                        or not self._pool.has_entries():
                    self._assign[tag] = "random"
                else:
                    path = self._pool.sample()
                    policy = None
                    if path is not None:
                        key = str(path)
                        if key not in self._policy_cache:
                            try:
                                self._policy_cache[key] = BattlePolicy(model_path=path)
                            except Exception:
                                self._policy_cache[key] = None
                        policy = self._policy_cache[key]
                    self._assign[tag] = policy if (
                        policy is not None and policy.model is not None) else "heuristic"
                if len(self._assign) > 50:
                    for k in list(self._assign.keys())[:-25]:
                        del self._assign[k]
            return self._assign[tag]

        def choose_move(self, battle):
            policy = self._policy_for(battle)
            if policy == "heuristic":
                return super().choose_move(battle)
            if policy == "search":
                try:
                    from champions_agent.env.search_expert import decide
                    d = decide(battle, depth=1)
                    if d is not None:
                        if d["kind"] == "move":
                            return self.create_order(d["move"], mega=d["mega"])
                        return self.create_order(d["pokemon"])
                except Exception:
                    pass
                return super().choose_move(battle)   # 失敗時はヒューリスティクス
            if policy == "random":
                return self.choose_random_move(battle)
            try:
                return policy.choose_order(battle)
            except Exception:
                return self.choose_random_move(battle)

    opp = _PoolOpponent(**player_kwargs)
    # 選出も相性ベースに揃える (学習環境側と対称にする)
    from champions_agent.env.showdown_env import apply_matchup_teampreview
    apply_matchup_teampreview(opp)
    return opp


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
    # ベンチマーク勝率が記録されていれば抽選の重み付けに使う
    bench_rate = None
    bench_path = EVAL_DIR / f"last_eval_{style}_benchmark.json"
    if bench_path.exists():
        try:
            bench_rate = float(json.loads(
                bench_path.read_text()).get("win_rate"))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    pool = OpponentPool()
    path = pool.add(style, ckpt, win_rate, bench_rate=bench_rate)
    br = f" ベンチ{bench_rate:.2f}" if bench_rate is not None else ""
    print(f"[opponent_pool] [{style}] 勝率{win_rate:.2f}{br} でプール入り: {path.name} "
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
