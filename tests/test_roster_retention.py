"""相手ロスターの枠と観測情報を失わない (2026-10-09 ④) のテスト。

10/8 18:27 (logs/battles/battle_20261008_182620.jsonl L10〜L187、正解 debug_frames/frame_1791451695.png の右列 6 体):
交代の文言「ヤドキング」がカントー形に即確定され、タイプ {みず, エスパー} が一致する枠が無いので推定スコア最低の
ムクホークの枠を上書き → ガラル形への訂正後に link_active_to_party が [どく/エスパー] の未特定枠へ統合して 6 枠が 5 枠に →
手動確定「ガラルヤドキング」が「枠の番号が範囲外」で無視され、guess_confirm に出ていないムクホークとアシレーヌの枠が
ヤドキング (slowkinggalar) との mismatch として残った。

- match_slot: 根拠のある対応 (推定の重複 / タイプ一致 / 未特定枠) だけを使い、決まらなければ None
- 形態の曖昧さ: 同名の形態の候補を保持し、候補のどれかとタイプが一致する枠が 1 つだけならその枠・その形態に決める
- 対応待ち (pending): 満枠で決まらなければ枠を壊さず末尾に保持し、形態の訂正・手動確定で決まったら枠へ移す
- link_active_to_party: 両方に観測があれば統合しない、満枠でロスターの枠を pop しない、統合をイベントに残す
- 手動確定: 範囲外でも対応待ちの個体・同じ種の枠に入れる。形態違い (ヤドキング → ガラルヤドキング) は同じ個体
- 対戦ログ: guess_confirm の superseded / unresolved、roster_change の merged_from / merged_to / unresolved、
  manual_fix の applied / reason

    python -m tests.test_roster_retention
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import battle_logger as BL
from battle_logger import BattleLogger
from champions_agent.config import PARTY_SIZE
from vision.events import EventParser
from vision.extractors import link_active_to_party
from vision.normalize import NameResolver
from vision.state import (MANUAL_REASON_OUT_OF_RANGE, BattleStateV2, PokemonState, _same_family,
                          apply_manual_species, apply_manual_species_unplaced, same_name_forms, same_name_forms_of)

resolver = NameResolver()

# 10/8 18:26 の選出画面の相手 6 枠 (L10 + L26。枠 3 は後からムクホークと推定、枠 5 は未特定)
ROWS_1008 = [("マスカーニャ", "meowscarada", ["くさ", "あく"], 1.0),
             ("グライオン", "gliscor", ["じめん", "ひこう"], 1.0),
             ("ハラバリー", "bellibolt", ["でんき"], 0.561),
             ("ムクホーク", "staraptor", ["ノーマル", "ひこう"], 0.553),
             ("アシレーヌ", "primarina", ["みず", "フェアリー"], 0.973),
             (None, None, ["どく", "エスパー"], None)]


def _state_from(rows):
    st = BattleStateV2()
    for ja, sid, types, score in rows:
        m = PokemonState(types=list(types))
        if ja:
            m.merge_species(ja, sid, guess=score is not None, score=score)
        st.opponent.party.append(m)
    st.player.party = [PokemonState(species_ja="イエッサン", species_id="indeedee"),
                       PokemonState(species_ja="カメックス", species_id="blastoise")]
    st.player.active_index = 0
    return st


def _frame(lg, st, scene, fired=()):
    st.scene = scene
    lg.on_frame(st.to_dict(), list(fired))


def _records(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _exclusive(sid, move_id, min_pct=1.0):
    """判明技による形態の訂正 (advisor.sets.exclusive_form_for_move) の代わり: ヘドロばくだんはガラル形だけ"""
    return "slowkinggalar" if (sid == "slowking" and move_id == "sludgebomb") else None


# ------------------------------------------------------------------ 純粋関数
def test_same_name_forms():
    assert same_name_forms_of("slowking", {"slowking", "slowkinggalar", "slowbro"}, set()) == ["slowking", "slowkinggalar"]
    assert same_name_forms_of("slowking", {"slowking", "slowkinggalar"}, {"slowkinggalar"}) == ["slowking"]
    assert same_name_forms_of("slowkinggalar", {"slowking", "slowkinggalar"}, set()) == ["slowkinggalar"]
    assert same_name_forms_of(None, set(), set()) == []
    # 実際の図鑑: ヤドキングはカントー形とガラル形、ガブリアスは 1 つ
    assert same_name_forms("slowking") == ("slowking", "slowkinggalar"), same_name_forms("slowking")
    assert same_name_forms("garchomp") == ("garchomp",)
    assert _same_family("slowking", "slowkinggalar") and _same_family("charizard", "charizardmegay")
    assert not _same_family("mew", "mewtwo") and not _same_family("slowking", "slowbro")
    print("test_same_name_forms OK")


def test_match_slot_uses_only_grounded_rules():
    st = _state_from(ROWS_1008)
    side = st.opponent
    side.active_index = None
    W, G = {"みず", "エスパー"}, {"どく", "エスパー"}
    # 形態の候補のうちガラル形だけが枠 5 とタイプ一致 → 枠 5・ガラル形
    assert side.match_slot([("slowking", W), ("slowkinggalar", G)]) == (5, "slowkinggalar")
    # カントー形だけ (形態が 1 つ) → タイプ一致の枠が無い。推定スコア最低 (ムクホーク) にも先頭にもしない
    assert side.match_slot([("slowking", W)]) == (None, None)
    assert side.replacement_slot(W) is None
    # 候補ごとに別の枠が一致 → 2) では決めない (未特定枠も無いので None)
    side.party[3].types = ["みず", "エスパー"]
    side.party[3].clear_species_guess()
    assert side.match_slot([("slowking", W), ("slowkinggalar", G)]) == (None, None)
    # 未特定枠 (種もタイプも無い) は 4) で使う。形態の候補が複数なら形態は決めない
    side.party[0] = PokemonState()
    assert side.match_slot([("slowking", W), ("slowkinggalar", G)]) == (0, None)
    # タイプだけ分かっている枠は「未特定枠」ではない
    side.party[0] = PokemonState(types=["ドラゴン"])
    assert side.match_slot([("garchomp", {"ドラゴン", "じめん"})]) == (None, None)
    print("test_match_slot_uses_only_grounded_rules OK")


# ------------------------------------------------------------------ 10/8 の流れの再現
def test_reproduce_1008_slowking_keeps_six_slots():
    """L10 → L29 → L42 → L52 → L153 の流れ: 6 枠を保持、ヤドキングはガラル形で枠 5、手動確定は枠 5、HP・技は枠 5、
    枠 3 (ムクホークの推定) は残り、guess_confirm にムクホーク・アシレーヌの枠が slowkinggalar との mismatch で残らない"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _state_from(ROWS_1008)
        p = EventParser(st, resolver)
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.5)
        _frame(lg, st, "selection")
        f = lg._file
        # L28/L29: 交代の文言「ヤドキング」
        fired = p.parse("King05はヤドキングを繰り出した")
        assert "switch_opponent" in fired, fired
        opp = st.opponent
        assert len(opp.party) == PARTY_SIZE, [q.species_ja for q in opp.party]
        assert opp.active_index == 5 and opp.party[5].species_id == "slowkinggalar", opp.party[5]
        assert opp.party[5].types and set(opp.party[5].types) == {"どく", "エスパー"}
        assert opp.party[3].species_ja == "ムクホーク" and opp.party[3].species_guess   # 推定は残る
        _frame(lg, st, "field", fired)
        # L40/L41: HP と技 (ガラル形への訂正の経路も通す)
        opp.active().hp_percent = 54.0
        with mock.patch("advisor.sets.exclusive_form_for_move", side_effect=_exclusive):
            fired = p.parse("相手のヤドキングのヘドロばくだん")
        assert any(x.startswith("move_opponent_") for x in fired), fired
        link_active_to_party(st, "opponent")
        _frame(lg, st, "command", fired)
        assert len(opp.party) == PARTY_SIZE and opp.party[5].species_id == "slowkinggalar"
        # L52: 手動確定「ガラルヤドキング」(候補のプルダウン = species_id つき)。範囲内なら枠 5 の形態違い = 同じ個体
        res = apply_manual_species(opp.party, 5, "ガラルヤドキング", "slowkinggalar")
        assert res["index"] == 5 and not res["moved"], res
        # 表示とずれた範囲外の番号でも無視しない: 同じ種の枠 5 に入れる
        res = apply_manual_species(opp.party, 6, "ガラルヤドキング", "slowkinggalar")
        assert res["index"] is None and res["reason"] == MANUAL_REASON_OUT_OF_RANGE
        res = apply_manual_species_unplaced(opp.party, "ガラルヤドキング", "slowkinggalar")
        assert res["index"] == 5 and res["via"] == "species_match", res
        # HP・技の履歴は枠 5 にだけある
        assert opp.party[5].hp_percent == 54.0 and opp.party[5].revealed_moves, opp.party[5]
        assert all(q.hp_percent is None and not q.revealed_moves for i, q in enumerate(opp.party) if i != 5)
        # L136: ヤドキングひんし → L153: マリルリ (みず/フェアリー) は推定アシレーヌの枠 4 にタイプ一致で入る
        opp.party[5].status, opp.party[5].hp_percent = "fainted", 0.0
        fired = p.parse("King05はマリルリを繰り出した")
        assert "switch_opponent" in fired
        assert len(opp.party) == PARTY_SIZE and opp.active_index == 4 and opp.party[4].species_id == "azumarill"
        assert opp.party[3].species_ja == "ムクホーク" and opp.party[5].species_id == "slowkinggalar"
        opp.active().hp_percent = 100.0
        _frame(lg, st, "command", fired)
        _frame(lg, st, "field")
        # 形態違いの手動確定は表示名を保つ (集計で同じ個体が「ヤドキング」「ガラルヤドキング」の 2 種に見えない)
        assert opp.party[5].species_ja == "ヤドキング" and "ガラルヤドキング" in opp.party[5].aliases
        lg._finalize("win")
        recs = _records(f)
        gc = {(r["slot"], r["species"]): r for r in recs if r["type"] == "guess_confirm"}
        assert gc[(3, "staraptor")]["verdict"] == "unrevealed", gc[(3, "staraptor")]
        # アシレーヌの推定は誤りだった (実体はマリルリ、正解の画像の右列 5 体目)。比べる相手はマリルリで、ヤドキングではない
        assert gc[(4, "primarina")]["revealed"] == "azumarill" and gc[(4, "primarina")]["verdict"] == "mismatch"
        assert not any(r["revealed"] in ("slowking", "slowkinggalar") for r in gc.values()), gc
        assert len([k for k in gc if k[1] == "primarina"]) == 1, gc   # 枠の番号のずれで 2 行にならない
        rc = [r for r in recs if r["type"] == "roster_change"]
        assert [(r["slot"], r["from"], r["to"]) for r in rc] == [(4, "primarina", "azumarill")], rc
        # 相手の種の集計は 6 枠の中だけ (7 体目が無い): scene 行の相手の party は常に 6 以下、集計に出る種は 6 枠の種
        from tools.analyze_battles import _parse_battle
        from tools.party_improvements import parse_battle
        scenes = [r for r in recs if r["type"] == "scene"]
        assert max(len(r["state"]["opponent"]["party"]) for r in scenes) == PARTY_SIZE
        b1, b2 = parse_battle(str(f)), _parse_battle(str(f))
        # 推定 (guess) は数えないので、確定した種は場に出た 2 体だけ。7 体目・別名の重複は無い
        assert sorted(b1["opp_roster"]) == ["マリルリ", "ヤドキング"], b1["opp_roster"]
        assert sorted(b2["opp_species"]) == ["マリルリ", "ヤドキング"], b2["opp_species"]
        assert sorted(b1["opp_fielded"]) == ["マリルリ", "ヤドキング"], b1["opp_fielded"]
        op = [r for r in recs if r["type"] == "opp_picks"][-1]
        assert len(op["slots"]) == PARTY_SIZE and op["n_appeared"] == 2, op
        print("test_reproduce_1008_slowking_keeps_six_slots OK")
    finally:
        shutil.rmtree(tmp)


