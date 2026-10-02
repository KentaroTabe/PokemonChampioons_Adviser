"""技の効果表の生成 (tools/build_move_data.py) のテスト。解析は純粋関数で小さな TS 断片を使い、実ファイルは煙試験。

    python -m tests.test_move_data
"""
from __future__ import annotations

from pathlib import Path

from tools import build_move_data as B

SNIPPET = """export const Moves: import('../sim/dex-moves').MoveDataTable = {
\tswordsdance: {
\t\tnum: 14,
\t\taccuracy: true,
\t\tbasePower: 0,
\t\tcategory: "Status",
\t\tname: "Swords Dance",
\t\tpp: 20,
\t\tpriority: 0,
\t\tflags: { snatch: 1, metronome: 1 },
\t\tboosts: {
\t\t\tatk: 2,
\t\t},
\t\tsecondary: null,
\t\ttarget: "self",
\t\ttype: "Normal",
\t},
\toverheat: {
\t\tnum: 315,
\t\taccuracy: 90,
\t\tbasePower: 130,
\t\tcategory: "Special",
\t\tname: "Overheat",
\t\tpp: 5,
\t\tpriority: 0,
\t\tflags: { protect: 1, mirror: 1, metronome: 1 },
\t\tself: {
\t\t\tboosts: {
\t\t\t\tspa: -2,
\t\t\t},
\t\t},
\t\tsecondary: null,
\t\ttarget: "normal",
\t\ttype: "Fire",
\t},
\ttripleaxel: {
\t\tnum: 813,
\t\taccuracy: 90,
\t\tbasePower: 20,
\t\tbasePowerCallback(pokemon, target, move) {
\t\t\treturn 20 * move.hit;
\t\t},
\t\tcategory: "Physical",
\t\tname: "Triple Axel",
\t\tpp: 10,
\t\tpriority: 0,
\t\tflags: { contact: 1, protect: 1, mirror: 1 },
\t\tmultihit: 3,
\t\tmultiaccuracy: true,
\t\tsecondary: null,
\t\ttarget: "normal",
\t\ttype: "Ice",
\t},
\tpoweruppunch: {
\t\tnum: 612,
\t\taccuracy: 100,
\t\tbasePower: 40,
\t\tcategory: "Physical",
\t\tname: "Power-Up Punch",
\t\tpp: 20,
\t\tpriority: 0,
\t\tflags: { contact: 1, protect: 1, mirror: 1, punch: 1, metronome: 1 },
\t\tsecondary: {
\t\t\tchance: 100,
\t\t\tself: {
\t\t\t\tboosts: {
\t\t\t\t\tatk: 1,
\t\t\t\t},
\t\t\t},
\t\t},
\t\ttarget: "normal",
\t\ttype: "Fighting",
\t},
\tblizzard: {
\t\tnum: 59,
\t\taccuracy: 70,
\t\tbasePower: 110,
\t\tcategory: "Special",
\t\tname: "Blizzard",
\t\tpp: 5,
\t\tpriority: 0,
\t\tflags: { protect: 1, mirror: 1, metronome: 1, wind: 1 },
\t\tonModifyMove(move) {
\t\t\tif (this.field.isWeather(['hail', 'snowscape'])) move.accuracy = true;
\t\t},
\t\tsecondary: {
\t\t\tchance: 10,
\t\t\tstatus: 'frz',
\t\t},
\t\ttarget: "allAdjacentFoes",
\t\ttype: "Ice",
\t},
};
"""
MOD_SNIPPET = """export const Moves: import('../../../sim/dex-moves').ModdedMoveDataTable = {
\toverheat: {
\t\tinherit: true,
\t\tbasePower: 120,
\t},
\tswordsdance: {
\t\tinherit: true,
\t\tisNonstandard: "Past",
\t},
};
"""


