"""行動評価エンジン。

画面から抽出した BattleStateV2 の状態辞書を受け取り、
1ターン先の期待値 (与ダメージ / 被ダメージ / 行動順 / 交代先の相性) を計算して
行動候補をスコアリングする。探索ベース (ダメージ計算+期待値) のアプローチで、
PokéAgent Challenge 2025 で強化学習を上回った foul-play 系の簡易版に相当する。

相手の型 (技/持ち物/特性) は使用率DB (advisor.sets) から予測する。
"""
from __future__ import annotations

import json
import os
from typing import Optional

from advisor.dex import get_dex, BOOST_MULT
from advisor.damage import MonView, FieldView, _is_grounded, calc_damage
from advisor.sets import get_predictor
from champions_agent.config import RL_BLEND_WEIGHT_DEFAULT

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

# 攻撃しつつ手持ちと入れ替わる交代技。素の交代が最善手のとき、これらが
# 無効化されない限り「同じ交代先に無償で出しつつダメージを稼ぐ」分だけ
# 常に優越する (接続テスト所感 2026-08-11: 素の交代を提案していた)
PIVOT_MOVE_IDS = {"uturn", "voltswitch", "flipturn"}
PIVOT_OVER_SWITCH_BONUS = 2.0   # 最善の交代スコアへの上乗せ (順位を上へ)
# こだわり系の持ち物 (技ロック)。ダメージ計算側は倍率のみ対応しており、
# 「前のターンと別の技を推奨する」実害が出ていた (2026-08-18 接続テスト)
CHOICE_ITEM_IDS = {"choicescarf", "choiceband", "choicespecs"}
# 交代の取り逃しでHPが古い可能性のある控えへの交代スコア減点
# (2026-08-18: ひんしを取り逃した個体が100%のまま交代候補に推奨された)
UNCERTAIN_SWITCH_PENALTY = 15.0
# 「行動前に倒される見込み」(素早さ負け or 相手のKO圏先制技) の局面で、
# 先に動けない技のスコアに掛ける割引。ダメージ期待値はほぼ実現しないが、
# 素早さ推定や相手の交代の可能性があるためゼロにはしない
# (2026-08-18 接続テスト: 常に後手でKO圏なのに非先制の大技を推し続けた。
#  従来の一律-12点では大技の素点に埋もれていた)
ACT_BEFORE_KO_DISCOUNT = 0.25
# KO圏 (最小ダメージ >= 残HP) では実効ダメージが残HPで頭打ちになり、
# 同じKO圏の技どうしの順位がRLブレンドの揺らぎだけで決まっていた
# (2026-08-20 第5回接続テスト: 残24%のライボルトへ 等倍53〜63% が
#  RL52%で 抜群106〜125% を上回った)。HP誤読や想定外の耐久に備え、
# 余剰ダメージ (突破余裕) を小さく加点して高火力側を上位に保つ
KO_MARGIN_WEIGHT = 0.15
KO_MARGIN_CAP = 20.0
# サイコフィールド中に接地した相手へ撃つ先制技は不発 (2026-09-10)。技スコアはこの値に固定する
PRIORITY_BLOCKED_SCORE = -20.0
# 挑発の文脈加点: 相手の技プール (判明+使用率予測) に占める変化技の
# 重み比率に比例して加点する。素点15固定では受け/起点作りの相手でも
# 攻撃技に埋もれ、RLが78-84%で挑発を推しても順位が上がらなかった
# (2026-08-21 第6回接続テスト: ブラッキー相手に挑発が3位のまま。
#  ユーザーは助言に逆らって挑発を使い、それが正解だった)
TAUNT_STATUS_BONUS_MAX = 35.0

_TYPE_JA2EN = None


def _eff_accuracy(mv: dict, atk_ability, def_ability) -> float:
    """実効命中率。ノーガード (攻守どちらが持っても必中) を反映する。

    チャンピオンズのライチュウ等はノーガード+低命中高威力 (でんじほう/
    きあいだま) が成立する (2026-08-21 第8回: 登録特性ノーガードでも
    命中50として減点していた)。
    """
    if "noguard" in {str(atk_ability or "").lower(),
                     str(def_ability or "").lower()}:
        return 1.0
    return (mv["accuracy"] or 100) / 100.0


def fainted_allies_of(side_state: dict, self_index: Optional[int]) -> int:
    """その側のひんしの数 (自分 self_index を除く。純粋)。calc_damage の文脈 fainted_allies (そうりょうのつかさ・
    おはかまいり) に渡す。2026-10-07 のダメージ照合で、エンジンが渡していなかったため過小になっていた"""
    # 対応待ちの個体 (相手の party の 7 番目以降、2026-10-09 ④) は数えない (vision.state.roster_slots)
    from vision.state import roster_slots
    return sum(1 for i, p in roster_slots((side_state or {}).get("party"))
               if i != self_index and (p or {}).get("status") == "fainted")


def _side_members(side_state: dict, side: str) -> list:
    """その側の残存メンバーの候補 [(index, 要素)] (純粋)。相手側は vision.state.roster_slots (先頭 6 枠、対応待ちを除く)。
    対応待ちの個体は 6 枠のどれかで追加の個体ではないので入れない (2026-10-09 ④。入れると同じ個体を枠と観測の 2 体として
    数える)。相手の場の個体が対応待ちの間は、呼び出し側 (_run_endgame / _run_search) が評価そのものを保留する"""
    party = (side_state or {}).get("party") or []
    if side != "opponent":
        return list(enumerate(party))
    from vision.state import roster_slots
    return roster_slots(party)


def opp_active_pending(opp_state: Optional[dict]) -> bool:
    """相手の場の個体が対応待ち (6 枠のどれか未定、vision.state の pending) か (純粋。2026-10-09 ④)。
    True の間は、ロスターの対応が要る評価 (終盤評価・探索の相手の控えの列挙) を保留し、場の個体への直接の採点だけを使う"""
    from vision.state import is_pending
    party = (opp_state or {}).get("party") or []
    idx = (opp_state or {}).get("active_index")
    return isinstance(idx, int) and 0 <= idx < len(party) and is_pending(party[idx])


OPP_PENDING_NOTE = "相手の場のポケモンが 6 枠のどれか未定 (対応待ち) のため、終盤評価と探索を保留 (直接の採点のみ)"


def opp_pending_note(opp_state: Optional[dict]) -> Optional[str]:
    """対応待ちの注記 (純粋)。場の個体に候補の枠 (vision.state.SideState.pending_hint。タイプが 1 個だけ重なる枠が 1 つ) が
    あれば「候補: 枠 N (種)」を足す (2026-10-09 段 2。自動では統合しない。画面の相手欄で人が枠を指定する)。枠の番号は 1 始まり"""
    if not opp_active_pending(opp_state):
        return None
    party = (opp_state or {}).get("party") or []
    hint = party[opp_state["active_index"]].get("pending_hint") or []
    if not hint:
        return OPP_PENDING_NOTE
    parts = [f"枠 {h['slot'] + 1} ({h.get('ja') or '/'.join(h.get('types') or []) or '未特定'}"
             f"{'、様子見画面の弱い読み' if h.get('source') == 'watch_loose' else ''})" for h in hint]
    return f"{OPP_PENDING_NOTE}。候補: {'、'.join(parts)} (画面の相手欄で枠を指定すると確定)"


