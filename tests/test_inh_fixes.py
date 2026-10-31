"""次の比較で使う経路の不具合の修正 (2026-10-31、docs/KNOWN_ISSUES.md E-inh-1・E-inh-4、レビュー 2026-10-31 判断 4) のテスト。

- (1) E-inh-1: 人間対 AI の書き出し (tools.export_my_team_showdown) は能力ポイントを点数のまま EVs 欄に書く (測定用の
  build_myteam_text と同じ関数 stat_points_evs)。ローカルの pokemon-showdown (CLI の validate-team) と node があれば、登録の形の
  完全な個体の書き出しが gen9championsbssregmc の検証を通ることも確かめる。無い環境では点数がそのまま出る単体検査だけ
- (2) E-inh-4 使用率の 2 経路: advisor.gimmick._stone_usage と tools.evolve_teams._usage_alternatives は advisor.data_pin の版を読む
  (明示した snapshot だけ、無ければ InputsPinError)。版を明示しないときの結果は変更前の処理 (この中に写した参照実装) と同じ
- (3) E-inh-4 モデルの実体の固定: RL 方策・選出モデルを明示したらそのファイルだけを読み、無い・sha16 が違えば InputsPinError
  (engine・選出の入口でも握りつぶさない)。明示しないときは従来どおりで、退避 (ema → best、登録チーム向け → 配布版) を version 行の
  runtime に残す。advice_mode の鍵 (--resolve --model-pin / --show / --freeze-data --with-models / 起動時の検査)、data_pin.verify_spec
- (4) E-inh-4 欠損・失敗を集計から消さない: 壊れた行の件数 (real_accept / advice_replay / analyze_battles)、scene 不足で外した対戦の
  件数と理由、規定外の勝敗の値は「不明」
実データ (本体の使用率 DB・モデル・ログ) は書き換えない (合成のファイルだけ。Showdown は本体の pokemon-showdown を読み取りで使う)。

    python -m tests.test_inh_fixes
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from advisor import data_pin as DP

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "champions_agent" / "data" / "db" / "schema.sql"

# 登録の形の完全な個体 (技 4・能力ポイント・性格・持ち物・特性。本体の config/my_team.json と同じ形の合成)
REGISTRY = {
    "ペリッパー": {"能力ポイント": {"h": 32, "b": 20, "d": 14}, "性格": "のんき", "持ち物": "しめったいわ", "特性": "あめふらし",
                   "技": ["ぼうふう", "とんぼがえり", "なみのり", "れいとうビーム"]},
    "ラグラージ": {"能力ポイント": {"h": 8, "a": 32, "s": 26}, "性格": "いじっぱり", "持ち物": "ラグラージナイト", "特性": "しめりけ",
                   "技": ["ウェーブタックル", "れいとうパンチ", "じしん", "あまごい"]},
    "ミミッキュ": {"種族ID": "mimikyu", "能力ポイント": {"h": 1, "a": 32, "b": 1, "s": 32}, "性格": "いじっぱり",
                   "持ち物": "いのちのたま", "特性": "ばけのかわ", "技": ["じゃれつく", "かげうち", "つるぎのまい", "シャドークロー"]},
    "ブリジュラス": {"種族ID": "archaludon", "能力ポイント": {"h": 2, "c": 32, "s": 32}, "性格": "おくびょう",
                     "持ち物": "レッドカード", "特性": "がんじょう", "技": ["てっていこうせん", "１０まんボルト", "あくのはどう", "ステルスロック"]},
    "ロトム": {"種族ID": "rotomwash", "能力ポイント": {"h": 2, "c": 32, "s": 32}, "性格": "ひかえめ", "持ち物": "こだわりスカーフ",
               "特性": "ふゆう", "技": ["ハイドロポンプ", "ボルトチェンジ", "おにび", "１０まんボルト"]},
    "カバルドン": {"種族ID": "hippowdon", "能力ポイント": {"h": 32, "b": 2, "d": 32}, "性格": "わんぱく", "持ち物": "オボンのみ",
                   "特性": "すなおこし", "技": ["じしん", "あくび", "ステルスロック", "ふきとばし"]},
}
CHAMPIONS_FORMAT = "gen9championsbssregmc"


def _raises(exc, fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except exc as e:
        return str(e)
    raise AssertionError(f"{exc.__name__} にならなかった: {getattr(fn, '__name__', fn)}")


@contextmanager
def _registry(entries: dict):
    """登録 (advisor.my_team) と現在の 6 体 (current_team_entries) を差し替える。使用率は読まない stub"""
    from advisor import my_team
    import advisor.sets as sets_mod
    import tools.evaluate_team as ev_mod

    class _Pred:
        def predict(self, _sid):
            return {"moves": [], "items": [], "abilities": []}
    saved = (ev_mod.current_team_entries, sets_mod.get_predictor)
    my_team.set_registry_override(entries)
    ev_mod.current_team_entries = lambda roster=None: {k: dict(v) for k, v in entries.items()}
    sets_mod.get_predictor = lambda: _Pred()
    try:
        yield
    finally:
        ev_mod.current_team_entries, sets_mod.get_predictor = saved
        my_team.clear_registry_override()


def _evs_line(text: str):
    return next((ln for ln in text.splitlines() if ln.startswith("EVs: ")), None)


def _showdown_dir():
    """validate-team を動かせる pokemon-showdown (このツリー、無ければ本体のツリー。読み取りだけ)。node か dist が無ければ None"""
    if not shutil.which("node"):
        return None
    cands = [REPO / "pokemon-showdown"]
    try:
        common = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-common-dir"], capture_output=True, text=True,
                                timeout=10).stdout.strip()
        if common:
            cands.append((REPO / common).resolve().parent / "pokemon-showdown")
    except Exception:
        pass
    for c in cands:
        if (c / "pokemon-showdown").exists() and (c / "dist" / "sim" / "team-validator.js").exists():
            return c
    return None


# ------------------------------------------------------------------ (1) E-inh-1
def test_export_writes_stat_points():
    from tools.evaluate_team import build_myteam_text, stat_points_evs
    from tools.export_my_team_showdown import export_team
    assert stat_points_evs({"h": 32, "b": 20, "d": 14}) == "32 HP / 20 Def / 14 SpD"
    assert stat_points_evs({"hp": 2, "SpA": 32, "x": 9}) == "2 HP / 32 SpA"         # 知らない鍵は飛ばす
    assert stat_points_evs(None) == ""
    for ja, entry in REGISTRY.items():
        with _registry({ja: entry}):
            human = export_team()
            measured = build_myteam_text()
        want = "EVs: " + stat_points_evs(entry["能力ポイント"])
        assert _evs_line(human) == want, (ja, human)
        assert _evs_line(measured) == want, (ja, measured)                         # 測定用と同じ規則
        vals = [int(x.split()[0]) for x in _evs_line(human)[5:].split(" / ")]
        assert max(vals) <= 32 and sum(vals) <= 66, (ja, vals)                       # 努力値 (×8) に換算しない
    print("test_export_writes_stat_points OK")


def test_export_passes_showdown_validation():
    """登録の形の完全な個体の書き出しが Showdown の champions の検証を通る (CLI が使える環境だけ。使えなければ単体検査で代える)"""
    from tools.export_my_team_showdown import export_team
    sd = _showdown_dir()
    if sd is None:
        print("test_export_passes_showdown_validation: node / pokemon-showdown が無いので CLI の検証なし "
              "(点数がそのまま出ることは test_export_writes_stat_points で確認済み)")
        return
    from tools.team_build.sets import VALIDATE_RUN_FAILED, validate_team_text
    # champions の BSS は 6 体のチームで検証する (持ち物は 6 体で重複しない)
    with _registry(REGISTRY):
        text = export_team()
    assert text.count("EVs: ") == 6, text
    ok, errs = validate_team_text(text, CHAMPIONS_FORMAT, showdown_dir=sd)
    assert not (errs and str(errs[0]).startswith(VALIDATE_RUN_FAILED)), errs
    assert ok, (text, errs)
    # 対照: ×8 の換算 (修正前の書き出し) は検証で拒否される
    bad = text.replace("EVs: 32 HP / 20 Def / 14 SpD", "EVs: 252 HP / 160 Def / 112 SpD", 1)
    assert bad != text
    ok, errs = validate_team_text(bad, CHAMPIONS_FORMAT, showdown_dir=sd)
    assert not ok and errs and not str(errs[0]).startswith(VALIDATE_RUN_FAILED), errs
    print(f"test_export_passes_showdown_validation OK ({sd})")


# ------------------------------------------------------------------ (2) E-inh-4 使用率の 2 経路
def _usage_db(path: Path) -> Path:
    """合成の使用率 DB: snapshot 1・2 (champions-singles、meta あり)、3 (別の形式)、4 (champions-singles、meta なし = 作成中)"""
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    snaps = {
        1: ("champions-singles", True, {"Garchomp": [("Garchompite", 40.0), ("Choice Scarf", 60.0)], "Salamence": [("Salamencite", 90.0)]},
            {"Garchomp": [("Jolly", "0/32/0/0/2/32", 50.0)]}, {"Garchomp": [("Earthquake", 90.0)]}),
        2: ("champions-singles", True, {"Garchomp": [("Garchompite", 70.0)], "Salamence": [("Salamencite", 50.0)]},
            {"Garchomp": [("Adamant", "2/32/0/0/0/32", 60.0)], "Salamence": [(None, "0/0/0/32/2/32", 40.0)]},
            {"Garchomp": [("Stone Edge", 50.0)], "Salamence": [("Draco Meteor", 80.0)]}),
        3: ("gen9ou", True, {"Garchomp": [("Garchompite", 5.0)]}, {"Garchomp": [("Timid", "0/0/0/32/2/32", 10.0)]},
            {"Garchomp": [("Spikes", 99.0)]}),
        4: ("champions-singles", False, {"Garchomp": [("Garchompite", 1.0)]}, {}, {}),
    }
    for sid, (fmt, meta, items, spreads, moves) in snaps.items():
        conn.execute("INSERT INTO usage_snapshot (id, source, format, number_of_battles, fetched_at) VALUES (?, ?, ?, ?, ?)",
                     (sid, "synthetic", fmt, 100, f"2026-10-{sid:02d}"))
        if meta:
            conn.execute("INSERT INTO meta_sets (snapshot_id, pokemon_name) VALUES (?, ?)", (sid, "Garchomp"))
        for name, rows in items.items():
            for item, pct in rows:
                conn.execute("INSERT INTO item_usage (snapshot_id, pokemon_name, item_name, usage_percent) VALUES (?, ?, ?, ?)",
                             (sid, name, item, pct))
        for name, rows in spreads.items():
            for nat, evs, pct in rows:
                conn.execute("INSERT INTO spread_usage (snapshot_id, pokemon_name, nature, evs, usage_percent) VALUES (?, ?, ?, ?, ?)",
                             (sid, name, nat, evs, pct))
        for name, rows in moves.items():
            for mv, pct in rows:
                conn.execute("INSERT INTO move_usage (snapshot_id, pokemon_name, move_name, usage_percent) VALUES (?, ?, ?, ?)",
                             (sid, name, mv, pct))
    conn.commit()
    conn.close()
    return path


@contextmanager
def _default_db(db: Path):
    """data_pin を通らない既定の DB の読み取り (champions_agent.data.database.get_connection) を合成の DB の読み取り専用に向ける"""
    from champions_agent.data import database as DBM

    @contextmanager
    def _ro(db_path=None):
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()
    orig = DBM.get_connection
    DBM.get_connection = _ro
    DP.clear_override()
    try:
        yield
    finally:
        DBM.get_connection = orig
        DP.clear_override()


def _old_stone_usage(snapshot_id=None) -> dict:
    """変更前 (2026-10-31 より前) の advisor.gimmick._stone_usage の処理の写し (参照実装)"""
    from champions_agent.config import USAGE_TARGET_FORMAT
    from champions_agent.data import database as db
    from advisor.gimmick import _toid, stone_table
    stones = stone_table()
    out: dict = {}
    try:
        with db.get_connection() as conn:
            snap = snapshot_id or db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
            rows = conn.execute("SELECT pokemon_name, item_name, usage_percent FROM item_usage WHERE snapshot_id=?",
                                (snap,)).fetchall()
    except Exception:
        return out
    for r in rows:
        item = _toid(r[1])
        if item in stones:
            out.setdefault(_toid(r[0]), {})[item] = float(r[2] or 0.0)
    return out


def _old_usage_alternatives() -> dict:
    """変更前の tools.evolve_teams._usage_alternatives の処理の写し (参照実装。キャッシュなし)"""
    from champions_agent.config import USAGE_TARGET_FORMAT
    from champions_agent.data import database as db
    from tools.evolve_teams import _to_id
    out: dict = {}
    with db.get_connection() as conn:
        snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
        if snap:
            for r in conn.execute("SELECT pokemon_name, move_name, usage_percent FROM move_usage WHERE snapshot_id = ?", (snap,)):
                out.setdefault(_to_id(r["pokemon_name"]), {"moves": [], "items": [], "spreads": []})["moves"].append(
                    (r["move_name"], r["usage_percent"]))
            for r in conn.execute("SELECT pokemon_name, item_name, usage_percent FROM item_usage WHERE snapshot_id = ?", (snap,)):
                out.setdefault(_to_id(r["pokemon_name"]), {"moves": [], "items": [], "spreads": []})["items"].append(
                    (r["item_name"], r["usage_percent"]))
            for r in conn.execute("SELECT pokemon_name, nature, evs, usage_percent FROM spread_usage WHERE snapshot_id = ?",
                                  (snap,)):
                out.setdefault(_to_id(r["pokemon_name"]), {"moves": [], "items": [], "spreads": []})["spreads"].append(
                    (r["nature"], r["evs"], r["usage_percent"]))
    return out


def test_stone_usage_pinned_and_unchanged():
    from advisor import gimmick as G
    with tempfile.TemporaryDirectory() as td:
        db = _usage_db(Path(td) / "u.sqlite3")
        with _default_db(db):
            # 明示しない: 変更前と同じ (champions-singles の meta のある最新 = snapshot 2)
            got = G._stone_usage(None)
            assert got == _old_stone_usage(None) == {"garchomp": {"garchompite": 70.0}, "salamence": {"salamencite": 50.0}}, got
            assert G._stone_usage(1) == _old_stone_usage(1)                         # 引数の snapshot も従来どおり
            assert G.stone_prior("garchomp")["garchompmega"] == 0.7
            # 明示: その snapshot だけ
            DP.set_override(usage_db=db, usage_snapshot=1)
            assert G._stone_usage(None) == {"garchomp": {"garchompite": 40.0}, "salamence": {"salamencite": 90.0}}
            assert G.stone_prior("salamence")["salamencemega"] == 0.9
            # 明示した DB だけ (snapshot なし): その DB の最新 id (data_pin.resolve_snapshot の規則)
            DP.set_override(usage_db=db)
            assert G._stone_usage(None) == {"garchomp": {"garchompite": 1.0}}
            # 無い snapshot・無い DB → InputsPinError (空の事前分布へ退避しない)
            DP.set_override(usage_db=db, usage_snapshot=99)
            assert "99" in _raises(DP.InputsPinError, G._stone_usage, None)
            assert "99" in _raises(DP.InputsPinError, G.expected_mega, "garchomp")
            DP.set_override(usage_db=Path(td) / "none.sqlite3", usage_snapshot=1)
            assert "無い" in _raises(DP.InputsPinError, G._stone_usage, None)
            # 外せば従来どおりに戻る
            DP.clear_override()
            assert G._stone_usage(None) == _old_stone_usage(None)
    print("test_stone_usage_pinned_and_unchanged OK")


def test_engine_mega_forms_reraises_pin():
    """engine._opp_mega_forms は固定入力の異常だけを外へ出す (他の例外は従来どおり空)"""
    import advisor.engine as ENG
    import advisor.gimmick as G
    opp = {"species_id": "garchomp"}
    with patch.object(G, "expected_mega", side_effect=DP.InputsPinError("test pin")):
        assert "test pin" in _raises(DP.InputsPinError, ENG._opp_mega_forms, opp, {})
    with patch.object(G, "expected_mega", side_effect=ValueError("ふつうの失敗")):
        assert ENG._opp_mega_forms(opp, {}) == {}
    print("test_engine_mega_forms_reraises_pin OK")


def test_usage_alternatives_pinned_and_unchanged():
    import tools.evolve_teams as EV
    with tempfile.TemporaryDirectory() as td:
        db = _usage_db(Path(td) / "u.sqlite3")
        with _default_db(db):
            EV._usage_cache = None
            got = EV._usage_alternatives()
            assert got == _old_usage_alternatives(), got
            assert got["garchomp"]["spreads"] == [("Adamant", "2/32/0/0/0/32", 60.0)] and got["salamence"]["moves"] == [("Draco Meteor", 80.0)]
            DP.set_override(usage_db=db, usage_snapshot=1)
            got1 = EV._usage_alternatives()                                         # 版が変われば作り直す
            assert got1["garchomp"]["spreads"] == [("Jolly", "0/32/0/0/2/32", 50.0)], got1
            assert got1["salamence"] == {"moves": [], "items": [("Salamencite", 90.0)], "spreads": []}, got1
            assert got1["garchomp"]["moves"] == [("Earthquake", 90.0)]
            DP.set_override(usage_db=db, usage_snapshot=99)
            assert "99" in _raises(DP.InputsPinError, EV._usage_alternatives)
            DP.clear_override()
            assert EV._usage_alternatives() == got                                  # 外せば従来どおり
            EV._usage_cache = None                                                  # 従来の消し方も効く
            assert EV._usage_alternatives() == got
            stub = {"meowscarada": {"moves": [], "items": [("focussash", 30.0)], "spreads": []}}
            EV._usage_cache = stub                                                  # 外から入れた値は従来どおりそのまま使う
            assert EV._usage_alternatives() is stub
    EV._usage_cache = None
    print("test_usage_alternatives_pinned_and_unchanged OK")


# ------------------------------------------------------------------ (3) E-inh-4 モデルの実体の固定
@contextmanager
def _models(**kw):
    DP.set_model_override(**kw)
    try:
        yield
    finally:
        DP.clear_model_override()


def test_rl_pinned_missing_or_mismatch_stops():
    import advisor.rl_bridge as RB
    from advisor.search import make_rl_leaf_fn
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "none.zip"
        with _models(rl_policy=missing):
            e1 = _raises(DP.InputsPinError, RB._load_model)
            assert "無い" in e1 and str(missing) in e1, e1
            assert _raises(DP.InputsPinError, RB._load_model) == e1                 # 2 回目も止める (黙って None にしない)
            assert _raises(DP.InputsPinError, RB.policy_hint, {}) == e1
            assert _raises(DP.InputsPinError, make_rl_leaf_fn, {"my_moves": [], "field": None, "turn": 3}) == e1
            from advisor.versions import _rl_detail
            path, sha, loaded, err, fb, pinned = _rl_detail()
            assert path == str(missing) and sha is None and loaded is False and err.startswith("inputs_pin:") and pinned is True
        f = Path(td) / "policy.zip"
        f.write_bytes(b"not a real policy")
        with _models(rl_policy=f, rl_policy_sha16="0" * 16):
            assert "sha16" in _raises(DP.InputsPinError, RB._load_model)
        # 中身は合っているが読み込めない (MaskablePPO の zip でない) も止める (RL なしの助言に黙って退避しない)
        with _models(rl_policy=f, rl_policy_sha16=DP.sha16_bytes(f.read_bytes())):
            assert "読み込めない" in _raises(DP.InputsPinError, RB._load_model)
    RB.reset_model()
    print("test_rl_pinned_missing_or_mismatch_stops OK")


def test_engine_rl_pin_error_result():
    """明示した RL 方策が無いとき、engine.evaluate は第一候補を出さずに固定入力の異常の結果を返す (RL なしの助言にしない)"""
    import advisor.engine as ENG
    import advisor.rl_bridge as RB
    from champions_agent import config as C
    from tests.test_scoring_transition import _revealed_only_pool, _state_171, resolver
    orig_pool = ENG.opponent_move_pool
    ENG.opponent_move_pool = _revealed_only_pool
    try:
        with patch.object(C, "ADVICE_DISPLAY_SEARCH", False), patch.object(RB, "CKPT_DIR", Path("/nonexistent-ckpt")):
            RB.reset_model()
            base = ENG.evaluate(_state_171(), resolver, rl_blend_weight=0, scoring_mode="legacy")
            assert base["ok"] and base["best"], base.get("reason")                    # 明示なし + ファイルなし = 従来どおり RL なし
            with tempfile.TemporaryDirectory() as td, _models(rl_policy=Path(td) / "none.zip"):
                r = ENG.evaluate(_state_171(), resolver, rl_blend_weight=0, scoring_mode="legacy")
            assert DP.is_pin_error_result(r) and not r.get("best") and "RL 方策" in r["error"], r
    finally:
        ENG.opponent_move_pool = orig_pool
        RB.reset_model()
    print("test_engine_rl_pin_error_result OK")


def test_rl_fallback_recorded():
    """明示しないとき: ema が無く best を読む退避を、理由として version 行の action_policy / runtime に残す"""
    import advisor.rl_bridge as RB
    from advisor import versions as V
    order = RB._policy_candidates("balance", "ema")
    assert RB.policy_choice(order, lambda n: True) == (order[0], None)
    name, why = RB.policy_choice(order, lambda n: n == order[1])
    assert name == order[1] and why == f"{order[0]} なし → {order[1]}"
    name, why = RB.policy_choice(order, lambda n: False)
    assert name == order[-1] and why.startswith("方策ファイルなし")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / order[1]).write_bytes(b"dummy best")
        with patch.object(RB, "CKPT_DIR", Path(td)):
            RB.reset_model()
            path, sha, loaded, err, fb, pinned = V._rl_detail()                     # 読み込みの前
            assert path == str(Path(td) / order[1]) and sha == DP.sha16_bytes(b"dummy best") and loaded is None
            assert fb == f"{order[0]} なし → {order[1]}" and pinned is False
            assert RB._load_model() is None                                          # 中身が方策でないので読み込みは失敗 (従来どおり)
            assert RB._model_fallback == fb
            _p, _s, loaded2, err2, fb2, _pin = V._rl_detail()
            assert loaded2 is False and err2 and fb2 == fb
    RB.reset_model()
    doc = V.summarize_versions(None, "/m/deployed.pt", "s1", None, None, ["a", "b", "c", "d", "e", "f"], path, sha, None, None,
                               "t", "g", "d", "e", "v1", rl_fallback_reason=fb, rl_pinned=False,
                               selection_chain_fallback=["registered_model_missing"], selection_pinned=False)
    assert doc["action_policy"]["fallback_reason"] == fb and doc["runtime"]["rl_fallback_reason"] == fb
    assert doc["selection_model"]["fallback_reason"] == "registered_model_missing"
    assert doc["runtime"]["selection_fallback_reason"] == "registered_model_missing" and doc["runtime"]["rl_pinned"] is False
    # 退避の理由は識別に入らない (version_id は同じ)
    doc0 = V.summarize_versions(None, "/m/deployed.pt", "s1", None, None, ["a", "b", "c", "d", "e", "f"], path, sha, None, None,
                                "t", "g", "d", "e", "v1")
    assert doc0["version_id"] == doc["version_id"] and doc0["selection_model"]["fallback_reason"] is None
    # 固定した選出モデル (中身がどれとも違う) の source は pinned
    dp = V.summarize_versions(None, "/p/sel.pt", "s9", "pinned", None, ["a"], None, None, None, None, "t", "g", "d", "e", "v1",
                              selection_pinned=True)
    assert dp["selection_model"]["source"] == "pinned" and dp["runtime"]["selection_pinned"] is True
    print("test_rl_fallback_recorded OK")


def test_rl_pinned_real_policy_records_actual_file():
    """sb3 と本物の方策ファイル (本体のチェックポイントを読み取りだけ) があれば、固定で読み込んだファイルのパスと sha16 が残る"""
    import advisor.rl_bridge as RB
    from advisor import versions as V
    try:
        import sb3_contrib  # noqa: F401
    except Exception:
        print("test_rl_pinned_real_policy_records_actual_file: sb3_contrib が無いので省略 (固定の止まる側は別のテストで確認)")
        return
    sd = _showdown_dir()
    main_root = sd.parent if sd is not None else REPO
    cands = [d / "champions_agent" / "train" / "checkpoints" / n for d in (REPO, main_root)
             for n in ("battle_policy_balance_ema.zip", "battle_policy_balance_best.zip")]
    src = next((c for c in cands if c.exists()), None)
    if src is None:
        print("test_rl_pinned_real_policy_records_actual_file: 方策ファイルが無いので省略")
        return
    sha = DP.sha16_bytes(src.read_bytes())
    with _models(rl_policy=src, rl_policy_sha16=sha):
        m = RB._load_model()
        assert m is not None and RB._model_path == str(src) and RB._model_sha == sha and RB._model_pinned
        path, s, loaded, err, fb, pinned = V._rl_detail()
        assert (path, s, loaded, err, fb, pinned) == (str(src), sha, True, None, None, True)
    RB.reset_model()
    print(f"test_rl_pinned_real_policy_records_actual_file OK ({src.name})")


def test_selection_model_pin_and_fallback():
    import champions_agent.agent.selection_dispatch as SD
    species = ["garchomp", "dragonite", "kingambit", "gholdengo", "rotomwash", "corviknight"]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        reg = d / "registered"
        key = SD.team_key(species)
        (reg / key).mkdir(parents=True)
        (reg / key / "selection_model.pt").write_bytes(b"registered-model")
        (reg / key / "manifest.json").write_text(json.dumps({"species": species}), encoding="utf-8")
        with patch.object(SD, "EXPERIMENT_MARK", d / "no_mark"):
            # 明示しない: 登録チーム向けがあればそれ、無ければ配布版 + 退避の理由
            assert SD.advisor_model_path(species, registered_dir=reg) == (reg / key / "selection_model.pt", SD.REGISTERED_PREFIX + key)
            other = species[:5] + ["pelipper"]
            ch = SD.advisor_model_choice(other, registered_dir=reg)
            assert ch["path"] == SD.deployed_model_path() and ch["source_id"] is None
            assert ch["fallback"] == ["registered_model_missing"] and not ch["pinned"]
            assert SD.advisor_model_path(other, registered_dir=reg) == (SD.deployed_model_path(), None)   # 従来どおりの経路
            assert SD.advisor_model_choice(species[:3], registered_dir=reg)["fallback"] == []         # 6 体そろわない = 退避ではない
            # 明示: 中身が登録チーム向けと同じ写し → そのファイル、source は registered
            copy = d / "pinned_sel.pt"
            copy.write_bytes(b"registered-model")
            sha = DP.sha16_bytes(copy.read_bytes())
            with _models(selection_model=copy, selection_model_sha16=sha):
                assert SD.advisor_model_path(species, registered_dir=reg) == (copy, SD.REGISTERED_PREFIX + key)
                # 別のパーティでも固定したファイルを使う (配布版へ退避しない)。中身が配布版と違えば source は pinned
                p, src = SD.advisor_model_path(other, registered_dir=reg)
                assert p == copy and src == SD.PINNED_SOURCE and SD.model_label(src) == "pinned"
            # 無い・sha16 が違う → InputsPinError (advisor_model_choice は例外を出さず pin_error)
            with _models(selection_model=d / "none.pt"):
                assert "無い" in _raises(DP.InputsPinError, SD.advisor_model_path, species, registered_dir=reg)
                ch = SD.advisor_model_choice(species, registered_dir=reg)
                assert ch["pinned"] and ch["pin_error"] and ch["sha16"] is None
            with _models(selection_model=copy, selection_model_sha16="f" * 16):
                assert "sha16" in _raises(DP.InputsPinError, SD.advisor_model_path, species, registered_dir=reg)
    print("test_selection_model_pin_and_fallback OK")


def test_selection_entry_does_not_swallow_pin():
    """選出の入口 (attach_model_pick → advisor.service.advise_selection) は固定した選出モデルの異常を握りつぶさない"""
    from advisor.selection import attach_model_pick, format_selection_advice
    party = [{"species_id": s, "species_ja": s} for s in ("garchomp", "dragonite", "kingambit", "gholdengo", "rotomwash",
                                                            "corviknight")]
    opp = [{"species_id": s} for s in ("pelipper", "swampert", "mimikyu")]
    with tempfile.TemporaryDirectory() as td, _models(selection_model=Path(td) / "none.pt"):
        adv = {"ok": True, "recommend": []}
        assert "無い" in _raises(DP.InputsPinError, attach_model_pick, adv, party, opp)
        assert "model_pick" not in adv
        from advisor.service import Advisor
        import advisor.selection as S
        with patch.object(S, "advise_selection", return_value={"ok": True, "recommend": [], "picked": [], "done": False,
                                                                 "reason": ""}):
            r = Advisor(resolver=object()).advise_selection({"player": {"party": party}, "opponent": {"party": opp}})
        assert DP.is_pin_error_result(r) and r["kind"] == "selection" and "固定入力の異常" in r["text"], r
    print("test_selection_entry_does_not_swallow_pin OK")


def test_data_pin_verify_and_check_runtime_models():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rl = d / "rl.zip"
        rl.write_bytes(b"rl-bytes")
        sel = d / "sel.pt"
        sel.write_bytes(b"sel-bytes")
        rs, ss = DP.sha16_bytes(b"rl-bytes"), DP.sha16_bytes(b"sel-bytes")
        # 環境変数の読み取り (純粋)
        env = DP.model_env_for(rl_policy=rl, rl_policy_sha16=rs, selection_model="sel.pt")
        assert DP.model_spec_from_env(env) == {"rl_policy": str(rl), "rl_policy_sha16": rs, "selection_model": "sel.pt",
                                               "selection_model_sha16": None}
        assert DP.model_spec_from_env({}) == {k: None for k in DP.MODEL_SPEC_KEYS}
        # verify_spec: モデルの欄があるときだけ確かめて足す (相対パスは cwd から)
        got = DP.verify_spec({"rl_policy": str(rl), "rl_policy_sha16": rs, "selection_model": "sel.pt"}, cwd=d)
        assert got["rl_policy_sha16"] == rs and got["selection_model"] == str(sel) and got["selection_model_sha16"] == ss
        assert "rl_policy" not in DP.verify_spec({})
        assert "sha16" in _raises(DP.InputsPinError, DP.verify_spec, {"rl_policy": str(rl), "rl_policy_sha16": "0" * 16})
        assert "無い" in _raises(DP.InputsPinError, DP.verify_spec, {"selection_model": str(d / "none.pt")})
        assert "パス" in _raises(DP.InputsPinError, DP.verify_model_spec, {"selection_model_sha16": ss})
        # check_runtime: 明示したモデルだけ欄を足す (データの固定の欄はそのまま)
        with _models(rl_policy=rl, rl_policy_sha16=rs):
            assert DP.check_runtime() == {"usage_db": None, "usage_snapshot": None, "bank_path": None, "bank_sha16": None,
                                          "rl_policy": str(rl), "rl_policy_sha16": rs}
        with _models(selection_model=d / "none.pt"):
            assert "無い" in _raises(DP.InputsPinError, DP.check_runtime)
    print("test_data_pin_verify_and_check_runtime_models OK")


def test_advice_mode_model_pin():
    from tools import advice_mode as AM
    K = (AM.MODEL_RL_KEY, AM.MODEL_RL_SHA_KEY, AM.MODEL_SEL_KEY, AM.MODEL_SEL_SHA_KEY)
    assert K == ("ADVICE_RL_POLICY", "ADVICE_RL_POLICY_SHA16", "ADVICE_SELECTION_MODEL", "ADVICE_SELECTION_MODEL_SHA16")
    r = AM.resolve_model_pin(None, {})
    assert r["source"] == "default" and not r["pinned"] and all(v is None for v in r["values"].values())
    sha = "ABCDEF0123456789"
    r = AM.resolve_model_pin(f"ADVICE_RL_POLICY=/p/rl.zip\nADVICE_RL_POLICY_SHA16={sha}\n", {"ADVICE_SELECTION_MODEL": "/p/s.pt"})
    assert r["values"] == {"ADVICE_RL_POLICY": "/p/rl.zip", "ADVICE_RL_POLICY_SHA16": sha.lower(), "ADVICE_SELECTION_MODEL": "/p/s.pt",
                           "ADVICE_SELECTION_MODEL_SHA16": None} and r["source"] == "file+env"
    for text, env, frag in (("ADVICE_RL_POLICY=/a.zip\n", {"ADVICE_RL_POLICY": "/b.zip"}, "矛盾"),
                            ("ADVICE_SELECTION_MODEL_SHA16=0123456789abcdef\n", {}, "一緒に"),
                            ("ADVICE_RL_POLICY=/a.zip\nADVICE_RL_POLICY_SHA16=xyz\n", {}, "16 進")):
        try:
            AM.resolve_model_pin(text, env)
            raise AssertionError(f"エラーにならなかった: {text}")
        except AM.AdviceModeError as e:
            assert frag in str(e), (text, e)
    # データの固定の解決はモデルの鍵を含むファイルでも変わらない (鍵は認める)
    assert AM.resolve_data_pin("ADVICE_RL_POLICY=/a.zip\n", {})["source"] == "default"
    assert "なし" in AM.model_pin_values_text(None)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rl_src = d / "src_rl.zip"
        rl_src.write_bytes(b"rl-source")
        sel_src = d / "src_sel.pt"
        sel_src.write_bytes(b"sel-source")
        # 写し (--freeze-data --with-models の中身)
        m = AM.freeze_models(d / "pinned" / "t1", rl_src, sel_src)
        assert Path(m["rl_policy"]).read_bytes() == b"rl-source" and m["rl_policy_sha16"] == DP.sha16_bytes(b"rl-source")
        assert m["lines"] == [f"ADVICE_RL_POLICY={m['rl_policy']}", f"ADVICE_RL_POLICY_SHA16={m['rl_policy_sha16']}",
                              f"ADVICE_SELECTION_MODEL={m['selection_model']}",
                              f"ADVICE_SELECTION_MODEL_SHA16={m['selection_model_sha16']}"]
        try:
            AM.freeze_models(d / "pinned" / "t1", rl_src, sel_src)
            raise AssertionError("上書きした")
        except FileExistsError:
            pass
        # server の起動時の検査
        env = {"ADVICE_RL_POLICY": m["rl_policy"], "ADVICE_RL_POLICY_SHA16": m["rl_policy_sha16"],
               "ADVICE_SELECTION_MODEL": m["selection_model"], "ADVICE_MODEL_PIN_SOURCE": "file"}
        local = d / "advice.local.env"
        local.write_text("\n".join(m["lines"][:3]) + "\n", encoding="utf-8")
        line = AM.model_pin_startup_check(env, local)
        assert "モデルの固定: 実効値 RL 方策" in line and f"rl_policy sha16 {m['rl_policy_sha16']}" in line and "設定元 file" in line, line
        assert "⚠" not in line, line
        line0 = AM.model_pin_startup_check({}, d / "none.env")
        assert "実効値 なし" in line0 and "設定元 記録なし" in line0, line0
        for bad_env, frag in (({"ADVICE_RL_POLICY": str(d / "none.zip")}, "無い"),
                              ({"ADVICE_RL_POLICY": m["rl_policy"], "ADVICE_RL_POLICY_SHA16": "0" * 16}, "sha16"),
                              ({"ADVICE_SELECTION_MODEL_SHA16": "0" * 16}, "一緒に")):
            try:
                AM.model_pin_startup_check(bad_env, local)
                raise AssertionError(f"SystemExit にならなかった: {bad_env}")
            except SystemExit as e:
                assert "起動を中止 (モデルの固定)" in str(e) and frag in str(e), (bad_env, e)
        # CLI: --resolve --model-pin (相対パスは --root から)。データの固定の 4 行の後に 5 行
        f = d / "a.env"
        f.write_text(f"ADVICE_RL_POLICY=pinned/t1/rl_policy.zip\nADVICE_RL_POLICY_SHA16={m['rl_policy_sha16']}\n", encoding="utf-8")
        base_env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_", "TRANSITION_SENSITIVITY"))}
        base_env["PYTHONPATH"] = str(REPO)
        run = lambda *extra: subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--resolve", "--sensitivity",
                                             "--data-pin", *extra, "--file", str(f), "--root", str(d)], cwd=str(REPO),
                                            env=base_env, capture_output=True, text=True, timeout=120)
        out = run()
        assert out.returncode == 0 and len(out.stdout.splitlines()) == 8, out              # --model-pin なしは従来の 8 行
        out = run("--model-pin")
        assert out.returncode == 0 and out.stdout.splitlines()[8:] == [
            "ADVICE_RL_POLICY=pinned/t1/rl_policy.zip", f"ADVICE_RL_POLICY_SHA16={m['rl_policy_sha16']}",
            "ADVICE_SELECTION_MODEL=", "ADVICE_SELECTION_MODEL_SHA16=", "ADVICE_MODEL_PIN_SOURCE=file"], out
        f.write_text("ADVICE_SELECTION_MODEL=pinned/none.pt\n", encoding="utf-8")
        bad = run("--model-pin")
        assert bad.returncode == 2 and "無い" in bad.stderr and "=" not in bad.stdout, bad
        # --show にモデルの固定の設定値の行
        f.write_text("ADVICE_RL_POLICY=/p/rl.zip\n", encoding="utf-8")
        show = subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--show", "--file", str(f)], cwd=str(REPO),
                              env=base_env, capture_output=True, text=True, timeout=120)
        assert show.returncode == 0 and "モデルの固定の設定値: RL 方策 /p/rl.zip / 選出モデル 固定なし" in show.stdout, show
        # --freeze-data --with-models: データの 3 行 + モデルの 4 行 (写し元は引数で向ける。設定ファイルは書き換えない)
        from tests.test_inputs_pin import _bank, _make_db
        db = _make_db(d / "u.sqlite3")
        bank = _bank(d / "bank.json", 30)
        fz = subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--freeze-data", "--with-models", "--out-dir",
                             str(d / "fz"), "--usage-db", str(db), "--bank", str(bank), "--rl-policy", str(rl_src),
                             "--selection-model", str(sel_src), "--file", str(f)], cwd=str(REPO), env=base_env,
                            capture_output=True, text=True, timeout=120)
        lines = [ln for ln in fz.stdout.splitlines() if not ln.startswith("#")]
        assert fz.returncode == 0 and len(lines) == 7, fz
        assert lines[0] == "ADVICE_USAGE_SNAPSHOT=2" and lines[3].startswith("ADVICE_RL_POLICY=") and \
            lines[6] == f"ADVICE_SELECTION_MODEL_SHA16={DP.sha16_bytes(b'sel-source')}", lines
        assert Path(lines[3].split("=", 1)[1]).parent == Path(lines[1].split("=", 1)[1]).parent      # 同じ時刻のディレクトリ
        assert f.read_text(encoding="utf-8") == "ADVICE_RL_POLICY=/p/rl.zip\n"
    print("test_advice_mode_model_pin OK")


def test_advice_env_exports_model_pin():
    """advice_env.sh: 値のあるモデルの固定の鍵だけを export し、設定元を ADVICE_MODEL_PIN_SOURCE に渡す。不正なら止める"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "config").mkdir()
        (d / "scripts").mkdir()
        shutil.copy2(REPO / "scripts" / "advice_env.sh", d / "scripts" / "advice_env.sh")
        rl = d / "rl.zip"
        rl.write_bytes(b"rl")
        (d / "config" / "advice.local.env").write_text(f"ADVICE_RL_POLICY={rl}\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_", "TRANSITION_SENSITIVITY"))}
        env.update(PYTHONPATH=str(REPO), ADVICE_ENV_PYTHON=sys.executable)
        script = ('. scripts/advice_env.sh && load_advice_env && '
                  'env | grep -E "^(ADVICE_RL_POLICY|ADVICE_SELECTION_MODEL|ADVICE_MODEL_PIN_SOURCE)" | sort')
        out = subprocess.run(["bash", "-c", script], cwd=str(d), env=env, capture_output=True, text=True, timeout=120)
        got = [ln for ln in out.stdout.splitlines() if ln.startswith("ADVICE_")]
        assert out.returncode == 0 and got == ["ADVICE_MODEL_PIN_SOURCE=file", f"ADVICE_RL_POLICY={rl}"], out
        assert f"モデルの固定: RL 方策 {rl}" in out.stdout, out.stdout
        (d / "config" / "advice.local.env").write_text(f"ADVICE_RL_POLICY={d / 'none.zip'}\n", encoding="utf-8")
        bad = subprocess.run(["bash", "-c", script], cwd=str(d), env=env, capture_output=True, text=True, timeout=120)
        assert bad.returncode != 0 and "ADVICE_RL_POLICY=" not in bad.stdout, bad
        (d / "config" / "advice.local.env").write_text("", encoding="utf-8")
        none = subprocess.run(["bash", "-c", script], cwd=str(d), env=env, capture_output=True, text=True, timeout=120)
        got = [ln for ln in none.stdout.splitlines() if ln.startswith("ADVICE_")]
        assert none.returncode == 0 and got == ["ADVICE_MODEL_PIN_SOURCE=default"] and "モデルの固定: なし" in none.stdout, none
    print("test_advice_env_exports_model_pin OK")


