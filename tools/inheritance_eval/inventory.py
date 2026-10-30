"""引き継ぎ評価 I-00: データ・モデル・実験記録の資産台帳 (docs/INHERITANCE_EVALUATION_PLAN_20261031.md §5 I-00、2026-10-31 開発者指示)。

    python -m tools.inheritance_eval.inventory --root <本体ツリー> --out <出力 dir> [--replay]

本体ツリー (--root) の資産を読み取りだけで棚卸しし、出力 dir に assets.jsonl / measurements.jsonl / cases.jsonl / manifest.json を書く
(report.md は人が書く。この道具は書かない)。原本には書かない:
  - 使用率 DB は file:...?mode=ro で開き、sqlite の backup で scratch (--scratch) に一貫した写しを作ってから数える
  - 記事バンクは本文を読まない (件数・出典の URL・方針・状態の欄だけ)
  - モデルは実行しない (ファイルの hash と、同じ dir に揃っている付属ファイルだけ)。--replay の 1 決定の再生も RL 方策を読まない
    (CHAMPIONS_MODELS_DIR を空の dir、RL_BLEND_WEIGHT=0)
資産の区分 (config.INH_ASSET_CATEGORIES): 観測事実 / 推定値 / 生成物 / 実対戦結果 / 合成結果 / 規則参照。AI の推奨は正解にしない。
純粋な分類 (ログの完了・欠損、資産の区分、画像と対戦の対応、登録の差分、run の状態、記事の方針、モデルの付属の揃い) は
tests/test_inheritance_inventory.py。
"""
from __future__ import annotations

import argparse
import calendar
import collections
import csv
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from champions_agent.config import (INH_ASSET_CATEGORIES, INH_CODE_COMMIT, INH_INV_EXCLUDE_DIRS, INH_INV_FRAME_SLACK_SEC,
                                    INH_INV_FULL_HASH_BUDGET_SEC, INH_INV_MODEL_EXTS, INH_INV_SAMPLE_HASH_BYTES,
                                    INH_INV_SELECTION_COMPANIONS, INH_RUN_ID, INH_TIME_BUDGET_SEC)
from vision.scenes import SCENE_COMMAND, SCENE_MOVE_SELECT, SCENE_SELECTION

TEST_ID = "I-00"
REPO = Path(__file__).resolve().parent.parent.parent

# 区分 (config の並びの名前で参照する。文字列を散らさない)
CAT_FACT, CAT_EST, CAT_GEN, CAT_REAL, CAT_SYNTH, CAT_RULE = INH_ASSET_CATEGORIES

# 用途 (計画書 §5 I-00 の判定: 正解・失敗再現・候補セット・比較基準)
USES = ("正解", "失敗再現", "候補セット", "比較基準")

# 対戦ログの行の種類 → 区分 (battle_logger の docstring の行の意味から。認識した状態・イベントは推定値、勝敗の行は実対戦結果
# (推定の印つきは推定値)、助言は生成物 (AI の推奨は正解にしない)、版・実行状態の行は観測事実 (その時点の実行環境の記録))
LOG_ROW_CATEGORY = {
    "scene": CAT_EST, "events": CAT_EST, "hp": CAT_EST, "rate": CAT_EST, "roster_change": CAT_EST,
    "guess_confirm": CAT_EST, "opp_picks": CAT_EST, "my_hp_trace": CAT_EST, "manual_fix": CAT_FACT,
    "manual_fix_overwritten": CAT_EST, "advice": CAT_GEN, "selection_record": CAT_GEN, "display": CAT_FACT,
    "decision": CAT_EST, "version": CAT_FACT, "runtime": CAT_FACT, "session": CAT_FACT, "frames": CAT_FACT,
    "client": CAT_FACT, "visibility": CAT_FACT, "frame_burst": CAT_FACT, "inputs_pin_error": CAT_FACT,
    "outcome": CAT_REAL,
}

# 資産の種類 → 区分 (assets.jsonl の categories)
ASSET_CATEGORIES = {
    "battle_log": [CAT_FACT, CAT_EST, CAT_GEN, CAT_REAL],
    "frame_image": [CAT_FACT],
    "my_team": [CAT_FACT],
    "usage_db": [CAT_FACT, CAT_GEN],
    "usage_db_schema": [CAT_GEN],
    "usage_db_empty": [],
    "smogon_stats": [CAT_FACT],
    "smogon_manifest": [CAT_GEN],
    "article_list": [CAT_FACT],
    "article_bank": [CAT_GEN],
    "article_candidate": [CAT_GEN],
    "article_policy": [CAT_FACT],
    "article_log": [CAT_FACT],
    "build_run": [CAT_SYNTH, CAT_GEN],
    "model": [CAT_GEN],
    "registry_index": [CAT_GEN],
    "failure_scenes": [CAT_GEN, CAT_EST],
    "incident_report": [CAT_FACT],
    "known_issues": [CAT_FACT, CAT_EST],
}

# 対戦ログの完了の区分 (最後の outcome 行で決める)
COMPLETION_LABELS = {
    "win_loss_observed": "勝敗あり (勝負の文言・画面から読んだ)",
    "win_loss_corrected": "勝敗あり (訂正行で確定)",
    "win_loss_inferred": "勝敗あり (推定。レートの増減・ひんしの観測)",
    "win_loss_manual": "勝敗あり (手入力)",
    "unknown": "終了の行はあるが勝敗不明",
    "no_outcome": "終了の行なし (途中で切れた・対戦外)",
}
OUTCOME_FINAL = ("win", "loss")


# ------------------------------------------------------------------ 純粋: 共通
def asset_categories(kind: str) -> list:
    """資産の種類の区分 (純粋)。知らない種類は ValueError (黙って空にしない)"""
    if kind not in ASSET_CATEGORIES:
        raise ValueError(f"資産の種類 {kind} の区分が無い")
    return list(ASSET_CATEGORIES[kind])


def row_category(row: dict) -> str:
    """対戦ログの 1 行の区分 (純粋)。outcome の推定の印つきは推定値、知らない type は推定値 (事実扱いしない)"""
    typ = row.get("type")
    if typ == "outcome" and row.get("inferred"):
        return CAT_EST
    return LOG_ROW_CATEGORY.get(typ, CAT_EST)


def dup_stats(items: Iterable) -> dict:
    """重複の集計 (純粋)。items = [(sha, bytes)]。sha が None の項目は数えない。
    {"n", "n_unique", "n_dup_files" (2 個目以降), "dup_rate", "bytes", "dup_bytes", "dup_bytes_rate"}"""
    seen: dict = {}
    n = n_dup = total = dup_b = 0
    for sha, size in items:
        if sha is None:
            continue
        n += 1
        total += int(size or 0)
        if sha in seen:
            n_dup += 1
            dup_b += int(size or 0)
        else:
            seen[sha] = 1
    return {"n": n, "n_unique": len(seen), "n_dup_files": n_dup, "dup_rate": round(n_dup / n, 4) if n else None,
            "bytes": total, "dup_bytes": dup_b, "dup_bytes_rate": round(dup_b / total, 4) if total else None}


def measurement(case_id: str, asset: str, category: str, input_, expected, expected_source: str, result: str,
                reason: str, elapsed_sec: float, split: str = "diag", value=None) -> dict:
    """measurements.jsonl の 1 行 (純粋。共通規約の欄)"""
    if result not in ("pass", "fail", "unexecuted", "timeout", "excluded"):
        raise ValueError(f"result {result}")
    if category not in INH_ASSET_CATEGORIES:
        raise ValueError(f"category {category}")
    row = {"test_id": TEST_ID, "case_id": case_id, "asset": asset, "category": category, "input": input_,
           "expected": expected, "expected_source": expected_source, "split": split, "result": result,
           "reason": reason, "elapsed_sec": round(float(elapsed_sec), 3)}
    if value is not None:
        row["value"] = value
    return row


def iso_local(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(t)))


# ------------------------------------------------------------------ 純粋: 対戦ログ
def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _opp_named(state: Optional[dict]) -> int:
    party = (((state or {}).get("opponent") or {}).get("party")) or []
    return sum(1 for p in party if isinstance(p, dict) and (p.get("ja") or p.get("species")))


def log_summary(records: list, decision_gap_sec: float) -> dict:
    """1 本の対戦ログの要約 (純粋)。records = 行の dict の列 (読めなかった行は呼び出し側が数えて渡さない)。
    decision_gap_sec = 場面の行からの決定点の間引き (tools.advice_replay.DECISION_GAP_SEC と同じ値を渡す)"""
    types = collections.Counter()
    cats = collections.Counter()
    ts = []
    versions, sessions, outcomes = [], [], []
    adv = {"battle": 0, "selection": 0, "with_state": 0, "no_state": 0, "no_turn": 0, "schema": collections.Counter(),
           "with_data_version": 0, "with_ev_infer": 0}
    scene_decisions = 0
    last_dec_t = -1e18
    opp_full = 0
    n_battle_scene = 0
    rosters = collections.Counter()
    for r in records:
        typ = r.get("type")
        types[typ] += 1
        cats[row_category(r)] += 1
        t = _num(r.get("t"))
        if t is not None:
            ts.append(t)
        if typ == "version":
            rules, team = r.get("rules") or {}, r.get("team") or {}
            sel, act = r.get("selection_model") or {}, r.get("action_policy") or {}
            versions.append({"version_id": r.get("version_id"), "git_commit": (rules.get("git_commit") or "")[:12] or None,
                             "usage_snapshot": rules.get("usage_snapshot"), "team_sha16": team.get("sha256"),
                             "team_species": team.get("species"), "registry_sha16": team.get("registry_sha256"),
                             "has_registry": isinstance(team.get("registry"), dict),
                             "selection_sha16": sel.get("sha256"), "selection_source": sel.get("source"),
                             "action_sha16": act.get("sha256"), "selection_features": rules.get("selection_features"),
                             "embedding_sha16": rules.get("embedding_sha256"), "dex_sha16": rules.get("dex_sha256"),
                             "regulation": rules.get("regulation") or r.get("regulation") or r.get("format"),
                             "designated_package": r.get("designated_package"), "t": t})
        elif typ == "session":
            sessions.append({k: r.get(k) for k in ("source", "package_id", "dataset_kind", "data_quality")})
        elif typ == "outcome":
            outcomes.append({"outcome": r.get("outcome"), "inferred": bool(r.get("inferred")),
                             "corrected": ("corrected_from" in r) or ("revised_from" in r), "manual": bool(r.get("manual")),
                             "t": t})
        elif typ == "advice":
            kind = r.get("kind")
            if kind in ("battle", "selection"):
                adv[kind] += 1
            st = r.get("state")
            if isinstance(st, dict) and st:
                adv["with_state"] += 1
                v = st.get("state_schema")
                adv["schema"][v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else 1] += 1
            else:
                adv["no_state"] += 1
            if r.get("turn") is None and (st or {}).get("turn") is None:
                adv["no_turn"] += 1
            if isinstance(r.get("data_version"), dict):
                adv["with_data_version"] += 1
            if isinstance(r.get("ev_infer"), dict):
                adv["with_ev_infer"] += 1
        elif typ == "scene":
            sc = r.get("scene")
            st = r.get("state") or {}
            if sc == SCENE_SELECTION:
                opp_full = max(opp_full, _opp_named(st))
                names = tuple(p.get("ja") for p in ((st.get("player") or {}).get("party") or []) if isinstance(p, dict))
                if len(names) == 6 and all(names):
                    rosters[names] += 1
            if sc in (SCENE_COMMAND, SCENE_MOVE_SELECT):
                n_battle_scene += 1
                if (st.get("player") or {}).get("party") and t is not None and t - last_dec_t >= decision_gap_sec:
                    scene_decisions += 1
                    last_dec_t = t
    adv["schema"] = {str(k): v for k, v in sorted(adv["schema"].items())}
    return {"n_rows": sum(types.values()), "types": dict(types), "row_categories": dict(cats),
            "t_first": min(ts) if ts else None, "t_last": max(ts) if ts else None,
            "versions": versions, "sessions": sessions, "outcomes": outcomes, "advice": adv,
            "n_decision_rows": types.get("decision", 0), "n_events": types.get("events", 0),
            "n_scene_decisions": scene_decisions, "n_battle_scene_rows": n_battle_scene, "opp_named_max": opp_full,
            "n_inputs_pin_error": types.get("inputs_pin_error", 0),
            "my_roster_ja": list(rosters.most_common(1)[0][0]) if rosters else None}


