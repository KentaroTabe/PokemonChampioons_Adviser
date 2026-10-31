"""版の固定の bundle (2026-10-31 追加レビュー判断 2・4、advisor/pin_bundle.py・tools/inputs_pin.make_pin_bundle・
tools/advice_mode --freeze-bundle / --bundle-pin) のテスト。

- (a) bundle の作成と manifest の形 (全ファイルの sha256、コード・設定・Showdown の版・規制、選出モデルの種別と対応する構築、
  使用率 DB は元と同じスキーマで必要な snapshot だけ)。CLI の --freeze-bundle は 1 行を出す
- (b) 固定後に元 (埋め込み・meta・選出モデル・RL 方策・使用率 DB・バンク・登録) を変えても、推論の結果と version 行の sha が変わらない
- (c) bundle の欠損・改変・個別の鍵との矛盾で InputsPinError (起動時の検査・--resolve --bundle-pin も止まる)
- (d) 使用率は抽出した DB だけを読む (元 DB に新しい snapshot を足しても・行を直しても変わらない)
- (e) 構築専用モデルの適用範囲 (6 体確定前は確認待ち、不一致は止めて理由を残す、順番違いは同じ構築、汎用は可、型の前提の照合、
  ADVICE_PIN_ALLOW_OTHER_TEAM=1 で汎化試験)
- (f) 明示なしは従来どおり (配備先の埋め込み・meta・登録・最新の DB を読む、version 行に bundle の欄なし)
合成のファイルだけを使う (本体の使用率 DB・モデル・登録は読まない)。torch が無い環境 (CI) では選出モデルの推論の値の比較を省き、
固定したモデルを読み込めないときに止まることを確かめる。

    python -m tests.test_pin_bundle
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
from advisor import pin_bundle as PB
from champions_agent.config import PIN_ALLOW_OTHER_TEAM_ENV, PIN_BUNDLE_ENV

REPO = Path(__file__).resolve().parent.parent
TEAM = ["archaludon", "hippowdon", "mimikyu", "pelipper", "rotomwash", "swampert"]     # tests.test_inh_fixes.REGISTRY の 6 体
OPP = ["garchomp", "dragonite", "kingambit"]
OTHER = ["garchomp", "dragonite", "kingambit", "gholdengo", "corviknight", "pelipper"]
PIN_ENV_KEYS = ("ADVICE_USAGE_DB", "ADVICE_USAGE_SNAPSHOT", "ADVICE_REAL_BANK", "ADVICE_REAL_BANK_SHA16", "ADVICE_RL_POLICY",
                "ADVICE_RL_POLICY_SHA16", "ADVICE_SELECTION_MODEL", "ADVICE_SELECTION_MODEL_SHA16", PIN_BUNDLE_ENV,
                PIN_ALLOW_OTHER_TEAM_ENV)


def _raises(exc, fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except exc as e:
        return str(e)
    raise AssertionError(f"{exc.__name__} にならなかった: {getattr(fn, '__name__', fn)}")


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _registry() -> dict:
    from tests.test_inh_fixes import REGISTRY
    return json.loads(json.dumps(REGISTRY, ensure_ascii=False))


def _emb_doc(scale: float) -> dict:
    from champions_agent.agent.selection_model import EMB_DIM
    vec = {}
    for i, s in enumerate(TEAM + OPP + ["gholdengo", "corviknight"]):
        vec[s] = [round(scale * ((i + 1) * 0.07 + j * 0.013) % 1.0, 4) for j in range(EMB_DIM)]
    return {"functional": {"basis": ["x"] * EMB_DIM, "vectors": vec}}


def _write_model(path: Path, seed: int) -> None:
    """選出モデルの .pt (torch があれば本物の重み、無ければ読み込めないバイト列)"""
    if _has_torch():
        import torch
        from champions_agent.agent.selection_model import make_net
        torch.manual_seed(seed)
        torch.save(make_net().state_dict(), path)
    else:
        path.write_bytes(f"not-a-model-{seed}".encode())


def _sources(d: Path) -> dict:
    """写し元 (合成): RL 方策・選出モデル・埋め込み・meta・使用率 DB・バンク・登録"""
    from tests.test_inputs_pin import _bank, _make_db
    m = d / "models"
    m.mkdir(parents=True)
    (m / "rl.zip").write_bytes(b"rl-policy-v1")
    _write_model(m / "selection_model.pt", 1)
    (m / "selection_model_emb.json").write_text(json.dumps(_emb_doc(1.0)), encoding="utf-8")
    (m / "selection_model_meta.json").write_text(json.dumps({"teams": [sorted(TEAM)]}), encoding="utf-8")
    reg = d / "my_team.json"
    reg.write_text(json.dumps(_registry(), ensure_ascii=False), encoding="utf-8")
    return {"rl_policy": m / "rl.zip", "selection_model": m / "selection_model.pt", "selection_emb": m / "selection_model_emb.json",
            "selection_meta": m / "selection_model_meta.json", "registry": reg, "usage_db": _make_db(d / "usage.sqlite3"),
            "bank": _bank(d / "bank.json", 30)}


def _selection(kind: str, team=None, premise=None) -> dict:
    from champions_agent.agent.selection_dispatch import features_version
    return {"kind": kind, "team": sorted(team) if team else None,
            "sets_premise": premise or {"fixed": None, "source": None, "sets": None}, "source_id": "test", "source_path": None,
            "source_manifest": None, "features": features_version(), "regulation": "gen9championsbssregmc"}


def _make(d: Path, src: dict, kind: str = "team", team=None, premise=None, name: str = "b1") -> Path:
    from tools import advice_mode as AM
    r = AM.freeze_bundle(d / "pinned", rl_policy=src["rl_policy"], selection_model=src["selection_model"],
                         selection_emb=src["selection_emb"], selection_meta=src["selection_meta"], selection_cond=None,
                         registry=src["registry"], selection=_selection(kind, team or (TEAM if kind == "team" else None), premise),
                         usage_db=src["usage_db"], bank=src["bank"], stamp=name, showdown_dir=d / "no_showdown")
    return Path(r["dir"])


def _reset_all() -> None:
    from advisor import versions as V
    DP.clear_override()
    DP.clear_model_override()
    PB.reset()
    V._CACHE.update(key=None, value=None, t=0.0)


@contextmanager
def _env(bundle=None, **extra):
    saved = {k: os.environ.get(k) for k in PIN_ENV_KEYS}
    for k in PIN_ENV_KEYS:
        os.environ.pop(k, None)
    if bundle is not None:
        os.environ[PIN_BUNDLE_ENV] = str(bundle)
    os.environ.update({k: str(v) for k, v in extra.items()})
    _reset_all()
    try:
        yield
    finally:
        for k in PIN_ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in saved.items() if v is not None})
        _reset_all()


def _party(species) -> list:
    return [{"species_id": s, "species_ja": s} for s in species]


def _select(my, opp=OPP) -> dict:
    from advisor.selection import attach_model_pick, choose_primary, format_selection_advice
    adv = {"ok": True, "recommend": [{"index": 0, "name": my[0] if my else "?", "lead": True, "mega_holder": False,
                                      "mega_assign": False}], "reason": "規則", "picked": 0, "done": False}
    attach_model_pick(adv, _party(my), _party(opp))
    choose_primary(adv)
    adv["text"] = format_selection_advice(adv)
    return adv


def _usage_view() -> dict:
    """使用率を読む経路の結果 (型予測・タイプの候補・チャンピオンズの種・バンク・メガ石・登録の補完)"""
    from tests.test_inputs_pin import _collect
    from advisor import gimmick as G
    from tools import evolve_teams as EV
    EV._usage_cache = None
    out = _collect()
    out["stone"] = G._stone_usage()
    out["alts"] = EV._usage_alternatives()
    return out


def _version_view() -> dict:
    from advisor import versions as V
    v = V.runtime_versions(refresh=True)
    return {"selection_sha": v["selection_model"]["sha256"], "rl_sha": v["action_policy"]["sha256"],
            "embedding_sha": v["rules"]["embedding_sha256"], "usage_snapshot": v["rules"]["usage_snapshot"],
            "registry_sha": v["team"].get("registry_sha256"), "bank_sha": (v["rules"].get("real_bank") or {}).get("sha16"),
            "version_id": v["version_id"], "pin_bundle": (v["runtime"].get("pin_bundle") or {}).get("manifest_sha16")}


# ------------------------------------------------------------------ 純粋
def test_pure_manifest_and_scope():
    man = {"kind": PB.BUNDLE_KIND, "version": PB.BUNDLE_VERSION,
           "files": {k: {"file": PB.FILES[k], "sha256": "a" * 64, "sha16": "a" * 16} for k in PB.REQUIRED_FILES},
           "selection": {"kind": "general", "features": "v1", "emb_sha16": "a" * 16}}
    man["files"]["usage_db"]["snapshot_id"] = 2
    man["files"]["registry"]["registry_sha16"] = "r" * 16
    man["files"]["selection_meta"] = None
    assert PB.check_bundle_manifest(man) == [], PB.check_bundle_manifest(man)
    assert PB.check_bundle_manifest(man, features_now="v3")[0].startswith("選出の特徴量の版 v1")
    actual = {PB.FILES[k]: "a" * 64 for k in PB.REQUIRED_FILES}
    assert PB.check_bundle_manifest(man, actual) == []
    actual[PB.FILES["usage_db"]] = "b" * 64
    actual.pop(PB.FILES["registry"])
    r = PB.check_bundle_manifest(man, actual)
    assert any("sha256" in x and "usage_bundle" in x for x in r) and any("my_team.json が無い" in x for x in r), r
    bad = json.loads(json.dumps(man))
    bad["files"]["rl_policy"]["file"] = "../../elsewhere.zip"        # bundle の外を指せない
    bad["files"].pop("real_bank")
    bad["selection"] = {"kind": "team", "team": ["a", "b"], "features": "v1"}
    r = PB.check_bundle_manifest(bad)
    assert any("rl_policy のファイル名" in x for x in r) and any("real_bank" in x for x in r) and any("6 体" in x for x in r), r
    assert PB.check_bundle_manifest(None) and PB.check_bundle_manifest({"kind": "x"})
    # bundle の値と個別の鍵の整合
    vals = PB.bundle_values(man, Path("/b"))
    assert vals["usage_db"] == "/b/usage_bundle.sqlite3" and vals["usage_snapshot"] == 2 and vals["rl_policy_sha16"] == "a" * 16
    assert PB.reconcile(vals, {"usage_snapshot": 2, "rl_policy": "/b/x/../rl_policy.zip", "bank_sha16": "A" * 16}) == []
    r = PB.reconcile(vals, {"usage_snapshot": 1, "selection_model": "/other.pt"})
    assert len(r) == 2 and "usage_snapshot=1" in r[0], r
    # 型の比較
    assert PB.sets_differences({"a": {"item": "x", "moves": ["m2", "m1"], "evs": {"hp": 32, "atk": 0}}},
                               {"a": {"item": "x", "moves": ["m1", "m2"], "evs": {"hp": 32}}}) == []
    d = PB.sets_differences({"a": {"item": "x"}, "b": {"item": "y"}}, {"a": {"item": "z"}})
    assert d == ["a.item: 学習時 x / 登録 z", "b: 登録に型が無い"], d
    # 適用範囲
    sel = {"kind": "team", "team": TEAM}
    assert PB.scope_decision({"kind": "general"}, OTHER[:2], False)["state"] == "ok"
    p = PB.scope_decision(sel, TEAM[:5], False)
    assert p["state"] == "pending" and not p["infer"] and "確認待ち" in p["reason"]
    assert PB.scope_decision(sel, list(reversed(TEAM)), True)["state"] == "ok"          # 順番だけの違いは同じ構築
    m = PB.scope_decision(sel, OTHER, True)
    assert m["state"] == "mismatch" and not m["infer"] and "garchomp" in m["reason"] and "止めた" in m["reason"], m
    o = PB.scope_decision(sel, OTHER, True, allow_other=True)
    assert o["state"] == "override" and o["infer"] and PIN_ALLOW_OTHER_TEAM_ENV in o["reason"]
    s = PB.scope_decision(sel, TEAM, True, set_diffs=["mimikyu.item: 学習時 a / 登録 b"])
    assert s["state"] == "mismatch" and "型" in s["reason"]
    assert PB.check_allow_value("1", "x") and not PB.check_allow_value("", "x") and not PB.check_allow_value("0", "x")
    assert "0 / 1" in _raises(ValueError, PB.check_allow_value, "yes", "x")
    print("test_pure_manifest_and_scope OK")


# ------------------------------------------------------------------ (a) 作成と manifest の形
def test_bundle_creation_and_manifest():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b = _make(d, src)
        man = json.loads((b / PB.MANIFEST_FILE).read_text(encoding="utf-8"))
        assert man["kind"] == PB.BUNDLE_KIND and man["version"] == PB.BUNDLE_VERSION and man["regulation"] == "gen9championsbssregmc"
        for k in PB.REQUIRED_FILES + ("selection_meta",):
            e = man["files"][k]
            assert e["file"] == PB.FILES[k] and (b / e["file"]).exists() and len(e["sha256"]) == 64, (k, e)
            want = str(Path(src[{"real_bank": "bank"}.get(k, k)]).resolve())
            assert (e.get("source_db") if k == "usage_db" else e["source"]) == want, (k, e)
        assert man["files"]["selection_cond"] is None
        assert man["files"]["registry"]["registry_sha16"] == __import__("advisor.my_team", fromlist=["x"]).registry_sha(_registry())
        assert man["selection"]["kind"] == "team" and man["selection"]["team"] == sorted(TEAM)
        assert man["selection"]["emb_sha16"] == man["files"]["selection_emb"]["sha16"]
        assert man["code"]["git_commit"] and "dirty" in man["code"]
        assert man["showdown"]["commit"] is None and man["showdown"]["error"]
        assert man["config"]["selection_features"] == man["selection"]["features"] and man["config"]["battle_format"]
        # 使用率: 元と同じスキーマの全表、snapshot は 2 (最新)。usage_snapshot / pokemon_usage は 2 以下、move_usage は 2 だけ
        ub = man["files"]["usage_db"]
        assert ub["snapshot_id"] == 2 and ub["latest_at_pin"] == 2 and ub["rules"]["spread_usage"] == "eq"
        con_s = sqlite3.connect(f"file:{src['usage_db']}?mode=ro", uri=True)
        con_b = sqlite3.connect(f"file:{b / PB.FILES['usage_db']}?mode=ro", uri=True)
        try:
            tables = lambda c: sorted(n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                      if not n.startswith("sqlite_") and n != "bundle_meta")
            assert tables(con_s) == tables(con_b), (tables(con_s), tables(con_b))
            assert [r[0] for r in con_b.execute("SELECT id FROM usage_snapshot ORDER BY id")] == [1, 2]
            assert {r[0] for r in con_b.execute("SELECT DISTINCT snapshot_id FROM move_usage")} == {2}
            assert {r[0] for r in con_b.execute("SELECT DISTINCT snapshot_id FROM pokemon_usage")} == {1, 2}
        finally:
            con_s.close()
            con_b.close()
        assert PB.verify_bundle(b, explicit={})["usage_snapshot"] == 2
        try:
            _make(d, src)
            raise AssertionError("上書きした")
        except FileExistsError:
            pass
        # CLI: --freeze-bundle は 1 行 (ADVICE_PIN_BUNDLE=<dir>) を出し、設定ファイルは書き換えない
        f = d / "a.env"
        f.write_text("ADVICE_SCORING_MODE=legacy\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_", "TRANSITION_SENSITIVITY"))}
        env["PYTHONPATH"] = str(REPO)
        base = [sys.executable, "-B", "-m", "tools.advice_mode", "--freeze-bundle", "--out-dir", str(d / "cli"), "--rl-policy",
                str(src["rl_policy"]), "--selection-model", str(src["selection_model"]), "--selection-emb", str(src["selection_emb"]),
                "--registry", str(src["registry"]), "--usage-db", str(src["usage_db"]), "--bank", str(src["bank"]),
                "--showdown-dir", str(d / "no_showdown"), "--file", str(f)]
        bad = subprocess.run(base, cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
        assert bad.returncode == 2 and "--selection-kind" in bad.stderr, bad
        bad = subprocess.run(base + ["--selection-kind", "team"], cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
        assert bad.returncode == 2 and "--selection-team" in bad.stderr, bad
        out = subprocess.run(base + ["--selection-kind", "team", "--selection-team", ",".join(TEAM)], cwd=str(REPO), env=env,
                             capture_output=True, text=True, timeout=180)
        lines = [ln for ln in out.stdout.splitlines() if not ln.startswith("#")]
        assert out.returncode == 0 and len(lines) == 1 and lines[0].startswith("ADVICE_PIN_BUNDLE="), out
        cli_dir = Path(lines[0].split("=", 1)[1])
        cm = json.loads((cli_dir / PB.MANIFEST_FILE).read_text(encoding="utf-8"))
        assert cm["selection"]["kind"] == "team" and cm["selection"]["source_id"].startswith("explicit:")
        assert cm["files"]["selection_meta"]["source"] == str(src["selection_meta"].resolve())     # モデルと同じ dir の meta
        assert "構築専用" in out.stdout and f.read_text(encoding="utf-8") == "ADVICE_SCORING_MODE=legacy\n"
    print("test_bundle_creation_and_manifest OK")


# ------------------------------------------------------------------ (b) (d) 固定後に元を変えても変わらない
def _mutate_sources(src: dict) -> None:
    from tests.test_inputs_pin import SNAP3, _add_snapshot
    src["rl_policy"].write_bytes(b"rl-policy-v2-changed")
    _write_model(src["selection_model"], 99)
    src["selection_emb"].write_text(json.dumps(_emb_doc(3.7)), encoding="utf-8")
    src["selection_meta"].write_text(json.dumps({"teams": [sorted(OTHER)]}), encoding="utf-8")
    reg = _registry()
    reg["ミミッキュ"]["持ち物"] = "きあいのタスキ"
    src["registry"].write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    src["bank"].write_text(json.dumps({"built_at": "b99", "n_battles": 99, "species": {"garchomp": {"appear": 50, "picked": 1}}}),
                           encoding="utf-8")
    con = sqlite3.connect(str(src["usage_db"]))
    try:
        con.execute("UPDATE move_usage SET usage_percent = usage_percent / 2 WHERE snapshot_id = 2")   # 同じ snapshot の行の修復
        con.execute("UPDATE item_usage SET item_name = 'Garchompite' WHERE snapshot_id = 2 AND pokemon_name = 'Garchomp'")
        con.commit()
        _add_snapshot(con, 3, SNAP3)                                                                  # 新しい snapshot
    finally:
        con.close()


def test_frozen_inputs_invariant():
    from champions_agent.agent import selection_model as SM
    from advisor import my_team as MT
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b = _make(d, src)
        torch_ok = _has_torch()
        with _env(b):
            assert DP.usage_source()["db"] == b / PB.FILES["usage_db"] and DP.usage_source()["snapshot_id"] == 2
            emb0 = SM._emb("mimikyu").tolist()
            teams0 = SM.trained_teams()
            reg0 = MT.registry_sha(MT._load())
            usage0 = _usage_view()
            ver0 = _version_view()
            sel0 = _select(list(reversed(TEAM))) if torch_ok else None
            rt0 = DP.check_runtime()
        assert teams0 == [tuple(sorted(TEAM))] and reg0 == MT.registry_sha(_registry())
        assert ver0["usage_snapshot"] == 2 and ver0["pin_bundle"] == PB.manifest_sha16(b)
        assert ver0["embedding_sha"] == json.loads((b / PB.MANIFEST_FILE).read_text())["files"]["selection_emb"]["sha16"]
        assert rt0["bundle"]["dir"] == str(b) and rt0["usage_snapshot"] == 2 and rt0["selection_model"] == str(b / "selection_model.pt")
        if torch_ok:
            assert sel0["model_pick"]["scope"] == "ok" and sel0["model_pick"]["trained"] and sel0["primary"] == "model", sel0
        _mutate_sources(src)
        with _env(b):
            assert SM._emb("mimikyu").tolist() == emb0
            assert SM.trained_teams() == teams0
            assert MT.registry_sha(MT._load()) == reg0
            usage1 = _usage_view()
            ver1 = _version_view()
            sel1 = _select(list(reversed(TEAM))) if torch_ok else None
        assert usage1 == usage0, (usage0, usage1)
        assert ver1 == ver0, (ver0, ver1)
        if torch_ok:
            assert sel1["model_pick"] == sel0["model_pick"], (sel0["model_pick"], sel1["model_pick"])
        # 対照: 新しく作った bundle では元の変更が入る (固定していなければ変わる入力だった)
        b2 = _make(d, src, name="b2")
        with _env(b2):
            assert SM._emb("mimikyu").tolist() != emb0 and SM.trained_teams() != teams0
            usage2 = _usage_view()
            ver2 = _version_view()
        assert usage2["snapshot"] == 3 and usage2 != usage0
        for k in ("selection_sha", "rl_sha", "embedding_sha", "usage_snapshot", "registry_sha", "bank_sha"):
            assert ver2[k] != ver0[k], k
    print(f"test_frozen_inputs_invariant OK (選出モデルの推論の比較: {'あり' if torch_ok else 'torch なしで省略'})")


def test_usage_reads_only_bundle():
    """(d) 抽出した DB だけを読む: 元 DB に snapshot を足しても、bundle の DB の snapshot は 1・2 のまま、読み先も bundle の DB"""
    from tests.test_inputs_pin import SNAP3, _add_snapshot
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b = _make(d, src)
        con = sqlite3.connect(str(src["usage_db"]))
        _add_snapshot(con, 3, SNAP3)
        con.close()
        with _env(b):
            v = _usage_view()
            assert v["snapshot"] == 2 and v["infer_snapshot"] == 2 and "salamence" not in v["usable"], v
            from advisor import ev_infer as E
            db, snap, pinned = E.data_version()
            assert Path(db) == b / PB.FILES["usage_db"] and snap == 2 and pinned
            # bundle に無い snapshot を個別に指定すると矛盾で止める (元の DB の最新へ退避しない)
        with _env(b, ADVICE_USAGE_SNAPSHOT="3"):
            assert "矛盾" in _raises(DP.InputsPinError, DP.usage_source)
        with _env(b, ADVICE_USAGE_SNAPSHOT="2", ADVICE_USAGE_DB=str(b / PB.FILES["usage_db"])):
            assert DP.usage_source()["snapshot_id"] == 2                    # 同じ値の個別指定は可
    print("test_usage_reads_only_bundle OK")


# ------------------------------------------------------------------ (c) 欠損・改変で止まる
def test_bundle_tamper_stops():
    from champions_agent.agent import selection_model as SM
    from advisor import my_team as MT
    from tools import advice_mode as AM
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b0 = _make(d, src)

        def fresh(name):
            t = d / name
            shutil.copytree(b0, t)
            return t
        # 各ファイルの欠損・改変 → verify_bundle と読み手が止まる
        cases = {"usage_db": lambda: DP.usage_source(), "selection_emb": lambda: SM._emb("mimikyu"),
                 "selection_meta": lambda: SM.trained_teams(), "registry": lambda: MT._load(),
                 "real_bank": lambda: DP.read_bank_bytes(DP.bank_source()),
                 "selection_model": lambda: __import__("champions_agent.agent.selection_dispatch", fromlist=["x"]).advisor_model_path(TEAM),
                 "rl_policy": lambda: DP.check_model_file("rl_policy", DP.model_source("rl_policy")["path"],
                                                          DP.model_source("rl_policy")["sha16"])}
        for i, (key, reader) in enumerate(cases.items()):
            for mode in ("missing", "changed"):
                t = fresh(f"t_{i}_{mode}")
                f = t / PB.FILES[key]
                if mode == "missing":
                    f.unlink()
                else:
                    with open(f, "ab") as fh:
                        fh.write(b"\n ")
                e1 = _raises(DP.InputsPinError, PB.verify_bundle, t, {})
                assert PB.FILES[key] in e1, (key, mode, e1)
                with _env(t):
                    e2 = _raises(DP.InputsPinError, reader)
                    assert ("無い" in e2 or "sha" in e2), (key, mode, e2)
        # manifest の欠損・壊れ・記録の改変
        t = fresh("t_man_missing")
        (t / PB.MANIFEST_FILE).unlink()
        with _env(t):
            assert "manifest.json が無い" in _raises(DP.InputsPinError, DP.usage_source)
            assert "manifest.json が無い" in _raises(DP.InputsPinError, MT._load)
        t = fresh("t_man_broken")
        (t / PB.MANIFEST_FILE).write_text("{broken", encoding="utf-8")
        assert "読めない" in _raises(DP.InputsPinError, PB.verify_bundle, t, {})
        t = fresh("t_man_edit")
        man = json.loads((t / PB.MANIFEST_FILE).read_text(encoding="utf-8"))
        man["files"]["selection_emb"]["sha256"] = "0" * 64
        (t / PB.MANIFEST_FILE).write_text(json.dumps(man), encoding="utf-8")
        assert "sha256" in _raises(DP.InputsPinError, PB.verify_bundle, t, {})
        # 特徴量の版が違う
        assert "特徴量の版" in _raises(DP.InputsPinError, PB.verify_bundle, b0, {}, None, "v3")
        with _env(b0), patch("champions_agent.agent.selection_dispatch.features_version", return_value="v3"):
            assert "特徴量の版" in _raises(DP.InputsPinError, DP.check_runtime)
        # 個別の鍵との矛盾 (一致なら可)
        with _env(b0, ADVICE_RL_POLICY=str(src["rl_policy"])):
            assert "矛盾" in _raises(DP.InputsPinError, DP.model_source, "rl_policy")
            assert _raises(DP.InputsPinError, __import__("advisor.rl_bridge", fromlist=["x"])._load_model)
        with _env(b0, ADVICE_RL_POLICY=str(b0 / "rl_policy.zip"), ADVICE_RL_POLICY_SHA16=PB.verify_bundle(b0, {})["files"]["rl_policy"]):
            assert DP.model_source("rl_policy")["path"] == b0 / "rl_policy.zip"
        # 起動時の検査 (server) と --resolve --bundle-pin
        line = AM.bundle_startup_check({PIN_BUNDLE_ENV: str(b0)}, d / "none.env")
        assert "版の固定 (bundle): 実効値 bundle" in line and "構築専用" in line and PB.manifest_sha16(b0) in line, line
        assert "実効値 なし" in AM.bundle_startup_check({}, d / "none.env")
        assert "bundle" in AM.data_pin_startup_check({PIN_BUNDLE_ENV: str(b0)}, d / "none.env")
        assert "選出モデル" in AM.model_pin_startup_check({PIN_BUNDLE_ENV: str(b0)}, d / "none.env")
        t = fresh("t_start")
        (t / PB.FILES["selection_emb"]).write_text("{}", encoding="utf-8")
        for bad_env in ({PIN_BUNDLE_ENV: str(t)}, {PIN_BUNDLE_ENV: str(b0), "ADVICE_USAGE_SNAPSHOT": "1"},
                        {PIN_BUNDLE_ENV: str(b0), PIN_ALLOW_OTHER_TEAM_ENV: "yes"}):
            try:
                AM.bundle_startup_check(bad_env, d / "none.env")
                raise AssertionError(f"SystemExit にならなかった: {bad_env}")
            except SystemExit as e:
                assert "起動を中止 (版の固定の bundle)" in str(e), (bad_env, e)
        f = d / "b.env"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_", "TRANSITION_SENSITIVITY"))}
        env["PYTHONPATH"] = str(REPO)
        run = lambda *extra: subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--resolve", "--sensitivity",
                                             "--data-pin", "--model-pin", *extra, "--file", str(f), "--root", str(d)],
                                            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
        f.write_text(f"ADVICE_PIN_BUNDLE={b0}\n", encoding="utf-8")
        out = run()
        assert out.returncode == 0 and len(out.stdout.splitlines()) == 13, out          # --bundle-pin なしは従来の 13 行
        out = run("--bundle-pin")
        assert out.returncode == 0 and out.stdout.splitlines()[13:] == [
            f"ADVICE_PIN_BUNDLE={b0}", "ADVICE_PIN_ALLOW_OTHER_TEAM=", "ADVICE_PIN_BUNDLE_SOURCE=file"], out
        f.write_text(f"ADVICE_PIN_BUNDLE={t}\n", encoding="utf-8")
        bad = run("--bundle-pin")
        assert bad.returncode == 2 and "selection_model_emb.json" in bad.stderr, bad
        f.write_text(f"ADVICE_PIN_BUNDLE={b0}\nADVICE_USAGE_SNAPSHOT=1\n", encoding="utf-8")
        bad = run("--bundle-pin")
        assert bad.returncode == 2 and "usage_snapshot=1" in bad.stderr, bad
        f.write_text("ADVICE_PIN_ALLOW_OTHER_TEAM=1\n", encoding="utf-8")
        bad = run("--bundle-pin")
        assert bad.returncode == 2 and "一緒に" in bad.stderr, bad
        f.write_text(f"ADVICE_PIN_BUNDLE={b0}\n", encoding="utf-8")
        show = subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--show", "--file", str(f)], cwd=str(REPO), env=env,
                              capture_output=True, text=True, timeout=180)
        assert show.returncode == 0 and f"版の固定 (bundle) の設定値: bundle {b0}" in show.stdout and "照合: 一致" in show.stdout, show
    print("test_bundle_tamper_stops OK")


def test_advice_env_exports_bundle():
    """advice_env.sh: bundle の鍵を uvicorn 用に export し、bundle が壊れていれば止まる"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b = _make(d, src)
        (d / "config").mkdir()
        (d / "scripts").mkdir()
        shutil.copy2(REPO / "scripts" / "advice_env.sh", d / "scripts" / "advice_env.sh")
        (d / "config" / "advice.local.env").write_text(f"ADVICE_PIN_BUNDLE={b}\nADVICE_PIN_ALLOW_OTHER_TEAM=1\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_", "TRANSITION_SENSITIVITY"))}
        env.update(PYTHONPATH=str(REPO), ADVICE_ENV_PYTHON=sys.executable)
        script = '. scripts/advice_env.sh && load_advice_env && env | grep -E "^ADVICE_PIN_" | sort'
        out = subprocess.run(["bash", "-c", script], cwd=str(d), env=env, capture_output=True, text=True, timeout=180)
        got = [ln for ln in out.stdout.splitlines() if ln.startswith("ADVICE_PIN_")]
        assert out.returncode == 0 and got == ["ADVICE_PIN_ALLOW_OTHER_TEAM=1", f"ADVICE_PIN_BUNDLE={b}",
                                               "ADVICE_PIN_BUNDLE_SOURCE=file"], out
        assert f"版の固定 (bundle): {b}" in out.stdout and "汎化試験" in out.stdout, out.stdout
        (b / PB.FILES["real_bank"]).write_text("{}", encoding="utf-8")
        bad = subprocess.run(["bash", "-c", script], cwd=str(d), env=env, capture_output=True, text=True, timeout=180)
        assert bad.returncode != 0 and "ADVICE_PIN_BUNDLE=" not in bad.stdout, bad
    print("test_advice_env_exports_bundle OK")


