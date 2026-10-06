"""Showdown 由来のコミット済みキャッシュ (champions_agent/data/sim_cache.py / tools/refresh_sim_caches.py) のテスト。

2026-10-06: CI (Showdown なし) で test_team_build_sets / test_my_roster_resolution が落ちた (非参戦種の除外・シムの種 id の変換・
Champions で使える持ち物の判定が pokemon-showdown/ を直接読んでいた)。読み手を「まずコミット済みのキャッシュを読み、Showdown が
あれば一致を検査して違えば警告 (値はキャッシュ)」にした。ここでは一時ファイルで検査の分岐と生成ツールを確かめ、コミット済みの
キャッシュの形・由来と、読み手がキャッシュの値を返すことを確かめる。どれも Showdown の有無で結果が変わらない。

    python -m tests.test_sim_caches
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from champions_agent.data import sim_cache as C
from champions_agent.env import team_builder
from tools import refresh_sim_caches as R
from tools.team_build import sets as team_sets
from vision import normalize

FORMATS_SRC = "data/mods/champions/formats-data.ts"
FORMATS_TS = ("export const FormatsData = {\n"
              "\tabra: {\n\t\ttier: \"Illegal\",\n\t},\n"
              "\tmetang: {\n\t\ttier: \"Illegal\",\n\t},\n"
              "\tmetagross: {\n\t\ttier: \"OU\",\n\t},\n"
              "\tgarchomp: {\n\t\tisNonstandard: null,\n\t\ttier: \"OU\",\n\t},\n};\n")
BASE_ITEMS_TS = ("export const Items = {\n\tlifeorb: {\n\t\tname: \"Life Orb\",\n\t},\n\tchoiceband: {\n\t\tname: \"x\",\n\t},\n"
                 "\tabsolite: {\n\t\tisNonstandard: \"Past\",\n\t},\n};\n")
MOD_ITEMS_TS = ("export const Items = {\n\tchoiceband: {\n\t\tinherit: true,\n\t\tisNonstandard: \"Past\",\n\t},\n"
                "\tabsolite: {\n\t\tinherit: true,\n\t\tisNonstandard: null,\n\t},\n};\n")


def _spec(tmp: Path, name: str = "illegal") -> R.CacheSpec:
    return R.CacheSpec(name, tmp / f"{name}.json", "ids", (FORMATS_SRC,), normalize.parse_illegal_ids, sorted, set)


def _write_source(sd: Path, rel: str, text: str) -> Path:
    p = sd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _load(spec: R.CacheSpec, sd: Path):
    """load_checked を読み手と同じ引数で呼ぶ → (値, stderr の本文)"""
    err = io.StringIO()
    with redirect_stderr(err):
        value = C.load_checked(spec.cache_path, spec.key, spec.decode, [sd / s for s in spec.sources], spec.parse)
    return value, err.getvalue()


def test_parse_and_diff_summary():
    assert normalize.parse_illegal_ids(FORMATS_TS) == {"abra", "metang"}
    assert normalize.parse_illegal_ids("") == set()
    assert team_builder.parse_available_items(BASE_ITEMS_TS, MOD_ITEMS_TS) == {"lifeorb", "absolite"}
    assert team_builder.parse_available_items("", "") == set()
    assert C.diff_summary({"a": 1, "b": 2}, {"b": 3, "c": 4}) == \
        "Showdown にだけある 1 / キャッシュにだけある 1 / 値が違う 1 (例: +c, -a, ~b)"
    assert C.diff_summary({"x", "y"}, {"y", "z"}) == "Showdown にだけある 1 / キャッシュにだけある 1 (例: +z, -x)"
    assert C.diff_summary({"x"}, {"x"}) == "Showdown にだけある 0 / キャッシュにだけある 0"
    print("test_parse_and_diff_summary OK")


def test_load_checked_branches():
    """一致 → 警告なし / 不一致 → 警告してキャッシュの値 (2 回目は黙る) / Showdown なし → キャッシュの値、警告なし /
    キャッシュなし → 警告して Showdown の値、両方なし → 警告して空 / 壊れた JSON・鍵違いはキャッシュなし扱い"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sd = tmp / "showdown"
        spec = _spec(tmp)
        spec.cache_path.write_text(R.render(R.build_doc(spec, {"abra", "metang"}, {})), encoding="utf-8")
        assert _load(spec, sd) == ({"abra", "metang"}, "")                    # Showdown なし: キャッシュ、検査しない
        src = _write_source(sd, FORMATS_SRC, FORMATS_TS)
        assert _load(spec, sd) == ({"abra", "metang"}, "")                    # 一致
        src.write_text(FORMATS_TS.replace('\tmetagross: {\n\t\ttier: "OU"', '\tmetagross: {\n\t\ttier: "Illegal"'),
                       encoding="utf-8")
        value, err = _load(spec, sd)
        assert value == {"abra", "metang"}, value                             # 違っても値はキャッシュ (環境で変えない)
        assert err.startswith("[warn] ") and "Showdown にだけある 1" in err and "+metagross" in err, err
        assert "scripts/refresh_sim_caches.sh" in err
        assert _load(spec, sd) == ({"abra", "metang"}, "")                    # 同じ警告は 1 プロセス 1 回
        # キャッシュが無い: Showdown の値 (警告つき)。両方無い: 空 (警告つき)
        missing = _spec(tmp, "missing")
        value, err = _load(missing, sd)
        assert value == {"abra", "metang", "metagross"} and "読めない" in err and "Showdown のデータを使う" in err, (value, err)
        value, err = _load(_spec(tmp, "missing2"), tmp / "no_showdown")
        assert value == set() and "空として扱う" in err, (value, err)
        # 壊れた JSON / 中身の鍵が無い → キャッシュなし扱い
        broken = _spec(tmp, "broken")
        broken.cache_path.write_text("{not json", encoding="utf-8")
        value, err = _load(broken, tmp / "no_showdown")
        assert value == set() and "読めない" in err, (value, err)
        wrong_key = _spec(tmp, "wrong_key")
        wrong_key.cache_path.write_text(json.dumps({"_meta": {}, "other": ["abra"]}), encoding="utf-8")
        value, err = _load(wrong_key, tmp / "no_showdown")
        assert value == set() and "読めない" in err, (value, err)
    print("test_load_checked_branches OK")


