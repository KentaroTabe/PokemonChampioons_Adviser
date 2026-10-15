"""40 局面の診断 (2026-10-15 M3b): 同じ入力で legacy と transition を比べ、順位差の原因を分ける (読み取りと再採点だけ)。

    python -m tools.diag_scenes <battles.jsonl> --result <check_advisor_player の結果.json> --out docs/ADVICE_DIAG_40_20261015.md
        [--seed 20261015] [--json 詳細.json]

入力は ADVICE_DIAG_LOG=1 ADVICE_DIAG_BOTH=1 で取った advisor-as-player の対戦記録 (turns[].diag: 採点入力の state・合法手・
両方式の出力・実行行動。champions_agent.env.advisor_player.diag_record)。手順:

1. 決定ごとに区分を決める (scene_status): transition が退避 → B (理由で 予算超過 / 未評価の区間 / 自分側の未対応技 / その他)、
   第一候補が異なる → A、一致 → C。助言なし・候補が 1 つの決定は除く。
2. 全決定を保存した state から再採点する (replay_decision): legacy (記録と同じ RL 加点の重み)、legacy (RL 加点なし)、transition
   (予算を DIAG_SCENES_BIG_BUDGET_MS に上げる。予算超過の退避は時間依存なので順位の再現性と分ける)。transition の入力
   (TransitionInputs) は scoring_transition.score を包んで写し取り、控え (V の残存戦力) を確かめる。
3. A の小区分 (a_bucket: 変化技が絡む / 自滅・温存・終盤 / 攻撃 対 交代 / その他) を決め、区分内から固定 seed で無作為に抽出する
   (sample_scenes。該当が足りなければ埋めない)。
4. 各局面: 再現性 (compare_ranking)、入力の正しさ (check_inputs)、A は順位差の分解 (decompose)、B は退避しなければどうなったか
   (what_if_fallback)、C は一致の理由 (agree_margin)。必須の回帰ケース (交代技の後の繰り出し・技の固定・とらわれ・選出情報の欠落) は
   診断対戦から探し、無ければ合成する (regression_cases)。
5. Markdown に書く (render_markdown)。

純粋な部分 (区分・抽出・分解・照合・描画) は tests/test_diag_scenes.py でテストする。再採点は環境 (図鑑・使用率 DB・RL のピン) に依存する。
閾値は champions_agent.config の DIAG_SCENES_* (採点には使わない)。
"""
from __future__ import annotations

import copy
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from champions_agent import config as C

A_BUCKETS = ("A_attack_switch", "A_status", "A_sacrifice_endgame")
B_BUCKETS = ("B_budget", "B_unevaluated", "B_own_unsupported")
BUCKET_ORDER = A_BUCKETS + B_BUCKETS + ("C_agree",)
BUCKET_LABELS = {
    "A_attack_switch": "A: 攻撃 対 交代",
    "A_status": "A: 変化技が絡む (積み・設置・壁など)",
    "A_sacrifice_endgame": "A: 自滅・温存 (捨てる判断)・終盤",
    "A_other": "A: その他 (攻撃 対 攻撃・交代 対 交代。抽出しない)",
    "B_budget": "B: 予算超過",
    "B_unevaluated": "B: 未評価の区間",
    "B_own_unsupported": "B: 自分側の未対応技",
    "B_other": "B: その他の退避 (抽出しない)",
    "C_agree": "C: 第一候補が一致 (対照)",
}
CAUSES = ("transition", "valuation", "unevaluated", "pending")
CAUSE_LABELS = {"transition": "(i) 遷移の違い", "valuation": "(ii) 評価の違い", "unevaluated": "(iii) 退避・未評価",
                "pending": "判断保留"}
KBA_MARK = "行動前に倒される見込み"          # legacy の ×0.25 の割引 (act_discount) の理由の文言 (advisor.engine)
STATUS_THREAT_MARK = "で倒される危険あり"      # legacy の変化技の −15 (threat_faces_me: 相手の攻撃 / 先制技で倒される危険あり)
DIES_ON_ENTRY_MARK = "交代出しで倒される危険あり"   # legacy の交代の「着地で倒される」(survives = False) の文言
_DMG_RE = re.compile(r"予測ダメージ\s*(\d+)〜(\d+)%")
_ACC_RE = re.compile(r"命中(\d+)")


# ------------------------------------------------------------------ 記録の読み (純粋)
def key_of(a: Optional[dict]) -> Optional[tuple]:
    return (a.get("kind"), a.get("id")) if a and a.get("kind") else None


def fmt_key(k: Optional[tuple]) -> str:
    if not k:
        return "-"
    return f"{k[1]}" if k[0] == "move" else f"交代:{k[1]}"


def iter_decisions(records: list, prefix: str = "") -> list:
    """対戦記録の列 → diag のある決定の列 (純粋)。key = "<prefix>b<対戦の番号>d<決定の番号>" (0 始まり、記録の順。prefix は
    対戦記録の組の名前 + ":"、1 組だけなら空)"""
    out = []
    for bi, r in enumerate(records or []):
        for di, t in enumerate(r.get("turns") or []):
            d = t.get("diag")
            if not d:
                continue
            out.append({"key": f"{prefix}b{bi:02d}d{di:02d}", "set": prefix.rstrip(":"), "battle": bi, "decision": di, "battle_id": r.get("battle_id"),
                        "turn": t.get("turn"), "diag": d, "own_team_text": r.get("diag_own_team_text"),
                        "selection": list(r.get("our_selection") or [])})
    return out


def modes_of(diag: dict) -> tuple:
    """diag の (transition の出力, legacy の出力)。どちらかが無ければ None"""
    outs = [diag.get("advice"), diag.get("other_mode")]
    tr = next((o for o in outs if o and o.get("scoring_mode") == "transition"), None)
    lg = next((o for o in outs if o and o.get("scoring_mode") == "legacy"), None)
    return tr, lg


def fallback_bucket(reason) -> str:
    """transition の退避の理由 → 区分 B の小区分 (純粋)"""
    r = str(reason or "")
    if r == "budget":
        return "B_budget"
    if r in ("opp_unevaluated_ambiguous", "unevaluated_ambiguous"):
        return "B_unevaluated"
    if r.startswith("unsupported:"):
        return "B_own_unsupported"
    return "B_other"


def scene_status(diag: dict) -> dict:
    """決定の区分 (純粋)。{"status": "fallback" / "differ" / "agree" / "excluded", "bucket", "why", "a_L", "a_T"}"""
    tr, lg = modes_of(diag)
    out = {"status": "excluded", "bucket": None, "why": None, "a_L": None, "a_T": None}
    if not tr or not lg or not tr.get("ok") or not lg.get("ok"):
        out["why"] = "no_advice"
        return out
    out["a_L"], out["a_T"] = key_of(lg.get("best")), key_of(tr.get("best"))
    if tr.get("scoring_fallback"):
        out.update(status="fallback", bucket=fallback_bucket(tr["scoring_fallback"]), why=str(tr["scoring_fallback"]))
        return out
    if len(tr.get("actions") or []) < 2 or len(lg.get("actions") or []) < 2:
        out["why"] = "single_candidate"
        return out
    if out["a_L"] != out["a_T"]:
        out["status"] = "differ"
    else:
        out.update(status="agree", bucket="C_agree")
    return out


def remaining_of(state: dict) -> tuple:
    """(自分の残り, 相手の残り) (純粋)。自分は状態の remaining (選出した生存数)、相手は選出数 (team_size、無ければ
    VALUATION_TEAM_SIZE) − 判明したひんしの数"""
    me, opp = state.get("player") or {}, state.get("opponent") or {}
    mine = me.get("remaining")
    if mine is None:
        mine = sum(1 for p in me.get("party") or [] if p.get("is_picked") and p.get("status") != "fainted")
    size = int(opp.get("team_size") or C.VALUATION_TEAM_SIZE)
    fainted = sum(1 for p in opp.get("party") or [] if p and p.get("status") == "fainted")
    return int(mine), max(0, size - fainted)


def kind_of(k: tuple, move_cat: Callable) -> str:
    """候補の種類 (純粋): "switch" / "status" (変化技) / "attack" """
    if k[0] == "switch":
        return "switch"
    return "status" if move_cat(k[1]) == "Status" else "attack"


