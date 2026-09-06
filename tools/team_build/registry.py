"""成果物台帳 (immutable registry) と bundle の status machine。

logs/registry/index.jsonl に追記し、logs/registry/<kind>/<id>/ に成果物をコピーして以後変更しない。
id は内容の sha256 (先頭 16 桁)。status は candidate → validation → canary → production → retired
(docs/TEAM_BUILDING_IMPLEMENTATION.md §9-8)。production は kind ごとに 1 つで、昇格時に前の
production を retired にしつつ `previous_production` に記録し、rollback で戻せる。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_DIR = REPO / "logs" / "registry"

KINDS = ("team", "selection_model", "rl_checkpoint", "meta_snapshot", "opponent_split",
         "evaluation", "package", "bundle")
STATUSES = ("candidate", "validation", "canary", "production", "retired")
TRANSITIONS = {
    "candidate": {"validation", "retired"},
    "validation": {"canary", "retired"},
    "canary": {"production", "retired"},
    "production": {"retired"},
    "retired": set(),
}


def sha256_path(path: Path) -> str:
    """ファイルまたはディレクトリ (相対パス順) の内容ハッシュ"""
    h = hashlib.sha256()
    path = Path(path)
    if path.is_dir():
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            h.update(str(p.relative_to(path)).encode("utf-8"))
            h.update(p.read_bytes())
    else:
        h.update(path.read_bytes())
    return h.hexdigest()


class Registry:
    def __init__(self, root: Path = DEFAULT_DIR):
        self.root = Path(root)
        self.index = self.root / "index.jsonl"

    # --- 読み ---
    def _rows(self) -> dict:
        rows: dict = {}
        if not self.index.exists():
            return rows
        for line in self.index.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["id"]] = r          # 同じ id は最後の行が最新
        return rows

    def get(self, artifact_id: str) -> Optional[dict]:
        return self._rows().get(artifact_id)

    def resolve(self, artifact_id: str) -> Path:
        r = self.get(artifact_id)
        if r is None:
            raise KeyError(artifact_id)
        return self.root / r["kind"] / artifact_id

    def production(self, kind: str) -> Optional[dict]:
        for r in self._rows().values():
            if r["kind"] == kind and r["status"] == "production":
                return r
        return None

    def list(self, kind: Optional[str] = None, status: Optional[str] = None) -> list:
        return [r for r in self._rows().values()
                if (kind is None or r["kind"] == kind) and (status is None or r["status"] == status)]

    # --- 書き ---
    def _append(self, row: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.index.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def register(self, kind: str, src: Path, meta: Optional[dict] = None,
                 run_id: Optional[str] = None, status: str = "candidate") -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        src = Path(src)
        digest = sha256_path(src)
        artifact_id = f"{kind}-{digest[:16]}"
        existing = self.get(artifact_id)
        if existing is not None:
            return existing
        dest = self.root / kind / artifact_id
        dest.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest / src.name)
        row = {"id": artifact_id, "kind": kind, "status": status, "sha256": digest,
               "created": time.strftime("%Y-%m-%d %H:%M:%S"), "run_id": run_id,
               "meta": meta or {}, "previous_production": None, "history": [status]}
        return self._append(row)

    def set_status(self, artifact_id: str, new_status: str, note: str = "") -> dict:
        r = self.get(artifact_id)
        if r is None:
            raise KeyError(artifact_id)
        if new_status not in TRANSITIONS.get(r["status"], set()):
            raise ValueError(f"{artifact_id}: {r['status']} → {new_status} は許可されない遷移")
        row = dict(r)
        if new_status == "production":
            cur = self.production(r["kind"])
            if cur is not None and cur["id"] != artifact_id:
                retired = dict(cur)
                retired["status"] = "retired"
                retired["history"] = list(cur.get("history", [])) + ["retired"]
                retired["note"] = f"superseded by {artifact_id}"
                self._append(retired)
                row["previous_production"] = cur["id"]
        row["status"] = new_status
        row["history"] = list(r.get("history", [])) + [new_status]
        if note:
            row["note"] = note
        return self._append(row)

    def rollback(self, kind: str, note: str = "rollback") -> dict:
        """現在の production を retired にし、その previous_production を production に戻す"""
        cur = self.production(kind)
        if cur is None:
            raise ValueError(f"{kind}: production がありません")
        prev_id = cur.get("previous_production")
        if not prev_id:
            raise ValueError(f"{kind}: 戻せる前の production がありません")
        prev = self.get(prev_id)
        retired = dict(cur)
        retired["status"] = "retired"
        retired["history"] = list(cur.get("history", [])) + ["retired"]
        retired["note"] = note
        self._append(retired)
        restored = dict(prev)
        restored["status"] = "production"
        restored["history"] = list(prev.get("history", [])) + ["production"]
        restored["note"] = f"restored by rollback from {cur['id']}"
        return self._append(restored)
