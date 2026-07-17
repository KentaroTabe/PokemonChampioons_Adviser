"""
学習済みモデル(または現時点ではヒューリスティック)を使い、
盤面情報から推奨行動を返す推論API。

将来的には既存の server.py / battle_state.py と連携し、
OCRで取得した盤面(BattleState.to_dict())を入力として
「おすすめの選出」「おすすめの技/交代」「おすすめのパーティ編集」を返す。

性格(PlayStyle)切り替え:
- Advisor(play_style=...) でユーザーが希望するプレイスタイル('offense'/'cycle'/
  'stall'/'balance')を指定できる。戦闘中アドバイス(advise_battle_action)は
  対応する性格別モデル(checkpoints/battle_policy_{play_style}.zip)をロードする。
- 選出/パーティ編集についても、将来的に性格別の重み付け候補提示に拡張できるよう
  play_styleを保持しておく。

現時点ではモジュール単体でテストできるよう、シンプルな関数群+薄いクラスとして実装する。
"""
from __future__ import annotations

from champions_agent.agent.policy_selection import select_team
from champions_agent.agent.policy_teambuild import propose_team_edit
from champions_agent.config import DEFAULT_PLAY_STYLE, PLAY_STYLES


def advise_selection(own_party: list[dict], opponent_party: list[dict]) -> dict:
    """選出フェーズのアドバイスを返す。

    own_party: [{"species": str, "hp_percent": float, "status": str}, ...] (6件)
    opponent_party: [{"species": str|None}, ...] (6件)
    """
    indices = select_team(own_party, opponent_party)
    return {
        "selected_indices": list(indices),
        "selected_species": [own_party[i]["species"] for i in indices],
        "note": "学習済みモデルが無い場合はヒューリスティック(種族値合計順)で選出しています。",
    }


def advise_team_edit(current_party: list[dict], selected_indices: list[int],
                      candidate_pool: list[dict]) -> dict:
    """対戦後のパーティ編集アドバイスを返す。"""
    new_party = propose_team_edit(current_party, selected_indices, candidate_pool)
    return {
        "new_party_species": [p["species"] for p in new_party],
    }


class Advisor:
    """性格(PlayStyle)を指定して各種アドバイスを取得するための薄いラッパークラス。

    使い方:
        advisor = Advisor(play_style="offense")
        advisor.advise_battle_action(battle)  # poke-envのAbstractBattleを渡す
        advisor.set_play_style("stall")       # 途中で性格を切り替えることも可能
    """

    def __init__(self, play_style: str = DEFAULT_PLAY_STYLE):
        if play_style not in PLAY_STYLES:
            raise ValueError(f"未知のplay_styleです: {play_style} (候補: {list(PLAY_STYLES.keys())})")
        self._play_style = play_style
        self._battle_policy = None  # 遅延ロード

    @property
    def play_style(self) -> str:
        return self._play_style

    def set_play_style(self, play_style: str) -> None:
        """性格を切り替える。対応する戦闘方策モデルは次回利用時に再ロードされる。"""
        if play_style not in PLAY_STYLES:
            raise ValueError(f"未知のplay_styleです: {play_style} (候補: {list(PLAY_STYLES.keys())})")
        if play_style != self._play_style:
            self._play_style = play_style
            self._battle_policy = None  # 性格が変わったらモデルを破棄し再ロードさせる

    def _get_battle_policy(self):
        if self._battle_policy is None:
            from champions_agent.agent.policy_battle import BattlePolicy
            self._battle_policy = BattlePolicy(play_style=self._play_style)
        return self._battle_policy

    def advise_selection(self, own_party: list[dict], opponent_party: list[dict]) -> dict:
        result = advise_selection(own_party, opponent_party)
        result["play_style"] = self._play_style
        return result

    def advise_battle_action(self, battle) -> dict:
        """戦闘中のアドバイス。poke-envのAbstractBattleオブジェクトを受け取り、
        性格別モデル(BattlePolicy)から推奨行動(BattleOrder)を返す。

        battle_state.py 由来のOCR辞書は現状poke-env形式へ未接続のため、
        本メソッドは poke-env の AbstractBattle を直接受け取ることを想定している。
        """
        policy = self._get_battle_policy()
        order = policy.choose_order(battle)
        return {
            "play_style": self._play_style,
            "order": str(order),
            "note": "学習済みモデルが無い場合はランダム合法行動にフォールバックしています。",
        }

    def advise_team_edit(self, current_party: list[dict], selected_indices: list[int],
                          candidate_pool: list[dict]) -> dict:
        result = advise_team_edit(current_party, selected_indices, candidate_pool)
        result["play_style"] = self._play_style
        return result


def advise_battle_action(battle_state_dict: dict) -> dict:
    """戦闘中のアドバイス(将来: server.py の battle_state と連携)。

    現時点ではプレースホルダ。poke-envのAbstractBattleを直接扱わないため、
    実際のゲーム画面OCR結果(battle_state.py の BattleState.to_dict())を
    どうpoke-env形式に変換するかは今後のインテグレーション課題。
    poke-envのAbstractBattleを直接扱える場合は Advisor.advise_battle_action を使うこと。
    """
    return {
        "note": "戦闘中アドバイスは今後実装予定。poke-envのAbstractBattleがあれば"
                "Advisor(play_style=...).advise_battle_action(battle) を利用してください。",
    }


if __name__ == "__main__":
    # 簡易動作確認用のダミー入力
    dummy_own = [{"species": "landorus-therian", "hp_percent": 1.0, "status": "none"}] * 6
    dummy_opp = [{"species": None}] * 6

    for style in PLAY_STYLES:
        advisor = Advisor(play_style=style)
        print(advisor.advise_selection(dummy_own, dummy_opp))
