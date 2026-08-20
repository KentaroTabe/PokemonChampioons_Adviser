"""構築提案の運用フロー (段階ゲート付き)。

段階1 (制約付き改善): 現パーティ (config/my_team.json から推定した6体) を
  種に、最大2枠までの入替空間を進化探索 (tools/evolve_teams の
  --seed-myteam 系) で探す。
段階2 (一般提案): メタ全体を初期集団にした進化探索 (日次 evolve と同系。
  メタ遷移外挿 --forecast-mix / PSROアーカイブ --archive-mix を利用可能な
  ときは混ぜる) で、考えられる構築から最良を提案する。

どちらの段階も**評価は実対戦** (ローカルShowdown、両サイド同一のRL方策)
で行い、選出モデルはパーティ評価に使わない。根拠は実測:
機能埋め込みでも未知チームの選出品質は相性ベース同等まで劣化する
(champions_agent/train/train_selection.py 冒頭の2026-07-29表)。
選出モデルの役割は「採用後の選出助言」であり、提案の採用手順に
データ収集→微調整 (collect_selection → train_selection) を含める。

必要条件の全体設計は docs/TEAM_PROPOSAL_DESIGN.md 参照。

    python -m tools.team_proposal --check              # 両段階の運用可否
    python -m tools.team_proposal --check --stage 2
    python -m tools.team_proposal --propose --stage 1
    python -m tools.team_proposal --propose --stage 2 --accept-battles 300
"""
from __future__ import annotations

import argparse
import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "logs" / "team_proposal"

# ---- 運用ゲートの閾値 (根拠は docs/TEAM_PROPOSAL_DESIGN.md) ----
USAGE_FRESH_DAYS = 7          # 使用率スナップショットの許容鮮度 (日次更新前提)
MIN_META_POOL = 40            # 段階2: 候補空間に必要な型プール種族数
MIN_EVAL_BATTLES = 40         # 1候補の評価戦数の下限 (SE<=0.08)
MIN_ACCEPT_BATTLES = 100      # 受入検定の片側戦数の下限 (対応のあるSE<=0.05)
VAL_GAIN_GATE_PCT = 5.0       # 選出モデルの未知チーム検証 改善率ゲート (%)
REQUIRED_BUILD_FIELDS = ("技", "性格", "能力ポイント")   # 種チーム忠実性の必須登録
RECOMMENDED_BUILD_FIELDS = ("持ち物", "特性")


@dataclass
class Condition:
    """運用条件1件。ok=None は「未計測」(hardなら不合格扱い)。"""
    cid: str
    label: str
    hard: bool
    ok: bool | None
    measured: str
    fix: str = ""

    @property
    def passed(self) -> bool:
        return self.ok is True


def _mark(c: Condition) -> str:
    if c.ok is True:
        return "✅"
    return "❌" if c.hard else "⚠️"


# ======================================================================
# 純粋な判定ロジック (テスト対象。I/Oは下の measure_* に隔離)
# ======================================================================
def registration_gaps(entries: dict) -> dict:
    """パーティ登録の欠落 {種族: [欠落フィールド]} (完全なら空辞書)"""
    gaps: dict = {}
    for ja, e in (entries or {}).items():
        missing = [f for f in REQUIRED_BUILD_FIELDS if not (e or {}).get(f)]
        moves = (e or {}).get("技") or []
        if "技" not in missing and len(moves) < 4:
            missing.append(f"技{len(moves)}/4")
        if missing:
            gaps[ja] = missing
    return gaps


def paired_verdict(a_outcomes: list, b_outcomes: list) -> dict:
    """同一相手列での対応のある勝敗比較 (a=提案, b=現行)。

    返り値: {n, mean, se, verdict}。verdict は
    "採用推奨" (a有意に上) / "現行維持" (b有意に上) / "差は誤差の範囲"。
    """
    n = min(len(a_outcomes or []), len(b_outcomes or []))
    if n < 2:
        return {"n": n, "mean": None, "se": None, "verdict": "検定不能"}
    d = [a_outcomes[i] - b_outcomes[i] for i in range(n)]
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1)
    se = (var / n) ** 0.5
    if se > 0 and mean > 2 * se:
        verdict = "採用推奨"
    elif se > 0 and mean < -2 * se:
        verdict = "現行維持"
    else:
        verdict = "差は誤差の範囲"
    return {"n": n, "mean": mean, "se": se, "verdict": verdict}


