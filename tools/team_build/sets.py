"""型ライブラリ (S6): 種族ごとの合法な型候補を列挙 → 制約 → 評価関数 → 上位。

- 代表型: meta_sets (整合規則つき) をそのまま候補 0 とする
- 代替: 使用率 BUILD_SET_ALT_MIN_PCT 以上の持ち物 / 技 / 性格×配分の単独入替 (組合せ爆発を避ける)
- 制約: 性格×配分の整合 (build_meta.nature_fits)、チーム内の持ち物重複 (アイテムクローズ)、
  Showdown validate-team (レギュレーションの合法性)
- 評価: Interaction Matrix の被覆 (脅威ごとの max(lead, switch_in, revenge) の和)
LLM は「この個体に speed control を担当させる」のような役割指定までで、型は探索器が決める。
"""
from __future__ import annotations

import itertools
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_MAX_MEGA_STONES, BUILD_SET_USAGE_WEIGHT, USAGE_TARGET_FORMAT
from champions_agent.data import database as db
from champions_agent.data.build_meta import move_categories, nature_fits, parse_points

REPO = Path(__file__).resolve().parent.parent.parent
SHOWDOWN_DIR = REPO / "pokemon-showdown"
ALT_MIN_PCT = 5.0          # 代替候補に採る最低使用率 (%) — config 化候補
MAX_ALTERNATIVES = 12      # 種族ごとの型候補の上限
REP_MARGIN = 0.05          # 代替が代表型をこの被覆差以上で上回らなければ代表型を採る
CHOICE_ITEMS = ("choicescarf", "choiceband", "choicespecs")


@lru_cache(maxsize=1)
def setup_move_ids() -> frozenset:
    """積み技 (自分の能力を上げる変化技) の id: advisor/data/boost_moves.json の self に上昇のある変化技 +
    advisor.search.SETUP_MOVES (のろい等、表に無いもの)。2026-10-02 ユーザー指摘: 常識フィルタの手書きの一覧に
    ちょうのまい / からをやぶる / こうそくいどう / はらだいこ 等が無かった → データから作る"""
    from advisor.dex import _boost_moves, get_dex
    from advisor.search import SETUP_MOVES
    from champions_agent.config import BUILD_GEN_SETUP_BOOSTS
    dex = get_dex()
    out = set(SETUP_MOVES)
    rows = list((_boost_moves().get("self") or {}).items()) + list(BUILD_GEN_SETUP_BOOSTS.items())
    for m, delta in rows:
        if any(int(v or 0) > 0 for v in (delta or {}).values()) \
                and str((dex.move(m) or {}).get("category") or "").lower() == "status":
            out.add(m)
    return frozenset(out)


@lru_cache(maxsize=1)
def choice_lock_moves() -> frozenset:
    """こだわり系の持ち物と組ませない変化技: 積み技 (setup_move_ids) + config BUILD_SET_CHOICE_LOCK_MOVES
    (設置 / 回復 / まもる / みがわり)"""
    from champions_agent.config import BUILD_SET_CHOICE_LOCK_MOVES
    return setup_move_ids() | frozenset(BUILD_SET_CHOICE_LOCK_MOVES)


@dataclass
class SetCandidate:
    species_id: str
    ability: Optional[str]
    item: Optional[str]
    nature: Optional[str]
    evs: Optional[str]           # "2/32/0/0/0/32" (能力ポイント)
    moves: list
    source: str = "representative"   # representative / alt:item / alt:move / alt:spread
    score: float = 0.0               # 被覆 (Interaction Matrix)
    notes: list = field(default_factory=list)
    usage_gap: float = 0.0           # 代表型との使用率差 (0..1、代替だけ > 0)。使用率の事前分布として採点から引く
    adj: float = 0.0                 # score − BUILD_SET_USAGE_WEIGHT × usage_gap (order_candidates が入れる)

    def as_row(self) -> dict:
        return {"item": self.item, "nature": self.nature, "evs": self.evs, "moves": list(self.moves),
                "ability": self.ability}

    def key(self) -> tuple:
        return (self.species_id, self.ability, self.item, self.nature, self.evs, tuple(self.moves))


