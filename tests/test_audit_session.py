"""セッション一括監査 (audit_session) の機械検出とサンプリングの検証。

使い方: scripts/run_test.sh test_audit_session
"""
import json
import tempfile
from pathlib import Path

from tools.audit_session import detect_anomalies, select_pairs


def _write_log(records) -> str:
    path = Path(tempfile.mkdtemp()) / "battle_test.jsonl"
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(path)


def _scene(t, side, ja, hp, status=None):
    mon = {"ja": ja, "hp": hp, "status": status}
    return {"t": t, "type": "scene", "scene": "field",
            "state": {side: {"party": [mon]},
                      ("opponent" if side == "player" else "player"):
                      {"party": []}}}


def test_detect_anomalies():
    log = _write_log([
        _scene(100, "opponent", "リザードン", 100.0),
        _scene(110, "opponent", "リザードン", 0.0, status="fainted"),
        _scene(120, "opponent", "リザードン", 34.0),       # ひんし後の再表示
        _scene(200, "player", "オーロンゲ", 20.0),
        _scene(210, "player", "オーロンゲ", 95.0),         # 交代なしの急回復
        {"t": 250, "type": "events", "fired": ["switch_player"]},
        _scene(255, "player", "ペリッパー", 100.0),        # 交代直後は正常
    ])
    anoms = detect_anomalies(log)
    descs = " / ".join(d for _, d in anoms)
    assert "ひんし後にHP34%" in descs, descs
    assert "急回復" in descs, descs
    assert "ペリッパー" not in descs, descs
    print(f"test_detect_anomalies OK: {len(anoms)}件 ({descs})")


def test_detect_seven_mons():
    mons = [{"ja": f"ポケ{i}", "hp": 100.0, "status": None} for i in range(7)]
    log = _write_log([
        {"t": 1, "type": "scene", "scene": "field",
         "state": {"opponent": {"party": mons}, "player": {"party": []}}},
    ])
    anoms = detect_anomalies(log)
    assert any("7匹" in d for _, d in anoms), anoms
    print("test_detect_seven_mons OK")


def test_select_pairs_budget_and_priority():
    def pair(t, frame, claim):
        return {"t": t, "ts_s": f"{t}", "frame": frame, "tol": 1.0,
                "claim": claim}

    b1_pairs = [pair(100 + i, f"f1_{i}.png", "scene=field\n...")
                for i in range(10)]
    b2_pairs = [pair(200 + i, f"f2_{i}.png", "hp: リザードン 50%")
                for i in range(10)]
    anomalies = [(203, "opponent:リザードン ひんし後にHP34%が再表示")]
    chosen = select_pairs([("battle1", b1_pairs, []),
                           ("battle2", b2_pairs, anomalies)], budget=6)
    frames = {p["frame"] for p in chosen}
    assert len(frames) <= 6, frames
    # 矛盾候補の周辺ペアが⚠付きで含まれる
    flagged = [p for p in chosen if p["claim"].startswith("⚠矛盾候補")]
    assert flagged and flagged[0]["battle"] == "battle2", chosen
    # 両対戦からサンプリングされている
    assert {p["battle"] for p in chosen} == {"battle1", "battle2"}
    print(f"test_select_pairs_budget_and_priority OK "
          f"(フレーム{len(frames)}枚, ⚠{len(flagged)}件)")


def main() -> None:
    test_detect_anomalies()
    test_detect_seven_mons()
    test_select_pairs_budget_and_priority()
    print("ALL OK")


if __name__ == "__main__":
    main()
