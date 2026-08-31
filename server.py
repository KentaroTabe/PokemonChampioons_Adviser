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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import socketio
from fastapi import FastAPI

from vision import ocr
from vision.pipeline import VisionPipeline
from vision.scenes import SCENE_SELECTION, SCENE_STANDBY


def should_advise_selection(state: dict) -> bool:
    """選出アドバイスを出してよい文脈か。

    対戦中に standby/selection と誤分類されたフレームで選出提案が
    表示されていた (2026-08-18 第3回テスト: turn進行中に計5回混入)。
    対戦が確定して以降 (battle_active) は、パイプラインの対戦リセット
    (選出3フレーム連続で battle_active が落ちる) を待ってから出す。
    """
    return (state.get("scene") in (SCENE_SELECTION, SCENE_STANDBY)
            and not state.get("outcome")
            and not state.get("battle_active"))
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

# 対戦状態スナップショット: 対戦中のデプロイ/再起動でも選出画面由来の
# 相手ロスター等を失わないよう、定期保存して起動時に復元する
SNAPSHOT_PATH = Path("logs") / "state_snapshot.json"
_last_snapshot_time = 0.0
try:
    if SNAPSHOT_PATH.exists() and \
            time.time() - SNAPSHOT_PATH.stat().st_mtime < 300:
        _snap = json.loads(SNAPSHOT_PATH.read_text())
        if _snap.get("battle_active") or _snap.get("scene") in (
                "command", "move_select", "field", "watch", "battle_hud"):
            pipeline.state.restore_from_dict(_snap)
            print("[server] 対戦状態を復元しました "
                  f"(相手ロスター{len(pipeline.state.opponent.party)}枠, "
                  f"ターン{pipeline.state.turn})")
except Exception as e:
    print(f"[server] 状態復元をスキップ: {e}")

frame_counter = 0
processed_counter = 0
dropped_counter = 0
_busy = False
_pending_frame = None      # 処理中に届いた最新フレーム (sid, data)
_last_state_json = ""
_last_advice_time = 0.0
_last_advice_key = ""
_last_dump_time = 0.0
_last_scene_log = 0.0
_last_scene = "unknown"
_last_frame_ts = 0.0

# デバッグフレームの保存は1枚あたり約46ms (1920x1080 PNG) かかり、
# フレーム処理と同じ経路に置くとその間に届くフレームが捨てられる。
# 専用スレッド1本に投げて処理を止めない (順序は保たれ、取りこぼし時も
# 検証データとしては十分)
_dump_pool = ThreadPoolExecutor(max_workers=1)


def _dump_frame_async(img, prefix: str) -> None:
    def _write(image, name):
        try:
            DUMP_DIR.mkdir(exist_ok=True)
            cv2.imwrite(str(DUMP_DIR / name), image)
        except Exception as e:
            print(f"[server] フレーム保存に失敗: {e}")

    _dump_pool.submit(_write, img.copy(), f"{prefix}_{int(time.time())}.png")


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
    # 構築提案の実行中にページを開き直しても「作成中」表示が復元されるように
    # (2026-08-25 第9回: 実行中である旨の表示が無いという指摘。進捗配信は
    # 発行元のsid宛てだったため、リロード後の画面には何も出なかった)
    if _proposal_running():
        await sio.emit('team_proposal_progress',
                       {"msg": "構築提案を実行中です (数十分かかることがあります)…",
                        "running": True}, room=sid)


@sio.on('send_frame')
async def handle_frame(sid, data):
    """受信フレームの受け口。処理中なら最新1枚だけ保持し、続けて処理する。

    以前は処理中に届いたフレームを即破棄していたため、1枚を処理し終えて
    から次の到着 (最大100ms後) まで遊ぶ時間が生まれ、実効レートが
    100msの倍数に丸められていた (実測: 受信17642枚中62%を破棄)。
    最新1枚を保持して連続処理することで、パイプラインの処理能力ぶんだけ
    確実に拾う (メッセージの見落とし削減)。
    """
    global frame_counter, dropped_counter, _busy, _pending_frame
    global _last_frame_ts
    frame_counter += 1
    _last_frame_ts = time.time()

    if _busy:
        if _pending_frame is not None:
            dropped_counter += 1   # 保持中の1枚を上書き = 実質の破棄
            # 破棄する1枚からメッセージ域だけ救出する (2026-08-31 設計変更:
            # 処理落ちで消えるフレームの瞬間表示メッセージが取り逃しの
            # 主因だった。軽量判定+退避で、OCRは後からパイプラインが消化)
            _, dropped = _pending_frame
            asyncio.get_event_loop().run_in_executor(
                None, _rescue_dropped_frame, dropped)
        _pending_frame = (sid, data)
        return
    _busy = True
    try:
        await _handle_one_frame(sid, data)
        while _pending_frame is not None:
            pend_sid, pend_data = _pending_frame
            _pending_frame = None
            await _handle_one_frame(pend_sid, pend_data)
    finally:
        _busy = False