def roster_vs_version(version_species: Optional[list], roster_ids: Optional[list]) -> Optional[bool]:
    """version 行の team.species (ファイルを開いた時点の登録・直近のロスターから作った 6 体) が、その対戦の選出画面で読んだ自分の
    6 体 (種族 id に解決したもの) と同じか (純粋)。どちらかが無ければ None (判定不能)。フォルム違い (rotomwash 等) は呼び出し側で解決済み"""
    if not version_species or not roster_ids or len(roster_ids) != 6 or any(r is None for r in roster_ids):
        return None
    return sorted(version_species) == sorted(roster_ids)


def log_completion(summary: dict) -> str:
    """完了の区分 (純粋。COMPLETION_LABELS の鍵)。最後の outcome 行で決める。訂正行 (corrected_from / revised_from) で勝敗が
    決まれば win_loss_corrected、手入力は win_loss_manual、推定の印は win_loss_inferred"""
    outs = summary.get("outcomes") or []
    if not outs:
        return "no_outcome"
    last = outs[-1]
    if last.get("outcome") not in OUTCOME_FINAL:
        return "unknown"
    if last.get("corrected"):
        return "win_loss_corrected"
    if last.get("manual"):
        return "win_loss_manual"
    if last.get("inferred"):
        return "win_loss_inferred"
    return "win_loss_observed"


def log_missing(summary: dict) -> list:
    """欠損の理由 (純粋。日本語の短い語の list、順序は固定)"""
    out = []
    adv = summary.get("advice") or {}
    vers = summary.get("versions") or []
    n_adv = adv.get("battle", 0) + adv.get("selection", 0)
    if not vers:
        out.append("version 行なし (動いていた版が分からない)")
    else:
        if all(v.get("usage_snapshot") is None for v in vers):
            out.append("使用率 snapshot の記録なし")
        if not any(v.get("has_registry") for v in vers):
            out.append("登録 (team.registry) の記録なし")
    if not any(v.get("regulation") for v in vers):
        out.append("規制 (regulation / format) の記録なし")
    if n_adv == 0:
        out.append("advice 行なし")
    elif adv.get("no_state"):
        out.append("state の無い advice 行あり")
    if adv.get("no_turn"):
        out.append("turn の無い advice 行あり")
    if not summary.get("n_events"):
        out.append("events 行なし")
    if not summary.get("n_battle_scene_rows"):
        out.append("決定画面 (command / move_select) の場面なし")
    if not summary.get("outcomes"):
        out.append("勝敗の行なし")
    return out


def log_uses(summary: dict, completion: str) -> dict:
    """用途ごとに使えるか (純粋)。
    正解: 勝敗 (実対戦結果) だけ。認識した状態は推定値なので事実の正解にはしない。推定の勝敗は除く
    失敗再現: 助言が見た状態 (advice 行の state) がある → その助言の入力を復元できる (scene 行だけなら近似の復元)
    候補セット: 選出画面で相手 6 体の名前が読めた (相手の構築の候補。名前は推定値)
    比較基準: 勝敗があり、version 行で版が分かる (旧システムの実戦の参考。版が分からなければ「版不明」)"""
    adv = summary.get("advice") or {}
    truth = completion in ("win_loss_observed", "win_loss_corrected", "win_loss_manual")
    if adv.get("with_state"):
        repro = "advice_state"
    elif summary.get("n_scene_decisions"):
        repro = "scene_only"
    else:
        repro = None
    has_ver = bool(summary.get("versions"))
    base = None
    if completion.startswith("win_loss"):
        base = "版あり" if has_ver else "版不明"
    return {"正解": truth, "失敗再現": repro, "候補セット": summary.get("opp_named_max", 0) >= 6, "比較基準": base}


def log_class(summary: dict, completion: str) -> str:
    """1 本の対戦ログの総合の分類 (純粋): 有効な完了試合 (勝敗が観測・訂正・手入力で決まり、決定画面がある) / 勝敗推定 /
    勝敗不明 / 未完了 / 対戦外 (決定画面も advice も無い)"""
    adv = summary.get("advice") or {}
    if not summary.get("n_battle_scene_rows") and not (adv.get("battle") or 0):
        return "対戦外 (決定画面・対戦の助言なし)"
    if completion in ("win_loss_observed", "win_loss_corrected", "win_loss_manual"):
        return "完了 (勝敗確定)"
    if completion == "win_loss_inferred":
        return "完了 (勝敗推定)"
    if completion == "unknown":
        return "終了 (勝敗不明)"
    return "未完了 (勝敗の行なし)"


def season_estimate(t: Optional[float], snapshots: list) -> Optional[dict]:
    """ログの時刻の規制・シーズンの推定 (純粋。推定値)。snapshots = [{"id", "fetched_epoch", "format", "source_month", "source"}]。
    t 以前に取得した最後の snapshot (smogon 等の別形式は除いて champions の形式だけ) の format / source_month。無ければ None"""
    if t is None:
        return None
    best = None
    for s in snapshots:
        if not str(s.get("format") or "").startswith("champions"):
            continue
        fe = s.get("fetched_epoch")
        if fe is None or fe > t:
            continue
        if best is None or fe > best["fetched_epoch"]:
            best = s
    if best is None:
        return None
    return {"format": best.get("format"), "season": best.get("source_month"), "snapshot_id": best.get("id"),
            "basis": "ログの最初の時刻より前に取得した最後の使用率 snapshot (推定値。ログに規制の記録は無い)"}


