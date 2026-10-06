"""WIN / LOSE の画面からの勝敗の読み取り (vision/win_lose) と、終了の扱いへの組み込みのテスト。

2026-10-06 第18回接続テスト: 勝負の文言を 15 戦中 6 戦で読めず、3 戦が誤り / 不明になった。文言のあと、ランク画面の前に
左右に WIN / LOSE が大きく出る画面が数秒あり (自分は左)、色の割合だけで判定できる。実フレーム (手元だけ) の値は
champions_agent/config の WIN_LOSE_* のコメントに記録してある。ここでは合成画像で判定の境界と組み込みを固定する。

    scripts/run_test.sh test_win_lose_screen
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

from champions_agent.config import WIN_LOSE_PLATE_MIN, WIN_LOSE_TEXT_YELLOW_MAX_OTHER, WIN_LOSE_TEXT_YELLOW_MIN
from vision import zones
from vision.win_lose import features, judge, read_win_lose

YELLOW = (0, 255, 255)      # BGR: WIN の文字の色 (HSV の H = 30)
BLUE = (255, 40, 20)        # 自分の名前の帯
MAGENTA = (200, 0, 255)     # 相手の名前の帯
GRAY = (40, 40, 40)


def _fill(img, zone, color, frac=1.0):
    """ゾーンの左から frac の幅を color で塗る"""
    h, w = img.shape[:2]
    y0, y1 = int(zone["y0"] * h), int(zone["y1"] * h)
    x0, x1 = int(zone["x0"] * w), int(zone["x1"] * w)
    img[y0:y1, x0:x0 + int((x1 - x0) * frac)] = color


def _screen(win_side=None, plates=True, size=(540, 960)):
    """合成の WIN / LOSE 画面。win_side: "left" / "right" / "both" / None (文字なし)"""
    img = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    img[:] = GRAY
    z = zones.WIN_LOSE
    if plates:
        _fill(img, z["left_plate"], BLUE)
        _fill(img, z["right_plate"], MAGENTA)
    if win_side in ("left", "both"):
        _fill(img, z["left_text"], YELLOW, 0.5)
    if win_side in ("right", "both"):
        _fill(img, z["right_text"], YELLOW, 0.5)
    return img


def test_read_from_synthetic_screens():
    assert read_win_lose(_screen("left")) == "win"
    assert read_win_lose(_screen("right")) == "loss"
    # 名前の帯が無い (場面の黄色いポケモン等) / 文字がまだ出ていない (対戦開始の演出も同じ見た目) / 両側が黄色 → 読まない
    assert read_win_lose(_screen("left", plates=False)) is None
    assert read_win_lose(_screen(None)) is None
    assert read_win_lose(_screen("both")) is None
    assert read_win_lose(None) is None
    f = features(_screen("left"))
    assert f["left_plate"] > 0.99 and f["right_plate"] > 0.99 and 0.45 < f["left_yellow"] < 0.55 and f["right_yellow"] == 0.0, f
    print("test_read_from_synthetic_screens OK")


def test_judge_thresholds():
    base = {"left_plate": 0.9, "right_plate": 0.9, "left_yellow": 0.0, "right_yellow": 0.0}
    assert judge(dict(base, left_yellow=WIN_LOSE_TEXT_YELLOW_MIN)) == "win"
    assert judge(dict(base, right_yellow=WIN_LOSE_TEXT_YELLOW_MIN)) == "loss"
    assert judge(dict(base, left_yellow=WIN_LOSE_TEXT_YELLOW_MIN - 0.01)) is None
    # 反対側にも黄色が多い (上限超) なら読まない
    assert judge(dict(base, left_yellow=0.45, right_yellow=WIN_LOSE_TEXT_YELLOW_MAX_OTHER + 0.01)) is None
    assert judge(dict(base, left_yellow=0.45, right_yellow=WIN_LOSE_TEXT_YELLOW_MAX_OTHER)) == "win"
    # 帯はどちらか片方でも足りなければ読まない
    assert judge(dict(base, left_yellow=0.45, left_plate=WIN_LOSE_PLATE_MIN - 0.01)) is None
    assert judge(dict(base, left_yellow=0.45, right_plate=0.0)) is None
    assert judge({}) is None
    print("test_judge_thresholds OK")


def test_parser_sets_outcome_once_per_battle():
    from vision.events import EventParser
    from vision.normalize import NameResolver
    from vision.state import BattleStateV2
    st = BattleStateV2()
    st.battle_active = True
    p = EventParser(st, NameResolver())
    assert p.end_by_win_lose_screen(None) is None and st.outcome is None
    assert p.end_by_win_lose_screen("win") == "battle_win_screen"
    assert st.outcome == "win" and st.battle_ended and st.win_lose_screen == "win"
    assert st.battle_active                      # ランク画面までは対戦中の扱い (勝負の文言と同じ)
    assert p.end_by_win_lose_screen("win") is None and p.end_by_win_lose_screen("loss") is None   # 1 対戦 1 回
    assert any(e.get("event") == "battle_win_screen" for e in st.events)
    # 次の対戦ではまた読める
    st.reset_battle()
    st.battle_active = True
    assert st.win_lose_screen is None and p.end_by_win_lose_screen("loss") == "battle_lose_screen" and st.outcome == "loss"
    # 勝負の文言と食い違ったら画面を採り、記録に残す
    st.reset_battle()
    st.outcome = "loss"
    assert p.end_by_win_lose_screen("win") == "battle_win_screen" and st.outcome == "win"
    assert any("食い違う" in e.get("text", "") for e in st.events)
    print("test_parser_sets_outcome_once_per_battle OK")


def test_logger_notice_and_readers_treat_screen_like_text():
    from battle_logger import BattleLogger
    from tools.battle_outcome import outcome_from_records, text_outcome_basis, text_outcome_of
    from vision.end_notice import battle_end_notice
    assert text_outcome_of(["faint", "battle_lose_screen"]) == "loss" and text_outcome_of(["battle_win_screen"]) == "win"
    assert text_outcome_basis(["battle_win_screen"]) == "win_lose_screen" and text_outcome_basis(["battle_win"]) == "battle_text"
    assert battle_end_notice({"outcome": "win"}, ["battle_win_screen"])["reason"] == "対戦終了: 勝ち (WIN / LOSE の画面)"
    # 読み手: 推定の記録のあと、ランク画面の前に画面を読めたら、画面の勝敗で上書きする (文言と同じ規則)
    assert outcome_from_records([{"type": "outcome", "outcome": "loss", "inferred": True},
                                 {"type": "events", "fired": ["battle_win_screen"]}]) == ("win", False, True)

    def frame(outcome=None):
        return {"scene": "field", "battle_seq": 1, "turn": 4, "events": [], "outcome": outcome,
                "player": {"party": []}, "opponent": {"party": []}}

    # 書き手: 画面で確定した勝敗はそのまま記録 (推定の欄は付かない)
    tmp = Path(tempfile.mkdtemp())
    lg = BattleLogger(log_dir=tmp)
    lg.on_frame(frame(), [])
    lg.on_frame(frame("win"), ["battle_win_screen"])
    f = lg._file
    outs = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if '"outcome"' in x]
    assert len(outs) == 1 and outs[0]["outcome"] == "win" and "inferred" not in outs[0], outs
    # 書き手: 先にランク画面で「不明」と記録したあとに画面を読めたら、訂正の行 (根拠 win_lose_screen)
    lg2 = BattleLogger(log_dir=tmp)
    lg2.on_frame(frame(), [])
    lg2.on_frame(frame(), ["battle_end_rank"])
    lg2.on_frame(frame("loss"), ["battle_lose_screen"])
    outs = [json.loads(x) for x in lg2._file.read_text(encoding="utf-8").splitlines() if '"outcome"' in x]
    assert [o["outcome"] for o in outs] == ["unknown", "loss"] and outs[1]["corrected_from"] == "unknown" \
        and outs[1]["basis"] == "win_lose_screen", outs
    assert lg2.outcome_info() == {"outcome": "loss", "inferred": False, "basis": "win_lose_screen", "basis_text": None}
    print("test_logger_notice_and_readers_treat_screen_like_text OK")


def main() -> None:
    test_read_from_synthetic_screens()
    test_judge_thresholds()
    test_parser_sets_outcome_once_per_battle()
    test_logger_notice_and_readers_treat_screen_like_text()
    print("ALL OK")


if __name__ == "__main__":
    main()
