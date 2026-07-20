"""シーン分類の一括スイープ。

debug_framesの直近Nフレームを分類し、シーン分布を表示する。
分類器変更時の回帰確認用 (対戦画面がselection化していないか等)。

使い方: python -m tools.check_scene_sweep [N=300]
"""
import glob
import sys

import cv2

from vision import scenes


def main(n=300):
    frames = sorted(glob.glob("debug_frames/frame_*.png"))[-n:]
    dist = {}
    sel_frames = []
    for p in frames:
        img = cv2.imread(p)
        if img is None:
            continue
        sc = scenes.classify(img)["scene"]
        dist[sc] = dist.get(sc, 0) + 1
        if sc == "selection":
            sel_frames.append(p.split("/")[-1])
    for sc, c in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {sc}: {c}")
    print("selectionフレーム:", sel_frames[-8:])


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
