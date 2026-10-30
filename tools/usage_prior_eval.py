"""型の事前分布の候補の評価: 使用率 DB (snapshot を固定、または対戦時点) と Smogon の Champions 統計 (月・形式・cutoff を固定) を、
実戦の対戦ログで判明した技・持ち物・特性の対数尤度で対応比較する (2026-10-30 レビュー、開発者承認)。

評価の道具だけ。助言・既定の方式・使用率 DB は変えない。混合比の調整はしない (docs/DESIGN_PRIOR_MIXING_20261023.md §9)。

    python -B -m tools.usage_prior_eval --month 2026-09 --format gen9championsbssregmc --cutoff 1500 \\
        --db <使用率 DB の写し> --snapshot 75 --logs <logs/battles> [--db-snapshot-at-battle] [--fetch] [--out logs/usage_prior_eval]

規則 (純粋な部分はテストする: tests/test_smogon_eval.py):
- 予測の入力と評価の正解を分ける (2026-10-30 レビュー): 正解 = 後から確認できた値 (初めて判明した技・持ち物・特性)。
  予測 = その値が判明する前の情報だけで作った分布。予測の時点は「その個体の判明前の最後の advice 行」(判明した行より前の行) で、
  予測の入力 (種族・メガの判明) はその行の state だけから取る。判明した行とそれより後の行の state は予測に使わない。
  判明前の行が無い (予測時点が無い)・その行で種が未確定 (推定・不明)・予測時点と判明の時点で種の組が違う (枠の取り違えの疑い)・
  図鑑に無い種は除外して件数を残す。
- 予測は事前分布 (DB / Smogon) そのもの。判明した技・持ち物からの条件づけ (更新) は今回はしない。
- 観測: 対戦ログの advice 行の state (相手の party) を時刻順に読み、個体 (対戦 × party の枠) ごとに「初めて判明した技 (revealed)」
  「初めて判明した持ち物 (item)」「初めて判明した特性 (ability)」を 1 標本にする。同じ対戦・同じ個体・同じ値の反復は 1 標本。
  判明の時点で種族が不明・推定 (guess) のものは数えない (件数は残す)。特性は種・形態で 1 つに決まるもの (単一特性・メガ) を数えない
  (状態は判明でなく図鑑から埋めるので、予測の良さを測れない。件数は残す)。
- 事象: 判明した技は「その技を持っている」(保有確率の対数)。見えなかった技を「持っていない」と数えない。持ち物・特性は判明したものだけ
  (メガストーンはメガシンカで分かるが、state の item に出たものだけを数える)。行動の選択確率 (技を使う確率) は作らない・使わない。
- 予測: 使用率 DB = 種ごとの採用率 (move_usage / item_usage / ability_usage の usage_percent / 100。上位だけ。sets.predict の合計 100 への
  正規化は通さない)。Smogon = advisor.usage_smogon の保有確率 / 持ち物・特性の分布。一覧に無い値は SMOGON_EVAL_PROB_FLOOR。
  メガ: DB は基本種にまとめているので基本種で引く。Smogon は既定 (pooled) で基本種 + メガを重みで合算 (DB と同じ条件)、
  known は予測時点でメガ形態が判明していればメガの項目 (感度として併記)。
- 対応比較: 両方の予測に種がある観測だけ。差 = log p(Smogon) − log p(DB) の観測あたりの平均、区間は対戦単位のブートストラップ。
- 時系列: 評価するのは統計の月より後 (翌月 1 日 0 時、ローカル時刻) に始まった対戦だけ。
- DB の snapshot: 固定 (--snapshot) が主。--db-snapshot-at-battle で、対戦ごとに「対戦の開始時刻以前に取得した最新の snapshot」
  (固定 snapshot と同じ source・format の中で fetched_at ≤ 対戦の開始の最大 id) でも比べ、両方を report に出す。
  fetched_at の時刻帯は SMOGON_EVAL_DB_FETCHED_AT_TZ (UTC と解釈。根拠は config のコメント)。
"""
from __future__ import annotations

import argparse
import calendar
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
                                    SMOGON_EVAL_CI, SMOGON_EVAL_DB_FETCHED_AT_TZ, SMOGON_EVAL_DB_SNAPSHOT,
                                    SMOGON_EVAL_MEGA_VIEW, SMOGON_EVAL_MEGA_VIEWS, SMOGON_EVAL_PROB_FLOOR)

REPO = Path(__file__).resolve().parent.parent
REPORT_VERSION = 2
KINDS = ("move", "item", "ability")
# 種類 → 予測の表の鍵 (Smogon の変換・DB の view で同じ)
VIEW_TABLE = {"move": "moves_hold", "item": "items", "ability": "abilities"}

# 除外の理由 (report の表示)。単位は「判明した値 (標本の候補)」
REASON_LABELS = {
    "move_unresolved": "技名を id にできない",
    "species_unknown_move": "判明の時点で種が未確定 (推定・不明)",
    "species_unknown_item": "判明の時点で種が未確定 (推定・不明)",
    "species_unknown_ability": "判明の時点で種が未確定 (推定・不明)",
    "item_megastone_unknown": "辞書外のメガストーン (megastone)",
    "item_conflict": "同じ個体の 2 つ目の違う持ち物 (最初の 1 つだけ数える)",
    "ability_conflict": "同じ個体の 2 つ目の違う特性 (最初の 1 つだけ数える)",
    "ability_fixed": "特性が種・形態で 1 つに決まる (単一特性・メガ。図鑑から埋まる値)",
    "no_pred_time": "予測時点が無い (判明前の advice 行が無い)",
    "pred_species_unknown": "予測時点で種が未確定 (推定・不明)",
    "species_changed": "予測時点と判明の時点で種 (基本種 + メガの組) が違う (枠の取り違えの疑い)",
    "species_not_in_dex": "図鑑に無い種",
    "missing_smogon": "Smogon に種が無い (対応から除外)",
    "missing_db": "DB に種が無い (対応から除外)",
    "no_db_snapshot": "対戦の開始より前の snapshot が無い (対戦時点 snapshot の比較だけ)",
}
# 観測の取り出しの件数の鍵 → 種類
EXTRACTION_KIND = {
    "move_unresolved": "move", "species_unknown_move": "move",
    "species_unknown_item": "item", "item_megastone_unknown": "item", "item_conflict": "item",
    "species_unknown_ability": "ability", "ability_conflict": "ability", "ability_fixed": "ability",
}


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


