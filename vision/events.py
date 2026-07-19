"""バトルメッセージ / ポップアップのイベント解析。

ゲーム内メッセージ (白文字・縁取り) をOCRした結果から、
天候・フィールド・設置技・壁・状態異常・ランク変化・交代・メガシンカ・
技使用・特性発動などを検出して BattleStateV2 に反映する。

メッセージは漢字混じり (例: 「砂あらしが 吹き始めた!」「相手の 特攻が
がくっと下がった!」) で、OCRの誤読も起きるため、キーワードは
「ORグループのAND」構造 + loose_key (清音化・小書き通常化・長音除去) で比較する。

    "keywords": [["砂あらし", "すなあらし"], ["吹き始め", "ふきはしめ"]]
    -> (砂あらし OR すなあらし) AND (吹き始め OR ふきはしめ)
"""
from __future__ import annotations

import re
import time
from typing import Optional

from vision.normalize import loose_key, normalize
from vision.state import BattleStateV2

# ==============================================================================
# キーワードイベント定義
# ==============================================================================
SIMPLE_EVENTS = [
    # --- 天候 ---
    {"id": "sun_start", "keywords": [["日差し", "日さし", "ひさし", "日ざし"], ["強く", "つよく"]],
     "action": "weather", "value": "sun"},
    {"id": "rain_start", "keywords": [["雨", "あめか"], ["降り始め", "ふりはしめ"]],
     "action": "weather", "value": "rain"},
    {"id": "sand_start", "keywords": [["砂あらし", "すなあらし", "砂嵐"], ["始め", "はしめ"]],
     "action": "weather", "value": "sandstorm"},
    {"id": "snow_start", "keywords": [["雪", "ゆきか"], ["降り始め", "ふりはしめ"]],
     "action": "weather", "value": "snow"},
    {"id": "sun_end", "keywords": [["日差し", "ひさし", "日ざし"], ["元に戻っ", "もとにもとつ"]],
     "action": "weather", "value": None},
    {"id": "rain_end", "keywords": [["雨", "あめか"], ["やんだ", "やんた", "上がった"]],
     "action": "weather", "value": None},
    {"id": "sand_end", "keywords": [["砂あらし", "すなあらし"], ["おさまっ", "収まっ"]],
     "action": "weather", "value": None},
    {"id": "snow_end", "keywords": [["雪", "ゆきか"], ["やんだ", "やんた"]],
     "action": "weather", "value": None},

    # --- フィールド ---
    {"id": "electric_terrain", "keywords": [["足元", "あしもと", "足下"], ["電気", "てんき"]],
     "action": "terrain", "value": "electric"},
    {"id": "grassy_terrain", "keywords": [["足元", "あしもと"], ["草", "くさか"], ["茂っ", "しけつ"]],
     "action": "terrain", "value": "grassy"},
    {"id": "psychic_terrain", "keywords": [["足元", "あしもと"], ["不思議", "ふしき"]],
     "action": "terrain", "value": "psychic"},
    {"id": "misty_terrain", "keywords": [["足元", "あしもと"], ["霧", "きりか"]],
     "action": "terrain", "value": "misty"},
    {"id": "terrain_end", "keywords": [["足元", "あしもと"], ["消え", "きえ", "元に戻っ", "もとにもとつ"]],
     "action": "terrain", "value": None},

    # --- トリックルーム / じゅうりょく / おいかぜ ---
    {"id": "trickroom_start", "keywords": [["時空", "しくう"], ["作り", "ゆかめ", "歪め"]],
     "action": "trickroom", "value": True},
    {"id": "trickroom_end", "keywords": [["時空", "しくう"], ["元に戻っ", "もとにもとつ"]],
     "action": "trickroom", "value": False},
    {"id": "gravity_start", "keywords": [["重力", "しゆうりよく"], ["強く", "つよく"]],
     "action": "gravity", "value": True},
    {"id": "gravity_end", "keywords": [["重力", "しゆうりよく"], ["元に戻っ", "もとにもとつ"]],
     "action": "gravity", "value": False},
    {"id": "tailwind_start", "keywords": [["追い風", "おいかせ"], ["吹き始め", "ふきはしめ"]],
     "action": "side_flag", "key": "tailwind", "value": True},
    {"id": "tailwind_end", "keywords": [["追い風", "おいかせ"], ["やんだ", "やんた"]],
     "action": "side_flag", "key": "tailwind", "value": False},

    # --- 設置技 (効果メッセージ側) ---
    {"id": "stealthrock_set",
     "keywords": [["とがった岩", "とかつたいわ", "尖った岩"], ["浮かび", "うかひ", "漂い", "ただよい"]],
     "action": "hazard", "key": "stealth_rock", "value": True},
    {"id": "spikes_set", "keywords": [["まきびし", "まきひし", "撒きびし"], ["散らば", "ちらは"]],
     "action": "hazard_add", "key": "spikes", "max": 3},
    {"id": "toxicspikes_set", "keywords": [["どくびし", "とくひし", "毒びし"], ["散らば", "ちらは"]],
     "action": "hazard_add", "key": "toxic_spikes", "max": 2},
    {"id": "stickyweb_set",
     "keywords": [["ねばねばネット", "ねはねはねつと"], ["張り巡", "はりめく"]],
     "action": "hazard", "key": "sticky_web", "value": True},
    {"id": "stealthrock_off",
     "keywords": [["とがった岩", "尖った岩"], ["消え", "きえ"]],
     "action": "hazard", "key": "stealth_rock", "value": False},

    # --- 壁 ---
    {"id": "reflect_start", "keywords": [["物理攻撃", "ふつりこうけき"], ["強く", "つよく"]],
     "action": "side_flag", "key": "reflect", "value": True},
    {"id": "lightscreen_start", "keywords": [["特殊攻撃", "とくしゆこうけき"], ["強く", "つよく"]],
     "action": "side_flag", "key": "light_screen", "value": True},
    {"id": "reflect_end", "keywords": [["リフレクター", "りふれくた"], ["なくなっ", "無くなっ"]],
     "action": "side_flag", "key": "reflect", "value": False},
    {"id": "lightscreen_end", "keywords": [["光の壁", "ひかりのかへ"], ["なくなっ", "無くなっ"]],
     "action": "side_flag", "key": "light_screen", "value": False},
    {"id": "auroraveil_end", "keywords": [["オーロラベール", "おろらへる"], ["なくなっ", "無くなっ"]],
     "action": "side_flag", "key": "aurora_veil", "value": False},

    # --- 状態異常 ---
    {"id": "toxic_on", "keywords": [["猛毒", "もうとく"]], "action": "status", "value": "toxic"},
    {"id": "poison_on", "keywords": [["毒", "とくを"], ["あび", "浴び", "受け"]],
     "action": "status", "value": "poison"},
    {"id": "burn_on", "keywords": [["やけど", "火傷"], ["負っ", "おつ"]],
     "action": "status", "value": "burn"},
    {"id": "paralysis_on", "keywords": [["まひして", "麻痺して", "しびれて"]],
     "action": "status", "value": "paralysis"},
    {"id": "sleep_on", "keywords": [["眠って", "ねむつて"], ["しまっ"]],
     "action": "status", "value": "sleep"},
    {"id": "freeze_on", "keywords": [["凍りつ", "こおりつ"]], "action": "status", "value": "freeze"},
    {"id": "drowsy_on", "keywords": [["眠くな", "ねむくな", "ねむけ"]],
     "action": "status", "value": "drowsy"},
    {"id": "wake_up", "keywords": [["目を覚まし", "めをさまし"]], "action": "status", "value": None},
    {"id": "thaw", "keywords": [["凍り", "こおり"], ["とけた", "溶けた", "解けた"]],
     "action": "status", "value": None},
    {"id": "poison_cured", "keywords": [["毒", "とくか"], ["治っ", "なおつ"]],
     "action": "status", "value": None},
    {"id": "burn_cured", "keywords": [["やけど", "火傷"], ["治っ", "なおつ"]],
     "action": "status", "value": None},
    {"id": "faint", "keywords": [["倒れた", "たおれた"]], "action": "faint"},

    # --- 揮発性状態 ---
    {"id": "confusion_on", "keywords": [["混乱し", "こんらんし"]],
     "action": "volatile", "key": "confusion", "value": True},
    {"id": "confusion_off", "keywords": [["混乱", "こんらん"], ["とけた", "解けた", "治っ"]],
     "action": "volatile", "key": "confusion", "value": False},
    {"id": "substitute_on", "keywords": [["身代わり", "みかわり"], ["現れ", "あらわれ"]],
     "action": "volatile", "key": "substitute", "value": True},
    {"id": "substitute_off", "keywords": [["身代わり", "みかわり"], ["消え", "きえ"]],
     "action": "volatile", "key": "substitute", "value": False},
    {"id": "leechseed_on", "keywords": [["種", "たね"], ["植えつけ", "うえつけ"]],
     "action": "volatile", "key": "leechseed", "value": True},
    {"id": "taunt_on", "keywords": [["挑発", "ちようはつ"], ["乗っ", "のつて"]],
     "action": "volatile", "key": "taunt", "value": True},
    {"id": "taunt_off", "keywords": [["挑発", "ちようはつ"], ["とけた", "解けた"]],
     "action": "volatile", "key": "taunt", "value": False},
    {"id": "encore_on", "keywords": [["アンコール", "あんこる"]],
     "action": "volatile", "key": "encore", "value": True},
    {"id": "saltcure_on", "keywords": [["塩漬け", "しおつけ"]],
     "action": "volatile", "key": "saltcure", "value": True},
    {"id": "attract_on", "keywords": [["メロメロ", "めろめろ"]],
     "action": "volatile", "key": "attract", "value": True},
    {"id": "yawn_on", "keywords": [["あくび", "欠伸"]],
     "action": "volatile", "key": "yawn", "value": True},
    {"id": "flinch", "keywords": [["ひるんで", "怯んで"]],
     "action": "volatile", "key": "flinch", "value": True},
    {"id": "bound_on", "keywords": [["締めつけ", "しめつけ"]],
     "action": "volatile", "key": "bind", "value": True},
    {"id": "perish_song", "keywords": [["滅びの歌", "ほろひのうた"]],
     "action": "volatile", "key": "perishsong", "value": True},

    # --- メガシンカ ---
    {"id": "mega_evolve", "keywords": [["メガシンカ", "めかしんか"]], "action": "mega"},

    # --- 勝敗 (「〜との勝負に勝った!」) ---
    {"id": "battle_win", "keywords": [["勝負", "しようふ"], ["勝った", "かつた"]],
     "action": "battle_end", "value": "win"},
    {"id": "battle_lose", "keywords": [["勝負", "しようふ"], ["負けた", "まけた"]],
     "action": "battle_end", "value": "loss"},

    # --- 持ち物 ---
    {"id": "leftovers_heal", "keywords": [["たべのこし", "食べ残し"], ["回復", "かいふく"]],
     "action": "item", "value": "たべのこし"},
    {"id": "sash_endure", "keywords": [["きあいのタスキ", "気合いのタスキ"], ["こらえ", "堪え"]],
     "action": "item", "value": "きあいのタスキ"},
    {"id": "lifeorb_recoil", "keywords": [["いのちのたま", "命の珠"], ["削られ", "けすられ"]],
     "action": "item", "value": "いのちのたま"},

    # --- 効果表示 / その他 (ログのみ) ---
    {"id": "protect_success", "keywords": [["攻撃から", "こうけきから"], ["守っ", "まもつ"]], "action": "noop"},
    {"id": "super_effective", "keywords": [["効果", "こうか"], ["ばつぐん", "抜群"]], "action": "noop"},
    {"id": "not_very_effective", "keywords": [["効果", "こうか"], ["いまひとつ", "今ひとつ"]], "action": "noop"},
    {"id": "no_effect", "keywords": [["効果", "こうか"], ["ないようだ", "無いようだ"]], "action": "noop"},
    {"id": "missed", "keywords": [["攻撃", "こうけき"], ["当たらな", "あたらな"]], "action": "noop"},
    {"id": "critical", "keywords": [["急所", "きゆうしよ", "キュウショ"]], "action": "noop"},
]

