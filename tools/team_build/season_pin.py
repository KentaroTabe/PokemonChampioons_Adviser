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
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_POOL_FRESHNESS_TOP_N, BUILD_POOL_FRESHNESS_WARN, BUILD_SEASON_PIN

REPO = Path(__file__).resolve().parent.parent.parent
PINS_PATH = REPO / "logs" / "registry" / "season_pins.json"
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


def main() -> None:
    ap = argparse.ArgumentParser(description="季節 (規制) ごとの固定: 分割の seed・使用率スナップショット・実在の構築の区切り")
    ap.add_argument("--show", action="store_true", help="表を表示する")
    ap.add_argument("--check", action="store_true", help="固定したスナップショットと最新の上位種の重なり (鮮度) を見る")
    ap.add_argument("--repin", default=None, metavar="REGULATION", help="この規制を固定し直す (新シーズンのデータが出たとき)")
    ap.add_argument("--snapshot", type=int, default=None, help="--repin で使うスナップショット id (省略時は最新)")
    ap.add_argument("--seed", type=int, default=None, help="--repin で使う分割の seed (省略時は今の seed)")
    args = ap.parse_args()
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
