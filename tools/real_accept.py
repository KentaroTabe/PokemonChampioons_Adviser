"""実機の受入の道具 (2026-10-16、docs/ADVICE_ACCURACY_PLAN_1009.md §8.22・§8.25): 実戦ログの同じ局面を両方式で再生する。

    python -m tools.real_accept [--target LOG ... | --logs N | --log PATH ...] [--out DIR] [--gap-sec S] [--dry-run] [--profile]
                                [--compare-search | --compare-sensitivity]

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

2026-10-16 表示用の探索の切り替え (config.ADVICE_DISPLAY_SEARCH、既定 off。計画書 §8.29 のレビュー):
  - 通常の再生は evaluate の既定 (config の設定) で動く。条件に display_search (設定値) を残す
  - --compare-search: 通常の受入の代わりに、同じ決定点 (固定局面) を両方式それぞれで表示用の探索 on / off の 2 通りに evaluate し、
    候補・得点・順位・第一候補の合法性 (check_best) が一致するかを決定ごとに照合する (compare_search_pair)。不一致の件数と例、
    所要時間 (on / off の p50 / p95)、transition の退避率 (on / off)、探索を省略した決定の件数 (= 択評価・起点注意なし) を表にする。
    不一致が 1 件でもあれば「満たさない」(summarize_search_compare)。出力は result.json と report.md (同じ出力先の規則)

2026-10-18 transition の感度の実機の既定 off (scripts/advice_env.sh が TRANSITION_SENSITIVITY=0 を export。レビュー 2026-10-18):
  - --compare-sensitivity: 通常の受入の代わりに、同じ決定点を transition で感度 on / off (config.TRANSITION_SENSITIVITY_ENABLED を
    一時的に切り替える) の 2 通りに evaluate し、本採点の候補・得点 (表示の得点と Q / Q_worst)・順位・第一候補の合法性 (check_best)・
    退避の理由が一致するかを決定ごとに照合する (compare_sensitivity_pair)。legacy は感度を使わないので照合しない。
    不一致が 1 件でもあれば「満たさない」(summarize_sensitivity_compare)。所要時間 (on / off) と感度の状態
    (battle_logger.sensitivity_fields_of の sensitivity_status) の件数も出す。出力は result.json と report.md

2026-10-23 再生のデータの版 (レビュー 2026-10-19 §2、tools.inputs_pin): --data-mode restore (既定) | current。
  - restore: 決定ごとに、その時点の version 行の使用率の snapshot と登録チームの sha、selection_record 行のバンクの sha16 を当時の
    版として使う (advisor.data_pin.set_override。型予測・タイプからの候補はその snapshot、候補の範囲はその id 以下)。登録チームは
    version 行の team.registry (助言エンジンが読んだ登録そのもの、2026-10-23 から記録) があればそれに差し替え (registry_sha256 と
    合わなければ記録の不整合で判定不能)、無い旧ログは今の登録の本文の sha と比べる。当時の snapshot が
    手元の DB に無い・記録が無い (2026-10-20 より前の version 行)・当時の登録チームが今の登録と違う決定は再生せず、照合できない決定
    (unconfirmed data_restore、checks data_restore unknown) に数える = 正しさは 判定不能 (照合できた範囲で違反 0) より良くならない。
    バンクが手元に無ければ読もうとしたら止める (対戦中の評価は読まない)。
  - current: 再生の開始時の DB の最新・今のバンク・今の登録で過去の局面を評価し直す (当時の入力ではないことを report に出す)。
  - result.json の conditions.data_versions (ログごとの当時の版・使った版・理由) と、受入条件の表の「入力: 再生のデータの版」の行。
    --compare-search / --compare-sensitivity も同じ規則で差し替え、復元できない決定は照合しない。

出力: logs/real_accept/<日時>/result.json と report.md (条件・正しさの違反・一致率・退避・所要時間・古い局面・受入条件との照合)。
受入条件 (champions_agent.config の REAL_ACCEPT_*) との照合は 満たす / 満たさない / 判定不能。10 fps の録画再生での処理率
(負荷) はこの道具では測らない (tools.shadow_load_check --compare-scoring)。

純粋な部分 (thin_by_gap / stale_reasons / check_best / decision_mode / display_rows / summarize / log_versions /
target_consistency / acceptance / render_markdown) は
tests/test_real_accept.py でテストする。--compare-search の純粋な部分 (rank_signature / compare_search_pair /
summarize_search_compare / render_search_compare_markdown) は tests/test_display_search_gate.py でテストする。
--compare-sensitivity の純粋な部分 (q_signature / compare_sensitivity_pair / summarize_sensitivity_compare /
render_sensitivity_compare_markdown) は tests/test_sensitivity_off.py でテストする。

2026-10-19 状態の保存・復元 (レビュー 2026-10-19 §1。battle_logger.compact_state の版 2 と tools.advice_replay の対の復元):
  - 再生の入力は advice 行 1 行だけから作る (_engine_state。後の行・scene 行から補わない)。state.now = t_now → t_gen → t
  - 版 2 の行は対応待ち・推定・HP の不確かさと観測時刻・消費済み持ち物・技の固定・直前の技も戻す。技の固定の照合 (check_best) は
    move_locks が無くても版 2 の state から作る (move_locks_for_check)
  - 版 1 の行 (state_schema の無い行) は、照合できない項目 (RESTORE_UNKNOWN_LABELS) を unconfirmed に、checks の
    state_restore を unknown に数える (= 正しさは 判定不能 (照合できた範囲で違反 0) より良くならない)。ログごとの版の行数を表に出す

2026-10-20 レビュー (docs/REAL_ACCEPT_TRANSITION_20261020.md §3〜§6。純粋な部分は tests/test_real_accept.py):
  - 選出の照合は交代先ごと (pick_status / appearance_indices): 選出済み (印、または個体同定が確かな登場記録) / 非選出確定
    (3 体が確定していてその外) / 不明。違反は非選出確定だけ、不明はその検査だけ判定不能。3 値の件数を report に出す
  - 退避は 3 単位: 決定単位 (decision_fallback_rows。decision 行が参照する助言、規則は config REAL_ACCEPT_FALLBACK_DECISION_ADVICE。
    各回と合算の両方に上限、1 回でも超えたら合算も未達)、助言行単位 (advice_row_counts、参考)、再生局面単位 (summarize の
    fallback、参考)。助言行数 / 決定と、advice 行に evaluated があれば 実計算の回数 / 決定 も出す
  - 再生の evaluate は各 advice 行の時点の version 行の設定 (感度 transition_sensitivity・表示用の探索 display_search。
    replay_settings) で行う (時間・退避の比較を実機に揃える)。記録の無い旧ログは config の既定で、その旨を report に書く
  - 版の照合は版の識別と実行状態を分ける (version_identity / group_consistency。version_id は比べない)。同条件の根拠
    (比べた欄と値、全 advice 行の policy.rl_loaded) を report に出す。新しい version 行 (runtime の欄あり) は
    advisor.versions.identity_of で識別を取る (無い版のコードでは旧い行の経路)

2026-10-31 固定入力の異常 (レビュー E-inputs-3): 実機の対戦ログに固定入力の異常の行 (type = config.INPUTS_PIN_LOG_TYPE、
server が助言を止めた記録。advice 行の advice.reason = config.INPUTS_PIN_REASON も同じ扱い) がある対戦は不成立:
その対戦を含む群の版の照合 (group_consistency) は一致にならず、受入条件の 満たす / PARTIAL の行は 判定不能 にする
(違反の 満たさない はそのまま)。理由は report の「同条件の根拠」に出す (inputs_pin_rows / inputs_pin_lines)。
再生の evaluate が固定入力の異常を返した決定 (--data-mode restore の当時の版が手元で使えない) は、照合できない決定
(unconfirmed data_restore) に数える (再生の結果で照合しない)。--compare-search / --compare-sensitivity も、on / off の
どちらか一方でも固定入力の異常を返した決定は一致にも不一致にもせず、照合できない決定 (n_unconfirmed) に数え、判定を 判定不能 にする
(pair_unconfirmed)
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
    INPUTS_PIN_LOG_TYPE, INPUTS_PIN_REASON,
    ADVICE_SCORING_MODES, REAL_ACCEPT_DISPLAY_DEADLINE_SEC, REAL_ACCEPT_DISPLAY_IN_TIME_MIN, REAL_ACCEPT_FALLBACK_ALL_MAX,
    REAL_ACCEPT_FALLBACK_BUDGET_MAX, REAL_ACCEPT_LATENCY_DELTA_SEC, REAL_ACCEPT_LATENCY_P95_SEC, REAL_ACCEPT_PROFILE_TOP,
    REAL_ACCEPT_STALE_HP_SEC, REAL_ACCEPT_VIOLATIONS_MAX, BSS_PICK_COUNT, REAL_ACCEPT_FALLBACK_DECISION_ADVICE,
    REAL_ACCEPT_VERSION_IGNORE_FIELDS, REAL_ACCEPT_VERSION_RUNTIME_FIELDS, REAL_ACCEPT_VERSION_REQUIRED_FIELDS)
from tools.advice_replay import (DECISION_GAP_SEC, RESTORE_UNKNOWN_LABELS, move_locks_for_check, replay_input_counts, replay_now,
                                 restore_unknowns)
from tools.decision_audit import _display_index
from tools.shadow_load_check import percentiles

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "logs" / "battles"
OUT_DIR = REPO / "logs" / "real_accept"
MODES = tuple(ADVICE_SCORING_MODES)          # ("legacy", "transition")
BUDGET_REASON = "budget"                     # advisor.scoring_transition の予算超過の退避の理由
EXAMPLES_PER_KEY = 5                         # 違反・未確認の例を項目ごとに残す数
LOG_INTERNAL_KEYS = ("records", "rows", "decisions", "path", "decision_versions")   # 出力 (result.json) に出さないログの欄

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
    "selection_unknown": "交代先の選出が不明 (3 体が確定せず、その交代先に選出の印・確かな登場記録が無い。その照合だけ判定不能)",
    "lock_unknown": "技の固定の対象の技が分からない",
    "no_position_key": "助言に場の個体の鍵 (position_key) が無い",
    # 状態の復元で照合できない項目 (2026-10-19: 版 1 の advice 行の state。tools.advice_replay.restore_unknowns)
    **RESTORE_UNKNOWN_LABELS,
    # 2026-10-23: --data-mode restore で当時のデータの版 (使用率の snapshot・登録チーム) を復元できない決定 (再生していない)
    "data_restore": "当時のデータの版を復元できない (再生していない。--data-mode restore)",
}
# 検査ごとの照合の結果 (成功 ok / 違反 ng / 判定不能 unknown。第一候補の種類に関係しない検査は None で数えない)
CHECK_LABELS = {
    "best": "第一候補がある",
    "move_owned": "技の所有", "move_pp": "技の PP", "move_fainted": "場の個体がひんしでない",
    "move_lock": "技の固定 (アンコール / こだわり)", "move_taunt": "ちょうはつ", "move_disable": "かなしばり",
    "switch_party": "交代先が party にある", "switch_form": "交代先の形態", "switch_self": "交代先が場の個体でない",
    "switch_fainted": "交代先がひんしでない", "switch_picked": "交代先が選出済み", "candidate_picked": "候補の交代先が選出済み",
    "active_identity": "場の個体の一致 (個体・メガ形態)",
    "state_restore": "状態の復元 (版 2 の欄をすべて戻せたか。版 1 の行は判定不能)",
    "data_restore": "データの版の復元 (当時の使用率の snapshot・登録チーム。--data-mode restore だけ数える)",
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


PICK_STATES = ("picked", "unpicked", "unknown")   # 交代先ごとの 選出済み / 非選出確定 / 不明
PICK_STATE_LABELS = {"picked": "選出済み", "unpicked": "非選出確定", "unknown": "不明"}
PICK_EVIDENCE_LABELS = {"mark": "選出の印", "active": "場の個体", "entry_turn": "場に出たターンの記録",
                        "pick_order": "選出順の記録", "hp_read": "HP の実読み (その助言の時刻まで)", "event": "登場の文言 (events 行)"}


def appearance_indices(records: list, party: list, t_ref: Optional[float]) -> set:
    """自分の party のうち、時刻 t_ref までの events 行の交代の文言 (fired に switch_player、texts に名前) で登場が確かな個体の
    index (純粋。2026-10-20 レビュー: 登場イベント)。名前は party の ja / display_name。t_ref が None なら空 (時刻で区切れない)"""
    if t_ref is None:
        return set()
    texts = []
    for r in records:
        if r.get("type") != "events" or r.get("t") is None or float(r["t"]) > float(t_ref):
            continue
        if not any("switch_player" in str(f) for f in (r.get("fired") or [])):
            continue
        texts += [str(x) for x in (r.get("texts") or [])]
    out = set()
    for i, m in enumerate(party or []):
        names = {n for n in (m.get("ja"), m.get("display_name")) if n}
        if any(n in tx for n in names for tx in texts):
            out.add(i)
    return out


def pick_status(compact: dict, appeared: Iterable = (), pick_count: int = BSS_PICK_COUNT) -> dict:
    """自分の party の個体ごとの選出の判明 (純粋。2026-10-20 レビュー: 部分判明でも交代先ごとに確認できるかで判定する)。
    選出済み = 選出の印 (picked)、または個体同定が確かな登場記録: 場の個体 (active / is_active)・場に出たターン (entry_turn)・
    選出順 (pick_order)・HP の実読み (hp_read_ts、推定 hp_estimated は除く)・登場の文言 (appeared = appearance_indices の index。
    その助言の時刻までの events 行)。compact はその advice 行の state (助言が見た状態) なので、その欄はどれも助言の時刻までの記録。
    推定の個体 (guess / pending) は登場記録を数えない (印だけ)。
    選出済みが pick_count 体以上 (3 体が確定) ならその外は 非選出確定、未満なら 不明。
    戻り値 {"status": [PICK_STATES], "evidence": [[PICK_EVIDENCE_LABELS の鍵]], "n_confirmed", "complete"}"""
    pl = compact.get("player") or {}
    party = pl.get("party") or []
    idx = pl.get("active")
    app = set(appeared or ())
    evid = []
    for i, m in enumerate(party):
        ev = []
        if m.get("picked"):
            ev.append("mark")
        if not (m.get("guess") or m.get("pending")):
            if i == idx or m.get("is_active"):
                ev.append("active")
            if m.get("entry_turn") is not None:
                ev.append("entry_turn")
            if m.get("pick_order") is not None:
                ev.append("pick_order")
            if m.get("hp_read_ts") is not None and not m.get("hp_estimated"):
                ev.append("hp_read")
            if i in app:
                ev.append("event")
        evid.append(ev)
    n_conf = sum(1 for ev in evid if ev)
    complete = n_conf >= pick_count
    status = ["picked" if ev else ("unpicked" if complete else "unknown") for ev in evid]
    return {"status": status, "evidence": evid, "n_confirmed": n_conf, "complete": complete}


def check_best(adv: Optional[dict], compact: dict, move_locks: Optional[dict] = None, registered: Iterable = (),
               is_status_move: Callable = lambda mid: False, is_mega: Callable = lambda sid: False,
               base_of: Callable = _identity, choice_items: Iterable = (), detail: bool = False,
               appeared: Iterable = ()) -> dict:
    """1 決定の第一候補の検査 (純粋)。adv = evaluate の戻り値 (best / actions / position_key)、compact = advice 行の簡約状態
    (battle_logger._compact_state)、move_locks = advice 行の move_locks (None = 欄の無い行。技の固定・かなしばりは照合できない)、
    registered = 場の個体の登録の技の id (読めた技が 4 つ未満のときの補完に使う)。is_status_move(技 id) = 変化技か、
    is_mega(種族 id) = メガ形態の id か、base_of(種族 id) = 同じ個体とみなす基本の id (メガ・戦闘中の形態変化を落とす)、
    choice_items = こだわり系の持ち物 id。
    戻り値 {"violations": [VIOLATION_LABELS の鍵], "unconfirmed": [UNCONFIRMED_LABELS の鍵]}。detail = True なら
    "checks": {CHECK_LABELS の鍵: "ok" / "ng" / "unknown" / None (この第一候補には関係しない)} も付ける (2026-10-16 レビュー:
    判定不能を合格にしないため、検査ごとに 成功 / 違反 / 判定不能 を分ける)。
    選出の照合 (2026-10-20 レビュー): 交代先ごとに pick_status (appeared = その助言の時刻までの登場の文言の index) で
    選出済み / 非選出確定 / 不明 を決め、違反 (switch_unpicked / candidate_unpicked) は 非選出確定 だけ。不明はその検査
    (switch_picked / candidate_picked) だけ判定不能 (unconfirmed に selection_unknown)。交代の候補が無ければ candidate_picked は ok。
    detail = True なら "targets": [{"id", "best" (第一候補か), "status" (PICK_STATES), "evidence"}] (交代先ごと、重複なし) も付ける"""
    viol, unconf = [], []
    checks: dict = {k: None for k in CHECK_LABELS}
    adv = adv or {}
    best = adv.get("best") if adv.get("ok", True) else None
    pl = compact.get("player") or {}
    party = pl.get("party") or []
    idx = pl.get("active")
    me = party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else None
    ps = pick_status(compact, appeared)
    targets: list = []

    def out():
        r = {"violations": list(dict.fromkeys(viol)), "unconfirmed": list(dict.fromkeys(unconf))}
        if detail:
            r["checks"] = checks
            r["targets"] = targets
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
            pst = ps["status"][ti]
            if pst == "unpicked":
                viol.append("switch_unpicked")
                checks["switch_picked"] = "ng"
            elif pst == "unknown":
                unconf.append("selection_unknown")
                checks["switch_picked"] = "unknown"
            else:
                checks["switch_picked"] = "ok"
    seen_t: set = set()
    cand_states = []
    for j, a in enumerate([best] + list(adv.get("actions") or [])):
        if not a or a.get("kind") != "switch":
            continue
        ci, _how = find(a.get("id"))
        if ci is None or ci in seen_t:
            continue
        seen_t.add(ci)
        cand_states.append(ps["status"][ci])
        targets.append({"id": a.get("id"), "best": j == 0, "status": ps["status"][ci], "evidence": ps["evidence"][ci]})
    if "unpicked" in cand_states:
        viol.append("candidate_unpicked")
        checks["candidate_picked"] = "ng"
    elif "unknown" in cand_states:
        unconf.append("selection_unknown")
        checks["candidate_picked"] = "unknown"
    else:
        checks["candidate_picked"] = "ok"
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


def inputs_pin_rows(records: list, examples: int = EXAMPLES_PER_KEY) -> dict:
    """1 本のログの固定入力の異常の記録 (純粋。2026-10-31): type = INPUTS_PIN_LOG_TYPE の行と、advice 行で advice.reason =
    INPUTS_PIN_REASON の行。戻り値 {"n": 行数, "n_rows": 異常の行, "n_advice": advice 行, "n_suppressed": 異常の行の n_suppressed の和,
    "sources": {出どころ: 行数}, "errors": [error の文言 (重複なし、examples 件まで)]}"""
    out = {"n": 0, "n_rows": 0, "n_advice": 0, "n_suppressed": 0, "sources": {}, "errors": []}
    for r in records or []:
        if r.get("type") == INPUTS_PIN_LOG_TYPE:
            out["n_rows"] += 1
            out["n_suppressed"] += int(r.get("n_suppressed") or 0)
            src, err = r.get("source") or "-", r.get("error")
        elif r.get("type") == "advice" and (r.get("advice") or {}).get("reason") == INPUTS_PIN_REASON:
            out["n_advice"] += 1
            src, err = "advice", (r.get("advice") or {}).get("error")
        else:
            continue
        out["n"] += 1
        out["sources"][src] = out["sources"].get(src, 0) + 1
        if err and err not in out["errors"] and len(out["errors"]) < examples:
            out["errors"].append(str(err))
    return out


def inputs_pin_text(info: Optional[dict]) -> str:
    """inputs_pin_rows の戻り値の 1 行の説明 (純粋)"""
    info = info or {}
    src = ", ".join(f"{k} {v}" for k, v in sorted((info.get("sources") or {}).items()))
    return (f"固定入力の異常の記録 {info.get('n', 0)} 行 ({src}"
            + (f"、間引いた件数 {info['n_suppressed']}" if info.get("n_suppressed") else "") + ")"
            + (f": {' / '.join(info.get('errors') or [])}" if info.get("errors") else ""))


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


# ------------------------------------------------------------------ 純粋: 版の識別と実行状態の照合 (2026-10-20 レビュー)
SCORING_MODE_FIELDS = ("scoring_mode", "scoring_mode_env", "scoring_mode_source")   # 両方式の増分の照合では比べない欄


def _flatten(d: Optional[dict], prefix: str = "") -> dict:
    """入れ子の dict を「a.b」の鍵に平らにする (純粋)。dict でない入れ子 (list など) は JSON の文字列にして比べられる形にする"""
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flatten(v, key + "."))
        elif v is None or isinstance(v, (str, int, float, bool)):
            out[key] = v
        else:
            out[key] = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return out


def _field_in(key: str, fields: Iterable) -> bool:
    return any(key == f or key.startswith(f + ".") for f in fields)


def _field_match(key: str, fields: Iterable) -> bool:
    """欄の名前の照合 (前方一致に加えて末尾一致: identity_of の "scoring.scoring_mode" と旧い行の "scoring_mode" を同じ欄とみなす)"""
    return any(key == f or key.startswith(f + ".") or key.endswith("." + f) for f in fields)


def _identity_problems(v: dict) -> list:
    """版の識別が成り立たない理由 (advisor.versions.identity_problems、2026-10-23: 新形式の行で登録内容の sha が無い)。無い版のコードでは []"""
    try:
        from advisor.versions import identity_problems
        return list(identity_problems(v))
    except Exception:
        return []


def _identity_fn_default() -> Optional[Callable]:
    """advisor.versions.identity_of (2026-10-20 に別の担当が足す、版の識別だけを取り出す関数)。無い版のコードでは None"""
    try:
        from advisor.versions import identity_of
        return identity_of
    except Exception:
        return None


def version_identity(records: list, ignore: Iterable = REAL_ACCEPT_VERSION_IGNORE_FIELDS,
                     runtime: Iterable = REAL_ACCEPT_VERSION_RUNTIME_FIELDS, identity_fn: Optional[Callable] = None) -> dict:
    """1 本のログの版の識別と実行状態 (純粋。identity_fn を渡さなければ advisor.versions.identity_of を読む。2026-10-20 レビュー:
    旧い version_id は実行状態 (RL 方策の遅延読み込みの印) も含む digest なので、版の識別と実行状態を分けて照合する)。
    version 行の形で 2 通りに読む:
      - 新しい行 ("runtime" の欄がある、2026-10-20 の分離以降) で identity_fn が使えるとき: 版の識別 = identity_fn(version 行)
        (コード・モデルの sha・データ版・採点設定) を平らにしたもの、実行状態 = version 行の runtime と、後から書かれた
        runtime 行 ({"type": "runtime", "runtime": {...}}) の runtime。format = "identity_of"
      - 旧い行 (runtime の欄が無い) か identity_fn が無いとき: 版の識別 = version 行の欄から ignore と runtime (実行状態の欄。
        action_policy.loaded ほか) を除いたもの、実行状態 = runtime の欄 (+ runtime 行)。format = "fields"
    どちらも served_commit (log_versions と同じ取り方) を版の識別に足す。戻り値:
      "identity": {欄: [値]}、"runtime": {欄: [値]}、"version_id": [値]、"n_version_rows"、"format": ["identity_of" / "fields"]
      "advice_runtime": {"n", "rl_loaded_true", "rl_loaded_false", "rl_loaded_missing"} (battle の advice 行の policy.rl_loaded)
      "runtime_ok": True (全 advice 行で読み込み成功、実行状態に読み込みの失敗なし) / False (失敗の記録あり) / None (確認できない)
      "runtime_basis": 根拠の文
      "identity_problems": 版の識別が成り立たない理由の列 (2026-10-23: 新形式の行 = team.registry の欄がある行で登録内容の sha
        team.registry_sha256 が無い。識別は登録内容の sha を必須照合し、ロスター由来の team.sha256 は照合しない)"""
    ignore, runtime = tuple(ignore), tuple(runtime)
    fn = identity_fn if identity_fn is not None else _identity_fn_default()
    vers = [r for r in records if r.get("type") == "version"]
    ident: dict = {}
    rt: dict = {}
    formats: list = []

    def put(dst, k, x):
        lst = dst.setdefault(k, [])
        if x not in lst:
            lst.append(x)
    problems: list = []
    for v in vers:
        if isinstance(v.get("runtime"), dict) and fn is not None:
            formats.append("identity_of")
            for why in _identity_problems(v):
                if why not in problems:
                    problems.append(why)
            for k, x in _flatten(fn(v)).items():
                put(ident, k, x)
            for k, x in _flatten(v["runtime"]).items():
                put(rt, f"runtime.{k}", x)
            continue
        formats.append("fields")
        for k, x in _flatten(v).items():
            if _field_in(k, ignore):
                continue
            put(rt if _field_in(k, runtime) else ident, k, x)
    for r in records:
        if r.get("type") == "runtime" and isinstance(r.get("runtime"), dict):
            for k, x in _flatten(r["runtime"]).items():
                put(rt, f"runtime.{k}", x)
    formats = list(dict.fromkeys(formats))
    lv = log_versions(records)
    if lv["served_commit"]:
        ident["served_commit"] = list(lv["served_commit"])
    advs = [r for r in records if r.get("type") == "advice" and r.get("kind") == "battle"]
    vals = [(r.get("policy") or {}).get("rl_loaded") for r in advs]
    ar = {"n": len(advs), "rl_loaded_true": sum(1 for x in vals if x is True),
          "rl_loaded_false": sum(1 for x in vals if x is False), "rl_loaded_missing": sum(1 for x in vals if x is None)}
    v_fail = [f"{k} = {x}" for k, xs in rt.items() for x in xs
              if (k.endswith("error") and x not in (None, "")) or (k.endswith("loaded") and x is False)]
    if v_fail or ar["rl_loaded_false"]:
        ok = False
        basis = "読み込みの失敗の記録あり: " + ", ".join(v_fail + ([f"advice 行の policy.rl_loaded = False {ar['rl_loaded_false']} 行"]
                                                         if ar["rl_loaded_false"] else []))
    elif ar["n"] and ar["rl_loaded_true"] == ar["n"]:
        ok = True
        basis = f"battle の advice 行 {ar['n']} / {ar['n']} で policy.rl_loaded = True (読み込み成功)"
    elif not ar["n"] and any(k.endswith("loaded") for k in rt) and all(xs == [True] for k, xs in rt.items()
                                                                        if k.endswith("loaded")):
        ok = True
        basis = "advice 行なし。実行状態の読み込みの欄 = True"
    else:
        ok = None
        basis = (f"読み込みを確認できない (advice 行 {ar['n']}、policy.rl_loaded の記録なし {ar['rl_loaded_missing']}、"
                 f"実行状態の読み込みの欄 {[(k, xs) for k, xs in rt.items() if k.endswith('loaded')]})")
    return {"identity": ident, "runtime": rt, "version_id": list(dict.fromkeys(v["version_id"] for v in vers if v.get("version_id"))),
            "n_version_rows": len(vers), "format": formats, "advice_runtime": ar, "runtime_ok": ok, "runtime_basis": basis,
            "identity_problems": problems, "inputs_pin": inputs_pin_rows(records)}


def group_consistency(log_infos: list, exclude: Iterable = (),
                      required: Iterable = REAL_ACCEPT_VERSION_REQUIRED_FIELDS) -> dict:
    """対象群が同じ版・設定で、同じ実行状態 (読み込み成功) か (純粋。2026-10-20 レビュー)。log_infos = [{"name", "ident":
    version_identity の戻り値}]。exclude = 比べない版の識別の欄 (両方式の増分の照合では SCORING_MODE_FIELDS)。
    一致 = required の欄が全ログで記録され、版の識別の欄 (version 行の欄 − 時刻・version_id − 実行状態の欄 + served_commit) が
    全ログで同じ 1 つの値 (一部のログにだけある欄も不一致)、かつ全ログで実行状態が読み込み成功。version_id は比べない
    (実行状態を含む digest。違えば差のある実行状態の欄を注記する)。
    戻り値 {"ok", "notes", "values": {欄: [値]}, "basis": [{"field", "value"}] (一致した欄と値)、"runtime": {ログ: 根拠の文}、
    "version_id": [値]} (acceptance の版・設定の照合と同じ形 ok / notes を持つ)"""
    exclude = tuple(exclude)
    notes, values, basis = [], {}, []
    ok = bool(log_infos)
    if not log_infos:
        notes.append("対象のログが無い")
    fmts = list(dict.fromkeys(f for li in log_infos for f in li["ident"].get("format") or []))
    if len(fmts) > 1:
        notes.append(f"version 行の形が混ざる ({' / '.join(fmts)})。欄の名前が違うので一致にならない")

    def has(idt, f):
        return any(_field_match(k, (f,)) and any(x not in (None, "") for x in v) for k, v in idt.items())
    for f in required:
        if _field_match(f, exclude):
            continue
        missing = [li["name"] for li in log_infos if not has(li["ident"]["identity"], f)]
        if missing:
            ok = False
            notes.append(f"{f} の記録なし {len(missing)} 本 ({', '.join(missing[:3])}{' ほか' if len(missing) > 3 else ''})")
    for li in log_infos:
        for why in li["ident"].get("identity_problems") or []:
            ok = False
            notes.append(f"版を識別できない: {li['name']} ({why})")
    fields = list(dict.fromkeys(k for li in log_infos for k in li["ident"]["identity"] if not _field_match(k, exclude)))
    for f in fields:
        vals: list = []
        for li in log_infos:
            for x in li["ident"]["identity"].get(f) or []:
                if x not in vals:
                    vals.append(x)
        values[f] = vals
        lacking = [li["name"] for li in log_infos if f not in li["ident"]["identity"]]
        if lacking and not _field_match(f, required):
            ok = False
            notes.append(f"{f} が一部のログに無い ({', '.join(lacking[:3])}{' ほか' if len(lacking) > 3 else ''})")
        elif len(vals) > 1:
            ok = False
            notes.append(f"{f} が揃わない ({' / '.join(str(v)[:16] for v in vals[:4])}{' ほか' if len(vals) > 4 else ''})")
        elif len(vals) == 1:
            basis.append({"field": f, "value": vals[0]})
    pin = {}
    for li in log_infos:
        info = li["ident"].get("inputs_pin") or {}
        if info.get("n"):
            ok = False
            pin[li["name"]] = inputs_pin_text(info)
            notes.append(f"不成立: {li['name']} に{pin[li['name']]}")
    runtime = {}
    for li in log_infos:
        idt = li["ident"]
        runtime[li["name"]] = idt["runtime_basis"]
        if idt["runtime_ok"] is not True:
            ok = False
            notes.append(f"実行状態: {li['name']} " + ("読み込みの失敗あり" if idt["runtime_ok"] is False else "読み込みを確認できない")
                         + f" ({idt['runtime_basis']})")
    vids = list(dict.fromkeys(v for li in log_infos for v in li["ident"]["version_id"]))
    if len(vids) > 1:
        rt_diff = []
        for k in dict.fromkeys(k for li in log_infos for k in li["ident"]["runtime"]):
            xs = []
            for li in log_infos:
                for x in li["ident"]["runtime"].get(k, [None]):
                    if x not in xs:
                        xs.append(x)
            if len(xs) > 1:
                rt_diff.append(f"{k}: {' / '.join(str(x) for x in xs)}")
        notes.append(f"version_id は {len(vids)} 種類 ({' / '.join(vids[:4])})。版の識別の欄で照合した (version_id は比べない)"
                     + (f"。実行状態の欄の差: {', '.join(rt_diff)}" if rt_diff else ""))
    return {"ok": ok, "notes": notes, "values": values, "basis": basis, "runtime": runtime, "version_id": vids, "inputs_pin": pin}


# ------------------------------------------------------------------ 純粋: 再生の設定 (2026-10-20 レビュー: 実機の設定に揃える)
def _search_bool(x) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if isinstance(x, str) and x.strip().lower() in ("on", "off"):
        return x.strip().lower() == "on"
    return None


def replay_settings(version: Optional[dict], default_sensitivity: bool, default_display_search) -> dict:
    """再生の evaluate の設定 (純粋)。その advice 行の時点の version 行の transition_sensitivity / display_search を使い
    (時間・退避の比較を実機の設定に揃えるため)、記録の無い欄は config の既定 (source = "config")。scoring_mode は version 行の
    実効値 (実機で使った方式。再生は両方式で行う)。戻り値 {"scoring_mode", "transition_sensitivity": bool, "display_search": bool,
    "source": {欄: "version" / "config" / None}, "from_version": 感度と探索の両方が version 行から}"""
    v = version or {}
    src = {}
    sens = v.get("transition_sensitivity")
    if isinstance(sens, bool):
        src["transition_sensitivity"] = "version"
    else:
        sens = bool(default_sensitivity)
        src["transition_sensitivity"] = "config"
    ds = _search_bool(v.get("display_search"))
    if ds is not None:
        src["display_search"] = "version"
    else:
        ds = bool(_search_bool(default_display_search))
        src["display_search"] = "config"
    sm = v.get("scoring_mode")
    src["scoring_mode"] = "version" if sm else None
    return {"scoring_mode": sm, "transition_sensitivity": sens, "display_search": ds, "source": src,
            "from_version": src["transition_sensitivity"] == "version" and src["display_search"] == "version"}


def versions_by_row(records: list, rows: list) -> list:
    """rows (records の advice 行) ごとに、その行より前の最後の version 行 (ファイルの順。純粋)。前に無ければログの最初の
    version 行、それも無ければ None"""
    first = next((r for r in records if r.get("type") == "version"), None)
    pos = {id(r): None for r in rows}
    cur = None
    for r in records:
        if r.get("type") == "version":
            cur = r
        elif id(r) in pos:
            pos[id(r)] = cur
    return [pos[id(r)] if pos[id(r)] is not None else first for r in rows]


def settings_label(st: dict) -> str:
    return (f"感度 {'on' if st['transition_sensitivity'] else 'off'} / 表示用の探索 {'on' if st['display_search'] else 'off'}"
            f" (実機の方式 {st.get('scoring_mode') or '記録なし'})")


# ------------------------------------------------------------------ 純粋: 退避の 3 単位と実計算の回数 (2026-10-20 レビュー)
def fallback_of(rec: Optional[dict]) -> Optional[str]:
    """advice 行の退避の理由 (top-level の fallback_reason → advice の scoring_fallback。純粋)"""
    rec = rec or {}
    if "fallback_reason" in rec:
        return rec.get("fallback_reason")
    return (rec.get("advice") or {}).get("scoring_fallback")


def _t_gen(r: dict) -> Optional[float]:
    x = (r.get("advice") or {}).get("t_gen", r.get("t"))
    return None if x is None else float(x)


def _valid_advice(r: dict, stale_hp_sec: float) -> bool:
    a = r.get("advice") or {}
    return (not a.get("provisional") and a.get("ok") is not False and bool(a.get("best") or a.get("actions"))
            and not stale_reasons(r, None, stale_hp_sec))


def decision_fallback_rows(records: list, rule: Iterable = REAL_ACCEPT_FALLBACK_DECISION_ADVICE,
                           stale_hp_sec: float = REAL_ACCEPT_STALE_HP_SEC, unrecorded_mode: Optional[str] = UNKNOWN_MODE) -> list:
    """退避の決定単位 (純粋): decision 行ごとに、開始前に固定した規則 rule (config REAL_ACCEPT_FALLBACK_DECISION_ADVICE) で
    数える助言を選ぶ。戻り値の各行 {"advice_id" (decision 行), "turn", "mode", "used_advice_id", "rule" (使った規則、無ければ None),
    "fallback_reason", "budget"}。方式は display_rows と同じ (直前の version 行 → 使った助言の記録)"""
    rule = tuple(rule)
    for step in rule:
        if step not in ("decision_advice_id", "last_valid"):
            raise ValueError(f"不明な規則: {step}")
    timeline = scene_timeline(records)
    advs = [r for r in records if r.get("type") == "advice" and r.get("kind") == "battle" and r.get("advice_id")]
    by_id = {r["advice_id"]: r for r in advs}
    out, prev_t, version = [], -1e18, None
    for rec in records:
        if rec.get("type") == "version":
            version = rec
            continue
        if rec.get("type") != "decision" or rec.get("t_decided") is None:
            continue
        t_dec = float(rec["t_decided"])
        t_open = decision_open_time(timeline, prev_t, t_dec)
        lo = t_open if t_open is not None else prev_t
        used, how = None, None
        for step in rule:
            if step == "decision_advice_id":
                r = by_id.get(rec.get("advice_id"))
            else:
                cands = [r for r in advs if _t_gen(r) is not None and lo <= _t_gen(r) <= t_dec and _valid_advice(r, stale_hp_sec)]
                r = max(cands, key=_t_gen) if cands else None
            if r is not None:
                used, how = r, step
                break
        dm = decision_mode(version, used, unrecorded_mode)
        fb = fallback_of(used) if used is not None else None
        out.append({"advice_id": rec.get("advice_id"), "turn": rec.get("turn"), "mode": dm["mode"],
                    "used_advice_id": (used or {}).get("advice_id"), "rule": how, "fallback_reason": fb,
                    "budget": bool(fb) and _reason_key(fb) == BUDGET_REASON})
        prev_t = t_dec
    return out


def advice_row_counts(records: list, unrecorded_mode: Optional[str] = UNKNOWN_MODE) -> dict:
    """1 本のログの助言行の数え (純粋): 助言行数 (battle の advice 行、確定前を含む。キャッシュを含む) / 決定、advice 行に
    evaluated の欄 (実計算したか。別の担当が足す) があれば 実計算の回数 / 決定、助言行単位の退避 (方式 = 直前の version 行 →
    advice 行の記録が transition の行)"""
    version, rows = None, []
    for rec in records:
        if rec.get("type") == "version":
            version = rec
        elif rec.get("type") == "advice" and rec.get("kind") == "battle":
            rows.append((decision_mode(version, rec, unrecorded_mode)["mode"], rec))
    n_dec = sum(1 for r in records if r.get("type") == "decision" and r.get("t_decided") is not None)
    has_eval = any("evaluated" in r for _m, r in rows)
    n_eval = sum(1 for _m, r in rows if r.get("evaluated")) if has_eval else None
    tr = [r for m, r in rows if m == "transition"]
    by: dict = {}
    for r in tr:
        fb = fallback_of(r)
        if fb:
            k = _reason_key(fb)
            by[k] = by.get(k, 0) + 1
    n_fb = sum(by.values())
    n_b = by.get(BUDGET_REASON, 0)
    return {"n_rows": len(rows), "n_provisional": sum(1 for _m, r in rows if (r.get("advice") or {}).get("provisional")),
            "n_decisions": n_dec, "rows_per_decision": (round(len(rows) / n_dec, 2) if n_dec else None),
            "has_evaluated": has_eval, "n_with_evaluated": sum(1 for _m, r in rows if "evaluated" in r),
            "n_evaluated": n_eval, "evaluated_per_decision": (round(n_eval / n_dec, 2) if has_eval and n_dec else None),
            "fallback": {"n": len(tr), "n_fallback": n_fb, "rate": _rate(n_fb, len(tr)), "n_budget": n_b,
                         "budget_rate": _rate(n_b, len(tr)), "by_reason": dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))}}


def _fb_block(rows: list) -> dict:
    matched = [r for r in rows if r.get("rule")]
    by: dict = {}
    for r in matched:
        if r.get("fallback_reason"):
            k = _reason_key(r["fallback_reason"])
            by[k] = by.get(k, 0) + 1
    n_fb = sum(by.values())
    n_b = by.get(BUDGET_REASON, 0)
    return {"n_decisions": len(rows), "n": len(matched), "n_unmatched": len(rows) - len(matched),
            "n_fallback": n_fb, "rate": _rate(n_fb, len(matched)), "n_budget": n_b, "budget_rate": _rate(n_b, len(matched)),
            "by_reason": dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))),
            "rule_used": {k: sum(1 for r in matched if r["rule"] == k) for k in dict.fromkeys(r["rule"] for r in matched)}}


def summarize_fallback_units(units: list, rule: Iterable = REAL_ACCEPT_FALLBACK_DECISION_ADVICE) -> dict:
    """退避の 3 単位のうち 決定単位 と 助言行単位 の集計 (純粋。再生局面単位は summarize の fallback)。
    units = [{"name", "decisions": decision_fallback_rows, "advice": advice_row_counts}]。決定単位は方式が transition の決定だけ。
    戻り値 {"rule", "decision": {"all", "by_log"}, "advice_row": {"all", "by_log"}, "counts": {ログ: 助言行・実計算の数え}}"""
    tr = {u["name"]: [r for r in u["decisions"] if r["mode"] == "transition"] for u in units}
    dec = {"all": _fb_block([r for rs in tr.values() for r in rs]),
           "by_log": {n: _fb_block(rs) for n, rs in tr.items() if rs}}
    ar_logs = {u["name"]: u["advice"]["fallback"] for u in units if u["advice"]["fallback"]["n"]}
    by: dict = {}
    for f in ar_logs.values():
        for k, v in f["by_reason"].items():
            by[k] = by.get(k, 0) + v
    n = sum(f["n"] for f in ar_logs.values())
    n_fb, n_b = sum(by.values()), by.get(BUDGET_REASON, 0)
    ar_all = {"n": n, "n_fallback": n_fb, "rate": _rate(n_fb, n), "n_budget": n_b, "budget_rate": _rate(n_b, n),
              "by_reason": dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))}
    counts = {u["name"]: {k: v for k, v in u["advice"].items() if k != "fallback"} for u in units}
    return {"rule": list(rule), "decision": dec, "advice_row": {"all": ar_all, "by_log": ar_logs}, "counts": counts}


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
    unconfirmed だけで決める)。checks は検査ごとに ok / ng / unknown を数える。
    pick_targets (2026-10-20): 交代先ごと (各決定の第一候補 + 候補の交代先、重複なし。結果の "targets") の 選出済み / 非選出確定 /
    不明 の件数 ("all" と第一候補だけの "best")、pick_examples = 選出済みでない交代先の例"""
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
            pt = {w: {x: 0 for x in PICK_STATES} for w in ("all", "best")}
            pex: list = []
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
                for tg in r.get("targets") or []:
                    if tg.get("status") in PICK_STATES:
                        pt["all"][tg["status"]] += 1
                        if tg.get("best"):
                            pt["best"][tg["status"]] += 1
                        if tg["status"] != "picked" and len(pex) < examples_per_key * 2:
                            pex.append({"log": d.get("log"), "advice_id": d.get("advice_id"), "turn": d.get("turn"),
                                        "id": tg.get("id"), "best": bool(tg.get("best")), "status": tg["status"]})
            mo[name] = {"n_eval": n_eval, "n_error": n_err, "n_violation_decisions": n_viol_dec,
                        "violations": v, "n_violations": sum(v.values()),
                        "n_legality": sum(v[k] for k in LEGALITY_LABELS), "n_identity": sum(v[k] for k in IDENTITY_LABELS),
                        "unconfirmed": u, "examples": ex, "n_unknown_decisions": n_unk_dec, "checks": ck,
                        "pick_targets": pt, "pick_examples": pex}
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


