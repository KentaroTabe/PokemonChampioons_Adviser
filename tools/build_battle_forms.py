"""Showdown の図鑑 (pokemon-showdown/data/pokedex.ts) の battleOnly から、戦闘中の形態変化 (ばけのかわが
剥がれたミミッキュ mimikyubusted、ギルガルドのブレード aegislashblade 等) を変化前の種に戻す表
advisor/data/battle_forms.json を起こす (2026-10-09)。

助言の登録参照 (advisor.engine.registration_names) が、場の個体の名前 (戦闘中の形態) で登録に当たらないとき、
最後に変化前の種の日本語名で引くために使う。

    python -m tools.build_battle_forms                                  # 生成
    python -m tools.build_battle_forms --pokedex <pokedex.ts> --dry-run # 書かずに要約だけ
    python -m tools.build_battle_forms --include-changes-from           # changesFrom 由来の行も入れる (既定は入れない)

既定は battleOnly 由来だけ。changesFrom (ロトムのフォルム・霊獣・オリジン・キョダイマックス等) は戦闘外の形態変化を含み、
登録の無いヒートロトムが「ロトム」の登録を引く副作用があるので入れない (2026-10-09)。
表に入れるのは、形態と変化前の種がどちらも助言の図鑑 (advisor.dex) にあるものだけ。battleOnly が複数 (zygardecomplete 等) なら
先頭。地方フォルム (alola / galar 等) を通常の種に戻す行は入れない (別の種の登録を引く副作用を避ける。地方フォルムの戦闘中の
形態を同じ地方フォルムに戻す darmanitangalarzen → darmanitangalar は入れる)。純粋な解析関数はテストから使う。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POKEDEX_TS = REPO / "pokemon-showdown" / "data" / "pokedex.ts"
OUT_PATH = REPO / "advisor" / "data" / "battle_forms.json"

# 地方フォルムの接尾辞 (形態か変化前の種にこれがあり、もう一方に無い行は入れない)
REGIONAL_SUFFIXES = ("alola", "galar", "hisui", "paldea")


def _toid(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def battle_form_sources_from_text(text: str, include_changes_from: bool = False) -> dict:
    """pokedex.ts の本文 → {形態の id: 変化前の種の id} (battleOnly。include_changes_from なら battleOnly が無い形態の
    changesFrom も。純粋)"""
    out = {}
    for m in re.finditer(r"^\t(\w+): \{(.*?)^\t\},", text or "", flags=re.M | re.S):
        body = m.group(2)
        bo = re.search(r"battleOnly: (\"[^\"]*\"|\[[^\]]*\])", body)
        cf = re.search(r"changesFrom: \"([^\"]*)\"", body)
        src = None
        if bo:
            names = re.findall(r"\"([^\"]*)\"", bo.group(1))
            src = names[0] if names else None
        elif cf and include_changes_from:
            src = cf.group(1)
        if src:
            out[m.group(1)] = _toid(src)
    return out


def _regional(sid: str) -> set:
    return {s for s in REGIONAL_SUFFIXES if s in sid}


def build_table(sources: dict, dex_ids) -> dict:
    """{形態: 変化前の種} のうち、両方が図鑑にあり、地方フォルムをまたがないもの (純粋)"""
    ids = set(dex_ids)
    return {f: b for f, b in sorted(sources.items())
            if f in ids and b in ids and f != b and _regional(f) == _regional(b)}


def _showdown_commit(pokedex: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(pokedex.parent), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Showdown の pokedex.ts → advisor/data/battle_forms.json")
    ap.add_argument("--pokedex", type=Path, default=POKEDEX_TS)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-changes-from", action="store_true",
                    help="changesFrom 由来 (戦闘外の形態変化を含む) の行も入れる")
    args = ap.parse_args()
    from advisor.dex import get_dex
    sources = battle_form_sources_from_text(args.pokedex.read_text(encoding="utf-8"), args.include_changes_from)
    table = build_table(sources, get_dex().species_ids())
    doc = {"_source": {"file": "pokemon-showdown/data/pokedex.ts", "showdown_commit": _showdown_commit(args.pokedex),
                       "include_changes_from": bool(args.include_changes_from),
                       "rule": "battleOnly (複数なら先頭)" + (" / changesFrom" if args.include_changes_from else "")
                               + "。形態と変化前の種が advisor.dex にあり、地方フォルムをまたがない"},
           "forms": table}
    print(f"元の行 {len(sources)} 件 → 表 {len(table)} 件")
    for f, b in table.items():
        print(f"  {f} → {b}")
    if not args.dry_run:
        args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"保存: {args.out}")


if __name__ == "__main__":
    main()
