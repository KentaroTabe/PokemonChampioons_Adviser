"""画面種別ごとの情報抽出器。

- extract_selection: 選出画面 (自パーティ名/持ち物、相手パーティのタイプ)
- extract_battle_hud: バトルHUD (両者の名前/HP/残数)
- extract_move_select: 技選択画面 (技名/PP/相性ヒント)
- extract_watch: 様子を見る画面 (タイプ/技/特性/持ち物/パーティHP/相手HP%)
"""
from __future__ import annotations

import re
from typing import Optional

import cv2
import numpy as np

from vision import zones, ocr
from vision.zones import crop
from vision.state import BattleStateV2, MoveSlot, PokemonState
from vision.typeicons import classify_type_icon

# 相性ヒント表記 -> 内部表現
EFFECTIVENESS_MAP = [
    ("ちようばつぐん", "super_extreme"),   # こうかちょうバツグン (4倍)
    ("はつぐん", "super"),                # ばつぐん (2倍)
    ("かなりいまひとつ", "resist_heavy"),  # 0.25倍
    ("いまひとつ", "resist"),
    ("こうかなし", "immune"),
    ("なさそう", "immune"),
    ("こうかあり", "neutral"),
]


def _normalize_hint(text: str) -> Optional[str]:
    from vision.normalize import loose_key
    key = loose_key(text)
    if not key:
        return None
    for pat, val in EFFECTIVENESS_MAP:
        from vision.normalize import loose_key as lk
        if lk(pat) in key:
            return val
    return None


def _read_pp(img, zone) -> Optional[tuple]:
    """PP表示 '12/12' (大きい現在値 + 小さい最大値) を読む"""
    c = crop(img, zone)
    if c is None:
        return None
    processed = ocr.white_text_mask(c, val_min=160)
    text = ocr.read_text(processed, allowlist="0123456789/")
    frac = ocr.parse_fraction(text)
    if frac:
        return frac
    digits = re.sub(r"\D", "", text)
    if digits:
        # '1212' -> (12,12) の分解は parse_fraction 済み。単独数字は現在PPのみとする
        val = int(digits[:2]) if len(digits) > 2 else int(digits)
        if 0 <= val <= 64:
            return (val, None)
    return None


# ==============================================================================
# 選出画面
# ==============================================================================
def extract_selection(img, state: BattleStateV2, resolver) -> None:
    """選出画面から両パーティを取得する (未確定の枠だけ処理)"""
    # 自分側: 種族名 + 持ち物 (テキスト)
    for i, z in enumerate(zones.SELECTION_MY):
        if i < len(state.player.party) and state.player.party[i].species_ja:
            continue
        name_text = ocr.read_zone_text(img, z["name"], mode="panel")
        if not name_text:
            continue
        sp = resolver.resolve_species(name_text, cutoff=0.72)
        mon = PokemonState()
        if sp:
            mon.species_ja, mon.species_id = sp[0], sp[1]
        mon.display_name = name_text
        item_text = ocr.read_zone_text(img, z["item"], mode="panel", val_min=150)
        if item_text:
            item = resolver.resolve(item_text, "items", cutoff=0.75)
            if item:
                mon.item_ja, mon.item_id = item[0], item[1]
            elif "ナイト" in item_text or "ナイド" in item_text:
                # 新規メガストーン (辞書外) の可能性
                mon.item_ja = item_text
                mon.item_id = "megastone"
        while len(state.player.party) <= i:
            state.player.party.append(PokemonState())
        if mon.species_ja or mon.display_name:
            state.player.party[i] = mon

    # 相手側: タイプアイコン (1〜2個)
    for i, z in enumerate(zones.SELECTION_OPP):
        if i < len(state.opponent.party) and state.opponent.party[i].types:
            continue
        t1 = classify_type_icon(crop(img, z["type1"]))
        t2 = classify_type_icon(crop(img, z["type2"]))
        types = [t for t in (t1, t2) if t]
        while len(state.opponent.party) <= i:
            state.opponent.party.append(PokemonState())
        if types:
            state.opponent.party[i].types = types


_TYPE_EN2JA = None


def _type_en2ja() -> dict:
    global _TYPE_EN2JA
    if _TYPE_EN2JA is None:
        import json
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _TYPE_EN2JA = {en: ja for ja, en in raw.get("types", {}).items()}
    return _TYPE_EN2JA


def _species_types_ja(species_id: str) -> list:
    """showdown IDから日本語タイプリストを引く (advisorのdex.jsonを利用)"""
    try:
        from advisor.dex import get_dex
        sp = get_dex().species(species_id)
        if sp:
            return [_type_en2ja().get(t, t) for t in sp.get("types", [])]
    except Exception:
        pass
    return []


