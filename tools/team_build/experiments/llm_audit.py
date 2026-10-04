"""実験 4: LLM の常識審査の一致率 (Cohen の κ)。

run の生成型 (s06_sets.json の source が role: の型) から n 件を抽出し、閉じた選択肢の検査表で LLM に判定させる。
同じ入力を repeats 回 × tier (sonnet / haiku) で採り、繰り返し間と tier 間の κ、カテゴリごとの指摘率、規則の常識フィルタ
(sets.set_sanity) との一致を出す。κ ≥ 0.7 なら判定器として使える、未満なら「規則の提案」だけに使う、という判断の材料。
全呼び出しは logs/build_search/experiments/llm_audit/<ts>/ に保存される (provider の記録)。

呼び出しが失敗した束 (利用上限の 429 など) の型は「判定なし」(None) として扱い、指摘率と κ の分母から外す
(2026-10-04: Haiku の 2・3 周目が全部 429 で失敗し、「指摘なし」と数えて κ 0.33・tier 間 κ 0.0 という偽の値が出た)。
--resume <記録のディレクトリ> で、成功した呼び出しは記録から再利用し、失敗した束だけ呼び直す。

  python -m tools.team_build.experiments.llm_audit --run-id ace_lopunny_1003 [--n 100] [--repeats 3] [--tiers sonnet,haiku] [--batch 10]
  python -m tools.team_build.experiments.llm_audit --run-id R --resume logs/build_search/experiments/llm_audit/<ts>   # 失敗した束だけやり直す
  python -m tools.team_build.experiments.llm_audit --run-id R --provider mock      # 配線の確認 (呼び出し無し)
"""
from __future__ import annotations

import argparse
import json
import random
import time
from itertools import combinations
from pathlib import Path
from typing import Optional

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


def majority(label_sets: list):
    """繰り返しの判定 (set の列。判定なしは None) → 過半数が付けた flag の集合。判定が 1 つも無ければ None"""
    if not label_sets:
        return set()
    valid = [s for s in label_sets if s is not None]
    if not valid:
        return None
    counts: dict = {}
    for s in valid:
        for f in s:
            counts[f] = counts.get(f, 0) + 1
    return {f for f, c in counts.items() if c * 2 > len(valid)}


def judged(labels: dict, ids: list) -> list:
    """判定が得られた id (None でない)"""
    return [i for i in ids if labels.get(i) is not None]


def agreement(runs: dict, ids: list, categories=CATEGORIES) -> dict:
    """runs = {label: {id: set(flags) | None}} → 対ごとの κ (any-flag と カテゴリごと)。対の両方で判定が得られた id だけを使い
    (n)、1 つも無い対は κ = None で平均から外す。戻り値 {"pairs": [...], "mean_kappa_any", "per_category"}"""
    labels = sorted(runs)
    pairs = []
    per_cat: dict = {c: [] for c in categories}
    for x, y in combinations(labels, 2):
        ok = [i for i in ids if runs[x].get(i) is not None and runs[y].get(i) is not None]
        row = {"a": x, "b": y, "n": len(ok), "kappa_any": None, "kappa_by_category": {}}
        if ok:
            row["kappa_any"] = cohen_kappa([bool(runs[x][i]) for i in ok], [bool(runs[y][i]) for i in ok])
            for c in categories:
                kc = cohen_kappa([c in runs[x][i] for i in ok], [c in runs[y][i] for i in ok])
                row["kappa_by_category"][c] = kc
                per_cat[c].append(kc)
        pairs.append(row)
    ks = [p["kappa_any"] for p in pairs if p["kappa_any"] is not None and p["kappa_any"] == p["kappa_any"]]
    return {"pairs": pairs, "mean_kappa_any": round(sum(ks) / len(ks), 4) if ks else None,
            "per_category": {c: (round(sum(v) / len(v), 4) if v else None) for c, v in per_cat.items()}}


def flag_rates(labels: dict, ids: list, categories=CATEGORIES) -> dict:
    """判定が得られた型 (n) の中での指摘率。判定なし (None) は分母に入れない"""
    ok = judged(labels, ids)
    n = len(ok) or 1
    return {"n": len(ok), "any": round(sum(1 for i in ok if labels[i]) / n, 3),
            **{c: round(sum(1 for i in ok if c in labels[i]) / n, 3) for c in categories}}


RETRY_MARK = "\n\n前回の出力の問題"      # provider.call が再試行の prompt に足す接尾辞の先頭 (記録の prompt から元の入力を取り出す)


def cached_verdicts(records: list, system: str) -> dict:
    """再開用: 成功した呼び出し記録 (error なし・problems なし・同じ system) → {(stage, 入力の prompt): authoritative}。純粋"""
    from tools.team_build.llm.provider import extract_json, normalize_output
    out: dict = {}
    for rec in records:
        if not isinstance(rec, dict) or rec.get("error") or rec.get("problems") or rec.get("system") != system:
            continue
        auth, _disp = normalize_output(extract_json(rec.get("raw_text") or ""))
        if isinstance(auth, dict) and isinstance(auth.get("verdicts"), list):
            out[(rec.get("stage"), str(rec.get("prompt") or "").split(RETRY_MARK, 1)[0])] = auth
    return out


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