REF = "参考"                                 # 判定に使わない単位の行 (助言行単位の退避)


def _fallback_unit_rows(units: dict, lim: dict, add: Callable) -> None:
    """退避の決定単位 (各回と合算。1 回でも超えたら合算も 満たさない) と助言行単位 (参考) の行を足す (純粋。acceptance の一部)"""
    dec = units["decision"]
    rule = " → ".join(units.get("rule") or [])
    for lbl, key, nk, rk in (("予算超過", "budget", "n_budget", "budget_rate"), ("全理由", "all", "n_fallback", "rate")):
        over = []
        for lg, b in dec["by_log"].items():
            r = b[rk]
            st = UNKNOWN if r is None else (OK if r <= lim[key] else NG)
            if st == NG:
                over.append(f"{lg} {b[nk]} / {b['n']} ({r:.3f})")
            add(f"退避: {lbl} (transition、決定単位、回ごと)", f"transition / {lg}", r, lim[key], st,
                (PENDING_NOTE + "。" if st == NG else "") + f"{b[nk]} / {b['n']} 決定"
                + (f"、助言が見つからない決定 {b['n_unmatched']} (分母に入れない)" if b["n_unmatched"] else ""))
        a = dec["all"]
        r = a[rk]
        if r is not None and r > lim[key]:
            over.insert(0, f"合算 {a[nk]} / {a['n']} ({r:.3f})")
        st = NG if over else (UNKNOWN if r is None else OK)
        notes = ([f"{a[nk]} / {a['n']} 決定 (decision 行が参照する助言。規則 {rule}、使った規則 {a['rule_used']})"]
                 if a["n_decisions"] else ["transition の決定が無い (version 行の方式が transition の決定だけを数える)"])
        if a["n_unmatched"]:
            notes.append(f"助言が見つからない決定 {a['n_unmatched']} (分母に入れない)")
        if over:
            notes.insert(0, f"{PENDING_NOTE}: 基準超え {', '.join(over)}")
        add(f"退避: {lbl} (transition、決定単位)", "transition", r, lim[key], st, "合算。" + "。".join(notes))
        ar = units["advice_row"]["all"]
        add(f"退避: {lbl} (transition、助言行単位)", "transition", ar[rk], lim[key], REF,
            f"{ar[nk]} / {ar['n']} 行 (battle の advice 行、確定前・キャッシュを含む。参考、判定は決定単位)。回ごと: "
            + (", ".join(f"{lg} {f[nk]} / {f['n']}" for lg, f in units["advice_row"]["by_log"].items()) or "-"))


