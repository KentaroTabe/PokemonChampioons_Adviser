"""OCRテキストの正規化と、日本語名(種族/技/特性/持ち物/タイプ)のファジー照合。

OCRは濁点の欠落・小書き文字の混同・長音の欠落などを起こしやすいため、
「正規化キー」同士の類似度で照合する。
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Optional

JP_NAMES_PATH = Path(__file__).resolve().parent / "data" / "jp_names.json"

# カタカナ -> ひらがな
_KATA_TO_HIRA = {chr(k): chr(k - 0x60) for k in range(0x30A1, 0x30F7)}

# 濁点・半濁点の清音化 (ひらがな)
_SEION = str.maketrans(
    "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ",
    "かきくけこさしすせそたちつてとはひふへほはひふへほう",
)

# 小書き文字の通常化
_SMALL = str.maketrans("ぁぃぅぇぉっゃゅょゎ", "あいうえおつやゆよわ")

# OCRが混同しやすい漢字/記号 -> かな (カタカナ語の誤読対策)
_OCR_CONFUSION = str.maketrans({
    "三": "み", "二": "に", "工": "え", "才": "お", "夕": "た", "卜": "と",
    "八": "は", "匕": "ひ", "口": "ろ", "力": "か", "干": "チ", "王": "モ",
    "一": "ー", "|": "ー", "1": "ー",
})


def normalize(text: str) -> str:
    """照合用の正規化: NFKC -> ひらがな化 -> 記号除去。濁点・小書きは保持。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(_KATA_TO_HIRA.get(c, c) for c in t)
    # かな・漢字・英数字・長音のみ残す
    t = re.sub(r"[^ぁ-ん一-龥a-zA-Z0-9ー]", "", t)
    return t.lower()


