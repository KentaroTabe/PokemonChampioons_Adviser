"""run と Package の manifest (再現に必要な版・ピン・シードを 1 か所に)。"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_PROTOCOL_VERSION, BUILD_SCHEMA_VERSION

REPO = Path(__file__).resolve().parent.parent.parent


def git_commit(path: Path = REPO) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def build_manifest(run_id: str, extra: Optional[dict] = None) -> dict:
    m = {
        "schema_version": BUILD_SCHEMA_VERSION,
        "protocol_version": BUILD_PROTOCOL_VERSION,
        "run_id": run_id,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": git_commit(REPO),
        "showdown_commit": git_commit(REPO / "pokemon-showdown"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        # 以下は run が埋める: meta_snapshot, pool_pin, meta_pin, opponent_split, seeds,
        # rl_checkpoint, selection_model, llm (provider / model / prompt_hash / sampling),
        # evaluation_protocol
    }
    if extra:
        m.update(extra)
    return m


def write_manifest(run_dir: Path, manifest: dict) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "manifest.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p
