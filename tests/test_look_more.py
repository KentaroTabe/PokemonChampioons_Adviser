"""もっと見る画面 (選出/交代) からの自パーティ自動登録の検証。

images/look_more/ の実スクショ12枚 (選出画面6枚 + 交代画面6枚、
能力タブ/ステータスタブ) をパイプラインに通し、config/my_team.json 相当の
一時ファイルへ正しい型 (能力ポイント/性格/持ち物/特性/技) が保存されることを
確認する。

使い方: scripts/run_test.sh test_look_more
"""
import json
import tempfile
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parent.parent
IMG_DIR = REPO / "images" / "look_more"

EXPECTED = {
    "ペリッパー": {
        "性格": "おだやか",
        "能力ポイント": {"h": 32, "b": 2, "d": 32},
        "持ち物": "しめったいわ",
        "特性": "あめふらし",
        "技": ["ぼうふう", "とんぼがえり", "なみのり", "れいとうビーム"],
    },
    "ラグラージ": {
        "性格": "ようき",
        "能力ポイント": {"h": 2, "a": 32, "s": 32},
        "持ち物": "ラグラージナイト",
        "特性": "しめりけ",
        "技": ["じしん", "ウェーブタックル", "れいとうパンチ", "どくづき"],
    },
    "ブリジュラス": {
        "性格": "ずぶとい",
        "能力ポイント": {"h": 32, "b": 20, "c": 2, "d": 11, "s": 1},
        "持ち物": "たべのこし",
        "特性": "じきゅうりょく",
        "技": ["ラスターカノン", "りゅうのはどう", "エレクトロビーム",
               "はどうだん"],
    },
}


def main() -> None:
    if not IMG_DIR.exists():
        print("test_look_more SKIP (images/look_more なし)")
        return

    # my_team.json を一時ファイルへ差し替え (実運用の登録を汚さない)
    from advisor import my_team
    tmp = Path(tempfile.mkdtemp()) / "my_team.json"
    my_team.CONFIG_PATH = tmp
    my_team._CACHE, my_team._CACHE_MTIME = None, -1.0

    from vision.pipeline import VisionPipeline
    pipe = VisionPipeline()
    files = sorted(IMG_DIR.glob("*.PNG")) + sorted(IMG_DIR.glob("*.png"))
    assert files, "スクショがありません"
    scenes = []
    for p in files:
        img = cv2.imread(str(p))
        if img is None:
            continue
        state, _ = pipe.process(img, single_shot=True)
        scenes.append((p.name, state["scene"]))

    for name, scene in scenes:
        assert scene == "watch", f"{name} が watch 以外に分類: {scene}"
    print(f"シーン分類 OK: {len(scenes)}枚すべて watch")

    saved = json.loads(tmp.read_text(encoding="utf-8")) if tmp.exists() else {}
    ok = True
    for sp, exp in EXPECTED.items():
        got = saved.get(sp)
        if got is None:
            print(f"✗ {sp}: 保存されていません")
            ok = False
            continue
        for key, val in exp.items():
            if got.get(key) != val:
                print(f"✗ {sp}.{key}: {got.get(key)} != 期待 {val}")
                ok = False
            else:
                print(f"  {sp}.{key} OK")
    assert ok, f"保存内容が正解と不一致: {json.dumps(saved, ensure_ascii=False)}"

    # 登録済みデータからのShowdownチーム書き出し (human_battle用)
    from tools.export_my_team_showdown import export_team
    text = export_team()
    assert "pelipper @ damprock" in text, text
    assert "Ability: drizzle" in text, text
    assert "EVs: 252 HP / 16 Def / 252 SpD" in text, text
    assert "Calm Nature" in text, text
    assert "- hurricane" in text, text
    assert "swampert @ swampertite" in text, text
    assert "archaludon @ leftovers" in text, text
    assert "Bold Nature" in text, text
    print("Showdownエクスポート OK")
    print("test_look_more ALL OK")


if __name__ == "__main__":
    main()
