# パーティ構築提案の方針転換案 v3 — Team × 専用助言方策の共同最適化

作成: 2026-09-06 (v1)。改訂: v2 (外部レビュー1、評価の三層化)、**v3 (外部レビュー2、共同最適化と4階層評価)**。
変更履歴は §12。v3 は「実装コストを無視した完成形」のレビューを土台に、**このプロジェクトの実コストで
実現できる形**に落としたもの。理想形からの意図的な縮小は理由つきで明記する (§2.3, §8)。

---

## 0. 結論

**採用。ただし対象を「パーティ」から「パーティ + そのパーティ専用に適応した助言方策」に変える。**

最終成果物は `final_team.json` ではなく **Final Build Package** (チーム + 適応済み選出モデル + 選出パターン +
対面行列 + 評価 + 頑健性 + 系譜 + manifest) になる (§6.4)。

責任分離 (完成形):

```
LLM         仮説生成・意味理解・説明 (コンセプト、修正仮説、記事)
Search      候補生成 (コンセプト別・多様性保存ビーム、型の列挙)
Rules       正しさ・合法性の保証 (所持・合法性・クローズ・整合・数値)
Learning    チームごとの助言方策の適応 (選出モデル)
Simulator   実験 (助言操縦の対戦)
Statistics  比較・脱落・採否 (対応差・実用差 ε・confidence racing)
Holdout     自己欺瞞の防止 (封印した相手集合で一度だけ)
```

一言で: **LLM に最適解を考えさせるのではなく、LLM に探索仮説を大量に作らせ、ルールで合法な探索空間に
閉じ込め、実際に配布する助言方策との組み合わせを未見の相手で競わせて決めるシステム。**

v3 で v2 から変えた最重要点は 1 つ: **選出適応を最後に一度 (S9) ではなく、候補比較の段階から
「Team + 候補専用の助言方策」を不可分の個体として扱う** (§2)。9/5 の実測で「素の強さと助言操縦の
順位が一致しない」ことが示されたが、同じ理屈で「汎用の助言方策で測った順位」と「専用に適応した
助言方策で測った順位」も一致しない可能性があり、後者こそユーザーが実際に得る値である。

---

## 1. 現行の実態 (要約)

- 入口: フロントの「構築提案」→ 別プロセス `tools.team_proposal --propose` (進化探索、両サイド同一方策の実対戦、受入検定)。
- 助言エンジンは操縦しない。直近の v3.1 決定は候補生成 → 助言操縦 300+600 戦 → 対応差 → Claude の想定運用、を使い捨てスクリプトで実施。
- 助言操縦の測定 (`tools/check_advisor_player`) は選出に `teampreview_order` (タイプ相性のヒューリスティック) を使い、
  実助言の選出 (`advisor/selection.advise_selection` → `selection_model.predict_best`) と一致していない。
- 選出モデルはチーム固有: 実際に使うチームで `scripts/collect_selection.sh 2 2500 myteam` (Showdown 実対戦、
  50% ランダム選出で探索) → `train_selection` で微調整しないと汎化しない (2026-07-29 実測、train_selection.py 冒頭表)。

---

## 2. 最適化対象と目的関数

### 2.1 定義

```
π_T = Adapt(π_0, T, D_search)          T 用に適応した助言方策
T*  = argmax_T  E_{O ~ M} [ P(win | T, π_T, O) ]
```

T はパーティ、π_0 は汎用の助言方策、D_search は適応に使う相手集合 (§3)、M は現在の環境分布。
成果物は (T*, π_T*) のペア。

### 2.2 このプロジェクトで「適応 (Adapt)」が意味するもの

