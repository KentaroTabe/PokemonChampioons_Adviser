# パーティ構築提案の方針転換案 v2 — Claude Code 主導・粒度別サブプロセス・評価の三層化

作成: 2026-09-06 (v1)。改訂: 2026-09-06 (v2、外部レビューを反映。変更点は §10)。
対象: 「構築はフロントではなく Claude Code のチャットで行い、裏で Opus / Sonnet / Haiku が粒度に応じた
サブプロセスとして走る」方針転換の評価と、採用する場合の具体設計。

---

## 0. 結論

**採用。ただし評価設計 (S6〜S11) を先に直す。** 目的関数を「強い構築」から
**「この助言システムを使うユーザーが勝ちやすい構築」** に置き直す点が核で、これは v1 と同じ。
v2 で変えたのは、その目的関数を**最初から最後まで一貫して使い、かつ評価系への過適合を防ぐ**ための構造。

責任分離 (不変):

```
LLM        探索空間を賢く絞る (コンセプト・候補選抜・修正仮説・説明)
ルール      間違ってはいけないことを保証する (所持・合法性・クローズ・整合・数値)
シミュレータ 強いかどうかを決める (助言エンジン操縦の対応差)
holdout    自分自身を騙していないことを確認する (最後に一度だけ使う相手集合)
```

v2 の必須変更 (レビューの 4 点、いずれも採用):

1. **S6 をヒューリスティック順位付けから、助言操縦の多段評価 (successive halving) へ。**
   移行の根拠が「素の強さと助言操縦の順位が一致しない」ことなので、絞り込みに別の指標を使うと
   本評価に到達する前に真の最適解を捨てる。素の強さは**頑健性の副指標**として残す (§2.1)。
2. **評価用の相手集合を dev / validation / holdout の三層に分ける。** 改修ループ (S8) が同じ相手列で
   回ると、その相手列に強くなっただけの構築を「環境に強い」と誤認する (§2.2)。
3. **選出学習の適応 (S9) を最終測定 (S10) より前に置く。** 測ったシステムと渡すシステムを同一にする。
   実装上も、現行の測定 (check_advisor_player) は選出に `teampreview_order` (ヒューリスティック) を使い、
   実際の助言が使う選出モデル (`selection_model.predict_best`) を使っていない。**測定の選出方策を
   実助言と一致させる**ことも同時に行う (§2.4)。
4. **「LLM 出力は id 限定」を「機械が意思決定に使うフィールドだけ構造化・検証対象、自然言語は表示専用」に
   再定義する** (§4.2)。

プロジェクト事情による読み替え: チャンピオンズにテラスタルは無い (メガシンカのみ、1 試合 1 回)。
レビューの `tera_required` は **`mega_required` (メガ枠を要求するか / メガ石の競合)** に置き換える。

---

## 1. 現行の実態 (v1 §1 の要約)

- 入口: フロントの「構築提案」パネル → 別プロセス `tools.team_proposal --propose` (進化探索、実対戦評価、受入検定)。
- 評価器: ローカル Showdown、両サイド同一方策 (ヒューリスティック / RL)。**助言エンジンは操縦しない。**
- 診断: `tools/team_report` (1v1 行列・素早さ・耐久・共起補完)、`advisor/team_advice` (試合後アドバイス)。
- 直近の実運用 (v3.1): 候補生成 → 助言操縦 300+600 戦 → 対応差 → Claude が想定運用。使い捨てスクリプトで実施。
- 実測 (9/5): 素の強さ (ヒューリスティック操縦) ドドゲザン 0.713 / サザンドラ 0.600 / カバルドン 0.483 に対し、
  助言操縦 (併合900戦) 0.754 / 0.742 / 0.713。順位も差の大きさも一致しない。

---

## 2. 評価設計 (v2 の中心)

### 2.1 目的関数と副指標

| 種別 | 指標 | 使い方 |
|---|---|---|
| Primary | **助言エンジン操縦の勝率** (advisor-as-player、固定チーム) | S6 の絞り込み、S7 の候補間比較、S10 の最終判定。すべて対応差 (§2.3) |
| Guardrail | 素の強さ (ヒューリスティック / 標準操縦の勝率) | 参照パーティとの差が `BUILD_RAW_GUARD` (既定 −0.10) を下回る候補に「要注意」フラグ。助言エンジン固有の癖を悪用した構築の検出。**判定には使わない** |

