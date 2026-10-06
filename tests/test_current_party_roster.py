"""現在のパーティ 6 体の決め方と、詳細パネルからの部分更新 (2026-09-16)。

- current_team_entries: 直近の選出ロスター (6 体) をそのまま現在の 6 体にし、未登録の種は空エントリで入れる
  (登録 23 体から旧チームを「現在」と推定し、使っていないチームの改善案を測定した事故の再発防止)
- merge_build_patch: patch にあるキーだけ置き換え、空値は削除、種族ID 等は残す

    python -m tests.test_current_party_roster
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import advisor.my_team as mt
from advisor.my_team import merge_build_patch
from tools.evaluate_team import current_team_entries


def _tmp_team(data: dict) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "my_team.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mt.CONFIG_PATH = tmp
    mt._CACHE, mt._CACHE_MTIME = None, -1.0
    return tmp


def test_roster_first_includes_unregistered():
    _tmp_team({"メタグロス": {"性格": "いじっぱり"}, "ペロリーム": {"性格": "ずぶとい"}, "ムクホーク": {"性格": "いじっぱり"},
               "アシレーヌ": {}, "ラグラージ": {}, "ドドゲザン": {}, "ミミッキュ": {}})
    roster = ["メタグロス", "イエッサン", "ミミロップ", "ペロリーム", "イダイトウ", "サーフゴー"]
    team = current_team_entries(roster=roster)
    assert list(team) == roster, list(team)
    assert team["メタグロス"]["性格"] == "いじっぱり" and team["イエッサン"] == {}
    assert "ムクホーク" not in team
    print("test_roster_first_includes_unregistered OK")


def test_fallback_without_roster():
    _tmp_team({"メタグロス": {}, "ペロリーム": {}})
    assert set(current_team_entries(roster=[])) == {"メタグロス", "ペロリーム"}        # 登録 ≤ 6 はそのまま
    assert set(current_team_entries(roster=["メタグロス"])) == {"メタグロス", "ペロリーム"}   # 6 体そろわないロスターは使わない
    print("test_fallback_without_roster OK")


def test_merge_build_patch():
    base = {"種族ID": "rotomwash", "性格": "ひかえめ", "技": ["ボルトチェンジ"], "持ち物": "こだわりスカーフ"}
    out = merge_build_patch(base, {"性格": "おくびょう", "技": [], "特性": "ふゆう"})
    assert out == {"種族ID": "rotomwash", "性格": "おくびょう", "持ち物": "こだわりスカーフ", "特性": "ふゆう"}, out
    assert base["技"] == ["ボルトチェンジ"]                     # 元は変えない
    assert merge_build_patch(None, {"能力ポイント": {"h": 32}}) == {"能力ポイント": {"h": 32}}
    assert merge_build_patch(base, {}) == base
    assert merge_build_patch(base, {"謎のキー": 1}) == base       # 型のキー以外は無視
    print("test_merge_build_patch OK")


if __name__ == "__main__":
    test_roster_first_includes_unregistered()
    test_fallback_without_roster()
    test_merge_build_patch()
