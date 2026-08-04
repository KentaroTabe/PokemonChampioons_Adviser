"""アドバイスの補助メッセージ (画面を開く促し) のテスト。

    python -m tests.test_advice_prompts

技が未読取で交代しか評価できていないとき、末尾の📱提案だけでは
気づかれない (2026-08-05接続テストのフィードバック)。推奨行の直後に
目立つ警告を出す。
"""
from __future__ import annotations

from advisor.service import Advisor


def _svc():
    return Advisor.__new__(Advisor)   # resolver不要の整形だけ試す


def test_switch_only_advice_warns_prominently():
    advice = {
        "ok": True,
        "best": {"kind": "switch", "name": "ペリッパー", "score": 12.0},
        "actions": [
            {"kind": "switch", "name": "ペリッパー", "score": 12.0,
             "reason": "被ダメ小"},
            {"kind": "switch", "name": "ラグラージ", "score": 8.0,
             "reason": "受け出し"},
        ],
        "suggestion": "技選択画面を一度開いてください (技とPPを読み取ります)",
    }
    text = _svc().format_advice(advice)
    lines = text.split("\n")
    assert lines[0].startswith("◎"), lines[0]
    assert "技が未読取" in lines[1], lines[1]
    assert "技選択画面" in lines[1]
    # 同内容の📱提案は重複させない
    assert sum("技選択画面" in l for l in lines) == 1, text
    print("test_switch_only_advice_warns_prominently OK")


def test_move_advice_has_no_warning():
    advice = {
        "ok": True,
        "best": {"kind": "move", "name": "なみのり", "score": 80.0},
        "actions": [
            {"kind": "move", "name": "なみのり", "score": 80.0, "reason": "抜群"},
            {"kind": "switch", "name": "ラグラージ", "score": 8.0,
             "reason": "受け出し"},
        ],
        "suggestion": "「様子を見る」を開くと相手パーティのHP%を取得できます",
    }
    text = _svc().format_advice(advice)
    assert "技が未読取" not in text
    assert "様子を見る" in text   # 別種の提案はこれまで通り末尾に出る
    print("test_move_advice_has_no_warning OK")


if __name__ == "__main__":
    test_switch_only_advice_warns_prominently()
    test_move_advice_has_no_warning()
    print("\nALL OK")