def parse_db_time(s: Optional[str]) -> Optional[float]:
    """usage_snapshot.fetched_at (sqlite の datetime('now') = UTC の 'YYYY-MM-DD HH:MM:SS') → epoch (純粋)"""
    try:
        return float(calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 純粋: 画像
FRAME_RE = re.compile(r"^([a-z]+)_(\d{9,11})\.(png|jpe?g)$", re.I)
BURST_FILE_RE = re.compile(r"^(\d{12,14})\.(png|jpe?g)$", re.I)


def frame_info(relpath: str) -> dict:
    """debug_frames 以下の相対パス → {"prefix", "t", "ext"} (純粋)。frame_/sel_/fc_ 等は秒、burst/burst_<秒>/<ミリ秒>.jpg は burst"""
    parts = relpath.split("/")
    name = parts[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    m = FRAME_RE.match(name)
    if m and len(parts) == 1:
        return {"prefix": m.group(1).lower() + "_", "t": float(m.group(2)), "ext": ext}
    m = BURST_FILE_RE.match(name)
    if m and len(parts) >= 2 and parts[0] == "burst":
        return {"prefix": "burst", "t": int(m.group(1)) / 1000.0, "ext": ext}
    return {"prefix": "other", "t": None, "ext": ext}


def match_frames(frame_ts: list, spans: list, slack: float) -> dict:
    """画像の時刻を対戦ログの期間に対応づける (純粋)。spans = [(log 名, t_first, t_last)]、slack = 前後の余裕。
    1 枚が複数の期間に入るときは期間の中心に近いログ。{"by_log": {log: 枚数}, "n_matched", "n_unmatched"}"""
    by: dict = collections.Counter()
    n_un = 0
    sp = [(n, a - slack, b + slack, (a + b) / 2) for n, a, b in spans if a is not None and b is not None]
    for t in frame_ts:
        if t is None:
            n_un += 1
            continue
        hit = [x for x in sp if x[1] <= t <= x[2]]
        if not hit:
            n_un += 1
            continue
        by[min(hit, key=lambda x: abs(t - x[3]))[0]] += 1
    return {"by_log": dict(by), "n_matched": sum(by.values()), "n_unmatched": n_un}


# ------------------------------------------------------------------ 純粋: 登録チーム
def team_diff(prev: Optional[dict], cur: dict) -> dict:
    """登録 (my_team.json の dict) の差分 (純粋): 入った個体・出た個体・型が変わった個体 (名前の list、順序は cur / prev の順)"""
    prev = prev or {}
    return {"added": [k for k in cur if k not in prev], "removed": [k for k in prev if k not in cur],
            "changed": [k for k in cur if k in prev and prev[k] != cur[k]]}


def registry_sha16(registry) -> Optional[str]:
    """advisor.my_team.registry_sha と同じ規則 (sort_keys・区切りの空白なし・ensure_ascii=False の UTF-8 の sha256 先頭 16 桁)"""
    if not isinstance(registry, dict):
        return None
    raw = json.dumps(registry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def bak_interval(name: str, mtime: float) -> tuple:
    """登録の写しの中身が使われていた期間の推定 (純粋。推定値): my_team.json.bak.YYYYMMDD は「その日に置き換える前の写し」と読み、
    [ファイルの mtime (中身を最後に書いた時刻), 名前の日付の終わり (ローカル時刻の 24:00)) とする。my_team.json (今の登録) は
    [mtime, None)。名前から日付が読めなければ (mtime, None)"""
    m = re.search(r"\.bak\.(\d{8})$", name)
    if not m:
        return mtime, None
    end = time.mktime(time.strptime(m.group(1), "%Y%m%d")) + 86400
    return mtime, end


def team_coverage(species_ids: list, registry: dict, id_of) -> dict:
    """version 行の team.species (種族 id 6 体) が登録 (日本語名の dict) にどれだけ入っているか (純粋)。id_of(日本語名, entry) → 種族 id。
    {"n", "registered", "with_moves", "missing": [id]}。技 4 つが登録されていれば with_moves"""
    ids = {}
    for ja, e in (registry or {}).items():
        sid = id_of(ja, e)
        if sid:
            ids[sid] = e
    reg = [s for s in species_ids or [] if s in ids]
    wm = [s for s in reg if len((ids[s] or {}).get("技") or []) >= 4]
    return {"n": len(species_ids or []), "registered": len(reg), "with_moves": len(wm),
            "missing": [s for s in species_ids or [] if s not in ids]}


# ------------------------------------------------------------------ 純粋: 構築 run
def run_status(names: Iterable, final_names: Iterable) -> str:
    """構築 run の状態 (純粋)。names = run dir の直下の名前、final_names = final/ の直下の名前"""
    names, final_names = set(names), set(final_names)
    if {"manifest.json", "evaluation.json"} <= final_names:
        return "完了 (final あり)"
    if "evaluation" in names and ("advisors" in names or "advisors_screen" in names):
        return "測定の途中で停止 (final なし)"
    if "s06_sets.json" in names:
        return "探索のみ (S6 まで。測定なし)"
    return "探索の途中で停止"


RUN_CONDITION_KEYS = ("regulation", "pool_source", "pool_snapshot", "meta_snapshot", "meta_pin", "profile", "search_mode",
                      "showdown_commit", "llm")


def run_condition(manifest: dict) -> dict:
    """run の比較条件 (純粋)。規制・相手プール・操縦 (pick_policy・models_dir は final の manifest)・版"""
    out = {k: manifest.get(k) for k in RUN_CONDITION_KEYS}
    out["sealed_id"] = (manifest.get("opponent_split") or {}).get("sealed_id")
    out["git_commit"] = (manifest.get("git_commit") or "")[:12] or None
    return out


def run_valid_measure(status: str, evaluation: Optional[dict]) -> dict:
    """run の測定が有効な測定として読めるか (純粋): 完了し、封印 holdout の verdict・n・区間があるか。
    勝率の出典は旧 AI (旧操縦・旧選出モデル) のシミュレーション = 合成結果。新 AI の正解にしない"""
    h = (evaluation or {}).get("s12_holdout") or {}
    ok = status.startswith("完了") and h.get("verdict") is not None and h.get("n") and h.get("ci") is not None
    return {"valid": bool(ok), "verdict": h.get("verdict"), "delta": h.get("delta"), "ci": h.get("ci"), "n": h.get("n"),
            "source": "旧 AI のシミュレーション (合成結果)。新 AI の正解にしない"}


# ------------------------------------------------------------------ 純粋: 記事
def host_of(url: Optional[str]) -> Optional[str]:
    try:
        h = urlparse(str(url or "").strip()).hostname
    except ValueError:
        return None
    return h.lower() if h else None


def policy_of(host: Optional[str], policies: dict) -> tuple:
    """ホストの方針 (純粋。host_policy.json の規則): 完全一致、無ければ match = suffix の項目を接尾辞で照合 (最長一致)。
    戻り値 (鍵 or None, 項目 or None)。無ければ (None, None) = 方針なし (unknown と同じ扱い)"""
    if not host:
        return None, None
    if host in policies and not str(host).startswith("_"):
        return host, policies[host]
    best = None
    for k, v in policies.items():
        if k.startswith("_") or not isinstance(v, dict) or v.get("match") != "suffix":
            continue
        if host == k or host.endswith("." + k):
            if best is None or len(k) > len(best):
                best = k
    return (best, policies[best]) if best else (None, None)


def fetch_allowed(url: str, policies: dict) -> tuple:
    """URL が今の方針で取得可か (純粋): fetch = allow で、allowed_urls があればその中にあること。(可否, 理由)"""
    host = host_of(url)
    key, pol = policy_of(host, policies)
    if pol is None:
        return False, "方針なし"
    if pol.get("fetch") != "allow":
        return False, f"fetch={pol.get('fetch')}"
    allowed = pol.get("allowed_urls")
    if allowed and url.rstrip("/") not in {u.rstrip("/") for u in allowed}:
        return False, "allowed_urls の外"
    return True, "allow" + (" (manifest の selected だけ)" if pol.get("allowed_from") == "manifest" else "")


# ------------------------------------------------------------------ 純粋: モデル
def model_ext_of(name: str, exts: Iterable = INH_INV_MODEL_EXTS) -> Optional[str]:
    """モデルの拡張子 (純粋)。'x.zip' / 'x.zip.prev' / 'x.zip.incompatible' のような退避名も数える。無ければ None"""
    low = name.lower()
    for e in exts:
        if low.endswith(e) or (e + ".") in low:
            return e
    return None


def excluded_dir(name: str, patterns: Iterable = INH_INV_EXCLUDE_DIRS) -> bool:
    """モデルの探索で入らない dir か (純粋。fnmatch のパターン: .venv / .venv312 / .venv39 等)"""
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def model_group_of(relpath: str) -> str:
    """モデルの置き場の群 (純粋。台帳の 1 行 = 1 群)"""
    p = relpath.replace("\\", "/")
    if p.startswith("champions_agent/train/checkpoints/"):
        rest = p[len("champions_agent/train/checkpoints/"):]
        if "/" not in rest:
            return "checkpoints (配備・退避名)"
        return "checkpoints/" + rest.split("/")[0]
    if p.startswith("logs/pinned_models/"):
        return "pinned_models (測定のピンの写し)"
    if p.startswith("logs/registry/selection_model/"):
        return "registry/selection_model"
    if p.startswith("logs/registry/package/"):
        return "registry/package"
    m = re.match(r"logs/build_search/runs/[^/]+/(advisors|advisors_screen|final|evaluation|sealed)/", p)
    if m:
        return f"build_search/runs/*/{m.group(1)}"
    return "/".join(p.split("/")[:2]) + " (その他)"


def selection_companions(dir_names: Iterable, companions: Iterable = INH_INV_SELECTION_COMPANIONS) -> list:
    """選出モデル (.pt) の dir に足りない付属ファイル (純粋。埋め込み・meta)"""
    s = set(dir_names)
    return [c for c in companions if c not in s]


# ------------------------------------------------------------------ 純粋: 失敗記録
def count_known_issues(text: str) -> dict:
    """docs/KNOWN_ISSUES.md の件数 (純粋): 章 (## )・ファミリー (### )・状態の印のある表の行 (✅ / 🟡 / 👁)"""
    sec = fam = 0
    marks = collections.Counter()
    for line in text.splitlines():
        if line.startswith("### "):
            fam += 1
        elif line.startswith("## "):
            sec += 1
        elif line.startswith("|") and not re.match(r"^\|\s*-", line):
            for mk in ("✅", "🟡", "👁"):
                if mk in line:
                    marks[mk] += 1
                    break
    return {"sections": sec, "families": fam, "rows_by_state": dict(marks), "rows_with_state": sum(marks.values())}


# ------------------------------------------------------------------ 副作用: hash
class Budget:
    """計算時間の上限 (config.INH_TIME_BUDGET_SEC) と全件 hash の上限"""

    def __init__(self, total: float, full_hash: float):
        self.t0 = time.time()
        self.total = total
        self.full_hash = full_hash
        self.hash_spent = 0.0

    def elapsed(self) -> float:
        return time.time() - self.t0

    def left(self) -> float:
        return self.total - self.elapsed()


def sha_file(path: Path, budget: Optional[Budget] = None) -> tuple:
    """(sha256 の先頭 16 桁, 方式 "full" / "sample")。全件 hash の時間を使い切ったら先頭・末尾の抽出 hash"""
    t0 = time.time()
    if budget is not None and budget.hash_spent >= budget.full_hash:
        return sha_sample(path), "sample"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if budget is not None:
        budget.hash_spent += time.time() - t0
    return h.hexdigest()[:16], "full"


def sha_sample(path: Path, n: int = INH_INV_SAMPLE_HASH_BYTES) -> str:
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(n))
        if size > 2 * n:
            f.seek(-n, os.SEEK_END)
            h.update(f.read(n))
    return "s" + h.hexdigest()[:15]


def _read_jsonl(path: Path) -> tuple:
    rows, bad = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return rows, bad


# ------------------------------------------------------------------ 副作用: 各資産の走査
def scan_db(root: Path, scratch: Path, budget: Budget) -> tuple:
    """使用率 DB: mode=ro で開き、sqlite の backup で scratch に写しを作り、写しで数える。(assets, snapshots, copy のパス)"""
    db = root / "champions_agent" / "data" / "db" / "champions.sqlite3"
    schema = root / "champions_agent" / "data" / "db" / "schema.sql"
    empty = root / "champions_agent" / "data" / "db" / "champions.db"
    assets, snaps = [], []
    scratch.mkdir(parents=True, exist_ok=True)
    copy = scratch / "champions_ro_copy.sqlite3"
    sha_orig, how = sha_file(db, budget)
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    if copy.exists():
        copy.unlink()
    dst = sqlite3.connect(str(copy))
    src.backup(dst)
    src.close()
    tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    snap_tables = [t for t in tables if "snapshot_id" in {c[1] for c in dst.execute(f"PRAGMA table_info({t})")}]
    per = collections.defaultdict(dict)
    for t in snap_tables:
        for sid, n in dst.execute(f"SELECT snapshot_id, COUNT(*) FROM {t} GROUP BY snapshot_id"):
            per[sid][t] = n
    for sid, src_, fmt, cut, month, nb, url, fetched in dst.execute(
            "SELECT id, source, format, rating_cutoff, source_month, number_of_battles, source_url, fetched_at "
            "FROM usage_snapshot ORDER BY id"):
        snaps.append({"id": sid, "source": src_, "format": fmt, "rating_cutoff": cut, "source_month": month,
                      "number_of_battles": nb, "source_host": host_of(url), "fetched_at": fetched,
                      "fetched_epoch": parse_db_time(fetched), "rows": per.get(sid, {})})
    dst.close()
    schema_text = schema.read_text(encoding="utf-8")
    schema_tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema_text)
    by_src = collections.Counter((s["source"], s["format"]) for s in snaps)
    assets.append({"asset_id": "usage_db", "kind": "usage_db", "path": str(db.relative_to(root)), "n_files": 1,
                   "bytes": db.stat().st_size, "sha16": sha_orig, "hash_method": how, "format": "sqlite3",
                   "categories": asset_categories("usage_db"), "readable": True,
                   "origin": "championsbattledata / pokedb / smogon の取得 (champions_agent.data.ingest)",
                   "period": {"first": snaps[0]["fetched_at"] if snaps else None, "last": snaps[-1]["fetched_at"] if snaps else None},
                   "details": {"read": "file:...?mode=ro → sqlite backup の写し (scratch) で集計", "tables": counts,
                               "schema_tables_missing_in_db": [t for t in schema_tables if t not in tables],
                               "db_tables_not_in_schema": [t for t in tables if t not in schema_tables and t != "sqlite_sequence"],
                               "n_snapshots": len(snaps), "snapshots_by_source_format": {f"{a}|{b}": n for (a, b), n in by_src.items()},
                               "snapshots": [{k: s[k] for k in ("id", "source", "format", "rating_cutoff", "source_month",
                                                                 "number_of_battles", "fetched_at", "rows")} for s in snaps]},
                   "uses": {"正解": False, "失敗再現": "当時の snapshot id があれば再生の入力に使える", "候補セット": True,
                            "比較基準": "snapshot id を固定すれば比較の入力に使える"},
                   "missing": ["規制 (regulation) は format 'champions-singles' と source_month だけ (M-1 等)。期間の終わりは無い"]})
    assets.append({"asset_id": "usage_db_schema", "kind": "usage_db_schema", "path": str(schema.relative_to(root)), "n_files": 1,
                   "bytes": schema.stat().st_size, "sha16": sha_file(schema)[0], "format": "sql",
                   "categories": asset_categories("usage_db_schema"), "readable": True, "origin": "リポジトリ (追跡)",
                   "details": {"tables": schema_tables}, "uses": {u: False for u in USES}, "missing": []})
    assets.append({"asset_id": "usage_db_champions_db", "kind": "usage_db_empty", "path": str(empty.relative_to(root)),
                   "n_files": 1, "bytes": empty.stat().st_size if empty.exists() else None, "sha16": None, "format": "空ファイル",
                   "categories": asset_categories("usage_db_empty"), "readable": empty.exists(),
                   "origin": "不明 (0 バイト。設定上の DB は champions.sqlite3)",
                   "uses": {u: False for u in USES}, "missing": ["中身なし。使用率 DB と同一視しない (保存の対象外)"]})
    return assets, snaps, copy


def scan_battle_logs(root: Path, snaps: list, budget: Budget) -> tuple:
    from tools.advice_replay import DECISION_GAP_SEC
    d = root / "logs" / "battles"
    assets, summaries = [], {}
    for p in sorted(d.glob("*.jsonl")):
        try:
            rows, bad = _read_jsonl(p)
            readable, err = True, None
        except OSError as e:
            rows, bad, readable, err = [], 0, False, repr(e)
        s = log_summary(rows, DECISION_GAP_SEC)
        comp = log_completion(s)
        sha, how = sha_file(p, budget)
        s.update(name=p.name, completion=comp, cls=log_class(s, comp), missing=log_missing(s), uses=log_uses(s, comp),
                 bad_lines=bad, sha16=sha, bytes=p.stat().st_size)
        summaries[p.name] = s
        vers = s["versions"]
        assets.append({
            "asset_id": f"battle_log:{p.name}", "kind": "battle_log", "path": str(p.relative_to(root)), "n_files": 1,
            "bytes": s["bytes"], "sha16": sha, "hash_method": how, "format": "jsonl (battle_logger)",
            "categories": asset_categories("battle_log"), "readable": readable and bad == 0, "read_error": err,
            "period": {"first": iso_local(s["t_first"]), "last": iso_local(s["t_last"])},
            "regulation": {"recorded": next((v["regulation"] for v in vers if v.get("regulation")), None),
                           "estimated": season_estimate(s["t_first"], snaps)},
            "origin": "server.py + battle_logger.py (実機の画面認識)",
            "versions": {"n_version_rows": len(vers),
                         "version_ids": sorted({v["version_id"] for v in vers if v.get("version_id")}),
                         "git_commits": sorted({v["git_commit"] for v in vers if v.get("git_commit")}),
                         "usage_snapshots": sorted({v["usage_snapshot"] for v in vers if v.get("usage_snapshot") is not None}),
                         "team_sha16": sorted({v["team_sha16"] for v in vers if v.get("team_sha16")}),
                         "registry_sha16": sorted({v["registry_sha16"] for v in vers if v.get("registry_sha16")}),
                         "selection_sha16": sorted({v["selection_sha16"] for v in vers if v.get("selection_sha16")}),
                         "action_sha16": sorted({v["action_sha16"] for v in vers if v.get("action_sha16")}),
                         "sessions": s["sessions"]},
            "completion": comp, "class": s["cls"],
            "outcomes": [o["outcome"] + ("(推定)" if o["inferred"] else "") + ("(訂正)" if o["corrected"] else "")
                         + ("(手入力)" if o["manual"] else "") for o in s["outcomes"]],
            "counts": {"rows": s["n_rows"], "advice_battle": s["advice"]["battle"], "advice_selection": s["advice"]["selection"],
                       "advice_with_state": s["advice"]["with_state"], "advice_no_state": s["advice"]["no_state"],
                       "advice_no_turn": s["advice"]["no_turn"], "state_schema": s["advice"]["schema"],
                       "decision_rows": s["n_decision_rows"], "scene_decisions": s["n_scene_decisions"],
                       "events": s["n_events"], "outcome_rows": len(s["outcomes"]),
                       "corrected_outcome_rows": sum(1 for o in s["outcomes"] if o["corrected"]),
                       "inputs_pin_error": s["n_inputs_pin_error"], "bad_lines": bad},
            "row_categories": s["row_categories"], "missing": s["missing"], "uses": s["uses"]})
    return assets, summaries


def scan_frames(root: Path, summaries: dict, budget: Budget) -> tuple:
    d = root / "debug_frames"
    files = sorted(p for p in d.rglob("*") if p.is_file())
    groups = collections.defaultdict(list)
    for p in files:
        rel = str(p.relative_to(d))
        info = frame_info(rel)
        sha, how = sha_file(p, budget)
        groups[info["prefix"]].append({"rel": rel, "t": info["t"], "ext": info["ext"], "bytes": p.stat().st_size,
                                       "sha16": sha, "how": how})
    spans = [(n, s["t_first"], s["t_last"]) for n, s in summaries.items()]
    assets = []
    all_items = []
    for prefix, items in sorted(groups.items()):
        ts = [i["t"] for i in items]
        m = match_frames(ts, spans, INH_INV_FRAME_SLACK_SEC)
        all_items += items
        tv = [t for t in ts if t is not None]
        assets.append({"asset_id": f"frames:{prefix}", "kind": "frame_image", "path": "debug_frames/" + (
            "burst/" if prefix == "burst" else prefix + "*"), "n_files": len(items),
            "bytes": sum(i["bytes"] for i in items), "sha16": None,
            "hash_method": "+".join(sorted({i["how"] for i in items})), "format": dict(collections.Counter(i["ext"] for i in items)),
            "categories": asset_categories("frame_image"), "readable": True, "origin": "server.py のデバッグ保存 (DEBUG_DUMP_FRAMES 等)",
            "period": {"first": iso_local(min(tv)) if tv else None, "last": iso_local(max(tv)) if tv else None},
            "matched_logs": len(m["by_log"]), "n_matched": m["n_matched"], "n_unmatched": m["n_unmatched"],
            "duplicates": dup_stats((i["sha16"], i["bytes"]) for i in items),
            "details": {"n_burst_dirs": len({i["rel"].split("/")[1] for i in items}) if prefix == "burst" else None,
                        "matched_by_log": m["by_log"]},
            "uses": {"正解": "静止画として人がラベルを付ければ事実の正解の素材 (今は未ラベル)", "失敗再現": m["n_matched"] > 0,
                     "候補セット": False, "比較基準": False},
            "missing": (["連続映像ではない (burst 以外は疎な静止画)"] if prefix != "burst" else ["burst は 1 回 30 秒前後の連続 JPEG"])
            + (["対戦ログに対応しない画像がある"] if m["n_unmatched"] else [])})
    return assets, dup_stats((i["sha16"], i["bytes"]) for i in all_items), groups


def scan_team(root: Path) -> tuple:
    cfg = root / "config"
    paths = sorted(cfg.glob("my_team.json.bak.*")) + [cfg / "my_team.json"]
    assets, docs = [], {}
    prev = None
    prev_name = None
    for p in paths:
        raw = p.read_bytes()
        try:
            doc = json.loads(raw)
            ok, err = isinstance(doc, dict), None
        except ValueError as e:
            doc, ok, err = {}, False, repr(e)
        docs[p.name] = doc
        diff = team_diff(prev, doc) if prev is not None else None
        n_full = sum(1 for e in doc.values() if isinstance(e, dict) and len(e.get("技") or []) >= 4 and e.get("持ち物"))
        assets.append({"asset_id": f"my_team:{p.name}", "kind": "my_team", "path": str(p.relative_to(root)), "n_files": 1,
                       "bytes": len(raw), "sha16": hashlib.sha256(raw).hexdigest()[:16], "registry_sha16": registry_sha16(doc),
                       "format": "json (日本語名 → 型)", "categories": asset_categories("my_team"), "readable": ok,
                       "read_error": err, "origin": "手入力 (scripts/register_my_team.sh / 直接編集)",
                       "period": {"mtime": iso_local(p.stat().st_mtime)},
                       "counts": {"n_entries": len(doc), "n_full_sets": n_full},
                       "diff_from": prev_name, "diff": diff,
                       "uses": {"正解": "自分の型の事実 (登録した時点)", "失敗再現": "当時の型の候補 (ログとの対応は species で推定)",
                                "候補セット": True, "比較基準": False},
                       "missing": (["版の欄が無い (sha と mtime だけで区別)"])})
        prev, prev_name = doc, p.name
    return assets, docs


def scan_smogon(root: Path) -> list:
    d = root / "logs" / "usage_smogon"
    out = []
    for p in sorted(d.rglob("*.json")):
        is_man = p.name.endswith(".manifest.json")
        raw = p.read_bytes()
        sha_full = hashlib.sha256(raw).hexdigest()
        det, missing = {}, []
        if is_man:
            m = json.loads(raw)
            data = p.with_name(p.name.replace(".manifest.json", ".json"))
            actual = hashlib.sha256(data.read_bytes()).hexdigest() if data.exists() else None
            det = {k: m.get(k) for k in ("source", "month", "format", "cutoff", "number_of_battles", "fetched_at_utc", "convert_version")}
            det["data_sha_matches_manifest"] = actual == m.get("sha256")
            det["source_host"] = host_of(m.get("url"))
            if actual != m.get("sha256"):
                missing.append("元 JSON の sha256 が manifest と一致しない")
        else:
            j = json.loads(raw)
            info = j.get("info") or {}
            det = {"info": info, "n_species": len(j.get("data") or {})}
        out.append({"asset_id": f"smogon:{p.relative_to(d)}", "kind": "smogon_manifest" if is_man else "smogon_stats",
                    "path": str(p.relative_to(root)), "n_files": 1, "bytes": len(raw), "sha16": sha_full[:16],
                    "format": "json (Smogon chaos)" if not is_man else "json (取得の manifest)",
                    "categories": asset_categories("smogon_manifest" if is_man else "smogon_stats"), "readable": True,
                    "origin": "smogon.com/stats の取得 (2026-10-30)", "details": det,
                    "uses": {"正解": False, "失敗再現": False, "候補セット": not is_man, "比較基準": not is_man},
                    "missing": missing + ([] if is_man else ["Showdown 上の BSS 統計。実機 (ゲーム内) の使用率ではない"])})
    return out


def scan_articles(root: Path) -> list:
    d = root / "logs" / "articles"
    pol = json.loads((d / "host_policy.json").read_text(encoding="utf-8"))
    out = []
    # blogs.csv: URL と シーズンの欄だけ読む (根拠文言・タイトルは読まない)
    rows = []
    with open(d / "blogs.csv", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"url": (r.get("根拠記事URL") or r.get("ブログURL") or "").strip(), "season": (r.get("シーズン") or "").strip()})
    by_policy = collections.Counter()
    by_host = collections.Counter()
    n_allowed = 0
    for r in rows:
        h = host_of(r["url"])
        k, p = policy_of(h, pol)
        by_policy[f"{k or '方針なし'}:{(p or {}).get('fetch', 'none')}"] += 1
        by_host[h] += 1
        ok, _why = fetch_allowed(r["url"], pol)
        n_allowed += ok
    raw = (d / "blogs.csv").read_bytes()
    out.append({"asset_id": "articles:blogs.csv", "kind": "article_list", "path": "logs/articles/blogs.csv", "n_files": 1,
                "bytes": len(raw), "sha16": hashlib.sha256(raw).hexdigest()[:16], "format": "csv (URL 一覧)",
                "categories": asset_categories("article_list"), "readable": True, "origin": "人と調査セッションの収集 (seeker)",
                "counts": {"n_rows": len(rows), "n_hosts": len(by_host), "n_with_url": sum(1 for r in rows if r["url"]),
                           "n_season_blank": sum(1 for r in rows if not r["season"]), "n_fetch_allowed_now": n_allowed},
                "details": {"by_policy": dict(by_policy.most_common()), "top_hosts": dict(by_host.most_common(8))},
                "uses": {"正解": False, "失敗再現": False, "候補セット": "URL のみ。型は本文の取得・解析が要る", "比較基準": False},
                "missing": ["本文は保存していない (URL・シーズンのみ)", "シーズンの空欄あり"]})
    # 記事バンク (構造化した型): 本文ではなく case の source・status・規制の欄だけ読む
    for bank in sorted((d / "bank").iterdir()):
        cases, _bad = _read_jsonl(bank / "cases.jsonl")
        man = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
        st = collections.Counter(c.get("status") for c in cases)
        hosts = collections.Counter(host_of((c.get("source") or {}).get("url")) for c in cases)
        regs = collections.Counter((c.get("meta") or {}).get("regulation") for c in cases)
        size = sum(p.stat().st_size for p in bank.iterdir())
        out.append({"asset_id": f"articles:bank/{bank.name}", "kind": "article_bank", "path": str(bank.relative_to(root)),
                    "n_files": len(list(bank.iterdir())), "bytes": size,
                    "sha16": hashlib.sha256((bank / "cases.jsonl").read_bytes()).hexdigest()[:16],
                    "format": f"jsonl ({man.get('schema')}, parser {man.get('parser')})",
                    "categories": asset_categories("article_bank"), "readable": True,
                    "origin": "tools.team_build.article_bank (取得した記事の解析)",
                    "counts": {"n_cases": len(cases), "by_status": dict(st), "n_synthetic": man.get("n_synthetic")},
                    "details": {"by_host": dict(hosts), "by_regulation": dict(regs),
                                "by_record_kind": dict(collections.Counter(c.get("record_kind") for c in cases))},
                    "uses": {"正解": False, "失敗再現": False, "候補セット": st.get("ok", 0), "比較基準": False},
                    "missing": ["1 ホスト (編集部の記事 1 ページ) だけ。使用実績の根拠 (usage_evidence) なし"]})
    for c in sorted((d / "candidates").glob("*.json")):
        j = json.loads(c.read_text(encoding="utf-8"))
        out.append({"asset_id": f"articles:candidates/{c.name}", "kind": "article_candidate", "path": str(c.relative_to(root)),
                    "n_files": 1, "bytes": c.stat().st_size, "sha16": hashlib.sha256(c.read_bytes()).hexdigest()[:16],
                    "format": "json (取得の記録と構造化の結果)", "categories": asset_categories("article_candidate"),
                    "readable": True, "origin": "tools.team_build.articles_fetch",
                    "details": {"host": j.get("host"), "fetched_at": j.get("fetched_at"), "saved_body": j.get("saved"),
                                "n_records": len(j.get("records") or [])},
                    "uses": {"正解": False, "失敗再現": False, "候補セット": "バンクの元", "比較基準": False},
                    "missing": [] if j.get("saved") else ["本文は保存していない (saved=false)"]})
    for name, kind in (("host_policy.json", "article_policy"), ("access.jsonl", "article_log"), ("state.jsonl", "article_log"),
                       ("sync.jsonl", "article_log"), ("manifest.json", "article_log")):
        p = d / name
        if not p.exists():
            continue
        raw = p.read_bytes()
        cnt = len(raw.splitlines()) if name.endswith(".jsonl") else None
        det = {}
        if name == "host_policy.json":
            det = {"by_fetch": dict(collections.Counter(v.get("fetch") for k, v in pol.items() if not k.startswith("_"))),
                   "schema": pol.get("_schema")}
        if name == "access.jsonl":
            acc, _ = _read_jsonl(p)
            det = {"by_host_status_mode": dict(collections.Counter(f"{a.get('host')}|{a.get('status')}|{a.get('mode')}" for a in acc))}
        out.append({"asset_id": f"articles:{name}", "kind": kind, "path": str(p.relative_to(root)), "n_files": 1,
                    "bytes": len(raw), "sha16": hashlib.sha256(raw).hexdigest()[:16], "format": name.rsplit(".", 1)[-1],
                    "categories": asset_categories(kind), "readable": True, "origin": "記事の取得の運用",
                    "counts": {"n_lines": cnt} if cnt is not None else {}, "details": det,
                    "uses": {u: False for u in USES}, "missing": []})
    return out