def evaluate_conditions(inputs: dict, stage: int) -> list:
    """測定値の辞書から条件リストを作る (純粋関数)。

    inputs のキー (measure_inputs が集める):
      usage_age_days, ranked_team_count, policy_best_exists,
      showdown_listening, party (dict|None), party_gaps,
      sel_general_exists, sel_val (dict|None), sel_in_dist (bool|None),
      meta_pool_size, forecast_available, archive_size,
      battles (int), accept_battles (int)
    """
    conds: list = []

    age = inputs.get("usage_age_days")
    conds.append(Condition(
        "C1", "使用率メタの鮮度", True,
        None if age is None else age <= USAGE_FRESH_DAYS,
        "未取得" if age is None else f"最新スナップショット {age:.1f}日前",
        "scripts/ 経由で usage 更新 (launchd usage-update が毎朝6:30に実行)"))

    rt = inputs.get("ranked_team_count")
    from champions_agent.config import USAGE_MIN_RANKED_TEAMS
    conds.append(Condition(
        "C2", "ランクド構築プールの量", True,
        None if rt is None else rt >= USAGE_MIN_RANKED_TEAMS,
        "未取得" if rt is None else f"{rt}構築 (下限{USAGE_MIN_RANKED_TEAMS})",
        "使用率DBの取り込み状況を確認 (champions_agent.data.ingest)"))

    conds.append(Condition(
        "C3", "評価用RL方策 (_best)", True,
        bool(inputs.get("policy_best_exists")),
        "battle_policy_balance_best.zip "
        + ("あり" if inputs.get("policy_best_exists") else "なし"),
        "学習ループが best を更新するまで待つ (best_checkpoint)"))

    conds.append(Condition(
        "C4", "ローカルShowdown (8100)", True,
        bool(inputs.get("showdown_listening")),
        "LISTEN中" if inputs.get("showdown_listening") else "未起動",
        "bash scripts/ensure_showdown.sh"))

    if stage == 1:
        party = inputs.get("party") or {}
        conds.append(Condition(
            "S1-1", "現パーティ6体の確定", True, len(party) == 6,
            f"{len(party)}体: {' / '.join(list(party)[:6])}" if party else "推定不能",
            "config/my_team.json の登録と直近対戦ログを確認"))

        gaps = inputs.get("party_gaps") or {}
        conds.append(Condition(
            "S1-2", "現パーティの登録完全性 (技4/性格/能力ポイント)", True,
            (len(party) == 6 and not gaps),
            "完全" if not gaps else "欠落: " + "; ".join(
                f"{ja}({', '.join(m)})" for ja, m in gaps.items()),
            "もっと見る画面での再登録 か config/my_team.json を補完"))

        battles = inputs.get("battles") or 0
        conds.append(Condition(
            "S1-3", "評価戦数の下限", True, battles >= MIN_EVAL_BATTLES,
            f"--battles {battles} (下限{MIN_EVAL_BATTLES}, SE≤0.08)",
            f"--battles {MIN_EVAL_BATTLES} 以上を指定"))

    if stage == 2:
        mp = inputs.get("meta_pool_size")
        conds.append(Condition(
            "S2-1", "メタ型プールの規模", True,
            None if mp is None else mp >= MIN_META_POOL,
            "未取得" if mp is None else f"{mp}種 (下限{MIN_META_POOL})",
            "使用率DBの取り込みを確認"))

        ab = inputs.get("accept_battles") or 0
        conds.append(Condition(
            "S2-2", "受入検定の戦数", True, ab >= MIN_ACCEPT_BATTLES,
            f"--accept-battles {ab} (下限{MIN_ACCEPT_BATTLES})",
            f"--accept-battles {MIN_ACCEPT_BATTLES} 以上を指定"))

        conds.append(Condition(
            "S2-3", "メタ遷移の外挿 (2ヶ月分の履歴)", False,
            bool(inputs.get("forecast_available")),
            "利用可" if inputs.get("forecast_available")
            else "履歴1ヶ月分のみ (現行メタで評価)",
            "使用率スナップショットの月次蓄積を待つ"))

        conds.append(Condition(
            "S2-4", "PSROアーカイブ (対策の普及への頑健性)", False,
            (inputs.get("archive_size") or 0) > 0,
            f"{inputs.get('archive_size') or 0}件",
            "日次 evolve の --update-archive が蓄積する"))

    # 選出モデル関連 (両段階共通)。提案の計算には不要だが、
    # 「採用後の選出助言の品質」と「モデルによる枝刈りの有効化」に関わる
    sv = inputs.get("sel_val") or {}
    gain = sv.get("gain_pct")
    conds.append(Condition(
        "M1", "選出モデルの構成汎化 (未知チーム検証)", False,
        None if gain is None else (sv.get("split") == "unseen_teams"
                                   and gain >= VAL_GAIN_GATE_PCT),
        "未計測 (train_selection の再実行でMETAに記録される)" if gain is None
        else f"改善{gain:+.1f}% / 分割={sv.get('split')} "
             f"(ゲート+{VAL_GAIN_GATE_PCT:.0f}%, {sv.get('at', '?')})",
        "python -m champions_agent.train.train_selection で再計測"))

    in_dist = inputs.get("sel_in_dist")
    conds.append(Condition(
        "M2", "現パーティが選出モデルの学習分布内", False,
        in_dist,
        "分布内" if in_dist else "分布外 (選出助言は参考値表示)",
        "bash scripts/collect_selection.sh 2 2500 myteam → train_selection"))

    return conds


