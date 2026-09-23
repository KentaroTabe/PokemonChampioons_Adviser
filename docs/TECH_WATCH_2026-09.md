# 技術動向の棚卸し (2026-09-24): プロジェクト開始 (2026-07-15) 以降に出たもの・使えるのに使っていないもの

前提 (現状): 外部 LLM は claude CLI のヘッドレス実行だけ (S4 概念・介入仮説 = Opus 5、S13 記事・記事の抽出 = Sonnet 5、
視覚監査 = Opus 5)。画面認識は Apple Vision OCR + OpenCV、行動評価は決定的計算 + 探索 + RL ブレンド。
Python 3.9.6 (Xcode 付属)、Apple Silicon、macOS 26.5.1、claude CLI 2.1.270。

## A. 提案 (優先順)

### 1. claude CLI の構造化出力 (`--json-schema`) を構築の LLM 呼び出しに使う — 最優先・小

- 状況: 開始時から存在 (v2.1.205、7 月上旬に不正スキーマの無言フォールバックが修正済み)。このリポジトリでは未使用
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

### 3. effort の段階を段ごとに設定する — 中

- Opus 5 以降は effort (low〜max) が主制御 (7/24)。CLI に `--effort` がある。S4 概念は xhigh / max、S13 記事は low〜medium で
  時間短縮 (今は 1 呼び出し 187 秒)、介入仮説は high など
- config に段ごとの effort を置き (マジックナンバーを CLI 引数に直書きしない)、2 の regression で測る

### 4. Fable 5.1 (9/1) を S4 だけ試す — 低

- $10 / $50 (Opus 5 の 2 倍、キャッシュ読みは $0.25)。1 run 約 $15 と試算。概念の質が上がるかは未測定。2 の後で

### 5. 呼び出しの衛生 — 低

- `--tools ""` にすると system prompt が 12.8k トークン (今の `--disallowedTools` 列挙は約 17k、9/6 実測)。1 呼び出し約 $0.03 の差
- `--max-budget-usd` を 1 呼び出しの上限として付ける (再試行の暴走に対する保険)
- 記録に CLI の `total_cost_usd` を残す (2 の試算を実測に置き換えられる)

### 6. Python 3.9 からの移行 (前提整備) — 中、SDK を使う必要が出たとき

- Anthropic Python SDK 1.0 (8/20) は 3.10+、Jev SDK も 3.10+。3.9 は 2025-10 に EOL。Homebrew には 3.14.2 がある
- pyobjc / torch / easyocr / poke-env の対応は未確認。champions_agent/.venv も別にある。CLI 経由のままなら急がない

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
