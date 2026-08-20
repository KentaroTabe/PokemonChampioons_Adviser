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


def load_dataset(path: Path = DATA_PATH, opp_col: str = "team",
                 builder=build_features):
    """収集データ -> (特徴量, 勝敗, メタ情報)。

    opp_col="team": 相手6体 (選出画面で見える情報) に条件付ける (既定)。
    opp_col="sel" : 相手が実際に選出した3体 (opp_sel) に条件付ける。
        選出の読み合いを解く利得行列 (payoff_matrix) 用のモデルは
        こちらで学習する。3体判明している行だけを使う。
    builder: 特徴量関数 (v2比較では build_features_v2 を渡す)
    """
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
    if opp_col == "sel" and "opp_sel" not in d.files:
        raise SystemExit("opp_sel が記録されていないデータです。"
                         "収集し直してください (2026-07-31以降の収集で記録)")
    feats, rewards, rows = [], [], []
    for i in range(len(d["action"])):
        team = [str(s) for s in d["team"][i]]
        if opp_col == "sel":
            opp = [str(s) for s in d["opp_sel"][i] if str(s)]
            if len(opp) < 3:
                continue   # 相手の3体が判明しきらなかった対戦は使わない
        else:
            # 相手6体は選出画面で見えている。使わないと「相手に応じた選出」を
            # 学習できず、平均的に強い3体しか選べなくなる
            opp = ([str(s) for s in d["opp_team"][i] if str(s)]
                   if has_opp else [])
        perm = SELECTION_PERMUTATIONS[int(d["action"][i])]
        feats.append(builder(team, opp, perm))
        rewards.append(float(d["reward"][i]))
        rows.append(i)
    if not feats:
        raise SystemExit(f"使える行がありません (opp_col={opp_col})")
    rows = np.array(rows, dtype=np.int64)
    team_key = ["|".join(sorted(str(s) for s in d["team"][i]))
                for i in rows]
    # group: 対応のある収集 (--paired) で同一条件だった対戦の識別子。-1は対応なし
    group = (d["group"][rows].astype(np.int64) if "group" in d.files
             else np.full(len(rewards), -1, dtype=np.int64))
    return (np.stack(feats), np.array(rewards, dtype=np.float32),
            {"n": len(rewards), "teams": len(set(team_key)),
             "has_opp": has_opp, "team_key": team_key, "group": group})


