"""claude CLI の実行ファイルの解決。

launchd から起動したジョブ (接続テストの操作パネル → end_connection_test.sh → tools.audit_session) は PATH に
~/.local/bin が無く、"claude" が見つからずに一括監査が落ちた (2026-09-29 第16回: FileNotFoundError 'claude')。
PATH で見つからなければ既知の置き場所を順に探す。見つからなければ "claude" のまま返す (従来どおりの失敗になる)。

    from tools.claude_cli import claude_command
    subprocess.run([claude_command(), "-p", ...])
"""
from __future__ import annotations

import os
import shutil

# claude のインストーラが置く場所 (native install / npm global / Homebrew) の順
CANDIDATES = ("~/.local/bin/claude", "~/.claude/local/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude")


def claude_command(which=shutil.which, exists=os.path.exists, expand=os.path.expanduser) -> str:
    """PATH → 既知の置き場所 の順に claude の実行ファイルを探す (引数は検査の注入用)。純粋"""
    found = which("claude")
    if found:
        return found
    for cand in CANDIDATES:
        p = expand(cand)
        if exists(p):
            return p
    return "claude"
