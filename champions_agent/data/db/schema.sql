-- champions_agent 環境データベース スキーマ
-- SQLite想定。PokeAPIの静的データと、使用率統計(型構成含む)を保持する。

PRAGMA foreign_keys = ON;

-- ==============================================================
-- 静的マスタデータ(PokeAPI由来)
-- ==============================================================

CREATE TABLE IF NOT EXISTS pokemon (
    id              INTEGER PRIMARY KEY,       -- PokeAPI pokemon id
    name            TEXT NOT NULL UNIQUE,       -- 英語名(PokeAPI slug)
    display_name    TEXT,                       -- 日本語名(取得できれば)
    hp              INTEGER,
    attack          INTEGER,
    defense         INTEGER,
    sp_attack       INTEGER,
    sp_defense      INTEGER,
    speed           INTEGER,
    type1           TEXT,
    type2           TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS abilities (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    effect_text     TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pokemon_abilities (
    pokemon_id      INTEGER NOT NULL REFERENCES pokemon(id) ON DELETE CASCADE,
    ability_id      INTEGER NOT NULL REFERENCES abilities(id) ON DELETE CASCADE,
    is_hidden       INTEGER NOT NULL DEFAULT 0,
    slot            INTEGER,
    PRIMARY KEY (pokemon_id, ability_id)
);

CREATE TABLE IF NOT EXISTS moves (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    type            TEXT,
    category        TEXT,       -- physical / special / status
    power           INTEGER,
    accuracy        INTEGER,
    pp              INTEGER,
    priority        INTEGER,
    effect_text     TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pokemon_moves (
    pokemon_id      INTEGER NOT NULL REFERENCES pokemon(id) ON DELETE CASCADE,
    move_id         INTEGER NOT NULL REFERENCES moves(id) ON DELETE CASCADE,
    learn_method    TEXT,       -- level-up / machine / egg / tutor 等
    PRIMARY KEY (pokemon_id, move_id, learn_method)
);

CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    effect_text     TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- ==============================================================
-- 使用率統計(スナップショット方式。定期実行のたびに1行追加)
-- ==============================================================

CREATE TABLE IF NOT EXISTS usage_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,          -- 'smogon' / 'pikalytics' / 'dummy' 等
    format          TEXT NOT NULL,          -- 'gen9ou' 等
    rating_cutoff   INTEGER,                -- 集計に使われたレーティング下限
    source_month    TEXT,                   -- 集計対象月(例 '2026-06')
    number_of_battles INTEGER,              -- 集計母数(対戦数)
    source_url      TEXT,                   -- 取得元URL
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    note            TEXT
);


CREATE TABLE IF NOT EXISTS pokemon_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,          -- pokemonテーブルへは緩く紐付け(表記揺れ許容のため名前保持)
    usage_percent   REAL NOT NULL,
    rank            INTEGER
);

CREATE TABLE IF NOT EXISTS move_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    move_name       TEXT NOT NULL,
    usage_percent   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ability_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    ability_name    TEXT NOT NULL,
    usage_percent   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS item_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    item_name       TEXT NOT NULL,
    usage_percent   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tera_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    tera_type       TEXT NOT NULL,
    usage_percent   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS spread_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    nature          TEXT,
    evs             TEXT,       -- "252/0/0/252/4/0" のような文字列表現
    usage_percent   REAL NOT NULL
);

-- パーティ内の相棒傾向(チームメイト共起率)。選出/相手予測の材料に使う。
CREATE TABLE IF NOT EXISTS teammate_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    teammate_name   TEXT NOT NULL,
    usage_percent   REAL NOT NULL
);

-- ==============================================================
-- build_meta.py が生成する「代表的な型」の集約結果(自己対戦の相手チーム生成に使用)
-- ==============================================================

CREATE TABLE IF NOT EXISTS meta_sets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    ability_name    TEXT,
    item_name       TEXT,
    tera_type       TEXT,
    nature           TEXT,
    evs             TEXT,
    move1           TEXT,
    move2           TEXT,
    move3           TEXT,
    move4           TEXT,
    weight          REAL NOT NULL DEFAULT 0  -- この型が選ばれる相対的な重み(使用率ベース)
);

CREATE INDEX IF NOT EXISTS idx_pokemon_usage_snapshot ON pokemon_usage(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_move_usage_snapshot ON move_usage(snapshot_id, pokemon_name);
CREATE INDEX IF NOT EXISTS idx_ability_usage_snapshot ON ability_usage(snapshot_id, pokemon_name);
CREATE INDEX IF NOT EXISTS idx_item_usage_snapshot ON item_usage(snapshot_id, pokemon_name);
CREATE INDEX IF NOT EXISTS idx_meta_sets_snapshot ON meta_sets(snapshot_id, pokemon_name);

-- ==============================================================
-- 役割タグ(プレイスタイル/性格付けのためのポケモン・技・持ち物の役割分類)
-- ルールベースで自動付与する(data/role_tagger.py)。将来的にDB内の
-- 種族値・技・特性・持ち物の組み合わせから算出したスコアを格納する。
-- ==============================================================

CREATE TABLE IF NOT EXISTS pokemon_role_tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES usage_snapshot(id) ON DELETE CASCADE,
    pokemon_name    TEXT NOT NULL,
    role            TEXT NOT NULL,     -- 'sweeper' / 'wall' / 'pivot' / 'hazard_setter' / 'wallbreaker' / 'support' 等
    score           REAL NOT NULL DEFAULT 0,  -- そのポケモンがその役割にどれだけ合致するか(0-1目安)
    UNIQUE(snapshot_id, pokemon_name, role)
);

CREATE INDEX IF NOT EXISTS idx_role_tags_snapshot ON pokemon_role_tags(snapshot_id, pokemon_name);

