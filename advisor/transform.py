"""へんしん (メタモン・かわりもの) の入力修正 (2026-10-21 レビュー、docs/FALLBACK_DIAG_20261020.md・計画書 §8.43)。

相手の場の個体が こちらの場の個体に へんしん した状態を、状態側で「へんしん時点のコピー元・写し・観測根拠」(PokemonState.transform)
として持ち、エンジンの共通部分 (advisor.engine.evaluate_common の入口) で評価用の入力を作る。両方式 (legacy の採点・transition の
build_inputs・探索・終盤評価・RL の観測) に同じ入力が行く。相手の個体の識別 (species = ditto、表示名) は状態では変えない。

- 観測根拠は「記録される観測」だけ (config TRANSFORM_*。10/20 3 戦目の実データで記録されていたもの)。根拠が無い・コピー元が
  1 つに決まらないときは transform を付けない (= 従来どおり。transition は効果の表に無い へんしん で退避する)。
- 写しに入れるもの: 種族 (現在形態。メガ後ならメガ形態)・タイプ・特性・技 (こちらの判明技 + 登録の技、PP は TRANSFORM_COPY_PP)・
  能力ランク (へんしん時点のこちらの値)・種族値・こちらの登録の型の努力値と性格 (登録が無ければ None。そのとき評価は自分の個体を
  評価するときと同じ既定の仮定 = engine.OFFENSIVE_EV・性格なし)。
- 相手のままのもの: HP (割合・最大 HP = 相手の種族の HP の種族値。写した種族の HP を使わない)・持ち物 (観測が無ければ相手の種族の
  使用率の推定のまま。推定を観測扱いにしない)・状態異常・揮発状態・種族の識別。
- 合成試験 (docs/FALLBACK_DIAG §4) の近似 (写した種族の使用率の配分で実数値を代用・写した種族の最大 HP・推定のスカーフを観測扱い) は
  入れない。

純粋な部分 (tracker_step / make_copy / eval_view / eval_state / view_stats) は外部環境なしでテストする (tests/test_transform_input.py)。
状態を書き換えるのは observe_frame (vision.pipeline がフレームの終わりに呼ぶ) だけ。
"""
from __future__ import annotations

import copy
from typing import Callable, Optional

from champions_agent import config as C

STAT_KEYS = ("atk", "def", "spa", "spd", "spe", "acc", "eva")   # vision.state.STAT_KEYS と同じ
MOVE_EVENT_PREFIX = "move_opponent_"
SWITCH_EVENT = "switch_opponent"


# ==============================================================================
# 個体の辞書の小さな読み取り (純粋)
# ==============================================================================
def _sid(p: Optional[dict]) -> Optional[str]:
    return (p or {}).get("species_id") or None


def _boosts(p: Optional[dict]) -> dict:
    """能力ランクの正規形 (0 を落とした dict)"""
    return {k: int(v) for k, v in ((p or {}).get("boosts") or {}).items() if v}


def _fainted(p: Optional[dict]) -> bool:
    if not p:
        return True
    if p.get("status") == "fainted":
        return True
    hp = p.get("hp_percent")
    return hp is not None and hp <= 0


def is_transform_species(p: Optional[dict], species=None) -> bool:
    """へんしん する種 (config TRANSFORM_SPECIES) の個体か"""
    return _sid(p) in tuple(C.TRANSFORM_SPECIES if species is None else species)


def merge_boosts(copied: dict, opp_now: dict, opp_at_start: dict) -> dict:
    """へんしん後の能力ランク (純粋): 写した値 + 相手の個体の「場に出てから観測した変化」(今 − 場に出たとき)。±6 で切る。
    へんしん はコピー元の能力ランクで相手自身の値を置き換えるので、へんしん より前の相手の変化は消え、その後の変化だけが載る。
    発動を見た時点では場に出たときからの変化はふつう 0 (= 写した値そのまま)"""
    out = {}
    for k in STAT_KEYS:
        v = int((copied or {}).get(k) or 0) + int((opp_now or {}).get(k) or 0) - int((opp_at_start or {}).get(k) or 0)
        v = max(-6, min(6, v))
        if v:
            out[k] = v
    return out


# ==============================================================================
# 写しの作成 (純粋。図鑑・形態の表は引く)
# ==============================================================================
def _types_ja(types_en) -> list:
    from advisor.engine import type_ja2en
    en2ja = {str(en).lower(): ja for ja, en in type_ja2en().items()}
    return [en2ja.get(str(t).lower(), t) for t in types_en or []]


