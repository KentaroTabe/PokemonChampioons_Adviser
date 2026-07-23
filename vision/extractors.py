"""画面種別ごとの情報抽出器。

- extract_selection: 選出画面 (自パーティ名/持ち物、相手パーティのタイプ)
- extract_battle_hud: バトルHUD (両者の名前/HP/残数)
- extract_move_select: 技選択画面 (技名/PP/相性ヒント)
- extract_watch: 様子を見る画面 (タイプ/技/特性/持ち物/パーティHP/相手HP%)
"""
from __future__ import annotations

import difflib
import re
import time
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
def _is_picked_panel(img, panel_zone) -> bool:
    """選出済みパネルかどうか。

    実画面の選出済み表示は「パネル左端の白いリボン+番号バッジ」
    (ライム色ハイライトはカーソル位置であって選出済みの印ではない —
    実フレーム検証 2026-07-20)。左端ストリップの白画素率で判定する。
    """
    strip = {"x0": max(0.0, panel_zone["x0"] - 0.004),
             "y0": panel_zone["y0"] + 0.005,
             "x1": panel_zone["x0"] + 0.017,
             "y1": panel_zone["y1"] - 0.01}
    c = crop(img, strip)
    if c is None or c.size == 0:
        return False
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([180, 60, 255]))
    return cv2.countNonZero(white) / white.size > 0.25


def extract_selection(img, state: BattleStateV2, resolver) -> None:
    """選出画面から両パーティ・選出進捗を取得する (未確定の枠だけ処理)"""
    # --- 選出進捗「N/3」 ---
    prog_text = ocr.read_zone_text(img, zones.SELECTION["progress"],
                                   allowlist="0123/")
    m = re.search(r"([0-3])\s*/\s*3", prog_text or "")
    if m:
        state.selection_picked = int(m.group(1))

    # --- 選出済みパネルの検出 (左端の白リボン)。
    #     リボンの出現順を選出順 (先発=1) として追跡する ---
    picked_count = 0
    for i, z in enumerate(zones.SELECTION_MY):
        picked = _is_picked_panel(img, z["panel"])
        picked_count += int(picked)
        if i < len(state.player.party):
            mon = state.player.party[i]
            if picked and not mon.is_picked:
                mon.pick_order = 1 + sum(
                    1 for p in state.player.party if p.is_picked)
            elif not picked:
                mon.pick_order = None
            mon.is_picked = picked
    # OCRが読めなかった場合はハイライト数で補完
    if m is None and picked_count > 0:
        state.selection_picked = picked_count

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

    # 相手側: タイプアイコン (1〜2個) -> 使用率候補 -> スプライト照合で種族特定
    for i, z in enumerate(zones.SELECTION_OPP):
        while len(state.opponent.party) <= i:
            state.opponent.party.append(PokemonState())
        slot = state.opponent.party[i]

        if not slot.types:
            t1 = classify_type_icon(crop(img, z["type1"]))
            t2 = classify_type_icon(crop(img, z["type2"]))
            types = [t for t in (t1, t2) if t]
            if types:
                slot.types = types

        # 種族特定: タイプから候補を絞り、パネルのアイコンで視覚照合
        if slot.types and not slot.species_ja:
            try:
                from advisor.infer import get_inference
                from vision.spriteid import identify_species
                cands = get_inference().candidates(slot.types)
                hit = identify_species(crop(img, z["icon"]), cands)
                if hit:
                    slot.merge_species(hit[1], hit[0])
                    state.log_event("selection", f"相手枠{i + 1}を{hit[1]}と特定 "
                                    f"(視覚照合{hit[2]})", event_id="species_identified")
            except Exception:
                pass


_MY_LEGAL_MAXES = None


def _my_legal_maxes():
    """自分チーム全員の理論最大HP集合 (種族特定前の読取検証に使う)。

    型登録がなければ None (検証しない)。my_team.json更新時は再計算。
    """
    global _MY_LEGAL_MAXES
    try:
        from advisor.my_team import _load, get_my_build
        from advisor.dex import get_dex, calc_hp
        team = _load()
        if not team:
            return None
        key = tuple(sorted(team.keys()))
        if _MY_LEGAL_MAXES and _MY_LEGAL_MAXES[0] == key:
            return _MY_LEGAL_MAXES[1]
        from vision.normalize import NameResolver
        maxes = set()
        for ja in team:
            b = get_my_build(ja)
            r = _resolver_singleton().resolve_species(ja, cutoff=0.9)
            if not (b and r):
                continue
            sp = get_dex().species(r[1])
            if sp:
                maxes.add(calc_hp(sp["baseStats"]["hp"], b["ev"].get("hp", 0), 50))
        _MY_LEGAL_MAXES = (key, maxes or None)
        return _MY_LEGAL_MAXES[1]
    except Exception:
        return None