def acceptance(summary: dict, display: dict, record_check: dict, limits: Optional[dict] = None,
               group: Optional[dict] = None, units: Optional[dict] = None, data: Optional[dict] = None,
               inputs_pin: Optional[dict] = None) -> list:
    """受入条件 (§8.22) との照合 (純粋)。戻り値 [{"item", "mode", "value", "limit", "status" (満たす / 満たさない / 判定不能 /
    PARTIAL), "note"}]。group = 対象群の情報 {"target_fixed": bool, "by_mode": {方式: target_consistency}, "delta":
    target_consistency (served_commit / version_id)}。None なら版・設定の照合をしない (呼び出し側が持たないとき)。
    遅延の p95 は各回 (display[方式]["by_log"]) と合算の両方に基準を当て、どれか 1 回でも超えたら合算も 満たさない
    (注記の先頭に PENDING_NOTE)。対象群の版・設定が揃わなければ、超えた回が無いかぎり 判定不能。
    units = summarize_fallback_units の戻り値 (2026-10-20 レビュー)。あれば退避を 決定単位 (各回と合算の両方に上限、1 回でも
    超えたら合算も 満たさない) と 助言行単位 (参考) でも出し、再生の行は再生局面単位 (参考) と注記する。
    data = tools.inputs_pin.DataSwitch.summary() (2026-10-23)。あれば再生のデータの版の行 (data_row) を足す。
    inputs_pin = {ログ名: inputs_pin_rows の戻り値} (2026-10-31)。あれば実機の固定入力の異常の行 (inputs_pin_row) を足し、異常の記録が
    ある対戦を 1 本でも含めば、満たす / PARTIAL の行を 判定不能 にする (不成立。満たさない・参考はそのまま)"""
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
            f"{fb['n_budget'] if key == 'budget' else fb['n_fallback']} / {fb['n']} 決定"
            + ("。再生局面単位 (時間で間引いた決定点、参考。判定は決定単位の行)" if units is not None else ""))
    if units is not None:
        _fallback_unit_rows(units, lim, add)
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
    if data is not None:
        rows.append(data_row(data))
    if inputs_pin is not None:
        prow = inputs_pin_row(inputs_pin)
        if prow["status"] == UNKNOWN:
            for r in rows:
                if r["status"] in (OK, PARTIAL):
                    r["status"] = UNKNOWN
                    r["note"] = "不成立 (実機の固定入力の異常の記録がある対戦を含む)。" + (r["note"] or "")
        rows.append(prow)
    return rows


