"""共通の状態遷移 (1 ターンの解決)。docs/ADVICE_ACCURACY_PLAN_1009.md §2.3 / §3.0 M1b。

探索 (advisor.search) と直接の採点 (advisor.engine) が同じ遷移を参照するための純粋な型と効果解決。
advisor.damage はダメージ計算、ここは行動順・HP 変化・消費・ひんし・繰り出し要求を担当する。
入力の状態は書き換えない (dataclasses.replace で新しい状態を返す)。分岐 (ダメージ乱数) は引数 roll ("min"/"avg"/"max") で再現する。

1 ターンの解決順 (resolve_turn):
  1. 交代 (自分 → 相手。着地時のステルスロック) 2. メガシンカ (相手 → 自分) 3. 技を 優先度 → 素早さ (トリックルームは逆) の順に解決。
     同速は自分が先 (従来どおり。同速の分岐は段 B-1)
  4. 各技の後にひんしを判定する。行動前にひんしになった個体は行動しない (与ダメージ 0、自滅も HP の消費も起きない)
  5. ターンの終わりに、ひんしの陣営は控え (静的評価が最良の 1 体に固定) を繰り出す (requires_switch)。繰り出した個体は
     このターンに行動しない (倒れた個体の予定行動を引き継がない)

対応範囲 (2026-10-09 M1b):

| 効果 | 扱い | Showdown の根拠 |
|---|---|---|
| 交代・ステルスロック・メガシンカ | 対応 (従来どおり) | |
| まもる系 | 対応 (従来どおり: 後から来る攻撃・状態異常技を防ぐ) | |
| 回復技・積み技・ステルスロック設置・状態異常技 | 対応 (従来どおりの簡易モデル。あくびは即ねむり) | |
| ふいうち系・サイコフィールドの先制技不発・グラススライダー | 対応 (従来どおり) | |
| 行動前のひんし | 対応: 行動しない。ひんし後の繰り出しは予定行動を引き継がない | sim/battle.ts runAction (case 'move': fainted なら行動しない) |
| 対象がひんし (先に倒れた) | 対応: 相手を対象にする技は失敗 (自滅も HP の消費も起きない)。自分対象の技・設置は通る | sim/battle-actions.ts useMoveInner (target が無ければ faint の前に -fail) |
| 自滅 (selfdestruct: "always": だいばくはつ・じばく・ミストバースト) | 対応: 技を出したら使用者はひんし (まもる・無効タイプ・外れでも)。命中判定より先 | useMoveInner (TryMove の後に this.battle.faint(pokemon)) |
| しめりけ (Damp) | 対応: 上の自滅技と てっていこうせん が不発 (自滅・HP の消費なし)。かたやぶり系は無視 | data/abilities.ts damp (onAnyTryMove) |
| HP の消費 (mindBlownRecoil: てっていこうせん・ビックリヘッド) | 対応: 技を出したら最大 HP の 1/2 (四捨五入) を失う。まもる・無効でも失う。マジックガードは防ぐ | applyRecoilDamage / onMoveFail |
| HP の消費 (chloroblastRecoil: クロロブラスト) | 対応: 与ダメージがあったときだけ 1/2。いしあたま・マジックガードは防ぐ | applyRecoilDamage (効果は 'recoil') |
| 反動 (recoil: すてみタックル等) | 対応: 実際に与えた HP × 割合 (四捨五入、最低 1)。いしあたま・マジックガードは防ぐ | applyRecoilDamage |
| 自滅 (selfdestruct: "ifHit": おきみやげ・いのちがけ・いやしのねがい・みかづきのまい) | 未対応 (unsupported "selfko_ifhit") | |
| 吸収技・いのちのたま・ゴツゴツメット/さめはだ | 未対応 (unsupported "drain" / "self_damage_item" / "contact_damage") | |
| きあいのタスキ・がんじょう・ばけのかわ・アイスフェイス | 未対応 (unsupported "survive_one_hit" / "free_hit"。段 B-4) | |
| 連続技 | 従来どおり期待回数の合計を 1 回で与える (unsupported "multihit_expected"。回数の分岐は段 B-1) | |
| 外れたときの反動 (とびひざげり等) | 未対応 (命中は分岐しない。unsupported "crash") | |
| 表に無い変化技 | 効果なし (unsupported "effect_unmodeled"。段 C) | |
| 命中・急所・追加効果・自分の能力変化・行動不能 (まひ・ねむり・ひるみ)・ターン終了時の効果・同速の分岐 | 未対応 (結果に印は付けない) | |

ひんしの順序: 自滅技は Showdown と同じく使用者のひんしがダメージより先 (faint は TryMove の直後)。反動・HP の消費は
ダメージの後。どちらも技の後にまとめてひんしを判定し、繰り出しはターンの終わり。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Optional

from advisor.damage import FieldView, MonView, _is_grounded, calc_damage
from advisor.dex import get_dex

TRANSITION_EFFECTS_PATH = Path(__file__).resolve().parent / "data" / "transition_effects.json"

# --- 補助技の効果モデル (探索で「効果ゼロの技」にしないため) ---
PROTECT_MOVES = {"protect", "detect", "banefulbunker", "spikyshield",
                 "burningbulwark", "silktrap"}
HEAL_MOVES = {"recover": 0.5, "roost": 0.5, "slackoff": 0.5,
              "softboiled": 0.5, "milkdrink": 0.5, "shoreup": 0.5,
              "moonlight": 0.5, "morningsun": 0.5, "synthesis": 0.5,
              "strengthsap": 0.4, "junglehealing": 0.25, "lifedew": 0.25}
SETUP_MOVES = {
    "swordsdance": {"atk": 2}, "nastyplot": {"spa": 2},
    "dragondance": {"atk": 1, "spe": 1}, "calmmind": {"spa": 1, "spd": 1},
    "bulkup": {"atk": 1, "def": 1}, "irondefense": {"def": 2},
    "quiverdance": {"spa": 1, "spd": 1, "spe": 1},
    "shellsmash": {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1},
    "agility": {"spe": 2}, "howl": {"atk": 1}, "curse": {"atk": 1, "def": 1},
    "victorydance": {"atk": 1, "def": 1, "spe": 1},
}
STATUS_MOVES = {"willowisp": "burn", "thunderwave": "paralysis",
                "toxic": "toxic", "spore": "sleep", "sleeppowder": "sleep",
                "hypnosis": "sleep", "darkvoid": "sleep", "glare": "paralysis",
                "nuzzle": "paralysis", "yawn": "sleep"}
HAZARD_MOVES = {"stealthrock"}
# ふいうち系: 相手が「攻撃技を選んでいて、まだ動いていない」場合のみ成功
SUCKER_MOVES = {"suckerpunch", "thunderclap"}

_EPS = 1e-9


@lru_cache(maxsize=1)
def transition_effects() -> dict:
    """遷移の仕様の値 (advisor/data/transition_effects.json)"""
    try:
        return json.loads(TRANSITION_EFFECTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class SimSide:
    """探索用の軽量な陣営状態"""
    active: MonView
    active_hp: float                  # 0..1
    bench: list = field(default_factory=list)   # [(MonView, hp_frac)]
    stealth_rock: bool = False
    # 1 試合 1 回の資源 (メガシンカ): 場のポケモンがこの先メガシンカする姿の確率 {mega_sid: p} (advisor.gimmick)。
    # 権利を消費済みなら mega_used。交代すると新しい場のポケモンの分は不明 (空) にする
    mega_forms: dict = field(default_factory=dict)
    mega_used: bool = False

    def alive_count(self) -> int:
        return (1 if self.active_hp > 0 else 0) + \
            sum(1 for _, hp in self.bench if hp > 0)


@dataclass
class Action:
    kind: str                 # "move" | "switch"
    move_id: Optional[str] = None
    bench_index: Optional[int] = None
    label: str = ""
    prob: float = 1.0         # 相手側: 行動分布の重み
    mega: bool = False        # 技と同時にメガシンカする (相手側: 事前分布で分岐)


@dataclass
class MoveResult:
    """技 1 回の解決結果 (resolve_move)。user / target は解決後の陣営 (ひんしの繰り出しはまだ)"""
    user: SimSide
    target: SimSide
    executed: bool            # 技が出たか (False: 対象なし・しめりけ等で失敗)
    dealt: float              # 対象に実際に与えた HP (対象の最大 HP 比、0..残り HP)
    events: list              # [(who, kind, detail)] who は "user" / "target"
    unsupported: list         # [(who, effect, source)] 遷移で扱わなかった効果
    faint_order: list         # このとき HP が 0 になった順 ("user" / "target")


@dataclass
class TurnResult:
    """1 ターンの解決結果。me / opp はターン終了時の状態 (ひんしの繰り出しを済ませた後)"""
    me: SimSide
    opp: SimSide
    events: list = field(default_factory=list)        # [(who, kind, detail)] who は "me" / "opp"、起きた順
    acted: dict = field(default_factory=lambda: {"me": False, "opp": False})        # 技の順番が来て行動したか
    fainted: dict = field(default_factory=lambda: {"me": False, "opp": False})      # このターンに場の個体がひんし
    requires_switch: dict = field(default_factory=lambda: {"me": False, "opp": False})  # ひんし後の繰り出し要求
    unsupported: list = field(default_factory=list)   # [(who, effect, source)]


def is_attack_action(act: Action) -> bool:
    """その行動が攻撃技か (ふいうち系の成功判定用)"""
    if act.kind != "move" or not act.move_id:
        return False
    if act.move_id in PROTECT_MOVES or act.move_id in HEAL_MOVES or \
            act.move_id in SETUP_MOVES or act.move_id in STATUS_MOVES or \
            act.move_id in HAZARD_MOVES:
        return False
    mv = get_dex().move(act.move_id)
    return bool(mv and mv.get("category") != "Status" and mv.get("power"))


def hazard_frac(mon: MonView, side: SimSide) -> float:
    if not side.stealth_rock:
        return 0.0
    mult = get_dex().effectiveness("Rock", mon.types)
    return 0.125 * mult


def priority(move_id: Optional[str], view: Optional[MonView] = None,
             fieldv: Optional[FieldView] = None) -> int:
    """技の優先度 (特性補正込み: いたずらごころ=変化技+1 等。フィールド込み: グラススライダーはグラスフィールドで
    接地した使用者なら +1。表は advisor/data/field_effects.json の priority)"""
    from advisor.dex import field_effects
    mv = get_dex().move(move_id) if move_id else None
    if not mv:
        return 0
    pri = mv.get("priority", 0)
    ab = (view.ability or "") if view is not None else ""
    if ab == "prankster" and (mv.get("category") or "") == "Status":
        pri += 1
    elif ab == "galewings" and (mv.get("type") or "") == "Flying" \
            and view is not None and view.hp_frac >= 0.999:
        pri += 1
    spec = (field_effects().get("priority") or {}).get(move_id or "")
    if spec and fieldv is not None:
        cur = fieldv.terrain if spec.get("kind") == "terrain" else fieldv.weather
        if cur and (spec.get("cond") == "any" or spec.get("cond") == cur):
            if spec.get("grounded") != "user" or view is None or _is_grounded(view):
                pri += int(spec.get("delta", 0))
    return pri


def speed(view: MonView, fieldv: Optional[FieldView] = None) -> float:
    """実効素早さ (すいすい/ようりょくそ等の特性込み)"""
    from advisor.damage import effective_speed
    return effective_speed(view, fieldv)


def dmg_frac(attacker: MonView, defender: MonView, def_hp: float,
             move_id: str, fieldv: Optional[FieldView], roll: str) -> float:
    """与ダメージ (防御側最大HP比 0..1)"""
    try:
        d = calc_damage(replace(attacker), replace(defender, hp_frac=def_hp),
                        move_id, fieldv)
    except Exception:
        return 0.0
    return max(0.0, d[roll] / 100.0)


def best_bench(side: SimSide, opp: SimSide) -> Optional[int]:
    """ひんし時の後続 (静的評価最大の1体に固定)"""
    best, best_v = None, -9.9
    for i, (view, hp) in enumerate(side.bench):
        if hp <= 0:
            continue
        v = hp - hazard_frac(view, side)
        if v > best_v:
            best, best_v = i, v
    return best


def apply_switch(side: SimSide, idx: int) -> SimSide:
    view, hp = side.bench[idx]
    hp = max(0.0, hp - hazard_frac(view, side))
    new_bench = list(side.bench)
    new_bench[idx] = (side.active, side.active_hp)
    return replace(side, active=view, active_hp=hp, bench=new_bench, mega_forms={})


def mega_evolve(side: SimSide) -> SimSide:
    """場のポケモンを最も確からしいメガ後の姿にし、権利を消費する (HP 割合・ランク・状態異常は保つ)"""
    from advisor.gimmick import mega_view
    if not side.mega_forms or side.mega_used:
        return side
    form = max(side.mega_forms, key=lambda k: (side.mega_forms[k], k))
    return replace(side, active=mega_view(side.active, form), mega_forms={}, mega_used=True)


# ------------------------------------------------------------------ 自分の HP を変える効果 (M1b)
def _round_half_up(x: float) -> int:
    """Showdown の Math.round (0.5 は切り上げ)"""
    return int(math.floor(x + 0.5))


@lru_cache(maxsize=512)
def _ability_kinds(ability: Optional[str]) -> tuple:
    """特性の効果表の式の種類と misc の効果名 (frozenset の組)"""
    from advisor import effects as E
    fs = E.ability_formulas(ability)
    kinds = frozenset(f.get("kind") for f in fs)
    misc = frozenset(f.get("effect") for f in fs if f.get("kind") == "misc")
    return kinds, misc


@lru_cache(maxsize=1024)
def self_cost_kind(move_id: Optional[str]) -> Optional[str]:
    """技を出した使用者自身の HP を変える効果の種類 (遷移で解決するもの): "selfko" (自滅) / "half_hp" (てっていこうせん系、
    出したら必ず) / "half_hp_on_damage" (クロロブラスト、与ダメージがあれば) / "recoil" (反動)。無ければ None"""
    from advisor import effects as E
    e = E.move_entry(move_id)
    if not e:
        return None
    if e.get("selfdestruct") == "always":
        return "selfko"
    if e.get("mindblown"):
        return "half_hp"
    if move_id in (transition_effects().get("hp_cost_on_damage_moves") or ()):
        return "half_hp_on_damage"
    if e.get("recoil"):
        return "recoil"
    return None


def _half_hp_cost_frac(view: MonView) -> float:
    """最大 HP の 1/2 (Math.round(maxhp / 2)) を最大 HP 比で"""
    hc = transition_effects().get("half_hp_cost") or {"num": 1, "den": 2}
    mhp = max(1, int(view.max_hp()))
    return _round_half_up(mhp * hc["num"] / hc["den"]) / mhp


def _recoil_frac(user: MonView, target: MonView, dealt: float, ratio: float) -> float:
    """反動: 実際に与えた HP × 割合 (四捨五入、最低 recoil_min_hp) を使用者の最大 HP 比で"""
    t_mhp = max(1, int(target.max_hp()))
    u_mhp = max(1, int(user.max_hp()))
    dealt_hp = dealt * t_mhp
    lo = int(transition_effects().get("recoil_min_hp", 1))
    return max(lo, _round_half_up(dealt_hp * ratio)) / u_mhp


def _damp_blocks(move_id: str, user: SimSide, target: SimSide) -> bool:
    """しめりけ: 場のどちらかが持っていれば自滅技・てっていこうせんは不発 (onAnyTryMove)。使用者がかたやぶり系なら相手の
    しめりけは無視する (breakable)"""
    if move_id not in (transition_effects().get("damp_blocked_moves") or ()):
        return False
    _uk, umisc = _ability_kinds(user.active.ability)
    if "block_explosion" in umisc:
        return True
    if target.active_hp > 0:
        _tk, tmisc = _ability_kinds(target.active.ability)
        if "block_explosion" in tmisc and "ignore_target_ability" not in umisc:
            return True
    return False


def resolve_move(user: SimSide, target: SimSide, move_id: str, fieldv: Optional[FieldView], roll: str = "avg", *,
                 target_protected: bool = False, damage: Optional[float] = None) -> MoveResult:
    """技 1 回の効果を解決する (純粋)。行動順・ふいうち・サイコフィールドの判定は呼び出し側 (resolve_turn) が済ませる。

    damage: 与ダメージ (対象の最大 HP 比) を呼び出し側が計算済みならその値 (直接の採点が文脈 ctx 込みで計算した値を渡す)。
        None なら advisor.damage で roll の値を計算する。
    使用者がこの時点でひんしなら何もしない (呼び出し側が行動前のひんしを除いている前提だが、念のため)。
    """
    from advisor import effects as E
    events: list = []
    unsupported: list = []
    faint_order: list = []
    te = transition_effects()
    if user.active_hp <= 0:
        return MoveResult(user, target, False, 0.0, [("user", "skip", "fainted")], [], [])

    # --- 自分を対象にする技・設置 (相手がひんしでも出る) ---
    if move_id in PROTECT_MOVES:
        events.append(("user", "protect", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in HEAL_MOVES:
        user = replace(user, active_hp=min(1.0, user.active_hp + HEAL_MOVES[move_id]))
        events.append(("user", "heal", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in SETUP_MOVES:
        boosts = dict(user.active.boosts or {})
        for k, d in SETUP_MOVES[move_id].items():
            boosts[k] = max(-6, min(6, (boosts.get(k) or 0) + d))
        user = replace(user, active=replace(user.active, boosts=boosts))
        events.append(("user", "boost", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in HAZARD_MOVES:
        target = replace(target, stealth_rock=True)
        events.append(("target", "hazard", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)

    # --- 相手を対象にする技: 対象がひんしなら失敗 (自滅・HP の消費の前。useMoveInner の notarget) ---
    if target.active_hp <= 0:
        events.append(("user", "fail", "no_target"))
        return MoveResult(user, target, False, 0.0, events, unsupported, faint_order)
    if _damp_blocks(move_id, user, target):
        events.append(("user", "fail", "damp"))
        return MoveResult(user, target, False, 0.0, events, unsupported, faint_order)

    if move_id in STATUS_MOVES:
        if target.active.status is None and not target_protected:
            target = replace(target, active=replace(target.active, status=STATUS_MOVES[move_id]))
            events.append(("target", "status", STATUS_MOVES[move_id]))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)

    entry = E.move_entry(move_id)
    kind = self_cost_kind(move_id)
    sd = entry.get("selfdestruct")
    if sd and sd != "always":
        unsupported.append(("user", "selfko_ifhit", move_id))
    mv = get_dex().move(move_id) or {}
    if (mv.get("category") or entry.get("category")) == "Status" and not sd:
        unsupported.append(("user", "effect_unmodeled", move_id))

    user_before = user
    # 自滅 (selfdestruct: "always"): 技を出した時点 (命中判定・まもる・タイプ無効より前) で使用者はひんし
    if kind == "selfko":
        user = replace(user, active_hp=0.0)
        events.append(("user", "selfko", move_id))
        faint_order.append("user")

    # --- ダメージ ---
    dealt = 0.0
    if target_protected:
        events.append(("target", "protected", move_id))
    else:
        if damage is None:
            dmg = dmg_frac(replace(user_before.active, hp_frac=user_before.active_hp), target.active,
                           target.active_hp, move_id, fieldv, roll)
        else:
            dmg = max(0.0, float(damage))
        dealt = min(dmg, target.active_hp)
        if dealt > 0:
            target = replace(target, active_hp=max(0.0, target.active_hp - dealt))
            events.append(("target", "damage", round(dealt, 6)))
            if target.active_hp <= _EPS:
                target = replace(target, active_hp=0.0)
                faint_order.append("target")

    # --- 自分の HP の消費・反動 (ダメージの後) ---
    ukinds, umisc = _ability_kinds(user_before.active.ability)
    loss = 0.0
    if kind == "half_hp":
        # てっていこうせん系: 当たっても (applyRecoilDamage)、まもる・無効・外れでも (onMoveFail) 失う。マジックガードは防ぐ
        if "no_indirect_damage" in umisc:
            events.append(("user", "hp_cost_blocked", user_before.active.ability))
        else:
            loss = _half_hp_cost_frac(user_before.active)
            events.append(("user", "hp_cost", round(loss, 6)))
    elif kind == "half_hp_on_damage":
        if dealt > 0:
            if "recoil_none" in ukinds:
                events.append(("user", "recoil_blocked", user_before.active.ability))
            else:
                loss = _half_hp_cost_frac(user_before.active)
                events.append(("user", "hp_cost", round(loss, 6)))
    elif kind == "recoil":
        if dealt > 0:
            if "recoil_none" in ukinds:
                events.append(("user", "recoil_blocked", user_before.active.ability))
            else:
                loss = _recoil_frac(user_before.active, target.active, dealt, float(entry.get("recoil") or 0.0))
                events.append(("user", "recoil", round(loss, 6)))
    if loss > 0 and user.active_hp > 0:
        user = replace(user, active_hp=max(0.0, user.active_hp - loss))
        if user.active_hp <= _EPS:
            user = replace(user, active_hp=0.0)
            faint_order.append("user")

    # --- 遷移で扱わない効果 (印だけ残す) ---
    if dealt > 0:
        if entry.get("drain"):
            unsupported.append(("user", "drain", move_id))
        if (user_before.active.item or "") in (te.get("self_damage_items") or ()):
            unsupported.append(("user", "self_damage_item", user_before.active.item))
        tkinds, tmisc = _ability_kinds(target.active.ability)
        if "contact" in (entry.get("flags") or ()) and (
                (target.active.item or "") in (te.get("contact_damage_items") or ())
                or any(f.get("kind") == "contact_effect" and f.get("effect") == "damage"
                       for f in E.ability_formulas(target.active.ability))):
            unsupported.append(("target", "contact_damage", target.active.item or target.active.ability))
        if tmisc & {"free_hit", "free_physical_hit"}:
            unsupported.append(("target", "free_hit", target.active.ability))
        hp_before = dealt + target.active_hp
        if hp_before >= 0.999 and "target" in faint_order and (
                "survive_ohko_at_full_hp" in tmisc
                or (target.active.item or "") in (te.get("survive_one_hit_items") or ())):
            unsupported.append(("target", "survive_one_hit", target.active.item or target.active.ability))
    if entry.get("multihit"):
        unsupported.append(("user", "multihit_expected", move_id))
    if entry.get("crash"):
        unsupported.append(("user", "crash", move_id))
    return MoveResult(user, target, True, dealt, events, unsupported, faint_order)


def requires_replacement(side: SimSide) -> bool:
    """場の個体がひんしで、繰り出せる控えが居るか (ひんし後の繰り出し要求)"""
    return side.active_hp <= 0 and best_bench(side, side) is not None


def self_cost_of(before: SimSide, after: SimSide) -> dict:
    """使用者の陣営の前後から、自分の損失 {"faints", "hp_loss_frac", "requires_switch"} を作る (直接の採点に渡す欄)"""
    faints = before.active_hp > 0 and after.active_hp <= 0
    return {"faints": bool(faints),
            "hp_loss_frac": round(max(0.0, before.active_hp - after.active_hp), 3),
            "requires_switch": bool(faints and requires_replacement(after))}


def resolve_turn(me: SimSide, opp: SimSide, my_act: Action, opp_act: Action,
                 my_field: Optional[FieldView], opp_field: Optional[FieldView],
                 roll: str) -> TurnResult:
    """1ターンを解決する (副作用なし)。解決順と対応範囲はモジュールの説明を参照"""
    me = replace(me, bench=list(me.bench))
    opp = replace(opp, bench=list(opp.bench))
    res = TurnResult(me=me, opp=opp)
    ev = res.events

    # 交代は攻撃より先に解決
    if my_act.kind == "switch":
        me = apply_switch(me, my_act.bench_index)
        ev.append(("me", "switch", me.active.species_id))
    if opp_act.kind == "switch":
        opp = apply_switch(opp, opp_act.bench_index)
        ev.append(("opp", "switch", opp.active.species_id))
    # メガシンカ (1 試合 1 回) は技の解決前に姿を変える
    if opp_act.kind == "move" and opp_act.mega:
        opp = mega_evolve(opp)
    if my_act.kind == "move" and my_act.mega:
        me = mega_evolve(me)

    movers = []
    if my_act.kind == "move" and me.active_hp > 0:
        movers.append(("me", my_act.move_id,
                       priority(my_act.move_id, me.active, my_field or opp_field),
                       speed(me.active, my_field)))
    if opp_act.kind == "move" and opp.active_hp > 0:
        movers.append(("opp", opp_act.move_id,
                       priority(opp_act.move_id, opp.active, my_field or opp_field),
                       speed(opp.active, my_field)))
    trick_room = bool(my_field and my_field.trick_room)
    movers.sort(key=lambda m: (-m[2], m[3] if trick_room else -m[3]))

    protected = {"me": False, "opp": False}
    moved: set = set()
    fieldv = my_field or opp_field
    psychic_terrain = bool(fieldv and fieldv.terrain == "psychic")
    for who, move_id, _pri, _spe in movers:
        def_side = "opp" if who == "me" else "me"
        atk = me if who == "me" else opp
        dfn = opp if who == "me" else me
        if atk.active_hp <= 0:
            # 行動前にひんし: 行動しない (与ダメージ 0、自滅も HP の消費も起きない)
            ev.append((who, "skip", "fainted_before_acting"))
            continue
        moved.add(who)
        res.acted[who] = True

        # サイコフィールド: 接地している相手を対象にした優先度 > 0 の技は不発 (自分対象の積み/回復/まもるは通る)
        if psychic_terrain and _pri > 0 and move_id not in PROTECT_MOVES and move_id not in HEAL_MOVES \
                and move_id not in SETUP_MOVES and _is_grounded(dfn.active):
            ev.append((who, "fail", "psychic_terrain"))
            continue

        # ふいうち系: 相手が攻撃技を選んでいて未行動の場合のみ成功する
        if move_id in SUCKER_MOVES:
            def_act = opp_act if who == "me" else my_act
            if def_side in moved or not is_attack_action(def_act):
                ev.append((who, "fail", "sucker"))
                continue   # 失敗 (交代/変化技/相手行動済み)

        ev.append((who, "move", move_id))
        mr = resolve_move(atk, dfn, move_id, my_field if who == "me" else opp_field, roll,
                          target_protected=protected[def_side])
        if move_id in PROTECT_MOVES and mr.executed:
            protected[who] = True
        label = {"user": who, "target": def_side}
        ev.extend((label[w], k, d) for w, k, d in mr.events)
        res.unsupported.extend((label[w], k, s) for w, k, s in mr.unsupported)
        # 技の後のひんしの判定 (HP が 0 になった順)
        for w in mr.faint_order:
            side_lbl = label[w]
            if not res.fainted[side_lbl]:
                res.fainted[side_lbl] = True
                ev.append((side_lbl, "faint", None))
        atk, dfn = mr.user, mr.target
        me, opp = (atk, dfn) if who == "me" else (dfn, atk)

    # ターンの終わり: このターンにひんしになった陣営は控えを繰り出す (行動はしない)。控えが居なければその陣営は残り 0。
    # ターンの始めからひんしだった場の個体 (入力の状態) は従来どおり繰り出さない
    for lbl in ("me", "opp"):
        side = me if lbl == "me" else opp
        if res.fainted[lbl] and side.active_hp <= 0:
            nxt = best_bench(side, opp if lbl == "me" else me)
            if nxt is not None:
                res.requires_switch[lbl] = True
                side = apply_switch(side, nxt)
                ev.append((lbl, "replace", side.active.species_id))
                if lbl == "me":
                    me = side
                else:
                    opp = side
    res.me, res.opp = me, opp
    return res