| 助言方策の構成要素 | 現状 | 候補ごとの適応 | v3 の扱い |
|---|---|---|---|
| 選出 (3体選び) | 選出モデル (勝率回帰、チーム固有) + 相性フォールバック | **可能・安価**: 候補チーム固定で 5,000 戦収集 → 微調整。1 候補 10〜20 分の見込み (実績: 多チーム収集 49,000 件 / 30 分から推定、要実測) | **候補ごとに適応する** (S7) |
| 行動 (技・交代) | ダメージ計算エンジン + RL 方策のブレンド (RL は自己対戦で学ぶ汎用方策、EMA 更新) | 理論上は可能だが、チームごとの RL 微調整は数時間規模で、ツールも未整備 | **汎用のまま** (ピン固定)。意図的な縮小。候補間で共通なので比較の公平性は保たれる |
| 探索・信念 (BELIEF_K 等) | 既定 OFF (P7〜P10 で棄却) | 対象外 | 対象外 |

つまり v3 の「候補専用の助言方策」= **汎用の行動方策 (ピン) + 候補専用に適応した選出モデル**。
行動方策の適応は将来課題として §11 に残す。

### 2.3 理想形からの縮小 (理由つき)

| 完成形 (レビュー) | v3 | 理由 |
|---|---|---|
| 全候補に専用 Advisor | racing の第1ラウンド生存 (≤8) に専用選出モデル。第1ラウンドは汎用 (相性選出) で行う | 20 候補 × 20 分 = 7 時間は 1 run に対して重い。第1ラウンドの脱落は「明確に劣る」もののみ (racing、§4) なので取りこぼしは限定的 |
| SEARCH 内 K-fold cross-fitting | SEARCH を A (適応用) / B (評価用) の固定 2 分割 | K 個の適応モデルを作るコストを避ける。漏洩防止 (「学習した相手に勝つ」) は 2 分割で達成できる |
| 型の全列挙 (10^5 variants) | 型ライブラリ (代表型 + 使用率 5% 以上の技・持ち物の入替) の列挙 → 制約 → 評価関数 | 列挙は安いが評価 (ダメージ計算) が候補数に比例する。ライブラリ外の型は実戦で見ない |
| 相手行動モデルの学習 | 対象外 | 単一ユーザーのログ規模 (数十戦/季) では学べない (§9) |

---

## 3. 評価データ: 4 階層と系統単位の分割

### 3.1 相手集合の系統化 (opponent family)

POOL_PIN (上位実構築) の各チームを **系統 (family)** にまとめる: 種族集合の Jaccard 類似度が閾値以上
(6 体中 4 体以上共通) かつメガ軸が同じなら同一系統。技だけ違う構築が別の階層に散る漏洩を防ぐ。
系統ごとに `rank / usage / style (offense, cycle, stall, balance) / 主要種 / メガ軸` の属性を持たせ、
**系統単位で層化分割** (group-stratified split) する。

### 3.2 4 階層

| 階層 | 用途 | 使い方の規則 | 目安 (top 200 → 系統化後) |
|---|---|---|---|
| SEARCH | 構築探索・改修・敗因統計・選出適応 | 何度使ってもよい。内部を A (適応用) / B (評価用) に固定分割し、適応した方策は B で測る | 50% |
| SELECTION | 完成候補の比較 | 「A と B なら A」と選ぶのは可。**「A がこの相手に負けたから A を変える」は禁止** (訓練データ化するため) | 25% |
| HOLDOUT | 最終確認 | **run につき一度だけ**。不合格なら「この run は失敗」で終了。holdout を見て修正しない。次 run は新しい Meta Snapshot か新しい封印 holdout を用意 | 20% |
| STRESS | 頑健性 | 順位決定に使わない。分布外の相手 (外部取り込み構築・旧シーズン構築) と助言方策の揺らぎ (§4.4) | 5% + 外部構築 |

シードも階層ごとに分け、run の manifest に固定する。

---

## 4. 統計的判定

### 4.1 対応比較 (維持)

候補 A・B・参照を **同一相手 × 同一シード** で走らせ、対応差 Δ = W_A − W_B を見る
(相手の強弱と乱数のノイズが相殺される)。候補ごとの独立 1,000 戦より効率が良い。

### 4.2 4 状態の判定 (実用差 ε = 0.02)

