# 常駐 Showdown (8100) を localhost 限定にし、外向き通信を止める手順 (2026-11-02、調査は読み取りのみ、適用は開発者)

レビュー (2026-11-02) の推奨「localhost 限定へ変更。稼働中の処理を確認してから計画的に適用し、一時サーバーと同様に外向き通信も確認する」に基づく。調査は読み取りのみ (Explore)。常駐は操作していない。

## 1. 確認した事実 (2026-11-02 14:47〜15:02)
- 常駐は pid 1764 `node pokemon-showdown start 8100 --no-security`、作業ディレクトリ `pokemon-showdown/`、PPID 1 (launchd)。9/27 13:40:49 に train_forever.sh → train_nightly.sh → `scripts/ensure_showdown.sh` (`tools.spawn_detached`) が起動。Showdown 用の plist は無い。子プロセスは sockets / room-battle / verifier / friends / artemis 2 / battlesearch / datasearch と team-validator-async 3 本。
- `pokemon-showdown/config/config.js` は `config-example.js` と同一 (`cmp`): `bindaddress='0.0.0.0'`、`loginserver` は公式、`routes` は公式 4 件、`noNetRequests` 未定義、`backdoor=true`、`repl=true`。待受は `*:8100` (IPv4)。clone は d849b220、`git status` は空。
- `dist/server/ip-tools.js:598` に `void IPTools.updateTorRanges();` が無条件で残る (読み込み時 1 回、check.torproject.org)。`dist/lib/net.js:183` は `Config.noNetRequests` が真なら `getStream()` が例外を投げる。dist で外向き HTTP を出すのは `lib/net.js` の `Net()` だけ (sockets.js は SSL の待受用)。
- **稼働中の外向き通信を実測**: 0.4 秒ごと 1400 回の観測で、pid 1764 から `104.20.39.239:80` (14:53:59〜14:54:06) と `104.20.39.239:443` (15:00:02〜15:00:15)。:80 は 10 分ごとの `reportUserStats` → `LoginServer.request('updateuserstats')` (rooms.ts:1289-1292、1458-1468) と見られる (未確認)。:443 の発生元は未確認。DNS は引いていない (104.20.39.239 は一時サーバーの修正前の観測と同じ宛先)。
- 稼働状況 (14:47): 学習・測定・接続テスト・煙試験・一時サーバー (8130) はいずれも動いていない。8100 への接続クライアントは 0 本。uvicorn 8000 (0.0.0.0) とフロントエンド 3000、操作パネル 8010 は稼働中。
- 8100 の使い手はすべて localhost / 127.0.0.1 宛て (champions_agent/env/showdown_env.py:59-61、tools/usefulness_p1.py:1134、arc_remeasure.py:328、team_proposal.py:266、control_panel.py:170-171)。LAN の IP や `.local` で 8100 を指す箇所は無い。poke-env のログインは password None なら公式の action.php に POST しない (ps_client.py:248)。

## 2. 変更案
### (a) config.js の末尾への追記だけで済む範囲
- `exports.bindaddress = '127.0.0.1';` と、一時サーバーで外向き 0 本を確認済みの `../PokemonChampioons_Adviser-worktrees/external/patches/showdown-temp-server-config.js` の全文 (`noNetRequests=true`、`loginserver=''`、`routes` は全部 localhost、`proxyip`・`crashguardemail`・`dnsbl`・`remoteladder`・`replaysdb`・`usepostgres`・外部 API 鍵 4 種の明示的な無効化、`disablehotpatchall=true`)。
- 止まるもの: 起動時の `invalidatecss`、10 分ごとの `updateuserstats`、`addreplay` と ladder の送信、チャットコマンドの外部 API、画像の寸法取得、Tor 範囲の取得 (`Net()` が例外を投げ、取得側は catch する)。観測した :443 も `Net()` 経由なら止まる (発生元は未確認)。
- config.js は clone 内でも gitignore (`/config/*`) なので、clone の HEAD・`git status`・`scripts/showdown_update.sh` の未コミット変更の検査に影響しない。

