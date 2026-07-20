"""タイプアイコン照合のスコア診断。

選出画面フレームの相手パーティ各スロットについて、タイプ候補の上位スコアを
表示する (でんき/かくとう等の混同ペアのマージン確認用)。

使い方: python -m tools.check_type_scores <frame.png> [...]
"""
import sys

import cv2
import numpy as np

from vision import zones
from vision.typeicons import (_core, _dhash, _load_templates, _mean_lab,
                              classify_type_icon)
from vision.zones import crop


def icon_scores(icon):
    core = _core(icon)
    gray_std = float(cv2.cvtColor(core, cv2.COLOR_BGR2GRAY).std())
    qhash, qlab = _dhash(core), _mean_lab(core)
    scored = []
    for name, entries in _load_templates().items():
        best_h = min(int(np.count_nonzero(qhash != e["hash"])) for e in entries)
        best_c = min(float(np.linalg.norm(qlab - e["lab"])) for e in entries)
        scored.append((best_h / 256.0 + 0.7 * best_c / 100.0, best_h, best_c, name))
    scored.sort()
    return gray_std, scored


def main(paths):
    for path in paths:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: 読み込み失敗")
            continue
        print(f"=== {path.split('/')[-1]} ===")
        for i, sz in enumerate(zones.SELECTION_OPP):
            for slot_key in ("type1", "type2"):
                zone = sz.get(slot_key)
                if zone is None:
                    continue
                icon = crop(img, zone)
                if icon is None or icon.size == 0:
                    continue
                result = classify_type_icon(icon)
                if result is None:
                    continue
                std, scored = icon_scores(icon)
                top = "  ".join(f"{n}(h={h},c={c:.0f},t={t:.2f})"
                                for t, h, c, n in scored[:3])
                print(f"  slot{i} {slot_key}: 判定={result} std={std:.0f}  {top}")


if __name__ == "__main__":
    main(sys.argv[1:])
