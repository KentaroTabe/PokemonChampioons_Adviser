"""相手列・対戦記録・遵守モデルの純粋関数テスト (M0)。

    python -m tests.test_team_build_opponents
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from tools.team_build import opponents as O
from tools.team_build import families as F
from tools.team_build import user_model as U
from tools.team_build.battle_log import summarize_events, _slim_advice, read_records


TEXT_A = """Garchomp @ focussash
Ability: roughskin
- earthquake

Metagross @ metagrossite
Ability: clearbody
- bulletpunch

Rotom-Wash @ choicescarf
Ability: levitate
- hydropump
"""


def test_parse_and_ids():
    species, mega = O.parse_team_text(TEXT_A, stones={"metagrossite"})
    assert species == frozenset({"garchomp", "metagross", "rotomwash"}), species
    assert mega == "metagross"
    assert O.team_id_of(TEXT_A) == O.team_id_of(TEXT_A + "\n")
    assert O.team_id_of(TEXT_A) != O.team_id_of(TEXT_A.replace("focussash", "lifeorb"))
    print("test_parse_and_ids OK")


def test_compose_team_latest_pool():
    """最新環境からの合成 (2026-09-18): 重みと共起で抽選、ベース種の重複なし、メガ石は上限まで、候補が尽きたら短く返す"""
    rng = random.Random(7)
    weights = {"garchomp": 59.0, "salamence": 59.0, "primarina": 27.0, "rotomwash": 5.0, "rotomheat": 2.0,
               "gengar": 16.0, "lopunny": 14.0, "metagross": 11.0, "hippowdon": 22.0, "mimikyu": 21.0, "zero": 0.0}
    teammates = {"garchomp": {"primarina": 76.0}, "salamence": {"garchomp": 40.0}}
    base = {"rotomwash": "rotom", "rotomheat": "rotom"}
    stone_of = {"salamence": "salamencite", "gengar": "gengarite", "lopunny": "lopunnite", "metagross": "metagrossite"}
    for _ in range(20):
        ids = O.compose_team(rng, weights, teammates, lambda s: base.get(s, s), stone_of, size=6, max_megas=2, mix=0.5)
        assert len(ids) == 6 and len(set(ids)) == 6 and "zero" not in ids
        assert not ({"rotomwash", "rotomheat"} <= set(ids))                       # ベース種の重複なし
        assert sum(1 for s in ids if stone_of.get(s)) <= 2                        # メガ石は上限まで
    # 候補が尽きたら size 未満: 石持ちしか残らないと上限で止まる
    ids = O.compose_team(random.Random(1), {"salamence": 1.0, "gengar": 1.0, "lopunny": 1.0}, {}, lambda s: s,
                         stone_of, size=6, max_megas=1)
    assert len(ids) == 1 and stone_of.get(ids[0])
    # 同じ seed なら同じ結果、共起の重みで相方が寄る (ガブリアス選出後はアシレーヌの得点が上がる)
    a = O.compose_team(random.Random(3), weights, teammates, lambda s: base.get(s, s), stone_of)
    b = O.compose_team(random.Random(3), weights, teammates, lambda s: base.get(s, s), stone_of)
    assert a == b
    print("test_compose_team_latest_pool OK")


def test_build_split_and_sequence():
    rng = random.Random(3)
    pool = [f"s{i}" for i in range(30)]
    teams, by_id = [], {}
    for i in range(40):
        sp = rng.sample(pool, 6)
        text = "\n\n".join(f"{s} @ item{j}\n- tackle" for j, s in enumerate(sp))
        tid = O.team_id_of(text)
        teams.append(F.Team(team_id=tid, species=frozenset(sp), mega=None, rank=i + 1))
        by_id[tid] = text
    with tempfile.TemporaryDirectory() as d:
        doc = O.build_split("run_t", Path(d), seed=5, teams=teams, by_id=by_id)
        assert (Path(d) / "opponent_families.json").exists()
        assert doc["n_teams"] == 40 and doc["sealed_id"]
        tiers = doc["tiers"]
        ids = tiers["search"] + tiers["selection"] + tiers["holdout"]
        assert len(ids) == len(set(ids)) == 40
        # fold は互いに素で SEARCH 全体を覆う (fold 数は config: 適応 / 評価 / 検証)
        all_fold_ids = [t for f in doc["search_folds"] for t in f]
        assert len(doc["search_folds"]) >= 2 and len(all_fold_ids) == len(set(all_fold_ids))
        assert sorted(all_fold_ids) == sorted(tiers["search"])
        loaded = O.load_split(Path(d) / "opponent_families.json")
        assert O.tier_ids(loaded, "search", fold=0) == doc["search_folds"][0]
        seq1 = O.opponent_sequence(O.tier_ids(loaded, "selection"), 50, seed=11)
        seq2 = O.opponent_sequence(O.tier_ids(loaded, "selection"), 50, seed=11)
        assert seq1 == seq2 and len(seq1) == 50            # 決定的、繰り返しで補う
        assert set(seq1) <= set(tiers["selection"])
        # holdout は封印ファイルにも同じ id 列
        sealed = json.loads((Path(d) / "sealed" / f"holdout_{doc['sealed_id']}.json").read_text())
        assert sorted(sealed["team_ids"]) == sorted(tiers["holdout"])
    print("test_build_split_and_sequence OK")


def test_event_summary_and_records():
    events = [
        (1, ["switch", "p1a: Garchomp", "Garchomp, L50, M", "100/100"]),
        (1, ["switch", "p2a: Gengar", "Gengar, L50", "100/100"]),
        (2, ["move", "p2a: Gengar", "Shadow Ball", "p1a: Garchomp"]),
        (2, ["-status", "p1a: Garchomp", "brn"]),
        (3, ["move", "p1a: Garchomp", "Earthquake", "p2a: Gengar"]),
        (3, ["faint", "p2a: Gengar"]),
        (3, ["-enditem", "p1a: Garchomp", "Focus Sash"]),
    ]
    s = summarize_events(events)
    assert s["ko_events"] == [{"turn": 3, "fainted": "p2a: Gengar", "by": "p1a: Garchomp", "move": "Earthquake"}]
    # poke-env の split_message 形式 (先頭が空文字) でも同じ要約になる
    s2 = summarize_events([(t, [""] + ev) for t, ev in events])
    assert s2 == s, s2
    assert s["status_events"][0]["status"] == "brn"
    assert s["resource_usage"][0]["item"] == "Focus Sash"
    assert len(s["switch_events"]) == 2
    slim = _slim_advice({"ok": True, "best": {"kind": "move", "id": "earthquake", "score": 20.0, "why": "x"},
                         "actions": [{"kind": "move", "id": "earthquake", "score": 20.0},
                                     {"kind": "switch", "id": "rotomwash", "score": 12.0}]})
    assert slim["best"] == {"kind": "move", "id": "earthquake", "score": 20.0}
    assert len(slim["actions"]) == 2 and "why" not in slim["best"]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.jsonl"
        p.write_text(json.dumps({"won": True}) + "\n" + json.dumps({"won": False}) + "\n")
        assert [r["won"] for r in read_records(p)] == [True, False]
    print("test_event_summary_and_records OK")


def test_user_model():
    adv = {"actions": [{"kind": "move", "id": "a", "score": 20.0}, {"kind": "move", "id": "b", "score": 19.0}]}
    adv_clear = {"actions": [{"kind": "move", "id": "a", "score": 20.0}, {"kind": "move", "id": "b", "score": 5.0}]}
    assert U.confidence_gap(adv) < U.confidence_gap(adv_clear)
    assert U.follow_probability("full", 0.0) == 1.0
    p_close = U.follow_probability("expert", U.confidence_gap(adv))
    p_clear = U.follow_probability("expert", U.confidence_gap(adv_clear))
    assert 0.5 <= p_close < p_clear <= 1.0, (p_close, p_clear)
    assert U.deviation_choice("mixed", adv)["id"] == "b"
    assert U.deviation_choice("expert", adv, rl_choice={"kind": "move", "id": "rl"})["id"] == "rl"
    assert U.deviation_choice("full", adv) is None
    rng = random.Random(0)
    follows = sum(U.decide("mixed", adv, rng)[1] for _ in range(2000))
    assert 0.6 * 2000 < follows < 0.95 * 2000, follows        # 迷い局面では基準 0.7 より少し上
    assert all(U.decide("full", adv, rng)[1] for _ in range(50))
    print("test_user_model OK")


if __name__ == "__main__":
    test_parse_and_ids()
    test_compose_team_latest_pool()
    test_build_split_and_sequence()
    test_event_summary_and_records()
    test_user_model()