### (b) ip-tools の差分 (任意、保険)
- `server/ip-tools.ts` の `void IPTools.updateTorRanges();` を `PS_FETCH_TOR_RANGES==='1'` のときだけに変える (`external/patches/showdown-ip-tools-no-outbound.diff`、1 hunk)。
- 影響: 常駐のコードが d849b220 と一致しなくなる (HEAD は変わらないので `tools/team_build/manifest.py` や `tools/inheritance_eval/conversion.py` の git_head は差分を記録しない)。`scripts/showdown_update.sh:16-19` は clone に未コミット変更があると中断するので、レギュレーション更新の前に差分を退避する。`pokemon-showdown/` は本体の gitignore の中の clone なので差分は本体のリポジトリに残らない (記録は patches 側のファイルと sha256)。

### bindaddress 127.0.0.1 の影響
- コードからの利用は全部 localhost なので影響なし。待受は今も IPv4 だけ。
- 影響が出うる経路: 別の端末からの接続 (コード上は無し。手で使っていたかは未確認)、`tools/human_battle.py` (同じ Mac のブラウザで公式クライアント `play.pokemonshowdown.com/~~localhost:8100/` を開く経路。`routes` を localhost にした後の表示・名前の登録は未確認)。`/hotpatch all` と `/savereplay` は使えなくなる (scripts / tools に使用箇所なし)。

## 3. 適用の手順 (開発者が実行)
1. 稼働中の処理を確かめる: `scripts/status.sh`、`pgrep -fl 'train_forever|train_nightly|train_battle|smoke_train|champions_agent.train|check_advisor_player|check_search_expert|team_build|usefulness_p1|arc_remeasure|human_battle|collect_selection|evaluate_team|team_proposal'` が空、`lsof -nP -iTCP:8100` に ESTABLISHED が無い、接続テストの開始マーカーが無い。
2. config をバックアップして追記する (`pokemon-showdown/` の中で):

```bash
cp config/config.js config/config.js.bak.20261102
```

```bash
printf "\n// 常駐 8100: localhost 限定 (2026-11-02)\nexports.bindaddress = '127.0.0.1';\n" >> config/config.js
```

```bash
cat /Users/kenta/GitHub/PokemonChampioons_Adviser-worktrees/external/patches/showdown-temp-server-config.js >> config/config.js
```

```bash
node -e 'const c=require("./config/config.js"); console.log(c.noNetRequests, JSON.stringify(c.loginserver), c.bindaddress)'
```

   出力が `true "" 127.0.0.1` になること。
3. (b) も当てる場合: `git -C pokemon-showdown apply --check <diff>` で確かめてから `apply`。build は次の `start` で自動的に走る (手で走らせるなら `cd pokemon-showdown && node build`)。`grep -n updateTorRanges dist/server/ip-tools.js` で条件付きになっていることを見る。
4. 再起動する: `pkill -f "pokemon-showdown start 8100"` で 1764 を止める (`scripts/stop_training.sh showdown` は train の launchctl disable 等の副作用があるので避ける)。`pgrep -fl pokemon-showdown` で子プロセスが残っていないか見て、残っていれば個別に止める。`bash scripts/ensure_showdown.sh 8100` で同じ仕組みで起動し直す。
5. 再起動後に確かめる: `lsof -nP -iTCP:8100 -sTCP:LISTEN` が `127.0.0.1:8100`。外向きの観測は **11 分以上** 続ける (10 分ごとの送信があるため。`lsof -nP -i -a -p <pid 群>` を 0.5 秒ごとに読み LISTEN 以外が 0 本)。`logs/showdown_detached.log` に `Worker 1 now listening on 127.0.0.1:8100`。形式の配信とクライアントの接続を軽い poke-env の 1 戦で確かめる (最小限のコマンドは未選定)。

## 4. 未確認
104.20.39.239 が play.pokemonshowdown.com か、:80 が `updateuserstats` か、:443 の発生元、子プロセスが親の停止に合わせて終わるか、`routes` を localhost にしたときの human_battle の動作、別の端末から 8100 を使った履歴、macOS のファイアウォールの設定、再起動時の `node build` の時間と dist とソースの一致 (ソースは無変更なので一致の見込み)。
