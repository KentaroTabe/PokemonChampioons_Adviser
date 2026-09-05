# パーティ構築提案の方針転換案 — Claude Code 主導・粒度別サブプロセス構成

作成: 2026-09-06。対象: 「パーティ構築はフロントエンドからではなく Claude Code のチャットとして行う。
ユーザーは条件を指定するとパーティ案が返る。裏では Opus / Sonnet / Haiku が粒度に応じた
サブプロセスとして走る」という方針転換の評価と、採用する場合の具体設計。

---

## 0. 結論

**条件つきで現行より良い。採用を推奨する。** 条件は次の 3 つ。

1. **最終判定は測定で行い、LLM は判定しない。** 判定軸は「助言エンジンが操縦した固定チーム勝率」
   (advisor-as-player、同一相手列・RL ピン固定・対応差)。v3.1 の決定で使った軸をそのまま正式化する。
2. **LLM の出力は必ずルールで検証してから使う。** 種族・技・持ち物・特性は図鑑と使用率 DB に
   存在するものだけを許し、所持・レギュレーション合法性・アイテムクローズ・性格/配分の整合を
   ルール側で強制する (LLM に「作らせない」、LLM は「選ぶ・説明する」)。
3. **入口はチャットを主とし、フロントエンドは同じパイプラインへの一発依頼フォームに留める。**
   フロントには判断ロジックを置かない (現行の GA ジョブと同じく別プロセスで走らせ、進捗と結果だけ表示)。

理由 (§2 で詳述): 現行の探索器は「素の強さ」を最適化していて、ユーザーが実際に得る価値
(助言エンジンに操縦されたときの勝率) と一致しない。v3.1 決定の実測がそれを示した
(ヒューリスティック操縦ではドドゲザンが +0.11〜+0.23 で突出、助言操縦では 5 候補が誤差の範囲)。
一方、コンセプト・役割分担・選出パターンの言語化は現行にまったく無く、ここが LLM の出番。

---

## 1. 現行の実態 (実装から)

| 要素 | 現状 | 出典 |
|---|---|---|
| 入口 | フロントの「構築提案」パネル (段階1/2、population/generations/battles/locked) → socket `run_team_proposal` → 別プロセス `tools.team_proposal --propose` | server.py 581-640, index.html 58 |
| 探索 | 進化探索 (1枠入替の変異)。初期集団は上位実構築 + 使用率メタ生成 | tools/evolve_teams.py |
| 評価器 | ローカル Showdown の実対戦。両サイド同一方策 (evaluate_team は SimpleHeuristics、evolve は RL+相性選出)。**助言エンジンは操縦しない** | tools/evaluate_team.py, evolve_teams.py |
| ゲート | 使用率鮮度 7日 / 型プール 40種 / 評価 40戦 / 受入 100戦 (対応差)。運用可否は `--check` | tools/team_proposal.py, docs/TEAM_PROPOSAL_DESIGN.md §3 |
| 診断 | 1v1 行列・素早さ関係・耐久チェック・補完候補 (共起) | tools/team_report.py, advisor/team_advice.py |
| 採用後 | 選出モデルの適応 (collect_selection → train_selection、+5% ゲート) | TEAM_PROPOSAL_DESIGN §4 |
| 直近の実運用 (v3/v3.1) | 候補生成 (使用率 + 整合型 + クローズ解決) → 助言エンジン操縦で 300+600 戦 → 対応差で判定 → Claude が想定運用と代替案を書く。**すべてチャットと使い捨てスクリプトで実施** | logs/build_search/final_team.json, scripts/team_candidates_measure.sh |

現行の限界 (実測に基づく):

- **評価軸の不一致**: 探索器の目的関数 (素の強さ) と、ユーザーの得る価値 (助言操縦の勝率) が違う。
  9/5 実測: ヒューリスティック操縦 ドドゲザン 0.713 / サザンドラ 0.600 / カバルドン 0.483、
  助言操縦 (併合900戦) 0.754 / 0.742 / 0.713 — 順位も差の大きさも一致しない。
- **意味の欠落**: GA は「なぜその並びか」「どう選出するか」を出せない。想定運用は毎回チャットで手書き。
- **条件の扱い**: 所持/未所持・固定枠・好みのスタイルは手作業 (v3 は所持 22 種を目視でフィルタ)。
- **再現性**: 判定に使った候補生成・測定・判定文がスクラッチパッドに散在。
- **入口の表現力**: フロントの入力は段階と数値パラメータのみ。「アーマーガアは未所持」「格闘に強く」は渡せない。