def hard_ok(conds: list) -> bool:
    return all(c.passed for c in conds if c.hard)


def render_report(conds: list, stage: int) -> str:
    lines = [f"■ 構築提案 運用条件チェック (段階{stage})"]
    for c in conds:
        kind = "必須" if c.hard else "推奨"
        lines.append(f"  {_mark(c)} [{c.cid}/{kind}] {c.label}: {c.measured}")
        if not c.passed and c.fix:
            lines.append(f"       → {c.fix}")
    lines.append("  判定: " + ("運用可能 (必須条件をすべて満たしています)"
                              if hard_ok(conds)
                              else "運用不可 (未達の必須条件があります)"))
    return "\n".join(lines)


# ======================================================================
# 測定 (I/O)
# ======================================================================
def measure_inputs(stage: int, battles: int, accept_battles: int) -> dict:
    out: dict = {"battles": battles, "accept_battles": accept_battles}

    # 使用率スナップショットの鮮度
    try:
        from champions_agent.config import USAGE_TARGET_FORMAT
        from champions_agent.data import database as db
        with db.get_connection() as conn:
            row = conn.execute(
                """SELECT MAX(fetched_at) AS t FROM usage_snapshot
                   WHERE format = ?""", (USAGE_TARGET_FORMAT,)).fetchone()
        if row and row["t"]:
            fetched = time.mktime(time.strptime(str(row["t"])[:19],
                                                "%Y-%m-%d %H:%M:%S"))
            out["usage_age_days"] = (time.time() - fetched) / 86400.0
    except Exception:
        out["usage_age_days"] = None

    try:
        from champions_agent.env.ranked_teams import build_ranked_teams
        out["ranked_team_count"] = len(build_ranked_teams(
            include_external=False))
    except Exception:
        out["ranked_team_count"] = None

    from champions_agent.config import MODELS_DIR
    out["policy_best_exists"] = \
        (MODELS_DIR / "battle_policy_balance_best.zip").exists()

    try:
        with socket.create_connection(("127.0.0.1", 8100), timeout=1.0):
            out["showdown_listening"] = True
    except OSError:
        out["showdown_listening"] = False

    if stage == 1:
        try:
            from tools.evaluate_team import current_team_entries
            party = current_team_entries()
            out["party"] = party
            out["party_gaps"] = registration_gaps(party)
        except Exception as e:
            out["party"] = None
            out["party_gaps"] = {"(取得失敗)": [str(e)[:40]]}

    if stage == 2:
        try:
            from tools.evolve_teams import _meta_pool_rows, forecast_scores, \
                load_archive
            out["meta_pool_size"] = len(_meta_pool_rows())
            out["forecast_available"] = forecast_scores() is not None
            out["archive_size"] = len(load_archive())
        except Exception:
            out.setdefault("meta_pool_size", None)
            out.setdefault("forecast_available", False)
            out.setdefault("archive_size", 0)

    # 選出モデル
    try:
        from champions_agent.agent.selection_model import (
            GENERAL_MODEL_PATH, META_PATH, is_in_distribution,
        )
        out["sel_general_exists"] = GENERAL_MODEL_PATH.exists()
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        out["sel_val"] = meta.get("val")
        party = out.get("party")
        if party is None and stage == 2:
            try:
                from tools.evaluate_team import current_team_entries
                party = current_team_entries()
            except Exception:
                party = None
        if party:
            from vision.normalize import NameResolver
            r = NameResolver()
            ids = []
            for ja in party:
                sp = r.resolve_species(ja, cutoff=0.85)
                ids.append(sp[1] if sp else ja)
            out["sel_in_dist"] = is_in_distribution(ids)
    except Exception:
        out.setdefault("sel_general_exists", False)
        out.setdefault("sel_val", None)
        out.setdefault("sel_in_dist", None)

    return out


