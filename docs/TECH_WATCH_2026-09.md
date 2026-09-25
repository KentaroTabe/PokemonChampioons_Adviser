# 技術動向の棚卸し (2026-09-24): プロジェクト開始 (2026-07-15) 以降に出たもの・使えるのに使っていないもの

前提 (現状): 外部 LLM は claude CLI のヘッドレス実行だけ (S4 概念・介入仮説 = Opus 5、S13 記事・記事の抽出 = Sonnet 5、
視覚監査 = Opus 5)。画面認識は Apple Vision OCR + OpenCV、行動評価は決定的計算 + 探索 + RL ブレンド。
Python 3.9.6 (Xcode 付属)、Apple Silicon、macOS 26.5.1、claude CLI 2.1.270。

## A. 提案 (優先順)

### 1. claude CLI の構造化出力 (`--json-schema`) を構築の LLM 呼び出しに使う — 最優先・小 → **実装済み (2026-09-24)**

- 実装: `ClaudeCLIProvider` が既定 schema `OUTPUT_SCHEMA` を `--json-schema` で渡し、応答の `structured_output` を使う。
  `call(schema=...)` で段ごとに差し替え。記録に structured / schema_hash / cost_usd。docs/TEAM_BUILDING_IMPLEMENTATION.md §12
- 状況: 開始時から存在 (v2.1.205、7 月上旬に不正スキーマの無言フォールバックが修正済み)。このリポジトリでは未使用だった
- 効果: 9/22 のインシデント (extract_json が文字列内の括弧で本文を捨てた) の類が構造的に消える。
  `authoritative` の id / enum をスキーマで縛れるので、検証器の一部もモデル側で保証できる
- 実測 (2026-09-24): haiku で `claude -p ... --output-format json --json-schema '{...}' --tools ""` →
  応答 JSON の `structured_output` に `{"n": 2}` が入った (内部は tool_use 1 回、`--tools ""` でも動く)
- 変更点: `ClaudeCLIProvider.complete` に段ごとの schema を渡し、`raw["structured_output"]` を優先、無ければ従来の
  extract_json。schema は最初 `{"authoritative": object, "display": object}` の required だけの緩いものから始め、
  validator の条件を順次スキーマへ移す。schema 不正は CLI が失敗するので、テストで各段の schema の妥当性を固定する

### 2. Claude Opus 5.5 (9/22) への切替を、測定つきで — 中

- $4 / $20 per MTok (Opus 5 は $5 / $25)、キャッシュ読み $0.20 (Opus 5 は $0.50)、1M 文脈、思考は常時 on。
  「多くの作業で Fable 5.1 相当」は Anthropic (報道) の主張で、この用途では未測定
- 今の費用: arch_0918 の LLM 記録 (Opus 13 呼び出し: キャッシュ書込 644k・読み 262k・出力 137k トークン) から
  Opus 5 で 1 run 約 $8〜10 → Opus 5.5 で約 $6〜8 (試算。CLI の `total_cost_usd` を記録していないため、5 分 / 1 時間の
  どちらのキャッシュで書かれたかで幅がある)。金額は小さく、狙いは概念の質・多様性の方
- 手順: 計画済みで未実装の regression (固定 BuildSpec で concept diversity / valid candidate rate / downstream WR、
  docs/TEAM_BUILDING_IMPLEMENTATION.md §「モデル更新への耐性」) を先に作り、それで切替を判定する。
  視覚監査は 8/18 と同じ同一フレーム 30 枚の比較をやり直す (haiku は幻覚、sonnet は主要な乖離、opus が最良、だった)

**結果 (2026-09-24、`python -m tools.team_build.llm.regression --run-id arch_0918`、固定入力 = arch_0918 の S1〜S3、11 軸):**

