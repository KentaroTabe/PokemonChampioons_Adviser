"""型の事前分布の候補の評価: 使用率 DB (snapshot を固定) と Smogon の Champions 統計 (月・形式・cutoff を固定) を、
実戦の対戦ログで判明した技・持ち物の対数尤度で対応比較する (2026-10-30 レビュー、開発者承認)。

評価の道具だけ。助言・既定の方式・使用率 DB は変えない。混合比の調整はしない (docs/DESIGN_PRIOR_MIXING_20261023.md §9)。

    python -B -m tools.usage_prior_eval --month 2026-09 --format gen9championsbssregmc --cutoff 1500 \\
        --db <使用率 DB の写し> --snapshot 75 --logs <logs/battles> [--fetch] [--out logs/usage_prior_eval]

規則 (純粋な部分はテストする: tests/test_smogon_eval.py):
- 観測: 対戦ログの advice 行の state (相手の party) を時刻順に読み、個体 (対戦 × party の枠) ごとに「初めて判明した技 (revealed)」
  「初めて判明した持ち物 (item)」を 1 標本にする。同じ対戦・同じ個体・同じ技の反復は 1 標本。後の行の情報 (種族の訂正・メガ) を
  前の観測に混ぜない (観測の時点の種族で予測する)。種族が不明・推定 (guess) の時点で判明したものは数えない (件数は残す)。
- 事象: 判明した技は「その技を持っている」(保有確率の対数)。見えなかった技を「持っていない」と数えない。持ち物は判明したものだけ
  (メガストーンはメガシンカで分かるが、state の item に出たものだけを数える)。
- 予測: 使用率 DB = 種ごとの採用率 (move_usage / item_usage の usage_percent / 100。上位 10 だけ。sets.predict の合計 100 への
  正規化は通さない)。Smogon = advisor.usage_smogon の保有確率 / 持ち物の分布。一覧に無い値は SMOGON_EVAL_PROB_FLOOR。
  メガ: DB は基本種にまとめているので基本種で引く。Smogon は既定 (pooled) で基本種 + メガを重みで合算 (DB と同じ条件)、
  known はメガ形態が判明していればメガの項目 (感度として併記)。
- 対応比較: 両方の予測に種がある観測だけ。差 = log p(Smogon) − log p(DB) の観測あたりの平均、区間は対戦単位のブートストラップ。
- 時系列: 評価するのは統計の月より後 (翌月 1 日 0 時、ローカル時刻) に始まった対戦だけ。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (SMOGON_CONVERT_VERSION, SMOGON_EVAL_BOOTSTRAP_N, SMOGON_EVAL_BOOTSTRAP_SEED,
                                    SMOGON_EVAL_CI, SMOGON_EVAL_DB_SNAPSHOT, SMOGON_EVAL_MEGA_VIEW,
                                    SMOGON_EVAL_MEGA_VIEWS, SMOGON_EVAL_PROB_FLOOR, SMOGON_MOVE_USE_DIVISOR)

REPO = Path(__file__).resolve().parent.parent
REPORT_VERSION = 1
KINDS = ("move", "item")


# ------------------------------------------------------------------ 時系列 (純粋)
def eval_start_epoch(month: str) -> float:
    """統計の月 (YYYY-MM) の翌月 1 日 0 時 (ローカル時刻) の epoch 秒"""
    y, m = (int(x) for x in month.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1))


def battle_start(rows: list) -> Optional[float]:
    """対戦の開始時刻: session / version 行の t、無ければ全行の最小の t"""
    ts = [r.get("t") for r in rows if r.get("type") in ("session", "version") and isinstance(r.get("t"), (int, float))]
    if not ts:
        ts = [r.get("t") for r in rows if isinstance(r.get("t"), (int, float))]
    return min(ts) if ts else None


def split_by_time(battles: list, start: float) -> tuple:
    """battles: [{"id", "t0", ...}] → (評価する対戦, 統計の月以前の対戦, 時刻の無い対戦) (純粋)"""
    keep, before, no_t = [], [], []
    for b in battles:
        if b.get("t0") is None:
            no_t.append(b)
        elif b["t0"] >= start:
            keep.append(b)
        else:
            before.append(b)
    return keep, before, no_t


# ------------------------------------------------------------------ 観測の取り出し (純粋)
def extract_observations(rows: list, battle_id: str, resolve_move: Callable[[str], Optional[str]],
                         canonical: Callable[[str], str] = lambda s: s,
                         is_mega: Callable[[str], bool] = lambda s: False) -> tuple:
    """1 対戦の行 → (観測の列, 件数)。

    観測 = {"battle", "slot", "kind" ("move" | "item"), "value", "species", "mega_known", "t"}。
    advice 行を時刻順 (同時刻はファイルの順) に読み、(枠, 種類, 値) の初出だけを観測にする。持ち物は枠ごとに最初の 1 つだけ
    (後で違う持ち物が出たら item_conflict に数える)。その時点で種族が不明・推定なら観測にせず species_unknown に数える"""
    stats = Counter()
    seen: set = set()
    item_slot: dict = {}
    obs: list = []
    advice = [(i, r) for i, r in enumerate(rows) if r.get("type") == "advice" and isinstance(r.get("t"), (int, float))]
    advice.sort(key=lambda ir: (ir[1]["t"], ir[0]))
    for _i, r in advice:
        party = (((r.get("state") or {}).get("opponent") or {}).get("party")) or []
        for slot, mon in enumerate(party):
            if not isinstance(mon, dict):
                continue
            sp_raw = mon.get("species")
            known = bool(sp_raw) and not mon.get("guess")
            sid = canonical(sp_raw) if sp_raw else None
            mega_known = bool(sid) and is_mega(sid)
            for ja in mon.get("revealed") or []:
                mid = resolve_move(ja)
                if not mid:
                    k = (slot, "move_ja", ja)
                    if k not in seen:
                        seen.add(k)
                        stats["move_unresolved"] += 1
                    continue
                k = (slot, "move", mid)
                if k in seen:
                    continue
                seen.add(k)
                if not known:
                    stats["species_unknown_move"] += 1
                    continue
                obs.append({"battle": battle_id, "slot": slot, "kind": "move", "value": mid, "species": sid,
                            "mega_known": mega_known, "t": r["t"]})
            item = mon.get("item")
            if item:
                if item == "megastone":
                    k = (slot, "item_megastone")
                    if k not in seen:
                        seen.add(k)
                        stats["item_megastone_unknown"] += 1
                    continue
                if slot in item_slot:
                    if item_slot[slot] != item and (slot, "item_conflict", item) not in seen:
                        seen.add((slot, "item_conflict", item))
                        stats["item_conflict"] += 1
                    continue
                item_slot[slot] = item
                if not known:
                    stats["species_unknown_item"] += 1
                    continue
                obs.append({"battle": battle_id, "slot": slot, "kind": "item", "value": item, "species": sid,
                            "mega_known": mega_known, "t": r["t"]})
    return obs, dict(stats)


def roster_species(rows: list, canonical: Callable[[str], str] = lambda s: s) -> set:
    """対戦に出てきた相手の種族 (推定でないもの) の集合 (純粋)"""
    out = set()
    for r in rows:
        if r.get("type") != "advice":
            continue
        for mon in (((r.get("state") or {}).get("opponent") or {}).get("party")) or []:
            if isinstance(mon, dict) and mon.get("species") and not mon.get("guess"):
                out.add(canonical(mon["species"]))
    return out


# ------------------------------------------------------------------ 尤度 (純粋)
def obs_prob(view: Optional[dict], kind: str, value: str, floor: float) -> tuple:
    """(確率, 一覧に入っていたか)。view が None なら (None, False)。確率は [floor, 1] に収める"""
    if view is None:
        return None, False
    table = view.get("moves_hold" if kind == "move" else "items") or {}
    p = table.get(value)
    in_list = p is not None and p > 0
    return min(1.0, max(floor, float(p or 0.0))), in_list


def score_observations(obs: list, smogon_view: Callable, db_view: Callable, floor: float) -> list:
    """観測に両方の予測の確率と対数尤度を付ける (純粋)。smogon_view(species, mega_known) / db_view(species) → view or None"""
    out = []
    for o in obs:
        sv = smogon_view(o["species"], o["mega_known"])
        dv = db_view(o["species"])
        ps, ins = obs_prob(sv, o["kind"], o["value"], floor)
        pd, ind = obs_prob(dv, o["kind"], o["value"], floor)
        out.append(dict(o, sm_found=sv is not None, db_found=dv is not None, p_sm=ps, p_db=pd, in_sm=ins, in_db=ind,
                        ll_sm=math.log(ps) if ps else None, ll_db=math.log(pd) if pd else None))
    return out


def bootstrap_ci(per_battle: list, n_boot: int, seed: int, level: float) -> tuple:
    """per_battle: [(差の和, 観測数)] → 観測あたりの平均差の区間 (対戦単位で復元抽出)。対戦が 2 未満なら (None, None)"""
    per_battle = [pb for pb in per_battle if pb[1] > 0]
    if len(per_battle) < 2:
        return None, None
    rng = random.Random(seed)
    k = len(per_battle)
    stats = []
    for _ in range(n_boot):
        s = n = 0.0
        for _j in range(k):
            d, c = per_battle[rng.randrange(k)]
            s += d
            n += c
        stats.append(s / n)
    stats.sort()
    a = (1.0 - level) / 2.0
    lo = stats[max(0, int(math.floor(a * n_boot)))]
    hi = stats[min(n_boot - 1, int(math.ceil((1.0 - a) * n_boot)) - 1)]
    return lo, hi


def mark_learnable(scored: list, learnable: Optional[Callable[[str, str], Optional[bool]]]) -> list:
    """技の観測に learnable (True / False / None = 判定できない) を付ける (純粋)。持ち物は None。
    learnable が None (learnset を渡さない) なら全部 None"""
    out = []
    for r in scored:
        v = learnable(r["species"], r["value"]) if (learnable and r["kind"] == "move") else None
        out.append(dict(r, learnable=v))
    return out


def summarize(scored: list, kind: str, n_boot: int, seed: int, level: float) -> dict:
    """1 種類 (技 / 持ち物) の対応比較の要約 (純粋)"""
    rows = [r for r in scored if r["kind"] == kind]
    paired = [r for r in rows if r["sm_found"] and r["db_found"]]
    per_battle: dict = defaultdict(lambda: [0.0, 0])
    for r in paired:
        pb = per_battle[r["battle"]]
        pb[0] += r["ll_sm"] - r["ll_db"]
        pb[1] += 1
    n = len(paired)
    lo, hi = bootstrap_ci([tuple(v) for v in per_battle.values()], n_boot, seed, level)
    return {
        "kind": kind,
        "n_obs": len(rows),
        "n_paired": n,
        "n_battles_paired": len(per_battle),
        "n_missing_smogon": sum(1 for r in rows if not r["sm_found"]),
        "n_missing_db": sum(1 for r in rows if not r["db_found"]),
        "n_missing_both": sum(1 for r in rows if not r["sm_found"] and not r["db_found"]),
        "mean_ll_smogon": (sum(r["ll_sm"] for r in paired) / n) if n else None,
        "mean_ll_db": (sum(r["ll_db"] for r in paired) / n) if n else None,
        "mean_diff": (sum(r["ll_sm"] - r["ll_db"] for r in paired) / n) if n else None,
        "ci": [lo, hi],
        "ci_level": level,
        "in_list_smogon": (sum(1 for r in paired if r["in_sm"]) / n) if n else None,
        "in_list_db": (sum(1 for r in paired if r["in_db"]) / n) if n else None,
    }


def per_species_table(scored: list) -> list:
    """種ごとの観測数と平均差 (純粋)。観測数の多い順"""
    agg: dict = {}
    for r in scored:
        a = agg.setdefault(r["species"], {"species": r["species"], "n_move": 0, "n_item": 0, "n_paired": 0,
                                          "sum_diff": 0.0, "in_smogon": r["sm_found"], "in_db": r["db_found"]})
        a["n_" + r["kind"]] += 1
        if r["sm_found"] and r["db_found"]:
            a["n_paired"] += 1
            a["sum_diff"] += r["ll_sm"] - r["ll_db"]
    out = []
    for a in agg.values():
        a["mean_diff"] = a["sum_diff"] / a["n_paired"] if a["n_paired"] else None
        del a["sum_diff"]
        out.append(a)
    out.sort(key=lambda a: (-(a["n_move"] + a["n_item"]), a["species"]))
    return out


def coverage(species_set: set, smogon_has: Callable[[str], bool], db_has: Callable[[str], bool]) -> dict:
    """種の集合のうち Smogon / DB に予測がある割合と、無い種 (純粋)"""
    sp = sorted(species_set)
    miss_s = [s for s in sp if not smogon_has(s)]
    miss_d = [s for s in sp if not db_has(s)]
    n = len(sp)
    return {"n_species": n, "smogon_rate": (n - len(miss_s)) / n if n else None, "db_rate": (n - len(miss_d)) / n if n else None,
            "missing_smogon": miss_s, "missing_db": miss_d}


# ------------------------------------------------------------------ report (純粋)
NEXT_STEPS = [
    "改善 (対数尤度の差の区間が 0 より上) が技・持ち物のどちらかで見えたら、助言への影響を調べる (今回は実装しない):",
    "  1. 決定再生 (tools.advice_replay / tools.real_accept の --data-mode restore) で、同じ局面を DB の予測と Smogon の予測 (または混合) で"
    "評価し、推奨の反転率を出す。",
    "  2. 反転した局面で、持ち物 (こだわりスカーフ・きあいのタスキ)・素早さ (配分の分布、ev_infer の仮説)・耐久の誤推定がどれだけ"
    "効いたかを分ける (反転の原因を 1 つずつ入れ替えて再評価)。",
    "  3. 混合比は前半の期間で選び、後半の期間で 1 回だけ評価する (docs/DESIGN_PRIOR_MIXING_20261023.md §3.1)。封印 holdout は流用しない。",
]


def _fmt(x, nd=3) -> str:
    """符号つき (差・区間)"""
    return "—" if x is None else f"{x:+.{nd}f}"


def _f(x, nd=3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def render_markdown(rep: dict) -> str:
    """report の dict → Markdown (純粋)"""
    st, db, bt = rep["stats"], rep["db"], rep["battles"]
    L = [f"# Smogon の Champions 統計の評価 ({st['format']} {st['month']} cutoff {st['cutoff']} vs 使用率 DB snapshot {db['snapshot_id']})", ""]
    L.append(f"生成: {rep['generated_at']}。report の版 {rep['report_version']}。評価の道具だけで、助言・既定の方式・使用率 DB は変えていない。")
    L += ["", "## 1. 評価範囲", "",
          "| 項目 | 値 |", "|---|---|",
          f"| 統計 | {st['format']} / {st['month']} / cutoff {st['cutoff']} / 対戦数 {st.get('number_of_battles')} (重み付きの集計で、独立標本数ではない) |",
          f"| 統計の元 JSON | sha256 {st['sha256']} / {st['size']} バイト / 取得 {st.get('fetched_at_utc')} / {st['url']} |",
          f"| 変換の版 | SMOGON_CONVERT_VERSION {st['convert_version']} (配分の単位 {st.get('spread_unit')}、技を使う確率 = 保有 ÷ {st.get('move_use_divisor')} は暫定で評価に使わない) |",
          f"| 使用率 DB | snapshot {db['snapshot_id']} ({db.get('source')}, {db.get('format')}, fetched_at {db.get('fetched_at')}、時刻帯は未確認) / {db.get('path')} (sha16 {db.get('sha16')}) |",
          f"| 対戦ログ | {bt['logs']} の {bt['n_files']} 本、評価 {bt['n_eval']} 本 ({bt.get('period_eval')}) |",
          f"| 時系列の切り分け | 統計の月の翌月 1 日 0 時 (ローカル) = {bt['eval_start_local']} 以降に始まった対戦だけ。前の対戦 {bt['n_before']} 本・時刻なし {bt['n_no_t']} 本・実戦でない {bt['n_not_real']} 本は除外 |",
          f"| DB の snapshot より前の対戦 | {bt.get('n_eval_before_db_snapshot')} 本 (DB は評価の対戦より後の情報を含みうる: DB に有利な向き) |",
          f"| 確率の下限 | 一覧に無い値は {rep['params']['floor']} (両方に同じ値) |",
          f"| 区間 | 対戦単位のブートストラップ {rep['params']['n_boot']} 回、{int(rep['params']['ci_level'] * 100)}% |",
          f"| メガの見方 (主) | {rep['params']['mega_view']} (pooled = 基本種 + メガを重みで合算、DB と同じ条件) |", ""]
    L += ["## 2. 対応比較 (差 = log p(Smogon) − log p(DB)、観測あたり)", "",
          "| 種類 | 観測 | 対応 (両方に種あり) | 対戦 | log p (Smogon) | log p (DB) | 差 | 区間 | 一覧に入っていた (Smogon / DB) | Smogon に種なし | DB に種なし |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k in KINDS:
        s = rep["results"][k]
        ci = s["ci"]
        L.append(f"| {k} | {s['n_obs']} | {s['n_paired']} | {s['n_battles_paired']} | {_f(s['mean_ll_smogon'])} | {_f(s['mean_ll_db'])} | "
                 f"{_fmt(s['mean_diff'])} | [{_fmt(ci[0])}, {_fmt(ci[1])}] | {_f(s['in_list_smogon'], 2)} / {_f(s['in_list_db'], 2)} | "
                 f"{s['n_missing_smogon']} | {s['n_missing_db']} |")
    L += ["", "感度 (メガの見方を変えたとき):", "", "| 見方 | 種類 | 対応 | 差 | 区間 |", "|---|---|---|---|---|"]
    for view, res in rep["results_by_view"].items():
        for k in KINDS:
            s = res[k]
            L.append(f"| {view} | {k} | {s['n_paired']} | {_fmt(s['mean_diff'])} | [{_fmt(s['ci'][0])}, {_fmt(s['ci'][1])}] |")
    cv = rep["coverage"]
    L += ["", "## 3. カバー率", "", "| 対象 | 種 | Smogon にある | DB にある | Smogon に無い種 | DB に無い種 |", "|---|---|---|---|---|---|"]
    for key, label in (("observed", "観測のあった個体の種"), ("roster", "評価の対戦の相手の種 (推定を除く)")):
        c = cv[key]
        L.append(f"| {label} | {c['n_species']} | {_f(c['smogon_rate'], 2)} | {_f(c['db_rate'], 2)} | {', '.join(c['missing_smogon']) or '—'} | "
                 f"{', '.join(c['missing_db']) or '—'} |")
    ls = rep.get("learnset") or {}
    if ls.get("results"):
        L += ["", f"感度 (種が覚えない技を除いたとき。learnset = {ls['path']}):", "",
              "| 種類 | 対応 | 差 | 区間 |", "|---|---|---|---|"]
        for k in KINDS:
            s = ls["results"][k]
            L.append(f"| {k} | {s['n_paired']} | {_fmt(s['mean_diff'])} | [{_fmt(s['ci'][0])}, {_fmt(s['ci'][1])}] |")
        L += ["", "除いた観測: " + (", ".join(f"{x['species']} {x['value']} ({x['battle']})" for x in ls["not_learnable"]) or "なし")]
    ex = rep["extraction"]
    L += ["", "観測の取り出しで数えなかったもの: " + (", ".join(f"{k} {v}" for k, v in sorted(ex.items())) or "なし"), ""]
    ck = rep["conversion_checks"]
    L += ["## 4. 変換の検査 (落としたもの)", "",
          f"- 種: {ck['species_total']} 項目のうち {ck['species_kept']} を採用、図鑑に無い {len(ck['species_dropped'])} 項目を落とした"
          f" ({', '.join(ck['species_dropped']) or '—'}、重み {ck['species_dropped_weight']:.1f})。",
          f"- 技 (図鑑に無い): {_dropped_line(ck['moves_dropped'])}",
          f"- 持ち物 (名前表に無い): {_dropped_line(ck['items_dropped'])}",
          f"- 特性 (名前表に無い): {_dropped_line(ck['abilities_dropped'])}",
          f"- 保有確率が 1 を超えた技: {ck['move_hold_over_1']} 件 (1 に切った)。個体の重み W の根拠: {ck['weight_basis']}", ""]
    L += ["## 5. 種ごとの観測 (観測の多い順)", "", "| 種 | 技 | 持ち物 | 対応 | 平均差 | Smogon | DB |", "|---|---|---|---|---|---|---|"]
    for a in rep["per_species"]:
        L.append(f"| {a['species']} | {a['n_move']} | {a['n_item']} | {a['n_paired']} | {_fmt(a['mean_diff'])} | "
                 f"{'○' if a['in_smogon'] else '×'} | {'○' if a['in_db'] else '×'} |")
    L += ["", "## 6. 読み方の注意", "",
          "- 判明した技・持ち物だけの尤度で、見えなかった技を「持っていない」とは数えていない。判明しやすい技 (使う技・表示の出る持ち物) に偏る。",
          "- 使用率 DB は上位 10 だけを持つ。一覧に無い値は両方とも下限の確率になる。",
          "- Showdown 上の Champions の模擬 (プレイヤー層・時期) と実機のランクマッチは同じ分布とは限らない (未確認)。",
          "- 観測数が少ない。区間が 0 をまたぐなら差は判定できない。", "",
          "## 7. 次の手順 (設計だけ、今回は実装しない)", ""] + NEXT_STEPS + [""]
    return "\n".join(L)


def _dropped_line(d: dict) -> str:
    if not d:
        return "なし"
    items = sorted(d.items(), key=lambda kv: -kv[1]["weight"])
    return ", ".join(f"{k or '(空)'} ({v['n_species']} 種、重み {v['weight']:.1f})" for k, v in items)


# ------------------------------------------------------------------ 入出力 (副作用)
def load_db_views(db_path: Path, snapshot_id: int, canonical: Callable[[str], str]) -> tuple:
    """使用率 DB (mode=ro) の snapshot → ({種族 id: {"moves_hold", "items"}}, snapshot の情報)。確率 = usage_percent / 100"""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT id, source, format, fetched_at FROM usage_snapshot WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise SystemExit(f"使用率 DB に snapshot {snapshot_id} が無い ({db_path})")
        info = {"snapshot_id": row[0], "source": row[1], "format": row[2], "fetched_at": row[3]}
        views: dict = {}
        for table, col, key in (("move_usage", "move_name", "moves_hold"), ("item_usage", "item_name", "items")):
            for name, val, pct in conn.execute(
                    f"SELECT pokemon_name, {col}, usage_percent FROM {table} WHERE snapshot_id=?", (snapshot_id,)):
                if pct is None:
                    continue
                sid = canonical(name)
                v = views.setdefault(sid, {"moves_hold": {}, "items": {}, "name": name})
                vid = "".join(ch for ch in str(val).lower() if ch.isalnum())
                v[key][vid] = v[key].get(vid, 0.0) + float(pct) / 100.0
    finally:
        conn.close()
    return views, info


def _sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def read_jsonl(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _local(t: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t is not None else None


def _parse_local(s: Optional[str]) -> Optional[float]:
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def build_report(month: str, fmt: str, cutoff: int, payload: dict, manifest: dict, db_path: Path, snapshot_id: int,
                 logs_dir: Path, floor: float = SMOGON_EVAL_PROB_FLOOR, n_boot: int = SMOGON_EVAL_BOOTSTRAP_N,
                 seed: int = SMOGON_EVAL_BOOTSTRAP_SEED, level: float = SMOGON_EVAL_CI,
                 mega_view: str = SMOGON_EVAL_MEGA_VIEW, learnsets_path: Optional[Path] = None) -> dict:
    from advisor import usage_smogon as us
    from vision.normalize import NameResolver

    vocab = us.legal_vocab()
    canonical, is_known = vocab["canonical"], vocab["is_known"]
    conv = us.convert_chaos(payload, vocab["species_ok"], vocab["move_ok"], vocab["item_ok"], vocab["ability_ok"], canonical)
    sm_species = conv["species"]
    sm_index = us.group_index(sm_species, is_known)
    db_views, db_info = load_db_views(db_path, snapshot_id, canonical)

    def is_mega(sid: str) -> bool:
        return us.mega_base_id(sid, is_known) is not None

    def db_view(sid: str):
        return db_views.get(us.group_id(sid, is_known)) or db_views.get(sid)

    def smogon_has(sid: str) -> bool:
        return us.group_view(sm_species, sm_index, sid, is_known, "pooled") is not None

    learnable = None
    if learnsets_path is not None:
        from tools.team_build.learnsets import learnsets, resolve_species
        table = learnsets(str(learnsets_path))
        if not table:
            raise SystemExit(f"learnset が読めない ({learnsets_path})")

        def learnable(sid: str, mid: str) -> Optional[bool]:
            key = resolve_species(sid, table)
            return (mid in table.get(key, ())) if key else None

    resolver = NameResolver()
    cache: dict = {}

    def resolve_move(ja: str) -> Optional[str]:
        if ja not in cache:
            r = resolver.resolve(ja, "moves", cutoff=0.8)     # advisor.engine.opponent_move_pool と同じ閾値
            cache[ja] = r[1] if r else None
        return cache[ja]

    start = eval_start_epoch(month)
    files = sorted(Path(logs_dir).glob("battle_*.jsonl"))
    battles, not_real = [], 0
    for p in files:
        rows = read_jsonl(p)
        sess = next((r for r in rows if r.get("type") == "session"), None)
        if sess is not None and sess.get("dataset_kind") not in (None, "real"):
            not_real += 1
            continue
        battles.append({"id": p.stem, "t0": battle_start(rows), "rows": rows})
    keep, before, no_t = split_by_time(battles, start)
    obs_all: list = []
    extraction = Counter()
    roster: set = set()
    for b in keep:
        obs, stats = extract_observations(b["rows"], b["id"], resolve_move, canonical, is_mega)
        obs_all += obs
        extraction.update(stats)
        roster |= roster_species(b["rows"], canonical)
    db_t = _parse_local(db_info.get("fetched_at"))
    results_by_view = {}
    scored_main = None
    for view in SMOGON_EVAL_MEGA_VIEWS:
        def sm_view(sid, mega_known, _v=view):
            return us.group_view(sm_species, sm_index, sid, is_known, _v if mega_known else "pooled")
        scored = score_observations(obs_all, sm_view, db_view, floor)
        scored = mark_learnable(scored, learnable)
        results_by_view[view] = {k: summarize(scored, k, n_boot, seed, level) for k in KINDS}
        if view == mega_view:
            scored_main = scored
    # 感度: 種が覚えない技 (champions mod の learnset。へんしん の写し・帰属の誤り) を除いたとき
    learnable_kept = [r for r in (scored_main or []) if r.get("learnable") is not False]
    results_learnable = ({k: summarize(learnable_kept, k, n_boot, seed, level) for k in KINDS}
                         if learnable else None)
    not_learnable = [{"battle": r["battle"], "slot": r["slot"], "species": r["species"], "value": r["value"]}
                     for r in (scored_main or []) if r.get("learnable") is False]
    obs_species = {o["species"] for o in obs_all}
    t0s = [b["t0"] for b in keep]
    return {
        "report_version": REPORT_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {"source": "smogon", "format": fmt, "month": month, "cutoff": cutoff, "url": manifest.get("url"),
                  "sha256": manifest.get("sha256"), "size": manifest.get("size"), "fetched_at": manifest.get("fetched_at"),
                  "fetched_at_utc": manifest.get("fetched_at_utc"), "number_of_battles": conv.get("number_of_battles"),
                  "convert_version": SMOGON_CONVERT_VERSION, "manifest_convert_version": manifest.get("convert_version"),
                  "spread_unit": conv["spread_unit"], "move_use_divisor": SMOGON_MOVE_USE_DIVISOR,
                  "n_species_converted": len(sm_species)},
        "db": dict(db_info, path=str(db_path), sha16=_sha16(Path(db_path)), n_species=len(db_views)),
        "battles": {"logs": str(logs_dir), "n_files": len(files), "n_not_real": not_real, "n_before": len(before),
                    "n_no_t": len(no_t), "n_eval": len(keep), "eval_start": start, "eval_start_local": _local(start),
                    "period_eval": [_local(min(t0s)) if t0s else None, _local(max(t0s)) if t0s else None],
                    "n_eval_before_db_snapshot": (sum(1 for t in t0s if t < db_t) if db_t is not None else None),
                    "ids": [b["id"] for b in keep]},
        "params": {"floor": floor, "n_boot": n_boot, "seed": seed, "ci_level": level, "mega_view": mega_view},
        "conversion_checks": conv["checks"],
        "extraction": dict(extraction),
        "results": results_by_view[mega_view],
        "results_by_view": results_by_view,
        "learnset": {"path": str(learnsets_path) if learnsets_path else None,
                     "results": results_learnable, "not_learnable": not_learnable},
        "coverage": {"observed": coverage(obs_species, smogon_has, lambda s: db_view(s) is not None),
                     "roster": coverage(roster, smogon_has, lambda s: db_view(s) is not None)},
        "per_species": per_species_table(scored_main or []),
        "observations": [{k: o[k] for k in ("battle", "slot", "kind", "value", "species", "mega_known", "p_sm", "p_db",
                                             "in_sm", "in_db", "sm_found", "db_found")} for o in (scored_main or [])],
        "next_steps": NEXT_STEPS,
    }


def main() -> None:
    from champions_agent.data.sources.usage_scraper import fetch_smogon_chaos_exact, load_smogon_chaos_saved

    ap = argparse.ArgumentParser(description="使用率 DB と Smogon の Champions 統計を実戦の判明技・持ち物の対数尤度で比べる")
    ap.add_argument("--month", required=True)
    ap.add_argument("--format", required=True, dest="fmt")
    ap.add_argument("--cutoff", required=True, type=int)
    ap.add_argument("--db", required=True, help="使用率 DB (mode=ro で読む。写しを渡す)")
    ap.add_argument("--snapshot", type=int, default=SMOGON_EVAL_DB_SNAPSHOT)
    ap.add_argument("--logs", required=True, help="対戦ログのディレクトリ (logs/battles。読み取りだけ)")
    ap.add_argument("--fetch", action="store_true", help="保存済みが無ければ Smogon から 1 回取る (保存済みなら取らない)")
    ap.add_argument("--smogon-root", default=None, help="元 JSON と manifest の置き場 (既定 SMOGON_USAGE_DIR)")
    ap.add_argument("--learnsets", default=None,
                    help="champions mod の learnsets.ts (感度: 種が覚えない技の観測を除いた比較も出す。読み取りだけ)")
    ap.add_argument("--out", default=str(REPO / "logs" / "usage_prior_eval"))
    args = ap.parse_args()

    root = Path(args.smogon_root) if args.smogon_root else None
    if args.fetch:
        payload, manifest = fetch_smogon_chaos_exact(args.month, args.fmt, args.cutoff, root=root)
    else:
        saved = load_smogon_chaos_saved(args.month, args.fmt, args.cutoff, root)
        if saved is None:
            raise SystemExit("保存済みの統計が無い (--fetch で 1 回取る)")
        payload, manifest = saved
    rep = build_report(args.month, args.fmt, args.cutoff, payload, manifest, Path(args.db), args.snapshot, Path(args.logs),
                       learnsets_path=Path(args.learnsets) if args.learnsets else None)
    out = Path(args.out) / f"{args.month}-{args.fmt}-{args.cutoff}-snap{args.snapshot}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(rep), encoding="utf-8")
    print(f"→ {out}/report.md, report.json")
    for k in KINDS:
        s = rep["results"][k]
        print(f"  {k}: 対応 {s['n_paired']} (対戦 {s['n_battles_paired']})、差 {_fmt(s['mean_diff'])} [{_fmt(s['ci'][0])}, {_fmt(s['ci'][1])}]")


if __name__ == "__main__":
    main()
