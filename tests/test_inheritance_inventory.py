"""引き継ぎ評価 I-00 資産台帳 (tools/inheritance_eval/inventory.py) の純粋な分類のテスト (合成データだけ。本体の資産は読まない)。

    python -m tests.test_inheritance_inventory

- 対戦ログの要約・完了 (勝敗の観測 / 訂正 / 推定 / 手入力 / 不明 / 行なし)・欠損・用途・総合の分類、選出画面の 6 体と version 行の照合
- 行と資産の区分 (観測事実 / 推定値 / 生成物 / 実対戦結果 / 合成結果)、知らない資産の種類は止める
- 画像の名前の解釈と対戦ログの期間への対応、登録の差分と sha、bak の期間の推定
- 構築 run の状態と有効な測定、記事のホストの方針 (接尾辞の照合・allowed_urls)、モデルの拡張子・置き場・付属・実行試験の条件
- 重複の集計、KNOWN_ISSUES の件数、使用率 snapshot からの規制の推定、measurements の行の検査、復元に使うログの選び方
"""
from __future__ import annotations

import time

from tools.inheritance_eval import inventory as INV

GAP = 8.0


def _ver(t, snap=None, registry=False, species=("a", "b", "c", "d", "e", "f")):
    team = {"sha256": "t" * 16, "species": list(species)}
    if registry:
        team.update(registry={"x": {}}, registry_sha256="r" * 16)
    return {"type": "version", "t": t, "version_id": "v1", "rules": {"git_commit": "c" * 40, "usage_snapshot": snap,
                                                                        "selection_features": "v1"},
            "team": team, "selection_model": {"sha256": "s" * 16}, "action_policy": {"sha256": "p" * 16}}


def _party(names):
    return {"party": [{"ja": n} for n in names]}


def _sel_scene(t, me=("ア", "イ", "ウ", "エ", "オ", "カ"), opp=("1", "2", "3", "4", "5", "6")):
    return {"type": "scene", "t": t, "scene": "selection", "state": {"player": _party(me), "opponent": _party(opp)}}


def _cmd_scene(t):
    return {"type": "scene", "t": t, "scene": "command", "state": {"player": _party(["ア"]), "opponent": _party(["1"])}}


def _advice(t, schema=None, turn=3, state=True):
    row = {"type": "advice", "t": t, "kind": "battle", "advice": {"best": {"kind": "move", "id": "tackle"}}}
    if state:
        st = {"turn": turn, "player": _party(["ア"]), "opponent": _party(["1"])}
        if schema:
            st["state_schema"] = schema
        row.update(state=st, turn=turn, advice_id="x")
    return row


def _full_log(outcomes, schema=2, with_version=True):
    recs = []
    if with_version:
        recs.append(_ver(100.0, snap=75))
    recs += [_sel_scene(101.0), _cmd_scene(110.0), _cmd_scene(112.0), _cmd_scene(125.0), _advice(126.0, schema),
             {"type": "events", "t": 127.0, "fired": [], "scene": "battle_hud"}]
    recs += outcomes
    return recs


