# Jev (TypeSafe AI の System One model) の独自調査とポケモンアドバイザーでの適用判断

調査日: 2026-09-24。元メモ: [docs/JEV_RESEARCH.md](JEV_RESEARCH.md) (2026-09-22、会話の合いの手システム向けの調査)。

**結論: ポケモンアドバイザーには現時点で採用しない。** アドバイザーの入力は画像と数値で、判定は決定的な計算・探索・
自前データの学習モデルに置いている。Jev が向くのは「英語の短いテキスト状態に対する閉じた選択肢の高速判定」で、
噛み合う箇所は OCR 文のイベント分類とその側の帰属だけ。そこも日本語の精度が未公表で、判定に必要な情報が文ではなく
画面側にある場合が多く、対戦中の経路に外部 API を入れる代償に見合う見込みが薄い。試すなら §4 の順で実測を先に行う。

## 1. 一次資料で確認した事実 (メモとの突き合わせ)

| 項目 | 確認した内容 | メモとの差 |
|---|---|---|
| 発表 | 2026-09-15 に TypeSafe AI (Diogo Almeida、元 OpenAI) が公式ブログで発表。早期アクセス (waitlist)。コンソールは console.typesafe.ai | メモは発表日と早期アクセスに触れていない |
| モデル | 安定版 jev-1.13.0、`jev-latest` はこれを指す。`jev-preview` も現在は同じ | 一致 |
| 制約・料金 | 64k tok/リクエスト、state + 最長質問 32k。入力 $0.042/M、出力無料。250,000 tok/秒・1,200 req/分 (需要で変動) | 一致 |
| 言語 | 英語が主。CJK は「扱えるが同等ではない」。日本語の公開評価は公式にも第三者にも無い (note.com の再検証記事も「実測なし」) | 一致 (要実測) |
| 遅延 | 公称 70〜500 ms (多くは 100 ms 前後、米西海岸から)。第三者実測 92〜214 ms、別の大規模ベンチで P50 0.30 秒。東京からは太平洋往復で +100 ms 強の見積もり (実測は無い) | メモは公称値のみ |
| 入力・提供形態 | テキストのみ。クラウド API のみ (自前ホストの記載なし)。リクエストを学習に使わないと明記 | 一致 |
| SDK | Python 3.10+ / Node 20+。このリポジトリの venv は Python 3.9.6 なので公式 SDK は入らない (HTTP 直叩きは可) | メモに無い |
| 公式の弱点 (jaggedness 文書) | 字義どおりに読む、計算・数え上げ不可、数値の近さの判定が苦手 (16 進より色名のような意味表現が得意)、日付は文字列扱い、間接参照・二重否定、無関係な state で精度低下、注入に無防備、生成は不可。選択肢は最大 255 | メモ §4 と一致。「数値の近さ」「注入」「255」が追加 |
| 信頼度 | 分布の形から算出 (1 択に集中 = 1.0)。docs は較正を主張せず「convenient measure」、製品ページは「較正済み」と主張。Noul に confidence は無い | 一致。較正は自分のデータで信頼度帯ごとの正答率を数える前提 |
| サイト | 公式は typesafe.ai / docs.typesafe.ai / console.typesafe.ai。メモが「公式サイト」とした jevai.net は自ら公式と名乗る (© Jev AI、hello@jevai.net) が、typesafe.ai からのリンクは無い | **関係は未確認**。申込・鍵の発行は console.typesafe.ai 側で行うのが安全 |

## 2. 第三者の実測 (公開されているもの)

- 10 データセット・約 22,500 呼び出しのベンチ (dev.to、aitejiu): スキルの振り分け Recall@1 75.8% (GPT-4o-mini 67.3%)、
  ツール選択 96.5%、意図分類 97.9% (7 クラス) / 80.3% (77 クラス)、プロンプト注入の検知 P/R 100%。
  効かない例: モデル難易度の振り分け 51.3% (無信号)、エージェント軌跡の失敗帰属 AUROC 0.56 (乱数並み)、
  **韓国語 48.9% vs 英語 61.5%** (非英語で落ちる)。P50 0.30 秒、総費用 $2.19
- フィッシング判定 2,000 通 (XenoSpectrum): 質問 1 つでは 62.6% (Haiku 4.5 は 81.3%)。1,000 件を見て設計した 5 問に
  分解すると 95.0%。遅延中央値 239 ms (Haiku 687 ms)、費用は 1/12。**質問の書き方で精度が大きく振れる**
- 合成テスト 8 件 (MindStudio): 92〜214 ms。「その他」を選択肢から外した強制選択では、低信頼度 (0.31) のまま誤答した
- オープンな互換実装: OpenJev (Qwen3.5-4B 等の既存モデルを流用する複数の独立プロジェクト。較正が未解決)、
  von (ModernBERT 395M、Apache-2.0、ローカル 18 ms、自前ベンチで macro 72%。英語中心と推定、未確認)

## 3. アドバイザーの構成要素との対応

