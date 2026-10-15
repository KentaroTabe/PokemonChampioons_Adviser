"""助言エンジン (advisor-as-player) の実戦診断: vs ベンチマークの勝率とレイテンシ。

    python -m tools.check_advisor_player --battles 100 --opp-seed 20260904 --json out.json
    オプション: --belief-k K (engine.BELIEF_K) / --sensor-q q / --workers N /
                --rl-blend W (RL 加点の重み。既定 = engine.RL_BLEND_DEFAULT) / --no-rl-blend (= --rl-blend 0) / --skip-random /
                --battle-format ID (既定 = config.TRAINING_BATTLE_FORMAT)
重みは evaluate の引数で渡す (2026-10-07: 環境変数 RL_BLEND_WEIGHT は書かない。起動時の既定値の供給源としてだけ読まれる)。
探索プレイヤー (check_search_expert) と同じ固定軸 (META_PIN) と相手列で測る。
自チーム: --team-file (固定) / --own-pool FILE (実験専用の構築プール、tools.scoring_pool。Random(相手 seed + 1) で対戦ごとに引く。
2026-10-09 M2d。--opp-offset があれば抽選も同じ数だけ先に進める: 2026-10-09 M2e、2 夜分割) / どちらも無ければ RankedTeambuilder
(META_PIN の構築プール、同じ seed の取り方)。
対戦ごとに引く 2 経路と相手 (RankedTeambuilder 上位 60 / --opp-split の列) は、引いたチームを対戦フォーマット (--battle-format) の
validate-team で検査し、不合法なら飛ばして次を引く (2026-10-09。飛ばした件数と理由は結果 JSON の stats の own_team_skipped /
own_team_skip_reasons と opp_team_skipped / opp_team_skip_reasons。draw_legal の説明)。
Showdown がチームを拒否した (popup) ときと、--stall-sec (既定 config ADVISOR_PLAYER_STALL_SEC) 秒 Showdown から何も届かないときは
黙って待たずに異常終了する (終了コード config ADVISOR_PLAYER_EXIT_TEAM_REJECTED / ADVISOR_PLAYER_EXIT_STALLED。BattleGuard)。
--models-dir DIR は、プロジェクトのモジュールを import する前に環境変数 CHAMPIONS_MODELS_DIR を同じ値にする (apply_models_dir_env。
rl_bridge・相手の操縦 (rl)・選出モデルと埋め込みが同じピンを読む)。--print-models-dir で各モジュールが読む dir を表示して終わる。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ------------------------------------------------------------------ --models-dir と環境変数 (2026-10-15 M3c)
# config.MODELS_DIR (相手の操縦 rl の ModelPlayer / BattlePolicy、選出モデル・埋め込みのピン) と advisor.rl_bridge.CKPT_DIR は
# import 時に環境変数 CHAMPIONS_MODELS_DIR を読む。以前は main() で環境変数を設定していたが、この時点で config は import 済みで、
# --models-dir は rl_bridge にしか効かなかった (相手の操縦と選出の埋め込みは既定の dir。docs/ADVICE_DIAG_40_20261015.md §7)。
# 直接起動 (python -m) のときは、下の import より前に引数から環境変数を設定する。
MODELS_DIR_ENV = "CHAMPIONS_MODELS_DIR"


def models_dir_env(argv: list, environ: dict) -> tuple:
    """引数の --models-dir → (環境変数に入れる値 (resolve 済み、無ければ None), 警告 (無ければ None)) (純粋)。
    既に環境変数があって値が違えば、引数を優先して警告を返す"""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--models-dir", default=None)
    ns, _rest = pre.parse_known_args(list(argv))
    if not ns.models_dir:
        return None, None
    value = str(Path(ns.models_dir).resolve())
    cur = environ.get(MODELS_DIR_ENV)
    warn = None
    if cur and str(Path(cur).resolve()) != value:
        warn = f"環境変数 {MODELS_DIR_ENV}={cur} と --models-dir {value} が違う。引数を優先する"
    return value, warn


def apply_models_dir_env(argv: list, environ=None) -> Optional[str]:
    """--models-dir があれば環境変数 CHAMPIONS_MODELS_DIR を同じ値にする (environ を書き換える。警告は stderr)。設定した値を返す"""
    environ = os.environ if environ is None else environ
    value, warn = models_dir_env(argv, environ)
    if warn:
        print(f"[check_advisor_player] ⚠ {warn}", file=sys.stderr)
    if value is not None:
        environ[MODELS_DIR_ENV] = value
    return value


if __name__ == "__main__":
    apply_models_dir_env(sys.argv[1:])

from champions_agent import config as _CFG  # noqa: E402 (--models-dir の環境変数を先に設定するため)
from champions_agent.config import SIM_INPUTS_VERSION, TRAINING_BATTLE_FORMAT  # noqa: E402
from champions_agent.env.sim_accept import summary_from_stats as sim_accept_summary  # noqa: E402


def _constant_teambuilder(text: str):
    """固定チームテキストを毎戦出す Teambuilder (text_for_battle で型登録に使う。どの対戦も同じ本文)"""
    from poke_env.teambuilder import Teambuilder

    class _Const(Teambuilder):
        def __init__(self):
            self.last_text = text
            self.texts = [text]
            self._packed = self.join_team(self.parse_showdown_team(text))

        def text_for_battle(self, k: int):
            return text

        def yield_team(self):
            return self._packed

    return _Const()


# ------------------------------------------------------------------ 対戦フォーマットでの合法性 (2026-10-09)
# アークの再測定 (M-B) が 3 腕とも 7 戦目で停滞: RankedTeambuilder のプールは champions mod (M-C) の持ち物で組まれ
# (team_builder._sanitize_item / _enforce_item_clause)、M-B では使えない持ち物 (ふうせん・ゴツゴツメット) を持つチームがある。
# Showdown がチームを拒否すると poke-env は popup を記録するだけで対戦の開始を待ち続ける。
# 対策: (a) 対戦ごとに引く経路で、引いたチームを対戦フォーマットで検査して不合法なら飛ばして次を引く (TeamLegality / draw_legal)、
# (b) 拒否の popup と、Showdown から何も届かない状態 (停滞) を見張り、黙って待たずに異常終了する (BattleGuard)。

class ValidatorUnavailable(RuntimeError):
    """validate-team そのものが動かない (node / pokemon-showdown が無い等)。不合法とは区別する
    (全件を「不合法」として飛ばし続けないため)"""


def _default_validate(text: str, battle_format: str) -> tuple:
    from tools.team_build.sets import validate_team_text
    return validate_team_text(text, battle_format)


class TeamLegality:
    """本文 → (合法か, errors) を対戦フォーマットで 1 チーム 1 回だけ検査して覚える (validate は差し替え可: テスト用)。
    検査は 1 件 0.27 秒 (node の起動、2026-10-09 実測)。プールは 276 件なので 600 戦でも検査は最大 276 回"""

    def __init__(self, battle_format: str, validate=None):
        self.battle_format = battle_format
        self._validate = validate or _default_validate
        self.cache: dict = {}
        self.sec = 0.0

    def __call__(self, text: str) -> tuple:
        if text not in self.cache:
            from tools.team_build.sets import VALIDATE_RUN_FAILED
            t0 = time.time()
            ok, errs = self._validate(text, self.battle_format)
            self.sec += time.time() - t0
            errs = [str(e) for e in (errs or [])]
            if not ok and errs and errs[0].startswith(VALIDATE_RUN_FAILED):
                raise ValidatorUnavailable(errs[0])
            self.cache[text] = (bool(ok), errs)
        return self.cache[text]

    @property
    def n_checked(self) -> int:
        return len(self.cache)


def skip_reason(errs) -> str:
    """飛ばした理由の集計キー (純粋): validate-team の最初の行 (120 文字まで)"""
    first = next((str(e).strip() for e in (errs or []) if str(e).strip()), "") or "(理由なし)"
    return first[:120]


def draw_legal(rng: random.Random, teams: list, is_legal) -> tuple:
    """rng.choice(teams) を合法 (is_legal(本文) → (ok, errors)) なものが出るまで引く (純粋。is_legal 次第)。
    返り値: (本文, 飛ばした [(本文, errors)])。不合法を飛ばした分だけ rng の列は進む: k 番目の対戦のチームは
    「同じ seed の rng.choice の列から不合法を除いた列」の k 番目。同じ seed・同じプール・同じフォーマットの条件どうしは
    同じ判定で同じだけ飛ばすので、条件間の対応 (対戦 k の自チームが同じ) は保たれる。
    プールの全件が不合法なら RuntimeError (無限に引き続けない)"""
    skipped: list = []
    bad: set = set()
    n_unique = len(set(teams))
    while True:
        text = rng.choice(teams)
        ok, errs = is_legal(text)
        if ok:
            return text, skipped
        skipped.append((text, errs))
        bad.add(text)
        if len(bad) >= n_unique:
            raise RuntimeError(f"プールの全 {n_unique} チームが不合法 (例: {skip_reason(errs)})")


OWN_STATS = "own_team"    # 自チームの飛ばしの stats の接頭辞 (own_team_skipped / own_team_skip_reasons)
OPP_STATS = "opp_team"    # 相手のチームの飛ばしの stats の接頭辞 (opp_team_skipped / opp_team_skip_reasons。2026-10-09)


def record_skips(stats: Optional[dict], skipped: list, prefix: str = OWN_STATS) -> None:
    """飛ばしたチームを stats に数える (純粋): <prefix>_skipped = 件数、<prefix>_skip_reasons = {理由: 件数}"""
    if stats is None or not skipped:
        return
    stats[f"{prefix}_skipped"] = stats.get(f"{prefix}_skipped", 0) + len(skipped)
    reasons = stats.setdefault(f"{prefix}_skip_reasons", {})
    for _, errs in skipped:
        r = skip_reason(errs)
        reasons[r] = reasons.get(r, 0) + 1


def _remembering_teambuilder(inner, legality=None, stats: Optional[dict] = None, stat_prefix: str = OWN_STATS):
    """RankedTeambuilder をラップし、出したチームテキストを出した順に覚える
    (poke-env の Player は Teambuilder 派生でないと受理しないため、
    import を遅延させてクラスを作る)。
    型登録は text_for_battle(k) = k 番目の対戦で出したチームで行う (2026-10-09)。last_text (直近に出した本文) は
    poke-env が対戦 k の開始直後に対戦 k+1 のチームを引くため、対戦 k の時点で既に次のチームになっている
    (advisor_player.team_text_for_battle の説明)。
    legality (TeamLegality) を渡すと、引いたチームが対戦フォーマットで不合法なら飛ばして次を引く (draw_legal。2026-10-09)。
    飛ばしたチームは texts に入れない (texts[k] = 対戦 k で実際に送った本文) で、stats に数える (record_skips)。
    相手の RankedTeambuilder にも同じものを使う (stat_prefix = OPP_STATS。2026-10-09)"""
    from poke_env.teambuilder import Teambuilder

    class _Remembering(Teambuilder):
        def __init__(self):
            self.inner = inner
            self.last_text = None
            self.texts: list = []

        def text_for_battle(self, k: int):
            return self.texts[k] if 0 <= k < len(self.texts) else None

        def yield_team(self):
            if legality is None:
                text = self.inner.rng.choice(self.inner.teams)
            else:
                text, skipped = draw_legal(self.inner.rng, self.inner.teams, legality)
                record_skips(stats, skipped, stat_prefix)
                if skipped:
                    side = "相手" if stat_prefix == OPP_STATS else "自分"
                    print(f"[team] 対戦 {len(self.texts) + 1} ({side}): 不合法なチームを {len(skipped)} 件飛ばした "
                          f"({skip_reason(skipped[0][1])})", flush=True)
            self.last_text = text
            self.texts.append(text)
            return self.join_team(self.parse_showdown_team(text))

    return _Remembering()


TEAM_REJECTED_MARK = "Your team was rejected"


def is_team_rejection(message: str) -> bool:
    """Showdown の受信メッセージがチームの拒否の popup か (純粋)"""
    return any(ln.startswith("|popup|") and TEAM_REJECTED_MARK in ln for ln in str(message or "").split("\n"))


def stall_exceeded(now: float, last_activity: float, limit_sec: float) -> bool:
    """最後の受信から limit_sec を超えたか (純粋)。limit_sec <= 0 は見張らない"""
    return limit_sec > 0 and (now - last_activity) > limit_sec


class BattleGuard:
    """対戦が始まらない状態を黙って待ち続けない (2026-10-09)。見張る Player の受信 (ps_client._handle_message) を包み、
    - チームの拒否の popup を受け取ったら、その旨を出力して終了コード ADVISOR_PLAYER_EXIT_TEAM_REJECTED で異常終了する
      (poke-env は拒否された challenge を作り直さないので、続けると対戦の列と型登録の対応 (texts[k]) が崩れる。次の対戦へは進めない)
    - どのクライアントも stall_sec 秒 Showdown から何も受け取らなければ、終了コード ADVISOR_PLAYER_EXIT_STALLED で異常終了する
    終了は on_exit (既定 os._exit。poke-env の受信ループは別スレッドなので例外では止まらない) を呼ぶ。テストでは差し替える"""

    def __init__(self, stall_sec: float, on_exit=None, clock=time.monotonic, log=None, describe=None):
        self.stall_sec = float(stall_sec)
        self._exit = on_exit or os._exit
        self._clock = clock
        self._log = log or (lambda m: print(m, flush=True))
        self._describe = describe or (lambda: "")
        self.last = clock()
        self.failed = None
        self._lock = threading.Lock()

    def touch(self) -> None:
        self.last = self._clock()

    def on_message(self, username: str, message: str) -> None:
        self.touch()
        if is_team_rejection(message):
            first = next(ln for ln in str(message).split("\n") if ln.startswith("|popup|"))
            self.fail(_CFG.ADVISOR_PLAYER_EXIT_TEAM_REJECTED,
                      f"{username} のチームが Showdown に拒否された: {first[len('|popup|'):].replace('||', ' ')}")

    def check(self) -> bool:
        """停滞していれば異常終了する。返り値: 停滞していたか"""
        if stall_exceeded(self._clock(), self.last, self.stall_sec):
            self.fail(_CFG.ADVISOR_PLAYER_EXIT_STALLED,
                      f"Showdown から {self.stall_sec:.0f} 秒何も届かない (対戦が始まらない・進まない)")
            return True
        return False

    def fail(self, code: int, reason: str) -> None:
        with self._lock:
            if self.failed is not None:
                return
            self.failed = (code, reason)
        extra = self._describe()
        self._log(f"[guard] 異常終了 (rc {code}): {reason}{(' / ' + extra) if extra else ''}")
        self._exit(code)

    def wrap(self, player) -> None:
        client = player.ps_client
        orig = client._handle_message
        name = getattr(player, "username", "?")

        async def _guarded(message, _orig=orig, _name=name):
            self.on_message(_name, message)
            return await _orig(message)

        client._handle_message = _guarded

    def start(self, interval: Optional[float] = None) -> None:
        """見張りのスレッド (daemon) を始める"""
        if self.stall_sec <= 0:
            return
        step = interval or max(1.0, min(30.0, self.stall_sec / 10.0))

        def _loop():
            while self.failed is None:
                time.sleep(step)
                self.check()

        self.touch()
        threading.Thread(target=_loop, name="battle-guard", daemon=True).start()


class _PoolSource:
    """実験専用の構築プール (tools/scoring_pool のファイル) から引く元 (_remembering_teambuilder の inner。RankedTeambuilder と同じ
    teams / rng の 2 つだけを持つ)"""

    def __init__(self, texts: list, rng: random.Random):
        self.teams = list(texts)
        self.rng = rng


def pool_rng(seed: int, texts: list, skip: int = 0, is_legal=None) -> random.Random:
    """実験専用の構築プールの抽選の rng (2026-10-09 M2e)。Random(seed) で、先に skip 回 (= --opp-offset) だけ引いて捨てる。
    同じ seed で最初から引いた列の skip 番目以降と同じ列になる (2 夜分割の part k が、事前に固定した 600 対戦の列の続きの自チームを
    引く)。skip = 0 なら従来と同じ。
    is_legal (TeamLegality) を渡すと、捨てるのは「対戦 skip 回ぶん」= 合法なチーム skip 個 (draw_legal と同じ数え方。2026-10-09):
    不合法を飛ばしながら引いた列の対戦 skip 以降と同じ列になる。プールが全件合法なら is_legal なしと同じ"""
    rng = random.Random(seed)
    for _ in range(max(0, int(skip))):
        if is_legal is None:
            rng.choice(texts)
        else:
            draw_legal(rng, texts, is_legal)
    return rng


def _start_legality(battle_format: str, texts: list) -> TeamLegality:
    """対戦ごとに引く経路の合法性の検査を用意する。最初の 1 件で validate-team が動くことを確かめ、動かなければ起動しない
    (動かないまま始めると全件を飛ばすか、拒否で止まる)"""
    legality = TeamLegality(battle_format)
    try:
        if texts:
            legality(texts[0])
    except ValidatorUnavailable as e:
        raise SystemExit(f"自チームの合法性を検査できない (pokemon-showdown の validate-team): {e}")
    return legality


def resolve_rl_blend(rl_blend: float | None, no_rl_blend: bool, default: float) -> float:
    """測定で使う RL 加点の重み (純粋)。--no-rl-blend は 0 の別名、どちらも無ければ既定値。
    両方を矛盾する値で指定したらエラー (どちらを測ったか manifest と食い違わないように)"""
    if no_rl_blend:
        if rl_blend is not None and float(rl_blend) != 0.0:
            raise SystemExit(f"--no-rl-blend と --rl-blend {rl_blend} は同時に指定できない")
        return 0.0
    return float(default) if rl_blend is None else float(rl_blend)


def opp_policy_info(bench) -> Optional[dict]:
    """相手の操縦者が学習済み方策 (ModelPlayer の BattlePolicy) を読んだか (純粋)。方策を持たない操縦者 (heuristic) は None"""
    pol = getattr(bench, "policy", None)
    if pol is None:
        return None
    mp = getattr(pol, "model_path", None)
    return {"model_path": str(mp) if mp is not None else None, "loaded": getattr(pol, "model", None) is not None,
            "stats": dict(getattr(pol, "stats", None) or {})}


def models_dir_report() -> dict:
    """各モジュールが読む models dir (import して確かめる。--print-models-dir 用)"""
    from advisor import rl_bridge
    from champions_agent.agent import policy_battle, selection_model
    return {"env": os.environ.get(MODELS_DIR_ENV), "config": str(_CFG.MODELS_DIR),
            "rl_bridge": str(rl_bridge.CKPT_DIR), "policy_battle": str(policy_battle.MODELS_DIR),
            "selection_model_emb": str(selection_model.EMB_PIN_PATH.parent)}


async def run(n_battles: int, opp_seed: int | None, json_out: str | None,
              skip_random: bool, belief_k: int | None, sensor_q: float | None,
              workers: int | None, no_rl_blend: bool,
              search_blend: float | None = None,
              team_file: str | None = None,
              pick_policy: str = "advisor", selection_model: str | None = None,
              opp_split: str | None = None, battle_log: str | None = None,
              pick_noise: float = 0.0, action_noise: float = 0.0,
              candidate_id: str | None = None,
              opp_offset: int = 0, selection_plan: str | None = None,
              opp_pilot: str | None = None, opp_pick_policy: str | None = None,
              rl_blend: float | None = None, battle_format: str | None = None,
              own_pool: str | None = None, stall_sec: float | None = None) -> None:
    from poke_env import AccountConfiguration
    from poke_env.player import RandomPlayer
    import advisor.engine as eng
    from champions_agent.env.advisor_player import make_advisor_player, transition_timing
    from champions_agent.env.ranked_teams import (
        RankedTeambuilder, pinned_meta_snapshot_id)
    from champions_agent.config import BUILD_OPP_PICK_POLICY, BUILD_OPP_PILOT
    from champions_agent.env.showdown_env import TrainingServerConfiguration, make_benchmark_player
    from tools.team_build.pilot import make_opponent_player
    opp_pilot = opp_pilot or BUILD_OPP_PILOT
    # 対戦のフォーマット (2026-10-09: 過去の判定の再測定で当時の M-B を指定するため。既定は学習と同じ TRAINING_BATTLE_FORMAT)
    battle_format = battle_format or TRAINING_BATTLE_FORMAT
    opp_pick_policy = opp_pick_policy or BUILD_OPP_PICK_POLICY

    if belief_k is not None:
        eng.BELIEF_K = belief_k
    if sensor_q is not None:
        eng.SENSOR_Q_DEFAULT = sensor_q
    if workers is not None:
        eng.SEARCH_WORKERS = workers
    rl_blend_weight = resolve_rl_blend(rl_blend, no_rl_blend, eng.RL_BLEND_DEFAULT)
    if search_blend is not None:
        eng.SEARCH_BLEND = search_blend
    meta_pin = pinned_meta_snapshot_id()
    own_pool_info = None
    legality = None
    opp_legality = None
    stats: dict = {}
    latencies: list = []
    uid = os.getpid() % 100000
    if team_file and own_pool:
        raise SystemExit("--team-file と --own-pool は同時に指定できない")
    if team_file:
        team_text = Path(team_file).read_text(encoding="utf-8")
        # 不正なチームだと Showdown が拒否し poke-env が対戦を待ち続けるので、先に合法性を検査して即終了する
        try:
            from tools.team_build.sets import validate_team_text
            ok, errs = validate_team_text(team_text, battle_format)
        except Exception:
            ok, errs = True, []
        if not ok:
            raise SystemExit(f"チーム本文が不正 (validate-team): {errs[:3]}")
        own_tb = _constant_teambuilder(team_text)
    elif own_pool:
        # 実験専用の構築プール (2026-10-09 M2d、tools/scoring_pool): RankedTeambuilder と同じく Random(相手 seed + 1) で対戦ごとに引く
        if opp_seed is None:
            raise SystemExit("--own-pool には --opp-seed が要る (条件間で自チームの列を対応させるため)")
        from tools.scoring_pool import load_pool
        try:
            pool_texts, pool_doc = load_pool(own_pool)
        except ValueError as e:
            raise SystemExit(f"--own-pool が不正: {e}")
        own_pool_info = {"path": str(own_pool), "sha16": pool_doc.get("sha16"), "n": len(pool_texts),
                         "snapshot_id": (pool_doc.get("conditions") or {}).get("snapshot_id"),
                         "battle_format": (pool_doc.get("conditions") or {}).get("battle_format"),
                         "skipped": int(opp_offset)}
        legality = _start_legality(battle_format, pool_texts)
        # 抽選の位置を相手列と同じ offset だけ進める (2026-10-09 M2e: 2 夜分割の part k の自チームを、同じ seed の列の続きにする)。
        # 進める数は対戦の数 (不合法を飛ばす列で数える。pool_rng の説明)
        own_tb = _remembering_teambuilder(
            _PoolSource(pool_texts, pool_rng(opp_seed + 1, pool_texts, opp_offset, is_legal=legality)),
            legality=legality, stats=stats)
    else:
        ranked = RankedTeambuilder(
            rng=random.Random(opp_seed + 1) if opp_seed is not None else None,
            meta_snapshot_id=meta_pin)
        # 対戦ごとに引くチームを対戦フォーマットで検査し、不合法なら飛ばす (2026-10-09: M-B の再測定が 7 戦目で停滞)
        legality = _start_legality(battle_format, ranked.teams)
        own_tb = _remembering_teambuilder(ranked, legality=legality, stats=stats)
    # 相手列: --opp-split FILE:TIER[:FOLD] なら系統分割の階層から決定的な相手列 (対応比較用)、
    # 無ければ従来どおり上位 60 構築からシードで抽選
    opp_team, split_info, family_of = None, None, {}
    if opp_split:
        from tools.team_build.opponents import (
            SequenceTeambuilder, load_split, opponent_sequence, tier_ids)
        parts = opp_split.split(":")
        doc = load_split(Path(parts[0]))
        tier = parts[1] if len(parts) > 1 else "selection"
        fold = int(parts[2]) if len(parts) > 2 else None
        # 相手列は「階層の全構築を seed で並べた列」を offset から n 戦ぶん使う (追加測定は続きから)。
        # 対戦フォーマットで不合法な構築は飛ばす (2026-10-09)。offset と n は対戦の数で数えるので、飛ばす分の余裕を持たせた長さで列を作る
        # (opponent_sequence は長さを伸ばしても先頭が同じ列)
        ids = tier_ids(doc, tier, fold)
        opp_legality = _start_legality(battle_format, [doc["texts"][i] for i in ids[:1]])
        seq = opponent_sequence(ids, 2 * (opp_offset + n_battles) + len(ids), opp_seed or 0)
        opp_team = SequenceTeambuilder(seq, doc["texts"], offset=opp_offset, is_legal=opp_legality,
                                       on_skip=lambda sk: record_skips(stats, sk, OPP_STATS))
        family_of = {tid: f["family_id"] for f in doc["families"] for tid in f["teams"]}
        split_info = {"file": parts[0], "tier": tier, "fold": fold, "offset": opp_offset,
                      "sealed_id": doc.get("sealed_id"), "run_id": doc.get("run_id")}
    elif opp_seed is not None:
        opp_ranked = RankedTeambuilder(top_n=60, include_external=False,
                                       rng=random.Random(opp_seed),
                                       meta_snapshot_id=meta_pin)
        # 相手も対戦フォーマットで不合法なチームは飛ばす (2026-10-09: M-B では上位 60 のうち 1 件が不合法で、対戦 17 で止まる)。
        # 同じ seed の腕どうしは同じ判定で同じだけ飛ばすので、相手列の対応は保たれる
        opp_legality = _start_legality(battle_format, opp_ranked.teams)
        opp_team = _remembering_teambuilder(opp_ranked, legality=opp_legality, stats=stats, stat_prefix=OPP_STATS)
    recorder = None
    if battle_log:
        from tools.team_build.battle_log import BattleRecorder
        recorder = BattleRecorder(
            Path(battle_log), candidate_team_id=candidate_id or (team_file or "ranked"),
            advisor_policy_id=(f"rl:{os.environ.get('CHAMPIONS_MODELS_DIR', 'default')}"
                               f"|sel:{selection_model or 'default'}|pick:{pick_policy}|opp:{opp_pilot}/{opp_pick_policy}"),
            user_policy="advisor", battle_seed=opp_seed, family_of=family_of)
    plan = None
    if selection_plan:
        from tools.team_build.plan_prior import load_plan
        plan = load_plan(selection_plan) or None
    player = make_advisor_player(
        team_source=own_tb, stats=stats, latencies=latencies,
        pick_policy=pick_policy, selection_model_path=selection_model,
        pick_noise=pick_noise, action_noise=action_noise,
        rng=random.Random((opp_seed or 0) + 7), recorder=recorder, opp_source=opp_team,
        selection_plan=plan, family_of=family_of, rl_blend_weight=rl_blend_weight,
        account_configuration=AccountConfiguration(f"ADv{uid}", None),
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=own_tb)
    # 環境チームの操縦 (2026-10-05): 行動は opp_pilot (heuristic / rl)、選出は opp_pick_policy。全腕で同じ設定 (racing が渡す)
    bench = make_opponent_player(opp_pilot, opp_team, battle_format, AccountConfiguration(f"ADo{uid}", None),
                                 pick_policy=opp_pick_policy, rng=random.Random((opp_seed or 0) + 11))
    # 黙って待ち続けない (2026-10-09): チームの拒否の popup と、Showdown から何も届かない状態 (stall_sec) で異常終了する
    guard = BattleGuard(_CFG.ADVISOR_PLAYER_STALL_SEC if stall_sec is None else stall_sec,
                        describe=lambda: f"完了 {sum(1 for b in player.battles.values() if b.finished)}/{n_battles} 戦")
    guard.wrap(player)
    guard.wrap(bench)
    guard.start()
    t0 = time.time()
    await player.battle_against(bench, n_battles=n_battles)
    dt = time.time() - t0
    outcomes = [1 if b.won else 0 for b in player.battles.values()]
    lat = sorted(latencies)
    p50 = lat[len(lat) // 2] if lat else 0.0
    p95 = lat[int(len(lat) * 0.95)] if lat else 0.0
    n_dec = stats.get("decide", 0) + stats.get("fallback", 0)
    print(f"=== 助言エンジン (belief_k={eng.BELIEF_K} sensor_q={eng.SENSOR_Q_DEFAULT} "
          f"search_blend={eng.SEARCH_BLEND} "
          f"workers={eng.SEARCH_WORKERS} rl_blend={rl_blend_weight:g} "
          f"meta={meta_pin or 'latest'} format={battle_format}) vs ベンチマーク {n_battles}戦 ({dt:.0f}s) ===")
    print(f"勝率: {player.n_won_battles / n_battles:.2f}")
    print(f"助言レイテンシ: p50 {p50:.0f}ms / p95 {p95:.0f}ms ({len(lat)}決定)")
    print(f"意思決定: 助言{stats.get('decide', 0)} / フォールバック{stats.get('fallback', 0)} "
          f"({stats.get('fallback', 0) / max(1, n_dec):.0%}) / 例外{stats.get('error', 0)} / "
          f"蘇生先の選択{stats.get('revive_select', 0)}")
    if stats.get("last_error"):
        print(f"直近の例外: {stats['last_error']}")
    print(f"受入監査: {sim_accept_summary(stats)}")
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(json.dumps({
            "n_battles": n_battles, "wins": player.n_won_battles,
            "win_rate": player.n_won_battles / n_battles, "outcomes": outcomes,
            "belief_k": eng.BELIEF_K, "sensor_q": eng.SENSOR_Q_DEFAULT,
            "workers": eng.SEARCH_WORKERS, "search_blend": eng.SEARCH_BLEND,
            "team_file": team_file, "candidate_id": candidate_id,
            "pick_policy": pick_policy, "selection_model": selection_model, "selection_plan": selection_plan,
            "opp_split": split_info, "battle_log": battle_log,
            "pick_noise": pick_noise, "action_noise": action_noise,
            "opp_pilot": opp_pilot, "opp_pick_policy": opp_pick_policy,
            "models_dir": os.environ.get("CHAMPIONS_MODELS_DIR"),
            # 2026-10-15 M3c: 実際に読んだ dir (config.MODELS_DIR = 相手の操縦 rl・選出モデルのピン) と相手の操縦の方策の読み込み
            # (loaded = False ならランダム合法手。stats の random_fallback がその手数)
            "config_models_dir": str(_CFG.MODELS_DIR),
            "opp_pilot_policy": opp_policy_info(bench),
            "rl_blend": rl_blend_weight,
            # 採点方式 (2026-10-09 M2b。環境変数 ADVICE_SCORING_MODE → config)。transition の退避の件数は stats の scoring_fallback*
            "advice_scoring_mode": _CFG.ADVICE_SCORING_MODE,
            # 2026-10-09 M2d: 感度の計算の有無 (環境変数 TRANSITION_SENSITIVITY → config)、自チームのプール (--own-pool)、
            # transition の本採点と全処理時間 (感度込み) の p50 / p95
            "transition_sensitivity": _CFG.TRANSITION_SENSITIVITY_ENABLED,
            "own_pool": own_pool_info,
            # 2026-10-09: 対戦ごとに引く自チームの対戦フォーマットでの検査 (検査したチーム数・秒)。飛ばした件数と理由は stats の
            # own_team_skipped / own_team_skip_reasons (飛ばした分だけ抽選の列が進む: draw_legal の説明)
            "own_team_legality": ({"battle_format": legality.battle_format, "checked": legality.n_checked,
                                   "sec": round(legality.sec, 1)} if legality is not None else None),
            # 相手側も同じ (2026-10-09)。飛ばした件数と理由は stats の opp_team_skipped / opp_team_skip_reasons
            "opp_team_legality": ({"battle_format": opp_legality.battle_format, "checked": opp_legality.n_checked,
                                   "sec": round(opp_legality.sec, 1)} if opp_legality is not None else None),
            "transition_timing": transition_timing(stats),
            # 2026-10-15 M3a: 状態変換の版 (修正前の結果を混ぜないための印) と受入監査 (第一候補の合法性・未選出の候補・説明できない
            # 飛ばし・ランダム退避・選出不明の件数。champions_agent.env.sim_accept)
            "inputs_version": SIM_INPUTS_VERSION,
            "accept": sim_accept_summary(stats),
            # 2026-10-16 E-holdout-1: さいきのいのり の蘇生先を固定規則で選んだ決定数 (助言の決定 decide には数えない。
            # stats の revive_select / accept:revive_select と同じ値)
            "revive_select": int(stats.get("revive_select", 0)),
            "opp_seed": opp_seed, "meta_snapshot": meta_pin, "battle_format": battle_format,
            "latency_p50_ms": round(p50, 1), "latency_p95_ms": round(p95, 1),
            "stats": {k: v for k, v in stats.items()
                      if k not in ("last_error",) and not str(k).startswith("_")},
            "elapsed_s": round(dt, 1),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {json_out}")
    if skip_random:
        return
    rand = RandomPlayer(
        account_configuration=AccountConfiguration(f"ADr{uid}", None),
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(meta_snapshot_id=meta_pin))
    bench2 = make_benchmark_player(
        battle_format=battle_format,
        account_configuration=AccountConfiguration(f"ADr2{uid}", None))
    guard.wrap(rand)
    guard.wrap(bench2)
    guard.touch()
    await rand.battle_against(bench2, n_battles=n_battles)
    print(f"--- 基準線: RandomPlayer 勝率 {rand.n_won_battles / n_battles:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="助言エンジンの実戦診断")
    ap.add_argument("--battles", type=int, default=20)
    ap.add_argument("--opp-seed", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-random", action="store_true")
    ap.add_argument("--belief-k", type=int, default=None)
    ap.add_argument("--sensor-q", type=float, default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-rl-blend", action="store_true", help="RL 加点を切る (--rl-blend 0 の別名)")
    ap.add_argument("--rl-blend", type=float, default=None,
                    help="RL の行動確率の加点の重み (既定 = config RL_BLEND_WEIGHT_DEFAULT、起動時の環境変数 RL_BLEND_WEIGHT があればその値)")
    ap.add_argument("--search-blend", type=float, default=None,
                    help="探索の推奨値をスコアへ統合する重み (P9)。0=無効")
    ap.add_argument("--pick-policy", choices=["advisor", "rule", "teampreview"], default="advisor",
                    help="選出方策: advisor = 実助言と同じ (選出モデル → 相性の規則) / rule = 相性の規則だけ / teampreview = 従来の簡易相性順")
    ap.add_argument("--opp-pilot", choices=["heuristic", "rl"], default=None,
                    help="環境チーム (相手) の操縦: heuristic = poke-env SimpleHeuristicsPlayer / rl = 学習済み行動方策 (既定 config BUILD_OPP_PILOT)")
    ap.add_argument("--opp-pick-policy", choices=["heuristic", "matchup", "rule", "model", "prior"], default=None,
                    help="環境チームの選出 (既定 config BUILD_OPP_PICK_POLICY)")
    ap.add_argument("--selection-model", default=None, help="候補専用の選出モデル (.pt)")
    ap.add_argument("--selection-plan", default=None,
                    help="構築の選出計画 (s06_sets/<cid>.plan.json)。advisor の選出でモデルの予測に計画の事前を足す (--opp-split が要る)")
    ap.add_argument("--opp-split", default=None,
                    help="相手列: opponent_families.json のパス:階層[:fold] (例 runs/x/opponent_families.json:selection)")
    ap.add_argument("--battle-log", default=None, help="対戦記録 (JSONL) の出力先")
    ap.add_argument("--pick-noise", type=float, default=0.0, help="STRESS: 選出を乱択する確率")
    ap.add_argument("--action-noise", type=float, default=0.0, help="STRESS: 2位の手を選ぶ確率")
    ap.add_argument("--models-dir", default=None,
                    help="RL のピン dir。import の前に CHAMPIONS_MODELS_DIR を同じ値にする (rl_bridge・相手の操縦 rl・選出モデル)")
    ap.add_argument("--print-models-dir", action="store_true",
                    help="各モジュールが読む models dir を JSON で表示して終わる (対戦しない)")
    ap.add_argument("--candidate-id", default=None, help="対戦記録に付ける候補 id")
    ap.add_argument("--opp-offset", type=int, default=0,
                    help="相手列の開始位置 (racing の追加測定で同じ相手列の続きを使う)")
    ap.add_argument("--battle-format", default=None,
                    help="対戦のフォーマット id (既定 config TRAINING_BATTLE_FORMAT)。過去の判定の再測定で当時の M-B (gen9championsbssregmb) を指定する")
    ap.add_argument("--team-file", default=None,
                    help="自分側を固定チーム (Showdownテキスト) にする (構築の操縦しやすさ測定)")
    ap.add_argument("--stall-sec", type=float, default=None,
                    help="Showdown から何も届かない秒数がこれを超えたら異常終了する (既定 config ADVISOR_PLAYER_STALL_SEC、0 で見張らない)")
    ap.add_argument("--own-pool", default=None,
                    help="自分側を実験専用の構築プール (tools.scoring_pool のファイル) から Random(相手 seed + 1) で対戦ごとに引く")
    args = ap.parse_args()
    if args.models_dir:
        # 環境変数は import の前に設定済み (apply_models_dir_env)。main() を import して呼ぶなど、設定が間に合わず config が
        # 別の dir を読んでいたら測定条件が崩れるので止める
        want = Path(args.models_dir).resolve()
        if os.environ.get(MODELS_DIR_ENV) != str(want) or Path(_CFG.MODELS_DIR).resolve() != want:
            raise SystemExit(f"--models-dir {want} が config.MODELS_DIR ({_CFG.MODELS_DIR}) に届いていない。"
                             f"python -m tools.check_advisor_player で起動するか、環境変数 {MODELS_DIR_ENV} を設定してから起動する")
    if args.print_models_dir:
        print(json.dumps(models_dir_report(), ensure_ascii=False))
        return
    asyncio.run(run(args.battles, args.opp_seed, args.json, args.skip_random,
                    args.belief_k, args.sensor_q, args.workers, args.no_rl_blend,
                    search_blend=args.search_blend, team_file=args.team_file,
                    pick_policy=args.pick_policy, selection_model=args.selection_model,
                    opp_split=args.opp_split, battle_log=args.battle_log,
                    pick_noise=args.pick_noise, action_noise=args.action_noise,
                    candidate_id=args.candidate_id,
                    opp_offset=args.opp_offset, selection_plan=args.selection_plan,
                    opp_pilot=args.opp_pilot, opp_pick_policy=args.opp_pick_policy,
                    rl_blend=args.rl_blend, battle_format=args.battle_format, own_pool=args.own_pool,
                    stall_sec=args.stall_sec))


if __name__ == "__main__":
    main()
