"""パーティ診断 (advisor/team_advice) の検証。

使い方: python -m tests.test_team_advice
"""
from vision.normalize import NameResolver

from advisor.team_advice import format_team_advice, team_advice


def test_team_advice():
    r = NameResolver()
    a = team_advice(r, top_n=10, n_suggest=3)
    assert a is not None, "my_team.jsonが読めていない"
    assert len(a["matchups"]) >= 1
    assert all(0 <= m["wins"] <= m["total"] for m in a["matchups"])
    text = format_team_advice(a)
    assert "構築診断" in text and "マッチアップ" in text
    print(text)
    print("test_team_advice OK")


def test_format_empty():
    assert "未登録" in format_team_advice(None)
    print("test_format_empty OK")


if __name__ == "__main__":
    test_team_advice()
    test_format_empty()
    print("ALL OK")
