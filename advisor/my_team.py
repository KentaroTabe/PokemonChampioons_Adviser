"""自分のチームの型 (能力ポイント/性格/持ち物) の登録と参照。

画面からは自分のポケモンの努力値配分・性格は読み取れないため、既定では
攻撃系252振りを仮定している。config/my_team.json に型を登録すると、
自分側のダメージ計算・素早さ比較が実際の型で行われる。

config/my_team.json の形式 (config/my_team.example.json 参照):
{
  "ペリッパー": {
    "能力ポイント": {"hp": 32, "spa": 32, "spe": 2},   # ゲーム内の0-32スケール
    "性格": "ひかえめ",
    "持ち物": "こだわりメガネ",   # 任意 (画面から読めた値が優先)
    "特性": "あめふらし"          # 任意
  }
}
努力値 (0-252) で書いても良い: 33以上の値はそのまま努力値として扱う。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "my_team.json"

# 性格 -> (上昇ステータス, 下降ステータス)。無補正はNone
_NATURES = {
    "いじっぱり": ("atk", "spa"), "やんちゃ": ("atk", "spd"),
    "さみしがり": ("atk", "def"), "ゆうかん": ("atk", "spe"),
    "ずぶとい": ("def", "atk"), "わんぱく": ("def", "spa"),
    "のうてんき": ("def", "spd"), "のんき": ("def", "spe"),
    "ひかえめ": ("spa", "atk"), "おっとり": ("spa", "def"),
    "うっかりや": ("spa", "spd"), "れいせい": ("spa", "spe"),
    "おだやか": ("spd", "atk"), "おとなしい": ("spd", "def"),
    "しんちょう": ("spd", "spa"), "なまいき": ("spd", "spe"),
    "おくびょう": ("spe", "atk"), "せっかち": ("spe", "def"),
    "むじゃき": ("spe", "spd"), "ようき": ("spe", "spa"),
    "がんばりや": None, "すなお": None, "てれや": None,
    "きまぐれ": None, "まじめ": None,
    # 英語IDも受け付ける
    "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
    "lonely": ("atk", "def"), "brave": ("atk", "spe"),
    "bold": ("def", "atk"), "impish": ("def", "spa"),
    "lax": ("def", "spd"), "relaxed": ("def", "spe"),
    "modest": ("spa", "atk"), "mild": ("spa", "def"),
    "rash": ("spa", "spd"), "quiet": ("spa", "spe"),
    "calm": ("spd", "atk"), "gentle": ("spd", "def"),
    "careful": ("spd", "spa"), "sassy": ("spd", "spe"),
    "timid": ("spe", "atk"), "hasty": ("spe", "def"),
    "naive": ("spe", "spd"), "jolly": ("spe", "spa"),
}

_CACHE: Optional[dict] = None
_CACHE_MTIME: float = -1.0


def _load() -> dict:
    """config/my_team.json を読む (更新されたら再読込)"""
    global _CACHE, _CACHE_MTIME
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _CACHE is not None and mtime == _CACHE_MTIME:
        return _CACHE
    try:
        _CACHE = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        _CACHE_MTIME = mtime
    except Exception as e:
        print(f"[my_team] 読み込み失敗: {e}")
        _CACHE = {}
    return _CACHE


def get_my_build(species_ja: Optional[str]) -> Optional[dict]:
    """種族名 (日本語) の登録済みの型を返す。

    戻り値: {"ev": {stat: 0-252}, "nature": {stat: 0.9/1.0/1.1},
             "item_ja": str|None, "ability_ja": str|None} または None
    """
    if not species_ja:
        return None
    entry = _load().get(species_ja)
    if not entry:
        return None
    ev = {}
    for stat, v in (entry.get("能力ポイント") or entry.get("evs") or {}).items():
        v = int(v)
        # 32以下はゲーム内の能力ポイント (32≒努力値252) とみなし換算する
        ev[stat] = min(252, v * 8) if v <= 32 else min(252, v)
    nature = {}
    nat = entry.get("性格") or entry.get("nature")
    if nat is not None:
        if nat not in _NATURES:
            print(f"[my_team] 未知の性格: {nat} ({species_ja})")
        else:
            pair = _NATURES[nat]
            if pair:
                nature[pair[0]] = 1.1
                nature[pair[1]] = 0.9
    return {
        "ev": ev,
        "nature": nature,
        "item_ja": entry.get("持ち物") or entry.get("item"),
        "ability_ja": entry.get("特性") or entry.get("ability"),
    }


def has_build(species_ja: Optional[str]) -> bool:
    return get_my_build(species_ja) is not None
