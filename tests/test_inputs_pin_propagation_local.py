"""tests/test_inputs_pin_propagation のうち poke-env が要る部分 (ローカル専用: scripts/run_test.sh の LOCAL_ONLY_TESTS。CI の最小依存 requirements-ci.txt に poke-env が無いため
scripts/ci_tests.sh から外した。2026-10-31)。

(e) シミュレータの測定 (advisor_player): make_advisor_player が poke_env.player.Player を読む。engine・advice_key・real_accept・
advice_replay の入口は tests/test_inputs_pin_propagation (CI)、サーバーの入口は tests/test_inputs_pin_propagation_server (ローカル専用)。

    python -m tests.test_inputs_pin_propagation_local
"""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advisor.engine as ENG
from advisor import data_pin as DP
from tests.test_inputs_pin_propagation import PIN_MSG
from tests.test_scoring_transition import _Hint


def test_advisor_player_counts_pin_error_as_error():
    """シミュレータの測定: inputs_pin の結果は例外として stats["error"] に数える。以前の経路 (evaluate が InputsPinError を
    外へ出したとき) と stats が同じ (last_error の文以外)。採点の集計 (scoring_*)・助言どおりの決定 (decide) には数えない"""
    from types import SimpleNamespace as NS
    from champions_agent.env import advisor_player as AP
    from tests.test_sim_legal_inputs import _Team, _battle
    from tests.test_sim_picked_roster import _team_text

    def run(fake_eval):
        stats: dict = {}
        with _Team(), _Hint(), patch.object(ENG, "evaluate", fake_eval):
            player = AP.make_advisor_player(team_source=NS(last_text=_team_text()), stats=stats, start_listening=False,
                                            battle_format="gen9championsbssregmc")
            player.create_order = lambda x, mega=False: ("order", getattr(x, "id", None) or getattr(x, "species", None))
            player.choose_random_move = lambda b: ("random",)
            out = player.choose_move(_battle())
        return out, stats

    def pin_result(state, resolver=None, **kw):
        return DP.pin_error_result(DP.InputsPinError(PIN_MSG))

    def pin_raise(state, resolver=None, **kw):
        raise DP.InputsPinError(PIN_MSG)
    out_new, st_new = run(pin_result)
    out_old, st_old = run(pin_raise)
    assert st_new["error"] == 1 and "InputsPinError" in st_new["last_error"] and PIN_MSG in st_new["last_error"], st_new
    assert out_new == out_old and {k: v for k, v in st_new.items() if k != "last_error"} == \
        {k: v for k, v in st_old.items() if k != "last_error"}, (st_new, st_old)
    assert not any(str(k).startswith("scoring") for k in st_new) and "decide" not in st_new, st_new
    # 純粋な部分: 通常の ok=False・ok=True・None は送り直さない
    AP.raise_if_pin_error(None)
    AP.raise_if_pin_error({"ok": False, "reason": "自分の場のポケモンが未特定です"})
    AP.raise_if_pin_error({"ok": True, "best": {"kind": "move", "id": "surf"}})
    try:
        AP.raise_if_pin_error(pin_result(None))
        raise AssertionError("送り直さない")
    except DP.InputsPinError as e:
        assert str(e) == PIN_MSG
    print("test_advisor_player_counts_pin_error_as_error OK")


def main():
    test_advisor_player_counts_pin_error_as_error()
    print("ALL OK")


if __name__ == "__main__":
    main()
