"""助言エンジン (advisor.engine.evaluate) をそのままプレイヤーにする (advisor-as-player)。

これまで「アドバイザー自身の対戦強度」は一度も測られていなかった
(h2h/ベンチは RL方策、check_search_expert は探索プレイヤーの強さ)。
探索改良 (P7/P6/P8) を助言に統合する判断 (P9) には、助言エンジンを
プレイヤーとして同じ固定軸で測る経路が要る。

poke-env の Battle を助言エンジンの状態辞書 (vision/state と同じ形) に変換し、
evaluate() の best をそのまま行動にする。自分側の型 (能力ポイント/性格) は
チームテキストから登録して、実戦で my_team.json に登録済みなのと同じ条件にする。
"""
from __future__ import annotations

import re
import random
from pathlib import Path
import time
from typing import Optional

from advisor.ev_infer import _nature_mult
from advisor.infer import species_ja_name
from champions_agent.env.search_expert import (
    _STATUS_MAP, _WEATHER_MAP, _TERRAIN_MAP, _names)

_STATS = ("hp", "atk", "def", "spa", "spd", "spe")
_EV_KEYS = {"HP": "hp", "Atk": "atk", "Def": "def",
            "SpA": "spa", "SpD": "spd", "Spe": "spe"}

# 自分側の型 (シム用): species_ja -> {"ev","nature","item_ja","ability_ja"}
_SIM_BUILDS: dict = {}
_SIM_MOVES: dict = {}
_orig_get_my_build = None
_orig_get_my_moves = None


def _install_build_hook() -> None:
    """advisor.my_team.get_my_build / get_my_moves を、シム登録 → 従来の順で引くようにする。
    技も差し替える (2026-10-05): 選出の規則は登録技で自分側を評価するので、シムの候補チームが登録チームと同じ種を持つとき
    登録チームの技で評価されていた"""
    global _orig_get_my_build, _orig_get_my_moves
    import advisor.my_team as mt
    if _orig_get_my_build is not None:
        return
    _orig_get_my_build = mt.get_my_build
    _orig_get_my_moves = mt.get_my_moves

    def hooked(species_ja):
        b = _SIM_BUILDS.get(species_ja or "")
        return b if b else _orig_get_my_build(species_ja)

    def hooked_moves(species_ja):
        if _SIM_BUILDS:                       # シムで登録している間は登録チームの技を引かない (無ければ空 = 使用率の予測技)
            return list(_SIM_MOVES.get(species_ja or "", []))
        return _orig_get_my_moves(species_ja)

    mt.get_my_build = hooked
    mt.get_my_moves = hooked_moves


def register_team_text(text: str, resolver=None) -> dict:
    """Showdownチームテキストから自分側の型 (能力ポイント・性格・技) を登録する。戻り値: 登録した辞書。
    技は resolver (vision.normalize.NameResolver) があれば日本語名で登録する (登録技の解決は日本語名から)"""
    _install_build_hook()
    out = {}
    _SIM_BUILDS.clear()
    _SIM_MOVES.clear()
    aliases: list = []
    for block in (text or "").strip().split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        head, _, item = lines[0].partition("@")
        sid = re.sub(r"[^a-z0-9]", "", head.strip().lower())
        ja = species_ja_name(sid) or sid
        ev, nature = {}, {}
        moves: list = []
        for l in lines[1:]:
            if l.startswith("- "):
                mid = re.sub(r"[^a-z0-9]", "", l[2:].strip().lower())
                mj = resolver.ja_of("moves", mid) if resolver is not None else None
                if mj:
                    moves.append(mj)
                continue
            if l.startswith("EVs:"):
                for part in l.split(":", 1)[1].split("/"):
                    m = re.match(r"\s*(\d+)\s+(\w+)", part)
                    if m:
                        pts, key = int(m.group(1)), m.group(2)
                        k = _EV_KEYS.get(key, key.lower())
                        # 能力ポイント (0-32) 表記なら努力値へ換算
                        ev[k] = min(252, pts * 8) if pts <= 32 else min(252, pts)
            elif l.endswith("Nature"):
                nature = _nature_mult(l.split()[0].lower())
        build = {"ev": {s: ev.get(s, 0) for s in _STATS} if ev else {},
                 "nature": nature, "item_ja": None, "ability_ja": None}
        _SIM_BUILDS[ja] = build
        _SIM_MOVES[ja] = moves
        out[ja] = build
        mega_ja = _mega_alias_ja(sid, item)
        if mega_ja and mega_ja != ja:
            aliases.append((mega_ja, build, moves))
    # メガ後の形態の日本語名でも同じ型を引けるようにする (2026-10-09: フォルム違いの基本種からのメガ。
    # floetteeternal @ floettite → メガ後 floettemega の基本種は gimmick._base_of で floette に戻り、送った名前
    # 「フラエッテ(えいえんのはな)」に当たらず既定の型で参照されていた)。基本種の名前で引ける個体 (ミミロップ等) には足さない
    # (_mega_alias_ja)。本文の個体の名前の登録は上書きしない。戻り値には含めない
    for mega_ja, build, moves in aliases:
        if mega_ja not in _SIM_BUILDS:
            _SIM_BUILDS[mega_ja] = build
            _SIM_MOVES[mega_ja] = moves
    return out


