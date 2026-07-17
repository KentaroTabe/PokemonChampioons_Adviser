"""
対戦後、config.DEFAULT_REGULATION.post_battle_edit_scope の範囲内で
パーティ(6体)を編集する方策。

編集可能範囲:
- "free": 6体全て自由に入れ替え可能
- "bench_only": ベンチ(選出しなかった3体)のみ入れ替え可能
- "none": 編集不可

現時点ではヒューリスティック(相手に見せたパーティ情報を踏まえた簡易評価)を実装し、
将来的に学習方策(TeamEditPolicyNet, train/選出同様の学習ループ)へ置き換える。
"""
from __future__ import annotations

from champions_agent.config import DEFAULT_REGULATION


def can_edit(index: int, selected_indices: list[int]) -> bool:
    """レギュレーションに従い、index番目のポケモンが編集可能かを判定する。"""
    scope = DEFAULT_REGULATION.post_battle_edit_scope
    if scope == "free":
        return True
    if scope == "none":
        return False
    if scope == "bench_only":
        return index not in selected_indices
    return False


def propose_team_edit(current_party: list[dict], selected_indices: list[int],
                       candidate_pool: list[dict]) -> list[dict]:
    """編集可能範囲内で、より使用率の高い/対戦相性の良いポケモンへの
    入れ替え候補を提案する(プロトタイプ: ヒューリスティック)。

    current_party: 現在の6体(各要素は {"species": str, ...})
    candidate_pool: 入れ替え候補プール(例: 自分の手持ち全体、育成済みの控えなど)
    戻り値: 編集後のパーティ(6体)
    """
    new_party = list(current_party)
    editable_slots = [i for i in range(len(current_party)) if can_edit(i, selected_indices)]

    if not editable_slots or not candidate_pool:
        return new_party

    # プロトタイプ: 編集可能スロットのうち先頭1つだけ、候補プール先頭と入れ替える例
    # (本格的な評価関数・学習方策は今後実装)
    slot = editable_slots[0]
    new_party[slot] = candidate_pool[0]
    return new_party