def has_acted_since_entry(last_move_player: Optional[str], move_ids) -> bool:
    """自分の場のポケモンが場に出てから技を使ったか (純粋)。

    state["last_move"]["player"] は自分側が技を使うと入り、交代・ひんしで消える (vision.events)。
    入っていて、それが今の場のポケモンの技 (move_ids) のどれかなら「使った」とする。技の欄に無い技名
    (交代の取り逃しで前の個体の技が残っている等) や未記録は「分からない」として False を返す
    (推測で選べなくしない)。であいがしら・ねこだまし の初手制限に使う (2026-10-07 ダメージ照合の合法手 122/124)
    """
    if not last_move_player:
        return False
    return last_move_player in {m for m in (move_ids or []) if m}


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
# 多世界探索 (P7、事前登録 2026-09-04 19:40): 相手型の仮説分布の重み上位
# BELIEF_K 仮説それぞれで探索し、仮説重みで統合する。0 で点推定 (従来)。
# BELIEF_MIN_WEIGHT 未満の仮説は刈り込む (レイテンシ対策)。
# 2026-09-04 22:35 判定で棄却し 0 に戻した: 探索プレイヤー各300戦・同一相手列で
# 単一仮定 0.413 / 最尤仮説 0.367 / 多世界K=8 0.380 (belief−current −0.033±0.036)。
# 実装は残置 (advice_replay --belief-k / check_search_expert --belief で再測定可)
BELIEF_K = 0
BELIEF_MIN_WEIGHT = 0.02
# センサ世界 (P8): 自分アクティブの表示HPが固着している確率 q と、その世界での
# HP低下幅。hp_uncertain (交代取り逃し等) が立った個体は高い q を使う。
# 既定 0 = 無効 (P8 の事前登録判定で採用されるまで助言経路は変えない)
SENSOR_Q_DEFAULT = 0.0
SENSOR_Q_UNCERTAIN = 0.0
SENSOR_HP_DELTA = 0.25
# 世界の並列実行数 (P7 レイテンシ条項の履行手段)。葉評価 (RL価値) を使う間は
# 子プロセスへ渡せないため逐次に落ちる。1=逐次
SEARCH_WORKERS = 1
# 探索の推奨値を行動スコアへ統合する重み (P9、事前登録 2026-09-04 20:30)。
# score += SEARCH_BLEND × (rec_a − max_rec)。0 で無効 (従来: 択評価は表示のみ)
SEARCH_BLEND = 0.0


def _rl_blend_default_from_env() -> float:
    """RL 加点の重みの既定値 (起動時に 1 回だけ読む)。

    既定は config の RL_BLEND_WEIGHT_DEFAULT。後方互換のため環境変数 RL_BLEND_WEIGHT があれば
    それを既定値の供給源として使う (tests/test_advisor が import 前に 0 を setdefault する、
    測定の子プロセスが環境変数を引き継ぐ)。2026-10-07 (影の計算の隔離条件 (a)): 以前は評価のたびに
    関数内で os.environ を読んでいたため、別スレッドから重みを変えると本番の助言まで変わり得た。
    今は重みを evaluate(rl_blend_weight=...) の引数で渡し、環境変数は起動時の既定値にしか効かない。
    数値として読めない値は警告して config の既定値を使う。
    """
    raw = os.environ.get("RL_BLEND_WEIGHT")
    if raw is None or not str(raw).strip():
        return float(RL_BLEND_WEIGHT_DEFAULT)
    try:
        return float(raw)
    except ValueError:
        print(f"[engine] RL_BLEND_WEIGHT={raw!r} は数値でないため既定値 "
              f"{RL_BLEND_WEIGHT_DEFAULT} を使う")
        return float(RL_BLEND_WEIGHT_DEFAULT)


# RL 加点の重みの既定値 (evaluate の rl_blend_weight が None のとき)。import 時に 1 回決まる
RL_BLEND_DEFAULT = _rl_blend_default_from_env()


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
    """実効素早さ。すいすい/ようりょくそ等の特性は damage.effective_speed に集約"""
    from advisor.damage import effective_speed as _es
    fv = FieldView(weather=(field or {}).get("weather"),
                   terrain=(field or {}).get("terrain"))
    spe = _es(mon, fv)
    if side.get("tailwind"):
        spe *= 2
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
    # 予測特性/持ち物を反映 (はたきおとす被弾後は持ち物を予測で復活させない)
    if not opp_view.ability and pred["abilities"]:
        opp_view.ability = pred["abilities"][0][0]
    if not opp_view.item and not opp_state.get("item_removed") \
            and pred["items"]:
        opp_view.item = pred["items"][0][0]
    return list(pool.items())


# ==============================================================================
# メインの評価
# ==============================================================================
def evaluate(state: dict, resolver=None,
             rl_blend_weight: Optional[float] = None) -> dict:
    """状態辞書から行動候補のランキングを生成する。

    rl_blend_weight: RL の行動確率の加点の重み (score += 重み × 確率)。None なら既定値
    (RL_BLEND_DEFAULT = config RL_BLEND_WEIGHT_DEFAULT、起動時の環境変数 RL_BLEND_WEIGHT があればその値)。
    採点は 2 段: evaluate_common (加点より前の共通部分) → finish_evaluation (加点以降)。
    影の計算 (advisor.shadow) は共通部分を使い回し、重みだけ変えて加点以降を再計算する。
    """
    return finish_evaluation(evaluate_common(state, resolver), rl_blend_weight)


