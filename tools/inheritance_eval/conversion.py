"""引き継ぎ評価 I-01: 日本語辞書・型登録・名前／単位／形式の変換の契約テスト (docs/INHERITANCE_EVALUATION_PLAN_20261031.md §5 I-01)。

    python -m tools.inheritance_eval.conversion --data-root <本体ツリー>         # 全部 (走査 + 登録 + fixture) を書き出す
    python -m tools.inheritance_eval.conversion --data-root <本体ツリー> --dry-run   # 集計だけ表示 (書き込みなし)

比べるもの:
- 現行 (OLD): vision.normalize.NameResolver / advisor.infer.species_ja_name / advisor.ja_names / advisor.my_team /
  tools.evaluate_team.build_myteam_text / tools.export_my_team_showdown / tools.register_my_team / champions_agent.env の
  team_text 経路。コピーせず、そのまま呼ぶ (書き込み経路は呼ばない。登録は my_team.set_registry_override で差し替え)。
- 最小対応 (M): ローカル Showdown の data/text/ja (pokedex / moves / items / abilities / names) を読み取り専用で解析した表 +
  記号・幅・かなの正規化 (minimal_key) の完全一致だけ。形式の変換は Showdown の Teams.import / export と TeamValidator、
  実数値は Showdown の Battle (対象 mod の statModify)。node で dist を読むだけで、サーバーは起動しない (showdown_ref.js)。
  「M+石」は M に Showdown のメガストーンの定義 (megaStone) から「<種名>ナイト[XYZ]」を足したもの (参考)。

入力: (1) 登録構築の全個体、(2) 対象規制の名前の全走査 (使用率 DB の最新 champions snapshot + Smogon chaos + 図鑑の id)、
(3) S の fixture (tests/fixtures/inheritance/conversion_cases.jsonl)。OCR 誤読への強さは測らない (I-02)。
出力: <out>/cases.jsonl, measurements.jsonl, manifest.json, report.md (共通規約・計画書 §9)。原本には書かない。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent.parent

NAME_CATEGORIES = ("species", "moves", "items", "abilities")
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
_STAT_KEYS = {"h": "hp", "a": "atk", "b": "def", "c": "spa", "d": "spd", "s": "spe",
              "hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe"}
_STAT_LABEL = {"hp": "HP", "atk": "Atk", "def": "Def", "spa": "SpA", "spd": "SpD", "spe": "Spe"}
_JA_TEXT_FILES = {"species": "pokedex.ts", "moves": "moves.ts", "items": "items.ts", "abilities": "abilities.ts"}

# 判定の重さ (measurements の severity)。「正確な入力の誤変換 0 件」は 誤変換 だけを数える
SEV_MISCONVERT = "誤変換"          # 別の id・別の単位の値を返した (黙って)
SEV_AMBIGUITY = "曖昧の黙殺"       # 決まらない入力を 1 つに決めて返した
SEV_MISSING = "欠落"               # 正確な入力を変換できない (不明として返した)
SEV_UNSUPPORTED = "非対応"         # その経路に機能が無い (不明として返した)
SEV_FILL = "補完"                  # 元の型に無い値を足した
SEV_NOTATION = "表記の差"          # 同じ id で文字列だけ違う (合格に数える)
SEV_LABEL = "表示ラベル"           # 値は正しいが単位の名前を取り違えて表示する


# ------------------------------------------------------------------ 純粋な関数
def toid(text) -> str:
    """Showdown の toID と同じ (英数字の小文字だけ)"""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


_KATA_TO_HIRA = {chr(k): chr(k - 0x60) for k in range(0x30A1, 0x30F7)}
_DASHES = re.compile(r"[\-‐‑‒–—―−ｰ]")


def minimal_key(text) -> str:
    """最小対応の正規化: NFKC (幅) → 空白・中黒を除く → ダッシュ類を長音「ー」に → カタカナをひらがなに → 小文字。
    濁点・小書き・長音の有無は変えない (誤読の補正はしない)"""
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = re.sub(r"\s+", "", t)
    t = t.replace("・", "")
    t = _DASHES.sub("ー", t)
    t = "".join(_KATA_TO_HIRA.get(c, c) for c in t)
    return t.lower()


_TS_ENTRY = re.compile(r'^\t"?([a-z0-9]+)"?: \{\s*$')
_TS_FIELD = re.compile(r'^\t\t(name|baseSpecies|forme): (null|"(?:[^"\\]|\\.)*")')


def parse_ja_text_ts(text: str) -> dict:
    """Showdown の data/text/ja/{pokedex,moves,items,abilities}.ts → {id: {"name", "baseSpecies"?, "forme"?}}。
    直下 (タブ 2 つ) の name / baseSpecies / forme だけを読む (世代別の入れ子 gen6: {...} は読まない)。null は None"""
    out: dict = {}
    cur = None
    for line in (text or "").splitlines():
        m = _TS_ENTRY.match(line)
        if m:
            cur = out.setdefault(m.group(1), {})
            continue
        if line.startswith("\t}"):
            cur = None
            continue
        if cur is None:
            continue
        f = _TS_FIELD.match(line)
        if f:
            cur[f.group(1)] = None if f.group(2) == "null" else json.loads(f.group(2))
    return out


def parse_ts_const_map(text: str, const: str) -> dict:
    """names.ts の `export const <const> ... = { key: "値", ... };` → {key: 値} (1 行 1 項目の表だけ)"""
    out: dict = {}
    inside = False
    for line in (text or "").splitlines():
        if not inside:
            if re.match(rf"^export const {re.escape(const)}\b", line):
                inside = True
            continue
        if line.startswith("};"):
            break
        m = re.match(r'^\t(\w+): (null|"(?:[^"\\]|\\.)*"),', line)
        if m:
            out[m.group(1)] = None if m.group(2) == "null" else json.loads(m.group(2))
    return out


def showdown_species_ja(entry: dict) -> Optional[str]:
    """ja/pokedex.ts の 1 項目 → 種の日本語名。name が無ければ、形態の欄 (forme) の無い基本形に限り baseSpecies。
    形態の名前の組み立て (基本種 + forme) はしない (最小対応)"""
    if not entry:
        return None
    if entry.get("name"):
        return entry["name"]
    if "forme" not in entry and entry.get("baseSpecies"):
        return entry["baseSpecies"]
    return None


def stone_names(stones: list, species_ja: dict) -> dict:
    """M+石: Showdown のメガストーン定義 [{"id", "base", "form"}] と種の日本語名 {id: ja} → {石 id: "<種名>ナイト[XYZ]"}。
    接尾辞はメガ後の id の末尾 (megax / megay / megaz)。基本種の日本語名が無ければ作らない"""
    out: dict = {}
    for st in stones or []:
        base_ja = species_ja.get(st.get("base"))
        if not base_ja:
            continue
        form = st.get("form") or ""
        sfx = {"megax": "X", "megay": "Y", "megaz": "Z"}.get(next((s for s in ("megax", "megay", "megaz")
                                                                     if form.endswith(s)), ""), "")
        out.setdefault(st["id"], f"{base_ja}ナイト{sfx}")
    return out


class MinimalTable:
    """最小対応の名前表: {カテゴリ: {id: 日本語名 or None}} → 正規化キーの完全一致で引く。同じキーに複数の id があれば曖昧"""

    def __init__(self, tables: dict):
        self.tables = {cat: dict(t) for cat, t in tables.items()}
        self.index: dict = {}
        for cat, table in self.tables.items():
            idx: dict = {}
            for i, ja in table.items():
                k = minimal_key(ja)
                if k:
                    idx.setdefault(k, set()).add(i)
            self.index[cat] = idx

    def to_id(self, category: str, text) -> dict:
        key = minimal_key(text)
        if not key:
            return {"status": "empty", "id": None, "candidates": []}
        ids = sorted(self.index.get(category, {}).get(key, ()))
        if not ids:
            return {"status": "unknown", "id": None, "candidates": []}
        if len(ids) > 1:
            return {"status": "ambiguous", "id": None, "candidates": ids}
        return {"status": "ok", "id": ids[0], "candidates": ids}

    def to_ja(self, category: str, ident) -> Optional[str]:
        return self.tables.get(category, {}).get(toid(ident))

    def duplicates(self, category: str) -> dict:
        return {k: sorted(v) for k, v in self.index.get(category, {}).items() if len(v) > 1}


def canonical_id(ident, aliases: Optional[dict] = None) -> str:
    """比較用の id: 英数字の小文字にし、別名表 (advisor/data/species_aliases.json の aliases) を図鑑の id にする"""
    i = toid(ident)
    return (aliases or {}).get(i, i)


def classify_name_pair(current_ja: Optional[str], showdown_ja: Optional[str]) -> str:
    """現行の日本語名と Showdown の日本語名の関係: 一致 / 表記の差 (正規化で一致) / 名前の差 / 現行のみ / Showdown のみ / 両方なし"""
    if not current_ja and not showdown_ja:
        return "両方なし"
    if not showdown_ja:
        return "現行のみ"
    if not current_ja:
        return "Showdownのみ"
    if current_ja == showdown_ja:
        return "一致"
    if minimal_key(current_ja) == minimal_key(showdown_ja):
        return "表記の差"
    return "名前の差"


def judge_name(expected: dict, got_id: Optional[str], got_status: str = "ok",
               aliases: Optional[dict] = None) -> tuple:
    """名前 → id の判定 (純粋)。expected.kind: exact / ambiguous / unknown / not_other / probe。
    got_status は "ok" (1 つに決めた) / "ambiguous" (候補を残した) / それ以外 (不明)。戻り値: (result, severity, reason)"""
    kind = expected.get("kind")
    got = canonical_id(got_id, aliases) if (got_id and got_status == "ok") else None
    if kind == "probe":
        return "excluded", None, f"機能の確認だけ (出力 {got_id or got_status})"
    if kind == "exact":
        want = canonical_id(expected["id"], aliases)
        if got == want:
            return "pass", None, "一致"
        if got is None:
            return "fail", SEV_MISSING, f"変換できない ({got_status})"
        return "fail", SEV_MISCONVERT, f"{got_id} を返した (正しくは {expected['id']})"
    if kind == "ambiguous":
        ids = {canonical_id(i, aliases) for i in expected.get("ids") or []}
        if got is None:
            return "pass", None, f"1 つに決めない ({got_status})"
        if got in ids:
            return "fail", SEV_AMBIGUITY, f"候補 {sorted(ids)} から {got_id} に決めた"
        return "fail", SEV_MISCONVERT, f"候補外の {got_id} を返した"
    if kind == "unknown":
        if got is None:
            return "pass", None, f"不明として返した ({got_status})"
        return "fail", SEV_MISCONVERT, f"存在しない名前を {got_id} に置き換えた"
    if kind == "not_other":
        allowed = {canonical_id(i, aliases) for i in expected.get("allowed") or []}
        if got is None or got in allowed:
            return "pass", None, f"別の種へ置き換えない ({got_id or got_status})"
        return "fail", SEV_MISCONVERT, f"別の {got_id} に置き換えた"
    return "fail", None, f"未知の期待値の種類 {kind}"


def nonzero(d: Optional[dict]) -> dict:
    return {k: int(v) for k, v in sorted((d or {}).items()) if v not in (None, 0, "0")}


def points_from_entry_keys(points: Optional[dict]) -> dict:
    """登録の能力ポイント ({h: 2, c: 32} / {hp: 2}) → {hp: 2, spa: 32} (0 は落とす。未知のキーは残さない)"""
    out = {}
    for k, v in (points or {}).items():
        stat = _STAT_KEYS.get(str(k).lower())
        if stat and int(v or 0):
            out[stat] = int(v)
    return dict(sorted(out.items()))


def read_points_minimal(entry: Optional[dict], point_cap: int, point_total: int) -> dict:
    """最小対応の登録の読み方: 「能力ポイント」の欄だけを各 0〜point_cap・合計 point_total 以下の整数として読む。
    単位の決まらない欄 (evs) や範囲外は拒否 (確認待ち)。戻り値: {"status": ok / reject / unknown, "points", "reason"}"""
    if entry is None:
        return {"status": "unknown", "points": None, "reason": "未登録"}
    if "evs" in entry and "能力ポイント" not in entry:
        return {"status": "reject", "points": None, "reason": "evs の欄は単位が決まらない (能力ポイントか努力値か)"}
    raw = entry.get("能力ポイント") or {}
    pts: dict = {}
    for k, v in raw.items():
        stat = _STAT_KEYS.get(str(k).lower())
        if stat is None:
            return {"status": "reject", "points": None, "reason": f"未知のステータスキー {k}"}
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return {"status": "reject", "points": None, "reason": f"整数でない {k}={v!r}"}
        if iv < 0 or iv > point_cap:
            return {"status": "reject", "points": None, "reason": f"{k}={iv} は 0〜{point_cap} の外"}
        if iv:
            pts[stat] = iv
    if sum(pts.values()) > point_total:
        return {"status": "reject", "points": None, "reason": f"合計 {sum(pts.values())} > {point_total}"}
    return {"status": "ok", "points": dict(sorted(pts.items())), "reason": ""}


def points_to_ev(points: dict, point_cap: int, ev_max: int) -> dict:
    """能力ポイント → 努力値相当 (比較用。現行の min(252, 8p) と同じ対応。p=point_cap は ev_max)"""
    per = ev_max // point_cap + (1 if ev_max % point_cap else 0)      # 252 / 32 → 8
    return {k: min(ev_max, int(v) * per) for k, v in sorted(points.items()) if int(v)}


def parse_ev_line_points(line: str) -> dict:
    """Showdown の EVs 行 → {stat: 値} (値の単位は解釈しない)"""
    out = {}
    body = line.split(":", 1)[1] if ":" in line else line
    labels = {v.lower(): k for k, v in _STAT_LABEL.items()}
    for part in body.split("/"):
        m = re.match(r"\s*(\d+)\s+(\w+)", part)
        if m and m.group(2).lower() in labels:
            out[labels[m.group(2).lower()]] = int(m.group(1))
    return out


def judge_values(expected: dict, got: Optional[dict], got_status: str, key: str) -> tuple:
    """数値の対応 (ev / points / stats) の判定 (純粋)。expected.kind: ev / points / stats / ambiguous / reject / unknown"""
    kind = expected.get("kind")
    if kind in ("ambiguous", "reject"):
        if got_status in ("reject", "unknown", "ambiguous"):
            return "pass", None, f"確認待ち・拒否として返した ({got_status})"
        word = "曖昧な単位を 1 つに決めた" if kind == "ambiguous" else "範囲外・単位違いの値を受理した"
        return "fail", (SEV_AMBIGUITY if kind == "ambiguous" else SEV_MISCONVERT), f"{word}: {got}"
    if kind == "unknown":
        if got_status == "unknown" or got is None:
            return "pass", None, "不明として返した"
        return "fail", SEV_MISCONVERT, f"未登録に値を作った: {got}"
    want = nonzero(expected.get(key))
    if got_status != "ok" or got is None:
        return "fail", (SEV_UNSUPPORTED if got_status == "reject" else SEV_MISSING), f"変換できない ({got_status})"
    if nonzero(got) == want:
        return "pass", None, "一致"
    return "fail", SEV_MISCONVERT, f"{nonzero(got)} (正しくは {want})"


def points_problems(problems) -> list:
    """Showdown の検証の問題のうち、能力ポイントの範囲 (1 能力の上限・合計の上限) の違反だけ (0 点の警告は含めない)"""
    return [p for p in (problems or []) if "Stat Points" in p and ("more than" in p or "total" in p)]


def wilson(k: int, n: int, z: float = 1.96) -> Optional[tuple]:
    """二項割合の Wilson 区間 (n=0 なら None)"""
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, center - half), min(1.0, center + half))


def measurement(test_id: str, case_id: str, asset: str, category: str, input_, expected, expected_source: str,
                split: str, result: str, reason: str, elapsed: float, **extra) -> dict:
    row = {"test_id": test_id, "case_id": case_id, "asset": asset, "category": category, "input": input_,
           "expected": expected, "expected_source": expected_source, "split": split, "result": result,
           "reason": reason, "elapsed_sec": round(elapsed, 4)}
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


def load_cases(text: str) -> list:
    """fixture の jsonl → ケースのリスト (空行は飛ばす)"""
    return [json.loads(line) for line in (text or "").splitlines() if line.strip()]


CASE_REQUIRED = ("case_id", "category", "op", "input", "expected", "expected_source", "split")
CASE_OPS = ("ja_to_id", "id_to_ja", "registry_ev", "sim_ev_line", "register_text", "export_human", "export_build",
            "pokemonset_text", "team_text_ja", "evs_ja", "stats", "same_name_both_sides")
CASE_SPLITS = ("dev", "confirm", "diag")


def validate_cases(cases: list, max_cases: int) -> list:
    """fixture の形の検査 (純粋)。問題のリスト (空なら合格)"""
    problems = []
    if len(cases) > max_cases:
        problems.append(f"件数 {len(cases)} > 上限 {max_cases}")
    seen = set()
    for c in cases:
        cid = c.get("case_id")
        for k in CASE_REQUIRED:
            if k not in c:
                problems.append(f"{cid}: {k} が無い")
        if cid in seen:
            problems.append(f"{cid}: case_id の重複")
        seen.add(cid)
        if c.get("op") not in CASE_OPS:
            problems.append(f"{cid}: 未知の op {c.get('op')}")
        if c.get("split") not in CASE_SPLITS:
            problems.append(f"{cid}: 未知の split {c.get('split')}")
        if not str(c.get("expected_source") or "").strip():
            problems.append(f"{cid}: 出典が空")
        if not isinstance(c.get("expected"), dict) or not c["expected"].get("kind"):
            problems.append(f"{cid}: expected.kind が無い")
    return problems


# ------------------------------------------------------------------ 読み込み (副作用: ファイル・DB の読み取り、node)
def sha256_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def git_head(path: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def git_dirty(path: Path) -> Optional[list]:
    try:
        out = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True,
                             timeout=30)
        return [l for l in out.stdout.splitlines() if l.strip()]
    except (OSError, subprocess.SubprocessError):
        return None


def load_showdown_ja(ps_dir: Path) -> dict:
    """data/text/ja の表 → {"species"/"moves"/"items"/"abilities"/"natures"/"types": {id: 日本語名 or None}, "_raw": ...}"""
    base = ps_dir / "data" / "text" / "ja"
    out: dict = {"_files": {}}
    for cat, fname in _JA_TEXT_FILES.items():
        p = base / fname
        raw = parse_ja_text_ts(p.read_text(encoding="utf-8"))
        out["_files"][fname] = sha256_file(p)
        if cat == "species":
            out[cat] = {i: showdown_species_ja(e) for i, e in raw.items()}
            out["_species_raw"] = raw
        else:
            out[cat] = {i: e.get("name") for i, e in raw.items()}
    names = (base / "names.ts").read_text(encoding="utf-8")
    out["_files"]["names.ts"] = sha256_file(base / "names.ts")
    out["natures"] = parse_ts_const_map(names, "NatureNames")
    out["types"] = parse_ts_const_map(names, "TypeNames")
    return out


def run_showdown(js: Path, ps_dir: Path, fmt: str, tasks: list, timeout: float = 600) -> dict:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node が無い")
    proc = subprocess.run([node, str(js), str(ps_dir)], input=json.dumps({"format": fmt, "tasks": tasks}),
                          capture_output=True, text=True, timeout=timeout, cwd=str(ps_dir))
    if proc.returncode != 0:
        raise RuntimeError(f"showdown_ref.js が失敗 (rc={proc.returncode}): {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def latest_champions_snapshot(db: Path, fmt: str) -> Optional[int]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id FROM usage_snapshot WHERE format = ? AND EXISTS (SELECT 1 FROM meta_sets m "
            "WHERE m.snapshot_id = usage_snapshot.id) ORDER BY id DESC LIMIT 1", (fmt,)).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def load_usage_names(db: Path, snapshot_id: int) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = {"species": "SELECT DISTINCT pokemon_name FROM pokemon_usage WHERE snapshot_id = ?",
             "moves": "SELECT DISTINCT move_name FROM move_usage WHERE snapshot_id = ?",
             "items": "SELECT DISTINCT item_name FROM item_usage WHERE snapshot_id = ?",
             "abilities": "SELECT DISTINCT ability_name FROM ability_usage WHERE snapshot_id = ?"}
        out = {cat: sorted({toid(r[0]) for r in conn.execute(sql, (snapshot_id,)) if toid(r[0])})
               for cat, sql in q.items()}
        snap = conn.execute("SELECT source, format, source_month, number_of_battles, fetched_at FROM usage_snapshot "
                            "WHERE id = ?", (snapshot_id,)).fetchone()
        out["_snapshot"] = dict(zip(("source", "format", "source_month", "number_of_battles", "fetched_at"), snap))
        return out
    finally:
        conn.close()


def load_smogon_names(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    species, moves, items, abilities = set(), set(), set(), set()
    for name, row in (d.get("data") or {}).items():
        species.add(toid(name))
        moves |= {toid(k) for k in (row.get("Moves") or {}) if toid(k)}
        items |= {toid(k) for k in (row.get("Items") or {}) if toid(k) and toid(k) != "nothing"}
        abilities |= {toid(k) for k in (row.get("Abilities") or {}) if toid(k)}
    return {"species": sorted(species), "moves": sorted(moves), "items": sorted(items),
            "abilities": sorted(abilities), "_info": d.get("info")}


# ------------------------------------------------------------------ 現行の変換 (そのまま呼ぶ)
class CurrentConverter:
    """現行の名前の変換: 日本語 → id は vision.normalize.NameResolver (既定の cutoff)、id → 日本語は advisor.infer.species_ja_name と
    advisor.ja_names (move_ja / item_ja / ability_ja / nature_ja)"""

    def __init__(self):
        from vision.normalize import NameResolver
        self.resolver = NameResolver()

    def to_id(self, category: str, text) -> dict:
        if category == "species":
            r = self.resolver.resolve_species(text) if text is not None else None
            return {"status": "ok", "id": r[1], "matched": r[0], "score": round(r[2], 3)} if r else \
                {"status": "unknown", "id": None}
        r = self.resolver.resolve(text, category) if text is not None else None
        return {"status": "ok", "id": str(r[1]), "matched": r[0], "score": round(r[2], 3)} if r else \
            {"status": "unknown", "id": None}

    def to_ja(self, category: str, ident) -> Optional[str]:
        from advisor import ja_names
        from advisor.infer import species_ja_name
        i = toid(ident)
        if not i:
            return None
        fn = {"species": species_ja_name, "moves": ja_names.move_ja, "items": ja_names.item_ja,
              "abilities": ja_names.ability_ja, "natures": ja_names.nature_ja}[category]
        got = fn(i)
        # 表に無いと id (や別名の id) をそのまま返すので、英数字だけの結果は「日本語名なし」とみなす
        return got if got and toid(got) != i and not str(got).isascii() else None


@contextlib.contextmanager
def registry_patched(registry: dict, entries: Optional[dict] = None, predictor_items=None):
    """現行の登録の読み取り・書き出しを、ファイルに触れずに差し替える:
    - advisor.my_team の登録 (set_registry_override。書き込み経路は呼ばない)
    - tools.evaluate_team.current_team_entries (対戦ログのロスターを読まない)
    - advisor.sets.get_predictor / tools.evolve_teams._usage_alternatives (使用率 DB を読まない固定の stub。
      predictor_items があれば持ち物の補完候補として返す = 補完の有無を見る)"""
    from advisor import my_team
    import advisor.sets as sets_mod
    import tools.evaluate_team as ev_mod
    import tools.evolve_teams as evo_mod

    class _Pred:
        def predict(self, _sid):
            return {"moves": [], "items": list(predictor_items or []), "abilities": []}

    saved = (ev_mod.current_team_entries, sets_mod.get_predictor, evo_mod._usage_alternatives)
    my_team.set_registry_override(registry)
    ev_mod.current_team_entries = lambda roster=None: {k: dict(v) for k, v in (entries or registry).items()}
    sets_mod.get_predictor = lambda: _Pred()
    evo_mod._usage_alternatives = lambda: {}
    try:
        yield
    finally:
        ev_mod.current_team_entries, sets_mod.get_predictor, evo_mod._usage_alternatives = saved
        my_team.clear_registry_override()


def captured(fn: Callable, *a, **kw) -> tuple:
    """fn の標準出力 (警告) を捕まえて (戻り値, 出力) を返す"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ------------------------------------------------------------------ 本体
