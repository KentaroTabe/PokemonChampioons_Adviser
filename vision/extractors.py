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
    ("いまひと", "resist"),
    ("こうかなし", "immune"),
    ("なさそう", "immune"),
    ("こうかあり", "neutral"),
    ("かあり", "neutral"),
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
    text = ocr.read_zone_text(img, zone, mode="panel", allowlist="0123456789/",
                              val_min=160)
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
        name_text = ocr.read_zone_text(img, z["name"], mode="panel",
                                       allowlist=ocr.KATAKANA_ALLOWLIST)
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
    my_name = ocr.read_zone_text(img, zones.BATTLE["my_name"], mode="panel",
                                 allowlist=ocr.KATAKANA_ALLOWLIST)
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
def detect_move_rows(img) -> list:
    """技リストの各行を動的検出し、名前/PP/ヒントのゾーンを導出する。

    選択中の行は拡大され、相性ヒントの有無で行位置がずれるため、
    固定ゾーンではなく毎回ピル (白い縁取り線ペア) を検出する。
    戻り値: [{"name": zone, "pp": zone, "hint": zone}] (相対座標)
    """
    from vision.scenes import detect_move_pills
    rows = []
    for (top, bot) in detect_move_pills(img)[:5]:
        rows.append({
            "name": {"x0": 0.765, "y0": top, "x1": 0.920, "y1": bot},
            "pp": {"x0": 0.905, "y0": top - 0.010, "x1": 0.990, "y1": bot + 0.005},
            "hint": {"x0": 0.735, "y0": bot, "x1": 0.910, "y1": min(0.99, bot + 0.042)},
        })
    return rows


def extract_move_select(img, state: BattleStateV2, resolver) -> None:
    me = state.player.ensure_active()
    new_moves = []
    for i, row in enumerate(detect_move_rows(img)):
        name_text = ocr.read_zone_text(img, row["name"], mode="panel")
        if not name_text:
            continue
        slot = MoveSlot(name_ja=name_text)
        mv = resolver.resolve(name_text, "moves", cutoff=0.7)
        if mv:
            slot.name_ja, slot.move_id = mv[0], mv[1]
        else:
            continue  # 技名として解決できない行 (誤検出) はスキップ
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
# 場の状況確認画面 (ランク倍率・場の効果のオーバーレイ)
# ==============================================================================
# 倍率表記 -> ランク段階 (通常ステータス)
_MULT_TO_STAGE = [(4.0, 6), (3.5, 5), (3.0, 4), (2.5, 3), (2.0, 2), (1.5, 1),
                  (1.0, 0), (0.67, -1), (0.5, -2), (0.4, -3), (0.33, -4),
                  (0.29, -5), (0.25, -6)]
# めいちゅう/かいひ用
_MULT_TO_STAGE_ACC = [(3.0, 6), (2.67, 5), (2.33, 4), (2.0, 3), (1.67, 2),
                      (1.33, 1), (1.0, 0), (0.75, -1), (0.6, -2), (0.5, -3),
                      (0.43, -4), (0.38, -5), (0.33, -6)]

_FC_STAT_LABELS = {
    "こうけき": "atk", "ほうきよ": "def", "とくこう": "spa", "とくほう": "spd",
    "すはやさ": "spe", "めいちゆう": "acc", "かいひ": "eva",
}

# 「〜状態」の効果名 -> 状態への反映
_FC_EFFECTS = [
    (("すなあらし",), ("weather", "sandstorm")),
    (("にほんはれ", "ひてり", "日差し"), ("weather", "sun")),
    (("あめ", "雨"), ("weather", "rain")),
    (("ゆき", "雪"), ("weather", "snow")),
    (("えれきふいると",), ("terrain", "electric")),
    (("くらすふいると",), ("terrain", "grassy")),
    (("さいこふいると",), ("terrain", "psychic")),
    (("みすとふいると",), ("terrain", "misty")),
    (("とりつくるむ", "ゆかんたしくう"), ("trickroom", None)),
    (("りふれくた",), ("side_flag", "reflect")),
    (("ひかりのかへ",), ("side_flag", "light_screen")),
    (("おろらへる",), ("side_flag", "aurora_veil")),
    (("おいかせ",), ("side_flag", "tailwind")),
    (("すてるすろつく",), ("hazard", "stealth_rock")),
    (("まきひし",), ("hazard_count", "spikes")),
    (("とくひし",), ("hazard_count", "toxic_spikes")),
    (("ねはねはねつと",), ("hazard", "sticky_web")),
    (("もうとく",), ("status", "toxic")),
    (("とく",), ("status", "poison")),
    (("やけと", "火傷"), ("status", "burn")),
    (("まひ",), ("status", "paralysis")),
    (("ねむり",), ("status", "sleep")),
    (("こおり",), ("status", "freeze")),
    (("こんらん",), ("volatile", "confusion")),
    (("みかわり",), ("volatile", "substitute")),
    (("やとりき",), ("volatile", "leechseed")),
]