# ======================================================================
# 提案の実行
# ======================================================================
def _evolve_args(stage: int, ns: argparse.Namespace) -> argparse.Namespace:
    """tools.evolve_teams.run() へ渡す引数を組み立てる"""
    forecast = ns.forecast_mix
    archive = ns.archive_mix
    if stage == 1:
        # 制約付き改善は「現メタでの実力」を測る。外挿やPSROは段階2で使う
        forecast = 0.0
        archive = 0.0
    return argparse.Namespace(
        population=ns.population, generations=ns.generations,
        battles=ns.battles, concurrency=ns.concurrency,
        forecast_mix=forecast, archive_mix=archive,
        update_archive=False,
        seed_myteam=(stage == 1), seed_file=None,
        locked=ns.locked, max_changes=ns.max_changes,
        set_mut=ns.set_mut, seed=ns.seed,
    )


async def _acceptance_test(best_text: str, accept_battles: int,
                           log) -> dict:
    """提案 vs 現行パーティの「同じ相手列」での対応のある受入検定"""
    import random as _random
    from tools.evaluate_team import build_myteam_text, evaluate_team_text
    from tools.evolve_teams import FixedSequenceTeambuilder, build_opponent

    seed_text = build_myteam_text()
    rng = _random.Random(20260820)
    opp = build_opponent(0.0, 0.0, rng)   # 受入は現行メタ分布のみで測る
    opponents = [opp.yield_team() for _ in range(accept_battles)]

    log(f"[受入検定] 提案 vs 現行 (各{accept_battles}戦, 同一相手列)")
    r_new = await evaluate_team_text(
        best_text, n_battles=accept_battles,
        opp_teambuilder=FixedSequenceTeambuilder(list(opponents)))
    r_cur = await evaluate_team_text(
        seed_text, n_battles=accept_battles,
        opp_teambuilder=FixedSequenceTeambuilder(list(opponents)))
    v = paired_verdict(r_new.get("outcomes") or [], r_cur.get("outcomes") or [])
    v["new_win_rate"] = r_new.get("win_rate")
    v["cur_win_rate"] = r_cur.get("win_rate")
    return v


def _adoption_steps(in_dist: bool | None) -> list:
    steps = [
        "1. 提案構築の型 (技/性格/能力ポイント/持ち物) をゲーム内で組み、"
        "もっと見る画面で登録する (config/my_team.json へ自動取込み)",
        "2. bash scripts/collect_selection.sh 2 2500 myteam "
        "(新パーティの選出データ収集。学習分布外のままだと選出助言が参考値)",
        "3. python -m champions_agent.train.train_selection (微調整の学習)",
        "4. 接続テストで助言品質を確認 (bash scripts/start_connection_test.sh)",
    ]
    if in_dist:
        steps[1] += " ※現在は分布内のため差分収集でよい"
    return steps


