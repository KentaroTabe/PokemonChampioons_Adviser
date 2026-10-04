"""テーマの検査 (2026-10-05 判断 #3・#4): 指定した条件が測定で実際に効いているかを、6 体の単位でなく**選出の単位**で記録する。

型のある制約ごとに見る:
  エース (spec.ace)              並びに居るか + 測定 (S8b / S10 の対戦記録) での選出率。選出率 < BUILD_THEME_ACE_PICK_MIN なら「満たさない」
  固定枠 (spec.favorites)        並びに居るか + 選出率 (閾値は無い。記録だけ)
  技の指定 (spec.required_moves) その種の型にその技が入っているか + その種の選出率

報告であって門ではない: S8b の後に theme_check_s08b、S10 の後に theme_check を summary に残す。BUILD_THEME_GATE が True のときだけ
S10 の勝者からテーマを満たさない並びを外す (全部外れるなら順位はそのまま none_pass の印だけ)。1 run 記録してから on にする。
純粋関数 (tests/test_theme_check.py)。
"""
from __future__ import annotations

import re
from typing import Optional

from champions_agent.config import BUILD_THEME_ACE_PICK_MIN


def _toid(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def theme_of_spec(spec: dict) -> dict:
    """request.json (BuildSpec.to_dict) → 検査する条件 {"ace", "favorites", "required_moves"}"""
    spec = spec or {}
    ace = _toid(spec.get("ace") or "")
    fav = [_toid(s) for s in (spec.get("favorites") or []) if _toid(s) and _toid(s) != ace]
    req = {_toid(k): [_toid(m) for m in (v or []) if _toid(m)] for k, v in (spec.get("required_moves") or {}).items() if _toid(k)}
    return {"ace": ace, "favorites": fav, "required_moves": {k: v for k, v in req.items() if v}}


def active(theme: dict) -> bool:
    return bool(theme and (theme.get("ace") or theme.get("favorites") or theme.get("required_moves")))


def pick_rates(records: list, species: list) -> dict:
    """対戦記録 (our_selection) から種ごとの選出率 {sid: {"n", "picked", "rate"}} (純粋)。選出が記録されていない試合は数えない"""
    want = [_toid(s) for s in species]
    n = 0
    picked = {s: 0 for s in want}
    for r in records or []:
        sel = {_toid(s) for s in (r.get("our_selection") or [])}
        if not sel:
            continue
        n += 1
        for s in want:
            if s in sel:
                picked[s] += 1
    return {s: {"n": n, "picked": picked[s], "rate": (round(picked[s] / n, 3) if n else None)} for s in want}


def check_team(theme: dict, row: dict, records: list, ace_min: float = BUILD_THEME_ACE_PICK_MIN) -> dict:
    """1 並びの検査 (純粋)。row = s06_sets.json の行 (members / sets)。
    pass: エースの指定があれば「並びに居て選出率 ≥ ace_min」(記録が無ければ None)。指定が無ければ None"""
    members = [_toid(s) for s in (row.get("members") or [])]
    sets_by = {_toid(st.get("species")): st for st in (row.get("sets") or [])}
    want = ([theme["ace"]] if theme.get("ace") else []) + list(theme.get("favorites") or []) + list(theme.get("required_moves") or {})
    rates = pick_rates(records, list(dict.fromkeys(want)))
    out: dict = {"ace": None, "favorites": {}, "required_moves": {}, "pass": None, "reasons": []}
    if theme.get("ace"):
        a = theme["ace"]
        r = rates.get(a) or {}
        in_team = a in members
        ok: Optional[bool]
        if not in_team:
            ok = False
            out["reasons"].append(f"エース {a} が並びに居ない")
        elif r.get("rate") is None:
            ok = None
        else:
            ok = r["rate"] >= ace_min
            if not ok:
                out["reasons"].append(f"エース {a} の選出率 {r['rate']} < {ace_min} (n={r['n']})")
        out["ace"] = {"species": a, "in_team": in_team, "n": r.get("n", 0), "pick_rate": r.get("rate"), "ok": ok}
        out["pass"] = ok
    for f in theme.get("favorites") or []:
        r = rates.get(f) or {}
        out["favorites"][f] = {"in_team": f in members, "n": r.get("n", 0), "pick_rate": r.get("rate")}
        if f not in members:
            out["reasons"].append(f"固定枠 {f} が並びに居ない")
            out["pass"] = False if out["pass"] is not False else out["pass"]
    for sid, mvs in (theme.get("required_moves") or {}).items():
        st = sets_by.get(sid)
        have = {_toid(m) for m in (st.get("moves") or [])} if st else set()
        missing = [m for m in mvs if m not in have]
        r = rates.get(sid) or {}
        out["required_moves"][sid] = {"in_team": sid in members, "present": (sid in members and not missing), "missing": missing,
                                      "n": r.get("n", 0), "pick_rate": r.get("rate")}
        if sid not in members or missing:
            out["reasons"].append(f"技の指定 {sid}:{'/'.join(mvs)} が満たされていない")
            out["pass"] = False if theme.get("ace") or out["pass"] is None else out["pass"]
    return out


def check_run(theme: dict, rows_by: dict, records_by: dict, ace_min: float = BUILD_THEME_ACE_PICK_MIN) -> dict:
    """複数の並びの検査 (純粋)。records_by = {candidate_id: [対戦記録]}"""
    teams = {cid: check_team(theme, rows_by.get(cid) or {}, recs, ace_min) for cid, recs in (records_by or {}).items()}
    verdicts = [t["pass"] for t in teams.values()]
    n_pass = sum(1 for v in verdicts if v is True)
    n_fail = sum(1 for v in verdicts if v is False)
    return {"theme": theme, "threshold": ace_min, "teams": teams, "n_checked": len(teams), "n_pass": n_pass, "n_fail": n_fail,
            "none_pass": bool(teams) and n_pass == 0 and n_fail > 0}


def apply_gate(ranked: list, teams: dict, gate: bool) -> tuple:
    """S10 の順位にテーマの門を掛ける (純粋)。gate が偽なら何もしない。満たさない (pass is False) 並びを外す。
    全部外れるなら順位のまま (呼び出し側が none_pass の印を残す)。戻り値 (順位, 外した並び)"""
    if not gate:
        return list(ranked), []
    failing = [c for c in ranked if (teams.get(c) or {}).get("pass") is False]
    passing = [c for c in ranked if c not in failing]
    if not passing:
        return list(ranked), []
    return passing, failing


def format_line(check: dict) -> str:
    t = check.get("theme") or {}
    parts = []
    for cid, r in (check.get("teams") or {}).items():
        a = r.get("ace") or {}
        parts.append(f"{cid}: ace {a.get('pick_rate')} (n={a.get('n')}) {'OK' if r.get('pass') else ('NG' if r.get('pass') is False else '-')}")
    head = f"テーマの検査 (エース {t.get('ace') or '-'} / 固定枠 {t.get('favorites') or '-'} / 技 {t.get('required_moves') or '-'}、閾値 {check.get('threshold')}): "
    return head + f"満たす {check.get('n_pass')} / 満たさない {check.get('n_fail')} / {check.get('n_checked')}" + (" — " + "; ".join(parts) if parts else "")