_RESOLVER = None


def _resolver_singleton():
    global _RESOLVER
    if _RESOLVER is None:
        from vision.normalize import NameResolver
        _RESOLVER = NameResolver()
    return _RESOLVER


def _expected_my_max(mon):
    """型登録 (config/my_team.json) から自分側の理論最大HPを計算する。

    OCRの一貫した桁落ち ("178"→"78") は多数決でも防げないため、
    登録された能力ポイントから計算した理論値を読取検証の基準にする。
    """
    try:
        from advisor.my_team import get_my_build
        from advisor.dex import get_dex, calc_hp
        build = get_my_build(mon.species_ja)
        if not build:
            return None
        sp = get_dex().species(mon.species_id)
        if not sp:
            return None
        return calc_hp(sp["baseStats"]["hp"], build["ev"].get("hp", 0), 50)
    except Exception:
        return None


def _resolve_ability_validated(resolver, text: str, mon):
    """特性を解決し、種族が判明していれば合法特性 (最大3択) に限定する。

    合法外の解決結果は候補内での再解決を試み、それでも合わなければ None
    (メガラグラージに「ばけのかわ」が付くような誤読・誤帰属を弾く)。
    """
    from vision.abilities import legal_abilities
    ab = resolver.resolve(text, "abilities", cutoff=0.72)
    legal = legal_abilities(mon.species_id)
    if legal is None:
        return ab
    if ab and ab[1] in legal:
        return ab
    return resolver.resolve_restricted(text, "abilities", legal)


def _set_hp(state: BattleStateV2, side_name: str, mon,
            pct=None, cur=None, mx=None) -> None:
    """HPを更新し、有意な変化をイベントログに記録する (ダメージ帰属用)。

    状態のhp_percent自体は常に最新読取で更新するが、イベント化は
    同じ値が2回連続で観測されたときのみ行う (単発の誤読でイベントが
    フラップした実績: 7%↔0%の往復など)。また回復技の上限を超える
    +60%超の増加は交代/別個体の出現とみなし、イベント化せず基準だけ移す。
    """
    old = mon.hp_percent
    raw = None
    if cur is not None and mx:
        # 自分側: 型登録済みの種族のみ理論最大HPで検証する。
        # 未登録種族 (チーム変更後にmy_team.json未更新) まで集合検証すると
        # 全読取が棄却されHPが取れなくなる (2026-07-23 監査で発見)
        if side_name == "player":
            expected = _expected_my_max(mon)
            if expected and mx != expected:
                return
            if expected is None:
                legal = _my_legal_maxes()
                try:
                    from advisor.my_team import has_build
                    registered = has_build(mon.species_ja)
                except Exception:
                    registered = False
                if registered and legal and mx not in legal:
                    return
        # 最大HPは種族ごとの多数決で確定する (「28/167」→「28/67」のような
        # 桁落ち誤読が50以上のガードを通過して定着するのを防ぐ)
        votes = state.hp_max_votes.setdefault((side_name, mon.species_ja), {})
        votes[mx] = votes.get(mx, 0) + 1
        best_mx, n = max(votes.items(), key=lambda kv: kv[1])
        if n >= 2 and mx != best_mx:
            if cur <= best_mx:
                mx = best_mx
            else:
                return
        new = round(cur / mx * 100, 1)
        raw = (cur, mx)
    elif pct is not None:
        new = float(pct)
    else:
        return

    def commit():
        mon.hp_percent = new
        if raw:
            mon.hp_current, mon.hp_max = raw

    last_read = getattr(mon, "_hp_last_read", None)
    mon._hp_last_read = new
    if old is None:
        # 初回は即反映 (アドバイスが値なしで止まらないように)
        commit()
        mon._hp_event_base = new
        mon._hp_stable_since = time.time()
        return
    if last_read is None or abs(new - last_read) > 2.0:
        mon._hp_stable_count = 1
        mon._hp_stable_since = time.time()
        return   # 1回だけの観測は状態にも反映しない (誤読の混入防止)
    mon._hp_stable_count = getattr(mon, "_hp_stable_count", 1) + 1
    # 時間安定条件: 気絶/被弾演出はHPバーが徐々に減るため、高頻度解析では
    # 遷移中の値も2回連続で読めてしまう。同値が600ms以上続いた場合のみ
    # 確定する (演出終了後の静止値だけが通る)
    if time.time() - getattr(mon, "_hp_stable_since", 0.0) < 0.6:
        return
    # ほぼ0%は交代/メガシンカ演出中の空バー誤読が多いため、3回連続観測を
    # 要求する (本物のひんし・瀕死残りなら低%表示が続くので3回目で確定する)。
    # バー由来の読取は1.4%等の端数になるため、閾値は0%だけでなく3%まで広げる
    # (実戦: メガメタグロス100%→1.x%→59%のフラップがイベント化した)
    if new <= 3.0 and mon._hp_stable_count < 3:
        return
    commit()
    # ほぼ0%への低下イベントは、ひんしメッセージの裏付けがある場合のみ発火する
    # (交代アニメの空バーが3秒以上続くと3回連続確認をすり抜けた実績。
    #  状態値の更新自体は行い、誤りなら次の確定読取で戻る)
    if new <= 3.0:
        last_faint = getattr(state, "last_faint", None)
        if not (last_faint and last_faint.get("side") == side_name
                and time.time() - last_faint.get("ts", 0) < 20.0):
            return
    base = getattr(mon, "_hp_event_base", old)
    delta = new - base
    if abs(delta) <= 2.5:
        return
    mon._hp_event_base = new
    # 交代由来の誤帰属: 回復技上限を超える増加、または全快への大幅ジャンプ
    # (対戦中に全快まで回復する手段はない) はイベント化しない
    if delta > 60 or (delta > 30 and new >= 99.5):
        return
    sign = "-" if delta < 0 else "+"
    state.log_event(
        "hp", f"{mon.species_ja or mon.display_name or '?'} "
              f"{base:.0f}%→{new:.0f}% ({sign}{abs(delta):.0f}%)",
        event_id=f"hp_{side_name}",
        detail={"side": side_name, "from": base, "to": new})


