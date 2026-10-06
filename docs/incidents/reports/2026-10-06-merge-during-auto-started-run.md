# インシデント記録: 接続テストの終了処理が自動で起動した測定 run の実行中に、メインの作業ツリーを新しい版へ進めた

## 1. サマリ

| 項目 | 内容 |
|---|---|
| 発生 | 2026-10-06 19:14:47 JST。メインの作業ツリーで `git merge --ff-only integ/1006` を実行し、HEAD を 4228999d → eceaad99 (17 コミット、66 ファイル) に進めた。同じ作業ツリーで構築の測定 run `improve_20261006_1913` が 77 秒前 (19:13:30) に起動していた |
| 検知 | 2026-10-06 20:50 頃、運用側 (第19回接続テストの対戦ログの確認を委任したエージェントの報告で気付いた。分単位の時刻は未記録) |
| 復旧 | 2026-10-06 21:40:26 JST に案 A (止めて取り直す) をユーザーが選び、親 pid 25267 と子プロセス 5 本 (tools.check_advisor_player) を TERM で停止 (停止の前後に `pgrep -fl` で実測、最終は 0 本。起動から 2 時間 27 分)。結果は使わない。取り直しは未実施 (新版の煙試験 / season_pin の後に新しい run id で探索から)。停止前の 21:31 時点: 実行中 (経過 2:18:05、run.log の最新は 21:24:07 の S8a racing の 100 戦目の後: 9 腕から 4 腕に絞り、残った 4 腕のうち 2 腕が cheap) |
| 影響 | run `improve_20261006_1913` の結果の比較可能性。親プロセスは開始時に読み込んだ旧版のモジュールのまま動き、19:14:47 以降に起動した子プロセス (S8a の適応と測定) と、親が合併の後に初めて import したモジュールは新版を読む。S8a は旧版の設定 (cheap を含む 3 variant) で組んだ 9 腕を、新版のコードの子プロセスで測っている |
| 失われた生データ | なし。対戦ログ・実戦の相手バンク・使用率 DB・登録チーム・run のファイルは合併で書き換わっていない (4.2) |

あわせて、この run の重み (session_threats) には第19回接続テストの勝敗の誤記録 (7 戦目: 負けと記録、実際は勝ち) が入っている。合併とは
別の問題で、4.3 に分けて書く。

## 2. 前提

- 接続テストの終了処理 (`scripts/end_connection_test.sh`、操作パネルの「終了」) は最後に `python -m tools.party_improvements --session --measure`
  を実行する。セッションの対戦から「動きづらかった相手パーティ」の上位 3 を選んで相手の重み (`logs/build_search/session_threats_<時刻>.json`)
  を書き、現行 + 近傍 3 並びの測定 run (`improve_<時刻>`、S0〜S13、数時間) を nohup で起動する。起動の前に `pgrep` で既存の構築 run を
  確かめ、走っていれば起動しない。この自動起動は docs/CONNECTION_TEST_CHECKLIST.md の E 節 (2026-09-09 追加) に書かれている。
- 構築の run (`python -m tools.team_build.run`) は 1 つの親プロセスで、S0〜S6 (探索) を自分で回し、S7 以降 (測定) は子プロセス
  (`tools.check_advisor_player` / `tools.collect_selection_data` / `champions_agent.train.train_selection`) を作業ツリーを cwd にした
  `python -m` で起動する。子プロセスは**起動した時点の作業ツリーのコード**を import する。親プロセスも、途中で初めて import するモジュールは
  その時点のファイルを読む (Python は一度読み込んだモジュールは読み直さない)。
- 作業規約 (リポジトリ直下の CLAUDE.md の「ブランチ運用」。追跡対象外) は、測定 run・接続テストが動いている間はこの作業ツリーで別ブランチを
  checkout しない (run の後段の子プロセスは作業ツリーのコードをそのとき import するので壊れる)、並行して作業するなら git worktree を使う、と
  定めている。merge / reset も作業ツリーのファイルを書き換える点で同じ。
