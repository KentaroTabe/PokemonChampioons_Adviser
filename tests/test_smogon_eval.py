"""Smogon の Champions 統計の評価基盤のテスト (2026-10-30)。

    python -m tests.test_smogon_eval

- 取得入口 (usage_scraper.fetch_smogon_chaos_exact): 形式指定が無視されない (gen9ou に退避しない)、保存と manifest、再取得しない
- 変換 (advisor.usage_smogon): 保有確率 (個体の重み W で割る) と重みの割合 (全技の重みで割る) の区別、メガの表記の名寄せ、
  合法性の検査 (落とした件数)、配分の単位
- 対応比較 (tools.usage_prior_eval): 判明技だけ、反復の水増しなし、後の行を混ぜない、時系列の切り分け、ブートストラップ、report の形
"""
from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import time
from pathlib import Path

from advisor import usage_smogon as us
from champions_agent.data.sources import usage_scraper as sc
from tools import usage_prior_eval as ev

KNOWN = {"salamence", "salamencemega", "lucario", "lucariomega", "lucariomegaz", "meowsticfemale",
         "meowsticfemalemega", "yanmega", "meganium", "garchomp", "garchompmegaz"}


def _is_known(sid: str) -> bool:
    return sid in KNOWN


def _entry(abilities, items, moves, spreads=None, raw=999):
    return {"Raw count": raw, "usage": 0.1, "Abilities": abilities, "Items": items, "Moves": moves,
            "Spreads": spreads or {}, "Teammates": {}, "Checks and Counters": {}}


def _payload(data, fmt="gen9championsbssregmc", cutoff=1500):
    return {"info": {"metagame": fmt, "cutoff": cutoff, "number of battles": 100}, "data": data}


def _ok(_x):
    return True


# ------------------------------------------------------------------ 変換
def test_hold_probability_vs_weight_share():
    """技の保有確率 = 重み / W (W = 特性の重みの合計)。全技の重みで割った値とは違う。Raw count は分母に使わない"""
    e = _entry({"aerilate": 100.0}, {"salamencite": 90.0, "nothing": 10.0},
               {"doubleedge": 80.0, "dragondance": 70.0, "earthquake": 70.0, "roost": 50.0, "dracometeor": 30.0, "": 100.0},
               raw=12345)
    conv, dropped = us.convert_entry(e, _ok, _ok, _ok)
    assert conv["weight"] == 100.0 and conv["weight_basis"] == "Abilities", conv["weight"]
    assert abs(conv["moves_hold"]["doubleedge"] - 0.80) < 1e-12
    share = 80.0 / sum(e["Moves"].values())             # 全技の重みの合計 (4W) で割った値
    assert abs(share - 0.20) < 1e-12 and abs(conv["moves_hold"]["doubleedge"] - share) > 0.5
    assert abs(sum(conv["moves_hold"].values()) - 3.0) < 1e-12    # 空き枠 1 つ分を除いた保有確率の合計 (最大 4)
    assert abs(conv["move_empty_slots"] - 1.0) < 1e-12
    assert "" not in conv["moves_hold"]
    # 技を使う確率 = 保有 ÷ 4 (暫定。保有と分けて持つ)
    for m, h in conv["moves_hold"].items():
        assert abs(conv["moves_use"][m] - h / 4.0) < 1e-12
    # 持ち物: 個体あたり 1 つ。"nothing" は分母に入れて一覧から外す
    assert abs(conv["items"]["salamencite"] - 0.9) < 1e-12 and "nothing" not in conv["items"]
    assert abs(conv["item_nothing"] - 0.1) < 1e-12
    assert abs(conv["abilities"]["aerilate"] - 1.0) < 1e-12
    assert dropped["move_hold_over_1"] == 0
    print("test_hold_probability_vs_weight_share OK")