def test_log_summary_and_completion():
    s = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "win"}]), GAP)
    assert s["t_first"] == 100.0 and s["t_last"] == 200.0
    assert s["advice"]["schema"] == {"2": 1} and s["advice"]["with_state"] == 1 and s["advice"]["no_state"] == 0
    # 決定点の間引き: 110 と 112 は 8 秒未満なので 1 つ、125 で 2 つ目
    assert s["n_scene_decisions"] == 2
    assert s["opp_named_max"] == 6 and s["my_roster_ja"] == ["ア", "イ", "ウ", "エ", "オ", "カ"]
    assert INV.log_completion(s) == "win_loss_observed"
    assert INV.log_class(s, "win_loss_observed") == "完了 (勝敗確定)"
    # 訂正行で勝敗が決まった (推定の負け → 訂正の勝ち)
    s2 = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "loss", "inferred": True},
                                    {"type": "outcome", "t": 210.0, "outcome": "win", "corrected_from": "loss"}]), GAP)
    assert INV.log_completion(s2) == "win_loss_corrected"
    s3 = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "win", "inferred": True}]), GAP)
    assert INV.log_completion(s3) == "win_loss_inferred" and INV.log_class(s3, "win_loss_inferred") == "完了 (勝敗推定)"
    s4 = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "win", "manual": True}]), GAP)
    assert INV.log_completion(s4) == "win_loss_manual"
    s5 = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "unknown"}]), GAP)
    assert INV.log_completion(s5) == "unknown" and INV.log_class(s5, "unknown") == "終了 (勝敗不明)"
    # 勝敗不明の後に勝ちの訂正
    s6 = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "unknown"},
                                    {"type": "outcome", "t": 210.0, "outcome": "win", "revised_from": "unknown"}]), GAP)
    assert INV.log_completion(s6) == "win_loss_corrected"
    s7 = INV.log_summary(_full_log([]), GAP)
    assert INV.log_completion(s7) == "no_outcome" and INV.log_class(s7, "no_outcome") == "未完了 (勝敗の行なし)"
    # 対戦外: 決定画面も対戦の助言も無い
    s8 = INV.log_summary([_sel_scene(1.0), {"type": "outcome", "t": 2.0, "outcome": "win"}], GAP)
    assert INV.log_class(s8, INV.log_completion(s8)) == "対戦外 (決定画面・対戦の助言なし)"


def test_log_missing_and_uses():
    # 版 1 の古いログ: version 行なし、state の無い advice、turn なし
    old = [_sel_scene(1.0, opp=("1", "2", "3")), _cmd_scene(5.0), _advice(6.0, state=False),
           {"type": "outcome", "t": 9.0, "outcome": "loss"}]
    s = INV.log_summary(old, GAP)
    miss = INV.log_missing(s)
    assert "version 行なし (動いていた版が分からない)" in miss
    assert "state の無い advice 行あり" in miss and "turn の無い advice 行あり" in miss
    assert "events 行なし" in miss and "規制 (regulation / format) の記録なし" in miss
    u = INV.log_uses(s, INV.log_completion(s))
    assert u == {"正解": True, "失敗再現": "scene_only", "候補セット": False, "比較基準": "版不明"}
    # 新しいログ: version 行に snapshot あり・registry なし、版 2 の state
    s2 = INV.log_summary(_full_log([{"type": "outcome", "t": 200.0, "outcome": "win", "inferred": True}]), GAP)
    miss2 = INV.log_missing(s2)
    assert "version 行なし (動いていた版が分からない)" not in miss2 and "使用率 snapshot の記録なし" not in miss2
    assert "登録 (team.registry) の記録なし" in miss2 and "events 行なし" not in miss2
    u2 = INV.log_uses(s2, INV.log_completion(s2))
    # 推定の勝敗は正解にしない。比較基準は勝敗 (推定を含む) + 版
    assert u2 == {"正解": False, "失敗再現": "advice_state", "候補セット": True, "比較基準": "版あり"}
    # 版 1 の state (state_schema なし) は版 1 と数える
    s3 = INV.log_summary(_full_log([], schema=None), GAP)
    assert s3["advice"]["schema"] == {"1": 1}
    # registry の記録があれば欠損に出ない
    s4 = INV.log_summary([_ver(1.0, snap=3, registry=True)], GAP)
    assert "登録 (team.registry) の記録なし" not in INV.log_missing(s4)


def test_roster_vs_version():
    assert INV.roster_vs_version(["a", "b", "c", "d", "e", "f"], ["f", "e", "d", "c", "b", "a"]) is True
    assert INV.roster_vs_version(["a", "b", "c", "d", "e", "f"], ["a", "b", "c", "d", "e", "g"]) is False
    assert INV.roster_vs_version(None, ["a"] * 6) is None
    assert INV.roster_vs_version(["a"] * 6, ["a", None, "b", "c", "d", "e"]) is None
    assert INV.roster_vs_version(["a"] * 6, ["a"] * 5) is None