def _mega_stone_form(sid: str, item: Optional[str]) -> Optional[str]:
    """本文の個体 (種族 id sid) の持ち物がその種のメガ石なら、メガ後の形態 id (石の表 gimmick.stone_table)。
    その種の石と言えるのは、メガ後の基本種 (gimmick._base_of) が sid と同じか sid の前方 (フォルム違い: floetteeternal と
    floette) のとき。石でない・別の種の石なら None (純粋。図鑑と石の表を引く)"""
    iid = re.sub(r"[^a-z0-9]", "", str(item or "").lower())
    if not iid or not sid:
        return None
    try:
        from advisor.gimmick import _base_of, stone_table
        form = stone_table().get(iid)
    except Exception:
        return None
    if not form:
        return None
    base = _base_of(form)
    return form if (base and sid.startswith(base)) else None


def _mega_alias_ja(sid: str, item: Optional[str]) -> Optional[str]:
    """本文の個体がメガ石を持ち、メガ後の登録参照 (engine.registration_names: 基本種の日本語名 → 個体の日本語名) が本文の名前に
    当たらないとき、メガ後の形態の日本語名 (別名の登録用)。それ以外は None。
    基本種の日本語名が本文の名前と同じ (lopunny → ミミロップ) なら基本種の名前で引けるので別名は作らない (従来の登録のまま)。
    作るのはフォルム違いの基本種 (floetteeternal: 本文の名前 フラエッテ(えいえんのはな)、基本種 floette の名前 フラエッテ) だけ"""
    form = _mega_stone_form(sid, item)
    if not form:
        return None
    from advisor.gimmick import _base_of
    if species_ja_name(_base_of(form)) == (species_ja_name(sid) or sid):
        return None
    ja = species_ja_name(form)
    return ja if ja and ja != form else None


def team_text_species(text: str) -> list:
    """チームテキストの各個体の種族 id (register_team_text と同じ読み方: 1 行目の「@」より前を英数字だけにする)"""
    out = []
    for block in (text or "").strip().split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if lines:
            out.append(re.sub(r"[^a-z0-9]", "", lines[0].split("@")[0].strip().lower()))
    return out


def team_matches_species(text: str, species) -> bool:
    """チームテキストが対戦の自分の個体 (poke-env の battle.team の種族 id) と合うか (純粋)。
    メガ・フォルム違い (gyaradosmega / aegislashblade) は本文の種族 id の前方一致で同じ個体とみなす"""
    sids = [s for s in team_text_species(text) if s]
    sp = [str(s or "") for s in (species or []) if s]
    if not sids or not sp:
        return False
    return all(any(b == t or b.startswith(t) for t in sids) for b in sp)


def team_text_for_battle(team_source, k: int, species=()) -> tuple:
    """k 番目 (0 始まり、対戦の生成順) の対戦で自分が送ったチームテキストと、その決め方を返す (純粋)。

    poke-env の Player は対戦 k が作られた直後に対戦 k+1 の challenge を送り、そのとき yield_team を呼ぶ
    (player.py `_send_challenges`: challenge(next_team) → `_battle_semaphore.acquire()` → 次の challenge。
    semaphore は `_create_battle` で対戦が作られたときに解放される)。そのため「直近に出したチーム (last_text)」は
    対戦 k の選出・行動の時点で既にチーム k+1 になっている (2026-10-09 実測、最後の対戦だけ一致)。
    相手側の id_for_battle(k) と同じく、出した順の k 番目を引く。

    team_source が text_for_battle(k) を持てばそれ (species と合わなければ texts から種族が合うものを k に近い順に探す)、
    持たなければ従来どおり last_text。戻り値: (text, how)。how は "index" / "species" / "unverified" / "last_text" / "none"
    """
    get = getattr(team_source, "text_for_battle", None)
    if get is None:
        text = getattr(team_source, "last_text", None)
        return (text, "last_text") if text else (None, "none")
    text = get(k)
    species = list(species or [])
    if not species or (text and team_matches_species(text, species)):
        return (text, "index") if text else (None, "none")
    texts = list(getattr(team_source, "texts", None) or [])
    for i in sorted(range(len(texts)), key=lambda i: (abs(i - k), i)):
        if team_matches_species(texts[i], species):
            return texts[i], "species"
    return (text, "unverified") if text else (None, "none")


