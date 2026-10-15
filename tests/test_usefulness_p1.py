"""有用性検証 P1 の起動ツール (tools/usefulness_p1) の純粋な部分のテスト。測定・Showdown・ピンの作成は行わない。

    python -m tests.test_usefulness_p1
"""
from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from champions_agent.config import P1_ABORT_SEC_PER_BATTLE, P1_FINAL_BATTLES, P1_PRELIM_BATTLES, P1_SEED, P1_TEAM_SHA16
from tools import usefulness_p1 as P


def _ranked_team(meta=58, n_teams=120) -> dict:
    """一般的なチーム (collect_facts の team_source general) の材料の例"""
    return {"source": "ranked", "meta_snapshot_id": meta, "meta_pin_file": "/a/META_PIN", "pool_pin": "pokedb_s3.json.gz",
            "seed": P1_SEED + 1, "top_n": None, "include_external": True, "n_teams": n_teams, "pool_sha16": "pppp",
            "registration_disabled_env": "ADVISOR_MY_TEAM_DISABLED"}


def _facts(team_sha=P1_TEAM_SHA16, sel_sha="aaaa", rl_sha="bbbb", split_sha="cccc", listening=True, emb_match=True,
           sealed="e80afbea9a925dc8", dirty=None, team=None) -> dict:
    """team を渡せばそれを自チームの材料にする (一般的なチーム: _ranked_team())。無ければ登録チーム (sha16 = team_sha)"""
    return {"team": team if team is not None else {"sha16": team_sha, "species": ["a", "b", "c", "d", "e", "f"], "file": "team.txt"},
            "selection_model": {"path": "sel.pt", "sha16": sel_sha, "mtime": "2026-08-30 17:43:19",
                                "embedding": {"pin_file": "pin/selection_model_emb.json", "fallback": "species_embedding.json",
                                              "matches_production": emb_match}},
            "rl_checkpoint": {"pin_dir": "/pin", "file": "/pin/battle_policy_balance_ema.zip", "sha16": rl_sha, "mtime": "x"},
            "data": {"dex_sha16": "d1", "effects_sha16": "e1"},
            "git": {"commit": "abc", "dirty_files": dirty or []},
            "showdown": {"commit": "sd", "port": 8100, "listening": listening},
            "season_pin": {"seed": 20261005, "snapshot": 58, "roster_until": 1.0, "created": "2026-10-07 00:44:38"},
            "usage_db": {"latest_snapshot_id": 61},
            "real_bank": {"built_at": "2026-10-08 19:00", "n_battles": 40, "sha16": "f1"},
            "split": {"sha16": split_sha, "sealed_id": sealed, "run_id": "improve_20261007_1807", "n_teams_in_fold": 33},
            "advisor": {"search_blend": 0.0, "shadow_variants_enabled": False}}


def _cond(**kw) -> dict:
    design = P.plan()
    cmds = {P.cond_name(w): P.build_command(w, Path("/out"), Path("/out/team.txt"), Path("/s.json"), 1, P1_SEED, 600,
                                            Path("/sel.pt"), "/pin", python="py") for w in design["weights"]}
    env = P.child_environment("/pin", base={})
    return P.build_conditions(design, _facts(**kw), 600, False, P1_SEED, 1, "/s.json", 7200, "/out", cmds, env, None, ["n1"])


def test_plan():
    d = P.plan()
    assert d["conditions"] == ["rl0", "rl5", "rl25"] and d["baseline"] == "rl25", d
    assert d["k"] == 2 and [c["cond"] for c in d["comparisons"]] == ["rl0", "rl5"]
    assert abs(d["level"] - 0.975) < 1e-12 and abs(d["z"] - 2.241403) < 1e-5
    d1 = P.plan((0.0, 25.0), 25.0)
    assert d1["k"] == 1 and abs(d1["z"] - 1.959964) < 1e-5
    try:
        P.plan((0.0, 5.0), 25.0)
        raise AssertionError("基準が重みに無いのに通った")
    except ValueError:
        pass
    print("test_plan OK")


def test_battles_and_out_dir():
    assert P.resolve_battles(None, False) == P1_FINAL_BATTLES
    assert P.resolve_battles(None, True) == P1_PRELIM_BATTLES
    assert P.resolve_battles(120, True) == 120
    import time
    t = time.mktime((2026, 10, 9, 22, 15, 0, 0, 0, -1))
    assert P.default_out_dir(t, False, Path("/r")) == Path("/r/p1_20261009_2215")
    assert P.default_out_dir(t, True, Path("/r")) == Path("/r/p1_prelim_20261009_2215")
    print("test_battles_and_out_dir OK")


def test_build_command():
    cmd = P.build_command(5.0, Path("/out"), Path("/out/team.txt"), Path("/s.json"), 1, 20261009, 600, Path("/sel.pt"), "/pin",
                          python="py")
    s = " ".join(cmd)
    assert cmd[:3] == ["py", "-m", "tools.check_advisor_player"]
    for frag in ("--battles 600", "--opp-seed 20261009", "--opp-offset 0", "--opp-split /s.json:search:1", "--opp-pilot rl",
                 "--opp-pick-policy rule", "--pick-policy advisor", "--team-file /out/team.txt", "--selection-model /sel.pt",
                 "--models-dir /pin", "--rl-blend 5", "--skip-random", "--belief-k 0", "--json /out/rl5.json",
                 "--battle-log /out/rl5.battles.jsonl", "--candidate-id p1_rl5"):
        assert frag in s, (frag, s)
    assert "--no-rl-blend" not in s
    # 重み 0 も --rl-blend 0 で渡す (--no-rl-blend と同時指定しない)
    assert "--rl-blend 0 " in " ".join(P.build_command(0.0, Path("/o"), Path("/t"), Path("/s"), 1, 1, 1, Path("/m"), "/p")) + " "
    print("test_build_command OK")