def test_mega_name_mapping_and_group():
    """Showdown のメガ表記 → この プロジェクトの id、メガの基本種、基本種 + メガの合算"""
    assert us.species_project_id("Salamence-Mega") == "salamencemega"
    assert us.species_project_id("Lucario-Mega-Z") == "lucariomegaz"
    assert us.species_project_id("Meowstic-F-Mega") == "meowsticfemalemega"
    assert us.species_project_id("Indeedee-F", canonical=lambda s: {"indeedeef": "indeedeefemale"}.get(s, s)) == "indeedeefemale"
    assert us.mega_base_id("salamencemega", _is_known) == "salamence"
    assert us.mega_base_id("lucariomegaz", _is_known) == "lucario"
    assert us.mega_base_id("meowsticfemalemega", _is_known) == "meowsticfemale"
    assert us.mega_base_id("yanmega", _is_known) is None      # "yan" は図鑑に無い: メガではない
    assert us.mega_base_id("meganium", _is_known) is None
    assert us.mega_base_id("salamence", _is_known) is None

    payload = _payload({
        "Salamence": _entry({"intimidate": 25.0}, {"lifeorb": 25.0}, {"dracometeor": 20.0, "earthquake": 10.0, "": 70.0}),
        "Salamence-Mega": _entry({"aerilate": 75.0}, {"salamencite": 75.0}, {"doubleedge": 60.0, "earthquake": 60.0, "": 180.0}),
    })
    conv = us.convert_chaos(payload, _is_known, _ok, _ok, _ok)
    sp = conv["species"]
    assert set(sp) == {"salamence", "salamencemega"}, set(sp)
    idx = us.group_index(sp, _is_known)
    assert idx == {"salamence": ["salamence", "salamencemega"]}, idx
    pooled = us.group_view(sp, idx, "salamence", _is_known, "pooled")
    # earthquake: (25 × 0.4 + 75 × 0.8) / 100 = 0.7。doubleedge: 75 × 0.8 / 100 = 0.6。lifeorb: 0.25、salamencite: 0.75
    assert abs(pooled["moves_hold"]["earthquake"] - 0.7) < 1e-12, pooled["moves_hold"]
    assert abs(pooled["moves_hold"]["doubleedge"] - 0.6) < 1e-12
    assert abs(pooled["items"]["lifeorb"] - 0.25) < 1e-12 and abs(pooled["items"]["salamencite"] - 0.75) < 1e-12
    # pooled はメガ形態の状態でも基本種 + メガ (DB と同じ条件)、known はメガの項目だけ
    assert us.group_view(sp, idx, "salamencemega", _is_known, "pooled")["moves_hold"] == pooled["moves_hold"]
    known = us.group_view(sp, idx, "salamencemega", _is_known, "known")
    assert abs(known["moves_hold"]["earthquake"] - 0.8) < 1e-12 and "dracometeor" not in known["moves_hold"]
    assert us.group_view(sp, idx, "salamence", _is_known, "known")["moves_hold"] == pooled["moves_hold"]
    assert us.group_view(sp, idx, "garchomp", _is_known) is None
    print("test_mega_name_mapping_and_group OK")


