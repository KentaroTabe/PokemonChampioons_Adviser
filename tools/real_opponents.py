"""実戦の相手バンク: 対戦ログ (logs/battles) から相手パーティの「実際の選出・先発・判明した型」を集める。

自己対戦の相手は使用率メタの代表型 + 相性ヒューリスティクスの選出で、実戦の相手 (このレート帯で当たる構成、
人が実際に選ぶ 3 体と先発) とはずれる (机上の分布)。バンクは
  - 学習環境の相手プール (champions_agent/env/real_opponents.py: 実戦の構成を混ぜ、観測した選出分布で選ばせる)
  - 選出助言の条件づけ (advisor/real_prior.py: 相手スロットを実戦の選出率で重みづけ)
に使う。学習には使わない (評価集合の衛生)。

    python -m tools.real_opponents --build [--days N | --last N]   # logs/real_opponents/bank.json
    python -m tools.real_opponents --show
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from champions_agent.config import REAL_BANK_MIN_ROSTER, REAL_BANK_PATH, USAGE_TARGET_FORMAT

REPO = Path(__file__).resolve().parent.parent
BANK_VERSION = 1


def roster_key(ids) -> str:
    return "|".join(sorted(ids))


def base_species(sid: str) -> tuple:
    """メガ形態の id (…mega / …megax / …megay) を基本種に丸め、メガかどうかを返す。
    画面認識は「メガミミロップ」を別枠で拾うため、そのままだとロースターが 7 体以上になる"""
    for suf in ("megax", "megay", "mega"):
        if sid.endswith(suf) and len(sid) > len(suf):
            return sid[: -len(suf)], True
    return sid, False


# ------------------------------------------------------------------ 集計 (純粋)
def build_bank(battles: list, resolve, min_roster: int = REAL_BANK_MIN_ROSTER) -> dict:
    """battles: tools.party_improvements.parse_battle の出力の列。resolve(ja) → species_id or None。

    戻り値: {"version", "n_battles", "teams": [{"roster", "n", "picks", "leads", "revealed", "results"}],
             "species": {id: {"appear", "picked", "lead", "moves", "items", "abilities", "mega"}}}
    """
    teams: dict = {}
    species: dict = defaultdict(lambda: {"appear": 0, "picked": 0, "lead": 0, "moves": Counter(), "items": Counter(),
                                         "abilities": Counter(), "mega": 0})
    n_used = 0
    for b in battles:
        ids = []
        ja2id = {}
        mega_seen = set()
        for ja in b.get("opp_roster") or []:
            sid = resolve(ja)
            if not sid:
                continue
            sid, is_mega = base_species(sid)
            if is_mega:
                mega_seen.add(sid)
            ja2id[ja] = sid
            if sid not in ids:
                ids.append(sid)
        if len(ids) < min_roster:
            continue
        n_used += 1
        key = roster_key(ids)
        t = teams.setdefault(key, {"roster": sorted(ids), "n": 0, "picks": Counter(), "leads": Counter(),
                                   "revealed": defaultdict(lambda: {"moves": Counter(), "items": Counter(),
                                                                    "abilities": Counter(), "mega": 0}),
                                   "results": Counter()})
        t["n"] += 1
        t["results"][b.get("outcome") or "unknown"] += 1
        picked = {ja2id[ja] for ja in (b.get("opp_fielded") or []) if ja in ja2id}
        for sid in picked:
            t["picks"][sid] += 1
        lead = ja2id.get(b.get("opp_lead"))
        if lead:
            t["leads"][lead] += 1
        for ja in b.get("opp_mega_ja") or []:
            if ja in ja2id:
                mega_seen.add(ja2id[ja])
        for sid in mega_seen:
            t["revealed"][sid]["mega"] += 1
            species[sid]["mega"] += 1
        for e in b.get("events") or []:
            sid = ja2id.get(e.get("opp"))
            eid = e.get("id") or ""
            if not sid:
                continue
            for prefix, key2 in (("move_opponent_", "moves"), ("item_opponent_", "items"), ("ability_opponent_", "abilities")):
                if eid.startswith(prefix):
                    t["revealed"][sid][key2][eid[len(prefix):]] += 1
                    species[sid][key2][eid[len(prefix):]] += 1
        for sid in ids:
            species[sid]["appear"] += 1
            if sid in picked:
                species[sid]["picked"] += 1
            if sid == lead:
                species[sid]["lead"] += 1
    out_teams = []
    for key, t in teams.items():
        out_teams.append({"roster": t["roster"], "n": t["n"], "picks": dict(t["picks"]), "leads": dict(t["leads"]),
                          "revealed": {sid: {"moves": dict(r["moves"]), "items": dict(r["items"]),
                                             "abilities": dict(r["abilities"]), "mega": r["mega"]}
                                       for sid, r in t["revealed"].items()},
                          "results": dict(t["results"]), "full": len(t["roster"]) == 6})
    out_teams.sort(key=lambda t: (-t["n"], t["roster"]))
    return {"version": BANK_VERSION, "built_at": time.strftime("%Y-%m-%d %H:%M"), "n_battles": n_used,
            "teams": out_teams,
            "species": {sid: {"appear": s["appear"], "picked": s["picked"], "lead": s["lead"], "mega": s["mega"],
                              "moves": dict(s["moves"]), "items": dict(s["items"]), "abilities": dict(s["abilities"])}
                        for sid, s in sorted(species.items())}}


def merge_set(rep, revealed: Optional[dict]):
    """代表型 (SetCandidate) に判明した型 (moves/items/abilities の頻度) を重ねる (純粋)。
    持ち物・特性は最頻の観測、技は観測の多い順に入れて残りを代表型で埋める"""
    from tools.team_build.sets import SetCandidate
    rv = revealed or {}
    item = rep.item
    if rv.get("items"):
        item = max(rv["items"].items(), key=lambda kv: (kv[1], kv[0]))[0]
    ability = rep.ability
    if rv.get("abilities"):
        ability = max(rv["abilities"].items(), key=lambda kv: (kv[1], kv[0]))[0]
    moves = [m for m, _ in sorted((rv.get("moves") or {}).items(), key=lambda kv: (-kv[1], kv[0]))][:4]
    for m in rep.moves:
        if len(moves) >= 4:
            break
        if m not in moves:
            moves.append(m)
    notes = list(rep.notes) + [f"real:{k}" for k in ("items", "abilities", "moves") if rv.get(k)]
    return SetCandidate(rep.species_id, ability, item, rep.nature, rep.evs, moves, "real+" + (rep.source or ""),
                        rep.score, notes)


def pick_distribution(team: dict, floor: float) -> dict:
    """観測した選出回数 → 個体ごとの選出確率 (未観測の個体にも floor を残す)"""
    n = max(1, int(team.get("n") or 0))
    out = {}
    for sid in team.get("roster") or []:
        out[sid] = max(floor, (team.get("picks") or {}).get(sid, 0) / n)
    return out


def blamed_species(errors: list, roster: list) -> set:
    """validate-team のエラー文 ("Garchomp can't learn Flip Turn." 等) から名指しされた種 id を拾う (純粋)"""
    ids = set()
    for e in errors or []:
        head = e.split(" can't ")[0].split("'s ")[0].split(" has ")[0].split(" is ")[0]
        cand = "".join(ch for ch in head.lower() if ch.isalnum())
        for sid in roster:
            if cand == sid or (cand and sid.startswith(cand)) or (cand and cand.startswith(sid)):
                ids.add(sid)
    return ids


# ------------------------------------------------------------------ 本文 (図鑑・DB が要る)
def attach_texts(bank: dict, log=print) -> dict:
    """6 体そろった相手について、代表型 + 判明した型で Showdown 本文を作り validate-team で合法性を検査する"""
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.data import database as db
    from tools.team_build import sets as S
    n_ok = 0
    with db.get_connection() as conn:
        sid_snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
        item_map = S.item_usage_map(conn, sid_snap, sorted({s for t in bank["teams"] for s in t["roster"]}))
        for t in bank["teams"]:
            t["text"], t["legal"] = None, False
            if not t.get("full"):
                continue
            reps = {sid: S.representative_set(conn, sid_snap, sid) for sid in t["roster"]}
            if any(r is None for r in reps.values()):
                t["error"] = "代表型が無い種を含む"
                continue
            # 画面認識で別個体に付いた技・持ち物が混ざると不合法になる → 名指しされた個体を代表型に戻して再検証
            reverted = set()
            for _attempt in range(len(t["roster"]) + 1):
                team = [merge_set(reps[sid], None if sid in reverted else (t.get("revealed") or {}).get(sid))
                        for sid in t["roster"]]
                alternatives = {c.species_id: [c] for c in team}
                team = S.enforce_max_megas(team, alternatives)      # 実構築は石 2 個が普通 (上限は config)
                team = S.resolve_item_clause(team, item_map)
                text = S.to_showdown_text(team)
                ok, errs = S.validate_team_text(text, TRAINING_BATTLE_FORMAT)
                t["text"], t["legal"], t["errors"] = text, ok, errs[:3]
                if ok:
                    break
                blamed = blamed_species(errs, t["roster"]) - reverted
                if not blamed:
                    break
                reverted |= blamed
            t["reverted"] = sorted(reverted)
            n_ok += int(t["legal"])
    log(f"[real_opponents] 本文を作成: 合法 {n_ok} / 6 体そろった {sum(1 for t in bank['teams'] if t.get('full'))} "
        f"/ 全 {len(bank['teams'])} 構成")
    return bank


def build(days: Optional[float] = None, last: Optional[int] = None, out: Optional[Path] = None, log=print) -> dict:
    from tools.party_improvements import load_battles
    from vision.normalize import NameResolver
    resolver = NameResolver()
    cache = {}

    def resolve(ja):
        if ja not in cache:
            r = resolver.resolve_species(ja, cutoff=0.9)
            cache[ja] = r[1] if r else None
        return cache[ja]

    battles = load_battles(days=days, last=last)
    bank = build_bank(battles, resolve)
    bank = attach_texts(bank, log=log)
    out = out or (REPO / REAL_BANK_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bank, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"[real_opponents] 保存: {out} (対戦 {bank['n_battles']} / 構成 {len(bank['teams'])} / 種 {len(bank['species'])})")
    return bank


def show(bank: dict, top: int = 10) -> str:
    from advisor.infer import species_ja_name
    L = [f"実戦の相手バンク: 対戦 {bank['n_battles']} / 構成 {len(bank['teams'])} "
         f"(6 体そろい {sum(1 for t in bank['teams'] if t.get('full'))}、本文合法 {sum(1 for t in bank['teams'] if t.get('legal'))}) "
         f"/ 種 {len(bank['species'])}  ({bank.get('built_at')})", ""]
    L.append("種ごとの出現 / 選出率 / 先発率 (出現 3 以上、選出率順):")
    rows = [(sid, s) for sid, s in bank["species"].items() if s["appear"] >= 3]
    rows.sort(key=lambda kv: -(kv[1]["picked"] / kv[1]["appear"]))
    for sid, s in rows[:top * 2]:
        L.append(f"  {species_ja_name(sid)}: 出現 {s['appear']} / 選出 {s['picked'] / s['appear']:.0%} / "
                 f"先発 {s['lead'] / s['appear']:.0%}" + (f" / メガ {s['mega']}" if s['mega'] else ""))
    L.append("")
    L.append("よく当たった構成 (n 順):")
    for t in bank["teams"][:top]:
        L.append(f"  n={t['n']} {'合法' if t.get('legal') else '本文なし'} " +
                 " / ".join(species_ja_name(s) for s in t["roster"]) +
                 f"  選出 {', '.join(f'{species_ja_name(k)}×{v}' for k, v in sorted(t['picks'].items(), key=lambda kv: -kv[1]))}"
                 + (f"  先発 {', '.join(f'{species_ja_name(k)}×{v}' for k, v in t['leads'].items())}" if t['leads'] else ""))
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--last", type=int, default=None)
    args = ap.parse_args(argv)
    path = REPO / REAL_BANK_PATH
    if args.build:
        bank = build(days=args.days, last=args.last)
        print(show(bank))
        return 0
    if not path.exists():
        print(f"バンクがありません: {path} (--build で作る)")
        return 1
    print(show(json.loads(path.read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