def test_load_checked_multiple_sources():
    """元ファイルが 2 つ (本体 + mod の items.ts): 片方でも無ければ Showdown は無いものとして検査しない"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sd = tmp / "showdown"
        spec = R.CacheSpec("items", tmp / "items.json", "available_items", tuple(team_builder.AVAILABLE_ITEMS_SOURCES),
                           team_builder.parse_available_items, sorted, set)
        spec.cache_path.write_text(R.render(R.build_doc(spec, {"lifeorb", "absolite"}, {})), encoding="utf-8")
        base, mod = team_builder.AVAILABLE_ITEMS_SOURCES
        _write_source(sd, base, BASE_ITEMS_TS)
        assert _load(spec, sd) == ({"lifeorb", "absolite"}, "")              # mod が無い → 検査しない
        _write_source(sd, mod, MOD_ITEMS_TS.replace('isNonstandard: "Past"', "isNonstandard: null"))
        value, err = _load(spec, sd)
        assert value == {"lifeorb", "absolite"} and "+choiceband" in err, (value, err)
    print("test_load_checked_multiple_sources OK")


def test_refresh_write_and_check():
    """生成ツール: 書き出し → 同じ Showdown なら変わらない → check は一致 → 元が変われば check は不一致 → 書き直し。
    元が無ければ失敗。由来には元ファイルの sha256 が入り、生成時刻は入らない (同じ入力から同じ本文)"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sd = tmp / "showdown"
        spec = _spec(tmp)
        src = _write_source(sd, FORMATS_SRC, FORMATS_TS)
        [(name, status, _detail)] = R.refresh([spec], sd)
        assert (name, status) == ("illegal", R.WRITTEN), status
        doc = json.loads(spec.cache_path.read_text(encoding="utf-8"))
        assert doc["ids"] == ["abra", "metang"] and doc["_meta"]["count"] == 2
        assert set(doc["_meta"]) == {"showdown_commit", "showdown_commit_date", "sources", "count", "note"}
        meta_src = doc["_meta"]["sources"][f"{R.SHOWDOWN_PREFIX}/{FORMATS_SRC}"]
        assert meta_src["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
        assert [r[1] for r in R.refresh([spec], sd)] == [R.UNCHANGED]
        assert [r[1] for r in R.refresh([spec], sd, check=True)] == [R.MATCH]
        src.write_text(FORMATS_TS.replace("\tabra: {\n\t\ttier: \"Illegal\",\n\t},\n", ""), encoding="utf-8")
        [(_n, status, detail)] = R.refresh([spec], sd, check=True)
        assert status == R.MISMATCH and "キャッシュにだけある 1" in detail and "-abra" in detail, (status, detail)
        assert json.loads(spec.cache_path.read_text(encoding="utf-8"))["ids"] == ["abra", "metang"]   # check は書かない
        [(_n, status, detail)] = R.refresh([spec], sd)
        assert status == R.WRITTEN and "-abra" in detail, (status, detail)
        assert json.loads(spec.cache_path.read_text(encoding="utf-8"))["ids"] == ["metang"]
        assert [r[1] for r in R.refresh([spec], tmp / "no_showdown")] == [R.NO_SOURCE]
        assert [r[1] for r in R.refresh([spec], tmp / "no_showdown", check=True)] == [R.NO_SOURCE]
        out = io.StringIO()
        with redirect_stdout(out):
            rc = R.main(["--check", "--showdown-dir", str(tmp / "no_showdown")])        # check は書かない
        assert rc == 1 and out.getvalue().count(R.NO_SOURCE) == len(R.cache_specs()), out.getvalue()
    print("test_refresh_write_and_check OK")


def test_committed_caches():
    """コミット済みのキャッシュ: 生成ツールの出力そのもの (手で編集していない)、由来がそろっている、件数が中身と合う。
    既存テストが頼る値 (非参戦のメタング / シムの indeedee / 使えないこだわりハチマキ等) が入っている"""
    payloads = {}
    for spec in R.cache_specs():
        text = Path(spec.cache_path).read_text(encoding="utf-8")
        doc = json.loads(text)
        assert R.render(doc) == text, f"{spec.cache_path} が生成ツールの出力の形でない (scripts/refresh_sim_caches.sh で作り直す)"
        meta = doc["_meta"]
        assert re.fullmatch(r"[0-9a-f]{40}", meta["showdown_commit"] or ""), meta
        assert re.match(r"\d{4}-\d{2}-\d{2}T", meta["showdown_commit_date"] or ""), meta
        assert set(meta["sources"]) == {f"{R.SHOWDOWN_PREFIX}/{s}" for s in spec.sources}, meta["sources"]
        for info in meta["sources"].values():
            # ローカルで mod に手を入れた Showdown から作ることもある (RUNBOOK §1) ので、変更の有無は記録されていればよい
            assert info["modified"] in (True, False, None) and re.fullmatch(r"[0-9a-f]{64}", info["sha256"]), info
        payload = doc[spec.key]
        assert meta["count"] == len(payload) > 0, (spec.name, meta["count"])
        if isinstance(payload, list):
            assert payload == sorted(set(payload)), spec.name
        payloads[spec.name] = spec.decode(payload)
    illegal, table, items = payloads["champions_illegal_ids"], payloads["sim_species_table"], payloads["champions_available_items"]
    assert {"finneon", "metang"} <= illegal and not ({"metagross", "greninja", "garchomp"} & illegal)
    assert table["indeedee"] == table["indeedeef"] == 876 and table["taurospaldeablaze"] == 128 and "indeedeemale" not in table
    assert {"choicescarf", "lifeorb", "focussash", "garchompitez", "delphoxite"} <= items
    assert not ({"choiceband", "choicespecs", "assaultvest", "weaknesspolicy"} & items)
    print(f"test_committed_caches OK (非参戦 {len(illegal)} / シムの種 {len(table)} / 使える持ち物 {len(items)})")


def test_readers_return_cache():
    """読み手はキャッシュの値を返す (Showdown があってもなくても、違っていても同じ)"""
    by_name = {s.name: s for s in R.cache_specs()}

    def cached(name):
        s = by_name[name]
        return s.decode(json.loads(Path(s.cache_path).read_text(encoding="utf-8"))[s.key])
    assert normalize.champions_illegal_ids() == cached("champions_illegal_ids")
    assert team_sets.sim_species_table() == cached("sim_species_table")
    assert team_builder._available_item_ids() == cached("champions_available_items")
    print("test_readers_return_cache OK")


def main() -> None:
    test_parse_and_diff_summary()
    test_load_checked_branches()
    test_load_checked_multiple_sources()
    test_refresh_write_and_check()
    test_committed_caches()
    test_readers_return_cache()
    print("\nALL OK")


if __name__ == "__main__":
    main()