def register_team_for_battle(team_source, k: int, species=(), resolver=None,
                             stats: Optional[dict] = None) -> Optional[str]:
    """対戦 k のチームでシムの型を登録する (同じ本文が登録済みなら何もしない)。登録に使った本文を返す。
    stats があれば決め方 (team_reg_<how>) を数え、登録済みの本文を stats["_registered"] に置く"""
    stats = stats if stats is not None else {}
    text, how = team_text_for_battle(team_source, k, species)
    if how not in ("index", "last_text"):
        stats[f"team_reg_{how}"] = stats.get(f"team_reg_{how}", 0) + 1
    if text and stats.get("_registered") != text:
        register_team_text(text, resolver)
        stats["_registered"] = text
    return text


# ------------------------------------------------------------------ 対応の確認 (2026-10-09 M2d、レビュー 6)
# 対戦ごとに (a) 送った自チームの本文の sha16 (型込み)、(b) 助言が参照した自分の場の個体の型 (配分・性格・持ち物・技。メガ後も) の
# sha16 と、(a) の該当個体の型との一致を記録する。条件間で (a) が一致し、各条件で (b) の不一致が 0 のときだけ採否を出す
# (tools/usefulness_verdict)
def team_text_sha16(text: Optional[str]) -> Optional[str]:
    """送った自チームの本文の sha16 (advisor.versions.sha256_text と同じ計算。本文が無ければ None)"""
    if not text:
        return None
    from advisor.versions import sha256_text
    return sha256_text(text)


def _ev_of_line(line: str) -> dict:
    ev = {}
    for part in line.split(":", 1)[1].split("/"):
        m = re.match(r"\s*(\d+)\s+(\w+)", part)
        if m:
            pts, key = int(m.group(1)), m.group(2)
            k = _EV_KEYS.get(key, key.lower())
            ev[k] = min(252, pts * 8) if pts <= 32 else min(252, pts)      # 能力ポイント (0-32) 表記は努力値へ
    return ev


def canonical_set(ev: Optional[dict], nature: Optional[dict], item: Optional[str], moves) -> dict:
    """型の比較用の正規形 (純粋): 努力値は 0 を落とす、持ち物と技は英数字の id、技は並べ替え"""
    def toid(x):
        return re.sub(r"[^a-z0-9]", "", str(x or "").lower()) or None
    return {"ev": {k: int(v) for k, v in sorted((ev or {}).items()) if int(v or 0) > 0},
            "nature": {k: float(v) for k, v in sorted((nature or {}).items())},
            "item": toid(item), "moves": sorted({toid(m) for m in (moves or []) if toid(m)})}


def set_sha16(canon: Optional[dict]) -> Optional[str]:
    if canon is None:
        return None
    import json
    from advisor.versions import sha256_text
    return sha256_text(json.dumps(canon, sort_keys=True, ensure_ascii=False))


def text_set_of(text: Optional[str], species_id: Optional[str]) -> Optional[dict]:
    """送った本文のうち、種族 id (メガ・フォルム違いは本文の種族 id の前方一致。前方一致しないメガ後の形態は、その石を持つ個体)
    の個体の型の正規形 (純粋)。無ければ None"""
    sid = re.sub(r"[^a-z0-9]", "", str(species_id or "").lower())
    if not text or not sid:
        return None
    best = None
    for block in (text or "").strip().split("\n\n"):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        head, _, item = lines[0].partition("@")
        bid = re.sub(r"[^a-z0-9]", "", head.lower())
        if not bid:
            continue
        if sid == bid or sid.startswith(bid):
            rank = len(bid)
        elif _mega_stone_form(bid, item.strip()) == sid:
            # メガ後の種族 id が本文の名前の前方一致にならない (floettemega と本文の floetteeternal @ floettite) ときは、
            # その石でメガシンカした個体とみなす (2026-10-09)。石で決まるので名前の前方一致より優先する
            rank = len(sid) + 1
        else:
            continue
        if best is not None and rank <= best[0]:
            continue
        ev, nature, moves = {}, {}, []
        for l in lines[1:]:
            if l.startswith("- "):
                moves.append(l[2:].strip())
            elif l.startswith("EVs:"):
                ev = _ev_of_line(l)
            elif l.endswith("Nature"):
                nature = _nature_mult(l.split()[0].lower())
        best = (rank, canonical_set(ev, nature, item.strip() or None, moves))
    return best[1] if best else None


def referenced_set(p: dict, resolver) -> Optional[dict]:
    """助言が自分の個体 p (状態辞書) について参照した型の正規形: build_mon_view (side=player) の努力値・性格・持ち物と、
    登録の技 (engine.registered_moves_of_mon。登録を引く名前の順は engine.registration_names)。個体を作れなければ None"""
    from advisor.engine import build_mon_view, registered_moves_of_mon
    v = build_mon_view(p, resolver, side="player")
    if v is None:
        return None
    try:
        moves = [mid for _ja, mid in registered_moves_of_mon(p, resolver)]
    except Exception:
        moves = []
    return canonical_set(v.ev, v.nature, v.item, moves)


