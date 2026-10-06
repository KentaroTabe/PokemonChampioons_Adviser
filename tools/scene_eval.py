"""正解つき局面 (2026-10-05 ③): 実戦ログから局面を切り出し、「正しい状態での助言」「画像から復元した状態での助言」
「期限内に表示できたか」を比べる枷。

    python -m tools.scene_eval extract --out logs/scenes/set1.jsonl [--consistent 20] [--failure 10] [--last 30]
    python -m tools.scene_eval evaluate --set logs/scenes/set1.jsonl [--json]

局面集の 1 行 (JSONL):
  {"scene_id": "s001", "category": "consistent"|"hp_stuck"|"advice_stop"|"late"|"stale"|"other",
   "source": {"file": 対戦ログ, "advice_id": ..., "t": 生成時刻, "turn": ...},
   "system_state": {助言が見た簡約状態 (battle_logger._compact_state)},
   "system_advice": {"kind", "id", "name"},                     # 当時の推奨 (ログの記録)
   "truth": {"state": null | 手で直した簡約状態,                  # 正しい状態 (ラベル)
             "legal_actions": null | [{"kind", "id"}],           # 正解ラベルで選べる行動
             "deadline_s": null | 10.0,                          # 表示期限 (決定画面が開いてからの秒)
             "notes": ""},
   "labels_status": "unlabeled"|"partial"|"labeled"}

extract は失敗例を意図的に含めて (整合 20 + 既知の失敗 10) 雛形を書く。truth は人が埋める (最善手ではなく、正しい状態・選べる行動・
表示期限だけ)。evaluate は局面ごとに
  - advice_truth  : truth.state があれば、その状態でエンジンを回した推奨
  - advice_system : system_state を復元してエンジンを回した推奨 (当時のコードでの推奨は system_advice に残っている)
  - feasible_system / feasible_truth: 当時の推奨が、システムの状態で / 正解ラベルで選べたか (None = 判定不能)
  - displayed_in_time: 表示時刻 − 決定画面が開いた時刻 ≤ deadline_s (表示の行が無ければ None)
を出す。**この局面集の誤り率は実戦全体の発生率ではない** (失敗例を意図的に含めた層化標本)。集計はカテゴリ別に出す。
純粋関数 (classify / pick_scenes / compare_best / evaluate_scene) は tests/test_advice_trace.py。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

from tools.advice_trace import LATE_SEC, display_rows, feasible_in_state, load_records

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
SCENES_DIR = REPO / "logs" / "scenes"
CATEGORIES = ("consistent", "hp_stuck", "advice_stop", "late", "stale", "other")
FAILURE_CATEGORIES = ("hp_stuck", "advice_stop", "late", "stale")


# ------------------------------------------------------------------ 切り出し (純粋)
def decision_windows(records: list) -> list:
    """決定画面 (command / move_select) が開いた時刻の列 [{"t_open", "turn"}] (純粋。decision_audit と同じ規則の簡約版)"""
    out = []
    open_ = None
    for d in records:
        if d.get("type") != "scene":
            continue
        sc = d.get("scene")
        if sc in ("command", "move_select"):
            if open_ is None:
                open_ = {"t_open": d.get("t"), "turn": d.get("turn")}
                out.append(open_)
        elif sc in ("watch", "field_check", "standby"):
            continue
        else:
            open_ = None
    return out


def classify(advice_rec: dict, records: list, disp_row: Optional[dict], late_sec: float = LATE_SEC) -> str:
    """助言の行 1 つの分類 (純粋): 手動修正が ±30 秒にあれば hp_stuck、表示が無ければ advice_stop、表示が遅ければ late、
    古い状態への表示なら stale、それ以外は consistent"""
    t = float(advice_rec.get("t") or 0)
    for d in records:
        if d.get("type") == "manual_fix" and abs(float(d.get("t") or 0) - t) <= 30.0:
            return "hp_stuck"
    if disp_row is None or not disp_row.get("displayed"):
        return "advice_stop"
    if disp_row.get("stale") is True:
        return "stale"
    if disp_row.get("latency") is not None and disp_row["latency"] > late_sec:
        return "late"
    return "consistent"


def pick_scenes(candidates: list, n_consistent: int, n_failure: int, seed: int = 0) -> list:
    """候補 (category 付き) から整合 n_consistent + 失敗 n_failure を選ぶ (純粋)。失敗はカテゴリを順に回して偏りを避ける。
    足りないカテゴリは他の失敗カテゴリで埋める。seed で決定的"""
    import random
    rng = random.Random(seed)
    cons = [c for c in candidates if c["category"] == "consistent"]
    rng.shuffle(cons)
    chosen = cons[:n_consistent]
    by_cat = {k: [c for c in candidates if c["category"] == k] for k in FAILURE_CATEGORIES + ("other",)}
    for v in by_cat.values():
        rng.shuffle(v)
    fails: list = []
    while len(fails) < n_failure and any(by_cat.values()):
        for k in FAILURE_CATEGORIES + ("other",):
            if by_cat[k] and len(fails) < n_failure:
                fails.append(by_cat[k].pop())
    return chosen + fails


def extract_candidates(path, records: list, late_sec: float = LATE_SEC) -> list:
    """1 対戦ログ → 局面の候補 (battle の助言、provisional を除く) に category を付ける"""
    disp = {r["advice_id"]: r for r in display_rows(records)}
    out = []
    for d in records:
        if d.get("type") != "advice" or d.get("kind") != "battle" or not d.get("advice_id") or not d.get("state"):
            continue
        adv = d.get("advice") or {}
        if adv.get("provisional"):
            continue
        best = adv.get("best") or ((adv.get("actions") or [None])[0])
        out.append({"category": classify(d, records, disp.get(d["advice_id"]), late_sec),
                    "source": {"file": Path(path).name, "advice_id": d["advice_id"], "t": adv.get("t_gen") or d.get("t"), "turn": d.get("turn"),
                               "version_id": d.get("version_id"), "state_id": d.get("state_id")},
                    "system_state": d.get("state"),
                    "system_advice": ({"kind": best.get("kind"), "id": best.get("id"), "name": best.get("name")} if best else None),
                    "display": disp.get(d["advice_id"]),
                    "truth": {"state": None, "legal_actions": None, "deadline_s": None, "notes": ""}, "labels_status": "unlabeled"})
    return out


# ------------------------------------------------------------------ 評価 (純粋な部分)
def compare_best(a: Optional[dict], b: Optional[dict]) -> Optional[bool]:
    if not a or not b:
        return None
    return (a.get("kind"), a.get("id")) == (b.get("kind"), b.get("id"))


def legal_by_label(best: Optional[dict], legal_actions: Optional[list]) -> Optional[bool]:
    """正解ラベル (選べる行動の列) で推奨が選べるか。ラベルが無ければ None (判定不能)"""
    if not best or legal_actions is None:
        return None
    return any((x.get("kind"), x.get("id")) == (best.get("kind"), best.get("id")) for x in legal_actions)


def displayed_in_time(t_open: Optional[float], t_shown: Optional[float], deadline_s: Optional[float]) -> Optional[bool]:
    if t_open is None or t_shown is None or deadline_s is None:
        return None
    return (float(t_shown) - float(t_open)) <= float(deadline_s)


def evaluate_scene(scene: dict, engine=None, resolver=None, t_open: Optional[float] = None) -> dict:
    """1 局面の評価。engine(state_dict, resolver) → advice (省略時は advisor.engine.evaluate)。
    復元は tools.advice_replay.compact_to_engine_state"""
    from tools.advice_replay import compact_to_engine_state
    if engine is None:
        from advisor.engine import evaluate as engine   # noqa: N806
    if resolver is None:
        try:
            from vision.normalize import NameResolver
            resolver = NameResolver()
        except Exception:
            resolver = None
    truth = scene.get("truth") or {}
    sys_adv_logged = scene.get("system_advice")
    out = {"scene_id": scene.get("scene_id"), "category": scene.get("category"), "labels_status": scene.get("labels_status"),
           "system_advice_logged": sys_adv_logged, "advice_system": None, "advice_truth": None,
           "same_as_logged": None, "truth_vs_system": None, "feasible_system": None, "feasible_truth": None, "displayed_in_time": None,
           "error": None}
    try:
        st = compact_to_engine_state(scene["system_state"], resolver)
        a = engine(st, resolver)
        b = a.get("best") or ((a.get("actions") or [None])[0]) if a.get("ok") else None
        out["advice_system"] = {"kind": b.get("kind"), "id": b.get("id"), "name": b.get("name")} if b else None
    except Exception as e:
        out["error"] = f"system: {e!r}"
    if truth.get("state"):
        try:
            st_t = compact_to_engine_state(truth["state"], resolver)
            a = engine(st_t, resolver)
            b = a.get("best") or ((a.get("actions") or [None])[0]) if a.get("ok") else None
            out["advice_truth"] = {"kind": b.get("kind"), "id": b.get("id"), "name": b.get("name")} if b else None
        except Exception as e:
            out["error"] = (out["error"] + "; " if out["error"] else "") + f"truth: {e!r}"
    out["same_as_logged"] = compare_best(out["advice_system"], sys_adv_logged)
    out["truth_vs_system"] = compare_best(out["advice_truth"], out["advice_system"])
    out["feasible_system"] = feasible_in_state(sys_adv_logged, scene.get("system_state"))
    out["feasible_truth"] = legal_by_label(sys_adv_logged, truth.get("legal_actions"))
    if out["feasible_truth"] is None and truth.get("state"):
        out["feasible_truth"] = feasible_in_state(sys_adv_logged, truth["state"])
    disp = scene.get("display") or {}
    out["displayed_in_time"] = displayed_in_time(t_open, disp.get("t_shown"), truth.get("deadline_s"))
    return out


def summarize_eval(rows: list) -> dict:
    """カテゴリ別の集計。注記: この局面集は失敗例を意図的に含めた層化標本で、誤り率は実戦全体の発生率ではない"""
    def agg(rs: list) -> dict:
        def cnt(key, val):
            return sum(1 for r in rs if r.get(key) is val)
        return {"n": len(rs), "truth_vs_system_same": cnt("truth_vs_system", True), "truth_vs_system_diff": cnt("truth_vs_system", False),
                "truth_unlabeled": cnt("advice_truth", None), "infeasible_system": cnt("feasible_system", False),
                "infeasible_truth": cnt("feasible_truth", False), "feasible_truth_unknown": cnt("feasible_truth", None),
                "displayed_in_time": cnt("displayed_in_time", True), "displayed_late": cnt("displayed_in_time", False),
                "display_unknown": cnt("displayed_in_time", None), "changed_since_logged": cnt("same_as_logged", False),
                "errors": sum(1 for r in rs if r.get("error"))}
    out = {"all": agg(rows), "by_category": {c: agg([r for r in rows if r.get("category") == c]) for c in CATEGORIES
                                             if any(r.get("category") == c for r in rows)},
           "note": "失敗例を意図的に含めた層化標本。ここでの誤り率は実戦全体の発生率ではない"}
    return out


# ------------------------------------------------------------------ CLI
def write_scene_set(scenes: list, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i, s in enumerate(scenes, 1):
            s = dict(s, scene_id=s.get("scene_id") or f"s{i:03d}")
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def read_scene_set(path: Path) -> list:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="正解つき局面の切り出しと評価")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("--out", default=str(SCENES_DIR / "set1.jsonl"))
    ex.add_argument("--consistent", type=int, default=20)
    ex.add_argument("--failure", type=int, default=10)
    ex.add_argument("--last", type=int, default=30, help="直近の対戦ログの本数")
    ex.add_argument("--late-sec", type=float, default=LATE_SEC)
    ex.add_argument("--seed", type=int, default=0)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--set", required=True)
    ev.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.cmd == "extract":
        files = sorted(BATTLE_DIR.glob("battle_*.jsonl"))[-args.last:]
        cands: list = []
        for f in files:
            cands += extract_candidates(f, load_records(f), args.late_sec)
        chosen = pick_scenes(cands, args.consistent, args.failure, args.seed)
        write_scene_set(chosen, Path(args.out))
        from collections import Counter
        print(f"候補 {len(cands)} 局面 ({dict(Counter(c['category'] for c in cands))}) → 選んだ {len(chosen)} "
              f"({dict(Counter(c['category'] for c in chosen))})。truth を埋めてから evaluate: {args.out}")
        return
    scenes = read_scene_set(Path(args.set))
    opens: dict = {}
    rows = []
    for s in scenes:
        src = s.get("source") or {}
        f = src.get("file")
        if f and f not in opens:
            opens[f] = decision_windows(load_records(BATTLE_DIR / f))
        t_open = None
        for w in opens.get(f, []):
            if w.get("t_open") is not None and src.get("t") is not None and w["t_open"] <= src["t"]:
                t_open = w["t_open"]
        rows.append(evaluate_scene(s, t_open=t_open))
    summ = summarize_eval(rows)
    if args.json:
        print(json.dumps({"summary": summ, "rows": rows}, ensure_ascii=False, indent=1))
        return
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    for r in rows:
        print(f"{r['scene_id']} [{r['category']}] 当時 {r['system_advice_logged']} / 復元 {r['advice_system']} / 正解 {r['advice_truth']} "
              f"同じ {r['truth_vs_system']} 選べる sys {r['feasible_system']} truth {r['feasible_truth']} 期限内 {r['displayed_in_time']}")


if __name__ == "__main__":
    main()
