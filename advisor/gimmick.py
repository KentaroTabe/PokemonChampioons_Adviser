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
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class FormResolution:
    """個体の形態の解決結果 (resolve_form)。2026-10-09 M1a (docs/ADVICE_ACCURACY_PLAN_1009.md 段 A-1)。

    base_species: 基本種 (メガ前) の id。species: 現在形態の id (種族値・タイプ・固定特性の参照先)。
    types: 評価に使うタイプ (英語、図鑑の表記)。types_temporary: 観測した一時的なタイプ変化 (へんげんじざい等) を使ったか。
    is_mega: 現在形態がメガ形態か。ability: 現在形態で一意に決まる特性 (無ければ None)。
    note: 入力の矛盾・未確定を解決したときの説明 (矛盾が無ければ None)"""
    base_species: str
    species: str
    types: tuple
    types_temporary: bool
    is_mega: bool
    ability: Optional[str]
    note: Optional[str] = None


def _types_en(types) -> list:
    """状態の types (日本語 / 英語の混在可) → 図鑑の表記の英語 (Normal 等)"""
    from advisor.engine import type_ja2en
    ja2en = type_ja2en()
    out = []
    for t in types or []:
        en = ja2en.get(t, t)
        en = str(en)[:1].upper() + str(en)[1:].lower() if en else en
        if en and en not in out:
            out.append(en)
    return out


def resolve_form(p: Optional[dict]) -> Optional[FormResolution]:
    """個体 (PokemonState の辞書 / その簡約) の基本種・現在形態・評価に使うタイプを解決する (副作用なし。図鑑と石の表を引く)。

    ダメージ (advisor.engine.build_mon_view)・行動順 (同じ MonView の素早さ)・RL の入力 (advisor.rl_bridge.encode_state) が
    この 1 つの結果を使う。見た目の名前 (species_ja = HUD の通常名) は個体の同定用で、ここでは見ない。
    - species_id がメガ形態 → そのまま (メガ後)。
    - is_mega なのに species_id が基本種 (HUD の通常名で戻された・文言のメガ名が読めなかった) → 持ち物の石でメガ形態を引く
      (石の表 = requiredItem)。持ち物が不明・総称 (megastone) ならメガ形態が 1 つの種だけ導出する (X/Y 等で決まらなければ
      基本種のまま = 曖昧な形態を強制確定しない)。持ち物がこの種の石以外と分かっていれば矛盾として基本種のまま。
    - is_mega でなければ基本種のまま (陣営のメガ権利の消費 mega_used は個体の形態を変えない)。
    タイプ: 一時的なタイプ変化の印 (type_changed) があれば観測値。メガ形態では、観測値が空・基本種の図鑑タイプ (メガ前の
    読みの残り)・メガ形態の図鑑タイプのどれかならメガ形態の図鑑タイプ、それ以外は観測値 (一時的な変化とみなす)。
    メガでなければ従来どおり観測値 (無ければ図鑑)。図鑑に無い種は None"""
    if not p:
        return None
    from advisor.dex import get_dex
    from vision.abilities import fixed_ability, mega_form_id
    dex = get_dex()
    sid = _toid(p.get("species_id"))
    if not sid or dex.species(sid) is None:
        return None
    note = None
    if is_mega_form(sid):
        base, cur = _base_of(sid), sid
    else:
        base, cur = sid, sid
        if p.get("is_mega"):
            item = _toid(p.get("item_id"))
            form = None
            if item and item != "megastone":
                stone = stone_table().get(item)
                if stone and stone in mega_forms(sid):
                    form = stone
                else:
                    note = f"メガの印と持ち物 ({item}) が矛盾するため形態は {sid} のまま"
            else:
                form = mega_form_id(sid, None)
                if form is None:
                    note = f"メガ後の形態が 1 つに決まらないため {sid} のまま"
            if form and dex.species(form) is not None:
                cur = form
    if dex.species(base) is None:
        base = cur
    is_mega_cur = cur != base or is_mega_form(cur)
    dex_cur = list(dex.species(cur)["types"])
    dex_base = list(dex.species(base)["types"])
    obs = _types_en(p.get("types"))
    temporary = False
    if p.get("type_changed") and obs:
        types, temporary = obs, set(obs) != set(dex_cur)
    elif is_mega_cur:
        if not obs or set(obs) in (set(dex_base), set(dex_cur)):
            types = dex_cur
        else:
            types, temporary = obs, True
    else:
        types = obs or dex_cur
        temporary = bool(obs) and set(obs) != set(dex_cur)
    ability = fixed_ability(cur, is_mega=is_mega_cur, item_id=p.get("item_id"))
    return FormResolution(base_species=base, species=cur, types=tuple(types), types_temporary=temporary,
                          is_mega=is_mega_cur, ability=ability, note=note)


def with_resolved_form(p: Optional[dict]) -> Optional[dict]:
    """個体の辞書の複製に、解決した形態 (species_id = 現在形態、types = 評価に使うタイプ (英語)、is_mega) を入れる (純粋)。
    解決できなければ元の辞書をそのまま返す。RL の入力 (advisor.rl_bridge.encode_state) が状態全体に適用する"""
    form = resolve_form(p)
    if form is None:
        return p
    return dict(p, species_id=form.species, types=list(form.types), is_mega=form.is_mega)


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