def build_pairs(group: np.ndarray, y: np.ndarray, idx: np.ndarray) -> tuple:
    """同一グループ内で勝敗が割れた組を (勝った側, 負けた側) で返す。

    必要なのは120通りの順位だけで絶対勝率ではないので、同条件で
    「どちらが勝ったか」を直接学習するほうがサンプル効率が良い。
    相手が同じなので、差が相手の引き運に汚されていない。
    """
    from collections import defaultdict
    pos = {v: k for k, v in enumerate(idx)}
    buckets = defaultdict(list)
    for i in idx:
        g = int(group[i])
        if g >= 0:
            buckets[g].append(i)
    win, lose = [], []
    for members in buckets.values():
        w = [i for i in members if y[i] > 0.5]
        l = [i for i in members if y[i] <= 0.5]
        for a in w:
            for b in l:
                win.append(pos[a])
                lose.append(pos[b])
    return np.array(win, dtype=np.int64), np.array(lose, dtype=np.int64)


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
    mse = torch.nn.MSELoss()

    def loss_fn(pred, target):
        # net はロジットを返すので勝率に直してから二乗誤差を取る
        return mse(torch.sigmoid(pred), target)

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
          finetune: bool = True, pair_weight: float = 0.5,
          force: bool = False, cond_sel: bool = False,
          features: str = "v1") -> None:
    import torch
    from torch.optim import Adam

    torch.manual_seed(RANDOM_SEED)
    rng = np.random.default_rng(RANDOM_SEED)

    if features == "v2":
        from champions_agent.agent.selection_features_v2 import (
            build_features_v2,
        )
        builder = build_features_v2
    else:
        builder = build_features
    X, y, meta = load_dataset(opp_col="sel" if cond_sel else "team",
                              builder=builder)
    tag = " (相手の実選出3体に条件付け)" if cond_sel else ""
    if features == "v2":
        tag += " [v2特徴量: 型情報+メタ事前分布]"
    print(f"[train_selection] データ {meta['n']}件{tag} / "
          f"チーム{meta['teams']}種 / "
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
        split_kind = "unseen_teams"   # 未知チーム分割 = 構成汎化の実測
    else:
        idx = rng.permutation(len(y))
        n_val = max(1, int(len(y) * holdout))
        val_idx, tr_idx = idx[:n_val], idx[n_val:]
        split_kind = "random"
    Xtr = torch.from_numpy(X[tr_idx])
    ytr = torch.from_numpy(y[tr_idx]).unsqueeze(1)
    Xva = torch.from_numpy(X[val_idx])
    yva = torch.from_numpy(y[val_idx]).unsqueeze(1)

    if features == "v2":
        from champions_agent.agent.selection_features_v2 import make_net_v2
        net = make_net_v2()
    else:
        net = make_net()
    # weight_decay: 未知チーム検証で train 0.09 / val 0.30 と強い過学習が
    # 出たため正則化する (選出データは1戦=1サンプルで枚数が稼ぎにくい)
    opt = Adam(net.parameters(), lr=lr, weight_decay=1e-3)
    mse = torch.nn.MSELoss()

    def loss_fn(pred, target):
        return mse(torch.sigmoid(pred), target)

    # 対応のある収集 (--paired) があれば、同一条件での勝ち負けの組を作る。
    # 必要なのは順位だけなので、絶対勝率の回帰より情報が濃い
    win_i, lose_i = build_pairs(meta["group"], y, tr_idx)
    if len(win_i):
        Wtr = torch.from_numpy(X[tr_idx][win_i])
        Ltr = torch.from_numpy(X[tr_idx][lose_i])
        print(f"  対応のある比較 {len(win_i)}組を併用 "
              f"(同じ相手に対する選出の勝ち負け)")
    else:
        Wtr = Ltr = None
        print("  対応のある比較なし (--paired で収集すると使える)")

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
            if Wtr is not None:
                # ペアワイズ: 勝った選出のスコアが負けた選出を上回るように
                p = torch.randint(0, len(Wtr), (min(batch, len(Wtr)),))
                diff = (net(Wtr[p]) - net(Ltr[p])).squeeze(-1)
                loss = loss + pair_weight * torch.nn.functional.softplus(
                    -diff).mean()
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
        if not force:
            print("  → 保存を中止します。既存モデルは維持されます "
                  "(--force で上書き可)")
            return

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    # 学習に使った埋め込みをモデルと一緒にピン止めする。推論はピン側を
    # 読むため、以後の埋め込み再構築 (使用率DBの日次更新) で座標が
    # 変わってもモデルは壊れない
    try:
        import shutil
        from champions_agent.agent.selection_model import EMB_PIN_PATH
        from tools.species_embedding import CACHE as EMB_LIVE_PATH
        shutil.copy(EMB_LIVE_PATH, EMB_PIN_PATH)
        print(f"[train_selection] 埋め込みをピン止め: {EMB_PIN_PATH}")
    except Exception as e:
        print(f"  ⚠ 埋め込みのピン止めに失敗: {e}")
    if features == "v2":
        # v2は比較実験中の候補。v1の配布/汎用/微調整には触れない
        from champions_agent.agent.selection_features_v2 import (
            V2_GENERAL_MODEL_PATH,
        )
        torch.save(net.state_dict(), V2_GENERAL_MODEL_PATH)
        print(f"[train_selection] v2汎用モデル保存: {V2_GENERAL_MODEL_PATH}")
        return
    if cond_sel:
        # 条件付きモデルは利得行列 (payoff_matrix) 専用の別ファイル。
        # 配布版/汎用モデル (相手6体に条件付け) とは入力の意味が違うため
        # 上書きしてはいけない
        from champions_agent.agent.selection_model import COND_MODEL_PATH
        torch.save(net.state_dict(), COND_MODEL_PATH)
        print(f"[train_selection] 条件付きモデル保存: {COND_MODEL_PATH}")
        _report_top(net, X, y)
        return
    # 微調整前の汎用モデルを別に残す。my_team に寄せた配布版では
    # 「毎戦チームが変わる」ベンチマークの測定に使えない
    torch.save(net.state_dict(), GENERAL_MODEL_PATH)
    print(f"[train_selection] 汎用モデル保存: {GENERAL_MODEL_PATH}")

    if finetune:
        _finetune_on_myteam(net, X, y, meta, lr / 10)

    torch.save(net.state_dict(), MODEL_PATH)
    # 学習に使ったチームを記録する。未学習のチームでは予測が外挿になるため、
    # アドバイザー側で「参考値」と断って表示するのに使う。
    # val は未知チーム検証の実測 (構築提案の運用ゲート tools/team_proposal が
    # 「モデルの構成汎化が測定済みか」を機械判定するのに使う)
    import json
    import time as _time
    d = np.load(DATA_PATH)
    teams = sorted({tuple(sorted(str(s).lower() for s in t))
                    for t in d["team"]})
    META_PATH.write_text(json.dumps(
        {"teams": [list(t) for t in teams], "n_samples": int(meta["n"]),
         "val": {"mse": round(best_val, 5), "baseline_mse": round(base, 5),
                 "gain_pct": round(gain, 2), "split": split_kind,
                 "at": _time.strftime("%Y-%m-%d %H:%M")}},
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
        pred = torch.sigmoid(net(torch.from_numpy(feats))).squeeze(-1).numpy()
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
    ap.add_argument("--pair-weight", type=float, default=0.5,
                    help="ペアワイズ損失の重み (0で無効)")
    ap.add_argument("--force", action="store_true",
                    help="平均予測を超えられなくても保存する")
    ap.add_argument("--cond-sel", action="store_true",
                    help="相手の実選出3体に条件付けたモデルを学習する "
                         "(利得行列/読み合い用。opp_sel入りのデータが必要)")
    ap.add_argument("--features", default="v1", choices=["v1", "v2"],
                    help="v2=型情報+メタ事前分布を加えた特徴量 (比較実験用。"
                         "保存先も selection_model_v2_general.pt に分離)")
    args = ap.parse_args()
    train(args.epochs, args.lr, args.holdout, finetune=not args.no_finetune,
          pair_weight=args.pair_weight, force=args.force,
          cond_sel=args.cond_sel, features=args.features)


if __name__ == "__main__":
    main()