def _rows_pending():
    """枠 5 のタイプアイコンが誤読された (どく/フェアリー) 選出画面: ヤドキングのどの形態ともタイプが一致する枠が無い"""
    rows = list(ROWS_1008)
    rows[5] = (None, None, ["どく", "フェアリー"], None)
    return rows


def test_pending_keeps_slots_and_resolves_by_manual_fix():
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _state_from(_rows_pending())
        p = EventParser(st, resolver)
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.5)
        _frame(lg, st, "selection")
        f = lg._file
        fired = p.parse("King05はヤドキングを繰り出した")
        opp = st.opponent
        before = [(q.species_id, q.species_guess, list(q.types)) for q in opp.party[:PARTY_SIZE]]
        assert len(opp.party) == PARTY_SIZE + 1 and opp.active_index == PARTY_SIZE, [q.species_ja for q in opp.party]
        pm = opp.party[PARTY_SIZE]
        assert pm.pending and pm.species_candidates == ["slowking", "slowkinggalar"] and pm.t_first_seen
        _frame(lg, st, "field", fired)
        # 観測は対応待ちの個体に付く
        opp.active().hp_percent = 54.0
        with mock.patch("advisor.sets.exclusive_form_for_move", side_effect=_exclusive):
            fired = p.parse("相手のヤドキングのヘドロばくだん")
        assert pm.species_id == "slowkinggalar" and pm.species_candidates == [], pm   # 形態の訂正で候補が消える
        link_active_to_party(st, "opponent")   # 形態は決まったが、タイプが一致する枠はまだ無い
        _frame(lg, st, "command", fired)
        assert pm.pending and len(opp.party) == PARTY_SIZE + 1
        assert [(q.species_id, q.species_guess, list(q.types)) for q in opp.party[:PARTY_SIZE]] == before
        assert pm.hp_percent == 54.0 and pm.revealed_moves and opp.fainted_count() == 0
        # 枠 5 への手動確定 (範囲内) → 枠 5 の種が決まり、対応待ちの個体が同じ種の枠 5 に移る
        res = apply_manual_species(opp.party, 5, "ガラルヤドキング", "slowkinggalar")
        assert res["index"] == 5, res
        moved = st.resolve_pending("opponent")
        assert len(moved) == 1 and moved[0]["merged_from"] == PARTY_SIZE and moved[0]["merged_to"] == 5, moved
        assert len(opp.party) == PARTY_SIZE and opp.active_index == 5
        s5 = opp.party[5]
        assert s5.species_id == "slowkinggalar" and not s5.pending and s5.hp_percent == 54.0 and s5.revealed_moves
        assert all(q.hp_percent is None and not q.revealed_moves for i, q in enumerate(opp.party) if i != 5)
        assert opp.party[3].species_ja == "ムクホーク" and opp.party[4].species_ja == "アシレーヌ"
        _frame(lg, st, "command")
        lg._finalize("win")
        rc = [r for r in _records(f) if r["type"] == "roster_change"]
        assert len(rc) == 1 and rc[0]["slot"] == 5 and rc[0]["to"] == "slowkinggalar", rc
        assert rc[0]["merged_from"] == PARTY_SIZE and rc[0]["merged_to"] == 5 and rc[0]["merge_kind"] == "pending"
        assert rc[0]["moved"]["hp"] == 54.0 and rc[0]["moved"]["revealed_moves"], rc[0]
        assert not any(r.get("unresolved") for r in rc)
        print("test_pending_keeps_slots_and_resolves_by_manual_fix OK")
    finally:
        shutil.rmtree(tmp)


