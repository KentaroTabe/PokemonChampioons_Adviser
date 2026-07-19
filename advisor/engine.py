"""行動評価エンジン。

画面から抽出した BattleStateV2 の状態辞書を受け取り、
1ターン先の期待値 (与ダメージ / 被ダメージ / 行動順 / 交代先の相性) を計算して
行動候補をスコアリングする。探索ベース (ダメージ計算+期待値) のアプローチで、
PokéAgent Challenge 2025 で強化学習を上回った foul-play 系の簡易版に相当する。

相手の型 (技/持ち物/特性) は使用率DB (advisor.sets) から予測する。
"""
from __future__ import annotations

import json
from typing import Optional

from advisor.dex import get_dex, BOOST_MULT
from advisor.damage import MonView, FieldView, calc_damage
from advisor.sets import get_predictor

# 画面の相性ヒント -> タイプ倍率
HINT_MULT = {
    "super_extreme": 4.0, "super": 2.0, "neutral": 1.0,
    "resist": 0.5, "resist_heavy": 0.25, "immune": 0.0,
}

# 攻撃技以外の代表的な技への簡易評価 (スコアはダメージ%相当のボーナス)
STATUS_MOVE_VALUE = {
    "protect": ("様子見/スカウトに有効。連続使用は失敗しやすい", 18),
    "recover": ("回復", 0), "softboiled": ("回復", 0), "roost": ("回復", 0),
    "slackoff": ("回復", 0), "moonlight": ("回復", 0), "synthesis": ("回復", 0),
    "swordsdance": ("攻撃+2の積み技", 25), "nastyplot": ("特攻+2の積み技", 25),
    "calmmind": ("特攻/特防+1", 22), "dragondance": ("攻撃/素早さ+1", 25),
    "irondefense": ("防御+2", 15), "bulkup": ("攻撃/防御+1", 20),
    "stealthrock": ("ステルスロック設置", 25), "spikes": ("まきびし設置", 18),
    "toxicspikes": ("どくびし設置", 15), "stickyweb": ("ねばねばネット設置", 18),
    "thunderwave": ("まひ撒き", 20), "willowisp": ("やけど撒き", 22),
    "toxic": ("毒撒き", 20), "spore": ("催眠", 30), "sleeppowder": ("催眠", 25),
    "substitute": ("身代わり", 15), "leechseed": ("やどりぎ", 18),
    "taunt": ("挑発", 15), "encore": ("アンコール", 15),
    "trickroom": ("トリックルーム展開", 15), "tailwind": ("おいかぜ展開", 18),
    "reflect": ("リフレクター展開", 20), "lightscreen": ("ひかりのかべ展開", 20),
    "auroraveil": ("オーロラベール展開", 25), "defog": ("設置技除去", 12),
    "rapidspin": ("設置技除去", 12), "uturn": ("対面操作", 0), "voltswitch": ("対面操作", 0),
    "haze": ("ランクリセット", 12), "whirlwind": ("吹き飛ばし", 12), "roar": ("吹き飛ばし", 12),
}

RECOVERY_MOVES = {"recover", "softboiled", "roost", "slackoff", "moonlight",
                  "synthesis", "morningsun", "shoreup", "strengthsap"}

_TYPE_JA2EN = None


def type_ja2en() -> dict:
    global _TYPE_JA2EN
    if _TYPE_JA2EN is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _TYPE_JA2EN = dict(raw.get("types", {}))
    return _TYPE_JA2EN


# ==============================================================================
# 状態辞書 -> ビュー構築
# ==============================================================================
OFFENSIVE_EV = {"atk": 252, "spa": 252, "spe": 252}


