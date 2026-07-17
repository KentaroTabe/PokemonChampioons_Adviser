"""
環境データ収集エントリポイント。

- PokeAPIから静的データ(種族値/タイプ/特性/技/持ち物)を取得しDBへ格納
- 使用率統計(ポケモン/技/特性/持ち物/テラス/努力値配分/相棒傾向)を取得しDBへ格納
- 定期実行(cron/launchd等)を想定しているが、現時点では手動実行のみサポート

使い方:
    python -m champions_agent.data.ingest --pokemon-limit 30
    python -m champions_agent.data.ingest --skip-static --use-dummy-usage
"""
from __future__ import annotations

import argparse
import sys

from tqdm import tqdm

from champions_agent.config import USAGE_TARGET_FORMAT, USAGE_MIN_RATING

from champions_agent.data import database as db
from champions_agent.data.sources import pokeapi_client as pokeapi
from champions_agent.data.sources import usage_scraper


def ingest_static_data(pokemon_limit: int = 30) -> None:
    """PokeAPIから静的データを取得しDBに投入する。

    件数が多いと時間がかかるため、デフォルトでは動作確認用に上位N件のみ取得する。
    全件取得したい場合は pokemon_limit を十分大きくすること。
    """
    print(f"[ingest] PokeAPIから静的データを取得します(上限 {pokemon_limit} 匹)")
    db.init_db()

    pokemon_entries = list(pokeapi.iter_pokemon_list(limit=pokemon_limit))
    ability_cache: dict[str, int] = {}
    move_cache: dict[str, int] = {}

    with db.get_connection() as conn:
        for entry in tqdm(pokemon_entries, desc="pokemon"):
            try:
                detail = pokeapi.fetch_pokemon_detail(entry["name"])
            except Exception as e:
                print(f"  [warn] {entry['name']} の取得に失敗: {e}", file=sys.stderr)
                continue

            pokemon_id = db.upsert_pokemon(conn, {
                "id": detail["id"],
                "name": detail["name"],
                "display_name": detail["display_name"],
                "hp": detail["hp"],
                "attack": detail["attack"],
                "defense": detail["defense"],
                "sp_attack": detail["sp_attack"],
                "sp_defense": detail["sp_defense"],
                "speed": detail["speed"],
                "type1": detail["type1"],
                "type2": detail["type2"],
            })

            for a in detail["abilities"]:
                if a["name"] not in ability_cache:
                    try:
                        ad = pokeapi.fetch_ability_detail(a["name"])
                    except Exception as e:
                        print(f"    [warn] ability {a['name']} 取得失敗: {e}", file=sys.stderr)
                        continue
                    ability_cache[a["name"]] = db.upsert_ability(conn, ad)
                db.link_pokemon_ability(
                    conn, pokemon_id, ability_cache[a["name"]], a["is_hidden"], a["slot"]
                )

            for m in detail["moves"]:
                if m["name"] not in move_cache:
                    try:
                        md = pokeapi.fetch_move_detail(m["name"])
                    except Exception as e:
                        print(f"    [warn] move {m['name']} 取得失敗: {e}", file=sys.stderr)
                        continue
                    move_cache[m["name"]] = db.upsert_move(conn, md)
                db.link_pokemon_move(
                    conn, pokemon_id, move_cache[m["name"]], m["learn_method"]
                )

            conn.commit()

    print(f"[ingest] 静的データ投入完了: pokemon={len(pokemon_entries)}, "
          f"abilities={len(ability_cache)}, moves={len(move_cache)}")