```
CI(Δ) 全体 > +ε            Improved
CI(Δ) 全体 < −ε            Degraded
CI(Δ) ⊂ [−ε, +ε]           Equivalent (統計的に有意でも実用上同等。これ以上戦数を増やさない)
それ以外                    Uncertain → 追加測定
```

### 4.3 Confidence racing (固定の 20→8→4 はやめる)

各候補を「現在の best」と対応比較し、Uncertain の間だけ追加測定する。戦数 100 → 300 → 600 → 1200 → 2400 は
候補であって固定順ではない。Degraded になった候補は脱落、Improved なら best を更新。上限 `BUILD_MAX_BATTLES`
(既定 2400) で打ち切り、そのときは Equivalent 扱い。多重比較で偽の Improved が出る危険は、SELECTION と HOLDOUT
が別集合であることで抑える。

### 4.4 頑健性 (STRESS): 素の強さより「助言方策への感度」

最終候補を次の条件でも測る (STRESS 階層):

```
Advisor current (ピン) / Advisor previous checkpoint / 選出ノイズ +5%, +10% / 行動ノイズ (2位の手を確率 p で選ぶ)
```

例: current 60% / noisy 59% の A と、current 61% / noisy 48% の B なら、B は特定方策の癖を悪用している疑いが強い。
素のヒューリスティック勝率は **diagnostic** (報告に載せるだけ) に格下げする。

### 4.5 最終判定 (S12) の規則

LLM は一切関与しない。Primary = 参照 (現行パーティ + その適応済み選出モデル) に対する ΔWR。

```
Improved   → 採用
Equivalent → 二次指標でタイブレーク (catastrophic matchup 率 / 助言感度 / 選出の安定性 /
              環境カバレッジ / ユーザー指定スタイルへの適合)。「こちらの方が綺麗」は入れない
Degraded   → 不採用 (run 失敗)
Uncertain  → 追加測定 (上限まで)
```

---

## 5. 段の定義 (S0〜S13)

| 段 | 内容 | 主担当 | 評価階層 |
|---|---|---|---|
| S0 | BuildSpec の正規化 (所持・固定・除外・スタイル・予算・レギュ) | rule + LLM (自由文のみ) | — |
| S1 | Meta Snapshot の固定 (使用率 DB の健全スナップショット、ローカルメタ、合法種) | rule | — |
| S2 | 相手系統の生成と 4 階層への分割、シード固定 | rule | — |
| S3 | 脅威リストと **Interaction Matrix** (§6.2、連続値と資源コスト) | simulator (ダメージ計算・1v1) | — |
| S4 | コンセプト系統を広く生成 (8〜20: offense / balance / bulky offense / cycle / setup / speed control / anti-meta / specific core …)。構造的に異なるものを残す | **Opus** (候補は所持種 id、根拠は行列) | — |
| S5 | 6 体候補を多様性保存つきで探索 (コンセプト別ビーム + quota: best overall / offense / balance / anti-meta / alternative core / novelty)。Score に加えて候補間距離を見る | search + Sonnet 選抜 (候補外は選べない) | — |
| S6 | 型候補の列挙と最適化 (ライブラリ × 合法な入替 → 役割制約 → ダメージ/素早さ/耐久の閾値 → 相乗制約 → 上位)。LLM は「この個体に speed control を担当させる」までで、型は探索器が決める | rule / search | — |
| S7 | **候補ごとの選出モデル適応** (SEARCH-A で収集・微調整)。第1ラウンド生存 (≤8) が対象。適応前は相性選出で代用 | learning | SEARCH-A |
| S8 | SEARCH-B で paired confidence racing (Team + 専用選出モデル として) | measurement | SEARCH-B |
| S9 | 対戦記録 → 敗因統計 → LLM 修正仮説 (≤3) → **介入実験** (Variant A: 1 体変更 / B: 型だけ変更 / C: 選出方策だけ変更) を paired で比較し原因候補を確認。≤2 枠は同系統の改修、3 枠以上は新しいコンセプト系統として分岐 | rule + LLM + measurement | SEARCH |
| S10 | 完成候補を SELECTION で比較 (見て選ぶのは可、見て直すのは不可) | measurement | SELECTION |
| S11 | 勝者の選出モデルを SEARCH + SELECTION で再学習 (最終版を固定) | learning | SEARCH+SELECTION |
| S12 | **封印 HOLDOUT で最終測定** + STRESS (§4.4、§4.5) | measurement | HOLDOUT / STRESS |
| S13 | Final Build Package・説明記事・登録 | rule + LLM (記事は表示専用) | — |

