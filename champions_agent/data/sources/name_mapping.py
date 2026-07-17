"""
Smogon/Pikalytics表記("Great Tusk", "Tauros-Paldea-Aqua" 等) と
PokeAPI slug表記("great-tusk", "tauros-paldea-aqua-breed" 等) の相互変換。

基本則: 小文字化 + スペースをハイフンに置換 でほぼ一致するが、
一部フォルム名などはPokeAPI側の命名が異なるため、例外テーブルで補正する。
"""
from __future__ import annotations

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