def build_mon_view(p: dict, resolver=None, side: str = "opponent") -> Optional[MonView]:
    """PokemonState辞書 -> MonView。種族不明なら None"""
    dex = get_dex()
    sid = p.get("species_id")
    sp = dex.species(sid)
    if sp is None:
        return None

    types_en = [type_ja2en().get(t, t) for t in (p.get("types") or [])]
    if not types_en:
        types_en = sp["types"]

    hp_frac = 1.0
    if p.get("hp_percent") is not None:
        hp_frac = max(0.0, min(1.0, p["hp_percent"] / 100.0))
    elif p.get("hp_current") is not None and p.get("hp_max"):
        hp_frac = p["hp_current"] / p["hp_max"]

    # 特性が一意な場合 (単一特性/メガ後の固定特性) は確定値を使う。
    # メガ前に判明していた特性が状態に残っていてもメガ後は固定値が正
    from vision.abilities import fixed_ability
    ability = p.get("ability_id")
    fa = fixed_ability(sid, is_mega=bool(p.get("is_mega")),
                       item_id=p.get("item_id"))
    if fa and (p.get("is_mega") or not ability):
        ability = fa

    # 自分側: config/my_team.json に登録された型があれば実際の
    # 能力ポイント/性格/持ち物で計算する (未登録は攻撃系252仮定)
    ev, nature, item = dict(OFFENSIVE_EV), {}, p.get("item_id")
    if side == "player":
        from advisor.my_team import get_my_build
        build = get_my_build(p.get("species_ja"))
        if build:
            if build["ev"]:
                ev = build["ev"]
            nature = build["nature"]
            if not item and build["item_ja"] and resolver:
                r = resolver.resolve(build["item_ja"], "items", cutoff=0.85)
                if r:
                    item = r[1]
            if not ability and build["ability_ja"] and resolver:
                r = resolver.resolve(build["ability_ja"], "abilities", cutoff=0.85)
                if r:
                    ability = r[1]

    return MonView(
        species_id=sid,
        name_ja=p.get("species_ja") or p.get("display_name") or sid,
        types=types_en,
        base=sp["baseStats"],
        hp_frac=hp_frac,
        status=p.get("status"),
        boosts=p.get("boosts") or {},
        ability=ability,
        item=item,
        ev=ev,
        nature=nature,
    )


def build_field_view(state: dict, attacker_side: str) -> FieldView:
    """攻撃方向に応じた FieldView (壁は防御側のものを見る)"""
    f = state["field"]
    def_side = "opponent" if attacker_side == "player" else "player"
    screens = state[def_side]["screens"]
    return FieldView(
        weather=f.get("weather"),
        terrain=f.get("terrain"),
        trick_room=f.get("trick_room", False),
        reflect=screens.get("reflect", False),
        light_screen=screens.get("light_screen", False),
        aurora_veil=screens.get("aurora_veil", False),
    )


def effective_speed(mon: MonView, side: dict, field: dict) -> float:
    spe = mon.stat("spe")
    if mon.item == "choicescarf":
        spe *= 1.5
    if side.get("tailwind"):
        spe *= 2
    if mon.status == "paralysis":
        spe *= 0.5
    return spe


