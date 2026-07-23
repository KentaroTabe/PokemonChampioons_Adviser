"""対戦ログの自動記録 (JSONL)。

アドバイザー較正・報酬チューニング・振り返りの土台となるデータ収集層。
1対戦 = 1ファイル (logs/battles/battle_YYYYmmdd_HHMMSS.jsonl) に、
以下のレコードを時系列で追記する:

  {"t": ..., "type": "scene",   "scene": ..., "state": {...簡約状態...}}
  {"t": ..., "type": "events",  "fired": [...], "scene": ...}
  {"t": ..., "type": "advice",  "kind": "battle"|"selection", "advice": {...}}
  {"t": ..., "type": "outcome", "outcome": "win"|"loss"|"unknown",
   ("inferred": true — 勝敗メッセージ取り逃し時のHP文脈からの推定)}

プレイヤーが実際に選んだ行動は events の move_player_* / switch_player として
記録される (アドバイスとの突き合わせで採用率・成績を後段で分析できる)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).resolve().parent / "logs" / "battles"


def _compact_state(state: dict) -> dict:
    """ログ用の簡約状態 (イベント履歴を除き、パーティは主要フィールドのみ)"""
    def mon(p):
        return {
            "species": p.get("species_id"),
            "ja": p.get("species_ja"),
            "types": p.get("types"),
            "hp": p.get("hp_percent"),
            "hp_raw": [p.get("hp_current"), p.get("hp_max")],
            "status": p.get("status"),
            "boosts": {k: v for k, v in (p.get("boosts") or {}).items() if v},
            "mega": p.get("is_mega"),
            "item": p.get("item_id"),
            "ability": p.get("ability_id"),
            "moves": [[m.get("move_id"), m.get("pp")] for m in (p.get("moves") or [])],
            "revealed": p.get("revealed_moves"),
            "picked": p.get("is_picked"),
        }

    return {
        "scene": state.get("scene"),
        "field": state.get("field"),
        "selection_picked": state.get("selection_picked"),
        "player": {
            "active": state["player"].get("active_index"),
            "remaining": state["player"].get("remaining"),
            "hazards": state["player"].get("hazards"),
            "party": [mon(p) for p in state["player"].get("party", [])],
        },
        "opponent": {
            "active": state["opponent"].get("active_index"),
            "remaining": state["opponent"].get("remaining"),
            "hazards": state["opponent"].get("hazards"),
            "party": [mon(p) for p in state["opponent"].get("party", [])],
        },
        "mega_used": state.get("mega_used"),
    }


class BattleLogger:
    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self._file: Optional[Path] = None
        self._prev_scene: Optional[str] = None
        self._prev_pick_key = None
        self._outcome_logged = False
        self._hp_seen_ts = 0.0   # 記録済みHP変化イベントの最終時刻
        self._selection_streak = 0  # 選出画面が連続何フレーム続いているか
        self._opened_ts = 0.0       # 現在のログファイルを開いた時刻
        self._battle_frames = 0     # command/move_selectの累計フレーム数
        self._last_zero = None      # 最後にHP0%を観測した側 (side, ts)
        self._last_switch = {}      # 各側の最後の交代時刻 {side: ts}
        self._rate_open = None      # この対戦に入る前のレート {"value","ts"}
        self._rate_last = None      # 直近観測レート (ファイルを跨いで保持)

    # ------------------------------------------------------------------
    def _open_new(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        name = time.strftime("battle_%Y%m%d_%H%M%S.jsonl")
        self._file = self.log_dir / name
        self._opened_ts = time.time()
        self._outcome_logged = False
        self._rate_open = self._rate_last   # この対戦に入る時点のレート
        print(f"[battle_log] 新しい対戦ログ: {self._file.name}")

    def _write(self, record: dict) -> None:
        if self._file is None:
            self._open_new()
        record["t"] = round(time.time(), 2)
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _infer_outcome(self) -> Optional[str]:
        """勝敗メッセージを取り逃した場合のフォールバック推定。

        最後にHP0%を観測した側が、その後交代せずに対戦が終わっていれば
        その側の負けとみなす (実戦: 勝敗表示が数秒で流れてOCRが取り逃し、
        outcome=unknown でローテーションした)。推定は直近3分の観測に限る。
        """
        # 1. レートの増減 (結果画面に必ず表示され、増=勝ち/減=負けが確実)
        ro, rl = self._rate_open, self._rate_last
        if ro and rl and rl["ts"] > self._opened_ts \
                and rl["value"] != ro["value"]:
            delta = rl["value"] - ro["value"]
            if abs(delta) <= 60:   # 1戦の変動として妥当な範囲のみ (誤読対策)
                return "win" if delta > 0 else "loss"
        # 2. 最後にHP0%を観測した側の負け (その後交代していない場合)
        if not self._last_zero:
            return None
        side, ts = self._last_zero
        if time.time() - ts > 180.0:
            return None
        if self._last_switch.get(side, 0.0) > ts:
            return None   # 倒れた後に交代している = 対戦は続いていた
        return "loss" if side == "player" else "win"

    def _finalize(self, outcome: Optional[str]) -> None:
        if self._file is not None and not self._outcome_logged:
            if outcome:
                self._write({"type": "outcome", "outcome": outcome})
            else:
                inferred = self._infer_outcome()
                rec = {"type": "outcome", "outcome": inferred or "unknown"}
                if inferred:
                    rec["inferred"] = True
                self._write(rec)
            self._outcome_logged = True
        self._file = None
        self._prev_scene = None
        self._hp_seen_ts = 0.0
        self._battle_frames = 0
        self._last_zero = None
        self._last_switch = {}

    # ------------------------------------------------------------------
    def on_frame(self, state: dict, fired: list) -> None:
        """毎フレーム呼び出し。シーン変化・イベント・勝敗を記録する"""
        scene = state.get("scene")

        # 新しい対戦の開始検知: 選出画面に入った時点でファイルを切り替える。
        # シーン分類は数フレーム揺れることがあるため、連続3フレーム選出画面が
        # 続いた場合のみ回転する (揺れのたびにログが数秒単位で分割されていた)
        if scene == "selection":
            self._selection_streak += 1
        else:
            self._selection_streak = 0
        # field/watch/battle_hudは選出画面の背景演出でも誤分類され得るため、
        # 明確なUIを要求するコマンド系画面のみを「対戦があった」証拠とする
        if scene in ("command", "move_select"):
            self._battle_frames += 1

        # 回転条件: 選出3フレーム連続 + 現ファイルが30秒以上経過 + 実際に
        # 対戦シーンを含む。長い選出画面中の分類揺れによる再突入では、まだ
        # 対戦が始まっていないので回転しない (実運用で30秒超の分割を観測)
        # 対戦済みの証拠は累計5フレーム以上を要求する (選出画面の演出で
        # commandが2フレーム誤分類されただけでは回転しない)
        if (self._selection_streak == 3 and self._file is not None
                and self._battle_frames >= 5
                and time.time() - self._opened_ts >= 30.0):
            self._finalize(state.get("outcome"))

        if fired:
            # 原文はイベント化された行のみから対応付ける (イベント化されなかった
            # ログ行が混ざると原文と発火IDの対応がズレる: 実測「Oncwn」等)
            ev_texts = [e["text"] for e in state.get("events", [])
                        if e.get("event")]
            self._write({"type": "events", "scene": scene, "turn": state.get("turn"),
                         "fired": fired,
                         "texts": ev_texts[-len(fired):]})
            # 勝敗推定用: 各側の最後の交代時刻
            for f in fired:
                if f.startswith("switch_"):
                    self._last_switch[f.split("_")[1]] = time.time()

        # レート観測 (結果画面の表示から)。値が変わった時のみ記録する
        lr = state.get("last_rate")
        if lr and (self._rate_last is None
                   or lr["value"] != self._rate_last["value"]):
            self._write({"type": "rate", "value": lr["value"]})
            self._rate_last = dict(lr)
        elif lr and self._rate_last and lr["ts"] > self._rate_last["ts"]:
            self._rate_last = dict(lr)   # 同値の再観測でも時刻は進める

        # HP変化 (extractorsの_set_hpがsource="hp"でstate.eventsに積む) を
        # 専用レコードで記録し、技イベントとのダメージ対応付けを可能にする。
        # 手動修正 (source="manual") も誤認識分析用に専用レコードで残す
        for e in state.get("events", []):
            if e.get("ts", 0) <= self._hp_seen_ts:
                continue
            if e.get("source") == "hp":
                self._hp_seen_ts = e["ts"]
                self._write({"type": "hp", "turn": state.get("turn"),
                             "text": e["text"], "detail": e.get("detail")})
                # 勝敗推定用: HP0%到達の側を覚えておく
                det = e.get("detail") or {}
                if det.get("to") is not None and det["to"] <= 3.0 \
                        and det.get("side"):
                    self._last_zero = (det["side"], e["ts"])
            elif e.get("source") == "manual":
                self._hp_seen_ts = e["ts"]
                self._write({"type": "manual_fix", "turn": state.get("turn"),
                             "scene": scene,
                             "text": e["text"], "detail": e.get("detail")})

        # 選出中は進捗/選出セットの変化でも記録する (シーン遷移だけだと
        # 選出済み状態がログに残らず、選出認識の検証ができない)
        pick_key = None
        if scene in ("selection", "standby"):
            pick_key = (state.get("selection_picked"),
                        tuple(p.get("is_picked") for p in
                              state.get("player", {}).get("party", [])))
        if scene != self._prev_scene or \
                (pick_key is not None and pick_key != self._prev_pick_key):
            self._write({"type": "scene", "scene": scene, "turn": state.get("turn"),
                         "state": _compact_state(state)})
            self._prev_scene = scene
            self._prev_pick_key = pick_key

        # 勝敗が確定したら記録して閉じる
        if state.get("outcome") and not self._outcome_logged:
            self._write({"type": "outcome", "outcome": state["outcome"]})
            self._outcome_logged = True

    def on_advice(self, advice: dict, kind: str) -> None:
        slim = {k: v for k, v in advice.items() if k not in ("text",)}
        self._write({"type": "advice", "kind": kind, "advice": slim})