def _rescue_dropped_frame(data) -> None:
    """破棄フレームのデコード+メッセージ域退避 (executorスレッドで実行)"""
    try:
        encoded = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        pipeline.rescue_scan(img)
    except Exception:
        pass


async def _handle_one_frame(sid, data):
    global processed_counter
    global _last_state_json, _last_advice_time, _last_advice_key
    global _last_dump_time, _last_scene_log
    try:
        encoded_data = data.split(',')[1]
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return

        if DUMP_FRAMES and time.time() - _last_dump_time > 10:
            _last_dump_time = time.time()
            _dump_frame_async(img, "frame")

        # CPU重処理はexecutorで実行し、イベントループ (受信/送信) を塞がない
        loop = asyncio.get_event_loop()
        state, fired = await loop.run_in_executor(None, pipeline.process, img)
        processed_counter += 1
        battle_log.on_frame(state, fired)
        spread_tracker.on_frame(state, fired)   # 相手の型推定 (先後/ダメージ観測)

        # 場の状況/選出画面は貴重な検証データなので、2秒間隔で保存する
        # (選出は自選出ハイライトの検証用: 選出操作の短い時間を捉える)
        if DUMP_FRAMES and state["scene"] in ("field_check", "selection",
                                              "standby") and \
                time.time() - _last_dump_time > 2:
            _last_dump_time = time.time()
            _dump_frame_async(img,
                              "fc" if state["scene"] == "field_check" else "sel")

        # 動作確認用: シーンが変わった瞬間 + 5秒ごとに状況をログ
        global _last_scene
        if state["scene"] != _last_scene:
            print(f"[server] シーン変化: {_last_scene} -> {state['scene']}")
            _last_scene = state["scene"]
        if time.time() - _last_scene_log > 5:
            _last_scene_log = time.time()
            rs = pipeline.rescue_stats
            print(f"[server] scene={state['scene']} 受信={frame_counter} "
                  f"処理={processed_counter} 破棄={dropped_counter} "
                  f"救出={rs['stashed']}/OCR{rs['ocr']}/発火{rs['events']} "
                  f"events={len(state['events'])}")

        if fired:
            for f in fired:
                print(f"[server] イベント検知: {f}")

        # 対戦状態スナップショット (5秒毎。再起動時の対戦中リカバリ用)
        global _last_snapshot_time
        if time.time() - _last_snapshot_time > 5:
            _last_snapshot_time = time.time()
            try:
                SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
                SNAPSHOT_PATH.write_text(
                    json.dumps(state, ensure_ascii=False))
            except OSError:
                pass

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
                # 診断は今対戦した実際の6体に絞る (my_team登録は型ライブラリ
                # として7体以上蓄積されるため、全登録を診断すると
                # 「13体のパーティ」のような結果になる)
                party_ja = [p.get("species_ja") for p in
                            state["player"]["party"] if p.get("species_ja")]
                data = await loop.run_in_executor(
                    None, lambda: team_advice(pipeline.resolver,
                                              party_ja=party_ja))
                text = format_team_advice(data)
                await sio.emit('team_advice', {"text": text, "data": data},
                               room=sid)
                print("--- パーティ診断 ---")
                print(text)
            except Exception as e:
                print(f"[server] パーティ診断エラー: {e}")
        elif state.get("scene") == "selection" and not state.get("outcome"):
            # 次戦の選出でリセット。outcomeが残っている間は解除しない
            # (パイプラインの対戦リセット前に解除すると診断が二重発火する)
            _team_advice_done = False

        # 選出画面: 選出進捗の判定と選出提案 (パーティ情報が変わった時だけ)。
        # outcome/battle_active が残っている間は出さない (前試合の参照と、
        # 対戦中の誤分類フレームでの選出提案混入の防止)
        if should_advise_selection(state):
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
                if advice.get("provisional"):
                    # 確定前: 次フレームで即再計算して安定を確認する
                    # (キーを消さないと状態が動くまで10秒待ちになる)
                    _last_advice_key = None
                elif advice.get("ok"):
                    print("--- アドバイス ---")
                    print(advice["text"])
                else:
                    print(f"[server] アドバイス保留: {advice.get('reason')}")

    except Exception as e:
        print(f"[server] 画像処理エラー: {e}")


