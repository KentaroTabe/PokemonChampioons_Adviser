"""正式比較の入力の実体の固定 (2026-10-23、レビュー 2026-10-19 §2) のテスト。

    python -m tests.test_inputs_pin

- advisor/data_pin: 版の明示 (環境変数・上書き)、明示した版が無い・sha16 が違えば止める (最新へ退避しない)
- tools/inputs_pin: bundle の書き出し、ピンの manifest と照合、ログの当時の版、再生の復元の計画
- 回帰: 合成の使用率 DB (snapshot 2 つ) とバンク 2 版で、ピン (bundle・バンクの写し・選出モデル) からの復元結果 (型予測・タイプからの
  候補・チャンピオンズの種の判定・選出の事前分布・モデルの sha) が、新しい snapshot・バンク・配備モデルの差し替え後も変わらない
- tools/usefulness_p1 の起動前検査、tools/usefulness_verdict の事後の検査、tools/arc_remeasure_summary、tools/real_accept のデータの版の行
実データ (本体の使用率 DB・バンク・モデル) は読まない (合成のファイルだけ)。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from advisor import data_pin as DP
from champions_agent.config import REAL_BANK_ENV, REAL_BANK_SHA_ENV, USAGE_DB_ENV, USAGE_SNAPSHOT_ENV
from tools import inputs_pin as IP

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "champions_agent" / "data" / "db" / "schema.sql"

# snapshot ごとの合成データ: pokemon_usage / move_usage / item_usage / ability_usage / teammate_usage
SNAP = {
    1: {"pokemon": [("Garchomp", 30.0, 1), ("Dragonite", 20.0, 2)],
        "moves": {"Garchomp": [("Earthquake", 80.0), ("Dragon Claw", 50.0)], "Dragonite": [("Extreme Speed", 90.0)]},
        "items": {"Garchomp": [("Choice Scarf", 60.0)], "Dragonite": [("Heavy-Duty Boots", 70.0)]},
        "abilities": {"Garchomp": [("Rough Skin", 100.0)], "Dragonite": [("Multiscale", 100.0)]},
        "mates": [("Garchomp", "Dragonite", 40.0)]},
    2: {"pokemon": [("Garchomp", 25.0, 1), ("Dragonite", 25.0, 2), ("Kingambit", 10.0, 3)],
        "moves": {"Garchomp": [("Earthquake", 70.0), ("Stone Edge", 40.0), ("Swords Dance", 30.0)],
                  "Dragonite": [("Extreme Speed", 85.0), ("Dragon Dance", 60.0)], "Kingambit": [("Sucker Punch", 90.0)]},
        "items": {"Garchomp": [("Rocky Helmet", 50.0)], "Dragonite": [("Choice Band", 40.0)], "Kingambit": [("Black Glasses", 50.0)]},
        "abilities": {"Garchomp": [("Rough Skin", 100.0)], "Dragonite": [("Multiscale", 100.0)],
                      "Kingambit": [("Supreme Overlord", 100.0)]},
        "mates": [("Garchomp", "Kingambit", 30.0)]},
}
# 後から足す snapshot (ピンの後に DB が更新された想定): ボーマンダ (ドラゴン/ひこう) が初めて現れ、ガブリアスの技も変わる
SNAP3 = {"pokemon": [("Garchomp", 5.0, 3), ("Dragonite", 40.0, 1), ("Salamence", 30.0, 2)],
         "moves": {"Garchomp": [("Spikes", 90.0)], "Dragonite": [("Roost", 70.0)], "Salamence": [("Dragon Dance", 80.0)]},
         "items": {"Garchomp": [("Focus Sash", 90.0)], "Salamence": [("Salamencite", 100.0)]},
         "abilities": {"Garchomp": [("Rough Skin", 100.0)], "Salamence": [("Intimidate", 100.0)]},
         "mates": [("Salamence", "Garchomp", 50.0)]}


def _add_snapshot(conn, sid: int, d: dict) -> None:
    conn.execute("INSERT INTO usage_snapshot (id, source, format, number_of_battles, fetched_at) VALUES (?, ?, ?, ?, ?)",
                 (sid, "synthetic", "champions-singles", 200, f"2026-10-{sid:02d}"))
    for name, pct, rank in d["pokemon"]:
        conn.execute("INSERT INTO pokemon_usage (snapshot_id, pokemon_name, usage_percent, rank) VALUES (?, ?, ?, ?)",
                     (sid, name, pct, rank))
    for table, col, key in (("move_usage", "move_name", "moves"), ("item_usage", "item_name", "items"),
                            ("ability_usage", "ability_name", "abilities")):
        for name, rows in d[key].items():
            for v, pct in rows:
                conn.execute(f"INSERT INTO {table} (snapshot_id, pokemon_name, {col}, usage_percent) VALUES (?, ?, ?, ?)",
                             (sid, name, v, pct))
    for a, b, pct in d["mates"]:
        conn.execute("INSERT INTO teammate_usage (snapshot_id, pokemon_name, teammate_name, usage_percent) VALUES (?, ?, ?, ?)",
                     (sid, a, b, pct))
    conn.commit()


def _make_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for sid, d in SNAP.items():
        _add_snapshot(conn, sid, d)
    conn.close()
    return path


def _bank(path: Path, picked: int) -> Path:
    path.write_text(json.dumps({"built_at": f"b{picked}", "n_battles": 10,
                                "species": {"garchomp": {"appear": 50, "picked": picked}}}), encoding="utf-8")
    return path


def _collect() -> dict:
    """今の版 (advisor.data_pin) で読んだ復元結果: 型予測・タイプからの候補の索引・チャンピオンズの種の判定・選出の事前分布"""
    from advisor.infer import get_inference
    from advisor.real_prior import load_bank, species_pick_prior
    from advisor.sets import get_predictor
    from advisor.team_advice import champions_usable
    p = get_predictor()
    inf = get_inference()
    bank = load_bank()
    return {"pred": {s: p.predict(s) for s in ("garchomp", "dragonite", "kingambit", "salamence")},
            "snapshot": p._snapshot_id, "infer_snapshot": inf.snapshot_id,
            "index": {tuple(sorted(k)): list(v) for k, v in inf._index.items()},
            "usable": sorted(s for s in ("garchomp", "dragonite", "kingambit", "salamence") if champions_usable(s)),
            "prior": species_pick_prior("garchomp", bank)}


# ------------------------------------------------------------------ advisor/data_pin
def test_spec_from_env_and_env_for():
    assert DP.spec_from_env({}) == {"usage_db": None, "usage_snapshot": None, "bank_path": None, "bank_sha16": None}
    env = DP.env_for(usage_db="/p/u.sqlite3", usage_snapshot=67, bank_path="/p/bank.json", bank_sha16="abcd")
    assert env == {USAGE_DB_ENV: "/p/u.sqlite3", USAGE_SNAPSHOT_ENV: "67", REAL_BANK_ENV: "/p/bank.json", REAL_BANK_SHA_ENV: "abcd"}
    assert DP.spec_from_env(env) == {"usage_db": "/p/u.sqlite3", "usage_snapshot": 67, "bank_path": "/p/bank.json",
                                     "bank_sha16": "abcd"}
    assert DP.env_for() == {}
    try:
        DP.spec_from_env({USAGE_SNAPSHOT_ENV: "latest"})
        raise AssertionError("整数でない snapshot id が通った")
    except DP.InputsPinError:
        pass
    print("test_spec_from_env_and_env_for OK")


def test_pinned_missing_stops_instead_of_latest():
    """明示した版が無い・sha16 が違う・復元できない版なら止める (最新へ黙って退避しない)"""
    from advisor.real_prior import bank_version, load_bank
    from advisor.sets import get_predictor
    with tempfile.TemporaryDirectory() as d:
        db = _make_db(Path(d) / "u.sqlite3")
        bank = _bank(Path(d) / "bank.json", 20)
        try:
            DP.set_override(usage_db=db, usage_snapshot=99)
            for fn in (lambda: get_predictor().predict("garchomp"), DP.check_runtime):
                try:
                    fn()
                    raise AssertionError("無い snapshot で止まらなかった")
                except DP.InputsPinError as e:
                    assert "99" in str(e)
            try:
                from advisor.infer import TypeInference
                TypeInference()
                raise AssertionError("TypeInference が無い snapshot で止まらなかった")
            except DP.InputsPinError:
                pass
            DP.set_override(usage_db=Path(d) / "none.sqlite3", usage_snapshot=1)
            try:
                get_predictor().predict("garchomp")
                raise AssertionError("無い DB で止まらなかった")
            except DP.InputsPinError:
                pass
            DP.set_override(bank_path=bank, bank_sha16="0000000000000000")
            for fn in (load_bank, bank_version, DP.check_runtime):
                try:
                    fn()
                    raise AssertionError("sha16 違いで止まらなかった")
                except DP.InputsPinError as e:
                    assert "sha16" in str(e)
            DP.set_override(bank_blocked="当時のバンク X が手元に無い")
            try:
                load_bank()
                raise AssertionError("blocked で止まらなかった")
            except DP.InputsPinError as e:
                assert "手元に無い" in str(e)
            # 合っていれば読める。check_runtime は照合した値を返す
            sha = DP.sha16_bytes(bank.read_bytes())
            DP.set_override(usage_db=db, usage_snapshot=1, bank_path=bank, bank_sha16=sha)
            assert DP.check_runtime() == {"usage_db": str(db), "usage_snapshot": 1, "bank_path": str(bank), "bank_sha16": sha}
            assert bank_version()["pinned"] is True and bank_version()["sha256"] == sha
            assert get_predictor().predict("garchomp")["moves"][0][0] == "earthquake"
        finally:
            DP.clear_override()
    assert DP.check_runtime() is None or not DP.usage_source()["pinned"]      # 明示が無ければ照合しない
    print("test_pinned_missing_stops_instead_of_latest OK")


# ------------------------------------------------------------------ tools/inputs_pin: 回帰 (ピンからの復元は不変)
def test_restore_from_pin_is_invariant():
    """合成の DB (snapshot 1・2) とバンク v1 でピンを作り、DB に snapshot 3 を足し、バンクを v2 に、配備モデルを差し替えても、
    ピン (bundle・バンクの写し・選出モデル) からの復元結果は変わらない。明示しない (最新) 経路は変わる (検査が効いている確認)"""
    import advisor.infer as I
    import advisor.sets as S
    import advisor.team_advice as T
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        src_db = _make_db(root / "src.sqlite3")
        bank = _bank(root / "bank.json", 20)
        ck = root / "checkpoints"
        ck.mkdir()
        (ck / IP.PIN_MODEL_FILE).write_bytes(b"model-v1")
        (ck / IP.PIN_EMB_FILE).write_text('{"functional": {"vectors": {"a": [1.0]}}}', encoding="utf-8")
        (ck / IP.PIN_META_FILE).write_text('{"teams": []}', encoding="utf-8")
        pin = root / "pin"
        pin.mkdir()
        man = IP.make_inputs_pin(pin, ck / IP.PIN_MODEL_FILE, src_db=src_db, bank=bank)
        assert man["usage_bundle"]["snapshot_id"] == 2 and man["usage_bundle"]["latest_at_pin"] == 2
        assert man["usage_bundle"]["tables"]["move_usage"] == 6 and man["usage_bundle"]["tables"]["pokemon_usage"] == 5
        assert man["usage_bundle"]["tables"]["usage_snapshot"] == 2 and man["usage_bundle"]["tables"]["teammate_usage"] == 2
        assert {k: man[k]["file"] for k in IP.SELECTION_KEYS} == IP.SELECTION_FILES
        assert IP.read_manifest(pin) == man and IP.verify_pin(pin) == []
        env = IP.child_env(man, pin)
        assert env == {USAGE_DB_ENV: str(pin / IP.BUNDLE_FILE), USAGE_SNAPSHOT_ENV: "2", REAL_BANK_ENV: str(pin / IP.BANK_FILE),
                       REAL_BANK_SHA_ENV: man["real_bank"]["sha16"]}
        spec = DP.spec_from_env(env)
        orig = (S.DB_PATH, I.DB_PATH, T.DB_PATH)
        try:
            # 明示しない経路 (最新) の DB を合成の DB に向ける
            S.DB_PATH = I.DB_PATH = T.DB_PATH = src_db
            # 本体の DB を snapshot 2 に固定した結果 = 起動時の最新 (明示しない経路) と同じ
            DP.set_override(usage_db=src_db, usage_snapshot=2, bank_path=bank, bank_sha16=man["real_bank"]["sha16"])
            want = _collect()
            DP.clear_override()
            unpinned = _collect_unpinned(bank)
            assert unpinned["pred"] == want["pred"] and unpinned["index"] == want["index"] and unpinned["usable"] == want["usable"], \
                "明示しない経路と、起動時の最新を明示した結果が違う (既定の挙動が変わった)"
            # ピン (bundle とバンクの写し) からの復元 = 本体の DB を固定した結果
            DP.set_override(usage_db=spec["usage_db"], usage_snapshot=spec["usage_snapshot"], bank_path=spec["bank_path"],
                            bank_sha16=spec["bank_sha16"])
            got = _collect()
            assert got == want, (got, want)
            assert want["snapshot"] == 2 and want["infer_snapshot"] == 2 and want["usable"] == ["dragonite", "garchomp", "kingambit"]
            assert want["pred"]["garchomp"]["moves"][0][0] == "earthquake" and want["prior"] == 20 / 50
            assert not want["pred"]["salamence"]["found"]
            # ---- ピンの後: DB に snapshot 3、バンクを v2、配備モデルを差し替え
            conn = sqlite3.connect(str(src_db))
            _add_snapshot(conn, 3, SNAP3)
            conn.close()
            _bank(bank, 40)
            (ck / IP.PIN_MODEL_FILE).write_bytes(b"model-v2")
            DP.set_override(usage_db=spec["usage_db"], usage_snapshot=spec["usage_snapshot"], bank_path=spec["bank_path"],
                            bank_sha16=spec["bank_sha16"])
            assert _collect() == want                                      # ピンからの復元は不変
            assert IP.verify_pin(pin) == [] and IP.sha256_file(pin / IP.PIN_MODEL_FILE) == man["selection_model"]["sha256"]
            # 本体の DB を snapshot 2 に固定しても同じ (TypeInference の範囲は 2 以下: snapshot 3 のボーマンダは入らない)
            DP.set_override(usage_db=src_db, usage_snapshot=2)
            pinned_src = _collect()
            assert {k: pinned_src[k] for k in ("pred", "index", "usable")} == {k: want[k] for k in ("pred", "index", "usable")}
            # 明示しない経路は最新 (3) を読むので変わる (= この検査は差し替えを検出できる)
            DP.clear_override()
            now = _collect_unpinned(bank)
            assert now["snapshot"] == 3 and now["pred"]["salamence"]["found"] and "salamence" in now["usable"]
            assert now["pred"]["garchomp"] != want["pred"]["garchomp"] and now["index"] != want["index"]
            assert now["prior"] == 40 / 50
            # バンクの写しが書き換えられたら止まる (sha16 違い)
            (pin / IP.BANK_FILE).write_text("{}", encoding="utf-8")
            DP.set_override(bank_path=spec["bank_path"], bank_sha16=spec["bank_sha16"])
            try:
                _collect()
                raise AssertionError("書き換えたバンクの写しで止まらなかった")
            except DP.InputsPinError:
                pass
            assert any(IP.BANK_FILE in r for r in IP.verify_pin(pin))
        finally:
            S.DB_PATH, I.DB_PATH, T.DB_PATH = orig
            DP.clear_override()
            DP.clear_override()
    print("test_restore_from_pin_is_invariant OK")


def _collect_unpinned(bank: Path) -> dict:
    """明示しない経路 (advisor.sets.DB_PATH の最新、バンクは path 渡し)"""
    from advisor.infer import get_inference
    from advisor.real_prior import load_bank, species_pick_prior
    from advisor.sets import get_predictor
    from advisor.team_advice import champions_usable
    assert not DP.usage_source()["pinned"]
    p = get_predictor()
    inf = get_inference()
    b = load_bank(bank)
    return {"pred": {s: p.predict(s) for s in ("garchomp", "dragonite", "kingambit", "salamence")},
            "snapshot": p._snapshot_id, "index": {tuple(sorted(k)): list(v) for k, v in inf._index.items()},
            "usable": sorted(s for s in ("garchomp", "dragonite", "kingambit", "salamence") if champions_usable(s)),
            "prior": species_pick_prior("garchomp", b)}


# ------------------------------------------------------------------ tools/inputs_pin: 純粋
def test_check_manifest_and_child_inputs():
    def e(src, f, sha):
        return IP.file_entry(src, f, sha)
    rec = {"selection_model": e("/ck/selection_model.pt", IP.PIN_MODEL_FILE, "a" * 64),
           "selection_emb": e("/ck/selection_model_emb.json", IP.PIN_EMB_FILE, "b" * 64),
           "selection_meta": e("/ck/selection_model_meta.json", IP.PIN_META_FILE, "c" * 64),
           "usage_bundle": dict(e("/db", IP.BUNDLE_FILE, "d" * 64), snapshot_id=67),
           "real_bank": e("/bank.json", IP.BANK_FILE, "f" * 64)}
    actual = {IP.PIN_MODEL_FILE: "a" * 64, IP.PIN_EMB_FILE: "b" * 64, IP.PIN_META_FILE: "c" * 64, IP.BUNDLE_FILE: "d" * 64,
              IP.BANK_FILE: "f" * 64}
    assert IP.check_manifest(rec, actual) == []
    assert IP.check_manifest(None, actual) and "記録" in IP.check_manifest(None, actual)[0]
    r = IP.check_manifest(rec, dict(actual, **{IP.PIN_MODEL_FILE: "0" * 64}))
    assert len(r) == 1 and IP.PIN_MODEL_FILE in r[0] and "違う" in r[0], r
    r = IP.check_manifest(rec, dict(actual, **{IP.BUNDLE_FILE: None}))
    assert r == [f"ピンに {IP.BUNDLE_FILE} が無い"], r
    # 選出の 3 つの出所の dir が揃わない
    r = IP.check_manifest(dict(rec, selection_meta=e("/other/selection_model_meta.json", IP.PIN_META_FILE, "c" * 64)), actual)
    assert any("揃っていない" in x for x in r), r
    # bundle の記録が無い・モデルの記録が無い
    assert any("bundle" in x for x in IP.check_manifest(dict(rec, usage_bundle=None), actual))
    assert any("選出モデルの実体" in x for x in IP.check_manifest(dict(rec, selection_model=None), actual))
    # 子プロセスの版
    got = {"a": {"usage_snapshot": 67, "bank_sha16": "f" * 16}, "b": {"usage_snapshot": 71, "bank_sha16": "f" * 16}, "c": None}
    reasons, missing = IP.child_inputs_reasons(rec, got)
    assert len(reasons) == 1 and "71" in reasons[0] and missing == ["c"], (reasons, missing)
    assert IP.bundle_where("usage_snapshot", 5) == "id <= 5" and IP.bundle_where("move_usage", 5) == "snapshot_id = 5"
    assert IP.bundle_where("pokemon_usage", 5) == "snapshot_id <= 5" and IP.bundle_where("teammate_usage", 5, "X") == "X"
    print("test_check_manifest_and_child_inputs OK")


def _ver(t, snap, team):
    return {"type": "version", "t": t, "rules": {"usage_snapshot": snap}, "team": {"sha256": team}}


def _sel(t, bank):
    return {"type": "selection_record", "t": t, "opp_pick_pred": {"bank": {"sha256": bank}}}


def test_log_versions_and_restore_plan():
    recs = [_sel(5.0, "bank0"), _ver(10.0, 67, "teamA"), {"type": "advice", "t": 11.0}, _sel(12.0, "bank1"),
            _ver(20.0, 68, "teamA"), {"type": "advice", "t": 21.0}]
    assert IP.log_data_versions(recs) == {"usage_snapshot": [67, 68], "team_sha16": ["teamA"], "bank_sha16": ["bank0", "bank1"]}
    assert IP.data_version_at(recs, 11.0) == {"usage_snapshot": 67, "team_sha16": "teamA", "bank_sha16": "bank0"}
    assert IP.data_version_at(recs, 21.0) == {"usage_snapshot": 68, "team_sha16": "teamA", "bank_sha16": "bank1"}
    assert IP.data_version_at(recs, 1.0) == {"usage_snapshot": 67, "team_sha16": "teamA", "bank_sha16": "bank0"}   # 前に無ければ最初
    assert IP.data_version_at([], 1.0) == {"usage_snapshot": None, "team_sha16": None, "bank_sha16": None}
    want = {"usage_snapshot": 67, "team_sha16": "teamA", "bank_sha16": "bank1"}
    p = IP.restore_plan(want, [60, 67, 75], "teamA", {"bank1": "/b.json"})
    assert p["ok"] and p["usage_snapshot"] == 67 and p["bank_path"] == "/b.json" and p["bank_blocked"] is None, p
    # バンクが手元に無い: 再生は止めない (読もうとしたら止める)
    p = IP.restore_plan(want, [67], "teamA", {"other": "/b.json"})
    assert p["ok"] and p["bank_path"] is None and "bank1" in p["bank_blocked"] and p["notes"], p
    # 判定不能: snapshot の記録が無い / 手元の DB に無い / 登録チームが違う
    for w, snaps, team, frag in ((dict(want, usage_snapshot=None), [67], "teamA", "使用率の snapshot が無い"),
                                 (want, [60], "teamA", "手元の DB に無い"),
                                 (want, [67], "teamB", "今の登録"),
                                 (dict(want, team_sha16=None), [67], "teamA", "登録チームの sha が無い")):
        p = IP.restore_plan(w, snaps, team, {})
        assert not p["ok"] and any(frag in r for r in p["reasons"]) and p["usage_snapshot"] is None, (frag, p)
    # current: 常に ok、当時と違う欄は notes
    p = IP.restore_plan(want, [67, 75], "teamB", {"bank9": "/b.json"}, mode="current", latest_snapshot=75)
    assert p["ok"] and p["used"] == {"usage_snapshot": 75, "team_sha16": "teamB", "bank_sha16": "bank9"} and len(p["notes"]) == 3, p
    try:
        IP.restore_plan(want, [], None, {}, mode="latest")
        raise AssertionError("未知のモードが通った")
    except ValueError:
        pass
    s = IP.summarize_data_versions([{"log": "a", "plan": IP.restore_plan(want, [67], "teamA", {})},
                                    {"log": "a", "plan": IP.restore_plan(want, [67], "teamA", {})},
                                    {"log": "b", "plan": IP.restore_plan(want, [60], "teamA", {})}])
    assert s["n_logs"] == 2 and s["n_restored"] == 1 and s["n_unrestored"] == 1 and s["n_decisions"] == 3
    assert s["n_unrestored_decisions"] == 1 and s["by_log"]["a"][0]["n"] == 2
    print("test_log_versions_and_restore_plan OK")


def test_data_switch_restore_and_current():
    """DataSwitch: restore は当時の snapshot、current は開始時の最新に固定。復元できない決定は ok False で差し替えない"""
    from advisor.sets import get_predictor
    with tempfile.TemporaryDirectory() as d:
        db = _make_db(Path(d) / "u.sqlite3")
        local = {"db": str(db), "snapshots": [1, 2], "latest": 2, "banks": {}, "team_sha16": "teamA"}
        recs = [_ver(10.0, 1, "teamA"), _ver(20.0, 2, "teamB")]
        try:
            ds = IP.DataSwitch("restore", local)
            assert ds.plan("x", recs, 11.0)["ok"] and get_predictor()._connect() is not None and get_predictor()._snapshot_id == 1
            assert not ds.plan("x", recs, 21.0)["ok"]                    # 登録が違う → 復元できない
            assert get_predictor()._snapshot_id == 1                     # 差し替えていない
            sm = ds.summary()
            assert sm["mode"] == "restore" and sm["n_decisions"] == 2 and sm["n_unrestored_decisions"] == 1
            ds.close()
            cur = IP.DataSwitch("current", local)
            assert cur.plan("x", recs, 11.0)["ok"]
            get_predictor()._connect()
            assert get_predictor()._snapshot_id == 2
            cur.close()
        finally:
            DP.clear_override()
    print("test_data_switch_restore_and_current OK")


# ------------------------------------------------------------------ tools/usefulness_p1・usefulness_verdict・arc_remeasure_summary・real_accept
def test_usefulness_p1_inputs_pin_errors():
    from tools import usefulness_p1 as P
    planned = {"planned": True, "selection_model": {"sha16": "a"}, "usage_bundle": {"snapshot_id": 75}, "real_bank": None}
    cmd_planned = {"c": ["py", "--selection-model", P.pin_model_path(P.PIN_PLANNED)]}
    assert P.inputs_pin_errors({"inputs_pin": planned, "commands": cmd_planned}) == []
    errs = P.inputs_pin_errors({"inputs_pin": dict(planned, selection_model=None, usage_bundle=None), "commands": cmd_planned})
    assert len(errs) == 2, errs
    # 配備先を指すコマンドは NG
    errs = P.inputs_pin_errors({"inputs_pin": planned, "commands": {"c": ["py", "--selection-model", "/ck/selection_model.pt"]}})
    assert len(errs) == 1 and "ピンの選出モデル" in errs[0], errs
    real = {"planned": False, "pin_dir": "/pin", "selection_model": {"sha16": "a"}, "check_reasons": []}
    cmd = {"c": ["py", "--selection-model", "/pin/selection_model.pt"]}
    assert P.inputs_pin_errors({"inputs_pin": real, "commands": cmd, "selection_model": {"sha16": "a"}}) == []
    errs = P.inputs_pin_errors({"inputs_pin": dict(real, check_reasons=["ピンに bank.json が無い"]), "commands": cmd,
                                "selection_model": {"sha16": "b"}})
    assert len(errs) == 2 and any("bank.json" in x for x in errs) and any("違う" in x for x in errs), errs
    # inputs_pin の無い条件表 (それより前の形) は検査しない
    assert P.inputs_pin_errors({"commands": cmd}) == [] and P.inputs_pin_errors({"inputs_pin": None}) == []
    assert P.command_selection_model(["a", "--selection-model", "/x"]) == "/x" and P.command_selection_model(["a"]) is None
    # 環境の記録: 版の明示の変数は立っているときだけ
    rec = P.env_record(dict(P.child_environment("/pin", base={}), **DP.env_for(usage_db="/pin/u", usage_snapshot=3)))
    assert rec[USAGE_DB_ENV] == "/pin/u" and rec[USAGE_SNAPSHOT_ENV] == "3" and REAL_BANK_ENV not in rec
    print("test_usefulness_p1_inputs_pin_errors OK")


def test_verdict_post_check_and_arc_summary():
    from tools import arc_remeasure_summary as A
    from tools import usefulness_verdict as V
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = _make_db(root / "src.sqlite3")
        bank = _bank(root / "bank.json", 20)
        (root / IP.PIN_MODEL_FILE).write_bytes(b"m")
        pin = root / "pin"
        pin.mkdir()
        man = IP.make_inputs_pin(pin, root / IP.PIN_MODEL_FILE, src_db=db, bank=bank)
        out = root / "out"
        out.mkdir()
        good = {"usage_snapshot": 2, "bank_sha16": man["real_bank"]["sha16"]}
        (out / "scoring_legacy.json").write_text(json.dumps({"inputs_pin": good}), encoding="utf-8")
        (out / "scoring_transition.json").write_text(json.dumps({"inputs_pin": dict(good, usage_snapshot=3)}), encoding="utf-8")
        cond = {"inputs_pin": dict(man, pin_dir=str(pin), planned=False)}
        reasons, summary = V.inputs_pin_post_check(cond, out, ["scoring:legacy", "scoring:transition"])
        assert len(reasons) == 1 and "scoring:transition" in reasons[0] and summary["usage_snapshot"] == 2, reasons
        (out / "scoring_transition.json").write_text(json.dumps({"inputs_pin": good}), encoding="utf-8")
        assert V.inputs_pin_post_check(cond, out, ["scoring:legacy", "scoring:transition"])[0] == []
        (pin / IP.BUNDLE_FILE).write_bytes(b"broken")
        reasons, _ = V.inputs_pin_post_check(cond, out, ["scoring:legacy", "scoring:transition"])
        assert len(reasons) == 1 and IP.BUNDLE_FILE in reasons[0], reasons
        # inputs_pin の無い条件表・dry-run の見込みは検査しない
        assert V.inputs_pin_post_check({}, out, ["scoring:legacy"]) == ([], None)
        assert V.inputs_pin_post_check({"inputs_pin": {"planned": True}}, out, ["scoring:legacy"]) == ([], None)
    # arc_remeasure_summary: 照合の理由があれば再測定の判定は判定不能、無ければそのまま、inputs_pin が無ければ「固定なし」
    summary = {"smoke": False, "arms": [], "verdicts": [{"verdict": "p9", "new": {"verdict": A.ADOPT}, "changed": True}],
               "baseline": None, "registration_ok": True}
    s1 = A.apply_inputs_pin(summary, {"selection_model": {"sha16": "a"}}, ["ピンに bank.json が無い"])
    assert s1["verdicts"][0]["new"]["verdict"] == A.INPUTS_UNVERIFIED and s1["verdicts"][0]["changed"] is None
    assert summary["verdicts"][0]["new"]["verdict"] == A.ADOPT                    # 元は書き換えない
    s2 = A.apply_inputs_pin(summary, {"selection_model": {"sha16": "a"}}, [])
    assert s2["verdicts"][0]["new"]["verdict"] == A.ADOPT and s2["inputs_pin"]["reasons"] == []
    s3 = A.apply_inputs_pin(summary, None, None)
    assert s3["inputs_pin"] is None and "入力の実体の固定: なし" in A.render(dict(s3, verdicts=[]))
    assert "照合 NG" in A.render(dict(s1, verdicts=[]))
    print("test_verdict_post_check_and_arc_summary OK")


def test_real_accept_data_row():
    """--data-mode restore で復元できない決定は再生せず照合できない決定に数える (正しさは 満たす にならない)。データの版の行"""
    from tools import real_accept as RA
    plan = IP.restore_plan({"usage_snapshot": None, "team_sha16": "t", "bank_sha16": None}, [1], "t", {})
    d = RA.unrestored_decision("battle_x.jsonl", {"advice_id": "x-1", "t": 1.0, "turn": 1}, {"scene": "command"}, plan)
    ok = {"ok": True, "best": {"kind": "move", "id": "a"}, "violations": [], "unconfirmed": [], "elapsed_ms": 1.0,
          "fallback_reason": None, "scoring_used": "legacy", "checks": {"data_restore": "ok"}}
    d_ok = {"log": "battle_y.jsonl", "advice_id": "y-1", "turn": 1, "active": None, "stale": [], "log_best": None,
            "log_mode": "legacy", "modes": {"legacy": ok, "transition": ok}}
    s = RA.summarize([d_ok, d])
    v = s["modes"]["legacy"]["valid"]
    assert v["n_eval"] == 2 and v["n_unknown_decisions"] == 1 and v["unconfirmed"]["data_restore"] == 1
    assert v["checks"]["data_restore"] == {"ok": 1, "ng": 0, "unknown": 1}
    data = {"mode": "restore", "n_decisions": 2, "n_unrestored_decisions": 1, "n_logs": 2, "n_restored": 1,
            "by_log": {"battle_x.jsonl": [{"ok": False, "reasons": plan["reasons"]}]}}
    rows = {r["item"]: r for r in RA.acceptance(s, {}, {"n_logs": 2, "n_logs_ok": 2}, data=data)}
    corr = [r for r in RA.acceptance(s, {}, {"n_logs": 2, "n_logs_ok": 2}) if r["item"].startswith("正しさ")]
    assert all(r["status"] == RA.PARTIAL for r in corr), corr
    row = rows["入力: 再生のデータの版 (使用率の snapshot・登録チーム・バンク)"]
    assert row["status"] == RA.UNKNOWN and "使用率の snapshot が無い" in row["note"] and row["value"].startswith("1 / 2"), row
    assert RA.data_row(dict(data, n_unrestored_decisions=0, n_restored=2, by_log={}))["status"] == RA.OK
    cur = RA.data_row({"mode": "current", "n_decisions": 2, "local": {"latest_snapshot": 75, "bank_sha16": ["b"], "team_sha16": "t"}})
    assert cur["status"] == RA.REF and "当時の入力ではない" in cur["note"]
    # data を渡さなければ行は足さない (従来の表)
    assert not any(r["item"].startswith("入力:") for r in RA.acceptance(s, {}, {"n_logs": 2, "n_logs_ok": 2}))
    md = RA.data_versions_markdown({"mode": "restore", "local": {"db": "/db", "latest_snapshot": 75, "bank_sha16": [], "team_sha16": "t"},
                                    **IP.summarize_data_versions([{"log": "battle_x.jsonl", "plan": plan}])})
    assert "判定不能" in md and "battle_x.jsonl" in md
    print("test_real_accept_data_row OK")


def test_inputs_audit_pure():
    from tools import inputs_audit as IA
    assert IA.is_formal("p1_20261015_1309") and IA.is_formal("p1_holdout_20261016_0124") and not IA.is_formal("p1_prelim_20261015_1304")
    a = {"x": {"moves": [("a", 60.0)], "items": [("i", 100.0)]}, "y": {"moves": [("b", 50.0)], "items": []}}
    b = {"x": {"moves": [("a", 55.0)], "items": [("j", 100.0)]}, "y": {"moves": [("b", 50.0)], "items": []}, "z": {}}
    assert IA.pred_diff_summary(a, b) == {"n": 2, "n_diff": 1, "n_top_move_diff": 0, "n_top_item_diff": 1}
    cond = {"started_at": "t", "factor": "scoring",
            "selection_model": {"path": "/ck/selection_model.pt", "sha16": "s1",
                                "embedding": {"pin_file": "/pin/emb.json", "pin_sha16": "e1"},
                                "meta": {"pin_file": "/pin/meta.json", "sha16": "m1"}},
            "usage_db": {"latest_snapshot_id": 67}, "real_bank": {"sha16": "b1"}}
    now = {"deployed_sha16": {"/ck/selection_model.pt": "s1"}, "deployed_emb_sha16": "e1", "deployed_meta_sha16": "m2",
           "pin_files": {"/pin/emb.json": "e1", "/pin/meta.json": "m1"}, "snapshots": [60, 67, 75], "latest_snapshot": 75,
           "bank_sha16": "b2", "pred_diff": {67: {"n": 10, "n_diff": 10, "n_top_move_diff": 3, "n_top_item_diff": 2}}}
    r = IA.audit_row("p1_x", cond, now)
    assert r["selection_model"]["match_now"] is True and r["selection_model"]["pt_in_pin"] is False
    assert r["embedding"]["pin_match"] is True and r["embedding"]["deployed_match"] is True
    assert r["meta"]["pin_match"] is True and r["meta"]["deployed_match"] is False
    assert r["usage"]["in_db_now"] is True and r["usage"]["reproducible_without_bundle"] is False and r["usage"]["same_as_latest"] is False
    assert r["usage"]["content_verifiable"] is False and r["real_bank"]["match_now"] is False and r["inputs_pin"] is False
    s = IA.summarize([r])
    assert s == {"n": 1, "selection_match_now": 1, "selection_pt_in_pin": 0, "embedding_pin_match": 1, "meta_pin_match": 1,
                 "snapshot_in_db": 1, "reproducible_without_bundle": 0, "bank_match_now": 0}
    assert "p1_x" in IA.render([r], s, now)
    print("test_inputs_audit_pure OK")


def main():
    test_spec_from_env_and_env_for()
    test_pinned_missing_stops_instead_of_latest()
    test_restore_from_pin_is_invariant()
    test_check_manifest_and_child_inputs()
    test_log_versions_and_restore_plan()
    test_data_switch_restore_and_current()
    test_usefulness_p1_inputs_pin_errors()
    test_verdict_post_check_and_arc_summary()
    test_real_accept_data_row()
    test_inputs_audit_pure()
    print("ALL OK")


if __name__ == "__main__":
    main()