# ------------------------------------------------------------------ (e) 構築専用モデルの適用範囲
def test_selection_scope_runtime():
    from advisor import versions as V
    torch_ok = _has_torch()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b_team = _make(d, src, "team", name="team")
        b_gen = _make(d, src, "general", name="general")
        with _env(b_team):
            # 6 体確定前: 確認待ち (推論しない)
            a = _select(TEAM[:5])
            assert a["model_pick_scope"]["state"] == "pending" and "model_pick" not in a and a["primary"] == "rule"
            assert "確認待ち" in a["text"], a["text"]
            # 別の構築: 止めて理由を表示・記録 (別のモデルへ退避しない)
            a = _select(OTHER)
            assert a["model_pick_scope"]["state"] == "mismatch" and "model_pick" not in a and a["primary"] == "rule"
            assert "⛔ 選出モデルを使わない" in a["text"] and "garchomp" in a["text"], a["text"]
            assert a["model_pick_scope"]["origin"] == "test"
            # 順番だけ違う同じ構築: 推論する (torch が無ければ固定したモデルを読めないので止まる)
            if torch_ok:
                a = _select(list(reversed(TEAM)))
                assert a["model_pick_scope"]["state"] == "ok" and a["model_pick"]["model"] == "pinned" and a["model_pick"]["trained"]
            else:
                assert "読み込めない" in _raises(DP.InputsPinError, _select, list(reversed(TEAM)))
            rt = V.runtime_versions(refresh=True)["runtime"]["pin_bundle"]
            assert rt["selection_kind"] == "team" and rt["allow_other_team"] is False and rt["selection_origin"] == "test"
        with _env(b_team, **{PIN_ALLOW_OTHER_TEAM_ENV: "1"}):
            if torch_ok:
                a = _select(OTHER)
                assert a["model_pick_scope"]["state"] == "override" and a["model_pick"]["scope"] == "override"
                assert not a["model_pick"]["trained"] and "汎化試験" in a["text"], a
            else:
                assert PB.selection_scope(OTHER, True)["state"] == "override"
            assert V.runtime_versions(refresh=True)["runtime"]["pin_bundle"]["allow_other_team"] is True
        with _env(b_team, **{PIN_ALLOW_OTHER_TEAM_ENV: "x"}):
            assert "0 / 1" in _raises(DP.InputsPinError, _select, OTHER)
        with _env(b_gen):
            # 汎用モデルは別の構築にも使える (学習チームに入っているかは bundle の meta で決まる)
            assert PB.selection_scope(OTHER, True)["state"] == "ok" and PB.selection_scope(OTHER[:4], False)["state"] == "ok"
            if torch_ok:
                a = _select(OTHER)
                assert a["model_pick_scope"]["state"] == "ok" and a["model_pick"]["trained"] is False
                assert _select(list(reversed(TEAM)))["model_pick"]["trained"] is True
        # 学習時の型の前提: 登録の型と一致なら推論、違えば止める
        prem_sets = PB.registry_sets(_registry(), TEAM)
        assert prem_sets["mimikyu"]["item"] == "lifeorb" and "playrough" in prem_sets["mimikyu"]["moves"], prem_sets["mimikyu"]
        b_ok = _make(d, src, "team", premise={"fixed": True, "source": "test", "sets": prem_sets}, name="prem_ok")
        bad_sets = json.loads(json.dumps(prem_sets))
        bad_sets["mimikyu"]["item"] = "focussash"
        b_bad = _make(d, src, "team", premise={"fixed": True, "source": "test", "sets": bad_sets}, name="prem_bad")
        assert PB.verify_bundle(b_ok, {})["set_diffs"] == [] and PB.verify_bundle(b_bad, {})["set_diffs"]
        with _env(b_ok):
            assert PB.selection_scope(TEAM, True)["state"] == "ok"
        with _env(b_bad):
            s = PB.selection_scope(TEAM, True)
            assert s["state"] == "mismatch" and "mimikyu.item" in s["reason"], s
            a = _select(TEAM)
            assert "model_pick" not in a and "型" in a["text"]
    print(f"test_selection_scope_runtime OK (推論: {'あり' if torch_ok else 'torch なしで省略'})")


