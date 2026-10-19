"""対戦の助言の再計算の鍵とキャッシュ (advice_key、server.py が使う) のテスト (2026-10-19 レビュー: 再計算の鍵の不足)。

- レビュワーの 6 種類の変更 (相手の持ち物 / 特性 / 対応待ち、自分のアンコール、HP の不確かさ、持ち物の消費) で鍵が変わる
- 場の個体の HP の観測からの経過時間が粒度を跨ぐと鍵が変わる。控えの観測時刻・粒度の中の時刻の差では変わらない
- 表示だけの欄 (表示名・候補・COMMAND の残り秒・イベント列) では変わらない。採点の設定・データの版では変わる
- 有効期限: 通常の結果は config.ADVICE_RECALC_TTL_SEC、退避 (scoring_fallback) と評価エラーは ADVICE_RECALC_FALLBACK_TTL_SEC
- キャッシュを使ったときも据え置き・安定化ゲートを通す (確定前 → 次の呼び出しで採点せずに確定)。後段の書き込みがキャッシュを汚さない
- 10/15 の実戦ログの advice 行の state (レビュワーの検査と同じ形): 旧鍵 (版 1) では 6 種類とも同じ鍵、新しい鍵では全部変わる
  (ログはコミットしないので、無い環境 (CI) ではこの 1 本だけ SKIP と出す)

    scripts/run_test.sh test_advice_key
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import advice_key as K
import advisor.engine as E
import advisor.service as SV   # 先に読む (service は engine.evaluate を import 時に束縛するので、差し替えの前に読んでおく)
from champions_agent import config as C
from vision.state import BattleStateV2, MoveSlot, PokemonState

T0 = 1_800_000_000.0
SETTINGS = {"scoring_mode": "legacy", "sensitivity": False, "display_search": "off", "rl_blend": 3.0, "search_blend": 0.0}
VERSIONS = {"team": "aaaa", "dex": "bbbb", "move_effects": "cccc", "usage_snapshot": 7}


def _state() -> dict:
    """BattleStateV2.to_dict の実際の欄名で作った対戦中の状態 (自分 3 体・相手 3 体、場の個体はどちらも 0 番)"""
    s = BattleStateV2()
    s.scene = "command"
    s.turn = 3
    s.battle_active = True
    s.command_no = 40
    me = PokemonState(species_ja="カメックス", species_id="blastoise", types=["みず"], hp_percent=80.0, hp_current=120,
                      hp_max=150, hp_read_ts=T0, item_id="blastoisinite", ability_id="torrent", is_active=True,
                      is_picked=True, moves=[MoveSlot(name_ja="なみのり", move_id="surf", pp=10, max_pp=15),
                                             MoveSlot(name_ja="れいとうビーム", move_id="icebeam", pp=8, max_pp=10)])
    b1 = PokemonState(species_ja="イエッサン", species_id="indeedee", types=["エスパー", "ノーマル"], hp_percent=100.0,
                      hp_current=137, hp_max=137, hp_read_ts=T0 - 100, is_picked=True)
    b2 = PokemonState(species_ja="オオニューラ", species_id="sneasler", types=["かくとう", "どく"], is_picked=True)
    s.player.party = [me, b1, b2]
    s.player.active_index = 0
    s.player.remaining = 3
    op = PokemonState(species_ja="カイリュー", species_id="dragonite", types=["ドラゴン", "ひこう"], hp_percent=100.0,
                      hp_read_ts=T0, is_active=True, display_name="カイリュー")
    o1 = PokemonState(species_ja="ミミッキュ", species_id="mimikyu", types=["ゴースト", "フェアリー"], species_guess=True)
    o2 = PokemonState(species_ja="ギルガルド", species_id="aegislash", types=["はがね", "ゴースト"])
    s.opponent.party = [op, o1, o2]
    s.opponent.active_index = 0
    s.opponent.remaining = 3
    return s.to_dict()


def _key(state: dict, now: float = T0 + 1.0, settings=None, versions=None) -> str:
    return K.advice_input_key(state, now, SETTINGS if settings is None else settings,
                              VERSIONS if versions is None else versions)


# レビュワーの 6 種類の変更 (proposal_key_check.json の change 名と同じ)。state を書き換える関数
def _opponent_item(st):
    st["opponent"]["party"][st["opponent"]["active_index"]]["item_id"] = "choicescarf"


def _opponent_ability(st):
    st["opponent"]["party"][st["opponent"]["active_index"]]["ability_id"] = "multiscale"


def _opponent_pending(st):
    st["opponent"]["party"][st["opponent"]["active_index"]]["pending"] = True


def _player_encore(st):
    p = st["player"]["party"][st["player"]["active_index"]]
    p["volatiles"] = list(p.get("volatiles") or []) + ["encore"]
    p["encore_move"] = "surf"


def _player_hp_uncertain(st):
    p = st["player"]["party"][st["player"]["active_index"]]
    p["hp_uncertain"] = True
    p["hp_estimated"] = True
    p["hp_source"] = "bar"


def _opponent_item_consumed(st):
    st["opponent"]["party"][st["opponent"]["active_index"]]["item_consumed"] = True


MUTATIONS = {"opponent_item": _opponent_item, "opponent_ability": _opponent_ability, "opponent_pending": _opponent_pending,
             "player_encore": _player_encore, "player_hp_uncertain": _player_hp_uncertain,
             "opponent_item_consumed": _opponent_item_consumed}


def test_reviewer_mutations_change_key():
    base = _state()
    k0 = _key(base)
    assert k0
    for name, fn in MUTATIONS.items():
        st = copy.deepcopy(base)
        fn(st)
        assert _key(st) != k0, name
    print("test_reviewer_mutations_change_key OK:", list(MUTATIONS))


def test_other_scoring_inputs_change_key():
    """持ち物の喪失・推定の種族・控えの HP・技の PP・設置・場・メガ使用・ターン・直前の技も鍵に入る"""
    base = _state()
    k0 = _key(base)
    changes = {
        "item_removed": lambda st: st["opponent"]["party"][0].__setitem__("item_removed", True),
        "species_guess_off": lambda st: st["opponent"]["party"][1].__setitem__("species_guess", False),
        "bench_hp": lambda st: st["player"]["party"][1].__setitem__("hp_percent", 50.0),
        "bench_fainted": lambda st: st["player"]["party"][1].__setitem__("status", "fainted"),
        "pp": lambda st: st["player"]["party"][0]["moves"][0].__setitem__("pp", 9),
        "taunt": lambda st: st["player"]["party"][0].__setitem__("volatiles", ["taunt"]),
        "hazard": lambda st: st["opponent"]["hazards"].__setitem__("stealth_rock", True),
        "screen": lambda st: st["player"]["screens"].__setitem__("reflect", True),
        "tailwind": lambda st: st["opponent"].__setitem__("tailwind", True),
        "trick_room": lambda st: st["field"].__setitem__("trick_room", True),
        "mega_used": lambda st: st["mega_used"].__setitem__("opponent", True),
        "turn": lambda st: st.__setitem__("turn", 4),
        "last_move": lambda st: st["last_move"].__setitem__("player", "surf"),
        "picked": lambda st: st["player"]["party"][2].__setitem__("is_picked", False),
        "spe_est": lambda st: st["opponent"]["party"][0].__setitem__("spe_est", 120),
    }
    for name, fn in changes.items():
        st = copy.deepcopy(base)
        fn(st)
        assert _key(st) != k0, name
    print("test_other_scoring_inputs_change_key OK:", len(changes))


def test_hp_age_crosses_grain():
    """場の個体の HP の観測からの経過時間が粒度 (config) を跨いだら鍵が変わる。粒度の中では変わらない"""
    g = float(C.ADVICE_KEY_HP_AGE_GRAIN_SEC)
    st = _state()
    k_a = _key(st, now=T0 + 0.1 * g)
    k_b = _key(st, now=T0 + 0.9 * g)
    k_c = _key(st, now=T0 + 1.1 * g)
    assert k_a == k_b
    assert k_a != k_c
    # 60 秒後 (レビュワーの例: 同じ 50% のバー推定でも範囲の下限が下がる) も直後と別の鍵
    assert _key(st, now=T0 + 60.0) != k_a
    print("test_hp_age_crosses_grain OK: grain", g)


def test_unrelated_changes_keep_key():
    """表示だけの欄・時刻の微小な差・控えの観測時刻・読み直しによる観測時刻の更新 (経過時間が同じ粒度) では変わらない"""
    base = _state()
    now = T0 + 1.0
    k0 = _key(base, now=now)
    changes = {
        "display_name": lambda st: st["opponent"]["party"][0].__setitem__("display_name", "Dragonite"),
        "aliases": lambda st: st["opponent"]["party"][0].__setitem__("aliases", ["カイリュ"]),
        "candidates": lambda st: st["opponent"]["party"][1].update(candidates=[{"id": "x", "ja": "x", "pct": 1.0}],
                                                                   guess_sure=False, next_score=0.3),
        "spe_range": lambda st: st["opponent"]["party"][0].__setitem__("spe_range", [100, 130]),
        "command_no": lambda st: st.__setitem__("command_no", 12),
        "events": lambda st: st["events"].append({"event": "move", "ts": T0}),
        "last_seen_ts": lambda st: st["player"]["party"][0].__setitem__("last_seen_ts", T0 + 0.5),
        "bench_read_ts": lambda st: st["player"]["party"][1].__setitem__("hp_read_ts", T0 - 300),
        "active_reread": lambda st: st["player"]["party"][0].__setitem__("hp_read_ts", T0 + 0.4),
        "hp_reject": lambda st: st.__setitem__("hp_reject", {"player": {"x": 1}}),
        "watch_opp_rows": lambda st: st.__setitem__("watch_opp_rows", [{"slot": 1}]),
    }
    for name, fn in changes.items():
        st = copy.deepcopy(base)
        fn(st)
        assert _key(st, now=now) == k0, name
    # 時刻の微小な差 (同じ粒度の中)
    assert _key(base, now=now + 0.3) == k0
    print("test_unrelated_changes_keep_key OK:", len(changes))


def test_settings_and_versions_change_key():
    st = _state()
    k0 = _key(st)
    for k, v in (("scoring_mode", "transition"), ("sensitivity", True), ("display_search", "on"), ("rl_blend", 0.0),
                 ("search_blend", 1.0)):
        assert _key(st, settings=dict(SETTINGS, **{k: v})) != k0, k
    for k, v in (("team", "dddd"), ("dex", "eeee"), ("move_effects", "ffff"), ("usage_snapshot", 8)):
        assert _key(st, versions=dict(VERSIONS, **{k: v})) != k0, k
    print("test_settings_and_versions_change_key OK")


def test_no_active_gives_empty_key():
    """自分の場の個体が無いときは旧鍵と同じく "" (サーバーは助言を計算しない)"""
    st = _state()
    st["player"]["active_index"] = None
    assert _key(st) == ""
    st["player"]["active_index"] = 9
    assert _key(st) == ""
    print("test_no_active_gives_empty_key OK")


def test_cache_ttl_normal_and_fallback():
    ttl, fb = float(C.ADVICE_RECALC_TTL_SEC), float(C.ADVICE_RECALC_FALLBACK_TTL_SEC)
    assert fb < ttl
    c = K.AdviceCache()
    assert c.needs_recalc("k", 0.0)                       # 空
    c.store("k", {"ok": True, "best": {"kind": "move", "id": "surf"}}, 100.0)
    assert not c.needs_recalc("k", 100.0 + ttl - 0.1)
    assert c.needs_recalc("k", 100.0 + ttl + 0.1)          # 通常の有効期限
    assert c.needs_recalc("k2", 100.5)                     # 鍵が変わった
    # 退避 (予算超過) の結果は短い有効期限
    c.store("k", {"ok": True, "scoring_fallback": "budget"}, 200.0)
    assert c.current_ttl() == fb
    assert not c.needs_recalc("k", 200.0 + fb - 0.1)
    assert c.needs_recalc("k", 200.0 + fb + 0.1)
    # 評価エラーも短い有効期限
    c.store("k", {"ok": False, "reason": "評価エラー: x"}, 300.0, error=True)
    assert c.needs_recalc("k", 300.0 + fb + 0.1)
    # 退避しない結果に戻れば通常の有効期限
    c.store("k", {"ok": True, "scoring_fallback": None}, 400.0)
    assert c.current_ttl() == ttl and not c.needs_recalc("k", 400.0 + fb + 0.1)
    print("test_cache_ttl_normal_and_fallback OK:", ttl, fb)


class _FakeAdvisor:
    """advise_cached が使う Advisor の部分 (resolver・据え置きと安定化の履歴・画面の提案)"""

    def __init__(self):
        self.resolver = None
        self._last_best = None
        self._stab_hist = None
        self.last_common = None

    @staticmethod
    def suggest_check(state):
        return ""


def test_advise_cached_runs_stability_every_time():
    """キャッシュを使ったときも安定化ゲートを通す: 1 回目は確定前 (provisional)、同じ鍵の 2 回目は採点せずに確定。
    後段 (表示・記録) が advice に書き込んでもキャッシュの結果は変わらない。有効期限を超えたら採点し直す"""
    calls = []

    def fake_evaluate(state, resolver=None, **kw):
        calls.append(state.get("turn"))
        return {"ok": True, "actions": [{"kind": "move", "id": "surf", "score": 50.0},
                                        {"kind": "move", "id": "icebeam", "score": 40.0}],
                "best": {"kind": "move", "id": "surf", "score": 50.0}, "position_key": "p", "scoring_mode": "legacy"}

    orig = E.evaluate
    E.evaluate = fake_evaluate
    try:
        adv, cache, st = _FakeAdvisor(), K.AdviceCache(), _state()
        key = _key(st)
        a1, ev1 = K.advise_cached(adv, st, cache, key, 100.0)
        assert ev1 and a1.get("provisional") is True and len(calls) == 1
        a1["text"] = "表示"
        a1["advice_id"] = "x-0001"            # battle_logger.on_advice が書き込む欄
        a2, ev2 = K.advise_cached(adv, st, cache, key, 100.5)
        assert not ev2 and len(calls) == 1      # 採点していない
        assert not a2.get("provisional") and a2["best"]["id"] == "surf"
        assert "text" not in a2 and "advice_id" not in a2
        assert "text" not in cache.result and "advice_id" not in cache.result
        # 有効期限を超えたら採点し直す
        _a3, ev3 = K.advise_cached(adv, st, cache, key, 100.0 + float(C.ADVICE_RECALC_TTL_SEC) + 0.1)
        assert ev3 and len(calls) == 2
        # 鍵が変わったら採点し直す
        st2 = copy.deepcopy(st)
        _opponent_item(st2)
        _a4, ev4 = K.advise_cached(adv, st2, cache, _key(st2), 111.0)
        assert ev4 and len(calls) == 3
    finally:
        E.evaluate = orig
    print("test_advise_cached_runs_stability_every_time OK")


def test_advise_cached_error_short_ttl():
    """評価が例外を出したら評価エラーの結果を返し、短い有効期限で採点し直す (毎フレームは採点しない)"""
    calls = []

    def boom(state, resolver=None, **kw):
        calls.append(1)
        raise RuntimeError("x")

    orig = E.evaluate
    E.evaluate = boom
    try:
        adv, cache, st = _FakeAdvisor(), K.AdviceCache(), _state()
        key = _key(st)
        a, ev = K.advise_cached(adv, st, cache, key, 100.0)
        assert ev and a["ok"] is False and "評価エラー" in a["reason"]
        _a, ev2 = K.advise_cached(adv, st, cache, key, 100.0 + float(C.ADVICE_RECALC_FALLBACK_TTL_SEC) - 0.1)
        assert not ev2 and len(calls) == 1
        _a, ev3 = K.advise_cached(adv, st, cache, key, 100.0 + float(C.ADVICE_RECALC_FALLBACK_TTL_SEC) + 0.1)
        assert ev3 and len(calls) == 2
    finally:
        E.evaluate = orig
    print("test_advise_cached_error_short_ttl OK")


def test_advise_cached_matches_advisor_advise():
    """advise_cached (採点 + 据え置き + 安定化ゲート + 画面の提案) が Advisor.advise と同じ結果を返す (同じ採点の戻り値で 3 回)。
    advice_key は Advisor.advise の前半と後半を分けて呼ぶので、advisor/service.py の変更で食い違ったらここで気づく"""
    Advisor = SV.Advisor
    seq = iter(["surf", "icebeam", "icebeam", "surf", "icebeam", "icebeam"])

    def fake_evaluate(state, resolver=None, **kw):
        b = next(seq)
        acts = [{"kind": "move", "id": b, "score": 50.0},
                {"kind": "move", "id": "surf" if b == "icebeam" else "icebeam", "score": 47.0}]
        return {"ok": True, "actions": acts, "best": acts[0], "position_key": "p", "scoring_mode": "legacy"}

    orig, orig_sv = E.evaluate, SV.evaluate
    E.evaluate = SV.evaluate = fake_evaluate
    try:
        st = _state()
        ref, mine = Advisor(), Advisor()
        ref._resolver = mine._resolver = object()   # NameResolver を作らない (fake_evaluate は使わない)
        cache = K.AdviceCache()
        ref_out, my_out = [], []
        for i in range(3):
            ref_out.append(ref.advise(st))
        for i in range(3):
            # 鍵は毎回変える (毎回採点させる)。ターンは同じ (据え置き・安定化ゲートが効く)
            a, ev = K.advise_cached(mine, copy.deepcopy(st), cache, f"k{i}", 100.0 + i)
            assert ev
            my_out.append(a)
        strip = lambda a: {k: v for k, v in a.items() if k != "suggestion"}
        assert [strip(a) for a in ref_out] == [strip(a) for a in my_out], (ref_out, my_out)
        assert [a.get("suggestion") for a in ref_out] == [a.get("suggestion") for a in my_out]
    finally:
        E.evaluate, SV.evaluate = orig, orig_sv
    print("test_advise_cached_matches_advisor_advise OK")


# --- 10/15 の実戦ログ (レビュワーの検査 proposal_key_check.json と同じ形) ---
REAL_LOG = Path("logs") / "battles" / "battle_20261015_100950.jsonl"


def _old_key_v1(state: dict) -> str:
    """旧鍵 (server._advice_key、版 1。2026-10-19 に置き換える前の本文そのまま。比較のためだけに残す)"""
    try:
        me = state["player"]["party"][state["player"]["active_index"]]
        opp_idx = state["opponent"]["active_index"]
        opp = state["opponent"]["party"][opp_idx] if opp_idx is not None else {}
        return json.dumps([
            state["scene"], me.get("species_id"), me.get("hp_percent"),
            [m.get("pp") for m in me.get("moves", [])],
            opp.get("species_id"), opp.get("hp_percent"),
            state["field"], me.get("boosts"), opp.get("boosts"),
        ], ensure_ascii=False)
    except Exception:
        return ""


def _find_real_log():
    roots = [Path(__file__).resolve().parent.parent]
    try:   # git worktree からは本体の作業ツリーのログを読む (ログはコミットしない)
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                                text=True, cwd=roots[0], timeout=10).stdout.strip()
        if common:
            roots.append(Path(common).parent)
    except Exception:
        pass
    return next((r / REAL_LOG for r in roots if (r / REAL_LOG).exists()), None)


def test_real_log_state_mutations():
    path = _find_real_log()
    if path is None:
        print(f"test_real_log_state_mutations SKIP: {REAL_LOG} が無い (ログはコミットしない。CI では実行しない)")
        return
    from tools.real_accept import _engine_state
    from vision.normalize import NameResolver
    resolver = NameResolver()
    row = None
    for line in path.open(encoding="utf-8"):
        d = json.loads(line)
        if d.get("type") == "advice" and d.get("kind") == "battle" and d.get("state") \
                and d["state"].get("player", {}).get("active") is not None \
                and d["state"].get("opponent", {}).get("active") is not None:
            row = d
            break
    assert row is not None, "対戦の advice 行 (両者の場の個体あり) が無い"
    st = _engine_state(row, resolver)
    now = row["t"]
    k_new, k_old = _key(st, now=now), _old_key_v1(st)
    assert k_new and k_old
    results = {}
    for name, fn in MUTATIONS.items():
        m = copy.deepcopy(st)
        fn(m)
        results[name] = (_old_key_v1(m) == k_old, _key(m, now=now) == k_new)
    # 旧鍵ではレビュワーの検査と同じく 6 種類とも鍵が変わらない (検査の再現)。新しい鍵では全部変わる
    assert all(old_same for old_same, _ in results.values()), results
    assert not any(new_same for _, new_same in results.values()), results
    # 時刻の微小な差では変わらない
    assert _key(st, now=now + 0.2) == k_new
    print(f"test_real_log_state_mutations OK: {path.name} turn={row.get('turn')} "
          + ", ".join(f"{k}: 旧鍵 同じ / 新鍵 変化" for k in results))


def main() -> None:
    test_reviewer_mutations_change_key()
    test_other_scoring_inputs_change_key()
    test_hp_age_crosses_grain()
    test_unrelated_changes_keep_key()
    test_settings_and_versions_change_key()
    test_no_active_gives_empty_key()
    test_cache_ttl_normal_and_fallback()
    test_advise_cached_runs_stability_every_time()
    test_advise_cached_error_short_ttl()
    test_advise_cached_matches_advisor_advise()
    test_real_log_state_mutations()
    print("ALL OK")


if __name__ == "__main__":
    main()
