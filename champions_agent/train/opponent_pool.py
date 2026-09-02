"""population-based selfplay のためのチェックポイントプール管理。

- 勝率ゲート: vs Random の勝率が閾値 (既定0.75) を超えた性格のチェックポイントを
  プール (train/checkpoints/pool/) へスナップショットする
- 学習時: プールにエントリがあれば、対戦相手を RandomPlayer から
  「過去チェックポイントの方策 (バトルごとにランダム抽選)」へ自動で切り替える
  (ε=一定確率でRandomも混ぜ、基礎的な相手への忘却を防ぐ)

CLI:
    python -m champions_agent.train.opponent_pool --list
    python -m champions_agent.train.opponent_pool --update-from-eval balance
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from pathlib import Path

from champions_agent.config import MODELS_DIR, TRAIN_OPP_POLICY_CACHE

POOL_DIR = MODELS_DIR / "pool"
STATE_PATH = POOL_DIR / "pool_state.json"
EVAL_DIR = Path(__file__).resolve().parent / "logs"

WIN_RATE_GATE = 0.75      # vs Random 勝率がこれ以上でプール入り
MAX_ENTRIES_PER_STYLE = 5  # 性格ごとの保持世代数
EPSILON_RANDOM = 0.10      # ランダム相手を混ぜる確率 (基礎相手の忘却防止)
# 頭打ち対策: 評価指標 (vsベンチマーク) と学習分布を近づけるため、
# ヒューリスティクス強敵と上位構築チームの比率を引き上げた
# (旧: heuristic 0.25 / ranked 0.30 でベンチ勝率0.3-0.7の振動が続いた)
EPSILON_HEURISTIC = 0.25  # 上位構築ヒューリスティクス相手を混ぜる確率 (常設の強敵)
# 探索エンジン相手 (advisor/searchのダメ計+択読み)。実測強度はベンチ勝率
# 0.41で学習済み方策と同格だが、ヒューリスティクスとは読み筋・交代傾向が
# 異なるため相手多様性として混入する (2026-07-24導入。heuristic 0.35→0.25
# に減らして枠を捻出、selfplayプール枠は0.45)。
# 2026-08-25: P1棄却後の登録済み次候補として 0.20→0.30 (P4)。
# 2026-09-02: 事前登録判定で棄却し 0.20 に復帰。主要指標 h2h vs 3_best は
# 18,000戦で 0.5662 (基準 0.6061 比 -0.040、ゲート +0.10 に遠く未達)。
# 軸対照 (凍結アンカー 0.5552、登録時 0.6061 から z≈8 で下方回転) を
# 補正した同一軸差も +0.009±0.008 で効果なし。ベンチ同日ペアは +0.045 で
# ガードレール通過 (害は無い)。selfplayプール枠は 0.45 に復帰。
# 詳細は training_changes.json 2026-09-02 17:55
EPSILON_SEARCH = 0.20
# 歴史的アンカー (_best 3本 + anchors/ の週次スナップショット) を相手に混ぜる
# 確率。2026-08-18 に 0.25 で導入 (docs/AXIS_GAP_ANALYSIS.md P1) したが、
# 2026-08-25 の事前登録判定で棄却し 0 に戻した: 主要指標 h2h vs 3_best は
# 0.514→0.606 (+0.092) でゲート +0.10 に未達 (2回測定18,000戦、詳細は
# training_changes.json)。ベンチガードレールは +0.140 で通過 (害は無い) が、
# 「通常学習を超える効果」の証明に届かなかった。selfplay プール枠は
# 0.45 に復帰。抽選機構と週次アンカー保存 (P2 測定基盤) は残置し、
# 再導入時はこの値を戻して新たに事前登録すること
EPSILON_ANCHOR = 0.0
ANCHORS_DIR = MODELS_DIR / "anchors"
ANCHOR_INTERVAL_DAYS = 7   # 週次アンカー保存の間隔
MAX_ANCHORS_PER_STYLE = 4  # anchors/ の性格ごと保持数 (_best は別枠で常時参照)
RANKED_TEAM_PROB = 0.50   # 相手チームを上位構築の実物にする確率
OWN_RANKED_TEAM_PROB = 0.50  # 自分チームを上位構築の実物にする確率
# (生成チームだけで学習すると「上位構築を操縦する」経験が積めず、
#  アドバイザーの実用途=ユーザーの実チームでの助言と分布がズレる)


def anchor_paths(models_dir: Path = None) -> list:
    """歴史的アンカーのファイル一覧: _best 3本 + anchors/ の週次スナップショット。

    存在するものだけ返す (導入直後は _best のみ)。
    """
    base = Path(models_dir) if models_dir else MODELS_DIR
    paths = []
    for style in ("balance", "offense", "cycle"):
        p = base / f"battle_policy_{style}_best.zip"
        if p.exists():
            paths.append(p)
    anchors = base / "anchors"
    if anchors.is_dir():
        paths.extend(sorted(anchors.glob("*.zip")))
    return paths


def draw_opponent_kind(r: float, has_pool: bool, has_anchor: bool,
                        epsilon_heuristic: float = EPSILON_HEURISTIC,
                        epsilon_search: float = EPSILON_SEARCH,
                        epsilon_random: float = EPSILON_RANDOM,
                        epsilon_anchor: float = EPSILON_ANCHOR) -> str:
    """一様乱数 r (0..1) から相手の種別を決める純粋関数。

    'heuristic' / 'search' / 'random' / 'anchor' / 'pool' を返す。
    アンカーが無い場合そのぶんは selfplay プールへ、プールも空なら
    'random' へ落ちる (カリキュラム初期と同じ構成)。
    """
    t1 = epsilon_heuristic
    t2 = t1 + epsilon_search
    t3 = t2 + epsilon_random
    t4 = t3 + epsilon_anchor
    if r < t1:
        return "heuristic"
    if r < t2:
        return "search"
    if r < t3:
        return "random"
    if r < t4 and has_anchor:
        return "anchor"
    return "pool" if has_pool else "random"


def save_weekly_anchors(today: str | None = None,
                         models_dir: Path = None) -> list:
    """current の週次アンカーを anchors/ へ保存する (P2、日次定点から呼ばれる)。

    性格ごとに、最新アンカーが ANCHOR_INTERVAL_DAYS 日以上前 (または無い)
    なら battle_policy_{style}.zip をコピーし、MAX_ANCHORS_PER_STYLE を
    超えた古いものを削除する。戻り値: 保存したファイル名のリスト。

    狙い: (1) プールに間隔を空けた過去世代を供給し忘却を防ぐ
          (2) 過去時点の再測を可能にする (8/15 の vs_agents 0.618 は
              スナップショット不在で検証不能だった)
    """
    from datetime import date, datetime
    base = Path(models_dir) if models_dir else MODELS_DIR
    anchors = base / "anchors"
    anchors.mkdir(parents=True, exist_ok=True)
    today_d = (datetime.strptime(today, "%Y%m%d").date() if today
               else date.today())
    saved = []
    for style in ("balance", "offense", "cycle"):
        src = base / f"battle_policy_{style}.zip"
        if not src.exists():
            continue
        existing = sorted(anchors.glob(f"{style}_*.zip"))
        if existing:
            newest = existing[-1].stem.split("_")[-1]
            try:
                newest_d = datetime.strptime(newest, "%Y%m%d").date()
                if (today_d - newest_d).days < ANCHOR_INTERVAL_DAYS:
                    continue
            except ValueError:
                pass
        dst = anchors / f"{style}_{today_d.strftime('%Y%m%d')}.zip"
        if dst.exists():
            continue
        shutil.copy(src, dst)
        saved.append(dst.name)
        existing = sorted(anchors.glob(f"{style}_*.zip"))
        for old in existing[:max(0, len(existing) - MAX_ANCHORS_PER_STYLE)]:
            old.unlink(missing_ok=True)
    return saved


class OpponentPool:
    def __init__(self):
        POOL_DIR.mkdir(parents=True, exist_ok=True)
        self.state = {"entries": []}
        if STATE_PATH.exists():
            try:
                self.state = json.loads(STATE_PATH.read_text())
            except Exception:
                pass
        # 実在しないファイルのエントリを掃除
        self.state["entries"] = [
            e for e in self.state.get("entries", [])
            if (POOL_DIR / e["file"]).exists()
        ]

    def _save(self):
        STATE_PATH.write_text(json.dumps(self.state, ensure_ascii=False, indent=1))

    def entries(self) -> list:
        return self.state.get("entries", [])

    def has_entries(self) -> bool:
        return len(self.entries()) > 0

    def add(self, style: str, checkpoint: Path, win_rate: float,
            bench_rate: float | None = None) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{style}_{ts}.zip"
        shutil.copy(checkpoint, POOL_DIR / fname)
        self.state["entries"].append({
            "file": fname, "style": style,
            "win_rate": win_rate, "bench_rate": bench_rate, "added_at": ts,
        })
        # 性格ごとに古い世代を間引く
        same = [e for e in self.state["entries"] if e["style"] == style]
        if len(same) > MAX_ENTRIES_PER_STYLE:
            for old in sorted(same, key=lambda e: e["added_at"])[:len(same) - MAX_ENTRIES_PER_STYLE]:
                (POOL_DIR / old["file"]).unlink(missing_ok=True)
                self.state["entries"].remove(old)
        self._save()
        return POOL_DIR / fname

    def sample(self, rng: random.Random | None = None) -> Path | None:
        """プールからチェックポイントを1つ抽選する。

        新しい世代ほど高確率、かつベンチマーク勝率が高い世代ほど高確率
        (頭打ち対策: 強い過去世代を優先して当てることでselfplayに
        上達圧力をかける。bench_rate未記録の旧エントリは0.4扱い)
        """
        rng = rng or random
        entries = self.entries()
        if not entries:
            return None
        ordered = sorted(entries, key=lambda e: e["added_at"])
        weights = [
            (i + 1) * (0.3 + (e.get("bench_rate") if e.get("bench_rate")
                              is not None else 0.4))
            for i, e in enumerate(ordered)
        ]
        chosen = rng.choices(ordered, weights=weights, k=1)[0]
        return POOL_DIR / chosen["file"]


def lru_get_or_load(cache: dict, key, loader, cap: int):
    """挿入順を参照順として使うLRUキャッシュの取得/ロード (純粋関数)。

    dict は挿入順を保つため、ヒット時に pop→再挿入で末尾 (最新) へ動かし、
    cap 超過時は先頭 (最古) から捨てる。loader はミス時のみ呼ばれる。
    2026-08-20 メモリ枯渇対策: 相手方策キャッシュが無上限で、全世代の
    torchモデルがワーカーごとに載っていた。
    """
    if key in cache:
        cache[key] = cache.pop(key)
    else:
        cache[key] = loader()
        while len(cache) > max(1, cap):
            cache.pop(next(iter(cache)))
    return cache[key]


def prune_finished_assignments(assign: dict, battles: dict) -> None:
    """終了済みバトルの相手割当を破棄する (純粋な辞書操作)。

    割当が残っていると、キャッシュから追い出した方策オブジェクトが
    参照で延命される。進行中バトルの割当は変えない (相手は不変)。
    """
    for tag in [t for t, b in list(battles.items())
                if getattr(b, "finished", False)]:
        assign.pop(tag, None)


class PoolOpponentPlayer:
    """バトルごとにプールから方策を抽選して指す対戦相手プレイヤー。

    poke-env の Player を継承する動的クラスとして生成する
    (make_pool_opponent を使うこと)。
    """


def make_pool_opponent(pool: OpponentPool, epsilon_random: float = EPSILON_RANDOM,
                       epsilon_heuristic: float = EPSILON_HEURISTIC,
                       epsilon_search: float = EPSILON_SEARCH,
                       **player_kwargs):
    """混合対戦相手を生成する。バトルごとに以下から抽選:

    - "heuristic": SimpleHeuristicsPlayer (ダメージ計算+交代判断の固定強敵)
    - "search":    探索エンジン (advisor/search、深さ1の択読み)
    - "random":    ランダム行動 (基礎相手の忘却防止)
    - それ以外:    selfplayプールの過去チェックポイント方策
    プールが空の場合は heuristic / search / random で構成される (カリキュラム初期)。
    """
    from poke_env.player import SimpleHeuristicsPlayer
    from champions_agent.agent.policy_battle import BattlePolicy

    class _PoolOpponent(SimpleHeuristicsPlayer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pool = pool
            self._assign: dict = {}      # battle_tag -> "random"|"heuristic"|BattlePolicy
            self._policy_cache: dict = {}

        def _load_policy(self, path):
            """チェックポイントを方策としてロードする (失敗は None)。

            キャッシュは LRU 上限つき (TRAIN_OPP_POLICY_CACHE)。抽選分布は
            変えず保持数だけ絞る。追い出された世代は次回抽選時に再ロード。
            """
            if path is None:
                return None

            def _loader():
                try:
                    return BattlePolicy(model_path=path)
                except Exception:
                    return None

            policy = lru_get_or_load(self._policy_cache, str(path),
                                     _loader, cap=TRAIN_OPP_POLICY_CACHE)
            return policy if (policy is not None
                              and policy.model is not None) else None

        def _policy_for(self, battle):
            tag = battle.battle_tag
            if tag not in self._assign:
                anchors = anchor_paths()
                kind = draw_opponent_kind(
                    random.random(),
                    has_pool=self._pool.has_entries(),
                    has_anchor=bool(anchors),
                    epsilon_heuristic=epsilon_heuristic,
                    epsilon_search=epsilon_search,
                    epsilon_random=epsilon_random,
                )
                if kind == "anchor":
                    # 歴史的アンカーは一様抽選 (プールの新世代優先と対照的に、
                    # 古い戦略族を均等に当てて忘却を防ぐ)
                    policy = self._load_policy(random.choice(anchors))
                    self._assign[tag] = policy if policy else "heuristic"
                elif kind == "pool":
                    policy = self._load_policy(self._pool.sample())
                    self._assign[tag] = policy if policy else "heuristic"
                else:
                    self._assign[tag] = kind
                # 終了済みバトルの割当を掃除 (キャッシュから追い出した方策が
                # 割当参照で延命されるのを防ぐ)。上限トリムは異常時の backstop
                prune_finished_assignments(self._assign, self.battles)
                if len(self._assign) > 50:
                    for k in list(self._assign.keys())[:-25]:
                        del self._assign[k]
            return self._assign[tag]

        def choose_move(self, battle):
            policy = self._policy_for(battle)
            if policy == "heuristic":
                return super().choose_move(battle)
            if policy == "search":
                try:
                    from champions_agent.env.search_expert import decide
                    d = decide(battle, depth=1)
                    if d is not None:
                        if d["kind"] == "move":
                            return self.create_order(d["move"], mega=d["mega"])
                        return self.create_order(d["pokemon"])
                except Exception:
                    pass
                return super().choose_move(battle)   # 失敗時はヒューリスティクス
            if policy == "random":
                return self.choose_random_move(battle)
            try:
                return policy.choose_order(battle)
            except Exception:
                return self.choose_random_move(battle)

    opp = _PoolOpponent(**player_kwargs)
    # 選出は学習環境側と揃える (対称にする。TRAIN_SELECTION で切り替わる)
    from champions_agent.env.showdown_env import apply_train_teampreview
    apply_train_teampreview(opp)
    return opp


# ------------------------------------------------------------------
def update_from_eval(style: str) -> bool:
    """evaluate.py が出力した直近の評価結果を読み、ゲートを超えていればプール入り"""
    eval_path = EVAL_DIR / f"last_eval_{style}.json"
    if not eval_path.exists():
        print(f"[opponent_pool] 評価結果がありません: {eval_path}")
        return False
    result = json.loads(eval_path.read_text())
    win_rate = float(result.get("win_rate", 0.0))
    ckpt = MODELS_DIR / f"battle_policy_{style}.zip"
    if not ckpt.exists():
        print(f"[opponent_pool] チェックポイントがありません: {ckpt}")
        return False
    if win_rate < WIN_RATE_GATE:
        print(f"[opponent_pool] [{style}] 勝率{win_rate:.2f} < ゲート{WIN_RATE_GATE} "
              "のためプール追加を見送り (相手はRandomのまま)")
        return False
    # ベンチマーク勝率が記録されていれば抽選の重み付けに使う
    bench_rate = None
    bench_path = EVAL_DIR / f"last_eval_{style}_benchmark.json"
    if bench_path.exists():
        try:
            bench_rate = float(json.loads(
                bench_path.read_text()).get("win_rate"))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    pool = OpponentPool()
    path = pool.add(style, ckpt, win_rate, bench_rate=bench_rate)
    br = f" ベンチ{bench_rate:.2f}" if bench_rate is not None else ""
    print(f"[opponent_pool] [{style}] 勝率{win_rate:.2f}{br} でプール入り: {path.name} "
          f"(計{len(pool.entries())}件) -> 次回学習からselfplay相手に使われます")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="selfplay相手プールの管理")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update-from-eval", type=str, default=None,
                         metavar="STYLE", help="直近評価の勝率でゲート判定しプールへ追加")
    args = parser.parse_args()

    if args.update_from_eval:
        update_from_eval(args.update_from_eval)
        return
    pool = OpponentPool()
    print(f"pool entries: {len(pool.entries())}")
    for e in pool.entries():
        print(f"  {e['file']}  style={e['style']} win_rate={e['win_rate']}")


if __name__ == "__main__":
    main()