| 腕 | LLM 系統 | 提案数 | core 距離 | 使用種 | 軸 | 1 回通過 | 再試行 / 呼出失敗 | 1 呼び出し | 合計 | 費用 |
|---|---|---|---|---|---|---|---|---|---|---|
| 元 run の再生 (Opus 5 既定) | 71 | 87 | 0.956 | 52 | 11 | 0.82 | 2 / 0 | 約 150 秒 | 1,952 秒 | 約 $8.5 (推定) |
| Opus 5 既定 (再測定) | 74 | 88 | 0.956 | 53 | 11 | 1.00 | 0 / 0 | 154 秒 | 1,697 秒 | $8.49 |
| Opus 5.5 既定 (旧停止規則) | 39 | 56 | 0.948 | 39 | 7 | 0.57 | 3 / 1 | 44 秒 | 440 秒 | $4.05 |
| Opus 5.5 既定 (軸を一巡) | 59 | 87 | 0.952 | 50 | 10 | 0.82 | 2 / 0 | 45 秒 | 580 秒 | $5.91 |
| **Opus 5.5 xhigh (軸を一巡)** | **82** | 87 | 0.950 | 50 | 11 | 0.91 | 1 / 0 | 219 秒 | 2,627 秒 | $10.57 |

- Opus 5 は 2 標本 (71 / 74) で安定。Opus 5.5 の既定 effort は同じ提案数でも既出 core との重複が増え (stall の軸で 75%、anti_meta で
  100%)、系統が 2 割減る。旧停止規則では 1 軸の重複率で残り 4 軸が打ち切られた → 軸を一巡するまで止めない規則
  (config `BUILD_ARCHETYPE_FULL_PASS`) を入れた
- Opus 5.5 の xhigh は 11 軸中 10 軸で重複 0 (系統 82、Opus 5 比 +11〜15%)、検証器も 1 回でほぼ通る。速さ・安さの利点は消え、
  Opus 5 比で +$2 / +16 分
- **判断: S4 だけ Opus 5.5 + xhigh** (config `BUILD_LLM_STAGE_MODELS` / `BUILD_LLM_EFFORT`)。狙いは多様性で、run 全体
  (27 時間) に対して +16 分は誤差。介入仮説 (tier opus) は未測定なので Opus 5 のまま。戻すのは config の 2 行
- 視覚監査は同一の 5 対戦 20 枚で Opus 5 (331 秒) と 5.5 (112 秒) を比較し、検出が同等 (幻覚なし) なので `AUDIT_MODEL` を 5.5 に
- 費用の実測 (記録の cost_usd): S4 1 呼び出し Opus 5 $0.60〜0.92、Opus 5.5 既定 $0.42〜0.51、5.5 xhigh $0.62〜0.96。
  測定全体で約 $33
- **実 run での確認 (arch_0924、2026-09-24 17:51 起動、snapshot 46、条件は arch_0918 と同じ)**: S4 は 11 ラウンド、LLM 系統 73
  (arch_0918 の Opus 5 既定は 71)、提案数 87 (同数)、core 距離 0.952 (0.956)、使用種 52 (52)、軸 10 (anti_meta が全部重複。
  一巡ルールで special まで続行)、重複 0% のラウンド 8/11 (4/11)、1 回通過 0.91 (0.82)、12 呼び出し 47 分 / $10.74 (13 呼び出し
  33 分 / 約 $8.5)。S5 候補 628 → 89 (587 → 79)。**系統数の差は +2 で標本のばらつきの範囲** (Opus 5 は 71 / 74、5.5 xhigh は
  82 / 73)。重複の少なさは再現したが、費用 +$2、時間 +14 分。下流 (S8a 生存、holdout) を見てから、差が無ければ S4 を Opus 5 既定に
  戻す (config 2 行)
