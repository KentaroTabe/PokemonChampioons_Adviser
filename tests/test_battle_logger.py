"""BattleLoggerのログ回転デバウンスの検証。

選出画面のシーン分類が数フレーム揺れても、1対戦=1ファイルが保たれることを
確認する (実運用で3秒間に3ファイル分割される事象が起きた)。

使い方: python -m tests.test_battle_logger
"""
import shutil
import tempfile
import time
from pathlib import Path

from battle_logger import BattleLogger


def _state(scene):
    return {"scene": scene, "outcome": None, "turn": 1, "events": [],
            "field": {}, "selection_picked": None, "mega_used": {},
            "player": {"active_index": None, "remaining": None, "hazards": {},
                       "party": []},
            "opponent": {"active_index": None, "remaining": None, "hazards": {},
                         "party": []}}


def test_rotation_debounce():
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        # 対戦1開始 (選出3フレーム -> ファイル生成はwrite時)
        for _ in range(5):
            lg.on_frame(_state("selection"), [])
        lg.on_frame(_state("command"), ["move_player_surf"])
        first = lg._file
        assert first is not None

        # 対戦中に選出画面へ1-2フレーム誤分類が混ざっても回転しない
        for scene in ("selection", "field", "selection", "selection", "command"):
            lg.on_frame(_state(scene), [])
        assert lg._file == first, "誤分類フレームでログが分割された"

        # 開始直後 (30秒以内) は選出が3フレーム続いても回転しない
        for _ in range(5):
            lg.on_frame(_state("selection"), [])
        assert lg._file == first, "30秒以内に回転した"

        # 対戦済み証拠 (command/move_select 累計5フレーム以上) を満たす
        for _ in range(5):
            lg.on_frame(_state("command"), [])

        # 30秒経過後の選出画面3連続で次の対戦として回転する
        lg._opened_ts = time.time() - 31.0
        lg._selection_streak = 0
        time.sleep(1.1)   # ファイル名は秒解像度のため、同一秒内の再生成を避ける
        for _ in range(3):
            lg.on_frame(_state("selection"), [])
        lg.on_frame(_state("selection"), [])
        assert lg._file is None or lg._file != first
        lg.on_frame(_state("command"), ["move_player_surf"])
        second = lg._file
        assert second != first, "新しい対戦でファイルが切り替わらない"

        # 長い選出画面: 対戦フレームが5未満のファイルは30秒超でも回転しない
        # (選出中の演出でcommandが2フレーム誤分類されるケースを含む)
        lg._finalize(None)
        for _ in range(5):
            lg.on_frame(_state("selection"), [])
        lg.on_frame(_state("command"), [])   # 誤分類2フレーム相当
        lg.on_frame(_state("command"), [])
        third = lg._file
        lg._opened_ts = time.time() - 31.0
        lg._selection_streak = 0
        for _ in range(4):
            lg.on_frame(_state("selection"), [])
        assert lg._file == third, "選出画面のみのファイルが回転した"
        print("test_rotation_debounce OK")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_rotation_debounce()
    print("ALL OK")
