"""選出モデル v2 特徴量: 種族埋め込み (v1) + 型情報 + 相手のメタ事前分布。

v1 (selection_model.build_features) は種族の機能埋め込みのみで、
「スカーフの有無」「先制技」「素早さライン」といった型レベルの判断材料を
持たない。上位プレイヤーの選出はここを読む。

- 自分側: チームプールのテキストから実セット (持ち物/技/配分) を引く。
  収集データは種族しか記録していないが、6体の種族構成からプール内の
  チームを一意に特定できる (271チームは種族構成で互いに異なる)。
- 相手側: 実セットは見えないので、使用率DBから種族ごとの事前分布
  (スカーフ率・こだわり率・タスキ率・素早さ配分・先制技率) を使う。

v1と入力次元が違うため別モデル (selection_model_v2_general.pt)。
v1は配布のまま残し、A/B比較で優劣を判定してから昇格を判断する。

⚠ 実測の結果、v2は棄却 (2026-08-02。事前登録ゲート2つとも不通過):
  - 未知チーム検証のMSE改善: v1 +5.6% / v2 +5.5% (52,000件・同一分割)
  - 未知チームのペア順位付け精度: v1 0.642 / v2 0.631 (1,222組)
  カバレッジは健全 (271/271チーム特定成功・相手61/63種に事前分布) で、
  「特徴量が届かなかった」わけではない。機能埋め込みが型情報の効きを
  既に内包しているか、このデータ量では手作り8次元がノイズになる。
  オンライン測定 (20,000戦) はゲート不通過のため実施していない。
  モジュールは将来の特徴量実験の土台として残す (配布には未使用)。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from champions_agent.agent.selection_model import (
    EMB_DIM, FEATURE_DIM, build_features,
)
from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
from champions_agent.config import MODELS_DIR

V2_GENERAL_MODEL_PATH = MODELS_DIR / "selection_model_v2_general.pt"

SET_DIM = 8          # 1体ぶんの型特徴
PRIOR_DIM = 5        # 相手1種族ぶんのメタ事前分布
# v1の120次元 + 選出3体の型(8x3) + 控え平均(8) + 相手事前分布 平均+最大(5x2)
FEATURE_DIM_V2 = FEATURE_DIM + SET_DIM * 4 + PRIOR_DIM * 2

# 先制技 / 積み技 (選出判断に効く代表的なもの。網羅よりも安定を優先)
PRIORITY_MOVES = {
    "aquajet", "suckerpunch", "machpunch", "bulletpunch", "shadowsneak",
    "extremespeed", "iceshard", "quickattack", "vacuumwave", "grassyglide",
    "jetpunch", "accelerock", "firstimpression", "fakeout",
}
SETUP_MOVES = {
    "swordsdance", "nastyplot", "dragondance", "calmmind", "irondefense",
    "bulkup", "quiverdance", "shellsmash", "agility", "curse", "howl",
}


def _to_id(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# ------------------------------------------------------------------
# 自分側: チームプールから実セットを引く
# ------------------------------------------------------------------
_team_lookup: dict | None = None
_mega_stones: set | None = None


def _mega_stone_ids() -> set:
    """メガストーンのitem id集合 (dexのrequiredItemから)"""
    global _mega_stones
    if _mega_stones is None:
        from advisor.dex import get_dex
        dex = get_dex()
        _mega_stones = {
            _to_id(sp.get("requiredItem"))
            for sp in dex._species.values()
            if sp.get("isMega") and sp.get("requiredItem")}
    return _mega_stones


def _parse_block(block: str) -> dict:
    lines = [l.strip() for l in block.strip().split("\n")]
    head = lines[0]
    item = head.split(" @ ", 1)[1] if " @ " in head else ""
    moves = [l[2:].strip() for l in lines if l.startswith("- ")]
    nature = next((l[:-len(" Nature")] for l in lines
                   if l.endswith(" Nature")), "")
    spe_ev = 0
    for l in lines:
        if l.startswith("EVs: "):
            m = re.search(r"(\d+)\s+Spe", l)
            spe_ev = int(m.group(1)) if m else 0
    return {"item": _to_id(item), "moves": [_to_id(m) for m in moves],
            "nature": nature.lower(), "spe_ev": spe_ev}


def _build_team_lookup() -> dict:
    """種族構成 (ソート済みタプル) -> {種族id: セット} の辞書"""
    from champions_agent.env.ranked_teams import build_ranked_teams
    texts = list(build_ranked_teams(include_external=True))
    try:
        from tools.evaluate_team import build_myteam_text
        texts.append(build_myteam_text())
    except Exception:
        pass
    out: dict = {}
    for text in texts:
        blocks = [b for b in text.strip().split("\n\n") if b.strip()]
        sets = {}
        for b in blocks:
            sid = _to_id(b.strip().split("\n")[0].split(" @ ")[0])
            sets[sid] = _parse_block(b)
        key = tuple(sorted(sets))
        out.setdefault(key, sets)
    return out


def _lookup_sets(my_species: list) -> dict:
    global _team_lookup
    if _team_lookup is None:
        _team_lookup = _build_team_lookup()
    key = tuple(sorted(_to_id(s) for s in my_species))
    return _team_lookup.get(key, {})


# みがるなど素早さ性格の補正 (choiceに使うのは上昇/下降の符号だけ)
_SPE_PLUS = {"timid", "jolly", "hasty", "naive"}
_SPE_MINUS = {"brave", "relaxed", "quiet", "sassy"}


def _set_feats(species, sets: dict) -> np.ndarray:
    """1体ぶんの型特徴 (8次元)。セット不明ならゼロ"""
    v = np.zeros(SET_DIM, dtype=np.float32)
    s = sets.get(_to_id(species))
    if not s:
        return v
    item = s["item"]
    v[0] = 1.0 if item == "choicescarf" else 0.0
    v[1] = 1.0 if item in ("choiceband", "choicespecs") else 0.0
    v[2] = 1.0 if item == "focussash" else 0.0
    v[3] = 1.0 if item in _mega_stone_ids() else 0.0
    # 素早さの実数値 (Lv50, champions配分)。/200で正規化
    try:
        from advisor.dex import calc_stat, get_dex
        sp = get_dex().species(_to_id(species))
        base = (sp or {}).get("baseStats", {}).get("spe", 80)
        nat = 1.1 if s["nature"] in _SPE_PLUS else \
            (0.9 if s["nature"] in _SPE_MINUS else 1.0)
        # championsの能力ポイント(0-32)は従来EV(0-252)の約1/8刻み
        v[4] = calc_stat(base, s["spe_ev"] * 8, nat) / 200.0
    except Exception:
        pass
    moves = set(s["moves"])
    v[5] = 1.0 if moves & PRIORITY_MOVES else 0.0
    v[6] = 1.0 if moves & SETUP_MOVES else 0.0
    v[7] = len(moves) / 4.0
    return v


# ------------------------------------------------------------------
# 相手側: 使用率DBのメタ事前分布
# ------------------------------------------------------------------
_prior_cache: dict | None = None


def _priors() -> dict:
    """種族id -> [スカーフ率, こだわり率, タスキ率, 平均SpeEV/32, 先制技率]"""
    global _prior_cache
    if _prior_cache is None:
        out: dict = {}
        try:
            from champions_agent.config import USAGE_TARGET_FORMAT
            from champions_agent.data import database as db
            with db.get_connection() as conn:
                snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
                if snap:
                    for r in conn.execute(
                            """SELECT pokemon_name, item_name, usage_percent
                               FROM item_usage WHERE snapshot_id = ?""",
                            (snap,)):
                        sid = _to_id(r["pokemon_name"])
                        it = _to_id(r["item_name"])
                        p = out.setdefault(sid, np.zeros(PRIOR_DIM,
                                                         dtype=np.float32))
                        w = float(r["usage_percent"]) / 100.0
                        if it == "choicescarf":
                            p[0] += w
                        if it in ("choiceband", "choicespecs"):
                            p[1] += w
                        if it == "focussash":
                            p[2] += w
                    for r in conn.execute(
                            """SELECT pokemon_name, evs, usage_percent
                               FROM spread_usage WHERE snapshot_id = ?""",
                            (snap,)):
                        sid = _to_id(r["pokemon_name"])
                        p = out.setdefault(sid, np.zeros(PRIOR_DIM,
                                                         dtype=np.float32))
                        try:
                            spe = int(str(r["evs"]).split("/")[5])
                        except (IndexError, ValueError):
                            continue
                        p[3] += (spe / 32.0) * float(r["usage_percent"]) / 100.0
                    for r in conn.execute(
                            """SELECT pokemon_name, move_name, usage_percent
                               FROM move_usage WHERE snapshot_id = ?""",
                            (snap,)):
                        if _to_id(r["move_name"]) in PRIORITY_MOVES:
                            sid = _to_id(r["pokemon_name"])
                            p = out.setdefault(
                                sid, np.zeros(PRIOR_DIM, dtype=np.float32))
                            p[4] += float(r["usage_percent"]) / 100.0
        except Exception:
            pass
        # 率は0-1へクリップ (使用率の重複計上を安全側で吸収)
        _prior_cache = {k: np.clip(v, 0.0, 1.0) for k, v in out.items()}
    return _prior_cache


def _prior_feats(species) -> np.ndarray:
    return _priors().get(_to_id(species),
                         np.zeros(PRIOR_DIM, dtype=np.float32))


# ------------------------------------------------------------------
# 特徴量とモデル
# ------------------------------------------------------------------
def build_features_v2(my_species: list, opp_species: list,
                      perm) -> np.ndarray:
    base = build_features(my_species, opp_species, perm)
    sets = _lookup_sets(my_species)
    chosen = [_set_feats(my_species[i], sets) for i in perm]
    benched = [_set_feats(s, sets)
               for i, s in enumerate(my_species) if i not in perm]
    bench_mean = (np.mean(benched, axis=0) if benched
                  else np.zeros(SET_DIM, dtype=np.float32))
    opp = [_prior_feats(s) for s in opp_species if s]
    opp_mean = (np.mean(opp, axis=0) if opp
                else np.zeros(PRIOR_DIM, dtype=np.float32))
    opp_max = (np.max(opp, axis=0) if opp
               else np.zeros(PRIOR_DIM, dtype=np.float32))
    return np.concatenate(
        [base] + chosen + [bench_mean, opp_mean, opp_max]).astype(np.float32)


def make_net_v2():
    import torch.nn as nn
    return nn.Sequential(
        nn.Linear(FEATURE_DIM_V2, 64), nn.ReLU(),
        nn.Linear(64, 32), nn.ReLU(),
        nn.Linear(32, 1),
    )


_models: dict = {}


def load_model_v2(path: Path = V2_GENERAL_MODEL_PATH):
    key = str(path)
    if key in _models:
        return _models[key]
    try:
        import torch
        net = make_net_v2()
        net.load_state_dict(torch.load(path, map_location="cpu"))
        net.eval()
        _models[key] = net
    except Exception:
        _models[key] = None
    return _models[key]


def predict_best_v2(my_species: list, opp_species: list,
                    path: Path = V2_GENERAL_MODEL_PATH):
    """最良の選出 (perm, 予測勝率)。モデルが無ければ None"""
    net = load_model_v2(path)
    if net is None or len(my_species) < 3:
        return None
    import torch
    perms = [p for p in SELECTION_PERMUTATIONS if max(p) < len(my_species)]
    feats = np.stack([build_features_v2(my_species, opp_species, p)
                      for p in perms])
    with torch.no_grad():
        pred = torch.sigmoid(net(torch.from_numpy(feats))).squeeze(-1).numpy()
    i = int(np.argmax(pred))
    return perms[i], float(pred[i])