def test_legality_check_counts_drops():
    """図鑑に無い種・技、名前表に無い持ち物・特性を落として件数と重みを残す。分母 W は変えない"""
    payload = _payload({
        "Garchomp": _entry({"roughskin": 50.0, "fakeability": 50.0}, {"choicescarf": 60.0, "fakeitem": 30.0, "leek": 10.0},
                           {"earthquake": 90.0, "fakemove": 40.0, "": 270.0}),
        "Fakemon": _entry({"x": 10.0}, {"y": 10.0}, {"earthquake": 10.0, "": 30.0}),
        "": _entry({"x": 1.0}, {"y": 1.0}, {"": 4.0}),
    })
    conv = us.convert_chaos(payload, species_ok=lambda s: s == "garchomp", move_ok=lambda m: m == "earthquake",
                            item_ok=lambda i: i in ("choicescarf", "stick"), ability_ok=lambda a: a == "roughskin")
    ck = conv["checks"]
    assert ck["species_total"] == 2 and ck["species_kept"] == 1, ck
    assert ck["species_dropped"] == ["Fakemon"] and abs(ck["species_dropped_weight"] - 10.0) < 1e-12
    assert ck["moves_dropped"] == {"fakemove": {"n_species": 1, "weight": 40.0}}, ck["moves_dropped"]
    assert ck["items_dropped"] == {"fakeitem": {"n_species": 1, "weight": 30.0}}, ck["items_dropped"]
    assert ck["abilities_dropped"] == {"fakeability": {"n_species": 1, "weight": 50.0}}
    g = conv["species"]["garchomp"]
    assert g["weight"] == 100.0
    assert abs(g["moves_hold"]["earthquake"] - 0.9) < 1e-12        # 落としても分母は W のまま
    assert abs(g["items"]["choicescarf"] - 0.6) < 1e-12 and abs(g["items"]["stick"] - 0.1) < 1e-12   # leek → stick
    assert abs(g["abilities"]["roughskin"] - 0.5) < 1e-12
    print("test_legality_check_counts_drops OK")


def test_spread_unit():
    """配分: 0〜32 なら能力ポイントのまま、32 を超えれば努力値として能力ポイントに換算する"""
    pts = {"A": _entry({"a": 1.0}, {"b": 1.0}, {"": 4.0}, {"Adamant:2/32/0/0/0/32": 1.0})}
    evs = {"A": _entry({"a": 1.0}, {"b": 1.0}, {"": 4.0}, {"Jolly:4/252/0/0/0/252": 1.0})}
    assert us.detect_spread_unit(pts) == "points" and us.detect_spread_unit(evs) == "evs"
    assert us.parse_spread("Adamant:2/32/0/0/0/32", "points") == {
        "nature": "adamant", "points": {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}}
    got = us.parse_spread("Jolly:4/252/0/0/0/252", "evs")
    assert got["nature"] == "jolly" and got["points"] == {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}, got
    assert us.parse_spread("bad", "points") is None
    conv = us.convert_chaos(_payload(evs), _ok, _ok, _ok, _ok)
    assert conv["spread_unit"] == "evs" and conv["species"]["a"]["spreads"][0]["p"] == 1.0
    print("test_spread_unit OK")


# ------------------------------------------------------------------ 取得入口
class _Resp:
    def __init__(self, status, body: bytes = b""):
        self.status_code = status
        self.content = body


