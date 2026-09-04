"""助言エンジン (advisor.engine.evaluate) をそのままプレイヤーにする (advisor-as-player)。

これまで「アドバイザー自身の対戦強度」は一度も測られていなかった
(h2h/ベンチは RL方策、check_search_expert は探索プレイヤーの強さ)。
探索改良 (P7/P6/P8) を助言に統合する判断 (P9) には、助言エンジンを
プレイヤーとして同じ固定軸で測る経路が要る。

poke-env の Battle を助言エンジンの状態辞書 (vision/state と同じ形) に変換し、
evaluate() の best をそのまま行動にする。自分側の型 (能力ポイント/性格) は
チームテキストから登録して、実戦で my_team.json に登録済みなのと同じ条件にする。
"""
from __future__ import annotations

import re
import time
from typing import Optional

from advisor.ev_infer import _nature_mult
from advisor.infer import species_ja_name
from champions_agent.env.search_expert import (
    _STATUS_MAP, _WEATHER_MAP, _TERRAIN_MAP, _names)

_STATS = ("hp", "atk", "def", "spa", "spd", "spe")
_EV_KEYS = {"HP": "hp", "Atk": "atk", "Def": "def",
            "SpA": "spa", "SpD": "spd", "Spe": "spe"}

# 自分側の型 (シム用): species_ja -> {"ev","nature","item_ja","ability_ja"}
_SIM_BUILDS: dict = {}
_orig_get_my_build = None


def _install_build_hook() -> None:
    """advisor.my_team.get_my_build を、シム登録 → 従来の順で引くようにする"""
    global _orig_get_my_build
    import advisor.my_team as mt
    if _orig_get_my_build is not None:
        return
    _orig_get_my_build = mt.get_my_build

    def hooked(species_ja):
        b = _SIM_BUILDS.get(species_ja or "")
        return b if b else _orig_get_my_build(species_ja)

    mt.get_my_build = hooked


def register_team_text(text: str) -> dict:
    """Showdownチームテキストから自分側の型を登録する。戻り値: 登録した辞書"""
    _install_build_hook()
    out = {}
    for block in (text or "").strip().split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        head = lines[0].split("@")[0].strip()
        sid = re.sub(r"[^a-z0-9]", "", head.lower())
        ja = species_ja_name(sid) or sid
        ev, nature = {}, {}
        for l in lines[1:]:
            if l.startswith("EVs:"):
                for part in l.split(":", 1)[1].split("/"):
                    m = re.match(r"\s*(\d+)\s+(\w+)", part)
                    if m:
                        pts, key = int(m.group(1)), m.group(2)
                        k = _EV_KEYS.get(key, key.lower())
                        # 能力ポイント (0-32) 表記なら努力値へ換算
                        ev[k] = min(252, pts * 8) if pts <= 32 else min(252, pts)
            elif l.endswith("Nature"):
                nature = _nature_mult(l.split()[0].lower())
        build = {"ev": {s: ev.get(s, 0) for s in _STATS} if ev else {},
                 "nature": nature, "item_ja": None, "ability_ja": None}
        _SIM_BUILDS[ja] = build
        out[ja] = build
    return out


def _mon_entry(p, own: bool, resolver, active: bool, available=None) -> dict:
    sid = p.species or ""
    status = p.status
    st = _STATUS_MAP.get(getattr(status, "name", str(status)).upper()) \
        if status is not None else None
    if p.fainted:
        st = "fainted"
    item = p.item if p.item not in (None, "", "unknown_item") else None
    entry = {
        "species_id": sid,
        "species_ja": species_ja_name(sid) or sid,
        "hp_percent": float(p.current_hp_fraction or 0.0) * 100.0,
        "status": st,
        "boosts": dict(p.boosts or {}),
        "ability_id": p.ability or None,
        "item_id": item,
        "is_mega": "mega" in sid,
        "is_active": active,
        "moves": [],
        "revealed_moves": [],
    }
    if own:
        if getattr(p, "max_hp", None):
            entry["hp_max"] = p.max_hp
            entry["hp_current"] = p.current_hp
        entry["is_picked"] = True
        moves = list((p.moves or {}).values())
        if active and available is not None:
            moves = [m for m in moves if m.id in available] or moves
        for m in moves[:4]:
            entry["moves"].append({
                "move_id": m.id,
                "name_ja": (resolver.ja_of("moves", m.id) if resolver else None)
                or m.id,
                "pp": getattr(m, "current_pp", None),
                "max_pp": getattr(m, "max_pp", None)})
    else:
        for m in list((p.moves or {}).values())[:4]:
            ja = (resolver.ja_of("moves", m.id) if resolver else None) or m.id
            entry["revealed_moves"].append(ja)
    return entry


