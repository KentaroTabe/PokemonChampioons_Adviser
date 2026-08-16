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
from typing import Optional

STAT_KEYS = ("atk", "def", "spa", "spd", "spe", "acc", "eva")

MAJOR_STATUSES = ("poison", "toxic", "burn", "paralysis", "sleep", "freeze", "drowsy")


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

    def merge_species(self, species_ja: str, species_id: Optional[str]):
        self.species_ja = species_ja
        if species_id:
            self.species_id = species_id

    def set_boost(self, stat: str, delta: int):
        if stat in self.boosts:
            self.boosts[stat] = max(-6, min(6, self.boosts[stat] + delta))

    def reset_on_switch_out(self):
        """交代で消える揮発性状態をリセット"""
        self.boosts = {k: 0 for k in STAT_KEYS}
        keep = {"leechseed"}  # やどりぎも交代で消えるが表記簡略化のため消す
        self.volatiles = []
        self.is_active = False

    def to_dict(self):
        d = asdict(self)
        d["moves"] = [m if isinstance(m, dict) else asdict(m) for m in
                      (self.moves or [])]
        return d


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

    def active(self) -> Optional[PokemonState]:
        if self.active_index is not None and 0 <= self.active_index < len(self.party):
            return self.party[self.active_index]
        return None

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

    def find_by_species(self, species_ja: str) -> Optional[int]:
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

    def switch_to_species(self, species_ja: str, species_id: Optional[str]) -> PokemonState:
        idx = self.find_by_species(species_ja)
        if idx is None and len(self.party) >= 6:
            # 満枠での「初登場」= 既存枠の視覚同定ミスが濃厚 (実測:
            # ラフレシアと誤同定した枠の実体がフシギバナで、appendにより
            # ルール上あり得ない7匹構成になった)。図鑑タイプが一致し
            # 技未判明の非アクティブ枠を新種で置き換える。ロスターは
            # 対戦中に6を超えない
            new_types = _dex_types_ja_of(species_id)
            cand = None
            for i, p in enumerate(self.party):
                if i == self.active_index or p.revealed_moves or \
                        p.status == "fainted":
                    continue
                if new_types and p.types and set(p.types) == new_types:
                    cand = i
                    break
                if cand is None and not p.species_ja:
                    cand = i
            if cand is None:
                for i, p in enumerate(self.party):
                    if i != self.active_index and not p.revealed_moves and \
                            p.status != "fainted":
                        cand = i
                        break
            if cand is not None:
                p = self.party[cand]
                p.species_ja, p.species_id = species_ja, species_id
                p.types = list(new_types) if new_types else []
                idx = cand
        if idx is None:
            if len(self.party) >= 6:
                # 満枠で置換候補も無い場合は7枠目を作らない (ロスターは
                # 対戦中に6を超えない。2026-08-05接続テストで誤読由来の
                # 7枠目が生えて表示を汚した)。現在のアクティブを維持する
                return self.ensure_active()
            # 初登場 -> 一旦末尾に追加する (どの選出枠に対応するかは
            # link_active_to_party がタイプ照合で解決し、余剰枠を除去する)
            mon = PokemonState(species_ja=species_ja, species_id=species_id)
            self.party.append(mon)
            idx = len(self.party) - 1
        self.switch_to(idx)
        mon = self.party[idx]
        mon.merge_species(species_ja, species_id)
        return mon

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
        self.opponent = SideState()
        self.scene: str = "unknown"
        self.selection_picked: Optional[int] = None   # 選出画面の「N/3」のN
        self.command_no: Optional[int] = None     # 画面右上のCOMMAND番号 (残り時間秒)
        self.turn: int = 0
        self.mega_used = {"player": False, "opponent": False}
        self.battle_active: bool = False
        self.outcome: Optional[str] = None    # win / loss (勝敗メッセージから)
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

    def side(self, name: str) -> SideState:
        return self.player if name == "player" else self.opponent

    def reset_battle(self):
        """新しい対戦の開始 (選出画面検知時など) に呼ぶ"""
        keep_rate = self.last_rate   # レートは対戦を跨ぐ情報なので保持
        next_seq = self.battle_seq + 1   # 世代番号も跨いで単調増加させる
        self.__init__()
        self.last_rate = keep_rate
        self.battle_seq = next_seq

    def restore_from_dict(self, d: dict) -> None:
        """スナップショットからの復元 (サーバー再起動の対戦中リカバリ用)。

        選出画面でしか取れない情報 (相手ロスター/選出フラグ) を含む
        主要フィールドを書き戻す。イベントログ等は復元しない
        """
        def load_mon(md: dict) -> PokemonState:
            mon = PokemonState()
            for k in ("species_ja", "species_id", "display_name", "gender",
                      "types", "hp_percent", "hp_current", "hp_max",
                      "status", "volatiles", "boosts", "ability_ja",
                      "ability_id", "item_ja", "item_id", "item_consumed",
                      "revealed_moves", "aliases", "is_mega", "is_active",
                      "is_picked", "pick_order"):
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
            side.party = [load_mon(m) for m in (sd.get("party") or [])[:6]]
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
            "outcome": self.outcome,
            "field": self.field.to_dict(),
            "player": self.player.to_dict(),
            "opponent": self.opponent.to_dict(),
            "mega_used": dict(self.mega_used),
            "events": self.events[-30:],
            "last_rate": self.last_rate,
            "protect_streak": dict(self.protect_streak),
            "battle_seq": self.battle_seq,
            "last_move": dict(self.last_move),
        }
