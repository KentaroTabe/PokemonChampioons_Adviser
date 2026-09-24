# インシデント記録: 日次定点の評価プロセスが timeout なしで 5 日間ハングし、定点が 6 日分欠測

- **発生**: 2026-09-18 22:43 JST(日次定点の 3 本目の評価 `vs_agents` 3,000 戦の開始時刻。
  実際に止まった時刻は出力が無く**未確認**)
- **検知**: 2026-09-24 19:58 JST(6 日後。構築 run arch_0924 の測定が前回より遅い原因を
  `ps` で切り分けている最中に、5 日 21 時間走り続ける `evaluate` プロセスを見つけた)
- **復旧**: 未実施(2026-09-24 時点)。ハングしたプロセスを止めれば親が残りの評価を続けて
  終了するが、構築 run arch_0924 の測定中は追加の負荷を避けたいので、手順(§7)を提示して
  run 終了後に実施する
- **影響**: 日次定点 `logs/progress_tracking.jsonl` が **9/18〜9/23 の 6 日分欠測**(9/24 も
  プロセスが残る限り発火しない)。週次アンカー(9/23 予定)が未保存。**本番の学習・
  チェックポイント・構築 run の結果は無傷**(学習は 9/17 から停止中、`evaluate` は `--no-save`)
- **失われた生データ**: なし。失われたのは日付つきの定点 6 行(§5)

---

## 1. 前提: 日次定点の仕組み

学習の進捗と配布条件を毎日同じシードで測り、`logs/progress_tracking.jsonl` に 1 行追記する
仕組み(`tools/track_progress.py`)。launchd の calendar ジョブ
`com.championsadviser.track-progress`(毎日 21:50)が `champions_agent/scripts/track_progress_daily.sh`
を起動し、標準出力は `logs/track_progress_daily.log` に追記される。

| 順 | 評価 | 戦数 | 用途 |
|---|---|---|---|
| 1 | current + 相性選出 vs ベンチ | 3,000 | 学習の生の進捗 |
| 2 | _best + モデル選出 vs ベンチ | 3,000 | 配布条件(凍結参照。動いたら軸側の変化を疑う) |
| 3 | current vs エージェント(3 性格の _best 巡回) | 3,000 | 第 2 の評価軸 |
| 4 | current vs balance_best(直接対戦) | 1,000 | 主指標(8/18 導入) |
| 5 | EMA 方策の同指標 | 1,000 | 振動対策の観察 |

各評価は `champions_agent.train.evaluate` を **`subprocess.run` で逐次**起動し、標準出力の
`[evaluate] {...}` 行を読む。5 本のあとに週次アンカー保存(`save_weekly_anchors`、7 日間隔)、
凍結参照の逸脱警告、停滞判定を行い、最後に行を書く。**行は最後にまとめて書く**ので、途中で
止まるとその日の値は一切残らない。

通常の所要は 11,000 戦で約 24 分(9/17: 21:50:03 → 22:14:15)。

## 2. 何が起きたか

### 2-1. 原因のコード

`_run_eval` は子プロセスを **timeout なし**で待っていた。`evaluate` 側には `--timeout`
(SIGALRM で自己終了)が 2026-08 に用意されていたが、日次定点はそれを渡していなかった。

```python
# tools/track_progress.py (修正前)
r = subprocess.run(
    cmd,
    cwd=REPO, env=env, capture_output=True, text=True)   # timeout 無し、--timeout も渡さない
```

同じ「Showdown を待つ子プロセス」でも、構築パイプラインの選出データ収集は 9/7 の事故
(空理由の team rejected で poke-env が待ち続けた)を受けて `BUILD_COLLECT_TIMEOUT_PER_1K`
で打ち切るようになっていた。日次定点は同じ穴を持ったまま残っていた。

### 2-2. 時系列

| 日時 (JST) | 出来事 | 根拠 |
|---|---|---|
| 09-18 16:48 | 構築 run arch_0918 開始(9/19 19:43 まで 26.9 時間) | run.log |
| 09-18 19:29〜22:44 | arch_0918 の S8a レース round 1(12 腕 × 200 戦、5 並列) | run.log |
| 09-18 21:50 | launchd が日次定点を発火(ラッパーの実起動は 21:59:30。9.5 分の遅れは**未確認**) | `ps -o lstart`、日次ログの見出し行 |
| 09-18 21:59〜22:43 | 評価 1・2(計 6,000 戦)。通常 13 分のところ 44 分(run と競合) | 評価 3 の開始時刻から逆算 |
| 09-18 22:43:51 | 評価 3(`--opponent agents --battles 3000`、pid 17844)開始 | `ps -o lstart` |
| 09-18 22:44〜 | arch_0918 が S7 適応(4 候補 × 最大 14,000 戦の収集)へ。負荷はさらに上がる | run.log |
| 09-19 00:29〜00:30 | arch_0918 側で収集の websocket 断が 3 件(`collect rc=124`、seed を変えて再試行) | run.log 113〜115 行 |
| 09-19〜09-23 21:50 | launchd は前回のジョブが生きているので発火しない。ログに痕跡なし | `launchctl print`(runs=28、pid 16210 のまま) |
| 09-24 19:58 | 検知 | 本レポート |