流れ: 探索 → そのチームを扱える選出方策を作る → Team × 方策として評価 → 改修 → 未見データで候補選択 →
最終方策を学習 → 完全未使用データで一度だけ試験。

---

## 6. データ契約

### 6.1 run ディレクトリ

```
logs/build_search/runs/<run_id>/
  manifest.json           §6.4
  request.json            S0
  meta_snapshot.json      S1
  opponent_families.json  S2 (系統・属性・階層割当・シード)
  interaction_matrix.json S3
  s04_concepts.json / s05_candidates.json / s06_sets/ / lineage.json
  advisors/<candidate_id>/selection_model.*   S7, S11
  battles/<stage>/<candidate_id>.jsonl        §6.3
  evaluation/<stage>.json                      racing の履歴・4 状態
  loss_stats.json / interventions.json         S9
  robustness.json                              S12 STRESS
  llm/<stage>_<model>_<n>.json                 prompt / response / 検証 / トークン数
  final/                                       §6.4
```

### 6.2 Interaction Matrix (S3)

covered / not covered の二値ではなく、**勝つためにどれだけ資源を要求するか**まで表す。

```json
{"my_id": "kingambit", "opponent": "garchomp#1",
 "lead": 0.41, "switch_in": 0.22, "revenge": 0.88, "setup_stop": 0.70,
 "speed_control": false, "hazard_pressure": 0.0, "status_pressure": 0.0,
 "mega_required": false,
 "resource_cost": {"hp": 0.62, "item": false, "mega": false}}
```

値は代表型同士のダメージ計算と 1v1 (advisor/damage, endgame.duel, calc_stat) から機械的に出す
(lead = 対面からの勝率、switch_in = 最大打点を受けてからの勝率、revenge = 削れた相手を上から/先制で落とせる度合い、
setup_stop = +1/+2 の相手を止められる度合い、resource_cost.hp = 勝つのに失う HP 割合、item = タスキ/木の実を消費するか)。

### 6.3 対戦記録 (勝敗だけでは足りない)

測定用の対戦 (check_advisor_player) と接続テストの実戦の両方で、再現できるものはすべて残す:

```
battle_id, candidate_team_id, advisor_policy_id (行動方策ピン + 選出モデル id), opponent_team_id, opponent_family_id,
battle_seed, policy_seed, team_preview (両者 6 体), our_selection, opponent_selection, lead (両者),
turns[]: {state_before, advisor_recommendation (best + actions + スコア), executed_action, opponent_action, state_after},
switch_events, ko_events (誰が何で誰を), status_events, resource_usage (タスキ/木の実/ばけのかわ), mega_usage (両者・ターン),
win/loss, turn_count, remaining_members, termination_reason
```

これで後から助言側の問題 (推奨と結果の系統的なずれ) も解析できる。

### 6.4 Final Build Package と manifest

```
final/
  team.json                チーム (Showdown 形式 + 日本語表記 + 種族ID)
  advisor_policy/          適応済み選出モデル (+ 行動方策ピンへの参照と sha256)
  selection_patterns.json  基本選出 / 対○○ (S10 までの記録から機械生成、説明は表示専用)
  matchup_matrix.json      Interaction Matrix の並び版
  evaluation.json          racing / SELECTION / HOLDOUT の全結果 (対応差・CI・4 状態)
  robustness.json          STRESS (方策の揺らぎ・分布外相手・素の強さ diagnostic)
  lineage.json             親子関係・変更・仮説
  build_report.md          構築記事形式 (Sonnet、表示専用)
  manifest.json            git commit / build schema 版 / meta snapshot / opponent split と系統 / Showdown commit /
                           行動方策 (モデル・チェックポイント sha256) / 選出モデル (同) / LLM モデルと prompt 版 /
                           全シード / 評価プロトコル版
```

