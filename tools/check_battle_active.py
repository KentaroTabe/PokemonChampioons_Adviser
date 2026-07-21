"""対戦が進行中かどうかを対戦ログの内容から判定する (deploy.sh のガード用)。

    python -m tools.check_battle_active [分]

終了コード 0 = 対戦中 (デプロイ不可) / 1 = 対戦していない (デプロイ可)。

mtime ではなくレコード内容で判定する理由:
対戦の合間もメニュー画面が watch/field に誤分類されてシーンレコードと
空アドバイスが書き込まれ続けるため、mtime は静かにならない (実測)。
実対戦中は events/hp レコードや command/move_select シーンが必ず混ざる。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 対戦中とみなすシグナル
ACTIVE_TYPES = {"events", "hp", "outcome", "manual_fix"}
ACTIVE_SCENES = {"command", "move_select", "selection", "standby", "battle_hud"}


def battle_active(window_min: float = 3.0) -> bool:
    log_dir = Path(__file__).resolve().parent.parent / "logs" / "battles"
    files = sorted(log_dir.glob("*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return False
    cutoff = time.time() - window_min * 60
    # 最新2ファイルを見る (切れ目でファイルが回転した直後に対応)
    for f in files[:2]:
        if f.stat().st_mtime < cutoff:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines[-200:]):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("t", 0) < cutoff:
                break
            typ = d.get("type")
            if typ in ACTIVE_TYPES:
                return True
            if typ == "scene" and d.get("scene") in ACTIVE_SCENES:
                return True
            # advice はシグナルにしない: 対戦の合間もメニューが watch に
            # 誤分類されてアドバイス (ok=True) が生成され続ける (実測)
    return False


if __name__ == "__main__":
    window = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    active = battle_active(window)
    print("active" if active else "idle")
    sys.exit(0 if active else 1)
