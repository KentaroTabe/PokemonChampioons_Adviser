"""構築の進化探索: 対戦AIを評価関数にしてメタに強いチームを育てる。

フェーズ2 (進化ループ):
  初期集団 (上位実構築 + 使用率メタ生成チーム) → 各チームを実対戦で評価
  (両サイド同一のRL方策 + 相性選出、相手=メタ分布の構築群) → 上位を残して
  1枠入替の変異 → 世代を回す。

フェーズ3 (メタ遷移対応):
  --forecast-mix: 使用率スナップショットの月次履歴から上昇トレンドを外挿し、
    「伸びている種族を含む構築」を相手分布に混ぜる (履歴が1ヶ月分しか
    ない間は自動で無効化し、現行メタのみで評価する)
  --archive-mix: 過去の探索で勝ち残ったチーム (アーカイブ) を相手に混ぜ、
    「自分の対策構築が普及した後のメタ」への頑健性を測る (PSRO-lite)。
    --update-archive で今回の最優秀チームをアーカイブへ追加する。

制約付き改善 (自分のパーティを少しだけ変える):
  --seed-myteam: config/my_team.json の登録チームを種にする
  --locked "ペリッパー,ラグラージ": 入れ替え禁止の固定枠
  --max-changes 2: 種チームから同時に変えてよい枠数の上限

    python -m tools.evolve_teams --population 12 --generations 3 --battles 40
    python -m tools.evolve_teams --seed-myteam --locked ミミッキュ --max-changes 2
    python -m tools.evolve_teams --update-archive                              # 反復運用

結果: logs/team_evolution/run_<時刻>.json + 最優秀チームの日本語表示。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path

# champions modの未知エフェクト警告 (MEGA_SOL等) は動作に影響しないため抑制
logging.getLogger("poke-env").setLevel(logging.ERROR)

from champions_agent.config import USAGE_TARGET_FORMAT
from champions_agent.data import database as db
from champions_agent.env.ranked_teams import build_ranked_teams
from champions_agent.env.team_builder import (
    PokemonSet, _base_species_key, _fetch_meta_pool, _sanitize_item,
    _sanitize_species, build_random_team_text, to_showdown_name,
)

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "logs" / "team_evolution"
ARCHIVE_PATH = OUT_DIR / "archive.json"


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _team_species(team_text: str) -> list:
    """チームテキスト -> 種族id列 (ブロック先頭行から)"""
    out = []
    for block in team_text.strip().split("\n\n"):
        head = block.strip().split("\n")[0]
        out.append(_to_id(head.split(" @ ")[0]))
    return out


def _team_ja(team_text: str) -> str:
    from advisor.infer import species_ja_name
    return " / ".join(species_ja_name(s) or s for s in _team_species(team_text))


# ------------------------------------------------------------------
# フェーズ3: メタ遷移の外挿
# ------------------------------------------------------------------
def forecast_scores() -> dict | None:
    """種族id -> 予測使用率 (現在値 + 月次トレンドの1期外挿)。

    champions-singles のスナップショットが2ヶ月分未満なら None
    (トレンドが定義できないため呼び出し側で無効化する)。
    """
    with db.get_connection() as conn:
        rows = conn.execute(
            """SELECT id, source_month FROM usage_snapshot
               WHERE format = ? ORDER BY fetched_at""",
            (USAGE_TARGET_FORMAT,)).fetchall()
        by_month = {}
        for r in rows:                      # 同月の再取得は最新を採用
            by_month[r["source_month"]] = r["id"]
        if len(by_month) < 2:
            return None
        months = list(by_month)[-2:]
        usage = {}
        for i, m in enumerate(months):
            for r in conn.execute(
                    """SELECT pokemon_name, usage_percent FROM pokemon_usage
                       WHERE snapshot_id = ?""", (by_month[m],)):
                usage.setdefault(_to_id(r["pokemon_name"]), [0.0, 0.0])[i] = \
                    r["usage_percent"]
    return {sid: max(0.0, cur + (cur - prev))
            for sid, (prev, cur) in usage.items()}


# ------------------------------------------------------------------
# 相手分布 (現行メタ + 予測メタ + アーカイブ)
# ------------------------------------------------------------------
try:
    from poke_env.teambuilder import Teambuilder as _TB
except Exception:
    _TB = object


class WeightedTextsTeambuilder(_TB):
    """チームテキスト群から重み付きで1チームを選ぶTeambuilder"""

    def __init__(self, texts, weights=None, rng=None):
        self.texts = list(texts)
        self.weights = list(weights) if weights else None
        self.rng = rng or random.Random()
        self._packed = {}

    def yield_team(self) -> str:
        text = self.rng.choices(self.texts, weights=self.weights, k=1)[0]
        if text not in self._packed:
            self._packed[text] = self.join_team(
                self.parse_showdown_team(text))
        return self._packed[text]


class FixedSequenceTeambuilder(_TB):
    """あらかじめ決めた相手チームを順番に返す (共通乱数)。

    候補ごとに同じ相手列を当てることで、チーム間の差が相手の引き運に
    埋もれないようにする。相手を引き直して評価すると、120戦でも
    標準誤差0.046・12個体の最大値を取ると勝者の呪いで0.07〜0.09上振れし、
    構築間の差 (おそらく0.05前後) が読めない。
    """

    def __init__(self, packed_teams):
        self.packed = list(packed_teams)
        self.i = 0

    def yield_team(self) -> str:
        t = self.packed[self.i % len(self.packed)]
        self.i += 1
        return t


class MixtureTeambuilder(_TB):
    """複数Teambuilderの重み付き混合"""

    def __init__(self, parts, rng=None):   # parts: [(weight, teambuilder)]
        self.parts = [(w, tb) for w, tb in parts if w > 0]
        self.rng = rng or random.Random()

    def yield_team(self) -> str:
        weights = [w for w, _ in self.parts]
        _, tb = self.rng.choices(self.parts, weights=weights, k=1)[0]
        return tb.yield_team()


def load_archive() -> list:
    if ARCHIVE_PATH.exists():
        try:
            return json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def build_opponent(forecast_mix: float, archive_mix: float,
                   rng: random.Random):
    """相手チーム分布: 現行メタ + 予測メタ + アーカイブの混合"""
    # 上位60構築に固定。ここが動くと適応度の基準が変わり、過去の実行結果や
    # アーカイブの fitness と比較できなくなる (広げる場合は別の実験として
    # training_changes.json に記録すること)
    ranked = build_ranked_teams(top_n=60, include_external=False)
    parts = []
    fs = forecast_scores() if forecast_mix > 0 else None
    if forecast_mix > 0 and fs is None:
        print("[evolve] 使用率履歴が2ヶ月分未満のため予測メタ混合は無効化 "
              "(現行メタのみで評価)", flush=True)
    if fs:
        w = [sum(fs.get(s, 0.0) for s in _team_species(t)) + 1e-6
             for t in ranked]
        parts.append((forecast_mix,
                      WeightedTextsTeambuilder(ranked, w, rng)))
    archive = load_archive()
    if archive_mix > 0 and archive:
        parts.append((archive_mix, WeightedTextsTeambuilder(
            [e["text"] for e in archive], rng=rng)))
    base = 1.0 - sum(w for w, _ in parts)
    parts.append((base, WeightedTextsTeambuilder(ranked, rng=rng)))
    return MixtureTeambuilder(parts, rng=rng)


# ------------------------------------------------------------------
# フェーズ2: 集団の初期化・変異・評価
# ------------------------------------------------------------------
def init_population(size: int, rng: random.Random) -> list:
    """初期集団: 半分は上位実構築、半分は使用率メタからの生成チーム"""
    ranked = build_ranked_teams(top_n=60, include_external=False)
    pop = [{"origin": "ranked", "text": t, "fitness": None}
           for t in ranked[:size // 2]]
    while len(pop) < size:
        pop.append({"origin": "generated",
                    "text": build_random_team_text(size=6), "fitness": None})
    return pop


class Constraint:
    """制約付き改善: 種チームからの距離上限と固定枠"""

    def __init__(self, seed_text: str, locked_ja: list, max_changes: int):
        self.seed_keys = {_base_species_key(s)
                          for s in _team_species(seed_text)}
        self.max_changes = max_changes
        self.locked = set()
        if locked_ja:
            from vision.normalize import NameResolver
            resolver = NameResolver()
            for name in locked_ja:
                r = resolver.resolve_species(name.strip(), cutoff=0.8)
                if r is None:
                    raise SystemExit(f"--locked の種族を解決できません: {name}")
                self.locked.add(_base_species_key(r[1]))
        unknown = self.locked - self.seed_keys
        if unknown:
            raise SystemExit(f"--locked が種チームにいません: {unknown}")

    def mutable_slots(self, team_text: str) -> list:
        """変異してよいスロット番号。固定枠は常に不可。既に max_changes
        枠変わっているチームは「変更済みスロットの再変異」のみ許す"""
        species = [_base_species_key(s) for s in _team_species(team_text)]
        changed = [i for i, s in enumerate(species)
                   if s not in self.seed_keys]
        if len(changed) >= self.max_changes:
            return [i for i in changed if species[i] not in self.locked]
        return [i for i, s in enumerate(species) if s not in self.locked]


def init_population_seeded(size: int, seed_text: str, constraint: Constraint,
                           pool_rows: list, rng: random.Random) -> list:
    """種チーム + その制約内変異体で初期集団を作る"""
    pop = [{"origin": "seed", "text": seed_text, "fitness": None}]
    tries = 0
    while len(pop) < size and tries < size * 20:
        tries += 1
        text = mutate(seed_text, pool_rows, rng, constraint)
        if rng.random() < 0.5:   # 半分は2枠目まで変異
            text = mutate(text, pool_rows, rng, constraint)
        if all(text != m["text"] for m in pop):
            pop.append({"origin": "mutant", "text": text, "fitness": None})
    return pop


def _meta_pool_rows() -> list:
    with db.get_connection() as conn:
        snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
        return _fetch_meta_pool(conn, snap) if snap else []


def _apply_synergy_bias(team_text: str, idx: int, cands: list,
                        weights: list) -> list:
    """残す5体との共起 (synergy) で候補の重みを最大2倍までブーストする。

    埋め込みが無い/読めない場合は元の重みをそのまま返す (安全側)。
    """
    try:
        from tools.species_embedding import load
        syn = load()["cooccurrence"]["synergy"]
    except Exception:
        return weights
    keep = [s for i, s in enumerate(_team_species(team_text)) if i != idx]
    scores = []
    for r in cands:
        sid = _to_id(r["pokemon_name"])
        vec = syn.get(sid)
        if not vec:
            scores.append(0.0)
            continue
        ids = load()["cooccurrence"]["ids"]
        pos = {s: i for i, s in enumerate(ids)}
        scores.append(sum(vec[pos[k]] for k in keep if k in pos))
    hi = max(scores) if scores else 0.0
    if hi <= 0:
        return weights
    return [w * (1.0 + s / hi) for w, s in zip(weights, scores)]


def mutate(team_text: str, pool_rows: list, rng: random.Random,
           constraint: "Constraint | None" = None) -> str:
    """1枠を使用率重み付きの別種族 (meta_setsの型) に入れ替える。

    constraint があれば固定枠と変更数上限 (種チームからの距離) を守る。
    """
    blocks = team_text.strip().split("\n\n")
    if constraint is not None:
        slots = constraint.mutable_slots(team_text)
        if not slots:
            return team_text
        idx = rng.choice(slots)
    else:
        idx = rng.randrange(len(blocks))
    current = {_base_species_key(s) for s in _team_species(team_text)}
    cands = [r for r in pool_rows
             if _base_species_key(_to_id(r["pokemon_name"])) not in current]
    if not cands:
        return team_text
    weights = [max(0.01, float(r["weight"] or 0.01)) for r in cands]
    # 共起埋め込みで「残る5体と噛み合う候補」を優先する。闇雲な入れ替えより
    # 収束が速い (tools/species_embedding の synergy = 一緒に使われる度合い)。
    # 使用率重みは残したまま最大2倍までのブーストに留め、多様性は保つ
    weights = _apply_synergy_bias(team_text, idx, cands, weights)
    row = rng.choices(cands, weights=weights, k=1)[0]
    item = _sanitize_item(row["item_name"])
    used_items = {b.split(" @ ", 1)[1].split("\n")[0].strip()
                  for b in blocks[:idx] + blocks[idx + 1:] if " @ " in b}
    if item and _to_id(item) in {_to_id(i) for i in used_items}:
        item = None   # アイテムクローズ
    new_set = PokemonSet(
        species=to_showdown_name(_sanitize_species(row["pokemon_name"])),
        ability=row["ability_name"], item=item,
        tera_type=row["tera_type"], nature=row["nature"], evs=row["evs"],
        moves=[row["move1"], row["move2"], row["move3"], row["move4"]])
    blocks[idx] = new_set.to_showdown_text()
    return "\n\n".join(blocks)


# ------------------------------------------------------------------
# セット内変異 (種族は保ったまま型を変える局所改善)
# ------------------------------------------------------------------
# 上位プレイヤーの改善は種族の総入れ替えより「持ち物・技1枠・配分」の
# 局所手術が主。従来の mutate (種族入れ替えのみ) ではこの近傍を探索
# できなかった。
#
# ⚠ 候補をランキング上位チームのテキストから引いてはいけない。
# オープンデータにはチームごとの技・配分が含まれず、テキスト生成時に
# メタ最頻セットで埋められるため、「変種」は持ち物しか違わない
# (2026-07-31に実測: gengar変種2件の観測技は4種のみ)。
# 使用率DB (move_usage / item_usage / spread_usage) から使用率重みで
# 引く。実際に使われている型の範囲なので一貫性も保たれる。

_usage_cache: dict | None = None


def _usage_alternatives() -> dict:
    """種族id -> {"moves": [(技, %)], "items": [(持ち物, %)],
    "spreads": [(性格, EV文字列, %)]} (最新スナップショット)"""
    global _usage_cache
    if _usage_cache is None:
        out: dict = {}
        with db.get_connection() as conn:
            snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
            if snap:
                for r in conn.execute(
                        """SELECT pokemon_name, move_name, usage_percent
                           FROM move_usage WHERE snapshot_id = ?""", (snap,)):
                    out.setdefault(_to_id(r["pokemon_name"]),
                                   {"moves": [], "items": [], "spreads": []})[
                        "moves"].append((r["move_name"], r["usage_percent"]))
                for r in conn.execute(
                        """SELECT pokemon_name, item_name, usage_percent
                           FROM item_usage WHERE snapshot_id = ?""", (snap,)):
                    out.setdefault(_to_id(r["pokemon_name"]),
                                   {"moves": [], "items": [], "spreads": []})[
                        "items"].append((r["item_name"], r["usage_percent"]))
                for r in conn.execute(
                        """SELECT pokemon_name, nature, evs, usage_percent
                           FROM spread_usage WHERE snapshot_id = ?""", (snap,)):
                    out.setdefault(_to_id(r["pokemon_name"]),
                                   {"moves": [], "items": [], "spreads": []})[
                        "spreads"].append(
                            (r["nature"], r["evs"], r["usage_percent"]))
        _usage_cache = out
    return _usage_cache


def _block_item(block: str) -> str | None:
    head = block.strip().split("\n")[0]
    return head.split(" @ ", 1)[1].strip() if " @ " in head else None


def _block_moves(block: str) -> list:
    return [l[2:].strip() for l in block.strip().split("\n")
            if l.startswith("- ")]


def _replace_item(block: str, item: str | None) -> str:
    lines = block.strip().split("\n")
    species = lines[0].split(" @ ")[0].strip()
    lines[0] = f"{species} @ {item}" if item else species
    return "\n".join(lines)


def _team_items(blocks: list, skip: int) -> set:
    """アイテムクローズ用: skip以外のスロットが持つアイテムid集合"""
    return {_to_id(_block_item(b)) for i, b in enumerate(blocks)
            if i != skip and _block_item(b)}


def _evs_line(evs: str) -> str | None:
    """EV文字列 "H/A/B/C/D/S" -> "EVs: 2 HP / 32 SpA" 形式の行。

    クランプ (各32/合計66) は PokemonSet.to_showdown_text と同じ規則
    (champions modの能力ポイント仕様)。
    """
    labels = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
    values = []
    for v in str(evs or "").split("/"):
        try:
            values.append(max(0, min(32, int(v))))
        except ValueError:
            values.append(0)
    while sum(values) > 66:
        i = values.index(max(values))
        values[i] -= sum(values) - 66 if values[i] >= sum(values) - 66 else 1
    parts = [f"{v} {l}" for l, v in zip(labels, values) if v]
    return ("EVs: " + " / ".join(parts)) if parts else None


def mutate_set(team_text: str, rng: random.Random,
               constraint: "Constraint | None" = None) -> str:
    """1枠の種族は保ったまま、型 (持ち物/技1枠/性格・配分) を変える。

    候補は使用率DBから使用率重みで引く (実際に使われている型の範囲)。
    種族が変わらないため Constraint の変更数 (種チームからの距離) は
    増えない。固定枠は型もいじらない (ユーザーが実機で使うセットを
    勝手に変えない) ため mutable_slots に従う。
    """
    blocks = [b.strip() for b in team_text.strip().split("\n\n")]
    slots = (constraint.mutable_slots(team_text) if constraint
             else list(range(len(blocks))))
    if not slots:
        return team_text
    # 不発 (その種族の使用率データが無い等) のときは別の操作/別のスロットを
    # 試す。不発をそのまま返すとGAが同一個体を再評価して対戦数を無駄にする
    kinds = ["item", "move", "spread"]
    rng.shuffle(kinds)
    rng.shuffle(slots)
    for idx in slots:
        sid = _to_id(blocks[idx].split("\n")[0].split(" @ ")[0])
        alt = _usage_alternatives().get(sid)
        if not alt:
            continue
        for kind in kinds:
            if kind == "item":
                used = _team_items(blocks, idx)
                cur = _to_id(_block_item(blocks[idx]) or "")
                cands = [(_sanitize_item(n), w) for n, w in alt["items"]]
                cands = [(n, w) for n, w in cands
                         if n and _to_id(n) not in used and _to_id(n) != cur]
                if cands:
                    item = rng.choices([n for n, _ in cands],
                                       weights=[max(w, 0.1)
                                                for _, w in cands], k=1)[0]
                    blocks[idx] = _replace_item(blocks[idx], item)
                    return "\n\n".join(blocks)

            if kind == "move":
                cur_moves = _block_moves(blocks[idx])
                cur_ids = {_to_id(m) for m in cur_moves}
                cands = [(n, w) for n, w in alt["moves"]
                         if _to_id(n) not in cur_ids]
                if cur_moves and cands:
                    new_move = rng.choices([n for n, _ in cands],
                                           weights=[max(w, 0.1)
                                                    for _, w in cands],
                                           k=1)[0]
                    old = rng.choice(cur_moves)
                    lines = blocks[idx].split("\n")
                    for i, l in enumerate(lines):
                        if l.strip() == f"- {old}":
                            lines[i] = f"- {new_move}"
                            break
                    blocks[idx] = "\n".join(lines)
                    return "\n\n".join(blocks)

            if kind == "spread":
                lines = blocks[idx].split("\n")
                ev_i = next((i for i, l in enumerate(lines)
                             if l.startswith("EVs: ")), None)
                nat_i = next((i for i, l in enumerate(lines)
                              if l.endswith(" Nature")), None)
                # championsスナップショットは nature が None (EVのみ記録)。
                # その場合はEV行だけを差し替え、性格は現在のまま残す
                cands = [(nat, evs, w) for nat, evs, w in alt["spreads"]
                         if evs and _evs_line(evs)]
                if ev_i is not None and cands:
                    nat, evs, _w = rng.choices(
                        cands, weights=[max(c[2], 0.1) for c in cands],
                        k=1)[0]
                    new_ev = _evs_line(evs)
                    new_nat = (f"{str(nat).capitalize()} Nature"
                               if nat and nat_i is not None else None)
                    changed = lines[ev_i] != new_ev or \
                        (new_nat is not None and lines[nat_i] != new_nat)
                    if not changed:
                        continue   # 現在と同じ配分を引いた
                    lines[ev_i] = new_ev
                    if new_nat is not None:
                        lines[nat_i] = new_nat
                    blocks[idx] = "\n".join(lines)
                    return "\n\n".join(blocks)

    return team_text


def mutate_any(team_text: str, pool_rows: list, rng: random.Random,
               constraint: "Constraint | None" = None,
               set_prob: float = 0.5) -> str:
    """set_prob の確率でセット内変異、残りは従来の種族入れ替え"""
    if rng.random() < set_prob:
        out = mutate_set(team_text, rng, constraint)
        if out != team_text:
            return out
        # セット内変異が不発 (候補なし) なら種族入れ替えへフォールバック
    return mutate(team_text, pool_rows, rng, constraint)


async def evaluate_population(pop: list, n_battles: int, opp_builder,
                              concurrency: int = 3,
                              opponents: list = None) -> None:
    """集団を評価する。

    ⚠ 毎世代すべての個体を測り直す。以前は生存者の評価値を再利用していたが、
    固定なのは相手の「分布」であって引きは毎回違うため、1世代目でたまたま
    勝った個体が上振れした点数のまま居座り、探索が原理的に進まなくなっていた
    (世代推移が 0.70→0.70→0.70 とフラットになる症状)。

    opponents を渡すと全個体が同じ相手列と戦う (共通乱数)。
    """
    from tools.evaluate_team import evaluate_team_text
    for i in range(0, len(pop), concurrency):
        chunk = pop[i:i + concurrency]
        results = await asyncio.gather(*[
            evaluate_team_text(
                m["text"], n_battles=n_battles,
                # 個体ごとに独立したインスタンスを渡す (同じ順で同じ相手に当たる)
                opp_teambuilder=(FixedSequenceTeambuilder(opponents)
                                 if opponents else opp_builder))
            for m in chunk], return_exceptions=True)
        for m, r in zip(chunk, results):
            if isinstance(r, Exception):
                print(f"[evolve] 評価失敗 ({_team_ja(m['text'])[:40]}): {r}",
                      flush=True)
                m["fitness"] = 0.0
                m["outcomes"] = []
            else:
                m["fitness"] = r["win_rate"]
                m["outcomes"] = r.get("outcomes") or []
                # 累積 (世代をまたいだ総合成績。報告用)
                m["cum_wins"] = m.get("cum_wins", 0) + r["wins"]
                m["cum_battles"] = m.get("cum_battles", 0) + r["n_battles"]


def _paired_verdict(pop: list, log) -> None:
    """上位2件を「同じ相手列での対応のある比較」で検定する。

    絶対勝率の差は相手の引きに左右されるが、相手を揃えてあれば
    対戦ごとの差を取れる。これが誤差に埋もれるなら、1位を選んだこと自体に
    根拠がない (探索が進んでいないのに進んだように見えるのを防ぐ)。
    """
    if len(pop) < 2:
        return
    a, b = pop[0].get("outcomes"), pop[1].get("outcomes")
    if not a or not b or len(a) != len(b):
        return
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1) if n > 1 else 0.0
    se = (var / n) ** 0.5
    verdict = "有意差あり" if abs(mean) > 2 * se else "⚠ 差は誤差の範囲"
    log(f"  1位 vs 2位 (対応のある比較): {mean:+.3f} ± {se:.3f} → {verdict}")
    top_se = (pop[0]["fitness"] * (1 - pop[0]["fitness"]) / n) ** 0.5
    log(f"  1位の勝率 {pop[0]['fitness']:.2f} ± {top_se:.3f} (SE)")


async def run(args, log=None) -> dict:
    """進化探索を実行し、最優秀チーム等を返す。

    log: 進捗コールバック (省略時は標準出力)。サーバーからの実行用。
    返り値: {"best_text", "best_ja", "fitness", "path"}
    """
    log = log or (lambda m: print(m, flush=True))
    rng = random.Random(args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    opp = build_opponent(args.forecast_mix, args.archive_mix, rng)
    pool_rows = _meta_pool_rows()
    constraint = None
    if args.seed_myteam or args.seed_file:
        if args.seed_file:
            seed_text = Path(args.seed_file).read_text(encoding="utf-8")
        else:
            from tools.evaluate_team import build_myteam_text
            seed_text = build_myteam_text()
        locked = [s for s in (args.locked or "").replace("、", ",").split(",")
                  if s.strip()]
        constraint = Constraint(seed_text, locked, args.max_changes)
        log(f"[evolve] 制約付き改善: 種={_team_ja(seed_text)} / "
            f"固定{len(constraint.locked)}枠 / 変更上限{args.max_changes}")
        pop = init_population_seeded(args.population, seed_text,
                                     constraint, pool_rows, rng)
    else:
        pop = init_population(args.population, rng)
    history = []

    for gen in range(args.generations):
        t0 = time.time()
        # 世代ごとに相手列を1本引き、その世代の全個体を同じ列で評価する
        # (世代内は対応のある比較。世代をまたぐと相手が変わるので、
        #  世代間の勝率を直接比べてはいけない)
        opponents = [opp.yield_team() for _ in range(args.battles)]
        await evaluate_population(pop, args.battles, opp,
                                  concurrency=args.concurrency,
                                  opponents=opponents)
        pop.sort(key=lambda m: -m["fitness"])
        log(f"===== 世代{gen + 1}/{args.generations} "
            f"({time.time() - t0:.0f}s) =====")
        for m in pop:
            log(f"  {m['fitness']:.2f} [{m['origin']}] "
                f"{_team_ja(m['text'])}")
        history.append([{"origin": m["origin"], "fitness": m["fitness"],
                         "cum_wins": m.get("cum_wins"),
                         "cum_battles": m.get("cum_battles"),
                         "species": _team_species(m["text"])} for m in pop])
        _paired_verdict(pop, log)
        if gen == 0:
            # 健全性: 既知の強構築 (ranked) が生成チームを平均で上回るか
            by = {}
            for m in pop:
                by.setdefault(m["origin"], []).append(m["fitness"])
            means = {k: sum(v) / len(v) for k, v in by.items()}
            if "ranked" in means and "generated" in means:
                verdict = "OK" if means.get("ranked", 0) >= \
                    means.get("generated", 0) else "⚠要確認"
                log(f"  健全性: ranked平均{means.get('ranked', 0):.2f} vs "
                    f"generated平均{means.get('generated', 0):.2f} {verdict}")
        if gen + 1 >= args.generations:
            break
        survivors = pop[:max(2, len(pop) // 2)]
        children = []
        while len(survivors) + len(children) < args.population:
            parent = survivors[len(children) % len(survivors)]
            children.append({"origin": "mutant",
                             "text": mutate_any(parent["text"], pool_rows,
                                                rng, constraint,
                                                getattr(args, "set_mut", 0.5)),
                             "fitness": None})
        # 生存者も次世代で測り直す (評価値の再利用はしない。
        # 上の evaluate_population の注記を参照)
        pop = survivors + children

    best = pop[0]
    run_path = OUT_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}.json"
    run_path.write_text(json.dumps({
        "battles": args.battles, "population": args.population,
        "generations": args.generations, "forecast_mix": args.forecast_mix,
        "archive_mix": args.archive_mix,
        "best": {"fitness": best["fitness"], "text": best["text"]},
        "history": history,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"\n最優秀 (勝率{best['fitness']:.2f}): {_team_ja(best['text'])}")
    from tools.evaluate_team import team_text_to_ja
    best_ja = team_text_to_ja(best["text"])
    log(best_ja)
    log(f"記録: {run_path}")

    if args.update_archive:
        archive = load_archive()
        key = sorted(_team_species(best["text"]))
        if not any(sorted(e.get("species", [])) == key for e in archive):
            archive.append({"t": time.time(), "fitness": best["fitness"],
                            "species": key, "text": best["text"]})
            ARCHIVE_PATH.write_text(
                json.dumps(archive, ensure_ascii=False, indent=1),
                encoding="utf-8")
            log(f"アーカイブへ追加 (計{len(archive)}件) — 次回の相手分布に"
                "混ざります (--archive-mix)")
    return {"best_text": best["text"], "best_ja": best_ja,
            "fitness": best["fitness"], "path": str(run_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description="構築の進化探索")
    ap.add_argument("--population", type=int, default=12)
    ap.add_argument("--generations", type=int, default=3)
    ap.add_argument("--battles", type=int, default=40,
                    help="1チームあたりの評価対戦数")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--forecast-mix", type=float, default=0.3,
                    help="予測メタを相手に混ぜる比率 (履歴不足時は自動無効)")
    ap.add_argument("--archive-mix", type=float, default=0.2,
                    help="過去の優勝チームを相手に混ぜる比率")
    ap.add_argument("--update-archive", action="store_true",
                    help="最優秀チームをアーカイブへ追加 (PSRO反復)")
    ap.add_argument("--seed-myteam", action="store_true",
                    help="config/my_team.json を種にした制約付き改善")
    ap.add_argument("--seed-file", default=None,
                    help="種チームのShowdownテキストファイル")
    ap.add_argument("--locked", default=None,
                    help="入れ替え禁止の種族 (カンマ区切り、日本語可)")
    ap.add_argument("--max-changes", type=int, default=2,
                    help="種チームから同時に変えてよい枠数")
    ap.add_argument("--set-mut", type=float, default=0.5,
                    help="セット内変異 (持ち物/技/型) を使う確率。"
                         "0で従来の種族入れ替えのみ")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
