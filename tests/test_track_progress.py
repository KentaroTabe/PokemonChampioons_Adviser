"""日次定点 (tools/track_progress) のテスト: 評価 1 回の打ち切り (戦数比例の timeout を evaluate の
--timeout と subprocess の timeout の両方に渡す)、強制終了・自己終了のどちらでも None を返して
記録に残す、結果行の読み取り、凍結参照の逸脱指標。

    python -m tests.test_track_progress
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

from champions_agent.config import TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K, TRACK_PROGRESS_TIMEOUT_GRACE_S
from tools import track_progress as TP


def _fake_run(stdout: str = "", returncode: int = 0, raise_timeout: bool = False):
    calls = []

    def run(cmd, **kw):
        calls.append((list(cmd), dict(kw)))
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)
    return run, calls


def test_timeout_is_proportional_to_battles():
    assert TP.eval_timeout_s(1000) == TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K
    assert TP.eval_timeout_s(3000) == 3 * TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K
    # 1,000 戦未満でも 1,000 戦ぶんは待つ (即時打ち切りにならない)
    assert TP.eval_timeout_s(0) == TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K
    assert TP.eval_timeout_s(1) == TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K
    assert TP.eval_timeout_s(999) == TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K
    print("ok: timeout は戦数に比例")


def test_run_eval_passes_timeouts_and_parses_result():
    orig = TP.subprocess.run
    TP.TIMEOUTS.clear()
    run, calls = _fake_run(stdout="[evaluate] {'win_rate': 0.512, 'n': 3000}\n")
    TP.subprocess.run = run
    try:
        res = TP._run_eval(3000, "current", "matchup", opponent="agents", agents_style="balance")
    finally:
        TP.subprocess.run = orig
    assert res == {"win_rate": 0.512, "n": 3000}, res
    cmd, kw = calls[0]
    expect = TP.eval_timeout_s(3000)
    assert cmd[cmd.index("--timeout") + 1] == str(expect), cmd
    assert kw["timeout"] == expect + TRACK_PROGRESS_TIMEOUT_GRACE_S, kw
    assert "--agents-style" in cmd and "--no-save" in cmd
    assert TP.TIMEOUTS == []
    print("ok: --timeout と subprocess timeout を渡し、結果行を読む")


def test_run_eval_records_forced_timeout():
    orig = TP.subprocess.run
    TP.TIMEOUTS.clear()
    run, calls = _fake_run(raise_timeout=True)
    TP.subprocess.run = run
    try:
        res = TP._run_eval(3000, "current", "matchup", opponent="agents")
    finally:
        TP.subprocess.run = orig
    assert res is None
    assert TP.TIMEOUTS == ["current/matchup/agents"], TP.TIMEOUTS
    print("ok: 強制終了 (TimeoutExpired) は None + 記録")


def test_run_eval_records_self_timeout():
    orig = TP.subprocess.run
    TP.TIMEOUTS.clear()
    run, calls = _fake_run(stdout="[evaluate] TIMEOUT: 2700秒で打ち切り\n", returncode=1)
    TP.subprocess.run = run
    try:
        res = TP._run_eval(3000, "best", "model")
    finally:
        TP.subprocess.run = orig
    assert res is None
    assert TP.TIMEOUTS == ["best/model/benchmark"], TP.TIMEOUTS
    TP.TIMEOUTS.clear()
    print("ok: evaluate 側の自己終了 (SIGALRM) も None + 記録")


def test_run_eval_without_result_line_is_none():
    orig = TP.subprocess.run
    TP.TIMEOUTS.clear()
    run, calls = _fake_run(stdout="[rl_bridge] loaded\n", returncode=2)
    TP.subprocess.run = run
    try:
        res = TP._run_eval(1000, "current", "matchup")
    finally:
        TP.subprocess.run = orig
    assert res is None
    assert TP.TIMEOUTS == []
    print("ok: 結果行なしは None (timeout 扱いにしない)")


def test_deviation_sigma():
    # 同じ勝率なら 0、n=3000 同士で 0.05 の差は約 3.9SE
    assert TP.deviation_sigma(0.5, 0.5, 3000, 3000) == 0.0
    s = TP.deviation_sigma(0.55, 0.50, 3000, 3000)
    assert 3.8 < s < 4.0, s
    print("ok: deviation_sigma")


def main() -> None:
    test_timeout_is_proportional_to_battles()
    test_run_eval_passes_timeouts_and_parses_result()
    test_run_eval_records_forced_timeout()
    test_run_eval_records_self_timeout()
    test_run_eval_without_result_line_is_none()
    test_deviation_sigma()
    print("\nALL OK")


if __name__ == "__main__":
    main()
