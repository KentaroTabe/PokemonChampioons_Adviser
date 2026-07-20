# server.py — ミラーリング映像の受信 -> 状態抽出 -> アドバイス配信
#
# フロントエンド (index.html) から WebSocket で受け取ったフレームを
# vision.VisionPipeline で解析し、状態が変わるたびに
#   - state_update : バトル状態 (BattleStateV2.to_dict())
#   - advice_update: 行動アドバイス (advisor.engine.evaluate の結果)
# を配信する。
#
# 起動: uvicorn server:app_asgi --host 0.0.0.0 --port 8000
#
# デバッグ: 環境変数 DEBUG_DUMP_FRAMES=1 で受信フレームを約10秒ごとに
# debug_frames/ に保存する (実映像でのゾーン調整用)。
import asyncio
import base64
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import socketio
from fastapi import FastAPI

from vision import ocr
from vision.pipeline import VisionPipeline
from advisor.service import Advisor
from battle_logger import BattleLogger

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
app_asgi = socketio.ASGIApp(sio, app)

pipeline = VisionPipeline()
advisor = Advisor(resolver=pipeline.resolver)
battle_log = BattleLogger()
from advisor.ev_infer import get_tracker as _get_spread_tracker
spread_tracker = _get_spread_tracker()

# 起動 (更新反映) のタイミングで不要ログを掃除する
# (断片対戦ログ / 古いデバッグフレーム。失敗してもサーバーは起動する)
try:
    from tools.cleanup_logs import cleanup
    cleanup()
except Exception as e:
    print(f"[server] ログ掃除をスキップ: {e}")

# バトル中の初回OCRで初期化が走ると数十秒フレームが詰まるため、
# サーバー起動時に先にウォームアップしておく (Apple Vision優先)
print("[server] OCRバックエンドを初期化します...")
ocr.preload()
print("[server] 準備完了。フロントエンドからの接続を待っています。")

DUMP_FRAMES = os.environ.get("DEBUG_DUMP_FRAMES") == "1"
DUMP_DIR = Path("debug_frames")

frame_counter = 0
processed_counter = 0
dropped_counter = 0
_busy = False
_last_state_json = ""
_last_advice_time = 0.0
_last_advice_key = ""
_last_dump_time = 0.0
_last_scene_log = 0.0
_last_scene = "unknown"


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
    print(f"[server] フロントエンドが接続しました: {sid}")
    await sio.emit('state_update', pipeline.state.to_dict(), room=sid)