def evaluate_common(state: dict, resolver=None) -> dict:
    """採点の共通部分 (RL 加点より前): ダメージ・採点の基礎・詰み筋・同時手番探索・SEARCH_BLEND・rl_hint の取得まで。

    戻り値: 評価できなければ {"ok": False, "reason"} (evaluate の戻り値と同じ)。評価できれば
    {"ok": True, "actions": 加点前の行動 (並べ替え済み), "rl_hint", "move_type_mult", 表示用の注記, 後段の判定に使う旗,
     "context": 影の計算の層別用の局面の要約} — finish_evaluation の入力。
    """
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
                if not opp_view.item and not opp_p.get("item_removed") \
                        and guess["item"]:
                    opp_view.item = guess["item"]
                opp_spread_note = f"相手の型推定: {guess['summary']}"
            # 先後観測の実効素早さ範囲は確度に関係なく反映する
            # (探索/詰み筋/RLの先手判定が観測と矛盾しないように)
            if guess and (guess.get("spe_lower") or guess.get("spe_upper")):
                opp_view.spe_bounds = (guess.get("spe_lower"),
                                       guess.get("spe_upper"))
        except Exception:
            pass

    my_field = build_field_view(state, "player")
    opp_field = build_field_view(state, "opponent")

    actions = []
    threats = []
    speed_note = ""
    # ダメージ計算の盤面の文脈: 味方のひんしの数 (そうりょうのつかさ・おはかまいり)
    my_ctx = {"fainted_allies": fainted_allies_of(my_state, my_active_idx)}
    opp_ctx = {"fainted_allies": fainted_allies_of(opp_state, opp_active_idx)}
    opp_status_ratio = 0.0   # 相手の技プールに占める変化技の重み比率
    opp_taunted = False      # 相手が挑発中 (重ねる価値なし)

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
        # 相手がアンコール中で直前技が判明していれば、脅威はその技のみになる
        opp_vols_ = {str(v).lower() for v in (opp_p.get("volatiles") or [])}
        opp_locked = (state.get("last_move") or {}).get("opponent")
        if "encore" in opp_vols_ and opp_locked and \
                any(m == opp_locked for m, _ in pool):
            pool = [(opp_locked, 100.0)]
        for mid, weight in pool:
            mv = dex.move(mid)
            if not mv:
                continue
            d = calc_damage(opp_view, my_view, mid, opp_field, ctx=opp_ctx)
            acc = _eff_accuracy(mv, opp_view.ability, my_view.ability)
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
        # 相手の変化技傾向 (挑発の価値算定用)。プール重みは判明技=100、
        # 未判明は使用率×減衰なので、そのまま傾向の重み付き比率になる
        tot_w = stat_w = 0.0
        for mid, w in pool:
            mv_ = dex.move(mid)
            if not mv_:
                continue
            tot_w += w
            if mv_["category"] == "Status" or not mv_["power"]:
                stat_w += w
        opp_status_ratio = (stat_w / tot_w) if tot_w > 0 else 0.0
        opp_taunted = "taunt" in opp_vols_

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

    # 相手の先制技: KO圏の先制技を持つ相手には、素早さで勝っていても
    # 「先に殴られる」前提で評価する (かげうち/ふいうち/しんそく等)
    opp_priority_threat = 0     # KO圏の先制技の最大優先度
    # サイコフィールド: 接地している側への先制技 (優先度 > 0) は不発。相手→自分、自分→相手の両方向で見る
    psychic_terrain = (state["field"].get("terrain") == "psychic")
    my_grounded = _is_grounded(my_view)
    opp_grounded = _is_grounded(opp_view) if opp_view is not None else True
    for t in threats:
        mv_t = dex.move(t["move_id"])
        pri_t = (mv_t or {}).get("priority") or 0
        if pri_t > 0 and psychic_terrain and my_grounded:
            continue
        if pri_t > 0 and t["dmg_max"] >= my_hp_pct * 0.95:
            opp_priority_threat = max(opp_priority_threat, pri_t)
    if psychic_terrain and my_grounded:
        speed_note = "サイコフィールド中: 接地している自分への先制技は不発。" + speed_note
    # 「相手の攻撃が自分より先に来る」状況 (素早さ負け or 先制技KO圏)
    threat_faces_me = threat_ko and (i_am_faster is False
                                     or opp_priority_threat > 0)
    if opp_priority_threat > 0 and i_am_faster:
        speed_note = (f"⚠相手はKO圏の先制技持ち (優先度+{opp_priority_threat})。"
                      "素早さで勝っていても先に殴られる想定。" + speed_note)

    # ------------------------------------------------------------------
    # 自分の技の評価
    # ------------------------------------------------------------------
    # わざふうじ状態: ちょうはつ=変化技不可 / かなしばり=特定技不可 /
    # アンコール=直前の技以外不可 (直前技はイベントの last_move で解決。
    # 未判明の場合のみ従来どおり注記に留める)
    my_vols = {str(v).lower() for v in (my_p.get("volatiles") or [])}
    disabled_ids = {v.split("_", 1)[1] for v in my_vols
                    if v.startswith("disable_")}
    encore_locked = None
    if "encore" in my_vols:
        encore_locked = (state.get("last_move") or {}).get("player")
    # こだわりロック: こだわり系を持って技を使った後は、交代するまで
    # その技しか選べない (last_move は交代・ひんしでクリアされる)
    choice_locked = None
    if my_view.item in CHOICE_ITEM_IDS:
        choice_locked = (state.get("last_move") or {}).get("player")
    can_ko_first = False   # 先に動いて倒せる技があるか (死に出し判定用)
    move_type_mult = {}    # 攻撃技のタイプ相性倍率 (交代技の無効判定用)
    # 自分の場のポケモンがひんしなら、この決定は「次を出す」しかない。
    # 技を評価すると倒れた個体の技を推奨し続ける (2026-08-18 接続テスト:
    # 瀕死のムクホークのブレイブバードを推奨し続けた)
    my_fainted = (my_p.get("status") == "fainted"
                  or (my_p.get("hp_percent") is not None
                      and my_p["hp_percent"] <= 0))
    # とんぼがえり系の使用後は「交代先を選ぶ」決定 (2026-08-21 第8回:
    # 交代先選択の場面で技トップの助言が出て役に立たなかった)。
    # フラグは vision 側 (events) が技使用で立て、交代完了/次ターンで下ろす
    pivot_pending = bool(state.get("pending_pivot_switch")) and not my_fainted
    switch_only = my_fainted or pivot_pending
    # 技が画面から未読取でも、my_team登録の型があれば登録技で評価する
    # (2026-08-18 接続テスト: 技選択画面を開くまで自分の技が不明のまま
    #  助言していた。登録技はPP不明のまま扱う。画面読取が入れば上書きされる)
    my_move_slots = list(my_p.get("moves") or [])
    if not my_move_slots and not switch_only and resolver is not None:
        try:
            from advisor.my_team import get_my_moves
            for ja in get_my_moves(my_p.get("species_ja")):
                mv_r = resolver.resolve(ja, "moves", cutoff=0.7)
                if mv_r:
                    my_move_slots.append(
                        {"name_ja": mv_r[0], "move_id": mv_r[1], "pp": None})
        except Exception:
            pass
    # であいがしら・ねこだまし (場に出た最初の行動でしか選べない) の制限: 場に出てから技を使ったか
    acted_since_entry = has_acted_since_entry(
        (state.get("last_move") or {}).get("player"),
        [s.get("move_id") for s in my_move_slots])
    from advisor import effects as _E
    for slot in ([] if switch_only else my_move_slots):
        mid = slot.get("move_id")
        mv = dex.move(mid)
        name = slot.get("name_ja") or mid or "不明な技"
        if mv is None:
            continue
        if slot.get("pp") == 0:
            continue
        if acted_since_entry and _E.first_turn_only(_E.move_entry(mid)):
            actions.append({"kind": "move", "id": mid, "name": name,
                            "score": -99.0,
                            "reason": "場に出た最初のターンしか選べない"})
            continue
        if mid in disabled_ids:
            actions.append({"kind": "move", "id": mid, "name": name,
                            "score": -99.0,
                            "reason": "かなしばりで選べない"})
            continue
        if encore_locked and mid != encore_locked:
            actions.append({"kind": "move", "id": mid, "name": name,
                            "score": -99.0,
                            "reason": "アンコール中は直前の技しか選べない"})
            continue
        if choice_locked and mid != choice_locked:
            actions.append({"kind": "move", "id": mid, "name": name,
                            "score": -99.0,
                            "reason": "こだわり中は直前の技しか選べない (交代で解除)"})
            continue
        if "taunt" in my_vols and (mv["category"] == "Status"
                                   or not mv["power"]):
            actions.append({"kind": "move", "id": mid, "name": name,
                            "score": -99.0,
                            "reason": "ちょうはつ中は変化技を選べない"})
            continue

        acc = _eff_accuracy(
            mv, my_view.ability,
            opp_view.ability if opp_view is not None else None)
        reason_parts = []
        score = 0.0
        act_discount = False   # 行動前に倒される見込み (RLブレンド後に割引)

        if mv["category"] == "Status" or not mv["power"]:
            desc, base_score = STATUS_MOVE_VALUE.get(mid, ("補助技", 8))
            score = float(base_score)
            reason_parts.append(desc)
            if mid in RECOVERY_MOVES:
                missing = 100.0 - my_hp_pct
                score = min(45.0, missing * 0.6)
                reason_parts.append(f"残りHP{my_hp_pct:.0f}%からの回復")
            if mid == "taunt":
                if opp_taunted:
                    score = 4.0
                    reason_parts.append("相手は挑発中 (重ねても効果なし)")
                elif opp_status_ratio > 0:
                    score += TAUNT_STATUS_BONUS_MAX * opp_status_ratio
                    if opp_status_ratio >= 0.3:
                        reason_parts.append(
                            f"相手は変化技主体の見込み ({opp_status_ratio:.0%})"
                            " — 回復/積み/設置を止められる")
            if mid == "stealthrock" and opp_state["hazards"]["stealth_rock"]:
                score = 2.0
                reason_parts.append("既に設置済み")
            # 重ねられない設置技・場効果は既設置なら推奨しない (2026-08-31
            # 第11回: ねばねばネット設置済みのまま再設置を推奨し続けた)
            if mid == "stickyweb" and opp_state["hazards"].get("sticky_web"):
                score = 2.0
                reason_parts.append("既に設置済み")
            if mid == "spikes" and \
                    opp_state["hazards"].get("spikes", 0) >= 3:
                score = 2.0
                reason_parts.append("まきびしは上限まで設置済み")
            if mid == "toxicspikes" and \
                    opp_state["hazards"].get("toxic_spikes", 0) >= 2:
                score = 2.0
                reason_parts.append("どくびしは上限まで設置済み")
            _scr = my_state.get("screens") or {}
            if (mid == "reflect" and _scr.get("reflect")) or \
                    (mid == "lightscreen" and _scr.get("light_screen")) or \
                    (mid == "auroraveil" and _scr.get("aurora_veil")):
                score = 2.0
                reason_parts.append("展開中 (重ねられない)")
            if mid == "tailwind" and my_state.get("tailwind"):
                score = 2.0
                reason_parts.append("おいかぜは既に吹いている")
            if mid == "protect" and threat_faces_me:
                score += 10
                reason_parts.append("高火力を一度受け流せる")
            if threat_faces_me and mid not in ("protect",):
                score -= 15
                reason_parts.append("相手の攻撃で倒される危険あり"
                                    if i_am_faster is False else
                                    "相手の先制技で倒される危険あり")
        else:
            override = None
            if opp_view is None:
                override = HINT_MULT.get(slot.get("effectiveness")) \
                    if slot.get("effectiveness") else 1.0
            if opp_view is not None:
                d = calc_damage(my_view, opp_view, mid, my_field, ctx=my_ctx)
            else:
                dummy = MonView(species_id="", types=["Normal"],
                                base={"hp": 80, "atk": 80, "def": 80,
                                      "spa": 80, "spd": 80, "spe": 80},
                                ev=dict(OFFENSIVE_EV))
                d = calc_damage(my_view, dummy, mid, my_field,
                                override_type_mult=override, ctx=my_ctx)
            exp = d["avg"] * acc
            move_type_mult[mid] = d["type_mult"]

            ko_prob = 0.0
            if d["min"] >= opp_hp_pct:
                ko_prob = 1.0
            elif d["max"] >= opp_hp_pct:
                ko_prob = 0.5
            ko_prob *= acc

            effective = min(exp, opp_hp_pct)
            score = effective + ko_prob * 40.0
            if ko_prob > 0:
                # 同じKO圏なら突破余裕 (余剰ダメージ) の大きい技を上位に
                score += min(KO_MARGIN_CAP,
                             KO_MARGIN_WEIGHT * max(0.0, exp - opp_hp_pct))
            mv_pri = mv["priority"] or 0
            if mv_pri > 0 and psychic_terrain and opp_grounded:
                # サイコフィールド中に接地した相手へ撃つ先制技は不発: ダメージも先手も無い
                exp, ko_prob, mv_pri = 0.0, 0.0, 0
                score = PRIORITY_BLOCKED_SCORE
                reason_parts.append("サイコフィールド中は接地した相手への先制技が不発")
            # この技での実効先手: 優先度が相手のKO圏先制技を上回れば
            # 素早さ不問で先に動ける (しんそく+2 > かげうち+1 の撃ち合い等)
            strikes_first = (mv_pri > opp_priority_threat) or \
                (mv_pri == opp_priority_threat and bool(i_am_faster))
            if mv_pri > 0:
                score += 6.0
                if threat_ko:
                    score += 14.0
                    reason_parts.append("先制技: 倒される前に削れる")
            elif mv_pri < 0:
                reason_parts.append("後攻技 (必ず相手が先に動く)")
                if threat_ko:
                    score -= 10.0
            if strikes_first and ko_prob >= 0.5:
                score += 25.0
                can_ko_first = True
                reason_parts.append("先制技で先に倒せる見込み"
                                    if mv_pri > 0 and not i_am_faster
                                    else "先手で倒せる見込み")
            elif ko_prob >= 0.5:
                reason_parts.append(
                    "倒せる圏内だが相手の先制技が先の可能性"
                    if mv_pri > 0 else "倒せる圏内だが相手が先手の可能性")
            if threat_faces_me and not strikes_first:
                # 先に動けない技は撃つ前に倒される見込みが高い。割引は
                # RLブレンド後にまとめて適用する (act_discount フラグ)。
                # 技スコアだけ割り引くと、修正前の方策で学習したRL事前分布が
                # 「実行されない技」を上位へ復活させる
                act_discount = True
                reason_parts.append("行動前に倒される見込み (先制技/交代を優先)")
            if mid in ("suckerpunch", "thunderclap"):
                reason_parts.append("相手が攻撃技以外 (交代/変化技) だと失敗")

            eff_txt = {4.0: "抜群(4倍)", 2.0: "抜群", 1.0: "等倍", 0.5: "いまひとつ",
                       0.25: "いまひとつ(1/4)", 0.0: "無効"}.get(d["type_mult"], "")
            dmg_txt = f"予測ダメージ {d['min']:.0f}〜{d['max']:.0f}%"
            if acc < 1.0:
                dmg_txt += f" (命中{int(acc * 100)})"
            reason_parts.insert(0, f"{dmg_txt} {eff_txt}")
            reason_parts += d["notes"]

        action = {
            "kind": "move",
            "id": mid,
            "name": name,
            "score": round(score, 1),
            "reason": " / ".join(reason_parts),
        }
        if act_discount:
            action["act_discount"] = True
        actions.append(action)

    # ------------------------------------------------------------------
    # 交代の評価 (選出済みの3体に限る: 未選出への交代は提案できない)
    # ------------------------------------------------------------------
    from advisor.party import battle_party_indices
    _allowed = battle_party_indices(my_state)
    for i, p in enumerate(my_state["party"]):
        if i == my_active_idx or p.get("status") == "fainted":
            continue
        if _allowed is not None and i not in _allowed:
            continue
        cand = build_mon_view(p, resolver, side="player")
        if cand is None or opp_view is None:
            continue

        incoming = 0.0
        if opp_best_move:
            d_in = calc_damage(opp_view, cand, opp_best_move, opp_field, ctx=opp_ctx)
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
                d_out = calc_damage(cand, opp_view, g, my_field,
                                    ctx={"fainted_allies": fainted_allies_of(my_state, i)})
                counter = max(counter, d_out["avg"])

        cand_hp_pct = cand.hp_frac * 100.0
        survives = (incoming + hazard_dmg) < cand_hp_pct * 0.9
        score = (counter * 0.45) - (incoming * 0.5) - hazard_dmg * 0.8
        if survives:
            score += 20.0
        if threat_faces_me:
            score += 10.0  # 居座りが危険 (素早さ負け or 先制技KO圏) なら交代の価値が上がる

        reason = (f"被ダメ予測 {incoming:.0f}%"
                  + (f" + 設置技 {hazard_dmg:.0f}%" if hazard_dmg else "")
                  + f" / 交代後の打点 約{counter:.0f}%")
        if not survives:
            reason += " / 交代出しで倒される危険あり"
        if p.get("hp_uncertain"):
            # 交代を見逃した個体はHPが古い (ひんし済みの可能性すらある)
            score -= UNCERTAIN_SWITCH_PENALTY
            reason += " / ⚠HP不明 (交代の見逃しあり。実際は瀕死の可能性)"

        actions.append({
            "kind": "switch",
            "id": p.get("species_id") or f"slot{i}",
            "name": p.get("species_ja") or p.get("display_name") or f"{i}番",
            "score": round(score, 1),
            "reason": reason,
            # 死に出し評価用の内訳 (無償降臨なら incoming を受けずに着地)
            "counter": round(counter, 1),
            "incoming": round(incoming, 1),
            "hazard": round(hazard_dmg, 1),
        })

    actions.sort(key=lambda a: -a["score"])

    # メガシンカ可能なら、切った場合の打点/耐久差を定量評価して提案する
    mega_note = ""
    if (not state.get("mega_used", {}).get("player")
            and (my_p.get("item_id") == "megastone"
                 or (my_p.get("item_ja") or "").endswith(("ナイトX", "ナイトY", "ナイトZ", "ナイト")))):
        mega_note = "メガシンカが可能です (種族値+100)。攻撃するターンにメガシンカを推奨。"
        try:
            mega_note = _mega_timing_note(my_p, my_view, opp_view, my_field,
                                          resolver) or mega_note
        except Exception:
            pass
    # 相手の場のポケモンがこの先メガシンカする見込み (使用率 DB の石の使用率 + 判明情報)
    try:
        opp_forms = _opp_mega_forms(opp_state["party"][opp_state["active_index"]], state)
        if opp_forms:
            from advisor.infer import species_ja_name
            parts = ", ".join(f"{species_ja_name(k)} {v:.0%}" for k, v in sorted(opp_forms.items(), key=lambda kv: -kv[1]))
            mega_note = (mega_note + " / " if mega_note else "") + f"相手もメガシンカ未使用: {parts} の見込み"
    except Exception:
        pass

    # 詰み筋・勝ち筋判定 (残存メンバーの1v1マッチアップ行列)。
    # 探索より先に計算し、勝ち筋の温存を探索の葉評価へ渡す (定説H3)
    endgame = ""
    wincon_sid = None
    opp_pending = opp_active_pending(opp_state)
    try:
        endgame = _run_endgame(my_state, opp_state, resolver)
        import re as _re
        m = _re.search(r"勝ち筋:\s*(\S+)\s*が", endgame or "")
        if m:
            wincon_sid = next(
                (p.get("species_id") for p in my_state.get("party", [])
                 if p.get("species_ja") == m.group(1)), None)
    except Exception:
        pass

    # 同時手番探索 (択の利得行列 + 2手読み)。失敗しても本体は返す
    gtheory = None
    try:
        gtheory = _run_search(state, my_state, my_view, my_p,
                              opp_state, opp_view, resolver,
                              pool, my_field, opp_field,
                              wincon_sid=wincon_sid)
    except Exception:
        import traceback
        traceback.print_exc()

    # P9: 探索の推奨値 (最善からの差) をスコアへ統合する。既定 0 で無効
    if SEARCH_BLEND > 0 and gtheory and gtheory.get("actions"):
        _apply_search_blend(actions, gtheory["actions"], SEARCH_BLEND)
        actions.sort(key=lambda a: -a["score"])

    # RL学習済み方策 (行動分布+局面価値)。表示に加えて、
    # 行動スコアへ確率をブレンドし推奨順位にも反映する (加点は finish_evaluation → rescore_actions)
    rl_hint = None
    try:
        from advisor.rl_bridge import policy_hint
        from advisor.damage import effective_speed as _es2
        # 画面から技が未読取のフレームでは、RLにも登録技フォールバックを
        # 見せる (state直参照のままだと合法手が「交代のみ」に縮退し、
        # RL確率が交代に集中→技画面を開いた瞬間に推奨が反転した。
        # 2026-08-18 第3回: command画面 交代RL100% → move_select 剣舞RL70%)
        rl_state = state
        if my_move_slots and not (my_p.get("moves") or []):
            rl_party = list(my_state["party"])
            rl_party[my_active_idx] = dict(my_p, moves=my_move_slots)
            rl_state = dict(state,
                            player=dict(my_state, party=rl_party))
        rl_hint = policy_hint(rl_state, my_spe_actual=_es2(my_view, my_field))
    except Exception:
        pass

    return {
        "ok": True,
        "actions": actions,
        "rl_hint": rl_hint,
        "move_type_mult": move_type_mult,
        "threats": threats,
        "speed_note": speed_note,
        "mega_note": mega_note,
        "opp_inference": opp_inference_note,
        "opp_moves_note": opp_moves_note,
        "opp_spread_note": opp_spread_note,
        "gtheory": gtheory,
        "endgame": endgame,
        # 相手の場の個体が対応待ち: 終盤評価・探索を保留した印 (advice 行にそのまま残る。2026-10-09 ④)
        "opp_pending": opp_pending,
        "opp_pending_note": opp_pending_note(opp_state) if opp_pending else None,
        "encore": "encore" in my_vols,
        "encore_locked": encore_locked,
        "choice_locked": choice_locked,
        "threat_faces_me": threat_faces_me,
        "can_ko_first": can_ko_first,
        "my_fainted": my_fainted,
        "pivot_pending": pivot_pending,
        # 影の計算 (advice_variant) の層別用 (攻め / 受け / 残り体数)。表示の助言には出さない
        "context": {
            "turn": state.get("turn"),
            "my_hp_pct": round(my_hp_pct, 1),
            "opp_hp_pct": round(opp_hp_pct, 1),
            "i_am_faster": i_am_faster,
            "threat_ko": threat_ko,
            "threat_faces_me": threat_faces_me,
            "can_ko_first": can_ko_first,
            "switch_only": switch_only,
            "my_remaining": my_state.get("remaining"),
            "opp_remaining": opp_state.get("remaining"),
        },
    }