def extract_field_hp(img, state: BattleStateV2) -> None:
    """フィールドシーン (技アニメーション/メッセージ中) の軽量HP読取。

    ダメージはフィールドシーン中にHPバーへ反映されるため、コマンド画面待ちでは
    「どの技で何%減ったか」の対応付けができない。HUDバナーが見えている間だけ
    HPを読み続けることで、技イベントとHP変化イベントを時系列で対応付ける。
    """
    from vision.scenes import _crimson_ratio, _hp_bar_pixels
    # HUDが表示されているかの軽量ゲート
    if _crimson_ratio(crop(img, zones.BATTLE["opp_banner"])) < 0.15:
        return

    opp = state.opponent.active()
    if opp is not None:
        hp_text = ocr.read_zone_text(img, zones.BATTLE["opp_hp_text"], mode="panel",
                                     allowlist="0123456789%")
        pct = ocr.parse_percent(hp_text)
        bar = ocr.hp_bar_ratio(crop(img, zones.BATTLE["opp_hp_bar"]))
        if pct is not None and bar is not None and abs(pct - bar * 100) > 15:
            pct = round(bar * 100, 1)
        elif pct is None and bar is not None:
            pct = round(bar * 100, 1)
        if pct is not None:
            _set_hp(state, "opponent", opp, pct=pct)

    me = state.player.active()
    if me is not None and _hp_bar_pixels(crop(img, zones.BATTLE["my_hp_bar"])) > 30:
        my_hp = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel",
                                   allowlist="0123456789/")
        frac = ocr.parse_fraction(my_hp)
        if frac and frac[1] and frac[1] >= 50:
            cur, mx = frac
            known = _expected_my_max(me) or \
                (me.hp_max if me.hp_max and me.hp_max >= 50 else None)
            legal = _my_legal_maxes()
            try:
                from advisor.my_team import has_build
                registered = has_build(me.species_ja)
            except Exception:
                registered = False
            if known and mx != known:
                pass   # 基準と食い違う読みは捨てる (桁落ちは現在値も壊れている)
            elif known is None and registered and legal and mx not in legal:
                pass   # 登録済み種族なのに理論最大集合に無い読みは捨てる
            elif cur <= mx:
                _set_hp(state, "player", me, cur=cur, mx=mx)


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
        # 種族確定済みスロットへ「別のポケモンの名前」を上書きしない。
        # (実戦: 交代を見逃した状態でHUDの新ポケモン名が前のポケモンの
        #  display_nameに入り、イベントの名前照合が誤って一致して
        #  別ポケモンの技として記録された)
        from vision.normalize import loose_key as _lk
        import difflib as _dl
        key_new = _lk(name_text)
        cur = [_lk(x) for x in (opp.species_ja, opp.display_name) if x]
        similar = (not cur) or any(
            k and (k in key_new or key_new in k
                   or _dl.SequenceMatcher(None, key_new, k).ratio() >= 0.5)
            for k in cur)
        if similar:
            # OCR揺れの別表記をエイリアスとして蓄積 (以後の帰属照合に使う)
            if opp.display_name and opp.display_name != name_text \
                    and opp.display_name not in (opp.aliases or []):
                opp.aliases.append(opp.display_name)
                opp.aliases = opp.aliases[-6:]
            opp.display_name = name_text
        elif opp.species_ja:
            # 見逃した交代の兆候: 表示名の合う既存枠があればそちらへ切替
            idx = state.opponent.find_by_display_name(name_text)
            if idx is not None:
                state.opponent.switch_to(idx)
                opp = state.opponent.party[idx]
                opp.display_name = name_text
            else:
                state.log_event(
                    "system",
                    f"相手HUD名不一致 ({name_text}≠{opp.species_ja}) "
                    "交代見逃しの疑い", event_id="hud_name_mismatch")
    else:
        opp = state.opponent.ensure_active()
    link_active_to_party(state, "opponent")
    opp = state.opponent.ensure_active()

    # 種族未特定なら、HUDのフルカラーアイコンを候補スプライトと照合して特定する
    # (候補 = タイプ判明済みで種族未特定の相手枠から使用率推測した種族の合算)
    if not opp.species_ja:
        try:
            from advisor.infer import get_inference
            from vision.spriteid import identify_species_color
            inference = get_inference()
            cand_map: dict = {}
            for p in state.opponent.party[:6]:
                if p.species_ja or not p.types:
                    continue
                for sid, prob, ja in inference.candidates(p.types):
                    if sid not in cand_map or prob > cand_map[sid][0]:
                        cand_map[sid] = (prob, ja)
            cands = [(sid, prob, ja) for sid, (prob, ja) in cand_map.items()]
            hit = identify_species_color(crop(img, zones.BATTLE["opp_icon"]), cands)
            if hit:
                state.opponent.switch_to_species(hit[1], hit[0])
                link_active_to_party(state, "opponent")
                opp = state.opponent.ensure_active()
                state.log_event("battle_hud", f"相手を{hit[1]}と特定 (アイコン照合{hit[2]})",
                                event_id="species_identified")
        except Exception:
            pass

    hp_text = ocr.read_zone_text(img, zones.BATTLE["opp_hp_text"], mode="panel",
                                 allowlist="0123456789%")
    pct = ocr.parse_percent(hp_text)
    bar = ocr.hp_bar_ratio(crop(img, zones.BATTLE["opp_hp_bar"]))
    if pct is not None:
        # OCRとバー残量が大きく食い違う場合 (「1%」->「19」等の誤読) はバーを信用する
        if bar is not None and abs(pct - bar * 100) > 15:
            _set_hp(state, "opponent", opp, pct=round(bar * 100, 1))
        else:
            _set_hp(state, "opponent", opp, pct=float(pct))
    elif bar is not None:
        _set_hp(state, "opponent", opp, pct=round(bar * 100, 1))

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
    # 最大HPが50未満の読みは誤読とみなす (Lv50の最大HPは実質50以上。
    # 選出画面の「0/3」進捗がこのゾーンに重なって読まれる事故も弾く)
    if frac and frac[1] and frac[1] >= 50:
        cur, mx = frac
        # 最大HPは対戦中に変化しない (メガシンカでも不変)。基準値は
        # 型登録 (config/my_team.json) から計算した理論値を最優先し、
        # なければ過去の読取値を使う。基準と食い違う読みは誤OCRとみなし、
        # 桁補正できれば現在値だけ更新、できなければ読み捨てる
        # (「135/178」→「35/78」のような一貫した桁落ちは多数決でも防げない)
        known = _expected_my_max(me) or \
            (me.hp_max if me.hp_max and me.hp_max >= 50 else None)
        legal = _my_legal_maxes()
        ok = True
        if known and mx != known:
            digits = re.sub(r"\D", "", my_hp)
            ks = str(known)
            if digits.endswith(ks) and digits[:-len(ks)].isdigit() \
                    and int(digits[:-len(ks)]) <= known:
                cur = int(digits[:-len(ks)])
            else:
                # 桁落ちは現在値側も壊れていることが多い ("135/167"→"35/78")。
                # 補正できない読みは捨て、基準と一致する読みだけを採用する
                ok = False
            mx = known
        elif known is None and legal and mx not in legal:
            # 種族特定前でも、チーム全員の理論最大HP集合に無い読みは誤読
            # (「16/67」が特定前に素通りして定着する事故の防止)
            ok = False
        if ok and cur <= mx:
            _set_hp(state, "player", me, cur=cur, mx=mx)
        # 過去に定着した誤った最大HPの掃除 (理論値と食い違えば読み直しに戻す)
        if known and me.hp_max and me.hp_max != known:
            me.hp_current, me.hp_max, me.hp_percent = None, None, None

    # --- 特性が一意な種族 (単一特性/メガ) は確定値として自動設定 ---
    # メガシンカ後は特性が変わるため、メガ前に判明していた特性
    # (例: ラグラージのしめりけ) が残っていても固定特性で上書きする
    from vision.abilities import fixed_ability
    for side_name in ("player", "opponent"):
        mon = state.side(side_name).active()
        if mon is None or not mon.species_id:
            continue
        if mon.ability_id and not mon.is_mega:
            continue
        fa = fixed_ability(mon.species_id, is_mega=mon.is_mega,
                           item_id=mon.item_id)
        if fa and mon.ability_id != fa:
            mon.ability_id = fa
            mon.ability_ja = resolver.ja_of("abilities", fa) or fa

    # --- 残数ボール (緑=残り。単調減少で更新しノイズに耐える) ---
    for side_obj, zone_key in ((state.opponent, "opp_balls"),
                                (state.player, "my_balls")):
        count = ocr.count_pokeballs(crop(img, zones.BATTLE[zone_key]))
        if count is not None and 1 <= count <= 3:
            if side_obj.remaining is None or count <= side_obj.remaining:
                side_obj.remaining = count

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