@sio.on('send_frame')
async def handle_frame(sid, data):
    global frame_counter, processed_counter, dropped_counter, _busy
    global _last_state_json, _last_advice_time, _last_advice_key
    global _last_dump_time, _last_scene_log
    frame_counter += 1

    # 処理が追いつかない場合は古いフレームを捨てる (最新優先)。
    # OCRは重いので、これが無いとキューが伸び続けて表示が遅延し続ける
    if _busy:
        dropped_counter += 1
        return
    _busy = True
    try:
        encoded_data = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return

        if DUMP_FRAMES and time.time() - _last_dump_time > 10:
            _last_dump_time = time.time()
            DUMP_DIR.mkdir(exist_ok=True)
            cv2.imwrite(str(DUMP_DIR / f"frame_{int(time.time())}.png"), img)

        # CPU重処理はexecutorで実行し、イベントループ (受信/送信) を塞がない
        loop = asyncio.get_event_loop()
        state, fired = await loop.run_in_executor(None, pipeline.process, img)
        processed_counter += 1
        battle_log.on_frame(state, fired)
        spread_tracker.on_frame(state, fired)   # 相手の型推定 (先後/ダメージ観測)

        # 場の状況画面は貴重な検証データなので、検出したら間隔に関係なく保存する
        if DUMP_FRAMES and state["scene"] == "field_check" and \
                time.time() - _last_dump_time > 2:
            _last_dump_time = time.time()
            DUMP_DIR.mkdir(exist_ok=True)
            cv2.imwrite(str(DUMP_DIR / f"fc_{int(time.time())}.png"), img)

        # 動作確認用: シーンが変わった瞬間 + 5秒ごとに状況をログ
        global _last_scene
        if state["scene"] != _last_scene:
            print(f"[server] シーン変化: {_last_scene} -> {state['scene']}")
            _last_scene = state["scene"]
        if time.time() - _last_scene_log > 5:
            _last_scene_log = time.time()
            print(f"[server] scene={state['scene']} 受信={frame_counter} "
                  f"処理={processed_counter} 破棄={dropped_counter} events={len(state['events'])}")

        if fired:
            for f in fired:
                print(f"[server] イベント検知: {f}")

        _attach_candidates(state)
        state_json = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if fired or state_json != _last_state_json or processed_counter % 20 == 0:
            _last_state_json = state_json
            await sio.emit('state_update', state, room=sid)

        # 試合終了: パーティ診断・改善案を1回だけ配信する
        global _team_advice_done
        if state.get("outcome") in ("win", "loss") and not _team_advice_done:
            _team_advice_done = True
            try:
                from advisor.team_advice import team_advice, format_team_advice
                data = await loop.run_in_executor(
                    None, team_advice, pipeline.resolver)
                text = format_team_advice(data)
                await sio.emit('team_advice', {"text": text, "data": data},
                               room=sid)
                print("--- パーティ診断 ---")
                print(text)
            except Exception as e:
                print(f"[server] パーティ診断エラー: {e}")
        elif state.get("scene") == "selection":
            _team_advice_done = False

        # 選出画面: 選出進捗の判定と選出提案 (パーティ情報が変わった時だけ)
        if state["scene"] in ("selection", "standby"):
            sel_key = json.dumps([
                state.get("selection_picked"),
                [p.get("species_id") for p in state["player"]["party"]],
                [p.get("types") for p in state["opponent"]["party"]],
                [p.get("is_picked") for p in state["player"]["party"]],
            ], ensure_ascii=False)
            now = time.time()
            if sel_key != _last_advice_key or now - _last_advice_time > 15.0:
                _last_advice_key = sel_key
                _last_advice_time = now
                advice = await loop.run_in_executor(None, advisor.advise_selection, state)
                battle_log.on_advice(advice, "selection")
                await sio.emit('advice_update', advice, room=sid)
                print("--- 選出アドバイス ---")
                print(advice["text"])

        # コマンド選択中のみアドバイスを計算 (状態が変わった時だけ)
        if state["scene"] in ("command", "move_select", "watch"):
            key = _advice_key(state)
            now = time.time()
            if key and (key != _last_advice_key or now - _last_advice_time > 10.0):
                _last_advice_key = key
                _last_advice_time = now
                advice = await loop.run_in_executor(None, advisor.advise, state)
                advice["text"] = advisor.format_advice(advice)
                battle_log.on_advice(advice, "battle")
                await sio.emit('advice_update', advice, room=sid)
                if advice.get("ok"):
                    print("--- アドバイス ---")
                    print(advice["text"])
                else:
                    print(f"[server] アドバイス保留: {advice.get('reason')}")

    except Exception as e:
        print(f"[server] 画像処理エラー: {e}")
    finally:
        _busy = False


def _attach_candidates(state: dict) -> None:
    """相手の未確定ポケモンにタイプ推論の候補リストを付与する (プルダウン用)"""
    try:
        from advisor.infer import get_inference
        for i, p in enumerate(state["opponent"]["party"]):
            if p.get("species_ja") or not p.get("types"):
                continue
            cands = get_inference().candidates(p["types"], top_k=8)
            if cands:
                p["candidates"] = [
                    {"id": sid_, "ja": ja, "pct": round(prob * 100, 1)}
                    for sid_, prob, ja in cands]
    except Exception:
        pass


_MANUAL_FIELDS = {"hp_percent", "hp_current", "status", "item", "ability",
                  "boost", "is_mega", "clear_status"}
_team_advice_done = False
MY_TEAM_PATH = Path(__file__).resolve().parent / "config" / "my_team.json"


@sio.on('get_my_team')
async def get_my_team(sid, data=None):
    """登録済みパーティと入力サジェスト用の名前一覧を返す"""
    try:
        team = {}
        if MY_TEAM_PATH.exists():
            team = json.loads(MY_TEAM_PATH.read_text(encoding="utf-8"))
        names = {cat: sorted(j for j, *_ in pipeline.resolver._entries.get(cat, []))
                 for cat in ("species", "moves", "items", "abilities")}
        from advisor.my_team import _NATURES
        names["natures"] = [n for n in _NATURES if not n.isascii()]
        await sio.emit('my_team_data', {"team": team, "names": names}, room=sid)
    except Exception as e:
        print(f"[server] get_my_teamエラー: {e}")


@sio.on('save_my_team')
async def save_my_team(sid, data):
    """フロントエンドのパーティ編集フォームから config/my_team.json を保存"""
    try:
        team = data.get("team") or {}
        # 最低限の妥当性チェック (種族名が解決できるエントリのみ保存)
        cleaned = {}
        for ja, entry in team.items():
            if not ja or not pipeline.resolver.resolve_species(ja, cutoff=0.85):
                print(f"[server] my_team: 種族を解決できずスキップ: {ja}")
                continue
            cleaned[ja] = entry
        MY_TEAM_PATH.parent.mkdir(exist_ok=True)
        MY_TEAM_PATH.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[server] my_team.json 保存: {len(cleaned)}体 ({list(cleaned)})")
        await sio.emit('my_team_saved', {"ok": True, "count": len(cleaned)},
                       room=sid)
    except Exception as e:
        print(f"[server] save_my_teamエラー: {e}")
        await sio.emit('my_team_saved', {"ok": False, "reason": str(e)}, room=sid)