# ==============================================================================
# 相手の技候補
# ==============================================================================
def opponent_move_pool(opp_state: dict, opp_view: MonView, resolver) -> list:
    """相手の技候補 [(move_id, weight, revealed)] を4枠モデルで作る。

    ポケモンは技を4つしか覚えられないため:
    - 判明済みの技 k 個は確定枠 (weight=100)
    - 残り 4-k 枠は使用率データから「判明技を除いた採用率上位」で推定
    - 4 つ判明済みなら推定は行わない (完全スカウト状態)
    """
    pool = {}
    revealed_set = set()
    for ja in opp_state.get("revealed_moves") or []:
        r = resolver.resolve(ja, "moves", cutoff=0.8) if resolver else None
        if r:
            pool[r[1]] = 100.0
            revealed_set.add(r[1])

    remaining_slots = max(0, 4 - len(revealed_set))
    # 4技判明 (フルスカウト) でも特性/持ち物予測には使うため常に取得する
    pred = get_predictor().predict(opp_view.species_id)
    if remaining_slots > 0:
        unrevealed = [(mid, pct) for mid, pct in pred["moves"]
                      if mid not in revealed_set]
        # 残り枠数+2個まで候補として保持 (重みは採用率を残り枠比率で減衰)
        scale = remaining_slots / 4.0
        for mid, pct in unrevealed[:remaining_slots + 2]:
            pool[mid] = pct * (0.5 + 0.5 * scale)
    if not pool:
        # フォールバック: タイプ一致の代表技を仮定
        dex = get_dex()
        for t in opp_view.types:
            for mid, mv in (("flamethrower", None),):
                pass
        generic = {"Fire": "flamethrower", "Water": "surf", "Electric": "thunderbolt",
                   "Grass": "energyball", "Ice": "icebeam", "Fighting": "closecombat",
                   "Poison": "sludgebomb", "Ground": "earthquake", "Flying": "bravebird",
                   "Psychic": "psychic", "Bug": "bugbuzz", "Rock": "stoneedge",
                   "Ghost": "shadowball", "Dragon": "dracometeor", "Dark": "knockoff",
                   "Steel": "ironhead", "Fairy": "moonblast", "Normal": "doubleedge"}
        for t in opp_view.types:
            if t in generic:
                pool[generic[t]] = 50.0
    # 予測特性/持ち物を反映
    if not opp_view.ability and pred["abilities"]:
        opp_view.ability = pred["abilities"][0][0]
    if not opp_view.item and pred["items"]:
        opp_view.item = pred["items"][0][0]
    return list(pool.items())


