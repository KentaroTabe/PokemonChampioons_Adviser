"""BattleLoggerのログ回転の検証。

回転の根拠は状態リセットの世代番号 (battle_seq) のみ。
シーン分類の揺れでは決して回転しない。

旧仕様 (ロガー独自の選出画面ストリークで回転) は 2026-08-11 の接続テストで
「状態リセットと分割がズレて1ファイルに複数対戦が連結される」事故を起こした
ため、battle_seq 連動に変更した (tests/test_session_split.py も参照)。

使い方: python -m tests.test_battle_logger
"""
import shutil
import tempfile
import time
from pathlib import Path

from battle_logger import BattleLogger


def _state(scene, seq=0):
    return {"scene": scene, "outcome": None, "turn": 1, "events": [],
            "field": {}, "selection_picked": None, "mega_used": {},
            "battle_seq": seq,
            "player": {"active_index": None, "remaining": None, "hazards": {},
                       "party": []},
            "opponent": {"active_index": None, "remaining": None, "hazards": {},
                         "party": []}}


def test_rotation_follows_battle_seq():
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        for _ in range(5):
            lg.on_frame(_state("selection"), [])
        lg.on_frame(_state("command"), ["move_player_surf"])
        first = lg._file
        assert first is not None

        # シーン分類がどれだけ揺れても、seqが同じなら回転しない
        for scene in ("selection", "field", "selection", "selection",
                      "command", "selection", "selection", "selection"):
            lg.on_frame(_state(scene), [])
        assert lg._file == first, "seq不変でログが分割された"

        # 状態リセット (seq+1) で回転する
        lg.on_frame(_state("selection", seq=1), [])
        assert lg._file is None or lg._file != first
        lg.on_frame(_state("command", seq=1), ["move_player_surf"])
        second = lg._file
        assert second != first, "新しい対戦でファイルが切り替わらない"
        print("test_rotation_follows_battle_seq OK")
    finally:
        shutil.rmtree(tmp)


def test_outcome_inference():
    # 実戦 (2026-07-22 3試合目): 勝敗メッセージをOCRが取り逃して
    # outcome=unknown でローテーションした。最後にHP0%になった側が
    # その後交代していなければ、その側の負けと推定する
    import json
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        lg.on_frame(_state("command"), ["move_player_surf"])
        first = lg._file
        # 自分側がHP0%に (hpイベントとしてstate.eventsに載る)
        st = _state("field")
        st["events"] = [{"source": "hp", "ts": time.time(),
                         "text": "ブリジュラス 6%→0% (-6%)",
                         "detail": {"side": "player", "from": 6.0, "to": 0.0}}]
        lg.on_frame(st, [])
        # 勝敗メッセージなしでローテーション
        lg._finalize(None)
        records = [json.loads(l) for l in first.read_text().splitlines()]
        oc = [r for r in records if r["type"] == "outcome"][0]
        assert oc["outcome"] == "loss", oc
        assert oc.get("inferred") is True, oc

        # 0%の後に交代があれば推定しない (対戦は続いていた)
        lg2 = BattleLogger(log_dir=tmp)
        time.sleep(1.1)
        lg2.on_frame(_state("command"), ["move_player_surf"])
        f2 = lg2._file
        st = _state("field")
        st["events"] = [{"source": "hp", "ts": time.time(),
                         "text": "x 6%→0%",
                         "detail": {"side": "player", "from": 6.0, "to": 0.0}}]
        lg2.on_frame(st, [])
        lg2.on_frame(_state("field"), ["switch_player"])
        lg2._finalize(None)
        records = [json.loads(l) for l in f2.read_text().splitlines()]
        oc = [r for r in records if r["type"] == "outcome"][0]
        assert oc["outcome"] == "unknown", oc

        # 相手側が最後に0%なら勝ちと推定
        lg3 = BattleLogger(log_dir=tmp)
        time.sleep(1.1)
        lg3.on_frame(_state("command"), ["move_player_surf"])
        f3 = lg3._file
        st = _state("field")
        st["events"] = [{"source": "hp", "ts": time.time(),
                         "text": "x 10%→0%",
                         "detail": {"side": "opponent", "from": 10.0, "to": 0.0}}]
        lg3.on_frame(st, [])
        lg3._finalize(None)
        records = [json.loads(l) for l in f3.read_text().splitlines()]
        oc = [r for r in records if r["type"] == "outcome"][0]
        assert oc["outcome"] == "win", oc
        print("test_outcome_inference OK")
    finally:
        shutil.rmtree(tmp)


def test_outcome_inference_by_rate():
    # 実戦 (5試合目): 最終ひんしも勝敗メッセージも取り逃し、HP0%由来の
    # 推定も効かなかった。結果画面のレート増減から勝敗を推定する
    import json
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        # 前の対戦の結果画面でレート1602を観測済み (ファイルを跨いで保持)
        st = _state("field")
        st["last_rate"] = {"value": 1602, "ts": time.time()}
        lg.on_frame(st, [])
        lg._finalize("win")
        time.sleep(1.1)
        # 新しい対戦 (rate_open=1602)
        lg.on_frame(_state("command"), ["move_player_surf"])
        f = lg._file
        # 対戦後の結果画面でレート1618を観測 (増=勝ち)。勝敗メッセージは欠落
        st = _state("field")
        st["last_rate"] = {"value": 1618, "ts": time.time()}
        lg.on_frame(st, [])
        lg._finalize(None)
        records = [json.loads(l) for l in f.read_text().splitlines()]
        oc = [r for r in records if r["type"] == "outcome"][0]
        assert oc["outcome"] == "win", oc
        assert oc.get("inferred") is True, oc
        rates = [r for r in records if r["type"] == "rate"]
        assert rates and rates[-1]["value"] == 1618, rates

        # レート減=負け
        time.sleep(1.1)
        lg.on_frame(_state("command"), ["move_player_surf"])
        f2 = lg._file
        st = _state("field")
        st["last_rate"] = {"value": 1601, "ts": time.time()}
        lg.on_frame(st, [])
        lg._finalize(None)
        records = [json.loads(l) for l in f2.read_text().splitlines()]
        oc = [r for r in records if r["type"] == "outcome"][0]
        assert oc["outcome"] == "loss", oc

        # 変動が大きすぎる場合 (誤読疑い) は推定しない
        time.sleep(1.1)
        lg.on_frame(_state("command"), ["move_player_surf"])
        f3 = lg._file
        st = _state("field")
        st["last_rate"] = {"value": 2500, "ts": time.time()}
        lg.on_frame(st, [])
        lg._finalize(None)
        records = [json.loads(l) for l in f3.read_text().splitlines()]
        oc = [r for r in records if r["type"] == "outcome"][0]
        assert oc["outcome"] == "unknown", oc
        print("test_outcome_inference_by_rate OK")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_rotation_follows_battle_seq()
    test_outcome_inference()
    test_outcome_inference_by_rate()
    print("ALL OK")
