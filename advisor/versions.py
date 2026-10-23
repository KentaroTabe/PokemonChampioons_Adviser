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

2026-10-20 レビュー (版の識別と実行状態の分離。tests/test_version_split.py):
  version_id の digest の材料を **版の識別** (identity_of) に限る: コード (git commit・選出の特徴量の版)、モデルの実体の sha
  (選出モデル .pt・RL 方策ファイル・種族の埋め込み)、データ版 (使用率の snapshot id・図鑑・効果表・登録チームの sha と 6 体)、
  採点設定 (方式・感度・表示用の探索・RL / 探索の重み・鍵の版)。
  **実行状態** (runtime_of: RL 方策の読み込み 未試行 None / 成功 True / 失敗 False とその理由、選出モデルの退避理由、指定 Package の
  ラベル、使用率の snapshot id の出どころ) は digest に入れず、version 行の "runtime" に残す (action_policy.loaded / error と
  selection_model.fallback_reason も従来どおり残す)。以前は action_policy.loaded が digest に入り、再起動後の 1 戦目
  (RL 方策の遅延読み込みの前に version 行を書くので null) だけ version_id が違って見えた。
  読み込みが version 行の後に完了・失敗したら battle_logger が "runtime" 行を 1 行書く (runtime_change)。advice 行の
  policy.rl_loaded も従来どおり。照合する側は「版の識別が同じ」と「実行状態が同じ (読み込み成功)」を別々に確かめる。
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
EMBEDDING_PATH = REPO / "champions_agent" / "data" / "species_embedding.json"
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


# 採点設定の欄 (advice_key.scoring_settings の鍵 + 鍵の版)。identity_of の "scoring" はこの順で並べる
SCORING_IDENTITY_KEYS = ("scoring_mode", "sensitivity", "display_search", "rl_blend", "search_blend", "key_version")


def identity_of(doc: Optional[dict]) -> dict:
    """版の識別 (純粋。version_id の digest の材料): version 行 (または summarize_versions の戻り値) から、コード・モデルの実体の sha・
    データ版・採点設定だけを取り出す。実行状態 (読み込みの成否・退避理由・指定のラベル) と時刻は入れない。

    2026-10-20 より前の version 行 (scoring / embedding / usage_snapshot の欄が無い) も読める: 採点設定は行の上位の欄
    (scoring_mode / transition_sensitivity / display_search、2026-10-16〜18 に足した欄) から取り、無い欄は None"""
    d = doc or {}
    sel = d.get("selection_model") or {}
    ap = d.get("action_policy") or {}
    team = d.get("team") or {}
    rules = d.get("rules") or {}
    sc = d.get("scoring")
    if not isinstance(sc, dict):
        sc = {"scoring_mode": d.get("scoring_mode"), "sensitivity": d.get("transition_sensitivity"),
              "display_search": d.get("display_search")}
    return {
        "code": {"git_commit": rules.get("git_commit"), "selection_features": rules.get("selection_features")},
        "models": {"selection_sha256": sel.get("sha256"), "rl_sha256": ap.get("sha256"),
                   "embedding_sha256": rules.get("embedding_sha256")},
        "data": {"usage_snapshot": rules.get("usage_snapshot"), "dex_sha256": rules.get("dex_sha256"),
                 "effects_sha256": rules.get("effects_sha256"), "team_sha256": team.get("sha256"),
                 "team_species": sorted(str(x) for x in (team.get("species") or []))},
        "scoring": {k: sc.get(k) for k in SCORING_IDENTITY_KEYS},
    }


def runtime_of(doc: Optional[dict]) -> dict:
    """実行状態 (純粋。digest に入れない): RL 方策の読み込み (None = まだ試していない / True = 成功 / False = 失敗) とその理由、
    選出モデルの退避理由と出どころ、指定 Package のラベル、使用率の snapshot id の出どころ。version 行に "runtime" があれば
    それを優先し、無い旧版の行は action_policy / selection_model / designated_package から作る"""
    d = doc or {}
    rt = d.get("runtime")
    if isinstance(rt, dict):
        return dict(rt)
    sel = d.get("selection_model") or {}
    ap = d.get("action_policy") or {}
    return {"rl_loaded": ap.get("loaded"), "rl_error": ap.get("error"), "selection_fallback_reason": sel.get("fallback_reason"),
            "selection_source": sel.get("source"), "selection_package": sel.get("package"),
            "designated_package": d.get("designated_package"), "usage_snapshot_source": None}


