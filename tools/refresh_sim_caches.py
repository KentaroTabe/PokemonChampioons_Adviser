"""Showdown のデータ (pokemon-showdown/。リポジトリに含めない) から、コミットするキャッシュを作り直す。

    python -m tools.refresh_sim_caches                     # 書き出し (既定の Showdown = リポジトリ直下の pokemon-showdown)
    python -m tools.refresh_sim_caches --check             # 書かずに、コミット済みのキャッシュと Showdown のデータの一致だけ見る
                                                           # (違えば終了コード 1)
    python -m tools.refresh_sim_caches --showdown-dir DIR  # 別の場所の Showdown を読む (worktree から本体の checkout を読む等。読むだけ)

2026-10-06: CI と worktree には Showdown が無く、非参戦種の除外 (vision.normalize.champions_illegal_ids)・シムの種 id の変換
(tools.team_build.sets.sim_species_table)・Champions で使える持ち物の判定 (champions_agent.env.team_builder._available_item_ids)
が効かずに、テストの結果が環境で変わっていた。読み手はこのキャッシュを先に読み、Showdown があれば一致を検査する
(champions_agent/data/sim_cache.load_checked)。

キャッシュ:
  vision/data/champions_illegal_ids.json             … champions mod の formats-data.ts で tier Illegal の種族 id
  tools/team_build/data/sim_species_table.json       … pokedex.ts の種族 id → 図鑑番号
  champions_agent/data/champions_available_items.json … 本体 + champions mod の items.ts で Champions で使える持ち物 id
由来 (_meta): Showdown のコミットとその日付、元ファイルごとの「そのコミットから変わっているか」と内容の sha256、件数。
生成時刻は入れない (同じ Showdown からは同じバイト列になる)。レギュレーション切替で Showdown を更新したら作り直してコミットする
(docs/REGULATION_CHANGE_RUNBOOK.md §2)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from champions_agent.data.sim_cache import META_KEY, diff_summary, payload_of
from champions_agent.env import team_builder
from tools.team_build import sets as team_sets
from vision import normalize

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SHOWDOWN_DIR = REPO / "pokemon-showdown"
SHOWDOWN_PREFIX = "pokemon-showdown"      # _meta.sources に書くパスの先頭 (リポジトリ直下の置き場所)
GIT_TIMEOUT_SEC = 10
NOTE = ("python -m tools.refresh_sim_caches で Showdown のデータから生成 (手で編集しない)。読み手はこの値を使い、"
        "Showdown のデータがあれば一致を検査して違えば警告する (champions_agent/data/sim_cache.py)")

# 状態 (refresh の戻り値)
WRITTEN = "書いた"
UNCHANGED = "変わらない"
MATCH = "一致"
MISMATCH = "不一致"
NO_SOURCE = "Showdown が読めない"
FAILED = (MISMATCH, NO_SOURCE)


@dataclass(frozen=True)
class CacheSpec:
    name: str
    cache_path: Path
    key: str                              # 中身の鍵
    sources: tuple                        # Showdown の中のパス (parse の引数の順)
    parse: Callable[..., Any]             # 本文, ... → 使う形 (読み手と同じ関数)
    encode: Callable[[Any], Any]          # 使う形 → JSON に書く形
    decode: Callable[[Any], Any]          # JSON の形 → 使う形 (読み手が load_checked に渡すものと同じ)


def cache_specs() -> list:
    return [
        CacheSpec("champions_illegal_ids", normalize.ILLEGAL_IDS_CACHE_PATH, normalize.ILLEGAL_IDS_CACHE_KEY,
                  (normalize.ILLEGAL_IDS_SOURCE,), normalize.parse_illegal_ids, sorted, set),
        CacheSpec("sim_species_table", team_sets.SIM_SPECIES_CACHE_PATH, team_sets.SIM_SPECIES_CACHE_KEY,
                  (team_sets.POKEDEX_SOURCE,), team_sets.parse_sim_species, dict, dict),
        CacheSpec("champions_available_items", team_builder.AVAILABLE_ITEMS_CACHE_PATH, team_builder.AVAILABLE_ITEMS_CACHE_KEY,
                  tuple(team_builder.AVAILABLE_ITEMS_SOURCES), team_builder.parse_available_items, sorted, set),
    ]


# ------------------------------------------------------------------ 純粋
def build_doc(spec: CacheSpec, value: Any, origin: dict) -> dict:
    """使う形の値 + 由来 (origin_of の戻り値) → キャッシュの JSON 文書。純粋"""
    meta = {"showdown_commit": origin.get("showdown_commit"), "showdown_commit_date": origin.get("showdown_commit_date"),
            "sources": {f"{SHOWDOWN_PREFIX}/{s}": dict((origin.get("sources") or {}).get(s) or {}) for s in spec.sources},
            "count": len(value), "note": NOTE}
    return {META_KEY: meta, spec.key: spec.encode(value)}


def render(doc: dict) -> str:
    """キャッシュの JSON 文書 → ファイルの本文 (鍵の順を固定、1 行 1 項目)。純粋"""
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def check_doc(spec: CacheSpec, doc: Any, fresh: Any) -> Optional[str]:
    """コミット済みの文書と作り直した値を比べる。一致なら None、違えば差の要約。純粋"""
    try:
        cached = spec.decode(payload_of(doc, spec.key))
    except (ValueError, TypeError) as e:
        return f"形が違う ({e})"
    return None if cached == fresh else diff_summary(cached, fresh)


def origin_label(origin: dict) -> str:
    commit = (origin.get("showdown_commit") or "?")[:9]
    date = (origin.get("showdown_commit_date") or "?")[:10]
    modified = any((v or {}).get("modified") for v in (origin.get("sources") or {}).values())
    return f"Showdown {commit} ({date})" + (" + ローカルの変更" if modified else "")


# ------------------------------------------------------------------ 副作用 (git は読むだけ、書くのはキャッシュだけ)
def _git(showdown_dir: Path, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", str(showdown_dir), *args],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def origin_of(showdown_dir: Path, sources) -> dict:
    """由来: Showdown のコミットとその日付、元ファイルごとの「そのコミットから変わっているか」(git で比べる) と内容の sha256。
    git の情報が取れなければ None (git の checkout でない等)"""
    line = _git(showdown_dir, "log", "-1", "--format=%H %cI")
    commit, date = line.split(" ", 1) if line and " " in line else (None, None)
    per = {}
    for s in sources:
        head_blob = _git(showdown_dir, "rev-parse", f"HEAD:{s}")
        work_blob = _git(showdown_dir, "hash-object", "--", s)
        per[s] = {"modified": (head_blob != work_blob) if (head_blob and work_blob) else None,
                  "sha256": hashlib.sha256((Path(showdown_dir) / s).read_bytes()).hexdigest()}
    return {"showdown_commit": commit, "showdown_commit_date": date, "sources": per}


def refresh(specs: list, showdown_dir: Path, check: bool = False) -> list:
    """各キャッシュを Showdown のデータから作り直す (check なら書かずに比べるだけ)。戻り値: [(名前, 状態, 詳細)]"""
    out = []
    for spec in specs:
        try:
            fresh = spec.parse(*[(Path(showdown_dir) / s).read_text(encoding="utf-8") for s in spec.sources])
        except (OSError, ValueError) as e:
            out.append((spec.name, NO_SOURCE, str(e)))
            continue
        try:
            old_text = Path(spec.cache_path).read_text(encoding="utf-8")
        except OSError:
            old_text = None
        try:
            doc = json.loads(old_text) if old_text is not None else None
        except ValueError:
            doc = None
        if check:
            diff = "キャッシュが読めない" if doc is None else check_doc(spec, doc, fresh)
            out.append((spec.name, MATCH if diff is None else MISMATCH, diff or f"{len(fresh)} 件"))
            continue
        origin = origin_of(showdown_dir, spec.sources)
        body = render(build_doc(spec, fresh, origin))
        if body == old_text:
            out.append((spec.name, UNCHANGED, f"{len(fresh)} 件、{origin_label(origin)}"))
            continue
        Path(spec.cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(spec.cache_path).write_text(body, encoding="utf-8")
        before = "新規" if doc is None else (check_doc(spec, doc, fresh) or "中身は同じ、由来だけ更新")
        out.append((spec.name, WRITTEN, f"{len(fresh)} 件、{origin_label(origin)}、前回との差: {before}"))
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Showdown のデータ → コミットするキャッシュ (非参戦種 / シムの種 id → 図鑑番号 / 使える持ち物)")
    ap.add_argument("--check", action="store_true", help="書かずに、コミット済みのキャッシュと Showdown のデータの一致だけ見る")
    ap.add_argument("--showdown-dir", default=str(DEFAULT_SHOWDOWN_DIR), help="読む Showdown の checkout (既定: リポジトリ直下)")
    args = ap.parse_args(argv)
    rows = refresh(cache_specs(), Path(args.showdown_dir), check=args.check)
    for name, status, detail in rows:
        print(f"{name}: {status} ({detail})")
    return 1 if any(status in FAILED for _n, status, _d in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
