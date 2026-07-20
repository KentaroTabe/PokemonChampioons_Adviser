"""対戦ログの自動記録 (JSONL)。

アドバイザー較正・報酬チューニング・振り返りの土台となるデータ収集層。
1対戦 = 1ファイル (logs/battles/battle_YYYYmmdd_HHMMSS.jsonl) に、
以下のレコードを時系列で追記する:

  {"t": ..., "type": "scene",   "scene": ..., "state": {...簡約状態...}}
  {"t": ..., "type": "events",  "fired": [...], "scene": ...}
  {"t": ..., "type": "advice",  "kind": "battle"|"selection", "advice": {...}}
  {"t": ..., "type": "outcome", "outcome": "win"|"loss"|"unknown"}

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
        self._outcome_logged = False
        self._hp_seen_ts = 0.0   # 記録済みHP変化イベントの最終時刻
        self._selection_streak = 0  # 選出画面が連続何フレーム続いているか
        self._opened_ts = 0.0       # 現在のログファイルを開いた時刻
        self._battle_seen = False   # 現在のファイルに対戦シーンが含まれるか

    # ------------------------------------------------------------------
    def _open_new(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        name = time.strftime("battle_%Y%m%d_%H%M%S.jsonl")
        self._file = self.log_dir / name
        self._opened_ts = time.time()
        self._outcome_logged = False
        print(f"[battle_log] 新しい対戦ログ: {self._file.name}")

    def _write(self, record: dict) -> None:
        if self._file is None:
            self._open_new()
        record["t"] = round(time.time(), 2)
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _finalize(self, outcome: Optional[str]) -> None:
        if self._file is not None and not self._outcome_logged:
            self._write({"type": "outcome", "outcome": outcome or "unknown"})
            self._outcome_logged = True
        self._file = None
        self._prev_scene = None
        self._hp_seen_ts = 0.0
        self._battle_seen = False

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
            self._battle_seen = True

        # 回転条件: 選出3フレーム連続 + 現ファイルが30秒以上経過 + 実際に
        # 対戦シーンを含む。長い選出画面中の分類揺れによる再突入では、まだ
        # 対戦が始まっていないので回転しない (実運用で30秒超の分割を観測)
        if (self._selection_streak == 3 and self._file is not None
                and self._battle_seen
                and time.time() - self._opened_ts >= 30.0):
            self._finalize(state.get("outcome"))

        if fired:
            self._write({"type": "events", "scene": scene, "turn": state.get("turn"),
                         "fired": fired,
                         "texts": [e["text"] for e in state.get("events", [])[-len(fired):]]})

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
            elif e.get("source") == "manual":
                self._hp_seen_ts = e["ts"]
                self._write({"type": "manual_fix", "turn": state.get("turn"),
                             "scene": scene,
                             "text": e["text"], "detail": e.get("detail")})

        if scene != self._prev_scene:
            self._write({"type": "scene", "scene": scene, "turn": state.get("turn"),
                         "state": _compact_state(state)})
            self._prev_scene = scene

        # 勝敗が確定したら記録して閉じる
        if state.get("outcome") and not self._outcome_logged:
            self._write({"type": "outcome", "outcome": state["outcome"]})
            self._outcome_logged = True

    def on_advice(self, advice: dict, kind: str) -> None:
        slim = {k: v for k, v in advice.items() if k not in ("text",)}
        self._write({"type": "advice", "kind": kind, "advice": slim})
