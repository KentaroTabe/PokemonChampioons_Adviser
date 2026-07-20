"""
meta_sets(DBに蓄積された代表的な型)から、ポケモンごとの「役割タグ」を
ルールベースで自動付与し、pokemon_role_tags テーブルへ保存する。

役割タグは、性格別(offense/cycle/stall等)のチーム生成バイアス(env/team_builder.py)や
報酬シェイピング(env/reward.py)で「そのプレイスタイルに合うポケモン/型か」を
判定する材料として使う。

判定は「技構成のキーワード」だけでなく、種族値(耐久指標: HP×Def, HP×SpD)も
補助シグナルとして併用する。回復技を持たない高耐久ポケモン(例: 定数ダメージや
遅延技で粘る型)もwallとして正しく拾えるようにするため。
"""
from __future__ import annotations

from champions_agent.config import USAGE_TARGET_FORMAT
from champions_agent.data import database as db

# --- 技名キーワード分類(PokeAPI slug表記ベース。名前正規化後のmove_nameと突合) ---
SETUP_MOVES = {
    "swordsdance", "nastyplot", "dragondance", "calmmind", "quiverdance",
    "bulkup", "shellsmash", "irondefense", "agility", "workup",
}
RECOVERY_MOVES = {
    "recover", "roost", "slackoff", "softboiled", "moonlight", "morningsun",
    "synthesis", "wish", "rest", "strengthsap",
}
PIVOT_MOVES = {
    "uturn", "voltswitch", "flipturn", "partingshot", "batonpass", "teleport",
}
HAZARD_SETUP_MOVES = {
    "stealthrock", "spikes", "toxicspikes", "stickyweb",
}
HAZARD_REMOVAL_MOVES = {
    "rapidspin", "defog", "courtchange",
}
STATUS_SUPPORT_MOVES = {
    "thunderwave", "willowisp", "toxic", "spore", "stunspore", "sleeppowder",
    "glare", "yawn",
}
PASSIVE_MOVES = {"protect", "substitute", "toxic", "willowisp", "haze", "roar", "whirlwind"}

CHOICE_ITEMS = {"choiceband", "choicespecs", "choicescarf"}
BULKY_ITEMS = {"leftovers", "rockyhelmet", "assaultvest", "heavydutyboots"}

ROLES = [
    "sweeper", "wallbreaker", "wall", "pivot",
    "hazard_setter", "hazard_removal", "status_support",
]

# 耐久指標(HP*Def, HP*SpD の種族値積)のうち、この値を超えると
# 「回復技が無くても実質的にwallとして機能しうる」とみなす閾値。
# 種族値ベースの目安(例: HP110×Def110=12100 程度から高耐久帯とみなす)。
BULK_STAT_THRESHOLD = 12000


def _bulk_score(pokemon_row: dict | None) -> float:
    """HP×Def および HP×SpD の種族値積から、技構成に依存しない耐久スコア(0-1)を算出する。

    回復技を持たない「特殊受け/物理受け」(定数ダメージ・遅延技・アイテムで粘る型)を
    正しくwallとして拾うための補助シグナル。
    """
    if not pokemon_row:
        return 0.0
    hp = pokemon_row.get("hp") or 0
    defense = pokemon_row.get("defense") or 0
    sp_defense = pokemon_row.get("sp_defense") or 0

    bulk_phys = hp * defense
    bulk_spec = hp * sp_defense
    best_bulk = max(bulk_phys, bulk_spec)

    if best_bulk <= 0:
        return 0.0
    return min(1.0, best_bulk / BULK_STAT_THRESHOLD)


def _score_meta_set(row: dict, pokemon_row: dict | None = None) -> dict[str, float]:
    """1つの代表的な型(meta_setsの1行)に対して、各役割への合致スコア(0-1)を算出する。"""
    moves = {row.get("move1"), row.get("move2"), row.get("move3"), row.get("move4")}
    moves.discard(None)
    item = (row.get("item_name") or "").replace(" ", "").lower()

    scores = {r: 0.0 for r in ROLES}

    setup_count = len(moves & SETUP_MOVES)
    if setup_count > 0:
        scores["sweeper"] += min(1.0, 0.5 * setup_count)

    if item in CHOICE_ITEMS:
        scores["wallbreaker"] += 0.7
        scores["sweeper"] += 0.2

    # --- wallスコア: 技キーワード(回復技)+ 耐久種族値(HP*Def, HP*SpD)の複合判定 ---
    has_recovery = bool(moves & RECOVERY_MOVES)
    has_passive_kit = bool(moves & PASSIVE_MOVES) or item in BULKY_ITEMS
    bulk = _bulk_score(pokemon_row)

    if has_recovery:
        scores["wall"] += 0.6
    if item in BULKY_ITEMS:
        scores["wall"] += 0.2
    # 耐久種族値が高いほど加点(回復技の有無に関わらず機能する受けを拾う)
    scores["wall"] += 0.5 * bulk
    # 回復技が無くても、高耐久+受け構成(protect/toxic等の遅延技)なら追加加点
    if not has_recovery and has_passive_kit and bulk > 0.5:
        scores["wall"] += 0.2

    if moves & PIVOT_MOVES:
        scores["pivot"] += 0.7

    if moves & HAZARD_SETUP_MOVES:
        scores["hazard_setter"] += 0.8

    if moves & HAZARD_REMOVAL_MOVES:
        scores["hazard_removal"] += 0.8

    status_count = len(moves & STATUS_SUPPORT_MOVES)
    if status_count > 0:
        scores["status_support"] += min(1.0, 0.4 * status_count)

    return {k: round(min(v, 1.0), 3) for k, v in scores.items()}


def _fetch_pokemon_static_map(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT name, hp, attack, defense, sp_attack, sp_defense, speed FROM pokemon"
    ).fetchall()
    return {r["name"]: dict(r) for r in rows}


def build_role_tags(fmt: str = USAGE_TARGET_FORMAT, source: str | None = None) -> int:
    """最新スナップショットのmeta_setsから役割タグを再構築する。戻り値: 生成した行数。"""
    with db.get_connection() as conn:
        snapshot_id = db.latest_snapshot_id(conn, source=source, fmt=fmt)
        if snapshot_id is None:
            raise RuntimeError(
                f"usage_snapshot が見つかりません(source={source}, format={fmt})。"
                "先に data.ingest / data.build_meta を実行してください。"
            )

        meta_rows = conn.execute(
            "SELECT * FROM meta_sets WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchall()
        static_map = _fetch_pokemon_static_map(conn)

        conn.execute("DELETE FROM pokemon_role_tags WHERE snapshot_id = ?", (snapshot_id,))

        inserted = 0
        for row in meta_rows:
            row_d = dict(row)
            pokemon_row = static_map.get(row_d["pokemon_name"])
            scores = _score_meta_set(row_d, pokemon_row)
            for role, score in scores.items():
                if score <= 0:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO pokemon_role_tags
                        (snapshot_id, pokemon_name, role, score)
                    VALUES (?, ?, ?, ?)
                    """,
                    (snapshot_id, row_d["pokemon_name"], role, score),
                )
                inserted += 1
        conn.commit()

    print(f"[role_tagger] pokemon_role_tags 生成完了: snapshot_id={snapshot_id}, rows={inserted}")
    return inserted


if __name__ == "__main__":
    build_role_tags()
