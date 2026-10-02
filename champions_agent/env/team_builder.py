"""
DBの meta_sets(環境データ)から、自己対戦やシミュレーション用の
「現環境らしい」ポケモンパーティを確率的に生成する。

- 生成されたパーティは Showdown のチームフォーマット(パックド形式 / showdown形式)へ変換し、
  poke-env の Player にセットして使うことを想定している。
- 使用率(weight)に比例した重み付きサンプリングでポケモンを選出し、
  そのポケモンの代表的な型(技/持ち物/特性/テラス/努力値)を meta_sets から引く。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from champions_agent.config import (
    USAGE_TARGET_FORMAT, DEFAULT_REGULATION, PLAY_STYLES, DEFAULT_PLAY_STYLE,
)
from champions_agent.data import database as db
from champions_agent.data.sources.name_mapping import to_showdown_name




@dataclass
class PokemonSet:
    species: str
    ability: str | None
    item: str | None
    tera_type: str | None
    nature: str | None
    evs: str | None
    moves: list[str]

    def to_showdown_text(self) -> str:
        """poke-env / Showdown のteambuilder importable format(簡易版)へ変換する。"""
        # 持ち物なしの場合は「@」自体を書かない (「Species @」はパース不能)
        lines = [f"{self.species} @ {self.item}" if self.item else self.species]
        # チャンピオンズのランクバトルはLv50固定
        lines.append("Level: 50")
        if self.ability:
            lines.append(f"Ability: {self.ability}")
        if self.evs:
            # evs文字列 "HP/Atk/Def/SpA/SpD/Spe" を Showdown形式へ変換。
            # champions mod はEV欄を「能力ポイント (各0-32・合計66)」として
            # ネイティブ解釈するため、CBD由来の生の値をそのまま渡す。
            # 安全のため 各32/合計66 を超えた分だけ削る
            labels = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
            values = []
            for v in self.evs.split("/"):
                try:
                    values.append(max(0, min(32, int(v))))
                except ValueError:
                    values.append(0)
            while sum(values) > 66:
                i = values.index(max(values))
                values[i] -= sum(values) - 66 if values[i] >= sum(values) - 66 else 1
            ev_parts = [f"{v} {l}" for l, v in zip(labels, values) if v]
            if ev_parts:
                lines.append("EVs: " + " / ".join(ev_parts))
        if self.nature:
            lines.append(f"{self.nature.capitalize()} Nature")
        if self.tera_type:
            lines.append(f"Tera Type: {self.tera_type.capitalize()}")
        for m in self.moves:
            if m:
                lines.append(f"- {m}")
        return "\n".join(lines)


def _fetch_meta_pool(conn, snapshot_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT pokemon_name, ability_name, item_name, tera_type,
               nature, evs, move1, move2, move3, move4, weight
        FROM meta_sets
        WHERE snapshot_id = ? AND move1 IS NOT NULL
        """,
        (snapshot_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_move_pool(conn, snapshot_id: int, limit: int = 12) -> dict:
    """{species_id: [技 (使用率順)]}: learnset で落ちた技の埋め合わせ用"""
    out: dict = {}
    try:
        rows = conn.execute(
            "SELECT pokemon_name, move_name, usage_percent FROM move_usage WHERE snapshot_id = ? "
            "ORDER BY pokemon_name, usage_percent DESC", (snapshot_id,)).fetchall()
    except Exception:
        return out
    for r in rows:
        lst = out.setdefault(r[0], [])
        if len(lst) < limit:
            lst.append(r[1])
    return out


def _fetch_fallback_items(conn, snapshot_id: int) -> list[str]:
    """アイテム重複解消用の代替候補を、実使用率DB (=champions実在確定) から取る"""
    rows = conn.execute(
        """
        SELECT item_name, SUM(usage_percent) AS total
        FROM item_usage WHERE snapshot_id = ?
        GROUP BY item_name ORDER BY total DESC LIMIT 30
        """,
        (snapshot_id,),
    ).fetchall()
    return [r["item_name"] for r in rows]


def _fetch_role_scores(conn, snapshot_id: int) -> dict[str, dict[str, float]]:
    """pokemon_name -> {role: score} のマップを返す。"""
    rows = conn.execute(
        "SELECT pokemon_name, role, score FROM pokemon_role_tags WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    result: dict[str, dict[str, float]] = {}
    for r in rows:
        result.setdefault(r["pokemon_name"], {})[r["role"]] = r["score"]
    return result


def _apply_play_style_bias(pool: list[dict], role_scores: dict[str, dict[str, float]],
                            play_style: str) -> list[float]:
    """性格(PlayStyle)の役割重み倍率を反映した、サンプリング用の重みリストを返す。

    基本重み(使用率weight)に対し、各役割スコア×倍率の合計を乗算係数として掛け合わせる。
    どの役割にも該当しない(スコア0)ポケモンは倍率1.0のまま(基本重みのみ)。
    """
    style = PLAY_STYLES.get(play_style, PLAY_STYLES[DEFAULT_PLAY_STYLE])
    multipliers = style.role_weight_multipliers

    weights = []
    for p in pool:
        base = max(p["weight"], 0.01)
        roles = role_scores.get(p["pokemon_name"], {})
        if not roles:
            weights.append(base)
            continue
        # 役割スコアで重み付けした倍率の加重平均(1.0を中心に増減)
        bias = 1.0
        total_score = sum(roles.values())
        if total_score > 0:
            bias = sum(score * multipliers.get(role, 1.0) for role, score in roles.items()) / total_score
        weights.append(base * bias)
    return weights


def build_random_party(size: int = 6, fmt: str = USAGE_TARGET_FORMAT,
                        source: str | None = None, rng: random.Random | None = None,
                        play_style: str = DEFAULT_PLAY_STYLE,
                        ) -> list[PokemonSet]:

    """使用率(weight)と性格(play_style)の役割バイアスに応じた重み付きサンプリングで、
    size体のパーティを生成する。

    play_style: config.PLAY_STYLES のキー('offense'/'cycle'/'stall'/'balance')。
                役割タグ(data/role_tagger.py)が未生成の場合は使用率のみで選出される。
    poke-env で自己対戦相手のチームとして利用する想定。
    """
    rng = rng or random.Random()

    with db.get_connection() as conn:
        snapshot_id = db.latest_snapshot_id(conn, source=source, fmt=fmt)
        if snapshot_id is None:
            raise RuntimeError(
                f"usage_snapshot が見つかりません(source={source}, format={fmt})。"
                "先に data.ingest / data.build_meta を実行してください。"
            )
        pool = _fetch_meta_pool(conn, snapshot_id)
        role_scores = _fetch_role_scores(conn, snapshot_id)
        fallback_items = _fetch_fallback_items(conn, snapshot_id)
        move_pool = _fetch_move_pool(conn, snapshot_id)
    # 技は champions mod の learnset で検査し、覚えない技は使用率上位の合法な技で埋める (2026-09-13 障害:
    # cbd の M-C データにある「メテオアサルト」を mod が拒否し、学習が毎サイクル止まった)。合法な技が無い型は候補から外す
    from champions_agent.env.legality import fill_moves
    legal_pool = []
    for r in pool:
        r = dict(r)
        moves = fill_moves(r["pokemon_name"], [r["move1"], r["move2"], r["move3"], r["move4"]],
                           move_pool.get(r["pokemon_name"], []))
        if not moves:
            continue
        for i in range(4):
            r[f"move{i + 1}"] = moves[i] if i < len(moves) else None
        legal_pool.append(r)
    pool = legal_pool

    if len(pool) < size:
        raise RuntimeError(
            f"meta_sets の候補数({len(pool)})がパーティサイズ({size})未満です。"
            "ingest対象のポケモン数を増やしてください。"
        )

    banned = set(DEFAULT_REGULATION.banned_species)
    pool = [p for p in pool if p["pokemon_name"] not in banned]

    weights = _apply_play_style_bias(pool, role_scores, play_style)
    chosen = rng.choices(pool, weights=weights, k=size)

    # Species Clause対応: ベース種族単位で重複を排除する
    # (rotomheat/rotomwash や gengar/gengarmega は同一種族としてカウントされる)
    seen = set()
    result = []
    for c in chosen:
        key = _base_species_key(c["pokemon_name"])
        if key in seen:
            continue
        seen.add(key)
        result.append(c)
    attempts = 0
    while len(result) < size and attempts < 200:
        attempts += 1
        candidate = rng.choices(pool, weights=weights, k=1)[0]
        key = _base_species_key(candidate["pokemon_name"])
        if key not in seen:
            seen.add(key)
            result.append(candidate)

    sets = [
        PokemonSet(
            species=to_showdown_name(_sanitize_species(r["pokemon_name"])),
            ability=r["ability_name"],
            item=_sanitize_item(r["item_name"]),
            tera_type=r["tera_type"],
            nature=r["nature"],
            evs=r["evs"],
            moves=[m for m in (r["move1"], r["move2"], r["move3"], r["move4"]) if m],
        )
        for r in result
    ]
    _enforce_item_clause(sets, fallback_items)
    return sets



# --- champions形式向けサニタイズ ---------------------------------------------
# - メガ形態の種族エントリ (CBDはメガを独立ページで集計) はベース種+メガストーンで表現
# - アイテムはchampions modの実在IDのみ許可 (新メガストーン dragoninite 等は実在)
# - Flat Rules の Item Clause 用にチーム内アイテム重複を解消する
_LEGAL_ITEM_IDS = None

# アイテム重複時の代替候補 (使用率DBから動的に取得。これは最終フォールバック)
_FALLBACK_ITEMS = ["leftovers", "sitrusberry", "focussash", "lumberry"]


def _legal_item_ids() -> set:
    """championsのmod items.ts + 本体items.ts + jp_names からアイテムIDを収集する"""
    global _LEGAL_ITEM_IDS
    if _LEGAL_ITEM_IDS is None:
        import json
        import re as _re
        from pathlib import Path
        ids: set = set()
        repo = Path(__file__).resolve().parents[2]
        for ts in (repo / "pokemon-showdown" / "data" / "mods" / "champions" / "items.ts",
                   repo / "pokemon-showdown" / "data" / "items.ts"):
            try:
                ids |= set(_re.findall(r"^\t(\w+): \{", ts.read_text(), _re.M))
            except Exception:
                pass
        # 注意: jp_names.json は合法集合に含めない。あれは表示名解決用の
        # 辞書であり、シムに存在しないIDが混ざるとバリデーション却下で
        # env起動が全滅する (2026-07-23 に発生: 合成ストーンIDが混入し
        # 学習サイクルが空回りした)
        _LEGAL_ITEM_IDS = ids
    return _LEGAL_ITEM_IDS


_AVAILABLE_ITEM_IDS = None


def parse_item_status(text: str) -> dict:
    """items.ts の本文 → {item_id: isNonstandard の値 (None = 標準)}。1 タブの `id: {` ブロックごとに isNonstandard を読む (純粋)"""
    import re as _re
    out: dict = {}
    cur = None
    for line in text.splitlines():
        m = _re.match(r"^\t(\w+): \{", line)
        if m:
            cur = m.group(1)
            out[cur] = None
            continue
        if cur is None or not line.startswith("\t\t"):
            continue
        m = _re.match(r'^\t\tisNonstandard: (?:"([A-Za-z]+)"|null)', line)
        if m:
            out[cur] = m.group(1)
    return out


def available_items(base: dict, mod: dict) -> set:
    """Champions で使える持ち物: 本体で標準 (isNonstandard 無し) のものに mod の指定を重ねる (mod の null = 使える、
    "Past" 等 = 使えない)。本体に無く mod にだけある id はその mod の指定で決める (純粋)"""
    out: set = set()
    for item, status in base.items():
        st = mod[item] if item in mod else status
        if st is None:
            out.add(item)
    for item, status in mod.items():
        if item not in base and status is None:
            out.add(item)
    return out


def _available_item_ids() -> set:
    """Champions で実際に使える持ち物 id (champions mod で isNonstandard: "Past" のこだわりハチマキ / メガネ / じゃくてんほけん /
    とつげきチョッキ等を除く。2026-10-02: 生成型のこだわりハチマキが validate-team で落ちた)。読めなければ _legal_item_ids"""
    global _AVAILABLE_ITEM_IDS
    if _AVAILABLE_ITEM_IDS is None:
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        try:
            base = parse_item_status((repo / "pokemon-showdown" / "data" / "items.ts").read_text())
            mod = parse_item_status((repo / "pokemon-showdown" / "data" / "mods" / "champions" / "items.ts").read_text())
            _AVAILABLE_ITEM_IDS = available_items(base, mod)
        except Exception:
            _AVAILABLE_ITEM_IDS = set(_legal_item_ids())
    return _AVAILABLE_ITEM_IDS


def _sanitize_species(name: str) -> str:
    for suf in ("megax", "megay", "megaz", "mega"):
        if name.endswith(suf) and len(name) > len(suf) + 2:
            return name[: -len(suf)]
    return name


_BASE_SPECIES_MAP = None


def _base_species_key(name: str) -> str:
    """Species Clause判定用のベース種族キー (ロトムフォーム違い・メガ等を同一視)"""
    global _BASE_SPECIES_MAP
    if _BASE_SPECIES_MAP is None:
        import json
        import re as _re
        from pathlib import Path
        _BASE_SPECIES_MAP = {}
        dex_path = Path(__file__).resolve().parents[1] / "data" / "champions_dex.json"
        try:
            species = json.loads(dex_path.read_text()).get("species", {})
            for sid, entry in species.items():
                base = entry.get("baseSpecies") or entry.get("name") or sid
                _BASE_SPECIES_MAP[sid] = _re.sub(r"[^a-z0-9]", "", base.lower())
        except Exception:
            pass
    key = _sanitize_species(name)
    return _BASE_SPECIES_MAP.get(key, key)


def _sanitize_item(item: str | None) -> str | None:
    if not item:
        return item
    legal = _legal_item_ids()
    if legal and item not in legal:
        return "leftovers"
    return item


_SPECIES_ITEMS_CACHE: dict | None = None


def _species_item_alternatives() -> dict:
    """species_id -> [使用率降順のitem_id] (最新スナップショット、遅延ロード)"""
    global _SPECIES_ITEMS_CACHE
    if _SPECIES_ITEMS_CACHE is None:
        out: dict = {}
        try:
            with db.get_connection() as conn:
                snap = db.latest_snapshot_id(conn)
                if snap:
                    for r in conn.execute(
                            """SELECT pokemon_name, item_name, usage_percent
                               FROM item_usage WHERE snapshot_id = ?
                               ORDER BY usage_percent DESC""", (snap,)):
                        it = _sanitize_item(r["item_name"])
                        if it:
                            out.setdefault(str(r["pokemon_name"]), []).append(it)
        except Exception:
            pass
        _SPECIES_ITEMS_CACHE = out
    return _SPECIES_ITEMS_CACHE


def _species_usage_key(species: str) -> str:
    """PokemonSet.species (Showdown表示名) -> item_usage の pokemon_name キー"""
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", (species or "").lower())


def _enforce_item_clause(sets: list[PokemonSet], fallback_items: list[str] | None = None) -> None:
    """Flat Rules (Item Clause = 1) のためチーム内のアイテム重複を解消する。

    衝突時はまず**その種族自身の使用率次点**から未使用品を選ぶ (実戦で
    使われる型の範囲に収める)。種族の候補が尽きたときだけ全体人気の
    フォールバックへ落とす (2026-08-30 第10回: 全体人気リストが先行して
    いたため、オボンが衝突したカバルドンに種族の使用実績が無い
    こだわりスカーフが充当された。種族次点は たべのこし 28.8% だった)
    """
    candidates = (fallback_items or []) + _FALLBACK_ITEMS
    per_species = _species_item_alternatives()
    used: set = set()
    for s in sets:
        if s.item and s.item in used:
            own = per_species.get(_species_usage_key(s.species)) or []
            s.item = next((f for f in own if f not in used),
                          next((f for f in candidates if f not in used), None))
        if s.item:
            used.add(s.item)


def build_random_team_text(size: int = 6, **kwargs) -> str:
    """poke-env の Teambuilder(ShowdownTeam)にそのまま渡せるテキスト形式で返す。"""
    party = build_random_party(size=size, **kwargs)
    return "\n\n".join(p.to_showdown_text() for p in party)


try:
    from poke_env.teambuilder import Teambuilder as _PokeEnvTeambuilder
except Exception:  # poke-env未導入環境 (データ収集のみ) でもimport可能にする
    _PokeEnvTeambuilder = object


class ChampionsTeambuilder(_PokeEnvTeambuilder):
    """毎バトル新しいメタチームを生成する poke-env Teambuilder。

    ConstantTeambuilder と違い、バトルごとに使用率メタから確率生成するため、
    学習が特定チームに過学習しない。style_pool を渡すとバトルごとに
    性格 (プレイスタイル) もランダムに切り替わる。
    """

    def __init__(self, size: int = 6, play_style: str | None = None,
                 style_pool: list[str] | None = None,
                 rng: random.Random | None = None):
        self.size = size
        self.play_style = play_style
        self.style_pool = style_pool
        self.rng = rng or random.Random()

    def yield_team(self) -> str:
        style = self.play_style
        if style is None:
            pool = self.style_pool or list(PLAY_STYLES.keys())
            style = self.rng.choice(pool)
        text = build_random_team_text(size=self.size, play_style=style)
        mons = self.parse_showdown_team(text)
        return self.join_team(mons)


if __name__ == "__main__":
    print(build_random_team_text(size=6))