- 当日の段取り: 第18回接続テストの修正 (fix/connection-test-18) などを統合用ブランチ integ/1006 (worktree) にまとめ、「本体への反映は改善 run
  の終了後に feature/team-build を integ/1006 へ ff する」予定だった (docs/TEAM_BUILD_PENDING_1005.md §17)。この「改善 run」は第18回の終了処理が
  起動した improve_20261006_0128 で、18:36:32 に終わっていた (同 run の run.log)。

## 3. 何が起きたか

| 時刻 (10/6) | 出来事 | 根拠 |
|---|---|---|
| 18:06〜19:12 | 第19回接続テスト (サーバーは旧版 4228999d) | 対戦ログの version の行 |
| 18:36:32 | 前の改善 run improve_20261006_0128 が終了 (再現性の門で holdout に進めず) | 同 run の run.log |
| 19:12:36 | 終了処理の開始 (操作パネル)。アドバイザーの停止 | logs/control_panel/20261006_191236__end.log、logs/server_nohup.log の最終更新 |
| 19:13:26 | 実戦の相手バンクを再生成 | logs/real_opponents/bank.json の更新時刻 |
| 19:13:30 | session_threats を書き、run `improve_20261006_1913` を起動 (pid 25267) | `ps` の開始時刻、session_threats_20261006_1913.json の更新時刻、run.log「start (commit 4228999d)」(19:13:31) |
| 19:13:30〜19:15:42 | 終了処理の続き: セッション一括監査 (tools.audit_session、所要 132 秒) | end.log |
| 19:13:31〜19:18:35 | run の S0〜S6 (親プロセス)。S3 (19:13:36〜19:17:52) の途中で下の合併 | run.log |
| **19:14:47** | **運用側がメインの作業ツリーで `git merge --ff-only integ/1006` (4228999d → eceaad99)** | .git/logs/HEAD の記録 1791281687 (= 19:14:47 JST)「merge integ/1006: Fast-forward」 |
| 19:18:36〜19:19:52 | S7〜S13 の開始。S8a の軽い適応 (3 候補 + 参照 × 1,000 戦の収集と学習。子プロセス) | run.log |
| 19:19:52〜20:29:47 | 参照の 4 variant (rule / generic / cheap / production) × 300 戦 (子プロセス) | evaluation/s08a_reference_reference@*.json |
| 20:29:47 | S8a racing を「arms=9+ref」で開始 (cheap の腕を含む) | run.log、evaluation/s08a_screen_L01_INC@cheap.log ほか |
| 21:24:07 | S8a racing の 100 戦目の後: 5 腕が degraded で脱落し、L02_INC@generic / L02_INC@cheap / L03_INC@generic / L03_INC@cheap の 4 腕で 200 戦目へ | run.log |
| 21:31 | run は実行中 (経過 2:18:05) | `pgrep -fl tools.team_build.run`、`ps` |
| 21:40:26 | ユーザー指示で run を停止 (親 pid 25267 に TERM → 5 秒後に残った子 5 本に TERM → 最終 `pgrep` は 0 本) | 停止スクリプトの出力、`pgrep -fl` |
| 21:51:32 | 第19回 4・7 戦目の対戦ログに勝敗の訂正の outcome 行を足した (ユーザー指示。4.3 の別の問題の方の対処) | 対戦ログの最終行 (`corrected_from`、`corrected_at`) |

起動のコマンド (pid 25267):

```
-m tools.team_build.run --run-id improve_20261006_1913 --stages all --profile medium --only-incumbent --incumbent-neighbors-s5 3
  --max-candidates 4 --threat-weights-file logs/build_search/session_threats_20261006_1913.json --parallel 5 --seed 281610
```

