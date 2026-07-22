"""選出画面のアドバイス: 6体から最適な3体+先発を提案する。

相手はタイプアイコンしか分からないため、**最新の使用率データから
「そのタイプ構成なら何%の確率でどの種族か」を推測** (advisor/infer.py) し、
候補種族の実際の種族値・予測技構成に対するダメージ計算で評価する:

- 与ダメージ: 自分の予測技構成 -> 候補種族への最大打点% (candidate確率で加重)
- 被ダメージ: 候補種族の予測技構成 -> 自分への最大打点%
- 候補が推測できないタイプ構成はタイプ相性ヒューリスティクスへフォールバック

チーム評価は C(6,3)=20通りの総当たり:
    Σ_j max_i score(i,j)  … 相手の全員に対して「誰かが有利」であること (補完性)
  + 0.3 Σ_i avg_j score(i,j) … 個々の平均的な強さ
先発は選出3体のうち平均スコア最大の個体。
"""
from __future__ import annotations

import json
from itertools import combinations
from typing import Optional

from advisor.damage import MonView, calc_damage
from advisor.dex import get_dex
from advisor.engine import OFFENSIVE_EV
from advisor.infer import get_inference
from advisor.sets import get_predictor

_TYPE_JA2EN = None


def _ja2en() -> dict:
    global _TYPE_JA2EN
    if _TYPE_JA2EN is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _TYPE_JA2EN = dict(raw.get("types", {}))
    return _TYPE_JA2EN


def _my_attack_profile(species_id: str) -> list:
    """自分のポケモンの攻撃プロファイル: [(攻撃タイプ, 実効威力係数)]

    使用率DBの予測技 (上位) から、攻撃技のタイプと
    威力x採用率x攻撃種族値の係数を作る。DBに無い場合はタイプ一致80として扱う。
    """
    dex = get_dex()
    sp = dex.species(species_id)
    if sp is None:
        return []
    atk_stat = max(sp["baseStats"].get("atk", 80), sp["baseStats"].get("spa", 80))

    profile = []
    pred = get_predictor().predict(species_id)
    for mid, pct in pred["moves"]:
        mv = dex.move(mid)
        if not mv or mv["category"] == "Status" or not mv["power"]:
            continue
        stab = 1.5 if mv["type"] in sp["types"] else 1.0
        coeff = (mv["power"] / 100.0) * stab * (atk_stat / 120.0) * min(1.0, pct / 30.0 + 0.5)
        profile.append((mv["type"], coeff))
    if not profile:
        # 予測が無い場合はタイプ一致技 (威力90相当) を仮定
        for t in sp["types"]:
            profile.append((t, 0.9 * 1.5 * (atk_stat / 120.0)))
    return profile


def _matchup_score(my_profile: list, my_types: list, opp_types_en: list) -> float:
    """自分1体 vs 相手1体 (タイプのみ・フォールバック用) のマッチアップスコア"""
    dex = get_dex()
    # 与ダメージ側: 予測技の中でその相手に最も通る打点
    offense = 0.0
    for atk_type, coeff in my_profile:
        eff = dex.effectiveness(atk_type, opp_types_en)
        offense = max(offense, coeff * eff)
    # 被ダメージ側: 相手がタイプ一致で殴ってきたときの最大倍率
    threat = 0.0
    for t in opp_types_en:
        threat = max(threat, dex.effectiveness(t, my_types))
    return offense - 0.8 * threat


def _is_mega_holder(p: dict) -> bool:
    """メガストーン持ちかどうか (メガシンカは1試合1回のため選出評価で考慮する)"""
    item_id = p.get("item_id") or ""
    item_ja = p.get("item_ja") or ""
    if item_id == "megastone":
        return True
    if "ナイト" in item_ja:
        return True
    # 実在メガストーンID (gengarite等)。しんかのきせき(eviolite)は除外
    if item_id.endswith("ite") and item_id not in ("eviolite",):
        return True
    return False


