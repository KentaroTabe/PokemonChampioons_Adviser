"""
Smogon/Pikalytics表記("Great Tusk", "Tauros-Paldea-Aqua" 等) と
PokeAPI slug表記("great-tusk", "tauros-paldea-aqua-breed" 等) の相互変換。

基本則: 小文字化 + スペースをハイフンに置換 でほぼ一致するが、
一部フォルム名などはPokeAPI側の命名が異なるため、例外テーブルで補正する。
"""
from __future__ import annotations

import re

# Smogon表記 -> PokeAPI slug の例外(基本則で変換できないもの)

SMOGON_TO_POKEAPI_OVERRIDES: dict[str, str] = {
    "tauros-paldea-aqua": "tauros-paldea-aqua-breed",
    "tauros-paldea-blaze": "tauros-paldea-blaze-breed",
    "tauros-paldea-combat": "tauros-paldea-combat-breed",
    "ogerpon-wellspring": "ogerpon-wellspring-mask",
    "ogerpon-hearthflame": "ogerpon-hearthflame-mask",
    "ogerpon-cornerstone": "ogerpon-cornerstone-mask",
    "great tusk": "great-tusk",
    "walking wake": "walking-wake",
    "iron valiant": "iron-valiant",
    "iron bundle": "iron-bundle",
    "iron hands": "iron-hands",
    "iron moth": "iron-moth",
    "iron thorns": "iron-thorns",
    "iron jugulis": "iron-jugulis",
    "iron treads": "iron-treads",
    "iron boulder": "iron-boulder",
    "iron crown": "iron-crown",
    "roaring moon": "roaring-moon",
    "raging bolt": "raging-bolt",
    "gouging fire": "gouging-fire",
    "sandy shocks": "sandy-shocks",
    "scream tail": "scream-tail",
    "brute bonnet": "brute-bonnet",
    "flutter mane": "flutter-mane",
    "slither wing": "slither-wing",
    "chien-pao": "chien-pao",
    "chi-yu": "chi-yu",
    "ting-lu": "ting-lu",
    "wo-chien": "wo-chien",
    "porygon-z": "porygon-z",
    "mr. mime": "mr-mime",
    "mime jr.": "mime-jr",
    "type: null": "type-null",
    "farfetch'd": "farfetchd",
    "sirfetch'd": "sirfetchd",
}


def to_pokeapi_slug(name: str) -> str:
    """Smogon/Pikalytics表記の種族名をPokeAPI slugへ変換する。"""
    key = name.strip().lower()
    if key in SMOGON_TO_POKEAPI_OVERRIDES:
        return SMOGON_TO_POKEAPI_OVERRIDES[key]
    slug = key.replace(" ", "-").replace("'", "").replace(".", "").replace(":", "")
    slug = slug.replace("--", "-")
    return slug


def _title_case_token(token: str) -> str:
    """"farfetch'd" -> "Farfetch'd" のように先頭文字のみ大文字化する。"""
    return token[:1].upper() + token[1:] if token else token


def _title_case_smogon_name(name: str) -> str:
    """Smogon表記の小文字名(例: "tauros-paldea-aqua")をShowdown表示用に
    トークンごとCapitalizeする(区切り文字のスペース/ハイフンは維持)。
    """
    tokens = re.split(r"([-\s])", name)
    return "".join(t if t in ("-", " ") else _title_case_token(t) for t in tokens)


# PokeAPI slug -> Showdown正式種族名 の例外テーブル。
# SMOGON_TO_POKEAPI_OVERRIDES の逆引き(値と一意対応するため自動生成できる)。
POKEAPI_TO_SHOWDOWN_OVERRIDES: dict[str, str] = {
    slug: _title_case_smogon_name(smogon_key)
    for smogon_key, slug in SMOGON_TO_POKEAPI_OVERRIDES.items()
}

# チャンピオンズ固有の対応: championsbattledata のIDと
# Showdown champions mod の合法種族表記の差分
POKEAPI_TO_SHOWDOWN_OVERRIDES.update({
    # チャンピオンズにはエターナルフラワーのみ登場 (素のフローエットはIllegal)
    "floette": "Floette-Eternal",
})


def to_showdown_name(slug: str) -> str:
    """PokeAPI slug(DBのpokemon_name等)をPokemon Showdownの正式な種族名表記へ変換する。

    Showdownのteam importer(パックドチーム/importable format)はスペース+ハイフン混在の
    独特な命名規則("Great Tusk", "Ogerpon-Wellspring", "Tauros-Paldea-Aqua"等)を要求するため、
    PokeAPI slug表記(全てハイフン区切り)のままではニックネーム扱いされ拒否される。

    例外テーブルに無い単純なケース(フォーム差分の無いポケモン等)は、
    ハイフン区切りをスペースに変換しトークンごとCapitalizeする基本則で対応する
    (例: "great-tusk" -> "Great Tusk", "iron-valiant" -> "Iron Valiant")。
    """
    key = slug.strip().lower()
    if key in POKEAPI_TO_SHOWDOWN_OVERRIDES:
        return POKEAPI_TO_SHOWDOWN_OVERRIDES[key]
    return " ".join(w.capitalize() for w in key.split("-"))



def normalize_move_name(name: str) -> str:
    """Smogonの技名表記(スペースなし小文字 例: 'stealthrock')をPokeAPI slug('stealth-rock')へ変換。

    SmogonのMoves辞書はスペースを除去した小文字表記のため、
    正確な変換にはPokeAPI側の技名一覧との突合が必要。
    プロトタイプとしては簡易的にそのまま小文字化して返し、
    DB引き当て時に move_alias テーブル(将来追加)で補正する運用を想定する。
    """
    return name.strip().lower()


def normalize_item_name(name: str) -> str:
    """Smogonの持ち物表記(例: 'heavydutyboots')をそのまま小文字化して返す。

    技名同様、正式なPokeAPI slug('heavy-duty-boots')との突合は
    item_alias(将来追加)で補正する。
    """
    return name.strip().lower()


def normalize_ability_name(name: str) -> str:
    return name.strip().lower()
