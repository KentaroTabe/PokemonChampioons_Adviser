"""持ち物の手がかりの集計の共通処理 (読み取りだけ。2026-10-30 レビュー、開発者承認)。CLI との対応は tools/item_cues/__init__.py。

- 対戦ログの読み込み (read_jsonl は tools.usage_prior_eval と共通)、対象の対戦の除外規則、行の取り出し (状態行・events 行・advice 行・version 行)
- 個体の鍵: 対戦 × 側 × 基本種 (メガ形態は基本種にまとめる。10/30 の集計と同じ。usage_prior_eval は party の枠を鍵にする)
- 帰属: events 行ごとの場の個体 (build_timeline。2 通り)
- 出場区間: 技の実行を交代・ひんしの発火で切った列 (cut_stints)。連続・否定の判定と守りの条件は純粋な関数
- 持ち物の判明 (状態の item)・自分側の登録の持ち物、天候・フィールド・壁の開始と終了 (episodes)
- 再現の記録 (repro_record) と出力 (write_outputs)

純粋な部分 (帰属・出場区間・連続・守りの条件・開始の束ね・再現率) は外部環境なしで動く (tests/test_item_cues.py)。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from champions_agent import config as C
from tools.usage_prior_eval import _sha16 as file_sha16
from tools.usage_prior_eval import read_jsonl

REPO = Path(__file__).resolve().parent.parent.parent
SIDES = ("player", "opponent")
STATE_ROW_TYPES = ("scene", "advice")

# 帰属: events 行の技・持ち物の発火を、どの個体のものとするか
ATTR_NEXT_ON_SWITCH = "next_on_switch"   # 直前の状態行の場の個体。同じ側の交代の発火の後 (次の状態行まで) は次の状態行の場の個体 (主)
ATTR_LAST_STATE = "last_state"           # 直前の状態行の場の個体だけ (10/30 の negation_guard.py / recall_cues.py)
ATTRIBUTIONS = (ATTR_NEXT_ON_SWITCH, ATTR_LAST_STATE)

# 対象の対戦の除外規則 (再現の記録に残す)
EXCLUSION_RULES = (
    "対象は <logs>/battle_*.jsonl (ファイル名の順)",
    "events 行に技 (move_*) か交代 (switch_*) の発火が 1 つも無いファイルは対戦として数えない (no_move_or_switch)",
    "読めない行 (JSON でない・空行) は飛ばす (tools.usage_prior_eval.read_jsonl)",
    "session 行の dataset_kind で除外しない (10/30 の集計と同じ。件数だけ記録に残す)",
)

# vision の発火 ID → 効果 (vision/events.py の辞書・_apply_ability_effect の表と同じ値)
MEGA_ITEM_RE = re.compile(r"ite[xyz]?$")      # メガストーンの id の目安 (10/30 の集計と同じ近似。eviolite も当たる)
WEATHER_MSG = {"rain_start": "rain", "sun_start": "sun", "sand_start": "sandstorm", "snow_start": "snow"}
WEATHER_END = {"rain_end": "rain", "sun_end": "sun", "sand_end": "sandstorm", "snow_end": "snow"}
WEATHER_ABIL = {"drizzle": "rain", "drought": "sun", "sandstream": "sandstorm", "snowwarning": "snow",
                "orichalcumpulse": "sun"}
WEATHER_MOVE = {"raindance": "rain", "sunnyday": "sun", "sandstorm": "sandstorm", "snowscape": "snow",
                "hail": "snow", "chillyreception": "snow"}
TERRAIN_MSG = {"electric_terrain": "electric", "grassy_terrain": "grassy", "psychic_terrain": "psychic",
               "misty_terrain": "misty"}
TERRAIN_ABIL = {"electricsurge": "electric", "grassysurge": "grassy", "psychicsurge": "psychic",
                "mistysurge": "misty", "hadronengine": "electric"}
TERRAIN_MOVE = {"electricterrain": "electric", "grassyterrain": "grassy", "psychicterrain": "psychic",
                "mistyterrain": "misty"}
SCREEN_MOVE = {"reflect": "reflect", "lightscreen": "lightscreen", "auroraveil": "auroraveil"}
SCREEN_END = {"reflect_end": "reflect", "lightscreen_end": "lightscreen", "auroraveil_end": "auroraveil"}
STRUGGLE = "struggle"

_SIDE_ID = re.compile(r"(ability|move|item)_(player|opponent)_(.+)$")


# ---------------------------------------------------------------- 読み込み・除外

def battle_files(logs_dir: Path) -> list:
    return sorted(Path(logs_dir).glob("battle_*.jsonl"))


def fired_of(r: dict) -> list:
    return list(r.get("fired") or [])


def is_battle(rows: list) -> bool:
    """除外規則: events 行に技・交代の発火が 1 つ以上ある (純粋)"""
    return any(f.startswith(("move_", "switch_")) for r in rows if r.get("type") == "events" for f in fired_of(r))


def battle_month(name: str) -> str:
    """battle_YYYYMMDD_HHMMSS.jsonl → YYYYMM"""
    m = re.match(r"battle_(\d{6})", name)
    return m.group(1) if m else "?"


def load_battles(logs_dir: Path) -> dict:
    """→ {"files": [{"name", "sha16"}], "battles": [{"name", "rows"}], "excluded": {理由: [name]}, "all_rows": {name: rows}}"""
    files, battles, excluded, all_rows = [], [], defaultdict(list), {}
    for p in battle_files(logs_dir):
        rows = read_jsonl(p)
        files.append({"name": p.name, "sha16": file_sha16(p)})
        all_rows[p.name] = rows
        if is_battle(rows):
            battles.append({"name": p.name, "rows": rows})
        else:
            excluded["no_move_or_switch"].append(p.name)
    return {"files": files, "battles": battles, "excluded": dict(excluded), "all_rows": all_rows}


# ---------------------------------------------------------------- 行の取り出し

def is_state_row(r: dict) -> bool:
    return r.get("type") in STATE_ROW_TYPES and isinstance(r.get("state"), dict)


def state_rows(rows: list) -> list:
    return [r for r in rows if is_state_row(r)]


def event_rows(rows: list) -> list:
    return [r for r in rows if r.get("type") == "events"]


def advice_rows(rows: list) -> list:
    """advice 行を時刻順 (同時刻はファイルの順) に (usage_prior_eval.extract_observations と同じ並べ方)"""
    adv = [(i, r) for i, r in enumerate(rows) if r.get("type") == "advice" and isinstance(r.get("t"), (int, float))]
    adv.sort(key=lambda ir: (ir[1]["t"], ir[0]))
    return [r for _i, r in adv]


def version_rows(rows: list) -> list:
    return [r for r in rows if r.get("type") == "version"]


def base_sid(sid: Optional[str]) -> Optional[str]:
    """個体の鍵の種: メガ形態 (…mega / megax / megay) を基本種にまとめる"""
    if not sid:
        return None
    for suf in ("megax", "megay", "mega"):
        if sid.endswith(suf) and len(sid) > len(suf):
            return sid[: -len(suf)]
    return sid


def active_of(state: Optional[dict], side: str) -> Optional[dict]:
    sd = (state or {}).get(side) or {}
    idx, party = sd.get("active"), sd.get("party") or []
    if isinstance(idx, int) and 0 <= idx < len(party) and isinstance(party[idx], dict):
        return party[idx]
    return None


def active_base(state: Optional[dict], side: str) -> Optional[str]:
    return base_sid((active_of(state, side) or {}).get("species"))


def individual_key(battle: str, side: str, species: Optional[str]) -> tuple:
    """個体の鍵 = (対戦, 側, 基本種)"""
    return battle, side, base_sid(species)


# ---------------------------------------------------------------- 帰属・出場区間 (純粋)

def build_timeline(rows: list, attribution: str = ATTR_NEXT_ON_SWITCH) -> list:
    """events 行ごとに場の個体 (基本種) を決める → [{"i", "t", "turn", "fired", "texts", "active": {側: 基本種}}] (純粋)。

    next_on_switch: 直前の状態行の場の個体。同じ側の交代の発火があった行から次の状態行までは、次の状態行の場の個体。
    last_state: 直前の状態行の場の個体だけ"""
    if attribution not in ATTRIBUTIONS:
        raise ValueError(f"attribution: {attribution}")
    next_state_at: list = [None] * (len(rows) + 1)
    nxt = None
    for i in range(len(rows) - 1, -1, -1):
        if is_state_row(rows[i]):
            nxt = rows[i]["state"]
        next_state_at[i] = nxt
    last_state, pending = None, {s: False for s in SIDES}
    out = []
    for i, r in enumerate(rows):
        if is_state_row(r):
            last_state, pending = r["state"], {s: False for s in SIDES}
            continue
        if r.get("type") != "events":
            continue
        fired = fired_of(r)
        act = {}
        for side in SIDES:
            if attribution == ATTR_NEXT_ON_SWITCH and f"switch_{side}" in fired:
                pending[side] = True
            act[side] = active_base(next_state_at[i + 1] if pending[side] else last_state, side)
        out.append({"i": i, "t": r.get("t"), "turn": r.get("turn"), "fired": fired, "texts": list(r.get("texts") or []),
                    "active": act})
    return out


def state_actives(rows: list) -> list:
    """状態行ごとの場の個体 → [(行番号, {側: 基本種})] (純粋)"""
    return [(i, {s: active_base(r["state"], s) for s in SIDES}) for i, r in enumerate(rows) if is_state_row(r)]


def max_turn(timeline: list) -> int:
    turns = [e["turn"] for e in timeline if isinstance(e.get("turn"), int)]
    return max(turns) if turns else 0


def cut_stints(timeline: list, side: str) -> list:
    """出場区間: 側 side の技の実行を、その側の交代の発火と、ひんしの発火 (側を問わない。安全側) で切る (純粋)。
    → [{"sid", "moves": [{"move", "turn", "t", "i"}]}] (区間の始まりの順)。帰属は timeline の active[side]。
    同じ区間に別の個体の技が挟まっても (帰属の揺れ)、個体ごとに別の区間として続ける (10/30 の集計と同じ)"""
    pat = re.compile(rf"move_{side}_(.+)$")
    open_: dict = {}
    out: list = []
    for e in timeline:
        sid = e["active"].get(side)
        for f in e["fired"]:
            if f == f"switch_{side}" or f == "faint":
                open_.clear()
                continue
            m = pat.match(f)
            if not m or not sid:
                continue
            st = open_.get(sid)
            if st is None:
                st = {"sid": sid, "moves": []}
                open_[sid] = st
                out.append(st)
            st["moves"].append({"move": m.group(1), "turn": e.get("turn"), "t": e.get("t"), "i": e["i"]})
    return out


def _sep_ok(a: dict, b: dict, min_sep: float) -> bool:
    """2 つの技の時刻の差が min_sep 以上 (時刻が無ければ偽 = 使わない側)"""
    ta, tb = a.get("t"), b.get("t")
    return isinstance(ta, (int, float)) and isinstance(tb, (int, float)) and (tb - ta) >= min_sep


def stint_cues(moves: list, n_lock: int = C.ITEM_CUE_LOCK_REPEAT, min_sep: float = C.ITEM_CUE_MIN_SEP_SEC) -> dict:
    """1 つの出場区間の技の列 → 手がかり (純粋):
    lock = 同じ技 n_lock 連続、lock_distinct_turns = その最後の n_lock 回の turn の値が全部違う、
    lock_sep = 最後の n_lock 回の隣り合う時刻の差が全部 min_sep 以上、dup_same_turn = 同じ技が同じ turn の値で続いた回数、
    negation = 隣り合う 2 つが違う技 (最初の 1 組を negation_pair に)"""
    lock = lock_turns = lock_sep = False
    dup = 0
    pair = None
    run: list = []
    for x in moves:
        if run and run[-1]["move"] == x["move"]:
            if run[-1]["turn"] == x["turn"]:
                dup += 1
            run.append(x)
        else:
            if run and pair is None:
                pair = (run[-1], x)
            run = [x]
        if len(run) >= n_lock:
            lock = True
            tail = run[-n_lock:]
            if len({y["turn"] for y in tail}) == n_lock:
                lock_turns = True
            if all(_sep_ok(a, b, min_sep) for a, b in zip(tail, tail[1:])):
                lock_sep = True
    return {"n_moves": len(moves), "lock": lock, "lock_distinct_turns": lock_turns, "lock_sep": lock_sep,
            "dup_same_turn": dup, "negation": pair is not None, "negation_pair": pair}


def individual_cues(stints: list, n_lock: int = C.ITEM_CUE_LOCK_REPEAT, min_sep: float = C.ITEM_CUE_MIN_SEP_SEC) -> dict:
    """出場区間の列 → 個体 (基本種) ごとの手がかりの集約 (純粋)。negation_pair は時刻順で最初の 1 組"""
    out: dict = {}
    for st in stints:
        c = stint_cues(st["moves"], n_lock, min_sep)
        a = out.setdefault(st["sid"], {"n_moves": 0, "lock": False, "lock_distinct_turns": False, "lock_sep": False,
                                       "dup_same_turn": 0, "negation": False, "negation_pair": None})
        a["n_moves"] += c["n_moves"]
        a["dup_same_turn"] += c["dup_same_turn"]
        for k in ("lock", "lock_distinct_turns", "lock_sep", "negation"):
            a[k] = a[k] or c[k]
        if a["negation_pair"] is None and c["negation_pair"] is not None:
            a["negation_pair"] = c["negation_pair"]
    return out


def guard_flags(a: dict, b: dict, side: str, sid: str, actives_between: Iterable[dict],
                min_sep: float = C.ITEM_CUE_MIN_SEP_SEC) -> dict:
    """続けて起きる 2 つの技 (a → b、同じ出場区間 = 間に交代・ひんしの発火なし) の守りの条件 (純粋)。
    actives_between: 2 つの技の間の状態行の場の個体 ({側: 基本種})。

    same_turn: turn の値が同じ (10/30 の条件。数え漏れで正しい組も当たる)
    struggle: どちらかが わるあがき (§3.4 の 5。アンコール・へんしん はログから判定しないので未適用)
    active_changed: 間の状態行で場の個体が別の種になった (§3.4 の 2)
    too_close: 時刻の差が min_sep 未満、または時刻が無い (§3.4 の 4)
    clean_1030 = same_turn・struggle・active_changed のどれでもない (設計文書 §1.2 の「どれにも当たらない」)
    clean = struggle・active_changed・too_close のどれでもない (§3.4 の条件。§3.4 の 3 の処理の空白はログで測れないので未適用)"""
    same_turn = a.get("turn") == b.get("turn")
    struggle = STRUGGLE in (a.get("move"), b.get("move"))
    changed = any(act.get(side) and act.get(side) != sid for act in actives_between)
    too_close = not _sep_ok(a, b, min_sep)
    return {"same_turn": same_turn, "struggle": struggle, "active_changed": changed, "too_close": too_close,
            "clean_1030": not (same_turn or struggle or changed), "clean": not (struggle or changed or too_close)}


def actives_between(actives: list, i_from: int, i_to: int) -> list:
    """state_actives の結果から、行番号が (i_from, i_to) の間の状態行の場の個体 (純粋)"""
    return [act for i, act in actives if i_from < i < i_to]


def dt_bucket(a: dict, b: dict) -> str:
    ta, tb = a.get("t"), b.get("t")
    if not isinstance(ta, (int, float)) or not isinstance(tb, (int, float)):
        return "no_t"
    d = tb - ta
    for lim in (5, 10, 20, 30, 60):
        if d < lim:
            return f"<{lim}s"
    return ">=60s"


# ---------------------------------------------------------------- 持ち物

def item_kind(item: str) -> str:
    """状態の item の分類: mega (メガストーン) / marble (いのちのたま の誤解決、8/21 まで) / item"""
    if MEGA_ITEM_RE.search(item) or item == "megastone":
        return "mega"
    return "marble" if item == "marble" else "item"


def first_items(rows: list) -> dict:
    """状態行の party の item の、個体 (側, 基本種) ごとの最初の値 → {(側, 基本種): {"item", "t", "turn", "values": set}} (純粋)。
    相手側 = 表示で判明した持ち物、自分側 = 登録の持ち物"""
    out: dict = {}
    for r in state_rows(rows):
        st = r["state"]
        for side in SIDES:
            for p in (st.get(side) or {}).get("party") or []:
                if not isinstance(p, dict):
                    continue
                b, it = base_sid(p.get("species")), p.get("item")
                if not b or not it:
                    continue
                cur = out.setdefault((side, b), {"item": it, "t": r.get("t"), "turn": st.get("turn"), "values": set()})
                cur["values"].add(it)
    return out


def registered_items(rows: list) -> dict:
    """自分側の登録の持ち物 {基本種: item}"""
    return {b: v["item"] for (side, b), v in first_items(rows).items() if side == "player"}


def known_opponent_items(rows: list) -> dict:
    """相手の判明した持ち物 (メガストーン・marble を除く) {基本種: {"item", "t", "turn", "changed"}}"""
    return {b: {"item": v["item"], "t": v["t"], "turn": v["turn"], "changed": len(v["values"]) > 1}
            for (side, b), v in first_items(rows).items() if side == "opponent" and item_kind(v["item"]) == "item"}


def appeared(rows: list, side: str) -> set:
    """状態行で場に出た個体 (基本種)"""
    return {a for r in state_rows(rows) for a in [active_base(r["state"], side)] if a}


def item_fire(f: str) -> Optional[tuple]:
    """発火 ID item_<側>_<id> → (側, id)"""
    m = _SIDE_ID.match(f)
    return (m.group(2), m.group(3)) if m and m.group(1) == "item" else None


# ---------------------------------------------------------------- 天候・フィールド・壁 (純粋)

def _side_start(f: str, active: dict, kind: str, table: dict) -> Optional[tuple]:
    m = _SIDE_ID.match(f)
    if m and m.group(1) == kind and m.group(3) in table:
        side = m.group(2)
        return table[m.group(3)], side, kind, active.get(side)
    return None


def weather_start(f: str, active: dict) -> Optional[tuple]:
    """→ (天候, 側 | None, 信号 msg / ability / move, 設定した個体 | None)"""
    if f in WEATHER_MSG:
        return WEATHER_MSG[f], None, "msg", None
    return _side_start(f, active, "ability", WEATHER_ABIL) or _side_start(f, active, "move", WEATHER_MOVE)


def terrain_start(f: str, active: dict) -> Optional[tuple]:
    if f in TERRAIN_MSG:
        return TERRAIN_MSG[f], None, "msg", None
    return _side_start(f, active, "ability", TERRAIN_ABIL) or _side_start(f, active, "move", TERRAIN_MOVE)


def screen_start(f: str, active: dict) -> Optional[tuple]:
    return _side_start(f, active, "move", SCREEN_MOVE)


def weather_end(f: str, texts: list) -> Optional[tuple]:
    """→ (効果 | "*" = どれでも, 側 | None)"""
    return (WEATHER_END[f], None) if f in WEATHER_END else None


def terrain_end(f: str, texts: list) -> Optional[tuple]:
    return ("*", None) if f == "terrain_end" else None


def screen_end(f: str, texts: list) -> Optional[tuple]:
    """壁の終了は文言の「相手の / 味方の」で側を決める"""
    if f not in SCREEN_END:
        return None
    txt = " ".join(texts or [])
    return SCREEN_END[f], ("opponent" if "相手" in txt else ("player" if "味方" in txt else None))


FIELD_KINDS = {"weather": (weather_start, weather_end), "terrain": (terrain_start, terrain_end),
               "screen": (screen_start, screen_end)}


def episodes(timeline: list, start_fn: Callable, end_fn: Callable, merge_sec: float = C.ITEM_CUE_MERGE_SEC) -> tuple:
    """開始のエピソード (merge_sec 以内の同じ効果の信号を 1 回に束ねる) と終了の対応 (純粋)。
    → (エピソードの列 [{"val", "t", "turn", "side", "src": set, "setter", "ended", "end_t", "end_turn"}],
       開始を読めていない終了の発火の Counter)"""
    eps: list = []
    orphan = Counter()
    for e in timeline:
        tt, turn = e.get("t") or 0, e.get("turn")
        for f in e["fired"]:
            s = start_fn(f, e["active"])
            if s:
                val, side, src, setter = s
                last = eps[-1] if eps else None
                if last and last["val"] == val and tt - last["t"] <= merge_sec and not last["ended"]:
                    last["src"].add(src)
                    last["side"] = last["side"] or side
                    last["setter"] = last["setter"] or setter
                    continue
                eps.append({"val": val, "t": tt, "turn": turn, "side": side, "src": {src}, "setter": setter,
                            "ended": False, "end_t": None, "end_turn": None})
            en = end_fn(f, e.get("texts") or [])
            if en:
                ev, eside = en
                for ep in reversed(eps):
                    if not ep["ended"] and (ev == "*" or ep["val"] == ev) and (eside is None or ep["side"] in (None, eside)):
                        ep["ended"], ep["end_t"], ep["end_turn"] = True, tt, turn
                        break
                else:
                    orphan[f] += 1
    return eps, orphan


def superseded(eps: list, k: int, merge_sec: float = C.ITEM_CUE_MERGE_SEC, same_side_val: bool = False) -> bool:
    """k 番目の開始が後の開始で上書きされた (純粋)。天候・フィールドは後の開始すべて、壁は同じ効果・同じ側の後の開始"""
    ep = eps[k]
    return any(e2["t"] > ep["t"] + merge_sec and (not same_side_val or (e2["val"] == ep["val"] and e2["side"] == ep["side"]))
               for e2 in eps[k + 1:])


def ratio(num: int, den: int) -> Optional[float]:
    """再現率 (純粋)。母数 0 は None"""
    return round(num / den, 4) if den else None


# ---------------------------------------------------------------- 再現の記録・出力

def aggregate_sha16(sha16s: Iterable[str]) -> str:
    """各ファイルの sha16 を (ファイル名の順で) 連結した文字列の sha16 (純粋)"""
    return hashlib.sha256("".join(sha16s).encode("ascii")).hexdigest()[:16]


def git_head() -> dict:
    """コードの版 (取れなければ None)"""
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    head = run("rev-parse", "HEAD") or None
    status = run("status", "--porcelain")
    return {"head": head, "dirty": (bool(status) if status is not None else None)}


def version_summary(battles: list) -> dict:
    """version 行の集約: 版のある対戦の数、version_id・rules.git_commit・rules.usage_snapshot の値ごとの対戦の数"""
    vid, commit, snap = Counter(), Counter(), Counter()
    n = 0
    for b in battles:
        vs = version_rows(b["rows"])
        if not vs:
            continue
        n += 1
        v = vs[0]
        vid[str(v.get("version_id"))] += 1
        rules = v.get("rules") or {}
        commit[str(rules.get("git_commit"))[:12]] += 1
        snap[str(rules.get("usage_snapshot"))] += 1
    return {"n_battles_with_version": n, "version_id": dict(vid.most_common()), "git_commit": dict(commit.most_common()),
            "usage_snapshot": dict(snap.most_common())}


def config_values(names: Iterable[str]) -> dict:
    return {n: getattr(C, n) for n in names}


CONFIG_NAMES = ("ITEM_CUE_MERGE_SEC", "ITEM_CUE_MIN_SEP_SEC", "ITEM_CUE_LOCK_REPEAT", "ITEM_CUE_FIELD_BASE_TURNS",
                "ITEM_CUE_NOEND_REMAIN_TURNS", "ITEM_CUE_OPPORTUNITY_TURNS", "ITEM_CUE_MUST_END_TURNS",
                "ITEM_CUE_RECALL_MIN_MOVES", "ITEM_CUE_EXTENDER_ITEMS")


def repro_record(cli: str, logs_dir: Path, loaded: dict, args: dict, rules: Iterable[str] = (),
                 db: Optional[dict] = None) -> dict:
    """再現の記録 (出力の先頭に入れる)"""
    battles = loaded["battles"]
    names = [b["name"] for b in battles]
    months = Counter(battle_month(n) for n in names)
    kinds = Counter()
    for b in battles:
        sess = next((r for r in b["rows"] if r.get("type") == "session"), None)
        kinds[str(sess.get("dataset_kind")) if sess else "no_session"] += 1
    return {
        "cli": cli,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "logs": {"dir": str(logs_dir), "n_files": len(loaded["files"]), "n_battles": len(battles),
                 "excluded": {k: len(v) for k, v in loaded["excluded"].items()},
                 "excluded_files": loaded["excluded"],
                 "period": [names[0] if names else None, names[-1] if names else None],
                 "battles_by_month": dict(sorted(months.items())),
                 "dataset_kind": dict(kinds.most_common()),
                 "sha16_all_files": aggregate_sha16(f["sha16"] for f in loaded["files"]),
                 "sha16_files": {f["name"]: f["sha16"] for f in loaded["files"]}},
        "versions": version_summary(battles),
        "db": db,
        "exclusion_rules": list(EXCLUSION_RULES) + list(rules),
        "config": config_values(CONFIG_NAMES),
        "args": args,
        "code": git_head(),
    }


def md_table(header: list, rows: list) -> list:
    out = ["| " + " | ".join(str(h) for h in header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows]
    return out


def md_counter(c: dict) -> str:
    items = sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0]))) if all(isinstance(v, (int, float)) for v in c.values()) \
        else list(c.items())
    return "、".join(f"{k} {v}" for k, v in items) if items else "(なし)"


def render_record(rec: dict) -> list:
    lg = rec["logs"]
    out = ["## 再現の記録", ""]
    out += md_table(["項目", "値"], [
        ["CLI", rec["cli"]],
        ["実行引数", f"`{json.dumps(rec['args'], ensure_ascii=False)}`"],
        ["コードの版", f"{rec['code'].get('head')} (未コミットの変更 {rec['code'].get('dirty')})"],
        ["作成", rec["generated_at"]],
        ["対象ログ", lg["dir"]],
        ["ファイル / 対戦", f"{lg['n_files']} / {lg['n_battles']} (除外 {md_counter(lg['excluded'])})"],
        ["期間 (ファイル名)", f"{lg['period'][0]} 〜 {lg['period'][1]}"],
        ["月ごとの対戦", md_counter(lg["battles_by_month"])],
        ["session の dataset_kind", md_counter(lg["dataset_kind"])],
        ["全ファイルの sha16 を連結した sha16", lg["sha16_all_files"]],
        ["version 行のある対戦", f"{rec['versions']['n_battles_with_version']} (version_id {len(rec['versions']['version_id'])} 種、"
                                 f"rules.git_commit {md_counter(rec['versions']['git_commit'])})"],
        ["version 行の rules.usage_snapshot", md_counter(rec["versions"]["usage_snapshot"])],
        ["使用率 DB", "使わない" if not rec["db"] else
         f"{rec['db'].get('path')} (sha16 {rec['db'].get('sha16')}、snapshot {rec['db'].get('snapshots')})"],
        ["config", f"`{json.dumps(rec['config'], ensure_ascii=False)}`"],
    ])
    out += ["", "除外規則:", ""] + [f"- {r}" for r in rec["exclusion_rules"]] + [""]
    return out


def write_outputs(out_dir: Path, cli: str, md_lines: list, payload: dict) -> tuple:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{cli}_{time.strftime('%Y%m%d_%H%M%S')}"
    md, js = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=_json_default), encoding="utf-8")
    return md, js


def _json_default(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def add_common_args(ap) -> None:
    ap.add_argument("--logs", required=True, help="対戦ログのディレクトリ (logs/battles。読み取りだけ)")
    ap.add_argument("--out", default=str(REPO / "logs" / "item_cues"), help="出力の置き場 (<cli>_<日時>.md / .json)")
