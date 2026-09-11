"""build_report.md (S13、表示専用): 構築記事の形式で成果を説明する。Sonnet が書くが、数値は evaluation から
機械的に埋め込み、LLM の文章は display 扱い (判定に使わない)。LLM が無ければテンプレートだけを出す。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

REPORT_SYSTEM = (
    "あなたはポケモンチャンピオンズの構築記事を書く。入力 (チーム、コンセプト、選出パターン、対面行列の要約、評価結果) の"
    "数値と id だけを根拠に、構築コンセプト → 各個体の採用理由 → 選出パターン → 苦手・課題 → 戦績 の順で日本語の Markdown を書く。"
    "入力に無い数値・技・持ち物を作らない。評価の数値は入力の値をそのまま引用する。ポケモン・技・持ち物・特性・性格は "
    "入力の ja (日本語名の対応表) の表記で書き、id は括弧で添える。authoritative は {\"ok\": true} だけ、"
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


def _ja(sid) -> str:
    """種族 id → 「日本語 (id)」。表示は jp_names の逆引き (手書きの翻訳をしない)"""
    from advisor.ja_names import species_ja
    if isinstance(sid, (list, tuple, set)):
        return ", ".join(_ja(s) for s in sid)
    if isinstance(sid, dict):
        return ", ".join(f"{_ja(k)} → {_ja(v)}" for k, v in sid.items())
    if not sid:
        return str(sid)
    ja = species_ja(sid)
    return f"{ja} ({sid})" if ja != sid else str(sid)


def ja_facts(facts: dict) -> dict:
    """LLM に渡す日本語名の対応表 (チームの種族・技・持ち物・特性・性格だけ)"""
    from advisor.ja_names import ability_ja, item_ja, move_ja, nature_ja, species_ja
    out: dict = {"species": {}, "moves": {}, "items": {}, "abilities": {}, "natures": {}}
    for st in facts.get("sets") or []:
        sid = st.get("species") or ""
        out["species"][sid] = species_ja(sid)
        for m in st.get("moves") or []:
            out["moves"][m] = move_ja(m)
        if st.get("item"):
            out["items"][st["item"]] = item_ja(st["item"])
        if st.get("ability"):
            out["abilities"][st["ability"]] = ability_ja(st["ability"])
        if st.get("nature"):
            out["natures"][st["nature"]] = nature_ja(st["nature"])
    pair = facts.get("rule_pair") or {}
    concept = facts.get("concept") or {}
    for sid in list(pair.get("shared_weak") or []) + list(pair.get("ace_checks") or []) + list(concept.get("weak_to") or []) \
            + list((pair.get("covered_by") or {}).values()) + list(concept.get("core_ids") or []):
        out["species"].setdefault(sid, species_ja(sid))
    return out


def template_report(facts: dict) -> str:
    from advisor.ja_names import team_table_ja
    h = facts.get("holdout") or {}
    concept = facts.get("concept") or {}
    lines = [f"# 構築レポート: {facts.get('candidate_id')}", "",
             "## コンセプト", f"- 軸: {_ja(concept.get('core_ids'))} / 勝ち筋: {concept.get('win_condition')}",
             "", "## 並び", team_table_ja(facts.get("sets") or []), ""]
    for st in facts.get("sets") or []:
        if st.get("notes"):
            lines.append(f"- {_ja(st.get('species'))}: {st.get('notes')}")
    pair = facts.get("rule_pair")
    if pair:
        lines += ["", "## 規則の対 (設置役 / エース)",
                  f"- 設置役: {_ja(pair.get('setter'))} / エース: {_ja(pair.get('ace'))}",
                  f"- 共通の苦手 (両方の被覆が閾値未満): {_ja(pair.get('shared_weak'))}",
                  f"- 見ている個体: {_ja(pair.get('covered_by'))}",
                  f"- 未対策: {_ja(pair.get('uncovered'))} (見ている割合 {pair.get('score')})",
                  f"- エースの止め手 {_ja(pair.get('ace_checks'))} を設置役が見ている割合: {pair.get('setter_covers_ace_checks')}"]
    lines += ["", "## 評価 (封印 holdout)", f"- verdict: {h.get('verdict')} / ΔWR: {h.get('delta')} / CI: {h.get('ci')} / n: {h.get('n')}",
              f"- ablation: {facts.get('ablation')}", f"- STRESS 最悪感度: {facts.get('robustness_worst')}", "",
              "## 注意", "- 探索時の勝率は期待勝率ではない。採否は holdout の verdict による。"]
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, candidate_id: str, provider=None) -> Path:
    facts = facts_from_run(run_dir, candidate_id)
    facts["ja"] = ja_facts(facts)
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
