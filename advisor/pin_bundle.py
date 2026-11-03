"""版の固定の bundle (2026-10-31 追加レビュー判断 2・4、docs/INHERITANCE_B0_REVIEW_20261031.md §3 の 2・4)。

1 つの dir (logs/pinned_data/<時刻>/) に、助言が読む入力を一組で置く (作るのは tools.inputs_pin.make_pin_bundle、
CLI は python -m tools.advice_mode --freeze-bundle):
  rl_policy.zip               RL 方策
  selection_model.pt          助言の選出モデル
  selection_model_emb.json    その推論が読む機能埋め込み (selection_model._functional_store / v3 の _functional_store)
  selection_model_meta.json   同じモデルの meta (trained_teams。無いモデルは記録 None = 学習チームなし)
  selection_model_cond.pt     実戦の選出傾向に条件づけた推し (predict_with_prior) の条件付きモデル (無ければ記録 None = 推しを出さない)
  usage_bundle.sqlite3        使用率 DB から必要な snapshot (と履歴の範囲) を抽出した不変の sqlite (スキーマは元と同じ)
  bank.json                   実戦バンクの写し
  my_team.json                登録 (advisor.my_team が読む config/my_team.json の写し)
  manifest.json               各ファイルの sha256・出所、コード commit、設定、Showdown の版、選出モデルの種別と適用範囲、規制

読み手 (環境変数 config.PIN_BUNDLE_ENV = ADVICE_PIN_BUNDLE を設定したとき):
  - advisor.data_pin: 使用率 DB・snapshot・バンク・RL 方策・選出モデルの版を manifest から埋める (個別の鍵と一緒に書けば一致を確かめ、
    違えば InputsPinError)
  - champions_agent.agent.selection_model / selection_features_v3: 埋め込みと meta を bundle から読む (配備先の emb・meta を読まない)
  - advisor.my_team._load: 登録を bundle から読む
  - advisor.selection.attach_model_pick: 条件付きモデルを bundle から読み、構築専用モデルの適用範囲を確かめる (selection_scope)
どれも、ファイルが無い・sha256 が manifest と違う・読めなければ InputsPinError (配備先へ黙って退避しない)。
ファイルの照合は stat (mtime_ns・size・inode) が変わったときだけ sha256 を計算し直す (推論のたびに全体を読まない)。

構築専用モデルの適用範囲 (判断 4): manifest の selection.kind が "team" なら、自分の 6 体 (種族 id の集合。順番は問わない) が
selection.team と同じときだけ推論する。6 体が確定していなければ確認待ち (推論しない)、違えば止めて理由を残す。学習が固定型を
前提にした (selection.sets_premise.fixed) なら、bundle の登録の型も照合する。汎用 ("general") は複数構築に使える。
汎化試験として別構築へ適用するのは config.PIN_ALLOW_OTHER_TEAM_ENV = 1 のときだけ (version 行の runtime と助言に残す)。
source_id の "pinned" は由来の表示で、適用範囲の判定には使わない (manifest の kind と team だけで決める)。

2026-11-02 種別の根拠と正式比較の可否 (レビュー 11/2 判断 1・2):
  - 種別 (selection.kind: general / team / unknown) はモデルの実体と結び付いた学習履歴で決める (classify_from_meta): モデルと
    同じ dir の <stem>_meta.json の provenance を、meta に記録された model_sha16 がモデルの sha256 と一致するときだけ採用
    (kind_source "meta")。meta が無い・provenance が無い・sha が違えば "unknown" (kind_source "unknown")。ファイルの名前
    (selection_model.pt / Package / registered) では決めない。明示 (--selection-kind) は根拠の文 (kind_basis) を必須にし
    kind_source "explicit" (CLI。API で根拠なしの明示は formal_ok=False)。meta の判定は selection.meta_classification に残す
  - 型の前提 (selection.sets_premise) は "fixed" (学習データが型を固定。premise_sets に型) / "none" (型に条件づけない) /
    "unknown" (分からない) の 3 値。旧い形 ({"fixed": True / None, "sets"}) も読む (True → fixed、それ以外 → unknown)
  - 適用範囲: 構築専用 + fixed → 6 体と型の内容まで照合、構築専用 + none → 6 体だけ、構築専用 + unknown → 6 体は照合するが
    正式比較には使えない、不明の種別 → 照合できず正式比較には使えない
  - 正式比較の可否 formal_ok (formal_status): 種別が meta から決まる (または明示 + 根拠)、構築専用なら前提が unknown でない、
    のときだけ True。False でも起動・推論はできるが、起動ログ・画面・version 行の runtime に「適用範囲未確認 (診断)」を出し、
    tools.real_accept の群の照合で判定不能にする
  - 画面: bundle 使用中は「固定した登録を使用中 (bundle <dir 名>、manifest sha16 …)」を助言欄の上部に常時出す (pin_status)
  - advisor.team_advice (meta_top・補完候補) は配備先の DB を読む。操作・選出の試験の経路ではないので今回の固定対象から除外
    (TEAM_ADVICE_EXCLUSION。構築・修理の比較で使う段階で固定対象に入れる)

2026-11-02 復元した学習履歴 (開発者判断 4「まず根拠の復元。再学習は後」、tools/inheritance_eval/reconstruct_provenance.py):
  - provenance.source は "trained" (train_selection が学習時に書いた。鍵が無い旧い記録も trained とみなす) / "reconstructed"
    (学習の記録・ファイルの sha・mtime から後で復元した)。復元は evidence ([{"kind", "detail", "path"}])・reconstructed_at・
    reconstructed_by を持つ
  - 証拠の強さ (reconstructed_evidence_status): training_log (学習の記録の保存行のパスがモデルと同じで、その直後の時刻とモデルの
    mtime が一致) か、参照先のモデル自身に強い由来 (training_log の一致、または学習コードが書いた source trained の meta) がある
    hash_identity (ファイルの sha256 がその参照先と同一) があれば
    「強い」。参照先に強い証拠が無い hash_identity・置き場の実体が自分自身と同一であること・mtime だけ・run の manifest
    (run_manifest) の記述だけは「弱い」(コードの経路からの推定は由来の確認にしない。2026-11-02 判断)
  - classify_from_meta: 復元で証拠が強ければ種別を採り kind_source "reconstructed"、弱ければ種別は「不明」のまま (kind_source
    "unknown"、kind_basis に理由)。formal_status は reconstructed を meta と同じに扱う (種別・型の前提の条件は同じ。構築専用で
    型の前提が unknown なら formal_ok False)

純粋な部分 (bundle_values / check_bundle_manifest / reconcile / normalize_set / sets_differences / scope_decision /
classify_from_meta / reconstructed_evidence_status / premise_of / formal_status / pin_status_payload) は tests/test_pin_bundle.py と tests/test_model_kind.py で確かめる。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from champions_agent.config import PIN_ALLOW_OTHER_TEAM_ENV, PIN_BUNDLE_ENV

BUNDLE_KIND = "advice_pin_bundle"
BUNDLE_VERSION = 1
MANIFEST_FILE = "manifest.json"
# manifest の files の鍵 → bundle の中のファイル名 (この名前以外は認めない = bundle の外を指せない)
FILES = {
    "rl_policy": "rl_policy.zip",
    "selection_model": "selection_model.pt",
    "selection_emb": "selection_model_emb.json",
    "selection_meta": "selection_model_meta.json",
    "selection_cond": "selection_model_cond.pt",
    "usage_db": "usage_bundle.sqlite3",
    "real_bank": "bank.json",
    "registry": "my_team.json",
}
REQUIRED_FILES = ("rl_policy", "selection_model", "selection_emb", "usage_db", "real_bank", "registry")
OPTIONAL_FILES = ("selection_meta", "selection_cond")
MODEL_KINDS = ("general", "team", "unknown")
KIND_LABEL = {"general": "汎用", "team": "構築専用", "unknown": "不明"}
KIND_SOURCES = ("meta", "reconstructed", "explicit", "unknown")
# 学習履歴から種別を決めた根拠 (meta の provenance。explicit / unknown 以外)
HISTORY_KIND_SOURCES = ("meta", "reconstructed")
# provenance.source: 学習時に書いた (trained、鍵が無い旧い記録も同じ) / 記録から後で復元した (reconstructed、2026-11-02)
PROVENANCE_SOURCES = ("trained", "reconstructed")
# 復元の証拠の種類と、それだけで種別を決めてよい「強い」証拠 (advisor.pin_bundle.reconstructed_evidence_status)
EVIDENCE_KINDS = ("hash_identity", "training_log", "run_manifest", "mtime")
STRONG_EVIDENCE_KINDS = ("hash_identity", "training_log")
# hash_identity が強い証拠になるのは、参照先のモデル自身の証拠 (要素の ref_evidence) にこれがあるときだけ (2026-11-02 判断):
#   training_log = 参照先の保存行と mtime が一致 / trained_meta = 参照先に学習コードが書いた meta (source trained、model_sha16 が
#   参照先と一致、kind_source meta)。学習コードが書いた記録は学習ログと同じ強さとする。記録から復元した弱い meta は含めない
REF_STRONG_EVIDENCE_KINDS = ("training_log", "trained_meta")
SETS_PREMISES = ("fixed", "none", "unknown")
PREMISE_LABEL = {"fixed": "固定型", "none": "前提なし (型に条件づけない)", "unknown": "不明"}
# 学習履歴 (champions_agent.train.train_selection.build_provenance) の kind → manifest の種別
PROVENANCE_KIND_MAP = {"general": "general", "team_finetuned": "team"}
UNVERIFIED_LABEL = "適用範囲未確認 (診断)"
PINNED_REGISTRY_LABEL = "固定した登録を使用中"
EDIT_NOTE = "bundle 使用中は登録の編集が助言に反映されない (助言は bundle の登録の写しを読む。反映するなら bundle を作り直して再起動する)"
TEAM_ADVICE_EXCLUSION = ("advisor.team_advice (meta_top・補完候補) は配備先の DB を読む。操作・選出の試験の経路ではないので今回の"
                         "固定対象から除外 (構築・修理の比較で使う段階で固定対象に入れる)")
# bundle から埋める版の欄 (advisor.data_pin の spec と model spec の鍵)
DATA_SPEC_KEYS = ("usage_db", "usage_snapshot", "bank_path", "bank_sha16")
MODEL_SPEC_KEYS = ("rl_policy", "rl_policy_sha16", "selection_model", "selection_model_sha16")
SPEC_KEYS = DATA_SPEC_KEYS + MODEL_SPEC_KEYS
_PATH_KEYS = ("usage_db", "bank_path", "rl_policy", "selection_model")
SCOPE_STATES = ("ok", "pending", "mismatch", "override")


def _err(msg: str):
    from advisor.data_pin import InputsPinError
    return InputsPinError(msg)


# ------------------------------------------------------------------ 純粋
def bundle_values(man: dict, d) -> dict:
    """manifest → advisor.data_pin の spec の値 (純粋)。パスは bundle の dir の中、sha16 は manifest の値"""
    d = Path(d)
    f = (man or {}).get("files") or {}
    get = lambda k: f.get(k) or {}
    return {"usage_db": str(d / FILES["usage_db"]) if get("usage_db").get("file") else None,
            "usage_snapshot": get("usage_db").get("snapshot_id"),
            "bank_path": str(d / FILES["real_bank"]) if get("real_bank").get("file") else None,
            "bank_sha16": get("real_bank").get("sha16"),
            "rl_policy": str(d / FILES["rl_policy"]) if get("rl_policy").get("file") else None,
            "rl_policy_sha16": get("rl_policy").get("sha16"),
            "selection_model": str(d / FILES["selection_model"]) if get("selection_model").get("file") else None,
            "selection_model_sha16": get("selection_model").get("sha16")}


def check_bundle_manifest(man: Optional[dict], actual: Optional[dict] = None, features_now: Optional[str] = None) -> list:
    """manifest の形と、実際のファイルの sha256 (actual: ファイル名 → sha256 or None。None を渡せばファイルは照合しない) の照合 (純粋)。
    features_now = 今の選出の特徴量の版 (渡せば manifest の版と比べる)。成り立たない理由の list"""
    m = man or {}
    if not m:
        return [f"{MANIFEST_FILE} が無い・読めない"]
    reasons = []
    if m.get("kind") != BUNDLE_KIND:
        reasons.append(f"manifest の kind {m.get('kind')!r} が {BUNDLE_KIND} でない")
    if m.get("version") != BUNDLE_VERSION:
        reasons.append(f"manifest の version {m.get('version')!r} を読めない (読めるのは {BUNDLE_VERSION})")
    files = m.get("files")
    if not isinstance(files, dict):
        return reasons + ["manifest に files が無い"]
    for k in REQUIRED_FILES + OPTIONAL_FILES:
        e = files.get(k)
        if e is None:
            if k in REQUIRED_FILES:
                reasons.append(f"bundle に {k} ({FILES[k]}) の記録が無い")
            continue
        if not isinstance(e, dict) or e.get("file") != FILES[k]:
            reasons.append(f"{k} のファイル名 {(e or {}).get('file') if isinstance(e, dict) else e!r} が {FILES[k]} でない")
            continue
        if not e.get("sha256"):
            reasons.append(f"{k} ({FILES[k]}) の sha256 の記録が無い")
            continue
        if actual is not None:
            got = actual.get(FILES[k])
            if got is None:
                reasons.append(f"bundle に {FILES[k]} が無い")
            elif got != e["sha256"]:
                reasons.append(f"bundle の {FILES[k]} の sha256 {got[:16]} が manifest の {e['sha256'][:16]} と違う")
    ub = files.get("usage_db") or {}
    if ub and not isinstance(ub.get("snapshot_id"), int):
        reasons.append("使用率の snapshot id が manifest に無い")
    reg = files.get("registry") or {}
    if reg and not reg.get("registry_sha16"):
        reasons.append("登録 (my_team) の registry_sha16 が manifest に無い")
    sel = m.get("selection")
    if not isinstance(sel, dict):
        return reasons + ["manifest に selection (選出モデルの種別と適用範囲) が無い"]
    if sel.get("kind") not in MODEL_KINDS:
        reasons.append(f"選出モデルの種別 {sel.get('kind')!r} が {MODEL_KINDS} のどれでもない")
    if sel.get("kind") == "team":
        team = sel.get("team")
        if not isinstance(team, list) or len({str(x) for x in team}) != 6:
            reasons.append(f"構築専用の選出モデルの対応する 6 体が manifest に無い ({team!r})")
    sp = sel.get("sets_premise")
    if isinstance(sp, str) and sp not in SETS_PREMISES:
        reasons.append(f"型の前提 {sp!r} が {SETS_PREMISES} のどれでもない")
    ks = sel.get("kind_source")
    if ks is not None and ks not in KIND_SOURCES:
        reasons.append(f"種別の根拠 kind_source {ks!r} が {KIND_SOURCES} のどれでもない")
    if ks == "unknown" and sel.get("kind") != "unknown":
        reasons.append(f"種別の根拠が無い (kind_source unknown) のに種別が {sel.get('kind')!r}")
    if "formal_ok" in sel and bool(sel.get("formal_ok")) != formal_status(sel)["ok"]:
        reasons.append(f"selection.formal_ok {sel.get('formal_ok')!r} が記録の内容 (種別・根拠・型の前提) から決まる値と違う")
    if not sel.get("features"):
        reasons.append("選出の特徴量の版が manifest に無い")
    emb = files.get("selection_emb") or {}
    if emb.get("sha16") and sel.get("emb_sha16") != emb.get("sha16"):
        reasons.append(f"selection.emb_sha16 {sel.get('emb_sha16')} が埋め込みのファイルの sha16 {emb.get('sha16')} と違う")
    if features_now is not None and sel.get("features") and str(sel["features"]).lower() != str(features_now).lower():
        reasons.append(f"選出の特徴量の版 {sel['features']} (bundle) が今の版 {features_now} と違う (その版の埋め込みでしか読めない)")
    return reasons


def _norm_path(p) -> str:
    return str(Path(p).expanduser().resolve())


def reconcile(bundle_vals: dict, explicit: dict) -> list:
    """bundle の値と個別に明示した値 (advisor.data_pin の spec / model spec の形。None は未指定) の整合 (純粋に近い: パスは resolve で
    正規化する)。違う欄の理由の list"""
    reasons = []
    for k in SPEC_KEYS:
        ev = (explicit or {}).get(k)
        if ev is None or ev == "":
            continue
        bv = (bundle_vals or {}).get(k)
        if k in _PATH_KEYS:
            same = bv is not None and _norm_path(ev) == _norm_path(bv)
        elif k == "usage_snapshot":
            same = bv is not None and int(ev) == int(bv)
        else:
            same = bv is not None and str(ev).lower() == str(bv).lower()
        if not same:
            reasons.append(f"個別に明示した {k}={ev} が bundle の値 {bv} と違う (bundle だけを書くか、値を揃える)")
    return reasons


def normalize_set(row: Optional[dict]) -> dict:
    """型の比較用の形 (純粋): {"item", "ability", "nature", "moves" (ソート済み), "evs" ({stat: 点数}、0 は除く)}"""
    r = row or {}
    evs = {str(k): int(v) for k, v in (r.get("evs") or r.get("points") or {}).items() if v}
    return {"item": r.get("item") or None, "ability": r.get("ability") or None,
            "nature": (str(r["nature"]).lower() if r.get("nature") else None),
            "moves": sorted(str(m) for m in (r.get("moves") or []) if m), "evs": dict(sorted(evs.items()))}


def sets_differences(premise: Optional[dict], actual: Optional[dict]) -> list:
    """学習時の型の前提 (premise: {species_id: 型}) と今の登録の型 (actual) の違い (純粋)。違いの文言の list (空なら一致)"""
    out = []
    act = {k: normalize_set(v) for k, v in (actual or {}).items()}
    for sid, row in sorted((premise or {}).items()):
        want = normalize_set(row)
        got = act.get(sid)
        if got is None:
            out.append(f"{sid}: 登録に型が無い")
            continue
        for f in ("item", "ability", "nature", "moves", "evs"):
            if want[f] != got[f]:
                out.append(f"{sid}.{f}: 学習時 {want[f]} / 登録 {got[f]}")
    return out


def premise_of(sel: Optional[dict]) -> tuple:
    """manifest の selection の型の前提 (純粋) → (前提 "fixed" / "none" / "unknown", 型 {species_id: 型} or None)。
    新しい形: sets_premise が文字列、型は premise_sets。旧い形: sets_premise = {"fixed": True / None, "sets"}。
    fixed でも型が無ければ照合できないので unknown"""
    s = sel or {}
    sp = s.get("sets_premise")
    if isinstance(sp, str):
        premise = sp if sp in SETS_PREMISES else "unknown"
        sets = s.get("premise_sets") if premise == "fixed" else None
    elif isinstance(sp, dict):
        premise = "fixed" if sp.get("fixed") is True else "unknown"
        sets = sp.get("sets") if premise == "fixed" else None
    else:
        premise, sets = "unknown", None
    if premise == "fixed" and not (isinstance(sets, dict) and sets):
        return "unknown", None
    return premise, (dict(sets) if premise == "fixed" else None)


def reconstructed_evidence_status(prov: Optional[dict]) -> dict:
    """復元した provenance (source "reconstructed") の証拠の強さ (純粋。2026-11-02 開発者判断 4)。
    {"ok": 種別を決めてよいか, "kinds": 証拠の種類の list (記録の順、重複なし), "strong": 強い証拠の種類の list, "reason": 決めない理由 or None}。
    強い証拠 (STRONG_EVIDENCE_KINDS):
      - training_log: 学習の記録の保存行のパスがモデルと同じで、保存行の直後の時刻とモデルの mtime が一致 (許容は
        config.PROVENANCE_LOG_MTIME_TOLERANCE_S)
      - hash_identity: モデルのファイルの sha256 が参照先のモデル (汎用モデル) と同一で、かつ参照先のモデル自身に強い証拠
        (要素の ref_evidence に REF_STRONG_EVIDENCE_KINDS = training_log か trained_meta) があるときだけ。参照先が不明 (ref_evidence に
        それが無い・鍵が無い。参照先の meta が記録から復元した弱いもの) なら弱い。置き場の実体が自分自身と同一であることは証拠にしない (コードの経路からの推定は
        由来の確認ではない。2026-11-02 判断)
    弱い証拠: mtime (ファイルの時刻だけ)・run_manifest (構築 run の manifest・Package の記述だけ)・参照先が不明の hash_identity。
    弱い証拠だけなら種別は決めない (「不明」のまま)。
    evidence の各要素は {"kind": EVIDENCE_KINDS のどれか, "detail", "path"} (hash_identity は "ref_evidence": 参照先自身の証拠の種類の
    list も持つ)。知らない kind の要素は数えない"""
    p = prov or {}
    ev = p.get("evidence")
    if not isinstance(ev, list) or not ev:
        return {"ok": False, "kinds": [], "strong": [], "reason": "復元の証拠 (evidence) が無い"}
    kinds, strong, weak_hash = [], [], False
    for e in ev:
        k = e.get("kind") if isinstance(e, dict) else None
        if k not in EVIDENCE_KINDS:
            continue
        if k not in kinds:
            kinds.append(k)
        if k == "hash_identity":
            ref = e.get("ref_evidence")
            ok = isinstance(ref, list) and any(r in REF_STRONG_EVIDENCE_KINDS for r in ref)
            weak_hash = weak_hash or not ok
        else:
            ok = k in STRONG_EVIDENCE_KINDS
        if ok and k not in strong:
            strong.append(k)
    if not strong:
        return {"ok": False, "kinds": kinds, "strong": [],
                "reason": (f"復元の証拠が弱い ({', '.join(kinds) or '読める証拠なし'} だけ。training_log が無い"
                           + ("。hash_identity の参照先のモデル自身に training_log の一致が無い (参照先が不明)" if weak_hash else "")
                           + ")")}
    return {"ok": True, "kinds": kinds, "strong": strong, "reason": None}


def classify_from_meta(meta: Optional[dict], model_sha256: Optional[str], meta_name: Optional[str] = None) -> dict:
    """選出モデルの種別をモデルの実体と結び付いた学習履歴で決める (純粋。2026-11-02 レビュー判断 1)。
    meta = モデルと同じ dir の <stem>_meta.json の中身 (無ければ None)、model_sha256 = モデルのファイルの sha256。
    meta の model_sha16 がモデルの sha256 の先頭 16 桁と一致し、provenance の形が正しいときだけ採用 (kind_source "meta")。
    それ以外は種別 "unknown" (kind_source "unknown")。ファイルの名前は見ない。
    provenance.source が "reconstructed" (記録から復元。2026-11-02) なら reconstructed_evidence_status で証拠が強いときだけ採用し
    kind_source "reconstructed"、弱ければ "unknown" (理由は kind_basis)。source が無ければ trained とみなす。
    戻り値 {"kind", "team", "sets_premise", "premise_sets", "premise_source", "kind_source", "kind_basis", "provenance"}"""
    where = meta_name or "meta"
    unk = lambda why: {"kind": "unknown", "team": None, "sets_premise": "unknown", "premise_sets": None, "premise_source": None,
                       "kind_source": "unknown", "kind_basis": why, "provenance": None}
    if not isinstance(meta, dict):
        return unk(f"モデルと同じ dir に学習履歴の {where} が無い (由来不明)")
    prov = meta.get("provenance")
    if not isinstance(prov, dict):
        return unk(f"{where} に学習履歴 (provenance) が無い (由来不明。2026-11-02 より前の学習)")
    want = str(meta.get("model_sha16") or "").lower()
    got = str(model_sha256 or "")[:16].lower()
    if not want:
        return unk(f"{where} に model_sha16 が無い (モデルの実体と結び付かない)")
    if not got or want != got:
        return unk(f"{where} の model_sha16 {want} がモデルの sha16 {got or '不明'} と違う (別のモデルの記録)")
    source = prov.get("source") or "trained"
    if source not in PROVENANCE_SOURCES:
        return unk(f"{where} の provenance.source {source!r} を読めない ({PROVENANCE_SOURCES} のどれでもない)")
    ev_st = reconstructed_evidence_status(prov) if source == "reconstructed" else None
    if ev_st is not None and not ev_st["ok"]:
        return unk(f"{where} の provenance は記録からの復元 (reconstructed) だが種別を決めない: {ev_st['reason']}")
    kind = PROVENANCE_KIND_MAP.get(prov.get("kind"))
    if kind is None:
        return unk(f"{where} の provenance.kind {prov.get('kind')!r} を読めない ({tuple(PROVENANCE_KIND_MAP)} のどれでもない)")
    team = sorted({str(x) for x in (prov.get("finetune_team") or [])}) or None
    if kind == "team" and (team is None or len(team) != 6):
        return unk(f"{where} の provenance が team_finetuned なのに 6 体の finetune_team が無い ({prov.get('finetune_team')!r})")
    sp = prov.get("sets_premise")
    if sp == "fixed" and isinstance(prov.get("sets"), dict) and prov.get("sets"):
        premise, sets = "fixed", dict(prov["sets"])
    elif sp == "none":
        premise, sets = "none", None
    else:
        premise, sets = "unknown", None
    if ev_st is None:
        basis = (f"{where} の provenance (kind {prov.get('kind')}、学習 {prov.get('trained_at')}、code {str(prov.get('code_commit') or '?')[:12]}、"
                 f"model_sha16 {want} 一致)")
    else:
        basis = (f"{where} の provenance (記録からの復元 reconstructed、kind {prov.get('kind')}、証拠 {', '.join(ev_st['kinds'])} "
                 f"(強い証拠 {', '.join(ev_st['strong'])})、復元 {prov.get('reconstructed_at')}、model_sha16 {want} 一致)")
    keep = {k: prov.get(k) for k in ("kind", "finetune_team", "sets_premise", "general_model_sha16", "trained_at", "data_path",
                                     "code_commit")}
    keep["source"] = source
    if ev_st is not None:
        keep.update(evidence_kinds=ev_st["kinds"], reconstructed_at=prov.get("reconstructed_at"),
                    reconstructed_by=prov.get("reconstructed_by"))
    return {"kind": kind, "team": team if kind == "team" else None, "sets_premise": premise, "premise_sets": sets,
            "premise_source": f"{where} の provenance" if premise == "fixed" else None,
            "kind_source": "meta" if ev_st is None else "reconstructed", "kind_basis": basis, "provenance": keep}


def formal_status(sel: Optional[dict]) -> dict:
    """正式比較に使えるか (純粋)。{"ok": bool, "reason": 使えない理由 or None}。
    種別が meta から決まる (kind_source "meta"、または記録から復元して証拠が強い "reconstructed") か明示 + 根拠 (kind_source
    "explicit" かつ kind_basis あり)、種別が不明でない、構築専用なら型の前提が unknown でない、のときだけ ok。
    reconstructed の証拠の強さは classify_from_meta が判定済み (弱ければ種別が unknown になる)"""
    s = sel or {}
    kind, ks = s.get("kind"), s.get("kind_source")
    if kind not in ("general", "team"):
        why = s.get("kind_basis") if ks == "unknown" else None
        return {"ok": False, "reason": "選出モデルの種別が不明" + (f" ({why})" if why else "")}
    if ks == "explicit":
        if not str(s.get("kind_basis") or "").strip():
            return {"ok": False, "reason": "種別を明示したが根拠 (kind_basis) が無い"}
    elif ks not in HISTORY_KIND_SOURCES:
        return {"ok": False, "reason": "種別の根拠 (kind_source) の記録が無い (学習履歴で決めていない旧い bundle)"}
    if kind == "team" and premise_of(s)[0] == "unknown":
        return {"ok": False, "reason": "構築専用モデルの学習時の型の前提が不明 (型まで一致したことを確かめられない)"}
    return {"ok": True, "reason": None}


def finalize_selection(sel: Optional[dict], meta_cls: dict) -> dict:
    """make_pin_bundle が manifest に書く selection (純粋)。sel = 呼び出し側の値 (tools.advice_mode.selection_profile)、
    meta_cls = classify_from_meta の結果 (bundle に写すモデルと meta から)。
    sel に kind が無い・kind_source が meta / reconstructed / unknown なら meta の判定を採る。kind があれば明示 (kind_source explicit、
    kind_basis は sel の値。無ければ None = formal_ok False)。型の前提は 3 値 (sets_premise) + 型 (premise_sets) に直す。
    meta_classification (meta の判定の要約)・formal_ok・formal_reason を足す"""
    out = dict(sel or {})
    m = dict(meta_cls or {})
    explicit = out.get("kind") is not None and out.get("kind_source") not in HISTORY_KIND_SOURCES + ("unknown",)
    if explicit:
        premise, sets = premise_of(out)
        old = out.get("sets_premise")
        src = out.get("premise_source") or (old.get("source") if isinstance(old, dict) else None)
        out.update(kind_source="explicit", kind_basis=(str(out.get("kind_basis")).strip() or None) if out.get("kind_basis") else None,
                   sets_premise=premise, premise_sets=sets, premise_source=src if premise == "fixed" else None)
        if out.get("kind") != "team":
            out["team"] = None
    else:
        out.update(kind=m.get("kind", "unknown"), team=m.get("team"), sets_premise=m.get("sets_premise", "unknown"),
                   premise_sets=m.get("premise_sets"), premise_source=m.get("premise_source"),
                   kind_source=m.get("kind_source", "unknown"), kind_basis=m.get("kind_basis"))
    out["meta_classification"] = {k: m.get(k) for k in ("kind", "team", "sets_premise", "kind_source", "kind_basis", "provenance")}
    st = formal_status(out)
    out["formal_ok"], out["formal_reason"] = st["ok"], st["reason"]
    return out


def scope_decision(sel: Optional[dict], my_species, confirmed: bool, allow_other: bool = False,
                   set_diffs: Optional[list] = None) -> dict:
    """選出モデルの適用範囲の判定 (純粋)。sel = manifest の selection、my_species = 自分の種族 id の列、confirmed = 6 体が確定したか、
    allow_other = 汎化試験の明示、set_diffs = 型の照合の違い (前提が固定型でないときは None)。
    戻り値 {"state" (ok / pending / mismatch / override), "infer" (推論するか), "kind", "team", "reason", "formal_ok", "formal_reason"}。
    汎用は常に ok。構築専用は 6 体確定前は pending、種族 id の集合 (順番は問わない) か型が違えば mismatch
    (allow_other なら override = 推論するが汎化試験として残す)。型の前提が none なら 6 体だけ、unknown なら 6 体は照合するが
    formal_ok False。種別が不明なら照合できず ok (推論する) で formal_ok False (適用範囲未確認の診断。2026-11-02)"""
    s = sel or {}
    kind = s.get("kind")
    team = sorted({str(x) for x in (s.get("team") or [])})
    st = formal_status(s)
    base = {"kind": kind, "team": team or None, "formal_ok": st["ok"], "formal_reason": st["reason"]}
    if kind == "unknown":
        return dict(base, state="ok", infer=True, reason=f"{st['reason']}。{UNVERIFIED_LABEL}")
    if kind != "team":
        return dict(base, state="ok", infer=True, reason=None)
    mine = sorted({str(x) for x in (my_species or []) if x})
    if not confirmed:
        return dict(base, state="pending", infer=False,
                    reason=f"自分の 6 体が確定していない (確認待ち。構築専用の選出モデル、読めた {len(mine)} 体)")
    why = None
    if set(mine) != set(team):
        missing = sorted(set(team) - set(mine))
        extra = sorted(set(mine) - set(team))
        why = f"構築専用の選出モデルの対応する 6 体と違う (モデルにだけある {missing} / 今の構築にだけある {extra})"
    elif set_diffs:
        why = f"構築専用の選出モデルの学習時の型と登録の型が違う ({'; '.join(set_diffs[:6])}{' ほか' if len(set_diffs) > 6 else ''})"
    if why is None:
        return dict(base, state="ok", infer=True, reason=None)
    if allow_other:
        return dict(base, state="override", infer=True,
                    reason=f"{why}。{PIN_ALLOW_OTHER_TEAM_ENV}=1 により汎化試験として適用する")
    return dict(base, state="mismatch", infer=False, reason=f"{why}。選出モデルの推論を止めた (別モデルへ退避しない)")


def check_allow_value(raw: Optional[str], where: str) -> bool:
    """汎化試験の明示の値 (空・0 = しない、1 = する)。それ以外は ValueError (純粋)"""
    v = (raw or "").strip()
    if v in ("", "0"):
        return False
    if v == "1":
        return True
    raise ValueError(f"{where}: {PIN_ALLOW_OTHER_TEAM_ENV}={raw!r} は 0 / 1 のどちらかで書く")


# ------------------------------------------------------------------ 副作用 (ファイルの読み取り。結果は stat ごとにキャッシュ)
_manifest_cache: dict = {}      # str(dir) → {"stat": ..., "manifest": dict, "sha16": str}
_file_cache: dict = {}          # str(path) → {"stat": ..., "sha256": str}
_value_cache: dict = {}         # (種類, str(path), sha256) → 読み込んだ値


def reset() -> None:
    """読み込み済みの manifest・照合・値を捨てる"""
    _manifest_cache.clear()
    _file_cache.clear()
    _value_cache.clear()


def bundle_dir(environ: Optional[dict] = None) -> Optional[Path]:
    """環境変数の bundle の dir (無ければ None)。相対パスは今のディレクトリから"""
    env = os.environ if environ is None else environ
    raw = (env.get(PIN_BUNDLE_ENV) or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_absolute() else Path.cwd() / p


def active(environ: Optional[dict] = None) -> bool:
    env = os.environ if environ is None else environ
    return bool((env.get(PIN_BUNDLE_ENV) or "").strip())


def allow_other_team(environ: Optional[dict] = None) -> bool:
    """汎化試験の明示 (不正な値は InputsPinError)"""
    env = os.environ if environ is None else environ
    try:
        return check_allow_value(env.get(PIN_ALLOW_OTHER_TEAM_ENV), f"環境変数 {PIN_ALLOW_OTHER_TEAM_ENV}")
    except ValueError as e:
        raise _err(str(e))


def _stat_key(p: Path):
    st = p.stat()
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def load_manifest(d) -> dict:
    """bundle の manifest (形の照合つき。ファイルの sha は照合しない)。無い・読めない・形が違えば InputsPinError"""
    d = Path(d)
    mp = d / MANIFEST_FILE
    try:
        key = _stat_key(mp)
    except OSError:
        raise _err(f"版の固定の bundle {d} に {MANIFEST_FILE} が無い (別のデータへ退避しない)")
    hit = _manifest_cache.get(str(d))
    if hit and hit["stat"] == key:
        return hit["manifest"]
    try:
        raw = mp.read_bytes()
        man = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise _err(f"版の固定の bundle の {mp} を読めない: {e}")
    reasons = check_bundle_manifest(man if isinstance(man, dict) else None)
    if reasons:
        raise _err(f"版の固定の bundle {d} の manifest が不正: " + " / ".join(reasons))
    _manifest_cache[str(d)] = {"stat": key, "manifest": man, "sha16": hashlib.sha256(raw).hexdigest()[:16]}
    return man


def manifest_sha16(d) -> Optional[str]:
    load_manifest(d)
    return (_manifest_cache.get(str(Path(d))) or {}).get("sha16")


def _verified(path: Path, sha256: str, label: str) -> Path:
    """ファイルの sha256 が manifest と一致することを確かめる (stat が前回と同じなら計算し直さない)"""
    try:
        key = _stat_key(path)
    except OSError:
        raise _err(f"版の固定の bundle に {label} ({path}) が無い (別のファイルへ退避しない)")
    hit = _file_cache.get(str(path))
    if not (hit and hit["stat"] == key):
        try:
            got = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as e:
            raise _err(f"版の固定の bundle の {label} ({path}) を読めない: {e}")
        hit = {"stat": key, "sha256": got}
        _file_cache[str(path)] = hit
    if hit["sha256"] != sha256:
        raise _err(f"版の固定の bundle の {label} ({path}) の sha256 {hit['sha256'][:16]} が manifest の {sha256[:16]} と違う")
    return path


def file_path(key: str, environ: Optional[dict] = None, d=None) -> Optional[Path]:
    """bundle の中のファイル (sha256 を照合済み)。記録が None (任意のファイルが元に無かった) なら None。bundle が無効なら InputsPinError"""
    d = Path(d) if d is not None else bundle_dir(environ)
    if d is None:
        raise _err(f"{PIN_BUNDLE_ENV} が無いのに bundle のファイル {key} を読もうとした")
    man = load_manifest(d)
    e = (man.get("files") or {}).get(key)
    if e is None:
        if key in REQUIRED_FILES:
            raise _err(f"版の固定の bundle {d} に {key} の記録が無い")
        return None
    return _verified(d / FILES[key], e["sha256"], key)


def effective_spec(environ: Optional[dict] = None) -> Optional[dict]:
    """bundle から埋めた版 (advisor.data_pin の spec + model spec の鍵)。bundle が無ければ None。
    個別の鍵 (環境変数) と一緒に書いて値が違えば InputsPinError"""
    env = os.environ if environ is None else environ
    d = bundle_dir(env)
    if d is None:
        return None
    from advisor.data_pin import model_spec_from_env, spec_from_env
    vals = bundle_values(load_manifest(d), d)
    explicit = dict(spec_from_env(env))
    explicit.update(model_spec_from_env(env))
    reasons = reconcile(vals, explicit)
    if reasons:
        raise _err(f"版の固定の bundle {d} と個別の鍵が矛盾: " + " / ".join(reasons))
    return vals


def selection_info(environ: Optional[dict] = None) -> Optional[dict]:
    """manifest の selection (選出モデルの種別・対応する構築・特徴量の版・由来)。bundle が無ければ None"""
    d = bundle_dir(environ)
    if d is None:
        return None
    return dict(load_manifest(d).get("selection") or {})


def _json_value(kind: str, key: str, environ: Optional[dict]):
    p = file_path(key, environ)
    if p is None:
        return None, None
    sha = _file_cache[str(p)]["sha256"]
    ck = (kind, str(p), sha)
    if ck not in _value_cache:
        try:
            _value_cache[ck] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise _err(f"版の固定の bundle の {key} ({p}) を JSON として読めない: {e}")
    return _value_cache[ck], p


def selection_emb_store(features: str, environ: Optional[dict] = None) -> Optional[dict]:
    """bundle の機能埋め込みの辞書 (species_id → ベクトル)。bundle が無ければ None。特徴量の版が bundle と違う・ベクトルが空なら
    InputsPinError (生きた埋め込みへ退避しない)"""
    if not active(environ):
        return None
    sel = selection_info(environ) or {}
    if str(sel.get("features") or "").lower() != str(features).lower():
        raise _err(f"選出の特徴量の版 {features} が bundle の版 {sel.get('features')} と違う (その版の埋め込みでしか読めない)")
    raw, p = _json_value("emb", "selection_emb", environ)
    store = ((raw or {}).get("functional") or {}).get("vectors") if isinstance(raw, dict) else None
    if not store:
        raise _err(f"版の固定の bundle の埋め込み {p} に functional.vectors が無い")
    return store


def selection_emb_sha16(environ: Optional[dict] = None) -> Optional[str]:
    """bundle の埋め込みの sha16 (照合済み)。bundle が無ければ None"""
    if not active(environ):
        return None
    p = file_path("selection_emb", environ)
    return _file_cache[str(p)]["sha256"][:16]


def selection_meta_teams(environ: Optional[dict] = None) -> Optional[list]:
    """bundle の選出モデルの meta の学習チーム (種族 id のソート済みタプルの列)。bundle が無ければ None、meta の記録が None なら []"""
    if not active(environ):
        return None
    raw, _p = _json_value("meta", "selection_meta", environ)
    if raw is None:
        return []
    return [tuple(sorted(t)) for t in (raw.get("teams") or [])]


def selection_cond_path(environ: Optional[dict] = None) -> tuple:
    """(bundle が有効か, 条件付きモデルのパス or None)。bundle が無ければ (False, None) = 呼び出し側は従来のパス"""
    if not active(environ):
        return False, None
    return True, file_path("selection_cond", environ)


def registry(environ: Optional[dict] = None) -> Optional[dict]:
    """bundle の登録 (my_team の dict)。bundle が無ければ None。registry_sha16 が manifest と違えば InputsPinError"""
    if not active(environ):
        return None
    raw, p = _json_value("registry", "registry", environ)
    if not isinstance(raw, dict):
        raise _err(f"版の固定の bundle の登録 {p} が dict でない")
    want = ((load_manifest(bundle_dir(environ)).get("files") or {}).get("registry") or {}).get("registry_sha16")
    ck = ("registry_ok", str(p), _file_cache[str(p)]["sha256"], want)
    if ck not in _value_cache:
        from advisor.my_team import registry_sha
        got = registry_sha(raw)
        if got != want:
            raise _err(f"版の固定の bundle の登録 {p} の registry_sha16 {got} が manifest の {want} と違う")
        _value_cache[ck] = True
    return raw


def registry_sets(reg: dict, species_ids) -> dict:
    """登録 (my_team の dict) から、指定した種族 id の型 (normalize_set の形) を作る (副作用: 名前の解決表を読む)。
    種族 id は 種族ID の欄、無ければ日本語名の解決。持ち物・特性・技・性格は Showdown の id、能力ポイントは点数のまま"""
    from tools.evaluate_team import nature_en_of
    from vision.normalize import NameResolver
    keys = {"h": "hp", "a": "atk", "b": "def", "c": "spa", "d": "spd", "s": "spe",
            "hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe"}
    want = {str(s) for s in species_ids or []}
    res = NameResolver()
    out = {}
    toid = lambda x: "".join(ch for ch in str(x or "").lower() if ch.isalnum()) or None
    for ja, e in (reg or {}).items():
        if not isinstance(e, dict):
            continue
        sid = e.get("種族ID") or e.get("species_id")
        if not sid:
            r = res.resolve_species(ja, cutoff=0.85)
            sid = r[1] if r else None
        sid = toid(sid)
        if sid not in want:
            continue
        item = ability = None
        if e.get("持ち物") or e.get("item"):
            ri = res.resolve(e.get("持ち物") or e.get("item"), "items", cutoff=0.8)
            item = toid(ri[1]) if ri else None
        if e.get("特性") or e.get("ability"):
            ra = res.resolve(e.get("特性") or e.get("ability"), "abilities", cutoff=0.8)
            ability = toid(ra[1]) if ra else None
        moves = []
        for mj in (e.get("技") or e.get("moves") or []):
            rm = res.resolve(mj, "moves", cutoff=0.8)
            if rm:
                moves.append(toid(rm[1]))
        nat = nature_en_of(e.get("性格") or e.get("nature"))
        evs = {keys[str(k).lower()]: int(v) for k, v in (e.get("能力ポイント") or e.get("evs") or {}).items()
               if str(k).lower() in keys}
        out[sid] = normalize_set({"item": item, "ability": ability, "nature": nat, "moves": moves, "evs": evs})
    return out


def premise_sets(sel: dict) -> Optional[dict]:
    """manifest の学習時の型の前提 ({species_id: 型})。固定型でない (none)・不明 (unknown) なら None (純粋)"""
    premise, sets = premise_of(sel)
    return sets if premise == "fixed" else None


def bundle_set_differences(environ: Optional[dict] = None) -> Optional[list]:
    """構築専用モデルの学習時の型の前提と bundle の登録の型の違い (前提が固定型でなければ None)。bundle の中身は不変なので 1 回だけ計算する"""
    sel = selection_info(environ) or {}
    prem = premise_sets(sel)
    if prem is None:
        return None
    reg = registry(environ)
    d = bundle_dir(environ)
    ck = ("set_diffs", str(d), manifest_sha16(d))
    if ck not in _value_cache:
        _value_cache[ck] = sets_differences(prem, registry_sets(reg, list(prem)))
    return list(_value_cache[ck])


def selection_scope(my_species, confirmed: bool, environ: Optional[dict] = None) -> Optional[dict]:
    """bundle の選出モデルを今の構築に適用してよいか (scope_decision)。bundle が無ければ None"""
    if not active(environ):
        return None
    sel = selection_info(environ) or {}
    diffs = None
    if sel.get("kind") == "team" and confirmed and set(str(x) for x in my_species or []) == set(sel.get("team") or []):
        diffs = bundle_set_differences(environ)
    out = scope_decision(sel, my_species, confirmed, allow_other_team(environ), diffs)
    out["origin"] = sel.get("source_id")
    return out


def verify_bundle(d=None, explicit: Optional[dict] = None, environ: Optional[dict] = None,
                  features_now: Optional[str] = None) -> dict:
    """bundle 全体の照合 (副作用: 全ファイルの読み取り。起動時の検査)。manifest の形、全ファイルの sha256、個別の鍵との整合
    (explicit: data_pin の spec / model spec の形。None なら environ の環境変数)、特徴量の版、登録の registry_sha16。
    合わなければ InputsPinError (理由をまとめる)。戻り値は要約 {"dir", "manifest_sha16", "files": {鍵: sha16 or None},
    "usage_snapshot", "selection": {...}, "set_diffs", "values"}"""
    env = os.environ if environ is None else environ
    d = Path(d) if d is not None else bundle_dir(env)
    if d is None:
        raise _err(f"{PIN_BUNDLE_ENV} が無い")
    if not d.is_absolute():
        d = Path.cwd() / d
    man = load_manifest(d)
    actual = {}
    for k in REQUIRED_FILES + OPTIONAL_FILES:
        if (man["files"].get(k)) is None:
            continue
        p = d / FILES[k]
        try:
            actual[FILES[k]] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            actual[FILES[k]] = None
    if features_now is None:
        try:
            from champions_agent.agent.selection_dispatch import features_version
            features_now = features_version()
        except Exception:
            features_now = None
    reasons = check_bundle_manifest(man, actual, features_now)
    reasons += meta_kind_consistency(man.get("selection") or {}, d, actual.get(FILES["selection_model"]))
    vals = bundle_values(man, d)
    if explicit is None:
        from advisor.data_pin import model_spec_from_env, spec_from_env
        explicit = dict(spec_from_env(env))
        explicit.update(model_spec_from_env(env))
    reasons += reconcile(vals, explicit)
    if reasons:
        raise _err(f"版の固定の bundle {d} を使えない: " + " / ".join(reasons))
    benv = dict(env)
    benv[PIN_BUNDLE_ENV] = str(d)
    reg = registry(benv)            # registry_sha16 の照合
    _json_value("emb", "selection_emb", benv)
    sel = dict(man.get("selection") or {})
    diffs = None
    if sel.get("kind") == "team" and premise_sets(sel) is not None:
        diffs = sets_differences(premise_sets(sel), registry_sets(reg, list(premise_sets(sel))))
    return {"dir": str(d), "manifest_sha16": manifest_sha16(d),
            "files": {k: ((man["files"].get(k) or {}).get("sha16")) for k in REQUIRED_FILES + OPTIONAL_FILES},
            "usage_snapshot": vals["usage_snapshot"], "selection": sel, "set_diffs": diffs, "values": vals,
            "code": man.get("code"), "showdown": man.get("showdown"), "regulation": man.get("regulation"),
            "formal": formal_status(sel)}


def meta_kind_consistency(sel: dict, d, model_sha256: Optional[str]) -> list:
    """種別を meta で決めた bundle (kind_source "meta" / 復元の "reconstructed") で、bundle の meta の写しとモデルから決め直した種別・6 体・型の前提が
    manifest と同じか (副作用: bundle の meta の読み取り)。違う理由の list (manifest の書き換え・別のモデルの meta を防ぐ)"""
    if (sel or {}).get("kind_source") not in HISTORY_KIND_SOURCES:
        return []
    p = Path(d) / FILES["selection_meta"]
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"種別を meta で決めた bundle なのに {FILES['selection_meta']} を読めない"]
    c = classify_from_meta(meta, model_sha256, FILES["selection_meta"])
    want = (sel.get("kind"), sorted(sel.get("team") or []) or None) + premise_of(sel)
    got = (c["kind"], c["team"], c["sets_premise"], c["premise_sets"])
    if want != got:
        return [f"manifest の選出モデルの種別 {want[0]} / 6 体 {want[1]} / 型の前提 {want[2]} が bundle の meta の判定 "
                f"{got[0]} / {got[1]} / {got[2]} と違う ({c['kind_basis']})"]
    return []


def runtime_summary(environ: Optional[dict] = None) -> Optional[dict]:
    """version 行の runtime に残す bundle の要約 (例外を出さない)。bundle が無ければ None。
    formal_ok = 正式比較に使えるか (False は 適用範囲未確認 の診断。tools.real_accept の群の照合で判定不能にする)"""
    env = os.environ if environ is None else environ
    if not active(env):
        return None
    d = bundle_dir(env)
    try:
        sel = selection_info(env) or {}
        st = formal_status(sel)
        return {"dir": str(d), "manifest_sha16": manifest_sha16(d), "selection_kind": sel.get("kind"),
                "selection_team": sel.get("team"), "selection_origin": sel.get("source_id"),
                "selection_kind_source": sel.get("kind_source"), "selection_sets_premise": premise_of(sel)[0],
                "formal_ok": st["ok"], "formal_reason": st["reason"],
                "allow_other_team": allow_other_team(env), "error": None}
    except Exception as e:
        return {"dir": str(d), "manifest_sha16": None, "selection_kind": None, "selection_team": None,
                "selection_origin": None, "selection_kind_source": None, "selection_sets_premise": None,
                "formal_ok": False, "formal_reason": "bundle を読めない", "allow_other_team": None, "error": str(e)[:300]}


def describe(summary: dict) -> str:
    """起動ログ・--freeze-bundle の 1 行の説明 (純粋)"""
    sel = summary.get("selection") or {}
    kind = sel.get("kind")
    premise = premise_of(sel)[0]
    txt = f"{summary.get('dir')} (manifest sha16 {summary.get('manifest_sha16')}, 使用率 snapshot {summary.get('usage_snapshot')}"
    txt += f", 選出モデル {KIND_LABEL.get(kind, kind)} (根拠 {sel.get('kind_source') or '記録なし'})"
    if kind == "team":
        txt += f" 対応 6 体 {sel.get('team')}"
        diffs = summary.get("set_diffs")
        if premise == "none":
            txt += ", 型の前提 なし (6 体だけ照合)"
        elif premise == "unknown" or diffs is None:
            txt += ", 型の前提 不明 (照合しない)"
        else:
            txt += (", 型の前提 一致" if not diffs else f", 型の前提 不一致 {len(diffs)} 件 (この構築では推論しない)")
    txt += f", 由来 {sel.get('source_id') or 'deployed'}"
    st = formal_status(sel)
    txt += ", 正式比較 可" if st["ok"] else f", {UNVERIFIED_LABEL}: {st['reason']}"
    return txt + ")"


def pin_status_payload(summary: Optional[dict], error: Optional[str] = None) -> dict:
    """画面 (助言欄の上部・登録の編集) に出す bundle の状態 (純粋)。summary = runtime_summary の戻り値 (bundle が無ければ None)。
    server は接続時に 'pin_status' で送り、登録の画面 (my_team_data / my_team_detail) にも添える。
    {"active", "dir_name", "manifest_sha16", "formal_ok", "formal_reason", "kind", "text", "edit_note"}"""
    if not summary:
        return {"active": False, "dir_name": None, "manifest_sha16": None, "formal_ok": None, "formal_reason": None,
                "kind": None, "text": None, "edit_note": None}
    name = Path(str(summary.get("dir") or "")).name or str(summary.get("dir"))
    err = error or summary.get("error")
    ok = bool(summary.get("formal_ok")) and not err
    text = f"{PINNED_REGISTRY_LABEL} (bundle {name}、manifest sha16 {summary.get('manifest_sha16') or '不明'})"
    if err:
        text += f" ⚠ bundle を読めない: {err}"
    if not ok:
        text += f" / {UNVERIFIED_LABEL}: {summary.get('formal_reason') or '不明'}"
    return {"active": True, "dir_name": name, "manifest_sha16": summary.get("manifest_sha16"), "formal_ok": ok,
            "formal_reason": None if ok else (summary.get("formal_reason") or err), "kind": summary.get("selection_kind"),
            "text": text, "edit_note": EDIT_NOTE}


def pin_status(environ: Optional[dict] = None) -> dict:
    """今の bundle の画面表示 (副作用: manifest の読み取り。例外を出さない)"""
    return pin_status_payload(runtime_summary(environ))


def startup_exclusion_line(environ: Optional[dict] = None) -> Optional[str]:
    """server の起動ログの固定対象の除外の 1 行 (bundle を使うときだけ。無ければ None)"""
    return f"版の固定 (bundle) の対象外: {TEAM_ADVICE_EXCLUSION}" if active(environ) else None
