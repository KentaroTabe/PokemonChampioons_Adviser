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


def _ev_to_points(evs: dict) -> dict:
    """努力値 (0-252) をチャンピオンズの能力ポイント (0-32, 合計66) に換算。

    Showdown系スプレッドの端数 (252/252/4の4など) は切り上げた上で、
    合計が66に2以内で届かない場合は端数枠 (4ポイント未満) に寄せる
    (252/252/4 -> 32/32/2 という自然な表記になる)。
    """
    pts = {k: min(32, (v + 7) // 8) for k, v in evs.items() if v}
    total = sum(pts.values())
    diff = 66 - total
    if 0 < diff <= 2:
        small = [k for k, v in pts.items() if v < 4]
        if len(small) == 1:
            pts[small[0]] += diff
    return pts


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
        self.spe_lower: Optional[float] = None   # 実効素早さの下限 (観測由来)
        self.spe_upper: Optional[float] = None
        self._choice_locked = False

    # ------------------------------------------------------------------
    @staticmethod
    def _query_spreads(species_id: str) -> list:
        try:
            db = sqlite3.connect(str(DB_PATH))
            # チャンピオンズ実データを優先し、無い場合のみ全ソース (Smogon
            # フォールバック含む) を使う。SVデータの型を混ぜない
            from advisor.team_advice import _champions_filter
            filters = [_champions_filter(db), "1=1"]

            def q(table, cols, flt, limit):
                for name_expr in (
                    "pokemon_name = ?",
                    "REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ?",
                ):
                    got = list(db.execute(
                        f"SELECT {cols}, SUM(usage_percent) w FROM {table} "
                        f"WHERE {name_expr} AND {flt} GROUP BY {cols} "
                        f"ORDER BY w DESC LIMIT {limit}", (species_id,)))
                    if got:
                        return got
                return []

            rows = q("spread_usage", "nature, evs", filters[0], 8)
            if not rows:
                rows = q("spread_usage", "nature, evs", filters[1], 8)
            elif all(r[0] is None for r in rows):
                # チャンピオンズ実データ (pokedb) は性格を持たないため、
                # 性格つきのフォールバック行を低重みで補完する
                extra = q("spread_usage", "nature, evs", filters[1], 6)
                seen = {(r[0], r[1]) for r in rows}
                total = sum(r[2] for r in rows) or 1.0
                for nat, evs, w in extra:
                    if nat is not None and (nat, evs) not in seen:
                        rows.append((nat, evs, w * 0.3 * total /
                                     (sum(x[2] for x in extra) or 1.0)))
            items = q("item_usage", "item_name", filters[0], 4) or \
                q("item_usage", "item_name", filters[1], 4)
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
        # pokedb由来データは性格行 (evs=None) とEV行 (nature=None) が独立
        # なので、両方があればクロス結合して仮説にする
        complete = [(n, e, w) for n, e, w in spreads if n and e]
        ev_only = [(e, w) for n, e, w in spreads if e and not n]
        nat_only = [(n, w) for n, e, w in spreads if n and not e]
        if ev_only and nat_only:
            tot_n = sum(w for _, w in nat_only) or 1.0
            for e, we in ev_only[:4]:
                for n, wn in nat_only[:4]:
                    complete.append((n, e, we * wn / tot_n))
        elif ev_only:
            complete += [(None, e, w) for e, w in ev_only]
        spreads = complete or spreads
        total_s = sum(w for _, _, w in spreads) or 1.0
        total_i = sum(w for _, w in items) or 1.0
        hyps = []
        for nature, evs_str, ws in spreads:
            try:
                vals = [int(x) for x in evs_str.split("/")]
                # DBには努力値表記 (0-252) とチャンピオンズの能力ポイント表記
                # (0-32) が混在する。最大32以下なら能力ポイントとみなし換算
                if vals and max(vals) <= 32:
                    vals = [min(252, v * 8) for v in vals]
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
        # 実効素早さの範囲を狭める (表示・素早さ実数の把握用)
        if opp_first:
            self.spe_lower = max(self.spe_lower or 0, my_effective_speed)
        else:
            self.spe_upper = min(self.spe_upper or 9999, my_effective_speed)
        from advisor.damage import FieldView, effective_speed
        fv = FieldView(weather="rain" if rain else None)
        for h in self.hyps:
            v = self._view(h, opp_state)
            if v is None:
                continue
            v.status = opp_state.get("status")
            spe = effective_speed(v, fv)
            consistent = (spe > my_effective_speed) if opp_first \
                else (spe < my_effective_speed)
            # 先後は追い風/こだわり等の未観測要因もあるためソフトに更新
            h["logw"] += math.log(1.0 if consistent else 0.2)

    def observe_item(self, item_id: Optional[str]) -> None:
        """持ち物の判明 (発動/はたき落とし/表示) で、別の持ち物の仮説を実質除外する"""
        if not item_id or not self.hyps:
            return
        iid = str(item_id).replace("-", "").replace(" ", "").lower()
        if not any((h["item"] or "") == iid for h in self.hyps):
            return   # 仮説に無い持ち物なら情報として使えない (全滅を避ける)
        self.n_obs += 1
        for h in self.hyps:
            if (h["item"] or "") != iid:
                h["logw"] += math.log(1e-6)

    def observe_choice_lock(self) -> None:
        """同一技の3連続使用を観測 -> こだわり系持ち物の仮説を強める"""
        if self._choice_locked or not self.hyps:
            return
        self._choice_locked = True
        self.n_obs += 1
        # 3連続同一技はこだわり系のほぼ確定的な証拠 (変化技サイクル等の
        # 例外はあるが稀) なので強く更新する
        for h in self.hyps:
            if h["item"] and h["item"].startswith("choice"):
                h["logw"] += math.log(4.0)
            else:
                h["logw"] += math.log(0.1)

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
        # 表示はチャンピオンズの能力ポイント (0-32, 合計66) 表記
        abbr = {"hp": "H", "atk": "A", "def": "B",
                "spa": "C", "spd": "D", "spe": "S"}
        pts = _ev_to_points(top["evs"])
        ev_txt = " ".join(f"{abbr[k]}{v}" for k, v in pts.items() if v)
        nature_ja = _NATURE_JA.get(top["nature"], top["nature"]) or "性格不明"
        spe_note = ""
        if self.spe_lower or self.spe_upper:
            lo = f"{self.spe_lower:.0f}<" if self.spe_lower else ""
            hi = f"<{self.spe_upper:.0f}" if self.spe_upper else ""
            spe_note = f" 実効S{lo}S{hi}" if (lo or hi) else ""
        return {
            "nature": top["nature"],
            "nature_ja": nature_ja,
            "evs": top["evs"],
            "item": top["item"],
            "prob": round(prob, 3),
            "n_obs": self.n_obs,
            "spe_lower": self.spe_lower,
            "spe_upper": self.spe_upper,
            "summary": f"{nature_ja}"
                       f" {ev_txt}"
                       + (f" @{top['item']}" if top["item"] else "")
                       + f" ({prob:.0%}, 観測{self.n_obs}件)"
                       + spe_note,
        }


    def top_k(self, k: int, min_weight: float = 0.0) -> list:
        """重み上位k仮説を [{"nature","evs","item","weight"}] で返す (P7)。

        weight は全仮説で正規化した事後確率 (上位kの和 = 被覆率)。
        min_weight 未満の仮説は、1つ以上返せていれば刈り込む。
        観測が無ければ使用率由来の事前分布そのもの。
        """
        if not self.hyps or k <= 0:
            return []
        mx = max(h["logw"] for h in self.hyps)
        ws = [math.exp(h["logw"] - mx) for h in self.hyps]
        total = sum(ws) or 1.0
        ranked = sorted(zip(self.hyps, ws), key=lambda pr: -pr[1])
        out = []
        for h, w in ranked[:k]:
            wn = w / total
            if wn < min_weight and out:
                break
            out.append({"nature": h["nature"], "evs": h["evs"],
                        "item": h["item"], "weight": round(wn, 4)})
        return out

    def speed_estimate(self, opp_state: Optional[dict] = None) -> Optional[dict]:
        """最良仮説の実効素早さを観測レンジでクランプした推定値。

        戻り値: {"est": int, "lo": float|None, "hi": float|None, "n_obs": int}
        (フロントエンド表示とRLの素早さ比較に使う)
        """
        if not self.hyps:
            return None
        top = max(self.hyps, key=lambda h: h["logw"])
        v = self._view(top, opp_state or {})
        if v is None:
            return None
        v.status = (opp_state or {}).get("status")
        from advisor.damage import effective_speed
        est = effective_speed(v)
        if self.spe_lower:
            est = max(est, int(self.spe_lower) + 1)
        if self.spe_upper:
            est = min(est, max(1, int(self.spe_upper) - 1))
        return {"est": int(est), "lo": self.spe_lower, "hi": self.spe_upper,
                "n_obs": self.n_obs}


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
        self._opp_streak = {}         # species_id -> (move_id, 連続回数)

    def estimator(self, species_id: str) -> SpreadEstimator:
        if species_id not in self._est:
            self._est[species_id] = SpreadEstimator(species_id)
        return self._est[species_id]

    def best_for(self, species_id: Optional[str]) -> Optional[dict]:
        est = self._est.get(species_id)
        return est.best() if est else None

    def hypotheses_for(self, species_id: Optional[str], k: int,
                       min_weight: float = 0.0) -> list:
        """多世界探索 (P7) 用: 種族の重み上位k仮説。推定器が無ければ
        事前分布 (使用率由来) の推定器を作って返す"""
        if not species_id:
            return []
        return self.estimator(species_id).top_k(k, min_weight)

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

        # 選出画面: 相手6体の仮説を事前構築しておく (対戦開始直後から
        # 事前分布ベースの型表示ができる)
        if state.get("scene") == "selection":
            for p in state["opponent"].get("party", []):
                if p.get("species_id"):
                    self.estimator(p["species_id"])

        now = time.time()
        turn = state.get("turn")
        for f in fired:
            for side in ("player", "opponent"):
                if f.startswith(f"move_{side}_"):
                    self._last_move[side] = (now, f.split("_", 2)[2], turn)

        # こだわりロック検知: 同一ポケモンが同一技を3連続 (交代で解除)
        for f in fired:
            if f.startswith("move_opponent_"):
                opp = self._active(state, "opponent")
                if opp and opp.get("species_id"):
                    sid = opp["species_id"]
                    mid = f.split("_", 2)[2]
                    prev_mid, streak = self._opp_streak.get(sid, (None, 0))
                    streak = streak + 1 if mid == prev_mid else 1
                    self._opp_streak[sid] = (mid, streak)
                    if streak >= 3:
                        self.estimator(sid).observe_choice_lock()
            elif f == "switch_opponent":
                self._opp_streak.clear()

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
        # 特性による優先度変化の可能性がある行動は先後観測に使わない
        # (実戦: いたずらごころヤミラミのおにび先制を「実速が上」と誤学習し
        #  推定素早さが大きく狂った)。可能特性ベースで保守的に弾く
        def _pri_ability_noise(p_dict, mv) -> bool:
            try:
                from advisor.rl_bridge import _possible_abilities_dict
                poss = _possible_abilities_dict(p_dict)
            except Exception:
                return False
            cat = (mv.get("category") or "").lower()
            mtype = (mv.get("type") or "").lower()
            if "prankster" in poss and cat == "status":
                return True   # いたずらごころ: 変化技+1
            if "galewings" in poss and mtype == "flying":
                return True   # はやてのつばさ: ひこう技+1 (満タン時)
            if "triage" in poss and (mv.get("heal") or mv.get("drain")):
                return True   # ヒーリングシフト
            return False
        if _pri_ability_noise(opp, mv_o) or _pri_ability_noise(me, mv_p):
            return
        from advisor.damage import FieldView, effective_speed
        f = state.get("field", {})
        my_spe = effective_speed(my_view, FieldView(
            weather=f.get("weather"), terrain=f.get("terrain")))
        rain = f.get("weather") == "rain"
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