# メガストーン持ちが2体以上選出に入った場合のペナルティ
# (メガシンカは1試合1回のため、2体目以降のストーンは実質「持ち物なし」になる)
MEGA_DUPLICATE_PENALTY = 0.6

# 天候シナジーのボーナス (設置役+恩恵役が同時選出された場合、恩恵役1体あたり)。
# スケール根拠: coverage合計は1対面あたり概ね0.5-1.0。天候下の素早さ倍化+
# タイプ一致技1.5倍は実質1-2対面の有利化に相当する (実測: 雨コア
# ペリッパー+メガラグラージが選ばれる閾値は1.2、余裕を持たせて1.4)
WEATHER_SYNERGY_BONUS = 1.4

# 天候の設置特性と恩恵特性 (選出シナジー評価用)
_WEATHER_SETTERS = {
    "drizzle": "rain", "drought": "sun",
    "sandstream": "sand", "snowwarning": "snow",
}
_WEATHER_ABUSERS = {
    "swiftswim": "rain", "raindish": "rain", "dryskin": "rain",
    "chlorophyll": "sun", "solarpower": "sun", "flowergift": "sun",
    "sandrush": "sand", "sandforce": "sand", "sandveil": "sand",
    "slushrush": "snow", "icebody": "snow", "snowcloak": "snow",
}


def _mega_species_id(species_id: str, item_id: str) -> Optional[str]:
    """メガストーン持ちのメガ後種族ID (dexに存在する場合のみ)"""
    dex = get_dex()
    # リザードン等のX/Y分岐はストーンIDの末尾で判別
    if item_id and item_id.endswith(("itex", "itey")):
        cand = f"{species_id}mega{item_id[-1]}"
        if dex.species(cand):
            return cand
    cand = f"{species_id}mega"
    return cand if dex.species(cand) else None


_RESOLVER = None


def _resolve_ability_id(ability_ja: str) -> Optional[str]:
    global _RESOLVER
    if _RESOLVER is None:
        from vision.normalize import NameResolver
        _RESOLVER = NameResolver()
    r = _RESOLVER.resolve(ability_ja, "abilities", cutoff=0.8)
    return r[1] if r else None


def _own_ability(p: dict, species_id: str, is_mega: bool = False,
                 item_id: str = "") -> Optional[str]:
    """自分のポケモンの特性ID (状態 -> 型登録 -> 固定特性の順で解決)"""
    if not is_mega:
        if p.get("ability_id"):
            return p["ability_id"]
        # 型登録 (config/my_team.json) の特性 (例: ペリッパー=あめふらし)
        try:
            from advisor.my_team import get_my_build
            b = get_my_build(p.get("species_ja"))
            if b and b.get("ability_ja"):
                aid = _resolve_ability_id(b["ability_ja"])
                if aid:
                    return aid
        except Exception:
            pass
    try:
        from vision.abilities import fixed_ability
        return fixed_ability(species_id, is_mega=is_mega, item_id=item_id)
    except Exception:
        return None


def _weather_synergy_bonus(members: list) -> float:
    """選出3体の天候シナジー: 設置役がいれば恩恵役1体ごとにボーナス。

    members: [{"ability": ..., ...}] (メガ枠はメガ後特性で渡すこと)
    """
    weathers = {_WEATHER_SETTERS[m["ability"]] for m in members
                if m.get("ability") in _WEATHER_SETTERS}
    if not weathers:
        return 0.0
    bonus = 0.0
    for m in members:
        ab = m.get("ability")
        if ab in _WEATHER_ABUSERS and _WEATHER_ABUSERS[ab] in weathers:
            bonus += WEATHER_SYNERGY_BONUS
    return bonus


def _make_view(species_id: str) -> Optional[MonView]:
    dex = get_dex()
    sp = dex.species(species_id)
    if sp is None:
        return None
    return MonView(species_id=species_id, types=sp["types"],
                   base=sp["baseStats"], ev=dict(OFFENSIVE_EV))


