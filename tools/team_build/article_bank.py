"""記事バンク (docs/ARTICLE_BANK_DESIGN_1006.md §3.5〜§5, §3.7): 解析結果 → 本文を含まない記録 → 検査 → バンクの保存・読み出し。

- build_record: article_parse.parse_article の結果 + 出典・メタ → case の記録 (unresolved_names / site_id_observations は入れない)。
  記録の種類 record_kind = team (6 体) / single_set (単体の型 1 体)。出典は 2 軸 (publisher_kind = 誰が掲載したか /
  usage_evidence = 使用実績の根拠) と合成の印 synthetic を別項目で持つ
- validate_record: 個体の数 (種類ごと)・4 技・参戦種・配分の整合 (ポイント合計 / 252 表示との対応 / 実数値の再計算) ・メガ形態と石 ・
  選出規則の個体 ・出典の 2 軸
- usable_for: 用途 (pool / weakness / selection / parser_eval) ごとに記録を使えるか (合成・処理状態・規制・種類・使用実績の根拠)
- set_regulation: 規制を更新し、根拠 (regulation_basis) と履歴 (regulation_history) を残す
- assert_no_prose: 記録や LLM の入力に かな・漢字を含む文字列が無いこと (本文・引用の混入の門)
- llm_payload: LLM に渡す部分集合 (個体の型、主張、選出規則、未確定項目の分類と参照 id)
- host_allowed: ホストごとの可否 (fetch / send_llm が allow のときだけ通す。unknown は進めない)
- save_bank / load_bank: logs/articles/bank/<version>/cases.jsonl + manifest.json (version = 内容のハッシュ)。合成の記録は
  保存・読み出しの両方で既定で拒否する (allow_synthetic=True のときだけ。一時ディレクトリへの保存でも許可を別に要る)
純粋関数 (save/load 以外はファイルに触れない)。テストは tests/test_article_parse.py / tests/test_article_bank.py。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_ARTICLE_EV252_OFFSET, BUILD_ARTICLE_EV252_PER_POINT,
                                    BUILD_ARTICLE_MOVES_PER_SET, BUILD_ARTICLE_POOL_EVIDENCE, BUILD_ARTICLE_PUBLISHER_KINDS,
                                    BUILD_ARTICLE_PURPOSE_RECORD_KINDS, BUILD_ARTICLE_RECORD_KIND_MEMBERS,
                                    BUILD_ARTICLE_REGULATION_BASES, BUILD_ARTICLE_REGULATION_NAMES, BUILD_ARTICLE_SEASON_REGULATION,
                                    BUILD_ARTICLE_USAGE_EVIDENCE, BUILD_GEN_EV_POINT_CAP, BUILD_GEN_POINT_BUDGET)
from tools.team_build.article_parse import PARSER_VERSION, STAT_ORDER, mega_table

# 2: record_kind、出典の 2 軸 (publisher_kind / usage_evidence) と synthetic、meta の regulation_basis / regulation_history (2026-10-06)
SCHEMA_VERSION = "article_case/2"
REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_BANK_DIR = REPO / "logs" / "articles" / "bank"
_PROSE_RE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
PAYLOAD_MEMBER_KEYS = ("id", "species_id", "base_species_id", "mega_stone", "item", "nature", "ability", "points", "ev252", "actual", "moves")
SOURCE_AXES = (("publisher_kind", BUILD_ARTICLE_PUBLISHER_KINDS), ("usage_evidence", BUILD_ARTICLE_USAGE_EVIDENCE))
USABLE_STATUSES = ("ok", "warnings")          # 用途 (parser_eval 以外) に使える処理状態
UNKNOWN_REGULATION = "unknown"


def normalize_source(source: Optional[dict]) -> dict:
    """出典の検査と既定値: publisher_kind / usage_evidence は無指定 (None) → "unknown"、表に無い値は ValueError。
    synthetic (合成の記事) は bool で既定 False。他の項目はそのまま (本文を入れないことは呼び出し側と assert_no_prose で守る)"""
    src = dict(source or {})
    for key, table in SOURCE_AXES:
        v = src.get(key)
        if v is None:
            src[key] = "unknown"
        elif v not in table:
            raise ValueError(f"source.{key} が表に無い値 (許可: {', '.join(table)})")
    syn = src.get("synthetic", False)
    if not isinstance(syn, bool):
        raise ValueError("source.synthetic は bool にする")
    src["synthetic"] = syn
    return src


def record_status(members: list, warnings: list, record_kind: str) -> str:
    """処理状態: 個体が無い → failed、種類の個体数に満たない・技一覧の無い個体 → incomplete、警告 → warnings、それ以外 → ok"""
    expected = BUILD_ARTICLE_RECORD_KIND_MEMBERS[record_kind]
    if not members:
        return "failed"
    if len(members) < expected or any(not m["moves"] for m in members):
        return "incomplete"
    if warnings or any(m["warnings"] for m in members):
        return "warnings"
    return "ok"


def build_record(parsed: dict, source: dict, meta: Optional[dict] = None, case_id: Optional[str] = None,
                 record_kind: str = "team") -> dict:
    """解析結果 → case の記録。本文・文・解決できなかった名前・サイト固有 id の観測は入れない (ローカル用で別扱い)。
    record_kind: "team" (6 体と各 4 技で ok) / "single_set" (1 体と 4 技で ok)。source は normalize_source で検査する"""
    if record_kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
        raise ValueError(f"record_kind が表に無い値 (許可: {', '.join(BUILD_ARTICLE_RECORD_KIND_MEMBERS)})")
    src = normalize_source(source)
    members = [{k: v for k, v in m.items() if k != "display"} for m in parsed.get("members", [])]
    body = {"members": members, "claims": parsed.get("claims", []), "selection_rules": parsed.get("selection_rules", []),
            "selection_combinable": parsed.get("selection_combinable"), "unresolved": parsed.get("unresolved", [])}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    warnings = list(parsed.get("warnings", []))
    if len(members) > BUILD_ARTICLE_RECORD_KIND_MEMBERS[record_kind]:
        warnings.append("members_exceed_kind")
    return {"case_id": case_id or f"case_{digest}", "record_kind": record_kind, "source": src, "meta": dict(meta or {}), **body,
            "counts": dict(parsed.get("counts", {})), "warnings": warnings,
            "versions": {"parser": parsed.get("parser_version", PARSER_VERSION), "dictionary": parsed.get("dictionary_version"),
                         "schema": SCHEMA_VERSION}, "status": record_status(members, warnings, record_kind)}


def _expected_ev252(points: int) -> int:
    return BUILD_ARTICLE_EV252_PER_POINT * points - BUILD_ARTICLE_EV252_OFFSET if points > 0 else 0


def expected_actual(species_id: str, points: Optional[dict], nature: Optional[str], dex=None) -> Optional[list]:
    """能力ポイントと性格から実数値 (Lv50、個体値 31) を再計算する。種族値が無ければ None。記事の実数値との整合の検査にだけ使う"""
    from advisor.dex import calc_hp, calc_stat, get_dex
    from advisor.my_team import nature_multipliers
    sp = (dex or get_dex()).species(species_id)
    if not sp or not sp.get("baseStats"):
        return None
    mult = nature_multipliers(nature or "") or {}
    pts = points or {}
    out = []
    for k in STAT_ORDER:
        ev = BUILD_ARTICLE_EV252_PER_POINT * int(pts.get(k, 0) or 0)       # calc_stat は努力値 (ev // 4 を Lv50 で半分にする) を取る
        base = int(sp["baseStats"][k])
        out.append(calc_hp(base, ev) if k == "hp" else calc_stat(base, ev, mult.get(k, 1.0)))
    return out


def validate_record(record: dict, legal_ids: Optional[set] = None, dex=None) -> list:
    """記録の検査 → 問題の列 (空なら OK)。警告の文言は id と数値だけ (本文を含まない)。
    個体の数の期待値は record_kind ごと (team = 6、single_set = 1。record_kind の無い古い記録は team)、基本種の重複は team だけ見る"""
    problems: list = []
    members = record.get("members", [])
    kind = record.get("record_kind", "team")
    if kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
        problems.append("record_kind_unknown")
        kind = "team"
    if len(members) != BUILD_ARTICLE_RECORD_KIND_MEMBERS[kind]:
        problems.append(f"members:{len(members)}")
    if kind == "team":
        bases = [m.get("base_species_id") for m in members]
        if len(set(bases)) != len(bases):
            problems.append("duplicate_base_species")
    src = record.get("source") or {}
    for key, table in SOURCE_AXES:
        if src.get(key, "unknown") not in table:
            problems.append(f"source_{key}_unknown_value")
    if not isinstance(src.get("synthetic", False), bool):
        problems.append("source_synthetic_not_bool")
    mega = mega_table()
    for m in members:
        mid = m.get("id")
        if legal_ids and m.get("species_id") not in legal_ids and m.get("base_species_id") not in legal_ids:
            problems.append(f"{mid}:species_not_legal:{m.get('species_id')}")
        if len(m.get("moves") or []) != BUILD_ARTICLE_MOVES_PER_SET:
            problems.append(f"{mid}:moves:{len(m.get('moves') or [])}")
        if len(set(m.get("moves") or [])) != len(m.get("moves") or []):
            problems.append(f"{mid}:duplicate_move")
        pts = m.get("points")
        if pts:
            if any(v > BUILD_GEN_EV_POINT_CAP for v in pts.values()):
                problems.append(f"{mid}:point_cap")
            if sum(pts.values()) != BUILD_GEN_POINT_BUDGET:
                problems.append(f"{mid}:point_total:{sum(pts.values())}")
        ev = m.get("ev252")
        if pts and ev:
            for k in STAT_ORDER:
                if _expected_ev252(int(pts.get(k, 0) or 0)) != int(ev.get(k, 0) or 0):
                    problems.append(f"{mid}:ev252_mismatch:{k}")
        act = m.get("actual")
        if act is not None and len(act) != len(STAT_ORDER):
            problems.append(f"{mid}:actual_len:{len(act)}")
        elif act and pts:
            exp = expected_actual(m.get("species_id"), pts, m.get("nature"), dex)
            if exp and exp != list(act):
                problems.append(f"{mid}:actual_mismatch:" + ",".join(k for k, a, e in zip(STAT_ORDER, act, exp) if a != e))
        sid = m.get("species_id")
        if sid in mega["forms"]:
            _base, stone = mega["forms"][sid]
            if stone and m.get("item") != stone:
                problems.append(f"{mid}:mega_item:{m.get('item')}")
        elif m.get("item") in mega["stones"] and mega["stones"][m["item"]] != sid:
            problems.append(f"{mid}:stone_without_form")
    ids = {m.get("id") for m in members}
    for i, r in enumerate(record.get("selection_rules", [])):
        if not set(r.get("selected_members") or []) <= ids:
            problems.append(f"rule{i}:members_outside_team")
        if r.get("lead") is not None and r["lead"] not in (r.get("selected_members") or []):
            problems.append(f"rule{i}:lead_outside_selection")
        if any(k in r for k in ("probability", "prob", "weight")):
            problems.append(f"rule{i}:probability_not_allowed")
    for i, c in enumerate(record.get("claims", [])):
        if c.get("subject") != "team" and c.get("subject") not in ids:
            problems.append(f"claim{i}:subject_unknown")
        if c.get("basis") != "author_explicit":
            problems.append(f"claim{i}:basis:{c.get('basis')}")
    return problems


def usable_for(record: dict, purpose: str, regulation: Optional[str] = None, policy: Optional[dict] = None) -> bool:
    """記録を用途に使えるか (2026-10-06 ユーザー判断)。purpose ∈ BUILD_ARTICLE_PURPOSE_RECORD_KINDS ("pool" / "weakness" /
    "selection" / "parser_eval")。
    - parser_eval (解析器の評価) は常に True (合成の記事も可)
    - それ以外: 合成 (source.synthetic) は False、status が ok / warnings 以外は False、meta.regulation が None / "unknown" か
      引数の regulation と違えば False (regulation を指定しなければ一致しないので False。規制を問わずに使う用途は作らない)
    - pool (相手プール本体): team だけ、かつ usage_evidence が BUILD_ARTICLE_POOL_EVIDENCE (自己申告 / 対戦記録で確認) に入ること。
      初版は編集部の推奨をプール本体に入れないが、判定は publisher_kind ではなく使用実績の根拠で行う
    - selection (選出予測): team だけ。weakness (似た構築の弱点): team / single_set
    - policy (host_policy) を渡せば、出典ホストの purposes の制限 (host_allowed) も見る (ホスト全体の allow で全用途に通さない)"""
    kinds = BUILD_ARTICLE_PURPOSE_RECORD_KINDS.get(purpose)
    if kinds is None:
        raise ValueError(f"purpose が表に無い値 (許可: {', '.join(BUILD_ARTICLE_PURPOSE_RECORD_KINDS)})")
    if purpose == "parser_eval":
        return True
    src = record.get("source") or {}
    if src.get("synthetic"):
        return False
    if policy is not None and not host_allowed(policy, src.get("host"), purpose):
        return False
    if record.get("status") not in USABLE_STATUSES:
        return False
    reg = (record.get("meta") or {}).get("regulation")
    if reg in (None, UNKNOWN_REGULATION) or reg != regulation:
        return False
    if record.get("record_kind", "team") not in kinds:
        return False
    if purpose == "pool" and src.get("usage_evidence", "unknown") not in BUILD_ARTICLE_POOL_EVIDENCE:
        return False
    return True


def known_regulations() -> set:
    """規制 id として受け付ける値 (記事の規制名の表と、シーズン → 規制の表の値)"""
    return set(BUILD_ARTICLE_REGULATION_NAMES.values()) | set(BUILD_ARTICLE_SEASON_REGULATION.values())


def set_regulation(record: dict, regulation: Optional[str], basis: str, at: Optional[str] = None) -> dict:
    """meta.regulation を更新した新しい記録 (元の記録は変えない)。meta.regulation_basis (BUILD_ARTICLE_REGULATION_BASES の列挙値) と
    meta.regulation_history ([{"from", "to", "basis"(, "at")}]。本文は入れない) を残す。regulation は既知の規制 id / "unknown" / None"""
    if basis not in BUILD_ARTICLE_REGULATION_BASES:
        raise ValueError(f"regulation の basis が表に無い値 (許可: {', '.join(BUILD_ARTICLE_REGULATION_BASES)})")
    if regulation is not None and regulation != UNKNOWN_REGULATION and regulation not in known_regulations():
        raise ValueError("regulation が既知の規制 id ではない (BUILD_ARTICLE_REGULATION_NAMES / BUILD_ARTICLE_SEASON_REGULATION)")
    rec = dict(record)
    meta = dict(rec.get("meta") or {})
    history = [dict(h) for h in meta.get("regulation_history") or []]
    step = {"from": meta.get("regulation"), "to": regulation, "basis": basis}
    if at:
        step["at"] = at
    history.append(step)
    meta.update({"regulation": regulation, "regulation_basis": basis, "regulation_history": history})
    rec["meta"] = meta
    return rec


def assert_no_prose(obj, path: str = "$") -> None:
    """記録・LLM の入力に かな・漢字を含む文字列が無いことを確かめる (本文・引用の混入の門)。違反は場所と長さだけを報告する"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _PROSE_RE.search(str(k)):
                raise ValueError(f"prose key at {path} (len {len(str(k))})")
            assert_no_prose(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_prose(v, f"{path}[{i}]")
    elif isinstance(obj, str) and _PROSE_RE.search(obj):
        raise ValueError(f"prose value at {path} (len {len(obj)})")


def llm_payload(record: dict) -> dict:
    """LLM に渡す部分集合: 個体の型 (id と数値)、主張、選出規則、未確定項目の分類と参照 id。本文・出典 URL・メタの自由記述は含まない"""
    payload = {"case_id": record.get("case_id"), "record_kind": record.get("record_kind", "team"),
               "regulation": (record.get("meta") or {}).get("regulation"),
               "members": [{k: m.get(k) for k in PAYLOAD_MEMBER_KEYS} for m in record.get("members", [])],
               "claims": [dict(c) for c in record.get("claims", [])],
               "selection_rules": [dict(r) for r in record.get("selection_rules", [])],
               "selection_combinable": record.get("selection_combinable"),
               "unresolved": [{"category": u.get("category"), "source_ref": u.get("source_ref")} for u in record.get("unresolved", [])]}
    assert_no_prose(payload)
    return payload


POLICY_ACTIONS = ("fetch", "store", "send_llm")   # host_policy の allow / unknown / deny を持つ項目 (それ以外の purpose は用途の制限で見る)


def host_policy_entry(policy: Optional[dict], host: Optional[str]) -> dict:
    """host_policy.json の host の項目 (ホスト名は canonical_host で比較: 小文字、www. とポートを落とす)。無ければ空"""
    from tools.team_build.article_parse import canonical_host
    key = canonical_host(host)
    if not key:
        return {}
    for h, entry in (policy or {}).items():
        if isinstance(entry, dict) and canonical_host(h) == key:
            return entry
    return {}


def host_allowed(policy: Optional[dict], host: Optional[str], purpose: str = "fetch", url: Optional[str] = None) -> bool:
    """host_policy.json ({host: {"fetch" | "store" | "send_llm": allow|unknown|deny, "allowed_urls": [...], "purposes": [...]}}) の判定
    (2026-10-06 ユーザー判断: ホスト全体を無条件に allow にせず、対象 URL と用途の制限も実装で守る)。
    - fetch / store / send_llm: 値が allow のときだけ True (unknown は進めない)。fetch で allowed_urls があれば url が必須で、
      正規化した URL (articles_ingest.normalize_url: 追跡クエリ・断片・末尾の / を落とす) が一覧のどれかと一致するときだけ True
    - それ以外の purpose (pool / weakness / selection / parser_eval): purposes があればその中にあるときだけ True、無ければ True"""
    entry = host_policy_entry(policy, host)
    if purpose in POLICY_ACTIONS:
        if entry.get(purpose) != "allow":
            return False
        if purpose == "fetch" and entry.get("allowed_urls") is not None:
            from tools.team_build.articles_ingest import normalize_url
            if not url:
                return False
            return normalize_url(url) in {normalize_url(u) for u in entry["allowed_urls"]}
        return True
    purposes = entry.get("purposes")
    return True if purposes is None else purpose in purposes


def bank_version(cases: list) -> str:
    return hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _is_synthetic(case: dict) -> bool:
    return bool((case.get("source") or {}).get("synthetic"))


def save_bank(cases: list, bank_dir: Path = DEFAULT_BANK_DIR, allow_synthetic: bool = False) -> Path:
    """cases → <bank_dir>/<version>/cases.jsonl + manifest.json。保存前に本文の混入を検査する (2026-10-06 から source / meta も含めて全体)。
    合成の記録 (source.synthetic) が 1 件でもあれば ValueError (allow_synthetic=True のときだけ通す。保存先が一時ディレクトリでも
    許可は別に要る: 保存先と許可を分ける)"""
    n_synthetic = sum(1 for c in cases if _is_synthetic(c))
    if n_synthetic and not allow_synthetic:
        raise ValueError(f"合成の記録 {n_synthetic} 件はバンクに保存しない (allow_synthetic=True のときだけ)")
    for c in cases:
        assert_no_prose(c)
    version = bank_version(cases)
    out = Path(bank_dir) / version
    out.mkdir(parents=True, exist_ok=True)
    (out / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    regs = Counter(str((c.get("meta") or {}).get("regulation")) for c in cases)
    (out / "manifest.json").write_text(json.dumps({"version": version, "schema": SCHEMA_VERSION, "parser": PARSER_VERSION, "n_cases": len(cases),
                                                   "n_synthetic": n_synthetic, "by_regulation": dict(regs),
                                                   "by_record_kind": dict(Counter(c.get("record_kind", "team") for c in cases)),
                                                   "by_status": dict(Counter(c.get("status") for c in cases))},
                                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def load_bank(version: str, bank_dir: Path = DEFAULT_BANK_DIR, allow_synthetic: bool = False) -> list:
    """固定版の読み出し (version を指定する。最新を暗黙に選ばない)。合成の記録は既定で除く (allow_synthetic=True のときだけ含める)"""
    path = Path(bank_dir) / version / "cases.jsonl"
    cases = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return cases if allow_synthetic else [c for c in cases if not _is_synthetic(c)]