def scan_runs(root: Path, budget: Budget) -> list:
    d = root / "logs" / "build_search" / "runs"
    out = []
    conds = {}
    for rd in sorted(x for x in d.iterdir() if x.is_dir()):
        m = json.loads((rd / "manifest.json").read_text(encoding="utf-8"))
        names = [x.name for x in rd.iterdir()]
        fin = rd / "final"
        fnames = [x.name for x in fin.iterdir()] if fin.is_dir() else []
        st = run_status(names, fnames)
        ev = None
        fm = {}
        if (fin / "evaluation.json").exists():
            ev = json.loads((fin / "evaluation.json").read_text(encoding="utf-8"))
        if (fin / "manifest.json").exists():
            fm = json.loads((fin / "manifest.json").read_text(encoding="utf-8"))
        cond = run_condition(m)
        cond.update(pick_policy=fm.get("pick_policy"), models_dir=Path(fm["models_dir"]).name if fm.get("models_dir") else None)
        conds[rd.name] = cond
        size = sum(p.stat().st_size for p in rd.rglob("*") if p.is_file())
        val = run_valid_measure(st, ev)
        out.append({"asset_id": f"build_run:{rd.name}", "kind": "build_run", "path": str(rd.relative_to(root)),
                    "n_files": sum(1 for p in rd.rglob("*") if p.is_file()), "bytes": size,
                    "sha16": hashlib.sha256((rd / "manifest.json").read_bytes()).hexdigest()[:16], "hash_method": "manifest.json のみ",
                    "format": "dir (manifest.json + 段ごとの json + 測定)", "categories": asset_categories("build_run"),
                    "readable": True, "origin": "tools.team_build.run (シミュレーション)", "period": {"created": m.get("created")},
                    "regulation": {"recorded": m.get("regulation"), "season_pin": (m.get("season_pin") or {}).get("source")},
                    "status": st, "condition": cond, "valid_measure": val,
                    "final_candidate": fm.get("candidate_id"), "final_selection_sha16": (fm.get("selection_model_sha256") or "")[:16] or None,
                    "uses": {"正解": False, "失敗再現": "run.log と段の json で手順を追える",
                             "候補セット": "s05_candidates / s06_sets (旧採点で絞った候補)" if "s06_sets.json" in names else False,
                             "比較基準": "旧 AI の参考値 (合成結果)" if val["valid"] else False},
                    "missing": ([] if m.get("regulation") else ["規制の記録なし (2026-10-07 より前)"])
                    + ([] if st.startswith("完了") else ["final なし"])})
    # 同条件の run の数 (条件の鍵が全部同じ)
    key_of = {n: json.dumps({k: v for k, v in c.items() if k not in ("git_commit",)}, sort_keys=True) for n, c in conds.items()}
    groups = collections.Counter(key_of.values())
    for a in out:
        n = a["asset_id"].split(":", 1)[1]
        a["n_same_condition"] = groups[key_of[n]]
    return out


