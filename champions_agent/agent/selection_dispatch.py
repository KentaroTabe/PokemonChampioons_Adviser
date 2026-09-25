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


# ---- 試用中 Package の選出モデル (2026-09-25) ----
# 接続テストで候補 Package を試すとき (logs/.experiment_package = package_id)、Package 同梱の適応済み選出モデル
# (logs/registry/package/<id>/advisor_policy/selection_model.pt) を助言サーバーの選出の推しに使う。測定 (S8b の fresh 変種、
# S10、holdout) と同じモデルで試すため (arch_0924 の L06_C020 は汎用モデルだと S8a で degraded、専用モデルで +0.28)。
# 登録パーティが Package の 6 体に含まれるときだけ使い (別のパーティに外挿しない)、ラベル OFF で配布版に戻る。
# 評価 (evaluate / check_advisor_player) はこの経路を通らない (配布版と汎用モデルの測定軸は動かさない)。
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXPERIMENT_MARK = REPO_ROOT / "logs" / ".experiment_package"
PACKAGES_DIR = REPO_ROOT / "logs" / "registry" / "package"


def experiment_package_model(mark: Optional[Path] = None, packages_dir: Optional[Path] = None) -> Optional[dict]:
    """試用中 Package の同梱モデル {"package_id", "path", "species"}。ラベル無し / Package が無い / モデル無しなら None。
    パスは注入できる (テスト用)。既定はモジュールの EXPERIMENT_MARK / PACKAGES_DIR (呼び出し時に読む)"""
    import json
    mark = Path(mark) if mark is not None else EXPERIMENT_MARK
    packages_dir = Path(packages_dir) if packages_dir is not None else PACKAGES_DIR
    try:
        package_id = mark.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not package_id or "/" in package_id or package_id.startswith("."):
        return None
    pdir = packages_dir / package_id
    model = pdir / "advisor_policy" / "selection_model.pt"
    if not model.exists():
        return None
    species: list = []
    try:
        team = json.loads((pdir / "team.json").read_text(encoding="utf-8"))
        species = [str(s) for s in (team.get("species") or [])]
    except (OSError, ValueError):
        species = []
    return {"package_id": package_id, "path": model, "species": species}


def advisor_model_path(my_species: Optional[list] = None, version: Optional[str] = None) -> tuple:
    """助言サーバーの選出モデル → (path, package_id or None)。試用中 Package のモデルは、my_species (登録パーティの id) が
    Package の 6 体に含まれるときだけ使う。my_species を渡さなければ Package のモデルをそのまま返す"""
    pkg = experiment_package_model()
    if pkg and (my_species is None or (pkg["species"] and set(my_species) <= set(pkg["species"]))):
        return Path(pkg["path"]), pkg["package_id"]
    return deployed_model_path(version), None