評価 3 の内部で何を待っていたかは、出力が無いので**未確認**。プロセスの状態は次のとおり。

- 経過 5 日 21 時間に対して CPU 時間 2 分 48 秒(ハング後は一切計算していない)
- `sample` の主スレッドは asyncio のイベントループ(`kevent`)で待機、他スレッドは全て
  `pthread_cond_wait`。`lsof -i` に TCP 接続が無い(Showdown との websocket は既に閉じている)。
  子プロセスなし。常駐メモリ 1.3 GB(ピーク 1.8 GB)
- 同時刻に arch_0918 側で websocket 断が起きているので、負荷による接続断のあと poke-env が
  対戦の完了を待ち続けたと考えるのが自然だが、**因果は未確認**

### 2-3. 記録に残っていたサイン

- `logs/track_progress_daily.log` の最終行が 6 日間ずっと
  `===== 日次定点: Fri Sep 18 21:59:30 JST 2026 =====`(見出しだけで `done` が無い)。
  評価 1・2 の結果行も無い。標準出力がファイル向けのブロックバッファで、プロセス終了まで
  書き出されないため
- `scripts/status.sh` の launchctl 節に `16210  0  com.championsadviser.track-progress`
  (pid 付き = 実行中)が 9/18 から出続けていた。9/23〜24 に何度も status.sh を実行したが、
  その列を読んでいなかった
- `logs/progress_tracking.jsonl` の最終行が 2026-09-17 21:50 のまま

## 3. 被害の内訳

### 3-1. 日次定点

| 日 | 定点 | 状態 |
|---|---|---|
| 09-17 21:50 | あり | 47 行目(最終行)。current 0.528 / best 0.551 / vs_agents 0.4627 |
| 09-18 21:59 | **なし** | 開始したが未完。評価 1・2 の値はプロセス内のバッファにあり未書き出し |
| 09-19〜09-23 | **なし**(5 日) | launchd が発火せず。ログに痕跡なし |
| 09-24 21:50 | 発火しない(予定) | pid 16210 が残る限り |

### 3-2. 週次アンカー

`anchors/` の最新は `*_20260916.zip`(9/16 22:06)。次は 9/23 以降の定点で保存されるはずだったが、
定点が走っていないので未保存。学習は 9/17 20:37 で停止しており(`battle_policy_balance.zip`
の更新時刻)、保存されるはずだった重みは現在のチェックポイントと同一。

### 3-3. 無傷だった範囲

| 範囲 | 状態 | 根拠 |
|---|---|---|
| `logs/progress_tracking.jsonl` の既存 47 行 | 無傷 | 追記のみ。最終行 9/17 |
| チェックポイント(current / _best / EMA) | 無傷 | `evaluate` は `--no-save`。更新時刻 9/17 20:37 |
| anchors 4 世代 × 3 性格(8/25〜9/16) | 無傷 | ファイル一覧 |
| 構築 run arch_0918 の結果 | 完走、holdout PASS | ハング中のプロセスは CPU 0% で測定を邪魔していない。ただし 21:59〜22:43 の評価 1・2 と S8a round 1 は互いに遅くしている(round 0 の 78 分に対し round 1 は 195 分。切り分けは未確認) |
| 構築 run arch_0924(9/24 測定中) | 影響なし | 同上(常駐メモリ 1.3 GB を占有していた点のみ) |

## 4. 失われたもの

| 種別 | 内容 | 復元 |
|---|---|---|
| 日付つきの定点 6 行(9/18〜9/23) | 不可 | 学習停止中で重みは 9/17 20:37 から不変。次回の定点が同じ重みを測るので、**方策の進捗としての情報損失は無い**。失ったのは「凍結参照 (_best) の逸脱監視」の 6 日間の空白 |
| 週次アンカー 9/23 | 次回の定点で保存される(重みは同一) | 可 |
| 時間 | ユーザーの作業は止めていない | — |
| メモリ | 1.3〜1.8 GB を 6 日間占有(構築 run の測定と同居) | 停止すれば解放 |

## 5. なぜ防げなかったか

1. **子プロセスに timeout が無かった**。`evaluate --timeout` は存在したのに渡しておらず、
   `subprocess.run` にも timeout が無い。構築パイプラインでは 9/7 に同種の穴を塞いだが、
   日次定点は対象に入っていなかった(「Showdown を待つ subprocess」の棚卸しをしていない)
