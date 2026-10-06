"""学習環境で実戦の相手バンク (logs/real_opponents/bank.json、tools/real_opponents で作る) を使う。

- RealBankTeambuilder: 実戦で当たった構成 (代表型 + 判明した型、合法なもの) を遭遇数で重みづけして出す
- apply_real_pick_teampreview: 相手プレイヤーの選出を、その構成で観測した選出・先発の分布からサンプルする
  (観測が足りない構成は従来の選出のまま)。自己対戦の「机上の選出」を実戦の傾向で補正する
"""
from __future__ import annotations

import json
import random
import types
from pathlib import Path
from typing import Optional

from champions_agent.config import (REAL_BANK_MIN_PICK_OBS, REAL_BANK_MIN_TEAMS, REAL_BANK_PATH,
                                    REAL_BANK_PICK_FLOOR, REAL_BANK_SPECIES_MIN_APPEAR, TRAIN_REAL_PICK_PROB)

REPO = Path(__file__).resolve().parent.parent.parent

try:
    from poke_env.teambuilder import Teambuilder as _PokeEnvTeambuilder
except Exception:  # pragma: no cover
    _PokeEnvTeambuilder = object


def _toid(name) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def load_bank(path: Optional[Path] = None) -> Optional[dict]:
    p = Path(path) if path else REPO / REAL_BANK_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def usable_teams(bank: Optional[dict]) -> list:
    return [t for t in (bank or {}).get("teams", []) if t.get("legal") and t.get("text")]


def sample_picks(roster: list, picks: dict, leads: dict, n: int, rng: random.Random, k: int = 3,
                 floor: float = REAL_BANK_PICK_FLOOR) -> list:
    """観測した選出回数 (picks) と先発回数 (leads) から k 体を順序つきでサンプルする (純粋。先頭が先発)。
    先発は観測した先発の分布から、残りは選出率 (未観測にも floor) に比例して非復元でサンプルする"""
    n = max(1, int(n))
    prob = {sid: max(floor, (picks or {}).get(sid, 0) / n) for sid in roster}
    chosen = []
    if leads:
        ids, ws = zip(*[(sid, w) for sid, w in leads.items() if sid in prob])
        if ids:
            chosen.append(rng.choices(list(ids), weights=list(ws), k=1)[0])
    while len(chosen) < min(k, len(roster)):
        rest = [(sid, prob[sid]) for sid in roster if sid not in chosen]
        ids, ws = zip(*rest)
        chosen.append(rng.choices(list(ids), weights=list(ws), k=1)[0])
    return chosen


class RealBankTeambuilder(_PokeEnvTeambuilder):
    """実戦バンクの構成を遭遇数で重みづけして出す Teambuilder。last_team に出した構成を保持する"""

    def __init__(self, bank: Optional[dict] = None, rng: Optional[random.Random] = None,
                 min_teams: int = REAL_BANK_MIN_TEAMS):
        self.bank = bank if bank is not None else load_bank()
        self.teams = usable_teams(self.bank)
        self.rng = rng or random.Random()
        self.enabled = len(self.teams) >= min_teams
        self.last_team = None

    def yield_team(self) -> str:
        t = self.rng.choices(self.teams, weights=[max(1, int(x.get("n") or 1)) for x in self.teams], k=1)[0]
        self.last_team = t
        return self.join_team(self.parse_showdown_team(t["text"]))


def find_team(bank: Optional[dict], roster_ids: list) -> Optional[dict]:
    key = set(_toid(s) for s in roster_ids)
    for t in (bank or {}).get("teams", []):
        if set(t.get("roster") or []) == key:
            return t
    return None


def species_pick_weights(bank: Optional[dict], roster: list, min_appear: int = REAL_BANK_SPECIES_MIN_APPEAR) -> Optional[tuple]:
    """種ごとの実戦選出率・先発率 → (picks 相当の重み, leads 相当の重み, n=1)。
    出現の足りる種が 3 体未満なら None。足りない種は既知の平均で埋める (純粋)"""
    stats = (bank or {}).get("species") or {}
    rates, lead_rates = {}, {}
    for sid in roster:
        s = stats.get(sid)
        if s and (s.get("appear") or 0) >= min_appear:
            rates[sid] = s["picked"] / s["appear"]
            lead_rates[sid] = s["lead"] / s["appear"]
    if len(rates) < 3:
        return None
    mean = sum(rates.values()) / len(rates)
    picks = {sid: rates.get(sid, mean) for sid in roster}
    leads = {sid: v for sid, v in lead_rates.items() if v > 0}
    return picks, leads


def apply_real_pick_teampreview(player, bank: Optional[dict], rng: Optional[random.Random] = None,
                                min_obs: int = REAL_BANK_MIN_PICK_OBS, prob: float = TRAIN_REAL_PICK_PROB) -> bool:
    """相手プレイヤーの teampreview を差し替える (確率 prob で実戦の傾向、残りは元の選出):
    1. 自分の 6 体がバンクの構成に一致し観測数が min_obs 以上 → その構成で観測した選出・先発の分布
    2. それ以外 → 種ごとの実戦選出率・先発率 (出現の足りる種が 3 体以上のとき)
    3. どちらも無ければ元の選出方法 (相性ヒューリスティクス等)。戻り値: 差し替えたか (バンクが無ければ False)"""
    if not bank or not (bank.get("teams") or bank.get("species")):
        return False
    rng = rng or random.Random()
    original = player.teampreview

    def _order_to_cmd(battle, roster, order):
        index_of = {}
        for i, p in enumerate(battle.team.values()):
            index_of.setdefault(_toid(p.species), i + 1)
        idx = [index_of[s] for s in order if s in index_of]
        if len(idx) != len(order):
            return None
        rest = [i for i in range(1, len(roster) + 1) if i not in idx]
        return "/team " + "".join(str(i) for i in idx + rest)

    def _teampreview(self, battle):
        try:
            if rng.random() >= prob:
                return original(battle)
            roster = [_toid(p.species) for p in battle.team.values()]
            t = find_team(bank, roster)
            if t and int(t.get("n") or 0) >= min_obs and t.get("picks"):
                order = sample_picks(t["roster"], t["picks"], t.get("leads") or {}, t["n"], rng)
                cmd = _order_to_cmd(battle, roster, order)
                if cmd:
                    return cmd
            sp = species_pick_weights(bank, roster)
            if sp:
                picks, leads = sp
                order = sample_picks(roster, picks, leads, 1, rng)
                cmd = _order_to_cmd(battle, roster, order)
                if cmd:
                    return cmd
        except Exception:
            pass
        return original(battle)

    player.teampreview = types.MethodType(_teampreview, player)
    return True
