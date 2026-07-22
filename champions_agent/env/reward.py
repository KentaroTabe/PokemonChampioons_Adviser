"""
戦闘方策(policy_battle)学習用の報酬設計。

poke-env の AbstractBattle オブジェクト(before/after)からHP割合の変化・
きぜつ・勝敗を見て報酬を計算する。まずはシンプルなHP差分+勝敗ボーナスから開始し、
必要に応じてステータス変化・場の状況などを加味して拡張する。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RewardConfig:
    hp_diff_weight: float = 1.0        # 自分/相手のHP割合差分にかける重み
    faint_bonus: float = 2.0           # 相手を1体きぜつさせた時の追加報酬
    fainted_penalty: float = 2.0       # 自分の1体がきぜつした時のペナルティ
    win_bonus: float = 30.0
    lose_penalty: float = 30.0
    step_penalty: float = 0.01         # 長期化を避けるための毎ターン微小ペナルティ


DEFAULT_REWARD_CONFIG = RewardConfig()

# --- 性格(PlayStyle)別の報酬プリセット ---
# team_builder のチーム生成バイアスと対になる、戦闘方策の報酬シェイピング。
# offense: 速攻決着を促す(step_penalty増・faint_bonus増)
# stall:   長期戦を許容し、HP温存を重視する(step_penalty減・hp_diff_weight増)
# cycle:   中間的だが、相手を着実に削ることを評価
REWARD_PRESETS: dict[str, RewardConfig] = {
    "offense": RewardConfig(
        hp_diff_weight=1.0, faint_bonus=3.0, fainted_penalty=1.5,
        win_bonus=30.0, lose_penalty=30.0, step_penalty=0.03,
    ),
    "cycle": RewardConfig(
        hp_diff_weight=1.3, faint_bonus=2.0, fainted_penalty=2.0,
        win_bonus=30.0, lose_penalty=30.0, step_penalty=0.015,
    ),
    # stall: 旧設定 (hp_diff 1.5 / faint 1.5 / step 0.002) は「殴らず耐える」
    # 局所解に崩壊した (vsRandom 0.27まで劣化)。耐久寄りは保ちつつ
    # 削り (faint_bonus) と決着 (step_penalty) の圧を残す
    "stall": RewardConfig(
        hp_diff_weight=1.2, faint_bonus=2.0, fainted_penalty=2.2,
        win_bonus=30.0, lose_penalty=30.0, step_penalty=0.008,
    ),
    "balance": DEFAULT_REWARD_CONFIG,
}


def get_reward_config(play_style: str = "balance") -> RewardConfig:
    return REWARD_PRESETS.get(play_style, DEFAULT_REWARD_CONFIG)



def compute_reward(prev_state: dict, curr_state: dict, done: bool, won: bool | None,
                    config: RewardConfig = DEFAULT_REWARD_CONFIG) -> float:
    """状態辞書(自作の簡易表現)からステップ報酬を計算する。

    prev_state / curr_state は以下の形式を想定:
        {
            "my_total_hp_percent": float,   # 自パーティ残りHP割合の合計 (0-6.0程度)
            "opp_total_hp_percent": float,
            "my_fainted_count": int,
            "opp_fainted_count": int,
        }
    """
    reward = 0.0

    hp_diff_prev = prev_state["my_total_hp_percent"] - prev_state["opp_total_hp_percent"]
    hp_diff_curr = curr_state["my_total_hp_percent"] - curr_state["opp_total_hp_percent"]
    reward += config.hp_diff_weight * (hp_diff_curr - hp_diff_prev)

    new_opp_faints = curr_state["opp_fainted_count"] - prev_state["opp_fainted_count"]
    new_my_faints = curr_state["my_fainted_count"] - prev_state["my_fainted_count"]
    reward += config.faint_bonus * max(new_opp_faints, 0)
    reward -= config.fainted_penalty * max(new_my_faints, 0)

    reward -= config.step_penalty

    if done:
        if won is True:
            reward += config.win_bonus
        elif won is False:
            reward -= config.lose_penalty

    return reward
