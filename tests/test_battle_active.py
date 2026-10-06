"""check_battle_active の判定 (outcome終端化 2026-08-21 第8回) のテスト。

    scripts/run_test.sh test_battle_active

リザルト画面 (ランクキー) で outcome が記録されたら、窓の残り時間を
待たずに「対戦していない」と判定する — 構築提案をリザルト確認の
数秒後に実行できるようにするため。
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from tools.check_battle_active import battle_active


def _write(log_dir: Path, records: list) -> None:
    p = log_dir / "battle_test.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_recent_battle_signals_are_active():
    d = Path(tempfile.mkdtemp())
    now = time.time()
    _write(d, [
        {"type": "scene", "scene": "command", "t": now - 30},
        {"type": "events", "fired": ["move_player_uturn"], "t": now - 10},
    ])
    assert battle_active(1.0, log_dir=d) is True
    print("test_recent_battle_signals_are_active OK")


def test_outcome_is_terminal():
    """最新のシグナルが outcome なら、直前に対戦記録があっても終了扱い"""
    d = Path(tempfile.mkdtemp())
    now = time.time()
    _write(d, [
        {"type": "scene", "scene": "command", "t": now - 20},
        {"type": "events", "fired": ["battle_end_rank"], "t": now - 5},
        {"type": "outcome", "outcome": "win", "t": now - 4},
    ])
    assert battle_active(1.0, log_dir=d) is False
    print("test_outcome_is_terminal OK")


def test_new_battle_after_outcome_is_active():
    """outcome の後に次戦の選出が始まっていれば対戦中"""
    d = Path(tempfile.mkdtemp())
    now = time.time()
    _write(d, [
        {"type": "outcome", "outcome": "win", "t": now - 30},
        {"type": "scene", "scene": "selection", "t": now - 5},
    ])
    assert battle_active(1.0, log_dir=d) is True
    print("test_new_battle_after_outcome_is_active OK")


def test_quiet_log_is_inactive():
    d = Path(tempfile.mkdtemp())
    old = time.time() - 600
    _write(d, [{"type": "scene", "scene": "command", "t": old}])
    assert battle_active(1.0, log_dir=d) is False
    print("test_quiet_log_is_inactive OK")


def main() -> None:
    test_recent_battle_signals_are_active()
    test_outcome_is_terminal()
    test_new_battle_after_outcome_is_active()
    test_quiet_log_is_inactive()
    print("\nALL OK")


if __name__ == "__main__":
    main()