例: Advisor Δ +0.042 / Raw Δ −0.005 → 採用。Advisor Δ +0.042 / Raw Δ −0.158 → 要注意 (採用は可、報告に明記)。

### 2.2 評価データの三層 (相手集合の分離)

相手は POOL_PIN (上位実構築、現行 60 構築を抽選) から引く。これを**順位を交互配分**して 3 つの互いに素な
集合に分ける (強さの分布を揃えるため。ハッシュ分割より偏らない):

```
順位 1,4,7,…  → dev        (探索・改修用。S6 / S8 で何度使ってもよい)
順位 2,5,8,…  → validation (候補間比較用。S7。敗因を見て直接チューニングしない)
順位 3,6,9,…  → holdout    (最終判定。S10 で run につき一度だけ使う)
```

実装: `RankedTeambuilder(split="dev"|"val"|"holdout", n_splits=3, top_n=BUILD_POOL_TOP_N)`。
`BUILD_POOL_TOP_N` は既定 120 (各 40 構築。現行の top 60 では各 20 で多様性が足りない)。
シードも層ごとに分ける (`BUILD_SEEDS = {"dev": [..], "val": [..], "holdout": [..]}`、run ごとに記録)。

ルール:
- dev は S6 の各ラウンドと S8 の再測定で使う。ラウンドごとにシードを変える (同じ 50 戦を見続けない)。
- validation は S7 のみ。S8 の修正仮説は **dev の敗因統計**から作り、validation の敗因は見ない。
- holdout は S10 で一度だけ。holdout で悪化なら「不採用 (要再設計)」とし、holdout を使い回して
  再挑戦しない (次 run では別シードの holdout を使う)。

### 2.3 対応差と実用差 ε、段階的サンプル数

候補 A と参照 B を同一相手列・同一シードで戦わせ、対応差 Δ = W_A − W_B の信頼区間で判定する
(現行 `paired_verdict` の拡張)。実用差 ε (`BUILD_EQUIV_EPS`、既定 0.02) を入れる:

```
CI 下限 > +ε          明確に改善
CI 上限 < −ε          悪化
CI が [−ε, +ε] に収まる  実用上同等 (これ以上戦数を増やさない)
それ以外               判定不能 → 次の段階へ
```

段階的サンプル数 (adaptive): 100 → 300 → 600 → 1200。各段階の後に上の分類を行い、
「明確」「悪化」「同等」のいずれかになった時点で止める。1200 でも判定不能なら「同等」として二次基準へ。
(v1 の「300 → 判定不能なら 600 で終了」を置き換え。holdout が独立しているので途中の適応的評価は攻めてよい)

### 2.4 測定系とユーザーに渡す系の同一性

| 要素 | 現状 | v2 |
|---|---|---|
| 行動の方策 | 助言エンジン (damage-calc + RL blend)、RL はピン | 同じ。ピン dir を manifest に記録 |
| 選出 (3体選び) | `teampreview_order` (ヒューリスティック) | **実助言と同じ `advise_selection` (選出モデル + 相性フォールバック)** を `--pick-policy advisor` で使う。既定を advisor にし、旧方式は比較用に残す |
| 選出モデル | 採用後に適応 (S9 相当が最後) | **適応してから holdout (S10)** |
| 相手 | top 60 をシードで抽選 | 三層分割 (§2.2) |

### 2.5 共適応の管理

「この構築の勝率」は構築だけの属性ではない。final_team.json と manifest.json に次を必ず持たせる:

```json
{"team_id": "...", "advisor_version": "<git commit>", "policy_checkpoint": "<pinned dir + sha256>",
 "selection_model": "<path + sha256>", "battle_engine_version": "<pokemon-showdown commit>",
 "ruleset": "gen9championsbssregmb", "meta_snapshot": 27, "pool_pin": "pokedb_s3_single_2026-07-17",
 "evaluation_dataset": {"split": "holdout", "top_n": 120, "n_splits": 3}, "seed_set": {"dev": [...], "val": [...], "holdout": [...]}}
```