def make_copy(src: Optional[dict], registered: Optional[list] = None, build: Optional[dict] = None,
              boosts: Optional[dict] = None, pp: Optional[int] = None) -> Optional[dict]:
    """こちらの個体 src (状態の個体の辞書) の写し (純粋)。作れなければ None (= transform を付けない)。

    registered: こちらの登録の技 [(日本語名, move_id)] (engine.registered_moves_of_mon)。判明技 (src の moves) の空いた枠を補う。
    build: こちらの登録の型 (engine.my_build_of の戻り値。{"ev", "nature", ...}) か None。
    boosts: へんしん時点のこちらの能力ランク (None なら src の今の値)。
    戻り値 {"species", "types" (日本語), "moves": [{"move_id", "name_ja", "pp"}], "ability", "boosts", "base_stats", "ev", "nature"}。
    種族が図鑑に無い・技が 1 つも分からないなら None (写しの技が無いと相手の技の候補を作れない)"""
    if not src:
        return None
    from advisor.dex import get_dex
    from advisor.engine import complete_moves_from_registration
    from advisor.gimmick import resolve_form
    form = resolve_form(src)
    if form is None:
        return None
    sp = get_dex().species(form.species)
    if not sp:
        return None
    slots = complete_moves_from_registration(list(src.get("moves") or []), list(registered or []))
    pp = int(C.TRANSFORM_COPY_PP if pp is None else pp)
    moves, seen = [], set()
    for s in slots:
        mid = (s or {}).get("move_id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        moves.append({"move_id": mid, "name_ja": s.get("name_ja") or mid, "pp": pp})
    moves = moves[:4]
    if not moves:
        return None
    ability = form.ability or src.get("ability_id")
    ev = dict(build["ev"]) if build and build.get("ev") else None
    nature = dict(build["nature"]) if build and build.get("nature") is not None else None
    return {
        "species": form.species,
        "types": _types_ja(form.types),
        "moves": moves,
        "ability": ability,
        "boosts": _boosts({"boosts": boosts} if boosts is not None else src),
        "base_stats": dict(sp["baseStats"]),
        "ev": ev,
        "nature": nature,
    }


def make_transform(copy_: dict, source_index: int, evidence: str, t: Optional[float]) -> dict:
    """状態の個体に付ける transform (純粋)"""
    return {"source_species": copy_["species"], "source_index": source_index, "copy": copy.deepcopy(copy_),
            "evidence": evidence, "t": None if t is None else round(float(t), 3)}


# ==============================================================================
# 観測根拠の判定 (純粋): フレームごとの追跡
# ==============================================================================
def pair_update(pair: Optional[dict], opp_index, opp: Optional[dict], my_index, my: Optional[dict], now: float,
                make_copy_fn: Callable) -> Optional[dict]:
    """相手の場の個体が TRANSFORM_SPECIES の間の「対面」の記録 (純粋)。

    相手の場の個体の種族が変わったら作り直す: 作った時点のこちらの場の個体 (枠・写し = make_copy_fn(my, my_index))、
    相手の能力ランク、時刻。start_ok = こちらの場の個体が分かっていて ひんし でなく写しを作れた。stable = 作ってから こちらの場の
    個体の枠・種族・能力ランクが変わっていない (技の根拠でコピー元を 1 つに決める条件)"""
    if opp is None or opp_index is None or not is_transform_species(opp):
        return None
    my_sid = _sid(my)
    # 相手の枠の番号は見ない (場の個体の仮の枠が選出の枠へ統合されると番号が変わる。TRANSFORM_SPECIES は 1 体だけ)
    if pair and pair.get("opp_species") == _sid(opp):
        if pair.get("stable") and (my_index != pair.get("my_index") or my_sid != pair.get("my_species")
                                   or _boosts(my) != pair.get("my_boosts")):
            pair = dict(pair, stable=False)
        return pair
    cp = None
    if my is not None and my_index is not None and my_sid and not _fainted(my):
        try:
            cp = make_copy_fn(my, my_index)
        except Exception:
            cp = None
    return {"opp_index": opp_index, "opp_species": _sid(opp), "opp_boosts": _boosts(opp), "my_index": my_index,
            "my_species": my_sid, "my_boosts": _boosts(my), "copy": cp, "t": float(now),
            "start_ok": cp is not None, "stable": cp is not None}


def move_evidence(fired: list, pair: Optional[dict], now: float, own_moves=None) -> Optional[str]:
    """技の根拠 (純粋): このフレームで相手が TRANSFORM_OWN_MOVES 以外の技を使い、その技が写しの技に入っている。
    対面が前のフレームから続いていて (このフレームで相手の交代を見ていない)、コピー元が 1 つに決まる (stable) ときだけ"""
    if not pair or not pair.get("stable") or not pair.get("copy"):
        return None
    if SWITCH_EVENT in (fired or []) or not (float(now) > float(pair.get("t") or 0.0)):
        return None
    own = set(C.TRANSFORM_OWN_MOVES if own_moves is None else own_moves)
    ids = {m["move_id"] for m in pair["copy"].get("moves") or []}
    for f in fired or []:
        if not str(f).startswith(MOVE_EVENT_PREFIX):
            continue
        mid = str(f)[len(MOVE_EVENT_PREFIX):]
        if mid and mid not in own and mid in ids:
            return f"event:{f}"
    return None


def tracker_step(tracker: Optional[dict], opp_index, opp: Optional[dict], my_index, my: Optional[dict], fired: list,
                 now: float, make_copy_fn: Callable) -> tuple:
    """1 フレームの追跡 (純粋)。戻り値 (新しい tracker, 付ける transform と相手の能力ランク {"transform", "boosts"} or None)。

    tracker = {"pair": pair_update の値, "pending_event": {"evidence", "t"} or None}。
    発動のイベント (TRANSFORM_EVENT_EVIDENCE) は TRANSFORM_EVENT_WINDOW_SEC の間だけ持ち、対面 (start_ok) があれば付ける
    (イベントと相手の場の個体の更新の順序が前後してよい)。技の根拠 (TRANSFORM_MOVE_EVIDENCE) は move_evidence。
    相手の場の個体に transform が既にあれば何もしない (へんしん は 1 回。交代で消える)"""
    tr = dict(tracker or {})
    ev_ids = set(C.TRANSFORM_EVENT_EVIDENCE)
    for f in fired or []:
        if f in ev_ids:
            tr["pending_event"] = {"evidence": f"event:{f}", "t": float(now)}
    pe = tr.get("pending_event")
    if pe and float(now) - float(pe["t"]) > float(C.TRANSFORM_EVENT_WINDOW_SEC):
        pe = None
    prev = tr.get("pair")
    pair = pair_update(prev, opp_index, opp, my_index, my, now, make_copy_fn)
    tr["pair"], tr["pending_event"] = pair, pe
    if pair is None or (opp or {}).get("transform"):
        return tr, None
    evidence = None
    if pe and pair.get("start_ok"):
        evidence = pe["evidence"]
        tr["pending_event"] = None
    elif C.TRANSFORM_MOVE_EVIDENCE:
        evidence = move_evidence(fired, pair, now)
    if evidence is None:
        return tr, None
    cp = pair["copy"]
    tf = make_transform(cp, pair["my_index"], evidence, now)
    return tr, {"transform": tf, "boosts": merge_boosts(cp.get("boosts"), _boosts(opp), pair.get("opp_boosts"))}


def _default_copy_fn(resolver) -> Callable:
    """こちらの個体の写しを作る関数 (登録の技・型は config/my_team.json を読む)"""
    def fn(my: dict, _idx) -> Optional[dict]:
        from advisor.engine import my_build_of, registered_moves_of_mon
        reg = []
        if resolver is not None:
            try:
                reg = registered_moves_of_mon(my, resolver)
            except Exception:
                reg = []
        try:
            build = my_build_of(my)
        except Exception:
            build = None
        return make_copy(my, reg, build)
    return fn


def observe_frame(state, fired: list, now: float, resolver=None, make_copy_fn: Optional[Callable] = None) -> Optional[dict]:
    """vision.pipeline がフレームの終わりに呼ぶ (副作用: state = vision.state.BattleStateV2 を書き換える)。
    追跡 (state.transform_tracker) を進め、根拠がそろったら相手の場の個体に transform を付け、能力ランクを写しにする。
    付けた transform を返す (付けなければ None)"""
    opp_side, my_side = state.opponent, state.player
    opp_mon, my_mon = opp_side.active(), my_side.active()
    # 相手の場の個体が対象の種でないフレーム (ほとんど) は個体の辞書を作らない (種族だけで pair_update が None を返す)
    target = opp_mon is not None and opp_mon.species_id in tuple(C.TRANSFORM_SPECIES)
    opp_d = (opp_mon.to_dict() if target else {"species_id": opp_mon.species_id}) if opp_mon is not None else None
    my_d = my_mon.to_dict() if (my_mon is not None and target) else None
    fn = make_copy_fn or _default_copy_fn(resolver)
    tr, attach = tracker_step(getattr(state, "transform_tracker", None),
                              opp_side.active_index if opp_mon is not None else None, opp_d,
                              my_side.active_index if my_mon is not None else None, my_d, fired, now, fn)
    state.transform_tracker = tr
    if attach is None or opp_mon is None:
        return None
    opp_mon.transform = attach["transform"]
    opp_mon.boosts = {k: int(attach["boosts"].get(k, 0)) for k in opp_mon.boosts}
    # 状態のイベント列に注記だけ残す (event_id は付けない: battle_logger は event_id のある項目の文言を発火の列と対応付けるので、
    # 付けると events 行の texts がずれる)
    try:
        state.log_event("system", f"{opp_mon.species_ja or opp_mon.species_id} は へんしん 中 "
                                  f"(写し: {attach['transform']['source_species']}、根拠 {attach['transform']['evidence']})")
    except Exception:
        pass
    return attach["transform"]


# ==============================================================================
# 評価用の入力 (純粋。両方式共通: engine.evaluate_common の入口)
# ==============================================================================
def eval_view(p: dict) -> Optional[dict]:
    """transform のある相手の個体 p の評価用の見た目 (純粋。図鑑を引く)。作れなければ None。

    写し: species_id (現在形態)・types・ability_id・判明技 (revealed_moves = 写しの技の日本語名。使用率の技の推定を使わない)。
    相手のまま: HP (割合・最大 HP の種族値)・持ち物・状態異常・揮発状態・能力ランク (状態の値。へんしん した時点で写しの値に
    置き換え済み、その後の変化は観測どおり)・種族の識別 (transform_view.original_species と species_ja)。
    spe_est (相手の種族の実効素早さの推定) は外す (写しの素早さはこちらの登録で決まる)"""
    tf = (p or {}).get("transform")
    if not tf:
        return None
    cp = tf.get("copy") or {}
    from advisor.dex import get_dex
    dex = get_dex()
    sp_c = dex.species(cp.get("species"))
    sp_o = dex.species(_sid(p))
    moves = [m for m in cp.get("moves") or [] if m.get("move_id")]
    if not sp_c or not sp_o or not moves:
        return None
    view = dict(p)
    view.pop("spe_est", None)
    view.update({
        "species_id": cp["species"],
        "types": list(cp.get("types") or []),
        "ability_id": cp.get("ability"),
        "ability_ja": None,
        "is_mega": False,
        "type_changed": False,
        "species_candidates": [],
        "revealed_moves": [m.get("name_ja") or m["move_id"] for m in moves],
        "moves": [],
        "transform_view": {
            "original_species": _sid(p),
            "source_species": tf.get("source_species"),
            "hp_base": sp_o["baseStats"]["hp"],
            "base": dict(cp.get("base_stats") or sp_c["baseStats"]),
            "ev": copy.deepcopy(cp.get("ev")),
            "nature": copy.deepcopy(cp.get("nature")),
            "moves": [m["move_id"] for m in moves],
            "evidence": tf.get("evidence"),
        },
    })
    return view


def eval_state(state: dict) -> tuple:
    """評価用の状態 (純粋。入力は書き換えない)。戻り値 (state', transform_applied)。
    相手の場の個体に transform があり写しを作れたら、その個体を eval_view に置き換えた浅い写しの状態と True。
    transform が無い (作れない) なら元の state と False。相手の場の個体が分からなければ元の state と None"""
    opp = (state or {}).get("opponent") or {}
    party = opp.get("party") or []
    idx = opp.get("active_index")
    if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(party)):
        return state, None
    view = eval_view(party[idx])
    if view is None:
        return state, False
    new_party = list(party)
    new_party[idx] = view
    out = dict(state)
    out["opponent"] = dict(opp, party=new_party)
    return out, True


