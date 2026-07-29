"""選出モデルの学習 (実対戦データからの勝率回帰)。

tools/collect_selection_data.py が集めた実対戦データ
(選出 → 戦闘 → 勝敗) から、「この3体をこの順で出したときの勝率」を
予測するモデルを学習する。推論時は120通りを総当たりして最良を選ぶ。

    python -m champions_agent.train.train_selection
    python -m champions_agent.train.train_selection --epochs 300 --holdout 0.2

前提: python -m tools.collect_selection_data --battles 2000 でデータを収集済み。
学習後は tools/check_selection --strategies matchup,model で実戦比較する。

⚠ 実測で分かっている前提 (2026-07-29, my_team実戦は各200戦):

  | 構築プール | 未知チーム検証 | my_team実戦 | 相性 |
  |---|---|---|---|
  | 60種 (多チームのみ)        | +1.4% | 0.34 | 0.34 |
  | 60種 + my_team 5000件      | +4.7% | 0.52 | 0.30 |
  | 272種 + my_team 5000件     | +7.3% | 0.44 | 0.27 |
  | 272種 + my_team微調整      | +7.3% | 0.51 | 0.24 |

  - 機能埋め込みがあっても「見たことのないチームの選出」までは汎化しない。
    多チームデータは土台にしかならず、**実際に使うチームのデータ収集が必須**。
  - 構築プールを広げると汎化は上がるが、その分 my_team への密着が落ちる
    (0.52→0.44)。全体で学習したあと my_team で微調整して寄せ直す。
  - my_team を変えたら scripts/collect_selection.sh 2 2500 myteam を回して
    学習し直すこと (微調整のデータが無いと自動で見送られる)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from champions_agent.agent.selection_model import (
    GENERAL_MODEL_PATH, META_PATH, MODEL_PATH, build_features, make_net,
)
from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
from champions_agent.config import MODELS_DIR, RANDOM_SEED

DATA_PATH = Path(__file__).resolve().parent / "logs" / "selection_data.npz"


def load_dataset(path: Path = DATA_PATH):
    """収集データ -> (特徴量, 勝敗, メタ情報)"""
    if not path.exists():
        raise SystemExit(
            f"収集データがありません: {path}\n"
            "先に python -m tools.collect_selection_data --battles 2000 を実行")
    d = np.load(path)
    if "team" not in d.files:
        raise SystemExit("古い形式のデータです。収集し直してください")
    has_opp = "opp_team" in d.files
    if not has_opp:
        print("  ⚠ 相手チームが記録されていない古いデータです "
              "(相手を見ない学習になります)。収集し直しを推奨")
    feats, rewards = [], []
    for i in range(len(d["action"])):
        team = [str(s) for s in d["team"][i]]
        # 相手6体は選出画面で見えている。使わないと「相手に応じた選出」を
        # 学習できず、平均的に強い3体しか選べなくなる
        opp = ([str(s) for s in d["opp_team"][i] if str(s)]
               if has_opp else [])
        perm = SELECTION_PERMUTATIONS[int(d["action"][i])]
        feats.append(build_features(team, opp, perm))
        rewards.append(float(d["reward"][i]))
    team_key = ["|".join(sorted(str(s) for s in t)) for t in d["team"]]
    return (np.stack(feats), np.array(rewards, dtype=np.float32),
            {"n": len(rewards), "teams": len(set(team_key)),
             "has_opp": has_opp, "team_key": team_key})


def _norm_species(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _myteam_species() -> set:
    """config/my_team.json の現在の6体を showdown ID の集合で返す"""
    from tools.evaluate_team import current_team_entries
    from vision.normalize import NameResolver
    resolver = NameResolver()
    out = set()
    for ja in current_team_entries():
        r = resolver.resolve_species(ja, cutoff=0.85)
        if r:
            out.add(_norm_species(r[1]))
    return out


def _finetune_on_myteam(net, X, y, meta, lr: float, epochs: int = 200) -> None:
    """全体で学習したあと、実際に使うチームのデータだけで微調整する。

    構築プールを60→272に広げると未知チームへの汎化は上がるが、その分
    my_team への密着が落ちる (実測: 未知チーム検証 +4.7%→+7.3% の一方で
    my_team実戦 0.52→0.44)。汎化を土台にしてから使うチームに寄せ直す。
    """
    import torch
    from torch.optim import Adam
    try:
        mine = _myteam_species()
    except Exception as e:
        print(f"  微調整をとばします (my_teamを読めない: {e})")
        return
    idx = [i for i, k in enumerate(meta["team_key"])
           if {_norm_species(s) for s in k.split("|")} == mine]
    if len(idx) < 500:
        print(f"  微調整をとばします (my_teamのデータが{len(idx)}件と少ない。"
              "bash scripts/collect_selection.sh 2 2500 myteam で収集する)")
        return

    rng = np.random.default_rng(RANDOM_SEED)
    idx = np.array(idx)
    rng.shuffle(idx)
    n_val = max(1, len(idx) // 5)
    va, tr = idx[:n_val], idx[n_val:]
    Xtr, ytr = torch.from_numpy(X[tr]), torch.from_numpy(y[tr]).unsqueeze(1)
    Xva, yva = torch.from_numpy(X[va]), torch.from_numpy(y[va]).unsqueeze(1)

    opt = Adam(net.parameters(), lr=lr, weight_decay=1e-3)
    loss_fn = torch.nn.MSELoss()
    net.eval()
    with torch.no_grad():
        before = float(loss_fn(net(Xva), yva))
    best, best_state = before, {k: v.clone() for k, v in net.state_dict().items()}
    for _ in range(epochs):
        net.train()
        perm_idx = torch.randperm(len(Xtr))
        for s in range(0, len(Xtr), 512):
            sel = perm_idx[s:s + 512]
            opt.zero_grad()
            loss_fn(net(Xtr[sel]), ytr[sel]).backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            v = float(loss_fn(net(Xva), yva))
        if v < best:
            best = v
            best_state = {k: t.clone() for k, t in net.state_dict().items()}
    net.load_state_dict(best_state)
    print(f"  my_teamで微調整: {len(tr)}件 / 保持{len(va)}件 "
          f"MSE {before:.4f} → {best:.4f}")


def train(epochs: int = 300, lr: float = 1e-3, holdout: float = 0.2,
          finetune: bool = True) -> None:
    import torch
    from torch.optim import Adam

    torch.manual_seed(RANDOM_SEED)
    rng = np.random.default_rng(RANDOM_SEED)

    X, y, meta = load_dataset()
    print(f"[train_selection] データ {meta['n']}件 / チーム{meta['teams']}種 / "
          f"特徴{X.shape[1]}次元 / 全体勝率{y.mean():.3f}")
    if meta["teams"] == 1:
        print("  ※ 単一チームのデータのため、学習結果はそのチーム専用")

    # 検証は「学習に出てこないチーム」で行う (同じチームで割ると
    # 丸暗記でも高得点になり、未知チームへの汎化を測れない)
    if meta["teams"] > 5 and meta.get("team_key") is not None:
        keys = meta["team_key"]
        uniq = sorted(set(keys))
        rng.shuffle(uniq)
        held = set(uniq[:max(1, int(len(uniq) * holdout))])
        val_idx = np.array([i for i, k in enumerate(keys) if k in held])
        tr_idx = np.array([i for i, k in enumerate(keys) if k not in held])
        print(f"  検証は未知チーム {len(held)}種 ({len(val_idx)}件) で実施")
    else:
        idx = rng.permutation(len(y))
        n_val = max(1, int(len(y) * holdout))
        val_idx, tr_idx = idx[:n_val], idx[n_val:]
    Xtr = torch.from_numpy(X[tr_idx])
    ytr = torch.from_numpy(y[tr_idx]).unsqueeze(1)
    Xva = torch.from_numpy(X[val_idx])
    yva = torch.from_numpy(y[val_idx]).unsqueeze(1)

    net = make_net()
    # weight_decay: 未知チーム検証で train 0.09 / val 0.30 と強い過学習が
    # 出たため正則化する (選出データは1戦=1サンプルで枚数が稼ぎにくい)
    opt = Adam(net.parameters(), lr=lr, weight_decay=1e-3)
    loss_fn = torch.nn.MSELoss()
    best_val, best_state = float("inf"), None
    batch = 512
    for ep in range(1, epochs + 1):
        net.train()
        # ミニバッチ (数千件規模では全バッチ勾配だと収束が遅い)
        perm_idx = torch.randperm(len(Xtr))
        loss = None
        for s in range(0, len(Xtr), batch):
            sel = perm_idx[s:s + batch]
            opt.zero_grad()
            loss = loss_fn(net(Xtr[sel]), ytr[sel])
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            vloss = float(loss_fn(net(Xva), yva))
        if vloss < best_val:
            best_val, best_state = vloss, {k: v.clone()
                                           for k, v in net.state_dict().items()}
        if ep % max(1, epochs // 6) == 0:
            print(f"  epoch {ep}/{epochs} train={float(loss.detach()):.4f} "
                  f"val={vloss:.4f}", flush=True)

    if best_state is not None:
        net.load_state_dict(best_state)
    # ベースライン: 常に全体平均を予測した場合のMSE (これを下回れば学習できている)
    base = float(((yva - ytr.mean()) ** 2).mean())
    print(f"[train_selection] 最良val MSE={best_val:.4f} "
          f"(平均予測のみ={base:.4f})")
    gain = (base - best_val) / base * 100 if base else 0.0
    print(f"  平均予測からの改善: {gain:+.1f}%")
    if best_val >= base * 0.98:
        print("  ⚠ 平均予測をほとんど超えられていません。"
              "この状態のモデルは配布に値しません (データ量/特徴量を見直す)")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    # 微調整前の汎用モデルを別に残す。my_team に寄せた配布版では
    # 「毎戦チームが変わる」ベンチマークの測定に使えない
    torch.save(net.state_dict(), GENERAL_MODEL_PATH)
    print(f"[train_selection] 汎用モデル保存: {GENERAL_MODEL_PATH}")

    if finetune:
        _finetune_on_myteam(net, X, y, meta, lr / 10)

    torch.save(net.state_dict(), MODEL_PATH)
    # 学習に使ったチームを記録する。未学習のチームでは予測が外挿になるため、
    # アドバイザー側で「参考値」と断って表示するのに使う
    import json
    d = np.load(DATA_PATH)
    teams = sorted({tuple(sorted(str(s).lower() for s in t))
                    for t in d["team"]})
    META_PATH.write_text(json.dumps(
        {"teams": [list(t) for t in teams], "n_samples": int(meta["n"])},
        ensure_ascii=False), encoding="utf-8")
    print(f"[train_selection] 保存: {MODEL_PATH}")

    _report_top(net, X, y)


def _report_top(net, X, y) -> None:
    """学習済みモデルが推す選出を、実データの勝率と並べて表示する"""
    import torch
    from advisor.infer import species_ja_name
    d = np.load(DATA_PATH)
    team = [str(s) for s in d["team"][0]]
    feats = np.stack([build_features(team, [], p)
                      for p in SELECTION_PERMUTATIONS])
    with torch.no_grad():
        pred = net(torch.from_numpy(feats)).squeeze(-1).numpy()
    order = np.argsort(-pred)
    print("\n■ モデルが推す選出 (上位5, ★=先発)")
    for rank in order[:5]:
        perm = SELECTION_PERMUTATIONS[rank]
        mask = d["action"] == rank
        actual = f"実測{d['reward'][mask].mean():.2f}({mask.sum()}回)" \
            if mask.sum() >= 3 else "実測データ僅少"
        names = " → ".join(species_ja_name(team[i]) or team[i] for i in perm)
        print(f"  予測{pred[rank]:.2f} / {actual}  ★{names}")


def main() -> None:
    ap = argparse.ArgumentParser(description="選出モデルの学習")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--no-finetune", action="store_true",
                    help="my_teamでの微調整を行わない (汎用モデルを見たいとき)")
    args = ap.parse_args()
    train(args.epochs, args.lr, args.holdout, finetune=not args.no_finetune)


if __name__ == "__main__":
    main()
