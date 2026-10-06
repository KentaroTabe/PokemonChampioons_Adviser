"""単体の型 (single_set) の手入力 (docs/ARTICLE_BANK_DESIGN_1006.md §3.10、2026-10-06 ユーザー判断)。

自作の型、ゲーム内で確認した型、許可された記事 (人が読んで手で写した型) を登録する共通の入口。最初は入力ファイルと検査コマンドだけ。
入力ファイル (JSON の配列。名前は日本語名か Showdown の id):
  [{"species": "メガラグラージ", "item": "ラグラージナイト", "ability": "すいすい", "nature": "ようき",
    "points": {"H": 2, "A": 32, "S": 32} | "2/32/0/0/0/32" | {"hp": 2, ...},     # 能力ポイント (合計 BUILD_GEN_POINT_BUDGET)
    "moves": ["ウェーブタックル", "じしん", "れいとうパンチ", "どくづき"],
    "actual": [177, 202, 130, 103, 130, 134],                                      # 任意 (記載の実数値。再計算と照合する)
    "source": {"publisher_kind": "user_submission_site", "usage_evidence": "none", "url": "...", "host": "yakkun.com",
               "redistributable": false},
    "regulation": "M-C" | <規制 id> | null, "regulation_basis": "user_confirmed",
    "note": "ローカルのメモ (記録に入れない)"}]
規則:
- 名前は辞書 (jp_names.json) の厳密一致と confirmed の別名だけで解決する。解決できなければ問題として報告し、推測で埋めない
- 記事由来の手入力を「対戦記録で確認した型」にしない: usage_evidence に battle_log_confirmed は受け付けない (その確認は別の経路)。
  無指定は "none"
- 単体の型から 6 体の構築や 3 体の選出を補完しない (record_kind = single_set、claims / selection_rules は空)
- 既定は redistributable = False (yakkun の「一般公開しない個人的な利用」の範囲。公開バンクへの同梱は投稿者の許可などを別に確認する)
- source.entry_method = "manual" を付ける。note は記録に入れない (かな・漢字は記録に残さない)
    python -m tools.team_build.article_manual --check <file>                       # 検査だけ (問題と解決した id を表示)
    python -m tools.team_build.article_manual --import <file> [--bank-dir DIR] [--base-version V]   # 検査を通った型をバンクに保存
純粋関数 (check/import の入出力以外はファイルに触れない)。テストは tests/test_article_manual.py。
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_ARTICLE_MOVES_PER_SET, BUILD_ARTICLE_REGULATION_NAMES
from tools.team_build.article_bank import (DEFAULT_BANK_DIR, UNKNOWN_REGULATION, build_record, known_regulations, load_bank, save_bank,
                                           set_regulation, validate_record)
from tools.team_build.article_parse import STAT_ORDER, ArticleDictionary, default_dictionary, parse_member_head, value_id

PARSER_VERSION = "article_manual/1"
ENTRY_METHOD = "manual"
DEFAULT_USAGE_EVIDENCE = "none"
DEFAULT_REGULATION_BASIS = "user_confirmed"
FORBIDDEN_EVIDENCE = ("battle_log_confirmed",)          # 手入力では付けられない (対戦記録で確認する経路は別)
_STAT_LETTERS = {"h": "hp", "a": "atk", "b": "def", "c": "spa", "d": "spd", "s": "spe"}
REQUIRED_KEYS = ("species", "item", "nature", "moves")


def points_from(value) -> Optional[dict]:
    """能力ポイントの入力 → {stat: int} (0 は省く)。形が違えば None。
    {"H": 2, "A": 32} / {"hp": 2, ...} / "2/32/0/0/0/32" (H/A/B/C/D/S の順) を受け付ける"""
    if value is None:
        return None
    pts: dict = {}
    if isinstance(value, str):
        parts = value.split("/")
        if len(parts) != len(STAT_ORDER):
            return None
        try:
            vals = [int(p) for p in parts]
        except ValueError:
            return None
        pts = dict(zip(STAT_ORDER, vals))
    elif isinstance(value, dict):
        for k, v in value.items():
            key = str(k).strip().lower()
            key = _STAT_LETTERS.get(key, key)
            if key not in STAT_ORDER or not isinstance(v, int) or isinstance(v, bool):
                return None
            pts[key] = v
    else:
        return None
    return {k: pts[k] for k in STAT_ORDER if pts.get(k)}


def resolve_name(dic: ArticleDictionary, category: str, text) -> Optional[str]:
    """日本語名 (辞書の厳密一致 + confirmed の別名) か id → id。解決できなければ None (あいまい一致はしない)"""
    if not isinstance(text, str) or not text.strip():
        return None
    ident = value_id(dic.lookup(category, text))
    if ident:
        return ident
    return text if dic.name_of(category, text) else None


def regulation_id(value) -> Optional[str]:
    """入力の規制 ("M-C" / 規制 id / None / "unknown") → 規制 id か "unknown"。知らない値は None"""
    if value in (None, "", UNKNOWN_REGULATION):
        return UNKNOWN_REGULATION
    if value in BUILD_ARTICLE_REGULATION_NAMES:
        return BUILD_ARTICLE_REGULATION_NAMES[value]
    return value if value in known_regulations() else None


def member_from_entry(entry: dict, dic: ArticleDictionary) -> tuple:
    """入力 1 件 → (個体の辞書 (article_parse の members の形) or None, 問題の列)。名前の解決は辞書の厳密一致と確定した別名だけ"""
    problems = [f"missing:{k}" for k in REQUIRED_KEYS if not entry.get(k)]
    if problems:
        return None, problems
    head = parse_member_head(f"{entry['species']} @ {entry['item']} ({entry['nature']}) {entry.get('ability') or ''}", dic)
    if head is None:
        for cat, key in (("species", "species"), ("items", "item"), ("natures", "nature")):
            if resolve_name(dic, cat, entry.get(key)) is None:
                problems.append(f"unresolved:{key}")
        return None, problems or ["head_not_parsed"]
    if entry.get("ability") and head["ability"] is None:
        problems.append("unresolved:ability")
    moves = []
    for i, mv in enumerate(entry.get("moves") or []):
        mid = resolve_name(dic, "moves", mv)
        if mid is None:
            problems.append(f"unresolved:move{i + 1}")
        else:
            moves.append(mid)
    if len(entry.get("moves") or []) != BUILD_ARTICLE_MOVES_PER_SET:
        problems.append(f"moves:{len(entry.get('moves') or [])}")
    pts = points_from(entry.get("points"))
    if entry.get("points") is not None and pts is None:
        problems.append("points_format")
    actual = entry.get("actual")
    if actual is not None and not (isinstance(actual, list) and len(actual) == len(STAT_ORDER) and all(isinstance(x, int) for x in actual)):
        problems.append("actual_format")
        actual = None
    member = {"species_id": head["species_id"], "base_species_id": head["base_species_id"], "mega_stone": head["mega_stone"],
              "item": head["item"], "nature": head["nature"], "ability": head["ability"], "id": "m1", "points": pts, "ev252": None,
              "actual": actual, "moves": moves, "alt_move_lines": 0, "warnings": list(head["warnings"]), "notes": list(head["notes"])}
    return member, problems


def source_from_entry(entry: dict) -> tuple:
    """入力の source → 記録の source (entry_method = manual、usage_evidence の既定 none、battle_log_confirmed は拒否、
    redistributable の既定 False)。note は入れない。→ (source, 問題の列)"""
    src = dict(entry.get("source") or {})
    problems = []
    src.pop("note", None)
    src["entry_method"] = ENTRY_METHOD
    if src.get("usage_evidence") is None:
        src["usage_evidence"] = DEFAULT_USAGE_EVIDENCE
    if src["usage_evidence"] in FORBIDDEN_EVIDENCE:
        problems.append("usage_evidence_not_allowed_for_manual")
    src.setdefault("redistributable", False)
    if not isinstance(src["redistributable"], bool):
        problems.append("redistributable_not_bool")
    src.setdefault("synthetic", False)
    return src, problems


def record_from_entry(entry: dict, dic: Optional[ArticleDictionary] = None, today: Optional[str] = None) -> tuple:
    """入力 1 件 → (記録 or None, 問題の列)。記録は record_kind = single_set、claims / selection_rules は空 (補完しない)。
    規制は set_regulation で根拠つき (既定 user_confirmed)。validate_record の問題 (ポイント合計・実数値の再計算等) も問題に足す"""
    dic = dic or default_dictionary()
    member, problems = member_from_entry(entry, dic)
    src, sp = source_from_entry(entry)
    problems += sp
    reg = regulation_id(entry.get("regulation"))
    if reg is None:
        problems.append("regulation_unknown_value")
    basis = entry.get("regulation_basis") or DEFAULT_REGULATION_BASIS
    if member is None or problems:
        return None, problems
    parsed = {"members": [member], "claims": [], "selection_rules": [], "selection_combinable": None, "unresolved": [], "warnings": [],
              "counts": {"members": 1, "claims": 0, "selection_rules": 0, "unresolved": 0, "team_sentences": 0},
              "parser_version": PARSER_VERSION, "dictionary_version": dic.version}
    try:
        rec = build_record(parsed, src, {}, record_kind="single_set")
        rec = set_regulation(rec, reg, basis, at=today or datetime.date.today().isoformat())
    except ValueError as e:
        return None, [f"record:{type(e).__name__}"]
    problems = validate_record(rec)
    return (rec if not problems else None), problems


def check_entries(entries: list, dic: Optional[ArticleDictionary] = None, today: Optional[str] = None) -> list:
    """入力の配列 → [{"index", "record" or None, "problems"}]"""
    dic = dic or default_dictionary()
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            out.append({"index": i, "record": None, "problems": ["not_object"]})
            continue
        rec, probs = record_from_entry(e, dic, today)
        out.append({"index": i, "record": rec, "problems": probs})
    return out


def load_entries(path: Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("入力は JSON の配列にする")
    return data


def import_entries(entries: list, bank_dir: Path = DEFAULT_BANK_DIR, base_version: Optional[str] = None,
                   dic: Optional[ArticleDictionary] = None, today: Optional[str] = None) -> tuple:
    """検査を通った記録だけをバンクに保存する (base_version を指定すればその版の記録に足した新しい版)。問題のある入力が 1 件でもあれば
    保存しない。→ (保存先 or None, 検査結果)"""
    results = check_entries(entries, dic, today)
    if any(r["problems"] for r in results) or not results:
        return None, results
    cases = list(load_bank(base_version, bank_dir)) if base_version else []
    cases += [r["record"] for r in results]
    return save_bank(cases, bank_dir), results


def _describe(rec: dict) -> str:
    m = rec["members"][0]
    pts = " ".join(f"{k}{v}" for k, v in (m.get("points") or {}).items())
    return (f"{m['species_id']} (base {m['base_species_id']}, stone {m['mega_stone']}) @ {m['item']} {m['nature']} {m['ability']} "
            f"[{pts}] {'/'.join(m['moves'])} reg={rec['meta'].get('regulation')} ({rec['meta'].get('regulation_basis')}) "
            f"evidence={rec['source'].get('usage_evidence')} status={rec['status']}")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="単体の型の手入力: 検査 (--check) と取り込み (--import)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", metavar="FILE", help="入力ファイルを検査して結果を表示する (保存しない)")
    g.add_argument("--import", dest="import_file", metavar="FILE", help="検査を通った型をバンクに保存する")
    ap.add_argument("--bank-dir", default=str(DEFAULT_BANK_DIR))
    ap.add_argument("--base-version", help="この版の記録に足して新しい版を作る")
    args = ap.parse_args(argv)
    path = Path(args.check or args.import_file)
    entries = load_entries(path)
    if args.check:
        results = check_entries(entries)
        out_path = None
    else:
        out_path, results = import_entries(entries, Path(args.bank_dir), args.base_version)
    n_bad = 0
    for r in results:
        if r["problems"]:
            n_bad += 1
            print(f"#{r['index']}: 問題 {', '.join(r['problems'])}")
        else:
            print(f"#{r['index']}: {_describe(r['record'])}")
    print(f"{len(results)} 件、問題 {n_bad} 件")
    if args.import_file:
        print(f"保存: {out_path}" if out_path else "保存しない (問題のある入力がある)")
        return 0 if out_path else 1
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
