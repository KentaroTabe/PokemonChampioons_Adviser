"""読み負荷 (重い択) の測定。

定説「読みを減らせる構築が良い」の操作的定義 (docs/READ_BURDEN_DESIGN.md):

    重い択 := その局面の最善手 (recommended基準) の 期待値 − 保証値 が
              しきい値 (既定0.15) を超えるターン
    読み負荷(チーム) := 1戦あたりの重い択の平均回数

期待値と保証値の差は「読みが当たる前提の得」なので、これが大きい局面ほど
相手の応手を読む必要がある。差が小さい局面は何をされても結果が変わらない
(=読み不要)。Phase A では測定のみで、選択圧はかけない。
"""
from __future__ import annotations

HEAVY_GAP = 0.15


def top_gap(actions: list) -> float | None:
    """択評価の行動リストから、最善手の 期待値−保証値 を返す。

    最善手は recommended (期待値と保証値のブレンド = 実際に操縦が使う基準)
    が最大のもの。行動が無ければ None。
    """
    if not actions:
        return None
    best = max(actions, key=lambda a: a.get("recommended", a.get("expected", 0)))
    exp = best.get("expected")
    worst = best.get("worst")
    if exp is None or worst is None:
        return None
    return float(exp) - float(worst)


def attach_burden_meter(player, depth: int = 1,
                        threshold: float = HEAVY_GAP) -> None:
    """poke-envプレイヤーの choose_move をラップし、読み負荷を計測する。

    行動選択自体は変えない (測定のみ)。ターンごとに深さ1の択評価
    (~4ms実測) を回し、player.read_burden に累積する:
        {"turns": 評価できたターン数, "heavy": 重い択の数, "gap_sum": 合計}
    """
    player.read_burden = {"turns": 0, "heavy": 0, "gap_sum": 0.0}
    original = player.choose_move

    def wrapped(battle):
        try:
            # モジュール属性経由で呼ぶ (テストでの差し替えを効かせるため)
            from champions_agent.env import search_expert
            r = search_expert.search_options(battle, depth=depth)
            gap = top_gap((r or {}).get("actions") or [])
            if gap is not None:
                rb = player.read_burden
                rb["turns"] += 1
                rb["gap_sum"] += gap
                if gap > threshold:
                    rb["heavy"] += 1
        except Exception:
            pass
        return original(battle)

    player.choose_move = wrapped


def summarize(player, n_battles: int) -> dict:
    """計測結果を1戦あたりへ正規化する"""
    rb = getattr(player, "read_burden", None) or \
        {"turns": 0, "heavy": 0, "gap_sum": 0.0}
    return {
        "turns": rb["turns"],
        "heavy": rb["heavy"],
        "heavy_per_battle": round(rb["heavy"] / n_battles, 2)
        if n_battles else 0.0,
        "gap_mean": round(rb["gap_sum"] / rb["turns"], 3)
        if rb["turns"] else 0.0,
    }