def test_parse_snippet():
    table = B.build_table(SNIPPET, MOD_SNIPPET, overrides={"tripleaxel": {"note": "人手の訂正"}})
    assert set(table) == {"swordsdance", "overheat", "tripleaxel", "poweruppunch", "blizzard"}
    sd = table["swordsdance"]
    assert sd["category"] == "Status" and sd["setup_boosts"] == {"atk": 2} and sd["target_boosts"] == {}
    assert sd["accuracy"] is None and sd["nonstandard"] == "Past"          # mod の上書き
    oh = table["overheat"]
    assert oh["self_boosts"] == {"spa": -2} and oh["power"] == 120 and oh["accuracy"] == 90   # mod で威力 120
    assert B.demerit_tags(oh) == ["selfdrop"]
    ta = table["tripleaxel"]
    assert ta["multihit"] == 3 and ta["multiaccuracy"] is True and ta["variable_power"] == "hit_count"
    assert ta["has_power_callback"] and ta["note"] == "人手の訂正"
    pp = table["poweruppunch"]
    assert pp["secondary"] == {"chance": 100, "status": None, "volatile": None, "target_boosts": {}, "self_boosts": {"atk": 1}}
    assert pp["self_boosts"] == {} and pp["setup_boosts"] == {}
    bz = table["blizzard"]
    assert bz["accuracy_weather"] == {"snow": True} and bz["secondary"]["status"] == "frz" and bz["secondary"]["chance"] == 10
    assert B.demerit_tags(bz) == ["lowacc"]
    # 連続技の期待回数と命中込みの期待威力
    assert B.expected_hits({"multihit": None}) == 1.0 and B.expected_hits({"multihit": 3}) == 3.0
    assert abs(B.expected_hits({"multihit": [2, 5]}) - 3.1) < 1e-9 and B.expected_hits({"multihit": [2, 5]}, skill_link=True) == 5.0
    # トリプルアクセル: 1 発ごとに命中 90% で、外したら止まる。20×0.9 + 40×0.81 + 60×0.729 = 94.14
    assert abs(B.expected_power(ta) - (20 * 0.9 + 40 * 0.81 + 60 * 0.729)) < 1e-9
    # 命中 1 回の連続技: 25 × 3.1 × 1.0、スキルリンクで 25 × 5
    seed = {"power": 25, "accuracy": 100, "multihit": [2, 5], "multiaccuracy": False}
    assert abs(B.expected_power(seed) - 25 * 3.1) < 1e-9 and abs(B.expected_power(seed, skill_link=True) - 125) < 1e-9
    assert abs(B.expected_power(oh) - 120 * 0.9) < 1e-9 and abs(B.expected_power(bz) - 110 * 0.7) < 1e-9
    assert abs(B.expected_power(bz, accuracy_mult=1.3) - 110 * 0.91) < 1e-9       # ふくがん等の命中補正 (上限 1)
    assert abs(B.expected_power(dict(bz, accuracy=None)) - 110) < 1e-9             # 必中
    # 優先表の下書き: 安定技 / それ以外 (階層はデメリットの印だけで決まる。可変威力は情報の印)
    pri = B.build_priority(table)
    fire = pri["Special"]["Fire"]
    assert [r["move"] for r in fire] == ["overheat"] and fire[0]["tier"] == "other" and fire[0]["tags"] == ["selfdrop"]
    ice = {r["move"]: r for r in pri["Physical"]["Ice"]}
    assert ice["tripleaxel"]["tier"] == "stable" and ice["tripleaxel"]["tags"] == [] and ice["tripleaxel"]["base"] == 94.1
    assert pri["Physical"]["Fighting"][0] == {"move": "poweruppunch", "tier": "stable", "base": 40.0, "power": 40,
                                             "accuracy": 100, "tags": []}
    assert pri["Special"]["Ice"][0]["tier"] == "other" and pri["Special"]["Ice"][0]["tags"] == ["lowacc"]   # ふぶき 命中 70
    print("test_parse_snippet OK")


