"""決定再生ハーネス: 認識誤りが推奨をどれだけ変えるかの感度測定。

    python -m tools.advice_replay [--logs N] [--out PATH]

対戦ログの決定時点 (command/move_select の状態) を復元し、認識誤りを
模した摂動を1種類ずつ加えてエンジンを再実行、推奨 (best) の反転率を
フィールド別に測る。「認識が多少違っても提案が正しければよい」(2026-08-31
ユーザー方針) の判定材料で、認識改善の優先順位を提案への影響順に
並べ替えるための道具。

- 基準は「復元状態での再計算」(ログに残った当時の助言ではない)。
  復元の不完全さの影響を摂動効果から分離するため。
- RLブレンドは本番同等 (EMA方策)。決定ごとに同一条件で基準/摂動を比較する。
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path
from typing import Optional

from battle_logger import (MON_V2_EXTRA, MON_V2_FIELDS, SIDE_V2_FIELDS, STATE_SCHEMA_V1, STATE_V2_FIELDS,
                           state_schema_of)

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "logs" / "battles"
OUT_DIR = REPO / "logs" / "advice_replay"

# 決定点の間引き間隔 (秒)。同一ターン内の助言更新 (churn) を1決定に潰す
DECISION_GAP_SEC = 8.0


# ------------------------------------------------------------------
# 簡約状態 (battle_logger の compact_state = 版 2 / _compact_state = 版 1) -> エンジン入力への復元
# ------------------------------------------------------------------
# 版 1 の行で照合できない項目 (real_accept が判定不能に数える。2026-10-19 レビュー: 旧版で照合できないものは判定不能として区別する)
RESTORE_UNKNOWN_LABELS = {
    "restore_v1_hp": "HP の不確かさ・観測時刻 (版 1 の記録に無い。確定値として再生した)",
    "restore_v1_item": "消費済み・失った持ち物 (版 1 の記録に無い)",
    "restore_v1_pending": "推定・対応待ちの印の欠落と帰属 (版 1 は印が付いたときだけ書き、2026-10-09 より前は対応待ちの印も無い。"
                          "候補の枠・初観測時刻は無い)",
    "restore_v1_volatiles": "控えの揮発状態・交代技の後の繰り出し待ち (版 1 は move_locks の場の個体だけ。move_locks の無い行は"
                            "場の個体の技の固定も無い)",
    "restore_v1_move_meta": "技の相性の表示・最大 PP (版 1 の記録に無い)",
    "restore_v2_missing": "版 2 の欄の欠け (記録の失敗)",
}
RESTORE_V1_KEYS = ("restore_v1_hp", "restore_v1_item", "restore_v1_pending", "restore_v1_volatiles", "restore_v1_move_meta")


def _flag(m: dict, key: str, schema: int):
    """推定・対応待ちの印の復元: 版 2 は記録の真偽 (欄が無ければ None)、版 1 は印が付いていれば True、無ければ None (不明。
    版 1 は True のときだけ書き、それより前の行には印そのものが無いので、False と区別できない)"""
    if schema >= 2:
        return bool(m[key]) if key in m and m[key] is not None else None
    return True if m.get(key) else None


def _mon_from_compact(m: dict, resolver, schema: int = STATE_SCHEMA_V1) -> dict:
    hp_raw = m.get("hp_raw") or [None, None]
    meta = m.get("move_meta") if isinstance(m.get("move_meta"), list) else None
    moves = []
    for i, mv in enumerate(m.get("moves") or []):
        mid = mv[0] if isinstance(mv, (list, tuple)) else None
        pp = mv[1] if isinstance(mv, (list, tuple)) and len(mv) > 1 else None
        if not mid:
            continue
        ja = None
        try:
            ja = resolver.ja_of("moves", mid)
        except Exception:
            pass
        mm = meta[i] if meta is not None and i < len(meta) and isinstance(meta[i], (list, tuple)) else None
        moves.append({"name_ja": ja or mid, "move_id": mid, "pp": pp,
                      "max_pp": mm[0] if mm and len(mm) > 0 else None,
                      "effectiveness": mm[1] if mm and len(mm) > 1 else None})
    d = {
        "species_id": m.get("species"),
        "species_ja": m.get("ja"),
        "types": list(m.get("types") or []),
        "hp_percent": m.get("hp"),
        "hp_current": hp_raw[0],
        "hp_max": hp_raw[1],
        "status": m.get("status"),
        "boosts": dict(m.get("boosts") or {}),
        "is_mega": bool(m.get("mega")),
        "item_id": m.get("item"),
        "ability_id": m.get("ability"),
        "moves": moves,
        "revealed_moves": list(m.get("revealed") or []),
        "is_picked": bool(m.get("picked")),
    }
    # 推定・対応待ち・形態の候補 (2026-10-19: 以前は保存しても復元していなかった)
    d["species_guess"] = _flag(m, "guess", schema)
    d["pending"] = _flag(m, "pending", schema)
    cands = m.get("candidates")
    d["species_candidates"] = list(cands) if isinstance(cands, list) else None
    hint = m.get("pending_hint")
    d["pending_hint"] = copy.deepcopy(hint) if hint is not None else None
    # 版 2 の欄 (版 1 でも scene 行の state には HP の出所・観測時刻がある)。記録に無い欄は None のまま (確定値に変換しない)
    for k in MON_V2_FIELDS:
        d[k] = copy.deepcopy(m[k]) if k in m else None
    return d


def compact_to_engine_state(c: dict, resolver, move_locks: dict = None) -> dict:
    """ログの簡約状態をエンジン入力 (evaluateが受ける形) に復元する (battle_logger.compact_state の対。純粋)。

    版 2 (state_schema = 2): 個体・陣営・全体の版 2 の欄をすべて戻す (技の固定・直前の技・ターンも state から)。move_locks は使わない
    (同じ state から書いた同じ値)。記録に無い欄は None のまま (不確かさ不明。確定値に変換しない)。
    版 1 (state_schema の無い行): 版 1 の欄と、推定・対応待ちの印 (付いていれば) を戻す。move_locks (advice 行の欄、
    battle_logger.move_locks_of、2026-10-09 M1a 以降の行だけにある) を渡せば turn / last_move と場の個体の技の固定 (volatiles /
    encore_move / volatile_turns / entry_turn) も戻す。それ以前の行には無いので、技の固定は復元できない (戻した状態では固定なしになる)。
    照合できない項目は restore_unknowns。now は復元しない (呼び出し側が replay_now で入れる)"""
    schema = state_schema_of(c)

    def side(sd: dict) -> dict:
        out_sd = {
            "active_index": sd.get("active"),
            "remaining": sd.get("remaining"),
            "tailwind": bool(sd.get("tailwind")),
            "hazards": dict(sd.get("hazards") or {
                "stealth_rock": False, "spikes": 0,
                "toxic_spikes": 0, "sticky_web": False}),
            "screens": dict(sd.get("screens") or {
                "reflect": False, "light_screen": False,
                "aurora_veil": False}),
            "party": [_mon_from_compact(m, resolver, schema)
                      for m in (sd.get("party") or [])],
        }
        if schema >= 2:
            for k in SIDE_V2_FIELDS:
                if k in sd:
                    out_sd[k] = copy.deepcopy(sd[k])
        return out_sd
    out = {
        "field": dict(c.get("field") or
                      {"weather": None, "terrain": None, "trick_room": False}),
        "mega_used": dict(c.get("mega_used") or
                          {"player": False, "opponent": False}),
        "player": side(c.get("player") or {}),
        "opponent": side(c.get("opponent") or {}),
    }
    if schema >= 2:
        for k in STATE_V2_FIELDS:
            if k in c:
                out[k] = copy.deepcopy(c[k])
        return out
    if move_locks:
        out["turn"] = move_locks.get("turn")
        out["last_move"] = dict(move_locks.get("last_move") or {})
        for sname in ("player", "opponent"):
            row = move_locks.get(sname) or {}
            sd = out[sname]
            idx = sd.get("active_index")
            if row and isinstance(idx, int) and 0 <= idx < len(sd["party"]):
                for k in ("volatiles", "encore_move", "volatile_turns", "entry_turn"):
                    if k in row:
                        sd["party"][idx][k] = copy.deepcopy(row[k])
    return out


def replay_now(row: dict) -> Optional[float]:
    """再生の state.now (純粋): advice 行の t_now (evaluate に渡した now) → 助言の t_gen (生成時刻) → 行の t。どれも無ければ None。
    現在の時計を使うと build_inputs が何日分も古い HP として扱うため (2026-10-19 レビュー)"""
    for v in (row.get("t_now"), (row.get("advice") or {}).get("t_gen"), row.get("t")):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def restore_unknowns(row: dict) -> list:
    """advice 行の状態の復元で照合できない項目 (純粋。RESTORE_UNKNOWN_LABELS の鍵)。版 1 の行は RESTORE_V1_KEYS の全部
    (どの個体でも HP の不確かさ等が記録に無い)。版 2 の行は欄が欠けていれば restore_v2_missing"""
    c = row.get("state") or {}
    if state_schema_of(c) < 2:
        return list(RESTORE_V1_KEYS)
    miss = [k for k in STATE_V2_FIELDS if k not in c]
    for sname in ("player", "opponent"):
        sd = c.get(sname) or {}
        miss += [k for k in SIDE_V2_FIELDS if k not in sd]
        for m in sd.get("party") or []:
            miss += [k for k in MON_V2_FIELDS + MON_V2_EXTRA if k not in m]
    return ["restore_v2_missing"] if miss else []


def move_locks_for_check(row: dict) -> Optional[dict]:
    """real_accept.check_best に渡す技の固定 (純粋): advice 行の move_locks があればそれ。無ければ、版 2 の行は state の場の個体の
    揮発状態と直前の技から作る (固定が無いことも確定。空の欄は書かない battle_logger.move_locks_of と同じ形)、版 1 の行は None
    (照合できない)"""
    if row.get("move_locks") is not None:
        return row["move_locks"]
    c = row.get("state") or {}
    if state_schema_of(c) < 2:
        return None
    out = {"turn": c.get("turn"), "last_move": {k: v for k, v in (c.get("last_move") or {}).items() if v}}
    for sname in ("player", "opponent"):
        sd = c.get(sname) or {}
        idx, party = sd.get("active"), sd.get("party") or []
        m = party[idx] if isinstance(idx, int) and 0 <= idx < len(party) else None
        if not m:
            continue
        r = {k: m.get(k) for k in ("volatiles", "encore_move", "volatile_turns", "entry_turn") if m.get(k) not in (None, [], {})}
        if r:
            out[sname] = r
    return out


def _active(state: dict, side: str):
    sd = state[side]
    idx = sd.get("active_index")
    party = sd.get("party") or []
    if idx is not None and 0 <= idx < len(party):
        return party[idx]
    return None


# ------------------------------------------------------------------
# 摂動 (認識誤りの模擬)。適用不能なら None を返す
# ------------------------------------------------------------------
def p_opp_hp_stale(state):
    """相手アクティブの被弾を見逃した (実際より+25%高く見えている)"""
    mon = _active(state, "opponent")
    if mon is None or mon.get("hp_percent") is None or mon["hp_percent"] > 75:
        return None
    s = copy.deepcopy(state)
    m = _active(s, "opponent")
    m["hp_percent"] = min(100.0, m["hp_percent"] + 25.0)
    m["hp_current"] = None
    return s


def p_my_hp_stale(state):
    """自分アクティブの被弾を見逃した (100%に固着)"""
    mon = _active(state, "player")
    if mon is None or mon.get("hp_percent") is None or mon["hp_percent"] > 85:
        return None
    s = copy.deepcopy(state)
    m = _active(s, "player")
    m["hp_percent"], m["hp_current"] = 100.0, None
    return s


def _revive_one(state, side):
    s = copy.deepcopy(state)
    sd = s[side]
    idx = sd.get("active_index")
    for i, m in enumerate(sd["party"]):
        if i != idx and m.get("status") == "fainted":
            m["status"] = None
            m["hp_percent"], m["hp_current"] = 25.0, None
            return s
    return None


def p_opp_faint_missed(state):
    """相手のひんしを見逃した (死んだ個体が25%で生存表示)"""
    return _revive_one(state, "opponent")


def p_my_faint_missed(state):
    """自分の控えのひんしを見逃した"""
    return _revive_one(state, "player")


def p_boosts_missed(state):
    """両アクティブの能力ランクを取り逃した (全て0扱い)"""
    a, b = _active(state, "player"), _active(state, "opponent")
    if not ((a and any((a.get("boosts") or {}).values()))
            or (b and any((b.get("boosts") or {}).values()))):
        return None
    s = copy.deepcopy(state)
    for side in ("player", "opponent"):
        m = _active(s, side)
        if m:
            m["boosts"] = {}
    return s


def p_opp_item_missed(state):
    """相手アクティブの持ち物が読めていない"""
    mon = _active(state, "opponent")
    if mon is None or not mon.get("item_id"):
        return None
    s = copy.deepcopy(state)
    _active(s, "opponent")["item_id"] = None
    return s


def p_opp_revealed_missed(state):
    """相手アクティブの判明技を取り逃した (全て未判明)"""
    mon = _active(state, "opponent")
    if mon is None or not mon.get("revealed_moves"):
        return None
    s = copy.deepcopy(state)
    _active(s, "opponent")["revealed_moves"] = []
    return s


def p_picks_lost(state):
    """選出フラグを全て失った (未選出提案の混入リスク)"""
    if not any(m.get("is_picked") for m in state["player"]["party"]):
        return None
    s = copy.deepcopy(state)
    for m in s["player"]["party"]:
        m["is_picked"] = False
    return s


PERTURBATIONS = [
    ("相手HP固着(+25%)", p_opp_hp_stale),
    ("自分HP固着(100%)", p_my_hp_stale),
    ("相手ひんし見逃し", p_opp_faint_missed),
    ("自分ひんし見逃し", p_my_faint_missed),
    ("ランク変化取り逃し", p_boosts_missed),
    ("相手持ち物不明", p_opp_item_missed),
    ("相手判明技消失", p_opp_revealed_missed),
    ("選出フラグ消失", p_picks_lost),
]


# ------------------------------------------------------------------
# 決定点の抽出と再生
# ------------------------------------------------------------------
def extract_decisions(path: Path) -> list:
    """command/move_select の状態スナップショットを決定点として間引き抽出"""
    out, last_t = [], -1e18
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") != "scene" or \
                r.get("scene") not in ("command", "move_select"):
            continue
        st = r.get("state")
        if not st or not (st.get("player") or {}).get("party"):
            continue
        t = r.get("t", 0)
        if t - last_t < DECISION_GAP_SEC:
            continue
        last_t = t
        out.append((path.name, t, st))
    return out


def _best_key(advice: dict):
    best = (advice or {}).get("best")
    if not best:
        return None
    return (best.get("kind"), best.get("id"))


def _best_margin(advice: dict) -> float:
    acts = (advice or {}).get("actions") or []
    if len(acts) < 2:
        return 999.0
    return acts[0]["score"] - acts[1]["score"]


def run(paths: list, out_path: Path) -> dict:
    from advisor.engine import evaluate
    from vision.normalize import NameResolver
    resolver = NameResolver()

    decisions = []
    for p in paths:
        decisions += extract_decisions(p)
    print(f"決定点 {len(decisions)}件 ({len(paths)}ログ)")

    stats = {name: {"applicable": 0, "flips": 0, "big_margin_flips": 0,
                    "examples": []}
             for name, _ in PERTURBATIONS}
    baseline_fail = 0
    for fname, t, compact in decisions:
        try:
            base_state = compact_to_engine_state(compact, resolver)
            base_state["now"] = float(t)     # 観測の時刻 (scene 行の t)。現在の時計で HP を古いと扱わない (2026-10-19)
            base = evaluate(base_state, resolver)
        except Exception:
            baseline_fail += 1
            continue
        bkey = _best_key(base)
        if not base.get("ok") or bkey is None:
            baseline_fail += 1
            continue
        margin = _best_margin(base)
        for name, fn in PERTURBATIONS:
            try:
                pert = fn(base_state)
            except Exception:
                pert = None
            if pert is None:
                continue
            try:
                adv = evaluate(pert, resolver)
            except Exception:
                continue
            pkey = _best_key(adv)
            if pkey is None:
                continue
            st = stats[name]
            st["applicable"] += 1
            if pkey != bkey:
                st["flips"] += 1
                if margin > 10.0:
                    st["big_margin_flips"] += 1
                if len(st["examples"]) < 3:
                    st["examples"].append(
                        {"log": fname, "t": t,
                         "base": list(bkey), "pert": list(pkey),
                         "margin": round(margin, 1)})

    print(f"基準再計算 不能/助言なし: {baseline_fail}件")
    print(f"\n{'摂動':<18} {'適用':>4} {'反転':>4} {'反転率':>7} {'高マージン反転':>8}")
    for name, _ in PERTURBATIONS:
        s = stats[name]
        rate = s["flips"] / s["applicable"] if s["applicable"] else 0.0
        print(f"{name:<18} {s['applicable']:>4} {s['flips']:>4} "
              f"{rate:>6.1%} {s['big_margin_flips']:>8}")

    result = {"at": time.strftime("%Y-%m-%d %H:%M"),
              "decisions": len(decisions), "baseline_fail": baseline_fail,
              "stats": stats,
              "logs": [p.name for p in paths]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n保存: {out_path}")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="認識誤りの推奨感度の再生測定")
    ap.add_argument("--logs", type=int, default=12,
                    help="対象にする直近の対戦ログ数")
    ap.add_argument("--out", default=None)
    ap.add_argument("--belief-k", type=int, default=None,
                    help="エンジンの相手型仮説数を上書き (P7比較用: 0=点推定, 8=多世界)")
    args = ap.parse_args()
    if args.belief_k is not None:
        import advisor.engine as _eng
        _eng.BELIEF_K = args.belief_k
        print(f"[replay] BELIEF_K={args.belief_k}")
    paths = sorted(LOG_DIR.glob("battle_*.jsonl"))[-args.logs:]
    out = Path(args.out) if args.out else \
        OUT_DIR / f"replay_{time.strftime('%Y%m%d_%H%M')}.json"
    run(paths, out)


if __name__ == "__main__":
    main()
