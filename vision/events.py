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
    # かなしばり: 解除を先に判定する (両方「かなしばり」を含むため)
    {"id": "disable_off", "keywords": [["かなしばり", "金縛り"],
                                       ["とけた", "解けた"]],
     "action": "volatile", "key": "disable", "value": False},
    {"id": "disable_on", "keywords": [["かなしばり", "金縛り"]],
     "action": "volatile", "key": "disable", "value": True},
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
    # --- ねばねばネットの被弾 (2026-08-31 第11回: 「〜はねばねばネットに
    #     ひっかかった!」が技使用 move_*_stickyweb として誤発火し、設置側の
    #     状態を乱した)。被弾側の陣営にネットが存在する確認として扱う ---
    {"id": "web_caught",
     "keywords": [["ねばねばネット", "ねはねはネット"], ["ひっかか", "引っかか"]],
     "action": "hazard", "key": "sticky_web", "value": True},
    # --- ふうせん (2026-08-25: 登場表示で持ち物を確定し、割れたら消費) ---
    {"id": "balloon_float",
     "keywords": [["ふうせん", "風船"], ["うかんている", "浮いている", "ういている"]],
     "action": "item", "value": "ふうせん"},
    {"id": "balloon_pop",
     "keywords": [["ふうせん", "風船"], ["われた", "割れた", "はれつ"]],
     "action": "balloon_pop"},

    # --- 勝敗 (「〜との勝負に勝った!」) ---
    {"id": "battle_win", "keywords": [["勝負", "しようふ"], ["勝った", "かつた"]],
     "action": "battle_end", "value": "win"},
    {"id": "battle_lose", "keywords": [["勝負", "しようふ"], ["負けた", "まけた"]],
     "action": "battle_end", "value": "loss"},
    # 相手の降参による勝ち (実戦: 降参終了は勝負メッセージ辞書に無く取り逃した)
    {"id": "battle_win", "keywords": [["相手", "あいて"], ["降参", "こうさん", "投了"]],
     "action": "battle_end", "value": "win"},

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

    def _recently(self, event_id: str, window: float = 6.0) -> bool:
        """直近windowで発火済みか (タイムスタンプを更新しない参照専用)"""
        last = self._recent_fired.get(event_id)
        return last is not None and time.time() - last < window

    # --------------------------------------------------------------
    def _is_opponent_text(self, cleaned: str) -> Optional[bool]:
        norm = loose_key(cleaned)
        head = norm[:10]
        if "相手の" in head or "あいての" in head:
            return True
        # OCR誤読対策: 「相手の」の1文字目が化けた「狙手の/柤手の」等。
        # 自陣メッセージは種族名 (カタカナ) 始まりで文頭付近に「手の」は
        # 現れないため、文頭6文字以内の「手の」は「相手の」とみなす
        # (実戦: 「狙手のハラバリーのみすびたし」が自分の技に誤帰属した)
        if "手の" in head[:6]:
            return True
        # パーティ名の照合: 文頭一致に限らず先頭14文字から両陣営を探す。
        # 片方の陣営にだけ見つかった場合のみその陣営と判定する
        # (ミラー・両陣営同種は判定しない = プレフィックス判定に委ねる)
        scan = norm[:14]
        found = set()
        for side_name, is_opp in (("player", False), ("opponent", True)):
            side = self.state.side(side_name)
            for mon in side.party:
                for cand in (mon.species_ja, mon.display_name,
                             *(mon.aliases or [])):
                    ck = loose_key(cand) if cand else ""
                    if ck and len(ck) >= 3 and (norm.startswith(ck[:3])
                                                or ck in scan):
                        found.add(is_opp)
        if len(found) == 1:
            return found.pop()
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

    def _target_mon_checked(self, cleaned: str, source: str):
        """対象個体の特定。戻り値: (side_name, side, mon, 名前照合できたか)

        名前照合はまず厳密部分一致、次にファジー (OCR劣化した名前の救済:
        「ベリッパー」→ペリッパー等)。照合できない場合はアクティブへ
        フォールバックするが、matched=False を返すので呼び出し側は
        個体レベルの帰属 (判明技の記録等) を控えられる
        (実戦: 「相手の型別対子のボルトチェンジ」等の名前崩れで、技が
        別のポケモンの判明技として記録された)
        """
        side_name = self._target_side(cleaned, source)
        side = self.state.side(side_name)
        norm = loose_key(cleaned)
        head = norm[:14]
        matches = []
        for mon in side.party:
            for cand in (mon.species_ja, mon.display_name,
                         *(mon.aliases or [])):
                ck = loose_key(cand) if cand else ""
                if ck and len(ck) >= 3 and ck in head:
                    matches.append(mon)
                    break
        if matches:
            # 同名フォーム (ロトム/ウォッシュロトム等) 対策: 一致した名前を
            # 内包する同族スロットも候補へ加えたうえで、場に出ている個体を
            # 優先する (2026-08-20 第5回持ち越し: 「相手のロトムはたおれた」
            # がゲーム表示上は同名のウォッシュ個体でなく基本形スロットへ
            # 誤帰属した)
            keys = {loose_key(m.species_ja) for m in matches if m.species_ja}
            for mon in side.party:
                mk = loose_key(mon.species_ja) if mon.species_ja else ""
                if mon not in matches and mk and \
                        any(k and k in mk for k in keys):
                    matches.append(mon)
            act = side.active()
            mon = act if act in matches else matches[0]
            return side_name, side, mon, True
        # ファジー照合: プレフィックスを除いた先頭セグメント vs パーティ名
        seg = re.sub(r"^(相手の|あいての|.手の)", "", norm).split("の")[0][:8]
        if len(seg) >= 3:
            import difflib
            names = {}
            for mon in side.party:
                for cand in (mon.species_ja, mon.display_name,
                             *(mon.aliases or [])):
                    ck = loose_key(cand) if cand else ""
                    if ck and len(ck) >= 3:
                        names[ck] = mon
            m = difflib.get_close_matches(seg, list(names.keys()),
                                          n=1, cutoff=0.6)
            if m:
                return side_name, side, names[m[0]], True
        return side_name, side, side.ensure_active(), False

    def _target_mon(self, cleaned: str, source: str):
        side_name, side, mon, _matched = self._target_mon_checked(cleaned, source)
        return side_name, side, mon

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

        # リザルト/ランク表示 (「ランクIV レート1602」等) はバトルイベントではないので無視。
        # ただしレート数値は勝敗推定に使えるため抽出して保持する
        # (勝敗メッセージのOCR取り逃しが6戦中2戦で発生。レートの増減は
        #  結果画面に必ず表示されるので、増=勝ち/減=負けの裏付けになる)
        if re.search(r"(ランク|らんく).{0,4}(レート|れーと)|レート\d{3,}|ボール級", cleaned):
            # レートは cleaned ではなく raw_text から抽出する: ランク画面の表示は
            # 「レート1626.580」の小数3桁形式で、正規化が小数点を落とすと
            # 「1626580」→先頭5桁=16265 が妥当範囲外となり全戦で棄却されていた
            # (2026-08-25 第9回: 8戦でレート観測0件、勝敗不明3戦の主因)
            cands = []
            m1 = re.search(r"レート\s*(\d{3,5}(?:[.,]\d{1,4})?)", raw_text)
            if m1:
                cands.append(float(m1.group(1).replace(",", ".")))
            # 小数点がOCRで落ちて桁が連結された場合の救済 (整数部4桁まで)
            m2 = re.search(r"レート(\d{3,4})", cleaned)
            if m2:
                cands.append(float(m2.group(1)))
            for val in cands:
                if 500 <= val <= 4000:   # ありえない値 (誤読) は捨てる
                    self.state.last_rate = {"value": val,
                                            "ts": round(time.time(), 2)}
                    break
            # ランク画面は対戦終了後に必ず表示される。勝敗文言を取り逃して
            # いても、ここを対戦終了のキーにする (2026-08-21 ユーザー提案)。
            # battle_logger はこのイベントで勝敗レコードを即時確定する
            # (次戦の選出まで待つとひんし数等の終局情報が失われる)
            if self.state.battle_active and not self._dedup("battle_end_rank"):
                self.state.battle_active = False
                self.state.log_event("system", "ランク画面を検出 (対戦終了)",
                                     event_id="battle_end_rank")
                return ["battle_end_rank"]
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

        # 3. ランク変化 (複数ステータス同時変化はステータスごとに発火する)
        if not any(not f.startswith("switch") for f in fired):
            fired.extend(self._parse_rank_change(cleaned, norm, source))

        # 3.5 タイプ変化 (変幻自在/リベロ等):「Xは こおりタイプに なった!」
        if not any(not f.startswith("switch") for f in fired):
            tc = self._parse_type_change(cleaned, norm, source)
            if tc:
                fired.append(tc)

        # 3.6 はたきおとす:「AはBの<持ち物>をはたきおとした!」→ Bの持ち物を喪失
        if not any(not f.startswith("switch") for f in fired):
            ko = self._parse_knockoff(cleaned, norm)
            if ko:
                fired.append(ko)

        # 4. 技使用 / 特性発動 ("{名前}の {技/特性}")
        # 相手の判明技の収集が重要なため、他イベントと複合したメッセージ
        # (「相手のXのわざ! 効果は〜」等) でも技解析は常に試みる。
        # 交代 (「〜を繰り出した」) は種族名が技に誤マッチしやすいので除外。
        # 効果切れ (「リフレクターがなくなった」等の *_end) は技の使用では
        # ないため除外 (壁切れメッセージが move_opponent_reflect を誤発火した)
        # 「〜は…にひっかかった!」等の被弾/接触メッセージは技の使用ではない
        # (2026-08-31 第11回: 相手が網にかかるたび複合文フォールバックが
        #  ねばねばネットを技として拾い、判明技汚染と誤設置が起きた)
        is_contact_msg = ("ひつかか" in norm or "ひっかか" in cleaned
                          or "引っかか" in cleaned)
        if not is_contact_msg and \
                not any(f.startswith("switch") or f.endswith("_end")
                        for f in fired):
            mu = self._parse_move_or_ability(cleaned, norm, source,
                                             apply_effects=not fired)
            if mu and mu not in fired:
                fired.append(mu)
            elif mu is None and source == "message":
                # 「の」区切りで解決できない複合文: 文中の技名を部分一致で探す
                found = self.resolver.find_in_text(cleaned, "moves", min_len=5)
                if found:
                    side_name, _side, mon, matched = \
                        self._target_mon_checked(cleaned, source)
                    if (side_name == "opponent"
                            and not (found[1] == "charge" and self._recently(
                                f"ability_{side_name}_electromorphosis"))
                            and not self._dedup(f"move_{side_name}_{found[1]}")):
                        # 名前照合できた個体にのみ判明技を記録する
                        # (照合失敗時のアクティブ帰属は別個体を汚染する)
                        if matched and found[0] not in mon.revealed_moves:
                            mon.revealed_moves.append(found[0])
                        fired.append(f"move_{side_name}_{found[1]}")

        # 連続まもるの追跡 (成功率減衰の観測用): まもる系の使用で+1、
        # 同じ側が他の技を使ったら0にリセット
        _PROTECT_IDS = ("protect", "detect", "banefulbunker", "spikyshield",
                        "kingsshield", "silktrap", "burningbulwark", "obstruct")
        for side in ("player", "opponent"):
            # 交代・ひんしで直前技をクリア (アンコールの技固定も同時に切れる)
            if any(f == f"switch_{side}" or f == "faint" and
                   (self.state.last_faint or {}).get("side") == side
                   for f in fired):
                self.state.last_move.pop(side, None)
            moves_fired = [f for f in fired if f.startswith(f"move_{side}_")]
            if not moves_fired:
                continue
            self.state.last_move[side] = moves_fired[-1].split("_", 2)[2]
            if any(f.split("_", 2)[2] in _PROTECT_IDS for f in moves_fired):
                self.state.protect_streak[side] = \
                    self.state.protect_streak.get(side, 0) + 1
            else:
                self.state.protect_streak[side] = 0

        # とんぼがえり系の交代先選択コンテキスト (engineが交代限定助言に使う)。
        # 使用で立て、交代完了/対戦終了で下ろす (次ターン到達時は pipeline 側)
        for f in fired:
            if f.startswith("move_player_") and \
                    f.split("move_player_", 1)[1] in self.PIVOT_SWITCH_MOVE_IDS:
                self.state.pending_pivot_switch = True
            elif f == "switch_player" or f.startswith("battle_"):
                self.state.pending_pivot_switch = False

        self.state.log_event(source, cleaned, event_id=",".join(fired) or None)
        if fired:
            # 生テキストを残す: 「技の取りこぼし」「誤帰属」の事後調査は
            # イベントIDだけでは不可能 (2026-08-05接続テストで、どの誤読が
            # 原因か特定できなかった)
            print(f"[events] {','.join(fired)} <- {cleaned[:48]}")
        return fired

    # 自分の交代先選択が発生する対面操作技 (使用後の助言を交代限定にする)
    # ⚠ ボルトチェンジが無効化された等で交代が発生しないケースは、
    #   次ターン到達 (pipelineのターン加算) でフラグが下りる
    PIVOT_SWITCH_MOVE_IDS = frozenset({
        "uturn", "voltswitch", "flipturn", "partingshot",
        "batonpass", "shedtail", "chillyreception", "teleport"})

    # --------------------------------------------------------------
    def _parse_switch(self, cleaned: str, norm: str, fired: list) -> bool:
        if not any(k in norm for k in ("繰り出", "くりたし", "くりた", "ゆけつ")):
            return False
        sp = self.resolver.find_species_in_text(cleaned, cutoff=0.7)
        if not sp:
            return False
        jp, sid, _ = sp
        # 側の判定はメッセージ形式を最優先する。種族の所属だけで判定すると
        # ミラーマッチ (相手も自分と同じ種族を使用) で相手の繰り出しが
        # 自分側へ化け、activeの乗っ取りと「助言なしの自分交代」の誤記録を
        # 生んだ (2026-08-19実測: 「RX78ー2はサザンドラを繰り出した」が
        # switch_player として発火した)。表記の対応:
        #   自分の繰り出し: 「ゆけっ!X!」(まれに「こちらはXを繰り出した」)
        #   相手の繰り出し: 「(トレーナー名)は Xを繰り出した」
        if re.search(r"相手|あいて", cleaned):
            side_name = "opponent"
        elif "ゆけつ" in norm or re.search(r"こちら", cleaned):
            side_name = "player"
        elif re.search(r"繰り出|くりだ", cleaned):
            side_name = "opponent"   # トレーナー名形式は相手の繰り出し
        elif self.state.player.find_by_species(jp) is not None:
            side_name = "player"
        else:
            side_name = "opponent"
        if side_name == "player":
            # 自分側は登録済みチーム優先で解決し直す。全種族ファジーだと
            # 誤読が近縁の別種に化けて別枠を作る (2026-08-05接続テスト:
            # メタグロス誤読→メタングの7枠目が生えた)
            from vision.extractors import resolve_my_species
            r = resolve_my_species(self.resolver, jp, cutoff=0.7)
            if r:
                jp, sid = r[0], r[1]
            side = self.state.player
        else:
            side = self.state.opponent
        if self._dedup(f"switch_{side_name}_{sid}"):
            return True
        mon = side.switch_to_species(jp, sid)
        from vision.extractors import link_active_to_party
        link_active_to_party(self.state, side_name)
        # 確定特性の着地効果 (いかく等): 「〜のいかく!」「こうげきがさがった」
        # のメッセージは演出中で取り逃しやすい (第9回: 捕捉0件) ため、
        # 交代イベント時点で確定的に適用する。特性が確定している個体のみ
        # (相手側の推定特性では適用しない — 誤適用の方が害が大きい)
        from advisor.dex import switch_in_ability_effects
        land = switch_in_ability_effects(mon.ability_id)
        if land:
            other_name = "opponent" if side_name == "player" else "player"
            other = self.state.side(other_name).active()
            if other is not None and other.status != "fainted":
                for stat, delta in land.items():
                    if not self._dedup(f"boost_{other_name}_{stat}_{delta:+d}"):
                        other.set_boost(stat, delta)
        # 交代後の新しい個体に前の個体のひんし裏付けを誤適用しない
        # (実戦: マスカーニャひんし→メタグロス登場直後の空バー誤読1%が
        #  「ひんし裏付けあり」としてHPイベント化した)
        last_faint = getattr(self.state, "last_faint", None)
        if last_faint and last_faint.get("side") == side_name:
            self.state.last_faint = None
        fired.append(f"switch_{side_name}")
        return True

    # --------------------------------------------------------------
    _TYPE_JA2EN = {
        "ノーマル": "normal", "ほのお": "fire", "みず": "water",
        "でんき": "electric", "くさ": "grass", "こおり": "ice",
        "かくとう": "fighting", "どく": "poison", "じめん": "ground",
        "ひこう": "flying", "エスパー": "psychic", "むし": "bug",
        "いわ": "rock", "ゴースト": "ghost", "ドラゴン": "dragon",
        "あく": "dark", "はがね": "steel", "フェアリー": "fairy",
    }

    def _parse_type_change(self, cleaned: str, norm: str,
                           source: str) -> Optional[str]:
        """「Xは <タイプ>タイプに なった!」(変幻自在/リベロ等) を検出し、
        対象のタイプを差し替える (2026-08-18 接続テスト:
        ゲッコウガのこおり化が状態に反映されず相性計算がズレた)。
        """
        if loose_key("タイプに") not in norm or \
                not (loose_key("なった") in norm or loose_key("なつた") in norm):
            return None
        found = None
        for ja in self._TYPE_JA2EN:
            if loose_key(ja + "タイプ") in norm:
                found = ja
                break
        if found is None:
            return None
        side_name, side, mon = self._target_mon(cleaned, source)
        event_id = f"type_change_{side_name}_{self._TYPE_JA2EN[found]}"
        if self._dedup(event_id):
            return None
        mon.types = [found]
        # 図鑑タイプによる自動訂正 (backfill) を抑止する。交代で解除される
        # (2026-08-21: 変幻自在の変化が数秒で図鑑タイプに戻されていた)
        mon.type_changed = True
        return event_id

    def _parse_rank_change(self, cleaned: str, norm: str, source: str) -> list:
        """ランク変化イベントの一覧を返す (無ければ空リスト)。

        捨て台詞 (攻撃+特攻) や瞑想 (特攻+特防) など1メッセージで複数の
        ステータスが動く技があるため、文中の全ステータス名に適用する
        (2026-08-18 接続テスト: 最初の1つで打ち切っており片方を取り逃した)。
        """
        stats = []
        for jp, key in STAT_NAMES.items():
            if loose_key(jp) in norm and key not in stats:
                stats.append(key)
        if not stats:
            return []
        for pat, delta in RANK_CHANGES:
            if loose_key(pat) in norm:
                side_name, side, mon = self._target_mon(cleaned, source)
                fired = []
                for stat in stats:
                    event_id = f"boost_{side_name}_{stat}_{delta:+d}"
                    if self._dedup(event_id):
                        continue   # OCR揺れの再読でランクを二重適用しない
                    mon.set_boost(stat, delta)
                    fired.append(event_id)
                if delta < 0:
                    self._maybe_white_herb(side_name, mon)
                return fired
        return []

    # --------------------------------------------------------------
    def _parse_move_or_ability(self, cleaned: str, norm: str, source: str,
                               apply_effects: bool = True) -> Optional[str]:
        """「{名前}の {技 or 特性}!」形式。「の」区切り候補を右から試す"""
        # 「〜は…にひっかかった!」等の被弾/接触メッセージは技の使用ではない
        # (2026-08-31 第11回: 相手が網にかかるたび move_opponent_stickyweb が
        #  誤発火し、陣営誤りの設置効果まで適用されていた)。
        # norm は loose_key で「っ→つ」正規化されるため両形を見る
        if "ひつかか" in norm or "ひっかか" in cleaned or "引っかか" in cleaned:
            return None
        body = re.sub(r"[!!]+$", "", cleaned)
        positions = [m.start() for m in re.finditer("の", body)]
        if not positions:
            return None

        if source in ("left_popup", "right_popup"):
            return self._parse_popup(cleaned, source)

        side_name, side, mon, name_matched = \
            self._target_mon_checked(cleaned, source)
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

        # でんきにかえる(特性)の「じゅうでん状態になった」表示を技じゅうでんと
        # 誤認しない: 直近で同陣営のでんきにかえるが発動していたらその状態表示
        # とみなす (実戦: ハラバリー被弾時に move_charge が誤発火した)
        if r[1] == "charge" and self._recently(
                f"ability_{side_name}_electromorphosis"):
            return None

        event_id = f"move_{side_name}_{r[1]}"
        if self._dedup(event_id):
            return None   # OCR揺れの再読 (まきびし等の効果二重適用を防ぐ)
        # 名前照合できた個体にのみ判明技を記録する (照合失敗時のアクティブ
        # フォールバックは、追跡ズレ時に別個体の判明技を汚染する)
        if side_name == "opponent" and name_matched \
                and r[0] not in mon.revealed_moves:
            mon.revealed_moves.append(r[0])
        if apply_effects:
            # 他イベントが既に発火している場合は場への効果を二重適用しない
            self._apply_move_side_effect(r[1], side_name)
            self._apply_move_boosts(r[1], side_name, mon)
            self._apply_move_item_recoil(r[1], mon)
        return event_id

    def _apply_move_item_recoil(self, move_id: str, mon) -> None:
        """ダメージ技使用時に確定発動する持ち物 (いのちのたま反動) の反映。

        反動メッセージ「命が少し削られた!」は演出中で取り逃す (2026-08-30
        第10回監査: ミミッキュ78%主張 vs 実65%、差は反動ぶん)。持ち物が
        確定していれば技イベントから決定的に引く。マジックガードは無反動。
        まもる/外れ時は誤適用になるが、実読みが上書き修正する
        """
        if mon is None or getattr(mon, "item_consumed", False) or \
                getattr(mon, "item_removed", False):
            return
        from advisor.dex import get_dex, item_damaging_move_effects
        eff = item_damaging_move_effects(mon.item_id)
        if not eff:
            return
        if (mon.ability_id or "") == "magicguard":
            return
        mv = get_dex().move(move_id)
        if not mv or mv.get("category") == "Status":
            return
        frac = eff.get("self_hp_fraction") or 0.0
        if not frac or mon.status == "fainted":
            return
        if mon.hp_percent is not None:
            mon.hp_percent = max(0.0, round(mon.hp_percent + frac * 100, 1))
        if mon.hp_current is not None and mon.hp_max:
            mon.hp_current = max(
                0, mon.hp_current + round(mon.hp_max * frac))

    def _apply_move_boosts(self, move_id: str, user_side: str, user_mon):
        """技の確定的な能力ランク変化 (100%発動のみ) を使用イベントで反映する。

        能力変化メッセージは技演出中の短時間表示でフレーム取得から系統的に
        漏れる (2026-08-25 第9回: 8対戦で捕捉0件、つるぎのまい・いかく含む)。
        技の使用メッセージ自体は確実に取れるため、そこから決定的に反映し、
        メッセージ読み (_parse_rank_change) と場の状況画面 (extract_field_check)
        は補正役に回す。boost_* のdedupキーを登録しておき、直後にメッセージも
        読めた場合の二重適用を防ぐ (3秒窓。次ターンの再使用は窓外で適用される)。
        制約: 命中失敗・まもる時の対象側効果は誤適用になる (発生率は低く、
        場の状況画面の実測値で上書き修正される)。
        """
        from advisor.dex import move_boost_effects
        eff = move_boost_effects(move_id)
        if not eff:
            return
        target_side = "opponent" if user_side == "player" else "player"
        applies = [("self", user_side, user_mon),
                   ("target", target_side, self.state.side(target_side).active())]
        for scope, side_name, mon in applies:
            if mon is None:
                continue
            for stat, delta in (eff.get(scope) or {}).items():
                if self._dedup(f"boost_{side_name}_{stat}_{delta:+d}"):
                    continue   # 直前にメッセージ経由で適用済み
                mon.set_boost(stat, delta)
            self._maybe_white_herb(side_name, mon)

    def _popup_mon(self, name_part: str, default_side: str):
        """ポップアップの名前部分から帰属先の個体を決める。

        名前が照合できないのにアクティブ個体へフォールバックすると、発動して
        いないポケモンに他個体の特性が付く誤帰属が起きる (実戦で観測)。
        名前で確認できた場合のみ帰属し、できなければ None (帰属なし) を返す。
        """
        name_key = loose_key(re.sub(r"^(相手の|あいての)", "", name_part))
        if len(name_key) < 3:
            return None, None
        # 1. 両サイドのパーティから名前一致 (逆サイド誤りにも耐える)。
        #    同名フォームが複数一致する場合は場に出ている個体を優先
        for side_name in (default_side,
                          "opponent" if default_side == "player" else "player"):
            side_obj = self.state.side(side_name)
            matches = []
            for mon in side_obj.party:
                for cand in (mon.species_ja, mon.display_name,
                             *(mon.aliases or [])):
                    ck = loose_key(cand) if cand else ""
                    if ck and len(ck) >= 3 and (ck in name_key or name_key in ck):
                        matches.append(mon)
                        break
            if matches:
                act = side_obj.active()
                return side_name, (act if act in matches else matches[0])
        # 2. 種族名として解決できるなら、未特定のアクティブ個体の特定に使う
        sp = self.resolver.resolve_species(name_part, cutoff=0.8)
        if sp:
            active = self.state.side(default_side).ensure_active()
            if active.species_ja is None:
                active.merge_species(sp[0], sp[1])
                return default_side, active
        return None, None

    def _parse_knockoff(self, cleaned: str, norm: str) -> Optional[str]:
        """「AはBの<持ち物>をはたきおとした!」: Bの持ち物を失わせる。

        以後の登録バックフィル・使用率予測での復活は item_removed が抑止する
        (2026-08-21 第7回: はたき後も持ち物を保持したまま計算していた)。
        """
        if loose_key("はたきおとした") not in norm and "はたき落とした" not in cleaned:
            return None
        body = re.sub(r"[!!]+$", "", cleaned)
        m = re.search(r"(.+?)を(?:はたき|叩き)", body)
        if not m:
            return None
        prefix = m.group(1)
        # 攻撃側「Aは」を取り除き、持ち主側「(相手の)Bの<持ち物>」だけを見る
        am = re.search(r"は(.+)$", prefix)
        owner_part = am.group(1) if am else prefix
        positions = [i.start() for i in re.finditer("の", owner_part)]
        best = None   # (score, pos, item)
        for pos in positions:
            tail = owner_part[pos + 1:]
            if len(normalize(tail)) < 2:
                continue
            it = self.resolver.resolve(tail, "items", cutoff=0.7)
            if it and (best is None or it[2] > best[0]):
                best = (it[2], pos, it)
        if best is None:
            return None
        _, pos, it = best
        default_side = "opponent" if re.search(r"相手|あいて",
                                               owner_part[:pos]) else "player"
        side_name, mon = self._popup_mon(owner_part[:pos], default_side)
        if mon is None:
            return None
        event_id = f"knockoff_{side_name}_{it[1]}"
        if self._dedup(event_id):
            return None
        mon.item_ja, mon.item_id = None, None
        mon.item_removed = True
        return event_id

    def _parse_popup(self, cleaned: str, source: str) -> Optional[str]:
        """ポップアップ「{名前}の {特性 or 持ち物}」: 特性優先、次に持ち物。

        「の」の分割位置は**全候補を試して最高スコアの解決を採用**する。
        従来は最後の「の」から順に「最初に解決できた候補」を採用しており、
        名前自体に「の」を含む持ち物が壊れた (2026-08-21 第7回:
        「ミミッキュのいのちのたま」→ 末尾分割「たま」→ ビーだま(marble)
        に誤解決し、いのちのたま(完全一致)が選ばれなかった)。
        """
        body = re.sub(r"[!!]+$", "", cleaned)
        positions = [m.start() for m in re.finditer("の", body)]
        if not positions:
            return None
        default_side = self._target_side(cleaned, source)
        best = None   # (score, pos, ab, it)
        for pos in positions:
            tail = body[pos + 1:]
            if len(normalize(tail)) < 2:
                continue
            ab = self.resolver.resolve(tail, "abilities", cutoff=0.72)
            it = None if ab else self.resolver.resolve(tail, "items", cutoff=0.75)
            if not ab and not it:
                continue
            score = (ab or it)[2]
            if best is None or score > best[0]:
                best = (score, pos, ab, it)
        if best is None:
            return None
        for pos, ab, it in ((best[1], best[2], best[3]),):
            tail = body[pos + 1:]
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
            if ab:
                # 種族が判明しているなら合法特性 (最大3択) に限定して検証。
                # 合法外なら候補内で再解決し、それでも合わなければ捨てる
                from vision.abilities import legal_abilities
                legal = legal_abilities(mon.species_id)
                if legal is not None and ab[1] not in legal:
                    ab = self.resolver.resolve_restricted(tail, "abilities", legal)
                    if not ab:
                        it = self.resolver.resolve(tail, "items", cutoff=0.75)
                        if not it:
                            self.state.log_event(source, cleaned, event_id=None)
                            return None
            event_id = (f"ability_{side_name}_{ab[1]}" if ab
                        else f"item_{side_name}_{it[1]}")
            if self._dedup(event_id):
                return None
            if ab:
                mon.ability_ja, mon.ability_id = ab[0], ab[1]
                self._apply_ability_effect(ab[1], side_name, mon=mon)
            else:
                mon.item_ja, mon.item_id = it[0], it[1]
                if mon is not None:
                    self._apply_item_activation(it[1], mon)
            return event_id
        return None

    def _apply_item_activation(self, item_id: str, mon) -> None:
        """発動型アイテムのポップアップ/メッセージ観測時の効果適用。

        従来は持ち物名の記録のみで、消費も効果 (回復・状態回復・ランク変化・
        しろいハーブの復元) も反映されなかった (2026-08-25 第9回指摘 →
        同日夜にしろいハーブ以外も網羅)。効果表は advisor/data/item_effects.json。
        ポップアップは発動の瞬間に出るため、条件側を取り逃していても消費は確定。
        パッシブな持ち物 (たべのこし等) は表に無く、何もしない
        """
        from advisor.dex import item_activation_effects
        eff = item_activation_effects(item_id)
        if not eff:
            return
        if eff.get("restore_lowered_stats"):
            for k, v in mon.boosts.items():
                if v < 0:
                    mon.boosts[k] = 0
        for stat, delta in (eff.get("boosts") or {}).items():
            mon.set_boost(stat, delta)
        hf = eff.get("heal_fraction")
        if hf and mon.status != "fainted":
            if mon.hp_percent is not None:
                mon.hp_percent = min(100.0,
                                     round(mon.hp_percent + hf * 100, 1))
            if mon.hp_current is not None and mon.hp_max:
                mon.hp_current = min(mon.hp_max,
                                     mon.hp_current + round(mon.hp_max * hf))
        flat = eff.get("heal_flat")
        if flat and mon.status != "fainted" and \
                mon.hp_current is not None and mon.hp_max:
            mon.hp_current = min(mon.hp_max, mon.hp_current + flat)
            mon.hp_percent = round(mon.hp_current / mon.hp_max * 100, 1)
        cure = eff.get("cure_status")
        if cure and mon.status and mon.status != "fainted":
            if cure == "all" or mon.status == cure:
                mon.status = None
        cv = eff.get("cure_volatile")
        if cv == "mental":
            mon.volatiles = [v for v in mon.volatiles
                             if v not in ("taunt", "encore")
                             and not v.startswith("disable")]
        elif cv:
            mon.volatiles = [v for v in mon.volatiles if v != cv]
        if eff.get("consume"):
            mon.item_consumed = True

    def _maybe_white_herb(self, side_name: str, mon) -> None:
        """確定している持ち物がしろいハーブなら、能力低下の反映直後に発動を
        推定適用する。発動ポップアップは演出中で取り逃しやすい (第9回までの
        全対戦で発火2件のみ) が、持ち物が確定していれば発動は決定的"""
        if mon is None or getattr(mon, "item_id", None) != "whiteherb":
            return
        if getattr(mon, "item_consumed", False) or \
                getattr(mon, "item_removed", False):
            return
        if not any(v < 0 for v in mon.boosts.values()):
            return
        for k, v in mon.boosts.items():
            if v < 0:
                mon.boosts[k] = 0
        mon.item_consumed = True
        self.state.log_event("system",
                             f"しろいハーブの発動を推定反映 ({side_name})",
                             event_id=f"item_{side_name}_whiteherb_auto")

    # --------------------------------------------------------------
    def _apply_ability_effect(self, ability_id: str, side_name: str,
                              mon=None):
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
        elif ability_id == "disguise":
            # ばけのかわが剥がれると最大HPの1/8の定数ダメージ。HP読みの
            # 合間に挟まると取り逃してHPが過大になる (2026-08-19 opus監査:
            # ミミッキュ88%実表示を100%と主張、差はちょうど1/8)。
            # モデル値として即時反映し、以後の実読みで上書きされる
            target = mon if mon is not None \
                else self.state.side(side_name).ensure_active()
            if target.hp_percent is not None:
                target.hp_percent = max(0.0, round(target.hp_percent - 12.5, 1))
                if target.hp_current is not None and target.hp_max:
                    target.hp_current = max(
                        0, target.hp_current - round(target.hp_max / 8))

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
            # HP0%イベントの裏付けに使う (交代アニメの空バー誤読と区別)
            self.state.last_faint = {"side": side_name, "ts": time.time()}
            if side.remaining:
                side.remaining = max(0, side.remaining - 1)
        elif action == "volatile":
            _, _, mon = self._target_mon(cleaned, source)
            if ev["value"] and ev["key"] not in mon.volatiles:
                mon.volatiles.append(ev["key"])
            elif not ev["value"] and ev["key"] in mon.volatiles:
                mon.volatiles.remove(ev["key"])
            if ev["key"] == "disable":
                if ev["value"]:
                    # 「◯◯の △△を かなしばりにした!」から封じられた技を特定
                    m = re.search(r"の\s*(.+?)\s*を", cleaned)
                    r = self.resolver.resolve(m.group(1), "moves",
                                              cutoff=0.72) if m else None
                    if r and f"disable_{r[1]}" not in mon.volatiles:
                        mon.volatiles.append(f"disable_{r[1]}")
                else:
                    mon.volatiles = [v for v in mon.volatiles
                                     if not v.startswith("disable_")]
        elif action == "mega":
            side_name, side, mon = self._target_mon(cleaned, source)
            mon.is_mega = True
            self.state.mega_used[side_name] = True
            sp = self.resolver.find_species_in_text(cleaned, cutoff=0.7)
            if sp and sp[0].startswith("メガ"):
                # species_id はメガ後 (種族値/特性計算用) にするが、
                # species_ja はメガ前の名前を維持する: 画面のHUD表示は
                # メガ後も元の名前のままなので、メガ名に変えると以後の
                # 名前照合が失敗して別枠が生える (実戦で重複を観測)
                mon.species_id = sp[1]
                base_ja = re.sub(r"[XY]$", "", sp[0][len("メガ"):])
                if not mon.species_ja:
                    mon.species_ja = base_ja
                # メガ名でも照合できるよう別名に登録
                if sp[0] not in (mon.aliases or []):
                    mon.aliases.append(sp[0])
            else:
                # メガ名がOCR崩れで解決できない場合 (「メガスコィラン」等、
                # 2026-08-25 第9回で実測) は、対象個体の種族からメガフォルムを
                # 導出する。X/Y両形態はメガストーンIDで判別し、判別できなければ
                # species_id は変えない (is_megaフラグのみ。誤確定より未確定)
                from vision.abilities import mega_form_id
                mid = mega_form_id(mon.species_id, mon.item_id)
                if mid:
                    mon.species_id = mid
                    if mon.species_ja:
                        alias = f"メガ{mon.species_ja}"
                        if alias not in (mon.aliases or []):
                            mon.aliases.append(alias)
            # メガフォルムの特性は固定なので確定値として設定する
            from vision.abilities import fixed_ability
            fa = fixed_ability(mon.species_id, is_mega=True, item_id=mon.item_id)
            if fa:
                mon.ability_id = fa
                mon.ability_ja = self.resolver.ja_of("abilities", fa) or fa
        elif action == "item":
            _, _, mon = self._target_mon(cleaned, source)
            mon.item_ja = ev["value"]
            item = self.resolver.resolve(ev["value"], "items", cutoff=0.9)
            if item:
                mon.item_id = item[1]
                self._apply_item_activation(item[1], mon)
        elif action == "balloon_pop":
            # 「◯◯のふうせんが割れた!」: 以後は地面技が当たる
            _, _, mon = self._target_mon(cleaned, source)
            if not mon.item_id:
                mon.item_ja, mon.item_id = "ふうせん", "airballoon"
            if mon.item_id == "airballoon":
                mon.item_consumed = True
