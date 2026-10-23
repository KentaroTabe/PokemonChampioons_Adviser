"""再生の --data-mode restore で、当時の登録チーム (助言エンジンが読んだ登録) を version 行から復元するテスト (2026-10-23)。

    python -m tests.test_restore_team_text

背景: version 行の team.sha256 は registered_team() の本文 (直近の選出ロスター = 前の対戦の 6 体を使用率で補った Showdown 本文) の
sha で、対戦ごとに変わり得るうえ、助言エンジンが実際に読む config/my_team.json の登録 (advisor.my_team._load()) とも一致しない。
そこで version 行の team に registry (_load() の dict そのもの) と registry_sha256 を足し、復元はその登録に差し替える。
- advisor.my_team: registry_sha (正規形の sha16)、set_registry_override / clear_registry_override (_load() の差し替え)
- advisor.versions.summarize_versions: team_registry を渡すと team.registry / team.registry_sha256 を足す (version_id は変わらない)
- tools.inputs_pin: team_registry_at / check_team_registry / restore_plan (registry があれば整合を確かめて使う。無ければ従来どおり
  今の本文の sha と比べる) / DataSwitch (再生の間だけ差し替え、close で外す)
実データ (本体の my_team.json・使用率 DB) は読まない (一時ファイルと合成の DB だけ)。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from advisor import data_pin as DP
from advisor import my_team as MT
from advisor import versions as V
from tools import inputs_pin as IP
from tests.test_inputs_pin import _make_db

REG_THEN = {
    "カメックス": {"種族ID": "blastoise", "能力ポイント": {"h": 17, "c": 16, "s": 32}, "性格": "ひかえめ", "持ち物": "カメックスナイト",
                "特性": "げきりゅう", "技": ["だいちのはどう", "はどうだん", "あくのはどう", "からをやぶる"]},
    "バシャーモ": {"特性": "かそく"},
}
REG_NOW = {
    "カメックス": {"種族ID": "blastoise", "能力ポイント": {"h": 32, "b": 32}, "性格": "ずぶとい", "持ち物": "たべのこし",
                "技": ["ハイドロポンプ", "れいとうビーム", "ねっとう", "まもる"]},
    "バシャーモ": {"能力ポイント": {"a": 32, "s": 32}, "性格": "ようき", "持ち物": "バシャーモナイト", "特性": "かそく",
               "技": ["フレアドライブ", "インファイト", "かみなりパンチ", "みきり"]},
}


class _TempMyTeam:
    """advisor.my_team が読む config/my_team.json を一時ファイルに向ける (キャッシュも外す)"""

    def __init__(self, data: dict):
        self.data = data

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "my_team.json"
        self.write(self.data)
        self.orig = MT.CONFIG_PATH
        MT.CONFIG_PATH = self.path
        MT._CACHE, MT._CACHE_MTIME = None, -1.0
        return self

    def write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MT._CACHE, MT._CACHE_MTIME = None, -1.0      # 同じ mtime の書き換えでも読み直す

    def __exit__(self, *exc):
        MT.CONFIG_PATH = self.orig
        MT._CACHE, MT._CACHE_MTIME = None, -1.0
        MT.clear_registry_override()
        self.dir.cleanup()


def _ver(t, snap, team_sha, registry=None, registry_sha="auto"):
    team = {"sha256": team_sha, "species": ["blastoise", "blaziken"]}
    if registry is not None:
        team["registry"] = registry
        team["registry_sha256"] = MT.registry_sha(registry) if registry_sha == "auto" else registry_sha
    return {"type": "version", "t": t, "rules": {"usage_snapshot": snap}, "team": team}


def _engine_view() -> dict:
    """助言エンジンが読む経路 (get_my_build / get_my_moves / has_build / registered_species_ja) の結果"""
    return {ja: {"build": MT.get_my_build(ja), "moves": MT.get_my_moves(ja), "has": MT.has_build(ja)}
            for ja in ("カメックス", "バシャーモ")} | {"_species": sorted(MT.registered_species_ja())}


def test_registry_sha_pure():
    a = {"b": {"技": ["x", "y"], "性格": "n"}, "a": {}}
    b = {"a": {}, "b": {"性格": "n", "技": ["x", "y"]}}
    assert MT.registry_sha(a) == MT.registry_sha(b) and len(MT.registry_sha(a)) == 16
    assert MT.registry_sha({"a": {"技": ["y", "x"]}}) != MT.registry_sha({"a": {"技": ["x", "y"]}})   # 技の順は意味がある
    assert MT.registry_sha(None) is None and MT.registry_sha([]) is None and MT.registry_sha({}) is not None
    print("test_registry_sha_pure OK")


def test_summarize_versions_registry_field():
    """team_registry を渡すと team.registry / registry_sha256 が付く。version_id (digest) は変わらない、渡さなければ欄も無い"""
    args = ("pkg", "/m/sel.pt", "s1", "pkg", ["a"], ["blastoise"], "/m/rl.zip", "r1", True, None, "team1", "g", "d", "e", "v1")
    d0 = V.summarize_versions(*args)
    d1 = V.summarize_versions(*args, team_registry=REG_THEN)
    assert "registry" not in d0["team"] and "registry_sha256" not in d0["team"]
    assert d1["team"]["registry"] == REG_THEN and d1["team"]["registry_sha256"] == MT.registry_sha(REG_THEN)
    assert d1["team"]["sha256"] == "team1" and d1["version_id"] == d0["version_id"]
    assert V.identity_of(d1) == V.identity_of(d0)
    # runtime_versions が読む登録の写し = 助言エンジンが読む登録 (_load())
    with _TempMyTeam(REG_THEN):
        snap = V._team_registry()
        assert snap == REG_THEN and snap is not MT._load()
    print("test_summarize_versions_registry_field OK")


def test_registry_override():
    """差し替え中は _load() の経路がすべて記録の登録を読み、外せばファイルに戻る。書き込みの経路 (_load_file) には効かない"""
    with _TempMyTeam(REG_NOW):
        now = _engine_view()
        MT.set_registry_override(REG_THEN)
        then = _engine_view()
        assert then["バシャーモ"]["build"]["item_ja"] is None and then["バシャーモ"]["has"] is False      # 当時は特性だけ
        assert then["カメックス"]["moves"] == REG_THEN["カメックス"]["技"]
        assert MT._load_file() == REG_NOW                                                             # ファイルはそのまま
        MT.clear_registry_override()
        assert _engine_view() == now and now["バシャーモ"]["has"] is True
        try:
            MT.set_registry_override(["x"])
            raise AssertionError("dict でない差し替えが通った")
        except TypeError:
            pass
    print("test_registry_override OK")


def test_team_registry_at_and_check():
    recs = [_ver(10.0, 1, "tA", REG_THEN), {"type": "advice", "t": 11.0}, _ver(20.0, 1, "tB", REG_NOW)]
    assert IP.team_registry_at(recs, 11.0)["registry"] == REG_THEN
    assert IP.team_registry_at(recs, 21.0)["registry"] == REG_NOW
    assert IP.team_registry_at(recs, 1.0)["registry"] == REG_THEN                       # 前に無ければ最初
    assert IP.team_registry_at([_ver(10.0, 1, "tA")], 11.0) is None                    # 旧ログ (欄なし)
    assert IP.team_registry_at([], 11.0) is None
    reg, why = IP.check_team_registry(IP.team_registry_at(recs, 11.0))
    assert reg == REG_THEN and why is None
    for rec, frag in (({"registry": REG_THEN, "registry_sha256": "0" * 16}, "記録の不整合"),
                      ({"registry": REG_THEN, "registry_sha256": None}, "記録の不整合"),
                      ({"registry": None, "registry_sha256": MT.registry_sha(REG_THEN)}, "記録の不整合")):
        reg, why = IP.check_team_registry(rec)
        assert reg is None and frag in why, (rec, why)
    print("test_team_registry_at_and_check OK")


def test_restore_plan_with_registry():
    want = {"usage_snapshot": 67, "team_sha16": "teamA", "bank_sha16": None}
    rec = {"registry": REG_THEN, "registry_sha256": MT.registry_sha(REG_THEN)}
    # 記録があれば今の本文の sha (teamB) とは比べない: 復元できる
    p = IP.restore_plan(want, [67], "teamB", {}, team_registry=rec)
    assert p["ok"] and p["team_registry"] == REG_THEN and p["used"]["team_registry_sha16"] == rec["registry_sha256"], p
    assert p["usage_snapshot"] == 67
    # 記録の不整合は判定不能 (今の登録へ退避しない)
    bad = dict(rec, registry_sha256="0" * 16)
    p = IP.restore_plan(want, [67], "teamA", {}, team_registry=bad)
    assert not p["ok"] and p["team_registry"] is None and any("記録の不整合" in r for r in p["reasons"]), p
    assert p["used"]["team_registry_sha16"] is None and p["usage_snapshot"] is None
    # 記録が無い旧ログは従来どおり (今の本文の sha と違えば判定不能、同じなら復元 = 差し替えなし)
    p = IP.restore_plan(want, [67], "teamB", {})
    assert not p["ok"] and any("今の登録" in r for r in p["reasons"]) and p["team_registry"] is None
    p = IP.restore_plan(want, [67], "teamA", {})
    assert p["ok"] and p["team_registry"] is None and "team_registry_sha16" not in p["used"]
    # snapshot が無ければ registry があっても判定不能
    p = IP.restore_plan(dict(want, usage_snapshot=None), [67], "teamA", {}, team_registry=rec)
    assert not p["ok"] and p["team_registry"] is None
    # current: 差し替えない。記録と今の登録が違えば notes
    p = IP.restore_plan(want, [67], "teamA", {}, mode="current", latest_snapshot=67, team_registry=rec,
                        current_registry_sha16=MT.registry_sha(REG_NOW))
    assert p["ok"] and p["team_registry"] is None and any("登録 (my_team)" in n for n in p["notes"]), p
    # 版の鍵: 登録が違えば別の版
    k1 = IP.plan_key(IP.restore_plan(want, [67], "x", {}, team_registry=rec))
    rec2 = {"registry": REG_NOW, "registry_sha256": MT.registry_sha(REG_NOW)}
    k2 = IP.plan_key(IP.restore_plan(want, [67], "x", {}, team_registry=rec2))
    assert k1 != k2
    print("test_restore_plan_with_registry OK")


def test_data_switch_restores_registry_independent_of_file():
    """DataSwitch restore: version 行に registry があれば、今の my_team.json を変えても再生の登録は同じ (当時の登録)。
    無い旧ログは今の本文と違えば判定不能、記録の sha が合わなければ判定不能。close で差し替えを外す"""
    with tempfile.TemporaryDirectory() as d, _TempMyTeam(REG_NOW) as mt:
        db = _make_db(Path(d) / "u.sqlite3")
        local = {"db": str(db), "snapshots": [1, 2], "latest": 2, "banks": {}, "team_sha16": "teamNow",
                 "team_registry_sha16": MT.registry_sha(REG_NOW)}
        recs = [_ver(10.0, 1, "teamThen", REG_THEN)]
        try:
            ds = IP.DataSwitch("restore", local)
            assert ds.plan("x", recs, 11.0)["ok"]
            v1 = _engine_view()
            mt.write({"カメックス": {"技": ["まもる"]}})                     # 今のファイルを変える
            assert ds.plan("x", recs, 12.0)["ok"]
            v2 = _engine_view()
            assert v1 == v2 and v1["カメックス"]["moves"] == REG_THEN["カメックス"]["技"], (v1, v2)
            # 旧ログ (registry なし、本文の sha が今と違う) / 記録の不整合 → 判定不能
            assert not ds.plan("old", [_ver(10.0, 1, "teamThen")], 11.0)["ok"]
            assert not ds.plan("bad", [_ver(10.0, 1, "teamThen", REG_THEN, registry_sha="0" * 16)], 11.0)["ok"]
            sm = ds.summary()
            assert sm["n_decisions"] == 4 and sm["n_unrestored_decisions"] == 2 and sm["local"]["team_registry_sha16"]
            ds.close()
            assert _engine_view()["カメックス"]["moves"] == ["まもる"]           # 外せば今のファイル
            # current: 今のファイルを読む (差し替えない)
            cur = IP.DataSwitch("current", local)
            p = cur.plan("x", recs, 11.0)
            assert p["ok"] and _engine_view()["カメックス"]["moves"] == ["まもる"]
            cur.close()
        finally:
            DP.clear_override()
    assert MT._REGISTRY_OVERRIDE is None
    print("test_data_switch_restores_registry_independent_of_file OK")


def main():
    test_registry_sha_pure()
    test_summarize_versions_registry_field()
    test_registry_override()
    test_team_registry_at_and_check()
    test_restore_plan_with_registry()
    test_data_switch_restores_registry_independent_of_file()
    print("ALL OK")


if __name__ == "__main__":
    main()