# ランク変化: (loose_keyパターン, 変化量)。長いパターンから順に判定する
RANK_CHANGES = [
    ("さいたいまてあかつ", +6), ("最大まて上かつ", +6),
    ("くくんとあかつ", +3), ("くくんと上かつ", +3),
    ("くんとあかつ", +2), ("くんと上かつ", +2),
    ("かくんとさかつ", -3), ("かくんと下かつ", -3),
    ("かくつとさかつ", -2), ("かくつと下かつ", -2),
    ("あかつ", +1), ("上かつ", +1),
    ("さかつ", -1), ("下かつ", -1),
]

# 注意: 「特防」「特攻」を先に判定する (「防」「攻」単体の誤マッチ防止)
STAT_NAMES = {
    "特攻": "spa", "とくこう": "spa", "特効": "spa", "特功": "spa",
    "特防": "spd", "とくほう": "spd",
    "攻撃": "atk", "こうけき": "atk", "攻繋": "atk",
    "防御": "def", "ほうきよ": "def", "防櫓": "def", "防禦": "def", "防衛": "def",
    "素早さ": "spe", "すはやさ": "spe", "素早": "spe",
    "命中率": "acc", "めいちゆうりつ": "acc", "命中": "acc",
    "回避率": "eva", "かいひりつ": "eva", "回避": "eva",
}