- **下流の結果 (arch_0924、2026-09-25 14:27 完走)**: 最終候補 3 並びが全部 holdout PASS。1 位 L06_C020 +0.288 [+0.231, +0.346]、
  2 位 L05_C028 +0.282 [+0.224, +0.340] (どちらも今回の S4 の系統から)、3 位は持ち込んだ前回 1 位 L69_C029 +0.252 [+0.193, +0.310]
  (arch_0918 では +0.173。参照が弱くなった分 Δ は膨らむので、比較は同じ環境の L69 に対して行う)。新しい系統の 1・2 位は L69 を
  +0.03〜0.04 上回るが CI は重なり、判別できる差ではない。S8a の生存 5/8 も前回 (6/8) と同水準。**判断 (2026-09-25): S4 を Opus 5 既定に
  戻す** (`BUILD_LLM_STAGE_MODELS` = {}、`BUILD_LLM_EFFORT["s04_concepts"]` = None。軸を一巡する停止規則は残す)。理由: 探索の
  系統数 +2、下流に差なし、費用 +$2.2 / 時間 +14 分。5.5 xhigh に戻すのは config 2 行 (使える候補が 345 種に広がった次の run で
  S4 の入力が増えたら再評価)

### 3. effort の段階を段ごとに設定する — 中 → **仕組みは実装済み (2026-09-24)、値は測定後に設定**

- Opus 5 以降は effort (low〜max) が主制御 (7/24)。CLI に `--effort` がある。S4 概念は xhigh / max、S13 記事は low〜medium で
  時間短縮 (今は 1 呼び出し 187 秒)、介入仮説は high など
- config `BUILD_LLM_EFFORT` (段ごと、None = CLI 既定) を provider が `--effort` に渡す。値は 2 の regression
  (`--effort xhigh` / `--stage s13 --effort low` の腕) で測って決める
