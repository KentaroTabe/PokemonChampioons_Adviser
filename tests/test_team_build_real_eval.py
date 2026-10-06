"""実戦評価・転移量制御・較正の純粋関数テスト。

    python -m tests.test_team_build_real_eval
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from tools.team_build import calibration as CAL
from tools.team_build import real_eval as RE
from tools.team_build import transfer as TR


def test_real_summary_and_compliance():
    with tempfile.TemporaryDirectory() as d:
        for i, (src, pid, out) in enumerate([("recommended", "pkg1", "win"), ("recommended", "pkg1", "loss"),
                                             ("organic", None, "win"), ("experiment", "pkg2", None)]):
            rows = [{"type": "session", "source": src, "package_id": pid, "dataset_kind": "real"},
                    {"type": "advice", "kind": "battle", "advice": {}}]
            if out:
                rows.append({"type": "outcome", "outcome": out})
            (Path(d) / f"battle_2026090{i}_000000.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        s = RE.real_summary(battles_dir=Path(d))
        assert s["n_logs"] == 4 and s["n_decided"] == 3 and s["wins"] == 2
        assert s["by_source"] == {"organic": 1, "recommended": 2, "experiment": 1}
        s2 = RE.real_summary(package_id="pkg1", battles_dir=Path(d))
        assert s2["n_decided"] == 2 and s2["win_rate"] == 0.5 and s2["weight"] < 0.01   # 2 戦では重みほぼ 0
    assert RE.compliance_from_audit([{"followed": True}, {"followed": False}, {"x": 1}]) == 0.5
    b = RE.blended({"win_rate": 0.5, "weight": 0.25}, 0.7)
    assert abs(b["score"] - 0.65) < 1e-9
    print("test_real_summary_and_compliance OK")


def test_since_ts_and_formatting():
    """since_ts (接続テストのマーカー以降) の絞り込みと、canary サマリーの文字列"""
    with tempfile.TemporaryDirectory() as d:
        old, new = Path(d) / "battle_20260901_000000.jsonl", Path(d) / "battle_20260902_000000.jsonl"
        for p, out in ((old, "win"), (new, "loss")):
            rows = [{"type": "session", "source": "experiment", "package_id": "pkg"}, {"type": "outcome", "outcome": out}]
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        now = time.time()
        os.utime(old, (now - 1000, now - 1000))
        s_all = RE.real_summary(package_id="pkg", battles_dir=Path(d))
        assert s_all["n_decided"] == 2 and s_all["wins"] == 1
        s_new = RE.real_summary(package_id="pkg", battles_dir=Path(d), since_ts=now - 10)
        assert s_new["n_logs"] == 1 and s_new["wins"] == 0
        assert [r["file"] for r in RE.labeled_rows(Path(d), package_id="pkg", since_ts=now - 10)] == [new.name]
        txt = RE.format_summary(s_all, "全期間")
        assert "[全期間] 対戦ログ 2 / 勝敗確定 2 (勝 1 敗 1)" in txt and "勝率 50.0%" in txt and "experiment 2" in txt
        assert "まだ無い" in RE.format_summary(RE.real_summary(package_id="none", battles_dir=Path(d)), "x")
    assert "決定なし" in RE.format_audit({"n_battles": 1, "n_decisions": 0})
    assert "読み込めない" in RE.format_audit(None)
    a = {"n_battles": 2, "n_decisions": 10, "advice_rate": 0.9, "compliance": 0.5, "timely_rate": None, "n_defects": 3}
    assert "一致 (遵守率) 50%" in RE.format_audit(a) and "時間内 -" in RE.format_audit(a) and "欠陥 3 件" in RE.format_audit(a)
    print("test_since_ts_and_formatting OK")


def test_team_match_excludes_other_party():
    """experiment ラベルが付いたまま別のパーティで対戦したログは Package の勝敗に入れない (2026-09-29 第16回:
    13 ログ中 10 が別パーティ)。選出画面で 6 枠読めた種、読めなければ場に出た種で照合する"""
    pkg = ["charizard", "hippowdon", "hydreigon", "metagross", "primarina", "sneasler"]

    def scene(scene_name, party, active=None):
        return {"type": "scene", "scene": scene_name,
                "state": {"player": {"active": active, "party": [{"species": s} for s in party]}}}

    with tempfile.TemporaryDirectory() as d:
        logs = {
            "battle_20260929_000001.jsonl": [scene("selection", pkg), {"type": "outcome", "outcome": "win"}],           # Package
            "battle_20260929_000002.jsonl": [scene("selection", ["archaludon", "garchomp", "lopunny", "slurpuff",
                                                                  "basculegion", "primarina"]),
                                             {"type": "outcome", "outcome": "loss"}],                                   # 別パーティ
            "battle_20260929_000003.jsonl": [scene("selection", []), scene("field", ["metagross", "hydreigon"], active=1),
                                             {"type": "outcome", "outcome": "win"}],                                    # 選出未読、場の種で照合
            "battle_20260929_000004.jsonl": [scene("selection", []), scene("field", ["garchomp"], active=0),
                                             {"type": "outcome", "outcome": "loss"}],                                   # 場の種が Package 外
            "battle_20260929_000005.jsonl": [{"type": "outcome", "outcome": "win"}],                                    # 何も読めない
        }
        for name, rows in logs.items():
            rows = [{"type": "session", "source": "experiment", "package_id": "pkg"}] + rows
            (Path(d) / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        rows = RE.labeled_rows(Path(d), package_id="pkg", package_species=pkg)
        assert [r["team_match"] for r in rows] == [True, False, True, False, None], [r["team_match"] for r in rows]
        assert rows[0]["party_read"] and not rows[2]["party_read"] and rows[2]["species"] == ["hydreigon"]
        s = RE.real_summary(package_id="pkg", battles_dir=Path(d), package_species=pkg)
        assert s["n_logs"] == 5 and s["n_decided"] == 3 and s["wins"] == 3, s      # 別パーティの 2 敗は除外
        assert s["n_team_mismatch"] == 2 and s["n_team_unknown"] == 1, s
        txt = RE.format_summary(s, "x")
        assert "別のパーティで対戦: 2 ログ" in txt and "読めなかったログ: 1" in txt, txt
        # 種の照合なし (registry に種が無い) なら従来どおり全部数える
        s0 = RE.real_summary(package_id="pkg", battles_dir=Path(d))
        assert s0["n_decided"] == 5 and s0["n_team_mismatch"] == 0
    assert RE.team_match([], pkg) is None and RE.team_match(["garchomp"], None) is None
    assert RE.team_match(["metagross"], pkg) is True and RE.team_match(["metagross", "garchomp"], pkg) is False
    print("test_team_match_excludes_other_party OK")


def test_transfer_and_calibration():
    assert TR.regulation_distance(["a", "b", "c"], ["a", "b", "c"]) == 0.0
    d = TR.regulation_distance(["a", "b", "c", "d"], ["a", "b", "x", "y"])
    assert abs(d - (1 - 2 / 6)) < 1e-9
    assert TR.lambda_old(0.0) == 1.0 and TR.lambda_old(0.5) < TR.lambda_old(0.2) < 1.0
    assert TR.mix_prior(0.6, 0.4, n_new=0, distance=0.0) == 0.6          # 新標本なし → 旧
    assert TR.mix_prior(0.6, 0.4, n_new=1000, distance=0.0) == 0.4       # 十分な新標本 → 新
    mid = TR.mix_prior(0.6, 0.4, n_new=200, distance=0.9)
    assert 0.4 < mid < 0.6
    p = [0.9, 0.8, 0.2, 0.1, 0.5]
    y = [1, 1, 0, 0, 1]
    assert CAL.brier(p, y) < 0.1
    t = CAL.reliability_table(p, y, bins=5)
    assert sum(r["n"] for r in t) == 5 and CAL.expected_calibration_error(t) >= 0.0
    print("test_transfer_and_calibration OK")


if __name__ == "__main__":
    test_real_summary_and_compliance()
    test_since_ts_and_formatting()
    test_team_match_excludes_other_party()
    test_transfer_and_calibration()
