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
        if idx is None:
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
        self.__init__()
        self.last_rate = keep_rate

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
        }
