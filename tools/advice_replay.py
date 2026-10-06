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

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "logs" / "battles"
OUT_DIR = REPO / "logs" / "advice_replay"

# 決定点の間引き間隔 (秒)。同一ターン内の助言更新 (churn) を1決定に潰す
DECISION_GAP_SEC = 8.0


# ------------------------------------------------------------------
# 簡約状態 (battle_logger._compact_state) -> エンジン入力への復元
# ------------------------------------------------------------------
def _mon_from_compact(m: dict, resolver) -> dict:
    hp_raw = m.get("hp_raw") or [None, None]
    moves = []
    for mv in (m.get("moves") or []):
        mid = mv[0] if isinstance(mv, (list, tuple)) else None
        pp = mv[1] if isinstance(mv, (list, tuple)) and len(mv) > 1 else None
        if not mid:
            continue
        ja = None
        try:
            ja = resolver.ja_of("moves", mid)
        except Exception:
            pass
        moves.append({"name_ja": ja or mid, "move_id": mid,
                      "pp": pp, "max_pp": None, "effectiveness": None})
    return {
        "species_id": m.get("species"),
        "species_ja": m.get("ja"),
        "types": m.get("types") or [],
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


def compact_to_engine_state(c: dict, resolver) -> dict:
    """ログの簡約状態をエンジン入力 (evaluateが受ける形) に復元する"""
    def side(sd: dict) -> dict:
        return {
            "active_index": sd.get("active"),
            "remaining": sd.get("remaining"),
            "tailwind": bool(sd.get("tailwind")),
            "hazards": dict(sd.get("hazards") or {
                "stealth_rock": False, "spikes": 0,
                "toxic_spikes": 0, "sticky_web": False}),
            "screens": dict(sd.get("screens") or {
                "reflect": False, "light_screen": False,
                "aurora_veil": False}),
            "party": [_mon_from_compact(m, resolver)
                      for m in (sd.get("party") or [])],
        }
    return {
        "field": dict(c.get("field") or
                      {"weather": None, "terrain": None, "trick_room": False}),
        "mega_used": dict(c.get("mega_used") or
                          {"player": False, "opponent": False}),
        "player": side(c.get("player") or {}),
        "opponent": side(c.get("opponent") or {}),
    }


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
