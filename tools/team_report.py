"""パーティ構築診断レポート (Phase 6.1/6.2/6.3)。

config/my_team.json の構築を使用率メタと突き合わせて診断する:
 1. マッチアップ診断: メタ上位N体との1v1行列 -> 各メンバーの勝ち数と
    「誰も勝てない脅威」(構築の穴) の列挙
 2. 素早さ関係: 実数Sとメタ上位の実数Sの上下関係、僅差の抜き調整候補
 3. 耐久チェック: メタ上位の最大打点に対する被ダメージ
 4. 補完提案 (--suggest): teammate_usage共起 + 穴への解答で残り枠候補

使い方:
    python -m tools.team_report            # 診断のみ
    python -m tools.team_report --suggest  # 補完提案つき
    python -m tools.team_report --top 30   # メタ上位数の変更
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from advisor.damage import MonView
from advisor.dex import get_dex, calc_stat
from advisor.endgame import duel, _best_dmg
from advisor.ev_infer import SpreadEstimator, _nature_mult
from advisor.infer import species_ja_name
from advisor.my_team import _load as load_my_team, get_my_build
from advisor.team_advice import meta_top, build_meta_view
from advisor.sets import get_predictor
from vision.normalize import NameResolver

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_my_views(resolver) -> list:
    """my_team.json -> [(ja, MonView, moves)]"""
    team = load_my_team()
    out = []
    for ja in team:
        r = resolver.resolve_species(ja, cutoff=0.9)
        if not r:
            print(f"  ! {ja}: 種族を解決できません")
            continue
        sid = r[1]
        sp = get_dex().species(sid)
        b = get_my_build(ja)
        view = MonView(species_id=sid, name_ja=ja, types=sp["types"],
                       base=sp["baseStats"], ev=b["ev"],
                       nature=b["nature"],
                       item=None)
        entry = load_my_team().get(ja) or {}
        moves_ja = entry.get("技") or entry.get("moves") or []
        moves = []
        for mj in moves_ja:
            rm = resolver.resolve(mj, "moves", cutoff=0.8)
            if rm:
                moves.append(rm[1])
        if not moves:
            moves = [m for m, _ in get_predictor().predict(sid)["moves"][:4]]
        out.append((ja, view, moves))
    return out


def report(top_n: int = 20, suggest: bool = False):
    resolver = NameResolver()
    mine = build_my_views(resolver)
    if not mine:
        print("config/my_team.json が空です")
        return
    meta = meta_top(top_n)
    meta_views = []
    for sid, usage in meta:
        v, moves = build_meta_view(sid)
        if v is not None:
            meta_views.append((sid, usage, v, moves))

    # --- 1. マッチアップ診断 ---
    print(f"# 構築診断 ({len(mine)}体 vs メタ上位{len(meta_views)}体)\n")
    print("## 1. マッチアップ (○=1v1で勝ち見込み)")
    uncovered = []
    win_counts = {}
    header = "                | " + " | ".join(
        f"{v.name_ja[:5]:<5}" for _, _, v, _ in meta_views[:10])
    for ja, mv, mmoves in mine:
        marks = []
        wins = 0
        for sid, usage, ov, omoves in meta_views:
            r = duel(mv, 1.0, mmoves, ov, 1.0, omoves)
            marks.append("○" if r else ("×" if r is False else "?"))
            wins += 1 if r else 0
        win_counts[ja] = wins
        print(f"  {ja:<8} 勝ち{wins:>2}/{len(meta_views)}  {''.join(marks)}")
    for sid, usage, ov, omoves in meta_views:
        beaten = any(duel(mv, 1.0, mmoves, ov, 1.0, omoves)
                     for _, mv, mmoves in mine)
        if not beaten:
            uncovered.append((ov.name_ja, usage))
    if uncovered:
        print("\n  ⚠ 構築の穴 (1v1で誰も勝てない):")
        for name, usage in uncovered:
            print(f"    - {name} (使用率{usage:.1f})")
    else:
        print("\n  ✅ メタ上位すべてに1v1で勝てる駒がいます")

    # --- 2. 素早さ関係 ---
    print("\n## 2. 素早さ関係 (実数)")
    rows = []
    for ja, mv, _ in mine:
        rows.append((mv.stat("spe"), f"[自] {ja}"))
    for sid, usage, ov, _ in meta_views:
        rows.append((ov.stat("spe"), f"    {ov.name_ja} (推定型)"))
    for spe, label in sorted(rows, reverse=True):
        print(f"  S{spe:>3} {label}")
    print("\n  抜き調整候補 (あと2ポイント=努力値16以内で抜ける相手):")
    found_adj = False
    for ja, mv, _ in mine:
        base = mv.base.get("spe", 80)
        my_ev = mv.ev.get("spe", 0)
        nat = mv.nature.get("spe", 1.0)
        for sid, usage, ov, _ in meta_views:
            target = ov.stat("spe")
            if mv.stat("spe") <= target:
                for extra in (8, 16):
                    if calc_stat(base, min(252, my_ev + extra), nat) > target:
                        print(f"    - {ja}: +{extra // 8}ポイントで"
                              f"{ov.name_ja} (S{target}) 抜き")
                        found_adj = True
                        break
    if not found_adj:
        print("    (該当なし)")

    # --- 3. 耐久チェック ---
    print("\n## 3. 被ダメージチェック (メタ上位の最大打点)")
    for ja, mv, _ in mine:
        worst = []
        for sid, usage, ov, omoves in meta_views[:10]:
            d = _best_dmg(ov, mv, omoves)
            if d >= 85:
                worst.append(f"{ov.name_ja}({d:.0f}%)")
        if worst:
            print(f"  {ja}: 一撃圏 {', '.join(worst[:4])}")

    # --- 4. 補完提案 ---
    if suggest:
        print("\n## 4. 補完候補 (共起率 + 穴への解答)")
        db = sqlite3.connect(str(DB_PATH))
        my_names = set()
        cooc = {}
        for ja, mv, _ in mine:
            my_names.add(mv.species_id)
            for name, w in db.execute(
                    "SELECT teammate_name, SUM(usage_percent) FROM teammate_usage "
                    "WHERE REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ? "
                    "GROUP BY teammate_name", (mv.species_id,)):
                cooc[_to_id(name)] = cooc.get(_to_id(name), 0) + (w or 0)
        db.close()
        cands = []
        for sid, w in sorted(cooc.items(), key=lambda kv: -kv[1])[:25]:
            if sid in my_names or not get_dex().species(sid):
                continue
            cv, cmoves = build_meta_view(sid)
            if cv is None:
                continue
            hole_cover = sum(
                1 for hn, _u in uncovered
                for msid, _uu, ov, omoves in meta_views
                if ov.name_ja == hn and duel(cv, 1.0, cmoves, ov, 1.0, omoves))
            cands.append((w + hole_cover * 5000, sid, hole_cover))
        for score, sid, hole in sorted(cands, reverse=True)[:8]:
            note = f" (穴{hole}体に解答)" if hole else ""
            print(f"  - {species_ja_name(sid)}{note}")


if __name__ == "__main__":
    args = sys.argv[1:]
    top_n = 20
    if "--top" in args:
        top_n = int(args[args.index("--top") + 1])
    report(top_n=top_n, suggest="--suggest" in args)
