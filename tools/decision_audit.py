"""決定監査: テストA (アドバイザー追従) の対戦ログから決定ごとの妥当性指標を作る。

    python -m tools.decision_audit                 # 今日の対戦すべて
    python -m tools.decision_audit --battle <log>
    python -m tools.decision_audit --last 3        # 直近3対戦
    python -m tools.decision_audit --json          # 機械可読出力 (集計・回帰用)

接続テストA (全操作をアドバイス通りに行う) では、1決定 = 1テストケースになる。
各決定について以下を測る:

  1. 助言があったか      (無い決定 = 配信の欠落。テストAでは即不具合)
  2. 間に合ったか        (決定画面が開いてから助言が出るまでの秒数)
  3. 従えたか            (実際の行動と助言の一致。テストAでは100%が合格。
                          不一致 = 読み取り誤り・帰属誤り・UIの曖昧さのどれか)
  4. 結果はどうだったか  (そのターンのHP差分スイング。大きな失点の決定を
                          分岐点候補として挙げる。妥当性の断定ではなく監査対象の抽出)

review_battle (一致率と分岐点の振り返り) の決定紐付けロジックを土台に、
接続テスト用の欠陥検出 (欠落/遅延/不一致) と決定単位の集計を追加したもの。

判定不能の扱い (2026-10-07 確認、docs/USEFULNESS_VERIFICATION_PLAN_1007.md §10):
  - 遅延が測れない決定 (決定画面の開始を捉えられない) は「時間内」に数えない (n_timely の分母 n_latency_known からも除く)。
    件数は n_latency_unknown に別に出す。前の決定の助言が画面に残っていて遅延 0 とした決定は n_latency_carried に数える
  - 表示: display の行 (ブラウザが描画した時刻) で、決定の間の助言が表示されたかを分ける。
    shown (表示) / hidden (タブが隠れていて描画されていない) / not_shown (display の行はあるがこの助言の行が無い = 表示なし) /
    unknown (ログに display の行が 1 つも無い、または助言に advice_id が無い = 判定不能)
  - 時計差: display の行の t (サーバーの受信時刻) と t_shown (ブラウザの時計) の差が DECISION_AUDIT_CLOCK_SKEW_SEC を超えたら、
    ブラウザの時計で測った表示までの遅れ (display_latency) を判定不能にする (clock_skew)
  - 表示なし・時計差は欠陥の flags には入れない (欠陥の件数の意味は従来のまま)。件数を別に出す
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

from champions_agent.config import DECISION_AUDIT_CLOCK_SKEW_SEC
from tools.battle_outcome import outcome_from_records
from vision.scenes import (
    SCENE_COMMAND, SCENE_FIELD_CHECK, SCENE_MOVE_SELECT, SCENE_STANDBY,
    SCENE_WATCH,
)

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
# 接続テスト開始マーカー (analyze_battles と同じ運用。--session で参照)
MARKER = REPO / "logs" / ".connection_test_start"

# 助言の「遅い」判定 (決定画面が開いてから助言が出るまでの許容秒数)。
# COMMANDの持ち時間内で「読む→操作する」余裕を残す値として設定。
# 実測して合わない場合は --late-sec で上書きする
LATE_SEC = 10.0
# 「大きな失点」として監査対象に挙げるHP差分スイングの閾値 (%)。
# swing = そのターンの自分HP増減 - 相手HP増減 (負 = こちらが差し引き失点)
HEAVY_SWING = -25.0
# 助言と行動の紐付け上限秒 (review_battle と同じ値)
PAIR_WINDOW_SEC = 60.0


def _load(path: str) -> list:
    out = []
    for line in open(path):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _player_action(rec: dict):
    for f in rec.get("fired") or []:
        if f.startswith("move_player_"):
            return ("move", f[len("move_player_"):])
        if f == "switch_player":
            return ("switch", None)
    return None


def _next_active_species(records: list, i: int):
    """records[i] 以降で最初の対戦シーンの自分activeの種族id/和名"""
    for d in records[i:i + 12]:
        if d.get("type") != "scene":
            continue
        pl = (d.get("state") or {}).get("player") or {}
        idx = pl.get("active")
        party = pl.get("party") or []
        if idx is not None and 0 <= idx < len(party):
            return party[idx].get("species"), party[idx].get("ja")
    return None, None


def _hp_swing_by_turn(records: list) -> dict:
    """turn -> (自分HP増減合計, 相手HP増減合計)。hpレコードのdetailから集計"""
    swings: dict = {}
    for d in records:
        if d.get("type") != "hp":
            continue
        det = d.get("detail") or {}
        turn = d.get("turn")
        if turn is None or det.get("from") is None or det.get("to") is None:
            continue
        own, opp = swings.get(turn, (0.0, 0.0))
        delta = float(det["to"]) - float(det["from"])
        if det.get("side") == "player":
            own += delta
        elif det.get("side") == "opponent":
            opp += delta
        swings[turn] = (own, opp)
    return swings


def _display_index(records: list) -> dict:
    """display の行の索引 (純粋): {"has_rows": ログに display の行があるか,
    "shown": {advice_id: (t_shown, t_recv)} (最初の表示), "hidden": {advice_id: (t_shown, t_recv)}}。
    t_recv はサーバーが display の行を受け取った時刻 (行の t)、t_shown はブラウザの時計"""
    shown: dict = {}
    hidden: dict = {}
    has_rows = False
    for d in records:
        if d.get("type") != "display":
            continue
        has_rows = True
        aid = d.get("advice_id")
        if not aid or d.get("t_shown") is None:
            continue
        t_recv = float(d["t"]) if d.get("t") is not None else None
        target = hidden if d.get("hidden") else shown
        target.setdefault(aid, (float(d["t_shown"]), t_recv))
    return {"has_rows": has_rows, "shown": shown, "hidden": hidden}


def display_status(advice_ids: list, t_gen_of: dict, index: dict,
                   clock_skew_sec: float = DECISION_AUDIT_CLOCK_SKEW_SEC) -> dict:
    """決定の間に出た助言 (advice_ids) の表示の状態 (純粋)。
    {"display": shown / hidden / not_shown / unknown, "display_latency": 表示 − 生成 (秒、ブラウザの時計) or None,
     "clock_offset": t_shown − t_recv (秒) or None, "clock_skew": bool}。
    時計差 (|clock_offset| > clock_skew_sec) のときは display_latency を None (判定不能) にする"""
    out = {"display": "unknown", "display_latency": None, "clock_offset": None, "clock_skew": False}
    ids = [a for a in advice_ids if a]
    if not ids or not index.get("has_rows"):
        return out
    shown = [(index["shown"][a][0], index["shown"][a][1], a) for a in ids if a in index["shown"]]
    if shown:
        t_shown, t_recv, aid = min(shown)
        out["display"] = "shown"
        if t_recv is not None:
            out["clock_offset"] = round(t_shown - t_recv, 3)
            out["clock_skew"] = abs(t_shown - t_recv) > clock_skew_sec
        t_gen = t_gen_of.get(aid)
        if t_gen is not None and not out["clock_skew"]:
            out["display_latency"] = round(t_shown - float(t_gen), 3)
        return out
    out["display"] = "hidden" if any(a in index["hidden"] for a in ids) else "not_shown"
    return out


def _selection_audit(records: list) -> dict | None:
    """選出助言 (kind=selection, 完了時点の推奨) と実際の選出の突き合わせ"""
    final_rec = None
    for d in records:
        if d.get("type") == "advice" and d.get("kind") == "selection":
            adv = d.get("advice") or {}
            if adv.get("recommend"):
                final_rec = adv
    if final_rec is None:
        return None
    rec_idx = [r.get("index") for r in final_rec["recommend"]]
    rec_names = [r.get("name") for r in final_rec["recommend"]]
    lead_name = next((r.get("name") for r in final_rec["recommend"]
                      if r.get("lead")), rec_names[0] if rec_names else None)

    # 実際の選出: picked フラグが最も揃っている時点 (同数なら後の方) を使う。
    # 選出途中 (1/3) のスナップショットと比較すると偽の不一致になる
    picked_idx = None
    for d in records:
        if d.get("type") != "scene":
            continue
        party = ((d.get("state") or {}).get("player") or {}).get("party") or []
        picked = [i for i, p in enumerate(party) if p.get("picked")]
        if picked and (picked_idx is None or len(picked) >= len(picked_idx)):
            picked_idx = picked
    # 実際の先発: 最初の switch_player の直後の自分active
    actual_lead = None
    for i, d in enumerate(records):
        if d.get("type") == "events" and "switch_player" in (d.get("fired") or []):
            _sid, ja = _next_active_species(records, i + 1)
            actual_lead = ja
            break

    # 実際に場に出た自分側の種族 (バトル中シーンのactiveの和名を順に収集)。
    # 選出画面のpickedフラグは抽出が部分的なことがある (実測: 1体目までしか
    # 取れず、しかも一度Trueに立った後Falseへ戻った) ため、picked が
    # 揃っていない場合はこちらをグラウンドトゥルースにする
    observed = []
    for d in records:
        if d.get("type") != "scene":
            continue
        pl = (d.get("state") or {}).get("player") or {}
        idx, party = pl.get("active"), pl.get("party") or []
        if idx is not None and 0 <= idx < len(party):
            ja = party[idx].get("ja")
            if ja and ja not in observed:
                observed.append(ja)

    if picked_idx is not None and len(picked_idx) == len(rec_idx):
        members_match = sorted(picked_idx) == sorted(rec_idx)
        members_basis = "picked"
    elif observed:
        if any(ja not in rec_names for ja in observed):
            members_match = False       # 推奨外の種族が場に出た = 確実に逸脱
        elif len(observed) >= len(rec_names):
            members_match = True
        else:
            members_match = None        # 全員は確認できないが矛盾なし
        members_basis = f"observed:{len(observed)}体"
    else:
        members_match = None
        members_basis = "none"
    lead_match = (actual_lead is not None and lead_name is not None
                  and actual_lead == lead_name)
    return {
        "recommend_names": rec_names, "recommend_lead": lead_name,
        "picked_indexes": picked_idx, "actual_lead": actual_lead,
        "observed_members": observed,
        "members_basis": members_basis,
        "members_match": members_match,
        "lead_match": lead_match if actual_lead is not None else None,
    }


def audit_battle(records: list, late_sec: float = LATE_SEC,
                 heavy_swing: float = HEAVY_SWING) -> dict:
    """1対戦分のレコード列から決定監査の結果を作る (純粋関数)"""
    # outcome 行は最後のもの、勝負の文言があればそれを採る (訂正の行と文言優先。tools.battle_outcome)
    outcome = outcome_from_records(records)[0] or "unknown"
    swings = _hp_swing_by_turn(records)
    disp_index = _display_index(records)
    t_gen_of = {d["advice_id"]: ((d.get("advice") or {}).get("t_gen") or d.get("t"))
                for d in records if d.get("type") == "advice" and d.get("advice_id")}

    # 決定画面の追跡。行動イベントは解決シーン (field) に入ってから記録される
    # ため、「開いている決定」と「直近に閉じた決定」の両方を保持し、
    # 行動イベント側でどちらかに紐付ける
    cur_open = None       # {"t": 開いた時刻, "first_adv": 最初の助言時刻, "carried": 前の助言で遅延0とした}
    last_closed = None    # 同上 (fieldへ遷移した時点で退避)
    pending = None        # (t, advice, advice_id) 最後に見た battle 助言
    window_bests = []     # この決定の間に表示された best の履歴 (t, best, advice_id) (churn検出用)
    seen_decision_ctx = False   # command画面かbattle助言を一度でも見たか
    decisions = []

    for i, d in enumerate(records):
        typ = d.get("type")
        if typ == "scene":
            scene = d.get("scene")
            if scene in (SCENE_COMMAND, SCENE_MOVE_SELECT):
                seen_decision_ctx = True
                if cur_open is None:
                    t_open = d.get("t")
                    # 前の状態向けの助言が既に画面にあるなら遅延0扱い
                    # (フロントは直近助言を表示し続けるため実用上は即時)
                    first_adv = t_open if (
                        pending is not None and t_open is not None
                        and t_open - pending[0] <= PAIR_WINDOW_SEC) else None
                    cur_open = {"t": t_open, "first_adv": first_adv,
                                "carried": first_adv is not None}
            elif scene in (SCENE_WATCH, SCENE_FIELD_CHECK, SCENE_STANDBY):
                pass   # 決定中の情報確認画面。決定は開いたまま (往復対策)
            elif cur_open is not None:
                # field 等 = 行動が解決へ進んだ。行動イベントはこの後に
                # 記録されるので、閉じた決定として1件だけ持ち越す
                last_closed = cur_open
                cur_open = None
            continue
        if typ == "advice" and d.get("kind") == "battle":
            adv = d.get("advice") or {}
            if adv.get("provisional"):
                continue   # 確定前 (フロント未表示) の助言は表示履歴に数えない
            if adv.get("best") or adv.get("actions"):
                aid = d.get("advice_id") or adv.get("advice_id")
                pending = (d.get("t", 0), adv, aid)
                b = adv.get("best") or (adv.get("actions") or [{}])[0]
                window_bests.append((d.get("t", 0), b, aid))
                seen_decision_ctx = True
                if cur_open is not None and cur_open["first_adv"] is None:
                    cur_open["first_adv"] = d.get("t")
            continue
        if typ != "events":
            continue
        act = _player_action(d)
        if act is None:
            continue
        # 先発の繰り出し (対戦冒頭、決定画面もbattle助言もまだ無い) は
        # バトル中の決定ではなく選出の一部。選出監査側で評価する
        if act[0] == "switch" and not seen_decision_ctx:
            continue

        # --- 1決定 ---
        turn = d.get("turn")
        row = {"turn": turn, "t": d.get("t"), "executed_kind": act[0],
               "executed_id": act[1], "flags": []}
        own, opp = swings.get(turn, (0.0, 0.0))
        row["swing"] = round(own - opp, 1)

        if pending is None or d.get("t", 0) - pending[0] > PAIR_WINDOW_SEC:
            row["advice"] = None
            row["flags"].append("no_advice")
            decisions.append(row)
            cur_open = None
            last_closed = None
            window_bests = []
            continue

        t_adv, adv, pending_aid = pending
        best = adv.get("best") or (adv.get("actions") or [{}])[0]
        actions = adv.get("actions") or []
        margin = None
        if len(actions) >= 2 and actions[0].get("score") is not None \
                and actions[1].get("score") is not None:
            margin = round(actions[0]["score"] - actions[1]["score"], 1)
        row["advice"] = {"kind": best.get("kind"), "id": best.get("id"),
                         "name": best.get("name"), "score": best.get("score"),
                         "margin": margin}

        # 一致判定。最後の助言だけでなく、この決定の間に表示された
        # いずれかの best と一致すれば「追従できた」とみなす。
        # 助言は状態更新のたびに再計算され、ボタンを押した後に反転する
        # ことがある (実測: turn 1 でブレイブバードを6秒表示→押下後0.2秒で
        # すてみタックルへ反転)。最後の1件とだけ比べると偽の不一致になる
        def _matches(b) -> bool:
            if act[0] == "move":
                return b.get("kind") == "move" and b.get("id") == act[1]
            return b.get("kind") == "switch" and \
                (row.get("executed_id") is None
                 or b.get("id") == row.get("executed_id"))

        if act[0] != "move":
            sid, ja = _next_active_species(records, i + 1)
            row["executed_id"] = sid or row["executed_id"]
            row["executed_ja"] = ja
        distinct = {(b.get("kind"), b.get("id")) for _t, b, _a in window_bests}
        row["churn"] = len(distinct) > 1
        agree_last = _matches(best)
        agree_any = agree_last or any(_matches(b) for _t, b, _a in window_bests)
        row["agree"] = agree_any
        if not agree_any:
            row["flags"].append("mismatch")
        elif not agree_last:
            # 押下後の反転に追従が食われたケース (欠陥ではないが要観察)
            row["followed_earlier_best"] = True

        # 遅延 (決定画面が開いてから最初の助言まで)。開いている決定を優先し、
        # 無ければ直近に閉じた決定 (行動が解決シーンで記録されるケース)
        window = cur_open or last_closed
        latency = None
        if window and window["t"] is not None \
                and window["first_adv"] is not None \
                and d.get("t", 0) - window["t"] <= PAIR_WINDOW_SEC:
            latency = round(max(0.0, window["first_adv"] - window["t"]), 2)
        row["latency"] = latency
        # 遅延の根拠: measured (決定画面の後に助言が出た) / carried (前の助言が残っていて 0 とした) / None (判定不能)
        row["latency_basis"] = None if latency is None else (
            "carried" if (window or {}).get("carried") else "measured")
        if latency is not None and latency > late_sec:
            row["flags"].append("late")

        # 表示 (display の行) と時計差。欠陥の flags には入れず、件数を別に出す
        ids = [a for _t, _b, a in window_bests] + [pending_aid]
        row.update(display_status(list(dict.fromkeys(ids)), t_gen_of, disp_index))

        if row["swing"] <= heavy_swing:
            row["flags"].append("heavy_swing")

        decisions.append(row)
        pending = None
        cur_open = None
        last_closed = None
        window_bests = []

    n = len(decisions)
    with_adv = [x for x in decisions if x.get("advice")]
    agreed = [x for x in with_adv if x.get("agree")]
    lat_known = [x for x in with_adv if x.get("latency") is not None]
    timely = [x for x in lat_known if x["latency"] <= late_sec]
    return {
        "outcome": outcome,
        "n_decisions": n,
        "n_with_advice": len(with_adv),
        "n_agree": len(agreed),
        "n_latency_known": len(lat_known),
        "n_timely": len(timely),
        # 判定不能の内訳 (時間内にも欠陥にも数えない): 遅延が測れない / 前の助言で 0 とした
        "n_latency_unknown": len(with_adv) - len(lat_known),
        "n_latency_carried": sum(1 for x in lat_known if x.get("latency_basis") == "carried"),
        # 表示の内訳 (助言のある決定ごと): 表示 / 隠れたタブ / 表示なし / 判定不能 (display の行なし)、時計差
        "has_display_rows": disp_index["has_rows"],
        "n_display_shown": sum(1 for x in with_adv if x.get("display") == "shown"),
        "n_display_hidden": sum(1 for x in with_adv if x.get("display") == "hidden"),
        "n_display_not_shown": sum(1 for x in with_adv if x.get("display") == "not_shown"),
        "n_display_unknown": sum(1 for x in with_adv if x.get("display") == "unknown"),
        "n_clock_skew": sum(1 for x in with_adv if x.get("clock_skew")),
        "n_churn": sum(1 for x in with_adv if x.get("churn")),
        "max_latency": max((x["latency"] for x in lat_known), default=None),
        "decisions": decisions,
        "selection": _selection_audit(records),
        "defects": [x for x in decisions if x["flags"]],
    }


def _ja_move(mid):
    """技IDの和名解決 (失敗時はIDのまま)"""
    try:
        from vision.normalize import NameResolver
        if not hasattr(_ja_move, "_r"):
            _ja_move._r = NameResolver()
        return _ja_move._r.ja_of("moves", mid) or mid
    except Exception:
        return mid


def render_text(name: str, audit: dict, late_sec: float) -> str:
    o = {"win": "勝ち", "loss": "負け"}.get(audit["outcome"], "不明")
    lines = [f"📋 決定監査: {name} → {o}"]
    n, wa = audit["n_decisions"], audit["n_with_advice"]
    if n == 0:
        lines.append("  決定 (自分の行動イベント) がログにありません")
        return "\n".join(lines)
    ag, lk, tm = audit["n_agree"], audit["n_latency_known"], audit["n_timely"]
    lines.append(f"  決定 {n} / 助言あり {wa} ({wa / n:.0%})"
                 f" / 一致 {ag}/{wa} ({ag / wa:.0%})" if wa else
                 f"  決定 {n} / 助言あり 0")
    lu, lc = audit.get("n_latency_unknown", 0), audit.get("n_latency_carried", 0)
    if lk:
        lines.append(f"  {late_sec:.0f}秒以内の助言 {tm}/{lk} ({tm / lk:.0%})"
                     f" / 最大遅延 {audit['max_latency']:.1f}秒"
                     + (f" / 判定不能 {lu} 件 (時間内に数えない)" if lu else "")
                     + (f" / うち前の助言が残っていて 0 秒とした {lc} 件" if lc else ""))
    else:
        lines.append("  遅延: 決定画面の開始が捉えられず未計測"
                     + (f" (判定不能 {lu} 件)" if lu else ""))
    if wa:
        if audit.get("has_display_rows"):
            lines.append(f"  表示: 表示 {audit.get('n_display_shown', 0)} / 隠れたタブ {audit.get('n_display_hidden', 0)}"
                         f" / 表示なし {audit.get('n_display_not_shown', 0)}"
                         f" / 判定不能 {audit.get('n_display_unknown', 0)}"
                         f" / 時計差 {audit.get('n_clock_skew', 0)} (助言のある決定 {wa} 件)")
        else:
            lines.append(f"  表示: display の行が無いログ → 判定不能 {wa} 件")
    if audit.get("n_churn"):
        lines.append(f"  ⟳ 決定中に推奨が入れ替わった決定: {audit['n_churn']}件"
                     " (追従テストの読みやすさに影響。多いなら要ヒステリシス)")
    sel = audit["selection"]
    if sel:
        mm = {True: "一致", False: "不一致", None: "確認不能"}[sel["members_match"]]
        lm = {True: "一致", False: "不一致", None: "確認不能"}[sel["lead_match"]]
        basis = sel.get("members_basis", "")
        basis_note = f" [{basis}]" if basis and basis != "picked" else ""
        lines.append(f"  選出: メンバー{mm}{basis_note} / 先発{lm} "
                     f"(推奨: {' / '.join(sel['recommend_names'])},"
                     f" 先発 {sel['recommend_lead']})")
    for x in audit["defects"]:
        adv = x.get("advice") or {}
        label = {"no_advice": "助言なし", "mismatch": "不一致",
                 "late": "遅延", "heavy_swing": "大失点"}
        tags = ",".join(label[f] for f in x["flags"])
        actual = x.get("executed_ja") or x.get("executed_id") or "?"
        if x["executed_kind"] == "move" and x.get("executed_id"):
            actual = _ja_move(x["executed_id"])
        line = (f"  ⚠ [{tags}] turn {x['turn']}: 実際 {x['executed_kind']}:{actual}")
        if adv:
            line += f" / 推奨 {adv.get('kind')}:{adv.get('name') or adv.get('id')}"
        if x.get("latency") is not None and "late" in x["flags"]:
            line += f" / 遅延{x['latency']:.1f}秒"
        if "heavy_swing" in x["flags"]:
            line += f" / swing {x['swing']:+.0f}%"
        lines.append(line)
    if not audit["defects"]:
        undecided = []
        if lu:
            undecided.append(f"遅延の判定不能 {lu}")
        n_nd = audit.get("n_display_not_shown", 0) + audit.get("n_display_hidden", 0)
        if n_nd:
            undecided.append(f"表示されなかった助言 {n_nd}")
        if audit.get("n_display_unknown", 0):
            undecided.append(f"表示の判定不能 {audit['n_display_unknown']}")
        if audit.get("n_clock_skew", 0):
            undecided.append(f"時計差 {audit['n_clock_skew']}")
        if undecided:
            lines.append("  ✅ 欠陥なし (助言あり・一致・時間内)。ただし成功に含めていないもの: " + " / ".join(undecided))
        else:
            lines.append("  ✅ 欠陥なし (全決定: 助言あり・一致・時間内)")
    return "\n".join(lines)


def _session_start_ts() -> float | None:
    """接続テスト開始マーカーの時刻 (無ければ None)"""
    try:
        return float(MARKER.read_text().strip())
    except (OSError, ValueError):
        return None


def _files_since(files: list, since_ts: float) -> list:
    return [f for f in files if Path(f).stat().st_mtime >= since_ts]


def _pick_files(args) -> list:
    if args.battle:
        return [args.battle]
    files = sorted(glob.glob(str(Path(getattr(args, "battles_dir", None) or BATTLE_DIR) / "*.jsonl")))
    if args.session:
        ts = _session_start_ts()
        if ts is not None:
            return _files_since(files, ts)
        # マーカーが無ければ今日分へフォールバック (下へ続く)
    if args.last:
        return files[-args.last:]
    today = time.strftime("%Y%m%d")
    picked = [f for f in files if Path(f).name.startswith(f"battle_{today}")]
    return picked or files[-1:]


def main() -> None:
    ap = argparse.ArgumentParser(description="テストA用の決定監査")
    ap.add_argument("--battle", default=None, help="対戦ログのパス")
    ap.add_argument("--last", type=int, default=None, help="直近N対戦")
    ap.add_argument("--session", action="store_true",
                    help="接続テスト開始マーカー以降の全対戦 "
                         "(end_connection_test.sh が使う)")
    ap.add_argument("--battles-dir", default=None,
                    help="対戦ログの置き場 (既定 logs/battles。worktree から本体のログを読むとき)")
    ap.add_argument("--late-sec", type=float, default=LATE_SEC)
    ap.add_argument("--heavy-swing", type=float, default=HEAVY_SWING)
    ap.add_argument("--json", action="store_true", help="機械可読出力")
    args = ap.parse_args()

    files = _pick_files(args)
    if not files:
        raise SystemExit("対戦ログがありません")

    audits = []
    for f in files:
        audit = audit_battle(_load(f), late_sec=args.late_sec,
                             heavy_swing=args.heavy_swing)
        audits.append((Path(f).name, audit))

    if args.json:
        print(json.dumps({name: a for name, a in audits},
                         ensure_ascii=False, indent=1))
        return

    for name, a in audits:
        print(render_text(name, a, args.late_sec))
        print()
    if len(audits) > 1:
        n = sum(a["n_decisions"] for _, a in audits)
        wa = sum(a["n_with_advice"] for _, a in audits)
        ag = sum(a["n_agree"] for _, a in audits)
        lk = sum(a["n_latency_known"] for _, a in audits)
        tm = sum(a["n_timely"] for _, a in audits)
        nd = sum(len(a["defects"]) for _, a in audits)
        print(f"===== 集計 ({len(audits)}対戦) =====")
        if n:
            print(f"決定 {n} / 助言あり {wa / n:.0%}"
                  + (f" / 一致 {ag / wa:.0%}" if wa else "")
                  + (f" / 時間内 {tm / lk:.0%}" if lk else "")
                  + f" / 欠陥 {nd}件")
            tot = {k: sum(int(a.get(k) or 0) for _, a in audits)
                   for k in ("n_latency_unknown", "n_latency_carried", "n_display_shown", "n_display_hidden",
                             "n_display_not_shown", "n_display_unknown", "n_clock_skew")}
            print(f"判定不能 (成功に含めない): 遅延 {tot['n_latency_unknown']} (前の助言で 0 秒 {tot['n_latency_carried']} は時間内に含む)"
                  f" / 表示: 表示 {tot['n_display_shown']} 隠れたタブ {tot['n_display_hidden']}"
                  f" 表示なし {tot['n_display_not_shown']} 判定不能 {tot['n_display_unknown']} / 時計差 {tot['n_clock_skew']}")
        print("テストAの合格基準: 助言あり100% / 一致100% / 時間内100%。"
              "欠陥0でないなら ⚠ の行を1件ずつ潰す")


if __name__ == "__main__":
    main()
