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


def _facts(team_sha=P1_TEAM_SHA16, sel_sha="aaaa", rl_sha="bbbb", split_sha="cccc", listening=True, emb_match=True,
           sealed="e80afbea9a925dc8", dirty=None) -> dict:
    return {"team": {"sha16": team_sha, "species": ["a", "b", "c", "d", "e", "f"], "file": "team.txt"},
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
    a = P.prelim_advice(300.0, {"rl0": 50, "rl5": 50, "rl25": 40})
    assert abs(a["worst_sec_per_battle"] - 7.5) < 1e-12 and a["start_final"] is True and a["projected_final_sec"] == 7.5 * 600
    slow = P.prelim_advice(50 * (P1_ABORT_SEC_PER_BATTLE + 1), {"rl0": 50, "rl5": 50, "rl25": 50})
    assert slow["start_final"] is False
    assert P.prelim_advice(100.0, {"rl0": 0})["start_final"] is False      # 測れなければ始めない目安
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


if __name__ == "__main__":
    main()
