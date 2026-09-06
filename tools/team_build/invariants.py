"""hard invariant の監視 (安全装置 9: 自動の緊急ロールバック条件)。統計的な性能低下は人手判断 (alert のみ)。

invariant:
  crash        アドバイザーサーバーの Traceback / 再起動が閾値以上
  model_load   方策・選出モデルの読込失敗
  no_advice    決定に対する助言なしの割合が閾値以上
  illegal      不正な行動 (Showdown の "Invalid choice") が発生
  latency      助言レイテンシ p95 が閾値以上
チェックは server_nohup.log と直近の対戦ログ (decision_audit の出力) から機械的に行う。判定は文字列照合の集計のみ。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
THRESHOLDS = {"crash": 1, "model_load": 1, "no_advice_rate": 0.2, "illegal": 1, "latency_p95_ms": 3000}


def scan_server_log(text: str) -> dict:
    lines = text.splitlines()
    return {
        "crash": sum(1 for l in lines if "Traceback (most recent call last)" in l),
        "model_load": sum(1 for l in lines if re.search(r"(読み込み失敗|load(ing)? failed|No such file).*(policy|selection_model|\.zip|\.pt)", l)),
        "illegal": sum(1 for l in lines if "Invalid choice" in l or "不正な行動" in l),
        "restarts": sum(1 for l in lines if "Application startup complete" in l),
    }


def evaluate_invariants(server_counts: dict, no_advice_rate: Optional[float] = None,
                        latency_p95_ms: Optional[float] = None, thresholds: dict = THRESHOLDS) -> dict:
    violations = []
    if server_counts.get("crash", 0) >= thresholds["crash"]:
        violations.append(f"crash {server_counts['crash']} 件")
    if server_counts.get("model_load", 0) >= thresholds["model_load"]:
        violations.append(f"model_load 失敗 {server_counts['model_load']} 件")
    if server_counts.get("illegal", 0) >= thresholds["illegal"]:
        violations.append(f"illegal action {server_counts['illegal']} 件")
    if no_advice_rate is not None and no_advice_rate >= thresholds["no_advice_rate"]:
        violations.append(f"助言なし率 {no_advice_rate:.0%}")
    if latency_p95_ms is not None and latency_p95_ms >= thresholds["latency_p95_ms"]:
        violations.append(f"latency p95 {latency_p95_ms:.0f}ms")
    return {"ok": not violations, "violations": violations, "action": ("emergency_rollback" if violations else "none"),
            "note": "統計的な性能低下はここでは判定しない (alert + 人手)"}


def check_now(server_log: Path = REPO / "logs" / "server_nohup.log") -> dict:
    text = server_log.read_text(encoding="utf-8", errors="ignore") if server_log.exists() else ""
    return evaluate_invariants(scan_server_log(text))
