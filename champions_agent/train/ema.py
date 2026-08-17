"""EMA (Polyak平均) チェックポイントの管理。

    python -m champions_agent.train.ema --style balance          # 手動更新
    python -m champions_agent.train.ema --style balance --show   # 状態表示

背景 (2026-08-18, docs/AXIS_GAP_ANALYSIS.md): current の対 _best 勝率が
短時間で大きく動く疑いがある (逐次測定で 0.405 → 90分後 0.565)。
振動する学習への一般的対処として、重みの指数移動平均 (EMA) を
`battle_policy_{style}_ema.zip` として維持する。自己対戦の文脈では
過去方策の平均 (fictitious play の平均方策) の近似にあたり、
循環・振動に対して滑らかな参照点になる。

- 更新は学習1ラウンド (train_battle の train() 完了) ごとに1回
- EMA は評価・観察用のアーティファクト。学習の勾配・配布 (_best) には
  影響しない (配布への採用は日次定点で EMA の優位を確認してから別途判定)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from champions_agent.config import MODELS_DIR

# 1ラウンド=10万ステップごとの減衰率。実効的な平均窓は 1/(1-τ)=20ラウンド
# ≒ 200万ステップ ≒ 2〜3時間で、観測された振動の周期 (約1時間) より長い
EMA_TAU = 0.95


def blend_state_dicts(ema_sd: dict, cur_sd: dict, tau: float) -> dict:
    """tau*ema + (1-tau)*current。形状不一致があれば ValueError。

    浮動小数のテンソルのみ混合し、整数テンソル (カウンタ等) は current 側を
    採用する。
    """
    if set(ema_sd.keys()) != set(cur_sd.keys()):
        raise ValueError("state_dict のキーが一致しません")
    out = {}
    for k, ema_t in ema_sd.items():
        cur_t = cur_sd[k]
        if ema_t.shape != cur_t.shape:
            raise ValueError(f"形状不一致: {k} {ema_t.shape} vs {cur_t.shape}")
        if ema_t.is_floating_point():
            out[k] = tau * ema_t + (1.0 - tau) * cur_t
        else:
            out[k] = cur_t
    return out


def update_ema(style: str, tau: float = EMA_TAU,
               models_dir: Path | None = None) -> Path | None:
    """current を EMA に混ぜ込む。EMA が無ければ current のコピーで初期化。

    ネット構成や観測次元の変更で形状が合わなくなった場合は、EMA を
    current のコピーでリセットする (古い基底と混ぜても意味がないため)。
    戻り値: EMA のパス (current が無ければ None)。
    """
    base = Path(models_dir) if models_dir else MODELS_DIR
    cur_path = base / f"battle_policy_{style}.zip"
    ema_path = base / f"battle_policy_{style}_ema.zip"
    if not cur_path.exists():
        return None
    if not ema_path.exists():
        shutil.copy(cur_path, ema_path)
        print(f"[ema] [{style}] currentのコピーで初期化: {ema_path.name}")
        return ema_path

    from sb3_contrib import MaskablePPO
    cur = MaskablePPO.load(str(cur_path), device="cpu")
    try:
        ema = MaskablePPO.load(str(ema_path), device="cpu")
        blended = blend_state_dicts(ema.policy.state_dict(),
                                    cur.policy.state_dict(), tau)
        ema.policy.load_state_dict(blended)
    except (ValueError, RuntimeError, KeyError) as e:
        print(f"[ema] [{style}] 形状不一致のためリセット ({e})")
        shutil.copy(cur_path, ema_path)
        return ema_path

    tmp = ema_path.with_name(ema_path.name + ".tmp.zip")
    ema.save(str(tmp))
    tmp.replace(ema_path)
    return ema_path


def main() -> None:
    parser = argparse.ArgumentParser(description="EMAチェックポイントの管理")
    parser.add_argument("--style", type=str, required=True)
    parser.add_argument("--tau", type=float, default=EMA_TAU)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    ema_path = MODELS_DIR / f"battle_policy_{args.style}_ema.zip"
    if args.show:
        if ema_path.exists():
            import time
            mtime = time.strftime("%m-%d %H:%M",
                                  time.localtime(ema_path.stat().st_mtime))
            print(f"{ema_path.name}: 更新 {mtime}")
        else:
            print(f"{ema_path.name}: 未作成")
        return
    path = update_ema(args.style, tau=args.tau)
    print(f"[ema] 更新完了: {path}")


if __name__ == "__main__":
    main()
