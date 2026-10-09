"""2026-09-04〜05 の信念つき探索アークの判定を、正しい自チームの型で測り直す (2026-10-09 ユーザー判断。起動は運用側)。

    bash scripts/arc_remeasure.sh all --dry-run          # コマンド列と見積もり時間だけ (ピンを作らない・起動しない)
    bash scripts/arc_remeasure.sh p9_600                 # 判定を選んで回す (all / 判定名 / 判定名,判定名 / baseline)
    bash scripts/arc_remeasure.sh p9_300 --battles 2 --pin-dir <既存のピン>   # 煙試験 (各腕 2 戦)
    python -m tools.arc_remeasure_summary logs/arc_remeasure_<stamp>          # 当時の値との差と判定

背景: docs/incidents/reports/2026-09-04-sim-team-registration-off-by-one.md。advisor-as-player (tools.check_advisor_player) を
--team-file なしで回すと、対戦 k の選出・行動が対戦 k+1 のチームの型で計算されていた (d7869aff で修正)。

対象の判定 (CATALOG): 影響を受けたのは advisor-as-player の測定だけ。アークのうち探索プレイヤー (tools.check_search_expert) の判定
(P6-a / P6-b / P7 / P7' / P8 / P10) は、自分側を poke-env の battle.team の実数値 (Showdown の request) から作り、シムの型の登録
(register_team_text / get_my_build) を通らないので影響がない (EXCLUDED。2026-10-09 にコードを読んで確認、当時の版 8786537a も同じ)。

各腕は当時と同じ seed (20260904)・対戦数・引数 (K=0、sensor_q 0、workers 1、RL 加点 25、search_blend 0 / 40)。当時の
check_advisor_player に無かった選択は当時の挙動に合わせて明示する: 選出 = teampreview (当時の teampreview_order)、相手 =
heuristic 操縦 + heuristic 選出 (当時の make_benchmark_player)、フォーマット = M-B (gen9championsbssregmb、--format mc で現行)、
採点方式 = legacy、登録参照 (config/my_team.json) は無効化 (--use-my-team で当時どおり有効)。
変えられないもの: 助言エンジンのコード (当時から 5 週間ぶんの変更) と RL モデル (当時のピンは無い。ピンは 9/5 04:09 に導入され、
最初のピンは 9/5 18:46 で、アーク中は常時学習中のライブの EMA を読んでいた)。なので「忠実な再測定」にはならず「現行条件での再測定」。

RL モデル: CHAMPIONS_MODELS_DIR か --pin-dir があればそれ (既存のピン)、無ければ開始時に scripts/pin_models.sh で新しいピンを作る。
どちらにしたかは manifest.json の pin に残す。子プロセスはスレッド数 1 (tools.team_build.racing.child_env)。
純粋な部分 (計画・コマンド・環境・割り付け・見積もり) は tests/test_arc_remeasure.py で確かめる。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from champions_agent.config import (
    ARC_REMEASURE_DEADLINE, ARC_REMEASURE_FORMAT_DEFAULT, ARC_REMEASURE_FORMATS, ARC_REMEASURE_JOBS,
    ARC_REMEASURE_META_SNAPSHOT, ARC_REMEASURE_OUT_PREFIX, ARC_REMEASURE_SEC_PER_BATTLE, MY_TEAM_DISABLED_ENV,
    SHOWDOWN_PORT)

REPO = Path(__file__).resolve().parent.parent
ARC_SEED = 20260904
ARC_RL_BLEND = 25.0
PROGRESS_SEC = 300          # 進捗の表示間隔 (秒)

# 当時の腕に共通の引数 (check_advisor_player)。search_blend だけ腕ごと
ARC_COMMON_ARGS = ("--opp-seed", str(ARC_SEED), "--skip-random", "--belief-k", "0", "--sensor-q", "0.0",
                   "--workers", "1", "--rl-blend", f"{ARC_RL_BLEND:g}", "--pick-policy", "teampreview",
                   "--opp-pilot", "heuristic", "--opp-pick-policy", "heuristic")

# 判定ごとの当時の記録 (old は logs/ からの相対パス)。過去の事実なので設定ファイルではなくここに置く
CATALOG: dict = {
    "p9_300": {
        "label": "advisor baseline + P9 中間 (300 戦)",
        "registered": "2026-09-04 20:30 P9 事前登録 (基準 A は 21:05 の『advisor-as-player 初計測』を兼ねる)",
        "judged": "2026-09-04 22:10",
        "rule": "B−A ≥ +0.05 かつ 95%CI下限 > 0。CI が 0 を跨げば判定不能 → 倍増して 1 回だけ再測定 (§H)",
        "mde": 0.05, "final": False,
        "old_verdict": "判定不能 (+0.057±0.036、CI下限 −0.013) → p9_600 で再測定",
        "old_command": "bash scripts/advisor_baseline.sh 300 0 [--search-blend 40] (A 20:05〜21:04 → B 21:27〜22:05、逐次)",
        "battles": 300,
        "arms": {"A": {"search_blend": 0.0, "old": "advisor_verdict/advisor_20260904_2005.json"},
                 "B": {"search_blend": 40.0, "old": "advisor_verdict/advisor_20260904_2127.json"}},
    },
    "p9_600": {
        "label": "P9 (600 戦、倍増の再測定)",
        "registered": "2026-09-04 20:30 P9 事前登録 + 22:10 の倍増の規則",
        "judged": "2026-09-05 00:25",
        "rule": "B−A ≥ +0.05 かつ 95%CI下限 > 0 (最終。不能なら棄却)",
        "mde": 0.05, "final": True,
        "old_verdict": "棄却 (+0.037±0.026、CI下限 −0.014)",
        "old_command": "bash scripts/advisor_baseline.sh 600 0 [--search-blend 40] (A 22:37〜23:44 → B 23:28〜00:22、ほぼ逐次)",
        "battles": 600,
        "arms": {"A": {"search_blend": 0.0, "old": "advisor_verdict/advisor_20260904_2237.json"},
                 "B": {"search_blend": 40.0, "old": "advisor_verdict/advisor_20260904_2328.json"}},
    },
    "p9b_1500": {
        "label": "P9' (各 1,500 戦、最小効果量 +0.03)",
        "registered": "2026-09-05 09:00 P9' 事前登録 (記載は実施後の 09:00、実施 00:36〜04:05)",
        "judged": "2026-09-05 04:10",
        "rule": "B−A ≥ +0.03 かつ 95%CI下限 > 0 (最終。棄却時は再挑戦しない)",
        "mde": 0.03, "final": True,
        "old_verdict": "棄却 (−0.007±0.017、CI下限 −0.039)",
        "old_command": "check_advisor_player --battles 1500 --opp-seed 20260904 --skip-random --belief-k 0 "
                       "[--search-blend 40] を A/B 同時起動 (log は advisor_verdict/advisor_20260905_0036.log)",
        "battles": 1500,
        "arms": {"A": {"search_blend": 0.0, "old": "p9_verdict/p9b_A.json"},
                 "B": {"search_blend": 40.0, "old": "p9_verdict/p9b_B.json"}},
    },
}
# 判定名の別名: baseline = p9_300 の A だけ (advisor baseline の 300 戦)
ALIASES = {"baseline": ("p9_300", ("A",))}
VERDICT_ORDER = ("p9_300", "p9_600", "p9b_1500")

# 再測定の対象外 (探索プレイヤーの判定。自分側は battle.team の実数値で、型の登録を通らない)
_SEARCH_EXPERT_REASON = ("tools.check_search_expert (探索プレイヤー) の測定。自分側は poke-env の battle.team の実数値で作り "
                         "(search_expert._mon_view own=True)、シムの型の登録 (register_team_text / get_my_build) を通らないので影響なし")
EXCLUDED: dict = {
    "p6a": {"label": "P6-a 葉評価 OFF/ON (各 300)", "old_verdict": "維持 (0.547 vs 0.420)", "reason": _SEARCH_EXPERT_REASON},
    "p6b": {"label": "P6-b 相手事前分布 λ0/0.5 (各 300)", "old_verdict": "棄却 (0.503 vs 0.510)", "reason": _SEARCH_EXPERT_REASON},
    "p7": {"label": "P7 多世界 current/map/belief (各 300)", "old_verdict": "棄却 (0.380 vs 0.413)", "reason": _SEARCH_EXPERT_REASON},
    "p7b": {"label": "P7' 観測更新つき多世界 (各 300)", "old_verdict": "棄却 (0.513 vs 0.510)", "reason": _SEARCH_EXPERT_REASON},
    "p8": {"label": "P8 センサ世界 4 腕 (各 300)", "old_verdict": "棄却", "reason": _SEARCH_EXPERT_REASON},
    "p10": {"label": "P10 期待ダメージ推定 (各 600)", "old_verdict": "棄却 (0.535 vs 0.568)", "reason": _SEARCH_EXPERT_REASON},
}


# ------------------------------------------------------------------ 純粋
def select(spec: str) -> list:
    """判定の指定 (all / 判定名 / 別名 / カンマ区切り) → [(判定, (腕, ...)), ...] (純粋)。対象外の判定名はエラー"""
    out: dict = {}
    for name in [s.strip() for s in (spec or "").split(",") if s.strip()]:
        if name == "all":
            for v in VERDICT_ORDER:
                out[v] = tuple(CATALOG[v]["arms"])
        elif name in CATALOG:
            out[name] = tuple(CATALOG[name]["arms"])
        elif name in ALIASES:
            v, arms = ALIASES[name]
            out[v] = tuple(sorted(set(out.get(v, ())) | set(arms)))
        elif name in EXCLUDED:
            raise ValueError(f"{name} は再測定の対象外: {EXCLUDED[name]['reason']}")
        else:
            raise ValueError(f"不明な判定: {name} (all / {' / '.join(VERDICT_ORDER)} / {' / '.join(ALIASES)})")
    if not out:
        raise ValueError("判定が指定されていない")
    return [(v, out[v]) for v in VERDICT_ORDER if v in out]


def jobs_for(selection: list, battles_override: Optional[int] = None) -> list:
    """[(判定, 腕)] → 腕ごとの仕事 (純粋)。battles_override は煙試験用 (当時と違う対戦数になる)"""
    jobs = []
    for v, arms in selection:
        entry = CATALOG[v]
        for a in arms:
            arm = entry["arms"][a]
            jobs.append({"verdict": v, "arm": a, "battles": int(battles_override or entry["battles"]),
                         "arc_battles": entry["battles"], "search_blend": float(arm["search_blend"]), "old": arm["old"]})
    return jobs


def arm_paths(out_dir: Path, verdict: str, arm: str) -> dict:
    base = Path(out_dir) / verdict
    return {"json": base / f"{arm}.json", "log": base / f"{arm}.log", "battles": base / f"{arm}.battles.jsonl"}


def build_command(job: dict, out_dir: Path, battle_format: str, python: str = sys.executable) -> list:
    """腕のコマンド (純粋)。当時の腕の引数 + フォーマット + 出力先"""
    p = arm_paths(out_dir, job["verdict"], job["arm"])
    return [python, "-m", "tools.check_advisor_player", "--battles", str(job["battles"]), *ARC_COMMON_ARGS,
            "--search-blend", f"{job['search_blend']:g}", "--battle-format", battle_format,
            "--json", str(p["json"]), "--battle-log", str(p["battles"]),
            "--candidate-id", f"arc:{job['verdict']}:{job['arm']}"]


def child_environment(pin_dir: str, base: Optional[dict] = None, use_my_team: bool = False) -> dict:
    """子プロセスの環境 (純粋): スレッド数 1 の一式 (racing.child_env) + CHAMPIONS_MODELS_DIR = ピン + 採点方式 legacy +
    登録参照の無効化 (use_my_team=False のとき。当時は有効だったが、今の config/my_team.json は当時と違うので既定は切る)"""
    from tools.team_build.racing import child_env
    env = child_env(base)
    env["CHAMPIONS_MODELS_DIR"] = str(pin_dir)
    env["ADVICE_SCORING_MODE"] = "legacy"
    env.pop("RL_BLEND_WEIGHT", None)        # 重みは --rl-blend で明示する
    if use_my_team:
        env.pop(MY_TEAM_DISABLED_ENV, None)
    else:
        env[MY_TEAM_DISABLED_ENV] = "1"
    return env


def schedule(jobs: list, slots: int) -> list:
    """LPT (対戦数の多い順に、いちばん空いている枠へ) で枠に割り付ける (純粋)。返り値: 枠ごとの仕事のリスト"""
    slots = max(1, int(slots))
    lanes: list = [[] for _ in range(slots)]
    loads = [0] * slots
    order = sorted(range(len(jobs)), key=lambda i: (-jobs[i]["battles"], i))
    for i in order:
        k = min(range(slots), key=lambda s: (loads[s], s))
        lanes[k].append(jobs[i])
        loads[k] += jobs[i]["battles"]
    return [lane for lane in lanes if lane]


def makespan_battles(lanes: list) -> int:
    return max((sum(j["battles"] for j in lane) for lane in lanes), default=0)


def estimate(jobs: list, slots: int, sec_per_battle: float = ARC_REMEASURE_SEC_PER_BATTLE) -> dict:
    """見積もり (純粋): 判定ごと (その判定だけを slots 枠で回したとき) と、全体 (LPT の割り付け) の秒数"""
    per: dict = {}
    for v in dict.fromkeys(j["verdict"] for j in jobs):
        js = [j for j in jobs if j["verdict"] == v]
        mk = makespan_battles(schedule(js, min(slots, len(js))))
        per[v] = {"arms": [j["arm"] for j in js], "battles": sum(j["battles"] for j in js),
                  "makespan_battles": mk, "sec": mk * sec_per_battle}
    lanes = schedule(jobs, slots)
    mk = makespan_battles(lanes)
    return {"sec_per_battle": sec_per_battle, "slots": slots, "per_verdict": per,
            "total_battles": sum(j["battles"] for j in jobs), "makespan_battles": mk, "total_sec": mk * sec_per_battle,
            "sequential_sec": sum(j["battles"] for j in jobs) * sec_per_battle,
            "lanes": [[f"{j['verdict']}/{j['arm']}" for j in lane] for lane in lanes]}


def finish_check(start: _dt.datetime, seconds: float, deadline_hhmm: str = ARC_REMEASURE_DEADLINE) -> dict:
    """開始 start から seconds 後の終了見込みと、start の後で最初に来る deadline (HH:MM) との比較 (純粋)"""
    end = start + _dt.timedelta(seconds=float(seconds))
    hh, mm = (int(x) for x in deadline_hhmm.split(":"))
    dl = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if dl <= start:
        dl += _dt.timedelta(days=1)
    return {"start": start.strftime("%Y-%m-%d %H:%M"), "end": end.strftime("%Y-%m-%d %H:%M"),
            "deadline": dl.strftime("%Y-%m-%d %H:%M"), "ok": end <= dl,
            "margin_min": round((dl - end).total_seconds() / 60.0, 1)}


def fmt_hours(sec: float) -> str:
    return f"{sec / 3600.0:.2f} 時間 ({sec / 60.0:.0f} 分)"


def preflight_errors(facts: dict, battle_format: str, out_has_results: bool) -> list:
    """起動前の検査 (純粋)。不合格なら起動しない"""
    errs = []
    if facts.get("meta_pin") != ARC_REMEASURE_META_SNAPSHOT:
        errs.append(f"META_PIN が {facts.get('meta_pin')} (当時は {ARC_REMEASURE_META_SNAPSHOT})。軸が違うので起動しない")
    if not facts.get("pin_has_ckpt"):
        errs.append(f"ピン {facts.get('pin_dir')} に battle_policy_*_ema.zip / _best.zip が無い")
    if not facts.get("showdown_listening"):
        errs.append(f"Showdown (port {SHOWDOWN_PORT}) が待ち受けていない")
    if facts.get("format_known") is False:
        errs.append(f"フォーマット {battle_format} が pokemon-showdown/config/formats.ts に見つからない")
    if out_has_results:
        errs.append("出力先に前の結果 (*.json) がある")
    return errs


def manifest(selection: list, jobs: list, est: dict, battle_format: str, pin: dict, env: dict, facts: dict,
             battles_override: Optional[int], use_my_team: bool) -> dict:
    """条件の記録 (純粋)"""
    return {
        "purpose": "2026-09-04〜05 の信念つき探索アークの判定 (advisor-as-player) を正しい自チームの型で再測定 "
                   "(docs/incidents/reports/2026-09-04-sim-team-registration-off-by-one.md)",
        "kind": "現行条件での再測定 (助言エンジンのコードと RL モデルは当時と違う。seed・対戦数・腕の引数・META_PIN・フォーマットは当時と同じ)",
        "smoke": battles_override is not None,
        "battles_override": battles_override,
        "verdicts": {v: list(arms) for v, arms in selection},
        "battle_format": battle_format,
        "seed": ARC_SEED,
        "common_args": list(ARC_COMMON_ARGS),
        "jobs": jobs,
        "estimate": est,
        "pin": pin,
        "env": {k: env.get(k) for k in ("CHAMPIONS_MODELS_DIR", "ADVICE_SCORING_MODE", MY_TEAM_DISABLED_ENV,
                                        "OMP_NUM_THREADS", "MKL_NUM_THREADS") if env.get(k) is not None},
        "use_my_team": use_my_team,
        "facts": facts,
        "excluded": {k: v["reason"] for k, v in EXCLUDED.items()},
    }


def progress_line(elapsed: float, counts: dict, totals: dict) -> str:
    parts = " / ".join(f"{k} {counts.get(k, 0)}/{totals[k]}" for k in totals)
    return f"[arc] {elapsed / 60.0:.0f} 分: {parts}"


# ------------------------------------------------------------------ 取得・起動 (副作用)
def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _format_known(battle_format: str) -> Optional[bool]:
    """pokemon-showdown/config/formats.ts の name を id に直して探す (読めなければ None = 未確認)"""
    import re
    path = REPO / "pokemon-showdown" / "config" / "formats.ts"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    ids = {re.sub(r"[^a-z0-9]", "", m.lower()) for m in re.findall(r'name:\s*"([^"]+)"', text)}
    return battle_format in ids


def _git(args: list) -> Optional[str]:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(REPO), timeout=30).stdout.strip()
    except Exception:
        return None


def collect_facts(pin_dir: Optional[Path], battle_format: str) -> dict:
    from champions_agent.env.ranked_teams import pinned_meta_snapshot_id
    ck = None
    if pin_dir is not None:
        for name in ("battle_policy_balance_ema.zip", "battle_policy_balance_best.zip"):
            if (Path(pin_dir) / name).exists():
                ck = name
                break
    try:
        from importlib.metadata import version as _version
        poke_env_v = _version("poke-env")
    except Exception:
        poke_env_v = None
    # 当時 (9/4〜05) の venv は python 3.9 (log の site-packages のパス)。版の違いは条件の違いとして残す
    return {"python": sys.version.split()[0], "poke_env": poke_env_v,
            "meta_pin": pinned_meta_snapshot_id(), "pin_dir": str(pin_dir) if pin_dir else None,
            "pin_has_ckpt": ck is not None, "pin_ckpt": ck, "showdown_port": SHOWDOWN_PORT,
            "showdown_listening": _listening(SHOWDOWN_PORT), "format_known": _format_known(battle_format),
            "git_head": _git(["rev-parse", "--short", "HEAD"]), "git_branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "git_dirty": [ln[3:] for ln in (_git(["status", "--porcelain"]) or "").splitlines() if ln.strip()]}


def make_pin() -> Path:
    """scripts/pin_models.sh で新しいピンを作る (最終行がピンの絶対パス)"""
    r = subprocess.run(["bash", str(REPO / "scripts" / "pin_models.sh")], capture_output=True, text=True,
                       cwd=str(REPO), timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"ピンの作成に失敗: {r.stderr.strip()}")
    return Path(r.stdout.strip().splitlines()[-1])


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


def launch(jobs: list, out_dir: Path, env: dict, battle_format: str, slots: int) -> dict:
    """LPT の順に slots 本ずつ起動し、空いたら次を起動する。返り値: {判定/腕: returncode}"""
    pending = sorted(jobs, key=lambda j: -j["battles"])
    running: dict = {}
    rcs: dict = {}
    totals = {f"{j['verdict']}/{j['arm']}": j["battles"] for j in jobs}
    t0 = time.time()
    next_report = t0 + PROGRESS_SEC
    try:
        while pending or running:
            while pending and len(running) < slots:
                j = pending.pop(0)
                key = f"{j['verdict']}/{j['arm']}"
                p = arm_paths(out_dir, j["verdict"], j["arm"])
                p["log"].parent.mkdir(parents=True, exist_ok=True)
                lf = p["log"].open("a", encoding="utf-8")
                cmd = build_command(j, out_dir, battle_format)
                lf.write(f"=== [{key}] {time.strftime('%F %T')} {' '.join(cmd)}\n")
                lf.flush()
                running[key] = (subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), env=env), lf)
                print(f"[arc] 起動 {key} pid {running[key][0].pid}", flush=True)
            time.sleep(1.0)
            for key in [k for k, (pr, _) in running.items() if pr.poll() is not None]:
                pr, lf = running.pop(key)
                rcs[key] = pr.returncode
                lf.close()
                print(f"[arc] 終了 {key} rc {pr.returncode} ({(time.time() - t0) / 60.0:.0f} 分)", flush=True)
            if time.time() >= next_report:
                counts = {k: _count_lines(arm_paths(out_dir, *k.split("/"))["battles"]) for k in totals}
                print(progress_line(time.time() - t0, counts, totals), flush=True)
                next_report = time.time() + PROGRESS_SEC
    except KeyboardInterrupt:
        print("[arc] 中断 (Ctrl-C) → 走っている腕を止める", flush=True)
        for key, (pr, lf) in running.items():
            pr.terminate()
            try:
                pr.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pr.kill()
                pr.wait()
            rcs[key] = pr.returncode
            lf.close()
    return rcs


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="2026-09-04〜05 のアークの判定 (advisor-as-player) の再測定")
    ap.add_argument("verdicts", nargs="?", default="all",
                    help=f"all / {' / '.join(VERDICT_ORDER)} / baseline (カンマ区切り可)")
    ap.add_argument("--dry-run", action="store_true", help="コマンド列と見積もり時間だけ出す (ピンを作らない・起動しない)")
    ap.add_argument("--battles", type=int, default=None, help="煙試験用に各腕の対戦数を上書きする (当時の対戦数と違う)")
    ap.add_argument("--jobs", type=int, default=ARC_REMEASURE_JOBS, help="同時に回す腕の数")
    ap.add_argument("--format", choices=sorted(ARC_REMEASURE_FORMATS), default=ARC_REMEASURE_FORMAT_DEFAULT,
                    help="mb = 当時の M-B (既定) / mc = 現行の M-C")
    ap.add_argument("--pin-dir", default=None, help="既存の RL のピン (既定: CHAMPIONS_MODELS_DIR、無ければ pin_models.sh で作る)")
    ap.add_argument("--use-my-team", action="store_true",
                    help="登録参照 (config/my_team.json) を無効化しない (当時の挙動。今の登録は当時と違う)")
    ap.add_argument("--out", default=None, help="出力先 (既定 logs/arc_remeasure_<YYYYMMDD_HHMM>)")
    args = ap.parse_args(argv)

    try:
        selection = select(args.verdicts)
    except ValueError as e:
        print(f"[arc] {e}", file=sys.stderr)
        return 2
    battle_format = ARC_REMEASURE_FORMATS[args.format]
    jobs = jobs_for(selection, args.battles)
    slots = max(1, min(int(args.jobs), len(jobs)))
    est = estimate(jobs, slots)
    now = _dt.datetime.now()
    out_dir = Path(args.out) if args.out else REPO / f"{ARC_REMEASURE_OUT_PREFIX}{now.strftime('%Y%m%d_%H%M')}"

    pin_given = args.pin_dir or os.environ.get("CHAMPIONS_MODELS_DIR")
    pin_planned = str(Path(pin_given).resolve()) if pin_given else "(起動時に scripts/pin_models.sh で作成)"
    sel_s = ", ".join("%s(%s)" % (v, "/".join(a)) for v, a in selection)
    print(f"[arc] 判定: {sel_s}  フォーマット {battle_format}  "
          f"並列 {slots}  出力 {out_dir}")
    print(f"[arc] RL のピン: {pin_planned} (当時のピンは無い: 最初のピンは 2026-09-05 18:46、アーク中はライブの EMA)")
    print("[arc] 対象外 (影響なし): " + ", ".join(EXCLUDED))
    print("[arc] コマンド:")
    for j in jobs:
        print("   " + " ".join(build_command(j, out_dir, battle_format, python=".venv/bin/python")))
    print(f"[arc] 見積もり ({est['sec_per_battle']} 秒/戦 = P1 の実測、{slots} 並列の割り付け):")
    for v, e in est["per_verdict"].items():
        print(f"   {v:<9} 腕 {'/'.join(e['arms'])}  計 {e['battles']} 戦  単独で回すと {fmt_hours(e['sec'])}")
    print(f"   合計 {est['total_battles']} 戦  割り付け {est['lanes']}  → {fmt_hours(est['total_sec'])} "
          f"(逐次なら {fmt_hours(est['sequential_sec'])})")
    fc = finish_check(now, est["total_sec"])
    print(f"   いま始めると終了見込み {fc['end']} (期限 {fc['deadline']}、{'間に合う' if fc['ok'] else '間に合わない'}、"
          f"余裕 {fc['margin_min']} 分)")
    if args.dry_run:
        return 0

    if pin_given:
        pin_dir, pin = Path(pin_given).resolve(), {"dir": None, "source": "existing", "arc_pin": None}
    else:
        pin_dir, pin = make_pin(), {"dir": None, "source": "new (scripts/pin_models.sh)", "arc_pin": None}
    pin["dir"] = str(pin_dir)
    pin["arc_pin"] = "無し (ピンは 2026-09-05 04:09 に導入、最初のピンは 20260905_184623。アーク中は常時学習中のライブの EMA)"
    facts = collect_facts(pin_dir, battle_format)
    out_has = out_dir.exists() and any(out_dir.glob("*/*.json"))
    errs = preflight_errors(facts, battle_format, out_has)
    if errs:
        for e in errs:
            print(f"[arc] 起動しない: {e}", file=sys.stderr)
        return 1
    env = child_environment(str(pin_dir), use_my_team=args.use_my_team)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = manifest(selection, jobs, est, battle_format, pin, env, facts, args.battles, args.use_my_team)
    doc["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[arc] 開始 {doc['started_at']} (manifest.json)", flush=True)
    rcs = launch(jobs, out_dir, env, battle_format, slots)
    doc["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    doc["returncodes"] = rcs
    (out_dir / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[arc] 完了 {doc['finished_at']} rc {rcs}")
    print(f"[arc] 集計: .venv/bin/python -m tools.arc_remeasure_summary {out_dir}")
    return 0 if all(rc == 0 for rc in rcs.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
