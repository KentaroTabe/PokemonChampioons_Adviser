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


# ---- 登録チーム向けの選出モデル (2026-10-06 判断) ----
# 構築の改善 run (終了処理) は参照 = 登録の 6 体にも S7 と同じ適応 (5,000〜8,000 戦、独立 fold の実測で checkpoint を選ぶ) を作る。
# それを 6 体の鍵 (registered:<key>) で logs/registry/registered/<key>/selection_model.pt に置き (tools.team_build.register_selection)、
# 助言サーバーは 試用 Package → 登録チーム向け → 配布版 の順に引く。鍵は 6 体の種 id の集合なので、パーティを替えれば自動で外れる。
# 軽い適応 (S8a cheap、1,000 戦) のモデルはこの経路に置かない (参照で 0.313 と汎用 0.613 より弱い)
REGISTERED_DIR = REPO_ROOT / "logs" / "registry" / "registered"
REGISTERED_PREFIX = "registered:"


def team_key(species: list) -> str:
    """6 体の種 id の集合の鍵 (順序に依らない sha256 の先頭 16 桁)"""
    import hashlib
    ids = sorted({str(s).strip().lower() for s in (species or []) if s})
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]


def registered_team_model(my_species: Optional[list], registered_dir: Optional[Path] = None,
                          version: Optional[str] = None) -> Optional[dict]:
    """登録チーム向けの選出モデル {"key", "path", "species", "manifest"}。無い / 6 体でない / 特徴量の版が違えば None"""
    import json
    if not my_species or len(set(my_species)) != 6:
        return None
    rdir = Path(registered_dir) if registered_dir is not None else REGISTERED_DIR
    key = team_key(my_species)
    model = rdir / key / "selection_model.pt"
    if not model.exists():
        return None
    manifest: dict = {}
    try:
        manifest = json.loads((rdir / key / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    feat = manifest.get("features")
    if feat and features_version(feat) != features_version(version):
        return None                        # 作ったときの特徴量の版でしか読めない
    return {"key": key, "path": model, "species": list(manifest.get("species") or sorted(set(my_species))), "manifest": manifest}


def advisor_model_path(my_species: Optional[list] = None, version: Optional[str] = None,
                       registered_dir: Optional[Path] = None) -> tuple:
    """助言サーバーの選出モデル → (path, source_id or None)。source_id は 試用 Package の id、登録チーム向けなら
    "registered:<key>"、配布版なら None。試用中 Package のモデルは、my_species (登録パーティの id) が Package の 6 体に含まれるときだけ
    使う。my_species を渡さなければ Package のモデルをそのまま返す。登録チーム向けは my_species の 6 体の鍵が一致するときだけ"""
    pkg = experiment_package_model()
    if pkg and (my_species is None or (pkg["species"] and set(my_species) <= set(pkg["species"]))):
        return Path(pkg["path"]), pkg["package_id"]
    reg = registered_team_model(my_species, registered_dir, version)
    if reg:
        return Path(reg["path"]), REGISTERED_PREFIX + reg["key"]
    return deployed_model_path(version), None


def model_label(source_id: Optional[str]) -> str:
    """選出モデルの経路の表示名: experiment:<Package id> / registered:<key> / deployed"""
    if not source_id:
        return "deployed"
    if str(source_id).startswith(REGISTERED_PREFIX):
        return str(source_id)
    return f"experiment:{source_id}"
