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

## 9. 決定事項 (2026-09-06、ユーザー決定。レビュー4 を反映)

作業ブランチ: `feature/team-build` (新機能のため本流から分離)。

### 目的関数・評価

| # | 項目 | 決定 |
|---|---|---|
| 1 | objective | Primary = max E[WR]。`favorites` は **hard constraint**、`easy` は **遵守モデル込み WR**、`stable` は **E[WR] − λ·Risk** (下位 10% の状況の勝率を重視する CVaR 的評価)。UI では 4 種に見えるが内部では意味を区別 |
| 2 | 遵守モデル | **Adherence と Deviation quality を別軸に**。full 100% / high 90% / mixed 70% は非遵守時 human-like policy (相性ヒューリスティック + ノイズ)、expert 50% は非遵守時 **strong search-like policy** (探索器の 2 位以内の手)。遵守確率は定数ではなく P(follow) = f(confidence gap, state, user type) (助言の 1 位と 2 位の差が小さいほど離反しやすい)。初期はパラメトリック擬似ユーザー、実ログが溜まったら離反行動から学習 |
| 3 | ε / racing / split | ε = 0.02 維持。**戦数の絶対上限は置かない**: 必要な精度に達したら終了し、達しなければ Uncertain で終了できる (重要候補は 4,800 / 9,600 へ延長可)。分割は **SEARCH 50 / SELECTION 30 / HOLDOUT 20** (top 200 → 100 / 60 / 40、系統単位の層化)。**STRESS は分割ではなく別生成** (行動ノイズ 5% / 10%、過去チェックポイント、稀な系統、メタ遷移、相手方策の変化)。SEARCH 内部は cross-fitting |
| 4 | holdout の返却 | 探索プロセスには **PASS / FAIL / INCONCLUSIVE** のみ、人には ΔWR と CI まで。相手別・敗因・battle log・弱い系統は run 終了まで封印。終了後に開いても**同じ holdout で同じ候補を修正 → 再評価しない**。holdout 自体を version 化し、同じ holdout に対して開発を続けない |
| 5 | 実戦の主指標化 | 200 戦は **early real-world signal** に留める。Score = w_N · WR_real + (1 − w_N) · WR_synthetic で、有効標本と CI に応じて重みを増やす。実戦を強く効かせる条件: 有効標本 ≥ 1,000 かつ 95% CI 半幅 ≤ 3pt かつ主要系統の最低カバレッジ (二項近似: 200 戦 ±6.9pt、1,000 戦 ±3.1pt、2,400 戦 ±2.0pt) |

### 学習・昇格

| # | 項目 | 決定 |
|---|---|---|
| 6 | 共同最適化の範囲 | **行動方策もチーム条件付きにする**: π_action(s, TeamEmbedding) = Universal Action Policy + Team-specific lightweight adapter (汎用知識を共有し、チーム固有の勝ち筋だけ学ぶ。実装は汎用チェックポイントから自チーム固定で微調整、KL 正則化で忘却を防ぐ)。**適応する候補数は固定しない**: racing で「best である可能性がまだ十分残っている候補」を残す。**適応の停止は収束で決める**: 最低 5,000 戦、改善中は継続、3 評価連続で改善 < ε_train なら停止 (policy loss / value calibration / held-out WR / 方策変化量を監視) |
| 7 | canary | 20 戦は **smoke canary** (crash なし / illegal action なし / 正しいチェックポイント読込 / ログ取得 / latency 正常) と明確化。性能判定には使わない。**昇格条件を明文化**: 独立した full run 3 回 + 全 gate PASS + 重大 regression 0 件 + 人手 approve |
| 8 | production ピンの管理 | **Release bundle** (Team / PickPolicy / ActionPolicy / BeliefModel / OpponentModels / MetaSnapshot / Engine) を 1 つの不変単位にし、candidate → validation → canary → production → retired を **bundle 単位**で遷移。RL チェックポイントの production ピンも同じ status machine に載せる |
| 9 | ロールバック | **Automatic emergency rollback** (hard invariant 違反: crash 率急上昇 / illegal action / model load failure / 助言不能 / latency 暴騰 / 勝率の明白な崩壊 → 即時自動) と **Human performance rollback** (統計的な性能低下 → alert + 人手判断) を分ける |

### 運用

| # | 項目 | 決定 |
|---|---|---|
| 10 | run 中の学習 | **止めない**。run 開始時に RL チェックポイント / 選出モデル / Meta Snapshot / 相手データセット / Engine commit を全部不変にピンし、run はその凍結スナップショットで最後まで走る。学習停止が必要になるなら共有 mutable state があるということなので、そこを直す |
| 11 | LLM 予算 | **回数上限は撤廃**し、**coverage で停止** (concept family coverage / diversity score / new concept yield / duplicate rate。新しい有望系統がほぼ出なくなったら停止)。コンセプト pool は 12〜30。**改修反復 2 回は維持** (dev への過適合防止)。3 回目以降は同じ branch の repair ではなく新しい concept branch |
| 12 | 構築記事 | **記事なしで動く + あれば追加 evidence**。LLM で自然言語 → structured claims (Pokémon id / sets / roles / stated matchups / stated selection patterns) に変換し、ルール/シミュレーションで再検証。記事は真実ではなく候補生成のヒント |
| 13 | 入口 | **チャットとフロントを最初から同じ BuildSpec API に載せる** (チャット = 複雑な制約や曖昧な希望、フロント = favorites / style / objective の構造入力)。fast profile は価値が高いので提供するが、結果の status を **draft / provisional / validated / production** で区別し、fast 結果を学習上の正式な推薦と同じ扱いにしない |
| 14 | 将来枠 | **Opponent Action Model と Belief Model は初期完成形へ昇格** (synthetic prior + 既存の対戦方策集団から始める。Belief = SpreadEstimator + 較正測定、Opponent Action = 方策集団による相手事前分布 → 実ログで更新)。Active learning と多数ユーザー時の分布補正は後回し (1,000〜10,000 戦以降) |

## 10. LLM の不安定性に左右されにくくする対策

現設計で LLM が関与するのは BuildSpec 解析 / コンセプト生成 / 候補選抜の理由 / 敗因仮説 / 記事で、
最終採否・合法性・勝率測定・racing は LLM を使わない。残るリスクは「LLM が間違ったものを採用する」ではなく
**「LLM が良い候補を思いつかない」** (探索漏れ・改修漏れ) なので、候補生成を LLM 一系統に依存させない。

| 対策 | 実装 |
|---|---|
| コンセプト生成を 1 回の出力に依存させない | 複数の独立生成 (seed / prompt framing / モデル系統) + **ルール生成の baseline concepts** の和集合 → 機械で重複除去・構造クラスタリング → distinct families。coverage 指標で停止 (§9-11) |
| LLM が出せない領域は Search が作る | Candidate Sources を並列化: LLM concepts / Rule enumeration / Mutation / Crossover / Historical strong teams (上位実構築・過去 Package) / Novelty search。LLM は探索ヒューリスティックの一つ |
| S8 (改修) も同じ | 機械統計 (loss statistics / matchup shifts / selection failures / action gaps / resource failures) → LLM 仮説 ≤N + **ルール側の mutation 候補** → 全部を実対戦で競わせる。診断が間違っていても測定で落ちるだけ |
| BuildSpec 解析は二段階 | LLM 解析 → schema validation + constraint consistency checker。フィールドごとに **resolved / inferred / unknown** を持ち、LLM の推測とユーザーの明示指定を区別。unknown は既定値かユーザー確認 |
| モデル更新への耐性 | manifest に provider / model exact version / prompt hash / schema version / sampling params / raw output を保存。Provider を変えたら **固定 BuildSpec セットで candidate-generation regression test** (指標は文章の一致ではなく concept diversity / valid candidate rate / downstream WR) |

これにより「LLM が賢いからシステムが強い」ではなく **「LLM は探索を速くするが、正しさは測定系が保証する」** 構造になり、
LLM が一時的に不安定でも「強い候補が多少効率悪く探索される」に留まる。

## 11. 決定に伴う実装項目の追加 (§2 への差分)

