# B0-prep: Foul Play + poke-engine の動的確認の準備 (phaseB_20261031)

> 配置の注記 (2026-10-31、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。測定の出力 (manifest.json / cases.jsonl 25 件 / measurements.jsonl 59 行) は、本体の `logs/inheritance_eval/phaseB_20261031/B0-prep/` の mkdir が権限で拒否されたため **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261031/B0-prep/`** にある (リポジトリ外)。委託先は拒否された操作を言い換えて再試行していない。

- 実施: 2026-10-31 14:40〜14:55 JST 頃。計算時間は数秒。cargo / rustc は無し (Rust 未導入)。
- 対象: gen9championsbssregmc、ローカル Showdown d849b220、Foul Play 8f541f80 → ブランチ local-regmc (external 内、未コミット)、poke-engine f7b147f1 (build していない)。
- 手順 1〜3 (外部データの固定の試行、ローカル専用ログイン、チーム) と実行手順の準備まで。手順 4〜6 (動的照合 25 ケース、対戦 3、速度 3、揺れ 1) は unexecuted。

## 1. 外部データ (external/data_pinned/manifest.json)

| id | URL | 状態 |
|---|---|---|
| smogon_chaos_cutoff0 | smogon.com/stats/2026-09/chaos/gen9championsbssregmc-0.json | 取得不能 (外部取得のコマンドが権限で拒否) |
| foulplay_full_sets | data.foulplay.cc/gen9championsbssregmc/pokemon_full_sets.json | 取得不能 (robots.txt の取得が拒否、利用条件は未確認) |
| foulplay_replay_moves | data.foulplay.cc/gen9championsbssregmc/replay_moves.json | 同上 |
| showdown_sample_sets | play.pokemonshowdown.com/data/sets/gen9championsbssregmc.json | 同上 |
| smogon_chaos_cutoff1500_local | 手元 logs/usage_smogon/2026-09 (2026-10-30 取得、69,462 戦) | 固定済み、**条件変更の代替**: 元 sha256 d274cc77924924b00aedbb89b9f2d87d75add6676ddbdaa43b096636f429f26e / 3,773,627 B、Foul Play のキャッシュ形式 1fcbf80527622acc2dbd0ba9cdfe531c91108c07a730795e7f24def39d7c2b78 / 4,000,603 B |

- 月: Foul Play は実行日の前月を取りに行く (10/31 なら 2026-09)。キャッシュのファイル名に月が入らないため、固定したファイルが日付に関係なく使われる。
- cutoff: Foul Play の既定は -0 (コードの注記に `always use the -0 file`)。1500 を使う場合は条件変更として記録する。foulplay と Showdown の型集合には手元の代替が無く、これらが無い間は元来の B0 にならない。

## 2. ローカル専用ログイン (external/patches/foul-play-local-login.diff)
- ホストが localhost / 127.0.0.1 / ::1 なら `play.pokemonshowdown.com` に HTTP を出さない。challstr を待って `/trn <name>,0,` を送り、`|updateuser|<name>|1|` で確定を確かめる。`|nametaken|` かタイムアウトなら LoginError。`--no-security` (Config.noguestsecurity) のサーバーが空のトークンを受け付ける仕様に依る。ローカルのアドレスで password を指定すると接続前に LoginError で止める。公開サーバー向けの経路は変えていない。ローカル判定は自動 (明示フラグではない)。
- 保存データ (external/patches/foul-play-offline-data.diff): `FP_DATA_CACHE_DIR` でキャッシュの置き場所を固定データのディレクトリに向け、`FP_OFFLINE_DATA=1` ではファイルが無いと PinnedDataMissing で止まる (取りに行かない・`{}` を書かない。無改変版は取得に失敗すると `{}` をキャッシュに書く)。環境変数が無ければ従来どおり。
- 確認: 単体 15/15 pass (HTTP は差し替えで禁止)。常駐 8100 への実接続 1 回 (ユーザー名 b0prepsmoke、対戦なし、起動・停止・設定変更なし) でログイン確定 0.25 秒、HTTP 0 回。変更は 3 ファイル +73/−4 行、無改変版は main のまま。

## 3. チーム (external/teams/、validate-team gen9championsbssregmc で本文・packed とも rc 0)
- **regmc_speed** (速度・完走用。10/5 に登録したメガカメックス + イエッサン軸の 6 体を、修正後の書き出し (f15e2409) で出力。直近の選出画面のロスターは 3 体が技未登録で 6 体そろわないため使わない): blastoise @ blastoisinite (torrent, Modest, 17/0/1/16/0/32): terrainpulse / aurasphere / darkpulse / shellsmash、indeedee @ terrainextender (psychicsurge, Timid, 2/0/23/15/0/26): expandingforce / mysticalfire / encore / substitute、sneasler @ psychicseed (unburden, Adamant, 4/32/25/0/0/5): direclaw / closecombat / throatchop / swordsdance、armarouge @ focussash (weakarmor, Modest, 1/0/0/32/1/32): armorcannon / expandingforce / taunt / endure、salamence @ salamencite (intimidate, Adamant, 31/5/0/0/15/15): bodyslam / earthquake / dragondance / roost、archaludon @ redcard (sturdy, Timid, 2/0/0/32/0/32): steelbeam / thunderbolt / darkpulse / stealthrock。sha256 は本文 9de12936…eaab、packed f37471e4…ac27。
- **regmc_diag** (境界効果の診断用。cutoff 0 が無いため cutoff 1500 の各種の最多の型): Golisopod @ Golisopite (Emergency Exit、メガで toughclaws)、Ditto @ Choice Scarf (Imposter)、Mimikyu @ Life Orb (Disguise)、Grimmsnarl @ Light Clay (Prankster, Reflect / Light Screen)、Pelipper @ Damp Rock (Drizzle)、Indeedee @ Terrain Extender (Psychic Surge)。sha256 は本文 fea6343f…eca、packed 825db9d8…87a3。
- 陰性対照: EVs ×8 の本文は 'more than 32 Stat Points' で拒否、7 体の本文も拒否 (validator が実際に拒否することの確認)。

## 4. 動的照合のケース (cases.jsonl、25 件、すべて unexecuted)
- メガ 8 件: salamence aerilate、golisopod toughclaws、lucario-Z auraguard (Showdown 側にも 'TODO check breakable')、garchomp-Z levitate、absol-Z sharpness、blastoise メガランチャー、starmie の種族値、メガのターンの素早さ。
- 技 9 件: tripleaxel は表を置換せず合計ダメージの分布で照合。ほかに makeitrain、crabhammer / syrupbomb、meteorassault / slash、expandingforce、terrainpulse、direclaw + unburden、steelbeam / redcard / sturdy、Champions の状態異常。
- 延長 4 件: lightclay、terrainextender、damprock、観測後の追跡と探索中の将来効果の区別。へんしん 2 件、ばけのかわ 1 件。名前 1 件: 判明しているのに未対応の名前を NONE / UNKNOWNITEM に黙って置換する経路の検出。
- 判定の区分: 正確に一致 / 探索上の意図的な近似 / 観測後だけ対応 / 欠落・不具合 / 参照側も未確定。正解側はローカル Showdown の遷移で、完全情報のルール検査に限る。
- 照合の道筋: poke-engine-py の `generate_instructions(state, m1, m2)` が返す命令の集合を、`simulate-battle` を seed を変えて回した HP 差・状態と比べる (SimProcess / SimTracker は tools/dmg_compare.py の部品)。`calculate_damage` は未知の技名で panic するので名前は事前に検査する。

## 5. 対戦・速度
- すべて unexecuted (cargo が無い、型集合のデータが取得不能)。速度の条件: BSS では 1 決定あたりの局面数は `--search-parallelism` (既定 1)、局面あたりの時間は `--search-time-ms`。0.25 / 1 / 3 秒は parallelism 1、threads 1 で測る。

## 6. Rust 導入後に実行するコマンド (external/b0_tools/run_after_cargo.sh)
```
export SERVER_URI=ws://127.0.0.1:<port>/showdown/websocket   # 常駐 8100 か一時サーバーか: 未決
export FP_DATA_CACHE_DIR=<external/data_pinned の配置>        # 1500 代替なら .../alt_cutoff1500_condition_change (条件変更)
bash b0_tools/run_after_cargo.sh check     # cargo/rustc の版、clone の HEAD と差分
bash b0_tools/run_after_cargo.sh build     # requests/dateutil を入れ、poke-engine HEAD を --features poke-engine/bss で build・install
bash b0_tools/run_after_cargo.sh smoke     # 1 プロセスでログインと待受
bash b0_tools/run_after_cargo.sh battles   # Foul Play 同士 3 戦 (speed vs diag、1 秒/局面)。|error|・Traceback・UNKNOWNITEM・NONE を検出
bash b0_tools/run_after_cargo.sh speed     # 0.25/1/3 秒 × 1 戦。run_fp_timed.py で 1 決定の壁時計、sample_procs.sh でプロセス群の RSS・CPU
bash b0_tools/run_after_cargo.sh repeat    # 同じ局面 5 回 (スクリプトは build 後に作成)
```
Foul Play の `--websocket-uri local` は localhost:8000 (このマシンでは uvicorn) を指すので使えない。

## 7. 未確認
外部データ 4 件の中身と data.foulplay.cc / play.pokemonshowdown.com の robots.txt と利用条件。build、動的照合、対戦、速度、揺れ。run_fp_timed.py の動作 (poke_engine が無いため import できず)。常駐 8100 のコードが d849b220 と同じか。

## 1 行まとめ
用途: 操作の B0 の準備 → 対照: なし → 正しさ: ログイン (15/15 + 実接続) とチーム 2 つ (validator pass、陰性対照 pass) は確認済み。外部データ 4 件は取得不能 → 効果: 未測定 → 費用: cargo の導入と外部取得の承認が要る → 採否: 保留 → 未確認範囲: build・動的照合・対戦・速度・揺れ、外部データの中身と利用条件。
