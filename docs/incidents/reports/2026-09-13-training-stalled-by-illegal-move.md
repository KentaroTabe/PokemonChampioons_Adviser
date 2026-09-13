# 2026-09-13 学習ループの停止: 使用率データの技をシミュレータが拒否

## 1. サマリ

- 発生: 2026-09-13 06:22 (常時学習 cycle 74 の開始直後)。検知: 同日 18:08 (ユーザーの「学習の経過は」の確認で発見)。
  復旧: 18:13 (修正を入れて再起動)。
- 影響: cycle 74〜86 の 13 サイクル × 3 性格 (balance / offense / cycle) のほぼ全てで学習が途中で止まり、
  smoke_train のタイムアウト (1,038 秒) で打ち切られた。例外は cycle 84 の cycle 性格 1 回だけ (偶然その型を引かなかった)。
- 失われた生データ: なし。チェックポイント・評価履歴・使用率 DB は無傷。失われたのは約 12 時間分の学習量。

## 2. 前提

常時学習 (`champions_agent/scripts/train_forever.sh` → `train_nightly.sh` → `tools/smoke_train`) は、
毎サイクル各性格の方策を Showdown (ローカル、champions mod) で自己対戦させて更新する。
自分側のチームは 70% が「使用率 DB の代表的な型からのランダム生成」(`champions_agent/env/team_builder.py`、
最新の使用率スナップショットを使う)、30% が上位構築 (`env/ranked_teams.py`)。相手側も同様の混合。
使用率 DB は毎日 06:30 に championsbattledata.com / champs.pokedb.tokyo から更新される (`usage-update` ジョブ)。
生成したチームは poke-env 経由で Showdown に送られ、Showdown がチームを検証する。

## 3. 何が起きたか

1. 2026-09-12 21:32 の取り込みで、使用率スナップショット 35 に新シーズン M-C の種 (ネギガナイト = sirfetchd) の
   データが入った。その代表型は `leafblade / meteorassault / firstimpression / closecombat`。
2. Showdown の champions mod の learnset (`pokemon-showdown/data/mods/champions/learnsets.ts`) では
   sirfetchd はメテオアサルトを覚えない (48 技のうちに無い)。mod の `moves.ts` は `meteorassault` を
   `inherit: true, basePower: 170` で定義しているが、継承元の本体データが `isNonstandard: "Past"` のため
   「Meteor Assault does not exist in Gen 9」「can't learn Meteor Assault」の 2 つの理由で拒否される。
3. ランダム生成チーム (`build_random_party`) が sirfetchd を引くと、Showdown がチームを拒否するポップアップを返す。
   poke-env はチームの受理を待ち続けるため対戦が始まらず、環境の step が進まない。
   ログの記録 (train_forever.log 4984385 行目付近):

   ```
   2026-09-13 12:31:22,586 - ChampionsSin lq82l - WARNING - Popup message received: |popup|Your team was rejected for the following reasons:||||- Sirfetch'd's move Meteor Assault does not exist in Gen 9.||- Sirfetch'd (Sirfetch’d) can't learn Meteor Assault.
   [smoke_train] TIMEOUT: 1038秒で打ち切り
   [nightly] [balance] 学習が失敗/タイムアウトしました
   ```

4. 06:22 の cycle 74 から毎サイクル同じことが起き、`watch_training --history` の評価欄は `R-/B-` (評価なし) が並んだ。

## 4. 被害の内訳

| 項目 | 06:22 以前 (cycle 1〜73、9/12 00:18〜) | 06:22〜18:13 (cycle 74〜86) |
|---|---|---|
| 学習の完走 | 全性格が毎サイクル完走 (fps 194〜232) | 39 回中 1 回だけ完走 (cycle 84 の cycle 性格) |
| 1 サイクルあたりの学習量 | 昼 50,000 / 夜 110,000 ステップ × 3 性格 | 途中保存 (20,000 ステップごと) までの端数のみ (例: cycle 80 balance は 11,808 ステップ) |
| 評価 (vs Random / ベンチ) | 毎サイクル実施 | 実施されず (`R-/B-`)。best_checkpoint の更新もなし |
| チェックポイント | 正常 | 正常 (途中保存は原子的、破損なし) |
| 使用率 DB・評価軸 (META_PIN=24) | 正常 | 正常 (無傷) |

