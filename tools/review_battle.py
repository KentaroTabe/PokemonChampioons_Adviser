"""ポストゲームレビュー: 1対戦の「アドバイスと実際の行動」の突き合わせ。

対戦ログに記録された各ターンのアドバイス (advice.best) と、実際に選んだ
行動 (move_player_* / switch_player) を比較し、一致率と分岐点を表示する。
「どこでアドバイスと違う手を選び、その対戦はどうなったか」の振り返り用。

    python -m tools.review_battle                 # 最新の勝敗確定対戦
    python -m tools.review_battle --battle <log>
    python -m tools.review_battle --all           # 一致した手も表示

注意: これは「アドバイザーとの差分」であり「ミスの断定」ではない。
アドバイザー自体の精度が天井のため、分岐点は再検討の候補として読む。
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"


def _ja_move(mid: str, resolver) -> str:
    return resolver.ja_of("moves", mid) or mid


def _load(path: str) -> list:
    out = []
    for line in open(path):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _player_action(rec: dict):
    for f in rec.get("fired") or []:
        if f.startswith("move_player_"):
            return ("move", f[len("move_player_"):])
        if f == "switch_player":
            return ("switch", None)
    return None


def _active_species(records: list, i: int):
    """records[i] 以降で最初の対戦シーンの自分activeの種族id"""
    for d in records[i:i + 12]:
        if d.get("type") != "scene":
            continue
        st = d.get("state") or {}
        pl = st.get("player") or {}
        idx = pl.get("active")
        party = pl.get("party") or []
        if idx is not None and 0 <= idx < len(party):
            return party[idx].get("species"), party[idx].get("ja")
    return None, None


def review(path: str, show_all: bool) -> None:
    from vision.normalize import NameResolver
    resolver = NameResolver()
    records = _load(path)
    outcome = next((d.get("outcome") for d in records
                    if d.get("type") == "outcome"), "unknown")
    pending_advice = None
    decisions = []   # (t, actual_label, best_label, agree, best)
    for i, d in enumerate(records):
        if d.get("type") == "advice" and d.get("kind") == "battle":
            adv = d.get("advice") or {}
            if adv.get("best"):
                pending_advice = (d.get("t", 0), adv)
            continue
        if d.get("type") != "events":
            continue
        act = _player_action(d)
        if act is None or pending_advice is None:
            continue
        t_adv, adv = pending_advice
        if d.get("t", 0) - t_adv > 60:   # 古すぎる助言は紐付けない
            pending_advice = None
            continue
        best = adv["best"]
        if act[0] == "move":
            actual = f"技: {_ja_move(act[1], resolver)}"
            agree = best.get("kind") == "move" and \
                best.get("id") == act[1]
        else:
            sid, ja = _active_species(records, i + 1)
            actual = f"交代: {ja or sid or '?'}"
            agree = best.get("kind") == "switch" and \
                (sid is None or best.get("id") == sid)
        decisions.append((d.get("t", 0), actual, best, agree))
        pending_advice = None

    name = Path(path).name
    n = len(decisions)
    n_agree = sum(1 for x in decisions if x[3])
    print(f"🔍 レビュー: {name} → 結果: "
          f"{'勝ち' if outcome == 'win' else '負け' if outcome == 'loss' else '不明'}")
    if not n:
        print("アドバイスと行動の対応付けができる手がありません")
        return
    print(f"アドバイス一致率: {n_agree}/{n} ({n_agree / n:.0%})\n")
    for t, actual, best, agree in decisions:
        if agree and not show_all:
            continue
        ts = time.strftime("%H:%M:%S", time.localtime(t))
        mark = "○" if agree else "≠"
        kind = "技" if best.get("kind") == "move" else "交代"
        rec_label = f"{kind}: {best.get('name') or best.get('id')}"
        line = f"{mark} [{ts}] 実際 {actual} / 推奨 {rec_label}"
        if not agree and best.get("reason"):
            line += f"  ({best['reason']})"
        print(line)
    if n_agree == n:
        print("(全手がアドバイスと一致)")
    elif outcome == "loss":
        print("\n負け試合の分岐点は上の ≠ の手。次戦で試す候補になる")


def main() -> None:
    ap = argparse.ArgumentParser(description="1対戦のポストゲームレビュー")
    ap.add_argument("--battle", default=None)
    ap.add_argument("--all", action="store_true", help="一致した手も表示")
    args = ap.parse_args()
    battle = args.battle
    if battle is None:
        for f in reversed(sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))):
            recs = _load(f)
            if any(d.get("type") == "outcome" and
                   d.get("outcome") in ("win", "loss") for d in recs):
                battle = f
                break
    if battle is None:
        raise SystemExit("勝敗確定の対戦ログがありません")
    review(battle, args.all)


if __name__ == "__main__":
    main()
