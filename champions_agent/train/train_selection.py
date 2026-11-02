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

2026-11-02 学習履歴の記録 (レビュー 11/2 判断 1): 保存したモデルごとに、同じ dir の <stem>_meta.json
(selection_model.provenance_meta_path。配布版は META_PATH = selection_model_meta.json) に model_sha16 (そのモデルの
sha256 の先頭 16 桁) と provenance を書く:
  {"kind": "general" | "team_finetuned", "finetune_team": [6 体の種族 id] or None, "sets_premise": "fixed" | "none",
   "sets": 固定型なら {species_id: 型} or None, "general_model_sha16", "trained_at", "data_path", "code_commit",
   "source": "trained" (学習時に書いた記録。記録から後で復元したものは "reconstructed"、tools/inheritance_eval/reconstruct_provenance.py。
   鍵が無い記録も読み手は trained とみなす)}
配布版 (MODEL_PATH) は finetune で実際に微調整したときだけ team_finetuned (型は my_team の登録 = 微調整データの収集
(collect_selection_data --teams myteam) が使う build_myteam_text の型。学習時点の登録を記録する)。微調整を見送った・
--no-finetune なら general。汎用モデル (GENERAL_MODEL_PATH / v2 / v3)・条件付きモデル (COND_MODEL_PATH) は general、
sets_premise "none" (型に条件づけていない)。版の固定の bundle (advisor.pin_bundle.classify_from_meta) はこの記録と
model_sha16 の一致だけで種別を決める (ファイルの名前では決めない)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np

from champions_agent.agent.selection_model import (
    GENERAL_MODEL_PATH, META_PATH, MODEL_PATH, build_features, make_net, provenance_meta_path,
)
from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
from champions_agent.config import MODELS_DIR, RANDOM_SEED

DATA_PATH = Path(__file__).resolve().parent / "logs" / "selection_data.npz"
REPO_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_KINDS = ("general", "team_finetuned")
PROVENANCE_PREMISES = ("fixed", "none")


# ------------------------------------------------------------------ 学習履歴 (provenance)
def build_provenance(kind: str, finetune_team: Optional[list], sets_premise: str, sets: Optional[dict],
                     general_model_sha16: Optional[str], data_path, code_commit: Optional[str],
                     trained_at: Optional[str] = None) -> dict:
    """学習履歴の記録 (純粋)。kind = general / team_finetuned、sets_premise = fixed (学習データが型を固定) / none (型に条件づけない)。
    team_finetuned は 6 体、fixed は型 (sets) を持つ。形が合わなければ ValueError (読み手が「不明」にする記録を書かない)"""
    if kind not in PROVENANCE_KINDS:
        raise ValueError(f"provenance の kind {kind!r} は {PROVENANCE_KINDS} のどれか")
    if sets_premise not in PROVENANCE_PREMISES:
        raise ValueError(f"provenance の sets_premise {sets_premise!r} は {PROVENANCE_PREMISES} のどれか")
    team = sorted({str(s) for s in finetune_team or []}) or None
    if kind == "team_finetuned" and (team is None or len(team) != 6):
        raise ValueError(f"team_finetuned には 6 体の finetune_team が要る ({finetune_team!r})")
    if kind == "general" and team is not None:
        raise ValueError("general に finetune_team は書かない")
    return {"kind": kind, "finetune_team": team, "sets_premise": sets_premise,
            "sets": (dict(sets) if sets_premise == "fixed" and sets else None),
            "general_model_sha16": general_model_sha16, "trained_at": trained_at or time.strftime("%Y-%m-%d %H:%M:%S"),
            "data_path": str(data_path) if data_path is not None else None, "code_commit": code_commit, "source": "trained"}


def model_meta_doc(model_sha16: str, provenance: dict, base: Optional[dict] = None) -> dict:
    """meta の中身 (純粋): base (teams・n_samples・val など) に model_sha16 と provenance を足す"""
    out = dict(base or {})
    out["model_sha16"] = model_sha16
    out["provenance"] = dict(provenance)
    return out


