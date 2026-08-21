"""battle_logger の勝敗フォールバック推定 (ひんし数) のテスト。

    scripts/run_test.sh test_battle_outcome_infer

2026-08-20 第5回接続テスト: リザルト画面を飛ばして次戦の選出へ進むと
勝敗メッセージもレートも取れず、9戦中6戦が outcome=unknown になった。
ローテーション時に前戦最終盤面のひんし数から勝敗を推定する。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from battle_logger import BattleLogger


def _party(n_fainted: int, n_total: int = 3) -> list:
    return [{"status": "fainted" if i < n_fainted else None,
             "species_id": f"mon{i}", "ja": f"モン{i}"}
            for i in range(n_total)]


def _frame(seq: int, my_f: int, opp_f: int, scene: str = "field") -> dict:
    return {"scene": scene, "battle_seq": seq, "turn": 5,
            "events": [],
            "player": {"party": _party(my_f)},
            "opponent": {"party": _party(opp_f)}}


def _read_outcomes(log_dir: Path) -> list:
    out = []
    for p in sorted(log_dir.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("type") == "outcome":
                out.append(r)
    return out


def _run(my_f: int, opp_f: int) -> dict:
    log_dir = Path(tempfile.mkdtemp())
    lg = BattleLogger(log_dir=log_dir)
    lg.on_frame(_frame(1, 0, 0), [])          # 対戦1開始 (基準記録)
    lg.on_frame(_frame(1, my_f, opp_f), [])   # 最終盤面
    lg.on_frame(_frame(2, 0, 0, "selection"), [])   # 次戦へ回転
    recs = _read_outcomes(log_dir)
    assert len(recs) == 1, recs
    return recs[0]


def test_opponent_all_fainted_infers_win():
    rec = _run(my_f=1, opp_f=3)
    assert rec["outcome"] == "win" and rec.get("inferred") is True, rec
    print("test_opponent_all_fainted_infers_win OK")


def test_player_all_fainted_infers_loss():
    rec = _run(my_f=3, opp_f=2)
    assert rec["outcome"] == "loss" and rec.get("inferred") is True, rec
    print("test_player_all_fainted_infers_loss OK")


def test_undecided_stays_unknown():
    rec = _run(my_f=2, opp_f=2)
    assert rec["outcome"] == "unknown" and "inferred" not in rec, rec
    print("test_undecided_stays_unknown OK")


def test_rank_key_finalizes_outcome_immediately():
    """ランク画面イベント (battle_end_rank) で勝敗を即時確定する (第7回提案)。

    ローテーションを待たず、終局時点のひんし数で推定できる。
    その後のローテーションで二重記録しない。
    """
    log_dir = Path(tempfile.mkdtemp())
    lg = BattleLogger(log_dir=log_dir)
    lg.on_frame(_frame(1, 0, 0), [])
    lg.on_frame(_frame(1, 1, 3), [])                      # 終局盤面
    lg.on_frame(_frame(1, 1, 3), ["battle_end_rank"])     # ランク画面
    recs = _read_outcomes(log_dir)
    assert len(recs) == 1 and recs[0]["outcome"] == "win" \
        and recs[0].get("inferred") is True, recs
    lg.on_frame(_frame(2, 0, 0, "selection"), [])         # 次戦へ回転
    recs2 = _read_outcomes(log_dir)
    assert len(recs2) == 1, recs2                          # 二重記録なし
    print("test_rank_key_finalizes_outcome_immediately OK")


def test_explicit_outcome_wins_over_inference():
    """battle_lose 等で outcome が確定していれば推定は使わない"""
    log_dir = Path(tempfile.mkdtemp())
    lg = BattleLogger(log_dir=log_dir)
    lg.on_frame(_frame(1, 0, 0), [])
    lg.on_frame(dict(_frame(1, 0, 3), outcome="loss"), [])   # 表示は負け
    recs = _read_outcomes(log_dir)
    assert recs and recs[-1]["outcome"] == "loss" \
        and "inferred" not in recs[-1], recs
    print("test_explicit_outcome_wins_over_inference OK")


def main() -> None:
    test_opponent_all_fainted_infers_win()
    test_player_all_fainted_infers_loss()
    test_undecided_stays_unknown()
    test_rank_key_finalizes_outcome_immediately()
    test_explicit_outcome_wins_over_inference()
    print("\nALL OK")


if __name__ == "__main__":
    main()