def _rl_probs(rl_hint: Optional[dict]) -> dict:
    """RL の行動分布 (rl_hint["top"]) → {ラベル: 確率} (純粋)。

    「技名+メガ」は技名側にも最大値で寄せる (素の技の確率で上書きしない。2026-09-12 修正:
    上書きのせいで「たきのぼり+メガ 100% / たきのぼり 0%」が技側 0% になりブレンドが消えていた)
    """
    probs: dict = {}
    for t in (rl_hint or {}).get("top") or []:
        base_label = t["label"].replace("+メガ", "")
        probs[base_label] = max(probs.get(base_label, 0.0), t["prob"])
        if t["label"] != base_label:
            probs[t["label"]] = max(probs.get(t["label"], 0.0), t["prob"])
    return probs


def rescore_actions(actions: list, rl_hint: Optional[dict], move_type_mult: dict,
                    rl_blend_weight: float) -> list:
    """加点以降の採点 (純粋関数): RL 加点 → KO 前割引 → 交代技の複合価値の補正 → 並べ替え。

    actions は evaluate_common の加点前の行動 (並べ替え済み)。入力は書き換えず、行動の辞書を複製して返す
    (影の計算が同じ共通部分から重みだけ変えて何度でも呼べるように)。2026-10-07 に evaluate から切り出した。
    中身 (順序・丸め・例外の扱い) は切り出す前と同じ。
    """
    actions = [dict(a) for a in actions]
    try:
        if rl_hint and rl_hint.get("top"):
            probs = _rl_probs(rl_hint)
            for a in actions:
                if a["score"] <= -90:
                    continue   # わざふうじ等で選べない行動はブレンドしない
                key = a["name"] if a["kind"] == "move" else f"交代:{a['name']}"
                p = probs.get(key)
                if p is not None:
                    a["score"] = round(a["score"] + rl_blend_weight * p, 1)
                    a["reason"] = (a.get("reason") or "") + f" / RL{p:.0%}"
            actions.sort(key=lambda a: -a["score"])
    except Exception:
        pass

    # 行動前に倒される見込みの技の割引 (RL補正も含めて掛ける)。
    # RL方策は「先に動けない」文脈を学習しきれておらず、技スコアだけ
    # 割り引くとRL事前分布が実行されない技を上位へ復活させるため、
    # ブレンド後の合計に対して適用する。割引した技の id は後段の補正
    # (交代技の複合価値) が「実行できない技」を持ち上げないように覚えておく
    ko_before_act_ids = set()
    for a in actions:
        if a.pop("act_discount", False):
            a["score"] = round(a["score"] * ACT_BEFORE_KO_DISCOUNT, 1)
            ko_before_act_ids.add(a.get("id"))
    actions.sort(key=lambda a: -a["score"])

    # 交代技の複合価値: 素の交代が最善のとき、無効化されない交代技があれば
    # 「同じ交代を実現しつつダメージも入る」分だけ交代より優先する。
    # 無効相性 (ボルトチェンジ→地面等) は交代自体が発生しないため対象外。
    # 行動前に倒される見込みの技 (上の割引) も対象外: 交代技は自分の行動順に
    # 発動するので、先に倒されれば交代もダメージも起きない。素の交代は
    # 相手の攻撃より先に成立するため、その状況では素の交代の方が安全
    # (2026-10-05: この補正が割引を上書きし、ハッサム vs リザードンで
    # 先に倒される とんぼがえり 53 点 > 交代 51 点 になっていた)
    try:
        if actions and actions[0]["kind"] == "switch":
            best_switch = actions[0]
            for a in actions:
                if (a["kind"] == "move" and a["id"] in PIVOT_MOVE_IDS
                        and a["score"] > -90
                        and move_type_mult.get(a["id"], 0) > 0):
                    if a["id"] in ko_before_act_ids:
                        a["reason"] += (" / 先に倒される見込みなので交代技は"
                                        f"発動しない: 素の交代 ({best_switch['name']}) を優先")
                        continue
                    a["score"] = round(
                        best_switch["score"] + PIVOT_OVER_SWITCH_BONUS, 1)
                    a["reason"] += (f" / 交代するならまずこの技: ダメージを"
                                    f"入れつつ {best_switch['name']} に引ける")
            actions.sort(key=lambda a: -a["score"])
    except Exception:
        pass
    return actions


