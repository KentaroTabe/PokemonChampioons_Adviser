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


PINNED_SOURCE = "pinned"          # 固定した選出モデルが 試用 Package / 登録チーム向け / 配布版 のどれとも中身が違うときの source_id


def _model_chain(my_species: Optional[list], version: Optional[str], registered_dir: Optional[Path]) -> tuple:
    """試用 Package → 登録チーム向け → 配布版 の順に引いた (path, source_id, 退避の理由の列) (副作用: ファイルの有無を見る)。
    退避の理由 (2026-10-31 E-inh-4。version 行の selection_model.fallback_reason / runtime に残す):
      - "registered_model_missing": 6 体がそろっているのに、その鍵の登録チーム向けモデルが無い (配布版を使う)
      - "registered_features_mismatch": 登録チーム向けモデルはあるが特徴量の版が違う (配布版を使う)
    試用 Package を使わなかった理由は advisor.versions.summarize_versions がラベルから決める (ここでは足さない)"""
    pkg = experiment_package_model()
    if pkg and (my_species is None or (pkg["species"] and set(my_species) <= set(pkg["species"]))):
        return Path(pkg["path"]), pkg["package_id"], []
    reg = registered_team_model(my_species, registered_dir, version)
    if reg:
        return Path(reg["path"]), REGISTERED_PREFIX + reg["key"], []
    reasons = []
    if my_species and len(set(my_species)) == 6:
        rdir = Path(registered_dir) if registered_dir is not None else REGISTERED_DIR
        if (rdir / team_key(my_species) / "selection_model.pt").exists():
            reasons.append("registered_features_mismatch")
        else:
            reasons.append("registered_model_missing")
    return deployed_model_path(version), None, reasons


def advisor_model_choice(my_species: Optional[list] = None, version: Optional[str] = None,
                         registered_dir: Optional[Path] = None) -> dict:
    """助言サーバーの選出モデルの決定 (例外を出さない。version 行の記録用)。
    {"path", "source_id", "fallback": [退避の理由], "pinned": 固定したか, "sha16": 固定したときの実際の sha16, "pin_error": 固定の異常 or None}。
    固定 (advisor.data_pin.model_source("selection_model")、2026-10-31 E-inh-4) したときは、そのファイルだけを使う: path = 固定のパス、
    source_id = 中身 (sha16) が同じなら 試用 Package / 登録チーム向け / 配布版 の id、どれとも違えば PINNED_SOURCE。ファイルが無い・
    sha16 が違えば pin_error (advisor_model_path は InputsPinError で止める。別のファイルへ退避しない)"""
    from advisor.data_pin import InputsPinError, check_model_file, model_source, sha16_bytes
    chain_path, chain_src, reasons = _model_chain(my_species, version, registered_dir)
    try:
        src = model_source("selection_model")
    except InputsPinError as e:
        # 版の固定の bundle が不正・個別の鍵と矛盾 (2026-10-31)
        return {"path": None, "source_id": PINNED_SOURCE, "fallback": [], "pinned": True, "sha16": None, "pin_error": str(e)}
    if not src["pinned"]:
        return {"path": chain_path, "source_id": chain_src, "fallback": reasons, "pinned": False, "sha16": None,
                "pin_error": None}
    try:
        sha = check_model_file("selection_model", src["path"], src["sha16"])
    except InputsPinError as e:
        return {"path": src["path"], "source_id": PINNED_SOURCE, "fallback": [], "pinned": True, "sha16": None,
                "pin_error": str(e)}
    from advisor import pin_bundle as PB
    if PB.active():
        # 版の固定の bundle (2026-10-31 追加レビュー判断 4): 由来は固定時に manifest に残したもの (origin)。配備先のファイルとの一致は
        # 探さない。source_id は表示だけの PINNED_SOURCE で、適用範囲は manifest の kind / team で決める (advisor.pin_bundle.selection_scope)
        try:
            sel = PB.selection_info() or {}
        except InputsPinError as e:
            return {"path": src["path"], "source_id": PINNED_SOURCE, "fallback": [], "pinned": True, "sha16": None,
                    "pin_error": str(e)}
        return {"path": Path(src["path"]), "source_id": PINNED_SOURCE, "fallback": [], "pinned": True, "sha16": sha,
                "pin_error": None, "origin": sel.get("source_id"), "kind": sel.get("kind")}
    source_id = PINNED_SOURCE
    cands = []
    pkg = experiment_package_model()
    if pkg:
        cands.append((Path(pkg["path"]), pkg["package_id"]))
    reg = registered_team_model(my_species, registered_dir, version)
    if reg:
        cands.append((Path(reg["path"]), REGISTERED_PREFIX + reg["key"]))
    cands.append((deployed_model_path(version), None))
    for cp, cid in cands:
        try:
            if sha16_bytes(Path(cp).read_bytes()) == sha:
                source_id = cid
                break
        except OSError:
            continue
    return {"path": Path(src["path"]), "source_id": source_id, "fallback": [], "pinned": True, "sha16": sha, "pin_error": None}


def advisor_model_path(my_species: Optional[list] = None, version: Optional[str] = None,
                       registered_dir: Optional[Path] = None) -> tuple:
    """助言サーバーの選出モデル → (path, source_id or None)。source_id は 試用 Package の id、登録チーム向けなら
    "registered:<key>"、配布版なら None。試用中 Package のモデルは、my_species (登録パーティの id) が Package の 6 体に含まれるときだけ
    使う。my_species を渡さなければ Package のモデルをそのまま返す。登録チーム向けは my_species の 6 体の鍵が一致するときだけ。
    2026-10-31 E-inh-4: 選出モデルを固定 (advisor.data_pin) したときはそのファイルだけを返し (source_id は advisor_model_choice)、
    無い・sha16 が違えば InputsPinError (登録チーム向け → 配布版 の退避をしない)"""
    ch = advisor_model_choice(my_species, version, registered_dir)
    if ch["pin_error"]:
        from advisor.data_pin import InputsPinError
        raise InputsPinError(ch["pin_error"])
    return Path(ch["path"]), ch["source_id"]


def model_label(source_id: Optional[str]) -> str:
    """選出モデルの経路の表示名: experiment:<Package id> / registered:<key> / deployed / pinned (固定したモデル、2026-10-31)"""
    if not source_id:
        return "deployed"
    if str(source_id).startswith(REGISTERED_PREFIX) or source_id == PINNED_SOURCE:
        return str(source_id)
    return f"experiment:{source_id}"
