"""Showdown のデータ (pokemon-showdown/。リポジトリに含めない) から作った、コミット済みのキャッシュの読み込み。

2026-10-06 ユーザー判断: テストの結果を環境 (Showdown の有無) で変えないため、読み手は
「まずキャッシュを読み、Showdown のデータがあれば作り直して一致を検査し、違えば警告する (値はキャッシュを使う)」。
(CI には Showdown が無く、非参戦種の除外・シムの種 id の変換・Champions で使える持ち物の判定が効かずに
test_my_roster_resolution / test_team_build_sets が落ちていた。)
キャッシュの作り直しは scripts/refresh_sim_caches.sh (python -m tools.refresh_sim_caches)。

キャッシュの形: {"_meta": {由来: showdown_commit / showdown_commit_date / sources ({元ファイル: {modified, sha256}}) /
count / note}, <中身の鍵>: 中身}。中身の鍵・元ファイル・使う形 (decode) は読み手が決める。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

META_KEY = "_meta"
REFRESH_HINT = "bash scripts/refresh_sim_caches.sh"
DIFF_EXAMPLES = 5          # 警告に載せる差の例の数 (種類ごと)

REPO = Path(__file__).resolve().parent.parent.parent
_warned: set = set()


def payload_of(doc: Any, key: str) -> Any:
    """キャッシュの JSON 文書 → 中身。形が違えば ValueError。純粋"""
    if not isinstance(doc, dict) or key not in doc:
        raise ValueError(f"鍵 {key!r} が無い")
    return doc[key]


def diff_summary(cached: Any, fresh: Any, examples: int = DIFF_EXAMPLES) -> str:
    """キャッシュの値と Showdown から作り直した値の差の要約 (辞書なら鍵と値、それ以外は集合として比べる)。純粋"""
    if isinstance(cached, dict) and isinstance(fresh, dict):
        added = sorted(set(fresh) - set(cached))
        removed = sorted(set(cached) - set(fresh))
        changed = sorted(k for k in set(cached) & set(fresh) if cached[k] != fresh[k])
        parts = [f"Showdown にだけある {len(added)}", f"キャッシュにだけある {len(removed)}", f"値が違う {len(changed)}"]
        ex = ([f"+{k}" for k in added[:examples]] + [f"-{k}" for k in removed[:examples]]
              + [f"~{k}" for k in changed[:examples]])
    else:
        a, b = set(cached), set(fresh)
        added, removed = sorted(b - a), sorted(a - b)
        parts = [f"Showdown にだけある {len(added)}", f"キャッシュにだけある {len(removed)}"]
        ex = [f"+{k}" for k in added[:examples]] + [f"-{k}" for k in removed[:examples]]
    return " / ".join(parts) + (f" (例: {', '.join(ex)})" if ex else "")


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def _warn_once(key: tuple, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    print(f"[warn] {message}", file=sys.stderr)


def load_checked(cache_path: Path, key: str, decode: Callable[[Any], Any], sources: Sequence[Path],
                 parse: Callable[..., Any]) -> Any:
    """キャッシュを読んで decode で使う形にし、Showdown のデータ (sources の全部) が読めれば parse(本文, ...) で作り直して
    一致を検査する (sources の 1 つでも読めなければ Showdown は無いものとして検査しない)。
    - 一致しない: 警告して、キャッシュの値を返す (結果を環境で変えない)
    - キャッシュが読めない (無い / 壊れている): 警告して、Showdown のデータがあればそれを、無ければ空 (parse("", ...)) を返す
    副作用: ファイルの読み込みと stderr への警告 (同じキャッシュ・同じ種類の警告は 1 プロセス 1 回)"""
    cached, err = None, None
    try:
        cached = decode(payload_of(json.loads(Path(cache_path).read_text(encoding="utf-8")), key))
    except (OSError, ValueError, TypeError) as e:
        err = e
    try:
        fresh = parse(*[Path(p).read_text(encoding="utf-8") for p in sources])
    except (OSError, ValueError):          # 無い / 読めない / 文字コードが違う → Showdown は無いものとする
        fresh = None
    where = ", ".join(_rel(p) for p in sources)
    if cached is not None:
        if fresh is not None and fresh != cached:
            _warn_once((str(cache_path), "mismatch"),
                       f"{_rel(cache_path)} が Showdown のデータ ({where}) と違う: {diff_summary(cached, fresh)}。"
                       f"キャッシュの値を使う。作り直し: {REFRESH_HINT}")
        return cached
    _warn_once((str(cache_path), "unreadable"),
               f"キャッシュ {_rel(cache_path)} が読めない ({err})。"
               + ("Showdown のデータを使う" if fresh is not None else "空として扱う") + f"。作り直し: {REFRESH_HINT}")
    return fresh if fresh is not None else parse(*([""] * len(sources)))