def _attach_candidates(state: dict) -> None:
    """相手の未確定ポケモンにタイプ推論の候補リストを付与する (プルダウン用)。

    あわせて型推定トラッカーの実効素早さ推定 (観測で更新される) を
    相手ポケモンへ添付する (フロント表示 + RLの素早さ比較用)
    """
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
    try:
        from advisor.ev_infer import get_tracker
        tracker = get_tracker()
        for p in state["opponent"]["party"]:
            sid_ = p.get("species_id")
            est = tracker._est.get(sid_) if sid_ else None
            if est is None:
                continue
            se = est.speed_estimate(p)
            if se and (se["n_obs"] > 0 or se["lo"] or se["hi"]):
                p["spe_est"] = se["est"]
                p["spe_range"] = [se["lo"], se["hi"]]
    except Exception:
        pass
    # 未確定枠に「次に出してきそう度」を付与 (相手視点のマッチアップ:
    # 自分の場のポケモンに有利な候補ほど次に出やすい、を候補確率で加重)
    try:
        from advisor.dex import get_dex
        from advisor.rl_bridge import _JA2EN_TYPES
        dex = get_dex()
        mi = state["player"].get("active_index")
        me = state["player"]["party"][mi] if mi is not None and \
            mi < len(state["player"].get("party", [])) else None
        my_types = []
        if me:
            my_types = [_JA2EN_TYPES.get(t, t).capitalize()
                        for t in (me.get("types") or [])]
            if not my_types and me.get("species_id"):
                sp_me = dex.species(me["species_id"])
                my_types = sp_me["types"] if sp_me else []
        if my_types:
            for p in state["opponent"]["party"]:
                if p.get("species_ja") or not p.get("candidates"):
                    continue
                acc = tot = 0.0
                for c in p["candidates"]:
                    sp = dex.species(c["id"])
                    if sp is None:
                        continue
                    # 候補のSTABが自分にどれだけ通るか - 自分のSTABの通り
                    offense = max((dex.effectiveness(t, my_types)
                                   for t in sp["types"]), default=1.0)
                    incoming = max((dex.effectiveness(t, sp["types"])
                                    for t in my_types), default=1.0)
                    w = c["pct"] / 100.0
                    acc += w * (offense - 0.8 * incoming)
                    tot += w
                if tot > 0:
                    p["next_score"] = round(acc / tot, 3)
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


# (旧「1からのパーティ提案」(generate_team) は 2026-08-20 に削除。
#  後継はゲート付きの構築提案 run_team_proposal / tools/team_proposal.py)


# ------------------------------------------------------------------
# 分析・コーチング (敗因分析 / 環境 / レビュー / プレイブック / 改善)
# ------------------------------------------------------------------
_analysis_busy = False


def _battle_in_progress() -> bool:
    """対戦中は重い分析ジョブ (構築提案/改善/プレイブック) を実行しない。

    旧判定「フレーム受信中 + シーンが対戦系」は誤検知が多く、メニュー画面が
    field 等に分類されてキャプチャ中は常に対戦中扱いになり、構築提案が
    一度も実行できなかった (2026-08-21 第6回接続テスト)。
    対戦ログの記録内容 (events/hp や command 等の対戦シグナルのみ。
    メニュー誤分類で出続ける advice は含まない) で判定する。
    直近60秒に対戦シグナルが無ければ対戦中ではない
    """
    try:
        from tools.check_battle_active import battle_active
        return battle_active(1.0)
    except Exception:
        # フォールバック: フレーム受信そのものが無ければ対戦中ではない
        return time.time() - _last_frame_ts < 60


@sio.on('run_analysis')
async def run_analysis(sid, data):
    """軽量分析: 敗因分析 / 環境ダイジェスト / 直近対戦レビュー"""
    kind = (data or {}).get("kind", "battles")

    def _work():
        if kind == "meta":
            from tools.meta_digest import digest
            return digest(12, None)
        if kind == "review":
            from tools.review_battle import latest_decided_battle, review_text
            path = latest_decided_battle()
            return review_text(path) if path else "勝敗確定の対戦ログがありません"
        from tools.analyze_battles import run_report
        # 接続テスト中はセッション全体、それ以外は直近50戦
        text, _ = run_report(last=int((data or {}).get("last") or 50),
                             session=True)
        return text

    try:
        text = await asyncio.get_event_loop().run_in_executor(None, _work)
    except Exception as e:
        import traceback
        traceback.print_exc()
        text = f"分析エラー: {e}"
    await sio.emit('analysis_result', {"kind": kind, "text": text}, room=sid)


