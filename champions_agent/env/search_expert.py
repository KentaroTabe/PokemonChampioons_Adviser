"""探索エンジン (advisor/search) を学習の教師・対戦相手として使うブリッジ。

アドバイザーのダメージ計算+同時手番探索を poke_env のバトル状態から
直接呼び出せるようにする。用途は2つ:
  1. 学習の対戦相手 (opponent_pool 経由): ヒューリスティクスとは読み筋の
     異なる相手として混入し、自己対戦の相手多様性を上げる
  2. 行動クローンの教師 (train/bc_pretrain.py): 探索の選択を模倣させる
     (下記の実測強度に注意)

探索は自分側=全情報 (実数値まで使用)、相手側=公開情報 (視認済みの技・HP) を
使う。未視認技は使用率DBの実技セット、DBに無い種族はタイプ代表技で補完する。

実測強度 (tools/check_search_expert, 各100戦 vs 上位構築×SimpleHeuristics):
  - 2026-07-24 素の2手読み (depth=2): 0.41
  - 2026-07-26 価値ブレンド (当時の_best参照): 0.64 — 一時BC教師に採用
  - 2026-07-27 素の2手読み: 0.44 / 価値ブレンド (現_best参照): 0.34
  [参考: RandomPlayer 0.1-0.2 / 学習済み方策 0.56-0.62]
⚠ 価値ブレンドの効果は参照する _best に強く依存し、現在は逆効果
  (0.44→0.34)。価値ヘッドはモデルごとに報酬スケールが異なり、SimSide由来の
  合成状態では較正が保証されないため。use_value を有効にする場合は
  check_search_expert で必ず前後比較すること。
  BC教師としては方策 (0.56-0.62) に劣るため、ネット拡幅の初期化には
  bc_pretrain --teacher policy (方策蒸留) を使う。
  対戦相手としての混入 (depth=1, 価値なし) は多様性目的で有効。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from advisor.damage import FieldView, MonView
from advisor.dex import BOOST_MULT
from advisor.search import Action, SimSide, search

# 相手のEV仮定はアドバイザー本体と同じ「攻撃系252振り」(advisor/engine.py)
OFFENSIVE_EV = {"atk": 252, "spa": 252, "spe": 252}

# 未視認の相手技の代用: タイプごとの代表的な中威力技 (探索で「無害な相手」に
# ならないための仮定。実在IDでないとダメ計できないため代表IDを使う)
_TYPE_REP_MOVES = {
    "Normal": "bodyslam", "Fire": "flamethrower", "Water": "surf",
    "Electric": "thunderbolt", "Grass": "energyball", "Ice": "icebeam",
    "Fighting": "closecombat", "Poison": "sludgebomb", "Ground": "earthquake",
    "Flying": "airslash", "Psychic": "psychic", "Bug": "bugbuzz",
    "Rock": "stoneedge", "Ghost": "shadowball", "Dragon": "dragonpulse",
    "Dark": "crunch", "Steel": "ironhead", "Fairy": "moonblast",
}

_STATUS_MAP = {"BRN": "burn", "PAR": "paralysis", "PSN": "poison",
               "TOX": "toxic", "SLP": "sleep", "FRZ": "freeze"}

_WEATHER_MAP = {"SUNNYDAY": "sun", "DESOLATELAND": "sun",
                "RAINDANCE": "rain", "PRIMORDIALSEA": "rain",
                "SANDSTORM": "sandstorm",
                "SNOW": "snow", "SNOWSCAPE": "snow", "HAIL": "snow"}

_TERRAIN_MAP = {"ELECTRIC_TERRAIN": "electric", "GRASSY_TERRAIN": "grassy",
                "PSYCHIC_TERRAIN": "psychic", "MISTY_TERRAIN": "misty"}


def _names(enum_iter) -> set:
    """enumのdict/setから .name 集合を得る (モック可能なようにduck-typing)"""
    out = set()
    for k in (enum_iter or {}):
        out.add(getattr(k, "name", str(k)).upper())
    return out


def _types_of(p) -> list:
    return [getattr(t, "name", str(t)).capitalize()
            for t in (p.types or []) if t is not None]


@dataclass
class _ActualStatView(MonView):
    """実数値が判明している側 (自分チーム) 用のMonView。

    MonViewは種族値+EV仮定から実数値を再計算するが、poke_envは自分側の
    実数値をサーバーから受け取っているため、それをそのまま使う
    (EV0仮定のままだと火力/耐久/素早さを大幅に過小評価し、探索が
    誤った択を選ぶ — 導入時の実測でベンチ勝率0.36まで落ちた原因)。
    """
    actual: dict = field(default_factory=dict)   # {"hp","atk",...: 実数値}

    def stat(self, key: str, ignore_boost: bool = False) -> int:
        val = self.actual.get(key)
        if not val:
            return super().stat(key, ignore_boost)
        if key != "hp" and not ignore_boost:
            val = int(val * BOOST_MULT.get(self.boosts.get(key, 0), 1.0))
        return int(val)

    def max_hp(self) -> int:
        return int(self.actual.get("hp") or super().max_hp())


def _mon_view(p, own: bool = False) -> Optional[MonView]:
    """poke_env Pokemon -> ダメ計用 MonView。

    own=True: 実数値 (p.stats/p.max_hp) をそのまま使う。
    own=False: アドバイザーと同じ攻撃系252振り仮定で計算する。
    """
    if p is None:
        return None
    status = p.status
    status_s = _STATUS_MAP.get(getattr(status, "name", str(status)).upper()) \
        if status is not None else None
    kwargs = dict(
        species_id=p.species or "",
        level=p.level or 50,
        types=_types_of(p),
        base=dict(p.base_stats or {}),
        hp_frac=float(p.current_hp_fraction or 0.0),
        status=status_s,
        boosts=dict(p.boosts or {}),
        ability=p.ability or None,
        item=p.item or None,
    )
    if own:
        actual = {k: v for k, v in (getattr(p, "stats", None) or {}).items()
                  if v}
        if getattr(p, "max_hp", None):
            actual["hp"] = p.max_hp
        return _ActualStatView(actual=actual, **kwargs)
    return MonView(ev=dict(OFFENSIVE_EV), **kwargs)


def _field_view(battle, side_conditions) -> FieldView:
    weather = None
    for w in _names(battle.weather):
        weather = _WEATHER_MAP.get(w, weather)
    terrain, trick_room = None, False
    for f in _names(battle.fields):
        terrain = _TERRAIN_MAP.get(f, terrain)
        if f == "TRICK_ROOM":
            trick_room = True
    conds = _names(side_conditions)
    return FieldView(weather=weather, terrain=terrain, trick_room=trick_room,
                     reflect="REFLECT" in conds,
                     light_screen="LIGHT_SCREEN" in conds,
                     aurora_veil="AURORA_VEIL" in conds)


def _bench(team_values, active, own: bool = False) -> tuple:
    """(SimSide.bench, benchの各要素に対応するPokemonリスト)"""
    views, mons = [], []
    for p in team_values:
        if p is active or p.fainted:
            continue
        v = _mon_view(p, own=own)
        if v is not None:
            views.append((v, float(p.current_hp_fraction or 0.0)))
            mons.append(p)
    return views[:4], mons[:4]


_meta_moves_cache: Optional[dict] = None


def _squash(name: str) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def _meta_move_ids(species: str) -> list:
    """使用率DB (meta_sets) の実技セット。無ければ空リスト"""
    global _meta_moves_cache
    if _meta_moves_cache is None:
        _meta_moves_cache = {}
        try:
            from champions_agent.env.ranked_teams import _load_meta_sets
            for name, row in _load_meta_sets().items():
                ids = [_squash(row[k]) for k in
                       ("move1", "move2", "move3", "move4") if row.get(k)]
                _meta_moves_cache[_squash(name)] = ids
        except Exception:
            pass
    key = _squash(species)
    for k in (key, key.removesuffix("megay").removesuffix("megax")
              .removesuffix("mega")):
        if k in _meta_moves_cache:
            return _meta_moves_cache[k]
    return []


def _opp_move_pool(opp_active) -> list:
    """相手の想定技プール [(id, w)]。

    視認済みの技 (w=1.0) に、使用率DBの実技セット (w=0.6) を補完する。
    平均8ターン程度の短期決戦では序盤=未視認の意思決定が大半を占めるため、
    タイプ代表技よりも実セットでのモデル化が効く。DBに無い種族のみ
    タイプ代表技で代用する。
    """
    revealed = [m.id for m in (opp_active.moves or {}).values()]
    pool = [(m, 1.0) for m in revealed[:6]]
    meta = [m for m in _meta_move_ids(opp_active.species or "")
            if m not in revealed]
    pool += [(m, 0.6) for m in meta]
    if pool:
        return pool[:8]
    return [(_TYPE_REP_MOVES[t], 1.0) for t in _types_of(opp_active)
            if t in _TYPE_REP_MOVES] or [("bodyslam", 1.0)]


def search_options(battle, depth: int = 1,
                   use_value: bool = False) -> Optional[dict]:
    """現局面の択評価 (行動ごとの期待値/保証値) を返す。

    decide の探索部分だけを切り出したもの。読み負荷の測定
    (期待値と保証値の差 = 読み依存度) など、行動選択以外の用途で使う。
    戻り値: search() の結果 {"actions": [...], "matrix": [...]} or None
    """
    active = battle.active_pokemon
    opp_active = battle.opponent_active_pokemon
    if active is None or opp_active is None:
        return None
    my_view, opp_view = _mon_view(active, own=True), _mon_view(opp_active)
    if my_view is None or opp_view is None:
        return None

    my_bench, _ = _bench(battle.team.values(), active, own=True)
    opp_bench, _ = _bench(battle.opponent_team.values(), opp_active)
    me = SimSide(active=my_view, active_hp=my_view.hp_frac, bench=my_bench,
                 stealth_rock="STEALTH_ROCK" in _names(battle.side_conditions))
    opp = SimSide(active=opp_view, active_hp=opp_view.hp_frac, bench=opp_bench,
                  stealth_rock="STEALTH_ROCK" in
                  _names(battle.opponent_side_conditions))
    my_moves = [m.id for m in (battle.available_moves or [])][:4]
    return search(me, opp, my_moves, _opp_move_pool(opp_active),
                  my_field=_field_view(battle, battle.side_conditions),
                  opp_field=_field_view(battle,
                                        battle.opponent_side_conditions),
                  depth=depth)


_est_cache: dict = {}


def _belief_views(opp_view, species: str, k: int) -> list:
    """相手型の事前分布 (使用率由来) の上位k仮説で [(重み, MonView)] を作る。

    シムでは対戦中の観測更新は行わない (事前分布のみ)。k=1 は最尤仮説
    (MAP) 1つ、k=0 は従来の攻撃系252振り単一仮定 (呼び出し側で分岐)。
    持ち物は未判明のときだけ仮説の値を使う。
    """
    if k <= 0 or opp_view is None or not species:
        return []
    from advisor.ev_infer import SpreadEstimator, _nature_mult
    est = _est_cache.get(species)
    if est is None:
        est = SpreadEstimator(species)
        _est_cache[species] = est
    hyps = est.top_k(k)
    import copy as _copy
    worlds = []
    for h in hyps:
        v = _copy.copy(opp_view)
        v.ev = dict(h["evs"])
        v.nature = _nature_mult(h["nature"])
        if not opp_view.item and h["item"]:
            v.item = h["item"]
        worlds.append((h["weight"], v))
    return worlds


def decide(battle, depth: int = 1, by: str = "recommended",
           use_value: bool = False, belief_k: int = 0,
           opp_prior_mix: float = 0.0) -> Optional[dict]:
    """探索で最善行動を選ぶ。

    返り値: {"kind": "move"|"switch", "move": Move|None, "mega": bool,
             "pokemon": Pokemon|None, "action_index": int}  (選べなければ None)
    action_index は学習環境のアクション番号 (0-5=交代 / 6-9=技 / 10-13=技+メガ)
    by: 行動の選択基準。"recommended" (期待値+保証値のブレンド) か
        "expected" (純期待値。読みを外しても咎めない相手には強気が正着)
    use_value: depth>=2 のとき、RL価値関数を葉評価にブレンドする。
        効果が参照する _best に依存し不安定なため既定はOFF
        (2026-07-26は0.41→0.64と改善、2026-07-27は0.44→0.34と劣化)。
        有効化する場合は check_search_expert --no-value との比較必須
    belief_k: 相手型の仮説数 (P7)。0=従来の単一仮定、1=最尤仮説、
        2以上=多世界探索 (仮説重みで統合)。学習の相手としては既定0
        (訓練分布を変えない)。診断 (check_search_expert --belief) で使う
    opp_prior_mix: 相手行動の事前分布の混合率 λ (P6-b)。0 で使用率のみ。
        自己対戦方策 (rl_bridge.policy_of_sim) を相手の立場で評価し、
        根の相手行動分布に (1-λ)·使用率 + λ·方策 で混ぜる
    """
    active = battle.active_pokemon
    opp_active = battle.opponent_active_pokemon
    if active is None or opp_active is None:
        return None
    my_view, opp_view = _mon_view(active, own=True), _mon_view(opp_active)
    if my_view is None or opp_view is None:
        return None

    my_bench, my_bench_mons = _bench(battle.team.values(), active, own=True)
    opp_bench, _ = _bench(battle.opponent_team.values(), opp_active)
    me = SimSide(active=my_view, active_hp=my_view.hp_frac, bench=my_bench,
                 stealth_rock="STEALTH_ROCK" in _names(battle.side_conditions))
    opp = SimSide(active=opp_view, active_hp=opp_view.hp_frac, bench=opp_bench,
                  stealth_rock="STEALTH_ROCK" in
                  _names(battle.opponent_side_conditions))

    available = {m.id: m for m in (battle.available_moves or [])}
    my_moves = list(available.keys())[:4]
    my_field = _field_view(battle, battle.side_conditions)

    # RL価値関数の葉評価ブレンド (アドバイザーの_run_searchと同じ構成)
    leaf_fn = None
    if use_value and depth >= 2:
        try:
            from advisor.rl_bridge import _load_model, value_of_sim
            if _load_model() is not None:
                turn = getattr(battle, "turn", None) or 5

                def leaf_fn(m2, o2):
                    return value_of_sim(m2, o2, my_moves, my_field, turn=turn)
        except Exception:
            leaf_fn = None

    opp_field = _field_view(battle, battle.opponent_side_conditions)
    pool = _opp_move_pool(opp_active)
    # P6-b: 相手行動の事前分布 (相手を me に置いた自己対戦方策の行動分布)
    opp_prior = None
    if opp_prior_mix > 0:
        try:
            from advisor.rl_bridge import policy_of_sim
            opp_prior = policy_of_sim(opp, me, [m for m, _ in pool][:4],
                                      opp_field,
                                      turn=getattr(battle, "turn", None) or 5)
        except Exception:
            opp_prior = None
    result = None
    worlds = _belief_views(opp_view, opp_active.species, belief_k)
    if worlds:
        from advisor.search import aggregate_worlds
        outs, ws = [], []
        for w_k, view_k in worlds:
            opp_k = SimSide(active=view_k, active_hp=opp.active_hp,
                            bench=opp.bench, stealth_rock=opp.stealth_rock)
            outs.append(search(me, opp_k, my_moves, pool,
                               my_field=my_field, opp_field=opp_field,
                               depth=depth, leaf_value_fn=leaf_fn,
                               opp_prior=opp_prior, prior_mix=opp_prior_mix))
            ws.append(w_k)
        result = aggregate_worlds(outs, ws, coverage=sum(ws))
    if result is None:
        result = search(me, opp, my_moves, pool,
                        my_field=my_field, opp_field=opp_field,
                        depth=depth, leaf_value_fn=leaf_fn,
                        opp_prior=opp_prior, prior_mix=opp_prior_mix)

    switchable = {p.species for p in (battle.available_switches or [])}
    team_order = list(battle.team.values())[:6]
    move_order = [m.id for m in list((active.moves or {}).values())[:4]]
    actions = list(result.get("actions") or [])
    if by == "expected":
        actions.sort(key=lambda a: -a["expected"])
    for a in actions:
        if a["kind"] == "move" and a.get("move_id") in available:
            if a["move_id"] not in move_order:
                continue
            idx = move_order.index(a["move_id"])
            mega = bool(battle.can_mega_evolve)
            return {"kind": "move", "move": available[a["move_id"]],
                    "mega": mega, "pokemon": None,
                    "action_index": (10 if mega else 6) + idx}
        if a["kind"] == "switch" and a.get("bench_index") is not None:
            mon = my_bench_mons[a["bench_index"]] \
                if a["bench_index"] < len(my_bench_mons) else None
            if mon is None or mon.species not in switchable:
                continue
            team_idx = next((i for i, p in enumerate(team_order)
                             if p is mon), None)
            if team_idx is None:
                continue
            return {"kind": "switch", "move": None, "mega": False,
                    "pokemon": mon, "action_index": team_idx}
    return None


def teampreview_order(battle) -> str:
    """タイプ相性ベースの選出: 相手6体への攻守スコア上位3体を選ぶ。

    poke_env既定のランダム選出は3v3 (BSS) では大きなハンデになる。
    スコア = Σ_相手 (自分の最良STAB相性 - 相手の最良STAB相性)
    """
    from advisor.dex import get_dex
    dex = get_dex()
    my_mons = list(battle.team.values())
    opp_mons = list(battle.opponent_team.values())

    def best_eff(atk_types: list, def_types: list) -> float:
        return max((dex.effectiveness(t, def_types) for t in atk_types),
                   default=1.0)

    scores = []
    for i, p in enumerate(my_mons):
        mt = _types_of(p)
        s = 0.0
        for q in opp_mons:
            qt = _types_of(q)
            s += best_eff(mt, qt) - best_eff(qt, mt)
        scores.append((s, i))
    order = [i for _, i in sorted(scores, key=lambda x: -x[0])]
    return "/team " + "".join(str(i + 1) for i in order)


def make_search_expert_player(depth: int = 1, **player_kwargs):
    """SearchExpertPlayer を生成する (poke_env import を遅延させるため関数化)"""
    from poke_env.player import Player

    class SearchExpertPlayer(Player):
        """探索エンジンで行動を選ぶ poke_env プレイヤー"""

        def choose_move(self, battle):
            try:
                d = decide(battle, depth=depth)
            except Exception:
                d = None
            if d is None:
                return self.choose_random_move(battle)
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    return SearchExpertPlayer(**player_kwargs)