def inputs_pin_row(inputs_pin: dict) -> dict:
    """実機の固定入力の異常の行 (純粋。2026-10-31)。inputs_pin = {ログ名: inputs_pin_rows の戻り値}。
    異常の記録のある対戦が 1 本でもあれば 判定不能 (不成立)、無ければ 満たす"""
    item = "入力: 実機の固定入力 (data_pin) の異常の記録 (対戦ログ)"
    bad = {n: v for n, v in (inputs_pin or {}).items() if (v or {}).get("n")}
    n = len(inputs_pin or {})
    if bad:
        return {"item": item, "mode": "-", "value": f"{len(bad)} / {n} ログ", "limit": 0, "status": UNKNOWN,
                "note": "不成立: " + "; ".join(f"{k}: {inputs_pin_text(v)}" for k, v in bad.items())}
    return {"item": item, "mode": "-", "value": f"0 / {n} ログ", "limit": 0, "status": OK if n else UNKNOWN,
            "note": "" if n else "対象のログが無い"}


def inputs_pin_lines(inputs_pin: Optional[dict]) -> list:
    """report の「同条件の根拠」の固定入力の異常の節 (純粋。2026-10-31)。異常の記録のあるログだけを並べる"""
    bad = {n: v for n, v in (inputs_pin or {}).items() if (v or {}).get("n")}
    if inputs_pin is None:
        return []
    out = ["", "### 同条件の根拠 (実機の固定入力の異常)", ""]
    if not bad:
        return out + [f"- 異常の記録なし ({len(inputs_pin)} ログ)"]
    out.append("不成立 (判定不能): 次の対戦は助言が読む固定入力 (advisor.data_pin で版を明示したデータ) が使えず、server が助言を止めた。"
               "この対戦を含む群は同条件とみなさない")
    out += [f"- {n}: {inputs_pin_text(v)}" for n, v in bad.items()]
    return out


def data_row(data: dict) -> dict:
    """再生の入力のデータの版の行 (純粋。2026-10-23)。data = tools.inputs_pin.DataSwitch.summary()。
    restore: 全決定で当時の版を復元できれば 満たす、復元できない決定があれば 判定不能 (その決定は再生していない)。
    current: 参考 (今のデータで過去の局面を評価し直した。当時の入力ではない)"""
    item = "入力: 再生のデータの版 (使用率の snapshot・登録チーム・バンク)"
    n, nu = data.get("n_decisions", 0), data.get("n_unrestored_decisions", 0)
    if data.get("mode") == "current":
        loc = data.get("local") or {}
        return {"item": item, "mode": "current", "value": f"{n} 決定", "limit": None, "status": REF,
                "note": f"今のデータで過去の局面を評価し直した (当時の入力ではない): 使用率 snapshot {loc.get('latest_snapshot')}・"
                        f"バンク {', '.join(loc.get('bank_sha16') or []) or 'なし'}・登録チーム {loc.get('team_sha16')}"}
    reasons = sorted({r for v in (data.get("by_log") or {}).values() for x in v for r in x["reasons"]})
    return {"item": item, "mode": "restore", "value": f"{n - nu} / {n} 決定 (ログ {data.get('n_restored', 0)} / {data.get('n_logs', 0)})",
            "limit": None, "status": UNKNOWN if (nu or not n) else OK,
            "note": ("復元できない決定は再生していない: " + " / ".join(reasons)) if nu else ("" if n else "決定が無い")}


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


def _replay_settings_lines(c: dict) -> list:
    """条件の節: 再生の設定 (2026-10-20。version 行の設定に揃えたか)"""
    rc = c.get("replay_settings")
    if not rc:
        return []
    head = ("- 再生の設定 = version 行の設定 (時間・退避の比較を実機に揃える。採点一致の確認 --compare-* とは別)"
            if c.get("replay_settings_from_version") else
            "- 再生の設定: version 行に記録の無い欄は config の既定 (旧ログ。下の注記)")
    out = [head]
    for lg, ents in rc.items():
        for e in ents:
            src = e.get("source") or {}
            cfg = [k for k in ("transition_sensitivity", "display_search") if src.get(k) == "config"]
            out.append(f"  - {lg}: {settings_label(e)} — {e.get('n', 0)} 決定点"
                       + (f" (config の既定: {', '.join(cfg)}。version 行に記録なし)" if cfg else " (version 行)"))
    return out


def _group_basis_lines(tg: Optional[dict]) -> list:
    """同条件の根拠 (2026-10-20: 比べた版の識別の欄と値、実行状態の根拠)"""
    if not tg:
        return []
    out = []
    for mode, cons in (tg.get("by_mode") or {}).items():
        if not cons.get("basis") and not cons.get("runtime"):
            continue
        out += ["", f"### 同条件の根拠 ({mode_label(mode)}、版の識別と実行状態を分けて照合)", "",
                "| 欄 | 値 (全ログで同じ) |", "|---|---|"]
        out += [f"| {b['field']} | {str(b['value'])[:80]} |" for b in cons.get("basis") or []]
        diff = [f"{k}: {' / '.join(str(x)[:16] for x in v)}" for k, v in (cons.get("values") or {}).items() if len(v) > 1]
        if diff:
            out += ["", "揃わない欄: " + "; ".join(diff)]
        out += ["", "実行状態 (読み込み):", ""] + [f"- {lg}: {txt}" for lg, txt in (cons.get("runtime") or {}).items()]
        if cons.get("version_id"):
            out.append(f"- version_id: {' / '.join(cons['version_id'])} (比べない。実行状態を含む digest)")
    return out


def _pick_target_lines(s: dict) -> list:
    """正しさの節: 交代先ごとの 選出済み / 非選出確定 / 不明 (2026-10-20)"""
    if not any(s["modes"][m]["valid"].get("pick_targets") for m in MODES):
        return []
    out = ["", "交代先ごとの選出の判明 (有効な決定、第一候補 + 候補の交代先、決定ごとに重複なし。違反は 非選出確定 だけ、"
               "不明 はその照合だけ判定不能):", "",
           "| 交代先 | " + " | ".join(f"{m} " + " / ".join(PICK_STATE_LABELS[x] for x in PICK_STATES) for m in MODES) + " |",
           "|---|" + "---|" * len(MODES)]
    for w, lab in (("all", "全交代先"), ("best", "第一候補")):
        cells = []
        for m in MODES:
            pt = (s["modes"][m]["valid"].get("pick_targets") or {}).get(w) or {}
            cells.append(" / ".join(str(pt.get(x, 0)) for x in PICK_STATES))
        out.append(f"| {lab} | " + " | ".join(cells) + " |")
    ex = [e for m in MODES for e in (s["modes"][m]["valid"].get("pick_examples") or []) if m == "transition"]
    if ex:
        out += ["", "選出済みでない交代先の例 (transition):", ""] + [
            f"- {e['log']} {e['advice_id']} (turn {e.get('turn')}) {e['id']}{' (第一候補)' if e['best'] else ''}: "
            f"{PICK_STATE_LABELS[e['status']]}" for e in ex]
    return out