2. **launchd の calendar ジョブは前回が生きていると発火しない**。ラッパーには二重起動を
   スキップしてログに書く分岐があるが、launchd が起動しないので分岐に到達せず、ログは
   「静か」なだけで「異常」に見えなかった
3. **標準出力がブロックバッファ**で、評価 1・2 の結果すら日次ログに出なかった。行ごとに
   書かれていれば「評価 3 から先が無い」と読めた
4. **経過時間を見る仕組みが無かった**。`status.sh` は launchctl の行を出すだけで、
   日次ジョブが何時間走っているかを判定しない

気付けたはずのサイン: `logs/track_progress_daily.log` の最終行が `done` ではない状態が
6 日続いたこと(§2-3)。

## 6. 復旧手順(未実施。構築 run arch_0924 の終了後に行う)

ハングしたプロセスを止めると親(`tools.track_progress`、pid 16219)は評価 3 を `None` として
評価 4・5 と週次アンカー保存に進み、**9/18 21:59 の日付**で行を書いて終了する
(行の日付は開始時に決まる。評価 1・2 の値はその時点の測定値)。所要は 2,000 戦分(通常 5 分)。

```bash
kill 17844
```

続いて次を確認する。

```bash
bash scripts/status.sh
```

```bash
tail -n 25 logs/track_progress_daily.log
```

確認点: status.sh の「日次定点」節が「(停止中)」になり、日次ログに `vs_agents: None` と
`===== done` が出て、`progress_tracking.jsonl` に `"date": "2026-09-18 21:59"` の行が増える。
その後は 9/24 21:50 の定点が通常どおり発火する。注意: 親プロセスは Python 3.9 の venv で
起動しているが、9/24 に `.venv` を 3.12 へ切り替えたため、残りの評価 4・5 が
`sys.executable` 経由でどちらの環境で走るかは**未確認**(失敗しても `None` で記録される)。

run の測定中に止める場合(メモリ 1.3 GB の解放が目的)は、直後の評価 4・5 と、同日 21:50 の
日次定点(11,000 戦、約 25〜40 分)が run と重なり、その日の定点の絶対値は競合で歪む
(`track_progress_daily.sh` の注記: 並列評価は水準を歪める)。

## 7. 恒久対策(2026-09-24 実装)

| 変更 | 内容 |
|---|---|
| `champions_agent/config.py` | `TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K = 900`(1,000 戦あたりの秒数、通常の約 7 倍)、`TRACK_PROGRESS_TIMEOUT_GRACE_S = 60` |
| `tools/track_progress.py` | `eval_timeout_s(battles)` で戦数比例の秒数を出し、`evaluate --timeout`(自己終了)と `subprocess.run(timeout=+猶予)`(強制終了)の二段で打ち切る。打ち切りは `[track_progress] TIMEOUT ...` を日次ログに書き、行に `eval_timeouts` として残す(該当値は `None`)。結果行が無い場合も rc と stderr 末尾を書く。標準出力を行バッファにする |
| `config/jobs.env` + `scripts/status.sh` | 「日次定点」節を追加。`tools.track_progress` の経過時間が `TRACK_PROGRESS_MAX_MINUTES`(240)を超えたら ⚠ を出し、`evaluate` プロセスを列挙する(操作パネルの「稼働状況の詳細」からも見える) |
| `tests/test_track_progress.py`(CI 登録) | timeout が戦数に比例し 1,000 戦未満でも下限を持つ / `--timeout` と subprocess timeout を渡す / 強制終了・自己終了のどちらも `None` + 記録 / 結果行なしは timeout 扱いにしない / `deviation_sigma` |

最悪でも 1 日のジョブは 5 評価 × 45 分 = 約 3.75 時間で終わり、翌日の発火を妨げない。

## 8. 教訓 / 未対応の課題

- **外部プロセス(Showdown、claude CLI、node)を待つ `subprocess.run` には必ず timeout を付ける。**
  棚卸し(2026-09-24)で timeout が無いまま残っているもの: `tools/reward_sweep.py` の `evaluate`
  呼び出し、`tools/audit_subtask.py` / `tools/audit_session.py` の `claude` 呼び出し、
  `tools/validate_teams.py` の `node validate-team`。いずれも未対応
- **launchd の calendar ジョブは「静かに欠測」する。** 発火しなかった痕跡は残らないので、
  経過時間か最終成功時刻を見る監視が要る(今回は status.sh に追加。日次ログの `done` の
  日付を見る監視は未対応)
- **構築 run と日次定点の重なり**: 9/18 は評価が約 3.4 倍遅くなり、run 側の S8a round 1 も
  2.5 倍長引いた。run 中は定点を止める/ずらす運用は未決(ユーザー判断)
- 学習に影響する変更ではないが、定点の欠測は `champions_agent/train/training_changes.json`
  に記録した(ベンチ推移の空白の説明用)