_REG_MOVE_CACHE: dict = {}


def _registered_move_ids(species_ja, resolver) -> set:
    """my_team.json の登録技をshowdown ID集合で返す (キャッシュ付き)"""
    if not species_ja:
        return set()
    if species_ja in _REG_MOVE_CACHE:
        return _REG_MOVE_CACHE[species_ja]
    ids = set()
    try:
        from advisor.my_team import get_my_moves
        for ja in get_my_moves(species_ja):
            r = resolver.resolve(ja, "moves", cutoff=0.7)
            if r:
                ids.add(r[1])
    except Exception:
        pass
    _REG_MOVE_CACHE[species_ja] = ids
    return ids


def resolve_move_owner(state: BattleStateV2, move_ids: set, resolver):
    """技画面に映っている技集合から「真の場のポケモン」を特定する。

    交代直後は active_index の更新が画面より遅れることがあり、読み取った
    技が前のポケモンへ書き込まれて「交代後のポケモンの技をお勧めする」
    誤アドバイスが起きた (実戦)。登録技 (my_team.json) との一致数で
    所有者を判定し、activeと食い違えばactiveを補正する。

    戻り値: (対象のPokemonState, active修正したか)
    """
    me = state.player.ensure_active()
    if not move_ids:
        return me, False
    best = None
    for i, p in enumerate(state.player.party):
        if p.status == "fainted":
            continue
        reg = _registered_move_ids(p.species_ja, resolver)
        if not reg:
            continue
        score = len(move_ids & reg)
        if best is None or score > best[0]:
            best = (score, i, p)
    if best is None:
        return me, False
    score, idx, owner = best
    active_reg = _registered_move_ids(me.species_ja, resolver)
    active_score = len(move_ids & active_reg) if active_reg else None
    # 画面の技が別の登録ポケモンに2つ以上一致し、activeとの一致を上回る
    # 場合のみ「activeの取り違え」とみなして補正する
    if owner is not me and score >= 2 and score > (active_score or 0):
        state.player.active_index = idx
        for j, p in enumerate(state.player.party):
            p.is_active = (j == idx)
        state.log_event(
            "system",
            f"技画面照合: 場のポケモンを{owner.species_ja}に補正",
            event_id="active_fix_by_moves")
        return owner, True
    return me, False


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
        # 技の所有者を登録技と照合し、activeの取り違えがあれば補正する
        move_ids = {s.move_id for s in new_moves if s.move_id}
        me, _fixed = resolve_move_owner(state, move_ids, resolver)
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
            if frac and frac[1] and frac[1] >= 50:
                _set_hp(state, side_name, mon, cur=frac[0], mx=frac[1])
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
            ab = _resolve_ability_validated(resolver, body, mon)
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
_EN2JA_TYPES = {"Normal": "ノーマル", "Fire": "ほのお", "Water": "みず",
                "Electric": "でんき", "Grass": "くさ", "Ice": "こおり",
                "Fighting": "かくとう", "Poison": "どく", "Ground": "じめん",
                "Flying": "ひこう", "Psychic": "エスパー", "Bug": "むし",
                "Rock": "いわ", "Ghost": "ゴースト", "Dragon": "ドラゴン",
                "Dark": "あく", "Steel": "はがね", "Fairy": "フェアリー"}