def test_pending_resolved_by_form_correction():
    """形態ごとに別の枠がタイプ一致 (カントー形 → 枠 3 [みず/エスパー]、ガラル形 → 枠 5 [どく/エスパー]) で決められない →
    対応待ち。判明技でガラル形に決まったら、そのタイプの枠 5 に HP・技ごと移る"""
    rows = list(ROWS_1008)
    rows[3] = (None, None, ["みず", "エスパー"], None)
    st = _state_from(rows)
    p = EventParser(st, resolver)
    p.parse("King05はヤドキングを繰り出した")
    opp = st.opponent
    assert len(opp.party) == PARTY_SIZE + 1 and opp.party[PARTY_SIZE].pending, [q.species_ja for q in opp.party]
    link_active_to_party(st, "opponent")
    assert len(opp.party) == PARTY_SIZE + 1   # 形態が決まるまで動かない
    opp.active().hp_percent = 54.0
    with mock.patch("advisor.sets.exclusive_form_for_move", side_effect=_exclusive):
        p.parse("相手のヤドキングのヘドロばくだん")
    link_active_to_party(st, "opponent")
    assert len(opp.party) == PARTY_SIZE and opp.active_index == 5, [q.species_ja for q in opp.party]
    s5 = opp.party[5]
    assert s5.species_id == "slowkinggalar" and s5.hp_percent == 54.0 and s5.revealed_moves and not s5.pending
    assert opp.party[3].species_ja is None and opp.party[3].hp_percent is None and not opp.party[3].revealed_moves
    ev = [e for e in st.events if e["event"] == "roster_pending_resolved"]
    assert len(ev) == 1 and ev[0]["detail"]["merged_to"] == 5 and ev[0]["detail"]["basis"] == "rule", ev
    # 統合先の枠にも観測があれば (別の個体かもしれない) 移さない
    st = _state_from(rows)
    p = EventParser(st, resolver)
    p.parse("King05はヤドキングを繰り出した")
    st.opponent.party[5].hp_percent = 80.0
    pm = st.opponent.active()
    pm.hp_percent, pm.species_id, pm.species_candidates = 54.0, "slowkinggalar", []
    assert st.resolve_pending("opponent") == [] and pm.pending and st.opponent.party[5].hp_percent == 80.0
    print("test_pending_resolved_by_form_correction OK")