def test_child_environment():
    env = P.child_environment("/pin", base={"PATH": "/bin"})
    assert env["CHAMPIONS_MODELS_DIR"] == "/pin" and env["OMP_NUM_THREADS"] == "1" and env["PATH"] == "/bin"
    # 呼び出し側でスレッド数を指定していればそれを優先 (racing.child_env と同じ)
    assert P.child_environment("/pin", base={"OMP_NUM_THREADS": "4"})["OMP_NUM_THREADS"] == "4"
    rec = P.env_record(env)
    assert rec["CHAMPIONS_MODELS_DIR"] == "/pin" and rec["VECLIB_MAXIMUM_THREADS"] == "1" and "PATH" not in rec
    print("test_child_environment OK")


def test_build_conditions_fields():
    c = _cond()
    for key in ("k", "baseline_weight", "weights", "final_battles", "mde", "level", "z", "team", "selection_model", "rl_checkpoint",
                "data", "git", "showdown", "season_pin", "usage_db", "real_bank", "opponents", "env", "commands", "started_at",
                "advisor", "notes", "incomplete"):
        assert key in c, key
    assert c["team"]["match"] is True and c["team"]["expected_sha16"] == P1_TEAM_SHA16
    assert c["opponents"]["fold_label"] == "B" and c["opponents"]["seed"] == P1_SEED and c["opponents"]["offset"] == 0
    assert c["opponents"]["n_teams_in_fold"] == 33 and c["opponents"]["pilot"] == "rl"
    assert c["advisor"]["belief_k"] == 0 and c["advisor"]["shadow_variants_enabled"] is False
    assert c["final_battles"] == 600 and c["battles"] == 600 and c["notes"] == ["n1"]
    assert set(c["commands"]) == {"rl0", "rl5", "rl25"}
    json.dumps(c)          # JSON に書ける
    # 取得できない項目は null のまま残る (落とさない)
    f = _facts()
    f["season_pin"] = None
    f["real_bank"] = None
    c2 = P.build_conditions(P.plan(), f, 50, True, 1, 1, "/s", 7200, "/o", {}, {}, None, ["season_pin: 無い"])
    assert "season_pin" in c2 and c2["season_pin"] is None and c2["real_bank"] is None and c2["prelim"] is True
    print("test_build_conditions_fields OK")


def test_preflight():
    assert P.preflight_errors(_cond()) == []
    errs = P.preflight_errors(_cond(team_sha="deadbeefdeadbeef"))
    assert len(errs) == 1 and "sha16" in errs[0], errs
    assert any("登録チーム" in e for e in P.preflight_errors(_cond(team_sha=None)))
    assert any("選出モデル" in e for e in P.preflight_errors(_cond(sel_sha=None)))
    assert any("分割" in e for e in P.preflight_errors(_cond(split_sha=None)))
    assert any("RL" in e for e in P.preflight_errors(_cond(rl_sha=None)))
    assert any("Showdown" in e for e in P.preflight_errors(_cond(listening=False)))
    assert any("前の結果" in e for e in P.preflight_errors(_cond(), out_has_results=True))
    assert P.preflight_warnings(_cond()) == []
    w = P.preflight_warnings(_cond(emb_match=False, sealed="other", dirty=["x.py"]))
    assert any("埋め込み" in x for x in w) and any("封印 id" in x for x in w) and any("未コミット" in x for x in w), w
    print("test_preflight OK")


def test_progress_finalize_prelim():
    line = P.progress_line(125.0, {"rl0": 25, "rl5": 0, "rl25": 20})
    assert line.startswith("[p1 2:05]") and "rl0 25 戦 (5.0 秒/戦)" in line and "rl5 0 戦 (-)" in line, line
    c = _cond()
    ok = P.finalize(c, {"rl0": 600, "rl5": 600, "rl25": 600}, 3000.0, False, {"rl0": 0, "rl5": 0, "rl25": 0}, 600)
    assert ok["incomplete"] is False and ok["elapsed_sec"] == 3000.0 and ok["reached"]["rl5"] == 600
    to = P.finalize(c, {"rl0": 590, "rl5": 600, "rl25": 580}, 7201.0, True, {"rl0": -15, "rl5": 0, "rl25": -15}, 600)
    assert to["incomplete"] is True and to["reached"] == {"rl0": 590, "rl5": 600, "rl25": 580}
    assert any("時間上限" in r for r in to["incomplete_reasons"])
    bad = P.finalize(c, {"rl0": 600, "rl5": 12, "rl25": 600}, 900.0, False, {"rl0": 0, "rl5": 1, "rl25": 0}, 600)
    assert bad["incomplete"] is True and any("異常終了" in r for r in bad["incomplete_reasons"])
    assert c["incomplete"] is None          # 元の条件表は変えない
    # 本番開始可 (2026-10-09 レビュー指摘 2): 全 3 条件が規定数に到達・正常終了 (rc 0)・未完了でない・最遅が上限内
    ok0 = {"rl0": 0, "rl5": 0, "rl25": 0}
    a = P.prelim_advice(300.0, {"rl0": 50, "rl5": 50, "rl25": 50}, ok0, False, 50)
    assert a["start_final"] is True and a["reasons"] == [] and abs(a["worst_sec_per_battle"] - 6.0) < 1e-12, a
    assert a["projected_final_sec"] == 6.0 * 600
    # 到達数 0 / 50 / 50 は開始不可 (以前は 0 を速度の計算から除いて True になった)
    z = P.prelim_advice(300.0, {"rl0": 0, "rl5": 50, "rl25": 50}, ok0, False, 50)
    assert z["start_final"] is False and z["worst_sec_per_battle"] is None and any("未到達" in r for r in z["reasons"]), z
    # 1 条件が 40 戦で止まった / 異常終了 / 未完了 / 条件が欠ける → 開始不可
    assert P.prelim_advice(300.0, {"rl0": 50, "rl5": 50, "rl25": 40}, ok0, False, 50)["start_final"] is False
    assert P.prelim_advice(300.0, {"rl0": 50, "rl5": 50, "rl25": 50}, {"rl0": 0, "rl5": 1, "rl25": 0}, False, 50)["start_final"] is False
    assert P.prelim_advice(300.0, {"rl0": 50, "rl5": 50, "rl25": 50}, {"rl0": 0, "rl5": None, "rl25": 0}, False, 50)["start_final"] is False
    assert P.prelim_advice(300.0, {"rl0": 50, "rl5": 50, "rl25": 50}, ok0, True, 50)["start_final"] is False
    assert P.prelim_advice(300.0, {"rl0": 50, "rl5": 50}, {"rl0": 0, "rl5": 0}, False, 50)["start_final"] is False
    # 最遅の条件が上限を超える → 開始不可
    slow = P.prelim_advice(50 * (P1_ABORT_SEC_PER_BATTLE + 1), {"rl0": 50, "rl5": 50, "rl25": 50}, ok0, False, 50)
    assert slow["start_final"] is False and any("上限" in r for r in slow["reasons"]), slow
    assert P.dirty_files(" M champions_agent/config.py\nM  a.py\n") == ["champions_agent/config.py", "a.py"]
    assert P.dirty_files(None) is None and P.dirty_files("") == []
    print("test_progress_finalize_prelim OK")