def _dex_types_ja_by_id(species_id) -> Optional[set]:
    """種族IDの図鑑タイプ (日本語集合)"""
    if not species_id:
        return None
    try:
        from advisor.dex import get_dex
        sp = get_dex().species(species_id)
        if sp:
            return {_EN2JA_TYPES.get(t, t) for t in sp["types"]}
    except Exception:
        pass
    return None


def _dex_types_ja(mon) -> Optional[set]:
    """自分の個体の図鑑タイプ (日本語集合)。メガ済みならメガ後の姿で引く"""
    sid = mon.species_id
    if sid and mon.is_mega:
        try:
            from advisor.dex import get_dex
            if get_dex().species(sid + "mega"):
                sid = sid + "mega"
        except Exception:
            pass
    return _dex_types_ja_by_id(sid)


_LIME_LOW = np.array([30, 100, 120])
_LIME_HIGH = np.array([50, 255, 255])
_STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")


def _lime_ratio(region) -> float:
    if region is None or region.size == 0:
        return 0.0
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _LIME_LOW, _LIME_HIGH)
    return cv2.countNonZero(mask) / float(region.shape[0] * region.shape[1])


def _watch_active_tab(img) -> str:
    """もっと見るパネルの選択中タブ (ライム色ピルの左右位置で判定)"""
    tb = zones.WATCH["tab_bar"]
    mid = (tb["x0"] + tb["x1"]) / 2.0
    left = _lime_ratio(crop(img, {**tb, "x1": mid}))
    right = _lime_ratio(crop(img, {**tb, "x0": mid}))
    return "status" if right > left else "ability"


