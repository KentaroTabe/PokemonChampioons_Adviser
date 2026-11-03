"""採点方式の比較 (tools/usefulness_p1 --compare scoring --team general) の実験専用の構築プールを作って固定する (2026-10-09 M2d、レビュー 2)。

    python -m tools.scoring_pool                              # 最新の使用率スナップショットから合成して logs/usefulness/pools/ に書く
    python -m tools.scoring_pool --db <champions.sqlite3> --showdown-dir <pokemon-showdown> --out <file>

7 月の固定プール (META_PIN / POOL_PIN の上位構築) は回帰確認には使えるが、現行規制 (TRAINING_BATTLE_FORMAT = M-C) での採点方式の
選定には、現行規制で合法性を確かめた資料を実験専用に固定する。既存の共通ピン (META_PIN / POOL_PIN) は読みも書きもしない。

作り方: 使用率 DB の最新スナップショット (meta_sets が揃ったもの) から tools.team_build.opponents.synthetic_pool の仕組みで 6 体の
チームを合成し (種の重み = 使用率 ∪ ゲーム内順位、型 = meta_sets の代表型、技は champions mod の learnset で検査、共起 = teammate_usage)、
各チームを tools.team_build.sets.validate_team_text (Showdown の validate-team、TRAINING_BATTLE_FORMAT) で検査して不合法を除く。
プールファイル: 条件 (snapshot id、フォーマット、生成 seed、要求件数、合成数、除外数と理由)、各チームの本文 (team_id 付き)、
本文全体の sha16 (チームの型まで含む。本文を "\\n\\n\\n" で連結した sha256 の先頭 16 桁 = usefulness_p1 の構築プールの sha16 と同じ計算)。

使用率 DB は読み取り専用 (mode=ro) で開く。check_advisor_player --own-pool FILE がこのファイルを読み、Random(相手 seed + 1) で対戦ごとに引く
(RankedTeambuilder と同じ対応のとり方)。純粋な部分 (pool_doc / pool_sha16 / check_pool_doc / default_out_path) は
tests/test_advice_m2d.py で確かめる。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from champions_agent.config import (DB_PATH, P1_OUT_ROOT, P1_SCORING_POOL_N, P1_SCORING_POOL_SEED, TRAINING_BATTLE_FORMAT)

REPO = Path(__file__).resolve().parent.parent
POOL_SCHEMA = "scoring_pool/1"


# ------------------------------------------------------------------ 純粋
def pool_sha16(texts: Sequence[str]) -> str:
    """プールの本文全体の sha16 (チームの型まで含む。usefulness_p1.ranked_team_facts の pool_sha16 と同じ計算)"""
    from advisor.versions import sha256_text
    return sha256_text("\n\n\n".join(texts))


def unsupported_species(texts: Sequence[str]) -> dict:
    """本文の列のうち、助言の図鑑 (advisor.dex) で引けない種を持つチーム {本文の位置: [種族 id]} (2026-10-09 M2e)。
    種族 id は champions_agent.env.advisor_player.team_text_species の読み方 (1 行目の「@」より前を英数字だけ) で、
    advisor.dex.canonical_species_id の別名 (indeedeef → indeedeefemale 等) で引ければ対応済み。
    引けない種の個体は助言が評価できない (advisor_player.check_reference の ref_unbuilt、usefulness_verdict で正式判定を止める)"""
    from advisor.dex import canonical_species_id, get_dex
    from champions_agent.env.advisor_player import team_text_species
    dex = get_dex()
    out = {}
    for i, t in enumerate(texts):
        bad = [sid for sid in team_text_species(t) if sid and dex.species(canonical_species_id(sid)) is None]
        if bad:
            out[i] = bad
    return out


def pool_doc(texts: Sequence[str], team_ids: Sequence[str], *, snapshot_id: Optional[int], battle_format: str, seed: int,
             n_requested: int, n_generated: int, excluded: Sequence[dict], db_path: Optional[str],
             showdown: Optional[dict] = None, created_at: Optional[str] = None) -> dict:
    """プールファイルの中身 (純粋)。excluded = [{team_id, errors}] (validate-team で不合法だったチーム)"""
    texts = list(texts)
    return {
        "schema": POOL_SCHEMA,
        "purpose": "採点方式の比較 (usefulness_p1 --compare scoring --team general) の実験専用の自チームのプール。"
                   "現行規制で合法性を確かめた合成チーム。共通ピン (META_PIN / POOL_PIN) とは別",
        "conditions": {"snapshot_id": snapshot_id, "battle_format": battle_format, "seed": int(seed),
                       "n_requested": int(n_requested), "n_generated": int(n_generated), "n_excluded": len(excluded),
                       "source": "tools.team_build.opponents.synthetic_pool", "db_path": db_path, "showdown": showdown or {},
                       "created_at": created_at},
        "excluded": [dict(e) for e in excluded],
        "n": len(texts),
        "sha16": pool_sha16(texts),
        "teams": [{"team_id": tid, "text": t} for tid, t in zip(team_ids, texts)],
    }


def check_pool_doc(doc: Optional[dict]) -> list:
    """プールファイルの検査 (純粋)。問題の list (空なら使ってよい): 本文の sha16 が記録と一致・件数が記録と一致・1 件以上・
    全チームの種が助言の図鑑で引ける (unsupported_species。2026-10-09 M2e)"""
    if not doc:
        return ["プールファイルが読めない"]
    errs = []
    texts = [t.get("text") or "" for t in (doc.get("teams") or [])]
    if not texts:
        errs.append("プールが空")
    if doc.get("n") is not None and int(doc["n"]) != len(texts):
        errs.append(f"件数 {len(texts)} が記録 {doc.get('n')} と違う")
    actual = pool_sha16(texts) if texts else None
    if doc.get("sha16") != actual:
        errs.append(f"本文の sha16 {actual} が記録 {doc.get('sha16')} と違う")
    bad = unsupported_species(texts)
    if bad:
        sp = sorted({sid for v in bad.values() for sid in v})
        errs.append(f"助言の図鑑で引けない種を持つチーム {len(bad)} 件 ({', '.join(sp)})")
    return errs


def default_out_path(snapshot_id: Optional[int], now: float, root: Path = REPO / P1_OUT_ROOT) -> Path:
    """既定の出力先 (純粋): logs/usefulness/pools/scoring_pool_<snapshot>_<YYYYMMDD>.json"""
    return Path(root) / "pools" / f"scoring_pool_{snapshot_id}_{time.strftime('%Y%m%d', time.localtime(now))}.json"


def load_pool(path) -> tuple:
    """プールファイル → (本文の列, 中身)。検査 (check_pool_doc) に通らなければ ValueError"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    errs = check_pool_doc(doc)
    if errs:
        raise ValueError(f"{path}: " + " / ".join(errs))
    return [t["text"] for t in doc["teams"]], doc


