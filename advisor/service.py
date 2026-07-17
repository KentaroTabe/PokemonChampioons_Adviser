"""アドバイザーのエントリポイント。

VisionPipeline の状態辞書を受け取り、行動アドバイス (日本語) を返す。

    from advisor.service import Advisor
    advisor = Advisor()
    advice = advisor.advise(state_dict)
    print(advisor.format_advice(advice))
"""
from __future__ import annotations

from typing import Optional

from advisor.engine import evaluate


class Advisor:
    def __init__(self, resolver=None):
        # resolver は vision.normalize.NameResolver (省略時は遅延生成)
        self._resolver = resolver

    @property
    def resolver(self):
        if self._resolver is None:
            from vision.normalize import NameResolver
            self._resolver = NameResolver()
        return self._resolver

    def advise(self, state_dict: dict) -> dict:
        try:
            return evaluate(state_dict, self.resolver)
        except Exception as e:  # アドバイス失敗で本体を落とさない
            import traceback
            traceback.print_exc()
            return {"ok": False, "reason": f"評価エラー: {e}"}

    def format_advice(self, advice: dict) -> str:
        """人間向けのテキスト整形"""
        if not advice.get("ok"):
            return f"[アドバイス不可] {advice.get('reason')}"
        lines = []
        if advice.get("best"):
            b = advice["best"]
            kind = "技" if b["kind"] == "move" else "交代"
            lines.append(f"◎ 推奨: [{kind}] {b['name']} (スコア {b['score']})")
        for a in advice["actions"][:6]:
            kind = "技" if a["kind"] == "move" else "交代"
            lines.append(f"  - [{kind}] {a['name']}: {a['score']}  {a['reason']}")
        if advice.get("speed_note"):
            lines.append(f"  {advice['speed_note']}")
        if advice.get("mega_note"):
            lines.append(f"  ★ {advice['mega_note']}")
        if advice.get("threats"):
            t = advice["threats"][0]
            mark = "(判明済)" if t.get("revealed") else "(予測)"
            lines.append(f"  ⚠ 最大脅威: {t['move_id']} {mark} "
                         f"被ダメ {t['dmg_min']:.0f}〜{t['dmg_max']:.0f}%")
        return "\n".join(lines)
