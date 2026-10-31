"""tests/test_advice_m2d のうち poke-env が要る部分 (ローカル専用: scripts/run_test.sh の LOCAL_ONLY_TESTS。CI の最小依存 requirements-ci.txt に poke-env が無いため
scripts/ci_tests.sh から外した。2026-10-31)。

段 2 の --own-pool: check_advisor_player._remembering_teambuilder が poke_env.teambuilder.Teambuilder を継ぐので、poke-env の
無い環境では作れない。M2d の他の段 (engine の回帰を含む) は tests/test_advice_m2d (CI)。

    python -m tests.test_advice_m2d_local
"""
from __future__ import annotations

import os
import random

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from tests.test_advice_m2d import _TEAM_A, _TEAM_B


def test_own_pool_teambuilder_correspondence():
    """--own-pool: Random(相手 seed + 1) で対戦ごとに引く。同じ seed の 2 条件で自チームの列 (本文) が一致する"""
    from tools.check_advisor_player import _PoolSource, _remembering_teambuilder
    texts = [_TEAM_A, _TEAM_B, _TEAM_A.replace("Earthquake", "Outrage")]
    tb1 = _remembering_teambuilder(_PoolSource(texts, random.Random(20261009 + 1)))
    tb2 = _remembering_teambuilder(_PoolSource(texts, random.Random(20261009 + 1)))
    for _ in range(10):
        tb1.yield_team()
        tb2.yield_team()
    assert tb1.texts == tb2.texts and len(set(tb1.texts)) > 1
    assert [tb1.text_for_battle(k) for k in range(10)] == tb1.texts
    print("test_own_pool_teambuilder_correspondence OK")


def main():
    test_own_pool_teambuilder_correspondence()
    print("ALL OK")


if __name__ == "__main__":
    main()