運用側 (この作業を進めていた Claude Code のセッション) の記録では、合併の直前に確かめたのは `scripts/status.sh` と、終わっていた run
(improve_20261006_0128) の `pgrep` だけで、新しい run の有無を `pgrep -f tools.team_build.run` で確かめていなかった。status.sh の常駐プロセスの
一覧のパターンには構築の run が無い。

```bash
# scripts/status.sh 8 行目 (常駐プロセスの一覧)。tools.team_build.run はこのパターンに無い
pgrep -fl "audit_monitor|train_forever|train_battle|smoke_train|uvicorn|human_battle|pokemon-showdown|tools.control_panel"
```

run を起動する側には既存の run の確認があるが、作業ツリーを動かす側には同じ確認が無い。

```python
# tools/party_improvements.py launch_measurement (起動する側)
active = active_measurement()          # pgrep -fl -m tools.team_build.run
if active:
    return {"skipped": f"構築 run が実行中 ({active[:80]}…)。終了後に --measure を再実行"}
```

合併の後の run の動き方を決めたのは、次の 2 つの仕組みである。

```python
# tools/team_build/run.py (旧版 4228999d) _measure: 測定段のモジュールは S7 の開始 (19:18:36) で初めて import する
from tools.team_build import ablation as AB, adapt as AD, racing as R, stress as ST
from tools.team_build.pipeline import run_measurement

# tools/team_build/racing.py: 測定の子プロセスは作業ツリーを cwd にした python -m で起動する
cmd = [sys.executable, "-m", "tools.check_advisor_player", "--battles", str(n), ...]
res = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), timeout=timeout, env=child_env())
```

異常のサインは記録に残っていた。終了処理の出力 (操作パネルの出力欄。end.log 261 行目) は 19:13:30 に「測定を起動しました: run
`improve_20261006_1913` (現行 + 近傍 3 並びを S8a〜S13 に掛ける。見込み数時間)」と出していた。合併の 77 秒前である。一方、run の記録
(manifest.json の git_commit、run.log の開始の行) は開始時の 4228999d だけで、途中で作業ツリーの版が変わったことはどこにも記録されない。

## 4. 被害の内訳

### 4.1 run improve_20261006_1913 の各段が読んだコード

| 部分 | 時刻 | 読んだコード | 根拠 |
|---|---|---|---|
| 親プロセスの run.py と、開始時に import したモジュール (champions_agent.config ほか) | 19:13:30〜 | 旧版 4228999d | manifest.json の git_commit (開始時に記録)。新版の run.py が書く「S2 season pin」の行が run.log に無い。S8a の腕が 3 候補 × 3 variant = 9 (旧版の config は `BUILD_SCREEN_VARIANTS = ("rule", "generic", "cheap")`、新版は `("rule", "generic")`) |
| S0〜S2 (親) | 19:13:31〜19:13:36 | 旧版 | 合併の前 |
| S3〜S6 (親) | 19:13:36〜19:18:35 | 主に旧版。合併の後に親が初めて import したモジュールは新版の可能性がある (例: S5 の `joint_stage` が import する `role_sets` / `set_lint`。どちらも版の間で変わっている) | どのモジュールをいつ初めて import したかは記録に無く未確認 |
| 測定段の取りまとめ (親の `tools.team_build.pipeline` ほか) | 19:18:36〜 | 新版と見られる | 旧版の run.py は測定段のモジュールを `_measure` の中で import する (上の引用)。親の sys.modules は記録に無く未確認。新版の pipeline.py は cheap の適応を config の variant で決める (`needs_cheap`) が、config は旧版の値のままなので、cheap の適応と腕が残った |
| 子プロセス (S8a の軽い適応、参照の 4 variant × 300 戦、S8a racing) | 19:18:36〜 | 新版 | 子は作業ツリーを cwd にした `python -m` で起動する (racing.py / adapt.py)。仕組みからの帰結で、子の出力 (evaluation/s08a_*.json / .log) に版の記録は無い |

