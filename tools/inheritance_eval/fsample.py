"""引き継ぎ評価 段階 A: 検証セット F (事実確認) と R (既知の失敗) の標本を切り出す (docs/INHERITANCE_EVALUATION_PLAN_20261031.md
§3.2・§4・§7 段階 A「F のラベル作成」の準備、2026-10-31)。正解欄は空のまま書き、人が labeling_guide.md に従って記入する。

    python -m tools.inheritance_eval.fsample --data-root <本体ツリー> [--out-dir <出力先>] [--seed S] [--write [--overwrite]]

既定は dry-run (件数だけ出す)。--write で出力先に manifest.json / cases.jsonl (F) / r_cases.jsonl (R) / measurements.jsonl /
report.md / labeling_guide.md を書く。原本 (対戦ログ・保存フレーム・logs/scenes) は読むだけ。

F (事実確認): 保存フレーム (debug_frames の frame_ / sel_ / fc_ の PNG と burst/<id>/<ミリ秒>.jpg の連続保存) が、対戦ログの
決定画面の窓 (scene 行が command / move_select、または selection の間。情報確認の画面 (watch / field_check / standby) は窓を閉じない。
decision_audit・scene_eval と同じ規則) に入っている決定を 1 行 = 1 決定で取る。助言の有無・成否では選ばない (助言は参考欄だけ)。
件数が目標 (config INH_FSAMPLE_TARGET_DECISIONS) を超えるときだけ対戦ごとに順に回して無作為に選ぶ。dev / confirm は対戦単位で
決定数が概ね半分になるように分ける (seed 固定)。R に入れた局面の窓は F から外す。
R (既知の失敗): logs/scenes/newteam_1006.jsonl の局面と、事故記録 (docs/incidents/reports/、docs/KNOWN_ISSUES.md の E-*) のうち
局面 (対戦ログと時刻) を特定できたものについて、根拠画像 (保存フレームのファイル) の有無と時刻の対応を出す。画像の内容は判定しない。

セッション = 対戦ログの時間の幅の間が INH_FSAMPLE_SESSION_GAP_SEC 以下で続く対戦 (接続テストの 1 回)。id は最初の対戦のファイル名の日時。
純粋な部分 (decision_windows / assign_sessions / pick_frames / build_f / split_by_battle / r_from_scene_set ほか) は
tests/test_inheritance_fsample.py。
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import random
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

from champions_agent.config import (
    INH_FSAMPLE_BURST_SUBDIR, INH_FSAMPLE_DATASET_KINDS, INH_FSAMPLE_LABEL_BUDGET_MIN, INH_FSAMPLE_LABEL_MIN_SELECTION,
    INH_FSAMPLE_LABEL_MIN_TURN, INH_FSAMPLE_MAX_FRAMES_PER_CASE, INH_FSAMPLE_NEAR_FRAME_TOLS_SEC, INH_FSAMPLE_R_FRAME_TOL_SEC,
    INH_FSAMPLE_REOPEN_MERGE_SEC, INH_FSAMPLE_SEED, INH_FSAMPLE_SESSION_GAP_SEC, INH_FSAMPLE_TARGET_BATTLES,
    INH_FSAMPLE_TARGET_DECISIONS, INH_FSAMPLE_TARGET_SESSIONS, INH_FSAMPLE_TIME_BUDGET_SEC, SCENE_SAMPLES_ANY_FRAME_PREFIXES)
from tools.real_accept import appearance_indices, pick_status
from tools.scene_samples import _system_advice, advice_at, frame_time
from vision.scenes import (
    SCENE_COMMAND, SCENE_FIELD_CHECK, SCENE_MOVE_SELECT, SCENE_SELECTION, SCENE_STANDBY, SCENE_WATCH)

REPO = Path(__file__).resolve().parents[2]
TEST_ID = "F-R"
RUN_ID = "phaseA_20261031"
CODE_COMMIT = "768bae44"
GUIDE_TEMPLATE = Path(__file__).resolve().parent / "fsample_labeling_guide.md"

FAMILY_TURN = "turn"            # command / move_select (行動の決定)
FAMILY_SELECTION = "selection"  # 選出画面 (選出の決定)
_FAMILY_OF = {SCENE_COMMAND: FAMILY_TURN, SCENE_MOVE_SELECT: FAMILY_TURN, SCENE_SELECTION: FAMILY_SELECTION}
INFO_SCENES = (SCENE_WATCH, SCENE_FIELD_CHECK, SCENE_STANDBY)
KIND_BURST = "burst"
_BATTLE_STEM_RE = re.compile(r"battle_(\d{8}_\d{6})")

# 判読不能の区分 (truth.unreadable[].kind)。本来隠れている情報 (truth.unknown_fields) とは別に数える
UNREADABLE_KINDS = {
    "off_screen": "対戦中に画面に出る情報だが、この画像の画面には出ていない (別画面・切り替えの前後)",
    "occluded": "演出・ウィンドウ・カーソル等で隠れている",
    "transition": "演出の途中 (HP の減少中・暗転・技の演出) で値が定まらない",
    "low_quality": "解像度・圧縮・ぶれで読めない",
    "ambiguous": "読めるが候補が複数残る (似た種・フォルム・桁の読み違いの余地)",
}
# measurements の category (共通規約)
CAT_FACT = "観測事実"
EXPECTED_SOURCE_F = "人のラベル (未記入。labeling_guide.md)"
PUBLIC_BOUNDARY = ("その時刻の画像 (と同じ決定の窓の他の画像) に出ている情報と、それ以前に画面に出た情報だけ。"
                   "後で判明した技・持ち物・選出・HP を過去の決定に戻さない。対戦ログの記録 (system_state) は当時のシステムの観測で正解ではない")

# 事故記録の局面の特定 (運用側が docs を読んで決めた対応。自動の照合は対戦ログ名・助言 id の有無だけ)。
#   kind advice_id: その助言の行の時刻 / offset: 対戦ログの最初の行からの秒の範囲にある最初の battle の助言の行
#   kind none: 局面ではない (理由つきで除外)
KNOWN_ISSUE_LOCATORS = {
    "E-accept-1": {"kind": "advice_id", "battle": "battle_20261020_085420.jsonl", "advice_id": "20261020_085420-0052",
                   "what": "選出が 1 体しか判明しない決定で、選出していないブリジュラスへの交代を第一候補に表示 (KNOWN_ISSUES の「2 戦目 0052」)"},
    "E-inputs-2": {"kind": "none", "reason": "版の記録 (version 行の team.sha256) の時点の問題で、決定の局面ではない"},
    "E-accept-2": {"kind": "none", "reason": "版の識別 (digest) の問題で、決定の局面ではない"},
    "E-inputs-1": {"kind": "none", "reason": "正式比較の入力の固定の問題で、実戦の局面ではない"},
    "E-inputs-3": {"kind": "none", "reason": "固定入力の異常の伝播 (レビューの再現) で、実戦の局面ではない"},
    "E-ev-1": {"kind": "none", "reason": "型推定のレビューの合成の再現で、実戦の局面 (対戦ログと時刻) が無い"},
    "E-ev-2": {"kind": "none", "reason": "型推定のレビューの合成の再現で、実戦の局面 (対戦ログと時刻) が無い"},
    "E-holdout-1": {"kind": "none", "reason": "シミュレータの holdout の局面で、実戦の対戦ログ・保存フレームが無い"},
}
INCIDENT_LOCATORS = {
    "2026-09-16-unregistered-party-misrecognition.md": {
        "kind": "offset", "battle": "battle_20260916_033459.jsonl", "from_sec": 362.0, "to_sec": 365.0,
        "what": "対戦の終了後に出た誤助言 (報告の「終了後の誤助言 1 戦 (362〜365 秒)」)"},
}


# ------------------------------------------------------------------ 純粋: フレーム
def parse_frame(rel: str) -> Optional[dict]:
    """debug_frames からの相対パス → {"name", "kind" (frame_ / sel_ / fc_ / burst), "t", "burst_id"}。形が違えば None。
    PNG は tools.scene_samples.frame_time (秒)、連続保存は burst/<id>/<ミリ秒>.jpg (server._dump_raw_async)"""
    parts = Path(rel).parts
    if len(parts) == 3 and parts[0] == INH_FSAMPLE_BURST_SUBDIR and parts[2].endswith(".jpg") and parts[2][:-4].isdigit():
        return {"name": rel, "kind": KIND_BURST, "t": int(parts[2][:-4]) / 1000.0, "burst_id": parts[1]}
    if len(parts) == 1:
        ft = frame_time(parts[0])
        if ft is not None and ft[0] in tuple(SCENE_SAMPLES_ANY_FRAME_PREFIXES):
            return {"name": rel, "kind": ft[0], "t": ft[1], "burst_id": None}
    return None


class FrameIndex:
    """時刻順の保存フレーム (bisect で区間を引く)"""

    def __init__(self, frames: Iterable[dict]):
        self.frames = sorted(frames, key=lambda f: (f["t"], f["name"]))
        self.ts = [f["t"] for f in self.frames]

    def between(self, t0: float, t1: float) -> list:
        return self.frames[bisect.bisect_left(self.ts, t0):bisect.bisect_right(self.ts, t1)]

    def nearest_gap(self, t0: float, t1: float) -> Optional[float]:
        """区間 [t0, t1] の外で最も近いフレームまでの秒 (区間内にあれば 0、フレームが無ければ None)"""
        if self.between(t0, t1):
            return 0.0
        i = bisect.bisect_left(self.ts, t0)
        gaps = []
        if i > 0:
            gaps.append(t0 - self.ts[i - 1])
        j = bisect.bisect_right(self.ts, t1)
        if j < len(self.ts):
            gaps.append(self.ts[j] - t1)
        return min(gaps) if gaps else None


def pick_frames(frames: list, k: int = INH_FSAMPLE_MAX_FRAMES_PER_CASE) -> tuple:
    """決定の窓の中のフレームから最大 k 枚 (PNG があれば PNG だけから、等間隔) と代表 1 枚 (選んだ中の真ん中) を選ぶ (純粋)。
    戻り値 (選んだフレームの列, 代表)。空なら ([], None)"""
    if not frames or k <= 0:
        return [], None
    png = [f for f in frames if f["kind"] != KIND_BURST]
    pool = png or list(frames)
    if len(pool) <= k:
        chosen = list(pool)
    elif k == 1:
        chosen = [pool[len(pool) // 2]]
    else:
        idx = sorted({round(i * (len(pool) - 1) / (k - 1)) for i in range(k)})
        chosen = [pool[i] for i in idx]
    return chosen, chosen[len(chosen) // 2]


# ------------------------------------------------------------------ 純粋: 対戦ログ
def battle_span(records: list) -> Optional[tuple]:
    ts = [float(d["t"]) for d in records if isinstance(d.get("t"), (int, float))]
    return (min(ts), max(ts)) if ts else None


def session_meta(records: list) -> Optional[dict]:
    for d in records:
        if d.get("type") == "session":
            return {k: d.get(k) for k in ("source", "dataset_kind", "data_quality", "package_id")}
    return None


def decision_windows(records: list, t_end: Optional[float] = None, merge_sec: float = INH_FSAMPLE_REOPEN_MERGE_SEC) -> list:
    """決定画面の窓 [{"family", "t_open", "t_close", "turn", "scenes", "n_reopen", "closed_by_end"}] (純粋)。
    scene 行を時刻順に見て、command / move_select (turn) と selection (selection) の連続を 1 つの窓にする。情報確認の画面は窓を閉じない。
    閉じた後、同じ系統・同じターンの窓が merge_sec 秒以内に開き直したら同じ決定にまとめる。ターン 0 (または不明) の selection は
    直前の窓も selection なら間隔によらずまとめる (1 対戦 1 回の選出)。最後の窓が閉じなければ t_end (対戦ログの最後) で閉じる"""
    rows = sorted((d for d in records if d.get("type") == "scene" and isinstance(d.get("t"), (int, float))),
                  key=lambda d: float(d["t"]))
    raw: list = []
    cur = None
    for d in rows:
        sc = d.get("scene")
        t = float(d["t"])
        fam = _FAMILY_OF.get(sc)
        if cur is not None:
            if fam == cur["family"]:
                if sc not in cur["scenes"]:
                    cur["scenes"].append(sc)
                if cur["intervals"][-1][1] is not None:        # 情報確認の画面から決定画面に戻った
                    cur["intervals"].append([t, None])
                continue
            if sc in INFO_SCENES:
                if cur["intervals"][-1][1] is None:
                    cur["intervals"][-1][1] = t
                continue
            _close(cur, t, False)
            cur = None
        if fam is not None:
            cur = {"family": fam, "t_open": t, "t_close": None, "turn": d.get("turn"), "scenes": [sc],
                   "n_reopen": 0, "closed_by_end": False, "intervals": [[t, None]]}
            raw.append(cur)
    if cur is not None:
        _close(cur, float(t_end) if t_end is not None else cur["t_open"], True)
    merged: list = []
    for w in raw:
        w = dict(w, spans=[[w["t_open"], w["t_close"]]])
        prev = merged[-1] if merged else None
        if prev is not None and prev["family"] == w["family"] and prev["turn"] == w["turn"]:
            sel0 = w["family"] == FAMILY_SELECTION and w["turn"] in (0, None)
            if sel0 or w["t_open"] - prev["t_close"] <= merge_sec:
                prev["t_close"] = w["t_close"]
                prev["closed_by_end"] = w["closed_by_end"]
                prev["n_reopen"] += 1 + w["n_reopen"]
                prev["scenes"] += [s for s in w["scenes"] if s not in prev["scenes"]]
                prev["intervals"] += w["intervals"]
                prev["spans"] += w["spans"]
                continue
        merged.append(w)
    return merged


def _close(w: dict, t: float, by_end: bool) -> None:
    w["t_close"] = t
    w["closed_by_end"] = by_end
    if w["intervals"][-1][1] is None:
        w["intervals"][-1][1] = t


def frames_of_window(fidx: "FrameIndex", w: dict) -> tuple:
    """決定の窓のフレーム (純粋)。決定画面の区間 (intervals) のフレームを優先し、無ければ窓 (spans。開き直しの間は含めない) の中の
    情報確認の画面のフレーム。戻り値 (フレームの列, 根拠 "decision" | "info_only" | None)"""
    def union(ivs):
        seen, out = set(), []
        for a, b in ivs:
            for f in fidx.between(a, b):
                if f["name"] not in seen:
                    seen.add(f["name"])
                    out.append(f)
        return sorted(out, key=lambda f: (f["t"], f["name"]))
    dec = union(w.get("intervals") or [[w["t_open"], w["t_close"]]])
    if dec:
        return dec, "decision"
    info = union(w.get("spans") or [[w["t_open"], w["t_close"]]])
    return (info, "info_only") if info else ([], None)


def assign_sessions(spans: dict, gap: float = INH_FSAMPLE_SESSION_GAP_SEC) -> dict:
    """{対戦ログ名: (開始, 終了)} → {対戦ログ名: セッション id} (純粋)。開始順に並べ、前の対戦の終了から gap 秒を超えて始まったら
    新しいセッション。id は "S" + 最初の対戦のファイル名の日時 (battle_YYYYMMDD_HHMMSS → SYYYYMMDD_HHMM)"""
    out = {}
    sid, prev_end = None, None
    for name, (t0, t1) in sorted(spans.items(), key=lambda kv: (kv[1][0], kv[0])):
        if sid is None or prev_end is None or t0 - prev_end > gap:
            m = _BATTLE_STEM_RE.search(name)
            sid = "S" + (m.group(1)[:13] if m else name)
            prev_end = t1
        else:
            prev_end = max(prev_end, t1)
        out[name] = sid
    return out


def observed_record_at(records: list, t: float) -> Optional[dict]:
    """時刻 t 以前で最後の state を持つ scene / advice の行 (当時のシステムの観測)。無ければ None"""
    best = None
    for d in records:
        if d.get("type") not in ("scene", "advice") or not d.get("state") or not isinstance(d.get("t"), (int, float)):
            continue
        if float(d["t"]) <= t and (best is None or float(d["t"]) >= float(best["t"])):
            best = d
    return best


def _move_id(mv):
    return mv[0] if isinstance(mv, (list, tuple)) and mv else mv


def summarize_state(compact: Optional[dict], appeared: Iterable = ()) -> Optional[dict]:
    """当時のシステムの観測 (battle_logger._compact_state) の要約 (純粋): 場の個体・HP・技・持ち物・選出の 3 値 (tools.real_accept.pick_status:
    選出済み / 非選出確定 / 不明。相手側も同じ規則をシステムの記録に当てたもの)・天候・壁。正解ではない"""
    if not compact:
        return None
    fld = compact.get("field") or {}
    out = {"scene": compact.get("scene"), "selection_picked": compact.get("selection_picked"),
           "weather": fld.get("weather"), "terrain": fld.get("terrain"), "trick_room": fld.get("trick_room")}
    for side in ("player", "opponent"):
        s = compact.get(side) or {}
        party = s.get("party") or []
        ps = pick_status({"player": s}, appeared if side == "player" else ())
        mons = []
        for i, m in enumerate(party):
            mons.append({"slot": i, "species": m.get("species"), "ja": m.get("ja"), "hp": m.get("hp"), "hp_raw": m.get("hp_raw"),
                         "status": m.get("status"), "moves": [_move_id(mv) for mv in (m.get("moves") or [])],
                         "revealed": list(m.get("revealed") or []), "item": m.get("item"), "ability": m.get("ability"),
                         "mega": m.get("mega"), "guess": bool(m.get("guess") or m.get("pending")), "pick": ps["status"][i]})
        out[side] = {"active": s.get("active"), "remaining": s.get("remaining"),
                     "screens": sorted(k for k, v in (s.get("screens") or {}).items() if v),
                     "picks_confirmed": ps["n_confirmed"], "party": mons}
    return out


def truth_template(family: str) -> dict:
    """正解欄の雛形 (全部未記入)。書式は labeling_guide.md"""
    side = {"active": None, "party": None}
    return {"scene": None,
            "state": {"player": dict(side), "opponent": dict(side), "weather": None, "terrain": None, "trick_room": None,
                      "screens": {"player": None, "opponent": None}},
            "legal_actions": None, "unknown_fields": None, "unreadable": None, "notes": ""}


# ------------------------------------------------------------------ 純粋: F の組み立て
def candidate_windows(battles: list, fidx: FrameIndex, r_marks: Iterable = (), k: int = INH_FSAMPLE_MAX_FRAMES_PER_CASE,
                      merge_sec: float = INH_FSAMPLE_REOPEN_MERGE_SEC, dataset_kinds=INH_FSAMPLE_DATASET_KINDS,
                      near_tols=INH_FSAMPLE_NEAR_FRAME_TOLS_SEC) -> dict:
    """battles: [(ログ名, records)] → {"pool": [候補], "excluded": [除外 (理由つき)]} (純粋)。
    候補 = 決定の窓に保存フレームが 1 枚以上ある決定。除外: 実戦以外のログ (dataset_kind) / R の局面 (r_marks: (ログ名, 時刻)) /
    窓に画像が無い (窓の外で最も近いフレームまでの秒 near_gap を残す)"""
    marks = defaultdict(list)
    for name, t in r_marks:
        marks[name].append(float(t))
    pool, excluded = [], []
    for name, recs in battles:
        span = battle_span(recs)
        if span is None:
            continue
        meta = session_meta(recs)
        bad_kind = meta is not None and meta.get("dataset_kind") not in tuple(dataset_kinds)
        for w in decision_windows(recs, span[1], merge_sec):
            base = {"battle": name, "family": w["family"], "turn": w["turn"], "t_open": round(w["t_open"], 3),
                    "t_close": round(w["t_close"], 3), "scenes_logged": w["scenes"], "n_reopen": w["n_reopen"],
                    "closed_by_end": w["closed_by_end"]}
            if bad_kind:
                excluded.append(dict(base, reason="dataset_kind", detail=meta.get("dataset_kind")))
                continue
            if w["family"] == FAMILY_SELECTION and w["turn"] not in (0, None):
                excluded.append(dict(base, reason="selection_mid_battle",
                                     detail="ターン 1 以降の選出画面の判定 (誤認識か、対戦ログの終わりに入った次の対戦の選出画面)"))
                continue
            if any(w["t_open"] - merge_sec <= t <= w["t_close"] for t in marks.get(name, ())):
                excluded.append(dict(base, reason="r_overlap", detail="R (既知の失敗) の局面と同じ決定"))
                continue
            inwin, basis = frames_of_window(fidx, w)
            if not inwin:
                gap = fidx.nearest_gap(w["t_open"], w["t_close"])
                excluded.append(dict(base, reason="no_frame", detail="決定の窓に保存フレームが無い",
                                     near_gap=round(gap, 2) if gap is not None else None))
                continue
            chosen, primary = pick_frames(inwin, k)
            pool.append(dict(base, frames=[f["name"] for f in chosen], frame_primary=primary["name"], t_ref=primary["t"],
                             n_frames_in_window=len(inwin), frame_kinds=dict(Counter(f["kind"] for f in inwin)),
                             frame_basis=basis, records=recs))
    return {"pool": pool, "excluded": excluded}


def sample_round_robin(pool: list, target: int, seed: int = INH_FSAMPLE_SEED) -> list:
    """候補が target を超えるときだけ、対戦ごとに 1 件ずつ順に回して無作為に選ぶ (純粋。1 対戦への偏りを避ける)。以下なら全部"""
    if len(pool) <= target:
        return list(pool)
    rng = random.Random(seed)
    by = defaultdict(list)
    for c in pool:
        by[c["battle"]].append(c)
    names = sorted(by)
    rng.shuffle(names)
    for n in names:
        rng.shuffle(by[n])
    out = []
    while len(out) < target:
        for n in names:
            if by[n] and len(out) < target:
                out.append(by[n].pop())
    return sorted(out, key=lambda c: (c["battle"], c["t_open"]))


def split_by_battle(counts: dict, seed: int = INH_FSAMPLE_SEED) -> dict:
    """{対戦ログ名: 決定数} → {対戦ログ名: "dev" | "confirm"} (純粋)。seed で並べ替えた後、決定数の多い順に、その時点で決定数の
    少ない側 (同数なら対戦数の少ない側、それも同じなら dev) へ入れる。対戦単位で決定数が概ね半分になる"""
    rng = random.Random(seed)
    names = sorted(counts)
    rng.shuffle(names)
    names.sort(key=lambda n: -counts[n])       # 安定な並べ替え: 同数の中は seed の順
    tot = {"dev": 0, "confirm": 0}
    nb = {"dev": 0, "confirm": 0}
    out = {}
    for n in names:
        s = min(("dev", "confirm"), key=lambda k: (tot[k], nb[k], k != "dev"))
        out[n] = s
        tot[s] += counts[n]
        nb[s] += 1
    return out


def build_f(battles: list, fidx: FrameIndex, sessions: dict, r_marks: Iterable = (), dev_refs: Optional[dict] = None,
            seed: int = INH_FSAMPLE_SEED, target: int = INH_FSAMPLE_TARGET_DECISIONS, k: int = INH_FSAMPLE_MAX_FRAMES_PER_CASE,
            merge_sec: float = INH_FSAMPLE_REOPEN_MERGE_SEC) -> dict:
    """F の行と除外・件数を作る (純粋)。sessions: {ログ名: セッション id}、dev_refs: {ログ名: [その対戦に触れた docs]}。
    戻り値 {"cases": [行], "excluded": [除外], "not_sampled": [候補だが目標超過で選ばなかった], "split": {ログ名: 区分}}"""
    cw = candidate_windows(battles, fidx, r_marks, k, merge_sec)
    chosen = sample_round_robin(cw["pool"], target, seed)
    chosen_ids = {id(c) for c in chosen}
    not_sampled = [{k2: v for k2, v in c.items() if k2 != "records"} for c in cw["pool"] if id(c) not in chosen_ids]
    split = split_by_battle(dict(Counter(c["battle"] for c in chosen)), seed)
    refs = dev_refs or {}
    cases = []
    for i, c in enumerate(chosen, 1):
        recs = c["records"]
        t = c["t_ref"]
        obs = observed_record_at(recs, t)
        compact = (obs or {}).get("state")
        party = ((compact or {}).get("player") or {}).get("party") or []
        appeared = appearance_indices(recs, party, t) if party else set()
        adv = advice_at(recs, t)
        sc = None
        for d in recs:
            if d.get("type") == "scene" and isinstance(d.get("t"), (int, float)) and float(d["t"]) <= t:
                if sc is None or float(d["t"]) >= float(sc["t"]):
                    sc = d
        cid = f"F{i:03d}"
        cases.append({
            "case_id": cid, "scene_id": cid, "set": "F", "family": c["family"], "split": split[c["battle"]],
            "session": sessions.get(c["battle"]), "battle": c["battle"], "turn": c["turn"], "t": round(t, 3),
            "t_open": c["t_open"], "t_close": c["t_close"], "scene_logged": (sc or {}).get("scene"),
            "scenes_logged_in_window": c["scenes_logged"], "frames": c["frames"], "frame_primary": c["frame_primary"],
            "n_frames_in_window": c["n_frames_in_window"], "frame_kinds_in_window": c["frame_kinds"],
            "frame_basis": c["frame_basis"],
            "extraction": "frame_scene (決定画面の窓に入る保存フレーム。助言の有無・成否に依存しない)",
            "system_state": summarize_state(compact, appeared),
            "system_state_source": ({"type": obs.get("type"), "t": obs.get("t"), "age_sec": round(t - float(obs["t"]), 2)}
                                    if obs else None),
            "advice_present": adv is not None, "system_advice": _system_advice(adv) if adv else None,
            "advice_id": (adv or {}).get("advice_id"),
            "dev_refs": refs.get(c["battle"], []), "past_data_diagnostic": bool(refs.get(c["battle"])),
            "public_boundary": PUBLIC_BOUNDARY, "expected_source": EXPECTED_SOURCE_F,
            "truth": truth_template(c["family"]), "labels_status": "unlabeled", "labeler": None, "labeled_at": None,
        })
    excluded = cw["excluded"]
    return {"cases": cases, "excluded": excluded, "not_sampled": not_sampled, "split": split,
            "n_pool": len(cw["pool"])}


def f_summary(f: dict, sessions: dict, targets: Optional[dict] = None) -> dict:
    """F の構成と不足 (純粋)"""
    tg = targets or {"decisions": INH_FSAMPLE_TARGET_DECISIONS, "battles": INH_FSAMPLE_TARGET_BATTLES,
                     "sessions": INH_FSAMPLE_TARGET_SESSIONS}
    cases = f["cases"]
    n_dec = len(cases)
    battles = sorted({c["battle"] for c in cases})
    sess = sorted({c["session"] for c in cases if c["session"]})
    ex = Counter(e["reason"] for e in f["excluded"])
    return {
        "decisions": n_dec, "battles": len(battles), "sessions": len(sess),
        "shortfall": {"decisions": max(0, tg["decisions"] - n_dec), "battles": max(0, tg["battles"] - len(battles)),
                      "sessions": max(0, tg["sessions"] - len(sess))},
        "targets": tg,
        "by_split": {s: {"decisions": sum(1 for c in cases if c["split"] == s),
                         "battles": len({c["battle"] for c in cases if c["split"] == s})} for s in ("dev", "confirm")},
        "by_family": dict(Counter(c["family"] for c in cases)),
        "by_session": {s: {"decisions": sum(1 for c in cases if c["session"] == s),
                           "battles": sorted({c["battle"] for c in cases if c["session"] == s})} for s in sess},
        "by_battle": dict(Counter(c["battle"] for c in cases)),
        "advice_present": sum(1 for c in cases if c["advice_present"]),
        "past_data_diagnostic": sum(1 for c in cases if c["past_data_diagnostic"]),
        "excluded": dict(ex), "not_sampled": len(f["not_sampled"]), "pool": f["n_pool"],
    }


def near_gap_hist(excluded: list, tols=INH_FSAMPLE_NEAR_FRAME_TOLS_SEC) -> dict:
    """画像の無い決定のうち、窓の外 tol 秒以内に保存フレームがある数 (診断。F には入れない)"""
    gaps = [e.get("near_gap") for e in excluded if e["reason"] == "no_frame"]
    out = {f"<={t:g}s": sum(1 for g in gaps if g is not None and g <= t) for t in tols}
    out["no_frame_total"] = len(gaps)
    out["no_frame_at_all"] = sum(1 for g in gaps if g is None)
    return out


# ------------------------------------------------------------------ 純粋: R
def _frames_near(fidx: FrameIndex, t: float, tol: float, span: Optional[tuple]) -> list:
    fs = fidx.between(t - tol, t + tol)
    if span is not None:
        fs = [f for f in fs if span[0] <= f["t"] <= span[1]]
    return fs


def r_row(r_id: str, origin: str, ref: str, battle: Optional[str], t: Optional[float], records: Optional[list],
          fidx: FrameIndex, tol: float = INH_FSAMPLE_R_FRAME_TOL_SEC, extra: Optional[dict] = None) -> dict:
    """R の 1 局面の根拠画像の有無 (純粋)。画像あり = 記録の時刻 ±tol 秒以内・対戦ログの時間の幅の中に保存フレームのファイルがある。
    画像の内容 (その局面が写っているか) は判定しない"""
    row = {"r_id": r_id, "set": "R", "origin": origin, "ref": ref, "battle": battle, "t": t,
           "log_exists": records is not None and len(records) > 0}
    span = battle_span(records) if records else None
    if t is None:
        row.update(frames=[], frame_exists=False, time_matches=False, evidence="not_located", included_in_r=False,
                   nearest_frame_sec=None)
    else:
        near = _frames_near(fidx, float(t), tol, span)
        gap = fidx.nearest_gap(float(t), float(t))
        in_win = None
        if records:
            for w in decision_windows(records, span[1] if span else None):
                if w["t_open"] - INH_FSAMPLE_REOPEN_MERGE_SEC <= float(t) <= w["t_close"]:
                    in_win = {"family": w["family"], "t_open": w["t_open"], "t_close": w["t_close"], "turn": w["turn"],
                              "frames_in_window": [f["name"] for f in frames_of_window(fidx, w)[0]][:INH_FSAMPLE_MAX_FRAMES_PER_CASE]}
                    break
        row.update(frames=[f["name"] for f in near], frame_exists=bool(near), time_matches=bool(near),
                   nearest_frame_sec=round(gap, 2) if gap is not None else None, decision_window=in_win,
                   evidence="image_available" if near else ("no_log" if not row["log_exists"] else "no_image"),
                   included_in_r=bool(near))
    if extra:
        row.update(extra)
    row.update(truth=truth_template(FAMILY_TURN), labels_status="unlabeled", use="修理・回帰専用 (F の発生率・確認成績に混ぜない)")
    return row


def r_from_scene_set(scene_rows: list, set_name: str, logs: dict, fidx: FrameIndex,
                     tol: float = INH_FSAMPLE_R_FRAME_TOL_SEC) -> list:
    """logs/scenes/<set>.jsonl の行 → R の行 (純粋)。logs: {ログ名: records}"""
    out = []
    for s in scene_rows:
        src = s.get("source") or {}
        b = src.get("file")
        out.append(r_row(f"R-{set_name}-{s.get('scene_id')}", f"scene_set:{set_name}", str(s.get("scene_id")), b,
                         src.get("t"), logs.get(b), fidx, tol,
                         {"category": s.get("category"), "turn": src.get("turn"), "advice_id": src.get("advice_id"),
                          "existing_labels_status": s.get("labels_status")}))
    return out


def locate(locator: dict, records: Optional[list]) -> Optional[float]:
    """事故記録の局面の時刻 (純粋)。advice_id: その助言の行の時刻 / offset: 対戦ログの最初の行から [from, to] 秒の最初の battle の助言"""
    if not records:
        return None
    if locator["kind"] == "advice_id":
        for d in records:
            if d.get("type") == "advice" and d.get("advice_id") == locator["advice_id"] and d.get("t") is not None:
                return float(d["t"])
        return None
    if locator["kind"] == "offset":
        span = battle_span(records)
        if span is None:
            return None
        for d in sorted((d for d in records if d.get("type") == "advice" and d.get("kind") == "battle"
                         and isinstance(d.get("t"), (int, float))), key=lambda d: float(d["t"])):
            if locator["from_sec"] <= float(d["t"]) - span[0] <= locator["to_sec"]:
                return float(d["t"])
    return None


def known_issue_ids(text: str) -> list:
    """KNOWN_ISSUES.md の表の E-* の id (出現順、重複なし)"""
    out = []
    for m in re.finditer(r"^\| (E-[a-z0-9]+-\d+)", text, re.M):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def battle_stems(text: str) -> list:
    return sorted(set(_BATTLE_STEM_RE.findall(text)))


# ------------------------------------------------------------------ 配線
def _load(path: Path) -> list:
    out = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def _span_stream(path: Path) -> Optional[tuple]:
    lo = hi = None
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                t = json.loads(line).get("t")
            except (ValueError, AttributeError):
                continue
            if isinstance(t, (int, float)):
                lo = t if lo is None or t < lo else lo
                hi = t if hi is None or t > hi else hi
    return (float(lo), float(hi)) if lo is not None else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_frames(fdir: Path) -> tuple:
    frames, ignored = [], []
    for p in sorted(fdir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(fdir).as_posix()
        fr = parse_frame(rel)
        (frames if fr else ignored).append(fr or rel)
    return frames, ignored


def _dev_refs(docs_dir: Path, stems: Iterable[str]) -> dict:
    texts = {}
    for p in sorted(docs_dir.rglob("*.md")):
        try:
            texts[p.relative_to(docs_dir.parent).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    out = {}
    for stem in stems:
        key = stem.replace("battle_", "").replace(".jsonl", "")
        out[stem] = sorted(n for n, tx in texts.items() if key in tx)
    return out


def _local(t: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t is not None else None


def _git_state(root: Path) -> dict:
    def run(*a):
        try:
            return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {"head": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": (run("status", "--porcelain") or "").splitlines()}


def _measure(case_id, input_, split, result, reason, expected=None, expected_source=EXPECTED_SOURCE_F, asset="debug_frames + logs/battles",
             category=CAT_FACT, elapsed=None) -> dict:
    return {"test_id": TEST_ID, "case_id": case_id, "asset": asset, "category": category, "input": input_, "expected": expected,
            "expected_source": expected_source, "split": split, "result": result, "reason": reason, "elapsed_sec": elapsed}


def run(data_root: Path, seed: int, budget_sec: float = INH_FSAMPLE_TIME_BUDGET_SEC) -> dict:
    t_start = time.time()
    bdir, fdir = data_root / "logs" / "battles", data_root / "debug_frames"
    scene_set = data_root / "logs" / "scenes" / "newteam_1006.jsonl"
    docs = data_root / "docs"
    frames, ignored = _scan_frames(fdir) if fdir.exists() else ([], [])
    fidx = FrameIndex(frames)
    files = sorted(bdir.glob("battle_*.jsonl"))
    spans, unexecuted = {}, []
    for p in files:
        if time.time() - t_start > budget_sec:
            unexecuted.append(p.name)
            continue
        sp = _span_stream(p)
        if sp is not None:
            spans[p.name] = sp
    sessions = assign_sessions(spans)
    with_frames = sorted(n for n, (a, b) in spans.items() if fidx.between(a, b))
    # R: 局面の集合と事故記録
    scene_rows = [json.loads(l) for l in scene_set.read_text(encoding="utf-8").splitlines() if l.strip()] if scene_set.exists() else []
    ki_text = (docs / "KNOWN_ISSUES.md").read_text(encoding="utf-8") if (docs / "KNOWN_ISSUES.md").exists() else ""
    inc_dir = docs / "incidents" / "reports"
    incidents = sorted(inc_dir.glob("*.md")) if inc_dir.exists() else []
    need = set(with_frames) | {(s.get("source") or {}).get("file") for s in scene_rows}
    need |= {v["battle"] for v in list(KNOWN_ISSUE_LOCATORS.values()) + list(INCIDENT_LOCATORS.values()) if v.get("battle")}
    logs = {n: _load(bdir / n) for n in sorted(x for x in need if x and (bdir / x).exists())}
    r_rows = r_from_scene_set(scene_rows, scene_set.stem, logs, fidx)
    for eid in known_issue_ids(ki_text):
        loc = KNOWN_ISSUE_LOCATORS.get(eid)
        if loc is None:
            row = r_row(f"R-ki-{eid}", "known_issue", eid, None, None, None, fidx)
            row.update(locate_reason="自動では局面を特定できない (運用側の対応表に無い)")
        elif loc["kind"] == "none":
            row = r_row(f"R-ki-{eid}", "known_issue", eid, None, None, None, fidx)
            row.update(locate_reason=loc["reason"])
        else:
            t = locate(loc, logs.get(loc["battle"]))
            row = r_row(f"R-ki-{eid}", "known_issue", eid, loc["battle"], t, logs.get(loc["battle"]), fidx,
                        extra={"what": loc.get("what"), "locator": {k: v for k, v in loc.items() if k != "what"}})
        r_rows.append(row)
    for p in incidents:
        loc = INCIDENT_LOCATORS.get(p.name)
        if loc is None:
            stems = battle_stems(p.read_text(encoding="utf-8", errors="replace"))
            row = r_row(f"R-inc-{p.stem}", "incident", p.name, None, None, None, fidx)
            row.update(locate_reason=("対戦ログ名はあるが局面 (時刻・助言) の記述が無い: " + ", ".join(stems)) if stems
                       else "対戦ログ名の記述が無い (局面ではない事故)")
        else:
            t = locate(loc, logs.get(loc["battle"]))
            row = r_row(f"R-inc-{p.stem}", "incident", p.name, loc["battle"], t, logs.get(loc["battle"]), fidx,
                        extra={"what": loc.get("what"), "locator": {k: v for k, v in loc.items() if k != "what"}})
        r_rows.append(row)
    # R の局面 (画像の有無によらず) と同じ決定は F に入れない
    r_marks = [(r["battle"], r["t"]) for r in r_rows if r.get("battle") and r.get("t") is not None]
    # F
    battles = [(n, logs[n]) for n in with_frames if n in logs]
    dev_refs = _dev_refs(docs, with_frames) if docs.exists() else {}
    f = build_f(battles, fidx, sessions, r_marks, dev_refs, seed)
    # 対戦ログの外のフレーム
    covered = set()
    for n, (a, b) in spans.items():
        for fr in fidx.between(a, b):
            covered.add(fr["name"])
    outside = [fr for fr in frames if fr["name"] not in covered]
    groups = defaultdict(list)
    for fr in outside:
        groups[fr["burst_id"] or f"png:{time.strftime('%Y-%m-%d', time.localtime(fr['t']))}"].append(fr["t"])
    outside_groups = {g: {"n": len(ts), "from": _local(min(ts)), "to": _local(max(ts))} for g, ts in sorted(groups.items())}
    return {"frames": frames, "ignored_files": ignored, "spans": spans, "sessions": sessions, "with_frames": with_frames,
            "logs": logs, "scene_set": scene_set, "scene_rows": scene_rows, "incidents": incidents, "ki_text": ki_text,
            "r_rows": r_rows, "f": f, "unexecuted_logs": unexecuted, "outside_groups": outside_groups,
            "elapsed_sec": round(time.time() - t_start, 2), "data_root": data_root, "seed": seed}


def measurements(res: dict) -> list:
    f = res["f"]
    out = []
    for c in f["cases"]:
        out.append(_measure(c["case_id"], {"battle": c["battle"], "t": c["t"], "family": c["family"], "frames": c["frames"]},
                            c["split"], "unexecuted", "抽出済み・正解ラベル未記入 (人の作業待ち)"
                            + (" / 過去データ診断 (開発で使った対戦)" if c["past_data_diagnostic"] else "")))
    reasons = {"no_frame": "画像の無い決定 (決定の窓に保存フレームが無い)", "r_overlap": "R (既知の失敗) の局面のため F から除外",
               "dataset_kind": "実戦以外のログ (session 行の dataset_kind)",
               "selection_mid_battle": "ターン 1 以降の選出画面の判定 (誤認識か次の対戦の選出画面。選出は対戦の最初の 1 回)"}
    for i, e in enumerate(f["excluded"], 1):
        out.append(_measure(f"F-x{i:03d}", {k: v for k, v in e.items() if k != "reason"}, "diag", "excluded",
                            reasons.get(e["reason"], e["reason"])))
    for i, e in enumerate(f["not_sampled"], 1):
        out.append(_measure(f"F-ns{i:03d}", {k: e[k] for k in ("battle", "t_open", "family")}, "diag", "excluded",
                            "候補だが目標件数を超えたため選ばなかった"))
    for n in res["unexecuted_logs"]:
        out.append(_measure(f"F-u-{n}", {"battle": n}, "diag", "unexecuted", "計算時間の上限で走査しなかった"))
    for g, v in res["outside_groups"].items():
        out.append(_measure(f"F-o-{g}", v, "diag", "excluded", "保存フレームはあるが対応する対戦ログが無い (時間の幅の外)"))
    s = f_summary(f, res["sessions"])
    for key in ("decisions", "battles", "sessions"):
        short = s["shortfall"][key]
        out.append(_measure(f"F-target-{key}", {"target": s["targets"][key], "got": s[key]}, "diag",
                            "pass" if short == 0 else "fail",
                            "目標を満たす" if short == 0 else f"不足 {short} (計画書 §4: 満たせない件数は不足と報告)"))
    for r in res["r_rows"]:
        if r["included_in_r"]:
            res_, why = "pass", "根拠画像あり (時刻 ±{:g} 秒の保存フレーム)。内容は未判定".format(INH_FSAMPLE_R_FRAME_TOL_SEC)
        elif r["evidence"] == "not_located":
            res_, why = "excluded", "局面を特定できない: " + str(r.get("locate_reason") or "")
        elif r["evidence"] == "no_log":
            res_, why = "excluded", "対戦ログが無い"
        else:
            res_, why = "excluded", f"根拠画像なし (最も近い保存フレームまで {r.get('nearest_frame_sec')} 秒)"
        out.append(_measure(r["r_id"], {"origin": r["origin"], "ref": r["ref"], "battle": r["battle"], "t": r["t"]}, "diag",
                            res_, why, expected_source="人のラベル (未記入)。R は修理・回帰専用"))
    out.append(_measure("F-R-elapsed", {"budget_sec": INH_FSAMPLE_TIME_BUDGET_SEC}, "diag", "pass", "抽出の計算時間",
                        elapsed=res["elapsed_sec"]))
    return out


def label_estimate(cases: list) -> dict:
    n_sel = sum(1 for c in cases if c["family"] == FAMILY_SELECTION)
    n_turn = len(cases) - n_sel
    mins = n_sel * INH_FSAMPLE_LABEL_MIN_SELECTION + n_turn * INH_FSAMPLE_LABEL_MIN_TURN
    full = (INH_FSAMPLE_TARGET_DECISIONS * INH_FSAMPLE_LABEL_MIN_TURN if not cases else
            INH_FSAMPLE_TARGET_DECISIONS * mins / max(1, len(cases)))
    return {"n_selection": n_sel, "n_turn": n_turn, "min_per_selection": INH_FSAMPLE_LABEL_MIN_SELECTION,
            "min_per_turn": INH_FSAMPLE_LABEL_MIN_TURN, "minutes": mins, "minutes_if_60": round(full, 1),
            "budget_min": INH_FSAMPLE_LABEL_BUDGET_MIN, "fits": mins <= INH_FSAMPLE_LABEL_BUDGET_MIN,
            "fits_if_60": full <= INH_FSAMPLE_LABEL_BUDGET_MIN}


def render_report(res: dict, manifest: dict) -> str:
    f = res["f"]
    s = f_summary(f, res["sessions"])
    est = label_estimate(f["cases"])
    gh = near_gap_hist(f["excluded"])
    rr = res["r_rows"]
    L = [f"# 検証セット F (事実確認) と R (既知の失敗) の標本 — {RUN_ID} / {TEST_ID}", "",
         f"作成: {_local(manifest['finished_at'])} / コード: {CODE_COMMIT} + 作業ツリーの差分 (tools/inheritance_eval/fsample.py) / seed {res['seed']}。",
         "状態: **抽出のみ。正解ラベルは未記入** (labels_status = unlabeled)。この文書の件数は標本の構成で、誤り率などの測定結果ではない。", "",
         "## 1. F の構成", "",
         f"- 決定 **{s['decisions']}** / 対戦 **{s['battles']}** / セッション **{s['sessions']}** "
         f"(目標 {s['targets']['decisions']} / {s['targets']['battles']} / {s['targets']['sessions']})。"
         f"候補の総数 {s['pool']} (目標以下なので全部採用)" if s["not_sampled"] == 0 else
         f"- 決定 **{s['decisions']}** / 対戦 **{s['battles']}** / セッション **{s['sessions']}** (候補 {s['pool']} から対戦ごとに順に無作為)",
         f"- 場面: 行動 (command / move_select) {s['by_family'].get(FAMILY_TURN, 0)}、選出 (selection) {s['by_family'].get(FAMILY_SELECTION, 0)}",
         f"- 区分 (対戦単位): dev {s['by_split']['dev']['decisions']} 決定 / {s['by_split']['dev']['battles']} 対戦、"
         f"confirm {s['by_split']['confirm']['decisions']} 決定 / {s['by_split']['confirm']['battles']} 対戦",
         f"- その時刻に助言があった決定 {s['advice_present']} (参考。正解にしない、選び方にも使っていない)",
         f"- 代表の画像が決定画面の区間に無く、窓の中の情報確認の画面 (様子見など) だけの決定 "
         f"{sum(1 for c in f['cases'] if c['frame_basis'] == 'info_only')} (frame_basis = info_only。合法手は画像から確認できない見込み)",
         f"- 過去データ診断 (docs に対戦ログ名が出てくる = 開発で使った対戦) の決定 {s['past_data_diagnostic']} / {s['decisions']}", "",
         "| セッション | 対戦 | 決定 | 区分 |", "|---|---|---|---|"]
    for sid, v in s["by_session"].items():
        for b in v["battles"]:
            L.append(f"| {sid} | {b} | {s['by_battle'][b]} | {f['split'][b]} |")
    L += ["", "画像の内訳 (決定ごとの窓に入る保存フレームの種類):", ""]
    kinds = Counter()
    for c in f["cases"]:
        for k, v in c["frame_kinds_in_window"].items():
            kinds[k] += v
    L.append("- " + "、".join(f"{k} {v} 枚" for k, v in sorted(kinds.items())) + "。1 決定に付けたのは最大 "
             f"{INH_FSAMPLE_MAX_FRAMES_PER_CASE} 枚 (PNG があれば PNG、無ければ連続保存の JPEG から等間隔)")
    L += ["", "## 2. 不足と除外", "",
          f"- 不足: 決定 {s['shortfall']['decisions']}、対戦 {s['shortfall']['battles']}、セッション {s['shortfall']['sessions']} "
          "(計画書 §4「満たせない件数は不足と報告」)。",
          f"- 画像の無い決定: {gh['no_frame_total']} (保存フレームの残っている対戦の中で、決定の窓に 1 枚も無い)。窓の外 "
          + "、".join(f"{k} に保存フレームがある {v}" for k, v in gh.items() if k.startswith("<=")) + " (診断。F には入れていない)。",
          f"- 除外の理由別: " + "、".join(f"{k} {v}" for k, v in sorted(s["excluded"].items())),
          f"- 保存フレームの残る対戦ログ {len(res['with_frames'])} 本 / 走査した対戦ログ {len(res['spans'])} 本 (計算時間の上限で走査しなかった {len(res['unexecuted_logs'])} 本)。"
          f"フレームはあるが決定の窓に入らず F に 1 件も入らない対戦: {', '.join(sorted(set(res['with_frames']) - set(s['by_battle']))) or 'なし'}。",
          f"- 保存フレームの最も古い時刻 {_local(min((fr['t'] for fr in res['frames']), default=None))}。"
          "それより前の対戦 (例: R の 10/6 の局面) の画像は残っていない。",
          f"- 保存フレーム {len(res['frames'])} 枚 (対象外のファイル {len(res['ignored_files'])})。対戦ログの時間の幅の外のフレーム:"]
    for g, v in res["outside_groups"].items():
        L.append(f"  - {g}: {v['n']} 枚 ({v['from']} 〜 {v['to']})。対応する対戦ログが無いので状態の記録と突き合わせられない")
    L += ["", "## 3. R の内訳", ""]
    by = Counter((r["origin"], r["evidence"]) for r in rr)
    L.append("| 出典 | 画像あり | 画像なし | ログなし | 局面を特定できない |")
    L.append("|---|---|---|---|---|")
    for o in sorted({r["origin"] for r in rr}):
        L.append(f"| {o} | {by[(o, 'image_available')]} | {by[(o, 'no_image')]} | {by[(o, 'no_log')]} | {by[(o, 'not_located')]} |")
    inc = [r for r in rr if r["included_in_r"]]
    L += ["", f"R に入れた局面 (根拠画像あり): **{len(inc)}**。" + ("" if inc else "根拠画像を確認できた局面は無い。")]
    for r in inc:
        L.append(f"- {r['r_id']}: {r['battle']} t={r['t']} ({_local(r['t'])})、フレーム {len(r['frames'])} 枚。{r.get('what') or r.get('category')}")
    no_img = [r for r in rr if r["evidence"] == "no_image"]
    if no_img:
        L.append(f"- 画像なし {len(no_img)} 件: 最も近い保存フレームまで "
                 f"{min(r['nearest_frame_sec'] for r in no_img if r['nearest_frame_sec'] is not None) if any(r['nearest_frame_sec'] is not None for r in no_img) else '—'} 秒以上。"
                 "当時の保存フレームが debug_frames に残っていない")
    L += ["", "R は修理・回帰専用。F の発生率・確認成績に混ぜない。R の局面と同じ決定は F から外した (除外の理由 r_overlap)。", "",
          "## 4. ラベル作成の見積もり (未実測)", "",
          f"- 選出 {est['n_selection']} × {est['min_per_selection']:g} 分 + 行動 {est['n_turn']} × {est['min_per_turn']:g} 分 = "
          f"**{est['minutes']:g} 分** (予算 {est['budget_min']:g} 分: {'収まる' if est['fits'] else '超える'})。",
          f"- 60 件に増やした場合の見込み {est['minutes_if_60']:g} 分 ({'収まる' if est['fits_if_60'] else '超える'})。"
          "1 件あたりの分は見積もりで、最初の 5 件の実測で見直す。", "",
          "## 5. 次の手順", "",
          "1. 人が labeling_guide.md に従って cases.jsonl の truth を記入する (dev から。confirm は dev の手順が固まってから)。",
          "2. 不足を埋めるかの判断 (下の選択肢)。埋めない場合、F は上の構成のまま「標本不足」の注記つきで使う。",
          "3. R は画像のある局面だけ正解を記入する。画像の無い局面は修理用の記録として残し、事実の正解は付けない。", "",
          "不足を埋める選択肢 (未実施・判断待ち):",
          "- (a) 次の接続テストで DEBUG_DUMP_FRAMES=1 にして保存フレームを増やす (新しい対戦・セッションが増える。最も素直)。",
          "- (b) 決定の窓の外 (前後数秒) の保存フレームを足す (上の「窓の外」の件数)。決定画面そのものではないので、状態は確認できても合法手は確認できない。",
          "- (c) シーン判定に依存しない時刻抽出 (tools.scene_samples の frame_time の系統) の画像を足す。決定画面とは限らないので、人が決定画面かを確かめる欄が要る。", "",
          "## 6. 1 行のまとめ", "",
          f"用途: 段階 A の F (事実確認) と R (既知の失敗) の標本 → 対照: なし (標本の準備) → 正しさ: 未確認 (正解ラベル未記入) → "
          f"効果: 対象外 → 費用: 計算 {res['elapsed_sec']:g} 秒・人のラベル見積もり {est['minutes']:g} 分 → 採否: 保留 (標本不足 "
          f"決定 {s['shortfall']['decisions']} / 対戦 {s['shortfall']['battles']}、ラベル未記入) → 未確認範囲: 画像の内容、"
          "シーン判定が取りこぼした決定画面、保存フレームの無い決定・対戦"]
    return "\n".join(L) + "\n"


def write_outputs(res: dict, out_dir: Path, overwrite: bool = False) -> list:
    names = ("manifest.json", "cases.jsonl", "r_cases.jsonl", "measurements.jsonl", "report.md", "labeling_guide.md")
    exist = [n for n in names if (out_dir / n).exists()]
    if exist and not overwrite:
        raise SystemExit(f"出力先に既にあります (上書きは --overwrite): {', '.join(exist)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    f = res["f"]
    data_root = res["data_root"]
    used_logs = sorted({c["battle"] for c in f["cases"]} | {r["battle"] for r in res["r_rows"] if r.get("battle")})
    used_frames = sorted({n for c in f["cases"] for n in c["frames"]} | {n for r in res["r_rows"] for n in r.get("frames", [])})
    listing = hashlib.sha256("\n".join(f"{fr['name']}" for fr in res["frames"]).encode()).hexdigest()
    bdir = data_root / "logs" / "battles"
    manifest = {
        "test_id": TEST_ID, "run_id": RUN_ID, "code": {"commit": CODE_COMMIT, "worktree": _git_state(REPO),
                                                       "fsample_sha256": _sha256(Path(__file__)),
                                                       "config_sha256": _sha256(REPO / "champions_agent" / "config.py")},
        "data_root": str(data_root), "data_root_git": _git_state(data_root),
        "data": {"battle_logs_scanned": len(res["spans"]), "battle_logs_unexecuted": len(res["unexecuted_logs"]),
                 "battle_logs_used": {n: _sha256(bdir / n) for n in used_logs if (bdir / n).exists()},
                 "frames_total": len(res["frames"]), "frames_listing_sha256": listing,
                 "frames_used": {n: _sha256(data_root / "debug_frames" / n) for n in used_frames},
                 "scene_set": {str(res["scene_set"].name): _sha256(res["scene_set"])} if res["scene_set"].exists() else {},
                 "known_issues_sha256": _sha256(data_root / "docs" / "KNOWN_ISSUES.md") if (data_root / "docs" / "KNOWN_ISSUES.md").exists() else None,
                 "incident_reports": [p.name for p in res["incidents"]]},
        "conditions": {k: v for k, v in globals().items() if k.startswith("INH_FSAMPLE_")} | {
            "SCENE_SAMPLES_ANY_FRAME_PREFIXES": SCENE_SAMPLES_ANY_FRAME_PREFIXES,
            "decision_scenes": {FAMILY_TURN: [SCENE_COMMAND, SCENE_MOVE_SELECT], FAMILY_SELECTION: [SCENE_SELECTION]},
            "info_scenes": list(INFO_SCENES)},
        "seed": res["seed"], "time_budget_sec": INH_FSAMPLE_TIME_BUDGET_SEC, "elapsed_sec": res["elapsed_sec"],
        "finished_at": time.time(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=list) + "\n", encoding="utf-8")
    with (out_dir / "cases.jsonl").open("w", encoding="utf-8") as fh:
        for c in f["cases"]:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    with (out_dir / "r_cases.jsonl").open("w", encoding="utf-8") as fh:
        for r in res["r_rows"]:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "measurements.jsonl").open("w", encoding="utf-8") as fh:
        for m in measurements(res):
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    (out_dir / "report.md").write_text(render_report(res, manifest), encoding="utf-8")
    est = label_estimate(f["cases"])
    guide = GUIDE_TEMPLATE.read_text(encoding="utf-8")
    guide = guide.replace("{N_CASES}", str(len(f["cases"]))).replace("{N_SELECTION}", str(est["n_selection"])) \
        .replace("{N_TURN}", str(est["n_turn"])).replace("{MINUTES}", f"{est['minutes']:g}") \
        .replace("{MINUTES_60}", f"{est['minutes_if_60']:g}").replace("{BUDGET}", f"{est['budget_min']:g}") \
        .replace("{MIN_SEL}", f"{est['min_per_selection']:g}").replace("{MIN_TURN}", f"{est['min_per_turn']:g}") \
        .replace("{UNREADABLE_KINDS}", "\n".join(f"| `{k}` | {v} |" for k, v in UNREADABLE_KINDS.items())) \
        .replace("{FRAMES_DIR}", str(data_root / "debug_frames"))
    (out_dir / "labeling_guide.md").write_text(guide, encoding="utf-8")
    return [out_dir / n for n in names]


def main() -> None:
    ap = argparse.ArgumentParser(description="検証セット F / R の標本 (正解欄は空)。既定は dry-run")
    ap.add_argument("--data-root", default=str(REPO), help="logs/battles・debug_frames・logs/scenes・docs のある本体ツリー")
    ap.add_argument("--out-dir", default=None, help=f"出力先 (既定 <data-root>/logs/inheritance_eval/{RUN_ID}/{TEST_ID})")
    ap.add_argument("--seed", type=int, default=INH_FSAMPLE_SEED)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="出力先の既存のファイルを置き換える")
    args = ap.parse_args()
    root = Path(args.data_root).resolve()
    res = run(root, args.seed)
    s = f_summary(res["f"], res["sessions"])
    print(json.dumps({k: s[k] for k in ("decisions", "battles", "sessions", "shortfall", "by_split", "by_family", "excluded",
                                        "pool", "past_data_diagnostic", "advice_present")}, ensure_ascii=False, indent=1))
    print("R:", dict(Counter((r["origin"], r["evidence"]) for r in res["r_rows"])))
    print("near_gap:", near_gap_hist(res["f"]["excluded"]), "elapsed", res["elapsed_sec"])
    out = Path(args.out_dir) if args.out_dir else root / "logs" / "inheritance_eval" / RUN_ID / TEST_ID
    if not args.write:
        print(f"dry-run: 書いていません (書くなら --write、出力先 {out})")
        return
    for p in write_outputs(res, out, args.overwrite):
        print("保存:", p)


if __name__ == "__main__":
    main()