| 追加 / 変更 | 内容 |
|---|---|
| `adapt.py` | 選出モデルに加えて **行動方策の adapter** (`adapt_action`、実装済み): 汎用チェックポイントから自チーム固定の自己対戦で微調整、小 LR + target_kl + EMA で逸脱を抑える (SB3 に基底への KL 罰則が無いための代替)、対応比較で採否 |
| `user_model.py` | 遵守モデル (Adherence × Deviation quality、P(follow) = f(confidence gap, state, type)) |
| `racing.py` | 上限撤廃 (精度到達で終了、Uncertain 終了可)、「best の可能性が残る候補」を残す脱落規則 |
| `families.py` | 50 / 30 / 20 の 3 階層 + SEARCH 内 cross-fitting。STRESS は `stress.py` が別生成 |
| `holdout.py` | PASS / FAIL / INCONCLUSIVE、holdout の version 化 |
| `real_eval.py` | w_N による実戦/合成の重みづけ (§9-5) |
| `registry.py` / `promote.py` | bundle 単位の status machine、昇格条件 (3 run + gate + regression 0 + approve)、emergency rollback の invariant 監視 (`invariants.py`) |
| `concepts.py` | 多系統生成 + クラスタリング + coverage 停止。`sources.py` で Rule / Mutation / Crossover / Historical / Novelty の候補源 |
| `articles.py` | 記事 → structured claims → 再検証 |
| `spec.py` | 二段階解析 (resolved / inferred / unknown) |
| `opponent_model.py` / `belief.py` | 方策集団による相手事前分布、SpreadEstimator の較正測定 (初期完成形) |
| プロファイルの status | draft (fast) / provisional (medium) / validated (full) / production |

## 12. 実装状況

