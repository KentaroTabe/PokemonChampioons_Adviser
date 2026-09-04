"""HP鮮度レポート (tools/hp_freshness_report) の純粋部分のテスト。

    scripts/run_test.sh test_hp_freshness
"""
from __future__ import annotations

from tools.hp_freshness_report import summarize_rows


def _row(t, scene, my_ts, opp_ts=None):
    def mon(ts):
        return {"species_id": "x", "hp_percent": 50.0, "hp_read_ts": ts}
    return {"type": "scene", "scene": scene, "t": t,
            "state": {"player": {"active_index": 0, "party": [mon(my_ts)]},
                      "opponent": {"active_index": 0, "party": [mon(opp_ts)]}}}


def test_summarize_rows_counts_stale_decisions():
    rows = [
        _row(100.0, "command", my_ts=99.5, opp_ts=90.0),   # 自分は新鮮、相手は10秒固着
        _row(110.0, "move_select", my_ts=104.0, opp_ts=None),  # 自分6秒固着、相手は計測不能
        _row(120.0, "watch", my_ts=100.0, opp_ts=100.0),   # 決定点でない
        {"type": "advice"},
    ]
    s = summarize_rows(rows, stale_sec=3.0)
    assert s["decisions"] == 2
    assert s["measurable"] == 3            # (自,相) + (自)
    assert s["stale"] == 2                 # 相手10秒 + 自分6秒
    assert s["max_age"] == 10.0
    assert s["per_side"]["player"] == {"measurable": 2, "stale": 1}
    assert s["per_side"]["opponent"] == {"measurable": 1, "stale": 1}
    print("test_summarize_rows_counts_stale_decisions OK")


def test_summarize_rows_without_freshness_fields():
    """hp_read_ts の無い古いログは計測不能として数える"""
    rows = [{"type": "scene", "scene": "command", "t": 5.0,
             "state": {"player": {"active_index": 0, "party": [{"hp_percent": 80.0}]},
                       "opponent": {"active_index": None, "party": []}}}]
    s = summarize_rows(rows, 3.0)
    assert s["decisions"] == 1 and s["measurable"] == 0 and s["stale"] == 0
    print("test_summarize_rows_without_freshness_fields OK")


if __name__ == "__main__":
    test_summarize_rows_counts_stale_decisions()
    test_summarize_rows_without_freshness_fields()