def _highlight_species(img, resolver):
    """もっと見る画面で左列のハイライト行 (カーソル対象) の種族を読む。

    選出画面と交代画面で行ピッチが異なるため両ゾーン群を走査し、
    ライム比率が最大の行の名前をOCRして種族に解決する。
    """
    best_r, best_zone = 0.0, None
    for zset in (zones.SELECTION_MY, zones.WATCH_MY):
        for z in zset:
            r = _lime_ratio(crop(img, z["panel"]))
            if r > best_r:
                best_r, best_zone = r, z
    if best_zone is None or best_r < 0.10:
        return None
    name = ocr.read_zone_text(img, best_zone["name"], mode="panel",
                              allowlist=ocr.KATAKANA_ALLOWLIST)
    if not name:
        return None
    return resolver.resolve_species(name, cutoff=0.7)


def _extract_watch_status(img, state: BattleStateV2, resolver) -> None:
    """ステータスタブ: 実数値/能力ポイント/性格を読み取り、種族値からの
    理論値と全6ステータスが一致した場合のみ my_team へ型を保存する。

    もっと見る画面 (選出/交代) からの自パーティ登録の自動化。理論値照合を
    通った読取だけを保存するため、OCR誤読・誤帰属で誤った型は登録されない。
    """
    sp = _highlight_species(img, resolver)
    if sp is None:
        me = state.player.ensure_active()
        if me is not None and me.species_ja:
            sp = (me.species_ja, me.species_id)
    if sp is None or not sp[1]:
        return

    values, ocr_points = {}, {}
    for key, z in zip(_STAT_KEYS, zones.WATCH_STATS):
        v = ocr.read_zone_text(img, z["value"], mode="panel",
                               allowlist="0123456789")
        if not v or not v.isdigit():
            return   # 実数値6行すべて読めた場合のみ扱う
        values[key] = int(v)
        p = ocr.read_zone_text(img, z["points"], mode="panel",
                               allowlist="0123456789")
        # ポイント列は1桁の小さな文字でOCRが落ちやすい (実測: 0/1/2が空読み)。
        # 読めた場合の照合にのみ使い、確定値は実数値からの逆算で決める
        ocr_points[key] = int(p) if p and p.isdigit() else None

    from advisor.my_team import (nature_multipliers, nature_names_ja,
                                 update_build)
    nature_text = ocr.read_zone_text(img, zones.WATCH["nature_value"],
                                     mode="panel")
    match = difflib.get_close_matches(nature_text or "", nature_names_ja(),
                                      n=1, cutoff=0.6)
    if not match:
        return
    nature = match[0]

    try:
        from advisor.dex import calc_hp, calc_stat, get_dex
        base = (get_dex().species(sp[1]) or {}).get("baseStats") or {}
    except Exception:
        return
    if not base:
        return

    # 能力ポイントの逆算: Lv50では実数値がポイントに対して単調増加のため
    # 一意に定まる。逆算値が存在しない実数値 (誤読/種族違い) は保存しない
    mults = nature_multipliers(nature) or {}
    points = {}
    for key in _STAT_KEYS:
        cands = []
        for p in range(33):
            ev = min(252, p * 8)
            calc = calc_hp(base.get("hp", 0), ev, 50) if key == "hp" else \
                calc_stat(base.get(key, 0), ev, mults.get(key, 1.0), 50)
            if calc == values[key]:
                cands.append(p)
        op = ocr_points[key]
        if op is not None and op in cands:
            points[key] = op
        elif cands:
            # 下降補正 (×0.9) では隣接ポイントが同じ実数値になり得る
            # (例: ペリッパー攻撃63はp0/p1両方)。実数値が同じ=計算上
            # 等価なので最小値を採る
            points[key] = min(cands)
        else:
            state.log_event(
                "system", f"もっと見る不一致: {sp[0]} {key} 実{values[key]} "
                f"(逆算候補なし OCR{op}) 保存見送り", event_id=None)
            return

    if update_build(sp[0], {"能力ポイント": points, "性格": nature}):
        pts = " ".join(f"{k}{v}" for k, v in points.items() if v)
        state.log_event("system", f"my_team更新: {sp[0]} {nature} {pts}",
                        event_id=None)


