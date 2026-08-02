"""選出モデル v2特徴量 (型情報+メタ事前分布) のテスト。

    python -m tests.test_selection_v2

- 次元とv1互換部分の一致
- チーム特定 (種族構成→実セット) が機能すること
- 型フラグ (スカーフ/メガ石/先制技) が実際のセットと一致すること
- 相手のメタ事前分布が値を持つこと
"""
from __future__ import annotations

import numpy as np


def _sample_team_text():
    from champions_agent.env.ranked_teams import build_ranked_teams
    return build_ranked_teams(include_external=True)[0]


def test_dims_and_v1_prefix():
    from champions_agent.agent.selection_features_v2 import (
        FEATURE_DIM_V2, build_features_v2,
    )
    from champions_agent.agent.selection_model import (
        FEATURE_DIM, build_features,
    )
    from tools.evolve_teams import _team_species
    team = _team_species(_sample_team_text())
    opp = ["dragonite", "mimikyu", "archaludon",
           "greninja", "delphox", "gyarados"]
    perm = (0, 1, 2)
    v2 = build_features_v2(team, opp, perm)
    v1 = build_features(team, opp, perm)
    assert v2.shape == (FEATURE_DIM_V2,), v2.shape
    assert FEATURE_DIM_V2 == FEATURE_DIM + 8 * 4 + 5 * 2
    assert np.allclose(v2[:FEATURE_DIM], v1), "v1互換部分が一致しない"
    print(f"test_dims_and_v1_prefix OK ({FEATURE_DIM_V2}次元)")


def test_team_lookup_and_flags():
    from champions_agent.agent.selection_features_v2 import (
        _lookup_sets, _mega_stone_ids, _set_feats,
    )
    from tools.evolve_teams import _block_item, _team_species
    text = _sample_team_text()
    team = _team_species(text)
    sets = _lookup_sets(team)
    assert len(sets) == 6, f"チーム特定に失敗: {len(sets)}体"

    # 実セットの持ち物とフラグの整合を全スロットで確認
    import re

    def to_id(s):
        return re.sub(r"[^a-z0-9]", "", str(s).lower())

    checked = 0
    for block in text.strip().split("\n\n"):
        sid = to_id(block.split("\n")[0].split(" @ ")[0])
        item = to_id(_block_item(block) or "")
        f = _set_feats(sid, sets)
        assert f[0] == (1.0 if item == "choicescarf" else 0.0), sid
        assert f[3] == (1.0 if item in _mega_stone_ids() else 0.0), sid
        assert 0.0 < f[4] < 1.5, f"素早さ特徴が異常: {sid} {f[4]}"
        assert f[7] > 0, f"技数が0: {sid}"
        checked += 1
    assert checked == 6
    print("test_team_lookup_and_flags OK (6スロット整合)")


def test_unknown_team_graceful():
    from champions_agent.agent.selection_features_v2 import build_features_v2
    # プールに存在しない架空のチーム -> 型特徴はゼロで落ちない
    team = ["pikachu", "eevee", "snorlax", "lapras", "ditto", "mew"]
    opp = ["dragonite", "mimikyu", "archaludon",
           "greninja", "delphox", "gyarados"]
    v = build_features_v2(team, opp, (0, 1, 2))
    assert np.all(np.isfinite(v))
    print("test_unknown_team_graceful OK")


def test_opp_priors_nonzero():
    from champions_agent.agent.selection_features_v2 import _prior_feats
    # メタ上位のスカーフ率・素早さ配分はゼロでないはず
    known = 0
    for sp in ["garchomp", "greninja", "mimikyu", "dragonite"]:
        p = _prior_feats(sp)
        if p.sum() > 0:
            known += 1
    assert known >= 3, f"メタ事前分布が空: {known}/4"
    print(f"test_opp_priors_nonzero OK ({known}/4種族に分布あり)")


if __name__ == "__main__":
    test_dims_and_v1_prefix()
    test_team_lookup_and_flags()
    test_unknown_team_graceful()
    test_opp_priors_nonzero()
    print("\nALL OK")