def transform_applied_of(state: dict) -> Optional[bool]:
    """advice の欄 transform_applied (純粋): evaluate_common が評価用の入力に写しを使うか (eval_state と同じ判定)。
    True = 写しで評価、False = 相手の場の個体に transform が無い (写しを作れない)、None = 相手の場の個体が分からない"""
    try:
        return eval_state(state)[1]
    except Exception:
        return None


def view_stats(tv: dict, default_ev: dict) -> tuple:
    """評価用の見た目の種族値・努力値・性格 (純粋)。HP は相手の種族の種族値 (最大 HP を写した種族で仮定しない)、
    努力値の HP は default_ev の HP (相手の個体の既定の仮定。evaluate_common が相手の種族の型推定で上書きする)、
    HP 以外は写し (こちらの登録の型。無ければ default_ev = 自分の個体を評価するときと同じ既定)"""
    base = dict(tv.get("base") or {})
    base["hp"] = tv["hp_base"]
    ev = dict(tv.get("ev") or default_ev)
    ev["hp"] = int((default_ev or {}).get("hp", 0))
    nature = dict(tv.get("nature") or {})
    return base, ev, nature


def transform_note(view: Optional[dict]) -> str:
    """表示の注記 (純粋)"""
    tv = (view or {}).get("transform_view") or {}
    if not tv:
        return ""
    return f"相手は へんしん 中: {tv.get('source_species')} の写しで評価 (HP・持ち物は相手のまま、根拠 {tv.get('evidence')})"
