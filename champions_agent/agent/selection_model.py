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
    """勝率回帰のMLP (小さめ: データが数千件規模のため)"""
    import torch.nn as nn
    return nn.Sequential(
        nn.Linear(FEATURE_DIM, 64), nn.ReLU(),
        nn.Linear(64, 32), nn.ReLU(),
        nn.Linear(32, 1), nn.Sigmoid(),
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
        pred = net(torch.from_numpy(feats)).squeeze(-1).numpy()
    out = sorted(zip(perms, pred.tolist()), key=lambda x: -x[1])
    return out


def predict_best(my_species: list, opp_species: list,
                 path: Path = MODEL_PATH):
    """最良の選出 (perm, 予測勝率)。モデルが無ければ None"""
    scored = score_all(my_species, opp_species, path)
    return scored[0] if scored else None