def loose_key(text: str) -> str:
    """さらに緩い正規化: 清音化 + 小書き通常化 + OCR混同吸収 + 長音除去。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(_OCR_CONFUSION)
    t = "".join(_KATA_TO_HIRA.get(c, c) for c in t)
    t = re.sub(r"[^ぁ-ん一-龥a-zA-Z0-9ー]", "", t).lower()
    t = t.translate(_SEION).translate(_SMALL)
    return t.replace("ー", "")


def similarity(a: str, b: str) -> float:
    """2つの生テキストの類似度 (0..1)。正規化キーと緩いキーの高い方を採用する。"""
    n1 = SequenceMatcher(None, normalize(a), normalize(b)).ratio()
    n2 = SequenceMatcher(None, loose_key(a), loose_key(b)).ratio()
    return max(n1, n2)


# メガストーン名の構文: 名前表は「<種族名>ナイト[X|Y|Z]」(リザードンナイトX / ガブリアスナイトZ) だが、手入力の登録では
# 「メガ<種族名>Zナイト」のような並びも使われる。文字列全体の類似度で照合すると接尾辞 (X/Y/Z) の位置違いが
# 無印の石に吸われる (2026-09-29 第17回: 登録の「メガガブリアスZナイト」が ガブリアスナイト (通常のメガ) に解決され、
# 助言のメガ後の種族値・タイプが別フォルムになっていた)。種族部と接尾辞を分けて取り出し、接尾辞は厳密一致で照合する
_STONE_RE = re.compile(r"^(.+?)([XYZ])?(?:ナイト|ナイド)([XYZ])?$")


def parse_mega_stone_name(text: str) -> Optional[tuple]:
    """メガストーン名 → (種族部, 接尾辞 'x' / 'y' / 'z' / '')。ストーン名の形 (…ナイト…) でなければ None。純粋。
    種族部に「メガ」の接頭辞が付いていてもそのまま返す (メガニウムナイトのような種族名と区別できないため、呼び出し側が
    接頭辞なしの候補も試す)"""
    t = unicodedata.normalize("NFKC", text or "").strip()
    t = re.sub(r"[\s!！]", "", t)
    m = _STONE_RE.match(t)
    if not m or not m.group(1).strip():
        return None
    return m.group(1).strip(), (m.group(2) or m.group(3) or "").lower()


def stone_species_variants(species_part: str) -> list:
    """ストーン名の種族部の照合候補: そのまま + 先頭の「メガ」を外したもの (「メガガブリアスZナイト」→ ガブリアス)"""
    out = [species_part]
    if species_part.startswith("メガ") and len(species_part) > len("メガ"):
        out.append(species_part[len("メガ"):])
    return out


_ILLEGAL_SPECIES_PATH = (Path(__file__).resolve().parent.parent
                         / "pokemon-showdown" / "data" / "mods"
                         / "champions" / "formats-data.ts")


def champions_illegal_ids() -> set:
    """チャンピオンズ非参戦の種族ID集合 (modの定義で tier: Illegal 明示)。

    全国図鑑ベースの辞書で解決すると、OCRの揺れが非参戦の近縁種へ飛ぶ
    (2026-08-05接続テスト: ゲッコウガ→ケイコウオ / メタグロス→メタング。
    どちらも非参戦)。明示的に Illegal とされた種族だけを除外し、
    定義に無い種族は安全側で残す。ファイルが読めなければ空集合 (制限なし)。
    """
    try:
        text = _ILLEGAL_SPECIES_PATH.read_text(encoding="utf-8")
    except OSError:
        return set()
    out = set()
    for m in re.finditer(r"^\t(\w+): \{([^}]*)\}", text, flags=re.M):
        if '"Illegal"' in m.group(2):
            out.add(m.group(1))
    return out


class NameResolver:
    """jp_names.json に基づく日本語名 -> 英語ID の解決器。

    種族はチャンピオンズ参戦のものに絞る (非参戦種は解決候補にしない)。
    """

    def __init__(self, path: Path = JP_NAMES_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        illegal = champions_illegal_ids()
        # category -> [(jp, value, norm_key, loose)]
        self._entries: dict[str, list[tuple]] = {}
        for cat, table in raw.items():
            rows = []
            for jp, val in table.items():
                if (cat == "species" and illegal
                        and isinstance(val, dict)
                        and val.get("id") in illegal):
                    continue
                rows.append((jp, val, normalize(jp), loose_key(jp)))
            self._entries[cat] = rows
        # メガストーン: (日本語名, id, 種族部の正規化キー, 種族部の緩いキー, 接尾辞)
        self._stones: list[tuple] = []
        for jp, val, _nk, _lk in self._entries.get("items", []):
            parsed = parse_mega_stone_name(jp)
            if parsed:
                self._stones.append((jp, val, normalize(parsed[0]), loose_key(parsed[0]), parsed[1]))

    def categories(self):
        return list(self._entries.keys())

    def resolve_mega_stone(self, text: str, cutoff: float = 0.8) -> Optional[tuple]:
        """メガストーン名を「種族部の類似 + 接尾辞 (X/Y/Z) の厳密一致」で解決する。
        戻り値: (日本語名, id, 種族部のスコア) または None (ストーン名の形でない / 一致なし)"""
        parsed = parse_mega_stone_name(text)
        if not parsed or not self._stones:
            return None
        species_part, suffix = parsed
        best, best_score = None, 0.0
        for variant in stone_species_variants(species_part):
            key, lkey = normalize(variant), loose_key(variant)
            if not key:
                continue
            for jp, val, nk, lk, sfx in self._stones:
                if sfx != suffix:
                    continue
                s = SequenceMatcher(None, key, nk).ratio()
                if lk and lkey:
                    s = max(s, SequenceMatcher(None, lkey, lk).ratio())
                if s > best_score:
                    best, best_score = (jp, val, s), s
        return best if best and best_score >= cutoff else None

    @lru_cache(maxsize=4096)
    def resolve(self, text: str, category: str, cutoff: float = 0.8) -> Optional[tuple]:
        """text に最も近い名前を返す。戻り値: (日本語名, 値, スコア) または None。

        値は species の場合 {"id":..., "num":...}、それ以外は英語ID文字列。
        持ち物はメガストーン名の構文を先に見る (接尾辞 X/Y/Z を厳密に扱う。resolve_mega_stone 参照)
        """
        if not text:
            return None
        if category == "items":
            stone = self.resolve_mega_stone(text, cutoff)
            if stone:
                return stone
        key = normalize(text)
        lkey = loose_key(text)
        if not key:
            return None

        best = None
        best_score = 0.0
        for jp, val, nk, lk in self._entries.get(category, []):
            # 完全一致は即決
            if key == nk:
                return (jp, val, 1.0)
            # 長さが大きく違う候補はスキップ (高速化)
            if abs(len(nk) - len(key)) > max(2, int(len(key) * 0.5)):
                continue
            s = SequenceMatcher(None, key, nk).ratio()
            if lk and lkey:
                s = max(s, SequenceMatcher(None, lkey, lk).ratio())
            if s > best_score:
                best_score = s
                best = (jp, val, s)

        if best and best_score >= cutoff:
            return best
        return None

    def ja_of(self, category: str, value) -> Optional[str]:
        """英語ID -> 日本語名の逆引き"""
        for jp, val, _nk, _lk in self._entries.get(category, []):
            if val == value:
                return jp
        return None

    def resolve_restricted(self, text: str, category: str, allowed_ids,
                           cutoff: float = 0.6) -> Optional[tuple]:
        """許可されたID集合の中からのみ解決する (特性の種族別3択など)。

        候補が数件に絞られている前提なので、通常より低いcutoffでも安全。
        """
        if not text or not allowed_ids:
            return None
        key, lkey = normalize(text), loose_key(text)
        best, best_score = None, 0.0
        for jp, val, nk, lk in self._entries.get(category, []):
            if val not in allowed_ids:
                continue
            s = SequenceMatcher(None, key, nk).ratio()
            if lk and lkey:
                s = max(s, SequenceMatcher(None, lkey, lk).ratio())
            if s > best_score:
                best_score = s
                best = (jp, val, s)
        return best if best and best_score >= cutoff else None

    def resolve_species(self, text: str, cutoff: float = 0.8) -> Optional[tuple]:
        """種族名解決。メガ表記 (メガXXX) にも対応。戻り値: (日本語名, showdown_id, スコア)"""
        r = self.resolve(text, "species", cutoff)
        if r:
            jp, val, score = r
            return (jp, val["id"], score)
        return None

    def find_in_text(self, text: str, category: str, min_len: int = 5) -> Optional[tuple]:
        """文中に含まれる名前 (技等) を部分一致で探す。

        複合メッセージ (「相手のXのシャドーボール!効果は〜」) から技名を拾う用途。
        誤検出を避けるため正規化後 min_len 文字以上の名前のみ対象。
        戻り値: (日本語名, 値) or None (最長一致を優先)
        """
        key = normalize(text)
        lkey = loose_key(text)
        if not key:
            return None
        best = None
        for jp, val, nk, lk in self._entries.get(category, []):
            if len(nk) < min_len:
                continue
            if nk in key or (lk and len(lk) >= min_len and lk in lkey):
                if best is None or len(nk) > len(normalize(best[0])):
                    best = (jp, val)
        return best

    def find_species_in_text(self, text: str, candidates: Optional[list] = None,
                             cutoff: float = 0.75) -> Optional[tuple]:
        """メッセージ文中に含まれる種族名を探す (「相手の リザードンは〜」等)。

        candidates に日本語種族名リストを渡すとその中だけを部分一致で探す。
        戻り値: (日本語名, showdown_id, スコア)
        """
        key = normalize(text)
        lkey = loose_key(text)
        if not key:
            return None
        rows = self._entries.get("species", [])
        if candidates:
            cand_keys = {normalize(c) for c in candidates}
            rows = [r for r in rows if r[2] in cand_keys]

        best = None
        best_score = 0.0
        for jp, val, nk, lk in rows:
            if len(nk) < 3:
                continue
            if nk in key:
                s = 0.9 + 0.1 * min(1.0, len(nk) / 8)
            elif lk and len(lk) >= 3 and lk in lkey:
                # 小書き文字/濁点のOCR誤読を許容した部分一致
                s = 0.8 + 0.1 * min(1.0, len(lk) / 8)
            else:
                continue
            # 長い名前ほど優先 (「リザード」より「リザードン」)
            if s > best_score or (best and abs(s - best_score) < 1e-9 and len(nk) > len(normalize(best[0]))):
                best_score = s
                best = (jp, val["id"], s)
        if best and best_score >= cutoff:
            return best
        return None
