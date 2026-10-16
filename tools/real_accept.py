"""実機の受入の道具 (2026-10-16、docs/ADVICE_ACCURACY_PLAN_1009.md §8.22・§8.25): 実戦ログの同じ局面を両方式で再生する。

    python -m tools.real_accept [--target LOG ... | --logs N | --log PATH ...] [--out DIR] [--gap-sec S] [--dry-run] [--profile]

実戦ログ (logs/battles/*.jsonl) の advice 行 (kind = battle、確定前 provisional を除く。state が助言エンジンの入力) を、
tools.advice_replay と同じ規則 (DECISION_GAP_SEC の間引き) で決定点にし、復元した状態 (tools.advice_replay.compact_to_engine_state、
move_locks があれば技の固定も戻す) を**同じ入力で** legacy (RL 加点は本番と同じ既定値) と transition の両方で
advisor.engine.evaluate(..., scoring_mode=...) に通す。図鑑・resolver・登録チーム (config/my_team.json) は本番と同じものを読む。
本番の助言 (advisor.service.Advisor.advise) の据え置き・安定化ゲートは通さない (状態を持つため。決定点ごとの evaluate の結果を比べる)。

決定ごとの検査 (両方式それぞれ。check_best):
  (a) 第一候補が合法か: 技は state の場の個体の技 (読めた技が 4 つ未満なら登録の技で補う)・PP・ひんし・技の固定 (move_locks の
      アンコール / こだわり / ちょうはつ / かなしばり)、交代は party にあるか・場の個体でないか・ひんしでないか・選出済みか。
      候補 (actions) に未選出の個体への交代が入っても違反 (sim_accept の unpicked_candidate と同じ)
  (b) 個体・形態の取り違え: 交代先の id が party の id と一致するか (形態違いだけ一致なら switch_form)、助言の場の個体
      (position_key) が state の場の個体と同じ個体で、メガ形態の表記が state (mega の印) と一致するか
  (c) 古い局面: advice 行の hp_stale (場の HP を最後に読めてからの秒数) が REAL_ACCEPT_STALE_HP_SEC を超える、または助言の直前の
      scene 行が解決側の場面 (battle_hud / field = 決定が解決へ進んだ後)。有効な助言に数えず別に集計する
  (d) 両方式の第一候補の一致 / 不一致、(e) transition の退避 (理由別、予算超過は別に数える)、(f) evaluate の所要時間 (p50 / p95 と差)
採点方式の良し悪し (勝率) は判定しない。

決定開始 → 有効な助言の表示の遅延 (display_rows) は再生では測れないので、実戦ログの decision 行・scene 行・display 行から
**その接続テストで使った方式** (下記の 2026-10-16 の修正を参照) について集計し、別の表にする:
  決定開始 = 決定 (t_decided) の直前の決定画面の続き (決定画面と情報確認の画面だけが続く scene 行の並び) の最初の scene 行の時刻
  (サーバーが認識した時刻。前の決定の解決後の演出中に決定画面へ一瞬誤分類された行を決定開始にしないため。決定の途中で解決側の
  場面へ誤分類された行があると、そこから後を決定開始にする = 遅延を短めに測る)、
  有効な助言 = 決定開始 〜 t_decided に生成された確定の助言で、古い局面でないもの、表示 = display 行 (隠れたページでない) の受信時刻。
  以前の助言が画面に残っていただけの決定は遅延 0 とせず「生成なし」、hidden は別集計、決定開始を捉えられない決定は判定不能
  (どちらも成功に数えない)。

2026-10-16 レビュー (§8.28) の修正:
  - 期限内 = 表示時刻 ≤ min(決定開始 + 期限, 決定時刻)。決定の後に表示されたものは「決定後の表示」(after_decision) として
    別に数え、遅延の分布にも期限内にも入れない (分母には残す)
  - 表示の遅延の方式は、その決定時のサーバー設定 (直前の version 行の scoring_mode。advice 行の記録と突き合わせ、食い違えば注記)。
    助言なしの決定もその方式の分母に残す。どちらにも記録が無ければ「不明」(legacy に寄せない)
  - 正しさは検査ごとに 成功 / 違反 / 判定不能 を数え (check_best(detail=True) の checks)、判定不能が 1 件でも残れば
    総合は「判定不能 (照合できた範囲で違反 0)」。満たす は全決定で全検査が照合できて違反 0 のときだけ
  - 遅延の判定は --target で事前に決めた接続テスト群について、各回 (ログごと) と合算の両方に基準を当てる。1 回でも超えたら
    合算も 満たさない (「未達 (原因確認まで保留)」)。対象群の served_commit / version_id / scoring_mode が揃わなければ判定不能
    (served_commit は version 行に無ければ client 行から取る)。再生の evaluate の増分は参考値のまま
  - --profile: evaluate の内訳 (cProfile の累積時間の上位 REAL_ACCEPT_PROFILE_TOP 行) を方式ごとに出す (採点は変えない)

出力: logs/real_accept/<日時>/result.json と report.md (条件・正しさの違反・一致率・退避・所要時間・古い局面・受入条件との照合)。
受入条件 (champions_agent.config の REAL_ACCEPT_*) との照合は 満たす / 満たさない / 判定不能。10 fps の録画再生での処理率
(負荷) はこの道具では測らない (tools.shadow_load_check --compare-scoring)。

純粋な部分 (thin_by_gap / stale_reasons / check_best / decision_mode / display_rows / summarize / log_versions /
target_consistency / acceptance / render_markdown) は
tests/test_real_accept.py でテストする。
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from battle_logger import DECISION_INFO_SCENES, DECISION_RESOLVE_SCENES, DECISION_SCENES
from champions_agent.config import (
    ADVICE_SCORING_MODES, REAL_ACCEPT_DISPLAY_DEADLINE_SEC, REAL_ACCEPT_DISPLAY_IN_TIME_MIN, REAL_ACCEPT_FALLBACK_ALL_MAX,
    REAL_ACCEPT_FALLBACK_BUDGET_MAX, REAL_ACCEPT_LATENCY_DELTA_SEC, REAL_ACCEPT_LATENCY_P95_SEC, REAL_ACCEPT_PROFILE_TOP,
    REAL_ACCEPT_STALE_HP_SEC, REAL_ACCEPT_VIOLATIONS_MAX)
from tools.advice_replay import DECISION_GAP_SEC
from tools.decision_audit import _display_index
from tools.shadow_load_check import percentiles

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "logs" / "battles"
OUT_DIR = REPO / "logs" / "real_accept"
MODES = tuple(ADVICE_SCORING_MODES)          # ("legacy", "transition")
BUDGET_REASON = "budget"                     # advisor.scoring_transition の予算超過の退避の理由
EXAMPLES_PER_KEY = 5                         # 違反・未確認の例を項目ごとに残す数

OK, NG, UNKNOWN = "満たす", "満たさない", "判定不能"
# 正しさ: 違反 0 だが照合できない検査が残る (2026-10-16 レビュー。「満たす」は全決定で全検査が照合できて違反 0 のときだけ)
PARTIAL = "判定不能 (照合できた範囲で違反 0)"
# 遅延: どれか 1 回でも基準を超えた (合算で隠さない。状態は 満たさない のまま、注記の先頭に付ける)
PENDING_NOTE = "未達 (原因確認まで保留)"
UNKNOWN_MODE = "unknown"                     # 方式不明 (version 行にも advice 行にも記録が無い決定。legacy に寄せない)
MODE_LABELS = {UNKNOWN_MODE: "不明"}

# (a) 合法性
LEGALITY_LABELS = {
    "move_not_owned": "第一候補の技が場の個体の技に無い",
    "move_pp0": "第一候補の技の PP が 0",
    "move_while_fainted": "場の個体がひんしなのに第一候補が技",
    "move_locked": "技の固定 (アンコール / こだわり) に反する第一候補",
    "move_taunted": "ちょうはつ中に変化技",
    "move_disabled": "かなしばりの技",
    "switch_self": "場の個体への交代",
    "switch_fainted": "ひんしの個体への交代",
    "switch_unpicked": "未選出の個体への交代 (第一候補)",
    "candidate_unpicked": "候補に未選出の個体への交代",
}
# (b) 個体・形態の取り違え
IDENTITY_LABELS = {
    "switch_missing": "交代先が party に無い",
    "switch_form": "交代先の形態の表記が party と違う",
    "active_mismatch": "助言の場の個体が state の場の個体と別",
    "active_form": "助言の場の個体のメガ形態の表記が state と違う",
}
VIOLATION_LABELS = {**LEGALITY_LABELS, **IDENTITY_LABELS}
# 照合できなかった項目 (違反に数えない。件数だけ出す)
UNCONFIRMED_LABELS = {
    "no_best": "第一候補なし (評価できない・候補が無い)",
    "no_active": "state の場の個体が分からない",
    "moves_unknown": "技が読めず登録も無い (技の照合なし)",
    "moves_from_registration": "第一候補の技は登録からの補完 (画面では未確認)",
    "selection_unknown": "選出が分からない (未選出の照合なし)",
    "lock_unknown": "技の固定の対象の技が分からない",
    "no_position_key": "助言に場の個体の鍵 (position_key) が無い",
}
# 検査ごとの照合の結果 (成功 ok / 違反 ng / 判定不能 unknown。第一候補の種類に関係しない検査は None で数えない)
CHECK_LABELS = {
    "best": "第一候補がある",
    "move_owned": "技の所有", "move_pp": "技の PP", "move_fainted": "場の個体がひんしでない",
    "move_lock": "技の固定 (アンコール / こだわり)", "move_taunt": "ちょうはつ", "move_disable": "かなしばり",
    "switch_party": "交代先が party にある", "switch_form": "交代先の形態", "switch_self": "交代先が場の個体でない",
    "switch_fainted": "交代先がひんしでない", "switch_picked": "交代先が選出済み", "candidate_picked": "候補の交代先が選出済み",
    "active_identity": "場の個体の一致 (個体・メガ形態)",
}
CHECK_RESULTS = ("ok", "ng", "unknown")
STALE_LABELS = {"hp_stale": "場の HP の読みが古い (hp_stale > REAL_ACCEPT_STALE_HP_SEC)",
                "resolve_scene": "解決側の場面 (battle_hud / field) の後の助言"}
DISPLAY_STATUS_LABELS = {
    "shown": "有効な助言が表示された", "hidden": "隠れたページ (別集計)", "no_advice": "決定の間に助言の生成なし (前の助言が残っていただけ)",
    "stale_only": "決定の間の助言が全部古い局面", "not_shown": "表示の記録なし", "no_open": "決定開始を捉えられない (判定不能)",
    "after_decision": "決定後の表示 (遅延・期限内に入れない)",
}


# ------------------------------------------------------------------ 純粋: 決定点・古い局面
def thin_by_gap(rows: list, gap_sec: float) -> list:
    """advice 行の間引き (純粋。tools.advice_replay.extract_decisions と同じ規則): 前に採った行から gap_sec 秒未満の行は捨てる"""
    out, last_t = [], -1e18
    for r in rows:
        t = float(r.get("t") or 0.0)
        if t - last_t < gap_sec:
            continue
        last_t = t
        out.append(r)
    return out


def battle_advice_rows(records: list) -> list:
    """再生の対象の advice 行 (純粋): kind = battle、確定前 (provisional) でない、評価できた (ok)、state に自分の party がある"""
    out = []
    for r in records:
        if r.get("type") != "advice" or r.get("kind") != "battle":
            continue
        adv = r.get("advice") or {}
        if adv.get("provisional") or adv.get("ok") is False:
            continue
        st = r.get("state") or {}
        if not ((st.get("player") or {}).get("party")):
            continue
        out.append(r)
    return out


def scene_timeline(records: list) -> list:
    """scene 行の (時刻, 場面) の列 (時刻順。純粋)"""
    return sorted((float(r["t"]), r.get("scene")) for r in records if r.get("type") == "scene" and r.get("t") is not None)


def scene_before(timeline: list, t: float) -> Optional[str]:
    """時刻 t 以前の最後の scene 行の場面 (純粋。無ければ None)"""
    cur = None
    for ts, sc in timeline:
        if ts > t:
            break
        cur = sc
    return cur


def decision_open_time(timeline: list, prev_t: float, t_dec: float) -> Optional[float]:
    """決定開始の時刻 (純粋): prev_t < 時刻 ≤ t_dec の scene 行のうち最後の決定画面 (DECISION_SCENES) から遡り、決定画面と
    情報確認の画面 (DECISION_INFO_SCENES) だけが続く並びの最初の決定画面の時刻。決定画面が無ければ None"""
    win = [(ts, sc) for ts, sc in timeline if prev_t < ts <= t_dec]
    last = max((i for i, (_ts, sc) in enumerate(win) if sc in DECISION_SCENES), default=None)
    if last is None:
        return None
    start = win[last][0]
    for ts, sc in reversed(win[:last]):
        if sc in DECISION_SCENES:
            start = ts
        elif sc in DECISION_INFO_SCENES:
            continue
        else:
            break
    return start


def stale_reasons(row: dict, prev_scene: Optional[str], stale_hp_sec: float = REAL_ACCEPT_STALE_HP_SEC) -> list:
    """advice 行が古い局面への助言かの理由 (純粋。空なら古くない): hp_stale (場の HP の読みが stale_hp_sec より古い) /
    resolve_scene (助言の直前の scene 行が解決側の場面)。hp_stale が読めていない (None) 側は古いと数えない"""
    out = []
    hs = row.get("hp_stale") or {}
    vals = [float(v) for v in hs.values() if v is not None]
    if vals and max(vals) > stale_hp_sec:
        out.append("hp_stale")
    if prev_scene in DECISION_RESOLVE_SCENES:
        out.append("resolve_scene")
    return out


# ------------------------------------------------------------------ 純粋: 第一候補の検査
def _fainted(m: Optional[dict]) -> bool:
    if not m:
        return False
    hp = m.get("hp")
    return m.get("status") == "fainted" or (hp is not None and float(hp) <= 0)


def _key(a: Optional[dict]) -> Optional[tuple]:
    return (a.get("kind"), a.get("id")) if a and a.get("kind") else None


def _identity(x: str) -> str:
    return x


def _hp_known(m: Optional[dict]) -> bool:
    """ひんしかどうかを照合できるか (HP か状態異常の記録がある)"""
    return bool(m) and (m.get("hp") is not None or bool(m.get("status")))


def check_best(adv: Optional[dict], compact: dict, move_locks: Optional[dict] = None, registered: Iterable = (),
               is_status_move: Callable = lambda mid: False, is_mega: Callable = lambda sid: False,
               base_of: Callable = _identity, choice_items: Iterable = (), detail: bool = False) -> dict:
    """1 決定の第一候補の検査 (純粋)。adv = evaluate の戻り値 (best / actions / position_key)、compact = advice 行の簡約状態
    (battle_logger._compact_state)、move_locks = advice 行の move_locks (None = 欄の無い行。技の固定・かなしばりは照合できない)、
    registered = 場の個体の登録の技の id (読めた技が 4 つ未満のときの補完に使う)。is_status_move(技 id) = 変化技か、
    is_mega(種族 id) = メガ形態の id か、base_of(種族 id) = 同じ個体とみなす基本の id (メガ・戦闘中の形態変化を落とす)、
    choice_items = こだわり系の持ち物 id。
    戻り値 {"violations": [VIOLATION_LABELS の鍵], "unconfirmed": [UNCONFIRMED_LABELS の鍵]}。detail = True なら
    "checks": {CHECK_LABELS の鍵: "ok" / "ng" / "unknown" / None (この第一候補には関係しない)} も付ける (2026-10-16 レビュー:
    判定不能を合格にしないため、検査ごとに 成功 / 違反 / 判定不能 を分ける)"""
    viol, unconf = [], []
    checks: dict = {k: None for k in CHECK_LABELS}
    adv = adv or {}
    best = adv.get("best") if adv.get("ok", True) else None
    pl = compact.get("player") or {}
    party = pl.get("party") or []
    idx = pl.get("active")
    me = party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else None
    picked_known = any(m.get("picked") for m in party)

    def out():
        r = {"violations": list(dict.fromkeys(viol)), "unconfirmed": list(dict.fromkeys(unconf))}
        if detail:
            r["checks"] = checks
        return r

    def same(a, b) -> bool:
        return bool(a) and bool(b) and base_of(a) == base_of(b)

    def find(sid):
        """(party の index, 一致の種類 exact / form) or (None, None)"""
        for i, m in enumerate(party):
            if m.get("species") == sid:
                return i, "exact"
        for i, m in enumerate(party):
            if same(m.get("species"), sid):
                return i, "form"
        return None, None

    if not best or not best.get("kind"):
        unconf.append("no_best")
        checks["best"] = "unknown"
        return out()
    checks["best"] = "ok"
    if best["kind"] == "move":
        mid = best.get("id")
        if me is None:
            unconf.append("no_active")
            for k in ("move_owned", "move_pp", "move_fainted", "move_lock", "move_taunt", "move_disable"):
                checks[k] = "unknown"
        else:
            if _fainted(me):
                viol.append("move_while_fainted")
                checks["move_fainted"] = "ng"
            else:
                checks["move_fainted"] = "ok" if _hp_known(me) else "unknown"
            slots = [(mv[0], mv[1] if len(mv) > 1 else None) for mv in (me.get("moves") or [])
                     if isinstance(mv, (list, tuple)) and mv and mv[0]]
            seen = {m for m, _ in slots}
            owned = set(seen)
            if len(slots) < 4:
                owned |= set(registered or ())
            if not owned:
                unconf.append("moves_unknown")
                checks["move_owned"] = "unknown"
            elif mid not in owned:
                viol.append("move_not_owned")
                checks["move_owned"] = "ng"
            elif mid not in seen:
                unconf.append("moves_from_registration")
                checks["move_owned"] = "unknown"
            else:
                checks["move_owned"] = "ok"
            pps = [pp for m, pp in slots if m == mid]
            if any(pp == 0 for pp in pps):
                viol.append("move_pp0")
                checks["move_pp"] = "ng"
            elif pps and all(pp is not None for pp in pps):
                checks["move_pp"] = "ok"
            else:
                checks["move_pp"] = "unknown"           # 画面で読めていない技 / PP が読めていない
            locks = move_locks or {}
            row = locks.get("player") or {}
            vols = {str(v).lower() for v in (row.get("volatiles") or [])}
            last = (locks.get("last_move") or {}).get("player")
            locked = False
            lock_unknown = False
            if "encore" in vols:
                lm = row.get("encore_move") or last
                if lm and lm in owned:
                    locked = locked or mid != lm
                else:
                    unconf.append("lock_unknown")
                    lock_unknown = True
            if me.get("item") in set(choice_items or ()) and last:
                if last in owned:
                    locked = locked or mid != last
                else:
                    unconf.append("lock_unknown")
                    lock_unknown = True
            if locked:
                viol.append("move_locked")
                checks["move_lock"] = "ng"
            else:
                checks["move_lock"] = "unknown" if (lock_unknown or move_locks is None) else "ok"
            if "taunt" in vols and is_status_move(mid):
                viol.append("move_taunted")
                checks["move_taunt"] = "ng"
            else:
                # 変化技でなければ ちょうはつ に関係なく合法。変化技で move_locks の欄が無ければ照合できない
                checks["move_taunt"] = "unknown" if (move_locks is None and is_status_move(mid)) else "ok"
            if f"disable_{mid}" in vols:
                viol.append("move_disabled")
                checks["move_disable"] = "ng"
            else:
                checks["move_disable"] = "unknown" if move_locks is None else "ok"
    elif best["kind"] == "switch":
        ti, how = find(best.get("id"))
        if ti is None:
            viol.append("switch_missing")
            checks["switch_party"] = "ng"
        else:
            checks["switch_party"] = "ok"
            if how == "form":
                viol.append("switch_form")
                checks["switch_form"] = "ng"
            else:
                checks["switch_form"] = "ok"
            t = party[ti]
            if ti == idx:
                viol.append("switch_self")
                checks["switch_self"] = "ng"
            else:
                checks["switch_self"] = "ok" if isinstance(idx, int) else "unknown"
            if _fainted(t):
                viol.append("switch_fainted")
                checks["switch_fainted"] = "ng"
            else:
                checks["switch_fainted"] = "ok" if _hp_known(t) else "unknown"
            if picked_known and not t.get("picked"):
                viol.append("switch_unpicked")
                checks["switch_picked"] = "ng"
            else:
                checks["switch_picked"] = "ok" if picked_known else "unknown"
    if not picked_known:
        unconf.append("selection_unknown")
        if any(a and a.get("kind") == "switch" for a in [best] + list(adv.get("actions") or [])):
            checks["candidate_picked"] = "unknown"
    else:
        checks["candidate_picked"] = "ok"
        for a in [best] + list(adv.get("actions") or []):
            if not a or a.get("kind") != "switch":
                continue
            ci, _how = find(a.get("id"))
            if ci is not None and not party[ci].get("picked"):
                viol.append("candidate_unpicked")
                checks["candidate_picked"] = "ng"
                break
    # 助言の場の個体 (position_key の ("my", 枠, 現在形態の id, ひんし))
    pk = adv.get("position_key")
    my = pk[0] if isinstance(pk, (list, tuple)) and pk and isinstance(pk[0], (list, tuple)) and len(pk[0]) >= 3 else None
    if my is None:
        unconf.append("no_position_key")
        checks["active_identity"] = "unknown"
    elif me is not None:
        sp = my[2]
        checks["active_identity"] = "ok"
        if my[1] != idx or (sp and not same(sp, me.get("species"))):
            viol.append("active_mismatch")
            checks["active_identity"] = "ng"
        elif sp and bool(is_mega(sp)) != (bool(me.get("mega")) or bool(is_mega(me.get("species")))):
            viol.append("active_form")
            checks["active_identity"] = "ng"
    else:
        checks["active_identity"] = "unknown"
    return out()


# ------------------------------------------------------------------ 純粋: 実戦ログの表示の遅延
def advice_mode(rec: Optional[dict]) -> tuple:
    """advice 行の「使った方式」(設定された方式) と記録の有無 (純粋): (方式, 記録あり)。
    top-level の scoring_mode (2026-10-16 以降) → advice の scoring_mode (M2b 以降) の順。どちらも無ければ既定の legacy (記録なし)"""
    rec = rec or {}
    m = rec.get("scoring_mode") or (rec.get("advice") or {}).get("scoring_mode")
    return (m, True) if m else ("legacy", False)


def decision_mode(version: Optional[dict], adv_rec: Optional[dict], unrecorded_mode: Optional[str] = UNKNOWN_MODE) -> dict:
    """1 決定の方式 (純粋。2026-10-16 レビュー): その決定時のサーバー設定 = 直前の version 行の scoring_mode から取る (advice 行が
    無い決定 = 助言の生成なしもその方式の分母に残す)。advice 行に方式の記録があれば突き合わせ、食い違えば mismatch。
    version 行に記録が無ければ advice 行の記録、どちらにも無ければ unrecorded_mode (既定 UNKNOWN_MODE = 方式不明、legacy に寄せない)。
    戻り値 {"mode", "recorded" (どちらかに記録あり), "source" ("version" / "advice" / None), "mismatch", "advice_mode"}"""
    vm = (version or {}).get("scoring_mode")
    am, a_rec = advice_mode(adv_rec) if adv_rec else (None, False)
    am = am if a_rec else None
    if vm:
        return {"mode": vm, "recorded": True, "source": "version", "mismatch": bool(am and am != vm), "advice_mode": am}
    if am:
        return {"mode": am, "recorded": True, "source": "advice", "mismatch": False, "advice_mode": am}
    return {"mode": unrecorded_mode, "recorded": False, "source": None, "mismatch": False, "advice_mode": None}


def display_rows(records: list, deadline_sec: float = REAL_ACCEPT_DISPLAY_DEADLINE_SEC,
                 stale_hp_sec: float = REAL_ACCEPT_STALE_HP_SEC, unrecorded_mode: Optional[str] = UNKNOWN_MODE) -> list:
    """decision 行ごとの「決定開始 → 有効な助言の表示」(純粋)。戻り値の各行:
    {"advice_id", "turn", "mode", "mode_recorded", "mode_source", "mode_mismatch", "t_open", "t_decided",
     "status" (DISPLAY_STATUS_LABELS の鍵), "latency" (秒、status = shown のときだけ),
     "in_time" (表示時刻 ≤ min(t_open + deadline_sec, t_decided))、"gen_to_shown" (decision 行の t_shown − t_gen)}。
    表示の時刻はサーバーの受信時刻 (display 行の t。無ければ t_shown。tools.decision_audit と同じ)。
    決定 (t_decided) の後に表示されたものは status = after_decision (遅延の分布にも期限内にも入れない、分母には残す)。
    方式は decision_mode (直前の version 行 → advice 行)。どちらにも記録が無い決定は unrecorded_mode
    (既定 UNKNOWN_MODE = 方式不明。記録が無いものを legacy と推定する互換は残さない。2026-10-16 レビュー)"""
    timeline = scene_timeline(records)
    disp = _display_index(records)
    advs = [r for r in records if r.get("type") == "advice" and r.get("kind") == "battle" and r.get("advice_id")]
    by_id = {r["advice_id"]: r for r in advs}
    out = []
    prev_t = -1e18
    version = None
    for rec in records:
        if rec.get("type") == "version":
            version = rec
            continue
        if rec.get("type") != "decision":
            continue
        d = rec
        t_dec = d.get("t_decided")
        if t_dec is None:
            continue
        t_dec = float(t_dec)
        t_open = decision_open_time(timeline, prev_t, t_dec)
        dm = decision_mode(version, by_id.get(d.get("advice_id")), unrecorded_mode)
        row = {"advice_id": d.get("advice_id"), "turn": d.get("turn"), "mode": dm["mode"], "mode_recorded": dm["recorded"],
               "mode_source": dm["source"], "mode_mismatch": dm["mismatch"],
               "t_open": t_open, "t_decided": t_dec, "status": None, "latency": None, "in_time": False,
               "gen_to_shown": (round(float(d["t_shown"]) - float(d["t_gen"]), 3)
                                if d.get("t_shown") is not None and d.get("t_gen") is not None else None)}
        if t_open is None:
            row["status"] = "no_open"
        else:
            cands = []
            for r in advs:
                a = r.get("advice") or {}
                tg = a.get("t_gen", r.get("t"))
                if tg is None or not (t_open <= float(tg) <= t_dec) or a.get("provisional") or a.get("ok") is False:
                    continue
                if not (a.get("best") or a.get("actions")):
                    continue
                cands.append(r)
            valid = [r for r in cands if not stale_reasons(r, None, stale_hp_sec)]
            shown = []
            for r in valid:
                s = disp["shown"].get(r["advice_id"])
                if s is not None:
                    t_shown, t_recv = s
                    shown.append(t_recv if t_recv is not None else t_shown)
            before = [x for x in shown if x <= t_dec]
            if before:
                first = min(before)
                row["status"] = "shown"
                row["latency"] = round(max(0.0, first - t_open), 3)
                row["in_time"] = first <= min(t_open + deadline_sec, t_dec)
            elif shown:
                row["status"] = "after_decision"
            elif any(r["advice_id"] in disp["hidden"] for r in cands):
                row["status"] = "hidden"
            elif not cands:
                row["status"] = "no_advice"
            elif not valid:
                row["status"] = "stale_only"
            else:
                row["status"] = "not_shown"
        out.append(row)
        prev_t = t_dec
    return out


def _display_block(rs: list) -> dict:
    front = [r for r in rs if r["status"] != "hidden"]
    lat = [r["latency"] * 1000.0 for r in rs if r["status"] == "shown"]
    g2s = [r["gen_to_shown"] * 1000.0 for r in rs if r.get("gen_to_shown") is not None]
    n_in = sum(1 for r in front if r["in_time"])
    return {"n": len(rs), "n_front": len(front), "n_mode_unrecorded": sum(1 for r in rs if not r["mode_recorded"]),
            "n_mode_mismatch": sum(1 for r in rs if r.get("mode_mismatch")),
            "mode_sources": {k: sum(1 for r in rs if r.get("mode_source") == k) for k in ("version", "advice")},
            "status": {k: sum(1 for r in rs if r["status"] == k) for k in DISPLAY_STATUS_LABELS},
            "latency_ms": percentiles(lat), "gen_to_shown_ms": percentiles(g2s),
            "n_in_time": n_in, "in_time_rate": (round(n_in / len(front), 4) if front else None)}


def summarize_display(rows: list) -> dict:
    """方式ごとの表示の遅延の集計 (純粋)。分母 (前面の対象決定) = hidden を除く全決定。判定不能・生成なし・決定後の表示は
    成功に数えない。各方式に "by_log": {ログ名: 同じ形の集計} (各回の値。行に log が無ければ鍵は None) を付ける"""
    out = {}
    for mode in sorted({r["mode"] for r in rows}, key=str):
        rs = [r for r in rows if r["mode"] == mode]
        blk = _display_block(rs)
        logs = list(dict.fromkeys(r.get("log") for r in rs))
        blk["by_log"] = {lg: _display_block([r for r in rs if r.get("log") == lg]) for lg in logs}
        out[mode] = blk
    return out


def summarize_display_all(rows: list) -> dict:
    """方式を問わない合算 (参考。過去の値と比べるため。純粋)"""
    return _display_block(rows)


# ------------------------------------------------------------------ 純粋: 対象群の版・設定の照合
CODE_FIELDS = ("served_commit", "version_id", "scoring_mode")


def log_versions(records: list) -> dict:
    """1 本のログの版・設定 (純粋): {"served_commit": [...], "version_id": [...], "scoring_mode": [...], "served_commit_source"}。
    version_id / scoring_mode は version 行から。served_commit は version 行にあればそれ、無ければ client 行 (助言ページが受け取った
    commit) から取り、出どころを served_commit_source ("version" / "client" / None) に書く。記録の無い欄は空の列"""
    vers = [r for r in records if r.get("type") == "version"]
    out = {"version_id": list(dict.fromkeys(r["version_id"] for r in vers if r.get("version_id"))),
           "scoring_mode": list(dict.fromkeys(r["scoring_mode"] for r in vers if r.get("scoring_mode")))}
    sc = list(dict.fromkeys(r["served_commit"] for r in vers if r.get("served_commit")))
    src = "version" if sc else None
    if not sc:
        sc = list(dict.fromkeys(r["served_commit"] for r in records if r.get("type") == "client" and r.get("served_commit")))
        src = "client" if sc else None
    out["served_commit"] = sc
    out["served_commit_source"] = src
    return out


def target_consistency(log_infos: list, fields: tuple = CODE_FIELDS) -> dict:
    """対象群が同じコード・設定か (純粋): log_infos = [{"name", "versions": log_versions の戻り値}]。
    各欄 (CODE_FIELDS) が全ログで記録され、値が 1 種類なら一致。戻り値 {"ok": True / False, "notes": [...], "values": {欄: [値]}}。
    記録の無い欄があれば照合できないので ok = False (判定不能の扱い)"""
    notes, values = [], {}
    ok = bool(log_infos)
    if not log_infos:
        notes.append("対象のログが無い")
    for f in fields:
        missing = [li["name"] for li in log_infos if not li["versions"].get(f)]
        vals = list(dict.fromkeys(v for li in log_infos for v in li["versions"].get(f) or []))
        values[f] = vals
        if missing:
            ok = False
            notes.append(f"{f} の記録なし {len(missing)} 本 ({', '.join(missing[:3])}{' ほか' if len(missing) > 3 else ''})")
        if len(vals) > 1:
            ok = False
            notes.append(f"{f} が揃わない ({' / '.join(str(v)[:12] for v in vals[:4])}{' ほか' if len(vals) > 4 else ''})")
    return {"ok": ok, "notes": notes, "values": values}


# ------------------------------------------------------------------ 純粋: 集計と受入条件
def _rate(a: int, b: int) -> Optional[float]:
    return round(a / b, 4) if b else None


def _reason_key(fb) -> str:
    """退避の理由の分類 (advisor.scoring_transition._reason_key と同じ規則: 未対応の効果は「理由:効果:技」まで、ほかは「:」の前)"""
    key = str(fb).split(":", 2)
    return ":".join(key[:3]) if key[0] in ("unsupported", "opp_unsupported") else key[0]


def summarize(decisions: list, examples_per_key: int = EXAMPLES_PER_KEY) -> dict:
    """再生の決定の列の集計 (純粋)。decisions の各要素は replay の決定の dict:
    {"log", "advice_id", "t", "stale": [...], "log_best", "modes": {方式: {"ok", "best", "violations", "unconfirmed",
     "elapsed_ms", "scoring_used", "fallback_reason", "error", "checks" (任意。check_best(detail=True) の checks)}}}。
    有効 (古くない) な決定と古い局面の決定を分けて数える。退避と一致率は有効な決定で数える。
    照合できない決定 (n_unknown_decisions) = unconfirmed が残る、または checks に unknown がある決定 (checks の無い結果は
    unconfirmed だけで決める)。checks は検査ごとに ok / ng / unknown を数える"""
    valid = [d for d in decisions if not d.get("stale")]
    stale = [d for d in decisions if d.get("stale")]
    out = {"n": len(decisions), "n_valid": len(valid), "n_stale": len(stale),
           "stale_reasons": {k: sum(1 for d in stale if k in d["stale"]) for k in STALE_LABELS}, "modes": {}}
    for mode in MODES:
        mo = {}
        for name, ds in (("valid", valid), ("stale", stale)):
            v = {k: 0 for k in VIOLATION_LABELS}
            u = {k: 0 for k in UNCONFIRMED_LABELS}
            ex: dict = {}
            n_eval, n_err, n_viol_dec, n_unk_dec = 0, 0, 0, 0
            ck = {k: {x: 0 for x in CHECK_RESULTS} for k in CHECK_LABELS}
            for d in ds:
                r = (d.get("modes") or {}).get(mode)
                if r is None:
                    continue
                if r.get("error"):
                    n_err += 1
                    continue
                n_eval += 1
                if r.get("violations"):
                    n_viol_dec += 1
                for k in r.get("violations") or []:
                    v[k] = v.get(k, 0) + 1
                    lst = ex.setdefault(k, [])
                    if len(lst) < examples_per_key:
                        lst.append({"log": d.get("log"), "advice_id": d.get("advice_id"), "turn": d.get("turn"),
                                    "best": r.get("best"), "active": d.get("active")})
                for k in r.get("unconfirmed") or []:
                    u[k] = u.get(k, 0) + 1
                checks = r.get("checks") or {}
                for k, x in checks.items():
                    if x in CHECK_RESULTS:
                        ck.setdefault(k, {y: 0 for y in CHECK_RESULTS})[x] += 1
                if r.get("unconfirmed") or any(x == "unknown" for x in checks.values()):
                    n_unk_dec += 1
            mo[name] = {"n_eval": n_eval, "n_error": n_err, "n_violation_decisions": n_viol_dec,
                        "violations": v, "n_violations": sum(v.values()),
                        "n_legality": sum(v[k] for k in LEGALITY_LABELS), "n_identity": sum(v[k] for k in IDENTITY_LABELS),
                        "unconfirmed": u, "examples": ex, "n_unknown_decisions": n_unk_dec, "checks": ck}
        ms = [float(d["modes"][mode]["elapsed_ms"]) for d in valid + stale
              if (d.get("modes") or {}).get(mode) and d["modes"][mode].get("elapsed_ms") is not None]
        mo["elapsed_ms"] = percentiles(ms)
        out["modes"][mode] = mo
    # (d) 一致 (有効な決定で、両方式とも第一候補があるもの)
    both = [d for d in valid if all(_key(((d.get("modes") or {}).get(m) or {}).get("best")) for m in MODES)]
    agree = [d for d in both if len({_key(d["modes"][m]["best"]) for m in MODES}) == 1]
    out["agreement"] = {"n": len(both), "n_agree": len(agree), "rate": _rate(len(agree), len(both)),
                        "examples": [{"log": d.get("log"), "advice_id": d.get("advice_id"), "turn": d.get("turn"),
                                      **{m: d["modes"][m]["best"] for m in MODES}}
                                     for d in both if d not in agree][:examples_per_key]}
    # (e) 退避 (有効な決定の transition)
    tr = [d["modes"]["transition"] for d in valid if (d.get("modes") or {}).get("transition")
          and not d["modes"]["transition"].get("error") and d["modes"]["transition"].get("ok")]
    by: dict = {}
    for r in tr:
        fb = r.get("fallback_reason")
        if fb:
            k = _reason_key(fb)
            by[k] = by.get(k, 0) + 1
    n_fb = sum(by.values())
    n_budget = by.get(BUDGET_REASON, 0)
    out["fallback"] = {"n": len(tr), "n_fallback": n_fb, "rate": _rate(n_fb, len(tr)),
                       "n_budget": n_budget, "budget_rate": _rate(n_budget, len(tr)),
                       "by_reason": dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))}
    # (f) 所要時間の差 (同じ決定の対: transition − legacy)
    diffs = [float(d["modes"]["transition"]["elapsed_ms"]) - float(d["modes"]["legacy"]["elapsed_ms"])
             for d in valid + stale
             if all(((d.get("modes") or {}).get(m) or {}).get("elapsed_ms") is not None for m in MODES)]
    out["elapsed_diff_ms"] = percentiles(diffs)
    lp, tp = out["modes"]["legacy"]["elapsed_ms"]["p95"], out["modes"]["transition"]["elapsed_ms"]["p95"]
    out["elapsed_p95_delta_ms"] = (round(tp - lp, 1) if lp is not None and tp is not None else None)
    # 復元の確からしさの参考: ログに残った当時の第一候補と、そのログの方式での再生の第一候補が同じか (据え置き・安定化ゲートは通さない)
    same_n = same_k = 0
    for d in valid:
        lb, lm = d.get("log_best"), d.get("log_mode")
        r = (d.get("modes") or {}).get(lm or "legacy") or {}
        if _key(lb) and _key(r.get("best")):
            same_n += 1
            same_k += _key(lb) == _key(r.get("best"))
    out["log_best_match"] = {"n": same_n, "n_match": same_k, "rate": _rate(same_k, same_n)}
    return out


def mode_label(mode) -> str:
    return MODE_LABELS.get(mode, str(mode))


def _correctness_row(v: dict, lim_v) -> tuple:
    """正しさの判定 (純粋): (値, 状態, 注記)。違反が上限を超えれば 満たさない。違反 0 でも照合できない決定が残れば
    PARTIAL (照合できた範囲で違反 0)。満たす は全決定で全検査が照合できて違反 0 のときだけ"""
    if v["n_eval"] == 0:
        return None, UNKNOWN, "評価できた有効な決定が無い"
    base = f"合法性 {v['n_legality']} / 個体・形態 {v['n_identity']} (評価 {v['n_eval']} 決定)"
    if v["n_violations"] > lim_v:
        return v["n_violations"], NG, base
    n_unk = v.get("n_unknown_decisions", 0)
    if n_unk:
        items = [CHECK_LABELS[k] for k, c in (v.get("checks") or {}).items() if c.get("unknown") and k in CHECK_LABELS]
        uitems = [UNCONFIRMED_LABELS[k] for k, n in (v.get("unconfirmed") or {}).items() if n and k in UNCONFIRMED_LABELS]
        what = "、".join(items) if items else "、".join(uitems)
        return v["n_violations"], PARTIAL, (f"照合できた範囲で違反 {v['n_violations']}。照合できない検査が残る決定 {n_unk} / "
                                            f"{v['n_eval']} (項目: {what})。{base}")
    return v["n_violations"], OK, base


def acceptance(summary: dict, display: dict, record_check: dict, limits: Optional[dict] = None,
               group: Optional[dict] = None) -> list:
    """受入条件 (§8.22) との照合 (純粋)。戻り値 [{"item", "mode", "value", "limit", "status" (満たす / 満たさない / 判定不能 /
    PARTIAL), "note"}]。group = 対象群の情報 {"target_fixed": bool, "by_mode": {方式: target_consistency}, "delta":
    target_consistency (served_commit / version_id)}。None なら版・設定の照合をしない (呼び出し側が持たないとき)。
    遅延の p95 は各回 (display[方式]["by_log"]) と合算の両方に基準を当て、どれか 1 回でも超えたら合算も 満たさない
    (注記の先頭に PENDING_NOTE)。対象群の版・設定が揃わなければ、超えた回が無いかぎり 判定不能"""
    lim = {"violations": REAL_ACCEPT_VIOLATIONS_MAX, "p95_sec": REAL_ACCEPT_LATENCY_P95_SEC,
           "delta_sec": REAL_ACCEPT_LATENCY_DELTA_SEC, "in_time": REAL_ACCEPT_DISPLAY_IN_TIME_MIN,
           "budget": REAL_ACCEPT_FALLBACK_BUDGET_MAX, "all": REAL_ACCEPT_FALLBACK_ALL_MAX,
           "deadline_sec": REAL_ACCEPT_DISPLAY_DEADLINE_SEC, **(limits or {})}
    rows = []

    def add(item, mode, value, limit, status, note=""):
        rows.append({"item": item, "mode": mode, "value": value, "limit": limit, "status": status, "note": note})

    for mode in MODES:
        val, st, note = _correctness_row(summary["modes"][mode]["valid"], lim["violations"])
        add("正しさ: 違反 (有効な決定、再生)", mode, val, lim["violations"], st, note)
    fb = summary["fallback"]
    for item, key, rk in (("退避: 予算超過 (transition、再生)", "budget", "budget_rate"),
                          ("退避: 全理由 (transition、再生)", "all", "rate")):
        r = fb[rk]
        add(item, "transition", r, lim[key], UNKNOWN if r is None else (OK if r <= lim[key] else NG),
            f"{fb['n_budget'] if key == 'budget' else fb['n_fallback']} / {fb['n']} 決定")
    tgt_note = ""
    if group is not None and not group.get("target_fixed"):
        tgt_note = "対象は事前に固定していない (--target なし、--logs / --log で選んだログ)。"
    for mode, ds in sorted(display.items(), key=lambda kv: str(kv[0])):
        ml = mode_label(mode)
        lim_ms = lim["p95_sec"] * 1000.0
        over = []
        for lg, b in (ds.get("by_log") or {}).items():
            p = b["latency_ms"]["p95"]
            if p is None:
                st = UNKNOWN
            elif p > lim_ms:
                st = NG
                over.append(f"{lg} {p / 1000.0:.2f} 秒")
            else:
                st = OK
            add("遅延: 決定開始 → 有効な助言の表示の p95 (実戦ログ、回ごと)", f"{ml} / {lg}",
                (None if p is None else round(p / 1000.0, 3)), lim["p95_sec"], st,
                (PENDING_NOTE + "。" if st == NG else "") + f"表示 {b['status']['shown']} / {b['n']} 決定")
        p95 = ds["latency_ms"]["p95"]
        cons = ((group or {}).get("by_mode") or {}).get(mode) if group is not None else None
        notes = [f"表示 {ds['status']['shown']} / {ds['n']} 決定"]
        if p95 is not None and p95 > lim_ms:
            over.insert(0, f"合算 {p95 / 1000.0:.2f} 秒")
        if over:
            st = NG
            notes.insert(0, f"{PENDING_NOTE}: 基準超え {', '.join(over)}")
        elif p95 is None:
            st = UNKNOWN
        elif cons is not None and not cons["ok"]:
            st = UNKNOWN
        else:
            st = OK
        if cons is not None and not cons["ok"]:
            notes.append("対象群の版・設定が揃わない: " + "; ".join(cons["notes"]))
        if mode == UNKNOWN_MODE:
            notes.append("方式不明 (version 行にも advice 行にも記録なし。legacy に寄せない)")
        if ds.get("n_mode_mismatch"):
            notes.append(f"version 行と advice 行の方式の食い違い {ds['n_mode_mismatch']} 決定 (version 行を採用)")
        add("遅延: 決定開始 → 有効な助言の表示の p95 (実戦ログ)", ml, (None if p95 is None else round(p95 / 1000.0, 3)),
            lim["p95_sec"], st, tgt_note + "合算。" + "。".join(notes))
        r = ds["in_time_rate"]
        add(f"表示: 期限 ({lim['deadline_sec']:g} 秒) 内の割合 (実戦ログ、前面の全対象決定)", ml, r, lim["in_time"],
            UNKNOWN if r is None else (OK if r >= lim["in_time"] else NG),
            f"{ds['n_in_time']} / {ds['n_front']} (hidden {ds['status']['hidden']} は別、判定不能 {ds['status']['no_open']}・"
            f"決定後の表示 {ds['status'].get('after_decision', 0)} は成功に数えない。表示時刻 ≤ min(決定開始 + 期限, 決定時刻))")
    ref = summary.get("elapsed_p95_delta_ms")
    ref_note = (f"参考: 再生の evaluate の p95 の差 {ref / 1000.0:+.3f} 秒 (参考値。実機の表示の増分の代わりにしない)"
                if ref is not None else "参考: 再生の evaluate の時間なし")
    if all(m in display and display[m]["latency_ms"]["p95"] is not None for m in MODES):
        d = (display["transition"]["latency_ms"]["p95"] - display["legacy"]["latency_ms"]["p95"]) / 1000.0
        dc = (group or {}).get("delta") if group is not None else None
        if dc is not None and not dc["ok"]:
            add("遅延: legacy より増える分 (実戦ログの p95 の差)", "transition", round(d, 3), lim["delta_sec"], UNKNOWN,
                "両方式の対象群の版が揃わない: " + "; ".join(dc["notes"]) + "。" + ref_note)
        else:
            add("遅延: legacy より増える分 (実戦ログの p95 の差)", "transition", round(d, 3), lim["delta_sec"],
                OK if d <= lim["delta_sec"] else NG, ref_note)
    else:
        add("遅延: legacy より増える分 (実戦ログの p95 の差)", "transition", None, lim["delta_sec"], UNKNOWN,
            "実戦ログに両方式の決定が無い。" + ref_note)
    add("負荷: 10 fps の録画再生の処理率 (legacy 比 10% 以上悪化しない)", "-", None, None, UNKNOWN,
        "この道具では測らない (tools.shadow_load_check --compare-scoring)")
    rc = record_check
    add("記録: 使った方式と退避理由 (version 行・advice 行)", "-", f"{rc['n_logs_ok']} / {rc['n_logs']} ログ", None,
        UNKNOWN if not rc["n_logs"] else (OK if rc["n_logs_ok"] == rc["n_logs"] else NG),
        "欄の無いログは記録の追加 (2026-10-16) より前" if rc["n_logs_ok"] < rc["n_logs"] else "")
    return rows


def record_check_of(logs: list) -> dict:
    """記録の照合 (純粋): logs = [{"name", "records"}]。version 行に scoring_mode があり、battle の advice 行がすべて
    scoring_mode と fallback_reason の欄を持つログを数える"""
    ok = 0
    for lg in logs:
        recs = lg["records"]
        ver = [r for r in recs if r.get("type") == "version"]
        advs = [r for r in recs if r.get("type") == "advice" and r.get("kind") == "battle"]
        if ver and all("scoring_mode" in r for r in ver) and all("scoring_mode" in r and "fallback_reason" in r for r in advs):
            ok += 1
    return {"n_logs": len(logs), "n_logs_ok": ok}


# ------------------------------------------------------------------ 純粋: Markdown
def _fmt(x, nd=3) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _pct_txt(p: dict, scale: float = 1.0, unit: str = "ms") -> str:
    if not p or not p.get("n"):
        return "-"
    return f"p50 {p['p50'] * scale:.1f} / p95 {p['p95'] * scale:.1f} / max {p['max'] * scale:.1f} {unit} (n={p['n']})"


def render_markdown(result: dict) -> str:
    c, s, disp = result["conditions"], result["summary"], result["display"]
    L = [f"# 実機の受入: 実戦ログの同一局面の両方式再生 ({c['at']})", "",
         "採点方式の良し悪し (勝率) は判定しない。正しさ・退避・evaluate の所要時間と、実戦ログの表示の遅延 (その接続テストで使った方式) を見る。", "",
         "## 条件", "",
         f"- コード: {c['code'].get('commit') or '-'}{' (未コミットの変更あり)' if c['code'].get('dirty') else ''} / エンジン {c['code'].get('engine')}",
         f"- 方式: {' / '.join(c['modes'])} (RL 加点の重み {c['rl_blend_weight']}、RL_ADVICE_STYLE {c['rl_style']}、transition の予算 "
         f"{c['transition_budget_ms']} ms、感度 {'あり' if c['transition_sensitivity'] else 'なし'})、据え置き・安定化ゲートは通さない",
         f"- 間引き {c['gap_sec']} 秒、古い局面 = hp_stale > {c['stale_hp_sec']} 秒 または解決側の場面の後",
         f"- 決定数: advice 行 (battle、確定) {c['n_advice_rows']} → 決定点 {s['n']} (有効 {s['n_valid']} / 古い局面 {s['n_stale']})"]
    tg = c.get("target")
    if tg is not None:
        L.append("- 対象: " + ("事前に決めた接続テスト群 (--target)" if tg.get("fixed") else
                              "事前に固定していない (--logs / --log で選んだログ。遅延の判定はその旨を注記)"))
        for mode, cons in (tg.get("by_mode") or {}).items():
            L.append(f"- 版・設定の照合 ({mode_label(mode)}): " + ("一致" if cons["ok"] else "揃わない (" + "; ".join(cons["notes"]) + ")"))
    L += ["", "| ログ | advice 行 | 決定点 | decision 行 | 使った方式 (advice 行) | move_locks | version_id | served_commit | "
              "scoring_mode (version 行) |", "|---|---|---|---|---|---|---|---|---|"]
    for lg in c["logs"]:
        modes = ", ".join(f"{k} {v}" for k, v in lg["modes_used"].items()) or "-"
        v = lg.get("versions") or {}

        def j(xs, n=12):
            return ", ".join(str(x)[:n] for x in xs) if xs else "記録なし"
        sc = j(v.get("served_commit") or [], 10) + (" (client 行)" if v.get("served_commit_source") == "client" else "")
        L.append(f"| {lg['name']} | {lg['n_advice_rows']} | {lg['n_decisions']} | {lg['n_decision_rows']} | {modes} | "
                 f"{'あり' if lg['has_move_locks'] else 'なし'} | {j(v.get('version_id') or [])} | {sc} | "
                 f"{j(v.get('scoring_mode') or [])} |")
    L += ["", "## 受入条件との照合", "", "| 項目 | 方式 | 値 | 基準 | 判定 | 注記 |", "|---|---|---|---|---|---|"]
    for r in result["acceptance"]:
        L.append(f"| {r['item']} | {r['mode']} | {_fmt(r['value'])} | {_fmt(r['limit'])} | {r['status']} | {r['note']} |")
    L += ["", "## 正しさの違反 (再生)", "", "| 項目 | " + " | ".join(f"{m} 有効 | {m} 古い局面" for m in MODES) + " |",
          "|---|" + "---|" * (2 * len(MODES))]
    for k, lab in VIOLATION_LABELS.items():
        L.append(f"| {lab} | " + " | ".join(f"{s['modes'][m]['valid']['violations'][k]} | {s['modes'][m]['stale']['violations'][k]}"
                                           for m in MODES) + " |")
    L.append("| (評価できた決定 / 評価の失敗) | " + " | ".join(
        f"{s['modes'][m]['valid']['n_eval']} / {s['modes'][m]['valid']['n_error']} | "
        f"{s['modes'][m]['stale']['n_eval']} / {s['modes'][m]['stale']['n_error']}" for m in MODES) + " |")
    if any(s["modes"][m]["valid"].get("checks") for m in MODES):
        L += ["", "検査ごとの照合 (有効な決定。成功 / 違反 / 判定不能。第一候補に関係しない検査は数えない):", "",
              "| 検査 | " + " | ".join(f"{m} 成功 / 違反 / 判定不能" for m in MODES) + " |", "|---|" + "---|" * len(MODES)]
        for k, lab in CHECK_LABELS.items():
            cells = []
            for m in MODES:
                cc = (s["modes"][m]["valid"].get("checks") or {}).get(k) or {}
                cells.append(f"{cc.get('ok', 0)} / {cc.get('ng', 0)} / {cc.get('unknown', 0)}")
            L.append(f"| {lab} | " + " | ".join(cells) + " |")
        L.append("| (照合できない検査が残る決定) | " + " | ".join(
            f"{s['modes'][m]['valid'].get('n_unknown_decisions', 0)} / {s['modes'][m]['valid']['n_eval']}" for m in MODES) + " |")
    L += ["", "照合できなかった項目 (違反に数えない、有効な決定):", ""]
    for k, lab in UNCONFIRMED_LABELS.items():
        L.append(f"- {lab}: " + " / ".join(f"{m} {s['modes'][m]['valid']['unconfirmed'][k]}" for m in MODES))
    ex_lines = []
    for m in MODES:
        for part in ("valid", "stale"):
            for k, lst in s["modes"][m][part]["examples"].items():
                for e in lst:
                    b = e.get("best") or {}
                    ex_lines.append(f"- [{m} / {'有効' if part == 'valid' else '古い局面'}] {VIOLATION_LABELS[k]}: {e['log']} "
                                    f"{e['advice_id']} (turn {e.get('turn')}) 第一候補 {b.get('kind')}:{b.get('id')} "
                                    f"場の個体 {e.get('active')}")
    if ex_lines:
        L += ["", "違反の例:", ""] + ex_lines
    ag = s["agreement"]
    L += ["", "## 両方式の第一候補の一致 (有効な決定)", "",
          f"一致 {ag['n_agree']} / {ag['n']} ({_fmt(ag['rate'])})"]
    if ag["examples"]:
        L += ["", "不一致の例:", ""] + [f"- {e['log']} {e['advice_id']} (turn {e.get('turn')}): " +
                                      " / ".join(f"{m} {(e[m] or {}).get('kind')}:{(e[m] or {}).get('id')}" for m in MODES)
                                      for e in ag["examples"]]
    fb = s["fallback"]
    L += ["", "## transition の退避 (有効な決定)", "",
          f"退避 {fb['n_fallback']} / {fb['n']} ({_fmt(fb['rate'])})、うち予算超過 {fb['n_budget']} ({_fmt(fb['budget_rate'])})", "",
          "| 理由 | 件数 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in fb["by_reason"].items()]
    L += ["", "## evaluate の所要時間 (再生、全決定点)", "", "| 方式 | 所要時間 |", "|---|---|"]
    for m in MODES:
        L.append(f"| {m} | {_pct_txt(s['modes'][m]['elapsed_ms'])} |")
    L.append(f"| 差 (同じ決定の transition − legacy) | {_pct_txt(s['elapsed_diff_ms'])} |")
    L.append(f"| p95 の差 | {_fmt(s['elapsed_p95_delta_ms'], 1)} ms |")
    L += ["", "## 古い局面 (有効な助言に数えない)", "",
          f"{s['n_stale']} / {s['n']} 決定点 (" + ", ".join(f"{STALE_LABELS[k]} {v}" for k, v in s["stale_reasons"].items()) + ")"]
    lbm = s["log_best_match"]
    L += ["", f"参考: ログに残った当時の第一候補と、そのログの方式での再生の第一候補の一致 {lbm['n_match']} / {lbm['n']} "
              f"({_fmt(lbm['rate'])})。据え置き・安定化ゲートを通さないこと、RL・使用率データの更新で変わりうる"]
    L += ["", "## 実戦ログの表示の遅延 (その接続テストで使った方式。再生の時間とは別)", "",
          "方式 = その決定時のサーバー設定 (version 行の scoring_mode → advice 行の記録)。どちらにも記録が無い決定は 不明 "
          "(legacy に寄せない)。各回 (ログごと) と合算の両方に基準を当てる。", "",
          "| 方式 / 回 | 決定 | 決定開始 → 有効な助言の表示 | 生成 → 表示 (decision 行) | 期限内 | " +
          " | ".join(DISPLAY_STATUS_LABELS.values()) + " |", "|---|---|---|---|---|" + "---|" * len(DISPLAY_STATUS_LABELS)]

    def disp_line(label, ds):
        return (f"| {label} | {ds['n']} | {_pct_txt(ds['latency_ms'], 0.001, '秒')} | {_pct_txt(ds['gen_to_shown_ms'])} | "
                f"{ds['n_in_time']} / {ds['n_front']} ({_fmt(ds['in_time_rate'])}) | " +
                " | ".join(str(ds["status"].get(k, 0)) for k in DISPLAY_STATUS_LABELS) + " |")
    for m, ds in sorted(disp.items(), key=lambda kv: str(kv[0])):
        note = f" (方式の記録なし {ds['n_mode_unrecorded']})" if ds["n_mode_unrecorded"] else ""
        if ds.get("n_mode_mismatch"):
            note += f" (version 行と advice 行の食い違い {ds['n_mode_mismatch']})"
        L.append(disp_line(f"**{mode_label(m)} 合算**{note}", ds))
        for lg, b in (ds.get("by_log") or {}).items():
            L.append(disp_line(f"{mode_label(m)} / {lg}", b))
    da = result.get("display_all")
    if da and len(disp) > 1:
        L.append(disp_line("全体 (方式を問わない、参考)", da))
    L += ["", "決定開始 = 決定の直前の、決定画面と情報確認の画面だけが続く scene 行の並びの最初の時刻 (サーバーが認識した時刻。"
              "フレームの到着からの処理時間は含まない。決定の途中で解決側の場面へ誤分類された行があると遅延を短めに測る)。"
              "期限内 = 表示時刻 ≤ min(決定開始 + 期限, 決定時刻)。決定の後の表示は遅延の分布にも期限内にも入れない (分母には残す)。"
              "再生の evaluate の所要時間の差は参考値で、実機の表示の増分の代わりにしない。"]
    prof = result.get("profile")
    if prof:
        L += ["", "## evaluate の内訳 (--profile、cProfile の累積時間の上位)", ""]
        for mode, txt in prof.items():
            L += [f"### {mode}", "", "```", txt.rstrip(), "```", ""]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 副作用あり: 読み込み・再生・出力
def load_records(path: Path) -> list:
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _code_info() -> dict:
    info = {"commit": None, "dirty": None, "engine": None}
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        info["commit"] = r.stdout.strip() or None
        r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, timeout=10)
        info["dirty"] = bool(r.stdout.strip())
    except Exception:
        pass
    try:
        import advisor.engine as E
        info["engine"] = str(Path(E.__file__).resolve())
    except Exception:
        pass
    return info


def _checker_kwargs() -> dict:
    """check_best に渡す図鑑・形態の判定 (副作用: 図鑑・メガ石の表の読み込み)"""
    from advisor.dex import get_dex
    from advisor.engine import CHOICE_ITEM_IDS, battle_form_source
    from advisor.gimmick import _base_of, is_mega_form
    dex = get_dex()

    def is_status_move(mid):
        mv = dex.move(mid)
        return bool(mv) and (mv.get("category") == "Status" or not mv.get("power"))

    def base_of(sid):
        s = str(sid or "")
        if is_mega_form(s):
            s = _base_of(s)
        return battle_form_source(s) or s

    return {"is_status_move": is_status_move, "is_mega": is_mega_form, "base_of": base_of, "choice_items": CHOICE_ITEM_IDS}


def _engine_state(row: dict, resolver) -> dict:
    from tools.advice_replay import compact_to_engine_state
    c = row["state"]
    st = compact_to_engine_state(c, resolver, row.get("move_locks"))
    st["scene"] = c.get("scene")
    st.setdefault("turn", row.get("turn"))
    return st


def _registered_ids(state: dict, resolver) -> list:
    from advisor.engine import registered_moves_of_mon
    pl = state["player"]
    idx = pl.get("active_index")
    if not isinstance(idx, int) or not (0 <= idx < len(pl["party"])):
        return []
    try:
        return [mid for _ja, mid in registered_moves_of_mon(pl["party"][idx], resolver)]
    except Exception:
        return []


def _active_label(compact: dict) -> Optional[str]:
    pl = compact.get("player") or {}
    party, idx = pl.get("party") or [], pl.get("active")
    m = party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else None
    return None if m is None else f"{m.get('species')}{' (mega)' if m.get('mega') else ''}"


def _slim_best(b: Optional[dict]) -> Optional[dict]:
    return None if not b else {"kind": b.get("kind"), "id": b.get("id"), "name": b.get("name")}


def replay_one(state: dict, resolver, order: tuple) -> dict:
    """1 決定点を order の順に各方式で evaluate する (副作用: エンジン)。{方式: (戻り値 or None, 所要 ms, エラー)}"""
    from advisor.engine import evaluate
    out = {}
    for mode in order:
        s = copy.deepcopy(state)
        t0 = time.perf_counter()
        try:
            adv = evaluate(s, resolver, scoring_mode=mode)
            err = None
        except Exception as e:
            adv, err = None, f"{type(e).__name__}: {e}"
        out[mode] = (adv, round((time.perf_counter() - t0) * 1000.0, 1), err)
    return out


def profile_evaluate(states: list, resolver, mode: str, top: int = REAL_ACCEPT_PROFILE_TOP) -> str:
    """states を mode で evaluate したときの cProfile の累積時間の上位 top 行 (副作用: エンジン。採点は変えない)"""
    import cProfile
    import io
    import pstats
    from advisor.engine import evaluate
    pr = cProfile.Profile()
    for st in states:
        s = copy.deepcopy(st)
        pr.enable()
        try:
            evaluate(s, resolver, scoring_mode=mode)
        except Exception:
            pass
        pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
    lines = buf.getvalue().splitlines()
    # 先頭の空行・ファイル名の行を落とし、表だけ残す
    i = next((k for k, ln in enumerate(lines) if "function calls" in ln), 0)
    return "\n".join(ln for ln in lines[i:] if ln.strip()) + "\n"


def run(paths: list, out_dir: Optional[Path], gap_sec: float = DECISION_GAP_SEC, dry_run: bool = False,
        target_fixed: bool = False, profile: bool = False) -> dict:
    """target_fixed = 対象のログを事前に決めた接続テスト群として渡したか (--target)。profile = evaluate の内訳も出す"""
    logs = []
    for p in paths:
        recs = load_records(p)
        rows = battle_advice_rows(recs)
        dec = thin_by_gap(rows, gap_sec)
        modes_used: dict = {}
        for r in rows:
            m, rec = advice_mode(r)
            k = m if rec else f"{m} (記録なし)"
            modes_used[k] = modes_used.get(k, 0) + 1
        logs.append({"path": Path(p), "name": Path(p).name, "records": recs, "rows": rows, "decisions": dec,
                     "n_advice_rows": len(rows), "n_decisions": len(dec),
                     "n_decision_rows": sum(1 for r in recs if r.get("type") == "decision"),
                     "has_move_locks": any("move_locks" in r for r in rows), "modes_used": modes_used,
                     "versions": log_versions(recs)})
    n_dec = sum(lg["n_decisions"] for lg in logs)
    print(f"ログ {len(logs)} 本、advice 行 {sum(lg['n_advice_rows'] for lg in logs)} → 決定点 {n_dec} (間引き {gap_sec} 秒)")
    for lg in logs:
        print(f"  {lg['name']}: advice {lg['n_advice_rows']} → 決定点 {lg['n_decisions']} / decision 行 {lg['n_decision_rows']} / "
              f"方式 {lg['modes_used']} / move_locks {'あり' if lg['has_move_locks'] else 'なし'}")
    if dry_run:
        print("(dry-run: 再生しない、何も書かない)")
        return {"dry_run": True, "logs": [{k: v for k, v in lg.items() if k not in ("records", "rows", "decisions", "path")}
                                          for lg in logs]}

    from vision.normalize import NameResolver
    from advisor.engine import RL_BLEND_DEFAULT
    from champions_agent import config as C
    import os
    resolver = NameResolver()
    ck = _checker_kwargs()
    # 初回の読み込み (図鑑・RL・使用率) を所要時間から外す
    first = next((lg["decisions"][0] for lg in logs if lg["decisions"]), None)
    if first is not None:
        replay_one(_engine_state(first, resolver), resolver, MODES)
    decisions = []
    i = 0
    for lg in logs:
        timeline = scene_timeline(lg["records"])
        for row in lg["decisions"]:
            compact = row["state"]
            try:
                st = _engine_state(row, resolver)
            except Exception as e:
                print(f"  復元できない: {lg['name']} {row.get('advice_id')}: {e}")
                continue
            reg = _registered_ids(st, resolver)
            order = MODES if i % 2 == 0 else tuple(reversed(MODES))      # 順番の偏り (キャッシュ) を均す
            i += 1
            res = replay_one(st, resolver, order)
            lm, _rec = advice_mode(row)
            d = {"log": lg["name"], "advice_id": row.get("advice_id"), "t": row.get("t"), "turn": row.get("turn"),
                 "scene": compact.get("scene"), "active": _active_label(compact),
                 "stale": stale_reasons(row, scene_before(timeline, float(row.get("t") or 0.0))),
                 "log_best": _slim_best((row.get("advice") or {}).get("best")), "log_mode": lm, "modes": {}}
            for mode, (adv, ms, err) in res.items():
                if err or adv is None:
                    d["modes"][mode] = {"ok": False, "error": err or "None", "elapsed_ms": ms, "best": None,
                                        "violations": [], "unconfirmed": []}
                    continue
                chk = check_best(adv, compact, row.get("move_locks"), reg, detail=True, **ck)
                d["modes"][mode] = {"ok": bool(adv.get("ok")), "best": _slim_best(adv.get("best")), "elapsed_ms": ms,
                                    "scoring_used": adv.get("scoring_used") or (adv.get("scoring_mode") if adv.get("ok") else None),
                                    "fallback_reason": adv.get("scoring_fallback"), "reason": adv.get("reason"),
                                    "violations": chk["violations"], "unconfirmed": chk["unconfirmed"],
                                    "checks": chk["checks"]}
            decisions.append(d)
            if i % 20 == 0:
                print(f"  {i} / {n_dec}")
    summary = summarize(decisions)
    disp_rows = []
    for lg in logs:
        for r in display_rows(lg["records"], unrecorded_mode=UNKNOWN_MODE):
            disp_rows.append({"log": lg["name"], **r})
    display = summarize_display(disp_rows)
    rc = record_check_of(logs)
    vers = {lg["name"]: lg["versions"] for lg in logs}
    by_mode = {m: target_consistency([{"name": n, "versions": vers[n]} for n in (ds.get("by_log") or {}) if n in vers])
               for m, ds in display.items()}
    delta_logs = [n for m in MODES for n in ((display.get(m) or {}).get("by_log") or {}) if n in vers]
    group = {"target_fixed": target_fixed, "by_mode": by_mode,
             "delta": target_consistency([{"name": n, "versions": vers[n]} for n in delta_logs],
                                         fields=("served_commit", "version_id"))}
    prof = None
    if profile:
        states = []
        for lg in logs:
            for row in lg["decisions"]:
                try:
                    states.append(_engine_state(row, resolver))
                except Exception:
                    continue
        prof = {m: profile_evaluate(states, resolver, m) for m in MODES}
    result = {
        "conditions": {"at": time.strftime("%Y-%m-%d %H:%M"), "code": _code_info(), "modes": list(MODES),
                       "rl_blend_weight": RL_BLEND_DEFAULT, "rl_style": os.environ.get("RL_ADVICE_STYLE", "balance"),
                       "transition_budget_ms": C.TRANSITION_BUDGET_MS, "transition_sensitivity": C.TRANSITION_SENSITIVITY_ENABLED,
                       "gap_sec": gap_sec, "stale_hp_sec": REAL_ACCEPT_STALE_HP_SEC,
                       "display_deadline_sec": REAL_ACCEPT_DISPLAY_DEADLINE_SEC,
                       "n_advice_rows": sum(lg["n_advice_rows"] for lg in logs),
                       "target": {"fixed": target_fixed, "by_mode": by_mode, "delta": group["delta"]},
                       "logs": [{k: v for k, v in lg.items() if k not in ("records", "rows", "decisions", "path")}
                                for lg in logs]},
        "summary": summary, "display": display, "display_all": summarize_display_all(disp_rows),
        "acceptance": acceptance(summary, display, rc, group=group), "record_check": rc,
        "decisions": decisions, "display_rows": disp_rows, "profile": prof,
    }
    out_dir = Path(out_dir) if out_dir else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = render_markdown(result)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"保存: {out_dir}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="実機の受入: 実戦ログの同一局面を両方式 (legacy / transition) で再生する")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--logs", type=int, default=None, help="logs/battles の直近 N 本 (ファイル名の順)")
    g.add_argument("--log", nargs="+", default=None, help="対戦ログのパス (複数可。対象を事前に固定したことにはならない)")
    g.add_argument("--target", nargs="+", default=None, metavar="LOG",
                   help="事前に決めた接続テスト群のログ (遅延の判定の対象。各回と合算の両方に基準を当て、版・設定の一致を検査する)")
    ap.add_argument("--out", default=None, help="出力のディレクトリ (既定 logs/real_accept/<日時>)")
    ap.add_argument("--gap-sec", type=float, default=DECISION_GAP_SEC, help="決定点の間引き間隔 (秒、tools.advice_replay と同じ既定)")
    ap.add_argument("--dry-run", action="store_true", help="対象のログと決定点の数だけ出す (再生しない・書かない)")
    ap.add_argument("--profile", action="store_true",
                    help=f"evaluate の内訳 (cProfile の累積時間の上位 {REAL_ACCEPT_PROFILE_TOP} 行) を方式ごとに出す (採点は変えない)")
    args = ap.parse_args()
    if args.target:
        paths = [Path(p) for p in args.target]
    elif args.log:
        paths = [Path(p) for p in args.log]
    else:
        paths = sorted(LOG_DIR.glob("battle_*.jsonl"))[-(args.logs or 1):]
    missing = [str(p) for p in paths if not Path(p).exists()]
    if missing or not paths:
        raise SystemExit(f"対戦ログが無い: {missing or LOG_DIR}")
    run(paths, Path(args.out) if args.out else None, args.gap_sec, args.dry_run, target_fixed=bool(args.target),
        profile=args.profile)


if __name__ == "__main__":
    main()