---

## 2. 提案の評価

### 2.1 良くなる点

| 観点 | 現行 | 転換後 |
|---|---|---|
| 目的関数 | 素の強さ (ヒューリスティック/RL操縦) | **助言操縦の勝率** (実際に使う経路) を最終判定に |
| 説明 | 無し | コンセプト・採用理由・選出パターン・苦手を構築記事の形で出力 |
| 条件 | locked / max_changes のみ | 所持・除外・固定・スタイル・予算を自由文/フォームで指定 |
| 反復 | ボタン再実行 | チャットで「格闘が重いので差し替え案」のような対話的改修 |
| 再現性 | 散在 | run ディレクトリに入力・中間 JSON・LLM 入出力・測定を全保存 |

### 2.2 悪くなり得る点と対策

| リスク | 対策 |
|---|---|
| LLM の幻覚 (存在しない技・非合法・未所持) | 出力は id 列に限定し、図鑑/使用率 DB/所持リスト/Showdown `validate-team` で検証。不合格は差し戻し (最大2回) → ルール既定値へフォールバック |
| LLM の好みで判定が揺れる | 判定は測定のみ。LLM は「候補を出す・絞る・説明する」に限定。判定不能なら倍増1回 → 二次基準を**明文化して**適用 (v3.1 と同じ手順) |
| コスト | LLM 呼び出しは 1 run で Opus 1〜3 回・Sonnet 3〜5 回・Haiku 数回に上限。入力は JSON 要約のみ (画像なし)。全入出力をログ |
| 所要時間 | 測定が支配的 (現行と同じ 1〜2.5 時間)。LLM 部分は数分。バックグラウンド実行と進捗表示 |
| 複雑化 | 既存ツール (evaluate_team / evolve_teams / team_report / team_candidates_measure / register_my_team) を部品として再利用し、新規はオーケストレーションと検証に絞る |
| 9/9 の M-C 切替 | 環境データが薄い期間は S1 が旧シーズンの健全な型を使う (build_meta の引き継ぎ保護)。シミュレータの M-C 対応は上流待ち (REGULATION_CHANGE_RUNBOOK) |

### 2.3 「フロントから」と「チャットで」の整理

- **チャット (主)**: 条件を会話で詰められる。改修ループを対話的に回せる。LLM サブプロセスは
  Claude Code の Agent (モデル指定) で起動。
- **フロント (従)**: 「構築依頼」フォーム → 依頼 JSON → 別プロセスで同じパイプラインを
  ヘッドレス実行 (`claude -p --model …`、tools/audit_subtask と同じ方式) → 進捗と結果を表示。
  対話は無し (一発依頼)。対戦中は起動しない (現行ガードを流用)。

---

## 3. タスク分解 (上位勢の構築記事の構造に倣う)

構築記事の典型構造は「コンセプト → 軸 (メガ枠/エース) → 相性補完・役割分担 → 各個体の採用理由と
調整意図 → 選出パターン (基本選出 / 対○○) → 苦手・課題 → 戦績」。構築の作業もこの順で進む。
これをサブプロセスに写像し、各段で **入力 → 出力 (JSON) → 担当 (ルール / LLM とその粒度)** を固定する。
(外部の構築記事サイトを新たに取得するには CLAUDE.md の規約により事前承認が要る。本案は記事構造の
一般知識に基づく。記事本文を参考にしたい場合はユーザーが貼り付ける運用が最も安全)