def _watch_target(state: BattleStateV2, resolver, found_types, new_moves):
    """様子見画面に表示されている個体を特定する。

    この画面はカーソルを合わせた任意のポケモンを表示するため、
    activeへ無条件に書き込むと「ブリジュラスの詳細を見た瞬間に
    場のラグラージがドラゴンタイプになる」汚染が起きた (実戦)。
    技 (登録技との一致) → タイプ (図鑑タイプとの一致) の順で特定し、
    特定できなければ None (書き込まない)
    """
    party = state.player.party
    # 1) 表示中の技4つと登録技の一致数で特定
    ids = {s.move_id for s in new_moves if s.move_id}
    if len(ids) >= 2:
        scored = []
        for p in party:
            reg = _registered_move_ids(p.species_ja, resolver)
            if reg:
                scored.append((len(ids & reg), p))
        if scored:
            scored.sort(key=lambda x: -x[0])
            if scored[0][0] >= 2 and (len(scored) == 1
                                      or scored[0][0] > scored[1][0]):
                return scored[0][1]
    # 2) 表示タイプが図鑑タイプと一致する個体が一意なら特定
    if found_types:
        matches = [p for p in party
                   if _dex_types_ja(p) == set(found_types)]
        if len(matches) == 1:
            return matches[0]
    # 3) タイプ未読取ならactive (従来挙動)、読めたのに誰とも一致しない
    #    場合は書き込まない (誤読 or 相手の詳細画面)
    if not found_types:
        return state.player.ensure_active()
    active = state.player.ensure_active()
    if _dex_types_ja(active) == set(found_types):
        return active
    return None


def extract_watch(img, state: BattleStateV2, resolver) -> None:
    """様子を見る/もっと見る画面。中央パネルはタブで内容が変わる:
    能力タブ=技/特性/持ち物、ステータスタブ=実数値/能力ポイント/性格"""
    if _watch_active_tab(img) == "status":
        _extract_watch_status(img, state, resolver)
    else:
        _extract_watch_ability(img, state, resolver)
    _extract_watch_side_columns(img, state, resolver)


