# server.py — ミラーリング映像の受信 -> 状態抽出 -> アドバイス配信
#
# フロントエンド (index.html) から WebSocket で受け取ったフレームを
# vision.VisionPipeline で解析し、状態が変わるたびに
#   - state_update : バトル状態 (BattleStateV2.to_dict())
#   - advice_update: 行動アドバイス (advisor.engine.evaluate の結果)
# を配信する。
#
# 起動: uvicorn server:app_asgi --host 0.0.0.0 --port 8000
import base64
import json
import time

import cv2
import numpy as np
import socketio
from fastapi import FastAPI

from vision.pipeline import VisionPipeline
from advisor.service import Advisor

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app_asgi = socketio.ASGIApp(sio, app)

pipeline = VisionPipeline()
advisor = Advisor(resolver=pipeline.resolver)

frame_counter = 0
_last_state_json = ""
_last_advice_time = 0.0
_last_advice_key = ""


def _advice_key(state: dict) -> str:
    """アドバイス再計算が必要かどうかの判定キー"""
    try:
        me = state["player"]["party"][state["player"]["active_index"]]
        opp_idx = state["opponent"]["active_index"]
        opp = state["opponent"]["party"][opp_idx] if opp_idx is not None else {}
        return json.dumps([
            state["scene"], me.get("species_id"), me.get("hp_percent"),
            [m.get("pp") for m in me.get("moves", [])],
            opp.get("species_id"), opp.get("hp_percent"),
            state["field"], me.get("boosts"), opp.get("boosts"),
        ], ensure_ascii=False)
    except Exception:
        return ""


@sio.on('connect')
async def connect(sid, environ):
    print(f"フロントエンドが接続しました: {sid}")
    await sio.emit('state_update', pipeline.state.to_dict(), room=sid)


@sio.on('send_frame')
async def handle_frame(sid, data):
    global frame_counter, _last_state_json, _last_advice_time, _last_advice_key
    frame_counter += 1

    try:
        encoded_data = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return

        state, fired = pipeline.process(img)

        # 状態変化時 or 2秒ごとに同期
        state_json = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if fired or state_json != _last_state_json or frame_counter % 60 == 0:
            _last_state_json = state_json
            await sio.emit('state_update', state, room=sid)

        # コマンド選択中のみアドバイスを計算 (状態が変わった時だけ)
        if state["scene"] in ("command", "move_select", "watch"):
            key = _advice_key(state)
            now = time.time()
            if key and (key != _last_advice_key or now - _last_advice_time > 10.0):
                _last_advice_key = key
                _last_advice_time = now
                advice = advisor.advise(state)
                advice["text"] = advisor.format_advice(advice)
                await sio.emit('advice_update', advice, room=sid)
                if advice.get("ok"):
                    print("--- アドバイス ---")
                    print(advice["text"])

    except Exception as e:
        print(f"画像処理エラー: {e}")


@sio.on('reset_state')
async def reset_state(sid, data=None):
    """フロントエンドから状態リセット要求 (新しい対戦の開始など)"""
    pipeline.reset()
    await sio.emit('state_update', pipeline.state.to_dict(), room=sid)
    print("状態をリセットしました")
