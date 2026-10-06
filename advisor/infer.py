"""相手ポケモンの種族推測。

選出画面では相手はタイプアイコンしか分からないため、
「そのタイプ構成を持つ種族」を最新の使用率データ (championsbattledata +
pokedb上位構築由来のスナップショット) から確率付きで推測する。

例: ほのお/ゴースト -> ラウドボーン 62% / ソウブレイズ 38% など
(確率は使用率に比例。メガ形態の使用率はベース種へ合算する)
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from typing import Optional

from advisor.dex import get_dex
from advisor.sets import DB_PATH
from champions_agent.config import INFER_PAST_USAGE_DECAY, INFER_USAGE_FLOOR, SELECTION_GUESS_SURE_PROB

_TYPE_JA2EN = None
_ID2JA = None


def _ja2en() -> dict:
    global _TYPE_JA2EN
    if _TYPE_JA2EN is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _TYPE_JA2EN = dict(raw.get("types", {}))
    return _TYPE_JA2EN


# リージョン/フォルムのID接尾辞 -> 日本語表示 (base日本語名を{}に埋める)
_FORM_SUFFIXES = [
    ("hisui", "ヒスイ{}"),
    ("galar", "ガラル{}"),
    ("alola", "アローラ{}"),
    ("paldeacombatbreed", "パルデア{}(コンバット)"),
    ("paldeablazebreed", "パルデア{}(ブレイズ)"),
    ("paldeaaquabreed", "パルデア{}(ウォーター)"),
    ("paldea", "パルデア{}"),
    ("therian", "{}(れいじゅう)"),
    ("incarnate", "{}(けしん)"),
    ("wellspringmask", "{}(いどのめん)"),
    ("hearthflamemask", "{}(かまどのめん)"),
    ("cornerstonemask", "{}(いしずえのめん)"),
    ("singlestrike", "{}(いちげき)"),
    ("rapidstrike", "{}(れんげき)"),
    ("male", "{}(オス)"),
    ("female", "{}(メス)"),
]

# 個別フォルムの明示マッピング
_FORM_EXPLICIT = {
    "rotomwash": "ウォッシュロトム",
    "rotomheat": "ヒートロトム",
    "rotomfrost": "フロストロトム",
    "rotomfan": "スピンロトム",
    "rotommow": "カットロトム",
    "urshifusinglestrikegmax": "ウーラオス(いちげき)",
    "urshifurapidstrikegmax": "ウーラオス(れんげき)",
    # 2026-10-02: 名前表に無いフォルム (記事で id のまま出ていた。除外ファイル・固定枠・エースの指定にも使えるようにする)
    "floetteeternal": "フラエッテ(えいえんのはな)",
    "gourgeistsmall": "パンプジン(ちいさいサイズ)",
    "gourgeistaverage": "パンプジン(ふつうのサイズ)",
    "gourgeistlarge": "パンプジン(おおきいサイズ)",
    "gourgeistsuper": "パンプジン(とくだいサイズ)",
    "pumpkaboosmall": "バケッチャ(ちいさいサイズ)",
    "pumpkabooaverage": "バケッチャ(ふつうのサイズ)",
    "pumpkaboolarge": "バケッチャ(おおきいサイズ)",
    "pumpkaboosuper": "バケッチャ(とくだいサイズ)",
}


def species_ja_name(species_id: str) -> str:
    """showdown ID -> 日本語種族名。

    ヒスイ/ガラル/ロトム等のフォルムIDは jp_names に無いことが多いため、
    接尾辞を解析してベース種の日本語名から組み立てる (無ければIDのまま)。
    """
    global _ID2JA
    if _ID2JA is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _ID2JA = {}
        for ja, v in raw.get("species", {}).items():
            _ID2JA.setdefault(v["id"], ja)
    if species_id in _ID2JA:
        return _ID2JA[species_id]
    if species_id in _FORM_EXPLICIT:
        return _FORM_EXPLICIT[species_id]
    for suf, fmt in _FORM_SUFFIXES:
        if species_id.endswith(suf):
            base = species_id[: -len(suf)]
            if base in _ID2JA:
                return fmt.format(_ID2JA[base])
    # メガフォルム: swampertmega -> メガラグラージ
    base = _base_species_id(species_id)
    if base != species_id and base in _ID2JA:
        suffix = species_id[len(base):]
        xy = {"megax": "X", "megay": "Y", "megaz": "Z"}.get(suffix, "")
        return f"メガ{_ID2JA[base]}{xy}"
    return _ID2JA.get(species_id, species_id)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _base_species_id(species_id: str) -> str:
    """メガ形態は選出画面ではベース種として表示されるため合算用に丸める"""
    for suf in ("megax", "megay", "megaz", "mega"):
        if species_id.endswith(suf) and len(species_id) > len(suf) + 2:
            return species_id[: -len(suf)]
    return species_id


def prior_weights(latest_rows: list, past_rows: list) -> dict:
    """基本種 id → 事前の重み (純粋)。
    latest_rows: 最新スナップショットの [(種の名前, 使用率%, ゲーム内順位 or None)]、past_rows: 過去の [(種の名前, 使用率%)]。

    - メガ形態は基本種へ合算する (使用率% は和、順位は良い方)。
    - 最新にある種: max(使用率%, 順位 r を使用率曲線の r 番目に読み替えた値)。構築システムの環境スナップショット
      (tools.team_build.meta_snapshot.merge_ranked) と同じ定義。使用率% は pokedb の上位ランカー構築の採用率で、新しい
      シーズンのオープンデータが出るまで前のシーズンのまま (2026-10-06 第18回接続テスト: 規制 M-C に入って 4 週間たっても
      シーズン 5 = M-B の採用率で、今期のゲーム内順位 2 位のボーマンダが ドラゴン/ひこう の候補で 0.9% だった)。
      ゲーム内順位 (championsbattledata の今期の列位置) は今期を映すので、こちらで底上げする。
    - 最新に無い種: 過去の最大使用率を INFER_PAST_USAGE_DECAY 倍して残す (現メタ優先は保ちつつ低使用率の種もゼロにしない)
    """
    from tools.team_build.meta_snapshot import usage_at_rank
    latest: dict = {}
    rank: dict = {}
    for name, pct, r in latest_rows:
        base = _base_species_id(_slug(name))
        latest[base] = latest.get(base, 0.0) + max(float(pct), INFER_USAGE_FLOOR)
        if r is not None:
            rank[base] = min(rank.get(base, int(r)), int(r))
    curve = sorted(latest.values(), reverse=True)
    weights = {base: max(u, usage_at_rank(rank.get(base), curve)) for base, u in latest.items()}
    past: dict = {}
    for name, pct in past_rows:
        base = _base_species_id(_slug(name))
        past[base] = max(past.get(base, 0.0), max(float(pct), INFER_USAGE_FLOOR))
    for base, v in past.items():
        if base not in weights:
            weights[base] = v * INFER_PAST_USAGE_DECAY
    return weights


class TypeInference:
    """タイプ構成 -> 種族候補 (確率付き) の推測器"""

    def __init__(self):
        self._index: dict[frozenset, list] = {}
        self._build()

    def _build(self) -> None:
        if not DB_PATH.exists():
            return
        dex = get_dex()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            snap = conn.execute(
                "SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
            if snap is None:
                return
            # 全スナップショットを読む: 最新だけだと約半数の種族が候補から
            # 消える (実測: 最新235種/全期間491種。むし/ひこう構成の
            # ストライク等が推測不能だった)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(pokemon_usage)")}
            rank_col = "rank" if "rank" in cols else "NULL"     # 順位の列が無い古い DB でも読めるように
            rows = conn.execute(
                f"SELECT pokemon_name, usage_percent, snapshot_id, {rank_col} AS rank "
                "FROM pokemon_usage").fetchall()
            conn.close()
        except Exception:
            return

        latest_id = snap["id"]
        usage = prior_weights(
            [(r["pokemon_name"], r["usage_percent"], r["rank"]) for r in rows if r["snapshot_id"] == latest_id],
            [(r["pokemon_name"], r["usage_percent"]) for r in rows if r["snapshot_id"] != latest_id])

        # チャンピオンズフィルタ: 全期間へ広げた際にSV由来スナップショットの
        # 種族 (チャンピオンズに存在しない) が混入しないようにする
        try:
            from advisor.team_advice import champions_usable
        except Exception:
            def champions_usable(_sid):
                return True

        for sid, weight in usage.items():
            if not champions_usable(sid):
                continue
            sp = dex.species(sid)
            if sp is None:
                continue
            key = frozenset(sp["types"])
            self._index.setdefault(key, []).append((sid, weight))

        for key, entries in self._index.items():
            entries.sort(key=lambda e: -e[1])

    def candidates(self, types_ja: list, top_k: int = 5) -> list:
        """タイプ構成 (日本語) から候補を返す。

        戻り値: [(species_id, 確率, 日本語名)] 確率は正規化済み・降順。
        """
        ja2en = _ja2en()
        types_en = frozenset(ja2en.get(t, t) for t in (types_ja or []) if t)
        if not types_en:
            return []
        entries = self._index.get(types_en, [])[:top_k]
        total = sum(w for _, w in entries)
        if total <= 0:
            return []
        return [(sid, w / total, species_ja_name(sid)) for sid, w in entries]


_inference: Optional[TypeInference] = None


def get_inference() -> TypeInference:
    global _inference
    if _inference is None:
        _inference = TypeInference()
    return _inference


def guess_view(cands: list, species_id: Optional[str], sure_prob: float = SELECTION_GUESS_SURE_PROB) -> dict:
    """選出画面の推定の枠 (species_id と推定済み) を画面にどう出すか (純粋)。cands = candidates() の出力。
    戻り値 {"sure": ほぼ確定か, "candidates": 画面に出す候補 (ほぼ確定なら空)}。

    ほぼ確定 = 推定した種が、タイプからの候補の中で sure_prob 以上 (SELECTION_GUESS_SURE_PROB。実測で外れが無かった帯)。
    ほぼ確定の枠には候補を出さず、これまで通りその種として扱う。それ以外の推定の枠には候補を出して手で直せるようにする
    (2026-10-06 第18回接続テスト: 全部の推定の枠にプルダウンが出て、選ぶことを求められているように見えた)"""
    base = _base_species_id(species_id) if species_id else None
    p = next((prob for sid, prob, _ja in cands if sid == base), 0.0)
    sure = bool(base) and p >= sure_prob
    return {"sure": sure, "candidates": [] if sure else list(cands)}