def test_pending_unresolved_at_end_and_guess_verdicts():
    """対応が決まらないまま終わった: roster_change に unresolved: true、未判明の推定は unresolved (mismatch にしない)"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _state_from(_rows_pending())
        p = EventParser(st, resolver)
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.5)
        _frame(lg, st, "selection")
        f = lg._file
        fired = p.parse("King05はヤドキングを繰り出した")
        opp = st.opponent
        opp.active().hp_percent = 40.0
        _frame(lg, st, "field", fired)
        # 範囲外の手動確定は対応待ちの個体に入る (形態が決まる) が、タイプが一致する枠が無いので対応待ちのまま
        assert apply_manual_species(opp.party, 9, "ガラルヤドキング", "slowkinggalar")["index"] is None
        res = apply_manual_species_unplaced(opp.party, "ガラルヤドキング", "slowkinggalar")
        assert res["index"] == PARTY_SIZE and res["via"] == "pending", res
        assert st.resolve_pending("opponent") == []
        pm = opp.party[PARTY_SIZE]
        assert pm.pending and pm.species_id == "slowkinggalar" and pm.species_candidates == []
        # 別の種 (マスカーニャ) は推定の枠 0 に名前で当たる → match
        p.parse("King05はマスカーニャを繰り出した")
        assert opp.active_index == 0 and len(opp.party) == PARTY_SIZE + 1
        opp.active().hp_percent = 100.0
        _frame(lg, st, "command", ["switch_opponent"])
        lg._finalize("win")
        recs = _records(f)
        un = [r for r in recs if r["type"] == "roster_change" and r.get("unresolved")]
        assert len(un) == 1 and un[0]["to"] == "slowkinggalar" and un[0]["slot"] is None, un
        assert un[0]["t_first_seen"] == pm.t_first_seen and un[0]["moved"]["hp"] == 40.0
        gc = {r["species"]: r["verdict"] for r in recs if r["type"] == "guess_confirm"}
        assert gc["meowscarada"] == "match", gc
        assert gc["staraptor"] == gc["primarina"] == gc["bellibolt"] == BL.GUESS_UNRESOLVED, gc
        assert "mismatch" not in gc.values()
        print("test_pending_unresolved_at_end_and_guess_verdicts OK")
    finally:
        shutil.rmtree(tmp)


def test_pending_not_counted_as_seventh():
    """対応待ちの個体 (party の 7 番目) は、種・枠・選出・ひんし数・控えに数えない。場の個体としては助言が使う
    (roster_slots。ひんし数 / opp_picks の appeared / analyze_battles・party_improvements の集計 / real_opponents のバンク /
    助言エンジンの控えとひんしの数)"""
    from advisor.engine import _side_members, fainted_allies_of
    from tools.analyze_battles import _parse_battle
    from tools.party_improvements import parse_battle
    from tools.real_opponents import build_bank
    from vision.state import roster_slots
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _state_from(_rows_pending())
        p = EventParser(st, resolver)
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.5)
        _frame(lg, st, "selection")
        f = lg._file
        fired = p.parse("King05はヤドキングを繰り出した")
        opp = st.opponent
        opp.active().hp_percent = 30.0
        _frame(lg, st, "command", fired)
        # 助言エンジンの残存メンバーの候補は 6 枠だけ (対応待ちは 6 枠のどれかで、追加の個体ではない)
        d = st.to_dict()["opponent"]
        assert [i for i, _ in _side_members(d, "opponent")] == [0, 1, 2, 3, 4, 5]
        assert [i for i, _ in roster_slots(d["party"])] == list(range(PARTY_SIZE))
        # 対応待ちのひんし → ひんし数には入れない (誤読の 7 体目で終了と判定した 9/29 の事故と同じ形を避ける)
        p.parse("相手のヤドキングはたおれた")
        assert opp.party[PARTY_SIZE].status == "fainted" and opp.fainted_count() == 0
        assert fainted_allies_of(st.to_dict()["opponent"], 0) == 0
        # 控えの個体には入れない (場から下がった対応待ち)
        p.parse("King05はマスカーニャを繰り出した")
        assert opp.active_index == 0
        opp.active().hp_percent = 100.0
        d = st.to_dict()["opponent"]
        assert PARTY_SIZE not in [i for i, _ in _side_members(d, "opponent")]
        _frame(lg, st, "command", ["switch_opponent"])
        _frame(lg, st, "field")   # scene 行は場面が変わったときだけ書かれる
        lg._finalize("win")
        recs = _records(f)
        op = [r for r in recs if r["type"] == "opp_picks"][-1]
        assert op["n_appeared"] == 1 and [s["species"] for s in op["slots"] if s["appeared"]] == ["meowscarada"], op
        assert len(op["slots"]) == PARTY_SIZE
        b1, b2 = parse_battle(str(f)), _parse_battle(str(f))
        assert "ヤドキング" not in b1["opp_roster"] and "ヤドキング" not in b1["opp_fielded"], b1
        assert "ヤドキング" not in b2["opp_species"] and "ヤドキング" not in b2["opp_fielded"], b2
        bank = build_bank([b1], lambda ja: resolver.resolve_species(ja, cutoff=0.9)[1], min_roster=1)
        assert set(bank["species"]) == {"meowscarada"}, bank["species"].keys()
        # 対応待ちのまま終わった個体は roster_change の unresolved 行で別に残る
        assert [r["to"] for r in recs if r["type"] == "roster_change" and r.get("unresolved")] == ["slowking"]
        print("test_pending_not_counted_as_seventh OK")
    finally:
        shutil.rmtree(tmp)


MY_DURALUDON = {"species_id": "duraludon", "species_ja": "ブリジュラス", "types": ["ドラゴン", "はがね"],
                "hp_percent": 100.0, "hp_current": 197, "hp_max": 197, "status": None, "boosts": {},
                "ability_id": "stamina", "item_id": "leftovers", "is_picked": True, "revealed_moves": [],
                "moves": [{"name_ja": "りゅうのはどう", "move_id": "dragonpulse", "pp": 12, "max_pp": 12},
                          {"name_ja": "ラスターカノン", "move_id": "flashcannon", "pp": 16, "max_pp": 16},
                          {"name_ja": "はどうだん", "move_id": "aurasphere", "pp": 20, "max_pp": 20},
                          {"name_ja": "まもる", "move_id": "protect", "pp": 8, "max_pp": 8}]}


def _advice_state(st):
    d = st.to_dict()
    d["player"]["party"] = [dict(MY_DURALUDON)]
    d["player"]["active_index"] = 0
    return d


def test_engine_holds_roster_evaluation_while_opp_pending():
    """相手の場の個体が対応待ちの間: (a) 終盤評価・探索は保留 (7 体を列挙しない)、opp_pending を助言に残す。
    (b) 場の個体への直接の採点は対応待ちの個体の観測 (HP) を使う。(c) 対応が決まったら従来どおり"""
    from advisor import engine
    st = _state_from(_rows_pending())
    p = EventParser(st, resolver)
    p.parse("King05はヤドキングを繰り出した")
    opp = st.opponent
    pm = opp.active()
    pm.hp_percent, pm.species_id, pm.species_candidates = 30.0, "slowkinggalar", []
    d = _advice_state(st)
    assert engine.opp_active_pending(d["opponent"]) is True
    assert engine._run_endgame(d["player"], d["opponent"], None) == ""
    adv = engine.evaluate(d)
    assert adv["ok"] and adv["opp_pending"] is True and adv["opp_pending_note"], adv.get("opp_pending")
    assert adv["endgame_note"] == "" and adv["gtheory"] is None
    common = engine.evaluate_common(d, None)
    assert common["context"]["opp_hp_pct"] == 30.0, common["context"]     # (b) 対応待ちの個体の HP で採点
    assert common["endgame"] == "" and common["opp_pending"] is True
    # (c) 手動確定で枠 5 に対応 → 対応待ちが解け、保留しない
    assert apply_manual_species(opp.party, 5, "ガラルヤドキング", "slowkinggalar")["index"] == 5
    assert len(st.resolve_pending("opponent")) == 1 and len(opp.party) == PARTY_SIZE
    d2 = _advice_state(st)
    assert engine.opp_active_pending(d2["opponent"]) is False
    adv2 = engine.evaluate(d2)
    assert adv2["ok"] and adv2["opp_pending"] is False and adv2["opp_pending_note"] is None
    assert engine.evaluate_common(d2, None)["context"]["opp_hp_pct"] == 30.0
    assert adv2["gtheory"] is not None, "対応が決まったら探索は従来どおり走る"
    print("test_engine_holds_roster_evaluation_while_opp_pending OK")


def test_pending_faint_moves_to_slot_once():
    """対応待ちでひんし → ひんし数 (終了判定) には数えない → 対応が決まった時点で枠に 1 回だけ反映し、二重に数えない"""
    st = _state_from(_rows_pending())
    p = EventParser(st, resolver)
    p.parse("King05はヤドキングを繰り出した")
    opp = st.opponent
    p.parse("相手のヤドキングはたおれた")
    assert opp.party[PARTY_SIZE].status == "fainted" and opp.fainted_count() == 0
    assert apply_manual_species(opp.party, 5, "ガラルヤドキング", "slowkinggalar")["index"] == 5
    moved = st.resolve_pending("opponent")
    assert len(moved) == 1 and moved[0]["moved"]["status"] == "fainted"
    assert len(opp.party) == PARTY_SIZE and opp.party[5].status == "fainted" and opp.party[5].hp_percent == 0.0
    assert opp.fainted_count() == 1
    assert st.resolve_pending("opponent") == [] and opp.fainted_count() == 1          # 二重に数えない
    print("test_pending_faint_moves_to_slot_once OK")


def test_final_guess_verdict_pure():
    assert BL.final_guess_verdict("x", "match", True, True) == ("x", "match")
    assert BL.final_guess_verdict("y", "mismatch", True, False) == (None, "superseded")
    assert BL.final_guess_verdict(None, "unrevealed", False, True) == (None, "unresolved")
    assert BL.final_guess_verdict(None, "unrevealed", False, False) == (None, "unrevealed")
    assert BL.final_guess_verdict("y", "mismatch", False, True) == ("y", "mismatch")
    assert BL.shift_slot_keys({0: "a", 2: "b", 3: "c"}, 2) == {0: "a", 2: "c"}
    print("test_final_guess_verdict_pure OK")


# ------------------------------------------------------------------ link_active_to_party の統合
def test_link_merge_keeps_observations_and_slots():
    # (a) 4 枠 (選出画面で 2 枠読めなかった) + 場の個体 (末尾、HP と技あり) → 形態の候補のうちガラル形とタイプが一致する
    #     未特定の [どく/エスパー] 枠 (観測なし) に統合して pop (6 枠未満は従来どおり)、統合をイベントに残す
    st = _state_from(ROWS_1008[:3] + [(None, None, ["どく", "エスパー"], None)])
    opp = st.opponent
    ph = PokemonState(species_ja="ヤドキング", species_id="slowking",
                      species_candidates=["slowking", "slowkinggalar"], hp_percent=54.0,
                      revealed_moves=["ヘドロばくだん"], is_active=True)
    opp.party.append(ph)
    opp.active_index = 4
    link_active_to_party(st, "opponent")
    assert len(opp.party) == 4 and opp.active_index == 3, [q.species_ja for q in opp.party]
    assert opp.party[3].species_id == "slowkinggalar" and opp.party[3].hp_percent == 54.0
    assert opp.party[3].revealed_moves == ["ヘドロばくだん"] and opp.party[3].species_candidates == []
    ev = [e for e in st.events if e["event"] == "roster_merge"]
    assert len(ev) == 1 and ev[0]["detail"]["merged_from"] == 4 and ev[0]["detail"]["merged_to"] == 3, ev
    assert ev[0]["detail"]["moved"]["hp"] == 54.0 and ev[0]["detail"]["source_cleared"] is False
    # (a') 5 枠 + 場の個体 = 6: 統合元の 6 枠目は pop せず未特定の枠に戻す (相手は 6 体。次の初登場の受け皿になる)
    st = _state_from(ROWS_1008[:5] + [(None, None, ["どく", "エスパー"], None)])
    opp = st.opponent
    opp.party.pop(3)
    opp.party.append(PokemonState(species_ja="ヤドキング", species_id="slowkinggalar", hp_percent=54.0,
                                  is_active=True))
    opp.active_index = 5
    link_active_to_party(st, "opponent")
    assert len(opp.party) == PARTY_SIZE and opp.active_index == 4 and opp.party[4].hp_percent == 54.0
    assert opp.party[5].species_ja is None and not opp.party[5].types
    # (b) 統合先にも観測がある (別の種の判断が入る余地) → 統合しない
    st = _state_from(ROWS_1008)
    opp = st.opponent
    opp.party.pop(3)
    opp.party[4].hp_percent = 80.0            # 未特定の [どく/エスパー] 枠に HP の観測
    ph = PokemonState(species_ja="ヤドキング", species_id="slowkinggalar", hp_percent=54.0, is_active=True)
    opp.party.append(ph)
    opp.active_index = 5
    link_active_to_party(st, "opponent")
    assert len(opp.party) == 6 and opp.party[4].hp_percent == 80.0 and opp.party[5].hp_percent == 54.0
    assert [e["event"] for e in st.events].count("roster_merge_skip") == 1
    link_active_to_party(st, "opponent")      # 毎フレーム呼ばれても記録は 1 回
    assert [e["event"] for e in st.events].count("roster_merge_skip") == 1
    # (c) 満枠でロスターの枠が統合元 → pop せず未特定の枠に戻す (6 枠を保つ)
    st = _state_from(ROWS_1008)
    opp = st.opponent
    opp.party[2] = PokemonState(species_ja="ヤドキング", species_id="slowkinggalar", hp_percent=54.0, is_active=True)
    opp.active_index = 2
    link_active_to_party(st, "opponent")
    assert len(opp.party) == PARTY_SIZE and opp.active_index == 5, [q.species_ja for q in opp.party]
    assert opp.party[5].species_id == "slowkinggalar" and opp.party[5].hp_percent == 54.0
    assert opp.party[2].species_ja is None and opp.party[2].hp_percent is None
    ev = [e for e in st.events if e["event"] == "roster_merge"][-1]
    assert ev["detail"]["source_cleared"] is True and ev["detail"]["merged_from"] == 2
    print("test_link_merge_keeps_observations_and_slots OK")


def test_logger_merge_shift_marks_superseded():
    """party が 6 未満で統合の pop が枠の番号をずらしたとき: ずれた番号で推定と判明を比べない (superseded / 詰め直し)"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _state_from(ROWS_1008)
        opp = st.opponent
        # 5 枠: マスカーニャ / グライオン (推定) / 未特定 [どく/じめん] / ハラバリー / アシレーヌ
        opp.party = [opp.party[0], opp.party[1], PokemonState(types=["どく", "じめん"]), opp.party[2], opp.party[4]]
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.5)
        _frame(lg, st, "selection")
        f = lg._file
        # 推定グライオンの枠が場に出たドオーで上書きされた (旧規則の根拠の無い置き換えの再現)
        opp.party[1] = PokemonState(species_ja="ドオー", species_id="clodsire", hp_percent=70.0, is_active=True)
        opp.active_index = 1
        _frame(lg, st, "command")
        link_active_to_party(st, "opponent")                              # 枠 1 → 枠 2 (pop で番号は 1 に詰まる)
        assert [q.species_id for q in opp.party] == ["meowscarada", "clodsire", "bellibolt", "primarina"]
        _frame(lg, st, "command")
        lg._finalize("win")
        recs = _records(f)
        gc = {r["species"]: r for r in recs if r["type"] == "guess_confirm"}
        assert set(gc) == {"meowscarada", "gliscor", "bellibolt", "primarina"}, gc   # 詰めた番号で数え直さない
        # 推定グライオンの枠は統合で消えた: ドオーとの mismatch にしない
        assert gc["gliscor"]["verdict"] == BL.GUESS_SUPERSEDED and gc["gliscor"]["revealed"] is None, gc["gliscor"]
        assert gc["bellibolt"]["verdict"] == "unrevealed" and gc["primarina"]["verdict"] == "unrevealed", gc
        assert gc["bellibolt"]["slot"] == 3 and gc["primarina"]["slot"] == 4   # slot は推定した時点の番号のまま
        rc = [r for r in recs if r["type"] == "roster_change"]
        assert not any(r.get("from") in ("bellibolt", "primarina") for r in rc), rc
        assert [(r["slot"], r["from"], r["to"]) for r in rc] == [(1, "gliscor", "clodsire"), (1, None, "clodsire")], rc
        assert rc[1]["merged_from"] == 1 and rc[1]["merged_to"] == 1 and rc[1]["moved"]["hp"] == 70.0
        print("test_logger_merge_shift_marks_superseded OK")
    finally:
        shutil.rmtree(tmp)