- **結果 (2026-09-24)**: S4 は Opus 5.5 で xhigh が既定より系統 +23 (59 → 82) → xhigh を設定。S13 記事 (Sonnet 5、L69_C029) は
  既定 119 秒 / $0.23 / 本文 3,361 字、low 38 秒 / $0.15 / 3,289 字で長さは同じだが、low は個体ごとの小見出し (### カイリュー …)
  が落ちて平坦になる → 既定のまま (1 run に 1 回なので時間の差は誤差)

### 4. Fable 5.1 (9/1) を S4 だけ試す — 低 → **小規模テストで利点なし、見送り (2026-09-24)**

- $10 / $50 (Opus 5 の 2 倍、キャッシュ読みは $0.25)。1 run 約 $15 と試算。概念の質が上がるかは未測定。2 の後で
- 結果: 最初の 3 軸 (setup_sweep / trick_room / weather) だけ Fable 5.1 既定で回し、系統 19 (Opus 5 既定 19、5.5 既定 18、
  5.5 xhigh 19 と同じ)、1 呼び出し 98 秒・$1.27 (Opus 5 の 1.8 倍、5.5 既定の 2.8 倍)。多様性の差が出ないので全軸の測定には進めない

### 5. 呼び出しの衛生 — 低 → **実装済み (2026-09-24)**

- `--tools ""` にすると system prompt が 12.8k トークン (今の `--disallowedTools` 列挙は 24.7k、どちらも 9/24 に haiku で実測。
  9/6 の実測は約 17k)。差の約 12k トークンは Opus 5 の 1 時間キャッシュ書込 ($10/M) で 1 呼び出し約 $0.12、1 run (13 呼び出し) 約 $1.5
  → config `BUILD_LLM_CLI_TOOLS = ""` で provider が `--tools ""` を渡す
- `--max-budget-usd` を 1 呼び出しの上限として付ける (再試行の暴走に対する保険) → config `BUILD_LLM_MAX_BUDGET_USD` (5.0)
- 記録に CLI の `total_cost_usd` を残す → 記録の cost_usd。2 の試算を次の run から実測に置き換えられる

### 6. Python 3.9 からの移行 (前提整備) — 中 → **3.12 の venv を構築・確認済み (2026-09-24)、切替は常駐停止時に**

- Anthropic Python SDK 1.0 (8/20) は 3.10+、Jev SDK も 3.10+。3.9 は 2025-10 に EOL
- `brew install python@3.12` → `scripts/venv_rebuild.sh /opt/homebrew/bin/python3.12 .venv312` (依存は requirements-full.txt、
  移行前と同じ主版に固定。pyobjc は 12.2、torch 2.8、opencv 5.0、poke-env 0.10)。CI サブセット + 画面認識の実データテスト
  (test_advisor / test_ocr_parse / test_rl_bridge / test_my_team / test_frame_intake / test_events) は 3.9 と同じ結果
  (test_team_proposal の 1 件と test_look_more は 3.9 でも落ちていた既存の問題。同日に修正済み)
- 切替後の初回の日次ジョブ (9/24): 5:00 deploy (停止中のため起動せず)、6:30 使用率更新 (snapshot 46、262 種、エラーなし)、
  13:00 evolve (run_20260924_130713、エラーなし) はいずれも 3.12 で正常
- 切替は `scripts/venv_switch.sh .venv312` (リンク方式、戻しは --rollback)。docs/OPERATIONS.md「Python 環境」

## B. 新しいが今は使わない (理由つき)

| 技術 (時期) | 見送る理由 |
|---|---|
| Jev (9/15) | [JEV_EVALUATION.md](JEV_EVALUATION.md)。入力が画像・数値で、判定は決定的処理に置いている。日本語未評価 |
| Gemini 3.8 Flash (9/2) | 画像・動画入力可で $0.75 / $3.75 だが TTFT 13.3 秒 (思考トークン) → リアルタイム不可。監査の代替はベンダー追加 + 8/18 同等の比較が必要 |
| Claude Code の 8〜9 月の新機能 (/design、クロスセッション、plugin eval、`--restricted`、Fable 5.1 対応) | 運用に関係が薄い。`--restricted` は評価ハーネス用で settings を無視するが CLAUDE.md の扱いは未確認 |
| Files API GA、computer / browser use toolset (8/19)、Managed Agents | 用途なし (監査はローカルのフレームを CLI が読む) |
| poke-env 0.16 (8/20: Smogon 使用率 API・learnset 生成・Gen9 既定) | champions_agent は >= 0.8.3 で固定。学習を止めている間は更新不要。learnset は自前で持っている |
| foul-play | 2026 年は Showdown データの同期のみ。設計変更なし |
| ローカル VLM OCR (DeepSeek-OCR 2、1 月) | Apple Vision の精度は足りており、誤読の主因は負荷 (取りこぼし)。1 フレーム秒単位の VLM は 10 fps 経路に載らない |
| 実時間 VLM 映像理解 (arXiv 2609.13986、9/12) | 最初の文まで 0.9〜1.0 秒 (サーバー GPU)。Mac の CPU 経路では未検証。設計の参考にとどめる |
| PokéAgent Challenge の 22M 軌跡 (3 月の論文、開始前) | Showdown gen9 の分布で、Champions (Lv50・メガのみ) と違う。自前の自己対戦データで足りている |

## C. ゲーム側

- レギュレーション M-C (9/9〜12/2): 反映済み (arch_0918)。次の切替は 12/2 → docs/REGULATION_CHANGE_RUNBOOK.md

## D. 出典

- [Claude Platform release notes](https://platform.claude.com/docs/en/release-notes/overview) / [Pricing](https://platform.claude.com/docs/en/about-claude/pricing) /
  [Claude Code what's new](https://code.claude.com/docs/en/whats-new) / [headless と --json-schema](https://code.claude.com/docs/en/headless) /
  [issue #75875 (--json-schema の v2.1.205 修正)](https://github.com/anthropics/claude-code/issues/75875)
- [Opus 5.5 (TechCrunch)](https://techcrunch.com/2026/09/22/anthropic-releases-opus-5-5-with-lower-prices-and-fable-level-performance/) /
  [Gemini 3.8 Flash (Artificial Analysis)](https://artificialanalysis.ai/models/releases/gemini-3-8-flash) /
  [poke-env releases](https://github.com/hsahovic/poke-env/releases) / [foul-play](https://github.com/pmariglia/foul-play) /
  [DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR) / [arXiv 2609.13986](https://arxiv.org/abs/2609.13986) /
  [PokéAgent Challenge](https://arxiv.org/abs/2603.15563) / [Champions 規制 (Victory Road)](https://victoryroad.pro/champions-regulations/)
