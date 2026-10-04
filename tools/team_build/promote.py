"""昇格・ロールバック (人手コマンド)。学習成果物と Package は registry の status machine で管理する。

    python -m tools.team_build.promote --list [--kind package]
    python -m tools.team_build.promote --gate <artifact_id>            # 昇格条件の確認 (PASS / 不足)
    python -m tools.team_build.promote --to validation|canary|production <artifact_id>
    python -m tools.team_build.promote --install <package_id>          # production Package の構築を my_team.json に登録
    python -m tools.team_build.promote --rollback --kind package
    python -m tools.team_build.promote --experiment <package_id> | --experiment-off   # 接続テストで候補を試用 (experiment ラベル)

昇格条件 (docs/TEAM_BUILDING_IMPLEMENTATION.md §9-7、2026-10-05 Phase 5): 同じ 6 体で holdout PASS の **full run** が
BUILD_PROMOTE_MIN_FULL_RUNS 回 (= full run 2 本 + 確認 1 回) + 全 gate PASS + 重大 regression 0 + 人手 approve。
full run の定義: 封印 holdout の対戦数 ≥ BUILD_PROMOTE_MIN_HOLDOUT_N、別の封印の分割 (sealed_id) ごとに 1 回だけ数える。
登録チームと同じ 6 体の Package は数えない (1002d の PASS は参照との差が選出モデルのばらつきだった)。
自動昇格はしない。緊急ロールバックの invariant 監視は invariants.py (後続)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_PROMOTE_DISTINCT_SPLITS, BUILD_PROMOTE_MIN_FULL_RUNS, BUILD_PROMOTE_MIN_HOLDOUT_N
from tools.team_build.registry import Registry

REPO = Path(__file__).resolve().parent.parent.parent
EXPERIMENT_MARK = REPO / "logs" / ".experiment_package"


def gate_check(reg: Registry, artifact_id: str) -> dict:
    row = reg.get(artifact_id)
    if row is None:
        return {"ok": False, "problems": [f"{artifact_id} が registry に無い"]}
    problems = []
    meta = row.get("meta") or {}
    hold = meta.get("holdout") or {}
    if row["kind"] == "package":
        if hold.get("verdict") not in ("PASS", "PASS_EQUIVALENT"):
            problems.append(f"holdout が PASS ではない: {hold.get('verdict')}")
        final = reg.resolve(artifact_id)
        rob = json.loads((final / "robustness.json").read_text(encoding="utf-8")) if (final / "robustness.json").exists() else None
        if rob is None:
            problems.append("robustness.json (STRESS) が無い")
        elif any((r.get("paired") or {}).get("state") == "degraded" for r in rob.get("rows", [])):
            problems.append("STRESS で degraded の variant がある")
        if not (final / "evaluation.json").exists():
            problems.append("evaluation.json が無い")
        ev = json.loads((final / "evaluation.json").read_text(encoding="utf-8")) if (final / "evaluation.json").exists() else {}
        if "ablation" not in ev:
            problems.append("ablation 表が無い")
        reg_key = registered_team_key()
        if reg_key and team_key(meta) == reg_key:
            problems.append("登録チームと同じ 6 体 (昇格しても変わらない。参照との差は選出モデルのばらつきで、PASS は数えない)")
        runs = pass_runs_for_team(reg.list(kind="package"), meta, exclude_key=reg_key)
        if len(runs) < BUILD_PROMOTE_MIN_FULL_RUNS:
            problems.append(f"full run の PASS が {len(runs)} 回 (必要 {BUILD_PROMOTE_MIN_FULL_RUNS}。同じ 6 体、holdout n ≥ "
                            f"{BUILD_PROMOTE_MIN_HOLDOUT_N}、別の封印の分割ごとに 1 回)")
    return {"ok": not problems, "problems": problems, "status": row["status"], "kind": row["kind"],
            "full_run": {"min_runs": BUILD_PROMOTE_MIN_FULL_RUNS, "min_holdout_n": BUILD_PROMOTE_MIN_HOLDOUT_N,
                         "distinct_splits": BUILD_PROMOTE_DISTINCT_SPLITS}}


def team_key(meta: dict) -> tuple:
    """Package の同一性の鍵 = 6 体の種の集合 (candidate_id は run ごとに振り直されるので同じ並びの保証が無い。2026-10-05 判断 #24)"""
    return tuple(sorted(str(s) for s in ((meta or {}).get("species") or [])))


def pass_runs_for_team(packages: list, meta: dict, min_n: int = BUILD_PROMOTE_MIN_HOLDOUT_N,
                       distinct_splits: bool = BUILD_PROMOTE_DISTINCT_SPLITS, exclude_key: Optional[tuple] = None) -> set:
    """同じ 6 体の Package のうち holdout PASS の **full run** の run_id の集合 (純粋)。
    full run = holdout の対戦数 n ≥ min_n (n が無い古い記録は数えない。min_n=0 なら数える)。distinct_splits なら同じ封印の分割
    (sealed_id) の PASS は 1 回に数える (最初の run)。exclude_key (登録チームの 6 体) と同じ鍵の Package は空 (昇格しても変わらない)。
    species が無い古い Package は candidate_id で数える"""
    key = team_key(meta)
    if exclude_key and key and key == tuple(exclude_key):
        return set()
    by_split: dict = {}
    for r in packages or []:
        m = r.get("meta") or {}
        hold = m.get("holdout") or {}
        if hold.get("verdict") not in ("PASS", "PASS_EQUIVALENT"):
            continue
        if key:
            same = bool(m.get("species")) and team_key(m) == key       # 6 体が確認できない Package は数えない
        else:
            same = m.get("candidate_id") == meta.get("candidate_id")
        if not same:
            continue
        n = hold.get("n")
        if min_n and (n is None or int(n) < int(min_n)):
            continue
        k = (hold.get("sealed_id") or r.get("run_id")) if distinct_splits else r.get("run_id")
        by_split.setdefault(k, r.get("run_id"))
    return set(by_split.values())


def registered_team_key() -> tuple:
    """登録チーム (config/my_team.json) の 6 体の鍵。読めなければ空"""
    try:
        from tools.team_build.run import registered_team
        _text, ids, _mega = registered_team()
        return tuple(sorted(str(s) for s in ids)) if ids else ()
    except Exception:
        return ()


def banned_in_team_text(text: str, banned) -> list:
    """Package の構築本文 (Showdown 形式) に含まれる使わないポケモンの id。純粋 (解析は sets.parse_set_text)"""
    from tools.team_build.sets import parse_set_text
    b = set(banned or ())
    return [sid for sid in parse_set_text(text or "") if sid in b]


def install_package(reg: Registry, package_id: str, allow_banned: bool = False) -> None:
    """Package の構築を config/my_team.json に登録する (手入力ベースの登録経路を使う)。
    使わないポケモン (config/banned_species.txt) を含む Package は止める (--allow-banned で承知の上なら通す)"""
    final = reg.resolve(package_id)
    team = json.loads((final / "team.json").read_text(encoding="utf-8"))
    from tools.team_build.spec import read_banned_file
    found = banned_in_team_text(team.get("text") or "", read_banned_file()["ids"])
    if found and not allow_banned:
        raise SystemExit(f"install 中止: 使わないポケモン {found} が含まれる (config/banned_species.txt)。承知の上なら --allow-banned")
    tmp = final / "team.txt"
    tmp.write_text(team["text"], encoding="utf-8")
    import subprocess
    import sys
    subprocess.run([sys.executable, "-m", "tools.register_my_team", "--team-file", str(tmp)], cwd=str(REPO), check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="昇格・ロールバック (人手)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--kind", default=None)
    ap.add_argument("--gate", default=None)
    ap.add_argument("--to", default=None, choices=["validation", "canary", "production", "retired"])
    ap.add_argument("--install", default=None)
    ap.add_argument("--allow-banned", action="store_true", help="使わないポケモンを含む Package でも --install する (承知の上で)")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--experiment", default=None)
    ap.add_argument("--experiment-off", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("artifact_id", nargs="?")
    args = ap.parse_args()
    reg = Registry()
    if args.list:
        for r in reg.list(kind=args.kind):
            print(f"{r['id']:40s} {r['status']:10s} run={r.get('run_id')} {json.dumps(r.get('meta'), ensure_ascii=False)[:80]}")
        return
    if args.gate:
        print(json.dumps(gate_check(reg, args.gate), ensure_ascii=False, indent=1))
        return
    if args.to and args.artifact_id:
        if args.to == "production":
            g = gate_check(reg, args.artifact_id)
            if not g["ok"]:
                print("昇格条件を満たしていません:", g["problems"])
                print("(条件を承知で昇格する場合は --note に理由を書いて再実行)")
                if not args.note:
                    return
        row = reg.set_status(args.artifact_id, args.to, note=args.note)
        print(f"{row['id']} → {row['status']}")
        return
    if args.install:
        install_package(reg, args.install, allow_banned=args.allow_banned)
        return
    if args.rollback:
        row = reg.rollback(args.kind or "package", note=args.note or "rollback")
        print(f"rollback → {row['id']} ({row['status']})")
        return
    if args.experiment:
        if reg.get(args.experiment) is None:
            print(f"registry に無い id: {args.experiment} (--list --kind package で確認)")
            return
        EXPERIMENT_MARK.write_text(args.experiment, encoding="utf-8")
        print(f"experiment ラベル ON: {args.experiment}")
        return
    if args.experiment_off and EXPERIMENT_MARK.exists():
        EXPERIMENT_MARK.unlink()
        print("experiment ラベル OFF")


if __name__ == "__main__":
    main()
