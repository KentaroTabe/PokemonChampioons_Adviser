"""ダメージ照合と対象別の確認表 (2026-10-07 docs/USEFULNESS_VERIFICATION_PLAN_1007.md §10): ローカル Showdown の
simulate-battle (乱数の seed を固定) で対戦を回し、Showdown の対戦ログ (HP の変化・行動順・発動) を正解として、助言側の計算と突き合わせる。
正解側に助言側の計算は使わない。

    python -m tools.dmg_compare [--moves 200] [--seed S] [--teams-from <opponent_families.json>] [--showdown-dir <dir>]
                                [--json out.json] [--no-legal]

対象別の確認表 (各欄「一致数 / 確認数 / 未確認」):
  damage          ダメージ: 実ダメージ (正確な HP の差) が助言側 advisor.damage.calc_damage の乱数幅 (min〜max、許容 ±DMG_COMPARE_TOL_HP)
                  に入るか。急所・化けの皮・みがわり・まもるは対象外 (未確認に数える)。倒した / きあいのタスキ・がんじょうで残った手は
                  「助言側の最大が残り HP に届くか」で見る。両者が同じ状況になるよう、特性・持ち物・ランク・状態異常・天候・壁は
                  Showdown の値をそのまま使う (助言側の型の推定はここでは確かめない)
  order           優先度と素早さ順: 両者が技を選んだターンの先に動いた側と、助言側の予測 (advisor.search._priority の優先度 →
                  advisor.engine.effective_speed の素早さ (おいかぜ込み) → トリックルームで反転) が一致するか。同速・せんせいのツメ等は未確認
  incapacitation  行動不能 (まひ・ねむり・こおり・ひるみ): **局面ごとの一致ではなく「未対応の行動不能に遭遇した件数」**。
                  Showdown で起きた行動不能 1 件ごとに、その種類 (まひ / ねむり / こおり / ひるみ) を助言側の探索
                  (advisor.search.simulate_turn) が扱っているか (incapacitation_model: 同じ 1 ターンを状態だけ変えて与ダメージが減るか)
                  を見る。判定は種類ごとに 1 つなので、件数は「その種類の行動不能に何回遭遇したか」を表し、助言がその局面で正しかったかは表さない。
                  表の欄: unhandled_encounters (未対応の種類に遭遇した件数 = 旧 mismatch) / handled_encounters (= 旧 match) /
                  encounters (= 旧 checked)
  forme           形態遷移 (メガシンカ・フォルムチェンジ): 変化の後の実数値 (Showdown の request の stats) と特性が、助言側の図鑑
                  (advisor.dex の種族値 + 型の能力ポイント・性格) と vision.abilities.fixed_ability の特性に一致するか
  activation      特性と持ち物の発動: **発動の前提が一致したかではなく「対応記述の有無」**。発動ログ ([from] ability / item、
                  -activate、-enditem 等) に出た特性・持ち物の種類ごとに、助言側の表 (advisor/data/ability_effects.json の式、
                  item_effects.json、damage.py / engine.py が名前で扱う持ち物) に記述があるかを見る (効果の量や発動条件が正しいかは見ない)。
                  表の欄: described (記述あり = 旧 match) / not_described (記述なし = 旧 mismatch) / kinds (発動した種類 = 旧 checked)
  legal           合法手: p1 の決定ごとに助言エンジン (advisor.engine.evaluate) を回し、第一候補が Showdown の request で選べる行動か

両者の行動は seed を固定した乱数で選ぶ (自発の交代・メガシンカは DMG_COMPARE_SWITCH_PROB / DMG_COMPARE_MEGA_PROB)。チームは
--teams-from の opponent_families.json の DMG_COMPARE_TEAM_TIER 層 (既定 search。封印した holdout は使わない) から seed で選ぶ。
Showdown は `node pokemon-showdown simulate-battle --skip-build` を対戦ごとに 1 プロセス起動する (常駐のサーバー (8100) は使わない、
ビルドもしない)。純粋な部分 (ログの解析・照合・表の集計) は tests/test_dmg_compare.py。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import select
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Optional

from champions_agent.config import (
    DMG_COMPARE_BATTLE_TIMEOUT_SEC, DMG_COMPARE_ENGINE_WORKERS, DMG_COMPARE_FORMAT, DMG_COMPARE_MAX_TURNS,
    DMG_COMPARE_MEGA_PROB, DMG_COMPARE_MOVES, DMG_COMPARE_ROUNDING_HP, DMG_COMPARE_SEED, DMG_COMPARE_SWITCH_PROB,
    DMG_COMPARE_TEAM_TIER, DMG_COMPARE_TOL_HP)

REPO = Path(__file__).resolve().parent.parent
SHOWDOWN_DIR = REPO / "pokemon-showdown"
RUNS_DIR = REPO / "logs" / "build_search" / "runs"

CATEGORIES = ("damage", "order", "incapacitation", "forme", "activation", "legal")
CATEGORY_JA = {"damage": "ダメージ", "order": "優先度と素早さ順", "incapacitation": "行動不能 (まひ・ねむり・こおり・ひるみ)",
               "forme": "形態遷移 (メガ・フォルム)", "activation": "特性と持ち物の発動", "legal": "合法手"}
# 局面ごとの一致ではない欄 (2026-10-07 レビュー): 表示の見出しと --json の別名のキー
CATEGORY_MEASURE_JA = {"incapacitation": "未対応の行動不能に遭遇した件数", "activation": "対応記述の有無"}
# confirm_table の別名 (新しいキー → 旧キー)。旧キーも残す
TABLE_ALIASES = {"incapacitation": {"unhandled_encounters": "mismatch", "handled_encounters": "match", "encounters": "checked"},
                 "activation": {"described": "match", "not_described": "mismatch", "kinds": "checked"}}
SIDES = ("p1", "p2")
STAT_KEYS = ("atk", "def", "spa", "spd", "spe")
_EV_KEYS = {"HP": "hp", "Atk": "atk", "Def": "def", "SpA": "spa", "SpD": "spd", "Spe": "spe"}
_STATUS = {"par": "paralysis", "brn": "burn", "psn": "poison", "tox": "toxic", "slp": "sleep", "frz": "freeze"}
_WEATHER = {"raindance": "rain", "primordialsea": "rain", "sunnyday": "sun", "desolateland": "sun", "sandstorm": "sandstorm",
            "snowscape": "snow", "snow": "snow", "hail": "snow"}
_TERRAIN = {"electricterrain": "electric", "grassyterrain": "grassy", "psychicterrain": "psychic", "mistyterrain": "misty"}
_SIDE_COND = {"reflect": "reflect", "lightscreen": "light_screen", "auroraveil": "aurora_veil", "tailwind": "tailwind"}
CANT_KINDS = ("par", "slp", "frz", "flinch")
# ダメージの照合から外す発動 (Showdown の HP の差が技の威力を表さない)
_DAMAGE_SHIELDS = ("ability: Disguise", "ability: Ice Face", "Substitute", "move: Substitute", "move: Protect", "move: Detect",
                   "move: King's Shield", "move: Spiky Shield", "move: Baneful Bunker", "move: Silk Trap", "move: Burning Bulwark")
# 行動順の照合から外す発動 (確率で先に動く持ち物・特性)
_ORDER_RANDOM = ("item: Quick Claw", "item: Custap Berry", "ability: Quick Draw")


def toid(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ------------------------------------------------------------------ チーム本文 (純粋)
def parse_team_text(text: str) -> list:
    """Showdown 形式のチーム本文 → [{"species", "item", "ability", "nature", "evs" (本文の値のまま), "moves"}]"""
    out = []
    for block in (text or "").strip().split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        name, _, item = lines[0].partition("@")
        mon = {"species": name.strip(), "item": item.strip(), "ability": "", "nature": "", "evs": {}, "moves": []}
        for l in lines[1:]:
            if l.startswith("Ability:"):
                mon["ability"] = l.split(":", 1)[1].strip()
            elif l.startswith("EVs:"):
                for part in l.split(":", 1)[1].split("/"):
                    m = re.match(r"\s*(\d+)\s+(\w+)", part)
                    if m:
                        mon["evs"][_EV_KEYS.get(m.group(2), m.group(2).lower())] = int(m.group(1))
            elif l.endswith("Nature"):
                mon["nature"] = l.split()[0]
            elif l.startswith("- "):
                mon["moves"].append(l[2:].strip())
        out.append(mon)
    return out


def nickname(i: int) -> str:
    """i 番目 (0 始まり) の個体のニックネーム。Showdown は名前を省くと基本の種族名 (Floette-Eternal → Floette) にするので、
    フォルム違いでも本文の個体と結べるように番号の名前を付ける"""
    return f"M{i + 1}"


def to_sim_team(sets: list) -> list:
    """simulate-battle の >player に渡すチーム (JSON)。Lv50、ニックネームは nickname(i)"""
    return [{"name": nickname(i), "species": s["species"], "item": s["item"], "ability": s["ability"], "moves": list(s["moves"]),
             "nature": s["nature"], "evs": dict(s["evs"]), "ivs": {}, "level": 50, "gender": ""} for i, s in enumerate(sets)]


# ------------------------------------------------------------------ プロトコル (純粋)
def parse_ident(ident: str) -> tuple:
    """"p1a: Mimikyu" / "p1: Mimikyu" (request) → ("p1", "mimikyu")、"p2" → ("p2", None)。
    "p2: B" (場の効果の陣営) も名前を返すので、陣営として使う呼び出し側は名前を見ない"""
    m = re.match(r"^(p\d)[a-z]?(?::\s*(.*))?$", (ident or "").strip())
    if not m:
        return None, None
    return m.group(1), (toid(m.group(2)) or None)


def parse_hp(cond: str) -> tuple:
    """"131/131" → (131, 131, None)、"0 fnt" → (0, None, "fnt")、"88/167 par" → (88, 167, "par")"""
    s = (cond or "").strip()
    parts = s.split()
    status = parts[1] if len(parts) > 1 else None
    if "/" in parts[0]:
        a, b = parts[0].split("/", 1)
        return int(a), int(b), status
    return int(parts[0]), None, status or ("fnt" if parts[0] == "0" else None)


def species_of_details(details: str) -> str:
    return toid((details or "").split(",")[0])


def omniscient_lines(block_lines: list) -> list:
    """update の行から |split| の公開側を捨てて、全員の正確な HP が見える行の列にする"""
    out, i = [], 0
    while i < len(block_lines):
        l = block_lines[i]
        if l.startswith("|split|"):
            if i + 1 < len(block_lines):
                out.append(block_lines[i + 1])
            i += 3
            continue
        out.append(l)
        i += 1
    return out


def _tag(parts: list, prefix: str) -> Optional[str]:
    for p in parts:
        if p.startswith(prefix):
            return p[len(prefix):].strip()
    return None


class SimTracker:
    """simulate-battle の全員視点の行と request から、照合に使う観測を作る (純粋: 入力は行と request の dict だけ)。
    teams: {"p1": parse_team_text の列, "p2": ...}、builds: {"p1": {種族 id: {"ev", "nature"}}, ...} (助言側の型の前提)"""

    def __init__(self, teams: dict, builds: Optional[dict] = None):
        self.mons: dict = {s: {} for s in SIDES}
        for s in SIDES:
            for i, st in enumerate(teams.get(s) or []):
                key = toid(nickname(i))
                sid = toid(st["species"])
                self.mons[s][key] = {"key": key, "base_species": sid, "species": sid, "hp": None, "maxhp": None, "status": None,
                                     "boosts": {}, "item": toid(st["item"]) or None, "ability": toid(st["ability"]) or None,
                                     "types": None, "volatiles": set(), "last_move": None, "seen": False,
                                     "build": ((builds or {}).get(s) or {}).get(sid) or {"ev": {}, "nature": {}},
                                     "set_moves": [toid(m) for m in st["moves"]]}
        self.active: dict = {s: None for s in SIDES}
        # イリュージョン: プロトコルは化けた先の名前で書くので、request の場の個体 (正解) への読み替えを持つ (交代・replace で消す)
        self.alias: dict = {s: {} for s in SIDES}
        self.field = {"weather": None, "terrain": None, "trick_room": False}
        self.side_cond: dict = {s: set() for s in SIDES}
        self.mega_used = {s: False for s in SIDES}
        self.turn = 0
        self.cur: Optional[dict] = None
        self.choices: dict = {}
        self.turn_first: Optional[dict] = None
        self.turn_snap: Optional[dict] = None
        self.turn_flags: set = set()
        self.pending_forme: list = []
        self.stats_seen: set = set()
        self.n_moves = 0
        self.para_attempts = 0
        self.para_cant = 0
        self.obs: dict = {"damage": [], "order": [], "incapacitation": [], "forme": [], "activation": [], "stats": [], "legal": []}

    # -------------------------------------------------------------- 状態
    def _ident(self, ident: str) -> tuple:
        """プロトコルの個体名 → (陣営, 本当の個体の名前)。イリュージョンの読み替えを通す"""
        side, key = parse_ident(ident)
        if side in self.alias and key in self.alias[side]:
            key = self.alias[side][key]
        return side, key

    def mon(self, side: Optional[str], key: Optional[str], create: bool = True) -> Optional[dict]:
        """陣営と名前の個体。create=False なら知らない名前は None (陣営の名前 "p2: B" を個体にしない)"""
        if side not in self.mons or key is None:
            return None
        if key not in self.mons[side]:
            if not create:
                return None
            self.mons[side][key] = {"key": key, "base_species": key, "species": key, "hp": None, "maxhp": None, "status": None,
                                    "boosts": {}, "item": None, "ability": None, "types": None, "volatiles": set(),
                                    "last_move": None, "seen": False, "build": {"ev": {}, "nature": {}}, "set_moves": []}
        return self.mons[side][key]

    def snap(self, side: str) -> Optional[dict]:
        """場のポケモンの写し (照合の入力)。場に居なければ None"""
        m = self.mon(side, self.active.get(side))
        if m is None:
            return None
        s = {k: deepcopy(v) for k, v in m.items() if k not in ("volatiles",)}
        s["volatiles"] = sorted(m["volatiles"])
        s["side"] = side
        s["tailwind"] = "tailwind" in self.side_cond[side]
        s["screens"] = sorted(self.side_cond[side] - {"tailwind"})
        # 照合の内訳用 (助言エンジンは calc_damage にこの文脈を渡していない。そうりょうのつかさ・おはかまいり の差の説明に使う)
        s["fainted_allies"] = sum(1 for k, x in self.mons[side].items() if k != m["key"] and x.get("hp") == 0)
        return s

    def set_choice(self, side: str, choice: dict) -> None:
        """運転側がこのターンに選んだ行動 ({"kind": move/switch, "id", "mega"})。強制交代 (ひんし後) は渡さない"""
        self.choices[side] = choice

    def _activation(self, kind: str, name: str, ident: str, line: str) -> None:
        side, key = self._ident(ident)
        m = self.mon(side, key, create=False)
        self.obs["activation"].append({"turn": self.turn, "kind": kind, "id": toid(name), "name": name, "side": side,
                                       "species": (m or {}).get("species"), "line": line})

    def _close_ctx(self) -> None:
        c, self.cur = self.cur, None
        if not c or c.get("def") is None or c.get("atk") is None:
            return
        if not c["damage_hp"] and not c["immune"]:
            return
        hp_after = c["damage_hp"][-1] if c["damage_hp"] else c["hp_before"]
        maxhp = c["def"].get("maxhp")
        if c["hp_before"] is None or maxhp is None:
            return
        self.obs["damage"].append({
            "turn": c["turn"], "move": c["move"], "attacker": c["atk"], "defender": c["def"], "field": c["field"],
            "crit": c["crit"], "hits": c["hits"] or max(1, len(c["damage_hp"])), "damage": c["hp_before"] - hp_after,
            "hp_before": c["hp_before"], "hp_after": hp_after, "maxhp": maxhp, "ko": hp_after == 0, "events": c["events"],
            "immune": c["immune"], "miss": c["miss"]})

    def _finalize_turn(self) -> None:
        ch = self.choices
        if (self.turn_first is not None and self.turn_snap is not None and all(s in ch for s in SIDES)
                and all(ch[s].get("kind") == "move" for s in SIDES)):
            self.obs["order"].append({"turn": self.turn, "choices": deepcopy(ch), "first": self.turn_first["side"],
                                      "first_kind": self.turn_first["kind"], "snap": self.turn_snap, "flags": sorted(self.turn_flags)})
        self.choices = {}
        self.turn_first = None
        self.turn_snap = None
        self.turn_flags = set()

    def _first_action(self, side: str, kind: str) -> None:
        if self.turn_first is None:
            self.turn_first = {"side": side, "kind": kind}
            self.turn_snap = {"p1": self.snap("p1"), "p2": self.snap("p2"), "field": dict(self.field)}

    # -------------------------------------------------------------- 入力
    def feed_line(self, line: str) -> None:
        if not line.startswith("|"):
            return
        parts = line.split("|")[1:]
        tag = parts[0] if parts else ""
        if tag in ("", "upkeep"):
            self._close_ctx()
            return
        if tag == "turn":
            self._close_ctx()
            self._finalize_turn()
            self.turn = int(parts[1])
            return
        if tag in ("win", "tie"):
            self._close_ctx()
            return
        if tag in ("switch", "drag", "replace"):
            self._close_ctx()
            side, key = parse_ident(parts[1])
            prev = self.mon(side, self.active.get(side))
            if prev is not None and tag != "replace":
                prev.update(boosts={}, volatiles=set(), types=None, last_move=None)
            if side in self.alias:
                self.alias[side] = {}
            self.active[side] = key
            m = self.mon(side, key)
            m["species"] = m["details_species"] = species_of_details(parts[2])
            m["seen"] = True
            if len(parts) > 3 and parts[3]:
                hp, mx, st = parse_hp(parts[3])
                m["hp"] = hp
                m["maxhp"] = mx or m["maxhp"]
                m["status"] = None if st in (None, "fnt") else st
            return
        if tag in ("detailschange", "-formechange"):
            # detailschange = 恒久の変化 (メガシンカ等。request の details も変わる)、-formechange = 一時の変化 (バトルスイッチ等。
            # request の details は元のまま、stats は今の姿)
            side, key = self._ident(parts[1])
            m = self.mon(side, key)
            new = species_of_details(parts[2])
            if m is not None and new and new != m["species"]:
                self.pending_forme.append({"turn": self.turn, "side": side, "key": key, "from": m["species"], "to": new,
                                           "kind": "mega" if "mega" in new else "forme", "item": m["item"], "build": m["build"],
                                           "truth_before": m.get("truth_stats"), "truth_after": None})
                m["species"] = new
            if m is not None and tag == "detailschange" and new:
                m["details_species"] = new
                # メガシンカ等の後の特性はプロトコルに出ない (次の request で分かる)。それまでの写しに印を付け、request で直す
                m["ability_pending"] = True
            return
        if tag == "-mega":
            side, _key = parse_ident(parts[1])
            self.mega_used[side] = True
            return
        if tag == "move":
            self._close_ctx()
            side, key = self._ident(parts[1])
            move = toid(parts[2])
            flags = parts[4:] if len(parts) > 4 else []
            tside, tkey = parse_ident(parts[3]) if len(parts) > 3 and parts[3] else (None, None)
            self.n_moves += 1
            self._first_action(side, "move")
            atk = self.mon(side, key)
            if atk is not None and atk.get("status") == "par":
                self.para_attempts += 1
            if atk is not None and not _tag(flags, "[from]"):
                atk["last_move"] = move
            target_side = tside if tside and tside != side else None
            dfn = self.mon(target_side, self.active.get(target_side)) if target_side else None
            self.cur = {"turn": self.turn, "side": side, "move": move, "target_side": target_side,
                        "target_key": self.active.get(target_side) if target_side else None,
                        "atk": self.snap(side), "def": self.snap(target_side) if target_side else None, "field": dict(self.field),
                        "crit": False, "hits": None, "damage_hp": [], "events": [], "immune": False, "miss": "[miss]" in flags,
                        "hp_before": (dfn or {}).get("hp")}
            src = _tag(flags, "[from]")
            if src and (src.startswith("ability:") or src.startswith("item:")):
                k, _, n = src.partition(":")
                self._activation(k.strip(), n.strip(), parts[1], line)
            return
        if tag == "cant":
            self._close_ctx()
            side, key = self._ident(parts[1])
            reason = toid(parts[2]) if len(parts) > 2 else ""
            self._first_action(side, "cant")
            m = self.mon(side, key)
            if reason in CANT_KINDS:
                self.obs["incapacitation"].append({"turn": self.turn, "side": side, "species": (m or {}).get("species"),
                                                   "reason": reason, "status": (m or {}).get("status")})
            if reason == "par":
                self.para_attempts += 1
                self.para_cant += 1
            return
        # ---- 以下は状態の更新 (と、技の文脈への記録)
        side, key = self._ident(parts[1]) if len(parts) > 1 else (None, None)
        m = self.mon(side, key, create=False) if key else None
        src = _tag(parts, "[from]")
        if src and (src.startswith("ability:") or src.startswith("item:")) and len(parts) > 1:
            k, _, n = src.partition(":")
            of = _tag(parts, "[of]")
            self._activation(k.strip(), n.strip(), of or parts[1], line)
        c = self.cur
        is_target = c is not None and side == c["target_side"] and key == c["target_key"]
        if tag in ("-damage", "-heal", "-sethp"):
            if m is not None and len(parts) > 2:
                hp, mx, st = parse_hp(parts[2])
                m["hp"] = hp
                if mx:
                    m["maxhp"] = mx
                m["status"] = None if st in (None, "fnt") else st
            if tag == "-damage" and is_target and not src:
                c["damage_hp"].append(m["hp"])
        elif tag == "faint" and m is not None:
            m["hp"] = 0
        elif tag == "-crit" and is_target:
            c["crit"] = True
        elif tag == "-hitcount" and is_target and len(parts) > 2:
            try:
                c["hits"] = int(parts[2])
            except ValueError:
                pass
        elif tag == "-miss" and c is not None:
            c["miss"] = True
        elif tag == "-immune" and is_target:
            c["immune"] = True
        elif tag == "-status" and m is not None:
            m["status"] = parts[2]
        elif tag == "-curestatus" and m is not None:
            m["status"] = None
        elif tag in ("-boost", "-unboost") and m is not None and len(parts) > 3:
            d = int(parts[3]) * (1 if tag == "-boost" else -1)
            m["boosts"][parts[2]] = max(-6, min(6, m["boosts"].get(parts[2], 0) + d))
        elif tag == "-setboost" and m is not None and len(parts) > 3:
            m["boosts"][parts[2]] = int(parts[3])
        elif tag in ("-clearboost", "-clearallboost"):
            targets = [m] if (tag == "-clearboost" and m is not None) else [self.mon(s, self.active.get(s)) for s in SIDES]
            for t in targets:
                if t is not None:
                    t["boosts"] = {}
        elif tag == "-clearnegativeboost" and m is not None:
            m["boosts"] = {k: v for k, v in m["boosts"].items() if v > 0}
        elif tag == "-clearpositiveboost" and m is not None:
            m["boosts"] = {k: v for k, v in m["boosts"].items() if v < 0}
        elif tag == "-weather":
            name = toid(parts[1]) if len(parts) > 1 else ""
            self.field["weather"] = None if name in ("", "none") else _WEATHER.get(name, name)
        elif tag in ("-fieldstart", "-fieldend"):
            name = toid((parts[1] if len(parts) > 1 else "").replace("move:", ""))
            if name == "trickroom":
                self.field["trick_room"] = tag == "-fieldstart"
            elif name in _TERRAIN:
                self.field["terrain"] = _TERRAIN[name] if tag == "-fieldstart" else None
        elif tag in ("-sidestart", "-sideend") and side in self.side_cond and len(parts) > 2:
            name = toid(parts[2].replace("move:", ""))
            if name in _SIDE_COND:
                (self.side_cond[side].add if tag == "-sidestart" else self.side_cond[side].discard)(_SIDE_COND[name])
        elif tag == "-item" and m is not None and len(parts) > 2:
            m["item"] = toid(parts[2])
        elif tag == "-enditem" and m is not None and len(parts) > 2:
            m["item"] = None
            if not src:
                self._activation("item", parts[2], parts[1], line)
            if is_target:
                c["events"].append(f"enditem:{toid(parts[2])}")
        elif tag == "-ability" and m is not None and len(parts) > 2:
            m["ability"] = toid(parts[2])
            if not src:
                self._activation("ability", parts[2], parts[1], line)
        elif tag == "-activate" and len(parts) > 2:
            eff = parts[2]
            if eff.startswith("ability:") or eff.startswith("item:"):
                k, _, n = eff.partition(":")
                self._activation(k.strip(), n.strip(), parts[1], line)
            if eff in _ORDER_RANDOM:
                self.turn_flags.add(eff)
            if is_target or (c is not None and eff in _DAMAGE_SHIELDS and side == c.get("target_side")):
                c["events"].append(eff)
        elif tag in ("-start", "-end") and m is not None and len(parts) > 2:
            eff = parts[2]
            if tag == "-start" and toid(eff) == "typechange" and len(parts) > 3:
                m["types"] = [t.strip() for t in parts[3].split("/") if t.strip()]
            vol = toid(eff.replace("move:", ""))
            if vol == "disable" and len(parts) > 3:
                vol = f"disable_{toid(parts[3])}"
            if vol in ("taunt", "encore") or vol.startswith("disable_") or vol == "substitute":
                (m["volatiles"].add if tag == "-start" else m["volatiles"].discard)(vol)
            if tag == "-end" and vol == "disable":
                m["volatiles"] = {v for v in m["volatiles"] if not v.startswith("disable_")}

    def feed_request(self, side: str, req: dict) -> None:
        """request の自分側の情報 (正確な実数値・特性・持ち物・HP) で状態を合わせ、実数値の確認と形態遷移の正解を記録する。
        request が来た = シミュレータが入力を待っている = 今の技の処理は終わっているので、開いている技の文脈を先に閉じる"""
        self._close_ctx()
        pokes = ((req or {}).get("side") or {}).get("pokemon") or []
        true_active = next((parse_ident(p.get("ident", ""))[1] for p in pokes if p.get("active")), None)
        shown = self.active.get(side)
        if (true_active and shown and shown != true_active and not req.get("teamPreview")
                and true_active in self.mons.get(side, {})):
            # イリュージョン: 場に居るのは request の個体。プロトコルの名前 (化けた先) をこの個体に読み替える
            self.alias[side][shown] = true_active
            self.active[side] = true_active
            tm = self.mon(side, true_active)
            tm["seen"] = True
        for p in pokes:
            _s, key = parse_ident(p.get("ident", ""))
            m = self.mon(side, key)
            if m is None:
                continue
            ds = species_of_details(p.get("details", ""))
            if ds and ds != m.get("details_species"):       # details が変わったときだけ姿を変える (一時のフォルムは保つ)
                m["details_species"] = ds
                m["species"] = ds
            hp, mx, st = parse_hp(p.get("condition", "0 fnt"))
            m["hp"] = hp
            m["maxhp"] = mx or m["maxhp"]
            m["status"] = None if st in (None, "fnt") else st
            m["item"] = toid(p.get("item")) or None
            m["ability"] = toid(p.get("ability") or p.get("baseAbility")) or m["ability"]
            if m.get("ability_pending"):
                m["ability_pending"] = False
                self._patch_ability(side, key, m["ability"])
            truth = {"stats": dict(p.get("stats") or {}), "maxhp": m["maxhp"], "ability": m["ability"], "species": m["species"]}
            m["truth_stats"] = truth
            sk = (side, key, m["species"])
            if sk not in self.stats_seen and p.get("stats"):
                self.stats_seen.add(sk)
                self.obs["stats"].append({"side": side, "species": m["species"], "build": m["build"], "truth": truth})
        rest = []
        for f in self.pending_forme:
            m = self.mon(f["side"], f["key"]) if f["side"] == side else None
            if m is not None and m.get("truth_stats") and m["truth_stats"]["species"] == f["to"]:
                f["truth_after"] = m["truth_stats"]
                self.obs["forme"].append(f)
            else:
                rest.append(f)
        self.pending_forme = rest

    def _patch_ability(self, side: str, key: str, ability: Optional[str]) -> None:
        """姿が変わってから request が来るまでに作った写しの特性を、request の特性 (正解) に直す"""
        snaps = []
        for o in self.obs["damage"]:
            snaps += [o["attacker"], o["defender"]]
        for o in self.obs["order"]:
            snaps += [o["snap"].get("p1"), o["snap"].get("p2")]
        for s in snaps:
            if s and s.get("side") == side and s.get("key") == key and s.get("ability_pending"):
                s["ability"] = ability
                s["ability_pending"] = False
                s["ability_patched"] = True

    def finish(self) -> None:
        """対戦の終わり: 開いている技の文脈を閉じ、正解の来なかった形態遷移を記録する (未確認になる)"""
        self._close_ctx()
        self.obs["forme"].extend(self.pending_forme)
        self.pending_forme = []


# ------------------------------------------------------------------ 照合 (純粋)
def mon_view(snap: dict):
    """写し → advisor.damage.MonView (特性・持ち物・ランク・状態異常・タイプの変化は Showdown の値)。図鑑に無ければ None"""
    from advisor.damage import MonView
    from advisor.dex import get_dex
    dex = get_dex()
    sp = dex.species(snap.get("species")) or dex.species(snap.get("base_species"))
    if sp is None:
        return None
    hp_frac = 1.0
    if snap.get("hp") is not None and snap.get("maxhp"):
        hp_frac = max(0.0, min(1.0, snap["hp"] / snap["maxhp"]))
    return MonView(species_id=snap.get("species"), types=list(snap.get("types") or sp["types"]), base=sp["baseStats"],
                   hp_frac=hp_frac, status=_STATUS.get(snap.get("status") or ""), boosts=dict(snap.get("boosts") or {}),
                   ability=snap.get("ability"), item=snap.get("item"), ev=dict((snap.get("build") or {}).get("ev") or {}),
                   nature=dict((snap.get("build") or {}).get("nature") or {}))


def _field_view(field: dict, def_screens=()):
    from advisor.damage import FieldView
    sc = set(def_screens or ())
    return FieldView(weather=(field or {}).get("weather"), terrain=(field or {}).get("terrain"),
                     trick_room=bool((field or {}).get("trick_room")), reflect="reflect" in sc, light_screen="light_screen" in sc,
                     aurora_veil="aurora_veil" in sc)


def _breakdown(o: dict) -> dict:
    a, d = o["attacker"], o["defender"]
    return {"attacker": {"species": a.get("species"), "ability": a.get("ability"), "item": a.get("item"), "boosts": a.get("boosts"),
                         "status": a.get("status"), "types": a.get("types"), "fainted_allies": a.get("fainted_allies")},
            "defender": {"species": d.get("species"), "ability": d.get("ability"), "item": d.get("item"), "boosts": d.get("boosts"),
                         "status": d.get("status"), "hp_before": o.get("hp_before"), "maxhp": o.get("maxhp")},
            "weather": (o.get("field") or {}).get("weather"), "terrain": (o.get("field") or {}).get("terrain"),
            "screens": d.get("screens"), "events": o.get("events")}


def hit_scale(move: str, hits_calc: float, hits_obs: int) -> float:
    """助言側の幅 (期待回数 hits_calc ぶん) を、実際に当たった回数 hits_obs ぶんに直す倍率 (純粋)。
    助言側の hits は、威力が 1 発ごとに上がる連続技 (トリプルアクセル等: 最大回数 n より大きい) では威力の単位の合計
    (1 + 2 + … + n) なので、当たった回数 k の単位 k(k+1)/2 で割合をとる。それ以外は 1 発ぶん × 回数"""
    from advisor import effects as E
    mh = E.move_entry(move).get("multihit")
    nmax = max(mh) if isinstance(mh, (list, tuple)) and mh else (mh if isinstance(mh, (int, float)) else None)
    if nmax and hits_calc > float(nmax) + 1e-9:
        return (hits_obs * (hits_obs + 1) / 2.0) / (nmax * (nmax + 1) / 2.0)
    return hits_obs / hits_calc


def judge_damage(o: dict, tol_hp: float = DMG_COMPARE_TOL_HP, calc=None) -> dict:
    """1 手の実ダメージと助言側の乱数幅 (純粋)。match: True / False / None (対象外 = 未確認)"""
    if calc is None:
        from advisor.damage import calc_damage as calc
    row = {"turn": o["turn"], "move": o["move"], "actual_hp": o["damage"], "maxhp": o["maxhp"],
           "actual_pct": round(100.0 * o["damage"] / o["maxhp"], 1) if o["maxhp"] else None,
           "calc_min": None, "calc_max": None, "match": None, "reason": "", "breakdown": _breakdown(o)}
    if o.get("crit"):
        row["reason"] = "急所 (対象外)"
        return row
    shield = [e for e in o.get("events") or [] if e in _DAMAGE_SHIELDS]
    if shield:
        row["reason"] = f"{shield[0]} (対象外)"
        return row
    if o["attacker"].get("ability_pending") or o["defender"].get("ability_pending"):
        row["reason"] = "姿が変わった後の特性が分からないまま対戦が終わった (未確認)"
        return row
    a, d = mon_view(o["attacker"]), mon_view(o["defender"])
    if a is None or d is None:
        row["reason"] = "助言側の図鑑に無い種族 (未確認)"
        return row
    try:
        res = calc(a, d, o["move"], _field_view(o.get("field"), o["defender"].get("screens")))
    except Exception as e:      # 助言側の計算が落ちる手は未確認 (理由を残す)
        row["reason"] = f"助言側の計算が例外: {e!r}"
        return row
    row["calc_min"], row["calc_max"], row["calc_notes"] = res["min"], res["max"], res.get("notes")
    if o.get("immune"):
        row["match"] = res["max"] == 0
        row["reason"] = "無効 (Showdown)" + ("" if row["match"] else f" / 助言側は {res['min']}〜{res['max']}%")
        return row
    if res["max"] <= 0:
        row["match"] = o["damage"] <= 0
        row["reason"] = "" if row["match"] else "助言側は 0 (無効・変化技・条件不成立)"
        return row
    hits = int(o.get("hits") or 1)
    scale = hit_scale(o["move"], float(res.get("hits") or 1.0) or 1.0, hits)
    mx = o["maxhp"]
    lo = res["min"] * scale * mx / 100.0
    hi = res["max"] * scale * mx / 100.0
    tol = tol_hp + 0.001 * mx * hits      # 助言側の % は 0.1 刻みの丸め
    capped = bool(o.get("ko")) or (o.get("hp_after") == 1
                                   and any(e in ("enditem:focussash", "ability: Sturdy") for e in o.get("events") or []))
    row["calc_lo_hp"], row["calc_hi_hp"] = round(lo, 1), round(hi, 1)
    if capped:
        row["match"] = hi + tol >= o["damage"]
        row["reason"] = "残り HP で頭打ち (倒した・タスキ / がんじょう)" + ("" if row["match"] else ": 助言側の最大が届かない")
    else:
        row["match"] = lo - tol <= o["damage"] <= hi + tol
        if not row["match"]:
            small = o["damage"] < lo - tol
            row["outside_hp"] = round((lo - o["damage"]) if small else (o["damage"] - hi), 2)
            row["reason"] = (f"実ダメージが助言側の乱数幅より{'小さい' if small else '大きい'} (幅の外 {row['outside_hp']} HP"
                             f"、許容 {round(tol, 2)} HP)")
    return row


def predict_first(snap_p1: dict, snap_p2: dict, move_p1: str, move_p2: str, field: dict) -> tuple:
    """助言側の行動順の予測 (純粋): ("p1" / "p2" / None (同速), 根拠, 内訳)"""
    from advisor.engine import effective_speed
    from advisor.search import _priority
    v1, v2 = mon_view(snap_p1), mon_view(snap_p2)
    if v1 is None or v2 is None:
        return None, "図鑑に無い種族", {}
    fv = _field_view(field)
    p1, p2 = _priority(move_p1, v1, fv), _priority(move_p2, v2, fv)
    s1 = effective_speed(v1, {"tailwind": snap_p1.get("tailwind")}, field or {})
    s2 = effective_speed(v2, {"tailwind": snap_p2.get("tailwind")}, field or {})
    info = {"priority": [p1, p2], "speed": [s1, s2], "trick_room": bool((field or {}).get("trick_room"))}
    if p1 != p2:
        return ("p1" if p1 > p2 else "p2"), "priority", info
    if s1 == s2:
        return None, "同速", info
    faster = "p1" if s1 > s2 else "p2"
    if info["trick_room"]:
        faster = "p2" if faster == "p1" else "p1"
    return faster, "speed", info


def judge_order(o: dict) -> dict:
    ch = o["choices"]
    row = {"turn": o["turn"], "moves": [ch["p1"].get("id"), ch["p2"].get("id")], "actual_first": o["first"], "match": None, "reason": ""}
    if o.get("flags"):
        row["reason"] = f"{o['flags'][0]} が発動 (対象外)"
        return row
    if any((o["snap"].get(s) or {}).get("ability_pending") for s in SIDES):
        row["reason"] = "姿が変わった後の特性が分からないまま対戦が終わった (未確認)"
        return row
    pred, basis, info = predict_first(o["snap"]["p1"], o["snap"]["p2"], ch["p1"].get("id"), ch["p2"].get("id"), o["snap"].get("field"))
    row.update(predicted_first=pred, basis=basis, info=info,
               species=[(o["snap"]["p1"] or {}).get("species"), (o["snap"]["p2"] or {}).get("species")])
    if pred is None:
        row["reason"] = f"{basis} (未確認)"
        return row
    row["match"] = pred == o["first"]
    if not row["match"]:
        row["reason"] = f"助言側は {pred} が先 ({basis})、Showdown は {o['first']} が先"
    return row


def incapacitation_model() -> dict:
    """助言側の探索 (advisor.search.simulate_turn) が行動不能を扱うか (純粋な探り): 同じ 1 ターンを状態だけ変えて回し、
    行動側の与ダメージが減るか (= 行動できない場合を織り込んでいるか)。{"par", "slp", "frz", "flinch": bool, "detail": {...}}"""
    from dataclasses import replace

    from advisor.damage import MonView
    from advisor.dex import get_dex
    from advisor.search import Action, SimSide, simulate_turn
    dex = get_dex()

    def view(sid, spe_ev=0):
        sp = dex.species(sid)
        return MonView(species_id=sid, types=list(sp["types"]), base=sp["baseStats"], ev={"atk": 252, "hp": 252, "spe": spe_ev})

    def dealt(me_view, opp_view, my_move, opp_move):
        me, opp = SimSide(active=me_view, active_hp=1.0), SimSide(active=opp_view, active_hp=1.0)
        _me2, opp2 = simulate_turn(me, opp, Action("move", my_move), Action("move", opp_move), None, None, "avg")
        return 1.0 - opp2.active_hp

    slow, fast = view("snorlax"), view("weavile", spe_ev=252)
    base = dealt(slow, fast, "bodyslam", "swordsdance")
    detail = {"healthy": round(base, 4)}
    out = {}
    for reason, status in (("par", "paralysis"), ("slp", "sleep"), ("frz", "freeze")):
        v = dealt(replace(slow, status=status), fast, "bodyslam", "swordsdance")
        detail[reason] = round(v, 4)
        out[reason] = v < base * 0.999
    flinch = dealt(slow, fast, "bodyslam", "fakeout")
    plain = dealt(slow, fast, "bodyslam", "quickattack")
    detail["flinch"], detail["no_flinch"] = round(flinch, 4), round(plain, 4)
    out["flinch"] = flinch < plain * 0.999
    out["detail"] = detail
    return out


def judge_incapacitation(o: dict, model: dict) -> dict:
    """Showdown で起きた行動不能 1 件 (o) について、その種類を助言側の探索が扱うか (model、種類ごとに 1 つの判定)。
    match=False は「未対応の行動不能に遭遇した」1 件で、その局面の助言が誤っていたことは意味しない (局面ごとの照合ではない)"""
    ok = bool(model.get(o["reason"]))
    return {"turn": o["turn"], "side": o["side"], "species": o["species"], "reason": o["reason"], "match": ok,
            "note": "" if ok else "助言側の探索はこの行動不能を扱わない (同じ状況で行動できる前提)"}


def advisor_stats(species: str, build: dict) -> Optional[dict]:
    """助言側の実数値 (advisor.dex の種族値 + 型の能力ポイント・性格)。図鑑に無ければ None"""
    from advisor.damage import MonView
    from advisor.dex import get_dex
    sp = get_dex().species(species)
    if sp is None:
        return None
    v = MonView(species_id=species, base=sp["baseStats"], ev=dict((build or {}).get("ev") or {}),
                nature=dict((build or {}).get("nature") or {}))
    out = {k: v.stat(k, ignore_boost=True) for k in STAT_KEYS}
    out["hp"] = v.max_hp()
    return out


def judge_stats(o: dict) -> dict:
    adv = advisor_stats(o["species"], o["build"])
    truth = dict(o["truth"]["stats"], hp=o["truth"].get("maxhp"))
    row = {"side": o["side"], "species": o["species"], "advisor": adv, "truth": truth, "match": None, "diff": {}}
    if adv is None:
        return row
    row["diff"] = {k: (adv[k], truth.get(k)) for k in adv if truth.get(k) is not None and adv[k] != truth.get(k)}
    row["match"] = not row["diff"]
    return row


def judge_forme(o: dict) -> dict:
    from vision.abilities import fixed_ability
    row = {"turn": o["turn"], "side": o["side"], "from": o["from"], "to": o["to"], "kind": o["kind"], "match": None, "reason": ""}
    if not o.get("truth_after"):
        row["reason"] = "変化の後の request が来なかった (倒れた等。未確認)"
        return row
    adv = advisor_stats(o["to"], o["build"])
    if adv is None:
        row["reason"] = "助言側の図鑑に変化後の種族が無い (未確認)"
        return row
    truth = dict(o["truth_after"]["stats"], hp=o["truth_after"].get("maxhp"))
    diff = {k: (adv[k], truth.get(k)) for k in adv if truth.get(k) is not None and adv[k] != truth.get(k)}
    ab_adv = fixed_ability(o["to"], is_mega=o["kind"] == "mega", item_id=o.get("item"))
    ab_truth = o["truth_after"].get("ability")
    row.update(stats_diff=diff, ability_advisor=ab_adv, ability_truth=ab_truth)
    ab_ok = ab_adv == ab_truth
    row["match"] = not diff and ab_ok
    if not row["match"]:
        row["reason"] = " / ".join(([f"実数値の食い違い {diff}"] if diff else [])
                                   + ([f"特性: 助言側 {ab_adv} / Showdown {ab_truth}"] if not ab_ok else []))
    return row


def _source_text(rel: str) -> str:
    try:
        return (REPO / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def advisor_knows(kind: str, ident: str) -> bool:
    """助言側の表にその特性・持ち物の対応記述があるか (有無だけ。効果の中身の正しさは見ない)。
    特性: ability_effects.json の式 (formula) か damage.py の従来の辞書。
    持ち物: item_effects.json (activation / on_damaging_move) か、damage.py / engine.py が名前で扱っているもの"""
    from advisor import effects as E
    from advisor.dex import _item_effects
    ident = toid(ident)
    if kind == "ability":
        return bool(E.ability_formulas(ident)) or f'"{ident}"' in _source_text("advisor/damage.py")
    tbl = _item_effects()
    if ident in ((tbl.get("activation") or {})) or ident in ((tbl.get("on_damaging_move") or {})):
        return True
    return any(f'"{ident}"' in _source_text(p) for p in ("advisor/damage.py", "advisor/engine.py"))


def judge_activation(o: dict) -> dict:
    """発動した特性・持ち物 1 件 (o) について、助言側の表に対応記述があるか (advisor_knows)。
    match は「対応記述の有無」で、発動の前提 (効果の量・条件) が Showdown と一致したかは見ない"""
    ok = advisor_knows(o["kind"], o["id"])
    return {"turn": o["turn"], "kind": o["kind"], "id": o["id"], "species": o["species"], "match": ok,
            "note": "" if ok else "助言側の表に効果が無い"}


def judge_legal(o: dict) -> dict:
    row = {"turn": o["turn"], "best": o.get("best"), "match": None, "reason": o.get("error") or ""}
    b = o.get("best")
    if not b:
        row["reason"] = row["reason"] or "助言なし (未確認)"
        return row
    if b.get("kind") == "move":
        row["match"] = b.get("id") in set(o.get("legal_moves") or [])
    elif b.get("kind") == "switch":
        row["match"] = b.get("id") in set(o.get("legal_switches") or [])
    if row["match"] is False:
        row["reason"] = f"Showdown で選べない ({b.get('kind')}:{b.get('id')}、選べる技 {sorted(o.get('legal_moves') or [])})"
    row["n_ranked_illegal"] = o.get("n_ranked_illegal", 0)
    return row


def judge_all(obs: dict, model: Optional[dict] = None, tol_hp: float = DMG_COMPARE_TOL_HP) -> dict:
    """観測の全部を照合する (純粋)。{"rows": {category: [行]}, "stats": [行], "table": confirm_table, "para": {...}}"""
    model = model if model is not None else incapacitation_model()
    rows = {"damage": [judge_damage(o, tol_hp) for o in obs.get("damage") or []],
            "order": [judge_order(o) for o in obs.get("order") or []],
            "incapacitation": [judge_incapacitation(o, model) for o in obs.get("incapacitation") or []],
            "forme": [judge_forme(o) for o in obs.get("forme") or []],
            "activation": [judge_activation(o) for o in obs.get("activation") or []],
            "legal": [judge_legal(o) for o in obs.get("legal") or []]}
    stats = [judge_stats(o) for o in obs.get("stats") or []]
    return {"rows": rows, "stats": stats, "table": confirm_table(rows), "model": model}


def confirm_table(rows: dict) -> dict:
    """対象別の確認表 (純粋): {category: {"match", "checked", "unconfirmed", "mismatch"}}。checked = 一致 + 不一致、
    unconfirmed = 対象外・判定できなかった行。観測が 0 の欄は unconfirmed も 0 で、表示側で「未観測」とする。
    行動不能と発動は局面ごとの一致ではないので、意味に合う別名のキー (TABLE_ALIASES) も足す (旧キーも残す。計算は同じ):
      incapacitation: unhandled_encounters (未対応の行動不能に遭遇した件数) / handled_encounters / encounters
      activation    : described (対応記述あり) / not_described (記述なし) / kinds (発動した種類)"""
    out = {}
    for cat in CATEGORIES:
        rs = rows.get(cat) or []
        if cat == "activation":
            # 発動は同じ特性・持ち物が毎ターン出る (たべのこし等) ので、種類 (kind, id) ごとに 1 件と数える
            rs = list({(r.get("kind"), r.get("id")): r for r in rs}.values())
        m = sum(1 for r in rs if r.get("match") is True)
        mm = sum(1 for r in rs if r.get("match") is False)
        out[cat] = {"match": m, "checked": m + mm, "mismatch": mm, "unconfirmed": sum(1 for r in rs if r.get("match") is None)}
        for new_key, old_key in (TABLE_ALIASES.get(cat) or {}).items():
            out[cat][new_key] = out[cat][old_key]
    return out


def _table_line(cat: str, r: dict) -> str:
    """確認表の 1 行。行動不能・発動は一致数 / 確認数ではなく、その欄の意味の言い方で出す"""
    note = " (この実行では観測なし)" if r["checked"] + r["unconfirmed"] == 0 else ""
    if cat == "incapacitation":
        return (f"  {CATEGORY_JA[cat]} — {CATEGORY_MEASURE_JA[cat]}: {r['mismatch']} / 遭遇 {r['checked']}"
                f" (助言側が扱う種類 {r['match']}) / 未確認 {r['unconfirmed']}{note}")
    if cat == "activation":
        return (f"  {CATEGORY_JA[cat]} — {CATEGORY_MEASURE_JA[cat]}: 記述あり {r['match']} / 発動した種類 {r['checked']}"
                f" (記述なし {r['mismatch']}) / 未確認 {r['unconfirmed']}{note}")
    rate = f" ({r['match'] / r['checked']:.0%})" if r["checked"] else ""
    return f"  {CATEGORY_JA[cat]:<28s} {r['match']} / {r['checked']}{rate} / 未確認 {r['unconfirmed']}{note}"


def format_report(res: dict, n_moves: int, n_battles: int, para: Optional[dict] = None, limit: int = 20) -> str:
    t = res["table"]
    lines = [f"ダメージ照合: Showdown simulate-battle {n_battles} 戦 / {n_moves} 手", "",
             "対象別の確認表 (一致数 / 確認数 / 未確認。行動不能は「未対応の行動不能に遭遇した件数」、発動は「対応記述の有無」で、"
             "局面ごとの一致ではない):"]
    for cat in CATEGORIES:
        lines.append(_table_line(cat, t[cat]))
    st = res.get("stats") or []
    sm = sum(1 for r in st if r.get("match") is True)
    sc = sum(1 for r in st if r.get("match") is not None)
    lines.append(f"  (補助) 実数値 (request の stats と助言側)      {sm} / {sc} / 未確認 {len(st) - sc}")
    dmg = res["rows"]["damage"]
    if t["damage"]["checked"]:
        near = sum(1 for r in dmg if r.get("match") is False and r.get("outside_hp") is not None
                   and r["outside_hp"] <= DMG_COMPARE_ROUNDING_HP)
        lines.append(f"  ダメージの一致率 (乱数幅の中): {t['damage']['match'] / t['damage']['checked']:.1%}"
                     f" / 不一致 {t['damage']['mismatch']} 件のうち幅の外 {DMG_COMPARE_ROUNDING_HP} HP 以内 {near} 件 (整数の切り捨ての差の疑い)")
    na = len(res["rows"].get("activation") or [])
    lines.append(f"  (発動は種類ごとに数えた。発動の行は {na} 件)")
    if para:
        lines.append(f"  まひ: 行動を試みた {para.get('attempts', 0)} 回のうち動けなかった {para.get('cant', 0)} 回")
    md = res.get("model") or {}
    lines.append(f"  助言側の探索の行動不能の扱い: " + ", ".join(f"{k}={md.get(k)}" for k in CANT_KINDS))
    from collections import Counter
    for cat in CATEGORIES:
        bad = [r for r in res["rows"][cat] if r.get("match") is False]
        if not bad:
            continue
        if cat in ("activation", "incapacitation"):
            keyf = (lambda r: f"{r['kind']}:{r['id']}") if cat == "activation" else (lambda r: r["reason"])
            cnt = Counter(keyf(r) for r in bad)
            head = "未対応の行動不能に遭遇した一覧" if cat == "incapacitation" else "対応記述の無い発動の一覧"
            lines += ["", f"{head}: {CATEGORY_JA[cat]} ({len(bad)} 行): " + ", ".join(f"{k} {v} 回" for k, v in cnt.most_common())
                      + f" / {bad[0].get('note', '')}"]
            continue
        lines += ["", f"不一致の一覧: {CATEGORY_JA[cat]} ({len(bad)} 件、先頭 {min(limit, len(bad))} 件)"]
        for r in bad[:limit]:
            if cat == "damage":
                b = r["breakdown"]
                lines.append(f"  T{r['turn']} {b['attacker']['species']} {r['move']} → {b['defender']['species']}: 実 {r['actual_hp']} HP "
                             f"({r['actual_pct']}%) / 助言側 {r['calc_min']}〜{r['calc_max']}% ({r.get('calc_lo_hp')}〜{r.get('calc_hi_hp')} HP) "
                             f"{r['reason']}")
                lines.append(f"      攻 特性 {b['attacker']['ability']} 持ち物 {b['attacker']['item']} ランク {b['attacker']['boosts']} "
                             f"状態 {b['attacker']['status']} 味方のひんし {b['attacker'].get('fainted_allies')} / "
                             f"防 特性 {b['defender']['ability']} 持ち物 {b['defender']['item']} "
                             f"ランク {b['defender']['boosts']} / 天候 {b['weather']} 場 {b['terrain']} 壁 {b['screens']} 発動 {b['events']} "
                             f"注記 {r.get('calc_notes')}")
            else:
                lines.append(f"  T{r.get('turn')} {json.dumps({k: v for k, v in r.items() if k not in ('match',)}, ensure_ascii=False)[:400]}")
    unc = [r for r in dmg if r.get("match") is None]
    if unc:
        lines += ["", "ダメージの未確認の内訳: " + ", ".join(f"{k} {v}" for k, v in Counter(r["reason"] for r in unc).most_common())]
    unk = sorted({(r["kind"], r["id"]) for r in res["rows"]["activation"] if r.get("match") is False})
    if unk:
        lines.append("助言側の表に無い発動: " + ", ".join(f"{k}:{i}" for k, i in unk))
    return "\n".join(lines)


# ------------------------------------------------------------------ 合法手 (助言エンジンの入力を作る。純粋)
def engine_state(tracker: SimTracker, side: str, req: dict, resolver=None) -> Optional[dict]:
    """tracker の状態と request から、助言エンジン (advisor.engine.evaluate) の状態辞書を作る (side が「自分」)。
    自分の技は request の技欄 (PP 込み、かなしばり等で選べない技も含めて全部)、相手は場に出た個体の公開情報"""
    from advisor.infer import species_ja_name
    other = "p2" if side == "p1" else "p1"
    req_side = (req or {}).get("side") or {}
    act_moves = {}
    for a in (req or {}).get("active") or []:
        for mv in a.get("moves") or []:
            act_moves[mv.get("id")] = mv
    party, active_index = [], None
    for i, p in enumerate(req_side.get("pokemon") or []):
        _s, key = parse_ident(p.get("ident", ""))
        m = tracker.mon(side, key)
        if m is None:
            continue
        if p.get("active"):
            active_index = i
        hp, mx = m.get("hp"), m.get("maxhp")
        fainted = hp == 0
        moves = []
        for mid in p.get("moves") or []:
            info = act_moves.get(mid) if p.get("active") else None
            moves.append({"move_id": mid, "name_ja": mid, "pp": (info or {}).get("pp"), "max_pp": (info or {}).get("maxpp")})
        party.append({"species_id": m["species"], "species_ja": species_ja_name(m["base_species"]) or m["base_species"],
                      "hp_percent": (100.0 * hp / mx) if (hp is not None and mx) else None, "hp_current": hp, "hp_max": mx,
                      "status": "fainted" if fainted else _STATUS.get(m.get("status") or ""), "boosts": dict(m["boosts"]),
                      "ability_id": m.get("ability"), "item_id": m.get("item"), "is_mega": "mega" in (m["species"] or ""),
                      "is_active": bool(p.get("active")), "is_picked": True, "volatiles": sorted(m["volatiles"]),
                      "moves": moves, "revealed_moves": []})
    if active_index is None:
        return None
    opp_party, opp_active = [], None
    for key, m in tracker.mons[other].items():
        if not m.get("seen"):
            continue
        if key == tracker.active.get(other):
            opp_active = len(opp_party)
        hp, mx = m.get("hp"), m.get("maxhp")
        opp_party.append({"species_id": m["species"], "species_ja": species_ja_name(m["base_species"]) or m["base_species"],
                          "hp_percent": (100.0 * hp / mx) if (hp is not None and mx) else None,
                          "status": "fainted" if hp == 0 else _STATUS.get(m.get("status") or ""), "boosts": dict(m["boosts"]),
                          "ability_id": None, "item_id": None, "is_mega": "mega" in (m["species"] or ""),
                          "is_active": key == tracker.active.get(other), "moves": [], "revealed_moves": [], "volatiles": []})
    if opp_active is None:
        return None

    def side_dict(s, party_, idx):
        return {"active_index": idx, "tailwind": "tailwind" in tracker.side_cond[s],
                "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
                "screens": {k: k in tracker.side_cond[s] for k in ("reflect", "light_screen", "aurora_veil")},
                "party": party_, "remaining": sum(1 for p in party_ if p.get("status") != "fainted")}

    me_active = tracker.mon(side, tracker.active.get(side))
    opp_m = tracker.mon(other, tracker.active.get(other))
    return {"scene": "command", "turn": tracker.turn, "field": dict(tracker.field),
            "mega_used": {"player": tracker.mega_used[side], "opponent": tracker.mega_used[other]},
            "last_move": {"player": (me_active or {}).get("last_move"), "opponent": (opp_m or {}).get("last_move")},
            "player": side_dict(side, party, active_index), "opponent": side_dict(other, opp_party, opp_active)}


def legal_sets(tracker: SimTracker, side: str, req: dict) -> tuple:
    """request で選べる技 id と交代先の種族 id"""
    act = ((req or {}).get("active") or [{}])[0]
    moves = {m.get("id") for m in act.get("moves") or [] if not m.get("disabled") and (m.get("pp") is None or m.get("pp") > 0)}
    switches = set()
    if not act.get("trapped") and not act.get("maybeTrapped"):
        for p in ((req or {}).get("side") or {}).get("pokemon") or []:
            if p.get("active") or str(p.get("condition", "")).endswith(" fnt"):
                continue
            _s, key = parse_ident(p.get("ident", ""))
            m = tracker.mon(side, key)
            if m is not None:
                switches.add(m["species"])
    return moves, switches


# ------------------------------------------------------------------ 実行 (副作用: Showdown の子プロセス)
class SimProcess:
    """node pokemon-showdown simulate-battle --skip-build を 1 戦ぶん動かす (標準入出力。ブロックは空行区切り)"""

    def __init__(self, showdown_dir: Path, timeout: float = DMG_COMPARE_BATTLE_TIMEOUT_SEC):
        self.p = subprocess.Popen(["node", "pokemon-showdown", "simulate-battle", "--skip-build"], cwd=str(showdown_dir),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)   # 読まない stderr で詰まらないように捨てる
        self.buf = b""
        self.timeout = timeout

    def send(self, s: str) -> None:
        self.p.stdin.write((s + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def read_block(self) -> Optional[list]:
        deadline = time.time() + self.timeout
        while b"\n\n" not in self.buf:
            left = deadline - time.time()
            if left <= 0:
                return None
            r, _w, _x = select.select([self.p.stdout], [], [], left)
            if not r:
                return None
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                if self.buf.strip():
                    block, self.buf = self.buf, b""
                    return block.decode("utf-8").splitlines()
                return None
            self.buf += chunk
        block, self.buf = self.buf.split(b"\n\n", 1)
        lines = block.decode("utf-8").splitlines()
        return lines if lines else []

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.terminate()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def _choose(req: dict, rng: random.Random, switch_prob: float, mega_prob: float) -> tuple:
    """request から乱数で行動を選ぶ → (Showdown の選択文字列, tracker に渡す選択 or None)"""
    if req.get("teamPreview"):
        n = len(((req.get("side") or {}).get("pokemon") or []))
        order = list(range(1, n + 1))
        rng.shuffle(order)
        k = int(req.get("maxChosenTeamSize") or 3)
        return "team " + "".join(str(i) for i in order[:k]), None
    pokes = (req.get("side") or {}).get("pokemon") or []
    bench = [i + 1 for i, p in enumerate(pokes) if not p.get("active") and not str(p.get("condition", "")).endswith(" fnt")]
    if req.get("forceSwitch"):
        return (f"switch {rng.choice(bench)}" if bench else "pass"), None
    act = (req.get("active") or [{}])[0]
    moves = [(i + 1, m) for i, m in enumerate(act.get("moves") or []) if not m.get("disabled")]
    can_switch = bench and not act.get("trapped") and not act.get("maybeTrapped")
    if can_switch and (not moves or rng.random() < switch_prob):
        idx = rng.choice(bench)
        return f"switch {idx}", {"kind": "switch", "id": toid(pokes[idx - 1].get("details", "").split(",")[0])}
    if not moves:
        return "default", None
    i, m = rng.choice(moves)
    mega = bool(act.get("canMegaEvo")) and rng.random() < mega_prob
    return f"move {i}" + (" mega" if mega else ""), {"kind": "move", "id": m.get("id"), "mega": mega}


def _advisor_builds(text: str) -> dict:
    """助言側の型の前提 (champions_agent.env.advisor_player.register_team_text と同じ換算) → {種族 id: {"ev", "nature"}}"""
    from advisor.infer import species_ja_name
    from champions_agent.env.advisor_player import register_team_text
    got = register_team_text(text)
    out = {}
    for st in parse_team_text(text):
        sid = toid(st["species"])
        b = got.get(species_ja_name(sid) or sid)
        if b:
            out[sid] = {"ev": dict(b.get("ev") or {}), "nature": dict(b.get("nature") or {})}
    return out


def run_battle(texts: dict, seed: int, showdown_dir: Path, move_budget: int, legal: bool = True,
               fmt: str = DMG_COMPARE_FORMAT, switch_prob: float = DMG_COMPARE_SWITCH_PROB,
               mega_prob: float = DMG_COMPARE_MEGA_PROB, max_turns: int = DMG_COMPARE_MAX_TURNS,
               dump: Optional[list] = None) -> dict:
    """1 戦を回して観測を返す。move_budget 手に達したらその場で打ち切る。dump (list) を渡すと送受信の行を足す (再現・確認用)"""
    builds = {"p2": _advisor_builds(texts["p2"]), "p1": _advisor_builds(texts["p1"])}   # p1 を後に登録 (助言エンジンは p1 の型を見る)
    teams = {s: parse_team_text(texts[s]) for s in SIDES}
    tr = SimTracker(teams, builds)
    rng = random.Random(seed)
    evaluate = resolver = None
    if legal:
        import advisor.engine as eng
        eng.SEARCH_WORKERS = DMG_COMPARE_ENGINE_WORKERS
        evaluate = eng.evaluate
        try:
            from vision.normalize import NameResolver
            resolver = NameResolver()
        except Exception:
            resolver = None
    sim = SimProcess(showdown_dir)
    status = "ok"

    def send(s: str) -> None:
        if dump is not None:
            dump.append(s)
        sim.send(s)

    try:
        send(">start " + json.dumps({"formatid": fmt, "seed": [seed % 65536, (seed >> 4) % 65536, 7, 11]}))
        for s in SIDES:
            send(f">player {s} " + json.dumps({"name": s, "team": to_sim_team(teams[s])}))
        while True:
            block = sim.read_block()
            if block is None:
                status = "timeout"
                break
            if not block:
                continue
            if dump is not None:
                dump.extend(block + [""])
            head = block[0]
            if head == "update":
                for l in omniscient_lines(block[1:]):
                    tr.feed_line(l)
                if tr.n_moves >= move_budget:
                    status = "budget"
                    break
                if tr.turn > max_turns:
                    status = "max_turns"
                    break
            elif head == "sideupdate" and len(block) > 1:
                side = block[1]
                for l in block[2:]:
                    if l.startswith("|error|"):
                        send(f">{side} default")
                        continue
                    if not l.startswith("|request|"):
                        continue
                    req = json.loads(l[len("|request|"):] or "null") or {}
                    tr.feed_request(side, req)
                    if req.get("wait"):
                        continue
                    if legal and side == "p1" and req.get("active") and not req.get("forceSwitch"):
                        tr.obs["legal"].append(_legal_obs(tr, side, req, evaluate, resolver))
                    cmd, choice = _choose(req, rng, switch_prob, mega_prob)
                    if choice is not None:
                        tr.set_choice(side, choice)
                    send(f">{side} {cmd}")
            elif head == "end":
                break
    finally:
        sim.close()
        tr.finish()
    return {"obs": tr.obs, "n_moves": tr.n_moves, "turns": tr.turn, "status": status,
            "para": {"attempts": tr.para_attempts, "cant": tr.para_cant}}


def _legal_obs(tr: SimTracker, side: str, req: dict, evaluate, resolver) -> dict:
    moves, switches = legal_sets(tr, side, req)
    o = {"turn": tr.turn, "legal_moves": sorted(moves), "legal_switches": sorted(switches), "best": None, "error": None}
    try:
        st = engine_state(tr, side, req, resolver)
        if st is None:
            o["error"] = "状態を作れない"
            return o
        adv = evaluate(st, resolver)
        if not adv.get("ok"):
            o["error"] = f"助言なし: {adv.get('reason')}"
            return o
        b = adv.get("best") or {}
        o["best"] = {"kind": b.get("kind"), "id": b.get("id")}
        o["n_ranked_illegal"] = sum(1 for a in adv.get("actions") or [] if a.get("score", 0) > -90 and (
            (a.get("kind") == "move" and a.get("id") not in moves) or (a.get("kind") == "switch" and a.get("id") not in switches)))
    except Exception as e:      # 助言エンジンが落ちた決定は未確認 (理由を残す)
        o["error"] = f"助言エンジンが例外: {e!r}"
    return o


def load_team_texts(split_path: Path, tier: str = DMG_COMPARE_TEAM_TIER) -> list:
    doc = json.loads(Path(split_path).read_text(encoding="utf-8"))
    if tier == "holdout":
        raise SystemExit("封印した holdout の構築は使わない")
    ids = list((doc.get("tiers") or {}).get(tier) or [])
    return [doc["texts"][i] for i in ids if i in (doc.get("texts") or {})]


def _latest_split() -> Optional[Path]:
    cands = sorted(RUNS_DIR.glob("*/opponent_families.json"), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def run(n_moves: int, seed: int, split_path: Path, showdown_dir: Path, legal: bool = True, log=print,
        dump: Optional[list] = None) -> dict:
    texts = load_team_texts(split_path)
    if len(texts) < 2:
        raise SystemExit(f"チームが足りない: {split_path}")
    rng = random.Random(seed)
    obs: dict = {k: [] for k in ("damage", "order", "incapacitation", "forme", "activation", "stats", "legal")}
    total, battles, para = 0, [], {"attempts": 0, "cant": 0}
    k = 0
    while total < n_moves:
        a, b = rng.sample(range(len(texts)), 2)
        bseed = seed + 1000 * k
        if dump is not None:
            dump.append(f"#### 対戦 {k + 1} seed {bseed} チーム {a} vs {b}")
        res = run_battle({"p1": texts[a], "p2": texts[b]}, bseed, showdown_dir, n_moves - total, legal=legal, dump=dump)
        k += 1
        total += res["n_moves"]
        for key in obs:
            obs[key] += res["obs"].get(key) or []
        para = {x: para[x] + res["para"][x] for x in para}
        battles.append({"seed": bseed, "teams": [a, b], "n_moves": res["n_moves"], "turns": res["turns"], "status": res["status"]})
        log(f"[dmg_compare] 対戦 {k}: {res['n_moves']} 手 / {res['turns']} ターン ({res['status']}) 累計 {total} 手")
        if res["n_moves"] == 0 and res["status"] != "ok":
            raise SystemExit(f"simulate-battle が動かない ({res['status']})")
    return {"obs": obs, "n_moves": total, "battles": battles, "para": para}


def main() -> None:
    ap = argparse.ArgumentParser(description="Showdown の対戦ログと助言側の計算の照合 (ダメージ・行動順・行動不能・形態・発動・合法手)")
    ap.add_argument("--moves", type=int, default=DMG_COMPARE_MOVES, help="照合する手数 (|move| の行の数)")
    ap.add_argument("--seed", type=int, default=DMG_COMPARE_SEED)
    ap.add_argument("--teams-from", default=None, help="opponent_families.json (既定: 最新の run)")
    ap.add_argument("--showdown-dir", default=str(SHOWDOWN_DIR))
    ap.add_argument("--no-legal", action="store_true", help="合法手の確認 (助言エンジンを回す) を省く")
    ap.add_argument("--json", default=None, help="結果 (表・行・観測) の保存先")
    ap.add_argument("--dump-log", default=None, help="simulate-battle との送受信の全行の保存先 (確認・再現用)")
    args = ap.parse_args()
    split = Path(args.teams_from) if args.teams_from else _latest_split()
    if split is None or not split.exists():
        raise SystemExit("チームの元 (opponent_families.json) がありません。--teams-from で指定")
    t0 = time.time()
    dump: Optional[list] = [] if args.dump_log else None
    out = run(args.moves, args.seed, split, Path(args.showdown_dir), legal=not args.no_legal, dump=dump)
    if args.dump_log:
        Path(args.dump_log).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dump_log).write_text("\n".join(dump) + "\n", encoding="utf-8")
    res = judge_all(out["obs"])
    print(format_report(res, out["n_moves"], len(out["battles"]), para=out["para"]))
    print(f"\n所要 {time.time() - t0:.0f} 秒 / チームの元 {split} (層 {DMG_COMPARE_TEAM_TIER}) / seed {args.seed}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"table": res["table"], "table_measures": CATEGORY_MEASURE_JA,
                                               "rows": res["rows"], "stats": res["stats"], "model": res["model"],
                                               "battles": out["battles"], "para": out["para"], "n_moves": out["n_moves"],
                                               "seed": args.seed, "teams_from": str(split)},
                                              ensure_ascii=False, indent=1, default=list), encoding="utf-8")
        print(f"保存: {args.json}")


if __name__ == "__main__":
    main()
