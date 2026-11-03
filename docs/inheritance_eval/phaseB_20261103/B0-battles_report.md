# B0-battles: 別条件のデータ構成 regmc_smogon_only_2026-09 と、Foul Play 同士 3 戦の完走・待ち時間・メモリの初回計測 (phaseB_20261103)

> 配置の注記 (2026-11-03、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。測定の出力 (manifest.json / measurements.jsonl 33 行 (pass 22 / fail 3 / excluded 3 / unexecuted 5) / summary.json / stages.jsonl / unknownitem_analysis.json / runs/) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261103/B0-battles/`**。エンジンは poke-engine f7b147f1 無改変 (.so sha256 06587dce…)、Foul Play は HEAD 8f541f8 に local-login と offline-data の差分を当てた写し (別の担当が作業中の変更を混ぜないため)。サーバーは一時 8130 (d849b220+patched、外向き 0 本の版)。全体 206 秒、一時サーバーは停止済み。

## A. データ構成の定義と影響
- 名前 `regmc_smogon_only_2026-09`。使い方: `FP_DATA_CACHE_DIR=<external>/data_pinned/configs/regmc_smogon_only_2026-09 FP_OFFLINE_DATA=1`。
- 使うデータ: Smogon chaos cutoff 0 (2026-09、69,462 戦)。写しの sha256 e4fcbb42…、原本 1a4462eb…。
- 無効にしたデータ (`missing_declared`): foulplay_full_sets / foulplay_replay_moves / showdown_sample_sets。3 件とも指定 URL が HTTP 404 (2026-11-03T04:02Z、証拠は data_pinned/retrieval_20261103/)。
- 実装: 宣言した型集合は `{}` として読み、読むたびに WARNING `PINNED_DATA_DECLARED_MISSING` を出す (ファイルは作らない)。宣言できるのは型集合 3 件だけで Smogon chaos は宣言できない。宣言の無い欠損は PinnedDataMissing で止まる。理由の無い宣言・ファイルとの矛盾・sha256 の不一致は PinnedDataConfigError で止まる。manifest の無いディレクトリ (fp_cache、alt_cutoff1500) は従来どおり。単体確認 31/31 (宣言) と 15/15 (ローカルログイン)。
- Foul Play 内の影響 (コードで確認): 相手の型は TeamDatasets が空になり、完全な型からの抽出 (手順 1・2) を通らず、SmogonSets (chaos の周辺分布: 配分 上位 20 × 特性 × 持ち物 上位 10 × テラス 上位 6 の積、独立を仮定) で組む。技はリプレイ由来の組が無く、使用率から 1 つずつ抽出して矛盾する組を外すだけ。選出の推定は team preview の MCTS の親和度で、型集合を直接は使わない。メガの推定は chaos の Raw count。ゾロアークの判定は発火しない。
- 実対戦の WARNING は A・B とも 9 行 (3 戦 × 3 件)、Could not sample 0、外部取得 0。4 件が揃った条件・cutoff 1500 の代替とは結果を混ぜない。

## B. 3 戦の結果 (A = regmc_speed (受け)、B = regmc_diag (挑戦)、両方 1000 ms、parallelism 1、threads 1)
| 戦 | 完走 | 勝者 | ターン | 判断数 A/B |
|---|---|---|---|---|
| 1 | 完走 | B | 10 | 13/12 |
| 2 | 完走 | A | 17 | 19/21 |
| 3 | 完走 | A | 8 | 9/11 |

| 項目 | 件数 | 判定 |
|---|---|---|
| `\|error\|` / Traceback / PinnedDataMissing / PinnedDataConfigError / 不正な選択 | すべて 0 | pass |
| UNKNOWNITEM | A 2,320 行 / B 6,686 行 | **fail** |
| NONE | 同数 | 状態の文字列の中の正規の値 (空き枠など)。記録のみ |

UNKNOWNITEM の内訳 (79 状態): 自分側 = グリムアル (lightclay) 41、イエッサン (terrainextender) 26、ペリッパー (damprock) 20、ブリジュラス (redcard) 8 (エンジンの Items に無く既定値になる。順序 2 の持ち物の列挙の件)。相手側 = グリムアル 36/38、イエッサン 16/25、メガボーマンダ 15/28 ほか (内訳は未確認)。3 戦は接続の動作確認で、強さの判定ではない。

## C. 速度 (ウォームアップ 1 戦の後、A の探索時間を変えて各 1 戦、B は 250 ms、同時実行 1。判断全体 = websocket で最後のメッセージを受け取ってから判断を送るまで)
| A の探索時間 | 判断数 | 判断全体 p50 / p95 / 最大 (s) | 探索計算 p50 (s) | 選出の判断 (s) | 1 標本の反復数 p50 / p95 | 反復/秒 | 時間切れ |
|---|---|---|---|---|---|---|---|
| 250 ms | 12 | 0.556 / 0.708 / 0.708 | 0.537 | 1.23 | 83,500 / 750,750 | 334,000 | 0 |
| 1000 ms | 15 | 1.331 / 1.607 / 1.890 | 1.307 | 2.93 | 317,000 / 4,139,900 | 317,000 | 0 |
| 3000 ms | 13 | 3.400 / 3.495 / 3.519 | 3.398 | 7.05 | 888,000 / 10,000,000 | 296,000 | 0 |
| 3 戦の A (1000 ms) | 38 | 1.322 / 1.458 / 1.518 | 1.311 | 2.48 | 298,500 / 3,352,700 | 298,500 | 0 |
- 手の判断は全件 MCTS 1 標本。選出は 2 標本を順に探索するので探索時間の約 2 倍。判断全体と設定値の差 0.3〜0.4 秒の大半は find_best_move の中 (worker の起動、型の抽出、状態の変換)。websocket から送信までの上乗せは p50 で 0.03 秒以下。1 ターンの残り時間の最小 55 秒、時間切れ 0。

## D. メモリ (A・B の Foul Play と子孫を 0.5 秒間隔)
| 条件 | RSS 合計の最大 (MB) | 単独の最大 (MB) | %CPU 最大 / p50 | プロセス数 |
|---|---|---|---|---|
| 3 戦 | 446.5 | 159.8 | 198.6 / 156.4 | 6 |
| 250 ms | 298.3 | 70.1 | 195.3 / 71.0 | 6 |
| 1000 ms | 277.5 | 152.2 | 162.4 / 92.5 | 6 |
| 3000 ms | 483.2 | 353.6 | 195.7 / 98.9 | 6 |
swap (Mac 全体) は 10,146〜10,342 MB で計測前から約 10 GB。計測中は別の実験 (run_experiment.py、約 1〜2 コア) と並走していた (load 4.4〜6.2 / 8 コア)。

## E. 残る未確認
各条件 1 戦でばらつきと区間は無い。強さは判定していない。サンプル数・並列数の調整、長時間動作時の低下、並走する処理との競合の切り分け、相手側の UNKNOWNITEM の内訳、反復数 10M が上限値か、速度の段の状態の文字列での検出とターン数、4 件が揃った条件との比較 (データが無い)。`pin_data.py` の引数なし実行は manifest を作り直して `data_configs` が消える (--apply は残す)。

## 1 行まとめ
用途: Reg M-C での接続と待ち時間の確認 → 対照: 無し (初回計測) → 正しさ: 3/3 完走、エラー 0。ただし自分の持ち物 4 種が UNKNOWNITEM → 効果: 未評価 → 費用: 判断 p50 0.56 / 1.33 / 3.40 秒、RSS 最大 483 MB → 採否: 保留 → 未確認範囲: 各 1 戦、強さ、相手側の UNKNOWNITEM の内訳、長時間動作。