# ------------------------------------------------------------------ (4) E-inh-4 欠損・失敗を集計から消さない
def _write_lines(path: Path, lines: list) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_jsonl_lines_pure():
    from tools.jsonl_lines import bad_lines_summary, bad_lines_text, parse_jsonl_text
    recs, bad = parse_jsonl_text('{"type": "a"}\n\n{"type": "b"\n[1, 2]\n  \n{"type": "c"}\n7\n')
    assert [r["type"] for r in recs] == ["a", "c"] and [b["line"] for b in bad] == [3, 4, 7], (recs, bad)
    s = bad_lines_summary({"x.jsonl": bad, "y.jsonl": []})
    assert s == {"n": 3, "files": {"x.jsonl": 3}} and "計 3 行" in bad_lines_text(s) and "x.jsonl 3 行" in bad_lines_text(s)
    assert bad_lines_text({"n": 0, "files": {}}) == ""
    print("test_jsonl_lines_pure OK")


def _log_with_bad_line(path: Path) -> Path:
    rows = [json.dumps({"t": 1.0 + i, "type": "scene", "scene": "command", "state": {}}) for i in range(10)]
    rows.insert(4, '{"t": 4.5, "type": "advice", "kin')        # 改行なしで切れた行
    return _write_lines(path, rows)


