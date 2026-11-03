"""引き継ぎ評価 I-10: ログ・版固定・再生・実験管理の記録契約の試験 (2026-10-31 段階 A、docs/INHERITANCE_EVALUATION_PLAN_20261031.md §5 I-10)。

    python -m tools.inheritance_eval.record_contract --src-repo <本体> --out <本体>/logs/inheritance_eval/phaseA_20261031/I-10

既存 (battle_logger / advisor.versions / advisor.data_pin / tools.inputs_pin / tools.advice_replay / tools.real_accept /
tools.team_build.registry) と、最小記録 (tools.inheritance_eval.minimal_record = JSONL + 小さな manifest + 明示した経路) を、
同じ合成の入力で試験 1〜8 にかける。すべて空の一時ディレクトリで行い、本体の原本 (使用率 DB・実戦バンク・登録チーム) は
読み取り専用で読むだけ (前後の sha256 を manifest に残す)。勝率の改善は測らない。

  1 保存 → 復元: 観測状態・推奨・表示イベントを保存し、空のディレクトリへ写して復元。記録した意味の欠落をフィールド単位で数える
    (状態の欄ごとに、evaluate が読んだ欄か (_AccessDict で実測) を付ける)
  2 原本 DB の更新: DB の写しに新しい snapshot を足しても、固定した版の再生がそれを読まない
  3 モデルの欠損: 固定した RL 方策・選出モデルを欠損させたとき、黙って別のモデルへ退避しない
  4 hash 不一致: 実戦バンク・登録チーム・ピンの sha が指定と違えば止まる
  5 途中行の破損: 読み手が落ちずにその行を数えて報告する
  6 途中停止・再開: ファイルが分かれる / 追記される場合の読み取り
  7 指定版が無い: usage_snapshot / 登録の sha / git commit が存在しない値のとき、最新へ黙って退避しない
  8 集計の検出: 既知の値の fixture で集計が一致するか、誤った勝敗・候補・版・費用を検出できるか、失敗を成功数から消さないか
合成の観測状態は tests/fixtures/inheritance/i10_state.json (make_i10_state.py が vision.state.BattleStateV2.to_dict で作った形)。
純粋な部分 (flatten / field_loss / count_rows / compare_expected / meas) は tests/test_inheritance_record.py。
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from champions_agent.config import (INH_I10_CODE_COMMIT, INH_I10_COMMIT_MISSING, INH_I10_OUT_SUBDIR, INH_I10_RUN_ID,
                                    INH_I10_SHA_MISSING, INH_I10_SNAPSHOT_MISSING, INH_I10_TEST_ID, INH_I10_TIME_BUDGET_SEC)
from tools.inheritance_eval import minimal_record as MR

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURE_STATE = REPO / "tests" / "fixtures" / "inheritance" / "i10_state.json"
T0 = 1_792_000_000.0
EXISTING_MODULES = ("battle_logger", "advisor.versions", "advisor.data_pin", "tools.inputs_pin", "tools.advice_replay")
AGG_MODULES = ("tools.real_accept", "tools.analyze_battles")
MINIMAL_MODULES = ("tools.inheritance_eval.minimal_record",)
BOUNDARY = "合成 fixture (記録契約の試験)。隠れ情報・後から判明した情報は使わない"
LOSS = ("missing", "changed")


# ------------------------------------------------------------------ 純粋: 欄の比較
def flatten(obj, prefix: str = "") -> dict:
    """入れ子の dict / list を葉の経路 → 値にする (純粋)。list の要素は [i]、空の dict / list は葉として残す"""
    out = {}
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)) and obj:
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = list(obj) if isinstance(obj, tuple) else obj
    return out


def norm_path(p: str) -> str:
    """経路の添字を [] に揃える (純粋)"""
    return re.sub(r"\[\d+\]", "[]", p)


def is_empty(v) -> bool:
    """情報を持たない値 (純粋): None / False / "" / [] / {}。0 は値として扱う"""
    return v is None or v is False or v == "" or v == [] or v == {}


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def _ancestors(n: str) -> list:
    parts = re.split(r"(?=\.)|(?=\[\])", n)
    return ["".join(parts[:i]).lstrip(".") for i in range(1, len(parts))]


def use_of(n: str, reads: dict) -> str:
    """欄 (正規化した経路) を evaluate が読んだか (純粋): read (鍵で読んだ) / iter (入れ物を走査した) / container (祖先だけ読んだ) /
    unread。reads = {"read": set, "iter": set}"""
    if n in reads.get("read", ()):
        return "read"
    if n in reads.get("iter", ()):
        return "iter"
    if any(a in reads.get("read", ()) or a in reads.get("iter", ()) for a in _ancestors(n)):
        return "container"
    return "unread"


def field_loss(orig: dict, restored: dict, reads: Optional[dict] = None) -> dict:
    """保存前の状態と復元した状態の欄ごとの比較 (純粋)。status: same / empty_equiv (どちらも情報なし) / zero_equiv (0 と欄なし) /
    missing (復元に無い) / changed (値が違う) / added (復元にだけある)。戻り値 {"rows", "counts", "lost": [正規化した経路],
    "lost_used": [evaluate が読んだ欄 (read / iter) の欠落], "lost_by_use": {use: 件数}}"""
    reads = reads or {}
    fo, fr = flatten(orig), flatten(restored)
    rows = []
    for p, v in fo.items():
        if p not in fr:
            st = "empty_equiv" if is_empty(v) else ("zero_equiv" if _same(v, 0) else "missing")
        elif _same(v, fr[p]):
            st = "same"
        elif is_empty(v) and is_empty(fr[p]):
            st = "empty_equiv"
        else:
            st = "changed"
        rows.append({"path": p, "norm": norm_path(p), "status": st, "use": use_of(norm_path(p), reads),
                     "orig": v, "restored": fr.get(p, "<なし>")})
    for p in fr:
        if p not in fo:
            rows.append({"path": p, "norm": norm_path(p), "status": "empty_equiv" if is_empty(fr[p]) else "added",
                         "use": use_of(norm_path(p), reads), "orig": "<なし>", "restored": fr[p]})
    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lost = [r for r in rows if r["status"] in LOSS]
    by_use: dict = {}
    for r in lost:
        by_use[r["use"]] = by_use.get(r["use"], 0) + 1
    return {"rows": rows, "counts": counts, "lost": sorted({r["norm"] for r in lost}),
            "lost_used": sorted({r["norm"] for r in lost if r["use"] in ("read", "iter")}), "lost_by_use": by_use,
            "n_fields": len(fo)}


# battle_logger._compact_state の欄の名前 (状態の欄 → 簡約状態の欄)。名前が同じ欄は載せない
COMPACT_NAMES = {"species_id": "species", "species_ja": "ja", "hp_percent": "hp", "hp_current": "hp_raw[]", "hp_max": "hp_raw[]",
                 "is_mega": "mega", "item_id": "item", "ability_id": "ability", "revealed_moves": "revealed", "is_picked": "picked",
                 "species_guess": "guess", "species_candidates": "candidates", "active_index": "active"}


def recorded_in(norm: str, compact_norms: set) -> bool:
    """状態の欄 (正規化した経路) が簡約状態 (advice 行の state) に記録されているか (純粋)。最後の鍵だけ COMPACT_NAMES で読み替え、
    その経路か、その経路を祖先に持つ欄があれば記録あり"""
    head, _, last = norm.rpartition(".")
    m = re.match(r"^([^\[]+)(.*)$", last)
    key, rest = (m.group(1), m.group(2)) if m else (last, "")
    mapped = COMPACT_NAMES.get(key, key) + rest
    want = f"{head}.{mapped}" if head else mapped
    return any(c == want or c.startswith(want + ".") or c.startswith(want + "[") for c in compact_norms)


def perturb(v):
    """出力への影響を見るための別の値 (純粋): 真偽は反転、整数は 1 (1 なら 2)、文字列は別の場面名、ほかは None"""
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return 1 if v != 1 else 2
    if isinstance(v, float):
        return v + 1.0
    if isinstance(v, str):
        return "move_select" if v != "move_select" else "command"
    return None


def set_paths(state: dict, norm: str, fn) -> int:
    """state の中で正規化した経路 norm に当たる葉を fn(値) に置き換える (state を書き換える)。置き換えた数"""
    parts = re.findall(r"[^.\[\]]+|\[\]", norm)
    n = 0

    def walk(obj, i):
        nonlocal n
        p, last = parts[i], i == len(parts) - 1
        if p == "[]":
            if not isinstance(obj, list):
                return
            for j in range(len(obj)):
                if last:
                    obj[j] = fn(obj[j])
                    n += 1
                else:
                    walk(obj[j], i + 1)
        elif isinstance(obj, dict) and p in obj:
            if last:
                obj[p] = fn(obj[p])
                n += 1
            else:
                walk(obj[p], i + 1)
    walk(state, 0)
    return n


def advice_sig(adv: Optional[dict]) -> dict:
    """推奨の比較の形 (純粋): ok・第一候補・候補と得点 (6 桁)・退避の理由"""
    a = adv or {}
    return {"ok": a.get("ok"), "best": [(a.get("best") or {}).get("kind"), (a.get("best") or {}).get("id")],
            "actions": [[x.get("kind"), x.get("id"), round(float(x.get("score") or 0.0), 6)] for x in a.get("actions") or []],
            "fallback": a.get("scoring_fallback")}


# ------------------------------------------------------------------ 純粋: 独立の数え方と照合
def count_rows(records: Iterable) -> dict:
    """1 本のログの行を型ごとに数える (純粋。tools.real_accept / battle_logger の集計とは別に、行の型と欄だけで数える)"""
    out = {"n_advice": 0, "n_failed": 0, "n_ok": 0, "n_evaluated": 0, "n_cache": 0, "n_eval_unknown": 0, "n_inputs_pin_rows": 0,
           "n_suppressed": 0, "n_decision_rows": 0, "outcomes": [], "git_commits": [], "n_version_rows": 0}
    for r in records:
        typ = r.get("type")
        if typ == "advice" and r.get("kind") == "battle":
            out["n_advice"] += 1
            if (r.get("advice") or {}).get("ok") is False:
                out["n_failed"] += 1
            else:
                out["n_ok"] += 1
            ev = r.get("evaluated")
            key = "n_evaluated" if ev is True else "n_cache" if ev is False else "n_eval_unknown"
            out[key] += 1
        elif typ == "inputs_pin_error":
            out["n_inputs_pin_rows"] += 1
            out["n_suppressed"] += int(r.get("n_suppressed") or 0)
        elif typ == "decision" and r.get("t_decided") is not None:
            out["n_decision_rows"] += 1
        elif typ == "outcome":
            out["outcomes"].append(r.get("outcome"))
        elif typ == "version":
            out["n_version_rows"] += 1
            g = (r.get("rules") or {}).get("git_commit")
            if g not in out["git_commits"]:
                out["git_commits"].append(g)
    return out


def compare_expected(expected: dict, got: dict) -> list:
    """期待値と集計の値の食い違い (純粋)。期待値の鍵だけ比べる。[{"key", "expected", "got"}]"""
    return [{"key": k, "expected": v, "got": got.get(k, "<なし>")} for k, v in expected.items() if got.get(k, "<なし>") != v]


def meas(case_id: str, asset: str, category: str, inp, expected, expected_source: str, result: str, reason: str,
         elapsed: Optional[float], split: str = "diag", **extra) -> dict:
    """measurements.jsonl の 1 行 (純粋。共通規約の欄)"""
    assert result in ("pass", "fail", "unexecuted", "timeout", "excluded"), result
    return {"test_id": INH_I10_TEST_ID, "case_id": case_id, "asset": asset, "category": category, "input": inp,
            "expected": expected, "expected_source": expected_source, "split": split, "result": result, "reason": reason,
            "elapsed_sec": None if elapsed is None else round(float(elapsed), 3), **extra}


# ------------------------------------------------------------------ evaluate が読む欄の実測
def _join(path: str, k) -> str:
    return f"{path}.{k}" if path else str(k)


class _AccessDict(dict):
    """鍵の読み (get / [] / in) と走査 (items / values / keys / iter) を記録する dict (evaluate の入力の欄の実測用)"""

    def __init__(self, d: dict, path: str, log: dict):
        super().__init__()
        self._p, self._log = path, log
        for k, v in d.items():
            dict.__setitem__(self, k, _wrap(v, _join(path, k), log))

    def _hit(self, k, kind: str = "read") -> None:
        self._log.setdefault(kind, set()).add(norm_path(_join(self._p, k)))

    def __getitem__(self, k):
        self._hit(k)
        return dict.__getitem__(self, k)

    def get(self, k, default=None):
        self._hit(k)
        return dict.get(self, k, default)

    def __contains__(self, k):
        self._hit(k)
        return dict.__contains__(self, k)

    def _all(self):
        for k in dict.keys(self):
            self._hit(k, "iter")

    def items(self):
        self._all()
        return dict.items(self)

    def values(self):
        self._all()
        return dict.values(self)

    def keys(self):
        self._all()
        return dict.keys(self)

    def __iter__(self):
        self._all()
        return dict.__iter__(self)

    def __deepcopy__(self, memo):
        return _AccessDict({k: copy.deepcopy(dict.__getitem__(self, k), memo) for k in dict.keys(self)}, self._p, self._log)


class _AccessList(list):
    def __init__(self, xs, path: str, log: dict):
        super().__init__(_wrap(x, f"{path}[]", log) for x in xs)
        self._p, self._log = path, log

    def __deepcopy__(self, memo):
        return _AccessList([copy.deepcopy(x, memo) for x in self], self._p, self._log)


def _wrap(v, path: str, log: dict):
    if isinstance(v, dict) and not isinstance(v, _AccessDict):
        return _AccessDict(v, path, log)
    if isinstance(v, list) and not isinstance(v, _AccessList):
        return _AccessList(v, path, log)
    return v


# ------------------------------------------------------------------ 副作用: 実行の部品
def sha256_full(path) -> Optional[str]:
    return MR.sha256_file(path)


@contextmanager
def _pinned(db, snap, bank, bank_sha, registry):
    from advisor import data_pin as DP
    DP.set_override(usage_db=db, usage_snapshot=snap, bank_path=bank, bank_sha16=bank_sha, team_registry=registry)
    try:
        yield
    finally:
        DP.clear_override()


@contextmanager
def _versions(doc: dict):
    """battle_logger が読む版の記録を、事前に固定した doc にする (本体のモデル・登録を読まない)"""
    from advisor import versions as V
    orig = (V.runtime_versions, V.rl_loaded_now)
    V.runtime_versions = lambda refresh=False: copy.deepcopy(doc)
    V.rl_loaded_now = lambda: None
    try:
        yield
    finally:
        V.runtime_versions, V.rl_loaded_now = orig


_RES: list = []


def _resolver():
    if not _RES:
        from vision.normalize import NameResolver
        _RES.append(NameResolver())
    return _RES[0]


def _evaluate(state: dict, mode: str, reads: Optional[dict] = None) -> dict:
    from advisor.engine import evaluate
    st = copy.deepcopy(state)
    if reads is not None:
        st = _wrap(st, "", reads)
    return evaluate(st, _resolver(), rl_blend_weight=0, scoring_mode=mode, display_search=False)


def _read_jsonl(path: Path) -> list:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


class Ctx:
    """試験の共通の入力: 一時ディレクトリ・ピン (選出モデル・使用率の bundle・バンクの写し)・固定した版の記録"""

    def __init__(self, src_repo: Path, tmp_root: Optional[Path]):
        self.src = Path(src_repo)
        self.root = Path(tempfile.mkdtemp(prefix="inh_i10_", dir=tmp_root))
        self.src_db = self.src / "champions_agent" / "data" / "db" / "champions.sqlite3"
        self.src_bank = self.src / "logs" / "real_opponents" / "bank.json"
        self.src_team = self.src / "config" / "my_team.json"
        self.state = json.loads(FIXTURE_STATE.read_text(encoding="utf-8"))
        self.state["now"] = T0
        self.notes: list = []

    def originals(self) -> dict:
        return {str(p): sha256_full(p) for p in (self.src_db, self.src_bank, self.src_team)}

    def setup(self) -> None:
        from advisor import versions as V
        from tools import inputs_pin as IP
        models = self.root / "models"
        models.mkdir()
        for n in ("battle_policy_balance_ema.zip", "battle_policy_balance_best.zip", "battle_policy_balance.zip"):
            (models / n).write_bytes(f"synthetic-{n}".encode())
        self.models = models
        sel = self.root / "sel_src"
        sel.mkdir()
        (sel / IP.PIN_MODEL_FILE).write_bytes(b"synthetic-selection-model")
        self.pin = self.root / "pin"
        self.pin.mkdir()
        self.pin_man = IP.make_inputs_pin(self.pin, sel / IP.PIN_MODEL_FILE, src_db=self.src_db, bank=self.src_bank)
        self.snap = self.pin_man["usage_bundle"]["snapshot_id"]
        self.bundle = self.pin / IP.BUNDLE_FILE
        self.bank = self.pin / IP.BANK_FILE
        self.bank_sha16 = self.pin_man["real_bank"]["sha16"]
        self.registry = json.loads(self.src_team.read_text(encoding="utf-8"))
        rl = models / "battle_policy_balance_ema.zip"
        self.doc = V.summarize_versions(
            None, str(self.pin / IP.PIN_MODEL_FILE), V.sha256_file(self.pin / IP.PIN_MODEL_FILE), None, None,
            ["lopunny", "slurpuff", "baxcalibur"], str(rl), V.sha256_file(rl), None, None, "team-sha-i10", INH_I10_CODE_COMMIT,
            V.sha256_file(REPO / "advisor" / "data" / "dex.json"), V.sha256_file(REPO / "advisor" / "data" / "move_effects.json"),
            "v1", embedding_sha=V.sha256_file(V.EMBEDDING_PATH), usage_snapshot=self.snap, usage_snapshot_source="pinned",
            scoring=V.scoring_identity(), team_registry=self.registry, registry_required=True,
            real_bank={"path": str(self.bank), "sha16": self.bank_sha16, "pinned": True})
        self.doc["collected_at"] = T0

    def pinned(self, db=None, snap="same", bank=None, bank_sha="same", registry="same"):
        return _pinned(db or self.bundle, self.snap if snap == "same" else snap, bank or self.bank,
                       self.bank_sha16 if bank_sha == "same" else bank_sha, self.registry if registry == "same" else registry)

    def tmp(self, name: str) -> Path:
        p = self.root / name
        p.mkdir(parents=True, exist_ok=False)
        return p


# ------------------------------------------------------------------ 試験 1: 保存 → 復元
EVENTS_FIRED = ["move_player_closecombat"]
SHOWN_AT = T0 + 0.4
MODES = ("legacy", "transition")


def _log_existing(ctx: Ctx, log_dir: Path, adv: dict) -> tuple:
    """既存のロガーで 観測状態 (scene)・表示イベント (events / display)・推奨 (advice) を書く。(ログの経路, advice_id)"""
    from battle_logger import BattleLogger
    st = copy.deepcopy(ctx.state)
    with _versions(ctx.doc):
        lg = BattleLogger(log_dir=log_dir, guess_prob_fn=None)
        lg.on_frame(st, [])
        st_ev = copy.deepcopy(st)
        st_ev["events"] = [{"ts": T0, "source": "log", "text": "ミミロップの インファイト!", "event": EVENTS_FIRED[0]}]
        lg.on_frame(st_ev, list(EVENTS_FIRED))
        sid = lg.on_advice({"ok": True, "recommend": [{"species": "lopunny"}]}, "selection", st)
        lg.on_selection_record(sid, {"candidates": [], "opp_pick_pred": {"bank": {"sha256": ctx.bank_sha16}}})
        aid = lg.on_advice(copy.deepcopy(adv), "battle", st, evaluated=True, t_eval=T0, ev_infer=None)
        lg.on_display(aid, SHOWN_AT, kind="battle")
        path = lg._file
        lg.close()
    return path, aid


def t1(ctx: Ctx) -> tuple:
    """戻り値 (measurements, 詳細)"""
    from tools import inputs_pin as IP
    from tools.advice_replay import replay_state
    out, det = [], {}
    t = time.time()
    reads: dict = {}
    with ctx.pinned():
        orig = {m: _evaluate(ctx.state, m, reads) for m in MODES}
        orig = {m: _evaluate(ctx.state, m) for m in MODES}          # 読みの記録なしで同じ入力 (比較の基準)
        a_dir = ctx.tmp("t1_existing_write")
        log_path, aid = _log_existing(ctx, a_dir, orig["transition"])
    det["reads"] = {k: sorted(v) for k, v in reads.items()}
    # 空のディレクトリへ写し、そこからだけ復元する
    b = ctx.tmp("t1_existing_restore")
    shutil.copy2(log_path, b / log_path.name)
    shutil.copytree(ctx.pin, b / "pin")
    shutil.rmtree(a_dir)
    recs = _read_jsonl(b / log_path.name)
    ver = [r for r in recs if r.get("type") == "version"]
    adv_row = next(r for r in recs if r.get("type") == "advice" and r.get("kind") == "battle")
    disp = [r for r in recs if r.get("type") == "display"]
    evs = [r for r in recs if r.get("type") == "events"]
    pin_reasons = IP.verify_pin(b / "pin")
    ds = IP.DataSwitch("restore", local=IP.local_data(db=b / "pin" / IP.BUNDLE_FILE, bank=b / "pin" / IP.BANK_FILE))
    try:
        plan = ds.plan(log_path.name, recs, adv_row.get("t"))
        restored, info = replay_state(adv_row, _resolver())
        rest = {m: _evaluate(restored, m) for m in MODES} if plan["ok"] else {}
    finally:
        ds.close()
    loss = field_loss(ctx.state, restored, reads)
    det["existing"] = {"log": log_path.name, "plan": {k: plan.get(k) for k in ("ok", "reasons", "notes", "usage_snapshot", "bank_sha16")},
                       "pin_reasons": pin_reasons, "loss": loss, "info": info,
                       "bytes_file": (b / log_path.name).stat().st_size,
                       "bytes_advice_row": len(json.dumps(adv_row, ensure_ascii=False).encode()) + 1,
                       "n_rows": len(recs), "row_types": [r.get("type") for r in recs]}
    el = time.time() - t
    same_adv = {m: advice_sig(orig[m]) == advice_sig(rest.get(m)) for m in MODES}
    det["existing"]["same_advice"] = same_adv
    det["existing"]["advice_diff"] = {m: {"orig": advice_sig(orig[m]), "restored": advice_sig(rest.get(m))}
                                      for m in MODES if not same_adv[m]}
    inp = "fixture i10_state.json → BattleLogger (scene / events / selection / selection_record / advice / display) → 空 dir へ写す → " \
          "verify_pin + DataSwitch(restore) + advice_replay.replay_state → evaluate"
    out.append(meas("t1_existing_restore_plan", "tools.inputs_pin / advice_replay", "合成結果", inp,
                    "ピン照合 0 件・復元計画 ok・当時の snapshot / バンク", "§5 I-10 (版を固定して空 dir から復元)",
                    "pass" if (not pin_reasons and plan["ok"] and plan["usage_snapshot"] == ctx.snap) else "fail",
                    f"pin {pin_reasons or 'ok'} / plan ok={plan['ok']} reasons={plan['reasons']} snapshot={plan['usage_snapshot']}", el))
    compact_norms = {norm_path(p) for p in flatten(adv_row.get("state") or {})}
    for r in loss["rows"]:
        r["recorded"] = recorded_in(r["norm"], compact_norms)
    rec_lost = sorted({r["norm"] for r in loss["rows"] if r["status"] in LOSS and r["recorded"]})
    unrec_lost = sorted({r["norm"] for r in loss["rows"] if r["status"] in LOSS and not r["recorded"]})
    lost_used = loss["lost_used"]
    # 読む欄の欠落が推奨を変え得るか: 保存前の状態でその欄だけ別の値にして evaluate し、推奨が変わるかを見る
    effect = {}
    with ctx.pinned():
        for n in lost_used:
            st2 = copy.deepcopy(ctx.state)
            k = set_paths(st2, n, perturb)
            effect[n] = {"n_set": k, "changed": {m: advice_sig(_evaluate(st2, m)) != advice_sig(orig[m]) for m in MODES}}
    det["existing"]["recorded_lost"], det["existing"]["unrecorded_lost"], det["existing"]["perturb"] = rec_lost, unrec_lost, effect
    el = time.time() - t
    out.append(meas("t1_existing_recorded_not_restored", "battle_logger.compact_state ↔ advice_replay.compact_to_engine_state",
                    "合成結果", inp, "advice 行に記録した欄が復元で欠落 0 件", "§5 I-10 採用条件 (記録した意味の欠落 0 件)",
                    "pass" if not rec_lost else "fail",
                    f"状態の欄 {loss['n_fields']}: {loss['counts']}。記録したのに advice_replay.replay_state が戻さない欄 {rec_lost}"
                    f" (tools.real_accept._engine_state_ex は scene だけ足す)。その欄だけ値を変えたときの推奨の変化 "
                    f"{ {n: effect[n]['changed'] for n in rec_lost if n in effect} }", el))
    used_unrec = [n for n in lost_used if n in unrec_lost]
    changing = [n for n, e in effect.items() if any(e["changed"].values())]
    out.append(meas("t1_existing_unrecorded_read", "battle_logger.compact_state", "合成結果", inp,
                    "evaluate が読む欄をすべて記録する (読む欄の欠落 0 件)", "§5 I-10 (必要な再現性)",
                    "pass" if not used_unrec else "fail",
                    f"evaluate が読むのに advice 行に記録しない欄 {used_unrec}。その欄だけ値を変えて推奨が変わった欄 {changing or 'なし'} "
                    f"(この fixture の範囲。読まない欄の欠落 {sorted(set(unrec_lost) - set(lost_used))})", el))
    out.append(meas("t1_existing_advice_same", "battle_logger + advice_replay + engine", "合成結果", inp,
                    "復元した状態での推奨 (第一候補・候補と得点・退避) が保存前と一致 (legacy / transition)", "§3.3 (出力一致は欠落の検出用)",
                    "pass" if all(same_adv.values()) else "fail", f"一致 {same_adv}", el))
    ok_disp = len(disp) == 1 and disp[0].get("advice_id") == aid and disp[0].get("t_shown") == round(SHOWN_AT, 3)
    ok_ev = len(evs) == 1 and evs[0].get("fired") == EVENTS_FIRED
    ok_best = advice_sig(adv_row.get("advice")) == advice_sig(orig["transition"])
    ok_ver = len(ver) == 1 and (ver[0].get("rules") or {}).get("usage_snapshot") == ctx.snap and \
        ((ver[0].get("team") or {}).get("registry_sha256") is not None)
    out.append(meas("t1_existing_rows", "battle_logger", "合成結果", inp,
                    "表示 (advice_id・t_shown)・イベント (fired)・推奨 (best / actions)・版 (snapshot・登録の sha) が読み戻せる",
                    "§5 I-10", "pass" if (ok_disp and ok_ev and ok_best and ok_ver) else "fail",
                    f"display {ok_disp} / events {ok_ev} / advice {ok_best} / version {ok_ver}", el))
    # ---- 最小記録
    t = time.time()
    c = ctx.tmp("t1_minimal_write")
    inputs = c / "inputs"
    inputs.mkdir()
    for f in (IP.BUNDLE_FILE, IP.BANK_FILE, IP.PIN_MODEL_FILE):
        shutil.copy2(ctx.pin / f, inputs / f)
    (inputs / "registry.json").write_text(json.dumps(ctx.registry, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    shutil.copy2(ctx.models / "battle_policy_balance_ema.zip", inputs / "rl_policy.zip")
    MR.write_manifest(c, {"usage_db": inputs / IP.BUNDLE_FILE, "real_bank": inputs / IP.BANK_FILE,
                          "registry": inputs / "registry.json", "selection_model": inputs / IP.PIN_MODEL_FILE,
                          "rl_policy": inputs / "rl_policy.zip"}, INH_I10_CODE_COMMIT,
                      extra={"usage_snapshot": ctx.snap, "scoring_mode": "transition"})
    w = MR.Writer(c)
    sseq = w.append("obs", state=ctx.state)
    w.append("events", state_seq=sseq, fired=list(EVENTS_FIRED))
    w.append("advice", kind="battle", advice_id="a1", state_seq=sseq, advice=orig["transition"])
    w.append("display", advice_id="a1", t_shown=SHOWN_AT)
    d = ctx.root / "t1_minimal_restore"
    shutil.copytree(c, d)
    shutil.rmtree(c)
    probs = MR.verify(d, INH_I10_CODE_COMMIT)
    recs_m, rprobs = MR.read(d)
    man = json.loads((d / MR.MANIFEST).read_text(encoding="utf-8"))
    obs = next(r for r in recs_m if r["type"] == "obs")
    from advisor.data_pin import sha16_bytes
    bank_p = MR.resolve(d, "real_bank")
    with _pinned(MR.resolve(d, "usage_db"), man["usage_snapshot"], bank_p, sha16_bytes(bank_p.read_bytes()),
                 json.loads(MR.resolve(d, "registry").read_text(encoding="utf-8"))):
        rest_m = {m: _evaluate(obs["state"], m) for m in MODES}
    loss_m = field_loss(ctx.state, obs["state"], reads)
    same_m = {m: advice_sig(orig[m]) == advice_sig(rest_m[m]) for m in MODES}
    adv_m = next(r for r in recs_m if r["type"] == "advice")
    obs_bytes = len(json.dumps(obs, ensure_ascii=False, default=str).encode()) + 1
    adv_bytes = len(json.dumps(adv_m, ensure_ascii=False, default=str).encode()) + 1
    det["minimal"] = {"verify": probs, "read_problems": rprobs, "loss": loss_m, "same_advice": same_m,
                      "bytes_file": (d / MR.RECORDS).stat().st_size, "bytes_manifest": (d / MR.MANIFEST).stat().st_size,
                      "bytes_obs_row": obs_bytes, "bytes_advice_row": adv_bytes, "n_rows": len(recs_m)}
    el = time.time() - t
    inp_m = "fixture → minimal_record (obs / events / advice / display + manifest) → 空 dir へ写す → verify + resolve → evaluate"
    out.append(meas("t1_minimal_field_loss", "minimal_record", "合成結果", inp_m, "evaluate が読む欄の欠落 0 件", "§5 I-10",
                    "pass" if not loss_m["lost_used"] and not probs and not rprobs else "fail",
                    f"状態の欄 {loss_m['n_fields']}: {loss_m['counts']} / verify {probs or 'ok'} / 読みの問題 {rprobs or 'なし'}", el))
    out.append(meas("t1_minimal_advice_same", "minimal_record + engine", "合成結果", inp_m,
                    "復元した状態での推奨が保存前と一致", "§3.3", "pass" if all(same_m.values()) else "fail", f"一致 {same_m}", el))
    return out, det


# ------------------------------------------------------------------ 試験 2: 原本 DB の更新
def _collect_usage() -> dict:
    from advisor.infer import get_inference
    from advisor.sets import get_predictor
    p = get_predictor()
    pred = {s: p.predict(s) for s in ("garchomp", "dragonite", "kingambit", "lopunny")}
    return {"snapshot": p._snapshot_id, "infer_snapshot": get_inference().snapshot_id,
            "pred": json.loads(json.dumps(pred, default=str, sort_keys=True))}


def _add_snapshot(db: Path, src_snap: int) -> int:
    """DB の写しに、src_snap の行を写して技の使用率の順を逆にした新しい snapshot を足す (写しにだけ書く)"""
    conn = sqlite3.connect(str(db))
    try:
        new = int(conn.execute("SELECT MAX(id) FROM usage_snapshot").fetchone()[0]) + 1
        cols = [r[1] for r in conn.execute("PRAGMA table_info(usage_snapshot)") if r[1] != "id"]
        conn.execute(f"INSERT INTO usage_snapshot (id, {', '.join(cols)}) SELECT ?, {', '.join(cols)} FROM usage_snapshot WHERE id=?",
                     (new, src_snap))
        for tbl in ("pokemon_usage", "move_usage", "item_usage", "ability_usage", "teammate_usage"):
            tc = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})") if r[1] not in ("id", "snapshot_id")]
            conn.execute(f"INSERT INTO {tbl} (snapshot_id, {', '.join(tc)}) SELECT ?, {', '.join(tc)} FROM {tbl} WHERE snapshot_id=?",
                         (new, src_snap))
        conn.execute("UPDATE move_usage SET usage_percent = 100.0 - usage_percent WHERE snapshot_id=?", (new,))
        conn.execute("UPDATE item_usage SET usage_percent = 100.0 - usage_percent WHERE snapshot_id=?", (new,))
        conn.commit()
        return new
    finally:
        conn.close()


def t2(ctx: Ctx, log_dir: Path) -> tuple:
    from tools import inputs_pin as IP
    out, det = [], {}
    t = time.time()
    w = ctx.tmp("t2_db_copy") / "usage_copy.sqlite3"
    shutil.copy2(ctx.bundle, w)
    sha_before = sha256_full(w)
    with ctx.pinned(db=w):
        before = _collect_usage()
    new = _add_snapshot(w, ctx.snap)
    with ctx.pinned(db=w):
        after = _collect_usage()
    with ctx.pinned(db=w, snap=None):          # 対照: 版を明示しない (その DB の最新)
        latest = _collect_usage()
    # 再生の経路: 記録した log の当時の版で、写し (最新 = new) を手元の DB にした計画
    logs = sorted(log_dir.glob("*.jsonl"))
    recs = _read_jsonl(logs[0]) if logs else []
    ds = IP.DataSwitch("restore", local=IP.local_data(db=w, bank=ctx.bank))
    try:
        adv_row = next((r for r in recs if r.get("type") == "advice" and r.get("kind") == "battle"), None)
        plan = ds.plan("t2", recs, (adv_row or {}).get("t")) if adv_row else {"ok": False, "usage_snapshot": None, "reasons": ["log なし"]}
        replay_snap = _collect_usage()["snapshot"] if plan["ok"] else None
    finally:
        ds.close()
    el = time.time() - t
    det.update(new_snapshot=new, before=before["snapshot"], after=after["snapshot"], latest=latest["snapshot"],
               plan={k: plan.get(k) for k in ("ok", "reasons", "usage_snapshot")}, replay_snapshot=replay_snap)
    inp = f"使用率 bundle の写しに snapshot {new} (snapshot {ctx.snap} の写しで技・持ち物の順を逆にしたもの) を足す"
    out.append(meas("t2_existing_pinned_ignores_new", "advisor.data_pin (set_override)", "合成結果", inp,
                    f"固定した snapshot {ctx.snap} の予測が追加の前後で同じ", "§5 I-10 (原本 DB を更新した想定)",
                    "pass" if before == after and after["snapshot"] == ctx.snap else "fail",
                    f"前 {before['snapshot']} / 後 {after['snapshot']} / 予測一致 {before['pred'] == after['pred']}", el))
    out.append(meas("t2_control_latest_changes", "advisor.data_pin (版を明示しない)", "合成結果", inp,
                    f"対照: 明示しない経路は最新 {new} を読み、予測が変わる (試験が差を検出できることの確認)", "試験の検出力の確認",
                    "pass" if latest["snapshot"] == new and latest["pred"] != before["pred"] else "fail",
                    f"最新 {latest['snapshot']} / 予測が変わった {latest['pred'] != before['pred']}", el))
    out.append(meas("t2_existing_replay_plan", "tools.inputs_pin.DataSwitch (restore)", "合成結果", inp,
                    f"再生の計画が当時の snapshot {ctx.snap} を使い、再生中の予測もその版", "§5 I-10",
                    "pass" if plan["ok"] and plan["usage_snapshot"] == ctx.snap and replay_snap == ctx.snap else "fail",
                    f"plan {det['plan']} / 再生中の snapshot {replay_snap}", el))
    # 最小記録: ファイルの sha を固定する方式。更新された写しは照合で止まる (古い版を読み分けることはしない)
    t = time.time()
    m = ctx.tmp("t2_minimal")
    w2 = m / "usage_copy.sqlite3"
    shutil.copy2(ctx.bundle, w2)
    MR.write_manifest(m, {"usage_db": w2}, INH_I10_CODE_COMMIT, extra={"usage_snapshot": ctx.snap})
    _add_snapshot(w2, ctx.snap)
    try:
        MR.resolve(m, "usage_db")
        res, why = "fail", "更新後の写しを照合なしで返した"
    except MR.MinimalPinError as e:
        res, why = "pass", f"MinimalPinError: {e} (古い版を読み分けず、写しの差し替えを要求する)"
    out.append(meas("t2_minimal_pinned_file", "minimal_record", "合成結果", inp, "更新した DB を黙って読まない (止まる)", "§5 I-10",
                    res, why, time.time() - t))
    det["copy_sha_changed"] = sha_before != sha256_full(w)
    return out, det


# ------------------------------------------------------------------ 試験 3: モデルの欠損
def t3(ctx: Ctx) -> tuple:
    from advisor import rl_bridge as RB
    from advisor import versions as V
    from champions_agent.agent import selection_dispatch as SD
    from tools import inputs_pin as IP
    from tools import real_accept as RA
    out, det = [], {}
    # (a) ピンの選出モデル
    t = time.time()
    p = ctx.root / "t3_pin"
    shutil.copytree(ctx.pin, p)
    (p / IP.PIN_MODEL_FILE).unlink()
    reasons = IP.verify_pin(p)
    out.append(meas("t3_pin_selection_missing", "tools.inputs_pin.verify_pin", "合成結果", "ピンの selection_model.pt を消す",
                    "照合が理由を返す (起動しない)", "§5 I-10 (モデルの欠損)", "pass" if reasons else "fail", f"{reasons}", time.time() - t))
    # (b) RL 方策: 読み込み順の先頭 (ema) を消す
    t = time.time()
    orig_dir = RB.CKPT_DIR
    mdir = ctx.root / "t3_models"
    shutil.copytree(ctx.models, mdir)
    orig_tried = (RB._model_tried, RB._model)
    try:
        RB.CKPT_DIR = mdir
        RB._model_tried, RB._model = False, None         # 試験 1 の読み込みの失敗を持ち越さない (読み込み前の状態)
        os.environ.pop("RL_POLICY_SOURCE", None)
        os.environ.pop("RL_ADVICE_STYLE", None)
        first = V._rl_info()
        (mdir / "battle_policy_balance_ema.zip").unlink()
        second = V._rl_info()
        for f in mdir.glob("*.zip"):
            f.unlink()
        third = V._rl_info()
    finally:
        RB.CKPT_DIR = orig_dir
        RB._model_tried, RB._model = orig_tried
    det["rl"] = {"first": first, "after_ema_removed": second, "all_removed": third}
    silent = second[0] and second[0] != first[0] and second[3] is None
    out.append(meas("t3_rl_ema_missing", "advisor.rl_bridge / advisor.versions._rl_info", "合成結果",
                    "固定したつもりの RL 方策 (ema) を消す", "エラーか判定不能 (別の方策へ黙って退避しない)", "§5 I-10",
                    "fail" if silent else "pass",
                    f"読み込み順の次 ({Path(str(second[0])).name}) へ理由なしで移り、version 行には新しい経路・sha だけが残る。"
                    f"RL の sha を指定して照合する経路 (data_pin 相当) は無く、再生 (advice_replay / real_accept) も記録の rl sha と"
                    f"今の方策を照合しない" if silent else f"{second}", time.time() - t))
    # (c) RL 方策が全部無い: 読み込み失敗 → real_accept の実行状態で判定不能
    t = time.time()
    doc_fail = V.summarize_versions(None, "/m/sel.pt", "s1", None, None, ["a"], None, None, False,
                                    "load_failed (sb3_contrib / torch / file)", "t", "g", "d", "e", "v1",
                                    scoring=V.scoring_identity(), usage_snapshot=1)
    recs = [dict(doc_fail, type="version", t=1.0, scoring_mode="transition", served_commit="c"),
            {"type": "advice", "kind": "battle", "t": 2.0, "advice_id": "x", "advice": {"ok": True}, "policy": {"rl_loaded": False}}]
    ident = RA.version_identity(recs)
    g = RA.group_consistency([{"name": "x", "ident": ident}])
    out.append(meas("t3_rl_all_missing", "tools.real_accept.version_identity / group_consistency", "合成結果",
                    "RL 方策が無く読み込みに失敗した version 行・advice 行", "実行状態の失敗として群が不成立", "§5 I-10",
                    "pass" if ident["runtime_ok"] is False and not g["ok"] else "fail",
                    f"runtime_ok {ident['runtime_ok']} / 群 ok {g['ok']} / third={third}", time.time() - t))
    # (d) 登録チーム向けの選出モデル (実機の経路): 消すと配布版へ
    t = time.time()
    orig_mark = SD.EXPERIMENT_MARK
    rdir = ctx.root / "t3_registered"
    species = ["lopunny", "slurpuff", "baxcalibur", "garchomp", "kingambit", "dragonite"]
    key = SD.team_key(species)
    (rdir / key).mkdir(parents=True)
    (rdir / key / "selection_model.pt").write_bytes(b"synthetic-registered")
    try:
        SD.EXPERIMENT_MARK = ctx.root / "no_experiment_mark"
        a = SD.advisor_model_path(species, registered_dir=rdir)
        (rdir / key / "selection_model.pt").unlink()
        b = SD.advisor_model_path(species, registered_dir=rdir)
    finally:
        SD.EXPERIMENT_MARK = orig_mark
    doc_b = V.summarize_versions(None, str(b[0]), "s2", b[1], None, species, None, None, True, None, "t", "g", "d", "e", "v1")
    det["selection"] = {"with_model": [str(a[0]), a[1]], "removed": [str(b[0]), b[1]],
                        "fallback_reason": doc_b["selection_model"]["fallback_reason"], "source": doc_b["selection_model"]["source"]}
    silent_sel = str(a[1] or "").startswith(SD.REGISTERED_PREFIX) and b[1] is None and doc_b["selection_model"]["fallback_reason"] is None
    out.append(meas("t3_selection_registered_missing", "champions_agent.agent.selection_dispatch.advisor_model_path", "合成結果",
                    "登録チーム向けの選出モデルを消す", "エラーか判定不能", "§5 I-10", "fail" if silent_sel else "pass",
                    "配布版 (deployed) へ移り、fallback_reason は None (source が registered → deployed に変わるだけ)" if silent_sel
                    else f"{det['selection']}", time.time() - t))
    # (e) 指定 Package のモデルが無い: 理由は記録するが、受入の群は成立する
    t = time.time()
    doc_e = V.summarize_versions("pkgX", "/m/deployed.pt", "s3", None, ["a"], ["a"], "/m/rl.zip", "r", True, None, "t", "g", "d",
                                 "e", "v1", scoring=V.scoring_identity(), usage_snapshot=1)
    recs_e = [dict(doc_e, type="version", t=1.0, scoring_mode="transition", served_commit="c"),
              {"type": "advice", "kind": "battle", "t": 2.0, "advice_id": "x", "advice": {"ok": True}, "policy": {"rl_loaded": True}}]
    ident_e = RA.version_identity(recs_e)
    g_e = RA.group_consistency([{"name": "x", "ident": ident_e}])
    out.append(meas("t3_designated_package_missing", "advisor.versions.summarize_versions + real_accept", "合成結果",
                    "指定 Package の選出モデルが無い (配布版で助言)", "受入・再生で判定不能になる", "§5 I-10",
                    "pass" if not g_e["ok"] else "fail",
                    f"fallback_reason={doc_e['selection_model']['fallback_reason']} を記録するが、群の照合は ok={g_e['ok']} "
                    f"(実行状態の照合は error / loaded だけを見る)", time.time() - t))
    # (f) 最小記録
    t = time.time()
    m = ctx.tmp("t3_minimal")
    (m / "rl_policy.zip").write_bytes(b"x")
    (m / "selection_model.pt").write_bytes(b"y")
    MR.write_manifest(m, {"rl_policy": m / "rl_policy.zip", "selection_model": m / "selection_model.pt"}, INH_I10_CODE_COMMIT)
    (m / "rl_policy.zip").unlink()
    try:
        MR.resolve(m, "rl_policy")
        res, why = "fail", "無いファイルの経路を返した"
    except MR.MinimalPinError as e:
        res, why = "pass", f"MinimalPinError: {e}"
    out.append(meas("t3_minimal_model_missing", "minimal_record", "合成結果", "manifest の rl_policy を消す", "止まる", "§5 I-10",
                    res, why, time.time() - t))
    return out, det


# ------------------------------------------------------------------ 試験 4: hash 不一致
def t4(ctx: Ctx, log_dir: Path) -> tuple:
    from advisor.data_pin import InputsPinError
    from advisor.real_prior import load_bank
    from tools import inputs_pin as IP
    out = []

    def expect_pin_error(fn) -> tuple:
        try:
            fn()
            return "fail", "例外なしで読めた"
        except InputsPinError as e:
            return "pass", f"InputsPinError: {e}"

    t = time.time()
    with ctx.pinned(bank_sha=INH_I10_SHA_MISSING):
        r = expect_pin_error(load_bank)
    out.append(meas("t4_bank_sha_mismatch", "advisor.data_pin / advisor.real_prior", "合成結果", "バンクの sha16 を違う値で指定",
                    "InputsPinError", "§5 I-10 (hash 不一致)", r[0], r[1], time.time() - t))
    t = time.time()
    bdir = ctx.tmp("t4_bank")
    bank2 = bdir / "bank.json"
    shutil.copy2(ctx.bank, bank2)
    bank2.write_bytes(bank2.read_bytes() + b"\n")          # 中身を 1 バイト変える (JSON としては同じ)
    with ctx.pinned(bank=bank2):
        r = expect_pin_error(load_bank)
    out.append(meas("t4_bank_content_changed", "advisor.data_pin / advisor.real_prior", "合成結果",
                    "バンクの写しの中身を変え、sha16 は元の値で指定", "InputsPinError", "§5 I-10", r[0], r[1], time.time() - t))
    # 登録チーム: version 行の registry_sha256 / registry の中身を書き換えた記録
    logs = sorted(log_dir.glob("*.jsonl"))
    recs = _read_jsonl(logs[0])
    local = IP.local_data(db=ctx.bundle, bank=ctx.bank)
    for cid, mut, desc in (("t4_registry_sha_mismatch", lambda tm: tm.update(registry_sha256=INH_I10_SHA_MISSING),
                            "version 行の team.registry_sha256 を違う値に"),
                           ("t4_registry_content_changed", lambda tm: tm["registry"].update({"__i10__": {"x": 1}}),
                            "version 行の team.registry の中身を変え、sha はそのまま")):
        t = time.time()
        rr = copy.deepcopy(recs)
        for r in rr:
            if r.get("type") == "version":
                mut(r["team"])
        adv_t = next(r for r in rr if r.get("type") == "advice" and r.get("kind") == "battle")["t"]
        plan = IP.restore_plan(IP.data_version_at(rr, adv_t), local["snapshots"], local["team_sha16"], local["banks"], "restore",
                               local["latest"], team_registry=IP.team_registry_at(rr, adv_t))
        out.append(meas(cid, "tools.inputs_pin.restore_plan", "合成結果", desc, "判定不能 (ok False と理由)", "§5 I-10",
                        "pass" if not plan["ok"] else "fail", f"ok={plan['ok']} reasons={plan['reasons']}", time.time() - t))
    t = time.time()
    p = ctx.root / "t4_pin"
    shutil.copytree(ctx.pin, p)
    c = sqlite3.connect(str(p / IP.BUNDLE_FILE))
    try:
        c.execute("UPDATE move_usage SET usage_percent = usage_percent + 1 WHERE rowid = (SELECT MIN(rowid) FROM move_usage)")
        c.commit()
    finally:
        c.close()
    reasons = IP.verify_pin(p)
    out.append(meas("t4_pin_bundle_changed", "tools.inputs_pin.verify_pin", "合成結果", "ピンの bundle の 1 行を書き換える",
                    "照合が理由を返す", "§5 I-10", "pass" if reasons else "fail", f"{reasons}", time.time() - t))
    t = time.time()
    m = ctx.tmp("t4_minimal")
    shutil.copy2(ctx.bank, m / "bank.json")
    MR.write_manifest(m, {"real_bank": m / "bank.json"}, INH_I10_CODE_COMMIT)
    (m / "bank.json").write_text("{}", encoding="utf-8")
    probs = MR.verify(m)
    out.append(meas("t4_minimal_sha_mismatch", "minimal_record", "合成結果", "manifest のバンクを書き換える", "照合が理由を返す",
                    "§5 I-10", "pass" if probs else "fail", f"{probs}", time.time() - t))
    return out, {}


# ------------------------------------------------------------------ 試験 5: 途中行の破損
def _corrupt_middle(src: Path, dst: Path, typ: str) -> int:
    """src の type = typ の最初の行 (中ほど) を壊して dst に書く。壊した行番号 (1 始まり)"""
    lines = src.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, x in enumerate(lines) if json.loads(x).get("type") == typ and 0 < i < len(lines) - 1)
    lines[idx] = lines[idx][: len(lines[idx]) // 2]
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return idx + 1


def _reader_case(case_id: str, asset: str, desc: str, fn, n_lines: int) -> dict:
    """読み手を呼び、落ちたか・壊れた行を報告したかを見る。fn は (読めた結果の説明, 報告した壊れた行数 or None) を返す"""
    t = time.time()
    try:
        got, reported = fn()
    except Exception as e:
        return meas(case_id, asset, "合成結果", desc, "落ちずに壊れた行を数えて報告", "§5 I-10 (途中行の破損)", "fail",
                    f"例外で止まった: {type(e).__name__}: {str(e)[:120]}", time.time() - t)
    ok = reported == 1
    return meas(case_id, asset, "合成結果", desc, "落ちずに壊れた行を数えて報告", "§5 I-10 (途中行の破損)", "pass" if ok else "fail",
                f"{n_lines} 行: {got}。壊れた行の報告 {reported if reported is not None else 'なし (黙って飛ばした)'}",
                time.time() - t)


def t5(ctx: Ctx, log_dir: Path) -> tuple:
    from tools import advice_replay as AR
    from tools import analyze_battles as AB
    from tools import real_accept as RA
    from tools.team_build.registry import Registry
    out, det = [], {}
    src = sorted(log_dir.glob("*.jsonl"))[0]
    d = ctx.tmp("t5")
    bad = d / src.name
    line_no = _corrupt_middle(src, bad, "advice")
    n_lines = len(bad.read_text(encoding="utf-8").splitlines())
    det["corrupted_line"] = line_no
    desc = f"対戦ログの {line_no} 行目 (advice 行) を途中で切る ({n_lines} 行)"
    out.append(_reader_case("t5_real_accept_load_records", "tools.real_accept.load_records", desc,
                            lambda: (f"記録 {len(RA.load_records(bad))} 件", None), n_lines))
    out.append(_reader_case("t5_advice_replay_read", "tools.advice_replay._read_records", desc,
                            lambda: (f"記録 {len(AR._read_records(bad))} 件", None), n_lines))
    out.append(_reader_case("t5_analyze_battles_parse", "tools.analyze_battles._parse_battle", desc,
                            lambda: (f"集計 (battle の scene {AB._parse_battle(str(bad))['n_battle_scenes']} 行)", None), n_lines))
    reg = d / "registry"
    reg.mkdir()
    rows = [{"id": f"team-{i:016d}", "kind": "team", "status": "candidate", "meta": {}} for i in range(3)]
    txt = "\n".join(json.dumps(r) for r in rows) + "\n"
    lines = txt.splitlines()
    lines[1] = lines[1][:20]
    (reg / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.append(_reader_case("t5_registry_index", "tools.team_build.registry.Registry", "registry の index.jsonl の 2 行目 (3 行中) を切る",
                            lambda: (f"記録 {len(Registry(reg).list())} 件", None), 3))
    m = ctx.tmp("t5_minimal")
    w = MR.Writer(m)
    for i in range(5):
        w.append("advice", i=i)
    lines = (m / MR.RECORDS).read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2][:15]
    (m / MR.RECORDS).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def mr_read():
        recs, probs = MR.read(m)
        return f"記録 {len(recs)} 件 (problems {probs})", len(probs)
    out.append(_reader_case("t5_minimal_read", "minimal_record.read", "records.jsonl の 3 行目 (5 行中) を切る", mr_read, 5))
    return out, det


# ------------------------------------------------------------------ 試験 6: 途中停止・再開
def t6(ctx: Ctx) -> tuple:
    from battle_logger import BattleLogger
    from tools import analyze_battles as AB
    from tools import real_accept as RA
    out, det = [], {}
    t = time.time()
    d = ctx.tmp("t6_existing")
    st = copy.deepcopy(ctx.state)
    adv = {"ok": True, "best": {"kind": "move", "id": "closecombat"}, "actions": [{"kind": "move", "id": "closecombat", "score": 1.0}]}
    with _versions(ctx.doc), ctx.pinned():
        lg1 = BattleLogger(log_dir=d, guess_prob_fn=None)
        lg1.on_frame(st, [])
        a1 = lg1.on_advice(dict(adv), "battle", st, evaluated=True, t_eval=T0)
        del lg1                                           # 停止 (close を呼ばずに落ちた)
        lg2 = BattleLogger(log_dir=d, guess_prob_fn=None)   # 再開: 同じ対戦 (battle_seq も同じ)
        st2 = copy.deepcopy(st)
        st2["turn"] = st["turn"] + 1
        lg2.on_frame(st2, [])
        a2 = lg2.on_advice(dict(adv), "battle", st2, evaluated=True, t_eval=T0 + 30)
        lg2.close()
    files = sorted(d.glob("*.jsonl"))
    recs = {f.name: RA.load_records(f) for f in files}
    seqs = sorted({str(((r.get("state") or {}).get("battle_seq"))) for rs in recs.values() for r in rs if r.get("type") == "scene"})
    parsed = [AB._parse_battle(str(f)) for f in files]
    det["split"] = {"files": [f.name for f in files], "advice_ids": [a1, a2], "battle_seq_in_rows": seqs,
                    "analyze_n": len(parsed)}
    linked = len(files) == 1 or any(s not in ("None",) for s in seqs)
    out.append(meas("t6_existing_restart_split", "battle_logger + real_accept / analyze_battles", "合成結果",
                    "同じ対戦 (battle_seq 7) の途中でロガーを落として作り直す",
                    "1 対戦として読める、または分かれたことを読み手が知れる (対戦をつなぐ鍵がある)", "§5 I-10 (途中停止・再開)",
                    "pass" if linked else "fail",
                    f"ファイル {len(files)} 本 ({', '.join(f.name for f in files)})。scene / advice / version / session 行に対戦の鍵が無く "
                    f"(scene 行の battle_seq {seqs}。battle_seq は my_hp_trace・frame_burst・inputs_pin_error 行にだけあり、常には書かれず、"
                    f"server の再起動で 0 から数え直す)、読み手は 2 対戦として数える (analyze_battles {len(parsed)} 件)。"
                    f"advice_id は重複しない ({a1} / {a2})" if not linked else f"{det['split']}", time.time() - t))
    # 追記: 途中で切れた最後の行の後に、同じファイルへ次の行を追記 (同じプロセスの書き込みが途中で失敗した想定)
    t = time.time()
    f = files[-1]
    lines = f.read_text(encoding="utf-8").splitlines()
    n_before = len(lines)
    f.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][: len(lines[-1]) // 2], encoding="utf-8")   # 最後の行を途中で切る
    with _versions(ctx.doc):
        lg3 = BattleLogger(log_dir=d, guess_prob_fn=None)
        lg3._file = f                                       # 同じファイルへ追記 (書き込みの途中失敗の後に続けた想定)
        lg3._advice_files[a2] = f
        lg3.on_display(a2, T0 + 31.0, kind="battle")
    got = RA.load_records(f)
    det["append"] = {"lines_before": n_before, "records_read": len(got)}
    lost = n_before + 1 - len(got)
    out.append(meas("t6_existing_append_after_truncation", "battle_logger._write + real_accept.load_records", "合成結果",
                    "最後の行を途中で切ったファイルに 1 行追記する", "切れた行を数えて報告し、追記した行は読める", "§5 I-10",
                    "fail" if lost > 0 else "pass",
                    f"書いた {n_before + 1} 行のうち {len(got)} 件を読めた。改行なしの切れ端に追記が連結し、{lost} 行を報告なしに失った"
                    if lost > 0 else "欠落なし", time.time() - t))
    # 最小記録
    t = time.time()
    m = ctx.tmp("t6_minimal")
    w = MR.Writer(m)
    for i in range(3):
        w.append("advice", i=i)
    raw = (m / MR.RECORDS).read_text(encoding="utf-8")
    (m / MR.RECORDS).write_text(raw[:-10], encoding="utf-8")       # 最後の行を途中で切る (停止)
    w2 = MR.Writer(m)                                             # 再開
    w2.append("advice", i=3)
    recs_m, probs = MR.read(m)
    seq = [r.get("seq") for r in recs_m]
    resume = [r for r in recs_m if r["type"] == "resume"]
    ok = len(probs) == 1 and len(resume) == 1 and resume[0]["problems"] and seq == sorted(seq) and recs_m[-1].get("i") == 3
    det["minimal"] = {"seq": seq, "problems": probs, "resume": resume}
    out.append(meas("t6_minimal_resume", "minimal_record.Writer / read", "合成結果", "最後の行を切って停止 → 同じ dir で再開して追記",
                    "切れた行を報告し、再開の行と以後の記録が読める", "§5 I-10", "pass" if ok else "fail",
                    f"seq {seq} / problems {probs} / resume {len(resume)}", time.time() - t))
    return out, det


# ------------------------------------------------------------------ 試験 7: 指定版が無い
def t7(ctx: Ctx, log_dir: Path) -> tuple:
    from advisor.data_pin import InputsPinError
    from advisor.sets import get_predictor
    from tools import inputs_pin as IP
    from tools import real_accept as RA
    out = []
    recs = _read_jsonl(sorted(log_dir.glob("*.jsonl"))[0])
    for r in recs:
        if r.get("type") == "advice" and r.get("kind") == "battle":
            r.setdefault("policy", {})["rl_loaded"] = True     # 群の照合が実行状態で不成立にならないように (commit だけを試す)
    adv_t = next(r for r in recs if r.get("type") == "advice" and r.get("kind") == "battle")["t"]
    local = IP.local_data(db=ctx.bundle, bank=ctx.bank)

    def plan_of(rr):
        return IP.restore_plan(IP.data_version_at(rr, adv_t), local["snapshots"], local["team_sha16"], local["banks"], "restore",
                               local["latest"], team_registry=IP.team_registry_at(rr, adv_t))

    def mutate(fn):
        rr = copy.deepcopy(recs)
        for r in rr:
            if r.get("type") == "version":
                fn(r)
        return rr
    t = time.time()
    p = plan_of(mutate(lambda r: r["rules"].update(usage_snapshot=INH_I10_SNAPSHOT_MISSING)))
    out.append(meas("t7_usage_snapshot_missing_replay", "tools.inputs_pin.restore_plan", "合成結果",
                    f"version 行の usage_snapshot を {INH_I10_SNAPSHOT_MISSING} に", "判定不能 (最新で代わりに再生しない)",
                    "§5 I-10 (指定版が無い)", "pass" if not p["ok"] and p["usage_snapshot"] is None else "fail",
                    f"ok={p['ok']} reasons={p['reasons']}", time.time() - t))
    t = time.time()
    with ctx.pinned(snap=INH_I10_SNAPSHOT_MISSING):
        try:
            get_predictor().predict("garchomp")
            r = ("fail", "最新で予測した")
        except InputsPinError as e:
            r = ("pass", f"InputsPinError: {e}")
    out.append(meas("t7_usage_snapshot_missing_live", "advisor.data_pin + advisor.sets", "合成結果",
                    f"固定の snapshot を {INH_I10_SNAPSHOT_MISSING} にして予測", "InputsPinError", "§5 I-10", r[0], r[1], time.time() - t))
    t = time.time()
    local_team = dict(local, team_sha16="team-sha-i10")       # 手元の登録の本文の sha = 記録の値 (照合が成り立つ手元)

    def old_team(sha):
        def fn(r):
            r["team"].pop("registry", None)
            r["team"].pop("registry_sha256", None)
            r["team"]["sha256"] = sha
        return fn

    def plan_local(rr):
        return IP.restore_plan(IP.data_version_at(rr, adv_t), local_team["snapshots"], local_team["team_sha16"], local_team["banks"],
                               "restore", local_team["latest"], team_registry=IP.team_registry_at(rr, adv_t))
    ctrl = plan_local(mutate(old_team("team-sha-i10")))
    p = plan_local(mutate(old_team(INH_I10_SHA_MISSING)))
    out.append(meas("t7_team_sha_missing_old_row", "tools.inputs_pin.restore_plan", "合成結果",
                    "旧形式の version 行 (registry の欄なし) で team.sha256 を存在しない値に (手元の登録の sha は記録どおりの値)",
                    "判定不能 (対照: 記録どおりの sha なら ok)", "§5 I-10", "pass" if (ctrl["ok"] and not p["ok"]) else "fail",
                    f"対照 ok={ctrl['ok']} / 存在しない sha ok={p['ok']} reasons={p['reasons']}", time.time() - t))
    t = time.time()
    p = plan_of(mutate(lambda r: r["team"].update(registry=None, registry_sha256=INH_I10_SHA_MISSING)))
    out.append(meas("t7_registry_missing", "tools.inputs_pin.restore_plan", "合成結果",
                    "version 行の team.registry を null、registry_sha256 を存在しない値に", "判定不能", "§5 I-10",
                    "pass" if not p["ok"] else "fail", f"ok={p['ok']} reasons={p['reasons']}", time.time() - t))
    # git commit: 1 本だけの再生・受入
    t = time.time()
    fake = mutate(lambda r: (r["rules"].update(git_commit=INH_I10_COMMIT_MISSING), r.update(served_commit=INH_I10_COMMIT_MISSING)))
    p = plan_of(fake)
    ident = RA.version_identity(fake)
    g1 = RA.group_consistency([{"name": "fake", "ident": ident}])
    out.append(meas("t7_git_commit_missing_single", "tools.inputs_pin.restore_plan + real_accept.group_consistency", "合成結果",
                    f"version 行の git_commit / served_commit を存在しない {INH_I10_COMMIT_MISSING[:12]}… に (ログ 1 本)",
                    "今のコードとの食い違いを判定不能にする (今のコードで黙って再生しない)", "§5 I-10",
                    "pass" if (not p["ok"] or not g1["ok"]) else "fail",
                    f"復元の計画 ok={p['ok']} (commit を見ない)、群の照合 ok={g1['ok']} {g1['notes']} (群の中の一致だけを見る)。"
                    f"再生・受入は今の commit (_code_info) を出力に書くが、記録の commit と比べない", time.time() - t))
    t = time.time()
    good = copy.deepcopy(recs)
    for r in good:
        if r.get("type") == "version":
            r["served_commit"] = INH_I10_CODE_COMMIT
    g2 = RA.group_consistency([{"name": "good", "ident": RA.version_identity(good)}, {"name": "fake", "ident": ident}])
    out.append(meas("t7_git_commit_missing_group", "tools.real_accept.group_consistency", "合成結果",
                    "正しい commit のログと存在しない commit のログの群", "群が不成立", "§5 I-10", "pass" if not g2["ok"] else "fail",
                    f"ok={g2['ok']} notes={[n for n in g2['notes'] if 'commit' in n]}", time.time() - t))
    t = time.time()
    m = ctx.tmp("t7_minimal")
    (m / "x.bin").write_bytes(b"x")
    MR.write_manifest(m, {"x": m / "x.bin"}, INH_I10_COMMIT_MISSING)
    probs = MR.verify(m, INH_I10_CODE_COMMIT)
    out.append(meas("t7_minimal_commit_mismatch", "minimal_record.verify", "合成結果", "manifest の code_commit を存在しない値に",
                    "照合が理由を返す", "§5 I-10", "pass" if probs else "fail", f"{probs}", time.time() - t))
    return out, {}


# ------------------------------------------------------------------ 試験 8: 集計の検出
def _ver_row(git: str, t: float, served: Optional[str] = None) -> dict:
    from advisor import versions as V
    doc = V.summarize_versions(None, "/m/sel.pt", "sel1", None, None, ["a"], "/m/rl.zip", "rl1", True, None, "team1", git, "dex1",
                               "eff1", "v1", embedding_sha="emb1", usage_snapshot=7, usage_snapshot_source="pinned",
                               scoring={"scoring_mode": "transition"}, team_registry={"x": 1}, registry_required=True)
    return dict(doc, type="version", t=t, scoring_mode="transition", served_commit=served or git)


def _adv_row(i: int, t: float, compact: dict, ok: bool = True, evaluated=True, best="closecombat") -> dict:
    a = {"ok": ok, "best": {"kind": "move", "id": best} if ok else None,
         "actions": [{"kind": "move", "id": best, "score": 1.0}] if ok else [], "t_gen": t}
    if not ok:
        a["reason"] = "eval_error"
    return {"type": "advice", "kind": "battle", "t": t, "advice_id": f"a{i}", "advice": a, "state": compact,
            "policy": {"rl_loaded": True}, "scoring_mode": "transition", "scoring_used": "transition", "fallback_reason": None,
            "evaluated": evaluated, "t_eval": t if evaluated is not None else None,
            "cache_hit": (not evaluated) if evaluated is not None else None}


# 既知の値の fixture: ログごとの (勝敗, battle の scene 数, advice の内訳, 決定の行, 固定入力の異常)。期待値は作り方から決まる
AGG_SPEC = {
    "battle_20991231_000001.jsonl": {"outcome": "win", "scenes": 4, "adv": [True, True, False], "fail": [], "dec": 1, "pin": 0},
    "battle_20991231_000002.jsonl": {"outcome": "loss", "scenes": 3, "adv": [True], "fail": [True], "dec": 1, "pin": 1},
    "battle_20991231_000003.jsonl": {"outcome": "loss->win", "scenes": 3, "adv": [True, True], "fail": [], "dec": 1, "pin": 0},
    "battle_20991231_000004.jsonl": {"outcome": None, "scenes": 3, "adv": [None], "fail": [], "dec": 0, "pin": 0},
    "battle_20991231_000005.jsonl": {"outcome": "loss", "scenes": 2, "adv": [True], "fail": [], "dec": 0, "pin": 0},
}
AGG_EXPECTED = {"wins": 2, "losses": 2, "n": 5, "n_decided": 4, "n_corrected": 1, "n_advice": 9, "n_failed": 1,
                "n_evaluated": 7, "n_cache": 1, "n_eval_unknown": 1, "n_inputs_pin_rows": 1, "n_suppressed": 2, "n_decision_rows": 3}


def build_agg_logs(d: Path, compact: dict, scene_state: dict, git: str = "g1") -> dict:
    """AGG_SPEC の対戦ログを d に書く (副作用: 書き込み)。{ファイル名: 記録}"""
    out = {}
    for k, (name, sp) in enumerate(AGG_SPEC.items()):
        t = 1000.0 * (k + 1)
        rows = [{"type": "session", "t": t, "source": "organic"}, _ver_row(git, t + 0.1)]
        for j in range(sp["scenes"]):
            rows.append({"type": "scene", "t": t + 1 + j, "scene": "command", "turn": j + 1, "state": scene_state})
        i = 0
        for ev in sp["adv"]:
            i += 1
            rows.append(_adv_row(k * 10 + i, t + 2 + i, compact, evaluated=ev))
        for _ in sp["fail"]:
            i += 1
            rows.append(_adv_row(k * 10 + i, t + 2 + i, compact, ok=False, evaluated=True))
        for _ in range(sp["dec"]):
            rows.append({"type": "decision", "t": t + 20, "advice_id": f"a{k * 10 + 1}", "turn": 1, "t_gen": t + 3,
                         "t_shown": None, "t_decided": t + 19, "action": None, "match": None})
        for _ in range(sp["pin"]):
            rows.append({"type": "inputs_pin_error", "t": t + 21, "reason": "inputs_pin", "source": "evaluate",
                         "error": "使用率の snapshot 999999 が DB に無い", "n_suppressed": 2})
        if sp["outcome"] == "loss->win":
            rows.append({"type": "outcome", "t": t + 30, "outcome": "loss"})
            rows.append({"type": "events", "t": t + 31, "fired": ["battle_win"]})
        elif sp["outcome"]:
            rows.append({"type": "outcome", "t": t + 30, "outcome": sp["outcome"]})
        rows.append({"type": "events", "t": t + 32, "fired": ["battle_end_rank"]})
        (d / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        out[name] = rows
    return out


def t8(ctx: Ctx) -> tuple:
    from battle_logger import _compact_state, compact_state, evaluation_counts
    from tools import analyze_battles as AB
    from tools import real_accept as RA
    out, det = [], {}
    d = ctx.tmp("t8_logs")
    compact = json.loads(json.dumps(compact_state(ctx.state), default=str))
    scene_state = json.loads(json.dumps(_compact_state(ctx.state), default=str))
    logs = build_agg_logs(d, compact, scene_state)
    paths = sorted(d.glob("*.jsonl"))
    # 独立の数え方 (行の型と欄だけ) と、作り方から決まる期待値が一致するか (fixture 自体の確認)
    t = time.time()
    indep: dict = {}
    for recs in logs.values():
        for k, v in count_rows(recs).items():
            if isinstance(v, int):
                indep[k] = indep.get(k, 0) + v
    mism = compare_expected({k: v for k, v in AGG_EXPECTED.items() if k in indep}, indep)
    out.append(meas("t8_fixture_independent_count", "tools.inheritance_eval.record_contract.count_rows", "合成結果",
                    "AGG_SPEC の 5 本", AGG_EXPECTED, "fixture の作り方 (AGG_SPEC)", "pass" if not mism else "fail", f"食い違い {mism}",
                    time.time() - t))
    # analyze_battles: 全ファイルを _parse_battle → summarize (除外なし) / load_battles (断片の除外あり)
    t = time.time()
    s_all = AB.summarize([AB._parse_battle(str(p)) for p in paths])
    got = {"wins": s_all["wins"], "losses": s_all["losses"], "n": s_all["n"], "n_decided": s_all["n_decided"],
           "n_corrected": s_all["n_corrected"]}
    mism = compare_expected({k: AGG_EXPECTED[k] for k in got}, got)
    out.append(meas("t8_analyze_summarize", "tools.analyze_battles.summarize", "合成結果", "5 本を _parse_battle → summarize",
                    {k: AGG_EXPECTED[k] for k in got}, "AGG_SPEC", "pass" if not mism else "fail", f"{got} 食い違い {mism}", time.time() - t))
    t = time.time()
    orig_dir = AB.BATTLE_DIR
    try:
        AB.BATTLE_DIR = d
        lb = AB.load_battles()
    finally:
        AB.BATTLE_DIR = orig_dir
    s_lb = AB.summarize(lb)
    got = {"wins": s_lb["wins"], "losses": s_lb["losses"], "n": s_lb["n"]}
    mism = compare_expected({k: AGG_EXPECTED[k] for k in got}, got)
    out.append(meas("t8_analyze_load_battles", "tools.analyze_battles.load_battles", "合成結果",
                    "5 本 (うち 1 本は battle の scene 2 行で負けの記録あり)", {k: AGG_EXPECTED[k] for k in got}, "AGG_SPEC",
                    "pass" if not mism else "fail",
                    f"{got}。MIN_BATTLE_SCENES ({AB.MIN_BATTLE_SCENES}) 未満の対戦を件数の報告なしに外し、その負けが成績から消えた" if mism
                    else f"{got}", time.time() - t))
    # real_accept の集計の入口 (dry-run: 再生しない)
    t = time.time()
    r = RA.run(paths, None, dry_run=True)
    lg = {x["name"]: x for x in r["logs"]}
    got = {"n_advice": sum(x["advice_counts"]["n_rows"] for x in lg.values()),
           "n_evaluated": sum(x["advice_counts"]["n_evaluated"] or 0 for x in lg.values()),
           "n_inputs_pin_rows": sum(x["inputs_pin"]["n_rows"] for x in lg.values()),
           "n_suppressed": sum(x["inputs_pin"]["n_suppressed"] for x in lg.values()),
           "n_decision_rows": sum(x["n_decision_rows"] for x in lg.values())}
    ev = {"n_evaluated": 0, "n_cache": 0, "n_eval_unknown": 0}
    for recs in logs.values():
        c = evaluation_counts(recs)
        ev["n_evaluated"] += c["n_evaluated"]
        ev["n_cache"] += c["n_cache_hit"]
        ev["n_eval_unknown"] += c["n_unknown"]
    got.update(ev)
    mism = compare_expected({k: AGG_EXPECTED[k] for k in got}, got)
    out.append(meas("t8_real_accept_counts", "tools.real_accept (dry-run) + battle_logger.evaluation_counts", "合成結果",
                    "5 本の助言行・実計算・固定入力の異常・決定の行", {k: AGG_EXPECTED[k] for k in got}, "AGG_SPEC",
                    "pass" if not mism else "fail", f"{got} 食い違い {mism}", time.time() - t))
    n_success_rows = sum(x["n_advice_rows"] for x in lg.values())
    has_failed_field = any("n_failed" in x["advice_counts"] or "n_failed" in x for x in lg.values())
    out.append(meas("t8_failures_kept_in_total", "tools.real_accept.advice_row_counts", "合成結果",
                    "失敗した助言 (advice.ok = False) 1 行", "助言行の総数 (n_rows) から失敗を消さない", "§5 I-10 (失敗を成功数から消さない)",
                    "pass" if got["n_advice"] == AGG_EXPECTED["n_advice"] else "fail",
                    f"n_rows 合計 {got['n_advice']} (失敗を含む)。再生の対象 (n_advice_rows) は {n_success_rows}", time.time() - t))
    out.append(meas("t8_failures_reported", "tools.real_accept (_load_logs / advice_row_counts)", "合成結果",
                    "失敗した助言 1 行", "失敗の件数が成功と別の欄で出る", "§5 I-10", "pass" if has_failed_field else "fail",
                    f"失敗の件数の欄なし。n_rows {got['n_advice']} − 再生の対象 {n_success_rows} の差からしか分からない "
                    f"(確定前・party の無い行も同じ差に入る)" if not has_failed_field else "欄あり", time.time() - t))
    # 版: 同じ版の 4 本 (断片を除く) は群が成立する
    t = time.time()
    infos = [{"name": n, "ident": lg[n]["ident"]} for n in list(lg)[:4] if n != "battle_20991231_000002.jsonl"]
    g = RA.group_consistency(infos)
    out.append(meas("t8_version_group_ok", "tools.real_accept.group_consistency", "合成結果", "同じ版の 3 本 (固定入力の異常の 1 本を除く)",
                    "群が成立", "AGG_SPEC", "pass" if g["ok"] else "fail", f"ok={g['ok']} notes={g['notes']}", time.time() - t))
    t = time.time()
    g_pin = RA.group_consistency([{"name": n, "ident": lg[n]["ident"]} for n in list(lg)[:4]])
    out.append(meas("t8_inputs_pin_marks_group", "tools.real_accept.group_consistency", "合成結果",
                    "固定入力の異常の行がある 1 本を含む群", "群が不成立 (失敗を成功に混ぜない)", "§5 I-10",
                    "pass" if not g_pin["ok"] else "fail", f"ok={g_pin['ok']}", time.time() - t))
    # ---- 誤った値を混ぜる
    # 誤った勝敗: 記録は勝ちだが、ランク画面の前に「負け」の文言
    t = time.time()
    bad = list(logs["battle_20991231_000001.jsonl"])
    bad.insert(-1, {"type": "events", "t": 1031.0, "fired": ["battle_lose"]})
    p = d.parent / "t8_bad_outcome.jsonl"
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in bad), encoding="utf-8")
    pb = AB._parse_battle(str(p))
    out.append(meas("t8_inject_wrong_outcome", "tools.battle_outcome.OutcomeTracker (analyze_battles)", "合成結果",
                    "勝ちの記録の後、ランク画面の前に負けの文言", "訂正 (corrected) として検出し、負けにする", "battle_outcome の規則",
                    "pass" if pb["outcome"] == "loss" and pb["corrected"] else "fail",
                    f"outcome={pb['outcome']} corrected={pb['corrected']}", time.time() - t))
    t = time.time()
    bad = [dict(x, outcome="won") if x.get("type") == "outcome" else x for x in logs["battle_20991231_000001.jsonl"]]
    p2 = d.parent / "t8_bad_outcome_value.jsonl"
    p2.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in bad), encoding="utf-8")
    s2 = AB.summarize([AB._parse_battle(str(p2))])
    out.append(meas("t8_inject_invalid_outcome_value", "tools.analyze_battles.summarize", "合成結果", "勝敗の値を規定外の 'won' に",
                    "規定外の値として報告する (不明に混ぜない)", "§5 I-10 (誤った勝敗)", "fail" if s2["n_decided"] == 0 else "pass",
                    f"n={s2['n']} n_decided={s2['n_decided']}: 規定外の値の報告はなく、勝敗未定と同じ扱い", time.time() - t))
    # 誤った候補: 第一候補が場の個体の技に無い
    t = time.time()
    chk = RA.check_best({"ok": True, "best": {"kind": "move", "id": "earthquake"}, "actions": [{"kind": "move", "id": "earthquake"}]},
                        compact, detail=True)
    out.append(meas("t8_inject_wrong_candidate", "tools.real_accept.check_best", "合成結果", "第一候補を場の個体に無い技 (じしん) に",
                    "違反 move_not_owned", "real_accept の合法性の検査", "pass" if "move_not_owned" in chk["violations"] else "fail",
                    f"violations={chk['violations']}", time.time() - t))
    # 誤った版: 1 本だけ commit が違う / version_id だけ書き換え
    t = time.time()
    infos_g = [{"name": n, "ident": lg[n]["ident"]} for n in ("battle_20991231_000001.jsonl", "battle_20991231_000003.jsonl")]
    other = [dict(x, rules=dict(x["rules"], git_commit="g2"), served_commit="g2") if x.get("type") == "version" else x
             for x in logs["battle_20991231_000004.jsonl"]]
    g2 = RA.group_consistency(infos_g + [{"name": "other", "ident": RA.version_identity(other)}])
    out.append(meas("t8_inject_wrong_version", "tools.real_accept.group_consistency", "合成結果", "3 本のうち 1 本の commit を変える",
                    "群が不成立", "§5 I-10 (誤った版)", "pass" if not g2["ok"] else "fail", f"ok={g2['ok']}", time.time() - t))
    t = time.time()
    tampered = [dict(x, version_id="ffffffffffff") if x.get("type") == "version" else x for x in logs["battle_20991231_000004.jsonl"]]
    g3 = RA.group_consistency(infos_g + [{"name": "tampered", "ident": RA.version_identity(tampered)}])
    from advisor.versions import version_split_of
    vs = version_split_of(tampered)
    out.append(meas("t8_inject_tampered_version_id", "tools.real_accept.group_consistency / advisor.versions", "合成結果",
                    "version 行の version_id だけを識別と合わない値に書き換える", "記録の不整合として検出する", "§5 I-10 (誤った版)",
                    "pass" if not g3["ok"] else "fail",
                    f"ok={g3['ok']} (version_id は比べない設計。version_split_of は identity_digest {vs['identity_digest']} を計算するが、"
                    f"記録の {vs['version_id']} と比べる読み手が無い)", time.time() - t))
    # 誤った費用: evaluated = True と cache_hit = True が同時 / t_eval が行の時刻より後
    t = time.time()
    rows_c = [dict(_adv_row(1, 10.0, compact), cache_hit=True), dict(_adv_row(2, 11.0, compact), t_eval=99.0)]
    c = evaluation_counts(rows_c)
    out.append(meas("t8_inject_wrong_cost", "battle_logger.evaluation_counts", "合成結果",
                    "evaluated = True と cache_hit = True が矛盾する行 / t_eval が行の時刻より後の行", "矛盾を数えて報告する",
                    "§5 I-10 (誤った費用)", "fail" if c["n_evaluated"] == 2 and "n_inconsistent" not in c else "pass",
                    f"{c}: 2 行とも実計算に数え、矛盾の報告なし", time.time() - t))
    det["agg_expected"] = AGG_EXPECTED
    return out, det


# ------------------------------------------------------------------ 依存・設定・記録量
# 子プロセス (新しいインタプリタ) で mods を import して増えたモジュールを分類する。この道具自身の import を数えないため
# record_contract を読み込まずに動く本文 (argv: リポジトリ, モジュールのカンマ区切り)
PROBE_CODE = """
import importlib, json, sys
from pathlib import Path
repo = str(Path(sys.argv[1]).resolve())
before = set(sys.modules)
for m in sys.argv[2].split(","):
    importlib.import_module(m)
