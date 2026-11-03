"""決定対応表: 対戦ログから「決定 1 件 1 行」の表を作る (docs/ADVICE_ACCURACY_PLAN_1009.md §1.2 の最初の成果物)。

    python -m tools.decision_table <battle.jsonl ...> [--out 出力先.md] [--json 出力先.json] [--check-expected]

`tools.decision_audit.audit_battle` の決定 (監査が行動イベントと結び付けたもの) を土台に、決定ごとに次を足す。
  - キー: ログ名 + 決定時刻 (t) + 行動イベント (events 行) の行番号。turn は欠落・重複があるため参考欄にとどめる
  - 関連する advice_id (この決定の間に出た battle 助言の全部。確定前 provisional の助言は監査と同じく除く)
  - 場面、自分の場・相手の場の個体 (形態つき)、助言の上位 3、プレイヤーの行動、一致/不一致、利用可能性
  - 助言時点の状態の不確かさ (hp_stale / my_hp_estimated / opp_pending / 相手の技の既知数)

監査の判定ロジックは変えず、audit_battle の戻り値 (decisions) を events 行・advice 行に結び付けて読むだけ。
audit_battle は決定の行に events 行の位置と advice_id の一覧を持たないので、同じ走査の規則 (決定ごとに window_bests を
リセット、provisional は数えない) をここで再現して補う。再現がずれていないことは、行ごとの t の一致と、
決定の最終助言の best が audit_battle の row["advice"] と一致することで確かめる (一致しなければ警告を出す)。

分類 (調査での 7 分類) は人が付けるもので、このスクリプトは付けない。docs 側の表にキーで結ぶ。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from tools.decision_audit import _player_action, audit_battle

REPO = Path(__file__).resolve().parent.parent
DEX_PATH = REPO / "advisor" / "data" / "dex.json"

# 2026-10-09 に計画書 §1.1 が報告した再集計の値 (10 戦)。--check-expected で突き合わせる
EXPECTED_1009 = {"n_decisions": 69, "n_with_advice": 69, "n_agree": 26, "n_mismatch": 43,
                 "n_available_yes": 62, "n_available_no": 0, "n_available_unknown": 7}

TOP_N = 3


def _load_with_lines(path: str) -> tuple[list, list]:
    """(records, 行番号 (1 始まり、ファイルの行)) を返す。壊れた行は audit の _load と同じく読み飛ばす"""
    recs, lines = [], []
    with open(path) as fh:
        for no, line in enumerate(fh, 1):
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
            lines.append(no)
    return recs, lines


def _is_usable_battle_advice(d: dict) -> bool:
    """audit_battle が表示履歴 (window_bests) に数える battle 助言か: kind=battle・確定済み・best か actions がある"""
    if d.get("type") != "advice" or d.get("kind") != "battle":
        return False
    adv = d.get("advice") or {}
    return not adv.get("provisional") and bool(adv.get("best") or adv.get("actions"))


def _aid(d: dict) -> Optional[str]:
    return d.get("advice_id") or (d.get("advice") or {}).get("advice_id")


def link_decisions(records: list, decisions: list) -> list:
    """決定 (audit_battle の decisions) ごとに、events 行の位置と、その決定に属する battle 助言の行の位置を求める (純粋)。

    決定は行動イベント (events 行で _player_action が値を返すもの) と 1 対 1 に、出現順で対応する。先発の繰り出しなど
    監査が数えない行動イベントは飛ばされるので、t が一致する最初の未使用イベントを順に探す。
    返り値: [{"ev_index": records の添字, "adv_indexes": [助言の行の添字 (生成順)]}] (決定と同じ長さ・順)"""
    action_idx = [i for i, d in enumerate(records) if d.get("type") == "events" and _player_action(d) is not None]
    out, cursor, prev_ev = [], 0, -1
    for row in decisions:
        ev = None
        while cursor < len(action_idx):
            i = action_idx[cursor]
            cursor += 1
            if records[i].get("t") == row.get("t"):
                ev = i
                break
        if ev is None:
            raise ValueError(f"決定 t={row.get('t')} に対応する events 行が見つからない")
        advs = [i for i in range(prev_ev + 1, ev) if _is_usable_battle_advice(records[i])]
        out.append({"ev_index": ev, "adv_indexes": advs})
        prev_ev = ev
    return out


def _active(side: dict) -> dict:
    idx, party = side.get("active"), side.get("party") or []
    return party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else {}


def describe_mon(mon: dict) -> dict:
    """場の個体の要約: 種族 id・和名・メガ・HP・判明技の数 (純粋)"""
    return {"species": mon.get("species"), "ja": mon.get("ja"), "mega": bool(mon.get("mega")), "hp": mon.get("hp"),
            "types": mon.get("types"), "status": mon.get("status"), "boosts": mon.get("boosts") or {},
            "revealed": list(mon.get("revealed") or []), "moves": list(mon.get("moves") or []),
            "pending": bool(mon.get("pending")), "guess": bool(mon.get("guess"))}


def mon_label(m: dict) -> str:
    """表に出す個体の表記: 和名 (種族 id)[メガ] HP%"""
    if not m or not m.get("species"):
        return "(未同定)" + (f" {m['hp']:.0f}%" if m and m.get("hp") is not None else "")
    s = f"{m.get('ja') or m['species']}({m['species']})"
    if m.get("mega"):
        s += "[メガ]"
    s += f" {m['hp']:.0f}%" if m.get("hp") is not None else " HP?"
    return s


def top_actions(adv: dict, n: int = TOP_N) -> list:
    out = []
    for a in (adv.get("actions") or [])[:n]:
        out.append({"kind": a.get("kind"), "id": a.get("id"), "name": a.get("name"), "score": a.get("score")})
    return out


def _fmt_action(a: dict) -> str:
    kind = {"move": "技", "switch": "交代"}.get(a.get("kind"), a.get("kind"))
    sc = a.get("score")
    return f"{kind}:{a.get('name') or a.get('id')} {sc:.1f}" if sc is not None else f"{kind}:{a.get('name') or a.get('id')}"


def rank_of_player_action(row: dict, actions: list) -> Optional[int]:
    """最終助言の actions の中でのプレイヤーの行動の順位 (1 始まり)。候補に無ければ None (純粋)"""
    kind, ident = row.get("executed_kind"), row.get("executed_id")
    for i, a in enumerate(actions, 1):
        if a.get("kind") == kind and (ident is None or a.get("id") == ident):
            return i
    return None


def _player_label(row: dict, move_ja) -> str:
    if row.get("executed_kind") == "move":
        return f"技:{move_ja(row.get('executed_id'))}"
    return f"交代:{row.get('executed_ja') or row.get('executed_id')}"


def build_rows(path: str, move_ja=None, dex: Optional[dict] = None) -> tuple[list, dict]:
    """1 対戦のログから決定の行を作る。返り値: (行の列, audit_battle の戻り値)。副作用なし (ファイルの読み取りのみ)"""
    records, lineno = _load_with_lines(path)
    audit = audit_battle(records)
    links = link_decisions(records, audit["decisions"])
    move_ja = move_ja or (lambda x: x)
    moves = (dex or {}).get("moves", {})
    name = Path(path).name
    rows = []
    for row, link in zip(audit["decisions"], links):
        advs = [records[i] for i in link["adv_indexes"]]
        aids = list(dict.fromkeys(a for a in (_aid(d) for d in advs) if a))
        last = advs[-1] if advs else None
        adv = (last or {}).get("advice") or {}
        st = (last or {}).get("state") or {}
        actions = adv.get("actions") or []
        me, opp = _active(st.get("player") or {}), _active(st.get("opponent") or {})
        ctx = (last or {}).get("context") or {}
        hp_stale = (last or {}).get("hp_stale") or {}
        opp_mon = describe_mon(opp)
        ex_id = row.get("executed_id")
        category = (moves.get(ex_id) or {}).get("category") if row.get("executed_kind") == "move" else None
        out = {
            "log": name, "t": row.get("t"), "event_line": lineno[link["ev_index"]],
            "turn": row.get("turn"),
            "advice_ids": aids, "advice_lines": [lineno[i] for i in link["adv_indexes"]],
            "scene": st.get("scene"), "advice_turn": (last or {}).get("turn"),
            "me": describe_mon(me), "opp": opp_mon,
            "top": top_actions(adv), "n_actions": len(actions),
            "player": {"kind": row.get("executed_kind"), "id": ex_id, "ja": row.get("executed_ja"),
                       "label": _player_label(row, move_ja), "category": category,
                       "rank": rank_of_player_action(row, actions),
                       "score": next((a.get("score") for a in actions if a.get("kind") == row.get("executed_kind")
                                      and a.get("id") == ex_id), None)},
            "agree": row.get("agree") if row.get("advice") else None,
            "followed_earlier_best": bool(row.get("followed_earlier_best")),
            "churn": bool(row.get("churn")), "flags": list(row.get("flags") or []),
            "available": row.get("available"), "available_reason": row.get("available_reason"),
            "display_state": row.get("display_state"), "latency": row.get("latency"),
            "latency_basis": row.get("latency_basis"), "swing": row.get("swing"),
            "uncertainty": {"hp_stale": hp_stale, "my_hp_estimated": bool(ctx.get("my_hp_estimated")),
                            "my_hp_source": ctx.get("my_hp_source"),
                            "opp_pending": bool(adv.get("opp_pending")),
                            "opp_revealed": len(opp_mon["revealed"]),
                            "me_revealed": len(describe_mon(me)["revealed"]), "me_moves": len(describe_mon(me)["moves"])},
            "margin": (row.get("advice") or {}).get("margin"),
            "result": row.get("result"),
            "speed_note": adv.get("speed_note"), "mega_note": adv.get("mega_note"),
            # 助言の注記に「ひんし: 交代先を選んでください」があるか (= 助言エンジンがひんし後の繰り出しと判断した決定)
            "faint_switch_advice": "ひんし" in (adv.get("speed_note") or ""),
            "provisional_only": bool(adv.get("provisional")),
        }
        # 再現の自己検査: 最終助言の best が audit_battle の row["advice"] と一致するか
        ra = row.get("advice")
        if ra is not None:
            b = adv.get("best") or (actions[0] if actions else {})
            if (b.get("kind"), b.get("id")) != (ra.get("kind"), ra.get("id")):
                out["link_warning"] = "最終助言の best が監査の row と一致しない"
        elif advs:
            out["link_warning"] = "監査は助言なしだが助言の行を結び付けた"
        rows.append(out)
    return rows, audit


def summarize(audits: list) -> dict:
    """audit_battle の戻り値の列から、計画書 §1.1 と同じ項目の合計を出す (純粋)"""
    s = {"n_decisions": sum(a["n_decisions"] for a in audits),
         "n_with_advice": sum(a["n_with_advice"] for a in audits),
         "n_agree": sum(a["n_agree"] for a in audits)}
    s["n_mismatch"] = s["n_with_advice"] - s["n_agree"]
    for k in ("n_available_yes", "n_available_no", "n_available_unknown", "n_display_hidden", "n_display_not_shown"):
        s[k] = sum(int(a.get(k) or 0) for a in audits)
    return s


def compare_expected(summary: dict, expected: dict = EXPECTED_1009) -> list:
    """期待値との差の一覧 (純粋)。空なら一致"""
    return [f"{k}: 再集計 {summary.get(k)} / 期待 {v}" for k, v in expected.items() if summary.get(k) != v]


def log_labels(names: list) -> dict:
    """ログ名 → 短い戦ラベル (純粋)。日付ごとに時刻順で a, b, ... を振り、日の数字を前に付ける (20261008_182620 → 8a)。
    初版の調査 (a6439a7c) の「8a T5」「9d T1」と同じ付け方"""
    out, count = {}, {}
    for n in sorted(names):
        stem = n.removeprefix("battle_").removesuffix(".jsonl")
        day = stem.split("_")[0]
        k = count.get(day, 0)
        count[day] = k + 1
        out[n] = f"{int(day[-2:])}{chr(ord('a') + k)}"
    return out


def assign_ids(rows: list) -> list:
    """各行に "id" (例 8a-T5。同じ戦・同じ turn に複数の決定があれば a, b, ... を付ける) と "no" (全体の連番) を足す (純粋・破壊的)"""
    labels = log_labels(sorted({r["log"] for r in rows}))
    seen = {}
    for r in rows:
        seen.setdefault((r["log"], r["turn"]), []).append(r)
    for i, r in enumerate(rows, 1):
        same = seen[(r["log"], r["turn"])]
        suffix = chr(ord("a") + same.index(r)) if len(same) > 1 else ""
        r["id"] = f"{labels[r['log']]}-T{r['turn'] if r['turn'] is not None else '?'}{suffix}"
        r["no"] = i
    return rows


def score_gap(r: dict) -> Optional[float]:
    """最終助言の第一候補のスコア − プレイヤーの行動のスコア (候補に無ければ None)。第一候補は据え置きで最高点とは限らない"""
    if not r["top"] or r["player"]["score"] is None or r["top"][0].get("score") is None:
        return None
    return round(r["top"][0]["score"] - r["player"]["score"], 1)


def per_log_summary(audits_by_log: dict) -> list:
    """戦ごとの集計 [{log, n, agree, mismatch, yes, unknown}] (純粋)"""
    out = []
    for name, a in audits_by_log.items():
        out.append({"log": name, "n": a["n_decisions"], "agree": a["n_agree"], "mismatch": a["n_with_advice"] - a["n_agree"],
                    "yes": a["n_available_yes"], "no": a["n_available_no"], "unknown": a["n_available_unknown"]})
    return out


def render_per_log(per_log: list, labels: dict) -> str:
    lines = ["| 戦 | ログ | 決定 | 一致 | 不一致 | 利用可能 yes | no | unknown |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in per_log:
        lines.append(f"| {labels[s['log']]} | {s['log']} | {s['n']} | {s['agree']} | {s['mismatch']} | {s['yes']} | {s['no']} | {s['unknown']} |")
    return "\n".join(lines)


def _cell(s) -> str:
    return str(s if s is not None else "-").replace("|", "\\|").replace("\n", " ")


def _uncertainty_label(u: dict) -> str:
    """助言時点の状態の不確かさの表記。HP 古 = 場の HP が最後に実際に読めてからの秒数 (未読 = 一度も読めていない)"""
    parts = []
    hs = u.get("hp_stale") or {}
    for side, key in (("自", "player"), ("相", "opponent")):
        v = hs.get(key)
        parts.append(f"{side}HP古 {v:.1f}s" if isinstance(v, (int, float)) else f"{side}HP未読")
    if u.get("my_hp_estimated"):
        parts.append("自HPバー推定")
    if u.get("opp_pending"):
        parts.append("相手対応待ち")
    parts.append(f"相技既知 {u.get('opp_revealed')}")
    return " / ".join(parts)


def compact_advice_ids(r: dict) -> str:
    """advice_id を「<ログ名>-NNNN」の NNNN 部分だけにして連ねる (ログ名が合わない id は全体を出す)"""
    prefix = r["log"].removeprefix("battle_").removesuffix(".jsonl") + "-"
    return ",".join(a[len(prefix):] if a.startswith(prefix) else a for a in r["advice_ids"]) or "-"


def _agree_label(r: dict) -> str:
    if r["agree"] is None:
        return "助言なし"
    if not r["agree"]:
        return "不一致"
    return "一致" + ("(前の第一候補)" if r["followed_earlier_best"] else "")


def _available_label(r: dict) -> str:
    return r["available"] + (f"({r['available_reason']})" if r["available"] != "yes" else "")


def render_table(rows: list) -> str:
    """決定 1 件 1 行の事実表 (Markdown)。rows は assign_ids 済み"""
    head = ["ID", "連番", "ログ:t:行", "turn", "advice_id (NNNN)", "場面", "自分の場", "相手の場", "助言の上位 3 (最終助言)",
            "プレイヤーの行動 (順位)", "一致", "利用可能", "不確かさ"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        key = f"{r['log'].removeprefix('battle_').removesuffix('.jsonl')}:{r['t']}:L{r['event_line']}"
        top = "<br>".join(f"{k}. {_fmt_action(a)}" for k, a in enumerate(r["top"], 1)) or "-"
        pl = r["player"]
        rank = f"{pl['rank']}位" if pl["rank"] else ("候補外" if r["top"] else "-")
        cells = [r["id"], r["no"], key, r["turn"], compact_advice_ids(r), r["scene"], mon_label(r["me"]), mon_label(r["opp"]),
                 top, f"{pl['label']} ({rank})", _agree_label(r), _available_label(r), _uncertainty_label(r["uncertainty"])]
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines)


def _load_dex() -> dict:
    try:
        return json.loads(DEX_PATH.read_text())
    except (OSError, ValueError):
        return {}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="決定対応表 (決定 1 件 1 行)")
    ap.add_argument("logs", nargs="+", help="対戦ログ (.jsonl) のパス")
    ap.add_argument("--out", default=None, help="Markdown (戦ごとの集計 + 決定の表) の出力先。無ければ標準出力")
    ap.add_argument("--json", default=None, help="行の JSON の出力先 (分類の作業用)")
    ap.add_argument("--check-expected", action="store_true",
                    help="計画書 §1.1 の再集計値 (決定 69 / 一致 26 / 利用可能 yes 62・unknown 7) と突き合わせ、違えば終了コード 1")
    args = ap.parse_args(argv)

    from tools.decision_audit import _ja_move
    dex = _load_dex()
    all_rows, audits, by_log = [], [], {}
    for p in args.logs:
        rows, audit = build_rows(p, move_ja=_ja_move, dex=dex)
        all_rows += rows
        audits.append(audit)
        by_log[Path(p).name] = audit
    assign_ids(all_rows)
    summary = summarize(audits)
    labels = log_labels(list(by_log))
    warns = [f"{r['log']} t={r['t']} L{r['event_line']}: {r['link_warning']}" for r in all_rows if r.get("link_warning")]
    md = render_per_log(per_log_summary(by_log), labels) + "\n\n" + render_table(all_rows)
    if args.out:
        Path(args.out).write_text(md + "\n")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "rows": all_rows}, ensure_ascii=False, indent=1))
    print("再集計:", json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    for w in warns:
        print("警告:", w, file=sys.stderr)
    if args.check_expected:
        diff = compare_expected(summary)
        print("期待値との差:", diff or "なし", file=sys.stderr)
        return 1 if diff or warns else 0
    return 1 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