def _side_state(team_values, active, own: bool, side_conditions, resolver,
                available=None) -> dict:
    party, active_index = [], None
    for i, p in enumerate(team_values):
        is_active = p is active
        if is_active:
            active_index = i
        party.append(_mon_entry(p, own, resolver, is_active, available))
    names = _names(side_conditions)
    counts = {getattr(k, "name", str(k)).upper(): v
              for k, v in (side_conditions or {}).items()}
    return {
        "active_index": active_index,
        "tailwind": "TAILWIND" in names,
        "hazards": {"stealth_rock": "STEALTH_ROCK" in names,
                    "spikes": int(counts.get("SPIKES", 0) or 0),
                    "toxic_spikes": int(counts.get("TOXIC_SPIKES", 0) or 0),
                    "sticky_web": "STICKY_WEB" in names},
        "screens": {"reflect": "REFLECT" in names,
                    "light_screen": "LIGHT_SCREEN" in names,
                    "aurora_veil": "AURORA_VEIL" in names},
        "party": party,
        "remaining": sum(1 for p in team_values if not p.fainted),
    }


def battle_to_state(battle, resolver=None) -> Optional[dict]:
    """poke-env Battle -> 助言エンジンの状態辞書"""
    active = battle.active_pokemon
    opp_active = battle.opponent_active_pokemon
    if active is None or opp_active is None:
        return None
    available = {m.id for m in (battle.available_moves or [])}
    weather = None
    for w in (battle.weather or {}):
        weather = _WEATHER_MAP.get(getattr(w, "name", str(w)).upper())
    terrain, trick_room = None, False
    for f in (battle.fields or {}):
        name = getattr(f, "name", str(f)).upper()
        terrain = _TERRAIN_MAP.get(name, terrain)
        if name == "TRICK_ROOM":
            trick_room = True
    own_vals = list(battle.team.values())
    opp_vals = list(battle.opponent_team.values())
    return {
        "scene": "command",
        "turn": battle.turn,
        "field": {"weather": weather, "terrain": terrain,
                  "trick_room": trick_room},
        "mega_used": {"player": any("mega" in (p.species or "") for p in own_vals),
                      "opponent": any("mega" in (p.species or "") for p in opp_vals)},
        "player": _side_state(own_vals, active, True, battle.side_conditions,
                              resolver, available),
        "opponent": _side_state(opp_vals, opp_active, False,
                                battle.opponent_side_conditions, resolver),
    }


def choose_from_advice(battle, advice: dict) -> Optional[dict]:
    """助言の best を poke-env の行動に写す。選べなければ None"""
    if not advice or not advice.get("ok"):
        return None
    cands = [advice.get("best")] + list(advice.get("actions") or [])
    available = {m.id: m for m in (battle.available_moves or [])}
    switchable = {p.species: p for p in (battle.available_switches or [])}
    for a in cands:
        if not a:
            continue
        if a.get("kind") == "move" and a.get("id") in available:
            return {"kind": "move", "move": available[a["id"]],
                    "mega": bool(battle.can_mega_evolve), "pokemon": None}
        if a.get("kind") == "switch" and a.get("id") in switchable:
            return {"kind": "switch", "move": None, "mega": False,
                    "pokemon": switchable[a["id"]]}
    return None


def make_advisor_player(team_source=None, stats: Optional[dict] = None,
                        latencies: Optional[list] = None, **player_kwargs):
    """助言エンジンで戦う poke-env Player を作る。

    team_source: last_text 属性を持つ Teambuilder (自分側の型登録に使う)。
    stats / latencies: 診断用の集計先 (省略可)。
    """
    from poke_env.player import Player
    from advisor.engine import evaluate
    from vision.normalize import NameResolver
    from champions_agent.env.search_expert import teampreview_order
    resolver = NameResolver()
    stats = stats if stats is not None else {}
    _install_build_hook()

    class _AdvisorPlayer(Player):
        def choose_move(self, battle):
            t0 = time.perf_counter()
            try:
                text = getattr(team_source, "last_text", None)
                if text and stats.get("_registered") != text:
                    register_team_text(text)
                    stats["_registered"] = text
                state = battle_to_state(battle, resolver)
                d = choose_from_advice(battle, evaluate(state, resolver)) \
                    if state else None
            except Exception as e:
                stats["error"] = stats.get("error", 0) + 1
                stats["last_error"] = repr(e)
                d = None
            if latencies is not None:
                latencies.append((time.perf_counter() - t0) * 1000.0)
            if d is None:
                stats["fallback"] = stats.get("fallback", 0) + 1
                return self.choose_random_move(battle)
            stats["decide"] = stats.get("decide", 0) + 1
            stats[d["kind"]] = stats.get(d["kind"], 0) + 1
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    return _AdvisorPlayer(**player_kwargs)