# ==============================================================================
# メインの評価
# ==============================================================================
def evaluate(state: dict, resolver=None) -> dict:
    """状態辞書から行動候補のランキングを生成する"""
    dex = get_dex()
    my_state = state["player"]
    opp_state = state["opponent"]

    my_active_idx = my_state.get("active_index")
    opp_active_idx = opp_state.get("active_index")
    if my_active_idx is None or my_active_idx >= len(my_state["party"]):
        return {"ok": False, "reason": "自分の場のポケモンが未特定です"}
    my_p = my_state["party"][my_active_idx]
    my_view = build_mon_view(my_p, resolver, side="player")
    if my_view is None:
        return {"ok": False, "reason": f"自分のポケモン ({my_p.get('display_name')}) の種族を特定できません"}

    opp_p = None
    opp_view = None
    opp_inference_note = ""
    if opp_active_idx is not None and opp_active_idx < len(opp_state["party"]):
        opp_p = opp_state["party"][opp_active_idx]
        opp_view = build_mon_view(opp_p, resolver)
        if opp_view is None and (opp_p.get("types") or []):
            # 種族未判明: タイプから使用率ベースで最有力種族を推測して評価する
            try:
                from advisor.infer import get_inference
                cands = get_inference().candidates(opp_p["types"])
            except Exception:
                cands = []
            if cands:
                sid, prob, ja = cands[0]
                assumed = dict(opp_p)
                assumed["species_id"] = sid
                opp_view = build_mon_view(assumed, resolver)
                if opp_view is not None:
                    opp_view.name_ja = f"{ja}(推測)"
                    note = f"相手は {ja} と推測して評価 (確率{int(round(prob * 100))}%"
                    if len(cands) >= 2:
                        note += f", 次点: {cands[1][2]}{int(round(cands[1][1] * 100))}%"
                    opp_inference_note = note + ")"

    # 型推定 (先後・ダメージ観測の尤度スコアリング) が確度を持っていれば、
    # 相手のEV/性格/持ち物の仮定を推定値に差し替える
    opp_spread_note = ""
    if opp_view is not None:
        try:
            from advisor.ev_infer import get_tracker, _nature_mult
            guess = get_tracker().best_for(opp_view.species_id)
            if guess and guess["n_obs"] >= 1 and guess["prob"] >= 0.25:
                opp_view.ev = dict(guess["evs"])
                opp_view.nature = _nature_mult(guess["nature"])
                if not opp_view.item and guess["item"]:
                    opp_view.item = guess["item"]
                opp_spread_note = f"相手の型推定: {guess['summary']}"
        except Exception:
            pass

    my_field = build_field_view(state, "player")
    opp_field = build_field_view(state, "opponent")

    actions = []
    threats = []
    speed_note = ""

    # ------------------------------------------------------------------
    # 相手からの脅威 (相手の技候補ごとの被ダメージ)
    # ------------------------------------------------------------------
    opp_best_dmg = 0.0
    opp_best_move = None
    i_am_faster = None
    opp_moves_note = ""
    pool = []
    if opp_view is not None:
        pool = opponent_move_pool(opp_p, opp_view, resolver)
        for mid, weight in pool:
            mv = dex.move(mid)
            if not mv:
                continue
            d = calc_damage(opp_view, my_view, mid, opp_field)
            acc = (mv["accuracy"] or 100) / 100.0
            exp = d["avg"] * acc
            if d["avg"] > 0:
                threats.append({
                    "move_id": mid, "dmg_min": d["min"], "dmg_max": d["max"],
                    "dmg_avg": d["avg"], "weight": weight,
                    "revealed": weight >= 100.0,
                })
                if exp > opp_best_dmg:
                    opp_best_dmg = exp
                    opp_best_move = mid
        threats.sort(key=lambda t: -t["dmg_avg"])

        # 4枠モデルの説明文 (判明 k/4 + 残り枠の推定)
        revealed_ja = opp_p.get("revealed_moves") or []
        k = min(len(revealed_ja), 4)
        if k >= 4:
            opp_moves_note = f"相手の技: 4/4判明 ({'、'.join(revealed_ja[:4])}) — 完全スカウト済み"
        elif k > 0:
            est = [mid for mid, w in pool if w < 100.0][:4 - k]
            opp_moves_note = (f"相手の技: 判明{k}/4 ({'、'.join(revealed_ja)}) / "
                              f"残り{4 - k}枠は使用率から推定: {'、'.join(est)}")

        my_spe = effective_speed(my_view, my_state, state["field"])
        opp_spe = effective_speed(opp_view, opp_state, state["field"])
        i_am_faster = my_spe >= opp_spe
        if state["field"].get("trick_room"):
            i_am_faster = not i_am_faster
            speed_note = "トリックルーム中: 遅い方が先に動きます。"
        faster_txt = "自分が先手" if i_am_faster else "相手が先手"
        speed_note += (f"推定素早さ: 自分{int(my_spe)} vs 相手{int(opp_spe)} ({faster_txt}の見込み)。"
                       f"※相手のスカーフ/性格補正は未確定")

    my_hp_pct = my_view.hp_frac * 100.0
    opp_hp_pct = opp_view.hp_frac * 100.0 if opp_view else 100.0
    threat_ko = opp_best_dmg >= my_hp_pct * 0.95

    # ------------------------------------------------------------------
    # 自分の技の評価
    # ------------------------------------------------------------------
    for slot in my_p.get("moves") or []:
        mid = slot.get("move_id")
        mv = dex.move(mid)
        name = slot.get("name_ja") or mid or "不明な技"
        if mv is None:
            continue
        if slot.get("pp") == 0:
            continue

        acc = (mv["accuracy"] or 100) / 100.0
        reason_parts = []
        score = 0.0

        if mv["category"] == "Status" or not mv["power"]:
            desc, base_score = STATUS_MOVE_VALUE.get(mid, ("補助技", 8))
            score = float(base_score)
            reason_parts.append(desc)
            if mid in RECOVERY_MOVES:
                missing = 100.0 - my_hp_pct
                score = min(45.0, missing * 0.6)
                reason_parts.append(f"残りHP{my_hp_pct:.0f}%からの回復")
            if mid == "stealthrock" and opp_state["hazards"]["stealth_rock"]:
                score = 2.0
                reason_parts.append("既に設置済み")
            if mid == "protect" and threat_ko and not i_am_faster:
                score += 10
                reason_parts.append("高火力を一度受け流せる")
            if threat_ko and i_am_faster is False and mid not in ("protect",):
                score -= 15
                reason_parts.append("相手の攻撃で倒される危険あり")
        else:
            override = None
            if opp_view is None:
                override = HINT_MULT.get(slot.get("effectiveness")) \
                    if slot.get("effectiveness") else 1.0
            if opp_view is not None:
                d = calc_damage(my_view, opp_view, mid, my_field)
            else:
                dummy = MonView(species_id="", types=["Normal"],
                                base={"hp": 80, "atk": 80, "def": 80,
                                      "spa": 80, "spd": 80, "spe": 80},
                                ev=dict(OFFENSIVE_EV))
                d = calc_damage(my_view, dummy, mid, my_field,
                                override_type_mult=override)
            exp = d["avg"] * acc

            ko_prob = 0.0
            if d["min"] >= opp_hp_pct:
                ko_prob = 1.0
            elif d["max"] >= opp_hp_pct:
                ko_prob = 0.5
            ko_prob *= acc

            effective = min(exp, opp_hp_pct)
            score = effective + ko_prob * 40.0
            if mv["priority"] > 0:
                score += 6.0
                if threat_ko:
                    score += 14.0
                    reason_parts.append("先制技: 倒される前に削れる")
            if i_am_faster and ko_prob >= 0.5:
                score += 25.0
                reason_parts.append("先手で倒せる見込み")
            elif ko_prob >= 0.5 and not i_am_faster:
                reason_parts.append("倒せる圏内だが相手が先手の可能性")
            if threat_ko and not i_am_faster and mv["priority"] <= 0 and ko_prob < 0.5:
                score -= 12.0

            eff_txt = {4.0: "抜群(4倍)", 2.0: "抜群", 1.0: "等倍", 0.5: "いまひとつ",
                       0.25: "いまひとつ(1/4)", 0.0: "無効"}.get(d["type_mult"], "")
            dmg_txt = f"予測ダメージ {d['min']:.0f}〜{d['max']:.0f}%"
            if acc < 1.0:
                dmg_txt += f" (命中{int(acc * 100)})"
            reason_parts.insert(0, f"{dmg_txt} {eff_txt}")
            reason_parts += d["notes"]

        actions.append({
            "kind": "move",
            "id": mid,
            "name": name,
            "score": round(score, 1),
            "reason": " / ".join(reason_parts),
        })

    # ------------------------------------------------------------------
    # 交代の評価
    # ------------------------------------------------------------------
    for i, p in enumerate(my_state["party"]):
        if i == my_active_idx or p.get("status") == "fainted":
            continue
        cand = build_mon_view(p, resolver, side="player")
        if cand is None or opp_view is None:
            continue

        incoming = 0.0
        if opp_best_move:
            d_in = calc_damage(opp_view, cand, opp_best_move, opp_field)
            incoming = d_in["avg"]

        hazard_dmg = 0.0
        hz = my_state["hazards"]
        if hz["stealth_rock"]:
            rock_mult = get_dex().effectiveness("Rock", cand.types)
            hazard_dmg += 12.5 * rock_mult
        if hz["spikes"]:
            hazard_dmg += [0, 12.5, 16.7, 25.0][min(3, hz["spikes"])]

        # 交代先からの反撃力 (予測される自分の技は不明のためタイプ一致代表技で近似)
        counter = 0.0
        for t in cand.types:
            generic = {"Fire": "flamethrower", "Water": "surf", "Electric": "thunderbolt",
                       "Grass": "energyball", "Ice": "icebeam", "Fighting": "closecombat",
                       "Poison": "sludgebomb", "Ground": "earthquake", "Flying": "bravebird",
                       "Psychic": "psychic", "Bug": "bugbuzz", "Rock": "stoneedge",
                       "Ghost": "shadowball", "Dragon": "outrage", "Dark": "knockoff",
                       "Steel": "ironhead", "Fairy": "moonblast", "Normal": "doubleedge"}
            g = generic.get(t)
            if g:
                d_out = calc_damage(cand, opp_view, g, my_field)
                counter = max(counter, d_out["avg"])

        cand_hp_pct = cand.hp_frac * 100.0
        survives = (incoming + hazard_dmg) < cand_hp_pct * 0.9
        score = (counter * 0.45) - (incoming * 0.5) - hazard_dmg * 0.8
        if survives:
            score += 20.0
        if threat_ko and not i_am_faster:
            score += 10.0  # 居座りが危険なら交代の価値が上がる

        reason = (f"被ダメ予測 {incoming:.0f}%"
                  + (f" + 設置技 {hazard_dmg:.0f}%" if hazard_dmg else "")
                  + f" / 交代後の打点 約{counter:.0f}%")
        if not survives:
            reason += " / 交代出しで倒される危険あり"

        actions.append({
            "kind": "switch",
            "id": p.get("species_id") or f"slot{i}",
            "name": p.get("species_ja") or p.get("display_name") or f"{i}番",
            "score": round(score, 1),
            "reason": reason,
        })

    actions.sort(key=lambda a: -a["score"])

    # メガシンカ可能なら注記
    mega_note = ""
    if (not state.get("mega_used", {}).get("player")
            and (my_p.get("item_id") == "megastone"
                 or (my_p.get("item_ja") or "").endswith(("ナイトX", "ナイトY", "ナイト")))):
        mega_note = "メガシンカが可能です (種族値+100)。攻撃するターンにメガシンカを推奨。"

    # 同時手番探索 (択の利得行列 + 2手読み)。失敗しても本体は返す
    gtheory = None
    try:
        gtheory = _run_search(state, my_state, my_view, my_p,
                              opp_state, opp_view, resolver,
                              pool, my_field, opp_field)
    except Exception:
        import traceback
        traceback.print_exc()

    return {
        "ok": True,
        "actions": actions,
        "threats": threats[:5],
        "speed_note": speed_note,
        "mega_note": mega_note,
        "opp_inference": opp_inference_note,
        "opp_moves_note": opp_moves_note,
        "opp_spread_note": opp_spread_note,
        "gtheory": gtheory,
        "best": actions[0] if actions else None,
    }