def _fallback_unit_lines(u: Optional[dict]) -> list:
    """退避の 3 単位と助言行・実計算の数え (2026-10-20)"""
    if not u:
        return []
    out = ["", "### 退避の単位 (transition)", "",
           f"決定単位 = decision 行が参照する助言 (規則 {' → '.join(u.get('rule') or [])}、開始前に固定。config "
           "REAL_ACCEPT_FALLBACK_DECISION_ADVICE)。各回と合算の両方に上限を当てる。助言行単位・再生局面単位は参考。", "",
           "| 単位 / 回 | 退避 (全理由) | 予算超過 | 理由 |", "|---|---|---|---|"]

    def ln(label, b, unit):
        return (f"| {label} | {b['n_fallback']} / {b['n']} {unit} ({_fmt(b['rate'])}) | {b['n_budget']} ({_fmt(b['budget_rate'])}) | "
                + (", ".join(f"{k} {v}" for k, v in b["by_reason"].items()) or "-") + " |")
    d = u["decision"]
    out.append(ln("**決定単位 合算**", d["all"], "決定"))
    out += [ln(f"決定単位 / {lg}", b, "決定") for lg, b in d["by_log"].items()]
    a = u["advice_row"]
    out.append(ln("助言行単位 合算 (確定前・キャッシュを含む)", a["all"], "行"))
    out += [ln(f"助言行単位 / {lg}", b, "行") for lg, b in a["by_log"].items()]
    if d["all"].get("n_unmatched"):
        out += ["", f"助言が見つからない決定 {d['all']['n_unmatched']} (分母に入れない)"]
    out += ["", "### 助言行と実計算の回数", "", "| ログ | 助言行 (battle、確定前を含む) | 決定 | 助言行 / 決定 | 実計算 / 決定 |",
            "|---|---|---|---|---|"]
    for lg, c in (u.get("counts") or {}).items():
        ev = (f"{c['n_evaluated']} / {c['n_decisions']} = {_fmt(c['evaluated_per_decision'], 2)}"
              + (f" (evaluated の欄 {c['n_with_evaluated']} / {c['n_rows']} 行)" if c["n_with_evaluated"] < c["n_rows"] else "")
              if c["has_evaluated"] else "evaluated の欄なし (助言行数はキャッシュを含む)")
        out.append(f"| {lg} | {c['n_rows']} (確定前 {c['n_provisional']}) | {c['n_decisions']} | "
                   f"{_fmt(c['rows_per_decision'], 2)} | {ev} |")
    return out


