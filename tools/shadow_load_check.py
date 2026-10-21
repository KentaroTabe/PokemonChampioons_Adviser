"""影の計算 (advice_variant) の負荷確認: 保存フレームを前面 10 fps 相当の間隔で流し、ON / OFF で助言の遅延を比べる。

    python -m tools.shadow_load_check --frames-dir <debug_frames> [--since "2026-10-06 18:00"] [--until ...]
        [--repeat 5] [--interval 0.1] [--rounds 2] [--limit N] [--json out.json]

計画 §2 の隔離条件 (e) (2026-10-07 判断 3): 前面のページで通常のフレーム (10 fps) が届く条件で、遅延の p50 / p95 / max と
期限超過率が悪化しないことを、接続テストの前に録画再生で確かめる。隠れたページの 1〜2 fps は余裕の根拠にしない。

- サーバー (server.handle_frame / _handle_one_frame) と同じ流れを模す: 受信は送信間隔ごとに別のタスク、処理中に届いたフレームは
  最新 1 枚だけ保持 (古い保持分は破棄)、フレーム処理 (VisionPipeline.process) と助言は既定の executor。
  助言の判断は server と同じ関数 advice_key.frame_advice_step (2026-10-21 レビュー: 旧い鍵の写しと 10 秒の更新間隔で
  Advisor.advise を直接呼んでいたのをやめた): 再計算の鍵 (版 2)・AdviceCache (有効期限 config.ADVICE_RECALC_TTL_SEC、退避・
  評価エラーは ADVICE_RECALC_FALLBACK_TTL_SEC)・DataVersions・確定前の助言の次のフレームの通し直し (recheck)・型推定の写し
  (inputs_fn) を server と同じ引数で渡す。キャッシュ・DataVersions・recheck は再生 1 回ごとに新しく持つ。
  ON のときだけ keep_common=True と影の計算の投入 (make_job + ShadowWorker.submit) を足す (server と同じ)。
- 対戦ログの局面の now: 記録の時刻 (tools.advice_replay.replay_now = t_eval → t_now → t_gen → t) + その局面を最初に送ってからの
  経過時間 (replay_clock)。現在の時計を使うと HP の観測からの経過時間が何日分にもなり、鍵 (経過時間の粒度) と transition の HP の
  範囲が本番と変わるため。保存フレームの認識結果で助言するときは現在の時計 (server と同じ)。行に ev_infer があれば型推定も入れる。
- 遅延 = 助言を出した時刻 (emit 相当) − その助言のもとになったフレームの到着時刻。期限超過率 = 遅延が
  SHADOW_DEADLINE_SEC を超えた助言の割合。影の計算のスキップの内訳 (待機中の破棄 / 実行中の中止 × 理由) も出す。
- 保存フレームは約 10 秒間隔 (DEBUG_DUMP_FRAMES) なので、--repeat で同じフレームを続けて送り「同じ画面が続く」状況を作る。
- 保存フレームには決定画面がほとんど無い (10/6〜7 の 179 枚で助言 1 件) ため、--battle-logs を渡すと助言は対戦ログの
  advice 行の局面 (同じ局面を --repeat 回) で計算し、保存フレームはフレーム処理 (OCR 等) の負荷として巡回して流す。
- 対戦ログ (logs/) には書かない (行は手元のリストに集めて件数だけ出す)。
- 採点方式の比較 (2026-10-16 実機の受入、docs/ADVICE_ACCURACY_PLAN_1009.md §8.22 の「負荷」): --compare-scoring で、影の計算は OFF の
  まま採点方式 legacy / transition を交互 (L→T→T→L を --rounds 回) に同じ録画で再生し、方式ごとの遅延・処理率 (proc_rate =
  処理 / 送信)・破棄を比べる。方式は本番と同じく設定 (config.ADVICE_SCORING_MODE) を回ごとに切り替えて渡す (2026-10-21。
  advise の scoring_mode 引数で迂回しない。鍵の採点の設定も同じ値になる)。感度 (config.TRANSITION_SENSITIVITY_ENABLED) は
  --sensitivity on|off (既定 off = 実機の設定。指定が無く環境変数 TRANSITION_SENSITIVITY があればその値)、表示用の探索は
  config.ADVICE_DISPLAY_SEARCH (環境変数 ADVICE_DISPLAY_SEARCH、既定 off) のまま。回ごとに 採点した件数 (evaluated)・キャッシュを
  使った件数・助言を出さなかったフレーム (cached_skip)・理由の内訳 (frame_advice_step の reason) を出す。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import json
import time
from pathlib import Path
from typing import Optional

from advice_key import AdviceCache, DataVersions, frame_advice_step
from champions_agent.config import SHADOW_DEADLINE_SEC, SHADOW_LOAD_FRAME_INTERVAL_SEC

REPO = Path(__file__).resolve().parent.parent
ADVICE_SCENES = ("command", "move_select", "watch")      # server._handle_one_frame の助言の条件と同じ
SENSITIVITY_VALUES = ("on", "off")
SENSITIVITY_DEFAULT = "off"                               # 実機の設定 (config/advice.local.env の TRANSITION_SENSITIVITY=0) と同じ


# ------------------------------------------------------------------ 純粋な計算
def percentiles(values_ms: list) -> dict:
    """{"n", "p50", "p95", "max"} (ms、小数 1 桁)。空なら None"""
    xs = sorted(values_ms)
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "max": None}

    def q(p):
        return round(xs[min(len(xs) - 1, int(round((len(xs) - 1) * p)))], 1)
    return {"n": len(xs), "p50": q(0.50), "p95": q(0.95), "max": round(xs[-1], 1)}


def step_counts(steps: list) -> dict:
    """frame_advice_step の戻り値の列 → 件数 (純粋): n_steps (判断したフレーム)、n_emitted (助言を出した)、n_evaluated (採点した)、
    n_cached (キャッシュの採点結果で出した)、reasons (reason ごとの件数)"""
    out = {"n_steps": 0, "n_emitted": 0, "n_evaluated": 0, "n_cached": 0, "reasons": {}}
    for st in steps:
        out["n_steps"] += 1
        r = str(st.get("reason"))
        out["reasons"][r] = out["reasons"].get(r, 0) + 1
        if st.get("emitted"):
            out["n_emitted"] += 1
            out["n_evaluated" if st.get("evaluated") else "n_cached"] += 1
    return out


def replay_clock(base_now: Optional[float], t_first: float, t_cur: float, wall: float) -> float:
    """再生の now (純粋): 記録の時刻 base_now + その局面を最初に送ってからの経過 (t_cur − t_first、perf_counter の秒)。
    base_now が無ければ現在の時計 wall (保存フレームの認識結果で助言するとき。server と同じ)"""
    if base_now is None:
        return float(wall)
    return float(base_now) + max(0.0, float(t_cur) - float(t_first))


def sensitivity_setting(arg: Optional[str], env: Optional[str]) -> bool:
    """再生の感度 (純粋): --sensitivity on|off の指定 → 無ければ環境変数 TRANSITION_SENSITIVITY が設定されていればその値
    (config.transition_sensitivity_from_value) → どちらも無ければ SENSITIVITY_DEFAULT (off = 実機と同じ)"""
    from champions_agent.config import transition_sensitivity_from_value
    if arg:
        return arg == "on"
    if env is not None and env.strip():
        return transition_sensitivity_from_value(env)
    return SENSITIVITY_DEFAULT == "on"


class _Settings:
    """config の採点の設定を再生の間だけ切り替える (本番と同じく設定で渡す。終わったら戻す)"""

    NAMES = ("ADVICE_SCORING_MODE", "TRANSITION_SENSITIVITY_ENABLED", "ADVICE_DISPLAY_SEARCH")

    def __init__(self, **values):
        self.values = {k: v for k, v in values.items() if v is not None}

    def __enter__(self):
        from champions_agent import config as C
        self.orig = {k: getattr(C, k) for k in self.NAMES}
        for k, v in self.values.items():
            setattr(C, k, v)
        return self

    def __exit__(self, *a):
        from champions_agent import config as C
        for k, v in self.orig.items():
            setattr(C, k, v)


def run_summary(latency_ms: list, advise_ms: list, proc_ms: list, n_sent: int, n_dropped: int,
                deadline_sec: float, shadow_stats: Optional[dict] = None, n_rows: int = 0,
                steps: Optional[dict] = None) -> dict:
    """1 回の再生の要約 (純粋)。steps = step_counts の戻り値 (あれば "steps" に入れる)"""
    over = sum(1 for x in latency_ms if x > deadline_sec * 1000.0)
    out = {"latency": percentiles(latency_ms), "advise": percentiles(advise_ms), "frame_proc": percentiles(proc_ms),
           "n_sent": n_sent, "n_processed": len(proc_ms), "n_dropped": n_dropped,
           "proc_rate": (round(len(proc_ms) / n_sent, 4) if n_sent else None),
           "over_deadline": over, "over_deadline_rate": (round(over / len(latency_ms), 4) if latency_ms else None)}
    if steps is not None:
        out["steps"] = steps
    if shadow_stats is not None:
        st = shadow_stats
        dropped = sum(st.get("dropped_pending", {}).values())
        aborted = sum(st.get("aborted_running", {}).values())
        late = st.get("dropped_pending", {}).get("expired", 0) + st.get("aborted_running", {}).get("deadline", 0)
        out["shadow"] = {"submitted": st.get("submitted", 0), "done": st.get("done", 0), "changed": st.get("changed", 0),
                         "dropped_pending": dict(st.get("dropped_pending", {})),
                         "aborted_running": dict(st.get("aborted_running", {})),
                         "n_skipped": dropped + aborted, "n_rows": n_rows,
                         "deadline_miss_rate": (round(late / st["submitted"], 4) if st.get("submitted") else None)}
    return out


def abba_order(a, b, rounds: int) -> list:
    """順序の偏り (温まり・熱) を均す並び a→b→b→a を rounds 回 (純粋)"""
    return [a, b, b, a] * max(1, int(rounds))


def _parse_when(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()


def select_frame_paths(frames_dir: Path, since: Optional[float], until: Optional[float], prefixes: tuple,
                       limit: Optional[int] = None) -> list:
    """保存フレーム (<prefix>_<unix 秒>.png) を時刻順に選ぶ"""
    out = []
    for p in frames_dir.glob("*.png"):
        pre, _, ts = p.stem.rpartition("_")
        if pre not in prefixes:
            continue
        try:
            t = int(ts)
        except ValueError:
            continue
        if (since is not None and t < since) or (until is not None and t > until):
            continue
        out.append((t, p))
    out.sort()
    paths = [p for _, p in out]
    return paths[:limit] if limit else paths


# ------------------------------------------------------------------ 再生 (副作用あり)
def _encode_frames(paths: list) -> list:
    """ブラウザと同じく JPEG (品質 80) の base64 にする (サーバーのデコードの費用も測る)"""
    import cv2
    out = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            out.append(base64.b64encode(buf).decode())
    return out


def build_sequence(n_frames: int, repeat: int, n_states: int = 0) -> list:
    """送るフレームの並び [(フレームの番号, 局面の番号 or None)] (純粋)。
    局面が無ければ各フレームを repeat 回ずつ。局面があれば各局面を repeat 回ずつ (同じ局面が続く = 同じ画面が続く)、
    フレームは保存フレームを順に巡回して使う (フレーム処理の負荷を掛けるため)"""
    r = max(1, repeat)
    if n_states <= 0:
        return [(fi, None) for fi in range(n_frames) for _ in range(r)]
    out = []
    for si in range(n_states):
        for _ in range(r):
            out.append((len(out) % max(1, n_frames), si))
    return out


def load_advice_states(paths: list, resolver=None) -> list:
    """対戦ログの advice 行 (kind=battle、provisional でない) → [(助言の入力の局面, 記録の時刻)] (tools.advice_replay.replay_state:
    now = t_eval → t_now → t_gen → t、行に ev_infer があれば型推定も入れる)。scene は command、turn はログの値"""
    from tools.advice_replay import replay_state
    out = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") != "advice" or r.get("kind") != "battle" or not r.get("state"):
                continue
            if (r.get("advice") or {}).get("provisional"):
                continue
            try:
                s, _info = replay_state(r, resolver)
            except Exception:
                continue
            s["scene"] = "command"
            s["turn"] = r.get("turn")
            out.append((s, s.get("now")))
    return out


class ReplayAdvice:
    """再生 1 回分の助言の判断 (server の _advice_cache / _data_versions / _advice_recheck にあたるものを持ち、frame_advice_step を
    server と同じ引数で呼ぶ)。step は同期 (呼び出し側が executor で呼ぶ)。steps に判断の記録 (reason / evaluated / 出したか) を残す"""

    def __init__(self, advisor, keep_common: bool = False, tracker=None, data_versions=None):
        self.advisor = advisor
        self.keep_common = keep_common
        self.cache = AdviceCache()
        self.versions = data_versions if data_versions is not None else DataVersions()
        self.recheck = False
        self.tracker = tracker
        self.steps: list = []

    def step(self, state: dict, now: float) -> dict:
        from battle_logger import ev_infer_of
        inputs_fn = None
        if self.tracker is not None:
            tr = self.tracker
            inputs_fn = lambda: ev_infer_of(state, tr.best_for)      # noqa: E731 (server と同じ写し)
        res = frame_advice_step(self.advisor, state, now, self.cache, self.versions, self.recheck,
                                keep_common=self.keep_common, inputs_fn=inputs_fn)
        if res["advice"] is not None:
            self.recheck = res["recheck"]
        self.steps.append({"reason": res["reason"], "evaluated": res["evaluated"], "emitted": res["advice"] is not None,
                           "key_changed": res["key_changed"], "t_eval": res["t_eval"], "now": now})
        return res


async def simulate(encoded: list, interval: float, repeat: int, shadow_on: bool,
                   deadline_sec: float = SHADOW_DEADLINE_SEC, advice_states: Optional[list] = None) -> dict:
    """サーバーの受信 → 処理 → 助言 の流れを模して 1 回再生する。採点の設定は呼び出し側が config で渡す (_Settings)。
    advice_states ([(局面, 記録の時刻)]、load_advice_states) があれば、助言は保存フレームの認識結果ではなく対戦ログの局面で
    計算する (保存フレームは約 10 秒間隔で決定画面がほとんど無いため。フレーム処理の負荷は保存フレームを巡回して掛ける)"""
    import cv2
    import numpy as np
    from advisor.ev_infer import get_tracker
    from advisor.service import Advisor
    from vision.pipeline import VisionPipeline

    pipe = VisionPipeline()
    advisor = Advisor(resolver=pipe.resolver)
    ra = ReplayAdvice(advisor, keep_common=shadow_on, tracker=get_tracker())
    first_sent: dict = {}
    rows: list = []
    worker = None
    make_job = compact = digest = None
    if shadow_on:
        from advisor.shadow import ShadowWorker, make_job
        from battle_logger import _compact_state as compact, state_digest as digest
        worker = ShadowWorker(sink=rows.append, deadline_sec=deadline_sec)
    loop = asyncio.get_running_loop()
    st = {"busy": False, "pending": None, "dropped": 0, "n_adv": 0}
    latency_ms, advise_ms, proc_ms = [], [], []

    async def handle_one(t_arr: float, b64: str, adv: Optional[tuple] = None) -> None:
        nparr = np.frombuffer(base64.b64decode(b64), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        t_p = time.perf_counter()
        state, _fired = await loop.run_in_executor(None, pipe.process, img)
        proc_ms.append((time.perf_counter() - t_p) * 1000.0)
        base_now, t_first = None, t_arr
        if adv is not None:
            # 助言は対戦ログの局面で計算する (フレーム処理の負荷は保存フレームで掛ける)。now は記録の時刻 + 最初に送ってからの経過
            si, (state, base_now) = adv[0], adv[1]
            t_first = first_sent.get(si, t_arr)
        if state["scene"] in ADVICE_SCENES and not state.get("battle_ended") and not state.get("outcome"):
            now = replay_clock(base_now, t_first, time.perf_counter(), time.time())
            t_a = time.perf_counter()
            res = await loop.run_in_executor(None, ra.step, state, now)
            if res["advice"] is None:
                return
            advice = res["advice"]
            t_emit = time.perf_counter()
            if res["evaluated"]:
                advise_ms.append((t_emit - t_a) * 1000.0)       # server の _advise_ms と同じく採点したときだけ
            latency_ms.append((t_emit - t_arr) * 1000.0)
            st["n_adv"] += 1
            advice["text"] = advisor.format_advice(advice)
            if worker is not None:
                job = make_job(advisor.last_common, f"sim-{st['n_adv']:05d}", digest(compact(state)),
                               shown_best=advice.get("best"))
                worker.submit(job)

    async def on_frame(t_arr: float, b64: str, adv_state: Optional[tuple]) -> None:
        if st["busy"]:
            if st["pending"] is not None:
                st["dropped"] += 1
            st["pending"] = (t_arr, b64, adv_state)
            return
        st["busy"] = True
        try:
            await handle_one(t_arr, b64, adv_state)
            while st["pending"] is not None:
                p = st["pending"]
                st["pending"] = None
                await handle_one(*p)
        finally:
            st["busy"] = False

    seq = build_sequence(len(encoded), repeat, len(advice_states) if advice_states else 0)
    t0 = time.perf_counter()
    tasks = []
    for i, (fi, si) in enumerate(seq):
        delay = t0 + i * interval - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        adv_state = (si, advice_states[si]) if (advice_states and si is not None) else None
        t_arr = time.perf_counter()
        if adv_state is not None:
            first_sent.setdefault(si, t_arr)
        tasks.append(asyncio.ensure_future(on_frame(t_arr, encoded[fi], adv_state)))
    await asyncio.gather(*tasks)
    stats = None
    if worker is not None:
        await loop.run_in_executor(None, worker.wait_idle, 10.0)
        worker.stop()
        stats = worker.stats
    return run_summary(latency_ms, advise_ms, proc_ms, len(seq), st["dropped"], deadline_sec, stats, len(rows),
                       steps=step_counts(ra.steps))


def _fmt(label: str, s: dict) -> str:
    lat, adv, fp = s["latency"], s["advise"], s["frame_proc"]
    line = (f"[{label}] 助言 {lat['n']} 件 遅延 p50 {lat['p50']} / p95 {lat['p95']} / max {lat['max']} ms "
            f"(期限 {SHADOW_DEADLINE_SEC:g} 秒超過 {s['over_deadline']} = {s['over_deadline_rate']}) / 助言の計算 p50 {adv['p50']} "
            f"p95 {adv['p95']} ms / フレーム処理 p50 {fp['p50']} p95 {fp['p95']} ms / 送信 {s['n_sent']} 処理 {s['n_processed']} "
            f"破棄 {s['n_dropped']} (処理率 {s.get('proc_rate')})")
    stp = s.get("steps")
    if stp:
        line += (f"\n    助言の判断 {stp['n_steps']} フレーム: 出した {stp['n_emitted']} (採点 {stp['n_evaluated']} / キャッシュ "
                 f"{stp['n_cached']}) / 理由 {stp['reasons']}")
    sh = s.get("shadow")
    if sh:
        line += (f"\n    影の計算: 投入 {sh['submitted']} 計算 {sh['done']} (第一候補の変化 {sh['changed']}) / 待機中の破棄 "
                 f"{sh['dropped_pending'] or 0} / 実行中の中止 {sh['aborted_running'] or 0} / 期限に間に合わない率 "
                 f"{sh['deadline_miss_rate']}")
    return line


def _load_avg() -> Optional[list]:
    import os
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def _compare_scoring(args, encoded: list, paths: list, states: Optional[list], sens: bool) -> None:
    """採点方式の比較 (影の計算 OFF、legacy / transition を交互)。方式は config.ADVICE_SCORING_MODE を回ごとに切り替える (本番と同じ
    設定の経路)。方式ごとに遅延・処理率・破棄・採点した件数を並べる"""
    from champions_agent.config import ADVICE_SCORING_MODES
    a, b = ADVICE_SCORING_MODES[0], ADVICE_SCORING_MODES[1]
    if args.warmup > 0:
        for m in (a, b):
            with _Settings(ADVICE_SCORING_MODE=m, TRANSITION_SENSITIVITY_ENABLED=sens):
                w = asyncio.run(simulate(encoded[:args.warmup], args.interval, args.repeat, False,
                                         advice_states=(states[:args.warmup] if states else None)))
            print(f"(ウォームアップ {m} {args.warmup}: 助言 {w['latency']['n']} 件、結果は使わない)")
    results = []
    for m in abba_order(a, b, args.rounds):
        load0 = _load_avg()
        with _Settings(ADVICE_SCORING_MODE=m, TRANSITION_SENSITIVITY_ENABLED=sens):
            s = asyncio.run(simulate(encoded, args.interval, args.repeat, False, advice_states=states))
        s["load_before"], s["load_after"] = load0, _load_avg()
        print(_fmt(m, s) + f"\n    load average (前 / 後) {s['load_before']} / {s['load_after']}")
        results.append({"scoring_mode": m, **s})
    agg = {}
    for m in (a, b):
        rs = [r for r in results if r["scoring_mode"] == m]
        agg[m] = {k: [r["latency"][k] for r in rs] for k in ("p50", "p95", "max")}
        agg[m].update(over_deadline_rate=[r["over_deadline_rate"] for r in rs], proc_rate=[r["proc_rate"] for r in rs],
                      n_dropped=[r["n_dropped"] for r in rs],
                      n_evaluated=[(r.get("steps") or {}).get("n_evaluated") for r in rs],
                      n_cached=[(r.get("steps") or {}).get("n_cached") for r in rs])
    print("まとめ (回ごと):", json.dumps(agg, ensure_ascii=False))
    if args.json:
        from champions_agent import config as C
        Path(args.json).write_text(json.dumps({"frames": [p.name for p in paths], "repeat": args.repeat, "interval": args.interval,
                                               "compare": "scoring", "sensitivity": sens,
                                               "display_search": C.ADVICE_DISPLAY_SEARCH,
                                               "recalc_ttl_sec": C.ADVICE_RECALC_TTL_SEC,
                                               "recalc_fallback_ttl_sec": C.ADVICE_RECALC_FALLBACK_TTL_SEC,
                                               "runs": results, "agg": agg},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {args.json}")


def main() -> None:
    ap = argparse.ArgumentParser(description="影の計算の負荷確認 (保存フレームの 10 fps 再生、ON / OFF)")
    ap.add_argument("--frames-dir", default=str(REPO / "debug_frames"))
    ap.add_argument("--since", default=None, help="この時刻以降のフレーム (unix 秒 か 'YYYY-mm-dd HH:MM')")
    ap.add_argument("--until", default=None)
    ap.add_argument("--prefixes", default="frame", help="使うフレームの接頭辞 (カンマ区切り。frame = 対戦中の定期保存)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--repeat", type=int, default=5, help="同じフレームを続けて送る回数 (同じ画面が続く状況)")
    ap.add_argument("--interval", type=float, default=SHADOW_LOAD_FRAME_INTERVAL_SEC, help="送信間隔 (秒)。前面 = 0.1")
    ap.add_argument("--rounds", type=int, default=1, help="OFF→ON→ON→OFF の組を何回回すか (順序の偏りを均す)")
    ap.add_argument("--warmup", type=int, default=12, help="測定の前に流して捨てるフレーム数 (初回の読み込みを除く)")
    ap.add_argument("--battle-logs", nargs="*", default=None,
                    help="対戦ログ (jsonl)。指定すると助言はその advice 行の局面で計算し、保存フレームはフレーム処理の負荷に使う")
    ap.add_argument("--json", default=None, help="結果の保存先 (logs/ 以外を推奨)")
    ap.add_argument("--compare-scoring", action="store_true",
                    help="影の計算は OFF のまま、採点方式 legacy / transition を交互に再生して比べる (実機の受入の負荷)")
    ap.add_argument("--sensitivity", choices=SENSITIVITY_VALUES, default=None,
                    help=f"transition の感度 (既定 {SENSITIVITY_DEFAULT} = 実機と同じ。指定が無く環境変数 TRANSITION_SENSITIVITY が"
                         "あればその値)")
    args = ap.parse_args()
    import os
    sens = sensitivity_setting(args.sensitivity, os.environ.get("TRANSITION_SENSITIVITY"))

    paths = select_frame_paths(Path(args.frames_dir), _parse_when(args.since), _parse_when(args.until),
                               tuple(x.strip() for x in args.prefixes.split(",") if x.strip()), args.limit)
    if not paths:
        raise SystemExit(f"フレームがありません: {args.frames_dir}")
    encoded = _encode_frames(paths)
    states = None
    if args.battle_logs:
        from vision.normalize import NameResolver
        states = load_advice_states(args.battle_logs, NameResolver())
        print(f"対戦ログ {len(args.battle_logs)} 本の局面 {len(states)} 件 × {args.repeat} 回 (フレームは巡回)")
    print(f"フレーム {len(encoded)} 枚 ({paths[0].name} 〜 {paths[-1].name}) × {args.repeat} 回 / 間隔 {args.interval} 秒 / "
          f"感度 {'on' if sens else 'off'} / load average {_load_avg()}")
    from vision import ocr
    ocr.preload()
    if args.compare_scoring:
        _compare_scoring(args, encoded, paths, states, sens)
        return
    if args.warmup > 0:
        # OCR・RL のモデル読み込み・dex 等の初回の費用を測定から外す (結果は捨てる)
        with _Settings(TRANSITION_SENSITIVITY_ENABLED=sens):
            w = asyncio.run(simulate(encoded[:args.warmup], args.interval, args.repeat, True,
                                     advice_states=(states[:args.warmup] if states else None)))
        print(f"(ウォームアップ {args.warmup}: 助言 {w['latency']['n']} 件、結果は使わない)")
    results = []
    order = []
    for _ in range(max(1, args.rounds)):
        order += [False, True, True, False]
    for on in order:
        with _Settings(TRANSITION_SENSITIVITY_ENABLED=sens):
            s = asyncio.run(simulate(encoded, args.interval, args.repeat, on, advice_states=states))
        label = "ON " if on else "OFF"
        print(_fmt(label, s))
        results.append({"shadow_on": on, **s})
    agg = {}
    for on in (False, True):
        rs = [r for r in results if r["shadow_on"] == on]
        agg["on" if on else "off"] = {k: [r["latency"][k] for r in rs] for k in ("p50", "p95", "max")}
        agg["on" if on else "off"]["over_deadline_rate"] = [r["over_deadline_rate"] for r in rs]
    print("まとめ (回ごと):", json.dumps(agg, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps({"frames": [p.name for p in paths], "repeat": args.repeat,
                                               "interval": args.interval, "runs": results, "agg": agg},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {args.json}")


if __name__ == "__main__":
    main()
