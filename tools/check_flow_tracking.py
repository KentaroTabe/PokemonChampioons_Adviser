"""対戦の流れ追跡 (ターン/技/HP変化) の検証。

1. ターンカウンター: シーン遷移列をパイプラインのロジック通りに流し、
   command往復で二重カウントしないことを確認
2. _set_hp: HP変化イベントの発火条件 (減少/回復/ノイズ無視) を確認
3. extract_field_hp: 実フレーム (debug_frames のフィールドシーン) から
   HPが読めるかを確認

使い方: python -m tools.check_flow_tracking [debug_frames_dir]
"""
import glob
import sys

import cv2

from vision import scenes
from vision.extractors import _set_hp, extract_field_hp
from vision.state import BattleStateV2


def check_scene_smoothing():
    """単発の誤分類フレームが状態のシーンに反映されないことを確認"""
    from vision.pipeline import VisionPipeline
    pipe = VisionPipeline()
    pipe.state.scene = "selection"

    def feed(raw):
        # process()のスムージング部だけを再現 (画像なしテスト)
        prev = pipe.state.scene
        scene = raw
        if scene != pipe._pending_scene:
            pipe._pending_scene = scene
            pipe._pending_count = 1
        else:
            pipe._pending_count += 1
        if scene != prev and pipe._pending_count < 2 and prev not in (None, "unknown"):
            scene = prev
        pipe.state.scene = scene
        return scene

    seq = ["selection", "battle_hud", "selection", "command", "selection",
           "selection", "command", "command", "command"]
    out = [feed(s) for s in seq]
    assert out[:6] == ["selection"] * 6, out
    assert out[6] == "selection" and out[7] == "command", out
    print(f"シーンスムージング OK: {seq} -> {out}")


def check_turn_counter():
    transitions = ["standby", "command", "move_select", "command", "watch",
                   "command", "field", "field", "command", "field",
                   "battle_hud", "command"]
    turn = 0
    prev = "unknown"
    for sc in transitions:
        if sc == "command" and prev not in ("command", "move_select", "watch",
                                            "field_check"):
            turn += 1
        prev = sc
    assert turn == 3, f"turn={turn} (期待3: 開始/field後/battle_hud後)"
    print(f"ターンカウンター OK (遷移{len(transitions)}回 -> {turn}ターン)")


def check_set_hp_events():
    state = BattleStateV2()
    mon = state.opponent.ensure_active()
    mon.species_ja = "テストポケモン"
    _set_hp(state, "opponent", mon, pct=100.0)   # 初回: イベントなし
    _set_hp(state, "opponent", mon, pct=64.0)    # 1回目の観測: 未確定
    _set_hp(state, "opponent", mon, pct=64.0)    # 2回連続 -> -36%発火
    _set_hp(state, "opponent", mon, pct=63.0)    # -1%: ノイズ無視
    _set_hp(state, "opponent", mon, pct=88.0)    # 1回目: 未確定
    _set_hp(state, "opponent", mon, pct=88.0)    # 2回連続 -> +24%発火 (基準64から)
    # 単発誤読のフラップ (7% <-> 0%) はイベント化されない
    for v in (7.0, 0.0, 7.0, 0.0):
        _set_hp(state, "opponent", mon, pct=v)
    # 値が安定したら確定 (-81%)
    _set_hp(state, "opponent", mon, pct=7.0)
    _set_hp(state, "opponent", mon, pct=7.0)
    # 交代由来の大幅増 (+60%超) はイベント化されない
    _set_hp(state, "opponent", mon, pct=100.0)
    _set_hp(state, "opponent", mon, pct=100.0)
    hp_events = [e for e in state.events if e["source"] == "hp"]
    texts = [e["text"] for e in hp_events]
    assert len(hp_events) == 3, f"hpイベント{len(hp_events)}件 (期待3): {texts}"
    assert "-36%" in texts[0] and "+24%" in texts[1] and "-81%" in texts[2], texts
    print(f"HP変化イベント OK: {texts}")


def check_field_hp_on_frames(frame_dir: str, limit: int = 200):
    frames = sorted(glob.glob(f"{frame_dir}/frame_*.png"))[-limit:]
    field_n = read_n = 0
    samples = []
    for path in frames:
        img = cv2.imread(path)
        if img is None or scenes.classify(img)["scene"] != "field":
            continue
        field_n += 1
        state = BattleStateV2()
        state.opponent.ensure_active()
        me = state.player.ensure_active()
        me.hp_max = None
        extract_field_hp(img, state)
        opp = state.opponent.active()
        got = opp.hp_percent is not None or me.hp_percent is not None
        if got:
            read_n += 1
            if len(samples) < 5:
                samples.append((path.split("/")[-1],
                                f"相手{opp.hp_percent}% 自分{me.hp_percent}%"))
    print(f"フィールドフレーム {field_n}枚中 HP読取 {read_n}枚 "
          f"(バナー非表示の演出中フレームは読めなくて正常)")
    for name, s in samples:
        print(f"  {name}: {s}")


if __name__ == "__main__":
    check_scene_smoothing()
    check_turn_counter()
    check_set_hp_events()
    frame_dir = sys.argv[1] if len(sys.argv) > 1 else "debug_frames"
    check_field_hp_on_frames(frame_dir)
    print("FLOW TRACKING OK")