def legacy_kba(la: Optional[dict]) -> bool:
    """legacy がその技を「行動前に倒される見込み」(×0.25) としたか"""
    return bool(la) and KBA_MARK in str(la.get("reason") or "")


def a_bucket(a_L: tuple, a_T: tuple, legacy: dict, trans: dict, remaining: tuple, move_cat: Callable,
             prob: float = None, endgame_n: int = None) -> str:
    """区分 A の小区分 (純粋。上から順に当てはめる):
    1. 変化技が絡む: a_L か a_T が変化技 → A_status
    2. 自滅・温存・終盤: a_L か a_T が自滅する技 (legacy の self_cost.faints)、または攻撃と交代の組で攻撃側が先に倒される見込み
       (legacy の ×0.25 か transition の p_ko_before_act ≥ PROB: 居座って倒されるか引くかの捨てる判断)、または自分と相手の残りが
       どちらも ENDGAME_REMAINING 以下 → A_sacrifice_endgame
    3. 攻撃と交代の組 → A_attack_switch
    4. それ以外 (攻撃 対 攻撃・交代 対 交代) → A_other (抽出しない)
    legacy / trans: (kind, id) → 行動の辞書 (再採点の全候補)"""
    prob = C.DIAG_SCENES_PROB if prob is None else prob
    endgame_n = C.DIAG_SCENES_ENDGAME_REMAINING if endgame_n is None else endgame_n
    kinds = {kind_of(a_L, move_cat), kind_of(a_T, move_cat)}
    if "status" in kinds:
        return "A_status"
    if any(((legacy.get(k) or {}).get("self_cost") or {}).get("faints") for k in (a_L, a_T)):
        return "A_sacrifice_endgame"
    if kinds == {"attack", "switch"}:
        atk = a_L if a_L[0] == "move" else a_T
        p_kba = float(((trans.get(atk) or {}).get("branches") or {}).get("p_ko_before_act") or 0.0)
        if legacy_kba(legacy.get(atk)) or p_kba >= prob:
            return "A_sacrifice_endgame"
    mine, theirs = remaining
    if mine <= endgame_n and theirs <= endgame_n:
        return "A_sacrifice_endgame"
    if kinds == {"attack", "switch"}:
        return "A_attack_switch"
    return "A_other"


def sample_scenes(pools: dict, quotas: dict, seed: int) -> tuple:
    """区分ごとの候補 (key の列) から固定 seed で無作為に抽出する (純粋)。戻り値 (選んだ {区分: [key]}, 不足 {区分: 不足数})。
    区分は BUCKET_ORDER の順に 1 つの rng で引く。候補は key の順に並べてから引く (記録の順で決まる)。足りない区分は埋めない"""
    rng = random.Random(seed)
    chosen, short = {}, {}
    for b in BUCKET_ORDER:
        if b not in quotas:
            continue
        pool = sorted(pools.get(b) or [])
        n = int(quotas[b])
        if len(pool) <= n:
            chosen[b] = pool
            if len(pool) < n:
                short[b] = n - len(pool)
        else:
            chosen[b] = sorted(rng.sample(pool, n))
    return chosen, short


# ------------------------------------------------------------------ 照合 (純粋)
def compare_ranking(recorded: Optional[list], replayed: Optional[list], tol: float = 1e-9) -> dict:
    """記録の順位 [{"kind","id","score"}] と再採点の順位の一致 (純粋)。same_order = 種類・id の並びが同じ、
    same_scores = 同じ並びで点も同じ (tol 以内)、max_abs_diff = 対応する候補の点の差の最大"""
    rec = [(key_of(a), a.get("score")) for a in recorded or []]
    rep = [(key_of(a), a.get("score")) for a in replayed or []]
    same_order = [k for k, _ in rec] == [k for k, _ in rep]
    rep_by = dict(rep)
    diffs = [abs(float(s) - float(rep_by[k])) for k, s in rec if k in rep_by and s is not None and rep_by[k] is not None]
    mx = max(diffs) if diffs else (0.0 if not rec and not rep else None)
    same_scores = same_order and len(diffs) == len(rec) and (mx is None or mx <= tol)
    return {"same_order": same_order, "same_scores": bool(same_scores), "max_abs_diff": mx}


def repro_verdict(rec_out: Optional[dict], rep_out: Optional[dict], mode: str) -> dict:
    """1 方式の再現性 (純粋)。rec_out = 記録の出力 (diag の advice / other_mode)、rep_out = 再採点の出力 (evaluate の戻り値)。
    status: "ok" (順位と点が一致) / "fallback_same" (同じ理由で退避し、退避先の順位も一致) / "time_dependent" (記録が予算超過の退避:
    時間依存なので別扱い) / "mismatch"。transition の再採点は予算を外しているので、記録の予算超過は再現の対象にしない"""
    rec_fb = (rec_out or {}).get("scoring_fallback") if mode == "transition" else None
    rep_fb = (rep_out or {}).get("scoring_fallback") if mode == "transition" else None
    cmp = compare_ranking((rec_out or {}).get("actions"), (rep_out or {}).get("actions"))
    if mode == "transition" and rec_fb == "budget":
        return {"status": "time_dependent", "recorded_fallback": rec_fb, "replay_fallback": rep_fb, **cmp}
    if rec_fb or rep_fb:
        same = (rec_fb == rep_fb) and cmp["same_scores"]
        return {"status": "fallback_same" if same else "mismatch", "recorded_fallback": rec_fb, "replay_fallback": rep_fb,
                **cmp}
    return {"status": "ok" if cmp["same_scores"] else "mismatch", **cmp}


def check_inputs(state: dict, legal: dict, outs: dict, inp_summary: Optional[dict]) -> list:
    """入力の正しさ (純粋)。問題の文言の列 (空なら問題なし)。
    - 候補が合法手の中だけか (技 = poke-env の available_moves、交代 = available_switches)。両方式の全候補
    - 未選出の個体 (状態の is_picked が偽) が候補に入っていないか
    - transition の入力の控え (V の残存戦力) が「選出・生存・場にいない」個体だけか、状態に無い残り (my_unknown) が 0 か
    outs: {"legacy": evaluate の出力, "transition": ...}。inp_summary: summarize_inputs の結果 (transition が入力を作れなければ None)"""
    probs = []
    moves, switches = set(legal.get("moves") or []), set(legal.get("switches") or [])
    me = state.get("player") or {}
    party = me.get("party") or []
    ai = me.get("active_index")
    unpicked = {p.get("species_id") for p in party if not p.get("is_picked")}
    expected_bench = sorted(p.get("species_id") for i, p in enumerate(party)
                            if i != ai and p.get("is_picked") and p.get("status") != "fainted")
    for mode, out in outs.items():
        for a in (out or {}).get("actions") or []:
            k = key_of(a)
            if k is None:
                continue
            if k[0] == "move" and k[1] not in moves:
                probs.append(f"{mode}: 候補の技 {k[1]} が合法手に無い")
            if k[0] == "switch" and k[1] not in switches:
                probs.append(f"{mode}: 候補の交代 {k[1]} が合法手に無い")
            if k[0] == "switch" and k[1] in unpicked:
                probs.append(f"{mode}: 未選出の {k[1]} が候補に入った")
    if inp_summary is not None:
        bench = sorted(inp_summary.get("bench") or [])
        if bench != expected_bench:
            probs.append(f"transition: V の控え {bench} が選出・生存の控え {expected_bench} と違う")
        if any(s in unpicked for s in bench):
            probs.append("transition: 未選出の個体が V の残存戦力に入った")
        if int(inp_summary.get("my_unknown") or 0) != 0:
            probs.append(f"transition: 状態に無い自分の残り my_unknown = {inp_summary['my_unknown']}")
    return probs