def scan_models(root: Path, budget: Budget) -> tuple:
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [x for x in dns if not excluded_dir(x)]
        for fn in fns:
            if model_ext_of(fn):
                files.append(Path(dp) / fn)
    groups = collections.defaultdict(list)
    for p in sorted(files):
        rel = str(p.relative_to(root))
        sha, how = sha_file(p, budget)
        groups[model_group_of(rel)].append({"rel": rel, "bytes": p.stat().st_size, "sha16": sha, "how": how,
                                            "ext": model_ext_of(p.name), "name": p.name})
    allf = [i for g in groups.values() for i in g]
    sha_dirs = collections.defaultdict(set)
    for i in allf:
        sha_dirs[i["sha16"]].add(model_group_of(i["rel"]))
    assets = []
    for g, items in sorted(groups.items()):
        sel = [i for i in items if i["name"].startswith("selection_model") and i["ext"] == ".pt"]
        sel_missing = 0
        for i in sel:
            dn = [x.name for x in (root / i["rel"]).parent.iterdir()]
            if selection_companions(dn):
                sel_missing += 1
        rl = [i for i in items if i["ext"] == ".zip"]
        assets.append({"asset_id": f"models:{g}", "kind": "model", "path": g, "n_files": len(items),
                       "bytes": sum(i["bytes"] for i in items), "sha16": None,
                       "hash_method": "+".join(sorted({i["how"] for i in items})),
                       "format": dict(collections.Counter(i["ext"] for i in items)),
                       "categories": asset_categories("model"), "readable": True,
                       "origin": "champions_agent の学習・tools.team_build の適応 (adapt)",
                       "duplicates": dup_stats((i["sha16"], i["bytes"]) for i in items),
                       "dup_outside_group": sum(1 for i in items if len(sha_dirs[i["sha16"]]) > 1),
                       "counts": {"selection_pt": len(sel), "selection_pt_missing_emb_or_meta": sel_missing, "rl_zip": len(rl)},
                       "uses": {"正解": False, "失敗再現": "版 (sha) が対戦ログの version 行と一致すれば当時の推奨の再計算に使える",
                                "候補セット": False, "比較基準": "特徴量・埋め込み・規制が揃えば OLD の診断用"},
                       "missing": (["選出モデルの .pt と同じ dir に埋め込み・meta が無い"] if sel_missing else [])
                       + (["RL 方策の観測の版はファイルの外に記録が無い (読み込んで初めて分かる)"] if rl else [])})
    return assets, dup_stats((i["sha16"], i["bytes"]) for i in allf), allf


def scan_registry(root: Path) -> list:
    p = root / "logs" / "registry" / "index.jsonl"
    rows, bad = _read_jsonl(p)
    last = {}
    for r in rows:
        last[r["id"]] = r
    by = collections.Counter(f"{r.get('kind')}|{r.get('status')}" for r in last.values())
    missing_dirs = sum(1 for r in last.values() if not (root / "logs" / "registry" / r["kind"] / r["id"]).exists())
    return [{"asset_id": "registry:index.jsonl", "kind": "registry_index", "path": str(p.relative_to(root)), "n_files": 1,
             "bytes": p.stat().st_size, "sha16": hashlib.sha256(p.read_bytes()).hexdigest()[:16], "format": "jsonl (追記)",
             "categories": asset_categories("registry_index"), "readable": bad == 0, "origin": "tools.team_build.registry",
             "counts": {"n_rows": len(rows), "n_ids": len(last), "by_kind_status": dict(by), "ids_without_dir": missing_dirs,
                        "n_production": sum(1 for r in last.values() if r.get("status") == "production")},
             "uses": {"正解": False, "失敗再現": "成果物の sha と run の対応", "候補セット": "package の species",
                      "比較基準": "package の holdout (合成結果)"},
             "missing": (["production の行なし (配備中の選出モデルは registry 外の checkpoints/selection_model.pt)"]
                         if not any(r.get("status") == "production" for r in last.values()) else [])}]


def scan_failures(root: Path) -> list:
    out = []
    p = root / "logs" / "scenes" / "newteam_1006.jsonl"
    rows, bad = _read_jsonl(p)
    st = collections.Counter(r.get("labels_status") for r in rows)
    cat = collections.Counter(r.get("category") for r in rows)
    truth_filled = sum(1 for r in rows if any(v not in (None, "", [], {}) for k, v in (r.get("truth") or {}).items()))
    srcs = collections.Counter((r.get("source") or {}).get("file") for r in rows)
    out.append({"asset_id": "failures:newteam_1006.jsonl", "kind": "failure_scenes", "path": str(p.relative_to(root)),
                "n_files": 1, "bytes": p.stat().st_size, "sha16": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                "format": "jsonl (tools.scene_samples)", "categories": asset_categories("failure_scenes"),
                "readable": bad == 0, "origin": "tools.scene_samples (整合 20 + 失敗 10 の抽出)",
                "counts": {"n_rows": len(rows), "labels_status": dict(st), "category": dict(cat), "truth_filled": truth_filled,
                           "n_source_logs": len(srcs)},
                "uses": {"正解": False, "失敗再現": sum(n for c, n in cat.items() if c != "consistent"), "候補セット": False,
                         "比較基準": False},
                "missing": ["正解 (truth) 未記入", "整合 20 + 失敗 10 の抽出 (発生率の推定に使わない)"]})
    inc = sorted((root / "docs" / "incidents" / "reports").glob("*.md"))
    out.append({"asset_id": "failures:incident_reports", "kind": "incident_report", "path": "docs/incidents/reports/",
                "n_files": len(inc), "bytes": sum(x.stat().st_size for x in inc), "sha16": None, "format": "md",
                "categories": asset_categories("incident_report"), "readable": True, "origin": "人と運用セッションの記録 (追跡)",
                "period": {"first": inc[0].name[:10] if inc else None, "last": inc[-1].name[:10] if inc else None},
                "uses": {"正解": False, "失敗再現": len(inc), "候補セット": False, "比較基準": False},
                "missing": ["再現の入力 (ログ・画像) への参照は文中の記述だけ"]})
    k = root / "docs" / "KNOWN_ISSUES.md"
    ki = count_known_issues(k.read_text(encoding="utf-8"))
    out.append({"asset_id": "failures:KNOWN_ISSUES.md", "kind": "known_issues", "path": "docs/KNOWN_ISSUES.md", "n_files": 1,
                "bytes": k.stat().st_size, "sha16": hashlib.sha256(k.read_bytes()).hexdigest()[:16], "format": "md",
                "categories": asset_categories("known_issues"), "readable": True, "origin": "接続テスト・監査の台帳",
                "counts": ki, "uses": {"正解": False, "失敗再現": ki["rows_with_state"], "候補セット": False, "比較基準": False},
                "missing": ["ファミリー単位。個別事象の入力への参照は無い"]})
    return out


# ------------------------------------------------------------------ 副作用: 復元の確認 (cases)
def _species_id_of(resolver):
    def f(ja, entry):
        sid = (entry or {}).get("種族ID")
        if sid:
            return sid
        r = resolver.resolve_species(ja, cutoff=0.85)
        return r[1] if r else None
    return f


def patch_default_db(db_copy: Path):
    """champions_agent.data.database.get_connection (data_pin を通らない既定の DB の読み取り) を写しの読み取り専用に差し替える。
    戻り値 = 元に戻す関数。2026-10-31 の確認: tools.evolve_teams._usage_alternatives (登録の本文の配分・性格の補完) と
    advisor.gimmick._stone_usage (engine._opp_mega_forms の石の事前分布、snapshot_id なし) がこの経路で最新 snapshot を読む
    (版を固定しない)。原本を開かず、ワークツリーに空の DB を作らないための差し替えで、版は固定しないまま"""
    from contextlib import contextmanager
    from champions_agent.data import database as DB

    @contextmanager
    def _ro_copy(db_path=None):
        c = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()
    orig = DB.get_connection
    DB.get_connection = _ro_copy

    def undo():
        DB.get_connection = orig
    return undo


def current_team_sha(root: Path, db_copy: Path, snapshot: Optional[int]) -> dict:
    """今の登録 (本体の config/my_team.json) と本体の直近のロスターから、version 行の team.sha256 と同じ規則の sha16 を作る
    (advisor.versions._registered_team と同じ計算。DB は写しを版で固定して読む)。副作用: data_pin・my_team の上書き"""
    from advisor import data_pin as DP
    from advisor import my_team as MT
    from advisor.versions import sha256_text
    import tools.evaluate_team as ET
    import tools.evolve_teams as EV
    reg = json.loads((root / "config" / "my_team.json").read_text(encoding="utf-8"))
    orig = ET._latest_selection_roster
    DP.set_override(usage_db=db_copy, usage_snapshot=snapshot, team_registry=reg)
    ET._latest_selection_roster = lambda log_dir=None: orig(root / "logs" / "battles")
    EV._usage_cache = None
    undo = patch_default_db(db_copy)
    try:
        from tools.team_build.run import registered_team
        text, ids, _m = registered_team()
        return {"sha16": sha256_text(text) if text else None, "species": ids, "snapshot": snapshot,
                "roster_source": "本体の logs/battles の直近 5 本の選出画面 (tools.evaluate_team._latest_selection_roster)",
                "spread_fill_snapshot": "写しの最新 (tools.evolve_teams._usage_alternatives は版を固定しない)"}
    finally:
        ET._latest_selection_roster = orig
        undo()
        EV._usage_cache = None
        DP.clear_override()


