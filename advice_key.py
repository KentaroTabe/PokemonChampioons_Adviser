"""対戦の助言の再計算の鍵と、計算結果のキャッシュ (server.py の補助。2026-10-19 レビュー: 再計算の鍵の不足)。

旧鍵 (server._advice_key、版 1) は場面・自分の場の個体の種族/HP%/PP・相手の場の個体の種族/HP%・場・能力変化しか見ておらず、
相手の持ち物・特性・対応待ち、自分のアンコール、HP の不確かさ、持ち物の消費が変わっても再計算しなかった (10/15 のログの状態を
1 項目ずつ変えた検査で 6 種類すべて同じ鍵)。ここでは鍵を「採点の入力」に作り直す (版 2、config.ADVICE_KEY_VERSION)。

- 鍵の材料は state の実体 (vision.state の to_dict と server._attach_candidates が付ける欄) から取り、推測で補わない。
  表示だけの欄 (表示名・候補の一覧・次に出しそう度・COMMAND の残り秒・イベント列・時刻) は入れない。
- HP の範囲は観測からの経過時間で変わる (advisor.scoring_transition.hp_range_of は場の個体だけ、経過時間で下限を下げる)。
  場の個体については 観測からの経過時間 を config.ADVICE_KEY_HP_AGE_GRAIN_SEC の粒度で丸めて鍵に入れる (粒度を跨いだら再計算)。
  観測の絶対時刻は入れない (読めている間は毎フレーム更新されるので、入れると値が同じでも鍵が変わり続ける)。
- 採点の設定 (採点方式・感度・表示用の探索・RL の重み・探索の重み) とデータの版 (使用率の snapshot id・登録チーム・図鑑・効果表) も入れる。
- キャッシュするのは採点の結果 (evaluate / finish_evaluation の戻り値) だけ。据え置き (apply_advice_hysteresis)・安定化ゲート
  (apply_advice_stability)・画面の提案 (suggest_check) はキャッシュを使ったときも毎回通す (advise_cached)。
- 有効期限: 通常 config.ADVICE_RECALC_TTL_SEC、退避 (scoring_fallback あり) と評価エラーは config.ADVICE_RECALC_FALLBACK_TTL_SEC。

純粋な部分 (advice_input_key / AdviceCache) はサーバーを起動せずにテストする (tests/test_advice_key.py)。

2026-10-20 (レビュー: キャッシュを使った助言の評価時刻): advise_cached_ex は (助言, 採点したか, 採点を実際に行った時刻,
採点の時点の入力の写し) を返す。キャッシュを使ったときの時刻と入力の写しは、元の採点のときの値 (AdviceCache.t / inputs)。
advise_cached は従来どおり (助言, 採点したか) を返す (tests/test_version_split.py)。

2026-10-21 (レビュー: 録画再生が本番の再計算の経路を通らない): フレームごとの判断 (now を state に入れる → 鍵 → 要否 →
advise_cached_ex、確定前の通し直し) を frame_advice_step に抽出した。server._handle_one_frame と tools.shadow_load_check は
この関数だけを呼ぶ (tests/test_replay_path、実際の入口は tests/test_replay_path_server)。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import traceback
from pathlib import Path
from typing import Optional

from champions_agent import config as C

# 個体の欄のうち採点の入力になるもの (vision.state.PokemonState の欄名。値はそのまま鍵に入れる)。
# 表示だけの欄 (display_name / aliases / gender / guess_score / last_seen_ts / t_first_seen / candidates / guess_sure /
# next_score / spe_range) と、時刻 (hp_read_ts は経過時間の粒度に直して別に入れる) は入れない
MON_KEY_FIELDS = (
    # 識別・形態
    "species_id", "species_ja", "types", "is_mega", "type_changed", "species_guess", "species_candidates",
    # HP の値と不確かさ (範囲の lo / hi と出所はこの組と経過時間で決まる)
    "hp_percent", "hp_current", "hp_max", "hp_uncertain", "hp_estimated", "hp_source",
    # 状態
    "status", "volatiles", "boosts",
    # 特性・持ち物 (確定 / 推定 / 消費 / 喪失)
    "ability_id", "ability_ja", "item_id", "item_ja", "item_consumed", "item_removed",
    # 技 (自分は画面の技と PP、相手は目撃した技)
    "revealed_moves",
    # 技の固定 (アンコールの技・揮発状態の残りターン・場に出たターン)
    "encore_move", "volatile_turns", "entry_turn",
    # 対応待ち (vision.state の pending と、その候補の枠)
    "pending", "pending_hint",
    # 選出の印
    "is_picked", "pick_order", "is_active",
    # 型推定トラッカーの実効素早さ (server._attach_candidates が付ける。RL の素早さ比較に使う)
    "spe_est",
)
MOVE_KEY_FIELDS = ("move_id", "name_ja", "pp", "max_pp")
SIDE_KEY_FIELDS = ("active_index", "remaining", "team_size", "hazards", "screens", "tailwind")
STATE_KEY_FIELDS = ("scene", "turn", "field", "mega_used", "last_move", "protect_streak", "pending_pivot_switch")


def _mon_key(p: dict, is_active: bool, now: Optional[float], grain: float) -> dict:
    out = {k: p.get(k) for k in MON_KEY_FIELDS if p.get(k) is not None}
    out["moves"] = [[m.get(k) for k in MOVE_KEY_FIELDS] if isinstance(m, dict) else m for m in (p.get("moves") or [])]
    # 場の個体の HP の観測からの経過時間 (粒度で丸める)。控えは経過時間で範囲が変わらない (hp_range_of の is_active=False)
    ts = p.get("hp_read_ts")
    if is_active and ts and now is not None:
        age = max(0.0, float(now) - float(ts))
        out["hp_age_bucket"] = int(math.floor(age / grain)) if grain > 0 else round(age, 3)
    return out


def _side_key(side: dict, now: Optional[float], grain: float) -> dict:
    out = {k: side.get(k) for k in SIDE_KEY_FIELDS}
    idx = side.get("active_index")
    out["party"] = [_mon_key(p, i == idx, now, grain) for i, p in enumerate(side.get("party") or [])]
    return out


def advice_input_key(state: dict, now: Optional[float], settings: Optional[dict] = None,
                     versions: Optional[dict] = None, grain: Optional[float] = None) -> str:
    """対戦の助言の再計算の鍵 (純粋)。採点の入力が同じなら同じ文字列、どれかが変われば違う文字列。

    now: 経過時間の基準 (秒。サーバーは time.time())。settings / versions: 採点の設定とデータの版 (scoring_settings() /
    DataVersions.current() の戻り値。テストでは固定の辞書)。grain: HP の経過時間の粒度 (None なら config)。
    自分の場の個体が無い (active_index が範囲外) 状態は旧鍵と同じく "" を返す (サーバーは "" のとき助言を計算しない)"""
    try:
        me_side = state["player"]
        me_idx = me_side["active_index"]
        if not isinstance(me_idx, int) or not (0 <= me_idx < len(me_side["party"])):
            return ""
        g = float(C.ADVICE_KEY_HP_AGE_GRAIN_SEC if grain is None else grain)
        doc = {
            "v": int(C.ADVICE_KEY_VERSION),
            "state": {k: state.get(k) for k in STATE_KEY_FIELDS},
            "player": _side_key(me_side, now, g),
            "opponent": _side_key(state.get("opponent") or {}, now, g),
            "settings": settings or {},
            "versions": versions or {},
        }
        return json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return ""


def scoring_settings() -> dict:
    """採点の設定 (実行時の値を読む。advisor.engine の重みは起動時の環境変数で決まる値)"""
    out = {"scoring_mode": getattr(C, "ADVICE_SCORING_MODE", None),
           "sensitivity": getattr(C, "TRANSITION_SENSITIVITY_ENABLED", None),
           "display_search": getattr(C, "ADVICE_DISPLAY_SEARCH", None)}
    try:
        from advisor import engine as E
        out["rl_blend"] = getattr(E, "RL_BLEND_DEFAULT", None)
        out["search_blend"] = getattr(E, "SEARCH_BLEND", None)
    except Exception as e:
        out["engine_error"] = repr(e)[:80]
    return out


class DataVersions:
    """データの版: 登録チーム (config/my_team.json、更新時刻で読み直される)・図鑑・効果表の sha256 (更新時刻が変わったときだけ
    計算し直す) と、使用率の snapshot id (型予測 advisor.sets.get_predictor が実際に使っている値。最初の予測の前は None)"""

    def __init__(self, paths: Optional[dict] = None):
        self._paths = paths
        self._memo: dict = {}

    def _default_paths(self) -> dict:
        from advisor import my_team, dex, effects
        return {"team": my_team.CONFIG_PATH, "dex": dex.DEX_PATH, "move_effects": effects.MOVE_EFFECTS_PATH}

    def _sha(self, name: str, path: Path) -> Optional[str]:
        try:
            st = Path(path).stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        hit = self._memo.get(name)
        if hit and hit[0] == sig:
            return hit[1]
        try:
            h = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
        except OSError:
            h = None
        self._memo[name] = (sig, h)
        return h

    def current(self) -> dict:
        try:
            paths = self._paths or self._default_paths()
        except Exception:
            paths = {}
        out = {name: self._sha(name, p) for name, p in sorted(paths.items())}
        try:
            from advisor.sets import get_predictor
            out["usage_snapshot"] = getattr(get_predictor(), "_snapshot_id", None)
        except Exception:
            out["usage_snapshot"] = None
        return out


class AdviceCache:
    """採点の結果のキャッシュ (1 件だけ持つ)。鍵が変わったか、有効期限を超えたら計算し直す (純粋。時刻は引数)"""

    def __init__(self, ttl: Optional[float] = None, fallback_ttl: Optional[float] = None):
        self.ttl = float(C.ADVICE_RECALC_TTL_SEC if ttl is None else ttl)
        self.fallback_ttl = float(C.ADVICE_RECALC_FALLBACK_TTL_SEC if fallback_ttl is None else fallback_ttl)
        self.clear()

    def clear(self) -> None:
        self.key: Optional[str] = None
        self.result: Optional[dict] = None
        self.common = None
        self.t: float = 0.0
        self.short: bool = False
        self.inputs = None          # 採点の時点で取った入力の写し (advise_cached_ex の inputs_fn。2026-10-20)

    def needs_recalc(self, key: str, now: float) -> bool:
        return self.recalc_reason(key, now) is not None

    def recalc_reason(self, key: str, now: float) -> Optional[str]:
        """計算し直す理由 (純粋): "first" = 結果を持っていない、"key_changed" = 鍵が変わった、"ttl" = 通常の有効期限を超えた、
        "fallback_ttl" = 退避・評価エラーの短い有効期限を超えた。キャッシュを使えるなら None (2026-10-21、needs_recalc と同じ判定)"""
        if self.result is None:
            return "first"
        if key != self.key:
            return "key_changed"
        if now - self.t > self.current_ttl():
            return "fallback_ttl" if self.short else "ttl"
        return None

    def current_ttl(self) -> float:
        """いま持っている結果の有効期限 (秒)"""
        return self.fallback_ttl if self.short else self.ttl

    def store(self, key: str, result: dict, now: float, common=None, error: bool = False) -> None:
        self.key, self.t, self.common = key, float(now), common
        self.result = copy.deepcopy(result)
        self.short = bool(error or (result or {}).get("scoring_fallback"))

    def get(self) -> Optional[dict]:
        """キャッシュの結果の写し (後段の処理と記録が書き込むので写しを渡す)"""
        return None if self.result is None else copy.deepcopy(self.result)


def _evaluate(advisor, state: dict, keep_common: bool):
    """採点だけ (Advisor.advise の前半と同じ呼び方。据え置き・安定化ゲートは含まない)。戻り値 (result, common, error)"""
    from advisor.engine import evaluate, evaluate_common, finish_evaluation
    try:
        if keep_common:
            common = evaluate_common(state, advisor.resolver)
            return finish_evaluation(common, None), common, False
        return evaluate(state, advisor.resolver), None, False
    except Exception as e:   # 助言の失敗で本体を落とさない (Advisor.advise と同じ)
        traceback.print_exc()
        return {"ok": False, "reason": f"評価エラー: {e}"}, None, True


def _postprocess(advisor, result: dict, state: dict) -> dict:
    """据え置き・安定化ゲート・画面の提案 (Advisor.advise の後半と同じ。キャッシュを使ったときも毎回通す)"""
    from advisor.service import apply_advice_hysteresis, apply_advice_stability
    try:
        result, advisor._last_best = apply_advice_hysteresis(result, advisor._last_best, state.get("turn"))
        result, advisor._stab_hist = apply_advice_stability(result, advisor._stab_hist, state.get("turn"))
    except Exception as e:
        traceback.print_exc()
        result = {"ok": False, "reason": f"評価エラー: {e}"}
    suggestion = advisor.suggest_check(state)
    if suggestion:
        result["suggestion"] = suggestion
    if result.get("ok"):
        # 自分の選出の判定の欄 (2026-10-20、Advisor.advise と同じ。欄の追加だけで採点の値は変えない)
        from advisor.service import pick_state_field
        result = dict(result, pick_state=pick_state_field(state))
    return result


def advise_cached_ex(advisor, state: dict, cache: AdviceCache, key: str, now: float, keep_common: bool = False,
                     inputs_fn=None) -> tuple:
    """advise_cached の詳細版。戻り値 (advice, 採点したか, 採点を実際に行った時刻, 採点の時点の入力の写し)。
    採点したときは時刻 = now、入力の写し = inputs_fn() (採点の直前に同じスレッドで取る。例外なら None)。キャッシュを使ったときは
    元の採点の時刻 (cache.t) と、そのとき取った写し (cache.inputs)"""
    if cache.needs_recalc(key, now):
        advisor.last_common = None
        inputs = None
        if inputs_fn is not None:
            try:
                inputs = inputs_fn()
            except Exception:
                inputs = None
        raw, common, error = _evaluate(advisor, state, keep_common)
        cache.store(key, raw, now, common=common, error=error)
        cache.inputs = copy.deepcopy(inputs)
        result, evaluated = raw, True
    else:
        result, common, evaluated = cache.get(), cache.common, False
        inputs = copy.deepcopy(cache.inputs)
    if keep_common:
        advisor.last_common = common
    return _postprocess(advisor, result, state), evaluated, float(cache.t), inputs


def advise_cached(advisor, state: dict, cache: AdviceCache, key: str, now: float, keep_common: bool = False) -> tuple:
    """対戦の助言。鍵が前回と同じで有効期限内ならキャッシュの採点結果を使い、そうでなければ採点する。
    どちらでも据え置き・安定化ゲート・画面の提案を通す。keep_common=True なら advisor.last_common に採点の共通部分を置く
    (キャッシュを使ったときはキャッシュした共通部分)。戻り値 (advice, 採点したか)"""
    advice, evaluated, _t, _inp = advise_cached_ex(advisor, state, cache, key, now, keep_common=keep_common)
    return advice, evaluated


# フレームごとの助言の判断の理由 (frame_advice_step の reason)。AdviceCache.recalc_reason の 4 つに加えて:
# "recheck" = キャッシュは使えるが、確定前 (provisional) の助言を出した次のフレームなので安定化ゲートを通し直す (採点はしない)、
# "cached_skip" = キャッシュが使えて通し直しも要らない (助言を出さない)、"no_key" = 自分の場の個体が無い (助言を出さない)
FRAME_STEP_REASONS = ("first", "key_changed", "ttl", "fallback_ttl", "recheck", "cached_skip", "no_key")


def frame_advice_step(advisor, state: dict, now: float, cache: AdviceCache, data_versions, recheck: bool = False,
                      keep_common: bool = False, inputs_fn=None) -> dict:
    """本番のフレームごとの対戦の助言の判断 (2026-10-21 レビュー: 録画再生を本番と同じ経路に通す)。
    server._handle_one_frame (決定画面のフレーム) と tools.shadow_load_check (録画再生) はこの関数だけを呼ぶ。

    手順: state["now"] = now (advice 行の t_now と transition の HP の範囲の経過時間の基準) → 再計算の鍵 (advice_input_key に
    実行時の採点の設定 scoring_settings() とデータの版 data_versions.current()) → 鍵が "" なら何もしない → キャッシュが使えて
    recheck も偽なら何もしない → それ以外は advise_cached_ex (鍵が変わったか有効期限を超えたら採点、そうでなければキャッシュの
    採点結果に据え置き・安定化ゲートを通す)。

    引数: cache / data_versions は呼び出し側が持つ (server はプロセスに 1 つ、再生は再生 1 回に 1 つ)。recheck = 前のフレームで
    確定前の助言を出したか (戻り値の "recheck" を次のフレームに渡す)。keep_common / inputs_fn は advise_cached_ex と同じ。
    戻り値 {"advice": 助言 or None (出さない), "evaluated": 採点したか, "t_eval": 採点を実際に行った時刻 (キャッシュなら元の時刻。
    出さないなら None), "inputs": 採点の時点の入力の写し, "key": 鍵, "key_changed": 前の結果の鍵から変わったか (結果が無ければ偽),
    "reason": FRAME_STEP_REASONS のどれか, "recheck": 次のフレームに渡す recheck}"""
    state["now"] = now
    key = advice_input_key(state, now, scoring_settings(), data_versions.current())
    out = {"advice": None, "evaluated": False, "t_eval": None, "inputs": None, "key": key,
           "key_changed": bool(key and cache.result is not None and key != cache.key), "reason": None, "recheck": bool(recheck)}
    if not key:
        out["reason"] = "no_key"
        return out
    why = cache.recalc_reason(key, now)
    if why is None and not recheck:
        out["reason"] = "cached_skip"
        return out
    advice, evaluated, t_eval, inputs = advise_cached_ex(advisor, state, cache, key, now, keep_common=keep_common,
                                                         inputs_fn=inputs_fn)
    out.update(advice=advice, evaluated=evaluated, t_eval=t_eval, inputs=inputs, reason=why or "recheck",
               recheck=bool(advice.get("provisional")))
    return out
