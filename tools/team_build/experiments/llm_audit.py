"""実験 4: LLM の常識審査の一致率 (Cohen の κ)。

run の生成型 (s06_sets.json の source が role: の型) から n 件を抽出し、閉じた選択肢の検査表で LLM に判定させる。
同じ入力を repeats 回 × tier (sonnet / haiku) で採り、繰り返し間と tier 間の κ、カテゴリごとの指摘率、規則の常識フィルタ
(sets.set_sanity) との一致を出す。κ ≥ 0.7 なら判定器として使える、未満なら「規則の提案」だけに使う、という判断の材料。
全呼び出しは logs/build_search/experiments/llm_audit/<ts>/ に保存される (provider の記録)。

  python -m tools.team_build.experiments.llm_audit --run-id ace_lopunny_1003 [--n 100] [--repeats 3] [--tiers sonnet,haiku] [--batch 10]
  python -m tools.team_build.experiments.llm_audit --run-id R --provider mock      # 配線の確認 (呼び出し無し)
"""
from __future__ import annotations

import argparse
import json
import random
import time
from itertools import combinations
from pathlib import Path

from tools.team_build.experiments import OUT, RUNS, load_json, write_result

CATEGORIES = ("nature_move_mismatch", "item_mismatch", "redundant_field_move", "missing_main_type_attack", "ev_misallocation",
              "role_mismatch", "unusable_move_set")
SYSTEM = (
    "あなたはポケモンチャンピオンズ (レベル 50、6 体から 3 体を選出) の構築に詳しい審査員。与えられた型 (特性・持ち物・性格・能力ポイント・技・役割) を"
    "人が見て「変」な点があるかだけ判定する。判定は次の閉じた選択肢に限る: "
    "nature_move_mismatch (性格が下げる側の攻撃技を使っている) / item_mismatch (持ち物が技や役割と合わない: タイプ強化で該当技なし、"
    "カゴのみでねむる無し、攻撃役のねむる + カゴ、こだわり + 変化技、持ち物なし) / redundant_field_move (特性で張れる場を技でも張る) / "
    "missing_main_type_attack (自分のタイプの攻撃技が無い) / ev_misallocation (攻撃役なのに主攻撃に振っていない、受け役なのに素早さに振る等) / "
    "role_mismatch (役割と型が合わない: 速い種の受け、火力の無い攻撃役等) / unusable_move_set (技の組み合わせとして成立しない)。"
    "問題が無ければ flags は空配列。推測や好みは書かない。authoritative に {\"verdicts\": [{\"id\": int, \"flags\": [選択肢...]}]} を返す。"
)
SCHEMA = {"type": "object", "properties": {"authoritative": {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "flags": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}}},
    "required": ["id", "flags"]}}}, "required": ["verdicts"]}}, "required": ["authoritative"]}


# ------------------------------------------------------------------ 純粋関数
def cohen_kappa(a: list, b: list) -> float:
    """2 値の列どうしの Cohen の κ (完全一致で 1、偶然と同じで 0)。両方が全部同じ値なら 1"""
    n = min(len(a), len(b))
    if n == 0:
        return float("nan")
    a, b = [bool(x) for x in a[:n]], [bool(x) for x in b[:n]]
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


def parse_verdicts(auth: dict, ids: list, categories=CATEGORIES) -> dict:
    """{id: set(flags)} (選択肢外の flag は捨てる、無い id は空集合)"""
    out = {i: set() for i in ids}
    for v in (auth or {}).get("verdicts") or []:
        try:
            i = int(v.get("id"))
        except (TypeError, ValueError):
            continue
        if i in out:
            out[i] = {f for f in (v.get("flags") or []) if f in categories}
    return out


def majority(label_sets: list) -> set:
    """繰り返しの判定 (set の列) → 過半数が付けた flag の集合"""
    if not label_sets:
        return set()
    counts: dict = {}
    for s in label_sets:
        for f in s:
            counts[f] = counts.get(f, 0) + 1
    return {f for f, c in counts.items() if c * 2 > len(label_sets)}


def agreement(runs: dict, ids: list, categories=CATEGORIES) -> dict:
    """runs = {label: {id: set(flags)}} → 対ごとの κ (any-flag と カテゴリごと)。戻り値 {"pairs": [...], "mean_kappa_any", "per_category"}"""
    labels = sorted(runs)
    pairs = []
    per_cat: dict = {c: [] for c in categories}
    for x, y in combinations(labels, 2):
        ka = cohen_kappa([bool(runs[x][i]) for i in ids], [bool(runs[y][i]) for i in ids])
        row = {"a": x, "b": y, "kappa_any": ka, "kappa_by_category": {}}
        for c in categories:
            kc = cohen_kappa([c in runs[x][i] for i in ids], [c in runs[y][i] for i in ids])
            row["kappa_by_category"][c] = kc
            per_cat[c].append(kc)
        pairs.append(row)
    ks = [p["kappa_any"] for p in pairs if p["kappa_any"] == p["kappa_any"]]
    return {"pairs": pairs, "mean_kappa_any": round(sum(ks) / len(ks), 4) if ks else None,
            "per_category": {c: (round(sum(v) / len(v), 4) if v else None) for c, v in per_cat.items()}}


