"""claude CLI の実行ファイル解決 (tools/claude_cli) のテスト: PATH → 既知の置き場所 → 見つからなければ "claude"。

    python -m tests.test_claude_cli
"""
from __future__ import annotations

from tools.claude_cli import CANDIDATES, claude_command


def test_resolution_order():
    # PATH にあればそれ (絶対パス)
    assert claude_command(which=lambda n: "/usr/local/bin/claude", exists=lambda p: False,
                          expand=lambda c: c) == "/usr/local/bin/claude"
    # PATH に無ければ ~/.local/bin/claude 等を順に探す (launchd 環境。2026-09-29 第16回で一括監査が落ちた原因)
    home = {"~/.local/bin/claude": "/Users/x/.local/bin/claude", "~/.claude/local/claude": "/Users/x/.claude/local/claude"}
    got = claude_command(which=lambda n: None, exists=lambda p: p == "/Users/x/.claude/local/claude",
                         expand=lambda c: home.get(c, c))
    assert got == "/Users/x/.claude/local/claude", got
    got2 = claude_command(which=lambda n: None, exists=lambda p: p in ("/Users/x/.local/bin/claude", "/Users/x/.claude/local/claude"),
                          expand=lambda c: home.get(c, c))
    assert got2 == "/Users/x/.local/bin/claude", got2                 # 先に見つかった候補
    # どこにも無ければ従来どおり "claude" (失敗の仕方を変えない)
    assert claude_command(which=lambda n: None, exists=lambda p: False, expand=lambda c: c) == "claude"
    assert CANDIDATES[0] == "~/.local/bin/claude"
    print("test_resolution_order OK")


if __name__ == "__main__":
    test_resolution_order()