def _extract_watch_ability(img, state: BattleStateV2, resolver) -> None:
    # タイプ (テキスト表記)
    type_text = ocr.read_zone_text(img, zones.WATCH["type_row"], mode="panel")
    found = []
    if type_text:
        for jp in ("ノーマル", "ほのお", "みず", "でんき", "くさ", "こおり", "かくとう",
                    "どく", "じめん", "ひこう", "エスパー", "むし", "いわ", "ゴースト",
                    "ドラゴン", "あく", "はがね", "フェアリー"):
            from vision.normalize import loose_key
            if loose_key(jp) in loose_key(type_text):
                found.append(jp)

    # 技 + PP: 先に読み取り、持ち主特定に使う
    new_moves = []
    for i, row in enumerate(zones.WATCH_MOVES):
        name_text = ocr.read_zone_text(img, row["name"], mode="panel")
        if not name_text:
            continue
        mv = resolver.resolve(name_text, "moves", cutoff=0.7)
        if mv is None:
            continue
        pp = _read_pp(img, row["pp"])
        slot = MoveSlot(name_ja=mv[0], move_id=mv[1])
        if pp:
            slot.pp, slot.max_pp = pp[0], pp[1]
        new_moves.append(slot)

    # 表示中の個体を特定してから書き込む (誤帰属防止)。
    # 状態未構築 (選出中のもっと見る等) ではハイライト行の名前で特定する
    me = _watch_target(state, resolver, found, new_moves)
    build_species = me.species_ja if me is not None else None
    if me is None:
        hl = _highlight_species(img, resolver)
        if hl is not None and found and \
                _dex_types_ja_by_id(hl[1]) == set(found):
            build_species = hl[0]   # my_team登録のみ行う (状態へは書かない)
        else:
            state.log_event("system", f"様子見画面の帰属不能 (タイプ={found})",
                            event_id=None)
            return

    # 特性 / 持ち物
    ability_ja, item_ja = None, None
    ability_text = ocr.read_zone_text(img, zones.WATCH["ability_value"], mode="panel")
    if ability_text:
        if me is not None:
            ab = _resolve_ability_validated(resolver, ability_text, me)
        else:
            ab = resolver.resolve(ability_text, "abilities", cutoff=0.72)
        if ab:
            ability_ja = ab[0]
            if me is not None:
                me.ability_ja, me.ability_id = ab[0], ab[1]
    item_text = ocr.read_zone_text(img, zones.WATCH["item_value"], mode="panel")
    if item_text:
        it = resolver.resolve(item_text, "items", cutoff=0.72)
        if it:
            item_ja = it[0]
            if me is not None:
                me.item_ja, me.item_id = it[0], it[1]

    if found and me is not None:
        me.types = found

    if len(new_moves) >= 3 and me is not None:
        # 既存エントリのPP情報は引き継ぐ
        old = {m.move_id or m.name_ja: m for m in me.moves}
        for slot in new_moves:
            prev = old.get(slot.move_id or slot.name_ja)
            if prev:
                slot.pp = slot.pp if slot.pp is not None else prev.pp
                slot.max_pp = slot.max_pp or prev.max_pp
                slot.effectiveness = prev.effectiveness
        me.moves = new_moves

    # 自パーティ登録 (my_team) の自動更新: 技4つが読めた場合のみ技を保存
    if build_species:
        from advisor.my_team import update_build
        patch = {"特性": ability_ja, "持ち物": item_ja}
        if len(new_moves) == 4:
            patch["技"] = [m.name_ja for m in new_moves]
        if update_build(build_species, patch):
            state.log_event("system",
                            f"my_team更新: {build_species} (能力タブ)",
                            event_id=None)


def _extract_watch_side_columns(img, state: BattleStateV2, resolver) -> None:
    # 左列: 自分の選出パーティのHP実数値
    for i, z in enumerate(zones.WATCH_MY[:3]):
        name_text = ocr.read_zone_text(img, z["name"], mode="panel",
                                       allowlist=ocr.KATAKANA_ALLOWLIST)
        hp_text = ocr.read_zone_text(img, z["hp"], mode="panel",
                                     allowlist="0123456789/")
        frac = ocr.parse_fraction(hp_text)
        if not name_text or not frac or not frac[1] or frac[1] < 50:
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
        _set_hp(state, "player", mon, cur=frac[0], mx=frac[1])
        if frac[0] == 0 and mon.hp_current == 0:
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