新版の設定どおりなら、S8a の腕は 3 候補 × 2 variant = 6 で、S8a の軽い適応 (3 候補 + 参照 × 1,000 戦の収集と学習) は省略される。この run では
旧版の設定で組んだ 9 腕と、参照の 4 variant (20:29:47 の run.log: rule 0.483 / generic 0.71 / cheap 0.30 / production 0.667、各 300 戦) を、
新版のコードの子プロセスで測っている。21:24:07 の 100 戦目の後に残った 4 腕のうち 2 腕 (L02_INC@cheap / L03_INC@cheap) は、新版の設定には
無い腕である。季節の固定 (新版の `season_pin`: 分割の seed・相手プールのスナップショット・実在の構築の区切り) も
旧版の run.py なので使われておらず、新版の run と相手の分割が揃わない。

版の間の差 (`git diff --shortstat 4228999d eceaad99`): 66 ファイル、3,723 行追加 / 235 行削除。run に関わるもの:
tools/team_build/ の pipeline.py (cheap の省略、S9 の変更) / run.py (season_pin) / gen_sets.py / set_lint.py / role_sets.py / repair.py /
sets.py / opponents.py / real_eval.py / triggers.py と新しい season_pin.py / register_selection.py、champions_agent/config.py
(`BUILD_SCREEN_VARIANTS`、`BUILD_CALIBRATION_VARIANTS` = `("cheap",)` → `("generic",)`、`BUILD_SEASON_PIN`)、測定の対戦で子プロセスが使う
助言エンジン側 (advisor/infer.py / selection.py / service.py / versions.py、champions_agent/agent/selection_dispatch.py。測定は
`pick_policy: advisor`)。どの変更が測定の数字をどれだけ動かすかは未確認。

この後の段で止まるおそれ (ast と grep での静的な確認、実行はしていない): 親は旧版の config を持ったままなので、新版で増えた設定名 (18 個) を
読む新版のモジュールを初めて import すると ImportError になる。tools/team_build の測定段のモジュール (pipeline / racing / adapt / stress /
ablation / holdout / finalists / package / report / repair / review_run ほか) は新版で増えた設定名を読まない。読むのは real_eval.py と
season_pin.py で、real_eval は S2 (19:13:36、合併の前) の env_match → env_validity.read_real_battle で旧版が読み込まれていると見られ、
season_pin は旧版の run.py からは import されない。助言エンジン側 (advisor/infer.py ほか) にも読むものがあり、親が後から初めて import するかは
未確認。また、旧版のまま読み込まれている sets.py はメガ型と非メガ型の一貫化 (`pick_alternative`) を持たず、S9 で
初めて読み込まれる見込みの新版の repair.py と組み合わさる (推測)。

### 4.2 無傷だった範囲

- **run のファイル**: 合併は git の追跡対象のファイルだけを書き換える。`logs/` は .gitignore の対象 (39 行目) で、run のディレクトリ
  (logs/build_search/runs/improve_20261006_1913/) は合併で書き換わっていない。21 時台に JSON 35 本・JSONL 14 本を読み、読めないものは 0。
  合併の前に書かれた成果物 (request.json / meta_snapshot.json / opponent_families.json / sealed/ / s02_env_match.json / species_features.json、
  19:13:31〜19:13:55) は旧版だけで作られている。
- **実戦の相手バンク** (logs/real_opponents/bank.json): 最終更新 19:13:26 (合併の前。終了処理の旧版)。
- **使用率 DB** (champions_agent/data/db/champions.sqlite3、.gitignore の対象): 最終更新 06:35:04 (日次の更新)。
- **登録チーム** (config/my_team.json、未追跡): 最終更新 10/5 23:27:09。
- **対戦ログ** (logs/battles/): 第19回の最後のログの最終更新 19:12:30 (合併の前)。読み取りだけで、訂正の行も足していない。
- **他の run**: 20:55 と 21:31 の `pgrep -fl tools.team_build.run` は pid 25267 の 1 本だけ。10/6 に作られた run は improve_20261006_0128
  (18:36:32 終了) と improve_20261006_1913 だけ。
