"""操縦の方策 (選出): 実戦の助言と測定 (シム) で同じ経路を使うための共通部 (2026-10-05: 操縦はアドバイザーが行う)。

選出の方策 (pick policy):
  model    学習の選出モデル (候補専用 / 配布版が分布内のとき)。champions_agent.env.advisor_player.advisor_pick_order
  rule     実戦の助言と同じ相性の規則 (advisor.selection.advise_selection: ダメージ計算の対面行列)。モデルが無いときの予備
  matchup  タイプ相性の簡易規則 (search_expert.teampreview_order と同じ式)。従来の "teampreview"
  prior    実戦の選出率 (advisor.real_prior) に比例して 3 体を引く (環境チームの選出の模倣用)
  heuristic  poke-env SimpleHeuristicsPlayer 自身の選出 (従来の環境チーム)
相手 (環境チーム) の操縦 (pilot): heuristic = poke-env SimpleHeuristicsPlayer (従来) / rl = 学習済み行動方策 (ModelPlayer、ピンの ema)。
どちらが実戦に近いかは実験 12 (選出の一致) / 13 (勝率) で決め、config BUILD_OPP_PILOT / BUILD_OPP_PICK_POLICY に反映する。
perm を返す関数は純粋 (依存は引数で渡す)。poke-env に触る部分は遅延 import。
"""
from __future__ import annotations

import re
from typing import Callable, Optional

PICK_POLICIES = ("model", "rule", "matchup", "prior", "heuristic", "teampreview")
OPP_PILOTS = ("heuristic", "rl")