def link_active_to_party(state: BattleStateV2, side_name: str) -> None:
    """種族が判明した場に出ているポケモンを、選出画面由来のパーティ枠へ紐付ける。

    相手側はタイプアイコンしか分からないため、種族のタイプと一致する枠へマージする。
    紐付け後、余った末尾のプレースホルダ枠 (7枠目以降) を削除する。
    """
    side = state.side(side_name)
    side.prune_placeholders()
    active = side.active()
    if active is None or not active.species_ja:
        return
    cur_idx = side.active_index

    types = _species_types_ja(active.species_id) if active.species_id else []
    target_idx = None
    for i, p in enumerate(side.party):
        if p is active:
            continue
        # 1) 同一種族の枠
        if p.species_ja == active.species_ja:
            target_idx = i
            break
        # 2) タイプが一致する未特定枠 (相手側: 選出画面のタイプアイコン由来)
        if (target_idx is None and types and p.species_ja is None
                and p.types and set(p.types) == set(types)):
            target_idx = i
    if target_idx is None:
        return

    slot = side.party[target_idx]
    # プレースホルダ (active) の情報を選出枠へマージ
    slot.merge_species(active.species_ja, active.species_id)
    for attr in ("display_name", "gender", "hp_percent", "hp_current", "hp_max",
                 "status", "ability_ja", "ability_id", "item_ja", "item_id"):
        val = getattr(active, attr)
        if val is not None:
            setattr(slot, attr, val)
    slot.volatiles = active.volatiles
    slot.boosts = active.boosts
    slot.moves = active.moves or slot.moves
    slot.revealed_moves = list({*slot.revealed_moves, *active.revealed_moves})
    slot.is_mega = active.is_mega or slot.is_mega
    if not slot.types and types:
        slot.types = types
    slot.is_active = True

    # マージ元 (プレースホルダ等) を除去
    if cur_idx is not None and cur_idx != target_idx and cur_idx < len(side.party):
        side.party.pop(cur_idx)
        if target_idx > cur_idx:
            target_idx -= 1
    side.active_index = target_idx
    side.prune_placeholders()


# ==============================================================================
# バトルHUD (コマンド/技選択画面共通)
# ==============================================================================
def extract_battle_hud(img, state: BattleStateV2, resolver) -> None:
    # --- 相手: 表示名 + HP% ---
    name_text = ocr.read_zone_text(img, zones.BATTLE["opp_name"], mode="panel")
    opp = state.opponent.active()
    if name_text:
        sp = resolver.resolve_species(name_text, cutoff=0.85)
        if sp:
            # 種族名がそのまま表示されている場合: 種族で枠を確定
            opp = state.opponent.switch_to_species(sp[0], sp[1])
        elif opp is None or (opp.display_name and
                             opp.display_name != name_text and not opp.species_ja):
            # ニックネーム表示: 表示名で追跡
            idx = state.opponent.find_by_display_name(name_text)
            if idx is not None:
                state.opponent.switch_to(idx)
                opp = state.opponent.party[idx]
            else:
                opp = state.opponent.ensure_active()
        else:
            opp = state.opponent.ensure_active()
        opp.display_name = name_text
    else:
        opp = state.opponent.ensure_active()
    link_active_to_party(state, "opponent")
    opp = state.opponent.ensure_active()

    hp_text = ocr.read_zone_text(img, zones.BATTLE["opp_hp_text"], mode="panel",
                                 allowlist="0123456789%")
    pct = ocr.parse_percent(hp_text)
    bar = ocr.hp_bar_ratio(crop(img, zones.BATTLE["opp_hp_bar"]))
    if pct is not None:
        # OCRとバー残量が大きく食い違う場合 (「1%」->「19」等の誤読) はバーを信用する
        if bar is not None and abs(pct - bar * 100) > 15:
            opp.hp_percent = round(bar * 100, 1)
        else:
            opp.hp_percent = float(pct)
    elif bar is not None:
        opp.hp_percent = round(bar * 100, 1)

    # --- 自分: 表示名 + HP実数 ---
    me = state.player.ensure_active()
    my_name = ocr.read_zone_text(img, zones.BATTLE["my_name"], mode="panel")
    if my_name:
        me.display_name = my_name
        sp = resolver.resolve_species(my_name, cutoff=0.8)
        if sp:
            # 表示名=種族名のケース (ニックネーム未設定)
            idx = state.player.find_by_species(sp[0])
            if idx is not None and idx != state.player.active_index:
                state.player.switch_to(idx)
                me = state.player.party[idx]
            me.merge_species(sp[0], sp[1])
            link_active_to_party(state, "player")
            me = state.player.ensure_active()

    my_hp = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel",
                               allowlist="0123456789/")
    frac = ocr.parse_fraction(my_hp)
    if frac and frac[1]:
        me.hp_current, me.hp_max = frac
        me.hp_percent = round(frac[0] / frac[1] * 100, 1)

    # --- COMMAND 残り秒数 ---
    cmd = ocr.read_zone_text(img, zones.BATTLE["command_no"], mode="panel",
                             allowlist="0123456789")
    if cmd.isdigit():
        val = int(cmd)
        if 0 <= val <= 45:
            state.command_no = val

    state.battle_active = True


