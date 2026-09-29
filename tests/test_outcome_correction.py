"""勝敗の誤記録の再発防止 (2026-09-29 第17回 15:53 の対戦: 勝ったのに「負け」と記録) のテスト。

- 様子見画面の名前の誤読で生えた 7 体目 (HP 0) を 3 体目のひんしに数えない (state.fainted_count、events._check_all_fainted)
- ロスター判明済みなら様子見画面の左列のロスター外の名前で枠を増やさない (extractors._extract_watch_side_columns)
- 勝負の文言が先の記録と食い違えば訂正の行を足す (battle_logger)。読み手は最後の outcome 行 + 文言優先 (tools.battle_outcome)
- 連続する対戦のレート差の妥当性 (読み違い・勝敗との矛盾) を分析に出す (analyze_battles.rate_flags)

    scripts/run_test.sh test_outcome_correction
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np

from champions_agent.config import BATTLE_END_FAINT_CONFIRM_SEC, RATE_MAX_DELTA_PER_BATTLE
from vision.events import EventParser
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()
MY6 = [("ブリジュラス", "archaludon"), ("ガブリアス", "garchomp"), ("ミミロップ", "lopunny"),
       ("ペロリーム", "slurpuff"), ("イダイトウ", "basculegion"), ("アシレーヌ", "primarina")]


def _state():
    st = BattleStateV2()
    st.player.party = [PokemonState(species_ja=j, species_id=i) for j, i in MY6]
    st.opponent.party = [PokemonState(species_ja=j, species_id=i) for j, i in
                         (("ガブリアス", "garchomp"), ("ドドゲザン", "kingambit"), ("ゴリランダー", "rillaboom"))]
    st.player.active_index = 0
    st.opponent.active_index = 0
    st.battle_active = True
    return st, EventParser(st, resolver)


def _faint(p: EventParser, text: str) -> list:
    fired = p.parse(text)
    for k in list(p._recent_fired):
        p._recent_fired[k] -= 60.0
    return fired


def test_fainted_count_ignores_extra_slot_and_uses_picks():
    st, _ = _state()
    st.player.party[0].status = "fainted"
    st.player.party[5].status = "fainted"
    st.player.party.append(PokemonState(species_ja="フレフワン", species_id="aromatisse", status="fainted",
                                        hp_percent=0.0, is_picked=True))   # 誤読で生えた 7 体目
    assert st.player.fainted_count() == 2
    for i in (0, 1, 5):
        st.player.party[i].is_picked = True
    st.player.party[2].status = "fainted"   # 未選出の枠への誤帰属
    assert st.player.fainted_count() == 3 and st.player.fainted_count(picked_only=True) == 2
    st.player.party[1].is_picked = False    # 選出が 3 体分かっていなければロスター全体
    assert st.player.fainted_count(picked_only=True) == 3
    print("test_fainted_count_ignores_extra_slot_and_uses_picks OK")


def test_third_faint_hint_not_triggered_by_phantom_slot():
    st, p = _state()
    for i in (0, 1, 5):
        st.player.party[i].is_picked = True
    st.player.party.append(PokemonState(species_ja="フレフワン", species_id="aromatisse", status="fainted",
                                        hp_percent=0.0, is_picked=True))
    assert "battle_end_faint" not in _faint(p, "ブリジュラスは たおれた!")
    assert "battle_end_faint" not in _faint(p, "アシレーヌは たおれた!")     # 実際のひんし 2 + 幽霊 1 では終わらない
    assert st.end_hint is None and st.battle_active
    fired = _faint(p, "ガブリアスは たおれた!")
    assert "battle_end_faint" in fired, fired                            # 選出 3 体目で見込み
    st.end_hint["ts"] -= BATTLE_END_FAINT_CONFIRM_SEC + 1
    assert p.confirm_end_hint() == "battle_end_faint_confirmed" and st.outcome == "loss"
    print("test_third_faint_hint_not_triggered_by_phantom_slot OK")


def test_battle_text_overrides_earlier_outcome_in_state():
    st, p = _state()
    st.outcome = "loss"
    st.battle_active = False
    fired = p.parse("せぶぶとの勝負に勝った!")
    assert "battle_win" in fired and st.outcome == "win"
    print("test_battle_text_overrides_earlier_outcome_in_state OK")


def test_watch_column_ignores_unknown_name_when_roster_full():
    from vision import extractors, zones

    def run(n_known: int):
        st = BattleStateV2()
        st.player.party = [PokemonState(species_ja=j, species_id=i) for j, i in MY6[:n_known]]
        st.player.active_index = 0
        st.battle_active = True

        def fake_read(img, zone, **kw):
            if zone is zones.WATCH_MY[0]["name"]:
                return "フレフワン"          # ひんしの行の名前誤読 (ロスター外)
            if zone is zones.WATCH_MY[0]["hp"]:
                return "0/165"
            if zone is zones.WATCH_MY[1]["name"]:
                return "アシレーヌ"
            if zone is zones.WATCH_MY[1]["hp"]:
                return "80/187"
            return ""

        img = np.zeros((1080, 1920, 3), np.uint8)
        with mock.patch.object(extractors.ocr, "read_zone_text", side_effect=fake_read), \
                mock.patch.object(extractors, "_plausible_max_hp", return_value=True):
            extractors._extract_watch_side_columns(img, st, resolver)
        return st

    st = run(6)
    names = [p.species_ja for p in st.player.party]
    assert len(st.player.party) == 6 and "フレフワン" not in names, names
    assert any(e.get("event") == "watch_name_ignored" for e in st.events), [e.get("event") for e in st.events]
    assert st.player.fainted_count() == 0
    st2 = run(2)                                  # 選出画面を経ずに起動: ロスター未判明なら従来どおり登録する
    assert "フレフワン" in [p.species_ja for p in st2.player.party]
    print("test_watch_column_ignores_unknown_name_when_roster_full OK")


def test_logger_writes_correction_when_text_contradicts():
    from battle_logger import BattleLogger

    def party(n_fainted, n=3):
        return [{"status": "fainted" if i < n_fainted else None, "species_id": f"m{i}", "ja": f"モン{i}"}
                for i in range(n)]

    def frame(outcome=None):
        return {"scene": "field", "battle_seq": 1, "turn": 9, "events": [], "outcome": outcome,
                "player": {"party": party(2)}, "opponent": {"party": party(2)}}

    log_dir = Path(tempfile.mkdtemp())
    lg = BattleLogger(log_dir=log_dir)
    lg.on_frame(frame(), [])
    lg.on_frame(frame("loss"), ["battle_end_faint_confirmed"])   # 誤った確定
    lg.on_frame(frame("win"), ["battle_win"])                    # 勝負の文言 (state は win に上書き済み)
    lg.on_frame(frame("win"), ["battle_end_rank"])
    recs = []
    for f in sorted(log_dir.glob("*.jsonl")):
        recs += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    outs = [r for r in recs if r.get("type") == "outcome"]
    assert [o["outcome"] for o in outs] == ["loss", "win"], outs
    assert outs[1].get("corrected_from") == "loss" and outs[1].get("basis") == "battle_text", outs
    from tools.battle_outcome import outcome_from_records
    assert outcome_from_records(recs) == ("win", False, True)
    print("test_logger_writes_correction_when_text_contradicts OK")


def _write_log(records) -> Path:
    path = Path(tempfile.mkdtemp()) / "battle_20260929_155342.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path


def _records_loss_then_win_text():
    def mon(ja, sid, hp=None):
        return {"species": sid, "ja": ja, "types": [], "hp": hp, "status": None, "boosts": {}, "mega": None,
                "item": None, "ability": None, "moves": [], "revealed": [], "picked": True}
    st = {"scene": "command", "field": {}, "selection_picked": 3, "mega_used": {},
          "player": {"active": 0, "party": [mon("ガブリアス", "garchomp", 100.0)]},
          "opponent": {"active": 0, "party": [mon("ゴリランダー", "rillaboom", 37.0)]}}
    return [
        {"type": "session", "t": 0.0, "source": "organic"},
        {"type": "scene", "t": 1.0, "scene": "command", "turn": 9, "state": st},
        {"type": "outcome", "t": 411.6, "outcome": "loss"},                      # 3 体目のひんしからの誤った確定
        {"type": "scene", "t": 420.0, "scene": "command", "turn": 10, "state": st},
        {"type": "events", "t": 451.9, "scene": "field", "turn": 10, "fired": ["battle_win"], "texts": ["せぶぶとの勝負に勝った"]},
        {"type": "rate", "t": 459.3, "value": 1712.104},
        {"type": "events", "t": 459.3, "scene": "field", "turn": 10, "fired": ["battle_end_rank"], "texts": []},
    ]


def test_readers_prefer_battle_text_over_recorded_outcome():
    from tools.analyze_battles import _parse_battle
    from tools.party_improvements import parse_battle
    from tools.team_build.real_eval import read_battle_labels
    from tools.decision_audit import audit_battle
    path = _write_log(_records_loss_then_win_text())
    b = _parse_battle(str(path))
    assert b["outcome"] == "win" and b["corrected"] is True and b["inferred"] is False, b
    b2 = parse_battle(str(path))
    assert b2["outcome"] == "win" and b2.get("corrected") is True, b2
    assert read_battle_labels(path)["outcome"] == "win"
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert audit_battle(recs)["outcome"] == "win"
    # 文言が無ければ記録どおり
    path2 = _write_log([r for r in _records_loss_then_win_text() if "battle_win" not in (r.get("fired") or [])])
    assert _parse_battle(str(path2))["outcome"] == "loss" and _parse_battle(str(path2))["corrected"] is False
    # 書き手が訂正の行を足した新しいログ: 最後の行を採り、訂正ありと分かる
    recs3 = [r for r in _records_loss_then_win_text() if "battle_win" not in (r.get("fired") or [])]
    recs3.insert(4, {"type": "outcome", "t": 451.9, "outcome": "win", "corrected_from": "loss", "basis": "battle_text"})
    path3 = _write_log(recs3)
    b3 = _parse_battle(str(path3))
    assert b3["outcome"] == "win" and b3["corrected"] is True, b3
    assert parse_battle(str(path3))["outcome"] == "win" and parse_battle(str(path3))["corrected"] is True
    assert read_battle_labels(path3)["outcome"] == "win" and read_battle_labels(path3)["corrected"] is True
    print("test_readers_prefer_battle_text_over_recorded_outcome OK")


def test_text_after_rank_screen_belongs_to_next_battle():
    """2 戦連結ログ (第16回 01:55): 先の対戦の「負け」の記録 → ランク画面 → 後の対戦の「勝った」。後の文言で先の記録を
    反転させない (ランク画面を過ぎた後の文言は次の対戦のもの)"""
    from tools.battle_outcome import outcome_from_records
    from tools.analyze_battles import _parse_battle
    recs = [r for r in _records_loss_then_win_text() if "battle_win" not in (r.get("fired") or [])]
    recs.append({"type": "events", "t": 1077.5, "scene": "field", "turn": 2, "fired": ["battle_win"], "texts": ["勝負に勝った"]})
    recs.append({"type": "events", "t": 1086.4, "scene": "field", "turn": 2, "fired": ["battle_end_rank"], "texts": []})
    assert outcome_from_records(recs) == ("loss", False, False)
    assert _parse_battle(str(_write_log(recs)))["outcome"] == "loss"
    # 記録より前の文言は訂正の対象にしない (通常の順序: 文言 → 記録、同じ勝敗)
    recs2 = [{"type": "events", "t": 350.7, "scene": "field", "turn": 6, "fired": ["battle_win"], "texts": ["勝負に勝った"]},
             {"type": "outcome", "t": 350.7, "outcome": "win"},
             {"type": "events", "t": 360.7, "scene": "field", "turn": 6, "fired": ["battle_end_rank"], "texts": []}]
    assert outcome_from_records(recs2) == ("win", False, False)
    print("test_text_after_rank_screen_belongs_to_next_battle OK")


def test_rate_flags():
    from tools.analyze_battles import rate_flags
    battles = [{"file": "a", "rate": 1753.6, "outcome": "loss"},
               {"file": "b", "rate": 1715.6, "outcome": "loss"},   # −38: 1 戦の変動を超える (読み違いの疑い)
               {"file": "c", "rate": None, "outcome": "loss"},     # 観測なしは飛ばす
               {"file": "d", "rate": 1696.5, "outcome": "loss"},
               {"file": "e", "rate": 1712.1, "outcome": "loss"},   # +15.6 なのに負け: 矛盾
               {"file": "f", "rate": 1729.5, "outcome": "win"},
               {"file": "g", "rate": 1749.2, "outcome": "win"}]
    flags = rate_flags(battles)
    by = {r["file"]: r for r in flags}
    assert "a" not in by and by["b"]["delta"] == -38.0 and "読み違い" in by["b"]["flag"], flags
    assert by["d"]["flag"] is None and abs(by["d"]["delta"] - (-19.1)) < 0.05
    assert "矛盾" in by["e"]["flag"] and by["f"]["flag"] is None and by["g"]["flag"] is None
    assert RATE_MAX_DELTA_PER_BATTLE > 20   # 実測の 1 戦の変動 (20 弱) を誤検出しない
    print("test_rate_flags OK")


def main() -> None:
    test_fainted_count_ignores_extra_slot_and_uses_picks()
    test_third_faint_hint_not_triggered_by_phantom_slot()
    test_battle_text_overrides_earlier_outcome_in_state()
    test_watch_column_ignores_unknown_name_when_roster_full()
    test_logger_writes_correction_when_text_contradicts()
    test_readers_prefer_battle_text_over_recorded_outcome()
    test_text_after_rank_screen_belongs_to_next_battle()
    test_rate_flags()
    print("ALL OK")


if __name__ == "__main__":
    main()