# ------------------------------------------------------------------ 手動確定
def test_manual_species_family_and_pending():
    party = [PokemonState(species_ja="ヤドキング", species_id="slowking", types=["みず", "エスパー"]),
             PokemonState(types=["ノーマル"])]
    # 確定済みの枠の形態違い → 同じ個体の形態を直す (未確定の枠へ付け替えない)
    res = apply_manual_species(party, 0, "ガラルヤドキング", "slowkinggalar")
    assert res["index"] == 0 and not res["moved"] and party[0].species_id == "slowkinggalar", res
    # 対応待ちの個体は「入れる種が別の枠にもある」の判定に使わない (確定済みとして拒まない)
    party = [PokemonState(types=["どく", "エスパー"]),
             PokemonState(species_ja="ヤドキング", species_id="slowking", pending=True,
                          species_candidates=["slowking", "slowkinggalar"])]
    res = apply_manual_species(party, 0, "ヤドキング", "slowking")
    assert res["index"] == 0 and party[1].species_ja == "ヤドキング", res
    # 範囲外: 当たらなければ理由つきで入れない
    res = apply_manual_species_unplaced(party, "ガブリアス", "garchomp")
    assert res["index"] is None and res["reason"].startswith(MANUAL_REASON_OUT_OF_RANGE), res
    print("test_manual_species_family_and_pending OK")


