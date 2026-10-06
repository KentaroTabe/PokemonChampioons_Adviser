"""対戦ログの自動記録 (JSONL)。

アドバイザー較正・報酬チューニング・振り返りの土台となるデータ収集層。
1対戦 = 1ファイル (logs/battles/battle_YYYYmmdd_HHMMSS.jsonl) に、
以下のレコードを時系列で追記する:

  {"t": ..., "type": "scene",   "scene": ..., "state": {...簡約状態...}}
  {"t": ..., "type": "events",  "fired": [...], "scene": ...}
  {"t": ..., "type": "advice",  "kind": "battle"|"selection", "advice": {...}, "advice_id": ..., "version_id": ...,
   "state_id": ..., "state": {...助言が見た簡約状態...}, "policy": {"selection": ..., "rl_loaded": ...}}
  {"t": ..., "type": "display", "advice_id": ..., "t_shown": ブラウザの表示時刻 (秒)}
  {"t": ..., "type": "version", ...advisor.versions.runtime_versions() (指定 Package / 実際に読んだモデルの sha / 退避理由)}
  {"t": ..., "type": "outcome", "outcome": "win"|"loss"|"unknown",
   ("inferred": true — 勝敗メッセージ取り逃し時の推定、"basis": 推定の根拠 (レートの増減 / ひんしの観測)、
    "revised_from": 後から読めたレートで推定し直したときの前の値、"corrected_from": 勝負の文言で訂正したときの前の値)}

2026-10-05 ②: 助言の行に advice_id と、助言が見た状態 (state) とその digest (state_id)、動いていた版 (version_id) を付け、
ブラウザが表示した時刻を display の行で別に残す。「どの版が、どの状態を見て、何を推奨し、いつ表示されたか」を 1 本で追うため
(tools/team_build/real_eval の trace と tools/advice_trace)。

プレイヤーが実際に選んだ行動は events の move_player_* / switch_player として
記録される (アドバイスとの突き合わせで採用率・成績を後段で分析できる)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import (BSS_PICK_COUNT, OUTCOME_LAST_ZERO_MAX_SEC, OUTCOME_ZERO_HP_PCT,
                                    RATE_INFER_MAX_DELTA, RATE_INFER_MIN_DELTA)
from tools.battle_outcome import rate_inference, text_outcome_basis, text_outcome_of

LOG_DIR = Path(__file__).resolve().parent / "logs" / "battles"
# 対戦終了を確定させるイベント (どれか 1 つで勝敗レコードを書く): ランク画面の文言 / リザルト画面のシーン分類 /
# 3体目のひんしの確定 (2026-09-16: 勝負文言・ランク文言の取り逃しでも終了を取れるように)
BATTLE_END_EVENTS = ("battle_end_rank", "battle_end_result", "battle_end_faint_confirmed")


def _compact_state(state: dict) -> dict:
    """ログ用の簡約状態 (イベント履歴を除き、パーティは主要フィールドのみ)"""
    def mon(p):
        d = {
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
        # 選出画面の推定 (確定ではない) の印。分析・実戦バンクは推定を「相手の 6 体」に数えない (2026-09-29)
        if p.get("species_guess"):
            d["guess"] = True
        return d

    return {
        "scene": state.get("scene"),
        "field": state.get("field"),
        "selection_picked": state.get("selection_picked"),
        "player": {
            "active": state["player"].get("active_index"),
            "remaining": state["player"].get("remaining"),
            "hazards": state["player"].get("hazards"),
            "screens": state["player"].get("screens"),
            "tailwind": state["player"].get("tailwind"),
            "party": [mon(p) for p in state["player"].get("party", [])],
        },
        "opponent": {
            "active": state["opponent"].get("active_index"),
            "remaining": state["opponent"].get("remaining"),
            "hazards": state["opponent"].get("hazards"),
            "screens": state["opponent"].get("screens"),
            "tailwind": state["opponent"].get("tailwind"),
            "party": [mon(p) for p in state["opponent"].get("party", [])],
        },
        "mega_used": state.get("mega_used"),
    }


EXPERIMENT_MARK = Path("logs") / ".experiment_package"   # 候補 Package の試用中はここに package_id


def state_digest(compact: dict, n: int = 12) -> str:
    """助言が見た簡約状態の digest (純粋)。同じ状態への助言は同じ id になる"""
    import hashlib
    return hashlib.sha1(json.dumps(compact, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:n]


def battle_source_labels() -> dict:
    """{"source": organic|recommended|experiment, "package_id", "dataset_kind": "real",
        "data_quality": "trusted"}。registry の production Package と現在の my_team を比べる"""
    labels = {"source": "organic", "package_id": None,
              "dataset_kind": "real", "data_quality": "trusted"}
    try:
        if EXPERIMENT_MARK.exists():
            pid = EXPERIMENT_MARK.read_text(encoding="utf-8").strip()
            if pid:
                labels.update(source="experiment", package_id=pid)
                return labels
        from tools.team_build.registry import Registry
        prod = Registry().production("package")
        if prod:
            want = set(prod.get("meta", {}).get("species") or [])
            from tools.evaluate_team import current_team_entries
            from advisor.my_team import registered_species_id
            from vision.normalize import NameResolver
            resolver = NameResolver()
            have = set()
            for ja in current_team_entries().keys():
                sid = registered_species_id(ja)
                if not sid:
                    r = resolver.resolve_species(ja, cutoff=0.9)
                    sid = r[1] if r else None
                if sid:
                    have.add(sid)
            if want and want == have:
                labels.update(source="recommended", package_id=prod["id"])
    except Exception:
        pass
    return labels


class BattleLogger:
    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self._file: Optional[Path] = None
        self._prev_scene: Optional[str] = None
        self._prev_pick_key = None
        self._outcome_logged = False
        self._outcome_value = None  # 記録済みの勝敗 (勝負文言と食い違えば訂正の行を足す)
        self._hp_seen_ts = 0.0   # 記録済みHP変化イベントの最終時刻
        self._last_seq = None       # 前フレームの対戦世代番号 (battle_seq)
        self._opened_ts = 0.0       # 現在のログファイルを開いた時刻
        self._last_zero = None      # 最後にHP0%を観測した側 (side, ts)
        self._last_switch = {}      # 各側の最後の交代時刻 {side: ts}
        self._fainted_last = (0, 0)  # 直前フレームの (自分, 相手) ひんし数
        self._rate_open = None      # この対戦に入る前のレート {"value","ts"}
        self._rate_last = None      # 直近観測レート (ファイルを跨いで保持)
        # レートの増減をこの対戦の勝敗に帰属できるかの文脈 (tools.battle_outcome.rate_inference。2026-10-06 第18回:
        # ランク画面の最初の読みは対戦前の値のことがあり、直前の対戦の増減をこの対戦の勝敗と記録した)
        self._rate_reads: list = []          # この対戦のファイルを開いてから読めたレート (値が変わるたびに 1 つ)
        self._rate_open_fresh = False        # _rate_open は直前の対戦の終了画面で読めた値か
        self._rate_open_post = False         # _rate_open は直前の対戦の「後」の値だと分かっているか
        self._prev_outcome = None            # 直前の対戦の勝敗 (文言などで確定したものだけ)
        self._next_rate_ctx = (False, False, None)   # 次の対戦に渡す (fresh, post, 確定した勝敗)。_finalize が作る
        self._outcome_strong = False         # 記録済みの勝敗は確定値か (推定・不明なら False → 後から読めたレートで更新する)
        self._outcome_info = None            # 記録済みの勝敗の表示用 {"outcome","inferred","basis","basis_text"}
        self._revision = None                # 推定を更新した直後の情報 (server が 1 回だけ通知に使う)
        self._version = None        # この対戦の version 行 (advisor.versions.runtime_versions)
        self._advice_seq = 0        # 助言 ID の連番 (ファイル内で一意)
        self._advice_files: dict = {}   # 助言 ID → その助言を書いた対戦ログ (表示の行を助言の対戦に帰属させる。2026-10-06)

    # ------------------------------------------------------------------
    def _open_new(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        name = time.strftime("battle_%Y%m%d_%H%M%S.jsonl")
        path = self.log_dir / name
        n = 2
        while path.exists():   # 同一秒内の回転でも別ファイルにする
            path = self.log_dir / name.replace(".jsonl", f"_{n}.jsonl")
            n += 1
        self._file = path
        self._opened_ts = time.time()
        self._outcome_logged = False
        self._outcome_value = None
        self._outcome_strong = False
        self._outcome_info = None
        self._revision = None
        self._rate_open = self._rate_last   # この対戦に入る時点のレート
        self._rate_open_fresh, self._rate_open_post, self._prev_outcome = self._next_rate_ctx
        self._next_rate_ctx = (False, False, None)
        self._rate_reads = []
        print(f"[battle_log] 新しい対戦ログ: {self._file.name}")
        # 由来ラベル (2026-09-06 構築システムの安全装置): 実戦ログは dataset_kind=real。
        # source は production Package の構築を使っていれば recommended、候補の試用なら
        # experiment、それ以外は organic。学習ローダはこの行で実ログと合成ログを混ぜない
        try:
            self._write({"type": "session", **battle_source_labels()})
        except Exception as e:      # ラベル付けの失敗で対戦ログを止めない
            print(f"[battle_log] 由来ラベル付け失敗: {e}")
        # 動いている版 (指定 Package / 実際に読んだ選出モデルと行動方策の sha / 構築の版 / 退避理由)。2026-10-05 ②
        try:
            from advisor.versions import runtime_versions
            self._version = runtime_versions(refresh=True)
            self._write({"type": "version", **self._version})
        except Exception as e:
            print(f"[battle_log] 版の記録に失敗: {e}")
            self._version = None

    def _write(self, record: dict) -> None:
        if self._file is None:
            self._open_new()
        record["t"] = round(time.time(), 2)
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _rate_inference(self) -> Optional[dict]:
        """この対戦で読めたレートから勝敗を推定できるか (tools.battle_outcome.rate_inference)。できなければ None"""
        ro = self._rate_open
        return rate_inference(self._rate_reads, ro["value"] if ro else None, self._rate_open_fresh,
                              self._rate_open_post, self._prev_outcome, RATE_INFER_MIN_DELTA, RATE_INFER_MAX_DELTA)

    def _infer_outcome_detail(self) -> Optional[dict]:
        """勝敗メッセージを取り逃した場合のフォールバック推定。戻り値 {"outcome", "basis" (rate / last_zero / fainted),
        "basis_text" (表示用の根拠)}、推定できなければ None。

        最後にHP0%を観測した側が、その後交代せずに対戦が終わっていれば
        その側の負けとみなす (実戦: 勝敗表示が数秒で流れてOCRが取り逃し、
        outcome=unknown でローテーションした)。推定は直近3分の観測に限る。
        """
        # 1. レートの増減 (この対戦の増減だと言える場合だけ。ランク画面の最初の読みは対戦前の値のことがある)
        ri = self._rate_inference()
        if ri:
            return {"outcome": ri["outcome"], "basis": "rate",
                    "basis_text": f"レート {ri['from']:.1f} → {ri['to']:.1f}"}
        # 2. 最後にHP0%を観測した側の負け (その後交代していない場合)
        if self._last_zero:
            side, ts = self._last_zero
            if time.time() - ts <= OUTCOME_LAST_ZERO_MAX_SEC and \
                    self._last_switch.get(side, 0.0) <= ts:
                who = "自分" if side == "player" else "相手"
                return {"outcome": "loss" if side == "player" else "win", "basis": "last_zero",
                        "basis_text": f"最後にひんしを観測したのは{who}側で、その後の交代なし"}
        # 3. 選出3体のひんし数 (勝敗メッセージもレートもHP0イベントも
        #    取れなかった場合の最終手段。リザルト画面を飛ばして次戦の選出へ
        #    進むと unknown になっていた: 2026-08-20 第5回で9戦中6戦)
        my_f, opp_f = self._fainted_last
        if (opp_f >= BSS_PICK_COUNT) != (my_f >= BSS_PICK_COUNT):
            return {"outcome": "win" if opp_f >= BSS_PICK_COUNT else "loss", "basis": "fainted",
                    "basis_text": f"ひんしの数 自分 {my_f} / 相手 {opp_f}"}
        return None

    def _infer_outcome(self) -> Optional[str]:
        d = self._infer_outcome_detail()
        return d["outcome"] if d else None

    def _log_outcome(self, outcome: Optional[str]) -> None:
        """勝敗の行を書く。outcome (勝負の文言などで確定した値) が無ければ推定し、根拠を添える (推定もできなければ unknown)"""
        if outcome:
            rec = {"type": "outcome", "outcome": outcome}
            info = {"outcome": outcome, "inferred": False, "basis": None, "basis_text": None}
        else:
            d = self._infer_outcome_detail()
            rec = {"type": "outcome", "outcome": d["outcome"] if d else "unknown"}
            if d:
                rec.update(inferred=True, basis=d["basis"], basis_text=d["basis_text"])
            info = {"outcome": rec["outcome"], "inferred": bool(d), "basis": d["basis"] if d else None,
                    "basis_text": d["basis_text"] if d else None}
        self._write(rec)
        self._outcome_logged = True
        self._outcome_value = rec["outcome"]
        self._outcome_strong = bool(outcome)
        self._outcome_info = info

    def _revise_by_rate(self) -> None:
        """勝敗を推定・不明で記録した後にレートが読めたら推定し直し、変わったら更新の行を足す (読み手は最後の outcome 行を採る)。
        2026-10-06 第18回の 9 戦目: ランク画面の検出時に読めたのは対戦前の値で、対戦後の値は 34 秒後に読めた"""
        if not self._outcome_logged or self._outcome_strong or self._file is None:
            return
        ri = self._rate_inference()
        if not ri or ri["outcome"] == self._outcome_value:
            return
        text = f"レート {ri['from']:.1f} → {ri['to']:.1f}"
        prev = self._outcome_value
        self._write({"type": "outcome", "outcome": ri["outcome"], "inferred": True, "basis": "rate",
                     "basis_text": text, "revised_from": prev})
        self._outcome_value = ri["outcome"]
        self._outcome_info = {"outcome": ri["outcome"], "inferred": True, "basis": "rate", "basis_text": text,
                              "revised_from": prev}
        self._revision = dict(self._outcome_info)

    def outcome_info(self) -> Optional[dict]:
        """この対戦の記録済みの勝敗 {"outcome", "inferred", "basis", "basis_text"} (未記録なら None)。終了の通知に使う"""
        return dict(self._outcome_info) if self._outcome_info else None

    def pop_revision(self) -> Optional[dict]:
        """推定を更新した直後に 1 回だけ返す (server が助言欄に通知する)"""
        rev, self._revision = self._revision, None
        return rev

    def _finalize(self, outcome: Optional[str]) -> None:
        if self._file is not None and not self._outcome_logged:
            self._log_outcome(outcome)
        # 次の対戦に渡すレートの文脈: この対戦でレートが読めたか、最後の読みが対戦後の値だと分かっているか (2 つ以上読めた /
        # 増減をこの対戦に帰属できた)、この対戦の勝敗が確定しているか
        had_reads = bool(self._rate_reads)
        self._next_rate_ctx = (had_reads, had_reads and self._rate_inference() is not None,
                               self._outcome_value if (self._outcome_strong and self._outcome_value in ("win", "loss"))
                               else None)
        self._rate_reads = []
        self._outcome_value = None
        self._outcome_strong = False
        self._outcome_info = None
        self._revision = None
        self._file = None
        self._prev_scene = None
        self._hp_seen_ts = 0.0
        self._last_zero = None
        self._last_switch = {}
        self._fainted_last = (0, 0)

    # ------------------------------------------------------------------
    def on_frame(self, state: dict, fired: list) -> None:
        """毎フレーム呼び出し。シーン変化・イベント・勝敗を記録する"""
        scene = state.get("scene")

        # 新しい対戦の開始検知: パイプラインの reset_battle が上げる世代番号
        # (battle_seq) の変化のみを根拠にファイルを切り替える。
        # 以前はロガー側が独自の選出画面ストリークで分割しており、状態リセット
        # と分割がズレて「1ファイルに複数対戦」「状態は別対戦のまま」が起きた
        # (2026-08-11の実測)。分割の根拠を状態リセットと同一にする
        seq = state.get("battle_seq")
        if seq is not None:
            if self._last_seq is None:
                self._last_seq = seq   # 起動直後は基準を記録するだけ (回転しない)
            elif seq != self._last_seq:
                self._last_seq = seq
                self._finalize(state.get("outcome"))

        # レート観測 (結果画面の表示から)。値が変わった時のみ記録する。
        # ⚠ fired の処理より先に更新する: battle_end_rank (ランク画面での
        # 対戦終了確定) と同一フレームで捕捉されたレートを、勝敗推定
        # (_infer_outcome のレート増減) が同フレーム内で参照できるように
        lr = state.get("last_rate")
        if lr and (self._rate_last is None
                   or lr["value"] != self._rate_last["value"]):
            self._write({"type": "rate", "value": lr["value"]})
            self._rate_last = dict(lr)
            self._rate_reads.append(float(lr["value"]))
            self._revise_by_rate()   # 勝敗を推定・不明で記録した後に読めた値なら、推定し直す
        elif lr and self._rate_last and lr["ts"] > self._rate_last["ts"]:
            self._rate_last = dict(lr)   # 同値の再観測でも時刻は進める

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
            # ランク画面 = 対戦終了のキー (2026-08-21 ユーザー提案)。
            # 終局時点のひんし数・レート増減が揃っているここで勝敗を確定する
            # (次戦の選出まで待つと状態がリセットされ推定材料が失われる)
            if any(f in fired for f in BATTLE_END_EVENTS) and not self._outcome_logged:
                self._log_outcome(state.get("outcome"))
            # 勝負の文言は最も強い根拠: 先に記録した勝敗 (3 体目のひんしからの確定等) と食い違えば訂正の行を足す
            # (読み手は最後の outcome 行を採る。2026-09-29 第17回 15:53: 誤読の 7 体目で「負け」と記録した後に
            # 「勝負に勝った」を読んだが、記録は負けのままだった)
            text_out = text_outcome_of(fired)
            if self._outcome_logged and text_out and text_out != self._outcome_value:
                basis = text_outcome_basis(fired)
                self._write({"type": "outcome", "outcome": text_out,
                             "corrected_from": self._outcome_value, "basis": basis})
                self._outcome_value = text_out
                self._outcome_info = {"outcome": text_out, "inferred": False, "basis": basis, "basis_text": None}
            if self._outcome_logged and text_out and text_out == self._outcome_value:
                self._outcome_strong = True   # 文言で裏づけられた (推定で記録した値でも、以後レートで動かさない)

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
                if det.get("to") is not None and det["to"] <= OUTCOME_ZERO_HP_PCT \
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
            self._log_outcome(state["outcome"])

        # 勝敗推定用: 両側のひんし数を毎フレーム控える。ローテーションの
        # フレームでは state が次戦へリセット済みのため、_finalize は
        # ここで控えた「前フレーム = 前戦最終盤面」の値を参照する
        self._fainted_last = (
            sum(1 for p in state.get("player", {}).get("party", [])
                if p.get("status") == "fainted"),
            sum(1 for p in state.get("opponent", {}).get("party", [])
                if p.get("status") == "fainted"))

    def on_advice(self, advice: dict, kind: str, state: Optional[dict] = None) -> str:
        """助言の記録。advice に advice_id / t_gen を書き込み (ブラウザが表示の確認に使う)、助言が見た簡約状態とその digest、
        動いていた版の id、実際に使った選出モデルの経路 (model_pick.model: experiment:<id> / deployed) と RL の読み込み状態を残す。
        戻り値 advice_id"""
        if self._file is None:
            self._open_new()
        self._advice_seq += 1
        aid = f"{self._file.stem[7:]}-{self._advice_seq:04d}"      # battle_YYYYmmdd_HHMMSS → YYYYmmdd_HHMMSS-0001
        self._advice_files[aid] = self._file
        if len(self._advice_files) > 2000:
            self._advice_files.pop(next(iter(self._advice_files)))
        advice["advice_id"] = aid
        advice["t_gen"] = round(time.time(), 2)
        slim = {k: v for k, v in advice.items() if k not in ("text",)}
        rec = {"type": "advice", "kind": kind, "advice": slim, "advice_id": aid,
               "version_id": (self._version or {}).get("version_id")}
        if state is not None:
            try:
                compact = _compact_state(state)
                rec["state"] = compact
                rec["state_id"] = state_digest(compact)
                rec["turn"] = state.get("turn")
            except Exception:
                pass
        try:
            from advisor.versions import rl_loaded_now
            mp = (advice.get("model_pick") or {}) if kind == "selection" else {}
            rec["policy"] = {"selection": (mp.get("model") if kind == "selection" else None),
                             "primary": advice.get("primary") if kind == "selection" else None,
                             # ◎ がモデルのとき、登録チームで学習済みか (2026-10-06: 未学習の配布版も ◎ になるので層別に要る)
                             "model_trained": (advice.get("model_trained") if kind == "selection" else None),
                             "rl_loaded": rl_loaded_now() if kind == "battle" else None}
        except Exception:
            pass
        self._write(rec)
        return aid

    def on_display(self, advice_id: str, t_shown: Optional[float], kind: Optional[str] = None, hidden: Optional[bool] = None) -> None:
        """ブラウザが助言を表示した時刻 (ブラウザの時計、秒)。生成時刻 (advice の t_gen) と分けて残す (受入条件 2)。
        表示の行は**助言を書いた対戦のファイル**に書く (2026-10-06: タブが隠れていると表示の確認が止まり、見えた時にまとめて届くので、
        前の対戦の助言の表示が今の対戦のファイルに混ざっていた)。hidden = タブが隠れていて描画されずに送られた (表示とは数えない)"""
        if not advice_id:
            return
        target = self._advice_files.get(str(advice_id))
        if target is None and self._file is None:
            return
        rec = {"type": "display", "advice_id": str(advice_id), "t_shown": (round(float(t_shown), 3) if t_shown is not None else None)}
        if kind:
            rec["kind"] = kind
        if hidden is not None:
            rec["hidden"] = bool(hidden)
        if target is not None and target != self._file:
            rec["attributed"] = "advice_battle"
            rec["t"] = round(time.time(), 2)
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return
        self._write(rec)
