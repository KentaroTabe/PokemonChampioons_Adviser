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

```bash
cd champions_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## データ収集(定期実行前提・現時点は手動実行)

```bash
# 静的データ(PokeAPI)+ 使用率統計(Smogon実データ)をまとめて取得
python -m champions_agent.data.ingest --pokemon-limit 30 --format gen9ou --rating 1500

# 使用率統計から代表的な型(meta_sets)を構築
python -m champions_agent.data.build_meta

# meta_setsから役割タグ(sweeper/wall/pivot等)を自動付与
python -m champions_agent.data.role_tagger
```

`data/db/champions.sqlite3` にPokeAPIの静的データと使用率統計(Smogon chaos JSON由来)が
格納されます。本番運用時は cron / launchd 等で上記3コマンドを定期実行してください(未設定)。

ネットワーク不要のダミーデータで動作確認したい場合は `--use-dummy-usage` を付与してください。

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
