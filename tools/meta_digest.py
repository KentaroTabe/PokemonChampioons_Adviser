"""環境ダイジェスト: 「今の環境」を1画面で把握するレポート。

  - 使用率上位 (上位帯の公式データ) とトレンド (2ヶ月分以上あれば増減)
  - よく組まれる並び (teammate_usage の上位ペア)
  - ローカルメタ (自分の対戦ログで実際に当たった相手と成績) との比較

    python -m tools.meta_digest
    python -m tools.meta_digest --top 15 --days 14
"""
from __future__ import annotations

import argparse
import re

from champions_agent.config import USAGE_TARGET_FORMAT
from champions_agent.data import database as db


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _ja(name: str) -> str:
    from advisor.infer import species_ja_name
    return species_ja_name(_to_id(name)) or name


def _month_snapshots(conn) -> dict:
    """source_month -> snapshot_id (同月の再取得は最新)"""
    rows = conn.execute(
        """SELECT id, source_month FROM usage_snapshot
           WHERE format = ? ORDER BY fetched_at""",
        (USAGE_TARGET_FORMAT,)).fetchall()
    out = {}
    for r in rows:
        out[r["source_month"]] = r["id"]
    return out


def digest(top: int, days: float | None) -> str:
    lines = []
    with db.get_connection() as conn:
        months = _month_snapshots(conn)
        if not months:
            return "使用率DBが空です (bash champions_agent/scripts/update_usage_db.sh)"
        cur_month = list(months)[-1]
        cur = months[cur_month]
        usage = {r["pokemon_name"]: r["usage_percent"] for r in conn.execute(
            """SELECT pokemon_name, usage_percent FROM pokemon_usage
               WHERE snapshot_id = ? ORDER BY usage_percent DESC""", (cur,))}
        prev_usage = {}
        if len(months) >= 2:
            prev = months[list(months)[-2]]
            prev_usage = {r["pokemon_name"]: r["usage_percent"]
                          for r in conn.execute(
                          """SELECT pokemon_name, usage_percent
                             FROM pokemon_usage WHERE snapshot_id = ?""",
                          (prev,))}
        # 並びは「使用率上位の種族の相方」に限定する (低頻度種族同士の
        # 100%ペアが上に来るのを防ぐ)
        top_names = list(usage)[:15]
        ph = ",".join("?" for _ in top_names)
        pairs = conn.execute(
            f"""SELECT pokemon_name, teammate_name, usage_percent
                FROM teammate_usage WHERE snapshot_id = ?
                AND pokemon_name IN ({ph})
                ORDER BY usage_percent DESC LIMIT 60""",
            (cur, *top_names)).fetchall()

    lines.append(f"🌐 環境ダイジェスト (使用率データ: {cur_month})")
    lines.append(f"\n■ 使用率上位{top} (上位帯)"
                 + ("" if prev_usage else " ※履歴1ヶ月分のため増減は次回から"))
    for name, pct in list(usage.items())[:top]:
        trend = ""
        if prev_usage:
            d = pct - prev_usage.get(name, 0.0)
            trend = f"  ({'+' if d >= 0 else ''}{d:.1f}pt)"
        lines.append(f"  {_ja(name)}: {pct:.1f}%{trend}")
    if prev_usage:
        risers = sorted(((n, p - prev_usage.get(n, 0.0))
                         for n, p in usage.items()),
                        key=lambda x: -x[1])[:5]
        lines.append("\n■ 上昇トレンド (前月比)")
        for n, d in risers:
            if d > 0:
                lines.append(f"  {_ja(n)}: +{d:.1f}pt")

    seen = set()
    pair_lines = []
    for r in pairs:
        key = tuple(sorted([_to_id(r["pokemon_name"]),
                            _to_id(r["teammate_name"])]))
        if key in seen or key[0] == key[1]:
            continue
        seen.add(key)
        pair_lines.append(f"  {_ja(r['pokemon_name'])} + "
                          f"{_ja(r['teammate_name'])} ({r['usage_percent']:.0f}%)")
        if len(pair_lines) >= 8:
            break
    if pair_lines:
        lines.append("\n■ よく組まれる並び")
        lines += pair_lines

    # ローカルメタ (自分の対戦ログ) との比較
    try:
        from tools.analyze_battles import load_battles, summarize
        battles = load_battles(days=days)
        if battles:
            s = summarize(battles)
            major_ids = {_to_id(n) for n, p in usage.items() if p >= 5.0}
            lines.append(f"\n■ 自分の環境 (直近{len(battles)}戦の実測)")
            for sp, n in s["encounters"].most_common(10):
                w, l = s["opp_stats"].get(sp, (0, 0))
                rec = f" {w}勝{l}敗" if (w + l) else ""
                mark = "" if _to_id_from_ja(sp, major_ids) else " ★上位帯では少数派"
                lines.append(f"  {sp}: {n}戦{rec}{mark}")
            lines.append("  ★= 上位帯で使用率5%未満の相手。自分のレート帯"
                         "固有のメタなので個別対策の価値が高い")
    except Exception as e:
        lines.append(f"\n(対戦ログ比較は利用不可: {e})")
    return "\n".join(lines)


def _to_id_from_ja(ja_name: str, top_ids: set) -> bool:
    """日本語種族名が使用率上位のid集合に含まれるか"""
    try:
        from vision.normalize import NameResolver
        global _resolver
        if "_resolver" not in globals() or _resolver is None:
            _resolver = NameResolver()
        r = _resolver.resolve_species(ja_name, cutoff=0.85)
        return bool(r and _to_id(r[1]) in top_ids)
    except Exception:
        return True   # 判定不能時は★を付けない


_resolver = None


def main() -> None:
    ap = argparse.ArgumentParser(description="環境ダイジェスト")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--days", type=float, default=None,
                    help="ローカルメタ集計の対象日数 (省略時は全ログ)")
    args = ap.parse_args()
    print(digest(args.top, args.days))


if __name__ == "__main__":
    main()
