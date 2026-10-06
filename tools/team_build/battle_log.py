"""対戦記録 (合成ログ): 勝敗だけでなく再現に必要な情報を turn 単位で残す。

docs/TEAM_BUILDING_IMPLEMENTATION.md §3.3 / TEAM_BUILDING_PLAN v3 §6.3。
1 対戦 = 1 行 (JSONL)。dataset_kind は常に "synthetic" (実戦ログは battle_logger が real を書く)。

BattleRecorder は助言操縦の Player から呼ぶ:
  rec.on_decision(battle, state_summary, advice, executed)   # 各ターン
  rec.on_end(battle, extra)                                   # 対戦終了時に 1 行書く
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_SCHEMA_VERSION

RECORD_FIELDS = (
    "schema_version", "dataset_kind", "battle_id", "candidate_team_id", "advisor_policy_id",
    "opponent_team_id", "opponent_family_id", "battle_seed", "policy_seed", "user_policy",
    "team_preview", "our_selection", "opponent_selection", "lead",
    "turns", "switch_events", "ko_events", "status_events", "resource_usage", "mega_usage",
    "won", "turn_count", "remaining_members", "termination_reason", "elapsed_s")


def _species_list(mons) -> list:
    out = []
    for m in (mons or {}).values() if isinstance(mons, dict) else (mons or []):
        sid = getattr(m, "species", None)
        if sid:
            out.append(sid)
    return out


def _events_of(battle) -> list:
    """全ターンの観測イベント (poke-env の battle.observations[turn].events) を平坦化"""
    obs = getattr(battle, "observations", None) or {}
    out = []
    for turn in sorted(obs.keys()):
        for ev in list(getattr(obs[turn], "events", None) or []):
            out.append((turn, list(ev)))
    return out


def _norm(ev: list) -> list:
    """poke-env の split_message は '|move|p1a: X|...' を split した ['', 'move', ...] 形式。
    先頭の空要素を落として ['move', ...] に揃える"""
    ev = list(ev or [])
    while ev and ev[0] == "":
        ev = ev[1:]
    return ev


def summarize_events(events: list) -> dict:
    """faint / switch / status / item / mega をイベント列から拾う (壊れても空で返す)"""
    ko, sw, st, items, mega = [], [], [], [], []
    last_attacker: Optional[str] = None
    for turn, ev in events:
        ev = _norm(ev)
        if not ev:
            continue
        tag = ev[0] if isinstance(ev[0], str) else ""
        try:
            if tag == "move" and len(ev) > 2:
                last_attacker = (ev[1], ev[2])           # (user, move)
            elif tag == "faint" and len(ev) > 1:
                ko.append({"turn": turn, "fainted": ev[1],
                           "by": last_attacker[0] if last_attacker else None,
                           "move": last_attacker[1] if last_attacker else None})
            elif tag in ("switch", "drag") and len(ev) > 1:
                sw.append({"turn": turn, "to": ev[1], "kind": tag})
            elif tag == "-status" and len(ev) > 2:
                st.append({"turn": turn, "target": ev[1], "status": ev[2]})
            elif tag in ("-enditem", "-item") and len(ev) > 2:
                items.append({"turn": turn, "target": ev[1], "item": ev[2], "kind": tag})
            elif tag == "-mega" and len(ev) > 1:
                mega.append({"turn": turn, "target": ev[1]})
            elif tag == "detailschange" and len(ev) > 2 and "mega" in str(ev[2]).lower():
                mega.append({"turn": turn, "target": ev[1]})
        except Exception:
            continue
    return {"ko_events": ko, "switch_events": sw, "status_events": st,
            "resource_usage": items, "mega_usage": mega}


class BattleRecorder:
    def __init__(self, path: Path, candidate_team_id: str, advisor_policy_id: str,
                 user_policy: str = "full", battle_seed: Optional[int] = None,
                 policy_seed: Optional[int] = None, family_of: Optional[dict] = None):
        self.path = Path(path)
        self.candidate_team_id = candidate_team_id
        self.advisor_policy_id = advisor_policy_id
        self.user_policy = user_policy
        self.battle_seed = battle_seed
        self.policy_seed = policy_seed
        self.family_of = family_of or {}
        self._turns: dict = {}
        self._t0: dict = {}
        self._picks: dict = {}

    def on_pick(self, battle, species_order: list) -> None:
        """teampreview で送った順 (先頭 3 体が選出、先頭が先発)"""
        tag = getattr(battle, "battle_tag", None) or id(battle)
        self._picks[tag] = list(species_order)

    def on_decision(self, battle, state_summary: Optional[dict], advice: Optional[dict],
                    executed: Optional[dict], followed: Optional[bool] = None) -> None:
        tag = getattr(battle, "battle_tag", None) or id(battle)
        self._t0.setdefault(tag, time.time())
        rec = {"turn": getattr(battle, "turn", None), "state_before": state_summary,
               "advisor_recommendation": _slim_advice(advice), "executed_action": executed,
               "followed": followed}
        self._turns.setdefault(tag, []).append(rec)

    def on_end(self, battle, opponent_team_id: Optional[str] = None,
               extra: Optional[dict] = None) -> dict:
        tag = getattr(battle, "battle_tag", None) or id(battle)
        events = _events_of(battle)
        summ = summarize_events(events)
        team = getattr(battle, "team", {}) or {}
        opp = getattr(battle, "opponent_team", {}) or {}
        preview_opp = _species_list(getattr(battle, "teampreview_opponent_team", None))
        picks = self._picks.pop(tag, None)
        role = getattr(battle, "player_role", None) or "p1"
        opp_role = "p2" if role == "p1" else "p1"
        record = {
            "schema_version": BUILD_SCHEMA_VERSION, "dataset_kind": "synthetic",
            "battle_id": str(tag), "candidate_team_id": self.candidate_team_id,
            "advisor_policy_id": self.advisor_policy_id,
            "opponent_team_id": opponent_team_id,
            "opponent_family_id": self.family_of.get(opponent_team_id),
            "battle_seed": self.battle_seed, "policy_seed": self.policy_seed,
            "user_policy": self.user_policy,
            "team_preview": {"ours": sorted(_species_list(team)), "theirs": sorted(preview_opp)},
            "our_selection": (picks[:3] if picks else _species_list(team)),
            "opponent_selection": _species_list(opp),
            "lead": {"ours": (picks[0] if picks else _first_switch(events, role)),
                     "theirs": _first_switch(events, opp_role)},
            "events_seen": len(events),
            "observation_turns": len(getattr(battle, "observations", None) or {}),
            "turns": self._turns.pop(tag, []),
            "won": bool(getattr(battle, "won", False)),
            "turn_count": getattr(battle, "turn", None),
            "remaining_members": {"ours": sum(1 for m in team.values() if not getattr(m, "fainted", False)),
                                  "theirs": sum(1 for m in opp.values() if not getattr(m, "fainted", False))},
            "termination_reason": "finished" if getattr(battle, "finished", False) else "unknown",
            "elapsed_s": round(time.time() - self._t0.pop(tag, time.time()), 1),
        }
        record.update(summ)
        if extra:
            record.update(extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


def _first_switch(events: list, role: str) -> Optional[str]:
    for _turn, ev in events:
        ev = _norm(ev)
        if ev and ev[0] in ("switch", "drag") and len(ev) > 1 and str(ev[1]).startswith(role):
            return ev[1]
    return None


def _slim_advice(advice: Optional[dict]) -> Optional[dict]:
    if not advice:
        return None
    acts = []
    for a in (advice.get("actions") or [])[:4]:
        acts.append({k: a.get(k) for k in ("kind", "id", "score") if k in a})
    best = advice.get("best") or {}
    return {"best": {k: best.get(k) for k in ("kind", "id", "score") if k in best},
            "actions": acts, "ok": advice.get("ok")}


def read_records(path: Path) -> list:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out
