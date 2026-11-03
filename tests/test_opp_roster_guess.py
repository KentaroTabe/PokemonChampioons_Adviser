"""選出画面の相手枠の「推定」(species_guess) の扱いのテスト。

2026-09-29 第17回接続テスト: 選出画面のタイプアイコン + スプライト照合の結果が確定として扱われ、
セグレイブ が カイリュー と推定されて実物の カイリュー と 2 枠になる / ゴリランダー が メガニウム と推定される、
などが表示・分析・実戦バンクに「他の対戦の顔ぶれ」として混ざった。

- adopt_selection_guess: 同種の重複を作らない (スコアの高い方を残す、確定済みには負ける)
- replacement_slot / switch_to_species: 場に出た種は推定の重複 → タイプ一致 → 未特定の枠の順に置き換え、どれにも当たらなければ
  枠を置き換えず対応待ち (pending) に置く (2026-10-09 ④、ユーザー承認)
- extract_selection: 推定の印を付け、重複を保留する
- 分析 (analyze_battles / party_improvements): 推定 (guess) は相手の 6 体に数えない
- apply_manual_species: 手入力は推定の枠を上書きできる (2026-10-06 第18回: 推定の枠への手入力が 9 回続けて無視された)

    scripts/run_test.sh test_opp_roster_guess
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np

from vision.state import BattleStateV2, PokemonState, adopt_selection_guess


def test_adopt_guess_dedupes_same_species():
    party = [PokemonState(types=["ドラゴン", "こおり"]), PokemonState(types=["ドラゴン", "ひこう"]), PokemonState()]
    assert adopt_selection_guess(party, 1, "dragonite", "カイリュー", 0.70) == "adopt"
    assert party[1].species_guess and party[1].guess_score == 0.70 and party[1].species_ja == "カイリュー"
    # 同種を別枠に (スコアが低い) → 保留。既存はそのまま
    assert adopt_selection_guess(party, 0, "dragonite", "カイリュー", 0.60) == "skip"
    assert party[0].species_ja is None and party[1].species_ja == "カイリュー"
    # 余裕 (SELECTION_GUESS_REPLACE_MARGIN) 未満の差でも入れ替えない (フレーム間の揺れで往復しない)
    assert adopt_selection_guess(party, 0, "dragonite", "カイリュー", 0.72) == "skip"
    # 余裕以上高い → 既存の推定を取り消して入れ替え (タイプは残る)
    assert adopt_selection_guess(party, 0, "dragonite", "カイリュー", 0.80) == "replaced"
    assert party[0].species_ja == "カイリュー" and party[1].species_ja is None
    assert party[1].types == ["ドラゴン", "ひこう"] and not party[1].species_guess
    # 確定済み (推定でない) の同種には負けない
    party[2].merge_species("カイリュー", "dragonite")
    party[0].clear_species_guess()
    assert adopt_selection_guess(party, 0, "dragonite", "カイリュー", 0.99) == "skip"
    assert party[0].species_ja is None
    print("test_adopt_guess_dedupes_same_species OK")


def test_confirmed_merge_clears_guess():
    p = PokemonState()
    p.merge_species("カイリュー", "dragonite", guess=True, score=0.7)
    assert p.species_guess and p.guess_score == 0.7
    p.merge_species("セグレイブ", "baxcalibur")
    assert not p.species_guess and p.guess_score is None and p.species_id == "baxcalibur"
    d = p.to_dict()
    assert d["species_guess"] is False and "guess_score" in d
    print("test_confirmed_merge_clears_guess OK")


def _full_side(rows):
    st = BattleStateV2()
    side = st.opponent
    for ja, sid, types, score in rows:
        m = PokemonState(types=list(types))
        if ja:
            m.merge_species(ja, sid, guess=score is not None, score=score)
        side.party.append(m)
    side.active_index = 0
    return st, side


ROWS_DUP = [("カバルドン", "hippowdon", ["じめん"], 0.7), ("カイリュー", "dragonite", ["ドラゴン", "ひこう"], 0.62),
            ("リザードン", "charizard", ["ほのお", "ひこう"], 0.8), ("カイリュー", "dragonite", ["ドラゴン", "ひこう"], 0.75),
            ("サーフゴー", "gholdengo", ["はがね", "ゴースト"], 0.66), ("ハッサム", "scizor", ["むし", "はがね"], 0.9)]


def test_new_species_replaces_duplicate_guess_first():
    """第17回 16:06: 推定の カイリュー が 2 枠 → 場に出た セグレイブ はスコアの低い方の重複枠に入る"""
    st, side = _full_side(ROWS_DUP)
    side.switch_to_species("セグレイブ", "baxcalibur")
    names = [p.species_ja for p in side.party]
    assert len(side.party) == 6 and names.count("カイリュー") == 1, names
    assert names[1] == "セグレイブ" and not side.party[1].species_guess, names
    assert side.active().species_ja == "セグレイブ"
    assert side.party[3].species_ja == "カイリュー"      # スコアの高い方の推定は残る
    print("test_new_species_replaces_duplicate_guess_first OK")


def test_type_match_prefers_unconfirmed_slot():
    st, side = _full_side(ROWS_DUP)
    side.party[3].merge_species("カイリュー", "dragonite")   # 場に出て確定した方
    side.party[1].clear_species_guess()                      # 未特定 (ドラゴン/ひこう のまま)
    side.switch_to_species("ボーマンダ", "salamence")        # ドラゴン/ひこう
    names = [p.species_ja for p in side.party]
    assert names[1] == "ボーマンダ" and names[3] == "カイリュー", names
    print("test_type_match_prefers_unconfirmed_slot OK")


def _slot_snapshot(side):
    """元の 6 枠の種・推定・スコア・タイプ・HP・判明技 (対応待ちの前後で変わらないことを比べる)"""
    return [(p.species_id, p.species_ja, p.species_guess, p.guess_score, list(p.types), p.hp_percent,
             list(p.revealed_moves)) for p in side.party[:6]]


def test_lowest_score_guess_replaced_without_type_match():
    """タイプが一致する枠が無い初登場 (メタグロス) は、推定スコアが最も低い枠 (ミミッキュ 0.5) を置き換えない。
    2026-10-09 の判断 (ユーザー承認、④): 推定スコア最低の枠への置き換えは対応の根拠が無く、正しかったかもしれない推定と
    その枠の観測を失う (10/8 18:27: 「ヤドキング」が推定ムクホークの枠を上書き)。元の 6 枠をそのまま保持し、帰属先の
    決まらない場の個体は対応待ち (party の 7 番目、pending) に置いて、HP・技はその個体に付ける。
    旧来の期待値 (枠 3 がメタグロスに置き換わる) から変更"""
    rows = list(ROWS_DUP)
    rows[3] = ("ミミッキュ", "mimikyu", ["ゴースト", "フェアリー"], 0.5)
    st, side = _full_side(rows)
    side.party[0].hp_percent = 70.0                           # 既存の枠の観測 (場に出た後で下がった)
    side.party[0].revealed_moves = ["じしん"]
    side.party[5].merge_species("ハッサム", "scizor")          # 手動確定 (推定でない)
    before = _slot_snapshot(side)
    side.switch_to_species("メタグロス", "metagross")         # はがね/エスパー: タイプ一致なし
    assert len(side.party) == 7 and _slot_snapshot(side) == before, [q.species_ja for q in side.party]
    assert side.party[3].species_ja == "ミミッキュ" and side.party[3].species_guess and side.party[3].guess_score == 0.5
    pm = side.active()
    assert pm is side.party[6] and pm.pending and pm.species_id == "metagross"
    pm.hp_percent = 62.0                                      # 帰属先の決まらない観測は対応待ちの個体に付く
    pm.revealed_moves.append("コメットパンチ")
    assert _slot_snapshot(side) == before
    assert side.party[6].hp_percent == 62.0 and side.party[6].revealed_moves == ["コメットパンチ"]
    print("test_lowest_score_guess_replaced_without_type_match OK")


def test_confirmed_slots_survive_guess_replacement():
    """確定済み (推定でない) の枠も、ただ 1 つの推定の枠 (リザードン 0.8) も、タイプの合わない初登場 (メタグロス) で
    置き換えられない。2026-10-09 の判断 (ユーザー承認、④): 推定の枠を置き換える根拠はタイプの一致・同種の重複・未特定の枠
    だけで、それ以外は元の 6 枠を保持して場の個体を対応待ち (pending) に置く。旧来の期待値 (枠 2 がメタグロスに
    置き換わる) から変更"""
    rows = [("カバルドン", "hippowdon", ["じめん"], None), ("カイリュー", "dragonite", ["ドラゴン", "ひこう"], None),
            ("リザードン", "charizard", ["ほのお", "ひこう"], 0.8), ("サーフゴー", "gholdengo", ["はがね", "ゴースト"], None),
            ("ハッサム", "scizor", ["むし", "はがね"], None), ("ミミッキュ", "mimikyu", ["ゴースト", "フェアリー"], None)]
    st, side = _full_side(rows)
    side.party[1].hp_percent = 45.0                           # 確定済みの枠の観測
    side.party[1].status = "paralysis"
    before = _slot_snapshot(side)
    side.switch_to_species("メタグロス", "metagross")
    assert len(side.party) == 7 and _slot_snapshot(side) == before, [q.species_ja for q in side.party]
    assert side.party[2].species_ja == "リザードン" and side.party[2].species_guess
    assert side.party[1].status == "paralysis"
    pm = side.active()
    assert pm is side.party[6] and pm.pending and pm.species_ja == "メタグロス"
    pm.hp_percent = 88.0
    pm.revealed_moves.append("バレットパンチ")
    assert _slot_snapshot(side) == before and side.party[6].hp_percent == 88.0
    print("test_confirmed_slots_survive_guess_replacement OK")


def test_extract_selection_marks_guess_and_dedupes():
    """選出画面: 2 枠が同じ種 (カイリュー) と照合されたら、スコアの高い方だけ推定として入り、もう一方は未特定のまま"""
    from vision import extractors

    state = BattleStateV2()
    img = np.zeros((1080, 1920, 3), np.uint8)
    icon_types = iter(["ドラゴン", "こおり", "ドラゴン", "ひこう"] + [None] * 8)

    class FakeInference:
        def candidates(self, types, top_k=5):
            if set(types) == {"ドラゴン", "こおり"}:
                return [("baxcalibur", 0.5, "セグレイブ"), ("dragonite", 0.3, "カイリュー")]
            if set(types) == {"ドラゴン", "ひこう"}:
                return [("dragonite", 0.9, "カイリュー")]
            return []

    def fake_identify(icon, cands, **kw):
        # セグレイブの枠が カイリュー と誤照合される (第17回で 3 戦観測)
        return ("dragonite", "カイリュー", 0.62) if cands and cands[0][0] == "baxcalibur" else ("dragonite", "カイリュー", 0.75)

    with mock.patch.object(extractors.ocr, "read_zone_text", return_value=""), \
            mock.patch.object(extractors, "_is_picked_panel", return_value=None), \
            mock.patch.object(extractors, "classify_type_icon", side_effect=lambda crop: next(icon_types, None)), \
            mock.patch("advisor.infer.get_inference", return_value=FakeInference()), \
            mock.patch("vision.spriteid.identify_species", side_effect=fake_identify):
        extractors.extract_selection(img, state, None)
        extractors.extract_selection(img, state, None)   # 2 フレーム目でも往復しない
    party = state.opponent.party
    assert len(party) == 6
    assert party[1].species_ja == "カイリュー" and party[1].species_guess and party[1].guess_score == 0.75
    assert party[0].species_ja is None and party[0].types == ["ドラゴン", "こおり"], party[0]
    ids = [e.get("event") for e in state.events]
    assert "species_identified" in ids and "species_guess_dup" in ids, ids
    assert ids.count("species_guess_dup") == 1, ids                # 同じ結論は 1 回だけ記録
    print("test_extract_selection_marks_guess_and_dedupes OK")


def test_parsers_skip_guessed_roster_entries():
    from tools.party_improvements import parse_battle
    from tools.analyze_battles import _parse_battle

    def mon(ja, sid, guess=False, hp=None):
        d = {"species": sid, "ja": ja, "types": [], "hp": hp, "status": None, "boosts": {}, "mega": None,
             "item": None, "ability": None, "moves": [], "revealed": [], "picked": None}
        if guess:
            d["guess"] = True
        return d

    def scene(scene_name, party, active=None, t=1.0):
        return {"type": "scene", "t": t, "scene": scene_name, "turn": 1,
                "state": {"scene": scene_name, "field": {}, "selection_picked": None, "mega_used": {},
                          "player": {"active": 0, "party": [mon("ブリジュラス", "archaludon")]},
                          "opponent": {"active": active, "party": party}}}

    records = [
        {"type": "session", "t": 0.0, "source": "organic"},
        scene("selection", [mon("カイリュー", "dragonite", guess=True), mon("ハッサム", "scizor", guess=True)], t=1.0),
        # 場に出て確定 (guess なし)、hp 観測あり
        scene("command", [mon("セグレイブ", "baxcalibur", hp=100.0), mon("ハッサム", "scizor", guess=True)], active=0, t=2.0),
        {"type": "outcome", "t": 3.0, "outcome": "win"},
    ]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "battle_20260929_000000.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        b1 = parse_battle(str(path))
        b2 = _parse_battle(str(path))
    assert b1["opp_roster"] == ["セグレイブ"], b1["opp_roster"]
    assert b1["opp_fielded"] == ["セグレイブ"]
    assert b2["opp_species"] == ["セグレイブ"] and b2["opp_benched"] == [], b2
    print("test_parsers_skip_guessed_roster_entries OK")


def test_parsers_drop_species_of_replaced_slot():
    """guess の印が無い (古い) ログでも、枠の種が途中で別の種に置き換わったら前の種は数えない (場に出た種は残す)"""
    from tools.party_improvements import parse_battle
    from tools.analyze_battles import _parse_battle

    def mon(ja, sid, hp=None):
        return {"species": sid, "ja": ja, "types": [], "hp": hp, "status": None, "boosts": {}, "mega": None,
                "item": None, "ability": None, "moves": [], "revealed": [], "picked": None}

    def scene(scene_name, party, active=None, t=1.0):
        return {"type": "scene", "t": t, "scene": scene_name, "turn": 1,
                "state": {"scene": scene_name, "field": {}, "selection_picked": None, "mega_used": {},
                          "player": {"active": 0, "party": [mon("ブリジュラス", "archaludon")]},
                          "opponent": {"active": active, "party": party}}}

    records = [
        scene("selection", [mon("カイリュー", "dragonite"), mon("ハッサム", "scizor"), mon("メタグロス", "metagross")], t=1.0),
        # 枠 0 の カイリュー が場に出た セグレイブ に置き換わる (= 選出画面の誤同定)
        scene("command", [mon("セグレイブ", "baxcalibur", hp=100.0), mon("ハッサム", "scizor"), mon("メタグロス", "metagross")],
              active=0, t=2.0),
        # 場に出た ハッサム の枠が (誤って) 別の種に置き換わっても、場に出た事実で残る
        scene("command", [mon("セグレイブ", "baxcalibur", hp=80.0), mon("ハッサム", "scizor", hp=100.0),
                          mon("メタグロス", "metagross")], active=1, t=3.0),
        scene("command", [mon("セグレイブ", "baxcalibur", hp=80.0), mon("サーフゴー", "gholdengo"),
                          mon("メタグロス", "metagross")], active=0, t=4.0),
        {"type": "outcome", "t": 5.0, "outcome": "loss"},
    ]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "battle_20260929_000001.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        b1 = parse_battle(str(path))
        b2 = _parse_battle(str(path))
    assert b1["opp_roster"] == ["ハッサム", "メタグロス", "セグレイブ", "サーフゴー"], b1["opp_roster"]
    assert sorted(b1["opp_fielded"]) == ["セグレイブ", "ハッサム"]
    assert b2["opp_species"] == ["サーフゴー", "セグレイブ", "ハッサム", "メタグロス"], b2["opp_species"]
    assert b2["opp_benched"] == ["サーフゴー", "メタグロス"], b2["opp_benched"]
    print("test_parsers_drop_species_of_replaced_slot OK")


def test_compact_log_carries_guess_flag():
    from battle_logger import _compact_state
    st = BattleStateV2()
    st.opponent.party = [PokemonState(), PokemonState()]
    st.opponent.party[0].merge_species("カイリュー", "dragonite", guess=True, score=0.7)
    st.opponent.party[1].merge_species("ハッサム", "scizor")
    c = _compact_state(st.to_dict())
    assert c["opponent"]["party"][0].get("guess") is True
    assert "guess" not in c["opponent"]["party"][1]
    print("test_compact_log_carries_guess_flag OK")


def _guessed_party(rows):
    """rows: [(日本語名, id, 推定か)] → 相手の 6 枠 (None は未確定の枠)"""
    party = []
    for row in rows:
        p = PokemonState(types=["ノーマル"])
        if row:
            p.merge_species(row[0], row[1], guess=row[2], score=0.9 if row[2] else None)
        party.append(p)
    return party


def test_manual_species_overrides_guess():
    """2026-10-06 第18回接続テスト: 6 枠とも選出画面の推定で埋まっているとき、推定の カイリュー を ボーマンダ に直す手入力が
    「確定済み・空き枠なし」として無視された (15 戦で 9 回)。推定は確定ではないので、その枠に上書きする"""
    from vision.state import apply_manual_species
    party = _guessed_party([("フシギバナ", "venusaur", True), ("カイリュー", "dragonite", True),
                            ("ドヒドイデ", "toxapex", True), ("サーフゴー", "gholdengo", True),
                            ("カバルドン", "hippowdon", True), ("クエスパトラ", "espathra", True)])
    res = apply_manual_species(party, 1, "ボーマンダ", "salamence")
    assert res == {"index": 1, "moved": False, "cleared": [], "reason": None}, res
    assert party[1].species_ja == "ボーマンダ" and party[1].species_id == "salamence" and not party[1].species_guess
    assert [p.species_ja for i, p in enumerate(party) if i != 1] == ["フシギバナ", "ドヒドイデ", "サーフゴー", "カバルドン", "クエスパトラ"]
    # 推定と同じ種を選ぶ = 推定の確定 (印が消える)
    res = apply_manual_species(party, 0, "フシギバナ", "venusaur")
    assert res["index"] == 0 and not party[0].species_guess and party[0].species_ja == "フシギバナ"
    print("test_manual_species_overrides_guess OK")


def test_manual_species_confirmed_slot_and_duplicates():
    from vision.state import apply_manual_species
    # 対象枠が別の種で確定済み → 未確定の枠へ付け替える (2026-08-20 の挙動を保つ)。推定の枠には付け替えない
    party = _guessed_party([("ハッサム", "scizor", False), ("カイリュー", "dragonite", True), None])
    res = apply_manual_species(party, 0, "ガブリアス", "garchomp")
    assert res["index"] == 2 and res["moved"] is True and party[2].species_ja == "ガブリアス", res
    assert party[0].species_ja == "ハッサム" and party[1].species_ja == "カイリュー" and party[1].species_guess
    # 未確定の枠が無ければ入れない (理由つき)
    party = _guessed_party([("ハッサム", "scizor", False), ("カイリュー", "dragonite", True)])
    res = apply_manual_species(party, 0, "ガブリアス", "garchomp")
    assert res["index"] is None and "確定済み" in res["reason"], res
    assert [p.species_ja for p in party] == ["ハッサム", "カイリュー"]
    # 同じ種で確定済みの枠はそのまま
    res = apply_manual_species(party, 0, "ハッサム", "scizor")
    assert res["index"] == 0 and res["moved"] is False and party[0].species_ja == "ハッサム"
    # 入れる種が別の枠の「推定」にある → そちらを取り消して入れる (同種 2 体はあり得ない)
    party = _guessed_party([("カイリュー", "dragonite", True), ("ボーマンダ", "salamence", True)])
    res = apply_manual_species(party, 0, "ボーマンダ", "salamence")
    assert res["index"] == 0 and res["cleared"] == [1], res
    assert party[0].species_ja == "ボーマンダ" and party[1].species_ja is None and party[1].types == ["ノーマル"]
    # 入れる種が別の枠で「確定済み」→ 入れない (確定した枠は動かさない)
    party = _guessed_party([("カイリュー", "dragonite", True), ("ボーマンダ", "salamence", False)])
    res = apply_manual_species(party, 0, "ボーマンダ", "salamence")
    assert res["index"] is None and "slot1" in res["reason"], res
    assert party[0].species_ja == "カイリュー" and party[0].species_guess
    # 範囲外の枠
    assert apply_manual_species(party, 9, "ボーマンダ", "salamence")["index"] is None
    print("test_manual_species_confirmed_slot_and_duplicates OK")


def main() -> None:
    test_adopt_guess_dedupes_same_species()
    test_confirmed_merge_clears_guess()
    test_new_species_replaces_duplicate_guess_first()
    test_type_match_prefers_unconfirmed_slot()
    test_lowest_score_guess_replaced_without_type_match()
    test_confirmed_slots_survive_guess_replacement()
    test_extract_selection_marks_guess_and_dedupes()
    test_parsers_skip_guessed_roster_entries()
    test_parsers_drop_species_of_replaced_slot()
    test_compact_log_carries_guess_flag()
    test_manual_species_overrides_guess()
    test_manual_species_confirmed_slot_and_duplicates()
    print("ALL OK")


if __name__ == "__main__":
    main()