助言エンジンや RL 方策を更新したら、採用中の構築を **同じ holdout 手順で再測定**し、差を記録する
(訓練の日次定点と同じ扱い)。

---

## 3. 段の定義 (S0〜S11)

構築記事の構造 (コンセプト → 軸 → 相性補完・役割 → 調整 → 選出パターン → 試運転・改修) に写像する。
外部の構築記事サイトの新規取得は CLAUDE.md により事前承認が要るため、記事本文はユーザーが貼る運用を既定とする。

| 段 | 名称 | 入力 → 出力 | 担当 | 評価セット |
|---|---|---|---|---|
| S0 | 依頼の正規化 (BuildSpec) | 自由文 / フォーム → `request.json` (所持・固定・除外・スタイル・予算・レギュ) | schema + Haiku (自由文のとき。チャットでは主セッション) | — |
| S1 | 環境と対面特徴 | 使用率 DB・対戦ログ・レギュ → `meta_snapshot.json` (上位30種・代表型・共起・脅威・ローカルメタ・合法種)、`matchup_features.json` (§3.1) | ルール / データ | — |
| S2 | コンセプト 3〜5 案 | request + 対面特徴 → `s02_concepts.json` (軸 2〜3 体・勝ち筋 enum・支援役割 enum・苦手) | **Opus** (候補は所持種 id に限定、特徴を根拠に) | — |
| S3 | 多様な 6 体候補 | concepts → `s03_candidates.json` (コンセプトごとにビーム、多様性枠、計 15〜20 並び) | 探索 (ルール) + Sonnet 選抜 (候補外の種は選べない) | — |
| S4 | 型候補列挙と制約充足 | candidates + 型ライブラリ → `s04_sets/*.txt` + `adjustments.json` | ルール (型ライブラリから列挙 → 評価関数 → 上位。合法性は Showdown validate-team) | — |
| S5 | 選出・役割・仮想敵特徴 | sets → `s05_matchups.json` (対面特徴の並び版)、`selection_plan.json` (基本選出 / 対○○) | simulation + ルール → Sonnet 文章化 (表示専用) | — |
| S6 | 粗→精の助言操縦評価 (successive halving) | 15〜20 並び → 50 戦 → 上位 8 → 200 戦 → 上位 3〜4 | **助言操縦** (対応差、参照 = 現行パーティ)。副指標として素の強さ 300 戦 | dev (ラウンドごとに別シード) |
| S7 | validation で精密評価 | 上位 3〜4 + 参照 → adaptive 100→300→600→1200 | 測定 (対応差 + ε) | validation |
| S8 | 敗因統計 → 修正仮説 → ≤2 枠改修 | dev の対戦記録 → `loss_stats.json` (機械) → Opus が仮説 ≤3 → 差し替え (系譜つき) → S4〜S7 を差し替え分のみ再実行。最大 2 反復 | LLM (仮説) + 測定 (検証) | dev (統計) / validation (再比較) |
| S9 | 選出方策の適応 | 決定チームで collect_selection → train_selection (+5% ゲート、TEAM_PROPOSAL_DESIGN §4)。適応後のモデルを manifest に固定 | 学習 | — |
| S10 | **holdout で最終測定** | 決定チーム (適応後の選出モデルで) + 参照 → adaptive、一度だけ | 測定 | holdout |
| S11 | 成果物 | `final_team.json` (§2.5 の同一性情報つき)・`report.md` (構築記事形式)・my_team.json 登録・PROJECT_STATUS | ルール + Sonnet (記事) | — |

### 3.1 対面特徴 (S1): 1v1 行列を多面化する

1v1 の勝敗だけでは「後投げできるか」「上から縛れるか」「起点にされるか」を落とす。脅威ごとに次の
真偽値/数値を**ルールで**計算し (advisor/damage, endgame.duel, calc_stat, 技フラグ)、S2〜S8 で共通に使う:

```json
{"my_id": "kingambit", "threat_id": "garchomp", "set_id": "garchomp#1",
 "lead": false, "switch_in": false, "revenge": true, "setup_stop": true, "speed_control": "slower",
 "hazard": "none", "status": "none", "mega_required": false, "resource_cost": "none",
 "evidence": {"dmg_taken_best": 0.71, "dmg_dealt_best": 0.62, "priority": "suckerpunch"}}
```

