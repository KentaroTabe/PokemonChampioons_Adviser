"""選出モデル: 「この6体からこの3体をこの順で出したときの勝率」を予測する。

方策勾配 (120通りの分類) ではなく**勝率の回帰**にしている理由:
選出は1エピソード1決定・報酬は勝敗のみで、120通りの分類をREINFORCEで
学ぶにはサンプルが桁で足りない。回帰なら1エピソードが必ず1つの
(状態, 行動) → 勝率 の教師データになり、サンプル効率が大きく上がる。

入力は種族IDではなく**機能埋め込み** (メタ上位への対面ベクトル) を使う:
- 未知の種族へ汎化する (種族の同一性を覚える必要がない)
- 組合せ爆発を圧縮できる (tools/species_embedding 参照)

推論時は120通りすべてをスコアして最大を選ぶ (探索空間が小さいので総当たりで十分)。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
from champions_agent.config import MODELS_DIR

MODEL_PATH = MODELS_DIR / "selection_model.pt"
# 微調整前の汎用モデル。配布版 (MODEL_PATH) は my_team に寄せてあるため、
# ランクド構築でのベンチマークにはこちらを使う (自チームが毎戦変わる場面で
# 特定チーム向けの偏りを持ち込まない)
GENERAL_MODEL_PATH = MODELS_DIR / "selection_model_general.pt"
META_PATH = MODELS_DIR / "selection_model_meta.json"
EMB_DIM = 20        # 機能埋め込みの次元 (メタ上位N体)
FEATURE_DIM = EMB_DIM * 6   # 選出3体(順序込み) + 控え平均 + 相手平均/最大


def _to_id(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


_emb_cache: dict = {}


def _emb(species) -> np.ndarray:
    """種族の機能埋め込み (未収録はゼロ)"""
    sid = _to_id(species)
    if sid not in _emb_cache:
        try:
            from tools.species_embedding import vector
            v = vector(sid, "functional")
        except Exception:
            v = None
        _emb_cache[sid] = (np.array(v, dtype=np.float32) if v
                           else np.zeros(EMB_DIM, dtype=np.float32))
    return _emb_cache[sid]


def build_features(my_species: list, opp_species: list, perm) -> np.ndarray:
    """1つの選出候補を特徴ベクトルにする。

    my_species: 自分6体の種族 / opp_species: 相手6体 (不明はNone可)
    perm: 選ぶ3体のインデックス (順序あり)
    """
    chosen = [_emb(my_species[i]) for i in perm]
    benched = [_emb(s) for i, s in enumerate(my_species) if i not in perm]
    opp = [_emb(s) for s in opp_species if s]
    bench_mean = (np.mean(benched, axis=0) if benched
                  else np.zeros(EMB_DIM, dtype=np.float32))
    opp_mean = (np.mean(opp, axis=0) if opp
                else np.zeros(EMB_DIM, dtype=np.float32))
    opp_max = (np.max(opp, axis=0) if opp
               else np.zeros(EMB_DIM, dtype=np.float32))
    return np.concatenate(chosen + [bench_mean, opp_mean, opp_max]).astype(
        np.float32)


def make_net():
    """勝率回帰のMLP (小さめ: データが数千件規模のため)。

    出力は**ロジット**。勝率が要る場所で sigmoid をかける。
    ペアワイズ学習 (同一条件の2つの選出のどちらが勝ったかを学ぶ) で
    スコア差をそのまま扱いたいため。Sigmoid は重みを持たないので、
    末尾に含めていた頃のチェックポイントもそのまま読める。
    """
    import torch.nn as nn
    return nn.Sequential(
        nn.Linear(FEATURE_DIM, 64), nn.ReLU(),
        nn.Linear(64, 32), nn.ReLU(),
        nn.Linear(32, 1),
    )


_models: dict = {}   # path -> net or None


def load_model(path: Path = MODEL_PATH):
    key = str(path)
    if key in _models:
        return _models[key]
    try:
        import torch
        net = make_net()
        net.load_state_dict(torch.load(path, map_location="cpu"))
        net.eval()
        _models[key] = net
    except Exception:
        _models[key] = None
    return _models[key]


def trained_teams() -> list:
    """学習に使ったチーム (種族idのソート済みタプル) の一覧"""
    import json
    try:
        d = json.loads(META_PATH.read_text(encoding="utf-8"))
        return [tuple(sorted(t)) for t in d.get("teams", [])]
    except Exception:
        return []


def is_in_distribution(my_species: list) -> bool:
    """そのチームで学習済みか (未学習なら予測は外挿=参考値)"""
    teams = trained_teams()
    if not teams:
        return False
    return tuple(sorted(_to_id(s) for s in my_species)) in teams


def score_all(my_species: list, opp_species: list,
              path: Path = MODEL_PATH) -> list:
    """[(perm, 予測勝率)] を降順で返す。モデルが無ければ空リスト"""
    net = load_model(path)
    if net is None or len(my_species) < 3:
        return []
    import torch
    perms = [p for p in SELECTION_PERMUTATIONS
             if max(p) < len(my_species)]
    feats = np.stack([build_features(my_species, opp_species, p)
                      for p in perms])
    with torch.no_grad():
        pred = torch.sigmoid(net(torch.from_numpy(feats))).squeeze(-1).numpy()
    out = sorted(zip(perms, pred.tolist()), key=lambda x: -x[1])
    return out


def predict_best(my_species: list, opp_species: list,
                 path: Path = MODEL_PATH):
    """最良の選出 (perm, 予測勝率)。モデルが無ければ None"""
    scored = score_all(my_species, opp_species, path)
    return scored[0] if scored else None


# ------------------------------------------------------------------
# 選出の読み合い (利得行列の均衡解)
# ------------------------------------------------------------------
# score_all は「相手の選出を平均化した」勝率を最大化する。しかし相手も
# こちらの6体を見て3体を選ぶ同時手番ゲームであり、上位プレイヤーは
# この読み合いを織り込む。自分の120通り × 相手の20通り (3体組・順序は
# 特徴量に影響しない) の利得行列を作り、ゼロサムゲームとして解く。
#
# ⚠ 前提: 相手3体に条件付けた勝率予測ができるモデル (opp_sel を記録した
# データで学習した COND_MODEL_PATH)。相手6体で学習した汎用モデルに
# 3体だけ渡すと外挿になるため、条件付きモデルの検証前に既定へ
# 昇格させてはいけない。

# 相手の実選出 (opp_sel) に条件付けたモデル。--cond-sel で学習する
COND_MODEL_PATH = MODELS_DIR / "selection_model_cond.pt"


def payoff_matrix(my_species: list, opp_species: list,
                  path: Path = COND_MODEL_PATH):
    """予測勝率の利得行列 (行=自分の120選出, 列=相手の3体組20通り)。

    返り値: (M, my_perms, opp_combos)。モデルが無い/6体未満なら None
    """
    from itertools import combinations
    net = load_model(path)
    if net is None or len(my_species) < 3:
        return None
    opp = [s for s in opp_species if s]
    if len(opp) < 4:
        # 相手の選出に不確実性がない/小さいときは行列にする意味がない
        return None
    import torch
    my_perms = [p for p in SELECTION_PERMUTATIONS
                if max(p) < len(my_species)]
    opp_combos = list(combinations(range(len(opp)), 3))
    feats = np.stack([
        build_features(my_species, [opp[j] for j in combo], perm)
        for perm in my_perms for combo in opp_combos])
    with torch.no_grad():
        pred = torch.sigmoid(net(torch.from_numpy(feats))).squeeze(-1).numpy()
    M = pred.reshape(len(my_perms), len(opp_combos))
    return M, my_perms, opp_combos


def solve_matrix_game(M: np.ndarray, iters: int = 2000):
    """ゼロサム行列ゲームを fictitious play で近似的に解く。

    行側が M (勝率) を最大化、列側が最小化する。
    返り値: (行の混合戦略, 列の混合戦略, ゲーム値)
    fictitious play はゼロサム2人ゲームで均衡に収束することが知られている
    (Robinson 1951)。iters=2000 で誤差は実用上無視できる。
    """
    n, m = M.shape
    row_counts = np.zeros(n)
    col_counts = np.zeros(m)
    r = int(np.argmax(M.mean(axis=1)))
    c = int(np.argmin(M.mean(axis=0)))
    for _ in range(iters):
        row_counts[r] += 1
        col_counts[c] += 1
        r = int(np.argmax(M @ (col_counts / col_counts.sum())))
        c = int(np.argmin((row_counts / row_counts.sum()) @ M))
    p = row_counts / row_counts.sum()
    q = col_counts / col_counts.sum()
    value = float(p @ M @ q)
    return p, q, value


def predict_maximin(my_species: list, opp_species: list,
                    path: Path = COND_MODEL_PATH):
    """読み合いを織り込んだ選出。

    返り値: (perm, ゲーム値, [(perm, 混合確率)降順])。前提を満たさない
    (条件付きモデルが無い等) ときは None → 呼び出し側は score_all へ
    フォールバックすること。
    perm は均衡混合戦略で最も重い選出 (決定的。評価の再現性のため)。
    対人で読まれるのを避けたい場合は混合確率でサンプリングする。
    """
    made = payoff_matrix(my_species, opp_species, path)
    if made is None:
        return None
    M, my_perms, _ = made
    p, _q, value = solve_matrix_game(M)
    order = np.argsort(-p)
    ranked = [(my_perms[i], float(p[i])) for i in order if p[i] > 0]
    return my_perms[int(order[0])], value, ranked
