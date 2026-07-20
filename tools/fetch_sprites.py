"""ポケモンスプライトの欠品を PokeAPI 公式リポジトリから補完する。

アイコン照合 (vision/spriteid.py) 用に、チャンピオンズ収録種の
図鑑番号スプライトを images/templetes/{num}.png へ取得する。

    python -m tools.fetch_sprites          # 欠品のみ取得
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{num}.png"
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "images" / "templetes"
CBD_DEX = Path(__file__).resolve().parent.parent / "champions_agent" / "data" / "champions_dex.json"


def main() -> None:
    from advisor.dex import get_dex
    dex = get_dex()
    cbd = json.loads(CBD_DEX.read_text())
    have = {int(p.stem) for p in TEMPLATE_DIR.glob("*.png") if p.stem.isdigit()}

    needed = set()
    for sid in cbd["species"]:
        sp = dex.species(sid)
        if sp and sp["num"] > 0:
            needed.add(sp["num"])
    missing = sorted(needed - have)
    print(f"欠品 {len(missing)}種を取得します")

    ok = ng = 0
    for num in missing:
        url = SPRITE_URL.format(num=num)
        try:
            with urllib.request.urlopen(url, timeout=20) as res:
                data = res.read()
            (TEMPLATE_DIR / f"{num}.png").write_bytes(data)
            ok += 1
        except Exception as e:
            ng += 1
            if ng <= 5:
                print(f"  取得失敗 #{num}: {e}")
        time.sleep(0.05)
        if (ok + ng) % 100 == 0:
            print(f"  {ok + ng}/{len(missing)}...")
    print(f"完了: 取得{ok} / 失敗{ng}")


if __name__ == "__main__":
    main()
