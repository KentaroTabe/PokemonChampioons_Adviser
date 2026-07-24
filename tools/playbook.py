"""プレイブック生成: 自チーム×環境上位構築の選出チャートと勝ち筋。

自分のパーティ (config/my_team.json の登録型) を環境上位の各構築と
AI同士で実対戦させ、「どの相手に何を選出し、誰を先発にし、何に警戒するか」を
実測ベースの虎の巻 (markdown) にまとめる。

    python -m tools.playbook                     # 上位12構築 x 各30戦
    python -m tools.playbook --opponents 20 --battles 60
    python -m tools.playbook --team-file my.txt  # 任意チームのプレイブック

出力: logs/playbooks/playbook_<時刻>.md (+ 標準出力に要約)
注意:
- 操縦は学習済みRL方策のため、助言の質は方策の強さが天井
- 同一チーム同士の反復対戦は展開が相関するため、勝率は0%/100%へ
  振れやすい。数値の絶対値より「有利/互角/不利」の傾向として読む
- 勝率が全体に低い場合はパーティ改善 (tools/evolve_teams --seed-myteam) を先に
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from collections import Counter
from pathlib import Path

logging.getLogger("poke-env").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "logs" / "playbooks"


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _team_ids(team_text: str) -> list:
    out = []
    for block in team_text.strip().split("\n\n"):
        head = block.strip().split("\n")[0]
        out.append(_to_id(head.split(" @ ")[0]))
    return out


def _ja(sid: str) -> str:
    from advisor.infer import species_ja_name
    return species_ja_name(sid) or sid


def _types_of(sid: str) -> list:
    from advisor.dex import get_dex
    sp = get_dex().species(sid)
    return (sp or {}).get("types") or []


def _best_eff(atk_ids_types: list, def_types: list) -> float:
    from advisor.dex import get_dex
    return max((get_dex().effectiveness(t, def_types)
                for t in atk_ids_types), default=1.0)


def _matchup_scores(my_ids: list, opp_ids: list) -> dict:
    """自分の各ポケモンの対面スコア (攻撃相性 - 被弾相性 の合計)"""
    scores = {}
    for mid in my_ids:
        mt = _types_of(mid)
        s = 0.0
        for oid in opp_ids:
            ot = _types_of(oid)
            s += _best_eff(mt, ot) - _best_eff(ot, mt)
        scores[mid] = s
    return scores


async def _battle_opponent(my_text: str, opp_text: str,
                           n_battles: int) -> dict:
    """自チーム vs 1つの相手構築をn戦。勝率と実際の選出傾向を返す"""
    from tools.evaluate_team import _make_player
    me, ev = _make_player(my_text, "PBa", "rl", "matchup")
    opp, _ = _make_player(opp_text, "PBb", ev, "matchup")
    await asyncio.wait_for(me.battle_against(opp, n_battles=n_battles),
                           timeout=120 * n_battles)
    trios_win, trios_all = Counter(), Counter()
    for b in me.battles.values():
        team = [p.species for p in b.team.values()]
        if len(team) != 3:
            continue
        key = tuple(sorted(team))
        trios_all[key] += 1
        if b.won:
            trios_win[key] += 1
    return {"win_rate": me.n_won_battles / n_battles,
            "wins": me.n_won_battles, "n": n_battles,
            "trios_win": trios_win, "trios_all": trios_all,
            "evaluator": ev}


def _entry_text(opp_ids: list, r: dict, my_ids: list) -> tuple:
    """1相手構築ぶんのプレイブック記述 (見出し, 本文行リスト)"""
    trio = None
    for src in (r["trios_win"], r["trios_all"]):
        if src:
            trio = list(src.most_common(1)[0][0])
            break
    scores = _matchup_scores(my_ids, opp_ids)
    if trio is None:
        trio = sorted(my_ids, key=lambda m: -scores.get(m, 0))[:3]
    # 先発: 選出3体のうち対面スコア最大
    lead = max(trio, key=lambda m: scores.get(m, 0))
    # 要警戒: 相手で「自分の選出3体への攻撃相性 - 被弾」が最大の個体
    opp_scores = _matchup_scores(opp_ids, trio)
    danger = max(opp_scores, key=opp_scores.get) if opp_scores else None
    wr = r["win_rate"]
    verdict = "有利" if wr >= 0.6 else ("互角" if wr >= 0.45 else "不利")
    head = f"vs {' / '.join(_ja(o) for o in opp_ids)}"
    lines = [
        f"- 実測勝率: {wr:.0%} ({r['wins']}/{r['n']}) → **{verdict}**",
        f"- 推奨選出: {' / '.join(_ja(m) for m in trio)} (先発: {_ja(lead)})",
    ]
    if danger is not None:
        lines.append(f"- 要警戒: {_ja(danger)} (この選出に通りやすい)")
    if wr < 0.45:
        best_all = max(_matchup_scores(my_ids, opp_ids).items(),
                       key=lambda x: x[1])[0]
        if best_all not in trio:
            lines.append(f"- 別案: {_ja(best_all)} の選出も検討 (相性値は最大)")
    return head, lines


async def run(args) -> None:
    from champions_agent.env.ranked_teams import build_ranked_teams
    from tools.evaluate_team import build_myteam_text
    if args.team_file:
        my_text = Path(args.team_file).read_text(encoding="utf-8")
    else:
        my_text = build_myteam_text()
    my_ids = _team_ids(my_text)
    opps = build_ranked_teams()[:args.opponents]
    print(f"[playbook] 自チーム: {' / '.join(_ja(m) for m in my_ids)}")
    print(f"[playbook] 相手: 上位{len(opps)}構築 x 各{args.battles}戦", flush=True)

    results = []
    t0 = time.time()
    for i in range(0, len(opps), args.concurrency):
        chunk = opps[i:i + args.concurrency]
        rs = await asyncio.gather(*[
            _battle_opponent(my_text, o, args.battles) for o in chunk],
            return_exceptions=True)
        for o, r in zip(chunk, rs):
            if isinstance(r, Exception):
                print(f"[playbook] 対戦失敗 (相手{i}): {r}", flush=True)
                continue
            results.append((o, r))
        print(f"[playbook] {len(results)}/{len(opps)} 構築完了", flush=True)

    results.sort(key=lambda x: x[1]["win_rate"])
    overall = sum(r["wins"] for _, r in results) / \
        max(1, sum(r["n"] for _, r in results))
    md = [f"# プレイブック ({time.strftime('%Y-%m-%d %H:%M')})",
          f"自チーム: {' / '.join(_ja(m) for m in my_ids)}",
          f"総合勝率 (環境上位{len(results)}構築): {overall:.0%} / "
          f"評価者: RL方策+相性選出 / 所要{time.time() - t0:.0f}s",
          "", "苦手な構築から順に並べる。", ""]
    for opp_text, r in results:
        head, lines = _entry_text(_team_ids(opp_text), r, my_ids)
        md.append(f"## {head}")
        md += lines
        md.append("")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"playbook_{time.strftime('%Y%m%d_%H%M')}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"\n総合勝率: {overall:.0%}")
    print("苦手トップ3:")
    for opp_text, r in results[:3]:
        print(f"  {r['win_rate']:.0%} vs "
              f"{' / '.join(_ja(o) for o in _team_ids(opp_text))}")
    print(f"プレイブック: {path}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="選出チャート+勝ち筋の生成")
    ap.add_argument("--opponents", type=int, default=12,
                    help="環境上位から何構築を相手にするか")
    ap.add_argument("--battles", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--team-file", default=None,
                    help="Showdownチームテキスト (省略時はmy_team.json)")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
