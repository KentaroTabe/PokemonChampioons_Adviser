"""負け戦の自動分類プローブ (操縦の弱点分析)。

read_burden と同じ choose_move ラップ方式で、対戦ごとに
- 相手アクティブの最大ランク上昇 (積まれ度) とその種族
- 終了時の残数差 / ターン数 / 相手チーム構成
を記録し、負けを型分類する:

    setup_loss   : 相手に+2以上積まれて負けた (全抜き型)
    close_loss   : 相手残り1体まで追い詰めての負け (詰め損ね)
    outmatched   : 積まれず残り2体以上を残されての負け (対面押し負け)

分類は介入の選択に使う (docs/HEURISTICS_CATALOG.md H5 / 観測拡張 / 終盤)。
"""
from __future__ import annotations


def attach_loss_probe(player) -> None:
    """choose_move をラップして対戦ごとの統計を集める (行動は変えない)"""
    player.loss_probe = {}   # battle_tag -> {"max_boost": int, "boost_sp": str}
    original = player.choose_move

    def wrapped(battle):
        try:
            rec = player.loss_probe.setdefault(
                battle.battle_tag, {"max_boost": 0, "boost_sp": None})
            opp = battle.opponent_active_pokemon
            if opp is not None:
                stage = max([v for v in (opp.boosts or {}).values()] or [0])
                if stage > rec["max_boost"]:
                    rec["max_boost"] = stage
                    rec["boost_sp"] = opp.species
        except Exception:
            pass
        return original(battle)

    player.choose_move = wrapped


def battle_rows(player) -> list:
    """対戦ごとの記録を行データに集計する"""
    rows = []
    for tag, b in player.battles.items():
        if b.won is None:
            continue
        probe = (getattr(player, "loss_probe", {}) or {}).get(tag, {})
        my_fainted = sum(1 for p in b.team.values()
                         if p.fainted)
        opp_fainted = sum(1 for p in b.opponent_team.values() if p.fainted)
        rows.append({
            "won": bool(b.won),
            "turns": getattr(b, "turn", None),
            "opp_team": sorted(p.species for p in b.opponent_team.values()
                               if p.species),
            "my_remaining": max(0, 3 - my_fainted),
            "opp_remaining": max(0, 3 - opp_fainted),
            "max_opp_boost": probe.get("max_boost", 0),
            "boost_species": probe.get("boost_sp"),
        })
    return rows


def classify_loss(row: dict) -> str | None:
    """負け1件の型分類 (勝ちなら None)"""
    if row["won"]:
        return None
    if row["max_opp_boost"] >= 2:
        return "setup_loss"
    if row["opp_remaining"] <= 1:
        return "close_loss"
    return "outmatched"


def summarize(rows: list) -> dict:
    """分類集計と苦手構築ランキング"""
    losses = [r for r in rows if not r["won"]]
    cats: dict = {}
    sweepers: dict = {}
    for r in losses:
        c = classify_loss(r)
        cats[c] = cats.get(c, 0) + 1
        if c == "setup_loss" and r.get("boost_species"):
            sp = r["boost_species"]
            sweepers[sp] = sweepers.get(sp, 0) + 1

    by_team: dict = {}
    for r in rows:
        key = "|".join(sorted(r["opp_team"]))
        st = by_team.setdefault(key, [0, 0])   # [wins, losses]
        st[0 if r["won"] else 1] += 1
    worst_teams = sorted(
        ((k, w, l) for k, (w, l) in by_team.items() if w + l >= 5),
        key=lambda x: (x[1] / (x[1] + x[2]), -(x[1] + x[2])))[:10]

    by_species: dict = {}
    for r in rows:
        for sp in r["opp_team"]:
            st = by_species.setdefault(sp, [0, 0])
            st[0 if r["won"] else 1] += 1
    worst_species = sorted(
        ((k, w, l) for k, (w, l) in by_species.items() if w + l >= 20),
        key=lambda x: x[1] / (x[1] + x[2]))[:12]

    return {"n": len(rows), "losses": len(losses), "categories": cats,
            "sweepers": sorted(sweepers.items(), key=lambda x: -x[1])[:10],
            "worst_teams": worst_teams, "worst_species": worst_species,
            "close_margin": sum(1 for r in losses
                                if r["opp_remaining"] == 1)}