def _predicted_attack_moves(species_id: str, limit: int = 6) -> list:
    """使用率DBから予測される攻撃技ID (採用率順)"""
    dex = get_dex()
    out = []
    for mid, pct in get_predictor().predict(species_id)["moves"]:
        mv = dex.move(mid)
        if mv and mv["category"] != "Status" and mv["power"]:
            out.append((mid, pct))
        if len(out) >= limit:
            break
    return out


def _best_damage_pct(attacker: MonView, atk_moves: list, defender: MonView) -> float:
    """予測技の中で最も通る技の期待ダメージ% (命中込み)"""
    dex = get_dex()
    best = 0.0
    for mid, _pct in atk_moves:
        mv = dex.move(mid)
        d = calc_damage(attacker, defender, mid)
        acc = (mv["accuracy"] or 100) / 100.0 if mv else 1.0
        best = max(best, d["avg"] * acc)
    if best == 0.0 and attacker.types:
        # 予測技が無い場合: タイプ一致の代表打点 (威力90相当) を仮定
        for t in attacker.types:
            eff = dex.effectiveness(t, defender.types)
            best = max(best, 40.0 * eff)
    return best


def _species_matchup_score(my_view: MonView, my_moves: list,
                           cand_view: MonView, cand_moves: list) -> float:
    """自分1体 vs 推測種族1体の対面スコア。

    タイプ相性やダメージ差ではなく「対面で戦った場合の勝敗」
    (撃破ターン数の差+実効素早さの先手権、特性込み) を使う。
    duel_scoreは[-1,1]なので従来スコアと同レンジ。
    """
    from advisor.endgame import duel_score
    s = duel_score(my_view, 1.0, [m for m, _ in my_moves],
                   cand_view, 1.0, [m for m, _ in cand_moves])
    if s is not None:
        return s
    # 双方打点なし (変化技のみ等): ダメージ差フォールバック
    offense = _best_damage_pct(my_view, my_moves, cand_view)
    threat = _best_damage_pct(cand_view, cand_moves, my_view)
    return offense / 100.0 - 0.8 * (threat / 100.0)


