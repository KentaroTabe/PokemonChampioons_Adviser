"""confidence racing の判定ロジックのテスト (測定はスタブ: 真の勝率から乱数で勝敗列を作る)。

    python -m tests.test_team_build_racing
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path

from tools.team_build import racing as R
from tools.team_build.verdict import DEGRADED, EQUIVALENT, IMPROVED, UNCERTAIN


def _stub_measure(true_rates: dict, rng: random.Random):
    """measure_round の差し替え: 各 arm に true_rates[arm_id] の勝率で n 戦ぶん勝敗を足す (対応比較の相手ノイズも共有)"""
    def _measure(arms, n, offset, seed, split_file, tier, fold, out_dir, stage, parallel=1, timeout=0,
                 pick_policy="advisor"):
        # 同一相手列: 相手の強さ shared[i] を全 arm で共有し、arm の実力との差で勝敗を決める
        shared = [rng.random() for _ in range(n)]
        for arm in arms:
            p = true_rates[arm.arm_id]
            outs = [1 if shared[i] < p else 0 for i in range(n)]
            arm.outcomes.extend(outs)
            arm.n_done = len(arm.outcomes)
            arm.history.append({"offset": offset, "n": n})
    return _measure


def test_race_eliminates_and_terminates():
    rng = random.Random(7)
    rates = {"ref": 0.55, "good": 0.70, "same": 0.555, "bad": 0.40, "close": 0.60}
    R.measure_round = _stub_measure(rates, rng)
    with tempfile.TemporaryDirectory() as d:
        cands = [R.Arm(a, Path(f"{a}.txt")) for a in ("good", "same", "bad", "close")]
        ref = R.Arm("ref", Path("ref.txt"))
        res = R.race(cands, ref, Path("split.json"), "search", 1, Path(d), stage="test",
                     steps=(100, 300, 600, 1200, 2400), max_battles=2400, log=lambda m: None)
        st = {a["arm_id"]: a for a in res["arms"]}
        assert st["good"]["state"] == IMPROVED, st["good"]
        assert st["bad"]["state"] == DEGRADED and st["bad"]["eliminated_at"] is not None or st["bad"]["state"] == DEGRADED
        assert st["same"]["state"] in (EQUIVALENT, UNCERTAIN, DEGRADED), st["same"]["state"]
        assert res["n_candidates_seen"] == 4 and (Path(d) / "test.json").exists()
        assert all(a["n_done"] <= 2400 for a in res["arms"])
        assert res["reference"]["n_done"] == max(a["n_done"] for a in res["arms"])   # 参照は常に一緒に測る
        cont = R.contenders(res)
        assert "good" in cont and "bad" not in cont, cont
    print("test_race_eliminates_and_terminates OK")


def test_min_terminal_n_and_run_to_max():
    """improved は min_terminal_n 戦未満では確定させず測り続ける。run_to_max は状態によらず上限まで"""
    rng = random.Random(11)
    rates = {"ref": 0.50, "good": 0.75, "bad": 0.30}
    R.measure_round = _stub_measure(rates, rng)
    with tempfile.TemporaryDirectory() as d:
        cands = [R.Arm(a, Path(f"{a}.txt")) for a in ("good", "bad")]
        ref = R.Arm("ref", Path("ref.txt"))
        res = R.race(cands, ref, Path("split.json"), "search", 1, Path(d), stage="t1",
                     steps=(100, 300, 600), max_battles=600, log=lambda m: None, min_terminal_n=300)
        st = {a["arm_id"]: a for a in res["arms"]}
        assert st["good"]["state"] == IMPROVED and st["good"]["n_done"] >= 300, st["good"]   # 100 戦では確定しない
        assert st["bad"]["state"] == DEGRADED and st["bad"]["n_done"] == 100                  # 脱落は早期のまま
        assert res["min_terminal_n"] == 300 and res["run_to_max"] is False
        res2 = R.race([R.Arm("good", Path("good.txt"))], R.Arm("ref", Path("ref.txt")), Path("split.json"), "holdout",
                      2, Path(d), stage="t2", steps=(100, 300, 600), max_battles=600, log=lambda m: None,
                      compare_to_best=False, run_to_max=True)
        assert res2["arms"][0]["n_done"] == 600 and res2["arms"][0]["state"] == IMPROVED
        res3 = R.race([R.Arm("good", Path("good.txt"))], R.Arm("ref", Path("ref.txt")), Path("split.json"), "search",
                      3, Path(d), stage="t3", steps=(100, 300, 600), max_battles=600, log=lambda m: None,
                      min_terminal_n=0)
        assert res3["arms"][0]["n_done"] == 100                                                # 従来: 1 段目で確定
    print("test_min_terminal_n_and_run_to_max OK")


def test_measure_cmd_flags():
    arm = R.Arm("c1", Path("t.txt"), selection_model="m.pt", models_dir="pin", extra_args=["--action-noise", "0.1"])
    cmd = R.measure_cmd(arm, 100, 300, 5, Path("split.json"), "search", 1, Path("o.json"), Path("b.jsonl"))
    s = " ".join(cmd)
    for flag in ("--opp-split split.json:search:1", "--opp-offset 300", "--selection-model m.pt",
                 "--models-dir pin", "--action-noise 0.1", "--candidate-id c1", "--pick-policy advisor"):
        assert flag in s, flag
    # arm 固有の選出方策は round の指定より優先 (ablation で条件を混ぜて同時に回す)
    arm2 = R.Arm("c2", Path("t.txt"), pick_policy="teampreview")
    s2 = " ".join(R.measure_cmd(arm2, 100, 0, 5, Path("split.json"), "search", 1, Path("o.json"), Path("b.jsonl"),
                                pick_policy="advisor"))
    assert "--pick-policy teampreview" in s2 and "--pick-policy advisor" not in s2, s2
    print("test_measure_cmd_flags OK")


def test_measure_round_reuses_measured_json():
    """同じ (stage, arm, offset, n) の測定済み JSON があれば測り直さない (止めた run の腕単位の再開)"""
    import importlib
    import json as _json
    R2 = importlib.reload(R)          # 上のテストで差し替えた measure_round を元に戻す
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "st_a_0_5.json").write_text(_json.dumps({"outcomes": [1, 0, 1, 1, 0], "win_rate": 0.6}), encoding="utf-8")
        (out / "st_b_0_5.json").write_text(_json.dumps({"outcomes": [1, 0]}), encoding="utf-8")   # 件数不足 → 使わない
        calls = []
        R2._run_one = lambda cmd, log_path, timeout: (calls.append(cmd), 1)[1]    # 実行は失敗扱い
        a, b = R2.Arm("a", Path("a.txt")), R2.Arm("b", Path("b.txt"))
        R2.measure_round([a, b], 5, 0, 1, Path("split.json"), "search", 1, out, "st", parallel=1)
        assert a.outcomes == [1, 0, 1, 1, 0] and a.n_done == 5 and a.history[0]["reused"] is True
        assert b.outcomes == [] and len(calls) == 1 and "--candidate-id b" in " ".join(calls[0])
    print("test_measure_round_reuses_measured_json OK")


def test_look_z_widens_with_looks():
    """段階判定 (optional stopping) の z: 1 段は 1.96、段数が増えるほど広がり、表外は Bonferroni"""
    from tools.team_build.verdict import look_z, n_looks
    assert look_z(1) == 1.96
    assert abs(look_z(3) - 2.289) < 1e-9          # Pocock K=3
    assert look_z(3) < look_z(7) < look_z(12)     # 単調に広がる (K=12 は表外 → Bonferroni)
    assert look_z(12) > 2.555                     # Bonferroni は Pocock 表の最大値より保守的
    assert look_z(7, correction="none") == 1.96   # 旧挙動
    assert abs(look_z(3, correction="bonferroni") - 2.394) < 1e-3
    # 段数: cap 以下の段階数。medium (600) は 100/300/600 の 3 段、cap が段階に無ければ cap 自体が最終段
    assert n_looks((100, 300, 600, 1200), cap=600) == 3
    assert n_looks((100, 300, 600, 1200), cap=1200) == 4
    assert n_looks((100, 300, 600, 1200), cap=800) == 4
    assert n_looks((100, 300), cap=None) == 2
    print("test_look_z_widens_with_looks OK")


def test_race_uses_look_adjusted_z():
    """racing の判定 z は段数に応じた値で、結果に記録される。境界候補は 1.96 なら落ちる差でも保留になる"""
    rng = random.Random(11)
    rates = {"ref": 0.60, "edge": 0.50}
    R.measure_round = _stub_measure(rates, rng)
    with tempfile.TemporaryDirectory() as d:
        res = R.race([R.Arm("edge", Path("e.txt"))], R.Arm("ref", Path("r.txt")), Path("split.json"), "search", 1,
                     Path(d), stage="t", steps=(100, 300, 600), max_battles=600, log=lambda m: None)
    assert res["n_looks"] == 3 and abs(res["z"] - 2.289) < 1e-9, (res["n_looks"], res["z"])
    arm = res["arms"][0]
    assert arm["result"]["ci_low"] is not None
    # CI 幅が z に比例している (se × z)
    half = (arm["result"]["ci_high"] - arm["result"]["ci_low"]) / 2
    assert abs(half - 2.289 * arm["result"]["se"]) < 1e-9, (half, arm["result"]["se"])
    print("test_race_uses_look_adjusted_z OK")


if __name__ == "__main__":
    test_race_eliminates_and_terminates()
    test_min_terminal_n_and_run_to_max()
    test_measure_cmd_flags()
    test_measure_round_reuses_measured_json()
    test_look_z_widens_with_looks()
    test_race_uses_look_adjusted_z()
