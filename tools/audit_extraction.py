"""抽出監査ハーネス: デバッグフレームと抽出状態の時刻対応付け。

Claudeがフレーム画像 (正解) を目視し、同時刻の抽出状態と突き合わせて
乖離を見つけるためのツール。

    python -m tools.audit_extraction                # 最新対戦の監査ペア一覧
    python -m tools.audit_extraction --battle <log> # 対戦ログ指定
    python -m tools.audit_extraction --at <epoch>   # 指定時刻の抽出状態詳細
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRAME_DIR = REPO / "debug_frames"


def _frames() -> list:
    """(epoch, path) 時刻順"""
    out = []
    for p in glob.glob(str(FRAME_DIR / "*.png")):
        try:
            ts = int(Path(p).stem.split("_")[-1])
            out.append((ts, p))
        except ValueError:
            continue
    return sorted(out)


def _nearest_frame(frames: list, ts: float, tol: float = 6.0):
    best = None
    for fts, p in frames:
        d = abs(fts - ts)
        if d <= tol and (best is None or d < best[0]):
            best = (d, fts, p)
    return best


def _summarize_state(st: dict) -> str:
    def side(sd, label):
        rows = []
        for p in (sd.get("party") or []):
            if not (p.get("ja") or p.get("types")):
                continue
            hp = p.get("hp")
            hp_s = f" {hp:.0f}%" if hp is not None else ""
            mega = "[メガ]" if p.get("mega") else ""
            types = "/".join(p.get("types") or [])
            rev = ",".join(p.get("revealed") or [])
            rows.append(f"    {p.get('ja') or '?'}{mega}{hp_s}"
                        f" [{types}]" + (f" 判明技:{rev}" if rev else ""))
        act = sd.get("active")
        return [f"  {label} (active={act}):"] + rows

    lines = []
    lines += side(st.get("player") or {}, "自分")
    lines += side(st.get("opponent") or {}, "相手")
    f = st.get("field") or {}
    fx = [k for k in ("weather", "terrain") if f.get(k)] + \
        (["TR"] if f.get("trick_room") else [])
    if fx:
        lines.append(f"  場: {[f.get('weather'), f.get('terrain')]}")
    return "\n".join(lines)


def collect_pairs(battle_log: str, include_selection: bool = False) -> list:
    """対戦ログとデバッグフレームの監査ペアを構造化して返す。

    返り値: [{"t", "ts_s", "frame", "tol", "claim"}]  (claim = 抽出主張の要約文)
    既定は対戦中の状況理解が主目的のため選出画面レコードはスキップする
    (選出抽出は監査済みでほぼ正確。include_selection=True で含める)
    """
    frames = _frames()
    pairs = []
    for line in open(battle_log):
        d = json.loads(line)
        t = d.get("t", 0)
        typ = d.get("type")
        if typ not in ("scene", "events", "hp"):
            continue
        if not include_selection and typ == "scene" and \
                d.get("scene") in ("selection", "standby"):
            continue
        hit = _nearest_frame(frames, t)
        if hit is None:
            continue
        ts_s = time.strftime("%H:%M:%S", time.localtime(t))
        if typ == "scene":
            claim = f"scene={d.get('scene')}\n" + \
                _summarize_state(d.get("state") or {})
        else:
            claim = f"{typ}: {d.get('fired') or d.get('text')}"
        pairs.append({"t": t, "ts_s": ts_s, "frame": hit[2],
                      "tol": hit[0], "claim": claim})
    return pairs


def audit(battle_log: str, include_selection: bool = False) -> None:
    pairs = collect_pairs(battle_log, include_selection)
    print(f"=== 監査ペア: {battle_log} ===")
    print("各行のフレームをReadで目視し、抽出状態と突き合わせる:\n")
    for p in pairs:
        print(f"--- {p['ts_s']} frame={p['frame']} (±{p['tol']:.0f}s)")
        print(p["claim"])
    print(f"\n監査対象 {len(pairs)}件")


def main() -> None:
    ap = argparse.ArgumentParser(description="抽出監査ハーネス")
    ap.add_argument("--battle", default=None)
    ap.add_argument("--at", type=float, default=None)
    ap.add_argument("--include-selection", action="store_true",
                    help="選出画面のレコードも監査対象に含める")
    args = ap.parse_args()
    if args.at:
        # 指定時刻に最も近い対戦ログレコードを表示
        logs = sorted(glob.glob(str(REPO / "logs" / "battles" / "*.jsonl")))
        best = None
        for lg in logs[-5:]:
            for line in open(lg):
                d = json.loads(line)
                dt = abs(d.get("t", 0) - args.at)
                if best is None or dt < best[0]:
                    best = (dt, d)
        if best:
            print(json.dumps(best[1], ensure_ascii=False, indent=1)[:3000])
        return
    battle = args.battle or sorted(
        glob.glob(str(REPO / "logs" / "battles" / "*.jsonl")))[-1]
    audit(battle, include_selection=args.include_selection)


if __name__ == "__main__":
    main()
