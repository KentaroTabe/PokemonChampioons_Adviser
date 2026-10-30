"""持ち物の手がかりの集計の道具 (tools/item_cues) のテスト (2026-10-30)。合成の対戦ログだけで閉じる。

    python -m tests.test_item_cues

- 帰属 (next_on_switch / last_state) と出場区間 (交代・ひんしで切る)
- 連続・否定の判定 (turn の値の重複、時刻の差)、守りの条件 (turn が同じ・わるあがき・場の個体の変化・時刻の差)
- 天候・フィールド・壁の開始の束ね・終了の対応 (壁は側を合わせる)・上書き、再現率の計算
- 3 本の CLI (cues / field / recall) の集計と、出力の先頭の再現の記録の形
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from champions_agent import config as C
from tools.item_cues import common as cm
from tools.item_cues import cues as cues_cli
from tools.item_cues import field as field_cli
from tools.item_cues import recall as recall_cli


def _state(t, my_active, opp_active, opp_items=None, kind="advice"):
    """状態行: 自分 pelipper (しめったいわ) / garchomp (スカーフ) / clodsire (たべのこし)、相手 gholdengo / amoonguss"""
    opp_items = opp_items or {}
    return {"type": kind, "kind": "battle", "t": t, "state": {
        "player": {"active": my_active, "party": [{"species": "pelipper", "item": "damprock"},
                                                  {"species": "garchomp", "item": "choicescarf"},
                                                  {"species": "clodsire", "item": "leftovers"}]},
        "opponent": {"active": opp_active, "party": [{"species": "gholdengo", "item": opp_items.get(0)},
                                                     {"species": "amoonguss", "item": opp_items.get(1)}]},
        "field": {}}}


def _ev(t, turn, fired, texts=None):
    return {"type": "events", "t": t, "turn": turn, "fired": fired, "texts": texts or [f"文言 {f}" for f in fired]}


def _battle_rows():
    """合成の 1 対戦。
    自分: pelipper が あめふらし で雨 → ぼうふう 3 回 (turn 1・2・3、30 秒以上の間隔) → 交代 → garchomp (スカーフ) が同じ turn に 5 秒差で違う技 2 つ
    相手: gholdengo が シャドーボール → ゴールドラッシュ (30 秒差、否定)。後で たべのこし の表示 → 状態に item
    雨は turn 6 に終了の文言 (開始から 5)"""
    return [
        {"type": "version", "version_id": "v1", "rules": {"git_commit": "abcdef1234567890", "usage_snapshot": 75}, "t": 99},
        _state(100, 0, 0),
        _ev(110, 1, ["ability_player_drizzle", "rain_start"]),
        _ev(115, 1, ["move_player_hurricane"]),
        _ev(120, 1, ["move_opponent_shadowball"]),
        _state(140, 0, 0),
        _ev(150, 2, ["move_player_hurricane"]),
        _ev(155, 2, ["move_opponent_makeitrain"]),
        _ev(185, 3, ["move_player_hurricane"]),
        _ev(190, 3, ["switch_player"]),
        _state(195, 1, 0),
        _ev(200, 4, ["move_player_earthquake"]),
        _ev(205, 4, ["move_player_dragonclaw"]),
        _ev(230, 5, ["item_opponent_leftovers"], ["サーフゴーは たべのこしで 少し 回復"]),
        _state(231, 1, 0, {0: "leftovers"}),
        _ev(260, 6, ["rain_end"]),
        _state(261, 1, 0, {0: "leftovers"}),
    ]


def _write_logs(d: Path) -> None:
    rows = _battle_rows()
    (d / "battle_20261001_120000.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n\n{bad\n",
                                                    encoding="utf-8")
    # 技・交代の発火が無いファイル (除外)
    (d / "battle_20261002_120000.jsonl").write_text(json.dumps(_state(1, 0, 0)) + "\n", encoding="utf-8")
    (d / "other.jsonl").write_text("{}\n", encoding="utf-8")       # battle_*.jsonl でないので読まない


# ---------------------------------------------------------------- 純粋な部分

def test_attribution_and_stints():
    rows = [_state(0, 0, 0), _ev(10, 1, ["move_player_hurricane"]), _ev(20, 1, ["switch_player", "move_player_uturn"]),
            _state(21, 1, 0), _ev(60, 2, ["move_player_earthquake"]), _ev(70, 2, ["faint"]), _ev(100, 3, ["move_player_earthquake"])]
    nxt = cm.build_timeline(rows, cm.ATTR_NEXT_ON_SWITCH)
    last = cm.build_timeline(rows, cm.ATTR_LAST_STATE)
    # 交代の発火の行の技: next_on_switch は次の状態行の場の個体、last_state は直前の状態行の場の個体
    assert nxt[1]["active"]["player"] == "garchomp", nxt[1]
    assert last[1]["active"]["player"] == "pelipper", last[1]
    st = cm.cut_stints(nxt, "player")
    # 交代で切る (pelipper の区間が閉じる)。同じ行で switch_player の後に並ぶ技は交代の後の個体 (garchomp) の新しい区間。ひんしで切る
    assert [(s["sid"], [m["move"] for m in s["moves"]]) for s in st] == [
        ("pelipper", ["hurricane"]), ("garchomp", ["uturn", "earthquake"]), ("garchomp", ["earthquake"])], st
    # last_state では交代の行の技は pelipper に付く (交代で区間は切れているので別の区間)
    st_last = cm.cut_stints(last, "player")
    assert [(s["sid"], [m["move"] for m in s["moves"]]) for s in st_last][:2] == [("pelipper", ["hurricane"]), ("pelipper", ["uturn"])]
    assert cm.max_turn(nxt) == 3
    # メガ形態は基本種にまとめる
    assert cm.base_sid("garchompmega") == "garchomp" and cm.base_sid("charizardmegax") == "charizard"
    assert cm.base_sid("mega") == "mega" and cm.base_sid(None) is None
    print("test_attribution_and_stints OK")


def test_stint_cues_lock_and_negation():
    def mv(move, turn, t):
        return {"move": move, "turn": turn, "t": t, "i": 0}
    # 3 連続: turn の値が別々、時刻の差 30 秒 → lock / lock_distinct_turns / lock_sep
    c = cm.stint_cues([mv("a", 1, 0), mv("a", 2, 30), mv("a", 3, 60)], n_lock=3, min_sep=20)
    assert c["lock"] and c["lock_distinct_turns"] and c["lock_sep"] and not c["negation"] and c["dup_same_turn"] == 0, c
    # 同じ turn の値の再読み (重複) が混ざると turn の条件は外れる。時刻の差が短いと lock_sep も外れる
    c = cm.stint_cues([mv("a", 1, 0), mv("a", 1, 5), mv("a", 2, 35)], n_lock=3, min_sep=20)
    assert c["lock"] and not c["lock_distinct_turns"] and not c["lock_sep"] and c["dup_same_turn"] == 1, c
    # 否定: 最初の 1 組
    c = cm.stint_cues([mv("a", 1, 0), mv("b", 2, 30), mv("c", 3, 60)], n_lock=3, min_sep=20)
    assert c["negation"] and (c["negation_pair"][0]["move"], c["negation_pair"][1]["move"]) == ("a", "b") and not c["lock"]
    # 個体ごとの集約: 区間をまたいで連続しない (交代で切れた 2 回 + 1 回は 3 連続でない)
    ind = cm.individual_cues([{"sid": "x", "moves": [mv("a", 1, 0), mv("a", 2, 30)]}, {"sid": "x", "moves": [mv("a", 3, 60)]}],
                             n_lock=3, min_sep=20)
    assert ind["x"]["n_moves"] == 3 and not ind["x"]["lock"] and not ind["x"]["negation"], ind
    print("test_stint_cues_lock_and_negation OK")


def test_guard_flags():
    a = {"move": "shadowball", "turn": 1, "t": 100.0, "i": 2}
    b = {"move": "makeitrain", "turn": 2, "t": 130.0, "i": 6}
    g = cm.guard_flags(a, b, "opponent", "gholdengo", [{"opponent": "gholdengo"}], min_sep=20)
    assert g["clean"] and g["clean_1030"] and not any(g[k] for k in ("same_turn", "struggle", "active_changed", "too_close")), g
    # turn が同じでも時刻が離れていれば §3.4 の条件は通る (10/30 の条件は落とす)
    g = cm.guard_flags(a, dict(b, turn=1), "opponent", "gholdengo", [], min_sep=20)
    assert g["same_turn"] and not g["clean_1030"] and g["clean"], g
    # 時刻の差が短い (再読みの疑い)・時刻が無い
    g = cm.guard_flags(a, dict(b, t=105.0), "opponent", "gholdengo", [], min_sep=20)
    assert g["too_close"] and not g["clean"] and g["clean_1030"], g
    assert cm.guard_flags(a, dict(b, t=None), "opponent", "gholdengo", [], min_sep=20)["too_close"]
    # わるあがき
    g = cm.guard_flags(a, dict(b, move="struggle"), "opponent", "gholdengo", [], min_sep=20)
    assert g["struggle"] and not g["clean"] and not g["clean_1030"]
    # 間の状態行で場の個体が別の種 (交代の読み落としの疑い)。不明 (None) は変化にしない
    g = cm.guard_flags(a, b, "opponent", "gholdengo", [{"opponent": None}, {"opponent": "amoonguss"}], min_sep=20)
    assert g["active_changed"] and not g["clean"] and not g["clean_1030"]
    assert not cm.guard_flags(a, b, "opponent", "gholdengo", [{"opponent": None}], min_sep=20)["active_changed"]
    # 間の状態行の取り出し (行番号が開区間)
    acts = [(1, {"opponent": "a"}), (3, {"opponent": "b"}), (6, {"opponent": "c"})]
    assert cm.actives_between(acts, 2, 6) == [{"opponent": "b"}]
    assert cm.dt_bucket(a, b) == "<60s" and cm.dt_bucket(a, dict(b, t=None)) == "no_t"
    print("test_guard_flags OK")


def test_episodes_merge_end_and_superseded():
    act = {"player": "pelipper", "opponent": "tyranitar"}
    tl = [{"t": 0, "turn": 1, "fired": ["ability_player_drizzle"], "texts": [], "active": act, "i": 0},
          {"t": 5, "turn": 1, "fired": ["rain_start"], "texts": [], "active": act, "i": 1},                    # 束ねる
          {"t": 40, "turn": 2, "fired": ["ability_opponent_sandstream", "sand_start"], "texts": [], "active": act, "i": 2},
          {"t": 80, "turn": 4, "fired": ["sand_end", "rain_end"], "texts": [], "active": act, "i": 3}]
    eps, orphan = cm.episodes(tl, cm.weather_start, cm.weather_end, merge_sec=15)
    assert [(e["val"], sorted(e["src"]), e["side"], e["setter"], e["ended"]) for e in eps] == [
        ("rain", ["ability", "msg"], "player", "pelipper", True), ("sandstorm", ["ability", "msg"], "opponent", "tyranitar", True)], eps
    assert orphan == {} and eps[1]["end_turn"] == 4
    assert cm.superseded(eps, 0, merge_sec=15) and not cm.superseded(eps, 1, merge_sec=15)
    # 開始の無い終了は orphan。15 秒より後の同じ天候は別の開始 (再発動)
    tl2 = [{"t": 0, "turn": 1, "fired": ["rain_end"], "texts": [], "active": act, "i": 0},
           {"t": 10, "turn": 1, "fired": ["rain_start"], "texts": [], "active": act, "i": 1},
           {"t": 40, "turn": 2, "fired": ["rain_start"], "texts": [], "active": act, "i": 2}]
    eps2, orphan2 = cm.episodes(tl2, cm.weather_start, cm.weather_end, merge_sec=15)
    assert orphan2 == {"rain_end": 1} and len(eps2) == 2 and eps2[0]["side"] is None and eps2[0]["setter"] is None
    # 壁: 終了の文言の「相手 / 味方」で側を合わせる
    tl3 = [{"t": 0, "turn": 1, "fired": ["move_player_reflect"], "texts": [], "active": act, "i": 0},
           {"t": 30, "turn": 2, "fired": ["move_opponent_reflect"], "texts": [], "active": act, "i": 1},
           {"t": 90, "turn": 5, "fired": ["reflect_end"], "texts": ["味方の リフレクターが なくなった"], "active": act, "i": 2}]
    eps3, _o = cm.episodes(tl3, cm.screen_start, cm.screen_end, merge_sec=15)
    assert [(e["side"], e["ended"]) for e in eps3] == [("player", True), ("opponent", False)], eps3
    assert not cm.superseded(eps3, 0, merge_sec=15, same_side_val=True)
    print("test_episodes_merge_end_and_superseded OK")


def test_recall_pure():
    eps = [{"val": "rain", "t": 0, "turn": 1, "ended": True}, {"val": "rain", "t": 100, "turn": 3, "ended": False},
           {"val": "sun", "t": 300, "turn": 12, "ended": False}]
    # 1 番目は上書きされた (数えない)、2 番目は上書きされた、3 番目は残り 1 ターン (数えない)
    assert recall_cli.weather_must_end(eps, 13, must_end=9) == (0, 0)
    assert recall_cli.weather_must_end(eps[:1], 13, must_end=9) == (1, 1)
    assert recall_cli.weather_must_end(eps[1:2], 13, must_end=9) == (1, 0)
    moves = {"clodsire": 3, "pelipper": 5, "toxapex": 1}
    fires = {"clodsire": 2}
    items = {"clodsire": "leftovers", "pelipper": "damprock", "toxapex": "leftovers"}
    assert recall_cli.holder_recall(moves, fires, items, "leftovers", min_moves=2) == (1, 1)
    assert cm.ratio(58, 70) == 0.8286 and cm.ratio(0, 0) is None
    assert field_cli.item_class("weather", {"side": "player", "setter": "pelipper"}, items) == "extender"
    assert field_cli.item_class("weather", {"side": "player", "setter": "clodsire"}, items) == "no_extender"
    assert field_cli.item_class("screen", {"side": "opponent", "setter": "x"}, items) == "opp"
    assert field_cli.item_class("screen", {"side": None, "setter": None}, items) == "unknown"
    print("test_recall_pure OK")


# ---------------------------------------------------------------- CLI

def _check_record(rec: dict, cli: str, d: Path) -> None:
    assert rec["cli"] == cli
    lg = rec["logs"]
    assert lg["dir"] == str(d) and lg["n_files"] == 2 and lg["n_battles"] == 1, lg
    assert lg["excluded"] == {"no_move_or_switch": 1} and lg["excluded_files"]["no_move_or_switch"] == ["battle_20261002_120000.jsonl"]
    assert lg["period"] == ["battle_20261001_120000.jsonl", "battle_20261001_120000.jsonl"]
    assert lg["battles_by_month"] == {"202610": 1}
    files = sorted(d.glob("battle_*.jsonl"))
    sha = [cm.file_sha16(p) for p in files]
    assert lg["sha16_files"] == {p.name: s for p, s in zip(files, sha)}
    assert lg["sha16_all_files"] == cm.aggregate_sha16(sha) and len(lg["sha16_all_files"]) == 16
    assert rec["versions"]["n_battles_with_version"] == 1 and rec["versions"]["usage_snapshot"] == {"75": 1}
    assert rec["versions"]["git_commit"] == {"abcdef123456": 1}
    assert rec["config"]["ITEM_CUE_MIN_SEP_SEC"] == C.ITEM_CUE_MIN_SEP_SEC and set(rec["config"]) == set(cm.CONFIG_NAMES)
    assert rec["args"]["logs"] == str(d)
    assert any("no_move_or_switch" in r for r in rec["exclusion_rules"])
    assert set(rec["code"]) == {"head", "dirty"}


def _run(cli_mod, d: Path, out: Path, extra=()) -> tuple:
    cli_mod.main(["--logs", str(d), "--out", str(out), *extra])
    js = sorted(out.glob(f"{cli_mod.CLI}_*.json"))[-1]
    md = js.with_suffix(".md")
    return json.loads(js.read_text(encoding="utf-8")), md.read_text(encoding="utf-8")


def test_cli_cues():
    with tempfile.TemporaryDirectory() as td:
        d, out = Path(td) / "battles", Path(td) / "out"
        d.mkdir()
        _write_logs(d)
        rep, md = _run(cues_cli, d, out)
        _check_record(rep["record"], "cues", d)
        assert rep["record"]["db"] is None
        assert md.index("## 再現の記録") < md.index("## ログの概要"), "再現の記録が先頭"
        c = rep["counts"]
        assert c["battles"]["n"] == 1 and c["battles"]["with_opp_item_known"] == 1
        assert c["item_fire_opponent"] == {"leftovers": 1} and c["item_battle_opponent"] == {"leftovers": 1}
        assert c["opp_item_known"]["item"] == 1 and c["opp_item_known_by_item"] == {"leftovers": 1}
        # 判明 (t=231) の前に gholdengo の技 2 つ (否定) があった
        assert c["opp_known_pre_cue"] == {"n": 1, "any_move": 1, "two_moves": 1, "lock3": 0}, c["opp_known_pre_cue"]
        ch = c["choice"]
        assert ch["player_other / lock"] == 1 and ch["player_other / lock_distinct_turns"] == 1 and ch["player_other / lock_sep"] == 1
        assert ch["player_scarf / negation"] == 1 and ch["player_scarf / mons_with_move"] == 1
        assert ch["opponent / negation"] == 1 and ch["opponent / ge2moves"] == 1 and ch.get("opponent / lock", 0) == 0
        assert c["choice_negation_item_opponent"] == {"leftovers": 1} and c["choice_lock_item_player"] == {"damprock": 1}
        g = c["guard"]
        # 自分の garchomp: 同じ turn・5 秒差 → どちらの条件でも落ちる。相手の gholdengo: 30 秒差・別の turn → 通る
        for attr in cm.ATTRIBUTIONS:
            assert g[f"{attr} / player / negation"] == 1 and g[f"{attr} / player / same_turn"] == 1
            assert g[f"{attr} / player / too_close"] == 1 and g.get(f"{attr} / player / clean", 0) == 0
            assert g[f"{attr} / opponent / clean"] == 1 and g[f"{attr} / opponent / clean_1030"] == 1
        assert c["switch_player"] == {"switch_fired": 1, "changes": 1, "no_switch_fired": 0}, c["switch_player"]
        # 交代の発火なしに場の個体が変わった (交代の読み落とし・種の訂正の上限の目安)
        R = defaultdict(Counter)
        cues_cli.switch_changes([_state(0, 0, 0), _ev(5, 1, ["move_opponent_x"]), _state(10, 0, 1)], R)
        assert R["switch_opponent"] == {"changes": 1, "no_switch_fired": 1}, R["switch_opponent"]
        assert rep["overview"]["n_files"] == 2 and rep["overview"]["fired_prefix"]["move_player"] == 5
    print("test_cli_cues OK")


def test_cli_cues_db():
    with tempfile.TemporaryDirectory() as td:
        d, out = Path(td) / "battles", Path(td) / "out"
        d.mkdir()
        _write_logs(d)
        db = Path(td) / "usage.sqlite3"
        conn = sqlite3.connect(db)
        conn.executescript("CREATE TABLE usage_snapshot (id INTEGER, fetched_at TEXT, source TEXT, format TEXT);"
                           "CREATE TABLE item_usage (snapshot_id INTEGER, pokemon_name TEXT, item_name TEXT, usage_percent REAL);"
                           "INSERT INTO usage_snapshot VALUES (75, '2026-10-22 21:32:02', 'src', 'fmt');"
                           "INSERT INTO item_usage VALUES (75, 'a', 'leftovers', 40), (75, 'b', 'leftovers', 10), (75, 'a', 'focussash', 30);")
        conn.commit()
        conn.close()
        rep, md = _run(cues_cli, d, out, ["--db", str(db), "--snapshot", "75"])
        assert rep["record"]["db"]["path"] == str(db) and rep["record"]["db"]["snapshots"] == [75]
        assert rep["record"]["db"]["sha16"] == cm.file_sha16(db)
        items = rep["db_items"][0]
        assert items["n_species"] == 2 and items["items"][0] == {"item": "leftovers", "n_species": 2, "usage_sum": 50.0}
        assert "## 使用率 DB の持ち物の一覧" in md
    print("test_cli_cues_db OK")


def test_cli_field_and_recall():
    with tempfile.TemporaryDirectory() as td:
        d, out = Path(td) / "battles", Path(td) / "out"
        d.mkdir()
        _write_logs(d)
        rep, md = _run(field_cli, d, out)
        _check_record(rep["record"], "field", d)
        c = rep["counts"]
        assert c["weather_start"] == {"rain": 1} and c["weather_start_src"] == {"ability+msg": 1}
        assert c["weather_start_side"] == {"player": 1} and c["weather_ended"] == {"n": 1} and c["weather_end_turns"] == {"5": 1}
        assert c["weather_eps_by_class"] == {"extender": 1} and c["weather_setter_item_player"] == {"damprock": 1}
        assert c["weather_retrigger"] == {"battles_with_weather": 1} and c["weather_starts_per_battle"] == {"1": 1}
        assert rep["lists"]["weather_end_dt_extender"] == [5]
        assert md.index("## 再現の記録") < md.index("## 開始と終了")

        rep, md = _run(recall_cli, d, out)
        _check_record(rep["record"], "recall", d)
        c = rep["counts"]
        # clodsire (たべのこし) は場に出ていない。天候は残り 5 ターンで「終わったはず」でない
        assert c["leftovers"] == {} or c["leftovers"].get("holders", 0) == 0, c
        assert c["weather_end_msg"].get("must_end", 0) == 0
        assert c["player_item_on_field"] == {"damprock": 1, "choicescarf": 1}, c["player_item_on_field"]
        assert c["player_item_displayed"] == {"damprock": 0, "choicescarf": 0}
        assert md.index("## 再現の記録") < md.index("## 再現率")
    print("test_cli_field_and_recall OK")


def main():
    test_attribution_and_stints()
    test_stint_cues_lock_and_negation()
    test_guard_flags()
    test_episodes_merge_end_and_superseded()
    test_recall_pure()
    test_cli_cues()
    test_cli_cues_db()
    test_cli_field_and_recall()
    print("ALL OK")


if __name__ == "__main__":
    main()