- **同じ時刻に動いていた終了処理の続き** (セッション一括監査 tools.audit_session、19:13:30〜19:15:42): scripts/end_connection_test.sh と
  tools/audit_session.py は合併で変わっていない。監査のレポートは 19:15:42 に書かれている。途中で新版のモジュールを import したかは未確認。

### 4.3 別の問題: run の重みに勝敗の誤記録が入っている

第19回の 7 戦目 (18:43〜18:49) は、保存フレームに WIN が写っている勝ちだが、旧版の勝敗の推定が直前の対戦のレートの増減を取り違えて
「負け (推定)」と記録した (docs/incidents/reports/2026-10-06-rate-delta-misattributed-outcome.md と同じ機構)。終了処理はこれを負けとして
「動きづらかった相手パーティ」の上位 3 に入れ、run の重みに ミミロップ 0.8957 / ソウブレイズ 0.5598 / オオニューラ 0.5598 が入った。重みは
1 + 負けなら 1.0 + 圧力 (0〜1) なので、勝ちと記録されていれば 7 戦目は 1.33 で、負けの 6 戦 (2 以上) より下になり上位 3 に入らない
(式からの計算。正しい勝敗で重みを作り直してはいない)。詳細は docs/CONNECTION_TEST_CHECKLIST.md の第19回の節と docs/KNOWN_ISSUES.md の D。

## 5. 失われたもの

- **生データ**: 失われていない。
- **派生物**: run improve_20261006_1913 の合併の後に書かれた成果物 (s03_archetypes.json、s04〜s06、reference_team.txt、advisors_screen/、
  evaluation/) は版が混ざった状態で作られ、他の run と比べてよい保証が無い。合併の前に書かれたもの (S0〜S2 と species_features.json) は
  旧版だけで作られている。
- **時間**: 21:31 時点で 2 時間 18 分の計算 (5 並列)。結果を捨てるなら、この時間と完走までの残りが失われる。
- **完走した場合の registry**: 測定段は registry に status=candidate の行を書く (S7 の検証済みの選出モデル、S7b の参照の適応モデル、S13 の
  Package。新版の tools/team_build/pipeline.py 717 / 755 / 1018 行)。昇格は人手 (tools.team_build.promote) なので本番には入らないが、この run の
  行を採用の判断に使わない印が要る (案)。

## 6. なぜ防げなかったか

- **合併の前の確認が「終わる予定だった run」を対象にしていた。** 段取り (PENDING §17) の条件は「改善 run の終了後」で、確かめたのは
  improve_20261006_0128 の終了と status.sh だった。その 77 秒前に、接続テストの終了処理が新しい run を起動していた。
- **status.sh の常駐プロセスの一覧に構築の run が無い。** 一覧に出る Showdown (pokemon-showdown) は 9 日前から常駐していて、run の有無の
  手がかりにならない。
- **自動で起動する仕組みと、作業ツリーを動かす規則が別々の場所に書かれていた。** 自動起動は CONNECTION_TEST_CHECKLIST.md の E 節、checkout
  の禁止は作業規約 (CLAUDE.md) にあり、どちらからも他方を辿れない。運用側は合併の判断のときに自動起動を意識していなかった。
- **起動する側にしか確認が無い。** `launch_measurement` は既存の run があれば起動しないが、checkout / merge / reset の側には同じ確認が無い。
- **版が変わったことが記録されない。** manifest.json の git_commit は開始時の値だけで、子プロセスの出力にも版が無い。今回は合併の時刻と
  run の起動の時刻を並べて分かったが、気付かなければ混ざったまま結果が読まれていた。
