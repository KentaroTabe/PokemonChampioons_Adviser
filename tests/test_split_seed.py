"""分割 (S2) の seed の規制ごとの固定 (tools/team_build/split_seed) のテスト。

    python -m tests.test_split_seed
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.team_build import split_seed as SS


def test_resolve_pure():
    # 表に無い規制: run の seed を登録して使う (fixed:new)
    seed, src, table = SS.resolve_split_seed("gen9championsbssregmb", 20260906, {}, fixed=True)
    assert (seed, src) == (20260906, "fixed:new") and table["gen9championsbssregmb"]["seed"] == 20260906
    # 表にある規制: 表の seed (run の seed が違っても)
    seed2, src2, table2 = SS.resolve_split_seed("gen9championsbssregmb", 777, table, fixed=True)
    assert (seed2, src2) == (20260906, "fixed:gen9championsbssregmb") and table2 == table
    # 別の規制は別の項
    seed3, src3, table3 = SS.resolve_split_seed("gen9championsbssregmc", 777, table2, fixed=True)
    assert (seed3, src3) == (777, "fixed:new") and set(table3) == {"gen9championsbssregmb", "gen9championsbssregmc"}
    # 固定しない設定: run の seed、表は触らない
    assert SS.resolve_split_seed("gen9championsbssregmb", 777, table3, fixed=False)[:2] == (777, "run")
    # 明示の指定 (--split-seed) が最優先
    assert SS.resolve_split_seed("gen9championsbssregmb", 777, table3, fixed=True, override=5)[:2] == (5, "arg")
    # 規制が空なら default の項
    assert SS.resolve_split_seed("", 1, {}, fixed=True)[2] == {"default": SS.resolve_split_seed("", 1, {}, fixed=True)[2]["default"]}
    print("test_resolve_pure OK")


def test_file_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "registry" / "split_seeds.json"
        assert SS.load_table(path) == {}
        seed, src = SS.split_seed_for("regA", 11, path=path, fixed=True)
        assert (seed, src) == (11, "fixed:new") and json.loads(path.read_text(encoding="utf-8"))["regA"]["seed"] == 11
        seed2, src2 = SS.split_seed_for("regA", 22, path=path, fixed=True)
        assert (seed2, src2) == (11, "fixed:regA")
        # 壊れた表は空として扱う
        path.write_text("not json", encoding="utf-8")
        assert SS.load_table(path) == {}
        seed3, src3 = SS.split_seed_for("regA", 33, path=path, fixed=False)
        assert (seed3, src3) == (33, "run")
    print("test_file_roundtrip OK")


def main() -> None:
    test_resolve_pure()
    test_file_roundtrip()
    print("ALL OK")


if __name__ == "__main__":
    main()
