"""対戦終了の追加シグナル (2026-09-16 第15回接続テスト後)。

- 3体目のひんし → battle_end_faint (見込み) → 猶予後に battle_end_faint_confirmed (勝敗つき)。猶予内の交代で取り消し
- リザルト画面のシーン分類 → battle_end_result
- 勝負文言・ランク画面でも battle_ended が立つ (終了後の助言抑止に使う)
- 設置技の展開文「相手の足下にねばねばネットが広がった」は技の使用ではなく、相手側への設置
- battle_logger は新しい終了イベントでも勝敗レコードを書く

    python -m tests.test_battle_end_signals
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from champions_agent.config import BATTLE_END_FAINT_CONFIRM_SEC
from vision.events import EventParser
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()
MY = [("ブリジュラス", "archaludon"), ("ライチュウ", "raichu"), ("ミミッキュ", "mimikyu")]
OPP = [("リザードン", "charizard"), ("ガブリアス", "garchomp"), ("カイリュー", "dragonite")]


def _state(player=MY, opp=OPP):
    st = BattleStateV2()
    st.player.party = [PokemonState(species_ja=j, species_id=i) for j, i in player]
    st.opponent.party = [PokemonState(species_ja=j, species_id=i) for j, i in opp]
    st.player.active_index = 0
    st.opponent.active_index = 0
    st.battle_active = True
    return st, EventParser(st, resolver)


def _faint(p: EventParser, text: str) -> list:
    """ひんし文を解析し、イベント ID の重複除外窓 (EVENT_DEDUP_SEC) を進める (実戦のひんしは 10 秒以上離れる)"""
    fired = p.parse(text)
    for k in list(p._recent_fired):
        p._recent_fired[k] -= 60.0
    return fired


def test_third_faint_hint_then_confirm_loss():
    st, p = _state()
    assert "battle_end_faint" not in _faint(p, "ブリジュラスは たおれた!")
    assert "battle_end_faint" not in _faint(p, "ライチュウは たおれた!")
    fired = _faint(p, "ミミッキュは たおれた!")
    assert "faint" in fired and "battle_end_faint" in fired, fired
    assert st.end_hint and st.end_hint["side"] == "player" and st.end_hint["fainted"] == 3
    assert st.battle_active and not st.battle_ended and st.outcome is None   # 見込みの段階では終わらない
    assert p.confirm_end_hint() is None                                      # 猶予内
    st.end_hint["ts"] -= BATTLE_END_FAINT_CONFIRM_SEC + 1
    assert p.confirm_end_hint() == "battle_end_faint_confirmed"
    assert st.outcome == "loss" and st.battle_ended and not st.battle_active and st.end_hint is None
    assert p.confirm_end_hint() is None
    assert any(e.get("event") == "battle_end_faint_confirmed" for e in st.events)
    print("test_third_faint_hint_then_confirm_loss OK")


def test_opponent_third_faint_is_win():
    st, p = _state()
    for name in ("リザードン", "ガブリアス"):
        _faint(p, f"相手の {name}は たおれた!")
    fired = _faint(p, "相手の カイリューは たおれた!")
    assert "battle_end_faint" in fired, fired
    assert st.end_hint["side"] == "opponent"
    st.end_hint["ts"] -= BATTLE_END_FAINT_CONFIRM_SEC + 1
    assert p.confirm_end_hint() == "battle_end_faint_confirmed"
    assert st.outcome == "win" and st.battle_ended
    print("test_opponent_third_faint_is_win OK")


def test_switch_within_grace_cancels_hint():
    """ひんしの帰属誤り: 見込みの後にその陣営の交代を観測したら取り消す (対戦は続く)"""
    st, p = _state()
    for name in ("リザードン", "ガブリアス", "カイリュー"):
        _faint(p, f"相手の {name}は たおれた!")
    assert st.end_hint and st.end_hint["side"] == "opponent"
    fired = p.parse("トレーナーは ボーマンダを 繰り出した!")
    assert "switch_opponent" in fired, fired
    assert p.confirm_end_hint() == "battle_end_faint_cancel"
    assert st.end_hint is None and st.battle_active and not st.battle_ended and st.outcome is None
    print("test_switch_within_grace_cancels_hint OK")


def test_both_sides_fainted_ends_without_outcome():
    st, p = _state()
    for name in ("ブリジュラス", "ライチュウ", "ミミッキュ"):
        _faint(p, f"{name}は たおれた!")
    for name in ("リザードン", "ガブリアス", "カイリュー"):
        _faint(p, f"相手の {name}は たおれた!")
    assert st.end_hint["side"] == "player"        # 最初の見込みを保つ
    st.end_hint["ts"] -= BATTLE_END_FAINT_CONFIRM_SEC + 1
    assert p.confirm_end_hint() == "battle_end_faint_confirmed"
    assert st.outcome is None and st.battle_ended  # 勝敗はレート/文言に任せる
    print("test_both_sides_fainted_ends_without_outcome OK")


def test_result_scene_ends_battle():
    st, p = _state()
    assert p.end_by_result_scene() == "battle_end_result"
    assert st.battle_ended and not st.battle_active and st.outcome is None
    assert p.end_by_result_scene() is None            # 二重発火しない
    st2, p2 = _state()
    st2.battle_active = False
    assert p2.end_by_result_scene() is None           # 対戦中でなければ何もしない
    print("test_result_scene_ends_battle OK")


def test_message_and_rank_set_ended():
    st, p = _state()
    fired = p.parse("トレーナーとの 勝負に 負けた!")
    assert "battle_lose" in fired and st.outcome == "loss" and st.battle_ended, fired
    st2, p2 = _state()
    fired = p2.parse("ランクIV レート1602")
    assert fired == ["battle_end_rank"] and st2.battle_ended and not st2.battle_active
    print("test_message_and_rank_set_ended OK")


def test_web_spread_message_is_hazard_not_move():
    """「相手の足下にねばねばネットが広がった」= 自分が相手側に設置。技の使用文ではない"""
    st, p = _state()
    fired = p.parse("相手の 足下に ねばねばネットが 広がった!")
    assert "stickyweb_set" in fired, fired
    assert not any(f.startswith("move_") for f in fired), fired
    assert st.opponent.sticky_web is True and not st.player.sticky_web
    st2, p2 = _state()
    fired = p2.parse("味方の 周りに とがった岩が 漂い始めた!")
    assert "stealthrock_set" in fired and st2.player.stealth_rock is True, fired
    assert not any(f.startswith("move_") for f in fired), fired
    print("test_web_spread_message_is_hazard_not_move OK")


def test_logger_writes_outcome_on_new_end_events():
    from battle_logger import BattleLogger

    def party(n_fainted, n=3):
        return [{"status": "fainted" if i < n_fainted else None, "species_id": f"m{i}", "ja": f"モン{i}"}
                for i in range(n)]

    def frame(my_f, opp_f, outcome=None):
        return {"scene": "field", "battle_seq": 1, "turn": 4, "events": [], "outcome": outcome,
                "player": {"party": party(my_f)}, "opponent": {"party": party(opp_f)}}

    for end_event, my_f, opp_f, want in (("battle_end_faint_confirmed", 3, 0, "loss"),
                                         ("battle_end_result", 0, 3, "win")):
        log_dir = Path(tempfile.mkdtemp())
        lg = BattleLogger(log_dir=log_dir)
        lg.on_frame(frame(0, 0), [])
        lg.on_frame(frame(my_f, opp_f), [])
        lg.on_frame(frame(my_f, opp_f), [end_event])
        recs = []
        for f in sorted(log_dir.glob("*.jsonl")):
            recs += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        outs = [r for r in recs if r.get("type") == "outcome"]
        assert len(outs) == 1 and outs[0]["outcome"] == want and outs[0].get("inferred") is True, (end_event, outs)
    print("test_logger_writes_outcome_on_new_end_events OK")


if __name__ == "__main__":
    test_third_faint_hint_then_confirm_loss()
    test_opponent_third_faint_is_win()
    test_switch_within_grace_cancels_hint()
    test_both_sides_fainted_ends_without_outcome()
    test_result_scene_ends_battle()
    test_message_and_rank_set_ended()
    test_web_spread_message_is_hazard_not_move()
    test_logger_writes_outcome_on_new_end_events()
