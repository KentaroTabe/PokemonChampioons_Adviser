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
# 推定もできなかったとき (記録は unknown)
RESULT_NOT_INFERRED = "勝敗は未確定 (勝負の文言を読めず、レート・ひんしの観測からも決まらない)"


def result_text(outcome: Optional[str], recorded: Optional[dict] = None) -> str:
    """終了の通知に出す勝敗 (純粋)。outcome = 対戦状態の勝敗 (勝負の文言などで確定した値)、recorded = 対戦ログに記録した勝敗
    (BattleLogger.outcome_info: {"outcome", "inferred", "basis_text"})。
    確定していなければ、推定した結果とその根拠を出す (2026-10-06 第18回接続テスト: 「推定」とだけ出て、どちらと推定したかが
    分からなかった。15 戦のうち推定 3・不明 2 で、推定の 1 つは誤りだった)"""
    if outcome in RESULT_JA:
        return RESULT_JA[outcome]
    if recorded is None:
        return RESULT_UNKNOWN
    got = recorded.get("outcome")
    if got in RESULT_JA and recorded.get("inferred"):
        why = recorded.get("basis_text")
        return f"{RESULT_JA[got]}と推定" + (f" [{why}]" if why else "")
    if got in RESULT_JA:
        return RESULT_JA[got]
    return RESULT_NOT_INFERRED


def outcome_revision_notice(revision: Optional[dict]) -> Optional[dict]:
    """勝敗の推定を、後から読めたレートで更新したときの通知 (純粋)。revision = BattleLogger.pop_revision"""
    if not revision or revision.get("outcome") not in RESULT_JA:
        return None
    before = RESULT_JA.get(revision.get("revised_from") or "", "未確定")
    why = revision.get("basis_text")
    return {"ok": False, "kind": "battle", "battle_end": True,
            "reason": f"勝敗の推定を更新: {before} → {RESULT_JA[revision['outcome']]}と推定" + (f" [{why}]" if why else "")}


def battle_end_notice(state: dict, fired: list, recorded: Optional[dict] = None) -> Optional[dict]:
    """state (BattleStateV2.to_dict) と発火イベントから通知を作る。通知が無ければ None。
    戻り値の battle_end=True は終了の確定 (呼び出し側が 1 対戦 1 回に抑える)、end_hint は見込み/取り消し。
    recorded は対戦ログに記録した勝敗 (推定の結果と根拠を出すのに使う。result_text)"""
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
    result = result_text(state.get("outcome"), recorded)
    return {"ok": False, "kind": "battle", "battle_end": True, "reason": f"対戦終了: {result} ({basis})"}
