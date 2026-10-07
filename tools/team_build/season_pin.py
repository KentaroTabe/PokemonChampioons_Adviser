"""季節 (規制) ごとの固定 (2026-10-06 判断、TEAM_BUILD_PENDING_1005 §7.4-3 / §15.2 / §16): 分割の seed、相手プールの使用率スナップショット、
実在の構築のバンクの区切り。

S2 は 相手プール (使用率のスナップショットから合成、mixed なら実戦で当たった構築を先に) → 系統 → SEARCH / SELECTION / HOLDOUT の層化分割
→ SEARCH の fold を決める。run ごとに seed やスナップショットが違うと分割が変わり、参照 (登録チーム) の適応モデルや fold の記録を run 間で
再利用できない (1 run で数時間)。評価側の META_PIN / POOL_PIN と同じ運用で、規制 (レギュレーション) ごとに最初の run の時点の
  - 分割の seed
  - 使用率スナップショットの id (latest / mixed のプールの元)
  - 実在の構築のバンクの区切り (この時刻より後の対戦は mixed のプールに入れない)
を logs/registry/season_pins.json に記録し、同じ規制の run は同じものを使う。

- holdout は封印のまま: 同じ入力 + 同じ seed なら同じ系統が封印される。採否は封印した holdout が守る。
- 測定の相手列 (S8a / S8b / S10 / holdout の対戦の順) の seed は run の seed のまま。同じ対戦を繰り返さない。
- 固定し直すのは pokedb の新シーズンのデータ (100 構築以上) が出たときに 1 回 (`--repin <規制>`)。
- 鮮度: 固定したスナップショットと最新の上位 N 種 (順位の重みつき) の重なりが BUILD_POOL_FRESHNESS_WARN (0.8) を切ったら警告
  (`--check`、triggers の記録)。固定し直すかはその時に判断する。
- run の manifest には run の seed (seed) と固定 (split_seed / season_pin) の両方を残す。

    python -m tools.team_build.season_pin --show
    python -m tools.team_build.season_pin --check
    python -m tools.team_build.season_pin --repin gen9championsbssregmb [--snapshot 61] [--seed 20260906]
    python -m tools.team_build.season_pin --check-splits [--runs-dir logs/build_search/runs] [--pins logs/registry/season_pins.json]

分割の独立性 (--check-splits、2026-10-07 docs/USEFULNESS_VERIFICATION_PLAN_1007.md §7・§10): 固定 (season_pin) を使った run の
opponent_families.json から、(1) 同じ構築・同じ系統が search / selection / holdout をまたいでいないか、(2) search の fold A / B / V が
重ならず search と一致するか、(3) 層をまたいで似た構築 (種族集合の Jaccard が系統の閾値以上) が無いか (メガ軸が違う・系統化の
貪欲な割当で別系統になったもの)、(4) 同じ固定を使う run どうしで同じ構築が別の層に入っていないか、を数える。
読むのは構築の id と種族の集合だけで、holdout の結果は読まない (件数だけを出す)。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import (
    BUILD_FAMILY_JACCARD, BUILD_POOL_FRESHNESS_TOP_N, BUILD_POOL_FRESHNESS_WARN, BUILD_SEASON_PIN)

REPO = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO / "logs" / "registry" / "season_pins.json"
RUNS_DIR = REPO / "logs" / "build_search" / "runs"
PIN_KEYS = ("seed", "pool_snapshot_id", "roster_until")


def _key(regulation: Optional[str]) -> str:
    return str(regulation or "").strip() or "default"


def _stamp(now: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now if now is not None else time.time()))


# ------------------------------------------------------------------ 純粋
def resolve_pin(regulation: Optional[str], run_seed: int, latest_snapshot_id: Optional[int], now: Optional[float], table: Optional[dict],
                fixed: bool = True, override_seed: Optional[int] = None) -> tuple:
    """(固定, 由来, 更新した表) を返す (純粋)。固定 = {"seed", "pool_snapshot_id", "roster_until"}。
    由来: "run" (固定しない: run の seed、最新のスナップショット) / "fixed:<規制>" (表にある) / "fixed:new" (この run の seed と最新の
    スナップショット、今の時刻を区切りとして登録) / 末尾 "+arg" (seed を --split-seed で上書き。表は変えない)"""
    table = dict(table or {})
    if not fixed:
        pin = {"seed": int(run_seed), "pool_snapshot_id": None, "roster_until": None}
        src = "run"
    else:
        key = _key(regulation)
        entry = table.get(key)
        if entry and entry.get("seed") is not None:
            pin = {"seed": int(entry["seed"]), "pool_snapshot_id": entry.get("pool_snapshot_id"), "roster_until": entry.get("roster_until")}
            src = f"fixed:{key}"
        else:
            pin = {"seed": int(run_seed), "pool_snapshot_id": latest_snapshot_id,
                   "roster_until": (round(float(now), 2) if now is not None else None)}
            table[key] = dict(pin, created_at=_stamp(now), source="first_run")
            src = "fixed:new"
    if override_seed is not None:
        pin = dict(pin, seed=int(override_seed))
        src += "+arg"
    return pin, src, table


def rank_weights(top: list) -> dict:
    """[(種, 順位)] → 正規化した重み (順位 1 が最大: N − 順位 + 1)。空なら空"""
    n = len(top)
    raw = {str(s): float(n - int(r) + 1) for s, r in top}
    tot = sum(raw.values()) or 1.0
    return {s: w / tot for s, w in raw.items()}


def weighted_overlap(top_a: list, top_b: list) -> Optional[float]:
    """2 つの上位 N 種 ([(種, 順位)]) の重なり (順位の重みつき、0〜1): 両方に居る種ごとに重みの小さい方を足す。どちらかが空なら None"""
    if not top_a or not top_b:
        return None
    wa, wb = rank_weights(top_a), rank_weights(top_b)
    return round(sum(min(wa[s], wb[s]) for s in set(wa) & set(wb)), 4)


def freshness_row(pinned: Optional[int], latest: Optional[int], overlap: Optional[float], top_n: int,
                  warn_below: float = BUILD_POOL_FRESHNESS_WARN) -> dict:
    """鮮度の 1 行 (純粋): 固定と最新が同じなら重なりは 1、警告は重なりが閾値未満のときだけ"""
    same = pinned is not None and latest is not None and int(pinned) == int(latest)
    ov = 1.0 if same else overlap
    return {"pinned": pinned, "latest": latest, "top_n": top_n, "overlap": ov, "same": same,
            "warn": (ov is not None and ov < warn_below), "warn_below": warn_below}


# ------------------------------------------------------------------ 表の読み書き
def load_table(path: Optional[Path] = None) -> dict:
    p = Path(path) if path is not None else PINS_PATH
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return dict(doc) if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_table(table: dict, path: Optional[Path] = None) -> Path:
    p = Path(path) if path is not None else PINS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


# ------------------------------------------------------------------ 使用率 DB
def latest_snapshot() -> Optional[int]:
    """最新の使用率スナップショット (meta_sets のあるもの)。DB が読めなければ None"""
    try:
        from champions_agent.data import database as db
        with db.get_connection() as conn:
            return db.latest_snapshot_id(conn)
    except Exception:
        return None


def top_species(conn, snapshot_id: int, n: int = BUILD_POOL_FRESHNESS_TOP_N) -> list:
    """スナップショットの使用率上位 n 種 → [(種, 順位)]"""
    rows = conn.execute("SELECT pokemon_name, usage_percent FROM pokemon_usage WHERE snapshot_id=? ORDER BY usage_percent DESC, pokemon_name "
                        "LIMIT ?", (int(snapshot_id), int(n))).fetchall()
    return [(str(r[0]), i + 1) for i, r in enumerate(rows)]


def freshness(pinned_snapshot_id: Optional[int], latest_snapshot_id: Optional[int] = None, top_n: int = BUILD_POOL_FRESHNESS_TOP_N,
              warn_below: float = BUILD_POOL_FRESHNESS_WARN, conn=None) -> dict:
    """固定したスナップショットと最新の上位 top_n 種の重なり。conn を渡せばその DB (テスト用)、無ければ使用率 DB"""
    if pinned_snapshot_id is None:
        return freshness_row(None, latest_snapshot_id, None, top_n, warn_below) | {"note": "no_pin"}
    try:
        if conn is None:
            from champions_agent.data import database as db
            with db.get_connection() as c:
                latest = latest_snapshot_id if latest_snapshot_id is not None else db.latest_snapshot_id(c)
                ov = weighted_overlap(top_species(c, pinned_snapshot_id, top_n), top_species(c, latest, top_n)) if latest is not None else None
        else:
            latest = latest_snapshot_id
            ov = weighted_overlap(top_species(conn, pinned_snapshot_id, top_n), top_species(conn, latest, top_n)) if latest is not None else None
    except Exception as e:
        return freshness_row(pinned_snapshot_id, latest_snapshot_id, None, top_n, warn_below) | {"error": repr(e)}
    return freshness_row(pinned_snapshot_id, latest, ov, top_n, warn_below)


# ------------------------------------------------------------------ 配線
def pin_for(regulation: Optional[str], run_seed: int, path: Optional[Path] = None, fixed: Optional[bool] = None,
            override_seed: Optional[int] = None, latest_snapshot_id: Optional[int] = None, now: Optional[float] = None) -> tuple:
    """run が S2 に使う固定と由来。表に無い規制なら、run の seed と最新のスナップショットと今の時刻を登録する"""
    fixed = BUILD_SEASON_PIN if fixed is None else bool(fixed)
    table = load_table(path)
    now = time.time() if now is None else now
    if fixed and _key(regulation) not in table and latest_snapshot_id is None:
        latest_snapshot_id = latest_snapshot()
    pin, src, new_table = resolve_pin(regulation, run_seed, latest_snapshot_id, now, table, fixed=fixed, override_seed=override_seed)
    if new_table != table:
        save_table(new_table, path)
    return pin, src


def repin(regulation: str, snapshot_id: Optional[int] = None, seed: Optional[int] = None, path: Optional[Path] = None,
          now: Optional[float] = None) -> dict:
    """固定し直す (新シーズンのデータが出たとき): スナップショット (省略時は最新)、seed (省略時は今の seed)、区切り = 今。前の固定は previous に残す"""
    table = load_table(path)
    key = _key(regulation)
    old = table.get(key) or {}
    snap = snapshot_id if snapshot_id is not None else latest_snapshot()
    now = time.time() if now is None else now
    entry = {"seed": int(seed if seed is not None else (old.get("seed") if old.get("seed") is not None else 0)),
             "pool_snapshot_id": snap, "roster_until": round(float(now), 2), "created_at": _stamp(now), "source": "repin",
             "previous": ({k: old.get(k) for k in PIN_KEYS + ("created_at",)} if old else None)}
    table[key] = entry
    save_table(table, path)
    return entry


def check(path: Optional[Path] = None, top_n: int = BUILD_POOL_FRESHNESS_TOP_N, warn_below: float = BUILD_POOL_FRESHNESS_WARN,
          conn=None, latest_snapshot_id: Optional[int] = None) -> dict:
    """表にある規制ごとの鮮度。{"regulations": {規制: 鮮度の行}, "any_warn": bool}"""
    table = load_table(path)
    out: dict = {}
    for key, entry in sorted(table.items()):
        out[key] = freshness(entry.get("pool_snapshot_id"), latest_snapshot_id, top_n, warn_below, conn=conn)
        out[key]["seed"] = entry.get("seed")
        out[key]["roster_until"] = entry.get("roster_until")
    return {"regulations": out, "any_warn": any(r.get("warn") for r in out.values())}


# ------------------------------------------------------------------ 分割の独立性 (純粋)
def _tier_of_team(doc: dict) -> dict:
    """team_id → [層] (層の id 列から。同じ構築が複数の層にあれば複数)"""
    from tools.team_build.families import TIERS
    out: dict = {}
    for tier in TIERS:
        for tid in (doc.get("tiers") or {}).get(tier) or []:
            out.setdefault(tid, []).append(tier)
    return out


def split_independence(doc: dict, min_jaccard: Optional[float] = None) -> dict:
    """1 run の opponent_families.json の分割の独立性 (純粋)。数えるのは件数だけ (holdout の構築の中身は出さない)。
      team_multi_tier       : 2 つ以上の層に入っている構築の数
      team_dup_in_tier      : 同じ層の id 列に重複して入っている構築の数
      family_cross_tier     : 構築が 2 つ以上の層に散っている系統の数 (families の tier と、層の id 列の両方で見る)
      family_tier_mismatch  : families の tier と、その系統の構築が入っている層が食い違う系統の数
      similar_cross_tier    : 層をまたぐ構築の組で、種族集合の Jaccard ≥ min_jaccard のもの (same_mega / other_mega に分ける)
      identical_cross_tier  : 層をまたぐ構築の組で、種族集合が完全に同じもの
      folds                 : fold の数・大きさ、fold どうしの重なり、fold の和と search 層の差、fold をまたぐ系統、fold をまたぐ似た組
      sealed_ok             : sealed_id が holdout の id 列から作り直した値と一致するか (sealed_id が無ければ None)
      ok                    : 重なり (team_multi_tier / family_cross_tier / fold の重なり・差 / fold をまたぐ系統) が全部 0
    似た組 (similar_*) は系統の定義 (同じメガ軸の単連結) の外で起きるので ok には含めず、件数を別に出す"""
    from tools.team_build.families import TIERS, jaccard, sealed_id
    # 既定は run が系統化に使った閾値 (無ければ config の BUILD_FAMILY_JACCARD)
    thr = float(min_jaccard if min_jaccard is not None else (doc.get("min_jaccard") or BUILD_FAMILY_JACCARD))
    tiers = doc.get("tiers") or {}
    tier_of = _tier_of_team(doc)
    teams = doc.get("teams") or {}
    multi = sorted(t for t, ts in tier_of.items() if len(set(ts)) > 1)
    dup_in_tier = sum(1 for t, ts in tier_of.items() if len(ts) != len(set(ts)))
    fam_cross, fam_mismatch = 0, 0
    fam_of: dict = {}
    for f in doc.get("families") or []:
        member_tiers = {x for tid in f.get("teams") or [] for x in tier_of.get(tid, [])}
        for tid in f.get("teams") or []:
            fam_of[tid] = f.get("family_id")
        if len(member_tiers) > 1:
            fam_cross += 1
        if f.get("tier") is not None and member_tiers and member_tiers != {f.get("tier")}:
            fam_mismatch += 1

    def _pairs(groups: dict) -> dict:
        """groups: 名前 → team_id 列。違う群の構築の組で、似ている (≥ thr) / 完全に同じ 種族集合 の組を数える"""
        names = list(groups)
        sim_same, sim_other, ident = 0, 0, 0
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                for ta in groups[a]:
                    sa = set((teams.get(ta) or {}).get("species") or [])
                    if not sa:
                        continue
                    ma = (teams.get(ta) or {}).get("mega")
                    for tb in groups[b]:
                        sb = set((teams.get(tb) or {}).get("species") or [])
                        if not sb:
                            continue
                        if sa == sb:
                            ident += 1
                        if jaccard(sa, sb) >= thr:
                            if ma == (teams.get(tb) or {}).get("mega"):
                                sim_same += 1
                            else:
                                sim_other += 1
        return {"same_mega": sim_same, "other_mega": sim_other, "identical": ident}

    cross = _pairs({t: list(tiers.get(t) or []) for t in TIERS})
    folds = [list(f) for f in (doc.get("search_folds") or [])]
    fold_sets = [set(f) for f in folds]
    fold_overlap = sum(len(fold_sets[i] & fold_sets[j]) for i in range(len(folds)) for j in range(i + 1, len(folds)))
    union = set().union(*fold_sets) if fold_sets else set()
    search = set(tiers.get("search") or [])
    fold_of: dict = {}
    for i, f in enumerate(folds):
        for tid in f:
            fold_of.setdefault(tid, set()).add(i)
    by_fam: dict = {}
    for tid, idx in fold_of.items():
        by_fam.setdefault(fam_of.get(tid, tid), set()).update(idx)
    fam_cross_fold = sum(1 for v in by_fam.values() if len(v) > 1)
    fold_pairs = _pairs({f"fold{i}": f for i, f in enumerate(folds)}) if folds else {"same_mega": 0, "other_mega": 0, "identical": 0}
    fold_info = {"n_folds": len(folds), "sizes": [len(f) for f in folds], "overlap": fold_overlap,
                 "missing_from_folds": len(search - union) if folds else None, "not_in_search": len(union - search),
                 "family_cross_fold": fam_cross_fold, "similar_cross_fold": fold_pairs}
    sealed_ok = None
    if doc.get("sealed_id"):
        sealed_ok = sealed_id(tiers.get("holdout") or []) == doc["sealed_id"]
    n_fams = {t: len({fam_of.get(tid, tid) for tid in tiers.get(t) or []}) for t in TIERS}
    ok = (not multi and fam_cross == 0 and fam_mismatch == 0 and fold_overlap == 0
          and (not folds or (fold_info["missing_from_folds"] == 0 and fold_info["not_in_search"] == 0)) and fam_cross_fold == 0)
    return {"run_id": doc.get("run_id"), "seed": doc.get("seed"), "pool_source": doc.get("pool_source"),
            "pool_snapshot": doc.get("pool_snapshot"), "min_jaccard": thr,
            "n_teams": {t: len(tiers.get(t) or []) for t in TIERS}, "n_families": n_fams,
            "team_multi_tier": len(multi), "team_dup_in_tier": dup_in_tier,
            "family_cross_tier": fam_cross, "family_tier_mismatch": fam_mismatch,
            "similar_cross_tier": {"same_mega": cross["same_mega"], "other_mega": cross["other_mega"]},
            "identical_cross_tier": cross["identical"], "folds": fold_info, "sealed_id": doc.get("sealed_id"),
            "sealed_ok": sealed_ok, "ok": ok}


def cross_run_consistency(docs: dict) -> dict:
    """同じ固定 (seed・プールの元・スナップショット・構築数) を使う run どうしで、同じ構築が別の層・別の fold に入っていないか (純粋)。
    docs: run_id → opponent_families.json。{"groups": [{"key", "runs", "teams_conflict_tier", "teams_conflict_fold", "sealed_ids"}], "ok"}"""
    groups: dict = {}
    for rid, doc in sorted(docs.items()):
        key = (doc.get("seed"), doc.get("pool_source"), doc.get("pool_snapshot"), doc.get("top_n"))
        groups.setdefault(key, []).append((rid, doc))
    out = []
    for key, members in groups.items():
        tier_seen: dict = {}
        fold_seen: dict = {}
        for _rid, doc in members:
            for tid, ts in _tier_of_team(doc).items():
                tier_seen.setdefault(tid, set()).update(ts)
            for i, f in enumerate(doc.get("search_folds") or []):
                for tid in f:
                    fold_seen.setdefault(tid, set()).add(i)
        out.append({"key": {"seed": key[0], "pool_source": key[1], "pool_snapshot": key[2], "top_n": key[3]},
                    "runs": [rid for rid, _ in members],
                    "teams_conflict_tier": sum(1 for v in tier_seen.values() if len(v) > 1),
                    "teams_conflict_fold": sum(1 for v in fold_seen.values() if len(v) > 1),
                    "sealed_ids": sorted({str(doc.get("sealed_id")) for _rid, doc in members})})
    return {"groups": out, "ok": all(g["teams_conflict_tier"] == 0 and g["teams_conflict_fold"] == 0 and len(g["sealed_ids"]) == 1
                                     for g in out)}


def select_pinned_runs(manifests: dict, table: dict) -> dict:
    """run_id → manifest から、固定 (season_pins.json の規制の seed) を使った run を選ぶ (純粋)。
    条件: manifest の season_pin の由来が fixed で始まり、seed が表の規制の seed と同じ。→ {run_id: 規制}"""
    out = {}
    for rid, man in manifests.items():
        sp = (man or {}).get("season_pin") or {}
        src = str(sp.get("source") or (man or {}).get("split_seed_source") or "")
        if not src.startswith("fixed"):
            continue
        reg = _key((man or {}).get("regulation"))
        entry = table.get(reg) or {}
        if entry.get("seed") is not None and sp.get("seed") is not None and int(sp["seed"]) == int(entry["seed"]):
            out[rid] = reg
    return out


def check_splits(runs_dir: Optional[Path] = None, pins_path: Optional[Path] = None, min_jaccard: Optional[float] = None) -> dict:
    """固定を使った run の分割を検査する (読み取りのみ)。{"pins", "runs": {run_id: split_independence}, "cross_run", "ok"}"""
    runs_dir = Path(runs_dir) if runs_dir is not None else RUNS_DIR
    table = load_table(pins_path)
    manifests, docs = {}, {}
    for d in sorted(runs_dir.glob("*")):
        man_p, split_p = d / "manifest.json", d / "opponent_families.json"
        if not (man_p.exists() and split_p.exists()):
            continue
        try:
            manifests[d.name] = json.loads(man_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    picked = select_pinned_runs(manifests, table)
    for rid in sorted(picked):
        try:
            docs[rid] = json.loads((runs_dir / rid / "opponent_families.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    per_run = {rid: dict(split_independence(doc, min_jaccard), regulation=picked[rid]) for rid, doc in docs.items()}
    cross = cross_run_consistency(docs)
    return {"pins": table, "runs": per_run, "cross_run": cross,
            "ok": bool(per_run) and all(r["ok"] for r in per_run.values()) and cross["ok"]}


def format_check_splits(res: dict) -> str:
    lines = [f"分割の独立性: 固定を使った run {len(res['runs'])} 本 → {'重なり 0' if res['ok'] else '要確認'}"]
    for rid, r in res["runs"].items():
        f = r["folds"]
        lines.append(f"  [{rid}] 規制 {r['regulation']} seed {r['seed']} プール {r['pool_source']}:{r['pool_snapshot']} "
                     f"構築 {r['n_teams']} 系統 {r['n_families']}")
        lines.append(f"    層をまたぐ: 構築 {r['team_multi_tier']} / 系統 {r['family_cross_tier']} (tier 記録の食い違い {r['family_tier_mismatch']}) / "
                     f"似た組 (Jaccard ≥ {r['min_jaccard']}) 同じメガ軸 {r['similar_cross_tier']['same_mega']} 別のメガ軸 "
                     f"{r['similar_cross_tier']['other_mega']} / 種族が完全に同じ組 {r['identical_cross_tier']} / 封印 id の一致 {r['sealed_ok']}")
        lines.append(f"    fold {f['n_folds']} ({f['sizes']}): 重なり {f['overlap']} / search との差 (fold に無い {f['missing_from_folds']}・"
                     f"search 外 {f['not_in_search']}) / fold をまたぐ系統 {f['family_cross_fold']} / fold をまたぐ似た組 "
                     f"同じメガ軸 {f['similar_cross_fold']['same_mega']} 別のメガ軸 {f['similar_cross_fold']['other_mega']}")
    for g in res["cross_run"]["groups"]:
        lines.append(f"  同じ固定の run {g['runs']}: 別の層に入った構築 {g['teams_conflict_tier']} / 別の fold {g['teams_conflict_fold']} / "
                     f"封印 id {g['sealed_ids']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="季節 (規制) ごとの固定: 分割の seed・使用率スナップショット・実在の構築の区切り")
    ap.add_argument("--show", action="store_true", help="表を表示する")
    ap.add_argument("--check", action="store_true", help="固定したスナップショットと最新の上位種の重なり (鮮度) を見る")
    ap.add_argument("--repin", default=None, metavar="REGULATION", help="この規制を固定し直す (新シーズンのデータが出たとき)")
    ap.add_argument("--snapshot", type=int, default=None, help="--repin で使うスナップショット id (省略時は最新)")
    ap.add_argument("--seed", type=int, default=None, help="--repin で使う分割の seed (省略時は今の seed)")
    ap.add_argument("--check-splits", action="store_true",
                    help="固定を使った run の分割 (opponent_families.json) の独立性を数える (読み取りのみ)")
    ap.add_argument("--runs-dir", default=None, help="--check-splits で読む run の置き場 (既定 logs/build_search/runs)")
    ap.add_argument("--pins", default=None, help="--check-splits で読む固定の表 (既定 logs/registry/season_pins.json)")
    ap.add_argument("--json", action="store_true", help="--check-splits の結果を JSON で出す")
    args = ap.parse_args()
    if args.check_splits:
        res = check_splits(Path(args.runs_dir) if args.runs_dir else None, Path(args.pins) if args.pins else None)
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else format_check_splits(res))
        return
    if args.repin:
        entry = repin(args.repin, args.snapshot, args.seed)
        print(json.dumps({args.repin: entry}, ensure_ascii=False, indent=1))
    elif args.check:
        res = check()
        print(json.dumps(res, ensure_ascii=False, indent=1))
        for reg, f in res["regulations"].items():
            if f.get("warn"):
                print(f"警告: {reg} の固定 {f.get('pinned')} と最新 {f.get('latest')} の上位 {f.get('top_n')} 種の重なり {f.get('overlap')} "
                      f"< {f.get('warn_below')}。固定し直すなら --repin {reg}")
    else:
        print(json.dumps(load_table(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