def parse_db_time(s: Optional[str], tz: str = SMOGON_EVAL_DB_FETCHED_AT_TZ) -> Optional[float]:
    """使用率 DB の fetched_at ('YYYY-MM-DD HH:MM:SS') → epoch 秒 (純粋)。tz = "utc" か "local"。読めなければ None"""
    try:
        st = time.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return float(calendar.timegm(st)) if tz == "utc" else time.mktime(st)


def snapshot_at_battle(snapshots: list, t: Optional[float]) -> Optional[int]:
    """snapshots: [(snapshot id, 取得の epoch 秒 or None)] → 取得が t 以前の最大 id (純粋)。無ければ None"""
    if t is None:
        return None
    cands = [sid for sid, ft in snapshots if ft is not None and ft <= t]
    return max(cands) if cands else None


# ------------------------------------------------------------------ 観測の取り出し (純粋)
def _observation(battle_id: str, slot: int, kind: str, value: str, sid: Optional[str], mega_known: bool, t: float,
                 pred: Optional[dict]) -> dict:
    """観測 1 件。species / mega_known / t は判明した行の値 (正解の側の記録)。pred_* は判明前の最後の行の値 (予測の入力)"""
    return {"battle": battle_id, "slot": slot, "kind": kind, "value": value, "species": sid, "mega_known": mega_known,
            "t": t, "t_reveal": t,
            "t_pred": pred["t"] if pred else None,
            "pred_species": pred["species"] if pred else None,
            "pred_known": bool(pred and pred["known"]),
            "pred_mega_known": bool(pred and pred["mega_known"])}


def extract_observations(rows: list, battle_id: str, resolve_move: Callable[[str], Optional[str]],
                         canonical: Callable[[str], str] = lambda s: s,
                         is_mega: Callable[[str], bool] = lambda s: False,
                         ability_fixed: Callable[[Optional[str], bool], bool] = lambda sid, mega: False) -> tuple:
    """1 対戦の行 → (観測の列, 件数)。

    観測 = {"battle", "slot", "kind" ("move" | "item" | "ability"), "value" (正解), "species", "mega_known", "t" (= "t_reveal"),
            "t_pred", "pred_species", "pred_known", "pred_mega_known"}。
    advice 行を時刻順 (同時刻はファイルの順) に読み、(枠, 種類, 値) の初出だけを観測にする。持ち物・特性は枠ごとに最初の 1 つだけ
    (後で違う値が出たら item_conflict / ability_conflict に数える)。判明の時点で種族が不明・推定なら観測にせず species_unknown_* に数える。
    特性は ability_fixed(種族, メガの印) が真 (種・形態で 1 つに決まる) なら観測にせず ability_fixed に数える。

    予測の入力 (pred_*) は「判明した行より前の advice 行のうち、その枠が載っていた最後の行」の state だけから取る。
    今の行の state は、行を処理し終えてから次の行の予測の入力になる (判明した行・後の行の情報は予測に入らない)"""
    stats = Counter()
    seen: set = set()
    item_slot: dict = {}
    ability_slot: dict = {}
    obs: list = []
    prev: dict = {}      # 枠 → 今の行より前の行での {"t", "species", "known", "mega_known"} (予測の入力の候補)
    advice = [(i, r) for i, r in enumerate(rows) if r.get("type") == "advice" and isinstance(r.get("t"), (int, float))]
    advice.sort(key=lambda ir: (ir[1]["t"], ir[0]))
    for _i, r in advice:
        party = (((r.get("state") or {}).get("opponent") or {}).get("party")) or []
        cur: dict = {}
        for slot, mon in enumerate(party):
            if not isinstance(mon, dict):
                continue
            sp_raw = mon.get("species")
            known = bool(sp_raw) and not mon.get("guess")
            sid = canonical(sp_raw) if sp_raw else None
            mega_known = bool(sid) and is_mega(sid)
            cur[slot] = {"t": r["t"], "species": sid if known else None, "known": known,
                         "mega_known": mega_known if known else False}
            pred = prev.get(slot)        # 前の行の値だけ (この行の state は使わない)
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
                obs.append(_observation(battle_id, slot, "move", mid, sid, mega_known, r["t"], pred))
            item = mon.get("item")
            if item:
                if item == "megastone":
                    k = (slot, "item_megastone")
                    if k not in seen:
                        seen.add(k)
                        stats["item_megastone_unknown"] += 1
                elif slot in item_slot:
                    if item_slot[slot] != item and (slot, "item_conflict", item) not in seen:
                        seen.add((slot, "item_conflict", item))
                        stats["item_conflict"] += 1
                else:
                    item_slot[slot] = item
                    if not known:
                        stats["species_unknown_item"] += 1
                    else:
                        obs.append(_observation(battle_id, slot, "item", item, sid, mega_known, r["t"], pred))
            ability = mon.get("ability")
            if ability:
                if ability_fixed(sid, bool(mon.get("mega"))):
                    k = (slot, "ability_fixed", ability)
                    if k not in seen:
                        seen.add(k)
                        stats["ability_fixed"] += 1
                elif slot in ability_slot:
                    if ability_slot[slot] != ability and (slot, "ability_conflict", ability) not in seen:
                        seen.add((slot, "ability_conflict", ability))
                        stats["ability_conflict"] += 1
                else:
                    ability_slot[slot] = ability
                    if not known:
                        stats["species_unknown_ability"] += 1
                    else:
                        obs.append(_observation(battle_id, slot, "ability", ability, sid, mega_known, r["t"], pred))
        prev.update(cur)                 # この行の state は次の行からの予測の入力になる
    return obs, dict(stats)