| 段 | 名称 | 入力 | 出力 | 担当 | 根拠 |
|---|---|---|---|---|---|
| S0 | 依頼の正規化 | 自由文 or フォーム (所持/固定/除外/スタイル/予算/レギュ) | `request.json` (id 列・数値) | ルール (my_team.json の所持一覧・図鑑照合) + **Haiku** (自由文→構造化。チャット時は主セッションが直接行う) | 解釈は軽い。誤解は S0 の確認表示で潰す |
| S1 | 環境スナップショット | 使用率 DB (最新の健全スナップショット)、対戦ログ (直近の相手)、レギュ | `meta.json`: 上位30種+代表型+共起、脅威リスト、ローカルメタ、合法種集合 | **ルール** (build_meta / meta_sets / team_report の材料) | 全部データ。LLM 不要 |
| S2 | コンセプト候補 | request + meta + **被覆グラフ** (各所持種について「上位30種のうち誰に勝ち/負けるか」の 1v1 行列、ルールで計算) | `concepts.json`: 3〜5 案。各案 = 軸 2〜3体 (メガ枠含む)・勝ち筋・想定される苦手 | **Opus** (意味的な組み立て)。候補は所持種 id に限定、行列を根拠として引用させる | ここが人間の構築者の「発想」に相当し、現行に無い |
| S3 | 並びの完成 | concepts + 被覆グラフ + 役割タグ (role_tagger) + 共起 + 素早さ帯 | `lineups.json`: 各コンセプト 2〜3 並び (6体) | **ルール** (ビーム探索: 脅威被覆の増分 + 共起 + 役割の充足 [起点作り/クッション/ストッパー/掃除/崩し]) → **Sonnet** が上位 K からの選抜と採用理由 (候補外の種は選べない) | 列挙は機械、良し悪しの説明は LLM |
| S4 | 型の決定 | lineups + meta_sets (整合型) + 図鑑 | `sets/<lineup>.txt` (Showdown 形式、能力ポイント表記) + `adjustments.json` (確定数・耐久ライン) | **ルール** (choose_coherent_spread、クローズ解決 `_clause_alternative`、ダメージ計算で調整表、Showdown `validate-team` で合法性) | 数値は LLM に任せない。技の差し替え候補が要る場合のみ Sonnet が「使用率5%以上の合法技」から選ぶ |
| S5 | 仮想敵チェックと選出案 | sets + 上位10アーキタイプ (上位実構築) | `matchups.json` (1v1 行列・穴) + `selection_plan.json` (基本選出 / 対○○) | **ルール** (endgame.duel の行列、advisor/selection の相性選出で各相手構築に対する3体) → **Sonnet** が選出パターンと勝ち筋を文章化 (行列の数値だけを根拠に) | 想定運用の自動化 |
| S6 | スクリーニング測定 | sets | `screen.json` (各並びの素の勝率) | **ルール** (evaluate_team_text、同一相手列 300戦、並列) | 数分で 6〜10 並びを 4〜6 に絞る |
| S7 | 本測定 | 上位並び + 現行パーティ (参照) | `measure/*.json`, `verdict.json` (対応差) | **ルール** (scripts/team_candidates_measure.sh: 同一相手列・RL ピン・round1 300 → 判定不能なら round2 600) | **最終判定**。v3.1 の手順を正式化 |
| S8 | 改修ループ | verdict + 敗因帰属 (対戦ログ: どの相手に負けたか、team_report の穴) | 差し替え指示 (各並び ≤2 枠、所持種のみ) → S4〜S7 を再実行 (最大 2 反復) | **Opus** (測定結果を読んで差し替えを決める) + ルール (検証・再測定) | 人間の「試運転→改修」 |
| S9 | 成果物 | 全段の JSON | `final_team.json` (版・測定・想定運用・代替案)、`report.md` (構築記事形式)、my_team.json 登録、選出学習の適応 | **Sonnet** (report.md) + **ルール** (final_team.json、register_my_team、collect_selection → train_selection) | 記録と採用 |

粒度とモデルの対応 (要約):

- **Opus**: S2 (コンセプト)、S8 (改修の意思決定)。1 run で 1〜3 回。長い文脈の意味的判断。
- **Sonnet**: S3 (選抜と理由)、S5 (選出パターン文章化)、S9 (記事)。3〜5 回。構造化データの言語化。
- **Haiku**: S0 (自由文の構造化、フロント経由のとき)、進捗の要約。数回。
- **ルール**: S1・S3 列挙・S4・S5 行列・S6・S7・検証全般・登録。**判定と数値はすべてここ。**

---

## 4. データ契約 (run ディレクトリ)

`logs/build_search/runs/<run_id>/` に段ごとの JSON を置く (git 管理外、logs/ 配下)。

