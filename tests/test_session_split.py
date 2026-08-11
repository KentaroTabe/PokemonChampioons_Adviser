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


if __name__ == "__main__":
    test_reset_battle_increments_seq()
    test_logger_rotates_on_seq_change()
    test_last_move_tracking()
    print("all OK")