class Runner:
    def __init__(self, data_root: Path, out_dir: Path, budget_sec: float):
        from champions_agent import config as cfg
        self.cfg = cfg
        self.data_root = data_root
        self.out_dir = out_dir
        self.budget = budget_sec
        self.t0 = time.monotonic()
        self.test_id = cfg.INH_CONV_TEST_ID
        self.ps_dir = data_root / cfg.INH_CONV_SHOWDOWN_DIR
        self.js = Path(__file__).resolve().parent / "showdown_ref.js"
        self.fmt = cfg.INH_CONV_FORMAT
        self.point_cap, self.point_total = cfg.BUILD_GEN_EV_POINT_CAP, cfg.BUILD_GEN_POINT_BUDGET
        self.ev_max = cfg.INH_CONV_EV_MAX
        self.rows: list = []
        self.cases: list = []
        self.notes: list = []
        self.aliases = json.loads((REPO / "advisor" / "data" / "species_aliases.json").read_text(
            encoding="utf-8")).get("aliases") or {}

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def over_budget(self) -> bool:
        return self.elapsed() > self.budget

    # ---------------------------------------------------------- 準備
    def prepare(self):
        self.sd = load_showdown_ja(self.ps_dir)
        meta = run_showdown(self.js, self.ps_dir, self.fmt, [{"id": "stones", "kind": "mega_stones"}])
        self.format_info = {k: meta.get(k) for k in ("format", "mod", "exists")}
        self.stones = [s for s in meta["results"][0]["stones"] if s.get("nonstandard") is None]
        species_ja = {i: j for i, j in self.sd["species"].items() if j}
        self.stone_ja = stone_names(self.stones, species_ja)
        base = {c: self.sd[c] for c in ("species", "moves", "items", "abilities", "natures", "types")}
        self.minimal = MinimalTable(base)
        with_stones = dict(base)
        items = dict(base["items"])
        for sid, ja in self.stone_ja.items():
            if not items.get(sid):
                items[sid] = ja
        with_stones["items"] = items
        self.minimal_stone = MinimalTable(with_stones)
        self.current = CurrentConverter()
        from vision.normalize import champions_illegal_ids
        self.illegal = set(champions_illegal_ids())
        reg_path = self.data_root / self.cfg.INH_CONV_REGISTRY_FILE
        self.registry = json.loads(reg_path.read_text(encoding="utf-8"))
        self.registry_path = reg_path

    def add(self, **kw):
        self.rows.append(measurement(self.test_id, **kw))

    # ---------------------------------------------------------- Showdown の一括呼び出し
    def showdown_texts(self, texts: dict) -> dict:
        tasks = [{"id": k, "kind": "team_text", "text": t} for k, t in texts.items() if t]
        if not tasks:
            return {}
        res = run_showdown(self.js, self.ps_dir, self.fmt, tasks)
        return {r["id"]: r for r in res["results"]}

    def minimal_export(self, key: str, entry: dict, table: MinimalTable) -> tuple:
        """最小対応の書き出し: 名前は表の完全一致 (決まらなければ書かない)、能力ポイントは欄の値そのまま、性格は表の完全一致。
        戻り値: (本文, {欄: 状態})"""
        status: dict = {}
        sid = entry.get("種族ID")
        if sid:
            status["species"] = {"status": "ok", "id": toid(sid), "via": "種族ID"}
        else:
            status["species"] = table.to_id("species", key)
        sp = status["species"].get("id")
        lines = [sp or "MISSINGNO"]
        if entry.get("持ち物"):
            status["item"] = table.to_id("items", entry["持ち物"])
            if status["item"].get("id"):
                lines[0] += f" @ {status['item']['id']}"
        if entry.get("特性"):
            status["ability"] = table.to_id("abilities", entry["特性"])
            if status["ability"].get("id"):
                lines.append(f"Ability: {status['ability']['id']}")
        lines.append("Level: 50")
        pts = read_points_minimal(entry, self.point_cap, self.point_total)
        status["points"] = pts
        if pts["status"] == "reject":
            return "", status          # 能力ポイントが決まらない型は書き出さない (確認待ち)
        if pts["status"] == "ok" and pts["points"]:
            lines.append("EVs: " + " / ".join(f"{v} {_STAT_LABEL[k]}" for k, v in pts["points"].items()))
        if entry.get("性格"):
            status["nature"] = table.to_id("natures", entry["性格"])
            if status["nature"].get("id"):
                lines.append(f"{status['nature']['id'].capitalize()} Nature")
        mv_status = [table.to_id("moves", m) for m in (entry.get("技") or [])]
        status["moves"] = mv_status
        lines += [f"- {m['id']}" for m in mv_status if m.get("id")]
        return "\n".join(lines), status

    # ---------------------------------------------------------- (3) fixture
    def run_fixture(self):
        cfg = self.cfg
        path = REPO / cfg.INH_CONV_FIXTURE
        cases = load_cases(path.read_text(encoding="utf-8"))
        problems = validate_cases(cases, cfg.INH_CONV_FIXTURE_MAX)
        if problems:
            self.notes.append("fixture の形の問題: " + "; ".join(problems))
        self.fixture_sha = sha256_file(path)
        # 期待値の id が対象 mod に実在するか (fixture の自己検査)
        want: dict = {}
        for c in cases:
            e, inp = c["expected"], c["input"]
            cat = inp.get("category") if c["op"] in ("ja_to_id", "same_name_both_sides") else None
            if cat in NAME_CATEGORIES:
                for i in ([e.get("id")] if e.get("kind") == "exact" else []) + list(e.get("ids") or []):
                    want.setdefault(cat, set()).add(i)
        ex_tasks = [{"id": cat, "kind": "exists", "category": cat, "ids": sorted(v)} for cat, v in want.items()]
        exists = {r["id"]: r["exists"] for r in run_showdown(self.js, self.ps_dir, self.fmt, ex_tasks)["results"]}
        self.fixture_missing_ids = sorted(f"{cat}:{i}" for cat, d in exists.items() for i, v in d.items() if v is None)
        # 書き出し系の本文を先に作って Showdown にまとめて渡す
        export_texts: dict = {}
        prepared: dict = {}
        for c in cases:
            if self.over_budget():
                break
            op, inp = c["op"], c["input"]
            if op in ("export_human", "export_build", "stats"):
                prepared[c["case_id"]] = self._prepare_export(c)
                for path_name, text in prepared[c["case_id"]]["texts"].items():
                    export_texts[f"{c['case_id']}|{path_name}"] = text
            elif op in ("sim_ev_line", "register_text"):
                text = ("Garchomp\n" + inp["line"]) if op == "sim_ev_line" else inp["text"]
                export_texts[f"{c['case_id']}|minimal"] = text
            elif op == "pokemonset_text":
                export_texts[f"{c['case_id']}|minimal"] = f"{inp['species']}\nEVs: " + " / ".join(
                    f"{v} {_STAT_LABEL[k]}" for k, v in zip(STATS, inp["evs"].split("/")) if int(v))
        sd_res = self.showdown_texts(export_texts)
        for c in cases:
            self.cases.append({**c, "asset": "S fixture", "boundary": "入力の文字列・登録の値だけ (対戦の状態は使わない)"})
            if self.over_budget():
                self.add(case_id=c["case_id"], asset="S fixture", category="合成結果", input_=c["input"],
                         expected=c["expected"], expected_source=c["expected_source"], split=c["split"],
                         result="timeout", reason="時間の上限", elapsed=0.0, path="current")
                continue
            for path_name, res in self._run_case(c, prepared.get(c["case_id"]), sd_res):
                result, severity, reason, t, output = res
                self.add(case_id=c["case_id"], asset="S fixture", category="合成結果", input_=c["input"],
                         expected=c["expected"], expected_source=c["expected_source"], split=c["split"],
                         result=result, reason=reason, elapsed=t, path=path_name, severity=severity,
                         case_category=c["category"], op=c["op"], output=output)

    def _prepare_export(self, c: dict) -> dict:
        inp, op = c["input"], c["op"]
        key, entry = inp["key"], inp["entry"]
        out: dict = {"texts": {}, "warn": {}}
        t = time.monotonic()
        if op in ("export_human", "export_build"):
            fill = [("leftovers", 1.0)] if op == "export_build" else None
            with registry_patched({key: entry}, predictor_items=fill):
                if op == "export_human":
                    from tools.export_my_team_showdown import export_team
                    text, warn = captured(export_team)
                else:
                    from tools.evaluate_team import build_myteam_text
                    text, warn = captured(build_myteam_text)
            out["texts"]["current"] = text.strip()
            out["warn"]["current"] = warn.strip()
        mtext, mstatus = self.minimal_export(key, entry, self.minimal_stone)
        out["texts"]["minimal"] = mtext
        out["minimal_status"] = mstatus
        out["elapsed"] = time.monotonic() - t
        return out

    def _sd_set(self, sd_res: dict, rid: str) -> Optional[dict]:
        r = sd_res.get(rid)
        if not r or not r.get("sets"):
            return None
        return r["sets"][0]

    def _judge_export(self, expected: dict, sdset: Optional[dict], text: str) -> tuple:
        """書き出した本文を Showdown で読んだ結果で判定する"""
        if expected.get("kind") == "reject":
            if sdset is None:
                return "pass", None, "書き出さなかった (拒否)"
            probs = points_problems(sdset["problems"])
            if probs:
                return "pass", None, f"Showdown の検証で拒否: {probs[0]}"
            return "fail", SEV_MISCONVERT, f"範囲外の値を黙って書き換えた/通した: {sdset['parsed']['evs']}"
        if sdset is None:
            return "fail", SEV_MISSING, "本文が無い / Showdown で読めない"
        got_pts = nonzero(sdset["parsed"]["evs"])
        want_pts = nonzero(expected.get("points"))
        reasons, sev = [], None
        if got_pts != want_pts:
            reasons.append(f"EVs 欄 {got_pts} (正しくは能力ポイント {want_pts})")
            sev = SEV_MISCONVERT
        stat_probs = points_problems(sdset["problems"])
        if stat_probs:
            reasons.append(f"Showdown の検証: {stat_probs[0]}")
            sev = sev or SEV_MISCONVERT
        if "item" in expected:
            got_item = sdset["parsed"].get("item")
            if (expected["item"] or None) != (got_item or None):
                reasons.append(f"持ち物 {got_item} (登録は {expected['item']})")
                sev = sev or (SEV_FILL if expected["item"] is None else
                              (SEV_MISSING if not got_item else SEV_MISCONVERT))
        if reasons:
            return "fail", sev, "; ".join(reasons)
        return "pass", None, "一致"

    def _run_case(self, c: dict, prep: Optional[dict], sd_res: dict):
        op, inp, exp = c["op"], c["input"], c["expected"]
        out = []
        if op in ("ja_to_id", "same_name_both_sides"):
            cat, text = inp["category"], inp["text"]
            for name, fn in (("current", lambda: self.current.to_id(cat, text)),
                             ("minimal", lambda: self.minimal.to_id(cat, text)),
                             ("minimal+stone", lambda: self.minimal_stone.to_id(cat, text))):
                if name == "minimal+stone" and cat != "items":
                    continue
                t = time.monotonic()
                got = fn()
                if op == "same_name_both_sides":
                    got2 = fn()
                    if got2.get("id") != got.get("id"):
                        out.append((name, ("fail", SEV_MISCONVERT, "同じ名前で結果が変わる", time.monotonic() - t, got)))
                        continue
                r, sev, reason = judge_name(exp, got.get("id"), got.get("status"), self.aliases)
                out.append((name, (r, sev, reason, time.monotonic() - t, got)))
            return out
        if op == "id_to_ja":
            cat, ident = inp["category"], inp["id"]
            for name, fn in (("current", self.current.to_ja), ("minimal", self.minimal.to_ja)):
                t = time.monotonic()
                got = fn(cat, ident)
                if exp["kind"] == "ja":
                    if not got:
                        r = ("fail", SEV_MISSING, "日本語名が無い")
                    elif got == exp["ja"]:
                        r = ("pass", None, "一致")
                    elif minimal_key(got) == minimal_key(exp["ja"]):
                        r = ("pass", SEV_NOTATION, f"表記の差 ({got})")
                    else:
                        r = ("fail", SEV_MISCONVERT, f"{got} (正しくは {exp['ja']})")
                else:      # ja_distinct_from
                    other = fn(cat, exp["other_id"])
                    if not got:
                        r = ("fail", SEV_MISSING, "日本語名が無い")
                    elif other and minimal_key(other) == minimal_key(got):
                        r = ("fail", SEV_AMBIGUITY, f"{exp['other_id']} と同じ名前 {got}")
                    else:
                        r = ("pass", None, f"{got} / {exp['other_id']} は {other}")
                out.append((name, (*r, time.monotonic() - t, got)))
            return out
        if op == "registry_ev":
            entry = inp["entry"]
            t = time.monotonic()
            from advisor import my_team
            with registry_patched({"テスト": entry} if entry is not None else {}):
                b, warn = captured(my_team.get_my_build, "テスト")
            got = None if b is None else b.get("ev")
            status = "unknown" if b is None else "ok"
            key = "ev"
            r = judge_values(exp, got, status, key)
            out.append(("current", (*r, time.monotonic() - t, {"ev": got, "warn": warn.strip() or None})))
            t = time.monotonic()
            mp = read_points_minimal(entry, self.point_cap, self.point_total)
            r = judge_values(exp, mp["points"], mp["status"], "points")
            out.append(("minimal", (*r, time.monotonic() - t, mp)))
            return out
        if op == "sim_ev_line":
            from champions_agent.env.advisor_player import _ev_of_line
            t = time.monotonic()
            got = _ev_of_line(inp["line"])
            r = judge_values(exp, got, "ok", "ev")
            out.append(("current", (*r, time.monotonic() - t, got)))
            out.append(("minimal", self._minimal_points_judge(exp, sd_res, c["case_id"])))
            return out
        if op == "register_text":
            from tools.register_my_team import parse_team_text
            t = time.monotonic()
            parsed = parse_team_text(inp["text"])
            got = points_from_entry_keys(parsed[0]["points"]) if parsed else None
            r = judge_values(exp, got, "ok" if parsed else "unknown", "points")
            out.append(("current", (*r, time.monotonic() - t, got)))
            out.append(("minimal", self._minimal_points_judge(exp, sd_res, c["case_id"])))
            return out
        if op in ("export_human", "export_build"):
            for name in ("current", "minimal"):
                sdset = self._sd_set(sd_res, f"{c['case_id']}|{name}")
                r = self._judge_export(exp, sdset, prep["texts"].get(name, ""))
                output = {"text": prep["texts"].get(name), "warn": prep["warn"].get(name),
                          "showdown": sdset and {"evs": sdset["parsed"]["evs"], "item": sdset["parsed"]["item"],
                                                 "problems": sdset["problems"]}}
                out.append((name, (*r, prep["elapsed"] / 2, output)))
            return out
        if op == "pokemonset_text":
            from champions_agent.env.team_builder import PokemonSet
            t = time.monotonic()
            text = PokemonSet(species=inp["species"], ability=None, item=None, tera_type=None, nature=None,
                              evs=inp["evs"], moves=["earthquake"]).to_showdown_text()
            line = next((l for l in text.splitlines() if l.startswith("EVs:")), "")
            got = parse_ev_line_points(line) if line else {}
            given = {k: int(v) for k, v in zip(STATS, inp["evs"].split("/")) if int(v)}
            if exp["kind"] == "reject":
                r = ("pass", None, "拒否") if not text else \
                    (("fail", SEV_MISCONVERT, f"{given} を {nonzero(got)} に黙って丸めた") if nonzero(got) != given
                     else ("fail", SEV_MISCONVERT, "範囲外の値を通した"))
            else:
                r = judge_values(exp, got, "ok", "points")
            out.append(("current", (*r, time.monotonic() - t, text)))
            out.append(("minimal", self._minimal_points_judge(exp, sd_res, c["case_id"])))
            return out
        if op == "team_text_ja":
            from tools.evaluate_team import team_text_to_ja
            t = time.monotonic()
            got = team_text_to_ja(inp["text"])
            bad = exp["label"] in got
            r = ("fail", SEV_LABEL, f"能力ポイントを「{exp['label']}」と表示") if bad \
                else ("pass", None, "ラベルに含まない")
            out.append(("current", (*r, time.monotonic() - t, got)))
            return out
        if op == "evs_ja":
            from advisor.ja_names import evs_ja
            t = time.monotonic()
            got = evs_ja(inp["evs"])
            r = ("pass", None, "一致") if got == exp["text"] else ("fail", SEV_MISCONVERT, f"{got}")
            out.append(("current", (*r, time.monotonic() - t, got)))
            return out
        if op == "stats":
            key, entry = inp["key"], inp["entry"]
            t = time.monotonic()
            got = self._current_stats(key, entry)
            r = judge_values(exp, got, "ok" if got else "unknown", "stats")
            out.append(("current", (*r, time.monotonic() - t, got)))
            sdset = self._sd_set(sd_res, f"{c['case_id']}|minimal")
            got_sd = sdset and sdset.get("stats")
            r = judge_values(exp, got_sd, "ok" if got_sd else "unknown", "stats")
            out.append(("minimal", (*r, prep["elapsed"], got_sd)))
            return out
        return [("current", ("fail", None, f"未実装の op {op}", 0.0, None))]

    def _minimal_points_judge(self, exp: dict, sd_res: dict, case_id: str) -> tuple:
        t = time.monotonic()
        sdset = self._sd_set(sd_res, f"{case_id}|minimal")
        if sdset is None:
            return ("fail", SEV_MISSING, "Showdown で読めない", time.monotonic() - t, None)
        probs = points_problems(sdset["problems"])
        status = "reject" if probs else "ok"
        pts = nonzero(sdset["parsed"]["evs"])
        key = "points"
        if exp.get("kind") == "ev":
            got = points_to_ev(pts, self.point_cap, self.ev_max) if status == "ok" else None
            key = "ev"
        else:
            got = pts if status == "ok" else None
        r = judge_values(exp, got, status, key)
        return (*r, time.monotonic() - t, {"evs": sdset["parsed"]["evs"], "problems": probs})

    def _current_stats(self, key: str, entry: dict) -> Optional[dict]:
        """現行の実数値: my_team.get_my_build (能力ポイント → 努力値相当、性格の倍率) + advisor.dex の種族値と calc_hp / calc_stat"""
        from advisor import my_team
        from advisor.dex import calc_hp, calc_stat, get_dex
        with registry_patched({key: entry}):
            b, _w = captured(my_team.get_my_build, key)
            sid = my_team.registered_species_id(key) or self.current.to_id("species", key).get("id")
        sp = get_dex().species(sid or "")
        if not b or not sp:
            return None
        bs = sp["baseStats"]
        ev, nat = b.get("ev") or {}, b.get("nature") or {}
        out = {"hp": calc_hp(bs["hp"], ev.get("hp", 0))}
        for s in STATS[1:]:
            out[s] = calc_stat(bs[s], ev.get(s, 0), nat.get(s, 1.0))
        return out

    # ---------------------------------------------------------- (1) 登録構築の全個体
    def expected_registry_ids(self, key: str, entry: dict, stone_expect: dict) -> dict:
        """登録の 1 個体の期待値 (現行の変換を使わずに決める): 種族は 種族ID か Showdown ja の完全一致、持ち物は fixture の
        手で確認したストーン名 → Showdown ja の完全一致 → M+石、特性・性格・技は Showdown ja の完全一致"""
        exp: dict = {}
        src: dict = {}
        if entry.get("種族ID"):
            exp["species"], src["species"] = toid(entry["種族ID"]), "登録の 種族ID"
        else:
            r = self.minimal.to_id("species", key)
            exp["species"], src["species"] = r.get("id"), f"Showdown ja/pokedex.ts の完全一致 ({r['status']})"
        if entry.get("持ち物"):
            k = minimal_key(entry["持ち物"])
            if k in stone_expect:
                exp["item"], src["item"] = stone_expect[k], "fixture の手で確認したストーン名"
            else:
                r = self.minimal.to_id("items", entry["持ち物"])
                if r.get("id"):
                    exp["item"], src["item"] = r["id"], "Showdown ja/items.ts の完全一致"
                else:
                    r = self.minimal_stone.to_id("items", entry["持ち物"])
                    exp["item"], src["item"] = r.get("id"), f"M+石 (Showdown megaStone + 種名 + ナイト) ({r['status']})"
        if entry.get("特性"):
            r = self.minimal.to_id("abilities", entry["特性"])
            exp["ability"], src["ability"] = r.get("id"), f"Showdown ja/abilities.ts ({r['status']})"
        if entry.get("性格"):
            r = self.minimal.to_id("natures", entry["性格"])
            exp["nature"], src["nature"] = r.get("id"), f"Showdown ja/names.ts NatureNames ({r['status']})"
        if entry.get("技"):
            rs = [self.minimal.to_id("moves", m) for m in entry["技"]]
            exp["moves"] = [r.get("id") for r in rs]
            src["moves"] = "Showdown ja/moves.ts の完全一致 (" + ",".join(r["status"] for r in rs) + ")"
        if entry.get("能力ポイント") is not None:
            exp["points"], src["points"] = points_from_entry_keys(entry.get("能力ポイント")), "登録の 能力ポイント"
        return {"ids": exp, "source": src}

    def run_registry(self, stone_expect: dict):
        from tools.register_my_team import parse_team_text, to_entry
        from tools.evaluate_team import build_myteam_text
        from tools.export_my_team_showdown import export_team
        dex = json.loads((REPO / "champions_agent" / "data" / "champions_dex.json").read_text(encoding="utf-8"))
        texts: dict = {}
        per: dict = {}
        for idx, (key, entry) in enumerate(self.registry.items()):
            cid = f"REG-{idx:02d}"
            t = time.monotonic()
            exp = self.expected_registry_ids(key, entry, stone_expect)
            with registry_patched({key: entry}):
                cur_text, warn = captured(build_myteam_text)
                hum_text, warn_h = captured(export_team)
            min_text, min_status = self.minimal_export(key, entry, self.minimal_stone)
            per[cid] = {"key": key, "entry": entry, "exp": exp, "cur_text": cur_text.strip(), "warn": warn.strip(),
                        "hum_text": hum_text.strip(), "warn_h": warn_h.strip(), "min_text": min_text,
                        "min_status": min_status, "t": time.monotonic() - t}
            texts[f"{cid}|current"] = cur_text.strip()
            texts[f"{cid}|human"] = hum_text.strip()
            texts[f"{cid}|minimal"] = min_text
            # 実数値の基準: 期待値の型 (種族・能力ポイント・性格) を Showdown の Battle で計算
            if exp["ids"].get("species") and exp["ids"].get("nature") is not None:
                pts = exp["ids"].get("points") or {}
                ref = [exp["ids"]["species"], "Level: 50"]
                if pts:
                    ref.append("EVs: " + " / ".join(f"{v} {_STAT_LABEL[k]}" for k, v in pts.items()))
                ref.append(f"{exp['ids']['nature'].capitalize()} Nature")
                texts[f"{cid}|ref"] = "\n".join(ref)
        sd = self.showdown_texts(texts)
        resolver = self.current.resolver
        for cid, p in per.items():
            key, entry, exp = p["key"], p["entry"], p["exp"]
            self.cases.append({"case_id": cid, "category": "registry", "op": "registry_roundtrip",
                               "input": {"key": key, "entry": entry}, "expected": exp["ids"],
                               "expected_source": exp["source"], "split": "diag", "asset": "登録構築 (config/my_team.json)",
                               "boundary": "登録の値だけ (対戦・画面は使わない)",
                               "note": "登録は開発で広く使ったデータ = 過去データ診断"})
            base = dict(case_id=cid, asset="登録構築 (config/my_team.json)", category="観測事実",
                        input_={"key": key}, expected_source="; ".join(f"{k}: {v}" for k, v in exp["source"].items()),
                        split="diag")
            incomplete = not entry.get("技") or entry.get("能力ポイント") is None
            # --- 現行: build_myteam_text → Showdown → register_my_team で読み戻し
            cur_set = self._sd_set(sd, f"{cid}|current")
            back = None
            if p["cur_text"]:
                parsed = parse_team_text(p["cur_text"])
                if parsed:
                    with registry_patched({key: entry}):
                        bkey, bentry = to_entry(parsed[0], dex, resolver)
                    back = {"key": bkey, "entry": bentry}
            self._registry_fields("current", base, p, cur_set, back, incomplete)
            # --- 現行 (人間対 AI の書き出し): export_my_team_showdown → Showdown の検証
            hum_set = self._sd_set(sd, f"{cid}|human")
            self._registry_export_human(base, p, hum_set, incomplete)
            # --- 最小対応: 表の完全一致 → Showdown → 表の逆引き
            min_set = self._sd_set(sd, f"{cid}|minimal")
            mback = None
            if min_set:
                par = min_set["parsed"]
                tbl = self.minimal_stone
                mback = {"key": tbl.to_ja("species", par["species"]), "entry": {
                    "種族ID": par["species"], "持ち物": tbl.to_ja("items", par["item"]) if par["item"] else None,
                    "特性": tbl.to_ja("abilities", par["ability"]) if par["ability"] else None,
                    "性格": tbl.to_ja("natures", par["nature"]) if par["nature"] else None,
                    "能力ポイント": nonzero(par["evs"]),
                    "技": [tbl.to_ja("moves", m) for m in par["moves"]]}}
            self._registry_fields("minimal", base, p, min_set, mback, incomplete)
            # --- 実数値 (現行の計算 vs Showdown の Battle)
            ref = self._sd_set(sd, f"{cid}|ref")
            t = time.monotonic()
            if ref and ref.get("stats") and not incomplete:
                got = self._current_stats(key, entry)
                r = judge_values({"kind": "stats", "stats": ref["stats"]}, got, "ok" if got else "unknown", "stats")
                self.add(**{**base, "expected": ref["stats"]}, result=r[0], severity=r[1], reason=r[2],
                         elapsed=time.monotonic() - t, path="current", field="stats", output=got)
            else:
                self.add(**{**base, "expected": None}, result="excluded",
                         reason="登録が不完全 (技か能力ポイントが無い) か期待値を作れない", elapsed=0.0, path="current",
                         field="stats")

    def _ids_of_back(self, back: Optional[dict], table_fn) -> dict:
        """読み戻した登録 (日本語) → id (比較用)。table_fn(cat, text) は {"status", "id"} を返す"""
        if not back:
            return {}
        e = back["entry"]
        out = {"species": toid(e.get("種族ID")) if e.get("種族ID") else table_fn("species", back["key"]).get("id")}
        if e.get("持ち物"):
            out["item"] = table_fn("items", e["持ち物"]).get("id")
        if e.get("特性"):
            out["ability"] = table_fn("abilities", e["特性"]).get("id")
        if e.get("性格"):
            out["nature"] = table_fn("natures", e["性格"]).get("id")
        out["moves"] = [table_fn("moves", m).get("id") for m in (e.get("技") or [])]
        out["points"] = points_from_entry_keys(e.get("能力ポイント"))
        return out

    def _registry_fields(self, path: str, base: dict, p: dict, sdset: Optional[dict], back: Optional[dict],
                         incomplete: bool):
        exp = p["exp"]["ids"]
        entry = p["entry"]
        t = time.monotonic()
        text = p["cur_text"] if path == "current" else p["min_text"]
        if incomplete:
            self.add(**{**base, "expected": exp}, result="excluded",
                     reason="登録が不完全 (技か能力ポイントが無い)。現行は使用率で補完する仕様で、元の型の保持の対象外",
                     elapsed=0.0, path=path, field="all", output={"text": text or None, "warn": p["warn"] or None})
            return
        # Showdown が読んだ id (書き出しの正しさ)
        par = (sdset or {}).get("parsed") or {}
        wrote = {"species": par.get("species"), "item": par.get("item"), "ability": par.get("ability"),
                 "nature": par.get("nature"), "moves": par.get("moves") or [], "points": nonzero(par.get("evs"))}
        # 読み戻し (日本語 → id。比較は 2 つの表のどちらでも id が一致すれば同じ値とみなす)
        cur = self.current

        def either(cat, text):
            r = self.minimal_stone.to_id(cat, text)
            if r.get("id"):
                return r
            return cur.to_id(cat, text)
        back_ids = self._ids_of_back(back, either)
        for field in ("species", "item", "ability", "nature", "points", "moves"):
            if field not in exp:
                continue
            want = exp[field]
            if want is None or (field == "moves" and None in want):
                self.add(**{**base, "expected": want}, result="excluded",
                         reason="期待値を Showdown の表から独立に決められない", elapsed=0.0, path=path, field=field,
                         output={"wrote": wrote.get(field), "back": back_ids.get(field)})
                continue
            cw = lambda v: canonical_id(v, self.aliases) if isinstance(v, str) else v
            w_ok = (cw(wrote.get(field)) == cw(want)) if field not in ("moves", "points") else \
                ([cw(m) for m in wrote["moves"]] == [cw(m) for m in want] if field == "moves" else wrote["points"] == want)
            b = back_ids.get(field)
            b_ok = (cw(b) == cw(want)) if field not in ("moves", "points") else \
                ([cw(m) for m in (b or [])] == [cw(m) for m in want] if field == "moves" else b == want)
            if w_ok and b_ok:
                res, sev, reason = "pass", None, "書き出し・読み戻しとも一致"
                orig = entry.get({"item": "持ち物", "ability": "特性", "nature": "性格"}.get(field, ""), None)
                back_ja = (back or {}).get("entry", {}).get({"item": "持ち物", "ability": "特性",
                                                            "nature": "性格"}.get(field, ""), None)
                if field == "species" and back and not back["key"]:
                    res, sev = "fail", (SEV_UNSUPPORTED if path == "minimal" else SEV_MISSING)
                    reason = f"id は一致するが登録キー {p['key']} を復元できない"
                elif field == "species" and back and back["key"] != p["key"]:
                    res, sev, reason = "pass", SEV_NOTATION, f"登録キー {p['key']} → {back['key']}"
                elif orig and back_ja and orig != back_ja:
                    sev, reason = SEV_NOTATION, f"同じ id で表記が変わる: {orig} → {back_ja}"
                elif field == "moves" and back and back["entry"].get("技") != entry.get("技"):
                    sev, reason = SEV_NOTATION, f"同じ id で表記が変わる: {entry.get('技')} → {back['entry'].get('技')}"
            else:
                res = "fail"
                parts = []
                if not w_ok:
                    parts.append(f"書き出し {wrote.get(field)}")
                if not b_ok:
                    parts.append(f"読み戻し {b}")
                lost = (wrote.get(field) in (None, [], {}) if not w_ok else b in (None, [], {}))
                sev = SEV_MISSING if lost else SEV_MISCONVERT
                if path == "minimal" and lost:
                    sev = SEV_UNSUPPORTED
                reason = "; ".join(parts) + f" (正しくは {want})"
            self.add(**{**base, "expected": want}, result=res, severity=sev, reason=reason,
                     elapsed=(time.monotonic() - t) + p["t"] / 12, path=path, field=field,
                     output={"wrote": wrote.get(field), "back": b})
        probs = list(sdset.get("problems") or []) if sdset else ["Showdown で読めない"]
        legal = points_problems(probs) + [x for x in probs if "Showdown で読めない" in x]
        self.add(**{**base, "expected": "Showdown の検証で能力ポイントの問題なし"},
                 result="pass" if not legal else "fail", severity=None if not legal else SEV_MISCONVERT,
                 reason="問題なし" if not probs else "; ".join(probs[:3]), elapsed=0.0, path=path,
                 field="showdown_validate", output={"problems": probs})

    def _registry_export_human(self, base: dict, p: dict, sdset: Optional[dict], incomplete: bool):
        if incomplete or not p["hum_text"]:
            self.add(**{**base, "expected": p["exp"]["ids"].get("points")}, result="excluded",
                     reason="登録が不完全 (技が 4 つ無い等で書き出されない)", elapsed=0.0, path="current-human",
                     field="points", output={"text": p["hum_text"] or None, "warn": p["warn_h"] or None})
            return
        want = p["exp"]["ids"].get("points") or {}
        got = nonzero(((sdset or {}).get("parsed") or {}).get("evs"))
        probs = points_problems((sdset or {}).get("problems") or [])
        ok = got == want and not probs
        self.add(**{**base, "expected": want}, result="pass" if ok else "fail",
                 severity=None if ok else SEV_MISCONVERT,
                 reason="一致" if ok else f"EVs 欄 {got} (正しくは能力ポイント {want}); {'; '.join(probs[:2])}",
                 elapsed=0.0, path="current-human", field="points", output={"evs": got, "problems": probs})

    # ---------------------------------------------------------- (2) 全走査
    def run_scan(self):
        cfg = self.cfg
        db = self.data_root / "champions_agent" / "data" / "db" / "champions.sqlite3"
        self.snapshot_id = latest_champions_snapshot(db, cfg.USAGE_TARGET_FORMAT)
        usage = load_usage_names(db, self.snapshot_id) if self.snapshot_id else {}
        smogon_path = self.data_root / cfg.INH_CONV_SMOGON_FILE
        smogon = load_smogon_names(smogon_path)
        from advisor.dex import get_dex
        dex = get_dex()
        dex_ids = {"species": sorted(dex.species_ids()), "moves": sorted(dex._moves.keys())}
        self.scan_sources = {
            "usage_db": {"path": str(db), "sha256": sha256_file(db), "snapshot_id": self.snapshot_id,
                         "snapshot": usage.get("_snapshot"),
                         "counts": {c: len(usage.get(c) or []) for c in NAME_CATEGORIES}},
            "smogon": {"path": str(smogon_path), "sha256": sha256_file(smogon_path), "info": smogon.get("_info"),
                       "counts": {c: len(smogon.get(c) or []) for c in NAME_CATEGORIES}},
            "dex": {"path": "advisor/data/dex.json", "sha256": sha256_file(REPO / "advisor" / "data" / "dex.json"),
                    "counts": {c: len(v) for c, v in dex_ids.items()}},
        }
        populations: dict = {}
        for cat in NAME_CATEGORIES:
            ids = sorted(set(usage.get(cat) or []) | set(smogon.get(cat) or []))
            populations[("regmc", cat)] = (ids, {i: [s for s, src in (("db", usage), ("smogon", smogon))
                                                     if i in set(src.get(cat) or [])] for i in ids})
        for cat in ("species", "moves"):
            regmc = set(populations[("regmc", cat)][0])
            ids = [i for i in dex_ids[cat] if i not in regmc]
            populations[("dex", cat)] = (ids, {i: ["dex"] for i in ids})
        ex_tasks = [{"id": f"{scope}|{cat}", "kind": "exists", "category": cat, "ids": ids}
                    for (scope, cat), (ids, _src) in populations.items()]
        exists = {r["id"]: r["exists"] for r in run_showdown(self.js, self.ps_dir, self.fmt, ex_tasks)["results"]}
        for (scope, cat), (ids, srcmap) in populations.items():
            cid = f"SCAN-{scope}-{cat}"
            self.cases.append({"case_id": cid, "category": "scan", "op": "id_roundtrip",
                               "input": {"scope": scope, "category": cat, "n": len(ids)},
                               "expected": "id → 現行の日本語 → 現行の id が元の id (別名表で正規化) に戻る。"
                                           "Showdown ja と名前を突き合わせる",
                               "expected_source": "Showdown data/text/ja (読み取り専用) + 対象 mod の実在 (Dex.forFormat)",
                               "split": "confirm" if scope == "regmc" else "diag",
                               "asset": "名前の表 (vision/data/jp_names.json + 組み立て)",
                               "boundary": "名前の id だけ"})
            for i in ids:
                if self.over_budget():
                    self.add(case_id=f"{cid}:{i}", asset="名前の表", category="規則参照", input_={"id": i},
                             expected=None, expected_source="", split="confirm" if scope == "regmc" else "diag",
                             result="timeout", reason="時間の上限", elapsed=0.0, scope=scope, cat=cat)
                    continue
                self.add(**self._scan_one(cid, scope, cat, i, srcmap.get(i), (exists.get(f"{scope}|{cat}") or {}).get(i)))

    def _scan_one(self, cid, scope, cat, ident, sources, sd_exists) -> dict:
        t = time.monotonic()
        cur_ja = self.current.to_ja(cat, ident)
        back = self.current.to_id(cat, cur_ja) if cur_ja else {"status": "unknown", "id": None}
        sd_ja = self.minimal.to_ja(cat, ident)
        sd_ja_stone = self.minimal_stone.to_ja(cat, ident) if cat == "items" else sd_ja
        min_back = self.minimal.to_id(cat, sd_ja) if sd_ja else {"status": "unknown", "id": None}
        mins_back = self.minimal_stone.to_id(cat, sd_ja_stone) if sd_ja_stone else {"status": "unknown", "id": None}
        want = canonical_id(ident, self.aliases)
        illegal = cat == "species" and ident in self.illegal
        if not cur_ja:
            res, sev, reason = "fail", SEV_MISSING, "現行の日本語名が無い"
        elif not back.get("id") and illegal:
            res, sev, reason = "excluded", None, f"champions 非参戦 (現行は {cur_ja} を解決候補から外す仕様)"
        elif not back.get("id"):
            res, sev, reason = "fail", SEV_MISSING, f"現行の名前 {cur_ja} から id に戻らない"
        elif canonical_id(back["id"], self.aliases) != want:
            res, sev, reason = "fail", SEV_MISCONVERT, (f"{cur_ja} → {back['id']} (元は {ident}"
                                                        + ("、非参戦の名前を別種へ置換)" if illegal else ")"))
        else:
            res, sev, reason = "pass", None, "往復一致"
        detail = {"current_ja": cur_ja, "current_back": back.get("id"), "current_score": back.get("score"),
                  "showdown_ja": sd_ja, "showdown_ja_stone": sd_ja_stone if cat == "items" else None,
                  "name_class": classify_name_pair(cur_ja, sd_ja),
                  "name_class_stone": classify_name_pair(cur_ja, sd_ja_stone) if cat == "items" else None,
                  "minimal_back": min_back.get("status") if min_back.get("status") != "ok" else
                  ("ok" if canonical_id(min_back["id"], self.aliases) == want else f"other:{min_back['id']}"),
                  "minimal_stone_back": (mins_back.get("status") if mins_back.get("status") != "ok" else
                                         ("ok" if canonical_id(mins_back["id"], self.aliases) == want
                                          else f"other:{mins_back['id']}")) if cat == "items" else None,
                  "sources": sources, "in_mod": None if sd_exists is None else True, "illegal": illegal or None}
        if sd_exists is None:
            detail["in_mod"] = False
        return dict(case_id=f"{cid}:{ident}", asset="名前の表", category="規則参照", input_={"id": ident, "category": cat},
                    expected={"id": want}, expected_source="元の id (使用率 DB / Smogon / 図鑑)",
                    split="confirm" if scope == "regmc" else "diag", result=res, reason=reason,
                    elapsed=time.monotonic() - t, severity=sev, scope=scope, cat=cat, detail=detail)

    # ---------------------------------------------------------- 辞書の重複
    def dictionary_stats(self) -> dict:
        from vision.normalize import JP_NAMES_PATH, normalize
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        out: dict = {"jp_names_sha256": sha256_file(JP_NAMES_PATH), "categories": {}}
        for cat, table in raw.items():
            by_id: dict = {}
            by_key: dict = {}
            for ja, v in table.items():
                i = v["id"] if isinstance(v, dict) else v
                by_id.setdefault(i, []).append(ja)
                by_key.setdefault(normalize(ja), set()).add(i)
            out["categories"][cat] = {
                "entries": len(table), "ids": len(by_id),
                "ids_with_multiple_ja": {i: js for i, js in by_id.items() if len(js) > 1},
                "normalize_key_collisions": {k: sorted(v) for k, v in by_key.items() if len(v) > 1},
            }
        out["showdown_duplicates"] = {cat: self.minimal.duplicates(cat) for cat in
                                      ("species", "moves", "items", "abilities", "natures")}
        out["showdown_null_names"] = {cat: sum(1 for v in self.sd[cat].values() if not v) for cat in
                                      ("species", "moves", "items", "abilities")}
        out["showdown_entries"] = {cat: len(self.sd[cat]) for cat in ("species", "moves", "items", "abilities")}
        return out

    # ---------------------------------------------------------- 書き出し
    def stone_expectations(self) -> dict:
        """fixture の持ち物の exact ケース (手で確認したストーン名) → {正規化キー: id}"""
        path = REPO / self.cfg.INH_CONV_FIXTURE
        out = {}
        for c in load_cases(path.read_text(encoding="utf-8")):
            e, inp = c["expected"], c["input"]
            if c["op"] == "ja_to_id" and inp.get("category") == "items" and e.get("kind") == "exact":
                out[minimal_key(inp["text"])] = e["id"]
        return out

    def manifest(self, wall: float) -> dict:
        cfg = self.cfg
        return {
            "test_id": self.test_id, "run_id": cfg.INH_RUN_ID,
            "code": {"base_commit": "768bae44", "worktree_head": git_head(REPO), "worktree_dirty": git_dirty(REPO),
                     "conversion_py_sha256": sha256_file(Path(__file__)),
                     "showdown_ref_js_sha256": sha256_file(self.js)},
            "rules": {"format": self.fmt, "showdown": self.format_info, "showdown_commit": git_head(self.ps_dir),
                      "showdown_ja_files_sha256": self.sd.get("_files"), "node": _node_version(),
                      "point_cap": self.point_cap, "point_total": self.point_total, "ev_max": self.ev_max},
            "data": {"registry": {"path": str(self.registry_path), "sha256": sha256_file(self.registry_path),
                                  "entries": len(self.registry)},
                     "jp_names_sha256": sha256_file(REPO / "vision" / "data" / "jp_names.json"),
                     "article_aliases_sha256": sha256_file(REPO / "vision" / "data" / "article_aliases.json"),
                     "species_aliases_sha256": sha256_file(REPO / "advisor" / "data" / "species_aliases.json"),
                     "fixture": {"path": cfg.INH_CONV_FIXTURE, "sha256": getattr(self, "fixture_sha", None)},
                     "scan": getattr(self, "scan_sources", None)},
            "conditions": {"current": "現行の関数をそのまま呼ぶ (NameResolver の cutoff は既定)。登録は set_registry_override で "
                                      "差し替え、使用率の予測は固定の stub (DB を読まない)",
                           "minimal": "Showdown data/text/ja + minimal_key の完全一致 / Teams.import・export / TeamValidator / "
                                      "Battle (node、サーバーなし)",
                           "minimal+stone": "minimal + Showdown megaStone の <種名>ナイト[XYZ]"},
            "seed": None, "seed_note": "乱数を使わない (決定的)",
            "time_budget_sec": self.budget, "wall_sec": round(wall, 2),
            "notes": self.notes,
        }


