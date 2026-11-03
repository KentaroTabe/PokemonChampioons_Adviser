"""シミュレータ (advisor-as-player) の測定の受入監査 (2026-10-15 M3a。純粋な関数だけ)。

本番の測定 (P1 / P2 / P9 / racing) で常に残す軽い監査。決定ごとに次を数え、stats (accept:<項目>) と対戦記録 (battles.jsonl の
欄 accept) に残す。ADVICE_DIAG_LOG の完全な記録 (diag) とは別で、本番では完全な記録を取らない。

- best_illegal: 第一候補が poke-env の合法手 (available_moves / available_switches) に無い
- unpicked_candidate: 助言の候補に未選出の個体への交代が入った
- skip_explained / skip_unexplained: 第一候補を飛ばして別の行動を実行した。状態にシミュレータの合法手が入っていなかった
  (要求が無い等、理由が分かる) なら explained、入っていたのに飛ばしたなら unexplained (状態と実行環境の食い違い)
- random_fallback: 助言から行動を作れず、ランダム行動 (choose_random_move) に退避した
- picked_unknown: 対戦中に選出が分からず (picked_mask_of が None)、全個体を選出扱いにした (入力不成立)
- forced_single: 助言から行動を作れなかったが、合法手が 1 つだけだったのでそれを実行した (ランダムではない。わるあがき・反動等)
- switch_info_unknown: 交代先の照合 (switchable_check) で実行環境の要求が無く照合できなかった
- revive_select: さいきのいのり の蘇生先の選択 (要求の種類 revive_select) を固定規則 (1 体なら確定、複数なら自分の選出順で先) で
  実行した (2026-10-16 E-holdout-1)。助言を通さない決定で、random_fallback にも skip_* にも数えない。違反ではない (件数だけ残す)

受入 (accept_violations): best_illegal / unpicked_candidate / skip_unexplained / random_fallback / picked_unknown が
config.SIM_ACCEPT_LIMITS 以下。満たさなければ正式判定 (usefulness_verdict)・順位判定 (racing)・判定 (arc_remeasure_summary) を
出さない。結果 JSON の inputs_version (config.SIM_INPUTS_VERSION) で、修正前の結果を混ぜない。
"""
from __future__ import annotations

from typing import Iterable, Optional

from champions_agent.config import SIM_ACCEPT_LIMITS, SIM_INPUTS_VERSION

ACCEPT_KEYS = ("best_illegal", "unpicked_candidate", "skip_unexplained", "random_fallback", "picked_unknown")
COUNT_KEYS = ("decisions",) + ACCEPT_KEYS + ("skip_explained", "forced_single", "switch_info_unknown", "revive_select")
STATS_PREFIX = "accept:"
LABELS = {"best_illegal": "第一候補が不合法", "unpicked_candidate": "候補に未選出の個体",
          "skip_unexplained": "説明できない第一候補の飛ばし", "random_fallback": "ランダム行動への退避",
          "picked_unknown": "選出が分からない決定 (入力不成立)"}
# 違反ではないが表示する項目 (usefulness_verdict の表示)
INFO_LABELS = {"revive_select": "蘇生先の選択 (さいきのいのり、固定規則)"}


def _key(a: Optional[dict]) -> Optional[tuple]:
    return (a.get("kind"), a.get("id")) if a and a.get("kind") else None