- **気付けたはずのサイン**: 終了処理の出力の「測定を起動しました: run `improve_20261006_1913` … 見込み数時間」(19:13:30、操作パネルの出力欄と
  logs/control_panel/20261006_191236__end.log)。

## 7. 復旧手順

2026-10-06 21:40:26 に案 A を実施 (ユーザー指示。親に TERM → 5 秒後に残った子に TERM → 最終 `pgrep` は 0 本)。取り直しは未実施。選択肢 (当時の案):

| 案 | やること | 得るもの / 失うもの |
|---|---|---|
| A. 止めて取り直す | pid 25267 と子プロセスを止め (止める前と後に `pgrep -fl` で親と子を確かめる)、コードが安定してから新しい run id で探索 (S0) から起動し直す | 新版で揃った結果。ここまでの計算 (21:31 時点で 2 時間 18 分) を捨てる |
| B. 完走させて結果を捨てる | 止めずに終わらせ、結果は採否に使わない (参考に留め、registry の行に印を付ける) | 止める手間が無い。終わるまで 5 並列で CPU を使い、次の接続テストや煙試験と重なりうる |

取り直す場合に先に決めること (案):

- 7 戦目の勝敗の扱い (対戦ログに訂正の行を足すか、重みだけ作り直すか。訂正の行を足すのは運用側の判断)。そのままだと同じ重みで始まる。→ **2026-10-06 21:51 に 4・7 戦目の対戦ログへ訂正の outcome 行を足した** (ユーザー指示。読み手は 11 戦 5 勝 6 敗と数える)。次の終了処理 / run は訂正後の勝敗で重みを作る。
- 新版の最初の run は季節の固定 (`season_pin`) を作る (PENDING §17 では統合後の煙試験で作る予定)。取り直しの run と煙試験の順番。

## 8. 恒久対策

いずれも案で、実装していない。回帰テストは実装のときに足し、テスト名をこの節に書く。

| 案 | 置き場 | 内容 |
|---|---|---|
| (1) | .claude 側の運用手順 (運用側が別途記録) と docs/DEV_COMMANDS.md | メインの作業ツリーで checkout / merge / reset をする直前に `pgrep -fl tools.team_build.run` を実行し、出たら作業ツリーを動かさない (作業は worktree で行い、本体への反映は run の終了を確かめてから) |
| (2) | scripts/status.sh | 常駐プロセスの一覧 (と経過時間つきの一覧) に tools.team_build.run を含める |
| (3) | docs/CONNECTION_TEST_CHECKLIST.md | E 節の既存の記述 (終了処理が測定 run を起動する) に、run の実行中はメインの作業ツリーを動かさないことを書き足す。終了処理の後に run が始まることを、起動の手順の側 (0. 前提と起動) にも書く |
| (4) (追加の案) | tools/team_build (run の記録と子プロセスの起動) | 子プロセスを起動するたびに作業ツリーの HEAD を読み、開始時の git_commit と違えば run.log に警告を出して manifest に残す (版の混在を後から分かるようにする) |

## 9. 教訓 / 未対応の課題

1. **作業ツリーを動かす前は、予定の run ではなく「今動いているもの」を数える。** 自動で起動する仕組みのある処理 (接続テストの終了処理) の
   直後は特に。
2. **自動で起動する仕組みの説明と、作業ツリーを動かす規則は、同じ場所から辿れるようにする。** 片方だけを読んでも判断を誤らないように。
3. **版の混在は、記録が無いと後から確かめられない。** run の記録は開始時の版だけでなく、子プロセスを起動した時点の版も残す。

未対応: 8 章の案 (1)〜(4) の実装、取り直しの run (新版の煙試験の後)。済: run improve_20261006_1913 の停止 (21:40:26、7 章)、第19回 4・7 戦目の勝敗の訂正 (21:51、対戦ログに訂正の行)。学習 (RL) の分布や
評価には影響しないので、champions_agent/train/training_changes.json には追記しない。
