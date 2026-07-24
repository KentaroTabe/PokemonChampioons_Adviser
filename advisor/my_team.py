"""自分のチームの型 (能力ポイント/性格/持ち物) の登録と参照。

登録は「もっと見る」画面 (選出画面/交代画面) の自動読み取りで行われる
(vision/extractors.py の様子見抽出がステータスタブ/能力タブを読んで
update_build を呼ぶ)。config/my_team.json を手で編集しても良い。
未登録の個体は攻撃系252振りを仮定してダメージ計算する。

config/my_team.json の形式 (config/my_team.example.json 参照):
{
  "ペリッパー": {
    "能力ポイント": {"h": 32, "c": 32, "s": 2},   # ゲーム内の0-32スケール
    "性格": "ひかえめ",
    "持ち物": "こだわりメガネ",   # 任意 (画面から読めた値が優先)
    "特性": "あめふらし"          # 任意
  }
}
ステータスキーはHABCDS表記 (h/a/b/c/d/s) と英語名 (hp/atk/def/spa/spd/spe) の
両方を受け付ける。努力値 (0-252) で書いても良い: 33以上はそのまま努力値扱い。
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

# HABCDS表記 (h=HP, a=攻撃, b=防御, c=特攻, d=特防, s=素早さ) を受け付ける
_STAT_ALIASES = {"h": "hp", "a": "atk", "b": "def", "c": "spa",
                 "d": "spd", "s": "spe",
                 "hp": "hp", "atk": "atk", "def": "def", "spa": "spa",
                 "spd": "spd", "spe": "spe"}

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
        key = _STAT_ALIASES.get(str(stat).lower())
        if key is None:
            print(f"[my_team] 未知のステータスキー: {stat} ({species_ja})")
            continue
        v = int(v)
        # 32以下はゲーム内の能力ポイント (32≒努力値252) とみなし換算する
        ev[key] = min(252, v * 8) if v <= 32 else min(252, v)
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


def nature_names_ja() -> list:
    """日本語の性格名一覧 (もっと見る画面のOCR照合用)"""
    return [k for k in _NATURES if not k.isascii()]


def nature_multipliers(nature_ja: str) -> Optional[dict]:
    """性格名 -> {stat: 0.9/1.0/1.1}。未知の性格は None"""
    if nature_ja not in _NATURES:
        return None
    pair = _NATURES[nature_ja]
    return {pair[0]: 1.1, pair[1]: 0.9} if pair else {}


_POINT_KEYS = {"hp": "h", "atk": "a", "def": "b",
               "spa": "c", "spd": "d", "spe": "s"}


def update_build(species_ja: str, patch: dict) -> bool:
    """もっと見る画面の読み取り結果でエントリを更新する。

    patch: {"能力ポイント": {stat: 0-32}, "性格": str, "持ち物": str,
            "特性": str, "技": [str]} の部分集合 (空値は無視)。
    変更があった場合のみ保存して True を返す。
    """
    if not species_ja:
        return False
    if "能力ポイント" in patch and patch["能力ポイント"]:
        patch = dict(patch)
        patch["能力ポイント"] = {
            _POINT_KEYS.get(k, k): v
            for k, v in patch["能力ポイント"].items() if v}
    data = {k: dict(v) for k, v in _load().items()}
    entry = data.get(species_ja, {})
    changed = False
    for key, val in patch.items():
        if val in (None, "", [], {}):
            continue
        if entry.get(key) != val:
            entry[key] = val
            changed = True
    if not changed:
        return False
    data[species_ja] = entry
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    except OSError as e:
        print(f"[my_team] 保存失敗: {e}")
        return False
    print(f"[my_team] {species_ja} の型を更新: {list(patch.keys())}")
    return True


def get_my_moves(species_ja: Optional[str]) -> list:
    """登録済みの技名リスト (日本語)。未登録なら空リスト。

    技画面の所有者照合 (どのポケモンの技画面か) に使う
    """
    if not species_ja:
        return []
    entry = _load().get(species_ja) or {}
    return list(entry.get("技") or entry.get("moves") or [])