def sets_match(ref: Optional[dict], exp: Optional[dict]) -> bool:
    """参照した型と送った型が一致するか (純粋)。持ち物は参照側が無い (消費・はたきおとされた) なら比べない"""
    if ref is None or exp is None:
        return False
    if ref["ev"] != exp["ev"] or ref["nature"] != exp["nature"] or ref["moves"] != exp["moves"]:
        return False
    return ref["item"] is None or ref["item"] == exp["item"]


def check_reference(corr: dict, p: Optional[dict], text: Optional[str], resolver) -> Optional[bool]:
    """1 決定ぶんの (b) の検査を corr (対戦ごとの記録) に積む。戻り値: 一致 True / 不一致 False / 検査できない None。
    助言の図鑑に無い種 (フォルム違い: indeedeef 等。build_mon_view が None) は型を参照していないので不一致に数えず、
    ref_unbuilt に数える (助言はその個体を評価できない。両条件で同じ)"""
    if not p or not p.get("species_id"):
        return None
    ref = referenced_set(p, resolver)
    if ref is None:
        corr["ref_unbuilt"] = corr.get("ref_unbuilt", 0) + 1
        corr.setdefault("unbuilt_species", [])
        if str(p.get("species_id")) not in corr["unbuilt_species"]:
            corr["unbuilt_species"].append(str(p.get("species_id")))
        return None
    exp = text_set_of(text, p.get("species_id"))
    ok = sets_match(ref, exp)
    corr["ref_checked"] = corr.get("ref_checked", 0) + 1
    sid = str(p.get("species_id"))
    corr.setdefault("ref_sets", {})[sid] = set_sha16(ref)
    corr.setdefault("sent_sets", {})[sid] = set_sha16(exp)
    if not ok:
        corr["ref_mismatch"] = corr.get("ref_mismatch", 0) + 1
        corr.setdefault("first_mismatch", {"species": sid, "referenced": ref, "sent": exp})
    return ok


def correspondence_record(corr: Optional[dict]) -> dict:
    """対戦記録 (battles.jsonl) に足す対応の確認の欄 (純粋)。own_team_sha16 = (a)、ref_check = (b) の件数と型の sha16"""
    c = corr or {}
    return {"own_team_sha16": c.get("own_team_sha16"),
            "ref_check": {"n": int(c.get("ref_checked") or 0), "mismatch": int(c.get("ref_mismatch") or 0),
                          "unbuilt": int(c.get("ref_unbuilt") or 0), "unbuilt_species": list(c.get("unbuilt_species") or []),
                          "referenced": dict(c.get("ref_sets") or {}), "sent": dict(c.get("sent_sets") or {}),
                          "first_mismatch": c.get("first_mismatch")}}


def _mon_entry(p, own: bool, resolver, active: bool, available=None) -> dict:
    sid = p.species or ""
    status = p.status
    st = _STATUS_MAP.get(getattr(status, "name", str(status)).upper()) \
        if status is not None else None
    if p.fainted:
        st = "fainted"
    item = p.item if p.item not in (None, "", "unknown_item") else None
    entry = {
        "species_id": sid,
        "species_ja": species_ja_name(sid) or sid,
        "hp_percent": float(p.current_hp_fraction or 0.0) * 100.0,
        "status": st,
        "boosts": dict(p.boosts or {}),
        "ability_id": p.ability or None,
        "item_id": item,
        "is_mega": "mega" in sid,
        "is_active": active,
        "moves": [],
        "revealed_moves": [],
    }
    if own:
        if getattr(p, "max_hp", None):
            entry["hp_max"] = p.max_hp
            entry["hp_current"] = p.current_hp
        entry["is_picked"] = True
        moves = list((p.moves or {}).values())
        if active and available is not None:
            moves = [m for m in moves if m.id in available] or moves
        for m in moves[:4]:
            entry["moves"].append({
                "move_id": m.id,
                "name_ja": (resolver.ja_of("moves", m.id) if resolver else None)
                or m.id,
                "pp": getattr(m, "current_pp", None),
                "max_pp": getattr(m, "max_pp", None)})
    else:
        for m in list((p.moves or {}).values())[:4]:
            ja = (resolver.ja_of("moves", m.id) if resolver else None) or m.id
            entry["revealed_moves"].append(ja)
    return entry