| 日付 | マイルストーン | 内容 |
|---|---|---|
| 2026-09-06 | M0 (評価基盤) | `tools/team_build/`: verdict (対応差の 4 状態、次の戦数、実戦重み w_N)、families (Jaccard + メガ軸の系統化、系統単位の層化分割 50/30/20、SEARCH の cross-fit、holdout 封印)、opponents (POOL_PIN 上位 200 の系統化と `opponent_families.json`、対応比較用の決定的な相手列、SequenceTeambuilder)、registry (不変台帳、candidate→validation→canary→production→retired、rollback)、manifest、battle_log (turn 単位の合成記録: 選出・先発・KO 元・交代・状態異常・道具消費・メガ)、user_model (遵守 × 離反の質)。`check_advisor_player` に `--pick-policy advisor` (実助言と同じ選出モデル、分布外は相性順に fallback) / `--selection-model` / `--opp-split FILE:TIER[:FOLD]` / `--battle-log` / `--pick-noise` / `--action-noise` / `--user-policy` / `--models-dir` / `--candidate-id`。実戦ログ (battle_logger) に由来ラベル (source / package_id / dataset_kind / data_quality)。テスト: test_team_build_core / _opponents / _player (CI サブセット)。煙試験: M-B データで 200 構築 → 155 系統 → 100/60/40、SELECTION 階層の相手列で 3 戦、記録の整合 (相手の選出 ⊆ 相手構築の種族) を確認 |
| 2026-09-07 | M1 (探索段 S0〜S6) | `spec` (BuildSpec: objective / favorites=hard constraint / banned / owned は my_team から、provenance resolved/inferred、日本語名の解決、検証)、`meta_snapshot` (最新の健全スナップショット、上位 60 種 + 代表型 + 共起、脅威 30、ローカルメタ、合法種)、`interaction` (lead / switch_in / revenge / setup_stop / speed / hazard / status / uses_mega / resource_cost、メガ石の型はメガ後で評価)、`features` (所持種の被覆・役割・タイプ・共起)、`sets` (代表型 + 単独入替の代替 → 整合 → クローズ → validate-team → 被覆で採点)、`candidates` (コンセプト別ビーム + 距離 + quota の多様性保存)、`concepts` (ルール baseline + LLM 複数 framing → クラスタリング → coverage 停止、authoritative 検証)、`llm/provider` (claude CLI ヘッドレス / モック、全入出力保存、差し戻し再試行)、`run.py` (S0〜S6 一気通貫、profile fast/medium/full、--llm none/headless)。通し実行 (fast、LLM なし): 23 所持 → 7 コンセプト → 25 並び → 8 保持 → 8/8 合法、18 秒。テスト: interaction / sets / candidates / concepts (CI 登録) |
| 2026-09-06 | M1 (2/2) 品質修正 | 通し実行で見えた問題を修正: 被覆の飽和 (max → 対面/後投げ/切り返しの加重平均、チーム被覆は使用率加重の最良 0.7 + 次善 0.3)、持ち物の攻撃的偏り (代表型優先 REP_MARGIN、こだわり系 + 積み/設置/回復、カゴ + ねむる無し、攻撃的持ち物 + 耐久配分を除外、メガ石の代替は代表型がメガ石のときだけ)、コンセプトの相方重複 (framing 別 baseline: favorites / mega / offense / bulky / speed / anti_meta)、候補の多様性 (系統ごとの最良を先に、距離 0.5)、メガ枠 1 体 (ビームで hard、型決定で enforce_single_mega、クローズ差し替え先からメガ石を除外) |
| 2026-09-06 | M2/M3 (測定段 S7〜S13、成果物、安全装置) | `racing` (confidence racing、対応差 4 状態、脱落/継続、n_candidates_seen)、`adapt` (候補ごとの選出モデル: SEARCH-A 収集 → 汎用起点の微調整 → 収束停止 → registry candidate)、`holdout` (封印、PASS/FAIL/INCONCLUSIVE、usage ログで再利用を警告)、`stress` (行動/選出ノイズ、遵守モデル、prev/best チェックポイント集団)、`ablation` (Team/Pick/Action 分解)、`loss_stats` (機械の敗因統計)、`interventions` (仮説 ≤3 + ルール mutation → Variant A/B/C、系譜)、`sources` (historical/mutation/crossover/novelty)、`articles` (記事 → structured claims)、`package` (Final Build Package + manifest)、`report` (記事は表示専用)、`promote` (人手の昇格/ロールバック/install/experiment ラベル)、`real_eval` (実戦の CI・遵守率・w_N 重み)、`transfer` (λ_old)、`calibration` (Brier/信頼性表)、`invariants` (緊急ロールバック条件)、`pipeline` + `run.py --stages measure/all`、`/build-team` スキル、scripts/team_build*.sh。煙試験 (極小戦数、33 分): S8a → S7 適応 (2 候補) → S8b → S10 → S11 → 封印 holdout (INCONCLUSIVE, n=40) → STRESS 8 variant → ablation → Package (registry candidate)。参照チーム本文の種族ID未反映 (Rotom→rotomwash) を修正、対戦前に validate-team で不正チームを即時失敗に。テスト 9 ファイル追加 (CI 登録) |
| 2026-09-06 | M5 (入口) + 実地確認 | フロントの依頼フォーム (固定/除外/目的/スタイル/規模/測定まで/LLM) と server の `run_team_build` (別プロセス、run.log の進捗配信、summary 要約、status)。結果は draft / provisional / validated で区別、採用は promote (人手)。測定段の規模をプロファイルで切替 (fast: race 300・STRESS 100 / medium: 600・150 / full: 既定)。`--article-file` で記事の structured claims を軸候補に。LLMProvider を claude CLI (Haiku) で実地確認 (authoritative 省略時の寛容な正規化を追加)。Opus のコンセプト生成 (S4) を実 run で確認: 1 呼び出し約 43 秒、入力約 43k トークン (うち CLI の既定 system prompt 約 29k をキャッシュ)、出力約 2k、round 0 で +8 系統 / round 1 で +5 系統、authoritative は 1 回目で検証通過 |
| 2026-09-06 | 残課題の解消 | (1) **行動方策の adapter** (S11b、full プロファイル既定 / `--adapt-action on`): 学習環境に自チーム固定 (`make_training_env(own_team_text)`, `smoke_train --own-team-file`) と更新幅制限 (`TRAIN_TARGET_KL`) を追加し、`adapt.adapt_action` が基底ピンを候補専用 dir にコピー → CHAMPIONS_MODELS_DIR をそこへ向けて chunk (既定 100k step) ごとに微調整 (TRAIN_LR 3e-5、target_kl 0.02、EMA を推論に使う) → 同一相手列で adapter vs 基底を対応比較 → improved のときだけ採用 (registry rl_checkpoint candidate)。SB3 に基底方策への KL 罰則は無いため、小 LR + target_kl + EMA で代替 (意図的な逸脱)。煙試験: 2048 step + 20 戦で 281 秒、機構を確認。ablation の A1 は adapter (採用時) か前世代。(2) **CLI の既定 system prompt**: `--bare` は OAuth が外れて不可。全ツールを `--disallowedTools` にすると約 29k → 約 17.5k トークン/呼び出し (実測)。Provider に組み込み。(3) **ε・分割比の見直し**: `tools/team_build/review_run.py --run-id <id>` が racing の終了状態・上限到達数・CI 半幅・適応の停止理由・系統分割・LLM トークン・段ごとの所要を要約し、見直しの推奨を出す (判定はしない)。実 run m3_llm (medium) の完了後に適用 |
| 2026-09-06 | M4 実 run (チャット構築テスト chat_0906) | 条件: 除外 17 種 (ユーザー指定 14 + 未所持 3)、所持 47、目的 max_wr、medium、Opus あり (6 回、出力 45.8k トークン、探索 S0〜S6 約 24 分)。58 並び (全合法) の上位 4 を測定: **S8a スクリーニング (選出モデル未適応、同一相手列) で全候補が参照 v3.1 に劣り degraded** (100 戦: #0 0.56 / #1 0.56 vs 参照 0.82、300 戦: #2 0.715 / #3 0.675 vs 参照 0.865 の 200 戦分) → S10 contenders なし = `no_contender` で終了 (27 分)。**探索段のスコア順位は助言操縦の実測勝率と合っておらず、参照 (現行チーム) を上回る候補を作れていない**。review_run の指摘: 終了時 CI 半幅 0.094 (ε の 2 倍超、脱落判定なので実害なし)。副産物の修正: `--reuse-concepts` 時に系統コアへ残った除外種の混入 (1 並び) → run.py でコアから除去。次の改善候補: `--repairs` で S9 介入 (敗因統計 → 部分入替) を回す、現行チームの構成を探索の種 (historical) に含める、探索スコアを実測勝率で較正する |
| 2026-09-07 | 評価プロトコルの見直し (レビュー 2 件) と選出方策 ablation | 決定: **選出適応前に代理スコアや未適応の測定で候補を落とさない**。racing の段階判定に段数 K に応じた z (Pocock、`BUILD_RACE_LOOK_CORRECTION`) を導入。`tools/team_build/pick_ablation.py` で #0/#2/#3/参照 v3.1 を teampreview / generic (汎用基底 `selection_model_general.pt` を強制) / fresh (候補専用適応、収集 SEARCH fold 0) / production (参照に強制) の 4 条件 × 300 戦 (SEARCH fold 1、同一相手列) で測定 (適応 4 本 9,000〜15,000 戦で収束、計 1 時間 40 分 + 測定 1 時間)。**結果**: 適応の上げ幅はチームで大きく異なる。#3 は fresh で +0.133 [+0.066, +0.201] (teampreview 比)、generic 比 +0.263、一方 #0 は fresh が generic より −0.070 で teampreview 並み。generic は #3 を −0.130 悪化させ、screening 方策として不適 (順位反転が CI 超えで 1 対)。参照は teampreview 0.850 ≈ production 0.853 > generic 0.820 ≈ fresh 0.803 (選出方策に鈍感、B_incumbent +0.050 は誤差の範囲)。**S8a (teampreview) で degraded だった #3 は、適応同士の比較では参照と同等 (+0.013 uncertain)、実運用比較 (候補 fresh vs 参照 production) でも −0.037 uncertain** で、未適応の測定で落としてはいけない候補だった。判定: Case C (適応してから screening)。次: 適応の learning curve (1,000〜5,000 戦のチェックポイント) で screening 用の cheap adaptation の規模を決める。事実: 参照 v3.1 は production 選出モデルの分布外 (ドドゲザン追加後に未学習) で、実助言の選出は teampreview に落ちている |
| 2026-09-07 | learning curve と新 screening プロトコル | `tools/team_build/adapt_curve.py` (adapt_selection の keep_checkpoints) で #0/#2/#3 の N=1000/2000/3000/5000 を fold 1 で各 300 戦: #0 0.640/0.597/0.660/0.613 (収束 0.623)、#2 0.713/0.767/0.810/0.803 (0.763)、#3 0.767/0.747/0.647/0.780 (0.817)。**N=1000 の順位 (#3 > #2 > #0) は収束後と一致 (反転 0)** が、checkpoint の質は N でぶれる (#3 の n3000 は収束比 −0.170 degraded、#2 は n3000/n5000 が収束より高い) → S7 の val_mse 停止は勝率を保証しない。N=1000 の Δ vs 参照 fresh は #0 −0.16 / #2 −0.09 / #3 −0.04 で、ε=0.02 のままだと #2 を落とす (収束後は同等)。**決定 (Case C)**: (1) S8a = 全候補 + 参照を cheap adaptation (`BUILD_SCREEN_ADAPT_BATTLES`=1000) してから同一相手列で screening、脱落は伸び代 margin 込み (CI 上端 + `BUILD_SCREEN_MARGIN`=0.05 < −ε、段階 100/300)。代理スコアでは絞らず `max_candidates` は S7 で収束まで適応する数 (生存の Δ 上位)。(2) S8b は候補 × 選出方策 variant (fresh / generic) を腕にして候補ごとに測定で選ぶ (`choose_variants`、S11 は fresh のときだけ)。(3) S5 に現行チーム (登録 6 体) を exploitation pool として必ず入れる: 較正点 + 近傍 (1 枠入替、`BUILD_INCUMBENT_NEIGHBORS`=4)、S6 で登録個体は登録の型に差し替え・持ち物は登録優先・メガ枠は登録に合わせる。(4) review_run に代理スコアの順位予測力 (Spearman / Precision@k / 実測最良の代理順位 / Regret@k。chat_0906: n=4 で Spearman −0.74、実測最良は代理 3 位)。(5) racing の段別 z (Pocock)。煙試験: 探索 fast で 18/18 合法 (現行 + 近傍 4 を含む)、測定は m1_smoke5 (極小戦数) で通し確認。**未実施**: chat_0906 条件での本 run (58 候補 + 現行枝: cheap adaptation 約 2 時間 + screening 約 2 時間 + full 適応 ≤8)。制約: margin は 3 候補の実測からの見積もりで、8〜12 候補への ablation 拡張で更新する |
| 2026-09-07 | Team × PickVariant と検証つき checkpoint 選択 (ユーザー決定) | 決定: 58 候補の本 run は待つ / ablation を 8〜12 候補に拡張 / v3.1 の fresh 昇格は見送り。本 run 前の 2 変更を実装 (6dc521b5, f16e79b1): (1) **S8a/S8b を Team × PickVariant 評価** に。S8a は teampreview / generic / cheap、S8b は teampreview / generic / fresh を腕にし、チームの実力 = 最善 variant の Δ、脱落は全 variant が degraded のときだけ。参照 (現行チーム) も variant の最善 (`s08a_reference`、煙試験では teampreview 0.90 > generic 0.825 > cheap 0.70) を S8a〜S12 の参照にする。(2) **S7 の checkpoint 選択を val_mse から独立 fold の実測勝率へ**: SEARCH を 3 fold (A 適応の収集 / B 評価 / V 検証、`BUILD_FOLD_*`) にし、適応で残した checkpoint (最初と最後を含め等間隔 `BUILD_ADAPT_VALIDATE_MAX_CKPTS`=4 点) を fold V で `BUILD_ADAPT_VALIDATE_N`=200 戦ずつ測って勝率最大 (同率なら学習の進んだ方) を採る。S11 (SEARCH+SELECTION 再学習) は検証 fold が学習に入るため既定 off。S7 の適応は数チーム並列。run.py: `--strata` (S5 順位で層化)、`--candidates`、`--include-incumbent`、`--stop-after s08a|s08b`、`--s08b-seed-offset`、`--s11`、`--validate-n/--validate-max`。`tools/team_build/screen_margin.py` が S8a/S8b から uplift 分布・margin ごとの誤脱落表・順位反転を出す。煙試験 m1_smoke6 (fast、極小戦数、探索〜S13、50 分): 現行 + 近傍 + 上位 2 の 4 チーム × 3 variant、参照 variant = teampreview、S7 検証 fold=2、S8b は両チームとも fresh、勝者 = 現行 (fresh)、holdout INCONCLUSIVE n=40。**ablation 拡張 = run chat_0907** (chat_0906 の概念を再利用、seed 20260907、68 並び全合法、現行 v3.1 の S5 スコア 0.91 は 68 中最下位級 = 代理スコアの較正点): strata 1,2,3,5,8,13,21,34 + 現行 + 近傍 1 の 10 チームを S8a/S8b とも 1 段 300 戦 (脱落なし、S8b は S8a と同一相手列) で測る |
| 2026-09-09 | ablation 拡張の結果 (chat_0907) と改善案の測定起動 | S7 は 8 チーム完了後に 2 チームの収集が Showdown の空理由 "team rejected" で無応答になり停止 (9/7 14:48) → 収集 timeout の戦数比例化・seed を変えた再試行・候補単位の失敗・`--resume` (4ac113fb) で 9/9 0:35 再開。S8b は resume で参照の本文が再生成され、当時不正だった登録 (ラグラージ) に置き換わって Δ が全腕 0 に → 参照は resume で再生成しない (3624faec) として 11:38 再開、11:50 完了 (候補 30 腕の JSON は再利用)。**結果** (10 チーム × 3 variant、S8a/S8b とも 300 戦・同一相手列、参照 = v3.1 の cheap 変種 0.897 / 0.900): **S8b で参照を上回る腕なし**。最善は L22_C059 (デルフォックス/ガブリアス/ドドゲザン/ミミッキュ/ウォッシュロトム/オオニューラ) teampreview −0.043 uncertain、他 29 腕は degraded (−0.07〜−0.35)。現行 v3.1 と同一の並び L00_INC は teampreview 0.817 / 0.790、cheap 0.827、fresh 0.797 で、参照の cheap モデル (0.873〜0.900、計 900 戦) より 0.05〜0.10 低い = **同じチームでも適応の引き (選出モデルの当たり外れ) と 300 戦の誤差で ±0.05〜0.10 ぶれる** (候補間の差と同程度)。screen_margin: full 適応 (S7 収束 + 検証つき checkpoint) の uplift は cheap (1,000 戦) 比で平均 −0.034 / q50 −0.053 / q90 0.0 / 最大 +0.007 → **S7 は cheap を上回らない**。誤脱落は margin 0 でも 0、順位反転 5 対は全て誤差の範囲。代理スコア (S5) は前回同様に実測と合わない (v3.1 は S5 最下位級のまま実測 1 位)。review_run: S8b の CI 半幅 0.061 は ε の 2 倍超 (1 段 300 戦の設計どおり)。**示唆 (判断はユーザー)**: S7 (8,000〜16,000 戦/候補) を省き、cheap 適応を複数回引いて fold V で最善を採る方が安く、適応の引きのぶれも抑えられる。改善案の測定 `improve_20260909_1155` (現行 メタグロス/ミミッキュ/アシレーヌ/ムクホーク/ラグラージ/ドドゲザン + 近傍 3、medium、セッションの脅威重み 18 種、`tools/party_improvements --last 12 --measure`) を 11:55 に起動。結果は `--report improve_20260909_1155` |
| 2026-09-09 | コンセプト規則 (BuildSpec.rules、hard constraint) | ユーザー依頼「サイコフィールド + 先制技に弱いエースのパーティを除外リストにないポケモンのみで」を機械的な制約にした。`tools/team_build/rules.py` の RULES に `psychic_terrain_priority_ace`: 設置役 = 技 psychicterrain を覚える (champions mod の learnsets.ts を `tools/team_build/learnsets.py` で読む。使用率データには無い技) か特性 psychicsurge、エース = 接地 (ひこう/ふゆう/ふうせん無し) で 素早さ種族値 ≥ `BUILD_RULE_ACE_MIN_SPE`=100 かつ 防御 ≤ `BUILD_RULE_ACE_MAX_DEF`=70 (メガ石を持つ型はメガ後の値)、設置役とエースは別個体。S3 で判定材料 (代表型 + 図鑑 + learnset) を `s03_rules.json` に保存、S4 で (設置役 × エース) の軸を系統の先頭に追加 (`BUILD_RULE_MAX_CORES`=24) し LLM にも rules/constraints として渡す、S5 で規則を満たさない並びを固定枠と同様に落とす (現行チーム枝は入れない)、S6 で設置役の型に技を差し込む (積み技でない変化技 → 変化技 → 末尾の順に差し替え、こだわり持ち物は alt:item に替える、validate-team で合法性を確認)。CLI `--rules`、spec の `rules`。煙試験 rule_smoke (chat_0906 の条件 + 規則、fast、LLM なし): 設置役 デルフォックス/クエスパトラ/サーナイト、エース クエスパトラ/サーナイト/マスカーニャ/ライチュウ/オオニューラ/ウルガモス、軸 +15、192 → 109 並び → 17 候補すべて合法。テスト tests/test_team_build_rules (CI 登録) |
| 2026-09-09 | 改善案の測定 improve_20260909_1155 の結果 | 現行 (メタグロス/ミミッキュ/アシレーヌ/ムクホーク/ラグラージ/ドドゲザン) + 近傍 3、medium、11:55〜18:21 (6.5 時間)。S8a: 参照 variant = teampreview 0.737、4/4 生存。S7: 検証 fold の勝率で checkpoint を選択 (checkpoint 間のぶれ 0.05〜0.07)。S8b (600 戦): L01 メタグロス→キラフロル generic +0.053 [−0.002, +0.109]、L02 ラグラージ→ガブリアス teampreview +0.020、現行 fresh +0.002、L03 ムクホーク→キラフロル −0.022 degraded。S10 (SELECTION、600 戦): L01 +0.023 / L02 −0.005 / 現行 −0.008 (全て uncertain) → 勝者 L01。**封印 holdout: INCONCLUSIVE Δ +0.025 [−0.029, +0.079] n=600**。stress は 4 条件とも候補が上 (user_mixed +0.067 / user_expert +0.093 / policy_prev +0.067 / policy_best +0.147 improved)。ablation team −0.013 / pick −0.007 / action +0.020 / interaction 0。結論: メタグロス→キラフロルは「現行より悪くはない、良いとも言い切れない」。6 体の型つきレポート `tools/party_improvements --report` → logs/battle_analysis/improvements_measured_improve_20260909_1155.md。続けて規則つき構築 rule_0909 の測定 (8 候補: 設置役 × エースの対で散らす、S7 は上位 4、medium) を 18:23 に起動 |
| 2026-09-10 | 規則つき構築 rule_0909 の結果 (holdout PASS) | 探索 (9/9 16:41〜16:49、Opus 3 ラウンド 出力 28k トークン、提案は全て規則の軸に吸収、系統 31、118 → 24 候補 全合法) → 測定 8 候補 (S5 スコアは実測と相関しないので設置役 × エースの対で散らした)、medium、18:21〜02:03 (7.7 時間)。参照 = 登録チーム (メタグロス/ミミッキュ/アシレーヌ/ムクホーク/ラグラージ/ドドゲザン) の cheap 変種 0.737。S8a: 脱落 2 (L01 クエスパトラ+マスカーニャ、L09 サーナイト+ライチュウ)、S7 は Δ 上位 4。S8b (600 戦): L21 teampreview +0.150 improved (100 戦で確定判定)、L14 fresh +0.098 improved、L07 teampreview +0.093 improved、L00 +0.035。S10 (SELECTION、600 戦): L07 +0.078 improved、L14 +0.067 uncertain、L21 −0.043 degraded (**S8b の improved が別分割で反転**)、L00 −0.090。**勝者 L07_C026 = マフォクシー (メガ、サイコフィールドを差し込み) / ガブリアス / ドドゲザン / ミミッキュ / ウォッシュロトム / オオニューラ (スカーフ、エース)、選出方策 teampreview。封印 holdout PASS Δ +0.17 [+0.07, +0.27] n=100 (1 段目で確定)**。stress 10 条件すべて候補が上 (current +0.18 improved、user_expert +0.147 improved、他は uncertain、policy_prev +0.007)、ablation team +0.007 / pick +0.080 / action +0.013 (全て uncertain)。Package package-3cf32b21831639f2 (registry candidate)。注意: 勝者の軸は historical 系統 C026 (マフォクシー/ガブリアス/ミミッキュ) で規則を満たした並びであり、同居する先制技 (ふいうち・かげうち・ねこだまし) はフィールド下で自分も使えない (測定はそれ込み)。学習ループは 02:05 に再開 (`scripts/start_training.sh`、実戦バンクの混合が有効) |
| 2026-09-10 | レビュー対応 1: 持ち物の決め方 (ユーザー指摘: ドドゲザン いのちのたま / ミミッキュ ピントレンズ / オオニューラ スカーフ) | 原因 (実測): 型ライブラリは被覆だけで代替を採り (いのちのたま 0.461 vs くろいメガネ 0.400、使用率 8.7% vs 45.2%。スカーフ 0.593 vs しろいハーブ 0.516、6.2% vs 37.9%)、アイテムクローズは並び順で先の個体が残す (五十音順のドドゲザンがいのちのたまを取り、ミミッキュ 81% が 4% のピントレンズへ)。実効素早さはスカーフを見て かるわざ/かそく を見ない。対応: (1) `sets.order_candidates`: 代替の採点 = 被覆 − `BUILD_SET_USAGE_WEIGHT`(0.3) × 代表型との使用率差 (SetCandidate.usage_gap/adj)、代表型は margin 0.05 以上上回られない限り先頭 (両例とも代表型が残る)。(2) `sets.resolve_item_clause(usage_pct, prefer)`: 残す個体 = prefer の優先度 (規則のエース 2 > 設置役/登録個体 1) > 代表型 > その種でのその持ち物の使用率 > 並び順 (usage_pct 無しは従来動作)。(3) 規則: `rules.ensure_ace_item` がエースに `BUILD_RULE_ACE_ITEMS`(きあいのタスキ) を使用率 `BUILD_RULE_ACE_ITEM_MIN_PCT`(10%) 以上なら付け、メガ石は替えない。S6 の順序を 型 → メガ枠 → 規則 (技・持ち物) → クローズ に変更。(4) `features.boost_multiplier` + 役割 `speed_boost` (自己加速後に上を取れる脅威の割合。`BUILD_SPEED_BOOST_ABILITIES` かそく 1.5 / かるわざ 2.0 (消費アイテム `BUILD_CONSUMABLE_ITEMS` のとき)、`BUILD_SPEED_SETUP_MOVES`)。煙試験 rule_smoke2: 勝者の並びは マフォクシー (メガ) / ガブリアス スカーフ (タスキをエースに譲る) / ドドゲザン くろいメガネ / ミミッキュ いのちのたま / ロトム たべのこし / オオニューラ きあいのタスキ。テスト +4 (sets 2、rules 2) |
| 2026-09-10 | レビュー対応 2〜4 と助言エンジンのサイコフィールド対応 | (2) エースの定義を「先手を取る手段」の型に改訂 (`rules.ace_types`): 速攻型 = 先手率 (S3 roles.speed、加速前) ≥ `BUILD_RULE_ACE_FAST_SPEED_SHARE`(0.75) かつ 防御 (メガ後) ≤ `BUILD_RULE_ACE_FAST_MAX_DEF`(75)、自己加速型 = 加速手段 (かそく/かるわざ+消費アイテム/加速技) を持ち 加速後の先手率 (speed_boost) ≥ 0.8 かつ 耐久 (roles.bulk) ≥ 0.25、トリックルーム型 = 先手率 ≤ 0.3 で同じ 6 体に TR 使い (代表型に trickroom)。接地は共通、設置役とエースは別個体。**閾値はより良い値が見つかれば変更してよい (ユーザー合意)**。プール 47 では 設置役 マフォクシー/クエスパトラ/サーナイト、速攻型 マフォクシー/マスカーニャ/ライチュウ/イダイトウ、自己加速型 クエスパトラ/オオニューラ/バシャーモ/ウルガモス/ギャラドス/ポットデス/ペロリーム、TR 使いは代表型に無し (TR 型は成立せず)。軸は 3 体 (設置役 + エース + TR 使い) も可。(3) 並びの点に穴の罰則 (`candidates.worst_hole`: (`BUILD_LINEUP_HOLE_THRESHOLD` 0.4 − 最良被覆)+ × 脅威の重み × `BUILD_LINEUP_HOLE_WEIGHT` 0.5) と規則の対の相補性 (`rules.pair_complementarity`: 共通の苦手を他の 4 体が `BUILD_RULE_PAIR_COVER` 0.6 以上で見ている割合。見ていない分 × `BUILD_RULE_PAIR_WEIGHT` 0.5 を減点し、記事に 共通の苦手 / 見ている個体 / 未対策 / エースの止め手を設置役が見る割合 を書く)。(4) racing: improved/equivalent は `BUILD_RACE_MIN_TERMINAL_N`(300) 戦未満では確定させず測り続ける (degraded の早期脱落は維持)、封印 holdout は上限まで回す (`BUILD_HOLDOUT_RUN_TO_MAX`)、再現性の門 (`pipeline.repro_gate_ok`: 勝者の S8b と S10 の Δ が両方 ≥ 0 でなければ `not_reproducible` で終了、`BUILD_REPRO_GATE`)。助言エンジン: サイコフィールド中は接地した相手への先制技 (優先度 > 0、相手対象) を不発として扱う (`search.simulate_turn` で不発、engine は相手の先制技を脅威に数えず、自分の先制技を `PRIORITY_BLOCKED_SCORE` に固定)。テスト +7 (rules 2、candidates 1、racing 1、pipeline 1、search 1、advisor 1)。煙試験 rule_smoke3 (fast、LLM なし): 26/26 合法、対の相補性は概ね 1.0、穴の項は最大 0.017。本 run `rule_0910` (medium、Opus) を起動、測定は学習を止めて 8 候補で行う |
| 2026-09-10 | エースの火力・技範囲の条件 (ユーザー指摘: ペロリームが自己加速型に入っていた) | 代表型が補助型 (ねばねばネット/あくび/がむしゃら/マジカルシャイン + かるわざ) でも「加速手段 + 加速後の先手率 + 耐久」だけで自己加速型に入っていた。エース共通の門 `rules.offensive` を追加: 使う側の攻撃種族値 (代表型の攻撃技が物理なら攻撃、特殊なら特攻、メガ後) ≥ `BUILD_RULE_ACE_MIN_OFFENSE`(100)、攻撃技 (威力 > 0) の本数 ≥ `BUILD_RULE_ACE_MIN_ATTACK_MOVES`(2)、攻撃技のタイプ数 ≥ `BUILD_RULE_ACE_MIN_ATTACK_TYPES`(2)、脅威への平均被覆 ≥ `BUILD_RULE_ACE_MIN_COVERAGE`(0.3)。実測 (rule_0910 のプール): ペロリーム 85 / 1 本 / 1 タイプ / 0.20、ポットデス 134 / 1 本 (シェルスマッシュ+バトンタッチ型) / 0.20、クエスパトラ 101 / 1 本 (バトンタッチ型) / 0.16 は外れ、メガバシャーモ 160 / 2 / 2 / 0.55、メガライチュウY 160 / 2 / 2 / 0.32、メガマフォクシー 159 / 3 / 3 / 0.56、メガギャラドス 155 / 3 / 3 / 0.46、ウルガモス 135 / 2 / 2 / 0.44、オオニューラ 130 / 4 / 4 / 0.52、イダイトウ 112 / 4 / 2 / 0.54、マスカーニャ 110 / 4 / 4 / 0.31 が残る。クエスパトラは設置役としては残る (代表型がバトンタッチ型なのは代表型のみで探索する現方針の制約で、取り逃がしの量を観測してから見直す: ユーザー決定)。走行中の rule_0910 の 8 候補のうち L11 (クエスパトラ+ポットデス) と L34 (マフォクシー+クエスパトラ) は改訂後の定義では規則外 (結果は参考値として扱う) |
| 2026-09-10 | エース判定を型ライブラリの代替まで見る (ユーザー決定: ポットデスはエースになり得る) | 原因: 代表型は「技枠ごとに最も使われる技」の貼り合わせで、ポットデスは 4 枠目がアシストパワー (51%) ではなくバトンタッチ (78%) になり攻撃技 1 本の型になっていた (クエスパトラも同じ)。対応: `rules.pick_ace_set` が 代表型 + 単独入替の代替 (使用率 5% 以上、adj 降順) のうち火力・技範囲・被覆の門を通る最初の型で判定し、代表型と違えば `ace_sets` に残して S6 の `rules.ensure_ace_set` がエースの型 (技/性格/配分/特性。持ち物は今のまま) をその型に差し替える。被覆は「1 回積んだ後」も計算する (`rules.setup_stages`: 積み技の能力ランク + 特性の加速 1.5 → +1 / 2.0 → +2 を MonView.boosts に入れて Interaction Matrix を引き直し、積む前との大きい方)。自己加速型・積み型は積む前の被覆が低いのが普通で、これが無いとポットデス (0.16) もクエスパトラ (0.17) も門を通らなかった。実測 (rule_smoke5): ポットデス = からをやぶる/バトンタッチ/シャドーボール/アシストパワー (使用率差 0.10)、クエスパトラ = ルミナコリジョン/まもる/マジカルシャイン/めいそう (差 0.12)、ウルガモス = ちょうのまい/ギガドレイン/ほのおのまい/むしのさざめき (差 0.006) が代替型で判定され、エースは 速攻型 マフォクシー/マスカーニャ/ライチュウ/イダイトウ、自己加速型 クエスパトラ/オオニューラ/バシャーモ/ウルガモス/ギャラドス/ポットデス。走行中の rule_0910 の L11/L34 は代表型 (バトンタッチ型) のままなので、この定義での型とは違う (参考値) |
| 2026-09-10 | 技 + ポケモンの指定と型の指定 (ユーザー要望: 使用率に振り回されないため) | BuildSpec に `required_moves` ({species_id: [move_id]}、CLI `--moves "マフォクシー:サイコフィールド, ポットデス:からをやぶる/アシストパワー"`、日本語可) と `custom_sets` ({species_id: 型}、CLI `--sets-file` Showdown 本文、EVs は能力ポイント) を追加。`sets.inject_move` (差し込み枠: 積み技でない変化技 → 変化技 → 末尾、4 本未満なら足す。規則の設置役の技差し込みもこれに統一) / `apply_required_moves` / `finalize_candidates` (全候補に差し込んで同じ型はまとめる) / `parse_set_text` / `base_set`。指定の型がある種は使用率データを見ずその型だけを使う (新シーズンで使用率が無い種にも使える)。必須技はその種を使う全ての型 (S3 の特徴、規則の判定、S6) に入る。並びに入れることまでは強制しない (固定枠と併用)。`--spec` 併用時も `--rules/--moves/--sets-file` を spec に足す。テスト +2 (sets) + spec の解析/検証。煙試験 rule_smoke6: マフォクシー 全型にサイコフィールド、ポットデス からをやぶる+アシストパワー、26/26 合法 |
| 2026-09-10 | 規則つき構築の測り直し rule_0910 の結果 (INCONCLUSIVE) | 8 候補 (改訂前のエース定義で選定)、medium、12:11〜21:26 (9.3 時間、20:0x 以降は学習と並走)。参照 = 登録チームの cheap 変種 0.807。S8a: 8/8 生存、S7 は L13/L26/L11/L34。S8b (600 戦、improved は 300 戦未満で確定しない): L26 fresh +0.055 uncertain が最善、L11 (クエスパトラ+ポットデス、代表型) 脱落。S10 (600 戦): L26 +0.040 / L34 +0.023 / L13 +0.008 (全て uncertain) → 勝者 L26_C003 (イダイトウ スカーフ / マフォクシー メガ+サイコフィールド / ガブリアス オボン / ドドゲザン くろいメガネ / アローラキュウコン ひかりのねんど / オオニューラ タスキ、fresh)、再現性の門 OK (S8b +0.055 / S10 +0.040)。**封印 holdout (上限 600 戦まで): Δ +0.065 [+0.010, +0.120] → INCONCLUSIVE** (下端が ε 0.02 未満)。stress 10 条件: 候補が上 7 / 下 3、全て uncertain (action_noise_10 −0.093、user_mixed −0.020、policy_best −0.007)。ablation team +0.093 / pick −0.027 / action −0.040。rule_0909 の PASS (+0.17、100 戦確定) と比べ、判定規則の改訂で「参照より少し良いが確定はできない」に落ち着いた。持ち物は今回の直しどおり (ドドゲザン くろいメガネ、オオニューラ タスキ)。Package package-ce8b7f36e029ffa9 (registry candidate) |
| 2026-09-11 | learnset からの型生成 (ユーザー決定: 使用率が無いシーズン序盤の補完 + 使用率が低くても有用な技を拾う) | `tools/team_build/gen_sets.py`。全探索はしない: (1) 技プールの刈り込み = 攻撃技はタイプ × 分類ごとに「威力 × 命中 × STAB × 分類の適合」の上位 `BUILD_GEN_ATTACKS_PER_TYPE` 本 (反動/2 ターン技は `BUILD_GEN_AVOID_MOVES`)、補助技は役割辞書 (積み/先制/交代/設置/除去/状態異常/回復/まもる/フィールド/壁) に載るものだけ。(2) テンプレート `BUILD_GEN_TEMPLATES` (攻撃 3 + 積み、攻撃 3 + まもる/先制/交代/フィールド、攻撃 2 + 積み + まもる、攻撃 4、攻撃 2 + 設置 + 状態異常 …) ごとに 1 型、攻撃技は **想定する相手 (脅威の重み。`--targets` で特定の相手を最大重み) への与ダメージ割合の増分が最大の技から貪欲に**。(3) 型の定型 `BUILD_GEN_ARCHETYPES` (速攻/耐久/壁 × 物理/特殊: 配分は合計 66 ポイント、持ち物は M-C に無いものを mod の items.ts で除外)、性格は「+Spe で上を取れる相手 (重み) が `BUILD_GEN_SPEED_GAIN_MIN` 以上増えるか」、壁型は脅威からの物理/特殊の被ダメの大きい側に振る、特性は `BUILD_GEN_ABILITY_PRIORITY` (サイコメイカー等) を優先、メガ石があれば先頭テンプレートのメガ変種。種ごと `BUILD_GEN_MAX_SETS`(6) 型。合流: 代表型が無い種は生成型で補完 (S3 features / 規則の判定 / S6、アイテムクローズの差し替え先も生成側)、代表型がある種でも候補に加え、罰則 = 代表型に無い技の使用率差の平均 × `BUILD_SET_USAGE_WEIGHT`。`BUILD_GEN_SETS` = auto/missing/off。実測 (mc_smoke2、M-C 新種 7 を所持に追加、fast): 補完 = セグレイブ/エースバーン/グソクムシャ/イエッサン/パーモット/ゴリランダー/ボーマンダ、イエッサンが設置役 (サイコメイカー) に入り、23/23 合法。生成例: イエッサン サイコメイカー おくびょう C32 S32 サイコキネシス/マジカルシャイン/ミストバースト/サイコフィールド、ゴリランダー グラスメイカー いじっぱり H32 A32 ウッドハンマー/じしん/ばかぢから/グラススライダー、メガボーマンダ りゅうのまい型。既知の限界: フィールド/天候による威力補正は貪欲選択に入れていない (ワイドフォースはサイコキネシスに負ける → 次行で対応)、EV は定型のみ (想定相手ごとの微調整は次段) |
| 2026-09-11 | 生成型の技選択にフィールド/天候の威力補正 (ユーザー依頼「早めに」) | **自分で張れる**フィールド/天候だけを見る: 特性 (`BUILD_GEN_FIELD_SOURCES.abilities`: サイコメイカー/エレキメイカー/グラスメイカー/ミストメイカー/ひでり/あめふらし/すなおこし/ゆきふらし) は常時、技 (psychicterrain/sunnyday 等) はその技が型に入るテンプレートだけ (`gen_sets.own_field`)。(1) 刈り込み: フィールド無しの採点に加えて「特性 + 先頭のフィールド技」の条件込みの採点 (標準補正 `BUILD_GEN_FIELD_TYPE_BOOSTS` = フィールド 1.3 倍 (接地した使用者だけ)・晴れ/雨 1.5/0.5 倍、技固有 `BUILD_GEN_FIELD_MOVE_BOOSTS` = ワイドフォース 1.5 (使用者接地) / ライジングボルト 2.0 (相手接地) / ミストバースト 1.5 / ダイチノハドウ 2.0 / ウェザーボール 2.0、タイプ変化 `BUILD_GEN_FIELD_MOVE_TYPES` = ウェザーボール・ダイチノハドウ) でもタイプ × 分類の上位を残す。除外技 (`BUILD_GEN_AVOID_MOVES`) のソーラービーム/ソーラーブレードは晴れの条件下だけ解禁 (倍率 1.0 の登録)。(2) 貪欲選択: テンプレートごとに `own_field(特性, 補助技)` の `FieldView` を `calc_damage` に渡した与ダメージ表 (フィールドごとにキャッシュ) を使い、技固有の倍率を掛け、タイプ変化技は `calc_damage(..., override_move_type=)` (新引数: 相性・STAB・フィールド補正をそのタイプで計算) で評価。条件外の除外技は 0。フィールド役割の技は自分のタイプを強化するものを先に (`preferred_field_moves`)。付随修正: 壁型の性格は (物理向け −SpA, 特殊向け −Atk) を型ごとの攻撃技の分類 (混合なら先頭の技) で決める (`wall_nature_for_moves`。以前は速攻型と同じ (+Spe, +攻撃) の選び方を誤って当てはめ、コータスの「ふんか」型が しんちょう (−SpA) になっていた)。実測 (mc_smoke2 の脅威 30 種): イエッサン サイコメイカー = ワイドフォース/マジカルシャイン/マジカルフレイム (サイコキネシスから交代)、マフォクシー の attack3_field 型 = オーバーヒート/ワイドフォース/きあいだま/サイコフィールド、コータス ひでり おだやか = ふんか/じしん/ソーラービーム、ペリッパー あめふらし = ウェザーボール (雨でみず 100)/ぼうふう/れいとうビーム、ゴリランダー グラスメイカー は不変 (ウッドハンマーが最善のまま、グラススライダーは先制枠)。未対応 (この時点): メガ後の特性による天候 (次行で対応)、相手側のフィールド、自分の型以外 (チームの設置役) のフィールド。テスト tests/test_team_build_gen_sets (+3)、tests/test_abilities_calc (override_move_type) |
| 2026-09-11 | メガ後の特性で評価するメガ型の生成 + 想定相手ごとの能力ポイントの微調整 (ユーザー指摘: メガリザードン Y は環境に影響するので未対応にする理由が無い / EV 微調整も行う) | (1) **メガ型はフォルムごとに別に生成** (`gen_sets._generate_form`): メガ後の種族値・タイプ・特性 (champions_dex の isMega/requiredItem) で刈り込み・フィールド (ひでり → 晴れ)・接地・与ダメ表・配分を評価し、持ち物はその石で固定、型に書く特性はメガ前のもの (Showdown の記法)。フォルムごと `BUILD_GEN_MEGA_SETS`(2) 型 (以前は先頭の型の複製に石を持たせるだけで、メガ後の特性を見ていなかった)。X/Y/Z の複数フォルムも別々 (リザードン X = とうそう/物理、Y = ひでり/特殊+ソーラービーム、ガブリアス Z = 特殊)。(2) **Interaction Matrix もメガ後の特性で評価** (`interaction.view_from_set`: メガ石の型は `mega_ability` (champions_dex) を使う。以前はメガ前の特性のままで、メガクチートのちからもち等も落ちていた) し、**対面の場** `interaction.duel_field` = 自分の特性で張れる天候/フィールド (無ければ相手の特性) を両者のダメージと実効素早さに掛ける (endgame `_best_dmg` / `_race_turns` に fieldv 引数を追加。省略時は従来どおりで助言側の挙動は不変)。(3) **能力ポイントの微調整** (`gen_sets.tune_set_spread`、`BUILD_GEN_EV_*`): 重みの大きい `BUILD_GEN_EV_THREATS`(12) 種の相手に対し、配分の候補 (全 66 ポイント使い切り、1 能力 ≤ 32。素早さは「相手の上を取る最小ポイント」の集合、攻撃は 8 刻み、残りを HP/防御/特防に 8 刻み + 端数) を列挙し、Σ 重み × [上を取る 1.0 + 1 発耐える 1.0 + 2 発耐える 0.5 + 残り HP 0.25 + 1 発で倒す 1.0 + 2 発 0.5 + 与ダメ割合 0.25] (`BUILD_GEN_EV_WEIGHTS`、閾値と同様に見直し可) が最大の配分を採る。被ダメは 0 ポイントの基準からの伸縮 (× 基準防御/防御 × 基準 HP/HP)、与ダメは型のフィールド込み与ダメ表からの伸縮 (× 攻撃/基準攻撃) で、配分ごとにダメージ計算をやり直さない (3 種 24 型で 1.4 秒)。トリックルーム型は素早さに振らず下を取る。定型と同点なら定型。注記 `ev:tuned outspeed=k/n survive=k/n`。実測 (脅威 30 種): リザードン 通常 = C32 S20 H14 (S32 と同じ 8/12 の上を取れるので余りを HP へ)、メガ Y = H30 B16 S20 (晴れ + 特攻 159 で C0 でも 2 発圏 → 耐久へ)、メガ X = H30 A8 B8 S20、イエッサン = H17 B16 C8 S25、ガブリアス = H24 A16 D8 S18、メガガブリアス = A24 D14 S28。付随: 生成型の `notes` に `gen:mega:<フォルム id>`。テスト tests/test_team_build_gen_sets (EV 微調整の純粋関数、メガ型の組み立て)、tests/test_team_build_interaction (メガ後の特性・対面の場・晴れの打点)。名前の訂正: 前行の「トロッキー」は誤りで **コータス** (Torkoal) |
| 2026-09-11 | 味方の設置役・相手の場への対応と、フィールド/天候の補正表の一本化 (ユーザー指示「対応してください。スコープ外だった必要なものも実装」) | **表の一本化**: `advisor/data/field_effects.json` (場を張る特性/技、タイプ別倍率、ミストフィールドのドラゴン減衰、天候の防御補正、技固有の効果、優先度) を助言 (`advisor.damage` / `advisor.search`) と型生成 (`gen_sets`) の唯一の源にし、config の `BUILD_GEN_FIELD_*` を廃止。**助言側で欠けていたもの** (生成型が勧める技を助言が正しく評価できないため必要): `calc_damage` が技固有の効果を表から掛ける (ワイドフォース 1.5 / ライジングボルト 2.0 (相手接地) / ミストバースト 1.5 / ダイチノハドウ・ウェザーボール = タイプ変化 + 2 倍 / ソーラービーム系 = 晴れ以外の天候で 0.5 / グラスフィールドの じしん・じならし・マグニチュード 0.5 (接地した相手) / ゆきのこおりタイプ防御 1.5)、`field_move_effect()` を公開。探索の優先度 `_priority(move, view, fieldv)`: グラススライダーはグラスフィールドで接地した使用者なら +1。**相手の場**: 生成の与ダメ表と配分の微調整で、自分の場に無い種別は相手の特性が張る場 (`gen_sets.battle_field`) を使う。相手の技由来の場 (にほんばれ等) は使うか分からないので前提にしない。**並びの場**: `gen_sets.team_field_of` (規則の前提 `rules.rule_field` → 並び順で先の設置役の特性 (メガ後)/技) を S6 で求め、自分で張らないメンバーの型を `run.apply_team_field` で選び直す (場つきの生成型 `gen(sid, field)` を候補に足し、被覆をその場で採点 `rank_sets(field=)`。注記 `team_field:<場>` / `under:<種別>=<場>`、単一メガの制約を再適用)。規則の判定 (`rule_context`) もエースの型を規則の場 (サイコフィールド) で採点する。**Interaction Matrix**: `interaction_row/matrix` に場の指定 `fieldv`、無い種別は両者の特性の場で埋める (`resolve_field`)、行に `field` を記録。場は両者に掛かる (相手のエスパー技も強くなる) ので、規則つき run のエース分類は少し変わる (mc_smoke5: マフォクシー/セグレイブがエースから外れ、イエッサンが TR 型エースに)。実測 (脅威 30 種): ゴリランダー (グラスメイカー) は じしん → 10 まんばりき (グラスフィールドで じしん半減)、通常リザードンの にほんばれ型は耐久 12/12 (晴れで相手のみず技半減)、メガ Y も 12/12。テスト tests/test_abilities_calc (技固有の効果)、tests/test_search (グラススライダー)、tests/test_team_build_gen_sets (場の合成)、tests/test_team_build_interaction (場の指定/解決)、tests/test_team_build_rules (rule_field) |
| 2026-09-11 | メガ石の上限の緩和・かるわざのエースの持ち物・日本語名の表からの逆引き (ユーザー指摘 3 点) | (1) **メガ石**: 「1 試合 1 回のメガシンカ」を「1 構築 1 個の石」と取り違え、ビーム (`beam_complete max_megas=1`)・S6 (`enforce_single_mega`)・冗長の罰則 (2 個目から)・実相手の再構成 (`tools/real_opponents`) で 1 個に制限していた。ランク上位 200 構築の実測 (env/ranked_teams): 石 0 個 1% / 1 個 33% / **2 個 61%** / 3 個 4% / 4 個 0.5%。`BUILD_MAX_MEGA_STONES`=3 (上限)、`BUILD_MEGA_FREE_STONES`=2 (ここまで罰則なし)、`sets.enforce_max_megas` (keep → 被覆順に残す)、1 試合に 1 体しかメガシンカできないので並びの被覆は `candidates.mega_user` (メガ後 − 素の被覆の重みつき和が最大の 1 体) だけメガ後、他は素の姿の被覆 `SpeciesFeature.coverage_base` (S3 で石持ちの型の持ち物を外して計算) で数える。煙試験 mc_smoke6 (26/26 合法): 石 1 個 8 / 2 個 13 / 3 個 5 の並び (以前は全て 1 個)。**既知の限界**: シミュレータの相手 (RL 方策 / 選出モデル) はチームプレビューからメガ先を読まないので、「石 1 個は読まれる」不利は測定に現れない。緩和の根拠は実構築の分布であって測定ではない。(2) **かるわざのエースの持ち物**: タスキを強制していた (`rule:ace_item<-whiteherb`) が、かるわざは発動させる消耗品で良くタスキは他に回せる。特性ごとの優先 `BUILD_RULE_ACE_ITEMS_BY_ABILITY` = {かるわざ: しろいハーブ → ノーマルジュエル → タスキ} と発動条件 `BUILD_UNBURDEN_TRIGGERS` (しろいハーブ = 自分の能力を下げる技 (boost_moves.json の self)、ノーマルジュエル = ノーマルの攻撃技。`rules.unburden_trigger_ok`)。オオニューラの実データ (M-B 末): しろいハーブ 37.9% > タスキ 26.7% > オボン 16.8%、ノーマルジュエルは上位 10 に無い。mc_smoke6 ではオオニューラのエースがしろいハーブのまま。(3) **日本語名**: 表示は `advisor/ja_names` (vision/data/jp_names.json の逆引き: 種族/技/特性/持ち物/性格/タイプ、Showdown 本文の解析、Markdown の表) を通し、手書きの翻訳をしない (「ダイレクトクロー」は誤りで正しくは表の「フェイタルクロー」)。性格の表 (25 件) を jp_names.json に追加。`bash scripts/team_ja.sh --run-id <run> [--candidate <id>]` / `--file <本文>` で候補を日本語の表に。構築レポート (`report.py`) の並びも日本語の表、LLM には対応表 `ja` を渡す。テスト tests/test_team_build_candidates (+mega_user/coverage_base/上限)、tests/test_team_build_sets (+enforce_max_megas)、tests/test_team_build_rules (特性ごとの持ち物)、tests/test_team_build_report、tests/test_ja_names (新規、CI 登録) |
| 2026-09-11 | 1 試合 1 回の資源 (メガシンカ) の推定 (ユーザー指摘「相手がチームプレビューからメガ先を読まないのは致命的。選出の学習には必須、構築の相手にも必要」) | 設計 `docs/GIMMICK_INFERENCE_DESIGN.md`。**共通の推定器** `advisor/gimmick.py`: 種族ごとの「メガ石を持つ確率」を使用率 DB の石の使用率から事前分布に (X/Y は石ごとに按分、使用率が無い石持ち種は `GIMMICK_DEFAULT_STONE_PRIOR`)、判明情報 (持ち物・メガ済み・陣営の権利消費) で上書き、メガ後の姿 (`mega_view`) と期待種族値。**入れた場所**: (1) 助言の探索 (`advisor/search`): 相手の技を「メガシンカあり/なし」に分岐 (`opp_mega_split`、p ≥ `SEARCH_OPP_MEGA_MIN_PROB`=0.2)、分岐では技の解決前にメガ後の姿に変え権利を消費、engine が相手の場のポケモンの見込みを渡し注記 (「相手もメガシンカ未使用: メガリザードンY 61%, X 37% の見込み」)。探索時間は例で 0.5 → 1.4 秒。(2) 発見的な選出 (`advisor/selection`): 相手候補にメガ後の姿を確率で混ぜる。(3) **選出モデル v3** (`agent/selection_features_v3`): 自分の石持ちはメガ後の埋め込み、相手は事前分布で混ぜた埋め込み、スカラー 6。埋め込みにメガ後の姿 308 種を追加 (`tools/species_embedding`)。v1 のピンは触らず v3 は別ピン。`train_selection --features v3`、版の振り分けは `agent/selection_dispatch` (config `SELECTION_FEATURES`、構築の相手の選出・測定の助言の選出・候補専用モデルの適応が従う)。(4) **観測 v8** (`agent/encoders`、末尾追記 16 次元、`BATTLE_OBS_DIM`=436): 相手の場の P(メガ) と期待種族値の差、相手控えの P、自分の石/メガ可能、権利の消費、陣営の残りメガ脅威。既定の学習は v6 (388) のままで `TRAIN_OBS=v8` で有効、`train/migrate_obs.py` で旧チェックポイントの入力層をゼロ拡張 (出力一致を検査)。**切替はユーザー承認待ち**。(5) 収集データに `own_items` (自分の持ち物) / `own_mega` / `opp_mega` (実際にメガシンカした個体) を記録 (`tools/collect_selection_data`)、v3 の学習は記録があればそれを石の所在に使う。**オフラインの門 (v1 と同じ分割・手順、3 seed、64,000 件 / 274 チーム)**: 平均予測からの MSE 改善 v1 +3.7% / v3 +3.6%、対応比較の順位精度 v1 0.536 / v3 0.518 (seed 別 0.543/0.522/0.542 vs 0.503/0.520/0.531) → **v3 は劣らないとは言えず未昇格 (`SELECTION_FEATURES`="v1" のまま)**。理由の見立て: 既存の収集データは種族しか記録しておらず (石の所在はプールの型から復元、チーム内で変化しない)、相手も RL 方策で「読まない」対戦の勝敗なので、メガ先の情報が勝敗を分けた形跡がデータに無い。次の収集 (`own_items` 入り、v8 の方策・v3 の相手) で学習し直してから再判定する |