def render_markdown(result: dict) -> str:
    c, s, disp = result["conditions"], result["summary"], result["display"]
    L = [f"# 実機の受入: 実戦ログの同一局面の両方式再生 ({c['at']})", "",
         "採点方式の良し悪し (勝率) は判定しない。正しさ・退避・evaluate の所要時間と、実戦ログの表示の遅延 (その接続テストで使った方式) を見る。", "",
         "## 条件", "",
         f"- コード: {c['code'].get('commit') or '-'}{' (未コミットの変更あり)' if c['code'].get('dirty') else ''} / エンジン {c['code'].get('engine')}",
         f"- 方式: {' / '.join(c['modes'])} (RL 加点の重み {c['rl_blend_weight']}、RL_ADVICE_STYLE {c['rl_style']}、transition の予算 "
         f"{c['transition_budget_ms']} ms、{'config の既定の感度' if c.get('replay_settings') else '感度'} "
         f"{'あり' if c['transition_sensitivity'] else 'なし'})、据え置き・安定化ゲートは通さない"
         + (f"、表示用の探索 {c['display_search']} (ADVICE_DISPLAY_SEARCH)" if c.get("display_search") else ""),
         f"- 間引き {c['gap_sec']} 秒、古い局面 = hp_stale > {c['stale_hp_sec']} 秒 または解決側の場面の後",
         *_replay_settings_lines(c),
         replay_inputs_line(c.get("replay_inputs")),
         f"- 決定数: advice 行 (battle、確定) {c['n_advice_rows']} → 決定点 {s['n']} (有効 {s['n_valid']} / 古い局面 {s['n_stale']})"]
    tg = c.get("target")
    if tg is not None:
        L.append("- 対象: " + ("事前に決めた接続テスト群 (--target)" if tg.get("fixed") else
                              "事前に固定していない (--logs / --log で選んだログ。遅延の判定はその旨を注記)"))
        for mode, cons in (tg.get("by_mode") or {}).items():
            L.append(f"- 版・設定の照合 ({mode_label(mode)}): " + ("一致" if cons["ok"] else "揃わない")
                     + (" (" + "; ".join(cons["notes"]) + ")" if cons.get("notes") else ""))
    L += ["", "| ログ | advice 行 | 決定点 | decision 行 | 使った方式 (advice 行) | move_locks | state の版 | version_id | "
              "served_commit | scoring_mode (version 行) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for lg in c["logs"]:
        modes = ", ".join(f"{k} {v}" for k, v in lg["modes_used"].items()) or "-"
        v = lg.get("versions") or {}

        def j(xs, n=12):
            return ", ".join(str(x)[:n] for x in xs) if xs else "記録なし"
        sc = j(v.get("served_commit") or [], 10) + (" (client 行)" if v.get("served_commit_source") == "client" else "")
        L.append(f"| {lg['name']} | {lg['n_advice_rows']} | {lg['n_decisions']} | {lg['n_decision_rows']} | {modes} | "
                 f"{'あり' if lg['has_move_locks'] else 'なし'} | "
                 f"{', '.join(f'{k} {n}' for k, n in sorted((lg.get('state_schemas') or {}).items())) or '-'} | "
                 f"{j(v.get('version_id') or [])} | {sc} | "
                 f"{j(v.get('scoring_mode') or [])} |")
    L += ["", "## 受入条件との照合", "", "| 項目 | 方式 | 値 | 基準 | 判定 | 注記 |", "|---|---|---|---|---|---|"]
    for r in result["acceptance"]:
        L.append(f"| {r['item']} | {r['mode']} | {_fmt(r['value'])} | {_fmt(r['limit'])} | {r['status']} | {r['note']} |")
    L += _group_basis_lines(tg)
    L += inputs_pin_lines(c.get("inputs_pin"))
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
    L += _pick_target_lines(s)
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
    L += _fallback_unit_lines(result.get("fallback_units"))
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


# ------------------------------------------------------------------ 純粋: 表示用の探索 on / off の照合 (--compare-search、2026-10-16)
SEARCH_STATES = ("on", "off")                # 表示用の探索を実行する / 省略する (evaluate の display_search = True / False)
SEARCH_DIFF_LABELS = {
    "error": "どちらかが例外",
    "ok": "評価できたか (ok) が違う",
    "candidates": "候補の集合が違う",
    "scores": "同じ候補の得点が違う",
    "order": "候補の並び (順位) が違う",
    "best": "第一候補が違う",
    "legality": "第一候補の合法性 (check_best の違反・検査の結果) が違う",
    "fallback": "transition の退避 (使った方式・理由) が違う",
}


def pin_note(adv: Optional[dict]) -> Optional[str]:
    """evaluate の戻り値が固定入力の異常 (reason = INPUTS_PIN_REASON) なら error の文言、そうでなければ None (純粋。2026-10-31)"""
    if isinstance(adv, dict) and adv.get("ok") is False and adv.get("reason") == INPUTS_PIN_REASON:
        return str(adv.get("error") or INPUTS_PIN_REASON)
    return None


def pair_unconfirmed(on: dict, off: dict) -> Optional[str]:
    """on / off の照合ができない理由 (純粋。2026-10-31): どちらか一方でも固定入力の異常 (結果の "inputs_pin") なら
    INPUTS_PIN_REASON。照合できるなら None"""
    return INPUTS_PIN_REASON if ((on or {}).get("inputs_pin") or (off or {}).get("inputs_pin")) else None


def rank_signature(adv: Optional[dict]) -> Optional[dict]:
    """evaluate の戻り値のうち、表示用の探索 on / off で一致すべき部分 (純粋): 評価できたか・候補の並び [(種類, id, 名前, 得点)]・
    第一候補・使った方式・退避の理由。理由の文 (reason) と表示の注記は照合しない"""
    if adv is None:
        return None
    acts = [(a.get("kind"), a.get("id"), a.get("name"), a.get("score")) for a in (adv.get("actions") or [])]
    used = adv.get("scoring_used") or (adv.get("scoring_mode") if adv.get("ok") else None)
    return {"ok": bool(adv.get("ok")), "actions": acts, "best": _key(adv.get("best")), "scoring_used": used,
            "fallback": adv.get("scoring_fallback")}


def compare_search_pair(on: dict, off: dict) -> list:
    """1 決定・1 方式の探索 on / off の照合 (純粋)。on / off = {"sig": rank_signature の戻り値, "violations": check_best の違反,
    "checks": check_best の検査の結果 (任意), "error": 例外の文 or None}。戻り値は食い違いの種類 (SEARCH_DIFF_LABELS の鍵) の列で、
    空なら一致 (得点は同じ値、順位は同じ並び)"""
    if on.get("error") or off.get("error") or on.get("sig") is None or off.get("sig") is None:
        same_err = (on.get("error") or None) == (off.get("error") or None) and on.get("sig") is None and off.get("sig") is None
        return [] if same_err else ["error"]
    a, b = on["sig"], off["sig"]
    out = []
    if a["ok"] != b["ok"]:
        out.append("ok")
    ka = [(x[0], x[1]) for x in a["actions"]]
    kb = [(x[0], x[1]) for x in b["actions"]]
    if set(ka) != set(kb):
        out.append("candidates")
    sa = {(x[0], x[1]): (x[2], x[3]) for x in a["actions"]}
    sb = {(x[0], x[1]): (x[2], x[3]) for x in b["actions"]}
    if any(sa[k] != sb[k] for k in set(sa) & set(sb)):
        out.append("scores")
    if ka != kb:
        out.append("order")
    if a["best"] != b["best"]:
        out.append("best")
    if sorted(on.get("violations") or []) != sorted(off.get("violations") or []) or \
            (on.get("checks") or {}) != (off.get("checks") or {}):
        out.append("legality")
    if (a.get("scoring_used"), a.get("fallback")) != (b.get("scoring_used"), b.get("fallback")):
        out.append("fallback")
    return out


def summarize_search_compare(rows: list, examples_per_key: int = EXAMPLES_PER_KEY) -> dict:
    """--compare-search の集計 (純粋)。rows の各要素: {"log", "advice_id", "turn", "stale", "modes": {方式: {"on": 結果, "off": 結果,
    "diffs": compare_search_pair の戻り値}}}、結果 = {"elapsed_ms", "search_skipped", "sig", "violations", "checks", "error"}。
    方式ごとに 照合した決定数・不一致の決定数・種類別の件数と例・所要時間 (on / off の p50 / p95 と同じ決定の差 off − on)・
    transition の退避率 (on / off、予算超過は別)・探索を省略した決定の件数 (off で search_skipped = True = 択評価・起点注意なし) を出し、
    判定は 不一致 0 なら 満たす、1 件でもあれば 満たさない、照合した決定が無ければ 判定不能"""
    out = {"n": len(rows), "modes": {}}
    for mode in MODES:
        rs = [r for r in rows if (r.get("modes") or {}).get(mode)]
        n_unc = sum(1 for r in rs if r["modes"][mode].get("unconfirmed"))      # 照合できない決定 (固定入力の異常、2026-10-31)
        by = {k: 0 for k in SEARCH_DIFF_LABELS}
        ex = []
        n_mis = 0
        el = {st: [] for st in SEARCH_STATES}
        delta = []
        skipped = {st: 0 for st in SEARCH_STATES}
        fb = {st: {"n": 0, "n_fallback": 0, "n_budget": 0} for st in SEARCH_STATES}
        for r in rs:
            m = r["modes"][mode]
            diffs = m.get("diffs") or []
            if diffs:
                n_mis += 1
                for k in diffs:
                    by[k] = by.get(k, 0) + 1
                if len(ex) < examples_per_key:
                    ex.append({"log": r.get("log"), "advice_id": r.get("advice_id"), "turn": r.get("turn"), "diffs": diffs,
                               **{st: {"best": (m[st].get("sig") or {}).get("best"),
                                       "top": [list(x) for x in ((m[st].get("sig") or {}).get("actions") or [])[:3]],
                                       "fallback": (m[st].get("sig") or {}).get("fallback"), "error": m[st].get("error")}
                                  for st in SEARCH_STATES}})
            for st in SEARCH_STATES:
                x = m.get(st) or {}
                if x.get("elapsed_ms") is not None:
                    el[st].append(float(x["elapsed_ms"]))
                if x.get("search_skipped"):
                    skipped[st] += 1
                sig = x.get("sig") or {}
                if mode == "transition" and not x.get("error") and sig.get("ok"):
                    fb[st]["n"] += 1
                    if sig.get("fallback"):
                        fb[st]["n_fallback"] += 1
                        fb[st]["n_budget"] += _reason_key(sig["fallback"]) == BUDGET_REASON
            if all((m.get(st) or {}).get("elapsed_ms") is not None for st in SEARCH_STATES):
                delta.append(float(m["off"]["elapsed_ms"]) - float(m["on"]["elapsed_ms"]))
        for st in SEARCH_STATES:
            fb[st]["rate"] = _rate(fb[st]["n_fallback"], fb[st]["n"])
            fb[st]["budget_rate"] = _rate(fb[st]["n_budget"], fb[st]["n"])
        status = NG if n_mis else (UNKNOWN if (len(rs) - n_unc <= 0 or n_unc) else OK)
        out["modes"][mode] = {"n": len(rs) - n_unc, "n_unconfirmed": n_unc, "n_mismatch": n_mis, "by_kind": by, "examples": ex,
                              "status": status,
                              "elapsed_ms": {st: percentiles(el[st]) for st in SEARCH_STATES},
                              "elapsed_delta_ms": percentiles(delta), "n_search_skipped": skipped,
                              "fallback": fb if mode == "transition" else None}
    sts = [mo["status"] for mo in out["modes"].values()]
    out["status"] = NG if NG in sts else (UNKNOWN if UNKNOWN in sts or not sts else OK)
    out["n_mismatch"] = sum(mo["n_mismatch"] for mo in out["modes"].values())
    out["n_unconfirmed"] = sum(mo["n_unconfirmed"] for mo in out["modes"].values())
    return out


def render_search_compare_markdown(result: dict) -> str:
    """--compare-search の report.md (純粋)"""
    c, sm = result["conditions"], result["summary"]
    L = [f"# 表示用の探索 on / off の照合: 実戦ログの同一局面 ({c['at']})", "",
         "表示用の探索 (advisor.engine._run_search、択評価・起点注意) を省略しても、候補・得点・順位・第一候補の合法性が変わらないことを"
         "決定ごとに確かめる。不一致が 1 件でもあれば 満たさない。", "",
         "## 条件", "",
         f"- コード: {c['code'].get('commit') or '-'}{' (未コミットの変更あり)' if c['code'].get('dirty') else ''} / エンジン {c['code'].get('engine')}",
         f"- 方式: {' / '.join(c['modes'])} (RL 加点の重み {c['rl_blend_weight']}、transition の予算 {c['transition_budget_ms']} ms、"
         f"感度 {'あり' if c['transition_sensitivity'] else 'なし'})、探索の重み (SEARCH_BLEND) {c['search_blend']}、"
         f"設定 ADVICE_DISPLAY_SEARCH = {c['display_search']}",
         f"- 決定点 {sm['n']} (間引き {c['gap_sec']} 秒、古い局面も含む全決定点)。決定ごとに 方式 × on / off の 4 通りを、"
         "決定ごとに順番を逆にして evaluate する (キャッシュの偏りを均す)",
         replay_inputs_line(c.get("replay_inputs")),
         "- ログ: " + ", ".join(f"{lg['name']} ({lg['n_decisions']})" for lg in c["logs"]), "",
         "## 照合の結果", "", "| 方式 | 照合した決定 | 不一致の決定 | 判定 | 種類別 |", "|---|---|---|---|---|"]
    for mode, mo in sm["modes"].items():
        kinds = ", ".join(f"{SEARCH_DIFF_LABELS.get(k, k)} {v}" for k, v in mo["by_kind"].items() if v) or "-"
        L.append(f"| {mode} | {mo['n']} | {mo['n_mismatch']} | {mo['status']} | {kinds} |")
    L.append(f"| 総合 | {sm['n']} | {sm['n_mismatch']} | {sm['status']} | |")
    if sm.get("n_unconfirmed"):
        L += ["", f"照合できない決定 (固定入力の異常。on / off のどちらかが reason = {INPUTS_PIN_REASON}、一致にも不一致にも数えない): "
                  + ", ".join(f"{m} {mo.get('n_unconfirmed', 0)}" for m, mo in sm["modes"].items()) + "。判定は 判定不能"]
    L += ["", "## 所要時間 (evaluate、全決定点)", "", "| 方式 | 探索 on | 探索 off | 差 (同じ決定の off − on) |", "|---|---|---|---|"]
    for mode, mo in sm["modes"].items():
        L.append(f"| {mode} | {_pct_txt(mo['elapsed_ms']['on'])} | {_pct_txt(mo['elapsed_ms']['off'])} | "
                 f"{_pct_txt(mo['elapsed_delta_ms'])} |")
    L += ["", "## transition の退避率", "", "| 探索 | 評価 | 退避 (全理由) | 予算超過 |", "|---|---|---|---|"]
    fbs = (sm["modes"].get("transition") or {}).get("fallback") or {}
    for st in SEARCH_STATES:
        f = fbs.get(st) or {}
        L.append(f"| {st} | {f.get('n', 0)} | {f.get('n_fallback', 0)} ({_fmt(f.get('rate'))}) | "
                 f"{f.get('n_budget', 0)} ({_fmt(f.get('budget_rate'))}) |")
    L += ["", "## 探索を省略した決定 (択評価・起点注意なし)", "", "| 方式 | 探索 on で省略 | 探索 off で省略 |", "|---|---|---|"]
    for mode, mo in sm["modes"].items():
        L.append(f"| {mode} | {mo['n_search_skipped']['on']} | {mo['n_search_skipped']['off']} |")
    L += ["", "探索 off で省略した決定では、助言の表示に「🎲 択評価」と「⚠ 起点注意」が出ない (advice の gtheory = null、"
          "search_skipped = true)。探索の重み > 0 の設定では省略を認めないので、off でも省略 0 になる。"]
    exs = [(mode, e) for mode, mo in sm["modes"].items() for e in mo["examples"]]
    if exs:
        L += ["", "## 不一致の例", ""]
        for mode, e in exs:
            L.append(f"- {mode} {e['log']} {e['advice_id']} (ターン {e.get('turn')}): "
                     + ", ".join(SEARCH_DIFF_LABELS.get(k, k) for k in e["diffs"])
                     + f" / on {e['on']['best']} {e['on']['top']} 退避 {e['on']['fallback']}"
                     + f" / off {e['off']['best']} {e['off']['top']} 退避 {e['off']['fallback']}")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 純粋: transition の感度 on / off の照合 (--compare-sensitivity、2026-10-18)
SENS_STATES = SEARCH_STATES                  # 感度を計算する / しない (config.TRANSITION_SENSITIVITY_ENABLED = True / False)
SENS_MODE = "transition"                     # 感度を使うのは transition だけ
SENS_DIFF_LABELS = {**SEARCH_DIFF_LABELS, "q": "同じ候補の Q / Q_worst (本採点の値) が違う"}


def q_signature(adv: Optional[dict]) -> Optional[list]:
    """transition の本採点の値 [(種類, id, q, q_worst)] (純粋。transition で評価していない・退避した助言は None)"""
    if adv is None or not adv.get("ok") or adv.get("scoring_used") != SENS_MODE:
        return None
    return [(a.get("kind"), a.get("id"), a.get("q"), a.get("q_worst")) for a in (adv.get("actions") or [])]


def compare_sensitivity_pair(on: dict, off: dict) -> list:
    """1 決定の感度 on / off の照合 (純粋)。on / off = compare_search_pair と同じ形 + "q" (q_signature の戻り値)。
    戻り値は食い違いの種類 (SENS_DIFF_LABELS の鍵) の列で、空なら一致"""
    out = compare_search_pair(on, off)
    if "error" not in out and on.get("q") != off.get("q"):
        out.append("q")
    return out


def summarize_sensitivity_compare(rows: list, examples_per_key: int = EXAMPLES_PER_KEY) -> dict:
    """--compare-sensitivity の集計 (純粋)。rows の各要素: {"log", "advice_id", "turn", "on": 結果, "off": 結果, "diffs":
    compare_sensitivity_pair の戻り値}、結果 = {"elapsed_ms", "sig", "q", "violations", "checks", "error", "sens_status"}。
    照合した決定数・不一致の決定数・種類別の件数と例・所要時間 (on / off と差 off − on)・退避率 (on / off)・感度の状態の件数を出し、
    判定は 不一致 0 なら 満たす、1 件でもあれば 満たさない、照合した決定が無ければ 判定不能"""
    by = {k: 0 for k in SENS_DIFF_LABELS}
    ex = []
    n_mis = 0
    el = {st: [] for st in SENS_STATES}
    delta = []
    fb = {st: {"n": 0, "n_fallback": 0, "n_budget": 0} for st in SENS_STATES}
    status = {st: {} for st in SENS_STATES}
    n_unc = sum(1 for r in rows if r.get("unconfirmed"))       # 照合できない決定 (固定入力の異常、2026-10-31)
    for r in rows:
        diffs = r.get("diffs") or []
        if diffs:
            n_mis += 1
            for k in diffs:
                by[k] = by.get(k, 0) + 1
            if len(ex) < examples_per_key:
                ex.append({"log": r.get("log"), "advice_id": r.get("advice_id"), "turn": r.get("turn"), "diffs": diffs,
                           **{st: {"best": ((r.get(st) or {}).get("sig") or {}).get("best"),
                                   "top": [list(x) for x in (((r.get(st) or {}).get("sig") or {}).get("actions") or [])[:3]],
                                   "fallback": ((r.get(st) or {}).get("sig") or {}).get("fallback"),
                                   "error": (r.get(st) or {}).get("error")} for st in SENS_STATES}})
        for st in SENS_STATES:
            x = r.get(st) or {}
            if x.get("elapsed_ms") is not None:
                el[st].append(float(x["elapsed_ms"]))
            sig = x.get("sig") or {}
            if not x.get("error") and sig.get("ok"):
                fb[st]["n"] += 1
                if sig.get("fallback"):
                    fb[st]["n_fallback"] += 1
                    fb[st]["n_budget"] += _reason_key(sig["fallback"]) == BUDGET_REASON
            k = str(x.get("sens_status"))
            status[st][k] = status[st].get(k, 0) + 1
        if all((r.get(st) or {}).get("elapsed_ms") is not None for st in SENS_STATES):
            delta.append(float(r["off"]["elapsed_ms"]) - float(r["on"]["elapsed_ms"]))
    for st in SENS_STATES:
        fb[st]["rate"] = _rate(fb[st]["n_fallback"], fb[st]["n"])
        fb[st]["budget_rate"] = _rate(fb[st]["n_budget"], fb[st]["n"])
    return {"n": len(rows) - n_unc, "n_unconfirmed": n_unc, "n_mismatch": n_mis, "by_kind": by, "examples": ex,
            "status": NG if n_mis else (UNKNOWN if (len(rows) - n_unc <= 0 or n_unc) else OK),
            "elapsed_ms": {st: percentiles(el[st]) for st in SENS_STATES}, "elapsed_delta_ms": percentiles(delta),
            "fallback": fb, "sens_status": status}


def render_sensitivity_compare_markdown(result: dict) -> str:
    """--compare-sensitivity の report.md (純粋)"""
    c, sm = result["conditions"], result["summary"]
    kinds = ", ".join(f"{SENS_DIFF_LABELS.get(k, k)} {v}" for k, v in sm["by_kind"].items() if v) or "-"
    L = [f"# transition の感度 on / off の照合: 実戦ログの同一局面 ({c['at']})", "",
         "感度 (楽観側の HP・スカーフ / タスキの有無の仮説、順位には使わない) を計算しなくても、本採点の候補・得点・Q・順位・"
         "第一候補の合法性・退避の理由が変わらないことを決定ごとに確かめる。不一致が 1 件でもあれば 満たさない。", "",
         "## 条件", "",
         f"- コード: {c['code'].get('commit') or '-'}{' (未コミットの変更あり)' if c['code'].get('dirty') else ''} / エンジン {c['code'].get('engine')}",
         f"- 方式: transition (予算 {c['transition_budget_ms']} ms)、設定 TRANSITION_SENSITIVITY_ENABLED = {c['transition_sensitivity']} "
         f"(照合では on / off を切り替える)、ADVICE_DISPLAY_SEARCH = {c.get('display_search')}",
         f"- 決定点 {sm['n']} (間引き {c['gap_sec']} 秒、古い局面も含む全決定点)。決定ごとに on / off の順番を交互にする",
         replay_inputs_line(c.get("replay_inputs")),
         "- ログ: " + ", ".join(f"{lg['name']} ({lg['n_decisions']})" for lg in c["logs"]), "",
         "## 照合の結果", "", "| 照合した決定 | 不一致の決定 | 判定 | 種類別 |", "|---|---|---|---|",
         f"| {sm['n']} | {sm['n_mismatch']} | {sm['status']} | {kinds} |", "",
         *([f"照合できない決定 (固定入力の異常。on / off のどちらかが reason = {INPUTS_PIN_REASON}、一致にも不一致にも数えない): "
            f"{sm['n_unconfirmed']}。判定は 判定不能", ""] if sm.get("n_unconfirmed") else []),
         "## 所要時間 (evaluate、全決定点)", "", "| 感度 on | 感度 off | 差 (同じ決定の off − on) |", "|---|---|---|",
         f"| {_pct_txt(sm['elapsed_ms']['on'])} | {_pct_txt(sm['elapsed_ms']['off'])} | {_pct_txt(sm['elapsed_delta_ms'])} |", "",
         "## 退避率", "", "| 感度 | 評価 | 退避 (全理由) | 予算超過 |", "|---|---|---|---|"]
    for st in SENS_STATES:
        f = sm["fallback"].get(st) or {}
        L.append(f"| {st} | {f.get('n', 0)} | {f.get('n_fallback', 0)} ({_fmt(f.get('rate'))}) | "
                 f"{f.get('n_budget', 0)} ({_fmt(f.get('budget_rate'))}) |")
    L += ["", "## 感度の状態 (advice 行の sensitivity_status と同じ分類)", "", "| 感度 | 状態の件数 |", "|---|---|"]
    for st in SENS_STATES:
        L.append(f"| {st} | " + ", ".join(f"{k} {v}" for k, v in sorted(sm["sens_status"][st].items())) + " |")
    if sm["examples"]:
        L += ["", "## 不一致の例", ""]
        for e in sm["examples"]:
            L.append(f"- {e['log']} {e['advice_id']} (ターン {e.get('turn')}): "
                     + ", ".join(SENS_DIFF_LABELS.get(k, k) for k in e["diffs"])
                     + f" / on {e['on']['best']} {e['on']['top']} 退避 {e['on']['fallback']}"
                     + f" / off {e['off']['best']} {e['off']['top']} 退避 {e['off']['fallback']}")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 副作用あり: 読み込み・再生・出力
def state_schema_counts(rows: list) -> dict:
    """advice 行の state の版ごとの行数 (純粋)。{"v1": n, "v2": m}"""
    from battle_logger import state_schema_of
    out: dict = {}
    for r in rows:
        k = f"v{state_schema_of(r.get('state'))}"
        out[k] = out.get(k, 0) + 1
    return out


def load_records(path: Path) -> list:
    """対戦ログの記録の列 (壊れた行・dict でない行は除く)。飛ばした行も要るときは load_records_report"""
    return load_records_report(path)[0]


def load_records_report(path: Path) -> tuple:
    """(記録の列, 壊れた行 [{"line", "head"}]) (副作用: 読み取り。2026-10-31 E-inh-4: 壊れた行を黙って飛ばしていた。
    _load_logs が件数をログごとの bad_lines と conditions.bad_lines に出す)"""
    from tools.jsonl_lines import read_jsonl
    return read_jsonl(path)


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


def _engine_state_ex(row: dict, resolver) -> tuple:
    """advice 行 1 行だけから再生の入力を作る (同じ対戦の後の行・scene 行からは補わない = 後から判明した情報を混ぜない)。
    now = その行の t_eval → t_now → t_gen → t (replay_now。現在の時計は使わない)、行に ev_infer があれば型推定を入れる
    (tools.advice_replay.replay_state、2026-10-21 レビュー: 通常の再生・--compare-search・--compare-sensitivity のすべて)。
    戻り値 (state, {"now_source", "ev_infer_applied"})"""
    from tools.advice_replay import replay_state
    c = row["state"]
    st, info = replay_state(row, resolver, row.get("move_locks"))
    st["scene"] = c.get("scene")
    if st.get("turn") is None:
        st["turn"] = row.get("turn")
    return st, info


def _engine_state(row: dict, resolver) -> dict:
    """_engine_state_ex の state だけ"""
    return _engine_state_ex(row, resolver)[0]


def restore_check(row: dict) -> tuple:
    """状態の復元の照合 (純粋): (unconfirmed に足す鍵の列, checks["state_restore"] の値 ok / unknown)"""
    ru = restore_unknowns(row)
    return ru, ("unknown" if ru else "ok")


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


def replay_inputs_line(ri: Optional[dict]) -> str:
    """条件の節: 再生の入力の now の出所と型推定の適用の件数 (純粋。2026-10-21)"""
    if not ri:
        return "- 再生の入力: 記録なし"
    src = ", ".join(f"{k} {v}" for k, v in sorted(ri.get("now_source", {}).items())) or "-"
    return (f"- 再生の入力 (決定点 {ri.get('n', 0)}): now の出所 {src} (t_eval → t_now → t_gen → t)、"
            f"型推定 (advice 行の ev_infer) を適用 {ri.get('ev_infer_applied', 0)}")


def _slim_best(b: Optional[dict]) -> Optional[dict]:
    return None if not b else {"kind": b.get("kind"), "id": b.get("id"), "name": b.get("name")}


def replay_one(state: dict, resolver, order: tuple, settings: Optional[dict] = None) -> dict:
    """1 決定点を order の順に各方式で evaluate する (副作用: エンジン)。{方式: (戻り値 or None, 所要 ms, エラー)}。
    settings = replay_settings の戻り値 (2026-10-20: 実機の version 行の設定に揃える)。あれば感度
    (config.TRANSITION_SENSITIVITY_ENABLED を一時的に切り替えて戻す) と表示用の探索 (evaluate の display_search) をその値にする。
    None なら config の既定のまま"""
    from advisor.engine import evaluate
    from champions_agent import config as C
    out = {}
    orig = C.TRANSITION_SENSITIVITY_ENABLED
    try:
        if settings is not None:
            C.TRANSITION_SENSITIVITY_ENABLED = bool(settings["transition_sensitivity"])
        ds = None if settings is None else bool(settings["display_search"])
        for mode in order:
            s = copy.deepcopy(state)
            t0 = time.perf_counter()
            try:
                adv = evaluate(s, resolver, scoring_mode=mode, display_search=ds)
                err = None
            except Exception as e:
                adv, err = None, f"{type(e).__name__}: {e}"
            out[mode] = (adv, round((time.perf_counter() - t0) * 1000.0, 1), err)
    finally:
        C.TRANSITION_SENSITIVITY_ENABLED = orig
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


def replay_search_pair(state: dict, resolver, order: tuple) -> dict:
    """1 決定点を order (方式, 探索 on / off) の順に evaluate する (副作用: エンジン)。
    {方式: {"on" / "off": (戻り値 or None, 所要 ms, エラー)}}"""
    from advisor.engine import evaluate
    out: dict = {}
    for mode, st in order:
        s = copy.deepcopy(state)
        t0 = time.perf_counter()
        try:
            adv = evaluate(s, resolver, scoring_mode=mode, display_search=(st == "on"))
            err = None
        except Exception as e:
            adv, err = None, f"{type(e).__name__}: {e}"
        out.setdefault(mode, {})[st] = (adv, round((time.perf_counter() - t0) * 1000.0, 1), err)
    return out


def run_compare_search(logs: list, out_dir: Optional[Path], gap_sec: float, target_fixed: bool,
                       data_mode: str = "restore") -> dict:
    """--compare-search の本体 (副作用: エンジン・出力)。logs = _load_logs の戻り値。data_mode = 再生のデータの版 (tools.inputs_pin)"""
    from vision.normalize import NameResolver
    import advisor.engine as E
    from champions_agent import config as C
    from tools.inputs_pin import DataSwitch
    resolver = NameResolver()
    ck = _checker_kwargs()
    ds = DataSwitch(data_mode)
    combos = tuple((m, st) for m in MODES for st in SEARCH_STATES)
    first = _first_restorable(logs, ds)
    if first is not None:      # 初回の読み込み (図鑑・RL・使用率) を所要時間から外す
        replay_search_pair(_engine_state(first, resolver), resolver, combos)
    n_dec = sum(lg["n_decisions"] for lg in logs)
    rows = []
    infos = []
    i = 0
    for lg in logs:
        timeline = scene_timeline(lg["records"])
        for row in lg["decisions"]:
            compact = row["state"]
            if not ds.plan(lg["name"], lg["records"], row.get("t"))["ok"]:
                continue
            try:
                st, info = _engine_state_ex(row, resolver)
            except Exception as e:
                print(f"  復元できない: {lg['name']} {row.get('advice_id')}: {e}")
                continue
            infos.append(info)
            reg = _registered_ids(st, resolver)
            order = combos if i % 2 == 0 else tuple(reversed(combos))
            i += 1
            res = replay_search_pair(st, resolver, order)
            d = {"log": lg["name"], "advice_id": row.get("advice_id"), "t": row.get("t"), "turn": row.get("turn"),
                 "active": _active_label(compact), **info,
                 "stale": stale_reasons(row, scene_before(timeline, float(row.get("t") or 0.0))), "modes": {}}
            for mode, pair in res.items():
                m = {}
                for stt, (adv, ms, err) in pair.items():
                    x = {"elapsed_ms": ms, "error": err, "sig": None, "violations": [], "checks": {},
                         "search_skipped": None, "inputs_pin": pin_note(adv)}
                    if adv is not None and not err and not x["inputs_pin"]:
                        x["sig"] = rank_signature(adv)
                        x["search_skipped"] = adv.get("search_skipped")
                        if adv.get("ok"):
                            chk = check_best(adv, compact, move_locks_for_check(row), reg, detail=True, **ck)
                            x["violations"], x["checks"] = chk["violations"], chk["checks"]
                    m[stt] = x
                m["unconfirmed"] = pair_unconfirmed(m["on"], m["off"])
                m["diffs"] = [] if m["unconfirmed"] else compare_search_pair(m["on"], m["off"])
                d["modes"][mode] = m
            rows.append(d)
            if i % 20 == 0:
                print(f"  {i} / {n_dec}")
    summary = summarize_search_compare(rows)
    result = {
        "conditions": {"at": time.strftime("%Y-%m-%d %H:%M"), "code": _code_info(), "modes": list(MODES),
                       "rl_blend_weight": E.RL_BLEND_DEFAULT, "transition_budget_ms": C.TRANSITION_BUDGET_MS,
                       "transition_sensitivity": C.TRANSITION_SENSITIVITY_ENABLED, "search_blend": E.SEARCH_BLEND,
                       "display_search": C.ADVICE_DISPLAY_SEARCH, "gap_sec": gap_sec, "target_fixed": target_fixed,
                       "replay_inputs": replay_input_counts(infos), "data_mode": data_mode, "data_versions": ds.summary(),
                       "logs": [{k: v for k, v in lg.items() if k not in LOG_INTERNAL_KEYS}
                                for lg in logs]},
        "summary": summary, "decisions": rows,
    }
    ds.close()
    out_dir = Path(out_dir) if out_dir else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = render_search_compare_markdown(result) + data_versions_markdown(result["conditions"]["data_versions"])
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"保存: {out_dir}")
    return result


