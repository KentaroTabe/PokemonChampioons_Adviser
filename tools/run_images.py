"""サンプル画像でパイプラインを実行し、抽出結果を確認するツール。

    python -m tools.run_images <画像またはディレクトリ> [--json]

各画像を単発モード (single_shot) で処理し、シーン・抽出結果・イベントを表示する。
状態は画像をまたいで累積される (動画の代わりに連番静止画で検証する用途)。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2

from vision.pipeline import VisionPipeline


def summarize(state: dict) -> str:
    lines = []
    f = state["field"]
    lines.append(f"  field: weather={f['weather']} terrain={f['terrain']} "
                 f"TR={f['trick_room']} scene={state['scene']} cmd={state['command_no']}")
    for side in ("player", "opponent"):
        s = state[side]
        hz = s["hazards"]
        hz_txt = []
        if hz["stealth_rock"]:
            hz_txt.append("SR")
        if hz["spikes"]:
            hz_txt.append(f"spikes{hz['spikes']}")
        if hz["toxic_spikes"]:
            hz_txt.append(f"tspikes{hz['toxic_spikes']}")
        if hz["sticky_web"]:
            hz_txt.append("web")
        lines.append(f"  [{side}] active={s['active_index']} hazards={'+'.join(hz_txt) or '-'} "
                     f"tailwind={s['tailwind']}")
        for i, p in enumerate(s["party"]):
            if not (p["species_ja"] or p["display_name"] or p["types"]):
                continue
            mark = "*" if i == s["active_index"] else " "
            hp = ""
            if p["hp_current"] is not None:
                hp = f" HP={p['hp_current']}/{p['hp_max']}"
            elif p["hp_percent"] is not None:
                hp = f" HP={p['hp_percent']}%"
            moves = ""
            if p["moves"]:
                moves = " moves=" + ",".join(
                    f"{m['name_ja']}({m['pp']}/{m['max_pp']}{':' + m['effectiveness'] if m['effectiveness'] else ''})"
                    for m in p["moves"])
            if p["revealed_moves"]:
                moves += " revealed=" + ",".join(p["revealed_moves"])
            extra = []
            if p["status"]:
                extra.append(p["status"])
            if p["is_mega"]:
                extra.append("MEGA")
            boosts = {k: v for k, v in p["boosts"].items() if v}
            if boosts:
                extra.append(str(boosts))
            if p["ability_ja"]:
                extra.append(f"ab:{p['ability_ja']}")
            if p["item_ja"]:
                extra.append(f"item:{p['item_ja']}")
            lines.append(f"   {mark}[{i}] {p['species_ja'] or p['display_name']} "
                         f"types={p['types']}{hp}{moves} {' '.join(extra)}")
    return "\n".join(lines)


def main():
    target = Path(sys.argv[1])
    as_json = "--json" in sys.argv

    pipe = VisionPipeline()
    files = [target]
    if target.is_dir():
        files = sorted(p for p in target.iterdir()
                       if p.suffix.lower() in (".png", ".jpg", ".jpeg"))

    for p in files:
        img = cv2.imread(str(p))
        if img is None:
            continue
        t0 = time.time()
        state, fired = pipe.process(img, single_shot=True)
        dt = time.time() - t0
        print(f"\n=== {p.name} ({dt:.2f}s) scene={state['scene']} events={fired}")
        if as_json:
            print(json.dumps(state, ensure_ascii=False, indent=1))
        else:
            print(summarize(state))
            for ev in state["events"][-3:]:
                print(f"  log: [{ev['source']}] {ev['text'][:50]} -> {ev['event']}")


if __name__ == "__main__":
    main()
