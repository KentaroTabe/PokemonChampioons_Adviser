"""対戦ログ (JSONL) の行の読み取りと、読めなかった行の数え上げ (2026-10-31、KNOWN_ISSUES E-inh-4 / 引き継ぎ評価 I-10 t5)。

読み手 (tools.real_accept.load_records / tools.advice_replay._read_records / tools.analyze_battles._parse_battle) は壊れた行を
黙って飛ばしていた。ここでは飛ばした行を数えて返し、読み手が件数とファイル名を集計に出す。
- 空行 (空白だけの行) は壊れた行に数えない (従来どおり飛ばす)
- JSON として読めない行、JSON だが dict でない行 (数値・配列など。行の種類 type を持てない) を壊れた行に数えて飛ばす
"""
from __future__ import annotations

import json
from pathlib import Path

BAD_HEAD_CHARS = 60      # 壊れた行の先頭の何文字を記録に残すか (中身の確認用)


def parse_jsonl_text(text: str) -> tuple:
    """JSONL の本文 → (記録の列, 壊れた行 [{"line": 行番号 (1 始まり), "head": 先頭 BAD_HEAD_CHARS 文字}]) (純粋)"""
    records, bad = [], []
    for n, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            bad.append({"line": n, "head": line[:BAD_HEAD_CHARS]})
            continue
        if not isinstance(d, dict):
            bad.append({"line": n, "head": line[:BAD_HEAD_CHARS]})
            continue
        records.append(d)
    return records, bad


def read_jsonl(path) -> tuple:
    """ファイル → (記録の列, 壊れた行) (副作用: 読み取り。文字化けは置換して読む = 従来の読み手と同じ)"""
    return parse_jsonl_text(Path(path).read_text(encoding="utf-8", errors="replace"))


def bad_lines_summary(by_file: dict) -> dict:
    """{ファイル名: 壊れた行の列} → {"n": 合計, "files": {ファイル名: 件数}} (純粋。0 件のファイルは入れない)"""
    files = {name: len(bad) for name, bad in (by_file or {}).items() if bad}
    return {"n": sum(files.values()), "files": files}


def bad_lines_text(summary: dict) -> str:
    """bad_lines_summary の戻り値の 1 行の文言 (純粋。0 件なら空文字)"""
    if not summary or not summary.get("n"):
        return ""
    parts = [f"{name} {n} 行" for name, n in sorted(summary["files"].items())]
    return f"読めなかった行 (壊れた JSON・dict でない行、集計から除外): 計 {summary['n']} 行 ({', '.join(parts)})"
