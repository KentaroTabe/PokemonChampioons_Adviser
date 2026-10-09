"""相手枠のタイプアイコンの読みの安定判定と、対応待ちの個体の候補の判定 (純粋。画像処理は vision/extractors と vision/typeicons)。

2026-10-09 (fix/type-recognition、KNOWN_ISSUES A3 の 10/9 の 2 行。ユーザー判断 (レビュー済み)):
- 同じタイプが 2 つ並ぶ読み ([ノーマル, ノーマル] 等) は採用しない。重複を消して単タイプにせず、不整合として読み直しに回す
- タイプは一度入っても読み直す。同じ読みが TYPE_READ_STABLE_FRAMES 回続いたら「安定して確定」、確定後も違う読みが同じ回数
  続いたら訂正する。1 回目の読みは仮に入れる (表示と視覚照合の候補のため)
- 事前確率だけの無照合採用 (候補が実質 1 種) は、タイプが安定して確定した枠に限る。事前確率 1.0 は「タイプを正しく読めた確率」
  ではない (10/9 2 戦目: サーナイト [エスパー/フェアリー] を [ノーマル/フェアリー] と読み、候補 プクリン だけで照合なしに採用)
- タイプを訂正したら、それに依存した未確定の種族推定 (species_guess) を取り消して再計算する。手動確定・場で確認した種は保持する
- 対応待ちの個体は、タイプが 1 個だけ重なる枠が 1 つしか無くても自動で統合しない (候補として出し、人が枠を指定する)

欄の読み (cell) は (状態, タイプ) の組。状態は CELL_OK (タイプが読めた) / CELL_EMPTY (アイコンが無い) / CELL_UNREADABLE
(アイコンはあるが読めない)。単タイプのアイコンの位置は画面で違う: 選出画面は 2 つ目 (右) の欄、様子を見る画面は 1 つ目 (左) の欄
"""
from __future__ import annotations

from typing import Iterable, Optional

CELL_OK = "ok"
CELL_EMPTY = "empty"
CELL_UNREADABLE = "unreadable"

READ_OK = "ok"                    # タイプとして読めた
READ_NONE = "none"                # どちらの欄にもアイコンが無い (画面の切り替わり・未表示)
READ_INCOMPLETE = "incomplete"    # 読めない欄がある / 単タイプのアイコンが想定と逆の欄にある
READ_INCONSISTENT = "inconsistent"   # 同じタイプが 2 つ並ぶ (ありえない)

SINGLE_COL_SELECTION = 1   # 選出画面: 単タイプは 2 つ目の欄
SINGLE_COL_WATCH = 0       # 様子を見る画面: 単タイプは 1 つ目の欄

ACTION_ADOPT = "adopt"        # タイプの無い枠に入れる (仮。安定していれば確定)
ACTION_CONFIRM = "confirm"    # 入っているタイプと同じ読みが安定した
ACTION_CORRECT = "correct"    # 入っているタイプと違う読みが安定した → 訂正する


def cell_of(type_name: Optional[str], std: Optional[float], empty_std_max: float) -> tuple:
    """欄の分類の結果 → (状態, タイプ)。タイプが読めれば CELL_OK。読めず、濃淡のばらつきが empty_std_max 未満 (または切り出せない)
    なら CELL_EMPTY、それ以上なら CELL_UNREADABLE (アイコンはあるが読めない。単タイプの空の欄と区別する)"""
    if type_name:
        return CELL_OK, type_name
    if std is None or std < empty_std_max:
        return CELL_EMPTY, None
    return CELL_UNREADABLE, None


def normalize_reading(cells, single_col: int) -> tuple:
    """2 つの欄の読み → (READ_*, タイプの list or None)。
    - 読めない欄がある → READ_INCOMPLETE (仮にも入れない。次のフレームを待つ)
    - 両方空 → READ_NONE
    - 両方読めて同じタイプ → READ_INCONSISTENT (重複を消して単タイプにしない)
    - 1 つだけ読めた: 単タイプの欄 (single_col) なら単タイプ、逆の欄なら READ_INCOMPLETE (もう一方の取りこぼし)"""
    (s1, t1), (s2, t2) = cells
    if CELL_UNREADABLE in (s1, s2):
        return READ_INCOMPLETE, None
    if s1 == CELL_EMPTY and s2 == CELL_EMPTY:
        return READ_NONE, None
    if s1 == CELL_OK and s2 == CELL_OK:
        if t1 == t2:
            return READ_INCONSISTENT, [t1, t2]
        return READ_OK, [t1, t2]
    only = 0 if s1 == CELL_OK else 1
    if only != single_col:
        return READ_INCOMPLETE, None
    return READ_OK, [t1 if only == 0 else t2]


