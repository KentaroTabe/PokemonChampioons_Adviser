"""最小記録 (引き継ぎ評価 I-10 の比較用、2026-10-31): JSONL + 小さな manifest + 明示したファイル経路。

- manifest.json: 参照するファイル (コード・モデル・DB・登録チーム等) の経路・sha256・バイト数と、コードの commit。
  読み手は resolve() で経路を引き、無い・sha256 が違えば MinimalPinError で止める (別のファイル・最新へ退避しない)。
- records.jsonl: 1 行 1 記録 {"seq", "t", "type", ...}。書き手は再開時に最後の有効な seq から続け、途中で切れた行があれば
  改行で区切ってから "resume" の行を書く。読み手は壊れた行を飛ばさず problems に数えて返す。
純粋な部分 (check_manifest / parse_lines) は tests/test_inheritance_record.py。新しい常駐・学習基盤は持たない。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

MANIFEST = "manifest.json"
RECORDS = "records.jsonl"


class MinimalPinError(RuntimeError):
    """manifest で指定したファイルが無い・sha256 が違う・コードの版が違う"""


def sha256_file(path) -> Optional[str]:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def write_manifest(rec_dir: Path, refs: dict, code_commit: Optional[str], extra: Optional[dict] = None) -> dict:
    """refs = {名前: ファイル経路}。経路は rec_dir からの相対で残す (rec_dir の外なら絶対経路)"""
    rec_dir = Path(rec_dir)
    files = {}
    for name, p in refs.items():
        p = Path(p)
        try:
            rel = str(p.resolve().relative_to(rec_dir.resolve()))
        except ValueError:
            rel = str(p.resolve())
        files[name] = {"path": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size if p.exists() else None}
    man = {"version": 1, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "code_commit": code_commit, "files": files,
           **(extra or {})}
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / MANIFEST).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return man


def check_manifest(man: dict, actual_sha: dict, code_commit: Optional[str] = None) -> list:
    """manifest と実際の sha256 (名前 → sha256 or None) の照合 (純粋)。成り立たない理由の list"""
    out = []
    for name, e in (man.get("files") or {}).items():
        got = actual_sha.get(name)
        if got is None:
            out.append(f"{name}: ファイルが無い ({e.get('path')})")
        elif got != e.get("sha256"):
            out.append(f"{name}: sha256 {got[:16]} が記録 {str(e.get('sha256'))[:16]} と違う")
    if code_commit is not None and man.get("code_commit") != code_commit:
        out.append(f"code: 記録の commit {man.get('code_commit')} が今の {code_commit} と違う")
    return out


def _path_of(rec_dir: Path, e: dict) -> Path:
    p = Path(e["path"])
    return p if p.is_absolute() else Path(rec_dir) / p


def verify(rec_dir: Path, code_commit: Optional[str] = None) -> list:
    man = json.loads((Path(rec_dir) / MANIFEST).read_text(encoding="utf-8"))
    actual = {n: sha256_file(_path_of(rec_dir, e)) for n, e in (man.get("files") or {}).items()}
    return check_manifest(man, actual, code_commit)


def resolve(rec_dir: Path, name: str) -> Path:
    """manifest の名前 → 経路。無い・sha256 が違えば MinimalPinError (退避しない)"""
    man = json.loads((Path(rec_dir) / MANIFEST).read_text(encoding="utf-8"))
    e = (man.get("files") or {}).get(name)
    if e is None:
        raise MinimalPinError(f"{name}: manifest に無い")
    p = _path_of(rec_dir, e)
    why = check_manifest({"files": {name: e}}, {name: sha256_file(p)})
    if why:
        raise MinimalPinError(why[0])
    return p


def parse_lines(text: str) -> tuple:
    """JSONL の本文 → (記録の list, problems の list) (純粋)。壊れた行は {"line", "kind", "head"} で数える
    (kind: "truncated" = 最後の行で改行が無い / "invalid" = それ以外)。seq の重複・逆行も problems に足す"""
    recs, probs = [], []
    lines = text.split("\n")
    last_seq = None
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            kind = "truncated" if i == len(lines) else "invalid"
            probs.append({"line": i, "kind": kind, "head": line[:60]})
            continue
        s = r.get("seq")
        if isinstance(s, int) and last_seq is not None and s <= last_seq:
            probs.append({"line": i, "kind": "seq_not_increasing", "head": f"{last_seq} -> {s}"})
        last_seq = s if isinstance(s, int) else last_seq
        recs.append(r)
    return recs, probs


def read(rec_dir: Path) -> tuple:
    return parse_lines((Path(rec_dir) / RECORDS).read_text(encoding="utf-8", errors="replace"))


class Writer:
    """records.jsonl への追記。再開 (同じ rec_dir で作り直す) なら最後の有効な seq から続け、resume の行を書く"""

    def __init__(self, rec_dir: Path):
        self.path = Path(rec_dir) / RECORDS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seq = 0
        if self.path.exists():
            raw = self.path.read_text(encoding="utf-8", errors="replace")
            recs, probs = parse_lines(raw)
            self.seq = max([r.get("seq", 0) for r in recs if isinstance(r.get("seq"), int)] or [0])
            with self.path.open("a", encoding="utf-8") as f:
                if raw and not raw.endswith("\n"):
                    f.write("\n")              # 途中で切れた行を次の記録と混ぜない
            self.append("resume", after_seq=self.seq, problems=probs)

    def append(self, typ: str, **fields) -> int:
        self.seq += 1
        row = {"seq": self.seq, "t": round(time.time(), 3), "type": typ, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return self.seq