- lead: 対面から倒せる (先手 or 耐えて 2 発以内)。switch_in: 最大打点を受けて反撃で 2 発以内。
- revenge: 上から or 先制技で削れた相手を落とす。setup_stop: 積み後 (+1/+2) の相手を止められる。
- speed_control: faster / slower / tie (実数値、スカーフ考慮)。hazard: sets / removes / none。
- status: 撒く変化技 (おにび/どく/眠り)。mega_required: メガシンカ時のみ成立。resource_cost: sash / berry / disguise / none。

### 3.2 S3 の多様性

単純な上位 N 保持はコアの相互作用 (60点+60点=95点) を捨てる。コンセプトごとに独立したビームを持ち、
さらに `PLAY_STYLES` (offense / cycle / stall / balance) と「特定コア枠」で多様性枠を確保する
(「最強候補 10」ではなく「性質の違う有望候補 10〜20」)。既存の進化探索 (evolve_teams) は候補源の一つとして残す。

### 3.3 S8 の敗因統計 (機械が作る)

LLM の敗因診断は文章生成であって因果分析ではない。先に機械が dev の対戦記録から統計を作り、
LLM には「このデータを説明する修正仮説を最大 3 つ」だけ頼む:

```
loss_by_opponent_species / loss_by_opponent_lead / loss_by_archetype (相手構築のタグ)
loss_by_our_lead / loss_by_our_selection (3体組)
ko_source (誰に何で倒されたか) / unused_members (選出されない味方) / mega_timing (メガ使用/未使用と勝敗)
```

必要な実装: `tools/check_advisor_player --battle-log <jsonl>` で対戦単位の記録 (相手 6 体・自分の選出・先発・
ひんし順・KO 元・メガ使用・ターン数・勝敗) を出す (現在は勝敗列のみ)。

差し替えは系譜つき: `{"parent_team_id": "...", "changes": [{"out": "hydreigon", "in": "garchomp", "hypothesis": "..."}]}`。
1 反復 ≤2 枠、最大 2 反復 (ablation に近い局所探索を保つ)。

---

## 4. データ契約

### 4.1 run ディレクトリ

```
logs/build_search/runs/<run_id>/
  manifest.json          git commit / prompt version / model version / simulator commit / advisor version /
                         RNG seeds (dev/val/holdout) / dataset id (POOL_PIN, META_PIN, split) / schema version
  request.json           S0
  meta_snapshot.json     S1
  matchup_features.json  S1
  s02_concepts.json      S2
  s03_candidates.json    S3
  s04_sets/<k>.txt, adjustments.json   S4
  s05_matchups.json, selection_plan.json  S5
  evaluation/s06_r1.json, s06_r2.json, s07.json, s08_<iter>.json, s10_holdout.json
  battles/<stage>_<k>.jsonl   対戦単位の記録
  loss_stats.json        S8
  lineage.json           S8 の系譜
  llm/<stage>_<model>_<n>.json   prompt / response / 検証結果 / トークン数
  final_team.json, report.md     S11
```

### 4.2 LLM 出力の規約 (authoritative と display の分離)

機械が意思決定に使うフィールド (id・enum・構造化値) と、表示専用の自然言語を分ける。検証は前者だけに掛ける。

```json
{"authoritative": {"core_ids": ["metagross", "kingambit"], "mega_id": "metagross",
                   "win_condition": "setup_sweep", "support_roles": ["speed_control", "hazard_control"],
                   "weak_to": ["garchomp", "annihilape"]},
 "display": {"explanation": "この2体を軸に…", "caveats": "…"}}
```

検証器 (`validate.py`): (a) id の存在 (図鑑・使用率 DB)、(b) 所持、(c) 合法性、(d) クローズ、(e) enum の値域。
不合格は理由つきで差し戻し (最大 2 回) → ルール既定値へフォールバック。

---

## 5. 実装構造

