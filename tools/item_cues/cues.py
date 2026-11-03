"""持ち物の手がかりの件数 (読み取りだけ。2026-10-30 レビュー、開発者承認)。設計文書 §1.2 の「持ち物の表示」「相手の判明した持ち物」
「こだわりの手がかり」「交代」「読み取り欠落」「型推定の記録」の出典。

    python -B -m tools.item_cues.cues --logs <logs/battles> [--db <使用率 DB の写し> --snapshot 75 82] [--out DIR]

数えるもの (10/30 の count_cues.py の A・B・C・E・F・G、refine_cues.py の 1-2、negation_guard.py、known_by_month.py、
ev_infer_rows.py、explore_types.py、db_items.py。対応表は tools/item_cues/__init__.py):
- ログの概要 (全ファイル): 行の種類・場面・発火 ID の接頭辞
- 対戦の数・最大ターンの分布
- 持ち物の表示: item_<側>_<id> の発火の回数と発火のあった対戦の数、ほかの持ち物の文言
- 相手の判明した持ち物 (状態の item。個体 = 対戦 × 基本種): 分類 (item / marble / mega)、持ち物ごと、月ごと、判明の前の手がかり
- こだわりの手がかり (出場区間 = 交代・ひんしの発火で切る): 同じ技 3 連続 (turn の値が別々 / 時刻の差つき)、交代なしに違う技 2 つ (否定)。
  自分側は登録の持ち物 (こだわりスカーフ / それ以外) で分ける
- 否定の守りの条件: 個体ごとの最初の否定の 1 組について、帰属 2 通り (next_on_switch が主、last_state は 10/30 の negation_guard.py)
- 状態の場の個体の変化と交代の発火 (交代の読み落としの上限の目安)、frames 行 (読み取り欠落)、版 2 の advice 行の ev_infer
- 使用率 DB の持ち物の一覧 (--db と --snapshot のとき。mode=ro)
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from champions_agent import config as C
from tools.item_cues import common as cm

CLI = "cues"
SCARF = "choicescarf"
RULES = (
    "帰属 (主): 直前の状態行の場の個体、同じ側の交代の発火の後は次の状態行の場の個体 (next_on_switch)",
    "出場区間: その側の交代の発火、またはひんしの発火 (側を問わない) で切る",
    "相手の判明した持ち物: 状態行の相手の party の item の、(対戦, 基本種) ごとの最初の値。メガストーン (id の末尾 ite/itex/itey/itez・"
    "megastone) と marble (いのちのたま の誤解決) は別に数える",
    "自分側の登録の持ち物: 状態行の自分の party の item の、(対戦, 基本種) ごとの最初の値",
    "否定の守りの条件: 個体ごとの最初の否定の 1 組。guard_1030 (10/30 の定義) = turn が同じ・わるあがき・間の状態で場の個体が別の種、のどれでもない。"
    "guard_partial (§3.4 の一部だけ) = わるあがき・場の個体の変化・時刻の差 < ITEM_CUE_MIN_SEP_SEC (集計用の暫定値) のどれでもない "
    "(未検査: §3.4 の 3 処理の空白・5 のアンコール / へんしん・持ち物変更区間・個体と出場区間の識別。全条件を満たす標本ではない)。"
    "guard_full (較正用の確定標本) = 未判定 (全条件を検査できる記録が無い)",
)


# ---------------------------------------------------------------- 集計 (対戦ごと)

def overview(all_rows: dict) -> dict:
    """全ファイルの行の種類・場面・発火 ID の接頭辞 (10/30 の explore_types.py)"""
    types, scenes, prefix = Counter(), Counter(), Counter()
    for rows in all_rows.values():
        for r in rows:
            types[str(r.get("type"))] += 1
            if r.get("type") == "scene":
                scenes[str(r.get("scene"))] += 1
            if r.get("type") == "events":
                for f in cm.fired_of(r):
                    parts = f.split("_")
                    prefix["_".join(parts[:2]) if len(parts) > 1 else f] += 1
    return {"n_files": len(all_rows), "types": dict(types.most_common()), "scenes": dict(scenes.most_common()),
            "fired_prefix": dict(prefix.most_common())}


def turn_bucket(maxturn: int) -> str:
    return ("0" if maxturn == 0 else "1-2" if maxturn <= 2 else "3-5" if maxturn <= 5 else "6-8" if maxturn <= 8 else "9+")


def choice_counts(name: str, rows: list, timeline: list, R: dict, L: dict, my_items: dict, opp_known: dict) -> None:
    """こだわりの手がかり (固定・否定) を R / L に足す"""
    for side in cm.SIDES:
        ind = cm.individual_cues(cm.cut_stints(timeline, side))
        for sid, c in ind.items():
            if side == "player":
                it = my_items.get(sid, "?")
                g = "player_scarf" if it == SCARF else "player_other"
            else:
                it = (opp_known.get(sid) or {}).get("item", "unrevealed")
                g = "opponent"
            R["choice"][(g, "mons_with_move")] += 1
            R["choice"][(g, "ge2moves")] += int(c["n_moves"] >= 2)
            R["choice"][(g, "ge3moves")] += int(c["n_moves"] >= 3)
            R["choice"][(g, "lock")] += int(c["lock"])
            R["choice"][(g, "lock_distinct_turns")] += int(c["lock_distinct_turns"])
            R["choice"][(g, "lock_sep")] += int(c["lock_sep"])
            R["choice"][(g, "negation")] += int(c["negation"])
            R["choice"][(g, "lock_and_negation")] += int(c["lock"] and c["negation"])
            R["choice"][(g, "dup_same_turn")] += c["dup_same_turn"]
            if c["lock"]:
                R[f"choice_lock_item_{side}"][it] += 1
            if c["negation"]:
                R[f"choice_negation_item_{side}"][it] += 1
                if side == "player" and it == SCARF:
                    a, b = c["negation_pair"]
                    L["scarf_negation"].append((name, sid, a["move"], b["move"]))
            if side == "opponent" and c["lock"] and c["negation"]:
                L["opp_lock_and_negation"].append((name, sid))


def guard_counts(rows: list, R: dict, L: dict, name: str) -> None:
    """否定の守りの条件 (個体ごとの最初の否定の 1 組)。帰属 2 通り"""
    actives = cm.state_actives(rows)
    for attr in cm.ATTRIBUTIONS:
        tl = cm.build_timeline(rows, attr)
        for side in cm.SIDES:
            for sid, c in cm.individual_cues(cm.cut_stints(tl, side)).items():
                if not c["negation"]:
                    continue
                a, b = c["negation_pair"]
                g = cm.guard_flags(a, b, side, sid, cm.actives_between(actives, a["i"], b["i"]))
                key = (attr, side)
                R["guard"][key + ("negation",)] += 1
                for k, v in g.items():
                    if v is None:                     # guard_full: 未判定 (数えない。未判定の組の数だけ残す)
                        R["guard_undetermined"][key + (k,)] += 1
                        continue
                    R["guard"][key + (k,)] += int(v)
                R["guard_dt"][key + (cm.dt_bucket(a, b),)] += 1
                if attr == cm.ATTR_NEXT_ON_SWITCH and g["guard_1030"] and not g["guard_partial"]:
                    L["guard_1030_not_partial"].append((name, side, sid, a["move"], b["move"], a.get("t"), b.get("t")))


def pre_cues(timeline: list, opp_known: dict) -> Counter:
    """相手の判明した持ち物の前 (判明した状態行の時刻より前の技) に、その個体の手がかりがあったか"""
    out = Counter()
    stints = cm.cut_stints(timeline, "opponent")
    for b, k in opp_known.items():
        t_known = k.get("t") or 0
        before = [{"sid": st["sid"], "moves": [m for m in st["moves"] if (m.get("t") or 0) < t_known]}
                  for st in stints if st["sid"] == b]
        c = cm.individual_cues([st for st in before if st["moves"]]).get(b)
        out["n"] += 1
        out["any_move"] += int(bool(c and c["n_moves"]))
        out["two_moves"] += int(bool(c and c["negation"]))
        out["lock3"] += int(bool(c and c["lock"]))
    return out


def switch_changes(rows: list, R: dict) -> None:
    """状態行の場の個体 (基本種) が変わった回数と、その間に交代の発火が無かった回数"""
    for side in cm.SIDES:
        prev, sw = None, False
        for r in rows:
            if r.get("type") == "events":
                if f"switch_{side}" in cm.fired_of(r):
                    sw = True
                    R[f"switch_{side}"]["switch_fired"] += 1
                continue
            if not cm.is_state_row(r):
                continue
            sid = cm.active_base(r["state"], side)
            if not sid:
                continue
            if prev and sid != prev:
                R[f"switch_{side}"]["changes"] += 1
                R[f"switch_{side}"]["no_switch_fired"] += int(not sw)
            prev, sw = sid, False


def collect(loaded: dict) -> dict:
    R: dict = defaultdict(Counter)
    L: dict = defaultdict(list)
    samples: dict = defaultdict(list)
    maxturns = []
    by_month: dict = defaultdict(Counter)
    ev = Counter()
    ev_battles = set()
    for b in loaded["battles"]:
        name, rows = b["name"], b["rows"]
        month = cm.battle_month(name)
        tl = cm.build_timeline(rows)
        mt = cm.max_turn(tl)
        maxturns.append(mt)
        R["battles"]["n"] += 1
        has_v2 = any(r.get("type") == "advice" and (r.get("state") or {}).get("state_schema") == 2 for r in rows)
        R["battles"]["with_state_v2_advice"] += int(has_v2)
        R["battles"]["with_battle_advice"] += int(any(r.get("type") == "advice" and r.get("kind") == "battle" for r in rows))
        R["battles"]["with_frames"] += int(any(r.get("type") == "frames" for r in rows))
        R["maxturn"][turn_bucket(mt)] += 1

        # 持ち物の表示
        seen = set()
        for e in tl:
            for k, f in enumerate(e["fired"]):
                it = cm.item_fire(f)
                if it:
                    side, iid = it
                    R[f"item_fire_{side}"][iid] += 1
                    if it not in seen:
                        seen.add(it)
                        R[f"item_battle_{side}"][iid] += 1
                    if len(samples[f"{side}_{iid}"]) < 3 and e["texts"]:
                        samples[f"{side}_{iid}"].append(e["texts"][min(k, len(e["texts"]) - 1)])
                elif f.startswith(("knockoff_", "balloon_", "leftovers_heal", "sash_endure", "lifeorb_recoil")):
                    R["item_msg_other"][f.rsplit("_", 1)[0] if f.startswith("knockoff_") else f] += 1

        # 判明した持ち物
        firsts = cm.first_items(rows)
        opp_known = {}
        for (side, bs), v in firsts.items():
            if side != "opponent":
                continue
            kind = cm.item_kind(v["item"])
            R["opp_item_known"][kind] += 1
            R["opp_item_known"]["changed_later"] += int(len(v["values"]) > 1)
            if kind == "item":
                R["opp_item_known_by_item"][v["item"]] += 1
                by_month[month]["individuals"] += 1
                by_month["item_" + month][v["item"]] += 1
                opp_known[bs] = v
        if opp_known:
            R["battles"]["with_opp_item_known"] += 1
            by_month[month]["battles"] += 1
        my_items = {bs: v["item"] for (side, bs), v in firsts.items() if side == "player"}
        R["opp_known_pre_cue"].update(pre_cues(tl, opp_known))

        # こだわり・守りの条件・交代
        choice_counts(name, rows, tl, R, L, my_items, opp_known)
        guard_counts(rows, R, L, name)
        switch_changes(rows, R)

        # frames 行 (最後の 1 行)
        fr = [r for r in rows if r.get("type") == "frames"]
        if fr:
            last = fr[-1]
            for k in ("received", "processed", "dropped", "hidden", "unknown"):
                R["frames"][k] += last.get(k) or 0
            L["frames"].append((name, last.get("received"), last.get("processed"), last.get("dropped"), last.get("hidden"),
                                last.get("unknown"), last.get("recv_fps"), last.get("proc_fps"), last.get("span_sec")))

        # 版 2 の advice 行の ev_infer
        for r in rows:
            if r.get("type") != "advice" or r.get("kind") != "battle" or (r.get("state") or {}).get("state_schema") != 2:
                continue
            ev_battles.add(name)
            ev["v2_rows"] += 1
            ei = r.get("ev_infer")
            if ei is None:
                ev["ev_infer_null"] += 1
                continue
            ev["ev_infer_rows"] += 1
            for v in ei.values():
                if v:
                    ev["entries"] += 1
                    ev["entries_n_obs>0"] += int(((v or {}).get("n_obs") or 0) > 0)
    mt = sorted(maxturns)
    return {"counts": {k: {(" / ".join(kk) if isinstance(kk, tuple) else kk): vv for kk, vv in v.items()} for k, v in R.items()},
            "R": R, "lists": dict(L), "samples": dict(samples), "by_month": {k: dict(v) for k, v in by_month.items()},
            "maxturn_median": mt[len(mt) // 2] if mt else None, "maxturn_max": mt[-1] if mt else None,
            "ev_infer": dict(ev), "ev_infer_battles": sorted(ev_battles)}


def db_items(db_path: Path, snapshots: list) -> list:
    """使用率 DB (mode=ro) の snapshot ごとの持ち物の一覧 (10/30 の db_items.py)"""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    out = []
    try:
        for sid in snapshots:
            meta = conn.execute("SELECT id, fetched_at, source, format FROM usage_snapshot WHERE id=?", (int(sid),)).fetchone()
            rows = conn.execute("SELECT item_name, COUNT(DISTINCT pokemon_name) n, ROUND(SUM(usage_percent), 1) s "
                                "FROM item_usage WHERE snapshot_id=? GROUP BY item_name ORDER BY s DESC", (int(sid),)).fetchall()
            nsp = conn.execute("SELECT COUNT(DISTINCT pokemon_name) FROM item_usage WHERE snapshot_id=?", (int(sid),)).fetchone()[0]
            out.append({"snapshot": list(meta) if meta else [sid], "n_species": nsp, "n_items": len(rows),
                        "items": [{"item": a, "n_species": n, "usage_sum": s} for a, n, s in rows]})
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------- 出力

def guards_payload(res: dict) -> dict:
    """JSON の守りの条件の説明 (各 guard の検査した条件・未検査の条件、ITEM_CUE_MIN_SEP_SEC が暫定値であること、guard_full は未判定)"""
    return {"definitions": cm.GUARD_DEFS,
            "min_sep_sec": {"value": C.ITEM_CUE_MIN_SEP_SEC, "note": cm.GUARD_MIN_SEP_NOTE},
            "guard_full": {"n": 0, "status": "undetermined", "reason": cm.GUARD_FULL_UNDETERMINED,
                           "n_pairs_undetermined": {" / ".join(k[:2]): v for k, v in res["R"]["guard_undetermined"].items()
                                                    if k[-1] == "guard_full"}},
            "renamed_from_1030": {"clean_1030": "guard_1030", "clean": "guard_partial",
                                  "guard_clean_1030_not_clean": "guard_1030_not_partial"}}


def render(rec: dict, ov: dict, res: dict, dbi: list) -> list:
    R = res["R"]
    out = ["# 持ち物の手がかりの件数 (cues)", ""] + cm.render_record(rec)
    out += ["## ログの概要 (全ファイル)", "", f"- ファイル {ov['n_files']}", f"- 行の種類: {cm.md_counter(ov['types'])}",
            f"- 場面: {cm.md_counter(ov['scenes'])}", f"- 発火 ID の接頭辞: {cm.md_counter(ov['fired_prefix'])}", ""]
    out += ["## 対戦の数・最大ターン", "", f"- {cm.md_counter(R['battles'])}",
            f"- 最大ターンの分布: {cm.md_counter(R['maxturn'])} (中央値 {res['maxturn_median']}、最大 {res['maxturn_max']})", ""]
    out += ["## 持ち物の表示 (発火の回数 / 発火のあった対戦の数)", ""]
    ids = sorted(set(R["item_fire_player"]) | set(R["item_fire_opponent"]),
                 key=lambda i: -(R["item_fire_opponent"][i] + R["item_fire_player"][i]))
    out += cm.md_table(["持ち物", "相手", "自分"], [[i, f"{R['item_fire_opponent'][i]} / {R['item_battle_opponent'][i]}",
                                                   f"{R['item_fire_player'][i]} / {R['item_battle_player'][i]}"] for i in ids])
    out += ["", f"- ほかの持ち物の文言: {cm.md_counter(R['item_msg_other'])}", ""]
    out += ["## 相手の判明した持ち物 (個体 = 対戦 × 基本種)", "", f"- 分類: {cm.md_counter(R['opp_item_known'])}",
            f"- 持ち物ごと: {cm.md_counter(R['opp_item_known_by_item'])}",
            f"- 判明した対戦: {R['battles']['with_opp_item_known']}",
            f"- 判明の前の手がかり: {cm.md_counter(R['opp_known_pre_cue'])}", ""]
    months = sorted(k for k in res["by_month"] if not k.startswith("item_"))
    out += cm.md_table(["月", "個体", "対戦", "持ち物"], [[m, res["by_month"][m].get("individuals", 0), res["by_month"][m].get("battles", 0),
                                                       cm.md_counter(res["by_month"].get("item_" + m, {}))] for m in months])
    out += ["", "## こだわりの手がかり (個体ごと。出場区間 = 交代・ひんしの発火で切る)", ""]
    groups = (("player_scarf", "自分: スカーフ"), ("player_other", "自分: それ以外"), ("opponent", "相手"))
    labels = (("mons_with_move", "技を使った個体"), ("ge2moves", "技 2 回以上"), ("ge3moves", "技 3 回以上"),
              ("lock", f"同じ技 {C.ITEM_CUE_LOCK_REPEAT} 連続"), ("lock_distinct_turns", "　うち turn の値が別々"),
              ("lock_sep", f"　うち隣り合う時刻の差 ≥ {C.ITEM_CUE_MIN_SEP_SEC} 秒 ({cm.GUARD_MIN_SEP_NOTE})"), ("negation", "違う技 2 つ (否定)"),
              ("lock_and_negation", "連続と否定の両方"), ("dup_same_turn", "同じ技が同じ turn の値で続いた回数"))
    out += cm.md_table([""] + [g[1] for g in groups], [[lab] + [R["choice"][(g, k)] for g, _ in groups] for k, lab in labels])
    out += ["", f"- 自分の連続の登録の持ち物: {cm.md_counter(R['choice_lock_item_player'])}",
            f"- 自分の否定の登録の持ち物: {cm.md_counter(R['choice_negation_item_player'])}",
            f"- 相手の連続の後の判明: {cm.md_counter(R['choice_lock_item_opponent'])}",
            f"- 相手の否定の後の判明: {cm.md_counter(R['choice_negation_item_opponent'])}",
            f"- スカーフ持ちの否定: {res['lists'].get('scarf_negation', [])}", ""]
    out += ["## 否定の守りの条件 (個体ごとの最初の 1 組)", "",
            "帰属 next_on_switch が主。last_state は 10/30 の negation_guard.py の帰属 (設計文書 §1.2 の 216 / 215 と 134 / 154)。", "",
            f"- ITEM_CUE_MIN_SEP_SEC = {C.ITEM_CUE_MIN_SEP_SEC} 秒 ({cm.GUARD_MIN_SEP_NOTE})",
            f"- 較正用の確定標本: 0 ({cm.GUARD_FULL_UNDETERMINED})",
            "- どの guard も、交代・ひんしの発火で切った出場区間の中の組が前提 (§3.4 の 1)。"
            "guard_partial は §3.4 の条件の一部だけを検査した数で、全条件を満たす標本ではない", ""]
    out += [f"- **{k}** — {d['label']}。検査した条件: {'、'.join(d['checked']) or '(なし)'}。"
            f"未検査: {'、'.join(d['unchecked']) or '(なし)'}" + (f"。{d['status']}" if d.get("status") else "")
            for k, d in cm.GUARD_DEFS.items()]
    out += [""]
    gk = (("negation", "否定"), ("same_turn", "turn が同じ"), ("struggle", "わるあがき"), ("active_changed", "間に場の個体が別の種"),
          ("too_close", f"時刻の差 < {C.ITEM_CUE_MIN_SEP_SEC} 秒 ({cm.GUARD_MIN_SEP_NOTE})"),
          ("guard_1030", "guard_1030: どれにも当たらない (10/30 の定義)"),
          ("guard_partial", "guard_partial: §3.4 の一部の条件だけ通る (全条件ではない)"))
    cols = [(a, s) for a in cm.ATTRIBUTIONS for s in cm.SIDES]
    out += cm.md_table([""] + [f"{a} / {s}" for a, s in cols], [[lab] + [R["guard"][(a, s, k)] for a, s in cols] for k, lab in gk]
                       + [["guard_full: 較正用の確定標本"] + ["0 (未判定)" for _ in cols]])
    out += ["", "時刻の差の分布 (最初の 1 組):", ""]
    bks = ("<5s", "<10s", "<20s", "<30s", "<60s", ">=60s", "no_t")
    out += cm.md_table([""] + list(bks), [[f"{a} / {s}"] + [R["guard_dt"][(a, s, k)] for k in bks] for a, s in cols])
    out += ["", "## 交代 (状態の場の個体の変化)", ""]
    out += cm.md_table(["", "変化", "間に交代の発火なし", "交代の発火"],
                       [[s, R[f"switch_{s}"]["changes"], R[f"switch_{s}"]["no_switch_fired"], R[f"switch_{s}"]["switch_fired"]]
                        for s in ("opponent", "player")])
    out += ["", "## 読み取り欠落 (frames 行の最後)", "", f"- 対戦 {R['battles']['with_frames']}: {cm.md_counter(R['frames'])}", ""]
    out += ["## 型推定の記録 (版 2 の advice 行の ev_infer)", "", f"- 対戦 {len(res['ev_infer_battles'])}: {cm.md_counter(res['ev_infer'])}", ""]
    if dbi:
        out += ["## 使用率 DB の持ち物の一覧", ""]
        for d in dbi:
            out += [f"- snapshot {d['snapshot']}: 種 {d['n_species']}、持ち物 {d['n_items']}",
                    "  " + "、".join(f"{x['item']}:{x['n_species']}sp/{x['usage_sum']}" for x in d["items"]), ""]
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="持ち物の手がかりの件数 (実戦ログを読むだけ)")
    cm.add_common_args(ap)
    ap.add_argument("--db", default=None, help="使用率 DB (mode=ro で読む。写しを渡す)。--snapshot と一緒に")
    ap.add_argument("--snapshot", type=int, nargs="*", default=[], help="持ち物の一覧を出す snapshot の id")
    args = ap.parse_args(argv)
    logs = Path(args.logs)
    loaded = cm.load_battles(logs)
    db = None
    dbi: list = []
    if args.db and args.snapshot:
        dbi = db_items(Path(args.db), args.snapshot)
        db = {"path": str(args.db), "sha16": cm.file_sha16(Path(args.db)), "snapshots": args.snapshot,
              "snapshot_meta": [d["snapshot"] for d in dbi]}
    rec = cm.repro_record(CLI, logs, loaded, vars(args), RULES, db)
    ov = overview(loaded["all_rows"])
    res = collect(loaded)
    payload = {"record": rec, "overview": ov, "counts": res["counts"], "lists": res["lists"], "samples": res["samples"],
               "by_month": res["by_month"], "maxturn_median": res["maxturn_median"], "maxturn_max": res["maxturn_max"],
               "ev_infer": res["ev_infer"], "ev_infer_battles": res["ev_infer_battles"], "db_items": dbi,
               "guards": guards_payload(res)}
    md, js = cm.write_outputs(Path(args.out), CLI, render(rec, ov, res, dbi), payload)
    print(f"→ {md}\n→ {js}")


if __name__ == "__main__":
    main()
