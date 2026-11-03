# B0-dynamic: 動的照合の実行基盤と Showdown 側の正解 (phaseB_20261102)

> 配置の注記 (2026-11-02、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。測定の出力 (manifest.json / cases.jsonl 25 件 / measurements.jsonl 95 行 / gold/*.json 24 ファイル / names_precheck.json / engine_results.jsonl / repeat_results.jsonl / after_cargo/stages.jsonl / 各ログ) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261102/B0-dynamic/`** (リポジトリ外) にある。道具は `external/b0_tools/` (temp_server.sh、check_temp_server.py、dynamic_cases.py、replay_state_variance.py、record_stage.py、build_dynamic_outputs.py、test_b0_dynamic.py、更新した run_after_cargo.sh)。

- 対象: gen9championsbssregmc、Showdown d849b220 (mod champions)、poke-engine f7b147f1 (未 build)、Foul Play 8f541f80 + local-regmc の差分。
- cargo: **無し** (`export PATH="$HOME/.cargo/bin:$PATH"; cargo --version` が command not found)。照合・対戦・速度・揺れの試験側はすべて unexecuted で、成功に数えない (measurements.jsonl のまとめは `fail`: pass 36 / fail 2 / unexecuted 56)。
- データの条件変更: cutoff 0 の代わりに手元の Smogon cutoff 1500 を使う。必須の外部データ 4 件は承認待ちで、FP_OFFLINE_DATA=1 は外していない。型集合が無いので、これは元来の Foul Play の能力の評価ではない。
- 実時間: 約 30 分 (04:05〜04:35 UTC)。正解側の計算 325 秒。本体ツリーと常駐の 8100 / 8000 には触れていない。

## 1. 動的 25 ケースの結果
**区分別の件数: 正確に一致 0、探索上の意図的な近似 0、観測後だけ対応 0、欠落・不具合 0、参照側も未確定 0、unexecuted 25。**
Showdown 側の正解はそろっている (31/31 シナリオ、timeout 0・error 0)。下表の値はすべて Showdown で観測した事実 (simulate-battle の CLI、seed を変えて生成。サーバーは通さない)。

| ケース | Showdown の観測 (急所を除く乱数幅。n は seed 数) |
|---|---|
| mega_salamence_aerilate | Double-Edge 136〜162、反動 45〜53、Body Slam 97〜115 (相手は いかく で -1) |
| mega_golisopod_toughclaws | Iron Head 102〜121 |
| mega_lucarioz_auraguard | Waterfall (接触) 30〜35、Surf 29〜35。参照側の 'TODO check breakable' は扱っていない |
| mega_garchompz_levitate | メガ後の Earthquake は 16/16 で無効 |
| mega_absolz_sharpness | X-Scissor 80〜95 |
| mega_blastoise_launcher | Aura Sphere 106〜124、Shell Smash 後は +2/-1/+2/-1/+2、Dark Pulse 104〜123 |
| mega_starmie_stats | Waterfall 118〜139 (ちからもち込み) |
| mega_timing_speed | メガのターンは 16/16 でメガ後の素早さで先手 (167 < 169 < 189)。2 体目は canMegaEvo が false |
| move_tripleaxel | 命中回数 0 回: 6、2 回: 7、3 回: 47 (1 回で止まった例は 0)。合計 2 回: 49〜54、3 回: 93〜108、平均 85.1 |
| move_makeitrain | 120/95 で、**特攻 -2** (champions の mod)。T1 は 78〜93、-2 後は 39〜46、外れ 3/40 |
| move_crabhammer_syrupbomb | Crabhammer 84〜99 (急所 5/40)。Syrup Bomb 37〜45 (外れ 4/30)、素早さ -1 が 3 ターン続く |
| move_meteorassault_slash | Meteor Assault 115〜136、Slash 49〜58 |
| move_expandingforce_terrain | サイコフィールド下で Expanding Force 79〜93、Psychic 58〜70 |
| move_terrainpulse | エスパータイプになり 43〜50 |
| move_direclaw_secondary | 状態異常 15/60 (どく 8・ねむり 4・まひ 3)。Psychic Seed で特防 +1、かるわざで 60/60 先手 |
| move_steelbeam_redcard | Steel Beam 87〜103、自傷 84 (167 の半分)。がんじょうで 1 残り、レッドカードで 16/16 強制交代 |
| move_status_champions | まひの行動不能 38/280 = 0.136 (区間 0.10〜0.18。1/8 と合う)。ねむりで動けないターン数 1:16 / 2:24。こおり 0:5 / 1:3 / 2:11 (最長 2) |
| ext_lightclay_screens / ext_terrainextender / ext_damprock | どれも 8 ターン (6/6) |
| ext_future_effect_in_search | リフレクター 8 ターン。T7・T8 は 16〜19、切れた後の T9 は 33〜37 |
| transform_imposter / transform_move | 技の PP 5、特性 roughskin、へんしん後の Earthquake 91〜106 / 90〜106 (request の stats はへんしん後も Ditto 自身の値のままなので、PP・特性・ダメージで確認) |
| disguise_mimikyu | 1 発目は技のダメージ 0、ばけのかわで 20 (162 の 1/8) を受けて形態が Busted に変わる。2 発目は 60〜70 (防御 -1 の seed は 88〜99) |
| unknown_name_silent | 静的検査は fail (下の 2)。実行時の確認は unexecuted |

自動では区分を付けないもの (transform / disguise / drag / sturdy / unboost_seq / expiry_chain) は命令列と Showdown の値を並べて記録し、区分はレビューで付ける。

**静的な予想 (未確認。build 後の照合で確かめる)**: poke-engine の表では、メガの特性に違いがある (absolZ は magicbounce、garchompZ は sandforce、lucarioZ は adaptability、golisopod は emergencyexit)。starmie-mega の攻撃の種族値は 140 (Showdown は 100)。技は tripleaxel / makeitrain / crabhammer / syrupbomb / meteorassault / slash の威力・命中が違う。状態異常の定数はまひ 0.125・ねむり 1/3 で Showdown と合うが、こおりの 3 回目で必ず解ける処理は確認できていない。延長の持続は poke-engine では 5 ターン固定。このうち Foul Play が観測後に補うのは lightclay と damprock で、terrainextender は補わない。

## 2. 名前の事前検査 (静的、158 名)
黙って置き換わる名前は 5 個: 特性 auraguard (→ NONE)、持ち物 lightclay / damprock / terrainextender / redcard (→ UNKNOWNITEM)。種族・技・メガの組の欠けは 0。

## 3. 対戦の完走・時間・メモリ
unexecuted (cargo 無し)。1 決定あたりの p50 / p95、RSS、CPU も未測定。Foul Play の対戦は build 後も、FP_OFFLINE_DATA=1 のままだと型集合の 3 ファイルが無いため PinnedDataMissing で止まる見込み (この停止は外していない)。

## 4. 一時サーバー
d849b220 の Showdown を external 配下に clone・build (npm ci --offline 1 秒、node build 3 秒) し、127.0.0.1:8130 だけで待受 (--no-security、config/config.js に bindaddress 127.0.0.1)。形式 gen9championsbssregmc の配信、/version の commit、ローカル専用ログインの確定 (Foul Play のログインで HTTP 0 回) を確認して停止した (ポートは閉じ、残ったプロセス 0)。
**ただし起動中にサーバー自身が外へ 3 本接続していた** (116.202.120.181:443、104.20.39.239:80、172.66.165.159:443)。コード上の候補は `server/ip-tools.ts` の `updateTorRanges()` (モジュール読み込み時に無条件で check.torproject.org を取得) と `server/loginserver.ts` の `invalidatecss` (Config.loginserver の :80)。どの接続がどの処理かは未確認 (DNS は引いていない)。同じコードの常駐 8100 も同じ動きをしている可能性があるが未確認。対処の候補: (a) 容認、(b) config で loginserver 等を無効化 (Tor の取得は止まらない)、(c) clone の ip-tools だけ止める (コードが d849b220 と一致しなくなる)、(d) 通信を遮断した環境で起動。決まるまで `TEMP_SD_ALLOW_STARTUP_OUTBOUND=1` を明示しない限り start は起動しない。

## 5. 実行基盤 (ビルド後に使うもの)
- `run_after_cargo.sh`: check → build → smoke → dynamic → battles → speed → repeat の 7 段。一時サーバーの起動・停止を段の中に入れ、段ごとの結果を stages.jsonl に記録し、1 段に上限時間。summary は unexecuted を成功に数えない (`all_pass: false`)。
- `dynamic_cases.py all`: 正解側 (Showdown) と試験側 (poke-engine-py の generate_instructions) の 45 チェックポイント。試験側は一度も実行していないので、State の組み立て (`build_pe_state`)・命令の解析 (`pe_metric`)・区分の判定 (`judge`) は build 後の最初の実行で直しが出る前提。
- `replay_state_variance.py`: 同じ局面 5 回の行動と visits の揺れ (3 局面。poke_engine 不在で終了コード 3)。
- 単体試験 `test_b0_dynamic.py` 18/18 pass (ログの解析、名前の検査、要約、unexecuted を成功に数えないこと)。既存の `test_fp_local_patches.py` 15/15 pass。

## 6. 残る修正の規模の見積もり
試験側を実行していないので**見積もれない (保留)**。静的な手がかり: 表データの差 (メガ 5 体、技 6 個)、効果コードの新規 (特性 1: auraguard、持ち物 4: 延長 3 とレッドカード)、へんしん / かわりもの (genx に TRANSFORM の参照が 0 で、状態表現の改修が要る可能性)。全部直す方向へ進む前に、build と照合で確かめる必要がある。

## 1 行まとめ
用途: 操作の B0 の動的確認 → 対照: Showdown d849b220 → 正しさ: 正解側 31/31 は生成済み、試験側 45 チェックポイントは未実施、名前 5 個が黙って置換される → 効果: 未測定 → 費用: 正解側 5.5 分。cargo の導入と、一時サーバーの外向き通信の扱いの判断が要る → 採否: 保留 → 未確認範囲: build、試験側の照合、対戦 3 戦、速度、揺れ、外部データ 4 件、常駐 8100 の外向き通信。
