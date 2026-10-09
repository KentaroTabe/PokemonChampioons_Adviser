"""対戦ログの自動記録 (JSONL)。

アドバイザー較正・報酬チューニング・振り返りの土台となるデータ収集層。
1対戦 = 1ファイル (logs/battles/battle_YYYYmmdd_HHMMSS.jsonl) に、
以下のレコードを時系列で追記する:

  {"t": ..., "type": "scene",   "scene": ..., "state": {...簡約状態..., "hp_reject": {"player": ..., "opponent": ...}}}
    (hp_reject は 2026-10-09 に足した欄: 側ごとの最後に捨てた HP の読み。vision.state の HP_REJECT_REASONS の注記)
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

2026-10-07 段 0 (docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2。欄・行を足すだけで、既存の行の意味は変えない。表示する助言も変えない):
  advice 行に "hp_stale": {"player": 秒|null, "opponent": 秒|null} (助言時点で場の HP が最後に実際に読めてからの秒数)。
  {"type": "selection_record", "advice_id", "candidates", "opp_pick_pred"} 選出の助言ごとの記録用の欄 (advisor.selection_record)。
    server が助言を送った後に計算して書く (表示を遅らせない)。読み手は advice_id で選出の advice 行に結ぶ
  {"type": "frames", "received", "processed", "dropped", "hidden", "hidden_ratio", "span_sec", "recv_fps", "proc_fps", "final"}
    この対戦のファイルを開いてからのフレームの件数。fps は対戦の時間の幅 (ファイルを開いた時刻 → 最後に受信した時刻) で割る
  {"type": "roster_change", "slot", "from", "from_ja", "from_guess", "from_prob", "from_score", "to", "to_ja", "to_guess", "basis"}
    相手の枠の種の置き換え・消失 (basis: manual / field / cleared / selection_guess / name_read)
  {"type": "guess_confirm", "slot", "species", "ja", "prob", "score", "t_guess", "auto_accept", "sure", "revealed", "verdict"}
    選出画面の推定 (species_guess) ごとに、推定した時点の確率 (タイプからの候補の事前確率) と、後に場で判明した種
    (verdict: match / mismatch / unrevealed)。終了時に書く (読み手は (slot, species, t_guess) の最後の行を採る)
  {"type": "opp_picks", "slots": [{"slot", "species", "ja", "guess", "appeared", "pick_status"}], "n_appeared", "complete", ...}
    相手の選出ラベル 3 値 (picked_confirmed / unpicked_confirmed / unknown) と場に出たか。終了時に書く (読み手は最後の行を採る)
  manual_fix 行に "fix_id"、{"type": "manual_fix_overwritten", "fix_id", "label", ..., "manual_value", "new_value", "overwritten_at"}
    手入力の訂正が後の推定で別の値に変わった時刻 (最初の 1 回)
  {"type": "decision", "advice_id", "turn", "t_gen", "t_shown", "t_decided", "scene_from", "scene_to", "action", "match", "best"}
    助言のあと、決定画面 (command / move_select) から解決側の場面 (battle_hud / field) へ移った時刻と、その後の行動が第一候補と
    一致したか (行動が読めなければ null)
  {"type": "frame_burst", "dir", "t_start", "seconds"} 連続フレームの保存を始めた (server、DEBUG_DUMP_FRAMES=1 のときだけ)

2026-10-07 段 0 の実機確認で足した欄・行 (既存の欄の意味は変えない。hidden_ratio だけ下記のとおり分母を変えた):
  frames 行に "visible" / "unknown" (可視状態を通知した接続から見える状態で受信 / 通知の無い接続から受信)。
    hidden_ratio は既知 (hidden + visible) に対する比 (既知が 0 なら null)。visible の無い古い server から来たときは
    visible / unknown を null にし、hidden_ratio は従来どおり受信に対する比 (読み手は visible が null かで区別する)
  {"type": "client", "sid", "hello", "html_version", "features", "served_version", "stale", "visibility", "user_agent",
   "served_commit"} 助言ページの版と対応機能 (client_hello、client_state.py)。対戦ファイルを開いたとき (version 行の直後、
    接続中の全クライアント) と、対戦中に hello が来た (または待っても来なかった: hello=false) とき
  {"type": "visibility", "sid", "hidden", "source": "page_visibility"|"client_hello", "t_notified"} 可視状態の通知ごと。
    ファイルを開いたときは、可視状態の分かっている接続の最新の状態を 1 行ずつ書く
  frame_burst 行に "reason" ("scene:<場面>" / "timeout")・"battle_seq"・"battle_index"。保存を始めなかった対戦は終わりに
    {"type": "frame_burst", "skipped": true, "reason": no_start_scene|count_exhausted|not_every|disabled, "battle_seq", "battle_index"}

2026-10-09 ④ 相手の枠と観測情報を失わない (欄・値を足すだけ。既存の欄・値の意味は変えない):
  roster_change 行に、枠の統合 (link_active_to_party) / 対応待ちの個体の解消 (resolve_pending) のときだけ
    "merged_from" (統合元の枠の番号。対応待ちは PARTY_SIZE 以降)、"merged_to"、"merge_kind" (placeholder / pending)、
    "moved" ({"hp", "revealed_moves", "status"} 移した観測)、"replaced" (統合先の枠にあった種・推定・スコア・タイプ)、
    "source_cleared" (満枠で統合元の枠を未特定に戻した)、"t_first_seen" (対応待ちになった時刻)。種の無かった枠への統合は
    from が null の roster_change 行を 1 つ書く
  対応待ちのまま終わった場の個体: 終了時に {"type": "roster_change", "slot": null, "to", "to_ja", "unresolved": true,
    "candidates" (形態の候補), "t_first_seen", "moved", "final"}。読み手は t_first_seen ごとに最後の行を採る
  guess_confirm の verdict に superseded (推定の枠が統合で消えた・枠の番号がずれた) / unresolved (未判明だが対応待ちのまま
    終わった個体がいる) を足した。推定の的中率の集計は、この 2 値を分母から除く (match / mismatch だけで率を出す)
  manual_fix 行に、種の手動確定のとき "applied" (入れたか) と、入れなかったときの "reason"
  scene 行の相手の枠に "pending": true (対応待ちの個体) と "candidates" (形態が決まっていない種の候補)

2026-10-09 タイプの読み直し (fix/type-recognition。欄・値を足すだけ):
  roster_change 行に、相手枠のタイプを訂正した (選出画面・様子を見る画面の読みが安定して入っていたタイプと違った) とき
    "types_from" / "types_to" / "reason": "type_reread" / "type_source" ("selection" / "watch")。訂正で取り消した推定は
    from / from_ja / from_guess (取り消して種が無くなれば to は null、同じフレームで推定し直せば to にその種)。種の無い枠の
    訂正も、from / to が null の行を 1 つ書く。人が対応待ちの個体の枠を指定した統合は basis "manual" の roster_change
    (merged_from / merged_to つき)

2026-10-09 自分の HP のバー推定 (vision.my_hp_estimate。欄を足すだけ。既存の欄の意味は変えない):
  hp 行に、推定値の変化のときだけ "source": "bar" と "estimated": true (detail にも同じ欄)
  scene 行の state の個体に、HP が推定値のときだけ "hp_estimated": true と "hp_source" ("bar" / 技イベント由来は null)。
    助言の行の state と state_id の digest には足さない (scene 行だけ)
  scene 行の hp_reject.player に、推定値を入れた後は "estimated_from_bar": true と "estimate": [現在, 最大]
    (直前の棄却が推定で埋まった印)、バーがほぼ 0 のときは "faint_suspect": true (ひんしの疑い。確定ではない)
  advice 行に、自分の場の個体の HP が推定値のときだけ "context": {"my_hp_estimated": true, "my_hp_source": ...}

2026-10-09 HP の取り込み経路の記録 (fix/hp-paths。行と欄を足すだけ。既存の欄の意味は変えない):
  {"type": "my_hp_trace", "reason": "reject_streak"|"battle_end", "battle_seq", "n", "rows": [...]} 自分の HP の読みの経過
    (vision.state の my_hp_trace、直近 MY_HP_TRACE_LEN 件)。自分の HP の棄却が MY_HP_TRACE_DUMP_STREAK 回続いたときと、
    対戦の終わりに書く (1 対戦で最大 MY_HP_TRACE_MAX_DUMPS 行)。rows の各行は {t, scene, source ("hud"/"field"), name_text,
    hud_species, active_species, hp_text, frac, split_guess, known_max, bar, decision, reason} と、_set_hp に渡した読みの
    new / stable_count / since_commit、field 経路の HUD の判定の opp_banner / my_bar_px、バー推定の est_decision / est_reason。
    scene 行の state には載せない
  scene 行の state の個体 (両側) に "hp_read_ts" (HP を最後に実際に読んで確定した時刻)。助言の行の state と digest には足さない
  scene 行 (scene が watch のとき) の state に "watch_opp_rows": 様子見画面の右列の行ごとの読み [{row, hp_text, pct,
    species (色照合で同定した種族 id), method ("color"), score, written (書いたか / 書かなかった理由), slot}]
  hp_reject.opponent の reason に "watch_right_big_increase" (様子見画面の右列で、交代の文言なしに WATCH_OPP_BIG_INCREASE を
    超えて増えた読み。書かずに残す。row / species / method / score / hp_before つき)
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (BSS_PICK_COUNT, DECISION_ACTION_WAIT_SEC, DECISION_CONFIRM_FRAMES,
                                    OUTCOME_LAST_ZERO_MAX_SEC, OUTCOME_ZERO_HP_PCT, PARTY_SIZE, RATE_INFER_MAX_DELTA,
                                    RATE_INFER_MIN_DELTA, SELECTION_GUESS_SURE_PROB, SELECTION_PRIOR_AUTO_ACCEPT)
from tools.battle_outcome import rate_inference, text_outcome_basis, text_outcome_of
from vision.state import roster_slots
from vision.scenes import (SCENE_BATTLE_HUD, SCENE_COMMAND, SCENE_FIELD, SCENE_FIELD_CHECK, SCENE_MOVE_SELECT,
                           SCENE_STANDBY, SCENE_WATCH)

LOG_DIR = Path(__file__).resolve().parent / "logs" / "battles"
# 対戦終了を確定させるイベント (どれか 1 つで勝敗レコードを書く): ランク画面の文言 / リザルト画面のシーン分類 /
# 3体目のひんしの確定 (2026-09-16: 勝負文言・ランク文言の取り逃しでも終了を取れるように)
BATTLE_END_EVENTS = ("battle_end_rank", "battle_end_result", "battle_end_faint_confirmed")

# decision 行の場面の区分 (tools/decision_audit の区分と同じ): 決定画面 / 決定中の情報確認 (決定は開いたまま) / 解決側の場面
DECISION_SCENES = (SCENE_COMMAND, SCENE_MOVE_SELECT)
DECISION_INFO_SCENES = (SCENE_WATCH, SCENE_FIELD_CHECK, SCENE_STANDBY)
DECISION_RESOLVE_SCENES = (SCENE_BATTLE_HUD, SCENE_FIELD)
# 相手が場に出たとみなす場面 (tools/analyze_battles・party_improvements の _BATTLE_SCENES と同じ)
OPP_APPEAR_SCENES = (SCENE_COMMAND, SCENE_MOVE_SELECT, SCENE_WATCH, SCENE_FIELD_CHECK, SCENE_BATTLE_HUD, SCENE_FIELD)
# 選出ラベル 3 値
PICK_CONFIRMED = "picked_confirmed"
PICK_UNPICKED = "unpicked_confirmed"
PICK_UNKNOWN = "unknown"
# guess_confirm の verdict に足した値 (2026-10-09 ④。既存の match / mismatch / unrevealed の意味は変えない)
GUESS_SUPERSEDED = "superseded"   # 推定の枠が統合で消えた (枠の番号がずれた)
GUESS_UNRESOLVED = "unresolved"   # 未判明だが、対応待ちのまま終わった場の個体がいる
# 枠の統合・対応待ちの解消のイベント (vision/extractors.link_active_to_party / vision/state.BattleStateV2.resolve_pending)
ROSTER_MERGE_EVENTS = ("roster_merge", "roster_pending_resolved")


# ------------------------------------------------------------------ 段 0 の行の純粋関数 (2026-10-07)
def _base_sid(sid: Optional[str]) -> Optional[str]:
    """メガ形態の id を基本種に丸める (tools.real_opponents.base_species と同じ規則)"""
    if not sid:
        return None
    for suf in ("megax", "megay", "mega"):
        if sid.endswith(suf) and len(sid) > len(suf):
            return sid[: -len(suf)]
    return sid


def hp_stale_of(state: Optional[dict], now: float) -> dict:
    """助言時点で、自分 / 相手の場の個体の HP が最後に実際に読めてから (hp_read_ts) の秒数。読めていなければ None (純粋)"""
    out = {}
    for side in ("player", "opponent"):
        sd = (state or {}).get(side) or {}
        idx, party = sd.get("active_index"), sd.get("party") or []
        ts = party[idx].get("hp_read_ts") if isinstance(idx, int) and 0 <= idx < len(party) else None
        out[side] = round(max(0.0, float(now) - float(ts)), 2) if ts else None
    return out


FRAME_COUNT_KEYS = ("received", "processed", "dropped", "hidden")
# 可視状態の累積件数 (2026-10-07 実機確認: 接続ごとの可視状態。通知の無い接続からのフレームは unknown)。古い server には無い
FRAME_VISIBILITY_KEYS = ("visible", "unknown")


def _count_delta(start: Optional[dict], end: Optional[dict], k: str) -> int:
    return max(0, int((end or {}).get(k) or 0) - int((start or {}).get(k) or 0))


def frames_row(start: dict, end: dict, t_start: Optional[float]) -> dict:
    """フレームの累積件数 (server の受信 / 処理 / 破棄 / 隠れたページから受信 / 見えるページから受信 / 可視状態の通知なし) の差から
    frames 行の中身を作る (純粋)。受信 fps は対戦の時間の幅 (t_start → 最後に受信した時刻 end["last_recv_ts"]) で割る
    (送信 10 fps の仮定は使わない)。hidden_ratio は既知 (hidden + visible) に対する比 (既知が 0 なら None)。
    visible を持たない古い server の件数なら visible / unknown は None、hidden_ratio は従来どおり受信に対する比"""
    d = {k: _count_delta(start, end, k) for k in FRAME_COUNT_KEYS}
    t_end = (end or {}).get("last_recv_ts")
    span = float(t_end) - float(t_start) if (t_end and t_start and float(t_end) > float(t_start)) else None
    if all(k in (end or {}) for k in FRAME_VISIBILITY_KEYS):
        for k in FRAME_VISIBILITY_KEYS:
            d[k] = _count_delta(start, end, k)
        known = d["hidden"] + d["visible"]
        d["hidden_ratio"] = round(d["hidden"] / known, 3) if known else None
    else:
        d["visible"] = d["unknown"] = None
        d["hidden_ratio"] = round(d["hidden"] / d["received"], 3) if d["received"] else None
    d["span_sec"] = round(span, 2) if span else None
    d["recv_fps"] = round(d["received"] / span, 2) if span else None
    d["proc_fps"] = round(d["processed"] / span, 2) if span else None
    return d


def opp_slots_of(state: Optional[dict]) -> dict:
    """相手の枠 {slot: {"species", "ja", "guess", "score", "types"}} (PARTY_SIZE 枠まで、対応待ちを除く。純粋)"""
    party = (((state or {}).get("opponent") or {}).get("party") or [])
    return {i: {"species": p.get("species_id"), "ja": p.get("species_ja"), "guess": bool(p.get("species_guess")),
                "score": p.get("guess_score"), "types": list(p.get("types") or [])}
            for i, p in roster_slots(party)}


def _slot_identity(s: Optional[dict]):
    return (s or {}).get("species") or (s or {}).get("ja")


def opp_appeared_now(state: Optional[dict], scene: Optional[str]) -> list:
    """このフレームで場に出ていた (= 選出された) と分かる相手 [(slot, species_id)] (純粋)。
    従来の読み手 (analyze_battles) と同じ規則: 対戦の場面で、場の枠か HP が読めている枠。選出画面の推定 (guess) と
    対応待ちの個体 (roster_slots の外。roster_change の unresolved 行で別に残す) は数えない"""
    if scene not in OPP_APPEAR_SCENES:
        return []
    opp = (state or {}).get("opponent") or {}
    act = opp.get("active_index")
    out = []
    for i, p in roster_slots(opp.get("party")):
        if not p.get("species_id") or p.get("species_guess"):
            continue
        if i == act or p.get("hp_percent") is not None:
            out.append((i, p["species_id"]))
    return out


def label_opp_picks(slots: list, appeared, pick_count: int = BSS_PICK_COUNT) -> dict:
    """相手の選出ラベル 3 値 (純粋)。slots = 最終の枠 [{"slot", "species", "ja", "guess"}]、appeared = 場に出た種 id の集合。
    場に出た = picked_confirmed。場に出た種がちょうど pick_count 体なら、残りは unpicked_confirmed。それ未満 (または誤読で
    pick_count を超えた) なら残りは unknown。場に出たのに枠に無い種 (枠の置き換え) は slot=None の行で足す。
    (従来の「6 体 − 場に出た = 選出外」は、選出されたが場に出なかった個体を選出外にしてしまう)"""
    app = {_base_sid(s) for s in (appeared or []) if s}
    rows, seen = [], set()
    for s in slots or []:
        b = _base_sid(s.get("species"))
        if b:
            seen.add(b)
        rows.append({"slot": s.get("slot"), "species": s.get("species"), "ja": s.get("ja"), "guess": bool(s.get("guess")),
                     "appeared": bool(b and b in app)})
    for b in sorted(app - seen):
        rows.append({"slot": None, "species": b, "ja": None, "guess": False, "appeared": True})
    n_app = sum(1 for r in rows if r["appeared"])
    complete = n_app == pick_count
    for r in rows:
        r["pick_status"] = PICK_CONFIRMED if r["appeared"] else (PICK_UNPICKED if complete else PICK_UNKNOWN)
    return {"slots": rows, "n_appeared": n_app, "complete": complete, "inconsistent": n_app > pick_count,
            "pick_count": pick_count}


def roster_changes(prev: dict, cur: dict) -> list:
    """前のフレームから種が変わった (置き換わった / 消えた) 相手の枠 [(slot, 前, 後)] (純粋)。前に種が無かった枠は数えない"""
    out = []
    for i, before in sorted((prev or {}).items()):
        after = (cur or {}).get(i)
        if _slot_identity(before) and _slot_identity(after) != _slot_identity(before):
            out.append((i, before, after))
    return out


def roster_change_basis(after: Optional[dict], slot: int, active_index, scene: Optional[str], manual: bool) -> str:
    """枠の置き換えの根拠 (純粋): manual (手入力) / cleared (消えた) / field (場に出た) / selection_guess (別の推定) / name_read"""
    if manual:
        return "manual"
    if not _slot_identity(after):
        return "cleared"
    if after.get("guess"):
        return "selection_guess"
    if slot == active_index and scene in OPP_APPEAR_SCENES:
        return "field"
    return "name_read"


def guess_verdict(guessed: Optional[str], revealed_in_slot: Optional[str], appeared) -> tuple:
    """推定した種と、後に場で判明した種の突き合わせ (純粋) → (判明した種, "match" / "mismatch" / "unrevealed")。
    推定した種が (どの枠でも) 場に出たら一致。出ておらず、その枠に別の種が場で判明したら不一致。どちらでもなければ未判明"""
    g = _base_sid(guessed)
    app = {_base_sid(s) for s in (appeared or []) if s}
    if g and g in app:
        return guessed, "match"
    if revealed_in_slot and _base_sid(revealed_in_slot) != g:
        return revealed_in_slot, "mismatch"
    return None, "unrevealed"


def final_guess_verdict(revealed: Optional[str], verdict: str, superseded: bool, pending_open: bool) -> tuple:
    """guess_verdict の結果に、枠の統合と対応待ちを反映する (純粋。2026-10-09 ④) → (判明した種, verdict)。
    - superseded: 推定の枠が統合で消えた (枠の番号がずれた)。その枠の後の判明は推定と比べられない (一致は残す)
    - unresolved: 未判明だが、どの枠か決まらないまま終わった場の個体 (対応待ち) がいる。その個体がこの枠だった可能性が
      あるので未判明とは言えない
    既存の値 (match / mismatch / unrevealed) の意味は変えない。読み手は superseded / unresolved を的中率の分母から除く"""
    if verdict == "match":
        return revealed, verdict
    if superseded:
        return None, GUESS_SUPERSEDED
    if verdict == "unrevealed" and pending_open:
        return None, GUESS_UNRESOLVED
    return revealed, verdict


def shift_slot_keys(d: dict, popped: int) -> dict:
    """枠の番号をキーにした dict を、枠 popped が除かれた後の番号に直す (純粋): popped は落とし、後ろは 1 つ詰める"""
    return {(k - 1 if k > popped else k): v for k, v in d.items() if k != popped}


def opp_pending_of(state: Optional[dict]) -> list:
    """対応待ちの相手の個体 (party の PARTY_SIZE 以降で pending、純粋) → [{"key", "species", "ja", "candidates",
    "t_first_seen", "hp", "revealed", "status"}]"""
    party = (((state or {}).get("opponent") or {}).get("party") or [])
    out = []
    for i, p in enumerate(party):
        if i < PARTY_SIZE or not p.get("pending"):
            continue
        out.append({"key": p.get("t_first_seen") if p.get("t_first_seen") is not None else f"{i}:{p.get('species_id')}",
                    "species": p.get("species_id"), "ja": p.get("species_ja"),
                    "candidates": list(p.get("species_candidates") or []), "t_first_seen": p.get("t_first_seen"),
                    "hp": p.get("hp_percent"), "revealed": list(p.get("revealed_moves") or []),
                    "status": p.get("status")})
    return out


def player_action_of(fired: list) -> Optional[dict]:
    """発火 id からプレイヤーの行動 {"kind": move|switch, "id"} (純粋。tools/decision_audit._player_action と同じ規則)"""
    for f in fired or []:
        if f.startswith("move_player_"):
            return {"kind": "move", "id": f[len("move_player_"):]}
        if f == "switch_player":
            return {"kind": "switch", "id": None}
    return None


def decision_match(best: Optional[dict], action: Optional[dict]) -> Optional[bool]:
    """決定が第一候補と一致したか (純粋)。行動・第一候補が分からなければ None。交代先が読めない交代は、第一候補が交代なら None"""
    if not best or not action:
        return None
    if action.get("kind") == "move":
        return best.get("kind") == "move" and best.get("id") == action.get("id")
    if action.get("kind") == "switch":
        if best.get("kind") != "switch":
            return False
        if action.get("id") is None:
            return None
        return best.get("id") == action.get("id")
    return None


_MANUAL_SPECIES_RE = re.compile(r"相手の(.+?)を手動確定")
_MANUAL_MON_FIELDS = {"hp_percent": "hp_percent", "status": "status", "item": "item_ja", "ability": "ability_ja",
                      "is_mega": "is_mega", "types": "types", "hp_current": "hp_current"}


def _find_slot(state: Optional[dict], side: str, ja: Optional[str]) -> Optional[int]:
    party = (((state or {}).get(side) or {}).get("party") or [])
    return next((i for i, p in enumerate(party) if ja and p.get("species_ja") == ja), None)


def manual_fix_key(event: dict, state: Optional[dict]) -> Optional[dict]:
    """手入力の訂正 (state の events の source=manual) が直した対象 (純粋)。追えなければ None。
    {"target": mon|species|field|hazards, "side", "index", "field"}"""
    det = event.get("detail") or {}
    if event.get("event") == "species_manual":
        m = _MANUAL_SPECIES_RE.search(event.get("text") or "")
        idx = _find_slot(state, "opponent", m.group(1)) if m else None
        return {"target": "species", "side": "opponent", "index": idx, "field": "species"} if idx is not None else None
    tgt, fld = det.get("target"), str(det.get("field") or "")
    label = str(det.get("label") or "")
    if tgt == "mon":
        parts = label.split(":")
        side = det.get("side") or (parts[0] if parts else None)
        idx = det.get("index")
        if idx is None and len(parts) >= 2:
            idx = _find_slot(state, side, parts[1])
        if side not in ("player", "opponent") or idx is None:
            return None
        return {"target": "mon", "side": side, "index": int(idx), "field": fld}
    if tgt == "field" and fld:
        return {"target": "field", "side": None, "index": None, "field": fld}
    if tgt == "hazards" and fld:
        side = det.get("side") or (label.split(":")[0] if label else None)
        return {"target": "hazards", "side": side, "index": None, "field": fld}
    return None


def manual_current_value(state: Optional[dict], key: dict):
    """訂正の対象のいまの値 (純粋)。読めなければ None"""
    st = state or {}
    tgt = key.get("target")
    if tgt in ("mon", "species"):
        party = ((st.get(key.get("side")) or {}).get("party") or [])
        idx = key.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(party)):
            return None
        p = party[idx]
        if tgt == "species":
            return p.get("species_id") or p.get("species_ja")
        fld = key.get("field") or ""
        if fld.startswith("boost:"):
            return (p.get("boosts") or {}).get(fld.split(":", 1)[1])
        return p.get(_MANUAL_MON_FIELDS.get(fld, fld))
    if tgt == "field":
        return (st.get("field") or {}).get(key.get("field"))
    if tgt == "hazards":
        return ((st.get(key.get("side")) or {}).get("hazards") or {}).get(key.get("field"))
    return None


def manual_value_from_detail(key: dict, detail: Optional[dict]) -> tuple:
    """訂正で入れた値を detail["after"] から server (set_state) と同じ規則で求める (純粋) → (求まったか, 値)。
    入れた直後のフレームで推定が既に上書きしていても、入れた値と比べられるように。名前の解決が要る持ち物・特性・タイプ・種は求めない"""
    det = detail or {}
    if "after" not in det:
        return False, None
    v, fld = det.get("after"), str(key.get("field") or "")
    try:
        if key.get("target") == "mon":
            if fld == "hp_percent":
                return True, float(v)
            if fld == "status":
                return True, (v or None)
            if fld.startswith("boost:"):
                return True, max(-6, min(6, int(v)))
            if fld == "is_mega":
                return True, bool(v)
        elif key.get("target") == "field":
            if fld in ("weather", "terrain"):
                return True, (v or None)
            if fld == "trick_room":
                return True, bool(v)
        elif key.get("target") == "hazards":
            if fld == "stealth_rock":
                return True, bool(v)
            if fld == "spikes":
                return True, max(0, min(3, int(v)))
            if fld == "toxic_spikes":
                return True, max(0, min(2, int(v)))
    except (TypeError, ValueError):
        return False, None
    return False, None


def _same_value(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


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
        # 対応待ちの個体 (party の PARTY_SIZE 以降に置く場の個体) と、形態が決まっていない種の候補 (2026-10-09 ④)
        if p.get("pending"):
            d["pending"] = True
        if p.get("species_candidates"):
            d["candidates"] = list(p["species_candidates"])
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


def scene_row_state(state: dict) -> dict:
    """scene 行の state (純粋): 簡約状態 + 側ごとの「最後に捨てた HP の読み」(hp_reject、2026-10-09 に追加した欄)。

    hp_reject は scene 行にだけ載せる (助言の行の state と state_id の digest は _compact_state のままで変えない)"""
    d = _compact_state(state)
    d["hp_reject"] = state.get("hp_reject") or {"player": None, "opponent": None}
    # HP が推定値の個体に印 (2026-10-09。_compact_state の個体と同じ並び)
    # HP を最後に実際に読んで確定した時刻 hp_read_ts (2026-10-09 fix/hp-paths。digest を変えないよう scene 行だけ)
    for side in ("player", "opponent"):
        for row, p in zip(d[side]["party"], (state.get(side) or {}).get("party") or []):
            if p.get("hp_estimated"):
                row["hp_estimated"] = True
                row["hp_source"] = p.get("hp_source")
            row["hp_read_ts"] = p.get("hp_read_ts")
    # 様子見画面の右列の行ごとの読み (2026-10-09 fix/hp-paths。scene が watch のときだけ)
    if state.get("scene") == SCENE_WATCH:
        d["watch_opp_rows"] = [dict(r) for r in (state.get("watch_opp_rows") or [])]
    return d


def my_hp_context_of(state: Optional[dict]) -> dict:
    """advice 行の context (純粋): 自分の場の個体の HP が推定値なら {"my_hp_estimated": True, "my_hp_source": 出所}、
    推定でなければ {} (2026-10-09。採点には使わない記録だけの欄)"""
    sd = (state or {}).get("player") or {}
    idx, party = sd.get("active_index"), sd.get("party") or []
    p = party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else None
    if not p or not p.get("hp_estimated"):
        return {}
    return {"my_hp_estimated": True, "my_hp_source": p.get("hp_source")}


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


def default_guess_prob(types: list, species_id: Optional[str]) -> Optional[float]:
    """選出画面の推定の確率: タイプからの候補 (advisor.infer) の中でその種の事前確率。分からなければ None (副作用: 推論器の読み込み)"""
    if not types or not species_id:
        return None
    try:
        from advisor.infer import get_inference
        base = _base_sid(species_id)
        cands = get_inference().candidates(types)
        return next((round(float(p), 4) for sid, p, _ja in cands if sid == base), 0.0)
    except Exception:
        return None


class BattleLogger:
    def __init__(self, log_dir: Path = LOG_DIR, guess_prob_fn: Optional[Callable] = default_guess_prob,
                 frame_source: Optional[Callable] = None):
        self.log_dir = log_dir
        # 段 0 (2026-10-07): 推定の確率の求め方 (テストで差し替える) と、server のフレームの累積件数
        # ({"received", "processed", "dropped", "hidden", "last_recv_ts"} を返す呼び出し。無ければ frames 行を書かない)
        self.guess_prob_fn = guess_prob_fn
        self.frame_source = frame_source
        # 対戦ファイルを開いたときに version 行の直後に書く行 (client / visibility。server が client_state.ClientRegistry.open_rows を
        # 渡す。2026-10-07 実機確認)。type 付きの dict の list を返す呼び出し。無ければ書かない
        self.open_rows_source: Optional[Callable] = None
        self._frames_start: Optional[dict] = None
        self._fix_seq = 0
        self._reset_tracking()
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
        self._shown: dict = {}          # 助言 ID → 表示時刻 (ブラウザの時計。hidden でないもの)。decision 行に添える
        self._trace_written: set = set()  # 書いた my_hp_trace の分 (battle_seq, n, reason)

    def _reset_tracking(self) -> None:
        """段 0 の追跡 (1 対戦ぶん) を初期化する"""
        self._opp_slots: dict = {}       # 前のフレームの相手の枠 (opp_slots_of)
        self._appeared: set = set()      # 場に出た相手の種 id
        self._slot_revealed: dict = {}   # 枠 → 場で判明した種 (確定、場に出た)
        self._guesses: list = []         # 選出画面の推定 [{"slot", "species", "ja", "prob", "score", "t_guess"}]
        self._guess_keys: set = set()
        self._guess_prob_of: dict = {}   # (slot, species) → 推定時の確率 (roster_change の from_prob)
        self._merge_seen: set = set()    # 処理済みの枠の統合・対応待ちの解消のイベント (ts, event, text)
        self._reread_seen: set = set()   # 処理済みのタイプの訂正のイベント (ts, text)
        self._pending_open: dict = {}    # いまの対応待ちの個体 (opp_pending_of、key → 内容)。終了時に残れば unresolved
        self._fixes: list = []           # 追跡中の手入力の訂正 [{"fix_id", "key", "manual_value", ...}]
        self._dec: Optional[dict] = None   # 決定の追跡 {"advice_id", "best", "t_gen", "turn", "n_advice", "t_decided", ...}
        self._dec_open = False           # 決定画面を見てから、まだ解決側の場面に移っていない
        self._dec_resolve = None         # 解決側の場面に移った最初のフレーム (時刻, 場面, 決定画面の場面) と連続数
        self._dec_last_scene = None
        self._frames_sig = None          # 最後に書いた frames 行の内容 (同じなら書き直さない)
        self._picks_sig = None           # 最後に書いた opp_picks / guess_confirm の内容

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
        self._frames_start = self._frame_counts()
        self._frames_sig = None
        self._picks_sig = None
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
        # 助言ページの版と可視状態 (接続中の全クライアント。2026-10-07)
        if self.open_rows_source is not None:
            try:
                for r in self.open_rows_source() or []:
                    self._write(dict(r))
            except Exception as e:
                print(f"[battle_log] 接続の記録に失敗: {e}")

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
        # 段 0: 対戦の終わりの行 (frames / opp_picks / guess_confirm)。次の対戦への切り替え (_finalize) でも書き直す
        self._write_end_rows(final=False)

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
        if self._file is not None:
            self._write_end_rows(final=True)
        self._reset_tracking()
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

    # ------------------------------------------------------------------ 段 0 の追跡 (2026-10-07)
    def _frame_counts(self) -> Optional[dict]:
        if self.frame_source is None:
            return None
        try:
            return dict(self.frame_source() or {})
        except Exception:
            return None

    def _write_end_rows(self, final: bool) -> None:
        """対戦の終わりの行: 決めた後に行動を待っている decision、frames、opp_picks、guess_confirm、
        対応待ちのまま終わった個体の roster_change (unresolved: true)。
        勝敗を記録した時と次の対戦への切り替え (close を含む) で呼ぶ。内容が前に書いたものと同じなら書き直さない"""
        if self._file is None:
            return
        try:
            if self._dec and self._dec.get("t_decided") is not None:
                self._flush_decision()
            end = self._frame_counts()
            if end is not None and self._frames_start is not None:
                fr = frames_row(self._frames_start, end, self._opened_ts)
                sig = json.dumps(fr, sort_keys=True)
                if sig != self._frames_sig:
                    self._frames_sig = sig
                    self._write({"type": "frames", **fr, "final": bool(final)})
            slots = [{"slot": i, **s} for i, s in sorted(self._opp_slots.items())]
            if not slots and not self._appeared:
                return
            lab = label_opp_picks([s for s in slots if _slot_identity(s) or s.get("types")], self._appeared)
            guesses = []
            for g in self._guesses:
                slot_now = g.get("_slot", g["slot"])
                revealed, verdict = guess_verdict(g["species"], self._slot_revealed.get(slot_now), self._appeared)
                revealed, verdict = final_guess_verdict(revealed, verdict, bool(g.get("_superseded")),
                                                        bool(self._pending_open))
                prob = g.get("prob")
                pub = {k: v for k, v in g.items() if not k.startswith("_")}
                guesses.append({**pub, "revealed": revealed, "verdict": verdict,
                                "auto_accept": (prob >= SELECTION_PRIOR_AUTO_ACCEPT) if prob is not None else None,
                                "sure": (prob >= SELECTION_GUESS_SURE_PROB) if prob is not None else None,
                                "threshold_auto_accept": SELECTION_PRIOR_AUTO_ACCEPT,
                                "threshold_sure": SELECTION_GUESS_SURE_PROB})
            # 対応待ちのまま終わった場の個体 (2026-10-09 ④): roster_change に unresolved: true で残す (集計から除けるように)
            unresolved = [{"type": "roster_change", "slot": None, "scene": None, "turn": None,
                           "from": None, "from_ja": None, "from_guess": None, "from_prob": None, "from_score": None,
                           "to": p["species"], "to_ja": p["ja"], "to_guess": False, "basis": "field",
                           "unresolved": True, "candidates": p["candidates"], "t_first_seen": p["t_first_seen"],
                           "moved": {"hp": p["hp"], "revealed_moves": p["revealed"], "status": p["status"]}}
                          for _k, p in sorted(self._pending_open.items(), key=lambda kv: str(kv[0]))]
            sig = json.dumps([lab, guesses, unresolved], sort_keys=True, ensure_ascii=False, default=str)
            if sig == self._picks_sig:
                return
            self._picks_sig = sig
            self._write({"type": "opp_picks", **lab, "final": bool(final)})
            for g in guesses:
                self._write({"type": "guess_confirm", **g, "final": bool(final)})
            for r in unresolved:
                self._write({**r, "final": bool(final)})
        except Exception as e:      # 記録の失敗で対戦ログを止めない
            print(f"[battle_log] 終了時の行の記録に失敗: {e}")

    def close(self) -> None:
        """サーバー停止時: 開いている対戦の終わりの行を書く (勝敗は書かない。次の起動で別ファイルになる)"""
        if self._file is not None:
            self._write_end_rows(final=True)

    def _new_roster_merges(self, state: dict) -> list:
        """このフレームで新しく出た枠の統合・対応待ちの解消のイベント [(event, detail)] (state の events から)"""
        out = []
        for e in state.get("events", []) or []:
            if e.get("event") not in ROSTER_MERGE_EVENTS or (e.get("target") or "opponent") != "opponent":
                continue
            key = (e.get("ts"), e.get("event"), e.get("text"))
            if key in self._merge_seen:
                continue
            self._merge_seen.add(key)
            out.append((e["event"], dict(e.get("detail") or {})))
        return out

    def _new_type_rereads(self, state: dict) -> dict:
        """このフレームで新しく出たタイプの訂正のイベント {枠: detail} (state の events から。同じ枠は最後のもの)"""
        out = {}
        for e in state.get("events", []) or []:
            if e.get("event") != "type_reread" or (e.get("target") or "opponent") != "opponent":
                continue
            key = (e.get("ts"), e.get("text"))
            if key in self._reread_seen:
                continue
            self._reread_seen.add(key)
            det = dict(e.get("detail") or {})
            if isinstance(det.get("slot"), int):
                out[det["slot"]] = det
        return out

    @staticmethod
    def _reread_fields(det: dict) -> dict:
        return {"types_from": list(det.get("types_from") or []), "types_to": list(det.get("types_to") or []),
                "reason": "type_reread", "type_source": det.get("source")}

    def _shift_slots(self, popped: int) -> None:
        """相手の枠 popped が party から除かれた (統合): 枠の番号で持っている追跡を詰め直す。その枠の推定は superseded"""
        self._opp_slots = shift_slot_keys(self._opp_slots, popped)
        self._slot_revealed = shift_slot_keys(self._slot_revealed, popped)
        self._guess_keys = {((k - 1 if k > popped else k), s) for k, s in self._guess_keys if k != popped}
        self._guess_prob_of = {((k - 1 if k > popped else k), s): v for (k, s), v in self._guess_prob_of.items()
                               if k != popped}
        for g in self._guesses:
            if g.get("_superseded"):
                continue
            if g["_slot"] == popped:
                g["_superseded"] = True
            elif g["_slot"] > popped:
                g["_slot"] -= 1

    @staticmethod
    def _merge_fields(ev: str, det: dict) -> dict:
        """roster_change 行に足す統合の欄 (2026-10-09 ④): 統合元・統合先の枠と、移した観測情報・置き換えた枠の推定"""
        out = {"merged_from": det.get("merged_from"), "merged_to": det.get("merged_to"),
               "merge_kind": "pending" if ev == "roster_pending_resolved" else "placeholder",
               "moved": det.get("moved"), "replaced": det.get("replaced")}
        if det.get("source_cleared"):
            out["source_cleared"] = True
        if det.get("t_first_seen") is not None:
            out["t_first_seen"] = det.get("t_first_seen")
        if det.get("basis") == "manual":
            out["basis"] = "manual"   # 人が対応待ちの個体の枠を指定した (2026-10-09 段 2、vision.state.assign_pending)
        return out

    def _track_opponent(self, state: dict, scene: Optional[str], manual_species: bool) -> None:
        """相手の枠の置き換え (roster_change)、選出画面の推定 (guess_confirm の材料)、場に出た種 (opp_picks の材料) を追う。
        2026-10-09 ④: 枠の統合 (roster_merge) と対応待ちの解消 (roster_pending_resolved) を roster_change 行に
        merged_from / merged_to として付け、統合で枠の番号がずれたら追跡を詰め直す (ずれた番号で推定と判明を比べない)"""
        merges = self._new_roster_merges(state)
        for ev, det in merges:
            src = det.get("merged_from")
            if ev != "roster_merge" or not isinstance(src, int) or src >= PARTY_SIZE:
                continue
            if det.get("source_cleared"):
                # 統合元の枠は未特定の枠に戻った: その枠で場に判明した種は統合先へ移ったので、その枠の推定と比べない
                self._slot_revealed.pop(src, None)
                for g in self._guesses:
                    if g["_slot"] == src and not g.get("_superseded"):
                        g["_superseded"] = True
            else:
                self._shift_slots(src)
        merged_to = {det["merged_to"]: (ev, det) for ev, det in merges if isinstance(det.get("merged_to"), int)}
        rereads = self._new_type_rereads(state)
        cur = opp_slots_of(state)
        act = (state.get("opponent") or {}).get("active_index")
        written = set()
        for i, before, after in roster_changes(self._opp_slots, cur):
            row = {"type": "roster_change", "slot": i, "scene": scene, "turn": state.get("turn"),
                   "from": before.get("species"), "from_ja": before.get("ja"), "from_guess": before.get("guess"),
                   "from_prob": self._guess_prob_of.get((i, _slot_identity(before))),
                   "from_score": before.get("score"),
                   "to": (after or {}).get("species"), "to_ja": (after or {}).get("ja"),
                   "to_guess": bool((after or {}).get("guess")),
                   "basis": roster_change_basis(after, i, act, scene, manual_species)}
            if i in merged_to:
                row.update(self._merge_fields(*merged_to[i]))
            if i in rereads:
                row.update(self._reread_fields(rereads[i]))
            self._write(row)
            written.add(i)
        for i, (ev, det) in sorted(merged_to.items()):
            if i in written or i >= PARTY_SIZE:
                continue
            # 種の無かった枠 (未特定の枠) への統合: 置き換えの行は出ないので、統合の行を 1 つ書く
            after = cur.get(i) or {}
            rep = det.get("replaced") or {}
            row = {"type": "roster_change", "slot": i, "scene": scene, "turn": state.get("turn"),
                   "from": rep.get("species"), "from_ja": rep.get("ja"), "from_guess": bool(rep.get("guess")),
                   "from_prob": self._guess_prob_of.get((i, rep.get("species"))) if rep.get("species") else None,
                   "from_score": rep.get("score"),
                   "to": after.get("species"), "to_ja": after.get("ja"), "to_guess": bool(after.get("guess")),
                   "basis": roster_change_basis(after, i, act, scene, manual_species)}
            row.update(self._merge_fields(ev, det))
            self._write(row)
            written.add(i)
        for i, det in sorted(rereads.items()):
            if i in written or i >= PARTY_SIZE:
                continue
            # 種が変わらなかった訂正 (推定の無い枠など): 置き換えの行は出ないので、訂正の行を 1 つ書く
            before = self._opp_slots.get(i) or {}
            after = cur.get(i) or {}
            row = {"type": "roster_change", "slot": i, "scene": scene, "turn": state.get("turn"),
                   "from": det.get("cancelled") or before.get("species"),
                   "from_ja": det.get("cancelled_ja") or before.get("ja"),
                   "from_guess": bool(det.get("cancelled")) or bool(before.get("guess")),
                   "from_prob": self._guess_prob_of.get((i, det.get("cancelled") or before.get("species"))),
                   "from_score": before.get("score"),
                   "to": after.get("species"), "to_ja": after.get("ja"), "to_guess": bool(after.get("guess")),
                   "basis": roster_change_basis(after, i, act, scene, manual_species)}
            row.update(self._reread_fields(det))
            self._write(row)
        now = time.time()
        for i, s in cur.items():
            if s.get("guess") and s.get("species") and (i, s["species"]) not in self._guess_keys:
                self._guess_keys.add((i, s["species"]))
                prob = None
                if self.guess_prob_fn is not None:
                    try:
                        prob = self.guess_prob_fn(s.get("types") or [], s["species"])
                    except Exception:
                        prob = None
                self._guess_prob_of[(i, s["species"])] = prob
                self._guesses.append({"slot": i, "species": s["species"], "ja": s.get("ja"), "prob": prob,
                                      "score": s.get("score"), "t_guess": round(now, 2), "_slot": i})
        for i, sid in opp_appeared_now(state, scene):
            self._appeared.add(sid)
            self._slot_revealed[i] = sid
        self._pending_open = {p["key"]: p for p in opp_pending_of(state)}
        self._opp_slots = cur

    def _register_fix(self, event: dict, state: dict, fix_id: str, t: float) -> None:
        key = manual_fix_key(event, state)
        if key is None:
            return
        # 同じ対象への前の訂正は追わない (新しい訂正で置き換え)
        self._fixes = [f for f in self._fixes if f["key"] != key]
        ok, value = manual_value_from_detail(key, event.get("detail"))
        self._fixes.append({"fix_id": fix_id, "key": key, "manual_value": value if ok else manual_current_value(state, key),
                            "label": (event.get("detail") or {}).get("label") or event.get("text"),
                            "fix_t": t, "fix_turn": state.get("turn")})

    def _track_fixes(self, state: dict, scene: Optional[str]) -> None:
        """手入力の訂正が後の推定で別の値に変わったら (最初の 1 回) manual_fix_overwritten の行を書く"""
        keep = []
        for f in self._fixes:
            now_v = manual_current_value(state, f["key"])
            if _same_value(now_v, f["manual_value"]):
                keep.append(f)
                continue
            t = round(time.time(), 2)
            self._write({"type": "manual_fix_overwritten", "fix_id": f["fix_id"], "label": f["label"],
                         **{k: f["key"].get(k) for k in ("target", "side", "index", "field")},
                         "manual_value": f["manual_value"], "new_value": now_v, "fix_t": f["fix_t"],
                         "fix_turn": f["fix_turn"], "overwritten_at": t, "turn": state.get("turn"), "scene": scene})
        self._fixes = keep

    def _flush_decision(self, action: Optional[dict] = None) -> None:
        d, self._dec = self._dec, None
        if not d or d.get("t_decided") is None:
            return
        best = d.get("best")
        self._write({"type": "decision", "advice_id": d["advice_id"], "turn": d.get("turn"), "t_gen": d.get("t_gen"),
                     "t_shown": self._shown.get(d["advice_id"]), "t_decided": d["t_decided"],
                     "scene_from": d.get("scene_from"), "scene_to": d.get("scene_to"), "n_advice": d.get("n_advice"),
                     "best": best, "action": action, "match": decision_match(best, action)})

    def _track_decision(self, state: dict, scene: Optional[str], fired: list) -> None:
        """助言のあと、決定画面 → 解決側の場面への移り (DECISION_CONFIRM_FRAMES フレーム続く) を決定とし、その後の行動を突き合わせる"""
        now = time.time()
        d = self._dec
        act = player_action_of(fired)
        if act is not None and act["kind"] == "switch":
            pl = state.get("player") or {}
            idx, party = pl.get("active_index"), pl.get("party") or []
            if isinstance(idx, int) and 0 <= idx < len(party):
                act["id"] = party[idx].get("species_id")
        if d is not None and d.get("t_decided") is not None:
            if act is not None:
                self._flush_decision(act)
            elif now - d["t_decided"] > DECISION_ACTION_WAIT_SEC:
                self._flush_decision(None)
            return
        if scene in DECISION_SCENES:
            self._dec_open = True
            self._dec_resolve = None
            self._dec_last_scene = scene
        elif scene in DECISION_INFO_SCENES:
            pass                                  # 決定中の情報確認。決定は開いたまま
        elif scene in DECISION_RESOLVE_SCENES and self._dec_open:
            if self._dec_resolve is None:
                self._dec_resolve = {"t": round(now, 2), "scene": scene, "from": self._dec_last_scene, "n": 0,
                                     "action": None}
            self._dec_resolve["n"] += 1
            if act is not None and self._dec_resolve["action"] is None:
                self._dec_resolve["action"] = act       # 決定の確認より先に行動が読めた (低 fps のとき)
            if self._dec_resolve["n"] >= DECISION_CONFIRM_FRAMES:
                r = self._dec_resolve
                self._dec_open, self._dec_resolve = False, None
                if d is not None:
                    d.update(t_decided=r["t"], scene_from=r["from"], scene_to=r["scene"])
                    if r["action"] is not None:
                        self._flush_decision(r["action"])
        else:
            self._dec_resolve = None

    @property
    def file_open(self) -> bool:
        """対戦ファイルが開いているか (開いていなければ、次に何か書いたときに新しいファイルを開く)"""
        return self._file is not None

    def on_client_row(self, row: dict) -> None:
        """client 行 / visibility 行 (client_state.ClientRegistry が作る、type 付き) を、対戦ファイルが開いていれば書く。
        開いていなければ書かない (ファイルを開くだけの行にしない。次に開いたとき open_rows_source が最新の状態を書く)"""
        if self._file is None or not row:
            return
        try:
            self._write(dict(row))
        except Exception as e:
            print(f"[battle_log] 接続の記録に失敗: {e}")

    def on_frame_burst(self, info: dict) -> None:
        """連続フレームの保存を始めた (server)。保存先と開始時刻を対戦ログに残し、保存フレームと対戦を結ぶ"""
        try:
            self._write({"type": "frame_burst", **(info or {})})
        except Exception as e:
            print(f"[battle_log] 連続保存の記録に失敗: {e}")

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
                # 前の対戦の自分の HP の読みの経過 (reset_battle が出した分) は、前の対戦のファイルに書いてから閉じる
                # (前の対戦のファイルが開いていなければ書かない: 次の対戦のファイルに混ぜない)
                self._write_my_hp_traces(state, self._last_seq, write=self._file is not None)
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
        manual_species = False
        for e in state.get("events", []):
            if e.get("ts", 0) <= self._hp_seen_ts:
                continue
            if e.get("source") == "hp":
                self._hp_seen_ts = e["ts"]
                det = e.get("detail") or {}
                row = {"type": "hp", "turn": state.get("turn"), "text": e["text"], "detail": e.get("detail")}
                if det.get("estimated"):
                    # バー推定の値の変化 (2026-10-09): 出所と推定の印
                    row["source"] = det.get("source")
                    row["estimated"] = True
                self._write(row)
                # 勝敗推定用: HP0%到達の側を覚えておく (バー推定の値は数えない: 推定だけでひんしを確定しない)
                if det.get("to") is not None and det["to"] <= OUTCOME_ZERO_HP_PCT \
                        and det.get("side") and not det.get("estimated"):
                    self._last_zero = (det["side"], e["ts"])
            elif e.get("source") == "manual":
                self._hp_seen_ts = e["ts"]
                # fix_id (2026-10-07 段 0): 後の推定で上書きされたら manual_fix_overwritten の行がこの id で結ぶ
                self._fix_seq += 1
                fix_id = f"m{self._fix_seq:04d}"
                row = {"type": "manual_fix", "turn": state.get("turn"), "scene": scene,
                       "text": e["text"], "detail": e.get("detail"), "fix_id": fix_id}
                det = e.get("detail") or {}
                if "applied" in det:
                    # 種の手動確定を入れたか / 入れなかった理由 (2026-10-09 ④。入れなかったときだけ reason)
                    row["applied"] = bool(det.get("applied"))
                    if not row["applied"]:
                        row["reason"] = det.get("reason")
                self._write(row)
                if e.get("event") == "species_manual":
                    manual_species = True
                try:
                    self._register_fix(e, state, fix_id, float(e.get("ts") or time.time()))
                except Exception:
                    pass

        # 自分の HP の読みの経過 (2026-10-09 fix/hp-paths。このフレームで state が出した分)
        self._write_my_hp_traces(state, None)

        # 段 0 (2026-10-07): 相手の枠の置き換え・推定・場に出た種、手入力の訂正の上書き、決定の確認 (記録だけ。失敗で止めない)
        try:
            self._track_opponent(state, scene, manual_species)
            self._track_fixes(state, scene)
            self._track_decision(state, scene, fired)
        except Exception as e:
            print(f"[battle_log] 段 0 の追跡に失敗: {e}")

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
                         "state": scene_row_state(state)})
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
            sum(1 for _i, p in roster_slots(state.get("opponent", {}).get("party"))   # 対応待ちは数えない
                if p.get("status") == "fainted"))

    def _write_my_hp_traces(self, state: dict, only_seq, write: bool = True) -> None:
        """state の my_hp_trace_dumps (このフレームで出した自分の HP の読みの経過) を my_hp_trace 行として書く。
        only_seq を渡したら、その対戦の分だけを書く (前の対戦のファイルを閉じる前)。同じ分は 1 回だけ書く。
        write=False なら書かずに書いた扱いにする"""
        try:
            for dmp in state.get("my_hp_trace_dumps") or []:
                key = (dmp.get("battle_seq"), dmp.get("n"), dmp.get("reason"))
                if key in self._trace_written:
                    continue
                if only_seq is not None and dmp.get("battle_seq") != only_seq:
                    continue
                self._trace_written.add(key)
                if not write:
                    continue
                self._write({"type": "my_hp_trace", "reason": dmp.get("reason"), "battle_seq": dmp.get("battle_seq"),
                             "n": dmp.get("n"), "rows": list(dmp.get("rows") or [])})
        except Exception as e:      # 記録の失敗で対戦ログを止めない
            print(f"[battle_log] 自分の HP の読みの経過の記録に失敗: {e}")

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
        # 段 0 (2026-10-07): 助言時点の HP の古さ
        if state is not None:
            try:
                rec["hp_stale"] = hp_stale_of(state, time.time())
            except Exception:
                pass
            # 自分の HP が推定値 (バー推定) のときだけ context を残す (2026-10-09)
            try:
                ctx = my_hp_context_of(state)
                if ctx:
                    rec["context"] = ctx
            except Exception:
                pass
        self._write(rec)
        # decision 行の追跡: 表示する対戦の助言 (確定前 provisional を除く) を、次の決定に結ぶ助言にする
        try:
            if kind == "battle" and not advice.get("provisional") and advice.get("ok", True):
                if self._dec is not None and self._dec.get("t_decided") is not None:
                    self._flush_decision(None)        # 前の決定の行動が読めないまま次の助言が来た
                best = advice.get("best") or ((advice.get("actions") or [None])[0])
                prev_n = (self._dec or {}).get("n_advice", 0)
                self._dec = {"advice_id": aid, "turn": rec.get("turn"), "t_gen": advice.get("t_gen"), "t_decided": None,
                             "n_advice": prev_n + 1,
                             "best": ({"kind": best.get("kind"), "id": best.get("id"), "name": best.get("name")}
                                      if best else None)}
        except Exception:
            pass
        return aid

    def on_selection_record(self, advice_id: str, record: Optional[dict]) -> None:
        """選出の助言の記録用の欄 (2026-10-07 段 0、advisor.selection_record の candidates / opp_pick_pred) を、助言とは別の
        selection_record 行に書く。server は助言を送った**後**に計算して呼ぶ (表示を遅らせない)。行は display と同じく
        **助言を書いた対戦のファイル**に書き、advice_id で advice 行に結ぶ"""
        if not advice_id:
            return
        target = self._advice_files.get(str(advice_id))
        if target is None and self._file is None:
            return
        rec = {"type": "selection_record", "advice_id": str(advice_id), **{k: v for k, v in (record or {}).items()
                                                                           if k not in ("type", "advice_id", "t")}}
        if target is not None and target != self._file:
            rec["attributed"] = "advice_battle"
            rec["t"] = round(time.time(), 2)
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return
        self._write(rec)

    def on_advice_variant(self, record: dict) -> None:
        """影の計算 (advisor.shadow) の結果を advice_variant 行として書く (2026-10-07、計画 §2)。
        record は advisor.shadow が作る行 (type / advice_id を含む)。行は**助言を書いた対戦のファイル**に書く (on_display と同じ帰属)。
        助言の行が見つからない (ファイルの回転で古い助言の対応が消えた等) ときは書かない。
        サーバーはイベントループのスレッドからだけ呼ぶ (ワーカーのスレッドから直接書かない: 他の行の書き込みと競合させない)"""
        aid = str(record.get("advice_id") or "")
        target = self._advice_files.get(aid)
        if not aid or target is None:
            return
        rec = dict(record, type="advice_variant")
        rec["t"] = round(time.time(), 2)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

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
        if t_shown is not None and not hidden:
            self._shown.setdefault(str(advice_id), rec["t_shown"])
            if len(self._shown) > 2000:
                self._shown.pop(next(iter(self._shown)))
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