def test_manual_fix_row_applied_and_reason():
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _state_from(ROWS_1008)
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.5)
        _frame(lg, st, "selection")
        f = lg._file
        st.log_event("manual", "手動確定を無視: ガブリアス (枠の番号が範囲外 (対応待ちの個体にも同じ種の枠にも当たらない))",
                     event_id="species_manual_skip", detail={"applied": False, "reason": "範囲外", "index": 9})
        _frame(lg, st, "field")
        st.log_event("manual", "相手のガラルヤドキングを手動確定 (候補から選択)", event_id="species_manual",
                     detail={"applied": True, "index": 5, "via": "slot"})
        st.events[-1]["ts"] += 1.0   # 同じ 0.01 秒に 2 件書くと時刻で区別できないので時刻を進める (実機では起きない間隔)
        _frame(lg, st, "command")
        mf = [r for r in _records(f) if r["type"] == "manual_fix"]
        assert mf[0]["applied"] is False and mf[0]["reason"] == "範囲外", mf[0]
        assert mf[1]["applied"] is True and "reason" not in mf[1], mf[1]
        print("test_manual_fix_row_applied_and_reason OK")
    finally:
        shutil.rmtree(tmp)


def test_restore_keeps_pending():
    st = _state_from(_rows_pending())
    st.opponent.switch_to_species("ヤドキング", "slowking", now=123.0)
    assert len(st.opponent.party) == PARTY_SIZE + 1
    st2 = BattleStateV2()
    st2.restore_from_dict(st.to_dict())
    pm = st2.opponent.party[PARTY_SIZE]
    assert pm.pending and pm.species_candidates == ["slowking", "slowkinggalar"] and pm.t_first_seen == 123.0
    assert st2.opponent.active_index == PARTY_SIZE
    # 対応待ちの個体のひんしは数えない (roster_slots。2026-10-09 運用側の指示: 7 体目を数に入れない)
    pm.status = "fainted"
    assert st2.opponent.fainted_count() == 0
    print("test_restore_keeps_pending OK")


def main() -> None:
    test_same_name_forms()
    test_match_slot_uses_only_grounded_rules()
    test_reproduce_1008_slowking_keeps_six_slots()
    test_pending_keeps_slots_and_resolves_by_manual_fix()
    test_pending_resolved_by_form_correction()
    test_pending_unresolved_at_end_and_guess_verdicts()
    test_pending_not_counted_as_seventh()
    test_engine_holds_roster_evaluation_while_opp_pending()
    test_pending_faint_moves_to_slot_once()
    test_final_guess_verdict_pure()
    test_link_merge_keeps_observations_and_slots()
    test_logger_merge_shift_marks_superseded()
    test_manual_species_family_and_pending()
    test_manual_fix_row_applied_and_reason()
    test_restore_keeps_pending()
    print("ALL OK")


if __name__ == "__main__":
    main()
