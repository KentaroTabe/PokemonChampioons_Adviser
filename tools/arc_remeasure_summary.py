"""アークの判定の再測定 (tools/arc_remeasure.py) の集計: 当時の値と並べ、判定が変わるかを見る。

    python -m tools.arc_remeasure_summary logs/arc_remeasure_<stamp> [--old-root logs] [--json out.json]

腕ごと: 当時の勝率 / 再測定の勝率 / 差 (独立な 2 標本の SE)、登録の異常のサイン (stats の team_reg_*、正常なら出ない)。
判定ごと: B−A の対応差 (同じ seed なので対戦 i が対応する。tools.team_proposal.paired_verdict)、95%CI 下限、事前登録のゲート
(CATALOG の mde) での判定を、当時の json から計算し直した値と再測定の値で並べ、判定が変わったかを出す。
判定の読み方 (decide): 推定 ≥ mde かつ CI 下限 > 0 → 採用。中間 (final でない) で推定 ≥ mde だが CI 下限 ≤ 0 → 判定不能
(2026-09-04 22:10 の P9 中間と同じ読み)。それ以外 → 棄却。
対戦数を上書きした煙試験 (manifest の smoke) は「煙試験」と表示し、判定には使わない。
受入 (2026-10-15 M3a): 再測定の json の accept (champions_agent.env.sim_accept) が上限を超えた腕を含む判定は、再測定の判定を
「受入不成立」にして判定の変化を出さない。accept の無い json (修正前の版) は「受入監査なし」と表示する。再測定の腕の
inputs_version が今の版でない・腕の間で違うときは警告を出す。
入力の実体の固定 (2026-10-23、tools.inputs_pin): manifest.json に inputs_pin (ピンの選出モデル一組・使用率の bundle・バンクの写しの
manifest) があれば、ピンのファイルの sha256 を照合し、欠落・不一致なら再測定の判定を「判定不能 (入力の固定を確かめられない)」にする
(apply_inputs_pin)。inputs_pin の無い manifest (今の arc_remeasure はピンに入力の実体を置かない) は「入力の実体の固定なし」と表示する。
純粋な部分は tests/test_arc_remeasure.py と tests/test_inputs_pin.py で確かめる。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

from champions_agent.env.sim_accept import accept_violations, version_warnings
from tools.arc_remeasure import ALIASES, CATALOG, VERDICT_ORDER
from tools.team_proposal import paired_verdict

REPO = Path(__file__).resolve().parent.parent
Z95 = 1.96
ADOPT, INCONCLUSIVE, REJECT, UNTESTABLE = "採用", "判定不能", "棄却", "検定不能"
ACCEPT_FAILED = "受入不成立"     # 再測定の受入監査を満たさない (2026-10-15 M3a)
INPUTS_UNVERIFIED = "判定不能 (入力の固定を確かめられない)"     # 2026-10-23: ピンの入力の欠落・sha 不一致


# ------------------------------------------------------------------ 純粋
def decide(mean: Optional[float], lo: Optional[float], mde: float, final: bool) -> str:
    if mean is None or lo is None:
        return UNTESTABLE
    if mean >= mde and lo > 0:
        return ADOPT
    if not final and mean >= mde:
        return INCONCLUSIVE
    return REJECT


def paired(b: Optional[dict], a: Optional[dict], mde: float, final: bool) -> dict:
    """B−A の対応差とゲートの判定 (純粋)。片方が無ければ検定不能"""
    if not b or not a:
        return {"n": 0, "mean": None, "se": None, "lo": None, "verdict": UNTESTABLE}
    v = paired_verdict(b.get("outcomes") or [], a.get("outcomes") or [])
    mean, se = v.get("mean"), v.get("se")
    lo = (mean - Z95 * se) if mean is not None and se is not None else None
    return {"n": v["n"], "mean": mean, "se": se, "lo": lo, "verdict": decide(mean, lo, mde, final)}


def reg_signals(doc: Optional[dict]) -> dict:
    """登録の異常のサイン (stats の team_reg_<how>。正常なら空) と フォールバック・例外の数 (純粋)"""
    st = (doc or {}).get("stats") or {}
    out = {k: v for k, v in st.items() if str(k).startswith("team_reg_")}
    for k in ("fallback", "error"):
        if st.get(k):
            out[k] = st[k]
    return out


def accept_problems(doc: Optional[dict]) -> Optional[list]:
    """再測定の json の受入の検査 (純粋)。accept の欄が無ければ None (修正前の版: 検査できない)、あれば上限を超えた項目の理由"""
    if not doc or "accept" not in doc:
        return None
    return accept_violations(doc.get("accept"))


def arm_row(verdict: str, arm: str, old: Optional[dict], new: Optional[dict]) -> dict:
    """腕の当時と再測定の比較 (純粋)。差の SE は独立な 2 標本 (Showdown の乱数は対戦ごとに違うので対応させない)"""
    def wr(d):
        return (d["wins"] / d["n_battles"]) if d and d.get("n_battles") else None
    o, n = wr(old), wr(new)
    diff = se = None
    if o is not None and n is not None:
        diff = n - o
        se = math.sqrt(o * (1 - o) / old["n_battles"] + n * (1 - n) / new["n_battles"])
    return {"verdict": verdict, "arm": arm,
            "old_n": (old or {}).get("n_battles"), "old_wr": o,
            "new_n": (new or {}).get("n_battles"), "new_wr": n, "diff": diff, "se": se,
            "search_blend": (new or old or {}).get("search_blend"),
            "battle_format": (new or {}).get("battle_format"), "meta_snapshot": (new or {}).get("meta_snapshot"),
            "models_dir": (new or {}).get("models_dir"), "reg_signals": reg_signals(new),
            "inputs_version": (new or {}).get("inputs_version"), "accept_problems": accept_problems(new)}


def verdict_row(verdict: str, old: dict, new: dict, smoke: bool) -> dict:
    """判定の当時と再測定の比較 (純粋)。old / new は {腕: json}"""
    entry = CATALOG[verdict]
    po = paired(old.get("B"), old.get("A"), entry["mde"], entry["final"])
    pn = paired(new.get("B"), new.get("A"), entry["mde"], entry["final"])
    problems = [f"{a}: {x}" for a in ("A", "B") for x in (accept_problems(new.get(a)) or [])]
    if problems:
        pn = dict(pn, verdict=ACCEPT_FAILED)
    present = {a: d for a, d in new.items() if d}
    warnings = version_warnings({a: d.get("inputs_version") for a, d in present.items()}) \
        if any("inputs_version" in d for d in present.values()) else []
    changed = None
    if not smoke and pn["verdict"] not in (UNTESTABLE, ACCEPT_FAILED) and po["verdict"] != UNTESTABLE:
        changed = pn["verdict"] != po["verdict"]
    return {"verdict": verdict, "label": entry["label"], "rule": entry["rule"], "mde": entry["mde"],
            "final": entry["final"], "old_recorded": entry["old_verdict"], "old": po, "new": pn,
            "smoke": smoke, "changed": changed, "accept_problems": problems,
            "accept_audited": all(accept_problems(new.get(a)) is not None for a in ("A", "B") if new.get(a)),
            "version_warnings": warnings}


def build_summary(out_docs: dict, old_docs: dict, smoke: bool) -> dict:
    """out_docs / old_docs = {判定: {腕: json}} → 腕の行と判定の行 (純粋)"""
    arms, verdicts = [], []
    for v in VERDICT_ORDER:
        if v not in out_docs:
            continue
        new, old = out_docs.get(v) or {}, old_docs.get(v) or {}
        for a in CATALOG[v]["arms"]:
            if a in new:
                arms.append(arm_row(v, a, old.get(a), new.get(a)))
        if all(a in new for a in CATALOG[v]["arms"]):
            verdicts.append(verdict_row(v, old, new, smoke))
    base_v, base_arms = ALIASES["baseline"]
    baseline = next((r for r in arms if r["verdict"] == base_v and r["arm"] in base_arms), None)
    return {"smoke": smoke, "arms": arms, "verdicts": verdicts, "baseline": baseline,
            "registration_ok": all(not r["reg_signals"].get(k) for r in arms
                                   for k in r["reg_signals"] if k.startswith("team_reg_"))}


def apply_inputs_pin(summary: dict, inputs_pin: Optional[dict], reasons: Optional[list]) -> dict:
    """入力の実体の固定の照合結果を集計に反映する (純粋)。inputs_pin = manifest の inputs_pin (無ければ None = 固定なし)、
    reasons = 照合の理由 (tools.inputs_pin.verify_pin)。理由があれば再測定の判定を INPUTS_UNVERIFIED にして判定の変化を出さない"""
    out = dict(summary)
    out["inputs_pin"] = None if inputs_pin is None else {"reasons": list(reasons or []),
                                                          "selection_sha16": (inputs_pin.get("selection_model") or {}).get("sha16"),
                                                          "usage_snapshot": (inputs_pin.get("usage_bundle") or {}).get("snapshot_id"),
                                                          "bank_sha16": (inputs_pin.get("real_bank") or {}).get("sha16")}
    if inputs_pin is not None and reasons:
        out["verdicts"] = [dict(v, new=dict(v["new"], verdict=INPUTS_UNVERIFIED), changed=None) for v in summary["verdicts"]]
    return out


def _f(x, fmt="{:.3f}") -> str:
    return "—" if x is None else fmt.format(x)


def render(summary: dict) -> str:
    lines = []
    if summary["smoke"]:
        lines.append("※ 煙試験 (対戦数を上書き)。判定には使わない")
    lines.append("腕            当時            再測定          差 (±SE)          登録のサイン / FB・例外")
    for r in summary["arms"]:
        lines.append(f"{r['verdict']}/{r['arm']:<3} "
                     f"{_f(r['old_wr'])} ({r['old_n'] or '—'})   {_f(r['new_wr'])} ({r['new_n'] or '—'})   "
                     f"{_f(r['diff'], '{:+.3f}')} ± {_f(r['se'])}   {r['reg_signals'] or 'なし'}")
    lines.append("")
    for v in summary["verdicts"]:
        o, n = v["old"], v["new"]
        lines.append(f"[{v['verdict']}] {v['label']}  規則: {v['rule']}")
        lines.append(f"   当時   B−A {_f(o['mean'], '{:+.3f}')} ± {_f(o['se'])} (CI下限 {_f(o['lo'], '{:+.3f}')}, n {o['n']}) "
                     f"→ {o['verdict']}   (記録: {v['old_recorded']})")
        lines.append(f"   再測定 B−A {_f(n['mean'], '{:+.3f}')} ± {_f(n['se'])} (CI下限 {_f(n['lo'], '{:+.3f}')}, n {n['n']}) "
                     f"→ {n['verdict']}")
        ch = "—" if v["changed"] is None else ("変わった" if v["changed"] else "変わらない")
        lines.append(f"   判定: {ch}")
        if v.get("accept_problems"):
            lines.append("   受入不成立 (再測定の判定は出さない): " + " / ".join(v["accept_problems"]))
        elif not v.get("accept_audited", True):
            lines.append("   受入監査なし (再測定の json に accept が無い: 修正前の版の可能性)")
        for w in v.get("version_warnings") or []:
            lines.append(f"   警告: {w}")
    if summary["baseline"]:
        b = summary["baseline"]
        lines.append(f"advisor baseline (p9_300/A): 当時 {_f(b['old_wr'])} → 再測定 {_f(b['new_wr'])}")
    lines.append(f"登録のずれ: {'サインなし (team_reg_* が出ていない)' if summary['registration_ok'] else 'サインあり (team_reg_* を確認)'}")
    if "inputs_pin" in summary:
        ip = summary["inputs_pin"]
        if ip is None:
            lines.append("入力の実体の固定: なし (選出モデル・使用率・バンクは子プロセスの起動時の配備先・最新を読んだ。復元は確かめられない)")
        else:
            lines.append(f"入力の実体の固定: 選出モデル {ip.get('selection_sha16')} / 使用率 snapshot {ip.get('usage_snapshot')} / "
                         f"バンク {ip.get('bank_sha16')} / 照合 " + ("OK" if not ip["reasons"] else "NG: " + " / ".join(ip["reasons"])))
    return "\n".join(lines)


# ------------------------------------------------------------------ 読み込み (副作用)
def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_docs(out_dir: Path, old_root: Path) -> tuple:
    out_docs, old_docs = {}, {}
    for v in VERDICT_ORDER:
        d = out_dir / v
        if not d.is_dir():
            continue
        loaded = {a: _load(d / f"{a}.json") for a in CATALOG[v]["arms"]}
        out_docs[v] = {a: doc for a, doc in loaded.items() if doc is not None}
        old_docs[v] = {a: _load(old_root / arm["old"]) for a, arm in CATALOG[v]["arms"].items()}
    return out_docs, old_docs


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="アークの判定の再測定の集計")
    ap.add_argument("out_dir")
    ap.add_argument("--old-root", default=str(REPO / "logs"), help="当時の記録の置き場 (既定 <repo>/logs)")
    ap.add_argument("--json", default=None, help="集計を JSON でも書く")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    man = _load(out_dir / "manifest.json") or {}
    out_docs, old_docs = load_docs(out_dir, Path(args.old_root))
    if not out_docs:
        print(f"[arc] {out_dir} に判定の結果が無い", file=sys.stderr)
        return 1
    summary = build_summary(out_docs, old_docs, smoke=bool(man.get("smoke")))
    ip = man.get("inputs_pin") if isinstance(man.get("inputs_pin"), dict) else None
    reasons = None
    if ip is not None:
        from tools.inputs_pin import verify_pin
        pin = ip.get("pin_dir") or (man.get("pin") or {}).get("dir")
        reasons = verify_pin(Path(pin), ip) if pin else ["manifest にピンの dir が無い"]
    summary = apply_inputs_pin(summary, ip, reasons)
    print(render(summary))
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
