"""接続テスト後の「自パーティ改善案」。

セッションの実戦ログ (logs/battles/*.jsonl) から、
  1. 動きづらかった相手パーティ (負け、助言の最善手が低スコア/交代に追い込まれた決定の割合)
  2. 相手パーティの良い動き = 概念 (サイコフィールド + 先制技に弱い速い個体、トリックルーム、天候、設置、
     積み、先制技コア、対面操作 …) を、観測イベントと使用率メタの代表型から機械的にタグ付け
  3. 自パーティの構造的な弱点 (例: サイコフィールド下で先制技が使えないときの被覆の落ち込み)
  4. 改善案: 動きづらかった相手への対策候補 (所持プールの相性行列) と 1 枠入替の案 (被覆の差分、未測定の draft)
を出す。数値は tools/team_build/interaction の相性行列と実戦ログから計算し、判定はしない。

    python -m tools.party_improvements --session                  # 接続テスト開始マーカー以降の対戦
    python -m tools.party_improvements --last 10
    python -m tools.party_improvements --opponents armarouge,raichu  # 仮想の相手パーティの概念検討 (ログ不要)
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from champions_agent.config import (PARTY_IMPROVE_FRAIL_FAST_DEF, PARTY_IMPROVE_FRAIL_FAST_SPE,
                                    PARTY_IMPROVE_LOSS_WEIGHT, PARTY_IMPROVE_LOW_SCORE, PARTY_IMPROVE_MIN_DECISIONS,
                                    PARTY_IMPROVE_SLOW_SPE, PARTY_IMPROVE_TOP_PARTIES, PARTY_IMPROVE_TOP_PROPOSALS,
                                    PARTY_IMPROVE_TOP_THREATS, USAGE_TARGET_FORMAT)

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
MARKER = REPO / "logs" / ".connection_test_start"
OUT_DIR = REPO / "logs" / "battle_analysis"
_BATTLE_SCENES = {"command", "move_select", "watch", "field_check", "battle_hud", "field"}

# ------------------------------------------------------------------ 概念タグの定義 (技 id は Showdown id)
PSYCHIC_TERRAIN_SOURCES = {"psychicterrain", "expandingforce"}
TRICK_ROOM_MOVES = {"trickroom"}
WEATHER_MOVES = {"raindance", "sunnyday", "sandstorm", "snowscape", "hail", "chillyreception"}
WEATHER_ABILITIES = {"drizzle", "drought", "sandstream", "snowwarning"}
WEATHER_ABUSERS = {"swiftswim", "chlorophyll", "sandrush", "slushrush", "sandforce", "solarpower", "raindish", "icebody"}
HAZARD_MOVES = {"stealthrock", "spikes", "toxicspikes", "stickyweb", "ceaselessedge", "stoneaxe"}
SCREEN_MOVES = {"reflect", "lightscreen", "auroraveil"}
SETUP_MOVES = {"swordsdance", "nastyplot", "dragondance", "shellsmash", "calmmind", "bulkup", "quiverdance",
               "irondefense", "agility", "curse", "coil", "victorydance", "tidyup", "bellydrum"}
PIVOT_MOVES = {"uturn", "voltswitch", "partingshot", "flipturn", "batonpass", "teleport", "chillyreception"}
STATUS_MOVES = {"toxic", "willowisp", "thunderwave", "spore", "sleeppowder", "stunspore", "hypnosis", "yawn", "glare"}
SPEED_CONTROL_MOVES = {"tailwind", "icywind", "electroweb", "thunderwave", "stickyweb", "bulldoze", "trickroom"}
TERRAIN_ABILITIES = {"psychicsurge": "psychic", "electricsurge": "electric", "grassysurge": "grassy", "mistysurge": "misty"}

TAGS = {
    "psychic_terrain_support": {"label": "サイコフィールド + 先制技に弱い速い個体",
                                "why": "サイコフィールドで先制技が無効になる間、速くて脆い個体が先制技で落とされずに動ける"},
    "trick_room": {"label": "トリックルーム", "why": "遅い高火力を先手にする。速さで勝つ構築ほど噛み合わない"},
    "weather_offense": {"label": "天候 + 天候特性", "why": "天候の起点と、天候で速くなる/火力が上がる個体の組み合わせ"},
    "hazard_stack": {"label": "設置技の重ね", "why": "交代を繰り返す構築ほど削られる"},
    "setup_sweep": {"label": "積み技の押しつけ", "why": "1 回積まれてから止められるかが問われる"},
    "priority_core": {"label": "先制技コア", "why": "削れた個体を上から落とされる"},
    "pivot_cycle": {"label": "対面操作 (とんぼ/ボルチェン)", "why": "後投げの読み合いを一方的に握られる"},
    "status_pressure": {"label": "状態異常での削り", "why": "受け回しの起点にされる"},
    "screens": {"label": "壁", "why": "積みや削りの起点になる"},
}


# ------------------------------------------------------------------ 型の読み込み (純粋)
_STAT_KEYS = {"HP": "hp", "Atk": "atk", "Def": "def", "SpA": "spa", "SpD": "spd", "Spe": "spe"}


def _toid(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def parse_showdown_sets(text: str) -> dict:
    """Showdown 本文 → {species_id: set_row}。EVs 行は能力ポイント表記 (2 HP / 32 Atk …) を dict で保持する"""
    out = {}
    for block in (text or "").strip().split("\n\n"):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        head = lines[0]
        name, _, item = head.partition("@")
        row = {"item": _toid(item) or None, "ability": None, "nature": None, "evs": {}, "moves": []}
        for ln in lines[1:]:
            if ln.startswith("Ability:"):
                row["ability"] = _toid(ln.split(":", 1)[1])
            elif ln.startswith("EVs:"):
                for part in ln.split(":", 1)[1].split("/"):
                    m = re.match(r"\s*(\d+)\s+(\w+)", part)
                    if m and m.group(2) in _STAT_KEYS:
                        row["evs"][_STAT_KEYS[m.group(2)]] = int(m.group(1))
            elif ln.endswith("Nature"):
                row["nature"] = ln.split()[0].lower()
            elif ln.startswith("- "):
                row["moves"].append(_toid(ln[2:]))
        out[_toid(name)] = row
    return out


def set_row_from_candidate(c) -> dict:
    return {"item": c.item, "ability": c.ability, "nature": c.nature, "evs": c.evs, "moves": list(c.moves)}


# ------------------------------------------------------------------ 対戦ログ (副作用: 読み取りのみ)
def parse_battle(path: str) -> dict:
    """1 対戦のログ → 相手ロースター・選出・勝敗・決定ごとの助言 (相手の場の個体つき)・相手側の観測イベント"""
    outcome, inferred = None, False
    opp_roster, opp_fielded, my_picked = [], set(), set()
    opp_active_ja, my_active_ja = None, None
    decisions, events = [], []
    n_battle_scenes, t0 = 0, None
    opp_mega = False
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t0 = t0 or d.get("t")
        typ = d.get("type")
        if typ == "outcome":
            outcome, inferred = d.get("outcome"), bool(d.get("inferred"))
        elif typ == "scene":
            st = d.get("state") or {}
            in_battle = d.get("scene") in _BATTLE_SCENES
            if in_battle:
                n_battle_scenes += 1
            opp = st.get("opponent") or {}
            party = opp.get("party") or []
            for i, p in enumerate(party):
                if not p.get("ja"):
                    continue
                if p["ja"] not in opp_roster:
                    opp_roster.append(p["ja"])
                if in_battle and (p.get("hp") is not None or i == opp.get("active")):
                    opp_fielded.add(p["ja"])
                if p.get("is_mega") or p.get("mega"):
                    opp_mega = True
            ai = opp.get("active")
            if isinstance(ai, int) and 0 <= ai < len(party) and party[ai].get("ja"):
                opp_active_ja = party[ai]["ja"]
            me = st.get("player") or {}
            mparty = me.get("party") or []
            for p in mparty:
                if p.get("picked") and p.get("ja"):
                    my_picked.add(p["ja"])
            mi = me.get("active")
            if isinstance(mi, int) and 0 <= mi < len(mparty) and mparty[mi].get("ja"):
                my_active_ja = mparty[mi]["ja"]
        elif typ == "advice" and d.get("kind") == "battle":
            adv = d.get("advice") or {}
            acts = adv.get("actions") or []
            if not adv.get("ok") or not acts:
                continue
            best = acts[0]
            decisions.append({"t": d.get("t"), "opp": opp_active_ja, "me": my_active_ja,
                              "best_kind": best.get("kind"), "best_score": float(best.get("score") or 0.0),
                              "second_score": float(acts[1].get("score") or 0.0) if len(acts) > 1 else None})
        elif typ == "events":
            for f in d.get("fired") or []:
                events.append({"id": f, "t": d.get("t"), "turn": d.get("turn")})
    return {"file": Path(path).name, "t0": t0 or 0.0, "outcome": outcome, "inferred": inferred,
            "opp_roster": opp_roster, "opp_fielded": sorted(opp_fielded), "my_picked": sorted(my_picked),
            "decisions": decisions, "events": events, "n_battle_scenes": n_battle_scenes, "opp_mega": opp_mega}


def session_start_ts() -> Optional[float]:
    try:
        return float(MARKER.read_text().strip())
    except (OSError, ValueError):
        return None


def load_battles(since_ts: Optional[float] = None, last: Optional[int] = None, days: Optional[float] = None) -> list:
    files = sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))
    if since_ts:
        files = [f for f in files if Path(f).stat().st_mtime >= since_ts]
    if days:
        cutoff = time.time() - days * 86400
        files = [f for f in files if Path(f).stat().st_mtime >= cutoff]
    battles = [parse_battle(f) for f in files]
    battles = [b for b in battles if b["n_battle_scenes"] >= 3]
    return battles[-last:] if last else battles


# ------------------------------------------------------------------ 圧力 (動きづらさ、純粋)
def pressure_metrics(decisions: list, low_score: float = PARTY_IMPROVE_LOW_SCORE,
                     min_n: int = PARTY_IMPROVE_MIN_DECISIONS) -> dict:
    """決定の列 → 全体と相手個体ごとの {n, mean_best, switch_share, low_share, pressure_share}"""
    def summarize(rows: list) -> dict:
        n = len(rows)
        if n == 0:
            return {"n": 0, "mean_best": None, "switch_share": None, "low_share": None, "pressure_share": None}
        sw = sum(1 for r in rows if r.get("best_kind") == "switch")
        low = sum(1 for r in rows if (r.get("best_score") or 0.0) < low_score)
        pressed = sum(1 for r in rows if r.get("best_kind") == "switch" or (r.get("best_score") or 0.0) < low_score)
        return {"n": n, "mean_best": round(sum(r.get("best_score") or 0.0 for r in rows) / n, 1),
                "switch_share": round(sw / n, 3), "low_share": round(low / n, 3), "pressure_share": round(pressed / n, 3)}
    by_opp = defaultdict(list)
    for r in decisions:
        if r.get("opp"):
            by_opp[r["opp"]].append(r)
    per_opp = {k: summarize(v) for k, v in by_opp.items() if len(v) >= min_n}
    return {"overall": summarize(decisions), "by_opponent": per_opp}


def difficulty_weight(outcome: Optional[str], pressure_share: Optional[float],
                      loss_weight: float = PARTY_IMPROVE_LOSS_WEIGHT) -> float:
    return 1.0 + (loss_weight if outcome == "loss" else 0.0) + float(pressure_share or 0.0)


def rank_hard_parties(battles: list, top: int = PARTY_IMPROVE_TOP_PARTIES) -> list:
    rows = []
    for b in battles:
        if not b.get("opp_roster"):
            continue
        pm = pressure_metrics(b.get("decisions") or [])
        w = difficulty_weight(b.get("outcome"), pm["overall"].get("pressure_share"))
        worst = None
        for opp, m in pm["by_opponent"].items():
            if worst is None or (m["pressure_share"] or 0) > (worst[1]["pressure_share"] or 0):
                worst = (opp, m)
        rows.append({"file": b["file"], "outcome": b.get("outcome"), "inferred": b.get("inferred"),
                     "opp_roster": b["opp_roster"], "opp_fielded": b.get("opp_fielded") or [],
                     "my_picked": b.get("my_picked") or [], "pressure": pm["overall"], "by_opponent": pm["by_opponent"],
                     "worst_opponent": worst[0] if worst else None, "weight": round(w, 3)})
    rows.sort(key=lambda r: -r["weight"])
    return rows[:top]


# ------------------------------------------------------------------ 概念タグ (純粋、図鑑は引数で渡す)
def set_flags(species_id: str, set_row: dict, base: Optional[dict]) -> set:
    moves = set(set_row.get("moves") or [])
    ability = set_row.get("ability") or ""
    flags = set()
    if moves & PSYCHIC_TERRAIN_SOURCES or TERRAIN_ABILITIES.get(ability) == "psychic":
        flags.add("psychic_terrain")
    if moves & TRICK_ROOM_MOVES:
        flags.add("trick_room")
    if moves & WEATHER_MOVES or ability in WEATHER_ABILITIES:
        flags.add("weather_setter")
    if ability in WEATHER_ABUSERS:
        flags.add("weather_abuser")
    if moves & HAZARD_MOVES:
        flags.add("hazard")
    if moves & SCREEN_MOVES:
        flags.add("screens")
    if moves & SETUP_MOVES:
        flags.add("setup")
    if moves & PIVOT_MOVES:
        flags.add("pivot")
    if moves & STATUS_MOVES:
        flags.add("status")
    if moves & SPEED_CONTROL_MOVES:
        flags.add("speed_control")
    if base:
        spe, dfn, spd = base.get("spe", 0), base.get("def", 0), base.get("spd", 0)
        if spe >= PARTY_IMPROVE_FRAIL_FAST_SPE and min(dfn, spd) <= PARTY_IMPROVE_FRAIL_FAST_DEF:
            flags.add("frail_fast")
        if spe <= PARTY_IMPROVE_SLOW_SPE:
            flags.add("slow")
    return flags


def priority_attacks(moves: list, move_info) -> list:
    """威力のある先制技 (move_info(id) → {"priority", "power"} or None)"""
    out = []
    for m in moves or []:
        mv = move_info(m) if move_info else None
        if mv and (mv.get("priority") or 0) > 0 and (mv.get("power") or mv.get("basePower") or 0) > 0:
            out.append(m)
    return out


def team_concepts(members: dict, observed_events: Optional[list] = None, move_info=None, name=None) -> list:
    """members: {species_id: {"set": set_row, "base": baseStats or None}} → [{"tag", "label", "members", "evidence", "why"}]
    observed_events: 対戦ログの発火 id (move_opponent_x / ability_opponent_x / psychic_terrain / trickroom_start …)
    name: 表示名 (species_id → str)。無ければ id のまま"""
    nm = name or (lambda s: s)
    flags = {sid: set_flags(sid, m["set"], m.get("base")) for sid, m in members.items()}
    prio_users = [sid for sid, m in members.items() if priority_attacks(m["set"].get("moves"), move_info)]
    obs = set(observed_events or [])
    obs_moves = {e[len("move_opponent_"):] for e in obs if e.startswith("move_opponent_")}
    out = []

    def names(ids) -> str:
        return ", ".join(nm(s) for s in ids) if ids else "-"

    def add(tag, mem, evidence):
        out.append({"tag": tag, "label": TAGS[tag]["label"], "members": sorted(set(mem)),
                    "evidence": evidence, "why": TAGS[tag]["why"]})

    terrain_setters = [s for s, f in flags.items() if "psychic_terrain" in f]
    terrain_seen = "psychic_terrain" in obs or bool(obs_moves & PSYCHIC_TERRAIN_SOURCES)
    frail = [s for s, f in flags.items() if "frail_fast" in f]
    if (terrain_setters or terrain_seen) and frail:
        add("psychic_terrain_support", terrain_setters + frail,
            f"設置: {names(terrain_setters)}{' (観測あり)' if terrain_seen else ''} / 速くて脆い個体: {names(frail)}")
    tr = [s for s, f in flags.items() if "trick_room" in f]
    slow = [s for s, f in flags.items() if "slow" in f]
    if (tr or "trickroom_start" in obs or "trickroom" in obs_moves) and slow:
        add("trick_room", tr + slow, f"設置: {names(tr) if tr else '(観測)'} / 遅い個体: {names(slow)}")
    setters = [s for s, f in flags.items() if "weather_setter" in f]
    abusers = [s for s, f in flags.items() if "weather_abuser" in f]
    if setters and abusers:
        add("weather_offense", setters + abusers, f"起点: {names(setters)} / 恩恵: {names(abusers)}")
    hz = [s for s, f in flags.items() if "hazard" in f]
    if len(hz) >= 2 or len(obs_moves & HAZARD_MOVES) >= 2:
        add("hazard_stack", hz, f"設置役: {names(hz)} / 観測: {', '.join(sorted(obs_moves & HAZARD_MOVES)) or '-'}")
    su = [s for s, f in flags.items() if "setup" in f]
    if len(su) >= 2 or (obs_moves & SETUP_MOVES):
        add("setup_sweep", su, f"積み役: {names(su)} / 観測: {', '.join(sorted(obs_moves & SETUP_MOVES)) or '-'}")
    if len(prio_users) >= 2:
        add("priority_core", prio_users, f"先制技持ち: {names(prio_users)}")
    pv = [s for s, f in flags.items() if "pivot" in f]
    if len(pv) >= 2 or (len(obs_moves & PIVOT_MOVES) >= 1 and len(pv) >= 1):
        add("pivot_cycle", pv, f"対面操作: {names(pv)} / 観測: {', '.join(sorted(obs_moves & PIVOT_MOVES)) or '-'}")
    stt = [s for s, f in flags.items() if "status" in f]
    if len(stt) >= 2:
        add("status_pressure", stt, f"状態異常技持ち: {names(stt)}")
    sc = [s for s, f in flags.items() if "screens" in f]
    if sc:
        add("screens", sc, f"壁役: {names(sc)}")
    return out


# ------------------------------------------------------------------ 相性行列ベースの計算 (図鑑・型が要る)
def _views(sets: dict):
    from tools.team_build.interaction import view_from_set
    out = {}
    for sid, row in sets.items():
        try:
            out[sid] = view_from_set(sid, row)
        except Exception:
            continue
    return out


def coverage_table(my_views: dict, opp_views: dict) -> dict:
    """{my_id: {opp_id: coverage 0..1}}"""
    from tools.team_build.interaction import coverage_value, matrix
    mat = matrix(my_views, opp_views)
    return {mid: {oid: (coverage_value(row) if "error" not in row else 0.0) for oid, row in rows.items()}
            for mid, rows in mat.items()}


def team_cov(members: list, table: dict, threats: list, weights: Optional[dict] = None) -> float:
    from tools.team_build.candidates import SpeciesFeature, team_coverage
    feats = {sid: SpeciesFeature(sid, dict(table.get(sid, {}))) for sid in table}
    members = [m for m in members if m in feats]
    if not members:
        return 0.0
    return team_coverage(tuple(members), feats, threats, weights)


def priority_dependence(my_views: dict, opp_views: dict, weights: Optional[dict] = None) -> dict:
    """サイコフィールド下 (先制技が使えない) を、先制技を技リストから外して再計算し、被覆の落ち込みを出す"""
    from advisor.dex import get_dex
    dex = get_dex()
    prio_of = {sid: priority_attacks(moves, dex.move) for sid, (view, moves) in my_views.items()}
    without = {sid: (view, [m for m in moves if m not in prio_of[sid]]) for sid, (view, moves) in my_views.items()}
    full, blocked = coverage_table(my_views, opp_views), coverage_table(without, opp_views)
    threats = list(opp_views)
    members = list(my_views)
    per_member = {}
    for sid in members:
        f = sum(full[sid].values()) / max(1, len(threats))
        b = sum(blocked[sid].values()) / max(1, len(threats))
        per_member[sid] = {"priority_moves": prio_of[sid], "coverage": round(f, 3), "coverage_no_priority": round(b, 3),
                           "drop": round(f - b, 3)}
    return {"team_coverage": round(team_cov(members, full, threats, weights), 3),
            "team_coverage_no_priority": round(team_cov(members, blocked, threats, weights), 3),
            "per_member": per_member}


def counter_proposals(current: list, owned_views: dict, opp_views: dict, weights: Optional[dict] = None,
                      fixed: Optional[set] = None, top: int = PARTY_IMPROVE_TOP_PROPOSALS,
                      threat_order: Optional[list] = None, top_threats: int = PARTY_IMPROVE_TOP_THREATS) -> dict:
    """所持プール × 動きづらかった相手の相性行列から、(1) 相手ごとの対策候補 (現行外で被覆が高い所持種)、
    (2) 1 枠入替の案 (チーム被覆の差分が大きい順) を出す。未測定の draft。
    threat_order: 対策候補を出す相手の順 (難易度順)。被覆の計算は全相手で行う"""
    table = coverage_table(owned_views, opp_views)
    threats = list(opp_views)
    base = team_cov(current, table, threats, weights)
    per_threat = {}
    listed = [t for t in (threat_order or threats) if t in opp_views][:top_threats]
    for oid in listed:
        ranked = sorted(((table[s].get(oid, 0.0), s) for s in table), reverse=True)
        per_threat[oid] = {"best_current": max(((table[s].get(oid, 0.0), s) for s in current if s in table),
                                               default=(0.0, None)),
                           "best_owned_outside": [(round(v, 3), s) for v, s in ranked if s not in current][:3]}
    def own_cov(sid: str) -> float:
        row = table.get(sid, {})
        return sum(row.get(t, 0.0) for t in threats) / max(1, len(threats))

    swaps = []
    for out_m in current:
        if fixed and out_m in fixed:
            continue
        for in_m in table:
            if in_m in current:
                continue
            new = [in_m if m == out_m else m for m in current]
            delta = team_cov(new, table, threats, weights) - base
            swaps.append({"out": out_m, "in": in_m, "delta": round(delta, 4), "coverage": round(base + delta, 3),
                          "out_coverage": round(own_cov(out_m), 3)})
    # 同じ入替先が並ぶと情報が無いので入替先ごとに最良 (差分が同じなら、被覆の低い個体を外す案) を残す
    swaps.sort(key=lambda s: (-s["delta"], s["out_coverage"]))
    best_by_in, uniq = {}, []
    for s in swaps:
        if s["in"] in best_by_in:
            continue
        best_by_in[s["in"]] = s
        uniq.append(s)
    return {"base_coverage": round(base, 3), "per_threat": per_threat, "swaps": uniq[:top]}


# ------------------------------------------------------------------ 組み立て (副作用: 図鑑・DB・登録型の読み取り)
def _resolver():
    from vision.normalize import NameResolver
    return NameResolver()


def _ja(resolver, sid: str) -> str:
    try:
        from advisor.infer import species_ja_name
        return species_ja_name(sid) or sid
    except Exception:
        return sid


def _ids_from_ja(resolver, names: list) -> list:
    out = []
    for ja in names:
        r = resolver.resolve_species(ja, cutoff=0.9)
        if r and r[1] not in out:
            out.append(r[1])
    return out


def load_meta_sets(species_ids: list) -> dict:
    """使用率メタの代表型 {species_id: set_row} (無い種は落とす)"""
    from champions_agent.data import database as db
    from tools.team_build.sets import representative_set
    out = {}
    with db.get_connection() as conn:
        sid_snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
        for sid in species_ids:
            rep = representative_set(conn, sid_snap, sid)
            if rep is not None:
                out[sid] = set_row_from_candidate(rep)
    return out


def current_team_sets() -> tuple:
    """(現行 6 体の id 列, {id: set_row}) を登録型 (build_myteam_text) から"""
    from tools.evaluate_team import build_myteam_text
    sets = parse_showdown_sets(build_myteam_text())
    return list(sets), sets


def build_report(battles: list, hypothetical: Optional[list] = None) -> dict:
    from advisor.dex import get_dex
    from tools.team_build.spec import owned_species_ids
    dex = get_dex()
    resolver = _resolver()
    current, cur_sets = current_team_sets()
    owned = [s for s in owned_species_ids() if s not in current]
    owned_sets = load_meta_sets(owned)
    owned_sets.update(cur_sets)          # 現行は登録の型、それ以外は代表型
    owned_views = _views(owned_sets)
    my_views = {s: owned_views[s] for s in current if s in owned_views}

    parties = []
    if hypothetical:
        parties.append({"file": "(仮想)", "outcome": None, "opp_ids": hypothetical, "opp_roster": [], "weight": 1.0,
                        "events": [], "pressure": {}, "by_opponent": {}, "my_picked": [], "opp_fielded": []})
    else:
        by_file = {b["file"]: b for b in battles}
        for row in rank_hard_parties(battles):
            b = by_file[row["file"]]
            parties.append({**row, "opp_ids": _ids_from_ja(resolver, row["opp_roster"]),
                            "events": [e["id"] for e in b.get("events") or []]})

    all_opp_ids = sorted({oid for p in parties for oid in p["opp_ids"]})
    opp_sets = load_meta_sets(all_opp_ids)
    opp_views = _views(opp_sets)
    weights = defaultdict(float)
    for p in parties:
        for oid in p["opp_ids"]:
            weights[oid] += p["weight"]

    name = lambda s: _ja(resolver, s)   # noqa: E731
    # 対策候補を出す相手の順: パーティの難易度の重み + その個体に対する圧力 (相手個体ごとの pressure_share)
    threat_score = defaultdict(float)
    for p in parties:
        members = {oid: {"set": opp_sets[oid], "base": (dex.species(oid) or {}).get("baseStats")}
                   for oid in p["opp_ids"] if oid in opp_sets}
        p["concepts"] = team_concepts(members, p.get("events"), dex.move, name=name)
        p["missing_sets"] = [oid for oid in p["opp_ids"] if oid not in opp_sets]
        p["opp_ja"] = [name(oid) for oid in p["opp_ids"]]
        press = {}
        for ja_name, m in (p.get("by_opponent") or {}).items():
            ids = _ids_from_ja(resolver, [ja_name])
            if ids:
                press[ids[0]] = m.get("pressure_share") or 0.0
        for oid in p["opp_ids"]:
            threat_score[oid] += p["weight"] * (1.0 + press.get(oid, 0.0))
    threat_order = sorted(threat_score, key=lambda o: -threat_score[o])

    tags = {c["tag"] for p in parties for c in p["concepts"]}
    structural = {}
    if "psychic_terrain_support" in tags or hypothetical:
        structural["priority_dependence"] = priority_dependence(my_views, {o: opp_views[o] for o in opp_views}, dict(weights))
    proposals = counter_proposals(current, owned_views, opp_views, dict(weights), threat_order=threat_order) if opp_views else {}
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "current": current,
            "current_ja": [_ja(resolver, s) for s in current], "n_battles": len(battles),
            "parties": parties, "structural": structural, "proposals": proposals,
            "ja": {sid: _ja(resolver, sid) for sid in set(list(owned_views) + all_opp_ids)}}


def render(rep: dict) -> str:
    ja = rep["ja"]
    L = [f"## 自パーティ改善案 ({rep['generated_at']})", "",
         f"現行: {' / '.join(rep['current_ja'])} (対戦 {rep['n_battles']} 件)", ""]
    L.append("### 動きづらかった相手パーティ")
    if not rep["parties"]:
        L.append("- 該当なし (対戦ログに相手ロースターが無い)")
    for p in rep["parties"]:
        pr = p.get("pressure") or {}
        head = f"- **{' / '.join(p['opp_ja'] or p['opp_roster'])}** " + \
            (f"({'負け' if p['outcome'] == 'loss' else '勝ち' if p['outcome'] == 'win' else '勝敗不明'}" +
             (f"、圧力を受けた決定 {int(round((pr.get('pressure_share') or 0) * 100))}% / {pr.get('n')} 決定"
              if pr.get("n") else "") + ")" if p["file"] != "(仮想)" else "(仮想パーティ)")
        L.append(head)
        if p.get("worst_opponent"):
            m = p["by_opponent"].get(p["worst_opponent"], {})
            L.append(f"  - 最も動きづらかった相手個体: {p['worst_opponent']} (最善手の平均スコア {m.get('mean_best')}、"
                     f"交代が最善 {int(round((m.get('switch_share') or 0) * 100))}%)")
        if p.get("my_picked"):
            L.append(f"  - 自分の選出: {' / '.join(p['my_picked'])}")
        for c in p.get("concepts") or []:
            L.append(f"  - 概念: **{c['label']}** [{', '.join(ja.get(s, s) for s in c['members'])}] — {c['evidence']}。{c['why']}")
        if p.get("missing_sets"):
            L.append(f"  - 代表型なし (概念判定から除外): {', '.join(ja.get(s, s) for s in p['missing_sets'])}")
    st = rep.get("structural") or {}
    if st.get("priority_dependence"):
        pd = st["priority_dependence"]
        L += ["", "### 構造的な弱点: 先制技依存 (サイコフィールド下の被覆)",
              f"- チーム被覆 {pd['team_coverage']} → 先制技なし {pd['team_coverage_no_priority']} "
              f"(差 {round(pd['team_coverage'] - pd['team_coverage_no_priority'], 3)})"]
        for sid, m in sorted(pd["per_member"].items(), key=lambda kv: -kv[1]["drop"]):
            if m["priority_moves"]:
                L.append(f"  - {ja.get(sid, sid)}: {m['coverage']} → {m['coverage_no_priority']} (先制技 {', '.join(m['priority_moves'])})")
    pr = rep.get("proposals") or {}
    if pr:
        L += ["", "### 改善案 (相性行列による見積もり。未測定の draft)",
              f"- 現行のチーム被覆 (動きづらかった相手に対して): {pr['base_coverage']}"]
        for oid, t in pr["per_threat"].items():
            bc = t["best_current"]
            outs = ", ".join(f"{ja.get(s, s)} {v}" for v, s in t["best_owned_outside"])
            L.append(f"- {ja.get(oid, oid)}: 現行の最善 {ja.get(bc[1], bc[1]) if bc[1] else '-'} {round(bc[0], 3)} / "
                     f"現行外の所持種で高い順: {outs or '-'}")
        if pr["swaps"]:
            L.append("- 1 枠入替の案:")
            for s in pr["swaps"]:
                L.append(f"  - {ja.get(s['out'], s['out'])} → {ja.get(s['in'], s['in'])}: 被覆 {pr['base_coverage']} → {s['coverage']} ({s['delta']:+.3f})")
        L.append("- 採否は測定で決める: `bash scripts/team_build.sh <run_id> --stages all --profile medium` "
                 "(現行 + 近傍が候補に入る) か、案を --candidates で指定して S8a/S8b を回す")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="store_true", help="接続テスト開始マーカー以降の対戦")
    ap.add_argument("--last", type=int, default=None)
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--opponents", default=None, help="仮想の相手パーティ (species id のカンマ区切り)。ログは使わない")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)
    battles, hyp = [], None
    if args.opponents:
        hyp = [_toid(x) for x in args.opponents.split(",") if x.strip()]
    else:
        since = session_start_ts() if args.session else None
        battles = load_battles(since_ts=since, last=args.last, days=args.days)
    rep = build_report(battles, hypothetical=hyp)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        return 0
    md = render(rep)
    print(md)
    if not args.no_save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / f"improvements_{time.strftime('%Y%m%d_%H%M')}.md"
        p.write_text(md, encoding="utf-8")
        print(f"保存: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