proj, third, std = set(), set(), 0
for name in set(sys.modules) - before:
    f = getattr(sys.modules[name], "__file__", None) or ""
    if f and str(Path(f).resolve()).startswith(repo) and "site-packages" not in f:
        proj.add(name)
    elif "site-packages" in f:
        third.add(name.split(".")[0])
    else:
        std += 1
print(json.dumps({"project": sorted(proj), "third_party": sorted(third), "n_stdlib_or_builtin": std}))
"""


def config_refs(files: list) -> dict:
    """モジュールの本文から champions_agent.config の名前と、環境変数の名前 (文字列の定数) を拾う (純粋に近い: 読むだけ)"""
    names, envs = set(), set()
    for f in files:
        try:
            tree = ast.parse(Path(f).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        aliases = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module == "champions_agent.config":
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module == "champions_agent" and any(a.name == "config" for a in n.names):
                aliases.update(a.asname or a.name for a in n.names if a.name == "config")
            elif isinstance(n, ast.Import):
                aliases.update(a.asname for a in n.names if a.name == "champions_agent.config" and a.asname)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases and n.attr.isupper():
                names.add(n.attr)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "getenv") and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) and \
                    ("environ" in ast.unparse(n.func.value) or ast.unparse(n.func.value) == "os"):
                envs.add(n.args[0].value)
    return {"config_names": sorted(names), "env_literals": sorted(envs)}


def deps_of(mods: tuple) -> dict:
    r = subprocess.run([sys.executable, "-c", PROBE_CODE, str(REPO), ",".join(mods)],
                       cwd=str(REPO), capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, PYTHONPATH=str(REPO)))
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"error": (r.stderr or r.stdout)[-400:]}
    files = []
    for name in info["project"]:
        p = REPO / (name.replace(".", "/") + ".py")
        files.append(p if p.exists() else REPO / name.replace(".", "/") / "__init__.py")
    refs = config_refs(files)
    loc = sum(len(Path(f).read_text(encoding="utf-8").splitlines()) for f in files if Path(f).exists())
    return {"n_project_modules": len(info["project"]), "project_modules": info["project"], "third_party": info["third_party"],
            "n_stdlib_or_builtin": info["n_stdlib_or_builtin"], "n_config_names": len(refs["config_names"]),
            "config_names": refs["config_names"], "n_env_literals": len(refs["env_literals"]), "env_literals": refs["env_literals"],
            "project_lines": loc}


# ------------------------------------------------------------------ 出力
def render_report(manifest: dict, rows: list, details: dict, deps: dict, judgment: Optional[str]) -> str:
    L = [f"# I-10 ログ・版固定・再生・実験管理の記録契約 ({INH_I10_RUN_ID})", "",
         f"コード {manifest['code']['commit_base']} (作業ツリー {manifest['code']['worktree_head']}、未コミットの差分あり: "
         f"{manifest['code']['dirty']})。計算時間 {manifest['elapsed_sec']} 秒 / 上限 {INH_I10_TIME_BUDGET_SEC:.0f} 秒。"
         f"原本の変更なし: {manifest['originals_unchanged']}", ""]
    cnt: dict = {}
    for r in rows:
        cnt[r["result"]] = cnt.get(r["result"], 0) + 1
    L += [f"## 試験の結果 ({len(rows)} 件: {cnt})", "", "| case | 対象 | 結果 | 理由 |", "|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['case_id']} | {r['asset']} | {r['result']} | {str(r['reason']).replace('|', '/')[:400]} |")
    t1 = details.get("t1") or {}
    for side in ("existing", "minimal"):
        lo = (t1.get(side) or {}).get("loss")
        if not lo:
            continue
        L += ["", f"## 試験 1 の欄ごとの比較 ({side})", "",
              f"状態の欄 {lo['n_fields']}。状態 {lo['counts']} (zero_equiv = 0 と欄なし、empty_equiv = どちらも情報なし。"
              f"欠落に数えない)。欠落 (missing / changed) の evaluate の読み別 {lo['lost_by_use']}", ""]
        lost_rows = [x for x in lo["rows"] if x["status"] in LOSS]
        if lost_rows:
            L += ["| 欄 | 状態 | evaluate | advice 行に記録 | 保存前 | 復元 |", "|---|---|---|---|---|---|"]
            for x in lost_rows:
                L.append(f"| {x['path']} | {x['status']} | {x['use']} | {x.get('recorded', '-')} | "
                         f"{json.dumps(x['orig'], ensure_ascii=False)[:60]} | {json.dumps(x['restored'], ensure_ascii=False)[:60]} |")
        pert = (t1.get(side) or {}).get("perturb")
        if pert:
            L += ["", "読む欄の欠落の影響 (保存前の状態でその欄だけ別の値にして evaluate、推奨が変わったか):", ""]
            L += [f"- {n}: {e['n_set']} 箇所、変化 {e['changed']}" for n, e in pert.items()]
    ex, mn = t1.get("existing") or {}, t1.get("minimal") or {}
    L += ["", "## 記録量 (試験 1、決定 1 件)", "", "| 方式 | ファイル | 決定あたりの行 | manifest |", "|---|---|---|---|",
          f"| 既存 (対戦ログ) | {ex.get('bytes_file')} B ({ex.get('n_rows')} 行) | advice 行 {ex.get('bytes_advice_row')} B | "
          f"inputs_manifest + ピン (別) |",
          f"| 最小記録 | {mn.get('bytes_file')} B ({mn.get('n_rows')} 行) | obs {mn.get('bytes_obs_row')} B + advice "
          f"{mn.get('bytes_advice_row')} B | {mn.get('bytes_manifest')} B |"]
    L += ["", "## 依存数・設定項目 (import の実測と本文の走査)", "",
          "| 組 | 本体のモジュール | 本体の行数 | 外部パッケージ | config の名前 | 環境変数の文字列 |", "|---|---|---|---|---|---|"]
    for k, v in deps.items():
        if "error" in v:
            L.append(f"| {k} | 失敗: {v['error'][:80]} | | | | |")
            continue
        L.append(f"| {k} | {v['n_project_modules']} | {v['project_lines']} | {', '.join(v['third_party']) or 'なし'} | "
                 f"{v['n_config_names']} | {v['n_env_literals']} |")
    L += ["", "## 移植作業の時間", "", manifest.get("porting_time_note", "")]
    if judgment:
        L += ["", judgment.strip()]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="I-10 記録契約の試験")
    ap.add_argument("--src-repo", default=str(REPO), help="原本 (使用率 DB・実戦バンク・登録チーム) を読むリポジトリ (読み取りだけ)")
    ap.add_argument("--out", default=str(REPO / INH_I10_OUT_SUBDIR))
    ap.add_argument("--tmp-root", default=None)
    ap.add_argument("--porting-note", default="", help="移植作業の時間の記録 (report に書く)")
    ap.add_argument("--judgment", default=None, help="判定の節 (Markdown) のファイル。無ければ --out の judgment.md (あれば)")
    args = ap.parse_args()
    t_start = time.time()
    os.environ.setdefault("RL_BLEND_WEIGHT", "0")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(Path(args.src_repo), Path(args.tmp_root) if args.tmp_root else None)
    os.environ["CHAMPIONS_MODELS_DIR"] = str(ctx.root / "models_empty")      # 本体の RL 方策を読まない
    before = ctx.originals()
    ctx.setup()
    rows, details = [], {}
    log_dir = None
    tests = [("t1", lambda: t1(ctx)), ("t2", lambda: t2(ctx, log_dir)), ("t3", lambda: t3(ctx)), ("t4", lambda: t4(ctx, log_dir)),
             ("t5", lambda: t5(ctx, log_dir)), ("t6", lambda: t6(ctx)), ("t7", lambda: t7(ctx, log_dir)), ("t8", lambda: t8(ctx))]
    for name, fn in tests:
        if time.time() - t_start > INH_I10_TIME_BUDGET_SEC:
            rows.append(meas(f"{name}_all", "-", "合成結果", "-", "-", "-", "timeout", "計算時間の上限を超えたので実行しない", None))
            continue
        t = time.time()
        try:
            r, det = fn()
            rows += r
            details[name] = det
        except Exception as e:
            import traceback
            rows.append(meas(f"{name}_error", "-", "合成結果", "-", "-", "-", "unexecuted",
                             f"試験の道具の例外: {type(e).__name__}: {e} / {traceback.format_exc()[-600:]}", time.time() - t))
        if name == "t1":
            # 後の試験の入力: 試験 1 の既存の書き方で書いた対戦ログ (空 dir に写したもの)
            log_dir = ctx.root / "t1_existing_restore"
    after = ctx.originals()
    deps = {"既存 (記録 + 版固定 + 再生)": deps_of(EXISTING_MODULES), "既存 + 集計 (real_accept / analyze_battles)":
            deps_of(EXISTING_MODULES + AGG_MODULES), "最小記録": deps_of(MINIMAL_MODULES),
            "最小記録 + data_pin (engine に版を渡す)": deps_of(MINIMAL_MODULES + ("advisor.data_pin",))}
    for cid, (k, v) in zip(("m_deps_existing", "m_deps_existing_agg", "m_deps_minimal", "m_deps_minimal_pin"), deps.items()):
        rows.append(meas(cid, k, "観測事実", "import の実測 (新しいインタプリタ、import 時の閉包。関数内の遅延 import は数えない) "
                         "と本文の走査 (champions_agent.config の名前・環境変数の文字列)", "計測値 (合否なし。既存と最小記録の比較)",
                         "§5 I-10 指標 (依存数・設定項目)", "unexecuted" if "error" in v else "pass",
                         v.get("error") or f"本体のモジュール {v['n_project_modules']} ({v['project_lines']} 行) / 外部 {v['third_party']} / "
                         f"config の名前 {v['n_config_names']} / 環境変数 {v['n_env_literals']}", None))
    ex, mn = (details.get("t1") or {}).get("existing") or {}, (details.get("t1") or {}).get("minimal") or {}
    rows.append(meas("m_bytes_per_decision", "battle_logger / minimal_record", "観測事実", "試験 1 の 1 決定 (同じ観測状態・推奨・表示)",
                     "計測値 (合否なし)", "§5 I-10 指標 (記録量)", "pass" if ex and mn else "unexecuted",
                     f"既存: ファイル {ex.get('bytes_file')} B / advice 行 {ex.get('bytes_advice_row')} B。最小記録: ファイル "
                     f"{mn.get('bytes_file')} B / obs {mn.get('bytes_obs_row')} B + advice {mn.get('bytes_advice_row')} B / manifest "
                     f"{mn.get('bytes_manifest')} B", None))
    rows.append(meas("m_porting_time_minimal", "minimal_record", "観測事実", "最小記録の作成 (この担当のサブエージェントが書いた時間)",
                     "計測値 (合否なし)", "§5 I-10 指標 (移植作業の時間)", "pass" if args.porting_note else "unexecuted",
                     args.porting_note or "記録なし", None))
    rows.append(meas("m_porting_time_existing", "battle_logger / versions / data_pin / inputs_pin / advice_replay", "観測事実",
                     "既存の記録・版固定・再生を別のパッケージへ切り出す作業", "計測値", "§5 I-10 指標 (移植作業の時間)・§6",
                     "unexecuted", "切り出し (§6) はこの担当の範囲外として実施していない。依存数の実測だけを残す", None))
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"], capture_output=True, text=True).stdout.split("\n")
    manifest = {
        "test_id": INH_I10_TEST_ID, "run_id": INH_I10_RUN_ID, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "code": {"commit_base": INH_I10_CODE_COMMIT, "worktree_head": head, "dirty": [x for x in dirty if x.strip()],
                 "record_contract_sha256": sha256_full(Path(__file__)), "minimal_record_sha256": sha256_full(Path(MR.__file__))},
        "data_read": {"originals_sha256_before": before, "originals_sha256_after": after,
                      "fixture_state_sha256": sha256_full(FIXTURE_STATE), "bundle_snapshot": ctx.snap,
                      "bundle": {k: ctx.pin_man["usage_bundle"].get(k) for k in ("snapshot_id", "latest_at_pin", "sha16", "tables")},
                      "bank_sha16": ctx.bank_sha16},
        "originals_unchanged": before == after,
        "conditions": {"python": sys.version.split()[0], "rl_blend_weight": 0, "scoring_modes": list(MODES), "display_search": False,
                       "tmp_root": str(ctx.root), "models_dir": "合成 (一時ディレクトリ)", "seed": None,
                       "seed_note": "乱数を使わない (evaluate は決定的な入力で 2 方式)"},
        "time_budget_sec": INH_I10_TIME_BUDGET_SEC, "elapsed_sec": round(time.time() - t_start, 1),
        "porting_time_note": args.porting_note,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (out_dir / "measurements.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with (out_dir / "cases.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"test_id": INH_I10_TEST_ID, "case_id": r["case_id"], "input": r["input"], "public_info_boundary": BOUNDARY,
                                "expected": r["expected"], "expected_source": r["expected_source"], "split": r["split"]},
                               ensure_ascii=False, default=str) + "\n")
    (out_dir / "details.json").write_text(json.dumps({"details": details, "deps": deps}, ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
    jp = Path(args.judgment) if args.judgment else out_dir / "judgment.md"
    judgment = jp.read_text(encoding="utf-8") if jp.exists() else None
    (out_dir / "report.md").write_text(render_report(manifest, rows, details, deps, judgment), encoding="utf-8")
    shutil.rmtree(ctx.root, ignore_errors=True)
    cnt: dict = {}
    for r in rows:
        cnt[r["result"]] = cnt.get(r["result"], 0) + 1
    print(f"I-10: {len(rows)} 件 {cnt}、{manifest['elapsed_sec']} 秒、原本の変更なし {manifest['originals_unchanged']} → {out_dir}")


if __name__ == "__main__":
    main()