def _match_keywords(norm: str, groups: list) -> bool:
    """ORグループのAND判定"""
    for group in groups:
        if not any(loose_key(alt) in norm for alt in group):
            return False
    return True


class EventParser:
    def __init__(self, state: BattleStateV2, resolver):
        self.state = state
        self.resolver = resolver
        self._recent_fired: dict = {}   # event_id -> 最終発火時刻

    def _dedup(self, event_id: str) -> bool:
        """同一イベントIDの3秒以内の再発火を抑止する。

        同じメッセージがOCR揺れで微妙に異なるテキストとして複数回読まれると、
        テキスト単位のデデュープを通過して同じイベントが連発する
        (とんぼがえり×4等)。まきびし等は効果が二重適用されるため実害がある。
        再観測時は時刻を更新し、メッセージ表示が続く限り窓を延長する。
        """
        now = time.time()
        last = self._recent_fired.get(event_id)
        self._recent_fired[event_id] = now
        return last is not None and now - last < 3.0

    # --------------------------------------------------------------
    def _is_opponent_text(self, cleaned: str) -> Optional[bool]:
        norm = loose_key(cleaned)
        head = norm[:10]
        if "相手の" in head or "あいての" in head:
            return True
        for side_name, is_opp in (("player", False), ("opponent", True)):
            side = self.state.side(side_name)
            for mon in side.party:
                for cand in (mon.species_ja, mon.display_name):
                    ck = loose_key(cand) if cand else ""
                    if ck and len(ck) >= 3 and norm.startswith(ck[:3]):
                        return is_opp
        return None

    def _target_side(self, cleaned: str, source: str) -> str:
        if source == "left_popup":
            return "player"
        if source == "right_popup":
            return "opponent"
        is_opp = self._is_opponent_text(cleaned)
        if is_opp is None:
            return "player"
        return "opponent" if is_opp else "player"

    def _target_mon(self, cleaned: str, source: str):
        side_name = self._target_side(cleaned, source)
        side = self.state.side(side_name)
        # メッセージに名前が含まれる個体を特定できればそれを対象にする
        norm = loose_key(cleaned)
        head = norm[:14]
        for mon in side.party:
            for cand in (mon.species_ja, mon.display_name):
                ck = loose_key(cand) if cand else ""
                if ck and len(ck) >= 3 and ck in head:
                    return side_name, side, mon
        return side_name, side, side.ensure_active()

    # --------------------------------------------------------------
    def parse(self, raw_text: str, source: str = "message") -> list:
        """メッセージを解析し、発火したイベントIDのリストを返す"""
        if not raw_text:
            return []
        cleaned = re.sub(r"[^ぁ-んァ-ン一-龥a-zA-Z0-9ー%!!]", "", raw_text)
        if len(cleaned) < 2:
            return []
        if cleaned == self.state.last_texts.get(source):
            return []
        self.state.last_texts[source] = cleaned

        fired = []
        norm = loose_key(cleaned)

        # リザルト/ランク表示 (「ランクIV レート1602」等) はバトルイベントではないので無視
        if re.search(r"(ランク|らんく).{0,4}(レート|れーと)|レート\d{3,}|ボール級", cleaned):
            self.state.log_event(source, cleaned, event_id=None)
            return []

        # 1. 交代 (繰り出した / ゆけっ)
        self._parse_switch(cleaned, norm, fired)

        # 2. キーワードイベント (連結メッセージ「急所に当たった!効果は〜」等の
        #    ため、最初の1件で打ち切らず合致した全イベントを発火する)
        for ev in SIMPLE_EVENTS:
            if _match_keywords(norm, ev["keywords"]) and not self._dedup(ev["id"]):
                self._apply(ev, cleaned, source)
                fired.append(ev["id"])

        # 3. ランク変化
        if not any(not f.startswith("switch") for f in fired):
            rc = self._parse_rank_change(cleaned, norm, source)
            if rc:
                fired.append(rc)

        # 4. 技使用 / 特性発動 ("{名前}の {技/特性}")
        # 相手の判明技の収集が重要なため、他イベントと複合したメッセージ
        # (「相手のXのわざ! 効果は〜」等) でも技解析は常に試みる。
        # 交代 (「〜を繰り出した」) は種族名が技に誤マッチしやすいので除外
        if not any(f.startswith("switch") for f in fired):
            mu = self._parse_move_or_ability(cleaned, norm, source,
                                             apply_effects=not fired)
            if mu and mu not in fired:
                fired.append(mu)
            elif mu is None and source == "message":
                # 「の」区切りで解決できない複合文: 文中の技名を部分一致で探す
                found = self.resolver.find_in_text(cleaned, "moves", min_len=5)
                if found:
                    side_name, _side, mon = self._target_mon(cleaned, source)
                    if (side_name == "opponent" and found[0] not in mon.revealed_moves
                            and not self._dedup(f"move_{side_name}_{found[1]}")):
                        mon.revealed_moves.append(found[0])
                        fired.append(f"move_{side_name}_{found[1]}")

        self.state.log_event(source, cleaned, event_id=",".join(fired) or None)
        return fired

    # --------------------------------------------------------------
    def _parse_switch(self, cleaned: str, norm: str, fired: list) -> bool:
        if not any(k in norm for k in ("繰り出", "くりたし", "くりた", "ゆけつ")):
            return False
        sp = self.resolver.find_species_in_text(cleaned, cutoff=0.7)
        if not sp:
            return False
        jp, sid, _ = sp
        if self.state.player.find_by_species(jp) is not None:
            side, side_name = self.state.player, "player"
        else:
            side, side_name = self.state.opponent, "opponent"
        if self._dedup(f"switch_{side_name}_{sid}"):
            return True
        side.switch_to_species(jp, sid)
        from vision.extractors import link_active_to_party
        link_active_to_party(self.state, side_name)
        fired.append(f"switch_{side_name}")
        return True

    # --------------------------------------------------------------
    def _parse_rank_change(self, cleaned: str, norm: str, source: str) -> Optional[str]:
        stat = None
        for jp, key in STAT_NAMES.items():
            if loose_key(jp) in norm:
                stat = key
                break
        if stat is None:
            return None
        for pat, delta in RANK_CHANGES:
            if loose_key(pat) in norm:
                side_name, side, mon = self._target_mon(cleaned, source)
                event_id = f"boost_{side_name}_{stat}_{delta:+d}"
                if self._dedup(event_id):
                    return None   # OCR揺れの再読でランクを二重適用しない
                mon.set_boost(stat, delta)
                return event_id
        return None

    # --------------------------------------------------------------
    def _parse_move_or_ability(self, cleaned: str, norm: str, source: str,
                               apply_effects: bool = True) -> Optional[str]:
        """「{名前}の {技 or 特性}!」形式。「の」区切り候補を右から試す"""
        body = re.sub(r"[!!]+$", "", cleaned)
        positions = [m.start() for m in re.finditer("の", body)]
        if not positions:
            return None

        if source in ("left_popup", "right_popup"):
            return self._parse_popup(cleaned, source)

        side_name, side, mon = self._target_mon(cleaned, source)
        best = None
        for pos in reversed(positions):
            tail = body[pos + 1:]
            if len(normalize(tail)) < 2:
                continue
            r = self.resolver.resolve(tail, "moves", cutoff=0.72)
            if r and (best is None or r[2] > best[0][2]):
                best = (r, tail)
            # 「りゅうのはどう」のように技名自体に「の」を含むケース:
            # 右端の区切りで解決できなければ左側の区切りも試す (ループ継続)

        if not best:
            return None
        r, _ = best

        event_id = f"move_{side_name}_{r[1]}"
        if self._dedup(event_id):
            return None   # OCR揺れの再読 (まきびし等の効果二重適用を防ぐ)
        if side_name == "opponent" and r[0] not in mon.revealed_moves:
            mon.revealed_moves.append(r[0])
        if apply_effects:
            # 他イベントが既に発火している場合は場への効果を二重適用しない
            self._apply_move_side_effect(r[1], side_name)
        return event_id

    def _popup_mon(self, name_part: str, default_side: str):
        """ポップアップの名前部分から帰属先の個体を決める。

        名前が照合できないのにアクティブ個体へフォールバックすると、発動して
        いないポケモンに他個体の特性が付く誤帰属が起きる (実戦で観測)。
        名前で確認できた場合のみ帰属し、できなければ None (帰属なし) を返す。
        """
        name_key = loose_key(re.sub(r"^(相手の|あいての)", "", name_part))
        if len(name_key) < 3:
            return None, None
        # 1. 両サイドのパーティから名前一致 (逆サイド誤りにも耐える)
        for side_name in (default_side,
                          "opponent" if default_side == "player" else "player"):
            for mon in self.state.side(side_name).party:
                for cand in (mon.species_ja, mon.display_name):
                    ck = loose_key(cand) if cand else ""
                    if ck and len(ck) >= 3 and (ck in name_key or name_key in ck):
                        return side_name, mon
        # 2. 種族名として解決できるなら、未特定のアクティブ個体の特定に使う
        sp = self.resolver.resolve_species(name_part, cutoff=0.8)
        if sp:
            active = self.state.side(default_side).ensure_active()
            if active.species_ja is None:
                active.merge_species(sp[0], sp[1])
                return default_side, active
        return None, None

    def _parse_popup(self, cleaned: str, source: str) -> Optional[str]:
        """ポップアップ「{名前}の {特性 or 持ち物}」: 特性優先、次に持ち物"""
        body = re.sub(r"[!!]+$", "", cleaned)
        positions = [m.start() for m in re.finditer("の", body)]
        if not positions:
            return None
        default_side = self._target_side(cleaned, source)
        for pos in reversed(positions):
            tail = body[pos + 1:]
            if len(normalize(tail)) < 2:
                continue
            ab = self.resolver.resolve(tail, "abilities", cutoff=0.72)
            it = None if ab else self.resolver.resolve(tail, "items", cutoff=0.75)
            if not ab and not it:
                continue
            # 技名としての一致が上回る場合は特性/持ち物ではない
            # (「どくびし」が特性どくしゅ/持ち物どくけしに曖昧マッチする等)。
            # 技の効果適用はメッセージ側の解析に任せ、ここでは何もしない
            mv = self.resolver.resolve(tail, "moves", cutoff=0.72)
            if mv and mv[2] > (ab or it)[2]:
                self.state.log_event(source, cleaned, event_id=None)
                return None
            side_name, mon = self._popup_mon(body[:pos], default_side)
            if mon is None:
                # 帰属先が確認できない発動情報は捨てる (誤帰属より安全)
                self.state.log_event(source, cleaned, event_id=None)
                return None
            event_id = (f"ability_{side_name}_{ab[1]}" if ab
                        else f"item_{side_name}_{it[1]}")
            if self._dedup(event_id):
                return None
            if ab:
                mon.ability_ja, mon.ability_id = ab[0], ab[1]
                self._apply_ability_effect(ab[1], side_name)
            else:
                mon.item_ja, mon.item_id = it[0], it[1]
            return event_id
        return None

    # --------------------------------------------------------------
    def _apply_ability_effect(self, ability_id: str, side_name: str):
        weather_map = {"drought": "sun", "drizzle": "rain",
                       "sandstream": "sandstorm", "snowwarning": "snow",
                       "orichalcumpulse": "sun"}
        terrain_map = {"electricsurge": "electric", "grassysurge": "grassy",
                       "psychicsurge": "psychic", "mistysurge": "misty",
                       "hadronengine": "electric"}
        if ability_id in weather_map:
            self.state.field.weather = weather_map[ability_id]
            self.state.field.weather_turns = 5
        elif ability_id in terrain_map:
            self.state.field.terrain = terrain_map[ability_id]
            self.state.field.terrain_turns = 5
        elif ability_id == "intimidate":
            other = "opponent" if side_name == "player" else "player"
            self.state.side(other).ensure_active().set_boost("atk", -1)

    def _apply_move_side_effect(self, move_id: str, user_side: str):
        target = "opponent" if user_side == "player" else "player"
        tside = self.state.side(target)
        uside = self.state.side(user_side)
        if move_id == "stealthrock":
            tside.stealth_rock = True
        elif move_id == "spikes":
            tside.spikes = min(3, tside.spikes + 1)
        elif move_id == "toxicspikes":
            tside.toxic_spikes = min(2, tside.toxic_spikes + 1)
        elif move_id == "stickyweb":
            tside.sticky_web = True
        elif move_id == "reflect":
            uside.reflect = True
        elif move_id == "lightscreen":
            uside.light_screen = True
        elif move_id == "auroraveil":
            uside.aurora_veil = True
        elif move_id == "tailwind":
            uside.tailwind = True
        elif move_id == "trickroom":
            self.state.field.trick_room = not self.state.field.trick_room
            self.state.field.trick_room_turns = 5 if self.state.field.trick_room else None
        elif move_id == "rapidspin":
            uside.clear_hazards()
        elif move_id == "defog":
            uside.clear_hazards()
            tside.clear_hazards()

    # --------------------------------------------------------------
    def _apply(self, ev: dict, cleaned: str, source: str):
        action = ev["action"]
        f = self.state.field

        if action == "noop":
            return
        if action == "battle_end":
            self.state.outcome = ev["value"]
            return
        if action == "weather":
            f.weather = ev["value"]
            f.weather_turns = 5 if ev["value"] else None
        elif action == "terrain":
            f.terrain = ev["value"]
            f.terrain_turns = 5 if ev["value"] else None
        elif action == "trickroom":
            f.trick_room = ev["value"]
            f.trick_room_turns = 5 if ev["value"] else None
        elif action == "gravity":
            f.gravity = ev["value"]
        elif action == "side_flag":
            _, side, _ = self._target_mon(cleaned, source)
            setattr(side, ev["key"], ev["value"])
        elif action == "hazard":
            _, side, _ = self._target_mon(cleaned, source)
            setattr(side, ev["key"], ev["value"])
        elif action == "hazard_add":
            _, side, _ = self._target_mon(cleaned, source)
            setattr(side, ev["key"], min(ev["max"], getattr(side, ev["key"]) + 1))
        elif action == "status":
            _, _, mon = self._target_mon(cleaned, source)
            mon.status = ev["value"]
        elif action == "faint":
            side_name, side, mon = self._target_mon(cleaned, source)
            mon.status = "fainted"
            mon.hp_percent = 0.0
            mon.hp_current = 0
            if side.remaining:
                side.remaining = max(0, side.remaining - 1)
        elif action == "volatile":
            _, _, mon = self._target_mon(cleaned, source)
            if ev["value"] and ev["key"] not in mon.volatiles:
                mon.volatiles.append(ev["key"])
            elif not ev["value"] and ev["key"] in mon.volatiles:
                mon.volatiles.remove(ev["key"])
        elif action == "mega":
            side_name, side, mon = self._target_mon(cleaned, source)
            mon.is_mega = True
            self.state.mega_used[side_name] = True
            sp = self.resolver.find_species_in_text(cleaned, cutoff=0.7)
            if sp and sp[0].startswith("メガ"):
                mon.merge_species(sp[0], sp[1])
        elif action == "item":
            _, _, mon = self._target_mon(cleaned, source)
            mon.item_ja = ev["value"]
            item = self.resolver.resolve(ev["value"], "items", cutoff=0.9)
            if item:
                mon.item_id = item[1]
