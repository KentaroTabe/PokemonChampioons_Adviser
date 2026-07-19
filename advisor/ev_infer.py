"""相手の型 (性格/努力値/持ち物) の推定。

行動の先後 (素早さの不等式) と与ダメージ/被ダメージの観測から、
使用率DBの型候補 (spread_usage × item_usage) を尤度スコアリングする。
自由なEV回帰は行わない: 実際の相手はDB上位の型にほぼ収まるため、
候補集合に対するベイズ更新の方がはるかに頑健。

観測の収集は SpreadTracker.on_frame (サーバーの毎フレーム処理から呼ぶ)。
推定結果は best() でアドバイザーが参照する。
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

from advisor.damage import MonView, calc_damage
from advisor.dex import get_dex

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")

_STATS = ("hp", "atk", "def", "spa", "spd", "spe")

_NATURE_MODS = {
    "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
    "lonely": ("atk", "def"), "brave": ("atk", "spe"),
    "bold": ("def", "atk"), "impish": ("def", "spa"),
    "lax": ("def", "spd"), "relaxed": ("def", "spe"),
    "modest": ("spa", "atk"), "mild": ("spa", "def"),
    "rash": ("spa", "spd"), "quiet": ("spa", "spe"),
    "calm": ("spd", "atk"), "gentle": ("spd", "def"),
    "careful": ("spd", "spa"), "sassy": ("spd", "spe"),
    "timid": ("spe", "atk"), "hasty": ("spe", "def"),
    "naive": ("spe", "spd"), "jolly": ("spe", "spa"),
}

_NATURE_JA = {
    "adamant": "いじっぱり", "jolly": "ようき", "modest": "ひかえめ",
    "timid": "おくびょう", "bold": "ずぶとい", "impish": "わんぱく",
    "calm": "おだやか", "careful": "しんちょう", "relaxed": "のんき",
    "sassy": "なまいき", "brave": "ゆうかん", "quiet": "れいせい",
    "naughty": "やんちゃ", "lonely": "さみしがり", "lax": "のうてんき",
    "rash": "うっかりや", "mild": "おっとり", "gentle": "おとなしい",
    "hasty": "せっかち", "naive": "むじゃき",
}


def _dashify(species_id: str) -> str:
    """showdown id -> DBのダッシュ形式候補 (greattusk -> great-tusk 等は
    既存テーブルの名寄せに依存するため、両方式を試す)"""
    return species_id


def _nature_mult(nature: str) -> dict:
    pair = _NATURE_MODS.get(nature)
    if not pair:
        return {}
    return {pair[0]: 1.1, pair[1]: 0.9}


class SpreadEstimator:
    """1体の相手ポケモンに対する型仮説の管理"""

    def __init__(self, species_id: str):
        self.species_id = species_id
        self.hyps: list = self._build_hypotheses(species_id)
        self.n_obs = 0

    # ------------------------------------------------------------------
    @staticmethod
    def _query_spreads(species_id: str) -> list:
        try:
            db = sqlite3.connect(str(DB_PATH))
            names = {species_id}
            # DBはダッシュ入り名 (great-tusk) のことがある
            rows = []
            for name_expr in (
                "pokemon_name = ?",
                "REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ?",
            ):
                rows = list(db.execute(
                    f"SELECT nature, evs, SUM(usage_percent) w FROM spread_usage "
                    f"WHERE {name_expr} GROUP BY nature, evs "
                    f"ORDER BY w DESC LIMIT 8", (species_id,)))
                if rows:
                    break
            items = []
            for name_expr in (
                "pokemon_name = ?",
                "REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ?",
            ):
                items = list(db.execute(
                    f"SELECT item_name, SUM(usage_percent) w FROM item_usage "
                    f"WHERE {name_expr} GROUP BY item_name "
                    f"ORDER BY w DESC LIMIT 4", (species_id,)))
                if items:
                    break
            db.close()
            return [rows, items]
        except Exception:
            return [[], []]

    def _build_hypotheses(self, species_id: str) -> list:
        spreads, items = self._query_spreads(species_id)
        if not spreads:
            return []
        if not items:
            items = [(None, 1.0)]
        total_s = sum(w for _, _, w in spreads) or 1.0
        total_i = sum(w for _, w in items) or 1.0
        hyps = []
        for nature, evs_str, ws in spreads:
            try:
                vals = [int(x) for x in evs_str.split("/")]
                evs = dict(zip(_STATS, vals))
            except Exception:
                continue
            for item, wi in items:
                item_id = (item or "").replace("-", "").replace(" ", "").lower() or None
                hyps.append({
                    "nature": nature,
                    "evs": evs,
                    "item": item_id,
                    "logw": math.log((ws / total_s) * (wi / total_i) + 1e-9),
                })
        return hyps

    # ------------------------------------------------------------------
    def _view(self, hyp: dict, opp_state: dict) -> Optional[MonView]:
        dex = get_dex()
        sp = dex.species(self.species_id)
        if sp is None:
            return None
        return MonView(
            species_id=self.species_id,
            types=sp["types"],
            base=sp["baseStats"],
            hp_frac=1.0,
            boosts=opp_state.get("boosts") or {},
            ability=opp_state.get("ability_id"),
            item=hyp["item"],
            ev=hyp["evs"],
            nature=_nature_mult(hyp["nature"]),
        )

    # ------------------------------------------------------------------
    def observe_speed(self, opp_first: bool, my_effective_speed: float,
                      opp_state: dict, rain: bool = False) -> None:
        """同一優先度の技での先後観測。opp_first=Trueなら相手の実速が上"""
        if not self.hyps:
            return
        self.n_obs += 1
        for h in self.hyps:
            v = self._view(h, opp_state)
            if v is None:
                continue
            spe = v.stat("spe")
            if h["item"] == "choicescarf":
                spe = int(spe * 1.5)
            if opp_state.get("status") == "paralysis":
                spe = int(spe * 0.5)
            if rain and (opp_state.get("ability_id") == "swiftswim"):
                spe = int(spe * 2)
            consistent = (spe > my_effective_speed) if opp_first \
                else (spe < my_effective_speed)
            # 先後は追い風/こだわり等の未観測要因もあるためソフトに更新
            h["logw"] += math.log(1.0 if consistent else 0.2)

    def observe_damage(self, attacker: MonView, defender_is_hyp: bool,
                       move_id: str, observed_pct: float,
                       opp_state: dict, fieldv=None) -> None:
        """ダメージ観測。defender_is_hyp=True: 自分の技→相手 (耐久推定)、
        False: 相手の技→自分 (火力推定、attackerは仮説側で上書き)"""
        if not self.hyps or observed_pct <= 1.0:
            return
        self.n_obs += 1
        for h in self.hyps:
            hyp_view = self._view(h, opp_state)
            if hyp_view is None:
                continue
            try:
                if defender_is_hyp:
                    d = calc_damage(attacker, hyp_view, move_id, fieldv)
                else:
                    d = calc_damage(hyp_view, attacker, move_id, fieldv)
            except Exception:
                continue
            lo, hi = d["min"] - 3.0, d["max"] + 3.0
            if lo <= observed_pct <= hi:
                like = 1.0
            else:
                dist = (lo - observed_pct) if observed_pct < lo \
                    else (observed_pct - hi)
                like = math.exp(-(dist / 6.0) ** 2) + 1e-6
            h["logw"] += math.log(like)

    # ------------------------------------------------------------------
    def best(self) -> Optional[dict]:
        """最有力仮説と確信度。観測がなければ事前分布の最上位"""
        if not self.hyps:
            return None
        mx = max(h["logw"] for h in self.hyps)
        total = sum(math.exp(h["logw"] - mx) for h in self.hyps)
        ranked = sorted(self.hyps, key=lambda h: -h["logw"])
        top = ranked[0]
        prob = math.exp(top["logw"] - mx) / total
        abbr = {"hp": "H", "atk": "A", "def": "B",
                "spa": "C", "spd": "D", "spe": "S"}
        ev_txt = "".join(f"{abbr[k]}{round(v * 32 / 252)}"
                         for k, v in top["evs"].items() if v)
        return {
            "nature": top["nature"],
            "nature_ja": _NATURE_JA.get(top["nature"], top["nature"]),
            "evs": top["evs"],
            "item": top["item"],
            "prob": round(prob, 3),
            "n_obs": self.n_obs,
            "summary": f"{_NATURE_JA.get(top['nature'], top['nature'])}"
                       f" {ev_txt}"
                       + (f" @{top['item']}" if top["item"] else "")
                       + f" ({prob:.0%}, 観測{self.n_obs}件)",
        }


class SpreadTracker:
    """フレームストリームから観測を抽出し、種族ごとの推定器を更新する。

    battle_loggerと同様にサーバーの毎フレーム処理から on_frame(state, fired)
    で呼ばれる。技イベントとHP変化イベント (state.events) を時系列で対応付ける。
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._est: dict = {}          # species_id -> SpreadEstimator
        self._last_move = {"player": None, "opponent": None}  # (ts, move_id, turn)
        self._hp_seen_ts = 0.0
        self._order_turn = None       # 先後を判定済みのターン
        self._prev_scene = None

    def estimator(self, species_id: str) -> SpreadEstimator:
        if species_id not in self._est:
            self._est[species_id] = SpreadEstimator(species_id)
        return self._est[species_id]

    def best_for(self, species_id: Optional[str]) -> Optional[dict]:
        est = self._est.get(species_id)
        return est.best() if est else None

    # ------------------------------------------------------------------
    def on_frame(self, state: dict, fired: list) -> None:
        try:
            self._on_frame(state, fired)
        except Exception:
            pass   # 推定は補助機能。失敗しても本体を止めない

    def _active(self, state: dict, side: str) -> Optional[dict]:
        s = state[side]
        idx = s.get("active_index")
        if idx is None or idx >= len(s.get("party", [])):
            return None
        return s["party"][idx]

    def _on_frame(self, state: dict, fired: list) -> None:
        # 新しい対戦でリセット
        if state.get("scene") == "selection" and self._prev_scene not in (
                "selection", None):
            self.reset()
        self._prev_scene = state.get("scene")

        now = time.time()
        turn = state.get("turn")
        for f in fired:
            for side in ("player", "opponent"):
                if f.startswith(f"move_{side}_"):
                    self._last_move[side] = (now, f.split("_", 2)[2], turn)

        # --- 先後観測: 同一ターンに両者の技が揃ったら1回だけ判定 ---
        pm, om = self._last_move["player"], self._last_move["opponent"]
        if (pm and om and pm[2] == om[2] and pm[2] is not None
                and pm[2] != self._order_turn):
            self._observe_order(state, pm, om)
            self._order_turn = pm[2]

        # --- ダメージ観測: HP変化イベントを直前の技と対応付ける ---
        for e in state.get("events", []):
            if e.get("source") != "hp" or e.get("ts", 0) <= self._hp_seen_ts:
                continue
            self._hp_seen_ts = e["ts"]
            det = e.get("detail") or {}
            delta = (det.get("from") or 0) - (det.get("to") or 0)
            if delta <= 1.0:
                continue   # 回復や増加は扱わない
            self._observe_hp_drop(state, det.get("side"), delta, e["ts"])

    # ------------------------------------------------------------------
    def _my_view(self, state: dict):
        from advisor.engine import build_mon_view
        me = self._active(state, "player")
        if not me:
            return None, None
        return build_mon_view(me, None, side="player"), me

    def _observe_order(self, state: dict, pm, om) -> None:
        dex = get_dex()
        mv_p, mv_o = dex.move(pm[1]), dex.move(om[1])
        if not mv_p or not mv_o:
            return
        if mv_p.get("priority", 0) != mv_o.get("priority", 0):
            return   # 優先度が違うと素早さの情報にならない
        if state.get("field", {}).get("trick_room"):
            return
        opp = self._active(state, "opponent")
        my_view, me = self._my_view(state)
        if not opp or not opp.get("species_id") or my_view is None:
            return
        my_spe = my_view.stat("spe")
        if me.get("item_id") == "choicescarf":
            my_spe = int(my_spe * 1.5)
        if me.get("status") == "paralysis":
            my_spe = int(my_spe * 0.5)
        rain = state.get("field", {}).get("weather") == "rain"
        opp_first = om[0] < pm[0]
        self.estimator(opp["species_id"]).observe_speed(
            opp_first, my_spe, opp, rain=rain)

    def _observe_hp_drop(self, state: dict, side: Optional[str],
                         delta: float, ts: float) -> None:
        if side == "opponent":
            attacker_move = self._last_move["player"]
            opp = self._active(state, "opponent")
            my_view, _me = self._my_view(state)
            if (not attacker_move or ts - attacker_move[0] > 12.0
                    or not opp or not opp.get("species_id")
                    or my_view is None):
                return
            self.estimator(opp["species_id"]).observe_damage(
                my_view, True, attacker_move[1], delta, opp)
        elif side == "player":
            attacker_move = self._last_move["opponent"]
            opp = self._active(state, "opponent")
            my_view, _me = self._my_view(state)
            if (not attacker_move or ts - attacker_move[0] > 12.0
                    or not opp or not opp.get("species_id")
                    or my_view is None):
                return
            self.estimator(opp["species_id"]).observe_damage(
                my_view, False, attacker_move[1], delta, opp)


_tracker: Optional[SpreadTracker] = None


def get_tracker() -> SpreadTracker:
    global _tracker
    if _tracker is None:
        _tracker = SpreadTracker()
    return _tracker
