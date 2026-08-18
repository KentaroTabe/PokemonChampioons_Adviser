"""対戦フェーズ抽出監査のサブタスク実行 (claude headless / sonnet)。

対戦ログ×デバッグフレームの監査ペアを組み立て、claude CLIのヘッドレス
モード (-p) でフレーム画像を目視させ、抽出状態との乖離レポートを生成する。

    python -m tools.audit_subtask                  # 最新対戦を監査
    python -m tools.audit_subtask --battle <log>   # 対戦ログ指定
    python -m tools.audit_subtask --max-frames 10  # フレーム数上限

生成物: logs/audit_reports/<battle名>.md

モデルは opus 固定 (2026-08-18 の同一フレーム30枚での実測比較による):
  - haiku:  技名を別の実在技に取り違え (すてゼリフ→ステルスロック,
            ふいうち→おいうち)、非表示HUDのHP幻視、メッセージ見落とし、
            種族名の文字誤り多数 (ハラバリー→バタバリー等) — 監査には不適
            (2026-07-23 評価)
  - sonnet: 幻覚なしで主要な乖離 (HP誤帰属・ひんしHP固着・シーン誤判定)
            は検出できるが、「相手active=Noneが7フレーム続く」
            「自分activeがひんし個体を指す」等の系統的な状態欠落を見逃した
  - opus:   sonnetの検出を全て包含し、上記2クラス+アイコン誤同定を追加検出。
            フレーム間の相互参照 (「19秒前のフレームでは169/169」) と
            確度の較正も明確に上。セッション1回の監査なのでコスト増は許容
"""
from __future__ import annotations

import argparse
import glob
import subprocess
import time
from pathlib import Path

from tools.audit_extraction import collect_pairs

REPO = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO / "logs" / "audit_reports"
MODEL = "claude-opus-5"

PROMPT_HEADER = """\
あなたはポケモンチャンピオンズ(Switch対戦ゲーム)の画面認識システムの監査員です。
以下に「フレーム画像のパス」と「そのフレーム付近でシステムが抽出した状態の主張」の
ペアが並んでいます。各フレームをReadツールで開いて目視し、主張と実際の画面を
突き合わせて乖離を報告してください。

判定ルール:
- HUDのポケモン名・HP数値/%・メッセージ文・技名/PPは画面から正確に読み取ること
- HUDやメッセージが画面に無い項目は「確認不可」とする。画面に見えないものを
  推測で報告しない (幻覚厳禁)
- フィールド上の見た目だけからの種族同定は自信がある場合のみ。不確実なら
  「低確度」と明記する
- フレームと抽出時刻は最大±6秒ずれる。演出の途中経過による見かけの不一致は
  「時刻ズレの可能性」と分類し、乖離と断定しない
- 主張が画面から確認できて一致 → OK / 明確に矛盾 → 乖離 / 判断材料なし → 確認不可

出力フォーマット (これ以外の文章は不要):
## 乖離一覧
- [時刻] フレーム名: 主張「...」/ 画面の実際「...」/ 深刻度(高・中・低)
(乖離が無ければ「なし」)

## 確認不可
- [時刻] フレーム名: 理由

## 総評
一致した項目の概要と、抽出システムの改善に繋がる所見を3行以内で。
"""


def build_prompt(pairs: list, max_frames: int) -> tuple[str, int]:
    """フレーム単位でペアをまとめてプロンプトを組み立てる"""
    by_frame: dict[str, list] = {}
    for p in pairs:
        by_frame.setdefault(p["frame"], []).append(p)
    frames = list(by_frame.items())[:max_frames]
    blocks = []
    for path, ps in frames:
        claims = "\n".join(
            f"  - [{q['ts_s']} ±{q['tol']:.0f}s] {q['claim']}" for q in ps)
        blocks.append(f"### フレーム: {path}\n抽出システムの主張:\n{claims}")
    return PROMPT_HEADER + "\n\n" + "\n\n".join(blocks), len(frames)


def run(battle_log: str, max_frames: int, timeout: int) -> Path:
    pairs = collect_pairs(battle_log)
    if not pairs:
        raise SystemExit(f"監査ペアなし: {battle_log} (フレーム保持期間切れの可能性)")
    prompt, n = build_prompt(pairs, max_frames)
    print(f"[audit_subtask] {Path(battle_log).name}: "
          f"{len(pairs)}ペア → フレーム{n}枚を監査 (model={MODEL})", flush=True)
    t0 = time.time()
    res = subprocess.run(
        ["claude", "-p", prompt, "--model", MODEL,
         "--allowedTools", "Read", "--max-turns", str(max_frames * 2 + 10)],
        capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
    if res.returncode != 0:
        raise SystemExit(f"claude実行失敗: {res.stderr[-500:]}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / (Path(battle_log).stem + ".md")
    header = (f"# 抽出監査レポート: {Path(battle_log).name}\n"
              f"- 実行: {time.strftime('%Y-%m-%d %H:%M:%S')} / model={MODEL}"
              f" / フレーム{n}枚 / 所要{time.time() - t0:.0f}s\n\n")
    out.write_text(header + res.stdout, encoding="utf-8")
    print(f"[audit_subtask] レポート: {out}", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="抽出監査サブタスク (sonnet)")
    ap.add_argument("--battle", default=None, help="対戦ログ (既定: 最新)")
    ap.add_argument("--max-frames", type=int, default=20,
                    help="監査するフレーム数の上限 (コスト抑制)")
    ap.add_argument("--timeout", type=int, default=1200)
    args = ap.parse_args()
    battle = args.battle or sorted(
        glob.glob(str(REPO / "logs" / "battles" / "*.jsonl")))[-1]
    report = run(battle, args.max_frames, args.timeout)
    print(report.read_text(encoding="utf-8")[:2000])


if __name__ == "__main__":
    main()