無傷だった範囲: チェックポイント本体 / EMA / best、評価履歴 (止まっていただけで汚染はない)、使用率 DB、
Showdown サーバー (稼働し続けた)、構築システムの run (この期間は動かしていない)。

## 5. 失われたもの

- 学習量: 約 12 時間分 (昼の設定で 13 サイクル × 3 性格 × 50,000 ≈ 195 万ステップ相当。実際は各回 1〜2 万ステップ進んでいる)。
  復元不能だが、再開後に同じ時間を掛ければ取り戻せる性質のもの。
- 生データ・派生物: 失われていない。

## 6. なぜ防げなかったか

- 使用率データ (ゲームの実態: ネギガナイトはメテオアサルトを使う) と、シミュレータの learnset (champions mod の実装) は
  独立に更新され、食い違い得る。生成チームを Showdown に送る前に learnset で検査する段が無かった。
- 2026-09-06 に「poke-env は不正チームをポップアップで拒否して待ち続ける」ことを構築システム側で学び、
  `check_advisor_player` には validate-team の事前検査を入れていたが、学習環境のチーム生成には入れていなかった。
- 気付けたはずのサイン: `watch_training --history` の `R-/B-` が 06:22 から連続していた。
  `[nightly] ... 学習が失敗/タイムアウトしました` が 3 性格すべてで出続けていた (単発の失敗は以前もあったため、
  連続を検知する仕組みが無かった)。

## 7. 復旧手順

```
bash scripts/stop_training.sh
# 修正 (env/legality.py、team_builder.py、ranked_teams.py) を入れて検証
.venv/bin/python -m tests.test_env_legality
# ランダム生成 30 チーム + 上位 60 構築を validate-team で検査 → 全件合法 (拒否 0)
bash scripts/start_training.sh
```

復旧の確認: 18:13 再起動。再開後のサイクルで学習が完走し評価が出ることを確認する (本レポート作成時点では再起動直後)。

## 8. 恒久対策

- `champions_agent/env/legality.py` (新規): champions mod の learnset (`tools/team_build/learnsets`) で技を検査し、
  覚えない技は同じ種の使用率上位の合法な技で埋める。合法な技が無い型は候補から外す。learnset に無い種は検査しない。
- `champions_agent/env/team_builder.py` (`build_random_party`) と `champions_agent/env/ranked_teams.py` (`_to_team_text`) に適用。
  構築システムの相手 (`tools/team_build/opponents.py`) も `build_ranked_teams` 経由なので同じ検査を通る。
- 回帰テスト: `tests/test_env_legality.py` (擬似 learnset での検査・埋め合わせ、実データでネギガナイトの
  メテオアサルトが落ちること)。CI 登録済み。
- `champions_agent/train/training_changes.json` に記録 (2026-09-13 18:13、kind=fix)。

## 9. 教訓 / 未対応の課題

- 外部データ (使用率) をシミュレータに流す境界では、シミュレータの規則で検査してから送る。データ更新のたびに
  新しい食い違いが入り得る (新シーズンの新種は特に)。
- 未対応: 「学習が失敗/タイムアウト」が連続したときに通知する仕組み (現状は人が `watch_training` を見るまで気付けない)。
  poke-env のポップアップ (チーム拒否) を検知して即座に別チームで再試行する仕組みも未着手。
- 未確認: champions mod の learnset が実際のゲームより狭い可能性 (ネギガナイトのメテオアサルトは実データで採用率 2 位)。
  上流 (smogon/pokemon-showdown) の更新で解禁される可能性があるが、それまではシミュレータ側の規則に合わせる。
