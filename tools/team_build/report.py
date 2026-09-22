"""build_report.md (S13、表示専用): 構築記事の形式で成果を説明する。Sonnet が書くが、数値は evaluation から
機械的に埋め込み、LLM の文章は display 扱い (判定に使わない)。LLM が無ければテンプレートだけを出す。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

REPORT_SYSTEM = (
    "あなたはポケモンチャンピオンズの構築記事を書く。入力 (チーム、コンセプト、選出パターン、対面行列の要約、評価結果) の"
    "数値と id だけを根拠に、構築コンセプト → 各個体の採用理由 → 選出パターン → 苦手・課題 → 戦績 の順で日本語の Markdown を書く。"
    "finalists (方向性の違う他の最終候補) があれば、末尾に『他の方向性の候補との違い』を短く書く (どれを選ぶかは読者)。"
    "入力に無い数値・技・持ち物を作らない。評価の数値は入力の値を根拠にするが、本文では勝率・差は % か小数 3 桁に丸める。"
    "ポケモン・技・持ち物・特性・性格は入力の ja (日本語名の対応表) の表記で書き、id は括弧で添える。"
    "読者は対戦プレイヤーで、このシステムの内部を知らない: JSON のキー名や内部用語 (family_id, rule_pair, covered_by, "
    "setter, ace, coverage, holdout, ablation, state, se, ci など) を本文にそのまま書かず、日本語で言い換える "
    "(例: setter → 場を作る役、ace → 場で暴れる役、coverage → 脅威をどれだけ見られるかの割合、holdout → 封印した相手列での最終評価、"
    "ablation → チーム・選出・行動に分けた寄与、信頼区間 → 「この範囲に収まる見込み」)。"
    "notes の `rule:X<-Y` は「規則により Y を X に置き換えた」の意味 (例: rule:ace_item<-sitrusberry は持ち物をオボンのみから今の持ち物に替えた)。"
    "holdout.delta と s10 の delta は参照 (今使っている登録チーム) との勝率差で、前回との差ではない。"
    "robustness_worst は条件 (行動の乱れ・選出の乱れ・相手の方策) を変えたときの勝率の落ち幅の最悪値で、優位の下限ではない。"
    "authoritative は {\"ok\": true} だけ、本文は display.markdown に入れる。出力は JSON オブジェクト 1 つ。"
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
    _fill_abilities(team.get("sets") or [], run_dir / "s06_sets" / f"{candidate_id}.txt")
    concepts = load("s04_concepts.json") or {}
    fam = next((f for f in concepts.get("families", []) if team.get("candidate_id", "").endswith(f.get("family_id", "?"))), {})
    summary = load("evaluation/summary.json") or {}
    # 複数の最終候補 (2026-09-17): 自分の順位・holdout は finalists の行から、比較表は全員ぶん
    finalists = summary.get("finalists") or []
    mine = next((f for f in finalists if f.get("candidate_id") == candidate_id), None)
    members_of = {r.get("candidate_id"): list(r.get("members") or []) for r in sets}
    holdout = (mine or {}).get("holdout") if mine and mine.get("holdout") else summary.get("holdout")
    if mine and not mine.get("holdout"):
        holdout = None if mine.get("rank", 1) != 1 else summary.get("holdout")
    return {
        "candidate_id": candidate_id, "members": team.get("members"), "sets": team.get("sets"),
        "concept": {k: fam.get(k) for k in ("family_id", "core_ids", "mega_id", "win_condition", "support_roles", "weak_to",
                                            "archetype", "branch", "switching", "special_branch")},
        "archetype": team.get("archetype"),
        "rule_setter": team.get("rule_setter"), "rule_pair": team.get("rule_pair"),
        "holdout": holdout, "ablation": summary.get("ablation") if (mine is None or mine.get("rank", 1) == 1) else None,
        "robustness_worst": summary.get("robustness_worst") if (mine is None or mine.get("rank", 1) == 1) else None,
        "selection_patterns": load("final/selection_patterns.json") if (mine is None or mine.get("rank", 1) == 1) else None,
        "s10": {a["arm_id"]: {"win_rate": a.get("win_rate"), "state": a.get("state"), "delta": (a.get("result") or {}).get("mean")}
                for a in (load("evaluation/s10.json") or {}).get("arms", [])},
        "finalist_rank": (mine or {}).get("rank"),
        "finalists": [{"rank": f.get("rank"), "candidate_id": f.get("candidate_id"), "direction_ja": f.get("direction_ja"),
                       "delta_s10": f.get("delta_s10"), "holdout": f.get("holdout"),
                       "members": members_of.get(f.get("candidate_id"), [])} for f in finalists],
    }


def _fill_abilities(rows: list, sets_file: Path) -> None:
    """s06_sets.json の型の行には特性が無い (Showdown 本文にだけある) ので、本文から種族→特性を補う (表の特性欄が ? にならないように)"""
    if not sets_file.exists() or all(r.get("ability") for r in rows):
        return
    from advisor.ja_names import parse_showdown_text
    by_species = {r["species"]: r.get("ability") for r in parse_showdown_text(sets_file.read_text(encoding="utf-8"))}
    for r in rows:
        if not r.get("ability") and by_species.get(r.get("species")):
            r["ability"] = by_species[r["species"]]


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
    for f in facts.get("finalists") or []:
        for sid in f.get("members") or []:
            out["species"].setdefault(sid, species_ja(sid))
    return out


def finalists_table(facts: dict) -> str:
    """最終候補 (方向性の違う並び) の比較表。★ = この記事の候補"""
    rows = facts.get("finalists") or []
    if not rows:
        return ""
    def _h(f):
        h = f.get("holdout") or {}
        if not h:
            return "未測定"
        ci = h.get("ci") or [None, None]
        return f"{h.get('verdict')} {_fnum(h.get('delta'))} [{_fnum(ci[0])}, {_fnum(ci[1])}] n={h.get('n')}"
    lines = ["| 順位 | 候補 | 方向性 | S10 Δ | 封印 holdout | メンバー |", "|---|---|---|---|---|---|"]
    for f in rows:
        mark = "★ " if f.get("candidate_id") == facts.get("candidate_id") else ""
        lines.append(f"| {f.get('rank')} | {mark}{f.get('candidate_id')} | {f.get('direction_ja') or '-'} | "
                     f"{_fnum(f.get('delta_s10'))} | {_h(f)} | {_ja(f.get('members'))} |")
    return "\n".join(lines)


def _fnum(x) -> str:
    return f"{x:+.3f}" if isinstance(x, (int, float)) else "?"


def template_report(facts: dict) -> str:
    from advisor.ja_names import team_table_ja
    h = facts.get("holdout") or {}
    concept = facts.get("concept") or {}
    lines = [f"# 構築レポート: {facts.get('candidate_id')}", "",
             "## コンセプト", f"- 軸: {_ja(concept.get('core_ids'))} / 勝ち筋: {concept.get('win_condition')}"]
    if concept.get("archetype"):
        try:
            from tools.team_build.archetypes import SWITCHING_JA, label_ja
            arch = facts.get("archetype") or {}
            roles = arch.get("roles") or {}
            lines.append(f"- 構築の軸: {label_ja(concept.get('archetype'), concept.get('branch'), concept.get('special_branch'))} / 交代方針: "
                         f"{SWITCHING_JA.get(concept.get('switching') or '', concept.get('switching'))} / 役割: "
                         + ", ".join(f"{r}={_ja(v)}" for r, v in roles.items()))
        except Exception:
            pass
    lines += ["", "## 並び", team_table_ja(facts.get("sets") or []), ""]
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
              f"- ablation: {facts.get('ablation')}", f"- STRESS 最悪感度: {facts.get('robustness_worst')}"]
    table = finalists_table(facts)
    if table:
        lines += ["", "## 最終候補 (方向性の違う並び。どれを使うかは読者が選ぶ)", table]
    lines += ["", "## 注意", "- 探索時の勝率は期待勝率ではない。採否は holdout の verdict による。"]
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path, candidate_id: str, provider=None, out_path: Optional[Path] = None, log=None) -> Path:
    """out_path: 書き先 (既定 final/build_report.md。他の最終候補は final/alternatives/<cid>/build_report.md)。
    LLM (provider) を渡したのに本文 (display.markdown) が得られなければ、黙ってテンプレートだけにせず log に書く"""
    facts = facts_from_run(run_dir, candidate_id)
    facts["ja"] = ja_facts(facts)
    md = template_report(facts)
    if provider is not None:
        res = provider.call("s13_report", "sonnet", REPORT_SYSTEM, facts, validator=lambda a: [] if a.get("ok") else ["ok が無い"])
        text = (res.get("display") or {}).get("markdown") if res.get("ok") else None
        if text:
            md = text + "\n\n---\n(機械生成の数値表)\n\n" + md
        else:
            (log or print)(f"[report] {candidate_id}: LLM の本文なし (ok={res.get('ok')} problems={res.get('problems')} "
                           f"record={res.get('record')}) → テンプレートのみ")
    out = Path(out_path) if out_path else Path(run_dir) / "final" / "build_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return out


def main() -> None:
    """run 終了後にレポートだけ作り直す (LLM が使えなかった run の記事化、日本語名の表への差し替え)。
        python -m tools.team_build.report --run-id rule_0913 [--candidate L00_C005] [--llm headless|none]"""
    import argparse
    ap = argparse.ArgumentParser(description="構築レポートの再生成")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--candidate", default=None, help="省略時は final/team.json の勝者")
    ap.add_argument("--llm", choices=["none", "headless"], default="headless")
    args = ap.parse_args()
    run_dir = Path(__file__).resolve().parent.parent.parent / "logs" / "build_search" / "runs" / args.run_id
    cid = args.candidate
    if cid is None:
        cid = json.loads((run_dir / "final" / "team.json").read_text(encoding="utf-8"))["candidate_id"]
    provider = None
    if args.llm == "headless":
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(run_dir / "llm")
    out = write_report(run_dir, cid, provider)
    print(f"[report] {cid} → {out}")


if __name__ == "__main__":
    main()
