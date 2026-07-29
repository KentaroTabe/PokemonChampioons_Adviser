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

    def advise_selection(self, state_dict: dict) -> dict:
        """選出画面用: 選出進捗の判定と最適選出の提案"""
        from advisor.selection import (advise_selection, attach_model_pick,
                                       format_selection_advice)
        try:
            result = advise_selection(state_dict, self.resolver)
            # 学習済み選出モデルの推しを併記する (ヒューリスティクスの
            # 説明は残す。実戦比較では モデル0.51 > 相性0.29 > 乱択0.25)
            attach_model_pick(result,
                              state_dict.get("player", {}).get("party", []),
                              state_dict.get("opponent", {}).get("party", []))
        except Exception as e:
            import traceback
            traceback.print_exc()
            result = {"ok": False, "reason": f"選出評価エラー: {e}"}
        result["kind"] = "selection"
        result["text"] = format_selection_advice(result)
        return result

    def advise(self, state_dict: dict) -> dict:
        try:
            result = evaluate(state_dict, self.resolver)
        except Exception as e:  # アドバイス失敗で本体を落とさない
            import traceback
            traceback.print_exc()
            result = {"ok": False, "reason": f"評価エラー: {e}"}
        suggestion = self.suggest_check(state_dict)
        if suggestion:
            result["suggestion"] = suggestion
        return result

    @staticmethod
    def suggest_check(state: dict) -> str:
        """不足している情報に応じて、ユーザーに開いてほしい画面を提案する。

        スマホ版にも「場の状況」確認画面があるため、ランク変化などが起きた後は
        表示してもらうことで確定情報を取り込める (画面が表示されれば
        フレームとして解析対象になる)。
        """
        try:
            player = state.get("player", {})
            opp = state.get("opponent", {})
            idx = player.get("active_index")
            me = player["party"][idx] if idx is not None and idx < len(player.get("party", [])) else {}
            oidx = opp.get("active_index")
            om = opp["party"][oidx] if oidx is not None and oidx < len(opp.get("party", [])) else {}

            # 1. 自分の技が未取得 -> 技選択画面
            if not me.get("moves"):
                return "技選択画面を一度開いてください (技とPPを読み取ります)"

            # 1.5 自分の型 (能力ポイント/性格) が未登録 -> 登録を依頼
            # (画面からは読み取れないため、登録がなければ攻撃系252振りを仮定)
            from advisor.my_team import has_build
            if me.get("species_ja") and not has_build(me.get("species_ja")):
                return (f"{me['species_ja']}の型が未登録です。config/my_team.json に"
                        "能力ポイント・性格を書くと計算精度が上がります "
                        "(現在は攻撃系252振り仮定)")

            # 2. 直近でランク変化イベントがある -> 場の状況画面
            recent = state.get("events", [])[-12:]
            has_boost = any((e.get("event") or "").startswith("boost_") for e in recent)
            any_boosts = any(v for v in (me.get("boosts") or {}).values()) or \
                         any(v for v in (om.get("boosts") or {}).values())
            if has_boost or any_boosts:
                return ("「場の状況」確認画面を開くとランク補正・場の効果を確定できます "
                        "(数秒表示すれば読み取ります)")

            # 3. 相手のHPが不明 -> 様子を見る画面
            if om and om.get("hp_percent") is None:
                return "「様子を見る」を開くと相手パーティのHP%を取得できます"
        except Exception:
            pass
        return ""

    def format_advice(self, advice: dict) -> str:
        """人間向けのテキスト整形"""
        if not advice.get("ok"):
            text = f"[アドバイス不可] {advice.get('reason')}"
            if advice.get("suggestion"):
                text += f"\n  📱 {advice['suggestion']}"
            return text
        lines = []
        if advice.get("best"):
            b = advice["best"]
            kind = "技" if b["kind"] == "move" else "交代"
            lines.append(f"◎ 推奨: [{kind}] {b['name']} (スコア {b['score']})")
        for a in advice["actions"][:6]:
            kind = "技" if a["kind"] == "move" else "交代"
            lines.append(f"  - [{kind}] {a['name']}: {a['score']}  {a['reason']}")
        if advice.get("opp_inference"):
            lines.append(f"  🔍 {advice['opp_inference']}")
        if advice.get("opp_moves_note"):
            lines.append(f"  📋 {advice['opp_moves_note']}")
        if advice.get("opp_spread_note"):
            lines.append(f"  🧬 {advice['opp_spread_note']}")
        gt = advice.get("gtheory")
        if gt and gt.get("summary_lines"):
            lines.append("  🎲 択評価 (期待値/最悪ケース保証値):")
            for sl in gt["summary_lines"]:
                lines.append(f"     {sl}")
        if advice.get("endgame_note"):
            lines.append(f"  🏁 {advice['endgame_note']}")
        rl = advice.get("rl_hint")
        if rl and rl.get("top"):
            picks = " / ".join(f"{t['label']} {t['prob']:.0%}"
                               for t in rl["top"][:3])
            lines.append(f"  🤖 RL方策({rl['style']}): {picks}"
                         f" (局面評価 {rl['value']:+.2f})")
        if advice.get("speed_note"):
            lines.append(f"  {advice['speed_note']}")
        if advice.get("mega_note"):
            lines.append(f"  ★ {advice['mega_note']}")
        if advice.get("threats"):
            t = advice["threats"][0]
            mark = "(判明済)" if t.get("revealed") else "(予測)"
            lines.append(f"  ⚠ 最大脅威: {t['move_id']} {mark} "
                         f"被ダメ {t['dmg_min']:.0f}〜{t['dmg_max']:.0f}%")
        if advice.get("suggestion"):
            lines.append(f"  📱 {advice['suggestion']}")
        return "\n".join(lines)