def distributed_provenance(ft_team: Optional[list], sets: Optional[dict], general_model_sha16: Optional[str], data_path,
                           code_commit: Optional[str], trained_at: Optional[str] = None) -> dict:
    """配布版 (MODEL_PATH) の provenance (純粋): 実際に微調整した 6 体 (ft_team) があれば team_finetuned (型の前提 fixed、
    sets = 微調整データの型。読めなければ None)、見送った・--no-finetune なら general (型の前提 none)"""
    if ft_team:
        return build_provenance("team_finetuned", ft_team, "fixed", sets, general_model_sha16, data_path, code_commit, trained_at)
    return build_provenance("general", None, "none", None, general_model_sha16, data_path, code_commit, trained_at)


def _sha16(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _code_commit() -> Optional[str]:
    """学習したコードの commit (副作用: git の読み取り。取れなければ None)"""
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def write_model_meta(model_path: Path, provenance: dict, base: Optional[dict] = None,
                     meta_path: Optional[Path] = None) -> Path:
    """保存済みのモデルの meta を書く (副作用: モデルの読み取りと meta の書き込み)。meta_path の既定は同じ dir の <stem>_meta.json"""
    mp = Path(meta_path) if meta_path is not None else provenance_meta_path(model_path)
    mp.write_text(json.dumps(model_meta_doc(_sha16(model_path), provenance, base), ensure_ascii=False), encoding="utf-8")
    return mp


def _myteam_sets(team: list) -> Optional[dict]:
    """微調整データの型 (副作用: 登録の読み取り): build_myteam_text (collect_selection_data --teams myteam が使う本文) を
    {species_id: 型} にして 6 体に絞る。読めなければ None (読み手は型の前提を「不明」にする)"""
    try:
        from tools.evaluate_team import build_myteam_text
        from tools.party_improvements import parse_showdown_sets
        sets = parse_showdown_sets(build_myteam_text())
    except Exception as e:
        print(f"  ⚠ 微調整の型 (my_team) を読めない: {e} (meta の sets は None)")
        return None
    want = {str(s) for s in team}
    out = {k: v for k, v in sets.items() if _norm_species(k) in want}
    return out if len(out) == len(want) else None


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
    # 列が増えた収集 (own_items = 自分の持ち物: メガ石の所在) は、受け取れる特徴量関数 (v3) にだけ渡す
    import inspect
    accepts_items = "own_items" in inspect.signature(builder).parameters
    has_items = "own_items" in d.files
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
        if accepts_items and has_items:
            items = [str(s) for s in d["own_items"][i]]
            feats.append(builder(team, opp, perm, own_items=items if any(items) else None))
        else:
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


def _finetune_on_myteam(net, X, y, meta, lr: float, epochs: int = 200) -> Optional[list]:
    """全体で学習したあと、実際に使うチームのデータだけで微調整する。

    構築プールを60→272に広げると未知チームへの汎化は上がるが、その分
    my_team への密着が落ちる (実測: 未知チーム検証 +4.7%→+7.3% の一方で
    my_team実戦 0.52→0.44)。汎化を土台にしてから使うチームに寄せ直す。

    戻り値: 微調整した 6 体 (種族 id のソート済み list)。見送ったら None (2026-11-02: meta の provenance に使う)
    """
    import torch
    from torch.optim import Adam
    try:
        mine = _myteam_species()
    except Exception as e:
        print(f"  微調整をとばします (my_teamを読めない: {e})")
        return None
    idx = [i for i, k in enumerate(meta["team_key"])
           if {_norm_species(s) for s in k.split("|")} == mine]
    if len(idx) < 500:
        print(f"  微調整をとばします (my_teamのデータが{len(idx)}件と少ない。"
              "bash scripts/collect_selection.sh 2 2500 myteam で収集する)")
        return None

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
    return sorted(mine)


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
    elif features == "v3":
        from champions_agent.agent.selection_features_v3 import build_features_v3
        builder = build_features_v3
    else:
        builder = build_features
    X, y, meta = load_dataset(opp_col="sel" if cond_sel else "team",
                              builder=builder)
    tag = " (相手の実選出3体に条件付け)" if cond_sel else ""
    if features == "v2":
        tag += " [v2特徴量: 型情報+メタ事前分布]"
    if features == "v3":
        tag += " [v3特徴量: メガシンカ (自分は石の有無、相手は石の事前分布) 込み]"
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
    elif features == "v3":
        from champions_agent.agent.selection_features_v3 import make_net_v3
        net = make_net_v3()
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
        if features == "v3":
            # v3 は自分のピンを持つ (v1 のピンを上書きすると、基底のずれで配布中の v1 が崩れる: 2026-08-06 の前例)
            from champions_agent.agent.selection_features_v3 import V3_EMB_PIN_PATH
            shutil.copy(EMB_LIVE_PATH, V3_EMB_PIN_PATH)
            print(f"[train_selection] v3 の埋め込みをピン止め: {V3_EMB_PIN_PATH}")
        else:
            shutil.copy(EMB_LIVE_PATH, EMB_PIN_PATH)
            print(f"[train_selection] 埋め込みをピン止め: {EMB_PIN_PATH}")
    except Exception as e:
        print(f"  ⚠ 埋め込みのピン止めに失敗: {e}")
    # 学習履歴 (2026-11-02): どの保存先にも、同じ dir の <stem>_meta.json に model_sha16 と provenance を書く
    d = np.load(DATA_PATH)
    teams = sorted({tuple(sorted(str(s).lower() for s in t))
                    for t in d["team"]})
    # 学習に使ったチームを記録する。未学習のチームでは予測が外挿になるため、
    # アドバイザー側で「参考値」と断って表示するのに使う。
    # val は未知チーム検証の実測 (構築提案の運用ゲート tools/team_proposal が
    # 「モデルの構成汎化が測定済みか」を機械判定するのに使う)
    base_meta = {"teams": [list(t) for t in teams], "n_samples": int(meta["n"]),
                 "val": {"mse": round(best_val, 5), "baseline_mse": round(base, 5),
                         "gain_pct": round(gain, 2), "split": split_kind,
                         "at": time.strftime("%Y-%m-%d %H:%M")}}
    commit = _code_commit()

    def _save_general(path: Path, label: str) -> str:
        torch.save(net.state_dict(), path)
        sha = _sha16(path)
        write_model_meta(path, build_provenance("general", None, "none", None, sha, DATA_PATH, commit), base_meta)
        print(f"[train_selection] {label}保存: {path} (meta {provenance_meta_path(path).name}: general)")
        return sha
    if features == "v3":
        from champions_agent.agent.selection_features_v3 import V3_GENERAL_MODEL_PATH
        _save_general(V3_GENERAL_MODEL_PATH, "v3 汎用モデル")
        return
    if features == "v2":
        # v2は比較実験中の候補。v1の配布/汎用/微調整には触れない
        from champions_agent.agent.selection_features_v2 import (
            V2_GENERAL_MODEL_PATH,
        )
        _save_general(V2_GENERAL_MODEL_PATH, "v2汎用モデル")
        return
    if cond_sel:
        # 条件付きモデルは利得行列 (payoff_matrix) 専用の別ファイル。
        # 配布版/汎用モデル (相手6体に条件付け) とは入力の意味が違うため
        # 上書きしてはいけない
        from champions_agent.agent.selection_model import COND_MODEL_PATH
        _save_general(COND_MODEL_PATH, "条件付きモデル")
        _report_top(net, X, y)
        return
    # 微調整前の汎用モデルを別に残す。my_team に寄せた配布版では
    # 「毎戦チームが変わる」ベンチマークの測定に使えない
    general_sha = _save_general(GENERAL_MODEL_PATH, "汎用モデル")

    ft_team = _finetune_on_myteam(net, X, y, meta, lr / 10) if finetune else None

    torch.save(net.state_dict(), MODEL_PATH)
    prov = distributed_provenance(ft_team, _myteam_sets(ft_team) if ft_team else None, general_sha, DATA_PATH, commit)
    write_model_meta(MODEL_PATH, prov, base_meta, meta_path=META_PATH)
    print(f"[train_selection] 保存: {MODEL_PATH} (meta {META_PATH.name}: {prov['kind']}"
          + (f" {prov['finetune_team']} 型 {'あり' if prov['sets'] else '読めない'}" if ft_team else "") + ")")

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


def adapt_candidate(data_path: Path, out_model: Path, base_model: Path = GENERAL_MODEL_PATH,
                    lr: float = 1e-4, epochs: int = 200, holdout: float = 0.2,
                    report_json: Path | None = None, seed: int = RANDOM_SEED) -> dict:
    """構築システムの候補専用選出モデル (S7): 汎用モデルの重みを起点に、候補チームで集めた
    データ (data_path、単一チーム) だけで微調整して out_model に保存する。production の
    MODEL_PATH / GENERAL_MODEL_PATH / META_PATH には触れない。

    戻り値 (report_json にも保存): n, val_mse, base_val_mse (微調整前), gain_pct, improved
    """
    import json
    import torch
    from torch.optim import Adam
    from champions_agent.agent import selection_dispatch as SD
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    # 候補専用モデルの版は config SELECTION_FEATURES (v3 = メガシンカ込み)。起点の汎用モデルも同じ版
    version = SD.features_version()
    X, y, meta = load_dataset(Path(data_path), builder=SD.builder(version))
    n = len(y)
    net = SD.make_net(version)
    if base_model == GENERAL_MODEL_PATH:
        base_model = SD.general_model_path(version)
    if Path(base_model).exists():
        net.load_state_dict(torch.load(base_model, map_location="cpu"))
    idx = rng.permutation(n)
    n_val = max(1, int(n * holdout))
    va, tr = idx[:n_val], idx[n_val:]
    Xt, yt = torch.from_numpy(X), torch.from_numpy(y).unsqueeze(-1)

    def val_mse() -> float:
        net.eval()
        with torch.no_grad():
            pred = torch.sigmoid(net(Xt[va]))
            return float(((pred - yt[va]) ** 2).mean())

    base_val = val_mse()
    opt = Adam(net.parameters(), lr=lr)
    best_val, best_state = base_val, {k: v.clone() for k, v in net.state_dict().items()}
    for _ in range(epochs):
        net.train()
        perm = rng.permutation(tr)
        for i in range(0, len(perm), 256):
            b = perm[i:i + 256]
            opt.zero_grad()
            loss = torch.nn.functional.binary_cross_entropy_with_logits(net(Xt[b]), yt[b])
            loss.backward()
            opt.step()
        v = val_mse()
        if v < best_val:
            best_val, best_state = v, {k: v_.clone() for k, v_ in net.state_dict().items()}
    net.load_state_dict(best_state)
    out_model = Path(out_model)
    out_model.parent.mkdir(parents=True, exist_ok=True)
    torch.save(net.state_dict(), out_model)
    gain = (base_val - best_val) / base_val * 100 if base_val > 0 else 0.0
    rep = {"n": int(n), "n_train": int(len(tr)), "n_val": int(n_val), "base_val_mse": round(base_val, 5),
           "val_mse": round(best_val, 5), "gain_pct": round(gain, 2), "improved": best_val < base_val,
           "base_model": str(base_model), "out_model": str(out_model), "data": str(data_path),
           "teams": int(meta.get("teams", 0)), "features": version}
    if report_json:
        Path(report_json).write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[adapt_candidate] n={n} val_mse {base_val:.4f} → {best_val:.4f} ({gain:+.1f}%) 保存: {out_model}")
    return rep


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
    ap.add_argument("--features", default="v1", choices=["v1", "v2", "v3"],
                    help="v2=型情報+メタ事前分布を加えた特徴量 (比較実験用。"
                         "保存先も selection_model_v2_general.pt に分離)。"
                         "v3=メガシンカ (1試合1回の資源) 込み: 自分は石の有無、相手は石の事前分布 "
                         "(保存先 selection_model_v3_general.pt、埋め込みのピンも別)")
    ap.add_argument("--adapt-data", default=None, help="候補専用の適応: 候補データ npz")
    ap.add_argument("--adapt-out", default=None, help="候補専用の適応: 保存先 .pt")
    ap.add_argument("--adapt-report", default=None, help="候補専用の適応: 結果 JSON")
    args = ap.parse_args()
    if args.adapt_data:
        adapt_candidate(Path(args.adapt_data), Path(args.adapt_out), lr=args.lr / 10, epochs=args.epochs,
                        holdout=args.holdout, report_json=Path(args.adapt_report) if args.adapt_report else None)
        return
    train(args.epochs, args.lr, args.holdout, finetune=not args.no_finetune,
          pair_weight=args.pair_weight, force=args.force,
          cond_sel=args.cond_sel, features=args.features)


if __name__ == "__main__":
    main()
