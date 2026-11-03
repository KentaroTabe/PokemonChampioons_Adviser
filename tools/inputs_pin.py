"""正式比較の入力の実体の固定 (2026-10-23、レビュー 2026-10-19 §2): 選出モデルの実体のピン、使用率 DB の bundle、実戦バンクの写し。

ピン (tools/usefulness_p1 の pin_dir) に置くもの:
  selection_model.pt / selection_model_emb.json / selection_model_meta.json  指定された選出 checkpoint とその dir の埋め込み・meta
                                                                             (一組。複製の前後で元の sha が変わらないことを確かめる)
  usage_bundle.sqlite3   使用率 DB から、指定 snapshot の予測に要る行だけを書き出した sqlite (BUNDLE_RULES)
  bank.json              実戦バンク (logs/real_opponents/bank.json) の写し
  inputs_manifest.json   上の各ファイルの出所・sha256・件数 (照合の基準)
子プロセスは --selection-model にピンの .pt を、環境変数 (advisor.data_pin.env_for) に bundle・snapshot id・バンク・その sha16 を受け取る。
照合 (verify_pin): ピンの各ファイルの sha256 が manifest と一致すること、選出の 3 つが同じ dir から来たこと。合わなければ起動しない・
復元は判定不能 (最新へ黙って退避しない)。

実戦ログの再生 (tools.real_accept / tools.advice_replay の --data-mode):
  restore (既定) = version 行の使用率 snapshot・登録チームと selection_record 行のバンクの sha16 を当時の版として使う
                  (restore_plan。無い・手元に無い・今の登録と違えば判定不能)。登録チームは version 行の team.registry
                  (助言エンジンが実際に読んだ登録 = advisor.my_team._load() の dict、2026-10-23 から) があればそれに差し替える
                  (記録の整合 team.registry_sha256 == registry の sha を確かめ、合わなければ判定不能)。無い旧ログは従来どおり
                  team.sha256 (registered_team() の本文の sha) を今の値と比べる
  current        = 今のデータ (DB の最新・今のバンク・今の登録) で過去の局面を評価し直す
どちらも使ったデータの版を出す。純粋な部分 (bundle の規則・manifest の照合・ログの版の読み出し・復元の計画) は tests/test_inputs_pin.py。

2026-10-31 版の固定の bundle (追加レビュー判断 2、advisor/pin_bundle.py): make_pin_bundle が実機の助言の入力一式 (RL 方策・選出モデル +
埋め込み + meta + 条件付きモデル・使用率 DB の抽出・バンク・登録) を 1 つの dir に置き、manifest.json (各ファイルの sha256・出所、
コード commit、設定、Showdown の版、選出モデルの種別と対応する構築、規制) を書く。使用率 DB は export_bundle(full_schema=True) で
元と同じスキーマの全表を作り、BUNDLE_RULES の表は同じ規則、snapshot_id を持つほかの表は対象 snapshot の行だけ (eq)、snapshot に
依らない表 (図鑑の表など) は全行を書き出す (読み手の ev_infer / evolve_teams / gimmick が spread_usage 等も読むため)。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from champions_agent.config import REAL_BANK_PATH

REPO = Path(__file__).resolve().parent.parent
# 元の場所 (dry-run を本体のデータで動かすときに差し替えられるようモジュール値にする)
SRC_USAGE_DB = REPO / "champions_agent" / "data" / "db" / "champions.sqlite3"
SRC_BANK = REPO / REAL_BANK_PATH

PIN_MODEL_FILE = "selection_model.pt"
PIN_EMB_FILE = "selection_model_emb.json"
PIN_META_FILE = "selection_model_meta.json"
BUNDLE_FILE = "usage_bundle.sqlite3"
BANK_FILE = "bank.json"
MANIFEST_FILE = "inputs_manifest.json"
MANIFEST_VERSION = 1
SELECTION_KEYS = ("selection_model", "selection_emb", "selection_meta")
SELECTION_FILES = {"selection_model": PIN_MODEL_FILE, "selection_emb": PIN_EMB_FILE, "selection_meta": PIN_META_FILE}

# bundle に書き出す行 (snapshot id = S)。読み手: advisor.sets (move/item/ability の S だけ)、advisor.infer (pokemon_usage の S 以下)、
# advisor.team_advice.champions_usable (pokemon_usage と teammate_usage の名前、チャンピオンズのスナップショットの S 以下)
#   le    = snapshot_id <= S (usage_snapshot は id <= S)
#   eq    = snapshot_id = S
#   pairs = チャンピオンズのスナップショット (team_advice._champions_filter、S 以下) の (pokemon_name, teammate_name) の組ごとに 1 行
#           (snapshot_id = 組が現れた最小の id、usage_percent = その最大。名前の集合しか読まないので champions_usable の結果は同じ)
BUNDLE_RULES = {"usage_snapshot": "le", "pokemon_usage": "le", "move_usage": "eq", "item_usage": "eq", "ability_usage": "eq",
                "teammate_usage": "pairs"}


# ------------------------------------------------------------------ 純粋
def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_entry(source: Optional[str], file: Optional[str], sha256: Optional[str], **extra) -> dict:
    """manifest の 1 ファイルの欄 (純粋)"""
    return {"source": source, "file": file, "sha256": sha256, "sha16": sha256[:16] if sha256 else None, **extra}


def bundle_where(table: str, snapshot_id: int, champions_filter: Optional[str] = None) -> str:
    """bundle に書き出す行の WHERE 句 (純粋)。pairs は champions_filter (src. を付けない列名の条件) を使う"""
    rule = BUNDLE_RULES[table]
    s = int(snapshot_id)
    if rule == "le":
        return f"id <= {s}" if table == "usage_snapshot" else f"snapshot_id <= {s}"
    if rule == "eq":
        return f"snapshot_id = {s}"
    if rule == "pairs":
        return champions_filter or f"snapshot_id <= {s}"
    raise ValueError(f"未知の規則 {rule} ({table})")


def child_env(record: Optional[dict], pin_dir) -> dict:
    """子プロセスに渡す環境変数 (純粋。advisor.data_pin.env_for)。record = 条件表の inputs_pin (manifest)。bundle が無ければ
    使用率の明示を入れない・バンクが無ければバンクの明示を入れない (呼び出し側の起動前検査が止める)"""
    from advisor.data_pin import env_for
    r = record or {}
    ub, bk = r.get("usage_bundle") or {}, r.get("real_bank") or {}
    pin = Path(pin_dir)
    return env_for(usage_db=(pin / ub["file"]) if ub.get("file") else None,
                   usage_snapshot=ub.get("snapshot_id") if ub.get("file") else None,
                   bank_path=(pin / bk["file"]) if bk.get("file") else None,
                   bank_sha16=bk.get("sha16") if bk.get("file") else None)


def check_manifest(record: Optional[dict], actual: dict, require_bank: bool = True) -> list:
    """manifest (record) と実際のピンのファイルの sha256 (actual: ファイル名 → sha256 or None) の照合 (純粋)。成り立たない理由の list。
    選出の 3 つ (モデル・埋め込み・meta) は一組: 同じ dir から来ていること、記録のあるものはピンにあって sha256 が一致すること。
    モデルと bundle は必須。バンクは require_bank (元にバンクが無かったときは record の real_bank が None で、照合しない)"""
    r = record or {}
    reasons = []
    if not r:
        return ["入力のピンの記録 (inputs_manifest.json / 条件表の inputs_pin) が無い"]
    sel = [r.get(k) for k in SELECTION_KEYS]
    if not (r.get("selection_model") or {}).get("sha256"):
        reasons.append("選出モデルの実体がピンの記録に無い")
    srcs = {str(Path(e["source"]).parent) for e in sel if e and e.get("source")}
    if len(srcs) > 1:
        reasons.append(f"選出モデル・埋め込み・meta の出所の dir が揃っていない ({sorted(srcs)})")
    entries = [(k, r.get(k)) for k in SELECTION_KEYS] + [("usage_bundle", r.get("usage_bundle")), ("real_bank", r.get("real_bank"))]
    for k, e in entries:
        if not e:
            if k == "usage_bundle":
                reasons.append("使用率の bundle がピンの記録に無い")
            elif k == "real_bank" and require_bank:
                reasons.append("実戦バンクの写しがピンの記録に無い")
            continue
        f = e.get("file")
        if not f:
            continue
        got = actual.get(f)
        if got is None:
            reasons.append(f"ピンに {f} が無い")
        elif e.get("sha256") and got != e["sha256"]:
            reasons.append(f"ピンの {f} の sha256 {got[:16]} が記録 {e['sha256'][:16]} と違う")
    ub = r.get("usage_bundle") or {}
    if ub and ub.get("snapshot_id") is None:
        reasons.append("使用率の bundle の snapshot id が記録に無い")
    return reasons


def child_inputs_reasons(record: Optional[dict], got_by_cond: dict) -> tuple:
    """子プロセスの結果 JSON の inputs_pin (起動時に照合した版) が、条件表の inputs_pin (ピンの manifest) と同じか (純粋)。
    got_by_cond = 条件名 → 結果 JSON の inputs_pin (JSON が無い条件は入れない。欄が無い・None は「記録なし」)。
    戻り値 (成り立たない理由の list, 記録の無い条件の list)"""
    r = record or {}
    want_snap = (r.get("usage_bundle") or {}).get("snapshot_id")
    want_bank = (r.get("real_bank") or {}).get("sha16")
    reasons, missing = [], []
    for c, got in got_by_cond.items():
        if not isinstance(got, dict):
            missing.append(c)
            continue
        if want_snap is not None and got.get("usage_snapshot") != want_snap:
            reasons.append(f"{c}: 子プロセスが読んだ使用率の snapshot {got.get('usage_snapshot')} がピンの {want_snap} と違う")
        if want_bank is not None and got.get("bank_sha16") != want_bank:
            reasons.append(f"{c}: 子プロセスが読んだバンク {got.get('bank_sha16')} がピンの {want_bank} と違う")
    return reasons, missing


def _first(xs: Iterable):
    for x in xs:
        return x
    return None


def log_data_versions(records: list) -> dict:
    """1 本の実戦ログの記録から、当時のデータの版 (純粋): {"usage_snapshot": [...], "team_sha16": [...], "bank_sha16": [...]}
    (出現順・重複なし)。使用率の snapshot と登録チームの sha は version 行 (rules.usage_snapshot / team.sha256、2026-10-20 より前の行に
    usage_snapshot は無い)、バンクは selection_record 行の opp_pick_pred.bank.sha256"""
    us, ts, bs = [], [], []
    for r in records or []:
        if r.get("type") == "version":
            u = (r.get("rules") or {}).get("usage_snapshot")
            t = (r.get("team") or {}).get("sha256")
            if u is not None and u not in us:
                us.append(u)
            if t and t not in ts:
                ts.append(t)
        elif r.get("type") == "selection_record":
            b = ((r.get("opp_pick_pred") or {}).get("bank") or {}).get("sha256")
            if b and b not in bs:
                bs.append(b)
    return {"usage_snapshot": us, "team_sha16": ts, "bank_sha16": bs}


def data_version_at(records: list, t: Optional[float]) -> dict:
    """時刻 t の時点のデータの版 (純粋): t 以前で最後の version 行の snapshot・登録チームの sha、t 以前で最後の selection_record 行の
    バンクの sha16 (t 以前に無ければログの最初のもの)。t が None ならログの最初の行。{"usage_snapshot", "team_sha16", "bank_sha16"}"""
    ver = sel = None
    first_ver = first_sel = None
    for r in records or []:
        typ = r.get("type")
        if typ not in ("version", "selection_record"):
            continue
        rt = r.get("t")
        before = t is not None and rt is not None and float(rt) <= float(t)
        if typ == "version":
            first_ver = first_ver or r
            if before:
                ver = r
        else:
            if ((r.get("opp_pick_pred") or {}).get("bank") or {}).get("sha256"):
                first_sel = first_sel or r
                if before:
                    sel = r
    ver = ver or first_ver
    sel = sel or first_sel
    return {"usage_snapshot": ((ver or {}).get("rules") or {}).get("usage_snapshot"),
            "team_sha16": ((ver or {}).get("team") or {}).get("sha256"),
            "bank_sha16": (((sel or {}).get("opp_pick_pred") or {}).get("bank") or {}).get("sha256")}


def _version_row_at(records: list, t: Optional[float]) -> Optional[dict]:
    """時刻 t の時点の version 行 (純粋。data_version_at と同じ規則: t 以前で最後、無ければログの最初、t が None なら最初)"""
    ver = first = None
    for r in records or []:
        if r.get("type") != "version":
            continue
        first = first or r
        rt = r.get("t")
        if t is not None and rt is not None and float(rt) <= float(t):
            ver = r
    return ver or first


def team_registry_at(records: list, t: Optional[float]) -> Optional[dict]:
    """時刻 t の時点の version 行の登録の記録 (純粋。2026-10-23): {"registry": team.registry, "registry_sha256": team.registry_sha256}。
    version 行が無い・どちらの欄も無い (2026-10-23 より前の記録) なら None"""
    team = (_version_row_at(records, t) or {}).get("team") or {}
    if "registry" not in team and "registry_sha256" not in team:
        return None
    return {"registry": team.get("registry"), "registry_sha256": team.get("registry_sha256")}


def check_team_registry(rec: Optional[dict]) -> tuple:
    """登録の記録の整合 (純粋): (使う登録 or None, 判定不能の理由 or None)。registry が dict で、registry_sha256 が
    advisor.my_team.registry_sha(registry) と一致するときだけ使う"""
    from advisor.my_team import registry_sha
    rec = rec or {}
    reg, rs = rec.get("registry"), rec.get("registry_sha256")
    if not isinstance(reg, dict):
        return None, "version 行の team.registry が無い・dict でない (記録の不整合)"
    actual = registry_sha(reg)
    if not rs or rs != actual:
        return None, f"version 行の team.registry_sha256 {rs} が registry の sha {actual} と違う (記録の不整合)"
    return reg, None


DATA_MODES = ("restore", "current")


def restore_plan(want: dict, snapshots: Iterable, team_sha16: Optional[str], banks: dict, mode: str = "restore",
                 latest_snapshot: Optional[int] = None, team_registry: Optional[dict] = None,
                 current_registry_sha16: Optional[str] = None) -> dict:
    """再生に使うデータの版の計画 (純粋)。want = data_version_at の当時の版、snapshots = 手元の DB にある snapshot id、
    team_sha16 = 今の登録チームの本文の sha16、banks = 手元のバンク {sha16: path}、latest_snapshot = DB の最新。
    restore: 当時の snapshot が DB にあり、当時の登録チームが今の登録と同じなら ok (使う snapshot = 当時の値)。どれかが無ければ ok False
             と理由 (判定不能)。バンクは当時の sha16 が手元にあればそれ、無ければ bank_blocked (読もうとしたら止める。対戦中の評価は
             バンクを読まないので、再生自体は止めない)
    current: 今のデータ (DB の最新・今のバンク・今の登録) で評価し直す。ok は常に True、当時の版と違う欄を notes に出す
    戻り値 {"mode", "ok", "reasons", "notes", "usage_snapshot" (使う id、current は None = 最新), "bank_path", "bank_sha16",
            "bank_blocked", "recorded": want, "used": {...}, "team_registry": 差し替える登録 or None}
    2026-10-23 登録チーム: team_registry = team_registry_at の記録 (version 行の team.registry と registry_sha256。無い旧ログは None)。
      restore: 記録があれば整合 (check_team_registry) を確かめ、合えばその登録を使う (今の登録とは比べない。used.team_registry_sha16)、
               合わなければ判定不能。記録が無ければ従来どおり team_sha16 (今の本文の sha) と比べる
      current: 今の登録を使う。記録の sha と current_registry_sha16 (今の登録の sha) が違えば notes に出す"""
    if mode not in DATA_MODES:
        raise ValueError(f"--data-mode は {DATA_MODES} のどれか ({mode})")
    want = dict(want or {})
    snaps = {int(s) for s in snapshots or []}
    reasons, notes = [], []
    us, ts, bs = want.get("usage_snapshot"), want.get("team_sha16"), want.get("bank_sha16")
    if mode == "current":
        cur_bank = _first(banks.items()) if banks else None
        if us is not None and latest_snapshot is not None and int(us) != int(latest_snapshot):
            notes.append(f"使用率の snapshot: 当時 {us} → 今 {latest_snapshot}")
        if ts and team_sha16 and ts != team_sha16:
            notes.append(f"登録チーム: 当時 {ts} → 今 {team_sha16}")
        if bs and cur_bank and bs != cur_bank[0]:
            notes.append(f"実戦バンク: 当時 {bs} → 今 {cur_bank[0]}")
        rec_rs = (team_registry or {}).get("registry_sha256")
        if rec_rs and current_registry_sha16 and rec_rs != current_registry_sha16:
            notes.append(f"登録 (my_team): 当時 {rec_rs} → 今 {current_registry_sha16}")
        return {"mode": mode, "ok": True, "reasons": [], "notes": notes, "usage_snapshot": None,
                "bank_path": None, "bank_sha16": None, "bank_blocked": None, "recorded": want,
                "used": {"usage_snapshot": latest_snapshot, "team_sha16": team_sha16,
                         "bank_sha16": cur_bank[0] if cur_bank else None}, "team_registry": None}
    if us is None:
        reasons.append("version 行に使用率の snapshot が無い (2026-10-20 より前の記録か version 行なし)")
    elif int(us) not in snaps:
        reasons.append(f"当時の使用率の snapshot {us} が手元の DB に無い")
    reg = None
    if team_registry is not None:
        # 当時の登録 (version 行の team.registry) を使う。今の登録・本文とは比べない (2026-10-23)
        reg, why = check_team_registry(team_registry)
        if why:
            reasons.append(why)
    elif not ts:
        reasons.append("version 行に登録チームの sha が無い")
    elif not team_sha16:
        reasons.append("今の登録チームの本文が作れない (照合できない)")
    elif ts != team_sha16:
        reasons.append(f"当時の登録チーム {ts} が今の登録 {team_sha16} と違う (当時の型を復元できない)")
    bank_path = bank_blocked = None
    if bs and bs in (banks or {}):
        bank_path = banks[bs]
    elif bs:
        bank_blocked = f"当時のバンク {bs} が手元に無い"
    else:
        bank_blocked = "当時のバンクの版の記録が無い (selection_record 行なし)"
    if bank_blocked:
        notes.append(f"実戦バンク: {bank_blocked} (読もうとしたら止める。対戦中の評価は読まない)")
    used = {"usage_snapshot": int(us) if us is not None else None, "team_sha16": ts if not reasons else None,
            "bank_sha16": bs if bank_path else None}
    if team_registry is not None:
        used["team_registry_sha16"] = team_registry.get("registry_sha256") if reg is not None and not reasons else None
    return {"mode": mode, "ok": not reasons, "reasons": reasons, "notes": notes,
            "usage_snapshot": int(us) if us is not None and not reasons else None,
            "bank_path": str(bank_path) if bank_path else None, "bank_sha16": bs if bank_path else None,
            "bank_blocked": bank_blocked, "recorded": want, "used": used,
            "team_registry": reg if not reasons else None}


def plan_key(plan: dict) -> tuple:
    """同じ版かどうかの鍵 (純粋。版が変わったときだけ差し替える。登録の差し替えは used.team_registry_sha16 で区別する)"""
    return (plan.get("mode"), plan.get("usage_snapshot"), plan.get("bank_path"), plan.get("bank_sha16"), plan.get("bank_blocked"),
            (plan.get("used") or {}).get("team_registry_sha16"))


def summarize_data_versions(entries: list) -> dict:
    """再生のデータの版の要約 (純粋)。entries = [{"log", "plan"}] (決定ごと)。
    {"n_logs", "n_restored" (全決定を復元できたログ), "n_unrestored", "n_decisions", "n_unrestored_decisions",
     "by_log": {log: [版ごとの {"ok", "reasons", "notes", "recorded", "used", "n"}]}}"""
    by: dict = {}
    for e in entries:
        p = e["plan"]
        lst = by.setdefault(e["log"], [])
        item = {"ok": p["ok"], "reasons": p["reasons"], "notes": p["notes"], "recorded": p["recorded"], "used": p["used"]}
        hit = next((x for x in lst if {k: x[k] for k in item} == item), None)
        if hit is None:
            lst.append(dict(item, n=1))
        else:
            hit["n"] += 1
    n_ok = sum(1 for v in by.values() if all(x["ok"] for x in v))
    return {"n_logs": len(by), "n_restored": n_ok, "n_unrestored": len(by) - n_ok, "n_decisions": len(entries),
            "n_unrestored_decisions": sum(1 for e in entries if not e["plan"]["ok"]), "by_log": by}


# ------------------------------------------------------------------ 副作用 (ファイル)
def sha256_file(path) -> Optional[str]:
    try:
        return sha256_bytes(Path(path).read_bytes())
    except OSError:
        return None


def latest_snapshot_id(db: Path) -> Optional[int]:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def snapshot_ids(db: Path) -> list:
    if not Path(db).exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [int(r[0]) for r in conn.execute("SELECT id FROM usage_snapshot ORDER BY id")]
    finally:
        conn.close()


def full_schema_rules(table_cols: dict) -> dict:
    """全スキーマの書き出しの規則 (純粋)。table_cols = {表: [列名]}。BUNDLE_RULES の表はその規則、snapshot_id 列を持つほかの表は eq、
    usage_snapshot 以外で snapshot_id を持たない表は all (snapshot に依らない表。全行)"""
    out = {}
    for t, cols in table_cols.items():
        if t in BUNDLE_RULES:
            out[t] = BUNDLE_RULES[t]
        elif "snapshot_id" in cols:
            out[t] = "eq"
        else:
            out[t] = "all"
    return out


def export_bundle(src_db: Path, dst: Path, snapshot_id: Optional[int] = None, full_schema: bool = False) -> dict:
    """使用率 DB から BUNDLE_RULES の行を別の sqlite (dst) に書き出す (元は読み取り専用で開く)。snapshot_id が None なら元の最新。
    full_schema (2026-10-31 版の固定の bundle): 元の全表 (sqlite_ の内部表を除く) を同じスキーマで作り、full_schema_rules の規則で書き出す。
    戻り値 {"source_db", "file", "snapshot_id", "tables": {表: 行数}, "rules", "sha256", "size_bytes"}"""
    from advisor.team_advice import _champions_filter
    src_db, dst = Path(src_db), Path(dst)
    if dst.exists():
        raise FileExistsError(f"{dst} が既にある")
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    try:
        snap = snapshot_id if snapshot_id is not None else latest_snapshot_id(src_db)
        if snap is None or src.execute("SELECT 1 FROM usage_snapshot WHERE id=?", (int(snap),)).fetchone() is None:
            raise ValueError(f"使用率の snapshot {snap} が {src_db} に無い")
        flt = _champions_filter(src, max_snapshot=int(snap))
        if full_schema:
            names = [n for (n,) in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        else:
            names = list(BUNDLE_RULES)
        schema = {n: sql for n, sql in src.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name IN (%s)" % ",".join("?" * len(names)),
            tuple(names))}
        indexes = [sql for (sql,) in src.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND tbl_name IN (%s)"
            % ",".join("?" * len(names)), tuple(names))]
        cols = {n: [r[1] for r in src.execute(f"PRAGMA table_info({n})")] for n in schema}
    finally:
        src.close()
    missing = [t for t in BUNDLE_RULES if t not in schema]
    if missing:
        raise ValueError(f"{src_db} に表 {missing} が無い")
    rules = full_schema_rules(cols) if full_schema else dict(BUNDLE_RULES)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(f"file:{dst}?mode=rwc", uri=True)     # URI で開く (ATTACH の元を読み取り専用の URI で開くため)
    counts = {}
    try:
        out.execute("ATTACH DATABASE ? AS src", (f"file:{src_db}?mode=ro",))
        for t, sql in schema.items():
            out.execute(sql)
        for t in rules:
            if rules[t] == "all":
                c = ", ".join(cols[t])
                out.execute(f"INSERT INTO main.{t} ({c}) SELECT {c} FROM src.{t}")
                counts[t] = out.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
                continue
            where = (f"snapshot_id = {int(snap)}" if rules[t] == "eq" and t not in BUNDLE_RULES
                     else bundle_where(t, int(snap), flt))
            if rules[t] == "pairs":
                out.execute(f"INSERT INTO main.{t} (snapshot_id, pokemon_name, teammate_name, usage_percent) "
                            f"SELECT MIN(snapshot_id), pokemon_name, teammate_name, MAX(usage_percent) FROM src.{t} "
                            f"WHERE {where} GROUP BY pokemon_name, teammate_name")
            else:
                c = ", ".join(cols[t])
                out.execute(f"INSERT INTO main.{t} ({c}) SELECT {c} FROM src.{t} WHERE {where}")
            counts[t] = out.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
        for sql in indexes:
            out.execute(sql)
        out.execute("CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT)")
        meta = {"snapshot_id": int(snap), "source_db": str(src_db), "rules": rules, "champions_filter": flt,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        out.executemany("INSERT INTO bundle_meta VALUES (?, ?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        out.commit()
        out.execute("DETACH DATABASE src")
    finally:
        out.close()
    sha = sha256_file(dst)
    return {"source_db": str(src_db), "file": dst.name, "snapshot_id": int(snap), "tables": counts, "rules": dict(rules),
            "sha256": sha, "sha16": sha[:16] if sha else None, "size_bytes": dst.stat().st_size}


def _copy_checked(src: Path, dst: Path) -> str:
    """src を dst に複製し、複製の前後で src の sha256 が変わらず dst と一致することを確かめる。sha256 を返す"""
    before = sha256_file(src)
    shutil.copy2(src, dst)
    after, copied = sha256_file(src), sha256_file(dst)
    if not (before and before == after == copied):
        raise RuntimeError(f"{src} の複製の前後で sha256 が揃わない (書き換え中の可能性)")
    return copied


def make_inputs_pin(pin_dir: Path, selection_model: Path, src_db: Optional[Path] = None, bank: Optional[Path] = None,
                    snapshot_id: Optional[int] = None) -> dict:
    """ピンに選出モデルの一組・使用率の bundle・バンクの写しを置き、inputs_manifest.json を書く。manifest を返す。
    選出の埋め込みと meta は selection_model と同じ dir のもの (無ければ記録 None。埋め込みが無ければ選出は species_embedding に落ちる)。
    バンクが無ければ real_bank は None (子プロセスにバンクの明示を渡さない)"""
    pin_dir, selection_model = Path(pin_dir), Path(selection_model)
    src_db = Path(src_db) if src_db is not None else SRC_USAGE_DB
    bank = Path(bank) if bank is not None else SRC_BANK
    if not selection_model.exists():
        raise FileNotFoundError(f"選出モデル {selection_model} が無い")
    man: dict = {"version": MANIFEST_VERSION, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pin_dir": str(pin_dir)}
    for key in SELECTION_KEYS:
        name = SELECTION_FILES[key]
        src = selection_model if key == "selection_model" else selection_model.parent / name
        if not src.exists():
            man[key] = None
            continue
        man[key] = file_entry(str(src), name, _copy_checked(src, pin_dir / name))
    man["usage_bundle"] = export_bundle(src_db, pin_dir / BUNDLE_FILE, snapshot_id)
    man["usage_bundle"]["latest_at_pin"] = latest_snapshot_id(src_db)
    if bank.exists():
        sha = _copy_checked(bank, pin_dir / BANK_FILE)
        try:
            doc = json.loads((pin_dir / BANK_FILE).read_text(encoding="utf-8"))
        except ValueError:
            doc = {}
        man["real_bank"] = file_entry(str(bank), BANK_FILE, sha, built_at=doc.get("built_at"), n_battles=doc.get("n_battles"),
                                      data_until=doc.get("data_until"))
    else:
        man["real_bank"] = None
    (pin_dir / MANIFEST_FILE).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return man


def make_pin_bundle(out_dir: Path, *, rl_policy: Path, selection_model: Path, selection_emb: Path, selection_meta: Optional[Path],
                    selection_cond: Optional[Path], registry: Path, selection: dict, src_db: Optional[Path] = None,
                    bank: Optional[Path] = None, snapshot_id: Optional[int] = None, code: Optional[dict] = None,
                    config: Optional[dict] = None, showdown: Optional[dict] = None, regulation: Optional[str] = None) -> dict:
    """版の固定の bundle を out_dir (無いこと。上書きしない) に作り、manifest.json を書いて返す (2026-10-31 追加レビュー判断 2)。
    各ファイルは _copy_checked (複製の前後で元の sha256 が変わらず写しと一致)、使用率 DB は export_bundle(full_schema=True)。
    selection = 選出モデルの由来と、明示するときの種別 ({"kind": general / team (明示するときだけ。無ければ meta で決める),
    "kind_basis" (明示の根拠), "team", "sets_premise", "source_id", "source_path", "source_manifest", "features", "regulation"})。
    種別・6 体・型の前提は advisor.pin_bundle.finalize_selection が写した meta の provenance (model_sha16 の一致) で決め、
    kind_source / kind_basis / meta_classification / formal_ok / formal_reason を足す (2026-11-02)。
    emb_sha16 はここで埋める。selection_meta / selection_cond は無ければ None (記録 None)。
    作った後に advisor.pin_bundle.check_bundle_manifest で照合し、合わなければ RuntimeError (作りかけの dir は残す。消さない)"""
    from advisor import pin_bundle as PB
    from advisor.my_team import registry_sha
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} が既にある (上書きしない)")
    src_db = Path(src_db) if src_db is not None else SRC_USAGE_DB
    bank = Path(bank) if bank is not None else SRC_BANK
    srcs = {"rl_policy": rl_policy, "selection_model": selection_model, "selection_emb": selection_emb,
            "selection_meta": selection_meta, "selection_cond": selection_cond, "real_bank": bank, "registry": registry}
    for k in ("rl_policy", "selection_model", "selection_emb", "real_bank", "registry"):
        if srcs[k] is None or not Path(srcs[k]).exists():
            raise FileNotFoundError(f"{k} の写し元 {srcs[k]} が無い")
    if not src_db.exists():
        raise FileNotFoundError(f"使用率 DB {src_db} が無い")
    out_dir.mkdir(parents=True)
    files: dict = {}
    for k, src in srcs.items():
        if src is None or not Path(src).exists():
            files[k] = None
            continue
        src = Path(src)
        files[k] = file_entry(str(src.resolve()), PB.FILES[k], _copy_checked(src, out_dir / PB.FILES[k]))
    try:
        doc = json.loads((out_dir / PB.FILES["real_bank"]).read_text(encoding="utf-8"))
        files["real_bank"].update(built_at=doc.get("built_at"), n_battles=doc.get("n_battles"), data_until=doc.get("data_until"))
    except (ValueError, AttributeError):
        pass
    reg = json.loads((out_dir / PB.FILES["registry"]).read_text(encoding="utf-8"))
    files["registry"]["registry_sha16"] = registry_sha(reg)
    ub = export_bundle(src_db, out_dir / PB.FILES["usage_db"], snapshot_id, full_schema=True)
    ub["source_db"] = str(src_db.resolve())
    ub["latest_at_pin"] = latest_snapshot_id(src_db)
    files["usage_db"] = ub
    # 種別はモデルと結び付いた学習履歴 (写した meta の provenance と model_sha16) で決める。明示 (selection の kind) は
    # kind_source explicit と根拠 (kind_basis) を残す。名前 (selection_model.pt / Package / registered) では決めない (2026-11-02)
    meta_doc = None
    if files.get("selection_meta"):
        try:
            meta_doc = json.loads((out_dir / PB.FILES["selection_meta"]).read_text(encoding="utf-8"))
        except ValueError:
            meta_doc = None
    meta_name = Path(selection_meta).name if selection_meta is not None else PB.FILES["selection_meta"]
    sel = PB.finalize_selection(selection, PB.classify_from_meta(meta_doc, files["selection_model"]["sha256"], meta_name))
    sel["emb_sha16"] = files["selection_emb"]["sha16"]
    man = {"kind": PB.BUNDLE_KIND, "version": PB.BUNDLE_VERSION, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "dir": str(out_dir.resolve()), "files": files, "selection": sel, "code": code or {}, "config": config or {},
           "showdown": showdown or {}, "regulation": regulation}
    (out_dir / PB.MANIFEST_FILE).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    actual = {e["file"]: sha256_file(out_dir / e["file"]) for e in files.values() if e}
    reasons = PB.check_bundle_manifest(man, actual)
    if reasons:
        raise RuntimeError(f"作った bundle {out_dir} が manifest と合わない: " + " / ".join(reasons))
    return man


def planned_record(selection_model: Path, src_db: Optional[Path] = None, bank: Optional[Path] = None) -> dict:
    """ピン作成前 (dry-run) の見込み: 元のファイルの sha256 と、bundle にする snapshot id (元の最新)。ファイルは作らない"""
    selection_model = Path(selection_model)
    src_db = Path(src_db) if src_db is not None else SRC_USAGE_DB
    bank = Path(bank) if bank is not None else SRC_BANK
    rec: dict = {"version": MANIFEST_VERSION, "planned": True}
    for key in SELECTION_KEYS:
        name = SELECTION_FILES[key]
        src = selection_model if key == "selection_model" else selection_model.parent / name
        sha = sha256_file(src)
        rec[key] = file_entry(str(src), name, sha) if sha else None
    snap = latest_snapshot_id(src_db) if src_db.exists() else None
    rec["usage_bundle"] = ({"source_db": str(src_db), "file": BUNDLE_FILE, "snapshot_id": snap, "rules": dict(BUNDLE_RULES),
                            "sha256": None, "sha16": None, "latest_at_pin": snap} if snap is not None else None)
    sha = sha256_file(bank)
    rec["real_bank"] = file_entry(str(bank), BANK_FILE, sha) if sha else None
    return rec


def local_data(db: Optional[Path] = None, bank: Optional[Path] = None) -> dict:
    """再生の手元のデータ (副作用: 読み取り): DB の snapshot id の一覧と最新、今のバンクの sha16、今の登録チームの本文の sha16、
    今の登録 (advisor.my_team._load()) の sha16 (team_registry_sha16。current の notes に使う)"""
    db = Path(db) if db is not None else SRC_USAGE_DB
    bank = Path(bank) if bank is not None else SRC_BANK
    snaps = snapshot_ids(db)
    bsha = sha256_file(bank)
    team = None
    try:
        from advisor.versions import _registered_team
        team = _registered_team()[0]
    except Exception:
        team = None
    try:
        from advisor.my_team import registry_sha, registry_snapshot
        reg_sha = registry_sha(registry_snapshot())
    except Exception:
        reg_sha = None
    return {"db": str(db), "snapshots": snaps, "latest": max(snaps) if snaps else None,
            "banks": ({bsha[:16]: str(bank)} if bsha else {}), "team_sha16": team, "team_registry_sha16": reg_sha}


class DataSwitch:
    """再生の決定ごとに使うデータの版を決めて差し替える (副作用: advisor.data_pin.set_override)。
    mode restore: 当時の版 (restore_plan)。復元できない決定は ok False (呼び出し側が再生しない)。
    mode current: 再生の開始時の DB の最新と今のバンクに固定する (途中で DB が更新されても混ざらない)"""

    def __init__(self, mode: str = "restore", local: Optional[dict] = None):
        if mode not in DATA_MODES:
            raise ValueError(f"--data-mode は {DATA_MODES} のどれか ({mode})")
        self.mode = mode
        self.local = local if local is not None else local_data()
        self._key = None
        self.entries: list = []

    def plan(self, log_name: str, records: list, t: Optional[float], record: bool = True) -> dict:
        """時刻 t の決定の版を決め、復元できれば差し替える。record = 要約に数える (初回の読み込みの空回しは数えない)"""
        L = self.local
        p = restore_plan(data_version_at(records, t), L["snapshots"], L["team_sha16"], L["banks"], self.mode, L["latest"],
                         team_registry=team_registry_at(records, t), current_registry_sha16=L.get("team_registry_sha16"))
        if record:
            self.entries.append({"log": log_name, "plan": p})
        if p["ok"]:
            self.apply(p)
        return p

    def apply(self, p: dict) -> None:
        from advisor.data_pin import set_override
        L = self.local
        if self.mode == "current":
            cur = _first(L["banks"].items())
            key = ("current", L["latest"])
            if key != self._key:
                set_override(usage_db=L["db"], usage_snapshot=L["latest"], bank_path=cur[1] if cur else None,
                             bank_sha16=cur[0] if cur else None,
                             bank_blocked=None if cur else "手元にバンクが無い")
                self._key = key
            return
        key = plan_key(p)
        if key != self._key:
            set_override(usage_db=L["db"], usage_snapshot=p["usage_snapshot"], bank_path=p["bank_path"], bank_sha16=p["bank_sha16"],
                         bank_blocked=p["bank_blocked"], team_registry=p.get("team_registry"))
            self._key = key

    def close(self) -> None:
        from advisor.data_pin import clear_override
        clear_override()
        self._key = None

    def summary(self) -> dict:
        L = self.local
        return {"mode": self.mode, "local": {"db": L["db"], "latest_snapshot": L["latest"], "bank_sha16": list(L["banks"]),
                                             "team_sha16": L["team_sha16"], "team_registry_sha16": L.get("team_registry_sha16")},
                **summarize_data_versions(self.entries)}


def read_manifest(pin_dir: Path) -> Optional[dict]:
    try:
        return json.loads((Path(pin_dir) / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def actual_shas(pin_dir: Path, record: Optional[dict]) -> dict:
    """record に出てくるピンのファイルの今の sha256 (無ければ None)"""
    out = {}
    for k in SELECTION_KEYS + ("usage_bundle", "real_bank"):
        e = (record or {}).get(k) or {}
        if e.get("file"):
            out[e["file"]] = sha256_file(Path(pin_dir) / e["file"])
    return out


def verify_pin(pin_dir: Path, record: Optional[dict] = None, require_bank: bool = True) -> list:
    """ピンの照合 (副作用: 読み取り)。record が None ならピンの inputs_manifest.json。成り立たない理由の list"""
    rec = record if record is not None else read_manifest(pin_dir)
    if rec is None:
        return [f"{Path(pin_dir) / MANIFEST_FILE} が無い (入力の実体を固定していないピン)"]
    return check_manifest(rec, actual_shas(pin_dir, rec), require_bank=require_bank and rec.get("real_bank") is not None)
