# 外部データの取得・配置と常駐 8100 の適用報告 (2026-11-03)

ユーザーの「手動配置と常駐 8100 の変更の適用をあなたにやって欲しい」という実行依頼に基づき Codex が実施した。以前の「開発者が実行する」という分担を、この依頼の範囲で変更した。取得と適用に必要な実行権限は承認され、今回の自動承認レビューによる拒否は無かった。

## 開発担当への返信

### 1. 常駐 8100 は適用・検証済み

- 適用前に 8100 の利用クライアントと関連する学習・評価ジョブが無いことを確認した。PID・コマンド・作業ディレクトリを照合し、旧常駐 PID 1764 とその子だけを停止した。
- `config/config.js` に localhost 限定と、一時サーバーで使用した外向き通信抑止の設定を適用。`server/ip-tools.ts` には既存の Tor 取得ガードの 1 hunk だけを適用した。
- `scripts/ensure_showdown.sh 8100` でビルド・再起動。適用時刻は 2026-11-03 13:03:55 JST、新しい親 PID は 85909。待受は `127.0.0.1:8100`。`--no-security` は従来どおりであり、待受範囲を localhost に限定した。
- 設定は `bindaddress=127.0.0.1`、`noNetRequests=true`、`loginserver=''`。ビルド済みコードにも Tor 取得の条件を確認した。
- コードの識別は **d849b220082e113d8a17303509fb44d420c543d5 + patched**。HEAD だけで無改変と扱わないこと。差分と hash は下記に保存した。
- 再起動後の Node は既にインストールされていた v26.12.1。旧プロセスは v26.8.2 を使用していた。今回 Node や Python のインストール・更新は行っていない。
- 8000・3000・8010 のプロセスは再起動していない。同じ PID の待受が続いていることを確認した。

検証結果:

| 確認 | 結果 | 範囲 |
| --- | --- | --- |
| 外向き接続の観測 | 675.459 秒、1163 回、外向き接続先 0 件、観測エラー 0 件 | 起動時から新しい親と子を追跡。0.5 秒の待機とコマンド実行を繰り返し、10 分周期を超えて観測 |
| Reg M-C の接続・対戦 | 1 戦、11 ターンで両クライアントが完走 | poke-env の MaxBasePowerPlayer、選出固定。Foul Play の完走・強さ・速度の検証ではない |
| Foul Play のローカルログイン | `OpsFP1103` で成功、HTTP 呼び出し 0 回、0.036 秒 | 既存の `smoke_local_login.py` で確認 |

通信観測はソケットの間欠観測であり、観測間に開始・終了する短い接続まで不存在を証明するものではない。localhost の対戦接続は正常な通信として許容した。対戦煙試験の 1.326 秒は対戦全体の経過時間であり、B0 の意思決定の p50/p95 として扱わない。human_battle のブラウザ画面は今回の検証対象外。

バックアップと識別値:

- 元の設定: `/Users/kenta/GitHub/PokemonChampioons_Adviser/pokemon-showdown/config/config.js.bak.codex.20261103` (権限 0600)
- 再起動前のサーバーログ: `/Users/kenta/GitHub/PokemonChampioons_Adviser/logs/showdown_detached.before_codex_20261103.log`
- 適用後 config SHA-256: `b94f2b1975258604c269d46baa546e9bce0a4dd14fb048f81f06ca26d3167b2e`
- ソース差分 SHA-256: `25cfa30f64a14d97b7499bc7ffa91d94a3e35215b8ab93452734636e7526eeb9`
- dist/server/ip-tools.js SHA-256: `9a8f688032e0b08e3efc6ef5a2166468ae0b968eab2eaab8233d2ad4ee0022a6`

今後 Showdown を更新するときはこの差分と設定を確認する。設定は gitignore 内、ソース差分は clone 内の未コミット変更であり、本体の git commit だけでは再現できない。

### 2. 外部データは 1 件を取得・配置・固定、3 件は指定 URL が 404

robots.txt と公開利用条件のページを確認したうえで、既存 manifest の指定 URL に取得を試みた。認証情報は使用していない。取得できなかった 3 件は今回の権限拒否ではなく、配布先の HTTP 404 である。

