"""既存の選出モデルの学習履歴 (provenance) を学習の記録から復元する (2026-11-02 開発者判断 4「まず根拠の復元。再学習は後」)。

    python -m tools.inheritance_eval.reconstruct_provenance --root <本体ツリー> [--out <出力 dir>]          # 既定は dry-run
    python -m tools.inheritance_eval.reconstruct_provenance --root <本体ツリー> --apply <dir> [--apply-registry]

本体ツリー (--root) の選出モデル (MODELS_DIR の selection_model*.pt = 配布版・汎用・条件付き・v2・v3、Package
(logs/registry/package/**) の選出モデル、logs/selection_refresh/checkpoints の sandbox) を全部列挙し、sha256・mtime・meta の有無と、
記録から取れる証拠を突き合わせる。読むだけで、モデルの dir には書かない (--apply を付けたときだけ書く)。

証拠 (provenance.evidence の kind。強さの規則は advisor.pin_bundle.reconstructed_evidence_status):
  - hash_identity: ファイルの sha256 が汎用モデル (--root の GENERAL_MODEL_PATH の実体 = 参照先) と同一。参照先のモデル自身に
    強い由来があり汎用と決まるときだけ強い (要素の ref_evidence に training_log = 参照先の保存行の一致、または trained_meta =
    参照先に学習コードが書いた source trained の meta (model_sha16 一致、kind_source meta)。汎用 = general、型の前提 none)。
    参照先が不明なら弱い (ref_evidence 空)。参照先そのもの (置き場の実体が自分自身と同一) には付けない (2026-11-02 判断:
    コードの経路からの推定は由来の確認ではない)
  - training_log (強い): 学習の記録 (logs/*.log・logs/selection_refresh/*.log・champions_agent/train/logs/*.log) の
    「[train_selection] …保存: <パス>」の行のパスがモデルと同じで、保存行の直前の時刻 ≤ mtime、かつ保存行の直後の時刻と mtime の差が
    config.PROVENANCE_LOG_MTIME_TOLERANCE_S 以内。種別は保存行で決める: 汎用・v2/v3 汎用・条件付き → general (前提 none)、
    配布版 (「保存:」) は同じ学習の区間に「my_teamで微調整」があれば team_finetuned (6 体は直前の「[my_team] …現在の6体を推定」の
    名前を種族 id に直す。型の前提は記録が無いので unknown)、「微調整をとばします」があれば general、どちらも無ければ決めない
  - run_manifest (弱い): Package の manifest.json の selection_model_sha256・pick_variant、registry の index の行
  - mtime (弱い): ファイルの mtime だけ
弱い証拠だけのモデルは復元しない (「不明」のまま。一覧に理由を出す)。既に provenance を持つ meta は書き換えない。

出力 (--out。既定は書かずに一覧を標準出力へ): report.md (一覧)、results.json、proposed/<モデルの dir の root からの相対>/<stem>_meta.json
(meta の案)。--out は --root の MODELS_DIR・logs/registry の中には置けない。
--apply <dir>: <dir>/<モデルの相対パス> にそのモデル (sha256 が同じ) があるときだけ、隣に <stem>_meta.json を書く (既存の meta に
provenance が無ければ teams などの鍵を残して model_sha16 と provenance を足す。provenance があれば書かない)。
logs/registry 配下 (Package) は registry の成果物 (id = 内容の sha256) を変えてしまうので、--apply-registry を付けたときだけ書く。

純粋な部分 (記録の解析・保存行との照合・1 モデルの復元・一覧の Markdown) は tests/test_model_provenance.py。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from advisor import pin_bundle as PB
from champions_agent import config as C

REPO = Path(__file__).resolve().parent.parent.parent
RECONSTRUCTED_BY = "tools/inheritance_eval/reconstruct_provenance.py"


def _models_rel() -> Path:
    try:
        return Path(C.MODELS_DIR).resolve().relative_to(REPO)
    except ValueError:      # CHAMPIONS_MODELS_DIR で外を指している
        return Path("champions_agent/train/checkpoints")


MODELS_REL = _models_rel()
GENERAL_NAME = "selection_model_general.pt"
PACKAGE_REL = Path("logs/registry/package")
REGISTRY_REL = Path("logs/registry")
SANDBOX_REL = Path("logs/selection_refresh/checkpoints")
LOG_GLOBS = ("logs/*.log", "logs/selection_refresh/*.log", "champions_agent/train/logs/*.log")
# MODELS_DIR の中のファイル名 → 一覧の区分 (表示だけ。種別は証拠で決める)
ROLE_BY_NAME = {"selection_model.pt": "配布版", GENERAL_NAME: "汎用", "selection_model_cond.pt": "条件付き",
                "selection_model_v2_general.pt": "v2 汎用", "selection_model_v3_general.pt": "v3 汎用"}

# 学習の記録の行
_SAVE_RE = re.compile(r"^\[train_selection\] (?P<label>[^:]*?)保存: (?P<path>\S+)")
_START_RE = re.compile(r"^\[train_selection\] データ ")
_DT_RE = re.compile(r"(?:(?P<y>\d{4})-)?(?P<mo>\d{2})-(?P<d>\d{2}) (?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})")
_T_RE = re.compile(r"(?<![\d:.])(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})(?![\d:])")
_FT_RE = re.compile(r"my_teamで微調整")
_SKIP_RE = re.compile(r"微調整をとばします")
_TEAM_RE = re.compile(r"^\[my_team\] .*現在の6体を推定: (?P<names>.+)$")
# 保存行の名目 → 復元する provenance の kind (配布版の "" は区間の微調整の行で決める)
GENERAL_SAVE_LABELS = ("汎用モデル", "v2汎用モデル", "v3 汎用モデル", "条件付きモデル")


# ------------------------------------------------------------------ 純粋
def _fmt(t: Optional[float]) -> Optional[str]:
    return None if t is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _local_ts(y: int, mo: int, d: int, h: int, mi: int, s: int) -> float:
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


def parse_training_log(text: str, log_mtime: float, log_path: str = "") -> list:
    """学習の記録 (純粋) → 保存行の list。各要素 {"log", "line", "label", "path", "ts_before", "ts_after", "finetune"
    (True = 区間に「my_teamで微調整」/ False = 「微調整をとばします」/ None = どちらも無い), "team_names"}。
    時刻は「MM-DD HH:MM:SS」(年つきも可) で日付を覚え、時刻だけの行はその日付 (時刻が戻れば翌日) で読む。年が無ければ記録の mtime の年
    (月日が mtime より後なら前年)。保存行の後に時刻の行が無ければ ts_after は記録の mtime。区間 = 直前の「[train_selection] データ」の行から"""
    lm = time.localtime(log_mtime)
    date = None          # (y, mo, d)
    last_t = None
    stamps = []          # (行番号, 時刻)
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = _DT_RE.search(ln)
        if m:
            mo, d = int(m["mo"]), int(m["d"])
            y = int(m["y"]) if m["y"] else (lm.tm_year if (mo, d) <= (lm.tm_mon, lm.tm_mday) else lm.tm_year - 1)
            date = (y, mo, d)
            t = _local_ts(y, mo, d, int(m["h"]), int(m["mi"]), int(m["s"]))
        else:
            m = _T_RE.search(ln)
            if not m or date is None:
                continue
            t = _local_ts(*date, int(m["h"]), int(m["mi"]), int(m["s"]))
            if last_t is not None and t < last_t:      # 日付をまたいだ
                nd = _dt.date(*date) + _dt.timedelta(days=1)
                date = (nd.year, nd.month, nd.day)
                t = _local_ts(*date, int(m["h"]), int(m["mi"]), int(m["s"]))
        last_t = t
        stamps.append((i, t))
    out = []
    block_start = 0
    for i, ln in enumerate(lines):
        if _START_RE.match(ln):
            block_start = i
        m = _SAVE_RE.match(ln)
        if not m:
            continue
        before = [t for j, t in stamps if j < i]
        after = [t for j, t in stamps if j > i]
        block = lines[block_start:i]
        ft = True if any(_FT_RE.search(x) for x in block) else (False if any(_SKIP_RE.search(x) for x in block) else None)
        names = None
        for x in block:
            tm = _TEAM_RE.match(x)
            if tm:
                names = [n.strip() for n in tm["names"].split("/") if n.strip()]
        out.append({"log": log_path, "line": i + 1, "label": m["label"].strip(), "path": m["path"],
                    "ts_before": before[-1] if before else None, "ts_after": after[0] if after else log_mtime,
                    "ts_after_is_log_mtime": not after, "finetune": ft, "team_names": names if ft else None})
    return out


def match_save_events(events: list, model_abs: str, mtime: float, tol: float) -> list:
    """保存行のうち、このモデルを書いたとみなせるもの (純粋): パスが同じ、直前の時刻 ≤ mtime (1 秒の丸めを許す)、
    直後の時刻 − tol ≤ mtime ≤ 直後の時刻 + 1"""
    out = []
    for e in events:
        if str(e["path"]) != str(model_abs):
            continue
        if e["ts_before"] is not None and mtime < e["ts_before"] - 1:
            continue
        if not (e["ts_after"] - tol <= mtime <= e["ts_after"] + 1):
            continue
        out.append(e)
    return out


def kind_from_save(e: dict, resolve_names: Optional[Callable] = None) -> dict:
    """保存行から決まる種別 (純粋)。{"kind": general / team_finetuned / None, "team", "sets_premise", "why"}"""
    if e["label"] in GENERAL_SAVE_LABELS:
        return {"kind": "general", "team": None, "sets_premise": "none", "why": f"保存行「{e['label']}保存」"}
    if e["label"] == "":
        if e["finetune"] is True:
            ids = resolve_names(e["team_names"]) if (resolve_names and e.get("team_names")) else None
            team = sorted(set(ids)) if ids and len(set(ids)) == 6 else None
            return {"kind": "team_finetuned", "team": team, "sets_premise": "unknown",
                    "why": ("配布版の保存行の区間に「my_teamで微調整」。型の前提は記録が無いので unknown"
                            + ("" if team else f"。6 体を種族 id に直せない ({e.get('team_names')})"))}
        if e["finetune"] is False:
            return {"kind": "general", "team": None, "sets_premise": "none",
                    "why": "配布版の保存行の区間に「微調整をとばします」(微調整なし = 汎用)"}
        return {"kind": None, "team": None, "sets_premise": None,
                "why": "配布版の保存行だが、区間に微調整の有無の行が無い (種別を決めない)"}
    return {"kind": None, "team": None, "sets_premise": None, "why": f"保存行の名目「{e['label']}」を読めない"}


def reconstruct_one(model: dict, anchor: Optional[dict], save_events: list, manifests: list, tol: float, now: str,
                    resolve_names: Optional[Callable] = None) -> dict:
    """1 モデルの復元 (純粋)。model = {"rel", "abs", "sha256", "mtime", "size", "role", "meta" (既存の meta の中身 or None),
    "meta_exists"}、anchor = 汎用モデル (参照先) {"rel", "sha256", "ref_evidence" (参照先自身の強い証拠の種類。anchor_ref)}
    (無ければ None)、save_events = parse_training_log の全件、
    manifests = この sha の run_manifest の記述 [{"detail", "path", "kind_hint"}]。
    戻り値 {"rel", "role", "sha16", "mtime", "meta_state", "evidence", "notes", "provenance" (復元できたときの案 or None),
    "status" ("trained" 既に学習時の記録あり / "reconstructed" / "unknown"), "reason", "classification", "formal"}"""
    sha = model["sha256"]
    ev, notes = [], []
    kind = team = premise = None
    why = []
    old = model.get("meta")
    meta_state = ("なし" if not model.get("meta_exists") else
                  ("provenance あり" if isinstance(old, dict) and isinstance(old.get("provenance"), dict) else
                   ("provenance なし" if isinstance(old, dict) else "読めない")))
    if isinstance(old, dict) and isinstance(old.get("provenance"), dict):
        c = PB.classify_from_meta(old, sha, Path(model["rel"]).stem + "_meta.json")
        return {"rel": model["rel"], "role": model["role"], "sha16": sha[:16], "mtime": _fmt(model["mtime"]), "meta_state": meta_state,
                "evidence": [], "notes": ["既存の meta に provenance がある (書き換えない)"], "provenance": None,
                "status": {"meta": "trained", "reconstructed": "existing_reconstructed"}.get(c["kind_source"], "unknown"),
                "reason": c["kind_basis"],
                "classification": c, "formal": PB.formal_status(c)}
    if isinstance(old, dict):
        val = old.get("val") if isinstance(old.get("val"), dict) else {}
        notes.append(f"既存の meta (provenance なし): teams {len(old.get('teams') or [])} / n_samples {old.get('n_samples')}"
                     + (f" / val.at {val.get('at')}" if val.get("at") else ""))
    # hash_identity (参照先そのものには付けない。参照先自身に強い由来 (training_log / trained_meta) があるときだけ強い)
    if anchor and anchor.get("sha256") == sha and anchor["rel"] != model["rel"]:
        ref = list(anchor.get("ref_evidence") or [])
        ref_ok = any(k in PB.REF_STRONG_EVIDENCE_KINDS for k in ref)
        detail = (f"汎用モデル {anchor['rel']} と sha256 が同一 (sha16 {sha[:16]})。参照先自身の証拠: "
                  + (", ".join(ref) if ref_ok else "強い証拠なし (参照先が不明のため弱い証拠)"))
        ev.append({"kind": "hash_identity", "detail": detail, "path": anchor["rel"], "ref_evidence": ref})
        if ref_ok:
            kind, team, premise = "general", None, "none"
            why.append(f"hash_identity (参照先は {', '.join(ref)} で汎用) → general (前提 none)")
        else:
            notes.append(f"汎用モデル {anchor['rel']} と同一だが、その汎用モデル自身の由来が不明")
    # training_log
    for e in match_save_events(save_events, model["abs"], model["mtime"], tol):
        k = kind_from_save(e, resolve_names)
        detail = (f"{Path(e['log']).name}:{e['line']} 「{e['label']}保存: {e['path']}」、直前の時刻 {_fmt(e['ts_before'])}、"
                  f"直後の時刻 {_fmt(e['ts_after'])}{' (記録の mtime)' if e['ts_after_is_log_mtime'] else ''} と mtime "
                  f"{_fmt(model['mtime'])} が一致 (許容 {tol:g} 秒)。{k['why']}")
        if k["kind"] is None:
            notes.append(f"保存行は一致したが種別を決めない: {detail}")
            continue
        ev.append({"kind": "training_log", "detail": detail, "path": e["log"]})
        if kind is not None and (kind, team, premise) != (k["kind"], k["team"], k["sets_premise"]):
            notes.append(f"証拠の種別が食い違う ({kind} / {k['kind']})。復元しない")
            kind = "conflict"
            continue
        if kind is None:
            kind, team, premise = k["kind"], k["team"], k["sets_premise"]
            why.append(f"training_log → {k['kind']} (前提 {k['sets_premise']})")
    for mf in manifests:
        ev.append({"kind": "run_manifest", "detail": mf["detail"], "path": mf.get("path")})
    ev.append({"kind": "mtime", "detail": f"mtime {_fmt(model['mtime'])} (size {model.get('size')})", "path": model["rel"]})
    base = {"rel": model["rel"], "role": model["role"], "sha16": sha[:16], "mtime": _fmt(model["mtime"]), "meta_state": meta_state,
            "evidence": ev, "notes": notes}
    st = PB.reconstructed_evidence_status({"evidence": ev})
    if kind in (None, "conflict") or not st["ok"]:
        reason = ("証拠の種別が食い違う" if kind == "conflict" else
                  (st["reason"] if not st["ok"] else "強い証拠はあるが種別を決められない"))
        c = PB.classify_from_meta(old if isinstance(old, dict) else None, sha)
        return dict(base, provenance=None, status="unknown", reason=reason, classification=c, formal=PB.formal_status(c))
    prov = {"kind": kind, "finetune_team": team, "sets_premise": premise, "sets": None,
            "general_model_sha16": anchor["sha256"][:16] if anchor else None, "trained_at": _fmt(model["mtime"]),
            "data_path": None, "code_commit": None, "source": "reconstructed", "evidence": ev,
            "reconstructed_at": now, "reconstructed_by": RECONSTRUCTED_BY}
    doc = proposed_meta(old, sha, prov)
    c = PB.classify_from_meta(doc, sha, Path(model["rel"]).stem + "_meta.json")
    return dict(base, provenance=prov, status="reconstructed", reason="; ".join(why) + " (trained_at はファイルの mtime)",
                classification=c, formal=PB.formal_status(c))


def anchor_ref(anchor_model: dict, anchor_result: dict) -> dict:
    """hash_identity の参照先 (純粋): anchor_model = 汎用モデルの model_info、anchor_result = それ自身の復元 (hash_identity なし)。
    参照先自身が汎用と決まるときだけ ref_evidence に強い由来を入れる (それ以外は空 = 参照先が不明):
      - 学習コードが書いた meta (source trained、model_sha16 一致、kind_source meta) → trained_meta
      - 記録から復元した (今回、または既存の reconstructed の meta) で training_log がある → training_log
    参照先の meta が復元の弱いもの (種別 unknown) なら空"""
    c = anchor_result.get("classification") or {}
    ref = []
    if c.get("kind") == "general":
        if c.get("kind_source") == "meta" and ((c.get("provenance") or {}).get("source") or "trained") == "trained":
            ref = ["trained_meta"]
        elif c.get("kind_source") == "reconstructed":
            kinds = [e["kind"] for e in anchor_result.get("evidence") or []] or list((c.get("provenance") or {}).get("evidence_kinds") or [])
            ref = ["training_log"] if "training_log" in kinds else []
    return {"rel": anchor_model["rel"], "sha256": anchor_model["sha256"], "ref_evidence": ref}


def proposed_meta(old: Optional[dict], sha256: str, prov: dict) -> dict:
    """書く meta の中身 (純粋): 既存の meta (provenance なし) の鍵を残し、model_sha16 と provenance を足す"""
    out = dict(old) if isinstance(old, dict) and not isinstance(old.get("provenance"), dict) else {}
    out["model_sha16"] = sha256[:16]
    out["provenance"] = dict(prov)
    return out


def manifests_for_model(rel: str, records: list) -> list:
    """同じ sha の run_manifest の記述のうち、このモデルの記述 (純粋): モデルを含む dir のうち最も近い Package の manifest と
    registry の index の行はそのまま、ほかの場所の manifest (同じ実体を指す別の Package) は件数と場所の 1 件にまとめる"""
    parts = Path(rel).parts
    own_len = max((len(Path(r["scope_dir"]).parts) for r in records
                   if r.get("scope_dir") and parts[:len(Path(r["scope_dir"]).parts)] == Path(r["scope_dir"]).parts), default=None)
    out, others = [], []
    for r in records:
        sd = r.get("scope_dir")
        if sd is None:
            out.append({"detail": r["detail"], "path": r["path"]})
        elif own_len is not None and len(Path(sd).parts) == own_len and parts[:own_len] == Path(sd).parts:
            out.append({"detail": r["detail"] + " (このモデルの Package)", "path": r["path"]})
        else:
            others.append(r)
    if others:
        out.append({"detail": f"ほかの Package の manifest {len(others)} 件が同じ sha256 を指す ("
                              + ", ".join(sorted({r['detail'].split('、', 1)[1] for r in others})) + "): "
                              + ", ".join(r["path"] for r in others), "path": None})
    return out


def _ev_short(ev: list) -> str:
    """証拠の種類の短い表記 (強い証拠は太字。hash_identity は参照先が不明なら「(参照先不明)」で太字にしない)"""
    out = []
    for e in ev:
        strong = PB.reconstructed_evidence_status({"evidence": [e]})["ok"]
        k = e["kind"] + (" (参照先不明)" if e["kind"] == "hash_identity" and not strong else "")
        k = f"**{k}**" if strong else k
        if k not in out:
            out.append(k)
    return ", ".join(out) or "-"


def markdown(results: list, root: str, now: str, applied: Optional[list] = None) -> str:
    """一覧の Markdown (純粋)"""
    n_rec = sum(r["status"] == "reconstructed" for r in results)
    n_formal = sum(bool(r["formal"]["ok"]) for r in results)
    lines = [f"# 選出モデルの学習履歴の復元 ({now})", "",
             f"- 対象: {root} (読み取りのみ。meta を書くのは --apply のときだけ)",
             f"- 列挙 {len(results)} 個 / 復元 {n_rec} 個 / formal_ok の見込み True {n_formal} 個",
             "- 強い証拠 (太字): training_log と、参照先 (汎用モデル) 自身に強い由来 (training_log / trained の meta) がある hash_identity。"
             "弱い証拠だけなら「不明」のまま", "",
             "| モデル | 区分 | sha16 | mtime | meta | 種別 | 型の前提 | 証拠 | formal_ok の見込み | 根拠・理由 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        c = r["classification"]
        kind = PB.KIND_LABEL.get(c["kind"], c["kind"]) + (f" {c['team']}" if c.get("team") else "")
        fo = "True" if r["formal"]["ok"] else f"False ({r['formal']['reason']})"
        why = r["reason"] or ""
        if r["notes"]:
            why += (" / " if why else "") + " / ".join(r["notes"])
        lines.append(f"| `{r['rel']}` | {r['role']} | {r['sha16']} | {r['mtime']} | {r['meta_state']} | {kind} "
                     f"({c['kind_source']}) | {c['sets_premise']} | {_ev_short(r['evidence'])} | {fo} | {why.replace('|', '/')} |")
    lines += ["", "## 証拠の詳細", ""]
    for r in results:
        if r["status"] != "reconstructed":
            continue
        lines.append(f"- `{r['rel']}`")
        for e in r["evidence"]:
            lines.append(f"  - {e['kind']}: {e['detail']}")
    if applied is not None:
        lines += ["", "## --apply の結果", ""]
        lines += [f"- {a['target']}: {a['result']}" for a in applied] or ["- (書いたものなし)"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 副作用 (読み取り)
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_models(root: Path) -> list:
    """本体の選出モデルのファイル (相対パス, 区分)"""
    out = []
    md = root / MODELS_REL
    for p in sorted(md.glob("selection_model*.pt")):
        out.append((p.relative_to(root), ROLE_BY_NAME.get(p.name, "checkpoints")))
    for p in sorted((root / PACKAGE_REL).glob("**/selection_model*.pt")):
        out.append((p.relative_to(root), "Package"))
    for p in sorted((root / SANDBOX_REL).glob("selection_model*.pt")):
        out.append((p.relative_to(root), "sandbox (selection_refresh)"))
    return out


def model_info(root: Path, rel: Path, role: str) -> dict:
    from champions_agent.agent.selection_model import provenance_meta_path
    p = root / rel
    st = p.stat()
    mp = provenance_meta_path(p)
    return {"rel": str(rel), "abs": str(p), "sha256": _sha256(p), "mtime": st.st_mtime, "size": st.st_size, "role": role,
            "meta_exists": mp.exists(), "meta": _read_json(mp) if mp.exists() else None}


def load_save_events(root: Path, globs=LOG_GLOBS) -> list:
    out = []
    for g in globs:
        for p in sorted(root.glob(g)):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "[train_selection]" not in text:
                continue
            out += parse_training_log(text, p.stat().st_mtime, str(p.relative_to(root)))
    return out


def manifest_records(root: Path) -> dict:
    """sha256 → run_manifest の記述の list (Package の manifest.json と registry の index)"""
    out: dict = {}
    for mf in sorted((root / PACKAGE_REL).glob("**/manifest.json")):
        m = _read_json(mf)
        if not isinstance(m, dict) or not m.get("selection_model_sha256"):
            continue
        out.setdefault(m["selection_model_sha256"], []).append(
            {"detail": f"Package の manifest: selection_model_sha256 一致、pick_variant {m.get('pick_variant')!r}、"
                       f"pick_policy {m.get('pick_policy')!r}", "path": str(mf.relative_to(root)),
             "scope_dir": str(mf.parent.relative_to(root))})
    idx = root / REGISTRY_REL / "index.jsonl"
    if idx.exists():
        seen = set()
        for ln in idx.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("kind") != "selection_model" or r.get("id") in seen:
                continue
            seen.add(r.get("id"))
            out.setdefault(r.get("sha256"), []).append(
                {"detail": f"registry の index: {r.get('id')} run {r.get('run_id')} meta {json.dumps(r.get('meta'), ensure_ascii=False)}",
                 "path": str(idx.relative_to(root)), "scope_dir": None})
    return out


def _resolver() -> Optional[Callable]:
    """日本語名 → 種族 id (記録の「現在の6体を推定」を直す)。読めなければ None (6 体は決めない)"""
    try:
        from vision.normalize import NameResolver
        res = NameResolver()
    except Exception:
        return None

    def f(names):
        ids = []
        for ja in names or []:
            r = res.resolve_species(ja, cutoff=0.85)
            if not r:
                return None
            ids.append(re.sub(r"[^a-z0-9]", "", str(r[1]).lower()))
        return ids
    return f


def reconstruct(root: Path, tol: float, now: str, resolve_names: Optional[Callable] = None) -> list:
    """本体ツリーの選出モデルを全部復元する (副作用: 読み取りだけ)。resolve_names = 日本語名 → 種族 id (None なら NameResolver)"""
    root = Path(root).resolve()
    models = [model_info(root, rel, role) for rel, role in list_models(root)]
    gen_rel = str(MODELS_REL / GENERAL_NAME)
    gm = next((m for m in models if m["rel"] == gen_rel), None)
    events = load_save_events(root)
    mans = manifest_records(root)
    res = resolve_names or (_resolver() if any(e["finetune"] for e in events) else None)
    one = lambda m, anchor: reconstruct_one(m, anchor, events, manifests_for_model(m["rel"], mans.get(m["sha256"], [])), tol, now, res)
    # 参照先 (汎用モデル) 自身を先に復元する (hash_identity なしで)。その結果で hash_identity の強さが決まる
    anchor = anchor_ref(gm, one(gm, None)) if gm else None
    return [one(m, anchor) for m in models]


# ------------------------------------------------------------------ 副作用 (書き込み)
def _inside(p: Path, d: Path) -> bool:
    try:
        p.resolve().relative_to(d.resolve())
        return True
    except ValueError:
        return False


def write_outputs(out: Path, results: list, md: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for r in results:
        if r["status"] != "reconstructed":
            continue
        rel = Path(r["rel"])
        t = out / "proposed" / rel.parent / (rel.stem + "_meta.json")
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(json.dumps({"model_sha16": r["sha16"], "provenance": r["provenance"]}, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")


def apply_metas(dest: Path, results: list, include_registry: bool = False) -> list:
    """--apply: dest/<モデルの相対パス> に同じ sha256 のモデルがあるときだけ隣に meta を書く。結果の list"""
    out = []
    for r in results:
        if r["status"] != "reconstructed":
            continue
        rel = Path(r["rel"])
        model = dest / rel
        target = model.with_name(rel.stem + "_meta.json")
        if _inside(model, dest / REGISTRY_REL) and not include_registry:
            out.append({"target": str(target), "result": "書かない (logs/registry 配下。--apply-registry が無い)"})
            continue
        if not model.exists():
            out.append({"target": str(target), "result": "書かない (モデルが無い)"})
            continue
        if _sha256(model)[:16] != r["sha16"]:
            out.append({"target": str(target), "result": "書かない (モデルの sha16 が復元時と違う)"})
            continue
        old = _read_json(target) if target.exists() else None
        if target.exists() and not isinstance(old, dict):
            out.append({"target": str(target), "result": "書かない (既存の meta を読めない)"})
            continue
        if isinstance(old, dict) and isinstance(old.get("provenance"), dict):
            out.append({"target": str(target), "result": "書かない (既存の meta に provenance がある)"})
            continue
        doc = proposed_meta(old, _sha256(model), r["provenance"])
        target.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        out.append({"target": str(target), "result": "書いた" + (" (既存の鍵を残して追記)" if old else "")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="既存の選出モデルの学習履歴 (provenance) を記録から復元する (既定は dry-run)")
    ap.add_argument("--root", default=str(REPO), help="本体ツリー (読み取りのみ)")
    ap.add_argument("--out", help="report.md・results.json・meta の案 (proposed/) を書く dir。無ければ一覧を標準出力へ")
    ap.add_argument("--apply", help="この dir の同じ相対パスのモデルの隣に meta を書く (sha256 が一致するときだけ)")
    ap.add_argument("--apply-registry", action="store_true", help="--apply で logs/registry 配下 (Package) にも書く")
    ap.add_argument("--tolerance", type=float, default=float(C.PROVENANCE_LOG_MTIME_TOLERANCE_S),
                    help="保存行の直後の時刻と mtime の差の許容 (秒)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if a.out:
        out = Path(a.out)
        for bad in (root / MODELS_REL, root / REGISTRY_REL):
            if _inside(out, bad):
                raise SystemExit(f"--out {out} は {bad} の中に置けない (本体のモデルの dir には書かない)")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    results = reconstruct(root, a.tolerance, now)
    applied = apply_metas(Path(a.apply).resolve(), results, a.apply_registry) if a.apply else None
    md = markdown(results, str(root), now, applied)
    if a.out:
        write_outputs(Path(a.out), results, md)
        print(f"[reconstruct_provenance] 書いた: {Path(a.out) / 'report.md'}")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
