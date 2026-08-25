"""種族ごとの合法特性 (champions_dex.json 由来)。

各ポケモンの特性は最大3択程度なので、画面から読んだ特性はその種族の
合法特性セットに対して検証する (メガラグラージに「ばけのかわ」が付くような
誤帰属を弾く)。メガ・リージョン等の同系フォルムは baseSpecies でまとめ、
どのフォルムでも同じ集合を返す (メガシンカ前後の追跡ずれに耐えるため)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEX_PATH = (Path(__file__).resolve().parent.parent
            / "champions_agent" / "data" / "champions_dex.json")

_LEGAL: Optional[dict] = None
_FORMS: Optional[dict] = None   # フォルムkey -> そのフォルム自身の特性IDセット


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _load() -> dict:
    global _LEGAL
    if _LEGAL is not None:
        return _LEGAL
    _LEGAL = {}
    try:
        species = json.loads(DEX_PATH.read_text(encoding="utf-8"))["species"]
    except Exception:
        return _LEGAL
    groups: dict = {}
    for key, ent in species.items():
        base = _to_id(ent.get("baseSpecies") or key)
        abset = {_to_id(a) for a in (ent.get("abilities") or {}).values()}
        groups.setdefault(base, set()).update(abset)
    for key, ent in species.items():
        base = _to_id(ent.get("baseSpecies") or key)
        _LEGAL[key] = groups.get(base) or None
    return _LEGAL


def legal_abilities(species_id: Optional[str]):
    """種族の合法特性IDセット (同系フォルム込み)。不明な種族は None"""
    if not species_id:
        return None
    return _load().get(_to_id(species_id))


def _load_forms() -> dict:
    global _FORMS
    if _FORMS is not None:
        return _FORMS
    _FORMS = {}
    try:
        species = json.loads(DEX_PATH.read_text(encoding="utf-8"))["species"]
    except Exception:
        return _FORMS
    for key, ent in species.items():
        _FORMS[_to_id(key)] = {_to_id(a)
                               for a in (ent.get("abilities") or {}).values()}
    return _FORMS


# メガフォルムIDの末尾 (…mega / …megax / …megay)。ヤンマ→yanmega のような
# 自然名は candidates 探索が空になるため誤爆しない
_MEGA_TAIL = re.compile(r"mega[xy]?$")


def mega_form_id(species_id: Optional[str],
                 item_id: Optional[str] = None) -> Optional[str]:
    """基本形の種族IDからメガフォルムのIDを導出する (一意に決まる場合のみ)。

    X/Y両形態がある種はメガストーンIDの末尾 (x/y) で判別し、判別できなければ
    None (誤確定より未確定を選ぶ)。メガ名がOCRで読めないときのフォールバック
    (2026-08-25 第9回: 「メガスコィラン」等の崩れでメガ種族値が反映されなかった)。
    """
    forms = _load_forms()
    key = _to_id(species_id or "")
    if not key or _MEGA_TAIL.search(key):
        return None
    cands = [k for k in forms if k.startswith(key) and "mega" in k[len(key):]]
    if len(cands) > 1 and item_id:
        suffix = item_id[-1] if item_id[-1] in ("x", "y") else None
        narrowed = [c for c in cands if suffix and c.endswith("mega" + suffix)]
        cands = narrowed or cands
    return cands[0] if len(cands) == 1 else None


def fixed_ability(species_id: Optional[str], is_mega: bool = False,
                  item_id: Optional[str] = None) -> Optional[str]:
    """特性が一意に確定する場合はそのIDを返す。

    - 特性が1つしかない種族 (例: カイリュー=せいしんりょく)
    - メガシンカ後 (メガフォルムの特性は固定。リザードンのようにX/Yがある
      場合はメガストーンのIDで判別し、判別できなければ確定しない)
    """
    forms = _load_forms()
    key = _to_id(species_id or "")
    if not key:
        return None
    if is_mega and not _MEGA_TAIL.search(key):
        # まだ基本形のIDならメガフォルムを導出する。従来の endswith("mega")
        # 判定は …megax/…megay を「基本形」と誤判し、X/Y形態の特性を
        # 確定できていなかった (2026-08-25修正)
        mega = mega_form_id(key, item_id)
        if mega is None:
            return None
        abset = forms.get(mega)
    else:
        abset = forms.get(key)
    if abset and len(abset) == 1:
        return next(iter(abset))
    return None