def _first_advice_with_state(records: list, kind: str = "battle") -> Optional[dict]:
    for r in records:
        if r.get("type") == "advice" and r.get("kind") == kind and isinstance(r.get("state"), dict) and r.get("state"):
            return r
    return None


def case_battle_log(root: Path, name: str, label: str, summaries: dict, db_copy: Path, snaps: list, cur_team: dict,
                    team_docs: dict, all_models: list, replay: bool) -> tuple:
    """実戦ログ 1 本の復元の確認。戻り値 (case, measurements)"""
    from tools.inputs_pin import data_version_at, restore_plan, team_registry_at
    from tools.advice_replay import restore_unknowns, scoring_inputs_missing
    t0 = time.time()
    p = root / "logs" / "battles" / name
    recs, _ = _read_jsonl(p)
    s = summaries[name]
    cid = f"log_{label}"
    adv = _first_advice_with_state(recs)
    t = adv.get("t") if adv else (s["t_first"])
    want = data_version_at(recs, t)
    ms = []
    case = {"test_id": TEST_ID, "case_id": cid, "asset": f"battle_log:{name}", "split": "diag",
            "input": {"log": name, "advice_t": t, "advice_id": (adv or {}).get("advice_id")},
            "public_boundary": "advice 行の state (その時点で画面から読めた情報の簡約) だけを入力にする。後の行は使わない",
            "expected": {"当時の型": "version 行の team.registry か team.sha256 が今の登録と一致", "状態": "advice 行の state を復元",
                         "使用モデル": "version 行の selection / action の sha が手元のファイルにある", "結果": "最後の outcome 行"},
            "expected_source": "対戦ログの version / advice / outcome 行 (観測事実 / 推定値 / 実対戦結果)"}
    # 1. 当時の型
    reg_rec = team_registry_at(recs, t)
    snap_ids = [x["id"] for x in snaps]
    plan = restore_plan(want, snap_ids, cur_team.get("sha16"), {}, "restore", max(snap_ids), team_registry=reg_rec,
                        current_registry_sha16=registry_sha16(team_docs.get("my_team.json")))
    ver = (s["versions"] or [None])[-1]
    if ver is None:
        res, why = "fail", "version 行なし。当時の型・版は分からない"
    elif reg_rec is not None:
        res, why = ("pass", "team.registry と sha が整合") if not any("registry" in x for x in plan["reasons"]) else ("fail", "; ".join(plan["reasons"]))
    elif want.get("team_sha16") and want["team_sha16"] == cur_team.get("sha16"):
        res, why = "pass", "team.sha256 が今の登録の本文の sha と一致 (team.registry の記録は無い)"
    else:
        res, why = "fail", (f"team.registry の記録なし。team.sha256 {want.get('team_sha16')} が今の登録の本文の sha "
                            f"{cur_team.get('sha16')} (snapshot {cur_team.get('snapshot')} で計算) と違う")
    cov = None
    if ver and ver.get("team_species"):
        from vision.normalize import NameResolver
        idf = _species_id_of(NameResolver())
        cov = {n: team_coverage(ver["team_species"], d, idf) for n, d in team_docs.items()}
    ms.append(measurement(cid + ":team", f"battle_log:{name}", CAT_FACT, {"t": t, "team_sha16": want.get("team_sha16")},
                          "当時の自分の型を sha で一意に復元", "version 行 (観測事実)", res, why, time.time() - t0,
                          value={"species": (ver or {}).get("team_species"), "coverage_by_team_file": cov}))
    vr = s.get("version_vs_roster")
    ms.append(measurement(cid + ":team_vs_roster", f"battle_log:{name}", CAT_EST,
                          {"version_species": (ver or {}).get("team_species"), "roster_ids": s.get("roster_ids")},
                          "version 行の 6 体 = その対戦の選出画面の自分の 6 体", "scene 行 (選出画面の読み = 推定値)",
                          {True: "pass", False: "fail", None: "unexecuted"}[vr],
                          {True: "一致", False: "不一致 (version 行はファイルを開いた時点の登録・前のロスターから作る)",
                           None: "version 行か選出画面の 6 体が無い (判定不能)"}[vr], 0.0))
    # 2. 状態
    t1 = time.time()
    if adv is None:
        ms.append(measurement(cid + ":state", f"battle_log:{name}", CAT_EST, {"t": t}, "advice 行の state を復元",
                              "advice 行 (推定値)", "fail", "state のある advice 行なし (scene 行の近似だけ)", time.time() - t1,
                              value={"scene_decisions": s["n_scene_decisions"]}))
    else:
        unk = restore_unknowns(adv)
        miss = scoring_inputs_missing(adv)
        ms.append(measurement(cid + ":state", f"battle_log:{name}", CAT_EST,
                              {"t": t, "state_schema": adv["state"].get("state_schema", 1)},
                              "advice 行の state を照合できない項目なしで復元", "advice 行 (推定値。認識した状態で事実の正解ではない)",
                              "pass" if not unk else "fail",
                              "照合できない項目なし" if not unk else "照合できない項目: " + ", ".join(unk), time.time() - t1,
                              value={"restore_unknowns": unk, "scoring_inputs_missing": miss,
                                     "n_advice_with_state": s["advice"]["with_state"]}))
    # 3. 使用モデル
    t2 = time.time()
    if ver is None:
        ms.append(measurement(cid + ":model", f"battle_log:{name}", CAT_FACT, {}, "使用モデルの sha が分かり手元にある",
                              "version 行", "fail", "version 行なし", time.time() - t2))
    else:
        sel, act = ver.get("selection_sha16"), ver.get("action_sha16")
        ws, wa = model_presence(sel, all_models), model_presence(act, all_models)
        ms.append(measurement(cid + ":model", f"battle_log:{name}", CAT_FACT, {"selection_sha16": sel, "action_sha16": act},
                              "使用モデルの sha が分かり手元にある", "version 行 (観測事実)", "pass" if ws and wa else "fail",
                              f"選出モデル {len(ws)} か所 / 行動方策 {len(wa)} か所に同じ sha のファイル", time.time() - t2,
                              value={"selection_found_at": ws[:5], "action_found_at": wa[:5]}))
    # 4. データの版 (inputs_pin.restore_plan)
    t3 = time.time()
    ms.append(measurement(cid + ":data_version", f"battle_log:{name}", CAT_FACT, {"recorded": want},
                          "当時の使用率 snapshot・登録を tools.inputs_pin.restore_plan で復元", "version 行 + 手元の DB の写し",
                          "pass" if plan["ok"] else "fail", "; ".join(plan["reasons"]) or "復元できる", time.time() - t3,
                          value={"plan_used": plan["used"], "notes": plan["notes"]}))
    # 5. 結果
    t4 = time.time()
    comp = s["completion"]
    ms.append(measurement(cid + ":outcome", f"battle_log:{name}", CAT_REAL if comp != "win_loss_inferred" else CAT_EST,
                          {"outcome_rows": len(s["outcomes"])}, "勝敗が確定 (観測・訂正・手入力)", "outcome 行 (実対戦結果)",
                          "pass" if comp in ("win_loss_observed", "win_loss_corrected", "win_loss_manual") else "fail",
                          COMPLETION_LABELS[comp], time.time() - t4, value={"outcomes": s["outcomes"]}))
    # 6. 1 決定の再生 (--replay の対象のログだけ)
    if replay:
        ms.append(replay_one(root, name, recs, adv, plan, db_copy, snaps, cur_team, team_docs, cid))
    return case, ms


def replay_one(root: Path, name: str, recs: list, adv: Optional[dict], plan: dict, db_copy: Path, snaps: list,
               cur_team: dict, team_docs: dict, cid: str) -> dict:
    """1 決定の再生 (tools.inputs_pin.DataSwitch と tools.advice_replay.replay_state の経路)。RL 方策は読まない
    (CHAMPIONS_MODELS_DIR = 空の dir、RL_BLEND_WEIGHT = 0。モデルの実行をしない)。当時の推奨との一致は診断で、正解ではない"""
    t0 = time.time()
    if adv is None:
        return measurement(cid + ":replay", f"battle_log:{name}", CAT_GEN, {}, "1 決定の再生", "advice 行", "unexecuted",
                           "state のある advice 行なし", 0.0)
    from advisor import my_team as MT
    from advisor.engine import evaluate
    from tools.advice_replay import _best_key, replay_state
    from tools.inputs_pin import DataSwitch
    from vision.normalize import NameResolver
    ids = [x["id"] for x in snaps]
    local = {"db": str(db_copy), "snapshots": ids, "latest": max(ids), "banks": {}, "team_sha16": cur_team.get("sha16"),
             "team_registry_sha16": registry_sha16(team_docs.get("my_team.json"))}
    mode = "restore" if plan["ok"] else "current"
    ds = DataSwitch(mode, local=local)
    p = ds.plan(name, recs, adv.get("t"))
    # ワークツリーには config/my_team.json が無い: 当時の登録の記録が無いログは、今の登録 (本体の my_team.json) で読む
    if p.get("team_registry") is None:
        MT.set_registry_override(team_docs.get("my_team.json") or {})
    undo = patch_default_db(db_copy)
    try:
        st, info = replay_state(adv, NameResolver())
        out = evaluate(st, NameResolver())
        got = _best_key(out)
        logged = _best_key(adv.get("advice") or {})
        ok = bool(out.get("ok")) and got is not None
        return measurement(cid + ":replay", f"battle_log:{name}", CAT_GEN, {"t": adv.get("t"), "data_mode": mode},
                           "再生で助言が出る (一致は診断。当時の推奨は正解ではない)", "advice 行の advice.best (生成物)",
                           "pass" if ok else "fail",
                           (f"再生の第一候補 {got} / 当時 {logged} ({'一致' if got == logged else '不一致'})。RL 加点なし"
                            f" (当時の rl_blend は version 行の scoring)、データの版 {mode}"),
                           time.time() - t0, value={"replay_best": list(got) if got else None,
                                                    "logged_best": list(logged) if logged else None,
                                                    "match": got == logged, "now_source": info.get("now_source"),
                                                    "ev_infer_applied": info.get("ev_infer_applied"),
                                                    "plan_ok": p["ok"], "plan_reasons": p["reasons"], "notes": p["notes"]})
    except Exception as e:  # 記録して続ける (失敗も残す)
        return measurement(cid + ":replay", f"battle_log:{name}", CAT_GEN, {"t": adv.get("t"), "data_mode": mode},
                           "再生で助言が出る", "advice 行", "fail", f"例外: {e!r}", time.time() - t0)
    finally:
        undo()
        ds.close()
        MT.clear_registry_override()


def model_presence(sha16: Optional[str], all_models: list) -> list:
    """sha16 のモデルファイルの置き場 (純粋)"""
    if not sha16:
        return []
    return sorted({m["rel"] for m in all_models if m["sha16"] == sha16})