def _hp_frac_of(p: dict) -> float:
    if p.get("hp_percent") is not None:
        return max(0.0, min(1.0, p["hp_percent"] / 100.0))
    if p.get("hp_current") is not None and p.get("hp_max"):
        return max(0.0, p["hp_current"] / p["hp_max"])
    return 1.0


def _run_search(state, my_state, my_view, my_p, opp_state, opp_view,
                resolver, pool, my_field, opp_field):
    """状態辞書 -> SimSide を組み立てて同時手番探索を実行する"""
    from advisor.search import SimSide, search
    if my_view is None or opp_view is None:
        return None

    my_moves = [m.get("move_id") for m in (my_p.get("moves") or [])
                if m.get("move_id")]
    if not my_moves:
        return None

    def bench_of(side_state, side):
        bench = []
        active_idx = side_state.get("active_index")
        for i, p in enumerate(side_state.get("party", [])):
            if i == active_idx or p.get("status") == "fainted":
                continue
            v = build_mon_view(p, resolver, side=side)
            if v is not None:
                bench.append((v, _hp_frac_of(p)))
        return bench[:4]

    me = SimSide(active=my_view, active_hp=_hp_frac_of(my_p),
                 bench=bench_of(my_state, "player"),
                 stealth_rock=bool(my_state.get("hazards", {}).get("stealth_rock")))
    opp = SimSide(active=opp_view, active_hp=_hp_frac_of(
                      opp_state["party"][opp_state["active_index"]]),
                  bench=bench_of(opp_state, "opponent"),
                  stealth_rock=bool(opp_state.get("hazards", {}).get("stealth_rock")))
    result = search(me, opp, my_moves, pool,
                    my_field=my_field, opp_field=opp_field)
    # 表示用の要約 (上位3行動)
    lines = []
    for a in result["actions"][:3]:
        mark = " ⚠択リスク" if a["risky"] else ""
        lines.append(f"{a['label']}: 期待{a['expected']:+.2f} "
                     f"保証{a['worst']:+.2f} (最悪応手: {a['worst_reply']}){mark}")
    result["summary_lines"] = lines
    return result
