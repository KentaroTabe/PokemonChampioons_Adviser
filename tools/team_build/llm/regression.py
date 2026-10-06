"""固定入力での LLM 段の回帰測定 (provider / モデル / effort を変えたときの比較。docs/TEAM_BUILDING_IMPLEMENTATION.md
「モデル更新への耐性」、docs/TECH_WATCH_2026-09.md §A-2/3)。

    python -m tools.team_build.llm.regression --run-id arch_0918 --label opus5                      # 現行設定で S4 を回す
    python -m tools.team_build.llm.regression --run-id arch_0918 --label opus55 --model-opus claude-opus-5-5
    python -m tools.team_build.llm.regression --run-id arch_0918 --label opus55_xhigh --model-opus claude-opus-5-5 --effort xhigh
    python -m tools.team_build.llm.regression --run-id arch_0918 --label run --replay               # run の記録を再生 (費用なし)
    python -m tools.team_build.llm.regression --run-id arch_0918 --label report_low --stage s13 --candidate L69_C029 --effort low
    python -m tools.team_build.llm.regression --run-id arch_0918 --compare                          # 腕の比較表

入力は run の S1〜S3 の成果物 (request.json / species_features.json / s03_archetypes.json / s03_rules.json) をそのまま使い、
S4 (concepts.generate_concepts) だけを指定の provider 設定で回す (run.py の stage_s4 と同じ引数)。出力は
logs/build_search/regression/<run_id>/<label>/ (s04_concepts.json、llm/ の記録、metrics.json)。指標は文章の一致ではなく、
LLM 由来の系統の数と多様性 (core の Jaccard 距離、使った種の数、軸の数)、検証器を 1 回で通る率、所要時間、費用。
S13 (記事) は write_report を同じ run の final に対して回し、本文の有無・長さ・所要時間・費用を見る。
"""
from __future__ import annotations

import argparse
import json
import time
from itertools import combinations
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_ARCHETYPE_LLM_ROUNDS
from tools.team_build import archetypes as ARCH
from tools.team_build import concepts as K
from tools.team_build.features import features_from_json
from tools.team_build.llm.provider import ClaudeCLIProvider, LLMProvider, MockProvider, extract_json
from tools.team_build.run import mega_capable_ids
from tools.team_build.spec import legal_species_ids, load_spec

REPO = Path(__file__).resolve().parent.parent.parent.parent
RUNS_DIR = REPO / "logs" / "build_search" / "runs"
REG_DIR = REPO / "logs" / "build_search" / "regression"
S04_STAGE = "s04_concepts"
S13_STAGE = "s13_report"
DEFAULT_PER_ROUND = 8      # run.py の stage_s4 は generate_concepts の既定 (8) を使う


# ------------------------------------------------------------------ 入力 (run の成果物)
def load_inputs(run_dir: Path) -> dict:
    """run の S1〜S3 の成果物から stage_s4 と同じ引数を組み立てる"""
    run_dir = Path(run_dir)
    spec = load_spec(run_dir / "request.json")
    feats = features_from_json(json.loads((run_dir / "species_features.json").read_text(encoding="utf-8")))
    axes = None
    threats: list = []
    weights: Optional[dict] = None
    p_arch = run_dir / "s03_archetypes.json"
    if p_arch.exists():
        doc = json.loads(p_arch.read_text(encoding="utf-8"))
        axes = ARCH.llm_axes(ARCH.context_from_json(doc))
        info = doc.get("threat_info") or {}
        threats = list(info.keys())
        weights = {t: float((v or {}).get("weight") or 0.0) for t, v in info.items()}
    if not threats:
        p_meta = run_dir / "meta_snapshot.json"
        if p_meta.exists():
            meta = json.loads(p_meta.read_text(encoding="utf-8"))
            threats = [t["id"] if isinstance(t, dict) else t for t in (meta.get("threats") or [])]
    rules = None
    p_rules = run_dir / "s03_rules.json"
    if p_rules.exists():
        rules = json.loads(p_rules.read_text(encoding="utf-8")).get("rules") or None
    return {"spec": spec, "feats": feats, "threats": threats, "threat_weights": weights, "rules": rules,
            "archetypes": axes, "legal": legal_species_ids(), "mega": mega_capable_ids(list(feats))}


