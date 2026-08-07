"""操縦の弱点分析: ベンチ条件で対戦し、負けを型分類する。

    python -m tools.weakness_report --battles 3000

構造的レバーの選択材料 (2026-08-07の停滞2日連続を受けた Step 1):
  setup_loss が支配的 → 観測拡張 (積まれ危険度) / 積まれペナルティのA/B
  outmatched が支配的 → 苦手カリキュラム (H5) or 構築側
  close_loss が支配的 → 終盤の詰め (報酬の終盤重み等)
測定は --no-save 相当 (last_eval を書かない)。結果は logs/weakness/ へ保存。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

logging.getLogger("poke-env").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "logs" / "weakness"
OPP_SEED = 20260730


async def run(n_battles: int, style: str) -> dict:
    import random
    from poke_env import AccountConfiguration
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.env.loss_probe import (
        attach_loss_probe, battle_rows, summarize,
    )
    from champions_agent.env.ranked_teams import RankedTeambuilder
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, apply_matchup_teampreview,
        make_benchmark_player,
    )
    from champions_agent.train.evaluate import ModelPlayer, _uniq_accounts

    acc1, acc2 = _uniq_accounts()
    me = ModelPlayer(
        account_configuration=acc1,
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(top_n=60, include_external=False),
        play_style=style, checkpoint="best")
    opp = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        team=RankedTeambuilder(top_n=60, include_external=False,
                               rng=random.Random(OPP_SEED)),
        account_configuration=acc2)
    apply_matchup_teampreview(me)
    apply_matchup_teampreview(opp)
    attach_loss_probe(me)

    await me.battle_against(opp, n_battles=n_battles)
    rows = battle_rows(me)
    return {"rows": rows, "summary": summarize(rows)}


def _fmt(summary: dict) -> str:
    from advisor.infer import species_ja_name

    def ja(sid):
        return species_ja_name(sid) or sid

    n, losses = summary["n"], summary["losses"]
    lines = [f"■ 弱点分析 ({n}戦 / 負け{losses}戦)"]
    cats = summary["categories"]
    label = {"setup_loss": "積まれ負け (相手+2以上)",
             "close_loss": "詰め損ね (相手残り1体)",
             "outmatched": "対面押し負け (残り2体以上)"}
    for k in ("setup_loss", "close_loss", "outmatched"):
        c = cats.get(k, 0)
        pct = c / losses * 100 if losses else 0
        lines.append(f"  {label[k]:28s}: {c:4d}件 ({pct:.0f}%)")
    if summary["sweepers"]:
        lines.append("  積んできた相手 (積まれ負けの内訳):")
        for sp, c in summary["sweepers"]:
            lines.append(f"    {ja(sp)}: {c}件")
    lines.append("  苦手構築 (5戦以上・勝率昇順):")
    for key, w, l in summary["worst_teams"]:
        names = " / ".join(ja(s) for s in key.split("|")[:6])
        lines.append(f"    {w}勝{l}敗  {names}")
    lines.append("  苦手種族 (相手に居たとき・20戦以上):")
    for sp, w, l in summary["worst_species"]:
        lines.append(f"    {ja(sp)}: {w}勝{l}敗 ({w / (w + l):.0%})")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="操縦の弱点分析 (負けの型分類)")
    ap.add_argument("--battles", type=int, default=3000)
    ap.add_argument("--style", default="balance")
    args = ap.parse_args()

    result = asyncio.run(run(args.battles, args.style))
    print(_fmt(result["summary"]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"weakness_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
