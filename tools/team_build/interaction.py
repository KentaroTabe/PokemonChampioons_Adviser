"""Interaction Matrix: 自分の型 × 脅威の型の対面特徴 (連続値・資源コスト)。

docs/TEAM_BUILDING_PLAN.md v3 §6.2 / IMPLEMENTATION §2.1。covered / not covered の二値ではなく
「勝つためにどれだけ資源を要求するか」まで機械的に出す。計算は advisor/damage (ダメージ計算・実効素早さ) と
advisor/endgame (撃破ターン数・積み後の評価) を使い、LLM は使わない。

行の意味 (すべて 0..1 で 1 が有利、None は該当なし):
  lead        対面から (両者 100%) の有利度
  switch_in   相手の最大打点を受けてから (後投げ) の有利度。受け切れなければ 0
  revenge     削れた相手 (50%) を上から / 先制技で落とせる度合い
  setup_stop  1 回積んだ相手 (100%) を止められる度合い (相手に積み技が無ければ None)
  speed_control  "faster" / "slower" / "tie" (実効素早さ、スカーフ等込み)
  hazard        {"sets": bool, "removes": bool}   status_pressure  変化技の割合
  uses_mega     この型がメガ石を持つ (メガ枠を要求する)
  resource_cost {"hp": 対面で勝つまでに失う HP 割合, "item": タスキ/木の実を消費しそうか}
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional

from advisor.damage import MonView, effective_speed
from advisor.endgame import _best_dmg, _race_turns, _setup_boosted

HAZARD_SET = ("stealthrock", "spikes", "toxicspikes", "stickyweb")
HAZARD_REMOVE = ("defog", "rapidspin", "tidyup", "mortalspin")
STATUS_MOVES = ("willowisp", "toxic", "thunderwave", "spore", "sleeppowder", "yawn", "glare",
                "stunspore", "hypnosis", "darkvoid", "nuzzle")
CONSUMABLE_ITEMS = ("focussash", "sitrusberry", "lumberry", "chestoberry", "weaknesspolicy",
                    "airballoon", "redcard", "ejectbutton", "whiteherb", "mentalherb", "custapberry")
REVENGE_HP = 0.5          # 「削れた相手」の残 HP
SWITCH_IN_MIN_HP = 0.05   # 後投げ後にこれ未満しか残らなければ受け不可


def _score01(score: Optional[float]) -> Optional[float]:
    """duel_score 相当の [-1, 1] を [0, 1] に"""
    if score is None:
        return None
    return round((max(-1.0, min(1.0, score)) + 1.0) / 2.0, 3)


def _duel(a: MonView, a_hp: float, a_moves: list, b: MonView, b_hp: float, b_moves: list) -> Optional[float]:
    """endgame.duel_score と同じ定義 (撃破ターン差 + 先手権) を HP 指定つきで"""
    turns_a, eff_a = _race_turns(a, a_hp, a_moves, b, b_hp, b_moves)
    turns_b, eff_b = _race_turns(b, b_hp, b_moves, a, a_hp, a_moves)
    if turns_a is None and turns_b is None:
        return None
    if turns_a is None:
        return -1.0
    if turns_b is None:
        return 1.0
    a_first = effective_speed(eff_a) >= effective_speed(eff_b)
    margin = (turns_b - turns_a) + (0.5 if a_first else -0.5)
    return math.tanh(margin * 0.8)


def _priority_moves(moves: list) -> list:
    from advisor.dex import get_dex
    dex = get_dex()
    out = []
    for m in moves:
        mv = dex.move(m)
        # advisor の図鑑は威力を "power" で持つ (Showdown の basePower ではない)
        if mv and (mv.get("priority") or 0) > 0 and (mv.get("power") or mv.get("basePower") or 0) > 0:
            out.append(m)
    return out


def lead_score(me: MonView, my_moves: list, opp: MonView, opp_moves: list) -> Optional[float]:
    return _score01(_duel(me, 1.0, my_moves, opp, 1.0, opp_moves))


def switch_in_score(me: MonView, my_moves: list, opp: MonView, opp_moves: list) -> float:
    """相手の最大打点を 1 発受けてからの対面。受け切れなければ 0"""
    taken = _best_dmg(opp, me, opp_moves) / 100.0
    hp_after = 1.0 - taken
    if hp_after < SWITCH_IN_MIN_HP:
        return 0.0
    s = _duel(me, hp_after, my_moves, opp, 1.0, opp_moves)
    return _score01(s) if s is not None else 0.0


def revenge_score(me: MonView, my_moves: list, opp: MonView, opp_moves: list,
                  opp_hp: float = REVENGE_HP) -> float:
    """削れた相手を上から / 先制技で落とせるか"""
    dmg = _best_dmg(me, opp, my_moves) / 100.0
    if dmg <= 0:
        return 0.0
    faster = effective_speed(me) > effective_speed(opp)
    prio = _priority_moves(my_moves)
    prio_dmg = max((_best_dmg(me, opp, [p]) for p in prio), default=0.0) / 100.0
    if prio_dmg >= opp_hp:
        return 1.0
    if faster and dmg >= opp_hp:
        return 1.0
    # 上を取れないが 1 発耐えて返せる
    taken = _best_dmg(opp, me, opp_moves) / 100.0
    if dmg >= opp_hp and taken < 1.0:
        return 0.6
    if faster and dmg >= opp_hp / 2:
        return 0.4
    return 0.0


def setup_stop_score(me: MonView, my_moves: list, opp: MonView, opp_moves: list) -> Optional[float]:
    """1 回積んだ相手 (100%) を止められるか。相手に積み技が無ければ None"""
    boosted = _setup_boosted(opp, opp_moves)
    if boosted is None:
        return None
    s = _duel(me, 1.0, my_moves, boosted, 1.0, opp_moves)
    return _score01(s) if s is not None else 0.0


def speed_relation(me: MonView, opp: MonView) -> str:
    a, b = effective_speed(me), effective_speed(opp)
    return "faster" if a > b else ("slower" if a < b else "tie")


def resource_cost(me: MonView, my_moves: list, opp: MonView, opp_moves: list) -> dict:
    """対面で勝つまでに失う HP 割合と、消耗品を消費しそうか"""
    turns_a, _ = _race_turns(me, 1.0, my_moves, opp, 1.0, opp_moves)
    taken_per_turn = _best_dmg(opp, me, opp_moves) / 100.0
    if turns_a is None:
        hp_cost = 1.0
    else:
        faster = effective_speed(me) >= effective_speed(opp)
        hits = max(0, turns_a - (1 if faster else 0))
        hp_cost = min(1.0, hits * taken_per_turn)
    item = (me.item or "").lower()
    item_cost = bool(item in CONSUMABLE_ITEMS and taken_per_turn > 0 and hp_cost > 0)
    return {"hp": round(hp_cost, 3), "item": item_cost}


def interaction_row(my_id: str, me: MonView, my_moves: list, opp_id: str, opp: MonView,
                    opp_moves: list, mega_stones: Optional[set] = None) -> dict:
    stones = mega_stones or set()
    return {
        "my_id": my_id, "opponent": opp_id,
        "lead": lead_score(me, my_moves, opp, opp_moves),
        "switch_in": switch_in_score(me, my_moves, opp, opp_moves),
        "revenge": revenge_score(me, my_moves, opp, opp_moves),
        "setup_stop": setup_stop_score(me, my_moves, opp, opp_moves),
        "speed_control": speed_relation(me, opp),
        "hazard": {"sets": any(m in HAZARD_SET for m in my_moves),
                   "removes": any(m in HAZARD_REMOVE for m in my_moves)},
        "status_pressure": round(sum(1 for m in my_moves if m in STATUS_MOVES) / max(1, len(my_moves)), 3),
        "uses_mega": bool((me.item or "") in stones),
        "resource_cost": resource_cost(me, my_moves, opp, opp_moves),
    }


COVERAGE_WEIGHTS = {"lead": 0.5, "switch_in": 0.3, "revenge": 0.2}


def coverage_value(row: dict) -> float:
    """1 行を「この脅威をどれだけ扱えるか」の 1 値に潰す (候補探索のスコア用)。
    max だと 6 体いれば誰かが 1.0 になり飽和するので、対面 / 後投げ / 切り返しの加重平均にする"""
    tot, wsum = 0.0, 0.0
    for k, w in COVERAGE_WEIGHTS.items():
        v = row.get(k)
        if v is None:
            continue
        tot += w * float(v)
        wsum += w
    return round(tot / wsum, 4) if wsum else 0.0


def view_from_set(species_id: str, set_row: dict, level: int = 50) -> tuple:
    """型 (ability/item/nature/evs(能力ポイント "2/32/0/0/0/32" か dict)/moves) → (MonView, moves)。
    メガ石を持つ型はメガ後の種族値・タイプで評価する (メガシンカ後の対面が実態に近い)"""
    from advisor.dex import get_dex
    from advisor.ev_infer import _nature_mult
    dex = get_dex()
    sid = species_id
    item = (set_row.get("item") or set_row.get("item_name") or "") or None
    stones = _mega_stone_ids()
    if item in stones:
        # X/Y のメガ石 (…itex / …itey / …nitex / …nitey) は対応するフォルムへ (ライチュウナイトY → raichumegay。
        # 以前は先に見つかった megax に倒れていた)
        suffix = item[-1] if item[-1] in ("x", "y") else ""
        cands = [sid + "mega" + suffix] if suffix else [sid + "mega", sid + "megax", sid + "megay"]
        for cand in cands:
            if dex.species(cand):
                sid = cand
                break
    sp = dex.species(sid) or dex.species(species_id)
    if sp is None:
        raise KeyError(species_id)
    ev = _points_to_ev(set_row.get("evs") or set_row.get("能力ポイント"))
    nature = _nature_mult(set_row.get("nature")) if set_row.get("nature") else {}
    view = MonView(species_id=sid, types=list(sp["types"]), base=dict(sp["baseStats"]),
                   level=level, ev=ev, nature=nature or {}, item=item,
                   ability=set_row.get("ability") or set_row.get("ability_name"))
    moves = [m for m in (set_row.get("moves") or
                         [set_row.get(k) for k in ("move1", "move2", "move3", "move4")]) if m]
    return view, moves


_STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")


def _points_to_ev(evs) -> dict:
    """"2/32/0/0/0/32" または {"hp":2,...} (0-32 の能力ポイント) → 努力値 (8 倍、上限 252)"""
    if not evs:
        return {}
    if isinstance(evs, str):
        parts = evs.split("/")
        if len(parts) != 6:
            return {}
        pts = dict(zip(_STAT_ORDER, (int(p) for p in parts)))
    else:
        alias = {"h": "hp", "a": "atk", "b": "def", "c": "spa", "d": "spd", "s": "spe"}
        pts = {alias.get(k, k): int(v) for k, v in evs.items()}
    return {k: min(252, v * 8) if v <= 32 else min(252, v) for k, v in pts.items() if v}


def _mega_stone_ids() -> set:
    try:
        from tools.check_mega_items import mega_stones
        return {sid for (_a, _b, _c, sid) in mega_stones() if sid}
    except Exception:
        return set()


def matrix(my_sets: dict, threat_sets: dict) -> dict:
    """my_sets / threat_sets: {id: (MonView, moves)} → {my_id: {opp_id: row}}"""
    stones = _mega_stone_ids()
    out = {}
    for my_id, (me, my_moves) in my_sets.items():
        out[my_id] = {}
        for opp_id, (opp, opp_moves) in threat_sets.items():
            try:
                out[my_id][opp_id] = interaction_row(my_id, me, my_moves, opp_id, opp, opp_moves, stones)
            except Exception as e:      # 図鑑欠落等は行ごとに落とす
                out[my_id][opp_id] = {"my_id": my_id, "opponent": opp_id, "error": repr(e)}
    return out
