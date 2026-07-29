"""データ取得元サイトの robots.txt と規約ページを取得して表示する。

    python -m tools.check_site_policy

自動取得の可否を判断するために、機械可読の robots.txt と
人間向けの利用規約の両方を確認する。判断材料を残すのが目的で、
このツール自体はデータ収集を行わない。
"""
from __future__ import annotations

import requests

from champions_agent.data.sources.pokedb_opendata import BASE_URL, USER_AGENT

PAGES = [
    (f"{BASE_URL}/robots.txt", "robots.txt"),
    (f"{BASE_URL}/guide/opendata", "オープンデータの案内"),
    (f"{BASE_URL}/guide/terms", "利用規約 (推定URL)"),
    (f"{BASE_URL}/terms", "利用規約 (推定URL)"),
    (f"{BASE_URL}/guide", "ガイド"),
]


def fetch(url: str) -> tuple[int, str]:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    return r.status_code, r.text


def main() -> None:
    for url, label in PAGES:
        print(f"\n===== {label}: {url} =====")
        try:
            code, text = fetch(url)
        except Exception as e:
            print(f"  取得失敗: {e}")
            continue
        print(f"  HTTP {code} / {len(text)} bytes")
        if code != 200:
            continue
        if url.endswith("robots.txt"):
            print(text[:2000])
        else:
            # HTMLから規約に関わりそうな箇所だけ抜く
            import re
            plain = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
            plain = re.sub(r"<style.*?</style>", " ", plain, flags=re.S)
            plain = re.sub(r"<[^>]+>", "\n", plain)
            plain = re.sub(r"\n{2,}", "\n", plain)
            keys = ("規約", "禁止", "スクレイピング", "クロー", "自動", "転載",
                    "再配布", "商用", "オープンデータ", "ライセンス", "出典",
                    "負荷", "API", "取得")
            hit = [ln.strip() for ln in plain.splitlines()
                   if ln.strip() and any(k in ln for k in keys)]
            for ln in hit[:60]:
                print(f"  {ln}")
            if not hit:
                print("  (該当する記述が見つからない)")


if __name__ == "__main__":
    main()
