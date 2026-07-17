# server.py (Production Ready Version)
import base64
import cv2
import numpy as np
import socketio
from fastapi import FastAPI

from extractor import process_frame
from battle_state import battle_state

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app_asgi = socketio.ASGIApp(sio, app)

frame_counter = 0

@sio.on('connect')
async def connect(sid, environ):
    print(f"フロントエンドが接続しました: {sid}")
    # 初期状態を送信
    await sio.emit('state_update', battle_state.to_dict(), room=sid)

@sio.on('send_frame')
async def handle_frame(sid, data):
    global frame_counter
    frame_counter += 1
    
    try:
        encoded_data = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is not None:
            # 基本情報の抽出（HPバー読込など）は重いので30フレームに1回（約1秒に1回）に間引く
            # process_messageは毎フレームTrueで回す（内部で軽量な動的検知が行われるため）
            current_state, triggered_ocr = process_frame(
                img, 
                process_basic_info=(frame_counter % 30 == 0), 
                process_message=True
            )
            
            # OCRが発火した（＝イベントが起きて状態が変化した）瞬間、
            # または1秒に1回定期的にフロントエンドへ状態を同期する
            if triggered_ocr or (frame_counter % 30 == 0):
                await sio.emit('state_update', current_state, room=sid)

    except Exception as e:
        print(f"画像処理エラー: {e}")