```
tools/team_build/
  spec.py           S0: BuildSpec (schema)。自由文の構造化は llm.py 経由
  meta_snapshot.py  S1: meta_snapshot.json
  features.py       S1: 対面特徴 (§3.1、純粋関数。damage/endgame を使う)
  concepts.py       S2: Opus 呼び出し + 検証
  candidates.py     S3: コンセプト別ビーム + 多様性枠 + Sonnet 選抜
  sets.py           S4: 型ライブラリ列挙 → 評価関数 → validate-team
  matchups.py       S5: 並び版の対面特徴・相性選出 → 選出パターン (Sonnet は表示専用)
  evaluate.py       S6/S7/S10: successive halving / adaptive / holdout。check_advisor_player を呼ぶ
  verdict.py        対応差 + ε の分類 (純粋関数、paired_verdict の拡張)
  loss_stats.py     S8: 対戦記録 → 敗因統計 (純粋関数)
  repair.py         S8: 仮説 (Opus) → 差し替え (検証・系譜)
  adapt.py          S9: collect_selection → train_selection のラッパー
  report.py         S11: final_team.json / report.md / 登録
  llm/provider.py   LLMProvider (抽象) — AgentToolProvider (チャット内、Agent ツール) / ClaudeCLIProvider (`claude -p --model`)
  run.py            オーケストレータ: `python -m tools.team_build.run --request req.json [--from S4] [--to S7] [--llm headless]`
scripts/team_build.sh
.claude/skills/build-team/SKILL.md   チャット入口 (/build-team)
```

既存ツールへの変更:
- `tools/check_advisor_player`: `--pick-policy advisor|teampreview` (既定 advisor)、`--opp-split dev|val|holdout`、
  `--battle-log <jsonl>`。
- `champions_agent/env/ranked_teams.RankedTeambuilder`: `split` / `n_splits` (順位交互配分)。
- `tools/team_proposal.paired_verdict`: ε つき分類へ拡張 (旧結果との互換のため旧関数は残す)。
- `champions_agent/config.py`: `BUILD_EQUIV_EPS`, `BUILD_RAW_GUARD`, `BUILD_POOL_TOP_N`, `BUILD_STAGES = (100, 300, 600, 1200)`,
  `BUILD_HALVING = ((20, 50), (8, 200), (4, 600))`, `BUILD_MAX_REPAIRS = 2`, `BUILD_MAX_CHANGES = 2`。
- フロント (Phase 3): `run_team_build` (現行 `run_team_proposal` と同型: 別プロセス・ログ tail・対戦中ガード) と依頼フォーム。

---

## 6. 品質ゲート

| ゲート | 内容 | 段 |
|---|---|---|
| 所持 / 合法性 / クローズ / 型の整合 | v1 と同じ (ルール強制) | S2〜S8 |
| 目的関数の一貫性 | 絞り込みも判定も助言操縦。素の強さは副指標のみ | S6/S7/S10 |
| 三層分離 | dev で探索・改修、validation で比較、holdout は一度 | S6〜S10 |
| 測定系の同一性 | 選出方策 = 実助言、RL ピン、適応後に holdout | S9/S10 |
| 実用差 | ε 未満の差で戦数を増やさない | S6〜S10 |
| 頑健性 | Raw Δ < BUILD_RAW_GUARD は要注意フラグ | S7/S10 |
| 再現性 | manifest に commit / seeds / pins / model 版 / schema 版 | 全段 |
| 監査 | LLM 全入出力・検証不合格履歴・トークン数 | 全段 |

テスト: features / verdict / loss_stats / validate / candidates の列挙は純粋関数として tests/ に追加 (LLM はモック)。
測定を伴う統合は軽量テストと同列にしない。

---

## 7. コストと所要時間 (1 run、実測ベースの見積もり)

助言操縦の測定は 5 並列で約 1 戦/秒 (9/5 実測: 300 戦 × 5 本が 27 分)。学習ループと CPU を分け合う。

| 段 | 戦数 | 所要 |
|---|---|---|
| S6 r1: 20 並び × 50 戦 | 1,000 | 約 17 分 |
| S6 r2: 8 × 200 | 1,600 | 約 27 分 |
| S6 r3: 4 × 600 (または S7 に統合) | 2,400 | 約 40 分 |
| S7: 4 + 参照、adaptive (平均 300〜600) | 1,500〜3,000 | 25〜50 分 |
| S8: 1 反復 (差し替え分の S6/S7 再実行) | 1,000〜2,000 | 20〜35 分 |
| S9: 選出適応 | — | 約 30 分 (実績: 49,000 件収集) |
| S10: holdout (決定 + 参照、adaptive) | 600〜2,400 | 10〜40 分 |
| 合計 | — | **2.5〜4 時間** (v1 の 1〜2.5 時間より長い。目的関数を一貫させた代償) |

