# 課題提出用の部分公開 方針書

## 課題の要件 (2026-07-19確認)

- 生成AIを用いて開発した、**Dockerコンテナ上で動作しブラウザからアクセスする**
  簡単なWebアプリ (入力・処理・出力を持つこと)
- **GitHub Publicリポジトリ**として公開し、README.md にシステム概要・用途・
  実行方法・コンテナ起動方法・動作画面・使い方を記載
- 完成度は不問。開発過程で生成AI (Claude Code等) を利用すること自体が主題

本プロジェクトは全体をClaude Code (Agentic coding) で開発しており、その一部を
切り出せば課題の趣旨 (生成AIによる開発効率の体験) のレポート素材として最適。

## 切り出す部分の選定: アドバイザー部 (`advisor/`) をWebアプリ化

**選定理由**
1. **Docker制約**: 画面認識部 (`vision/`) はApple Vision OCR (macOS専用) に
   依存し、Linuxコンテナで動かない。OBS/実機も必要で審査者が試せない
2. **権利面が最も安全**: ゲーム画面キャプチャ・実画面由来テンプレートを
   一切含まずに完結する (ダメージ計算は公開データの数値のみ)
3. **入力・処理・出力が明確**:
   - 入力: 自分/相手のポケモン・技・HP・ランク・場の状態 (Webフォーム)
   - 処理: Lv50ダメージ計算+行動候補の期待値評価 (+型推定のデモ)
   - 出力: 推奨行動ランキング・ダメージ幅・確定数
4. RL部 (`champions_agent/`) はShowdownサーバーや学習環境が必要で
   「簡単なWebアプリ」の枠を超える

**アプリ名 (案)**: `champions-damage-adviser` —
ポケモンチャンピオンズ (Lv50・メガシンカ) 対応のダメージ計算&行動アドバイザー

## 新リポジトリの構成

```
champions-damage-adviser/
├── README.md            # 課題要件の項目を網羅 (下記)
├── Dockerfile           # python:3.11-slim + pip install
├── docker-compose.yml   # ポート公開のみの単純構成
├── requirements.txt     # fastapi, uvicorn のみ (numpy/cv2不要にする)
├── app/
│   ├── main.py          # FastAPI: フォーム/JSON API
│   ├── damage.py        # advisor/damage.py 由来 (vision依存を除去)
│   ├── engine.py        # advisor/engine.py の評価コアを簡約
│   ├── dex.py + data/   # 種族値/技データ (PokeAPI由来のJSON)
│   └── static/index.html# 入力フォームUI (既存index.htmlの意匠を流用)
└── examples/            # サンプル入力JSONとcurl例
```

**移植時の変更点**
- `vision.abilities` / `my_team` / OCR系への参照を除去 (特性・持ち物は
  フォーム入力にする)
- 使用率DB (sqlite) は**同梱しない**: 型推定デモを載せる場合は
  上位数体分の型候補を小さなJSONに焼き込んで出典を明記する
- Python 3.11ベースに更新 (本体は3.9だがコンテナでは自由)

## README.md に書く内容 (課題指定)

1. システム概要と用途 (元プロジェクトの全体像と本アプリの位置づけ1段落)
2. 実行方法: `docker compose up` → `http://localhost:8080`
3. 動作画面のスクリーンショット (**アプリUIのみ**。ゲーム画面は載せない)
4. 使い方 (入力例→出力例)
5. 生成AI利用の記録: Claude Codeでの開発フロー、実際のプロンプト例、
   人手実装ゼロである旨 (課題の趣旨に直結するため必ず書く)
6. データ出典とライセンス表記 (PokeAPI / 使用率データを含める場合は
   championsbattledata.com のクレジット必須 / ポケモン関連名称の商標注記)

## 公開時の除外チェックリスト

- [ ] ゲーム画面キャプチャ (`debug_frames/`等) — 一切含めない
- [ ] `images/type_templates_real/` — 含めない
- [ ] `logs/battles/` — 対戦相手名を含むため含めない
- [ ] `config/my_team.json` — 個人の構築情報。フォーム入力に置換
- [ ] sqlite使用率DB — 原則含めない (焼き込みJSON+出典明記で代替)
- [ ] **履歴を持ち込まない**: クリーンコピーで `git init` (本リポジトリの
      履歴には上記ファイルのパス・内容が含まれるため)
- [ ] git author/メールアドレスを提出用に確認 (アカウント特定回避が必要なら
      課題用GitHubアカウントを別途作成)

## 作業手順 (戻られたら着手可能)

1. 新ディレクトリにクリーンコピー+依存除去 (Claude Codeで実施)
2. Dockerfile/compose作成、`docker compose up` で動作確認
3. README執筆 (スクリーンショット撮影)
4. GitHub Publicリポジトリ作成・push・URL確認
5. レポート本文: リポジトリURL + 生成AI利用の体験 (本文に詳細は書かない)

**所要見込み**: 切り出し〜動作確認まで1セッションで完了可能。
公開前チェックリストの最終確認のみ人の目で行うことを推奨。
