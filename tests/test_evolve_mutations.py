"""構築のセット内変異 (種族を保ったまま型を変える) のテスト。

    python -m tests.test_evolve_mutations

- 種族構成が変わらないこと
- アイテムクローズ (持ち物重複禁止) が守られること
- 技変異が1枠だけ変えること / 配分変異がEVと性格だけ変えること
- 固定枠 (Constraint) の型をいじらないこと

⚠ 候補はランキングチームのテキストからではなく使用率DBから引く。
チームテキストの「変種」は持ち物しか違わない (オープンデータに技・配分が
含まれず、メタ最頻セットで埋められるため)。
"""
from __future__ import annotations

import random


def _species(text):
    from tools.evolve_teams import _team_species
    return sorted(_team_species(text))


def _items(text):
    from tools.evolve_teams import _block_item, _to_id
    return [_to_id(_block_item(b) or "")
            for b in text.strip().split("\n\n")]


def _sample_team():
    from champions_agent.env.ranked_teams import build_ranked_teams
    teams = build_ranked_teams(include_external=True)
    assert len(teams) >= 200, f"チームプールが少なすぎる: {len(teams)}"
    return teams[0]


def test_usage_alternatives():
    from tools.evolve_teams import _team_species, _usage_alternatives
    alt = _usage_alternatives()
    assert len(alt) >= 50, f"使用率データの種族が少なすぎる: {len(alt)}"
    # 変異対象チームの種族に、現在の4技を超える技候補があること
    # (これが無いと技変異が常に不発 = チームテキスト由来と同じ轍を踏む)
    team = _sample_team()
    with_extra = 0
    for sid in _team_species(team):
        a = alt.get(sid)
        if a and len(a["moves"]) > 4:
            with_extra += 1
    assert with_extra >= 3, \
        f"5技以上の使用率データを持つ種族が少なすぎる: {with_extra}/6"
    print(f"test_usage_alternatives OK ({len(alt)}種族 / "
          f"技候補あり{with_extra}/6)")


def test_set_mutation_keeps_species():
    from tools.evolve_teams import mutate_set
    team = _sample_team()
    rng = random.Random(7)
    changed = 0
    for _ in range(60):
        out = mutate_set(team, rng)
        assert _species(out) == _species(team), "種族構成が変わった"
        blocks = out.strip().split("\n\n")
        assert len(blocks) == 6, f"ブロック数が変わった: {len(blocks)}"
        items = [i for i in _items(out) if i]
        assert len(items) == len(set(items)), f"持ち物が重複: {items}"
        if out != team:
            changed += 1
    # 不発時は別操作/別スロットへフォールバックするので、ほぼ毎回成立するはず
    assert changed >= 55, f"変異の不発が多すぎる: {changed}/60"
    print(f"test_set_mutation_keeps_species OK ({changed}/60 変異成立)")


def _classify(b_old: str, b_new: str) -> str:
    """変異ブロックの種類を判定する ("item"/"move"/"spread"/"other")"""
    from tools.evolve_teams import _block_moves
    h_old = b_old.strip().split("\n")[0]
    h_new = b_new.strip().split("\n")[0]
    if h_old != h_new:
        body_same = b_old.strip().split("\n")[1:] == \
            b_new.strip().split("\n")[1:]
        return "item" if body_same else "other"
    om, nm = _block_moves(b_old), _block_moves(b_new)
    n_move_diff = sum(1 for a, b in zip(om, nm) if a != b)
    rest_old = [l for l in b_old.strip().split("\n")
                if not l.startswith("- ")]
    rest_new = [l for l in b_new.strip().split("\n")
                if not l.startswith("- ")]
    if n_move_diff and rest_old == rest_new:
        return "move" if n_move_diff == 1 and len(om) == len(nm) else "other"
    if not n_move_diff and rest_old != rest_new:
        diff = [(a, b) for a, b in zip(rest_old, rest_new) if a != b]
        if all(a.startswith("EVs: ") or a.endswith(" Nature")
               for a, _ in diff):
            return "spread"
    return "other"


def test_operator_kinds():
    from tools.evolve_teams import mutate_set
    team = _sample_team()
    rng = random.Random(11)
    counts = {"item": 0, "move": 0, "spread": 0, "other": 0}
    for _ in range(90):
        out = mutate_set(team, rng)
        if out == team:
            continue
        for b_old, b_new in zip(team.strip().split("\n\n"),
                                out.strip().split("\n\n")):
            if b_old.strip() != b_new.strip():
                counts[_classify(b_old, b_new)] += 1
    assert counts["other"] == 0, f"想定外の変異がある: {counts}"
    for k in ("item", "move", "spread"):
        assert counts[k] >= 5, f"{k}変異がほとんど発生していない: {counts}"
    print(f"test_operator_kinds OK {counts}")


def test_constraint_locked_slot_untouched():
    from advisor.infer import species_ja_name
    from tools.evolve_teams import Constraint, _team_species, mutate_set
    team = _sample_team()
    lock_id = _team_species(team)[0]
    lock_ja = species_ja_name(lock_id)
    if not lock_ja:
        print("test_constraint_locked_slot_untouched SKIP (日本語名なし)")
        return
    cons = Constraint(team, [lock_ja], max_changes=2)
    rng = random.Random(3)
    locked_block = team.strip().split("\n\n")[0].strip()
    for _ in range(60):
        out = mutate_set(team, rng, cons)
        assert out.strip().split("\n\n")[0].strip() == locked_block, \
            "固定枠の型が変更された"
    print("test_constraint_locked_slot_untouched OK")


def test_mutate_any_dispatch():
    from tools.evolve_teams import _meta_pool_rows, mutate_any
    team = _sample_team()
    rows = _meta_pool_rows()
    rng = random.Random(5)
    # set_prob=1.0 -> 種族構成は必ず維持される (不発時のフォールバックを除き)
    kept = sum(1 for _ in range(30)
               if _species(mutate_any(team, rows, rng, None, 1.0))
               == _species(team))
    assert kept >= 25, f"set_prob=1.0で種族が変わりすぎ: 維持{kept}/30"
    # set_prob=0.0 -> 従来の種族入れ替えのみ
    swapped = sum(1 for _ in range(30)
                  if _species(mutate_any(team, rows, rng, None, 0.0))
                  != _species(team))
    assert swapped >= 25, f"set_prob=0.0で種族が変わらない: {swapped}/30"
    print(f"test_mutate_any_dispatch OK (維持{kept}/30, 入替{swapped}/30)")


if __name__ == "__main__":
    test_usage_alternatives()
    test_set_mutation_keeps_species()
    test_operator_kinds()
    test_constraint_locked_slot_untouched()
    test_mutate_any_dispatch()
    print("\nALL OK")