def _toid(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


# ------------------------------------------------------------------ 純粋 (perm = 自分の 6 体の index、先頭が先発)
def perm_to_team_order(perm, n: int) -> str:
    """perm (index 3 つ) → Showdown の /team 文字列 (残りは元の順で後ろに)"""
    chosen = [int(i) for i in perm]
    rest = [i for i in range(n) if i not in chosen]
    return "/team " + "".join(str(i + 1) for i in chosen + rest)


def matchup_perm(my_types: list, opp_types: list, effectiveness: Callable) -> tuple:
    """タイプ相性の簡易規則 (search_expert.teampreview_order と同じ式): 点 = Σ_相手 (自分の最良 STAB 相性 − 相手の最良 STAB 相性)。
    my_types / opp_types = 個体ごとのタイプの列。戻り値 = 点の降順の上位 3 の index"""
    def best_eff(atk: list, dfn: list) -> float:
        return max((float(effectiveness(t, list(dfn))) for t in atk), default=1.0)
    scores = []
    for i, mt in enumerate(my_types):
        s = 0.0
        for qt in opp_types:
            s += best_eff(mt, qt) - best_eff(qt, mt)
        scores.append((s, i))
    order = [i for _s, i in sorted(scores, key=lambda x: (-x[0], x[1]))]
    return tuple(order[:3])


def prior_perm(my_ids: list, prior_of: Callable, rng, lead_order: Optional[tuple] = None) -> tuple:
    """実戦の選出率に比例した重みで 3 体を引く (復元なし)。prior_of(species_id) → 選出率 or None (None は既知の平均、無ければ 1)。
    先発は lead_order (matchup の順) に居る方を先に、無ければ引いた順"""
    priors = [prior_of(s) for s in my_ids]
    known = [p for p in priors if p is not None]
    mean = (sum(known) / len(known)) if known else 1.0
    w = [float(p) if p is not None else float(mean) for p in priors]
    w = [max(x, 1e-6) for x in w]
    idx = list(range(len(my_ids)))
    picked: list = []
    while idx and len(picked) < 3:
        tot = sum(w[i] for i in idx)
        r = rng.random() * tot
        acc = 0.0
        for i in idx:
            acc += w[i]
            if acc >= r:
                picked.append(i)
                idx.remove(i)
                break
    if lead_order:
        picked.sort(key=lambda i: (lead_order.index(i) if i in lead_order else len(lead_order)))
    return tuple(picked)


def rule_perm(state: dict, use_registered: bool = True, resolver=None) -> Optional[tuple]:
    """実戦の助言と同じ相性の規則 (advisor.selection.advise_selection) の推奨 → perm (先発が先頭)。評価できなければ None"""
    from advisor.selection import advise_selection
    adv = advise_selection(state, resolver, use_registered=use_registered)
    if not adv.get("ok"):
        return None
    rec = adv.get("recommend") or []
    lead = [r["index"] for r in rec if r.get("lead")]
    rest = [r["index"] for r in rec if not r.get("lead")]
    perm = tuple(lead + rest)
    return perm if len(perm) == 3 else None


def model_perm(my_ids: list, opp_ids: list, path=None) -> Optional[tuple]:
    from champions_agent.agent import selection_dispatch as SD
    best = SD.predict_best(list(my_ids), list(opp_ids), path)
    return tuple(best[0]) if best else None


def entries_from_species(ids: list, items: Optional[list] = None, own: bool = True) -> list:
    """種 id (と持ち物) だけから助言の状態辞書のパーティ要素を作る (選出画面相当)"""
    from advisor.infer import species_ja_name
    out = []
    for i, sid in enumerate(ids):
        item = (items[i] if items and i < len(items) else None) or None
        e = {"species_id": sid, "species_ja": species_ja_name(sid) or sid, "item_id": item, "hp_percent": 100.0,
             "status": None, "moves": [], "revealed_moves": []}
        if own:
            e["is_picked"] = False
        out.append(e)
    return out


def state_from_entries(player_entries: list, opponent_entries: list) -> dict:
    """選出画面の状態辞書 (advise_selection の入力)"""
    return {"scene": "selection", "selection_picked": 0, "field": {},
            "player": {"party": list(player_entries)}, "opponent": {"party": list(opponent_entries)}}


def pick_perm(policy: str, my_ids: list, opp_ids: list, *, dex=None, rng=None, prior_of: Optional[Callable] = None,
              model_path=None, state: Optional[dict] = None, use_registered: bool = False, my_items: Optional[list] = None,
              opp_items: Optional[list] = None) -> Optional[tuple]:
    """種 id の列から方策ごとの perm。rule は state (無ければ種から作る) で advise_selection を呼ぶ。失敗は None"""
    if dex is None:
        from advisor.dex import get_dex
        dex = get_dex()

    def types_of(ids):
        return [list((dex.species(s) or {}).get("types") or []) for s in ids]
    if policy in ("matchup", "teampreview", "heuristic"):
        return matchup_perm(types_of(my_ids), types_of(opp_ids), dex.effectiveness)
    if policy == "model":
        return model_perm(my_ids, opp_ids, model_path)
    if policy == "prior":
        import random as _random
        lead = matchup_perm(types_of(my_ids), types_of(opp_ids), dex.effectiveness)
        return prior_perm(my_ids, prior_of or (lambda s: None), rng or _random.Random(0), lead)
    if policy == "rule":
        st = state or state_from_entries(entries_from_species(my_ids, my_items, own=True),
                                         entries_from_species(opp_ids, opp_items, own=False))
        return rule_perm(st, use_registered=use_registered)
    raise ValueError(f"未知の選出方策: {policy}")


def pick_precision(pred: tuple, observed: set, n: int = 3) -> Optional[float]:
    """予測した 3 体のうち、実際に見えた相手の選出 (observed、部分でもよい) に入っていた割合 |pred ∩ obs| / |obs| (純粋)"""
    if not observed:
        return None
    return round(len(set(pred) & set(observed)) / len(observed), 4)


# ------------------------------------------------------------------ poke-env (遅延 import)
def apply_pilot_teampreview(player, policy: str, rng=None, resolver=None, use_registered: bool = False) -> None:
    """poke-env の Player の選出を policy に差し替える (環境チーム用)。heuristic は Player 自身の選出のまま。
    失敗したときは元の teampreview に落ちる"""
    import types
    if policy == "heuristic":
        return
    orig = player.teampreview
    from advisor.dex import get_dex
    dex = get_dex()
    prior_of = None
    if policy == "prior":
        try:
            from advisor.real_prior import load_bank, species_pick_prior
            bank = load_bank()
            prior_of = (lambda s: species_pick_prior(s, bank)) if bank else (lambda s: None)
        except Exception:
            prior_of = lambda s: None       # noqa: E731
    model_path = None
    if policy == "model":
        from champions_agent.agent import selection_dispatch as SD
        model_path = SD.general_model_path()

    def _teampreview(self, battle):
        try:
            mons = list(battle.team.values())
            opp_src = getattr(battle, "teampreview_opponent_team", None) or battle.opponent_team.values()
            opps = list(opp_src)
            my_ids = [p.species for p in mons]
            opp_ids = [p.species for p in opps]
            my_items = [getattr(p, "item", None) for p in mons]
            perm = pick_perm(policy, my_ids, opp_ids, dex=dex, rng=rng, prior_of=prior_of, model_path=model_path,
                             use_registered=use_registered, my_items=my_items)
            if perm and len(set(perm)) == 3:
                return perm_to_team_order(perm, len(mons))
        except Exception:
            pass
        return orig(battle)
    player.teampreview = types.MethodType(_teampreview, player)


def make_opponent_player(pilot: str, team, battle_format: str, account_configuration, pick_policy: str = "rule",
                         play_style: str = "balance", rng=None, **kwargs):
    """環境チームの操縦者: heuristic (poke-env SimpleHeuristicsPlayer) / rl (学習済み行動方策。ピン dir の ema → best の順)。
    選出は pick_policy (heuristic は Player 自身の選出)。rl の既定の選出は乱択なので必ず差し替える"""
    from champions_agent.env.showdown_env import TrainingServerConfiguration, make_benchmark_player
    if pilot == "rl":
        from champions_agent.config import MODELS_DIR
        from champions_agent.train.evaluate import ModelPlayer
        ckpt = "ema" if (MODELS_DIR / f"battle_policy_{play_style}_ema.zip").exists() else "best"
        player = ModelPlayer(battle_format=battle_format, server_configuration=TrainingServerConfiguration, team=team,
                             account_configuration=account_configuration, play_style=play_style, checkpoint=ckpt, **kwargs)
        apply_pilot_teampreview(player, pick_policy if pick_policy != "heuristic" else "matchup", rng=rng)
        return player
    player = make_benchmark_player(battle_format=battle_format, team=team, account_configuration=account_configuration, **kwargs)
    apply_pilot_teampreview(player, pick_policy, rng=rng)
    return player