def test_embedding_source_and_copy():
    """判断 (a) (2026-10-09): P1 のピンに選出モデルの埋め込みを複製すれば実機と一致、無ければ species_embedding.json に落ちて不一致"""
    with tempfile.TemporaryDirectory() as d:
        ckpt, pin = Path(d) / "checkpoints", Path(d) / "pin"
        ckpt.mkdir()
        pin.mkdir()
        (ckpt / P.SELECTION_EMB_FILE).write_text('{"functional": {"vectors": {"a": [1.0]}}}', encoding="utf-8")
        (ckpt / P.SELECTION_META_FILE).write_text('{"teams": []}', encoding="utf-8")
        fb = Path(d) / "species_embedding.json"
        fb.write_text('{"functional": {"vectors": {"a": [2.0]}}}', encoding="utf-8")
        from advisor.versions import sha256_file
        prod_sha, fb_sha = sha256_file(ckpt / P.SELECTION_EMB_FILE), sha256_file(fb)
        pin_emb = pin / P.SELECTION_EMB_FILE

        def _src():
            ps = sha256_file(pin_emb) if pin_emb.exists() else None
            return P.embedding_source(str(pin_emb), ps, "t" if ps else None, str(fb), fb_sha, str(ckpt / P.SELECTION_EMB_FILE),
                                      prod_sha, "t0")
        # ピンに埋め込みが無い (pin_models.sh だけ) → species_embedding.json に落ちて実機と違う
        e0 = _src()
        assert e0["used_source"] == "fallback" and e0["used"] == str(fb) and e0["matches_production"] is False, e0
        # 複製した後 → ピンの埋め込みを読み、実機と同じ。複製した埋め込みの sha16 と mtime を記録する
        copied = P.copy_selection_aux(pin, ckpt)
        assert copied == [P.SELECTION_EMB_FILE, P.SELECTION_META_FILE] and (pin / P.SELECTION_META_FILE).exists()
        e1 = _src()
        assert e1["used_source"] == "pin" and e1["matches_production"] is True and e1["pin_sha16"] == prod_sha, e1
        assert e1["pin_mtime"] == "t"
        # meta が無ければ埋め込みだけ複製する
        (ckpt / P.SELECTION_META_FILE).unlink()
        pin2 = Path(d) / "pin2"
        pin2.mkdir()
        assert P.copy_selection_aux(pin2, ckpt) == [P.SELECTION_EMB_FILE]
    # ピン作成前 (dry-run) は make_pin が複製する前提で実機と一致として扱う
    e2 = P.embedding_source(None, None, None, "fb", "f1", "prod", "p1", "t0", planned_copy=True)
    assert e2["used_source"] == "planned_copy" and e2["matches_production"] is True
    # 実機の埋め込みが読めなければ一致は不明 (None)
    assert P.embedding_source("pin", None, None, "fb", "f1", "prod", None, None)["matches_production"] is None
    print("test_embedding_source_and_copy OK")


def test_main_dry_run_and_guard():
    """--dry-run は条件表とコマンド列を出すだけ (ピンを作らない・起動しない・出力先を作らない)。
    本番起動でも登録チームの sha16 が違えば、ピンを作る前に止める"""
    orig = (P.collect_facts, P.registered_team_text, P.make_pin, P.launch)

    def _no(*a, **k):
        raise AssertionError("呼ばれてはいけない")
    try:
        P.registered_team_text = lambda: "team text"
        P.make_pin, P.launch = _no, _no
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "p1_dry"
            P.collect_facts = lambda *a, **k: _facts()
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = P.main(["--prelim", "--dry-run", "--out", str(out)])
            text = buf.getvalue()
            assert rc == 0, text
            assert not out.exists()
            assert "--battles 50" in text and "rl25: " in text and "起動していない" in text, text
            doc = json.loads(text[:text.index("\n# コマンド列")])
            assert doc["prelim"] is True and doc["battles"] == P1_PRELIM_BATTLES and doc["k"] == 2
            assert "(起動時に scripts/pin_models.sh で作成)" in doc["env"]["CHAMPIONS_MODELS_DIR"]
            # sha が違えば dry-run は NG を出して 3、本番は 2 で止まる (make_pin / launch は呼ばない)
            P.collect_facts = lambda *a, **k: _facts(team_sha="0000000000000000")
            with redirect_stdout(io.StringIO()):
                assert P.main(["--dry-run", "--out", str(out)]) == 3
                assert P.main(["--out", str(out)]) == 2
            assert not out.exists()
    finally:
        P.collect_facts, P.registered_team_text, P.make_pin, P.launch = orig
    print("test_main_dry_run_and_guard OK")


