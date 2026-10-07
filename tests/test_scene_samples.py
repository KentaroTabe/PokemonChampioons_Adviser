"""局面の標本 (tools/scene_samples) のテスト: 固定失敗 / advice 無作為 / 映像側 (シーン判定・時刻抽出) の切り出しと、助言なしのラベル。

    python -m tests.test_scene_samples
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from tools import scene_samples as SS
from vision.scenes import SCENE_COMMAND, SCENE_FIELD, SCENE_SELECTION

BASE_KEYS = {"category", "source", "system_state", "system_advice", "display", "truth", "labels_status"}


def _state(turn):
    return {"scene": "command", "turn": turn, "player": {"active": 0, "party": [{"species": "garchomp", "moves": [["earthquake", 8]]}]}}


def _scene(t, scene, turn=1):
    return {"type": "scene", "t": t, "scene": scene, "turn": turn, "state": _state(turn)}


def _advice(t, aid, kind="battle", turn=1):
    adv = {"ok": True, "t_gen": t, "advice_id": aid}
    if kind == "battle":
        adv.update(best={"kind": "move", "id": "earthquake", "name": "じしん"}, actions=[{"kind": "move", "id": "earthquake"}])
    else:
        adv.update(recommend=[{"index": 0, "name": "ガブリアス", "lead": True}])
    return {"type": "advice", "kind": kind, "t": t, "turn": turn, "advice_id": aid, "advice": adv, "state": _state(turn),
            "state_id": f"s{aid}", "version_id": "v1"}


def _display(aid, t_shown):
    return {"type": "display", "advice_id": aid, "t_shown": t_shown, "t": t_shown + 0.05, "kind": "battle", "hidden": False}


def _battle(t0=1000.0):
    """1 対戦 (t0 〜 t0+200): 表示された助言 / 表示なし / 手動修正の近く / 遅い表示 と、最後に助言の無い決定画面"""
    return [
        {"type": "session", "t": t0},
        _scene(t0, SCENE_SELECTION, 0), _advice(t0 + 1, "A1", kind="selection", turn=0),
        _scene(t0 + 10, SCENE_COMMAND, 1), _advice(t0 + 11, "A2"), _display("A2", t0 + 11.1),
        _scene(t0 + 15, SCENE_FIELD, 1),
        _scene(t0 + 30, SCENE_COMMAND, 2), _advice(t0 + 31, "A3", turn=2),                       # 表示なし → advice_stop
        _scene(t0 + 40, SCENE_FIELD, 2),
        _advice(t0 + 45, "A4", turn=3), _display("A4", t0 + 45.2), {"type": "manual_fix", "t": t0 + 50},   # hp_stuck
        _scene(t0 + 100, SCENE_COMMAND, 4), _advice(t0 + 101, "A5", turn=4), _display("A5", t0 + 120.0),  # late
        _scene(t0 + 150, SCENE_FIELD, 4),
        _scene(t0 + 190, SCENE_COMMAND, 5),
        {"type": "outcome", "t": t0 + 200, "outcome": "win"},
    ]


def _frames(t0=1000):
    return [f"frame_{t0 + 10 * k}.png" for k in range(21)] + [f"sel_{t0 + 2}.png", f"fc_{t0 + 16}.png", "notes.txt"]


def test_pure_helpers():
    assert SS.frame_time("frame_1791280056.png") == ("frame_", 1791280056.0)
    assert SS.frame_time("/x/sel_12.png") == ("sel_", 12.0) and SS.frame_time("notes.txt") is None
    recs = _battle()
    assert SS.battle_window(recs) == (1000.0, 1200.0)
    assert SS.scene_at(recs, 1012.0)["scene"] == SCENE_COMMAND and SS.scene_at(recs, 999.0) is None
    assert SS.advice_at(recs, 1012.0)["advice_id"] == "A2"
    assert SS.advice_at(recs, 1000.5) is None                                  # まだ助言が無い
    assert SS.advice_at(recs, 1195.0) is None                                  # 直前の助言 (A5、1101) は 60 秒より前
    assert SS.advice_at(recs, 1195.0, max_age=100.0)["advice_id"] == "A5"
    prov = _advice(1012.5, "P")
    prov["advice"]["provisional"] = True
    assert SS.advice_at(recs + [prov], 1013.0)["advice_id"] == "A2"            # 確定前の助言は画面に出ていない
    print("test_pure_helpers OK")


def test_frame_candidates():
    recs = _battle()
    bat = [("battle_a.jsonl", recs, SS.battle_window(recs))]
    parsed = [(n, SS.frame_time(n)) for n in _frames() if SS.frame_time(n)]
    time_frames = [(n, ft[1]) for n, ft in parsed if ft[0] == "frame_"]
    tc = SS.time_frame_candidates(bat, time_frames, interval=60.0, tol=6.0)
    assert [c[2] for c in tc] == ["frame_1000.png", "frame_1060.png", "frame_1120.png", "frame_1180.png"], [c[2] for c in tc]
    # 許容より遠いフレームしか無い時刻は飛ばす
    sparse = [("frame_1000.png", 1000.0), ("frame_1090.png", 1090.0)]
    assert [c[2] for c in SS.time_frame_candidates(bat, sparse, interval=60.0, tol=6.0)] == ["frame_1000.png"]
    any_frames = [(n, ft[1]) for n, ft in parsed]
    sc = SS.scene_frame_candidates(bat, any_frames)
    names = {c[2] for c in sc}
    # 決定画面 (選出 1000〜、コマンド 1010〜/1030〜/1100〜/1190〜) の時刻のフレームだけ。field の時刻 (1020、1040 など) は入らない
    assert {"frame_1000.png", "sel_1002.png", "frame_1010.png", "frame_1030.png", "frame_1100.png", "frame_1190.png"} <= names, names
    assert "frame_1020.png" not in names and "fc_1016.png" not in names and "frame_1040.png" not in names, names
    print("test_frame_candidates OK")


def test_frame_row_no_advice_label():
    recs = _battle()
    row = SS.frame_row("battle_a.jsonl", recs, "frame_1195.png", 1195.0, SS.KIND_FRAME_TIME, {})
    assert row["category"] == SS.CATEGORY_NO_ADVICE and row["system_advice"] is None, row
    assert row["source"]["kind"] == SS.KIND_FRAME_TIME and row["source"]["scene_logged"] == SCENE_COMMAND
    assert row["source"]["frame"] == "frame_1195.png" and row["system_state"]["turn"] == 5 and BASE_KEYS <= set(row)
    row2 = SS.frame_row("battle_a.jsonl", recs, "frame_1012.png", 1012.0, SS.KIND_FRAME_SCENE,
                        {"A2": {"advice_id": "A2", "displayed": True, "latency": 0.1, "stale": False}})
    assert row2["category"] == "consistent" and row2["source"]["advice_id"] == "A2" and row2["system_advice"]["id"] == "earthquake", row2
    row3 = SS.frame_row("battle_a.jsonl", recs, "sel_1002.png", 1002.0, SS.KIND_FRAME_SCENE, {})
    assert row3["system_advice"] == {"kind": "selection", "recommend": ["ガブリアス"]} and row3["category"] == "advice_stop", row3
    print("test_frame_row_no_advice_label OK")


def test_build_samples_counts_and_determinism():
    battles = [("battle_a.jsonl", _battle(1000.0)), ("battle_b.jsonl", _battle(2000.0))]
    frames = _frames(1000) + _frames(2000)
    res = SS.build_samples(battles, frames, seed=3, n_fixed=3, n_advice=4, n_frame=6, interval=60.0, tol=6.0)
    rows = res["rows"]
    kinds = [r["source"]["kind"] for r in rows]
    assert kinds.count(SS.KIND_FIXED_FAILURE) == 3 and kinds.count(SS.KIND_ADVICE_RANDOM) == 4, kinds
    assert kinds.count(SS.KIND_FRAME_SCENE) == 3 and kinds.count(SS.KIND_FRAME_TIME) == 3, kinds
    assert all(r["category"] != "consistent" for r in rows if r["source"]["kind"] == SS.KIND_FIXED_FAILURE)
    assert all(BASE_KEYS <= set(r) and r["source"]["kind"] in SS.KINDS for r in rows)
    assert res["pools"][SS.KIND_ADVICE_RANDOM] == 8 and res["pools"][SS.KIND_FRAME_TIME] == 8, res["pools"]
    again = SS.build_samples(battles, frames, seed=3, n_fixed=3, n_advice=4, n_frame=6, interval=60.0, tol=6.0)
    assert json.dumps(again["rows"], sort_keys=True) == json.dumps(rows, sort_keys=True)
    other = SS.build_samples(battles, frames, seed=4, n_fixed=3, n_advice=4, n_frame=6, interval=60.0, tol=6.0)
    assert json.dumps(other["rows"], sort_keys=True) != json.dumps(rows, sort_keys=True)
    # 候補が足りなければ取れる分だけ
    small = SS.build_samples(battles[:1], [], seed=3, n_fixed=3, n_advice=50, n_frame=10)
    assert sum(1 for r in small["rows"] if r["source"]["kind"] == SS.KIND_ADVICE_RANDOM) == 4
    assert not any(r["source"]["kind"] in (SS.KIND_FRAME_SCENE, SS.KIND_FRAME_TIME) for r in small["rows"])
    s = SS.summarize(res)
    assert s["n"] == len(rows) and set(s["by_kind"]) == set(SS.KINDS)
    assert all(r["source"]["display_logged"] is True for r in rows)
    print("test_build_samples_counts_and_determinism OK")


def test_logs_without_display_rows():
    """display の行が無いログ (表示の記録の前) では advice_stop は「表示されなかった」の意味にならないので固定失敗の候補から外す"""
    recs = [r for r in _battle(3000.0) if r.get("type") != "display"]
    res = SS.build_samples([("battle_c.jsonl", recs)], [], seed=1, n_fixed=3, n_advice=10, n_frame=0)
    fixed = [r for r in res["rows"] if r["source"]["kind"] == SS.KIND_FIXED_FAILURE]
    assert fixed and all(r["category"] == "hp_stuck" for r in fixed), [r["category"] for r in fixed]
    assert all(r["source"]["display_logged"] is False for r in res["rows"])
    # 無作為抽出 (2a) は成否に関係なく全部から取る (advice_stop も残る)
    adv = [r["category"] for r in res["rows"] if r["source"]["kind"] == SS.KIND_ADVICE_RANDOM]
    assert "advice_stop" in adv and len(adv) == 4, adv
    print("test_logs_without_display_rows OK")


def test_cli_dry_run_and_no_overwrite():
    with tempfile.TemporaryDirectory() as d:
        bdir, fdir = Path(d) / "battles", Path(d) / "frames"
        bdir.mkdir()
        fdir.mkdir()
        (bdir / "battle_20261007_000000.jsonl").write_text("\n".join(json.dumps(r) for r in _battle()) + "\n", encoding="utf-8")
        for n in _frames():
            (fdir / n).write_bytes(b"")
        out = Path(d) / "scenes" / "s.jsonl"
        base = ["scene_samples", "--battles-dir", str(bdir), "--frames-dir", str(fdir), "--last", "1", "--out", str(out)]
        old = sys.argv
        try:
            sys.argv = base
            SS.main()
            assert not out.exists()                          # dry-run は書かない
            sys.argv = base + ["--write"]
            SS.main()
            rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
            assert rows and all(r["scene_id"].startswith("s") for r in rows)
            before = out.read_text(encoding="utf-8")
            try:
                SS.main()                                    # 既にあれば止まる (追記・上書きしない)
                raise AssertionError("既存ファイルに書いた")
            except SystemExit:
                pass
            assert out.read_text(encoding="utf-8") == before
        finally:
            sys.argv = old
    print("test_cli_dry_run_and_no_overwrite OK")


def main() -> None:
    test_pure_helpers()
    test_frame_candidates()
    test_frame_row_no_advice_label()
    test_build_samples_counts_and_determinism()
    test_logs_without_display_rows()
    test_cli_dry_run_and_no_overwrite()
    print("ALL OK")


if __name__ == "__main__":
    main()
