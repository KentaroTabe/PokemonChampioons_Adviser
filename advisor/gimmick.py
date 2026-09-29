"""1 試合に 1 回しか使えない資源 (メガシンカ) の推定。将来のダイマックス/テラスタル等も同じ枠で扱う。

方針 (2026-09-11 ユーザー指摘: 「相手がチームプレビューからメガ先を読まない」のは致命的):
  - 相手の持ち物は見えないので、種族ごとの「メガ石を持つ確率」を使用率 DB (item_usage のメガ石) から事前分布として持つ。
    判明した情報 (持ち物の判明、メガシンカ済み、相手のメガ権利の消費) で上書きする。
  - 自分側は持ち物が分かるので確定 (石を持つ = 1.0)。
  - 出力は「メガ後の姿ごとの確率」{mega_sid: p}。X/Y のように複数の姿があれば石の使用率で按分する。
  - 選出モデル (v3 特徴量)、助言の探索 (相手のメガシンカを分岐)、学習の観測 (v8 ブロック) が共通してここを使う。
判定・数値は表とデータから機械的に出す (LLM は使わない)。
"""
from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from typing import Optional

from champions_agent.config import (GIMMICK_DEFAULT_STONE_PRIOR, GIMMICK_MIN_PRIOR, USAGE_TARGET_FORMAT)


