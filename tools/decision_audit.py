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
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

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

    # 実際の選出: 選出情報を持つ最後の scene レコードの picked フラグ
    picked_idx = None
    for d in records:
        if d.get("type") != "scene":
            continue
        party = ((d.get("state") or {}).get("player") or {}).get("party") or []
        picked = [i for i, p in enumerate(party) if p.get("picked")]
        if picked:
            picked_idx = picked
    # 実際の先発: 最初の switch_player の直後の自分active
    actual_lead = None
    for i, d in enumerate(records):
        if d.get("type") == "events" and "switch_player" in (d.get("fired") or []):
            _sid, ja = _next_active_species(records, i + 1)
            actual_lead = ja
            break

    members_match = (picked_idx is not None
                     and sorted(picked_idx) == sorted(rec_idx))
    lead_match = (actual_lead is not None and lead_name is not None
                  and actual_lead == lead_name)
    return {
        "recommend_names": rec_names, "recommend_lead": lead_name,
        "picked_indexes": picked_idx, "actual_lead": actual_lead,
        "members_match": members_match if picked_idx is not None else None,
        "lead_match": lead_match if actual_lead is not None else None,
    }


def audit_battle(records: list, late_sec: float = LATE_SEC,
                 heavy_swing: float = HEAVY_SWING) -> dict:
    """1対戦分のレコード列から決定監査の結果を作る (純粋関数)"""
    outcome = next((d.get("outcome") for d in records
                    if d.get("type") == "outcome"), "unknown")
    swings = _hp_swing_by_turn(records)

    # 決定画面の追跡。行動イベントは解決シーン (field) に入ってから記録される
    # ため、「開いている決定」と「直近に閉じた決定」の両方を保持し、
    # 行動イベント側でどちらかに紐付ける
    cur_open = None       # {"t": 開いた時刻, "first_adv": 最初の助言時刻}
    last_closed = None    # 同上 (fieldへ遷移した時点で退避)
    pending = None        # (t, advice) 最後に見た battle 助言
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
                    cur_open = {"t": t_open, "first_adv": first_adv}
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
            if adv.get("best") or adv.get("actions"):
                pending = (d.get("t", 0), adv)
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
            continue

        t_adv, adv = pending
        best = adv.get("best") or (adv.get("actions") or [{}])[0]
        actions = adv.get("actions") or []
        margin = None
        if len(actions) >= 2 and actions[0].get("score") is not None \
                and actions[1].get("score") is not None:
            margin = round(actions[0]["score"] - actions[1]["score"], 1)
        row["advice"] = {"kind": best.get("kind"), "id": best.get("id"),
                         "name": best.get("name"), "score": best.get("score"),
                         "margin": margin}

        # 一致判定 (review_battle と同じ規則)
        if act[0] == "move":
            agree = best.get("kind") == "move" and best.get("id") == act[1]
        else:
            sid, ja = _next_active_species(records, i + 1)
            row["executed_id"] = sid or row["executed_id"]
            row["executed_ja"] = ja
            agree = best.get("kind") == "switch" and \
                (sid is None or best.get("id") == sid)
        row["agree"] = agree
        if not agree:
            row["flags"].append("mismatch")

        # 遅延 (決定画面が開いてから最初の助言まで)。開いている決定を優先し、
        # 無ければ直近に閉じた決定 (行動が解決シーンで記録されるケース)
        window = cur_open or last_closed
        latency = None
        if window and window["t"] is not None \
                and window["first_adv"] is not None \
                and d.get("t", 0) - window["t"] <= PAIR_WINDOW_SEC:
            latency = round(max(0.0, window["first_adv"] - window["t"]), 2)
        row["latency"] = latency
        if latency is not None and latency > late_sec:
            row["flags"].append("late")

        if row["swing"] <= heavy_swing:
            row["flags"].append("heavy_swing")

        decisions.append(row)
        pending = None
        cur_open = None
        last_closed = None

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
    if lk:
        lines.append(f"  {late_sec:.0f}秒以内の助言 {tm}/{lk} ({tm / lk:.0%})"
                     f" / 最大遅延 {audit['max_latency']:.1f}秒")
    else:
        lines.append("  遅延: 決定画面の開始が捉えられず未計測")
    sel = audit["selection"]
    if sel:
        mm = {True: "一致", False: "不一致", None: "確認不能"}[sel["members_match"]]
        lm = {True: "一致", False: "不一致", None: "確認不能"}[sel["lead_match"]]
        lines.append(f"  選出: メンバー{mm} / 先発{lm} "
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
    files = sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))
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
        print("テストAの合格基準: 助言あり100% / 一致100% / 時間内100%。"
              "欠陥0でないなら ⚠ の行を1件ずつ潰す")


if __name__ == "__main__":
    main()
