"""登録チーム向けの選出モデルの登録 (2026-10-06 判断: 新しくシムを回さず、改善 run の参照の適応を次の接続テストから使う)。

構築の run (終了処理の改善 run を含む) は、参照 = 登録の 6 体にも S7 と同じ適応を作る (fold A で収束まで → 独立 fold V の実測で
checkpoint を選ぶ。evaluation/s07_adapt.json の "reference")。それを 6 体の鍵 (selection_dispatch.team_key) で
logs/registry/registered/<key>/selection_model.pt に置くと、助言サーバーは 試用 Package → 登録チーム向け → 配布版 の順に引く
(selection_dispatch.advisor_model_path)。鍵は 6 体の種 id の集合なので、パーティを替えれば自動で外れる。

    python -m tools.team_build.register_selection --run-id R [--arm reference]   # run の参照の適応 (検証で選んだ checkpoint) を登録
    python -m tools.team_build.register_selection --package <package_id>         # Package 同梱のモデルをその 6 体の鍵で登録
    python -m tools.team_build.register_selection --status                        # 登録チーム (config/my_team.json) のモデルの有無
    python -m tools.team_build.register_selection --remove <key>

登録するのは S7 と同じ手順 (収束まで適応 → 独立 fold の実測で checkpoint を選ぶ) を通ったモデルだけ。検証の無い適応は --force が要る。
軽い適応 (S8a cheap、1,000 戦) は登録しない (参照で 0.313 と汎用 0.613 より弱い: experiments/cheap_drift)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Optional

from champions_agent.agent.selection_dispatch import REGISTERED_DIR, features_version, team_key

REPO = Path(__file__).resolve().parent.parent.parent
RUNS = REPO / "logs" / "build_search" / "runs"
PACKAGES = REPO / "logs" / "registry" / "package"


def _load(p: Path) -> Optional[dict]:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sha256_file(p: Path, n: int = 16) -> Optional[str]:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:n]
    except OSError:
        return None


# ------------------------------------------------------------------ 純粋
def make_manifest(species: list, model_src: str, source: str, *, run_id: Optional[str] = None, arm: Optional[str] = None,
                  n_battles: Optional[int] = None, validated: Optional[dict] = None, features: Optional[str] = None,
                  sha256: Optional[str] = None, git_commit: Optional[str] = None, created_at: Optional[str] = None) -> dict:
    """登録の記録 (純粋): 鍵、6 体、元のモデル、作った run と適応の深さ、検証の結果、特徴量の版"""
    ids = sorted({str(s).strip().lower() for s in (species or []) if s})
    return {"key": team_key(ids), "species": ids, "model_src": str(model_src), "source": source, "run_id": run_id, "arm": arm,
            "n_battles": n_battles,
            "validated": ({"chosen_n": validated.get("chosen_n"), "fold": validated.get("fold"), "n": validated.get("n"),
                           "results": validated.get("results")} if validated else None),
            "features": features or features_version(), "sha256": sha256, "git_commit": git_commit,
            "created_at": created_at or time.strftime("%Y-%m-%d %H:%M:%S")}


def adaptation_entry(s07: Optional[dict], adapt_result: Optional[dict]) -> Optional[dict]:
    """run の参照の適応の記録: s07_adapt.json の項 (検証つき) を優先し、無ければ advisors/<arm>/adapt_result.json (純粋)"""
    for doc in (s07, adapt_result):
        if doc and doc.get("model"):
            return doc
    return None


def features_of(entry: dict) -> Optional[str]:
    """適応の記録から特徴量の版 (学習の報告 history[*].features)。無ければ None"""
    for rep in reversed(list(entry.get("history") or [])):
        if rep.get("features"):
            return str(rep["features"])
    return None


# ------------------------------------------------------------------ 配線
def species_of_run_reference(run_dir: Path) -> list:
    """run の参照チーム (reference_team.txt) の 6 体"""
    from tools.team_build.opponents import parse_team_text
    text = (Path(run_dir) / "reference_team.txt").read_text(encoding="utf-8")
    ids, _mega = parse_team_text(text)
    return sorted(ids)


def register_model(model_src: Path, species: list, registered_dir: Optional[Path] = None, *, source: str, run_id: Optional[str] = None,
                   arm: Optional[str] = None, n_battles: Optional[int] = None, validated: Optional[dict] = None,
                   features: Optional[str] = None) -> dict:
    """モデルを写して manifest を書く。戻り値 manifest (置き場は registered_dir/<key>/)"""
    rdir = Path(registered_dir) if registered_dir is not None else REGISTERED_DIR
    git = None
    try:
        from tools.team_build.manifest import git_commit
        git = git_commit(REPO)
    except Exception:
        pass
    man = make_manifest(species, str(model_src), source, run_id=run_id, arm=arm, n_battles=n_battles, validated=validated,
                        features=features, sha256=sha256_file(model_src), git_commit=git)
    out = rdir / man["key"]
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_src, out / "selection_model.pt")
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return man


def register_from_run(run_id: str, arm: str = "reference", runs: Optional[Path] = None, registered_dir: Optional[Path] = None,
                      force: bool = False) -> dict:
    """run の参照の適応 (検証で選んだ checkpoint) を登録する。検証が無ければ force が要る"""
    run_dir = (Path(runs) if runs is not None else RUNS) / run_id
    s07 = (_load(run_dir / "evaluation" / "s07_adapt.json") or {}).get(arm)
    entry = adaptation_entry(s07, _load(run_dir / "advisors" / arm / "adapt_result.json"))
    if not entry:
        raise SystemExit(f"{run_id}: {arm} の適応の記録が無い (evaluation/s07_adapt.json / advisors/{arm}/adapt_result.json)")
    validated = entry.get("validated") or {}
    model = Path(validated.get("chosen") or entry["model"])
    if not model.exists():
        raise SystemExit(f"{run_id}: モデルが無い: {model}")
    if not validated.get("chosen") and not force:
        raise SystemExit(f"{run_id}: {arm} の適応は独立 fold の検証を通っていない (--force で登録できる)")
    species = species_of_run_reference(run_dir)
    if len(species) != 6:
        raise SystemExit(f"{run_id}: 参照チームが 6 体でない: {species}")
    return register_model(model, species, registered_dir, source=f"run:{run_id}:{arm}", run_id=run_id, arm=arm,
                          n_battles=entry.get("n_battles"), validated=validated or None, features=features_of(entry))


def register_from_package(package_id: str, packages: Optional[Path] = None, registered_dir: Optional[Path] = None) -> dict:
    """Package 同梱の選出モデル (S7 の適応、検証つき) をその 6 体の鍵で登録する"""
    pdir = (Path(packages) if packages is not None else PACKAGES) / package_id
    model = pdir / "advisor_policy" / "selection_model.pt"
    team = _load(pdir / "team.json") or {}
    if not model.exists() or len(team.get("species") or []) != 6:
        raise SystemExit(f"{package_id}: 同梱のモデルか team.json の 6 体が無い")
    man = _load(pdir / "manifest.json") or {}
    return register_model(model, list(team["species"]), registered_dir, source=f"package:{package_id}", run_id=man.get("run_id"),
                          arm=team.get("candidate_id"), features=(man.get("selection_features") or None))


def status(my_species: Optional[list] = None, registered_dir: Optional[Path] = None) -> dict:
    """登録チームに対応するモデルの有無と、置いてある全部の鍵"""
    from champions_agent.agent.selection_dispatch import registered_team_model
    rdir = Path(registered_dir) if registered_dir is not None else REGISTERED_DIR
    if my_species is None:
        try:
            from tools.team_build.run import registered_team
            _text, my_species, _mega = registered_team()
        except Exception:
            my_species = []
    entries = []
    if rdir.exists():
        for d in sorted(p for p in rdir.iterdir() if p.is_dir()):
            man = _load(d / "manifest.json") or {}
            entries.append({"key": d.name, "species": man.get("species"), "source": man.get("source"), "n_battles": man.get("n_battles"),
                            "validated": bool((man.get("validated") or {}).get("chosen_n")), "features": man.get("features"),
                            "created_at": man.get("created_at"), "has_model": (d / "selection_model.pt").exists()})
    got = registered_team_model(list(my_species or []), rdir)
    return {"my_species": sorted(my_species or []), "key": team_key(my_species) if my_species else None,
            "registered": (got["key"] if got else None), "manifest": (got["manifest"] if got else None), "entries": entries}


def main() -> None:
    ap = argparse.ArgumentParser(description="登録チーム向けの選出モデルの登録")
    ap.add_argument("--run-id", default=None, help="この run の参照の適応を登録する")
    ap.add_argument("--arm", default="reference", help="run の中の腕 (既定 reference)")
    ap.add_argument("--package", default=None, help="この Package 同梱のモデルを登録する")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--remove", default=None, help="この鍵の登録を消す")
    ap.add_argument("--force", action="store_true", help="検証 (独立 fold の実測) を通っていない適応も登録する")
    args = ap.parse_args()
    if args.run_id:
        man = register_from_run(args.run_id, args.arm, force=args.force)
        print(f"登録: {man['key']} ← {man['source']} (n={man['n_battles']}, 検証 {(man.get('validated') or {}).get('chosen_n')}) "
              f"{REGISTERED_DIR / man['key']}")
    elif args.package:
        man = register_from_package(args.package)
        print(f"登録: {man['key']} ← {man['source']} {REGISTERED_DIR / man['key']}")
    elif args.remove:
        d = REGISTERED_DIR / args.remove
        if d.exists():
            shutil.rmtree(d)
            print(f"消した: {d}")
        else:
            print(f"無い: {d}")
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
