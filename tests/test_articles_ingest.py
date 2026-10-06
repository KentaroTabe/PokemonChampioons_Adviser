"""記事バンクの前段 (tools/team_build/articles_ingest) の純粋関数テスト。

    python -m tests.test_articles_ingest
"""
from __future__ import annotations

from tools.team_build import articles_ingest as A

CSV = """﻿確度,第2ラウンド,ブログ/著者,プラットフォーム,ブログURL,根拠文言,根拠記事URL,記事タイトル,シーズン,留保事項,出典担当
A,,甲,note,https://note.com/a,最終42位,https://note.com/a/n/n1 https://note.com/a/n/n1?app_launch=false,【シーズンM-4 最終42位】即席カバルカイリュー,M-4,,x
A,,乙,note,https://note.com/b,最終80位,https://note.com/b/n/n2,【Pokémon champions】最終80位 メガスターミー軸【ダブルバトル】,M-1,,x
B,,丙,はてなブログ系,https://c.hatenablog.com/,最終470位 / 最終286位,https://c.hatenablog.com/entry/1 https://c.hatenablog.com/entry/2/,Sandstorm Village【M-3 最終470位】 / バンドリ【ダブル マンスリーチャレンジ2026.06/最終286位】,"M-3,マンスリーチャレンジ,MCS2026.06,MCS 2026.06",,x
要追確認,,丁,,https://d.example.com/,最終99位,https://d.example.com/p,,M-5,B（要追確認）,x
B,,戊,note,https://note.com/e,最終10位,https://note.com/b/n/n2,重複の URL,M-6,,x
"""


def test_parse_and_manifest():
    rows = A.parse_rows(CSV)
    assert len(rows) == 5 and rows[0]["confidence"] == "A" and rows[0]["author"] == "甲"
    assert A.split_urls(rows[0]["article_urls"]) == ["https://note.com/a/n/n1"]            # 追跡クエリを落として重複排除
    assert A.split_urls(rows[2]["article_urls"]) == ["https://c.hatenablog.com/entry/1", "https://c.hatenablog.com/entry/2"]
    assert A.normalize_url("HTTPS://Note.com/x/?a=1#f") == "https://note.com/x"
    assert A.classify_format(rows[1]["title"]) == "double" and A.classify_format(rows[0]["title"]) == "single" and A.classify_format("") == "unknown"
    assert A.classify_format("【ﾎﾟﾁｬﾋﾟﾀﾞﾌﾞﾙ/構築記事】指＋範囲技") == "double"            # 半角カナ (NFKC で当たる。10/6 の一覧更新で発見)
    assert A.classify_format("【シングルM-2最終43位】勝てるダブルエース構築の組み方") == "single"   # 「ダブルエース」はダブルバトルではない
    assert A.classify_format("【M-4ダブル】貰い火ウインディ採用") == "double"
    assert A.seasons_of(rows[2]["season"]) == ["M-3", "MCS2026.06"] and A.seasons_of("M-1,M-4") == ["M-1", "M-4"]
    assert A.seasons_of("マンスリーチャレンジ") == ["MCS"] and A.seasons_of("") == []
    assert A.rank_of("最終470位 / 最終286位") == 286 and A.rank_of("") is None
    assert A.regulation_of(["M-5", "M-6", "M-1"], {"M-5": "regmb", "M-6": "regmc"}) == {"M-5": "regmb", "M-6": "regmc", "M-1": "unknown"}
    man = A.build_manifest(rows)
    assert man["n_rows"] == 5 and man["n_selected_rows"] == 4 and man["n_urls"] == 4           # 要追確認を除き、重複 URL は 1 つ
    assert man["n_double"] == 2 and man["n_single"] == 2 and man["n_to_fetch"] == 2        # 丙の 2 記事は URL ごとに判定 (2 本目だけダブル)
    assert [e["format"] for e in man["entries"]] == ["single", "double", "single", "double"]
    assert man["entries"][2]["title"] == "Sandstorm Village【M-3 最終470位】"
    assert A.split_titles("A / B<br>C") == ["A", "B", "C"] and A.split_titles("") == []
    assert man["by_season"] == {"M-1": 1, "M-3": 2, "M-4": 1, "MCS2026.06": 2}
    assert man["rank_median"] == 286 and man["n_rank_le_100"] == 2          # 中央値は記事 URL ごと (丙の 2 記事は 286 位)
    urls = [e["url"] for e in man["entries"]]
    assert urls == ["https://note.com/a/n/n1", "https://note.com/b/n/n2", "https://c.hatenablog.com/entry/1", "https://c.hatenablog.com/entry/2"]
    assert man["entries"][1]["selected"] is False and man["entries"][0]["robots"] == "unchecked"
    man2 = A.build_manifest(rows, include_unconfirmed=True)
    assert man2["n_urls"] == 5 and man2["n_unknown_format"] == 1 and man2["by_confidence"]["要追確認"] == 1
    print("test_parse_and_manifest OK")


def main() -> None:
    test_parse_and_manifest()
    print("ALL OK")


if __name__ == "__main__":
    main()
