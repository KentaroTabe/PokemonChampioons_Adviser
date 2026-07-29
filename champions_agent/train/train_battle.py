"""
戦闘方策(4技+2交代=6行動)の自己対戦学習エントリポイント。

前提: ローカルでPokemon Showdownサーバーが起動していること
    (env/showdown_env.py 冒頭のコメント参照)。

使い方:
    python -m champions_agent.train.train_battle --timesteps 10000
"""
from __future__ import annotations

import argparse
import os

from champions_agent.config import (
    MODELS_DIR, RANDOM_SEED, DEFAULT_PLAY_STYLE, PLAY_STYLES,
    TRAINING_BATTLE_FORMAT,
)
from champions_agent.env.showdown_env import make_training_env


def _make_env_fn(idx: int, battle_format: str, play_style: str,
                 opp_play_style_pool):
    """SubprocVecEnv用のenv生成関数 (spawnでpickle可能なトップレベル定義)。

    idxごとにseedをずらし、各サブプロセスが独立にShowdownへ接続する
    (poke-envのアカウント名は自動生成なので衝突しない)。
    """
    def _init():
        from stable_baselines3.common.monitor import Monitor
        env = make_training_env(battle_format=battle_format,
                                own_play_style=play_style,
                                opp_play_style_pool=opp_play_style_pool,
                                seed=RANDOM_SEED + idx * 1000)
        return Monitor(env)
    return _init


