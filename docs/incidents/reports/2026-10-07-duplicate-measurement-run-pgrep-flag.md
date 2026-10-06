# インシデント記録: 接続テストの終了処理が、実行中の測定 run に気付かず 2 本目の run を起動した (pgrep に無い option)

## 1. サマリ

| 項目 | 内容 |
|---|---|
| 発生 | 2026-10-07 01:33:33 JST。試験的な接続テスト (2 戦、01:20〜01:32) の終了処理 (`scripts/end_connection_test.sh` → `tools.party_improvements --session --measure`) が、実行中の測定 run `improve_20261007_0100` (01:00 起動) を検知できず、2 本目の run `improve_20261007_0133` を起動した |
| 検知 | 2026-10-07 01:36 頃、運用側 (Claude Code のセッション) が、ユーザーの依頼で試験のログを確認していて終了処理の出力「測定を起動しました: run improve_20261007_0133」に気付いた |
| 復旧 | 2026-10-07 01:37 頃、運用側の判断で 2 本目の親プロセス (pid 7777) に TERM を送った (ユーザーには事後報告)。run.log の最終行は 01:34:19 (S3)、子プロセス (測定) は起動前。`pgrep -fl improve_20261007_0133` は空。1 本目 (pid 99419、S8a の参照 3 腕) は影響なく継続 |
| 影響 | 2 本目が S0〜S3 を動いた約 4 分間 (01:33:33〜01:37 頃)、1 本目の S8a の測定 (3 並列) と CPU を分け合った。1 本目の結果への影響は小さいと見るが未計測 |
| 失われた生データ | なし。2 本目は自分の run ディレクトリにだけ書き、season_pin は読むだけ (`fixed:gen9championsbssregmc`)。1 本目のファイルは書き換わっていない |

## 2. 前提

- 接続テストの終了処理は、そのセッションの対戦から「動きづらかった相手」の重みを作り、現行 + 近傍 3 並びの測定 run
  (`tools.team_build.run`、medium、5 並列、数時間) を nohup で起動する (`tools/party_improvements.py` の `launch_measurement`)。
- run は同時に 1 本の想定。`launch_measurement` は起動前に `active_measurement()` で走っている run を探し、あれば
  `{"skipped": "構築 run が実行中 …"}` を返して起動しない設計だった。
- 2026-10-07 01:00 には、第19回の勝敗の訂正を受けた取り直しの run `improve_20261007_0100` が動いていた
  (docs/incidents/reports/2026-10-06-merge-during-auto-started-run.md の復旧)。

## 3. 何が起きたか

`active_measurement()` (修正前):

```python
res = subprocess.run(["pgrep", "-fl", "-m", "tools.team_build.run"], capture_output=True, text=True)
lines = [ln for ln in res.stdout.splitlines() if "-m tools.team_build.run" in ln]
return lines[0] if lines else None
```

macOS の pgrep に `-m` という option は無い (Linux の procps にも無い)。実行すると

```
pgrep: illegal option -- m
usage: pgrep [-Lfilnoqvx] [-d delim] [-F pidfile] [-G gid] ...
```

で rc=2、stdout は空。関数は rc を見ずに stdout だけを読むので、**run が走っていても常に None** を返し、起動に進んだ。
stderr は `capture_output=True` で捕まえたまま捨てられ、終了処理の側も `python -m tools.party_improvements --session --measure 2>/dev/null`
で stderr を捨てている。

| 時刻 (JST) | 出来事 | 根拠 |
|---|---|---|
| 01:00:06 | 取り直しの run `improve_20261007_0100` を起動 (pid 99419) | run.log「start (commit c29bca4c)」 |
| 01:05:09 | 同 run が S7-13 (S8a の参照 3 腕、各 300 戦) に入る | run.log |
| 01:20:03 | ユーザーが操作パネルから接続テストを開始 (試験、2 戦) | logs/control_panel/20261007_012003__start.log |
| 01:32:04 | ユーザーが操作パネルから終了処理 (一括監査あり) を実行 | logs/control_panel/20261007_013204__end.log |
| 01:33:31 | 終了処理が重みファイル session_threats_20261007_0133.json を書く (2 戦分: milotic / blaziken ほか 6 種) | ファイルの更新時刻 |
| 01:33:33 | 2 本目の run `improve_20261007_0133` が start (seed 304411) | run.log |
| 01:34:19 | 2 本目が S3 (session threat weights 6 種) を通過。これが最終行 | run.log |
| 01:35:52 | 終了処理の一括監査 (claude-opus-5-5、140 秒) が終わり、終了処理が完了 | end.log の更新時刻 |
| 01:36 頃 | 運用側が end.log の「測定を起動しました: run improve_20261007_0133」に気付き、`pgrep -fl -m tools.team_build.run` を手で実行して illegal option を確認 | この記録 |
| 01:37 頃 | 運用側が `pkill -TERM -f "tools.team_build.run --run-id improve_20261007_0133"` を実行 (pkill の時刻は記録していない) | この記録 |
| 01:39 | `pgrep -fl improve_20261007_0133` が空。1 本目 (99419) と子 3 本 (827 / 828 / 831) は継続 | pgrep |