def test_exact_fetch_does_not_fall_back():
    """形式・cutoff を指定どおりに取る。取れない・中身が違えば止まる (gen9ou に退避しない)。保存済みなら取らない"""
    calls = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def get_404(url, **kw):
            calls.append(url)
            return _Resp(404)
        try:
            sc.fetch_smogon_chaos_exact("2026-09", "gen9championsbssregmc", 1500, root=root, http_get=get_404)
            raise AssertionError("404 で止まっていない")
        except sc.SmogonChaosError:
            pass
        assert calls == ["https://www.smogon.com/stats/2026-09/chaos/gen9championsbssregmc-1500.json"], calls
        assert not any("gen9ou" in u for u in calls)

        # 対象外の形式 (gen9ou) は取りに行かない
        calls.clear()
        for bad in (("2026-09", "gen9ou", 1500), ("2026-9", "gen9championsbssregmc", 1500),
                    ("2026-09", "gen9championsbssregmc", "x")):
            try:
                sc.fetch_smogon_chaos_exact(*bad, root=root, http_get=get_404)
                raise AssertionError(f"{bad} で止まっていない")
            except sc.SmogonChaosError:
                pass
        assert calls == []

        # 中身の形式・cutoff が指定と違う (サーバーが別の統計を返した) なら止まって保存しない
        for body in (_payload({"A": _entry({"a": 1.0}, {}, {})}, fmt="gen9ou"),
                     _payload({"A": _entry({"a": 1.0}, {}, {})}, cutoff=0)):
            try:
                sc.fetch_smogon_chaos_exact("2026-09", "gen9championsbssregmc", 1500, root=root,
                                            http_get=lambda url, **kw: _Resp(200, json.dumps(body).encode()))
                raise AssertionError("中身の違いで止まっていない")
            except sc.SmogonChaosError:
                pass
        raw_path, man_path = sc.smogon_chaos_paths("2026-09", "gen9championsbssregmc", 1500, root)
        assert not raw_path.exists() and not man_path.exists()

        # 取れたら元のバイト列と manifest (sha256・取得時刻・サイズ・変換版) を保存する
        body = json.dumps(_payload({"Garchomp": _entry({"roughskin": 1.0}, {}, {})})).encode()
        payload, man = sc.fetch_smogon_chaos_exact("2026-09", "gen9championsbssregmc", 1500, root=root,
                                                   http_get=lambda url, **kw: _Resp(200, body), now=lambda: 1793000000.0)
        assert raw_path.read_bytes() == body
        saved = json.loads(man_path.read_text(encoding="utf-8"))
        import hashlib
        assert saved["sha256"] == hashlib.sha256(body).hexdigest() and saved["size"] == len(body)
        assert saved["fetched_at"] == 1793000000.0 and saved["convert_version"] == sc.SMOGON_CONVERT_VERSION
        assert saved["format"] == "gen9championsbssregmc" and saved["cutoff"] == 1500 and saved["month"] == "2026-09"
        assert man["reused"] is False and payload["info"]["metagame"] == "gen9championsbssregmc"

        # 2 回目は取りに行かない (再取得しない)
        def must_not_call(url, **kw):
            raise AssertionError("再取得した")
        payload2, man2 = sc.fetch_smogon_chaos_exact("2026-09", "gen9championsbssregmc", 1500, root=root,
                                                     http_get=must_not_call)
        assert man2["reused"] is True and payload2 == payload

        # 保存済みのファイルが manifest と違えば止まる
        raw_path.write_bytes(body + b" ")
        try:
            sc.load_smogon_chaos_saved("2026-09", "gen9championsbssregmc", 1500, root)
            raise AssertionError("sha の違いで止まっていない")
        except sc.SmogonChaosError:
            pass
    print("test_exact_fetch_does_not_fall_back OK")


# ------------------------------------------------------------------ 観測の取り出し
def _mon(species=None, revealed=(), item=None, guess=False):
    m = {"species": species, "revealed": list(revealed), "item": item}
    if guess:
        m["guess"] = True
    return m


def _adv(t, party):
    return {"type": "advice", "t": t, "state": {"opponent": {"party": party}}}


JA = {"じしん": "earthquake", "げきりん": "outrage", "つるぎのまい": "swordsdance", "ドラゴンダンス": "dragondance"}