def test_scoring_compare_dry_run():
    """採点方式の比較 (2026-10-09 M2b 設計判断 11): --compare scoring で条件 scoring:legacy (基準) / scoring:transition の
    2 つ。重みは両方とも既定 (P1_BASELINE_WEIGHT)、方式は条件ごとの環境変数 ADVICE_SCORING_MODE。ファイル名は「:」を「_」に。
    dry-run の起動前検査は RL の比較と同じ項目で通る (測定は起動しない)"""
    from champions_agent.config import P1_BASELINE_WEIGHT
    d = P.plan_scoring()
    assert d["conditions"] == ["scoring:legacy", "scoring:transition"] and d["baseline"] == "scoring:legacy", d
    assert d["k"] == 1 and abs(d["z"] - 1.959964) < 1e-5 and d["factor"] == "scoring"
    assert d["cond_env"] == {"scoring:legacy": {"ADVICE_SCORING_MODE": "legacy"},
                             "scoring:transition": {"ADVICE_SCORING_MODE": "transition"}}
    assert set(d["cond_weight"].values()) == {float(P1_BASELINE_WEIGHT)}
    cmd = " ".join(P.build_command(P1_BASELINE_WEIGHT, Path("/out"), Path("/t"), Path("/s"), 1, 1, 600, Path("/m"), "/p",
                                   python="py", cond="scoring:transition"))
    assert "--json /out/scoring_transition.json" in cmd and "--battle-log /out/scoring_transition.battles.jsonl" in cmd, cmd
    assert "--candidate-id p1_scoring_transition" in cmd and f"--rl-blend {P1_BASELINE_WEIGHT:g}" in cmd
    # RL の比較の条件名・ファイル名は変わらない
    assert P.cond_file("rl25") == "rl25" and "--json /out/rl25.json" in " ".join(
        P.build_command(25.0, Path("/out"), Path("/t"), Path("/s"), 1, 1, 600, Path("/m"), "/p"))
    orig = (P.collect_facts, P.registered_team_text, P.make_pin, P.launch)

    def _no(*a, **k):
        raise AssertionError("呼ばれてはいけない")
    try:
        P.registered_team_text = lambda: "team text"
        P.make_pin, P.launch = _no, _no
        # 2026-10-09: --compare scoring の既定の自チームは一般的なチーム。材料は collect_facts の team_source に合わせる
        P.collect_facts = lambda *a, **k: _facts(team=_ranked_team() if k.get("team_source") == "general" else None)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p1_scoring"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = P.main(["--compare", "scoring", "--prelim", "--dry-run", "--out", str(out)])
            text = buf.getvalue()
            assert rc == 0, text
            assert not out.exists()
            doc = json.loads(text[:text.index("\n# コマンド列")])
            assert doc["factor"] == "scoring" and set(doc["commands"]) == {"scoring:legacy", "scoring:transition"}
            assert doc["cond_env"]["scoring:transition"] == {"ADVICE_SCORING_MODE": "transition"} and doc["k"] == 1
            assert "scoring:transition: ADVICE_SCORING_MODE=transition " in text and "起動前の検査: OK" in text, text
            assert P.preflight_errors(doc) == []
    finally:
        P.collect_facts, P.registered_team_text, P.make_pin, P.launch = orig
    print("test_scoring_compare_dry_run OK")


def _scoring_cond(team_source: str, facts: dict) -> dict:
    d = P.plan_scoring()
    tf = Path("/out/team.txt") if team_source == "registered" else None
    cmds = {c: P.build_command(d["cond_weight"][c], Path("/out"), tf, Path("/s.json"), 1, P1_SEED, 600, Path("/sel.pt"), "/pin",
                               python="py", cond=c) for c in d["conditions"]}
    env = P.child_environment("/pin", base={}, team_source=team_source)
    return P.build_conditions(d, facts, 600, False, P1_SEED, 1, "/s.json", 7200, "/out", cmds, env, None, ["n1"],
                              team_source=team_source)


def test_team_source_general_and_registered():
    """2026-10-09 ユーザー判断: 採点方式の比較は一般的なチーム (--team general、既定) で行い、登録チームを使うなら最新の登録
    (--team registered、期待 sha16 は P1_SCORING_TEAM_SHA16)。RL の比較 (registered、P1_TEAM_SHA16) の条件表は従来どおり"""
    from champions_agent.config import MY_TEAM_DISABLED_ENV, P1_SCORING_TEAM_SHA16
    # 既定と明示
    assert P.resolve_team_source(None, "scoring") == "general" and P.resolve_team_source(None, "rl") == "registered"
    assert P.resolve_team_source("registered", "scoring") == "registered" and P.resolve_team_source("general", "rl") == "general"
    try:
        P.resolve_team_source("mine", "scoring")
        raise AssertionError("未知の出所が通った")
    except ValueError:
        pass
    assert P.own_team_seed(P1_SEED) == P1_SEED + 1 and P.own_team_seed(None) is None
    assert P.expected_team_sha16("scoring") == P1_SCORING_TEAM_SHA16 and P.expected_team_sha16("rl") == P1_TEAM_SHA16
    # コマンド: 一般的なチームは --team-file を渡さない
    s_gen = " ".join(P.build_command(25.0, Path("/o"), None, Path("/s"), 1, 1, 600, Path("/m"), "/p", python="py"))
    assert "--team-file" not in s_gen and "--selection-model /m" in s_gen, s_gen
    # 環境: 一般的なチームは登録参照の無効化を全条件に共通で立てる。registered は立てない (呼び出し側に残っていても消す)
    eg = P.child_environment("/pin", base={}, team_source="general")
    assert eg[MY_TEAM_DISABLED_ENV] == "1" and P.env_record(eg)[MY_TEAM_DISABLED_ENV] == "1"
    er = P.child_environment("/pin", base={MY_TEAM_DISABLED_ENV: "1"}, team_source="registered")
    assert MY_TEAM_DISABLED_ENV not in er and MY_TEAM_DISABLED_ENV not in P.env_record(er)
    assert MY_TEAM_DISABLED_ENV not in P.child_environment("/pin", base={})          # 既定 (RL の比較) も立てない

    # general の条件表
    g = _scoring_cond("general", _facts(team=_ranked_team()))
    assert g["team"]["source"] == "ranked" and g["team"]["label"] == "一般的なチーム" and g["team"]["seed"] == P1_SEED + 1
    assert g["team"]["meta_snapshot_id"] == 58 and g["team"]["top_n"] is None and g["team"]["include_external"] is True
    assert "expected_sha16" not in g["team"] and "match" not in g["team"]          # 登録チームの sha16 は照合しない
    assert "一般的なチーム" in g["purpose"] and any(x.startswith("自チーム: 一般的なチーム") for x in g["notes"]), g["notes"]
    assert g["notes"][0] == "n1" and g["env"][MY_TEAM_DISABLED_ENV] == "1"
    assert all("--team-file" not in " ".join(c) for c in g["commands"].values())
    assert g["cond_env"]["scoring:transition"] == {"ADVICE_SCORING_MODE": "transition"}   # 方式は条件ごと、無効化は共通の env
    json.dumps(g)
    assert P.preflight_errors(g) == []
    errs = P.preflight_errors(_scoring_cond("general", _facts(team=_ranked_team(meta=None))))
    assert len(errs) == 1 and "META_PIN" in errs[0], errs
    errs = P.preflight_errors(_scoring_cond("general", _facts(team=_ranked_team(n_teams=0))))
    assert len(errs) == 1 and "プールが空" in errs[0], errs
    # プールの件数が分からない (DB が無い等) だけでは止めない (理由は notes)
    assert P.preflight_errors(_scoring_cond("general", _facts(team=_ranked_team(n_teams=None)))) == []

    # registered (採点方式の比較): 期待は P1_SCORING_TEAM_SHA16。P1_TEAM_SHA16 (RL の比較の記録) とは照合しない
    r = _scoring_cond("registered", _facts(team_sha=P1_SCORING_TEAM_SHA16))
    assert r["team"]["source"] == "registered" and r["team"]["label"] == "最新の登録チーム"
    assert r["team"]["expected_sha16"] == P1_SCORING_TEAM_SHA16 and r["team"]["match"] is True
    assert "最新の登録チーム" in r["purpose"] and any(x.startswith("自チーム: 最新の登録チーム") for x in r["notes"])
    assert MY_TEAM_DISABLED_ENV not in r["env"]
    assert all("--team-file /out/team.txt" in " ".join(c) for c in r["commands"].values())
    assert P.preflight_errors(r) == []
    errs = P.preflight_errors(_scoring_cond("registered", _facts(team_sha=P1_TEAM_SHA16)))
    assert len(errs) == 1 and P1_SCORING_TEAM_SHA16 in errs[0] and "sha16" in errs[0], errs
    assert any("登録チーム" in e for e in P.preflight_errors(_scoring_cond("registered", _facts(team_sha=None))))

    # RL の比較 (registered) の条件表は従来と同じ項目 (source / label を足さない、notes も足さない)
    c = _cond()
    assert "source" not in c["team"] and c["team"]["expected_sha16"] == P1_TEAM_SHA16 and c["team"]["match"] is True
    assert c["notes"] == ["n1"] and MY_TEAM_DISABLED_ENV not in c["env"] and "自チーム" not in c["purpose"]
    print("test_team_source_general_and_registered OK")


