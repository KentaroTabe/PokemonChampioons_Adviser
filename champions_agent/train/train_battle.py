"""
戦闘方策(4技+2交代=6行動)の自己対戦学習エントリポイント。

前提: ローカルでPokemon Showdownサーバーが起動していること
    (env/showdown_env.py 冒頭のコメント参照)。

使い方:
    python -m champions_agent.train.train_battle --timesteps 10000
"""
from __future__ import annotations

import argparse

from champions_agent.config import MODELS_DIR, RANDOM_SEED, DEFAULT_PLAY_STYLE, PLAY_STYLES
from champions_agent.env.showdown_env import make_training_env


def train(total_timesteps: int = 10_000, battle_format: str = "gen9ou",
          play_style: str = DEFAULT_PLAY_STYLE,
          opp_play_style_pool: list[str] | None = None) -> None:
    """戦闘方策を性格(play_style)ごとに学習する。

    play_style: このモデル自身の性格('offense'/'cycle'/'stall'/'balance')。
                自チーム生成バイアス(team_builder)と報酬シェイピング(reward)の
                両方に反映される。
    opp_play_style_pool: 対戦相手チーム生成に使う性格候補。Noneなら全性格から
                毎エピソードランダムに選ばれる(population-based selfplayの簡易版)。
                特定の戦術のみへの過学習を防ぐ狙い。
    保存先: checkpoints/battle_policy_{play_style}.zip (性格ごとに別ファイル)
    """
    # Stable-Baselines3 はインポートコストが高いため関数内で遅延importする
    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor

    env = make_training_env(battle_format=battle_format, own_play_style=play_style,
                             opp_play_style_pool=opp_play_style_pool, seed=RANDOM_SEED)
    env = Monitor(env)

    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        seed=RANDOM_SEED,
    )
    model.learn(total_timesteps=total_timesteps)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = MODELS_DIR / f"battle_policy_{play_style}.zip"
    model.save(str(save_path))
    print(f"[train_battle] 学習済みモデルを保存しました: {save_path}")

    env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="戦闘方策の自己対戦学習(PPO・性格別)")
    parser.add_argument("--timesteps", type=int, default=10_000)
    parser.add_argument("--format", type=str, default="gen9ou")
    parser.add_argument("--play-style", type=str, default=DEFAULT_PLAY_STYLE,
                         choices=list(PLAY_STYLES.keys()),
                         help="学習するエージェントの性格")
    parser.add_argument("--opp-play-styles", type=str, nargs="*", default=None,
                         help="対戦相手チーム生成に使う性格候補(省略時は全性格からランダム)")
    args = parser.parse_args()

    train(total_timesteps=args.timesteps, battle_format=args.format,
          play_style=args.play_style, opp_play_style_pool=args.opp_play_styles)



if __name__ == "__main__":
    main()
