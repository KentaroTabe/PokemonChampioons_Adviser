"""実験 3: 相手プールの妥当性と、系統ごとの実戦 vs シム。

- 実戦ログ (logs/battles/battle_*.jsonl) から相手のパーティ (選出画面で読めた種) と勝敗を取り、相手プール (run の
  opponent_families.json) の構築に当てる (重なり = Jaccard、6 体読めていなければ 含有率)。閾値以上なら「プールに入る」。
- 自分の並びが run の参照 (reference_team.txt) と同じ対戦だけで、系統ごとの実戦の勝率と、参照の腕のシムの勝率
  (evaluation/battles/*_reference.jsonl) の順位相関を出す。実戦の試合数が少ないので桁の確認が目的。

  python -m tools.team_build.experiments.env_validity --run-id ace_lopunny_1003 [--days 60] [--threshold 0.5]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from tools.team_build.experiments import REPO, RUNS, load_json, write_result
from tools.team_build.review_run import spearman

BATTLES = REPO / "logs" / "battles"


# ------------------------------------------------------------------ 純粋関数
def overlap(observed: set, team: set) -> float:
    """観測した相手の種 (部分でもよい) と構築の 6 体の重なり: 6 体読めていれば Jaccard、足りなければ含有率 |A∩B| / |A|"""
    if not observed or not team:
        return 0.0
    inter = len(observed & team)
    if len(observed) >= 6:
        return inter / len(observed | team)
    return inter / len(observed)


def match_family(observed: set, teams: dict, family_of: dict) -> dict:
    """{"team_id", "family_id", "overlap"} (最大の重なりの構築)。teams = {tid: [species]}"""
    best = {"team_id": None, "family_id": None, "overlap": 0.0}
    for tid, species in teams.items():
        o = overlap(observed, set(species))
        if o > best["overlap"]:
            best = {"team_id": tid, "family_id": family_of.get(tid), "overlap": round(o, 3)}
    return best


def coverage_summary(matches: list, threshold: float) -> dict:
    n = len(matches)
    covered = sum(1 for m in matches if m["overlap"] >= threshold)
    hist: dict = {}
    for m in matches:
        b = f"{int(m['overlap'] * 10) / 10:.1f}"
        hist[b] = hist.get(b, 0) + 1
    return {"n": n, "covered": covered, "covered_share": round(covered / n, 3) if n else None, "overlap_hist": dict(sorted(hist.items()))}


def family_win_rates(records: list, key: str = "family_id", min_n: int = 3) -> dict:
    wl: dict = {}
    for r in records:
        f = r.get(key)
        if not f or r.get("won") is None:
            continue
        c = wl.setdefault(f, [0, 0])
        c[0 if r["won"] else 1] += 1
    return {f: {"n": w + l, "win_rate": round(w / (w + l), 3)} for f, (w, l) in wl.items() if w + l >= min_n}


def same_team_battles(battles: list, ref_species) -> list:
    """参照と同じ 6 体で戦い、勝敗が残っている対戦 (純粋)。自分の種が 6 体そろって一致するものだけ数える
    (2026-10-05: 部分集合で数えていたので、自分の種が 1 体しか読めていない別の構築の対戦が「同じ並び」に入っていた。
    docs/incidents/reports/2026-10-04-same-team-battles-subset-count.md)"""
    want = set(ref_species or ())
    if len(want) != 6:
        return []
    return [b for b in battles or [] if b.get("won") is not None and set(b.get("our_species") or []) == want]


def compare_families(real: dict, sim: dict) -> dict:
    common = sorted(set(real) & set(sim))
    rows = [{"family_id": f, "real": real[f], "sim": sim[f]} for f in common]
    rho = spearman([real[f]["win_rate"] for f in common], [sim[f]["win_rate"] for f in common]) if len(common) >= 3 else None
    return {"n_common": len(common), "spearman": rho, "rows": rows}


# ------------------------------------------------------------------ 実戦ログ
def read_real_battle(path: Path) -> dict:
    """相手のパーティ (選出画面の 6 体を優先、無ければ対戦中に見えた種) と勝敗、自分の種"""
    from tools.team_build.real_eval import read_battle_labels
    lab = read_battle_labels(path)
    opp_sel: Optional[list] = None
    opp_seen: set = set()
    in_battle: list = []          # 対戦中 (選出画面以外) に場に出た相手の種 (順)
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("type") != "scene":
                continue
            st = d.get("state") or {}
            opp = st.get("opponent") or {}
            party = opp.get("party") or []
            ids = [p.get("species") for p in party if p.get("species") and not p.get("guess")]
            if d.get("scene") == "selection" and opp_sel is None and len(ids) >= 6:
                opp_sel = ids[:6]
            opp_seen |= set(ids)
            idx = opp.get("active")
            if d.get("scene") != "selection" and idx is not None and 0 <= idx < len(party):
                sid = party[idx].get("species")
                if sid and not party[idx].get("guess") and sid not in in_battle:
                    in_battle.append(sid)
    except Exception:
        pass
    return {"file": path.name, "won": {"win": True, "loss": False}.get(lab.get("outcome")), "our_species": lab.get("species") or [],
            "opp_species": sorted(set(opp_sel or opp_seen)), "opp_full": opp_sel is not None, "opp_seen": sorted(opp_seen),
            "opp_seen_in_battle": in_battle, "consistent": (opp_sel is not None and opp_seen <= set(opp_sel) and len(lab.get("species") or []) == 6),
            "ts": path.stat().st_mtime}


def sim_reference_records(run_dir: Path) -> list:
    out: list = []
    for p in sorted((run_dir / "evaluation" / "battles").glob("*_reference*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out.append({"family_id": r.get("opponent_family_id"), "won": bool(r.get("won"))})
    return out


def main() -> None:
    import time
    from tools.team_build.sets import parse_set_text
    ap = argparse.ArgumentParser(description="実験 3: 相手プールの妥当性と系統ごとの実戦 vs シム")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--days", type=float, default=None, help="実戦ログをこの日数以内に限る")
    ap.add_argument("--threshold", type=float, default=0.5, help="「プールに入る」重なりの下限")
    ap.add_argument("--battles-dir", default=str(BATTLES))
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    split = load_json(run_dir / "opponent_families.json") or {}
    teams = {tid: t.get("species") or [] for tid, t in (split.get("teams") or {}).items()}
    family_of = {tid: f["family_id"] for f in split.get("families", []) for tid in f.get("teams", [])}
    ref_species = set(parse_set_text((run_dir / "reference_team.txt").read_text(encoding="utf-8"))) if (run_dir / "reference_team.txt").exists() else set()
    since = time.time() - args.days * 86400 if args.days else None
    in_window = [b for b in (read_real_battle(p) for p in sorted(Path(args.battles_dir).glob("battle_*.jsonl")))
                 if since is None or b["ts"] >= since]
    battles = [b for b in in_window if len(b["opp_species"]) >= 3]       # 相手の種が 3 体以上読めた対戦 (プールとの照合の対象)
    matches = []
    for b in battles:
        m = match_family(set(b["opp_species"]), teams, family_of)
        b.update(m)
        matches.append(m)
    uncovered: dict = {}
    for b in battles:
        if b["overlap"] < args.threshold:
            for s in b["opp_species"]:
                uncovered[s] = uncovered.get(s, 0) + 1
    # 実戦の勝率は同じ 6 体の対戦の全部で数える。系統ごとの比較だけ、相手がプールに入る対戦に限る
    # (2026-10-05: 勝率にもプールの条件を掛けていて 1 戦分の値になっていた)
    same_all = same_team_battles(in_window, ref_species)
    same_team = [b for b in same_all if b.get("overlap", 0.0) >= args.threshold]
    real_fam = family_win_rates(same_team)
    sim_fam = family_win_rates(sim_reference_records(run_dir), min_n=5)
    cmp = compare_families(real_fam, sim_fam)
    wins = [b["won"] for b in same_all]
    result = {"run_id": args.run_id, "n_pool_teams": len(teams), "n_real_battles": len(battles),
              "n_full_preview": sum(1 for b in battles if b["opp_full"]),
              "coverage": coverage_summary(matches, args.threshold),
              "uncovered_species_top": sorted(uncovered.items(), key=lambda kv: -kv[1])[:20],
              "same_team_battles": len(same_all), "same_team_wins": sum(1 for w in wins if w), "same_team_in_pool": len(same_team),
              "real_win_rate": round(sum(wins) / len(wins), 3) if wins else None,
              "sim_reference_win_rate": (round(sum(1 for r in sim_reference_records(run_dir) if r["won"]) / max(1, len(sim_reference_records(run_dir))), 3)
                                         if sim_reference_records(run_dir) else None),
              "family_compare": cmp,
              "battles": [{k: b[k] for k in ("file", "won", "opp_species", "opp_full", "family_id", "overlap")} for b in battles]}
    p = write_result("env_validity", result)
    print(f"実戦 {len(battles)} 戦 (相手 6 体が読めた {result['n_full_preview']})、プールに入る割合 {result['coverage']['covered_share']} "
          f"(閾値 {args.threshold})、重なりの分布 {result['coverage']['overlap_hist']}")
    print(f"入らない相手に多い種: {result['uncovered_species_top'][:10]}")
    print(f"参照と同じ 6 体の実戦 {len(same_all)} 戦 {result['same_team_wins']} 勝: 勝率 {result['real_win_rate']} / "
          f"シムの参照 {result['sim_reference_win_rate']}、うち相手がプールに入る {len(same_team)} 戦で "
          f"系統ごとの順位相関 {cmp['spearman']} (共通 {cmp['n_common']} 系統)")
    print(f"保存: {p}")


if __name__ == "__main__":
    main()