def case_run(root: Path, run_asset: dict, all_models: list) -> tuple:
    t0 = time.time()
    name = run_asset["asset_id"].split(":", 1)[1]
    rd = root / run_asset["path"]
    cid = f"run_{name}"
    case = {"test_id": TEST_ID, "case_id": cid, "asset": run_asset["asset_id"], "split": "diag",
            "input": {"run": name}, "public_boundary": "run の manifest・final と run.log (封印 holdout の詳細は読まない)",
            "expected": {"当時の型": "final/team.json か s06_sets", "状態": "run の条件 (規制・相手プール・操縦)",
                         "使用モデル": "final/manifest の models_dir・selection_model_sha256 が手元にある", "結果": "s12_holdout"},
            "expected_source": "run の manifest (観測事実) と evaluation (合成結果)"}
    ms = []
    team = rd / "final" / "team.json"
    ms.append(measurement(cid + ":team", run_asset["asset_id"], CAT_GEN, {"path": "final/team.json"},
                          "最終候補の型が読める", "final/team.json", "pass" if team.exists() else "fail",
                          "final/team.json あり" if team.exists() else f"final なし ({run_asset['status']})", time.time() - t0))
    t1 = time.time()
    cond = run_asset["condition"]
    miss = [k for k in ("regulation", "pool_snapshot", "meta_snapshot", "pick_policy", "models_dir") if not cond.get(k)]
    ms.append(measurement(cid + ":condition", run_asset["asset_id"], CAT_FACT, cond, "規制・相手プール・操縦・版が揃う",
                          "manifest.json", "pass" if not miss else "fail",
                          "揃う" if not miss else "記録なし: " + ", ".join(miss), time.time() - t1,
                          value={"n_same_condition": run_asset["n_same_condition"]}))
    t2 = time.time()
    fm_path = rd / "final" / "manifest.json"
    if fm_path.exists():
        fm = json.loads(fm_path.read_text(encoding="utf-8"))
        md = Path(fm.get("models_dir") or "")
        try:
            md_local = root / Path(*md.parts[md.parts.index("logs"):])
        except ValueError:
            md_local = None
        sel16 = (fm.get("selection_model_sha256") or "")[:16]
        where = model_presence(sel16, all_models)
        ok = bool(md_local and md_local.exists()) and bool(where)
        ms.append(measurement(cid + ":model", run_asset["asset_id"], CAT_GEN,
                              {"models_dir": str(md_local.relative_to(root)) if md_local else None, "selection_sha16": sel16},
                              "操縦の方策 (models_dir) と選出モデルが手元にある", "final/manifest.json", "pass" if ok else "fail",
                              f"models_dir {'あり' if md_local and md_local.exists() else 'なし'}、選出モデルの sha の一致 {len(where)} 件",
                              time.time() - t2, value={"selection_found_at": where[:5]}))
    else:
        ms.append(measurement(cid + ":model", run_asset["asset_id"], CAT_GEN, {}, "操縦の方策と選出モデルが手元にある",
                              "final/manifest.json", "fail", "final なし (使用モデルの記録なし)", time.time() - t2))
    t3 = time.time()
    v = run_asset["valid_measure"]
    ms.append(measurement(cid + ":result", run_asset["asset_id"], CAT_SYNTH, {"status": run_asset["status"]},
                          "封印 holdout の verdict・区間・n", "final/evaluation.json (合成結果。旧 AI のシミュレーション)",
                          "pass" if v["valid"] else "fail",
                          (f"{v['verdict']} delta {v['delta']} n {v['n']}" if v["valid"] else "holdout の結果なし"),
                          time.time() - t3, value=v))
    return case, ms


def case_team_bak(root: Path, name: str, team_docs: dict, summaries: dict) -> tuple:
    """登録の bak 1 件: どの対戦ログの期間に当たるか (mtime の区間)、そのログの version 行の species が入っているか"""
    t0 = time.time()
    cid = f"team_{name}"
    cfg = root / "config"
    order = sorted(cfg.glob("my_team.json.bak.*")) + [cfg / "my_team.json"]
    names = [p.name for p in order]
    i = names.index(name)
    t_from, t_to = bak_interval(name, (cfg / name).stat().st_mtime)
    logs = [n for n, s in summaries.items() if s["t_first"] is not None and s["t_first"] >= t_from
            and (t_to is None or s["t_first"] < t_to)]
    # 自分の 6 体: 選出画面の読み (推定値) を優先し、無ければ version 行 (ファイルを開いた時点の 6 体)
    six = {}
    for n in logs:
        ids = summaries[n].get("roster_ids")
        if ids and all(ids):
            six[n] = ("roster", ids)
        elif summaries[n]["versions"]:
            six[n] = ("version", summaries[n]["versions"][-1]["team_species"])
    with_ver = list(six)
    from vision.normalize import NameResolver
    idf = _species_id_of(NameResolver())
    cover = collections.Counter()
    for n in with_ver:
        c = team_coverage(six[n][1], team_docs[name], idf)
        cover[f"{c['registered']}/{c['n']} 登録, 技あり {c['with_moves']} ({six[n][0]})"] += 1
    case = {"test_id": TEST_ID, "case_id": cid, "asset": f"my_team:{name}", "split": "diag",
            "input": {"file": name, "mtime_from": iso_local(t_from), "mtime_to": iso_local(t_to)},
            "public_boundary": "登録ファイルと対戦ログの version 行 (species) だけ",
            "expected": {"当時の型": "その期間の対戦ログの 6 体が全部登録 (技 4 つ) されている",
                         "状態": "-", "使用モデル": "-", "結果": "その期間の対戦ログの勝敗"},
            "expected_source": "my_team.json の bak (観測事実) と version 行 (観測事実)"}
    ok = bool(with_ver) and all(k.startswith("6/6") and "技あり 6 " in k for k in cover)
    ms = [measurement(cid + ":coverage", f"my_team:{name}", CAT_FACT, {"n_logs_in_period": len(logs), "n_with_six": len(with_ver)},
                      "期間内の対戦の 6 体が全部この登録で型まで分かる",
                      "bak の期間 (mtime 〜 名前の日付の終わり、推定) と選出画面の 6 体 (推定値) / version 行",
                      "pass" if ok else "fail",
                      ("version 行のある対戦なし (6 体が分からない)" if not with_ver else
                       "; ".join(f"{k}: {v} 本" for k, v in cover.items())) + "。期間は推定、型の sha の照合はできない (登録の記録が無い)",
                      time.time() - t0, value={"logs": logs[:20], "diff_to_next": team_diff(team_docs[name], team_docs[names[i + 1]])
                                               if i + 1 < len(names) else None})]
    return case, ms


def case_failure(root: Path, frames_groups: dict) -> tuple:
    """失敗記録 1 件 (newteam_1006 の最初の非 consistent): 元のログ・advice_id・前後の画像・正解の有無"""
    t0 = time.time()
    p = root / "logs" / "scenes" / "newteam_1006.jsonl"
    rows, _ = _read_jsonl(p)
    r = next((x for x in rows if x.get("category") != "consistent"), rows[0])
    src = r.get("source") or {}
    cid = f"failure_{r.get('scene_id')}"
    case = {"test_id": TEST_ID, "case_id": cid, "asset": "failures:newteam_1006.jsonl", "split": "diag",
            "input": {"scene_id": r.get("scene_id"), "category": r.get("category"), "source_file": src.get("file"),
                      "advice_id": src.get("advice_id"), "t": src.get("t")},
            "public_boundary": "抽出した局面の system_state (その時点の認識) だけ。truth は採点側",
            "expected": {"当時の型": "元のログの version 行", "状態": "元のログの advice_id の行", "使用モデル": "元のログの version 行",
                         "結果": "truth (未記入なら復元不能)"},
            "expected_source": "logs/scenes/newteam_1006.jsonl (生成物) と元の対戦ログ"}
    ms = []
    lp = root / "logs" / "battles" / str(src.get("file"))
    recs, _ = _read_jsonl(lp) if lp.exists() else ([], 0)
    hit = next((x for x in recs if x.get("type") == "advice" and x.get("advice_id") == src.get("advice_id")), None)
    ms.append(measurement(cid + ":source", "failures:newteam_1006.jsonl", CAT_GEN, {"file": src.get("file"), "advice_id": src.get("advice_id")},
                          "元のログの同じ advice 行に戻れる", "source 欄", "pass" if hit else "fail",
                          "元の advice 行あり" if hit else "元のログか advice 行が無い", time.time() - t0,
                          value={"has_state": bool(hit and hit.get("state")),
                                 "has_version_row": any(x.get("type") == "version" for x in recs)}))
    t1 = time.time()
    t = _num(src.get("t"))
    near = []
    if t is not None:
        for g, items in frames_groups.items():
            near += [i["rel"] for i in items if i["t"] is not None and abs(i["t"] - t) <= INH_INV_FRAME_SLACK_SEC]
    ms.append(measurement(cid + ":frames", "failures:newteam_1006.jsonl", CAT_FACT, {"t": t, "slack_sec": INH_INV_FRAME_SLACK_SEC},
                          "根拠画像が残っている", "debug_frames の時刻", "pass" if near else "fail",
                          f"前後 {INH_INV_FRAME_SLACK_SEC:.0f} 秒の画像 {len(near)} 枚", time.time() - t1, value={"frames": near[:10]}))
    t2 = time.time()
    tr = r.get("truth") or {}
    filled = [k for k, v in tr.items() if v not in (None, "", [], {})]
    ms.append(measurement(cid + ":truth", "failures:newteam_1006.jsonl", CAT_FACT, {"labels_status": r.get("labels_status")},
                          "正解 (状態・合法手・期限) が記入済み", "truth 欄", "pass" if filled else "fail",
                          "未記入 (unlabeled)" if not filled else "記入: " + ", ".join(filled), time.time() - t2))
    return case, ms


def model_readiness(role: str, sha16: Optional[str], found_at: list, companions_missing: Optional[list],
                    features: list, embedding: list, embedding_now: Optional[str], regulation: list) -> dict:
    """モデルを実行試験へ進める条件 (計画書 §5 I-00: 対応する特徴量・埋め込み・規制まで揃った場合だけ) の判定 (純粋)。
    features / embedding / regulation = 対戦ログの version 行などに記録された値の list (記録が無ければ空)。
    companions_missing = 選出モデルの付属 (埋め込み・meta) の不足 (RL 方策は None = 対象外)。戻り値 {"ready", "lacks"}"""
    lacks = []
    if not sha16 or not found_at:
        lacks.append("ファイルが手元に無い")
    if companions_missing:
        lacks.append("付属の不足: " + ", ".join(companions_missing))
    if not features:
        lacks.append("特徴量 (観測) の版の記録なし")
    if role == "selection":
        if not embedding:
            lacks.append("埋め込みの sha の記録なし")
        elif embedding_now and embedding_now not in embedding:
            lacks.append(f"記録した埋め込み {embedding} が今のファイル {embedding_now} と違う")
    if not regulation:
        lacks.append("規制の記録なし")
    return {"ready": not lacks, "lacks": lacks}


