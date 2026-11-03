"""天候・フィールド・壁の開始・終了・ターン数と、場の状況画面の n/m (読み取りだけ。2026-10-30 レビュー、開発者承認)。
設計文書 §1.2 の「天候・フィールド・壁」の出典。

    python -B -m tools.item_cues.field --logs <logs/battles> [--out DIR]

数えるもの (10/30 の count_cues.py の D、refine_cues.py の 3-5、retrigger.py、field_turns.py。対応表は tools/item_cues/__init__.py):
- 開始 (ITEM_CUE_MERGE_SEC 以内の同じ効果の信号を 1 回に束ねる): 効果ごと、信号 (文言・特性・技) の組、設定した側
- 終了の文言: 読めた開始の数、開始を読めていない終了の発火、開始から終了までの turn の値の差
- 設定した個体の持ち物 (自分 = 登録、相手 = 判明した持ち物) と、延長の持ち物の有無の分類ごとの
  開始の数・ITEM_CUE_OPPORTUNITY_TURNS 以上続いた機会・終了までのターン数・ITEM_CUE_MUST_END_TURNS 以上続いて上書きも終了の文言も無い開始
- 天候の再発動 (次の開始が同じ天候)・上書き (別の天候)、1 対戦の開始の回数
- 状態行の field の weather_turns / terrain_turns の分布、ITEM_CUE_FIELD_BASE_TURNS を超えた例 (= 場の状況画面の n/m の m)、
  場の状況画面 (scene = field_check) の行の数
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from champions_agent import config as C
from tools.item_cues import common as cm
from vision.scenes import SCENE_FIELD_CHECK

CLI = "field"
LABELS = {"weather": "天候", "terrain": "フィールド", "screen": "壁"}
RULES = (
    "開始の信号: 天候 = 文言 (rain_start 等)・特性 (ability_<側>_drizzle 等)・技 (move_<側>_raindance 等)、"
    "フィールド = 文言・特性・技、壁 = 技 (move_<側>_reflect 等)",
    "終了の対応: 同じ効果の未終了の開始のうち最後のもの。フィールドの終了 (terrain_end) は効果を問わない。壁は文言の「相手 / 味方」で側を合わせる",
    "設定した個体: 特性・技の発火の側の、帰属 (next_on_switch) の場の個体。文言だけの開始は設定した個体が分からない (unknown)",
    "上書き: 天候・フィールドは後の開始すべて、壁は同じ効果・同じ側の後の開始 (ITEM_CUE_MERGE_SEC より後)",
    "ターン数は events 行の turn の値 (数え漏れの誤差を含む)。状態行の field は全部の状態行 (scene / advice) を数える",
)


def item_class(kind: str, ep: dict, my_items: dict) -> str:
    """設定した個体の持ち物の分類 (純粋): extender (自分の登録が延長の持ち物) / no_extender / opp / unknown"""
    if ep["side"] == "opponent":
        return "opp"
    if ep["side"] != "player":
        return "unknown"
    return "extender" if my_items.get(ep["setter"], "?") in C.ITEM_CUE_EXTENDER_ITEMS[kind] else "no_extender"


def collect(loaded: dict) -> dict:
    R: dict = defaultdict(Counter)
    L: dict = defaultdict(list)
    for b in loaded["battles"]:
        name, rows = b["name"], b["rows"]
        tl = cm.build_timeline(rows)
        mt = cm.max_turn(tl)
        my_items = cm.registered_items(rows)
        opp_known = cm.known_opponent_items(rows)
        for kind, (start_fn, end_fn) in cm.FIELD_KINDS.items():
            eps, orphan = cm.episodes(tl, start_fn, end_fn)
            R[f"{kind}_end_without_start"].update(orphan)
            for k, ep in enumerate(eps):
                R[f"{kind}_start"][ep["val"]] += 1
                R[f"{kind}_start_src"]["+".join(sorted(ep["src"]))] += 1
                R[f"{kind}_start_side"][ep["side"] or "unknown"] += 1
                st_turn = ep["turn"] if isinstance(ep["turn"], int) else None
                remain = (mt - st_turn) if st_turn is not None else None
                icls = item_class(kind, ep, my_items)
                R[f"{kind}_eps_by_class"][icls] += 1
                if remain is not None and remain >= C.ITEM_CUE_OPPORTUNITY_TURNS:
                    R[f"{kind}_opportunity_by_class"][icls] += 1
                if ep["ended"]:
                    dt = (ep["end_turn"] - st_turn) if isinstance(ep["end_turn"], int) and st_turn is not None else None
                    R[f"{kind}_ended"]["n"] += 1
                    R[f"{kind}_end_turns"][str(dt)] += 1
                    L[f"{kind}_end_dt_{icls}"].append(dt)
                    L[f"{kind}_end_detail"].append((name, ep["val"], ep["side"], ep["setter"], ep["turn"], ep["end_turn"],
                                                    round(ep["end_t"] - ep["t"], 1), "+".join(sorted(ep["src"]))))
                else:
                    long_ = remain is not None and remain >= C.ITEM_CUE_NOEND_REMAIN_TURNS
                    R[f"{kind}_no_end"]["remain_long" if long_ else "remain_short_or_unknown"] += 1
                    if not cm.superseded(eps, k, same_side_val=(kind == "screen")) and remain is not None \
                            and remain >= C.ITEM_CUE_MUST_END_TURNS:
                        R[f"{kind}_noend_must_end_not_superseded"][icls] += 1
                if ep["setter"] and ep["side"] == "player":
                    R[f"{kind}_setter_item_player"][my_items.get(ep["setter"], "?")] += 1
                elif ep["setter"] and ep["side"] == "opponent":
                    R[f"{kind}_setter_item_opponent"][(opp_known.get(ep["setter"]) or {}).get("item", "unrevealed")] += 1
            if kind == "weather" and eps:
                R["weather_retrigger"]["battles_with_weather"] += 1
                R["weather_starts_per_battle"][str(min(len(eps), 4)) if len(eps) < 4 else "4+"] += 1
                for a, c in zip(eps, eps[1:]):
                    R["weather_retrigger"]["same" if a["val"] == c["val"] else "other"] += 1

        # 状態行の field の残りターン・場の状況画面
        nfc = 0
        for r in cm.state_rows(rows):
            st = r["state"]
            f = st.get("field") or {}
            if r.get("type") == "scene" and r.get("scene") == SCENE_FIELD_CHECK:
                nfc += 1
            for key in ("weather", "terrain"):
                if f.get(key):
                    v = f.get(f"{key}_turns")
                    R[f"state_{key}_turns"][str(v)] += 1
                    if isinstance(v, int) and v > C.ITEM_CUE_FIELD_BASE_TURNS:
                        L["state_turns_over_base"].append((name, key, f.get(key), v, r.get("type"),
                                                           r.get("scene") or st.get("scene"), st.get("turn")))
        if nfc:
            R["field_check"]["battles"] += 1
            R["field_check"]["rows"] += nfc
    return {"R": R, "lists": dict(L)}


def render(rec: dict, res: dict) -> list:
    R, L = res["R"], res["lists"]
    out = ["# 天候・フィールド・壁 (field)", ""] + cm.render_record(rec)
    kinds = list(cm.FIELD_KINDS)
    out += ["## 開始と終了", ""]
    rows = [
        ["開始 (束ねた数)", *[f"{sum(R[f'{k}_start'].values())} ({cm.md_counter(R[f'{k}_start'])})" for k in kinds]],
        ["開始の信号", *[cm.md_counter(R[f"{k}_start_src"]) for k in kinds]],
        ["設定した側", *[cm.md_counter(R[f"{k}_start_side"]) for k in kinds]],
        ["終了の文言を読めた", *[R[f"{k}_ended"]["n"] for k in kinds]],
        ["開始を読めていない終了の文言", *[f"{sum(R[f'{k}_end_without_start'].values())} ({cm.md_counter(R[f'{k}_end_without_start'])})"
                                     for k in kinds]],
        [f"終了の文言なし (開始から最後まで ≥ {C.ITEM_CUE_NOEND_REMAIN_TURNS} / それ以外)",
         *[f"{R[f'{k}_no_end']['remain_long']} / {R[f'{k}_no_end']['remain_short_or_unknown']}" for k in kinds]],
        ["開始の数 (設定した個体の持ち物の分類)", *[cm.md_counter(R[f"{k}_eps_by_class"]) for k in kinds]],
        [f"開始後 {C.ITEM_CUE_OPPORTUNITY_TURNS} ターン以上続いた (機会)", *[cm.md_counter(R[f"{k}_opportunity_by_class"]) for k in kinds]],
        [f"{C.ITEM_CUE_MUST_END_TURNS} ターン以上続き、上書きも終了の文言も無い", *[cm.md_counter(R[f"{k}_noend_must_end_not_superseded"])
                                                                for k in kinds]],
        ["設定した自分の個体の登録の持ち物", *[cm.md_counter(R[f"{k}_setter_item_player"]) for k in kinds]],
        ["設定した相手の個体の判明した持ち物", *[cm.md_counter(R[f"{k}_setter_item_opponent"]) for k in kinds]],
    ]
    out += cm.md_table([""] + [LABELS[k] for k in kinds], rows)
    out += ["", "終了の文言までの turn の値の差 (設定した個体の持ち物の分類ごと):", ""]
    for k in kinds:
        for icls in ("extender", "no_extender", "opp", "unknown"):
            xs = L.get(f"{k}_end_dt_{icls}")
            if xs:
                out.append(f"- {LABELS[k]} {icls}: {sorted(xs, key=lambda x: (x is None, x if x is not None else 0))}")
    rt = R["weather_retrigger"]
    out += ["", "## 天候の再発動・上書き", "",
            f"- 天候のある対戦 {rt['battles_with_weather']}、1 対戦の開始の回数 {cm.md_counter(R['weather_starts_per_battle'])}",
            f"- 次の開始が同じ天候 (再発動) {rt['same']}、別の天候 (上書き) {rt['other']}", ""]
    over = L.get("state_turns_over_base", [])
    distinct = sorted({(x[0], x[1], x[2], x[3]) for x in over})
    out += ["## 状態の残りターン・場の状況画面", "",
            f"- weather_turns (状態行の件数): {cm.md_counter(R['state_weather_turns'])}",
            f"- terrain_turns (状態行の件数): {cm.md_counter(R['state_terrain_turns'])}",
            f"- 場の状況画面 ({SCENE_FIELD_CHECK}): 対戦 {R['field_check']['battles']}、行 {R['field_check']['rows']}",
            f"- 残りが {C.ITEM_CUE_FIELD_BASE_TURNS} を超えた状態行 {len(over)}、(対戦, 種類, 効果, 値) の組 {len(distinct)}、"
            f"対戦 {len({x[0] for x in over})}"]
    out += [f"  - {x}" for x in distinct] + [""]
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="天候・フィールド・壁の開始・終了・ターン数 (実戦ログを読むだけ)")
    cm.add_common_args(ap)
    args = ap.parse_args(argv)
    logs = Path(args.logs)
    loaded = cm.load_battles(logs)
    rec = cm.repro_record(CLI, logs, loaded, vars(args), RULES)
    res = collect(loaded)
    payload = {"record": rec, "counts": {k: dict(v) for k, v in res["R"].items()}, "lists": res["lists"]}
    md, js = cm.write_outputs(Path(args.out), CLI, render(rec, res), payload)
    print(f"→ {md}\n→ {js}")


if __name__ == "__main__":
    main()