def replay_provider(run_dir: Path, stage: str = S04_STAGE, log_dir: Optional[Path] = None) -> MockProvider:
    """run の llm/ 記録 (raw_text) を呼び出し順に返す MockProvider。費用をかけずに同じ段を再生し、基準の指標を出す。
    記録名 <stage>_<tier>_<k>_a<attempt>.json の k が通し番号 (再試行も別番号) なので k 順 = 呼び出し順"""
    recs = []
    for p in sorted((Path(run_dir) / "llm").glob(f"{stage}_*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        parts = p.stem.split("_")
        try:
            k = int(parts[-2])
        except (ValueError, IndexError):
            k = 0
        recs.append((k, p.name, rec))
    recs.sort(key=lambda x: (x[0], x[1]))
    return MockProvider([r.get("raw_text") or "" for _, _, r in recs], log_dir=log_dir)


# ------------------------------------------------------------------ 指標 (純粋関数)
def _core(f: dict) -> frozenset:
    return frozenset(f.get("core_ids") or [])


def _concepts_in_record(rec: dict) -> int:
    parsed = extract_json(rec.get("raw_text") or "")
    if not isinstance(parsed, dict):
        return 0
    auth = parsed.get("authoritative") if isinstance(parsed.get("authoritative"), dict) else parsed
    return len((auth or {}).get("concepts") or [])


def metrics(res: dict, records: list, owned: Optional[set] = None, label: str = "") -> dict:
    """S4 の結果 (generate_concepts の戻り値) と provider の記録から、腕の指標を作る"""
    fams = res.get("families") or []
    llm = [f for f in fams if str(f.get("source") or "").startswith("llm:")]
    cores = [_core(f) for f in llm if f.get("core_ids")]
    dist = []
    for a, b in combinations(cores, 2):
        u = len(a | b)
        dist.append(1.0 - len(a & b) / u if u else 0.0)
    used: set = set().union(*cores) if cores else set()
    recs = [r for r in records if r.get("stage") == S04_STAGE] or list(records)
    rounds = int(res.get("rounds") or 0)
    ok_first = sum(1 for r in recs if (r.get("attempt") or 1) == 1 and not r.get("problems"))
    call_errors = sum(1 for r in recs if r.get("error"))      # CLI / API の失敗 (検証の差し戻しとは別。再試行で救われる)
    n_concepts = sum(_concepts_in_record(r) for r in recs if not r.get("problems"))
    cost = [float(r["cost_usd"]) for r in recs if isinstance(r.get("cost_usd"), (int, float))]
    elapsed = [float(r.get("elapsed_s") or 0.0) for r in recs]
    tokens = {k: sum(int((r.get("usage") or {}).get(k) or 0) for r in recs)
              for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    return {"label": label, "stage": S04_STAGE,
            "model": (recs[0].get("model") if recs else None), "effort": (recs[0].get("effort") if recs else None),
            "rounds": rounds, "stop_reason": res.get("stop_reason"),
            "families_total": len(fams), "families_llm": len(llm), "concepts_returned": n_concepts,
            "core_mean_jaccard_distance": (round(sum(dist) / len(dist), 3) if dist else None),
            "species_used": len(used), "species_used_share": (round(len(used) / len(owned), 3) if owned else None),
            "archetypes_covered": len({f.get("archetype") for f in llm if f.get("archetype")}),
            "calls": len(recs), "first_attempt_ok_rate": (round(ok_first / rounds, 3) if rounds else None),
            "retries": sum(1 for r in recs if (r.get("attempt") or 1) > 1), "call_errors": call_errors,
            "elapsed_total_s": round(sum(elapsed), 1),
            "elapsed_mean_s": (round(sum(elapsed) / len(elapsed), 1) if elapsed else None),
            "cost_usd": (round(sum(cost), 3) if cost else None), "tokens": tokens}


# ------------------------------------------------------------------ 腕の実行
def arm_dir(run_id: str, label: str) -> Path:
    return REG_DIR / run_id / label


def run_s04(run_dir: Path, out_dir: Path, provider: LLMProvider, rounds: Optional[int] = None,
            per_round: int = DEFAULT_PER_ROUND, label: str = "", log=print) -> dict:
    inp = load_inputs(run_dir)
    axes = inp["archetypes"]
    rounds = rounds or (BUILD_ARCHETYPE_LLM_ROUNDS if axes else K.MAX_ROUNDS)
    t0 = time.time()
    res = K.generate_concepts(inp["spec"], inp["feats"], inp["threats"], inp["legal"], inp["mega"], provider=provider,
                              rounds=rounds, per_round=per_round, log=log, threat_weights=inp["threat_weights"],
                              rules=inp["rules"], archetypes=axes)
    res["elapsed_s"] = round(time.time() - t0, 1)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "s04_concepts.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    m = metrics(res, provider.calls, owned=set(inp["spec"].owned), label=label or out_dir.name)
    (out_dir / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return m


def run_s13(run_dir: Path, out_dir: Path, provider: LLMProvider, candidate_id: str, label: str = "", log=print) -> dict:
    from tools.team_build.report import write_report
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    path = write_report(run_dir, candidate_id, provider=provider, out_path=out_dir / "build_report.md", log=log)
    text = path.read_text(encoding="utf-8")
    recs = [r for r in provider.calls if r.get("stage") == S13_STAGE] or list(provider.calls)
    last = recs[-1] if recs else {}
    body = ""
    parsed = extract_json(last.get("raw_text") or "") if last else None
    if isinstance(parsed, dict):
        disp = parsed.get("display")
        body = (disp or {}).get("markdown") or "" if isinstance(disp, dict) else ""
    m = {"label": label or out_dir.name, "stage": S13_STAGE, "model": last.get("model"), "effort": last.get("effort"),
         "ok": bool(recs and not last.get("problems")), "calls": len(recs), "chars": len(text),
         "llm_body_chars": len(body), "elapsed_total_s": round(time.time() - t0, 1),
         "cost_usd": (round(sum(float(r["cost_usd"]) for r in recs if isinstance(r.get("cost_usd"), (int, float))), 3)
                      if any(isinstance(r.get("cost_usd"), (int, float)) for r in recs) else None),
         "tokens": {k: sum(int((r.get("usage") or {}).get(k) or 0) for r in recs)
                    for k in ("cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}}
    (out_dir / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return m


COLUMNS = [("label", "腕"), ("stage", "段"), ("model", "モデル"), ("effort", "effort"), ("rounds", "ラウンド"),
           ("stop_reason", "停止"), ("families_llm", "LLM 系統"), ("families_total", "系統計"), ("concepts_returned", "提案数"),
           ("core_mean_jaccard_distance", "core 距離"), ("species_used", "使用種"), ("archetypes_covered", "軸"),
           ("first_attempt_ok_rate", "1 回通過"), ("retries", "再試行"), ("call_errors", "呼出失敗"), ("llm_body_chars", "本文文字"),
           ("elapsed_mean_s", "平均秒"), ("elapsed_total_s", "合計秒"), ("cost_usd", "費用 $")]


def compare(run_id: str) -> str:
    rows = []
    for p in sorted((REG_DIR / run_id).glob("*/metrics.json")):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    lines = ["| " + " | ".join(h for _, h in COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        lines.append("| " + " | ".join("" if r.get(k) is None else str(r.get(k)) for k, _ in COLUMNS) + " |")
    text = "\n".join(lines)
    (REG_DIR / run_id).mkdir(parents=True, exist_ok=True)
    (REG_DIR / run_id / "compare.md").write_text(text + "\n", encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="固定入力での LLM 段の回帰測定")
    ap.add_argument("--run-id", required=True, help="入力に使う run (logs/build_search/runs/<run_id>)")
    ap.add_argument("--label", default=None, help="腕の名前 (出力先 logs/build_search/regression/<run_id>/<label>)")
    ap.add_argument("--stage", default="s04", choices=["s04", "s13"])
    ap.add_argument("--candidate", default=None, help="s13: 記事を書く候補 id")
    ap.add_argument("--model", default=None, help="この段のモデル id (段ごとの設定 BUILD_LLM_STAGE_MODELS を上書き)")
    ap.add_argument("--model-opus", default=None, help="tier opus のモデル id を上書き (段ごとの設定は外す)")
    ap.add_argument("--model-sonnet", default=None, help="tier sonnet のモデル id を上書き (段ごとの設定は外す)")
    ap.add_argument("--effort", default=None, help="この段の effort (low/medium/high/xhigh/max。none = CLI の既定)")
    ap.add_argument("--rounds", type=int, default=None, help="s04 のラウンド上限 (既定: run と同じ)")
    ap.add_argument("--per-round", type=int, default=DEFAULT_PER_ROUND)
    ap.add_argument("--replay", action="store_true", help="run の記録を再生する (費用なし。基準の腕)")
    ap.add_argument("--compare", action="store_true", help="腕の比較表を出す")
    args = ap.parse_args()
    if args.compare:
        print(compare(args.run_id))
        return
    if not args.label:
        raise SystemExit("--label が必要です")
    run_dir = RUNS_DIR / args.run_id
    out = arm_dir(args.run_id, args.label)
    stage = S04_STAGE if args.stage == "s04" else S13_STAGE
    models = {}
    stage_models = None
    if args.model_opus:
        models["opus"] = args.model_opus
    if args.model_sonnet:
        models["sonnet"] = args.model_sonnet
    if args.model:
        stage_models = {stage: args.model}
    elif models:
        stage_models = {stage: None}          # tier の上書きを効かせる (段ごとの設定は外す)
    effort = None
    if args.effort:
        effort = {stage: (None if args.effort == "none" else args.effort)}
    if args.replay:
        provider: LLMProvider = replay_provider(run_dir, stage, log_dir=out / "llm")
    else:
        provider = ClaudeCLIProvider(out / "llm", models=models, effort=effort, stage_models=stage_models)
    tier = "opus" if stage == S04_STAGE else "sonnet"
    print(f"[regression] run={args.run_id} label={args.label} stage={stage} provider={provider.name} "
          f"model={provider.model_for(stage, tier)} effort={provider.effort.get(stage)}", flush=True)
    if stage == S04_STAGE:
        m = run_s04(run_dir, out, provider, rounds=args.rounds, per_round=args.per_round, label=args.label)
    else:
        if not args.candidate:
            raise SystemExit("--stage s13 には --candidate が必要です")
        m = run_s13(run_dir, out, provider, args.candidate, label=args.label)
    print(json.dumps(m, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