def _analysis_progress_cb(sid, event: str = 'analysis_progress'):
    loop = asyncio.get_event_loop()

    def _progress(msg):
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(
                sio.emit(event, {"msg": str(msg)}, room=sid)))
    return _progress


async def _run_heavy_analysis(sid, kind, coro_factory,
                              progress_event: str = 'analysis_progress',
                              result_event: str = 'analysis_result'):
    """実対戦を伴う重いジョブ (プレイブック/改善/構築提案) の共通ランナー。

    実対戦ジョブどうしの同時実行は Showdown と CPU を奪い合うため、
    _analysis_busy を全ジョブで共有する。
    """
    global _analysis_busy
    if _analysis_busy or _proposal_running():
        await sio.emit(progress_event,
                       {"msg": "別の実対戦ジョブが実行中です"}, room=sid)
        return
    if _battle_in_progress():
        await sio.emit(result_event,
                       {"kind": kind, "text": "対戦中のため実行できません "
                        "(フレーム解析と競合します)。対戦後に再実行してください"},
                       room=sid)
        return
    _analysis_busy = True
    try:
        text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: asyncio.run(coro_factory()))
        await sio.emit(result_event, {"kind": kind, "text": text},
                       room=sid)
    except Exception as e:
        import traceback
        traceback.print_exc()
        await sio.emit(result_event,
                       {"kind": kind, "text": f"実行エラー: {e}"}, room=sid)
    finally:
        _analysis_busy = False


@sio.on('run_playbook')
async def run_playbook(sid, data):
    """プレイブック生成 (自チーム×環境上位構築、実対戦ベース)"""
    from types import SimpleNamespace
    progress = _analysis_progress_cb(sid)
    args = SimpleNamespace(
        opponents=int((data or {}).get("opponents") or 10),
        battles=int((data or {}).get("battles") or 20),
        concurrency=3, team_file=None)

    async def _job():
        from tools.playbook import run as pb_run
        result = await pb_run(args, log=progress)
        return result["md"]

    await _run_heavy_analysis(sid, "playbook", _job)


@sio.on('get_my_team_detail')
async def get_my_team_detail(sid, data):
    """自分のポケモンの登録詳細と欠落項目を返す (2026-08-25 第9回後:
    「もっと見る」で取り込んだ内容が登録済みか画面で確認したい、への対応)"""
    def _work():
        import advisor.my_team as mt
        from tools.team_proposal import registration_gaps
        entries = mt._load() or {}
        return {"entries": entries, "gaps": registration_gaps(entries)}

    try:
        payload = await asyncio.get_event_loop().run_in_executor(None, _work)
    except Exception as e:
        payload = {"error": str(e)}
    await sio.emit('my_team_detail', payload, room=sid)


@sio.on('check_team_proposal')
async def check_team_proposal(sid, data):
    """構築提案の運用条件チェック (軽量。提案は走らせない)"""
    stage = int((data or {}).get("stage") or 1)

    def _work():
        from tools.team_proposal import (
            evaluate_conditions, measure_inputs, render_report,
        )
        conds = evaluate_conditions(measure_inputs(stage, 40, 120), stage)
        return render_report(conds, stage)

    try:
        text = await asyncio.get_event_loop().run_in_executor(None, _work)
    except Exception as e:
        text = f"条件チェックに失敗: {e}"
    await sio.emit('team_proposal_result', {"kind": "check", "text": text},
                   room=sid)


_proposal_proc = None


def _proposal_running() -> bool:
    return _proposal_proc is not None and _proposal_proc.poll() is None


