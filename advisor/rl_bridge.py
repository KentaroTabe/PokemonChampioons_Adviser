"""RL学習結果 (MaskablePPO) のアドバイス統合。

学習は poke-env の Battle オブジェクトを encoders.encode_battle で
227次元観測にして行われている。本モジュールは画面抽出の状態辞書から
**同じ意味・同じ並びの観測ベクトルを再現**し、学習済み方策の
行動分布と局面価値をアドバイザーに提供する。

- 行動index: 0-5=交代(パーティ並び順) / 6-9=技 / 10-13=技+メガシンカ
- 使用チェックポイント: RL_ADVICE_STYLE (既定 balance)
- モデル/依存が無い環境では None を返して本体に影響しない
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np

from advisor.dex import get_dex

CKPT_DIR = (Path(__file__).resolve().parent.parent
            / "champions_agent" / "train" / "checkpoints")

ALL_TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting",
             "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
             "dragon", "dark", "steel", "fairy"]
TYPE_TO_IDX = {t: i for i, t in enumerate(ALL_TYPES)}
STATUS_LIST = ["none", "brn", "par", "slp", "frz", "psn", "tox"]
STATUS_MAP = {"burn": "brn", "paralysis": "par", "sleep": "slp",
              "freeze": "frz", "poison": "psn", "toxic": "tox"}
BOOST_KEYS = ["atk", "def", "spa", "spd", "spe", "acc", "eva"]
BASE_KEYS = ["hp", "atk", "def", "spa", "spd", "spe"]
N_MOVE_SLOTS, MOVE_FEAT_DIM, OBS_DIM = 4, 9, 227

_JA2EN_TYPES = {"ノーマル": "normal", "ほのお": "fire", "みず": "water",
                "でんき": "electric", "くさ": "grass", "こおり": "ice",
                "かくとう": "fighting", "どく": "poison", "じめん": "ground",
                "ひこう": "flying", "エスパー": "psychic", "むし": "bug",
                "いわ": "rock", "ゴースト": "ghost", "ドラゴン": "dragon",
                "あく": "dark", "はがね": "steel", "フェアリー": "fairy"}

_model = None
_model_tried = False


def _load_model():
    global _model, _model_tried
    if _model_tried:
        return _model
    _model_tried = True
    style = os.environ.get("RL_ADVICE_STYLE", "balance")
    path = CKPT_DIR / f"battle_policy_{style}.zip"
    try:
        from sb3_contrib import MaskablePPO
        _model = MaskablePPO.load(str(path), device="cpu")
        print(f"[rl_bridge] 学習済み方策をロード: {path.name}")
    except Exception as e:
        print(f"[rl_bridge] 方策ロード不可 ({e}) — RL提案は無効")
        _model = None
    return _model


def _en_types(p: dict) -> list:
    out = []
    for t in (p.get("types") or []):
        en = _JA2EN_TYPES.get(t, str(t).lower())
        if en in TYPE_TO_IDX:
            out.append(en)
    if not out:
        sp = get_dex().species(p.get("species_id"))
        if sp:
            out = [t.lower() for t in sp["types"] if t.lower() in TYPE_TO_IDX]
    return out


def _types_multihot(p: dict) -> np.ndarray:
    v = np.zeros(18, dtype=np.float32)
    for t in _en_types(p):
        v[TYPE_TO_IDX[t]] = 1.0
    return v


def _base_vec(p: dict) -> np.ndarray:
    v = np.zeros(6, dtype=np.float32)
    sp = get_dex().species(p.get("species_id"))
    if sp:
        for i, k in enumerate(BASE_KEYS):
            v[i] = (sp["baseStats"].get(k) or 0) / 200.0
    return v


def _boosts_vec(p: dict) -> np.ndarray:
    v = np.zeros(7, dtype=np.float32)
    b = p.get("boosts") or {}
    for i, k in enumerate(BOOST_KEYS):
        v[i] = (b.get(k) or 0) / 6.0
    return v


def _status_vec(p: dict) -> np.ndarray:
    v = np.zeros(7, dtype=np.float32)
    s = STATUS_MAP.get(p.get("status") or "", None)
    v[STATUS_LIST.index(s) if s else 0] = 1.0
    return v


def _hp_frac(p: dict) -> float:
    if p.get("status") == "fainted":
        return 0.0
    if p.get("hp_percent") is not None:
        return max(0.0, min(1.0, p["hp_percent"] / 100.0))
    if p.get("hp_current") is not None and p.get("hp_max"):
        return max(0.0, p["hp_current"] / p["hp_max"])
    return 1.0


def _eff(mtype_en: str, defender: dict) -> float:
    dex = get_dex()
    dtypes = [t.capitalize() for t in _en_types(defender)]
    return dex.effectiveness(mtype_en.capitalize(), dtypes) if dtypes else 1.0


def _move_vec(m: dict, own: dict, opp: Optional[dict]) -> np.ndarray:
    v = np.zeros(MOVE_FEAT_DIM, dtype=np.float32)
    mv = get_dex().move(m.get("move_id"))
    if not mv:
        return v
    power = float(mv.get("power") or 0)
    acc = mv.get("accuracy")
    acc = 1.0 if acc in (None, True) else (acc / 100.0 if acc > 1 else float(acc))
    cat = (mv.get("category") or "Status").lower()
    pp, mx = m.get("pp"), m.get("max_pp")
    pp_frac = (max(0, pp) / mx) if (pp is not None and mx) else 1.0
    mtype = (mv.get("type") or "Normal").lower()
    v[0] = min(power, 150.0) / 150.0
    v[1] = acc
    v[2] = (float(mv.get("priority") or 0) + 5.0) / 10.0
    v[3] = 1.0 if cat == "physical" else 0.0
    v[4] = 1.0 if cat == "special" else 0.0
    v[5] = 1.0 if cat == "status" else 0.0
    v[6] = 1.0 if mtype in _en_types(own) else 0.0
    v[7] = (_eff(mtype, opp) / 4.0) if (opp is not None and power > 0) else 0.25
    v[8] = pp_frac
    return v


def _bench_vec(p: Optional[dict], seen_flag: bool) -> np.ndarray:
    dim = 21 if seen_flag else 20
    v = np.zeros(dim, dtype=np.float32)
    if p is None:
        return v
    v[:18] = _types_multihot(p)
    v[18] = _hp_frac(p)
    v[19] = 1.0 if p.get("status") == "fainted" else 0.0
    if seen_flag:
        v[20] = 1.0
    return v


def _side_vec(side: dict) -> np.ndarray:
    v = np.zeros(8, dtype=np.float32)
    hz = side.get("hazards") or {}
    sc = side.get("screens") or {}
    v[0] = 1.0 if hz.get("stealth_rock") else 0.0
    v[1] = min(hz.get("spikes") or 0, 3) / 3.0
    v[2] = min(hz.get("toxic_spikes") or 0, 2) / 2.0
    v[3] = 1.0 if hz.get("sticky_web") else 0.0
    v[4] = 1.0 if sc.get("reflect") else 0.0
    v[5] = 1.0 if sc.get("light_screen") else 0.0
    v[6] = 1.0 if sc.get("aurora_veil") else 0.0
    v[7] = 1.0 if side.get("tailwind") else 0.0
    return v


def _field_vec(state: dict) -> np.ndarray:
    v = np.zeros(10, dtype=np.float32)
    f = state.get("field") or {}
    w = f.get("weather")
    v[0] = 1.0 if w == "sandstorm" else 0.0
    v[1] = 1.0 if w == "rain" else 0.0
    v[2] = 1.0 if w == "sun" else 0.0
    v[3] = 1.0 if w == "snow" else 0.0
    t = f.get("terrain")
    v[4] = 1.0 if t == "electric" else 0.0
    v[5] = 1.0 if t == "grassy" else 0.0
    v[6] = 1.0 if t == "psychic" else 0.0
    v[7] = 1.0 if t == "misty" else 0.0
    v[8] = 1.0 if f.get("trick_room") else 0.0
    v[9] = min(int(state.get("turn") or 0), 50) / 50.0
    return v


def encode_state(state: dict, my_spe_actual: Optional[float] = None) -> Optional[np.ndarray]:
    """状態辞書 -> encode_battle と同義・同並びの227次元観測"""
    my = state.get("player") or {}
    op = state.get("opponent") or {}
    mi, oi = my.get("active_index"), op.get("active_index")
    own = my["party"][mi] if mi is not None and mi < len(my.get("party", [])) else None
    opp = op["party"][oi] if oi is not None and oi < len(op.get("party", [])) else None
    if own is None:
        return None

    moves = (own.get("moves") or [])[:N_MOVE_SLOTS]
    own_parts = [_types_multihot(own), _base_vec(own), _boosts_vec(own),
                 np.array([_hp_frac(own)], dtype=np.float32), _status_vec(own)]
    for i in range(N_MOVE_SLOTS):
        own_parts.append(_move_vec(moves[i], own, opp) if i < len(moves)
                         else np.zeros(MOVE_FEAT_DIM, dtype=np.float32))
    own_vec = np.concatenate(own_parts)

    if opp is not None:
        opp_vec = np.concatenate([
            _types_multihot(opp), _base_vec(opp), _boosts_vec(opp),
            np.array([_hp_frac(opp)], dtype=np.float32), _status_vec(opp)])
        revealed = opp.get("revealed_moves") or []
        max_eff = 0.0
        from vision.normalize import NameResolver
        # 判明技の最大相性 (打点タイプのみ)。名前解決はresolver無しでも動くよう
        # dexのID直照合を先に試す
        for ja in revealed[:4]:
            mv = get_dex().move(str(ja))
            if mv and (mv.get("power") or 0) > 0:
                max_eff = max(max_eff, _eff((mv.get("type") or "normal").lower(), own))
        opp_extra = np.array([min(len(revealed), 4) / 4.0, max_eff / 4.0],
                             dtype=np.float32)
    else:
        opp_vec = np.zeros(39, dtype=np.float32)
        opp_extra = np.zeros(2, dtype=np.float32)

    own_bench = [p for j, p in enumerate(my.get("party", [])) if j != mi][:2]
    opp_bench = [p for j, p in enumerate(op.get("party", []))
                 if j != oi and (p.get("species_id") or p.get("types"))][:2]
    own_bench_vecs = [_bench_vec(own_bench[i] if i < len(own_bench) else None, False)
                      for i in range(2)]
    opp_bench_vecs = [_bench_vec(opp_bench[i] if i < len(opp_bench) else None, True)
                      for i in range(2)]
    opp_remaining = op.get("remaining")
    if opp_remaining is None:
        known = [p for p in op.get("party", []) if p.get("species_id") or p.get("types")]
        opp_remaining = max(1, sum(1 for p in known if p.get("status") != "fainted")) \
            if known else 3
    opp_count = np.array([min(opp_remaining, 3) / 3.0], dtype=np.float32)

    speed = np.zeros(2, dtype=np.float32)
    sp_opp = get_dex().species((opp or {}).get("species_id"))
    opp_base_spe = (sp_opp["baseStats"].get("spe") if sp_opp else 0) or 0
    if my_spe_actual and opp_base_spe > 0:
        ratio = my_spe_actual / opp_base_spe
        speed[0] = min(ratio, 2.0) / 2.0
        speed[1] = 1.0 if ratio >= 1.0 else 0.0

    vec = np.concatenate([own_vec, opp_vec, opp_extra,
                          *own_bench_vecs, *opp_bench_vecs, opp_count,
                          _side_vec(my), _side_vec(op),
                          _field_vec(state), speed]).astype(np.float32)
    if len(vec) < OBS_DIM:
        vec = np.concatenate([vec, np.zeros(OBS_DIM - len(vec), dtype=np.float32)])
    return vec[:OBS_DIM]


def _legal_actions(state: dict) -> list:
    """[(action_index, label, kind)] 合法手のみ"""
    my = state.get("player") or {}
    mi = my.get("active_index")
    party = my.get("party", [])
    own = party[mi] if mi is not None and mi < len(party) else None
    out = []
    for j, p in enumerate(party[:6]):
        if j != mi and p.get("species_id") and p.get("status") != "fainted":
            out.append((j, f"交代:{p.get('species_ja') or p['species_id']}", "switch"))
    if own:
        mega_ok = (not (state.get("mega_used") or {}).get("player")) and \
            ((own.get("item_ja") or "").endswith(("ナイト", "ナイトX", "ナイトY"))
             or own.get("item_id") == "megastone")
        for i, m in enumerate((own.get("moves") or [])[:4]):
            if not m.get("move_id"):
                continue
            if m.get("pp") is not None and m["pp"] <= 0:
                continue
            name = m.get("name_ja") or m["move_id"]
            out.append((6 + i, name, "move"))
            if mega_ok:
                out.append((10 + i, f"{name}+メガ", "mega"))
    return out


def value_of_sim(me, opp, my_moves: list, fieldv=None,
                 turn: int = 5) -> Optional[float]:
    """探索の葉ノード (SimSide対) をRL価値関数で評価する [-1,1]。

    SimSideから最小限の状態辞書を組み立ててencode_stateを再利用する。
    価値ヘッドの生値は報酬スケールなので tanh で圧縮して静的評価と揃える。
    """
    model = _load_model()
    if model is None:
        return None

    def party_of(side, active_moves=None):
        entries = [{
            "species_id": side.active.species_id,
            "hp_percent": max(0.0, side.active_hp) * 100.0,
            "status": side.active.status,
            "boosts": side.active.boosts or {},
            "moves": [{"move_id": m} for m in (active_moves or [])],
        }]
        for v, hp in side.bench:
            entries.append({"species_id": v.species_id,
                            "hp_percent": max(0.0, hp) * 100.0,
                            "status": "fainted" if hp <= 0 else None})
        return entries

    state = {
        "turn": turn,
        "field": {"weather": getattr(fieldv, "weather", None),
                  "terrain": getattr(fieldv, "terrain", None),
                  "trick_room": bool(getattr(fieldv, "trick_room", False))},
        "mega_used": {},
        "player": {"active_index": 0,
                   "remaining": me.alive_count(),
                   "hazards": {"stealth_rock": me.stealth_rock},
                   "screens": {},
                   "party": party_of(me, my_moves)},
        "opponent": {"active_index": 0,
                     "remaining": opp.alive_count(),
                     "hazards": {"stealth_rock": opp.stealth_rock},
                     "screens": {},
                     "party": party_of(opp)},
    }
    from advisor.damage import effective_speed
    obs = encode_state(state, my_spe_actual=effective_speed(me.active, fieldv))
    if obs is None:
        return None
    import math
    import torch
    obs_t, _ = model.policy.obs_to_tensor(obs[None, :])
    with torch.no_grad():
        v = float(model.policy.predict_values(obs_t).item())
    return math.tanh(v / 40.0)


def policy_hint(state: dict, my_spe_actual: Optional[float] = None) -> Optional[dict]:
    """学習済み方策の行動分布 (合法手で正規化) と局面価値を返す"""
    model = _load_model()
    if model is None:
        return None
    obs = encode_state(state, my_spe_actual)
    if obs is None:
        return None
    legal = _legal_actions(state)
    if not legal:
        return None
    import torch
    obs_t, _ = model.policy.obs_to_tensor(obs[None, :])
    with torch.no_grad():
        dist = model.policy.get_distribution(obs_t)
        probs = dist.distribution.probs.detach().cpu().numpy()[0]
        value = float(model.policy.predict_values(obs_t).item())
    scored = [(float(probs[idx]), label, kind) for idx, label, kind in legal]
    total = sum(p for p, _, _ in scored) or 1.0
    ranked = sorted(((p / total, label, kind) for p, label, kind in scored),
                    reverse=True)
    return {
        "top": [{"label": l, "prob": round(p, 3), "kind": k}
                for p, l, k in ranked[:4]],
        "value": round(value, 2),
        "style": os.environ.get("RL_ADVICE_STYLE", "balance"),
    }