@sio.on('set_state')
async def set_state(sid, data):
    """フロントエンドからの手動修正 (誤認識のユーザー訂正)。

    data: {"target": "mon", "side": "player|opponent", "index": int,
           "field": "hp_percent|status|item|ability|boost:atk|is_mega", "value": ...}
          {"target": "field", "field": "weather|terrain|trick_room", "value": ...}
          {"target": "hazards", "side": ..., "field": "stealth_rock|spikes", "value": ...}
    修正は manual_fix イベントとして対戦ログに記録される (誤認識分析用)。
    """
    try:
        target = data.get("target")
        field_name = str(data.get("field", ""))
        value = data.get("value")
        before = None
        label = ""
        if target == "mon":
            side = pipeline.state.side(data["side"])
            mon = side.party[int(data["index"])]
            label = f"{data['side']}:{mon.species_ja or '?'}:{field_name}"
            if field_name == "hp_percent":
                before = mon.hp_percent
                mon.hp_percent = float(value)
                if mon.hp_max:
                    mon.hp_current = round(float(value) / 100 * mon.hp_max)
                mon._hp_last_read = float(value)
                mon._hp_event_base = float(value)
            elif field_name == "status":
                before = mon.status
                mon.status = value or None
            elif field_name == "item":
                before = mon.item_ja
                r = pipeline.resolver.resolve(value, "items", cutoff=0.7) if value else None
                mon.item_ja, mon.item_id = (r[0], r[1]) if r else (value or None, None)
            elif field_name == "ability":
                before = mon.ability_ja
                r = pipeline.resolver.resolve(value, "abilities", cutoff=0.7) if value else None
                mon.ability_ja, mon.ability_id = (r[0], r[1]) if r else (value or None, None)
            elif field_name.startswith("boost:"):
                stat = field_name.split(":", 1)[1]
                before = mon.boosts.get(stat)
                mon.boosts[stat] = max(-6, min(6, int(value)))
            elif field_name == "is_mega":
                before = mon.is_mega
                mon.is_mega = bool(value)
        elif target == "field":
            f = pipeline.state.field
            label = f"field:{field_name}"
            before = getattr(f, field_name, None)
            if field_name in ("weather", "terrain"):
                setattr(f, field_name, value or None)
            elif field_name == "trick_room":
                f.trick_room = bool(value)
        elif target == "hazards":
            side = pipeline.state.side(data["side"])
            label = f"{data['side']}:hazards:{field_name}"
            before = getattr(side, field_name, None)
            if field_name in ("stealth_rock",):
                side.stealth_rock = bool(value)
            elif field_name == "spikes":
                side.spikes = max(0, min(3, int(value)))
            elif field_name == "toxic_spikes":
                side.toxic_spikes = max(0, min(2, int(value)))
        pipeline.state.log_event(
            "manual", f"手動修正 {label}: {before} -> {value}",
            event_id="manual_fix",
            detail={"target": target, "field": field_name,
                    "label": label, "before": before, "after": value})
        print(f"[server] 手動修正: {label} {before} -> {value}")
        st = pipeline.state.to_dict()
        _attach_candidates(st)
        await sio.emit('state_update', st, room=sid)
    except Exception as e:
        print(f"[server] set_stateエラー: {e}")


@sio.on('set_species')
async def set_species(sid, data):
    """フロントエンドのプルダウンから相手ポケモンの種族を確定する"""
    try:
        idx = int(data["index"])
        species_id = data["species_id"]
        species_ja = data.get("species_ja") or species_id
        party = pipeline.state.opponent.party
        if 0 <= idx < len(party):
            party[idx].merge_species(species_ja, species_id)
            pipeline.state.log_event(
                "manual", f"相手の{species_ja}を手動確定 (候補から選択)",
                event_id="species_manual")
            print(f"[server] 手動確定: 相手slot{idx} = {species_ja}")
            state = pipeline.state.to_dict()
            _attach_candidates(state)
            await sio.emit('state_update', state, room=sid)
    except Exception as e:
        print(f"[server] set_speciesエラー: {e}")


@sio.on('reset_state')
async def reset_state(sid, data=None):
    """フロントエンドから状態リセット要求 (新しい対戦の開始など)"""
    pipeline.reset()
    spread_tracker.reset()
    await sio.emit('state_update', pipeline.state.to_dict(), room=sid)
    print("[server] 状態をリセットしました")