@sio.on('run_team_proposal')
async def run_team_proposal(sid, data):
    """構築提案 (段階ゲート付き)。ボタン押下時のみ実行する。

    段階1=現パーティから最大2枠入替 / 段階2=メタ全体からの一般提案。
    ⚠ 別プロセス (nohup相当) で実行する: サーバー内で実対戦評価を回すと
    GILとCPUをフレーム解析と奪い合い、処理率が半減して対戦画面の認識が
    崩れた (2026-08-21 第8回: 取りこぼし率54%→76%、決定の助言あり33%)。
    進捗はログファイルのtailを team_proposal_progress で逐次配信する。
    サーバーが再起動してもジョブは走り続ける (結果は logs/team_proposal/)。
    """
    global _proposal_proc
    stage = int((data or {}).get("stage") or 1)
    if _proposal_running() or _analysis_busy:
        await sio.emit('team_proposal_progress',
                       {"msg": "別の実対戦ジョブが実行中です"}, room=sid)
        return
    if _battle_in_progress():
        await sio.emit('team_proposal_result',
                       {"kind": "refused",
                        "text": "対戦中のため実行できません。リザルト画面 "
                                "(ランク表示) まで進めば数秒後に実行できます"},
                       room=sid)
        return

    import subprocess
    import sys as _sys
    log_dir = Path("logs") / "team_proposal"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"
    # -u: サブプロセスの標準出力をバッファさせない。バッファありだと世代
    # 完了 (数分間隔) までログが書かれず、tail配信の「作成中」表示が
    # 長時間沈黙する (2026-08-25 第9回)
    cmd = [_sys.executable, "-u", "-m", "tools.team_proposal", "--propose",
           "--stage", str(stage),
           "--population", str(int((data or {}).get("population") or 8)),
           "--generations", str(int((data or {}).get("generations") or 2)),
           "--battles", str(int((data or {}).get("battles") or 40)),
           "--accept-battles",
           str(int((data or {}).get("accept_battles") or 120)),
           "--max-changes", str(int((data or {}).get("max_changes") or 2))]
    locked = (data or {}).get("locked")
    if locked:
        cmd += ["--locked", locked]
    # 強行 (必須ゲート未達でも実行)。対戦中ガードは対象外:
    # あれは提案の質ではなく画面認識と測定の保護のため
    if (data or {}).get("force"):
        cmd += ["--force"]
    with log_path.open("w") as lf:
        _proposal_proc = subprocess.Popen(
            cmd, stdout=lf, stderr=subprocess.STDOUT,
            start_new_session=True)
    print(f"[server] 構築提案を別プロセスで開始: pid={_proposal_proc.pid} "
          f"log={log_path}")
    asyncio.ensure_future(_watch_proposal(sid, _proposal_proc, log_path))


async def _watch_proposal(sid, proc, log_path):
    """提案サブプロセスのログをtailして進捗/結果を配信する。

    配信は全クライアント宛て (roomなし): 発行元sid宛てだとリロードや
    別画面からは実行中であることが見えない (2026-08-25 第9回指摘)。
    リロード直後の復元は connect ハンドラが行う。
    """
    sent = 0
    try:
        while proc.poll() is None:
            await asyncio.sleep(2.0)
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > sent:
                new = text[sent:]
                sent = len(text)
                tail = [l for l in new.splitlines() if l.strip()][-3:]
                for line in tail:
                    await sio.emit('team_proposal_progress',
                                   {"msg": line[:200], "running": True})
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = "(ログを読めませんでした)"
        # 結果表示: 最終サマリーブロック (「===== 構築提案 =====」以降) を
        # 丸ごと送る。末尾60行の固定切りだと6体分の一覧 (約72行) の先頭 =
        # 1匹目の名前と持ち物が切り落とされた (2026-08-30 第10回指摘)。
        # マーカーが無い場合 (ゲート不通過等) のみ末尾80行にフォールバック
        lines = [l for l in text.splitlines() if l.strip()]
        marker = next((i for i, l in enumerate(lines)
                       if "===== 構築提案 =====" in l), None)
        shown = lines[marker:] if marker is not None else lines[-80:]
        await sio.emit('team_proposal_result',
                       {"kind": "done", "text": "\n".join(shown)})
        print(f"[server] 構築提案プロセス終了: exit={proc.returncode}")
    except Exception as e:
        await sio.emit('team_proposal_result',
                       {"kind": "error", "text": f"進捗監視エラー: {e} "
                        f"(ジョブ自体は継続。結果: {log_path})"})


