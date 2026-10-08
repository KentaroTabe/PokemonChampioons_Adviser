"""有用性検証 P1: RL の行動確率の加点 0 / ×5 / ×25 の対応比較を起動する (docs/USEFULNESS_VERIFICATION_PLAN_1007.md §0.5・§4、2026-10-09)。

    python -m tools.usefulness_p1 --prelim --dry-run          # 条件表とコマンド列を出すだけ (起動しない・ピンを作らない)
    python -m tools.usefulness_p1 --prelim                     # 予備 50 戦 / 条件 (秒/戦の見積もり)
    python -m tools.usefulness_p1                              # 本番 600 戦 / 条件
    python -m tools.usefulness_verdict --out <出力 dir>        # 集計と §0.5 の 3 値判定

3 条件 (重み 0 / 5 / 25) を tools.check_advisor_player で 3 プロセス並列に回す。同じ相手列 (fold B、seed 固定) なので対戦 i が対応する。
シミュレータの状態から直接助言を計算する (OCR を通らない) ので、分かるのはシミュレーション条件下での RL 加点の効果で、実戦の有用性とは分ける。

起動前に条件表 conditions.json (§0.5 の「開始前に固定する項目」+ 版と経路の条件) を書く。取得できない項目は null にして理由を notes に残す。
起動前の検査 (不合格なら起動しない): 登録チームの本文の sha16 が config P1_TEAM_SHA16 と一致 / 選出モデル・分割ファイルがある /
ピンに RL の checkpoint がある / Showdown (SHOWDOWN_PORT) が待ち受けている / 出力先に前の結果が無い。
環境変数: CHAMPIONS_MODELS_DIR = ピン (相手の rl は config.MODELS_DIR を読むので --models-dir だけでは届かない)、スレッド数 1 の一式
(tools.team_build.racing.child_env と同じ)。
時間上限 (P1_TIME_LIMIT_SEC) を過ぎたら 3 プロセスを止めて incomplete: true (採否は出さない)。
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
    P1_TEAM_SHA16, P1_TERMINATE_GRACE_SEC, P1_TIME_LIMIT_SEC, P1_WEIGHTS, SHOWDOWN_PORT, TRAINING_BATTLE_FORMAT)
from tools.usefulness_verdict import cond_name, corrected_z

REPO = Path(__file__).resolve().parent.parent
FOLD_LABELS = {BUILD_FOLD_ADAPT: "A", BUILD_FOLD_EVAL: "B", BUILD_FOLD_VALIDATE: "V"}
# 経路に効く環境変数 (設定されていれば条件表に残す)
PATH_ENV_VARS = ("RL_ADVICE_STYLE", "RL_POLICY_SOURCE", "RL_BLEND_WEIGHT", "SELECTION_FEATURES", "SHOWDOWN_PORT")
SCHEDULE_NOTE = "launchd の track-progress (毎日 21:50、17〜34 分) が終わってから始め、06:30 の usage-update の前に終える"
PIN_PLANNED = "(起動時に scripts/pin_models.sh で作成)"
CKPT_DIR = REPO / "champions_agent" / "train" / "checkpoints"
SELECTION_EMB_FILE = "selection_model_emb.json"    # 選出モデルの機能埋め込みのピン (selection_model.EMB_PIN_PATH の名前)
SELECTION_META_FILE = "selection_model_meta.json"


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


def resolve_battles(battles: Optional[int], prelim: bool) -> int:
    """この測定の対戦数 (純粋): 明示があればそれ、無ければ予備 P1_PRELIM_BATTLES / 本番 P1_FINAL_BATTLES"""
    if battles is not None:
        return int(battles)
    return int(P1_PRELIM_BATTLES if prelim else P1_FINAL_BATTLES)


def default_out_dir(now: float, prelim: bool, root: Path = REPO / P1_OUT_ROOT) -> Path:
    """既定の出力先 (純粋): logs/usefulness/p1_<YYYYMMDD_HHMM> (予備は p1_prelim_...)"""
    stamp = time.strftime("%Y%m%d_%H%M", time.localtime(now))
    return Path(root) / (f"p1_prelim_{stamp}" if prelim else f"p1_{stamp}")


def build_command(weight: float, out_dir: Path, team_file: Path, split_file: Path, fold: int, seed: int, battles: int,
                  selection_model: Path, pin_dir: str, python: str = sys.executable, offset: int = P1_OPP_OFFSET,
                  tier: str = P1_SPLIT_TIER) -> list:
    """1 条件の tools.check_advisor_player のコマンド (純粋)"""
    c = cond_name(weight)
    return [python, "-m", "tools.check_advisor_player",
            "--battles", str(int(battles)), "--opp-seed", str(int(seed)), "--opp-offset", str(int(offset)),
            "--opp-split", f"{split_file}:{tier}:{int(fold)}",
            "--opp-pilot", P1_OPP_PILOT, "--opp-pick-policy", P1_OPP_PICK_POLICY, "--pick-policy", P1_PICK_POLICY,
            "--team-file", str(team_file), "--selection-model", str(selection_model), "--models-dir", str(pin_dir),
            "--rl-blend", f"{float(weight):g}", "--skip-random", "--belief-k", str(P1_BELIEF_K),
            "--json", str(Path(out_dir) / f"{c}.json"), "--battle-log", str(Path(out_dir) / f"{c}.battles.jsonl"),
            "--candidate-id", f"p1_{c}"]


def child_environment(pin_dir: str, base: Optional[dict] = None) -> dict:
    """測定の子プロセスの環境 (純粋): racing.child_env (スレッド数 1 の一式) + CHAMPIONS_MODELS_DIR = ピン"""
    from tools.team_build.racing import child_env
    env = child_env(base)
    env["CHAMPIONS_MODELS_DIR"] = str(pin_dir)
    return env


def env_record(env: dict) -> dict:
    """条件表に残す環境変数 (純粋): ピン・スレッド数・経路に効くもの"""
    from tools.team_build.racing import THREAD_ENV_VARS
    keys = ("CHAMPIONS_MODELS_DIR",) + tuple(THREAD_ENV_VARS) + PATH_ENV_VARS
    return {k: env.get(k) for k in keys}


def build_conditions(design: dict, facts: dict, battles: int, prelim: bool, seed: int, fold: int, split_file: str,
                     time_limit_sec: int, out_dir: str, commands: dict, env: dict, started_at: Optional[str],
                     notes: Sequence[str] = ()) -> dict:
    """条件表 (純粋)。facts = collect_facts の結果 (取得できない項目は None)。notes = 取得できなかった理由など"""
    f = facts or {}
    team = dict(f.get("team") or {})
    team.setdefault("expected_sha16", P1_TEAM_SHA16)
    team["match"] = (team.get("sha16") == team.get("expected_sha16")) if team.get("sha16") else False
    split = f.get("split") or {}
    return {
        "experiment": "P1",
        "purpose": "RL の行動確率の加点 0 / ×5 / ×25 の対応比較 (シミュレータ条件。OCR を通らない。実戦の有用性とは分けて評価する)",
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
        "opponents": {"split_file": str(split_file), "split_sha16": split.get("sha16"), "sealed_id": split.get("sealed_id"),
                      "sealed_id_expected": P1_SPLIT_SEALED_ID, "run_id": split.get("run_id"),
                      "tier": P1_SPLIT_TIER, "fold": int(fold), "fold_label": FOLD_LABELS.get(int(fold)),
                      "n_teams_in_fold": split.get("n_teams_in_fold"), "seed": int(seed), "offset": P1_OPP_OFFSET,
                      "pilot": P1_OPP_PILOT, "pick_policy": P1_OPP_PICK_POLICY, "battle_format": TRAINING_BATTLE_FORMAT},
        "advisor": dict(f.get("advisor") or {}, pick_policy=P1_PICK_POLICY, belief_k=P1_BELIEF_K, skip_random=True),
        "env": env_record(env),
        "commands": commands,
        "out_dir": str(out_dir),
        "schedule_note": SCHEDULE_NOTE,
        "started_at": started_at,
        "incomplete": None, "reached": None, "elapsed_sec": None,
        "notes": list(notes),
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
    if not team.get("sha16"):
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
    if not sd.get("listening"):
        errs.append(f"Showdown がポート {sd.get('port')} で待ち受けていない (bash scripts/ensure_showdown.sh {sd.get('port')})")
    if out_has_results:
        errs.append(f"出力先に前の結果がある ({cond.get('out_dir')})。別の --out を指定する")
    return errs


def preflight_warnings(cond: dict) -> list:
    """起動を止めない注意 (純粋)"""
    warns = []
    opp = cond.get("opponents") or {}
    if opp.get("sealed_id") and opp.get("sealed_id") != opp.get("sealed_id_expected"):
        warns.append(f"分割の封印 id {opp.get('sealed_id')} が期待 {opp.get('sealed_id_expected')} と違う")
    if opp.get("fold_label") != "B":
        warns.append(f"fold {opp.get('fold')} は B (方式の比較用、§0.4) ではない")
    emb = (cond.get("selection_model") or {}).get("embedding") or {}
    if emb and emb.get("matches_production") is False:
        warns.append("選出モデルの機能埋め込みが実機 (配備版の selection_model_emb.json) と違うものになる: "
                     f"測定は {emb.get('used')} を読む (ピンの {emb.get('pin_file')} が無いか実機と違う)")
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


def prelim_advice(elapsed: float, reached: dict, threshold: float = P1_ABORT_SEC_PER_BATTLE,
                  final_battles: int = P1_FINAL_BATTLES) -> dict:
    """予備測定の秒/戦と本番の目安 (純粋)。秒/戦は並列の各プロセスの経過 / 到達数の最大"""
    spb = {c: (elapsed / n if n else None) for c, n in reached.items()}
    vals = [v for v in spb.values() if v is not None]
    worst = max(vals) if vals else None
    ok = worst is not None and worst <= threshold
    return {"sec_per_battle": spb, "worst_sec_per_battle": worst, "threshold": threshold,
            "projected_final_sec": (worst * final_battles if worst is not None else None), "start_final": ok}


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


def collect_facts(team_text: Optional[str], selection_model: Path, split_file: Path, fold: int, pin_dir: Optional[Path],
                  notes: list) -> dict:
    """条件表の材料を集める。取れない項目は None にして notes に理由を足す (例外で落とさない)"""
    from advisor.versions import sha256_text
    facts: dict = {}
    # 登録チーム
    team = {"sha16": None, "species": None, "file": "team.txt"}
    if team_text:
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
    # Showdown
    sd_dir = REPO / "pokemon-showdown"
    sd = {"dir": str(sd_dir), "commit": _git(["rev-parse", "HEAD"], sd_dir) if sd_dir.exists() else None,
          "port": SHOWDOWN_PORT, "listening": _listening(SHOWDOWN_PORT)}
    if sd["commit"] is None:
        notes.append(f"showdown.commit: {sd_dir} が無いか git でない")
    facts["showdown"] = sd
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
    split = {"sha16": _sha16(split_file), "sealed_id": None, "run_id": None, "n_teams_in_fold": None}
    try:
        from tools.team_build.opponents import load_split, tier_ids
        doc = load_split(split_file)
        split.update(sealed_id=doc.get("sealed_id"), run_id=doc.get("run_id"),
                     n_teams_in_fold=len(tier_ids(doc, P1_SPLIT_TIER, int(fold))))
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
        from champions_agent.data import database as db
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            return db.latest_snapshot_id(conn)
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
    for c, cmd in cond["commands"].items():
        lf = (out_dir / f"{c}.log").open("a", encoding="utf-8")
        logs[c] = lf
        procs[c] = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), env=env)
        print(f"[p1] 起動 {c} pid {procs[c].pid}")
    timed_out = False
    next_report = t0 + P1_PROGRESS_SEC
    try:
        while any(p.poll() is None for p in procs.values()):
            time.sleep(1.0)
            now = time.time()
            if now >= next_report:
                counts = {c: _count_lines(out_dir / f"{c}.battles.jsonl") for c in procs}
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
    reached = {c: _count_lines(out_dir / f"{c}.battles.jsonl") for c in procs}
    rcs = {c: p.returncode for c, p in procs.items()}
    print(progress_line(elapsed, reached))
    return finalize(cond, reached, elapsed, timed_out, rcs, battles)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="P1: RL 加点 0 / ×5 / ×25 の対応比較を 3 プロセス並列で回す")
    ap.add_argument("--out", default=None, help="出力先 (既定 logs/usefulness/p1_<YYYYMMDD_HHMM>、予備は p1_prelim_...)")
    ap.add_argument("--battles", type=int, default=None, help=f"対戦数 / 条件 (既定 {P1_FINAL_BATTLES}、--prelim は {P1_PRELIM_BATTLES})")
    ap.add_argument("--prelim", action="store_true", help=f"予備測定 ({P1_PRELIM_BATTLES} 戦。秒/戦を見積もって本番の目安を出す)")
    ap.add_argument("--seed", type=int, default=P1_SEED, help="相手列の seed")
    ap.add_argument("--split", default=P1_SPLIT_FILE, help="opponent_families.json (分割ファイル)")
    ap.add_argument("--fold", type=int, default=BUILD_FOLD_EVAL, help="search の fold (既定 B = 方式の比較用)")
    ap.add_argument("--selection-model", default=P1_SELECTION_MODEL, help="選出モデル (3 条件で共通)")
    ap.add_argument("--pin-dir", default=None, help="RL のピン dir (既定: 起動時に scripts/pin_models.sh で作る)")
    ap.add_argument("--time-limit-sec", type=int, default=P1_TIME_LIMIT_SEC, help="時間上限 (超えたら止めて未完了)")
    ap.add_argument("--dry-run", action="store_true", help="条件表とコマンド列を出すだけ (起動しない・ピンを作らない)")
    args = ap.parse_args(argv)

    now = time.time()
    battles = resolve_battles(args.battles, args.prelim)
    out_dir = Path(args.out).resolve() if args.out else default_out_dir(now, args.prelim)
    split_file, selection_model = _abs(args.split), _abs(args.selection_model)
    design = plan()
    notes: list = []
    if args.prelim and battles != P1_PRELIM_BATTLES:
        notes.append(f"予備測定だが対戦数は --battles {battles} を使った")

    pin_dir: Optional[Path] = Path(args.pin_dir).resolve() if args.pin_dir else None
    team_text = registered_team_text()
    team_file = out_dir / "team.txt"
    # ピン未作成の dry-run でもコマンドに入るパス
    pin_for_cmd = str(pin_dir) if pin_dir is not None else PIN_PLANNED

    def assemble(pin_now: Optional[Path], started_at: Optional[str]) -> dict:
        pin_s = str(pin_now) if pin_now is not None else pin_for_cmd
        notes_now = list(notes)
        facts = collect_facts(team_text, selection_model, split_file, args.fold, pin_now, notes_now)
        cmds = {cond_name(w): build_command(w, out_dir, team_file, split_file, args.fold, args.seed, battles, selection_model, pin_s)
                for w in design["weights"]}
        env = child_environment(pin_s)
        return build_conditions(design, facts, battles, args.prelim, args.seed, args.fold, str(split_file),
                                args.time_limit_sec, str(out_dir), cmds, env, started_at, notes_now)

    if args.dry_run:
        cond = assemble(pin_dir, None)
        print(json.dumps(cond, ensure_ascii=False, indent=1))
        print("\n# コマンド列 (環境: CHAMPIONS_MODELS_DIR = ピン、スレッド数 1)")
        for c, cmd in cond["commands"].items():
            print(f"{c}: " + " ".join(cmd))
        errs, warns = preflight_errors(cond), preflight_warnings(cond)
        for w in warns:
            print(f"[注意] {w}")
        for e in errs:
            print(f"[起動前の検査 NG] {e}")
        print(f"[dry-run] 起動していない。起動前の検査: {'OK' if not errs else 'NG (' + str(len(errs)) + ' 件)'} / 開始時刻: {SCHEDULE_NOTE}")
        return 0 if not errs else 3

    out_has_results = out_dir.exists() and any(out_dir.glob("rl*.json*"))
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
    _write_json(out_dir / "conditions.json", cond)
    print(f"[p1] 条件表 {out_dir / 'conditions.json'} / {battles} 戦 × {len(cond['commands'])} 条件 / 開始 {started_at}")
    print(f"[p1] 注意: {SCHEDULE_NOTE}")
    env = child_environment(str(pin_dir))
    final = launch(cond, out_dir, env, battles, args.time_limit_sec)
    if args.prelim:
        final["prelim_advice"] = prelim_advice(final["elapsed_sec"], final["reached"])
    _write_json(out_dir / "conditions.json", final)
    print(f"[p1] 終了: incomplete={final['incomplete']} 到達 {final['reached']} 所要 {final['elapsed_sec']} 秒")
    if args.prelim:
        adv = final["prelim_advice"]
        worst = adv["worst_sec_per_battle"]
        print(f"[p1] 予備測定: 秒/戦 (最大) {worst if worst is None else round(worst, 2)} "
              f"/ 本番 {P1_FINAL_BATTLES} 戦の見込み {adv['projected_final_sec'] if adv['projected_final_sec'] is None else round(adv['projected_final_sec'] / 60)} 分")
        if not adv["start_final"]:
            print(f"[p1] 目安: 秒/戦が {P1_ABORT_SEC_PER_BATTLE} を超えた (または測れない) → 本番を始めない目安 "
                  f"({P1_FINAL_BATTLES} 戦で上限 {args.time_limit_sec} 秒を超える見込み)。判断は人")
    print(f"[p1] 集計: python -m tools.usefulness_verdict --out {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
