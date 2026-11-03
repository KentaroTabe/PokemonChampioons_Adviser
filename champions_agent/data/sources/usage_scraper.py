"""
使用率統計(よく使われるポケモン・技・特性・持ち物・テラスタイプ・努力値配分)の取得。

主軸: Smogon usage stats (chaos JSON)
    https://www.smogon.com/stats/{YYYY-MM}/chaos/{format}-{rating}.json
    - HTMLスクレイピングと違いサイト構造変化に強く、
      技/特性/持ち物/テラス/努力値配分/チームメイト共起率が1リクエストで全て取得できる。

補助(フォールバック): Pikalytics HTMLパース
    - Smogonにフォーマットが無いレギュ用。DOM構造依存のため壊れやすい。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import requests

from champions_agent.config import (
    USAGE_STATS_SOURCES, USAGE_TARGET_FORMAT, USAGE_MIN_RATING,
    USAGE_MIN_RANKED_TEAMS, SMOGON_CHAMPIONS_FORMATS, SMOGON_USAGE_DIR,
    SMOGON_FETCH_TIMEOUT_SEC, SMOGON_CONVERT_VERSION,
)
from champions_agent.data.sources.name_mapping import (
    to_pokeapi_slug, normalize_move_name, normalize_item_name, normalize_ability_name,
)

SMOGON_STATS_BASE_URL = "https://www.smogon.com/stats"
# チャンピオンズ実データが取得できない場合のフォールバック用フォーマット
SMOGON_FALLBACK_FORMAT = "gen9ou"


@dataclass
class PokemonUsageEntry:
    pokemon_name: str          # PokeAPI slug化済み
    usage_percent: float
    rank: int
    abilities: dict[str, float] = field(default_factory=dict)   # name -> percent
    items: dict[str, float] = field(default_factory=dict)
    moves: dict[str, float] = field(default_factory=dict)
    tera_types: dict[str, float] = field(default_factory=dict)
    spreads: list[dict[str, Any]] = field(default_factory=list)  # [{nature, evs, usage_percent}]
    teammates: dict[str, float] = field(default_factory=dict)


def _latest_available_month(fmt: str, rating: int) -> str:
    """直近数ヶ月を新しい順に試し、実際にファイルが存在する月を返す。

    Smogonの統計は当月分がまだ生成されていないことが多いため、
    未来日を仮定せず「現在月から遡って最初に見つかった月」を採用する。
    """
    today = date.today()
    year, month = today.year, today.month
    for _ in range(6):  # 直近6ヶ月まで遡る
        ym = f"{year:04d}-{month:02d}"
        url = f"{SMOGON_STATS_BASE_URL}/{ym}/chaos/{fmt}-{rating}.json"
        resp = requests.head(url, timeout=10)
        if resp.status_code == 200:
            return ym
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    raise RuntimeError(f"{fmt}-{rating} の使用率統計が直近6ヶ月分見つかりませんでした。")


def fetch_smogon_chaos(fmt: str = USAGE_TARGET_FORMAT,
                        rating: int = USAGE_MIN_RATING,
                        month: str | None = None) -> tuple[list[PokemonUsageEntry], dict]:
    """Smogon usage stats(chaos JSON)を取得し、PokemonUsageEntryのリストへ変換する。

    戻り値: (エントリ一覧, メタ情報dict{month, number_of_battles, cutoff})
    """
    resolved_month = month or _latest_available_month(fmt, rating)
    url = f"{SMOGON_STATS_BASE_URL}/{resolved_month}/chaos/{fmt}-{rating}.json"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    info = payload.get("info", {})
    data = payload.get("data", {})

    entries: list[PokemonUsageEntry] = []
    # usage降順でランクを振る
    sorted_items = sorted(data.items(), key=lambda kv: kv[1].get("usage", 0), reverse=True)

    for rank, (raw_name, stats) in enumerate(sorted_items, start=1):
        if raw_name == "empty":
            continue
        slug = to_pokeapi_slug(raw_name)

        abilities = {
            normalize_ability_name(k): v for k, v in stats.get("Abilities", {}).items()
            if k != "empty"
        }
        items = {
            normalize_item_name(k): v for k, v in stats.get("Items", {}).items()
            if k not in ("empty", "Nothing")
        }
        moves = {
            normalize_move_name(k): v for k, v in stats.get("Moves", {}).items()
            if k != "empty"
        }
        tera_types = dict(stats.get("Tera Types", {}))
        tera_types.pop("empty", None)

        spreads = []
        for spread_key, pct in stats.get("Spreads", {}).items():
            if ":" not in spread_key:
                continue
            nature, evs = spread_key.split(":", 1)
            spreads.append({"nature": nature.lower(), "evs": evs, "usage_percent": pct})

        teammates = {
            to_pokeapi_slug(k): v for k, v in stats.get("Teammates", {}).items()
        }

        entries.append(PokemonUsageEntry(
            pokemon_name=slug,
            usage_percent=stats.get("usage", 0.0) * 100,  # 0-1スケール -> %表記
            rank=rank,
            abilities=abilities,
            items=items,
            moves=moves,
            tera_types=tera_types,
            spreads=spreads,
            teammates=teammates,
        ))

    meta = {
        "month": resolved_month,
        "format": info.get("metagame", fmt),
        "rating_cutoff": info.get("cutoff", rating),
        "number_of_battles": info.get("number of battles"),
    }
    return entries, meta


def fetch_raw_usage_html(pokemon_name: str, fmt: str = USAGE_TARGET_FORMAT) -> str:
    """Pikalytics個別ポケモンページHTMLを取得する(Smogonにフォーマットが無い場合のフォールバック)。"""
    base_url = USAGE_STATS_SOURCES["pikalytics"]["base_url"]
    url = f"{base_url}/pokedex/{fmt}/{pokemon_name}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_usage_html(html: str, pokemon_name: str, rank: int) -> PokemonUsageEntry:
    """Pikalytics HTMLから使用率統計を抽出する(TODO: 実サイトのDOM構造に合わせて実装)。

    Smogon chaos JSONが利用可能なフォーマットでは本関数は使用しない。
    """
    raise NotImplementedError(
        "parse_usage_html はPikalyticsのDOM構造確定後に実装してください。"
        "通常フォーマット(gen9ou等)は fetch_smogon_chaos を使用してください。"
    )


def fetch_dummy_usage_stats(sample_pokemon: list[str] | None = None,
                             seed: int = 42) -> list[PokemonUsageEntry]:
    """開発・パイプライン検証用のダミー使用率データを生成する(ネットワーク不要)。"""
    rng = random.Random(seed)
    sample_pokemon = sample_pokemon or [
        "gholdengo", "landorus-therian", "great-tusk", "kingambit",
        "dragapult", "iron-valiant", "garganacl", "ting-lu",
        "roaring-moon", "ogerpon-wellspring",
    ]
    dummy_moves_pool = ["protect", "substitute", "swords-dance", "earthquake",
                        "thunderbolt", "shadow-ball", "u-turn", "close-combat"]
    dummy_items_pool = ["leftovers", "choice-scarf", "choice-band", "heavy-duty-boots",
                        "assault-vest", "life-orb"]
    dummy_abilities_pool = ["good-as-gold", "intimidate", "protosynthesis", "levitate"]
    dummy_tera_pool = ["steel", "water", "fairy", "ground", "flying"]

    entries: list[PokemonUsageEntry] = []
    for rank, name in enumerate(sample_pokemon, start=1):
        usage_percent = round(max(1.0, 40.0 - rank * 3.5 + rng.uniform(-2, 2)), 2)

        moves = {m: round(rng.uniform(5, 90), 1)
                  for m in rng.sample(dummy_moves_pool, k=4)}
        items = {i: round(rng.uniform(5, 60), 1)
                 for i in rng.sample(dummy_items_pool, k=3)}
        abilities = {a: round(rng.uniform(5, 90), 1)
                     for a in rng.sample(dummy_abilities_pool, k=2)}
        tera_types = {t: round(rng.uniform(5, 50), 1)
                      for t in rng.sample(dummy_tera_pool, k=2)}
        spreads = [{
            "nature": rng.choice(["jolly", "adamant", "modest", "timid", "careful"]),
            "evs": "252/0/0/252/4/0",
            "usage_percent": round(rng.uniform(10, 70), 1),
        }]
        teammates = {t: round(rng.uniform(5, 40), 1)
                     for t in rng.sample([p for p in sample_pokemon if p != name], k=min(3, len(sample_pokemon) - 1))}

        entries.append(PokemonUsageEntry(
            pokemon_name=name,
            usage_percent=usage_percent,
            rank=rank,
            abilities=abilities,
            items=items,
            moves=moves,
            tera_types=tera_types,
            spreads=spreads,
            teammates=teammates,
        ))
    return entries


def fetch_champions_usage(fmt: str = "Singles", season: str | None = None,
                          season_number: int | None = None,
                          limit: int | None = None
                          ) -> tuple[list[PokemonUsageEntry], dict]:
    """ポケモンチャンピオンズ実環境の使用率統計を取得する (2ソース統合)。

    - championsbattledata.com API: 技/持ち物/特性/性格/能力ポイントの採用率%
      (ゲーム内「バトルデータ」の日次収集。ポケモン自体の使用率%は提供されない)
    - champs.pokedb.tokyo オープンデータ: 上位ランカー構築から
      ポケモン使用率% (チーム採用頻度)・チームメイト共起率・持ち物傾向を集計

    クレジット: Battle data provided by Pokémon Champions Battle Data
    (https://championsbattledata.com) / バトルデータベース チャンピオンズ
    (https://champs.pokedb.tokyo)
    """
    from champions_agent.data.sources import championsbattledata as cbd
    from champions_agent.data.sources import pokedb_opendata as pokedb

    per_pokemon, cbd_meta = cbd.fetch_all(fmt=fmt, season=season, limit=limit)
    if not per_pokemon:
        raise RuntimeError("championsbattledata から1件も取得できませんでした")

    # pokedb は補完なので失敗しても続行する
    agg = None
    try:
        payload, sn = pokedb.fetch_ranked_teams(season_number=season_number, rule="single")
        agg = pokedb.aggregate_teams(payload)
        print(f"  [pokedb] シーズン{sn} ({agg['season']}) の上位{agg['n_teams']}構築を集計")
    except Exception as e:
        print(f"  [warn] pokedb opendata 取得失敗 (補完なしで続行): {e}")

    # 足切り: 構築数が少なすぎる集計から使用率%/共起率を作ると、
    # 数構築に載った種族だけが極端な使用率を持つ壊れたスナップショットになる
    # (fetch_ranked_teams 側でも遡るが、season_number 明示時や
    #  全シーズンが薄い場合はここに届く)。補完なしとして扱う。
    if agg is not None and agg["n_teams"] < USAGE_MIN_RANKED_TEAMS:
        print(f"  [warn] pokedb の構築数が{agg['n_teams']}件 (最低{USAGE_MIN_RANKED_TEAMS}) のため、"
              "使用率%/共起率の補完を見送ります")
        agg = None

    usage = agg["usage"] if agg else {}
    teammates_all = agg["teammates"] if agg else {}
    items_supplement = agg["items"] if agg else {}

    entries: list[PokemonUsageEntry] = []
    # 使用率が分かるもの (pokedb掲載) を上位に、それ以外は名前順で後ろに並べる
    ordered = sorted(per_pokemon.keys(),
                     key=lambda s: (-usage.get(s, {}).get("percent", 0.0), s))
    # rank = ゲーム内バトルデータの使用率順位 (cbd の列位置)。無い種は使用率% の順で後ろ (2026-09-18)
    ranks = assign_ranks(per_pokemon, ordered)
    for sid in ordered:
        parsed = per_pokemon[sid]
        u = usage.get(sid)
        # pokedb未掲載 (上位構築に不在) はごく低い擬似使用率を与える
        usage_percent = u["percent"] if u else 0.1
        teammates = dict(sorted(teammates_all.get(sid, {}).items(),
                                key=lambda kv: -kv[1])[:8])
        items = dict(parsed["items"]) or dict(items_supplement.get(sid, {}))
        entries.append(PokemonUsageEntry(
            pokemon_name=sid,
            usage_percent=usage_percent,
            rank=ranks[sid],
            abilities=parsed["abilities"],
            items=items,
            moves=parsed["moves"],
            tera_types={},          # チャンピオンズにテラスタルは無い
            spreads=parsed["spreads"],
            teammates=teammates,
        ))

    meta = {
        "month": cbd_meta.get("season"),
        "format": f"champions-{fmt.lower()}",
        "rating_cutoff": None,
        # ここは対戦数ではなく「集計母数 = pokedbの上位構築数」を入れている
        # (championsbattledataは対戦数を公開していない)。列名との差は note で補う。
        "number_of_battles": agg["n_teams"] if agg else None,
        "source": "championsbattledata+pokedb" if agg else "championsbattledata",
        "source_url": "https://championsbattledata.com/api",
        "note": ("usage%はpokedb上位構築の採用頻度、技/持ち物/特性/配分は"
                 "championsbattledata (ゲーム内バトルデータ由来)。"
                 "rank はゲーム内バトルデータの使用率順位 (championsbattledata の列位置。無い種は使用率%順で後置)。"
                 "number_of_battles は対戦数ではなくpokedbの集計構築数。"
                 "Credit: Battle data provided by Pokémon Champions Battle Data"),
    }
    return entries, meta


def assign_ranks(per_pokemon: dict, fallback_order: list) -> dict:
    """{species_id: rank}。championsbattledata の ingame_rank (ゲーム内バトルデータの使用率順位) があればそれ、
    無い種は「ある種の最大順位 + fallback_order (使用率% 降順) の順」で後ろに並べる。純粋"""
    known = {}
    for sid, parsed in per_pokemon.items():
        r = (parsed or {}).get("ingame_rank")
        if r is not None:
            try:
                known[sid] = int(r)
            except (TypeError, ValueError):
                continue
    out = dict(known)
    base = max(known.values(), default=0)
    k = 0
    for sid in fallback_order:
        if sid not in out:
            k += 1
            out[sid] = base + k
    for sid in per_pokemon:
        if sid not in out:
            k += 1
            out[sid] = base + k
    return out


def fetch_usage_stats(fmt: str = USAGE_TARGET_FORMAT, use_dummy: bool = False,
                       rating: int = USAGE_MIN_RATING, month: str | None = None,
                       source: str = "auto", season_number: int | None = None,
                       limit: int | None = None
                       ) -> tuple[list[PokemonUsageEntry], dict]:
    """使用率統計の取得エントリポイント。

    source:
      - "auto":     champions実データ -> 失敗時 Smogon gen9ou の順で試す (既定)
      - "champions": championsbattledata + pokedb のみ
      - "smogon":   従来の Smogon chaos JSON のみ
    use_dummy=True の場合のみダミーデータを返す(ネットワーク不要な動作確認用)。
    """
    if use_dummy:
        return fetch_dummy_usage_stats(), {"month": "dummy", "format": fmt,
                                            "rating_cutoff": rating,
                                            "number_of_battles": None,
                                            "source": "dummy", "source_url": None,
                                            "note": ""}

    if source in ("auto", "champions"):
        try:
            return fetch_champions_usage(season_number=season_number, limit=limit)
        except Exception as e:
            if source == "champions":
                raise
            print(f"[warn] champions実データの取得に失敗、Smogonへフォールバック: {e}")

    entries, meta = fetch_smogon_chaos(fmt=SMOGON_FALLBACK_FORMAT, rating=rating, month=month)
    meta.setdefault("source", "smogon")
    meta.setdefault("source_url",
                    f"{SMOGON_STATS_BASE_URL}/{meta.get('month')}/chaos/"
                    f"{SMOGON_FALLBACK_FORMAT}-{rating}.json")
    meta.setdefault("note", "fallback: champions実データ取得不可のためgen9ou統計を使用")
    return entries, meta


# ---------------------------------------------------------------------------
# 月・形式・cutoff を明示する Smogon chaos の取得 (2026-10-30 レビュー、開発者承認)
#
# fetch_usage_stats(source="smogon", fmt=...) は fmt を無視して gen9ou (SMOGON_FALLBACK_FORMAT) を取る (既定の挙動なので変えない)。
# Champions の統計を評価に使うときはこちらを使う: 形式が SMOGON_CHAMPIONS_FORMATS に無い・取れない・中身の形式や cutoff が
# 違えば SmogonChaosError で止める (gen9ou や別の月に退避しない)。取得した元 JSON はそのまま保存し、sha256・取得時刻・サイズ・
# 変換版を manifest に残す。保存済みなら取得しない (再取得しない。sha が manifest と違えば止める)。
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class SmogonChaosError(RuntimeError):
    """指定した月・形式・cutoff の chaos JSON が取れない・中身が指定と違う (退避しないで止める)"""


def smogon_chaos_url(month: str, fmt: str, cutoff: int) -> str:
    """chaos JSON の URL (純粋)"""
    return f"{SMOGON_STATS_BASE_URL}/{month}/chaos/{fmt}-{int(cutoff)}.json"


def smogon_chaos_paths(month: str, fmt: str, cutoff: int, root: Path | None = None) -> tuple[Path, Path]:
    """(元 JSON の保存先, manifest の保存先) (純粋)。root の既定はこのリポジトリ (作業ツリー) の SMOGON_USAGE_DIR"""
    base = Path(root) if root is not None else REPO_ROOT / SMOGON_USAGE_DIR
    d = base / month
    stem = f"{fmt}-{int(cutoff)}"
    return d / f"{stem}.json", d / f"{stem}.manifest.json"


def check_chaos_request(month: str, fmt: str, cutoff, formats=SMOGON_CHAMPIONS_FORMATS) -> None:
    """要求の検査 (純粋)。月は YYYY-MM、形式は formats の中、cutoff は整数。違えば SmogonChaosError"""
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise SmogonChaosError(f"月 {month!r} が YYYY-MM でない")
    if fmt not in formats:
        raise SmogonChaosError(f"形式 {fmt!r} は対象外 (SMOGON_CHAMPIONS_FORMATS = {list(formats)})")
    try:
        if int(cutoff) != cutoff or int(cutoff) < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise SmogonChaosError(f"cutoff {cutoff!r} が 0 以上の整数でない")


def check_chaos_payload(payload, fmt: str, cutoff: int) -> dict:
    """取得した chaos JSON の検査 (純粋)。info.metagame が fmt、info.cutoff が cutoff、data が空でない dict であること。
    違えば SmogonChaosError。戻り値 = info"""
    if not isinstance(payload, dict):
        raise SmogonChaosError("chaos JSON の最上位が dict でない")
    info = payload.get("info")
    data = payload.get("data")
    if not isinstance(info, dict) or not isinstance(data, dict) or not data:
        raise SmogonChaosError("chaos JSON に info / data が無い (または data が空)")
    meta = info.get("metagame")
    if meta != fmt:
        raise SmogonChaosError(f"中身の形式 {meta!r} が指定 {fmt!r} と違う (退避しない)")
    got_cut = info.get("cutoff")
    try:
        same_cut = got_cut is not None and float(got_cut) == float(cutoff)
    except (TypeError, ValueError):
        same_cut = False
    if not same_cut:
        raise SmogonChaosError(f"中身の cutoff {got_cut!r} が指定 {cutoff!r} と違う")
    return info


def chaos_manifest(month: str, fmt: str, cutoff: int, raw: bytes, info: dict, fetched_at: float, url: str) -> dict:
    """manifest の中身 (純粋)"""
    return {
        "source": "smogon",
        "url": url,
        "month": month,
        "format": fmt,
        "cutoff": int(cutoff),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "fetched_at": fetched_at,
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(fetched_at)),
        "number_of_battles": info.get("number of battles"),
        "info_metagame": info.get("metagame"),
        "info_cutoff": info.get("cutoff"),
        "convert_version": SMOGON_CONVERT_VERSION,
    }


def load_smogon_chaos_saved(month: str, fmt: str, cutoff: int, root: Path | None = None) -> tuple[dict, dict] | None:
    """保存済みの元 JSON と manifest を読む。無ければ None。sha256 が manifest と違えば SmogonChaosError"""
    raw_path, man_path = smogon_chaos_paths(month, fmt, cutoff, root)
    if not raw_path.exists() or not man_path.exists():
        return None
    raw = raw_path.read_bytes()
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    if sha != manifest.get("sha256"):
        raise SmogonChaosError(f"保存済みの {raw_path} の sha256 が manifest と違う ({sha[:16]} != {str(manifest.get('sha256'))[:16]})")
    payload = json.loads(raw.decode("utf-8"))
    check_chaos_payload(payload, fmt, cutoff)
    return payload, manifest


def fetch_smogon_chaos_exact(month: str, fmt: str, cutoff: int, root: Path | None = None,
                             http_get=None, now=None) -> tuple[dict, dict]:
    """月・形式・cutoff を明示して Smogon の chaos JSON を取る。戻り値 (元 JSON の dict, manifest)。

    - 形式が SMOGON_CHAMPIONS_FORMATS に無い・HTTP が 200 でない・中身の形式や cutoff が違う → SmogonChaosError
      (gen9ou・別の月・別の cutoff に退避しない)
    - 保存済み (root/<month>/<fmt>-<cutoff>.json と manifest) があれば取得しないでそれを返す (再取得しない)
    - http_get / now はテスト用の差し替え (既定は requests.get / time.time)
    """
    check_chaos_request(month, fmt, cutoff)
    saved = load_smogon_chaos_saved(month, fmt, cutoff, root)
    if saved is not None:
        payload, manifest = saved
        return payload, dict(manifest, reused=True)
    url = smogon_chaos_url(month, fmt, cutoff)
    getter = http_get or requests.get
    resp = getter(url, timeout=SMOGON_FETCH_TIMEOUT_SEC)
    status = getattr(resp, "status_code", None)
    if status != 200:
        raise SmogonChaosError(f"{url} が HTTP {status} (退避しない)")
    raw = resp.content
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SmogonChaosError(f"{url} が JSON でない: {e}")
    info = check_chaos_payload(payload, fmt, cutoff)
    fetched_at = float(now()) if now else time.time()
    manifest = chaos_manifest(month, fmt, cutoff, raw, info, fetched_at, url)
    raw_path, man_path = smogon_chaos_paths(month, fmt, cutoff, root)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload, dict(manifest, reused=False)
