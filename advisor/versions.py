"""実戦で「どの版が」動いているかの記録 (2026-10-05 ②: 運用条件の記録)。

受入条件 1: 指定された Package と、実際に読み込んだ選出モデル・行動方策の両方を残す。Package のラベルだけでは、読み込みの失敗や
規則への退避を見逃す。ここでは
  - 指定: logs/.experiment_package のラベル (designated_package)
  - 実際: 選出モデルは advisor_model_path が返した経路 (登録パーティが Package の 6 体に含まれないときは配布版に退避する) と、
    その sha256、退避の理由。行動方策 (RL) は読み込み順の先頭に存在する zip とその sha256、読み込めたか
  - 構築と型の版: config/my_team.json の本文の sha256 と 6 体
  - 規則・データの版: git の commit、図鑑と効果表の sha256、選出の特徴量の版
をまとめ、全体の digest (version_id) を付ける。battle_logger が対戦の先頭に "version" の行として書き、各助言の行は version_id と
選出モデルの実際の経路を持つ。評価側 (tools/team_build/real_eval.version_check) は Package の manifest の selection_model_sha256 と
ここに残った sha を突き合わせる。

純粋な部分 (digest / summarize_versions / sha) はテスト対象 (tests/test_advice_trace.py)。ファイルを読む部分は失敗しても例外を
外へ出さず None を入れる (記録のために助言を止めない)。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
EXPERIMENT_MARK = REPO / "logs" / ".experiment_package"
_CACHE: dict = {"key": None, "value": None, "t": 0.0}
CACHE_TTL_SEC = 30.0


def sha256_file(path, n: int = 16) -> Optional[str]:
    """ファイルの sha256 (先頭 n 桁)。読めなければ None"""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:n]
    except Exception:
        return None


def sha256_text(text: str, n: int = 16) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:n]


def digest(doc: dict, n: int = 12) -> str:
    """版の要約の digest (純粋): 時刻などの揮発する項目を除いて計算する"""
    stable = {k: v for k, v in (doc or {}).items() if k not in ("t", "version_id", "collected_at")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:n]


def summarize_versions(designated_package: Optional[str], selection_path: Optional[str], selection_sha: Optional[str],
                       selection_package: Optional[str], package_species: Optional[list], my_species: Optional[list],
                       rl_path: Optional[str], rl_sha: Optional[str], rl_loaded: Optional[bool], rl_error: Optional[str],
                       team_sha: Optional[str], git: Optional[str], dex_sha: Optional[str], effects_sha: Optional[str],
                       features: Optional[str]) -> dict:
    """版の要約 (純粋)。退避の理由 (fallback_reason) をここで決める:
      - ラベルが無い → None (退避ではない。配布版が本来の経路)
      - ラベルはあるが Package のモデルが使われていない → "package_model_missing" (モデルのファイルが無い) か
        "party_not_in_package" (登録パーティが Package の 6 体に含まれない)
      - 選出モデルのファイルが無い → "selection_model_missing" (規則への退避)"""
    fallback = None
    if designated_package and selection_package != designated_package:
        if package_species and my_species and not set(my_species) <= set(package_species):
            fallback = "party_not_in_package"
        else:
            fallback = "package_model_missing"
    if selection_path and selection_sha is None:
        fallback = (fallback + "+" if fallback else "") + "selection_model_missing"
    doc = {
        "designated_package": designated_package,
        "selection_model": {"package": selection_package, "path": selection_path, "sha256": selection_sha,
                            "fallback_reason": fallback},
        "action_policy": {"path": rl_path, "sha256": rl_sha, "loaded": rl_loaded, "error": rl_error},
        "team": {"sha256": team_sha, "species": sorted(str(s) for s in (my_species or []))},
        "rules": {"git_commit": git, "dex_sha256": dex_sha, "effects_sha256": effects_sha, "selection_features": features},
    }
    doc["version_id"] = digest(doc)
    return doc


def _registered_team() -> tuple:
    try:
        from tools.team_build.run import registered_team
        text, ids, _mega = registered_team()
        return (sha256_text(text) if text else None), list(ids or [])
    except Exception:
        return None, []


def _rl_info() -> tuple:
    """行動方策: 読み込み順の先頭に存在する zip、その sha、読み込めたか (まだ試していなければ None)、失敗の理由"""
    try:
        from advisor import rl_bridge as RB
        style = os.environ.get("RL_ADVICE_STYLE", "balance")
        source = os.environ.get("RL_POLICY_SOURCE", "ema")
        order = RB._policy_candidates(style, source)
        path = next((RB.CKPT_DIR / n for n in order if (RB.CKPT_DIR / n).exists()), None)
        loaded = None if not RB._model_tried else (RB._model is not None)
        err = None if loaded in (None, True) else "load_failed (sb3_contrib / torch / file)"
        return (str(path) if path else None), (sha256_file(path) if path else None), loaded, err
    except Exception as e:
        return None, None, None, repr(e)


def runtime_versions(refresh: bool = False) -> dict:
    """今動いている版の要約 (30 秒キャッシュ。ラベルのファイルと my_team.json の更新時刻が変わればやり直す)"""
    try:
        key = (EXPERIMENT_MARK.stat().st_mtime if EXPERIMENT_MARK.exists() else None,
               (REPO / "config" / "my_team.json").stat().st_mtime if (REPO / "config" / "my_team.json").exists() else None)
    except Exception:
        key = None
    now = time.time()
    if not refresh and _CACHE["value"] is not None and _CACHE["key"] == key and now - _CACHE["t"] < CACHE_TTL_SEC:
        return _CACHE["value"]
    designated = None
    try:
        designated = EXPERIMENT_MARK.read_text(encoding="utf-8").strip() or None
    except OSError:
        designated = None
    team_sha, my_species = _registered_team()
    sel_path, sel_pkg, pkg_species = None, None, None
    try:
        from champions_agent.agent.selection_dispatch import advisor_model_path, experiment_package_model, features_version
        p, pkg = advisor_model_path(my_species or None)
        sel_path, sel_pkg = str(p), pkg
        ep = experiment_package_model()
        pkg_species = list(ep["species"]) if ep else None
        features = features_version()
    except Exception:
        features = None
    rl_path, rl_sha, rl_loaded, rl_err = _rl_info()
    git = None
    try:
        from tools.team_build.manifest import git_commit
        git = git_commit(REPO)
    except Exception:
        pass
    doc = summarize_versions(designated, sel_path, sha256_file(sel_path) if sel_path else None, sel_pkg, pkg_species, my_species,
                             rl_path, rl_sha, rl_loaded, rl_err, team_sha, git,
                             sha256_file(REPO / "advisor" / "data" / "dex.json"), sha256_file(REPO / "advisor" / "data" / "move_effects.json"),
                             features)
    doc["collected_at"] = round(now, 2)
    _CACHE.update(key=key, value=doc, t=now)
    return doc


def rl_loaded_now() -> Optional[bool]:
    """助言の時点で行動方策が読み込めているか (まだ試していなければ None)"""
    try:
        from advisor import rl_bridge as RB
        return None if not RB._model_tried else (RB._model is not None)
    except Exception:
        return None