def prediction_samples(obs: list, is_known: Callable[[str], bool] = lambda s: True,
                       group_of: Callable[[str], str] = lambda s: s) -> tuple:
    """観測 → (予測の標本, {種類: {除外の理由: 件数}}) (純粋)。

    標本の species / mega_known は予測の入力 (判明前の最後の行の値) で、判明した行の値 (species_at_reveal) は記録だけ。
    予測時点が無い (no_pred_time)・予測時点で種が未確定 (pred_species_unknown)・予測時点と判明の時点で種の組 (group_of =
    基本種 + メガ) が違う (species_changed: 枠の取り違えの疑い)・図鑑に無い種 (species_not_in_dex) は除く"""
    samples: list = []
    excluded: dict = defaultdict(Counter)
    for o in obs:
        if o.get("t_pred") is None:
            excluded[o["kind"]]["no_pred_time"] += 1
            continue
        if not o.get("pred_known") or not o.get("pred_species"):
            excluded[o["kind"]]["pred_species_unknown"] += 1
            continue
        if o.get("species") and group_of(o["pred_species"]) != group_of(o["species"]):
            excluded[o["kind"]]["species_changed"] += 1
            continue
        if not is_known(o["pred_species"]):
            excluded[o["kind"]]["species_not_in_dex"] += 1
            continue
        samples.append({"battle": o["battle"], "slot": o["slot"], "kind": o["kind"], "value": o["value"],
                        "species": o["pred_species"], "mega_known": o["pred_mega_known"],
                        "t_pred": o["t_pred"], "t_reveal": o["t_reveal"], "species_at_reveal": o["species"]})
    return samples, {k: dict(v) for k, v in excluded.items()}


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


def known_slots(rows: list) -> set:
    """種族が確定した (推定でない) 時点のある相手の枠の集合 (純粋)。個体数の分母"""
    out = set()
    for r in rows:
        if r.get("type") != "advice":
            continue
        for slot, mon in enumerate((((r.get("state") or {}).get("opponent") or {}).get("party")) or []):
            if isinstance(mon, dict) and mon.get("species") and not mon.get("guess"):
                out.add(slot)
    return out


# ------------------------------------------------------------------ 尤度 (純粋)
def obs_prob(view: Optional[dict], kind: str, value: str, floor: float) -> tuple:
    """(確率, 一覧に入っていたか)。view が None か、その種類の表を持たないなら (None, False)。確率は [floor, 1] に収める"""
    if view is None or VIEW_TABLE[kind] not in view:
        return None, False
    table = view.get(VIEW_TABLE[kind]) or {}
    p = table.get(value)
    in_list = p is not None and p > 0
    return min(1.0, max(floor, float(p or 0.0))), in_list


