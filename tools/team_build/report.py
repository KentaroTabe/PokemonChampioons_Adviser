"""build_report.md (S13、表示専用): 構築記事の形式で成果を説明する。Sonnet が書くが、数値は evaluation から
機械的に埋め込み、LLM の文章は display 扱い (判定に使わない)。LLM が無ければテンプレートだけを出す。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

REPORT_SYSTEM = (
    "あなたはポケモンチャンピオンズの構築記事を書く。入力 (チーム、コンセプト、選出パターン、対面行列の要約、評価結果) の"
    "数値と id だけを根拠に、構築コンセプト → 各個体の採用理由 → 選出パターン → 苦手・課題 → 戦績 の順で日本語の Markdown を書く。"
    "入力に無い数値・技・持ち物を作らない。評価の数値は入力の値をそのまま引用する。authoritative は {\"ok\": true} だけ、"
    "本文は display.markdown に入れる。出力は JSON オブジェクト 1 つ。"
)


def facts_from_run(run_dir: Path, candidate_id: str) -> dict:
    run_dir = Path(run_dir)
    def load(p):
        try:
            return json.loads((run_dir / p).read_text(encoding="utf-8"))
        except Exception:
            return None
    sets = load("s06_sets.json") or []
    team = next((r for r in sets if r.get("candidate_id") == candidate_id), {})
    concepts = load("s04_concepts.json") or {}
    fam = next((f for f in concepts.get("families", []) if team.get("candidate_id", "").endswith(f.get("family_id", "?"))), {})
    summary = load("evaluation/summary.json") or {}
    return {
        "candidate_id": candidate_id, "members": team.get("members"), "sets": team.get("sets"),
        "concept": {k: fam.get(k) for k in ("family_id", "core_ids", "mega_id", "win_condition", "support_roles", "weak_to")},
        "rule_setter": team.get("rule_setter"), "rule_pair": team.get("rule_pair"),
        "holdout": summary.get("holdout"), "ablation": summary.get("ablation"),
        "robustness_worst": summary.get("robustness_worst"),
        "selection_patterns": load("final/selection_patterns.json"),
        "s10": {a["arm_id"]: {"win_rate": a.get("win_rate"), "state": a.get("state"), "delta": (a.get("result") or {}).get("mean")}
                for a in (load("evaluation/s10.json") or {}).get("arms", [])},
    }


def template_report(facts: dict) -> str:
    h = facts.get("holdout") or {}
    lines = [f"# 構築レポート: {facts.get('candidate_id')}", "",
             "## コンセプト", f"- 軸: {facts.get('concept', {}).get('core_ids')} / 勝ち筋: {facts.get('concept', {}).get('win_condition')}",
             "", "## 並び"]
    for st in facts.get("sets") or []:
        lines.append(f"- {st.get('species')} @ {st.get('item')} {st.get('nature')} {st.get('evs')} {st.get('moves')}")
    pair = facts.get("rule_pair")
    if pair:
        lines += ["", "## 規則の対 (設置役 / エース)",
                  f"- 設置役: {pair.get('setter')} / エース: {pair.get('ace')}",
                  f"- 共通の苦手 (両方の被覆が閾値未満): {pair.get('shared_weak')}",
                  f"- 見ている個体: {pair.get('covered_by')}",
                  f"- 未対策: {pair.get('uncovered')} (見ている割合 {pair.get('score')})",
                  f"- エースの止め手 {pair.get('ace_checks')} を設置役が見ている割合: {pair.get('setter_covers_ace_checks')}"]
    lines += ["", "## 評価 (封印 holdout)", f"- verdict: {h.get('verdict')} / ΔWR: {h.get('delta')} / CI: {h.get('ci')} / n: {h.get('n')}",
              f"- ablation: {facts.get('ablation')}", f"- STRESS 最悪感度: {facts.get('robustness_worst')}", "",
              "## 注意", "- 探索時の勝率は期待勝率ではない。採否は holdout の verdict による。"]
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, candidate_id: str, provider=None) -> Path:
    facts = facts_from_run(run_dir, candidate_id)
    md = template_report(facts)
    if provider is not None:
        res = provider.call("s13_report", "sonnet", REPORT_SYSTEM, facts, validator=lambda a: [] if a.get("ok") else ["ok が無い"])
        text = (res.get("display") or {}).get("markdown") if res.get("ok") else None
        if text:
            md = text + "\n\n---\n(機械生成の数値表)\n\n" + md
    out = Path(run_dir) / "final" / "build_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return out
