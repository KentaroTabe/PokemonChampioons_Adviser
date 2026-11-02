"""選出モデルの種別を学習履歴で決める (2026-11-02 レビュー判断 1・2、advisor/pin_bundle.py・tools/inputs_pin.make_pin_bundle・
tools/advice_mode --freeze-bundle・champions_agent/train/train_selection の provenance) のテスト。

- (a) 学習時の記録: build_provenance / distributed_provenance / write_model_meta (同じ dir の <stem>_meta.json に model_sha16 と provenance)。
  微調整を見送ったら配布版も general (_finetune_on_myteam が None を返す)
- (b) meta から種別が決まる / meta が無い・provenance が無い・sha 不一致なら「不明」/ 名前 (selection_model.pt・_general・registered) では
  決めない
- (c) 明示は根拠が必須 (CLI の --selection-kind-basis。API で根拠なしは formal_ok False)、manifest に kind_source / kind_basis
- (d) 型の前提の 3 値 (fixed / none / unknown) と適用範囲の判定 (fixed → 型まで、none → 6 体だけ、unknown → 正式比較に使えない)
- (e) formal_ok の条件、manifest の書き換えで止まる (formal_ok の食い違い・meta の判定との食い違い)
- (f) version 行の runtime と tools.real_accept の群の照合 (formal_ok False・記録なしの旧い bundle は不成立)
- (g) 画面表示 (pin_status_payload の形、server の emit、index.html の表示欄、起動ログ・選出の助言の文言)
合成のファイルだけを使う (本体のモデル・meta・登録は読まない)。torch が無い環境 (CI) では推論と微調整の部分を省く。

    python -m tests.test_model_kind
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from advisor import data_pin as DP
from advisor import pin_bundle as PB
from champions_agent.config import PIN_BUNDLE_ENV
from tests.test_pin_bundle import (OTHER, PIN_ENV_KEYS, REPO, TEAM, _env, _has_torch, _make, _raises, _registry, _select,
                                   _sources, _write_model)


def _prov(kind: str = "general", team=None, premise: str = "none", sets=None, gen_sha: str = "g" * 16) -> dict:
    from champions_agent.train.train_selection import build_provenance
    return build_provenance(kind, team, premise, sets, gen_sha, "/data/selection_data.npz", "c0ffee", "2026-11-02 10:00:00")


def _write_meta(model: Path, prov: dict, base=None) -> Path:
    from champions_agent.train.train_selection import write_model_meta
    return write_model_meta(model, prov, base if base is not None else {"teams": [sorted(TEAM)], "n_samples": 10})


def _freeze_meta(d: Path, src: dict, name: str, model: Path = None, meta: Path = None) -> Path:
    """種別を渡さずに (selection_profile の kind None) bundle を作る = meta で決める"""
    from tools import advice_mode as AM
    model = Path(model or src["selection_model"])
    from champions_agent.agent.selection_model import provenance_meta_path
    meta = meta if meta is not None else provenance_meta_path(model)
    sel = AM.selection_profile(model, None)
    r = AM.freeze_bundle(d / "pinned", rl_policy=src["rl_policy"], selection_model=model, selection_emb=src["selection_emb"],
                         selection_meta=meta if Path(meta).exists() else None, selection_cond=None, registry=src["registry"],
                         selection=sel, usage_db=src["usage_db"], bank=src["bank"], stamp=name, showdown_dir=d / "no_showdown")
    return Path(r["dir"])


def _man(b: Path) -> dict:
    return json.loads((b / PB.MANIFEST_FILE).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ (a) 学習時の記録
def test_training_provenance():
    from champions_agent.agent.selection_model import GENERAL_MODEL_PATH, META_PATH, MODEL_PATH, provenance_meta_path
    from champions_agent.train import train_selection as TS
    assert provenance_meta_path(MODEL_PATH) == META_PATH                       # 配布版の meta は従来の META_PATH
    assert provenance_meta_path(GENERAL_MODEL_PATH).name == "selection_model_general_meta.json"
    p = _prov("team_finetuned", list(reversed(TEAM)), "fixed", {"mimikyu": {"item": "lifeorb"}})
    assert p["kind"] == "team_finetuned" and p["finetune_team"] == sorted(TEAM) and p["sets_premise"] == "fixed" and p["sets"]
    assert set(p) == {"kind", "finetune_team", "sets_premise", "sets", "general_model_sha16", "trained_at", "data_path", "code_commit"}
    g = _prov()
    assert g["finetune_team"] is None and g["sets"] is None and g["sets_premise"] == "none"
    for bad in (dict(kind="x"), dict(kind="team_finetuned", team=TEAM[:5]), dict(kind="general", team=TEAM),
                dict(premise="unknown")):
        kw = dict(kind="general", team=None, premise="none")
        kw.update(bad)
        _raises(ValueError, _prov, kw["kind"], kw["team"], kw["premise"])
    # 配布版: 実際に微調整した 6 体があれば team_finetuned (fixed)、見送り・--no-finetune なら general (none)
    dp = TS.distributed_provenance(TEAM, {"mimikyu": {"item": "lifeorb"}}, "a" * 16, "/d.npz", "c1", "t")
    assert dp["kind"] == "team_finetuned" and dp["sets_premise"] == "fixed" and dp["general_model_sha16"] == "a" * 16
    dg = TS.distributed_provenance(None, None, "a" * 16, "/d.npz", "c1", "t")
    assert dg["kind"] == "general" and dg["sets_premise"] == "none" and dg["finetune_team"] is None
    dn = TS.distributed_provenance(TEAM, None, "a" * 16, "/d.npz", "c1", "t")     # 型を読めない → 記録は fixed / sets None
    assert dn["sets_premise"] == "fixed" and dn["sets"] is None
    with tempfile.TemporaryDirectory() as td:
        m = Path(td) / "selection_model_general.pt"
        m.write_bytes(b"weights-1")
        mp = _write_meta(m, g, {"teams": [["a"]], "n_samples": 3, "val": {"mse": 0.2}})
        doc = json.loads(mp.read_text(encoding="utf-8"))
        assert mp.name == "selection_model_general_meta.json" and doc["teams"] == [["a"]] and doc["val"] == {"mse": 0.2}
        assert doc["model_sha16"] == TS._sha16(m) and doc["provenance"] == g
        assert TS.write_model_meta(m, g, None, meta_path=Path(td) / "x.json").name == "x.json"
    # 微調整を見送ったら None (配布版は general と記録される)
    if _has_torch():
        import numpy as np
        with patch.object(TS, "_myteam_species", return_value=set(TEAM)):
            meta = {"team_key": ["|".join(OTHER)] * 10}
            assert TS._finetune_on_myteam(None, np.zeros((10, 2), dtype=np.float32), np.zeros(10, dtype=np.float32), meta, 1e-3) is None
        with patch.object(TS, "_myteam_species", side_effect=RuntimeError("no team")):
            assert TS._finetune_on_myteam(None, None, None, {"team_key": []}, 1e-3) is None
    print(f"test_training_provenance OK (微調整の見送り: {'確認' if _has_torch() else 'torch なしで省略'})")


# ------------------------------------------------------------------ (b) meta から種別が決まる / 名前では決めない
def test_classify_from_meta_pure():
    sha = "ab" * 32
    team_sets = {"mimikyu": {"item": "lifeorb", "moves": ["playrough"]}}
    meta = lambda prov, s=sha[:16]: {"model_sha16": s, "provenance": prov, "teams": []}
    c = PB.classify_from_meta(meta(_prov("team_finetuned", TEAM, "fixed", team_sets)), sha, "m.json")
    assert (c["kind"], c["team"], c["sets_premise"], c["kind_source"]) == ("team", sorted(TEAM), "fixed", "meta"), c
    assert c["premise_sets"] == team_sets and "model_sha16 " + sha[:16] + " 一致" in c["kind_basis"] and "m.json" in c["kind_basis"]
    g = PB.classify_from_meta(meta(_prov()), sha)
    assert (g["kind"], g["team"], g["sets_premise"], g["kind_source"]) == ("general", None, "none", "meta")
    # 不明: meta が無い / provenance が無い / model_sha16 が無い・違う / 形が違う
    for m, why in ((None, "無い"), ({"teams": [["a"]]}, "provenance) が無い"), ({"provenance": _prov()}, "model_sha16 が無い"),
                   (meta(_prov(), "f" * 16), "違う"), (meta(dict(_prov(), kind="mystery")), "読めない"),
                   (meta(dict(_prov("team_finetuned", TEAM, "fixed", team_sets), finetune_team=TEAM[:4])), "6 体")):
        u = PB.classify_from_meta(m, sha)
        assert u["kind"] == "unknown" and u["kind_source"] == "unknown" and why in u["kind_basis"], (m, u)
    assert PB.classify_from_meta(meta(_prov()), None)["kind"] == "unknown"
    # fixed なのに型が無い → 前提は unknown (種別は team のまま)
    nf = PB.classify_from_meta(meta(dict(_prov("team_finetuned", TEAM, "fixed", team_sets), sets=None)), sha)
    assert nf["kind"] == "team" and nf["sets_premise"] == "unknown" and nf["premise_sets"] is None
    # 前提の読み方 (新しい形と旧い形)
    assert PB.premise_of({"sets_premise": "none"}) == ("none", None)
    assert PB.premise_of({"sets_premise": "fixed", "premise_sets": team_sets}) == ("fixed", team_sets)
    assert PB.premise_of({"sets_premise": "fixed", "premise_sets": None}) == ("unknown", None)
    assert PB.premise_of({"sets_premise": {"fixed": True, "sets": team_sets}}) == ("fixed", team_sets)
    assert PB.premise_of({"sets_premise": {"fixed": None, "sets": None}}) == ("unknown", None)
    assert PB.premise_of({}) == ("unknown", None) and PB.premise_of({"sets_premise": "maybe"}) == ("unknown", None)
    print("test_classify_from_meta_pure OK")


def test_bundle_kind_from_meta_not_name():
    from champions_agent.agent import selection_dispatch as SD
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        src["selection_meta"].unlink()                  # 合成の meta (provenance なし) を消す
        # 1) 配布版の名前 (selection_model.pt) でも、学習履歴が team_finetuned なら構築専用 (fixed の型つき)
        sets = PB.registry_sets(_registry(), TEAM)
        _write_meta(src["selection_model"], _prov("team_finetuned", TEAM, "fixed", sets))
        b1 = _freeze_meta(d, src, "dist_team")
        s1 = _man(b1)["selection"]
        assert (s1["kind"], s1["team"], s1["sets_premise"], s1["kind_source"]) == ("team", sorted(TEAM), "fixed", "meta"), s1
        assert s1["formal_ok"] is True and s1["formal_reason"] is None and s1["premise_sets"] == sets
        assert s1["meta_classification"]["kind"] == "team" and s1["meta_classification"]["provenance"]["code_commit"] == "c0ffee"
        assert PB.verify_bundle(b1, {})["formal"] == {"ok": True, "reason": None}
        # 2) 汎用の名前 (selection_model_general.pt) でも、記録が無ければ「不明」(名前で汎用にしない)
        gm = src["selection_model"].with_name("selection_model_general.pt")
        _write_model(gm, 5)
        b2 = _freeze_meta(d, src, "general_no_meta", model=gm)
        s2 = _man(b2)["selection"]
        assert s2["kind"] == "unknown" and s2["kind_source"] == "unknown" and s2["formal_ok"] is False, s2
        assert _man(b2)["files"]["selection_meta"] is None and "無い" in s2["kind_basis"]
        # 3) 汎用モデルに general の記録 → 汎用 (meta は同じ dir の <stem>_meta.json を写す)
        _write_meta(gm, _prov())
        b3 = _freeze_meta(d, src, "general_meta", model=gm)
        m3 = _man(b3)
        assert (m3["selection"]["kind"], m3["selection"]["sets_premise"], m3["selection"]["formal_ok"]) == ("general", "none", True)
        assert m3["files"]["selection_meta"]["source"].endswith("selection_model_general_meta.json")
        # 4) 記録の model_sha16 が違う (モデルだけ作り直した) → 不明
        _write_model(gm, 6)
        s4 = _man(_freeze_meta(d, src, "general_stale", model=gm))["selection"]
        assert s4["kind"] == "unknown" and "違う" in s4["kind_basis"] and s4["formal_ok"] is False
        # 5) 登録チーム向けの場所 (registered:<key>) の名前・由来でも、記録が無ければ不明
        rdir = d / "registered" / "k1"
        rdir.mkdir(parents=True)
        rm = rdir / "selection_model.pt"
        _write_model(rm, 7)
        (rdir / "manifest.json").write_text(json.dumps({"species": TEAM, "regulation": "regX"}), encoding="utf-8")
        from tools import advice_mode as AM
        sel = AM.selection_profile(rm, SD.REGISTERED_PREFIX + "k1")
        assert sel["kind"] is None and sel["source_manifest"]["regulation"] == "regX" and sel["regulation"] == "regX"
        r = AM.freeze_bundle(d / "pinned", rl_policy=src["rl_policy"], selection_model=rm, selection_emb=src["selection_emb"],
                             selection_meta=None, selection_cond=None, registry=src["registry"], selection=sel,
                             usage_db=src["usage_db"], bank=src["bank"], stamp="registered", showdown_dir=d / "no_showdown")
        s5 = _man(Path(r["dir"]))["selection"]
        assert s5["kind"] == "unknown" and s5["team"] is None and s5["source_id"] == "registered:k1" and s5["formal_ok"] is False
    print("test_bundle_kind_from_meta_not_name OK")


# ------------------------------------------------------------------ (c) 明示は根拠が必須
def test_explicit_kind_needs_basis():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        # API: 根拠なしの明示は kind_source explicit・formal_ok False、根拠つきは True (型の前提が分かる場合)
        b = _make(d, src, "general", name="api_nobasis")
        s = _man(b)["selection"]
        assert s["kind"] == "general" and s["kind_source"] == "explicit" and s["kind_basis"] is None and s["formal_ok"] is False
        assert "根拠" in s["formal_reason"] and s["meta_classification"]["kind"] == "unknown"
        # CLI
        f = d / "a.env"
        f.write_text("ADVICE_SCORING_MODE=legacy\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ADVICE_", "TRANSITION_SENSITIVITY"))}
        env["PYTHONPATH"] = str(REPO)

        def cli(out, *extra):
            return subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--freeze-bundle", "--out-dir", str(d / out),
                                   "--rl-policy", str(src["rl_policy"]), "--selection-model", str(src["selection_model"]),
                                   "--selection-emb", str(src["selection_emb"]), "--registry", str(src["registry"]),
                                   "--usage-db", str(src["usage_db"]), "--bank", str(src["bank"]),
                                   "--showdown-dir", str(d / "no_showdown"), "--file", str(f), *extra],
                                  cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
        bad = cli("c1", "--selection-kind", "general")
        assert bad.returncode == 2 and "--selection-kind-basis" in bad.stderr, bad
        bad = cli("c2", "--selection-kind", "general", "--selection-kind-basis", "  ")
        assert bad.returncode == 2 and "--selection-kind-basis" in bad.stderr, bad
        bad = cli("c3", "--selection-team", ",".join(TEAM))
        assert bad.returncode == 2 and "--selection-kind と一緒に" in bad.stderr, bad
        bad = cli("c4", "--selection-kind", "general", "--selection-kind-basis", "x", "--selection-sets", str(f))
        assert bad.returncode == 2 and "--selection-sets" in bad.stderr, bad
        out = cli("c5", "--selection-kind", "general", "--selection-kind-basis", "全構築で学習し微調整なし (学習ログ 11/1)")
        assert out.returncode == 0, out
        cdir = Path([ln for ln in out.stdout.splitlines() if ln.startswith("ADVICE_PIN_BUNDLE=")][0].split("=", 1)[1])
        cs = _man(cdir)["selection"]
        assert cs["kind_source"] == "explicit" and cs["kind_basis"] == "全構築で学習し微調整なし (学習ログ 11/1)" and cs["formal_ok"]
        assert "#   選出モデルの種別: 汎用 / 根拠 explicit: 全構築で学習し微調整なし" in out.stdout
        assert "#   正式比較: 可 (formal_ok True)" in out.stdout
        assert "#   注意: 明示した種別 汎用 が学習履歴の判定 不明" in out.stdout, out.stdout
        # 種別を省けば meta で決まる (合成の meta は provenance なし → 不明。# 行に出る)
        out = cli("c6")
        assert out.returncode == 0 and "#   選出モデルの種別: 不明 / 根拠 unknown" in out.stdout, out
        assert "#   正式比較: 使わない — 適用範囲未確認 (診断) (formal_ok False)" in out.stdout
        # 明示の team + 型 (Showdown 本文) → fixed、型なし → unknown (正式比較に使えない)
        team_txt = d / "team.txt"
        team_txt.write_text("\n\n".join(f"{s.capitalize()} @ Leftovers\nAbility: Pressure\nEVs: 2 HP\nBold Nature\n- Protect"
                                        for s in TEAM) + "\n", encoding="utf-8")
        out = cli("c7", "--selection-kind", "team", "--selection-team", ",".join(TEAM), "--selection-kind-basis", "b",
                  "--selection-sets", str(team_txt))
        assert out.returncode == 0, out
        s7 = _man(Path([ln for ln in out.stdout.splitlines() if ln.startswith("ADVICE_PIN_BUNDLE=")][0].split("=", 1)[1]))["selection"]
        assert s7["sets_premise"] == "fixed" and sorted(s7["premise_sets"]) == sorted(TEAM) and s7["formal_ok"] is True
        assert s7["premise_source"] == str(team_txt.resolve())
        out = cli("c8", "--selection-kind", "team", "--selection-team", ",".join(TEAM), "--selection-kind-basis", "b")
        s8 = _man(Path([ln for ln in out.stdout.splitlines() if ln.startswith("ADVICE_PIN_BUNDLE=")][0].split("=", 1)[1]))["selection"]
        assert s8["sets_premise"] == "unknown" and s8["formal_ok"] is False and "型の前提が不明" in s8["formal_reason"]
        assert "注意: 構築専用モデルの学習時の型の前提が分からない" in out.stdout
    print("test_explicit_kind_needs_basis OK")


# ------------------------------------------------------------------ (d) (e) 前提の 3 値・適用範囲・formal_ok
def test_premise_scope_and_formal():
    team_sets = {"mimikyu": {"item": "lifeorb"}}
    meta_team = lambda premise, sets=None: {"kind": "team", "team": TEAM, "kind_source": "meta", "kind_basis": "m",
                                           "sets_premise": premise, "premise_sets": sets}
    # formal_ok の条件
    assert PB.formal_status(meta_team("fixed", team_sets)) == {"ok": True, "reason": None}
    assert PB.formal_status(meta_team("none"))["ok"] is True
    assert "型の前提が不明" in PB.formal_status(meta_team("unknown"))["reason"]
    assert PB.formal_status(meta_team("fixed", None))["ok"] is False                  # fixed でも型が無ければ unknown
    assert PB.formal_status({"kind": "general", "kind_source": "meta"})["ok"] is True   # 汎用は前提を問わない
    assert PB.formal_status({"kind": "general", "kind_source": "explicit", "kind_basis": "b"})["ok"] is True
    assert "根拠" in PB.formal_status({"kind": "general", "kind_source": "explicit"})["reason"]
    assert "旧い bundle" in PB.formal_status({"kind": "general"})["reason"]
    assert "種別が不明 (なし)" in PB.formal_status({"kind": "unknown", "kind_source": "unknown", "kind_basis": "なし"})["reason"]
    # 適用範囲: fixed → 型まで、none → 6 体だけ (型の違いは渡されない)、unknown → 6 体は照合し formal_ok False
    f = PB.scope_decision(meta_team("fixed", team_sets), TEAM, True, set_diffs=["mimikyu.item: 学習時 a / 登録 b"])
    assert f["state"] == "mismatch" and not f["infer"] and f["formal_ok"] is True
    n = PB.scope_decision(meta_team("none"), list(reversed(TEAM)), True)
    assert n["state"] == "ok" and n["infer"] and n["formal_ok"] is True
    assert PB.scope_decision(meta_team("none"), OTHER, True)["state"] == "mismatch"
    u = PB.scope_decision(meta_team("unknown"), TEAM, True)
    assert u["state"] == "ok" and u["infer"] and u["formal_ok"] is False and "型の前提が不明" in u["formal_reason"]
    assert PB.scope_decision(meta_team("unknown"), OTHER, True)["state"] == "mismatch"
    k = PB.scope_decision({"kind": "unknown", "kind_source": "unknown", "kind_basis": "meta なし"}, OTHER, True)
    assert k["state"] == "ok" and k["infer"] and k["formal_ok"] is False and PB.UNVERIFIED_LABEL in k["reason"]
    # finalize_selection: 明示は前提を 3 値に直し、meta の判定を meta_classification に残す
    mc = PB.classify_from_meta(None, "a" * 64)
    fz = PB.finalize_selection({"kind": "team", "team": TEAM, "kind_basis": " b ",
                                "sets_premise": {"fixed": True, "source": "s", "sets": team_sets}}, mc)
    assert (fz["kind_source"], fz["kind_basis"], fz["sets_premise"], fz["premise_sets"], fz["premise_source"]) == \
        ("explicit", "b", "fixed", team_sets, "s")
    assert fz["formal_ok"] is True and fz["meta_classification"]["kind"] == "unknown"
    fm = PB.finalize_selection({"kind": None, "source_id": "x", "team": ["zzz"]}, mc)
    assert fm["kind"] == "unknown" and fm["team"] is None and fm["source_id"] == "x" and fm["formal_ok"] is False
    # 実 bundle: fixed / none / unknown の適用範囲 (meta で決めた bundle)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        sets = PB.registry_sets(_registry(), TEAM)
        bad_sets = json.loads(json.dumps(sets))
        bad_sets["mimikyu"]["item"] = "focussash"
        cases = {"fixed_ok": ("fixed", sets), "fixed_bad": ("fixed", bad_sets), "none": ("none", None)}
        bs = {}
        for name, (premise, ss) in cases.items():
            _write_meta(src["selection_model"], _prov("team_finetuned", TEAM, premise, ss))
            bs[name] = _freeze_meta(d, src, name)
        _write_meta(src["selection_model"], dict(_prov("team_finetuned", TEAM, "fixed", sets), sets=None))
        bs["unknown"] = _freeze_meta(d, src, "unknown")
        assert _man(bs["unknown"])["selection"]["sets_premise"] == "unknown"
        with _env(bs["fixed_ok"]):
            assert PB.selection_scope(TEAM, True)["state"] == "ok" and PB.selection_scope(TEAM, True)["formal_ok"] is True
        with _env(bs["fixed_bad"]):
            s = PB.selection_scope(TEAM, True)
            assert s["state"] == "mismatch" and "mimikyu.item" in s["reason"]
        with _env(bs["none"]):
            assert PB.verify_bundle(bs["none"], {})["set_diffs"] is None
            assert PB.selection_scope(TEAM, True)["state"] == "ok" and PB.selection_scope(OTHER, True)["state"] == "mismatch"
        with _env(bs["unknown"]):
            s = PB.selection_scope(list(reversed(TEAM)), True)
            assert s["state"] == "ok" and s["formal_ok"] is False
            if _has_torch():
                a = _select(list(reversed(TEAM)))
                assert "model_pick" in a and "⚠ 選出モデルの適用範囲未確認 (診断): 構築専用モデルの学習時の型の前提が不明" in a["text"], a
        assert "型の前提 なし (6 体だけ照合)" in PB.describe(PB.verify_bundle(bs["none"], {}))
        assert "正式比較 可" in PB.describe(PB.verify_bundle(bs["fixed_ok"], {}))
        assert f"{PB.UNVERIFIED_LABEL}: 構築専用モデルの学習時の型の前提が不明" in PB.describe(PB.verify_bundle(bs["unknown"], {}))
        # manifest の書き換えで止まる: formal_ok を True に / 種別を meta の判定と違う値に
        import shutil
        t = d / "t_formal"
        shutil.copytree(bs["unknown"], t)
        man = _man(t)
        man["selection"]["formal_ok"] = True
        (t / PB.MANIFEST_FILE).write_text(json.dumps(man), encoding="utf-8")
        assert "formal_ok" in _raises(DP.InputsPinError, PB.verify_bundle, t, {})
        t = d / "t_kind"
        shutil.copytree(bs["none"], t)
        man = _man(t)
        man["selection"].update(kind="general", team=None)
        (t / PB.MANIFEST_FILE).write_text(json.dumps(man), encoding="utf-8")
        assert "bundle の meta の判定" in _raises(DP.InputsPinError, PB.verify_bundle, t, {})
        t = d / "t_unknown_src"
        shutil.copytree(bs["none"], t)
        man = _man(t)
        man["selection"]["kind_source"] = "unknown"
        (t / PB.MANIFEST_FILE).write_text(json.dumps(man), encoding="utf-8")
        assert "kind_source unknown" in _raises(DP.InputsPinError, PB.verify_bundle, t, {})
    print(f"test_premise_scope_and_formal OK (推論: {'あり' if _has_torch() else 'torch なしで省略'})")


# ------------------------------------------------------------------ (f) version 行と real_accept
def test_version_row_and_real_accept():
    from advisor import versions as V
    from tools import real_accept as RA
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b_unk = _freeze_meta(d, src, "unk")
        _write_meta(src["selection_model"], _prov())
        b_ok = _freeze_meta(d, src, "ok")
        with _env(b_ok):
            rt = V.runtime_versions(refresh=True)["runtime"]["pin_bundle"]
            assert rt["formal_ok"] is True and rt["formal_reason"] is None and rt["selection_kind"] == "general"
            assert rt["selection_kind_source"] == "meta" and rt["selection_sets_premise"] == "none"
        with _env(b_unk):
            rt2 = V.runtime_versions(refresh=True)["runtime"]["pin_bundle"]
            assert rt2["formal_ok"] is False and "種別が不明" in rt2["formal_reason"] and rt2["selection_kind"] == "unknown"
    # real_accept: 群の照合 (version 行の runtime の pin_bundle)
    assert RA.bundle_formal_problem({}) is None
    assert RA.bundle_formal_problem({"runtime.pin_bundle.formal_ok": [True], "runtime.pin_bundle.dir": ["/b"]}) is None
    w = RA.bundle_formal_problem({"runtime.pin_bundle.formal_ok": [False], "runtime.pin_bundle.formal_reason": ["種別が不明"]})
    assert w.startswith(RA.BUNDLE_FORMAL_PREFIX) and "種別が不明" in w
    assert "旧い bundle" in RA.bundle_formal_problem({"runtime.pin_bundle.dir": ["/b"], "runtime.pin_bundle.selection_kind": ["team"]})
    assert RA.bundle_formal_problem({"runtime.pin_bundle.formal_ok": [True, False]}).startswith(RA.BUNDLE_FORMAL_PREFIX)

    def ident(rt: dict) -> dict:
        return {"identity": {"code": ["c1"]}, "runtime": rt, "version_id": ["v1"], "format": ["identity_of"],
                "runtime_ok": True, "runtime_basis": "ok", "identity_problems": [], "inputs_pin": {"n": 0}}
    good = RA.group_consistency([{"name": "a", "ident": ident({"runtime.pin_bundle.formal_ok": [True]})}], required=())
    assert good["ok"] is True and good["bundle_formal"] == {}, good
    bad = RA.group_consistency([{"name": "a", "ident": ident({"runtime.pin_bundle.formal_ok": [True]})},
                                {"name": "b", "ident": ident({"runtime.pin_bundle.formal_ok": [False],
                                                              "runtime.pin_bundle.formal_reason": ["型の前提が不明"]})}], required=())
    assert bad["ok"] is False and list(bad["bundle_formal"]) == ["b"] and bad["inputs_pin"] == {}
    assert any(n.startswith("不成立: b は" + RA.BUNDLE_FORMAL_PREFIX) for n in bad["notes"]), bad["notes"]
    # 実際の version 行 → version_identity → group_consistency の通し
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b_unk = _freeze_meta(d, src, "unk2")
        with _env(b_unk):
            vrow = dict(V.runtime_versions(refresh=True), type="version")
        vi = RA.version_identity([vrow])
        assert vi["runtime"].get("runtime.pin_bundle.formal_ok") == [False], {k: v for k, v in vi["runtime"].items() if "pin" in k}
        g = RA.group_consistency([{"name": "x", "ident": vi}], required=())
        assert g["ok"] is False and "x" in g["bundle_formal"]
    print("test_version_row_and_real_accept OK")


# ------------------------------------------------------------------ (g) 画面表示・起動ログ
def test_screen_and_startup():
    from tools import advice_mode as AM
    assert PB.pin_status_payload(None) == {"active": False, "dir_name": None, "manifest_sha16": None, "formal_ok": None,
                                           "formal_reason": None, "kind": None, "text": None, "edit_note": None}
    ok = PB.pin_status_payload({"dir": "/x/pinned/20261102_101500", "manifest_sha16": "abcd" * 4, "selection_kind": "general",
                                "formal_ok": True, "formal_reason": None, "error": None})
    assert ok["active"] and ok["formal_ok"] is True and ok["dir_name"] == "20261102_101500"
    assert ok["text"] == f"{PB.PINNED_REGISTRY_LABEL} (bundle 20261102_101500、manifest sha16 {'abcd' * 4})", ok["text"]
    assert ok["edit_note"] == PB.EDIT_NOTE and "反映されない" in ok["edit_note"]
    ng = PB.pin_status_payload({"dir": "/x/b2", "manifest_sha16": "1" * 16, "selection_kind": "unknown", "formal_ok": False,
                                "formal_reason": "選出モデルの種別が不明", "error": None})
    assert ng["formal_ok"] is False and ng["text"].endswith(f" / {PB.UNVERIFIED_LABEL}: 選出モデルの種別が不明"), ng["text"]
    er = PB.pin_status_payload({"dir": "/x/b3", "manifest_sha16": None, "formal_ok": False, "formal_reason": "bundle を読めない",
                                "error": "manifest が無い"})
    assert er["formal_ok"] is False and "⚠ bundle を読めない: manifest が無い" in er["text"]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        b = _freeze_meta(d, src, "scr")
        with _env(b):
            st = PB.pin_status()
            assert st["active"] and st["dir_name"] == "scr" and st["manifest_sha16"] == PB.manifest_sha16(b) and st["formal_ok"] is False
            assert PB.startup_exclusion_line() == f"版の固定 (bundle) の対象外: {PB.TEAM_ADVICE_EXCLUSION}"
            assert "advisor.team_advice" in PB.TEAM_ADVICE_EXCLUSION and "除外" in PB.TEAM_ADVICE_EXCLUSION
        with _env(None):
            assert PB.pin_status()["active"] is False and PB.startup_exclusion_line() is None
        line = AM.bundle_startup_check({PIN_BUNDLE_ENV: str(b)}, d / "none.env")
        assert "選出モデル 不明 (根拠 unknown)" in line and f"{PB.UNVERIFIED_LABEL}:" in line, line
    # server の emit (接続時の pin_status・登録の画面への添付・起動ログの除外の行) と index.html の表示欄
    srv = (REPO / "server.py").read_text(encoding="utf-8")
    assert "await sio.emit('pin_status', _pin_status(), room=sid)" in srv
    assert '"pin_status": _pin_status()}, room=sid)' in srv and '"pin_status": _pin_status()}' in srv.split("def get_my_team_detail")[1]
    assert "_PB.startup_exclusion_line()" in srv
    html = (REPO / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<div class="panel" id="advice"><h3>アドバイス</h3>\s*<!--[^>]*-->\s*<div id="pin-status"', html)
    assert "socket.on('pin_status', applyPinStatus)" in html and 'id="team-editor-pin-note"' in html
    assert "applyPinStatus(d.pin_status)" in html and "pinStatus.edit_note" in html
    assert "'pin_status'" in re.search(r"const CLIENT_FEATURES = \[([^\]]*)\]", html).group(1)
    print("test_screen_and_startup OK")


def main() -> None:
    for k in PIN_ENV_KEYS:
        os.environ.pop(k, None)
    test_training_provenance()
    test_classify_from_meta_pure()
    test_bundle_kind_from_meta_not_name()
    test_explicit_kind_needs_basis()
    test_premise_scope_and_formal()
    test_version_row_and_real_accept()
    test_screen_and_startup()
    print("ALL OK")


if __name__ == "__main__":
    main()