def _side_state(team_values, active, own: bool, side_conditions, resolver,
                available=None) -> dict:
    party, active_index = [], None
    for i, p in enumerate(team_values):
        is_active = p is active
        if is_active:
            active_index = i
        party.append(_mon_entry(p, own, resolver, is_active, available))
    names = _names(side_conditions)
    counts = {getattr(k, "name", str(k)).upper(): v
              for k, v in (side_conditions or {}).items()}
    return {
        "active_index": active_index,
        "tailwind": "TAILWIND" in names,
        "hazards": {"stealth_rock": "STEALTH_ROCK" in names,
                    "spikes": int(counts.get("SPIKES", 0) or 0),
                    "toxic_spikes": int(counts.get("TOXIC_SPIKES", 0) or 0),
                    "sticky_web": "STICKY_WEB" in names},
        "screens": {"reflect": "REFLECT" in names,
                    "light_screen": "LIGHT_SCREEN" in names,
                    "aurora_veil": "AURORA_VEIL" in names},
        "party": party,
        "remaining": sum(1 for p in team_values if not p.fainted),
    }


def battle_to_state(battle, resolver=None) -> Optional[dict]:
    """poke-env Battle -> 助言エンジンの状態辞書"""
    active = battle.active_pokemon
    opp_active = battle.opponent_active_pokemon
    if active is None or opp_active is None:
        return None
    available = {m.id for m in (battle.available_moves or [])}
    weather = None
    for w in (battle.weather or {}):
        weather = _WEATHER_MAP.get(getattr(w, "name", str(w)).upper())
    terrain, trick_room = None, False
    for f in (battle.fields or {}):
        name = getattr(f, "name", str(f)).upper()
        terrain = _TERRAIN_MAP.get(name, terrain)
        if name == "TRICK_ROOM":
            trick_room = True
    own_vals = list(battle.team.values())
    opp_vals = list(battle.opponent_team.values())
    opp_side = _side_state(opp_vals, opp_active, False, battle.opponent_side_conditions, resolver)
    # 相手の選出数 (シミュレータの対戦は BSS の 3 体選出)。opponent_team は場に出た個体だけなので remaining は「判明した生存数」で、
    # 未判明の残りが分からない。transition 方式の局面評価 (advisor.scoring_transition) が未判明の残りを数えるのに使う
    # (legacy の採点はこの欄を読まない。2026-10-09 M2b)
    from champions_agent.config import BSS_PICK_COUNT
    opp_side["team_size"] = BSS_PICK_COUNT
    return {
        "scene": "command",
        "turn": battle.turn,
        "field": {"weather": weather, "terrain": terrain,
                  "trick_room": trick_room},
        "mega_used": {"player": any("mega" in (p.species or "") for p in own_vals),
                      "opponent": any("mega" in (p.species or "") for p in opp_vals)},
        "player": _side_state(own_vals, active, True, battle.side_conditions,
                              resolver, available),
        "opponent": opp_side,
    }


def preview_to_state(battle, resolver=None) -> Optional[dict]:
    """poke-env Battle (選出画面、active が居ない時点) -> 選出助言の状態辞書 (advisor.selection.advise_selection の入力)"""
    own_vals = list(battle.team.values())
    opp_src = getattr(battle, "teampreview_opponent_team", None) or battle.opponent_team.values()
    opp_vals = list(opp_src)
    if len(own_vals) < 3 or not opp_vals:
        return None
    own = [_mon_entry(p, True, resolver, False) for p in own_vals]
    for e in own:
        e["is_picked"] = False
    opp = [_mon_entry(p, False, resolver, False) for p in opp_vals]
    return {"scene": "selection", "selection_picked": 0, "field": {},
            "player": {"party": own}, "opponent": {"party": opp}}


def rule_pick_order(battle, resolver=None, use_registered: bool = True) -> Optional[str]:
    """実戦の助言と同じ相性の規則 (advise_selection) の選出。評価できなければ None"""
    from tools.team_build.pilot import perm_to_team_order, rule_perm
    state = preview_to_state(battle, resolver)
    if state is None:
        return None
    perm = rule_perm(state, use_registered=use_registered, resolver=resolver)
    return perm_to_team_order(perm, len(battle.team)) if perm else None


def choose_from_advice(battle, advice: dict) -> Optional[dict]:
    """助言の best を poke-env の行動に写す。選べなければ None"""
    if not advice or not advice.get("ok"):
        return None
    cands = [advice.get("best")] + list(advice.get("actions") or [])
    available = {m.id: m for m in (battle.available_moves or [])}
    switchable = {p.species: p for p in (battle.available_switches or [])}
    for a in cands:
        if not a:
            continue
        if a.get("kind") == "move" and a.get("id") in available:
            return {"kind": "move", "move": available[a["id"]],
                    "mega": bool(battle.can_mega_evolve), "pokemon": None}
        if a.get("kind") == "switch" and a.get("id") in switchable:
            return {"kind": "switch", "move": None, "mega": False,
                    "pokemon": switchable[a["id"]]}
    return None


def pick_order_from_perm(perm, n: int) -> str:
    """選出モデルの perm (自分 6 体の index 3 つ) → Showdown の /team 文字列 (残りは元の順で後ろに)"""
    chosen = [int(i) for i in perm]
    rest = [i for i in range(n) if i not in chosen]
    return "/team " + "".join(str(i + 1) for i in chosen + rest)


