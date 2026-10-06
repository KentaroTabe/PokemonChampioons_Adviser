"""ゲーム内使用率順位 (championsbattledata の column_position) の取り込みと、脅威リストへの反映 (2026-09-18) のテスト。

    python -m tests.test_usage_ingame_rank

- parse_battle_rows が ingame_rank を返す / assign_ranks (順位が無い種は使用率% 順で後置)
- meta_snapshot.merge_ranked: 使用率% 上位 ∪ ゲーム内順位上位、重み = max(使用率%, 順位を使用率曲線に読み替えた値)。
  順位が使用率% の順と同じ (以前のスナップショット) なら結果が変わらない
- repair_usage_ranks の純粋部分 (アーカイブ → 順位、置き換え後の rank)
DB・ネットワークは使わない。
"""
from __future__ import annotations

from champions_agent.data.sources.championsbattledata import parse_battle_rows
from champions_agent.data.sources.usage_scraper import assign_ranks
from tools.repair_usage_ranks import jst_date, new_ranks, ranks_from_archive
from tools.team_build.meta_snapshot import merge_ranked, threat_weight, usage_at_rank


def _row(pos, cat="move", name="Earthquake", pct="67.0%", pv=67.0):
    return {"pokemon": "X", "column_position": pos, "category": cat, "rank": 1, "name": name,
            "percentage": pct, "percentage_value": pv}


def test_parse_ingame_rank():
    parsed = parse_battle_rows({"rows": [_row(5), _row(5, "held_item", "Golisopite", "98.6%", 98.6)]})
    assert parsed["ingame_rank"] == 5 and parsed["moves"] == {"earthquake": 67.0} and parsed["items"] == {"golisopite": 98.6}
    assert parse_battle_rows({"rows": [_row(None)]})["ingame_rank"] is None
    assert parse_battle_rows({"rows": []})["ingame_rank"] is None
    print("test_parse_ingame_rank OK")


def test_assign_ranks():
    per = {"salamence": {"ingame_rank": 1}, "garchomp": {"ingame_rank": 2}, "golisopod": {"ingame_rank": 5},
           "oldmon": {"ingame_rank": None}, "nodata": {}}
    ranks = assign_ranks(per, ["garchomp", "oldmon", "salamence", "nodata", "golisopod"])
    assert ranks == {"salamence": 1, "garchomp": 2, "golisopod": 5, "oldmon": 6, "nodata": 7}, ranks
    # 順位が 1 つも無ければ fallback の順 (以前の挙動)
    assert assign_ranks({"a": {}, "b": {}}, ["b", "a"]) == {"b": 1, "a": 2}
    print("test_assign_ranks OK")


def test_merge_ranked_adds_ingame_top():
    usage = [("garchomp", 59.46), ("archaludon", 33.51), ("primarina", 27.03), ("mimikyu", 21.62),
             ("basculegion", 21.08), ("hippowdon", 17.3), ("salamence", 0.1), ("golisopod", 0.1)]
    ranks = [("salamence", 1), ("garchomp", 2), ("primarina", 3), ("hippowdon", 4), ("golisopod", 5),
             ("archaludon", 8), ("mimikyu", 11), ("basculegion", 17)]
    rows = merge_ranked(usage, ranks, top_n=4, ingame_n=5)
    ids = [r["id"] for r in rows]
    by = {r["id"]: r for r in rows}
    # 和集合: 使用率% 上位 4 (garchomp/archaludon/primarina/mimikyu) ∪ 順位上位 5 (salamence/garchomp/primarina/hippowdon/golisopod)
    assert set(ids) == {"garchomp", "archaludon", "primarina", "mimikyu", "salamence", "hippowdon", "golisopod"}, ids
    assert by["salamence"]["from_ingame_only"] and by["golisopod"]["from_ingame_only"] and not by["garchomp"]["from_ingame_only"]
    # 重み: ボーマンダ (1 位) は曲線の 1 番目 = 59.46、グソクムシャ (5 位) は 5 番目 = 21.08、カバルドン (4 位) は 4 番目 21.62 > 17.3
    assert abs(by["salamence"]["weight"] - 59.46) < 1e-9 and abs(by["golisopod"]["weight"] - 21.08) < 1e-9
    assert abs(by["hippowdon"]["weight"] - 21.62) < 1e-9 and abs(by["archaludon"]["weight"] - 33.51) < 1e-9
    assert ids[:2] == ["salamence", "garchomp"] and ids.index("golisopod") > ids.index("archaludon")   # weight 降順、同点は順位
    assert usage_at_rank(100, [59.46, 33.51]) == 33.51 and usage_at_rank(None, [1.0]) == 0.0 and usage_at_rank(1, []) == 0.0
    assert threat_weight({"usage": 5.0}) == 5.0 and threat_weight({"usage": 5.0, "weight": 9.0}) == 9.0
    print("test_merge_ranked_adds_ingame_top OK")


def test_merge_ranked_unchanged_when_rank_follows_usage():
    """順位が使用率% の順そのもの (2026-09-18 以前のスナップショット) なら、上位 top_n がそのまま出て重み = 使用率%"""
    usage = [(f"m{i:02d}", 50.0 - i) for i in range(10)]
    ranks = [(f"m{i:02d}", i + 1) for i in range(10)]
    rows = merge_ranked(usage, ranks, top_n=6, ingame_n=4)
    assert [r["id"] for r in rows] == [f"m{i:02d}" for i in range(6)]
    assert all(abs(r["weight"] - r["usage"]) < 1e-9 and not r["from_ingame_only"] for r in rows)
    # 順位が無いスナップショットでも使用率% 上位だけで動く
    rows = merge_ranked(usage, [], top_n=3, ingame_n=4)
    assert [r["id"] for r in rows] == ["m00", "m01", "m02"] and rows[0]["ingame_rank"] is None
    print("test_merge_ranked_unchanged_when_rank_follows_usage OK")


def test_repair_pure_parts():
    payload = {"data": {"salamence": {"rows": [_row(1)]}, "garchomp": {"rows": [_row(2)]}, "nopos": {"rows": [_row(None)]}}}
    assert ranks_from_archive(payload) == {"salamence": 1, "garchomp": 2}
    existing = [("garchomp", 59.46), ("nopos", 10.0), ("salamence", 0.1), ("other", 0.1)]
    assert new_ranks(existing, {"salamence": 1, "garchomp": 2}) == {"salamence": 1, "garchomp": 2, "nopos": 3, "other": 4}
    assert jst_date("2026-09-16 21:34:52") == "2026-09-17" and jst_date("2026-09-16 10:00:00") == "2026-09-16"
    print("test_repair_pure_parts OK")


if __name__ == "__main__":
    test_parse_ingame_rank()
    test_assign_ranks()
    test_merge_ranked_adds_ingame_top()
    test_merge_ranked_unchanged_when_rank_follows_usage()
    test_repair_pure_parts()
