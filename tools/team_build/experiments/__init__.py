"""単発の検証実験 (2026-10-04 の切り分けの見直しに伴うもの)。run の成果物と実戦ログだけで閉じ、新しい対戦は要らない。

  1. calibration     代理評価の較正の予備試験 (leave-one-run-out)        python -m tools.team_build.experiments.calibration
  2. concept_origin  構想の出所 (LLM / 規則 / 軸) ごとの到達率            python -m tools.team_build.experiments.concept_origin
  3. env_validity    相手プールの妥当性と系統ごとの実戦 vs シム           python -m tools.team_build.experiments.env_validity --run-id R
  4. llm_audit       LLM の常識審査の一致率 (Cohen の κ)                 python -m tools.team_build.experiments.llm_audit --run-id R
  8. learned_surrogate 学習の代理 (選出モデルの予測勝率) と測定の順位相関  python -m tools.team_build.experiments.learned_surrogate
 12. opponent_pick_validity 環境チームの選出の方策と実戦の選出の一致     python -m tools.team_build.experiments.opponent_pick_validity
 13. opponent_pilot_validity 環境チームの操縦 × 選出の方策と実戦の勝率    python -m tools.team_build.experiments.opponent_pilot_validity --run-id R
結果は logs/build_search/experiments/ に JSON で残す。純粋関数は tests/test_team_build_experiments.py で閉じる。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
RUNS = REPO / "logs" / "build_search" / "runs"
OUT = REPO / "logs" / "build_search" / "experiments"


def load_json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_result(name: str, doc: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def best_delta_by_team(res: dict) -> dict:
    """racing の結果 → {team_id: {"delta": variant の最大 Δ, "n": その腕の戦数, "eliminated": 全 variant が脱落か}}"""
    out: dict = {}
    for a in (res or {}).get("arms", []):
        d = (a.get("result") or {}).get("mean")
        if d is None:
            continue
        aid = a.get("arm_id") or ""
        cid = aid.rsplit("@", 1)[0] if "@" in aid else aid
        elim = a.get("eliminated_at") is not None or a.get("state") == "degraded"
        cur = out.get(cid)
        if cur is None or d > cur["delta"]:
            out[cid] = {"delta": float(d), "n": int(a.get("n_done") or 0), "eliminated": elim and (cur["eliminated"] if cur else True)}
        else:
            cur["eliminated"] = cur["eliminated"] and elim
    return out
