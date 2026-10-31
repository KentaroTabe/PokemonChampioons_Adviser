"""tests/test_sim_legal_inputs のうち poke-env が要る部分 (ローカル専用: scripts/run_test.sh の LOCAL_ONLY_TESTS。CI の最小依存 requirements-ci.txt に poke-env が無いため
scripts/ci_tests.sh から外した。2026-10-31)。

make_advisor_player の choose_move を偽 Battle で直接呼ぶ (make_advisor_player が poke_env.player.Player を読む)。状態変換・合法性・
受入監査の純粋な部分と集計は tests/test_sim_legal_inputs (CI)。使用率 DB・RL・Showdown には依存しない。

    python -m tests.test_sim_legal_inputs_local
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from champions_agent.env import advisor_player as AP
from champions_agent.env import sim_accept as SA
from tests.test_scoring_transition import _Hint
from tests.test_sim_legal_inputs import _Team, _battle
from tests.test_sim_picked_roster import _team_text


def test_player_counts_accept_and_records(tmp=None):
    """make_advisor_player の choose_move を偽 Battle で直接呼び、stats と対戦記録に受入監査が残ることを確かめる
    (選出不明は picked_unknown と決定の印、合法手 1 つは forced_single、通常の決定は印なし)"""
    from tools.team_build.battle_log import BattleRecorder
    with tempfile.TemporaryDirectory() as d, _Team(), _Hint():
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        stats: dict = {}
        player = AP.make_advisor_player(team_source=NS(last_text=_team_text()), stats=stats, recorder=rec,
                                        start_listening=False, battle_format="gen9championsbssregmc")
        player.create_order = lambda x, mega=False: ("order", getattr(x, "id", None) or getattr(x, "species", None))
        player.choose_random_move = lambda b: ("random",)
        b = _battle()
        assert player.choose_move(b)[0] == "order"
        nb = _battle(request=False)                         # 選出が分からない (要求なし)
        player.choose_move(nb)
        one = _battle(avail_moves=["struggle"], req_moves=[("struggle", False)], trapped=True)
        player.choose_move(one)
        assert stats["picked_unknown"] == 1 and stats["accept:picked_unknown"] == 1 and stats["accept:decisions"] == 3
        assert stats.get("accept:best_illegal", 0) == 0 and stats.get("accept:unpicked_candidate", 0) == 0
        assert stats.get("accept:skip_unexplained", 0) == 0 and stats.get("accept:random_fallback", 0) == 0, stats
        turns = rec._turns["battle-legal-1"]
        assert "accept" not in turns[0] and turns[1]["accept"]["picked_unknown"] is True
        acc = SA.summary_from_stats(stats)
        assert SA.accept_violations(acc) and any("選出が分からない" in x for x in SA.accept_violations(acc))
    print("test_player_counts_accept_and_records OK")


def main():
    test_player_counts_accept_and_records()
    print("ALL OK")


if __name__ == "__main__":
    main()