def advisor_pick_order(battle, selection_model_path=None, planned: Optional[list] = None) -> Optional[str]:
    """実助言と同じ選出: 選出モデル (候補専用モデルか、既定モデルで分布内のとき)。使えなければ None。
    planned = 構築の選出計画 (この相手の系統に出す 3 体)。あればモデルの予測勝率に計画の事前 (BUILD_PLAN_PRIOR_MIX) を足し、
    モデルが使えないときは計画そのものを使う (tools.team_build.plan_prior)"""
    from champions_agent.agent import selection_dispatch as SD
    from champions_agent.agent import selection_model as sm
    from tools.team_build.plan_prior import apply_plan_prior, plan_indices
    my = [p.species for p in battle.team.values()]
    opp_src = getattr(battle, "teampreview_opponent_team", None) or battle.opponent_team.values()
    opp = [p.species for p in opp_src]
    if len(my) < 3 or not opp:
        return None
    plan_idx = plan_indices(planned, my) if planned else None
    usable = selection_model_path is not None or sm.is_in_distribution(my)
    scored = []
    if usable:
        path = Path(selection_model_path) if selection_model_path else SD.deployed_model_path()
        scored = SD.score_all(my, opp, path)
    if not scored:
        return pick_order_from_perm(plan_idx, len(my)) if plan_idx else None
    scored = apply_plan_prior(scored, plan_idx)
    return pick_order_from_perm(scored[0][0], len(my))


def apply_action_noise(advice: dict, action_noise: float, rng) -> tuple:
    """助言に行動ノイズ (STRESS 用: 確率 p で 2 位の手) を適用し、(best を差し替えた助言, followed) を返す (純粋)。
    遵守モデル (user_policy) は 2026-10-05 に廃止: 操縦はアドバイザーが行う前提で測り、実戦の遵守率は記録だけ残す"""
    actions = list((advice or {}).get("actions") or [])
    if not advice or not advice.get("ok") or len(actions) < 2:
        return advice, True
    if action_noise > 0 and rng.random() < action_noise:
        adv = dict(advice)
        adv["best"] = actions[1]
        adv["actions"] = [actions[1]] + [a for a in actions if a is not actions[1]]
        return adv, False
    return advice, True


def _state_summary(battle) -> dict:
    def side(mon):
        if mon is None:
            return None
        return {"species": getattr(mon, "species", None),
                "hp": round(float(getattr(mon, "current_hp_fraction", 0.0) or 0.0), 3),
                "status": str(getattr(mon, "status", None) or "")}
    return {"turn": getattr(battle, "turn", None),
            "me": side(getattr(battle, "active_pokemon", None)),
            "opp": side(getattr(battle, "opponent_active_pokemon", None)),
            "can_mega": bool(getattr(battle, "can_mega_evolve", False))}


def _count_scoring(stats: dict, advice: Optional[dict]) -> None:
    """transition 方式の決定数と退避 (scoring_fallback) の件数を stats に数える (純粋な集計。legacy では何も足さない)。
    退避の理由は「unsupported:効果:技」「opp_unsupported:効果:技」(2026-10-09 M2c) までと、それ以外は「:」の前で分類する"""
    if not advice or advice.get("scoring_mode") != "transition":
        return
    stats["scoring_transition"] = stats.get("scoring_transition", 0) + 1
    fb = advice.get("scoring_fallback")
    if fb:
        parts = str(fb).split(":")
        key = ":".join(parts[:3]) if parts[0] in ("unsupported", "opp_unsupported") else parts[0]
        stats["scoring_fallback"] = stats.get("scoring_fallback", 0) + 1
        stats[f"scoring_fallback:{key}"] = stats.get(f"scoring_fallback:{key}", 0) + 1
    # 2026-10-09 M2d: 未評価の応手 (決定数と技)、本採点と全処理時間 (stats["_tr_main_ms"] / ["_tr_total_ms"] に積む。json には
    # 要約だけ書く: transition_timing)、感度の状態 (sens:<状態>) と省略の理由 (sens_omitted:<仮説>:<理由>)
    ts = advice.get("transition_stats") or {}
    une = ts.get("unevaluated")
    if une:
        stats["scoring_unevaluated"] = stats.get("scoring_unevaluated", 0) + 1
        for m in une.get("moves") or []:
            stats[f"scoring_unevaluated:{m}"] = stats.get(f"scoring_unevaluated:{m}", 0) + 1
    if fb:
        return
    if (ts.get("main") or {}).get("elapsed_ms") is not None:
        stats.setdefault("_tr_main_ms", []).append(float(ts["main"]["elapsed_ms"]))
    if ts.get("elapsed_total_ms") is not None:
        stats.setdefault("_tr_total_ms", []).append(float(ts["elapsed_total_ms"]))
    sens = ts.get("sens")
    if sens:
        k = f"sens:{sens.get('status') or 'unknown'}"
        stats[k] = stats.get(k, 0) + 1
        for name, why in (sens.get("omitted") or {}).items():
            k = f"sens_omitted:{name}:{why}"
            stats[k] = stats.get(k, 0) + 1


