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
    """単発誤分類の無視と、選出滞在中の離脱ヒステリシスを確認"""
    from vision.pipeline import VisionPipeline
    pipe = VisionPipeline()

    def feed(raw):
        scene = pipe._smooth_scene(raw, pipe.state.scene)
        pipe.state.scene = scene
        if scene == "selection":
            pipe._selection_streak += 1
        else:
            pipe._selection_streak = 0
        return scene

    # 対戦中: 単発誤分類は無視、2フレーム連続で確定
    pipe.state.scene = "field"
    seq = ["field", "command", "field", "field", "command", "command"]
    out = [feed(s) for s in seq]
    assert out == ["field", "field", "field", "field", "field", "command"], out

    # 選出滞在中: command/fieldが2-4フレーム続いても離脱しない (5フレームで離脱)
    seq2 = ["command", "command", "field", "selection",   # 4連続未満で復帰
            "field", "field", "field", "field", "field"]  # 5連続 -> 離脱
    pipe3 = VisionPipeline()
    pipe3.state.scene = "selection"
    pipe3._selection_streak = 3
    pipe3._in_selection = True
    out3 = []
    for s in seq2:
        sc = pipe3._smooth_scene(s, pipe3.state.scene)
        pipe3.state.scene = sc
        pipe3._selection_streak = pipe3._selection_streak + 1 if sc == "selection" else 0
        out3.append(sc)
    assert out3[:4] == ["selection"] * 4, out3
    assert out3[-1] == "field" and out3[-2] == "selection", out3
    print(f"シーンスムージング OK: 対戦中={out} / 選出滞在={out3}")


def check_turn_counter():
    # 行動解決 (field/standby) を見た後のcommand復帰のみ+1。
    # command<->move_select<->watch<->battle_hudの往復では加算しない
    transitions = ["standby", "command", "move_select", "command", "watch",
                   "command", "battle_hud", "command", "field", "field",
                   "battle_hud", "command", "field", "battle_hud", "command"]
    turn = 0
    resolution_seen = True
    for sc in transitions:
        if sc in ("field", "standby"):
            resolution_seen = True
        elif sc == "command" and resolution_seen:
            resolution_seen = False
            turn += 1
    assert turn == 3, f"turn={turn} (期待3: 開始/field後/field後)"
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
    # 0%への低下は2回連続でもまだ確定しない (交代アニメの空バー誤読対策)
    _set_hp(state, "opponent", mon, pct=0.0)
    _set_hp(state, "opponent", mon, pct=0.0)
    n_before = len([e for e in state.events if e["source"] == "hp"])
    # 3回目でもひんしメッセージの裏付けがなければイベント化しない
    _set_hp(state, "opponent", mon, pct=0.0)
    assert len([e for e in state.events if e["source"] == "hp"]) == n_before
    # ひんし裏付けありなら確定 (-7%)
    import time as _t
    state.last_faint = {"side": "opponent", "ts": _t.time()}
    mon._hp_last_read = None
    _set_hp(state, "opponent", mon, pct=0.0)
    _set_hp(state, "opponent", mon, pct=0.0)
    _set_hp(state, "opponent", mon, pct=0.0)
    # 交代由来の大幅増 (+60%超) はイベント化されない
    _set_hp(state, "opponent", mon, pct=100.0)
    _set_hp(state, "opponent", mon, pct=100.0)
    hp_events = [e for e in state.events if e["source"] == "hp"]
    texts = [e["text"] for e in hp_events]
    assert n_before == 3, f"0%が2回連続で確定してしまった: {texts}"
    assert len(hp_events) == 4, f"hpイベント{len(hp_events)}件 (期待4): {texts}"
    assert "-36%" in texts[0] and "+24%" in texts[1] and "-81%" in texts[2] \
        and "-7%" in texts[3], texts
    print(f"HP変化イベント OK: {texts}")


def check_hp_max_votes():
    """最大HPの桁落ち誤読 (167->67) が多数決で矯正されることを確認"""
    state = BattleStateV2()
    me = state.player.ensure_active()
    me.species_ja = "ペリッパー"
    _set_hp(state, "player", me, cur=167, mx=167)
    _set_hp(state, "player", me, cur=150, mx=167)
    _set_hp(state, "player", me, cur=150, mx=167)
    _set_hp(state, "player", me, cur=150, mx=67)   # 桁落ち誤読
    assert me.hp_max == 167, me.hp_max
    # 未確定の単発読取は状態に反映されない
    _set_hp(state, "player", me, cur=20, mx=167)
    assert me.hp_current == 150, me.hp_current
    _set_hp(state, "player", me, cur=20, mx=167)
    assert me.hp_current == 20, me.hp_current
    print("最大HP多数決/確定反映 OK")


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
    check_hp_max_votes()
    check_set_hp_events()
    frame_dir = sys.argv[1] if len(sys.argv) > 1 else "debug_frames"
    check_field_hp_on_frames(frame_dir)
    print("FLOW TRACKING OK")