_STAT_KEYS = {"hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe",
              "h": "hp", "a": "atk", "b": "def", "c": "spa", "d": "spd", "s": "spe"}
_STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")


def _toid(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def default_category_of():
    """技 id → 分類 (physical/special/status、小文字) を図鑑から引く callable"""
    from advisor.dex import get_dex
    dex = get_dex()
    return lambda m: str((dex.move(m) or {}).get("category") or "").lower()


def default_setup_moves():
    from advisor.search import SETUP_MOVES
    return SETUP_MOVES


def inject_move(moves: list, move: str, category_of=None, setup_moves=()) -> tuple:
    """技を 1 本差し込む (既にあればそのまま)。4 本未満なら足す。差し替え枠は
    (1) 積み技でない変化技の末尾 → (2) 変化技の末尾 → (3) 末尾。戻り値 (new_moves, 差し替えた技 or None)。純粋"""
    ms = list(moves)
    if move in ms:
        return ms, None
    if len(ms) < 4:
        return ms + [move], None
    cats = [(category_of(m) if category_of else "") for m in ms]
    idx = next((k for k in range(len(ms) - 1, -1, -1) if cats[k] == "status" and ms[k] not in setup_moves), None)
    if idx is None:
        idx = next((k for k in range(len(ms) - 1, -1, -1) if cats[k] == "status"), len(ms) - 1)
    replaced = ms[idx]
    ms[idx] = move
    return ms, replaced


def apply_required_moves(c: "SetCandidate", moves, category_of=None, setup_moves=()) -> "SetCandidate":
    """必須技 (技 + ポケモンの指定) を型に差し込む。無ければそのまま。注記 req:<技><-<差し替えた技>。純粋"""
    ms, notes = list(c.moves), []
    for m in moves or []:
        if m in ms:
            continue
        ms, replaced = inject_move(ms, m, category_of, setup_moves)
        notes.append(f"req:{m}<-{replaced}")
    if not notes:
        return c
    return SetCandidate(c.species_id, c.ability, c.item, c.nature, c.evs, ms, c.source + "+req", c.score,
                        list(c.notes) + notes, c.usage_gap, c.adj)


def parse_set_text(text: str) -> dict:
    """Showdown 形式の型本文 → {species_id: SetCandidate (source="custom")}。EVs 行はこのプロジェクトの規約どおり
    能力ポイント (0-32) として読む (H/A/B/C/D/S の順の文字列に直す)。純粋"""
    import re
    out: dict = {}
    for block in (text or "").strip().split("\n\n"):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        head = lines[0]
        name, _, item = head.partition("@")
        sid = _toid(name)
        if not sid:
            continue
        ability, nature, moves = None, None, []
        pts = {k: 0 for k in _STAT_ORDER}
        for ln in lines[1:]:
            if ln.startswith("Ability:"):
                ability = _toid(ln.split(":", 1)[1])
            elif ln.startswith("EVs:"):
                for part in ln.split(":", 1)[1].split("/"):
                    m = re.match(r"\s*(\d+)\s+(\w+)", part)
                    if m and m.group(2).lower() in _STAT_KEYS:
                        pts[_STAT_KEYS[m.group(2).lower()]] = int(m.group(1))
            elif ln.endswith("Nature"):
                nature = ln.split()[0].lower()
            elif ln.startswith("- "):
                moves.append(_toid(ln[2:]))
        evs = "/".join(str(pts[k]) for k in _STAT_ORDER) if any(pts.values()) else None
        out[sid] = SetCandidate(sid, ability, _toid(item) or None, nature, evs, moves, "custom")
    return out


def candidate_from_row(species_id: str, row: dict) -> "SetCandidate":
    """request.json に保存した型 (item/ability/nature/evs/moves) → SetCandidate (source="custom")"""
    return SetCandidate(species_id, row.get("ability"), row.get("item"), row.get("nature"), row.get("evs"),
                        list(row.get("moves") or []), row.get("source") or "custom")


def legal_item(item: Optional[str]) -> bool:
    """Champions で使える持ち物 id か (2026-09-13: cbd の未対応 id "unknownitem542" が代表型に入り validate-team で不合法になった。
    2026-10-02: champions mod で isNonstandard "Past" のこだわりハチマキ / メガネ / じゃくてんほけん等も除く)。
    持ち物なし (None/空) は True"""
    if not item:
        return True
    try:
        from champions_agent.env.team_builder import _available_item_ids
        ids = _available_item_ids()
    except Exception:
        return not str(item).startswith("unknownitem")
    return item in ids if ids else not str(item).startswith("unknownitem")


def _rows(conn, table: str, col: str, snapshot_id: int, name: str, min_pct: float) -> list:
    rows = [(r[0], r[1]) for r in conn.execute(
        f"SELECT {col}, usage_percent FROM {table} WHERE snapshot_id=? AND pokemon_name=? "
        f"AND usage_percent >= ? ORDER BY usage_percent DESC", (snapshot_id, name, min_pct))]
    if table == "item_usage":
        rows = [(i, p) for i, p in rows if legal_item(i)]
    return rows


def move_usage_pct(conn, snapshot_id: int, species_id: str) -> dict:
    """その種の技の使用率 {move_id: usage_percent} (無ければ {})。生成型の補助技の並べ替えに使う"""
    out: dict = {}
    for m, p in _rows(conn, "move_usage", "move_name", snapshot_id, species_id, 0.0):
        out[m] = max(out.get(m, 0.0), float(p or 0.0))
    return out


def representative_set(conn, snapshot_id: int, species_id: str) -> Optional[SetCandidate]:
    r = conn.execute(
        "SELECT ability_name, item_name, nature, evs, move1, move2, move3, move4 FROM meta_sets "
        "WHERE snapshot_id=? AND pokemon_name=?", (snapshot_id, species_id)).fetchone()
    if r is None:
        return None
    moves = [m for m in (r[4], r[5], r[6], r[7]) if m]
    item = r[1]
    if not legal_item(item):
        # 未対応の持ち物 id (unknownitem…) は、その種の使用率上位の合法な持ち物に替える
        alt = _rows(conn, "item_usage", "item_name", snapshot_id, species_id, 0.0)
        item = alt[0][0] if alt else None
    return SetCandidate(species_id, r[0], item, r[2], r[3], moves, "representative")


def base_set(conn, snapshot_id: int, species_id: str, custom: Optional["SetCandidate"] = None, required=None,
             category_of=None, setup_moves=(), generated=None) -> Optional["SetCandidate"]:
    """その種の基本の型: 指定の型 (custom) → 代表型 → 生成型 (generated: learnset から作った型、代表型が無い種の補完) の順。
    必須技 (required) があれば差し込む"""
    rep = custom if custom is not None else representative_set(conn, snapshot_id, species_id)
    if rep is None and generated:
        rep = generated[0]
    if rep is None:
        return None
    return apply_required_moves(rep, required, category_of, setup_moves) if required else rep


def finalize_candidates(cands: list, required=None, category_of=None, setup_moves=()) -> list:
    """必須技を全候補に差し込み、同じ型になったものは 1 つにまとめる (順序は保つ)。純粋"""
    if not required:
        return list(cands)
    out, seen = [], set()
    for c in cands:
        c2 = apply_required_moves(c, required, category_of, setup_moves)
        if c2.key() in seen:
            continue
        seen.add(c2.key())
        out.append(c2)
    return out


def enumerate_sets(conn, snapshot_id: int, species_id: str, min_pct: float = ALT_MIN_PCT,
                   limit: int = MAX_ALTERNATIVES, custom: Optional["SetCandidate"] = None, required=None,
                   category_of=None, setup_moves=(), generated=None) -> list:
    """代表型 + 単独入替の代替 (持ち物 / 技 1 本 / 性格×配分)。整合しない性格×配分は落とす。
    custom (指定の型) があれば使用率データは見ずその型だけを返す (使用率に振り回されないため)。
    generated (learnset から生成した型) は代表型が無ければそれを候補にし、あれば代替の後ろに加える
    (使用率差の罰則 usage_gap = 代表型に無い技の使用率差の平均)。required (必須技) は全候補に差し込む"""
    if custom is not None:
        return finalize_candidates([custom], required, category_of, setup_moves)
    rep = representative_set(conn, snapshot_id, species_id)
    if rep is None:
        return finalize_candidates(list(generated or []), required, category_of, setup_moves)
    if required:
        category_of = category_of or default_category_of()
        setup_moves = setup_moves or default_setup_moves()
    out = [rep]
    seen = {rep.key()}
    item_rows = _rows(conn, "item_usage", "item_name", snapshot_id, species_id, min_pct)
    move_rows = _rows(conn, "move_usage", "move_name", snapshot_id, species_id, min_pct)
    nature_rows = [(n, p) for n, p in _rows(conn, "spread_usage", "nature", snapshot_id, species_id, min_pct) if n]
    spread_rows = [(e, p) for e, p in _rows(conn, "spread_usage", "evs", snapshot_id, species_id, min_pct) if e]
    items = [i for i, _ in item_rows]
    moves = [m for m, _ in move_rows]
    natures = [n for n, _ in nature_rows]
    spreads = [e for e, _ in spread_rows]

    def pct_map(rows: list) -> dict:
        d: dict = {}
        for k, p in rows:
            d[k] = max(d.get(k, 0.0), float(p or 0.0))
        return d

    item_pct, move_pct, nature_pct, spread_pct = pct_map(item_rows), pct_map(move_rows), pct_map(nature_rows), pct_map(spread_rows)

    def gap(pct: dict, rep_key, alt_key) -> float:
        return max(0.0, pct.get(rep_key, 0.0) - pct.get(alt_key, 0.0)) / 100.0

    cats = move_categories(rep.moves)

    def add(c: SetCandidate) -> None:
        if c.key() not in seen and len(out) < limit:
            seen.add(c.key())
            out.append(c)

    stones = _mega_stones()
    rep_is_mega = (rep.item or "") in stones
    for it in items:
        if it == rep.item:
            continue
        if it in stones and not rep_is_mega:
            continue      # メガ石は代表型がメガ石のときだけ (メガ後で評価すると常に強く見えて偏る)
        # 代表型がメガ石なら、非メガ石の代替も残す (1 並びにメガ石 2 個のときの差し替え先)
        cand = SetCandidate(species_id, rep.ability, it, rep.nature, rep.evs, list(rep.moves), "alt:item",
                            usage_gap=gap(item_pct, rep.item, it))
        if not set_sanity(cand):
            add(cand)
    for mv in moves:
        if mv in rep.moves:
            continue
        for k in range(len(rep.moves)):
            new_moves = list(rep.moves)
            new_moves[k] = mv
            cand = SetCandidate(species_id, rep.ability, rep.item, rep.nature, rep.evs, new_moves, "alt:move",
                                usage_gap=gap(move_pct, rep.moves[k], mv))
            if not set_sanity(cand):
                add(cand)
    for nv in natures:
        for ev in spreads:
            if (nv, ev) == (rep.nature, rep.evs) or not nature_fits(nv, ev, cats):
                continue
            add(SetCandidate(species_id, rep.ability, rep.item, nv, ev, list(rep.moves), "alt:spread",
                             usage_gap=(gap(nature_pct, rep.nature, nv) + gap(spread_pct, rep.evs, ev)) / 2.0))
    if generated:
        # 生成型は代替の上限とは別枠で加える (罰則は「代表型に無い技の使用率差」)
        from tools.team_build.gen_sets import generated_usage_gap
        for g in generated:
            c = SetCandidate(species_id, g.ability, g.item, g.nature, g.evs, list(g.moves), g.source, 0.0,
                             list(g.notes), usage_gap=generated_usage_gap(g.moves, rep.moves, move_pct))
            if c.key() not in seen:
                seen.add(c.key())
                out.append(c)
    return finalize_candidates(out, required, category_of, setup_moves)


def _mega_stones() -> set:
    try:
        from tools.check_mega_items import mega_stones
        return {sid for (_a, _b, _c, sid) in mega_stones() if sid}
    except Exception:
        return set()


def set_sanity(c: "SetCandidate") -> list:
    """型の常識フィルタ (問題の一覧、空なら OK)。データ由来の単独入替が作る不整合だけを落とす"""
    from champions_agent.config import BUILD_SET_BERRY_MOVES, OFFENSIVE_ITEM_IDS
    from champions_agent.data.build_meta import _is_offensive_spread
    problems = []
    item = (c.item or "").lower()
    if item in OFFENSIVE_ITEM_IDS and c.evs and not _is_offensive_spread(c.evs) \
            and move_categories(c.moves).count("status") < len(c.moves):
        problems.append("攻撃的持ち物 + 耐久配分 (build_meta の整合規則と同じ)")
    if item in CHOICE_ITEMS and any(m in choice_lock_moves() for m in c.moves):
        problems.append("こだわり系 + 積み/設置/回復/まもる")
    if item == "chestoberry" and "rest" not in c.moves:
        problems.append("カゴのみ + ねむる無し")
    if item == "lumberry" and c.source.startswith("alt:item") and "rest" in c.moves:
        problems.append("ラムのみ + ねむる (カゴが本来)")
    if any(m in BUILD_SET_BERRY_MOVES for m in c.moves) and not item.endswith("berry"):
        problems.append("ゲップ + きのみ無し (きのみを食べた後しか出せない)")
    return problems


def enforce_max_megas(team: list, alternatives: dict, keep: Optional[str] = None,
                      max_n: int = BUILD_MAX_MEGA_STONES) -> list:
    """メガ石を持つ型が max_n 体を超えるなら、keep と被覆の高い順に max_n 体だけ残し、他はメガ石以外の最良代替に
    差し替える (2026-09-11 まで上限 1 体だった: 1 試合 1 回のメガシンカを 1 構築 1 個の石と取り違えていた。
    実構築は石 2 個が 6 割)。alternatives: {species_id: [SetCandidate (被覆降順)]}"""
    stones = _mega_stones()
    megas = [c for c in team if (c.item or "") in stones]
    if len(megas) <= max_n:
        return team
    order = sorted(megas, key=lambda c: (0 if c.species_id == keep else 1, -c.score, c.species_id))
    keepers = {c.species_id for c in order[:max(0, int(max_n))]}
    out = []
    for c in team:
        if (c.item or "") in stones and c.species_id not in keepers:
            alt = next((a for a in alternatives.get(c.species_id, []) if (a.item or "") not in stones), None)
            if alt is not None:
                alt = SetCandidate(alt.species_id, alt.ability, alt.item, alt.nature, alt.evs, list(alt.moves),
                                   alt.source, alt.score, list(alt.notes) + [f"mega_cap:{c.item}->{alt.item}"])
                out.append(alt)
                continue
            c = SetCandidate(c.species_id, c.ability, None, c.nature, c.evs, list(c.moves), c.source, c.score,
                             list(c.notes) + [f"mega_cap:{c.item}->none"])
        out.append(c)
    return out


def enforce_single_mega(team: list, alternatives: dict, keep: Optional[str] = None) -> list:
    """互換: メガ石 1 体だけ残す (enforce_max_megas の max_n=1)"""
    return enforce_max_megas(team, alternatives, keep, 1)


def has_mega_stone(item: Optional[str]) -> bool:
    """持ち物 id がメガ石か"""
    return bool(item) and item in _mega_stones()


def prefer_mega_set(team: list, alternatives: dict, species_id: str) -> tuple:
    """指定エース (2026-10-02): species_id の型をメガ石を持つ型にする (純粋)。今の型が石を持っていればそのまま。
    持っていなければ alternatives[species_id] (被覆降順) の石持ち型の先頭に差し替える (notes に ace_mega:<前>-><後>)。
    石持ちの型が無ければそのまま (呼び出し側が記録する)。戻り値 (team, エースが石を持っているか)"""
    stones = _mega_stones()
    out, holds = [], False
    for c in team:
        if c.species_id != species_id:
            out.append(c)
            continue
        if (c.item or "") in stones:
            out.append(c)
            holds = True
            continue
        alt = next((a for a in alternatives.get(species_id, []) if (a.item or "") in stones), None)
        if alt is None:
            out.append(c)
            continue
        out.append(SetCandidate(alt.species_id, alt.ability, alt.item, alt.nature, alt.evs, list(alt.moves), alt.source,
                                alt.score, list(alt.notes) + [f"ace_mega:{c.item}->{alt.item}"], alt.usage_gap, alt.adj))
        holds = True
    return out, holds


def _with_item(c: "SetCandidate", item: Optional[str], note: str) -> "SetCandidate":
    return SetCandidate(c.species_id, c.ability, item, c.nature, c.evs, list(c.moves), c.source, c.score,
                        c.notes + [note], c.usage_gap, c.adj)


def resolve_item_clause(team: list, item_usage: dict, usage_pct: Optional[dict] = None, prefer=()) -> list:
    """チーム内で持ち物が重複したら、使用率順の未使用品 (メガ石以外) に差し替える。
    team: [SetCandidate]、item_usage: {species_id: [item ids (使用率順)]}。
    usage_pct ({species_id: {item: 使用率 %}}) が無ければ従来どおり並び順で先の個体が残す。あれば残す個体を
    「prefer の優先度 (規則のエース 2 > 設置役/登録個体 1 > 他 0) > 代表型 (alt:item でない) > その種でのその持ち物の
    使用率 > 並び順」で決める。prefer は {species_id: 優先度} か、優先度 1 とみなす集合
    (2026-09-10: ミミッキュ 81% のいのちのたまが、並び順で先のドドゲザン 8.7% に取られた件の対応)"""
    stones = _mega_stones()
    if usage_pct is None:
        used = set()
        out = []
        for c in team:
            if (c.item and c.item in used) or not c.item:
                # 重複、または持ち物なし (メガ枠の解決で代替が無かった個体) → 使用率順の未使用品
                alt = next((i for i in item_usage.get(c.species_id, [])
                            if i not in used and i != c.item and i not in stones), None)
                if alt or c.item:
                    c = _with_item(c, alt, f"clause:{c.item}->{alt}")
            if c.item:
                used.add(c.item)
            out.append(c)
        return out
    prio = dict(prefer) if isinstance(prefer, dict) else {s: 1 for s in (prefer or ())}
    by_item: dict = {}
    for i, c in enumerate(team):
        if c.item:
            by_item.setdefault(c.item, []).append(i)
    losers: set = set()
    for item, idxs in by_item.items():
        if len(idxs) < 2:
            continue

        def rank(i: int, item=item) -> tuple:
            c = team[i]
            return (int(prio.get(c.species_id, 0)), not c.source.startswith("alt:item"),
                    float((usage_pct.get(c.species_id) or {}).get(item, 0.0)), -i)

        keeper = max(idxs, key=rank)
        losers.update(i for i in idxs if i != keeper)
    used = {c.item for i, c in enumerate(team) if c.item and i not in losers}
    out = []
    for i, c in enumerate(team):
        if i in losers or not c.item:
            # 重複で譲る個体、または持ち物なし (メガ枠の解決で代替が無かった個体) → 使用率順の未使用品
            alt = next((it for it in item_usage.get(c.species_id, [])
                        if it not in used and it != c.item and it not in stones), None)
            if alt or c.item:
                c = _with_item(c, alt, f"clause:{c.item}->{alt}")
            if alt:
                used.add(alt)
        out.append(c)
    return out


def parse_sim_species(text: str) -> dict:
    """pokedex.ts の本文 → {species_id: num} (1 タブの `id: {` ブロックと直後の num 行。純粋)"""
    import re
    out: dict = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\t(\w+): \{", line)
        if m:
            cur = m.group(1)
            continue
        if cur is None or not line.startswith("\t\t"):
            continue
        m = re.match(r"^\t\tnum: (-?\d+)", line)
        if m and cur not in out:
            out[cur] = int(m.group(1))
    return out


def resolve_sim_species(species_id: str, num: Optional[int], table: dict) -> str:
    """advisor の図鑑の種 id → シム (Showdown) の種 id (純粋)。シムに同じ id があればそのまま。無ければ同じ図鑑番号の id のうち
    先頭の一致が最も長いもの (同じなら短い方): indeedeemale → indeedee、taurospaldeablazebreed → taurospaldeablaze。
    図鑑番号が無い / 候補が無ければそのまま"""
    if not species_id or species_id in table:
        return species_id
    if num is None:
        return species_id
    cands = [sid for sid, n in table.items() if n == num]
    if not cands:
        return species_id

    def common(a: str, b: str) -> int:
        k = 0
        while k < min(len(a), len(b)) and a[k] == b[k]:
            k += 1
        return k
    return sorted(cands, key=lambda s: (-common(s, species_id), len(s), s))[0]


@lru_cache(maxsize=1)
def sim_species_table() -> dict:
    """シムの種 id → 図鑑番号 (pokemon-showdown/data/pokedex.ts。読めなければ空 = 変換しない)"""
    p = Path(__file__).resolve().parents[2] / "pokemon-showdown" / "data" / "pokedex.ts"
    try:
        return parse_sim_species(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sim_species_id(species_id: str) -> str:
    """advisor の図鑑の種 id をシムの種 id に (本文の書き出し用。2026-10-03: indeedeemale / taurospaldeablazebreed が
    validate-team で "does not exist" になった)"""
    table = sim_species_table()
    if not table or species_id in table:
        return species_id
    try:
        from advisor.dex import get_dex
        num = (get_dex().species(species_id) or {}).get("num")
    except Exception:
        num = None
    return resolve_sim_species(species_id, num, table)


def to_showdown_text(team: list, level: int = 50) -> str:
    """[SetCandidate] → Showdown 形式 (能力ポイント表記のまま EVs 行に書く: 既存ツールと同じ規約)。種はシムの id で書く"""
    from tools.evaluate_team import build_team_text  # noqa: F401  (規約の参照)
    stat_names = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
    blocks = []
    for c in team:
        head = sim_species_id(c.species_id) + (f" @ {c.item}" if c.item else "")
        lines = [head, f"Level: {level}"]
        if c.ability:
            lines.append(f"Ability: {c.ability}")
        pts = parse_points(c.evs) if c.evs else None
        if pts:
            ev_parts = [f"{pts[k]} {n}" for k, n in zip(("hp", "atk", "def", "spa", "spd", "spe"), stat_names) if pts[k]]
            if ev_parts:
                lines.append("EVs: " + " / ".join(ev_parts))
        if c.nature:
            lines.append(f"{c.nature.capitalize()} Nature")
        lines += [f"- {m}" for m in c.moves]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _block_species_id(block: str) -> str:
    head = block.strip().splitlines()[0] if block.strip() else ""
    name = head.partition("@")[0].strip()
    return "".join(ch for ch in name.lower() if ch.isalnum())


def team_blocks(text: str) -> list:
    """Showdown 本文 → [(species_id, block_text)] (空行区切り)"""
    out = []
    for block in text.strip().split("\n\n"):
        if block.strip():
            out.append((_block_species_id(block), block.strip()))
    return out


def registered_items(registered_text: str) -> dict:
    """登録チーム本文 → {species_id: item_id or None}"""
    out = {}
    for sid, block in team_blocks(registered_text):
        item = block.splitlines()[0].partition("@")[2].strip()
        out[sid] = "".join(ch for ch in item.lower() if ch.isalnum()) or None
    return out


def prefer_registered(team: list, reg_items: dict) -> list:
    """登録済み個体を先頭に並べ、持ち物を登録のものにする (純粋)。

    アイテムクローズの解決は後ろの個体が譲るので、登録個体の持ち物 (例: スカーフ) を守り、
    新規に入る個体の側を差し替えさせる (近傍候補でスカーフ重複が不合法になった)
    """
    first, rest = [], []
    for c in team:
        if c.species_id in reg_items:
            item = reg_items[c.species_id] or c.item
            first.append(SetCandidate(c.species_id, c.ability, item, c.nature, c.evs, list(c.moves),
                                      c.source, c.score, list(c.notes)))
        else:
            rest.append(c)
    return first + rest


def splice_registered_sets(text: str, registered_text: str) -> tuple:
    """候補本文のうち、登録チーム (config/my_team) に同じ種族がいる個体を登録の型に差し替える (純粋)。

    現行チームとその近傍 (1 枠入替) では、ユーザーが実際に使う型で測る方が忠実で、
    メタの代表型に置き換えると「構築の差」に「型の差」が混ざる。戻り値: (本文, 差し替えた種族 id)
    """
    reg = {sid: block for sid, block in team_blocks(registered_text)}
    blocks, replaced = [], []
    for sid, block in team_blocks(text):
        if sid in reg:
            blocks.append(reg[sid])
            replaced.append(sid)
        else:
            blocks.append(block)
    return "\n\n".join(blocks) + "\n", replaced


def validate_team_text(text: str, fmt: str, timeout: int = 60) -> tuple:
    """Showdown の validate-team で合法性を検査する。(ok, errors)"""
    try:
        res = subprocess.run(["node", "pokemon-showdown", "validate-team", fmt],
                             input=text, capture_output=True, text=True,
                             cwd=str(SHOWDOWN_DIR), timeout=timeout)
    except Exception as e:
        return False, [f"validate-team 実行失敗: {e!r}"]
    if res.returncode == 0:
        return True, []
    errs = [l for l in (res.stderr or res.stdout).splitlines() if l.strip()]
    return False, errs


def coverage_score(rows: dict) -> float:
    """{opp_id: row} → 被覆スコア (脅威ごとの max(lead, switch_in, revenge) の平均)"""
    from tools.team_build.interaction import coverage_value
    vals = [coverage_value(r) for r in rows.values() if "error" not in r]
    return sum(vals) / len(vals) if vals else 0.0


def order_candidates(scored: list, usage_weight: float = BUILD_SET_USAGE_WEIGHT,
                     rep_margin: float = REP_MARGIN) -> list:
    """被覆 (score) から使用率差の罰則 (usage_weight × usage_gap) を引いた adj の降順。
    代表型は、最良の代替が adj で rep_margin 以上上回らない限り先頭に残す。純粋 (テスト可)"""
    for c in scored:
        c.adj = c.score - usage_weight * (c.usage_gap or 0.0)
    ordered = sorted(scored, key=lambda c: -c.adj)
    rep = next((c for c in ordered if c.source == "representative"), None)
    if rep is not None and ordered and ordered[0] is not rep and ordered[0].adj - rep.adj < rep_margin:
        ordered.remove(rep)
        ordered.insert(0, rep)
    return ordered


def rank_sets(candidates: list, threat_sets: dict, usage_weight: float = BUILD_SET_USAGE_WEIGHT,
              field=None) -> list:
    """各型候補を Interaction Matrix の被覆で採点し、使用率の事前分布 (order_candidates) を掛けて降順に返す
    (candidates は同一種族)。field = 並びの場 (FieldView、設置役が張るフィールド/天候) の指定 (任意)"""
    from tools.team_build.interaction import matrix, view_from_set
    # 代表型も常識フィルタにかける (代表型は各属性の最多を独立に貼り合わせたもので、
    # こだわり系 + 積み/回復 のような不整合が残ることがある)。全滅なら元の候補をそのまま使う
    sane = [c for c in candidates if not set_sanity(c)]
    if sane:
        for c in candidates:
            if c not in sane:
                note = "sanity:" + ";".join(set_sanity(c))
                if note not in c.notes:
                    c.notes.append(note)
        candidates = sane
    scored = []
    for c in candidates:
        try:
            view, moves = view_from_set(c.species_id, c.as_row())
        except Exception as e:
            c.notes.append(f"view: {e!r}")
            continue
        rows = matrix({c.species_id: (view, moves)}, threat_sets, fieldv=field)[c.species_id]
        c.score = coverage_score(rows)
        scored.append(c)
    return order_candidates(scored, usage_weight, REP_MARGIN)


def item_usage_map(conn, snapshot_id: int, species_ids: list, min_pct: float = 0.0) -> dict:
    return {sid: [i for i, _ in _rows(conn, "item_usage", "item_name", snapshot_id, sid, min_pct)]
            for sid in species_ids}


def item_usage_pct_map(conn, snapshot_id: int, species_ids: list, min_pct: float = 0.0) -> dict:
    """{species_id: {item: 使用率 %}} (アイテムクローズの解決と規則のエースの持ち物に使う)"""
    return {sid: {i: float(p or 0.0) for i, p in _rows(conn, "item_usage", "item_name", snapshot_id, sid, min_pct)}
            for sid in species_ids}