def ingest_usage_stats(fmt: str = USAGE_TARGET_FORMAT, use_dummy: bool = False,
                        rating: int = USAGE_MIN_RATING, month: str | None = None) -> None:
    """使用率統計を取得し、新しいスナップショットとしてDBに投入する。

    既定では Smogon usage stats (chaos JSON) から実データを取得する。
    use_dummy=True の場合のみネットワーク不要のダミーデータを使う。
    """
    print(f"[ingest] 使用率統計を取得します(format={fmt}, rating={rating}, dummy={use_dummy})")
    db.init_db()

    entries, meta = usage_scraper.fetch_usage_stats(fmt=fmt, use_dummy=use_dummy,
                                                      rating=rating, month=month)
    source = "dummy" if use_dummy else "smogon"

    with db.get_connection() as conn:
        snapshot_id = db.create_usage_snapshot(
            conn, source=source, fmt=fmt,
            rating_cutoff=meta.get("rating_cutoff"),
            note="dummy data for pipeline verification" if use_dummy else "",
            source_month=meta.get("month"),
            number_of_battles=meta.get("number_of_battles"),
            source_url=(
                f"https://www.smogon.com/stats/{meta.get('month')}/chaos/{fmt}-{rating}.json"
                if not use_dummy else None
            ),
        )


        pokemon_rows, move_rows, ability_rows = [], [], []
        item_rows, tera_rows, spread_rows, teammate_rows = [], [], [], []

        for e in entries:
            pokemon_rows.append((snapshot_id, e.pokemon_name, e.usage_percent, e.rank))
            for name, pct in e.moves.items():
                move_rows.append((snapshot_id, e.pokemon_name, name, pct))
            for name, pct in e.abilities.items():
                ability_rows.append((snapshot_id, e.pokemon_name, name, pct))
            for name, pct in e.items.items():
                item_rows.append((snapshot_id, e.pokemon_name, name, pct))
            for name, pct in e.tera_types.items():
                tera_rows.append((snapshot_id, e.pokemon_name, name, pct))
            for s in e.spreads:
                spread_rows.append((snapshot_id, e.pokemon_name, s["nature"], s["evs"], s["usage_percent"]))
            for name, pct in e.teammates.items():
                teammate_rows.append((snapshot_id, e.pokemon_name, name, pct))

        db.bulk_insert(conn, "pokemon_usage",
                        ["snapshot_id", "pokemon_name", "usage_percent", "rank"], pokemon_rows)
        db.bulk_insert(conn, "move_usage",
                        ["snapshot_id", "pokemon_name", "move_name", "usage_percent"], move_rows)
        db.bulk_insert(conn, "ability_usage",
                        ["snapshot_id", "pokemon_name", "ability_name", "usage_percent"], ability_rows)
        db.bulk_insert(conn, "item_usage",
                        ["snapshot_id", "pokemon_name", "item_name", "usage_percent"], item_rows)
        db.bulk_insert(conn, "tera_usage",
                        ["snapshot_id", "pokemon_name", "tera_type", "usage_percent"], tera_rows)
        db.bulk_insert(conn, "spread_usage",
                        ["snapshot_id", "pokemon_name", "nature", "evs", "usage_percent"], spread_rows)
        db.bulk_insert(conn, "teammate_usage",
                        ["snapshot_id", "pokemon_name", "teammate_name", "usage_percent"], teammate_rows)
        conn.commit()

    print(f"[ingest] 使用率統計投入完了: snapshot_id={snapshot_id}, pokemon={len(entries)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="環境データ収集(定期実行想定・現状は手動実行)")
    parser.add_argument("--skip-static", action="store_true", help="PokeAPI静的データ取得をスキップ")
    parser.add_argument("--skip-usage", action="store_true", help="使用率統計取得をスキップ")
    parser.add_argument("--pokemon-limit", type=int, default=30,
                         help="静的データ取得対象のポケモン数上限(動作確認用デフォルト30)")
    parser.add_argument("--format", type=str, default=USAGE_TARGET_FORMAT,
                         help="使用率統計の対象フォーマット")
    parser.add_argument("--rating", type=int, default=USAGE_MIN_RATING,
                         help="使用率統計の対象レーティング下限(Smogon chaosの-0/-1500/-1630/-1760)")
    parser.add_argument("--month", type=str, default=None,
                         help="使用率統計の対象月(例 2026-06)。省略時は直近の存在する月を自動検出")
    parser.add_argument("--use-dummy-usage", action="store_true", default=False,
                         help="ネットワーク不要のダミーデータを使う(動作確認用。既定は実データ)")
    args = parser.parse_args()

    if not args.skip_static:
        ingest_static_data(pokemon_limit=args.pokemon_limit)
    if not args.skip_usage:
        ingest_usage_stats(fmt=args.format, use_dummy=args.use_dummy_usage,
                            rating=args.rating, month=args.month)



if __name__ == "__main__":
    main()