def test_real_accept_reports_bad_lines():
    from tools import real_accept as RA
    with tempfile.TemporaryDirectory() as td:
        p = _log_with_bad_line(Path(td) / "battle_x.jsonl")
        recs, bad = RA.load_records_report(p)
        assert len(recs) == 10 and len(bad) == 1 and bad[0]["line"] == 5
        assert len(RA.load_records(p)) == 10                                        # 従来の戻り値の形
        logs = RA._load_logs([p], RA.DECISION_GAP_SEC)
        assert logs[0]["bad_lines"] == 1 and logs[0]["bad_line_numbers"] == [5]
        assert RA.bad_lines_of(logs) == {"n": 1, "files": {"battle_x.jsonl": 1}}
        dry = RA.run([p], None, dry_run=True)
        assert dry["logs"][0]["bad_lines"] == 1                                     # 出力 (result.json の logs) に残る
    print("test_real_accept_reports_bad_lines OK")


def test_advice_replay_reports_bad_lines():
    from tools import advice_replay as AR
    with tempfile.TemporaryDirectory() as td:
        p = _log_with_bad_line(Path(td) / "battle_y.jsonl")
        recs, bad = AR._read_records_report(p)
        assert len(recs) == 10 and len(bad) == 1 and len(AR._read_records(p)) == 10
        import tools.inputs_pin as IP
        local = {"db": str(Path(td) / "none.sqlite3"), "snapshots": [], "latest": None, "banks": {}, "team_sha16": None,
                 "team_registry_sha16": None}
        with patch.object(IP, "local_data", return_value=local):          # 手元の使用率 DB・バンクを読まない
            res = AR.run([p], Path(td) / "out" / "r.json", data_mode="current")
        assert res["bad_lines"] == {"n": 1, "files": {"battle_y.jsonl": 1}}, res.get("bad_lines")
        saved = json.loads((Path(td) / "out" / "r.json").read_text(encoding="utf-8"))
        assert saved["bad_lines"]["n"] == 1
    print("test_advice_replay_reports_bad_lines OK")