def finish_evaluation(common: dict, rl_blend_weight: Optional[float] = None) -> dict:
    """採点の加点以降 (純粋関数): 共通部分 (evaluate_common の戻り値) と重みから、evaluate の戻り値を作る。

    common は書き換えない。rl_blend_weight が None なら RL_BLEND_DEFAULT。
    """
    if not common.get("ok"):
        return common
    weight = RL_BLEND_DEFAULT if rl_blend_weight is None else float(rl_blend_weight)
    actions = rescore_actions(common["actions"], common.get("rl_hint"),
                              common.get("move_type_mult") or {}, weight)
    speed_note = common["speed_note"]
    encore_locked = common["encore_locked"]
    choice_locked = common["choice_locked"]
    endgame = common["endgame"]
    my_fainted = common["my_fainted"]

    if common["encore"]:
        if encore_locked:
            locked_ja = next((a["name"] for a in actions
                              if a.get("id") == encore_locked), encore_locked)
            speed_note = (f"アンコール中: {locked_ja} しか選べません "
                          "(交代で解除)。" + speed_note)
        else:
            speed_note = ("アンコール中: 直前に使った技しか選べません。" +
                          speed_note)
    if choice_locked:
        locked_ja = next((a["name"] for a in actions
                          if a.get("id") == choice_locked), choice_locked)
        speed_note = (f"こだわり中: {locked_ja} しか選べません (交代で解除)。"
                      + speed_note)

    # 死に出しプランニング (定説: 捨てる順番と無償降臨。HEURISTICS_CATALOG H2)
    # 「相手のKO圏の攻撃が先に来る」かつ「先に倒し返せない」= この場は
    # 確定で落ちる。逃げても交代先が攻撃を受けるだけなので、削ってから
    # 倒され、次を無償で出すプランを明示する。勝ち筋の個体は温存する
    sacrifice_note = ""
    try:
        if common["threat_faces_me"] and not common["can_ko_first"] and not my_fainted:
            sacrifice_note = _sacrifice_note(actions, endgame)
    except Exception:
        pass

    if my_fainted:
        speed_note = ("ひんし: 交代先を選んでください (技は選べません)。"
                      + speed_note)
    elif common["pivot_pending"]:
        speed_note = ("とんぼがえり系の交代先を選ぶ場面です。"
                      + speed_note)

    return {
        "ok": True,
        "actions": actions,
        "threats": common["threats"][:5],
        "speed_note": speed_note,
        "mega_note": common["mega_note"],
        "opp_inference": common["opp_inference"],
        "opp_moves_note": common["opp_moves_note"],
        "opp_spread_note": common["opp_spread_note"],
        "gtheory": common["gtheory"],
        "endgame_note": endgame,
        "sacrifice_note": sacrifice_note,
        # 相手の場の個体が対応待ち: 終盤評価・探索を保留した (2026-10-09 ④。advice 行にそのまま残る)
        "opp_pending": bool(common.get("opp_pending")),
        "opp_pending_note": common.get("opp_pending_note"),
        "rl_hint": common.get("rl_hint"),
        "best": actions[0] if actions else None,
    }