# ==============================================================================
# 技選択画面
# ==============================================================================
def extract_move_select(img, state: BattleStateV2, resolver) -> None:
    me = state.player.ensure_active()
    new_moves = []
    for i, row in enumerate(zones.MOVE_ROWS):
        name_text = ocr.read_zone_text(img, row["name"], mode="panel")
        if not name_text:
            continue
        slot = MoveSlot(name_ja=name_text)
        mv = resolver.resolve(name_text, "moves", cutoff=0.7)
        if mv:
            slot.name_ja, slot.move_id = mv[0], mv[1]
        pp = _read_pp(img, row["pp"])
        if pp:
            slot.pp, slot.max_pp = pp[0], pp[1]
        hint_text = ocr.read_zone_text(img, row["hint"], mode="panel", val_min=150)
        if hint_text:
            slot.effectiveness = _normalize_hint(hint_text)
        new_moves.append(slot)

    if new_moves:
        # 既知の技リストへマージ (PP等を更新)
        existing = {m.move_id or m.name_ja: m for m in me.moves}
        merged = []
        for slot in new_moves:
            key = slot.move_id or slot.name_ja
            old = existing.get(key)
            if old:
                old.pp = slot.pp if slot.pp is not None else old.pp
                old.max_pp = slot.max_pp or old.max_pp
                old.effectiveness = slot.effectiveness or old.effectiveness
                merged.append(old)
            else:
                merged.append(slot)
        me.moves = merged


# ==============================================================================
# 様子を見る画面
# ==============================================================================
def extract_watch(img, state: BattleStateV2, resolver) -> None:
    me = state.player.ensure_active()

    # タイプ (テキスト表記)
    type_text = ocr.read_zone_text(img, zones.WATCH["type_row"], mode="panel")
    if type_text:
        found = []
        for jp in ("ノーマル", "ほのお", "みず", "でんき", "くさ", "こおり", "かくとう",
                    "どく", "じめん", "ひこう", "エスパー", "むし", "いわ", "ゴースト",
                    "ドラゴン", "あく", "はがね", "フェアリー"):
            from vision.normalize import loose_key
            if loose_key(jp) in loose_key(type_text):
                found.append(jp)
        if found:
            me.types = found

    # 特性 / 持ち物
    ability_text = ocr.read_zone_text(img, zones.WATCH["ability_value"], mode="panel")
    if ability_text:
        ab = resolver.resolve(ability_text, "abilities", cutoff=0.72)
        if ab:
            me.ability_ja, me.ability_id = ab[0], ab[1]
    item_text = ocr.read_zone_text(img, zones.WATCH["item_value"], mode="panel")
    if item_text:
        it = resolver.resolve(item_text, "items", cutoff=0.72)
        if it:
            me.item_ja, me.item_id = it[0], it[1]

    # 技 + PP
    for i, row in enumerate(zones.WATCH_MOVES):
        name_text = ocr.read_zone_text(img, row["name"], mode="panel")
        if not name_text:
            continue
        mv = resolver.resolve(name_text, "moves", cutoff=0.7)
        pp = _read_pp(img, row["pp"])
        key_ja = mv[0] if mv else name_text
        slot = next((m for m in me.moves if m.name_ja == key_ja), None)
        if slot is None:
            slot = MoveSlot(name_ja=key_ja, move_id=mv[1] if mv else None)
            me.moves.append(slot)
        if pp:
            slot.pp = pp[0]
            slot.max_pp = pp[1] or slot.max_pp

    # 左列: 自分の選出パーティのHP実数値
    for i, z in enumerate(zones.WATCH_MY[:3]):
        name_text = ocr.read_zone_text(img, z["name"], mode="panel")
        hp_text = ocr.read_zone_text(img, z["hp"], mode="panel",
                                     allowlist="0123456789/")
        frac = ocr.parse_fraction(hp_text)
        if not name_text or not frac or not frac[1]:
            continue
        sp = resolver.resolve_species(name_text, cutoff=0.72)
        idx = None
        if sp:
            idx = state.player.find_by_species(sp[0])
        if idx is None:
            idx = state.player.find_by_display_name(name_text)
        if idx is None:
            if not sp:
                continue
            # 選出画面を経ずに起動した場合でもパーティに登録する
            mon = PokemonState(species_ja=sp[0], species_id=sp[1],
                               display_name=name_text)
            state.player.party.append(mon)
            idx = len(state.player.party) - 1
        mon = state.player.party[idx]
        mon.hp_current, mon.hp_max = frac
        mon.hp_percent = round(frac[0] / frac[1] * 100, 1)
        if frac[0] == 0:
            mon.status = "fainted"

    # 右列: 相手パーティのHP% (視認済みのポケモンのみ表示される)
    for i, z in enumerate(zones.WATCH_OPP):
        if i >= len(state.opponent.party):
            break
        hp_text = ocr.read_zone_text(img, z["hp_text"], mode="panel",
                                     allowlist="0123456789%")
        pct = ocr.parse_percent(hp_text)
        if pct is not None:
            state.opponent.party[i].hp_percent = float(pct)
