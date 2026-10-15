"""採点方式の比較の診断 (段 1): 第一候補と実行行動の不一致の全数集計 (読み取りのみ)。

    python -m tools.diag_scoring_compare <条件名>=<battles.jsonl> [...] [--stats <条件名>=<結果.json>]

advisor-as-player の対戦記録 (tools.team_build.battle_log.BattleRecorder の battles.jsonl) から、決定ごとに助言の第一候補
(advisor_recommendation.best の種類・id) と実行行動 (executed_action) を突き合わせ、不一致を分類して Markdown の表を出す。

分類 (classify_decision。上から順に当てはめる):
- unpicked_switch: 未選出の個体への交代 (第一候補の交代先が our_selection に無い)
- fainted_switch: ひんしの個体への交代 (この決定より前のターンの ko_events、または同じターンの先の決定の後に倒れた個体)
- move_on_fainted: 場の個体がひんし (交代の場面) で第一候補が技
- move_in_switch_scene: 場の個体は生存、同じターンの 2 回目の決定 (交代技の後の繰り出し等、交代しか選べない場面) で第一候補が技
- move_locked: 第一候補が技で、実行したのが直前の決定と同じ個体の同じ技 (こだわり・げきりん・アンコール等の固定の可能性)
- move_restricted: 技の制限 (その他: PP 0・ちょうはつ・かなしばり等で選べない技。記録には合法手が無いので内訳不明)
- switch_blocked: 選出済み・生存の個体への交代が実行できなかった (とらわれ等の可能性。記録には合法手が無いので理由不明)
- id_form: 同じ個体の id・形態の不一致 (メガ名等)
- other: 理由不明

実行の分岐 (champions_agent.env.advisor_player.choose_from_advice と対応): 第一候補を実行 / 記録の上位 4 候補の k 位を実行
(第一候補を選べず候補の列を下った) / 上位 4 位より下の候補を実行 / 実行行動なし (choose_from_advice が None →
choose_random_move のランダム行動)。

純粋な部分 (classify_decision / summarize_battles / render_markdown) はテストする (tests/test_diag_scoring_compare.py)。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

CATEGORIES = ("unpicked_switch", "fainted_switch", "move_on_fainted", "move_in_switch_scene", "move_locked",
              "move_restricted", "switch_blocked", "id_form", "other")
CATEGORY_LABELS = {
    "unpicked_switch": "未選出の個体への交代",
    "fainted_switch": "ひんしの個体への交代",
    "move_on_fainted": "場の個体がひんし (交代の場面) で技",
    "move_in_switch_scene": "交代だけの場面 (同じターンの 2 回目の決定: 交代技の後等) で技",
    "move_locked": "技の固定の可能性 (直前と同じ個体が同じ技を実行)",
    "move_restricted": "技の制限 (その他: PP 0・ちょうはつ等。内訳は記録に無い)",
    "switch_blocked": "選出済みの生存個体への交代を実行できず (とらわれ等。理由は記録に無い)",
    "id_form": "id・形態の不一致 (メガ名等)",
    "other": "その他 (理由不明)",
}
_MEGA_SUFFIX = re.compile(r"mega[xy]?$")


def to_id(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def base_id(sid) -> str:
    """種族 id からメガの接尾辞 (mega / megax / megay) を落とす (純粋)"""
    s = to_id(sid)
    b = _MEGA_SUFFIX.sub("", s)
    return b or s


def same_mon(a, b) -> bool:
    """同じ個体の id か (純粋)。メガ後・フォルム違いは基本部分の前方一致で同じとみなす (scolipedemega と scolipede、
    floettemega と floetteeternal)"""
    x, y = base_id(a), base_id(b)
    if not x or not y:
        return False
    return x == y or x.startswith(y) or y.startswith(x)


def is_picked_id(sid, selection) -> bool:
    return any(same_mon(sid, s) for s in (selection or []))


def our_role(record: dict) -> Optional[str]:
    """自分の側 (p1 / p2)。lead.theirs ("p2a: X") の反対側。分からなければ None"""
    th = str(((record.get("lead") or {}).get("theirs")) or "")
    if th.startswith("p1"):
        return "p2"
    if th.startswith("p2"):
        return "p1"
    lo = str(((record.get("lead") or {}).get("ours")) or "")
    return lo[:2] if lo[:2] in ("p1", "p2") else None


def _ko_name(ident: str) -> str:
    return to_id(str(ident or "").split(":", 1)[-1])


def fainted_before(record: dict, turn, same_turn_prior: bool) -> set:
    """この決定の時点で倒れていた自分の個体 (名前の id) の集合 (純粋)。
    この決定より前のターンの ko_events と、同じターンの先の決定があれば同じターンの ko_events (交代の場面の決定は
    同じターンの倒れた後に来る) を数える"""
    role = our_role(record)
    out = set()
    if turn is None:
        return out
    for ko in record.get("ko_events") or []:
        who = str(ko.get("fainted") or "")
        if role and not who.startswith(role):
            continue
        t = ko.get("turn")
        if t is None:
            continue
        if t < turn or (t == turn and same_turn_prior):
            out.add(_ko_name(who))
    return out


def _key(a: Optional[dict]) -> Optional[tuple]:
    if not a or not a.get("kind"):
        return None
    return (a.get("kind"), a.get("id"))


def classify_decision(dec: dict, selection, fainted: set, prev: Optional[dict] = None,
                      same_turn_prior: bool = False) -> dict:
    """1 決定の分類 (純粋)。prev = 同じ対戦の直前の決定 (無ければ None)、same_turn_prior = 同じターンに先の決定があるか。戻り値 {"status", "category", "exec_rank", "unpicked_in_top4", "best_unpicked"}。
    status: "no_advice" (助言なし・ok でない) / "match" / "mismatch" / "random" (実行行動なし = ランダム行動)。
    category: 不一致・ランダムのときの分類 (CATEGORIES)。exec_rank: 実行行動が記録の上位 4 候補の何位か (1 始まり。無ければ None)"""
    rec = dec.get("advisor_recommendation") or {}
    best = rec.get("best") or {}
    acts = list(rec.get("actions") or [])
    executed = dec.get("executed_action")
    me = (dec.get("state_before") or {}).get("me") or {}
    out = {"status": None, "category": None, "exec_rank": None,
           "unpicked_in_top4": any(a.get("kind") == "switch" and not is_picked_id(a.get("id"), selection)
                                   for a in acts),
           "best_unpicked": best.get("kind") == "switch" and not is_picked_id(best.get("id"), selection)}
    if not rec or not rec.get("ok") or not best.get("kind"):
        out["status"] = "no_advice"
        return out
    ek = _key(executed)
    if ek is not None:
        for i, a in enumerate(acts):
            if _key(a) == ek:
                out["exec_rank"] = i + 1
                break
    if ek == _key(best):
        out["status"] = "match"
        return out
    out["status"] = "random" if ek is None else "mismatch"
    bid = best.get("id")
    if best.get("kind") == "switch":
        if not is_picked_id(bid, selection):
            cat = "unpicked_switch"
        elif base_id(bid) in {base_id(f) for f in fainted} or any(same_mon(bid, f) for f in fainted):
            cat = "fainted_switch"
        elif ek is not None and ek[0] == "switch" and same_mon(ek[1], bid):
            cat = "id_form"
        else:
            cat = "switch_blocked"
    else:
        hp = me.get("hp")
        prev_exec = (prev or {}).get("executed_action") or {}
        prev_me = ((prev or {}).get("state_before") or {}).get("me") or {}
        if hp is not None and float(hp) <= 0:
            cat = "move_on_fainted"
        elif ek is not None and ek[0] == "move" and to_id(ek[1]) == to_id(bid):
            cat = "id_form"
        elif same_turn_prior and (ek is None or ek[0] == "switch"):
            cat = "move_in_switch_scene"
        elif (ek is not None and ek[0] == "move" and prev_exec.get("kind") == "move" and prev_exec.get("id") == ek[1]
              and same_mon(prev_me.get("species"), me.get("species"))):
            cat = "move_locked"
        else:
            cat = "move_restricted"
    out["category"] = cat
    return out


def summarize_battles(records: list) -> dict:
    """対戦記録の列 → 集計 (純粋)。
    decisions: 全決定、advised: 助言が ok の決定、executed: 実行行動のある決定 (advisor_player の stats["decide"] に相当)、
    mismatch: 実行行動のある決定のうち第一候補と種類・id が違う件数 (レビュワーの集計の分子)、
    random: 実行行動なし (ランダム行動)、categories_mismatch / categories_random: 分類別の件数、
    exec_rank: 不一致のとき実行行動が上位 4 候補の何位だったか (">4" = 4 位より下)、
    unpicked_in_top4: 記録の上位 4 候補に未選出の個体への交代が入った決定数、best_unpicked: 第一候補が未選出への交代の決定数、
    battles_with_unpicked_best: 第一候補が未選出への交代になった決定を含む対戦数"""
    s = {"battles": 0, "decisions": 0, "advised": 0, "executed": 0, "match": 0, "mismatch": 0, "random": 0,
         "no_advice": 0, "categories_mismatch": Counter(), "categories_random": Counter(), "exec_rank": Counter(),
         "unpicked_in_top4": 0, "best_unpicked": 0, "battles_with_unpicked_best": 0,
         "mismatch_kind_pairs": Counter(), "selection_missing": 0}
    for r in records:
        s["battles"] += 1
        sel = list(r.get("our_selection") or [])
        if len(sel) < 3:
            s["selection_missing"] += 1
        any_unpicked_best = False
        prev_turns = Counter()
        prev = None
        for dec in r.get("turns") or []:
            turn = dec.get("turn")
            same_turn_prior = prev_turns[turn] > 0
            fainted = fainted_before(r, turn, same_turn_prior)
            prev_turns[turn] += 1
            s["decisions"] += 1
            c = classify_decision(dec, sel, fainted, prev, same_turn_prior)
            prev = dec
            st = c["status"]
            s[st] += 1
            if st != "no_advice":
                s["advised"] += 1
            if dec.get("executed_action"):
                s["executed"] += 1
            if c["unpicked_in_top4"]:
                s["unpicked_in_top4"] += 1
            if c["best_unpicked"] and st != "no_advice":
                s["best_unpicked"] += 1
                any_unpicked_best = True
            if st == "mismatch":
                s["categories_mismatch"][c["category"]] += 1
                s["exec_rank"][str(c["exec_rank"]) if c["exec_rank"] else ">4"] += 1
                best = (dec.get("advisor_recommendation") or {}).get("best") or {}
                s["mismatch_kind_pairs"][f"{best.get('kind')}→{(dec.get('executed_action') or {}).get('kind')}"] += 1
            elif st == "random":
                s["categories_random"][c["category"]] += 1
        if any_unpicked_best:
            s["battles_with_unpicked_best"] += 1
    return s


def _pct(n, d) -> str:
    return f"{n / d:.1%}" if d else "-"


def render_markdown(summaries: dict, stats: Optional[dict] = None) -> str:
    """条件名 → summarize_battles の結果 を Markdown にする (純粋)。stats は条件名 → 結果 json の stats (任意。
    transition の採点数・退避数から「未選出が残存戦力に入った決定」を出す)"""
    stats = stats or {}
    names = list(summaries)
    L = ["# 第一候補と実行行動の不一致 (全数)", ""]
    L.append("| 項目 | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))

    def row(label, f):
        L.append(f"| {label} | " + " | ".join(f(summaries[n]) for n in names) + " |")
    row("対戦数", lambda s: f"{s['battles']:,}")
    row("決定数 (記録の turns)", lambda s: f"{s['decisions']:,}")
    row("助言あり (ok)", lambda s: f"{s['advised']:,}")
    row("実行行動あり (stats.decide 相当)", lambda s: f"{s['executed']:,}")
    row("不一致 (実行行動ありのうち第一候補と種類・id が違う)",
        lambda s: f"{s['mismatch']:,} / {s['executed']:,} ({_pct(s['mismatch'], s['executed'])})")
    row("実行行動なし (ランダム行動)", lambda s: f"{s['random']:,}")
    row("助言なし・ok でない", lambda s: f"{s['no_advice']:,}")
    row("選出 (our_selection) が 3 体未満の対戦", lambda s: f"{s['selection_missing']:,}")
    L += ["", "## 不一致の分類 (実行行動あり)", ""]
    L.append("| 分類 | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    for c in CATEGORIES:
        L.append(f"| {CATEGORY_LABELS[c]} | " + " | ".join(
            f"{summaries[n]['categories_mismatch'].get(c, 0):,} ({_pct(summaries[n]['categories_mismatch'].get(c, 0), summaries[n]['mismatch'])})"
            for n in names) + " |")
    L += ["", "## 実行の分岐 (choose_from_advice: 第一候補を選べず候補の列を下った位置)", ""]
    L.append("| 実行した候補の順位 (記録の上位 4) | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    L.append("| 1 位 (= 第一候補、一致) | " + " | ".join(f"{summaries[n]['match']:,}" for n in names) + " |")
    for k in ("1", "2", "3", "4", ">4"):
        lab = {"1": "1 位 (第一候補と別の best。best と actions[0] が違う)", ">4": "4 位より下"}.get(k, f"{k} 位")
        L.append(f"| {lab} | " + " | ".join(f"{summaries[n]['exec_rank'].get(k, 0):,}" for n in names) + " |")
    L.append("| 実行行動なし (ランダム) | " + " | ".join(f"{summaries[n]['random']:,}" for n in names) + " |")
    L += ["", "### 不一致の種類の組 (第一候補 → 実行)", ""]
    pairs = sorted({k for n in names for k in summaries[n]["mismatch_kind_pairs"]})
    L.append("| 組 | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    for p in pairs:
        L.append(f"| {p} | " + " | ".join(f"{summaries[n]['mismatch_kind_pairs'].get(p, 0):,}" for n in names) + " |")
    if any(summaries[n]["random"] for n in names):
        L += ["", "### 実行行動なし (ランダム) の分類", ""]
        L.append("| 分類 | " + " | ".join(names) + " |")
        L.append("|---|" + "---|" * len(names))
        for c in CATEGORIES:
            L.append(f"| {CATEGORY_LABELS[c]} | " + " | ".join(
                f"{summaries[n]['categories_random'].get(c, 0):,}" for n in names) + " |")
    L += ["", "## 未選出の個体が入った範囲", ""]
    L.append("| 範囲 | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    L.append("| (a) 交代候補: 第一候補が未選出への交代 (決定数) | " + " | ".join(
        f"{summaries[n]['best_unpicked']:,} / {summaries[n]['advised']:,} ({_pct(summaries[n]['best_unpicked'], summaries[n]['advised'])})"
        for n in names) + " |")
    L.append("| (a) 交代候補: 記録の上位 4 候補に未選出への交代 (決定数) | " + " | ".join(
        f"{summaries[n]['unpicked_in_top4']:,} ({_pct(summaries[n]['unpicked_in_top4'], summaries[n]['advised'])})"
        for n in names) + " |")
    L.append("| (a) 第一候補が未選出への交代になった決定を含む対戦 | " + " | ".join(
        f"{summaries[n]['battles_with_unpicked_best']:,} / {summaries[n]['battles']:,}" for n in names) + " |")

    def material(n):
        st = stats.get(n) or {}
        tr = int(st.get("scoring_transition") or 0)
        fb = int(st.get("scoring_fallback") or 0)
        if not tr:
            return "記録に無い (transition の採点なし)"
        return f"記録に無い。コード上は transition で採点した全決定: {tr - fb:,} (採点 {tr:,} − 退避 {fb:,})"
    L.append("| (b) 残存戦力 (transition の V の自分側) | " + " | ".join(material(n) for n in names) + " |")
    L.append("| (c) 終盤評価の行列 (_run_endgame) | " + " | ".join(
        "記録に無い。コード上は全決定 (未選出 3 体が生存・満タンで行列に入る)" for _n in names) + " |")
    L.append("")
    return "\n".join(L)


def read_jsonl(path) -> list:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _pairs(args) -> dict:
    out = {}
    for a in args:
        name, _, path = a.partition("=")
        if not path:
            name, path = Path(a).stem.replace(".battles", ""), a
        out[name] = path
    return out


def main(argv: list) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    logs, stats_paths, i = [], [], 0
    while i < len(argv):
        if argv[i] == "--stats" and i + 1 < len(argv):
            stats_paths.append(argv[i + 1])
            i += 2
            continue
        logs.append(argv[i])
        i += 1
    summaries = {n: summarize_battles(read_jsonl(p)) for n, p in _pairs(logs).items()}
    stats = {}
    for n, p in _pairs(stats_paths).items():
        stats[n] = (json.loads(Path(p).read_text(encoding="utf-8")) or {}).get("stats") or {}
    print(render_markdown(summaries, stats))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
