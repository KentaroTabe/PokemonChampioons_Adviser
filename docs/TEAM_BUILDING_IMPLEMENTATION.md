# パーティ構築システム 実装案 — アルゴリズムは完成形、学習・昇格・評価は保守的に

作成: 2026-09-06。設計は docs/TEAM_BUILDING_PLAN.md v3 (Team × 専用選出モデルの共同最適化、相手系統の4階層、
confidence racing、Final Build Package) を前提とし、レビュー3 (「完成形の全学習ループを初日から自動運転するのが最大の危険」)
を反映して**学習・昇格・評価の権限だけを段階的に解放する**運用で実装する。

方針を一行で: **全コンポーネントを最初から作る。ただし学習成果物は shadow から始め、本番 (接続テストで使う Package) の
更新は人手の昇格コマンドでしか起きない。** 実装の段階化は「機能」ではなく「権限」で行う。

単一ユーザー・ローカル運用への読み替え: 悪意あるログ (レビュー3 §14)・アカウント分離 (§15)・多数ユーザー由来の
分布変形補正は現時点で対象外。ただしログの `source` / `data_quality` / `dataset_kind` フィールドは初日から持たせ、
将来の多数ユーザー化で構造を変えずに済むようにする。

---

## 1. 全体構成

```
入口          /build-team (チャット, Phase A) ── フロント依頼フォーム (Phase 後述)
                │
Build Pipeline  tools/team_build/run.py  S0〜S13  (--profile fast|medium|full)
                │   ├ LLM: concepts (Opus) / candidates 選抜 (Sonnet) / interventions 仮説 (Opus) / report (Sonnet)
                │   ├ Rules: spec, meta_snapshot, families, interaction, sets, validate
                │   ├ Learning (offline): adapt (選出モデル、候補ごと)
                │   └ Measurement: racing / ablation / stress / holdout (check_advisor_player)
                │
Evaluation Infra 相手系統化と4階層 (SEARCH-A/B, SELECTION, HOLDOUT[sealed], STRESS)、対応比較、4状態判定、
                対戦記録 (turn 単位)、方策集団 (ema/prev/best/anchors)、ユーザー遵守モデル
                │
Artifact Registry logs/registry/ (immutable, sha256): team / selection_model / rl_checkpoint / meta_snapshot /
                opponent_split / evaluation / package。status: shadow → canary → production (→ retired)
                │
Learning Loop   オンライン収集・オフライン更新: 実戦ログ (logs/battles, 読み取り専用) と合成ログ (runs/*/battles) を
                分離。収集 → 候補モデル学習 → offline 評価 → sealed validation → canary (接続テスト N 戦) → 昇格 (人手)
```

---

## 2. コンポーネントと責務

### 2.1 新規: `tools/team_build/`