def test_categories():
    cats = set(INV.INH_ASSET_CATEGORIES)
    assert INV.row_category({"type": "advice"}) == "生成物"           # AI の推奨は正解にしない
    assert INV.row_category({"type": "scene"}) == "推定値"            # 認識した状態は推定値
    assert INV.row_category({"type": "outcome", "outcome": "win"}) == "実対戦結果"
    assert INV.row_category({"type": "outcome", "outcome": "win", "inferred": True}) == "推定値"
    assert INV.row_category({"type": "version"}) == "観測事実"
    assert INV.row_category({"type": "未知の行"}) == "推定値"          # 知らない行を事実扱いしない
    assert INV.asset_categories("build_run")[0] == "合成結果"
    assert "実対戦結果" in INV.asset_categories("battle_log")
    for k in INV.ASSET_CATEGORIES:
        assert set(INV.asset_categories(k)) <= cats
    try:
        INV.asset_categories("unknown_kind")
        raise AssertionError("知らない資産の種類で止まらない")
    except ValueError:
        pass


def test_frames():
    assert INV.frame_info("frame_1792453815.png") == {"prefix": "frame_", "t": 1792453815.0, "ext": "png"}
    assert INV.frame_info("sel_1792453797.png")["prefix"] == "sel_"
    assert INV.frame_info("fc_1792454239.png")["prefix"] == "fc_"
    b = INV.frame_info("burst/burst_1792417673/1792417673106.jpg")
    assert b["prefix"] == "burst" and abs(b["t"] - 1792417673.106) < 1e-6
    assert INV.frame_info("notes.txt")["prefix"] == "other"
    spans = [("a.jsonl", 100.0, 200.0), ("b.jsonl", 250.0, 300.0), ("c.jsonl", None, None)]
    m = INV.match_frames([90.0, 150.0, 230.0, 260.0, 999.0, None], spans, slack=20.0)
    # 230 は a (100..200+20=220) の外、b (250-20=230..) の端に入る。90 は a の前の余裕の中
    assert m["by_log"] == {"a.jsonl": 2, "b.jsonl": 2} and m["n_unmatched"] == 2
    # 2 つの期間に入るときは中心に近いログ
    m2 = INV.match_frames([225.0], [("a", 100.0, 200.0), ("b", 240.0, 300.0)], slack=30.0)
    assert m2["by_log"] == {"b": 1}


def test_team():
    prev = {"ア": {"技": ["1"]}, "イ": {"技": ["2"]}}
    cur = {"イ": {"技": ["3"]}, "ウ": {}}
    assert INV.team_diff(prev, cur) == {"added": ["ウ"], "removed": ["ア"], "changed": ["イ"]}
    assert INV.team_diff(None, cur)["added"] == ["イ", "ウ"]
    from advisor.my_team import registry_sha
    reg = {"ア": {"技": ["a", "b"], "持ち物": "x"}, "イ": {"性格": "ようき"}}
    assert INV.registry_sha16(reg) == registry_sha(reg)
    assert INV.registry_sha16(dict(reversed(list(reg.items())))) == registry_sha(reg)   # キーの順に依らない
    assert INV.registry_sha16([1]) is None
    t_from, t_to = INV.bak_interval("my_team.json.bak.20261005", 1000.0)
    assert t_from == 1000.0 and t_to == time.mktime(time.strptime("20261006", "%Y%m%d"))
    assert INV.bak_interval("my_team.json", 5.0) == (5.0, None)
    cov = INV.team_coverage(["x", "y", "z"], {"ア": {"技": ["1", "2", "3", "4"]}, "イ": {"技": ["1"]}},
                            lambda ja, e: {"ア": "x", "イ": "y"}.get(ja))
    assert cov == {"n": 3, "registered": 2, "with_moves": 1, "missing": ["z"]}