```
request.json      S0  {"owned":[id], "locked":[id], "banned":[id], "style":"balance|offense|cycle|stall",
                       "budget":{"screen_battles":300,"measure_battles":300,"max_repairs":2},
                       "regulation":"gen9championsbssregmb", "notes":"自由文"}
meta.json         S1  {"snapshot_id":27, "top":[{"id","usage","set":{...},"teammates":[...]}], "threats":[id],
                       "local_meta":[{"id","count"}], "legal":[id]}
coverage.json     S1  {"matrix":{"my_id":{"opp_id":"win|lose|even"}}}   (endgame.duel、代表型同士)
concepts.json     S2  [{"name","core":[id],"mega":id,"win_condition","weak_to":[id],"rationale"}]
lineups.json      S3  [{"concept","members":[id×6],"roles":{id:role},"score":{...},"rationale"}]
sets/<k>.txt      S4  Showdown 形式 (能力ポイント表記)。adjustments.json に調整表
matchups.json     S5  1v1 行列と穴。selection_plan.json に基本選出 / 対○○
screen.json       S6  {"<k>":{"win_rate","n","outcomes"}}
measure/<k>.json  S7  check_advisor_player の出力 (round1/round2)。verdict.json に対応差と判定
llm/<step>_<model>_<n>.json   各 LLM 呼び出しの prompt / response / 検証結果
final_team.json   S9  版・測定・想定運用・代替案 (現行と同じ形式)。report.md は構築記事形式
```

LLM 出力の共通規約: **id 列だけを返す JSON** (種族/技/持ち物/特性は showdown id)。自由文は
`rationale` フィールドに限定。検証器 (`tools/team_build/validate.py`) が (a) id の存在、(b) 所持、
(c) 合法性、(d) クローズ、(e) 整合型 を確認し、不合格は理由つきで差し戻す (最大2回)。

---

## 5. 実装構造

```
tools/team_build/
  request.py        S0: フォーム/自由文 → request.json (Haiku 呼び出しは llm.py 経由)
  meta_snapshot.py  S1: meta.json / coverage.json (既存: build_meta, team_report の行列部品)
  concepts.py       S2: 被覆グラフの要約を作り Opus に投げる。検証つき
  complete.py       S3: ビーム探索で並びを列挙 → Sonnet 選抜
  assign_sets.py    S4: evaluate_team.build_team_text + 整合型 + クローズ + 調整表 + validate-team
  matchups.py       S5: 行列・穴・相性選出 → Sonnet が選出パターン文章化
  screen.py         S6: evaluate_team_text 並列 (既存 scratchpad の slot6_v3_finals を正式化)
  measure.py        S7: scripts/team_candidates_measure.sh の呼び出しと verdict (v3_verdict2 を正式化)
  repair.py         S8: 敗因帰属 + Opus 差し替え決定 (検証つき)
  report.py         S9: final_team.json / report.md / 登録 / 採用フロー
  validate.py       LLM 出力・並び・型の検証器 (純粋関数中心、テスト対象)
  llm.py            LLM 呼び出しの抽象: mode=agent (チャット内、Agent ツール) / headless (`claude -p --model`)。
                    入出力を llm/ に保存、JSON スキーマ検証、再試行
  run.py            オーケストレータ: `python -m tools.team_build.run --request req.json [--from S4] [--llm headless]`
scripts/team_build.sh      run.py のラッパー (venv・ログ除去)
.claude/skills/build-team/SKILL.md   チャット入口 (/build-team)。S0 を会話で行い、以降は run.py を段階実行。
                                     S2/S8 はチャット内 Agent (model=opus)、S3/S5/S9 は Agent (model=sonnet)
```

- **チャットモード**: 主セッションが `/build-team 条件…` で S0 を会話で確定 → `run.py --to S1` → S2 を
  Agent(opus) → 検証 → `run.py --from S3 --to S7` (バックグラウンド、完了通知) → S8 判断 → S9。
  各段の JSON をユーザーに要約提示し、途中で条件変更を受け付ける。
- **ヘッドレスモード** (フロント): server.py に `run_team_build` (現行 `run_team_proposal` と同型:
  別プロセス・ログ tail・対戦中ガード) を追加し、`run.py --llm headless` を起動。LLM は
  `claude -p --model claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5-20251001` を段ごとに使う。
  フロントは依頼フォーム (固定/除外/スタイル/予算) と結果 (report.md の要約 + 採用ボタン) のみ。
- **既存の GA (evolve_teams)** は S3 の候補源の一つとして残す (コンセプト外の発見用、任意)。
- **選出モデルの適応** (TEAM_PROPOSAL_DESIGN §4) は S9 の採用フローとして温存。

