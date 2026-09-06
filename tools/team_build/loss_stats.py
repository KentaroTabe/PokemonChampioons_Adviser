"""敗因統計 (S9、機械が作る): 対戦記録 (battle_log.jsonl) から構造化した統計を出す。LLM の診断は使わない。

出力: loss_by_opponent_species / loss_by_opponent_family / loss_by_opponent_lead / loss_by_our_lead /
      loss_by_our_selection / ko_source (誰に何で倒されたか) / our_ko (誰が何で倒したか) / unused_members /
      mega_usage_vs_outcome / avg_turns / n
すべて純粋関数 (入力は記録の list)。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional


def _norm_name(s: Optional[str]) -> str:
    """'p2a: Gengar' → 'gengar'"""
    if not s:
        return ""
    t = s.split(":", 1)[-1].strip().lower()
    return "".join(ch for ch in t if ch.isalnum())


def _wl(counter: dict, key, won: bool) -> None:
    c = counter.setdefault(key, [0, 0])
    c[0 if won else 1] += 1


def _rate_table(counter: dict, min_n: int = 1) -> list:
    rows = []
    for k, (w, l) in counter.items():
        n = w + l
        if n >= min_n:
            rows.append({"key": k, "n": n, "wins": w, "losses": l, "loss_rate": round(l / n, 3)})
    # 同点の並びも決定的に (key の辞書順)。set の反復順は hash seed で変わるため呼び出し側も sorted を使う
    return sorted(rows, key=lambda r: (-r["losses"], -r["loss_rate"], str(r["key"])))


def loss_stats(records: list, our_species: Optional[list] = None) -> dict:
    by_opp_sp, by_opp_fam, by_opp_lead, by_our_lead, by_sel = {}, {}, {}, {}, {}
    ko_src, our_ko = Counter(), Counter()
    used = Counter()
    mega = {"used": [0, 0], "unused": [0, 0]}
    turns = []
    n = 0
    for r in records:
        won = bool(r.get("won"))
        n += 1
        turns.append(r.get("turn_count") or 0)
        for sp in sorted(set(r.get("opponent_selection") or [])):
            _wl(by_opp_sp, sp, won)
        _wl(by_opp_fam, r.get("opponent_family_id") or "?", won)
        lead = r.get("lead") or {}
        _wl(by_opp_lead, _norm_name(lead.get("theirs")) or "?", won)
        _wl(by_our_lead, _norm_name(lead.get("ours")) or "?", won)
        sel = tuple(sorted(r.get("our_selection") or []))
        _wl(by_sel, "/".join(sel) if sel else "?", won)
        for sp in sel:
            used[sp] += 1
        role = "p1"
        for ev in r.get("ko_events") or []:
            fainted, by, move = _norm_name(ev.get("fainted")), _norm_name(ev.get("by")), ev.get("move")
            ours = str(ev.get("fainted", "")).startswith(role)
            if ours:
                ko_src[(fainted, by, move)] += 1
            else:
                our_ko[(by, fainted, move)] += 1
        used_mega = any(str(m.get("target", "")).startswith(role) for m in (r.get("mega_usage") or []))
        _wl(mega, "used" if used_mega else "unused", won)
    unused = []
    if our_species:
        unused = [{"species": sp, "selected": used.get(sp, 0), "rate": round(used.get(sp, 0) / max(1, n), 3)}
                  for sp in our_species]
        unused.sort(key=lambda x: x["selected"])
    return {
        "n": n, "wins": sum(1 for r in records if r.get("won")), "avg_turns": round(sum(turns) / max(1, n), 2),
        "loss_by_opponent_species": _rate_table(by_opp_sp),
        "loss_by_opponent_family": _rate_table(by_opp_fam),
        "loss_by_opponent_lead": _rate_table(by_opp_lead),
        "loss_by_our_lead": _rate_table(by_our_lead),
        "loss_by_our_selection": _rate_table(by_sel),
        "ko_source": [{"ours": k[0], "by": k[1], "move": k[2], "n": v} for k, v in ko_src.most_common(15)],
        "our_ko": [{"by": k[0], "theirs": k[1], "move": k[2], "n": v} for k, v in our_ko.most_common(15)],
        "unused_members": unused,
        "mega_usage_vs_outcome": {k: {"wins": v[0], "losses": v[1]} for k, v in mega.items()},
    }


def top_threats(stats: dict, k: int = 5) -> list:
    """負けに最も寄与した相手種 (敗北数順)"""
    return [r["key"] for r in stats.get("loss_by_opponent_species", [])[:k]]
