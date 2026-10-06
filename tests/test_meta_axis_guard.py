"""評価軸ドリフト検知のテスト (2026-08-19 ベンチ軸セット回転インシデント)。

    scripts/run_test.sh test_meta_axis_guard

meta_sets のセット回転 (build_meta) と凍結参照の定点逸脱 (track_progress) を
検知する純粋計算部分を検証する。DB・実対戦は使わない。
"""
from __future__ import annotations

from champions_agent.data.build_meta import count_substantive_changes
from tools.track_progress import deviation_sigma


def _sig(ability="levitate", item="leftovers", nature="bold",
         evs="32/0/32/0/2/0", moves=("a", "b", "c", "d")):
    return (ability, item, nature, evs, frozenset(moves))


def test_move_reorder_is_not_substantive():
    """技の並び順だけの違いは変化に数えない (8→9では並び替えのみの種も
    多数あり、実質変化127種と区別する必要があった)"""
    prev = {"x": _sig(moves=("a", "b", "c", "d"))}
    new = {"x": _sig(moves=("d", "c", "b", "a"))}
    assert count_substantive_changes(prev, new) == 0
    print("test_move_reorder_is_not_substantive OK")


def test_substantive_changes_counted():
    prev = {
        "item_change": _sig(item="choicescarf"),
        "move_change": _sig(moves=("a", "b", "c", "d")),
        "nature_change": _sig(nature="modest"),
        "same": _sig(),
        "prev_only": _sig(),
    }
    new = {
        "item_change": _sig(item="lifeorb"),
        "move_change": _sig(moves=("a", "b", "c", "e")),
        "nature_change": _sig(nature="rash"),
        "same": _sig(),
        "new_only": _sig(),
    }
    # 片側にしか居ない種は数えない (共通種のみ比較)
    assert count_substantive_changes(prev, new) == 3
    print("test_substantive_changes_counted OK")


def test_deviation_sigma_flags_axis_break():
    """実測の再現: 8/18→8/19 の best_model 0.5887→0.486 (各3,000戦) は
    3SE警告を大きく超える。通常の揺らぎ (±0.01) は超えない"""
    broken = deviation_sigma(0.5887, 0.486, 3000, 3000)
    assert broken > 3.0, broken          # 実測では約8SE
    normal = deviation_sigma(0.59, 0.60, 3000, 3000)
    assert normal < 3.0, normal
    # 退避条件: 分散ゼロでもクラッシュしない
    assert deviation_sigma(0.0, 0.0, 100, 100) == 0.0
    print("test_deviation_sigma_flags_axis_break OK")


def main() -> None:
    test_move_reorder_is_not_substantive()
    test_substantive_changes_counted()
    test_deviation_sigma_flags_axis_break()
    print("\nALL OK")


if __name__ == "__main__":
    main()