def runtime_change(runtime: Optional[dict], rl_loaded: Optional[bool]) -> Optional[dict]:
    """version 行の後の RL 方策の読み込み状態の変化 (純粋)。今の状態 rl_loaded が runtime の rl_loaded と違えば、新しい実行状態
    (runtime の写しに rl_loaded / rl_error を入れたもの) を返す。同じか、今の状態が None (まだ試していない) なら None"""
    if rl_loaded is None:
        return None
    cur = dict(runtime or {})
    if cur.get("rl_loaded") is rl_loaded:
        return None
    cur["rl_loaded"] = rl_loaded
    cur["rl_error"] = None if rl_loaded else "load_failed (sb3_contrib / torch / file)"
    return cur


def digest(doc: dict, n: int = 12) -> str:
    """版の要約の digest (純粋): 版の識別 (identity_of) だけから計算する。時刻と実行状態 (読み込みの成否・退避理由) は入らない"""
    ident = identity_of(doc)
    return hashlib.sha256(json.dumps(ident, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:n]


def summarize_versions(designated_package: Optional[str], selection_path: Optional[str], selection_sha: Optional[str],
                       selection_package: Optional[str], package_species: Optional[list], my_species: Optional[list],
                       rl_path: Optional[str], rl_sha: Optional[str], rl_loaded: Optional[bool], rl_error: Optional[str],
                       team_sha: Optional[str], git: Optional[str], dex_sha: Optional[str], effects_sha: Optional[str],
                       features: Optional[str], *, embedding_sha: Optional[str] = None, usage_snapshot=None,
                       usage_snapshot_source: Optional[str] = None, scoring: Optional[dict] = None) -> dict:
    """版の要約 (純粋)。退避の理由 (fallback_reason) をここで決める:
      - ラベルが無い → None (退避ではない。配布版が本来の経路)
      - ラベルはあるが Package のモデルが使われていない → "package_model_missing" (モデルのファイルが無い) か
        "party_not_in_package" (登録パーティが Package の 6 体に含まれない)
      - 選出モデルのファイルが無い → "selection_model_missing" (規則への退避)
    2026-10-20: embedding_sha (種族の埋め込み)・usage_snapshot (使用率の snapshot id) を rules に、採点設定 scoring を "scoring" に、
    実行状態を "runtime" に足す (欄の追加だけ)。version_id は identity_of の digest"""
    fallback = None
    if designated_package and selection_package != designated_package:
        if package_species and my_species and not set(my_species) <= set(package_species):
            fallback = "party_not_in_package"
        else:
            fallback = "package_model_missing"
    if selection_path and selection_sha is None:
        fallback = (fallback + "+" if fallback else "") + "selection_model_missing"
    source = None
    if selection_path:
        source = ("registered" if str(selection_package or "").startswith("registered:") else "package" if selection_package
                  else "deployed")
    doc = {
        "designated_package": designated_package,
        # package: 試用 Package の id / registered:<6 体の鍵> (登録チーム向け、2026-10-06) / None (配布版)。source はその区分
        "selection_model": {"package": selection_package, "path": selection_path, "sha256": selection_sha,
                            "fallback_reason": fallback, "source": source},
        "action_policy": {"path": rl_path, "sha256": rl_sha, "loaded": rl_loaded, "error": rl_error},
        "team": {"sha256": team_sha, "species": sorted(str(s) for s in (my_species or []))},
        "rules": {"git_commit": git, "dex_sha256": dex_sha, "effects_sha256": effects_sha, "selection_features": features,
                  "embedding_sha256": embedding_sha, "usage_snapshot": usage_snapshot},
        "scoring": {k: (scoring or {}).get(k) for k in SCORING_IDENTITY_KEYS},
    }
    doc["runtime"] = dict(runtime_of(doc), usage_snapshot_source=usage_snapshot_source)
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
    snap, snap_src = usage_snapshot_info()
    doc = summarize_versions(designated, sel_path, sha256_file(sel_path) if sel_path else None, sel_pkg, pkg_species, my_species,
                             rl_path, rl_sha, rl_loaded, rl_err, team_sha, git,
                             sha256_file(REPO / "advisor" / "data" / "dex.json"), sha256_file(REPO / "advisor" / "data" / "move_effects.json"),
                             features, embedding_sha=sha256_file(EMBEDDING_PATH), usage_snapshot=snap,
                             usage_snapshot_source=snap_src, scoring=scoring_identity())
    doc["collected_at"] = round(now, 2)
    _CACHE.update(key=key, value=doc, t=now)
    return doc


def usage_snapshot_info() -> tuple:
    """使用率の snapshot id と出どころ。型予測 (advisor.sets の SetPredictor) が接続済みならその id ("predictor")、まだなら
    接続したときに選ぶ id (DB の最新の snapshot、同じ SQL) を別の接続で読む ("db_latest")。読めなければ (None, None)。
    1 戦目は型予測がまだ接続していないので、接続済みの値だけを使うと 1 戦目だけ版が違って見える (RL 方策と同じ問題) ため"""
    try:
        import advisor.sets as _sets
        p = getattr(_sets, "_predictor", None)
        if p is not None and getattr(p, "_snapshot_id", None) is not None:
            return p._snapshot_id, "predictor"
        # 版を明示しているとき (advisor.data_pin、2026-10-23): その DB・snapshot (無ければ記録しない = (None, None))
        from advisor.data_pin import connect_usage, resolve_snapshot, usage_source
        src = usage_source()
        if src["pinned"]:
            conn = connect_usage(src["db"], True)
            try:
                return resolve_snapshot(conn, src["snapshot_id"], True), "pinned"
            finally:
                conn.close()
        db = Path(getattr(p, "db_path", None) or _sets.DB_PATH)
        if not db.exists():
            return None, None
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)   # この関数の中だけで使って閉じる (スレッド間で共有しない)
        try:
            row = conn.execute("SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        return (row[0], "db_latest") if row else (None, None)
    except Exception:
        return None, None


def scoring_identity() -> dict:
    """採点設定 (advice_key.scoring_settings と鍵の版 config.ADVICE_KEY_VERSION)。読めない欄は None"""
    out: dict = {}
    try:
        from advice_key import scoring_settings
        out.update(scoring_settings())
    except Exception:
        pass
    try:
        from champions_agent import config as C
        out["key_version"] = getattr(C, "ADVICE_KEY_VERSION", None)
    except Exception:
        out["key_version"] = None
    return {k: out.get(k) for k in SCORING_IDENTITY_KEYS}


def rl_loaded_now() -> Optional[bool]:
    """助言の時点で行動方策が読み込めているか (まだ試していなければ None)"""
    try:
        from advisor import rl_bridge as RB
        return None if not RB._model_tried else (RB._model is not None)
    except Exception:
        return None


def version_split_of(records) -> dict:
    """1 本の対戦ログの行から、版の識別と実行状態を分けて読み出す (純粋。2026-10-20)。旧版の version 行も読める。
    戻り値 {"version_id": 記録された version_id, "identity_digest": identity_of の digest (旧版の行でも同じ規則で計算し直した値),
    "identity": identity_of, "runtime": version 行の実行状態 (runtime_of), "runtime_rows": runtime 行の実行状態の列,
    "rl_loaded_advice": 対戦の advice 行の policy.rl_loaded の値の列 (重複を除いた出現順)}。version 行が無ければ各欄 None / 空"""
    ver = next((r for r in records or [] if r.get("type") == "version"), None)
    rt_rows = [dict(r.get("runtime") or {}) for r in records or [] if r.get("type") == "runtime"]
    seen: list = []
    for r in records or []:
        if r.get("type") == "advice" and r.get("kind") == "battle":
            v = (r.get("policy") or {}).get("rl_loaded")
            if v not in seen:
                seen.append(v)
    if ver is None:
        return {"version_id": None, "identity_digest": None, "identity": None, "runtime": None, "runtime_rows": rt_rows,
                "rl_loaded_advice": seen}
    return {"version_id": ver.get("version_id"), "identity_digest": digest(ver), "identity": identity_of(ver),
            "runtime": runtime_of(ver), "runtime_rows": rt_rows, "rl_loaded_advice": seen}