# ------------------------------------------------------------------ 順位差の分解 (純粋)
def legacy_assumption(la: Optional[dict], opp_hp_pct: Optional[float]) -> dict:
    """legacy の行動の辞書から、legacy が置いた遷移の前提を読む (純粋)。
    技: kba = 先に倒される前提 (攻撃技は ×0.25 の割引「行動前に倒される見込み」、変化技は −15「相手の攻撃で倒される危険あり」)、
    ko = 倒せる見込み (予測ダメージの最小が相手の HP 以上 1、最大が以上 0.5、
    命中を掛ける。advisor.engine と同じ規則。理由の文言の整数の % から読むので境界は ±1% の誤差がある)。交代: dies = 着地で倒される
    見込み (「交代出しで倒される危険あり」)、incoming = legacy の被ダメ予測 (相手の最善の 1 技の平均 %)"""
    if not la:
        return {}
    reason = str(la.get("reason") or "")
    if la.get("kind") == "switch":
        return {"dies": DIES_ON_ENTRY_MARK in reason, "incoming": la.get("incoming")}
    out = {"kba": KBA_MARK in reason or STATUS_THREAT_MARK in reason, "ko": None}
    m = _DMG_RE.search(reason)
    if m and opp_hp_pct is not None:
        lo, hi = float(m.group(1)), float(m.group(2))
        ko = 1.0 if lo >= opp_hp_pct else (0.5 if hi >= opp_hp_pct else 0.0)
        acc = _ACC_RE.search(reason)
        out["ko"] = ko * (float(acc.group(1)) / 100.0 if acc else 1.0)
    return out


def _contradictions(k: tuple, la: Optional[dict], ta: Optional[dict], opp_hp_pct, prob: float, ko_gap: float) -> list:
    """候補 k の legacy の前提と transition の分岐の食い違い [(向き, 文言, 種類)] (純粋)。向き: "+" = legacy が transition より高く
    見積もった (撃てる・倒せる・耐える前提)、"-" = 低く見積もった。種類: "kba" (先に倒される) / "ko" (倒せる) / "dies" (着地で倒される)"""
    out = []
    if not la or not ta:
        return out
    asm = legacy_assumption(la, opp_hp_pct)
    br = ta.get("branches") or {}
    name = fmt_key(k)
    if k[0] == "move":
        p_kba = float(br.get("p_ko_before_act") or 0.0)
        if not asm.get("kba") and p_kba >= prob:
            out.append(("+", f"{name}: legacy は撃てる前提、transition は先に倒される {p_kba:.0%}", "kba"))
        elif asm.get("kba") and p_kba < prob:
            out.append(("-", f"{name}: legacy は先に倒される前提、transition は先に倒される {p_kba:.0%}", "kba"))
        if asm.get("ko") is not None and p_kba < 1.0 - 1e-6:
            # legacy の倒せる見込みは行動順と別 (順は kba で扱う) なので、transition も「行動できた分岐」の倒す確率と比べる
            p_ko = min(1.0, float(br.get("p_ko") or 0.0) / (1.0 - p_kba))
            if asm["ko"] - p_ko >= ko_gap:
                out.append(("+", f"{name}: legacy の倒せる見込み {asm['ko']:.0%}、transition の行動できたときの倒す確率 {p_ko:.0%}", "ko"))
            elif p_ko - asm["ko"] >= ko_gap:
                out.append(("-", f"{name}: legacy の倒せる見込み {asm['ko']:.0%}、transition の行動できたときの倒す確率 {p_ko:.0%}", "ko"))
    else:
        p_faint = float(br.get("p_faint") or 0.0)
        if not asm.get("dies") and p_faint >= prob:
            out.append(("+", f"{name}: legacy は着地で耐える前提、transition は交代先が倒される {p_faint:.0%}", "dies"))
        elif asm.get("dies") and p_faint < prob:
            out.append(("-", f"{name}: legacy は着地で倒される前提、transition は交代先が倒される {p_faint:.0%}", "dies"))
    return out


def unevaluated_mass(ta: Optional[dict]) -> float:
    """transition の候補の未評価の質量 (応手の ε と分岐の未評価。無ければ 0)"""
    if not ta:
        return 0.0
    if ta.get("unevaluated_mass") is not None:
        return float(ta["unevaluated_mass"])
    return float(ta.get("eps_unevaluated") or 0.0)


def decompose(a_L: tuple, a_T: tuple, legacy: dict, trans: dict, opp_hp_pct: Optional[float],
              legacy_norl_best: Optional[tuple] = None, prob: float = None, ko_gap: float = None,
              uneval_main: float = None, tie_q: float = None, move_cat: Optional[Callable] = None) -> dict:
    """区分 A の順位差の主因 (純粋)。legacy / trans: (kind, id) → 再採点の行動の辞書 (legacy は reason・incoming・self_cost、
    transition は q・branches・unevaluated_mass)。legacy_norl_best: RL 加点なしの legacy の第一候補。

    規則 (上から順に当てはめる):
    - a_L か a_T が片方の方式の候補に無い → 判断保留
    - transition の Q(a_T) と Q(a_L) の差が TIE_Q 未満 (同点: 候補の並びの順で第一候補が決まる) → 判断保留
    - a_L か a_T の未評価の質量が UNEVAL_MAIN 以上 → (iii) 退避・未評価
    - RL 加点を外すと legacy の第一候補が a_T になる → (ii) 評価の違い (legacy の点数式の RL 加点)
    - 遷移の食い違い (legacy の前提と transition の分岐。_contradictions。両方の候補に同じ種類・同じ向きの食い違いがあれば相殺) の
      うち順位差を説明する向き (a_L を legacy が高く /
      a_T を低く見積もった) だけがある → (i) 遷移の違い。説明する向きと逆向きの両方がある → 判断保留
    - 説明する食い違いが無い → (ii) 評価の違い (同じ遷移の見込みで、V と legacy の点数式が違う方向を向く)
    戻り値 {"cause", "dq", "evidence": [文言], "notes": [文言]}"""
    prob = C.DIAG_SCENES_PROB if prob is None else prob
    ko_gap = C.DIAG_SCENES_KO_GAP if ko_gap is None else ko_gap
    uneval_main = C.DIAG_SCENES_UNEVAL_MAIN if uneval_main is None else uneval_main
    tie_q = C.DIAG_SCENES_TIE_Q if tie_q is None else tie_q
    lL, lT, tL, tT = legacy.get(a_L), legacy.get(a_T), trans.get(a_L), trans.get(a_T)
    out = {"cause": "pending", "dq": None, "evidence": [], "notes": []}
    if not (lL and lT and tL and tT):
        miss = [f"{m}:{fmt_key(k)}" for m, k, a in (("legacy", a_L, lL), ("legacy", a_T, lT),
                                                    ("transition", a_L, tL), ("transition", a_T, tT)) if not a]
        out["evidence"].append("候補の集合が違う (" + ", ".join(miss) + " が無い)")
        return out
    dq = float(tT.get("q") or 0.0) - float(tL.get("q") or 0.0)
    out["dq"] = round(dq, 4)
    out["notes"].append(f"legacy {fmt_key(a_L)} {lL.get('score')} > {fmt_key(a_T)} {lT.get('score')} / "
                        f"transition Q {fmt_key(a_T)} {tT.get('q'):+.3f} > {fmt_key(a_L)} {tL.get('q'):+.3f} (ΔQ {dq:+.3f})")
    if abs(dq) < tie_q:
        out["evidence"].append(f"transition の Q が同点 (差 {dq:+.4f} < {tie_q}): 第一候補は候補の並びの順で決まり、実質の順位差ではない")
        return out
    m = max(unevaluated_mass(tL), unevaluated_mass(tT))
    if m >= uneval_main:
        out["cause"] = "unevaluated"
        out["evidence"].append(f"未評価の質量 {m:.0%} (≥ {uneval_main:.0%})")
        return out
    if legacy_norl_best is not None and legacy_norl_best == a_T:
        out["cause"] = "valuation"
        out["evidence"].append(f"RL 加点を外すと legacy の第一候補も {fmt_key(a_T)} (legacy の点数式の RL 加点で逆転)")
        return out
    cL = _contradictions(a_L, lL, tL, opp_hp_pct, prob, ko_gap)
    cT = _contradictions(a_T, lT, tT, opp_hp_pct, prob, ko_gap)
    # 同じ種類・同じ向きの食い違いが両方の候補にあれば相殺する (例: legacy が両方を「先に倒される」とした)
    both = {(sg, ty) for sg, _t, ty in cL} & {(sg, ty) for sg, _t, ty in cT}
    if both:
        out["notes"].append("両方の候補に同じ食い違い (相殺): " + " / ".join(t for sg, t, ty in cL + cT if (sg, ty) in both))
        cL = [c for c in cL if (c[0], c[2]) not in both]
        cT = [c for c in cT if (c[0], c[2]) not in both]
    explain = [t for s, t, _ty in cL if s == "+"] + [t for s, t, _ty in cT if s == "-"]
    oppose = [t for s, t, _ty in cL if s == "-"] + [t for s, t, _ty in cT if s == "+"]
    if explain and not oppose:
        out["cause"] = "transition"
        out["evidence"] += explain
    elif explain and oppose:
        out["cause"] = "pending"
        out["evidence"].append("遷移の食い違いが両方向: " + " / ".join(explain + oppose))
    else:
        out["cause"] = "valuation"
        out["evidence"].append("legacy の前提 (先に倒される・倒せる・着地で耐える) と transition の分岐に順位差を説明する食い違いが無い"
                               + (" (逆向きの食い違い: " + " / ".join(oppose) + ")" if oppose else ""))
        kinds = {a_L[0], a_T[0]}
        if move_cat is not None and any(k[0] == "move" and move_cat(k[1]) == "Status" for k in (a_L, a_T)):
            out["notes"].append("legacy の変化技は固定点 (STATUS_MOVE_VALUE。回復は失った HP、ちょうはつは相手の変化技の割合)、"
                                "transition は効果を遷移で解決して V で評価")
        if "switch" in kinds:
            out["notes"].append("交代の点数は legacy が 反撃の代表技 ×0.45 − 被ダメ ×0.5 + 耐えれば 20、transition は V (残存戦力 M + "
                                "後続の対面 C)")
    return out