def _sacrifice_note(actions: list, endgame: str) -> str:
    """死に出しプラン: 無償降臨価値で後続を選ぶ。

    無償降臨は相手の攻撃 (incoming) を受けずに着地する (設置技は受ける)。
    今すぐ交代すると incoming を受けるため、確定死の局面では
    「攻撃で削ってから倒され、次を無償で出す」のが一般に得。
    勝ち筋 (endgame検出) の個体を捨て先の筆頭にしない。
    """
    import re
    switches = [a for a in actions
                if a["kind"] == "switch" and a.get("counter") is not None]
    if not switches:
        return ""
    # 無償降臨価値: 出た後の打点 − 設置技 − 次ターン以降の被弾リスク(軽く)
    ranked = sorted(switches, key=lambda a: -(
        a["counter"] - a.get("hazard", 0.0) * 0.8
        - a.get("incoming", 0.0) * 0.2))
    wincon = None
    m = re.search(r"勝ち筋:\s*(\S+)\s*が", endgame or "")
    if m:
        wincon = m.group(1)
    best = ranked[0]
    keep = ""
    if wincon and best["name"] == wincon and len(ranked) >= 2:
        best = ranked[1]
        keep = f" (勝ち筋の {wincon} は温存)"
    return (f"死に出しプラン: この場は最悪応手前提で倒される見込みです。"
            f"攻撃で削ってから倒され、{best['name']} を無償で出すのが"
            f"有効です{keep}")


