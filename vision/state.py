"""バトル状態スキーマ v2。

ポケモンチャンピオンズ(シングル)の対戦状況を表す。画面から抽出できる/
メッセージから推論できる全要素を保持する。

- FieldState: 場全体 (天候/フィールド/トリックルーム/じゅうりょく)
- SideState: 片側の陣営 (設置技/壁/おいかぜ/パーティ/場に出ているポケモン)
- PokemonState: 個々のポケモン
- BattleStateV2: 全体 + シーン情報 + イベントログ
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from typing import Optional

from champions_agent.config import (BSS_PICK_COUNT, MY_HP_TRACE_DUMP_STREAK, MY_HP_TRACE_LEN, MY_HP_TRACE_MAX_DUMPS,
                                    PARTY_SIZE, SAME_NAME_FORM_SUFFIXES, SELECTION_GUESS_REPLACE_MARGIN)

STAT_KEYS = ("atk", "def", "spa", "spd", "spe", "acc", "eva")

MAJOR_STATUSES = ("poison", "toxic", "burn", "paralysis", "sleep", "freeze", "drowsy")

# --- HP の読みの棄却の記録 (2026-10-09、KNOWN_ISSUES A1 の 10/8 の行) ---
# 抽出 (vision/extractors) が HP の読みを捨てたとき、その理由と観測を残す。理由の名前は既存の分岐に 1 つずつ付けた:
#   name_mismatch               相手: HUD の名前が読めたが、場の個体の既知の名前と類似度が閾値未満
#                               (name_kind: other_member = 相手の別の枠の名前と読めた / other_species = 図鑑の別の種族名と読めた /
#                                unreadable = どれとも合わない (判読不能、または未知の名前))
#   name_unreadable_big_change  相手: 名前が読めず、前の値からの変化が大きい (または HP 未知)
#   fraction_unparsable         自分: HP の文字が分数として読めない (最大 HP が 50 未満の読みを含む)
#   max_hp_mismatch             自分: 最大 HP が基準 (型登録の理論値 / 実測採用値 / 過去の読み) と合わない
#   max_hp_implausible          自分: 基準なし・種族判明済みで、図鑑の物理可能域に無い最大 HP
#   max_hp_not_in_team          自分: 基準なし・種族未特定 (または登録済み) で、チームの理論最大 HP の集合に無い
#   cur_over_max                自分: 現在値が最大値を超える
#   bar_mismatch                自分: 分数の割合とバーの塗りの割合の差が許容 (HP_BAR_MATCH_TOL) を超える
#   watch_right_big_increase    相手: 様子見画面の右列の HP% が、交代の文言なしに WATCH_OPP_BIG_INCREASE を超えて増えた
#                               (2026-10-09 fix/hp-paths。書かずに残す。row / species / score は右列の行と同定の結果)
# 状態には側ごとに「最後に捨てた読み」1 件だけを持つ (同じ理由・同じ候補値・同じ枠の連続は 1 件にまとめ、n と t_first で残す)。
# 件数は起動からの累計をモジュールに持つ (reset_battle で消えない。server の 5 秒統計の行に出す)
HP_REJECT_REASONS = ("name_mismatch", "name_unreadable_big_change", "fraction_unparsable", "max_hp_mismatch",
                     "max_hp_implausible", "max_hp_not_in_team", "cur_over_max", "bar_mismatch",
                     "watch_right_big_increase")
HP_REJECT_COUNTS: dict = {}   # {(side, reason): 件数}


def merge_hp_reject(prev: Optional[dict], rec: dict) -> dict:
    """直前の棄却の記録 prev に新しい記録 rec を重ねる (純粋)。

    理由・候補値・枠が prev と同じなら 1 件にまとめ (n を数え、t_first は最初の時刻のまま、他の欄は最新)、違えば rec で置き換える"""
    out = dict(rec)
    if prev and all(prev.get(k) == rec.get(k) for k in ("reason", "hp_candidate", "slot")):
        out["n"] = int(prev.get("n") or 1) + 1
        out["t_first"] = prev.get("t_first", prev.get("t"))
    else:
        out["n"] = 1
        out["t_first"] = rec.get("t")
    return out


# --- 自分の HP の読みの経過の記録 (2026-10-09 fix/hp-paths。オオニューラ 4/159 の取りこぼしの原因追跡用) ---
# extract_my_hud (source "hud") と extract_field_hp の自分側 (source "field") が、フレームごとに 1 行を state.my_hp_trace に足す
# (直近 MY_HP_TRACE_LEN 件)。decision:
#   commit / pending_stable     _set_hp に渡した読みを確定した / 安定待ちで保留した (pending の詳細は new / stable_count / since_commit)
#   HP_REJECT_REASONS の自分側   読みを捨てた (理由は既存の棄却の記録と同じ名前)
#   set_hp_*, max_split_guess, max_vote_mismatch, fainted_reread   _set_hp の中で捨てた (mon._hp_set_result)
#   estimate                    分数は使えず、バー推定の値を _set_hp に渡した (reason に捨てた理由)
#   skip_no_my_hud / no_text    自分の HUD が無い (my_hp_bar の画素が閾値以下) / HP の文字が読めない (どちらも数え上げに入れない)
MY_HP_TRACE_ACCEPT = ("commit", "pending_stable")
MY_HP_TRACE_NEUTRAL = ("skip_no_my_hud", "no_text")


def my_hp_reject_streak(prev: int, decision: str) -> int:
    """自分の HP の「確定も保留もしない読み」の連続数を更新する (純粋)。確定・保留で 0、HUD が無い・文字が無いフレームは数えない"""
    if decision in MY_HP_TRACE_ACCEPT:
        return 0
    if decision in MY_HP_TRACE_NEUTRAL:
        return int(prev)
    return int(prev) + 1


def format_hp_reject_counts(counts: dict) -> str:
    """棄却の件数 {(side, reason): n} → server の統計の行の短い文言 (純粋)。例: 「HP棄却 相手[name_mismatch=3] 自分[bar_mismatch=12]」"""
    if not counts:
        return "HP棄却=0"
    parts = []
    for side, label in (("player", "自分"), ("opponent", "相手")):
        items = sorted(((r, n) for (sd, r), n in counts.items() if sd == side and n), key=lambda kv: (-kv[1], kv[0]))
        if items:
            parts.append(f"{label}[" + " ".join(f"{r}={n}" for r, n in items) + "]")
    return "HP棄却 " + " ".join(parts) if parts else "HP棄却=0"


def _dex_types_ja_of(species_id: Optional[str]) -> Optional[set]:
    """種族IDの図鑑タイプ (日本語集合)。図鑑が引けない場合は None"""
    if not species_id:
        return None
    try:
        from advisor.dex import get_dex
        from advisor.engine import type_ja2en
        sp = get_dex().species(species_id)
        if not sp:
            return None
        en2ja = {v: k for k, v in type_ja2en().items()}
        return {en2ja.get(t, t) for t in sp["types"]}
    except Exception:
        return None


def same_name_forms_of(species_id: Optional[str], all_ids, illegal, suffixes=SAME_NAME_FORM_SUFFIXES) -> list:
    """名前が同じで形態が違う種の候補 (純粋)。素の id と、図鑑にある「素の id + 地方の接尾辞」の id (参戦外は除く)。
    素の id が既に地方の形態 (slowkinggalar 等) なら候補はそれだけ。例: slowking → [slowking, slowkinggalar]"""
    if not species_id:
        return []
    out = [species_id]
    for suf in suffixes:
        f = species_id + suf
        if f in all_ids and f not in illegal and f not in out:
            out.append(f)
    return out


@lru_cache(maxsize=1024)
def same_name_forms(species_id: Optional[str]) -> tuple:
    """same_name_forms_of を図鑑 (advisor.dex) と参戦外の id (vision.normalize) で引く。引けなければ (species_id,)"""
    if not species_id:
        return ()
    try:
        from advisor.dex import get_dex
        from vision.normalize import champions_illegal_ids
        return tuple(same_name_forms_of(species_id, set(get_dex().species_ids()), champions_illegal_ids()))
    except Exception:
        return (species_id,)


def mega_family_of(species_id: Optional[str], all_ids) -> list:
    """メガシンカの前後の種族 id (純粋)。species_id 自身、メガ形態なら素の種 (図鑑にあるとき)、素の種のメガ形態 (末尾 mega /
    megax / megay / megaz)。例: gardevoirmega → [gardevoirmega, gardevoir]、charizard → [charizard, charizardmegax, charizardmegay]
    (2026-10-09: 対応待ちの個体の照合で、場の個体がメガ後でも選出画面のタイプ (メガ前) の枠に当てるため)"""
    if not species_id:
        return []
    import re
    out = [species_id]
    m = re.match(r"^(.+)mega[xyz]?$", species_id)
    base = m.group(1) if (m and m.group(1) in all_ids) else species_id
    if base != species_id:
        out.append(base)
    for x in sorted(all_ids):
        if x not in out and x.startswith(base) and re.fullmatch(r"mega[xyz]?", x[len(base):]):
            out.append(x)
    return out


@lru_cache(maxsize=1024)
def mega_family(species_id: Optional[str]) -> tuple:
    """mega_family_of を図鑑 (advisor.dex) で引く。引けなければ (species_id,)"""
    if not species_id:
        return ()
    try:
        from advisor.dex import get_dex
        return tuple(mega_family_of(species_id, set(get_dex().species_ids())))
    except Exception:
        return (species_id,)


def _same_family(a: Optional[str], b: Optional[str]) -> bool:
    """種族 id が同じ種の形態違い (同じ id、または「素の id + 地方の接尾辞 / メガ」: slowking / slowkinggalar) か (純粋)。
    単なる前方一致 (mew / mewtwo) は同じ種にしない"""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if not long_.startswith(short):
        return False
    rest = long_[len(short):]
    return rest in SAME_NAME_FORM_SUFFIXES or rest.startswith("mega")


def is_pending(p) -> bool:
    """対応待ちの個体か (純粋)。PokemonState / state.to_dict の要素 / 対戦ログの簡約 (scene 行) のどれでも読む"""
    if p is None:
        return False
    return bool(p.get("pending") if isinstance(p, dict) else getattr(p, "pending", False))


def roster_slots(party, active_index: Optional[int] = None) -> list:
    """相手の「枠」として数える要素 [(枠の番号, 要素)] (純粋。2026-10-09 ④)。先頭 PARTY_SIZE 枠のうち対応待ち (pending)
    でないもの。種・枠・選出・残り体数・ひんし数を数える箇所はすべてこれを通す (対応待ちの個体 = party の 7 番目以降は
    6 体のどれかで、枠が決まっていないだけ。数に入れると相手が 7 体になる)。
    active_index を渡すと、場の個体が対応待ちのときに限りそれも含める (助言エンジンが場の個体として使う用途。数を
    数える用途では渡さない)。PokemonState / state.to_dict の要素 / 対戦ログの簡約のどれでも読む"""
    party = list(party or [])
    out = [(i, p) for i, p in enumerate(party[:PARTY_SIZE]) if not is_pending(p)]
    if isinstance(active_index, int) and PARTY_SIZE <= active_index < len(party) and is_pending(party[active_index]):
        out.append((active_index, party[active_index]))
    return out


def has_observation(p) -> bool:
    """場で観測した情報 (HP・技・状態異常) を持つ枠か (選出画面の推定・タイプアイコンは観測に数えない)"""
    return bool(p.hp_percent is not None or p.revealed_moves or p.moves or p.status)


@dataclass
class MoveSlot:
    name_ja: str = ""
    move_id: Optional[str] = None      # showdown形式ID (例: dragonpulse)
    pp: Optional[int] = None
    max_pp: Optional[int] = None
    effectiveness: Optional[str] = None  # super2x超='super_extreme'|'super'|'neutral'|'resist'|'immune'

    def to_dict(self):
        return asdict(self)


@dataclass
class PokemonState:
    # 識別
    species_ja: Optional[str] = None    # 種族名 (日本語)
    species_id: Optional[str] = None    # showdown形式ID
    display_name: Optional[str] = None  # 画面上の表示名 (ニックネーム/外国語含む)
    gender: Optional[str] = None        # 'M' / 'F' / None
    level: int = 50                     # ランクバトルは50固定

    # タイプ (日本語表記のリスト。フォームチェンジで変わり得る)
    types: list = field(default_factory=list)

    # HP
    hp_percent: Optional[float] = None  # 0-100
    hp_current: Optional[int] = None    # 自分側のみ実数値が見える
    hp_max: Optional[int] = None
    # 交代の取り逃し等でHPが古い可能性がある印 (新しい読みで解除される)。
    # 2026-08-18: ひんしを取り逃した個体が100%のまま交代候補に推奨された
    hp_uncertain: bool = False
    # HPの鮮度 (2026-09-05): 最後に実読みで確定した時刻と、技イベントからの
    # 期待ダメージで推定した値かどうか (実読みが来れば False に戻る)
    hp_read_ts: Optional[float] = None
    hp_estimated: bool = False
    # 推定値の出所 (2026-10-09): "bar" = 自分の HUD の分数が読めない間に、既知の最大 HP × バーの割合で入れた概算
    # (vision.my_hp_estimate。hp_estimated / hp_uncertain も True)。実読みで更新されると None に戻る。
    # 技イベント由来の推定 (vision.events、既定 OFF) は従来どおり hp_estimated だけを立て、ここは None のまま
    hp_source: Optional[str] = None

    # 状態
    status: Optional[str] = None        # MAJOR_STATUSES のいずれか / 'fainted' / None
    volatiles: list = field(default_factory=list)  # confusion, substitute, leechseed, ...
    boosts: dict = field(default_factory=lambda: {k: 0 for k in STAT_KEYS})

    # 判明している情報
    ability_ja: Optional[str] = None
    ability_id: Optional[str] = None
    item_ja: Optional[str] = None
    item_id: Optional[str] = None
    item_consumed: bool = False
    moves: list = field(default_factory=list)      # list[MoveSlot] 自分側: 画面から確定
    revealed_moves: list = field(default_factory=list)  # 相手側: 使用を目撃した技 (日本語)

    # この試合中に観測されたこの個体の別名 (OCR揺れの表示名キャッシュ)。
    # 手動確定や類似照合の際に蓄積し、以後のイベント帰属に使う
    aliases: list = field(default_factory=list)

    is_mega: bool = False
    is_active: bool = False
    is_picked: bool = False          # 選出画面で選出済み (左端の白リボン)
    pick_order: Optional[int] = None  # 選出順 (1=先発。リボン出現順で推定)
    last_seen_ts: float = 0.0
    # 対戦中のタイプ変化 (へんげんじざい/みずびたし等) を観測済みか。
    # True の間は図鑑タイプによる自動訂正 (backfill) を抑止する
    # (2026-08-21: マスカーニャの変幻自在が数秒で図鑑タイプに戻されていた)。
    # タイプ変化は交代で戻るため、交代アウトでフラグを解除し以後は図鑑で正す
    type_changed: bool = False
    # はたきおとす等で持ち物を失った (対戦中は復活しない)。True の間は
    # 登録バックフィル・使用率予測による持ち物の再設定を抑止する
    # (2026-08-21 第7回: はたき後も持ち物を保持したまま計算していた)
    item_removed: bool = False
    # 選出画面の推定 (タイプアイコン + スプライト照合) で入れた種族か。True の間は「相手の 6 体」として数えず
    # (分析・実戦バンク)、場に出た種が来たら置き換えてよい。名前が読めた/場に出た時点で確定 (False) に上書きされる
    # (2026-09-29 第17回: 推定の カイリュー が 2 枠に入り、実体は セグレイブ。表示と集計に他の対戦の顔ぶれが混ざって見えた)
    species_guess: bool = False
    guess_score: Optional[float] = None   # 推定時の視覚照合スコア (同種の重複でどちらを残すか)
    # 対応待ち (2026-10-09 ④): 満枠で場に出た種の対応先の枠を根拠をもって決められないとき、枠を壊さずに party の末尾
    # (PARTY_SIZE 以降) に置く場の個体。後の観測 (タイプ・形態の訂正・手動確定) で対応が決まった時点で枠へ移す
    # (SideState.resolve_pending)。(10/8 18:27: 対応先が無い「ヤドキング」が推定スコア最低の別の推定の枠を上書きした)
    pending: bool = False
    # 名前が複数の形態に当たる (ヤドキング = slowking / slowkinggalar) とき、形態が決まるまでの候補の種族 id。決まったら空
    species_candidates: list = field(default_factory=list)
    t_first_seen: Optional[float] = None   # 対応待ちになった時刻

    def merge_species(self, species_ja: str, species_id: Optional[str], guess: bool = False,
                      score: Optional[float] = None):
        """種族を設定する。guess=True は選出画面の推定 (確定ではない)。既定 (確定) は推定の印を消す"""
        self.species_ja = species_ja
        if species_id:
            self.species_id = species_id
        self.species_guess = bool(guess)
        self.guess_score = float(score) if (guess and score is not None) else None

    def clear_species_guess(self):
        """推定の種族を取り消す (タイプアイコン由来のタイプは残す)"""
        self.species_ja, self.species_id = None, None
        self.species_guess, self.guess_score = False, None

    def set_boost(self, stat: str, delta: int):
        if stat in self.boosts:
            self.boosts[stat] = max(-6, min(6, self.boosts[stat] + delta))

    def reset_on_switch_out(self):
        """交代で消える揮発性状態をリセット"""
        self.boosts = {k: 0 for k in STAT_KEYS}
        keep = {"leechseed"}  # やどりぎも交代で消えるが表記簡略化のため消す
        self.volatiles = []
        self.is_active = False
        # タイプ変化は交代で元に戻る。フラグを解除すると次のbackfillが
        # 図鑑タイプへ正してくれる (typesをここで直接触ると図鑑参照が要る)
        self.type_changed = False

    def to_dict(self):
        d = asdict(self)
        d["moves"] = [m if isinstance(m, dict) else asdict(m) for m in
                      (self.moves or [])]
        return d


def adopt_selection_guess(party: list, idx: int, species_id: str, species_ja: str, score: float,
                          margin: float = SELECTION_GUESS_REPLACE_MARGIN) -> str:
    """選出画面の推定 (タイプアイコン + スプライト照合) を枠 idx に入れる (純粋)。
    同じ種が別枠に既にあるとき (同種 2 体はルール上あり得ない): 別枠が推定でスコアが margin 以上低ければそちらを
    取り消して入れる ("replaced")、そうでなければ入れない ("skip")。戻り値: "adopt" / "replaced" / "skip"
    (2026-09-29 第17回: 3 戦でセグレイブがカイリューと推定され、実物のカイリューと 2 枠になった)"""
    slot = party[idx]
    dup = next((j for j, q in enumerate(party) if j != idx and q.species_id == species_id), None)
    if dup is not None:
        q = party[dup]
        if q.species_guess and (q.guess_score or 0.0) + margin < score:
            q.clear_species_guess()
            slot.merge_species(species_ja, species_id, guess=True, score=score)
            return "replaced"
        return "skip"
    slot.merge_species(species_ja, species_id, guess=True, score=score)
    return "adopt"


def apply_manual_species(party: list, idx: int, species_ja: str, species_id: Optional[str]) -> dict:
    """手動確定 (候補のプルダウン / 手入力) を相手の枠に入れる (純粋)。
    戻り値 {"index": 入れた枠 (入れなければ None), "moved": 別の枠へ付け替えたか, "cleared": 取り消した別枠の推定の index,
    "reason": 入れなかった理由 (入れたら None)}

    - 対象枠が未確定、または選出画面の推定 (species_guess) → その枠に入れる。推定は確定ではないので手入力で上書きできる
      (2026-10-06 第18回接続テスト: 推定の枠が「確定済み」と扱われ、6 枠とも推定で埋まっていて付け替え先も無く、推定の
      カイリュー / スターミー を直そうとした手入力が 9 回続けて無視された)
    - 対象枠が同じ種で確定済み → そのまま (入れ直しても同じ)
    - 対象枠が別の種で確定済み → 未確定の枠へ付け替える (プルダウンの描画から選択までの間に、対象枠が別フレームで自動確定
      されることがある。2026-08-20)。未確定の枠が無ければ入れない
    - 入れる種が別の枠にもあるとき (同種 2 体はルール上あり得ない): 別の枠が確定済みなら入れない、推定ならそちらを取り消す
    - 対象が対応待ちの個体 (pending) → その個体の種 (形態) を確定する。どの枠に当たるかは呼び出し側が
      SideState.resolve_pending で決める (2026-10-09 ④)
    - 対象枠が同じ種の別の形態 (ヤドキング / ガラルヤドキング) で確定済み → その枠の形態を直す (同じ個体)
    - 対応待ちの個体は「入れる種が別の枠にもある」の判定に使わない (枠が決まっていないので、手動確定が対応を決める側)
    """
    out = {"index": None, "moved": False, "cleared": [], "reason": None}
    if not (0 <= idx < len(party)):
        out["reason"] = MANUAL_REASON_OUT_OF_RANGE
        return out
    slot = party[idx]
    if slot.pending:
        _merge_manual(slot, species_ja, species_id)
        out["index"] = idx
        return out
    target = idx
    if slot.species_ja and not slot.species_guess and slot.species_ja != species_ja \
            and not _same_family(slot.species_id, species_id):
        target = next((j for j, p in enumerate(party) if not p.species_ja), None)
        if target is None:
            out["reason"] = f"slot{idx} は {slot.species_ja} で確定済み (未確定の枠なし)"
            return out
        out["moved"] = True

    def _same(q) -> bool:
        return q.species_ja == species_ja or bool(species_id and q.species_id == species_id)

    others = [(j, q) for j, q in enumerate(party) if j != target and q.species_ja and not q.pending and _same(q)]
    fixed = next((j for j, q in others if not q.species_guess), None)
    if fixed is not None:
        out["reason"] = f"{species_ja} は slot{fixed} で確定済み"
        return out
    for j, q in others:
        q.clear_species_guess()
        out["cleared"].append(j)
    _merge_manual(party[target], species_ja, species_id)
    out["index"] = target
    return out


def _merge_manual(p, species_ja: str, species_id: Optional[str]) -> None:
    """手動確定の種を入れる。既に同じ種の形態違い (ヤドキング ← ガラルヤドキング) が入っている確定済みの枠・対応待ちの
    個体は、表示名 (species_ja = 交代の文言・HUD の名前) を保って id とタイプだけ直し、入力の名前を別名に足す
    (判明技による形態の訂正 events._maybe_correct_form と同じ扱い。名前が変わると集計で同じ個体が 2 種に見える)"""
    if p.species_ja and not p.species_guess and species_id \
            and _same_family(p.species_id, species_id):
        p.species_id = species_id
        t = _dex_types_ja_of(species_id)
        if t:
            p.types = list(t)
        if species_ja and species_ja != p.species_ja and species_ja not in (p.aliases or []):
            p.aliases.append(species_ja)
    else:
        p.merge_species(species_ja, species_id)
    p.species_candidates = []


MANUAL_REASON_OUT_OF_RANGE = "枠の番号が範囲外"


def apply_manual_species_unplaced(party: list, species_ja: str, species_id: Optional[str]) -> dict:
    """手動確定の枠の番号が範囲外 (表示の枠とサーバーの枠がずれた) のときの適用先を決めて入れる (純粋)。
    1) 対応待ちの個体で同じ種 (形態の候補・形態違いを含む) のもの → その個体の種を確定する
    2) 種族名 (形態違いを含む) で一致する枠 → apply_manual_species と同じ規則でその枠に入れる
    どちらにも当たらなければ入れない。戻り値は apply_manual_species と同じ形 + "via" ("pending" / "species_match" / None)
    (2026-10-09 ④: 10/8 18:27 に手動確定「ガラルヤドキング」が「枠の番号が範囲外」で無視された)"""
    def _hits(q) -> bool:
        if q.species_ja == species_ja:
            return True
        ids = [q.species_id, *(q.species_candidates or [])]
        return any(species_id and (i == species_id or _same_family(i, species_id)) for i in ids if i)

    pend = [j for j, q in enumerate(party) if q.pending and _hits(q)]
    if len(pend) == 1:
        res = apply_manual_species(party, pend[0], species_ja, species_id)
        res["via"] = "pending"
        return res
    slots = [j for j, q in roster_slots(party) if q.species_ja and _hits(q)]
    if len(slots) == 1:
        res = apply_manual_species(party, slots[0], species_ja, species_id)
        res["via"] = "species_match"
        return res
    why = "対応待ちの個体が複数当たる" if len(pend) > 1 else (
        "同じ種の枠が複数ある" if len(slots) > 1 else "対応待ちの個体にも同じ種の枠にも当たらない")
    return {"index": None, "moved": False, "cleared": [], "reason": f"{MANUAL_REASON_OUT_OF_RANGE} ({why})",
            "via": None}


@dataclass
class SideState:
    trainer_name: Optional[str] = None
    party: list = field(default_factory=list)     # list[PokemonState] 最大6
    active_index: Optional[int] = None
    remaining: Optional[int] = None               # 残りポケモン数 (ボールアイコン)
    selected_count: int = 3

    # 場の効果 (自陣営側)
    stealth_rock: bool = False
    spikes: int = 0            # 0-3
    toxic_spikes: int = 0      # 0-2
    sticky_web: bool = False
    reflect: bool = False
    light_screen: bool = False
    aurora_veil: bool = False
    safeguard: bool = False
    tailwind: bool = False
    wish: bool = False
    # 満枠で場に出た種の対応先を決められないとき、対応待ちの個体として保持するか (2026-10-09 ④)。相手側だけ True
    # (BattleStateV2.__init__)。自分側は登録済みのロスターなので、満枠の初登場は誤読として扱う (従来どおり)
    hold_unplaced: bool = False

    def active(self) -> Optional[PokemonState]:
        if self.active_index is not None and 0 <= self.active_index < len(self.party):
            return self.party[self.active_index]
        return None

    def fainted_count(self, picked_only: bool = False) -> int:
        """ひんし数 (純粋)。ロスターの枠 (PARTY_SIZE) だけを数え、余剰の枠 (誤読で生えた 7 体目等) は数えない。
        picked_only=True で選出 (is_picked) が BSS_PICK_COUNT 体分かっていればその中だけを数える。
        (2026-09-29 第17回 15:53: 様子見画面の名前の誤読で生えた 7 体目 (HP 0) が 3 体目のひんしに数えられ、
        自分のガブリアスが残っているのに負けで終了扱い → 助言が止まり、勝った対戦が負けで記録された)"""
        # 対応待ちの個体 (PARTY_SIZE 以降、2026-10-09 ④) は数えない (roster_slots)。誤読の 7 体目のひんしで終了と
        # 判定した 9/29 の事故と同じ形を避ける。対応待ちの個体が 3 体目のひんしなら、終了は WIN / LOSE 画面などで取る
        roster = [p for _i, p in roster_slots(self.party)]
        if picked_only:
            picked = [p for p in roster if p.is_picked]
            if len(picked) >= BSS_PICK_COUNT:
                roster = picked
        return sum(1 for p in roster if p.status == "fainted")

    def ensure_active(self) -> PokemonState:
        """場に出ているポケモンを返す。未確定ならプレースホルダを作る。

        パーティが満枠 (6) の場合は新枠を作らず、帰属不明の観測を吸収する
        使い捨て枠 (UI非表示) を返す — ロスターは試合中に増えない
        """
        mon = self.active()
        if mon is None:
            if len(self.party) < 6:
                mon = PokemonState(is_active=True)
                self.party.append(mon)
                self.active_index = len(self.party) - 1
            else:
                if getattr(self, "_limbo", None) is None:
                    self._limbo = PokemonState()
                return self._limbo
        return mon

    def find_by_species(self, species_ja: str,
                        species_id: Optional[str] = None) -> Optional[int]:
        for i, p in enumerate(self.party):
            if p.species_ja == species_ja:
                return i
        # メガ正規化フォールバック: 「メガリザードン(X/Y)」と「リザードン」は
        # 同一個体 (メガ後も画面表示は元の名前のため、表記が混在し得る)
        def base(name):
            if not name:
                return None
            if name.startswith("メガ"):
                name = name[len("メガ"):]
                name = name[:-1] if name.endswith(("X", "Y")) else name
            return name
        want = base(species_ja)
        if want:
            for i, p in enumerate(self.party):
                if base(p.species_ja) == want:
                    return i
        # 同名フォーム族フォールバック: ゲームのHUD表示はフォーム名を省く
        # (ウォッシュロトムも「ロトム」と表示される) ため、素の名前の読みが
        # 既存のフォーム個体の隣に別枠として生えないよう、名前の末尾一致
        # + 図鑑IDの前方一致の両方を満たす枠へ寄せる (2026-08-25 第9回:
        # rotomwash の隣に rotom が生え、タイプがゴースト/でんきで表示された)。
        # ID前方一致を必須にするのは、コイル/レアコイルのような
        # 「名前は末尾一致するが別種」の誤併合を防ぐため
        if want and species_id and len(want) >= 3:
            for i, p in enumerate(self.party):
                pj = base(p.species_ja) or ""
                pid = p.species_id or ""
                if not pj or not pid or pj == want:
                    continue
                if not (pj.endswith(want) or want.endswith(pj)):
                    continue
                if pid.startswith(species_id) or species_id.startswith(pid):
                    return i
        return None

    def find_by_display_name(self, name: str) -> Optional[int]:
        for i, p in enumerate(self.party):
            if p.display_name and p.display_name == name:
                return i
            if name in (p.aliases or []):
                return i
        return None

    def switch_to(self, index: int):
        prev = self.active()
        if prev is not None:
            prev.reset_on_switch_out()
        self.active_index = index
        self.party[index].is_active = True

    def roster_eligible(self) -> list:
        """場に出た種の対応先にできる枠 [(i, 枠)]: ロスターの枠 (PARTY_SIZE 枠まで、対応待ちを除く) のうち
        非アクティブ・技未判明・非ひんし"""
        return [(i, p) for i, p in roster_slots(self.party)
                if i != self.active_index and not p.revealed_moves and p.status != "fainted"]

    def match_slot(self, cands: list) -> tuple:
        """場に出た種の対応先の枠を、根拠のある規則だけで決める (純粋)。cands = 形態の候補 [(species_id, 図鑑タイプの集合
        または None)] (形態が 1 つなら 1 要素)。戻り値 (枠, 形態の id) — 枠が決まらなければ (None, None)、枠は決まったが
        形態が決まらなければ形態は None。優先順:
        1) 選出画面の推定 (species_guess) で同じ種が 2 枠以上ある重複のうちスコアが低い枠 (同種 2 体はルール上あり得ない。
           2026-09-29 第17回: セグレイブ が カイリュー と推定され、実物の カイリュー と 2 枠になった)
        2) 図鑑タイプが一致する推定枠 / 未特定枠、次いでタイプが一致する枠。形態の候補が複数のときは、候補のどれかと
           タイプが一致する枠が 1 つだけのときに限る (その形態に決まる)。2 つ以上なら 2) では決めない
        4) 未特定枠 (種もタイプも無い枠)
        2026-10-09 ④: 従来の 3) 推定枠のうち視覚照合スコアが最も低いもの / 5) それ以外の候補の先頭 は、対応の根拠が無いので
        使わない (10/8 18:27: タイプが一致する枠が無い「ヤドキング」が、正しかったかもしれない推定 ムクホーク の枠を上書きした)。
        決まらなければ呼び出し側が対応待ち (pending) として枠を壊さずに保持する"""
        elig = self.roster_eligible()
        if not elig:
            return None, None
        # 形態の候補が複数か (同じ形態のメガの前後のタイプを並べた候補は 1 つと数える。2026-10-09)
        multi = len({sid for sid, _t in cands}) > 1
        counts: dict = {}
        for _i, p in roster_slots(self.party):
            if p.species_id:
                counts[p.species_id] = counts.get(p.species_id, 0) + 1
        dups = [((p.guess_score or 0.0), i) for i, p in elig
                if p.species_guess and counts.get(p.species_id, 0) >= 2]
        if dups:
            i = min(dups)[1]
            return i, self._form_by_slot_types(self.party[i], cands)
        typed = [(sid, t) for sid, t in cands if t]
        multi_types = len({frozenset(t) for _sid, t in typed}) > 1
        if typed:
            for only_unconfirmed in (True, False):
                hits = [(i, sid) for i, p in elig for sid, t in typed
                        if p.types and set(p.types) == set(t) and
                        (not only_unconfirmed or p.species_guess or not p.species_ja)]
                if not hits:
                    continue
                if not multi and not multi_types:
                    return hits[0][0], hits[0][1]
                slots = {i for i, _ in hits}
                if len(slots) == 1:
                    return hits[0][0], hits[0][1]
                break   # 形態ごとに別の枠が当たる = 決められない
        for i, p in elig:
            if not p.species_ja and not p.types:
                return i, (None if multi else cands[0][0])
        return None, None

    @staticmethod
    def _form_by_slot_types(p, cands: list) -> Optional[str]:
        """形態の候補のうち、枠のタイプ (選出画面のアイコン) と図鑑タイプが一致するもの。候補が 1 つならそれ"""
        if len({sid for sid, _t in cands}) == 1:   # 形態が 1 つ (メガの前後のタイプを並べた候補を含む)
            return cands[0][0]
        hit = [sid for sid, t in cands if t and p.types and set(p.types) == set(t)]
        return hit[0] if len(hit) == 1 else None

    def replacement_slot(self, new_types: Optional[set]) -> Optional[int]:
        """満枠で初登場の種 (形態が 1 つ) が来たとき置き換える枠 (純粋。match_slot の枠だけを返す)。決まらなければ None"""
        return self.match_slot([(None, new_types)])[0]

    def switch_to_species(self, species_ja: str, species_id: Optional[str],
                          now: Optional[float] = None) -> PokemonState:
        idx = self.find_by_species(species_ja, species_id)
        # 名前が複数の形態に当たる (ヤドキング = slowking / slowkinggalar) なら既定の形態に即確定しない (2026-10-09 ④)
        forms = list(same_name_forms(species_id)) if species_id else []
        ambiguous = len(forms) > 1
        if idx is None and len(self.party) >= PARTY_SIZE:
            # 満枠での「初登場」= 既存枠の視覚同定ミスが濃厚 (実測:
            # ラフレシアと誤同定した枠の実体がフシギバナで、appendにより
            # ルール上あり得ない7匹構成になった)。対応先は match_slot
            # (推定の重複 → タイプ一致 → 未特定) の根拠のある規則だけで決める
            cands = [(f, _dex_types_ja_of(f)) for f in forms] or [(species_id, _dex_types_ja_of(species_id))]
            cand, form = self.match_slot(cands)
            if cand is not None:
                p = self.party[cand]
                sid = form or species_id
                p.species_ja, p.species_id = species_ja, sid
                new_types = _dex_types_ja_of(sid) if (form or not ambiguous) else None
                p.types = list(new_types) if new_types else []
                p.species_guess, p.guess_score = False, None   # 場に出た = 確定
                p.species_candidates = list(forms) if (ambiguous and not form) else []
                idx = cand
            elif not self.hold_unplaced:
                # 対応待ちを持たない側 (自分側: ロスターは登録済みで、満枠の初登場は名前の誤読): 7 枠目を作らず、
                # 枠も置き換えずに現在のアクティブを維持する (2026-08-05 接続テスト: 誤読由来の 7 枠目が表示を汚した)
                return self.ensure_active()
            else:
                # 対応先を決められない: 既存の枠を置き換えず、対応待ちの個体として末尾 (PARTY_SIZE 以降) に置く。
                # 場の個体なので以後の HP・技はこの個体に付き、対応が決まったら resolve_pending が枠へ移す
                # (2026-10-09 ④。従来は推定スコア最低の枠などを上書きし、正しかった推定と観測情報を失っていた)
                t = None if ambiguous else _dex_types_ja_of(species_id)
                mon = PokemonState(species_ja=species_ja, species_id=species_id, types=list(t) if t else [],
                                   pending=True, species_candidates=list(forms) if ambiguous else [],
                                   t_first_seen=round(now if now is not None else time.time(), 2))
                self.party.append(mon)
                idx = len(self.party) - 1
        if idx is None:
            # 初登場 -> 一旦末尾に追加する (どの選出枠に対応するかは
            # link_active_to_party がタイプ照合で解決し、余剰枠を除去する)
            mon = PokemonState(species_ja=species_ja, species_id=species_id,
                               species_candidates=list(forms) if ambiguous else [])
            self.party.append(mon)
            idx = len(self.party) - 1
        self.switch_to(idx)
        mon = self.party[idx]
        # 同族フォーム一致 (素の名前読みが具体フォーム枠へ寄せられた場合) は、
        # 確定済みの具体フォーム (rotomwash等) を素形 (rotom) へ格下げしない
        downgrade = (mon.species_id and species_id
                     and mon.species_id != species_id
                     and mon.species_id.startswith(species_id))
        if not downgrade:
            mon.merge_species(species_ja, species_id)
        return mon

    # --- 対応待ちの個体 (2026-10-09 ④) ---
    def pending_indices(self) -> list:
        return [i for i, p in enumerate(self.party) if p.pending]

    def pending_target(self, pm: PokemonState) -> tuple:
        """対応待ちの個体 pm の対応先の枠 (純粋) → (枠, 形態の id, 根拠 "species" / "rule")。決まらなければ
        (None, None, None)。
        a) ロスターの枠に同じ種 (同名 / 形態の候補の id / 形態違い) がちょうど 1 つ (手動確定・名前の読みで枠に入った)
        b) match_slot (推定の重複 → タイプ一致 → 未特定)。形態の訂正 (判明技) で候補が絞れていればその形態のタイプで照合する"""
        forms = list(pm.species_candidates or []) or ([pm.species_id] if pm.species_id else [])
        same = []
        for i, p in roster_slots(self.party):
            if not (p.species_ja or p.species_id):
                continue
            if p.species_id and (p.species_id in forms or any(_same_family(p.species_id, f) for f in forms)):
                same.append((i, p.species_id))   # 枠の形態 (手動確定のガラル形など、より具体的なもの) を採る
            elif p.species_ja and p.species_ja == pm.species_ja:
                same.append((i, forms[0] if len(forms) == 1 else None))
        if len(same) == 1:
            return same[0][0], same[0][1], "species"
        if len(same) > 1:
            return None, None, None
        slot, form = self.match_slot(self._form_type_cands(forms))
        return slot, form, ("rule" if slot is not None else None)

    @staticmethod
    def _form_type_cands(forms: list) -> list:
        """形態の候補 → match_slot の候補 [(形態の id, タイプの集合)]。各形態について、メガの前後 (mega_family) のタイプも
        同じ形態の id で並べる (2026-10-09 段 2: 場の個体がメガ後 (gardevoirmega 等) でも、選出画面のタイプ (メガ前) の枠に
        当てる。統合後の種族 id は場の個体の形態のまま)"""
        out = []
        for f in forms:
            for g in (mega_family(f) or (f,)):
                t = _dex_types_ja_of(g)
                if t and (f, frozenset(t)) not in {(a, frozenset(b)) for a, b in out}:
                    out.append((f, t))
        return out or [(f, _dex_types_ja_of(f)) for f in forms]

    def pending_hint(self, pm: PokemonState) -> list:
        """対応待ちの個体 pm の「候補の枠」(純粋。2026-10-09 段 2、ユーザー判断)。自動の対応 (pending_target) が決まらないとき、
        種が未確定・技未判明・非ひんしの枠のうち次のものを [{"slot": 枠の番号 (0 始まり), "ja": 推定の種 or None,
        "types": 枠のタイプ, "source": 出所}] で返す。自動では統合しない (人が枠を指定する):
        - "partial_type": タイプが 1 個以上重なるが完全には一致しない枠がちょうど 1 つのとき、その枠
        - "watch_loose": 様子を見る画面の弱い読み (形状の照合が WATCH_TYPE_STRICT_HASH 超) が pm のタイプと一致した枠
          (vision/extractors._reread_watch_opp_types が pm._watch_loose_slots に入れる。枠のタイプは訂正しない)"""
        if self.pending_target(pm)[0] is not None:
            return []
        forms = list(pm.species_candidates or []) or ([pm.species_id] if pm.species_id else [])
        sets = [t for _f, t in self._form_type_cands(forms) if t]
        elig = {i: p for i, p in self.roster_eligible() if not ((p.species_ja or p.species_id) and not p.species_guess)}
        from vision.type_reading import partial_match_slots
        hits = partial_match_slots(sets, [(i, p.types) for i, p in elig.items()])
        found = [(hits[0], "partial_type")] if len(hits) == 1 else []
        for i in getattr(pm, "_watch_loose_slots", None) or []:
            if i in elig and all(i != j for j, _s in found):
                found.append((i, "watch_loose"))
        return [{"slot": i, "ja": self.party[i].species_ja if self.party[i].species_guess else None,
                 "types": list(self.party[i].types or []), "source": src} for i, src in found]

    def assign_pending(self, src_idx: int, dst_idx: int) -> dict:
        """人が対応待ちの個体 party[src_idx] の枠を dst_idx に指定して確定する (純粋。2026-10-09 段 2)。
        戻り値 {"ok", "reason" (入れなかった理由), "record" (merge_pending_into の記録、basis = "manual")}。
        枠が別の種で確定済み (推定でない・形態違いでもない) なら入れない。枠の推定・タイプは場の個体の種で置き換える"""
        if not (PARTY_SIZE <= src_idx < len(self.party)) or not self.party[src_idx].pending:
            return {"ok": False, "reason": "対応待ちの個体ではない", "record": None}
        if not (0 <= dst_idx < min(len(self.party), PARTY_SIZE)) or self.party[dst_idx].pending:
            return {"ok": False, "reason": MANUAL_REASON_OUT_OF_RANGE, "record": None}
        pm, dst = self.party[src_idx], self.party[dst_idx]
        forms = list(pm.species_candidates or []) or ([pm.species_id] if pm.species_id else [])
        if (dst.species_ja or dst.species_id) and not dst.species_guess \
                and not any(_same_family(dst.species_id, f) for f in forms) and dst.species_ja != pm.species_ja:
            return {"ok": False, "reason": f"枠 {dst_idx + 1} は {dst.species_ja} で確定済み", "record": None}
        form = None
        if len(forms) == 1:
            form = forms[0]
        elif dst.types:
            hit = [f for f, t in self._form_type_cands(forms) if t and set(t) == set(dst.types)]
            form = hit[0] if len({h for h in hit}) == 1 else None
        rec = self.merge_pending_into(src_idx, dst_idx, form)
        rec["basis"] = "manual"
        return {"ok": True, "reason": None, "record": rec}

    def merge_pending_into(self, src_idx: int, dst_idx: int, form: Optional[str]) -> dict:
        """対応待ちの個体 party[src_idx] を枠 party[dst_idx] へ移す (純粋)。観測した HP・技・状態・持ち物・特性・別名を
        枠に移し、対応待ちの個体は party から除く (末尾にあるので枠の番号はずれない)。戻り値: 対戦ログ用の記録"""
        src, dst = self.party[src_idx], self.party[dst_idx]
        replaced = {"species": dst.species_id, "ja": dst.species_ja, "guess": dst.species_guess,
                    "score": dst.guess_score, "types": list(dst.types or [])}
        keep_dst_species = bool(dst.species_id and not dst.species_guess
                                and (form is None or dst.species_id == form or _same_family(dst.species_id, form)))
        if not keep_dst_species:
            sid = form or src.species_id
            dst.species_ja, dst.species_id = src.species_ja, sid
            determined = bool(form) or len(src.species_candidates or []) <= 1
            t = _dex_types_ja_of(sid) if determined else None
            if t:
                dst.types = list(t)
            dst.species_candidates = [] if determined else list(src.species_candidates)
        else:
            # 枠の種 (手動確定のガラル形など) を保つ。表示名は場の個体の名前 (交代の文言・HUD の名前) にそろえ、枠の名前は
            # 別名に残す (名前でのイベントの帰属と、集計で同じ個体が 2 つの名前に分かれないため)
            if src.species_ja and dst.species_ja and src.species_ja != dst.species_ja:
                if dst.species_ja not in (dst.aliases or []):
                    dst.aliases.append(dst.species_ja)
                dst.species_ja = src.species_ja
            dst.species_candidates = []
        dst.species_guess, dst.guess_score = False, None
        for attr in ("display_name", "gender", "hp_percent", "hp_current", "hp_max", "hp_read_ts", "status",
                     "ability_ja", "ability_id", "item_ja", "item_id"):
            val = getattr(src, attr)
            if val is not None:
                setattr(dst, attr, val)
        for attr in ("hp_uncertain", "hp_estimated", "item_consumed", "item_removed", "is_mega", "type_changed"):
            setattr(dst, attr, bool(getattr(src, attr) or getattr(dst, attr)))
        if src.hp_estimated:
            dst.hp_source = src.hp_source   # 推定値の出所も推定の印と一緒に移す (2026-10-09)
        if any(src.boosts.values()):
            dst.boosts = dict(src.boosts)
        if src.volatiles:
            dst.volatiles = list(src.volatiles)
        dst.moves = src.moves or dst.moves
        dst.revealed_moves = list(dict.fromkeys([*dst.revealed_moves, *src.revealed_moves]))
        dst.aliases = list(dict.fromkeys([*dst.aliases, *src.aliases]))[-6:]
        dst.last_seen_ts = max(dst.last_seen_ts, src.last_seen_ts)
        was_active = self.active_index == src_idx
        dst.is_active = was_active or dst.is_active
        self.party.pop(src_idx)
        if was_active:
            self.active_index = dst_idx
        elif self.active_index is not None and self.active_index > src_idx:
            self.active_index -= 1
        return {"merged_from": src_idx, "merged_to": dst_idx, "species": dst.species_id, "ja": dst.species_ja,
                "form_determined": not dst.species_candidates, "t_first_seen": src.t_first_seen,
                "moved": {"hp": src.hp_percent, "revealed_moves": list(src.revealed_moves), "status": src.status},
                "replaced": replaced}

    def resolve_pending(self) -> list:
        """対応待ちの個体のうち対応先が決まったものを枠へ移す (純粋)。戻り値: 移した記録の list (merge_pending_into)"""
        out = []
        for pi in reversed(self.pending_indices()):   # 後ろから (pop で前の番号がずれない)
            if pi < PARTY_SIZE:
                continue   # 対応待ちは満枠のときだけ末尾に置く。ロスターの範囲にあるものは扱わない
            pm = self.party[pi]
            target, form, basis = self.pending_target(pm)
            if target is None:
                continue
            if basis != "species" and has_observation(self.party[target]) and has_observation(pm):
                continue   # 別の個体かもしれない枠の観測を上書きしない (同じ種の枠は同じ個体なので移す)
            r = self.merge_pending_into(pi, target, form)
            r["basis"] = basis
            out.append(r)
        return out

    def prune_placeholders(self):
        """種族もタイプも不明な非アクティブの余剰枠 (7枠目以降) を削除する"""
        keep = []
        for i, p in enumerate(self.party):
            is_extra = i >= 6 and not p.species_ja and not p.types
            if is_extra and i != self.active_index:
                continue
            keep.append(p)
        if len(keep) != len(self.party):
            active = self.active()
            self.party = keep
            if active in self.party:
                self.active_index = self.party.index(active)
            elif self.active_index is not None:
                self.active_index = min(self.active_index, len(self.party) - 1) \
                    if self.party else None

    def clear_hazards(self):
        self.stealth_rock = False
        self.spikes = 0
        self.toxic_spikes = 0
        self.sticky_web = False

    def to_dict(self):
        d = {
            "trainer_name": self.trainer_name,
            "active_index": self.active_index,
            "remaining": self.remaining,
            "party": [p.to_dict() for p in self.party],
            "hazards": {
                "stealth_rock": self.stealth_rock,
                "spikes": self.spikes,
                "toxic_spikes": self.toxic_spikes,
                "sticky_web": self.sticky_web,
            },
            "screens": {
                "reflect": self.reflect,
                "light_screen": self.light_screen,
                "aurora_veil": self.aurora_veil,
                "safeguard": self.safeguard,
            },
            "tailwind": self.tailwind,
        }
        # 対応待ちの個体の「候補の枠」(2026-10-09 段 2。画面の相手欄と助言の opp_pending_note が使う。自動では統合しない)
        for i, p in enumerate(self.party):
            if p.pending and i >= PARTY_SIZE:
                try:
                    d["party"][i]["pending_hint"] = self.pending_hint(p)
                except Exception:
                    d["party"][i]["pending_hint"] = []
        return d


@dataclass
class FieldState:
    weather: Optional[str] = None       # sun / rain / sandstorm / snow
    weather_turns: Optional[int] = None
    terrain: Optional[str] = None       # electric / grassy / psychic / misty
    terrain_turns: Optional[int] = None
    trick_room: bool = False
    trick_room_turns: Optional[int] = None
    gravity: bool = False

    def to_dict(self):
        return asdict(self)


class BattleStateV2:
    def __init__(self):
        self.field = FieldState()
        self.player = SideState()
        self.opponent = SideState(hold_unplaced=True)
        self.scene: str = "unknown"
        self.selection_picked: Optional[int] = None   # 選出画面の「N/3」のN
        self.command_no: Optional[int] = None     # 画面右上のCOMMAND番号 (残り時間秒)
        self.turn: int = 0
        self.mega_used = {"player": False, "opponent": False}
        self.battle_active: bool = False
        self.outcome: Optional[str] = None    # win / loss (勝敗メッセージから)
        # 終了シグナル (勝敗文言 / ランク画面 / リザルト画面 / 3体目のひんしの確定) のいずれかを見た。
        # battle_active は開始前も False なので、終了後の助言抑止にはこちらを使う
        self.battle_ended: bool = False
        # 3体目のひんし等の終了の兆候 {"side", "ts", "fainted"}。猶予内に交代が無ければ確定 (events.confirm_end_hint)
        self.end_hint: Optional[dict] = None
        # WIN / LOSE の画面から読んだ勝敗 (vision/win_lose。1 対戦 1 回だけ発火させるための印。reset_battle で消える)
        self.win_lose_screen: Optional[str] = None
        # とんぼがえり系を自分が使用し、交代先の選択が保留中 (2026-08-21
        # 第8回: この場面で技トップの助言が出ていた)。events が技使用で
        # 立て、switch_player / 次ターン到達で下ろす。engineは交代限定で助言
        self.pending_pivot_switch: bool = False
        self.events: list = []          # [{ts, source, text, event, target}]
        self.hp_max_votes: dict = {}    # (side, species) -> {最大HP読取値: 票数}
        self.last_texts = {"message": "", "left_popup": "", "right_popup": ""}
        # 直近のレート表示 {"value": int, "ts": float}。結果画面のレート増減
        # から勝敗を推定するため、対戦リセットを跨いで保持する
        self.last_rate: Optional[dict] = None
        # 連続まもる使用回数 (成功率減衰の追跡。他の技で0にリセット)
        self.protect_streak = {"player": 0, "opponent": 0}
        # 対戦の世代番号。reset_battle のたびに+1され、ログ分割の唯一の
        # 根拠になる (分割条件を書く側が独自のシーン判定を持つと、リセットと
        # 分割がズレて対戦ログが連結される事故が起きた: 2026-08-11)
        self.battle_seq: int = 0
        # 各側のアクティブが直前に使った技 {side: move_id}。
        # アンコールの技固定の解決に使う。交代・ひんしでその側をクリア
        self.last_move: dict = {}
        # 側ごとの「最後に捨てた HP の読み」(record_hp_reject。理由の一覧は HP_REJECT_REASONS の注記)
        self.hp_reject: dict = {"player": None, "opponent": None}
        # 自分の HP の読みの経過 (record_my_hp_trace、直近 MY_HP_TRACE_LEN 件。MY_HP_TRACE_ACCEPT の注記)
        self.my_hp_trace: list = []
        self.my_hp_reject_streak: int = 0       # 確定も保留もしない読みの連続数 (my_hp_reject_streak)
        self.my_hp_trace_dumps: int = 0         # この対戦で対戦ログに出した回数 (上限 MY_HP_TRACE_MAX_DUMPS)
        self.my_hp_trace_end_done: bool = False  # 対戦の終わりの分を出したか
        # このフレームで対戦ログに出す my_hp_trace の行 (pipeline が毎フレームの始めに空にする。to_dict の my_hp_trace_dumps)
        self.my_hp_trace_outbox: list = []
        # 様子見画面の右列の読み (最新のフレームの分。[{row, hp_text, pct, species, method, score, written}])
        self.watch_opp_rows: list = []

    # --- イベントログ ---
    def log_event(self, source: str, text: str, event_id: Optional[str] = None,
                  target: Optional[str] = None, detail: Optional[dict] = None):
        entry = {
            "ts": round(time.time(), 2),
            "source": source,
            "text": text,
            "event": event_id,
            "target": target,
            "detail": detail or {},
        }
        self.events.append(entry)
        if len(self.events) > 300:
            self.events = self.events[-300:]
        return entry

    def record_hp_reject(self, side: str, rec: dict) -> dict:
        """HP の読みを捨てた記録を残す。rec: {reason, name_text, name_similarity, hp_candidate, bar_ratio, slot, ...}。
        t を付け、側の「最後に捨てた読み」に重ね (merge_hp_reject)、起動からの件数 (HP_REJECT_COUNTS) を 1 増やす"""
        rec = dict(rec)
        rec.setdefault("t", round(time.time(), 2))
        merged = merge_hp_reject(self.hp_reject.get(side), rec)
        self.hp_reject[side] = merged
        key = (side, rec.get("reason"))
        HP_REJECT_COUNTS[key] = HP_REJECT_COUNTS.get(key, 0) + 1
        return merged

    def record_my_hp_trace(self, row: dict) -> dict:
        """自分の HP の読みの経過を 1 行足す (直近 MY_HP_TRACE_LEN 件)。棄却が MY_HP_TRACE_DUMP_STREAK 回続いたら
        対戦ログに出す分を作る (dump_my_hp_trace)"""
        row = dict(row)
        row.setdefault("t", round(time.time(), 2))
        row.setdefault("scene", self.scene)
        self.my_hp_trace.append(row)
        if len(self.my_hp_trace) > MY_HP_TRACE_LEN:
            self.my_hp_trace = self.my_hp_trace[-MY_HP_TRACE_LEN:]
        self.my_hp_reject_streak = my_hp_reject_streak(self.my_hp_reject_streak, row.get("decision"))
        if self.my_hp_reject_streak >= MY_HP_TRACE_DUMP_STREAK:
            self.dump_my_hp_trace("reject_streak")
            self.my_hp_reject_streak = 0
        return row

    def dump_my_hp_trace(self, reason: str) -> Optional[dict]:
        """いまの my_hp_trace を対戦ログに出す分 (outbox) に入れる。行が無い・この対戦の上限に達したら出さない (None)"""
        if not self.my_hp_trace or self.my_hp_trace_dumps >= MY_HP_TRACE_MAX_DUMPS:
            return None
        self.my_hp_trace_dumps += 1
        rec = {"reason": reason, "battle_seq": self.battle_seq, "n": self.my_hp_trace_dumps,
               "rows": [dict(r) for r in self.my_hp_trace]}
        self.my_hp_trace_outbox.append(rec)
        return rec

    def side(self, name: str) -> SideState:
        return self.player if name == "player" else self.opponent

    def resolve_pending(self, side_name: str = "opponent") -> list:
        """対応待ちの個体の対応が決まったら枠へ移し、移した内容をイベントに残す (対戦ログの roster_change に
        merged_from / merged_to を付ける材料。2026-10-09 ④)。戻り値: SideState.resolve_pending の記録"""
        res = self.side(side_name).resolve_pending()
        for r in res:
            self.log_event("system", f"対応待ちの {r.get('ja')} を枠 {r['merged_to']} に対応 "
                           f"(置き換えた枠: {r['replaced'].get('ja') or '未特定'})",
                           event_id="roster_pending_resolved", target=side_name, detail=r)
        return res

    def assign_pending(self, side_name: str, src_idx: int, dst_idx: int) -> dict:
        """人が対応待ちの個体の枠を指定して確定する (SideState.assign_pending)。入れたら roster_pending_resolved のイベントを
        残す (対戦ログの roster_change に merged_from / merged_to と basis manual)"""
        res = self.side(side_name).assign_pending(src_idx, dst_idx)
        if res["ok"]:
            r = res["record"]
            self.log_event("manual", f"対応待ちの {r.get('ja')} を枠 {r['merged_to'] + 1} に割り当て (手動、置き換えた枠: "
                           f"{r['replaced'].get('ja') or '未特定'})", event_id="roster_pending_resolved", target=side_name,
                           detail=r)
        return res

    def needs_reset_for_new_battle(self) -> bool:
        """確定選出画面に入ったとき、前の対戦の内容が載っているか (= reset_battle が要るか)。純粋。

        turn > 0 と battle_active は従来の根拠 (2026-08-11)。outcome / battle_ended は「対戦は終わった (勝敗文言や
        ランク画面は取れた) が command 画面を一度も取れず turn が 0 のまま」の根拠: 2026-09-29 第16回で熱圧迫により
        処理率 9% に落ち、この状態から次戦の選出でリセットされず、outcome が残って助言が止まり、ログも 2 戦連結した
        """
        return bool(self.turn > 0 or self.battle_active or self.outcome or self.battle_ended)

    def reset_battle(self):
        """新しい対戦の開始 (選出画面検知時など) に呼ぶ"""
        keep_rate = self.last_rate   # レートは対戦を跨ぐ情報なので保持
        next_seq = self.battle_seq + 1   # 世代番号も跨いで単調増加させる
        # 前の対戦の自分の HP の読みの経過を、終わりの分として出していなければ出す (このフレームの to_dict で対戦ログへ)
        if not self.my_hp_trace_end_done:
            self.dump_my_hp_trace("battle_end")
        outbox = list(self.my_hp_trace_outbox)
        self.__init__()
        self.last_rate = keep_rate
        self.battle_seq = next_seq
        self.my_hp_trace_outbox = outbox
        # __init__ の再実行では宣言外の臨時属性が消えない。位置ベースの
        # ものは前の対戦の値が誤適用されるため明示的に破棄する
        # (交代メニュー由来の選出確定 index 集合など)
        self._watch_roster_idx = set()

    def restore_from_dict(self, d: dict) -> None:
        """スナップショットからの復元 (サーバー再起動の対戦中リカバリ用)。

        選出画面でしか取れない情報 (相手ロスター/選出フラグ) を含む
        主要フィールドを書き戻す。イベントログ等は復元しない
        """
        def load_mon(md: dict) -> PokemonState:
            mon = PokemonState()
            for k in ("species_ja", "species_id", "display_name", "gender",
                      "types", "hp_percent", "hp_current", "hp_max",
                      "hp_uncertain", "hp_read_ts", "hp_estimated", "hp_source",
                      "status", "volatiles", "boosts", "ability_ja",
                      "ability_id", "item_ja", "item_id", "item_consumed",
                      "revealed_moves", "aliases", "is_mega", "is_active",
                      "is_picked", "pick_order", "species_guess", "guess_score",
                      "pending", "species_candidates", "t_first_seen"):
                if k in md and md[k] is not None:
                    setattr(mon, k, md[k])
            mon.moves = [MoveSlot(**{kk: m.get(kk) for kk in
                                     ("name_ja", "move_id", "pp", "max_pp",
                                      "effectiveness")})
                         for m in (md.get("moves") or [])]
            return mon

        for side_name in ("player", "opponent"):
            sd = d.get(side_name) or {}
            side = self.side(side_name)
            rows = sd.get("party") or []
            # ロスターは 6 枠まで。7 枠目以降は対応待ちの個体 (2026-10-09 ④) だけを戻す
            side.party = [load_mon(m) for m in rows[:PARTY_SIZE]] + \
                [load_mon(m) for m in rows[PARTY_SIZE:] if m.get("pending")]
            side.active_index = sd.get("active_index")
            hz = sd.get("hazards") or {}
            side.stealth_rock = bool(hz.get("stealth_rock"))
            side.spikes = int(hz.get("spikes") or 0)
            side.toxic_spikes = int(hz.get("toxic_spikes") or 0)
            side.sticky_web = bool(hz.get("sticky_web"))
            sc = sd.get("screens") or {}
            side.reflect = bool(sc.get("reflect"))
            side.light_screen = bool(sc.get("light_screen"))
            side.aurora_veil = bool(sc.get("aurora_veil"))
            side.tailwind = bool(sd.get("tailwind"))
        f = d.get("field") or {}
        self.field.weather = f.get("weather")
        self.field.weather_turns = f.get("weather_turns")
        self.field.terrain = f.get("terrain")
        self.field.terrain_turns = f.get("terrain_turns")
        self.field.trick_room = bool(f.get("trick_room"))
        self.turn = int(d.get("turn") or 0)
        self.mega_used = dict(d.get("mega_used") or
                              {"player": False, "opponent": False})
        self.protect_streak = dict(d.get("protect_streak") or
                                   {"player": 0, "opponent": 0})
        self.battle_active = bool(d.get("battle_active"))
        self.battle_ended = bool(d.get("battle_ended"))
        self.selection_picked = d.get("selection_picked")
        self.battle_seq = int(d.get("battle_seq") or 0)
        self.last_move = dict(d.get("last_move") or {})

    def to_dict(self):
        return {
            "scene": self.scene,
            "selection_picked": self.selection_picked,
            "turn": self.turn,
            "command_no": self.command_no,
            "battle_active": self.battle_active,
            "battle_ended": self.battle_ended,
            "end_hint": self.end_hint,
            "outcome": self.outcome,
            "pending_pivot_switch": self.pending_pivot_switch,
            "field": self.field.to_dict(),
            "player": self.player.to_dict(),
            "opponent": self.opponent.to_dict(),
            "mega_used": dict(self.mega_used),
            "events": self.events[-30:],
            "last_rate": self.last_rate,
            "protect_streak": dict(self.protect_streak),
            "battle_seq": self.battle_seq,
            "last_move": dict(self.last_move),
            "hp_reject": {k: (dict(v) if v else None) for k, v in self.hp_reject.items()},
            # 2026-10-09 fix/hp-paths: このフレームで対戦ログに出す自分の HP の読みの経過 (ふだんは空)・様子見画面の右列の読み
            "my_hp_trace_dumps": [dict(d) for d in self.my_hp_trace_outbox],
            "watch_opp_rows": [dict(r) for r in self.watch_opp_rows],
        }
