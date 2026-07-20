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


if __name__ == "__main__":
    test_build_parsing()
    test_engine_uses_build()
    print("ALL OK")