def test_extract_observations_dedup_and_order():
    """advice 行を時刻順に読み、(枠, 技) の初出だけを観測にする。推定の種の時点の判明は数えない。後の行を混ぜない"""
    rows = [
        {"type": "version", "t": 100.0},
        # ファイルの順と時刻の順が違う (時刻順に読む)
        _adv(130.0, [_mon("garchompmegaz", ["じしん", "げきりん", "つるぎのまい"]),
                     _mon("salamence", ["ドラゴンダンス"], item="lifeorb")]),
        _adv(110.0, [_mon("garchomp", ["じしん"]), _mon("salamence", ["ドラゴンダンス"], guess=True)]),
        _adv(120.0, [_mon("garchomp", ["じしん", "じしん", "げきりん", "ふめいのわざ"], item="megastone"),
                     _mon("salamence", ["ドラゴンダンス"], item="lifeorb")]),
        _adv(125.0, [_mon("garchomp", ["じしん", "げきりん"], item=None),
                     _mon("salamence", ["ドラゴンダンス"], item="choicescarf")]),
    ]
    obs, stats = ev.extract_observations(rows, "b1", JA.get, is_mega=lambda s: s.endswith("megaz"))
    got = [(o["slot"], o["kind"], o["value"], o["species"], o["mega_known"], o["t"]) for o in obs]
    assert got == [
        (0, "move", "earthquake", "garchomp", False, 110.0),     # 初出の時点の種族 (後のメガを混ぜない)
        (0, "move", "outrage", "garchomp", False, 120.0),
        (1, "item", "lifeorb", "salamence", False, 120.0),
        (0, "move", "swordsdance", "garchompmegaz", True, 130.0),
    ], got
    # 枠 1 の ドラゴンダンス は種族が推定の時点で判明したので数えない (後で確定しても観測にしない)
    assert stats == {"species_unknown_move": 1, "move_unresolved": 1, "item_megastone_unknown": 1,
                     "item_conflict": 1}, stats
    # 枠 0 の megastone (辞書外のメガストーン) は持ち物の観測にしない
    assert not any(o["kind"] == "item" and o["slot"] == 0 for o in obs)
    assert ev.battle_start(rows) == 100.0
    assert ev.roster_species(rows) == {"garchomp", "garchompmegaz", "salamence"}
    print("test_extract_observations_dedup_and_order OK")


def test_likelihood_only_revealed_and_bootstrap():
    """判明した事象だけの対数尤度 (見えなかった技は数えない)。下限、対応は両方に種がある観測だけ、対戦単位の区間"""
    sm = {"garchomp": {"moves_hold": {"earthquake": 0.9, "outrage": 0.5, "swordsdance": 0.6}, "items": {"choicescarf": 0.3}}}
    db = {"garchomp": {"moves_hold": {"earthquake": 0.8, "outrage": 0.25}, "items": {"choicescarf": 0.6}},
          "salamence": {"moves_hold": {"dragondance": 0.7}, "items": {}}}
    obs = [
        {"battle": "b1", "slot": 0, "kind": "move", "value": "earthquake", "species": "garchomp", "mega_known": False, "t": 1},
        {"battle": "b1", "slot": 0, "kind": "move", "value": "outrage", "species": "garchomp", "mega_known": False, "t": 2},
        {"battle": "b2", "slot": 0, "kind": "move", "value": "swordsdance", "species": "garchomp", "mega_known": False, "t": 3},
        {"battle": "b2", "slot": 1, "kind": "move", "value": "dragondance", "species": "salamence", "mega_known": False, "t": 4},
        {"battle": "b3", "slot": 0, "kind": "item", "value": "choicescarf", "species": "garchomp", "mega_known": False, "t": 5},
    ]
    scored = ev.score_observations(obs, lambda s, m: sm.get(s), db.get, floor=0.01)
    assert abs(scored[0]["ll_sm"] - math.log(0.9)) < 1e-12 and abs(scored[0]["ll_db"] - math.log(0.8)) < 1e-12
    assert scored[2]["p_db"] == 0.01 and scored[2]["in_db"] is False and scored[2]["in_sm"] is True   # 一覧に無い → 下限
    assert scored[3]["sm_found"] is False and scored[3]["p_sm"] is None
    s = ev.summarize(scored, "move", n_boot=500, seed=1, level=0.95)
    assert s["n_obs"] == 4 and s["n_paired"] == 3 and s["n_missing_smogon"] == 1 and s["n_missing_db"] == 0
    exp = (math.log(0.9 / 0.8) + math.log(0.5 / 0.25) + math.log(0.6 / 0.01)) / 3
    assert abs(s["mean_diff"] - exp) < 1e-12, s
    # 見えなかった技 (DB の dragondance 等) は尤度に入らない: 観測の数だけ
    assert s["n_battles_paired"] == 2
    lo, hi = s["ci"]
    assert lo <= hi
    # 区間は対戦単位: 対戦 b1 の 2 観測は一緒に抽出される (観測単位なら取りうる値が違う)
    d1 = math.log(0.9 / 0.8) + math.log(0.5 / 0.25)
    d2 = math.log(0.6 / 0.01)
    possible = {round(d1 / 2, 9), round(d2, 9), round((d1 + d2) / 3, 9)}
    pb = [(d1, 2), (d2, 1)]
    assert {round(ev.bootstrap_ci(pb, 50, seed, 0.0)[0], 9) for seed in range(20)} <= possible
    assert ev.bootstrap_ci([(1.0, 1)], 100, 1, 0.95) == (None, None)
    assert ev.bootstrap_ci(pb, 300, 7, 0.95) == ev.bootstrap_ci(pb, 300, 7, 0.95)     # seed で再現する
    si = ev.summarize(scored, "item", n_boot=100, seed=1, level=0.95)
    assert si["n_paired"] == 1 and abs(si["mean_diff"] - math.log(0.3 / 0.6)) < 1e-12 and si["ci"] == [None, None]
    # learnset の照合 (感度): 覚えない技に False、判定できない種に None、持ち物は None
    marked = ev.mark_learnable(scored, lambda sid, mid: {"garchomp": mid != "swordsdance"}.get(sid))
    assert [r["learnable"] for r in marked] == [True, True, False, None, None]
    assert [r["learnable"] for r in ev.mark_learnable(scored, None)] == [None] * 5
    print("test_likelihood_only_revealed_and_bootstrap OK")


