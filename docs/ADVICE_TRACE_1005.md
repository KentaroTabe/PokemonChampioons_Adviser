# 助言の追跡 (2026-10-05): 版・状態・推奨・表示を 1 本でつなぐ (② 運用条件の記録 / ③ 正解つき局面)

構造評価 (10/5) の指摘「内部評価で強いことと実戦で役立つ助言を出せることがつながっていない」への最初の到達点は、
各局面について **どの版が、どの状態を見て、何を推奨し、いつ表示されたか** を 1 本で追えることにする。勝率の差がまだ測れなくても、
認識・判断・表示・操作のどこを直すべきかを切り分けられる。

## 1. 記録 (②)。受入条件との対応

| 受入条件 | 記録 | 場所 |
|---|---|---|
| 1. 指定された Package と実際に使ったモデルの両方 | 対戦の先頭の `version` 行: 指定 Package (logs/.experiment_package)、実際に読んだ選出モデルの経路と sha256、退避の理由 (party_not_in_package / package_model_missing / selection_model_missing)、行動方策 (RL) の zip と sha と読み込めたか、構築 (config/my_team.json) の sha と 6 体、git の commit、図鑑と効果表の sha、選出の特徴量の版。全体の digest が version_id | `advisor/versions.py`、`battle_logger._open_new` |
| 1. 評価時の版との一致 | `real_eval` / `advice_trace` が Package の manifest の selection_model_sha256 と version 行の sha を突き合わせ、一致 / 不一致 / 判定不能 を分ける | `tools/advice_trace.version_check` |
| 2. 生成時刻と表示時刻を分ける | 助言の行に advice_id と t_gen (生成、サーバーの時計)。ブラウザは描画したフレームで `advice_shown` を返し、`display` 行に t_shown (ブラウザの時計) が残る。遅延 = t_shown − t_gen (時計差を含む) | `battle_logger.on_advice / on_display`、`server.py advice_shown`、`index.html` |
| 2. 古い状態への助言が遅れて表示された | 助言の行に助言が見た簡約状態 (state) とその digest (state_id)、turn。表示時点の直近 scene の turn が助言の turn より大きいか、場の個体が違えば stale | `advice_trace.display_rows` |
| 3. 実行不能判定に同じ誤認識を使わない | 「システムの状態で選べるか」(feasible_system: 助言が見た state で、ひんし・技欄・PP・控えの選出を検査。読めていなければ None) と「正解ラベルで選べるか」(feasible_truth: 局面集の legal_actions か truth.state) を分ける | `advice_trace.feasible_in_state`、`scene_eval.legal_by_label` |

各助言の行は `policy` も持つ (選出の助言: 使ったモデル experiment:<id> / deployed と ◎ の出所 model / rule。対戦の助言: RL が読み込めていたか)。

コマンド:

    python -m tools.advice_trace --last 3            # 対戦ごとの鎖 (版 → 状態 → 推奨 → 表示 → 選べたか)
    python -m tools.team_build.real_eval             # trace の集計 (版の一致 / 表示率と遅延 / 実行不能率) が summary に入る

## 2. 正解つき局面 (③)

`python -m tools.scene_eval extract --out logs/scenes/set1.jsonl --consistent 20 --failure 10` が、直近の対戦ログの対戦中の助言から
整合 20 + 既知の失敗 10 の雛形を切り出す。分類は記録から機械的に: 手動修正が ±30 秒にあれば hp_stuck、表示が無ければ advice_stop、
表示が遅ければ late、古い状態への表示なら stale、それ以外は consistent。

ラベル (truth) は人が埋める。最善手は要らない:
- `state`: 正しい簡約状態 (system_state を直したもの。HP、ひんし、場の個体、技欄)
- `legal_actions`: 正解の状態で選べる行動 [{kind, id}]
- `deadline_s`: 表示期限 (決定画面が開いてからの秒)
- `notes`: 何が起きていたか

`python -m tools.scene_eval evaluate --set logs/scenes/set1.jsonl` が局面ごとに出す:
- 当時の推奨 (ログ) / 復元した状態での推奨 (今のコード) / 正しい状態での推奨
- feasible_system / feasible_truth (None = 判定不能)
- displayed_in_time (表示時刻 − 決定画面が開いた時刻 ≤ deadline_s)

**注意**: この 30 局面は失敗例を意図的に含めた層化標本なので、ここでの誤り率を実戦全体の発生率として扱わない (集計の note に明記)。
発生率は `real_eval` の trace (全対戦) で見る。

## 3. 残り

- 配布版の選出モデルが分布外のときは規則に退避する。退避の有無は version 行と助言の policy に残るので、評価側で「測定と同じモデルが
  使われた対戦」だけを選べる (real_eval の次の作業: version 一致で層別した勝率)
- 決定監査 (tools/decision_audit) の遅延は決定画面が開いてから助言の行が出るまで。表示の行を使う遅延は advice_trace 側に置いた。
  同じ定義にそろえるかは 1 回の接続テストの記録を見てから決める
- 局面集の雛形は logs/ (リポジトリ外)。ラベル付けの手順と 30 局面の最初の結果は接続テストの後に記録する