def flag_rates(labels: dict, ids: list, categories=CATEGORIES) -> dict:
    n = len(ids) or 1
    return {"any": round(sum(1 for i in ids if labels[i]) / n, 3),
            **{c: round(sum(1 for i in ids if c in labels[i]) / n, 3) for c in categories}}


# ------------------------------------------------------------------ 配線
def generated_sets(run_dir: Path) -> list:
    rows = load_json(run_dir / "s06_sets.json") or []
    out: list = []
    seen: set = set()
    for row in rows:
        for s in row.get("sets") or []:
            if not str(s.get("source") or "").startswith("role:"):
                continue
            key = (s.get("species"), s.get("ability"), s.get("item"), s.get("nature"), s.get("evs"), tuple(s.get("moves") or []))
            if key in seen:
                continue
            seen.add(key)
            out.append({"species": s.get("species"), "ability": s.get("ability"), "item": s.get("item"), "nature": s.get("nature"),
                        "evs": s.get("evs"), "moves": list(s.get("moves") or []), "role": s.get("role") or row.get("roles", {}).get(s.get("species"))})
    return out


def rule_flags(sets: list) -> dict:
    from tools.team_build.sets import SetCandidate, set_sanity
    out = {}
    for i, s in enumerate(sets):
        c = SetCandidate(s["species"], s["ability"], s["item"], s["nature"], s["evs"], list(s["moves"]), "role:x")
        out[i] = set(["rule:" + p for p in set_sanity(c)])
    return out


def audit_once(provider, tier: str, sets: list, batch: int, stage: str) -> dict:
    labels: dict = {}
    ids = list(range(len(sets)))
    for k in range(0, len(ids), batch):
        chunk = ids[k:k + batch]
        payload = {"sets": [dict(sets[i], id=i) for i in chunk], "categories": list(CATEGORIES)}
        res = provider.call(stage, tier, SYSTEM, payload, schema=SCHEMA,
                            validator=lambda a: [] if isinstance(a.get("verdicts"), list) else ["verdicts が配列でない"])
        labels.update(parse_verdicts(res.get("authoritative") or {}, chunk))
    return labels


def main() -> None:
    ap = argparse.ArgumentParser(description="実験 4: LLM の常識審査の一致率 (Cohen の κ)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--tiers", default="sonnet,haiku")
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--provider", choices=["cli", "mock"], default="cli")
    args = ap.parse_args()
    sets = generated_sets(RUNS / args.run_id)
    rng = random.Random(args.seed)
    rng.shuffle(sets)
    sets = sets[:args.n]
    if not sets:
        raise SystemExit("生成型 (source role:) が見つからない")
    log_dir = OUT / "llm_audit" / time.strftime("%Y%m%d_%H%M%S")
    log_dir.mkdir(parents=True, exist_ok=True)
    if args.provider == "mock":
        from tools.team_build.llm.provider import MockProvider
        provider = MockProvider([json.dumps({"authoritative": {"verdicts": [{"id": i, "flags": []} for i in range(len(sets))]}})], log_dir)
    else:
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(log_dir)
    ids = list(range(len(sets)))
    runs: dict = {}
    for tier in [t.strip() for t in args.tiers.split(",") if t.strip()]:
        for r in range(args.repeats):
            label = f"{tier}#{r + 1}"
            runs[label] = audit_once(provider, tier, sets, args.batch, f"audit_{tier}_{r + 1}")
            print(f"[{label}] 指摘率 {flag_rates(runs[label], ids)['any']}")
    tiers = sorted({k.split('#')[0] for k in runs})
    within = {t: agreement({k: v for k, v in runs.items() if k.startswith(t + "#")}, ids) for t in tiers}
    maj = {t: {i: majority([runs[k][i] for k in runs if k.startswith(t + "#")]) for i in ids} for t in tiers}
    between = agreement(maj, ids) if len(tiers) >= 2 else None
    rules = rule_flags(sets)
    vs_rule = {t: cohen_kappa([bool(maj[t][i]) for i in ids], [bool(rules[i]) for i in ids]) for t in tiers}
    result = {"run_id": args.run_id, "n_sets": len(sets), "repeats": args.repeats, "tiers": tiers, "batch": args.batch,
              "within_tier": within, "between_tiers": between, "flag_rates": {k: flag_rates(v, ids) for k, v in runs.items()},
              "majority_flag_rates": {t: flag_rates(maj[t], ids) for t in tiers},
              "rule_flag_rate": round(sum(1 for i in ids if rules[i]) / len(ids), 3), "kappa_majority_vs_rule": vs_rule,
              "sets": [dict(sets[i], id=i, majority={t: sorted(maj[t][i]) for t in tiers}, rule=sorted(rules[i])) for i in ids],
              "log_dir": str(log_dir)}
    p = write_result("llm_audit", result)
    for t in tiers:
        print(f"[{t}] 繰り返し間 κ (any) = {within[t]['mean_kappa_any']}  カテゴリ別 = {within[t]['per_category']}  多数決の指摘率 = {result['majority_flag_rates'][t]['any']}")
    if between:
        print(f"tier 間 κ (多数決どうし) = {between['mean_kappa_any']}")
    print(f"規則の常識フィルタの指摘率 {result['rule_flag_rate']}、多数決との κ = {vs_rule}")
    print(f"保存: {p} (呼び出しの記録: {log_dir})")


if __name__ == "__main__":
    main()
