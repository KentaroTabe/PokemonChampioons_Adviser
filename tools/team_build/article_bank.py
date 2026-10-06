"""記事バンク (docs/ARTICLE_BANK_DESIGN_1006.md §3.5〜§5, §3.7): 解析結果 → 本文を含まない記録 → 検査 → バンクの保存・読み出し。

- build_record: article_parse.parse_article の結果 + 出典・メタ → case の記録 (unresolved_names / site_id_observations は入れない)。
  記録の種類 record_kind = team (6 体) / single_set (単体の型 1 体)。出典は 2 軸 (publisher_kind = 誰が掲載したか /
  usage_evidence = 使用実績の根拠) と合成の印 synthetic を別項目で持つ。種名だけ分かる個体 (members_named_only、個体 id なし) と、
  用途ごとの判定に使う facets (members_known / sets_known / selection_readable / any_set_or_claim) を持つ。
  処理状態 status = ok / warnings / incomplete (情報不足) / conflict (検査の矛盾。problems を渡したときだけ) / failed (個体が無い)
- build_validated_record: build_record → validate_record → 問題を渡して status に反映した記録 (process_batch と手入力が使う)
- validate_record: 個体の数 (種類ごと)・4 技・参戦種・配分の整合 (ポイント合計 / 252 表示との対応 / 実数値の再計算) ・メガ形態と石 ・
  選出規則の個体と種 ・出典の 2 軸。problem_is_insufficient で「情報不足」と「矛盾」を分ける
- usable_for: 用途 (pool / weakness / selection / parser_eval) ごとに記録を使えるか (合成・処理状態・規制・種類・使用実績の根拠・
  用途ごとの必要な情報 BUILD_ARTICLE_PURPOSE_REQUIREMENTS)
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
                                    BUILD_ARTICLE_PURPOSE_RECORD_KINDS, BUILD_ARTICLE_PURPOSE_REQUIREMENTS, BUILD_ARTICLE_RECORD_KIND_MEMBERS,
                                    BUILD_ARTICLE_REGULATION_BASES, BUILD_ARTICLE_REGULATION_NAMES, BUILD_ARTICLE_SEASON_REGULATION,
                                    BUILD_ARTICLE_USAGE_EVIDENCE, BUILD_GEN_EV_POINT_CAP, BUILD_GEN_POINT_BUDGET)
from tools.team_build.article_parse import PARSER_VERSION, STAT_ORDER, mega_table, normalize_named_only

# 2: record_kind、出典の 2 軸 (publisher_kind / usage_evidence) と synthetic、meta の regulation_basis / regulation_history (2026-10-06)
# 3: members_named_only (種名だけ分かる個体)、facets、status の incomplete (情報不足) / conflict (検査の矛盾) の区別と problems、
#    選出規則の schema 2 (article_parse/3) (2026-10-06 ユーザー判断)
SCHEMA_VERSION = "article_case/3"
REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_BANK_DIR = REPO / "logs" / "articles" / "bank"
_PROSE_RE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
PAYLOAD_MEMBER_KEYS = ("id", "species_id", "base_species_id", "mega_stone", "item", "nature", "ability", "points", "ev252", "actual", "moves")
SOURCE_AXES = (("publisher_kind", BUILD_ARTICLE_PUBLISHER_KINDS), ("usage_evidence", BUILD_ARTICLE_USAGE_EVIDENCE))
# 用途 (parser_eval 以外) に使える処理状態。incomplete は一律に除外せず、用途ごとの必要な情報 (facets) で判定する (2026-10-06 ユーザー判断)。
# conflict (検査の矛盾) と failed (個体が無い) は使わない
USABLE_STATUSES = ("ok", "warnings", "incomplete")
UNKNOWN_REGULATION = "unknown"
FACET_KEYS = ("members_known", "sets_known", "selection_readable", "any_set_or_claim")
_INSUFFICIENT_MEMBERS_RE = re.compile(r"members:(\d+)")
_INSUFFICIENT_MOVES_RE = re.compile(r"m\d+:moves:(\d+)")


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


def problem_is_insufficient(problem: str, record_kind: str = "team") -> bool:
    """validate_record の問題のうち「情報不足」(矛盾ではない) のもの: 個体の数が種類の数より少ない (members:<n>) と、技が
    BUILD_ARTICLE_MOVES_PER_SET より少ない (m<i>:moves:<n>)。それ以外 (ポイント合計・252 表示・実数値・メガ石・選出規則の個体と種・
    確率の混入・重複・多すぎる数・出典の値など) は矛盾"""
    expected = BUILD_ARTICLE_RECORD_KIND_MEMBERS.get(record_kind, BUILD_ARTICLE_RECORD_KIND_MEMBERS["team"])
    m = _INSUFFICIENT_MEMBERS_RE.fullmatch(problem)
    if m:
        return int(m.group(1)) < expected
    m = _INSUFFICIENT_MOVES_RE.fullmatch(problem)
    if m:
        return int(m.group(1)) < BUILD_ARTICLE_MOVES_PER_SET
    return False


def compute_facets(members: list, named_only: list, claims: list, selection_rules: list, record_kind: str) -> dict:
    """用途ごとの判定に使う情報の揃い方 (2026-10-06 ユーザー判断):
    - members_known: 種類の個体数 (team = 6) の種が分かる (型のある個体 + 種名だけ分かる個体の基本種の数)
    - sets_known: 型のある個体が種類の個体数に達し、各個体に BUILD_ARTICLE_MOVES_PER_SET 技がある (従来の ok の条件)
    - selection_readable: 選出規則が 1 つ以上読めた
    - any_set_or_claim: 技の揃った型が 1 つ以上あるか、筆者の主張が 1 つ以上ある"""
    expected = BUILD_ARTICLE_RECORD_KIND_MEMBERS.get(record_kind, BUILD_ARTICLE_RECORD_KIND_MEMBERS["team"])
    known = {m.get("base_species_id") or m.get("species_id") for m in list(members) + list(named_only)} - {None}
    complete = [m for m in members if len(m.get("moves") or []) == BUILD_ARTICLE_MOVES_PER_SET]
    return {"members_known": len(known) >= expected,
            "sets_known": len(members) >= expected and len(complete) == len(members),
            "selection_readable": bool(selection_rules),
            "any_set_or_claim": bool(complete) or bool(claims)}


def record_facets(record: dict) -> dict:
    """記録の facets (記録に無い古い記録は中身から計算する)"""
    f = record.get("facets")
    if isinstance(f, dict) and all(k in f for k in FACET_KEYS):
        return dict(f)
    kind = record.get("record_kind", "team")
    return compute_facets(record.get("members") or [], record.get("members_named_only") or [], record.get("claims") or [],
                          record.get("selection_rules") or [], kind if kind in BUILD_ARTICLE_RECORD_KIND_MEMBERS else "team")


def record_status(members: list, warnings: list, record_kind: str, problems: Optional[list] = None,
                  named_only: Optional[list] = None) -> str:
    """処理状態: 個体が無い (型のある個体も種名だけの個体も無い) → failed、検査の矛盾 (problems のうち情報不足でないもの) → conflict、
    種類の個体数に満たない・技一覧の無い個体・情報不足の問題 → incomplete、警告 → warnings、それ以外 → ok。
    problems (validate_record の結果) は渡されたときだけ見る (渡されなければ従来どおり)"""
    expected = BUILD_ARTICLE_RECORD_KIND_MEMBERS[record_kind]
    if not members and not named_only:
        return "failed"
    if problems and any(not problem_is_insufficient(p, record_kind) for p in problems):
        return "conflict"
    if len(members) < expected or any(not m.get("moves") for m in members) or problems:
        return "incomplete"
    if warnings or any(m.get("warnings") for m in members):
        return "warnings"
    return "ok"


def build_record(parsed: dict, source: dict, meta: Optional[dict] = None, case_id: Optional[str] = None,
                 record_kind: str = "team", problems: Optional[list] = None) -> dict:
    """解析結果 → case の記録。本文・文・解決できなかった名前・サイト固有 id の観測は入れない (ローカル用で別扱い)。
    record_kind: "team" (6 体と各 4 技で ok) / "single_set" (1 体と 4 技で ok)。source は normalize_source で検査する。
    members_named_only (変換層が渡した種名だけの個体) は個体 id を付けずに別の項目に置く (選出紹介から 6 体を補完しない)。
    problems: validate_record の結果を渡すと status に反映し (矛盾 → conflict、情報不足 → incomplete)、記録の problems に残す
    (id と数値だけの文字列)。渡さなければ従来どおり (problems の項目も作らない)"""
    if record_kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
        raise ValueError(f"record_kind が表に無い値 (許可: {', '.join(BUILD_ARTICLE_RECORD_KIND_MEMBERS)})")
    src = normalize_source(source)
    members = [{k: v for k, v in m.items() if k != "display"} for m in parsed.get("members", [])]
    named = normalize_named_only(parsed.get("members_named_only"))
    body = {"members": members, "members_named_only": named, "claims": parsed.get("claims", []),
            "selection_rules": parsed.get("selection_rules", []), "selection_combinable": parsed.get("selection_combinable"),
            "unresolved": parsed.get("unresolved", [])}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    warnings = list(parsed.get("warnings", []))
    if len(members) > BUILD_ARTICLE_RECORD_KIND_MEMBERS[record_kind]:
        warnings.append("members_exceed_kind")
    rec = {"case_id": case_id or f"case_{digest}", "record_kind": record_kind, "source": src, "meta": dict(meta or {}), **body,
           "counts": dict(parsed.get("counts", {})), "warnings": warnings,
           "versions": {"parser": parsed.get("parser_version", PARSER_VERSION), "dictionary": parsed.get("dictionary_version"),
                        "schema": SCHEMA_VERSION},
           "facets": compute_facets(members, named, body["claims"], body["selection_rules"], record_kind),
           "status": record_status(members, warnings, record_kind, problems, named)}
    if problems is not None:
        rec["problems"] = list(problems)
    return rec


def build_validated_record(parsed: dict, source: dict, meta: Optional[dict] = None, case_id: Optional[str] = None,
                           record_kind: str = "team", legal_ids: Optional[set] = None, dex=None) -> dict:
    """build_record → validate_record → その問題を渡した build_record (status に矛盾 / 情報不足を反映し、problems を残した記録)"""
    first = build_record(parsed, source, meta, case_id, record_kind)
    return build_record(parsed, source, meta, case_id, record_kind, problems=validate_record(first, legal_ids, dex))


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
    個体の数の期待値は record_kind ごと (team = 6、single_set = 1。record_kind の無い古い記録は team)、基本種の重複は team だけ見る
    (種名だけ分かる個体 members_named_only も含める)。情報不足 (個体・技が足りない) と矛盾の区別は problem_is_insufficient。
    選出規則の種 (selected_species): 型のある個体の基本種なら個体 id で書くべき (selected_species_is_member)、種名だけの個体と基本種が
    同じで形態が違えば form_mismatch、構築の種が全部分かっているのにその外の種なら species_outside_team"""
    problems: list = []
    members = record.get("members", [])
    named = record.get("members_named_only") or []
    kind = record.get("record_kind", "team")
    if kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
        problems.append("record_kind_unknown")
        kind = "team"
    expected = BUILD_ARTICLE_RECORD_KIND_MEMBERS[kind]
    if len(members) != expected:
        problems.append(f"members:{len(members)}")
    if len(members) + len(named) > expected:
        problems.append(f"members_total:{len(members) + len(named)}")
    if kind == "team":
        bases = [m.get("base_species_id") for m in members] + [n.get("base_species_id") for n in named]
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
    for k, n in enumerate(named):
        sid = n.get("species_id")
        if legal_ids and sid not in legal_ids and n.get("base_species_id") not in legal_ids:
            problems.append(f"named{k}:species_not_legal:{sid}")
        stone = n.get("mega_stone")
        if stone is not None:
            if sid in mega["forms"]:
                if mega["forms"][sid][1] and stone != mega["forms"][sid][1]:
                    problems.append(f"named{k}:mega_stone:{stone}")
            else:
                problems.append(f"named{k}:stone_without_form")
    ids = {m.get("id") for m in members}
    member_bases = {m.get("base_species_id") for m in members}
    named_by_base = {n.get("base_species_id"): n.get("species_id") for n in named}
    known_bases = member_bases | set(named_by_base)
    team_fully_known = kind == "team" and len(known_bases - {None}) >= expected
    for i, r in enumerate(record.get("selection_rules", [])):
        if not set(r.get("selected_members") or []) <= ids:
            problems.append(f"rule{i}:members_outside_team")
        if r.get("lead") is not None and r["lead"] not in (r.get("selected_members") or []):
            problems.append(f"rule{i}:lead_outside_selection")
        sel_species = r.get("selected_species") or []
        if r.get("lead_species") is not None and r["lead_species"] not in [s.get("species_id") for s in sel_species]:
            problems.append(f"rule{i}:lead_species_outside_selection")
        if r.get("lead") is not None and r.get("lead_species") is not None:
            problems.append(f"rule{i}:two_leads")
        for s in sel_species:
            sid, base = s.get("species_id"), s.get("base_species_id")
            if base in member_bases:
                problems.append(f"rule{i}:selected_species_is_member:{sid}")
            elif base in named_by_base and named_by_base[base] != sid:
                problems.append(f"rule{i}:selected_species_form_mismatch:{sid}")
            elif team_fully_known and base not in known_bases:
                problems.append(f"rule{i}:species_outside_team:{sid}")
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
    - それ以外: 合成 (source.synthetic) は False、status が conflict / failed (と未知の値) は False、meta.regulation が None /
      "unknown" か引数の regulation と違えば False (regulation を指定しなければ一致しないので False。規制を問わずに使う用途は作らない)
    - incomplete は一律に除外せず、用途ごとの必要な情報 (BUILD_ARTICLE_PURPOSE_REQUIREMENTS、記録の facets) で判定する:
      pool = 6 体の種と型が揃う、selection = 6 体の種が分かり選出規則がある (代表 1 体だけの構築は照合の条件を満たさないので不可)、
      weakness = 技の揃った型か筆者の主張が 1 つ以上
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
    facets = record_facets(record)
    return all(facets.get(req) for req in BUILD_ARTICLE_PURPOSE_REQUIREMENTS.get(purpose, ()))


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
    """LLM に渡す部分集合: 個体の型 (id と数値)、種名だけ分かる個体 (id)、主張、選出規則、未確定項目の分類と参照 id。
    本文・出典 URL・メタの自由記述は含まない"""
    payload = {"case_id": record.get("case_id"), "record_kind": record.get("record_kind", "team"),
               "regulation": (record.get("meta") or {}).get("regulation"),
               "members": [{k: m.get(k) for k in PAYLOAD_MEMBER_KEYS} for m in record.get("members", [])],
               "members_named_only": [dict(n) for n in record.get("members_named_only") or []],
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
