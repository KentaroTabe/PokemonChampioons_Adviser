"""助言が読む外部データ (使用率 DB の snapshot・実戦バンク) の「使う版」の明示 (2026-10-23、レビュー 2026-10-19 §2)。

型予測 (advisor.sets.SetPredictor)・タイプからの候補 (advisor.infer.TypeInference)・チャンピオンズの種の判定
(advisor.team_advice.champions_usable)・実戦バンク (advisor.real_prior) は、ここで決めた版を読む。

版の決め方 (上が優先):
  1. set_override(...) (同じプロセスの中の再生。tools.real_accept / tools.advice_replay の --data-mode restore)
  2. 環境変数 (config.USAGE_DB_ENV / USAGE_SNAPSHOT_ENV / REAL_BANK_ENV / REAL_BANK_SHA_ENV。正式比較の子プロセス)
  3. 既定 = 従来どおり (使用率 DB の最新 snapshot・config.REAL_BANK_PATH)
版を明示したとき (pinned) は、その版が無い・sha16 が違えば InputsPinError で止める (最新へ黙って退避しない)。
TypeInference / champions_usable の参照範囲は「指定 snapshot id 以下」(advisor.infer)。
登録チーム (2026-10-23): set_override(team_registry=...) は再生の間だけ advisor.my_team._load() を当時の登録 (version 行の
team.registry) に差し替える (環境変数での指定は無い)。clear_override で外す。

check_runtime() は子プロセスの起動時の照合 (tools.check_advisor_player が対戦の前に呼ぶ)。純粋な部分 (spec_from_env) と
照合 (check_runtime) は tests/test_inputs_pin.py で確かめる。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Optional

from champions_agent.config import REAL_BANK_ENV, REAL_BANK_PATH, REAL_BANK_SHA_ENV, USAGE_DB_ENV, USAGE_SNAPSHOT_ENV

REPO = Path(__file__).resolve().parent.parent
DEFAULT_USAGE_DB = REPO / "champions_agent" / "data" / "db" / "champions.sqlite3"   # advisor.sets.DB_PATH と同じ


class InputsPinError(RuntimeError):
    """明示した版のデータが無い・sha16 が違う (最新へ退避しないで止める)"""


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


def _merged() -> dict:
    spec = spec_from_env()
    for k in ("usage_db", "usage_snapshot", "bank_path", "bank_sha16"):
        if _override.get(k) is not None:
            spec[k] = _override[k]
    spec["bank_blocked"] = _override.get("bank_blocked")
    return spec


def usage_source() -> dict:
    """使用率の読み先: {"db": Path, "snapshot_id": 明示した id or None (最新), "pinned": 版を明示したか}。
    DB だけ明示して snapshot を明示しない場合も pinned (その DB の最新を使う。bundle は 1 つの snapshot しか持たない)"""
    s = _merged()
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


def check_runtime() -> Optional[dict]:
    """版を明示していれば、その版が読めることを確かめる (子プロセスの起動時)。明示が無ければ None。
    戻り値 {"usage_db", "usage_snapshot", "bank_path", "bank_sha16"} (実際に確かめた値)。合わなければ InputsPinError"""
    us, bs = usage_source(), bank_source()
    if not us["pinned"] and not bs["pinned"]:
        return None
    out = {"usage_db": None, "usage_snapshot": None, "bank_path": None, "bank_sha16": None}
    if us["pinned"]:
        conn = connect_usage(us["db"], True)
        try:
            out.update(usage_db=str(us["db"]), usage_snapshot=resolve_snapshot(conn, us["snapshot_id"], True))
        finally:
            conn.close()
    if bs["pinned"]:
        raw = read_bank_bytes(bs)
        out.update(bank_path=str(bs["path"]), bank_sha16=sha16_bytes(raw))
    return out