def agree_margin(lg_actions: list, tr_actions: list, close_q: float = None, close_legacy: float = None) -> dict:
    """区分 C の一致の理由 (純粋): 1 位と 2 位の差 (transition は Q、legacy は点数)。どちらかが閾値未満なら「僅差」"""
    close_q = C.DIAG_SCENES_CLOSE_Q if close_q is None else close_q
    close_legacy = C.DIAG_SCENES_CLOSE_LEGACY if close_legacy is None else close_legacy
    tq = [float(a.get("q")) for a in tr_actions or [] if a.get("q") is not None]
    ls = [float(a.get("score")) for a in lg_actions or [] if a.get("score") is not None]
    gq = (tq[0] - tq[1]) if len(tq) >= 2 else None
    gl = (ls[0] - ls[1]) if len(ls) >= 2 else None
    close = (gq is not None and gq < close_q) or (gl is not None and gl < close_legacy)
    return {"label": "僅差" if close else "差が大きい", "gap_q": None if gq is None else round(gq, 4),
            "gap_legacy": None if gl is None else round(gl, 1)}


def by_key(actions: Optional[list]) -> dict:
    return {key_of(a): a for a in actions or [] if key_of(a)}


def summarize_inputs(inp) -> Optional[dict]:
    """TransitionInputs の要約 (純粋。入力の正しさの照合用)"""
    if inp is None:
        return None
    return {"active": inp.me.active.species_id, "bench": [v.species_id for v, _hp in inp.me.bench],
            "my_unknown": int(inp.vctx.my_unknown or 0), "opp_unknown": int(inp.vctx.opp_unknown or 0),
            "candidates": [(c.kind, c.id, c.mode) for c in inp.candidates], "response_mode": inp.response_mode,
            "notes": list(inp.notes)}


# ------------------------------------------------------------------ 回帰ケース (純粋な部分)
REGRESSION_KINDS = ("pivot", "locked", "trapped", "picked_missing")
REGRESSION_LABELS = {"pivot": "交代技の後の繰り出し", "locked": "技の固定", "trapped": "とらわれ",
                     "picked_missing": "選出情報の欠落"}


def regression_kinds_of(state: dict) -> set:
    """状態が当たる回帰ケースの種類 (純粋)"""
    from champions_agent.env.advisor_player import SIM_REASON_LOCKED, SIM_REASON_TRAPPED
    out = set()
    me = state.get("player") or {}
    if state.get("pending_pivot_switch"):
        out.add("pivot")
    ai = me.get("active_index")
    act = (me.get("party") or [])[ai] if ai is not None and 0 <= ai < len(me.get("party") or []) else {}
    if any(v == SIM_REASON_LOCKED for v in (act.get("move_block_reasons") or {}).values()):
        out.add("locked")
    if me.get("switch_block_reason") == SIM_REASON_TRAPPED:
        out.add("trapped")
    return out


def synth_state(state: dict, kind: str) -> dict:
    """回帰ケースの合成 (純粋。state を書き換えずに写しを返す)。advisor_player.apply_sim_legality と同じ欄を立てる。
    pivot: 交代しか選べない要求 (場の個体は生存、技は全部選べない)、locked: 先頭の選べる技だけ選べる (ほかは固定で選べない)、
    trapped: 交代先なし (とらわれ)、picked_missing: 選出が分からず全個体を選出扱い (picked_mask_of が None のときの battle_to_state)"""
    from champions_agent.env.advisor_player import SIM_REASON_LOCKED, SIM_REASON_TRAPPED
    s = copy.deepcopy(state)
    me = s["player"]
    act = me["party"][me["active_index"]]
    legal = list(act.get("legal_move_ids") or [m.get("move_id") for m in act.get("moves") or []])
    if kind == "pivot":
        s["pending_pivot_switch"] = True
        act["legal_move_ids"] = []
        act["move_block_reasons"] = {m.get("move_id"): "simulator: 交代先を選ぶ要求 (技は選べない)" for m in act.get("moves") or []}
    elif kind == "locked":
        keep = legal[:1]
        act["legal_move_ids"] = keep
        act["move_block_reasons"] = {m.get("move_id"): SIM_REASON_LOCKED for m in act.get("moves") or []
                                     if m.get("move_id") not in keep}
    elif kind == "trapped":
        me["legal_switch_indices"] = []
        me["switch_block_reason"] = SIM_REASON_TRAPPED
    elif kind == "picked_missing":
        for p in me["party"]:
            p["is_picked"] = True
        me["remaining"] = sum(1 for p in me["party"] if p.get("status") != "fainted")
    return s


def regression_expect(kind: str, state: dict, outs: dict) -> list:
    """回帰ケースの期待 (純粋)。満たさない点の文言の列。pivot: 候補は交代だけ / locked: 技の候補は legal_move_ids の中だけ、
    固定の技は illegal_actions / trapped: 交代の候補が無い / picked_missing: (入力不成立の確認) 監査が picked_unknown を立てる"""
    probs = []
    me = state.get("player") or {}
    act = me["party"][me["active_index"]]
    for mode, out in outs.items():
        acts = (out or {}).get("actions") or []
        if kind == "pivot" and any(a.get("kind") == "move" for a in acts):
            probs.append(f"{mode}: 交代だけの場面で技が候補")
        if kind == "locked":
            legal = set(act.get("legal_move_ids") or [])
            bad = [a.get("id") for a in acts if a.get("kind") == "move" and a.get("id") not in legal]
            if bad:
                probs.append(f"{mode}: 固定で選べない技 {bad} が候補")
        if kind == "trapped" and any(a.get("kind") == "switch" for a in acts):
            probs.append(f"{mode}: とらわれで交代が候補")
    return probs


# ------------------------------------------------------------------ 描画 (純粋)
def _cell(x) -> str:
    return str(x).replace("|", "/").replace("\n", " ")


