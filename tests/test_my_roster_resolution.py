"""自分側の名前解決の優先順 (2026-09-16 第15回接続テスト後)。

第15回: 未登録のミミロップが登録済みのミミッキュ (類似度 0.6) に解決され、HUD で場のメタグロス枠を上書きして
7 体目が生えた。今の対戦のロスター → 表記どおりの種族 → 登録名への吸着、の順にし、ロスター外の HUD 名は無視する。

    python -m tests.test_my_roster_resolution
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import advisor.my_team as mt
from vision.extractors import adopt_my_hud_species, resolve_my_species
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()
ROSTER = ["メタグロス", "イエッサン", "ミミロップ", "ペロリーム", "イダイトウ", "サーフゴー"]


def _tmp_team(data: dict) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "my_team.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mt.CONFIG_PATH = tmp
    mt._CACHE, mt._CACHE_MTIME = None, -1.0
    return tmp


def test_roster_beats_registered():
    _tmp_team({"ミミッキュ": {"性格": "いじっぱり"}, "メタグロス": {"性格": "いじっぱり"}})
    r = resolve_my_species(resolver, "ミミロップ", cutoff=0.8, roster=ROSTER)
    assert r and r[0] == "ミミロップ" and r[1] == "lopunny", r
    r = resolve_my_species(resolver, "ミ三ロップ", cutoff=0.8, roster=ROSTER)     # OCR 揺れもロスターへ寄せる
    assert r and r[0] == "ミミロップ", r
    r = resolve_my_species(resolver, "ミミッキュ", cutoff=0.8, roster=ROSTER)     # ロスター外の表記どおりはそのまま
    assert r and r[0] == "ミミッキュ", r
    print("test_roster_beats_registered OK")


def test_exact_species_beats_registered_without_roster():
    _tmp_team({"ミミッキュ": {}})
    r = resolve_my_species(resolver, "ミミロップ", cutoff=0.8)
    assert r and r[0] == "ミミロップ", r     # 登録名 (ミミッキュ 0.6) より表記どおりを優先
    _tmp_team({"ゲッコウガ": {}, "メタグロス": {}})
    r = resolve_my_species(resolver, "ケイコウガ", cutoff=0.72)     # 表記どおりの種が無い誤読は登録名へ
    assert r and r[0] == "ゲッコウガ", r
    r = resolve_my_species(resolver, "メタング", cutoff=0.7)
    assert r and r[0] == "メタグロス", r
    print("test_exact_species_beats_registered_without_roster OK")


def _full_state():
    st = BattleStateV2()
    ids = {"メタグロス": "metagross", "イエッサン": "indeedee", "ミミロップ": "lopunny", "ペロリーム": "slurpuff",
           "イダイトウ": "basculegion", "サーフゴー": "gholdengo"}
    st.player.party = [PokemonState(species_ja=j, species_id=ids[j]) for j in ROSTER]
    st.player.active_index = 0
    return st


def test_hud_guard_ignores_out_of_roster_name():
    st = _full_state()
    res = adopt_my_hud_species(st, ("ミミッキュ", "mimikyu"))
    assert res is None
    assert st.player.party[0].species_ja == "メタグロス" and len(st.player.party) == 6
    assert st.player.active_index == 0
    assert any(e.get("event") == "hud_name_ignored" for e in st.events)
    print("test_hud_guard_ignores_out_of_roster_name OK")


def test_hud_in_roster_switches_slot():
    st = _full_state()
    res = adopt_my_hud_species(st, ("ミミロップ", "lopunny"))
    assert res is st.player.party[2] and st.player.active_index == 2
    assert st.player.party[0].hp_uncertain is True          # 交代の見逃しとして前の場の状態を不明扱い
    assert any(e.get("event") == "missed_switch" for e in st.events)
    print("test_hud_in_roster_switches_slot OK")


def test_hud_fills_unknown_active():
    st = BattleStateV2()
    st.player.party = [PokemonState()]
    st.player.active_index = 0
    res = adopt_my_hud_species(st, ("ミミロップ", "lopunny"))
    assert res is not None and st.player.party[0].species_ja == "ミミロップ"
    print("test_hud_fills_unknown_active OK")


if __name__ == "__main__":
    test_roster_beats_registered()
    test_exact_species_beats_registered_without_roster()
    test_hud_guard_ignores_out_of_roster_name()
    test_hud_in_roster_switches_slot()
    test_hud_fills_unknown_active()
