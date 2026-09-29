"""対戦終了 (とその見込み) を助言欄に出す通知の生成 (純粋計算。server.py が advice_update として配信する)。

終了の検知 (勝負文言 / 3 体目のひんしの確定 / リザルト画面 / ランク画面) と表示の間に遅れを作らず、何を根拠に
終了と判断したかを見せる。2026-09-29 第17回接続テスト: 「終了の認識が遅い」と見えた。実測は勝負文言が最終ひんしの
文言の 4〜6 秒後 (ゲームの表示どおり)、ランク画面がさらに約 10 秒後で、その間の助言欄は前のターンの助言のままだった。
"""
from __future__ import annotations

from typing import Optional

from champions_agent.config import BATTLE_END_FAINT_CONFIRM_SEC

# 終了の確定イベント → 根拠の表示 (fired に複数あれば先頭のものを使う)
END_BASIS = (
    (("battle_win", "battle_lose"), "勝負の文言"),
    (("battle_end_faint_confirmed",), "3 体目のひんしから確定"),
    (("battle_end_result",), "リザルト画面"),
    (("battle_end_rank",), "ランク画面"),
)
RESULT_JA = {"win": "勝ち", "loss": "負け"}
RESULT_UNKNOWN = "勝敗は未確定 (レートの増減・ひんし数から推定)"


def battle_end_notice(state: dict, fired: list) -> Optional[dict]:
    """state (BattleStateV2.to_dict) と発火イベントから通知を作る。通知が無ければ None。
    戻り値の battle_end=True は終了の確定 (呼び出し側が 1 対戦 1 回に抑える)、end_hint は見込み/取り消し"""
    fired = fired or []
    if "battle_end_faint" in fired:
        hint = state.get("end_hint") or {}
        who = "自分" if hint.get("side") == "player" else "相手"
        return {"ok": False, "kind": "battle", "end_hint": True,
                "reason": f"{who}の選出 3 体がひんし → 対戦終了の見込み "
                          f"({BATTLE_END_FAINT_CONFIRM_SEC:.0f} 秒以内に交代が無ければ確定)"}
    if "battle_end_faint_cancel" in fired:
        return {"ok": False, "kind": "battle", "end_hint": False,
                "reason": "対戦終了の見込みを取り消し (交代を観測)"}
    basis = next((label for ids, label in END_BASIS if any(f in fired for f in ids)), None)
    if not basis:
        return None
    result = RESULT_JA.get(state.get("outcome") or "", RESULT_UNKNOWN)
    return {"ok": False, "kind": "battle", "battle_end": True, "reason": f"対戦終了: {result} ({basis})"}
