"""Smogon の chaos JSON (重み付き件数) → 用途別の確率 (2026-10-30 レビュー、開発者承認)。

評価の道具 (tools/usage_prior_eval.py) だけが使う。助言・既定の方式・使用率 DB はこのモジュールを読まない。

chaos の数は百分率ではなく重み付き件数 (レーティングの不確実性で重みを付けた個体の数)。個体ごとに
  W = Abilities の重みの合計 (= Items の合計 = Spreads の合計。個体は特性・持ち物・配分を 1 つずつ持つ)
で、Moves の重みの合計は 4W (技は 4 枠。空き枠は "" の項目)。"Raw count" は重みの無い件数なので分母に使わない。
用途ごとに分母が違う:
  (a) 持ち物の分布  items[i]      = w_i / W          (個体あたり 1 つ。"nothing" = 持ち物なしは分母に入れて一覧からは外す)
  (b) 特性の分布    abilities[a]  = w_a / W          (同上)
  (c) 技の保有確率  moves_hold[m] = w_m / W          (技ごとに「持っている割合」。合計は最大 4 = 400%)
行動の選択確率 (その技を使う確率) は作らない。採用統計から出せるのは保有確率だけで、使う確率は局面で決まる
(2026-10-30 レビュー: 以前の「保有 ÷ 4 の暫定」は削除。最初の単位は技の保有・持ち物・特性の予測の評価まで)。
全技の重みの合計 (4W) で割った値は (c) ではない (メガボーマンダの すてみタックル: 保有 約 79.2%、4W で割ると 約 19.8%)。
周辺分布の積を実在する型の分布とは見なさない。配分 (Spreads) は配分の分布として別に持つ。

名寄せ: Showdown の表記 ("Salamence-Mega"、"Lucario-Mega-Z"、"Indeedee-F") → 英数字の小文字 (salamencemega …) →
SPECIES_OVERRIDES → advisor.dex.canonical_species_id (図鑑の別名)。メガは chaos では別の項目 (石を持つ個体) で、
使用率 DB (championsbattledata) は基本種にまとめている。基本種 + メガの合算は group_view で作る。
配分の単位: 値が全部 0〜32 なら能力ポイント (そのまま)。32 を超える値があれば努力値 (0〜252) とみなし
advisor.ev_infer._ev_to_points で能力ポイントに換算する (ファイル単位で判定して記録)。
合法性: 図鑑 (advisor/data/dex.json) に無い種・技、名前表 (vision/data/jp_names.json) に無い持ち物・特性は落として、
件数と重みを checks に残す。分母 W は落とす前の値のまま (残りの確率は変わらない)。
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from champions_agent.config import SMOGON_CONVERT_VERSION

# chaos の表記 (英数字の小文字にしたもの) → この プロジェクトの種族 id。基本則 (英数字の小文字 + 図鑑の別名) で引けないものだけ
SPECIES_OVERRIDES = {
    "meowsticfmega": "meowsticfemalemega",
    "meowsticmmega": "meowsticmalemega",
}
# chaos の持ち物 id → この プロジェクトの持ち物 id (名前表の id が古い綴り)
ITEM_OVERRIDES = {
    "leek": "stick",
}
EMPTY_KEYS = ("", "empty")          # 技の空き枠・未記入
NO_ITEM_KEYS = ("nothing", "")      # 持ち物なし
SPREAD_POINT_MAX = 32               # 能力ポイントの 1 能力の上限 (これを超える値があれば努力値の表記)
_MEGA_RE = re.compile(r"mega[xyz]?$")
_STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def toid(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def species_project_id(name: str, canonical: Optional[Callable[[str], str]] = None) -> str:
    """chaos の種族名 → この プロジェクトの種族 id (純粋。canonical は図鑑の別名の正規化、既定は恒等)"""
    sid = toid(name)
    sid = SPECIES_OVERRIDES.get(sid, sid)
    return canonical(sid) if canonical else sid


def mega_base_id(species_id: str, is_known: Callable[[str], bool]) -> Optional[str]:
    """メガ形態の id → 基本種の id。メガでなければ None (純粋)。
    末尾の mega / megax / megay / megaz を外した id が図鑑にあるときだけメガとみなす (yanmega・meganium はメガでない)"""
    if not species_id:
        return None
    m = _MEGA_RE.search(species_id)
    if not m or m.start() == 0:
        return None
    base = species_id[:m.start()]
    return base if is_known(base) else None


def group_id(species_id: str, is_known: Callable[[str], bool]) -> str:
    """基本種 + メガの組の id (= 基本種の id)"""
    return mega_base_id(species_id, is_known) or species_id


def detect_spread_unit(payload_data: dict) -> str:
    """ファイル全体の配分の単位 (純粋): "points" (全部 0〜32) か "evs" (32 を超える値がある)"""
    for e in payload_data.values():
        for key in (e.get("Spreads") or {}):
            if ":" not in key:
                continue
            for x in key.split(":", 1)[1].split("/"):
                try:
                    if int(x) > SPREAD_POINT_MAX:
                        return "evs"
                except ValueError:
                    continue
    return "points"


def parse_spread(key: str, unit: str) -> Optional[dict]:
    """'Adamant:1/32/0/0/1/32' → {"nature": "adamant", "points": {"hp": 1, ...}} (純粋)。形が違えば None"""
    if ":" not in key:
        return None
    nature, vals = key.split(":", 1)
    parts = vals.split("/")
    if len(parts) != 6:
        return None
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    raw = dict(zip(_STATS, nums))
    if unit == "evs":
        from advisor.ev_infer import _ev_to_points
        pts = _ev_to_points(raw)
        points = {k: int(pts.get(k, 0)) for k in _STATS}
    else:
        points = raw
    return {"nature": nature.strip().lower(), "points": points}


def _weight_total(entry: dict) -> tuple:
    """個体の重み W とその根拠 (純粋)"""
    for key in ("Abilities", "Items", "Spreads"):
        tot = sum(float(v) for v in (entry.get(key) or {}).values())
        if tot > 0:
            return tot, key
    tot = sum(float(v) for v in (entry.get("Moves") or {}).values()) / 4.0
    return tot, "Moves/4"


def convert_entry(entry: dict, move_ok: Callable[[str], bool], item_ok: Callable[[str], bool],
                  ability_ok: Callable[[str], bool], spread_unit: str = "points") -> tuple:
    """1 種の chaos の項目 → (変換した dict, この種で落としたもの) (純粋)。技は保有確率だけ (使う確率は作らない)"""
    W, basis = _weight_total(entry)
    dropped = {"moves": {}, "items": {}, "abilities": {}, "move_hold_over_1": 0}
    out = {"weight": W, "weight_basis": basis, "raw_count": entry.get("Raw count"), "usage": entry.get("usage"),
           "items": {}, "abilities": {}, "moves_hold": {}, "spreads": [],
           "item_nothing": 0.0, "move_empty_slots": 0.0}
    if W <= 0:
        return out, dropped
    for k, w in (entry.get("Items") or {}).items():
        iid = toid(k)
        if iid in NO_ITEM_KEYS:
            out["item_nothing"] += float(w) / W
            continue
        iid = ITEM_OVERRIDES.get(iid, iid)
        if not item_ok(iid):
            dropped["items"][iid] = dropped["items"].get(iid, 0.0) + float(w)
            continue
        out["items"][iid] = out["items"].get(iid, 0.0) + float(w) / W
    for k, w in (entry.get("Abilities") or {}).items():
        aid = toid(k)
        if not aid:
            continue
        if not ability_ok(aid):
            dropped["abilities"][aid] = dropped["abilities"].get(aid, 0.0) + float(w)
            continue
        out["abilities"][aid] = out["abilities"].get(aid, 0.0) + float(w) / W
    for k, w in (entry.get("Moves") or {}).items():
        mid = toid(k)
        if mid in EMPTY_KEYS:
            out["move_empty_slots"] += float(w) / W
            continue
        if not move_ok(mid):
            dropped["moves"][mid] = dropped["moves"].get(mid, 0.0) + float(w)
            continue
        h = float(w) / W
        if h > 1.0 + 1e-9:
            dropped["move_hold_over_1"] += 1
            h = 1.0
        out["moves_hold"][mid] = min(1.0, out["moves_hold"].get(mid, 0.0) + h)
    for k, w in (entry.get("Spreads") or {}).items():
        sp = parse_spread(k, spread_unit)
        if sp is None:
            continue
        sp["p"] = float(w) / W
        out["spreads"].append(sp)
    out["spreads"].sort(key=lambda s: -s["p"])
    return out, dropped


def convert_chaos(payload: dict, species_ok: Callable[[str], bool], move_ok: Callable[[str], bool],
                  item_ok: Callable[[str], bool], ability_ok: Callable[[str], bool],
                  canonical: Optional[Callable[[str], str]] = None) -> dict:
    """chaos JSON 全体 → {"species": {id: 変換した dict}, "checks": 落とした件数, "spread_unit", "convert_version", ...} (純粋)。
    species_ok / move_ok / item_ok / ability_ok は合法性 (図鑑・名前表にあるか) の判定。テストでは差し替える"""
    data = payload.get("data") or {}
    unit = detect_spread_unit(data)
    species: dict = {}
    checks = {"species_total": 0, "species_kept": 0, "species_dropped": [], "species_dropped_weight": 0.0,
              "species_merged": [], "moves_dropped": {}, "items_dropped": {}, "abilities_dropped": {},
              "move_hold_over_1": 0, "weight_basis": {}}
    for name, entry in data.items():
        if toid(name) in EMPTY_KEYS:
            continue
        checks["species_total"] += 1
        sid = species_project_id(name, canonical)
        conv, dropped = convert_entry(entry, move_ok, item_ok, ability_ok, unit)
        if not species_ok(sid):
            checks["species_dropped"].append(name)
            checks["species_dropped_weight"] += conv["weight"]
            continue
        if sid in species:
            # 表記違いで同じ id になった項目 (起きないはずだが、起きたら重みで合算して記録する)
            checks["species_merged"].append(name)
            species[sid] = merge_views([species[sid], conv])
        else:
            species[sid] = dict(conv, name=name)
        checks["species_kept"] += 1
        checks["weight_basis"][conv["weight_basis"]] = checks["weight_basis"].get(conv["weight_basis"], 0) + 1
        checks["move_hold_over_1"] += dropped["move_hold_over_1"]
        for cat in ("moves", "items", "abilities"):
            agg = checks[f"{cat}_dropped"]
            for k, w in dropped[cat].items():
                row = agg.setdefault(k, {"n_species": 0, "weight": 0.0})
                row["n_species"] += 1
                row["weight"] += w
    info = payload.get("info") or {}
    return {"species": species, "checks": checks, "spread_unit": unit, "convert_version": SMOGON_CONVERT_VERSION,
            "metagame": info.get("metagame"), "cutoff": info.get("cutoff"),
            "number_of_battles": info.get("number of battles")}


def merge_views(entries: list) -> dict:
    """複数の項目 (同じ組の基本種とメガ等) を重み W で合算した dict (純粋)。保有確率・持ち物・特性は W の加重平均"""
    entries = [e for e in entries if e and e.get("weight", 0) > 0]
    if not entries:
        return {"weight": 0.0, "items": {}, "abilities": {}, "moves_hold": {}, "spreads": [],
                "item_nothing": 0.0, "move_empty_slots": 0.0, "members": []}
    if len(entries) == 1:
        e = entries[0]
        return dict(e, members=[e.get("name")])
    W = sum(e["weight"] for e in entries)
    out = {"weight": W, "items": {}, "abilities": {}, "moves_hold": {}, "spreads": [],
           "item_nothing": 0.0, "move_empty_slots": 0.0, "members": [e.get("name") for e in entries]}
    for e in entries:
        f = e["weight"] / W
        for cat in ("items", "abilities", "moves_hold"):
            for k, v in e.get(cat, {}).items():
                out[cat][k] = out[cat].get(k, 0.0) + f * v
        out["item_nothing"] += f * e.get("item_nothing", 0.0)
        out["move_empty_slots"] += f * e.get("move_empty_slots", 0.0)
        for sp in e.get("spreads", []):
            out["spreads"].append(dict(sp, p=f * sp["p"]))
    out["spreads"].sort(key=lambda s: -s["p"])
    return out


def group_index(species: dict, is_known: Callable[[str], bool]) -> dict:
    """{組の id (基本種): [種族 id, ...]} (純粋)"""
    idx: dict = {}
    for sid in species:
        idx.setdefault(group_id(sid, is_known), []).append(sid)
    for v in idx.values():
        v.sort()
    return idx


def group_view(species: dict, index: dict, species_id: str, is_known: Callable[[str], bool],
               view: str = "pooled") -> Optional[dict]:
    """状態の種族 id に対する Smogon の予測 (純粋)。無ければ None。
    view = "pooled": 基本種 + そのメガ全部を重みで合算 (メガかどうかを使わない。使用率 DB と同じ条件)
    view = "known" : 状態の種族がメガ形態で、その項目があればメガの項目だけ。それ以外は pooled"""
    if view == "known" and species_id in species and mega_base_id(species_id, is_known):
        return merge_views([species[species_id]])
    members = index.get(group_id(species_id, is_known)) or []
    if not members:
        return None
    return merge_views([species[m] for m in members])


def legal_vocab() -> dict:
    """合法性の判定に使う語彙 (図鑑・名前表を読む)。{"species_ok", "move_ok", "item_ok", "ability_ok", "canonical", "is_known"}"""
    import json

    from advisor.dex import canonical_species_id, get_dex
    from vision.normalize import JP_NAMES_PATH
    dex = get_dex()
    raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
    items = set((raw.get("items") or {}).values())
    abilities = set((raw.get("abilities") or {}).values())

    def is_known(sid: str) -> bool:
        return dex.species(sid) is not None

    return {"species_ok": is_known, "move_ok": lambda m: dex.move(m) is not None,
            "item_ok": lambda i: i in items, "ability_ok": lambda a: a in abilities,
            "canonical": canonical_species_id, "is_known": is_known}


def convert_with_vocab(payload: dict) -> dict:
    """図鑑・名前表を語彙にして convert_chaos を呼ぶ (語彙の読み込みだけが副作用)"""
    v = legal_vocab()
    return convert_chaos(payload, v["species_ok"], v["move_ok"], v["item_ok"], v["ability_ok"], v["canonical"])
