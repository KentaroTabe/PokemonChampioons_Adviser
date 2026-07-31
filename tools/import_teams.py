"""外部から集めた構築を、収集・学習で使えるチームプールへ取り込む。

    python -m tools.import_teams data/teams/inbox/*.txt
    python -m tools.import_teams --list
    python -m tools.import_teams --dry-run data/teams/inbox/note_20260729.txt

■ 入力形式
1行1体で「種族名」または「種族名 @ 持ち物」。日本語名・英語名・showdown IDの
いずれでも良い。6体そろうと1チームとして確定する。

    # source: https://example.com/articles/12345
    カイリュー @ こだわりハチマキ
    ミミッキュ
    ガブリアス @ ヤチェのみ
    ...

Showdownのエクスポート形式 (Ability:/EVs:/- 技 などを含む) をそのまま貼っても、
種族行以外は読み飛ばすので動く。`===` 行と `# source:` 行はチームの区切りも兼ねる。

■ 技・特性・性格・努力値
ラダー構築 (ranked_teams) と同じく meta_sets のその種族の最多構成で補完する。
記事ごとの調整を再現しないのは、外部構築とラダー構築を同じ土俵に載せるため。
選出モデルの特徴量は種族埋め込みが主なので、この粒度で足りる。

■ 注意
構築記事の自動収集を禁じているサイトが多い。本ツールは「手元に用意した
テキストを取り込む」だけで、取得は行わない。取得元は source として記録する。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from champions_agent.env.team_builder import _base_species_key

STORE_PATH = (Path(__file__).resolve().parents[1] / "champions_agent" /
              "data" / "teams" / "external_teams.json")

# Showdownエクスポートに含まれる、種族行ではない行
_SKIP_PREFIX = ("-", "Ability:", "Level:", "EVs:", "IVs:", "Shiny:",
                "Happiness:", "Tera Type:", "とくせい", "レベル", "努力値")
_NATURE_RE = re.compile(r"\bNature\b|性格")

_JA2ID: dict | None = None
_ITEM_JA2ID: dict | None = None
_DEX: set | None = None
_META: set | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _load_maps() -> None:
    global _JA2ID, _ITEM_JA2ID, _DEX, _META
    if _JA2ID is not None:
        return
    from vision.normalize import JP_NAMES_PATH
    raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
    _JA2ID = {ja: v["id"] for ja, v in raw.get("species", {}).items()}
    _ITEM_JA2ID = dict(raw.get("items", {}))

    dex_path = (Path(__file__).resolve().parents[1] / "champions_agent" /
                "data" / "champions_dex.json")
    species = json.loads(dex_path.read_text(encoding="utf-8")).get("species", {})
    _DEX = {_norm(k) for k in species}
    for sid, entry in species.items():
        name = entry.get("name")
        if name:
            _DEX.add(_norm(name))

    # 技構成を補完できる種族 (meta_setsに無いと ranked_teams と同様に組めない)
    from champions_agent.data import database as db
    with db.get_connection() as conn:
        snap = db.latest_snapshot_id(conn)
        rows = conn.execute(
            "SELECT pokemon_name FROM meta_sets "
            "WHERE snapshot_id = ? AND move1 IS NOT NULL", (snap,)).fetchall()
    _META = {r["pokemon_name"] for r in rows}


def resolve_species(text: str) -> str | None:
    """日本語名/英語名/showdown ID -> showdown ID (champions_dexにあるもの)"""
    _load_maps()
    t = text.strip()
    if t in _JA2ID:
        return _JA2ID[t]
    n = _norm(t)
    return n if n in _DEX else None


def resolve_item(text: str) -> str | None:
    _load_maps()
    t = text.strip()
    if not t or t in ("なし", "none", "-"):
        return None
    return _ITEM_JA2ID.get(t) or _norm(t) or None


def _is_mega_stone(item_id: str | None) -> bool:
    return bool(item_id) and item_id.endswith("ite") and item_id != "eviolite"


def parse_file(path: Path) -> list:
    """テキスト -> [{"source":..., "species":[...], "items":[...]}]"""
    teams, cur, source = [], [], ""

    def flush_partial() -> None:
        """区切りに来たとき、6体未満の書きかけは捨てる (誤結合を防ぐ)"""
        cur.clear()

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("==="):
            m = re.search(r"(https?://\S+)", line)
            if m:
                source = m.group(1)
            flush_partial()
            continue
        if line.startswith(_SKIP_PREFIX) or _NATURE_RE.search(line):
            continue

        name, _, item_text = line.partition("@")
        sid = resolve_species(name)
        if sid is None:
            continue
        cur.append((sid, resolve_item(item_text)))
        if len(cur) == 6:
            teams.append({"source": source,
                          "species": [s for s, _ in cur],
                          "items": [i for _, i in cur]})
            cur.clear()
    return teams


def validate(team: dict) -> str | None:
    """不採用の理由を返す (採用できるなら None)"""
    _load_maps()
    sp = team["species"]
    if len(sp) != 6:
        return f"6体ではない ({len(sp)}体)"
    bases = [_base_species_key(s) for s in sp]
    if len(set(bases)) != 6:
        return "種族が重複 (Species Clause)"
    missing = [s for s in sp if s not in _META]
    if missing:
        return f"技構成を補完できない種族: {'/'.join(missing)}"
    n_mega = sum(1 for i in team["items"] if _is_mega_stone(i))
    if n_mega > 1:
        return f"メガストーンが{n_mega}個"
    return None


def team_key(species: list) -> str:
    return "|".join(sorted(_base_species_key(s) for s in species))


def load_store() -> dict:
    if STORE_PATH.exists():
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    return {"_note": "外部構築 (tools/import_teams.py で取り込み)", "teams": []}


def existing_keys(store: dict) -> set:
    """既存の外部構築 + ラダー構築のキー (重複取り込みを防ぐ)"""
    keys = {team_key(t["species"]) for t in store["teams"]}
    from champions_agent.env import ranked_teams as rt
    for t in rt._load_ladder_teams():
        sp = []
        for m in t.get("team", []):
            sid = resolve_species(m.get("pokemon", ""))
            if sid:
                sp.append(sid)
        if len(sp) == 6:
            keys.add(team_key(sp))
    return keys


def main() -> None:
    ap = argparse.ArgumentParser(description="外部構築の取り込み")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="保存せず結果だけ表示")
    ap.add_argument("--list", action="store_true", help="取り込み済みの一覧")
    args = ap.parse_args()

    store = load_store()
    if args.list:
        print(f"■ 取り込み済み {len(store['teams'])}件 → {STORE_PATH}")
        from collections import Counter
        src = Counter(t.get("source") or "(出典なし)" for t in store["teams"])
        for s, c in src.most_common():
            print(f"  {c:>4}件  {s}")
        return
    if not args.files:
        ap.error("取り込むファイルを指定してください (--list で一覧)")

    known = existing_keys(store)
    added, rejected, dup = [], [], 0
    for path in args.files:
        if not path.exists():
            print(f"⚠ ファイルがありません: {path}")
            continue
        for t in parse_file(path):
            reason = validate(t)
            if reason:
                rejected.append((t["species"], reason))
                continue
            k = team_key(t["species"])
            if k in known:
                dup += 1
                continue
            known.add(k)
            t["added"] = date.today().isoformat()
            t["file"] = path.name
            added.append(t)

    print(f"■ 取り込み結果")
    print(f"  採用    : {len(added)}")
    print(f"  重複除外: {dup}")
    print(f"  不採用  : {len(rejected)}")
    from collections import Counter
    for reason, c in Counter(r for _, r in rejected).most_common(10):
        print(f"    x{c}: {reason}")

    if args.dry_run:
        print("\n(--dry-run のため保存していません)")
        return
    store["teams"].extend(added)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n保存: {STORE_PATH} (合計 {len(store['teams'])}件)")


if __name__ == "__main__":
    main()