def propose(stage: int, ns: argparse.Namespace) -> int:
    conds = evaluate_conditions(
        measure_inputs(stage, ns.battles, ns.accept_battles), stage)
    print(render_report(conds, stage))
    if not hard_ok(conds) and not ns.force:
        print("→ 必須条件が未達のため実行しません (--force で強行、結果は参考値)")
        return 2

    # 実行時の競合回避 (学習中の対戦ログ・スイープの一時停止フラグ)
    from tools.check_battle_active import battle_active
    if battle_active(3.0):
        print("→ 対戦中のため実行しません (アドバイザーと競合させない)")
        return 3
    if (REPO / "logs" / "PAUSE_TRAINING").exists():
        print("→ PAUSE_TRAINING があるため実行しません (測定系との競合回避)")
        return 3

    import asyncio
    from tools.evolve_teams import run as evolve_run

    log = lambda m: print(m, flush=True)   # noqa: E731
    t0 = time.time()
    result = asyncio.run(evolve_run(_evolve_args(stage, ns), log))

    accept = None
    if ns.accept_battles > 0:
        accept = asyncio.run(_acceptance_test(
            result["best_text"], ns.accept_battles, log))

    in_dist = next((c.ok for c in conds if c.cid == "M2"), None)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M")
    payload = {
        "stage": stage, "at": time.strftime("%Y-%m-%d %H:%M"),
        "elapsed_sec": round(time.time() - t0, 1),
        "conditions": [{"cid": c.cid, "label": c.label, "hard": c.hard,
                        "ok": c.ok, "measured": c.measured} for c in conds],
        "params": {"population": ns.population,
                   "generations": ns.generations, "battles": ns.battles,
                   "max_changes": ns.max_changes, "locked": ns.locked,
                   "accept_battles": ns.accept_battles},
        "best": {"ja": result.get("best_ja"),
                 "fitness": result.get("fitness"),
                 "text": result.get("best_text")},
        "evolve_run": result.get("path"),
        "acceptance": accept,
        "adoption_steps": _adoption_steps(in_dist),
    }
    out_json = OUT_DIR / f"proposal_{ts}.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print("\n===== 構築提案 =====")
    print(f"段階{stage} / 提案: {result.get('best_ja')} "
          f"(探索時の勝率 {result.get('fitness')})")
    if accept:
        mean = accept.get("mean")
        se = accept.get("se")
        print(f"受入検定 (対応のある比較, n={accept.get('n')}): "
              f"提案{accept.get('new_win_rate')} vs 現行{accept.get('cur_win_rate')} "
              f"/ 差 {mean:+.3f} ± {se:.3f}(SE)" if mean is not None
              else "受入検定: 検定不能")
        print(f"判定: {accept.get('verdict')}")
    print("採用する場合の手順:")
    for s in payload["adoption_steps"]:
        print(f"  {s}")
    print(f"保存: {out_json}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="構築提案 (段階ゲート付き)")
    ap.add_argument("--check", action="store_true", help="運用可否の表示のみ")
    ap.add_argument("--propose", action="store_true", help="提案を実行")
    ap.add_argument("--stage", type=int, default=None, choices=(1, 2),
                    help="対象段階 (--check で省略時は両方を表示)")
    ap.add_argument("--force", action="store_true",
                    help="必須条件未達でも実行 (結果は参考値)")
    ap.add_argument("--population", type=int, default=10)
    ap.add_argument("--generations", type=int, default=3)
    ap.add_argument("--battles", type=int, default=60,
                    help="進化探索での1候補あたり評価戦数")
    ap.add_argument("--accept-battles", type=int, default=200,
                    help="受入検定 (提案vs現行) の片側戦数。0で省略")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--max-changes", type=int, default=2,
                    help="段階1: 現パーティから同時に変えてよい枠数")
    ap.add_argument("--locked", default=None,
                    help="段階1: 入替禁止の種族 (カンマ区切り、日本語)")
    ap.add_argument("--forecast-mix", type=float, default=0.3)
    ap.add_argument("--archive-mix", type=float, default=0.2)
    ap.add_argument("--set-mut", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=None)
    ns = ap.parse_args()

    if ns.propose:
        raise SystemExit(propose(ns.stage or 1, ns))
    # 既定は --check (--stage 省略時は両段階を表示)
    for st in ((ns.stage,) if ns.stage else (1, 2)):
        conds = evaluate_conditions(
            measure_inputs(st, ns.battles, ns.accept_battles), st)
        print(render_report(conds, st))
        print()


if __name__ == "__main__":
    main()
