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

上の表は resolve_turn (単一分岐、roll 指定。探索 search が使う) の範囲で、2026-10-09 M2b でも変えていない。
M2b の分岐つきの解決 resolve_turn_branches (transition 方式の採点 advisor.scoring_transition が使う) は、同じ解決順で
resolve_move(full=True) (= _resolve_move_full) を使い、次を追加で解決する (数値は data/transition_effects.json の *_m2b の欄):

| 効果 | resolve_turn_branches / full での扱い | Showdown の根拠 |
|---|---|---|
| 同速 | 両方の順を speed_tie_prob (0.5) ずつ | sim/battle.ts (同速は乱数) |
| まひ・ねむり・こおり | まひは 1/8 で行動しない分岐 (チャンピオンズ)。ねむり・こおりはこのターン行動しない (残りターンは状態に無い) | data/mods/champions/conditions.ts par |
| 命中 | 当たる / 外れる の分岐 (ノーガード・きょけんとつげきの後は必中) | battle-actions.ts hitStepAccuracy |
| ダメージ乱数 | (M2b) 最小〜最大の等間隔 16 点で 倒せる / 倒せない の 2 分岐 (各群の平均)。**M2c で変更** (下の表) | modifyDamage (乱数 85〜100) |
| 連続技 | 回数の分岐 ([2, 5] は 35/35/15/15、スキルリンク 5)。multiaccuracy (トリプルアクセル) は 1 発ごとの命中で打ち切り、威力 1:2:3。1 発ごとの平均ダメージ (乱数は分岐しない) | hitStepMoveHitLoop |
| 外れたときの反動 (crash) | 最大 HP の 1/2 (切り捨て)。マジックガードは防ぐ | moves.ts highjumpkick onMoveFail |
| きあいのタスキ・がんじょう | HP 満タンからの一撃を HP 1 で耐える (タスキは消費、がんじょうはかたやぶりで無視)。連続技は 2 発目で倒れる | items.ts focussash / abilities.ts sturdy (onDamage) |
| ばけのかわ・アイスフェイス | 初撃 (アイスフェイスは物理) を 0 にしてフォルムチェンジ。ばけのかわは最大 HP の 1/8 を失う | abilities.ts disguise / iceface |
| 吸収・いのちのたま・ゴツゴツメット・さめはだ・てつのトゲ | 与えた HP × 割合 (四捨五入) の回復 / 最大 HP の 1/10 / 接触 1 発ごとに攻撃側の 1/6・1/8 | battle.ts drain / items.ts / abilities.ts |
| 壁・ねばねばネット・まきびし・あくび | 陣営の screens / sticky_web (着地時 素早さ -1) / spikes / ねむけ (volatiles "yawn"。ねむりは次のターンの終わり) | moves.ts |
| 自分の能力変化・確定 (100%) の追加効果・ひるみ | インファイト等の self_boosts、相手の能力変化・状態異常 (100% のもの)、ひるみは相手がまだ行動していないときの分岐 | moves.ts secondary / self |
| 交代技 | 当たったら使用者はその場で控え (継続方策 best_bench) を出し、後から動く相手の技はその個体が受ける | moves.ts selfSwitch |
| きょけんとつげき | 使用者は次の行動まで受けるダメージ 2 倍・必中 (volatiles "glaiverush") | moves.ts glaiverush |
| 表に無い変化技・selfdestruct "ifHit"・いのちのたま以外の自傷の持ち物 | 未対応 (unsupported。自分の候補でも相手の応手でも、transition 方式は legacy に退避。M2c で相手側も退避に) | |
| 確率つきの追加効果 (ひるみ以外)・急所・ターン終了時の効果・天候/フィールドの変化 | 未対応 (印は付けない。段 D) | |

2026-10-09 M2c で resolve_turn_branches / full に足したこと (数値は data/transition_effects.json の *_m2c の欄):

| 効果 | 扱い | Showdown の根拠 |
|---|---|---|
| ダメージ乱数 | calc_damage の rolls (整数の計算の実際の 16 通り) を 1 つずつ解決し、結果が同じ乱数だけを束ねる (倒せる乱数は 1 つ、倒せない乱数は値ごと)。平均 HP に潰さない | modifyDamage (乱数 85〜100) |
| 分岐の併合 | 各行動の後に完全に同じ状態の分岐の確率を足す。TRANSITION_MERGE_HP_UNITS > 0 なら最後の行動の後だけ HP を丸めて併合 | |
| 対戦の終了 | 陣営の生存数 (場 + 控え + 未判明の残り) が 0 になったら終わり、以後誰も行動しない。両方 0 なら最後にひんしになった陣営の勝ち (自滅技 = 使用側の負け、反動・いのちのたま = 使用側の勝ち)。実機との一致は未確認 | sim/battle.ts checkWin (win(faintData.target.side))・faintMessages |
| アンコール | 対象の直前の技 (last_move) に固定 ("encore:<技>")。直前の技が無い・failencore の技なら失敗。まだ行動していない対象はこのターンの技を固定技に替える | data/mods/champions/moves.ts encore |
| ちょうはつ | 対象 "taunt"。まだ行動していない対象の変化技は失敗 | data/moves.ts taunt (onBeforeMove) |
| メンタルハーブ | アンコール・ちょうはつをすぐ治して消費 | data/items.ts mentalherb |
| どんかん・アロマベール 等 / マジックミラー | 揮発状態を受けない / 跳ね返しは未対応 (unsupported "magic_bounce") | abilities (status_immune の volatile) |
| みがわり | 最大 HP の 1/4 (切り捨て) を失い同じ HP の身代わり (HP が 1/4 以下・既にあれば失敗)。身代わりは次の攻撃を受け (超えた分は通らない)、反動・吸収は身代わりへのダメージで掛かり、接触ダメージ・追加効果は無い。状態異常の技は失敗。音技 (bypasssub)・すりぬけは貫通 | data/moves.ts substitute |
| こらえる | このターン、技のダメージを HP 1 で耐える (タスキより先)。後に行動する者が居なければ失敗。ターンの終わりに消える | data/moves.ts endure |
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
    # 以下は M2b (resolve_turn_branches / full の効果解決) が使う欄。既定値のままなら従来の遷移 (resolve_turn) と同じ結果になる
    # (探索 search は設定しない)。screens: この陣営の壁 {"reflect", "light_screen", "aurora_veil": bool}。None は「渡された
    # FieldView の壁をそのまま使う」(従来)。spikes: まきびしの層 (0〜3)。sticky_web: ねばねばネット。tailwind: おいかぜ (素早さ 2 倍)。
    # volatiles: 場の個体の揮発状態 ("yawn" = ねむけ、"glaiverush" = きょけんとつげきの後)。交代で消える
    screens: Optional[dict] = None
    spikes: int = 0
    sticky_web: bool = False
    tailwind: bool = False
    volatiles: frozenset = frozenset()
    # 以下は M2c (2026-10-09) が使う欄 (既定値なら M2b までと同じ)。last_move: 場の個体が直前に使った技 (アンコールの固定先。
    # 交代で消える)。sub_hp: みがわりの残り HP (場の個体の最大 HP 比、0 = なし。交代で消える)。
    # volatiles に足した値: "encore:<技 id>" (アンコールで固定された技)、"taunt" (ちょうはつ)、"endure" (このターンのこらえる)
    last_move: Optional[str] = None
    sub_hp: float = 0.0

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
    # 未対応の効果を「どちらの行動から生じたか」で分けたもの (M2b の退避の判定用。resolve_turn_branches だけが埋める)
    unsupported_by: dict = field(default_factory=lambda: {"me": [], "opp": []})
    # 対戦の終了 (2026-10-09 M2c。resolve_turn_branches だけが埋める): winner = "me" / "opp" / None (終わっていない)、
    # end_reason = "wipe" (片方の選出が全滅) / "last_faint" (両方が同じ技の解決で全滅し、最後にひんしになった側の勝ち) /
    # None。faint_seq = このターンにひんしになった陣営の順 (Showdown の faintQueue の順)
    winner: Optional[str] = None
    end_reason: Optional[str] = None
    faint_seq: list = field(default_factory=list)


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
    """着地時の設置技のダメージ (最大 HP 比)。ステルスロック + まきびし (M2b、接地している個体だけ。spikes が 0 なら従来どおり)"""
    out = 0.0
    if side.stealth_rock:
        mult = get_dex().effectiveness("Rock", mon.types)
        out += 0.125 * mult
    if side.spikes and _is_grounded(mon):
        table = transition_effects().get("spikes_frac") or [0, 0.125, 1 / 6, 0.25]
        out += float(table[max(0, min(len(table) - 1, int(side.spikes)))])
    return out


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
    """交代 (着地時の設置技を受ける)。M2b: ねばねばネットは接地した個体の素早さ -1 (sticky_web が False なら従来どおり)、
    場の揮発状態 (volatiles) は交代で消える"""
    view, hp = side.bench[idx]
    hp = max(0.0, hp - hazard_frac(view, side))
    if side.sticky_web and hp > 0 and _is_grounded(view):
        boosts = dict(view.boosts or {})
        boosts["spe"] = max(-6, (boosts.get("spe") or 0) - 1)
        view = replace(view, boosts=boosts)
    new_bench = list(side.bench)
    new_bench[idx] = (side.active, side.active_hp)
    return replace(side, active=view, active_hp=hp, bench=new_bench, mega_forms={}, volatiles=frozenset(),
                   last_move=None, sub_hp=0.0)


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
                 target_protected: bool = False, damage: Optional[float] = None, full: bool = False, hit: bool = True,
                 hit_damages: Optional[list] = None) -> MoveResult:
    """技 1 回の効果を解決する (純粋)。行動順・ふいうち・サイコフィールドの判定は呼び出し側 (resolve_turn) が済ませる。

    damage: 与ダメージ (対象の最大 HP 比) を呼び出し側が計算済みならその値 (直接の採点が文脈 ctx 込みで計算した値を渡す)。
        None なら advisor.damage で roll の値を計算する。
    使用者がこの時点でひんしなら何もしない (呼び出し側が行動前のひんしを除いている前提だが、念のため)。
    full: True なら M2b の効果解決 (_resolve_move_full: 命中の外れ・連続技の 1 発ごと・耐える効果・吸収・いのちのたま・接触ダメージ・
        外れたときの反動・壁・ねばねばネット・まきびし・あくび・自分の能力変化・確定の追加効果)。hit = 命中したか (分岐は呼び出し側)、
        hit_damages = 1 発ごとの与ダメージ (対象の最大 HP 比、きょけんとつげきの 2 倍まで込み)。False (既定) は M1b までと同じ
    """
    if full:
        return _resolve_move_full(user, target, move_id, fieldv, target_protected=target_protected, hit=hit,
                                  hit_damages=hit_damages if hit_damages is not None else
                                  (None if damage is None else [float(damage)]))
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