def render_markdown(doc: dict) -> str:
    """診断の結果 (run_diag の戻り値) → Markdown (純粋)"""
    L = []
    cond = doc.get("conditions") or {}
    L += ["# 40 局面の診断 (M3b、2026-10-15): legacy と transition の順位差の原因", ""]
    L += ["同じ入力 (保存した state と合法手) で legacy と transition を比べ、順位差を 遷移の違い / 評価 (V) の違い / 退避・未評価 に"
          "分ける。重みの調整・勝率への因果の寄与・新しい 600 戦は含めない。予算超過の退避 (時間依存) は順位の再現性と分けて扱う。", ""]
    L += ["## 1. 要点 (集計)", ""]
    for line in (doc.get("aggregate") or {}).get("lines") or []:
        L.append(line if line.startswith("  - ") else f"- {line}")
    L.append("")
    L += ["## 2. 条件", ""]
    for k, v in cond.items():
        L.append(f"- {k}: {_cell(v)}")
    L.append("")
    acc = doc.get("accept") or {}
    L += ["## 3. 診断対戦と受入監査", ""]
    for ps in doc.get("per_set") or []:
        L.append(f"- 組 {ps['label']}: 対戦 {ps['n_battles']} 戦 (勝率 {ps.get('win_rate')})、決定 {ps['n_decisions']} "
                 f"(diag のある決定 {ps['n_diag']})、受入監査 {'全項目 0' if not ps['accept_violations'] else '; '.join(ps['accept_violations'])}"
                 f" ({', '.join(f'{k} {v}' for k, v in ps['accept'].items())})")
    L.append(f"- 合計: 対戦 {doc.get('n_battles')} 戦、決定 {doc.get('n_decisions')} (diag のある決定 {doc.get('n_diag')})、"
             f"受入監査の判定: {'全項目 0 (合格)' if not doc.get('accept_violations') else '; '.join(doc['accept_violations'])}")
    L.append("")
    L.append("| 項目 | 件数 |")
    L.append("|---|---|")
    for k, v in acc.items():
        L.append(f"| {k} | {v} |")
    L.append("")
    L += ["## 4. 抽出の規則と該当数", ""]
    for line in doc.get("rules") or []:
        L.append(f"- {line}")
    L.append("")
    L.append("| 区分 | 該当 (母集団、組ごと) | 予定 | 抽出 (組ごと) | 不足 |")
    L.append("|---|---|---|---|---|")
    pools, quotas, chosen, short = doc["pool_sizes"], doc["quotas"], doc["chosen"], doc["short"]
    pbs = doc.get("pool_by_set") or {}

    def by_set_txt(d):
        d = {k: v for k, v in (d or {}).items() if k}
        return f" ({', '.join(f'{k} {v}' for k, v in sorted(d.items()))})" if d else ""
    for b in list(BUCKET_ORDER) + ["A_other", "B_other"]:
        ch = chosen.get(b) or []
        ch_sets = Counter(k.split(":", 1)[0] for k in ch if ":" in k)
        L.append(f"| {BUCKET_LABELS[b]} | {pools.get(b, 0)}{by_set_txt(pbs.get(b))} | {quotas.get(b, '-')} | "
                 f"{len(ch)}{by_set_txt(ch_sets)} | {short.get(b, 0) or '-'} |")
    L.append(f"| 除外 (助言なし・候補 1 つ) | {pools.get('excluded', 0)} | - | - | - |")
    L.append("")
    if doc.get("short_reasons"):
        for line in doc["short_reasons"]:
            L.append(f"- 不足の理由: {line}")
        L.append("")
    L += ["## 5. 局面ごとの確認", ""]
    L += ["列: 再現 = 保存した state からの再採点が元の順位と点に一致するか (L = legacy、T = transition。T は予算を外して再採点。"
          "ok = 一致、fb = 同じ理由で退避し退避先も一致、時間 = 元が予算超過の退避 (時間依存)、× = 不一致)。入力 = 候補が合法手の中だけ・"
          "未選出が候補と V に入っていない (ok / 問題の内容)。", ""]
    scenes = doc.get("scenes") or {}
    for b in BUCKET_ORDER:
        keys = chosen.get(b) or []
        L += [f"### {BUCKET_LABELS[b]} ({len(keys)} 件)", ""]
        if not keys:
            L += ["(該当なし)", ""]
            continue
        if b.startswith("A_"):
            L.append("| 局面 | ターン | 場 (自分 / 相手) | a_L (legacy) | a_T (transition) | 再現 L/T | 入力 | 主因 | 根拠 |")
            L.append("|---|---|---|---|---|---|---|---|---|")
        elif b.startswith("B_"):
            L.append("| 局面 | ターン | 場 (自分 / 相手) | 退避の理由 | legacy の第一候補 | 再現 L/T | 入力 | 退避しなければ |")
            L.append("|---|---|---|---|---|---|---|---|")
        else:
            L.append("| 局面 | ターン | 場 (自分 / 相手) | 第一候補 | 再現 L/T | 入力 | 一致の理由 |")
            L.append("|---|---|---|---|---|---|---|")
        for k in keys:
            s = scenes[k]
            rep = f"{s['repro']['legacy']} / {s['repro']['transition']}"
            inp = "ok" if not s["input_problems"] else "; ".join(s["input_problems"])
            board = s["board"]
            if b.startswith("A_"):
                d = s["decompose"]
                ev = " / ".join(d["evidence"] + d["notes"])
                L.append(f"| {k} | {s['turn']} | {_cell(board)} | {fmt_key(s['a_L'])} | {fmt_key(s['a_T'])} | {rep} | {_cell(inp)} | "
                         f"{CAUSE_LABELS[d['cause']]} | {_cell(ev)} |")
            elif b.startswith("B_"):
                L.append(f"| {k} | {s['turn']} | {_cell(board)} | {_cell(s['fallback'])} | {fmt_key(s['a_L'])} | {rep} | "
                         f"{_cell(inp)} | {_cell(s['what_if'])} |")
            else:
                m = s["margin"]
                L.append(f"| {k} | {s['turn']} | {_cell(board)} | {fmt_key(s['a_T'])} | {rep} | {_cell(inp)} | "
                         f"{m['label']} (Q の差 {m['gap_q']}、legacy の点数の差 {m['gap_legacy']}) |")
        L.append("")
    L += ["## 6. 必須の回帰ケース", ""]
    L.append("| ケース | 出所 | 局面 | 候補の上位 (legacy / transition) | 期待との差 | 入力の照合 (別の問題) | 補足 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in doc.get("regression") or []:
        L.append(f"| {REGRESSION_LABELS[r['kind']]} | {r['source']} | {r.get('key') or '-'} | {_cell(r['candidates'])} | "
                 f"{_cell('; '.join(r['problems']) or 'なし')} | {_cell('; '.join(r.get('input_problems') or []) or 'ok')} | "
                 f"{_cell(r.get('note') or '')} |")
    L.append("")
    if doc.get("findings"):
        L += ["## 7. 見つかった入力の誤り・不具合 (修正はしていない)", ""]
        for line in doc["findings"]:
            L.append(f"- {line}")
        L.append("")
    L += [f"## {8 if doc.get('findings') else 7}. 未確認・注意", ""]
    for line in doc.get("unconfirmed") or []:
        L.append(f"- {line}")
    L.append("")
    return "\n".join(L)


# ------------------------------------------------------------------ 再採点 (環境に依存: 図鑑・使用率 DB・RL のピン)
class Replayer:
    """保存した state を evaluate に渡し直す。対戦ごとに自チームの本文を登録し直す (advisor_player.register_team_text)。
    transition は scoring_transition.score を包んで入力を写し取る"""

    def __init__(self, big_budget_ms: float = None):
        from vision.normalize import NameResolver
        import advisor.scoring_transition as ST
        self.ST = ST
        self.resolver = NameResolver()
        self.big_budget_ms = C.DIAG_SCENES_BIG_BUDGET_MS if big_budget_ms is None else float(big_budget_ms)
        self._team = None
        self.captured = None
        self._orig_score = ST.score

    def register(self, text: Optional[str]) -> None:
        from champions_agent.env.advisor_player import register_team_text
        if text != self._team:
            register_team_text(text or "", self.resolver)
            self._team = text

    def evaluate(self, state: dict, mode: str, rl_blend_weight, *, big_budget: bool = True, score_wrapper=None) -> tuple:
        """戻り値 (evaluate の出力, transition の入力の要約 or None)"""
        from advisor.engine import evaluate
        ST = self.ST
        orig_budget = C.TRANSITION_BUDGET_MS
        self.captured = None
        inner = score_wrapper or self._orig_score

        def wrapped(inp, *a, **k):
            self.captured = inp
            return inner(inp, *a, **k)
        ST.score = wrapped
        if big_budget:
            C.TRANSITION_BUDGET_MS = self.big_budget_ms
        try:
            out = evaluate(copy.deepcopy(state), self.resolver, rl_blend_weight=rl_blend_weight, scoring_mode=mode)
        finally:
            ST.score = self._orig_score
            C.TRANSITION_BUDGET_MS = orig_budget
        return out, summarize_inputs(self.captured)

    def what_if_unevaluated(self, state: dict, rl_blend_weight) -> dict:
        """未評価の区間の判定を外した参考の順位: V_MIN / V_MAX を ±1e-9 に縮めると区間はほぼ点 (1 − m) q になり、未評価の応手を
        除いて正規化した q で並ぶ (候補ごとの分岐の未評価 δ が違うと、なお順位が決まらず退避することがある。0 にすると
        score の未評価の質量の計算が 0 で割るので幅を残す)"""
        ST = self.ST
        vmin, vmax = ST.V_MIN, ST.V_MAX
        ST.V_MIN, ST.V_MAX = -1e-9, 1e-9
        try:
            out, _ = self.evaluate(state, "transition", rl_blend_weight)
        finally:
            ST.V_MIN, ST.V_MAX = vmin, vmax
        return out

    def what_if_unsupported(self, state: dict, rl_blend_weight) -> tuple:
        """自分側の未対応技で退避した決定: 未対応の候補を外して採点し直す (外した技の点は出せない)。戻り値 (出力, 外した技)"""
        from dataclasses import replace
        ST = self.ST
        dropped = []
        orig = self._orig_score

        def drop_loop(inp, *a, **k):
            cur = inp
            for _ in range(len(inp.candidates)):
                r = orig(cur, *a, **k)
                fb = str(r.get("fallback") or "")
                if r.get("ok") or not fb.startswith("unsupported:") or not r.get("candidate"):
                    return r
                dropped.append(r["candidate"])
                cur = replace(cur, candidates=[c for c in cur.candidates if c.id != r["candidate"]])
                if not cur.candidates:
                    return r
            return orig(cur, *a, **k)
        out, _ = self.evaluate(state, "transition", rl_blend_weight, score_wrapper=drop_loop)
        _ = ST
        return out, dropped


def _board(state: dict) -> str:
    def side(s):
        party = s.get("party") or []
        ai = s.get("active_index")
        p = party[ai] if ai is not None and 0 <= ai < len(party) else {}
        hp = p.get("hp_percent")
        return f"{p.get('species_id') or '?'} {hp:.0f}%" if hp is not None else f"{p.get('species_id') or '?'}"
    mine, theirs = remaining_of(state)
    return f"{side(state.get('player') or {})} / {side(state.get('opponent') or {})} (残り {mine}-{theirs})"


def _opp_hp(state: dict) -> Optional[float]:
    o = state.get("opponent") or {}
    party = o.get("party") or []
    ai = o.get("active_index")
    if ai is None or not (0 <= ai < len(party)):
        return None
    return party[ai].get("hp_percent")


def _move_cat_fn():
    from advisor.dex import get_dex
    dex = get_dex()

    def f(mid):
        return (dex.move(mid) or {}).get("category")
    return f


def _outs_summary(out: Optional[dict]) -> str:
    acts = (out or {}).get("actions") or []
    return ", ".join(f"{fmt_key(key_of(a))} {a.get('score')}" for a in acts[:4])


def run_diag(sets: list, seed: int, quotas: dict, log=print) -> dict:
    """診断の本体 (再採点を含む。環境に依存)。sets = [(組の名前, 対戦記録の列, check_advisor_player の結果 dict)]。
    組が 2 つ以上なら局面の鍵に「組の名前:」を付ける。戻り値は render_markdown の入力"""
    import advisor.engine as eng
    from champions_agent.env.sim_accept import accept_violations, sum_battle_accept
    result = sets[0][2]
    eng.BELIEF_K = int(result.get("belief_k") or 0)
    if result.get("search_blend") is not None:
        eng.SEARCH_BLEND = float(result["search_blend"])
    rp = Replayer()
    move_cat = _move_cat_fn()
    multi = len(sets) > 1
    decs = []
    for label, records, _res in sets:
        decs += iter_decisions(records, prefix=f"{label}:" if multi else "")
    rows = {}
    pools: dict = {}
    for i, dec in enumerate(decs):
        d = dec["diag"]
        st = scene_status(d)
        tr_rec, lg_rec = modes_of(d)
        w = d.get("rl_blend_weight")
        rp.register(dec["own_team_text"])
        state = d["state"]
        row = {"key": dec["key"], "set": dec["set"], "turn": dec["turn"], "battle_id": dec["battle_id"], "status": st,
               "a_L": st["a_L"], "a_T": st["a_T"], "board": _board(state) if state else "-"}
        if not state:
            pools["excluded"] = pools.get("excluded", 0) + 1
            rows[dec["key"]] = row
            continue
        # 除外の決定 (候補 1 つ等) も再採点する (抽出はしないが、回帰ケース・全決定の再現性と入力の照合に数える)
        lg, _ = rp.evaluate(state, "legacy", w)
        tr, inp = rp.evaluate(state, "transition", w)
        row["replay"] = {"legacy": lg, "transition": tr, "inputs": inp}
        row["repro"] = {"legacy": repro_verdict(lg_rec, lg, "legacy"), "transition": repro_verdict(tr_rec, tr, "transition")}
        row["input_problems"] = check_inputs(state, d.get("legal") or {}, {"legacy": lg, "transition": tr}, inp)
        if st["status"] == "excluded":
            pools["excluded"] = pools.get("excluded", 0) + 1
        elif st["status"] == "differ":
            lgb, trb = by_key(lg.get("actions")), by_key(tr.get("actions"))
            bucket = a_bucket(st["a_L"], st["a_T"], lgb, trb, remaining_of(state), move_cat)
            row["status"] = dict(st, bucket=bucket)
            pools.setdefault(bucket, []).append(dec["key"])
        elif st["status"] == "fallback":
            pools.setdefault(st["bucket"], []).append(dec["key"])
        else:
            pools.setdefault("C_agree", []).append(dec["key"])
        rows[dec["key"]] = row
        if (i + 1) % 50 == 0:
            log(f"[diag_scenes] 再採点 {i + 1}/{len(decs)}")
    pool_lists = {b: v for b, v in pools.items() if isinstance(v, list)}
    chosen, short = sample_scenes(pool_lists, quotas, seed)
    scenes = {}
    dec_by = {x["key"]: x for x in decs}
    for b, keys in chosen.items():
        for k in keys:
            row = rows[k]
            dec = dec_by[k]
            d = dec["diag"]
            rp.register(dec["own_team_text"])
            state = d["state"]
            w = d.get("rl_blend_weight")
            lg, tr = row["replay"]["legacy"], row["replay"]["transition"]
            sc = {"set": row["set"], "turn": row["turn"], "board": row["board"], "a_L": row["a_L"], "a_T": row["a_T"],
                  "repro": {m: _repro_short(row["repro"][m]) for m in ("legacy", "transition")},
                  "repro_detail": row["repro"], "input_problems": row["input_problems"],
                  "legacy_top": _outs_summary(lg), "transition_top": _outs_summary(tr)}
            if b.startswith("A_"):
                norl, _ = rp.evaluate(state, "legacy", 0.0)
                sc["decompose"] = decompose(row["a_L"], row["a_T"], by_key(lg.get("actions")), by_key(tr.get("actions")),
                                            _opp_hp(state), legacy_norl_best=key_of(norl.get("best")), move_cat=move_cat)
                sc["legacy_norl_best"] = key_of(norl.get("best"))
            elif b.startswith("B_"):
                tr_rec, _lg_rec = modes_of(d)
                sc["fallback"] = tr_rec.get("scoring_fallback")
                sc["what_if"] = _what_if(b, rp, state, w, tr, row["a_L"])
            else:
                sc["margin"] = agree_margin(lg.get("actions"), tr.get("actions"))
            scenes[k] = sc
    regression = _regression(decs, rows, rp, seed, move_cat)
    per_set = []
    all_records = []
    for label, records, res in sets:
        acc = sum_battle_accept(records) or {}
        per_set.append({"label": label, "n_battles": len(records),
                        "n_decisions": sum(len(r.get("turns") or []) for r in records),
                        "n_diag": sum(1 for x in decs if x["set"] == (label if multi else "")),
                        "accept": {k: v for k, v in acc.items() if k != "inputs_version"},
                        "accept_violations": accept_violations(acc, label=label),
                        "result_accept": res.get("accept"), "win_rate": res.get("win_rate")})
        all_records += records
    acc = sum_battle_accept(all_records) or {}
    doc = {"n_battles": len(all_records), "n_decisions": sum(len(r.get("turns") or []) for r in all_records),
           "n_diag": len(decs), "accept": {k: v for k, v in acc.items() if k != "inputs_version"},
           "accept_violations": [v for ps in per_set for v in ps["accept_violations"]], "per_set": per_set,
           "pool_sizes": {b: (len(v) if isinstance(v, list) else v) for b, v in pools.items()},
           "pool_by_set": {b: dict(Counter(rows[k]["set"] for k in v)) for b, v in pool_lists.items()},
           "quotas": dict(quotas), "chosen": chosen, "short": short, "scenes": scenes, "regression": regression}
    doc["all_repro"] = _all_repro(rows)
    doc["all_inputs"] = _all_inputs(rows)
    return doc


def _repro_short(v: dict) -> str:
    return {"ok": "ok", "fallback_same": "fb", "time_dependent": "時間", "mismatch": "×"}.get(v.get("status"), "?")


def _what_if(bucket: str, rp: Replayer, state: dict, w, tr_big: dict, a_L) -> str:
    """区分 B: 退避しなければどうなったか (文言)"""
    def first(out):
        b = key_of((out or {}).get("best"))
        return fmt_key(b)
    if bucket == "B_budget":
        if tr_big.get("scoring_fallback"):
            return f"予算を外しても退避 ({tr_big['scoring_fallback']})"
        el = ((tr_big.get("transition_stats") or {}).get("main") or {}).get("elapsed_ms")
        same = "legacy と一致" if key_of(tr_big.get("best")) == a_L else "legacy と異なる"
        return f"予算を外すと transition の第一候補 {first(tr_big)} ({same}、本採点 {el} ms) / 上位: {_outs_summary(tr_big)}"
    if bucket == "B_unevaluated":
        une = ((tr_big.get("transition_stats") or {}).get("unevaluated") or {})
        out = rp.what_if_unevaluated(state, w)
        info = f"未評価 ε {une.get('eps')} ({', '.join(une.get('moves') or [])})"
        if out.get("scoring_fallback"):
            return f"{info} / 区間を点に縮めても第一候補が他の候補を上回らず退避 ({out['scoring_fallback']}。Q が同点か、" \
                   f"候補ごとの分岐の未評価の質量が違う)"
        same = "legacy と一致" if key_of(out.get("best")) == a_L else "legacy と異なる"
        return f"{info} / 区間の判定を外すと (未評価を除いて正規化した q の順) 第一候補 {first(out)} ({same}) / 上位: " \
               f"{_outs_summary(out)}"
    if bucket == "B_own_unsupported":
        out, dropped = rp.what_if_unsupported(state, w)
        if out.get("scoring_fallback"):
            return f"未対応の候補 {dropped} を外しても退避 ({out['scoring_fallback']})"
        same = "legacy と一致" if key_of(out.get("best")) == a_L else "legacy と異なる"
        return f"未対応の候補 {dropped} を外す (この技の点は出せない) と第一候補 {first(out)} ({same}) / 上位: {_outs_summary(out)}"
    return "-"


def _regression(decs: list, rows: dict, rp: Replayer, seed: int, move_cat) -> list:
    """必須の回帰ケース。診断対戦にあれば固定 seed でその中から 1 つ、無ければ合成"""
    from champions_agent.env.sim_accept import decision_flags
    rng = random.Random(seed + 1)
    found = {k: [] for k in REGRESSION_KINDS}
    for dec in decs:
        st = dec["diag"].get("state")
        if not st or "replay" not in rows[dec["key"]]:
            continue
        for kind in regression_kinds_of(st):
            found[kind].append(dec["key"])
    base = next((dec for dec in decs if dec["diag"].get("state") and rows[dec["key"]]["status"]["status"] != "excluded"
                 and not dec["diag"]["state"].get("pending_pivot_switch")
                 and len(((dec["diag"]["state"]["player"]["party"][dec["diag"]["state"]["player"]["active_index"]])
                          .get("legal_move_ids")) or []) >= 2
                 and len(dec["diag"].get("legal", {}).get("switches") or []) >= 1), None)
    out = []
    for kind in REGRESSION_KINDS:
        if found[kind]:
            k = rng.choice(sorted(found[kind]))
            dec = next(x for x in decs if x["key"] == k)
            st = dec["diag"]["state"]
            rp.register(dec["own_team_text"])
            outs = {"legacy": rows[k]["replay"]["legacy"], "transition": rows[k]["replay"]["transition"]}
            probs = regression_expect(kind, st, outs)
            by_set = Counter(rows[x]["set"] or "-" for x in found[kind])
            note = f"診断対戦で {len(found[kind])} 決定 ({', '.join(f'{a} {b}' for a, b in sorted(by_set.items()))})"
            out.append({"kind": kind, "source": "診断対戦", "key": k, "problems": probs, "note": note,
                        "input_problems": list(rows[k]["input_problems"]),
                        "candidates": f"{_outs_summary(outs['legacy'])} / {_outs_summary(outs['transition'])}"})
            continue
        if base is None:
            out.append({"kind": kind, "source": "合成できず", "key": None, "problems": ["合成の元にできる決定が無い"],
                        "candidates": "-"})
            continue
        rp.register(base["own_team_text"])
        w = base["diag"].get("rl_blend_weight")
        st = synth_state(base["diag"]["state"], kind)
        outs = {}
        for mode in ("legacy", "transition"):
            outs[mode], _inp = rp.evaluate(st, mode, w)
        probs = regression_expect(kind, st, outs)
        note = f"合成 (元: {base['key']})"
        if kind == "picked_missing":
            orig = rows[base["key"]]["replay"]
            unpicked = sorted(p.get("species_id") for p in base["diag"]["state"]["player"]["party"] if not p.get("is_picked"))
            added = sorted({a.get("id") for o in outs.values() for a in o.get("actions") or []
                            if a.get("kind") == "switch" and a.get("id") in unpicked})
            flags = decision_flags(outs["transition"], None, [], base["diag"]["legal"]["moves"],
                                   base["diag"]["legal"]["switches"], True, None, random_fallback=False)
            changed = [m for m in ("legacy", "transition")
                       if key_of(outs[m].get("best")) != key_of((orig.get(m) or {}).get("best"))]
            note += (f"。未選出 {unpicked} のうち候補に入った {added}、第一候補が変わった方式 {changed or 'なし'}、"
                     f"監査の picked_unknown = {flags['picked_unknown']} (測定では入力不成立として正式判定を出さない)")
            if not flags["picked_unknown"]:
                probs.append("監査が picked_unknown を立てない")
        out.append({"kind": kind, "source": "合成", "key": base["key"], "problems": probs, "note": note,
                    "input_problems": [],
                    "candidates": f"{_outs_summary(outs['legacy'])} / {_outs_summary(outs['transition'])}"})
    _ = move_cat
    return out


def _all_repro(rows: dict) -> dict:
    c = Counter()
    for r in rows.values():
        rep = r.get("repro")
        if not rep:
            continue
        for m in ("legacy", "transition"):
            c[f"{m}:{rep[m]['status']}"] += 1
    return dict(sorted(c.items()))


def _all_inputs(rows: dict) -> dict:
    n = bad = 0
    kinds = Counter()
    keys = []
    for r in rows.values():
        if "input_problems" not in r:
            continue
        n += 1
        if r["input_problems"]:
            bad += 1
            keys.append(r["key"])
            for p in r["input_problems"]:
                kinds[p] += 1
    return {"n": n, "bad": bad, "kinds": dict(kinds), "keys": keys}


def aggregate_lines(doc: dict) -> list:
    """集計の文言 (純粋)"""
    scenes, chosen = doc.get("scenes") or {}, doc.get("chosen") or {}
    a_keys = [k for b in A_BUCKETS for k in chosen.get(b) or []]
    causes = Counter(scenes[k]["decompose"]["cause"] for k in a_keys if "decompose" in scenes[k])
    lines = [f"区分 A {len(a_keys)} 件の主因: " + ", ".join(f"{CAUSE_LABELS[c]} {causes.get(c, 0)}" for c in CAUSES)]
    for b in A_BUCKETS:
        cb = Counter(scenes[k]["decompose"]["cause"] for k in chosen.get(b) or [])
        lines.append(f"  - {BUCKET_LABELS[b]}: " + ", ".join(f"{CAUSE_LABELS[c]} {cb.get(c, 0)}" for c in CAUSES))
    sets = sorted({k.split(":", 1)[0] for k in a_keys if ":" in k})
    for st in sets:
        cs = Counter(scenes[k]["decompose"]["cause"] for k in a_keys if k.startswith(st + ":"))
        lines.append(f"  - 組 {st}: " + ", ".join(f"{CAUSE_LABELS[c]} {cs.get(c, 0)}" for c in CAUSES))
    all_keys = [k for ks in chosen.values() for k in ks]
    not_rep = [k for k in all_keys if any(v == "×" for v in scenes[k]["repro"].values())]
    time_dep = [k for k in all_keys if scenes[k]["repro"].get("transition") == "時間"]
    lines.append(f"抽出 {len(all_keys)} 局面の再現性: 再現できなかった {len(not_rep)} 件{(' (' + ', '.join(not_rep) + ')') if not_rep else ''}"
                 f"、元が予算超過の退避 (時間依存、別扱い) {len(time_dep)} 件")
    bad = [k for k in all_keys if scenes[k]["input_problems"]]
    lines.append(f"抽出 {len(all_keys)} 局面の入力の誤り: {len(bad)} 件{(' (' + ', '.join(bad) + ')') if bad else ''}")
    lines.append(f"判断保留: {causes.get('pending', 0)} 件")
    ar = doc.get("all_repro") or {}
    lines.append("参考 (全決定の再採点): " + ", ".join(f"{k} {v}" for k, v in ar.items()))
    ai = doc.get("all_inputs") or {}
    lines.append(f"参考 (全決定の入力の照合): {ai.get('n')} 決定のうち問題あり {ai.get('bad')}"
                 + (f" ({', '.join(ai.get('keys') or [])}: {'; '.join(ai.get('kinds') or {})})" if ai.get("bad") else ""))
    reg = doc.get("regression") or []
    lines.append("回帰ケース: " + ", ".join(f"{REGRESSION_LABELS[r['kind']]} ({r['source']}) "
                                       f"{'期待どおり' if not r['problems'] else '期待と違う'}" for r in reg))
    return lines


# ------------------------------------------------------------------ CLI
def _read_jsonl(path) -> list:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _prepare_env(result: dict) -> None:
    """再採点の前に、診断対戦と同じ環境を立てる (engine・rl_bridge・config を import する前に呼ぶ)"""
    if result.get("models_dir"):
        os.environ["CHAMPIONS_MODELS_DIR"] = str(result["models_dir"])
    os.environ[C.MY_TEAM_DISABLED_ENV] = "1"
    if result.get("transition_sensitivity") is False:
        os.environ[C.TRANSITION_SENSITIVITY_ENV] = "0"
        C.TRANSITION_SENSITIVITY_ENABLED = False


def _pairs(items: list) -> list:
    """["名前=パス", ...] → [(名前, パス)] (名前が無ければ空の名前)"""
    out = []
    for it in items or []:
        name, sep, path = it.partition("=")
        out.append((name, path) if sep else ("", it))
    return out


def main(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="40 局面の診断 (M3b)")
    ap.add_argument("battles", nargs="+", help="対戦記録 battles.jsonl (複数の組なら 名前=パス)")
    ap.add_argument("--result", action="append", required=True,
                    help="check_advisor_player の結果 JSON (組と同じ順、名前=パス可。models_dir・belief_k・受入監査)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument("--seed", type=int, default=C.DIAG_SCENES_SEED)
    ap.add_argument("--conditions", default=None,
                    help="文書の条件・見つかった誤り・未確認 (JSON: {\"conditions\": {...}, \"findings\": [...], \"unconfirmed\": [...]})")
    args = ap.parse_args(argv)
    bpairs, rpairs = _pairs(args.battles), _pairs(args.result)
    if len(bpairs) != len(rpairs):
        raise SystemExit("battles と --result の数が違う")
    results = [json.loads(Path(p).read_text(encoding="utf-8")) for _n, p in rpairs]
    dirs = {r.get("models_dir") for r in results}
    if len(dirs) > 1:
        raise SystemExit(f"組ごとの RL のピンが違う: {dirs}")
    _prepare_env(results[0])
    sets = [(name, _read_jsonl(path), res) for (name, path), res in zip(bpairs, results)]
    doc = run_diag(sets, args.seed, C.DIAG_SCENES_QUOTAS)
    extra = json.loads(Path(args.conditions).read_text(encoding="utf-8")) if args.conditions else {}
    doc["conditions"] = extra.get("conditions") or {}
    doc["rules"] = rules_lines(args.seed)
    doc["short_reasons"] = [f"{BUCKET_LABELS[b]}: 母集団 {doc['pool_sizes'].get(b, 0)} 件 < 予定 {doc['quotas'][b]} 件 "
                            f"(診断対戦 {doc['n_battles']} 戦で該当が足りない。埋めない)" for b in doc["short"]]
    doc["aggregate"] = {"lines": aggregate_lines(doc)}
    doc["unconfirmed"] = list(extra.get("unconfirmed") or [])
    doc["findings"] = list(extra.get("findings") or [])
    Path(args.out).write_text(render_markdown(doc), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(_jsonable_doc(doc), ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(doc["aggregate"]["lines"]))
    return 0


def rules_lines(seed: int) -> list:
    return [
        "区分の決め方 (決定ごと): transition が退避 → B (理由で 予算超過 budget / 未評価の区間 opp_unevaluated_ambiguous・"
        "unevaluated_ambiguous / 自分側の未対応技 unsupported:*、それ以外の理由は B その他で抽出しない)、両方式の第一候補が異なる → A、"
        "一致 → C。助言なし・どちらかの候補が 1 つの決定は除外",
        "A の小区分 (上から順に当てはめる): (1) a_L か a_T が変化技 → 変化技が絡む / (2) 自滅する技 (legacy の self_cost.faints)、"
        f"攻撃と交代の組で攻撃側が先に倒される見込み (legacy の ×0.25 か transition の p_ko_before_act ≥ {C.DIAG_SCENES_PROB})、"
        f"自分と相手の残りがどちらも {C.DIAG_SCENES_ENDGAME_REMAINING} 以下 → 自滅・温存・終盤 / (3) 攻撃と交代の組 → 攻撃 対 交代 / "
        "(4) それ以外 (攻撃 対 攻撃・交代 対 交代) は抽出しない",
        f"抽出: 区分ごとに局面の鍵 (b<対戦>d<決定>) の順に並べ、random.Random({seed}) の 1 つの列で区分の順 "
        f"({', '.join(BUCKET_ORDER)}) に sample する。該当が予定に足りなければ全件を取り、足りない分は埋めない",
        f"順位差の主因 (区分 A、decompose): 候補の欠け → 判断保留 / transition の Q の差 < {C.DIAG_SCENES_TIE_Q} (同点) → 判断保留 / 未評価の質量 ≥ {C.DIAG_SCENES_UNEVAL_MAIN} → (iii) / RL 加点を外すと"
        " legacy も a_T → (ii) (RL 加点) / legacy の前提 (×0.25 の割引・倒せる見込み・着地で倒される) と transition の分岐 "
        f"(p_ko_before_act・p_ko・p_faint、確率 ≥ {C.DIAG_SCENES_PROB} を起きる側、倒せる見込みは行動できた分岐の p_ko / (1 − p_ko_before_act) と差 ≥ {C.DIAG_SCENES_KO_GAP}。変化技の legacy の −15「倒される危険あり」も先に倒される前提に数える) の"
        "食い違い (両方の候補に同じ種類・向きであれば相殺) が順位差を説明する向きだけ → (i)、両方向 → 判断保留、説明する食い違いが無い → (ii)",
    ]


def _jsonable_doc(doc: dict) -> dict:
    return json.loads(json.dumps(doc, ensure_ascii=False, default=str))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