def _toid(name: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


@lru_cache(maxsize=1)
def stone_table() -> dict:
    """{石 id: メガ後の種族 id} (champions_dex の isMega + requiredItem)"""
    from tools.check_mega_items import mega_stones
    return {item_id: sid for (sid, _n, _r, item_id) in mega_stones() if item_id}


@lru_cache(maxsize=1)
def stones_by_base() -> dict:
    """{基本種 id: [(石 id, メガ後 id)]}"""
    out: dict = {}
    for item_id, msid in stone_table().items():
        base = _base_of(msid)
        out.setdefault(base, []).append((item_id, msid))
    return out


def _base_of(mega_sid: str) -> str:
    m = re.match(r"^(.*?)mega[a-z]?$", mega_sid)
    return m.group(1) if m else mega_sid


def mega_forms(species_id: str) -> list:
    """基本種 → メガ後の種族 id (無ければ空)。メガ後 id を渡されたらそれ自身"""
    sid = _toid(species_id)
    if sid in stone_table().values():
        return [sid]
    return [msid for _item, msid in stones_by_base().get(sid, [])]


def is_mega_form(species_id: str) -> bool:
    return _toid(species_id) in set(stone_table().values())


def stone_form_of(species_id: str, item_id: Optional[str] = None, item_ja: Optional[str] = None) -> Optional[str]:
    """基本種 + メガ石 → メガ後のフォルム id (その種がメガシンカできなければ None)。
    石の id が分かれば requiredItem の表 (stone_table) で引く。分からなければ名前の末尾 (X / Y / Z) で推定し、
    末尾が無ければ無印のメガ → X の順。
    2026-09-18: 末尾 x/y だけを見る推定が各所にあり、Z 石 (ガブリアスナイトZ 等、M-C で追加) が通常のメガに倒れていた
    (脅威の評価・自分の型の被覆・助言のメガ後比較が別フォルムの種族値になる)。表引きを正にする"""
    base = _toid(species_id)
    forms = mega_forms(base)
    if not forms:
        return None
    if item_id:
        form = stone_table().get(_toid(item_id))
        if form in forms:
            return form
    suffix = ""
    if item_ja:
        # 名前は構文で読む: 「ガブリアスナイトZ」も「メガガブリアスZナイト」(手入力の並び) も接尾辞 z (2026-09-29)
        from vision.normalize import parse_mega_stone_name
        parsed = parse_mega_stone_name(item_ja)
        if parsed:
            suffix = parsed[1]
    if not suffix:
        text = (item_id or "").strip()
        suffix = text[-1].lower() if text and text[-1] in ("X", "Y", "Z", "x", "y", "z") else ""
    if suffix and base + "mega" + suffix in forms:
        return base + "mega" + suffix
    for cand in (base + "mega", base + "megax", base + "megay"):
        if cand in forms:
            return cand
    return forms[0]


def stone_base_species(item_id: Optional[str]) -> Optional[str]:
    """メガ石 id → その石でメガシンカする基本種 id (石でなければ None)"""
    form = stone_table().get(_toid(item_id)) if item_id else None
    return _base_of(form) if form else None


def same_species_stones(item_a: Optional[str], item_b: Optional[str]) -> bool:
    """2 つの持ち物 id が同じ種のメガ石 (フォルム違い: ガブリアスナイト と ガブリアスナイトZ 等) か。純粋"""
    if not item_a or not item_b or _toid(item_a) == _toid(item_b):
        return False
    a, b = stone_base_species(item_a), stone_base_species(item_b)
    return bool(a) and a == b


@lru_cache(maxsize=4)
def _stone_usage(snapshot_id: Optional[int]) -> dict:
    """{基本種 id: {石 id: 使用率%}} (使用率 DB)。読めなければ空"""
    from champions_agent.data import database as db
    stones = stone_table()
    out: dict = {}
    try:
        with db.get_connection() as conn:
            snap = snapshot_id or db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
            rows = conn.execute("SELECT pokemon_name, item_name, usage_percent FROM item_usage WHERE snapshot_id=?",
                                (snap,)).fetchall()
    except Exception:
        return out
    for r in rows:
        item = _toid(r[1])
        if item in stones:
            out.setdefault(_toid(r[0]), {})[item] = float(r[2] or 0.0)
    return out


def stone_prior(species_id: str, snapshot_id: Optional[int] = None, usage: Optional[dict] = None,
                default: float = GIMMICK_DEFAULT_STONE_PRIOR, floor: float = GIMMICK_MIN_PRIOR) -> dict:
    """種族が「メガ石を持っている」事前分布 {メガ後 id: 確率}。
    使用率 DB に石の使用率があればそれ (%→0..1)、石は持てるが使用率が無い種は default を姿の数で按分。
    石を持てない種は空。floor 未満の姿は落とす。usage を渡せば DB を読まない (テスト用)"""
    sid = _toid(species_id)
    forms = stones_by_base().get(sid) or []
    if not forms:
        return {}
    table = usage if usage is not None else _stone_usage(snapshot_id)
    rows = table.get(sid) or {}
    out = {msid: rows.get(item, 0.0) / 100.0 for item, msid in forms if rows.get(item, 0.0) / 100.0 >= floor}
    if not out and not rows:
        out = {msid: default / len(forms) for _item, msid in forms}
    return out


def expected_mega(species_id: str, item_id: Optional[str] = None, is_mega: bool = False,
                  mega_used: bool = False, snapshot_id: Optional[int] = None, usage: Optional[dict] = None) -> dict:
    """その個体がこの先メガシンカする姿の確率 {メガ後 id: p}。
    判明情報で上書き: 既にメガ後 / 陣営のメガ権利が消費済み → 空、持ち物が石と判明 → その姿 1.0、
    石以外と判明 → 空、不明 → 事前分布"""
    if is_mega or mega_used or is_mega_form(species_id):
        return {}
    item = _toid(item_id) if item_id else ""
    if item:
        msid = stone_table().get(item)
        return {msid: 1.0} if msid and msid in mega_forms(species_id) else {}
    return stone_prior(species_id, snapshot_id, usage)


def mega_probability(species_id: str, item_id: Optional[str] = None, is_mega: bool = False,
                     mega_used: bool = False, snapshot_id: Optional[int] = None, usage: Optional[dict] = None) -> float:
    """この先メガシンカする確率の合計 (0..1)"""
    return min(1.0, sum(expected_mega(species_id, item_id, is_mega, mega_used, snapshot_id, usage).values()))


def mega_view(view, mega_sid: str):
    """MonView をメガ後の姿に (種族値・タイプ・特性 (固定) ・種族 id)。図鑑に無ければそのまま"""
    from advisor.dex import get_dex
    from vision.abilities import fixed_ability
    sp = get_dex().species(mega_sid)
    if sp is None:
        return view
    ability = fixed_ability(mega_sid, is_mega=True) or view.ability
    return replace(view, species_id=mega_sid, types=list(sp["types"]), base=dict(sp["baseStats"]), ability=ability)


def mixture_base_stats(species_id: str, probs: dict) -> dict:
    """メガシンカの期待値としての種族値: (1 − Σp) × 素 + Σ p_f × メガ後_f。学習の観測や粗い脅威評価用"""
    from advisor.dex import get_dex
    dex = get_dex()
    base = dict((dex.species(_toid(species_id)) or {}).get("baseStats") or {})
    if not base or not probs:
        return base
    total = min(1.0, sum(probs.values()))
    out = {k: (1.0 - total) * float(v) for k, v in base.items()}
    for msid, p in probs.items():
        mb = (dex.species(msid) or {}).get("baseStats") or base
        for k in out:
            out[k] += float(p) * float(mb.get(k, base[k]))
    return out


def team_mega_probs(entries, mega_used: bool = False, snapshot_id: Optional[int] = None,
                    usage: Optional[dict] = None) -> list:
    """陣営 6 体分: entries = [(species_id, item_id or None, is_mega)] → [{"species", "p", "forms"}]"""
    out = []
    for species_id, item_id, is_mega in entries:
        forms = expected_mega(species_id, item_id, is_mega, mega_used, snapshot_id, usage)
        out.append({"species": _toid(species_id), "p": min(1.0, sum(forms.values())), "forms": forms})
    return out
