"""どこでも定義されていない名前の参照を静的に探す (対戦や長い測定のあとでしか通らない経路の NameError を CI で拾う)。

    python -m tests.test_undefined_names

2026-10-05: 代入・import の行を消して参照だけ残った名前が 2 件見つかった (実験 13 の集計の rows = 約 1 時間の測定が終わってから落ちる、
check_advisor_player の基準線の測定の make_benchmark_player)。どちらも対戦が要る経路で、既存のテストは通らない。
symtable で「参照されていて、そのスコープにも外側の関数にもモジュールにも束縛が無く、組み込みでもない名前」を出す。
「代入より前に使う」と、実行時に作る名前 (globals() への代入等) は対象外。import * のあるモジュールは調べない。
"""
from __future__ import annotations

import ast
import builtins
import symtable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("advisor", "champions_agent", "tools", "vision", "tests")
SKIP_PARTS = frozenset({".venv", "node_modules", "pokemon-showdown", "logs", "__pycache__"})
MODULE_NAMES = frozenset({"__file__", "__name__", "__doc__", "__package__", "__spec__", "__builtins__", "__class__"})


def _bound(sym) -> bool:
    return sym.is_assigned() or sym.is_parameter() or sym.is_imported() or sym.is_namespace()


def undefined_names(src: str, filename: str = "<src>") -> list:
    """ソース → [(スコープ名, スコープの行, 名前)] (純粋、昇順)。束縛の有無はスコープごとに見る
    (symtable は "top" という名前の関数をモジュール扱いして is_global を真にするので、束縛のある名前は先に除く)"""
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(ast.parse(src, filename))):
        return []
    top = symtable.symtable(src, filename, "exec")
    tables, stack = [], [top]
    while stack:
        t = stack.pop()
        tables.append(t)
        stack.extend(t.get_children())
    known = set(dir(builtins)) | MODULE_NAMES
    known |= {s.get_name() for s in top.get_symbols() if _bound(s)}
    known |= {s.get_name() for t in tables for s in t.get_symbols() if s.is_declared_global() and s.is_assigned()}
    out = []
    for t in tables:
        for s in t.get_symbols():
            if not s.is_referenced() or _bound(s) or s.get_name() in known:
                continue
            if t is top or s.is_global():
                out.append((t.get_name(), t.get_lineno(), s.get_name()))
    return sorted(out)


def repo_files() -> list:
    files = sorted(REPO.glob("*.py"))
    for d in SCAN_DIRS:
        files += sorted(p for p in (REPO / d).rglob("*.py") if not SKIP_PARTS & set(p.relative_to(REPO).parts))
    return files


def test_checker():
    # 代入の行を消して参照だけ残った形 (実験 13 の rows)、関数内の import から漏れた名前 (make_benchmark_player)
    assert undefined_names("def main():\n    result = {'rows': rows}\n    for r in rows:\n        print(r)\n") == [("main", 1, "rows")]
    assert undefined_names("def run():\n    from a import b\n    b()\n    return c()\n") == [("run", 1, "c")]
    assert undefined_names("x = undefined_at_module\n") == [("top", 0, "undefined_at_module")]
    # 誤検出しない形: 外側の関数の束縛、"top" という名前の関数、global 宣言つきの代入、内包表記、組み込み、モジュールの名前、
    # クラスの中の参照、後ろで定義される関数
    ok = '''
import os
from pathlib import Path
CONST = 1
_cache = None


def outer(a):
    b = a + CONST

    def top(table, col, limit):
        rows = [r for r in range(limit) if r != b]
        return sum(x for x in rows), table, col, later(a)
    return top


def later(v):
    return len(str(v)) + os.getpid()


def setup():
    global _made
    _made = Path(__file__).name


def use():
    return _made, _cache, __name__


class K:
    attr = CONST

    def m(self):
        return super().__repr__() + str(K.attr)
'''
    assert undefined_names(ok) == [], undefined_names(ok)
    # クラスの属性はメソッドから名前だけでは見えない (実行時に NameError) → 拾う。import * のあるモジュールは調べない
    assert undefined_names("class K:\n    attr = 1\n\n    def m(self):\n        return attr\n") == [("m", 4, "attr")]
    assert undefined_names("from os import *\n\ndef f():\n    return whatever\n") == []
    print("test_checker OK")


def test_repo_has_no_undefined_names():
    files = repo_files()
    assert len(files) > 100, f"調べるファイルが少なすぎる ({len(files)}): 走査の対象を確認"
    bad = []
    for p in files:
        rel = str(p.relative_to(REPO))
        for scope, line, name in undefined_names(p.read_text(encoding="utf-8"), rel):
            bad.append(f"{rel}: {name} (スコープ {scope}、{line} 行目から)")
    assert not bad, "定義されていない名前の参照:\n  " + "\n  ".join(bad)
    print(f"test_repo_has_no_undefined_names OK ({len(files)} ファイル)")


def main() -> None:
    test_checker()
    test_repo_has_no_undefined_names()
    print("ALL OK")


if __name__ == "__main__":
    main()
