"""自分パーティの「対戦で使える個体」の判定。

チャンピオンズは6体から3体選出のため、交代候補・詰み筋・RL合法手は
選出済みの3体に限られる。選出画面の白リボン追跡 (is_picked) を第一情報、
対戦中に登場した確かな記録 (種族の同定が推定でない個体の HP 観測・場に出た・選出順・登場) を補完に使う。

2026-10-20 (docs/REAL_ACCEPT_TRANSITION_20261020.md §3・§6、レビュー・開発者承認): 選出の印が足りないとき全員を許可していた
(None) ため、対戦冒頭 (印なし) に実際に選出されていないブリジュラスへの交代を第一候補に出した (助言 20261020_085420-0052)。
判定を 3 値 (picked / not_picked / unknown) にし、unknown の個体は交代候補・残存戦力・探索・RL の合法手のどれにも入れない
(交代候補が空になることを許容する)。ただし「選出のうち未判明の数」(unknown_count) は残存戦力の不確かさとして残す
(0 体扱いにして「残り 1 体」のような誤った終盤判定をしない)。登場記録の反映は vision の is_picked を書き換えず、ここ (助言側の
判定) で行う: 同じ判定が実機・対戦ログの再生・シミュレータに効く。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from champions_agent.config import BSS_PICK_COUNT, PARTY_SIZE


@dataclass(frozen=True)
class PickState:
    """自分の選出の 3 値の判定 (pick_state の戻り値)。picked / not_picked / unknown は party の index の集合"""
    picked: frozenset
    not_picked: frozenset
    unknown: frozenset
    unknown_count: int          # 選出のうち、どの個体か分かっていない数 (pick_count − picked の数、unknown の枠の数が上限、下限 0)

    def to_advice(self, party: list) -> dict:
        """advice の欄 pick_state (種族 id の並び。種族が無い枠は slot<i>)"""
        def ids(idx):
            return [_sid(party, i) for i in sorted(idx)]
        return {"picked": ids(self.picked), "unknown_count": int(self.unknown_count),
                "not_picked": ids(self.not_picked)}


def _sid(party: list, i: int) -> str:
    p = party[i] if 0 <= i < len(party) else {}
    return (p or {}).get("species_id") or f"slot{i}"


def species_certain(p: dict) -> bool:
    """種族の同定が確か (推定でない) か (純粋)。選出画面の推定 (species_guess)・形態の候補 (species_candidates)・対応待ち
    (pending) の個体は確かでない"""
    if not p or not (p.get("species_id") or p.get("species_ja")):
        return False
    return not (p.get("species_guess") or p.get("species_candidates") or p.get("pending"))


def has_appearance_record(p: dict) -> bool:
    """対戦に出たと確かに分かる記録があるか (純粋)。種族の同定が確か (species_certain) で、次のどれか:
    HP を観測済み (hp_percent / hp_read_ts)、場に出ている (is_active)、選出順 (pick_order)、
    登場した (entry_turn。「ゆけっ X」等の交代イベントで入る)、ひんし"""
    if not species_certain(p):
        return False
    return bool(p.get("hp_percent") is not None or p.get("hp_read_ts") or p.get("is_active")
                or p.get("pick_order") or p.get("entry_turn") is not None or p.get("status") == "fainted")


def pick_state(my_state: dict, pick_count: int = BSS_PICK_COUNT) -> PickState:
    """自分の選出の 3 値の判定 (純粋)。

    - 選出の印 (is_picked) が pick_count 体分あればそれで確定 (従来どおり。印は登場記録より優先)
    - それ以外は picked = 印 ∪ 確かな登場記録 (has_appearance_record)
    - picked が pick_count 体以上なら確定: 残りは not_picked
    - 足りなければ残りは unknown (交代候補・残存戦力・探索・RL の合法手に入れない)。unknown_count = pick_count − picked の数
      (未判明の選出は unknown の枠のどれかなので、unknown の枠の数を上限にする。party が 6 体そろわない合成の状態で、
      状態に無い個体を数えないため)
    対象は先頭 PARTY_SIZE 枠 (誤読で生えた 7 体目以降は使える個体に数えない)"""
    party = list((my_state or {}).get("party") or [])[:PARTY_SIZE]
    idx = set(range(len(party)))
    marked = {i for i in idx if (party[i] or {}).get("is_picked")}
    if len(marked) >= pick_count:
        picked = marked
    else:
        picked = marked | {i for i in idx if has_appearance_record(party[i] or {})}
    rest = idx - picked
    if len(picked) >= pick_count:
        return PickState(frozenset(picked), frozenset(rest), frozenset(), 0)
    return PickState(frozenset(picked), frozenset(), frozenset(rest), max(0, min(len(rest), pick_count - len(picked))))


def usable_indices(my_state: dict) -> set:
    """交代候補・残存戦力・探索・RL の合法手に使ってよい自分のパーティ index の集合 (pick_state の picked)。
    情報が無くても None (全員許可) にはしない (印 + 確かな登場記録 = 場の個体を含む、だけ)"""
    return set(pick_state(my_state).picked)


def battle_party_indices(my_state: dict) -> set:
    """交代候補にしてよい自分のパーティindex集合 (usable_indices と同じ。2026-10-20 から None を返さない)"""
    return usable_indices(my_state)


def is_switchable(my_state: dict, index: int) -> bool:
    """index のポケモンが交代候補として合法か (選出制限を考慮)"""
    return index in usable_indices(my_state)


def unknown_alive_count(my_state: dict, known_alive: int, ps: Optional[PickState] = None) -> int:
    """状態から特定できない自分の生存の残り (純粋)。残数 (画面の HUD の remaining) があれば 残数 − 分かっている生存数
    (従来の scoring_transition の my_unknown と同じ式)、無ければ選出のうち未判明の数 (pick_state の unknown_count。
    未判明の個体は生存として数える)"""
    remaining = (my_state or {}).get("remaining")
    if remaining is not None:
        return max(0, int(remaining) - int(known_alive))
    ps = ps if ps is not None else pick_state(my_state)
    return int(ps.unknown_count)