def test_time_split():
    """評価は統計の月の翌月 1 日 0 時 (ローカル) 以降に始まった対戦だけ"""
    start = ev.eval_start_epoch("2026-09")
    assert start == time.mktime((2026, 10, 1, 0, 0, 0, 0, 0, -1))
    assert ev.eval_start_epoch("2026-12") == time.mktime((2027, 1, 1, 0, 0, 0, 0, 0, -1))
    battles = [{"id": "a", "t0": start - 1}, {"id": "b", "t0": start}, {"id": "c", "t0": None}, {"id": "d", "t0": start + 86400}]
    keep, before, no_t = ev.split_by_time(battles, start)
    assert [b["id"] for b in keep] == ["b", "d"] and [b["id"] for b in before] == ["a"] and [b["id"] for b in no_t] == ["c"]
    print("test_time_split OK")


# ------------------------------------------------------------------ report の形 (図鑑・名前表は本物、DB・ログ・統計は合成)
def _make_db(path: Path):
    c = sqlite3.connect(str(path))
    c.executescript("""
        CREATE TABLE usage_snapshot (id INTEGER PRIMARY KEY, source TEXT, format TEXT, fetched_at TEXT);
        CREATE TABLE move_usage (id INTEGER PRIMARY KEY, snapshot_id INT, pokemon_name TEXT, move_name TEXT, usage_percent REAL);
        CREATE TABLE item_usage (id INTEGER PRIMARY KEY, snapshot_id INT, pokemon_name TEXT, item_name TEXT, usage_percent REAL);
    """)
    c.execute("INSERT INTO usage_snapshot VALUES (75, 'championsbattledata+pokedb', 'champions-singles', '2026-10-22 21:32:02')")
    c.executemany("INSERT INTO move_usage (snapshot_id, pokemon_name, move_name, usage_percent) VALUES (?,?,?,?)",
                  [(75, "garchomp", "earthquake", 80.0), (75, "garchomp", "outrage", 30.0), (74, "garchomp", "earthquake", 1.0)])
    c.executemany("INSERT INTO item_usage (snapshot_id, pokemon_name, item_name, usage_percent) VALUES (?,?,?,?)",
                  [(75, "garchomp", "choicescarf", 40.0)])
    c.commit()
    c.close()