def _nearest_stage(mult: float, table) -> int:
    return min(table, key=lambda mv: abs(mv[0] - mult))[1]


def extract_field_check(img, state: BattleStateV2, resolver) -> None:
    """「場の状況」画面からランク倍率・場の効果・状態異常を確定値として取り込む。

    レイアウトは表示中の陣営 (自分=藍紫/相手=深紅パネル) や特性行の有無で
    ずれるため、行OCR (座標付き) のラベル位置をアンカーにする。
    """
    from vision.normalize import loose_key

    lines = ocr.apple_ocr_lines(img, scale=1.0)
    if not lines:
        return

    def find(pred):
        return [(t, bb) for t, bb in lines if pred(t, bb)]

    # アンカー確認
    if not any("効果と場の状" in t or "こうかとはのしよ" in loose_key(t)
               for t, _ in lines):
        return

    # --- 表示中の陣営: 名前パネル付近の色 (深紅=相手 / 藍紫=自分) ---
    name_area = crop(img, {"x0": 0.19, "y0": 0.21, "x1": 0.43, "y1": 0.27})
    side_name = "player"
    if name_area is not None:
        hsv = cv2.cvtColor(name_area, cv2.COLOR_BGR2HSV)
        crimson = cv2.inRange(hsv, np.array([160, 60, 60]), np.array([180, 255, 255]))
        crimson2 = cv2.inRange(hsv, np.array([0, 60, 60]), np.array([8, 255, 255]))
        purple = cv2.inRange(hsv, np.array([105, 40, 40]), np.array([160, 255, 255]))
        c = cv2.countNonZero(crimson) + cv2.countNonZero(crimson2)
        p = cv2.countNonZero(purple)
        side_name = "opponent" if c > p else "player"
    side = state.side(side_name)

    # --- 種族名 (左上、x<0.45 y<0.30 の日本語行) ---
    mon = None
    for t, (x0, y0, x1, y1) in lines:
        if x0 < 0.45 and y0 < 0.30 and len(t) >= 3:
            sp = resolver.resolve_species(t, cutoff=0.75)
            if sp:
                idx = side.find_by_species(sp[0])
                if idx is not None:
                    mon = side.party[idx]
                else:
                    mon = side.switch_to_species(sp[0], sp[1])
                    link_active_to_party(state, side_name)
                    mon = side.ensure_active()
                break
    if mon is None:
        mon = side.ensure_active()

    # --- HP ---
    # 斜体数字は日本語モードで崩れるため、行の領域をen-USで再OCRする。
    # Visionの読みはスケールで揺れるため複数スケールを試し、妥当な結果のみ採用する
    h_img, w_img = img.shape[:2]
    for t, (x0, y0, x1, y1) in lines:
        if not (x0 < 0.48 and 0.26 < y0 < 0.36
                and any(c.isdigit() or c in "%％/" for c in t)):
            continue
        pad_y, pad_x = 0.012, 0.02
        box = img[max(0, int((y0 - pad_y) * h_img)):int((y1 + pad_y) * h_img),
                  max(0, int((x0 - pad_x) * w_img)):int((x1 + pad_x) * w_img)]
        want_pct = "%" in t or "％" in t
        done = False
        for s in (2.0, 1.5, 3.0, 1.0):
            t2 = ocr.apple_ocr_text(box, scale=s, langs=("en-US",))
            if not t2:
                continue
            digits = re.sub(r"\D", "", t2)
            if want_pct:
                # '%'が読めているか、数字が2桁以上ある場合のみ%として信用する
                if ("%" in t2 or len(digits) >= 2):
                    pct = ocr.parse_percent(t2)
                    if pct is not None:
                        mon.hp_percent = float(pct)
                        done = True
                        break
            frac = ocr.parse_fraction(t2)
            if frac and frac[1]:
                mon.hp_current, mon.hp_max = frac
                mon.hp_percent = round(frac[0] / frac[1] * 100, 1)
                done = True
                break
        if done:
            break

    # --- タイプバッジ (x<0.45, y 0.30-0.42) ---
    types_found = []
    for t, (x0, y0, x1, y1) in lines:
        if x0 < 0.45 and 0.30 < y0 < 0.42:
            for jp in ("ノーマル", "ほのお", "みず", "でんき", "くさ", "こおり",
                        "かくとう", "どく", "じめん", "ひこう", "エスパー", "むし",
                        "いわ", "ゴースト", "ドラゴン", "あく", "はがね", "フェアリー"):
                if loose_key(jp) in loose_key(t) and jp not in types_found:
                    types_found.append(jp)
    if types_found:
        mon.types = types_found

    # --- 特性 / 持ち物 (自分側ページのみ表示。x<0.48, y 0.36-0.52) ---
    for t, (x0, y0, x1, y1) in lines:
        if not (x0 < 0.48 and 0.36 < y0 < 0.52 and len(t) >= 3):
            continue
        body = t.replace("特性", "").replace("持ち物", "")
        if len(body) < 3:
            continue
        if not mon.ability_id:
            ab = resolver.resolve(body, "abilities", cutoff=0.8)
            if ab:
                mon.ability_ja, mon.ability_id = ab[0], ab[1]
                continue
        if not mon.item_id:
            it = resolver.resolve(body, "items", cutoff=0.8)
            if it:
                mon.item_ja, mon.item_id = it[0], it[1]

    # --- ランク倍率: ステータスラベル行と同じ高さの「×n.n」を対応付ける ---
    import re as _re
    mult_lines = []
    for t, (x0, y0, x1, y1) in lines:
        m = _re.search(r"[xX×¥*]?([0-9]+[.,][0-9]+)", t)
        if m and 0.28 < x0 < 0.50:
            try:
                mult_lines.append((float(m.group(1).replace(",", ".")), (y0 + y1) / 2))
            except ValueError:
                pass
    for t, (x0, y0, x1, y1) in lines:
        if x0 > 0.30:
            continue
        lk = loose_key(t)
        stat = None
        for label, key in _FC_STAT_LABELS.items():
            if label in lk:
                stat = key
                break
        if stat is None:
            continue
        cy = (y0 + y1) / 2
        near = [mv for mv in mult_lines if abs(mv[1] - cy) < 0.022]
        if not near:
            continue
        table = _MULT_TO_STAGE_ACC if stat in ("acc", "eva") else _MULT_TO_STAGE
        mon.boosts[stat] = _nearest_stage(near[0][0], table)

    # --- 場の効果リスト (右カラム x>0.45, y 0.28-0.60) ---
    effect_lines = find(lambda t, bb: bb[0] > 0.45 and 0.28 < bb[1] < 0.60)
    turn_re = _re.compile(r"([0-9])\s*/\s*([0-9])")
    for t, (x0, y0, x1, y1) in effect_lines:
        lk = loose_key(t)
        if "状態" not in t and "状感" not in t and not turn_re.search(t):
            continue
        # 同じ行 or 近い行のターン表記 n/m
        turns_left = None
        m = turn_re.search(t)
        if not m:
            cy = (y0 + y1) / 2
            for t2, (a0, b0, a1, b1) in effect_lines:
                if abs((b0 + b1) / 2 - cy) < 0.02:
                    m = turn_re.search(t2)
                    if m:
                        break
        if m:
            elapsed, total = int(m.group(1)), int(m.group(2))
            if 0 < total <= 8 and elapsed <= total:
                turns_left = total - elapsed

        for keywords, (action, value) in _FC_EFFECTS:
            if not any(loose_key(k) in lk for k in keywords):
                continue
            f = state.field
            if action == "weather":
                f.weather = value
                if turns_left is not None:
                    f.weather_turns = turns_left
            elif action == "terrain":
                f.terrain = value
                if turns_left is not None:
                    f.terrain_turns = turns_left
            elif action == "trickroom":
                f.trick_room = True
                if turns_left is not None:
                    f.trick_room_turns = turns_left
            elif action == "side_flag":
                setattr(side, value, True)
            elif action == "hazard":
                setattr(side, value, True)
            elif action == "hazard_count":
                if getattr(side, value) == 0:
                    setattr(side, value, 1)
            elif action == "status":
                mon.status = value
            elif action == "volatile":
                if value not in mon.volatiles:
                    mon.volatiles.append(value)
            break

    state.log_event("field_check", f"{side_name}:{mon.species_ja or '?'} 場の状況を取得",
                    event_id="field_check")


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
        name_text = ocr.read_zone_text(img, z["name"], mode="panel",
                                       allowlist=ocr.KATAKANA_ALLOWLIST)
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