def replay_sensitivity_pair(state: dict, resolver, order: tuple) -> dict:
    """1 決定点を transition で order (感度 on / off) の順に evaluate する (副作用: エンジン、config.TRANSITION_SENSITIVITY_ENABLED を
    一時的に切り替えて戻す)。{"on" / "off": (戻り値 or None, 所要 ms, エラー)}"""
    from advisor.engine import evaluate
    from champions_agent import config as C
    out: dict = {}
    orig = C.TRANSITION_SENSITIVITY_ENABLED
    try:
        for st in order:
            C.TRANSITION_SENSITIVITY_ENABLED = (st == "on")
            s = copy.deepcopy(state)
            t0 = time.perf_counter()
            try:
                adv = evaluate(s, resolver, scoring_mode=SENS_MODE)
                err = None
            except Exception as e:
                adv, err = None, f"{type(e).__name__}: {e}"
            out[st] = (adv, round((time.perf_counter() - t0) * 1000.0, 1), err)
    finally:
        C.TRANSITION_SENSITIVITY_ENABLED = orig
    return out


def run_compare_sensitivity(logs: list, out_dir: Optional[Path], gap_sec: float, target_fixed: bool,
                            data_mode: str = "restore") -> dict:
    """--compare-sensitivity の本体 (副作用: エンジン・出力)。logs = _load_logs の戻り値。data_mode = 再生のデータの版 (tools.inputs_pin)"""
    from vision.normalize import NameResolver
    from battle_logger import sensitivity_fields_of
    from champions_agent import config as C
    from tools.inputs_pin import DataSwitch
    resolver = NameResolver()
    ck = _checker_kwargs()
    ds = DataSwitch(data_mode)
    first = _first_restorable(logs, ds)
    if first is not None:      # 初回の読み込み (図鑑・RL・使用率) を所要時間から外す
        replay_sensitivity_pair(_engine_state(first, resolver), resolver, SENS_STATES)
    n_dec = sum(lg["n_decisions"] for lg in logs)
    rows = []
    infos = []
    i = 0
    for lg in logs:
        for row in lg["decisions"]:
            compact = row["state"]
            if not ds.plan(lg["name"], lg["records"], row.get("t"))["ok"]:
                continue
            try:
                st, info = _engine_state_ex(row, resolver)
            except Exception as e:
                print(f"  復元できない: {lg['name']} {row.get('advice_id')}: {e}")
                continue
            infos.append(info)
            reg = _registered_ids(st, resolver)
            order = SENS_STATES if i % 2 == 0 else tuple(reversed(SENS_STATES))
            i += 1
            res = replay_sensitivity_pair(st, resolver, order)
            d = {"log": lg["name"], "advice_id": row.get("advice_id"), "t": row.get("t"), "turn": row.get("turn"),
                 "active": _active_label(compact), **info}
            for stt, (adv, ms, err) in res.items():
                x = {"elapsed_ms": ms, "error": err, "sig": None, "q": None, "violations": [], "checks": {},
                     "sens_status": None, "inputs_pin": pin_note(adv)}
                if adv is not None and not err and not x["inputs_pin"]:
                    x["sig"] = rank_signature(adv)
                    x["q"] = q_signature(adv)
                    x["sens_status"] = sensitivity_fields_of(adv)["sensitivity_status"]
                    if adv.get("ok"):
                        chk = check_best(adv, compact, move_locks_for_check(row), reg, detail=True, **ck)
                        x["violations"], x["checks"] = chk["violations"], chk["checks"]
                d[stt] = x
            d["unconfirmed"] = pair_unconfirmed(d["on"], d["off"])
            d["diffs"] = [] if d["unconfirmed"] else compare_sensitivity_pair(d["on"], d["off"])
            rows.append(d)
            if i % 20 == 0:
                print(f"  {i} / {n_dec}")
    summary = summarize_sensitivity_compare(rows)
    result = {
        "conditions": {"at": time.strftime("%Y-%m-%d %H:%M"), "code": _code_info(), "mode": SENS_MODE,
                       "transition_budget_ms": C.TRANSITION_BUDGET_MS,
                       "transition_sensitivity": C.TRANSITION_SENSITIVITY_ENABLED,
                       "display_search": getattr(C, "ADVICE_DISPLAY_SEARCH", None), "gap_sec": gap_sec,
                       "target_fixed": target_fixed, "replay_inputs": replay_input_counts(infos),
                       "data_mode": data_mode, "data_versions": ds.summary(),
                       "logs": [{k: v for k, v in lg.items() if k not in LOG_INTERNAL_KEYS}
                                for lg in logs]},
        "summary": summary, "decisions": rows,
    }
    out_dir = Path(out_dir) if out_dir else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    ds.close()
    md = render_sensitivity_compare_markdown(result) + data_versions_markdown(result["conditions"]["data_versions"])
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"保存: {out_dir}")
    return result


def replay_pin_result(adv: dict, elapsed_ms) -> dict:
    """再生の evaluate が固定入力の異常 (reason = INPUTS_PIN_REASON) を返した方式の結果 (純粋。2026-10-31): 照合しない
    (unconfirmed data_restore、checks data_restore unknown。unrestored_decision と同じ数え方)。理由は data_reasons"""
    return {"ok": False, "best": None, "elapsed_ms": elapsed_ms, "scoring_used": None, "fallback_reason": None,
            "reason": INPUTS_PIN_REASON, "violations": [], "unconfirmed": ["data_restore"], "checks": {"data_restore": "unknown"},
            "targets": [], "data_reasons": [f"{INPUTS_PIN_REASON}: {(adv or {}).get('error')}"]}


def unrestored_decision(log_name: str, row: dict, compact: dict, plan: dict) -> dict:
    """当時のデータの版を復元できない決定 (純粋。2026-10-23): 再生せず、両方式とも照合できない決定 (unconfirmed data_restore、
    checks data_restore unknown) にする。第一候補・所要時間は無し"""
    res = {"ok": False, "best": None, "elapsed_ms": None, "scoring_used": None, "fallback_reason": None, "reason": None,
           "violations": [], "unconfirmed": ["data_restore"], "checks": {"data_restore": "unknown"}, "targets": [],
           "data_reasons": list(plan.get("reasons") or [])}
    return {"log": log_name, "advice_id": row.get("advice_id"), "t": row.get("t"), "turn": row.get("turn"),
            "scene": compact.get("scene"), "active": _active_label(compact), "replay_settings": None, "now_source": None,
            "ev_infer_applied": None, "stale": [], "log_best": _slim_best((row.get("advice") or {}).get("best")),
            "log_mode": advice_mode(row)[0], "modes": {m: dict(res) for m in MODES}, "data_restore": False}


def _first_restorable(logs: list, ds) -> Optional[dict]:
    """初回の読み込みの空回しに使う決定 (版を差し替える。要約には数えない)。復元できる決定が無ければ None"""
    for lg in logs:
        for row in lg["decisions"]:
            if ds.plan(lg["name"], lg["records"], row.get("t"), record=False)["ok"]:
                return row
    return None


