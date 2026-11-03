"""持ち物の推定の手がかりを実戦ログ (logs/battles/*.jsonl) から数える集計の道具 (読み取りだけ。2026-10-30 レビュー、開発者承認)。

設計文書 docs/DESIGN_ITEM_INFERENCE_20261030.md §1.2 の実数の出典。共通処理は common.py、CLI は 3 本:

    python -B -m tools.item_cues.cues   --logs <logs/battles> [--db <使用率 DB の写し> --snapshot 75 82] [--out DIR]
    python -B -m tools.item_cues.field  --logs <logs/battles> [--out DIR]
    python -B -m tools.item_cues.recall --logs <logs/battles> [--out DIR]

出力は <out>/<cli>_<YYYYMMDD_HHMMSS>.md / .json (既定の out は logs/item_cues)。先頭に再現の記録 (対象ログのディレクトリ・件数・期間、
全ファイルの sha16 を連結した sha16、version 行の集約、使用率 DB のパスと snapshot、除外規則と config の値、実行引数、コードの版)。

10/30 の scratchpad の集計スクリプト 11 本との対応:

| 元のスクリプト | CLI | 節 |
|---|---|---|
| count_cues.py (A 対戦の数・ターン) | cues | 対戦の数・最大ターンの分布 |
| count_cues.py (B 持ち物の発火) | cues | 持ち物の表示 |
| count_cues.py (C 判明した持ち物) | cues | 相手の判明した持ち物 |
| count_cues.py (D 天候・フィールド・壁) | field | 開始・終了・ターン数・設定した個体の持ち物 |
| count_cues.py (E こだわり) | cues | こだわりの手がかり (固定・否定) |
| count_cues.py (F 交代の読み落とし) | cues | 状態の場の個体の変化 |
| count_cues.py (G frames) | cues | 読み取り欠落 |
| refine_cues.py (1-2 こだわりの当たり外れ) | cues | こだわりの手がかり (自分側は登録で分ける) |
| refine_cues.py (3-5 終了までのターン数・機会) | field | 終了までのターン数・6 ターン以上の機会・文言の無い長い開始 |
| negation_guard.py | cues | 否定の守りの条件 (帰属 2 通り。guard_1030 / guard_partial / guard_full) |
| recall_cues.py (たべのこし) | recall | たべのこし の表示の再現率 |
| recall_cues.py (天候) | recall | 終わったはずの天候の終了の文言 |
| retrigger.py | field | 天候の再発動・上書き |
| field_turns.py | field | 状態の残りターンの分布・場の状況画面 |
| player_items.py | recall | 自分の登録の持ち物ごとの場に出た数と表示 |
| known_by_month.py | cues | 相手の判明した持ち物の月ごと |
| ev_infer_rows.py | cues | 型推定の記録 (版 2 の advice 行の ev_infer) |
| explore_types.py | cues | ログの概要 (行の種類・場面・発火 ID の接頭辞) |
| db_items.py | cues (--db --snapshot) | 使用率 DB の持ち物の一覧 |

否定の守りの条件の名前 (2026-10-31 レビューで改名。10/30 の出力の JSON は旧名):

| 10/30 の出力 | 今の名前 | 意味 |
|---|---|---|
| clean_1030 | guard_1030 | 10/30 の定義 (turn が同じ・わるあがき・場の個体の変化のどれでもない) |
| clean | guard_partial | 設計文書 §3.4 の条件の一部だけ (わるあがき・場の個体の変化・時刻の差)。全条件を満たす標本ではない |
| (なし) | guard_full | 較正用の確定標本。今の記録では全条件を検査できないので常に None (未判定) |
| lists.guard_clean_1030_not_clean | lists.guard_1030_not_partial | guard_1030 を通り guard_partial を通らない組 |

各 guard の検査した条件・未検査の条件は common.GUARD_DEFS (cues の出力の JSON の guards にも入れる)。
"""