LLM: Opus 1〜3 回 (S2、S8)、Sonnet 3〜5 回 (S3、S5、S11)、Haiku 数回。全体の 1 割未満。
料金は本書では扱わない (llm/ のトークン数から実行時に算出)。
短縮策 (要ユーザー承認): 構築実行中は学習を一時停止 (`logs/PAUSE_TRAINING`、40 分で自動解除) して測定を速める。

---

## 8. 段階的導入 (評価設計を先に作る)

| 段階 | 内容 | 成果 |
|---|---|---|
| Phase 0 (1 日) | **評価基盤**: 相手集合の三層分割、`--pick-policy advisor`、対戦単位の記録、ε つき対応差、manifest。v3.1 の使い捨てスクリプトの正式化 | 現行手順が再現可能かつ過適合しない形になる |
| Phase 1 (2〜3 日) | S0/S1 (特徴含む)/S3 (ルール列挙)/S4/S6/S7/S10/S11 (ファイル) + `/build-team` スキル。S2 は主セッションが直接 | チャットで一気通貫 |
| Phase 2 (2〜3 日) | S2/S8 を Opus、S3/S5/S11 を Sonnet のサブプロセスに (LLMProvider 経由)。S8 の敗因統計と系譜。S9 を組み込む | 説明つき提案と改修、holdout つき |
| Phase 3 (1〜2 日) | フロントの依頼フォーム + ヘッドレス実行 + 結果表示。コスト上限 | 一発依頼の入口 |

M-C 切替 (9/9) との関係は v1 と同じ (環境データが薄い間は引き継ぎ保護、シミュレータは上流待ち)。

---

## 9. 未決事項 (ユーザー判断)

1. 実用差 ε (既定 0.02) と頑健性ガード (既定 −0.10) の値。
2. 相手集合: top 120 を 3 分割 (各 40) でよいか。holdout で悪化した場合は「不採用・再設計」でよいか。
3. 構築実行中に学習を一時停止してよいか (都度確認か、run ごとの既定にするか)。
4. 1 run の LLM 予算 (呼び出し上限) と改修反復の上限 (既定 2)。
5. 構築記事の参照は「ユーザーが本文を貼る」運用でよいか (外部取得は要承認)。
6. 既存の進化探索 (evolve_teams) を S3 の候補源として残す (残す案)。
7. 入口の優先順位: チャット (Phase 1) → フロント (Phase 3)。

---

## 10. v1 → v2 の変更履歴 (レビュー反映)

| レビュー指摘 | 反映 |
|---|---|
| S6 のヒューリスティック順位付けは目的関数と矛盾 | 助言操縦の successive halving に変更 (§2.1, S6)。素の強さは頑健性の副指標に降格 |
| S8 で評価セットが訓練データ化 | dev / validation / holdout の三層 (§2.2)。S8 は dev の統計だけを見る |
| 選出学習の適応を最終測定の前に | S9 → S10 の順に変更。測定の選出方策も実助言と一致させる (§2.4、`--pick-policy advisor`) |
| 「id 限定」の再定義 | authoritative / display の分離 (§4.2) |
| 対応差の CI と実用差 ε | §2.3。adaptive 100→300→600→1200 |
| 1v1 被覆行列は補助に留める | 多面的な対面特徴 (§3.1)。テラスはメガ要求に読み替え |
| ビーム探索の多様性 | コンセプト別ビーム + スタイル/コア枠 (§3.2) |
| 型はライブラリから選択 | S4 を「列挙 → 評価関数 → 上位」に明文化 |
| 敗因帰属を LLM に信用させない | 機械の敗因統計 → LLM は仮説 ≤3 (§3.3)。対戦単位の記録を追加 |
| ≤2 枠・2 反復と系譜 | 維持。lineage.json を追加 |
| 共適応の管理 | manifest / final_team.json の同一性情報 (§2.5)。助言更新時の再測定 |
| LLMProvider アダプタ | llm/provider.py |
| manifest の徹底 | §4.1 |