def data_versions_markdown(dv: Optional[dict]) -> str:
    """再生のデータの版の節 (純粋。2026-10-23)"""
    if not dv:
        return ""
    loc = dv.get("local") or {}
    L = ["", "## 再生のデータの版 (--data-mode " + str(dv.get("mode")) + ")", "",
         f"手元: 使用率 DB {loc.get('db')} (最新 snapshot {loc.get('latest_snapshot')}) / バンク {', '.join(loc.get('bank_sha16') or []) or 'なし'}"
         f" / 登録チーム {loc.get('team_sha16')}"
         + (f" / 登録 (my_team) {loc.get('team_registry_sha16')}" if loc.get("team_registry_sha16") else ""),
         f"決定 {dv.get('n_decisions', 0)} (復元できない {dv.get('n_unrestored_decisions', 0)}) / ログ {dv.get('n_logs', 0)} "
         f"(全決定を復元 {dv.get('n_restored', 0)})", "",
         "| ログ | 決定 | 当時の版 (snapshot / 登録 / バンク) | 使った版 | 結果 |", "|---|---|---|---|---|"]
    for name, items in (dv.get("by_log") or {}).items():
        for x in items:
            rec, used = x.get("recorded") or {}, x.get("used") or {}
            res = "復元" if x["ok"] and dv.get("mode") == "restore" else ("今のデータ" if x["ok"] else "判定不能: " + " / ".join(x["reasons"]))
            if x.get("notes"):
                res += " (" + " / ".join(x["notes"]) + ")"
            team_used = str(used.get("team_sha16"))
            if "team_registry_sha16" in used:      # version 行の team.registry で登録を差し替えた (2026-10-23)
                team_used += f" (my_team {used.get('team_registry_sha16')})"
            L.append(f"| {name} | {x.get('n')} | {rec.get('usage_snapshot')} / {rec.get('team_sha16')} / {rec.get('bank_sha16')} | "
                     f"{used.get('usage_snapshot')} / {team_used} / {used.get('bank_sha16')} | {res} |")
    return "\n".join(L) + "\n"


def _load_logs(paths: list, gap_sec: float) -> list:
    """対象のログを読み、決定点に間引く (副作用: 読み込み・表示)"""
    logs = []
    for p in paths:
        recs, bad = load_records_report(p)
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
                     "state_schemas": state_schema_counts(rows),
                     "versions": log_versions(recs), "ident": version_identity(recs),
                     "decision_versions": versions_by_row(recs, dec),
                     "fallback_decisions": decision_fallback_rows(recs), "advice_counts": advice_row_counts(recs),
                     "inputs_pin": inputs_pin_rows(recs),
                     # 読めなかった行 (2026-10-31 E-inh-4。集計から除いた行の件数と行番号 (先頭 20))
                     "bad_lines": len(bad), "bad_line_numbers": [b["line"] for b in bad[:20]]})
    n_dec = sum(lg["n_decisions"] for lg in logs)
    print(f"ログ {len(logs)} 本、advice 行 {sum(lg['n_advice_rows'] for lg in logs)} → 決定点 {n_dec} (間引き {gap_sec} 秒)")
    for lg in logs:
        print(f"  {lg['name']}: advice {lg['n_advice_rows']} → 決定点 {lg['n_decisions']} / decision 行 {lg['n_decision_rows']} / "
              f"方式 {lg['modes_used']} / move_locks {'あり' if lg['has_move_locks'] else 'なし'} / "
              f"state の版 {lg['state_schemas']}")
        if lg["inputs_pin"]["n"]:
            print(f"  ⚠ {lg['name']}: 不成立 ({inputs_pin_text(lg['inputs_pin'])})")
    bl = bad_lines_of(logs)
    if bl["n"]:
        from tools.jsonl_lines import bad_lines_text
        print(f"  ⚠ {bad_lines_text(bl)}")
    return logs


def bad_lines_of(logs: list) -> dict:
    """ログの列の読めなかった行の集計 {"n", "files": {ログ: 件数}} (純粋。2026-10-31 E-inh-4)"""
    files = {lg["name"]: int(lg.get("bad_lines") or 0) for lg in logs if lg.get("bad_lines")}
    return {"n": sum(files.values()), "files": files}


def run(paths: list, out_dir: Optional[Path], gap_sec: float = DECISION_GAP_SEC, dry_run: bool = False,
        target_fixed: bool = False, profile: bool = False, compare_search: bool = False,
        compare_sensitivity: bool = False, data_mode: str = "restore") -> dict:
    """target_fixed = 対象のログを事前に決めた接続テスト群として渡したか (--target)。profile = evaluate の内訳も出す。
    compare_search = 通常の受入の代わりに表示用の探索 on / off の照合をする (run_compare_search)。
    data_mode = 再生のデータの版 (restore = 当時の版 / current = 今のデータ。tools.inputs_pin.DataSwitch)"""
    logs = _load_logs(paths, gap_sec)
    n_dec = sum(lg["n_decisions"] for lg in logs)
    if dry_run:
        print("(dry-run: 再生しない、何も書かない)")
        return {"dry_run": True, "logs": [{k: v for k, v in lg.items() if k not in LOG_INTERNAL_KEYS}
                                          for lg in logs]}

    if compare_search:
        return run_compare_search(logs, out_dir, gap_sec, target_fixed, data_mode)
    if compare_sensitivity:
        return run_compare_sensitivity(logs, out_dir, gap_sec, target_fixed, data_mode)
    from vision.normalize import NameResolver
    from advisor.engine import RL_BLEND_DEFAULT
    from champions_agent import config as C
    from tools.inputs_pin import DataSwitch
    import os
    resolver = NameResolver()
    ck = _checker_kwargs()
    dsw = DataSwitch(data_mode)
    # 再生の設定は各 advice 行の時点の version 行の設定 (感度・表示用の探索。2026-10-20 レビュー: 時間・退避の比較を実機に揃える)
    def settings_of(lg, j):
        return replay_settings(lg["decision_versions"][j], C.TRANSITION_SENSITIVITY_ENABLED, C.ADVICE_DISPLAY_SEARCH)
    # 初回の読み込み (図鑑・RL・使用率) を所要時間から外す
    first_lg = next((lg for lg in logs if lg["decisions"]), None)
    first_row = _first_restorable(logs, dsw)
    if first_lg is not None and first_row is not None:
        replay_one(_engine_state(first_row, resolver), resolver, MODES, settings_of(first_lg, 0))
    decisions = []
    infos = []
    replay_cfg: dict = {}
    i = 0
    for lg in logs:
        timeline = scene_timeline(lg["records"])
        for j, row in enumerate(lg["decisions"]):
            compact = row["state"]
            plan = dsw.plan(lg["name"], lg["records"], row.get("t"))
            if not plan["ok"]:
                # 当時のデータの版を復元できない決定は再生しない (最新で代わりに評価しない)。照合できない決定に数える
                decisions.append(unrestored_decision(lg["name"], row, compact, plan))
                continue
            try:
                st, info = _engine_state_ex(row, resolver)
            except Exception as e:
                print(f"  復元できない: {lg['name']} {row.get('advice_id')}: {e}")
                continue
            infos.append(info)
            reg = _registered_ids(st, resolver)
            order = MODES if i % 2 == 0 else tuple(reversed(MODES))      # 順番の偏り (キャッシュ) を均す
            i += 1
            rs = settings_of(lg, j)
            key = json.dumps(rs, sort_keys=True, ensure_ascii=False)
            ent = replay_cfg.setdefault(lg["name"], {}).setdefault(key, dict(rs, n=0))
            ent["n"] += 1
            res = replay_one(st, resolver, order, rs)
            appeared = appearance_indices(lg["records"], (compact.get("player") or {}).get("party") or [], replay_now(row))
            lm, _rec = advice_mode(row)
            d = {"log": lg["name"], "advice_id": row.get("advice_id"), "t": row.get("t"), "turn": row.get("turn"),
                 "scene": compact.get("scene"), "active": _active_label(compact), "replay_settings": settings_label(rs),
                 "now_source": info["now_source"], "ev_infer_applied": info["ev_infer_applied"],
                 "stale": stale_reasons(row, scene_before(timeline, float(row.get("t") or 0.0))),
                 "log_best": _slim_best((row.get("advice") or {}).get("best")), "log_mode": lm, "modes": {}}
            for mode, (adv, ms, err) in res.items():
                if err or adv is None:
                    d["modes"][mode] = {"ok": False, "error": err or "None", "elapsed_ms": ms, "best": None,
                                        "violations": [], "unconfirmed": []}
                    continue
                if (adv or {}).get("reason") == INPUTS_PIN_REASON:
                    # 再生の固定入力の異常 (2026-10-31): 当時の版で評価できていないので照合しない (照合できない決定に数える)
                    d["modes"][mode] = replay_pin_result(adv, ms)
                    continue
                chk = check_best(adv, compact, move_locks_for_check(row), reg, detail=True, appeared=appeared, **ck)
                ru, rst = restore_check(row)
                chk["unconfirmed"] = list(dict.fromkeys(chk["unconfirmed"] + ru))
                chk["checks"]["state_restore"] = rst
                chk["checks"]["data_restore"] = "ok" if data_mode == "restore" else None
                d["modes"][mode] = {"ok": bool(adv.get("ok")), "best": _slim_best(adv.get("best")), "elapsed_ms": ms,
                                    "scoring_used": adv.get("scoring_used") or (adv.get("scoring_mode") if adv.get("ok") else None),
                                    "fallback_reason": adv.get("scoring_fallback"), "reason": adv.get("reason"),
                                    "violations": chk["violations"], "unconfirmed": chk["unconfirmed"],
                                    "checks": chk["checks"], "targets": chk["targets"]}
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
    # 版の照合 (2026-10-20 レビュー): 版の識別 (version 行の欄 − 時刻・version_id − 実行状態 + served_commit) と実行状態
    # (全 advice 行の読み込み成功) を分けて照合する (group_consistency)。増分は方式の欄を除いて照合する
    idents = {lg["name"]: lg["ident"] for lg in logs}
    by_mode = {m: group_consistency([{"name": n, "ident": idents[n]} for n in (ds.get("by_log") or {}) if n in idents])
               for m, ds in display.items()}
    delta_logs = list(dict.fromkeys(n for m in MODES for n in ((display.get(m) or {}).get("by_log") or {}) if n in idents))
    group = {"target_fixed": target_fixed, "by_mode": by_mode,
             "delta": group_consistency([{"name": n, "ident": idents[n]} for n in delta_logs], exclude=SCORING_MODE_FIELDS)}
    units = summarize_fallback_units([{"name": lg["name"], "decisions": lg["fallback_decisions"], "advice": lg["advice_counts"]}
                                      for lg in logs])
    replay_cfg_out = {n: list(v.values()) for n, v in replay_cfg.items()}
    all_from_version = bool(replay_cfg_out) and all(e["from_version"] for v in replay_cfg_out.values() for e in v)
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
    data_versions = dsw.summary()
    dsw.close()
    result = {
        "conditions": {"at": time.strftime("%Y-%m-%d %H:%M"), "code": _code_info(), "modes": list(MODES),
                       "data_mode": data_mode, "data_versions": data_versions,
                       "rl_blend_weight": RL_BLEND_DEFAULT, "rl_style": os.environ.get("RL_ADVICE_STYLE", "balance"),
                       "transition_budget_ms": C.TRANSITION_BUDGET_MS, "transition_sensitivity": C.TRANSITION_SENSITIVITY_ENABLED,
                       "display_search": getattr(C, "ADVICE_DISPLAY_SEARCH", None),
                       "replay_settings": replay_cfg_out, "replay_settings_from_version": all_from_version,
                       "replay_inputs": replay_input_counts(infos),
                       "gap_sec": gap_sec, "stale_hp_sec": REAL_ACCEPT_STALE_HP_SEC,
                       "display_deadline_sec": REAL_ACCEPT_DISPLAY_DEADLINE_SEC,
                       "n_advice_rows": sum(lg["n_advice_rows"] for lg in logs),
                       "bad_lines": bad_lines_of(logs),
                       "inputs_pin": {lg["name"]: lg["inputs_pin"] for lg in logs},
                       "target": {"fixed": target_fixed, "by_mode": by_mode, "delta": group["delta"]},
                       "logs": [{k: v for k, v in lg.items() if k not in LOG_INTERNAL_KEYS}
                                for lg in logs]},
        "summary": summary, "display": display, "display_all": summarize_display_all(disp_rows),
        "acceptance": acceptance(summary, display, rc, group=group, units=units, data=data_versions,
                                 inputs_pin={lg["name"]: lg["inputs_pin"] for lg in logs}), "record_check": rc,
        "fallback_units": units,
        "decisions": decisions, "display_rows": disp_rows, "profile": prof,
    }
    out_dir = Path(out_dir) if out_dir else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = render_markdown(result) + data_versions_markdown(data_versions)
    from tools.jsonl_lines import bad_lines_text
    if bad_lines_of(logs)["n"]:
        md += f"\n⚠ {bad_lines_text(bad_lines_of(logs))}\n"
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
    cg = ap.add_mutually_exclusive_group()
    cg.add_argument("--compare-search", action="store_true",
                    help="通常の受入の代わりに、両方式それぞれで表示用の探索 on / off の候補・得点・順位・第一候補の合法性の一致を照合する")
    ap.add_argument("--data-mode", choices=("restore", "current"), default="restore",
                    help="再生のデータの版: restore = 当時の版 (version 行の使用率の snapshot・登録チーム、selection_record 行のバンク。"
                         "無い・今の登録と違えば判定不能) / current = 今のデータで過去の局面を評価し直す")
    cg.add_argument("--compare-sensitivity", action="store_true",
                    help="通常の受入の代わりに、transition で感度 on / off の本採点の候補・得点・Q・順位・第一候補の合法性・退避の一致を照合する")
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
        profile=args.profile, compare_search=args.compare_search, compare_sensitivity=args.compare_sensitivity,
        data_mode=args.data_mode)


if __name__ == "__main__":
    main()
