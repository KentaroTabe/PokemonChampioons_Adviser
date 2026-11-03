"""影の計算 (advice_variant): 表示しない代替条件の助言を同時に計算して記録する。

docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2 の advice_variant 行と §4「RL の行動確率の加点」(判断 3、2026-10-07)。
表示した助言と**同じ採点の共通部分** (advisor.engine.evaluate_common の戻り値: 状態・RL の行動分布を助言時点で固定した
snapshot) から、RL 加点の重みだけ変えて**加点以降 (RL 加点 → KO 前割引 → 交代技の補正 → 並べ替え) を再計算**し
(advisor.engine.rescore_actions)、重みごとの上位と点差を advice_id で紐づけた別行に残す。

隔離条件 (計画 §2 の (a)〜(e)):
  (a) 重みは関数の引数で渡す (環境変数・モジュール変数を書かない)。
  (b) 入力は助言時点の snapshot (make_job が深く複製する)。RL のモデルを再度引かない (行動分布は助言時点のもの)。
  (c) 共通部分を再利用し、加点の後の KO 前割引・交代技補正まで再計算する。
  (d) 待ち行列の上限 (config SHADOW_QUEUE_MAX)。破棄の基準は状態の更新 (新しい仕事の state_id が違う) と期限
      (生成から SHADOW_DEADLINE_SEC)。待機中の破棄 (dropped_pending) と実行中の中止 (aborted_running: 期限を越えたら
      重みの区切りで止める) を区別し、スキップした advice_id ごとに skipped 付きの advice_variant 行を書く。
  (e) 前面 10 fps での遅延の確認は tools/shadow_load_check。
ワーカーはスレッド 1 本。本番の状態・設定 (環境変数・モジュール変数・共有の dict) を書き換えない。
行の書き込み先 (sink) は呼び出し側が渡す (サーバーはイベントループのスレッドへ回して battle_logger に書く)。
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from advisor.engine import RL_BLEND_DEFAULT, rescore_actions
from champions_agent.config import (SHADOW_DEADLINE_SEC, SHADOW_QUEUE_MAX, SHADOW_RL_BLEND_WEIGHTS,
                                    SHADOW_TOP_N)

RECORD_TYPE = "advice_variant"
# スキップの種類 (行の skipped) と理由 (skip_reason)
SKIP_DROPPED = "dropped_pending"      # 待機中に捨てた (計算していない)
SKIP_ABORTED = "aborted_running"      # 計算を始めた後に区切りで止めた
REASON_STATE_CHANGED = "state_changed"   # 次の助言の状態 (state_id) が変わった
REASON_EXPIRED = "expired"               # 待機中に期限を過ぎた
REASON_QUEUE_FULL = "queue_full"         # 待ち行列の上限を超えた (古い方を捨てる)
REASON_DEADLINE = "deadline"             # 実行中に期限を過ぎた
REASON_STOPPED = "stopped"               # ワーカーの停止
REASON_ERROR = "error"                   # 計算中の例外


# ------------------------------------------------------------------
# 純粋な計算
# ------------------------------------------------------------------
def snapshot_inputs(common: dict) -> dict:
    """採点の共通部分から、加点以降の再計算に要る入力だけを深く複製する (助言時点の snapshot)。
    表示側がこの後に助言の辞書を書き換えても影響を受けない"""
    return copy.deepcopy({
        "actions": common.get("actions") or [],
        "rl_hint": common.get("rl_hint"),
        "move_type_mult": common.get("move_type_mult") or {},
        "context": common.get("context") or {},
    })


def _action_key(a: Optional[dict]) -> Optional[str]:
    if not a:
        return None
    return f"{a.get('kind')}:{a.get('id')}"


def variant_summary(actions: list, weight: float, top_n: int = SHADOW_TOP_N) -> dict:
    """再計算した行動の並び → {"rl_blend", "top": 上位 top_n [{kind,id,name,score}], "gaps": 1 位との点差 (2 位以降)} (純粋)"""
    top = [{"kind": a.get("kind"), "id": a.get("id"), "name": a.get("name"), "score": a.get("score")}
           for a in actions[:top_n]]
    gaps = []
    if top:
        s1 = top[0]["score"]
        gaps = [round(s1 - t["score"], 1) for t in top[1:]]
    return {"rl_blend": float(weight), "top": top, "gaps": gaps}


def compute_variants(inputs: dict, weights, top_n: int = SHADOW_TOP_N,
                     should_stop: Optional[Callable[[], bool]] = None) -> tuple:
    """重みごとに加点以降を再計算する (純粋。should_stop は協調的な打ち切りの問い合わせ)。
    戻り値: (variants のリスト, 打ち切ったか)。重みの区切りごとに should_stop を見る"""
    out = []
    for w in weights:
        if should_stop is not None and should_stop():
            return out, True
        acts = rescore_actions(inputs["actions"], inputs.get("rl_hint"),
                               inputs.get("move_type_mult") or {}, float(w))
        out.append(variant_summary(acts, w, top_n))
    return out, False


def best_changes(variants: list, base_weight: float) -> dict:
    """重みごとの第一候補と、基準の重みの第一候補から変わった重みの一覧 (純粋)"""
    best = {}
    for v in variants:
        best[v["rl_blend"]] = _action_key(v["top"][0]) if v["top"] else None
    base = best.get(float(base_weight))
    changed = [w for w, k in best.items() if base is not None and k != base]
    return {"base_best": base, "changed_weights": changed, "best_changed": bool(changed)}


# ------------------------------------------------------------------
# 仕事と行
# ------------------------------------------------------------------
@dataclass
class ShadowJob:
    advice_id: str
    state_id: Optional[str]
    inputs: dict
    t_gen: float                         # 生成時刻 (ワーカーの時計 = time.monotonic)
    turn: Optional[int] = None
    shown_best: Optional[str] = None     # 表示した助言の第一候補 ("kind:id"。ヒステリシス後)
    meta: dict = field(default_factory=dict)


def make_job(common: Optional[dict], advice_id: Optional[str], state_id: Optional[str],
             shown_best: Optional[dict] = None, clock: Callable[[], float] = time.monotonic,
             meta: Optional[dict] = None) -> Optional[ShadowJob]:
    """助言の直後 (表示の後) に呼ぶ。共通部分が無い・評価不能・advice_id が無いなら None"""
    if not common or not common.get("ok") or not advice_id:
        return None
    inputs = snapshot_inputs(common)
    return ShadowJob(advice_id=str(advice_id), state_id=state_id, inputs=inputs, t_gen=clock(),
                     turn=(inputs.get("context") or {}).get("turn"),
                     shown_best=_action_key(shown_best), meta=dict(meta or {}))


def _rl_top(inputs: dict) -> list:
    return [{"label": t.get("label"), "prob": t.get("prob")}
            for t in ((inputs.get("rl_hint") or {}).get("top") or [])]


def variant_record(job: ShadowJob, variants: list, base_weight: float, weights,
                   queue_ms: float, compute_ms: float) -> dict:
    """計算できた仕事の advice_variant 行 (純粋)"""
    rec = {"type": RECORD_TYPE, "advice_id": job.advice_id, "state_id": job.state_id, "turn": job.turn,
           "skipped": None, "base_weight": float(base_weight), "weights": [float(w) for w in weights],
           "variants": variants, "shown_best": job.shown_best, "rl_top": _rl_top(job.inputs),
           "context": job.inputs.get("context") or {},
           "queue_ms": round(queue_ms, 1), "compute_ms": round(compute_ms, 2)}
    rec.update(best_changes(variants, base_weight))
    if job.meta:
        rec["meta"] = job.meta
    return rec


def skip_record(job: ShadowJob, kind: str, reason: str, age_ms: float) -> dict:
    """スキップした仕事の advice_variant 行 (純粋)。kind = dropped_pending / aborted_running"""
    rec = {"type": RECORD_TYPE, "advice_id": job.advice_id, "state_id": job.state_id, "turn": job.turn,
           "skipped": kind, "skip_reason": reason, "age_ms": round(age_ms, 1),
           "context": job.inputs.get("context") or {}}
    if job.meta:
        rec["meta"] = job.meta
    return rec


# ------------------------------------------------------------------
# ワーカー (スレッド 1 本)
# ------------------------------------------------------------------
class ShadowWorker:
    """影の計算のワーカー。submit は表示の後に呼ぶ (呼び出し側のスレッドで即座に戻る)。

    sink(record) に advice_variant 行を渡す (計算できた行もスキップの行も)。sink はワーカーのスレッドからも
    submit を呼んだスレッドからも呼ばれる。
    """

    def __init__(self, sink: Callable[[dict], None], weights=SHADOW_RL_BLEND_WEIGHTS,
                 top_n: int = SHADOW_TOP_N, queue_max: int = SHADOW_QUEUE_MAX,
                 deadline_sec: float = SHADOW_DEADLINE_SEC, base_weight: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._sink = sink
        self.weights = tuple(float(w) for w in weights)
        self.top_n = int(top_n)
        self.queue_max = max(1, int(queue_max))
        self.deadline_sec = float(deadline_sec)
        self.base_weight = float(RL_BLEND_DEFAULT if base_weight is None else base_weight)
        self._clock = clock
        self._cond = threading.Condition()
        self._pending: deque = deque()
        self._running: Optional[ShadowJob] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self.stats = {"submitted": 0, "done": 0, "changed": 0,
                      SKIP_DROPPED: {}, SKIP_ABORTED: {}, "sink_errors": 0}

    # -- 公開 --------------------------------------------------------
    def submit(self, job: Optional[ShadowJob]) -> None:
        if job is None:
            return
        drops = []
        with self._cond:
            if self._stop:
                drops.append((job, SKIP_DROPPED, REASON_STOPPED))
            else:
                now = self._clock()
                keep: deque = deque()
                for j in self._pending:
                    if j.state_id != job.state_id:
                        drops.append((j, SKIP_DROPPED, REASON_STATE_CHANGED))
                    elif now - j.t_gen > self.deadline_sec:
                        drops.append((j, SKIP_DROPPED, REASON_EXPIRED))
                    else:
                        keep.append(j)
                keep.append(job)
                while len(keep) > self.queue_max:
                    drops.append((keep.popleft(), SKIP_DROPPED, REASON_QUEUE_FULL))
                self._pending = keep
                self.stats["submitted"] += 1
                self._ensure_thread()
                self._cond.notify_all()
        for j, kind, reason in drops:
            self._skip(j, kind, reason)

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """待機中・実行中の仕事が無くなるまで待つ (テストと負荷確認用)。戻り値: 空になったか"""
        end = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._pending or self._running is not None:
                rest = None if end is None else end - time.monotonic()
                if rest is not None and rest <= 0:
                    return False
                self._cond.wait(rest)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """ワーカーを止める。待機中の仕事は dropped_pending (stopped)、実行中は区切りで aborted_running (stopped)"""
        with self._cond:
            self._stop = True
            rest = list(self._pending)
            self._pending.clear()
            self._cond.notify_all()
            th = self._thread
        for j in rest:
            self._skip(j, SKIP_DROPPED, REASON_STOPPED)
        if th is not None:
            th.join(timeout)

    # -- 内部 --------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="shadow-variants", daemon=True)
            self._thread.start()

    def _emit(self, rec: dict) -> None:
        try:
            self._sink(rec)
        except Exception:
            self.stats["sink_errors"] += 1

    def _skip(self, job: ShadowJob, kind: str, reason: str) -> None:
        with self._cond:
            d = self.stats[kind]
            d[reason] = d.get(reason, 0) + 1
        self._emit(skip_record(job, kind, reason, (self._clock() - job.t_gen) * 1000.0))

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._stop:
                    self._cond.wait()
                if not self._pending:
                    return
                job = self._pending.popleft()
                self._running = job
            try:
                self._run_one(job)
            finally:
                with self._cond:
                    self._running = None
                    self._cond.notify_all()

    def _run_one(self, job: ShadowJob) -> None:
        start = self._clock()
        if start - job.t_gen > self.deadline_sec:
            self._skip(job, SKIP_DROPPED, REASON_EXPIRED)
            return
        deadline_at = job.t_gen + self.deadline_sec
        stopped = {"reason": REASON_DEADLINE}

        def should_stop() -> bool:
            if self._stop:
                stopped["reason"] = REASON_STOPPED
                return True
            return self._clock() > deadline_at

        try:
            variants, aborted = compute_variants(job.inputs, self.weights, self.top_n, should_stop)
        except Exception:
            self._skip(job, SKIP_ABORTED, REASON_ERROR)
            return
        if aborted:
            self._skip(job, SKIP_ABORTED, stopped["reason"])
            return
        end = self._clock()
        rec = variant_record(job, variants, self.base_weight, self.weights,
                             queue_ms=(start - job.t_gen) * 1000.0, compute_ms=(end - start) * 1000.0)
        with self._cond:
            self.stats["done"] += 1
            if rec.get("best_changed"):
                self.stats["changed"] += 1
        self._emit(rec)