「この構築は強い」ではなく「この環境・この助言方策・この評価プロトコルでは、これだけ強かった」を再現可能な形で残す。
助言方策や RL 方策を更新したら、採用中の Package を同じ HOLDOUT 手順で再測定し差を記録する。

### 6.5 LLM 出力の規約 (v2 と同じ)

機械が意思決定に使うフィールド (id・enum・構造化値) は `authoritative` に置き検証対象、自然言語は `display` (表示専用)。
不合格は理由つきで差し戻し (最大 2 回) → ルール既定値。

---

## 7. 実装構造

```
tools/team_build/
  spec.py            S0        families.py        S2 (系統化・層化分割・シード)
  meta_snapshot.py   S1        interaction.py     S3 (純粋関数中心)
  concepts.py        S4 (Opus) candidates.py      S5 (多様性保存ビーム + Sonnet 選抜)
  sets.py            S6        adapt.py           S7/S11 (collect_selection → train_selection のラッパー、候補ごと)
  racing.py          S8/S10/S12 (confidence racing、4 状態、STRESS)
  loss_stats.py      S9 (純粋関数)  interventions.py  S9 (仮説 → Variant A/B/C → paired)
  package.py         S13 (Final Build Package、manifest、登録、記事)
  battle_log.py      §6.3 の記録 (check_advisor_player から呼ぶ)
  llm/provider.py    LLMProvider (AgentToolProvider / ClaudeCLIProvider)
  run.py             オーケストレータ (`--from/--to`、`--llm headless`)
```

既存ツールへの変更:
- `tools/check_advisor_player`: `--pick-policy advisor|teampreview` (既定 advisor)、`--selection-model <path>` (候補専用モデル)、
  `--opp-split search-a|search-b|selection|holdout|stress`、`--battle-log <jsonl>`、`--pick-noise p`、`--action-noise p`、
  `--models-dir` (行動方策ピン、既存の CHAMPIONS_MODELS_DIR)。
- `champions_agent/env/ranked_teams`: 系統化と階層 (`split=`)、外部構築の STRESS 用取り込み。
- `tools/collect_selection_data` / `train_selection`: `--team-file` で候補チーム固定、出力先を候補 id ごとに分ける。
- `tools/team_proposal.paired_verdict`: 4 状態 + ε (旧関数は互換のため残す)。
- `champions_agent/config.py`: `BUILD_EQUIV_EPS=0.02`, `BUILD_MAX_BATTLES=2400`, `BUILD_RACE_STEPS=(100,300,600,1200,2400)`,
  `BUILD_FAMILY_JACCARD=0.5`, `BUILD_SPLIT=(0.5,0.25,0.2,0.05)`, `BUILD_MAX_CHANGES=2`, `BUILD_MAX_REPAIRS=2`,
  `BUILD_ADAPT_BATTLES=5000`, `BUILD_STRESS_NOISE=(0.05,0.10)`。
- フロント (Phase 3): `run_team_build` (現行 `run_team_proposal` と同型) と依頼フォーム。

---

## 8. コストと所要時間 (1 run、実測ベース)

助言操縦の測定は 5 並列で約 1 戦/秒 (9/5 実測)。学習ループと CPU を分け合う (構築中の一時停止は要承認)。

| 段 | 内容 | 所要 |
|---|---|---|
| S0〜S6 | ルール + LLM (Opus 1〜3 回、Sonnet 3〜5 回) | 10〜20 分 |
| S8 r1 | 15〜20 候補 × 100 戦、汎用 (相性) 選出で racing | 25〜35 分 |
| S7 | 生存 ≤8 候補の選出モデル適応 (5,000 戦収集 + 微調整、各 10〜20 分、2 並列) | 40〜80 分 |
| S8 r2〜 | 生存候補 × 300〜1200 戦 (Uncertain の間だけ) | 40〜90 分 |
| S9 | 介入実験 1 反復 (Variant A/B/C × paired 300〜600) | 40〜70 分 |
| S10 | SELECTION で 3〜4 候補 + 参照 | 30〜60 分 |
| S11 | 勝者の選出モデル再学習 | 15〜30 分 |
| S12 | HOLDOUT + STRESS (方策 2 版 × ノイズ 3 条件、各 300) | 40〜80 分 |
| 合計 | — | **4〜7 時間** (v2 の 2.5〜4 時間から増加。共同最適化と頑健性検査の代償) |

