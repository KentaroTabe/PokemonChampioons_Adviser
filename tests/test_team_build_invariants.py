"""hard invariant 監視のテスト。

    python -m tests.test_team_build_invariants
"""
from tools.team_build.invariants import evaluate_invariants, scan_server_log


def test_invariants():
    log = ("INFO: Application startup complete.\n[server] 準備完了\n"
           "Traceback (most recent call last):\n  File x\n"
           "[rl_bridge] 読み込み失敗: battle_policy_balance_ema.zip\n"
           "|error|[Invalid choice] Can't move\n")
    c = scan_server_log(log)
    assert c == {"crash": 1, "model_load": 1, "illegal": 1, "restarts": 1}, c
    r = evaluate_invariants(c, no_advice_rate=0.3, latency_p95_ms=5000)
    assert not r["ok"] and r["action"] == "emergency_rollback" and len(r["violations"]) == 5, r
    ok = evaluate_invariants({"crash": 0, "model_load": 0, "illegal": 0}, no_advice_rate=0.0, latency_p95_ms=800)
    assert ok["ok"] and ok["action"] == "none"
    print("test_invariants OK")


if __name__ == "__main__":
    test_invariants()