def train(total_timesteps: int = 10_000, battle_format: str = TRAINING_BATTLE_FORMAT,
          play_style: str = DEFAULT_PLAY_STYLE,
          opp_play_style_pool: list[str] | None = None,
          resume: bool = False, n_envs: int = 1) -> None:
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
    # MaskablePPO: 無効アクション (テラスタル等) をサンプリングさせない。
    # 無効アクションはサーバーのデフォルト行動に丸められて
    # クレジット割当が壊れるため、マスク学習が必須 (ROADMAP 9b)
    from sb3_contrib import MaskablePPO
    from stable_baselines3.common.monitor import Monitor

    # 並列環境: Showdown通信レイテンシが律速 (単一env≒140fps) なので、
    # 複数バトルを別プロセスで同時進行させて収集スループットを上げる。
    # 各サブプロセスがローカルShowdown(8100)に独立接続する。
    # 頭打ち対策のハイパーパラメータ (環境変数で調整可能):
    # - ent_coef: SB3既定0.0では探索が縮退しプラトーで振動する。0.01で探索維持
    # - learning_rate: 新規学習フェーズは標準の3e-4 (1e-4は旧400万step
    #   モデルの微調整用だった。観測v6の新規学習では立ち上がりを遅くする)
    # - net_arch: SB3既定の64x64は388次元観測に対して過小 (0.55で頭打ち)。
    #   256x256は正直な評価 (2026-07-26に評価凍結バグを修正) の下で
    #   +600万stepを積んでもベンチ0.56前後で収束したため、2026-07-27に
    #   512x512へ切替えた。新規512ネットはゼロからではなく bc_pretrain の
    #   行動クローンで初期化する (探索エキスパートを教師にして自己対戦の
    #   立ち上がり数日ぶんを短縮する)。
    #   256系統は checkpoints/net256_backup/ に退避 (戻す場合は
    #   TRAIN_NET_WIDTH=256 + バックアップを書き戻す)
    lr = float(os.environ.get("TRAIN_LR", "3e-4"))
    ent_coef = float(os.environ.get("TRAIN_ENT_COEF", "0.01"))
    width = int(os.environ.get("TRAIN_NET_WIDTH", "512"))
    ppo_kwargs = {"learning_rate": lr, "ent_coef": ent_coef,
                  "policy_kwargs": {"net_arch": [width, width]}}
    if n_envs > 1:
        from stable_baselines3.common.vec_env import SubprocVecEnv
        env = SubprocVecEnv(
            [_make_env_fn(i, battle_format, play_style, opp_play_style_pool)
             for i in range(n_envs)],
            start_method="spawn")
        # rollout長: 価値推定の安定化のため総サンプル4096/更新に拡大
        # (勝敗という遅延報酬の伝播には長めのロールアウトが効く)
        ppo_kwargs["n_steps"] = max(512, 4096 // n_envs)
        print(f"[train_battle] 並列環境 {n_envs} で学習 "
              f"(n_steps={ppo_kwargs['n_steps']}/env)")
    else:
        env = make_training_env(battle_format=battle_format,
                                own_play_style=play_style,
                                opp_play_style_pool=opp_play_style_pool,
                                seed=RANDOM_SEED)
        env = Monitor(env)

    save_path = MODELS_DIR / f"battle_policy_{play_style}.zip"
    model = None
    if resume and save_path.exists():
        # 夜間バッチ等での継続学習: 既存チェックポイントから再開する。
        # custom_objectsでLR/entropyを上書きしないと保存時の値を引き継いで
        # しまい、ハイパーパラメータ変更が再開時に反映されない
        try:
            model = MaskablePPO.load(
                str(save_path), env=env,
                custom_objects={"learning_rate": lr, "ent_coef": ent_coef})
            # ネット幅が希望と違う場合は新規学習へ (SB3のloadは保存時の
            # アーキテクチャを復元し、policy_kwargs指定を無視するため、
            # 観測次元が同じだと旧64x64のまま再開してしまう)
            try:
                actual = model.policy.mlp_extractor.policy_net[0].out_features
            except Exception:
                actual = None
            width = ppo_kwargs["policy_kwargs"]["net_arch"][0]
            if actual is not None and actual != width:
                raise ValueError(
                    f"net_arch mismatch: checkpoint={actual} != 希望={width}")
            # verboseは保存時の値が復元される。bc_pretrainで作った
            # チェックポイントは0で保存されており、そのままだとSB3の
            # 進捗テーブル (total_timesteps/fps) が出ず watch_training が
            # ステップ数を読めなくなる (2026-07-27の512化以降に発生)
            model.verbose = 1
            print(f"[train_battle] チェックポイントから再開: {save_path} "
                  f"(lr={lr}, ent_coef={ent_coef}, net={actual})")
        except Exception as e:
            # 観測空間の変更・アルゴリズム変更 (PPO->MaskablePPO) 等で
            # 互換性が無い場合は退避して新規学習する。
            # ロード成功後の検証 (net_arch不一致) で来た場合に備えて
            # modelを必ずNoneへ戻す (残すと旧モデルの学習を続けてしまう)
            model = None
            backup = save_path.with_suffix(".zip.incompatible")
            save_path.rename(backup)
            print(f"[train_battle] チェックポイントが非互換のため退避して"
                  f"新規学習します ({backup.name}): {e}")
    if model is None:
        model = MaskablePPO(
            "MlpPolicy",
            env,
            verbose=1,
            seed=RANDOM_SEED,
            **ppo_kwargs,
        )
    model.learn(total_timesteps=total_timesteps, reset_num_timesteps=not resume)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = MODELS_DIR / f"battle_policy_{play_style}.zip"
    model.save(str(save_path))
    print(f"[train_battle] 学習済みモデルを保存しました: {save_path}")

    env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="戦闘方策の自己対戦学習(PPO・性格別)")
    parser.add_argument("--timesteps", type=int, default=10_000)
    parser.add_argument("--format", type=str, default=TRAINING_BATTLE_FORMAT)
    parser.add_argument("--resume", action="store_true",
                         help="既存チェックポイントがあればそこから継続学習する")
    parser.add_argument("--play-style", type=str, default=DEFAULT_PLAY_STYLE,
                         choices=list(PLAY_STYLES.keys()),
                         help="学習するエージェントの性格")
    parser.add_argument("--opp-play-styles", type=str, nargs="*", default=None,
                         help="対戦相手チーム生成に使う性格候補(省略時は全性格からランダム)")
    parser.add_argument("--n-envs", type=int,
                         default=int(os.environ.get("N_ENVS", "1")),
                         help="並列環境数 (Showdown通信律速の高速化。8コアで6程度)")
    args = parser.parse_args()

    train(total_timesteps=args.timesteps, battle_format=args.format,
          play_style=args.play_style, opp_play_style_pool=args.opp_play_styles,
          resume=args.resume, n_envs=args.n_envs)



if __name__ == "__main__":
    main()
