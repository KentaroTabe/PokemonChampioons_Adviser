"""既存の正式比較の入力の監査 (読み取りのみ。2026-10-23、レビュー 2026-10-19 §2)。

    python -m tools.inputs_audit [--root <repo>] [--db <使用率 DB>] [--bank <バンク>] [--json <出力>]

logs/usefulness/p1_* (予備測定 p1_prelim_* を除く) の条件表 conditions.json ごとに:
  - 選出モデル: 当時の sha16 (条件表の selection_model.sha16) と今の配備先 (同じパス) の sha16 が一致するか。埋め込み・meta は
    当時のピン (logs/pinned_models/<時刻>/) に残っていて記録と一致するか、今の配備先と一致するか
  - 使用率: 当時の snapshot id (usage_db.latest_snapshot_id。型予測は DB の最新を読んでいた) が今の DB に残っているか、
    bundle 無し (今の経路 = DB の最新) で同じ予測になるか (その snapshot と今の最新で、その snapshot の全種の型予測を比べる)。
    id が残っていても、当時の内容と同じかは内容の sha の記録が無く確かめられない (TypeInference の参照範囲 = id 以下の行も同じ)
  - 実戦バンク: 当時の sha16 と今の bank.json の sha16 が一致するか (当時の写しは無い)
ログ・条件表・DB は書き換えない (DB は読み取り専用で開く)。純粋な部分 (audit_row / summarize) は tests/test_inputs_pin.py。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ 純粋
def is_formal(name: str) -> bool:
    """正式比較の出力 dir か (予備測定 p1_prelim_* を除く)"""
    return name.startswith("p1_") and not name.startswith("p1_prelim_")


def audit_row(name: str, cond: dict, now: dict) -> dict:
    """1 件の条件表の監査 (純粋)。now = 今の値 {"deployed_sha16": {パス: sha16 or None}, "deployed_emb_sha16", "deployed_meta_sha16",
    "pin_files": {パス: sha16 or None}, "snapshots": [id], "latest_snapshot", "bank_sha16", "pred_diff": {snapshot id: 予測の差の要約}}"""
    sel = cond.get("selection_model") or {}
    emb = sel.get("embedding") or {}
    meta = sel.get("meta") or {}
    path = sel.get("path")
    dep = (now.get("deployed_sha16") or {}).get(path)
    pin_emb = emb.get("pin_file")
    pin_meta = meta.get("pin_file")
    pin_files = now.get("pin_files") or {}
    snap = (cond.get("usage_db") or {}).get("latest_snapshot_id")
    snaps = set(now.get("snapshots") or [])
    bank = (cond.get("real_bank") or {}).get("sha16")
    pd = (now.get("pred_diff") or {}).get(snap)
    ip = cond.get("inputs_pin")
    return {
        "run": name, "started_at": cond.get("started_at"), "factor": cond.get("factor") or "rl",
        "inputs_pin": isinstance(ip, dict) and not ip.get("planned"),
        "selection_model": {"path": path, "recorded_sha16": sel.get("sha16"), "deployed_sha16_now": dep,
                            "match_now": (dep == sel.get("sha16")) if dep and sel.get("sha16") else None,
                            "pt_in_pin": bool(isinstance(ip, dict) and (ip.get("selection_model") or {}).get("sha256"))},
        "embedding": {"pin_file": pin_emb, "recorded_sha16": emb.get("pin_sha16") or emb.get("used_sha16"),
                      "pin_sha16_now": pin_files.get(pin_emb),
                      "pin_match": (pin_files.get(pin_emb) == emb.get("pin_sha16")) if pin_files.get(pin_emb) and emb.get("pin_sha16")
                      else None,
                      "deployed_sha16_now": now.get("deployed_emb_sha16"),
                      "deployed_match": (now.get("deployed_emb_sha16") == (emb.get("pin_sha16") or emb.get("used_sha16")))
                      if now.get("deployed_emb_sha16") else None},
        "meta": {"pin_file": pin_meta, "recorded_sha16": meta.get("sha16"), "pin_sha16_now": pin_files.get(pin_meta),
                 "pin_match": (pin_files.get(pin_meta) == meta.get("sha16")) if pin_files.get(pin_meta) and meta.get("sha16") else None,
                 "deployed_match": (now.get("deployed_meta_sha16") == meta.get("sha16")) if now.get("deployed_meta_sha16") else None},
        "usage": {"recorded_snapshot": snap, "in_db_now": (snap in snaps) if snap is not None else None,
                  "latest_now": now.get("latest_snapshot"),
                  "same_as_latest": (snap == now.get("latest_snapshot")) if snap is not None else None,
                  "pred_vs_latest": pd,
                  "reproducible_without_bundle": (False if (pd and pd.get("n_diff")) else (True if pd is not None else None)),
                  "content_verifiable": False},
        "real_bank": {"recorded_sha16": bank, "now_sha16": now.get("bank_sha16"),
                      "match_now": (bank == now.get("bank_sha16")) if bank and now.get("bank_sha16") else None},
    }


def pred_diff_summary(preds_a: dict, preds_b: dict) -> dict:
    """2 つの snapshot の型予測 {種: predict の戻り値} の差 (純粋)。n = 比べた種、n_diff = 分布が違う種、
    n_top_move_diff / n_top_item_diff = 最頻の技・持ち物が違う種"""
    keys = sorted(set(preds_a) & set(preds_b))

    def top(p, k):
        xs = (p or {}).get(k) or []
        return xs[0][0] if xs else None
    n_diff = sum(1 for s in keys if preds_a[s] != preds_b[s])
    return {"n": len(keys), "n_diff": n_diff,
            "n_top_move_diff": sum(1 for s in keys if top(preds_a[s], "moves") != top(preds_b[s], "moves")),
            "n_top_item_diff": sum(1 for s in keys if top(preds_a[s], "items") != top(preds_b[s], "items"))}


def summarize(rows: list) -> dict:
    """全件の要約 (純粋)"""
    def cnt(f):
        return sum(1 for r in rows if f(r))
    return {"n": len(rows),
            "selection_match_now": cnt(lambda r: r["selection_model"]["match_now"] is True),
            "selection_pt_in_pin": cnt(lambda r: r["selection_model"]["pt_in_pin"]),
            "embedding_pin_match": cnt(lambda r: r["embedding"]["pin_match"] is True),
            "meta_pin_match": cnt(lambda r: r["meta"]["pin_match"] is True),
            "snapshot_in_db": cnt(lambda r: r["usage"]["in_db_now"] is True),
            "reproducible_without_bundle": cnt(lambda r: r["usage"]["reproducible_without_bundle"] is True),
            "bank_match_now": cnt(lambda r: r["real_bank"]["match_now"] is True)}


def render(rows: list, summ: dict, now: dict) -> str:
    L = [f"# 正式比較の入力の監査 ({summ['n']} 件、読み取りのみ)", "",
         f"今: 使用率 DB の最新 snapshot {now.get('latest_snapshot')} / バンク {now.get('bank_sha16')}", "",
         "| run | 選出モデル 当時 → 今の配備先 | .pt のピン | 埋め込み (ピン / 配備先) | meta (ピン / 配備先) | 使用率 snapshot "
         "(DB に残る / 最新と同じ予測) | バンク 当時 → 今 |", "|---|---|---|---|---|---|---|"]

    def yn(x):
        return "-" if x is None else ("一致" if x else "不一致")
    for r in rows:
        s, e, m, u, b = r["selection_model"], r["embedding"], r["meta"], r["usage"], r["real_bank"]
        pd = u.get("pred_vs_latest") or {}
        repro = ("-" if u["reproducible_without_bundle"] is None else
                 ("同じ" if u["reproducible_without_bundle"] else f"違う ({pd.get('n_diff')}/{pd.get('n')} 種、最頻の技 "
                                                                    f"{pd.get('n_top_move_diff')}・持ち物 {pd.get('n_top_item_diff')})"))
        L.append(f"| {r['run']} | {s['recorded_sha16']} → {s['deployed_sha16_now']} ({yn(s['match_now'])}) | "
                 f"{'あり' if s['pt_in_pin'] else 'なし'} | {yn(e['pin_match'])} / {yn(e['deployed_match'])} | "
                 f"{yn(m['pin_match'])} / {yn(m['deployed_match'])} | {u['recorded_snapshot']} ({'残る' if u['in_db_now'] else '無い'} / "
                 f"{repro}) | {b['recorded_sha16']} → {b['now_sha16']} ({yn(b['match_now'])}) |")
    L += ["", f"要約: 選出モデルの sha が今の配備先と一致 {summ['selection_match_now']}/{summ['n']}、.pt がピンにある "
              f"{summ['selection_pt_in_pin']}/{summ['n']}、当時の snapshot が DB に残る {summ['snapshot_in_db']}/{summ['n']}、"
              f"bundle 無し (最新を読む経路) で同じ予測 {summ['reproducible_without_bundle']}/{summ['n']}、バンクが今と一致 "
              f"{summ['bank_match_now']}/{summ['n']}",
          "", "注: snapshot が DB に残っていても、当時の内容と同じかは内容の sha の記録が無く確かめられない "
              "(TypeInference の参照範囲 = その id 以下の行も同じ)。当時のバンクの写しは無い。"]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ 読み取り (副作用)
def _sha16(path) -> Optional[str]:
    from advisor.versions import sha256_file
    return sha256_file(path) if path else None


def _predictions(db: Path, snapshot_id: int) -> dict:
    """その snapshot の全種の型予測 (読み取り専用)"""
    from advisor.sets import SetPredictor, _slug_to_id
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [n for (n,) in conn.execute("SELECT DISTINCT pokemon_name FROM move_usage WHERE snapshot_id=?", (snapshot_id,))]
    finally:
        conn.close()
    p = SetPredictor(db_path=db, snapshot_id=snapshot_id)
    return {_slug_to_id(n): p.predict(_slug_to_id(n)) for n in names}


def collect(root: Path, db: Path, bank: Path) -> tuple:
    from tools.inputs_pin import snapshot_ids
    dirs = sorted(d for d in (root / "logs" / "usefulness").glob("p1_*") if d.is_dir() and is_formal(d.name))
    conds = {}
    for d in dirs:
        try:
            conds[d.name] = json.loads((d / "conditions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    snaps = snapshot_ids(db)
    latest = max(snaps) if snaps else None
    paths = {(c.get("selection_model") or {}).get("path") for c in conds.values()}
    pin_files = set()
    for c in conds.values():
        sel = c.get("selection_model") or {}
        pin_files |= {(sel.get("embedding") or {}).get("pin_file"), (sel.get("meta") or {}).get("pin_file")}
    ckpt = root / "champions_agent" / "train" / "checkpoints"
    now = {"deployed_sha16": {p: _sha16(p) for p in paths if p},
           "deployed_emb_sha16": _sha16(ckpt / "selection_model_emb.json"),
           "deployed_meta_sha16": _sha16(ckpt / "selection_model_meta.json"),
           "pin_files": {p: _sha16(p) for p in pin_files if p}, "snapshots": snaps, "latest_snapshot": latest,
           "bank_sha16": _sha16(bank), "pred_diff": {}}
    if latest is not None:
        latest_preds = _predictions(db, latest)
        for s in sorted({(c.get("usage_db") or {}).get("latest_snapshot_id") for c in conds.values()} - {None}):
            if s in snaps:
                now["pred_diff"][s] = pred_diff_summary(_predictions(db, s), latest_preds)
    rows = [audit_row(n, c, now) for n, c in conds.items()]
    return rows, now


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="既存の正式比較の入力の監査 (読み取りのみ)")
    ap.add_argument("--root", default=str(REPO), help="logs/usefulness と配備先 (champions_agent/train/checkpoints) のある repo")
    ap.add_argument("--db", default=None, help="使用率 DB (既定 <root>/champions_agent/data/db/champions.sqlite3、読み取り専用で開く)")
    ap.add_argument("--bank", default=None, help="実戦バンク (既定 <root>/logs/real_opponents/bank.json)")
    ap.add_argument("--json", default=None, help="結果を JSON でも書く")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    db = Path(args.db) if args.db else root / "champions_agent" / "data" / "db" / "champions.sqlite3"
    from champions_agent.config import REAL_BANK_PATH
    bank = Path(args.bank) if args.bank else root / REAL_BANK_PATH
    rows, now = collect(root, db, bank)
    if not rows:
        print(f"[inputs_audit] {root / 'logs' / 'usefulness'} に正式比較の条件表が無い", file=sys.stderr)
        return 1
    summ = summarize(rows)
    print(render(rows, summ, now))
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "summary": summ, "now": now}, ensure_ascii=False, indent=1,
                                              default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
