# server.py
import base64
import cv2
import numpy as np
import socketio
from fastapi import FastAPI

# 先ほど作成した抽出モジュールをインポート
from extractor import extract_battle_info

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app_asgi = socketio.ASGIApp(sio, app)

@sio.on('connect')
async def connect(sid, environ):
    print(f"フロントエンドが接続しました: {sid}")

@sio.on('send_frame')
async def handle_frame(sid, data):
    try:
        encoded_data = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is not None:
            # extractor.py の関数を呼び出して解析
            extraction_result = extract_battle_info(img)
            
            # 結果が空（UI非表示など）でなければフロントエンドへ返す
            if extraction_result:
                print(f"抽出結果: {extraction_result}")
                await sio.emit('result', extraction_result, room=sid)

    except Exception as e:
        print(f"画像処理エラー: {e}")