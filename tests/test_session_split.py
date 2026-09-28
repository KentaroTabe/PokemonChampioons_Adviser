"""対戦セッションの分割・リセット (2026-08-11の連結事故の回帰テスト)。

    python -m tests.test_session_split

- reset_battle が世代番号 (battle_seq) を単調増加させること
- BattleLogger が battle_seq の変化のみでログを回転すること
- last_move の追跡 (アンコールの技固定解決の材料) と交代/ひんしでのクリア
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from battle_logger import BattleLogger
from vision.events import EventParser
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()


def test_reset_battle_increments_seq():
    state = BattleStateV2()
    assert state.battle_seq == 0
    state.last_rate = {"value": 1500, "ts": 1.0}
    state.turn = 7
    state.reset_battle()
    assert state.battle_seq == 1
    assert state.turn == 0
    assert state.last_rate == {"value": 1500, "ts": 1.0}  # レートは跨いで保持
    state.reset_battle()
    assert state.battle_seq == 2
    assert state.to_dict()["battle_seq"] == 2
    print("test_reset_battle_increments_seq OK")


def test_logger_rotates_on_seq_change():
    with tempfile.TemporaryDirectory() as td:
        log = BattleLogger(log_dir=Path(td))
        state = BattleStateV2()
        state.scene = "selection"

        # 対戦1: 選出→場。シーン変化で記録が書かれファイルが開く
        log.on_frame(state.to_dict(), [])
        state.scene = "field"
        log.on_frame(state.to_dict(), [])
        state.scene = "command"
        log.on_frame(state.to_dict(), [])
        first = log._file
        assert first is not None

        # 同一対戦内でシーンが揺れてもファイルは変わらない
        for sc in ("field", "selection", "field", "selection", "command"):
            state.scene = sc
            log.on_frame(state.to_dict(), [])
        assert log._file == first, "seq不変でのシーン揺れで回転してはいけない"

        # リセット (seq+1) で回転する
        state.reset_battle()
        state.scene = "selection"
        log.on_frame(state.to_dict(), [])
        state.scene = "field"
        log.on_frame(state.to_dict(), [])
        assert log._file != first, "battle_seqの変化で回転するべき"

        # 旧ファイルには outcome レコードが書かれている
        lines = [json.loads(l) for l in first.read_text().splitlines()]
        assert any(r.get("type") == "outcome" for r in lines), lines
    print("test_logger_rotates_on_seq_change OK")


def _parser_with_actives():
    state = BattleStateV2()
    state.player.party = [
        PokemonState(species_ja="ブリジュラス", species_id="duraludon"),
        PokemonState(species_ja="ライチュウ", species_id="raichu"),
    ]
    state.player.active_index = 0
    state.opponent.party = [
        PokemonState(species_ja="ガブリアス", species_id="garchomp"),
    ]
    state.opponent.active_index = 0
    return state, EventParser(state, resolver)


def test_last_move_tracking():
    state, p = _parser_with_actives()
    p.parse("ブリジュラスの りゅうのはどう!")
    assert state.last_move.get("player") == "dragonpulse", state.last_move
    p.parse("相手の ガブリアスの じしん!")
    assert state.last_move.get("opponent") == "earthquake", state.last_move

    # 自分側の交代で自分側のみクリア
    p.parse("ゆけっ! ライチュウ!")
    assert "player" not in state.last_move, state.last_move
    assert state.last_move.get("opponent") == "earthquake"
    print("test_last_move_tracking OK")


def test_needs_reset_after_outcome_without_turns():
    """確定選出画面に入ったときのリセット判定 (2026-09-29 第16回): 対戦は終わった (outcome / battle_ended) が
    command 画面を一度も取れず turn=0 のままの状態も「前の対戦が載っている」とみなす。
    処理率 9% でこの状態になり、選出でリセットされず outcome が残って次戦の助言が止まり、ログも 2 戦連結した"""
    s = BattleStateV2()
    assert not s.needs_reset_for_new_battle()            # 起動直後 (何も載っていない)
    s.turn = 3
    assert s.needs_reset_for_new_battle()
    s = BattleStateV2()
    s.battle_active = True
    assert s.needs_reset_for_new_battle()
    s = BattleStateV2()
    s.outcome, s.battle_active = "win", False             # 勝敗は取れたが turn=0 のまま
    assert s.needs_reset_for_new_battle()
    s = BattleStateV2()
    s.battle_ended = True
    assert s.needs_reset_for_new_battle()
    s.reset_battle()
    assert not s.needs_reset_for_new_battle() and s.battle_seq == 1
    print("test_needs_reset_after_outcome_without_turns OK")


def test_move_inferred_from_definite_self_boost():
    """技の使用文を取り逃して自分への確定的な能力変化の文だけ読めたとき、自分の技のうちその変化を起こす技が
    1 つなら使用と推定して last_move を埋める (2026-09-29 第16回: こだわりスカーフのサザンドラのりゅうせいぐんを落とし、
    ロックを知らずに別の技を推奨した)。能力変化は二重適用しない。候補が 2 つ / 相手由来がありうる単発 -1 は推定しない"""
    from vision.state import MoveSlot
    state, p = _parser_with_actives()
    me = state.player.party[0]
    me.moves = [MoveSlot(name_ja="あくのはどう", move_id="darkpulse"), MoveSlot(name_ja="りゅうせいぐん", move_id="dracometeor"),
                MoveSlot(name_ja="とんぼがえり", move_id="uturn"), MoveSlot(name_ja="かえんほうしゃ", move_id="flamethrower")]
    fired = p.parse("ブリジュラスの 特攻が がくっと下がった!")
    assert "boost_player_spa_-2" in fired and "move_player_dracometeor" in fired, fired
    assert state.last_move.get("player") == "dracometeor", state.last_move
    assert me.boosts["spa"] == -2, me.boosts
    # 2 能力の組 (インファイト) も推定できる
    state2, p2 = _parser_with_actives()
    state2.player.party[0].moves = [MoveSlot(name_ja="インファイト", move_id="closecombat"),
                                    MoveSlot(name_ja="かみなりパンチ", move_id="thunderpunch")]
    fired2 = p2.parse("ブリジュラスの 防御と 特防が 下がった!")
    assert "move_player_closecombat" in fired2 and state2.last_move.get("player") == "closecombat", fired2
    # 同じ変化を起こす技が 2 つ (りゅうせいぐん / オーバーヒート) なら推定しない
    state3, p3 = _parser_with_actives()
    state3.player.party[0].moves = [MoveSlot(name_ja="りゅうせいぐん", move_id="dracometeor"),
                                    MoveSlot(name_ja="オーバーヒート", move_id="overheat")]
    fired3 = p3.parse("ブリジュラスの 特攻が がくっと下がった!")
    assert "boost_player_spa_-2" in fired3 and not any(f.startswith("move_player_") for f in fired3), fired3
    # 単発の -1 (いかく等の相手由来がありうる) は推定しない
    state4, p4 = _parser_with_actives()
    state4.player.party[0].moves = [MoveSlot(name_ja="ばかぢから", move_id="superpower")]
    fired4 = p4.parse("ブリジュラスの 攻撃が 下がった!")
    assert "boost_player_atk_-1" in fired4 and not any(f.startswith("move_player_") for f in fired4), fired4
    # 技の使用文が同じ解析で読めていれば推定はしない (通常経路)
    state5, p5 = _parser_with_actives()
    state5.player.party[0].moves = [MoveSlot(name_ja="りゅうせいぐん", move_id="dracometeor")]
    fired5 = p5.parse("ブリジュラスの りゅうせいぐん!")
    assert fired5.count("move_player_dracometeor") == 1 and state5.last_move.get("player") == "dracometeor"
    print("test_move_inferred_from_definite_self_boost OK")


if __name__ == "__main__":
    test_reset_battle_increments_seq()
    test_logger_rotates_on_seq_change()
    test_last_move_tracking()
    test_needs_reset_after_outcome_without_turns()
    test_move_inferred_from_definite_self_boost()
    print("all OK")
