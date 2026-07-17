"""champs.pokedb.tokyo 公式オープンデータ (上位ランカー構築) の取得。

https://champs.pokedb.tokyo/guide/opendata で公認されているダウンロードルート。
    https://champs.pokedb.tokyo/opendata/s{N}_{single|double}_ranked_teams.json

内容は「上位ランカーのチーム構成」(順位/レート/6体の種族・タイプ・持ち物)。
技・特性は含まれないため、本モジュールからは以下を集計して使う:
  - ポケモン使用率% (上位チームへの採用頻度) ... championsbattledata に無い情報
  - チームメイト共起率
  - 持ち物傾向 (補助)

名前は日本語のため、vision/data/jp_names.json で showdown ID へ変換する。
"""
from __future__ import annotations

import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path

import requests

BASE_URL = "https://champs.pokedb.tokyo"
ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"
# 通常ブラウザ以外を弾く設定のため、一般的なUAを名乗る (公認DLルート)
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

JP_NAMES_PATH = Path(__file__).resolve().parents[3] / "vision" / "data" / "jp_names.json"

_jp_maps = None


def _load_jp_maps() -> dict:
    """日本語名 -> showdown ID の変換表 (種族/持ち物)"""
    global _jp_maps
    if _jp_maps is None:
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _jp_maps = {
            "species": {ja: v["id"] for ja, v in raw.get("species", {}).items()},
            "items": dict(raw.get("items", {})),
        }
    return _jp_maps


def _species_id(ja_name: str, form: str = "") -> str:
    """日本語種族名 -> showdown ID。フォーム表記は暫定でベース種に丸める"""
    maps = _load_jp_maps()
    sid = maps["species"].get(ja_name)
    if sid:
        return sid
    # 未知の名前 (新ポケモン等) はスラッグ化した日本語のまま保持
    return re.sub(r"[^a-z0-9ぁ-んァ-ン一-龥ー]", "", ja_name.lower())


def _item_id(ja_name: str) -> str:
    maps = _load_jp_maps()
    iid = maps["items"].get(ja_name)
    if iid:
        return iid
    if ja_name.endswith(("ナイト", "ナイトX", "ナイトY")):
        # チャンピオンズ新規のメガストーン (辞書未収録) は表記そのまま保持
        return ja_name
    return ja_name


def fetch_ranked_teams(season_number: int | None = None,
                       rule: str = "single",
                       archive: bool = True) -> tuple[dict, int]:
    """公式オープンデータを取得する。season_number 省略時は最新から遡って探す。

    戻り値: (JSONペイロード, 実際に取得できたシーズン番号)
    """
    candidates = [season_number] if season_number else list(range(12, 0, -1))
    last_err: Exception | None = None
    for n in candidates:
        if n is None:
            continue
        url = f"{BASE_URL}/opendata/s{n}_{rule}_ranked_teams.json"
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            payload = resp.json()
            if archive:
                ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                path = ARCHIVE_DIR / f"pokedb_s{n}_{rule}_{date.today().isoformat()}.json.gz"
                with gzip.open(path, "wt", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                print(f"  [pokedb] 生データを保存: {path}")
            return payload, n
        except Exception as e:  # ネットワークエラー等は次の候補を試さず即時報告
            last_err = e
            break
    raise RuntimeError(f"pokedb opendata が取得できませんでした: {last_err or '404 (全シーズン)'}")


def aggregate_teams(payload: dict) -> dict:
    """チームデータから使用率/共起率/持ち物傾向を集計する。

    戻り値: {
      "usage":     {showdown_id: {"percent": float, "rank": int}},
      "teammates": {showdown_id: {other_id: percent}},
      "items":     {showdown_id: {item_id: percent}},
      "n_teams":   int, "season": str, "updated_at": str,
    }
    """
    teams = payload.get("teams", [])
    n = len(teams)
    appear = Counter()
    item_counts: dict = defaultdict(Counter)
    pair_counts = Counter()

    for t in teams:
        members = []
        for m in t.get("team", []):
            sid = _species_id(m.get("pokemon", ""), m.get("form", ""))
            if not sid:
                continue
            members.append(sid)
            appear[sid] += 1
            item = (m.get("item") or "").strip()
            if item:
                item_counts[sid][_item_id(item)] += 1
        for a, b in combinations(sorted(set(members)), 2):
            pair_counts[(a, b)] += 1

    usage = {}
    for rank, (sid, cnt) in enumerate(appear.most_common(), start=1):
        usage[sid] = {"percent": round(100.0 * cnt / max(1, n), 2), "rank": rank}

    teammates: dict = defaultdict(dict)
    for (a, b), cnt in pair_counts.items():
        pct_a = round(100.0 * cnt / max(1, appear[a]), 1)
        pct_b = round(100.0 * cnt / max(1, appear[b]), 1)
        teammates[a][b] = pct_a
        teammates[b][a] = pct_b

    items = {}
    for sid, counter in item_counts.items():
        total = sum(counter.values()) or 1
        items[sid] = {iid: round(100.0 * c / total, 1) for iid, c in counter.most_common(6)}

    return {
        "usage": usage,
        "teammates": dict(teammates),
        "items": items,
        "n_teams": n,
        "season": payload.get("season"),
        "updated_at": payload.get("updated_at"),
    }
