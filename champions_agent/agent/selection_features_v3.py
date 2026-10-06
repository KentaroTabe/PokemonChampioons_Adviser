"""選出モデル v3 特徴量: v1 の機能埋め込みに「メガシンカ」(1 試合 1 回の資源) を織り込む。

2026-09-11 ユーザー指摘: 選出の学習が「どの個体がメガシンカするか」をチームプレビューから読めないのは致命的
(相手も自分も)。将来のダイマックス/テラスタル等の 1 試合 1 回の資源も同じ枠 (advisor.gimmick) で扱う。

- 自分側: 石を持つ個体はメガ後の姿の埋め込み (持ち物はチームプールの本文から引く: v2 と同じ方法。
  収集データは種族しか記録していないが、6 体の種族構成でプール内のチームを特定できる)。
- 相手側: 種族ごとの石の事前分布 (使用率 DB の石の使用率) でメガ後の埋め込みを混ぜる:
  (1 − Σp) · emb(素) + Σ p_f · emb(メガ後_f)。
- スカラー 6: 選出 3 体の石フラグ (3)、控えの石持ち数/3 (1)、相手 6 体の P(石) の平均/最大 (2)。
v1 と入力次元が違うため別モデル (selection_model_v3_general.pt)。埋め込みのピンも別ファイル
(v1 のピンを上書きしない。2026-08-06 に基底のずれで配布選出が崩れた前例)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np

from champions_agent.agent.selection_model import EMB_DIM, FEATURE_DIM
from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
from champions_agent.config import MODELS_DIR

V3_GENERAL_MODEL_PATH = MODELS_DIR / "selection_model_v3_general.pt"
V3_EMB_PIN_PATH = MODELS_DIR / "selection_model_v3_emb.json"
SCALAR_DIM_V3 = 6
FEATURE_DIM_V3 = FEATURE_DIM + SCALAR_DIM_V3


def _to_id(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


_store: dict | None = None
_emb_cache: dict = {}


def _functional_store() -> dict:
    """機能埋め込み (v3 のピン優先、無ければ生きた埋め込み)"""
    global _store
    if _store is None:
        store = {}
        try:
            raw = json.loads(V3_EMB_PIN_PATH.read_text(encoding="utf-8"))
            store = (raw.get("functional") or {}).get("vectors") or {}
        except Exception:
            pass
        if not store:
            try:
                from tools.species_embedding import load
                store = (load().get("functional") or {}).get("vectors") or {}
            except Exception:
                store = {}
        _store = store
    return _store


def reset_cache() -> None:
    global _store
    _store = None
    _emb_cache.clear()


def _emb(species) -> np.ndarray:
    sid = _to_id(species)
    if sid not in _emb_cache:
        v = _functional_store().get(sid)
        _emb_cache[sid] = (np.array(v, dtype=np.float32) if v else np.zeros(EMB_DIM, dtype=np.float32))
    return _emb_cache[sid]


def _has_emb(species) -> bool:
    return _to_id(species) in _functional_store()


# ------------------------------------------------------------------ 自分側: 石を持つ個体 (チームプールの本文から)
def own_stone_map(my_species: list, sets: Optional[dict] = None) -> dict:
    """{種族 id: メガ後 id or None}。sets = {sid: {"item": ...}} (省略時は v2 の _lookup_sets でプールから引く)"""
    from advisor.gimmick import mega_forms, stone_table
    if sets is None:
        try:
            from champions_agent.agent.selection_features_v2 import _lookup_sets
            sets = _lookup_sets(my_species)
        except Exception:
            sets = {}
    out = {}
    for s in my_species:
        sid = _to_id(s)
        item = _to_id((sets.get(sid) or {}).get("item") or "")
        msid = stone_table().get(item)
        out[sid] = msid if (msid and msid in mega_forms(sid)) else None
    return out


def emb_own(species, stone_map: dict) -> np.ndarray:
    """自分の個体の埋め込み: 石を持てばメガ後の姿 (無ければ素)"""
    sid = _to_id(species)
    msid = stone_map.get(sid)
    if msid and _has_emb(msid):
        return _emb(msid)
    return _emb(sid)


def emb_opp(species, prior: Optional[dict] = None, usage: Optional[dict] = None) -> np.ndarray:
    """相手の個体の埋め込み: (1 − Σp) · 素 + Σ p_f · メガ後_f (p は石の事前分布)"""
    sid = _to_id(species)
    if prior is None:
        from advisor.gimmick import stone_prior
        prior = stone_prior(sid, usage=usage)
    base = _emb(sid)
    if not prior:
        return base
    forms = {m: p for m, p in prior.items() if _has_emb(m)}
    total = min(1.0, sum(forms.values()))
    if total <= 0:
        return base
    out = (1.0 - total) * base
    for m, p in forms.items():
        out = out + p * _emb(m)
    return out.astype(np.float32)


def opp_stone_probs(opp_species: list, usage: Optional[dict] = None) -> list:
    from advisor.gimmick import stone_prior
    return [min(1.0, sum(stone_prior(_to_id(s), usage=usage).values())) for s in opp_species if s]


def build_features_v3(my_species: list, opp_species: list, perm, stone_map: Optional[dict] = None,
                      usage: Optional[dict] = None, own_items: Optional[list] = None) -> np.ndarray:
    """1 つの選出候補 → 特徴ベクトル (FEATURE_DIM_V3)。stone_map / usage は省略時に DB とプールから引く。
    own_items (収集データに記録した自分の持ち物、種族と同じ並び) があればそれを石の所在に使う (プールの型より確か)"""
    if stone_map is None:
        if own_items is not None and len(own_items) == len(my_species):
            stone_map = own_stone_map(my_species, {_to_id(s): {"item": it} for s, it in zip(my_species, own_items)})
        else:
            stone_map = own_stone_map(my_species)
    chosen = [emb_own(my_species[i], stone_map) for i in perm]
    benched = [emb_own(s, stone_map) for i, s in enumerate(my_species) if i not in perm]
    opp = [emb_opp(s, usage=usage) for s in opp_species if s]
    bench_mean = np.mean(benched, axis=0) if benched else np.zeros(EMB_DIM, dtype=np.float32)
    opp_mean = np.mean(opp, axis=0) if opp else np.zeros(EMB_DIM, dtype=np.float32)
    opp_max = np.max(opp, axis=0) if opp else np.zeros(EMB_DIM, dtype=np.float32)
    flags = [1.0 if stone_map.get(_to_id(my_species[i])) else 0.0 for i in perm]
    bench_stones = sum(1.0 for i, s in enumerate(my_species) if i not in perm and stone_map.get(_to_id(s))) / 3.0
    probs = opp_stone_probs(opp_species, usage)
    scalars = np.array(flags + [bench_stones, float(np.mean(probs)) if probs else 0.0,
                                float(np.max(probs)) if probs else 0.0], dtype=np.float32)
    return np.concatenate(chosen + [bench_mean, opp_mean, opp_max, scalars]).astype(np.float32)


def make_net_v3():
    import torch.nn as nn
    return nn.Sequential(nn.Linear(FEATURE_DIM_V3, 64), nn.ReLU(), nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 1))


_models: dict = {}


def load_model_v3(path: Path = V3_GENERAL_MODEL_PATH):
    key = str(path)
    if key in _models:
        return _models[key]
    try:
        import torch
        net = make_net_v3()
        net.load_state_dict(torch.load(path, map_location="cpu"))
        net.eval()
        _models[key] = net
    except Exception:
        _models[key] = None
    return _models[key]


def score_all_v3(my_species: list, opp_species: list, path: Path = V3_GENERAL_MODEL_PATH) -> list:
    """120 通りの (perm, 予測勝率) を降順に。モデルが無ければ空"""
    net = load_model_v3(path)
    if net is None or len(my_species) < 3:
        return []
    import torch
    stone_map = own_stone_map(my_species)
    perms = [p for p in SELECTION_PERMUTATIONS if max(p) < len(my_species)]
    X = np.stack([build_features_v3(my_species, opp_species, p, stone_map) for p in perms])
    with torch.no_grad():
        y = torch.sigmoid(net(torch.from_numpy(X))).squeeze(-1).numpy()
    return sorted(zip(perms, y.tolist()), key=lambda t: -t[1])


def predict_best_v3(my_species: list, opp_species: list, path: Path = V3_GENERAL_MODEL_PATH):
    scored = score_all_v3(my_species, opp_species, path)
    return scored[0] if scored else None
