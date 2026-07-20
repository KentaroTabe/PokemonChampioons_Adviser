"""対戦ログ記録のE2E確認ツール。

保存済みフレームを時系列でパイプラインに流し、BattleLoggerが生成する
JSONLの構造を検証する。

    python -m tools.check_battle_log "debug_frames/frame_17844*.png"
"""
from __future__ import annotations

import glob
import json
import sys
import tempfile
from pathlib import Path

import cv2

from battle_logger import BattleLogger
from vision.pipeline import VisionPipeline
from advisor.service import Advisor


def main() -> None:
    pattern = sys.argv[1] if len(sys.argv) > 1 else "debug_frames/frame_17844*.png"
    files = sorted(glob.glob(pattern))
    assert files, f"フレームがありません: {pattern}"

    tmpdir = Path(tempfile.mkdtemp(prefix="battlelog_test_"))
    logger = BattleLogger(log_dir=tmpdir)
    pipe = VisionPipeline()
    advisor = Advisor(resolver=pipe.resolver)

    advice_done = False
    for f in files:
        state, fired = pipe.process(cv2.imread(f), single_shot=True)
        logger.on_frame(state, fired)
        if not advice_done and state["scene"] in ("command", "move_select"):
            advice = advisor.advise(state)
            advice["text"] = advisor.format_advice(advice)
            logger.on_advice(advice, "battle")
            advice_done = True
    logger._finalize(pipe.state.outcome)

    logs = list(tmpdir.glob("*.jsonl"))
    assert logs, "ログファイルが生成されていません"
    records = [json.loads(l) for l in logs[0].read_text().splitlines()]
    kinds = [r["type"] for r in records]
    print(f"ログ: {logs[0].name} レコード数={len(records)}")
    print("種別内訳:", {k: kinds.count(k) for k in set(kinds)})
    for r in records[:6]:
        summary = {k: v for k, v in r.items() if k not in ("state", "advice")}
        print(" ", summary)
    assert "scene" in kinds and "outcome" in kinds
    assert any(r["type"] == "events" for r in records)
    assert any(r["type"] == "advice" for r in records)
    print("check_battle_log OK")


if __name__ == "__main__":
    main()
