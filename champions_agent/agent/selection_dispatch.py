"""選出モデルの版 (v1 / v3) の振り分け。config SELECTION_FEATURES に従う。

呼び出し側 (構築の相手の選出 apply_model_teampreview、測定の助言の選出 advisor_pick_order、候補専用モデルの適応) は
ここを通し、版ごとの特徴量関数・ネット・汎用モデルのパスを直接参照しない。
候補専用モデル (S7 の適応で作る .pt) は作ったときの版で読む必要があるので、報告 JSON に "features" を残す。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from champions_agent.config import SELECTION_FEATURES


def features_version(version: Optional[str] = None) -> str:
    v = (version or SELECTION_FEATURES or "v1").lower()
    return v if v in ("v1", "v3") else "v1"


def general_model_path(version: Optional[str] = None) -> Path:
    if features_version(version) == "v3":
        from champions_agent.agent.selection_features_v3 import V3_GENERAL_MODEL_PATH
        return V3_GENERAL_MODEL_PATH
    from champions_agent.agent.selection_model import GENERAL_MODEL_PATH
    return GENERAL_MODEL_PATH


def deployed_model_path(version: Optional[str] = None) -> Path:
    """配布版 (my_team に寄せた微調整済み)。v3 は微調整版を持たないので汎用モデル"""
    if features_version(version) == "v3":
        return general_model_path("v3")
    from champions_agent.agent.selection_model import MODEL_PATH
    return MODEL_PATH


def builder(version: Optional[str] = None):
    if features_version(version) == "v3":
        from champions_agent.agent.selection_features_v3 import build_features_v3
        return build_features_v3
    from champions_agent.agent.selection_model import build_features
    return build_features


def make_net(version: Optional[str] = None):
    if features_version(version) == "v3":
        from champions_agent.agent.selection_features_v3 import make_net_v3
        return make_net_v3()
    from champions_agent.agent.selection_model import make_net as _mk
    return _mk()


def score_all(my_species: list, opp_species: list, path: Optional[Path] = None,
              version: Optional[str] = None) -> list:
    """[(perm, 予測勝率)] 降順。path 省略時は配布版 (v1) / 汎用 (v3)。モデルが無ければ空"""
    v = features_version(version)
    p = Path(path) if path else deployed_model_path(v)
    if v == "v3":
        from champions_agent.agent.selection_features_v3 import score_all_v3
        return score_all_v3(my_species, opp_species, p)
    from champions_agent.agent.selection_model import score_all as _score
    return _score(my_species, opp_species, p)


def predict_best(my_species: list, opp_species: list, path: Optional[Path] = None, version: Optional[str] = None):
    scored = score_all(my_species, opp_species, path, version)
    return scored[0] if scored else None