def score_observations(obs: list, smogon_view: Callable, db_view: Callable, floor: float) -> list:
    """観測に両方の予測の確率と対数尤度を付ける (純粋)。smogon_view(species, mega_known) / db_view(species) → view or None。
    渡す species / mega_known は予測の入力 (prediction_samples の標本なら判明前の行の値)"""
    out = []
    for o in obs:
        sv = smogon_view(o["species"], o["mega_known"])
        dv = db_view(o["species"])
        ps, ins = obs_prob(sv, o["kind"], o["value"], floor)
        pd, ind = obs_prob(dv, o["kind"], o["value"], floor)
        out.append(dict(o, sm_found=ps is not None, db_found=pd is not None, p_sm=ps, p_db=pd, in_sm=ins, in_db=ind,
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
    """技の観測に learnable (True / False / None = 判定できない) を付ける (純粋)。持ち物・特性は None。
    learnable が None (learnset を渡さない) なら全部 None"""
    out = []
    for r in scored:
        v = learnable(r["species"], r["value"]) if (learnable and r["kind"] == "move") else None
        out.append(dict(r, learnable=v))
    return out


def summarize(scored: list, kind: str, n_boot: int, seed: int, level: float) -> dict:
    """1 種類 (技 / 持ち物 / 特性) の対応比較の要約 (純粋)"""
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
        "n_individuals_paired": len({(r["battle"], r["slot"]) for r in paired}),
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
    """種ごとの観測数・対戦数・個体数と平均差 (純粋)。観測数の多い順"""
    agg: dict = {}
    for r in scored:
        a = agg.setdefault(r["species"], {"species": r["species"], "n_move": 0, "n_item": 0, "n_ability": 0, "n_paired": 0,
                                          "sum_diff": 0.0, "in_smogon": r["sm_found"], "in_db": r["db_found"],
                                          "_battles": set(), "_individuals": set()})
        a["n_" + r["kind"]] += 1
        a["_battles"].add(r["battle"])
        a["_individuals"].add((r["battle"], r["slot"]))
        if r["sm_found"] and r["db_found"]:
            a["n_paired"] += 1
            a["sum_diff"] += r["ll_sm"] - r["ll_db"]
    out = []
    for a in agg.values():
        a["mean_diff"] = a["sum_diff"] / a["n_paired"] if a["n_paired"] else None
        a["n_battles"] = len(a.pop("_battles"))
        a["n_individuals"] = len(a.pop("_individuals"))
        del a["sum_diff"]
        out.append(a)
    out.sort(key=lambda a: (-(a["n_move"] + a["n_item"] + a["n_ability"]), a["species"]))
    return out


def coverage(species_set: set, smogon_has: Callable[[str], bool], db_has: Callable[[str], bool]) -> dict:
    """種の集合のうち Smogon / DB に予測がある割合と、無い種 (純粋)"""
    sp = sorted(species_set)
    miss_s = [s for s in sp if not smogon_has(s)]
    miss_d = [s for s in sp if not db_has(s)]
    n = len(sp)
    return {"n_species": n, "smogon_rate": (n - len(miss_s)) / n if n else None, "db_rate": (n - len(miss_d)) / n if n else None,
            "missing_smogon": miss_s, "missing_db": miss_d}


def exclusion_table(extraction: dict, pred_excluded: dict, results: dict, n_known_individuals: int,
                    individuals_with_obs: dict) -> dict:
    """種類ごとの除外の理由と件数 (純粋)。{kind: {"reasons": {理由: 件数}, "n_individuals": 分母, "n_no_reveal": 判明なしの個体数}}。
    reasons の単位は判明した値 (標本の候補)。missing_* は両方に種がある対応から外れた標本 (results の n_missing_*)"""
    out = {}
    for k in KINDS:
        reasons: dict = {}
        for key, v in (extraction or {}).items():
            if EXTRACTION_KIND.get(key) == k and v:
                reasons[key] = v
        for key, v in ((pred_excluded or {}).get(k) or {}).items():
            if v:
                reasons[key] = v
        s = results.get(k) or {}
        if s.get("n_missing_smogon"):
            reasons["missing_smogon"] = s["n_missing_smogon"]
        if s.get("n_missing_db"):
            reasons["missing_db"] = s["n_missing_db"]
        n_with = individuals_with_obs.get(k, 0)
        out[k] = {"reasons": reasons, "n_individuals": n_known_individuals, "n_individuals_with_obs": n_with,
                  "n_no_reveal": max(0, n_known_individuals - n_with)}
    return out


# ------------------------------------------------------------------ report (純粋)
NEXT_STEPS = [
    "改善 (対数尤度の差の区間が 0 より上) が技・持ち物・特性のどれかで見えたら、助言への影響を調べる (今回は実装しない):",
    "  1. 決定再生 (tools.advice_replay / tools.real_accept の --data-mode restore) で、同じ局面を DB の予測と Smogon の予測 (または混合) で"
    "評価し、推奨の反転率を出す。",
    "  2. 反転した局面で、持ち物 (こだわりスカーフ・きあいのタスキ)・素早さ (配分の分布、ev_infer の仮説)・耐久の誤推定がどれだけ"
    "効いたかを分ける (反転の原因を 1 つずつ入れ替えて再評価)。",
    "  3. 混合比は前半の期間で選び、後半の期間で 1 回だけ評価する (docs/DESIGN_PRIOR_MIXING_20261023.md §3.1)。封印 holdout は流用しない。",
    "  4. 判明した技・持ち物からの条件づけ (更新) は別の単位 (今回は事前分布そのものの比較だけ)。",
]


def _fmt(x, nd=3) -> str:
    """符号つき (差・区間)"""
    return "—" if x is None else f"{x:+.{nd}f}"


def _f(x, nd=3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _comparison_rows(results: dict) -> list:
    L = ["| 種類 | 観測 | 対応 (両方に種あり) | 対戦 | 個体 | log p (Smogon) | log p (DB) | 差 | 区間 | 一覧に入っていた (Smogon / DB) "
         "| Smogon に種なし | DB に種なし |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k in KINDS:
        s = results[k]
        ci = s["ci"]
        L.append(f"| {k} | {s['n_obs']} | {s['n_paired']} | {s['n_battles_paired']} | {s.get('n_individuals_paired', '—')} | "
                 f"{_f(s['mean_ll_smogon'])} | {_f(s['mean_ll_db'])} | {_fmt(s['mean_diff'])} | [{_fmt(ci[0])}, {_fmt(ci[1])}] | "
                 f"{_f(s['in_list_smogon'], 2)} / {_f(s['in_list_db'], 2)} | {s['n_missing_smogon']} | {s['n_missing_db']} |")
    return L


def render_markdown(rep: dict) -> str:
    """report の dict → Markdown (純粋)"""
    st, db, bt = rep["stats"], rep["db"], rep["battles"]
    L = [f"# Smogon の Champions 統計の評価 ({st['format']} {st['month']} cutoff {st['cutoff']} vs 使用率 DB snapshot {db['snapshot_id']})", ""]
    L.append(f"生成: {rep['generated_at']}。report の版 {rep['report_version']}。評価の道具だけで、助言・既定の方式・使用率 DB は変えていない。")
    ab = rep.get("db_at_battle") or {}
    L += ["", "## 1. 評価範囲", "",
          "| 項目 | 値 |", "|---|---|",
          f"| 統計 | {st['format']} / {st['month']} / cutoff {st['cutoff']} / 対戦数 {st.get('number_of_battles')} (重み付きの集計で、独立標本数ではない) |",
          f"| 統計の元 JSON | sha256 {st['sha256']} / {st['size']} バイト / 取得 {st.get('fetched_at_utc')} / {st['url']} |",
          f"| 変換の版 | SMOGON_CONVERT_VERSION {st['convert_version']} (manifest の記録 {st.get('manifest_convert_version')}、配分の単位 "
          f"{st.get('spread_unit')})。技は保有確率だけで、行動の選択確率 (技を使う確率) は作らない |",
          f"| 使用率 DB (固定) | snapshot {db['snapshot_id']} ({db.get('source')}, {db.get('format')}, fetched_at {db.get('fetched_at')} "
          f"= {db.get('fetched_at_local')} ローカル、{db.get('fetched_at_tz')} と解釈) / {db.get('path')} (sha16 {db.get('sha16')})"
          + (f"、無い表 {', '.join(db['tables_missing'])}" if db.get("tables_missing") else "") + " |",
          f"| 使用率 DB (対戦時点) | " + (f"有効。{ab.get('rule')}" if ab.get("enabled") else "無効 (--db-snapshot-at-battle で有効)") + " |",
          f"| 対戦ログ | {bt['logs']} の {bt['n_files']} 本、評価 {bt['n_eval']} 本 ({bt.get('period_eval')}) |",
          f"| 時系列の切り分け | 統計の月の翌月 1 日 0 時 (ローカル) = {bt['eval_start_local']} 以降に始まった対戦だけ。前の対戦 {bt['n_before']} 本・時刻なし {bt['n_no_t']} 本・実戦でない {bt['n_not_real']} 本は除外 |",
          f"| 固定 snapshot より前の対戦 | {bt.get('n_eval_before_db_snapshot')} 本 (固定 snapshot は評価の対戦より後の情報を含みうる: DB に有利な向き) |",
          f"| 予測の入力と正解 | 正解 = 初めて判明した技・持ち物・特性。予測 = 判明前の最後の advice 行の state (種族・メガの判明) だけで引いた事前分布。"
          f"判明した行・後の行の state は予測に使わない。判明技からの条件づけ (更新) はしない |",
          f"| 確率の下限 | 一覧に無い値は {rep['params']['floor']} (両方に同じ値) |",
          f"| 区間 | 対戦単位のブートストラップ {rep['params']['n_boot']} 回、{int(rep['params']['ci_level'] * 100)}% |",
          f"| メガの見方 (主) | {rep['params']['mega_view']} (pooled = 基本種 + メガを重みで合算、DB と同じ条件) |", ""]
    L += ["## 2. 対応比較 (差 = log p(Smogon) − log p(DB)、観測あたり)", "", "### 2.1 固定 snapshot", ""]
    L += _comparison_rows(rep["results"])
    L += ["", "感度 (メガの見方を変えたとき):", "", "| 見方 | 種類 | 対応 | 差 | 区間 |", "|---|---|---|---|---|"]
    for view, res in rep["results_by_view"].items():
        for k in KINDS:
            s = res[k]
            L.append(f"| {view} | {k} | {s['n_paired']} | {_fmt(s['mean_diff'])} | [{_fmt(s['ci'][0])}, {_fmt(s['ci'][1])}] |")
    if ab.get("enabled"):
        used = ", ".join(f"{sid} ({n} 本)" for sid, n in sorted((ab.get("snapshots_used") or {}).items(), key=lambda kv: int(kv[0])))
        L += ["", "### 2.2 対戦時点の snapshot", "", f"規則: {ab.get('rule')}", "",
              f"使った snapshot (対戦数): {used or '—'}。対戦の開始より前の snapshot が無い対戦 {ab.get('n_battles_no_snapshot')} 本"
              f" (標本 {ab.get('n_samples_no_snapshot')})。", ""]
        L += _comparison_rows(ab["results"])
        L += ["", "固定と対戦時点の DB の対数尤度 (同じ標本、両方に種があるもの):", "",
              "| 種類 | 標本 | log p (DB 固定) | log p (DB 対戦時点) | 差 (対戦時点 − 固定) |", "|---|---|---|---|---|"]
        for k in KINDS:
            c = (ab.get("db_fixed_vs_at_battle") or {}).get(k) or {}
            L.append(f"| {k} | {c.get('n', 0)} | {_f(c.get('mean_ll_fixed'))} | {_f(c.get('mean_ll_at_battle'))} | {_fmt(c.get('mean_diff'))} |")
    cv = rep["coverage"]
    L += ["", "## 3. カバー率・評価できた件数・除外", "", "### 3.1 カバー率", "",
          "| 対象 | 種 | Smogon にある | DB にある | Smogon に無い種 | DB に無い種 |", "|---|---|---|---|---|---|"]
    for key, label in (("observed", "標本のあった個体の種 (予測の入力の種)"), ("roster", "評価の対戦の相手の種 (推定を除く)")):
        c = cv[key]
        L.append(f"| {label} | {c['n_species']} | {_f(c['smogon_rate'], 2)} | {_f(c['db_rate'], 2)} | {', '.join(c['missing_smogon']) or '—'} | "
                 f"{', '.join(c['missing_db']) or '—'} |")
    ex = rep.get("exclusions") or {}
    L += ["", "### 3.2 評価できた対戦数・個体数", "",
          "| 種類 | 対応の標本 | 対戦 | 個体 | 種の確定した個体 (分母) | 標本の候補があった個体 | 判明なしの個体 |", "|---|---|---|---|---|---|---|"]
    for k in KINDS:
        s = rep["results"][k]
        e = ex.get(k) or {}
        L.append(f"| {k} | {s['n_paired']} | {s['n_battles_paired']} | {s.get('n_individuals_paired', '—')} | {e.get('n_individuals', '—')} | "
                 f"{e.get('n_individuals_with_obs', '—')} | {e.get('n_no_reveal', '—')} |")
    L += ["", "### 3.3 除外した標本と理由 (単位は判明した値。判明なしの個体は 3.2)", "", "| 種類 | 理由 | 件数 |", "|---|---|---|"]
    any_reason = False
    for k in KINDS:
        for key, v in sorted(((ex.get(k) or {}).get("reasons") or {}).items()):
            any_reason = True
            L.append(f"| {k} | {REASON_LABELS.get(key, key)} ({key}) | {v} |")
    if not any_reason:
        L.append("| — | なし | 0 |")
    ls = rep.get("learnset") or {}
    if ls.get("results"):
        L += ["", f"感度 (種が覚えない技を除いたとき。learnset = {ls['path']}):", "",
              "| 種類 | 対応 | 差 | 区間 |", "|---|---|---|---|"]
        for k in KINDS:
            s = ls["results"][k]
            L.append(f"| {k} | {s['n_paired']} | {_fmt(s['mean_diff'])} | [{_fmt(s['ci'][0])}, {_fmt(s['ci'][1])}] |")
        L += ["", "除いた観測: " + (", ".join(f"{x['species']} {x['value']} ({x['battle']})" for x in ls["not_learnable"]) or "なし")]
    L.append("")
    ck = rep["conversion_checks"]
    L += ["## 4. 変換の検査 (落としたもの)", "",
          f"- 種: {ck['species_total']} 項目のうち {ck['species_kept']} を採用、図鑑に無い {len(ck['species_dropped'])} 項目を落とした"
          f" ({', '.join(ck['species_dropped']) or '—'}、重み {ck['species_dropped_weight']:.1f})。",
          f"- 技 (図鑑に無い): {_dropped_line(ck['moves_dropped'])}",
          f"- 持ち物 (名前表に無い): {_dropped_line(ck['items_dropped'])}",
          f"- 特性 (名前表に無い): {_dropped_line(ck['abilities_dropped'])}",
          f"- 保有確率が 1 を超えた技: {ck['move_hold_over_1']} 件 (1 に切った)。個体の重み W の根拠: {ck['weight_basis']}", ""]
    L += ["## 5. 種ごとの観測 (観測の多い順。種は予測の入力の種)", "",
          "| 種 | 技 | 持ち物 | 特性 | 対戦 | 個体 | 対応 | 平均差 | Smogon | DB |", "|---|---|---|---|---|---|---|---|---|---|"]
    for a in rep["per_species"]:
        L.append(f"| {a['species']} | {a['n_move']} | {a['n_item']} | {a.get('n_ability', 0)} | {a.get('n_battles', '—')} | "
                 f"{a.get('n_individuals', '—')} | {a['n_paired']} | {_fmt(a['mean_diff'])} | "
                 f"{'○' if a['in_smogon'] else '×'} | {'○' if a['in_db'] else '×'} |")
    L += ["", "## 6. 読み方の注意", "",
          "- 判明した技・持ち物・特性だけの尤度で、見えなかった技を「持っていない」とは数えていない。判明しやすい技 (使う技・表示の出る持ち物・"
          "場に出たときに表示の出る特性) に偏る。",
          "- 予測は事前分布そのもので、判明した技・持ち物からの条件づけはしていない (実際の助言は条件づけた分布を使う)。",
          "- 使用率 DB は上位だけを持つ。一覧に無い値は両方とも下限の確率になる。",
          "- 特性は種・形態で 1 つに決まるもの (単一特性・メガ) を除いている (状態は図鑑から埋まるので予測の良さを測れない)。",
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
_DB_TABLES = (("move_usage", "move_name", "moves_hold"), ("item_usage", "item_name", "items"),
              ("ability_usage", "ability_name", "abilities"))


def load_db_views(db_path: Path, snapshot_id: int, canonical: Callable[[str], str]) -> tuple:
    """使用率 DB (mode=ro) の snapshot → ({種族 id: {"moves_hold", "items", "abilities"}}, snapshot の情報)。確率 = usage_percent / 100。
    DB に無い表はその鍵を view に入れない (その種類は「DB に種なし」になる) で、info["tables_missing"] に残す"""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT id, source, format, fetched_at FROM usage_snapshot WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise SystemExit(f"使用率 DB に snapshot {snapshot_id} が無い ({db_path})")
        info = {"snapshot_id": row[0], "source": row[1], "format": row[2], "fetched_at": row[3]}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        present = [t for t in _DB_TABLES if t[0] in tables]
        info["tables_missing"] = [t[0] for t in _DB_TABLES if t[0] not in tables]
        views: dict = {}
        for table, col, key in present:
            for name, val, pct in conn.execute(
                    f"SELECT pokemon_name, {col}, usage_percent FROM {table} WHERE snapshot_id=?", (snapshot_id,)):
                if pct is None:
                    continue
                sid = canonical(name)
                v = views.setdefault(sid, dict({t[2]: {} for t in present}, name=name))
                vid = "".join(ch for ch in str(val).lower() if ch.isalnum())
                v[key][vid] = v[key].get(vid, 0.0) + float(pct) / 100.0
    finally:
        conn.close()
    return views, info


def list_db_snapshots(db_path: Path, source: str, fmt: str) -> list:
    """使用率 DB (mode=ro) の同じ source・format の snapshot → [(id, fetched_at の文字列)] (id の昇順)"""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        return [(int(r[0]), r[1]) for r in conn.execute(
            "SELECT id, fetched_at FROM usage_snapshot WHERE source=? AND format=? ORDER BY id", (source, fmt))]
    finally:
        conn.close()


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


def _db_view_fn(views: dict, group_of: Callable[[str], str]) -> Callable:
    def db_view(sid: str):
        return views.get(group_of(sid)) or views.get(sid)
    return db_view


def build_report(month: str, fmt: str, cutoff: int, payload: dict, manifest: dict, db_path: Path, snapshot_id: int,
                 logs_dir: Path, floor: float = SMOGON_EVAL_PROB_FLOOR, n_boot: int = SMOGON_EVAL_BOOTSTRAP_N,
                 seed: int = SMOGON_EVAL_BOOTSTRAP_SEED, level: float = SMOGON_EVAL_CI,
                 mega_view: str = SMOGON_EVAL_MEGA_VIEW, learnsets_path: Optional[Path] = None,
                 db_at_battle: bool = False, db_tz: str = SMOGON_EVAL_DB_FETCHED_AT_TZ) -> dict:
    from advisor import usage_smogon as us
    from vision.abilities import fixed_ability
    from vision.normalize import NameResolver

    vocab = us.legal_vocab()
    canonical, is_known = vocab["canonical"], vocab["is_known"]
    conv = us.convert_chaos(payload, vocab["species_ok"], vocab["move_ok"], vocab["item_ok"], vocab["ability_ok"], canonical)
    sm_species = conv["species"]
    sm_index = us.group_index(sm_species, is_known)
    db_views, db_info = load_db_views(db_path, snapshot_id, canonical)

    def is_mega(sid: str) -> bool:
        return us.mega_base_id(sid, is_known) is not None

    def group_of(sid: str) -> str:
        return us.group_id(sid, is_known)

    db_view = _db_view_fn(db_views, group_of)

    def ability_fixed(sid: Optional[str], mega_flag: bool) -> bool:
        if not sid:
            return False
        return bool(mega_flag) or is_mega(sid) or fixed_ability(sid) is not None

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
    individuals: set = set()
    for b in keep:
        obs, stats = extract_observations(b["rows"], b["id"], resolve_move, canonical, is_mega, ability_fixed)
        obs_all += obs
        extraction.update(stats)
        roster |= roster_species(b["rows"], canonical)
        individuals |= {(b["id"], s) for s in known_slots(b["rows"])}
    # 予測の入力 (判明前の最後の行の種族・メガ) だけの標本にする。判明した行・後の行の state は使わない
    samples, pred_excluded = prediction_samples(obs_all, is_known, group_of)
    db_t = parse_db_time(db_info.get("fetched_at"), db_tz)

    def sm_view_for(view: str) -> Callable:
        def sm_view(sid, mega_known):
            return us.group_view(sm_species, sm_index, sid, is_known, view if mega_known else "pooled")
        return sm_view

    results_by_view = {}
    scored_main = None
    for view in SMOGON_EVAL_MEGA_VIEWS:
        scored = score_observations(samples, sm_view_for(view), db_view, floor)
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

    # 対戦時点の snapshot (対戦の開始以前に取得した最新。固定 snapshot と同じ source・format)
    at_battle = {"enabled": False}
    if db_at_battle:
        snaps = [(sid, fa, parse_db_time(fa, db_tz)) for sid, fa in list_db_snapshots(db_path, db_info["source"], db_info["format"])]
        snap_of = {b["id"]: snapshot_at_battle([(sid, ep) for sid, _fa, ep in snaps], b["t0"]) for b in keep}
        groups: dict = defaultdict(list)
        no_snap = [s for s in samples if snap_of.get(s["battle"]) is None]
        for s in samples:
            if snap_of.get(s["battle"]) is not None:
                groups[snap_of[s["battle"]]].append(s)
        views_cache: dict = {}
        scored_ab: list = []
        sm_main = sm_view_for(mega_view)
        for sid in sorted(groups):
            if sid not in views_cache:
                views_cache[sid] = load_db_views(db_path, sid, canonical)[0]
            scored_ab += score_observations(groups[sid], sm_main, _db_view_fn(views_cache[sid], group_of), floor)
        # 同じ標本で、固定の DB と対戦時点の DB の対数尤度を並べる (両方に種があるもの)
        fixed_by_key = {(r["battle"], r["slot"], r["kind"], r["value"]): r for r in (scored_main or [])}
        cmp_ = {}
        for k in KINDS:
            pairs = [(fixed_by_key[(r["battle"], r["slot"], r["kind"], r["value"])], r) for r in scored_ab
                     if r["kind"] == k and r["db_found"] and (r["battle"], r["slot"], r["kind"], r["value"]) in fixed_by_key
                     and fixed_by_key[(r["battle"], r["slot"], r["kind"], r["value"])]["db_found"]]
            n = len(pairs)
            cmp_[k] = {"n": n,
                       "mean_ll_fixed": (sum(f["ll_db"] for f, _a in pairs) / n) if n else None,
                       "mean_ll_at_battle": (sum(a["ll_db"] for _f, a in pairs) / n) if n else None,
                       "mean_diff": (sum(a["ll_db"] - f["ll_db"] for f, a in pairs) / n) if n else None}
        used = Counter(v for v in snap_of.values() if v is not None)
        at_battle = {
            "enabled": True,
            "rule": (f"対戦ごとに、固定 snapshot と同じ source ({db_info['source']})・format ({db_info['format']}) の snapshot のうち "
                     f"fetched_at ({db_tz} と解釈) が対戦の開始時刻以前の最大 id"),
            "fetched_at_tz": db_tz,
            "snapshots": [{"id": sid, "fetched_at": fa, "fetched_at_local": _local(ep)} for sid, fa, ep in snaps],
            "snapshot_of_battle": snap_of,
            "snapshots_used": {str(k): v for k, v in used.items()},
            "n_battles_no_snapshot": sum(1 for v in snap_of.values() if v is None),
            "n_samples_no_snapshot": len(no_snap),
            "n_samples_no_snapshot_by_kind": dict(Counter(s["kind"] for s in no_snap)),
            "results": {k: summarize(scored_ab, k, n_boot, seed, level) for k in KINDS},
            "db_fixed_vs_at_battle": cmp_,
        }

    obs_species = {o["species"] for o in samples}
    t0s = [b["t0"] for b in keep]
    with_obs = {k: len({(o["battle"], o["slot"]) for o in obs_all if o["kind"] == k}) for k in KINDS}
    results_main = results_by_view[mega_view]
    return {
        "report_version": REPORT_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {"source": "smogon", "format": fmt, "month": month, "cutoff": cutoff, "url": manifest.get("url"),
                  "sha256": manifest.get("sha256"), "size": manifest.get("size"), "fetched_at": manifest.get("fetched_at"),
                  "fetched_at_utc": manifest.get("fetched_at_utc"), "number_of_battles": conv.get("number_of_battles"),
                  "convert_version": SMOGON_CONVERT_VERSION, "manifest_convert_version": manifest.get("convert_version"),
                  "spread_unit": conv["spread_unit"], "move_use_probability": "作らない (保有確率だけ)",
                  "n_species_converted": len(sm_species)},
        "db": dict(db_info, path=str(db_path), sha16=_sha16(Path(db_path)), n_species=len(db_views),
                   fetched_at_tz=db_tz, fetched_at_local=_local(db_t)),
        "battles": {"logs": str(logs_dir), "n_files": len(files), "n_not_real": not_real, "n_before": len(before),
                    "n_no_t": len(no_t), "n_eval": len(keep), "eval_start": start, "eval_start_local": _local(start),
                    "period_eval": [_local(min(t0s)) if t0s else None, _local(max(t0s)) if t0s else None],
                    "n_eval_before_db_snapshot": (sum(1 for t in t0s if t < db_t) if db_t is not None else None),
                    "ids": [b["id"] for b in keep]},
        "params": {"floor": floor, "n_boot": n_boot, "seed": seed, "ci_level": level, "mega_view": mega_view},
        "prediction": {"input": "判明前の最後の advice 行の state (種族・メガの判明) だけ。判明した行・後の行は使わない",
                       "conditioning": "なし (事前分布そのもの)"},
        "conversion_checks": conv["checks"],
        "extraction": dict(extraction),
        "prediction_excluded": pred_excluded,
        "exclusions": exclusion_table(dict(extraction), pred_excluded, results_main, len(individuals), with_obs),
        "results": results_main,
        "results_by_view": results_by_view,
        "db_at_battle": at_battle,
        "learnset": {"path": str(learnsets_path) if learnsets_path else None,
                     "results": results_learnable, "not_learnable": not_learnable},
        "coverage": {"observed": coverage(obs_species, smogon_has, lambda s: db_view(s) is not None),
                     "roster": coverage(roster, smogon_has, lambda s: db_view(s) is not None)},
        "per_species": per_species_table(scored_main or []),
        "observations": [{k: o[k] for k in ("battle", "slot", "kind", "value", "species", "species_at_reveal", "mega_known",
                                             "t_pred", "t_reveal", "p_sm", "p_db", "in_sm", "in_db", "sm_found", "db_found")}
                         for o in (scored_main or [])],
        "next_steps": NEXT_STEPS,
    }


def main() -> None:
    from champions_agent.data.sources.usage_scraper import fetch_smogon_chaos_exact, load_smogon_chaos_saved

    ap = argparse.ArgumentParser(description="使用率 DB と Smogon の Champions 統計を実戦の判明技・持ち物・特性の対数尤度で比べる")
    ap.add_argument("--month", required=True)
    ap.add_argument("--format", required=True, dest="fmt")
    ap.add_argument("--cutoff", required=True, type=int)
    ap.add_argument("--db", required=True, help="使用率 DB (mode=ro で読む。写しを渡す)")
    ap.add_argument("--snapshot", type=int, default=SMOGON_EVAL_DB_SNAPSHOT)
    ap.add_argument("--db-snapshot-at-battle", action="store_true",
                    help="固定 snapshot に加えて、対戦ごとに対戦の開始以前の最新 snapshot (同じ source・format) でも比べる")
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
                       learnsets_path=Path(args.learnsets) if args.learnsets else None,
                       db_at_battle=args.db_snapshot_at_battle)
    out = Path(args.out) / f"{args.month}-{args.fmt}-{args.cutoff}-snap{args.snapshot}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(rep), encoding="utf-8")
    print(f"→ {out}/report.md, report.json")
    for label, res in (("固定", rep["results"]),) + ((("対戦時点", rep["db_at_battle"]["results"]),)
                                                      if rep["db_at_battle"].get("enabled") else ()):
        for k in KINDS:
            s = res[k]
            print(f"  [{label}] {k}: 対応 {s['n_paired']} (対戦 {s['n_battles_paired']}、個体 {s['n_individuals_paired']})、"
                  f"差 {_fmt(s['mean_diff'])} [{_fmt(s['ci'][0])}, {_fmt(s['ci'][1])}]")


if __name__ == "__main__":
    main()
