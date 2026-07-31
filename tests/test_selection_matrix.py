"""選出の読み合い (利得行列の均衡解) のテスト。

    python -m tests.test_selection_matrix

- fictitious play が既知の解を持つゲームで正しい値に収束すること
- 利得行列の形状と、条件付きモデルが無いときのフォールバック
- 収集データの opp_sel 列 (相手の実選出) の記録形式
"""
from __future__ import annotations

import numpy as np


def test_solver_matching_pennies():
    """マッチングペニー: 均衡は両者 (0.5, 0.5)、ゲーム値 0.5 (勝率解釈)"""
    from champions_agent.agent.selection_model import solve_matrix_game
    M = np.array([[1.0, 0.0],
                  [0.0, 1.0]])
    p, q, v = solve_matrix_game(M)
    assert abs(v - 0.5) < 0.02, f"ゲーム値がずれている: {v}"
    assert abs(p[0] - 0.5) < 0.05, f"行戦略が偏っている: {p}"
    assert abs(q[0] - 0.5) < 0.05, f"列戦略が偏っている: {q}"
    print(f"test_solver_matching_pennies OK (v={v:.3f})")


def test_solver_dominant_strategy():
    """支配戦略があるゲーム: 行0が常に良い -> 行0に収束"""
    from champions_agent.agent.selection_model import solve_matrix_game
    M = np.array([[0.7, 0.6],
                  [0.4, 0.3]])
    p, q, v = solve_matrix_game(M)
    assert p[0] > 0.95, f"支配戦略を選べていない: {p}"
    # 列側は行0に対して悪い方 (0.6) を選ぶ
    assert abs(v - 0.6) < 0.02, f"ゲーム値がずれている: {v}"
    print(f"test_solver_dominant_strategy OK (v={v:.3f})")


def test_solver_beats_naive_argmax():
    """平均最大の選出が読まれると弱いケースで、均衡解が上回ること。

    行0: 平均は高い (0.55) が列1に刺されると0.15
    行1: 安定 (0.5/0.55)
    純粋均衡は無く、混合均衡の値は 0.5 + 0.45/17 ≈ 0.526。
    素朴な平均argmax (行0) は読まれると0.15しか取れない。
    """
    from champions_agent.agent.selection_model import solve_matrix_game
    M = np.array([[0.95, 0.15],
                  [0.50, 0.55]])
    naive = int(np.argmax(M.mean(axis=1)))
    assert naive == 0, "テストの前提が崩れている (平均argmaxが行0でない)"
    p, q, v = solve_matrix_game(M)
    worst_naive = float(M[naive].min())          # 0.15
    assert abs(v - 0.5265) < 0.01, f"均衡値が理論値とずれている: {v}"
    assert v > worst_naive + 0.3, \
        f"均衡 {v:.3f} が素朴なargmaxの最悪 {worst_naive:.3f} を上回らない"
    print(f"test_solver_beats_naive_argmax OK "
          f"(均衡{v:.3f} > argmax最悪{worst_naive:.3f})")


def test_payoff_matrix_requires_cond_model():
    """条件付きモデルが無いときは None (呼び出し側がフォールバックする)"""
    from pathlib import Path
    from champions_agent.agent.selection_model import (
        payoff_matrix, predict_maximin,
    )
    missing = Path("/nonexistent/selection_model_cond.pt")
    my6 = ["gengar", "garchomp", "primarina",
           "corviknight", "rotom", "hippowdon"]
    opp6 = ["dragonite", "mimikyu", "archaludon",
            "greninja", "delphox", "gyarados"]
    assert payoff_matrix(my6, opp6, missing) is None
    assert predict_maximin(my6, opp6, missing) is None
    print("test_payoff_matrix_requires_cond_model OK")


def test_payoff_matrix_shape_with_general_model():
    """行列の形状の検証 (モデルは汎用で代用。形状と値域だけを見る)。

    ⚠ 汎用モデルは相手6体で学習しており、3体への条件付けは外挿。
    このテストは配線の検証であって予測品質の検証ではない。
    """
    from champions_agent.agent.selection_model import (
        GENERAL_MODEL_PATH, load_model, payoff_matrix,
    )
    if load_model(GENERAL_MODEL_PATH) is None:
        print("test_payoff_matrix_shape SKIP (汎用モデルなし)")
        return
    my6 = ["gengar", "garchomp", "primarina",
           "corviknight", "rotom", "hippowdon"]
    opp6 = ["dragonite", "mimikyu", "archaludon",
            "greninja", "delphox", "gyarados"]
    made = payoff_matrix(my6, opp6, GENERAL_MODEL_PATH)
    assert made is not None
    M, my_perms, opp_combos = made
    assert M.shape == (120, 20), f"形状が想定外: {M.shape}"
    assert len(my_perms) == 120 and len(opp_combos) == 20
    assert 0.0 <= M.min() and M.max() <= 1.0, "勝率の値域を外れている"
    # 行列に条件付けの情報が入っている (全列が同一なら条件付けが死んでいる)
    assert float(np.ptp(M, axis=1).max()) > 1e-4, \
        "相手の選出を変えても予測が変わらない"
    print(f"test_payoff_matrix_shape OK (値域 {M.min():.2f}-{M.max():.2f})")


def test_opp_sel_recording_format():
    """_opp_selected: revealed な3体だけを拾い、長さ3にパディングする"""
    from tools.collect_selection_data import _opp_selected

    class _P:
        def __init__(self, species, revealed):
            self.species = species
            self.revealed = revealed

    class _B:
        opponent_team = {
            "p1": _P("gengar", True),
            "p2": _P("garchomp", False),
            "p3": _P("primarina", True),
            "p4": _P("rotom", False),
            "p5": _P("hippowdon", False),
            "p6": _P("corviknight", True),
        }

    sel = _opp_selected(_B())
    assert sel == ["gengar", "primarina", "corviknight"], sel

    class _B2:
        opponent_team = {"p1": _P("gengar", True)}

    sel2 = _opp_selected(_B2())
    assert sel2 == ["gengar", "", ""], sel2
    print("test_opp_sel_recording_format OK")


if __name__ == "__main__":
    test_solver_matching_pennies()
    test_solver_dominant_strategy()
    test_solver_beats_naive_argmax()
    test_payoff_matrix_requires_cond_model()
    test_payoff_matrix_shape_with_general_model()
    test_opp_sel_recording_format()
    print("\nALL OK")