def test_report_shape():
    """report (JSON + Markdown) に評価範囲・対応比較・カバー率・変換の検査・種ごとの表・次の手順が出る"""
    start = ev.eval_start_epoch("2026-09")
    payload = _payload({
        "Garchomp": _entry({"roughskin": 100.0}, {"choicescarf": 50.0, "lifeorb": 50.0},
                           {"earthquake": 90.0, "outrage": 60.0, "swordsdance": 50.0, "": 200.0}),
        "Fakemon": _entry({"x": 1.0}, {"y": 1.0}, {"": 4.0}),
    })
    manifest = {"url": "u", "sha256": "ab" * 32, "size": 10, "fetched_at": 1.0, "fetched_at_utc": "x", "convert_version": 1}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = td / "db.sqlite3"
        _make_db(db)
        logs = td / "battles"
        logs.mkdir()
        old = [{"type": "session", "t": start - 100, "dataset_kind": "real"},
               _adv(start - 90, [_mon("garchomp", ["じしん"])])]
        new = [{"type": "session", "t": start + 100, "dataset_kind": "real"},
               _adv(start + 110, [_mon("garchomp", ["じしん"], item="choicescarf"), _mon("mimikyu", [])]),
               _adv(start + 120, [_mon("garchomp", ["じしん", "げきりん"], item="choicescarf"), _mon("mimikyu", [])])]
        sim = [{"type": "session", "t": start + 200, "dataset_kind": "sim"}, _adv(start + 210, [_mon("garchomp", ["じしん"])])]
        for name, rows in (("battle_a.jsonl", old), ("battle_b.jsonl", new), ("battle_c.jsonl", sim)):
            (logs / name).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        rep = ev.build_report("2026-09", "gen9championsbssregmc", 1500, payload, manifest, db, 75, logs, n_boot=50)
    for key in ("stats", "db", "battles", "params", "conversion_checks", "extraction", "results", "results_by_view",
                "coverage", "per_species", "observations", "next_steps", "learnset"):
        assert key in rep, key
    assert rep["stats"]["sha256"] == "ab" * 32 and rep["stats"]["month"] == "2026-09" and rep["stats"]["cutoff"] == 1500
    assert rep["stats"]["convert_version"] == sc.SMOGON_CONVERT_VERSION
    assert rep["db"]["snapshot_id"] == 75 and rep["db"]["fetched_at"] == "2026-10-22 21:32:02"
    bt = rep["battles"]
    assert (bt["n_files"], bt["n_eval"], bt["n_before"], bt["n_not_real"]) == (3, 1, 1, 1), bt
    mv = rep["results"]["move"]
    assert mv["n_paired"] == 2, mv          # じしん (反復は 1 標本) + げきりん
    exp = (math.log(0.9 / 0.8) + math.log(0.6 / 0.3)) / 2
    assert abs(mv["mean_diff"] - exp) < 1e-9, mv
    it = rep["results"]["item"]
    assert it["n_paired"] == 1 and abs(it["mean_diff"] - math.log(0.5 / 0.4)) < 1e-9
    assert rep["conversion_checks"]["species_dropped"] == ["Fakemon"]
    cov = rep["coverage"]["roster"]
    assert cov["n_species"] == 2 and cov["missing_smogon"] == ["mimikyu"] and cov["missing_db"] == ["mimikyu"], cov
    assert set(rep["results_by_view"]) == {"pooled", "known"}
    md = ev.render_markdown(rep)
    for head in ("## 1. 評価範囲", "## 2. 対応比較", "## 3. カバー率", "## 4. 変換の検査", "## 5. 種ごとの観測",
                 "## 7. 次の手順"):
        assert head in md, head
    assert "ab" * 32 in md and "snapshot 75" in md and "Fakemon" in md
    json.dumps(rep, ensure_ascii=False)     # JSON にできる
    print("test_report_shape OK")


def main():
    test_hold_probability_vs_weight_share()
    test_mega_name_mapping_and_group()
    test_legality_check_counts_drops()
    test_spread_unit()
    test_exact_fetch_does_not_fall_back()
    test_extract_observations_dedup_and_order()
    test_likelihood_only_revealed_and_bootstrap()
    test_time_split()
    test_report_shape()
    print("ALL OK")


if __name__ == "__main__":
    main()