| モジュール | 責務 | 主要 API (純粋関数はテスト対象) | 既存資産 |
|---|---|---|---|
| `spec.py` | BuildSpec (schema_version, objective, constraints, budget profile, regulation) | `parse_spec(form_or_text) -> BuildSpec`, `validate_spec(spec, owned, legal)` | advisor/my_team (所持一覧) |
| `registry.py` | 不変の成果物台帳。sha256 で id、`index.jsonl` に kind/status/created/manifest 参照 | `register(kind, path, meta) -> artifact_id`, `resolve(id) -> Path`, `set_status(id, shadow|canary|production|retired)`, `production(kind) -> id` | logs/pinned_models (置換) |
| `manifest.py` | run と Package の manifest | `build_manifest(run_dir) -> dict` (git commit, schema 版, prompt 版, LLM モデル, Showdown commit, 行動方策/選出モデル id, META_PIN, POOL_PIN, split id, 全 seed, 評価プロトコル版) | pin_models.sh |
| `families.py` | 相手系統化と層化分割、holdout の封印 | `cluster_families(teams, jaccard_min, same_mega) -> list[Family]`, `stratified_split(families, ratios, seed) -> SplitMap`, `seal_holdout(split, run_id) -> sealed_id` | env/ranked_teams |
| `meta_snapshot.py` | Meta Snapshot の固定 (健全スナップショット、ローカルメタ、合法種) | `snapshot(meta_pin) -> MetaSnapshot` | build_meta, team_report |
| `interaction.py` | Interaction Matrix (連続値・資源コスト) | `interaction_row(my_set, opp_set) -> dict`, `matrix(candidate, threats) -> dict` | advisor/damage, endgame.duel, dex.calc_stat |
| `concepts.py` | S4 コンセプト系統 (Opus)。authoritative の検証 | `propose_concepts(spec, snapshot, matrix, n) -> list[Concept]` | llm/provider |
| `candidates.py` | S5 多様性保存ビーム + quota + Sonnet 選抜 | `beam(concept, pool, features, width, quotas) -> list[Lineup]`, `distance(a, b)` | role_tagger, PLAY_STYLES |
| `sets.py` | S6 型ライブラリの列挙 → 制約 → 評価関数 → validate-team | `enumerate_sets(species, library, usage_min)`, `filter_constraints(sets, role, thresholds)`, `validate_team(text, fmt) -> bool` | build_meta 整合規則, evaluate_team.build_team_text, Showdown validate-team |
| `adapt.py` | S7/S11 候補ごとの選出モデル適応 (SEARCH-A で収集・微調整)。成果物は registry に **shadow** で登録 | `adapt_selection(candidate, split, battles) -> artifact_id` | collect_selection_data, train_selection |
| `racing.py` | S8/S10 confidence racing (対応差、4状態、ε、上限)。`n_candidates_seen` を必ず記録 | `race(candidates, reference, split, steps, eps, max_battles) -> RacingResult`, `verdict4(diffs, eps) -> state` | check_advisor_player, team_proposal.paired_verdict |
| `ablation.py` | 固定部品との交差評価 (T, P, A) | `ablation_grid(final, reference) -> table` (T1P0A0 / T0P1A0 / T0P0A1 / T1P1A1 …) | racing |
| `stress.py` | 方策集団 (ema/prev/best/anchors)・選出/行動ノイズ・分布外相手・ユーザー遵守モデル | `stress(package, population, noises, ood, user_policies) -> robustness` | checkpoints/*.prev, anchors/, external teams |
| `holdout.py` | S12 封印 holdout。**採用/不採用/同等と ΔWR の CI しか返さない**。詳細は `sealed/` に書き、pipeline は読まない | `final_holdout(package, reference, sealed_id) -> {state, delta, ci}` | racing |
| `loss_stats.py` | S9 対戦記録 → 敗因統計 (SEARCH のみ) | `loss_stats(battles) -> dict` | battle_log |
| `interventions.py` | S9 仮説 (Opus ≤3) → Variant A/B/C → paired → 系譜 | `plan_interventions(stats, hypotheses) -> list[Variant]`, `record_lineage(parent, child, changes)` | racing |
| `battle_log.py` | 対戦記録 (turn 単位) の書き出し・読み込み。`dataset_kind=synthetic` 固定 | `BattleRecorder(run, stage, candidate_id, advisor_id)` | advisor_player (推奨と実行) |
| `package.py` | S13 Final Build Package (team / advisor_policy / selection_patterns / matchup_matrix / evaluation / robustness / lineage / build_report / manifest)。登録は `register_my_team` | `build_package(run) -> Path`, `install(package_id)` (= canary/production への切替は promote 側) | register_my_team |
| `promote.py` | 学習成果物と Package の昇格・ロールバック (人手コマンド) | `promote(id, to)`, `rollback(kind)`, `gate_check(id) -> report` | best_checkpoint |
| `real_eval.py` | 実戦ログからの評価: 実勝率 (CI)、遵守率、助言 vs 実行の反事実評価 | `compliance(logs)`, `real_wr(logs, package_id)`, `counterfactual(logs)` | decision_audit, advice_replay, battle_logger |
| `calibration.py` | 型推定 (SpreadEstimator) と選出モデルの較正 (Brier / log loss) | `belief_calibration(logs)`, `selection_calibration(data)` | ev_infer.top_k, selection_data.npz |
| `transfer.py` | レギュレーション遷移の転移量制御 λ_old (新旧プールの種族重なりから距離を出し、一般モデルの事前重みと環境混合を弱める)。不確実性を CI で明示 | `regulation_distance(old_pool, new_pool)`, `lambda_old(distance)` | REGULATION_CHANGE_RUNBOOK |
| `llm/provider.py` | LLMProvider 抽象 (AgentToolProvider / ClaudeCLIProvider)。入出力とトークン数を `llm/` に保存、authoritative の検証・再試行 | `call(stage, model, payload, schema) -> dict` | audit_subtask (claude -p) |
| `run.py` | オーケストレータ。`--profile`, `--from/--to`, `--llm`, `--holdout` (別コマンド、verdict のみ表示) | — | — |

### 2.2 既存ツールの変更

| 対象 | 変更 |
|---|---|
| `tools/check_advisor_player` | `--pick-policy advisor|teampreview` (既定 advisor = 実助言と同じ `advise_selection`)、`--selection-model <id>`、`--models-dir <id>` (行動方策)、`--opp-split <split_id>:<tier>`、`--battle-log`、`--pick-noise p`、`--action-noise p`、`--user-policy full|high|mixed|expert`、`--seed-set` |
| `champions_agent/env/ranked_teams` | 系統・階層による相手供給 (`split_id` + tier)、外部構築の STRESS 供給 |
| `tools/collect_selection_data` / `train_selection` | `--team-file`、`--opp-split`、`--out <dir>` (候補ごと)、学習データに `dataset_kind=synthetic` と split id を刻む |
| `tools/team_proposal.paired_verdict` | 4状態 + ε の `verdict4` を追加 (旧 API は残す) |
| `battle_logger.py` (実戦) | 各対戦に `source` (organic / recommended / experiment)、`package_id`、`dataset_kind=real`、`data_quality=trusted` を記録。production Package 使用中は自動で recommended |
| `champions_agent/config.py` | `BUILD_*` 定数群 (ε, 上限戦数, racing steps, 系統 Jaccard, 分割比, 適応戦数, ノイズ, 遵守モデルの従属率, λ_old の関数パラメータ, 実戦評価の主指標切替閾値) |
| `scripts/` | `team_build.sh`, `team_build_holdout.sh`, `team_build_promote.sh`, `team_build_status.sh` |

---

## 3. データ契約 (schema_version を全 JSON に持つ)

### 3.1 run ディレクトリ (v3 §6.1 に追加)

```
logs/build_search/runs/<run_id>/
  manifest.json  request.json  meta_snapshot.json  opponent_families.json  interaction_matrix.json
  s04_concepts.json  s05_candidates.json  s06_sets/  lineage.json
  advisors/<candidate_id>/ (registry への参照のみ)
  battles/<stage>/<candidate_id>.jsonl      dataset_kind=synthetic
  evaluation/racing_<stage>.json            n_candidates_seen, 各候補の履歴と 4 状態
  evaluation/ablation.json  evaluation/stress.json
  sealed/holdout_<sealed_id>.json           pipeline は読まない。run 終了後に人が開いてよい
  final/  (Package)
```

### 3.2 Registry

```
logs/registry/index.jsonl   {"id","kind","status","created","run_id","sha256","meta":{...}}
logs/registry/<kind>/<id>/  成果物本体 (コピー、以後変更しない)
```

kind: team / selection_model / rl_checkpoint / meta_snapshot / opponent_split / evaluation / package。
status 遷移: shadow → canary → production → retired。production は kind ごとに 1 つ。

### 3.3 対戦記録 (実戦・合成 共通スキーマ)

v3 §6.3 の項目に加えて: `dataset_kind` (real | synthetic)、`source` (organic | recommended | experiment)、
`data_quality` (trusted | normal | suspicious | synthetic)、`package_id`、`advisor_policy_id` (行動方策 id + 選出モデル id)、
`user_policy` (測定時の遵守モデル)。実戦ログは logs/battles (読み取り専用) に、合成は runs 配下に置き、
学習ローダは `dataset_kind` を検査して混ぜない (混ぜるのは明示フラグのみ)。

### 3.4 LLM 出力

`{"authoritative": {...id/enum/数値...}, "display": {...自然言語...}}`。検証は authoritative のみ。llm/ に prompt 版・モデル・
トークン数・検証結果を保存。

---

## 4. 安全装置 (レビュー3 の 12 項目) の実装対応

| # | 安全装置 | 実装 | 初日の扱い |
|---|---|---|---|
| 1 | 完全 sealed holdout | `families.seal_holdout` で run 開始時に封印。`holdout.py` は採用/不採用/同等と ΔWR の CI しか返さず、個別相手・敗因・matchup・battle log は `sealed/` に書いて pipeline も LLM も読まない。不合格は run ごと廃棄 (次 run は新しい封印)。人が開くのは run 終了後 | **必須** |
| 2 | Team / Pick / Action の自動 ablation | `ablation.py`: (T1,P0,A0) (T0,P1,A0) (T0,P0,A1) (T1,P1,A1) を SELECTION で paired。分解表 (Team / Pick / Action / Interaction) を Package に同梱 | **必須** (最終候補のみ) |
| 3 | 全候補と production の paired 評価 | racing の参照 = production Package (チーム + その選出モデル)。同一相手 × 同一 seed | **必須** |
| 4 | 実ログと合成ログの分離 | `dataset_kind`、置き場所の分離、ローダの検査 | **必須** |
| 5 | organic / recommended の分離 | battle_logger の `source`。production Package 使用中は recommended、候補の試用は experiment | **必須** (単一ユーザーでは検出用) |
| 6 | 較正の測定 | `calibration.py`: 型推定は実戦ログで判明した型に対する Brier / log loss、選出モデルは holdout データ。未較正なら探索は分布のまま (確定しない) | 簡易 (報告のみ、判定に使わない) |
| 7 | 過去方策集団との頑健性 | `stress.py`: ema / prev / best / anchors の各チェックポイントで最終候補を測る | **必須** (最終候補) |
| 8 | shadow → canary → production | registry の status。選出モデル・RL チェックポイント・Package すべて shadow から。canary = 接続テストで N 戦使用 (experiment ラベル) し、実戦 + 合成の paired で確認。昇格は `promote.py` の人手コマンドのみ | **必須** (自動昇格なし) |
| 9 | 自動ロールバック | registry が前 production を保持。`promote --rollback`。canary の合成 paired が Degraded なら自動で canary を取り消す (production は触らない) | 簡易 |
| 10 | 不変バージョニング | registry (sha256) + manifest。「Team 42 の勝率」ではなく「Team 42 + Pick 17 + Action 31 + Meta 27 + Split s3-2026-09-06 + Protocol v1」でのみ記録 | **必須** |
| 11 | 新レギュレーションの転移量制御 | `transfer.py`: 新旧プールの種族重なりから距離 d を出し λ_old = f(d) で一般モデルの事前重み・環境混合を弱める。初期は勝率を CI つきで表示 | 簡易 (M-C で実測して調整) |
| 12 | 遵守率を含む実戦評価 | `real_eval.py`: decision_audit から遵守率、実戦勝率の CI、助言 vs 実行の反事実評価 (探索器で再評価、Q(実行) > Q(助言) の状態群を報告。模倣教師にはしない)。実戦 N が閾値 (既定 200 戦) を超えたら実戦勝率を主指標に切替 | 簡易 (標本が小さい間は併記) |

Winner's curse の扱い: racing は `n_candidates_seen` と比較回数を記録し、探索時の最高値を期待勝率として**報告しない**。
Package の期待勝率は holdout の推定値のみ。SEARCH → SELECTION → HOLDOUT の完全分離で最終候補だけ新しいデータで
ゼロから測る。

ユーザー遵守モデル: 測定は「100% 従う」だけでなく `user_policy` (full / high 90% / mixed 70% / expert 50% を、
一定確率で 2 位の手か相性ヒューリスティックの手を選ぶ近似) でも回し、Package に併記する。目的関数の期待値は
BuildSpec の objective で選ぶ (§6)。

---

## 5. 時間軸プロファイル (非定常環境への対応)

| profile | 内容 | 所要 | 成果物 |
|---|---|---|---|
| fast | S0〜S6 + 汎用選出で racing 100 戦。適応・holdout なし | ≤30 分 | **暫定案** (provisional ラベル。registry には shadow、登録しない) |
| medium | + 生存 ≤4 の選出適応、SELECTION 比較。stress なし | ≤3 時間 | 候補 Package (shadow) |
| full | + 介入実験、ablation、STRESS、封印 holdout | 4〜7 時間 | Package (canary 可) |

レギュレーション開始直後は fast → medium → full を日を跨いで回し、Meta Snapshot の日付を必ず表示する。

---

## 6. 目的関数の選択 (BuildSpec.objective)

| objective | 期待値の取り方 |
|---|---|
| max_wr (既定) | E[P(win | T, π_T, full-follow)] |
| stable | 上に加えて STRESS (方策集団・ノイズ・遵守モデル) の最悪値で並べ替え |
| easy | E_U[P(win | T, π_T, U)] を遵守モデルの混合で取る (mixed 以上の重みを大きく) |
| favorites | 固定枠制約下の max_wr |

---

## 7. 学習ループの権限段階 (初日からの運用規則)

1. **収集はオンライン、更新はオフライン。** 実戦ログは溜めるだけ。production の行動方策・選出モデル・Package は run の出力で
   自動更新しない。
2. run が作る学習成果物 (候補ごとの選出モデル、Package) は registry に **shadow** で入る。
3. canary 昇格 (人手): full profile で holdout が Improved / Equivalent、STRESS で Degraded なし、ablation 表あり、を `gate_check` が確認したもの。
   接続テストで N 戦 (既定 20) を experiment ラベルで使う。
4. production 昇格 (人手): canary の合成 paired が Degraded でなく、実戦の遵守率と勝率が報告されていること。
5. ロールバックは 1 コマンド。RL チェックポイントの昇格 (`best_checkpoint`) も同じ status 遷移に載せる (学習ループ自体は現行どおり、
   production ピンの切替だけを人手にする)。
6. 権限の解放 (自動昇格・自動ロールバック・オンライン更新) は、上記を数 run 運用して問題が出なかった後にユーザー判断で行う。

---

## 8. 実装順序 (全部作る。ただし評価基盤から)

| M | 内容 | 主なテスト | 目安 |
|---|---|---|---|
| M0 | registry / manifest / families (系統化・層化・封印) / battle_log / verdict4 / config / check_advisor_player の新フラグ / ranked_teams の階層供給 / battle_logger の source | families・verdict4・registry の純粋関数、split の互いに素性、記録スキーマ | 1〜2 日 |
| M1 | spec / meta_snapshot / interaction / sets / candidates (ルール部分) / llm provider / concepts | interaction の単調性、sets の合法性、candidates の多様性 quota、provider のモック | 2 日 |
| M2 | adapt / racing / ablation / stress / holdout / loss_stats / interventions / run.py (fast → medium → full) | racing の 4 状態遷移 (合成データ)、ablation 表、holdout が詳細を返さないこと | 2〜3 日 |
| M3 | package / promote / real_eval / calibration / transfer / スキル `/build-team` / scripts | promote の状態遷移とロールバック、real_eval の遵守率、calibration の指標 | 1〜2 日 |
| M4 | M-B データでの通し run (fast → full)。所要とコストの実測、ε・分割比の見直し | — | 1 日 |
| M5 | フロント依頼フォーム + ヘッドレス実行 + Package 表示 | — | 1〜2 日 |

合計 8〜12 セッション日。M0〜M2 は M-B データで検証でき、M-C の環境データ到着 (9/9 以降、上流 Showdown 対応後) に
依存しない。

---

## 9. 判断すべき点

### 目的関数・評価
1. objective の既定を max_wr とし、stable / easy / favorites を BuildSpec で選ぶ形でよいか。
2. 遵守モデル (full / high 90% / mixed 70% / expert 50%) の近似 (一定確率で 2 位の手か相性ヒューリスティック) でよいか。
3. ε = 0.02、racing の上限 2,400 戦、STRESS ノイズ 5% / 10%、分割比 50 / 25 / 20 / 5% (top 200 を系統化)。
4. holdout は「採用 / 不採用 / 同等 + ΔWR の CI」のみ返し、詳細は run 終了後にしか開かない運用でよいか。
5. 実戦勝率を主指標に切り替える標本閾値 (既定 200 戦)。

### 学習・昇格
6. 共同最適化の範囲は選出モデルのみ (行動方策のチーム別適応は将来) でよいか。適応する候補数 ≤8、収集 5,000 戦。
7. canary の戦数 (既定 20 戦の接続テスト) と、昇格を人手コマンドに限定する期間 (「数 run 問題なし」まで)。
8. RL チェックポイントの production ピン切替も同じ status 遷移 (人手) に載せてよいか (学習ループ自体は現行どおり)。
9. 自動ロールバックの範囲 (canary の取り消しのみ自動、production は人手)。

### 運用
10. 4〜7 時間の full run 中に学習を一時停止してよいか (都度確認か、run の既定にするか)。
11. LLM 予算 (Opus ≤3 / Sonnet ≤5 / Haiku 数回)、コンセプト数 8〜20、改修反復 2。
12. 構築記事はユーザーが本文を貼る運用 (外部取得は要承認)。
13. 入口はチャット先行 (M3)、フロントは M5。fast profile の暫定案をフロントで見せる価値があるか。

### 将来枠 (今は決めない)
14. 相手行動モデル・Belief の条件付き分布・active learning・多数ユーザー時の分布補正 (ログ閾値 1,000 / 10,000 戦到達後)。