# ==================================================================== M2b: full の効果解決と分岐つきの 1 ターン
# docs/ADVICE_ACCURACY_PLAN_1009.md 段 B-1 / B-3 / B-4 と段 C の前倒し (壁・設置・積み・あくび の 1 ターンの状態変化)。
# 従来の resolve_turn (単一分岐、roll 指定) は変えない (探索 search の結果を変えないため)。分岐つきの解決は resolve_turn_branches。
def _frac_of(key: str, default: tuple) -> tuple:
    v = transition_effects().get(key)
    if isinstance(v, dict):
        return int(v.get("num", default[0])), int(v.get("den", default[1]))
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), int(v[1])
    return default


def floor_hp_frac(view: MonView, num: int, den: int) -> float:
    """Showdown の this.damage(baseMaxhp × num / den) の量 (clampIntRange: 切り捨て、最低 1) を最大 HP 比で"""
    mhp = max(1, int(view.max_hp()))
    return max(1, (mhp * int(num)) // int(den)) / mhp


def _one_hp(view: MonView) -> float:
    return 1.0 / max(1, int(view.max_hp()))


def _ignores_target_ability(view: MonView) -> bool:
    """かたやぶり系 (相手の特性の breakable を無視): がんじょう・ばけのかわ・アイスフェイスを無視する"""
    _k, misc = _ability_kinds(view.ability)
    return "ignore_target_ability" in misc


def _magic_guard(view: MonView) -> bool:
    _k, misc = _ability_kinds(view.ability)
    return "no_indirect_damage" in misc


def _status_immune(view: MonView, status: str, move_id: Optional[str] = None) -> bool:
    """状態異常にならないか: タイプ (ほのお = やけど 等)、でんじは の じめん、特性の status_immune (スイートベール = ねむり 等)"""
    from advisor import effects as E
    te = transition_effects()
    types = set(view.types or ())
    if types & set((te.get("status_type_immunity") or {}).get(status) or ()):
        return True
    if move_id and move_id in (te.get("type_immunity_status_moves") or ()):
        mv = get_dex().move(move_id) or {}
        if mv.get("type") and get_dex().effectiveness(mv["type"], list(types)) == 0:
            return True
    code = (te.get("status_codes") or {}).get(status)
    for f in E.ability_formulas(view.ability):
        if f.get("kind") == "status_immune" and code and code in (f.get("status") or ()):
            return True
    return False


def _flinch_immune(view: MonView) -> bool:
    """ひるまない: せいしんりょく (status_immune の volatile flinch)、りんぷん (追加効果を受けない)、おんみつマント"""
    from advisor import effects as E
    if (view.item or "") in (transition_effects().get("flinch_block_items") or ()):
        return True
    for f in E.ability_formulas(view.ability):
        if f.get("kind") == "status_immune" and "flinch" in (f.get("volatile") or ()):
            return True
        if f.get("kind") == "misc" and f.get("effect") == "no_secondary_taken":
            return True
    return False


def _volatile_immune(view: MonView, vol: str) -> bool:
    """揮発状態 vol (taunt / encore) を受けない特性 (status_immune の volatile: どんかん = ちょうはつ、アロマベール 等)"""
    from advisor import effects as E
    return any(f.get("kind") == "status_immune" and vol in (f.get("volatile") or ())
               for f in E.ability_formulas(view.ability))


def _reflects_status(view: MonView) -> bool:
    """マジックミラー (変化技を跳ね返す。遷移では解決しない)"""
    from advisor import effects as E
    return any(f.get("kind") == "immune" and f.get("effect") == "reflect" for f in E.ability_formulas(view.ability))


def _encorable(move_id: Optional[str]) -> bool:
    """アンコールで固定できる直前の技か (直前の技があり、failencore の印が無い)"""
    from advisor import effects as E
    if not move_id:
        return False
    flag = transition_effects().get("encore_fail_flag") or "failencore"
    return flag not in (E.move_entry(move_id).get("flags") or ())


def _bypasses_sub(move_id: str, user_view: MonView) -> bool:
    """みがわりを貫通するか: 技の印 (bypasssub。音技を含む) か、使用者の特性 (すりぬけ)"""
    from advisor import effects as E
    te = transition_effects()
    flags = E.move_entry(move_id).get("flags") or ()
    if any(f in flags for f in (te.get("substitute_bypass_flags") or ("bypasssub",))):
        return True
    _k, misc = _ability_kinds(user_view.ability)
    return (te.get("substitute_bypass_ability_effect") or "ignore_screens_substitute") in misc


def _no_secondary_taken(view: MonView) -> bool:
    _k, misc = _ability_kinds(view.ability)
    return "no_secondary_taken" in misc or (view.item or "") in (transition_effects().get("flinch_block_items") or ())


def _form_change(view: MonView, species_id: str) -> MonView:
    """フォルムチェンジ (ばけのかわ → ばけたすがた 等)。種族値・タイプを新しい姿から引く。HP 割合・ランク・持ち物は保つ"""
    sp = get_dex().species(species_id)
    if not sp:
        return replace(view, species_id=species_id)
    return replace(view, species_id=species_id, base=dict(sp["baseStats"]), types=list(sp["types"]))


def _apply_boosts(view: MonView, delta: dict) -> MonView:
    boosts = dict(view.boosts or {})
    for k, d in (delta or {}).items():
        boosts[k] = max(-6, min(6, (boosts.get(k) or 0) + int(d)))
    return replace(view, boosts=boosts)


def _resolve_move_full(user: SimSide, target: SimSide, move_id: str, fieldv: Optional[FieldView], *,
                       target_protected: bool, hit: bool, hit_damages: Optional[list]) -> MoveResult:
    """技 1 回の解決 (M2b の full)。命中・乱数・連続技の回数は呼び出し側 (move_outcomes) が分岐して hit / hit_damages で渡す。

    M1b (resolve_move の既定) との違い: 外れ (hit=False) と外れたときの反動 (crash)、1 発ごとの解決 (連続技)、きあいのタスキ・
    がんじょう (HP 満タンからの一撃を HP 1 で耐える。タスキは消費)、ばけのかわ (初撃を 0 にして ばけたすがた、最大 HP の 1/8 を失う)・
    アイスフェイス (物理の初撃を 0)、吸収 (与えた HP × 割合、四捨五入)、いのちのたま (最大 HP の 1/10)、ゴツゴツメット・さめはだ・
    てつのトゲ (接触 1 発ごとに攻撃側の最大 HP の 1/6・1/8)、壁 (この陣営の screens)、ねばねばネット・まきびし、あくび (ねむけ。
    ねむりは次のターンの終わり)、かわらわり系の壁の解除、自分の能力変化 (インファイト 等)、確定 (100%) の追加効果 (相手の能力変化・
    状態異常)、ひるみ (確率を印で返し、分岐は呼び出し側)、交代技 (印を返し、繰り出しは呼び出し側)、きょけんとつげき (次の行動まで
    受けるダメージ 2 倍・必中)。未対応のまま残るのは、表に無い変化技 (effect_unmodeled)、selfdestruct "ifHit"、いのちのたま以外の
    自傷の持ち物"""
    from advisor import effects as E
    te = transition_effects()
    events: list = []
    unsupported: list = []
    faint_order: list = []

    def faint(who: str):
        if who not in faint_order:
            faint_order.append(who)

    if user.active_hp <= 0:
        return MoveResult(user, target, False, 0.0, [("user", "skip", "fainted")], [], [])
    entry = E.move_entry(move_id)
    mv = get_dex().move(move_id) or {}
    category = mv.get("category") or entry.get("category")

    # --- 自分・場を対象にする技 (相手がひんしでも出る) ---
    if move_id in PROTECT_MOVES:
        events.append(("user", "protect", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in HEAL_MOVES:
        user = replace(user, active_hp=min(1.0, user.active_hp + HEAL_MOVES[move_id]))
        events.append(("user", "heal", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    setup = SETUP_MOVES.get(move_id) or (entry.get("setup_boosts") if entry.get("target") == "self" else None)
    if setup:
        user = replace(user, active=_apply_boosts(user.active, setup))
        events.append(("user", "boost", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    screen_key = (te.get("screen_moves") or {}).get(move_id)
    if screen_key:
        cur = dict(user.screens or {})
        if cur.get(screen_key):
            events.append(("user", "fail", "already_active"))
        else:
            cur[screen_key] = True
            user = replace(user, screens=cur)
            events.append(("user", "screen", screen_key))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in HAZARD_MOVES or move_id in ("stickyweb", "spikes"):
        if move_id == "stickyweb":
            ok = not target.sticky_web
            target = replace(target, sticky_web=True) if ok else target
        elif move_id == "spikes":
            ok = target.spikes < 3
            target = replace(target, spikes=target.spikes + 1) if ok else target
        else:
            ok = not target.stealth_rock
            target = replace(target, stealth_rock=True) if ok else target
        events.append(("target", "hazard", move_id) if ok else ("user", "fail", "already_active"))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in (te.get("substitute_moves") or ()):
        # みがわり (M2c): 既にある・HP が最大 HP の 1/4 以下なら失敗。成功なら最大 HP の 1/4 (切り捨て) を失い、同じ量の身代わり
        num, den = _frac_of("substitute_frac", (1, 4))
        cost = floor_hp_frac(user.active, num, den)
        if user.sub_hp > 0 or user.active_hp <= num / den + 1e-9:      # source.hp <= source.maxhp / 4 (割り算は切り捨てない)
            events.append(("user", "fail", "substitute"))
        else:
            user = replace(user, active_hp=max(0.0, user.active_hp - cost), sub_hp=cost)
            events.append(("user", "substitute", round(cost, 6)))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if move_id in (te.get("endure_moves") or ()):
        # こらえる (M2c): このターン、技のダメージを HP 1 で耐える。後に行動する者が居ないときの失敗は呼び出し側 (_step) が判定する
        user = replace(user, volatiles=user.volatiles | {"endure"})
        events.append(("user", "endure", None))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)

    # --- 相手を対象にする技: 対象がひんしなら失敗、しめりけ ---
    if target.active_hp <= 0:
        events.append(("user", "fail", "no_target"))
        return MoveResult(user, target, False, 0.0, events, unsupported, faint_order)
    if _damp_blocks(move_id, user, target):
        events.append(("user", "fail", "damp"))
        return MoveResult(user, target, False, 0.0, events, unsupported, faint_order)

    encore = move_id in (te.get("encore_moves") or ())
    taunt = move_id in (te.get("taunt_moves") or ())
    if encore or taunt:
        # アンコール・ちょうはつ (M2c): 対象に揮発状態を付ける (命中 100、まもるで防がれる、みがわりは貫通)。メンタルハーブはすぐ治して
        # 消費する。アンコールは対象の直前の技 (last_move) が無い・固定できない技 (failencore) なら失敗。対象がこのターンにまだ行動して
        # いなければ、その行動を替える (アンコール) / 変化技を失敗させる (ちょうはつ) のは呼び出し側 (_step)
        vol = "encore" if encore else "taunt"
        tv = target.active
        if target_protected:
            events.append(("target", "protected", move_id))
        elif _reflects_status(tv) and not _ignores_target_ability(user.active):
            unsupported.append(("user", "magic_bounce", move_id))
        elif _volatile_immune(tv, vol) and not _ignores_target_ability(user.active):
            events.append(("user", "fail", f"{vol}_immune"))
        elif any(str(v).startswith(vol) for v in target.volatiles):
            events.append(("user", "fail", "already_active"))
        elif encore and not _encorable(target.last_move):
            events.append(("user", "fail", "encore_no_last_move"))
        elif vol in ((te.get("volatile_cure_items") or {}).get(tv.item or "") or ()):
            target = replace(target, active=replace(tv, item=None))
            events.append(("target", "cure_item", tv.item))
        else:
            tag = f"encore:{target.last_move}" if encore else "taunt"
            target = replace(target, volatiles=target.volatiles | {tag})
            events.append(("target", vol, target.last_move if encore else None))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)

    drowsy = move_id in (te.get("drowsy_moves") or ())
    if drowsy or move_id in STATUS_MOVES:
        if target_protected:
            events.append(("target", "protected", move_id))
        elif target.sub_hp > 0 and not _bypasses_sub(move_id, user.active):
            events.append(("target", "substitute_block", move_id))
        elif not hit:
            events.append(("user", "miss", move_id))
        elif drowsy:
            if target.active.status is None and "yawn" not in target.volatiles \
                    and not _status_immune(target.active, "sleep", move_id):
                target = replace(target, volatiles=target.volatiles | {"yawn"})
                events.append(("target", "drowsy", move_id))
            else:
                events.append(("user", "fail", "yawn"))
        else:
            st = STATUS_MOVES[move_id]
            if target.active.status is None and not _status_immune(target.active, st, move_id):
                target = replace(target, active=replace(target.active, status=st))
                events.append(("target", "status", st))
            else:
                events.append(("user", "fail", "status"))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)
    if category == "Status":
        unsupported.append(("user", "effect_unmodeled", move_id))
        return MoveResult(user, target, True, 0.0, events, unsupported, faint_order)

    # --- 攻撃技 ---
    kind = self_cost_kind(move_id)
    sd = entry.get("selfdestruct")
    if sd and sd != "always":
        unsupported.append(("user", "selfko_ifhit", move_id))
    user_before = user
    if kind == "selfko":
        user = replace(user, active_hp=0.0)
        events.append(("user", "selfko", move_id))
        faint("user")
    dealt = 0.0
    dealt_sub = 0.0      # 身代わりが受けたダメージ (対象の最大 HP 比。反動の量に入る)
    landed = 0
    hit_mon = 0          # 身代わりでなく場の個体に当たった回数 (追加効果の判定)
    mold = _ignores_target_ability(user_before.active)
    if target_protected:
        events.append(("target", "protected", move_id))
    elif not hit:
        events.append(("user", "miss", move_id))
        if entry.get("crash") and user.active_hp > 0 and not _magic_guard(user_before.active):
            loss = floor_hp_frac(user_before.active, *_frac_of("crash_frac", (1, 2)))
            user = replace(user, active_hp=max(0.0, user.active_hp - loss))
            events.append(("user", "crash", round(loss, 6)))
            if user.active_hp <= _EPS:
                user = replace(user, active_hp=0.0)
                faint("user")
    else:
        if move_id in (te.get("screen_break_moves") or ()) and target.screens and any(target.screens.values()):
            target = replace(target, screens={})
            events.append(("target", "screens_broken", move_id))
        if hit_damages is None:
            dmg0 = dmg_frac(replace(user_before.active, hp_frac=user_before.active_hp), target.active,
                            target.active_hp, move_id, fieldv, "avg")
            if "glaiverush" in target.volatiles:
                dmg0 *= 2.0
            hit_damages = [dmg0]
        contact = "contact" in (entry.get("flags") or ())
        _tk, tmisc = _ability_kinds(target.active.ability)
        disguise = te.get("disguise_species") or {}
        iceface = te.get("iceface_species") or {}
        bypass_sub = _bypasses_sub(move_id, user_before.active)
        for i, dmg in enumerate(hit_damages):
            if target.active_hp <= 0 or (i > 0 and user.active_hp <= 0):
                break
            dmg = max(0.0, float(dmg))
            tv = target.active
            if target.sub_hp > 0 and not bypass_sub:
                # みがわり (M2c): この一撃は身代わりが受ける (超えた分は通らない)。反動・吸収は身代わりへのダメージで、接触ダメージ・
                # 追加効果は無い (onTryPrimaryHit → HIT_SUBSTITUTE)
                sub_dmg = min(dmg, target.sub_hp)
                left = target.sub_hp - sub_dmg
                target = replace(target, sub_hp=0.0 if left <= _EPS else left)
                dealt_sub += sub_dmg
                landed += 1
                events.append(("target", "substitute_damage", round(sub_dmg, 6)))
                if target.sub_hp <= 0:
                    events.append(("target", "substitute_broken", None))
                ratio = entry.get("drain")
                if ratio and user.active_hp > 0 and sub_dmg > 0:
                    t_mhp = max(1, int(tv.max_hp()))
                    u_mhp = max(1, int(user_before.active.max_hp()))
                    heal = math.ceil(sub_dmg * t_mhp * float(ratio) - 1e-9) / u_mhp
                    user = replace(user, active_hp=min(1.0, user.active_hp + heal))
                    events.append(("user", "drain", round(heal, 6)))
                continue
            hit_mon += 1
            if dmg > 0 and not mold and tv.species_id in disguise:
                # ばけのかわ: この一撃は 0、ばけたすがたになって最大 HP の 1/8 を失う (onDamage → onUpdate)
                target = replace(target, active=_form_change(tv, disguise[tv.species_id]))
                loss = floor_hp_frac(tv, *_frac_of("disguise_self_damage", (1, 8)))
                target = replace(target, active_hp=max(0.0, target.active_hp - loss))
                events.append(("target", "disguise", round(loss, 6)))
                landed += 1
                if target.active_hp <= _EPS:
                    target = replace(target, active_hp=0.0)
                    faint("target")
            elif dmg > 0 and not mold and category == "Physical" and tv.species_id in iceface:
                target = replace(target, active=_form_change(tv, iceface[tv.species_id]))
                events.append(("target", "iceface", None))
                landed += 1
            else:
                hp = target.active_hp
                take = min(dmg, hp)
                if dmg > 0 and dmg >= hp - _EPS and "endure" in target.volatiles:
                    # こらえる (M2c): 技のダメージを HP 1 で耐える (onDamagePriority -10 で、タスキ・がんじょうより先)
                    take = max(0.0, hp - _one_hp(tv))
                    events.append(("target", "endure_hold", None))
                elif dmg > 0 and dmg >= hp - _EPS and hp >= 1.0 - 1e-6:
                    # HP 満タンからの一撃: きあいのタスキ (消費)・がんじょう (かたやぶりで無視) は HP 1 で耐える
                    if (tv.item or "") in (te.get("survive_one_hit_items") or ()):
                        take = hp - _one_hp(tv)
                        target = replace(target, active=replace(tv, item=None))
                        events.append(("target", "survive", tv.item))
                    elif "survive_ohko_at_full_hp" in tmisc and not mold:
                        take = hp - _one_hp(tv)
                        events.append(("target", "survive", tv.ability))
                landed += 1
                if take > 0:
                    target = replace(target, active_hp=max(0.0, hp - take))
                    dealt += take
                    events.append(("target", "damage", round(take, 6)))
                    ratio = entry.get("drain")
                    if ratio and user.active_hp > 0:
                        t_mhp = max(1, int(tv.max_hp()))
                        u_mhp = max(1, int(user_before.active.max_hp()))
                        heal = _round_half_up(take * t_mhp * float(ratio)) / u_mhp
                        if heal > 0:
                            user = replace(user, active_hp=min(1.0, user.active_hp + heal))
                            events.append(("user", "drain", round(heal, 6)))
                if target.active_hp <= _EPS:
                    target = replace(target, active_hp=0.0)
                    faint("target")
            # 接触ダメージ (1 発ごと。onDamagingHit): ゴツゴツメット 1/6、さめはだ・てつのトゲ 1/8 (攻撃側の最大 HP)。マジックガードは防ぐ
            if contact and user.active_hp > 0 and not _magic_guard(user_before.active):
                cdf = te.get("contact_damage_frac") or {}
                frac = cdf.get(tv.item or "") or cdf.get(tv.ability or "")
                if frac:
                    loss = floor_hp_frac(user_before.active, int(frac[0]), int(frac[1]))
                    user = replace(user, active_hp=max(0.0, user.active_hp - loss))
                    events.append(("user", "contact_damage", round(loss, 6)))
                    if user.active_hp <= _EPS:
                        user = replace(user, active_hp=0.0)
                        faint("user")
        if entry.get("multihit"):
            events.append(("target", "hits", landed))

    # --- 自分の HP の消費・反動 (ダメージの後。M1b と同じ規則) ---
    ukinds, umisc = _ability_kinds(user_before.active.ability)
    loss = 0.0
    if kind == "half_hp":
        if "no_indirect_damage" in umisc:
            events.append(("user", "hp_cost_blocked", user_before.active.ability))
        else:
            loss = _half_hp_cost_frac(user_before.active)
            events.append(("user", "hp_cost", round(loss, 6)))
    elif kind == "half_hp_on_damage" and dealt + dealt_sub > 0:
        if "recoil_none" in ukinds:
            events.append(("user", "recoil_blocked", user_before.active.ability))
        else:
            loss = _half_hp_cost_frac(user_before.active)
            events.append(("user", "hp_cost", round(loss, 6)))
    elif kind == "recoil" and dealt + dealt_sub > 0:
        # 反動は身代わりへのダメージにも掛かる (substitute の onTryPrimaryHit → applyRecoilDamage)
        if "recoil_none" in ukinds:
            events.append(("user", "recoil_blocked", user_before.active.ability))
        else:
            loss = _recoil_frac(user_before.active, target.active, dealt + dealt_sub, float(entry.get("recoil") or 0.0))
            events.append(("user", "recoil", round(loss, 6)))
    # いのちのたま: 当たったら最大 HP の 1/10 (onAfterMoveSecondarySelf)。マジックガードは防ぐ
    if landed > 0 and (user_before.active.item or "") == "lifeorb" and "no_indirect_damage" not in umisc:
        lo = floor_hp_frac(user_before.active, *_frac_of("life_orb_frac", (1, 10)))
        loss += lo
        events.append(("user", "life_orb", round(lo, 6)))
    if loss > 0 and user.active_hp > 0:
        user = replace(user, active_hp=max(0.0, user.active_hp - loss))
        if user.active_hp <= _EPS:
            user = replace(user, active_hp=0.0)
            faint("user")

    # --- 当たった後の効果 ---
    if landed > 0 and user.active_hp > 0 and entry.get("self_boosts"):
        user = replace(user, active=_apply_boosts(user.active, entry["self_boosts"]))
        events.append(("user", "self_boost", move_id))
    sec = entry.get("secondary") or {}
    if hit_mon > 0 and target.active_hp > 0 and sec and not _no_secondary_taken(target.active):
        chance = float(sec.get("chance") or 100)
        if chance >= 100:
            # 確定の追加効果だけ解決する (確率つきの能力変化・状態異常は段 D。ひるみは下の印で分岐する)
            if sec.get("target_boosts"):
                target = replace(target, active=_apply_boosts(target.active, sec["target_boosts"]))
                events.append(("target", "boost", move_id))
            codes = {v: k for k, v in (te.get("status_codes") or {}).items()}
            st = codes.get(sec.get("status") or "")
            if st and target.active.status is None and not _status_immune(target.active, st):
                target = replace(target, active=replace(target.active, status=st))
                events.append(("target", "status", st))
            if sec.get("self_boosts") and user.active_hp > 0:
                user = replace(user, active=_apply_boosts(user.active, sec["self_boosts"]))
                events.append(("user", "self_boost", move_id))
        if sec.get("volatile") == "flinch" and not _flinch_immune(target.active):
            events.append(("target", "flinch_chance", min(1.0, chance / 100.0)))
    if move_id in (te.get("glaive_rush_moves") or ()) and user.active_hp > 0:
        user = replace(user, volatiles=user.volatiles | {"glaiverush"})
        events.append(("user", "glaiverush", None))
    if entry.get("self_switch") and landed > 0 and user.active_hp > 0:
        events.append(("user", "pivot", move_id))
    if dealt > 0 and (user_before.active.item or "") in (te.get("self_damage_items") or ()) \
            and (user_before.active.item or "") != "lifeorb":
        unsupported.append(("user", "self_damage_item", user_before.active.item))
    return MoveResult(user, target, True, dealt, events, unsupported, faint_order)


def field_against(fieldv: Optional[FieldView], defender: SimSide) -> Optional[FieldView]:
    """攻撃側から見た FieldView (壁は防御側の陣営のもの)。defender.screens が None なら渡された値のまま (従来)"""
    if defender.screens is None:
        return fieldv
    f = fieldv or FieldView()
    s = defender.screens
    return replace(f, reflect=bool(s.get("reflect")), light_screen=bool(s.get("light_screen")),
                   aurora_veil=bool(s.get("aurora_veil")))


def side_speed(side: SimSide, fieldv: Optional[FieldView]) -> float:
    """陣営の場の個体の実効素早さ (おいかぜで 2 倍)"""
    s = speed(side.active, fieldv)
    return s * 2 if side.tailwind else s


def default_damage_fn(att_view: MonView, att_hp: float, def_view: MonView, def_hp: float, move_id: str,
                      fieldv: Optional[FieldView], side: Optional[str] = None) -> Optional[dict]:
    """分岐の解決が使うダメージ計算 (calc_damage の戻り値。計算できなければ None)。直接の採点は文脈込み・キャッシュつきの関数を渡す"""
    try:
        return calc_damage(replace(att_view, hp_frac=att_hp), replace(def_view, hp_frac=def_hp), move_id, fieldv)
    except Exception:
        return None


def roll_split(dmin: float, dmax: float, hp: float, points: int) -> list:
    """M2b までのダメージ乱数の集約 (2026-10-09 M2c で move_outcomes からは外した。旧挙動の再現のテストだけが使う):
    最小〜最大を等間隔に points 点置き、残り HP 以上 (倒せる) と未満 (倒せない) の 2 分岐 [(確率, その群の平均ダメージ)] に集約する。
    倒せない群を平均ダメージ 1 点に潰すので、その後の HP の消費・反動・次の行動・ひんしの確率がずれる (M2c のレビュー: 被弾を
    耐えてから てっていこうせん を使う局面で自分のひんし確率 100%、16 通りを個別に解決すると 50%)"""
    n = max(1, int(points))
    rolls = [dmin + (dmax - dmin) * k / (n - 1) for k in range(n)] if n > 1 else [(dmin + dmax) / 2.0]
    ko = [r for r in rolls if r >= hp - _EPS]
    non = [r for r in rolls if r < hp - _EPS]
    if ko and non:
        return [(len(ko) / n, sum(ko) / len(ko)), (len(non) / n, sum(non) / len(non))]
    return [(1.0, sum(rolls) / len(rolls))]


def damage_rolls(d: dict, mult: float = 1.0, points: int = 16) -> list:
    """calc_damage の戻り値から乱数ごとのダメージ (最大 HP 比) の列を作る (純粋)。rolls (Showdown の整数の計算の実際の 16 通り、
    2026-10-09 M2c) があればそれを使い、無ければ (rolls を返さない damage_fn) 最小〜最大を等間隔に points 点置く (M2b までの近似)。
    mult = きょけんとつげきの 2 倍 等"""
    rs = d.get("rolls")
    if rs:
        return [max(0.0, float(r)) / 100.0 * mult for r in rs]
    n = max(1, int(points))
    lo, hi = float(d.get("min") or 0.0) / 100.0 * mult, float(d.get("max") or 0.0) / 100.0 * mult
    return [lo + (hi - lo) * k / (n - 1) for k in range(n)] if n > 1 else [(lo + hi) / 2.0]


def roll_outcomes(rolls: list, threshold: float) -> list:
    """乱数の列を、結果が同じになるものどうしで束ねる [(確率, ダメージ)] (純粋。確率の合計は 1)。

    threshold (残り HP、みがわりがあればその HP) 以上の乱数は結果が同じ (与える量が threshold で頭打ち) なので 1 つに束ねる。
    それ未満は同じ値の乱数だけを束ね、平均には潰さない (後続の HP の消費・反動・次の行動・ひんしまで結果を分けるため)。
    並びは 倒せる群 → ダメージの大きい順"""
    n = len(rolls)
    if n == 0:
        return [(1.0, 0.0)]
    ko = [r for r in rolls if r >= threshold - _EPS]
    out = []
    if ko:
        out.append((len(ko) / n, min(ko)))
    counts: dict = {}
    for r in rolls:
        if r < threshold - _EPS:
            k = round(r, 12)
            counts[k] = counts.get(k, 0) + 1
    for r in sorted(counts, reverse=True):
        out.append((counts[r] / n, r))
    return out


def multihit_counts(entry: dict, view: MonView) -> list:
    """連続技の回数の分布 [(回数, 確率)] (multiaccuracy 以外)。[2, 5] は 35/35/15/15 (スキルリンク = 5、いかさまダイス = 4〜5)"""
    mh = entry.get("multihit")
    if isinstance(mh, int):
        return [(mh, 1.0)]
    if isinstance(mh, (list, tuple)) and len(mh) == 2:
        lo, hi = int(mh[0]), int(mh[1])
        kinds, _misc = _ability_kinds(view.ability)
        if "multihit_max" in kinds:
            return [(hi, 1.0)]
        if (lo, hi) == (2, 5):
            key = "multihit_loaded_dice_2_5" if (view.item or "") == "loadeddice" else "multihit_2_5"
            return [(int(c), float(p)) for c, p in (transition_effects().get(key) or [[2, 0.35], [3, 0.35], [4, 0.15], [5, 0.15]])]
        return [(c, 1.0 / (hi - lo + 1)) for c in range(lo, hi + 1)]
    return [(1, 1.0)]


def _status_move_accuracy(move_id: str, user: SimSide, target: SimSide) -> float:
    """相手を対象にする変化技の命中 (0..1)。ノーガード・きょけんとつげきの後は必中"""
    from advisor import effects as E
    if "glaiverush" in target.volatiles:
        return 1.0
    ukinds, _um = _ability_kinds(user.active.ability)
    tkinds, _tm = _ability_kinds(target.active.ability)
    if "no_miss" in ukinds or "no_miss" in tkinds:
        return 1.0
    acc = E.move_entry(move_id).get("accuracy")
    if acc is None or acc is True:
        return 1.0
    return max(0.0, min(1.0, float(acc) / 100.0))


def move_outcomes(user: SimSide, target: SimSide, move_id: str, fieldv: Optional[FieldView], *,
                  target_protected: bool = False, damage_fn=None, roll_points: int = 16,
                  att_side: Optional[str] = None) -> list:
    """技 1 回の分岐 [(確率, MoveResult)] (純粋。確率の合計は 1)。命中 (当たる / 外れる)、ダメージ乱数、連続技の回数
    (multiaccuracy は 1 発ごとの命中で打ち切り。威力が回数で上がる技 (トリプルアクセル) は 1 発目 : 2 発目 : 3 発目 = 1 : 2 : 3)。

    ダメージ乱数 (2026-10-09 M2c): calc_damage の rolls (Showdown の整数の計算の実際の 16 通り) を 1 つずつ解決し、結果が同じ乱数
    だけを束ねる (roll_outcomes: 倒せる乱数は 1 つ、倒せない乱数は値ごと)。M2b までは倒せない群を平均ダメージ 1 点に潰していた。
    連続技は 1 発ごとの平均ダメージで、乱数は分岐しない (近似のまま)。fieldv は防御側の壁込み (field_against)。
    att_side = 使用者の陣営 ("me" / "opp"。damage_fn に渡して陣営の文脈を選ばせる)"""
    from advisor import effects as E
    damage_fn = damage_fn or default_damage_fn
    entry = E.move_entry(move_id)
    mv = get_dex().move(move_id) or {}
    category = mv.get("category") or entry.get("category")

    def full(**kw):
        return resolve_move(user, target, move_id, fieldv, full=True, target_protected=target_protected, **kw)

    if user.active_hp <= 0:
        return [(1.0, full())]
    is_attack = category != "Status" and bool(mv.get("power") or entry.get("variable_power") or entry.get("fixed_damage"))
    if not is_attack:
        te = transition_effects()
        if (move_id in STATUS_MOVES or move_id in (te.get("drowsy_moves") or ())) and target.active_hp > 0 \
                and not target_protected and not _damp_blocks(move_id, user, target) \
                and not (target.sub_hp > 0 and not _bypasses_sub(move_id, user.active)):
            acc = _status_move_accuracy(move_id, user, target)
            if acc < 1.0:
                return [(acc, full(hit=True)), (1.0 - acc, full(hit=False))]
        return [(1.0, full())]
    if target.active_hp <= 0 or target_protected or _damp_blocks(move_id, user, target):
        return [(1.0, full(hit_damages=[0.0]))]
    dmg_target = target
    if move_id in (transition_effects().get("screen_break_moves") or ()) and target.screens:
        dmg_target = replace(target, screens={})
    d = damage_fn(user.active, user.active_hp, target.active, target.active_hp, move_id,
                  field_against(fieldv, dmg_target), side=att_side)
    if not d:
        return [(1.0, full(hit_damages=[0.0]))]
    glaive = 2.0 if "glaiverush" in target.volatiles else 1.0
    acc_pct = d.get("accuracy")
    acc = 1.0 if (acc_pct is None or glaive > 1.0) else max(0.0, min(1.0, float(acc_pct) / 100.0))
    outs = []
    mh = entry.get("multihit")
    if mh:
        hits_noacc = float(d.get("hits") or 1.0) or 1.0
        unit = float(d["avg"]) / 100.0 / hits_noacc * glaive
        varpow = entry.get("variable_power") == "hit_count"

        def per(k):
            return [unit * (j if varpow else 1) for j in range(1, k + 1)]

        if entry.get("multiaccuracy"):
            n = int(mh if isinstance(mh, int) else mh[-1])
            for k in range(1, n + 1):
                pk = (acc ** k) * ((1.0 - acc) if k < n else 1.0)
                outs.append((pk, full(hit=True, hit_damages=per(k))))
        else:
            for c, pc in multihit_counts(entry, user.active):
                outs.append((acc * pc, full(hit=True, hit_damages=per(c))))
    else:
        # 身代わりがあれば、身代わりの HP 以上の乱数は結果が同じ (超えた分は通らない)
        sub = target.sub_hp > 0 and not _bypasses_sub(move_id, user.active)
        threshold = target.sub_hp if sub else target.active_hp
        for pr, dmg in roll_outcomes(damage_rolls(d, glaive, roll_points), threshold):
            outs.append((acc * pr, full(hit=True, hit_damages=[dmg])))
    if acc < 1.0:
        outs.append((1.0 - acc, full(hit=False)))
    return [(p, r) for p, r in outs if p > 1e-12]


@dataclass
class _Branch:
    """分岐の途中の状態 (resolve_turn_branches の内部)"""
    p: float
    me: SimSide
    opp: SimSide
    events: list
    acted: dict
    fainted: dict
    unsupported: list
    unsupported_by: dict
    protected: dict
    moved: frozenset = frozenset()
    flinched: frozenset = frozenset()
    faint_seq: tuple = ()             # ひんしになった陣営の順 (Showdown の faintQueue の順。M2c)
    winner: Optional[str] = None      # 対戦が終わった分岐の勝者 (M2c)
    end_reason: Optional[str] = None
    skips: tuple = ()                 # 行動しなかった理由 ((陣営, 理由), ...)。併合のキーに入れる (score の「先に倒される」の集計)

    def fork(self, mult: float) -> "_Branch":
        return _Branch(self.p * mult, self.me, self.opp, list(self.events), dict(self.acted), dict(self.fainted),
                       list(self.unsupported), {k: list(v) for k, v in self.unsupported_by.items()},
                       dict(self.protected), self.moved, self.flinched, self.faint_seq, self.winner, self.end_reason,
                       self.skips)

    def side(self, who: str) -> SimSide:
        return self.me if who == "me" else self.opp

    def set_side(self, who: str, s: SimSide) -> None:
        if who == "me":
            self.me = s
        else:
            self.opp = s


def resolve_turn_branches(me: SimSide, opp: SimSide, my_act: Action, opp_act: Action,
                          my_field: Optional[FieldView], opp_field: Optional[FieldView], *,
                          damage_fn=None, roll_points: Optional[int] = None, reserves: Optional[dict] = None,
                          merge_hp_units: Optional[float] = None) -> list:
    """1 ターンを分岐つきで解決する (純粋。入力は書き換えない)。戻り値 [(確率, TurnResult)]、確率の合計は 1。

    分岐: 行動順 (優先度 → 素早さ (おいかぜ・トリックルーム込み)、同速は speed_tie_prob で両方の順)、まひの行動不能 (1/8、
    チャンピオンズ)、命中 (当たる / 外れる)、ダメージ乱数 (calc_damage の 16 通りを結果ごとに。M2c)、連続技の回数、ひるみ
    (相手がまだ行動していないときだけ)。ねむり・こおりの個体はこのターン行動しない (残りターンは状態に無い)。解決の順序は
    resolve_turn と同じ (交代 → メガ → 技 → 技ごとのひんし判定 → ターンの終わりの繰り出し)。行動前にひんしの個体は行動しない
    (与ダメージ 0、自滅も HP の消費も無し)。交代技 (とんぼがえり 等) が当たったら、使用者はその場で控えを出す。ひんし後の繰り出しと
    交代技の繰り出し先は継続方策 best_bench (静的評価が最良の 1 体。対戦の仕様ではなく評価のための仮定) で決める。

    M2c (2026-10-09):
    - 分岐の併合: 各行動の解決の後に、状態 (両陣営の状態・行動済み・ひんし・ひるみ・ひんしの順・勝者 等) が完全に同じ分岐の確率を
      足して 1 つにする (events は最初の分岐のもの。同速の行動順が違う分岐どうしは併合しない)。merge_hp_units (None なら config
      TRANSITION_MERGE_HP_UNITS) が正の値なら、最後の行動の後だけ HP をその HP 量 (実数値) に丸めて併合する (HP はその群の確率の
      重みつき平均。ひんしと生存は混ぜない)。途中で丸めると、後の HP の消費・反動・ひんしの閾値をまたいで平均に潰すので丸めない。
    - 対戦の終了: 各技の解決の後に、陣営の生存数 (場 + 控え + reserves = 状態に無い未判明の残り {"me": n, "opp": n}) が 0 になったら
      対戦を終える (その後の行動はしない)。片方だけ 0 なら相手の勝ち (end_reason "wipe")。両方 0 なら最後にひんしになった個体の陣営の
      勝ち (end_reason "last_faint"): Showdown の checkWin は全陣営の残りが 0 のとき faintQueue の最後に処理した個体の陣営を勝ちにする
      (sim/battle.ts checkWin: this.win(faintData.target.side)、gen 5 以降)。自滅技 (useMoveInner で使用者を先に faint) は使用者の
      負け、反動・いのちのたま (ダメージの後) は使用者の勝ち。実機 (チャンピオンズ) でも同じ (2026-10-09 開発者の確認: 自滅技は使用側の負け、反動技は使用側の勝ち)。
    - アンコール・ちょうはつ: 後から動く対象は、アンコールなら固定技に替えて行動する (行動順は替える前の技のまま。チャンピオンズの
      優先度の付け替えは近似しない)、ちょうはつなら変化技が失敗する。こらえる: 後に行動する者が居なければ失敗。
      ターンの終わりにこらえる (endure) は消える。
    damage_fn(att_view, att_hp, def_view, def_hp, move_id, field, side=...) -> calc_damage の戻り値 (既定は default_damage_fn)"""
    from champions_agent.config import TRANSITION_MERGE_HP_UNITS, TRANSITION_ROLL_POINTS
    damage_fn = damage_fn or default_damage_fn
    points = int(roll_points or TRANSITION_ROLL_POINTS)
    units = float(TRANSITION_MERGE_HP_UNITS if merge_hp_units is None else merge_hp_units)
    reserves = {"me": int((reserves or {}).get("me") or 0), "opp": int((reserves or {}).get("opp") or 0)}
    te = transition_effects()
    me = replace(me, bench=list(me.bench))
    opp = replace(opp, bench=list(opp.bench))
    ev: list = []
    if my_act.kind == "switch":
        me = apply_switch(me, my_act.bench_index)
        ev.append(("me", "switch", me.active.species_id))
    if opp_act.kind == "switch":
        opp = apply_switch(opp, opp_act.bench_index)
        ev.append(("opp", "switch", opp.active.species_id))
    if opp_act.kind == "move" and opp_act.mega:
        opp = mega_evolve(opp)
    if my_act.kind == "move" and my_act.mega:
        me = mega_evolve(me)
    fieldv = my_field or opp_field
    movers = []
    if my_act.kind == "move" and me.active_hp > 0:
        movers.append(("me", my_act.move_id, priority(my_act.move_id, me.active, fieldv), side_speed(me, my_field)))
    if opp_act.kind == "move" and opp.active_hp > 0:
        movers.append(("opp", opp_act.move_id, priority(opp_act.move_id, opp.active, fieldv), side_speed(opp, my_field)))
    trick_room = bool(my_field and my_field.trick_room)
    movers.sort(key=lambda m: (-m[2], m[3] if trick_room else -m[3]))
    orders = [(1.0, movers)]
    if len(movers) == 2 and movers[0][2] == movers[1][2] and movers[0][3] == movers[1][3]:
        pt = float(te.get("speed_tie_prob", 0.5))
        orders = [(pt, movers), (1.0 - pt, [movers[1], movers[0]])]
    acts = {"me": my_act, "opp": opp_act}
    fields = {"me": my_field, "opp": opp_field}
    keycache: dict = {}
    finals: list = []
    for p_ord, order in orders:
        events = list(ev)
        if len(orders) > 1:
            events.append((order[0][0], "speed_tie_first", None))
        br = _Branch(p_ord, me, opp, events, {"me": False, "opp": False}, {"me": False, "opp": False}, [],
                     {"me": [], "opp": []}, {"me": False, "opp": False})
        frontier = [br]
        for i in range(len(order)):
            nxt: list = []
            for b in frontier:
                nxt.extend(_step(b, order, i, acts, fields, damage_fn, points, reserves))
            # 途中の併合は完全に同じ状態だけ (丸めると後の HP の消費・反動・ひんしの閾値をまたいで平均に潰すため)。
            # 丸めた併合 (units > 0) は最後の 1 回だけ (局面評価 V に渡す状態の数を減らす)
            last = i == len(order) - 1
            frontier = merge_branches(nxt, units if last else 0.0, keycache)
        if not order:
            frontier = merge_branches(frontier, units, keycache)
        finals.extend(frontier)          # 行動順 (同速の分岐) が違う分岐どうしは併合しない (events の行動順を残す)
    return [(b.p, _finish_branch(b)) for b in finals]


def _check_end(br: _Branch, reserves: dict) -> None:
    """陣営の生存数 (場 + 控え + 状態に無い残り) が 0 になったら対戦を終える (勝者と理由。規則は resolve_turn_branches の説明)"""
    if br.winner is not None:
        return
    alive = {}
    for lbl in ("me", "opp"):
        s = br.side(lbl)
        alive[lbl] = (1 if s.active_hp > 0 else 0) + sum(1 for _v, hp in s.bench if hp > 0) + reserves.get(lbl, 0)
    if alive["me"] == 0 and alive["opp"] == 0:
        br.winner = br.faint_seq[-1] if br.faint_seq else None
        br.end_reason = "last_faint"
    elif alive["me"] == 0:
        br.winner, br.end_reason = "opp", "wipe"
    elif alive["opp"] == 0:
        br.winner, br.end_reason = "me", "wipe"
    if br.end_reason:
        br.events.append((br.winner, "game_end", br.end_reason))


def _step(br: _Branch, order: list, i: int, acts: dict, fields: dict, damage_fn, points: int, reserves: dict) -> list:
    """order[i] の行動を解決した分岐の列 (行動前のひんし・ひるみ・ねむり/こおり・まひの行動不能を含む)"""
    if br.winner is not None or br.end_reason is not None:
        return [br]                     # 対戦が終わった分岐ではもう誰も行動しない
    who = order[i][0]
    atk = br.side(who)
    if atk.active_hp <= 0:
        # 行動前にひんし: 行動しない (与ダメージ 0、自滅も HP の消費も起きない)
        br.events.append((who, "skip", "fainted_before_acting"))
        br.skips = br.skips + ((who, "fainted_before_acting"),)
        return [br]
    if who in br.flinched:
        br.events.append((who, "skip", "flinch"))
        br.skips = br.skips + ((who, "flinch"),)
        br.moved = br.moved | {who}
        return [br]
    st = atk.active.status
    if st in ("sleep", "freeze"):
        br.events.append((who, "skip", st))
        br.skips = br.skips + ((who, st),)
        br.moved = br.moved | {who}
        return [br]
    out: list = []
    if st == "paralysis":
        num, den = _frac_of("paralysis_full_para", (1, 8))
        pp = num / den
        b2 = br.fork(pp)
        b2.events.append((who, "skip", "paralysis"))
        b2.skips = b2.skips + ((who, "paralysis"),)
        b2.moved = b2.moved | {who}
        out.append(b2)
        br = br.fork(1.0 - pp)
    out.extend(_act(br, order, i, acts, fields, damage_fn, points, reserves))
    return out


def _act(br: _Branch, order: list, i: int, acts: dict, fields: dict, damage_fn, points: int, reserves: dict) -> list:
    who, move_id, pri, _spe = order[i]
    def_side = "opp" if who == "me" else "me"
    te = transition_effects()
    atk = br.side(who)
    br.moved = br.moved | {who}
    br.acted[who] = True
    # アンコール (M2c): 固定された技に替えて行動する。ちょうはつ: 変化技は失敗する (onBeforeMove)
    locked = next((v.split(":", 1)[1] for v in atk.volatiles if str(v).startswith("encore:")), None)
    if locked and locked != move_id:
        br.events.append((who, "encore_override", locked))
        move_id = locked
    if "taunt" in atk.volatiles and _is_status_move(move_id):
        br.events.append((who, "fail", "taunt"))           # 技を出す前に止まる (cant)。直前の技は変わらない
        return [br]
    fieldv = fields["me"] or fields["opp"]
    dfn = br.side(def_side)
    if fieldv is not None and fieldv.terrain == "psychic" and pri > 0 and move_id not in PROTECT_MOVES \
            and move_id not in HEAL_MOVES and move_id not in SETUP_MOVES and _is_grounded(dfn.active):
        br.events.append((who, "fail", "psychic_terrain"))
        br.set_side(who, replace(atk, last_move=move_id))
        return [br]
    if move_id in SUCKER_MOVES:
        def_act = acts[def_side]
        if def_side in br.moved or not is_attack_action(def_act):
            br.events.append((who, "fail", "sucker"))
            br.set_side(who, replace(atk, last_move=move_id))
            return [br]
    if move_id in (te.get("endure_moves") or ()):
        # こらえる: 後に行動する者が居なければ失敗 (onPrepareHit: queue.willAct())
        later_acts = any(m[0] != who and m[0] not in br.moved and br.side(m[0]).active_hp > 0 for m in order[i + 1:])
        if not later_acts:
            br.events.append((who, "fail", "endure_last"))
            br.set_side(who, replace(atk, last_move=move_id))
            return [br]
    br.events.append((who, "move", move_id))
    atk = replace(atk, last_move=move_id)
    br.set_side(who, atk)
    label = {"user": who, "target": def_side}
    out: list = []
    for p_o, mr in move_outcomes(atk, dfn, move_id, fields[who], target_protected=br.protected[def_side],
                                 damage_fn=damage_fn, roll_points=points, att_side=who):
        nb = br.fork(p_o)
        if move_id in PROTECT_MOVES and mr.executed:
            nb.protected[who] = True
        nb.events.extend((label[w], k, d) for w, k, d in mr.events)
        marks = [(label[w], k, s) for w, k, s in mr.unsupported]
        nb.unsupported.extend(marks)
        nb.unsupported_by[who].extend(marks)
        for w in mr.faint_order:
            lbl = label[w]
            nb.faint_seq = nb.faint_seq + (lbl,)
            if not nb.fainted[lbl]:
                nb.fainted[lbl] = True
                nb.events.append((lbl, "faint", None))
        nb.set_side(who, mr.user)
        nb.set_side(def_side, mr.target)
        _check_end(nb, reserves)
        # 交代技: 当たったら使用者はその場で控えを出す (繰り出し先は継続方策 best_bench)
        if nb.winner is None and any(w == "user" and k == "pivot" for w, k, _d in mr.events):
            s = nb.side(who)
            nxt = best_bench(s, nb.side(def_side))
            if nxt is not None and s.active_hp > 0:
                s = apply_switch(s, nxt)
                nb.set_side(who, s)
                nb.events.append((who, "pivot_switch", s.active.species_id))
        chance = next((float(d) for w, k, d in mr.events if w == "target" and k == "flinch_chance"), 0.0)
        later = any(m[0] == def_side for m in order[i + 1:])
        if chance > 0 and later and nb.winner is None and def_side not in nb.moved and nb.side(def_side).active_hp > 0:
            if chance >= 1.0:
                nb.flinched = nb.flinched | {def_side}
                out.append(nb)
            else:
                f1 = nb.fork(chance)
                f1.flinched = f1.flinched | {def_side}
                out.append(f1)
                out.append(nb.fork(1.0 - chance))
        else:
            out.append(nb)
    return out


def _is_status_move(move_id: Optional[str]) -> bool:
    from advisor import effects as E
    mv = get_dex().move(move_id) if move_id else None
    cat = (mv or {}).get("category") or E.move_entry(move_id).get("category")
    return cat == "Status"


# ------------------------------------------------------------------ 分岐の併合 (M2c)
def _view_state_key(v: MonView, cache: dict) -> tuple:
    hit = cache.get(id(v))
    if hit is not None and hit[0] is v:
        return hit[1]
    k = (v.species_id, v.level, tuple(v.types or ()), tuple(sorted((v.base or {}).items())), v.status,
         tuple(sorted((v.boosts or {}).items())), v.ability, v.item, tuple(sorted((v.ev or {}).items())),
         tuple(sorted((v.nature or {}).items())), v.spe_bounds)
    cache[id(v)] = (v, k)
    return k


def _hp_key(hp: float, view: MonView, units: float):
    if hp <= 0:
        return 0
    if units <= 0:
        return round(float(hp), 9)
    mhp = max(1, int(view.max_hp()))
    return max(1, int(round(float(hp) * mhp / units)))


def side_state_key(s: SimSide, units: float = 0.0, cache: Optional[dict] = None) -> tuple:
    """陣営の状態のキー (分岐の併合用。units > 0 なら HP をその HP 量に丸める)。cache に同じ物 (id と is) のキーを覚えておく"""
    c = cache if cache is not None else {}
    ck = ("S", id(s), units)
    hit = c.get(ck)
    if hit is not None and hit[0] is s:
        return hit[1]
    out = _side_state_key(s, units, c)
    c[ck] = (s, out)
    return out


def _side_state_key(s: SimSide, units: float, c: dict) -> tuple:
    scr = tuple(sorted((s.screens or {}).items())) if s.screens is not None else None
    return (_view_state_key(s.active, c), _hp_key(s.active_hp, s.active, units),
            tuple((_view_state_key(v, c), _hp_key(hp, v, units)) for v, hp in s.bench),
            s.stealth_rock, s.spikes, s.sticky_web, scr, s.tailwind, tuple(sorted(map(str, s.volatiles))),
            tuple(sorted((s.mega_forms or {}).items())), s.mega_used, s.last_move, round(float(s.sub_hp), 9))


def _branch_key(b: _Branch, units: float, cache: dict) -> tuple:
    return (side_state_key(b.me, units, cache), side_state_key(b.opp, units, cache),
            b.acted["me"], b.acted["opp"], b.fainted["me"], b.fainted["opp"], b.protected["me"], b.protected["opp"],
            b.moved, b.flinched, b.faint_seq, b.winner, b.end_reason, b.skips, tuple(b.unsupported),
            tuple(b.unsupported_by["me"]), tuple(b.unsupported_by["opp"]))


def merge_branches(branches: list, units: float = 0.0, cache: Optional[dict] = None) -> list:
    """同じ状態の分岐の確率を足して 1 つにする (純粋。並びは最初に現れた順)。units > 0 なら HP を丸めたキーで併合し、場の個体の
    HP はその群の確率の重みつき平均にする (ひんしと生存は混ぜない)"""
    cache = cache if cache is not None else {}
    groups: dict = {}
    acc: dict = {}
    for b in branches:
        k = _branch_key(b, units, cache)
        g = groups.get(k)
        if g is None:
            groups[k] = b
            acc[k] = [b.p * b.me.active_hp, b.p * b.opp.active_hp]
        else:
            g.p += b.p
            acc[k][0] += b.p * b.me.active_hp
            acc[k][1] += b.p * b.opp.active_hp
    out = list(groups.values())
    if units > 0:
        for k, g in groups.items():
            if g.p > 0:
                if g.me.active_hp > 0:
                    g.me = replace(g.me, active_hp=acc[k][0] / g.p)
                if g.opp.active_hp > 0:
                    g.opp = replace(g.opp, active_hp=acc[k][1] / g.p)
    return out


def _finish_branch(br: _Branch) -> TurnResult:
    """ターンの終わり: このターンにひんしになった陣営は控え (継続方策 best_bench) を繰り出す (行動はしない)。
    1 ターンの揮発状態 (こらえる) を外す"""
    res = TurnResult(me=br.me, opp=br.opp, events=br.events, acted=br.acted, fainted=br.fainted,
                     unsupported=br.unsupported, unsupported_by=br.unsupported_by, winner=br.winner,
                     end_reason=br.end_reason, faint_seq=list(br.faint_seq))
    me, opp = br.me, br.opp
    for lbl in ("me", "opp"):
        side = me if lbl == "me" else opp
        if "endure" in side.volatiles:
            side = replace(side, volatiles=side.volatiles - {"endure"})
        if res.fainted[lbl] and side.active_hp <= 0 and br.winner is None:
            nxt = best_bench(side, opp if lbl == "me" else me)
            if nxt is not None:
                res.requires_switch[lbl] = True
                side = apply_switch(side, nxt)
                res.events.append((lbl, "replace", side.active.species_id))
        if lbl == "me":
            me = side
        else:
            opp = side
    res.me, res.opp = me, opp
    return res
