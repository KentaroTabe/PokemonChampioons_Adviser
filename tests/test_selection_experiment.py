"""試用中 Package (experiment ラベル) の同梱選出モデルを助言サーバーの選出の推しに使う経路のテスト (2026-09-25)。

    python -m tests.test_selection_experiment
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from champions_agent.agent import selection_dispatch as SD


def _make_registry(root: Path, package_id: str, species: list, with_model: bool = True) -> Path:
    pdir = root / "package" / package_id
    (pdir / "advisor_policy").mkdir(parents=True)
    if with_model:
        (pdir / "advisor_policy" / "selection_model.pt").write_bytes(b"dummy")
    (pdir / "team.json").write_text(json.dumps({"candidate_id": "L06", "species": species, "text": "x"}), encoding="utf-8")
    return root / "package"


def test_experiment_package_model_resolution():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mark = root / ".experiment_package"
        packages = _make_registry(root, "package-aaaa", ["charizard", "hippowdon", "hydreigon", "metagross", "primarina", "sneasler"])
        assert SD.experiment_package_model(mark, packages) is None                       # ラベル無し
        mark.write_text("package-zzzz\n", encoding="utf-8")
        assert SD.experiment_package_model(mark, packages) is None                       # registry に無い
        mark.write_text("../evil\n", encoding="utf-8")
        assert SD.experiment_package_model(mark, packages) is None                       # パスの細工は無視
        mark.write_text("package-aaaa\n", encoding="utf-8")
        got = SD.experiment_package_model(mark, packages)
        assert got["package_id"] == "package-aaaa" and got["path"].name == "selection_model.pt" and "hydreigon" in got["species"]
        # 同梱モデルが無い Package はラベルがあっても None
        _make_registry(root, "package-nomodel", ["a", "b"], with_model=False)
        mark.write_text("package-nomodel", encoding="utf-8")
        assert SD.experiment_package_model(mark, packages) is None
    print("test_experiment_package_model_resolution OK")


def test_advisor_model_path_uses_package_only_for_its_team():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        packages = _make_registry(root, "package-aaaa", ["charizard", "hippowdon", "hydreigon", "metagross", "primarina", "sneasler"])
        mark = root / ".experiment_package"
        orig = (SD.EXPERIMENT_MARK, SD.PACKAGES_DIR)
        SD.EXPERIMENT_MARK, SD.PACKAGES_DIR = mark, packages
        try:
            path, pid = SD.advisor_model_path(["charizard", "hydreigon", "metagross"])
            assert pid is None and path == SD.deployed_model_path()                       # ラベル無し → 配布版
            mark.write_text("package-aaaa", encoding="utf-8")
            path, pid = SD.advisor_model_path(["charizard", "hydreigon", "metagross", "primarina", "sneasler", "hippowdon"])
            assert pid == "package-aaaa" and path.name == "selection_model.pt"
            path, pid = SD.advisor_model_path(["charizard", "hydreigon", "garchomp"])    # Package に無い種が居る → 配布版
            assert pid is None and path == SD.deployed_model_path()
            path, pid = SD.advisor_model_path()                                          # パーティ指定なし → Package のモデル
            assert pid == "package-aaaa"
        finally:
            SD.EXPERIMENT_MARK, SD.PACKAGES_DIR = orig
    print("test_advisor_model_path_uses_package_only_for_its_team OK")


def test_attach_model_pick_passes_package_model():
    """advisor.selection.attach_model_pick は Package のモデルのパスで predict_best を呼び、model / trained を添える"""
    from advisor import selection as ASEL
    from champions_agent.agent import selection_model as SM
    seen = {}

    def fake_predict_best(my, opp, path=None):
        seen["path"] = path
        return ((0, 1, 2), 0.75)

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        team = ["charizard", "hippowdon", "hydreigon", "metagross", "primarina", "sneasler"]
        packages = _make_registry(root, "package-aaaa", team)
        mark = root / ".experiment_package"
        mark.write_text("package-aaaa", encoding="utf-8")
        orig = (SD.EXPERIMENT_MARK, SD.PACKAGES_DIR, SM.predict_best, SM.is_in_distribution)
        SD.EXPERIMENT_MARK, SD.PACKAGES_DIR = mark, packages
        SM.predict_best, SM.is_in_distribution = fake_predict_best, lambda my: False
        try:
            my_party = [{"species_id": s, "species_ja": s} for s in team]
            opp_party = [{"species_id": "garchomp"}, {"species_id": "gengar"}]
            advice = {}
            ASEL.attach_model_pick(advice, my_party, opp_party)
            mp = advice["model_pick"]
            assert Path(seen["path"]).name == "selection_model.pt" and "package-aaaa" in str(seen["path"])
            assert mp["model"] == "experiment:package-aaaa" and mp["trained"] is True and mp["names"] == team[:3]
            # ラベルを外すと配布版 (trained は学習分布の判定に戻る)
            mark.unlink()
            advice2 = {}
            ASEL.attach_model_pick(advice2, my_party, opp_party)
            assert advice2["model_pick"]["model"] == "deployed" and advice2["model_pick"]["trained"] is False
            assert Path(seen["path"]) == SD.deployed_model_path()
        finally:
            SD.EXPERIMENT_MARK, SD.PACKAGES_DIR = orig[0], orig[1]
            SM.predict_best, SM.is_in_distribution = orig[2], orig[3]
    print("test_attach_model_pick_passes_package_model OK")


if __name__ == "__main__":
    test_experiment_package_model_resolution()
    test_advisor_model_path_uses_package_only_for_its_team()
    test_attach_model_pick_passes_package_model()