def test_main_team_source():
    """main: --compare scoring の既定は general (登録チームの本文を読まない・--team-file なし・無効化の環境変数)。
    --team registered は最新の登録を P1_SCORING_TEAM_SHA16 と照合する。--compare rl は従来どおり registered"""
    from champions_agent.config import MY_TEAM_DISABLED_ENV, P1_SCORING_TEAM_SHA16
    orig = (P.collect_facts, P.registered_team_text, P.make_pin, P.launch)
    seen = []

    def _no(*a, **k):
        raise AssertionError("呼ばれてはいけない")

    def _collect(team_sha=P1_TEAM_SHA16, meta=58):
        def f(*a, **k):
            seen.append(dict(k))
            return _facts(team_sha=team_sha, team=_ranked_team(meta=meta) if k.get("team_source") == "general" else None)
        return f

    def _run(argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = P.main(argv)
        text = buf.getvalue()
        return rc, text, json.loads(text[:text.index("\n# コマンド列")])
    try:
        P.make_pin, P.launch = _no, _no
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p1"
            # 既定 general: 登録チームの本文は読まない
            P.registered_team_text = _no
            P.collect_facts = _collect()
            rc, text, doc = _run(["--compare", "scoring", "--prelim", "--dry-run", "--out", str(out)])
            assert rc == 0 and "起動前の検査: OK" in text, text
            assert seen[-1]["team_source"] == "general" and seen[-1]["seed"] == P1_SEED
            assert doc["team"]["source"] == "ranked" and doc["env"][MY_TEAM_DISABLED_ENV] == "1"
            assert all("--team-file" not in " ".join(c) for c in doc["commands"].values())
            assert f"{MY_TEAM_DISABLED_ENV}=1" in text and not out.exists()
            # META_PIN が無い → dry-run は 3、本番は 2 (ピンを作らない・起動しない)
            P.collect_facts = _collect(meta=None)
            with redirect_stdout(io.StringIO()):
                assert P.main(["--compare", "scoring", "--dry-run", "--out", str(out)]) == 3
                assert P.main(["--compare", "scoring", "--out", str(out)]) == 2
            assert not out.exists()
            # --team registered: 最新の登録 (P1_SCORING_TEAM_SHA16) と照合
            P.registered_team_text = lambda: "team text"
            P.collect_facts = _collect(team_sha=P1_SCORING_TEAM_SHA16)
            rc, text, doc = _run(["--compare", "scoring", "--team", "registered", "--prelim", "--dry-run", "--out", str(out)])
            assert rc == 0 and seen[-1]["team_source"] == "registered", text
            assert doc["team"]["source"] == "registered" and doc["team"]["expected_sha16"] == P1_SCORING_TEAM_SHA16
            assert MY_TEAM_DISABLED_ENV not in doc["env"]
            assert all(f"--team-file {out.resolve() / 'team.txt'}" in " ".join(c) for c in doc["commands"].values())
            P.collect_facts = _collect(team_sha=P1_TEAM_SHA16)
            with redirect_stdout(io.StringIO()):
                assert P.main(["--compare", "scoring", "--team", "registered", "--dry-run", "--out", str(out)]) == 3
            # --compare rl の既定は registered (P1_TEAM_SHA16 と照合、従来どおり)
            rc, text, doc = _run(["--prelim", "--dry-run", "--out", str(out)])
            assert rc == 0 and seen[-1]["team_source"] == "registered" and "source" not in doc["team"], text
    finally:
        P.collect_facts, P.registered_team_text, P.make_pin, P.launch = orig
    print("test_main_team_source OK")


HOLD_SEED = 20261015


def _split_doc(n_hold=40) -> dict:
    """封印 holdout 層を持つ分割の例 (search 33 を fold B に、selection 2、holdout n_hold。本文は全 id にある)"""
    search = [f"t{i}" for i in range(33)]
    hold = [f"h{i}" for i in range(n_hold)]
    sel = ["s0", "s1"]
    return {"sealed_id": "sealedX", "run_id": "improve_x", "tiers": {"search": search, "selection": sel, "holdout": hold},
            "search_folds": [[], search, []], "families": [], "texts": {x: f"text {x}" for x in search + sel + hold}}


def _holdout_cmds(split: Path, offset=0, battles=600) -> dict:
    d = P.plan_scoring()
    return {c: P.build_command(d["cond_weight"][c], Path("/out"), None, split, None, HOLD_SEED, battles, Path("/sel.pt"), "/pin",
                               python="py", cond=c, offset=offset, tier="holdout") for c in d["conditions"]}


def test_holdout_command_and_conditions():
    """--tier holdout (2026-10-15): --opp-split は FILE:holdout (fold なし)。条件表は tier holdout と n_teams_in_tier、
    fold / fold_label / n_teams_in_fold を出さない。purpose に「封印 holdout 層での 1 回の確認」。search の条件表は従来どおり"""
    cmd = _holdout_cmds(Path("/s.json"))["scoring:transition"]
    s = " ".join(cmd)
    assert "--opp-split /s.json:holdout " in s and "--opp-seed 20261015" in s, s
    spec = P.command_opponent_spec(cmd)
    assert spec == {"split_file": "/s.json", "tier": "holdout", "fold": None, "seed": HOLD_SEED, "offset": 0, "battles": 600}, spec
    assert P.command_opponent_spec(P.build_command(5.0, Path("/o"), None, Path("/s"), 1, 7, 50, Path("/m"), "/p"))["fold"] == 1
    d = P.plan_scoring()
    f = _facts(team=_ranked_team())
    f["split"] = {"sha16": "cccc", "sealed_id": "sealedX", "run_id": "improve_x", "n_teams_in_tier": 40}
    f["holdout"] = {"sealed_id": "sealedX", "candidate_key": "scoring:transition@abc", "sequence_check": {}, "reasons": []}
    env = P.child_environment("/pin", base={}, team_source="general", sensitivity_off=True)
    c = P.build_conditions(d, f, 600, False, HOLD_SEED, None, "/s.json", 10800, "/out", _holdout_cmds(Path("/s.json")), env,
                           None, [], team_source="general", tier="holdout")
    opp = c["opponents"]
    assert opp["tier"] == "holdout" and opp["n_teams_in_tier"] == 40 and opp["seed"] == HOLD_SEED, opp
    assert not ({"fold", "fold_label", "n_teams_in_fold"} & set(opp)), opp
    assert "封印 holdout 層 (40 構築) での 1 回の確認、search の結果とは合算しない" in c["purpose"], c["purpose"]
    assert c["holdout_check"]["required"] is True and c["holdout_check"]["candidate_key"] == "scoring:transition@abc"
    assert c["k"] == 1 and c["accept_check"]["required"] is True and c["env"]["TRANSITION_SENSITIVITY"] == "0"
    json.dumps(c)
    # search (既定) は従来どおり fold B、holdout_check は無い
    cs = _scoring_cond("general", _facts(team=_ranked_team()))
    assert cs["opponents"]["tier"] == "search" and cs["opponents"]["fold_label"] == "B" and "holdout_check" not in cs
    assert "n_teams_in_tier" not in cs["opponents"] and "封印 holdout" not in cs["purpose"]
    # 既定の出力先
    import time
    t = time.mktime((2026, 10, 16, 22, 15, 0, 0, 0, -1))
    assert P.default_out_dir(t, False, Path("/r"), tier="holdout") == Path("/r/p1_holdout_20261016_2215")
    assert P.default_out_dir(t, False, Path("/r"), part=(1, 2), tier="holdout") == Path("/r/p1_holdout_20261016_2215_part1of2")
    print("test_holdout_command_and_conditions OK")


def test_holdout_sequence_tier_check():
    """起動前検査の相手列の層の検査: コマンドから check_advisor_player と同じ相手列を作り、全件が holdout 層・本文あり・
    他の層と重ならないこと。holdout に無い id を混ぜると NG"""
    from tools.team_build.opponents import opponent_sequence
    doc = _split_doc()
    with tempfile.TemporaryDirectory() as tmp:
        split = Path(tmp) / "opponent_families.json"
        split.write_text(json.dumps(doc), encoding="utf-8")
        spec = P.command_opponent_spec(_holdout_cmds(split)["scoring:legacy"])
        seq = P.planned_opponent_ids(doc, spec)
        assert seq == opponent_sequence(doc["tiers"]["holdout"], 2 * 600 + 40, HOLD_SEED) and len(seq) == 1240
        r = P.opponent_tier_check(seq, doc, 600)
        assert r["reasons"] == [] and r["n_unique_in_head"] == 40 and r["n_tier_ids"] == 40, r
        # holdout に無い id (search の t3) を混ぜると NG
        bad = list(seq)
        bad[17] = "t3"
        r = P.opponent_tier_check(bad, doc, 600)
        assert r["outside"] == ["t3"] and any("holdout 層に無い" in x for x in r["reasons"]), r
        # 本文の無い id / 他の層との重なり / 列の不足も NG
        d2 = json.loads(json.dumps(doc))
        del d2["texts"]["h5"]
        assert any("本文" in x for x in P.opponent_tier_check(seq, d2, 600)["reasons"])
        d3 = json.loads(json.dumps(doc))
        d3["tiers"]["search"].append("h0")
        assert any("重なる" in x for x in P.opponent_tier_check(seq, d3, 600)["reasons"])
        assert any("しか無い" in x for x in P.opponent_tier_check(seq[:10], doc, 600)["reasons"])
        # holdout_facts: コマンドの層が search (取り違え) なら NG になる
        design = P.plan_scoring()
        hf = P.holdout_facts(_holdout_cmds(split), split, design, "abcdef0123", Path(tmp) / "usage.jsonl", None, HOLD_SEED)
        assert hf["errors"] == [] and all(not v["reasons"] for v in hf["sequence_check"].values()), hf
        assert hf["candidate_key"] == "scoring:transition@abcdef01" and hf["sealed_id"] == "sealedX"
        wrong = {c: [x.replace(":holdout", ":search:1") if x.endswith(":holdout") else x for x in cmd]
                 for c, cmd in _holdout_cmds(split).items()}
        hw = P.holdout_facts(wrong, split, design, "abcdef0123", Path(tmp) / "usage.jsonl", None, HOLD_SEED)
        assert all(any("holdout 層に無い" in x for x in v["reasons"]) for v in hw["sequence_check"].values()), hw
        cond = {"opponents": {"tier": "holdout", "n_teams_in_tier": 40}, "holdout_check": hw, "team": {"source": "ranked",
                "meta_snapshot_id": 1}}
        assert any("相手列の層の検査 NG" in e for e in P.preflight_errors(cond))
        # 1 部分 (part 2/2、offset 300、300 戦) でも同じ列の範囲を検査する
        hp = P.holdout_facts(_holdout_cmds(split, offset=300, battles=300), split, design, "abc", Path(tmp) / "u.jsonl",
                             {"k": 2, "n": 2}, HOLD_SEED)
        assert all(v["head"] == 600 and not v["reasons"] for v in hp["sequence_check"].values()), hp
    print("test_holdout_sequence_tier_check OK")


def test_holdout_usage_check():
    """開封履歴: 同じ封印 id で方式名 (鍵の「@」より前) が一致する行があれば commit が違っても使用済み (NG)。
    例外は別の part (同じ n・違う k・同じ seed) の行だけで、続きとして許す (commit が違っても)。封印 id か鍵が無ければ
    確かめられない (NG)"""
    key = "scoring:transition@abc12345"
    assert P.holdout_candidate_key(P.plan_scoring(), "abc1234567890") == key
    assert P.holdout_candidate_key(P.plan_scoring(), None) is None
    assert P.holdout_candidate_key(P.plan(), "abc12345") == "rl0+rl5@abc12345"
    rows = [{"sealed_id": "other", "candidate_key": key, "run_id": "x"},
            {"sealed_id": "sealedX", "candidate_key": "L00_INC:selection_model_n9000.pt", "run_id": "y"}]
    r = P.holdout_usage_check(rows, "sealedX", key)
    assert r["reasons"] == [] and r["used_before"] == [] and r["continued_parts"] == [], r
    used = rows + [{"sealed_id": "sealedX", "candidate_key": key, "run_id": "p1_holdout_1", "at": "t"}]
    r = P.holdout_usage_check(used, "sealedX", key)
    assert len(r["used_before"]) == 1 and any("使用済み" in x for x in r["reasons"]), r
    # 同じ方式で commit だけ違う行も使用済み (commit を変えて測り直さない)
    sim = rows + [{"sealed_id": "sealedX", "candidate_key": "scoring:transition@ffffffff", "run_id": "z", "at": "t2"}]
    r = P.holdout_usage_check(sim, "sealedX", key)
    assert len(r["used_before"]) == 1 and any("使用済み" in x and "ffffffff" in x for x in r["reasons"]), r
    # 方式名が違う行 (前方一致だけの別方式・別の封印 id) は使用済みにしない
    other = rows + [{"sealed_id": "sealedX", "candidate_key": "scoring:transition2@abc12345", "run_id": "w"},
                    {"sealed_id": "other", "candidate_key": "scoring:transition@ffffffff", "run_id": "v"}]
    assert P.holdout_usage_check(other, "sealedX", key)["reasons"] == []
    # 2 夜分割: part 1/2 の行があっても part 2/2 (同じ seed) は続き。part 1/2 の測り直し・seed 違い・part なしは使用済み
    p1row = P.holdout_usage_row("sealedX", key, "p1_holdout_a_part1of2", "t", "purpose", part={"k": 1, "n": 2}, seed=HOLD_SEED)
    assert p1row["part"] == {"k": 1, "n": 2} and p1row["kind"] == "usefulness_p1" and p1row["run_id"] == "p1_holdout_a_part1of2"
    r = P.holdout_usage_check([p1row], "sealedX", key, part={"k": 2, "n": 2}, seed=HOLD_SEED)
    assert r["reasons"] == [] and len(r["continued_parts"]) == 1, r
    assert P.holdout_usage_check([p1row], "sealedX", key, part={"k": 1, "n": 2}, seed=HOLD_SEED)["reasons"]
    assert P.holdout_usage_check([p1row], "sealedX", key, part={"k": 2, "n": 2}, seed=1)["reasons"]
    assert P.holdout_usage_check([p1row], "sealedX", key)["reasons"]
    # 別の part の続きは commit が違っても通る。commit 違いの同じ part は使用済み
    p1row_old = dict(p1row, candidate_key="scoring:transition@ffffffff")
    r = P.holdout_usage_check([p1row_old], "sealedX", key, part={"k": 2, "n": 2}, seed=HOLD_SEED)
    assert r["reasons"] == [] and len(r["continued_parts"]) == 1, r
    assert P.holdout_usage_check([p1row_old], "sealedX", key, part={"k": 1, "n": 2}, seed=HOLD_SEED)["reasons"]
    # 封印 id / 鍵が無い
    assert any("封印 id" in x for x in P.holdout_usage_check([], None, key)["reasons"])
    assert any("候補の鍵" in x for x in P.holdout_usage_check([], "sealedX", None)["reasons"])
    # preflight: commit 違いの行でも使用済み → NG。別の part の続きは注意だけ。fold の注意は出さない
    cond = {"opponents": {"tier": "holdout", "n_teams_in_tier": 40}, "team": {"source": "ranked", "meta_snapshot_id": 1},
            "holdout_check": dict(P.holdout_usage_check(sim, "sealedX", key),
                                  sequence_check={"scoring:legacy": {"reasons": []}}, errors=[])}
    errs = P.preflight_errors(cond)
    assert any("開封履歴" in e and "使用済み" in e for e in errs), errs
    assert not any("fold" in w for w in P.preflight_warnings(cond))
    cont = dict(cond, holdout_check=dict(P.holdout_usage_check([p1row_old], "sealedX", key, part={"k": 2, "n": 2},
                                                               seed=HOLD_SEED),
                                         sequence_check={"scoring:legacy": {"reasons": []}}, errors=[]))
    assert not any("開封履歴" in e for e in P.preflight_errors(cont))
    assert any("別の part" in w for w in P.preflight_warnings(cont))
    print("test_holdout_usage_check OK")


def test_main_holdout():
    """main --tier holdout: --seed の明示が要る (search の seed と同じは不可)・--prelim / --fold は不可。dry-run は開封履歴に
    追記しない。開封履歴に同じ組があれば dry-run は 3、本番は 2 (ピンを作らない・起動しない)。本番の起動時に 1 行追記する"""
    orig = (P.collect_facts, P.registered_team_text, P.make_pin, P.launch)

    def _no(*a, **k):
        raise AssertionError("呼ばれてはいけない")

    def _collect(*a, **k):
        f = _facts(team=_ranked_team())
        f["git"] = {"commit": "abcdef0123456789", "dirty_files": []}
        f["split"] = {"sha16": "cccc", "sealed_id": "sealedX", "run_id": "improve_x", "n_teams_in_tier": 40}
        assert k.get("tier") == "holdout" and a[3] is None, (a, k)      # fold は渡さない
        return f
    try:
        P.make_pin, P.launch, P.registered_team_text = _no, _no, _no
        P.collect_facts = _collect
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            split = tmp / "opponent_families.json"
            split.write_text(json.dumps(_split_doc()), encoding="utf-8")
            usage = tmp / "holdout_usage.jsonl"
            out = tmp / "p1_holdout"
            base = ["--tier", "holdout", "--compare", "scoring", "--split", str(split), "--holdout-usage-log", str(usage),
                    "--out", str(out), "--pin-dir", str(tmp / "pin")]
            with redirect_stdout(io.StringIO()):
                assert P.main(base + ["--dry-run"]) == 2                                   # --seed なし
                assert P.main(base + ["--seed", str(P1_SEED), "--dry-run"]) == 2            # search と同じ seed
                assert P.main(base + ["--seed", str(HOLD_SEED), "--prelim", "--dry-run"]) == 2
                assert P.main(base + ["--seed", str(HOLD_SEED), "--fold", "1", "--dry-run"]) == 2
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = P.main(base + ["--seed", str(HOLD_SEED), "--dry-run"])
            text = buf.getvalue()
            assert rc == 0 and "起動前の検査: OK" in text, text
            doc = json.loads(text[:text.index("\n# コマンド列")])
            assert doc["opponents"]["tier"] == "holdout" and "fold" not in doc["opponents"]
            assert all(f"--opp-split {split}:holdout " in " ".join(c) for c in doc["commands"].values())
            assert doc["holdout_check"]["candidate_key"] == "scoring:transition@abcdef01" and doc["holdout_check"]["appended"] is None
            assert not usage.exists() and not out.exists()                                # dry-run は追記しない
            # 使用済み → dry-run 3 (追記しない)、本番 2 (make_pin / launch は呼ばない)
            used = {"sealed_id": "sealedX", "candidate_key": "scoring:transition@abcdef01", "run_id": "p1_holdout_old", "at": "t"}
            usage.write_text(json.dumps(used) + "\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                assert P.main(base + ["--seed", str(HOLD_SEED), "--dry-run"]) == 3
                assert P.main(base + ["--seed", str(HOLD_SEED)]) == 2
            assert usage.read_text(encoding="utf-8").count("\n") == 1 and not out.exists()
            # 同じ方式で commit が違う行でも使用済み (dry-run 3、本番 2)
            usage.write_text(json.dumps(dict(used, candidate_key="scoring:transition@0000aaaa")) + "\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                assert P.main(base + ["--seed", str(HOLD_SEED), "--dry-run"]) == 3
                assert P.main(base + ["--seed", str(HOLD_SEED)]) == 2
            assert usage.read_text(encoding="utf-8").count("\n") == 1 and not out.exists()
            # 本番: 検査を通ったら起動前に 1 行追記 (run_id = 出力 dir 名)、条件表に appended
            usage.unlink()
            seen = {}

            def _launch(cond, out_dir, env, battles, time_limit):
                seen["cond"] = cond
                assert usage.exists(), "子プロセスの起動前に追記されていない"
                return P.finalize(cond, {c: battles for c in cond["commands"]}, 1.0, False, {c: 0 for c in cond["commands"]},
                                  battles)
            P.launch = _launch
            with redirect_stdout(io.StringIO()):
                assert P.main(base + ["--seed", str(HOLD_SEED)]) == 0
            rows = [json.loads(x) for x in usage.read_text(encoding="utf-8").splitlines()]
            assert len(rows) == 1 and rows[0]["sealed_id"] == "sealedX" and rows[0]["run_id"] == "p1_holdout", rows
            assert rows[0]["candidate_key"] == "scoring:transition@abcdef01" and "封印 holdout 層" in rows[0]["purpose"]
            final = json.loads((out / "conditions.json").read_text(encoding="utf-8"))
            assert final["holdout_check"]["appended"]["run_id"] == "p1_holdout" and seen["cond"]["opponents"]["tier"] == "holdout"
            # 2 回目は使用済みで起動しない
            P.launch = _no
            out2 = tmp / "p1_holdout2"
            with redirect_stdout(io.StringIO()):
                assert P.main([x if x != str(out) else str(out2) for x in base] + ["--seed", str(HOLD_SEED)]) == 2
            assert not out2.exists()
    finally:
        P.collect_facts, P.registered_team_text, P.make_pin, P.launch = orig
    print("test_main_holdout OK")


def main():
    test_plan()
    test_battles_and_out_dir()
    test_build_command()
    test_child_environment()
    test_build_conditions_fields()
    test_preflight()
    test_progress_finalize_prelim()
    test_embedding_source_and_copy()
    test_main_dry_run_and_guard()
    test_scoring_compare_dry_run()
    test_team_source_general_and_registered()
    test_main_team_source()
    test_holdout_command_and_conditions()
    test_holdout_sequence_tier_check()
    test_holdout_usage_check()
    test_main_holdout()


if __name__ == "__main__":
    main()