LLM は全体の 1 割未満。料金は llm/ のトークン数から実行時に算出する (本書では扱わない)。

---

## 9. 利用ログの活用 — 単一ユーザー規模での現実解

レビュー2 後半は「利用者が増えて数万〜数十万試合のログが溜まる」前提で書かれている。本プロジェクトは
単一ユーザー・ローカル運用で、実戦ログは接続テストの数戦〜数十戦/季 (logs/battles/*.jsonl、読み取り専用) である。
その規模でも効くもの、効かないものを分ける。

| 項目 | 規模の要件 | v3 での扱い |
|---|---|---|
| ログの由来ラベル (organic / recommended / experiment) | 不要 | **今すぐ**: 実戦ログと測定ログに `source` と candidate/advisor id を付ける (自己強化ループの検出と補正の前提) |
| 実際に当たった相手を評価分布に混ぜる (M_real) | 数十戦から可 (重みつき) | **S1**: ローカルメタを Meta Snapshot に含め、STRESS の相手に「実際に当たった構築」を入れる。順位決定には使わない (標本が小さい) |
| ユーザーが助言に従わなかった手の反事実評価 (助言の系統的誤り) | 数百決定から可 | **既存の再生ハーネス** (advice_replay / decision_audit) で「助言 vs 実行」を探索器で再評価し、Q(実行) > Q(助言) の状態群を報告。ユーザー行動を模倣教師にはしない |
| 相手の選出予測・行動モデルの学習 | 10^4 戦以上 | **対象外** (将来)。当面は使用率 DB の共起と相性選出で代用 |
| 型推定 (Belief) の条件付き分布 | 10^3〜10^4 戦 | **対象外** (P7〜P10 で棄却済み。データが増えたら再検討) |
| 次レギュレーションへの warm start | 不要 (構造の話) | **既に分離されている**: 行動方策 (RL、汎用) は継続、環境固有 (プール・埋め込み・選出モデル) は再収集 (REGULATION_CHANGE_RUNBOOK §5)。「Universal + Regulation adapter」の名前で manifest に明記 |
| 環境ダイナミクス (数日後に増える構築) | 複数シーズンの履歴 | **対象外** (将来)。evolve_teams の `--forecast-mix` が原型 |
| Active learning / 探索枠 (exploitation 90〜95%) | 多数ユーザー | **対象外**。単一ユーザーでは「通常利用」と「experimental build」を run の種別として分けるだけ |
| 自己強化ループの補正 (importance weighting) | 多数ユーザー | **対象外**。単一ユーザーでは自分の推奨チームが「環境」に混ざらないので、由来ラベルだけで足りる |

要点: ログの恩恵の中心は「LLM が賢くなる」ではなく **環境モデル・相手モデル・信念・選出/行動の価値** の精度であり、
それが候補の順位付けを正確にする。単一ユーザー規模では **由来ラベル・ローカルメタ・反事実評価** の 3 つが現実解で、
それ以外はログ量の閾値 (1,000 / 10,000 戦) を manifest に記録して将来判断する。

---

## 10. 段階的導入

| 段階 | 内容 | 成果 |
|---|---|---|
| Phase 0 (1〜2 日) | **評価基盤**: 相手系統化と 4 階層分割、`--pick-policy advisor` + `--selection-model`、対戦記録 (§6.3)、4 状態 racing、STRESS のノイズ注入、manifest、ログの由来ラベル。v3.1 の使い捨てスクリプトの正式化 | 現行手順が「Team × 方策」で再現可能かつ過適合しない形になる |
| Phase 1 (2〜3 日) | S0/S1/S3/S5 (ルール列挙)/S6/S8/S10/S12/S13 + `/build-team` スキル。S4 は主セッションが直接。S7 は生存候補の選出適応 (adapt.py) | チャットで一気通貫の共同最適化 |
| Phase 2 (2〜3 日) | S4/S9 を Opus、S5/S13 を Sonnet のサブプロセスに (LLMProvider)。S9 の介入実験 (Variant A/B/C)。系譜 | 説明つき提案と因果の切り分け |
| Phase 3 (1〜2 日) | フロントの依頼フォーム + ヘッドレス実行 + Package の表示。コスト上限 | 一発依頼の入口 |
| 将来 | 行動方策のチーム別適応、相手行動モデル、環境ダイナミクス (ログ閾値到達後) | — |

M-C 切替 (9/9) との関係: Phase 0〜1 は M-B データで作って検証できる。M-C の環境データが揃うまで S1 は旧シーズンの
型を引き継ぐ。シミュレータの M-C 対応は上流待ち。

---

## 11. 未決事項 (ユーザー判断)

1. 共同最適化の範囲: 選出モデルのみ (v3) でよいか。行動方策のチーム別適応は将来課題のままでよいか。
2. 適応する候補数の上限 (既定: racing 第1ラウンド生存 ≤8) と 1 候補あたりの収集戦数 (既定 5,000)。
3. 相手集合: top 200 を系統化して 50/25/20/5% でよいか。HOLDOUT 不合格は「run 失敗」で終了でよいか。
4. 構築実行中の学習一時停止 (4〜7 時間の run では影響が大きい)。
5. ε (0.02)、上限戦数 (2,400)、STRESS のノイズ (5%, 10%)。
6. 1 run の LLM 予算、コンセプト数 (8〜20)、改修反復 (2)。
7. 構築記事はユーザーが本文を貼る運用でよいか (外部取得は要承認)。
8. 入口の優先順位: チャット (Phase 1) → フロント (Phase 3)。

---

## 12. 変更履歴

### v2 → v3 (レビュー2 の反映)

| 指摘 | 反映 |
|---|---|
| 最適化対象を Team × Advisor に | §2。選出モデルを候補ごとに適応 (S7)、行動方策は汎用のまま (意図的縮小、§2.3) |
| S9 選出適応を独立工程にしない | S7 (候補比較前) + S11 (勝者の再学習) に分割 |
| 相手集合は系統単位で分割 | §3.1 (Jaccard + メガ軸、層化) |
| 4 階層 (SEARCH / SELECTION / HOLDOUT / STRESS) と cross-fitting | §3.2。SEARCH は A/B 固定 2 分割 (K-fold は縮小) |
| HOLDOUT 不合格時に修正しない | §3.2 (run 失敗で終了、次 run は新しい封印) |
| 固定 halving → confidence racing | §4.3 |
| 4 状態の判定と ε | §4.2 |
| 素の強さより助言感度 | §4.4 (STRESS)。素の強さは diagnostic |
| コンセプト 8〜20、多様性保存ビーム、型の列挙 | S4〜S6 |
| Interaction Matrix (連続値・資源コスト) | §6.2 |
| 仮説 → 介入 → 検証 (Variant A/B/C) | S9 |
| ≤2 枠は改修、3 枠以上は新系統 | S9 |
| 包括的な対戦記録 | §6.3 |
| Final Build Package と manifest | §6.4 |
| 利用ログの活用 | §9 (単一ユーザー規模に読み替え) |

### v1 → v2 (レビュー1 の反映)

S6 を助言操縦の多段評価に / 評価集合の三層化 / 選出適応後に holdout / LLM 出力の authoritative・display 分離 /
対応差の CI と ε / 対面特徴の多面化 (テラス → メガ要求) / ビームの多様性 / 型はライブラリ選択 /
敗因統計 → 仮説 / 系譜 / 共適応の manifest / LLMProvider。
