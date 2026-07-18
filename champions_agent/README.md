# Champions Agent

ポケモンチャンピオンズ(シングルバトル)向けの行動決定AIエージェント強化学習システム、
および学習材料となる環境データ(使用率・技/特性/持ち物構成など)をローカルDBへ収集するシステム。

既存のOCR/画面キャプチャ基盤(`../extractor.py`, `../battle_state.py`, `../server.py`)とは
疎結合に保ち、将来的に `serve/advisor.py` を通じて連携します。

## ディレクトリ構成

```
champions_agent/
├── README.md
├── requirements.txt
├── config.py                  # パス・レギュレーション・DB設定・性格(PlayStyle)定義
│
├── data/                       # ① 環境データ収集システム
│   ├── db/schema.sql            # SQLiteスキーマ定義(usage_snapshot / meta_sets / role_tags等)
│   ├── database.py              # DB接続・CRUDユーティリティ
│   ├── sources/
│   │   ├── pokeapi_client.py    # PokeAPI: 種族値/技/特性/タイプ等の静的データ
│   │   ├── usage_scraper.py     # Smogon chaos JSON からの使用率統計取得(実データ)
│   │   └── name_mapping.py      # Smogon表記 <-> PokeAPI slug の名前正規化
│   ├── ingest.py                 # 収集→正規化→DB投入エントリポイント(定期実行想定)
│   ├── build_meta.py             # 使用率から代表的な型(セット)を構築(meta_sets)
│   └── role_tagger.py            # meta_setsから役割タグ(sweeper/wall/pivot等)を自動付与
│
├── env/                         # ② バトル環境(poke-env ラッパ、シングル専用)
│   ├── showdown_env.py           # 性格別チーム生成+報酬シェイピングを統合したRL環境
│   ├── team_builder.py           # 使用率+役割タグ+性格バイアスでチームを確率生成
│   └── reward.py                 # 性格別の報酬プリセット(REWARD_PRESETS)
│
├── agent/                       # ③ RLエージェント本体(3つの意思決定に対応)
│   ├── spaces.py
│   ├── encoders.py
│   ├── policy_selection.py       # 選出(6→3体+順序)
│   ├── policy_battle.py          # 戦闘中の6行動選択(性格別モデルロード対応)
│   ├── policy_teambuild.py       # 対戦後のパーティ編集
│   └── model.py
│
├── train/                       # 学習ループ
│   ├── train_battle.py           # 性格別PPO学習(checkpoints/battle_policy_{style}.zip)
│   ├── train_selection.py
│   ├── selfplay.py
│   └── evaluate.py               # 学習済みモデルの勝率評価(vs Random / vs 他性格)
│
├── serve/                       # 推論/アドバイス提供
│   └── advisor.py                # Advisor(play_style=...) で性格切り替え可能
│
├── scripts/
│   └── setup_showdown.sh         # ローカルPokemon Showdownサーバーのセットアップ/起動
│
└── tests/
```

## セットアップ

以下のコマンド群は全て**リポジトリルート**(`PokemonChampioons_Adviser/`)から
`python -m champions_agent.xxx.yyy` の形式で実行することを前提としています
(`champions_agent`をPythonパッケージとしてインポートするため)。

```bash
# リポジトリルートで実行
python3 -m venv .venv
source .venv/bin/activate
pip install -r champions_agent/requirements.txt
```


## データ収集(チャンピオンズ実環境データ)

使用率統計は**ポケモンチャンピオンズの実データ**を取得します(2026-07差し替え済み):

1. 主軸: [championsbattledata.com](https://championsbattledata.com) API
   — ゲーム内「バトルデータ」の日次収集(技/持ち物/特性/性格/能力ポイントの採用率%)。
   要クレジット表記: *Battle data provided by Pokémon Champions Battle Data*
2. 補完: [champs.pokedb.tokyo](https://champs.pokedb.tokyo) 公式オープンデータ
   — 上位ランカー構築からポケモン使用率%・チームメイト共起率・持ち物傾向を集計
3. フォールバック: Smogon gen9ou(上記が取得不能な場合のみ自動)

```bash
# まとめて更新(ingest -> build_meta -> role_tagger。日次実行を想定)
bash champions_agent/scripts/update_usage_db.sh

# 個別に実行する場合
python -m champions_agent.data.ingest --skip-static --source auto
python -m champions_agent.data.build_meta      # 代表的な型(meta_sets)を構築
python -m champions_agent.data.role_tagger     # 役割タグ(sweeper/wall等)を付与
```

- launchdによる毎日06:30の自動実行: `scripts/com.championsadviser.usage-update.plist` 参照
- 取得した生JSONは `data/archive/` にgzip保全(配信元停止時の保険)
- 能力ポイントは **0〜32スケール**(32 ≒ 従来の努力値252相当)で格納
- `--source smogon` で旧来のSmogonのみ、`--use-dummy-usage` でネットワーク不要のダミー
- `--limit-usage 5` で少数ポケモンだけ取得する動作確認モード

## 性格(PlayStyle)

`config.PLAY_STYLES` に `offense` / `cycle` / `stall` / `balance` の4性格を定義しています。
各性格は以下の2箇所に反映されます:

- `env/team_builder.py`: 役割タグ(`pokemon_role_tags`)の重み付けにより、性格に合った
  ポケモン/型が選ばれやすくなる(例: `stall`は`wall`役割を優遇)。
- `env/reward.py`: 性格ごとの `RewardConfig`(`REWARD_PRESETS`)により、報酬シェイピングが
  変わる(例: `offense`は速攻決着を促す設定)。

## 学習

事前にローカルでPokemon Showdownサーバーを起動してください:

```bash
bash champions_agent/scripts/setup_showdown.sh          # 初回: clone + npm install
bash champions_agent/scripts/setup_showdown.sh --start  # サーバー起動 (localhost:8000)
```

※アドバイザーサーバー(`uvicorn server:app_asgi --port 8000`)とポートが競合するため、
実運用(ライブアドバイス)と学習を同時に動かさないこと。

性格ごとに戦闘方策を学習します(モデルは `train/checkpoints/battle_policy_{style}.zip` に保存):

```bash
python -m champions_agent.train.train_battle --play-style offense --timesteps 10000
python -m champions_agent.train.train_battle --play-style stall --timesteps 10000
```

`--opp-play-styles` で対戦相手チームの性格候補を指定できます(省略時は全性格からランダム、
population-based selfplayの簡易版として機能します)。

## 評価

```bash
python -m champions_agent.train.evaluate --play-style offense --battles 50
python -m champions_agent.train.evaluate --play-style offense --opponent-play-style stall --battles 50
```

## 推論/アドバイス

```bash
python -m champions_agent.serve.advisor
```

```python
from champions_agent.serve.advisor import Advisor

advisor = Advisor(play_style="offense")
advisor.advise_selection(own_party, opponent_party)
advisor.advise_battle_action(battle)  # poke-envのAbstractBattleを渡す
advisor.set_play_style("stall")       # 途中で性格を切り替え可能
```
