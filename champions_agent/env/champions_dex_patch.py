"""poke-env の gen9 静的データへチャンピオンズのdexを注入するパッチ。

poke-env は @pkmn 由来の gen9 データを同梱しているが、チャンピオンズ固有の
- 新フォーム (greninjamega / dragonitemega 等の新メガ)
- リバランスされた技 (威力/PP変更)
を知らないため、対戦中に新フォームが現れると KeyError で落ちる。

Showdownのchampions modからエクスポートしたJSON (champions_agent/data/champions_dex.json,
生成: node tools/export_champions_dex.js) を GenData(gen9) に上書き注入する。
showdown_env の import 時に自動適用される。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEX_JSON = REPO_ROOT / "champions_agent" / "data" / "champions_dex.json"
EXPORT_SCRIPT = REPO_ROOT / "tools" / "export_champions_dex.js"

_applied = False


def _ensure_dex_json() -> dict | None:
    if DEX_JSON.exists():
        try:
            return json.loads(DEX_JSON.read_text())
        except Exception:
            pass
    # 無ければShowdownから生成を試みる (ビルド済みdistが必要)
    try:
        out = subprocess.run(["node", str(EXPORT_SCRIPT)], capture_output=True,
                             text=True, timeout=120, cwd=str(REPO_ROOT))
        if out.returncode == 0 and out.stdout.strip():
            DEX_JSON.write_text(out.stdout)
            return json.loads(out.stdout)
        print(f"[champions_dex_patch] エクスポート失敗: {out.stderr[:200]}")
    except Exception as e:
        print(f"[champions_dex_patch] エクスポート実行エラー: {e}")
    return None


def apply() -> bool:
    """GenData(gen9) にチャンピオンズの種族/技データを注入する (冪等)"""
    global _applied
    if _applied:
        return True
    data = _ensure_dex_json()
    if not data:
        print("[champions_dex_patch] champions_dex.json が用意できないためスキップ "
              "(新メガ登場時にエラーになります)")
        return False

    from poke_env.data import GenData

    gen9 = GenData.from_gen(9)
    n_sp = n_mv = 0
    for sid, entry in data.get("species", {}).items():
        if sid not in gen9.pokedex:
            gen9.pokedex[sid] = entry
            n_sp += 1
        else:
            # 既存種も種族値等をchampions準拠へ上書き
            gen9.pokedex[sid].update(entry)
    for mid, entry in data.get("moves", {}).items():
        if mid not in gen9.moves:
            gen9.moves[mid] = entry
            n_mv += 1
        else:
            gen9.moves[mid].update(entry)

    print(f"[champions_dex_patch] 適用完了: 新規種族 {n_sp} / 新規技 {n_mv} "
          f"(既存エントリはchampions値で上書き)")
    _applied = True
    return True
