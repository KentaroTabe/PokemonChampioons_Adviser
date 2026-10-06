"""実戦評価 (遵守率つき): 接続テストの実戦ログ (logs/battles、由来ラベルつき) から
実勝率の CI、助言への遵守率、実戦/合成の重みづけ Score を出す。

- 200 戦は early signal。w_N = real_weight(有効標本, CI 半幅) で実戦の重みを増やす (§9-5)
- 遵守率: decision_audit の「従えたか」(助言と実行の一致) を使う。ユーザー行動を模倣教師にはしない
- 反事実評価 (助言 vs 実行を探索器で再評価) は advice_replay の経路を使う (後続)

CLI (試用中 Package の実戦サマリー。scripts/canary_summary.sh と end_connection_test.sh が使う):

    python -m tools.team_build.real_eval                     # experiment ラベル (logs/.experiment_package) の Package
    python -m tools.team_build.real_eval --package <id>
    python -m tools.team_build.real_eval --session           # + 接続テストのマーカー以降だけの集計 (決定監査もその範囲)
    python -m tools.team_build.real_eval --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from tools.battle_outcome import OutcomeTracker
from tools.team_build.verdict import binomial_halfwidth, blended_score, real_weight

REPO = Path(__file__).resolve().parent.parent.parent
BATTLES_DIR = REPO / "logs" / "battles"
EXPERIMENT_MARK = REPO / "logs" / ".experiment_package"      # promote --experiment が書く
SESSION_MARKER = REPO / "logs" / ".connection_test_start"    # start_connection_test.sh が書く


def read_battle_labels(path: Path) -> dict:
    """1 対戦ログの由来ラベル (session 行) と勝敗、助言/実行の件数、自分のパーティ (species: 選出画面で 6 枠読めた種、
    読めなければ対戦中に場に出た自分の種)。party_read = 選出画面で読めたか"""
    out = {"file": Path(path).name, "path": str(path), "source": "organic", "package_id": None, "outcome": None,
           "n_advice": 0, "n_manual_fix": 0, "species": [], "party_read": False}
    party_species = None
    actives: list = []
    ot = OutcomeTracker()   # 勝敗: outcome 行 (最後) + ランク画面前の勝負文言 (tools.battle_outcome)
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            t = d.get("type")
            ot.feed(d)
            if t == "session":
                out["source"] = d.get("source", "organic")
                out["package_id"] = d.get("package_id")
            elif t == "advice":
                out["n_advice"] += 1
            elif t == "manual_fix":
                out["n_manual_fix"] += 1
            elif t == "scene":
                pl = ((d.get("state") or {}).get("player") or {})
                party = pl.get("party") or []
                ids = [p.get("species") for p in party if p.get("species")]
                if d.get("scene") == "selection" and party_species is None and party and len(ids) == len(party):
                    party_species = ids
                idx = pl.get("active")
                if isinstance(idx, int) and 0 <= idx < len(party) and party[idx].get("species"):
                    actives.append(party[idx]["species"])
    except Exception:
        pass
    outcome, _inferred, corrected = ot.result()
    out["outcome"] = outcome if outcome in ("win", "loss") else None
    out["corrected"] = corrected
    out["species"] = sorted(set(party_species or actives))
    out["party_read"] = party_species is not None
    return out


def team_match(species: list, package_species) -> Optional[bool]:
    """対戦ログの自分の種 (species) が Package の 6 体に収まるか。どちらかが無ければ None (判定不能)。純粋"""
    if not species or not package_species:
        return None
    return set(species) <= set(package_species)


def labeled_rows(battles_dir: Path = BATTLES_DIR, package_id: Optional[str] = None, source: Optional[str] = None,
                 since_ts: Optional[float] = None, package_species=None) -> list:
    """由来ラベルで絞った対戦ログの行 (since_ts はファイルの更新時刻で絞る。analyze_battles --session と同じ規則)。
    package_species (Package の 6 体) を渡すと各行に team_match (True / False / None) を付ける:
    experiment ラベルは切り忘れると別のパーティの対戦にも付く (2026-09-29 第16回: 13 ログ中 10 が別パーティ) ので、
    集計 (summarize_rows) は team_match=False の行を勝敗から除く"""
    rows = []
    for p in sorted(Path(battles_dir).glob("battle_*.jsonl")):
        if since_ts is not None and p.stat().st_mtime < since_ts:
            continue
        r = read_battle_labels(p)
        if package_id and r["package_id"] != package_id:
            continue
        if source and r["source"] != source:
            continue
        r["team_match"] = team_match(r["species"], package_species)
        rows.append(r)
    return rows


def summarize_rows(rows: list) -> dict:
    """勝敗の集計。team_match=False (ラベルは付いているが Package と別のパーティ) の行は勝敗から除き、件数だけ残す"""
    usable = [r for r in rows if r.get("team_match") is not False]
    decided = [r for r in usable if r["outcome"]]
    n = len(decided)
    wins = sum(1 for r in decided if r["outcome"] == "win")
    wr = wins / n if n else None
    hw = binomial_halfwidth(wr, n) if n else None
    return {"n_logs": len(rows), "n_decided": n, "wins": wins, "win_rate": wr,
            "ci_halfwidth": (round(hw, 4) if hw is not None else None),
            "weight": (real_weight(n, hw) if n else 0.0),
            "n_team_mismatch": sum(1 for r in rows if r.get("team_match") is False),
            "n_team_unknown": sum(1 for r in usable if r.get("team_match") is None and not r.get("species")),
            "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in ("organic", "recommended", "experiment")}}


def real_summary(package_id: Optional[str] = None, source: Optional[str] = None,
                 battles_dir: Path = BATTLES_DIR, since_ts: Optional[float] = None, package_species=None) -> dict:
    return summarize_rows(labeled_rows(battles_dir, package_id=package_id, source=source, since_ts=since_ts,
                                       package_species=package_species))


def package_species_of(package_id: str) -> Optional[list]:
    """registry の Package の 6 体 (meta.species)。registry に無い / 読めないときは None"""
    try:
        from tools.team_build.registry import Registry
        row = Registry().get(package_id)
        return list((row or {}).get("meta", {}).get("species") or []) or None
    except Exception:
        return None


def selection_compliance(records: list) -> dict:
    """1 対戦の記録 (jsonl の行) から、選出助言 (kind=selection の最後の推奨) と実際の選出の一致と、助言が選出の完了前に出ていたか
    (時間内) を出す (純粋)。遵守モデルは廃止したが、実戦との差の源として記録は残す (2026-10-05)。
    戻り値 {"has_advice", "primary" (model / rule / None), "members_match" (True / False / None), "basis", "timely" (True / False / None),
            "latency_s" (助言 → 選出完了の秒。負なら助言が遅れた)}"""
    final, t_adv = None, None
    for d in records:
        if d.get("type") == "advice" and d.get("kind") == "selection":
            adv = d.get("advice") or {}
            if adv.get("recommend"):
                final, t_adv = adv, d.get("t")
    out = {"has_advice": final is not None, "primary": (final or {}).get("primary"), "members_match": None, "basis": "none",
           "timely": None, "latency_s": None,
           # モデルが ◎ のとき、そのモデルが登録チームで学習済みか (2026-10-06: 未学習の配布版も ◎ になるので層別に要る)
           "model_trained": ((final or {}).get("model_trained") if (final or {}).get("primary") == "model" else None)}
    if final is None:
        return out
    rec_idx = [r.get("index") for r in final["recommend"]]
    rec_names = [r.get("name") for r in final["recommend"]]
    picked_idx, t_done = None, None
    observed: list = []
    for d in records:
        if d.get("type") != "scene":
            continue
        st = d.get("state") or {}
        pl = st.get("player") or {}
        party = pl.get("party") or []
        picked = [i for i, p in enumerate(party) if p.get("picked")]
        if picked and (picked_idx is None or len(picked) >= len(picked_idx)):
            picked_idx = picked
        if t_done is None and (st.get("selection_picked") == 3 or len(picked) >= 3):
            t_done = d.get("t")
        idx = pl.get("active")
        if st.get("scene") != "selection" and idx is not None and 0 <= idx < len(party):
            ja = party[idx].get("ja")
            if ja and ja not in observed:
                observed.append(ja)
    if picked_idx is not None and len(picked_idx) == len(rec_idx):
        out["members_match"] = sorted(picked_idx) == sorted(rec_idx)
        out["basis"] = "picked"
    elif observed:
        if any(ja not in rec_names for ja in observed):
            out["members_match"] = False
        elif len(observed) >= len(rec_names):
            out["members_match"] = True
        out["basis"] = f"observed:{len(observed)}"
    if t_adv is not None and t_done is not None:
        out["latency_s"] = round(float(t_done) - float(t_adv), 1)
        out["timely"] = out["latency_s"] >= 0
    return out


def selection_compliance_rows(paths: list) -> dict:
    """対戦ログの集合 → 選出の遵守率 (一致 / 判定できた数)、時間内率、第一候補 (model / rule) 別の内訳"""
    agg = {"n_battles": 0, "n_with_advice": 0, "n_match": 0, "n_mismatch": 0, "n_unknown": 0, "n_timely": 0, "n_late": 0,
           "by_primary": {}}
    for p in paths:
        try:
            recs = [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
        except Exception:
            continue
        agg["n_battles"] += 1
        c = selection_compliance(recs)
        if not c["has_advice"]:
            continue
        agg["n_with_advice"] += 1
        key = c["primary"] or "unknown"
        if key == "model" and c.get("model_trained") is False:
            key = "model_untrained"             # 未学習の配布版が ◎ だった対戦は分けて数える
        b = agg["by_primary"].setdefault(key, {"n": 0, "match": 0, "mismatch": 0})
        b["n"] += 1
        if c["members_match"] is True:
            agg["n_match"] += 1
            b["match"] += 1
        elif c["members_match"] is False:
            agg["n_mismatch"] += 1
            b["mismatch"] += 1
        else:
            agg["n_unknown"] += 1
        if c["timely"] is True:
            agg["n_timely"] += 1
        elif c["timely"] is False:
            agg["n_late"] += 1
    agg["selection_compliance"] = _ratio(agg["n_match"], agg["n_match"] + agg["n_mismatch"])
    agg["selection_timely_rate"] = _ratio(agg["n_timely"], agg["n_timely"] + agg["n_late"])
    return agg


def compliance_from_audit(audit_rows: list) -> Optional[float]:
    """decision_audit --json の行 (各決定の followed: bool) から遵守率"""
    vals = [bool(r.get("followed")) for r in audit_rows if "followed" in r]
    return round(sum(vals) / len(vals), 3) if vals else None


def blended(real: dict, wr_synthetic: float) -> dict:
    w = real.get("weight", 0.0)
    return {"w_real": round(w, 3), "wr_real": real.get("win_rate"), "wr_synthetic": wr_synthetic,
            "score": round(blended_score(real.get("win_rate"), wr_synthetic, w), 4)}


def _ratio(a: int, b: int) -> Optional[float]:
    return round(a / b, 3) if b else None


def audit_aggregate(paths: list) -> Optional[dict]:
    """決定監査 (tools.decision_audit) を対戦ログに掛けて集計する: 決定数 / 助言あり / 一致 (= 遵守率) / 時間内 / 欠陥。
    decision_audit は画面認識 (cv2) を読み込むので遅延 import。読み込めない環境では None"""
    try:
        from tools.decision_audit import _load, audit_battle
    except ImportError:
        return None
    agg = {"n_battles": 0, "n_decisions": 0, "n_with_advice": 0, "n_agree": 0,
           "n_latency_known": 0, "n_timely": 0, "n_defects": 0}
    for p in paths:
        a = audit_battle(_load(str(p)))
        agg["n_battles"] += 1
        for k in ("n_decisions", "n_with_advice", "n_agree", "n_latency_known", "n_timely"):
            agg[k] += int(a.get(k) or 0)
        agg["n_defects"] += len(a.get("defects") or [])
    agg["advice_rate"] = _ratio(agg["n_with_advice"], agg["n_decisions"])
    agg["compliance"] = _ratio(agg["n_agree"], agg["n_with_advice"])
    agg["timely_rate"] = _ratio(agg["n_timely"], agg["n_latency_known"])
    return agg


def format_summary(s: dict, title: str) -> str:
    lines = [f"[{title}] 対戦ログ {s['n_logs']} / 勝敗確定 {s['n_decided']} (勝 {s['wins']} 敗 {s['n_decided'] - s['wins']})"]
    if s["win_rate"] is not None:
        lines.append(f"  勝率 {s['win_rate']:.1%} ± {s['ci_halfwidth']:.1%} (実戦の重み w={s['weight']:.2f})")
    else:
        lines.append("  勝率: 勝敗確定の対戦がまだ無い")
    bs = s["by_source"]
    lines.append(f"  由来: experiment {bs['experiment']} / recommended {bs['recommended']} / organic {bs['organic']}")
    if s.get("n_team_mismatch"):
        lines.append(f"  ⚠ ラベルは付いているが Package と別のパーティで対戦: {s['n_team_mismatch']} ログ (勝敗の集計から除外。"
                     "別のパーティを使うときは experiment ラベルを OFF に)")
    if s.get("n_team_unknown"):
        lines.append(f"  パーティを読めなかったログ: {s['n_team_unknown']} (集計に含む)")
    return "\n".join(lines)


def format_audit(a: Optional[dict]) -> str:
    if a is None:
        return "  決定監査: 実行できない環境 (tools.decision_audit を読み込めない)"
    if not a["n_decisions"]:
        return f"  決定監査: 決定なし ({a['n_battles']} 対戦)"

    def pct(x: Optional[float]) -> str:
        return "-" if x is None else f"{x:.0%}"

    return (f"  決定監査 ({a['n_battles']} 対戦): 決定 {a['n_decisions']} / 助言あり {pct(a['advice_rate'])} / "
            f"一致 (遵守率) {pct(a['compliance'])} / 時間内 {pct(a['timely_rate'])} / 欠陥 {a['n_defects']} 件")


def _read_ts(path: Path) -> Optional[float]:
    try:
        return float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="試用中 Package の実戦サマリー (由来ラベルつき対戦ログ)")
    ap.add_argument("--package", default=None, help="Package id (既定: logs/.experiment_package の値)")
    ap.add_argument("--session", action="store_true", help="接続テストのマーカー以降だけの集計も出す (決定監査もその範囲)")
    ap.add_argument("--no-audit", action="store_true", help="決定監査の集計を省く")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--battles-dir", default=str(BATTLES_DIR))
    args = ap.parse_args()
    package_id = args.package or (EXPERIMENT_MARK.read_text(encoding="utf-8").strip() if EXPERIMENT_MARK.exists() else "")
    if not package_id:
        raise SystemExit("experiment ラベルが無い (logs/.experiment_package)。--package <id> を指定してください")
    bdir = Path(args.battles_dir)
    species = package_species_of(package_id)
    out: dict = {"package_id": package_id, "package_species": species,
                 "all": real_summary(package_id=package_id, battles_dir=bdir, package_species=species)}
    since = None
    if args.session:
        since = _read_ts(SESSION_MARKER)
        out["session_start"] = since
        out["session"] = (real_summary(package_id=package_id, battles_dir=bdir, since_ts=since, package_species=species)
                          if since else None)
    if not args.no_audit:
        # 決定監査も Package のパーティで対戦したログだけを対象にする
        rows = [r for r in labeled_rows(bdir, package_id=package_id, since_ts=since, package_species=species)
                if r.get("team_match") is not False]
        out["audit"] = audit_aggregate([r["path"] for r in rows])
        # 選出の遵守率と時間内率 (記録だけ。補正には使わない: 2026-10-05)
        out["selection"] = selection_compliance_rows([r["path"] for r in rows])
        # 追跡 (②): 版の一致 (version 行 vs Package の selection_model_sha256) / 表示率と遅延 / 推奨がシステムの状態で選べたか
        try:
            from tools.advice_trace import package_selection_sha, summarize_paths
            out["trace"] = summarize_paths([r["path"] for r in rows], package_selection_sha(package_id))
        except Exception as e:
            out["trace"] = {"error": repr(e)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print(f"Package {package_id} の実戦 (session 行の package_id が一致し、自分のパーティが Package の 6 体に収まる対戦ログ)")
    if species:
        print(f"  Package のパーティ: {' / '.join(species)}")
    else:
        print("  (registry に Package の種が無いためパーティの照合なし)")
    print(format_summary(out["all"], "全期間"))
    if args.session:
        if out["session"] is None:
            print("  接続テストのマーカーが無い (今回分の集計は省略)")
        else:
            print(format_summary(out["session"], "今回の接続テスト (マーカー以降)"))
    if "audit" in out:
        print(format_audit(out["audit"]))
    if out.get("selection"):
        sc = out["selection"]
        print(f"[選出] 助言あり {sc['n_with_advice']}/{sc['n_battles']} / 一致 {sc['n_match']} 不一致 {sc['n_mismatch']} 不明 {sc['n_unknown']} "
              f"→ 遵守率 {sc['selection_compliance']} / 時間内率 {sc['selection_timely_rate']} / 第一候補別 {sc['by_primary']}")
    tr = out.get("trace") or {}
    if tr and "error" not in tr:
        v, d, f = tr["version"], tr["display"], tr["feasibility"]
        print(f"[追跡] 版: Package と一致 {v['n_match']} / 不一致 {v['n_mismatch']} / 判定不能 {v['n_unknown']} (退避 {v['fallback_reasons']}) / "
              f"表示: {d['n_displayed']}/{d['n_advice']} 遅延の中央値 {d['latency_p50']} 秒 (p90 {d['latency_p90']}) 遅い {d['n_late']} 古い状態 {d['n_stale']} / "
              f"実行不能 (システムの状態) {f['n_infeasible_system']}/{f['n']} (判定不能 {f['n_unknown_system']})")


if __name__ == "__main__":
    main()