# ------------------------------------------------------------------ 生成 (副作用: DB の読み取り・node の実行・ファイルの書き込み)
def build_pool(db_path: Path, showdown_dir: Path, n: int, seed: int, snapshot_id: Optional[int], battle_format: str,
               log=print) -> dict:
    from champions_agent.env import legality
    from tools.team_build.learnsets import learnsets
    from tools.team_build.opponents import _meta_tables, synthetic_pool
    from tools.team_build.sets import validate_team_text
    if not Path(db_path).exists():
        raise SystemExit(f"使用率 DB が無い: {db_path}")
    # 技の検査 (fill_moves) は champions mod の learnset。作業ツリーに pokemon-showdown が無くても指定の dir の learnset を使う
    lt = Path(showdown_dir) / "data" / "mods" / "champions" / "learnsets.ts"
    if lt.exists():
        legality._TABLE = learnsets(str(lt))
    else:
        log(f"[pool] 注意: learnset が無い ({lt})。技の検査をせずに合成する (不合法は validate-team で除く)")
    tables = _meta_tables(snapshot_id, db_path=Path(db_path))
    teams, by_id, snap = synthetic_pool(n=n, seed=seed, tables=tables)
    log(f"[pool] snapshot {snap} から {len(teams)} チームを合成 (要求 {n}、seed {seed})")
    texts, ids, excluded = [], [], []
    for i, t in enumerate(teams):
        text = by_id[t.team_id]
        # 助言の図鑑で引けない種 (別名の表 advisor/data/species_aliases.json でも対応できないもの) は勝敗を見ずに先に除く
        # (2026-10-09 M2e。理由は excluded に残す)
        bad = unsupported_species([text]).get(0)
        if bad:
            excluded.append({"team_id": t.team_id, "errors": [f"助言の図鑑で引けない種: {', '.join(bad)}"]})
            continue
        ok, errs = validate_team_text(text, battle_format, showdown_dir=showdown_dir)
        if ok:
            texts.append(text)
            ids.append(t.team_id)
        else:
            excluded.append({"team_id": t.team_id, "errors": list(errs)[:5]})
        if (i + 1) % 20 == 0:
            log(f"[pool] 検査 {i + 1}/{len(teams)} (除外 {len(excluded)})")
    sd = {"dir": str(showdown_dir), "commit": _git_head(showdown_dir)}
    return pool_doc(texts, ids, snapshot_id=snap, battle_format=battle_format, seed=seed, n_requested=n,
                    n_generated=len(teams), excluded=excluded, db_path=str(db_path), showdown=sd,
                    created_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def _git_head(d: Path) -> Optional[str]:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def main(argv: Optional[list] = None) -> int:
    from tools.team_build.sets import SHOWDOWN_DIR
    ap = argparse.ArgumentParser(description="採点方式の比較の実験専用の構築プールを作る")
    ap.add_argument("--db", default=str(DB_PATH), help="使用率 DB (読み取り専用で開く)")
    ap.add_argument("--showdown-dir", default=str(SHOWDOWN_DIR), help="pokemon-showdown (validate-team と learnset)")
    ap.add_argument("--n", type=int, default=P1_SCORING_POOL_N, help="合成するチーム数 (合法性の検査の前)")
    ap.add_argument("--seed", type=int, default=P1_SCORING_POOL_SEED)
    ap.add_argument("--snapshot", type=int, default=None, help="使用率スナップショット (既定: meta_sets が揃った最新)")
    ap.add_argument("--format", default=TRAINING_BATTLE_FORMAT)
    ap.add_argument("--out", default=None, help="出力先 (既定 logs/usefulness/pools/scoring_pool_<snapshot>_<YYYYMMDD>.json)")
    args = ap.parse_args(argv)
    doc = build_pool(Path(args.db), Path(args.showdown_dir), args.n, args.seed, args.snapshot, args.format)
    out = Path(args.out) if args.out else default_out_path(doc["conditions"]["snapshot_id"], time.time())
    if out.exists():
        raise SystemExit(f"出力先が既にある (上書きしない): {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    c = doc["conditions"]
    print(f"[pool] {out}: snapshot {c['snapshot_id']} / {c['battle_format']} / 合成 {c['n_generated']} → 合法 {doc['n']} "
          f"(除外 {c['n_excluded']}) / sha16 {doc['sha16']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
