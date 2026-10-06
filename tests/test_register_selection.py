"""登録チーム向けの選出モデルの登録 (tools/team_build/register_selection) と、助言側の経路 (selection_dispatch.registered_team_model)
のテスト。一時ディレクトリに偽の run / Package / registry を作って閉じる。

    python -m tests.test_register_selection
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from champions_agent.agent import selection_dispatch as SD
from tools.team_build import register_selection as RS

TEAM = ["dragonite", "tinkaton", "primarina", "garchomp", "corviknight", "gengar"]
TEXT = "\n\n".join(f"{sp.capitalize()} @ Leftovers\nAbility: x\n- Tackle" for sp in TEAM) + "\n"


def _fake_run(root: Path, run_id: str, validated: bool = True, features: str = "v1") -> Path:
    run_dir = root / "runs" / run_id
    (run_dir / "advisors" / "reference").mkdir(parents=True)
    (run_dir / "evaluation").mkdir()
    (run_dir / "reference_team.txt").write_text(TEXT, encoding="utf-8")
    model = run_dir / "advisors" / "reference" / "selection_model.pt"
    model.write_bytes(b"model-bytes")
    ckpt = run_dir / "advisors" / "reference" / "selection_model_n6000.pt"
    ckpt.write_bytes(b"ckpt-bytes")
    entry = {"candidate_id": "reference", "model": str(model), "n_battles": 8000,
             "history": [{"n_battles": 6000, "val_mse": 0.21, "features": features}, {"n_battles": 8000, "val_mse": 0.2, "features": features}]}
    if validated:
        entry["validated"] = {"chosen": str(ckpt), "chosen_n": 6000, "fold": 2, "n": 200,
                              "results": {"6000": {"win_rate": 0.7, "n": 200}, "8000": {"win_rate": 0.66, "n": 200}}}
    (run_dir / "evaluation" / "s07_adapt.json").write_text(json.dumps({"reference": entry}), encoding="utf-8")
    return run_dir


def test_pure_helpers():
    man = RS.make_manifest(list(reversed(TEAM)), "/m/x.pt", "run:r1:reference", run_id="r1", arm="reference", n_battles=8000,
                           validated={"chosen_n": 6000, "fold": 2, "n": 200, "results": {}}, features="v1", sha256="abc", git_commit="g",
                           created_at="2026-10-06 10:00:00")
    assert man["key"] == SD.team_key(TEAM) and man["species"] == sorted(TEAM) and man["validated"]["chosen_n"] == 6000
    assert man["features"] == "v1" and man["created_at"] == "2026-10-06 10:00:00"
    # 鍵は順序と大文字小文字に依らず、1 体違えば変わる
    assert SD.team_key([s.upper() for s in TEAM]) == SD.team_key(TEAM) and SD.team_key(TEAM[:5] + ["mimikyu"]) != SD.team_key(TEAM)
    assert RS.adaptation_entry(None, {"model": "/a"}) == {"model": "/a"} and RS.adaptation_entry({"model": "/b"}, {"model": "/a"})["model"] == "/b"
    assert RS.adaptation_entry({"model": None}, None) is None
    assert RS.features_of({"history": [{"features": "v1"}, {"x": 1}]}) == "v1" and RS.features_of({}) is None
    print("test_pure_helpers OK")


def test_register_from_run_and_lookup():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        reg = root / "registered"
        run_dir = _fake_run(root, "improve_x")
        man = RS.register_from_run("improve_x", "reference", runs=root / "runs", registered_dir=reg)
        key = SD.team_key(TEAM)
        assert man["key"] == key and (reg / key / "selection_model.pt").read_bytes() == b"ckpt-bytes", "検証で選んだ checkpoint を写す"
        assert man["source"] == "run:improve_x:reference" and man["n_battles"] == 8000 and man["validated"]["chosen_n"] == 6000
        assert man["features"] == "v1" and man["sha256"]
        saved = json.loads((reg / key / "manifest.json").read_text(encoding="utf-8"))
        assert saved["species"] == sorted(TEAM)
        # 助言側: 6 体の鍵が一致するときだけ引く。特徴量の版が違えば引かない
        got = SD.registered_team_model(TEAM, reg)
        assert got and got["key"] == key and got["path"].name == "selection_model.pt" and got["manifest"]["run_id"] == "improve_x"
        assert SD.registered_team_model(TEAM[:5] + ["mimikyu"], reg) is None and SD.registered_team_model(TEAM[:3], reg) is None
        assert SD.registered_team_model(TEAM, reg, version="v3") is None
        # 経路の順: 試用 Package (6 体を含む) → 登録チーム向け → 配布版
        orig = (SD.EXPERIMENT_MARK, SD.PACKAGES_DIR)
        mark = root / ".experiment_package"
        pkgs = root / "package"
        (pkgs / "pkg-a" / "advisor_policy").mkdir(parents=True)
        (pkgs / "pkg-a" / "advisor_policy" / "selection_model.pt").write_bytes(b"pkg")
        (pkgs / "pkg-a" / "team.json").write_text(json.dumps({"species": TEAM}), encoding="utf-8")
        SD.EXPERIMENT_MARK, SD.PACKAGES_DIR = mark, pkgs
        try:
            path, src = SD.advisor_model_path(TEAM, registered_dir=reg)
            assert src == "registered:" + key and path == reg / key / "selection_model.pt"
            mark.write_text("pkg-a", encoding="utf-8")
            path, src = SD.advisor_model_path(TEAM, registered_dir=reg)
            assert src == "pkg-a" and path.name == "selection_model.pt" and "pkg-a" in str(path)
            path, src = SD.advisor_model_path(TEAM[:5] + ["mimikyu"], registered_dir=reg)
            assert src is None and path == SD.deployed_model_path()
        finally:
            SD.EXPERIMENT_MARK, SD.PACKAGES_DIR = orig
        assert SD.model_label("registered:" + key) == "registered:" + key and SD.model_label("pkg-a") == "experiment:pkg-a"
        assert SD.model_label(None) == "deployed"
        # 状態: 登録チームに対応する鍵と、置いてある全部
        st = RS.status(TEAM, reg)
        assert st["registered"] == key and st["entries"][0]["validated"] is True and st["entries"][0]["has_model"]
        assert RS.status(TEAM[:5] + ["mimikyu"], reg)["registered"] is None
        # 検証の無い適応は force が要る
        _fake_run(root, "improve_nv", validated=False)
        try:
            RS.register_from_run("improve_nv", "reference", runs=root / "runs", registered_dir=reg)
            raise AssertionError("検証なしは拒む")
        except SystemExit as e:
            assert "検証" in str(e)
        man2 = RS.register_from_run("improve_nv", "reference", runs=root / "runs", registered_dir=reg, force=True)
        assert man2["validated"] is None and (reg / key / "selection_model.pt").read_bytes() == b"model-bytes"
        # Package から
        man3 = RS.register_from_package("pkg-a", packages=pkgs, registered_dir=reg)
        assert man3["source"] == "package:pkg-a" and (reg / key / "selection_model.pt").read_bytes() == b"pkg"
    print("test_register_from_run_and_lookup OK")


def main() -> None:
    test_pure_helpers()
    test_register_from_run_and_lookup()
    print("ALL OK")


if __name__ == "__main__":
    main()