---

## 6. 品質ゲート

| ゲート | 内容 | 段 |
|---|---|---|
| 所持 | request.owned にない種は候補にしない (v3 でハッサム/アーマーガア等を手で除外した反省) | S2〜S8 |
| 合法性 | Showdown `validate-team` (対象レギュのフォーマット id) を通らない並びは不採用 | S4 |
| クローズ | 種族・持ち物の重複禁止 (既存 `_clause_alternative`) | S4 |
| 型の整合 | 性格×配分×持ち物の整合 (build_meta の規則) | S4 |
| 測定 | 同一相手列・RL ピン・現行パーティを参照として同時測定。判定は対応差 (+0.03 かつ CI 下限 > 0 で採用推奨。判定不能は倍増1回 → 二次基準を記録) | S7 |
| 再現性 | seed・ピン dir・META_PIN・snapshot id を run に記録 | S6/S7 |
| コスト | LLM 呼び出し回数と入出力トークンを run に記録。上限超過で停止 | 全段 |
| 監査 | LLM の全入出力を保存。検証不合格の履歴を残す | 全段 |

テスト方針: 検証器・列挙・行列・判定は純粋関数として tests/ に追加 (LLM はモック)。
測定を伴う統合テストは軽量テストと同列に扱わない (既存規約)。

---

## 7. コストと所要時間の見積もり (1 run)

| 項目 | 見積もり | 備考 |
|---|---|---|
| S0〜S5 (ルール + LLM) | 5〜10 分 | LLM: Opus 1 回 (入力 15〜25k トークン程度)、Sonnet 3〜4 回 (各 5〜15k)、Haiku 数回 |
| S6 スクリーニング | 3〜6 分 | ヒューリスティック操縦 300戦 × 6〜10 並び (並列) |
| S7 本測定 | 30〜60 分 | 助言操縦 300戦 × 4〜6 並び + 参照 (並列、判定不能なら +600戦で +60 分) |
| S8 改修 1 反復 | +40〜70 分 | S4〜S7 の再実行 (差し替えた並びのみ) |
| 合計 | 1〜2.5 時間 | 現行の段階2 (GA) と同程度。LLM 部分は全体の 1 割未満 |

料金は本書では扱わない (モデル価格は変わるため、実行時に llm/ のトークン数から算出する)。

---

## 8. 段階的導入

| 段階 | 内容 | 成果 |
|---|---|---|
| Phase 0 (半日) | v3.1 の使い捨てスクリプト (候補生成・スクリーニング・判定) を tools/team_build に正式化。データ契約を固定 | 再現可能な「今の手順」 |
| Phase 1 (2〜3 日) | S0/S1/S3(ルール列挙)/S4/S6/S7/S9(ファイル) + `/build-team` スキル。S2 は主セッションが直接行う (サブエージェント無し) | チャットで一気通貫の構築が回る |
| Phase 2 (2 日) | S2/S8 を Opus、S3/S5/S9 を Sonnet のサブプロセスに分離。report.md (構築記事形式)。改修ループ | 説明つき提案と改修 |
| Phase 3 (1〜2 日) | フロントの依頼フォーム + ヘッドレス実行 + 結果表示。コスト上限 | 一発依頼の入口 |

M-C 切替 (9/9) との関係: Phase 0〜1 は M-B データで作って検証できる。M-C の環境データが
揃う (pokedb 100 構築超・cbd の技データ健全化) まで、S1 は旧シーズンの型を引き継ぐ。
シミュレータの M-C 対応 (上流) が入るまで S6/S7 は M-B 内容で測る点は明示する。

---

## 9. 未決事項 (ユーザー判断)

1. 入口の優先順位: チャット (Phase 1) → フロント (Phase 3) の順でよいか。
2. 1 run の LLM 予算 (呼び出し回数の上限) と、改修ループの反復上限 (既定 2)。
3. 構築記事の参照: 外部サイトの取得は規約上要承認。**ユーザーが記事本文を貼る**運用でよいか。
4. 既存 GA (evolve_teams) を候補源として残すか、廃止するか (残す案)。
5. 二次基準の優先順 (所持済み > フォールバック数 > 役割の単純さ > 次レギュの弱点 …) を固定してよいか
   (v3.1 で用いた順)。