def advise_selection(state: dict, resolver=None) -> dict:
    """状態辞書から選出提案を作る。

    戻り値: {"ok": bool, "picked": N, "done": bool,
             "recommend": [{"name","index","lead"}], "reason": str,
             "matrix": [[score]]}
    """
    dex = get_dex()
    ja2en = _ja2en()

    my_party = [p for p in state["player"]["party"][:6]]
    opp_party = [p for p in state["opponent"]["party"][:6]]

    picked = state.get("selection_picked")
    done = picked == 3

    # 自分側: 種族IDのあるものだけ評価対象
    mine = []
    for i, p in enumerate(my_party):
        sid = p.get("species_id")
        sp = dex.species(sid) if sid else None
        if sp is None:
            continue
        my_types = sp["types"]
        is_mega_holder = _is_mega_holder(p)
        item_id = p.get("item_id") or ""
        mega_sid = _mega_species_id(sid, item_id) if is_mega_holder else None
        mine.append({
            "index": i,
            "name": p.get("species_ja") or p.get("display_name") or sid,
            "species_id": sid,
            "types": my_types,
            "profile": _my_attack_profile(sid),
            "picked": p.get("is_picked", False),
            "mega_holder": is_mega_holder,
            # メガ後の姿での評価用 (種族値/タイプ/特性が変わる)
            "mega_sid": mega_sid,
            "ability": _own_ability(p, sid),
            "mega_ability": (_own_ability(p, mega_sid or sid, is_mega=True,
                                          item_id=item_id)
                             if mega_sid else None),
        })

    inference = get_inference()
    opps = []
    for j, p in enumerate(opp_party):
        types_ja = p.get("types") or []
        types_en = [ja2en.get(t, t) for t in types_ja]
        if not types_en and p.get("species_id"):
            sp = dex.species(p["species_id"])
            if sp:
                types_en = sp["types"]
        if not types_en:
            continue
        # 種族が判明済みならそれを確定候補に、未判明ならタイプから使用率推測
        if p.get("species_id") and dex.species(p["species_id"]):
            from advisor.infer import species_ja_name
            cands = [(p["species_id"], 1.0,
                      p.get("species_ja") or species_ja_name(p["species_id"]))]
        else:
            cands = inference.candidates(types_ja)
        label = p.get("species_ja") or "/".join(types_ja)
        opps.append({"index": j, "types": types_en, "label": label,
                     "types_ja": types_ja, "candidates": cands})

    if len(mine) < 3 or not opps:
        return {"ok": False, "picked": picked, "done": done,
                "reason": "選出評価に必要な情報が不足しています "
                          f"(自分{len(mine)}/6体, 相手{len(opps)}/6枠)"}

    # --- 候補種族ビュー/予測技のキャッシュ ---
    view_cache: dict = {}
    moves_cache: dict = {}

    def get_view(sid):
        if sid not in view_cache:
            view_cache[sid] = _make_view(sid)
            moves_cache[sid] = _predicted_attack_moves(sid) if view_cache[sid] else []
        return view_cache[sid], moves_cache[sid]

    # スコア行列: 候補種族分布で加重したダメージ計算ベース。
    # メガストーン持ちはメガ後の姿 (種族値/タイプ) でも行を作る
    def score_row(eval_sid, m):
        my_view, my_moves = get_view(eval_sid)
        row = {}
        for o in opps:
            score = None
            if my_view is not None and o["candidates"]:
                acc = 0.0
                total_p = 0.0
                for sid, prob, _ja in o["candidates"]:
                    cand_view, cand_moves = get_view(sid)
                    if cand_view is None:
                        continue
                    acc += prob * _species_matchup_score(
                        my_view, my_moves, cand_view, cand_moves)
                    total_p += prob
                if total_p > 0:
                    score = acc / total_p
            if score is None:
                # 候補が推測できない場合はタイプ相性フォールバック
                score = _matchup_score(m["profile"], m["types"], o["types"])
            row[o["index"]] = score
        return row

    matrix = {}         # (my_index, opp_index) -> 素の姿のスコア
    matrix_mega = {}    # (my_index, opp_index) -> メガ後の姿のスコア
    for m in mine:
        row = score_row(m["species_id"], m)
        for j, v in row.items():
            matrix[(m["index"], j)] = v
        if m["mega_sid"]:
            mrow = score_row(m["mega_sid"], m)
            for j, v in mrow.items():
                matrix_mega[(m["index"], j)] = v

    # C(n,3) 総当たり。メガシンカは1試合1回なので、コンボ内のストーン持ち
    # から「誰をメガ枠にするか」も同時に最適化する (メガ枠はメガ後の姿で
    # 評価、他のストーン持ちは持ち物が死ぬのでペナルティ)。
    # 天候シナジー (あめふらし→すいすい等) もコンボ単位で加点する
    def cell(m, j, is_assignee):
        if is_assignee and (m["index"], j) in matrix_mega:
            return matrix_mega[(m["index"], j)]
        return matrix[(m["index"], j)]

    best = None
    for combo in combinations(mine, 3):
        holders = [m for m in combo if m["mega_holder"]]
        # メガ割当の候補: ストーン持ちそれぞれ + 割当なし
        for assignee in (holders or [None]):
            coverage = sum(
                max(cell(m, o["index"], m is assignee) for m in combo)
                for o in opps)
            individual = sum(
                sum(cell(m, o["index"], m is assignee) for o in opps) / len(opps)
                for m in combo)
            total = coverage + 0.3 * individual
            # メガ枠以外のストーン持ちは持ち物が死ぬ
            total -= MEGA_DUPLICATE_PENALTY * sum(
                1 for m in holders if m is not assignee)
            # 天候シナジー (メガ枠はメガ後特性で評価: メガラグラージ=すいすい等)
            members = [
                {"ability": (m["mega_ability"] if m is assignee
                             and m["mega_ability"] else m["ability"])}
                for m in combo]
            total += _weather_synergy_bonus(members)
            if best is None or total > best[0]:
                best = (total, combo, assignee)

    _, combo, mega_assignee = best
    # 先発: 平均スコア最大
    lead = max(combo, key=lambda m: sum(matrix[(m["index"], o["index"])]
                                         for o in opps))
    ordered = [lead] + [m for m in combo if m is not lead]

    # 理由文の生成: 各選出が誰に対して有利か
    reasons = []
    for m in ordered:
        best_opps = sorted(opps, key=lambda o: -matrix[(m["index"], o["index"])])[:2]
        vs = "、".join(f"{o['label']}に有利" for o in best_opps
                       if matrix[(m["index"], o["index"])] > 0.15)
        reasons.append(f"{m['name']}" + (f" ({vs})" if vs else ""))

    # 相手の種族推測の表示用データ
    inference_view = []
    for o in opps:
        if o["candidates"] and not (len(o["candidates"]) == 1
                                     and o["candidates"][0][1] >= 1.0):
            cand_txt = "/".join(f"{ja}{int(round(prob * 100))}%"
                                 for _sid, prob, ja in o["candidates"][:3])
            inference_view.append(
                {"types": o["types_ja"], "text": f"{'/'.join(o['types_ja'])}→{cand_txt}"})

    # メガ枠 (最適化で選ばれた個体) を先頭に
    mega_picks = [m["name"] for m in ordered if m["mega_holder"]]
    if mega_assignee is not None and mega_assignee["name"] in mega_picks:
        mega_picks.remove(mega_assignee["name"])
        mega_picks.insert(0, mega_assignee["name"])

    # 天候シナジーの表示用データ
    synergy = None
    members = [{"ability": (m["mega_ability"] if m is mega_assignee
                            and m["mega_ability"] else m["ability"]),
                "name": m["name"]} for m in ordered]
    setters = [m for m in members if m["ability"] in _WEATHER_SETTERS]
    if setters:
        weather = _WEATHER_SETTERS[setters[0]["ability"]]
        abusers = [m["name"] for m in members
                   if _WEATHER_ABUSERS.get(m["ability"]) == weather]
        if abusers:
            synergy = {"weather": weather, "setter": setters[0]["name"],
                       "abusers": abusers}

    return {
        "ok": True,
        "picked": picked,
        "done": done,
        "recommend": [{"index": m["index"], "name": m["name"],
                       "lead": m is lead,
                       "mega_holder": m["mega_holder"],
                       "mega_assign": m is mega_assignee} for m in ordered],
        "reason": " / ".join(reasons),
        "inference": inference_view,
        "mega_picks": mega_picks,
        "synergy": synergy,
    }