def _node_version() -> Optional[str]:
    node = shutil.which("node")
    if not node:
        return None
    try:
        return subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------ 集計 (純粋)
def summarize(rows: list) -> dict:
    """measurements → 集計 (純粋)"""
    out: dict = {"by_asset_path": {}, "misconvert": [], "ambiguity": [], "scan": {}, "fixture_by_category": {}}
    for r in rows:
        key = f"{r['asset']}|{r.get('path') or '-'}"
        d = out["by_asset_path"].setdefault(key, {"pass": 0, "fail": 0, "excluded": 0, "timeout": 0,
                                                  "unexecuted": 0, "severity": {}})
        d[r["result"]] = d.get(r["result"], 0) + 1
        if r.get("severity") and r["result"] == "fail":
            d["severity"][r["severity"]] = d["severity"].get(r["severity"], 0) + 1
        if r.get("severity") == SEV_NOTATION and r["result"] == "pass":
            d["notation"] = d.get("notation", 0) + 1
        if r.get("severity") == SEV_MISCONVERT and r["result"] == "fail":
            out["misconvert"].append(r)
        if r.get("severity") == SEV_AMBIGUITY:
            out["ambiguity"].append(r)
        if r["asset"] == "名前の表":
            s = out["scan"].setdefault(f"{r.get('scope')}|{r.get('cat')}", {
                "n": 0, "pass": 0, "fail_missing": 0, "fail_misconvert": 0, "timeout": 0, "excluded": 0, "name_class": {},
                "name_class_stone": {}, "minimal_back": {}, "minimal_stone_back": {}, "not_in_mod": 0})
            s["n"] += 1
            if r["result"] == "pass":
                s["pass"] += 1
            elif r["result"] == "timeout":
                s["timeout"] += 1
            elif r["result"] == "excluded":
                s["excluded"] += 1
            elif r.get("severity") == SEV_MISCONVERT:
                s["fail_misconvert"] += 1
            else:
                s["fail_missing"] += 1
            det = r.get("detail") or {}
            for k in ("name_class", "name_class_stone", "minimal_back", "minimal_stone_back"):
                v = det.get(k)
                if v is not None:
                    v = v.split(":")[0]
                    s[k][v] = s[k].get(v, 0) + 1
            if det.get("in_mod") is False:
                s["not_in_mod"] += 1
        if r["asset"] == "S fixture":
            k = f"{r.get('case_category')}|{r.get('path')}|{r['split']}"
            d = out["fixture_by_category"].setdefault(k, {"pass": 0, "fail": 0, "excluded": 0, "timeout": 0})
            d[r["result"]] = d.get(r["result"], 0) + 1
    return out