def _battle(path: Path, n_scenes: int, outcome, broken: bool = False, t0: float = 1000.0) -> Path:
    rows = [json.dumps({"t": t0 + i, "type": "scene", "scene": "command", "state": {}}) for i in range(n_scenes)]
    if broken:
        rows.append('{"t": 1, "type": "scene", "sce')
    if outcome is not None:
        rows.append(json.dumps({"t": t0 + 99, "type": "outcome", "outcome": outcome}))
    return _write_lines(path, rows)


def test_analyze_battles_keeps_failures_visible():
    from tools import analyze_battles as AB
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _battle(d / "battle_1.jsonl", 5, "win", t0=1000.0)
        _battle(d / "battle_2.jsonl", 5, "loss", broken=True, t0=2000.0)
        _battle(d / "battle_3.jsonl", 1, "loss", t0=3000.0)                         # 断片 (対戦シーン不足) の負け
        _battle(d / "battle_4.jsonl", 5, "won", t0=4000.0)                          # 規定外の勝敗の値
        b2 = AB._parse_battle(str(d / "battle_2.jsonl"))
        assert b2["bad_lines"] == 1 and b2["outcome"] == "loss" and b2["outcome_invalid"] is None
        b4 = AB._parse_battle(str(d / "battle_4.jsonl"))
        assert b4["outcome"] == "unknown" and b4["outcome_invalid"] == "won"
        with patch.object(AB, "BATTLE_DIR", d):
            battles, rep = AB.load_battles_report()
            assert [b["file"] for b in AB.load_battles()] == [b["file"] for b in battles]   # 従来の戻り値の形
        assert [b["file"] for b in battles] == ["battle_1.jsonl", "battle_2.jsonl", "battle_4.jsonl"]
        assert [e["file"] for e in rep["excluded"]] == ["battle_3.jsonl"] and rep["excluded"][0]["outcome"] == "loss"
        assert rep["bad_lines"] == {"n": 1, "files": {"battle_2.jsonl": 1}}
        s = AB.summarize(battles, rep)
        assert (s["n"], s["n_decided"], s["wins"], s["losses"], s["n_unknown"]) == (3, 2, 1, 1, 1), s
        assert s["outcome_invalid"] == [("battle_4.jsonl", "won")] and s["n_excluded"] == 1
        assert s["excluded_outcomes"] == {"loss": 1}
        text = AB.report(s)
        assert "集計から外した対戦: 1戦" in text and "成績・勝率の分母に入れない" in text and "battle_3.jsonl" in text, text
        assert "勝敗不明: 1戦" in text and "'won' は規定外" in text and "battle_2.jsonl 1 行" in text, text
        # 報告なしの summarize (従来の呼び方) でも壊れた行と不明は数える
        s0 = AB.summarize(battles)
        assert s0["bad_lines"]["n"] == 1 and s0["n_unknown"] == 1 and s0["n_excluded"] == 0
        # last の窓: 残した対戦の最古より前に外した対戦は入れない
        with patch.object(AB, "BATTLE_DIR", d):
            _b, rep1 = AB.load_battles_report(last=1)
        assert [b["file"] for b in _b] == ["battle_4.jsonl"] and rep1["excluded"] == [] and rep1["bad_lines"]["n"] == 0
    print("test_analyze_battles_keeps_failures_visible OK")


def main() -> None:
    for k in [k for k in os.environ if k.startswith(("ADVICE_USAGE_", "ADVICE_REAL_BANK", "ADVICE_RL_POLICY",
                                                      "ADVICE_SELECTION_MODEL"))]:
        os.environ.pop(k)
    test_export_writes_stat_points()
    test_export_passes_showdown_validation()
    test_stone_usage_pinned_and_unchanged()
    test_engine_mega_forms_reraises_pin()
    test_usage_alternatives_pinned_and_unchanged()
    test_rl_pinned_missing_or_mismatch_stops()
    test_engine_rl_pin_error_result()
    test_rl_fallback_recorded()
    test_rl_pinned_real_policy_records_actual_file()
    test_selection_model_pin_and_fallback()
    test_selection_entry_does_not_swallow_pin()
    test_data_pin_verify_and_check_runtime_models()
    test_advice_mode_model_pin()
    test_advice_env_exports_model_pin()
    test_jsonl_lines_pure()
    test_real_accept_reports_bad_lines()
    test_advice_replay_reports_bad_lines()
    test_analyze_battles_keeps_failures_visible()
    print("ALL OK")


if __name__ == "__main__":
    main()
