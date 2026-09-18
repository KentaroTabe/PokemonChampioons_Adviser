"""構築の軸 (archetype): 一覧 (ARCHETYPES) と、環境に合わせた軸の具体化 (S4 の候補源 / S5 の制約 / S6 の型への反映)。

2026-09-18 ユーザー決定: 積み構築は交代すると能力変化が消えるので (バトンタッチを除き) 交代せず、壁で耐久を補う。
LLM の framing と規則だけではそうした「構築の型」を網羅できないので、約 10 の軸を表に持ち、軸ごとに
  - 必要な役割 (壁役 / 積みエース / TR 使い / 天候始動 / 設置役 …) を「型の技・特性・持ち物・数値」で機械的に判定し、
  - 環境 (脅威の重み) に合わせて分岐の適合を採点し、役割を埋めた core を S4 に出し (LLM にも軸ごとに提案させる)、
  - S5 で役割の最小数を hard constraint、S6 で役割の技・持ち物を型に保証する。
「特殊な勝ち筋」(ほろびのうた + 交代封じ、一撃必殺、みちづれ、のろい、やどりぎ、がむしゃら、カウンター系) は 1 つの軸にまとめ、
分岐で持つ。個別に軸を増やさない処置: 一覧では 1 行、S4 では合計 BUILD_ARCHETYPE_SPECIAL_MAX_CORES まで、LLM への提示も 1 回、
役割の判定は使用率でなく learnset ベース (流行らない技でも拾う)。
判定・数値は表とデータから機械的に出す (LLM は説明と追加の仮説だけ)。純粋関数は Caps (種ごとの判定材料) と threat_info
(脅威ごとの技・特性・タイプ・素早さ・重み) を受け取り、図鑑・DB・learnset の読み出しは run.py 側 (archetype_context) で行う。
一覧の文書: docs/TEAM_BUILD_ARCHETYPES.md (表とこの ARCHETYPES を同期させる。`python -m tools.team_build.archetypes --list`)。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from typing import Callable, Optional

from champions_agent.config import (BUILD_ARCHETYPE_ANSWER_COVERAGE, BUILD_ARCHETYPE_CORES_PER_BRANCH,
                                    BUILD_ARCHETYPE_FAST_SPEED_SHARE, BUILD_ARCHETYPE_FAST_THREAT_SPE,
                                    BUILD_ARCHETYPE_MIN_FIT, BUILD_ARCHETYPE_PRIORITY_BULK, BUILD_ARCHETYPE_ROLE_TOP,
                                    BUILD_ARCHETYPE_SPECIAL_MAX_CORES, BUILD_ARCHETYPE_TR_SPEED_SHARE,
                                    BUILD_ARCHETYPE_WALL_BULK, BUILD_RULE_ACE_MIN_ATTACK_MOVES,
                                    BUILD_RULE_ACE_MIN_ATTACK_TYPES, BUILD_RULE_ACE_MIN_COVERAGE, BUILD_RULE_ACE_MIN_OFFENSE)

# ---------------------------------------------------------------- 技・特性の表 (役割の判定と環境適合の材料)
SCREEN_MOVES = ("reflect", "lightscreen", "auroraveil")
LEAD_SUPPORT_MOVES = ("yawn", "thunderwave", "memento", "partingshot", "spore", "sleeppowder", "hypnosis", "glare",
                      "stickyweb", "stealthrock", "taunt", "encore")
PRIORITY_MOVES = ("suckerpunch", "bulletpunch", "shadowsneak", "aquajet", "extremespeed", "machpunch", "iceshard",
                  "quickattack", "accelerock", "vacuumwave", "firstimpression", "jetpunch", "thunderclap", "grassyglide",
                  "upperhand")
STEEL_PRIORITY_MOVES = ("bulletpunch", "machpunch", "iceshard", "aquajet", "shadowsneak", "extremespeed", "accelerock",
                        "jetpunch", "firstimpression")
PIVOT_MOVES = ("uturn", "voltswitch", "flipturn", "partingshot", "teleport", "chillyreception")
HAZARD_MOVES = ("stealthrock", "spikes", "toxicspikes", "stickyweb")
REMOVAL_MOVES = ("defog", "rapidspin", "tidyup", "mortalspin")
PHAZE_MOVES = ("roar", "whirlwind", "dragontail", "circlethrow", "haze", "clearsmog")
SETUP_COUNTER_MOVES = PHAZE_MOVES + ("taunt", "encore", "brickbreak", "psychicfangs", "ragingbull", "topsyturvy")
HEAL_MOVES = ("recover", "roost", "slackoff", "softboiled", "milkdrink", "shoreup", "moonlight", "morningsun",
              "synthesis", "strengthsap", "rest", "wish")
STATUS_MOVES = ("toxic", "willowisp", "thunderwave", "yawn", "spore", "sleeppowder", "glare", "hypnosis")
WEATHER_ABILITIES = {"sun": ("drought",), "rain": ("drizzle",), "sand": ("sandstream",), "snow": ("snowwarning",)}
WEATHER_MOVES = {"sun": ("sunnyday",), "rain": ("raindance",), "sand": ("sandstorm",), "snow": ("snowscape", "chillyreception")}
WEATHER_ABUSER_ABILITIES = {"sun": ("chlorophyll", "solarpower", "protosynthesis"), "rain": ("swiftswim",),
                            "sand": ("sandrush", "sandforce"), "snow": ("slushrush",)}
WEATHER_ABUSER_MOVES = {"sun": ("solarbeam", "solarblade", "weatherball"), "rain": ("weatherball", "hurricane", "thunder"),
                        "sand": (), "snow": ("auroraveil", "blizzard")}
WEATHER_TYPES = {"sun": ("Fire",), "rain": ("Water",), "sand": ("Rock", "Ground", "Steel"), "snow": ("Ice",)}
TERRAIN_ABILITIES = {"psychic": ("psychicsurge",), "grassy": ("grassysurge",), "electric": ("electricsurge",),
                     "misty": ("mistysurge",)}
TERRAIN_MOVES = {"psychic": ("psychicterrain",), "grassy": ("grassyterrain",), "electric": ("electricterrain",),
                 "misty": ("mistyterrain",)}
TERRAIN_ABUSER_MOVES = {"psychic": ("expandingforce",), "grassy": ("grassyglide",), "electric": ("risingvoltage",),
                        "misty": ("mistyexplosion",)}
TERRAIN_SEEDS = {"psychic": "psychicseed", "grassy": "grassyseed", "electric": "electricseed", "misty": "mistyseed"}
PRIORITY_BLOCK_ABILITIES = ("psychicsurge", "queenlymajesty", "dazzling", "armortail")
TRAP_ABILITIES = ("arenatrap", "shadowtag", "magnetpull")
TRAP_MOVES = ("meanlook", "block", "spiderweb", "thousandwaves", "jawlock", "octolock", "anchorshot", "spiritshackle",
              "fairylock")
OHKO_MOVES = ("sheercold", "fissure", "horndrill", "guillotine")
CHOICE_ITEMS = ("choicescarf", "choiceband", "choicespecs")
SWITCHING_JA = {"no_switch": "交代しない", "cycle": "交代で回す", "mixed": "状況次第"}

# ---------------------------------------------------------------- 役割の定義
# any_of / all_of の各条件 (alt): moves (min 本以上を「型に入れている (now)」か「覚える (can)」)、abilities、items、types、
# speed_share_min / speed_share_max / boost_mult_min。数値の門 (offensive / setup / bulk_min / grounded / not_mega) は役割全体。
# item = S6 で持たせたい持ち物 (メガ石・指定の型は上書きしない)
ROLE_SPECS = {
    "setup_ace": {"label": "積みエース", "setup": True, "offensive": True},
    "screens_dual": {"label": "壁役 (2 枚壁)", "any_of": [{"moves": ("reflect", "lightscreen"), "min": 2}],
                     "item": "lightclay", "not_mega": True},
    "screens_veil": {"label": "壁役 (オーロラベール)", "any_of": [{"moves": ("auroraveil",)}], "item": "lightclay",
                     "not_mega": True},
    "snow_source": {"label": "雪 (ゆきふらし / 雪技)", "any_of": [{"abilities": ("snowwarning",)}, {"moves": ("snowscape", "chillyreception")}]},
    "lead_support": {"label": "起点作り", "any_of": [{"moves": LEAD_SUPPORT_MOVES}], "item": "focussash"},
    "baton": {"label": "バトン役", "any_of": [{"moves": ("batonpass",)}], "setup": True},
    "receiver": {"label": "受け取り役", "offensive": True},
    "tr_setter": {"label": "トリックルーム使い", "any_of": [{"moves": ("trickroom",)}]},
    "tr_ace": {"label": "低速高火力", "offensive": True, "speed_share_max": BUILD_ARCHETYPE_TR_SPEED_SHARE},
    "hazard_setter": {"label": "設置役", "any_of": [{"moves": HAZARD_MOVES}]},
    "rocks_setter": {"label": "ステルスロック役", "any_of": [{"moves": ("stealthrock",)}]},
    "spikes_setter": {"label": "まきびし / どくびし役", "any_of": [{"moves": ("spikes", "toxicspikes")}]},
    "web_setter": {"label": "ねばねばネット役", "any_of": [{"moves": ("stickyweb",)}]},
    "phazer": {"label": "吹き飛ばし役", "any_of": [{"moves": PHAZE_MOVES}]},
    "pivot": {"label": "交代技持ち", "any_of": [{"moves": PIVOT_MOVES}]},
    "regen_wall": {"label": "受け (再生 / 回復)", "any_of": [{"abilities": ("regenerator",)}, {"moves": HEAL_MOVES}],
                   "bulk_min": BUILD_ARCHETYPE_WALL_BULK},
    "wall": {"label": "高耐久 (回復技)", "any_of": [{"moves": HEAL_MOVES}, {"abilities": ("regenerator", "poisonheal")}],
             "bulk_min": BUILD_ARCHETYPE_WALL_BULK},
    "status_user": {"label": "状態異常役", "any_of": [{"moves": STATUS_MOVES}]},
    "priority_attacker": {"label": "先制技持ち", "any_of": [{"moves": PRIORITY_MOVES}], "offensive": True},
    "priority_bulky": {"label": "先制技持ち (耐久あり)", "any_of": [{"moves": PRIORITY_MOVES}], "offensive": True,
                       "bulk_min": BUILD_ARCHETYPE_PRIORITY_BULK},
    "sucker": {"label": "ふいうち持ち", "any_of": [{"moves": ("suckerpunch",)}], "offensive": True},
    "steel_priority": {"label": "鋼 / 物理先制技持ち", "any_of": [{"moves": STEEL_PRIORITY_MOVES}], "offensive": True},
    # タスキ / スカーフは「型が既に持っている」ときだけ速攻役と数える (now_only。誰にでも持たせて速攻役にはしない)
    "fast_attacker": {"label": "速攻役", "offensive": True,
                      "any_of": [{"speed_share_min": BUILD_ARCHETYPE_FAST_SPEED_SHARE},
                                 {"items": ("choicescarf", "focussash"), "now_only": True}]},
    "tailwind": {"label": "おいかぜ役", "any_of": [{"moves": ("tailwind",)}]},
    "self_booster": {"label": "自己加速役", "offensive": True, "any_of": [{"boost_mult_min": 1.01}]},
    "answer": {"label": "上位脅威への回答", "offensive": True, "top_coverage_min": BUILD_ARCHETYPE_ANSWER_COVERAGE},
    # 特殊な勝ち筋
    "perish_singer": {"label": "ほろびのうた役", "any_of": [{"moves": ("perishsong",)}]},
    "trapper": {"label": "交代封じ", "any_of": [{"abilities": TRAP_ABILITIES}, {"moves": TRAP_MOVES}]},
    "ohko_user": {"label": "一撃必殺役", "any_of": [{"moves": OHKO_MOVES}]},
    "destiny_bond": {"label": "みちづれ役", "any_of": [{"moves": ("destinybond",)}],
                     "all_of": [[{"speed_share_min": 0.5}, {"items": ("focussash",)}]]},
    "curse_ghost": {"label": "のろい (ゴースト)", "any_of": [{"moves": ("curse",), "types": ("Ghost",)}],
                    "all_of": [[{"moves": ("protect", "substitute", "painsplit")}]]},
    "leech_seeder": {"label": "やどりぎ役", "any_of": [{"moves": ("leechseed",)}],
                     "all_of": [[{"moves": ("protect", "substitute")}]], "bulk_min": BUILD_ARCHETYPE_WALL_BULK},
    "endeavor_sash": {"label": "がむしゃら役", "any_of": [{"moves": ("endeavor",)}], "item": "focussash",
                      "all_of": [[{"moves": PRIORITY_MOVES}, {"speed_share_min": 0.5}]]},
    "counter_user": {"label": "カウンター / ミラーコート役", "any_of": [{"moves": ("counter", "mirrorcoat", "metalburst")}],
                     "item": "focussash"},
    "partner": {"label": "相方 (被覆で選ぶ)", "offensive": True},
}
for _w, _ab in WEATHER_ABILITIES.items():
    ROLE_SPECS[f"{_w}_setter"] = {"label": {"sun": "晴れ始動", "rain": "雨始動", "sand": "砂始動", "snow": "雪始動"}[_w],
                                  "any_of": [{"abilities": _ab}, {"moves": WEATHER_MOVES[_w]}]}
    ROLE_SPECS[f"{_w}_abuser"] = {"label": {"sun": "晴れアタッカー", "rain": "雨アタッカー", "sand": "砂アタッカー", "snow": "雪アタッカー"}[_w],
                                  "offensive": True,
                                  "any_of": [{"abilities": WEATHER_ABUSER_ABILITIES[_w]}, {"moves": WEATHER_ABUSER_MOVES[_w]},
                                             {"types": WEATHER_TYPES[_w]}]}
for _t, _ab in TERRAIN_ABILITIES.items():
    ROLE_SPECS[f"{_t}_setter"] = {"label": {"psychic": "サイコフィールド設置役", "grassy": "グラスフィールド設置役",
                                             "electric": "エレキフィールド設置役", "misty": "ミストフィールド設置役"}[_t],
                                  "any_of": [{"abilities": _ab}, {"moves": TERRAIN_MOVES[_t]}]}
    _alts = [{"moves": TERRAIN_ABUSER_MOVES[_t]}, {"items": (TERRAIN_SEEDS[_t],)}]
    if _t == "psychic":
        _alts += [{"speed_share_min": BUILD_ARCHETYPE_FAST_SPEED_SHARE}, {"boost_mult_min": 1.01}]
    elif _t == "grassy":
        _alts += [{"types": ("Grass",)}]
    elif _t == "electric":
        _alts += [{"types": ("Electric",)}]
    else:
        _alts += [{"types": ("Fairy", "Dragon")}]
    ROLE_SPECS[f"{_t}_abuser"] = {"label": {"psychic": "サイコフィールドの恩恵を受けるエース", "grassy": "グラスフィールドのエース",
                                             "electric": "エレキフィールドのエース", "misty": "ミストフィールドの恩恵を受ける役"}[_t],
                                  "offensive": _t != "misty", "grounded": True, "any_of": _alts}

# ---------------------------------------------------------------- 軸の一覧
# roles = 6 体中の最小数 (S5 の制約)、core_roles = S4 の core に入れる役割と数 (省略時は roles)、overlap_ok = 同じ個体が複数の
# 役割を兼ねてよい、win_condition は分岐で上書きできる、meta = 環境適合の式の種類 (branch_fit)
ARCHETYPES = {
    "setup_sweep": {
        "label": "積み展開", "win_condition": "setup_sweep", "switching": "no_switch",
        "support_roles": ["setup", "bulk", "speed_control"],
        "description": "壁や起点作りで積む 1〜2 ターンを買い、積みエースが積んで抜く。積んだ後は交代しない (交代で能力変化が消える。バトンタッチは例外)",
        "meta": "setup",
        "branches": {
            "screens_dual": {"label": "2 枚壁 (リフレクター + ひかりのかべ + ひかりのねんど)",
                             "roles": [("screens_dual", 1), ("setup_ace", 2)]},
            "screens_veil": {"label": "オーロラベール (雪 + ゆきふらし / さむいギャグ)",
                             "roles": [("screens_veil", 1), ("snow_source", 1), ("setup_ace", 2)],
                             "core_roles": [("screens_veil", 1), ("setup_ace", 2)], "overlap_ok": True},
            "lead_support": {"label": "起点作り (あくび / でんじは / おきみやげ / 設置 + タスキ)",
                             "roles": [("lead_support", 1), ("setup_ace", 2)]},
            "baton_pass": {"label": "バトンタッチ (積んで渡す)", "roles": [("baton", 1), ("receiver", 1)], "meta": "baton"},
        },
    },
    "trick_room": {
        "label": "トリックルーム", "win_condition": "speed_control", "switching": "no_switch",
        "support_roles": ["bulk", "priority"],
        "description": "5 ターンの逆転で低速高火力が上を取る。TR 中は交代しない",
        "meta": "trick_room",
        "branches": {
            "tr_double": {"label": "TR 使い 2 体 (再展開)", "roles": [("tr_setter", 2), ("tr_ace", 1)],
                          "core_roles": [("tr_setter", 2), ("tr_ace", 1)], "overlap_ok": True},
            "tr_single": {"label": "TR 使い 1 体 + 低速高火力 2 体", "roles": [("tr_setter", 1), ("tr_ace", 2)],
                          "overlap_ok": True},
        },
    },
    "weather": {
        "label": "天候", "win_condition": "offense_trade", "switching": "mixed",
        "support_roles": ["speed_control", "hazard_control"],
        "description": "始動役 (特性か技) が天候を張り、天候で速くなる / 強まるアタッカーが押す",
        "meta": "weather",
        "branches": {
            "sun": {"label": "晴れ", "roles": [("sun_setter", 1), ("sun_abuser", 1)], "core_roles": [("sun_setter", 1), ("sun_abuser", 2)],
                    "win_condition": "offense_trade"},
            "rain": {"label": "雨", "roles": [("rain_setter", 1), ("rain_abuser", 1)], "core_roles": [("rain_setter", 1), ("rain_abuser", 2)],
                     "win_condition": "speed_control"},
            "sand": {"label": "砂", "roles": [("sand_setter", 1), ("sand_abuser", 1)], "core_roles": [("sand_setter", 1), ("sand_abuser", 2)],
                     "win_condition": "bulky_attrition"},
            "snow": {"label": "雪", "roles": [("snow_setter", 1), ("snow_abuser", 1)], "core_roles": [("snow_setter", 1), ("snow_abuser", 2)],
                     "win_condition": "setup_sweep"},
        },
    },
    "terrain": {
        "label": "フィールド", "win_condition": "offense_trade", "switching": "mixed",
        "support_roles": ["speed_control", "priority"],
        "description": "設置役 (メイカー特性か技) がフィールドを張り、接地したエースが恩恵を受ける (サイコ = 先制技封じ、グラス / エレキ = 火力、ミスト = 状態異常無効・ドラゴン半減)",
        "meta": "terrain",
        "branches": {
            "psychic": {"label": "サイコフィールド (規則 psychic_terrain_priority_ace の hard 版と同じ構造)",
                        "roles": [("psychic_setter", 1), ("psychic_abuser", 1)], "win_condition": "speed_control"},
            "grassy": {"label": "グラスフィールド", "roles": [("grassy_setter", 1), ("grassy_abuser", 1)]},
            "electric": {"label": "エレキフィールド", "roles": [("electric_setter", 1), ("electric_abuser", 1)]},
            "misty": {"label": "ミストフィールド", "roles": [("misty_setter", 1), ("misty_abuser", 1)], "win_condition": "bulky_attrition"},
        },
    },
    "hazard_stack": {
        "label": "設置と削り", "win_condition": "hazard_chip", "switching": "cycle",
        "support_roles": ["hazard_control", "priority", "status"],
        "description": "設置技で削り、吹き飛ばしや交代圧で回数を稼ぎ、先制技や速攻で締める",
        "meta": "hazard",
        "branches": {
            "rocks_phaze": {"label": "ステルスロック + 吹き飛ばし", "roles": [("rocks_setter", 1), ("phazer", 1)],
                            "core_roles": [("rocks_setter", 1), ("priority_attacker", 1)], "overlap_ok": True},
            "spikes_stack": {"label": "まきびし / どくびし重ね", "roles": [("spikes_setter", 1), ("hazard_setter", 1)],
                             "core_roles": [("spikes_setter", 1), ("priority_attacker", 1)], "overlap_ok": True},
            "sticky_web": {"label": "ねばねばネット + 中速高火力", "roles": [("web_setter", 1), ("setup_ace", 1)],
                           "meta": "web"},
        },
    },
    "cycle": {
        "label": "サイクル", "win_condition": "cycle_pressure", "switching": "cycle",
        "support_roles": ["pivot", "bulk", "status"],
        "description": "交代技で有利対面を作り続け、受けで回す",
        "meta": "cycle",
        "branches": {
            "volt_turn": {"label": "とんぼ / ボルチェン / クイックターン", "roles": [("pivot", 2)]},
            "regenerator": {"label": "さいせいりょくの受け回し", "roles": [("regen_wall", 1), ("pivot", 1)]},
        },
    },
    "stall": {
        "label": "受けループ", "win_condition": "bulky_attrition", "switching": "cycle",
        "support_roles": ["bulk", "status", "hazard_control"],
        "description": "高耐久と回復で受け、状態異常と設置で削る",
        "meta": "stall",
        "branches": {
            "toxic_stall": {"label": "どく / やけど + 回復", "roles": [("wall", 2), ("status_user", 1)], "core_roles": [("wall", 2)],
                            "overlap_ok": True},
            "hazard_stall": {"label": "物理受け + 特殊受け + 設置", "roles": [("wall", 2), ("hazard_setter", 1)],
                             "core_roles": [("wall", 2)], "overlap_ok": True},
        },
    },
    "hyper_offense": {
        "label": "対面 (速攻)", "win_condition": "offense_trade", "switching": "no_switch",
        "support_roles": ["speed_control", "priority"],
        "description": "タスキ / スカーフ / メガの高速アタッカーで対面を取り続ける。交代しない",
        "meta": "offense",
        "branches": {
            "sash_scarf": {"label": "タスキ + スカーフ", "roles": [("fast_attacker", 2), ("priority_attacker", 1)],
                           "core_roles": [("fast_attacker", 2)], "overlap_ok": True},
            "tailwind": {"label": "おいかぜ + 高火力", "roles": [("tailwind", 1), ("fast_attacker", 1)]},
            "self_boost": {"label": "自己加速 (かそく / かるわざ / 加速技)", "roles": [("self_booster", 1), ("fast_attacker", 1)],
                           "core_roles": [("self_booster", 2)], "overlap_ok": True},
        },
    },
    "priority_bulky": {
        "label": "低速高火力と先制技", "win_condition": "priority_cleanup", "switching": "mixed",
        "support_roles": ["priority", "bulk", "status"],
        "description": "先制技持ちの高火力が上を取られても削り切る。削り役 (設置 / 状態異常) を添える",
        "meta": "priority",
        "branches": {
            "sucker_punch": {"label": "ふいうち軸", "roles": [("sucker", 1), ("priority_bulky", 1)], "overlap_ok": True},
            "steel_priority": {"label": "鋼 / 物理の先制技軸 (バレットパンチ / マッハパンチ)",
                               "roles": [("steel_priority", 1), ("priority_bulky", 1)], "overlap_ok": True},
        },
    },
    "anti_meta": {
        "label": "環境上位への回答", "win_condition": "anti_meta", "switching": "mixed",
        "support_roles": ["speed_control", "priority"],
        "description": "上位脅威 (重み順) への被覆が高い個体を軸にする",
        "meta": "none",
        "branches": {"top_threats": {"label": "上位脅威の重み順", "roles": [("answer", 2)]}},
    },
    "special": {
        "label": "特殊な勝ち筋", "win_condition": "anti_meta", "switching": "mixed", "special": True,
        "support_roles": ["bulk", "status", "priority"],
        "description": "流行らないが相手を倒す手段が特殊な型。判定は learnset ベース (使用率に頼らない)。全分岐で合計 BUILD_ARCHETYPE_SPECIAL_MAX_CORES の core まで",
        "meta": "special",
        "branches": {
            "perish_trap": {"label": "ほろびのうた + 交代封じ", "roles": [("perish_singer", 1), ("trapper", 1)], "overlap_ok": True},
            "ohko": {"label": "一撃必殺 (ぜったいれいど / じわれ / つのドリル / ハサミギロチン)", "roles": [("ohko_user", 1)]},
            "destiny_bond": {"label": "みちづれ", "roles": [("destiny_bond", 1)]},
            "curse_ghost": {"label": "のろい (ゴースト) + まもる / みがわり", "roles": [("curse_ghost", 1)]},
            "leech_seed": {"label": "やどりぎのタネ + まもる / みがわり", "roles": [("leech_seeder", 1)]},
            "endeavor_sash": {"label": "がむしゃら + タスキ + 先制技", "roles": [("endeavor_sash", 1)]},
            "counter_coat": {"label": "カウンター / ミラーコート + タスキ", "roles": [("counter_user", 1)]},
        },
    },
}
AXIS_ORDER = tuple(ARCHETYPES)


# ---------------------------------------------------------------- 判定材料
@dataclass(frozen=True)
class Caps:
    species_id: str
    moves: tuple                 # 判定の基準の型 (代表型か規則のエース型) の技
    ability: str
    item: str
    abilities: tuple             # その種が持ちうる特性 (メガ後を含む)
    types: tuple
    spe: int                     # 素早さ種族値 (メガ後)
    can_learn: dict = field(default_factory=dict)   # move_id -> bool (軸が参照する技)
    speed_share: float = 0.0     # 上位脅威への先手率
    boost_share: float = 0.0
    boost_mult: float = 1.0      # 自己加速の倍率 (1.0 = 無し)
    bulk: float = 0.0
    offense: int = 0
    attack_moves: int = 0
    attack_types: int = 0
    coverage_mean: float = 0.0
    coverage: dict = field(default_factory=dict)   # threat_id -> 0..1
    usage: float = 0.0
    mega: bool = False
    grounded: bool = True
    has_setup: bool = False      # 基準の型に積み技 (加速特性を含む)
    can_setup: bool = False      # learnset に積み技


def moves_needed() -> set:
    """軸が参照する技 id (S3 で can_learn を引くため)"""
    out: set = set()
    for spec in ROLE_SPECS.values():
        for group in _groups(spec):
            for alt in group:
                out.update(alt.get("moves") or ())
    return out


def _groups(spec: dict) -> list:
    groups = []
    if spec.get("any_of"):
        groups.append(list(spec["any_of"]))
    for g in spec.get("all_of") or []:
        groups.append(list(g))
    return groups


def _alt_holds(caps: Caps, alt: dict) -> tuple:
    """条件 1 つ → (now: 型が既に満たす, can: S6 で満たせる)"""
    now = can = True
    if "moves" in alt:
        need = int(alt.get("min", 1))
        has = sum(1 for m in alt["moves"] if m in caps.moves)
        learn = sum(1 for m in alt["moves"] if m in caps.moves or caps.can_learn.get(m))
        now = now and has >= need
        can = can and learn >= need
    if "abilities" in alt:
        now = now and caps.ability in alt["abilities"]
        can = can and any(a in alt["abilities"] for a in caps.abilities)
    if "items" in alt:
        now = now and (caps.item or "") in alt["items"]
        # 持ち物は S6 で持たせられる (メガ石の型は除く)
        can = can and not caps.mega
    if "types" in alt:
        ok = any(t in alt["types"] for t in caps.types)
        now, can = now and ok, can and ok
    if "speed_share_min" in alt:
        ok = caps.speed_share >= float(alt["speed_share_min"])
        now, can = now and ok, can and ok
    if "speed_share_max" in alt:
        ok = caps.speed_share <= float(alt["speed_share_max"])
        now, can = now and ok, can and ok
    if "boost_mult_min" in alt:
        ok = caps.boost_mult >= float(alt["boost_mult_min"])
        now, can = now and ok, can and ok
    if alt.get("now_only"):
        can = now
    return now, can


def _group_holds(caps: Caps, group: list) -> tuple:
    now = any(_alt_holds(caps, a)[0] for a in group)
    can = any(_alt_holds(caps, a)[1] for a in group)
    return now, can


def offensive(caps: Caps) -> bool:
    """エース共通の火力・技範囲 (規則のエースと同じ閾値)"""
    return (caps.offense >= BUILD_RULE_ACE_MIN_OFFENSE and caps.attack_moves >= BUILD_RULE_ACE_MIN_ATTACK_MOVES
            and caps.attack_types >= BUILD_RULE_ACE_MIN_ATTACK_TYPES and caps.coverage_mean >= BUILD_RULE_ACE_MIN_COVERAGE)


def role_score(caps: Caps, spec: dict, special: bool = False, top_threats: Optional[list] = None,
               threat_weights: Optional[dict] = None) -> Optional[float]:
    """役割の条件を満たせば 0..1 の得点 (高いほど適任)、満たさなければ None。
    得点 = 条件を型が既に満たす (0.35) か覚えるだけ (0.15) + 被覆 + 使用率 (特殊な勝ち筋は使用率を見ない) + 役割固有の項。純粋"""
    if spec.get("not_mega") and caps.mega:
        return None
    if spec.get("grounded") and not caps.grounded:
        return None
    if "speed_share_max" in spec and caps.speed_share > float(spec["speed_share_max"]):
        return None
    if "bulk_min" in spec and caps.bulk < float(spec["bulk_min"]):
        return None
    if spec.get("offensive") and not offensive(caps):
        return None
    if spec.get("setup") and not (caps.has_setup or caps.can_setup):
        return None
    top_cov = None
    if "top_coverage_min" in spec:
        ids = list(top_threats or [])[:5]
        if not ids:
            return None
        w = threat_weights or {}
        tot = sum(float(w.get(t, 1.0)) for t in ids) or 1.0
        top_cov = sum(float(w.get(t, 1.0)) * float(caps.coverage.get(t, 0.0)) for t in ids) / tot
        if top_cov < float(spec["top_coverage_min"]):
            return None
    groups = _groups(spec)
    now_all, can_all = True, True
    for g in groups:
        n, c = _group_holds(caps, g)
        now_all, can_all = now_all and n, can_all and c
    if groups and not can_all:
        return None
    score = 0.0
    if groups:
        score += 0.35 if now_all else 0.15
    if spec.get("setup"):
        score += 0.1 if caps.has_setup else 0.0
    score += 0.25 * min(1.0, caps.coverage_mean / 0.6)
    if not special:
        score += 0.15 * min(1.0, caps.usage / 20.0)
    if spec.get("offensive"):
        score += 0.1 * min(1.0, caps.offense / 130.0)
    if "bulk_min" in spec:
        score += 0.1 * min(1.0, caps.bulk)
    if "speed_share_max" in spec:
        score += 0.1 * (1.0 - caps.speed_share)
    if top_cov is not None:
        score += 0.3 * top_cov
    return round(min(1.0, score), 4)


def branch_requirements(axis_id: str, branch_id: str) -> list:
    return list(ARCHETYPES[axis_id]["branches"][branch_id]["roles"])


def branch_core_roles(axis_id: str, branch_id: str) -> list:
    br = ARCHETYPES[axis_id]["branches"][branch_id]
    return list(br.get("core_roles") or br["roles"])


def branch_win_condition(axis_id: str, branch_id: str) -> str:
    br = ARCHETYPES[axis_id]["branches"][branch_id]
    return br.get("win_condition") or ARCHETYPES[axis_id]["win_condition"]


def qualify(caps_by: dict, axis_id: str, branch_id: str, top_threats: Optional[list] = None,
            threat_weights: Optional[dict] = None) -> dict:
    """{role: {species_id: score}} (分岐の roles と core_roles に出る役割すべて)。純粋"""
    special = bool(ARCHETYPES[axis_id].get("special"))
    roles = {r for r, _n in branch_requirements(axis_id, branch_id)} | {r for r, _n in branch_core_roles(axis_id, branch_id)}
    out: dict = {}
    for role in sorted(roles):
        spec = ROLE_SPECS[role]
        out[role] = {}
        for sid, caps in caps_by.items():
            sc = role_score(caps, spec, special=special, top_threats=top_threats, threat_weights=threat_weights)
            if sc is not None:
                out[role][sid] = sc
    return out


def assign_roles(members, requirements: list, qualified: dict, overlap_ok: bool = False) -> Optional[dict]:
    """並びの中で役割の最小数を (別個体で。overlap_ok なら兼任可) 満たす割り当て {role: [sid]}。満たせなければ None。
    小さい探索 (6 体 × 3 役割) なのでバックトラック。純粋"""
    ms = [m for m in members]
    slots = []
    for role, n in requirements:
        slots.extend([role] * int(n))
    assign: dict = {}
    used: set = set()

    def rec(i: int) -> bool:
        if i >= len(slots):
            return True
        role = slots[i]
        cands = sorted((s for s in ms if s in (qualified.get(role) or {})), key=lambda s: -qualified[role][s])
        for s in cands:
            if not overlap_ok and s in used:
                continue
            if s in assign.get(role, []):
                continue
            assign.setdefault(role, []).append(s)
            used.add(s)
            if rec(i + 1):
                return True
            assign[role].pop()
            if not assign[role]:
                del assign[role]
            if not any(s in v for v in assign.values()):
                used.discard(s)
        return False

    return assign if rec(0) else None


def lineup_ok(members, axis_id: str, branch_id: Optional[str], qualified_by: dict) -> bool:
    """軸つきコンセプトの並びが役割の最小数を満たすか。分岐が無ければ制約なし (True)。純粋"""
    if not branch_id or axis_id not in ARCHETYPES or branch_id not in ARCHETYPES[axis_id]["branches"]:
        return True
    q = qualified_by.get((axis_id, branch_id)) or qualified_by.get(f"{axis_id}:{branch_id}") or {}
    br = ARCHETYPES[axis_id]["branches"][branch_id]
    return assign_roles(members, br["roles"], q, bool(br.get("overlap_ok"))) is not None


# ---------------------------------------------------------------- 環境適合
def _share(threat_info: dict, pred: Callable) -> float:
    tot = sum(float(t.get("weight", 1.0)) for t in threat_info.values()) or 1.0
    return sum(float(t.get("weight", 1.0)) for t in threat_info.values() if pred(t)) / tot


def _has(t: dict, moves=(), abilities=()) -> bool:
    mv = set(t.get("moves") or ())
    ab = set(t.get("abilities") or ())
    if t.get("ability"):
        ab.add(t["ability"])
    return any(m in mv for m in moves) or any(a in ab for a in abilities)


def _types(t: dict) -> tuple:
    return tuple(t.get("types") or ())


def branch_fit(axis_id: str, branch_id: str, threat_info: dict,
               type_mult: Optional[Callable] = None, fast_spe: int = BUILD_ARCHETYPE_FAST_THREAT_SPE) -> tuple:
    """分岐の環境適合 0..1 と根拠 (日本語)。threat_info: {threat_id: {moves, abilities/ability, types, spe, weight, rock_mult}}。
    type_mult(attack_type, types) → 倍率 (無ければ弱点の項は 0)。純粋"""
    axis = ARCHETYPES[axis_id]
    br = axis["branches"][branch_id]
    kind = br.get("meta") or axis.get("meta") or "none"
    notes = []
    if not threat_info:
        return 0.5, ["脅威の情報なし (0.5)"]

    def S(pred):
        return _share(threat_info, pred)

    def weak_share(types_: tuple) -> float:
        if not type_mult or not types_:
            return 0.0
        return S(lambda t: any(float(type_mult(a, list(_types(t)))) >= 2.0 for a in types_))

    fast = S(lambda t: int(t.get("spe") or 0) >= fast_spe)
    taunt = S(lambda t: _has(t, ("taunt",)))
    priority = S(lambda t: _has(t, PRIORITY_MOVES))
    fit = 0.5
    if kind == "setup":
        counter = S(lambda t: _has(t, SETUP_COUNTER_MOVES, ("unaware", "infiltrator")))
        fit = 1.0 - 0.8 * counter
        notes.append(f"積みを崩す手 (挑発/吹き飛ばし/くろいきり/壁割り/てんねん) {counter:.0%}")
    elif kind == "baton":
        counter = S(lambda t: _has(t, SETUP_COUNTER_MOVES, ("unaware",)))
        phaze = S(lambda t: _has(t, PHAZE_MOVES))
        fit = 1.0 - 0.8 * counter - 0.5 * phaze
        notes.append(f"積みを崩す手 {counter:.0%} / バトンを止める手 {phaze:.0%}")
    elif kind == "trick_room":
        fit = 0.3 + 0.7 * fast - 0.5 * taunt - 0.3 * priority
        notes.append(f"素早さ {fast_spe} 以上の脅威 {fast:.0%} (有利) / 挑発 {taunt:.0%} / 先制技 {priority:.0%} (不利)")
    elif kind == "weather":
        others = {w for w in WEATHER_ABILITIES if w != branch_id}
        other_ab = tuple(a for w in others for a in WEATHER_ABILITIES[w])
        other_mv = tuple(m for w in others for m in WEATHER_MOVES[w])
        over = S(lambda t: _has(t, other_mv, other_ab))
        weak = weak_share(WEATHER_TYPES.get(branch_id, ()))
        fit = 1.0 - 0.8 * over + 0.3 * weak
        notes.append(f"天候を上書きする脅威 {over:.0%} / 強まるタイプが弱点の脅威 {weak:.0%}")
    elif kind == "terrain":
        others = {x for x in TERRAIN_ABILITIES if x != branch_id}
        other_ab = tuple(a for x in others for a in TERRAIN_ABILITIES[x])
        other_mv = tuple(m for x in others for m in TERRAIN_MOVES[x])
        over = S(lambda t: _has(t, other_mv, other_ab))
        fit = 1.0 - 0.6 * over
        notes.append(f"フィールドを上書きする脅威 {over:.0%}")
        if branch_id == "psychic":
            fit += 0.5 * priority
            notes.append(f"先制技持ち {priority:.0%} (サイコフィールドの価値)")
        elif branch_id == "grassy":
            fit += 0.2 * weak_share(("Grass",))
        elif branch_id == "electric":
            fit += 0.2 * weak_share(("Electric",))
        elif branch_id == "misty":
            dragon = S(lambda t: "Dragon" in _types(t))
            fit += 0.3 * dragon
            notes.append(f"ドラゴンタイプの脅威 {dragon:.0%}")
    elif kind == "hazard":
        rock = S(lambda t: float(t.get("rock_mult", 1.0)) >= 2.0)
        removal = S(lambda t: _has(t, REMOVAL_MOVES, ("magicbounce",)))
        fit = 0.4 + 0.6 * rock - 0.6 * removal
        notes.append(f"ステルスロックが弱点 {rock:.0%} (有利) / 除去持ち {removal:.0%} (不利)")
    elif kind == "web":
        airborne = S(lambda t: "Flying" in _types(t) or _has(t, (), ("levitate",)))
        removal = S(lambda t: _has(t, REMOVAL_MOVES, ("magicbounce",)))
        fit = 0.9 - 0.6 * airborne - 0.6 * removal
        notes.append(f"浮いている脅威 {airborne:.0%} / 除去持ち {removal:.0%}")
    elif kind == "cycle":
        hazard = S(lambda t: _has(t, HAZARD_MOVES))
        trap = S(lambda t: _has(t, (), TRAP_ABILITIES))
        fit = 1.0 - 0.6 * hazard - 0.8 * trap
        notes.append(f"設置技持ち {hazard:.0%} / 罠特性 {trap:.0%}")
    elif kind == "stall":
        setup = S(lambda t: _has(t, SETUP_MOVES_FOR_FIT))
        te = S(lambda t: _has(t, ("taunt", "encore")))
        fit = 1.0 - 0.7 * setup - 0.5 * te
        notes.append(f"積み技持ち {setup:.0%} / 挑発・アンコール {te:.0%}")
    elif kind == "offense":
        pivot = S(lambda t: _has(t, PIVOT_MOVES))
        fit = 1.0 - 0.4 * priority - 0.3 * pivot
        notes.append(f"先制技持ち {priority:.0%} / 交代技持ち {pivot:.0%}")
    elif kind == "priority":
        block = S(lambda t: _has(t, ("psychicterrain",), PRIORITY_BLOCK_ABILITIES))
        fit = 0.5 + 0.5 * fast - 0.9 * block
        notes.append(f"素早さ {fast_spe} 以上 {fast:.0%} (有利) / 先制技を無効にする特性・サイコフィールド {block:.0%}")
    elif kind == "special":
        if branch_id == "perish_trap":
            ghost = S(lambda t: "Ghost" in _types(t))
            sound = S(lambda t: _has(t, (), ("soundproof",)))
            fit = 0.8 - 0.7 * (ghost + taunt + sound)
            notes.append(f"ゴースト (交代封じ無効) {ghost:.0%} / 挑発 {taunt:.0%} / ぼうおん {sound:.0%}")
        elif branch_id == "ohko":
            sturdy = S(lambda t: _has(t, (), ("sturdy",)))
            fit = 0.6 - 0.6 * sturdy
            notes.append(f"がんじょう {sturdy:.0%}")
        elif branch_id == "destiny_bond":
            fit = 0.6 - 0.5 * taunt
            notes.append(f"挑発 {taunt:.0%}")
        elif branch_id == "curse_ghost":
            bounce = S(lambda t: _has(t, (), ("magicbounce",)))
            fit = 0.6 - 0.5 * (taunt + bounce)
            notes.append(f"挑発 {taunt:.0%} / マジックミラー {bounce:.0%}")
        elif branch_id == "leech_seed":
            grass = S(lambda t: "Grass" in _types(t))
            bounce = S(lambda t: _has(t, (), ("magicbounce",)))
            fit = 0.7 - 0.8 * grass - 0.5 * taunt - 0.4 * bounce
            notes.append(f"くさタイプ (無効) {grass:.0%} / 挑発 {taunt:.0%} / マジックミラー {bounce:.0%}")
        elif branch_id == "endeavor_sash":
            hazard = S(lambda t: _has(t, HAZARD_MOVES))
            fit = 0.7 - 0.6 * hazard
            notes.append(f"設置技持ち (タスキが割れる) {hazard:.0%}")
        elif branch_id == "counter_coat":
            status = S(lambda t: _has(t, STATUS_MOVES))
            fit = 0.6 - 0.4 * status - 0.4 * taunt
            notes.append(f"状態異常技持ち {status:.0%} / 挑発 {taunt:.0%}")
    else:
        fit = 1.0
        notes.append("常に成立 (材料は脅威の重み)")
    fit = max(0.0, min(1.0, fit))
    return round(fit, 3), notes


SETUP_MOVES_FOR_FIT = ("swordsdance", "nastyplot", "dragondance", "calmmind", "bulkup", "quiverdance", "shellsmash",
                       "agility", "irondefense", "curse", "victorydance", "howl", "coil", "workup", "tidyup", "filletaway")


# ---------------------------------------------------------------- core の組み立て (S4)
def _weak_to(core: tuple, caps_by: dict, threats: list, n: int = 3) -> list:
    if not threats:
        return []
    return sorted(threats, key=lambda t: max((caps_by[m].coverage.get(t, 0.0) for m in core if m in caps_by), default=0.0))[:n]


def branch_cores(axis_id: str, branch_id: str, qualified: dict, caps_by: dict, fit: float, threats: list,
                 coverage_fn: Optional[Callable] = None, k: int = BUILD_ARCHETYPE_CORES_PER_BRANCH,
                 role_top: int = BUILD_ARCHETYPE_ROLE_TOP) -> list:
    """分岐の役割 (core_roles) を候補上位 role_top から組み合わせ、得点 (役割の平均 + 被覆) の上位 k 個の core を
    コンセプト dict で返す。役割の候補が足りなければ空。core が 1 体ならプールで被覆の高い相方を足す (2 体以上にする)。純粋"""
    axis = ARCHETYPES[axis_id]
    br = axis["branches"][branch_id]
    overlap_ok = bool(br.get("overlap_ok"))
    core_roles = branch_core_roles(axis_id, branch_id)
    pools = []
    for role, n in core_roles:
        cands = sorted((qualified.get(role) or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:role_top]
        if len(cands) < n and not (overlap_ok and cands):
            return []
        pools.append((role, n, [s for s, _sc in cands]))
    combos = []
    for picks in itertools.product(*[list(itertools.combinations(p[2], min(p[1], len(p[2])))) for p in pools]):
        assign = {}
        members: list = []
        ok = True
        for (role, n, _c), chosen in zip(pools, picks):
            for s in chosen:
                if s in members and not overlap_ok:
                    ok = False
                    break
                if s not in members:
                    members.append(s)
            if not ok:
                break
            assign[role] = list(chosen)
        if not ok or not members:
            continue
        combos.append((tuple(members), assign))
    seen, out = set(), []
    for members, assign in combos:
        core = tuple(sorted(set(members)))
        if len(core) < 2:
            extra = sorted((s for s in caps_by if s not in core and offensive(caps_by[s])),
                           key=lambda s: (-caps_by[s].coverage_mean, s))
            if extra:
                core = tuple(sorted(core + (extra[0],)))
                assign = dict(assign, partner=[extra[0]])
        if core in seen or len(core) < 2:
            continue
        seen.add(core)
        role_sc = [qualified[r][s] for r, ss in assign.items() if r in qualified for s in ss if s in qualified[r]]
        sc = (sum(role_sc) / len(role_sc) if role_sc else 0.0) + (0.5 * float(coverage_fn(core)) if coverage_fn else 0.0)
        out.append((sc, core, assign))
    out.sort(key=lambda x: (-x[0], x[1]))
    concepts = []
    for sc, core, assign in out[:max(0, k)]:
        mega = next((m for m in core if caps_by.get(m) and caps_by[m].mega), None)
        concepts.append({"name": f"arch:{axis_id}:{branch_id}:{'+'.join(core)}", "core_ids": list(core), "mega_id": mega,
                         "win_condition": branch_win_condition(axis_id, branch_id),
                         "support_roles": list(axis.get("support_roles") or []),
                         "weak_to": _weak_to(core, caps_by, threats), "source": f"archetype:{axis_id}",
                         "archetype": axis_id, "branch": branch_id, "roles": assign, "switching": axis["switching"],
                         "fit": fit, "core_score": round(sc, 4)})
    return concepts


def build_context(caps_by: dict, threat_info: dict, threats: list, threat_weights: Optional[dict] = None,
                  coverage_fn: Optional[Callable] = None, type_mult: Optional[Callable] = None,
                  min_fit: float = BUILD_ARCHETYPE_MIN_FIT, k: int = BUILD_ARCHETYPE_CORES_PER_BRANCH,
                  special_max: int = BUILD_ARCHETYPE_SPECIAL_MAX_CORES, axes: Optional[list] = None) -> dict:
    """全軸 × 分岐の 適合 / 役割の候補 / core。特殊な勝ち筋は合計 special_max まで (適合 × core 得点の順)。純粋
    戻り値: {"fits": {(axis, branch): {"fit", "notes"}}, "qualified": {(axis, branch): {role: {sid: score}}},
             "cores": [concept], "skipped": [{axis, branch, reason}]}"""
    top_threats = sorted(threats, key=lambda t: -float((threat_weights or {}).get(t, 0.0)))
    fits, qualified, cores, skipped = {}, {}, [], []
    special_pool = []
    for axis_id in (axes or AXIS_ORDER):
        axis = ARCHETYPES[axis_id]
        for branch_id in axis["branches"]:
            fit, notes = branch_fit(axis_id, branch_id, threat_info, type_mult=type_mult)
            fits[(axis_id, branch_id)] = {"fit": fit, "notes": notes}
            q = qualify(caps_by, axis_id, branch_id, top_threats=top_threats, threat_weights=threat_weights)
            qualified[(axis_id, branch_id)] = q
            if fit < min_fit:
                skipped.append({"axis": axis_id, "branch": branch_id, "reason": f"環境適合 {fit} < {min_fit}"})
                continue
            cs = branch_cores(axis_id, branch_id, q, caps_by, fit, threats, coverage_fn=coverage_fn, k=k)
            if not cs:
                skipped.append({"axis": axis_id, "branch": branch_id, "reason": "役割を満たす個体がプールに無い"})
                continue
            if axis.get("special"):
                special_pool.extend(cs)
            else:
                cores.extend(cs)
    special_pool.sort(key=lambda c: (-(c["fit"] * (0.5 + c["core_score"])), c["name"]))
    kept = special_pool[:max(0, special_max)]
    kept_branches = {c["branch"] for c in kept}
    for bid in sorted({c["branch"] for c in special_pool[max(0, special_max):]} - kept_branches):
        skipped.append({"axis": "special", "branch": bid, "reason": f"特殊な勝ち筋の上限 {special_max} 超"})
    cores.extend(kept)
    return {"fits": fits, "qualified": qualified, "cores": cores, "skipped": skipped}


def context_cores(ctx: dict) -> list:
    return [dict(c) for c in (ctx.get("cores") or [])]


def qualified_lookup(ctx: dict) -> dict:
    return ctx.get("qualified") or {}


def label_ja(axis_id: Optional[str], branch_id: Optional[str] = None) -> str:
    axis = ARCHETYPES.get(axis_id or "")
    if not axis:
        return ""
    br = axis["branches"].get(branch_id or "")
    return f"{axis['label']} / {br['label']}" if br else axis["label"]


def llm_axes(ctx: dict, top: int = 3) -> list:
    """LLM に渡す軸の一覧 (軸ごとに 1 項目。分岐の適合と役割の候補上位)。特殊な勝ち筋も 1 項目"""
    out = []
    for axis_id in AXIS_ORDER:
        axis = ARCHETYPES[axis_id]
        branches = []
        for branch_id, br in axis["branches"].items():
            f = (ctx.get("fits") or {}).get((axis_id, branch_id)) or {}
            q = (ctx.get("qualified") or {}).get((axis_id, branch_id)) or {}
            roles = []
            for role, n in br["roles"]:
                cands = sorted((q.get(role) or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:top]
                roles.append({"role": role, "label": ROLE_SPECS[role]["label"], "min": n, "candidates": [s for s, _ in cands]})
            branches.append({"id": branch_id, "label": br["label"], "fit": f.get("fit"), "notes": f.get("notes") or [],
                             "roles": roles})
        out.append({"id": axis_id, "label": axis["label"], "description": axis["description"],
                    "switching": axis["switching"], "switching_ja": SWITCHING_JA.get(axis["switching"], axis["switching"]),
                    "special": bool(axis.get("special")), "branches": branches})
    return out


# ---------------------------------------------------------------- 型への反映 (S6)
def apply_to_team(team: list, axis_id: str, branch_id: Optional[str], qualified: dict, can_learn: Callable,
                  category_of=None, setup_moves=(), stones=frozenset(), legal_item: Optional[Callable] = None,
                  alternatives: Optional[dict] = None, abilities_of: Optional[Callable] = None) -> tuple:
    """並びの型に役割の技・特性・持ち物を保証する。役割の割り当ては assign_roles (最小数)。
    条件の群ごとに、型が既に満たしていなければ 特性 (その種が持てる) → 技 (覚える。差し込みは sets.inject_move) → 持ち物 の順で
    満たす。役割の item (壁役の ひかりのねんど 等) は メガ石・こだわり系でなければ持たせる。こだわり系の型に変化技を差し込むときは
    代替の持ち物 (alt:item) に替える。元の SetCandidate は変更しない。戻り値 (team, assignment or None, notes)"""
    from tools.team_build.sets import inject_move
    if not branch_id or axis_id not in ARCHETYPES or branch_id not in ARCHETYPES[axis_id]["branches"]:
        return team, None, []
    br = ARCHETYPES[axis_id]["branches"][branch_id]
    members = [c.species_id for c in team]
    assign = assign_roles(members, br["roles"], qualified, bool(br.get("overlap_ok")))
    if assign is None:
        return team, None, ["arch:unsatisfied"]
    by_sid = {c.species_id: c for c in team}
    notes: list = []
    # 同じ個体が複数の役割を兼ねるとき、別の役割の技 (回復技 等) を差し込みで潰さないよう、その個体の全役割の技を守る
    keep_by_sid: dict = {}
    for role, sids in assign.items():
        for sid in sids:
            for group in _groups(ROLE_SPECS[role]):
                for alt in group:
                    keep_by_sid.setdefault(sid, set()).update(alt.get("moves") or ())
    for role, sids in assign.items():
        spec = ROLE_SPECS[role]
        for sid in sids:
            c = by_sid[sid]
            for group in _groups(spec):
                if any(_alt_holds_set(c, a, can_learn)[0] for a in group):
                    continue
                done = False
                for alt in group:                      # 特性 (技の枠を使わない): その種が持てる特性なら型の特性を替える
                    if "abilities" in alt and not any(k in alt for k in ("moves", "items", "types")) and abilities_of:
                        have = [a for a in alt["abilities"] if a in (abilities_of(sid) or ())]
                        if have and (c.item or "") not in stones:
                            notes.append(f"arch:{role}:ability<-{c.ability}")
                            c = dc_replace(c, ability=have[0], source=c.source + "+arch",
                                           notes=list(c.notes) + [f"arch:{role}:ability"])
                            done = True
                            break
                for alt in group:
                    if done:
                        break
                    if "moves" in alt and not any(k in alt for k in ("abilities", "items", "types", "speed_share_min",
                                                                     "speed_share_max", "boost_mult_min")):
                        need = int(alt.get("min", 1))
                        learnable = [m for m in alt["moves"] if m in c.moves or can_learn(sid, m)]
                        if len(learnable) < need:
                            continue
                        moves = list(c.moves)
                        # 差し込んだ技 (と、この個体の他の役割の技) を次の差し込みで潰さない
                        protected = tuple(setup_moves) + tuple(alt["moves"]) + tuple(keep_by_sid.get(sid, ()))
                        for m in learnable:
                            if sum(1 for x in alt["moves"] if x in moves) >= need:
                                break
                            if m in moves:
                                continue
                            moves, replaced = inject_move(moves, m, category_of, protected)
                            notes.append(f"arch:{role}:{m}<-{replaced}")
                        c = dc_replace(c, moves=moves, source=c.source + "+arch", notes=list(c.notes) + [f"arch:{role}"])
                        if (c.item or "") in CHOICE_ITEMS:
                            c = _swap_choice_item(c, alternatives, stones, notes, role)
                        done = True
                    elif "items" in alt and not any(k in alt for k in ("moves", "abilities", "types")):
                        item = next((i for i in alt["items"] if legal_item is None or legal_item(i)), None)
                        if item and (c.item or "") not in stones and c.item != item:
                            notes.append(f"arch:{role}:item<-{c.item}")
                            c = dc_replace(c, item=item, source=c.source + "+arch", notes=list(c.notes) + [f"arch:{role}:item"])
                        done = True
            pref = spec.get("item")
            if pref and (c.item or "") not in stones and c.item != pref and (legal_item is None or legal_item(pref)):
                notes.append(f"arch:{role}:item<-{c.item}")
                c = dc_replace(c, item=pref, source=c.source + "+arch", notes=list(c.notes) + [f"arch:{role}:item"])
            by_sid[sid] = c
    return [by_sid[c.species_id] for c in team], assign, notes


def _alt_holds_set(c, alt: dict, can_learn: Callable) -> tuple:
    """SetCandidate に対する条件判定 (型の技・持ち物・特性だけ見る。数値の条件は判定済みとして True)"""
    now = can = True
    if "moves" in alt:
        need = int(alt.get("min", 1))
        has = sum(1 for m in alt["moves"] if m in c.moves)
        learn = sum(1 for m in alt["moves"] if m in c.moves or can_learn(c.species_id, m))
        now, can = now and has >= need, can and learn >= need
    if "abilities" in alt:
        now = now and (c.ability or "") in alt["abilities"]
    if "items" in alt:
        now = now and (c.item or "") in alt["items"]
    return now, can


def _swap_choice_item(c, alternatives: Optional[dict], stones, notes: list, role: str):
    alt = next((a for a in (alternatives or {}).get(c.species_id, [])
                if a.source == "alt:item" and a.item and a.item not in CHOICE_ITEMS and a.item not in stones), None)
    if alt is None:
        notes.append(f"arch:{role}:choice_item_kept")
        return c
    notes.append(f"arch:{role}:item<-{c.item}")
    return dc_replace(c, item=alt.item, notes=list(c.notes) + [f"arch:{role}:item"])


# ---------------------------------------------------------------- 表示 (一覧 / 報告)
def render_list_md() -> str:
    """一覧 (docs/TEAM_BUILD_ARCHETYPES.md §1 と同期させる表) を Markdown で返す"""
    lines = ["| # | id | 軸 | 勝ち筋 | 交代方針 | 分岐 (必須役割: 最小数) |", "|---|---|---|---|---|---|"]
    for i, axis_id in enumerate(AXIS_ORDER, start=1):
        a = ARCHETYPES[axis_id]
        brs = []
        for bid, br in a["branches"].items():
            roles = "、".join(f"{ROLE_SPECS[r]['label']}×{n}" for r, n in br["roles"])
            brs.append(f"{br['label']} ({roles})")
        lines.append(f"| {i} | {axis_id} | {a['label']}{' (特殊)' if a.get('special') else ''} | {a['win_condition']} | "
                     f"{SWITCHING_JA.get(a['switching'], a['switching'])} | {' / '.join(brs)} |")
    return "\n".join(lines)


def render_context_md(ctx: dict, species_ja: Optional[Callable] = None, top: int = 4) -> str:
    """run の s03_archetypes.json (build_context の結果) を Markdown で要約する (適合・根拠・役割の候補・core)"""
    ja = species_ja or (lambda s: s)
    lines = []
    fits = ctx.get("fits") or {}
    qualified = ctx.get("qualified") or {}
    for axis_id in AXIS_ORDER:
        axis = ARCHETYPES[axis_id]
        lines.append(f"### {axis['label']} ({axis_id}、{SWITCHING_JA.get(axis['switching'], axis['switching'])})")
        for bid, br in axis["branches"].items():
            f = fits.get((axis_id, bid)) or fits.get(f"{axis_id}:{bid}") or {}
            q = qualified.get((axis_id, bid)) or qualified.get(f"{axis_id}:{bid}") or {}
            lines.append(f"- **{br['label']}** 適合 {f.get('fit', '?')} ({'; '.join(f.get('notes') or [])})")
            for role, n in br["roles"]:
                cands = sorted((q.get(role) or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:top]
                lines.append(f"  - {ROLE_SPECS[role]['label']} ×{n}: " + ("、".join(f"{ja(s)} {sc:.2f}" for s, sc in cands) or "候補なし"))
        cores = [c for c in (ctx.get("cores") or []) if c.get("archetype") == axis_id]
        if cores:
            lines.append("  - core: " + " / ".join(f"{c['branch']}: {'+'.join(ja(s) for s in c['core_ids'])} ({c.get('core_score')})" for c in cores))
    sk = ctx.get("skipped") or []
    if sk:
        lines.append("")
        lines.append("見送り: " + "、".join(f"{s['axis']}/{s['branch']} ({s['reason']})" for s in sk))
    return "\n".join(lines)


def context_to_json(ctx: dict) -> dict:
    """タプルのキーを 'axis:branch' の文字列にして JSON にできる形へ"""
    return {"fits": {f"{a}:{b}": v for (a, b), v in (ctx.get("fits") or {}).items()},
            "qualified": {f"{a}:{b}": v for (a, b), v in (ctx.get("qualified") or {}).items()},
            "cores": ctx.get("cores") or [], "skipped": ctx.get("skipped") or []}


def context_from_json(doc: dict) -> dict:
    def key(k: str) -> tuple:
        a, _, b = k.partition(":")
        return (a, b)
    return {"fits": {key(k): v for k, v in (doc.get("fits") or {}).items()},
            "qualified": {key(k): v for k, v in (doc.get("qualified") or {}).items()},
            "cores": doc.get("cores") or [], "skipped": doc.get("skipped") or []}


def main() -> None:
    """python -m tools.team_build.archetypes --list | --report --run-id <run>"""
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(description="構築の軸 (archetype) の一覧と run の判定結果")
    ap.add_argument("--list", action="store_true", help="一覧を Markdown で表示")
    ap.add_argument("--report", action="store_true", help="run の s03_archetypes.json を要約")
    ap.add_argument("--run-id")
    args = ap.parse_args()
    if args.list or not args.report:
        print(render_list_md())
        for axis_id in AXIS_ORDER:
            a = ARCHETYPES[axis_id]
            print(f"\n{axis_id}: {a['label']} — {a['description']}")
    if args.report:
        if not args.run_id:
            ap.error("--report には --run-id が要る")
        p = Path(__file__).resolve().parent.parent.parent / "logs" / "build_search" / "runs" / args.run_id / "s03_archetypes.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        from advisor.ja_names import species_ja
        print(render_context_md(context_from_json(doc), species_ja=lambda s: species_ja(s) or s))


if __name__ == "__main__":
    main()
