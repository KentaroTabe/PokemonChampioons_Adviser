"""OCRテキスト解析 (parse_fraction / parse_percent) の回帰テスト。

実運用で観測した誤読ケースを固定化する:
- 「167」(スラッシュ+最大値の取りこぼし) -> 1/7 に分割される事故
- 「715」(スラッシュが1に誤読) -> 7/5 と解釈される事故
- 選出画面の「0/3」進捗の混入 (parse自体は通り、extractor側の>=50で弾く)

使い方: python -m tests.test_ocr_parse
"""
from vision.ocr import parse_fraction, parse_percent


def test_parse_fraction():
    cases = [
        ("197/197", (197, 197)),
        ("64/132", (64, 132)),
        ("155/175", (155, 175)),
        ("167", None),        # 現在値のみ -> 分割しない
        ("197", None),
        ("17", None),
        ("715", None),        # 1をスラッシュとみなすのは両側2桁以上のみ
        ("0/3", (0, 3)),      # parseは通る (my HP側は >=50 ガードで弾く)
        ("1971197", (197, 197)),   # スラッシュが1に誤読 (両側3桁)
        ("", None),
    ]
    for text, want in cases:
        got = parse_fraction(text)
        assert got == want, f"parse_fraction({text!r}) = {got}, 期待 {want}"
    print("test_parse_fraction OK")


def test_parse_percent():
    cases = [("79%", 79), ("100", 100), ("0", 0), ("", None)]
    for text, want in cases:
        got = parse_percent(text)
        assert got == want, f"parse_percent({text!r}) = {got}, 期待 {want}"
    print("test_parse_percent OK")


if __name__ == "__main__":
    test_parse_fraction()
    test_parse_percent()
    print("ALL OK")