def _opp_mega_forms(opp_p: dict, state: dict) -> dict:
    """相手の個体がこの先メガシンカする姿 {mega_sid: p} (advisor.gimmick: 石の使用率の事前分布 + 持ち物/メガ済み/権利消費の判明情報)"""
    try:
        from advisor.gimmick import expected_mega
        item_id = opp_p.get("item_id")
        if item_id == "megastone":       # 画面認識の総称 (石であることは分かるが種類は不明) → 事前分布に任せる
            item_id = None
        return expected_mega(opp_p.get("species_id") or "", item_id, bool(opp_p.get("is_mega")),
                             bool((state.get("mega_used") or {}).get("opponent")))
    except Exception:
        return {}


def _mega_timing_note(my_p, my_view, opp_view, my_field, resolver):
    """メガフォルムでの最大打点/被ダメを比較し、切るタイミングを定量提案"""
    if opp_view is None:
        return None
    dex = get_dex()
    base_id = my_view.species_id
    # メガフォルムの解決: 石の id が分かれば requiredItem の表で引く (Z 石を通常メガに倒さない)。分からなければ
    # メガストーン名の末尾 (X/Y/Z) で推定する
    item_ja = (my_p.get("item_ja") or "")
    from advisor.gimmick import stone_form_of
    r_item = None
    try:
        r_item = resolver.resolve(item_ja, "items", cutoff=0.9) if (resolver is not None and item_ja) else None
    except Exception:
        r_item = None
    form = stone_form_of(base_id, r_item[1] if r_item else my_p.get("item_id"), item_ja)
    mega_sp = dex.species(form) if form else None
    if mega_sp is None:
        from vision.normalize import parse_mega_stone_name
        parsed = parse_mega_stone_name(item_ja)
        suffix = parsed[1] if parsed else ""
        mega_sp = dex.species(base_id + "mega" + suffix) or dex.species(base_id + "mega")
    if mega_sp is None:
        return None
    from dataclasses import replace as _replace
    mega_view = _replace(my_view, base=mega_sp["baseStats"],
                         types=mega_sp["types"])
    moves = [m.get("move_id") for m in (my_p.get("moves") or []) if m.get("move_id")]
    if not moves:
        return None

    def best(v):
        b = 0.0
        for mid in moves:
            try:
                b = max(b, calc_damage(v, opp_view, mid, my_field)["avg"])
            except Exception:
                pass
        return b

    d_base, d_mega = best(my_view), best(mega_view)
    gain = d_mega - d_base
    opp_hp = opp_view.hp_frac * 100
    if d_base < opp_hp <= d_mega:
        return (f"★今ターンにメガシンカ推奨: メガ後の打点{d_mega:.0f}%で"
                f"倒せる圏内に入る (通常{d_base:.0f}%では届かない)")
    if gain >= 12:
        return (f"メガシンカで最大打点 {d_base:.0f}%→{d_mega:.0f}%。"
                "攻撃するタイミングで切る価値が高い")
    return (f"メガシンカの打点上昇は+{gain:.0f}%と小さめ。"
            "耐久/素早さ目的か、後続のために温存も選択肢")


def _run_endgame(my_state, opp_state, resolver) -> str:
    """残存メンバーの1v1行列から勝ち筋/負け筋ノートを作る。相手の場の個体が対応待ちなら保留 (結果なし。2026-10-09 ④)"""
    from advisor.endgame import matchup_matrix, endgame_note
    if opp_active_pending(opp_state):
        return ""

    def mons_of(side_state, side):
        out = []
        # 自分側は選出済みの3体のみ (未選出は勝ち筋に数えられない)
        allowed = None
        if side == "player":
            from advisor.party import battle_party_indices
            allowed = battle_party_indices(side_state)
        for i, p in _side_members(side_state, side):
            if p.get("status") == "fainted":
                continue
            if allowed is not None and i not in allowed:
                continue
            v = build_mon_view(p, resolver, side=side)
            if v is None:
                continue
            if side == "player":
                moves = [m.get("move_id") for m in (p.get("moves") or [])
                         if m.get("move_id")]
            else:
                moves = [mid for mid, _ in
                         opponent_move_pool(p, v, resolver)][:6]
            if not moves:
                continue
            hp = p.get("hp_percent")
            hp = (hp / 100.0) if hp is not None else 1.0
            out.append((v.name_ja or v.species_id, v, hp, moves))
        return out

    my_mons = mons_of(my_state, "player")
    opp_mons = mons_of(opp_state, "opponent")
    if not my_mons or not opp_mons:
        return ""
    n_unknown = max(0, (opp_state.get("remaining") or len(opp_mons))
                    - len(opp_mons))
    return endgame_note(matchup_matrix(my_mons, opp_mons), n_unknown)


