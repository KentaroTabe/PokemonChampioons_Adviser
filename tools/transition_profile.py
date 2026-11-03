"""transition の処理時間の内訳の計測 (2026-10-16 レビュー、docs/ADVICE_ACCURACY_PLAN_1009.md §8.33)。

    python -m tools.transition_profile --target LOG ... [--repeat N] [--out DIR] [--gap-sec S]

実戦ログの決定点 (tools.real_accept と同じ復元・間引き) を transition で evaluate し、決定ごとに処理時間を次の部分に分けて対応づける:
  - pre   (共通部分): evaluate の開始 〜 advisor.scoring_transition.build_inputs の呼び出し (legacy の採点・合法性・RL の分布など、
            transition の入力の材料。両方式に共通)
  - build (入力作成): build_inputs (TransitionInputs を作るまで。候補・応手の列挙・HP の仮説・持ち物の仮説)
  - main  (本採点): score の main.elapsed_ms (順位を決める計算。予算 TRANSITION_BUDGET_MS の対象)
  - sens  (感度): score の sens.elapsed_ms (楽観側の HP・スカーフ / タスキの仮説での順位。順位には使わない)
  - post  (仕上げ): score の後 〜 evaluate の終わり (加点以降の finish_evaluation、score の戻り値の組み立ての残りを含む)
あわせて elapsed_total_ms (= main + sens)、分岐数 (n_branches)、応手の数 (responses)、候補数、退避の理由 (scoring_fallback)、
感度の状態 (sens.status: run / partial / omitted (予算で省略) / none (仮説なし) / disabled (無効)) を残す。

感度 on (config の既定と同じ) と off (score(..., sensitivity=False)。TRANSITION_SENSITIVITY=0 と同じ) の両方を、決定ごとに
TRANSITION_PROFILE_REPEAT 回ずつ (順番を交互に) 測り、数値は中央値を使う。計測のために build_inputs と score を時刻を記録する
包みに一時的に差し替える (中身は元の関数をそのまま呼ぶ。採点・予算・分岐数は変えない)。

出力: <out>/result.json と report.md (全体の割合、遅い局面 (全処理時間の上位 TRANSITION_PROFILE_TOP 件と p95 以上) の内訳、
分岐数・応手数との関係 (順位相関)、感度 off の時間、感度が主因かの判定)。
感度が主因 = 全処理時間が p95 以上の決定で、evaluate の時間のうち感度が TRANSITION_PROFILE_SENS_DOMINANT_SHARE 以上を占める。

純粋な部分 (median_run / parts_of / spearman / summarize_profile / render_markdown) は tests/test_transition_profile.py でテストする。
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import (TRANSITION_PROFILE_REPEAT, TRANSITION_PROFILE_SENS_DOMINANT_SHARE,
                                    TRANSITION_PROFILE_TOP)
from tools.shadow_load_check import percentiles

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "logs" / "transition_profile"
SENS_STATES = ("on", "off")                  # 感度を計算する (config の既定) / しない (sensitivity=False)
PARTS = ("pre", "build", "main", "sens", "post")
PART_LABELS = {"pre": "共通部分", "build": "入力作成", "main": "本採点", "sens": "感度", "post": "仕上げ"}
NUM_FIELDS = ("eval_ms", "pre_ms", "build_ms", "score_ms", "main_ms", "sens_ms", "total_ms", "post_ms")
# 決定ごとに変わらないはずの値 (繰り返しで食い違えば unstable に残す。退避・感度の状態は予算で揺れうる)
CAT_FIELDS = ("ok", "fallback", "n_branches", "n_responses", "n_candidates", "sens_status", "sens_run", "sens_omitted",
              "n_alts", "error", "sig")


# ------------------------------------------------------------------ 純粋
def median_run(runs: list) -> dict:
    """同じ決定・同じ感度の設定の繰り返し (runs) をまとめる (純粋)。数値 (NUM_FIELDS) は None を除いた中央値 (小数 1 桁)、
    それ以外は 1 回目の値。CAT_FIELDS が繰り返しで食い違えば "unstable" にその鍵を残す"""
    if not runs:
        return {}
    out = {k: v for k, v in runs[0].items() if k not in NUM_FIELDS}
    for k in NUM_FIELDS:
        xs = [float(r[k]) for r in runs if r.get(k) is not None]
        out[k] = round(statistics.median(xs), 1) if xs else None
    out["unstable"] = [k for k in CAT_FIELDS if any(r.get(k) != runs[0].get(k) for r in runs[1:])]
    out["n_runs"] = len(runs)
    return out


def parts_of(m: dict) -> dict:
    """1 決定の内訳 {部分: ms} (純粋)。測れなかった部分 (退避で score を呼ばなかった等) は 0"""
    return {p: float(m.get(f"{p}_ms") or 0.0) for p in PARTS}


def _ranks(xs: list) -> list:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def spearman(xs: list, ys: list) -> Optional[float]:
    """順位相関 (同順位は平均順位、純粋)。組が 3 未満か片方が定数なら None"""
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    rx, ry = _ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return round(sxy / (sxx * syy) ** 0.5, 3)


def _share_block(ms: list) -> dict:
    """決定の集まり (on の中央値の列) の部分ごとの合計 ms と、evaluate の合計に対する割合 (純粋)"""
    tot = {p: round(sum(parts_of(m)[p] for m in ms), 1) for p in PARTS}
    ev = round(sum(float(m.get("eval_ms") or 0.0) for m in ms), 1)
    tt = round(sum(float(m.get("total_ms") or 0.0) for m in ms), 1)
    return {"n": len(ms), "eval_ms": ev, "total_ms": tt, "parts_ms": tot,
            "share_of_eval": {p: (round(v / ev, 3) if ev else None) for p, v in tot.items()},
            "sens_share_of_total": round(tot["sens"] / tt, 3) if tt else None}


def _slim(r: dict) -> dict:
    on, off = r.get("on") or {}, r.get("off") or {}
    pt = parts_of(on)
    ev = float(on.get("eval_ms") or 0.0)
    return {"log": r.get("log"), "advice_id": r.get("advice_id"), "turn": r.get("turn"),
            "eval_ms": on.get("eval_ms"), "total_ms": on.get("total_ms"), "parts_ms": pt,
            "share_of_eval": {p: (round(v / ev, 3) if ev else None) for p, v in pt.items()},
            "n_branches": on.get("n_branches"), "n_responses": on.get("n_responses"), "n_candidates": on.get("n_candidates"),
            "n_alts": on.get("n_alts"), "sens_status": on.get("sens_status"), "sens_run": on.get("sens_run"),
            "sens_omitted": on.get("sens_omitted"), "fallback": on.get("fallback"),
            "eval_off_ms": off.get("eval_ms"), "total_off_ms": off.get("total_ms"),
            "fallback_off": off.get("fallback"), "unstable": sorted(set(on.get("unstable") or []) | set(off.get("unstable") or []))}


def summarize_profile(rows: list, top: int = TRANSITION_PROFILE_TOP,
                      dominant_share: float = TRANSITION_PROFILE_SENS_DOMINANT_SHARE) -> dict:
    """計測の集計 (純粋)。rows の各要素: {"log", "advice_id", "turn", "on": median_run の戻り値, "off": 同}。
    on の数値で 全体の割合・各部分の分布・遅い局面 (total_ms の上位 top 件と p95 以上)・分岐数 / 応手数 / 候補数との順位相関を、
    on / off で evaluate と全処理時間の分布と同じ決定の差 (on − off) を出し、感度が主因か (p95 以上の決定で sens / evaluate ≥
    dominant_share) を判定する"""
    ok = [r for r in rows if (r.get("on") or {}).get("eval_ms") is not None and not (r.get("on") or {}).get("error")]
    ons = [r["on"] for r in ok]
    out: dict = {"n": len(rows), "n_measured": len(ok), "top": top, "dominant_share": dominant_share}
    out["overall"] = _share_block(ons)
    out["dist"] = {f: {st: percentiles([float(r[st][f]) for r in ok if (r.get(st) or {}).get(f) is not None])
                       for st in SENS_STATES} for f in NUM_FIELDS}
    out["delta_eval_ms"] = percentiles([float(r["on"]["eval_ms"]) - float(r["off"]["eval_ms"]) for r in ok
                                        if (r.get("off") or {}).get("eval_ms") is not None])
    out["delta_total_ms"] = percentiles([float(r["on"]["total_ms"]) - float(r["off"]["total_ms"]) for r in ok
                                         if r["on"].get("total_ms") is not None and (r.get("off") or {}).get("total_ms") is not None])
    # 退避・感度の状態
    fb = {st: {} for st in SENS_STATES}
    ss: dict = {}
    for r in ok:
        for st in SENS_STATES:
            k = str((r.get(st) or {}).get("fallback") or "-").split(":")[0]
            fb[st][k] = fb[st].get(k, 0) + 1
        k = str(r["on"].get("sens_status") or "-")
        ss[k] = ss.get(k, 0) + 1
    out["fallback"] = fb
    out["sens_status"] = ss
    # 感度の仮説が予算で省略された決定 (感度が本採点の残りの予算を使い切った = 全処理時間が予算付近まで延びた)
    out["n_sens_budget_omitted"] = sum(1 for r in ok if "budget" in ((r["on"].get("sens_omitted") or {}).values()))
    # 参考: 感度 on / off で候補・得点・順位・第一候補・退避が同じか (tools.real_accept.rank_signature。感度は順位に使わない設計)
    mis = [r.get("advice_id") for r in ok if (r.get("off") or {}).get("sig") != r["on"].get("sig")]
    out["rank_mismatch"] = {"n": len(mis), "decisions": mis[:20]}
    out["n_unstable"] = sum(1 for r in ok if (r["on"].get("unstable") or (r.get("off") or {}).get("unstable")))
    # 遅い局面
    timed = [r for r in ok if r["on"].get("total_ms") is not None]
    timed.sort(key=lambda r: -float(r["on"]["total_ms"]))
    p95 = (out["dist"]["total_ms"]["on"] or {}).get("p95")
    out["p95_total_ms"] = p95
    out["slow_top"] = [_slim(r) for r in timed[:top]]
    tail = [r for r in timed if p95 is not None and float(r["on"]["total_ms"]) >= p95]
    out["p95_set"] = {"block": _share_block([r["on"] for r in tail]), "decisions": [r.get("advice_id") for r in tail]}
    out["slow_block"] = _share_block([r["on"] for r in timed[:top]])
    # 関係 (順位相関)
    rel = {}
    for xk in ("n_branches", "n_responses", "n_candidates", "n_alts"):
        xs = [r["on"].get(xk) for r in timed]
        rel[xk] = {yk: spearman(xs, [r["on"].get(yk) for r in timed]) for yk in ("main_ms", "sens_ms", "build_ms", "eval_ms")}
    out["relation"] = rel
    per_branch = [float(r["on"]["main_ms"]) / float(r["on"]["n_branches"]) for r in timed
                  if r["on"].get("main_ms") is not None and r["on"].get("n_branches")]
    out["main_ms_per_branch"] = {"n": len(per_branch), "p50": round(statistics.median(per_branch), 3) if per_branch else None,
                                 "max": round(max(per_branch), 3) if per_branch else None}
    # 判定
    sh = (out["p95_set"]["block"]["share_of_eval"] or {}).get("sens")
    out["sens_dominant"] = None if sh is None else bool(sh >= dominant_share)
    out["sens_share_p95"] = sh
    return out


def _f(x, nd=1) -> str:
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _pc(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _sens_txt(d: dict) -> str:
    """感度の状態の表示 (状態、計算した仮説、省略した仮説と理由)"""
    run = ",".join(d.get("sens_run") or [])
    om = ",".join(f"{k}:{v}" for k, v in sorted((d.get("sens_omitted") or {}).items()))
    return f"{d.get('sens_status')}" + (f" 計算 {run}" if run else "") + (f" 省略 {om}" if om else "")


def _pct_txt(p: Optional[dict]) -> str:
    p = p or {}
    return f"p50 {_f(p.get('p50'))} / p95 {_f(p.get('p95'))} / max {_f(p.get('max'))} ms (n {p.get('n', 0)})"


def render_markdown(result: dict) -> str:
    """report.md (純粋)"""
    c, s = result["conditions"], result["summary"]
    L = [f"# transition の処理時間の内訳 ({c['at']})", "",
         "実戦ログの決定点を transition で evaluate し、処理時間を 共通部分 / 入力作成 / 本採点 / 感度 / 仕上げ に分けて測る。"
         f"決定ごとに感度 on / off を各 {c['repeat']} 回測った中央値。", "",
         "## 条件", "",
         f"- コード: {c['code'].get('commit') or '-'}{' (未コミットの変更あり)' if c['code'].get('dirty') else ''} / エンジン {c['code'].get('engine')}",
         f"- 予算 TRANSITION_BUDGET_MS {c['transition_budget_ms']} ms、感度の設定値 (config) {c['transition_sensitivity']}、"
         f"ADVICE_DISPLAY_SEARCH {c.get('display_search')}",
         f"- 決定点 {s['n']} (計測できた {s['n_measured']}、間引き {c['gap_sec']} 秒)。ログ: "
         + ", ".join(f"{lg['name']} ({lg['n_decisions']})" for lg in c["logs"]),
         "- 部分: 共通部分 = evaluate の開始〜build_inputs、入力作成 = build_inputs、本採点 = main.elapsed_ms、感度 = sens.elapsed_ms、"
         "仕上げ = score の後〜evaluate の終わり", "",
         "## 全体の割合 (感度 on、全決定の合計)", "",
         "| 部分 | 合計 ms | evaluate に対する割合 |", "|---|---|---|"]
    ov = s["overall"]
    for p in PARTS:
        L.append(f"| {PART_LABELS[p]} | {_f(ov['parts_ms'][p])} | {_pc(ov['share_of_eval'][p])} |")
    L += [f"| evaluate 計 | {_f(ov['eval_ms'])} | |",
          f"| (参考) 全処理時間 elapsed_total = 本採点 + 感度 | {_f(ov['total_ms'])} | 感度 / 全処理時間 {_pc(ov['sens_share_of_total'])} |",
          "", "## 分布 (決定ごとの中央値)", "", "| 値 | 感度 on | 感度 off |", "|---|---|---|"]
    for f in ("eval_ms", "build_ms", "main_ms", "sens_ms", "total_ms", "pre_ms", "post_ms"):
        d = s["dist"][f]
        L.append(f"| {f} | {_pct_txt(d['on'])} | {_pct_txt(d['off'])} |")
    L += [f"| evaluate の差 (on − off、同じ決定) | {_pct_txt(s['delta_eval_ms'])} | |",
          f"| 全処理時間の差 (on − off) | {_pct_txt(s['delta_total_ms'])} | |", "",
          "## 遅い局面", "",
          f"全処理時間 (elapsed_total) の p95 = {_f(s['p95_total_ms'])} ms。",
          "", "| 集まり | 決定 | evaluate 計 ms | " + " | ".join(PART_LABELS[p] for p in PARTS) + " |",
          "|---|---|---|" + "---|" * len(PARTS)]
    for name, b in (("上位 %d 件" % s["top"], s["slow_block"]), ("p95 以上", s["p95_set"]["block"]), ("全体", ov)):
        L.append(f"| {name} | {b['n']} | {_f(b['eval_ms'])} | " + " | ".join(_pc(b['share_of_eval'][p]) for p in PARTS) + " |")
    L += ["", "| # | ログ / advice_id | evaluate ms | 全処理 ms | 入力作成 | 本採点 | 感度 | 共通+仕上げ | 分岐 | 応手 | 候補 | 仮説 | "
          "感度の状態 | 退避 | evaluate (感度 off) |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, d in enumerate(s["slow_top"], 1):
        pt = d["parts_ms"]
        L.append(f"| {i} | {d['log']} {d['advice_id']} | {_f(d['eval_ms'])} | {_f(d['total_ms'])} | {_f(pt['build'])} | "
                 f"{_f(pt['main'])} | {_f(pt['sens'])} ({_pc(d['share_of_eval']['sens'])}) | {_f(pt['pre'] + pt['post'])} | "
                 f"{_f(d['n_branches'])} | {_f(d['n_responses'])} | {_f(d['n_candidates'])} | {_f(d['n_alts'])} | "
                 f"{_sens_txt(d)} | {d['fallback'] or '-'} | {_f(d['eval_off_ms'])} |")
    L += ["", "## 分岐数・応手数・候補数との関係 (順位相関、感度 on)", "",
          "| 説明の値 | 本採点 ms | 感度 ms | 入力作成 ms | evaluate ms |", "|---|---|---|---|---|"]
    for xk, ys in s["relation"].items():
        L.append(f"| {xk} | {_f(ys['main_ms'], 3)} | {_f(ys['sens_ms'], 3)} | {_f(ys['build_ms'], 3)} | {_f(ys['eval_ms'], 3)} |")
    mb = s["main_ms_per_branch"]
    L += ["", f"本採点の分岐あたりの時間: p50 {_f(mb['p50'], 3)} ms / max {_f(mb['max'], 3)} ms (n {mb['n']})", "",
          "## 退避・感度の状態", "",
          "- 退避 (感度 on): " + ", ".join(f"{k} {v}" for k, v in sorted(s["fallback"]["on"].items())),
          "- 退避 (感度 off): " + ", ".join(f"{k} {v}" for k, v in sorted(s["fallback"]["off"].items())),
          "- 感度の状態 (on): " + ", ".join(f"{k} {v}" for k, v in sorted(s["sens_status"].items())),
          f"- 感度の仮説を予算で省略した決定 (感度が予算の残りを使い切った): {s['n_sens_budget_omitted']}",
          f"- 繰り返しで退避・状態・分岐数・順位が揺れた決定: {s['n_unstable']}",
          f"- 参考: 感度 on / off で候補・得点・順位・第一候補・退避が食い違った決定: {s['rank_mismatch']['n']}"
          + (f" ({', '.join(str(x) for x in s['rank_mismatch']['decisions'])})" if s["rank_mismatch"]["n"] else ""), "",
          "## 判定", "",
          f"全処理時間が p95 以上の決定 ({s['p95_set']['block']['n']} 件) で、evaluate の時間のうち感度の割合 "
          f"{_pc(s['sens_share_p95'])} (基準 {_pc(s['dominant_share'])} 以上で感度が主因) → "
          + ("**感度が主因**" if s["sens_dominant"] else ("**感度は主因でない**" if s["sens_dominant"] is False else "判定不能"))]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 副作用あり: 計測
class _Probe:
    """advisor.scoring_transition の build_inputs / score を時刻を記録する包みに一時的に差し替える (中身は元の関数)。
    sensitivity: score に渡す感度の有無 (None なら元のまま = config の既定)"""

    def __init__(self, sensitivity: Optional[bool]):
        self.sensitivity = sensitivity
        self.rec: dict = {}

    def __enter__(self):
        from advisor import scoring_transition as ST
        self.ST = ST
        self.orig_build, self.orig_score = ST.build_inputs, ST.score
        rec = self.rec
        ob, osc, sens = self.orig_build, self.orig_score, self.sensitivity

        def build(*a, **k):
            rec["t_b0"] = time.perf_counter()
            r = ob(*a, **k)
            rec["t_b1"] = time.perf_counter()
            inp = r[0] if isinstance(r, tuple) else None
            if inp is not None:
                rec["n_candidates"] = len(inp.candidates)
                rec["n_alts"] = (1 if inp.hp_alt is not None else 0) + sum(1 for _n, o, _w in inp.item_alts if o is not None)
            return r

        def score(inp, budget_ms=None, sensitivity=None):
            rec["t_s0"] = time.perf_counter()
            r = osc(inp, budget_ms, sensitivity if sens is None else sens)
            rec["t_s1"] = time.perf_counter()
            return r

        ST.build_inputs, ST.score = build, score
        return self

    def __exit__(self, *exc):
        self.ST.build_inputs, self.ST.score = self.orig_build, self.orig_score
        return False


def measure_one(state: dict, resolver, sensitivity: bool) -> dict:
    """1 決定点を transition で 1 回 evaluate し、内訳を返す (副作用: エンジン)"""
    from advisor.engine import evaluate
    s = copy.deepcopy(state)
    with _Probe(sensitivity) as pr:
        t0 = time.perf_counter()
        try:
            adv = evaluate(s, resolver, scoring_mode="transition")
            err = None
        except Exception as e:
            adv, err = None, f"{type(e).__name__}: {e}"
        t1 = time.perf_counter()
    rec = pr.rec
    ms = lambda a, b: round((b - a) * 1000.0, 2)  # noqa: E731
    out = {"eval_ms": ms(t0, t1), "error": err, "ok": bool(adv and adv.get("ok")),
           "fallback": (adv or {}).get("scoring_fallback"),
           "n_candidates": rec.get("n_candidates"), "n_alts": rec.get("n_alts")}
    out["pre_ms"] = ms(t0, rec["t_b0"]) if "t_b0" in rec else None
    out["build_ms"] = ms(rec["t_b0"], rec["t_b1"]) if "t_b1" in rec else None
    out["score_ms"] = ms(rec["t_s0"], rec["t_s1"]) if "t_s1" in rec else None
    end_of = rec.get("t_s1") or rec.get("t_b1")
    out["post_ms"] = ms(end_of, t1) if end_of else None
    ts = (adv or {}).get("transition_stats") or {}
    main, sens = ts.get("main") or {}, ts.get("sens") or {}
    out["main_ms"] = main.get("elapsed_ms", ts.get("elapsed_ms"))
    out["sens_ms"] = sens.get("elapsed_ms")
    out["total_ms"] = ts.get("elapsed_total_ms", ts.get("elapsed_ms"))
    # score の中で本採点・感度の外の時間 (戻り値の組み立て) は仕上げに寄せる
    if out["score_ms"] is not None and out["post_ms"] is not None:
        out["post_ms"] = round(out["post_ms"] + max(0.0, out["score_ms"] - float(out["main_ms"] or 0.0)
                                                    - float(out["sens_ms"] or 0.0)), 2)
    out["n_branches"] = ts.get("n_branches")
    out["n_responses"] = len(ts["responses"]) if isinstance(ts.get("responses"), list) else None
    from tools.real_accept import rank_signature
    out["sig"] = rank_signature(adv)
    out["sens_status"] = sens.get("status")
    out["sens_run"] = list(sens.get("run") or [])
    out["sens_omitted"] = dict(sens.get("omitted") or {})
    return out


def run(paths: list, out_dir: Optional[Path], repeat: int = TRANSITION_PROFILE_REPEAT, gap_sec: Optional[float] = None) -> dict:
    """計測の本体 (副作用: エンジン・出力)"""
    from tools import real_accept as RA
    from tools.advice_replay import DECISION_GAP_SEC
    from vision.normalize import NameResolver
    from champions_agent import config as C
    gap = DECISION_GAP_SEC if gap_sec is None else gap_sec
    logs = RA._load_logs(paths, gap)
    resolver = NameResolver()
    states = []
    for lg in logs:
        for row in lg["decisions"]:
            try:
                states.append((lg["name"], row, RA._engine_state(row, resolver)))
            except Exception as e:
                print(f"  復元できない: {lg['name']} {row.get('advice_id')}: {e}")
    if states:      # 初回の読み込み (図鑑・RL・使用率) を所要時間から外す
        for st in SENS_STATES:
            measure_one(states[0][2], resolver, st == "on")
    rows = []
    t_start = time.time()
    for i, (name, row, state) in enumerate(states):
        runs = {st: [] for st in SENS_STATES}
        for k in range(repeat):
            order = SENS_STATES if (i + k) % 2 == 0 else tuple(reversed(SENS_STATES))
            for st in order:
                runs[st].append(measure_one(state, resolver, st == "on"))
        rows.append({"log": name, "advice_id": row.get("advice_id"), "turn": row.get("turn"),
                     **{st: median_run(runs[st]) for st in SENS_STATES}})
        if (i + 1) % 20 == 0:
            print(f"  {i + 1} / {len(states)} ({time.time() - t_start:.0f} 秒)")
    summary = summarize_profile(rows)
    result = {"conditions": {"at": time.strftime("%Y-%m-%d %H:%M"), "code": RA._code_info(), "repeat": repeat,
                             "transition_budget_ms": C.TRANSITION_BUDGET_MS,
                             "transition_sensitivity": C.TRANSITION_SENSITIVITY_ENABLED,
                             "display_search": getattr(C, "ADVICE_DISPLAY_SEARCH", None), "gap_sec": gap,
                             "wall_sec": round(time.time() - t_start, 1),
                             "logs": [{"name": lg["name"], "n_decisions": lg["n_decisions"]} for lg in logs]},
              "summary": summary, "decisions": rows}
    out_dir = Path(out_dir) if out_dir else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = render_markdown(result)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"保存: {out_dir}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="transition の処理時間の内訳 (入力作成 / 本採点 / 感度) を実戦ログの決定点で測る")
    ap.add_argument("--target", nargs="+", required=True, metavar="LOG", help="対戦ログ (logs/battles/*.jsonl、複数可)")
    ap.add_argument("--repeat", type=int, default=TRANSITION_PROFILE_REPEAT, help="決定ごと・感度 on / off ごとの繰り返し (中央値)")
    ap.add_argument("--out", default=None, help="出力のディレクトリ (既定 logs/transition_profile/<日時>)")
    ap.add_argument("--gap-sec", type=float, default=None, help="決定点の間引き間隔 (秒、既定は tools.advice_replay と同じ)")
    args = ap.parse_args()
    paths = [Path(p) for p in args.target]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"対戦ログが無い: {missing}")
    run(paths, Path(args.out) if args.out else None, max(1, args.repeat), args.gap_sec)


if __name__ == "__main__":
    main()
