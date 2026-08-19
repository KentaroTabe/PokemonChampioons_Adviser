"""audit_session の機械前置フィルタ (矛盾候補検出) のテスト。

    python -m tests.test_audit_anomalies

2026-08-19 opus監査で、hp<=1 だけを「ひんし」とみなす検出器が、
きあいのタスキで1%残った生存個体や0%誤読の1フレームを「ひんし」扱いし、
その後の正常表示を「蘇生」矛盾として誤検出していた。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.audit_session import detect_anomalies


def _log(records) -> str:
    p = Path(tempfile.mkdtemp()) / "battle_test.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def _scene(t, ja, hp, status=None):
    return {"type": "scene", "t": t, "scene": "battle_hud",
            "state": {"player": {"party": []},
                      "opponent": {"party": [
                          {"ja": ja, "hp": hp, "status": status}]}}}


def test_sash_survivor_is_not_revive_anomaly():
    """タスキ1%生存→回復/継続表示を「蘇生」と誤検出しない"""
    path = _log([
        _scene(100.0, "サザンドラ", 100.0),
        _scene(110.0, "サザンドラ", 1.0),      # タスキで耐えた (生存)
        _scene(120.0, "サザンドラ", 1.0),
    ])
    anoms = detect_anomalies(path)
    assert not any("蘇生" in d for _, d in anoms), anoms
    print("test_sash_survivor_is_not_revive_anomaly OK")


def test_true_fainted_then_alive_is_flagged():
    """状態が明示的に fainted の後のHP再表示は従来どおり矛盾として検出"""
    path = _log([
        _scene(100.0, "ドリュウズ", 100.0),
        _scene(110.0, "ドリュウズ", 0.0, status="fainted"),
        _scene(130.0, "ドリュウズ", 100.0),
    ])
    anoms = detect_anomalies(path)
    assert any("蘇生" in d for _, d in anoms), anoms
    print("test_true_fainted_then_alive_is_flagged OK")


if __name__ == "__main__":
    test_sash_survivor_is_not_revive_anomaly()
    test_true_fainted_then_alive_is_flagged()
    print("\nALL OK")
