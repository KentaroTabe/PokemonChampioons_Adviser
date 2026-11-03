"""残件の小修正 (2026-10-23、計画書 §8.39・§8.42・§8.44 の残件) のテスト。

- 選出画面の選出提案の再計算間隔を config (SELECTION_RECALC_SEC、値は 15.0 のまま) に移した: server.py の構文木で、比較が
  config の名前を使い 15.0 のリテラルが残っていないことを確かめる (server は CI の最小依存では import できない)
- へんしん の残件 (a): 型推定のトラッカー (advisor.ev_infer.SpreadTracker) は、へんしん 中 (transform あり、または
  TRANSFORM_SPECIES の個体) の先後・ダメージの観測をメタモンの推定器に入れない (捨てる)。へんしん そのものを使ったターンの
  先後と、こだわりの固定 (持ち物は相手のまま) は入れる。へんしん しない相手の観測は従来どおり
- へんしん の残件 (b): 交代で transform が消えるとき、写しの技 (コピー元の技) を判明技から外し、へんしん 自体は残す
  (advisor.transform.strip_copied_moves と vision.state の reset_on_switch_out)

使用率 DB には依存しない (推定器は記録用の偽物に差し替える)。

    python -m tests.test_residuals_1023
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

from advisor import ev_infer as EI
from advisor import transform as TF
from advisor.damage import MonView
from champions_agent import config as C
from vision.state import BattleStateV2, PokemonState

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ 1. 選出の再計算間隔
def test_selection_recalc_config():
    """選出提案の再計算間隔は config.SELECTION_RECALC_SEC (値は従来の 15.0)。server.py は config から import して比較に使う"""
    assert float(C.SELECTION_RECALC_SEC) == 15.0, C.SELECTION_RECALC_SEC
    tree = ast.parse((REPO / "server.py").read_text(encoding="utf-8"))
    imported = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert ("champions_agent.config", "SELECTION_RECALC_SEC") in imported
    # `now - _last_advice_time > X` の比較を全部集める
    cmps = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.BinOp) and isinstance(n.left.right, ast.Name) \
                and n.left.right.id == "_last_advice_time":
            cmps.append(n.comparators[0])
    assert cmps, "選出提案の再計算の比較が見つからない"
    for c in cmps:
        assert isinstance(c, ast.Name) and c.id == "SELECTION_RECALC_SEC", ast.dump(c)
    print("test_selection_recalc_config OK")


# ------------------------------------------------------------------ 2. 型推定のトラッカー
_TF = {"source_species": "archaludon", "source_index": 0, "evidence": "event:ability_opponent_imposter", "t": 0.0,
       "copy": {"species": "archaludon", "types": ["はがね", "ドラゴン"], "ability": "sturdy", "boosts": {},
                "moves": [{"move_id": "steelbeam", "name_ja": "てっていこうせん", "pp": 5},
                          {"move_id": "thunderbolt", "name_ja": "10まんボルト", "pp": 5},
                          {"move_id": "darkpulse", "name_ja": "あくのはどう", "pp": 5},
                          {"move_id": "stealthrock", "name_ja": "ステルスロック", "pp": 5}],
                "base_stats": {}, "ev": None, "nature": None}}


def test_transform_masked_pure():
    ditto = {"species_id": "ditto"}
    tf_ditto = {"species_id": "ditto", "transform": _TF}
    chomp = {"species_id": "garchomp"}
    assert EI.transform_masked(ditto) and EI.transform_masked(tf_ditto)
    assert EI.transform_masked(ditto, "darkpulse") and EI.transform_masked(tf_ditto, "darkpulse")
    # へんしん そのもの (TRANSFORM_OWN_MOVES) を使ったターンの先後は相手自身の素早さ
    for mid in C.TRANSFORM_OWN_MOVES:
        assert not EI.transform_masked(ditto, mid) and not EI.transform_masked(tf_ditto, mid)
    assert not EI.transform_masked(chomp) and not EI.transform_masked(chomp, "earthquake")
    assert not EI.transform_masked(None)
    print("test_transform_masked_pure OK")


class _RecEst:
    def __init__(self, sid, log):
        self.sid, self.log = sid, log

    def observe_speed(self, *a, **k):
        self.log.append((self.sid, "speed"))

    def observe_damage(self, *a, **k):
        self.log.append((self.sid, "damage"))

    def observe_choice_lock(self):
        self.log.append((self.sid, "choice_lock"))


def _tracker():
    tr = EI.SpreadTracker()
    log = []
    tr.estimator = lambda sid: _RecEst(sid, log)
    me = {"species_id": "archaludon", "boosts": {}, "status": None}
    tr._my_view = lambda state: (MonView(species_id="archaludon", base={"spe": 85}), me)
    return tr, log


def _bstate(opp: dict, turn=3):
    return {"scene": "field", "turn": turn, "field": {}, "events": [],
            "player": {"active_index": 0, "party": [{"species_id": "archaludon", "boosts": {}, "status": None}]},
            "opponent": {"active_index": 0, "party": [dict({"boosts": {}, "status": None, "hp_percent": 100.0}, **opp)]}}


def _hp_drop(state, side):
    state["events"] = [{"source": "hp", "ts": time.time() + 0.01, "detail": {"side": side, "from": 100.0, "to": 60.0}}]


def test_tracker_skips_transformed_observations():
    """へんしん 中の先後・ダメージの観測はメタモンの推定器に入らない。入れるもの (へんしん したターンの先後・こだわりの固定・
    へんしん しない相手) は従来どおり"""
    # (1) 先後: 写した技を使ったターン (transform あり / 根拠がまだで transform なし) → 捨てる
    for opp in ({"species_id": "ditto", "transform": _TF}, {"species_id": "ditto"}):
        tr, log = _tracker()
        tr.on_frame(_bstate(opp), ["move_opponent_darkpulse", "move_player_thunderbolt"])
        assert log == [], (opp.get("transform") is not None, log)
    # (2) 先後: へんしん そのものを使ったターン → 相手自身の素早さとして入れる (発動と同じフレームで transform が付いていても)
    for opp in ({"species_id": "ditto"}, {"species_id": "ditto", "transform": _TF}):
        tr, log = _tracker()
        tr.on_frame(_bstate(opp), ["move_opponent_transform", "move_player_thunderbolt"])
        assert log == [("ditto", "speed")], log
    # (3) へんしん しない相手の先後は従来どおり
    tr, log = _tracker()
    tr.on_frame(_bstate({"species_id": "garchomp"}), ["move_opponent_dragonclaw", "move_player_thunderbolt"])
    assert log == [("garchomp", "speed")], log

    # (4) ダメージ (こちらが与えた): へんしん 中の防御・特防はコピー元のもの → 捨てる。へんしん しない相手は入れる
    for opp, want in (({"species_id": "ditto", "transform": _TF}, []), ({"species_id": "ditto"}, []),
                      ({"species_id": "garchomp"}, [("garchomp", "damage")])):
        tr, log = _tracker()
        st = _bstate(opp)
        tr.on_frame(st, ["move_player_thunderbolt"])
        _hp_drop(st, "opponent")
        tr.on_frame(st, [])
        assert log == want, (opp, log)
    # (5) ダメージ (相手が与えた): 写した技の攻撃・特攻はコピー元のもの → 捨てる。へんしん しない相手は入れる
    for opp, mid, want in (({"species_id": "ditto", "transform": _TF}, "darkpulse", []),
                           ({"species_id": "ditto"}, "darkpulse", []),
                           ({"species_id": "garchomp"}, "dragonclaw", [("garchomp", "damage")])):
        tr, log = _tracker()
        st = _bstate(opp)
        tr.on_frame(st, [f"move_opponent_{mid}"])
        _hp_drop(st, "player")
        tr.on_frame(st, [])
        assert log == want, (opp, log)
    # (6) こだわりの固定は持ち物 (相手のまま) の観測なので へんしん 中でも入れる
    tr, log = _tracker()
    st = _bstate({"species_id": "ditto", "transform": _TF})
    for turn in (1, 2, 3):
        st["turn"] = turn
        tr.on_frame(st, ["move_opponent_darkpulse"])
    assert ("ditto", "choice_lock") in log and all(k != "speed" and k != "damage" for _s, k in log), log
    print("test_tracker_skips_transformed_observations OK")


# ------------------------------------------------------------------ 3. 判明技の列
def test_strip_copied_moves_pure():
    rev = ["へんしん", "あくのはどう", "てっていこうせん", "ほかのわざ"]
    assert TF.strip_copied_moves(rev, _TF) == ["へんしん", "ほかのわざ"]
    assert TF.strip_copied_moves(rev, None) == rev and TF.strip_copied_moves(rev, {}) == rev
    # id の表記で入っていても外す。入力は書き換えない
    rev2 = ["へんしん", "darkpulse"]
    assert TF.strip_copied_moves(rev2, _TF) == ["へんしん"] and rev2 == ["へんしん", "darkpulse"]
    # コピー元が へんしん を持っていても、へんしん する種の自分の技は残す
    tf_own = {"copy": {"moves": [{"move_id": "transform", "name_ja": "へんしん"},
                                 {"move_id": "darkpulse", "name_ja": "あくのはどう"}]}}
    assert TF.strip_copied_moves(["へんしん", "あくのはどう"], tf_own) == ["へんしん"]
    print("test_strip_copied_moves_pure OK")


def test_switch_out_strips_copied_moves():
    """交代 (switch_to → reset_on_switch_out) で transform が消えるとき、写しの技を判明技から外し へんしん は残す。
    transform の無い個体の判明技は交代で変わらない"""
    s = BattleStateV2()
    op = PokemonState(species_ja="メタモン", species_id="ditto", types=["ノーマル"], hp_percent=70.0, is_active=True,
                      revealed_moves=["へんしん", "あくのはどう", "てっていこうせん"])
    op.transform = dict(_TF)
    other = PokemonState(species_ja="オオニューラ", species_id="sneasler", types=["かくとう", "どく"],
                         revealed_moves=["インファイト"])
    s.opponent.party, s.opponent.active_index = [op, other], 0
    s.opponent.switch_to(1)
    assert op.transform is None and op.revealed_moves == ["へんしん"], op.revealed_moves
    s.opponent.switch_to(0)
    assert other.revealed_moves == ["インファイト"], other.revealed_moves
    # transform が付かなかった (根拠なし) メタモンの判明技は従来どおり残す
    op2 = PokemonState(species_ja="メタモン", species_id="ditto", types=["ノーマル"], is_active=True,
                       revealed_moves=["あくのはどう"])
    s2 = BattleStateV2()
    s2.opponent.party, s2.opponent.active_index = [op2, PokemonState(species_id="sneasler")], 0
    s2.opponent.switch_to(1)
    assert op2.revealed_moves == ["あくのはどう"], op2.revealed_moves
    print("test_switch_out_strips_copied_moves OK")


if __name__ == "__main__":
    test_selection_recalc_config()
    test_transform_masked_pure()
    test_tracker_skips_transformed_observations()
    test_strip_copied_moves_pure()
    test_switch_out_strips_copied_moves()
    print("ALL OK")
