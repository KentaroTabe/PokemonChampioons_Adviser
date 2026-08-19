"""接続テストの一括監査: セッション全対戦を1回のsonnetサブタスクで検証する。

sonnetタスクを最小化する方式 (対戦ごとの個別監査を置き換える):
  1. 機械前置フィルタ: HPの急回復 (交代なし)・ひんし後のHP再表示・
     相手7匹化などの矛盾候補をPython側で無料で検出し、その周辺フレームを
     優先的に監査対象へ入れる
  2. 残り予算は「対戦×レコード種別」の層化サンプリング (系統的エラーは
     全対戦に同じ形で現れるため、少数サンプルで欠陥クラスを網羅できる)
  3. フレーム総予算 (既定30枚) に収めて claude 起動は1回だけ

    python -m tools.audit_session                 # マーカー以降 (無ければ直近5対戦)
    python -m tools.audit_session --last 8 --budget 30

生成物: logs/audit_reports/session_<時刻>.md
scripts/end_connection_test.sh から自動実行される。
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import time
from collections import Counter
from pathlib import Path

from tools.audit_extraction import collect_pairs
from tools.audit_subtask import MODEL, PROMPT_HEADER, REPORT_DIR

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
MARKER = REPO / "logs" / ".connection_test_start"

_BATTLE_SCENES = {"command", "move_select", "watch",
                  "field_check", "battle_hud", "field"}


def session_battles(last: int | None = None) -> list:
    """監査対象の対戦ログ。マーカー (接続テスト開始時刻) 以降を優先し、
    無ければ直近last件 (既定5)"""
    files = sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))
    if last is None and MARKER.exists():
        try:
            t0 = float(MARKER.read_text().strip())
            picked = [f for f in files if Path(f).stat().st_mtime >= t0]
            if picked:
                return picked
        except (ValueError, OSError):
            pass
    return files[-(last or 5):]


def detect_anomalies(battle_log: str) -> list:
    """機械検出できる矛盾候補 [(t, 説明)]。sonnetの優先確認対象になる"""
    out = []
    last_hp: dict = {}          # (side, ja) -> (hp, t)
    fainted: set = set()
    last_switch = {"player": 0.0, "opponent": 0.0}
    for line in open(battle_log):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("t", 0.0)
        typ = d.get("type")
        if typ == "events":
            for f in d.get("fired") or []:
                if f == "switch_player":
                    last_switch["player"] = t
                elif f == "switch_opponent":
                    last_switch["opponent"] = t
        elif typ == "scene":
            st = d.get("state") or {}
            for side in ("player", "opponent"):
                party = (st.get(side) or {}).get("party") or []
                named = [p for p in party if p.get("ja")]
                if len(named) > 6:
                    out.append((t, f"{side}のパーティが{len(named)}匹 "
                                   "(ルール上の上限6を超過)"))
                for p in named:
                    ja, hp = p["ja"], p.get("hp")
                    if hp is None:
                        continue
                    key = (side, ja)
                    prev = last_hp.get(key)
                    if key in fainted and hp > 5:
                        out.append((t, f"{side}:{ja} ひんし後にHP{hp:.0f}%"
                                       "が再表示 (蘇生は存在しない)"))
                        fainted.discard(key)
                    elif prev is not None and hp - prev[0] > 60 and \
                            t - last_switch[side] > 20:
                        out.append((t, f"{side}:{ja} HP{prev[0]:.0f}%→"
                                       f"{hp:.0f}%へ急回復 (交代検出なし)"))
                    # ひんし扱いは状態が明示的に fainted の場合のみ。
                    # hp<=1 だけで判定すると、きあいのタスキで1%残った
                    # 生存個体や0%誤読の1フレームが「ひんし」になり、
                    # その後の正常表示が「蘇生」矛盾として誤検出される
                    # (2026-08-19 opus監査: ドリュウズ/サザンドラ/ウルガモス)
                    if p.get("status") == "fainted":
                        fainted.add(key)
                    last_hp[key] = (hp, t)
    # 同種の連続検出は1件に圧縮する
    dedup, seen = [], set()
    for t, desc in out:
        k = desc.split(" (")[0]
        if k in seen:
            continue
        seen.add(k)
        dedup.append((t, desc))
    return dedup


def _pair_kind(pair: dict) -> str:
    claim = pair["claim"]
    if claim.startswith("scene="):
        return "scene:" + claim.split("\n")[0][len("scene="):]
    return claim.split(":")[0]


def select_pairs(per_battle: list, budget: int) -> list:
    """[(battle名, pairs, anomalies)] から監査対象ペアを選ぶ (純粋関数)。

    1. 矛盾候補の周辺ペア (±20s) を最優先で採用 (claimに⚠を付す)
    2. 残り予算を対戦×レコード種別のラウンドロビンで充当
    フレーム総数が budget を超えない。
    """
    chosen, frames = [], set()

    def take(battle, pair, note=None):
        if pair["frame"] not in frames and len(frames) >= budget:
            return False
        frames.add(pair["frame"])
        p = dict(pair)
        p["battle"] = battle
        if note:
            p["claim"] = f"⚠矛盾候補: {note}\n" + p["claim"]
        chosen.append(p)
        return True

    taken_ids = set()
    for battle, pairs, anomalies in per_battle:
        for t, desc in anomalies:
            best = min(pairs, key=lambda p: abs(p["t"] - t), default=None)
            if best is not None and abs(best["t"] - t) <= 20 and \
                    id(best) not in taken_ids:
                if take(battle, best, note=desc):
                    taken_ids.add(id(best))

    # 層化サンプリング: 対戦×種別ごとに均等に補充する
    buckets = []
    for battle, pairs, _ in per_battle:
        by_kind: dict = {}
        for p in pairs:
            if id(p) in taken_ids:
                continue
            by_kind.setdefault(_pair_kind(p), []).append(p)
        for kind, ps in sorted(by_kind.items()):
            # 各バケット内は時間的に散らす (中央値優先)
            ps = sorted(ps, key=lambda p: p["t"])
            order = [ps[len(ps) // 2], ps[0], ps[-1]] + ps[1:-1]
            buckets.append((battle, [p for i, p in enumerate(order)
                                     if p is not None]))
    progressed = True
    while len(frames) < budget and progressed:
        progressed = False
        for battle, ps in buckets:
            while ps:
                p = ps.pop(0)
                if id(p) in taken_ids:
                    continue
                taken_ids.add(id(p))
                if take(battle, p):
                    progressed = True
                break
            if len(frames) >= budget:
                break
    return chosen


def build_prompt(chosen: list, anomaly_count: int) -> tuple:
    by_frame: dict = {}
    for p in chosen:
        by_frame.setdefault(p["frame"], []).append(p)
    blocks = []
    for path, ps in by_frame.items():
        claims = "\n".join(
            f"  - [{q['ts_s']} ±{q['tol']:.0f}s / {q['battle']}] {q['claim']}"
            for q in ps)
        blocks.append(f"### フレーム: {path}\n抽出システムの主張:\n{claims}")
    intro = ""
    if anomaly_count:
        intro = (f"\n※ ⚠矛盾候補 が付いた主張は機械検出済みの疑い箇所 "
                 f"({anomaly_count}件)。最優先で確認すること。\n")
    return PROMPT_HEADER + intro + "\n\n" + "\n\n".join(blocks), len(by_frame)


def run(battles: list, budget: int, timeout: int,
        model: str | None = None) -> Path:
    model = model or MODEL
    per_battle = []
    total_anoms = 0
    for b in battles:
        pairs = collect_pairs(b)
        anoms = detect_anomalies(b)
        total_anoms += len(anoms)
        if pairs:
            per_battle.append((Path(b).stem, pairs, anoms))
    if not per_battle:
        raise SystemExit("監査ペアがありません (フレーム未保存の可能性)")
    chosen = select_pairs(per_battle, budget)
    prompt, n_frames = build_prompt(chosen, total_anoms)
    print(f"[audit_session] 対戦{len(per_battle)}件 / 矛盾候補{total_anoms}件 / "
          f"フレーム{n_frames}枚 (予算{budget}) を1回の{model}で監査",
          flush=True)
    t0 = time.time()
    res = subprocess.run(
        ["claude", "-p", prompt, "--model", model,
         "--allowedTools", "Read", "--max-turns", str(n_frames * 2 + 10)],
        capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
    if res.returncode != 0:
        raise SystemExit(f"claude実行失敗: {res.stderr[-500:]}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = "" if model == MODEL else f"_{model.split('-')[1]}"
    out = REPORT_DIR / f"session_{time.strftime('%Y%m%d_%H%M')}{tag}.md"
    header = (f"# セッション一括監査 ({time.strftime('%Y-%m-%d %H:%M')})\n"
              f"- 対戦{len(per_battle)}件 / 機械検出の矛盾候補{total_anoms}件 / "
              f"フレーム{n_frames}枚 / model={model} / "
              f"所要{time.time() - t0:.0f}s\n"
              f"- 対象: {', '.join(b for b, _, _ in per_battle)}\n\n")
    out.write_text(header + res.stdout, encoding="utf-8")
    print(f"[audit_session] レポート: {out}", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="接続テストの一括監査 (sonnet 1回)")
    ap.add_argument("--last", type=int, default=None,
                    help="直近N対戦を対象 (既定: 開始マーカー以降)")
    ap.add_argument("--budget", type=int, default=30,
                    help="フレーム総予算")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--model", type=str, default=None,
                    help="監査モデルの上書き (既定: audit_subtask.MODEL)")
    args = ap.parse_args()
    report = run(session_battles(args.last), args.budget, args.timeout,
                 model=args.model)
    print(report.read_text(encoding="utf-8")[:2500])


if __name__ == "__main__":
    main()
