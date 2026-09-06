"""Final Build Package (S13): チーム + 適応済み選出モデル + 選出パターン + 対面行列 + 評価 + 頑健性 + 系譜 + 記事 + manifest。

final/ を run ディレクトリに作り、registry に package (candidate) として登録する。
登録 (config/my_team.json) と production への昇格は promote.py の人手コマンドで行う。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

from tools.team_build.manifest import build_manifest


def _load(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def selection_patterns_from_records(records: list, top_k: int = 5) -> dict:
    """対戦記録から機械生成する選出パターン: 相手の先発/系統ごとに勝率の高かった自分の選出"""
    from collections import defaultdict
    by_lead: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in records:
        lead = (r.get("lead") or {}).get("theirs") or "?"
        lead = lead.split(":", 1)[-1].strip().lower()
        sel = "/".join(sorted(r.get("our_selection") or []))
        c = by_lead[lead][sel]
        c[0 if r.get("won") else 1] += 1
    out = {}
    for lead, sels in by_lead.items():
        rows = [{"selection": s, "n": w + l, "win_rate": round(w / max(1, w + l), 3)} for s, (w, l) in sels.items()]
        rows.sort(key=lambda x: (-x["n"], -x["win_rate"]))
        out[lead] = rows[:top_k]
    return out


def build_package(run_dir: Path, candidate_id: str, team_file: Path, selection_model: Optional[Path],
                  species: list, registry=None, report_md: Optional[str] = None, extra_manifest: Optional[dict] = None) -> dict:
    run_dir = Path(run_dir)
    final = run_dir / "final"
    final.mkdir(parents=True, exist_ok=True)
    text = Path(team_file).read_text(encoding="utf-8")
    team = {"candidate_id": candidate_id, "text": text, "species": list(species),
            "sets_file": str(team_file), "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    (final / "team.json").write_text(json.dumps(team, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    adv_dir = final / "advisor_policy"
    adv_dir.mkdir(exist_ok=True)
    if selection_model and Path(selection_model).exists():
        shutil.copy2(selection_model, adv_dir / "selection_model.pt")
    records = []
    for p in sorted((run_dir / "evaluation").rglob("*.jsonl")):
        if candidate_id in p.name:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
    (final / "selection_patterns.json").write_text(
        json.dumps(selection_patterns_from_records(records), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    matrix = _load(run_dir / "interaction_matrix.json", None)
    if matrix is None:
        matrix = final_team_matrix(run_dir, candidate_id)
    (final / "matchup_matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    evaluation = {name: _load(run_dir / "evaluation" / f"{name}.json") for name in
                  ("s08a_screen", "s08b_adapted", "s10", "s12_holdout", "ablation")
                  if (run_dir / "evaluation" / f"{name}.json").exists()}
    (final / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for src, dst in (("evaluation/robustness.json", "robustness.json"), ("lineage.json", "lineage.json")):
        if (run_dir / src).exists():
            shutil.copy2(run_dir / src, final / dst)
    (final / "build_report.md").write_text(report_md or f"# Build report {run_dir.name}\n\n(記事は S13 の Sonnet 呼び出しで生成)\n",
                                           encoding="utf-8")
    manifest = build_manifest(run_dir.name, dict(_load(run_dir / "manifest.json", {}), **(extra_manifest or {})))
    manifest["candidate_id"] = candidate_id
    manifest["selection_model_sha256"] = _sha(adv_dir / "selection_model.pt") if (adv_dir / "selection_model.pt").exists() else None
    (final / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    result = {"final_dir": str(final), "candidate_id": candidate_id, "artifact_id": None}
    if registry is not None:
        row = registry.register("package", final, meta={"candidate_id": candidate_id, "species": list(species),
                                                          "holdout": evaluation.get("s12_holdout")},
                                run_id=run_dir.name, status="candidate")
        result["artifact_id"] = row["id"]
    return result


def final_team_matrix(run_dir: Path, candidate_id: str) -> dict:
    """決定チームの型 × 脅威の Interaction Matrix (Package 用に機械生成)"""
    try:
        from tools.team_build.interaction import matrix, view_from_set
        from tools.team_build.meta_snapshot import load_snapshot, threat_sets
        sets = _load(run_dir / "s06_sets.json", [])
        team = next((r for r in sets if r.get("candidate_id") == candidate_id), None)
        if not team:
            return {}
        doc = load_snapshot(run_dir / "meta_snapshot.json")
        tv = threat_sets(doc, 30)
        mine = {}
        for st in team.get("sets") or []:
            try:
                mine[st["species"]] = view_from_set(st["species"], st)
            except Exception:
                continue
        return matrix(mine, tv)
    except Exception as e:
        return {"error": repr(e)}


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
