"""有用性検証 P1: RL の行動確率の加点 0 / ×5 / ×25 の対応比較を起動する (docs/USEFULNESS_VERIFICATION_PLAN_1007.md §0.5・§4、2026-10-09)。

    python -m tools.usefulness_p1 --prelim --dry-run          # 条件表とコマンド列を出すだけ (起動しない・ピンを作らない)
    python -m tools.usefulness_p1 --compare scoring --prelim --dry-run   # 採点方式 scoring:legacy / scoring:transition (M2b)
    python -m tools.usefulness_p1 --compare scoring --team registered --prelim --dry-run   # 自チームを最新の登録チームにする
    python -m tools.usefulness_p1 --prelim                     # 予備 50 戦 / 条件 (秒/戦の見積もり)
    python -m tools.usefulness_p1                              # 本番 600 戦 / 条件
    python -m tools.usefulness_verdict --out <出力 dir>        # 集計と §0.5 の 3 値判定

3 条件 (重み 0 / 5 / 25) を tools.check_advisor_player で 3 プロセス並列に回す。同じ相手列 (fold B、seed 固定) なので対戦 i が対応する。
シミュレータの状態から直接助言を計算する (OCR を通らない) ので、分かるのはシミュレーション条件下での RL 加点の効果で、実戦の有用性とは分ける。

起動前に条件表 conditions.json (§0.5 の「開始前に固定する項目」+ 版と経路の条件) を書く。取得できない項目は null にして理由を notes に残す。
起動前の検査 (不合格なら起動しない): 登録チームの本文の sha16 が期待値と一致 (一般的なチームは META_PIN) / 選出モデル・分割ファイルがある /
ピンに RL の checkpoint がある / Showdown (SHOWDOWN_PORT) が待ち受けている / 出力先に前の結果が無い。
環境変数: CHAMPIONS_MODELS_DIR = ピン (相手の rl は config.MODELS_DIR を読むので --models-dir だけでは届かない)、スレッド数 1 の一式
(tools.team_build.racing.child_env と同じ)。
時間上限 (P1_TIME_LIMIT_SEC) を過ぎたら 3 プロセスを止めて incomplete: true (採否は出さない)。
--compare scoring (2026-10-09 M2b 設計判断 11): 条件は採点方式 scoring:legacy (基準) / scoring:transition の 2 つ。重みは両方とも
既定 (P1_BASELINE_WEIGHT)、方式は条件ごとの環境変数 ADVICE_SCORING_MODE (条件表の cond_env) で子プロセスに渡す。ファイル名は
条件名の「:」を「_」にしたもの (scoring_legacy.json 等)。集計 (tools.usefulness_verdict) は条件表の factor scoring を見て
基準 scoring:legacy、比較 transition − legacy の 1 本 (k = 1) で判定する。
--team general|registered (自チームの出所。2026-10-09 ユーザー判断: 採点方式の比較は一般的なチームをベースに行う):
  - general (--compare scoring の既定): --team-file を渡さない = check_advisor_player の既定の自チーム (RankedTeambuilder。
    META_PIN の使用率スナップショットの構築プールから rng = Random(相手 seed + 1) で対戦ごとに引く)。seed が条件間で同じなので
    対戦 i の自チームも対応する。助言は自分側の種族名が登録チームと一致すると登録の型 (努力値・性格・持ち物・技) を使うので、
    全条件の子プロセスに MY_TEAM_DISABLED_ENV=1 (登録参照の無効化。条件ごとの cond_env とは別の共通の環境) を立てる。
    起動前の検査は登録チームの sha16 の代わりに META_PIN の snapshot id があること (構築プールが空でないこと) を見る。
  - registered (--compare rl の既定): config/my_team.json (最新の登録) の本文を team.txt に書いて --team-file で渡す。
    sha16 の期待値は --compare rl が P1_TEAM_SHA16 (P1 の記録)、--compare scoring が P1_SCORING_TEAM_SHA16。違えば起動しない。
  条件表の team.source (ranked / registered)・purpose・notes に自チームの出所を残す。--compare rl --team registered の条件表は従来と同じ。
2026-10-09 M2d (レビュー 2〜4・6):
  - --team general は実験専用の構築プール (tools.scoring_pool で最新の使用率スナップショットから合成し、現行規制の validate-team で
    合法性を確かめたファイル) を --own-pool で子プロセスに渡す (team.source pool、パスと sha16 を条件表に、起動前検査で sha16 と件数)。
    旧固定プール (META_PIN) で測る従来の general は --team general-pinned (条件表に「旧固定プール上の比較」と明記)。
  - --compare scoring は子プロセスで感度の計算を切る (TRANSITION_SENSITIVITY=0、全条件で共通)。
  - 条件表の showdown に git commit・未コミットの変更・フォーマットの name / mod / ruleset を残し、commit と mod が取れなければ起動しない。
  - --compare scoring の条件表に own_check (自チームの対応の検査。usefulness_verdict が読む) を書く。--prelim-dir (予備測定の出力) を
    渡すと、その相手 id と自チームの対応が成り立つことを起動前に確かめる (渡さなければ注意だけ)。
2026-10-09 M2e (レビューの推奨。採点方式の比較に限る。RL の比較の既定値は変えない):
  - --compare scoring の時間上限は P1_SCORING_TIME_LIMIT_SEC (600 戦 / 条件の全体で 180 分)。予備測定の後の本番の開始可否は
    「最も遅い条件の秒/戦 × P1_FINAL_BATTLES + P1_SCORING_START_MARGIN_SEC ≤ P1_SCORING_TIME_LIMIT_SEC」(prelim_advice の
    time_limit_sec / margin_sec)。RL の比較は従来どおり P1_ABORT_SEC_PER_BATTLE (12 秒/戦) の基準。
  - --part k/n (2 夜分割): 事前に固定した 600 対戦の列 (相手列 = 分割の階層を seed で並べた列、自チームの列 = --own-pool の
    Random(相手 seed + 1) の抽選の列) を n 個の連続した範囲に重複なく分け、k 番目の範囲だけを両条件で測る (相手は --opp-offset =
    範囲の先頭、自チームの抽選も check_advisor_player が同じ数だけ進める)。各 part の時間上限は全体の上限の按分。条件表の part に
    範囲を残し、tools.usefulness_verdict に全 part の出力を渡して結合し、最終判定は 1 回だけ出す (各 part に両条件が揃い、範囲が
    重複しないことを検査)。予備測定 (--prelim) と旧固定プール (--team general-pinned、抽選を進められない) には使えない。
2026-10-15 レビュー (封印 holdout 層での 1 回の確認、docs/ADVICE_ACCURACY_PLAN_1009.md §8.22):
  - --tier search|holdout (既定 search = 従来どおり fold B)。holdout は --seed の明示が要る (search の P1_SEED とは別)。fold は使わない
    (--opp-split は FILE:holdout、条件表の opponents に fold / fold_label を出さず tier holdout と n_teams_in_tier)。相手列は
    holdout 層の全 id を seed で並べた列 (opponent_sequence)。purpose に「封印 holdout 層での 1 回の確認」。--prelim は不可、--part は可
    (分割しても判定は 1 回)。既定の出力先は p1_holdout_<YYYYMMDD_HHMM>。
  - 起動前検査 (holdout): 各条件のコマンドの --opp-split / --opp-seed / --opp-offset / --battles から check_advisor_player と同じ
    相手列を作り、全件が holdout 層の id で本文があり、search / selection 層と重ならないこと。開封履歴 (holdout_usage.jsonl、
    tools.team_build.holdout.USAGE_LOG。--holdout-usage-log で替えられる) に、同じ封印 id (分割の sealed_id) で候補の鍵
    (比較の条件名を + でつないだもの @ git commit の先頭 8 桁、例 scoring:transition@cc0c8029) の方式名 (「@」より前) が一致する行が
    あれば、commit が違っても「使用済み」で起動しない (同じ方式で commit を変えて測り直すのも止める)。例外は 2 夜分割の別の part
    (同じ seed・同じ分割数・違う k) の行だけで、同じ 1 回の確認の続きとして許す。
    本番の起動時 (検査を通った後・子プロセスの起動前) に 1 行追記する: {sealed_id, candidate_key, run_id = 出力 dir 名, at, purpose,
    part, seed, kind}。dry-run では追記しない。
開始時刻の注意 (ツールでは強制しない): launchd の track-progress (毎日 21:50、17〜34 分) の後に始め、06:30 の usage-update の前に終える。

純粋な部分 (条件表・コマンド・環境・進捗・終了時の更新) は tests/test_usefulness_p1.py で確かめる。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from champions_agent.config import (
    BUILD_FOLD_ADAPT, BUILD_FOLD_EVAL, BUILD_FOLD_VALIDATE, P1_ABORT_SEC_PER_BATTLE, P1_ALPHA, P1_BASELINE_WEIGHT, P1_BELIEF_K,
    P1_FINAL_BATTLES, P1_INTERIM_BATTLES, P1_MDE, P1_OPP_OFFSET, P1_OPP_PICK_POLICY, P1_OPP_PILOT, P1_OUT_ROOT, P1_PICK_POLICY,
    P1_PRELIM_BATTLES, P1_PROGRESS_SEC, P1_SEED, P1_SELECTION_MODEL, P1_SPLIT_FILE, P1_SPLIT_SEALED_ID, P1_SPLIT_TIER,
    P1_SCORING_BASELINE, P1_SCORING_MODES, P1_SCORING_POOL_FILE, P1_SCORING_POOL_SHA16, P1_SCORING_START_MARGIN_SEC,
    P1_SCORING_TEAM_SHA16, P1_SCORING_TIME_LIMIT_SEC, P1_TEAM_DEFAULT,
    P1_TEAM_SHA16, P1_TEAM_SOURCES, P1_TERMINATE_GRACE_SEC, P1_TIME_LIMIT_SEC, P1_WEIGHTS, MY_TEAM_DISABLED_ENV, SHOWDOWN_PORT,
    SIM_ACCEPT_LIMITS, SIM_INPUTS_VERSION, TRAINING_BATTLE_FORMAT, TRANSITION_SENSITIVITY_ENV)
from tools.usefulness_verdict import HOLDOUT_TIER, TEAM_LABELS, cond_file, cond_name, corrected_z, holdout_banner

REPO = Path(__file__).resolve().parent.parent
FOLD_LABELS = {BUILD_FOLD_ADAPT: "A", BUILD_FOLD_EVAL: "B", BUILD_FOLD_VALIDATE: "V"}
# 相手の層 (2026-10-15 レビュー): search = fold で選ぶ方式の比較用 (従来)、holdout = 封印 holdout 層での 1 回の確認
TIERS = (P1_SPLIT_TIER, HOLDOUT_TIER)
HOLDOUT_USAGE_KIND = "usefulness_p1"
# 経路に効く環境変数 (設定されていれば条件表に残す)
PATH_ENV_VARS = ("RL_ADVICE_STYLE", "RL_POLICY_SOURCE", "RL_BLEND_WEIGHT", "SELECTION_FEATURES", "SHOWDOWN_PORT",
                 "ADVICE_SCORING_MODE")
SCHEDULE_NOTE = "launchd の track-progress (毎日 21:50、17〜34 分) が終わってから始め、06:30 の usage-update の前に終える"
PIN_PLANNED = "(起動時に scripts/pin_models.sh で作成)"
CKPT_DIR = REPO / "champions_agent" / "train" / "checkpoints"
SELECTION_EMB_FILE = "selection_model_emb.json"    # 選出モデルの機能埋め込みのピン (selection_model.EMB_PIN_PATH の名前)
SELECTION_META_FILE = "selection_model_meta.json"
# 一般的なチーム (--team general) の構築プール: tools.check_advisor_player の既定の自チーム (--team-file なし) は
# RankedTeambuilder(rng=Random(相手 seed + 1), meta_snapshot_id=META_PIN) で、top_n / include_external は RankedTeambuilder の既定
# (全件 / 外部構築を含む)。ここでは条件表に残すために同じ値を持つ (check_advisor_player を変えたら合わせる)
RANKED_TOP_N = None
RANKED_INCLUDE_EXTERNAL = True
RANKED_SEED_OFFSET = 1
# 一般的なチーム (登録参照を無効化する自チームの出所)。general = 実験専用の構築プール (--own-pool、2026-10-09 M2d) /
# general-pinned = 旧固定プール (META_PIN の RankedTeambuilder。条件表に「旧固定プール上の比較」と明記する)
GENERAL_SOURCES = ("general", "general-pinned")
PINNED_NOTE = "旧固定プール上の比較 (META_PIN / POOL_PIN の 7 月の構築プール。回帰確認用で、現行規制での採点方式の選定には使わない)"
# 自チームの対応の検査 (2026-10-09 M2d、レビュー 6。条件表の own_check。tools/usefulness_verdict が読む)
OWN_CHECK = {"required": True,
             "items": ["(a) 対戦 i の送った自チームの本文の sha16 (battles.jsonl の own_team_sha16) が条件間で一致",
                       "(b) 各条件で助言が参照した自分の場の個体の型 (配分・性格・持ち物・技。メガ後も) が送った型と一致 (ref_check.mismatch = 0)",
                       "(c) 確認できない対戦 (own_team_sha16 / ref_check が無い・検査 0 件) が無い",
                       "(e) 対戦中に選出が分からず全個体を選出扱いにした決定 (accept.picked_unknown、入力不成立) を含む対戦が無い"]}
# 測定の受入監査 (2026-10-15 M3a。条件表の accept_check。tools/usefulness_verdict が全比較 (RL・採点方式) で読む)
ACCEPT_CHECK = {"required": True, "inputs_version": SIM_INPUTS_VERSION, "limits": dict(SIM_ACCEPT_LIMITS),
                "items": ["(a) 第一候補が poke-env の合法手に含まれる (best_illegal)",
                          "(b) 候補に未選出の個体への交代が無い (unpicked_candidate)",
                          "(c) 第一候補を説明できない理由で飛ばしていない (skip_unexplained)",
                          "(d) ランダム行動への退避が無い (random_fallback)",
                          "全条件の結果の inputs_version が今の版で揃っている"]}


# ------------------------------------------------------------------ 純粋
def plan(weights: Sequence[float] = P1_WEIGHTS, baseline: float = P1_BASELINE_WEIGHT, alpha: float = P1_ALPHA) -> dict:
    """比較の設計 (純粋): 条件名、基準、比較の組、k、区間の水準と両側 z"""
    names = [cond_name(w) for w in weights]
    base = cond_name(baseline)
    if base not in names:
        raise ValueError(f"基準 ×{baseline:g} が重み {list(weights)} に無い")
    comps = [{"cond": cond_name(w), "weight": float(w), "vs": base} for w in weights if float(w) != float(baseline)]
    k = len(comps)
    return {"weights": [float(w) for w in weights], "conditions": names, "baseline_weight": float(baseline), "baseline": base,
            "comparisons": comps, "k": k, "alpha": float(alpha), "level": 1.0 - float(alpha) / max(1, k),
            "z": corrected_z(k, alpha)}


def scoring_cond_name(mode: str) -> str:
    """採点方式 → 条件名 (legacy → scoring:legacy)"""
    return f"scoring:{mode}"


def plan_scoring(modes: Sequence[str] = P1_SCORING_MODES, baseline: str = P1_SCORING_BASELINE,
                 alpha: float = P1_ALPHA, weight: float = P1_BASELINE_WEIGHT) -> dict:
    """採点方式の比較の設計 (純粋。2026-10-09 M2b)。条件ごとの RL 加点の重み (cond_weight) と環境変数 (cond_env) を持つ"""
    names = [scoring_cond_name(m) for m in modes]
    base = scoring_cond_name(baseline)
    if base not in names:
        raise ValueError(f"基準 {baseline} が方式 {list(modes)} に無い")
    comps = [{"cond": scoring_cond_name(m), "mode": m, "vs": base} for m in modes if m != baseline]
    k = len(comps)
    return {"factor": "scoring", "modes": list(modes), "weights": [float(weight)] * len(names), "conditions": names,
            "baseline_weight": float(weight), "baseline": base, "comparisons": comps, "k": k, "alpha": float(alpha),
            "level": 1.0 - float(alpha) / max(1, k), "z": corrected_z(k, alpha),
            "cond_weight": {c: float(weight) for c in names},
            "cond_env": {scoring_cond_name(m): {"ADVICE_SCORING_MODE": m} for m in modes}}


def resolve_team_source(team: Optional[str], compare: str) -> str:
    """自チームの出所 (純粋): 明示があればそれ、無ければ比較ごとの既定 (scoring → general、rl → registered)"""
    src = team or P1_TEAM_DEFAULT.get(compare, "registered")
    if src not in P1_TEAM_SOURCES:
        raise ValueError(f"自チームの出所 {src} は {P1_TEAM_SOURCES} に無い")
    return src


def expected_team_sha16(factor: str) -> str:
    """登録チームの本文の期待 sha16 (純粋): RL の比較は P1 の記録 (P1_TEAM_SHA16)、採点方式の比較は P1_SCORING_TEAM_SHA16"""
    return P1_SCORING_TEAM_SHA16 if factor == "scoring" else P1_TEAM_SHA16


def own_team_seed(seed: Optional[int]) -> Optional[int]:
    """一般的なチームの抽選の seed (純粋): check_advisor_player は Random(相手 seed + 1) で対戦ごとに引く"""
    return None if seed is None else int(seed) + RANKED_SEED_OFFSET


def resolve_battles(battles: Optional[int], prelim: bool) -> int:
    """この測定の対戦数 (純粋): 明示があればそれ、無ければ予備 P1_PRELIM_BATTLES / 本番 P1_FINAL_BATTLES"""
    if battles is not None:
        return int(battles)
    return int(P1_PRELIM_BATTLES if prelim else P1_FINAL_BATTLES)


def default_out_dir(now: float, prelim: bool, root: Path = REPO / P1_OUT_ROOT, part: Optional[tuple] = None,
                    tier: str = P1_SPLIT_TIER) -> Path:
    """既定の出力先 (純粋): logs/usefulness/p1_<YYYYMMDD_HHMM> (予備は p1_prelim_...、封印 holdout 層は p1_holdout_...、
    part k/n は末尾に _part<k>of<n>)"""
    stamp = time.strftime("%Y%m%d_%H%M", time.localtime(now))
    name = f"p1_prelim_{stamp}" if prelim else (f"p1_holdout_{stamp}" if tier == HOLDOUT_TIER else f"p1_{stamp}")
    if part:
        name += f"_part{int(part[0])}of{int(part[1])}"
    return Path(root) / name


def parse_part(spec: Optional[str]) -> Optional[tuple]:
    """--part の値 "k/n" → (k, n) (純粋。2026-10-09 M2e)。None はそのまま。1 ≤ k ≤ n でなければ ValueError"""
    if spec is None:
        return None
    try:
        k, n = (int(x) for x in str(spec).split("/", 1))
    except ValueError:
        raise ValueError(f"--part は k/n の形 (例 1/2): {spec}")
    if not (1 <= k <= n):
        raise ValueError(f"--part {spec}: 1 ≤ k ≤ n でない")
    return k, n


def part_range(k: int, n: int, total: int = P1_FINAL_BATTLES) -> tuple:
    """part k/n の対戦の範囲 [start, end) (純粋)。total 戦を n 個の連続した範囲に重複なく分ける (端数は後ろの part に寄せる)"""
    if not (1 <= int(k) <= int(n)):
        raise ValueError(f"part {k}/{n}: 1 ≤ k ≤ n でない")
    return (int(k) - 1) * int(total) // int(n), int(k) * int(total) // int(n)


def part_record(k: int, n: int, total: int = P1_FINAL_BATTLES, base_offset: int = P1_OPP_OFFSET) -> dict:
    """条件表の part (純粋): k / n / 範囲 [start, end) / 対戦数 / 相手列の offset (= base_offset + start) / 全体の対戦数"""
    start, end = part_range(k, n, total)
    return {"k": int(k), "n": int(n), "start": start, "end": end, "battles": end - start, "total_battles": int(total),
            "opp_offset": int(base_offset) + start,
            "note": "事前に固定した対戦の列 (相手列と自チームの抽選の列) の範囲 [start, end)。tools.usefulness_verdict で全 part を結合して 1 回だけ判定する"}


def resolve_time_limit(time_limit_sec: Optional[int], compare: str, part_battles: Optional[int] = None,
                       total: int = P1_FINAL_BATTLES) -> int:
    """この測定の時間上限 (純粋。2026-10-09 M2e)。明示があればそれ。無ければ RL の比較は P1_TIME_LIMIT_SEC (従来どおり)、
    採点方式の比較は P1_SCORING_TIME_LIMIT_SEC (part なら対戦数で按分して切り上げ)"""
    if time_limit_sec is not None:
        return int(time_limit_sec)
    if compare != "scoring":
        return int(P1_TIME_LIMIT_SEC)
    limit = int(P1_SCORING_TIME_LIMIT_SEC)
    if part_battles is not None:
        return -(-limit * int(part_battles) // int(total))
    return limit


def build_command(weight: float, out_dir: Path, team_file: Optional[Path], split_file: Path, fold: Optional[int], seed: int,
                  battles: int, selection_model: Path, pin_dir: str, python: str = sys.executable, offset: int = P1_OPP_OFFSET,
                  tier: str = P1_SPLIT_TIER, cond: Optional[str] = None, own_pool: Optional[Path] = None) -> list:
    """1 条件の tools.check_advisor_player のコマンド (純粋)。cond (条件名) が無ければ重みの条件名 rl<W>。
    team_file が None (一般的なチーム) なら --team-file を渡さない。own_pool (実験専用の構築プール、2026-10-09 M2d) があれば
    --own-pool で渡す (無ければ check_advisor_player の既定 = RankedTeambuilder)。
    fold が None (封印 holdout 層、2026-10-15) なら --opp-split は FILE:TIER (fold なし = 層の全 id)"""
    c = cond_file(cond or cond_name(weight))
    team_args = ["--team-file", str(team_file)] if team_file is not None else []
    if own_pool is not None:
        team_args += ["--own-pool", str(own_pool)]
    split_arg = f"{split_file}:{tier}" if fold is None else f"{split_file}:{tier}:{int(fold)}"
    return [python, "-m", "tools.check_advisor_player",
            "--battles", str(int(battles)), "--opp-seed", str(int(seed)), "--opp-offset", str(int(offset)),
            "--opp-split", split_arg,
            "--opp-pilot", P1_OPP_PILOT, "--opp-pick-policy", P1_OPP_PICK_POLICY, "--pick-policy", P1_PICK_POLICY,
            *team_args, "--selection-model", str(selection_model), "--models-dir", str(pin_dir),
            "--rl-blend", f"{float(weight):g}", "--skip-random", "--belief-k", str(P1_BELIEF_K),
            "--json", str(Path(out_dir) / f"{c}.json"), "--battle-log", str(Path(out_dir) / f"{c}.battles.jsonl"),
            "--candidate-id", f"p1_{c}"]


def command_opponent_spec(cmd: Sequence[str]) -> dict:
    """check_advisor_player のコマンド → 相手列の指定 {split_file, tier, fold, seed, offset, battles} (純粋。2026-10-15)。
    --opp-split FILE:TIER[:FOLD] の読み方は check_advisor_player と同じ (TIER の既定 selection、FOLD の既定 None)"""
    args = list(cmd)

    def val(flag):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None
    split = val("--opp-split")
    parts = split.split(":") if split else []
    return {"split_file": parts[0] if parts else None, "tier": parts[1] if len(parts) > 1 else ("selection" if parts else None),
            "fold": int(parts[2]) if len(parts) > 2 else None,
            "seed": int(val("--opp-seed")) if val("--opp-seed") is not None else None,
            "offset": int(val("--opp-offset") or 0), "battles": int(val("--battles") or 0)}


def planned_opponent_ids(doc: dict, spec: dict) -> list:
    """コマンドの相手列の指定 (command_opponent_spec) → check_advisor_player が作るのと同じ相手列 (純粋)。
    check_advisor_player は不合法な構築を飛ばす余裕を持たせて 2 × (offset + n) + 層の件数 の長さの列を作る (全件を返す)"""
    from tools.team_build.opponents import opponent_sequence, tier_ids
    ids = tier_ids(doc, spec["tier"], spec["fold"])
    return opponent_sequence(ids, 2 * (int(spec["offset"]) + int(spec["battles"])) + len(ids), spec["seed"] or 0)


def opponent_tier_check(seq: Sequence[str], doc: dict, head: int, tier: str = HOLDOUT_TIER) -> dict:
    """相手列が全件 tier 層 (既定 holdout) に属するかの検査 (純粋。2026-10-15)。seq = 実際の相手列 (planned_opponent_ids)、
    head = 先頭から実際に使う件数 (offset + 対戦数。不合法を飛ばすとその先も使うので、層の検査は seq の全件で行う)。
    層に無い id・本文 (texts) の無い id・他の層 (search / selection) と重なる id があれば reasons に"""
    from tools.team_build.opponents import tier_ids
    layer = set(tier_ids(doc, tier))
    others = set()
    for t, v in (doc.get("tiers") or {}).items():
        if t != tier:
            others |= set(v or [])
    texts = doc.get("texts") or {}
    seq = list(seq)
    outside = sorted({x for x in seq if x not in layer})
    no_text = sorted({x for x in seq if x not in texts})
    overlap = sorted(layer & others)
    used = seq[:int(head)]
    reasons = []
    if not layer:
        reasons.append(f"{tier} 層が空")
    if len(seq) < int(head):
        reasons.append(f"相手列が {len(seq)} 件しか無い (必要 {int(head)} 件)")
    if outside:
        reasons.append(f"相手列に {tier} 層に無い id が {len(outside)} 種 ({', '.join(outside[:5])})")
    if no_text:
        reasons.append(f"相手列に本文 (texts) の無い id が {len(no_text)} 種 ({', '.join(no_text[:5])})")
    if overlap:
        reasons.append(f"{tier} 層の id が他の層と重なる: {len(overlap)} 種 ({', '.join(overlap[:5])})")
    return {"tier": tier, "n_tier_ids": len(layer), "n_checked": len(seq), "head": int(head),
            "n_unique_in_head": len(set(used)), "outside": outside, "no_text": no_text, "overlap": overlap, "reasons": reasons}


def holdout_candidate_key(design: dict, git_commit: Optional[str]) -> Optional[str]:
    """開封履歴の候補の鍵 (純粋。2026-10-15): 比較の条件名を + でつないだもの @ git commit の先頭 8 桁
    (例 scoring:transition@cc0c8029)。commit が無ければ None (開封履歴を確かめられない)"""
    conds = [c.get("cond") for c in (design.get("comparisons") or []) if c.get("cond")]
    if not conds or not git_commit:
        return None
    return f"{'+'.join(conds)}@{str(git_commit)[:8]}"


def holdout_usage_check(rows: Sequence[dict], sealed_id: Optional[str], candidate_key: Optional[str],
                        part: Optional[dict] = None, seed: Optional[int] = None) -> dict:
    """開封履歴 (holdout_usage.jsonl の行) の判定 (純粋。2026-10-15)。同じ封印 id で、候補の鍵の方式名 (「@」より前、例
    scoring:transition) が一致する行があれば commit が違っても「使用済み」(reasons。同じ方式で commit を変えて測り直すのも止める)。
    例外は 2 夜分割の別の part (行の part の n が同じで k が違い、seed が同じ) の行だけで、同じ 1 回の確認の続きとして許す
    (continued_parts)"""
    reasons = []
    if not sealed_id:
        reasons.append("分割の封印 id (sealed_id) が読めず、開封履歴を確かめられない")
    if not candidate_key:
        reasons.append("候補の鍵 (比較の条件 @ git commit) が作れず、開封履歴を確かめられない")
    same, cont = [], []
    if sealed_id and candidate_key:
        method = candidate_key.split("@", 1)[0]
        for r in rows:
            if r.get("sealed_id") != sealed_id:
                continue
            if str(r.get("candidate_key") or "").split("@", 1)[0] != method:
                continue
            rp = r.get("part") if isinstance(r.get("part"), dict) else None
            if (part is not None and rp is not None and int(rp.get("n", -1)) == int(part.get("n", -2))
                    and int(rp.get("k", -1)) != int(part.get("k", -1)) and r.get("seed") == seed):
                cont.append(r)
            else:
                same.append(r)
    if same:
        reasons.append(f"封印 holdout {sealed_id} は方式 {candidate_key.split('@', 1)[0]} に使用済み (前回: "
                       f"{same[-1].get('candidate_key')} {same[-1].get('run_id')} {same[-1].get('at')})。"
                       "同じ holdout で同じ方式を測り直さない (commit が違っても)")
    return {"sealed_id": sealed_id, "candidate_key": candidate_key, "used_before": same, "continued_parts": cont,
            "reasons": reasons}


def holdout_usage_row(sealed_id: str, candidate_key: str, run_id: str, at: str, purpose: str,
                      part: Optional[dict] = None, seed: Optional[int] = None) -> dict:
    """開封履歴に追記する 1 行 (純粋。2026-10-15)。tools.team_build.holdout の行 (sealed_id / candidate_key / run_id / at) に
    purpose・part・seed・kind を足す (verdict は起動時には無いので書かない)"""
    return {"sealed_id": sealed_id, "candidate_key": candidate_key, "run_id": run_id, "at": at, "purpose": purpose,
            "part": ({"k": int(part["k"]), "n": int(part["n"])} if part else None), "seed": seed, "kind": HOLDOUT_USAGE_KIND}


def child_environment(pin_dir: str, base: Optional[dict] = None, team_source: str = "registered",
                      sensitivity_off: bool = False) -> dict:
    """測定の子プロセスの環境 (純粋): racing.child_env (スレッド数 1 の一式) + CHAMPIONS_MODELS_DIR = ピン。
    一般的なチーム (team_source general / general-pinned) は全条件で共通に登録参照を無効化する (MY_TEAM_DISABLED_ENV=1。条件ごとの
    cond_env とは別)。registered は従来どおり (無効化の変数を足さない。呼び出し側の環境に残っていれば消す)。
    sensitivity_off (採点方式の比較、2026-10-09 M2d) なら感度の計算を切る (TRANSITION_SENSITIVITY_ENV=0、全条件で共通。
    HP の悲観側を T と V に渡す本採点は変わらない)"""
    from tools.team_build.racing import child_env
    env = child_env(base)
    env["CHAMPIONS_MODELS_DIR"] = str(pin_dir)
    if team_source in GENERAL_SOURCES:
        env[MY_TEAM_DISABLED_ENV] = "1"
    else:
        env.pop(MY_TEAM_DISABLED_ENV, None)
    if sensitivity_off:
        env[TRANSITION_SENSITIVITY_ENV] = "0"
    return env


def env_record(env: dict) -> dict:
    """条件表に残す環境変数 (純粋): ピン・スレッド数・経路に効くもの。登録参照の無効化は立っているときだけ残す
    (RL の比較 (registered) の条件表の項目は従来と同じ)"""
    from tools.team_build.racing import THREAD_ENV_VARS
    keys = ("CHAMPIONS_MODELS_DIR",) + tuple(THREAD_ENV_VARS) + PATH_ENV_VARS
    rec = {k: env.get(k) for k in keys}
    if env.get(MY_TEAM_DISABLED_ENV):
        rec[MY_TEAM_DISABLED_ENV] = env.get(MY_TEAM_DISABLED_ENV)
    if env.get(TRANSITION_SENSITIVITY_ENV) is not None:
        rec[TRANSITION_SENSITIVITY_ENV] = env.get(TRANSITION_SENSITIVITY_ENV)
    return rec


def team_record(facts_team: Optional[dict], factor: str, team_source: str) -> dict:
    """条件表の team (純粋)。
    general: 一般的なチーム (source ranked、META_PIN の snapshot id・抽選 seed・プール)。登録チームの sha16 は照合しない。
    registered: 登録チームの本文の sha16 を期待値と照合する (RL の比較は P1_TEAM_SHA16 で従来と同じ項目、採点方式の比較は
    P1_SCORING_TEAM_SHA16 で source / label を足す)"""
    team = dict(facts_team or {})
    if team_source == "general" and team.get("source") == "pool":
        # 実験専用の構築プール (2026-10-09 M2d)。期待 sha16 (P1_SCORING_POOL_SHA16) があれば照合する
        team.update(label=TEAM_LABELS["pool"], expected_sha16=P1_SCORING_POOL_SHA16)
        team["match"] = (team.get("sha16") == P1_SCORING_POOL_SHA16) if P1_SCORING_POOL_SHA16 else None
        return team
    if team_source in GENERAL_SOURCES:
        team.update(source="ranked", label=TEAM_LABELS["ranked"])
        if team_source == "general-pinned":
            team.update(pinned=True, pinned_note=PINNED_NOTE)
        return team
    if factor == "scoring":
        team.update(source="registered", label=TEAM_LABELS["registered"], expected_sha16=expected_team_sha16(factor))
    else:
        team.setdefault("expected_sha16", P1_TEAM_SHA16)
    team["match"] = (team.get("sha16") == team.get("expected_sha16")) if team.get("sha16") else False
    return team


def team_note(team: dict) -> str:
    """条件表の notes に残す自チームの出所の 1 行 (純粋)"""
    if team.get("source") == "pool":
        return (f"自チーム: {TEAM_LABELS['pool']} (tools.scoring_pool のファイル {team.get('path')}: 使用率スナップショット "
                f"{team.get('snapshot_id')}、{team.get('battle_format')} の validate-team で合法性を確認した {team.get('n_teams')} 件、"
                f"本文の sha16 {team.get('sha16')}。check_advisor_player --own-pool で rng = Random(相手 seed + 1 = {team.get('seed')}) "
                f"から対戦ごとに引く。条件間で同じ seed なので対戦 i の自チームが対応する。助言の登録参照 (config/my_team.json) は "
                f"{MY_TEAM_DISABLED_ENV}=1 で両条件とも無効化 (シムの登録は送った本文から)。共通ピン (META_PIN / POOL_PIN) は使わない)")
    if team.get("source") == "ranked":
        pinned = f"【{PINNED_NOTE}】" if team.get("pinned") else ""
        return (f"自チーム: {pinned}{TEAM_LABELS['ranked']} (check_advisor_player の既定 = RankedTeambuilder。META_PIN の使用率スナップショット "
                f"{team.get('meta_snapshot_id')} の構築プール {team.get('n_teams')} 件から rng = Random(相手 seed + 1 = {team.get('seed')}) で"
                f"対戦ごとに引く。条件間で同じ seed なので対戦 i の自チームが対応する。助言の登録参照 (config/my_team.json) は "
                f"{MY_TEAM_DISABLED_ENV}=1 で両条件とも無効化)")
    return (f"自チーム: {TEAM_LABELS['registered']} (config/my_team.json の本文を team.txt に書いて --team-file で全対戦に使う。"
            f"sha16 {team.get('sha16')}、期待 {team.get('expected_sha16')})")


def build_conditions(design: dict, facts: dict, battles: int, prelim: bool, seed: int, fold: int, split_file: str,
                     time_limit_sec: int, out_dir: str, commands: dict, env: dict, started_at: Optional[str],
                     notes: Sequence[str] = (), team_source: str = "registered", offset: int = P1_OPP_OFFSET,
                     part: Optional[dict] = None, tier: str = P1_SPLIT_TIER) -> dict:
    """条件表 (純粋)。facts = collect_facts の結果 (取得できない項目は None)。notes = 取得できなかった理由など。
    team_source = 自チームの出所 (general / registered)。RL の比較で registered (既定) なら従来と同じ条件表。
    2026-10-09 M2e: offset = 相手列の offset (part の範囲の先頭)、part = part_record (2 夜分割)。採点方式の比較には
    時間上限と開始条件 (scoring_time) を足す。
    2026-10-15: tier holdout (封印 holdout 層での 1 回の確認) なら opponents に fold / fold_label を出さず tier holdout と
    n_teams_in_tier を、purpose に holdout の 1 行を、holdout_check に facts の holdout (相手列の層の検査と開封履歴) を書く"""
    f = facts or {}
    split = f.get("split") or {}
    factor = design.get("factor", "rl")
    team = team_record(f.get("team"), factor, team_source)
    purpose = ("採点方式 legacy (基準) / transition (2026-10-09 M2b) の対応比較 (シミュレータ条件。OCR を通らない。実戦の有用性とは"
               "分けて評価する)" if factor == "scoring" else
               "RL の行動確率の加点 0 / ×5 / ×25 の対応比較 (シミュレータ条件。OCR を通らない。実戦の有用性とは分けて評価する)")
    notes = list(notes)
    if factor == "scoring" or team_source in GENERAL_SOURCES:
        purpose += f"。自チーム: {team.get('label')}"
        if team.get("pinned"):
            purpose += f" ({PINNED_NOTE})"
        notes.append(team_note(team))
    extra = {"accept_check": dict(ACCEPT_CHECK)}
    if factor == "scoring":
        # 2026-10-09 M2d: 自チームの対応の検査 (verdict が読む) と、予備測定の対応の確認 (facts の prelim_check)
        extra["own_check"] = dict(OWN_CHECK)
        extra["prelim_check"] = f.get("prelim_check")
        # 2026-10-09 M2e: 採点方式の比較だけの時間上限 (全体) と本番の開始条件
        extra["scoring_time"] = {"time_limit_total_sec": int(P1_SCORING_TIME_LIMIT_SEC),
                                 "start_margin_sec": int(P1_SCORING_START_MARGIN_SEC),
                                 "start_rule": "最も遅い条件の秒/戦 × final_battles + start_margin_sec ≤ time_limit_total_sec"}
    if part is not None:
        extra["part"] = dict(part)
    holdout = tier == HOLDOUT_TIER
    opponents = {"split_file": str(split_file), "split_sha16": split.get("sha16"), "sealed_id": split.get("sealed_id"),
                 "sealed_id_expected": P1_SPLIT_SEALED_ID, "run_id": split.get("run_id")}
    if holdout:
        opponents.update(tier=HOLDOUT_TIER, n_teams_in_tier=split.get("n_teams_in_tier"))
    else:
        opponents.update(tier=P1_SPLIT_TIER, fold=int(fold), fold_label=FOLD_LABELS.get(int(fold)),
                         n_teams_in_fold=split.get("n_teams_in_fold"))
    opponents.update(seed=int(seed), offset=int(offset), pilot=P1_OPP_PILOT, pick_policy=P1_OPP_PICK_POLICY,
                     battle_format=TRAINING_BATTLE_FORMAT)
    if holdout:
        banner = holdout_banner({"opponents": opponents})
        purpose += f"。{banner}"
        notes.append(f"相手: {banner}。fold は使わない (層の全 id を seed {int(seed)} で並べた列)。"
                     f"開封履歴の候補の鍵 {(f.get('holdout') or {}).get('candidate_key')}")
        extra["holdout_check"] = dict(f.get("holdout") or {}, required=True, banner=banner)
    return {**extra,
        "experiment": "P1",
        "factor": factor,
        "purpose": purpose,
        "plan_doc": "docs/USEFULNESS_VERIFICATION_PLAN_1007.md §0.5・§4",
        "prelim": bool(prelim),
        "weights": design["weights"], "baseline_weight": design["baseline_weight"], "baseline": design["baseline"],
        "comparisons": design["comparisons"], "k": design["k"], "alpha": design["alpha"], "level": design["level"], "z": design["z"],
        "mde": P1_MDE, "final_battles": P1_FINAL_BATTLES, "interim_battles": P1_INTERIM_BATTLES, "battles": int(battles),
        "time_limit_sec": int(time_limit_sec), "abort_sec_per_battle": P1_ABORT_SEC_PER_BATTLE,
        "team": team,
        "selection_model": f.get("selection_model"),
        "rl_checkpoint": f.get("rl_checkpoint"),
        "data": f.get("data"),
        "git": f.get("git"),
        "showdown": f.get("showdown"),
        "season_pin": f.get("season_pin"),
        "usage_db": f.get("usage_db"),
        "real_bank": f.get("real_bank"),
        "opponents": opponents,
        "advisor": dict(f.get("advisor") or {}, pick_policy=P1_PICK_POLICY, belief_k=P1_BELIEF_K, skip_random=True),
        "env": env_record(env),
        # 条件ごとの環境変数 (採点方式の比較で ADVICE_SCORING_MODE を渡す。RL の比較では空)
        "cond_env": dict(design.get("cond_env") or {}),
        "commands": commands,
        "out_dir": str(out_dir),
        "schedule_note": SCHEDULE_NOTE,
        "started_at": started_at,
        "incomplete": None, "reached": None, "elapsed_sec": None,
        "notes": notes,
    }


def embedding_source(pin_file: Optional[str], pin_sha: Optional[str], pin_mtime: Optional[str], fallback: str,
                     fallback_sha: Optional[str], production: str, production_sha: Optional[str], production_mtime: Optional[str],
                     planned_copy: bool = False) -> dict:
    """測定で選出モデルが読む機能埋め込みと、実機 (checkpoints) との一致 (純粋)。
    ピンに埋め込みがあればそれ、無ければ species_embedding.json に落ちる。planned_copy (ピン作成前の dry-run) は make_pin が
    実機の埋め込みを複製する前提で、複製元の値を使う"""
    if planned_copy:
        used, used_sha, src = production, production_sha, "planned_copy"
        pin_file = pin_file or f"{PIN_PLANNED}/{SELECTION_EMB_FILE}"
    elif pin_sha:
        used, used_sha, src = pin_file, pin_sha, "pin"
    else:
        used, used_sha, src = fallback, fallback_sha, "fallback"
    return {"pin_file": pin_file, "pin_sha16": pin_sha, "pin_mtime": pin_mtime, "fallback": fallback,
            "used": used, "used_sha16": used_sha, "used_source": src,
            "production": production, "production_sha16": production_sha, "production_mtime": production_mtime,
            "matches_production": (used_sha == production_sha) if (used_sha and production_sha) else None}


def preflight_errors(cond: dict, out_has_results: bool = False) -> list:
    """起動前の検査 (純粋)。空なら起動してよい"""
    errs = []
    team = cond.get("team") or {}
    if team.get("source") == "pool":
        # 実験専用の構築プール (2026-10-09 M2d): 指定されていて、本文の sha16 と件数が記録と一致し、期待 sha16 があれば一致
        if not team.get("path"):
            errs.append("一般的なチームの実験専用の構築プールが指定されていない (--own-pool、tools.scoring_pool で作る)")
        elif team.get("errors"):
            errs.append(f"構築プール {team.get('path')} が不正: {' / '.join(team['errors'])}")
        elif not team.get("n_teams"):
            errs.append(f"構築プール {team.get('path')} が空")
        elif team.get("expected_sha16") and team.get("match") is False:
            errs.append(f"構築プールの sha16 {team.get('sha16')} が期待 {team.get('expected_sha16')} と違う")
    elif team.get("source") == "ranked":
        # 一般的なチーム: 登録チームの sha16 は照合しない。構築プールを固定する META_PIN の snapshot id があること
        if team.get("meta_snapshot_id") is None:
            errs.append(f"一般的なチームの使用率スナップショット (META_PIN) が読めない ({team.get('meta_pin_file')})")
        if team.get("n_teams") == 0:
            errs.append(f"一般的なチームの構築プールが空 (snapshot {team.get('meta_snapshot_id')})")
    elif not team.get("sha16"):
        errs.append("登録チームの本文が作れない (config/my_team.json)")
    elif not team.get("match"):
        errs.append(f"登録チームの sha16 {team.get('sha16')} が期待 {team.get('expected_sha16')} と違う")
    sel = cond.get("selection_model") or {}
    if not sel.get("sha16"):
        errs.append(f"選出モデルが読めない ({sel.get('path')})")
    if not (cond.get("opponents") or {}).get("split_sha16"):
        errs.append(f"分割ファイルが読めない ({(cond.get('opponents') or {}).get('split_file')})")
    rl = cond.get("rl_checkpoint") or {}
    if not rl.get("sha16"):
        errs.append(f"RL の checkpoint がピンに無い ({rl.get('pin_dir')})")
    sd = cond.get("showdown") or {}
    # Showdown の版とルール (2026-10-09 M2d、レビュー 4): commit と使う mod を条件表に記録できたこと
    if not sd.get("commit"):
        errs.append(f"Showdown の版 (git commit) を記録できない ({sd.get('dir')})")
    if "mod" in sd and not sd.get("mod"):
        errs.append(f"フォーマット {sd.get('battle_format')} の mod を config/formats.ts から特定できない ({sd.get('dir')})")
    pc = cond.get("prelim_check")
    if isinstance(pc, dict) and pc.get("reasons"):
        errs.append(f"予備測定 {pc.get('dir')} の対応の確認が成り立たない: {' / '.join(pc['reasons'])}")
    if not sd.get("listening"):
        errs.append(f"Showdown がポート {sd.get('port')} で待ち受けていない (bash scripts/ensure_showdown.sh {sd.get('port')})")
    if out_has_results:
        errs.append(f"出力先に前の結果がある ({cond.get('out_dir')})。別の --out を指定する")
    opp = cond.get("opponents") or {}
    if opp.get("tier") == HOLDOUT_TIER:
        # 封印 holdout 層 (2026-10-15): 予備測定は不可、層が空でない、相手列が全件 holdout 層、開封履歴に同じ組が無い
        if cond.get("prelim"):
            errs.append("封印 holdout 層は 1 回だけ測る (--prelim は使えない)")
        if not opp.get("n_teams_in_tier"):
            errs.append(f"分割ファイルの holdout 層が空か読めない ({opp.get('split_file')})")
        hc = cond.get("holdout_check")
        if not isinstance(hc, dict):
            errs.append("相手列の層の検査と開封履歴の確認 (holdout_check) が条件表に無い")
        else:
            seq = hc.get("sequence_check")
            if not isinstance(seq, dict) or not seq:
                errs.append("相手列の層の検査 (holdout_check.sequence_check) が無い")
            else:
                for c, r in seq.items():
                    for x in (r or {}).get("reasons") or []:
                        errs.append(f"相手列の層の検査 NG ({c}): {x}")
            for x in hc.get("errors") or []:
                errs.append(f"holdout の確認ができない: {x}")
            for x in hc.get("reasons") or []:
                errs.append(f"開封履歴: {x}")
    return errs


def preflight_warnings(cond: dict) -> list:
    """起動を止めない注意 (純粋)"""
    warns = []
    opp = cond.get("opponents") or {}
    if opp.get("sealed_id") and opp.get("sealed_id") != opp.get("sealed_id_expected"):
        warns.append(f"分割の封印 id {opp.get('sealed_id')} が期待 {opp.get('sealed_id_expected')} と違う")
    if opp.get("tier") == HOLDOUT_TIER:
        hc = cond.get("holdout_check") or {}
        for r in hc.get("continued_parts") or []:
            warns.append(f"開封履歴に同じ 1 回の確認の別の part {(r.get('part') or {}).get('k')}/{(r.get('part') or {}).get('n')} "
                         f"の行がある ({r.get('run_id')} {r.get('at')}): 続きとして測る")
    elif opp.get("fold_label") != "B":
        warns.append(f"fold {opp.get('fold')} は B (方式の比較用、§0.4) ではない")
    emb = (cond.get("selection_model") or {}).get("embedding") or {}
    if emb and emb.get("matches_production") is False:
        warns.append("選出モデルの機能埋め込みが実機 (配備版の selection_model_emb.json) と違うものになる: "
                     f"測定は {emb.get('used')} を読む (ピンの {emb.get('pin_file')} が無いか実機と違う)")
    team = cond.get("team") or {}
    if team.get("source") == "pool" and team.get("path") and not team.get("expected_sha16"):
        warns.append(f"構築プールの期待 sha16 (config P1_SCORING_POOL_SHA16) が未固定: 今回のプールは {team.get('sha16')}")
    if team.get("pinned"):
        warns.append(PINNED_NOTE)
    pc = cond.get("prelim_check")
    if cond.get("factor") == "scoring" and not cond.get("prelim") and not pc:
        warns.append("予備測定の結果を指定していない (--prelim-dir): 予備測定での自チームの対応の確認をしていない")
    if (cond.get("git") or {}).get("dirty_files"):
        warns.append(f"作業ツリーに未コミットの変更がある: {cond['git']['dirty_files']}")
    return warns


def progress_line(elapsed: float, counts: dict) -> str:
    """進捗の 1 行 (純粋): 各条件の対戦数と秒/戦"""
    parts = []
    for c, n in counts.items():
        spb = f"{elapsed / n:.1f} 秒/戦" if n else "-"
        parts.append(f"{c} {n} 戦 ({spb})")
    return f"[p1 {int(elapsed // 60):d}:{int(elapsed % 60):02d}] " + " | ".join(parts)


def finalize(cond: dict, reached: dict, elapsed: float, timed_out: bool, returncodes: dict, battles: int) -> dict:
    """終了時の条件表の更新 (純粋)。時間上限・異常終了・未到達のどれかなら incomplete: true"""
    out = dict(cond)
    short = {c: n for c, n in reached.items() if n < battles}
    failed = {c: rc for c, rc in returncodes.items() if rc not in (0, None)}
    reasons = []
    if timed_out:
        reasons.append(f"時間上限 {cond.get('time_limit_sec')} 秒を超えて停止")
    if failed:
        reasons.append(f"異常終了 {failed}")
    if short:
        reasons.append(f"対戦数が未到達 {short}")
    out.update(incomplete=bool(reasons), incomplete_reasons=reasons, reached=dict(reached), returncodes=dict(returncodes),
               elapsed_sec=round(float(elapsed), 1), finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return out


def prelim_advice(elapsed: float, reached: dict, returncodes: Optional[dict] = None, incomplete: bool = False,
                  battles: int = P1_PRELIM_BATTLES, threshold: float = P1_ABORT_SEC_PER_BATTLE,
                  final_battles: int = P1_FINAL_BATTLES, conds: Sequence[str] = (),
                  time_limit_sec: Optional[float] = None, margin_sec: float = 0.0) -> dict:
    """予備測定の秒/戦と本番の目安 (純粋)。秒/戦は並列の各プロセスの経過 / 到達数。
    本番開始可 (start_final) の条件 (2026-10-09 レビュー指摘 2): 全条件 (conds、既定は P1_WEIGHTS の 3 条件) が揃い、
    どの条件も battles 戦に到達し、全部が正常終了 (rc 0) で、条件表が未完了でなく、最も遅い条件の秒/戦が threshold 以内。
    到達 0 戦の条件は秒/戦が無いので、それだけで開始不可 (以前は速度の計算から除いていて 0 / 50 / 50 でも開始可になった)。
    time_limit_sec を渡すと (採点方式の比較、2026-10-09 M2e) 速度の基準を「最も遅い条件の秒/戦 × final_battles + margin_sec ≤
    time_limit_sec」に替える (threshold は使わない)。渡さなければ従来どおり (RL の比較)"""
    conds = list(conds) or [cond_name(w) for w in P1_WEIGHTS]
    spb = {c: (elapsed / reached[c] if reached.get(c) else None) for c in conds}
    reasons = []
    missing = [c for c in conds if c not in reached]
    if missing:
        reasons.append(f"結果の無い条件: {missing}")
    short = {c: reached.get(c, 0) for c in conds if (reached.get(c) or 0) < battles}
    if short:
        reasons.append(f"{battles} 戦に未到達: {short}")
    rcs = returncodes or {}
    bad = {c: rcs.get(c) for c in conds if rcs.get(c) != 0}
    if bad:
        reasons.append(f"正常終了でない (rc): {bad}")
    if incomplete:
        reasons.append("条件表が未完了 (incomplete)")
    vals = [v for v in spb.values() if v is not None]
    worst = max(vals) if vals and len(vals) == len(conds) else None
    if worst is None:
        reasons.append("秒/戦を全条件で測れていない")
    elif time_limit_sec is not None:
        need = worst * final_battles + float(margin_sec)
        if need > float(time_limit_sec):
            reasons.append(f"最も遅い条件の秒/戦 {worst:.1f} × {final_battles} 戦 + 余裕 {float(margin_sec):.0f} 秒 = {need:.0f} 秒が"
                           f"上限 {float(time_limit_sec):.0f} 秒を超える")
    elif worst > threshold:
        reasons.append(f"最も遅い条件の秒/戦 {worst:.1f} が上限 {threshold} を超える (600 戦で 2 時間を超える見込み)")
    out = {"sec_per_battle": spb, "worst_sec_per_battle": worst, "threshold": threshold,
           "projected_final_sec": (worst * final_battles if worst is not None else None),
           "start_final": not reasons, "reasons": reasons}
    if time_limit_sec is not None:
        out.update(threshold=None, criterion="sec_per_battle × final_battles + margin ≤ time_limit",
                   time_limit_sec=float(time_limit_sec), margin_sec=float(margin_sec), final_battles=int(final_battles),
                   max_sec_per_battle=(float(time_limit_sec) - float(margin_sec)) / max(1, int(final_battles)))
    return out


# ------------------------------------------------------------------ 取得 (副作用: ファイル・git・ソケットの読み取り)
def _sha16(path) -> Optional[str]:
    from advisor.versions import sha256_file
    return sha256_file(path)


def _mtime(path) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(Path(path).stat().st_mtime))
    except OSError:
        return None


def _git(args: list, cwd: Path, raw: bool = False) -> Optional[str]:
    """git の出力 (raw=False なら前後の空白を除く。porcelain は行頭の空白が意味を持つので raw で読む)"""
    try:
        r = subprocess.run(["git", "-C", str(cwd)] + args, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return r.stdout if raw else r.stdout.strip()
    except Exception:
        return None


def dirty_files(porcelain: Optional[str]) -> Optional[list]:
    """git status --porcelain の出力 → 変更のある追跡ファイルのパス (純粋)。None なら None"""
    if porcelain is None:
        return None
    return [ln[3:] for ln in porcelain.splitlines() if len(ln) > 3]


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else REPO / p


def rl_checkpoint_in(models_dir: Path) -> Optional[Path]:
    """助言側 (advisor.rl_bridge、既定 RL_ADVICE_STYLE / RL_POLICY_SOURCE) と同じ優先順で、models_dir の最初にある zip"""
    from advisor import rl_bridge as RB
    order = RB._policy_candidates(os.environ.get("RL_ADVICE_STYLE", "balance"), os.environ.get("RL_POLICY_SOURCE", "ema"))
    return next((models_dir / n for n in order if (models_dir / n).exists()), None)


def ranked_team_facts(seed: Optional[int], notes: list) -> dict:
    """一般的なチーム (check_advisor_player の既定の自チーム) の材料: META_PIN の snapshot id、POOL_PIN、抽選 seed、
    構築プールの件数と本文の sha16 (使用率 DB があるときだけ作る。DB が無ければ作らない)"""
    from advisor.versions import sha256_text
    from champions_agent.config import DB_PATH
    team = {"source": "ranked", "meta_snapshot_id": None, "meta_pin_file": None, "pool_pin": None,
            "seed": own_team_seed(seed), "top_n": RANKED_TOP_N, "include_external": RANKED_INCLUDE_EXTERNAL,
            "n_teams": None, "pool_sha16": None, "registration_disabled_env": MY_TEAM_DISABLED_ENV}
    try:
        from champions_agent.env.ranked_teams import META_PIN_PATH, PIN_PATH, pinned_meta_snapshot_id
        team["meta_pin_file"] = str(META_PIN_PATH)
        team["meta_snapshot_id"] = pinned_meta_snapshot_id()
        try:
            team["pool_pin"] = PIN_PATH.read_text(encoding="utf-8").strip() or None
        except OSError:
            notes.append(f"team.pool_pin: {PIN_PATH} が無い (ラダー構築は最新のアーカイブを使う)")
    except Exception as e:
        notes.append(f"team: META_PIN が読めない ({e!r})")
    if team["meta_snapshot_id"] is None:
        notes.append(f"team.meta_snapshot_id: META_PIN ({team['meta_pin_file']}) が無いか読めない")
        return team
    if not Path(DB_PATH).exists():
        notes.append(f"team.n_teams: 使用率 DB ({DB_PATH}) が無いので構築プールを作らない")
        return team
    try:
        from champions_agent.env.ranked_teams import build_ranked_teams
        teams = build_ranked_teams(top_n=RANKED_TOP_N, include_external=RANKED_INCLUDE_EXTERNAL,
                                   meta_snapshot_id=team["meta_snapshot_id"])
        team["n_teams"] = len(teams)
        team["pool_sha16"] = sha256_text("\n\n\n".join(teams)) if teams else None
    except Exception as e:
        notes.append(f"team.n_teams: 構築プールを作れない ({e!r})")
    return team


def pool_team_facts(own_pool: Optional[Path], seed: Optional[int], notes: list) -> dict:
    """実験専用の構築プール (--team general の自チーム、2026-10-09 M2d) の材料: パス・本文の sha16 (記録と計算)・件数・
    snapshot・フォーマット・抽選 seed。プールの検査 (tools.scoring_pool.check_pool_doc) の問題は errors に"""
    from tools.scoring_pool import check_pool_doc
    team = {"source": "pool", "path": str(own_pool) if own_pool else None, "sha16": None, "n_teams": None,
            "snapshot_id": None, "battle_format": None, "pool_seed": None, "seed": own_team_seed(seed), "errors": [],
            "registration_disabled_env": MY_TEAM_DISABLED_ENV}
    if own_pool is None:
        notes.append("team: 実験専用の構築プールが指定されていない (--own-pool / config P1_SCORING_POOL_FILE)")
        return team
    try:
        doc = json.loads(Path(own_pool).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        team["errors"] = [f"読めない ({e!r})"]
        return team
    cond = doc.get("conditions") or {}
    team.update(sha16=doc.get("sha16"), n_teams=len(doc.get("teams") or []), snapshot_id=cond.get("snapshot_id"),
                battle_format=cond.get("battle_format"), pool_seed=cond.get("seed"), n_excluded=cond.get("n_excluded"),
                file_sha16=_sha16(own_pool), errors=check_pool_doc(doc))
    if cond.get("battle_format") and cond.get("battle_format") != TRAINING_BATTLE_FORMAT:
        team["errors"].append(f"プールのフォーマット {cond.get('battle_format')} が測定の {TRAINING_BATTLE_FORMAT} と違う")
    return team


def format_rule(formats_text: Optional[str], battle_format: str) -> dict:
    """pokemon-showdown/config/formats.ts の本文 → フォーマット id の {name, mod, ruleset} (純粋)。
    name の英数字だけの小文字 (Showdown の toID) が battle_format と一致する項目の、次の項目までの mod: / ruleset: の行を読む"""
    import re
    out = {"name": None, "mod": None, "ruleset": None}
    if not formats_text:
        return out
    lines = formats_text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*name:\s*[\"'](.+?)[\"'],", line)
        if not m or re.sub(r"[^a-z0-9]", "", m.group(1).lower()) != battle_format:
            continue
        out["name"] = m.group(1)
        for ln in lines[i + 1:]:
            if re.match(r"\s*name:", ln) or (re.match(r"\s*\},?\s*$", ln) and out["mod"]):
                break
            mm = re.match(r"\s*mod:\s*[\"'](.+?)[\"']", ln)
            if mm:
                out["mod"] = mm.group(1)
            mr = re.match(r"\s*ruleset:\s*(\[.*\])", ln)
            if mr:
                out["ruleset"] = mr.group(1)
        break
    return out


def showdown_facts(sd_dir: Path, notes: list) -> dict:
    """Showdown の版とルール (2026-10-09 M2d、レビュー 4): git commit・未コミットの変更・フォーマットの name / mod / ruleset と
    mod の dir があるか。待ち受け (listening) も"""
    sd = {"dir": str(sd_dir), "commit": _git(["rev-parse", "HEAD"], sd_dir) if sd_dir.exists() else None,
          "dirty_files": dirty_files(_git(["status", "--porcelain", "--untracked-files=no"], sd_dir, raw=True))
          if sd_dir.exists() else None,
          "battle_format": TRAINING_BATTLE_FORMAT, "port": SHOWDOWN_PORT, "listening": _listening(SHOWDOWN_PORT)}
    try:
        text = (sd_dir / "config" / "formats.ts").read_text(encoding="utf-8")
    except OSError:
        text = None
    rule = format_rule(text, TRAINING_BATTLE_FORMAT)
    sd.update(format_name=rule["name"], mod=rule["mod"], ruleset=rule["ruleset"],
              mod_dir_exists=(sd_dir / "data" / "mods" / rule["mod"]).is_dir() if rule["mod"] else None)
    if sd["commit"] is None:
        notes.append(f"showdown.commit: {sd_dir} が無いか git でない")
    if rule["mod"] is None:
        notes.append(f"showdown.mod: {sd_dir}/config/formats.ts から {TRAINING_BATTLE_FORMAT} の mod を読めない")
    return sd


def prelim_correspondence(prelim_dir: Optional[Path]) -> Optional[dict]:
    """予備測定 (--prelim の出力 dir) の対応の確認 (2026-10-09 M2d、レビュー 6): 相手 id と自チーム (送った本文の sha16・参照した型)
    の対応が先頭 n 戦 (全条件で揃った数) で成り立つか。dir が無ければ None"""
    if prelim_dir is None:
        return None
    from tools.usefulness_verdict import (correspondence_check, load_outcomes_conds, load_records_conds,
                                          own_correspondence_check, scoring_conds)
    d = Path(prelim_dir)
    try:
        conds_doc = json.loads((d / "conditions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"dir": str(d), "n": 0, "reasons": [f"条件表が読めない ({e!r})"]}
    base, others = scoring_conds(conds_doc)
    conds = [base] + others
    _o, _s, opp_ids, _e = load_outcomes_conds(d, conds)
    recs = load_records_conds(d, conds)
    lens = [len(recs.get(c) or []) for c in conds]
    n = min(lens) if lens else 0
    reasons = []
    if n == 0:
        reasons.append("対戦記録 (battles.jsonl) が無い条件がある")
    else:
        reasons += correspondence_check(opp_ids, conds, n)
        own_reasons, _summary = own_correspondence_check(recs, conds, n)
        reasons += own_reasons
    return {"dir": str(d), "n": n, "reasons": reasons}


def collect_facts(team_text: Optional[str], selection_model: Path, split_file: Path, fold: Optional[int], pin_dir: Optional[Path],
                  notes: list, team_source: str = "registered", seed: Optional[int] = None,
                  own_pool: Optional[Path] = None, showdown_dir: Optional[Path] = None,
                  prelim_dir: Optional[Path] = None, tier: str = P1_SPLIT_TIER) -> dict:
    """条件表の材料を集める。取れない項目は None にして notes に理由を足す (例外で落とさない)。
    team_source general なら自チームは実験専用の構築プール (pool_team_facts、2026-10-09 M2d)、general-pinned なら旧固定プール
    (ranked_team_facts)、registered なら登録チームの本文。showdown_dir (既定 REPO/pokemon-showdown) は版とルールの記録に、
    prelim_dir (予備測定の出力) があれば対応の確認 (prelim_check) に使う。tier holdout (2026-10-15) なら分割の件数は
    holdout 層の全 id (n_teams_in_tier。fold は使わない)"""
    from advisor.versions import sha256_text
    facts: dict = {}
    # 自チーム
    team = {"sha16": None, "species": None, "file": "team.txt"}
    if team_source == "general":
        team = pool_team_facts(own_pool, seed, notes)
    elif team_source == "general-pinned":
        team = ranked_team_facts(seed, notes)
    elif team_text:
        team["sha16"] = sha256_text(team_text)
        try:
            from tools.team_build.opponents import parse_team_text
            ids, _mega = parse_team_text(team_text)
            team["species"] = sorted(ids)
        except Exception as e:
            notes.append(f"team.species: 種族の解析に失敗 ({e!r})")
    else:
        notes.append("team: config/my_team.json から登録チームの本文を作れない (tools.team_build.run.registered_team が空)")
    facts["team"] = team
    # 選出モデル + 機能埋め込みの出所 (CHAMPIONS_MODELS_DIR = ピンのとき、selection_model は ピン/selection_model_emb.json を読み、
    # 無ければ champions_agent/data/species_embedding.json に落ちる。実機 (環境変数なし) は checkpoints/selection_model_emb.json)
    sel = {"path": str(selection_model), "sha16": _sha16(selection_model), "mtime": _mtime(selection_model)}
    if sel["sha16"] is None:
        notes.append(f"selection_model: 読めない ({selection_model})")
    prod_emb = CKPT_DIR / SELECTION_EMB_FILE
    fallback = REPO / "champions_agent" / "data" / "species_embedding.json"
    # ピン未作成 (dry-run) は make_pin が作るピン (pin_models.sh + 埋め込みの複製) を想定する
    pin_emb = (pin_dir / SELECTION_EMB_FILE) if pin_dir is not None else None
    sel["embedding"] = embedding_source(
        str(pin_emb) if pin_emb is not None else None, _sha16(pin_emb) if pin_emb is not None else None,
        _mtime(pin_emb) if pin_emb is not None else None, str(fallback), _sha16(fallback), str(prod_emb), _sha16(prod_emb),
        _mtime(prod_emb), planned_copy=pin_dir is None)
    meta = (pin_dir / SELECTION_META_FILE) if pin_dir is not None else None
    sel["meta"] = ({"pin_file": str(meta), "sha16": _sha16(meta), "mtime": _mtime(meta)} if meta is not None
                   else {"pin_file": f"{PIN_PLANNED}/{SELECTION_META_FILE}", "sha16": _sha16(CKPT_DIR / SELECTION_META_FILE),
                         "mtime": _mtime(CKPT_DIR / SELECTION_META_FILE)})
    if sel["embedding"]["matches_production"] is None:
        notes.append("selection_model.embedding: 実機の埋め込みか測定で使う埋め込みが読めず、一致を確かめられない")
    facts["selection_model"] = sel
    # RL checkpoint (ピン)。dry-run でピン未作成なら、ピンの元 (checkpoints) の同じファイルで代用して notes に書く
    rl = {"pin_dir": str(pin_dir) if pin_dir is not None else PIN_PLANNED, "file": None, "sha16": None, "mtime": None,
          "opponent_file": None}
    src_dir = pin_dir if pin_dir is not None else CKPT_DIR
    try:
        ck = rl_checkpoint_in(src_dir)
        if ck is not None:
            rl.update(file=str(ck), sha16=_sha16(ck), mtime=_mtime(ck))
        else:
            notes.append(f"rl_checkpoint: {src_dir} に battle_policy_<style>_(ema|best).zip が無い")
        # 相手の rl (tools.team_build.pilot.make_opponent_player): MODELS_DIR の balance の ema、無ければ best
        opp = src_dir / "battle_policy_balance_ema.zip"
        rl["opponent_file"] = str(opp if opp.exists() else src_dir / "battle_policy_balance_best.zip")
    except Exception as e:
        notes.append(f"rl_checkpoint: 取得に失敗 ({e!r})")
    if pin_dir is None:
        rl["sha16_source"] = "checkpoints (ピン作成前の元。起動時のピンの値で上書きする)"
        notes.append("rl_checkpoint: dry-run のためピンは作っていない。sha16 はピンの元 (champions_agent/train/checkpoints) の値")
    facts["rl_checkpoint"] = rl
    # 規則・データの版と git (advisor.versions.runtime_versions)
    data = {"dex_sha16": None, "effects_sha16": None}
    git = {"commit": None, "dirty_files": None, "worktree": str(REPO)}
    try:
        from advisor.versions import runtime_versions
        rules = (runtime_versions(refresh=True) or {}).get("rules") or {}
        data.update(dex_sha16=rules.get("dex_sha256"), effects_sha16=rules.get("effects_sha256"),
                    selection_features=rules.get("selection_features"))
        git["commit"] = rules.get("git_commit")
    except Exception as e:
        notes.append(f"data/git: runtime_versions に失敗 ({e!r})")
    git["dirty_files"] = dirty_files(_git(["status", "--porcelain", "--untracked-files=no"], REPO, raw=True))
    if git["commit"] is None:
        notes.append("git.commit: 取得できない")
    facts["data"], facts["git"] = data, git
    # Showdown (版とルール。2026-10-09 M2d)
    facts["showdown"] = showdown_facts(Path(showdown_dir) if showdown_dir else REPO / "pokemon-showdown", notes)
    facts["prelim_check"] = prelim_correspondence(prelim_dir)
    # season_pin (読むだけ。pin_for は表を書くので使わない)
    try:
        from tools.team_build.season_pin import PINS_PATH, load_table
        entry = load_table(PINS_PATH).get(TRAINING_BATTLE_FORMAT)
        if entry:
            facts["season_pin"] = {"regulation": TRAINING_BATTLE_FORMAT, "seed": entry.get("seed"),
                                   "snapshot": entry.get("pool_snapshot_id"), "roster_until": entry.get("roster_until"),
                                   "created": entry.get("created_at")}
        else:
            facts["season_pin"] = None
            notes.append(f"season_pin: {PINS_PATH} に {TRAINING_BATTLE_FORMAT} の行が無い")
    except Exception as e:
        facts["season_pin"] = None
        notes.append(f"season_pin: 読めない ({e!r})")
    # 使用率 DB の最新 snapshot (読み取り専用で開く。DB が無ければ作らない)
    facts["usage_db"] = {"latest_snapshot_id": _latest_snapshot_ro(notes)}
    # 実戦バンク
    try:
        from advisor.real_prior import bank_version
        bv = bank_version()
        facts["real_bank"] = ({"built_at": bv.get("built_at"), "n_battles": bv.get("n_battles"), "sha16": bv.get("sha256"),
                               "data_until": bv.get("data_until"), "path": bv.get("path")} if bv else None)
        if not bv:
            notes.append("real_bank: バンクが無いか読めない")
    except Exception as e:
        facts["real_bank"] = None
        notes.append(f"real_bank: 読めない ({e!r})")
    # 分割ファイル
    holdout = tier == HOLDOUT_TIER
    split = ({"sha16": _sha16(split_file), "sealed_id": None, "run_id": None, "n_teams_in_tier": None} if holdout else
             {"sha16": _sha16(split_file), "sealed_id": None, "run_id": None, "n_teams_in_fold": None})
    try:
        from tools.team_build.opponents import load_split, tier_ids
        doc = load_split(split_file)
        split.update(sealed_id=doc.get("sealed_id"), run_id=doc.get("run_id"))
        if holdout:
            split["n_teams_in_tier"] = len(tier_ids(doc, HOLDOUT_TIER))
        else:
            split["n_teams_in_fold"] = len(tier_ids(doc, P1_SPLIT_TIER, int(fold)))
    except Exception as e:
        notes.append(f"split: 読めない ({split_file}: {e!r})")
    facts["split"] = split
    # 助言の経路条件 (engine のモジュール値と config)
    adv = {}
    try:
        import advisor.engine as eng
        from champions_agent.config import SHADOW_VARIANTS_ENABLED
        adv = {"search_blend": eng.SEARCH_BLEND, "belief_k_engine_default": eng.BELIEF_K,
               "sensor_q": eng.SENSOR_Q_DEFAULT, "search_workers": eng.SEARCH_WORKERS,
               "shadow_variants_enabled": SHADOW_VARIANTS_ENABLED}
    except Exception as e:
        notes.append(f"advisor: engine の値が読めない ({e!r})")
    facts["advisor"] = adv
    return facts


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _latest_snapshot_ro(notes: list) -> Optional[int]:
    import sqlite3
    from champions_agent.config import DB_PATH
    if not Path(DB_PATH).exists():
        notes.append(f"usage_db: {DB_PATH} が無い")
        return None
    try:
        # advisor/sets.py と同じ直接の SELECT (database.latest_snapshot_id は row_factory=Row の接続を前提にしていて、
        # 素の接続では TypeError になる。2026-10-09 の dry-run で判明)
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
            return int(row[0]) if row else None
        finally:
            conn.close()
    except Exception as e:
        notes.append(f"usage_db: 読めない ({e!r})")
        return None


def registered_team_text() -> Optional[str]:
    """登録チームの本文 (advisor.versions の _registered_team と同じ: tools.team_build.run.registered_team)"""
    try:
        from tools.team_build.run import registered_team
        text, _ids, _mega = registered_team()
        return text or None
    except Exception:
        return None


def default_usage_log() -> Path:
    """開封履歴の既定 (tools.team_build.holdout.USAGE_LOG = REPO/logs/build_search/holdout_usage.jsonl)"""
    from tools.team_build.holdout import USAGE_LOG
    return Path(USAGE_LOG)


def read_usage_rows(path: Path) -> tuple:
    """開封履歴の行を読む → (行の list, 読めない理由 or None)。ファイルが無ければ空 (まだ誰も開けていない)"""
    p = Path(path)
    if not p.exists():
        return [], None
    try:
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()], None
    except (OSError, ValueError) as e:
        return [], f"開封履歴 {p} が読めない ({e!r})"


def holdout_facts(commands: dict, split_file: Path, design: dict, git_commit: Optional[str], usage_log: Path,
                  part: Optional[dict], seed: int) -> dict:
    """封印 holdout 層の確認の材料 (副作用: 分割ファイルと開封履歴の読み取り。2026-10-15)。
    sequence_check = 条件ごとのコマンドから作った相手列の層の検査 (opponent_tier_check)、開封履歴の判定 (holdout_usage_check)、
    errors = 読めなかった理由"""
    from tools.team_build.opponents import load_split
    out: dict = {"usage_log": str(usage_log), "errors": [], "sequence_check": {}, "appended": None}
    docs: dict = {}
    sealed_id = None
    try:
        sealed_id = load_split(split_file).get("sealed_id")
    except Exception as e:
        out["errors"].append(f"分割ファイル {split_file} が読めない ({e!r})")
    for c, cmd in (commands or {}).items():
        spec = command_opponent_spec(cmd)
        try:
            sf = spec["split_file"]
            if sf not in docs:
                docs[sf] = load_split(Path(sf))
            doc = docs[sf]
            if doc.get("sealed_id") != sealed_id:
                out["errors"].append(f"{c}: コマンドの分割ファイル {sf} の封印 id {doc.get('sealed_id')} が {sealed_id} と違う")
            seq = planned_opponent_ids(doc, spec)
            out["sequence_check"][c] = dict(opponent_tier_check(seq, doc, spec["offset"] + spec["battles"]),
                                            spec_tier=spec["tier"], spec_fold=spec["fold"], seed=spec["seed"],
                                            offset=spec["offset"], battles=spec["battles"])
        except Exception as e:
            out["sequence_check"][c] = {"reasons": [f"相手列を作れない ({e!r})"]}
    key = holdout_candidate_key(design, git_commit)
    rows, err = read_usage_rows(usage_log)
    if err:
        out["errors"].append(err)
    out.update(holdout_usage_check(rows, sealed_id, key, part=part, seed=seed))
    out["note"] = ("開封履歴は本番の起動時 (起動前検査の後・子プロセスの起動前) に 1 行追記する。dry-run では追記しない。"
                   "同じ封印 id と候補の鍵の組があれば起動しない")
    return out


def append_usage_row(usage_log: Path, row: dict) -> None:
    """開封履歴に 1 行追記する (副作用。本番の起動時だけ)"""
    p = Path(usage_log)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def copy_selection_aux(pin_dir: Path, src_dir: Path = None) -> list:
    """選出モデルの機能埋め込み (と meta があればそれも) をピンへ複製する。複製したファイル名を返す。
    CHAMPIONS_MODELS_DIR = ピンのとき selection_model は ピン/selection_model_emb.json を読み、無ければ species_embedding.json に
    落ちて実機 (checkpoints の埋め込み) と違う選出になる (2026-10-09 判断 (a): P1 のピンにだけ複製。pin_models.sh は変えない)"""
    import shutil
    src_dir = Path(src_dir) if src_dir is not None else CKPT_DIR
    copied = []
    for name in (SELECTION_EMB_FILE, SELECTION_META_FILE):
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, Path(pin_dir) / name)
            copied.append(name)
    return copied


def make_pin() -> Path:
    """scripts/pin_models.sh でピンを作り (最終行がピンの絶対パス)、選出モデルの埋め込みと meta を複製する"""
    r = subprocess.run(["bash", str(REPO / "scripts" / "pin_models.sh")], capture_output=True, text=True, cwd=str(REPO), timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"ピンの作成に失敗: {r.stderr.strip()}")
    pin = Path(r.stdout.strip().splitlines()[-1])
    copied = copy_selection_aux(pin)
    if SELECTION_EMB_FILE not in copied:
        print(f"[p1] 注意: {CKPT_DIR / SELECTION_EMB_FILE} が無く、ピンへ複製できなかった")
    return pin


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def launch(cond: dict, out_dir: Path, env: dict, battles: int, time_limit_sec: int) -> dict:
    """3 プロセスを並列に起動し、進捗を出し、時間上限で止める。終了後の条件表を返す (書き込みは呼び出し側)"""
    procs, logs = {}, {}
    t0 = time.time()
    cond_env = cond.get("cond_env") or {}
    for c, cmd in cond["commands"].items():
        lf = (out_dir / f"{cond_file(c)}.log").open("a", encoding="utf-8")
        logs[c] = lf
        env_c = dict(env, **(cond_env.get(c) or {}))
        procs[c] = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), env=env_c)
        print(f"[p1] 起動 {c} pid {procs[c].pid}")
    timed_out = False
    next_report = t0 + P1_PROGRESS_SEC
    try:
        while any(p.poll() is None for p in procs.values()):
            time.sleep(1.0)
            now = time.time()
            if now >= next_report:
                counts = {c: _count_lines(out_dir / f"{cond_file(c)}.battles.jsonl") for c in procs}
                print(progress_line(now - t0, counts), flush=True)
                next_report = now + P1_PROGRESS_SEC
            if now - t0 > time_limit_sec:
                timed_out = True
                print(f"[p1] 時間上限 {time_limit_sec} 秒を超えた → 3 プロセスを止める (未完了。採否は出さない)", flush=True)
                break
    except KeyboardInterrupt:
        timed_out = True
        print("[p1] 中断 (Ctrl-C) → 3 プロセスを止める (未完了)", flush=True)
    if timed_out:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + P1_TERMINATE_GRACE_SEC
        for p in procs.values():
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    for lf in logs.values():
        lf.close()
    elapsed = time.time() - t0
    reached = {c: _count_lines(out_dir / f"{cond_file(c)}.battles.jsonl") for c in procs}
    rcs = {c: p.returncode for c, p in procs.items()}
    print(progress_line(elapsed, reached))
    return finalize(cond, reached, elapsed, timed_out, rcs, battles)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="P1: RL 加点 0 / ×5 / ×25 の対応比較を 3 プロセス並列で回す")
    ap.add_argument("--out", default=None, help="出力先 (既定 logs/usefulness/p1_<YYYYMMDD_HHMM>、予備は p1_prelim_...)")
    ap.add_argument("--battles", type=int, default=None, help=f"対戦数 / 条件 (既定 {P1_FINAL_BATTLES}、--prelim は {P1_PRELIM_BATTLES})")
    ap.add_argument("--prelim", action="store_true", help=f"予備測定 ({P1_PRELIM_BATTLES} 戦。秒/戦を見積もって本番の目安を出す)")
    ap.add_argument("--seed", type=int, default=None,
                    help=f"相手列の seed (既定 {P1_SEED}。--tier holdout では明示が要り、search の {P1_SEED} とは別の値)")
    ap.add_argument("--split", default=P1_SPLIT_FILE, help="opponent_families.json (分割ファイル)")
    ap.add_argument("--tier", choices=TIERS, default=P1_SPLIT_TIER,
                    help="相手の層: search = fold で選ぶ方式の比較用 (既定) / holdout = 封印 holdout 層での 1 回の確認 "
                         "(fold なし・--seed 必須・--prelim 不可。2026-10-15)")
    ap.add_argument("--fold", type=int, default=None, help=f"search の fold (既定 {BUILD_FOLD_EVAL} = B、方式の比較用。holdout では使わない)")
    ap.add_argument("--holdout-usage-log", default=None,
                    help="封印 holdout の開封履歴 (既定 tools.team_build.holdout.USAGE_LOG = logs/build_search/holdout_usage.jsonl)")
    ap.add_argument("--selection-model", default=P1_SELECTION_MODEL, help="選出モデル (3 条件で共通)")
    ap.add_argument("--pin-dir", default=None, help="RL のピン dir (既定: 起動時に scripts/pin_models.sh で作る)")
    ap.add_argument("--time-limit-sec", type=int, default=None,
                    help=f"時間上限 (超えたら止めて未完了。既定: rl は {P1_TIME_LIMIT_SEC}、scoring は {P1_SCORING_TIME_LIMIT_SEC} "
                         f"(--part は対戦数で按分))")
    ap.add_argument("--part", default=None,
                    help="k/n: 600 対戦の列を n 個の範囲に分けた k 番目だけを測る (--compare scoring の 2 夜分割。2026-10-09 M2e)")
    ap.add_argument("--dry-run", action="store_true", help="条件表とコマンド列を出すだけ (起動しない・ピンを作らない)")
    ap.add_argument("--compare", choices=("rl", "scoring"), default="rl",
                    help="比べる条件: rl = RL 加点の重み 0 / 5 / 25 (既定)、scoring = 採点方式 legacy / transition (2026-10-09 M2b)")
    ap.add_argument("--team", choices=P1_TEAM_SOURCES, default=None,
                    help="自チームの出所: general = 一般的なチーム (実験専用の構築プール --own-pool から seed で引く。登録参照は無効化) / "
                         "general-pinned = 旧固定プール (META_PIN の構築を seed で引く。旧固定プール上の比較) / "
                         "registered = 最新の登録チーム (config/my_team.json)。既定は --compare scoring → general、rl → registered")
    ap.add_argument("--own-pool", default=P1_SCORING_POOL_FILE,
                    help="--team general の実験専用の構築プール (tools.scoring_pool のファイル。既定 config P1_SCORING_POOL_FILE)")
    ap.add_argument("--showdown-dir", default=None, help="Showdown の版とルールを記録する pokemon-showdown (既定 REPO/pokemon-showdown)")
    ap.add_argument("--prelim-dir", default=None,
                    help="予備測定 (--prelim) の出力 dir。本番の起動前検査で、その相手 id と自チームの対応が成り立つことを確かめる")
    args = ap.parse_args(argv)
    team_source = resolve_team_source(args.team, args.compare)
    try:
        part = parse_part(args.part)
    except ValueError as e:
        print(f"[p1] {e}")
        return 2
    part_rec = None
    if part is not None:
        # 2 夜分割 (2026-10-09 M2e): 採点方式の比較の本番だけ。旧固定プールは自チームの抽選を進められないので使わない
        bad = []
        if args.compare != "scoring":
            bad.append("--part は --compare scoring だけ")
        if args.prelim:
            bad.append("--part は本番 (600 戦の列) だけ。--prelim とは併用しない")
        if team_source == "general-pinned":
            bad.append("--part は --team general-pinned (RankedTeambuilder、抽選の位置を進められない) では使えない")
        part_rec = part_record(*part)
        if args.battles is not None and int(args.battles) != part_rec["battles"]:
            bad.append(f"--battles {args.battles} が part {args.part} の対戦数 {part_rec['battles']} と違う")
        if bad:
            for b in bad:
                print(f"[p1] {b}")
            return 2
    # 相手の層 (2026-10-15): holdout は fold を使わず、seed を明示 (search の P1_SEED とは別)、予備測定は不可
    tier = args.tier
    holdout = tier == HOLDOUT_TIER
    if holdout:
        bad = []
        if args.seed is None:
            bad.append(f"--tier holdout は --seed の明示が要る (search の {P1_SEED} とは別の値)")
        elif int(args.seed) == int(P1_SEED):
            bad.append(f"--tier holdout の --seed {args.seed} が search の seed {P1_SEED} と同じ")
        if args.prelim:
            bad.append("--tier holdout は 1 回だけ測る (--prelim は使えない)")
        if args.fold is not None:
            bad.append("--tier holdout は fold を使わない (--fold を外す)")
        if bad:
            for b in bad:
                print(f"[p1] {b}")
            return 2
    seed = int(args.seed) if args.seed is not None else int(P1_SEED)
    fold: Optional[int] = None if holdout else (int(args.fold) if args.fold is not None else int(BUILD_FOLD_EVAL))
    usage_log: Optional[Path] = ((Path(args.holdout_usage_log).resolve() if args.holdout_usage_log else default_usage_log())
                                 if holdout else None)
    own_pool: Optional[Path] = _abs(args.own_pool).resolve() if (args.own_pool and team_source == "general") else None
    showdown_dir: Optional[Path] = Path(args.showdown_dir).resolve() if args.showdown_dir else None
    prelim_dir: Optional[Path] = Path(args.prelim_dir).resolve() if args.prelim_dir else None

    now = time.time()
    battles = part_rec["battles"] if part_rec else resolve_battles(args.battles, args.prelim)
    offset = part_rec["opp_offset"] if part_rec else P1_OPP_OFFSET
    time_limit = resolve_time_limit(args.time_limit_sec, args.compare, part_rec["battles"] if part_rec else None)
    out_dir = Path(args.out).resolve() if args.out else default_out_dir(now, args.prelim, part=part, tier=tier)
    split_file, selection_model = _abs(args.split), _abs(args.selection_model)
    design = plan_scoring() if args.compare == "scoring" else plan()
    # 採点方式の比較では感度 (楽観側の HP・持ち物) を切る (2026-10-09 M2d、レビュー 3。全条件で共通の環境)
    sens_off = args.compare == "scoring"
    notes: list = []
    if args.prelim and battles != P1_PRELIM_BATTLES:
        notes.append(f"予備測定だが対戦数は --battles {battles} を使った")

    pin_dir: Optional[Path] = Path(args.pin_dir).resolve() if args.pin_dir else None
    # 一般的なチームは --team-file を渡さない (登録チームの本文は読まない・team.txt を書かない)
    team_text = registered_team_text() if team_source == "registered" else None
    team_file: Optional[Path] = out_dir / "team.txt" if team_source == "registered" else None
    # ピン未作成の dry-run でもコマンドに入るパス
    pin_for_cmd = str(pin_dir) if pin_dir is not None else PIN_PLANNED

    def assemble(pin_now: Optional[Path], started_at: Optional[str]) -> dict:
        pin_s = str(pin_now) if pin_now is not None else pin_for_cmd
        notes_now = list(notes)
        facts = collect_facts(team_text, selection_model, split_file, fold, pin_now, notes_now,
                              team_source=team_source, seed=seed, own_pool=own_pool, showdown_dir=showdown_dir,
                              prelim_dir=prelim_dir, tier=tier)
        if design.get("factor") == "scoring":
            cmds = {c: build_command(design["cond_weight"][c], out_dir, team_file, split_file, fold, seed, battles,
                                     selection_model, pin_s, cond=c, own_pool=own_pool, offset=offset, tier=tier)
                    for c in design["conditions"]}
        else:
            cmds = {cond_name(w): build_command(w, out_dir, team_file, split_file, fold, seed, battles,
                                                selection_model, pin_s, own_pool=own_pool, tier=tier) for w in design["weights"]}
        if holdout:
            # 封印 holdout 層 (2026-10-15): 実際のコマンドから作る相手列の層の検査と開封履歴の確認
            facts = dict(facts, holdout=holdout_facts(cmds, split_file, design, (facts.get("git") or {}).get("commit"),
                                                      usage_log, part_rec, seed))
        env = child_environment(pin_s, team_source=team_source, sensitivity_off=sens_off)
        return build_conditions(design, facts, battles, args.prelim, seed, fold, str(split_file),
                                time_limit, str(out_dir), cmds, env, started_at, notes_now, team_source=team_source,
                                offset=offset, part=part_rec, tier=tier)

    if args.dry_run:
        cond = assemble(pin_dir, None)
        print(json.dumps(cond, ensure_ascii=False, indent=1))
        print("\n# コマンド列 (環境: CHAMPIONS_MODELS_DIR = ピン、スレッド数 1"
              + (f"、{MY_TEAM_DISABLED_ENV}=1 (登録参照の無効化、全条件)" if team_source in GENERAL_SOURCES else "")
              + (f"、{TRANSITION_SENSITIVITY_ENV}=0 (感度を切る、全条件)" if sens_off else "") + ")")
        for c, cmd in cond["commands"].items():
            extra = " ".join(f"{k}={v}" for k, v in ((cond.get("cond_env") or {}).get(c) or {}).items())
            print(f"{c}: " + (extra + " " if extra else "") + " ".join(cmd))
        errs, warns = preflight_errors(cond), preflight_warnings(cond)
        for w in warns:
            print(f"[注意] {w}")
        for e in errs:
            print(f"[起動前の検査 NG] {e}")
        print(f"[dry-run] 起動していない。起動前の検査: {'OK' if not errs else 'NG (' + str(len(errs)) + ' 件)'} / 開始時刻: {SCHEDULE_NOTE}")
        return 0 if not errs else 3

    out_has_results = out_dir.exists() and (any(out_dir.glob("rl*.json*")) or any(out_dir.glob("scoring_*.json*")))
    # ピンを作る前に、ピンに依らない検査 (登録チーム・選出モデル・分割・Showdown・出力先) を通す
    pre = assemble(pin_dir, None)
    errs = [e for e in preflight_errors(pre, out_has_results) if not (pin_dir is None and e.startswith("RL の checkpoint"))]
    if errs:
        for e in errs:
            print(f"[起動前の検査 NG] {e}")
        print("[p1] 起動しない")
        return 2
    if pin_dir is None:
        pin_dir = make_pin()
        print(f"[p1] ピン {pin_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    if team_file is not None:
        team_file.write_text(team_text or "", encoding="utf-8")
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    cond = assemble(pin_dir, started_at)
    errs = preflight_errors(cond)
    for w in preflight_warnings(cond):
        print(f"[注意] {w}")
    if errs:
        _write_json(out_dir / "conditions.json", dict(cond, incomplete=True, incomplete_reasons=["起動前の検査 NG"] + errs))
        for e in errs:
            print(f"[起動前の検査 NG] {e}")
        return 2
    if holdout:
        # 封印 holdout 層 (2026-10-15): 検査を通ったら子プロセスの起動前に開封履歴へ 1 行追記する (追記できなければ起動しない)
        hc = cond.get("holdout_check") or {}
        row = holdout_usage_row(hc.get("sealed_id"), hc.get("candidate_key"), out_dir.name, started_at, cond.get("purpose"),
                                part=part_rec, seed=seed)
        try:
            append_usage_row(usage_log, row)
        except OSError as e:
            _write_json(out_dir / "conditions.json", dict(cond, incomplete=True,
                                                          incomplete_reasons=[f"開封履歴 {usage_log} に追記できない ({e!r})"]))
            print(f"[起動前の検査 NG] 開封履歴 {usage_log} に追記できない ({e!r})")
            return 2
        cond = dict(cond, holdout_check=dict(hc, appended=row))
        print(f"[p1] 開封履歴 {usage_log} に追記: {hc.get('sealed_id')} / {hc.get('candidate_key')} / {out_dir.name}")
    _write_json(out_dir / "conditions.json", cond)
    print(f"[p1] 条件表 {out_dir / 'conditions.json'} / {battles} 戦 × {len(cond['commands'])} 条件 / 開始 {started_at}")
    print(f"[p1] 注意: {SCHEDULE_NOTE}")
    env = child_environment(str(pin_dir), team_source=team_source, sensitivity_off=sens_off)
    final = launch(cond, out_dir, env, battles, time_limit)
    if args.prelim:
        # 採点方式の比較 (2026-10-09 M2e): 全体の上限 (600 戦) と余裕から開始可否を求める。RL の比較は従来の秒/戦の基準
        scoring_rule = ({"time_limit_sec": P1_SCORING_TIME_LIMIT_SEC, "margin_sec": P1_SCORING_START_MARGIN_SEC}
                        if args.compare == "scoring" else {})
        final["prelim_advice"] = prelim_advice(final["elapsed_sec"], final["reached"], final.get("returncodes"),
                                               bool(final.get("incomplete")), battles,
                                               conds=list(cond["commands"]), **scoring_rule)
    _write_json(out_dir / "conditions.json", final)
    print(f"[p1] 終了: incomplete={final['incomplete']} 到達 {final['reached']} 所要 {final['elapsed_sec']} 秒")
    if args.prelim:
        adv = final["prelim_advice"]
        worst = adv["worst_sec_per_battle"]
        print(f"[p1] 予備測定: 秒/戦 (最大) {worst if worst is None else round(worst, 2)} "
              f"/ 本番 {P1_FINAL_BATTLES} 戦の見込み {adv['projected_final_sec'] if adv['projected_final_sec'] is None else round(adv['projected_final_sec'] / 60)} 分")
        if adv["start_final"]:
            rule = (f"最遅の秒/戦 × {adv.get('final_battles')} 戦 + 余裕 {adv.get('margin_sec'):.0f} 秒が上限 "
                    f"{adv.get('time_limit_sec'):.0f} 秒以内" if adv.get("time_limit_sec") is not None else "最遅の秒/戦が上限内")
            print(f"[p1] 本番開始可: 全条件が規定数に到達・正常終了・未完了でない・{rule} (判断は人)")
        else:
            print("[p1] 本番を始めない目安 (判断は人): " + " / ".join(adv["reasons"]))
    if part_rec:
        print(f"[p1] part {part_rec['k']}/{part_rec['n']} (対戦 {part_rec['start']}〜{part_rec['end'] - 1})。全 part が揃ったら "
              f"python -m tools.usefulness_verdict --out <part 1 の dir> ... <part {part_rec['n']} の dir> --merged-out <dir> で結合して判定")
    else:
        print(f"[p1] 集計: python -m tools.usefulness_verdict --out {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
