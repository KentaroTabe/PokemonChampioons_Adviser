"""ホスト別の変換層 (ページ → 構築ごとの unit)。登録は host_adapters() (ホスト名は article_parse.canonical_host の形)。
取得可否とページ構造を確認したホストだけ (docs/ARTICLE_BANK_DESIGN_1006.md §0 の判断)。"""
from __future__ import annotations


def host_adapters() -> dict:
    """ホスト → 変換層 (html_text, source, meta) -> [unit]"""
    from tools.team_build.adapters.gamewith import gamewith_units
    return {"gamewith.jp": gamewith_units}
