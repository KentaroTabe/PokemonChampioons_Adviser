"""引き継ぎ評価 I-10 (記録契約の試験) の純粋な比較の部分と最小記録のテスト (2026-10-31)。

    python -m tests.test_inheritance_record

- tools/inheritance_eval/record_contract: 欄の平坦化・正規化、保存前と復元の欄ごとの比較 (欠落・値の違い・情報なしの同値)、
  evaluate が読んだ欄の区分、簡約状態への記録の判定、値の書き換え、行の型ごとの独立の数え方、期待値との照合、measurements の行
- tools/inheritance_eval/minimal_record: manifest の照合 (無い・sha 違い・commit 違い)、壊れた行と途中で切れた行の報告、再開時の追記
- 試験 8 の fixture (AGG_SPEC) を書いて独立に数えた値が AGG_EXPECTED と一致する (fixture 自体の確認)
engine・使用率 DB・本体のデータは読まない (一時ディレクトリだけ)。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.inheritance_eval import minimal_record as MR
from tools.inheritance_eval import record_contract as RC


def test_flatten_and_norm():
    f = RC.flatten({"a": {"b": [1, {"c": None}], "d": {}}, "e": []})
    assert f == {"a.b[0]": 1, "a.b[1].c": None, "a.d": {}, "e": []}, f
    assert RC.norm_path("player.party[3].moves[0].move_id") == "player.party[].moves[].move_id"
    assert RC.flatten({"t": (1, 2)}) == {"t[0]": 1, "t[1]": 2}
    print("test_flatten_and_norm OK")


def test_is_empty_keeps_zero():
    assert all(RC.is_empty(v) for v in (None, False, "", [], {}))
    assert not any(RC.is_empty(v) for v in (0, 0.0, True, "x", [0]))
    print("test_is_empty_keeps_zero OK")


def test_field_loss_statuses():
    orig = {"scene": "command", "hp": 0.0, "flag": False, "boosts": {"atk": 2, "def": 0}, "lvl": 50, "x": 1}
    rest = {"hp": 0.0, "boosts": {"atk": 2}, "lvl": 51, "x": 1, "extra": None, "new": 3}
    reads = {"read": {"scene", "lvl"}, "iter": {"boosts.atk"}}
    lo = RC.field_loss(orig, rest, reads)
    st = {r["path"]: (r["status"], r["use"]) for r in lo["rows"]}
    assert st["scene"] == ("missing", "read")
    assert st["hp"] == ("same", "unread")
    assert st["flag"][0] == "empty_equiv"
    assert st["boosts.atk"] == ("same", "iter")
    assert st["boosts.def"][0] == "zero_equiv"            # 0 と欄なしは欠落に数えない (一覧には残す)
    assert st["lvl"] == ("changed", "read")
    assert st["extra"][0] == "empty_equiv" and st["new"][0] == "added"
    assert lo["lost"] == ["lvl", "scene"] and lo["lost_used"] == ["lvl", "scene"]
    assert lo["lost_by_use"] == {"read": 2} and lo["n_fields"] == 7
    # 同じなら欠落なし
    same = RC.field_loss(orig, json.loads(json.dumps(orig)))
    assert same["lost"] == [] and same["counts"] == {"same": 7}
    print("test_field_loss_statuses OK")


def test_use_of_container():
    reads = {"read": {"player.screens"}, "iter": set()}
    assert RC.use_of("player.screens.reflect_turns", reads) == "container"
    assert RC.use_of("player.party[].level", {"read": {"player.party"}}) == "container"
    assert RC.use_of("opponent.x", reads) == "unread"
    print("test_use_of_container OK")


def test_recorded_in():
    compact = {RC.norm_path(p) for p in RC.flatten({"scene": "command", "player": {"active": 0, "party": [
        {"species": "a", "hp_raw": [1, 2], "moves": [["tackle", 5]], "hp_read_ts": 1.0}]}})}
    assert RC.recorded_in("scene", compact)
    assert RC.recorded_in("player.active_index", compact)                 # 名前の読み替え
    assert RC.recorded_in("player.party[].species_id", compact)
    assert RC.recorded_in("player.party[].hp_max", compact)               # hp_raw[]
    assert RC.recorded_in("player.party[].hp_read_ts", compact)
    assert not RC.recorded_in("player.party[].level", compact)
    assert not RC.recorded_in("battle_seq", compact)
    print("test_recorded_in OK")


def test_perturb_and_set_paths():
    assert RC.perturb(True) is False and RC.perturb(50) == 1 and RC.perturb(1) == 2
    assert RC.perturb("command") == "move_select" and RC.perturb("move_select") == "command" and RC.perturb(None) is None
    st = {"player": {"party": [{"level": 50}, {"level": 50}, {"x": 1}]}, "turn": 3, "types": ["a", "b"]}
    assert RC.set_paths(st, "player.party[].level", RC.perturb) == 2
    assert [m.get("level") for m in st["player"]["party"]] == [1, 1, None]
    assert RC.set_paths(st, "turn", RC.perturb) == 1 and st["turn"] == 1
    assert RC.set_paths(st, "types[]", lambda v: v.upper()) == 2 and st["types"] == ["A", "B"]
    assert RC.set_paths(st, "nothing.here", RC.perturb) == 0
    print("test_perturb_and_set_paths OK")


def test_count_rows_and_compare():
    recs = [{"type": "version", "rules": {"git_commit": "g1"}},
            {"type": "advice", "kind": "battle", "advice": {"ok": True}, "evaluated": True},
            {"type": "advice", "kind": "battle", "advice": {"ok": False}, "evaluated": False},
            {"type": "advice", "kind": "battle", "advice": {"ok": True}},
            {"type": "advice", "kind": "selection", "advice": {"ok": True}},
            {"type": "inputs_pin_error", "n_suppressed": 3}, {"type": "decision", "t_decided": 5.0},
            {"type": "decision", "t_decided": None}, {"type": "outcome", "outcome": "loss"}, {"type": "outcome", "outcome": "win"}]
    c = RC.count_rows(recs)
    assert (c["n_advice"], c["n_failed"], c["n_ok"]) == (3, 1, 2)
    assert (c["n_evaluated"], c["n_cache"], c["n_eval_unknown"]) == (1, 1, 1)
    assert (c["n_inputs_pin_rows"], c["n_suppressed"], c["n_decision_rows"]) == (1, 3, 1)
    assert c["outcomes"] == ["loss", "win"] and c["git_commits"] == ["g1"] and c["n_version_rows"] == 1
    assert RC.compare_expected({"a": 1, "b": 2}, {"a": 1, "b": 3}) == [{"key": "b", "expected": 2, "got": 3}]
    assert RC.compare_expected({"z": 1}, {}) == [{"key": "z", "expected": 1, "got": "<なし>"}]
    print("test_count_rows_and_compare OK")


def test_meas_row():
    r = RC.meas("c1", "asset", "合成結果", "in", "exp", "src", "fail", "why", 1.23456)
    assert set(r) == {"test_id", "case_id", "asset", "category", "input", "expected", "expected_source", "split", "result", "reason",
                      "elapsed_sec"}
    assert r["test_id"] == "I-10" and r["split"] == "diag" and r["elapsed_sec"] == 1.235
    try:
        RC.meas("c1", "a", "合成結果", "", "", "", "ok", "", None)
        raise AssertionError("規定外の result が通った")
    except AssertionError as e:
        assert "ok" in str(e)
    print("test_meas_row OK")


def test_advice_sig():
    a = {"ok": True, "best": {"kind": "move", "id": "x"}, "actions": [{"kind": "move", "id": "x", "score": 1.00000001}],
         "scoring_fallback": None}
    b = json.loads(json.dumps(a))
    b["actions"][0]["score"] = 1.0
    assert RC.advice_sig(a) == RC.advice_sig(b)
    b["best"]["id"] = "y"
    assert RC.advice_sig(a) != RC.advice_sig(b)
    assert RC.advice_sig(None)["best"] == [None, None]
    print("test_advice_sig OK")


def test_agg_fixture_matches_expected():
    """AGG_SPEC で書いた 5 本を行の型だけで数えた値が AGG_EXPECTED と一致する (試験 8 の fixture の確認)"""
    with tempfile.TemporaryDirectory() as td:
        logs = RC.build_agg_logs(Path(td), {"player": {"party": [{}]}}, {"player": {"party": []}, "opponent": {"party": []}})
        files = sorted(Path(td).glob("*.jsonl"))
        assert [f.name for f in files] == sorted(RC.AGG_SPEC)
        tot: dict = {}
        for name in RC.AGG_SPEC:
            recs = [json.loads(x) for x in (Path(td) / name).read_text(encoding="utf-8").splitlines()]
            assert recs == logs[name]
            for k, v in RC.count_rows(recs).items():
                if isinstance(v, int):
                    tot[k] = tot.get(k, 0) + v
    want = {k: v for k, v in RC.AGG_EXPECTED.items() if k in tot}
    assert RC.compare_expected(want, tot) == [], RC.compare_expected(want, tot)
    print("test_agg_fixture_matches_expected OK")


# ------------------------------------------------------------------ minimal_record
def test_minimal_manifest_check():
    man = {"files": {"a": {"path": "a.bin", "sha256": "11"}, "b": {"path": "b.bin", "sha256": "22"}}, "code_commit": "c1"}
    assert MR.check_manifest(man, {"a": "11", "b": "22"}) == []
    why = MR.check_manifest(man, {"a": None, "b": "33"}, code_commit="c2")
    assert len(why) == 3 and "ファイルが無い" in why[0] and "sha256" in why[1] and "commit" in why[2], why
    assert MR.check_manifest(man, {"a": "11", "b": "22"}, code_commit="c1") == []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "x.bin").write_bytes(b"x")
        MR.write_manifest(d, {"x": d / "x.bin"}, "c1", extra={"usage_snapshot": 82})
        man = json.loads((d / MR.MANIFEST).read_text(encoding="utf-8"))
        assert man["files"]["x"]["path"] == "x.bin" and man["files"]["x"]["bytes"] == 1 and man["usage_snapshot"] == 82
        assert MR.verify(d, "c1") == [] and MR.resolve(d, "x") == d / "x.bin"
        (d / "x.bin").write_bytes(b"y")
        for fn in (lambda: MR.resolve(d, "x"), lambda: MR.resolve(d, "nothing")):
            try:
                fn()
                raise AssertionError("照合できない参照が通った")
            except MR.MinimalPinError:
                pass
    print("test_minimal_manifest_check OK")


def test_minimal_parse_lines():
    good = [json.dumps({"seq": i, "type": "a"}) for i in (1, 2, 3)]
    recs, probs = MR.parse_lines("\n".join(good) + "\n")
    assert len(recs) == 3 and probs == []
    recs, probs = MR.parse_lines("\n".join([good[0], good[1][:8], good[2]]) + "\n")
    assert len(recs) == 2 and probs == [{"line": 2, "kind": "invalid", "head": good[1][:8]}]
    recs, probs = MR.parse_lines("\n".join([good[0], good[1], good[2][:5]]))
    assert len(recs) == 2 and probs[0]["kind"] == "truncated" and probs[0]["line"] == 3
    recs, probs = MR.parse_lines("\n".join([good[1], good[0]]) + "\n")
    assert probs[0]["kind"] == "seq_not_increasing"
    print("test_minimal_parse_lines OK")


def test_minimal_writer_resume():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        w = MR.Writer(d)
        for i in range(3):
            w.append("advice", i=i)
        raw = (d / MR.RECORDS).read_text(encoding="utf-8")
        (d / MR.RECORDS).write_text(raw[:-7], encoding="utf-8")      # 停止: 最後の行が途中で切れた
        w2 = MR.Writer(d)
        assert w2.seq == 3                                             # 最後の有効な seq (2) の次に resume の行
        w2.append("advice", i=3)
        recs, probs = MR.read(d)
    assert [r["seq"] for r in recs] == [1, 2, 3, 4]
    assert [r["type"] for r in recs] == ["advice", "advice", "resume", "advice"]
    assert recs[2]["after_seq"] == 2 and recs[2]["problems"][0]["kind"] == "truncated"
    assert len(probs) == 1 and probs[0]["line"] == 3 and probs[0]["kind"] == "invalid"   # 切れ端は改行で区切られ、次の記録と混ざらない
    print("test_minimal_writer_resume OK")


if __name__ == "__main__":
    test_flatten_and_norm()
    test_is_empty_keeps_zero()
    test_field_loss_statuses()
    test_use_of_container()
    test_recorded_in()
    test_perturb_and_set_paths()
    test_count_rows_and_compare()
    test_meas_row()
    test_advice_sig()
    test_agg_fixture_matches_expected()
    test_minimal_manifest_check()
    test_minimal_parse_lines()
    test_minimal_writer_resume()
    print("ALL OK")