def test_runs():
    assert INV.run_status(["manifest.json", "final"], ["manifest.json", "evaluation.json", "team.json"]) == "完了 (final あり)"
    assert INV.run_status(["manifest.json", "evaluation", "advisors", "s06_sets.json"], []) == "測定の途中で停止 (final なし)"
    assert INV.run_status(["manifest.json", "s06_sets.json"], []) == "探索のみ (S6 まで。測定なし)"
    assert INV.run_status(["manifest.json", "s02_env_match.json"], []) == "探索の途中で停止"
    ev = {"s12_holdout": {"verdict": "PASS", "delta": 0.1, "ci": [0.01, 0.2], "n": 600}}
    v = INV.run_valid_measure("完了 (final あり)", ev)
    assert v["valid"] and v["verdict"] == "PASS" and "合成結果" in v["source"]
    assert not INV.run_valid_measure("測定の途中で停止 (final なし)", ev)["valid"]
    assert not INV.run_valid_measure("完了 (final あり)", {"s12_holdout": {"verdict": "PASS"}})["valid"]   # n・区間なし
    c = INV.run_condition({"regulation": "gen9x", "opponent_split": {"sealed_id": "abc"}, "git_commit": "0123456789abcdef"})
    assert c["regulation"] == "gen9x" and c["sealed_id"] == "abc" and c["git_commit"] == "0123456789ab"


def test_articles():
    pol = {"_schema": "host_policy/2", "gamewith.jp": {"fetch": "allow", "allowed_urls": ["https://gamewith.jp/p/1"]},
           "hatenablog.com": {"match": "suffix", "fetch": "allow", "allowed_from": "manifest"},
           "blog.hatenablog.com": {"match": "suffix", "fetch": "deny"},
           "yakkun.com": {"fetch": "deny"}, "note.com": {"fetch": "unknown"}}
    assert INV.host_of("https://Foo.Hatenablog.com/entry/1") == "foo.hatenablog.com"
    assert INV.host_of("") is None
    assert INV.policy_of("foo.hatenablog.com", pol)[0] == "hatenablog.com"
    assert INV.policy_of("x.blog.hatenablog.com", pol)[0] == "blog.hatenablog.com"      # 最長一致
    assert INV.policy_of("hatenablog.com.evil.jp", pol) == (None, None)                  # 接尾辞は . で区切る
    assert INV.policy_of("sub.yakkun.com", pol) == (None, None)                          # 完全一致の項目は接尾辞にしない
    assert INV.fetch_allowed("https://gamewith.jp/p/1/", pol)[0] is True
    assert INV.fetch_allowed("https://gamewith.jp/p/2", pol) == (False, "allowed_urls の外")
    assert INV.fetch_allowed("https://a.hatenablog.com/e", pol) == (True, "allow (manifest の selected だけ)")
    assert INV.fetch_allowed("https://note.com/x", pol) == (False, "fetch=unknown")
    assert INV.fetch_allowed("https://unknown.example/x", pol) == (False, "方針なし")


def test_models():
    assert INV.model_ext_of("battle_policy_balance.zip") == ".zip"
    assert INV.model_ext_of("battle_policy_balance.zip.prev") == ".zip"
    assert INV.model_ext_of("selection_model_n5000.pt") == ".pt"
    assert INV.model_ext_of("selection_model_emb.json") is None
    assert INV.model_ext_of("report.zipcode.txt") is None
    assert INV.model_group_of("champions_agent/train/checkpoints/selection_model.pt") == "checkpoints (配備・退避名)"
    assert INV.model_group_of("champions_agent/train/checkpoints/pool/a.zip") == "checkpoints/pool"
    assert INV.model_group_of("logs/pinned_models/20261016_012401/anchors/x.zip") == "pinned_models (測定のピンの写し)"
    assert INV.model_group_of("logs/build_search/runs/r1/advisors/L00/selection_model.pt") == "build_search/runs/*/advisors"
    assert INV.model_group_of("logs/registry/selection_model/selection_model-ab/x.pt") == "registry/selection_model"
    assert INV.excluded_dir(".venv312") and INV.excluded_dir(".venv") and INV.excluded_dir("node_modules")
    assert not INV.excluded_dir("logs")
    assert INV.selection_companions(["selection_model.pt", "selection_model_emb.json"]) == ["selection_model_meta.json"]
    assert INV.selection_companions(["selection_model_emb.json", "selection_model_meta.json"]) == []
    r = INV.model_readiness("selection", "s" * 16, ["a.pt"], [], ["v1"], ["e1"], "e1", ["gen9x"])
    assert r == {"ready": True, "lacks": []}
    r2 = INV.model_readiness("selection", "s" * 16, ["a.pt"], [], ["v1"], ["e1"], "e2", [])
    assert not r2["ready"] and any("埋め込み" in x for x in r2["lacks"]) and "規制の記録なし" in r2["lacks"]
    r3 = INV.model_readiness("action", "p" * 16, [], None, [], [], None, [])
    assert r3["lacks"] == ["ファイルが手元に無い", "特徴量 (観測) の版の記録なし", "規制の記録なし"]
    assert INV.model_presence("aa", [{"rel": "x", "sha16": "aa"}, {"rel": "y", "sha16": "bb"}, {"rel": "z", "sha16": "aa"}]) == ["x", "z"]
    assert INV.model_presence(None, [{"rel": "x", "sha16": None}]) == []


