"""config/my_team.json による自分側の型登録の検証。

使い方: python -m tests.test_my_team
"""
import json
import tempfile
from pathlib import Path

import advisor.my_team as mt


def test_build_parsing():
    tmp = Path(tempfile.mkdtemp()) / "my_team.json"
    tmp.write_text(json.dumps({
        "ペリッパー": {"能力ポイント": {"hp": 32, "spa": 32, "spe": 2},
                       "性格": "ひかえめ", "持ち物": "こだわりメガネ"},
        "ブリジュラス": {"evs": {"hp": 252, "def": 128}, "nature": "modest"},
    }, ensure_ascii=False), encoding="utf-8")
    mt.CONFIG_PATH = tmp
    mt._CACHE, mt._CACHE_MTIME = None, -1.0

    b = mt.get_my_build("ペリッパー")
    assert b["ev"] == {"hp": 252, "spa": 252, "spe": 16}, b["ev"]
    assert b["nature"] == {"spa": 1.1, "atk": 0.9}, b["nature"]
    assert b["item_ja"] == "こだわりメガネ"

    b2 = mt.get_my_build("ブリジュラス")
    assert b2["ev"] == {"hp": 252, "def": 128}, b2["ev"]
    assert b2["nature"] == {"spa": 1.1, "atk": 0.9}
    assert mt.get_my_build("ミミッキュ") is None
    assert mt.has_build("ペリッパー") and not mt.has_build("ミミッキュ")
    print("test_build_parsing OK")


def test_engine_uses_build():
    from advisor.engine import build_mon_view
    from vision.normalize import NameResolver
    r = NameResolver()
    p = {"species_id": "pelipper", "species_ja": "ペリッパー", "types": [],
         "hp_percent": 100.0}
    v_default = build_mon_view(p, r)                      # 相手扱い: 攻撃252仮定
    v_mine = build_mon_view(p, r, side="player")          # 登録済みの型
    assert v_mine.ev.get("hp") == 252 and v_mine.ev.get("spa") == 252
    assert v_mine.nature.get("spa") == 1.1
    assert v_mine.item == "choicespecs", v_mine.item
    assert v_default.ev.get("hp", 0) == 0                 # 相手側は据え置き
    # 型登録によりHP実数と特攻が変わる
    assert v_mine.stat("spa") > v_default.stat("spa")
    print("test_engine_uses_build OK")


def test_registration_disabled():
    """登録参照の無効化 (2026-10-09、環境変数 config.MY_TEAM_DISABLED_ENV): 立っている間は読み取りの経路が全種「未登録」を返し、
    build_mon_view(side="player") は未登録の既定 (攻撃系 252 の仮定、持ち物なし)、技は登録で補完しない。書き込みはファイルの中身を保つ。
    外せば従来どおり"""
    import os
    from advisor.engine import OFFENSIVE_EV, build_mon_view, legal_move_slots, registered_moves_of
    from champions_agent.config import MY_TEAM_DISABLED_ENV
    from vision.normalize import NameResolver
    r = NameResolver()
    tmp = Path(tempfile.mkdtemp()) / "my_team.json"
    tmp.write_text(json.dumps({
        "ペリッパー": {"能力ポイント": {"hp": 32, "spa": 32, "spe": 2}, "性格": "ひかえめ", "持ち物": "こだわりメガネ",
                       "特性": "あめふらし", "種族ID": "pelipper", "技": ["ぼうふう", "なみのり", "とんぼがえり", "はねやすめ"]},
    }, ensure_ascii=False), encoding="utf-8")
    orig_path = mt.CONFIG_PATH
    prev = os.environ.get(MY_TEAM_DISABLED_ENV)
    mt.CONFIG_PATH = tmp
    mt._CACHE, mt._CACHE_MTIME = None, -1.0
    p = {"species_id": "pelipper", "species_ja": "ペリッパー", "types": [], "hp_percent": 100.0}
    state = {"field": {}, "player": {}, "opponent": {}}
    my_p = {"species_ja": "ペリッパー", "moves": [{"name_ja": "ぼうふう", "move_id": "hurricane", "pp": 10}]}
    try:
        os.environ.pop(MY_TEAM_DISABLED_ENV, None)
        assert not mt.registration_disabled()
        on_view = build_mon_view(p, r, side="player")
        assert on_view.item == "choicespecs" and on_view.ev.get("hp") == 252
        reg = registered_moves_of("ペリッパー", r)
        assert len(reg) == 4, reg
        assert len(legal_move_slots(state, my_p, registered=reg).slots) == 4
        for val in ("1", "true", "yes"):
            os.environ[MY_TEAM_DISABLED_ENV] = val
            assert mt.registration_disabled(), val
            assert mt.get_my_build("ペリッパー") is None and mt.get_my_moves("ペリッパー") == []
            assert not mt.has_build("ペリッパー") and mt.registered_species_ja() == []
            assert mt.registered_species_id("ペリッパー") is None
            v = build_mon_view(p, r, side="player")
            assert v.ev == dict(OFFENSIVE_EV) and v.nature == {} and v.item is None, (v.ev, v.nature, v.item)
            assert v.ability is None, v.ability        # 登録の特性 (あめふらし) で補わない (ペリッパーは特性が一意でない)
            reg_off = registered_moves_of("ペリッパー", r)
            assert reg_off == []
            slots = legal_move_slots(state, my_p, registered=reg_off).slots
            assert [s.get("move_id") for s in slots] == ["hurricane"], slots
            assert not any(s.get("from_registration") for s in slots)
        # 書き込みは無効化中でもファイルの中身 (既存の種) を保つ
        assert mt.set_build("ミミッキュ", {"性格": "いじっぱり"})
        data = json.loads(tmp.read_text(encoding="utf-8"))
        assert set(data) == {"ペリッパー", "ミミッキュ"}, data
        # 空・0・false・no は無効化しない
        for val in ("", "0", "false", "no"):
            os.environ[MY_TEAM_DISABLED_ENV] = val
            assert not mt.registration_disabled(), val
            assert mt.get_my_build("ペリッパー")["item_ja"] == "こだわりメガネ"
        os.environ.pop(MY_TEAM_DISABLED_ENV, None)
        assert build_mon_view(p, r, side="player").item == "choicespecs"
        assert len(mt.get_my_moves("ペリッパー")) == 4
    finally:
        if prev is None:
            os.environ.pop(MY_TEAM_DISABLED_ENV, None)
        else:
            os.environ[MY_TEAM_DISABLED_ENV] = prev
        mt.CONFIG_PATH = orig_path
        mt._CACHE, mt._CACHE_MTIME = None, -1.0
    print("test_registration_disabled OK")


if __name__ == "__main__":
    test_build_parsing()
    test_engine_uses_build()
    test_registration_disabled()
    print("ALL OK")
