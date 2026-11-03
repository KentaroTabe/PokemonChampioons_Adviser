"""文言・表示の読み取りの再現率の目安 (読み取りだけ。2026-10-30 レビュー、開発者承認)。正解には自分側の登録の持ち物と、
対戦中に必ず起きたはずの事象を使う。設計文書 §1.2 の「自分側の場に出た個体数と表示」「たべのこし の再現率」「終わったはずの天候」の出典。

    python -B -m tools.item_cues.recall --logs <logs/battles> [--out DIR]

数えるもの (10/30 の recall_cues.py、player_items.py。対応表は tools/item_cues/__init__.py):
- たべのこし: 自分の登録が たべのこし で、技を ITEM_CUE_RECALL_MIN_MOVES 回以上使った個体のうち、たべのこし の表示 (item_player_leftovers) を
  1 回以上読めた割合 (HP 満タンの間は出ないので下限の目安)
- 天候の終了の文言: 後の開始で上書きされず、開始から対戦の最後まで ITEM_CUE_MUST_END_TURNS ターン以上ある (= 延長でも対戦中に終わった)
  開始のうち、終了の文言を読めた割合
- 自分の登録の持ち物ごとの、場に出た個体の数と、その対戦で表示 (item_player_<id>) が 1 回以上あった数
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from champions_agent import config as C
from tools.item_cues import common as cm

CLI = "recall"
LEFTOVERS = "leftovers"
RULES = (
    "帰属 (主): next_on_switch (技・たべのこし の表示を場の個体に帰属。たべのこし の表示の名前は使わない)",
    "自分の登録の持ち物: 状態行の自分の party の item の、(対戦, 基本種) ごとの最初の値。場に出た = 状態行で場の個体だったことがある",
    "持ち物の表示の有無 (登録の持ち物ごと) は対戦単位: その対戦で item_player_<id> が 1 回以上 (どの個体の表示かは問わない。10/30 と同じ)",
)


def weather_must_end(eps: list, maxturn: int, must_end: int = C.ITEM_CUE_MUST_END_TURNS) -> tuple:
    """天候の開始の列 → (終わったはずの開始の数, そのうち終了の文言を読めた数) (純粋)。
    終わったはず = 後の開始で上書きされず、開始の turn から対戦の最大ターンまで must_end 以上"""
    den = num = 0
    for k, ep in enumerate(eps):
        if cm.superseded(eps, k):
            continue
        if isinstance(ep["turn"], int) and maxturn - ep["turn"] >= must_end:
            den += 1
            num += int(ep["ended"])
    return den, num


def holder_recall(moves: dict, fires: dict, my_items: dict, item: str, min_moves: int = C.ITEM_CUE_RECALL_MIN_MOVES) -> tuple:
    """登録が item の個体のうち技を min_moves 回以上使った数と、そのうち表示が 1 回以上あった数 (純粋)。
    moves / fires: {基本種: 回数}"""
    den = num = 0
    for sid, it in my_items.items():
        if it == item and moves.get(sid, 0) >= min_moves:
            den += 1
            num += int(fires.get(sid, 0) > 0)
    return den, num


def collect(loaded: dict) -> dict:
    R: dict = defaultdict(Counter)
    for b in loaded["battles"]:
        rows = b["rows"]
        tl = cm.build_timeline(rows)
        my_items = cm.registered_items(rows)
        moves, lefto = Counter(), Counter()
        fired_all = set()
        for e in tl:
            sid = e["active"]["player"]
            for f in e["fired"]:
                fired_all.add(f)
                if not sid:
                    continue
                if f.startswith("move_player_"):
                    moves[sid] += 1
                if cm.item_fire(f) == ("player", LEFTOVERS):
                    lefto[sid] += 1
        den, num = holder_recall(moves, lefto, my_items, LEFTOVERS)
        R["leftovers"]["holders"] += den
        R["leftovers"]["holders_with_display"] += num
        eps, _orphan = cm.episodes(tl, cm.weather_start, cm.weather_end)
        den, num = weather_must_end(eps, cm.max_turn(tl))
        R["weather_end_msg"]["must_end"] += den
        R["weather_end_msg"]["read"] += num
        out_ = cm.appeared(rows, "player")
        for sid, it in my_items.items():
            if sid not in out_:
                continue
            R["player_item_on_field"][it] += 1
            R["player_item_displayed"][it] += int(f"item_player_{it}" in fired_all)
    return {"R": R}


def render(rec: dict, res: dict) -> list:
    R = res["R"]
    lf, we = R["leftovers"], R["weather_end_msg"]
    out = ["# 文言・表示の再現率 (recall)", ""] + cm.render_record(rec)
    out += ["## 再現率", ""]
    out += cm.md_table(["", "母数", "読めた", "割合"], [
        [f"たべのこし 持ちで技 {C.ITEM_CUE_RECALL_MIN_MOVES} 回以上の個体", lf["holders"], lf["holders_with_display"],
         cm.ratio(lf["holders_with_display"], lf["holders"])],
        [f"上書きされず {C.ITEM_CUE_MUST_END_TURNS} ターン以上続いた天候の終了の文言", we["must_end"], we["read"],
         cm.ratio(we["read"], we["must_end"])],
    ])
    out += ["", "## 自分の登録の持ち物ごとの場に出た個体と表示", ""]
    out += cm.md_table(["持ち物", "場に出た", "表示あり (対戦単位)"],
                       [[it, n, R["player_item_displayed"][it]] for it, n in
                        sorted(R["player_item_on_field"].items(), key=lambda kv: (-kv[1], kv[0]))])
    return out + [""]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="文言・表示の読み取りの再現率の目安 (実戦ログを読むだけ)")
    cm.add_common_args(ap)
    args = ap.parse_args(argv)
    logs = Path(args.logs)
    loaded = cm.load_battles(logs)
    rec = cm.repro_record(CLI, logs, loaded, vars(args), RULES)
    res = collect(loaded)
    payload = {"record": rec, "counts": {k: dict(v) for k, v in res["R"].items()}}
    md, js = cm.write_outputs(Path(args.out), CLI, render(rec, res), payload)
    print(f"→ {md}\n→ {js}")


if __name__ == "__main__":
    main()
