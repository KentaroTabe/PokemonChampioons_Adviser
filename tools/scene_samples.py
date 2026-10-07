"""局面の標本 (2026-10-07 docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2「局面の標本 (3 種に分ける)」): 1 回の接続テストの
対戦ログと保存フレームから、正解ラベルを付ける局面を切り出す。既定は dry-run (書かずに件数だけ出す)。

    python -m tools.scene_samples [--session | --battle <log> ... | --last N] [--frames-dir debug_frames] [--seed S]
    python -m tools.scene_samples --session --write [--out logs/scenes/samples_<日付>.jsonl]

取る標本 (件数・間隔は champions_agent/config.py の SCENE_SAMPLES_*):
  (1) fixed_failure  … 既知の失敗を含む固定局面集への追加候補。advice_trace の表示の行で分類した候補 (tools.scene_eval.extract_candidates)
                       から失敗 (hp_stuck / advice_stop / late / stale) を 3 件。修正確認用で、推移の指標には使わない
  (2a) advice_random … advice の行 (battle、provisional を除く) から成否に関係なく無作為に 10 件
  (2b) 入力映像側から無作為に 10 件。2 系統を半々:
       frame_scene   … 対戦ログのシーン判定が決定画面 (command / move_select / selection) だった時刻の保存フレーム
       frame_time    … シーン判定に依存しない時刻抽出: 各対戦の開始から 60 秒ごとの時刻に最も近い保存フレーム
                       (シーン判定で保存される sel_ / fc_ は使わず、約 10 秒おきの frame_ だけ)
       その時刻の画面に出ていた助言 (直前に生成された助言、60 秒以内) が無ければ category を no_advice (助言なし) にして残す
  (3) 短い連続フレームはここでは扱わない (保存の仕組みは別の担当)

出力の 1 行は logs/scenes/newteam_1006.jsonl と同じ形 (scene_id / category / source / system_state / system_advice / display / truth /
labels_status) に、source.kind (上の 4 種) と、映像側は source.frame (保存フレームのファイル名) / source.scene_logged (その時刻のシーン判定)
を足したもの。category は tools.scene_eval の分類 (consistent / hp_stuck / advice_stop / late / stale / display_unconfirmed /
display_hidden / display_unknown / other) に no_advice を足したもの。表示の判定 (判断 9、2026-10-07) は scene_eval.classify と同じ:
display の行が無いログは display_unknown、通知が欠けただけの助言は display_unconfirmed で、advice_stop は表示経路の欠陥と確認できたときだけ。
display_* は判定不能なので (1) の候補に入らない。source.display_logged はそのログに display の行があるか。
seed で決定的。既存のファイルには書かない (出力先が既にあれば止まる)。
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from champions_agent.config import (
    SCENE_SAMPLES_ADVICE_MAX_AGE_SEC, SCENE_SAMPLES_ANY_FRAME_PREFIXES, SCENE_SAMPLES_FRAME_INTERVAL_SEC,
    SCENE_SAMPLES_FRAME_TOL_SEC, SCENE_SAMPLES_N_ADVICE_RANDOM, SCENE_SAMPLES_N_FIXED_FAILURE, SCENE_SAMPLES_N_FRAME_RANDOM,
    SCENE_SAMPLES_SEED, SCENE_SAMPLES_TIME_FRAME_PREFIXES)
from tools.advice_trace import display_rows, load_records
from tools.scene_eval import (
    CATEGORY_DISPLAY_HIDDEN, CATEGORY_DISPLAY_UNCONFIRMED, CATEGORY_DISPLAY_UNKNOWN, classify, extract_candidates, pick_scenes,
    write_scene_set)
from vision.scenes import SCENE_COMMAND, SCENE_MOVE_SELECT, SCENE_SELECTION

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
SCENES_DIR = REPO / "logs" / "scenes"
FRAMES_DIR = REPO / "debug_frames"
MARKER = REPO / "logs" / ".connection_test_start"

KIND_FIXED_FAILURE = "fixed_failure"
KIND_ADVICE_RANDOM = "advice_random"
KIND_FRAME_SCENE = "frame_scene"
KIND_FRAME_TIME = "frame_time"
KINDS = (KIND_FIXED_FAILURE, KIND_ADVICE_RANDOM, KIND_FRAME_SCENE, KIND_FRAME_TIME)
CATEGORY_NO_ADVICE = "no_advice"
DECISION_SCENES = (SCENE_COMMAND, SCENE_MOVE_SELECT, SCENE_SELECTION)
_FRAME_RE = re.compile(r"^([a-z]+_)(\d+(?:\.\d+)?)\.(?:png|jpg)$")


# ------------------------------------------------------------------ 純粋
def frame_time(name: str) -> Optional[tuple]:
    """保存フレームのファイル名 (frame_1791280056.png) → (接頭辞, 時刻)。形が違えば None"""
    m = _FRAME_RE.match(Path(name).name)
    if not m:
        return None
    return m.group(1), float(m.group(2))


def battle_window(records: list) -> Optional[tuple]:
    """対戦ログの時間の幅 (最初と最後の行の t)。t が無ければ None"""
    ts = [float(d["t"]) for d in records if d.get("t") is not None]
    return (min(ts), max(ts)) if ts else None


def scene_at(records: list, t: float) -> Optional[dict]:
    """時刻 t 以前で最後の scene の行 (シーン判定)。無ければ None"""
    last = None
    for d in records:
        if d.get("type") == "scene" and d.get("t") is not None and float(d["t"]) <= t:
            if last is None or float(d["t"]) >= float(last["t"]):
                last = d
    return last


def _t_gen(d: dict) -> Optional[float]:
    v = (d.get("advice") or {}).get("t_gen") or d.get("t")
    return float(v) if v is not None else None


def advice_at(records: list, t: float, max_age: float = SCENE_SAMPLES_ADVICE_MAX_AGE_SEC) -> Optional[dict]:
    """時刻 t の画面に出ていた助言 = t 以前に生成された最後の助言 (battle / selection、provisional を除く) で、
    生成から max_age 秒以内のもの。無ければ None (助言なし)"""
    best = None
    for d in records:
        if d.get("type") != "advice" or d.get("kind") not in ("battle", "selection"):
            continue
        adv = d.get("advice") or {}
        if adv.get("provisional"):
            continue
        tg = _t_gen(d)
        if tg is None or tg > t:
            continue
        if best is None or tg >= _t_gen(best):
            best = d
    if best is None or t - _t_gen(best) > max_age:
        return None
    return best


def _system_advice(d: dict) -> Optional[dict]:
    adv = d.get("advice") or {}
    if d.get("kind") == "selection":
        rec = adv.get("recommend") or []
        return {"kind": "selection", "recommend": [r.get("name") for r in rec]} if rec else None
    best = adv.get("best") or ((adv.get("actions") or [None])[0])
    return {"kind": best.get("kind"), "id": best.get("id"), "name": best.get("name")} if best else None


def frame_row(file_name: str, records: list, frame_name: str, t: float, kind: str, disp: dict,
              max_age: float = SCENE_SAMPLES_ADVICE_MAX_AGE_SEC) -> dict:
    """保存フレーム 1 枚 → 局面の行 (純粋)。その時刻の助言が無ければ category = no_advice、system_state はシーン判定の状態"""
    sc = scene_at(records, t)
    adv = advice_at(records, t, max_age)
    src = {"file": file_name, "advice_id": None, "t": t, "turn": (sc or {}).get("turn"), "version_id": None, "state_id": None,
           "kind": kind, "frame": Path(frame_name).name, "scene_logged": (sc or {}).get("scene")}
    row = {"category": CATEGORY_NO_ADVICE, "source": src, "system_state": (sc or {}).get("state"), "system_advice": None,
           "display": None, "truth": {"state": None, "legal_actions": None, "deadline_s": None, "notes": ""},
           "labels_status": "unlabeled"}
    if adv is not None:
        aid = adv.get("advice_id")
        drow = disp.get(aid) if aid else None
        row.update(category=classify(adv, records, drow), system_state=adv.get("state") or row["system_state"],
                   system_advice=_system_advice(adv), display=drow)
        src.update(advice_id=aid, version_id=adv.get("version_id"), state_id=adv.get("state_id"), advice_t=_t_gen(adv),
                   advice_kind=adv.get("kind"))
    return row


def scene_frame_candidates(battles: list, frames: list) -> list:
    """シーン判定が決定画面だった時刻の保存フレーム [(file, records, frame_name, t)]。どの接頭辞でもよい"""
    out = []
    for name, t in frames:
        for fname, recs, win in battles:
            if win and win[0] <= t <= win[1]:
                sc = scene_at(recs, t)
                if sc is not None and sc.get("scene") in DECISION_SCENES:
                    out.append((fname, recs, name, t))
                break
    return out


def time_frame_candidates(battles: list, frames: list, interval: float = SCENE_SAMPLES_FRAME_INTERVAL_SEC,
                          tol: float = SCENE_SAMPLES_FRAME_TOL_SEC) -> list:
    """シーン判定に依存しない時刻抽出: 各対戦の開始 + k × interval (k = 0, 1, ...) に最も近い保存フレーム (差が tol 以内、1 枚 1 回)"""
    out = []
    used: set = set()
    for fname, recs, win in battles:
        if not win:
            continue
        k = 0
        while win[0] + k * interval <= win[1]:
            target = win[0] + k * interval
            k += 1
            near = [(abs(t - target), name, t) for name, t in frames if name not in used and win[0] <= t <= win[1]]
            if not near:
                continue
            d, name, t = min(near)
            if d <= tol:
                used.add(name)
                out.append((fname, recs, name, t))
    return out


def _sample(rng: random.Random, items: list, n: int) -> list:
    return rng.sample(items, min(n, len(items))) if n > 0 else []


def build_samples(battles: list, frames: list, seed: int = SCENE_SAMPLES_SEED, n_fixed: int = SCENE_SAMPLES_N_FIXED_FAILURE,
                  n_advice: int = SCENE_SAMPLES_N_ADVICE_RANDOM, n_frame: int = SCENE_SAMPLES_N_FRAME_RANDOM,
                  interval: float = SCENE_SAMPLES_FRAME_INTERVAL_SEC, tol: float = SCENE_SAMPLES_FRAME_TOL_SEC,
                  max_age: float = SCENE_SAMPLES_ADVICE_MAX_AGE_SEC, time_prefixes=SCENE_SAMPLES_TIME_FRAME_PREFIXES,
                  any_prefixes=SCENE_SAMPLES_ANY_FRAME_PREFIXES) -> dict:
    """標本を作る (純粋)。battles: [(ファイル名, records)]、frames: [保存フレームのファイル名]。
    戻り値: {"rows": [行], "pools": {kind: 候補数}}。(2a) と (2b) は (1) と独立に抽出する (同じ局面が両方に入ることがある)"""
    rng = random.Random(seed)
    bat = [(f, recs, battle_window(recs)) for f, recs in battles]
    rows: list = []
    cands: list = []
    for f, recs, _w in bat:
        logged = any(d.get("type") == "display" for d in recs)
        for c in extract_candidates(f, recs):
            c["source"]["display_logged"] = logged
            cands.append(c)
    # 表示の判定不能 (display_unknown / display_unconfirmed / display_hidden、判断 9) は失敗ではないので固定失敗集の候補から外す
    # (display の行が無いログの助言は display_unknown になり、ここで外れる)
    undecided = (CATEGORY_DISPLAY_UNKNOWN, CATEGORY_DISPLAY_UNCONFIRMED, CATEGORY_DISPLAY_HIDDEN)
    fixed_pool = [c for c in cands if c["category"] not in undecided]
    fixed = pick_scenes(fixed_pool, 0, n_fixed, seed)
    for c in fixed:
        rows.append(dict(c, source=dict(c["source"], kind=KIND_FIXED_FAILURE)))
    pool_sorted = sorted(cands, key=lambda c: (c["source"]["file"], str(c["source"]["advice_id"])))
    for c in _sample(rng, pool_sorted, n_advice):
        rows.append(dict(c, source=dict(c["source"], kind=KIND_ADVICE_RANDOM)))
    parsed = sorted((Path(n).name, ft) for n in frames for ft in [frame_time(n)] if ft is not None)
    any_frames = [(n, ft[1]) for n, ft in parsed if ft[0] in tuple(any_prefixes)]
    time_frames = [(n, ft[1]) for n, ft in parsed if ft[0] in tuple(time_prefixes)]
    scene_pool = scene_frame_candidates(bat, any_frames)
    time_pool = time_frame_candidates(bat, time_frames, interval, tol)
    n_scene = n_frame // 2
    n_time = n_frame - n_scene
    disp_cache: dict = {}
    for kind, pool, n in ((KIND_FRAME_SCENE, scene_pool, n_scene), (KIND_FRAME_TIME, time_pool, n_time)):
        for fname, recs, name, t in _sample(rng, pool, n):
            if fname not in disp_cache:
                disp_cache[fname] = {r["advice_id"]: r for r in display_rows(recs)}
            row = frame_row(fname, recs, name, t, kind, disp_cache[fname], max_age)
            row["source"]["display_logged"] = any(d.get("type") == "display" for d in recs)
            rows.append(row)
    return {"rows": rows, "pools": {KIND_FIXED_FAILURE: sum(1 for c in fixed_pool if c["category"] != "consistent"),
                                    KIND_ADVICE_RANDOM: len(cands), KIND_FRAME_SCENE: len(scene_pool), KIND_FRAME_TIME: len(time_pool)}}


def summarize(res: dict) -> dict:
    rows = res["rows"]
    return {"n": len(rows), "pools": res["pools"],
            "by_kind": {k: dict(Counter(r["category"] for r in rows if r["source"].get("kind") == k)) for k in KINDS},
            "files": dict(Counter(r["source"]["file"] for r in rows))}


# ------------------------------------------------------------------ 配線
def _session_files(battles_dir: Path, marker: Path) -> list:
    try:
        ts = float(marker.read_text().strip())
    except (OSError, ValueError):
        return []
    return [p for p in sorted(battles_dir.glob("battle_*.jsonl")) if p.stat().st_mtime >= ts]


def _default_out(files: list) -> Path:
    stamp = Path(files[0]).stem.replace("battle_", "")[:8] if files else "unknown"
    return SCENES_DIR / f"samples_{stamp}.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser(description="局面の標本 (固定失敗・advice 無作為・映像側 無作為) を切り出す。既定は dry-run")
    ap.add_argument("--battle", action="append", default=None, help="対戦ログ (複数可)")
    ap.add_argument("--last", type=int, default=None, help="直近 N 本の対戦ログ")
    ap.add_argument("--session", action="store_true", help="接続テスト開始マーカー以降の対戦ログ")
    ap.add_argument("--battles-dir", default=str(BATTLE_DIR))
    ap.add_argument("--frames-dir", default=str(FRAMES_DIR))
    ap.add_argument("--seed", type=int, default=SCENE_SAMPLES_SEED)
    ap.add_argument("--out", default=None, help="出力先 (既定 logs/scenes/samples_<最初の対戦の日付>.jsonl。既にあれば止まる)")
    ap.add_argument("--write", action="store_true", help="書き出す (無ければ dry-run: 件数だけ)")
    args = ap.parse_args()
    bdir = Path(args.battles_dir)
    if args.battle:
        files = [Path(p) for p in args.battle]
    elif args.session:
        files = _session_files(bdir, MARKER)
    else:
        files = sorted(bdir.glob("battle_*.jsonl"))[-(args.last or 1):]
    if not files:
        raise SystemExit("対戦ログがありません")
    battles = [(p.name, load_records(p)) for p in files]
    fdir = Path(args.frames_dir)
    frames = [p.name for p in fdir.glob("*.png")] if fdir.exists() else []
    res = build_samples(battles, frames, seed=args.seed)
    print(json.dumps(summarize(res), ensure_ascii=False, indent=1))
    out = Path(args.out) if args.out else _default_out(files)
    if not args.write:
        print(f"dry-run: 書いていません (書くなら --write、出力先 {out})")
        return
    if out.exists():
        raise SystemExit(f"出力先が既にあります (追記・上書きはしない): {out}")
    write_scene_set(res["rows"], out)
    print(f"保存: {out} ({len(res['rows'])} 行)")


if __name__ == "__main__":
    main()
