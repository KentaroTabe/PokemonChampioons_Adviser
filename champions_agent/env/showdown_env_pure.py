"""学習環境 (showdown_env) のうち、poke-env / gymnasium を import せずに使える純粋な関数。

showdown_env はモジュールの先頭で poke-env (SinglesEnv 等の基底クラス) と gymnasium を読むので、CI の最小依存
(requirements-ci.txt) では import できない (poke-env 0.10 は numpy>=2.0.2 を要求し、CI の numpy<2 と両立しない)。
ここに分けて CI で検証する (2026-10-06、tests/test_battle_prune)。showdown_env からも従来どおり同じ名前で参照できる。
"""
from __future__ import annotations


def is_ignorable_unknown_effect_warning(message: str, names=None) -> bool:
    """poke-env の "Unexpected effect 'X' received." のうち、X が既知のチャンピオンズ固有効果 (config) なら True。純粋"""
    from champions_agent.config import TRAIN_IGNORED_UNKNOWN_EFFECTS
    names = TRAIN_IGNORED_UNKNOWN_EFFECTS if names is None else names
    if not message.startswith("Unexpected effect '"):
        return False
    name = message[len("Unexpected effect '"):].split("'", 1)[0]
    return name in names


def prune_finished_battles(players, keep: int) -> int:
    """終了済みバトルを (新しい順に keep 件残して) 破棄する。戻り値は削除数。

    poke-env の Player._battles は reset_battles() (close時のみ呼ばれる) まで
    全対戦を保持し続け、Battleオブジェクト (ターンごとのイベント履歴を含む)
    がプロセスRSSを対戦数に比例して押し上げる (2026-08-19 実測:
    学習ワーカーが1スタイル実行内で数百MB単位の線形増加)。
    エピソード完結型の学習は過去バトルを参照しないため挙動には影響しない。

    ⚠ poke-env の勝敗カウンタ (n_won_battles 等) は _battles の走査で
    実装されているため、勝率をカウンタから読む評価系 (train/evaluate.py)
    にはこの関数を適用しないこと。
    """
    removed = 0
    for pl in players:
        if pl is None:
            continue
        battles = getattr(pl, "battles", None)
        if not battles:
            continue
        # dict は挿入順 = 対戦の時系列。終了済みの古い方から削除する
        finished = [tag for tag, b in list(battles.items())
                    if getattr(b, "finished", False)]
        drop = finished[:-keep] if keep > 0 else finished
        for tag in drop:
            battles.pop(tag, None)
            removed += 1
    return removed