def format_selection_advice(advice: dict) -> str:
    if not advice.get("ok"):
        base = f"[選出評価不可] {advice.get('reason')}"
    else:
        rec = advice["recommend"]
        names = " → ".join(("★" if r["lead"] else "") + r["name"] for r in rec)
        base = f"◎ 推奨選出: {names} (★=先発)\n  {advice['reason']}"
        if advice.get("inference"):
            base += "\n  🔍 相手の推測: " + " / ".join(
                i["text"] for i in advice["inference"][:6])
        mega = advice.get("mega_picks") or []
        if len(mega) == 1:
            base += f"\n  ⚡ メガ枠: {mega[0]}"
        elif len(mega) >= 2:
            base += (f"\n  ⚡ メガ枠: {mega[0]} を推奨 "
                     f"(ストーン持ち{len(mega)}体: {'/'.join(mega)}。"
                     "メガシンカは1試合1回、他の持ち物は死にます)")
        syn = advice.get("synergy")
        if syn:
            wj = {"rain": "雨", "sun": "晴れ", "sand": "砂嵐",
                  "snow": "雪"}.get(syn["weather"], syn["weather"])
            base += (f"\n  ☔ {wj}シナジー: {syn['setter']} → "
                     f"{'/'.join(syn['abusers'])}")
    picked = advice.get("picked")
    if advice.get("done"):
        return f"✅ 選出完了 (3/3)\n{base}"
    if picked is not None:
        return f"選出中 ({picked}/3)\n{base}"
    return base
