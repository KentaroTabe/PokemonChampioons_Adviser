"""相手プールの歴史的アンカー (P1) と週次アンカー保存 (P2) のテスト。

    python -m tests.test_anchor_pool

背景 (docs/AXIS_GAP_ANALYSIS.md): 相手プールが「性格ごと最新5件 ≒ 直近
2時間の自己コピー」しか持たず、古い戦略族への対応を忘れて 8/2 の _best に
頭打ち 0.405 で負けていた。_best 3本+週次スナップショットをアンカーとして
抽選に混ぜる。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from champions_agent.train.opponent_pool import (
    EPSILON_ANCHOR, EPSILON_HEURISTIC, EPSILON_RANDOM, EPSILON_SEARCH,
    MAX_ANCHORS_PER_STYLE, anchor_paths, draw_opponent_kind,
    save_weekly_anchors,
)


def test_draw_boundaries():
    """抽選の境界: heuristic / search / random / anchor / pool の順に並ぶ。

    アンカー帯の境界ロジックは epsilon_anchor を明示して検証する
    (既定値は P1 棄却で 0 になったが、機構自体は再導入に備えて残す)
    """
    t1 = EPSILON_HEURISTIC
    t2 = t1 + EPSILON_SEARCH
    t3 = t2 + EPSILON_RANDOM
    ea = 0.25
    t4 = t3 + ea
    eps = 1e-9
    cases = [
        (t1 - eps, "heuristic"), (t1 + eps, "search"),
        (t2 - eps, "search"), (t2 + eps, "random"),
        (t3 - eps, "random"), (t3 + eps, "anchor"),
        (t4 - eps, "anchor"), (t4 + eps, "pool"),
        (0.999, "pool"),
    ]
    for r, want in cases:
        got = draw_opponent_kind(r, has_pool=True, has_anchor=True,
                                 epsilon_anchor=ea)
        assert got == want, (r, got, want)
    print("test_draw_boundaries OK")


def test_draw_fallbacks():
    """アンカー無し→プールへ、プールも無し→random へ落ちる"""
    t3 = EPSILON_HEURISTIC + EPSILON_SEARCH + EPSILON_RANDOM
    r = t3 + 1e-6   # アンカー帯の乱数 (帯の検証のため明示指定)
    assert draw_opponent_kind(r, has_pool=True, has_anchor=False,
                              epsilon_anchor=0.25) == "pool"
    assert draw_opponent_kind(r, has_pool=False, has_anchor=False,
                              epsilon_anchor=0.25) == "random"
    assert draw_opponent_kind(0.999, has_pool=False, has_anchor=True) == "random"
    print("test_draw_fallbacks OK")


def test_anchor_share_matches_registered_verdict():
    """アンカー枠の既定値が判定記録と一致している (設定退行の防止)。

    2026-08-25 のP1事前登録判定で棄却 (+0.092 < ゲート+0.10) しアンカーを
    0 に戻した。次候補P4 (探索ε 0.20→0.30) も 2026-09-02 の事前登録判定で
    棄却 (18,000戦 0.5662、基準比 -0.040、軸補正後も +0.009 で効果なし) し、
    探索εは 0.20、selfplayプール枠は 0.45 に復帰した。
    値を変えるときは training_changes.json に事前登録した上で
    このテストを新しい意図値に更新すること (無断の値変更をここで検知する)
    """
    total = EPSILON_HEURISTIC + EPSILON_SEARCH + EPSILON_RANDOM + EPSILON_ANCHOR
    assert abs(EPSILON_ANCHOR - 0.0) < 1e-9, EPSILON_ANCHOR
    assert abs(EPSILON_SEARCH - 0.20) < 1e-9, EPSILON_SEARCH
    assert abs(total - 0.55) < 1e-9, f"selfplayプール枠が0.45でない (合計{total})"
    print("test_anchor_share_matches_registered_verdict OK")


def _make_models_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    for style in ("balance", "offense", "cycle"):
        (d / f"battle_policy_{style}.zip").write_bytes(b"current-" + style.encode())
        (d / f"battle_policy_{style}_best.zip").write_bytes(b"best-" + style.encode())
    return d


def test_anchor_paths_lists_best_and_snapshots():
    d = _make_models_dir()
    paths = anchor_paths(models_dir=d)
    assert len(paths) == 3, paths          # 導入直後は _best のみ
    save_weekly_anchors(today="20260818", models_dir=d)
    paths = anchor_paths(models_dir=d)
    assert len(paths) == 6, paths          # _best 3 + アンカー3
    print("test_anchor_paths_lists_best_and_snapshots OK")


def test_weekly_save_interval_and_retention():
    d = _make_models_dir()
    assert len(save_weekly_anchors(today="20260818", models_dir=d)) == 3
    # 同日・間隔内は保存しない
    assert save_weekly_anchors(today="20260818", models_dir=d) == []
    assert save_weekly_anchors(today="20260824", models_dir=d) == []   # 6日後
    # 7日後は保存する
    assert len(save_weekly_anchors(today="20260825", models_dir=d)) == 3
    # 保持数を超えたら古いものから消える
    for week in ("20260901", "20260908", "20260915", "20260922"):
        save_weekly_anchors(today=week, models_dir=d)
    per_style = sorted((d / "anchors").glob("balance_*.zip"))
    assert len(per_style) == MAX_ANCHORS_PER_STYLE, per_style
    assert per_style[0].name == "balance_20260901.zip", per_style
    print("test_weekly_save_interval_and_retention OK")


if __name__ == "__main__":
    test_draw_boundaries()
    test_draw_fallbacks()
    test_anchor_share_matches_registered_verdict()
    test_anchor_paths_lists_best_and_snapshots()
    test_weekly_save_interval_and_retention()
    print("\nALL OK")