@sio.on('improve_team')
async def improve_team(sid, data):
    """制約付きパーティ改善 (自チームを種にした進化探索)"""
    from types import SimpleNamespace
    progress = _analysis_progress_cb(sid)
    args = SimpleNamespace(
        population=8, generations=3,
        battles=int((data or {}).get("battles") or 30),
        concurrency=3, forecast_mix=0.3, archive_mix=0.2,
        update_archive=False, seed_myteam=True, seed_file=None,
        locked=(data or {}).get("locked") or None,
        max_changes=int((data or {}).get("max_changes") or 2), seed=None,
        set_mut=0.5)

    async def _job():
        from tools.evolve_teams import run as ev_run
        result = await ev_run(args, log=progress)
        return (f"改善候補 (対環境の実測勝率 {result['fitness']:.0%}):\n\n"
                f"{result['best_ja']}")

    await _run_heavy_analysis(sid, "improve", _job)


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
        # 保存直後にパーティ診断を実行して表示する (登録の即時フィードバック)
        try:
            from advisor.team_advice import team_advice, format_team_advice
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, team_advice, pipeline.resolver)
            await sio.emit('team_advice',
                           {"text": format_team_advice(data), "data": data},
                           room=sid)
            print("--- パーティ診断 (登録時) ---")
        except Exception as e:
            print(f"[server] 登録時診断エラー: {e}")
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
            elif field_name == "types":
                before = list(mon.types or [])
                valid = {"ノーマル", "ほのお", "みず", "でんき", "くさ", "こおり",
                         "かくとう", "どく", "じめん", "ひこう", "エスパー", "むし",
                         "いわ", "ゴースト", "ドラゴン", "あく", "はがね", "フェアリー"}
                types = [t.strip() for t in str(value).replace("・", "/").split("/")
                         if t.strip() in valid][:2]
                if types:
                    mon.types = types
                    # タイプ修正で種族の再推測が可能になる (species未確定なら再計算)
                    if not mon.species_ja:
                        mon.species_id = None
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
        # プルダウン描画から選択までの間に、対象枠が別フレームで自動確定
        # されることがある (2026-08-20: 選んだのに反映されない一因)。
        # 対象枠が既に別種族で確定済みなら、未確定枠へ付け替える。
        # 同種族で確定済みなら何もしない (二重適用の防止)
        if 0 <= idx < len(party) and party[idx].species_ja \
                and party[idx].species_ja != species_ja:
            alt = next((j for j, p in enumerate(party)
                        if not p.species_ja), None)
            print(f"[server] 手動確定: slot{idx}は{party[idx].species_ja}で"
                  f"確定済みのため slot{alt} へ付け替え")
            if alt is None:
                pipeline.state.log_event(
                    "manual",
                    f"手動確定を無視: {species_ja} (空き枠なし・全枠確定済み)",
                    event_id="species_manual_skip")
                await sio.emit('state_update', pipeline.state.to_dict(),
                               room=sid)
                return
            idx = alt
        if 0 <= idx < len(party):
            party[idx].merge_species(species_ja, species_id)
            # 直近の「HUD名不一致」で観測された別名をこの個体に紐づける
            # (試合中の個体名キャッシュ: 以後その名前のイベントが正しく帰属する)
            import re as _re
            now_ts = time.time()
            for e in pipeline.state.events[-40:]:
                if e.get("event") != "hud_name_mismatch":
                    continue
                if now_ts - e.get("ts", 0) > 120:
                    continue
                m = _re.search(r"\(([^≠)]+)≠", e.get("text") or "")
                if m:
                    alias = m.group(1).strip()
                    if alias and alias not in party[idx].aliases:
                        party[idx].aliases.append(alias)
                        print(f"[server] 別名を紐づけ: {species_ja} <- {alias}")
            party[idx].aliases = party[idx].aliases[-6:]
            pipeline.state.log_event(
                "manual", f"相手の{species_ja}を手動確定 (候補から選択)",
                event_id="species_manual")
            print(f"[server] 手動確定: 相手slot{idx} = {species_ja}")
            # 自己改善ループ: 確定した種族のアイコンを直近の選出フレームから
            # 収穫し、実キャプチャテンプレートとして保存する (次回から
            # 同タイプ複数候補でも視覚照合で自動確定できるようになる)
            try:
                from vision.spriteid import harvest_from_frame
                import glob as _glob
                frames = sorted(_glob.glob(str(DUMP_DIR / "sel_*.png")),
                                reverse=True)
                for fp in frames[:5]:   # 直近5枚から最初に検証を通ったもの
                    ts = int(Path(fp).stem.split("_")[-1])
                    if time.time() - ts > 300:
                        break
                    if harvest_from_frame(fp, idx, species_id):
                        print(f"[server] 種族アイコン収穫: {species_ja} <- "
                              f"{Path(fp).name}")
                        break
            except Exception as e:
                print(f"[server] アイコン収穫スキップ: {e}")
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