def update_streak(streak: Optional[dict], verdict: str, types: Optional[list]) -> dict:
    """読みの連続 {"cand": タイプの tuple or None, "n": 連続回数} を 1 フレームぶん進める (新しい dict を返す)。
    READ_OK: 前と同じ読み (集合として) なら n + 1、違えば 1 から。READ_INCONSISTENT: 連続を切る。それ以外 (空・読めない) は保つ"""
    cur = dict(streak or {"cand": None, "n": 0})
    if verdict == READ_OK:
        t = tuple(types)
        if cur["cand"] is not None and set(cur["cand"]) == set(t):
            cur["n"] += 1
        else:
            cur["cand"], cur["n"] = t, 1
    elif verdict == READ_INCONSISTENT:
        cur["cand"], cur["n"] = None, 0
    return cur


def type_reread_action(cur_types, stable: bool, streak: Optional[dict], n_required: int, locked: bool) -> Optional[str]:
    """読みの連続から、枠のタイプへの操作を決める。locked = 種が確定済み (手動確定・場で確認) で図鑑のタイプが正しい枠。
    戻り値: ACTION_ADOPT (タイプの無い枠に入れる) / ACTION_CONFIRM / ACTION_CORRECT / None"""
    if locked or not streak or streak.get("cand") is None:
        return None
    cand, n = streak["cand"], int(streak.get("n") or 0)
    if not cur_types:
        return ACTION_ADOPT
    if set(cand) == set(cur_types):
        return ACTION_CONFIRM if (not stable and n >= n_required) else None
    return ACTION_CORRECT if n >= n_required else None


def is_stable(streak: Optional[dict], n_required: int) -> bool:
    return bool(streak and streak.get("cand") is not None and int(streak.get("n") or 0) >= n_required)


def species_locked(slot) -> bool:
    """タイプを読み直さない枠か: 種が確定済み (推定でない = 手動確定・場に出て名前で確認) なら図鑑のタイプが正しい
    (backfill_player_static が図鑑で正す)。対戦中のタイプ変化を観測した枠も触らない"""
    return bool((slot.species_ja or slot.species_id) and not slot.species_guess) or bool(getattr(slot, "type_changed", False))


def watch_rows_agree(rows: list, family_types_of) -> bool:
    """様子を見る画面の相手の列の並びが枠の並びと一致しているかの確認。rows = [(読んだタイプの list or None,
    枠の確定済みの種族 id or None)]。確定済みの種がある行で、読みがその種 (メガの前後を含む) のどのタイプの組とも合わなければ
    False (並びが違う疑い。そのフレームの読みを使わない)。family_types_of(species_id) → [タイプの集合, ...]"""
    for types, sid in rows:
        if not types or not sid:
            continue
        fam = [set(t) for t in (family_types_of(sid) or []) if t]
        if fam and set(types) not in fam:
            return False
    return True


def watch_loose_matches(strict_cells, loose_cells, pending_type_sets, single_col: int = SINGLE_COL_WATCH) -> bool:
    """様子を見る画面の行で、確かな読み (strict) では決まらず、弱い読み (loose: 形状の照合が WATCH_TYPE_STRICT_HASH 超) が対応待ちの
    個体のタイプ (メガの前後を含む) のどれかと一致するか。一致しても枠のタイプは訂正しない (対応待ちに合わせた裏づけで、独立した
    根拠ではない)。その枠を対応待ちの個体の「候補」に加えるだけで、確定は人が枠を指定する (2026-10-09 レビューの方針)"""
    v, _t = normalize_reading(strict_cells, single_col)
    if v != READ_INCOMPLETE:
        return False
    v2, t2 = normalize_reading(loose_cells, single_col)
    return v2 == READ_OK and any(set(t2) == set(s) for s in pending_type_sets or [] if s)


def partial_match_slots(pending_type_sets: Iterable, slots: Iterable) -> list:
    """対応待ちの個体の型 (形態ごとのタイプの集合の list) と 1 個以上タイプが重なるが完全には一致しない枠の番号 (純粋)。
    slots = [(枠の番号, タイプの list)] (呼び出し側で、種が未確定・技未判明・非ひんしの枠に絞る)"""
    sets = [set(t) for t in pending_type_sets if t]
    out = []
    for i, types in slots:
        st = set(types or [])
        if not st or not sets:
            continue
        if any(st == s for s in sets):
            continue   # 完全一致は自動の規則 (match_slot) の範囲
        if any(st & s for s in sets):
            out.append(i)
    return out