def test_real_files():
    """実ファイル (Showdown 本体 + champions mod) の煙試験。無ければ飛ばす"""
    if not (B.MOVES_TS.exists() and B.MOD_TS.exists()):
        print("test_real_files SKIP (moves.ts なし)")
        return
    table = B.build_table(B.MOVES_TS.read_text(encoding="utf-8"), B.MOD_TS.read_text(encoding="utf-8"))
    t = table
    assert t["bulletseed"]["multihit"] == [2, 5] and not t["bulletseed"]["multiaccuracy"]
    assert t["tripleaxel"]["multiaccuracy"] and t["populationbomb"]["multihit"] == 10 and t["populationbomb"]["multiaccuracy"]
    assert t["surgingstrikes"]["multihit"] == 3 and t["surgingstrikes"]["will_crit"] and t["flowertrick"]["will_crit"]
    assert t["overheat"]["self_boosts"] == {"spa": -2} and t["closecombat"]["self_boosts"] == {"def": -1, "spd": -1}
    assert t["superpower"]["self_boosts"] == {"atk": -1, "def": -1}
    assert t["bravebird"]["recoil"] == 0.33 and t["headsmash"]["recoil"] == 0.5
    assert t["highjumpkick"]["crash"] and t["supercellslam"]["crash"]
    assert t["outrage"]["locked"] and t["solarbeam"]["charge"] and t["hyperbeam"]["recharge"]
    assert t["explosion"]["selfdestruct"] == "always" and t["mistyexplosion"]["selfdestruct"] == "always"
    assert t["steelroller"]["condition"] == "terrain_required" and t["belch"]["condition"] == "berry_eaten"
    assert t["dreameater"]["condition"] == "target_asleep" and t["suckerpunch"]["condition"] == "opponent_attacking"
    assert t["auroraveil"]["condition"] == "snow_required"
    assert t["hurricane"]["accuracy_weather"] == {"rain": True, "sun": 50} and t["thunder"]["accuracy_weather"] == {"rain": True, "sun": 50}
    assert t["blizzard"]["accuracy_weather"] == {"snow": True}
    assert t["expandingforce"]["field_power"] == {"terrain": ["psychic"], "mult": 1.5, "grounded": True}
    assert t["weatherball"]["field_type"] == {"sun": "Fire", "rain": "Water", "sand": "Rock", "snow": "Ice"}
    assert t["weatherball"]["field_power"].get("weather_double") is True
    assert t["lowkick"]["variable_power"] == "target_weight" and t["gyroball"]["variable_power"] == "speed_ratio_slower"
    assert t["hex"]["variable_power"] == "target_status" and t["knockoff"]["variable_power"] == "target_item"
    assert t["lastrespects"]["variable_power"] == "fainted_allies" and t["risingvoltage"]["variable_power"] == "terrain"
    assert t["bodypress"]["override_offensive_stat"] == "def" and t["foulplay"]["override_offensive_pokemon"] == "target"
    assert t["psyshock"]["override_defensive_stat"] == "def"
    assert t["swordsdance"]["setup_boosts"] == {"atk": 2} and t["quiverdance"]["setup_boosts"] == {"spa": 1, "spd": 1, "spe": 1}
    assert t["shellsmash"]["setup_boosts"] == {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1}
    assert t["scald"]["secondary"]["status"] == "brn" and t["scald"]["secondary"]["chance"] == 30
    assert t["anchorshot"]["power"] == 90 and t["absorb"]["nonstandard"] == "Past"       # champions mod の上書き
    assert t["uturn"]["self_switch"] and t["roar"]["force_switch"] and t["seismictoss"]["fixed_damage"] == "'level'"
    # 表に無い callback / onTry が増えていないか (増えたら表 VARIABLE_POWER_KIND / CONDITION_KIND を育てる)
    unknown_cb = sorted(m for m, e in t.items() if e["has_power_callback"] and m not in B.VARIABLE_POWER_KIND)
    unknown_try = sorted(m for m, e in t.items() if e["has_on_try"] and m not in B.CONDITION_KIND)
    assert not unknown_cb, unknown_cb
    assert not unknown_try, unknown_try
    pri = B.build_priority(t, learnable={"flamethrower", "overheat", "fireblast", "fierydance", "earthquake", "bulletseed",
                                         "scaleshot", "glaiverush", "knockoff", "populationbomb", "stoneedge"})
    fire = {r["move"]: r for r in pri["Special"]["Fire"]}
    assert fire["flamethrower"]["tier"] == "stable" and fire["fierydance"]["tier"] == "stable"
    assert fire["overheat"]["tier"] == "other" and fire["overheat"]["tags"] == ["selfdrop"]
    assert fire["fireblast"]["tier"] == "other" and fire["fireblast"]["tags"] == ["lowacc"]     # 命中 85 は安定技でない (90 未満)
    assert abs(pri["Physical"]["Grass"][0]["base"] - 25 * 3.1) < 1e-6                           # タネマシンガン 25 × 3.1 回
    dragon = {r["move"]: r for r in pri["Physical"]["Dragon"]}
    assert dragon["scaleshot"]["tags"] == ["selfdrop"]            # 追加効果 100% で防御 −1 (secondary.self)
    assert dragon["glaiverush"]["tags"] == ["selfvolatile"]       # 次のターン被ダメ 2 倍
    dark = {r["move"]: r for r in pri["Physical"]["Dark"]}
    assert dark["knockoff"]["tier"] == "stable" and dark["knockoff"]["tags"] == ["variable"]   # 可変威力は階層を変えない
    normal = {r["move"]: r for r in pri["Physical"]["Normal"]}
    assert abs(normal["populationbomb"]["base"] - sum(20 * 0.9 ** k for k in range(1, 11))) < 0.06   # 1 発ごとに命中 90%
    rock = {r["move"]: r for r in pri["Physical"]["Rock"]}
    assert rock["stoneedge"]["tier"] == "other" and rock["stoneedge"]["tags"] == ["lowacc"]
    print("test_real_files OK")


def test_generated_file_matches_source():
    """生成物 (advisor/data/move_effects.json) が今の Showdown データと一致する (更新忘れの検出)"""
    if not (B.OUT_PATH.exists() and B.MOVES_TS.exists()):
        print("test_generated_file_matches_source SKIP")
        return
    import json
    doc = json.loads(B.OUT_PATH.read_text(encoding="utf-8"))
    overrides = json.loads(B.OVERRIDES_PATH.read_text(encoding="utf-8")) if B.OVERRIDES_PATH.exists() else {}
    fresh = B.build_table(B.MOVES_TS.read_text(encoding="utf-8"), B.MOD_TS.read_text(encoding="utf-8"), overrides)
    diff = [m for m in sorted(set(doc["moves"]) | set(fresh)) if doc["moves"].get(m) != fresh.get(m)]
    assert not diff, f"move_effects.json が古い (scripts/build_move_data.sh で再生成): {diff[:10]}"
    print("test_generated_file_matches_source OK")


if __name__ == "__main__":
    test_parse_snippet()
    test_real_files()
    test_generated_file_matches_source()