def _fmt_ci(k: int, n: int) -> str:
    ci = wilson(k, n)
    return f"{k}/{n}" + (f" ({ci[0] * 100:.1f}〜{ci[1] * 100:.1f}%)" if ci else "")


def render_report(summary: dict, rows: list, dict_stats: dict, manifest: dict) -> str:
    """集計 → report.md の自動部分 (数値の表と不一致の一覧)。判定の節は別に書く"""
    L = []
    L.append("# I-01 日本語辞書・型登録・名前／単位／形式の変換 (段階 A、phaseA_20261031)")
    L.append("")
    L.append(f"- 生成: tools/inheritance_eval/conversion.py (計算時間 {manifest['wall_sec']} 秒 / 上限 {manifest['time_budget_sec']} 秒)")
    L.append(f"- 対象規制: {manifest['rules']['format']} (Showdown {manifest['rules']['showdown_commit'] and manifest['rules']['showdown_commit'][:9]}、"
             f"mod {manifest['rules']['showdown'].get('mod')})")
    L.append("- 区間は Wilson 95% (件数が少ないので目安。走査は全数なので区間を付けない)")
    L.append("")
    L.append("## 1. 経路ごとの結果")
    L.append("")
    L.append("| 資産 | 経路 | pass (うち表記の差) | fail | excluded | timeout | fail の内訳 |")
    L.append("|---|---|---|---|---|---|---|")
    for key, d in sorted(summary["by_asset_path"].items()):
        asset, path = key.split("|")
        sev = ", ".join(f"{k} {v}" for k, v in sorted(d["severity"].items()))
        L.append(f"| {asset} | {path} | {d['pass']} ({d.get('notation', 0)}) | {d['fail']} | {d['excluded']} | "
                 f"{d['timeout']} | {sev} |")
    L.append("")
    L.append("## 2. 全走査 (id → 日本語 → id の往復と Showdown ja との突き合わせ)")
    L.append("")
    L.append("| 範囲 | 種類 | n | 往復一致 | 欠落 | 誤変換 | 除外 (非参戦) | 名前の関係 (現行 vs Showdown ja) | M の往復 | M+石 の往復 | mod に無い id |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for key, s in sorted(summary["scan"].items()):
        scope, cat = key.split("|")
        nc = ", ".join(f"{k} {v}" for k, v in sorted(s["name_class"].items(), key=lambda x: -x[1]))
        mb = ", ".join(f"{k} {v}" for k, v in sorted(s["minimal_back"].items(), key=lambda x: -x[1]))
        msb = ", ".join(f"{k} {v}" for k, v in sorted(s["minimal_stone_back"].items(), key=lambda x: -x[1])) or "-"
        L.append(f"| {scope} | {cat} | {s['n']} | {s['pass']} ({s['pass'] / max(1, s['n']) * 100:.1f}%) | "
                 f"{s['fail_missing']} | {s['fail_misconvert']} | {s['excluded']} | {nc} | {mb} | {msb} | {s['not_in_mod']} |")
    L.append("")
    L.append("## 3. 誤変換 (severity = 誤変換 の fail) の一覧")
    L.append("")
    L.append("| 資産 | case | 経路 | 欄 | 理由 | split |")
    L.append("|---|---|---|---|---|---|")
    for r in summary["misconvert"]:
        L.append(f"| {r['asset']} | {r['case_id']} | {r.get('path') or '-'} | {r.get('field') or r.get('cat') or '-'} | "
                 f"{str(r['reason']).replace('|', '/')[:160]} | {r['split']} |")
    L.append("")
    L.append("## 4. 曖昧の黙殺 (決まらない入力を 1 つに決めた)")
    L.append("")
    for r in summary["ambiguity"]:
        L.append(f"- {r['case_id']} ({r.get('path')}): {r['reason']}")
    L.append("")
    L.append("## 5. fixture のカテゴリ別 (経路 × split)")
    L.append("")
    L.append("| カテゴリ | 経路 | split | pass / (pass+fail) | excluded |")
    L.append("|---|---|---|---|---|")
    for key, d in sorted(summary["fixture_by_category"].items()):
        cat, path, split = key.split("|")
        L.append(f"| {cat} | {path} | {split} | {_fmt_ci(d['pass'], d['pass'] + d['fail'])} | {d['excluded']} |")
    L.append("")
    L.append("## 6. 辞書の重複・欠落")
    L.append("")
    for cat, d in dict_stats["categories"].items():
        L.append(f"- jp_names.json {cat}: 項目 {d['entries']}、id {d['ids']}、複数の表記を持つ id "
                 f"{len(d['ids_with_multiple_ja'])}、正規化キーの衝突 (別 id) {len(d['normalize_key_collisions'])}"
                 + (f" 例 {list(d['normalize_key_collisions'].items())[:3]}" if d["normalize_key_collisions"] else ""))
    for cat, d in dict_stats["showdown_duplicates"].items():
        L.append(f"- Showdown ja {cat}: 項目 {dict_stats['showdown_entries'].get(cat, '-')}、名前なし (null) "
                 f"{dict_stats['showdown_null_names'].get(cat, '-')}、同じ正規化キーの複数 id {len(d)}"
                 + (f" 例 {list(d.items())[:3]}" if d else ""))
    L.append("")
    return "\n".join(L)


def scan_name_diffs(rows: list, limit: int = 400) -> list:
    """走査の「名前の差」(現行と Showdown ja が正規化しても違う) の一覧 (対象規制の範囲)"""
    out = []
    for r in rows:
        det = r.get("detail") or {}
        if r["asset"] == "名前の表" and r.get("scope") == "regmc" and det.get("name_class") == "名前の差":
            out.append((r.get("cat"), r["input"]["id"], det.get("current_ja"), det.get("showdown_ja")))
    return out[:limit]


def main(argv=None) -> int:
    from champions_agent import config as cfg
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-root", type=Path, default=REPO,
                    help="登録・使用率 DB・Smogon・pokemon-showdown のあるツリー (既定: このリポジトリ)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="出力先 (既定: <data-root>/logs/inheritance_eval/<run_id>/I-01)")
    ap.add_argument("--dry-run", action="store_true", help="集計を表示するだけで書き出さない")
    args = ap.parse_args(argv)
    out_dir = args.out_dir or (args.data_root / cfg.INH_OUT_DIR / cfg.INH_RUN_ID / cfg.INH_CONV_TEST_ID)
    run = Runner(args.data_root.resolve(), out_dir, cfg.INH_TIME_BUDGET_SEC)
    run.prepare()
    print(f"[I-01] 準備 {run.elapsed():.1f} 秒", flush=True)
    run.run_fixture()
    print(f"[I-01] fixture {run.elapsed():.1f} 秒", flush=True)
    run.run_registry(run.stone_expectations())
    print(f"[I-01] 登録 {run.elapsed():.1f} 秒", flush=True)
    run.run_scan()
    print(f"[I-01] 走査 {run.elapsed():.1f} 秒", flush=True)
    dstats = run.dictionary_stats()
    summary = summarize(run.rows)
    wall = run.elapsed()
    manifest = run.manifest(wall)
    manifest["fixture_missing_ids_in_mod"] = run.fixture_missing_ids
    manifest["counts"] = {"cases": len(run.cases), "measurements": len(run.rows)}
    report = render_report(summary, run.rows, dstats, manifest)
    diffs = scan_name_diffs(run.rows)
    report += "\n## 7. 名前の差 (対象規制、現行 vs Showdown ja、正規化しても違うもの)\n\n| 種類 | id | 現行 | Showdown ja |\n|---|---|---|---|\n"
    report += ("\n".join(f"| {c} | {i} | {a} | {b} |" for c, i, a, b in diffs) if diffs
               else "| (該当なし) | | | |") + "\n"
    if args.dry_run:
        print(report)
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "cases.jsonl", "w", encoding="utf-8") as f:
        for c in run.cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(out_dir / "measurements.jsonl", "w", encoding="utf-8") as f:
        for r in run.rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    manifest["dictionary"] = dstats
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + "\n",
                                           encoding="utf-8")
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"[I-01] 書き出し: {out_dir} (cases {len(run.cases)}, measurements {len(run.rows)}, {wall:.1f} 秒)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
