"""構築記事 → structured claims (§9-12): 記事は真実ではなく候補生成のヒント。

LLM で自然言語記事から id / 役割 / 対面主張 / 選出パターンだけを取り出し (authoritative)、図鑑・所持で検証したうえで
コンセプトの軸 (source=article) として渡す。記事無しでも pipeline は動く。"""
from __future__ import annotations

from typing import Optional

ARTICLE_SYSTEM = (
    "あなたはポケモンチャンピオンズの構築記事から機械可読の主張だけを抜き出す。authoritative に "
    "{\"claims\": [{\"species_ids\": [showdown id], \"roles\": {id: role}, \"matchups\": [{\"vs\": id, \"result\": \"favorable|unfavorable|even\"}], "
    "\"selection_patterns\": [[id, id, id]]}]} を返す。記事に無いことは書かない。id が分からない種は省く。説明は display。"
    "出力は JSON オブジェクト 1 つ。"
)


def validate_claims(auth: dict, legal: set) -> list:
    problems = []
    claims = auth.get("claims")
    if not isinstance(claims, list):
        return ["claims が配列でない"]
    for i, c in enumerate(claims):
        for sid in c.get("species_ids") or []:
            if legal and sid not in legal:
                problems.append(f"claims[{i}]: {sid} は未知の id")
        for m in c.get("matchups") or []:
            if m.get("result") not in ("favorable", "unfavorable", "even"):
                problems.append(f"claims[{i}]: matchups.result が enum 外")
    return problems


def extract_claims(provider, article_text: str, legal: set, stage: str = "s04_article") -> list:
    res = provider.call(stage, "sonnet", ARTICLE_SYSTEM, {"article": article_text[:12000]},
                        validator=lambda a: validate_claims(a, legal))
    return list(res["authoritative"].get("claims") or []) if res["ok"] else []


def claims_to_cores(claims: list, owned: set, max_cores: int = 6) -> list:
    """記事の主張 → 所持種だけの軸 (2〜3 体)。所持に無い種は落とす (記事は再検証の対象)"""
    out, seen = [], set()
    for c in claims:
        ids = [s for s in (c.get("species_ids") or []) if s in owned]
        for pat in (c.get("selection_patterns") or []):
            have = tuple(sorted(s for s in pat if s in owned))
            if 2 <= len(have) <= 3 and have not in seen:
                seen.add(have)
                out.append({"name": f"article:{'+'.join(have)}", "core_ids": list(have), "mega_id": None,
                            "win_condition": "offense_trade", "support_roles": [], "weak_to": [], "source": "article"})
        if 2 <= len(ids) and tuple(sorted(ids[:3])) not in seen:
            key = tuple(sorted(ids[:3]))
            seen.add(key)
            out.append({"name": f"article:{'+'.join(key)}", "core_ids": list(key), "mega_id": None,
                        "win_condition": "offense_trade", "support_roles": [], "weak_to": [], "source": "article"})
        if len(out) >= max_cores:
            break
    return out