def test_dup_and_known_issues():
    d = INV.dup_stats([("a", 10), ("b", 20), ("a", 10), (None, 99), ("a", 10)])
    assert d == {"n": 4, "n_unique": 2, "n_dup_files": 2, "dup_rate": 0.5, "bytes": 50, "dup_bytes": 20, "dup_bytes_rate": 0.4}
    assert INV.dup_stats([])["dup_rate"] is None
    text = "# 台帳\n## A. 認識\n### A1. 族\n| 情報 | 対応 | 状態 |\n|---|---|---|\n| x | y | ✅ (第9回) |\n| z | w | 🟡 |\n" \
           "## B. 型\n| a | b | 👁 要観察 |\n| 説明だけ | - | - |\n"
    k = INV.count_known_issues(text)
    assert k == {"sections": 2, "families": 1, "rows_by_state": {"✅": 1, "🟡": 1, "👁": 1}, "rows_with_state": 3}


def test_season_estimate():
    assert INV.parse_db_time("2026-10-29 21:31:52") == 1793309512.0
    assert INV.parse_db_time(None) is None
    snaps = [{"id": 1, "format": "champions-singles", "source_month": "M1", "fetched_epoch": 100.0},
             {"id": 2, "format": "gen9ou", "source_month": None, "fetched_epoch": 150.0},
             {"id": 3, "format": "champions-singles", "source_month": "M2", "fetched_epoch": 200.0}]
    e = INV.season_estimate(160.0, snaps)
    assert e["season"] == "M1" and e["snapshot_id"] == 1 and "推定値" in e["basis"]   # smogon の形式は使わない
    assert INV.season_estimate(250.0, snaps)["season"] == "M2"
    assert INV.season_estimate(50.0, snaps) is None and INV.season_estimate(None, snaps) is None


def test_measurement_and_pick_logs():
    row = INV.measurement("c1", "asset", "観測事実", {"a": 1}, "期待", "出典", "pass", "理由", 0.12345)
    assert row["test_id"] == "I-00" and row["split"] == "diag" and row["elapsed_sec"] == 0.123
    for bad in (("pending", "観測事実"), ("pass", "AI の推奨")):
        try:
            INV.measurement("c", "a", bad[1], {}, "", "", bad[0], "", 0.0)
            raise AssertionError("不正な result / category を通した")
        except ValueError:
            pass
    sums = {"battle_20261005_1.jsonl": {"advice": {"schema": {"1": 3}}},
            "battle_20261019_1.jsonl": {"advice": {"schema": {"1": 2}}},
            "battle_20261020_1.jsonl": {"advice": {"schema": {"1": 1, "2": 4}}},
            "battle_20261023_1.jsonl": {"advice": {"schema": {"2": 4}}},
            "battle_20260901_1.jsonl": {"advice": {"schema": {}}}}
    assert INV.pick_logs(sums) == {"v1": "battle_20261019_1.jsonl", "v2": "battle_20261020_1.jsonl",
                                   "latest": "battle_20261023_1.jsonl"}


def main() -> None:
    test_log_summary_and_completion()
    test_log_missing_and_uses()
    test_roster_vs_version()
    test_categories()
    test_frames()
    test_team()
    test_runs()
    test_articles()
    test_models()
    test_dup_and_known_issues()
    test_season_estimate()
    test_measurement_and_pick_logs()
    print("ALL OK")


if __name__ == "__main__":
    main()