def transition_timing(stats: dict) -> dict:
    """stats に積んだ transition の時間 (_tr_main_ms / _tr_total_ms) の要約 (純粋): 本採点と全処理時間 (感度込み) の p50 / p95"""
    def pct(vals, q):
        if not vals:
            return None
        s = sorted(vals)
        return round(s[min(len(s) - 1, int(len(s) * q))], 1)
    main, total = list(stats.get("_tr_main_ms") or []), list(stats.get("_tr_total_ms") or [])
    return {"main": {"n": len(main), "p50_ms": pct(main, 0.5), "p95_ms": pct(main, 0.95)},
            "total": {"n": len(total), "p50_ms": pct(total, 0.5), "p95_ms": pct(total, 0.95)}}


def make_advisor_player(team_source=None, stats: Optional[dict] = None,
                        latencies: Optional[list] = None,
                        pick_policy: str = "advisor", selection_model_path=None,
                        pick_noise: float = 0.0, action_noise: float = 0.0,
                        rng=None, recorder=None,
                        opp_source=None, selection_plan: Optional[dict] = None, family_of: Optional[dict] = None,
                        rl_blend_weight: Optional[float] = None,
                        **player_kwargs):
    """助言エンジンで戦う poke-env Player を作る (操縦はアドバイザー: 選出も行動も実戦の助言と同じ経路)。

    pick_policy: "advisor" = 実助言と同じ選出 (選出モデル → 使えなければ相性の規則 advise_selection → 最後に簡易相性順) /
                 "rule" = 相性の規則 (advise_selection) だけ /
                 "teampreview" = 従来の簡易相性順 (search_expert.teampreview_order。実戦の経路には無い)
    selection_model_path: 候補専用の選出モデル (None なら既定モデルを分布内のときだけ使う)
    pick_noise / action_noise: STRESS 用のノイズ (確率で乱択 / 2 位の手)
    recorder: tools.team_build.battle_log.BattleRecorder (対戦記録)。opp_source.last_id を相手 id に使う
    selection_plan / family_of: 構築の選出計画 (plan.json の dict) と 相手 team_id → 系統 id。pick_policy advisor のとき
        計画を選出モデルの初期値にする (plan_prior)。相手の系統は opp_source.last_id から引く
    rl_blend_weight: 助言の RL 加点の重み (evaluate の引数で渡す。None = 既定値。2026-10-07: 環境変数で渡さない)

    team_source: 自分側の型登録に使う Teambuilder。text_for_battle(k) (k 番目の対戦で出したチームテキスト) と
        texts (出した順の本文) を持つものを推奨 (2026-10-09)。無ければ従来どおり last_text (対戦 k の時点で
        次の対戦のチームに進んでいるので、チームが対戦ごとに変わる teambuilder では 1 戦ずれる)。
    stats / latencies: 診断用の集計先 (省略可)。
    """
    from poke_env.player import Player
    from advisor.engine import evaluate
    from vision.normalize import NameResolver
    from champions_agent.env.search_expert import teampreview_order
    resolver = NameResolver()
    stats = stats if stats is not None else {}
    _install_build_hook()
    rng = rng or random.Random(0)
    # 対戦ごとの対応の確認の記録 (battle_tag → {own_team_sha16, ref_checked, ref_mismatch, ...}。2026-10-09 M2d)
    corr_by_tag: dict = {}

    class _AdvisorPlayer(Player):
        def choose_move(self, battle):
            t0 = time.perf_counter()
            advice, followed, d = None, True, None
            try:
                text = self._register_team(battle)
                state = battle_to_state(battle, resolver)
                if state:
                    # 採点方式は config.ADVICE_SCORING_MODE (環境変数 ADVICE_SCORING_MODE で上書き。2026-10-09 M2b)
                    advice = evaluate(state, resolver, rl_blend_weight=rl_blend_weight)
                    _count_scoring(stats, advice)
                    try:
                        me = state["player"]
                        p_act = me["party"][me["active_index"]] if me.get("active_index") is not None else None
                        ok = check_reference(self._corr(battle), p_act, text, resolver)
                        if ok is None and p_act:
                            stats["ref_unbuilt"] = stats.get("ref_unbuilt", 0) + 1
                        if ok is not None:
                            stats["ref_checked"] = stats.get("ref_checked", 0) + 1
                            if not ok:
                                stats["ref_mismatch"] = stats.get("ref_mismatch", 0) + 1
                    except Exception as e:
                        stats["ref_error"] = stats.get("ref_error", 0) + 1
                        stats["last_ref_error"] = repr(e)
                    adv2, followed = apply_action_noise(advice, action_noise, rng)
                    d = choose_from_advice(battle, adv2)
                    if not followed:
                        stats["deviated"] = stats.get("deviated", 0) + 1
            except Exception as e:
                stats["error"] = stats.get("error", 0) + 1
                stats["last_error"] = repr(e)
                d = None
            if recorder is not None:
                try:
                    executed = None if d is None else {
                        "kind": d["kind"],
                        "id": (d["move"].id if d["kind"] == "move" else d["pokemon"].species)}
                    recorder.on_decision(battle, _state_summary(battle), advice, executed, followed)
                except Exception:
                    pass
            if latencies is not None:
                latencies.append((time.perf_counter() - t0) * 1000.0)
            if d is None:
                stats["fallback"] = stats.get("fallback", 0) + 1
                return self.choose_random_move(battle)
            stats["decide"] = stats.get("decide", 0) + 1
            stats[d["kind"]] = stats.get(d["kind"], 0) + 1
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def _register_team(self, battle):
            # 対戦の生成順 (poke-env の battles は生成順の dict) で k を決め、その対戦で送ったチームを登録する
            # (last_text は次の対戦のチームに先に進んでいる。team_text_for_battle の説明)
            tags = list(self.battles)
            tag = getattr(battle, "battle_tag", None)
            k = tags.index(tag) if tag in tags else max(0, len(tags) - 1)
            species = [getattr(p, "species", None) for p in (getattr(battle, "team", None) or {}).values()]
            text = register_team_for_battle(team_source, k, species, resolver, stats)
            corr = self._corr(battle)
            corr["own_team_sha16"] = team_text_sha16(text)
            corr["battle_index"] = k
            return text

        def _corr(self, battle) -> dict:
            tag = getattr(battle, "battle_tag", None) or id(battle)
            return corr_by_tag.setdefault(tag, {})

        def teampreview(self, battle):
            self._register_team(battle)
            order = self._pick(battle)
            if recorder is not None:
                try:
                    mons = list(battle.team.values())
                    idx = [int(c) - 1 for c in order.replace("/team", "").strip()]
                    recorder.on_pick(battle, [mons[i].species for i in idx if 0 <= i < len(mons)])
                except Exception:
                    pass
            return order

        def _pick(self, battle):
            if pick_noise > 0 and rng.random() < pick_noise:
                stats["pick_noise"] = stats.get("pick_noise", 0) + 1
                return self.random_teampreview(battle)
            if pick_policy == "advisor":
                planned = None
                if selection_plan:
                    from tools.team_build.plan_prior import plan_for_family
                    fam = (family_of or {}).get(getattr(opp_source, "last_id", None))
                    planned = plan_for_family(selection_plan, fam) or None
                    if planned:
                        stats["pick_plan_avail"] = stats.get("pick_plan_avail", 0) + 1
                try:
                    order = advisor_pick_order(battle, selection_model_path, planned)
                except Exception as e:
                    stats["pick_error"] = stats.get("pick_error", 0) + 1
                    stats["last_pick_error"] = repr(e)
                    order = None
                if order:
                    stats["pick_model"] = stats.get("pick_model", 0) + 1
                    return order
                stats["pick_fallback"] = stats.get("pick_fallback", 0) + 1
            if pick_policy in ("advisor", "rule"):
                # モデルが無い / 分布外: 実戦の助言と同じ相性の規則 (advise_selection) で選ぶ (従来の簡易相性順ではない)
                try:
                    order = rule_pick_order(battle, resolver, use_registered=True)
                except Exception as e:
                    stats["pick_rule_error"] = stats.get("pick_rule_error", 0) + 1
                    stats["last_pick_rule_error"] = repr(e)
                    order = None
                if order:
                    stats["pick_rule"] = stats.get("pick_rule", 0) + 1
                    return order
            try:
                stats["pick_matchup"] = stats.get("pick_matchup", 0) + 1
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

        def _battle_finished_callback(self, battle):
            super()._battle_finished_callback(battle)
            if recorder is not None:
                try:
                    # 相手 id は「k 番目の対戦 = 相手列の k 番目」で引く (終了コールバックと次の
                    # yield_team の順序は保証されないため last_id は使わない)
                    k = stats.get("_finished", 0)
                    stats["_finished"] = k + 1
                    if hasattr(opp_source, "id_for_battle"):
                        opp_id = opp_source.id_for_battle(k)
                    else:
                        opp_id = getattr(opp_source, "last_id", None)
                    tag = getattr(battle, "battle_tag", None) or id(battle)
                    recorder.on_end(battle, opponent_team_id=opp_id, extra=correspondence_record(corr_by_tag.pop(tag, None)))
                except Exception as e:
                    stats["record_error"] = stats.get("record_error", 0) + 1
                    stats["last_record_error"] = repr(e)

    return _AdvisorPlayer(**player_kwargs)