| 構成要素 | 入力 | 今の方式 | Jev の適合 | 判断 |
|---|---|---|---|---|
| シーン判定・HUD 抽出 (vision/) | 画像 | 色ヒューリスティクス + Apple Vision OCR | 不可 (テキストのみ) | × |
| OCR 文 → イベント (vision/events.py) | 日本語の短文 | 辞書 (正規表現) + 正規化 + 救出バッファ | 形式は合う (閉じたイベント集合への Choice)。ただし辞書に当たらない文の量は未計測で、生の OCR 行は対戦ログに残していない。日本語精度は未公表 | △ (実測の材料が無い) |
| 主語を落とした文の帰属 (9/7 の誤帰属) | 日本語の短文 + 直近の文脈 | 窓内に相手側へ反映済みなら残像とみなす | 文に主語が無い = 情報は画面 (HP バー・演出) 側にある。テキストモデルでは原理的に復元できず、事前確率で当てるだけ | × |
| ダメージ計算・行動評価 (advisor/engine) | 数値 | 決定的計算 + 探索 + RL ブレンド | 公式に「計算機ではない」 | × |
| 選出モデル・相手の型推定 | 自前の対戦データ・使用率 DB | 学習 (torch) / 統計 | ゲーム固有の知識が無い | × |
| 構築 S4 概念 / S13 記事 / 介入仮説 | 生成 | Opus 5 / Sonnet 5 | 生成は対象外 | × |
| 構築 S5/S6 の役割判定・軸適合 | 数値・データ | ROLE_SPECS の規則 + 使用率 | 規則で足りている。Jev は数値に弱い | × |
| 視覚監査 (audit_session) | 画像 | Opus 5 | 不可 | × |
| 記事の構造化抽出 (articles.py) | 日本語の長文 | Sonnet 5 | 抽出は生成モデルの役割。件数が少なく費用問題も無い | × |
| LLM 出力の検証 | JSON | コードの検証器 | 決定的検証と CLI の構造化出力の方が確実 ([TECH_WATCH_2026-09.md](TECH_WATCH_2026-09.md) §A-1) | × |

補足:
- 対戦中の経路は今ネット無しで動く。助言の合格基準は 10 秒以内 (decision_audit) なので 100〜300 ms の往復自体は収まるが、
  回線断・レート制限・値上げ・`jev-latest` の更新で答えが変わる、といった外部依存を対戦中の経路に持ち込む代償がある
- プロジェクト規約「判定・数値は LLM に任せない」に対して、Jev は生成しないが学習済みの判定器。使うなら
  「決定的処理が先、Jev は低信頼度で沈黙する後段」に限る (メモ §4.3 と同じ方針)
- 費用は問題にならない (OCR 文 1 件 ≈ 300 トークン = $0.00001。接続テスト 1 回で $0.01 未満)

## 4. 判断と再検討の条件

- **採用しない (2026-09-24)**。実装・依存の追加は行わない
- 再検討する条件 (全部): (a) 辞書に当たらない OCR 文が実測で無視できない量ある、(b) 日本語で信頼度 ≥ 0.9 の帯の正答率が
  ≥ 95% (自前 200 件)、(c) 東京からの p95 ≤ 300 ms、(d) 同じ材料でローカルの分類器 (von 等の再学習、または自前の小さな分類器。
  ネット不要) に勝つ
- 試す場合の順番: 1. battle_logger に生の OCR 行 (辞書に当たった / 当たらなかった) を記録する小さな変更 → 2. 次の接続テストで
  収集 → 3. 200 件を人手でラベル → 4. 3 条件 (全日本語 / 指示と基準だけ英語 / 全部英語) × 信頼度帯で正答率 → 5. ローカル基準との
  比較。モデル版は `jev-1.13.0` に固定する。費用は $1 未満
- メモの本来の用途 (会話の合いの手: 閉じた候補からの選択、確率でのゲート、生成しない) には向く。本書の判断はアドバイザーに限る

## 5. 出典

- 公式: [Introduction](https://docs.typesafe.ai/introduction) / [Models](https://docs.typesafe.ai/models) / [Confidence](https://docs.typesafe.ai/confidence) /
  [Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out) / [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) /
  [Quickstart](https://docs.typesafe.ai/introduction/quickstart) / [発表ブログ](https://typesafe.ai/blog/introducing-system-one-models-and-jev) / [typesafe.ai](https://typesafe.ai/)
- 製品ページ (関係未確認): [jevai.net](https://jevai.net/)
- 第三者: [ベンチ 10 データセット (dev.to)](https://dev.to/aitejiu/benchmarking-jev-what-a-decision-model-can-and-cant-do-in-an-agent-harness-20po) /
  [フィッシング判定と質問分解 (XenoSpectrum)](https://xenospectrum.com/en/jev-typesafe-bert-classifier-decomposition/) /
  [合成テスト (MindStudio)](https://www.mindstudio.ai/blog/jev-system-one-model-classification) / [解説 (flaviocopes)](https://flaviocopes.com/jev/) /
  [日本語での検証手順 (note)](https://note.com/allay0224/n/nbb47ec7a4772) / [OpenJev と非生成の代替 (DevelopersIO)](https://dev.classmethod.jp/en/articles/openjev-non-generative-ai-alternatives/) /
  [von](https://github.com/wfzyx/von) / [jev-voice (第三者の参考実装)](https://github.com/kevinbadi/jev-voice)