# ------------------------------------------------------------------ (f) 明示なしは従来どおり
def test_unpinned_unchanged():
    from champions_agent.agent import selection_dispatch as SD
    from champions_agent.agent import selection_model as SM
    from advisor import my_team as MT
    from advisor import versions as V
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        _make(d, src)
        with _env(None), patch.object(SM, "EMB_PIN_PATH", src["selection_emb"]), patch.object(SM, "META_PATH", src["selection_meta"]), \
                patch.object(MT, "CONFIG_PATH", src["registry"]):
            saved = (SM._pinned_store, dict(SM._emb_cache))
            SM._pinned_store = None
            SM._emb_cache.clear()
            MT._CACHE = None
            try:
                assert SM._emb("mimikyu").tolist() == [float(x) for x in _emb_doc(1.0)["functional"]["vectors"]["mimikyu"]] or \
                    [round(x, 4) for x in SM._emb("mimikyu").tolist()] == _emb_doc(1.0)["functional"]["vectors"]["mimikyu"]
                assert SM.trained_teams() == [tuple(sorted(TEAM))]
                assert MT._load() == _registry()
                assert not DP.usage_source()["pinned"] and not DP.model_source("selection_model")["pinned"]
                assert DP.check_runtime() is None
                assert PB.selection_scope(TEAM, True) is None and PB.registry() is None
                ch = SD.advisor_model_choice(TEAM)
                assert not ch["pinned"] and "origin" not in ch
                v = V.runtime_versions(refresh=True)
                assert "pin_bundle" not in v["runtime"]
                adv = {"ok": True, "recommend": [], "reason": ""}
                from advisor.selection import attach_model_pick
                attach_model_pick(adv, _party(TEAM[:5]), _party(OPP))
                assert "model_pick_scope" not in adv
            finally:
                SM._pinned_store, cache = saved
                SM._emb_cache.clear()
                SM._emb_cache.update(cache)
                MT._CACHE = None
    print("test_unpinned_unchanged OK")


def main() -> None:
    for k in PIN_ENV_KEYS:
        os.environ.pop(k, None)
    test_pure_manifest_and_scope()
    test_bundle_creation_and_manifest()
    test_frozen_inputs_invariant()
    test_usage_reads_only_bundle()
    test_bundle_tamper_stops()
    test_advice_env_exports_bundle()
    test_selection_scope_runtime()
    test_unpinned_unchanged()
    print("ALL OK")


if __name__ == "__main__":
    main()
