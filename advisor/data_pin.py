"""助言が読む外部データ (使用率 DB の snapshot・実戦バンク) の「使う版」の明示 (2026-10-23、レビュー 2026-10-19 §2)。

型予測 (advisor.sets.SetPredictor)・タイプからの候補 (advisor.infer.TypeInference)・チャンピオンズの種の判定
(advisor.team_advice.champions_usable)・実戦バンク (advisor.real_prior)・相手の型推定 (advisor.ev_infer、2026-10-30。版を明示したときは
その snapshot だけ) は、ここで決めた版を読む。

版の決め方 (上が優先):
  1. set_override(...) (同じプロセスの中の再生。tools.real_accept / tools.advice_replay の --data-mode restore)
  2. 環境変数 (config.USAGE_DB_ENV / USAGE_SNAPSHOT_ENV / REAL_BANK_ENV / REAL_BANK_SHA_ENV。正式比較の子プロセス)
  3. 既定 = 従来どおり (使用率 DB の最新 snapshot・config.REAL_BANK_PATH)
版を明示したとき (pinned) は、その版が無い・sha16 が違えば InputsPinError で止める (最新へ黙って退避しない)。
TypeInference / champions_usable の参照範囲は「指定 snapshot id 以下」(advisor.infer)。
登録チーム (2026-10-23): set_override(team_registry=...) は再生の間だけ advisor.my_team._load() を当時の登録 (version 行の
team.registry) に差し替える (環境変数での指定は無い)。clear_override で外す。

実機 (アドバイザーの常駐、2026-10-23 計画書 §8.49): config/advice.local.env の ADVICE_USAGE_SNAPSHOT / ADVICE_REAL_BANK /
ADVICE_REAL_BANK_SHA16 を scripts/advice_env.sh が uvicorn にだけ export する。読み手はどれも呼ばれたときに os.environ を読む
(import 時には読まない) ので、server の import 順に依らずに効く。起動時の検査は tools.advice_mode.data_pin_startup_check
(verify_spec: 指定 snapshot が DB にない・写しが無い・sha16 が違う・元のバンクそのものなら起動しない)。

check_runtime() は子プロセスの起動時の照合 (tools.check_advisor_player が対戦の前に呼ぶ)。純粋な部分 (spec_from_env) と
照合 (check_runtime) は tests/test_inputs_pin.py で確かめる。

2026-10-31 (KNOWN_ISSUES E-inh-4、レビュー 2026-10-31 判断 4):
  - 使用率を読む残りの 2 経路 (advisor.gimmick._stone_usage = 相手のメガ石の事前分布、tools.evolve_teams._usage_alternatives =
    登録の本文の配分・性格の補完) もここの版を読む。reset_caches はその読み込み済みの値も捨てる。
  - モデルの実体の固定: config.RL_POLICY_PIN_ENV / RL_POLICY_SHA_ENV (RL 方策) と SELECTION_MODEL_PIN_ENV / SELECTION_MODEL_SHA_ENV
    (助言の選出モデル)、または set_model_override。明示したときはそのファイルだけを読み、無い・sha16 が違えば InputsPinError
    (model_source / check_model_file。読み手は advisor.rl_bridge と champions_agent.agent.selection_dispatch.advisor_model_path)。
    verify_spec / check_runtime は spec にモデルの欄があれば一緒に確かめる (実機の起動時は tools.advice_mode.model_pin_startup_check)。

2026-10-31 版の固定の bundle (追加レビュー判断 2・4、advisor/pin_bundle.py): 環境変数 config.PIN_BUNDLE_ENV (ADVICE_PIN_BUNDLE) があれば、
使用率 DB・snapshot・バンク・RL 方策・選出モデルの版を bundle の manifest から埋める (_merged / model_source)。個別の鍵と一緒に
書いたときは値が一致しなければ InputsPinError。使用率 DB は bundle の sha256 を照合してから読む (usage_source)。set_override /
set_model_override (同じプロセスの中の再生・テスト) は bundle より優先する。check_runtime / verify_spec (spec の "bundle") は
bundle 全体 (全ファイルの sha256・特徴量の版・登録) を確かめる。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Optional

from champions_agent.config import (INPUTS_PIN_REASON, REAL_BANK_ENV, REAL_BANK_PATH, REAL_BANK_SHA_ENV, RL_POLICY_PIN_ENV,
                                    RL_POLICY_SHA_ENV, SELECTION_MODEL_PIN_ENV, SELECTION_MODEL_SHA_ENV, USAGE_DB_ENV,
                                    USAGE_SNAPSHOT_ENV)

REPO = Path(__file__).resolve().parent.parent
DEFAULT_USAGE_DB = REPO / "champions_agent" / "data" / "db" / "champions.sqlite3"   # advisor.sets.DB_PATH と同じ


class InputsPinError(RuntimeError):
    """明示した版のデータが無い・sha16 が違う (最新へ退避しないで止める)"""


def pin_error_result(err) -> dict:
    """InputsPinError を助言の結果の形にする (純粋。2026-10-31 E-inputs-3)。通常の「推定なし」・評価エラーと分けるため reason は
    config.INPUTS_PIN_REASON。第一候補 (best / actions) は持たない"""
    return {"ok": False, "reason": INPUTS_PIN_REASON, "error": str(err)}


def is_pin_error_result(result) -> bool:
    """助言の結果が固定入力の異常 (pin_error_result の形) か (純粋)"""
    return isinstance(result, dict) and result.get("ok") is False and result.get("reason") == INPUTS_PIN_REASON


# 同じプロセスの中の上書き (None の欄は環境変数・既定に任せる)。bank_blocked = 理由の文字列 (バンクを読もうとしたら止める)
_override: dict = {}


def spec_from_env(environ: Optional[dict] = None) -> dict:
    """環境変数から使う版 (純粋)。{"usage_db", "usage_snapshot", "bank_path", "bank_sha16"} (未設定は None)。
    snapshot id が整数でなければ InputsPinError"""
    env = os.environ if environ is None else environ
    snap_raw = (env.get(USAGE_SNAPSHOT_ENV) or "").strip()
    try:
        snap = int(snap_raw) if snap_raw else None
    except ValueError:
        raise InputsPinError(f"{USAGE_SNAPSHOT_ENV}={snap_raw!r} が snapshot id (整数) でない")
    return {"usage_db": (env.get(USAGE_DB_ENV) or "").strip() or None, "usage_snapshot": snap,
            "bank_path": (env.get(REAL_BANK_ENV) or "").strip() or None,
            "bank_sha16": (env.get(REAL_BANK_SHA_ENV) or "").strip() or None}


def env_for(usage_db=None, usage_snapshot=None, bank_path=None, bank_sha16=None) -> dict:
    """子プロセスに渡す環境変数 (純粋。None の欄は入れない)"""
    out = {}
    if usage_db is not None:
        out[USAGE_DB_ENV] = str(usage_db)
    if usage_snapshot is not None:
        out[USAGE_SNAPSHOT_ENV] = str(int(usage_snapshot))
    if bank_path is not None:
        out[REAL_BANK_ENV] = str(bank_path)
    if bank_sha16 is not None:
        out[REAL_BANK_SHA_ENV] = str(bank_sha16)
    return out


def _bundle_spec() -> Optional[dict]:
    """bundle から埋めた版 (advisor.pin_bundle.effective_spec)。bundle が無ければ None。矛盾・不正は InputsPinError"""
    from advisor import pin_bundle as PB
    if not PB.active():
        return None
    return PB.effective_spec()


def _merged() -> dict:
    spec = spec_from_env()
    b = _bundle_spec()
    if b is not None:
        spec.update({k: b[k] for k in ("usage_db", "usage_snapshot", "bank_path", "bank_sha16")})
    spec["_bundle"] = b is not None
    for k in ("usage_db", "usage_snapshot", "bank_path", "bank_sha16"):
        if _override.get(k) is not None:
            spec[k] = _override[k]
    spec["bank_blocked"] = _override.get("bank_blocked")
    return spec


def usage_source() -> dict:
    """使用率の読み先: {"db": Path, "snapshot_id": 明示した id or None (最新), "pinned": 版を明示したか}。
    DB だけ明示して snapshot を明示しない場合も pinned (その DB の最新を使う。bundle は 1 つの snapshot しか持たない)"""
    s = _merged()
    if s.get("_bundle") and _override.get("usage_db") is None:
        # bundle の使用率 DB は sha256 を照合してから読む (stat が変わらなければ計算し直さない)
        from advisor import pin_bundle as PB
        PB.file_path("usage_db")
    db = Path(s["usage_db"]) if s["usage_db"] else DEFAULT_USAGE_DB
    return {"db": db, "snapshot_id": s["usage_snapshot"], "pinned": bool(s["usage_db"] or s["usage_snapshot"] is not None)}


def bank_source() -> dict:
    """実戦バンクの読み先: {"path": Path, "sha16": 期待 sha16 or None, "pinned": 版を明示したか, "blocked": 読ませない理由 or None}"""
    s = _merged()
    p = Path(s["bank_path"]) if s["bank_path"] else REPO / REAL_BANK_PATH
    return {"path": p, "sha16": s["bank_sha16"], "pinned": bool(s["bank_path"] or s["bank_sha16"]),
            "blocked": s.get("bank_blocked")}


def connect_usage(db: Path, pinned: bool, check_same_thread: bool = True) -> Optional[sqlite3.Connection]:
    """使用率 DB を開く。版を明示したときは読み取り専用で開き、無ければ InputsPinError (明示していなければ None)"""
    if not Path(db).exists():
        if pinned:
            raise InputsPinError(f"使用率 DB {db} が無い")
        return None
    if pinned:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=check_same_thread)
    return sqlite3.connect(str(db), check_same_thread=check_same_thread)


def resolve_snapshot(conn: sqlite3.Connection, snapshot_id: Optional[int], pinned: bool) -> Optional[int]:
    """使う snapshot id。明示があればそれが DB にあることを確かめ (無ければ InputsPinError)、無ければ最新"""
    if snapshot_id is not None:
        row = conn.execute("SELECT id FROM usage_snapshot WHERE id=?", (int(snapshot_id),)).fetchone()
        if row is None:
            raise InputsPinError(f"使用率の snapshot {snapshot_id} が DB に無い")
        return int(row[0])
    row = conn.execute("SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
    if row is None and pinned:
        raise InputsPinError("明示した使用率 DB に snapshot が無い")
    return int(row[0]) if row else None


def sha16_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def read_bank_bytes(src: dict) -> Optional[bytes]:
    """バンクの中身 (副作用: 読み取り)。blocked なら InputsPinError、版を明示していて無い・sha16 が違えば InputsPinError、
    明示していなくて無ければ None"""
    if src.get("blocked"):
        raise InputsPinError(f"実戦バンクを読めない (復元できない版: {src['blocked']})")
    p = Path(src["path"])
    try:
        raw = p.read_bytes()
    except OSError:
        if src.get("pinned"):
            raise InputsPinError(f"実戦バンク {p} が無い")
        return None
    if src.get("sha16") and sha16_bytes(raw) != src["sha16"]:
        raise InputsPinError(f"実戦バンク {p} の sha16 {sha16_bytes(raw)} が指定 {src['sha16']} と違う")
    return raw


def set_override(usage_db=None, usage_snapshot=None, bank_path=None, bank_sha16=None, bank_blocked=None,
                 team_registry: Optional[dict] = None) -> None:
    """同じプロセスの中で使う版を差し替え、読み込み済みのキャッシュを捨てる (再生の --data-mode restore)。
    team_registry (2026-10-23) = 当時の登録 (version 行の team.registry)。dict なら advisor.my_team._load() をそれに差し替え、
    None なら差し替えない (config/my_team.json を読む)"""
    from advisor import my_team as MT
    _override.clear()
    _override.update({"usage_db": str(usage_db) if usage_db is not None else None,
                      "usage_snapshot": int(usage_snapshot) if usage_snapshot is not None else None,
                      "bank_path": str(bank_path) if bank_path is not None else None,
                      "bank_sha16": bank_sha16, "bank_blocked": bank_blocked})
    if team_registry is not None:
        MT.set_registry_override(team_registry)
    else:
        MT.clear_registry_override()
    reset_caches()


def clear_override() -> None:
    from advisor import my_team as MT
    _override.clear()
    MT.clear_registry_override()
    reset_caches()


def reset_caches() -> None:
    """版を読むモジュールの読み込み済みの値を捨てる (次に読むときに今の版で作り直す)"""
    try:
        import advisor.sets as S
        S._predictor = None
        S.exclusive_form_for_move.cache_clear()
    except Exception:
        pass
    try:
        import advisor.infer as I
        I._inference = None
    except Exception:
        pass
    try:
        import advisor.team_advice as T
        T._CHAMPIONS_IDS = None
    except Exception:
        pass
    try:
        import advisor.real_prior as R
        R._cache.update({"path": None, "mtime": None, "bank": None})
        R._ver_cache.update({"path": None, "mtime": None, "info": None})
    except Exception:
        pass
    try:
        from advisor import pin_bundle as PB
        PB.reset()
    except Exception:
        pass
    try:
        # 型推定のトラッカーの推定器 (版ごとの仮説。2026-10-30)。読み込まれていなければ持っている値も無い
        import sys
        E = sys.modules.get("advisor.ev_infer")
        if E is not None and E._tracker is not None:
            E._tracker.drop_estimators()
    except Exception:
        pass
    try:
        # 石の使用率 (相手のメガの事前分布) と登録の補完の使用率 (2026-10-31 E-inh-4)。読み込まれていなければ持っている値も無い
        import sys
        G = sys.modules.get("advisor.gimmick")
        if G is not None:
            G._stone_usage_default.cache_clear()
            G._stone_usage_pinned.cache_clear()
        EV = sys.modules.get("tools.evolve_teams")
        if EV is not None:
            EV._usage_cache = None
    except Exception:
        pass


def verify_spec(spec: dict, default_db: Path = DEFAULT_USAGE_DB, live_bank: Optional[Path] = None,
                cwd: Optional[Path] = None) -> dict:
    """spec (spec_from_env の形) で明示した版が読めることを確かめる (副作用: 読み取り。2026-10-23 実機の起動時の検査)。
    - 使用率: snapshot id (または DB) を明示していれば、その DB (usage_db、無ければ default_db) を読み取り専用で開き、指定 snapshot が
      DB にあることを確かめる (無ければ InputsPinError)
    - バンク: パスを明示していれば、そのファイルがあり (相対パスは cwd から)、sha16 を明示していれば一致すること。live_bank を渡せば、
      それと同じファイル (写しではない = 対戦ごとに書き換わる) なら InputsPinError
    戻り値 {"usage_db", "usage_snapshot", "bank_path", "bank_sha16"} (明示していない欄は None。bank_sha16 は実際の sha16)"""
    base = Path(cwd) if cwd is not None else Path.cwd()
    out = {"usage_db": None, "usage_snapshot": None, "bank_path": None, "bank_sha16": None}
    bundle = None
    if spec.get("bundle"):
        # 版の固定の bundle (2026-10-31): bundle 全体を確かめ (個別の欄との整合を含む)、個別の欄を bundle の値で埋めてから下の検査を通す
        from advisor import pin_bundle as PB
        bd = Path(spec["bundle"])
        if not bd.is_absolute():
            bd = base / bd
        explicit = {k: spec.get(k) for k in PB.SPEC_KEYS}
        for k in ("usage_db", "bank_path") + tuple(MODEL_KINDS):
            if explicit.get(k) and not Path(explicit[k]).is_absolute():
                explicit[k] = str(base / explicit[k])
        bundle = PB.verify_bundle(bd, explicit=explicit)
        spec = dict(spec)
        spec.update(bundle["values"])
    if spec.get("usage_db") or spec.get("usage_snapshot") is not None:
        db = Path(spec["usage_db"]) if spec.get("usage_db") else Path(default_db)
        if not db.is_absolute():
            db = base / db
        conn = connect_usage(db, True)
        try:
            out.update(usage_db=str(db), usage_snapshot=resolve_snapshot(conn, spec.get("usage_snapshot"), True))
        finally:
            conn.close()
    if spec.get("bank_path"):
        p = Path(spec["bank_path"])
        if not p.is_absolute():
            p = base / p
        if live_bank is not None and p.exists() and Path(live_bank).exists() and os.path.samefile(p, live_bank):
            raise InputsPinError(f"実戦バンク {p} は対戦ごとに書き換わる元のファイル (写しを指定する: python -m tools.advice_mode --freeze-data)")
        raw = read_bank_bytes({"path": p, "sha16": spec.get("bank_sha16"), "pinned": True, "blocked": None})
        out.update(bank_path=str(p), bank_sha16=sha16_bytes(raw))
    if any(spec.get(k) for k in MODEL_SPEC_KEYS):
        # モデルの実体の固定 (2026-10-31)。spec にモデルの欄があるときだけ確かめ、戻り値にも足す
        out.update(verify_model_spec(spec, cwd=base))
    if bundle is not None:
        out["bundle"] = {k: bundle[k] for k in ("dir", "manifest_sha16", "files", "usage_snapshot", "selection", "set_diffs")}
    return out


def check_runtime() -> Optional[dict]:
    """版を明示していれば、その版が読めることを確かめる (子プロセスの起動時)。明示が無ければ None。
    戻り値 {"usage_db", "usage_snapshot", "bank_path", "bank_sha16"} (実際に確かめた値)。合わなければ InputsPinError"""
    from advisor import pin_bundle as PB
    bundle = PB.verify_bundle() if PB.active() else None      # bundle 全体 (2026-10-31): 全ファイルの sha256・個別の鍵との整合
    us, bs = usage_source(), bank_source()
    ms = {k: model_source(k) for k in MODEL_KINDS}
    if not us["pinned"] and not bs["pinned"] and not any(m["pinned"] for m in ms.values()):
        return None
    out = {"usage_db": None, "usage_snapshot": None, "bank_path": None, "bank_sha16": None}
    if bundle is not None:
        out["bundle"] = {k: bundle[k] for k in ("dir", "manifest_sha16", "files", "usage_snapshot", "selection", "set_diffs")}
    if us["pinned"]:
        conn = connect_usage(us["db"], True)
        try:
            out.update(usage_db=str(us["db"]), usage_snapshot=resolve_snapshot(conn, us["snapshot_id"], True))
        finally:
            conn.close()
    if bs["pinned"]:
        raw = read_bank_bytes(bs)
        out.update(bank_path=str(bs["path"]), bank_sha16=sha16_bytes(raw))
    for kind, m in ms.items():
        if m["pinned"]:        # モデルの実体の固定 (2026-10-31): 明示したときだけ欄を足す
            out[kind] = str(m["path"]) if m["path"] is not None else None
            out[kind + "_sha16"] = check_model_file(kind, m["path"], m["sha16"])
    return out


# ------------------------------------------------------------------ モデルの実体の固定 (2026-10-31 E-inh-4)
# kind → (パスの環境変数, sha16 の環境変数, 表示名)
MODEL_KINDS = {"rl_policy": (RL_POLICY_PIN_ENV, RL_POLICY_SHA_ENV, "RL 方策"),
               "selection_model": (SELECTION_MODEL_PIN_ENV, SELECTION_MODEL_SHA_ENV, "選出モデル")}
MODEL_SPEC_KEYS = tuple(k for kind in MODEL_KINDS for k in (kind, kind + "_sha16"))
# 同じプロセスの中の上書き (テスト・再生用。None の欄は環境変数に任せる)
_model_override: dict = {}


def model_spec_from_env(environ: Optional[dict] = None) -> dict:
    """環境変数からモデルの固定 (純粋)。{"rl_policy", "rl_policy_sha16", "selection_model", "selection_model_sha16"} (未設定は None)。
    sha16 は小文字にする"""
    env = os.environ if environ is None else environ
    out = {}
    for kind, (path_env, sha_env, _label) in MODEL_KINDS.items():
        out[kind] = (env.get(path_env) or "").strip() or None
        out[kind + "_sha16"] = ((env.get(sha_env) or "").strip().lower() or None)
    return out


def model_env_for(rl_policy=None, rl_policy_sha16=None, selection_model=None, selection_model_sha16=None) -> dict:
    """子プロセスに渡すモデルの固定の環境変数 (純粋。None の欄は入れない)"""
    vals = {"rl_policy": rl_policy, "rl_policy_sha16": rl_policy_sha16, "selection_model": selection_model,
            "selection_model_sha16": selection_model_sha16}
    out = {}
    for kind, (path_env, sha_env, _label) in MODEL_KINDS.items():
        if vals[kind] is not None:
            out[path_env] = str(vals[kind])
        if vals[kind + "_sha16"] is not None:
            out[sha_env] = str(vals[kind + "_sha16"])
    return out


def set_model_override(rl_policy=None, rl_policy_sha16=None, selection_model=None, selection_model_sha16=None) -> None:
    """同じプロセスの中でモデルの固定を差し替え、読み込み済みのモデルを捨てる (テスト・再生用)"""
    _model_override.clear()
    _model_override.update({"rl_policy": str(rl_policy) if rl_policy is not None else None,
                            "rl_policy_sha16": rl_policy_sha16.lower() if rl_policy_sha16 else None,
                            "selection_model": str(selection_model) if selection_model is not None else None,
                            "selection_model_sha16": selection_model_sha16.lower() if selection_model_sha16 else None})
    reset_model_caches()


def clear_model_override() -> None:
    _model_override.clear()
    reset_model_caches()


def reset_model_caches() -> None:
    """読み込み済みの RL 方策を捨てる (次に読むときに今の固定で読み直す)。選出モデルはパスごとのキャッシュなので捨てない"""
    import sys
    RB = sys.modules.get("advisor.rl_bridge")
    if RB is not None and hasattr(RB, "reset_model"):
        RB.reset_model()


def model_source(kind: str) -> dict:
    """モデルの読み先: {"path": Path or None, "sha16": 期待 sha16 or None, "pinned": 明示したか, "label": 表示名}。
    パスか sha16 のどちらかがあれば pinned (sha16 だけなら check_model_file が止める)"""
    if kind not in MODEL_KINDS:
        raise ValueError(f"モデルの種類 {kind} を知らない")
    spec = model_spec_from_env()
    b = _bundle_spec()
    if b is not None:
        spec.update({kind: b[kind], kind + "_sha16": b[kind + "_sha16"]})
    for k in (kind, kind + "_sha16"):
        if _model_override.get(k) is not None:
            spec[k] = _model_override[k]
    path, sha = spec[kind], spec[kind + "_sha16"]
    return {"path": Path(path) if path else None, "sha16": sha, "pinned": bool(path or sha), "label": MODEL_KINDS[kind][2]}


def check_model_file(kind: str, path, sha16: Optional[str]) -> str:
    """明示したモデルのファイルを確かめ、実際の sha16 を返す (副作用: 読み取り)。パスが無い・ファイルが無い・sha16 が違えば
    InputsPinError (別のファイルへ退避しない)"""
    label = MODEL_KINDS[kind][2]
    if path is None:
        raise InputsPinError(f"{label} の sha16 だけが指定されている (パス {MODEL_KINDS[kind][0]} と一緒に指定する)")
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        raise InputsPinError(f"明示した{label} {p} が無い (別のファイルへ退避しない)")
    got = sha16_bytes(raw)
    if sha16 and got != str(sha16).lower():
        raise InputsPinError(f"明示した{label} {p} の sha16 {got} が指定 {sha16} と違う")
    return got


def verify_model_spec(spec: dict, cwd: Optional[Path] = None) -> dict:
    """spec (model_spec_from_env の形) で明示したモデルが読めることを確かめる (副作用: 読み取り。起動時の検査)。相対パスは cwd から。
    戻り値は明示した種類だけ {kind: 絶対パス, kind_sha16: 実際の sha16}。合わなければ InputsPinError"""
    base = Path(cwd) if cwd is not None else Path.cwd()
    out = {}
    for kind in MODEL_KINDS:
        path, sha = spec.get(kind), spec.get(kind + "_sha16")
        if not path and not sha:
            continue
        p = None
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = base / p
        out[kind] = str(p) if p is not None else None
        out[kind + "_sha16"] = check_model_file(kind, p, sha)
    return out