def audit_once(provider, tier: str, sets: list, batch: int, stage: str, cache: Optional[dict] = None) -> dict:
    """{id: set(flags) | None}。呼び出しが失敗した束 (再試行しても ok にならない) の型は None = 判定なし (「指摘なし」と区別する)。
    cache (cached_verdicts の戻り値) に同じ段・同じ入力の成功した記録があれば、呼び出さずにそれを使う"""
    labels: dict = {}
    ids = list(range(len(sets)))
    for k in range(0, len(ids), batch):
        chunk = ids[k:k + batch]
        payload = {"sets": [dict(sets[i], id=i) for i in chunk], "categories": list(CATEGORIES)}
        hit = (cache or {}).get((stage, json.dumps(payload, ensure_ascii=False, indent=1)))   # provider.call の prompt と同じ直列化
        if hit is not None:
            labels.update(parse_verdicts(hit, chunk))
            continue
        res = provider.call(stage, tier, SYSTEM, payload, schema=SCHEMA,
                            validator=lambda a: [] if isinstance(a.get("verdicts"), list) else ["verdicts が配列でない"])
        if not res.get("ok"):
            labels.update({i: None for i in chunk})
            continue
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
    ap.add_argument("--resume", default=None,
                    help="前回の呼び出し記録のディレクトリ。成功した呼び出しは記録から再利用し、失敗した束だけ呼び直す (同じ run / n / seed / batch で)")
    args = ap.parse_args()
    sets = generated_sets(RUNS / args.run_id)
    rng = random.Random(args.seed)
    rng.shuffle(sets)
    sets = sets[:args.n]
    if not sets:
        raise SystemExit("生成型 (source role:) が見つからない")
    log_dir = Path(args.resume) if args.resume else OUT / "llm_audit" / time.strftime("%Y%m%d_%H%M%S")
    if args.resume and not log_dir.is_dir():
        raise SystemExit(f"--resume のディレクトリが無い: {log_dir}")
    log_dir.mkdir(parents=True, exist_ok=True)
    cache = cached_verdicts([load_json(p) for p in sorted(log_dir.glob("*.json"))], SYSTEM) if args.resume else {}
    if args.resume:
        print(f"再開: {log_dir} の成功した呼び出し {len(cache)} 件を再利用する")
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
            runs[label] = audit_once(provider, tier, sets, args.batch, f"audit_{tier}_{r + 1}", cache)
            fr = flag_rates(runs[label], ids)
            print(f"[{label}] 指摘率 {fr['any']} (判定が得られた型 {fr['n']} / {len(ids)})")
    tiers = sorted({k.split('#')[0] for k in runs})
    within = {t: agreement({k: v for k, v in runs.items() if k.startswith(t + "#")}, ids) for t in tiers}
    maj = {t: {i: majority([runs[k][i] for k in runs if k.startswith(t + "#")]) for i in ids} for t in tiers}
    between = agreement(maj, ids) if len(tiers) >= 2 else None
    rules = rule_flags(sets)
    vs_rule = {}
    for t in tiers:
        ok = judged(maj[t], ids)
        vs_rule[t] = cohen_kappa([bool(maj[t][i]) for i in ok], [bool(rules[i]) for i in ok]) if ok else None
    n_failed = {k: len(ids) - len(judged(v, ids)) for k, v in runs.items()}
    result = {"run_id": args.run_id, "n_sets": len(sets), "repeats": args.repeats, "tiers": tiers, "batch": args.batch,
              "within_tier": within, "between_tiers": between, "flag_rates": {k: flag_rates(v, ids) for k, v in runs.items()},
              "n_failed": n_failed, "majority_flag_rates": {t: flag_rates(maj[t], ids) for t in tiers},
              "rule_flag_rate": round(sum(1 for i in ids if rules[i]) / len(ids), 3), "kappa_majority_vs_rule": vs_rule,
              "sets": [dict(sets[i], id=i, majority={t: (None if maj[t][i] is None else sorted(maj[t][i])) for t in tiers},
                            rule=sorted(rules[i])) for i in ids],
              "log_dir": str(log_dir)}
    p = write_result("llm_audit", result)
    for t in tiers:
        print(f"[{t}] 繰り返し間 κ (any) = {within[t]['mean_kappa_any']}  カテゴリ別 = {within[t]['per_category']}  多数決の指摘率 = {result['majority_flag_rates'][t]['any']}")
    if between:
        print(f"tier 間 κ (多数決どうし) = {between['mean_kappa_any']}")
    print(f"規則の常識フィルタの指摘率 {result['rule_flag_rate']}、多数決との κ = {vs_rule}")
    if any(n_failed.values()):
        print(f"判定が得られなかった型がある (呼び出しの失敗): { {k: v for k, v in n_failed.items() if v} } "
              f"→ --resume {log_dir} で失敗した束だけやり直せる")
    print(f"保存: {p} (呼び出しの記録: {log_dir})")


if __name__ == "__main__":
    main()