def _apply_search_blend(actions: list, search_actions: list,
                        weight: float) -> None:
    """探索の推奨値の最善からの差を行動スコアに加える (P9、純粋計算)。

    技は move_id、交代は「交代:<名前>」ラベルで対応付ける。探索に無い行動と
    選べない行動 (score <= -90) は変更しない。
    """
    if not actions or not search_actions:
        return
    rec = {}
    for sa in search_actions:
        if sa.get("kind") == "move" and sa.get("move_id"):
            rec[("move", sa["move_id"])] = sa["recommended"]
        elif sa.get("kind") == "switch":
            rec[("switch", str(sa.get("label", "")).split(":", 1)[-1])] = \
                sa["recommended"]
    if not rec:
        return
    best = max(rec.values())
    for a in actions:
        if a.get("score", 0) <= -90:
            continue
        key = ("move", a.get("id")) if a["kind"] == "move" \
            else ("switch", a.get("name"))
        if key in rec:
            delta = weight * (rec[key] - best)
            if delta:
                a["score"] = round(a["score"] + delta, 1)
                a["reason"] = (a.get("reason") or "") + f" / 探索{delta:+.0f}"


def _hp_frac_of(p: dict) -> float:
    if p.get("hp_percent") is not None:
        return max(0.0, min(1.0, p["hp_percent"] / 100.0))
    if p.get("hp_current") is not None and p.get("hp_max"):
        return max(0.0, p["hp_current"] / p["hp_max"])
    return 1.0


def _run_search(state, my_state, my_view, my_p, opp_state, opp_view,
                resolver, pool, my_field, opp_field, wincon_sid=None):
    """状態辞書 -> SimSide を組み立てて同時手番探索を実行する。相手の場の個体が対応待ちなら、相手の控えを列挙できない
    ので探索しない (相手が未判明のときと同じく None = 直接の採点だけ。2026-10-09 ④)"""
    from advisor.search import SimSide, search
    if my_view is None or opp_view is None or opp_active_pending(opp_state):
        return None

    my_moves = [m.get("move_id") for m in (my_p.get("moves") or [])
                if m.get("move_id")]
    if not my_moves:
        return None

    def bench_of(side_state, side):
        bench = []
        active_idx = side_state.get("active_index")
        # 自分側は選出済みの3体に限る (未選出は交代できない)
        allowed = None
        if side == "player":
            from advisor.party import battle_party_indices
            allowed = battle_party_indices(side_state)
        for i, p in _side_members(side_state, side):
            if i == active_idx or p.get("status") == "fainted":
                continue
            if allowed is not None and i not in allowed:
                continue
            v = build_mon_view(p, resolver, side=side)
            if v is not None:
                bench.append((v, _hp_frac_of(p)))
        return bench[:4]

    me = SimSide(active=my_view, active_hp=_hp_frac_of(my_p),
                 bench=bench_of(my_state, "player"),
                 stealth_rock=bool(my_state.get("hazards", {}).get("stealth_rock")))
    # 相手の場のポケモンがこの先メガシンカする姿 (事前分布 + 判明情報)。探索で「技 + メガシンカ」を分岐する
    opp_active_p = opp_state["party"][opp_state["active_index"]]
    opp_mega_forms = _opp_mega_forms(opp_active_p, state)
    opp = SimSide(active=opp_view, active_hp=_hp_frac_of(opp_active_p),
                  bench=bench_of(opp_state, "opponent"),
                  stealth_rock=bool(opp_state.get("hazards", {}).get("stealth_rock")),
                  mega_forms=opp_mega_forms,
                  mega_used=bool(state.get("mega_used", {}).get("opponent")))
    # RL価値関数を葉評価にブレンド (学習結果の反映)。使えない環境ではNone。
    # 並列実行のため関数でなく文脈 (leaf_ctx) も持ち、ワーカー側で再構成する
    from advisor.search import make_rl_leaf_fn
    leaf_ctx = {"my_moves": my_moves, "field": my_field,
                "turn": state.get("turn") or 5}
    leaf_fn = make_rl_leaf_fn(leaf_ctx)
    if leaf_fn is None:
        leaf_ctx = None

    import time as _time
    t0 = _time.perf_counter()
    result = None
    # 世界の積: 相手型の仮説 (P7) × 自分HPのセンサ世界 (P8)
    from advisor.search import aggregate_worlds, sensor_worlds
    opp_worlds = _belief_worlds(
        opp_view, opp_state["party"][opp_state["active_index"]]) \
        or [(1.0, opp_view)]
    q = SENSOR_Q_UNCERTAIN if my_p.get("hp_uncertain") else SENSOR_Q_DEFAULT
    me_worlds = sensor_worlds(me, q, SENSOR_HP_DELTA)
    combos = [(w_m * w_o, me_m, view_o)
              for w_m, me_m in me_worlds for w_o, view_o in opp_worlds]
    if len(combos) > 1:
        from advisor.search import run_world_searches
        jobs, ws = [], []
        for w_c, me_c, view_o in combos:
            opp_k = SimSide(active=view_o, active_hp=opp.active_hp,
                            bench=opp.bench, stealth_rock=opp.stealth_rock,
                            mega_forms=opp.mega_forms, mega_used=opp.mega_used)
            jobs.append(dict(me=me_c, opp=opp_k, my_moves=my_moves,
                             opp_move_pool=pool, my_field=my_field,
                             opp_field=opp_field, leaf_ctx=leaf_ctx,
                             wincon_sid=wincon_sid))
            ws.append(w_c)
        outs = run_world_searches(jobs, workers=SEARCH_WORKERS)
        result = aggregate_worlds(outs, ws, coverage=sum(ws))
    if result is None:
        result = search(me, opp, my_moves, pool,
                        my_field=my_field, opp_field=opp_field,
                        leaf_value_fn=leaf_fn, wincon_sid=wincon_sid)
    result["search_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
    # 表示用の要約 (上位3行動)
    lines = []
    for a in result["actions"][:3]:
        mark = " ⚠択リスク" if a["risky"] else ""
        lines.append(f"{a['label']}: 期待{a['expected']:+.2f} "
                     f"保証{a['worst']:+.2f} (最悪応手: {a['worst_reply']}){mark}")
    b = result.get("belief")
    if b:
        lines.append(f"型仮説{b['k']}件 (被覆{b['coverage']:.0%}) "
                     f"安定度{b['stability']:.0%} / 探索{result['search_ms']:.0f}ms")
    else:
        lines.append(f"探索{result['search_ms']:.0f}ms (点推定)")
    result["summary_lines"] = lines
    return result


def _belief_worlds(opp_view, opp_p: dict) -> list:
    """相手型の仮説分布から探索する世界 [(重み, MonView)] を作る (P7)。

    BELIEF_K=0、推定器が無い、仮説が1つ以下 (点推定と同じ) なら空を返し、
    呼び出し側は従来の単一探索に落ちる。仮説の持ち物は、持ち物が未判明で
    はたき落とされてもいない場合にだけ適用する (MAP経路と同じ規則)。
    """
    if BELIEF_K <= 0 or opp_view is None:
        return []
    try:
        from advisor.ev_infer import get_tracker, _nature_mult
        hyps = get_tracker().hypotheses_for(opp_view.species_id, BELIEF_K,
                                            BELIEF_MIN_WEIGHT)
    except Exception:
        return []
    if len(hyps) < 2:
        return []
    import copy as _copy
    worlds = []
    for h in hyps:
        v = _copy.copy(opp_view)
        v.ev = dict(h["evs"])
        v.nature = _nature_mult(h["nature"])
        if not opp_view.item and not opp_p.get("item_removed") and h["item"]:
            v.item = h["item"]
        worlds.append((h["weight"], v))
    return worlds
