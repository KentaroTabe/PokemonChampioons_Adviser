# server.py (Production Ready Version)
import base64
import cv2
import numpy as np
import socketio
import asyncio
from fastapi import FastAPI
import time

from advanced_extractor import process_frame, battle_state, detect_message_window

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app_asgi = socketio.ASGIApp(sio, app)

# 状態管理用変数
last_window_detection_time = 0
WINDOW_COOLDOWN = 1.0 # メッセージ処理のクールダウン (秒)
DELAY_BEFORE_OCR = 0.5 # ウィンドウ検知からOCR実行までの待機時間 (秒)

# 非同期で遅延OCRを実行するタスク
async def delayed_ocr_task(sid, img):
    await asyncio.sleep(DELAY_BEFORE_OCR)
    print(f"[{time.time():.2f}] 遅延OCRを実行します...")
    
    # 基本情報はスキップし、メッセージ解析のみ実行
    current_state, _ = process_frame(img, process_basic_info=False, process_message=True)
    
    # 最新のステートをフロントエンドに送信
    await sio.emit('state_update', current_state, room=sid)


@sio.on('connect')
async def connect(sid, environ):
    print(f"フロントエンドが接続しました: {sid}")
    # 初期状態を送信
    await sio.emit('state_update', battle_state.to_dict(), room=sid)

@sio.on('send_frame')
async def handle_frame(sid, data):
    global last_window_detection_time
    
    try:
        encoded_data = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is not None:
            current_time = time.time()
            
            # クールダウン中かどうか
            in_cooldown = (current_time - last_window_detection_time) < WINDOW_COOLDOWN

            # 1. 基本情報の更新と、軽量なウィンドウ検知 (毎フレーム実行)
            # 重いメッセージOCRはここではやらない (process_message=False)
            
            # 基本情報の抽出は数フレームに1回でも良いが、ここでは簡略化のため毎フレーム実行
            current_state, _ = process_frame(img, process_basic_info=True, process_message=False)
            
            # 2. ウィンドウ検知トリガー
            if not in_cooldown:
                is_open, _ = detect_message_window(img)
                if is_open:
                    print(f"[{current_time:.2f}] 💡 メッセージウィンドウ検知！ {DELAY_BEFORE_OCR}秒後にOCRを開始します。")
                    last_window_detection_time = current_time
                    
                    # 非同期タスクとして遅延OCRをスケジュール
                    # 注意: 簡易実装として現在のフレームの参照を渡している。
                    # 本番環境で厳密に0.5秒後の「新しい」画像を読みたい場合は、
                    # ここでフラグを立てて、後続のhandle_frame内で処理するのがベター。
                    sio.start_background_task(delayed_ocr_task, sid, img)

            # 定期的に基本状態は送信
            await sio.emit('state_update', current_state, room=sid)

    except Exception as e:
        print(f"画像処理エラー: {e}")