def decision_flags(advice: Optional[dict], advice_exec: Optional[dict], skipped: list, legal_moves: Iterable,
                   legal_switches: Iterable, legal_known: bool, picked_species: Optional[Iterable],
                   random_fallback: bool, forced: bool = False, revive_select: bool = False) -> dict:
    """1 決定の監査の印 (純粋)。advice = 助言 (evaluate の戻り値)、advice_exec = 実行に使った助言 (STRESS の行動ノイズで best を
    差し替えたもの。無ければ advice)、skipped = choose_with_trace が飛ばした候補、legal_moves / legal_switches = poke-env の合法手
    (技の id / 交代先の種族 id)、legal_known = 状態にシミュレータの合法手を入れたか、picked_species = 選出した個体の種族 id
    (分からなければ None)。revive_select = 蘇生先を固定規則で選んだ (助言なし)。戻り値は COUNT_KEYS の bool (decisions は常に
    True)"""
    adv = advice if (advice and advice.get("ok")) else None
    best = (adv or {}).get("best")
    moves, switches = set(legal_moves or ()), set(legal_switches or ())
    flags = {k: False for k in COUNT_KEYS}
    flags["decisions"] = True
    if best and best.get("kind"):
        legal = (best.get("id") in moves) if best["kind"] == "move" else \
            (best.get("id") in switches) if best["kind"] == "switch" else False
        flags["best_illegal"] = not legal
    if picked_species is None:
        flags["picked_unknown"] = True
    elif adv is not None:
        picked = set(picked_species)
        cands = [best] + list(adv.get("actions") or [])
        flags["unpicked_candidate"] = any(a and a.get("kind") == "switch" and a.get("id") not in picked for a in cands)
    ex = advice_exec if advice_exec is not None else adv
    ex_best = (ex or {}).get("best") if (ex and ex.get("ok")) else None
    if skipped and _key(ex_best) is not None and _key(skipped[0]) == _key(ex_best):
        flags["skip_explained" if not legal_known else "skip_unexplained"] = True
    flags["random_fallback"] = bool(random_fallback)
    flags["forced_single"] = bool(forced)
    flags["revive_select"] = bool(revive_select)
    return flags


def add_flags(counts: dict, flags: dict, prefix: str = "") -> None:
    """印を件数に足す (counts を書き換える)。prefix = "accept:" なら stats の鍵"""
    for k in COUNT_KEYS:
        if flags.get(k):
            counts[prefix + k] = counts.get(prefix + k, 0) + 1


def raised(flags: dict) -> dict:
    """決定の記録に残す印 (decisions 以外で立ったものだけ。何も無ければ空)"""
    return {k: True for k in COUNT_KEYS if k != "decisions" and flags.get(k)}


def summary_from_stats(stats: Optional[dict]) -> dict:
    """stats (accept:<項目>) → 結果 JSON の accept (純粋)。版 (inputs_version) も入れる"""
    st = stats or {}
    out = {k: int(st.get(STATS_PREFIX + k) or 0) for k in COUNT_KEYS}
    out["inputs_version"] = SIM_INPUTS_VERSION
    return out


def accept_violations(acc: Optional[dict], limits: Optional[dict] = None, label: str = "") -> list:
    """受入の検査 (純粋)。acc = summary_from_stats の結果 (または対戦記録の accept を足したもの)。上限を超えた項目の理由の list。
    acc が無ければ ["受入監査の記録が無い"]"""
    lim = dict(SIM_ACCEPT_LIMITS if limits is None else limits)
    pre = f"{label}: " if label else ""
    if not isinstance(acc, dict):
        return [f"{pre}受入監査の記録が無い (修正前の版の結果の可能性)"]
    out = []
    for k in ACCEPT_KEYS:
        n = int(acc.get(k) or 0)
        if n > int(lim.get(k, 0)):
            out.append(f"{pre}{LABELS[k]} {n} 件 (上限 {int(lim.get(k, 0))})")
    return out


def sum_battle_accept(records: Iterable) -> Optional[dict]:
    """対戦記録の列 (battles.jsonl) の accept を足す (純粋)。accept の無い記録が 1 つでもあれば missing に数える"""
    total = {k: 0 for k in COUNT_KEYS}
    n, missing = 0, 0
    for r in records or []:
        n += 1
        acc = (r or {}).get("accept")
        if not isinstance(acc, dict):
            missing += 1
            continue
        for k in COUNT_KEYS:
            total[k] += int(acc.get(k) or 0)
    total["battles"] = n
    total["battles_missing"] = missing
    return total


def version_warnings(versions: dict, expected: str = SIM_INPUTS_VERSION) -> list:
    """結果の版の検査 (純粋)。versions = 名前 → inputs_version (無ければ None)。違う版が混ざる・今の版でない結果があれば警告"""
    out = []
    vals = {str(v) if v is not None else None for v in versions.values()}
    if len(vals) > 1:
        out.append("入力の版 (inputs_version) の違う結果が混ざっている: " +
                   ", ".join(f"{k}={v or '無し (修正前)'}" for k, v in sorted(versions.items(), key=lambda kv: str(kv[0]))))
    old = sorted(str(k) for k, v in versions.items() if v != expected)
    if old:
        out.append(f"今の版 ({expected}) でない結果: {', '.join(old)}")
    return out
