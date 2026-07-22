"""自分パーティの「対戦で使える個体」の判定。

チャンピオンズは6体から3体選出のため、交代候補・詰み筋・RL合法手は
選出済みの3体に限られる。選出画面の白リボン追跡 (is_picked) を第一情報、
対戦中に登場した形跡 (HP観測/アクティブ/選出順) を補完に使う。
"""
from __future__ import annotations

from typing import Optional


def battle_party_indices(my_state: dict) -> Optional[set]:
    """交代候補にしてよい自分のパーティindex集合。Noneなら制限なし。

    - 選出フラグが3体分あればそれで確定
    - 一部でも選出フラグがあれば「フラグ + 登場済み」に絞る
    - フラグ皆無でも登場済みが3体そろえばそれで確定
    - 情報不足 (対戦冒頭で選出画面を取り逃した等) は None = 従来どおり
      全員許可 (正しい交代先を隠すより未選出提案の可能性を許容する)
    """
    party = my_state.get("party") or []
    picked = {i for i, p in enumerate(party) if p.get("is_picked")}
    appeared = {i for i, p in enumerate(party)
                if p.get("hp_percent") is not None or p.get("is_active")
                or p.get("pick_order")}
    if len(picked) >= 3:
        return picked
    if picked:
        return picked | appeared
    if len(appeared) >= 3:
        return appeared
    return None


def is_switchable(my_state: dict, index: int) -> bool:
    """index のポケモンが交代候補として合法か (選出制限を考慮)"""
    allowed = battle_party_indices(my_state)
    return allowed is None or index in allowed