| データ | 取得結果 | 配置・証拠 |
| --- | --- | --- |
| [Smogon 2026-09 Reg M-C cutoff 0](https://www.smogon.com/stats/2026-09/chaos/gen9championsbssregmc-0.json) | HTTP 200。形式、対象規制、cutoff を検証済み | `data_pinned/smogon_chaos_cutoff0/gen9championsbssregmc-0.json` |
| [Foul Play full sets](https://data.foulplay.cc/gen9championsbssregmc/pokemon_full_sets.json) | HTTP 404 | `data_pinned/retrieval_20261103/foulplay_full_sets/` |
| [Foul Play replay moves](https://data.foulplay.cc/gen9championsbssregmc/replay_moves.json) | HTTP 404 | `data_pinned/retrieval_20261103/foulplay_replay_moves/` |
| [Showdown sample sets](https://play.pokemonshowdown.com/data/sets/gen9championsbssregmc.json) | HTTP 404 | `data_pinned/retrieval_20261103/showdown_sample_sets/` |

上表の相対パスの起点は `/Users/kenta/GitHub/PokemonChampioons_Adviser-worktrees/external/`。

Smogon 原本は 2,359,399 bytes、334 種の項目、69,462 試合の統計。SHA-256 は `1a4462eb17c026e4ce83580ba9b62cc37ec6626ab4e534c75cc14dd1a4b340fb`。既存の `pin_data.py` で形式を検証して固定し、Foul Play 用キャッシュを生成した。キャッシュの中身が原本の `data` と同一であることを再確認した。

キャッシュ: `data_pinned/fp_cache/smogon_stats_cache/gen9championsbssregmc-0.json`。SHA-256 は `e4fcbb4268d60c690ddd7e850431d9712f879800b20da33575ec5562d0541413`。

取得時刻・URL・HTTP status・hash・レスポンス原本を保存し、manifest に追記した。変更前 manifest は `data_pinned/manifest.before_codex_20261103.json`。過去の取得試行の履歴も残した。公開バケットの一覧も保存し、その一覧には M-B の full sets はあるが M-C のキーは無かった。これにより別の取得方法で同じ URL を試し続ける理由は無い。ただし将来の配布や他の配布先まで否定する結果ではない。

空の JSON、M-B のデータ、名称だけ M-C に変えた代替物は配置していない。オフライン時の欠損停止も変更していない。`run_after_cargo.sh` の既定の cutoff 1500 も変更しておらず、今回配置した cutoff 0 で B0 の本試験を実行したという意味ではない。

### 3. 次の開発への具体的な提案

「4 件すべてを手動配置するまで待つ」という前提を更新する。現状は 3 件が指定配布先に無いため、ユーザーの手動操作でも解消できない。

Foul Play の元の取得経路は取得失敗時に空の集合を使い、探索側には TeamDatasets から候補が得られなければ SmogonSets を使う処理がある (`fp/data/sets/base.py`、`fp/search/standard_battles.py`)。この挙動を根拠に、固定契約に「取得未完了」「確認済みの配布なし」「取得済み」を明示することを提案する。

具体的には、確認済みの 404 を manifest と実行条件に固定した場合だけ、該当する補助データを使わない経路を有効にする。未確認の欠損・hash 不一致は引き続き停止する。これにより、今回取得した Smogon cutoff 0 を用いる条件を別名で記録して対戦完走・意思決定時間・RSS の実測へ進められる。将来 3 件が配布されたら別条件として比較する。条件を混ぜない。

このソフトウェア変更と B0 の本試験は今回の取得・常駐設定の作業には含めていない。Foul Play のローカル接続は確認できたが、B0 の正式採否は引き続き未確定。

### 4. 証拠の置き場

`/Users/kenta/GitHub/PokemonChampioons_Adviser-worktrees/external/operations/20261103_local_setup/`

適用・観測・対戦のスクリプト、`applied.json`、`applied-source.diff`、`outbound_samples.jsonl`、`outbound_summary.json`、`smoke_battle.json`、データ検証結果と `sha256.json` を保存した。Foul Play ログインの結果は実行ツールの出力を本報告に転記した。取得の原本とメタデータは `data_pinned/retrieval_20261103/` に保存した。

スクリプトは今回の実行の証拠であり、再適用用の汎用コマンドではない。再検証する場合は別の出力先を用意し、今回の証拠を上書きしない。

## 開発者向けの説明

8100 の変更は完了し、同じ Mac から引き続き対戦できる。ユーザーが改めて設定変更・再起動する必要はない。

データについても取得と配置を代行した。1 件は実データを固定できたが、残る 3 件は配布元に指定ファイルが無い。次に必要なのは、利用できる統計から対戦を実行するための明示的な条件設定である。追加の大規模学習を前提とせず、既存の Smogon 利用経路を活用する実装と実測を進めるのが妥当である。
