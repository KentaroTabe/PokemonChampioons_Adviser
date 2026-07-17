"""
選出画面のタイプアイコンを切り出し、images/type_templates/ に保存するスクリプト。

images/mini_test/select_1のコピー*.PNG の各画像には、
相手パーティの特定の1枠に対するタイプアイコン(1〜2個)が写っている。
このスクリプトは各画像内の正方形アイコン領域を自動検出し、
アルファチャンネル付きでトリミングして type_templates/ に保存する。

保存名は、ユーザーから提供されたタイプ対応表に基づき直接タイプ名で付与する。
"""
import cv2
import numpy as np
from pathlib import Path

SRC_DIR = Path("images/mini_test")
OUT_DIR = Path("images/type_templates")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ユーザー提供のタイプ対応 (画像ファイル名サフィックス -> [タイプ1, タイプ2])
TYPE_MAPPING = {
    "": ["くさ", "あく"],
    "2": ["ほのお", "ひこう"],
    "3": ["ゴースト", "フェアリー"],
    "4": ["ドラゴン", "じめん"],
    "5": ["はがね", "エスパー"],
    "6": ["みず", "フェアリー"],
}


def detect_icon_boxes(img):
    """画像内の明るい正方形領域(アイコン)を検出し、x座標順にソートして返す"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w > 20 and h > 20:
            boxes.append((x, y, w, h))
    boxes.sort(key=lambda b: b[0])

    # 2つのアイコンが1つの輪郭として結合検出された場合、幅で分割する
    result = []
    for (x, y, w, h) in boxes:
        if w > 55:  # 2アイコン分の幅とみなす
            half = w // 2
            result.append((x, y, half, h))
            result.append((x + half, y, w - half, h))
        else:
            result.append((x, y, w, h))
    return result


def extract_icon_with_alpha(img, box, margin=2):
    """
    指定boxの領域を切り出し、背景色(枠の外周から推定)を透過させたアルファ付き画像を返す。
    """
    x, y, w, h = box
    H, W = img.shape[:2]
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(W, x + w + margin)
    y1 = min(H, y + h + margin)
    crop = img[y0:y1, x0:x1]

    # 背景色は選出画面パネルの暗い色。輪郭の四隅から推定。
    ch, cw = crop.shape[:2]
    corner = max(2, min(ch, cw) // 8)
    corners = np.vstack([
        crop[:corner, :corner].reshape(-1, 3),
        crop[:corner, -corner:].reshape(-1, 3),
        crop[-corner:, :corner].reshape(-1, 3),
        crop[-corner:, -corner:].reshape(-1, 3),
    ])
    bg_color = np.median(corners, axis=0)

    dist = np.linalg.norm(crop.astype(np.float32) - bg_color, axis=2)
    fg_mask = (dist > 30).astype(np.uint8) * 255

    # ノイズ除去
    kernel = np.ones((3, 3), np.uint8)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

    coords = cv2.findNonZero(fg_mask)
    if coords is not None:
        bx, by, bw, bh = cv2.boundingRect(coords)
        crop = crop[by:by + bh, bx:bx + bw]
        fg_mask = fg_mask[by:by + bh, bx:bx + bw]

    bgra = cv2.cvtColor(crop, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = fg_mask
    return bgra


def main():
    for suffix, types in TYPE_MAPPING.items():
        img_path = SRC_DIR / f"select_1のコピー{suffix}.PNG"
        if not img_path.exists():
            print(f"[Warning] {img_path} が見つかりません。スキップします。")
            continue

        img = cv2.imread(str(img_path))
        if img is None:
            print(f"[Warning] {img_path} の読み込みに失敗しました。")
            continue

        boxes = detect_icon_boxes(img)
        if len(boxes) != len(types):
            print(f"[Warning] {img_path.name}: 検出数({len(boxes)}) と "
                  f"タイプ数({len(types)}) が一致しません。boxes={boxes}")
            # 可能な範囲だけ処理を続行
        for i, type_name in enumerate(types):
            if i >= len(boxes):
                break
            icon = extract_icon_with_alpha(img, boxes[i])
            out_path = OUT_DIR / f"{type_name}.png"
            if out_path.exists():
                print(f"  [Skip] {out_path} は既に存在します（重複タイプ、同一想定のためスキップ）")
                continue
            cv2.imwrite(str(out_path), icon)
            print(f"  [Saved] {out_path} <- {img_path.name} box={boxes[i]}")


if __name__ == "__main__":
    main()