# ------------------------------------------------------------------ main
def pick_logs(summaries: dict) -> dict:
    """復元の確認に使う実戦ログ 3 本 (純粋): 版 1 のスキーマ (state のある advice が全部版 1) の最後 / 版 2 を含む最初 / 10 月の最新"""
    names = sorted(summaries)
    v1 = [n for n in names if summaries[n]["advice"]["schema"].get("1") and not summaries[n]["advice"]["schema"].get("2")]
    v2 = [n for n in names if summaries[n]["advice"]["schema"].get("2")]
    oct_ = [n for n in names if n.startswith("battle_202610")]
    return {"v1": v1[-1] if v1 else None, "v2": v2[0] if v2 else None, "latest": oct_[-1] if oct_ else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="資産を読む本体ツリー")
    ap.add_argument("--out", required=True, help="出力 dir (logs/inheritance_eval/<run_id>/I-00)")
    ap.add_argument("--scratch", required=True, help="DB の写し・空のモデル dir を置く作業 dir")
    ap.add_argument("--replay", action="store_true", help="10 月の最新ログの 1 決定を再生する (RL 方策は読まない)")
    a = ap.parse_args()
    root, out, scratch = Path(a.root).resolve(), Path(a.out), Path(a.scratch)
    out.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    if a.replay:
        empty = scratch / "no_models"
        empty.mkdir(exist_ok=True)
        os.environ["CHAMPIONS_MODELS_DIR"] = str(empty)
        os.environ["RL_BLEND_WEIGHT"] = "0"
    budget = Budget(INH_TIME_BUDGET_SEC, INH_INV_FULL_HASH_BUDGET_SEC)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    assets, ms, cases, stage_times = [], [], [], {}

    def stage(name, fn):
        t0 = time.time()
        if budget.left() <= 0:
            ms.append(measurement(f"scan_{name}", name, CAT_FACT, {}, "走査", "-", "timeout", "時間上限 (未確認)", 0.0))
            stage_times[name] = 0.0
            return None
        r = fn()
        stage_times[name] = round(time.time() - t0, 2)
        print(f"[{name}] {stage_times[name]} 秒", flush=True)
        return r

    db_assets, snaps, db_copy = stage("usage_db", lambda: scan_db(root, scratch, budget))
    log_assets, summaries = stage("battle_logs", lambda: scan_battle_logs(root, snaps, budget))
    fr_assets, fr_dup, fr_groups = stage("frames", lambda: scan_frames(root, summaries, budget))
    team_assets, team_docs = stage("my_team", lambda: scan_team(root))
    sm_assets = stage("smogon", lambda: scan_smogon(root))
    art_assets = stage("articles", lambda: scan_articles(root))
    run_assets = stage("build_runs", lambda: scan_runs(root, budget))
    mdl_assets, mdl_dup, all_models = stage("models", lambda: scan_models(root, budget))
    reg_assets = stage("registry", lambda: scan_registry(root))
    fail_assets = stage("failures", lambda: scan_failures(root))
    # version 行の 6 体と、その対戦の選出画面の自分の 6 体 (推定値) の照合
    from vision.normalize import NameResolver
    idf = _species_id_of(NameResolver())
    cur_reg = team_docs.get("my_team.json") or {}
    for x in log_assets:
        sm = summaries[x["asset_id"].split(":", 1)[1]]
        ids = [idf(ja, cur_reg.get(ja)) for ja in sm["my_roster_ja"]] if sm.get("my_roster_ja") else None
        vs = sm["versions"][-1]["team_species"] if sm["versions"] else None
        sm["roster_ids"] = ids
        sm["version_vs_roster"] = roster_vs_version(vs, ids)
        x["my_roster"] = {"ja": sm.get("my_roster_ja"), "ids": ids, "version_team_matches": sm["version_vs_roster"]}
        if sm["version_vs_roster"] is False:
            x["missing"].append("version 行の 6 体が選出画面の自分の 6 体と違う (ファイルを開いた時点の登録・前のロスター)")
    assets = db_assets + log_assets + fr_assets + team_assets + sm_assets + art_assets + run_assets + mdl_assets + reg_assets + fail_assets
    log_dup = dup_stats((x["sha16"], x["bytes"]) for x in log_assets)

    # 各資産の読み取りの測定 (全件)
    for x in assets:
        cat = (x.get("categories") or [CAT_GEN])[0]
        ms.append(measurement(f"inventory:{x['asset_id']}", x["asset_id"], cat, {"path": x["path"]},
                              "読み取れて分類できる", "ファイルの読み取り", "pass" if x.get("readable") else "fail",
                              "; ".join(x.get("missing") or []) or "欠損なし", 0.0))
    ms.append(measurement("inventory:article_bodies", "articles", CAT_FACT, {}, "-", "-", "excluded",
                          "記事の本文は読まない (指示書。件数と出典だけ)", 0.0))
    ms.append(measurement("inventory:model_execution", "models", CAT_GEN, {}, "-", "-", "excluded",
                          "モデルは実行しない (指示書。hash と付属ファイルの揃いだけ)", 0.0))

    # 復元の確認
    t0 = time.time()
    picks = pick_logs(summaries)
    latest = picks["latest"]
    snap_latest = None
    if latest and summaries[latest]["versions"]:
        snap_latest = summaries[latest]["versions"][-1].get("usage_snapshot")
    try:
        cur_team = current_team_sha(root, db_copy, max(s["id"] for s in snaps))
        cur_team_at = current_team_sha(root, db_copy, snap_latest) if snap_latest is not None else None
    except Exception as e:
        cur_team, cur_team_at = {"sha16": None, "error": repr(e)}, None
    ms.append(measurement("team_sha_current", "my_team:my_team.json", CAT_GEN, {"snapshot_latest": max(s["id"] for s in snaps),
                                                                               "snapshot_of_latest_log": snap_latest},
                          "今の登録の本文の sha (version 行の team.sha256 と同じ計算)", "advisor.versions._registered_team の規則",
                          "pass" if cur_team.get("sha16") else "fail",
                          f"最新 snapshot で {cur_team.get('sha16')}、最新ログの snapshot で {(cur_team_at or {}).get('sha16')}",
                          time.time() - t0, value={"latest": cur_team, "at_latest_log_snapshot": cur_team_at}))
    for label, name in picks.items():
        if not name:
            ms.append(measurement(f"log_{label}", "battle_log", CAT_EST, {}, "該当するログ", "-", "unexecuted", "該当なし", 0.0))
            continue
        use_team = cur_team_at if (label == "latest" and cur_team_at) else cur_team
        c, m = case_battle_log(root, name, label, summaries, db_copy, snaps, use_team, team_docs, all_models,
                               a.replay and label == "latest")
        cases.append(c)
        ms += m
    done_runs = [x for x in run_assets if x["status"].startswith("完了")]
    part_runs = [x for x in run_assets if not x["status"].startswith("完了") and x["status"].startswith("測定")]
    by_created = lambda x: x["period"]["created"] or ""
    for ra in ([max(done_runs, key=by_created)] if done_runs else []) + ([max(part_runs, key=by_created)] if part_runs else []):
        c, m = case_run(root, ra, all_models)
        cases.append(c)
        ms += m
    bak = "my_team.json.bak.20261005"
    if bak in team_docs:
        c, m = case_team_bak(root, bak, team_docs, summaries)
        cases.append(c)
        ms += m
    c, m = case_failure(root, fr_groups)
    cases.append(c)
    ms += m

    # モデルを実行試験へ進められるか (対戦ログで使われた選出モデル・行動方策)
    t0 = time.time()
    vers = [v for sm in summaries.values() for v in sm["versions"]]
    emb_now = None
    emb_path = root / "champions_agent" / "data" / "species_embedding.json"
    if emb_path.exists():
        emb_now = hashlib.sha256(emb_path.read_bytes()).hexdigest()[:16]
    for role, key in (("selection", "selection_sha16"), ("action", "action_sha16")):
        for sha in sorted({v[key] for v in vers if v.get(key)}):
            where = model_presence(sha, all_models)
            comp = None
            if role == "selection" and where:
                comp = selection_companions([x.name for x in (root / where[0]).parent.iterdir()])
            rows = [v for v in vers if v.get(key) == sha]
            feats = sorted({v["selection_features"] for v in rows if v.get("selection_features")}) if role == "selection" else []
            embs = sorted({v["embedding_sha16"] for v in rows if v.get("embedding_sha16")})
            regs = sorted({v["regulation"] for v in rows if v.get("regulation")})
            rd = model_readiness(role, sha, where, comp, feats, embs, emb_now, regs)
            ms.append(measurement(f"model_ready:{role}:{sha}", f"model:{sha}", CAT_GEN,
                                  {"sha16": sha, "n_version_rows": len(rows)}, "特徴量・埋め込み・規制が揃い実行試験へ進める",
                                  "計画書 §5 I-00 の判定条件", "pass" if rd["ready"] else "fail",
                                  "揃う" if rd["ready"] else "; ".join(rd["lacks"]), time.time() - t0,
                                  value={"found_at": where[:5], "n_found": len(where), "features": feats, "embedding": embs,
                                         "embedding_now": emb_now, "regulation": regs}))
    elapsed = budget.elapsed()
    # 用途ごとの使用可能件数・欠損理由 (実戦ログ)
    cls = collections.Counter(s["cls"] for s in summaries.values())
    comp = collections.Counter(s["completion"] for s in summaries.values())
    miss = collections.Counter(m for s in summaries.values() for m in s["missing"])
    uses = {"正解 (勝敗確定)": sum(1 for s in summaries.values() if s["uses"]["正解"]),
            "失敗再現 (advice の state あり)": sum(1 for s in summaries.values() if s["uses"]["失敗再現"] == "advice_state"),
            "失敗再現 (scene 行の近似のみ)": sum(1 for s in summaries.values() if s["uses"]["失敗再現"] == "scene_only"),
            "候補セット (相手 6 体の名前)": sum(1 for s in summaries.values() if s["uses"]["候補セット"]),
            "比較基準 (勝敗 + 版あり)": sum(1 for s in summaries.values() if s["uses"]["比較基準"] == "版あり"),
            "比較基準 (勝敗のみ・版不明)": sum(1 for s in summaries.values() if s["uses"]["比較基準"] == "版不明")}
    decisions = {"advice_with_state": sum(s["advice"]["with_state"] for s in summaries.values()),
                 "advice_with_state_v2": sum(s["advice"]["schema"].get("2", 0) for s in summaries.values()),
                 "advice_with_data_version": sum(s["advice"]["with_data_version"] for s in summaries.values()),
                 "advice_in_logs_with_usage_snapshot": sum(s["advice"]["with_state"] for s in summaries.values()
                                                          if any(v.get("usage_snapshot") is not None for v in s["versions"])),
                 "scene_decisions": sum(s["n_scene_decisions"] for s in summaries.values()),
                 "decision_rows": sum(s["n_decision_rows"] for s in summaries.values())}
    stats = {"battle_logs": {"n": len(summaries), "class": dict(cls), "completion": dict(comp), "missing": dict(miss),
                             "uses": uses, "decisions": decisions, "duplicates": log_dup,
                             "n_with_version": sum(1 for s in summaries.values() if s["versions"]),
                             "version_vs_roster": dict(collections.Counter(str(s.get("version_vs_roster")) for s in summaries.values())),
                             "n_bad_lines": sum(s["bad_lines"] for s in summaries.values())},
             "frames": {"duplicates": fr_dup}, "models": {"duplicates": mdl_dup},
             "build_runs": {"n": len(run_assets), "status": dict(collections.Counter(x["status"] for x in run_assets)),
                            "valid_measure": sum(1 for x in run_assets if x["valid_measure"]["valid"]),
                            "verdicts": {x["asset_id"].split(":", 1)[1]: x["valid_measure"]["verdict"] for x in run_assets
                                         if x["valid_measure"]["valid"]},
                            "with_regulation": sum(1 for x in run_assets if x["regulation"]["recorded"]),
                            "n_condition_groups": len({json.dumps({k: v for k, v in x["condition"].items() if k != "git_commit"},
                                                                  sort_keys=True) for x in run_assets})},
             "storage_bytes": {k: sum(x["bytes"] or 0 for x in v) for k, v in
                               (("usage_db", db_assets), ("battle_logs", log_assets), ("frames", fr_assets), ("my_team", team_assets),
                                ("smogon", sm_assets), ("articles", art_assets), ("build_runs", run_assets), ("models", mdl_assets),
                                ("registry", reg_assets), ("failures", fail_assets))}}
    (out / "assets.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in assets), encoding="utf-8")
    (out / "measurements.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in ms), encoding="utf-8")
    (out / "cases.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in cases), encoding="utf-8")
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    code_files = [Path(__file__), REPO / "champions_agent" / "config.py"]
    manifest = {"test_id": TEST_ID, "run_id": INH_RUN_ID, "code_commit": INH_CODE_COMMIT,
                "code_sha16": {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in code_files},
                "code_note": "道具 (tools/inheritance_eval/inventory.py) と config の INH_* はワークツリー feat/inh-inventory の未コミットの変更",
                "data_root": str(root), "started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "time_budget_sec": INH_TIME_BUDGET_SEC, "elapsed_sec": round(elapsed, 1), "stage_sec": stage_times,
                "full_hash_budget_sec": INH_INV_FULL_HASH_BUDGET_SEC, "hash_spent_sec": round(budget.hash_spent, 1),
                "seed": None, "conditions": {"frame_slack_sec": INH_INV_FRAME_SLACK_SEC, "replay": a.replay,
                                             "replay_env": {"CHAMPIONS_MODELS_DIR": "空の dir", "RL_BLEND_WEIGHT": "0"} if a.replay else None,
                                             "db_read": "mode=ro → sqlite backup の写し"},
                "data": {"usage_db_sha16": db_assets[0]["sha16"], "usage_db_latest_snapshot": max(s["id"] for s in snaps),
                         "battle_logs_n": len(log_assets),
                         "battle_logs_digest": hashlib.sha256("".join(sorted(x["sha16"] for x in log_assets)).encode()).hexdigest()[:16],
                         "my_team_sha16": {x["path"]: x["sha16"] for x in team_assets},
                         "smogon_sha16": {x["path"]: x["sha16"] for x in sm_assets}},
                "outputs": ["assets.jsonl", "measurements.jsonl", "cases.jsonl", "stats.json", "report.md (人が書く)"],
                "counts": {"assets": len(assets), "measurements": len(ms), "cases": len(cases),
                           "results": dict(collections.Counter(m["result"] for m in ms))}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], ensure_ascii=False), f"elapsed {elapsed:.1f}s")


if __name__ == "__main__":
    main()