異常のサインは終了処理の出力に残っていた: 「測定を起動しました: run `improve_20261007_0133`」が、1 本目の run を起動した本人
(運用側) の目には矛盾として映った。終了処理の側に「run が実行中」の表示は無い (判定が空だったため)。

## 4. 被害の内訳

| 対象 | 実測 | 無傷か |
|---|---|---|
| 2 本目の run `improve_20261007_0133` | S0〜S3 を約 4 分。成果物は manifest / s02 / s03 まで。測定 (子プロセス) は起動前 | 不要な run。結果は無い。ディレクトリは残す (この記録の証拠) |
| 1 本目の run `improve_20261007_0100` | S8a の参照 3 腕 (300 戦ずつ) の最中。2 本目と重なった約 4 分間だけ CPU を分け合った。ファイルの書き換えなし | 結果の数値への影響は未計測 (各腕の seed は固定で、対戦の結果は CPU 負荷で変わらない。時間だけ延びた) |
| 接続テストの試験 2 戦の記録 | 対戦ログ 2 本、終了処理の集計・決定監査・相手バンク (対戦 207 → 構成 203)・一括監査は正常に終わった | 無傷 |
| 重みファイル session_threats_20261007_0133.json | 試験 2 戦から作られた重み (7 種)。使われていない | 残す (害なし) |
| season_pin / registry | 読むだけ (fixed)。書き換えなし | 無傷 |

## 5. 失われたもの

- 生データ: なし。
- 派生物: なし (2 本目は途中で止めたので成果物も無い)。
- 時間: 2 本目の約 4 分の CPU。1 本目の遅れは数分以内と見る (未計測)。

## 6. なぜ防げなかったか

1. **OS コマンドの option を、実際にそのコマンドを呼ぶテストで確かめていなかった。** `tests/test_party_improvements.py` には
   `measure_command` (引数の組み立て) のテストはあったが、`active_measurement` のテストは無かった。
2. **失敗が黙って「run なし」になる形だった** (fail open)。pgrep の rc を見ず、stderr も捨てていた。
3. **本番でこの経路が試されたのは今回が初めて。** 10/6 19:13 の自動起動のときは走っている run が無く、判定が空でも結果は同じだった。
4. **気付けたはずのサイン**: 操作パネルは `MEASURING_PATTERN` で「構築の測定 run: 実行中」と警告を出していた (今回のテスト中も表示)。
   終了処理の側はこの判定を使っていない。

## 7. 復旧手順

```
pkill -TERM -f "tools.team_build.run --run-id improve_20261007_0133"
pgrep -fl improve_20261007_0133          # → 何も出ない (01:39)
pgrep -fl "tools.team_build.run --run-id"  # → 99419 (improve_20261007_0100) の 1 本だけ
```

1 本目はそのまま継続 (結果は `python -m tools.party_improvements --report improve_20261007_0100`)。

## 8. 恒久対策

| 変更 | ファイル | 回帰テスト |
|---|---|---|
| pgrep の引数を純粋関数 `pgrep_argv()` にし、option は `-f` と `-l` だけにする | tools/party_improvements.py | `test_active_measurement_detection` (引数に `-m` が無い、実際の pgrep がその引数を受け付けて rc が 0 か 1) |
| 出力の読み方を `measurement_lines()` に分ける | 同 | 同 (run 本体の行だけを取り、watch / 子プロセスの行は取らない) |
| `active_measurement(run=None)`: pgrep の rc が 0 / 1 以外なら「pgrep が使えない (rc=…)」の文字列を返し、`launch_measurement` が起動を見送る (fail closed)。表示は「構築 run が実行中か確認できない」 | 同 | 同 (rc=2 を差し込んで見送ることを確かめる) |

案 (未実装): 終了処理が操作パネルと同じ判定 (`MEASURING_PATTERN`: run 本体 + `check_advisor_player`) を使う。run の起動時にロックファイルを
置き、`team_build_nohup.sh` の側でも二重起動を拒む。終了処理の `2>/dev/null` をやめて stderr をログに残す。

## 9. 教訓 / 未対応の課題

1. **外部コマンドの引数は、そのコマンドを本当に呼ぶテストで確かめる。** モックだけでは option の誤りは見えない。
2. **安全装置は失敗したら止まる側に倒す。** 「見つからなかった」と「調べられなかった」を同じ None にしない。
3. **自動起動する処理には、起動した側の記録に「何を確かめて起動したか」を残す。** 今回の出力には判定の結果が無く、
   2 本目が起きたことに気付くには pgrep を手で叩く必要があった。

未対応: 8 章の案 3 つ。1 本目の run の所要時間への影響の計測 (終了後に S8a の所要を 10/6 の run と比べる)。
学習 (RL) には影響しないので、champions_agent/train/training_changes.json には追記しない。
