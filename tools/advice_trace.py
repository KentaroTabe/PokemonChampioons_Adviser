"""助言の追跡 (2026-10-05 ②): 対戦ログから「どの版が、どの状態を見て、何を推奨し、いつ表示されたか」を 1 本で追う。
2026-10-07 段 0: decision 行 (表示から決定まで・第一候補との一致)、advice 行の hp_stale 欄、選出の advice 行と advice_id で結んだ
selection_record 行 (候補・相手の選出の予測) の要約を足した (無い古いログは 0 件)。

    python -m tools.advice_trace [--last N | --battle <log>] [--json] [--late-sec 10] [--package <id>]
    python -m tools.advice_trace --variants [--last N] [--json]   # 影の計算 (advice_variant) の集計

受入条件との対応:
  1. 版: version の行 (指定 Package / 実際に読んだ選出モデルの sha / 退避理由) と、Package の manifest の selection_model_sha256 を
     突き合わせる (version_check)。一致 / 不一致 / 判定不能 (version の行が無い、manifest が無い) を分ける
  2. 表示: 助言の生成時刻 (advice.t_gen) とブラウザの表示時刻 (display の行、ブラウザの時計) を分けて持ち、表示までの遅れと、
     「古い状態への助言が遅れて表示された」(表示時点の局面がもう進んでいた) を区別する (display_rows の stale)
  3. 実行不能: 推奨した行動が選べたかを「システムの状態 (助言が見た state)」で判定する (feasible_in_state)。正解ラベルでの判定は
     局面集 (tools/scene_eval) が行う。判定できないときは None を残す (ひんしや PP が読めていない等)

純粋関数 (version_check / display_rows / feasible_in_state / feasibility_rows / summarize) は tests/test_advice_trace.py。
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
LATE_SEC = 10.0


# ------------------------------------------------------------------ 読み込み
def load_records(path) -> list:
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def package_selection_sha(package_id: Optional[str]) -> Optional[str]:
    """registry の Package の manifest.json → selection_model_sha256 (無ければ None)"""
    if not package_id:
        return None
    try:
        from tools.team_build.registry import Registry
        final = Registry().resolve(package_id)
        man = json.loads((Path(final) / "manifest.json").read_text(encoding="utf-8"))
        return man.get("selection_model_sha256")
    except Exception:
        return None


# ------------------------------------------------------------------ 1. 版
def version_check(records: list, package_sha: Optional[str] = None) -> dict:
    """version の行と Package の selection_model_sha256 の突き合わせ (純粋)。
    match_package: True (実際に読んだモデルの sha が Package のものと一致) / False (別のモデル = 退避や読み込み失敗) /
    None (判定不能: version の行が無い、sha が無い、Package の sha が無い)。sha は先頭の桁で比べる (記録は 16 桁)"""
    ver = next((d for d in records if d.get("type") == "version"), None)
    out = {"has_version": ver is not None, "version_id": None, "designated_package": None, "selection": None,
           "action_policy": None, "team_species": None, "fallback_reason": None, "match_package": None}
    if ver is None:
        return out
    sel = ver.get("selection_model") or {}
    out.update({"version_id": ver.get("version_id"), "designated_package": ver.get("designated_package"), "selection": sel,
                "action_policy": ver.get("action_policy"), "team_species": (ver.get("team") or {}).get("species"),
                "fallback_reason": sel.get("fallback_reason")})
    rec_sha, pkg_sha = sel.get("sha256"), package_sha
    if rec_sha and pkg_sha:
        n = min(len(rec_sha), len(pkg_sha))
        out["match_package"] = rec_sha[:n] == pkg_sha[:n]
    return out


# ------------------------------------------------------------------ 2. 表示
def _active_species(compact: Optional[dict], side: str = "player") -> Optional[str]:
    sd = (compact or {}).get(side) or {}
    idx = sd.get("active")
    party = sd.get("party") or []
    if isinstance(idx, int) and 0 <= idx < len(party):
        return party[idx].get("species")
    return None


def display_rows(records: list) -> list:
    """battle の助言 (provisional を除く、advice_id あり) ごとに表示の行を結ぶ (純粋)。
    {advice_id, kind, turn, t_gen, t_shown, latency (表示 − 生成、秒。ブラウザとサーバーの時計差を含む), displayed,
     stale (表示時点で局面が進んでいた: 直近の scene の turn が助言の turn より大きい、または場の個体が違う。判定できなければ None)}"""
    shown: dict = {}
    hidden: dict = {}
    for d in records:
        if d.get("type") == "display" and d.get("advice_id") and d.get("t_shown") is not None:
            if d.get("hidden"):
                hidden.setdefault(d["advice_id"], float(d["t_shown"]))     # タブが隠れていて描画されていない: 表示とは数えない (2026-10-06)
                continue
            shown.setdefault(d["advice_id"], float(d["t_shown"]))
    scenes = [(float(d.get("t") or 0), d) for d in records if d.get("type") == "scene"]
    rows = []
    for d in records:
        if d.get("type") != "advice" or not d.get("advice_id"):
            continue
        adv = d.get("advice") or {}
        if adv.get("provisional"):
            continue
        t_gen = adv.get("t_gen") or d.get("t")
        t_shown = shown.get(d["advice_id"])
        row = {"advice_id": d["advice_id"], "kind": d.get("kind"), "turn": d.get("turn"), "t_gen": t_gen, "t_shown": t_shown,
               "latency": (round(t_shown - float(t_gen), 3) if (t_shown is not None and t_gen is not None) else None),
               "displayed": t_shown is not None, "hidden": (t_shown is None and d["advice_id"] in hidden),
               "stale": None, "state_id": d.get("state_id"), "version_id": d.get("version_id")}
        if t_shown is not None and d.get("kind") == "battle":
            last_scene = None
            for t, sc in scenes:
                if t <= t_shown:
                    last_scene = sc
            if last_scene is not None:
                adv_turn, sc_turn = d.get("turn"), last_scene.get("turn")
                sp_adv, sp_sc = _active_species(d.get("state")), _active_species(last_scene.get("state"))
                if adv_turn is not None and sc_turn is not None and sc_turn > adv_turn:
                    row["stale"] = True
                elif sp_adv and sp_sc and sp_adv != sp_sc:
                    row["stale"] = True
                elif adv_turn is not None and sc_turn is not None:
                    row["stale"] = False
        rows.append(row)
    return rows


# ------------------------------------------------------------------ 3. 実行不能
def feasible_in_state(best: Optional[dict], compact: Optional[dict]) -> Optional[bool]:
    """推奨 best ({kind, id}) がその簡約状態で選べるか (純粋)。True / False / None (判定不能)。
    技: 場の個体がひんしでなく、その技が技欄にあり、PP が読めていれば > 0。技欄が空なら None
    交代: 相手の種 id の控えが居て、場の個体でなく、ひんしでなく、選出の印があれば選出済み。種 id が無ければ None"""
    if not best or not compact:
        return None
    pl = compact.get("player") or {}
    party = pl.get("party") or []
    idx = pl.get("active")
    active = party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else None
    kind, bid = best.get("kind"), best.get("id")
    if kind == "move":
        if active is None:
            return None
        if active.get("status") == "fainted" or (active.get("hp") is not None and active["hp"] <= 0):
            return False
        moves = active.get("moves") or []
        if not moves:
            return None
        hit = next((m for m in moves if (m[0] if isinstance(m, (list, tuple)) else m) == bid), None)
        if hit is None:
            return False
        pp = hit[1] if isinstance(hit, (list, tuple)) and len(hit) > 1 else None
        if pp is None:
            return None
        return int(pp) > 0
    if kind == "switch":
        if not bid:
            return None
        cands = [i for i, p in enumerate(party) if p.get("species") == bid]
        if not cands:
            return None
        for i in cands:
            p = party[i]
            if i == idx or p.get("status") == "fainted" or (p.get("hp") is not None and p["hp"] <= 0):
                continue
            if p.get("picked") is False and any(q.get("picked") for q in party):
                continue
            return True
        return False
    return None


def feasibility_rows(records: list) -> list:
    """battle の助言 (provisional を除く) ごとに、推奨をシステムの状態で検査した行 (純粋)。feasible_truth は局面集で埋める"""
    rows = []
    for d in records:
        if d.get("type") != "advice" or d.get("kind") != "battle":
            continue
        adv = d.get("advice") or {}
        if adv.get("provisional") or not adv.get("ok", True):
            continue
        best = adv.get("best") or ((adv.get("actions") or [None])[0])
        rows.append({"advice_id": d.get("advice_id"), "turn": d.get("turn"),
                     "best": ({"kind": best.get("kind"), "id": best.get("id"), "name": best.get("name")} if best else None),
                     "feasible_system": feasible_in_state(best, d.get("state")) if d.get("state") else None,
                     "feasible_truth": None})
    return rows


# ------------------------------------------------------------------ 4. 表示から決定まで・HP の古さ (2026-10-07 段 0)
def decision_rows(records: list) -> list:
    """decision 行 (battle_logger、助言のあと決定が画面で確認できた時刻と第一候補との一致) を助言の表示と結ぶ (純粋)。
    shown_to_decided = 決定 − 表示 (秒。表示はブラウザの時計、決定はサーバーの時計)。decision 行の無い古いログは空"""
    shown = {}
    for d in records:
        if d.get("type") == "display" and d.get("advice_id") and d.get("t_shown") is not None and not d.get("hidden"):
            shown.setdefault(d["advice_id"], float(d["t_shown"]))
    rows = []
    for d in records:
        if d.get("type") != "decision":
            continue
        t_shown = d.get("t_shown") if d.get("t_shown") is not None else shown.get(d.get("advice_id"))
        t_dec = d.get("t_decided")
        rows.append({"advice_id": d.get("advice_id"), "turn": d.get("turn"), "t_shown": t_shown, "t_decided": t_dec,
                     "shown_to_decided": (round(float(t_dec) - float(t_shown), 3)
                                          if (t_dec is not None and t_shown is not None) else None),
                     "match": d.get("match"), "action": d.get("action"), "best": d.get("best")})
    return rows


def hp_stale_rows(records: list) -> list:
    """battle の助言 (provisional を除く) の hp_stale 欄 [{advice_id, player, opponent}] (純粋)。欄の無い古いログは空"""
    out = []
    for d in records:
        if d.get("type") != "advice" or d.get("kind") != "battle" or "hp_stale" not in d:
            continue
        if (d.get("advice") or {}).get("provisional"):
            continue
        hs = d.get("hp_stale") or {}
        out.append({"advice_id": d.get("advice_id"), "player": hs.get("player"), "opponent": hs.get("opponent")})
    return out


def selection_rows(records: list) -> list:
    """選出の助言 (ok) ごとに、advice_id で結んだ selection_record 行 (候補・相手の選出の予測) の有無と中身の要約 (純粋)。
    selection_record 行の無い古いログは has_record=False"""
    from tools.pick_labels import selection_records_by_advice
    recs = selection_records_by_advice(records)
    rows = []
    for d in records:
        if d.get("type") != "advice" or d.get("kind") != "selection" or not (d.get("advice") or {}).get("ok"):
            continue
        r = recs.get(str(d.get("advice_id"))) or {}
        pred = r.get("opp_pick_pred") if isinstance(r.get("opp_pick_pred"), dict) else {}
        cands = r.get("candidates") if isinstance(r.get("candidates"), dict) else {}
        rows.append({"advice_id": d.get("advice_id"), "has_record": bool(r),
                     "n_combos": len(pred.get("combos") or []), "incomplete": pred.get("incomplete"),
                     "methods": sorted(m for m, e in cands.items() if m not in ("reasons", "primary", "used") and e)})
    return rows


def selection_summary(rows: list) -> dict:
    """selection_rows の要約 (純粋): 選出の助言の数、記録の行と結べた数、6 枠判明の全分布 (incomplete=False) の数"""
    return {"n_advice": len(rows), "n_record": sum(1 for r in rows if r["has_record"]),
            "n_full_distribution": sum(1 for r in rows if r["has_record"] and r["incomplete"] is False)}


def _median(xs: list) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def decision_summary(rows: list) -> dict:
    """decision_rows の要約 (純粋): 件数、第一候補と一致 / 不一致 / 不明、表示から決定までの中央値"""
    return {"n": len(rows), "n_match": sum(1 for r in rows if r["match"] is True),
            "n_mismatch": sum(1 for r in rows if r["match"] is False),
            "n_unknown": sum(1 for r in rows if r["match"] is None),
            "shown_to_decided_p50": _median([r["shown_to_decided"] for r in rows])}


def hp_stale_summary(rows: list) -> dict:
    """hp_stale_rows の要約 (純粋): 件数、HP が一度も読めていない (null) 件数、古さの中央値と最大 (側ごと)"""
    out = {"n": len(rows)}
    for side in ("player", "opponent"):
        vals = [r[side] for r in rows if r[side] is not None]
        out[f"{side}_unread"] = sum(1 for r in rows if r[side] is None)
        out[f"{side}_p50"] = _median(vals)
        out[f"{side}_max"] = max(vals) if vals else None
    return out


# ------------------------------------------------------------------ まとめ
def _ratio(a: int, b: int) -> Optional[float]:
    return round(a / b, 3) if b else None


def summarize(records: list, package_sha: Optional[str] = None, late_sec: float = LATE_SEC) -> dict:
    """1 対戦の追跡の要約 (純粋)"""
    ver = version_check(records, package_sha)
    disp = display_rows(records)
    battle = [r for r in disp if r["kind"] == "battle"]
    lat = sorted(r["latency"] for r in battle if r["latency"] is not None)
    feas = feasibility_rows(records)
    return {
        "version": ver,
        "display": {"n_advice": len(battle), "n_displayed": sum(1 for r in battle if r["displayed"]),
                    "display_rate": _ratio(sum(1 for r in battle if r["displayed"]), len(battle)),
                    "latency_p50": (lat[len(lat) // 2] if lat else None), "latency_max": (lat[-1] if lat else None),
                    "n_late": sum(1 for x in lat if x > late_sec), "n_stale": sum(1 for r in battle if r["stale"] is True),
                    "n_stale_unknown": sum(1 for r in battle if r["displayed"] and r["stale"] is None),
                    "n_hidden": sum(1 for r in battle if r.get("hidden"))},
        "feasibility": {"n": len(feas), "n_infeasible_system": sum(1 for r in feas if r["feasible_system"] is False),
                        "n_unknown_system": sum(1 for r in feas if r["feasible_system"] is None),
                        "infeasible_rate_system": _ratio(sum(1 for r in feas if r["feasible_system"] is False),
                                                         sum(1 for r in feas if r["feasible_system"] is not None))},
        "decision": decision_summary(decision_rows(records)),
        "hp_stale": hp_stale_summary(hp_stale_rows(records)),
        "selection": selection_summary(selection_rows(records)),
    }


def summarize_paths(paths: list, package_sha: Optional[str] = None, late_sec: float = LATE_SEC) -> dict:
    """複数の対戦ログの集計: 版の一致 (一致 / 不一致 / 判定不能 の数と version_id の種類)、表示、実行不能"""
    agg = {"n_battles": 0, "version": {"n_match": 0, "n_mismatch": 0, "n_unknown": 0, "version_ids": {}, "fallback_reasons": {}},
           "display": {"n_advice": 0, "n_displayed": 0, "n_late": 0, "n_stale": 0, "n_stale_unknown": 0, "n_hidden": 0, "latencies": []},
           "feasibility": {"n": 0, "n_infeasible_system": 0, "n_unknown_system": 0}}
    dec_rows, hp_rows, sel_rows = [], [], []
    for p in paths:
        recs = load_records(p)
        if not recs:
            continue
        dec_rows += decision_rows(recs)
        hp_rows += hp_stale_rows(recs)
        sel_rows += selection_rows(recs)
        agg["n_battles"] += 1
        s = summarize(recs, package_sha, late_sec)
        v = s["version"]
        key = {True: "n_match", False: "n_mismatch", None: "n_unknown"}[v["match_package"]]
        agg["version"][key] += 1
        if v["version_id"]:
            agg["version"]["version_ids"][v["version_id"]] = agg["version"]["version_ids"].get(v["version_id"], 0) + 1
        if v["fallback_reason"]:
            agg["version"]["fallback_reasons"][v["fallback_reason"]] = agg["version"]["fallback_reasons"].get(v["fallback_reason"], 0) + 1
        for k in ("n_advice", "n_displayed", "n_late", "n_stale", "n_stale_unknown", "n_hidden"):
            agg["display"][k] += s["display"][k]
        agg["display"]["latencies"] += [r["latency"] for r in display_rows(recs) if r["kind"] == "battle" and r["latency"] is not None]
        for k in ("n", "n_infeasible_system", "n_unknown_system"):
            agg["feasibility"][k] += s["feasibility"][k]
    lat = sorted(agg["display"].pop("latencies"))
    agg["display"]["display_rate"] = _ratio(agg["display"]["n_displayed"], agg["display"]["n_advice"])
    agg["display"]["latency_p50"] = lat[len(lat) // 2] if lat else None
    agg["display"]["latency_p90"] = lat[int(len(lat) * 0.9)] if lat else None
    agg["feasibility"]["infeasible_rate_system"] = _ratio(agg["feasibility"]["n_infeasible_system"],
                                                          agg["feasibility"]["n"] - agg["feasibility"]["n_unknown_system"])
    agg["decision"] = decision_summary(dec_rows)
    agg["hp_stale"] = hp_stale_summary(hp_rows)
    agg["selection"] = selection_summary(sel_rows)
    return agg


def chain_rows(records: list) -> list:
    """「どの版が、どの状態を見て、何を推奨し、いつ表示されたか」を助言ごとに 1 行で (純粋)"""
    disp = {r["advice_id"]: r for r in display_rows(records)}
    feas = {r["advice_id"]: r for r in feasibility_rows(records)}
    rows = []
    for d in records:
        if d.get("type") != "advice" or not d.get("advice_id"):
            continue
        adv = d.get("advice") or {}
        best = adv.get("best") or ((adv.get("actions") or [None])[0]) if d.get("kind") == "battle" else None
        rec = (adv.get("recommend") or []) if d.get("kind") == "selection" else []
        dr, fr = disp.get(d["advice_id"], {}), feas.get(d["advice_id"], {})
        rows.append({"advice_id": d["advice_id"], "kind": d.get("kind"), "turn": d.get("turn"), "version_id": d.get("version_id"),
                     "state_id": d.get("state_id"), "policy": d.get("policy"),
                     "recommend": (f"{best.get('kind')}:{best.get('id')}" if best else ("/".join(str(r.get("name")) for r in rec) or None)),
                     "provisional": bool(adv.get("provisional")), "t_gen": adv.get("t_gen") or d.get("t"), "t_shown": dr.get("t_shown"),
                     "latency": dr.get("latency"), "stale": dr.get("stale"), "feasible_system": fr.get("feasible_system")})
    return rows


def format_chain(name: str, records: list, package_sha: Optional[str] = None) -> str:
    s = summarize(records, package_sha)
    v = s["version"]
    lines = [f"[{name}] 版: {v['version_id'] or '(version の行なし)'} 指定 Package {v['designated_package']} / 選出モデル "
             f"{(v['selection'] or {}).get('sha256')} 退避 {v['fallback_reason']} / Package と一致 {v['match_package']}",
             f"  表示: 助言 {s['display']['n_advice']} / 表示 {s['display']['n_displayed']} / 遅延の中央値 {s['display']['latency_p50']} 秒 "
             f"/ 遅い {s['display']['n_late']} / 古い状態への表示 {s['display']['n_stale']} (判定不能 {s['display']['n_stale_unknown']})",
             f"  実行不能 (システムの状態): {s['feasibility']['n_infeasible_system']} / {s['feasibility']['n']} (判定不能 {s['feasibility']['n_unknown_system']})"]
    dc, hs, sl = s["decision"], s["hp_stale"], s["selection"]
    if sl["n_record"]:
        lines.append(f"  選出の記録: 選出の助言 {sl['n_advice']} / selection_record と結べた {sl['n_record']} "
                     f"(相手 6 枠判明の全分布 {sl['n_full_distribution']})")
    if dc["n"]:
        lines.append(f"  決定: {dc['n']} (第一候補と一致 {dc['n_match']} / 不一致 {dc['n_mismatch']} / 不明 {dc['n_unknown']}) "
                     f"表示から決定までの中央値 {dc['shown_to_decided_p50']} 秒")
    if hs["n"]:
        lines.append(f"  HP の古さ (助言時点): 自分 中央値 {hs['player_p50']} 秒 / 未読 {hs['player_unread']}、"
                     f"相手 中央値 {hs['opponent_p50']} 秒 / 未読 {hs['opponent_unread']} (助言 {hs['n']})")
    for r in chain_rows(records):
        if r["provisional"]:
            continue
        lines.append(f"  {r['advice_id']} T{r['turn']} {r['kind']:9s} {str(r['recommend']):28s} 生成 {r['t_gen']} 表示 {r['t_shown']} "
                     f"遅延 {r['latency']} 古い {r['stale']} 選べる {r['feasible_system']} 状態 {r['state_id']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 4. 影の計算 (advice_variant、2026-10-07)
def variant_phase(context: Optional[dict]) -> str:
    """advice_variant の局面の分類 (純粋): 交代先選択 (ひんし・とんぼ後で交代しか選べない) / 受け (相手の KO 圏の攻撃が
    先に来る = threat_faces_me) / 攻め (それ以外) / 不明 (context が無い)"""
    c = context or {}
    if not c:
        return "不明"
    if c.get("switch_only"):
        return "交代先選択"
    if c.get("threat_faces_me"):
        return "受け"
    return "攻め"


def variant_layer(context: Optional[dict]) -> str:
    """残り体数の層 (純粋): "自分の残り v 相手の残り" (読めなければ ?)"""
    c = context or {}
    my_r, opp_r = c.get("my_remaining"), c.get("opp_remaining")
    return f"{my_r if my_r is not None else '?'}v{opp_r if opp_r is not None else '?'}"


def _variant_best(v: dict) -> Optional[str]:
    top = v.get("top") or []
    return f"{top[0].get('kind')}:{top[0].get('id')}" if top else None


def variant_rows(records: list) -> list:
    """advice_variant の行ごとに要約 (純粋)。{advice_id, turn, skipped, skip_reason, base_weight, best (重み → 第一候補),
    changed (基準の重みから第一候補が変わった重みの一覧), phase, layer, base_kind (基準の第一候補の種類), gap (基準の 1-2 位の点差)}"""
    rows = []
    for d in records:
        if d.get("type") != "advice_variant":
            continue
        ctx = d.get("context") or {}
        row = {"advice_id": d.get("advice_id"), "turn": d.get("turn"), "skipped": d.get("skipped"),
               "skip_reason": d.get("skip_reason"), "base_weight": d.get("base_weight"), "best": {}, "changed": [],
               "phase": variant_phase(ctx), "layer": variant_layer(ctx), "base_kind": None, "gap": None}
        if not d.get("skipped"):
            best = {float(v["rl_blend"]): _variant_best(v) for v in (d.get("variants") or []) if "rl_blend" in v}
            row["best"] = best
            base_w = d.get("base_weight")
            base = best.get(float(base_w)) if base_w is not None else None
            row["changed"] = [w for w, k in best.items() if base is not None and k != base]
            bv = next((v for v in (d.get("variants") or []) if base_w is not None and float(v.get("rl_blend", -1)) == float(base_w)), None)
            if bv and bv.get("top"):
                row["base_kind"] = bv["top"][0].get("kind")
                row["gap"] = (bv.get("gaps") or [None])[0]
        rows.append(row)
    return rows


def summarize_variants(records: list) -> dict:
    """影の計算の集計 (純粋): 計算できた件数、第一候補が重みで変わった回数 (重み別・局面別・残り体数別・第一候補の種類別)、
    スキップの内訳 (dropped_pending / aborted_running × 理由)"""
    rows = variant_rows(records)
    done = [r for r in rows if not r["skipped"]]
    out = {"n_rows": len(rows), "n_computed": len(done), "n_changed": sum(1 for r in done if r["changed"]),
           "changed_by_weight": {}, "by_phase": {}, "by_layer": {}, "by_base_kind": {},
           "skipped": {"dropped_pending": {}, "aborted_running": {}}, "examples": []}
    for r in done:
        for w in r["best"]:
            if w == (float(r["base_weight"]) if r["base_weight"] is not None else None):
                continue
            k = f"{w:g}"
            cw = out["changed_by_weight"].setdefault(k, {"n": 0, "changed": 0})
            cw["n"] += 1
            cw["changed"] += int(w in r["changed"])
        for key, val in (("by_phase", r["phase"]), ("by_layer", r["layer"]), ("by_base_kind", r["base_kind"] or "?")):
            b = out[key].setdefault(val, {"n": 0, "changed": 0})
            b["n"] += 1
            b["changed"] += int(bool(r["changed"]))
        if r["changed"] and len(out["examples"]) < 10:
            out["examples"].append({"advice_id": r["advice_id"], "turn": r["turn"], "phase": r["phase"], "layer": r["layer"],
                                    "best": {f"{w:g}": k for w, k in r["best"].items()}})
    for r in rows:
        if r["skipped"]:
            d = out["skipped"].setdefault(r["skipped"], {})
            d[r["skip_reason"] or "?"] = d.get(r["skip_reason"] or "?", 0) + 1
    for k, cw in out["changed_by_weight"].items():
        cw["rate"] = _ratio(cw["changed"], cw["n"])
    return out


def summarize_variants_paths(paths: list) -> dict:
    """複数の対戦ログの advice_variant を合わせて集計する"""
    recs = []
    for p in paths:
        recs += [d for d in load_records(p) if d.get("type") == "advice_variant"]
    s = summarize_variants(recs)
    s["n_battles"] = len(paths)
    return s


def format_variants(s: dict) -> str:
    lines = [f"影の計算 (advice_variant): 行 {s['n_rows']} / 計算 {s['n_computed']} / 第一候補が重みで変わった {s['n_changed']}"]
    for w, cw in sorted(s["changed_by_weight"].items(), key=lambda kv: float(kv[0])):
        lines.append(f"  重み {w}: 第一候補が基準と違う {cw['changed']} / {cw['n']} ({cw['rate']})")
    for key, title in (("by_phase", "局面"), ("by_layer", "残り体数"), ("by_base_kind", "基準の第一候補の種類")):
        parts = " / ".join(f"{k} {v['changed']}/{v['n']}" for k, v in sorted(s[key].items()))
        lines.append(f"  {title}: {parts or '-'}")
    sk = s["skipped"]
    lines.append("  スキップ: 待機中の破棄 " + (", ".join(f"{k} {v}" for k, v in sorted(sk.get('dropped_pending', {}).items())) or "0")
                 + " / 実行中の中止 " + (", ".join(f"{k} {v}" for k, v in sorted(sk.get('aborted_running', {}).items())) or "0"))
    for ex in s["examples"]:
        lines.append(f"  例 {ex['advice_id']} T{ex['turn']} {ex['phase']} {ex['layer']}: {ex['best']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="助言の追跡: 版 / 状態 / 推奨 / 表示を 1 本で")
    ap.add_argument("--battle", default=None)
    ap.add_argument("--last", type=int, default=1)
    ap.add_argument("--package", default=None, help="突き合わせる Package id (既定: version の行の指定 Package)")
    ap.add_argument("--late-sec", type=float, default=LATE_SEC)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--variants", action="store_true",
                    help="影の計算 (advice_variant 行) の集計だけを出す (対象の対戦ログを合わせて)")
    args = ap.parse_args()
    files = [args.battle] if args.battle else sorted(glob.glob(str(BATTLE_DIR / "battle_*.jsonl")))[-args.last:]
    if args.variants:
        s = summarize_variants_paths(files)
        print(json.dumps(s, ensure_ascii=False, indent=1) if args.json else format_variants(s))
        return
    out = []
    for f in files:
        recs = load_records(f)
        pkg = args.package or version_check(recs).get("designated_package")
        sha = package_selection_sha(pkg)
        if args.json:
            out.append({"file": Path(f).name, "summary": summarize(recs, sha, args.late_sec), "chain": chain_rows(recs)})
        else:
            print(format_chain(Path(f).name, recs, sha))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
