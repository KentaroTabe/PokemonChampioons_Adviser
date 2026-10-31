# B0-check: 操作の外部基準 B0 の第一候補 (Foul Play + poke-engine) の Reg M-C 適合性とこの Mac での速度 (段階 B の入口、phaseB_20261031)

> 配置の注記 (2026-10-31、開発者判断 (b)): 測定の出力 (manifest.json / cases.jsonl / measurements.jsonl 5,254 行) は `logs/inheritance_eval/phaseB_20261031/B0-check/` (gitignore) にあり変更していない。本文は委託先 (サブエージェント) の完了報告の付録で、委託先の report.md の書き込みがハーネスに止められたため、運用側 (Fable) がここに置く。clone と解析スクリプトは `../PokemonChampioons_Adviser-worktrees/external/` (リポジトリ外)。

- 実施: 2026-10-31 12:46〜13:00 (壁時計 約 14 分。上限 90 分)。計算時間は合計 1 秒未満
- 対象規制: gen9championsbssregmc (ローカル Showdown d849b220、mod champions、Flat Rules + VGC Timer)
- 許可された外部アクセス: GitHub の pmariglia/foul-play と pmariglia/poke-engine の clone、PyPI (external の venv に入れたのは websockets 14.1 だけ)。Rust の導入はしていない

## 要点
1. **poke-engine をこの Mac で build できず、Foul Play は起動していない** (cargo / rustc 無し。PyPI の poke-engine は 0.0.0〜0.0.48 の全版で wheel が無く sdist のみ)。確認 3 (対戦)・4 (速度) は unexecuted。
2. 版: Foul Play 8f541f80 (2026-10-03、タグなし)、poke-engine f7b147f1 (2026-10-11、v0.0.48 の後に 31 コミット)。必要な feature は `bss` (= champions + 6→3 のチームプレビュー 60 通り)。Foul Play の requirements の既定は terastallization なので入れ直しが要る。PyPI と同じ版のタグ v0.0.48 では Reg M-C のメガ 5 体 (absolZ / baxcalibur / garchompZ / golisopod / lucarioZ) が欠ける。
3. 名前は種 346/346・技 503/503 にある。食い違いはメガのデータ 6、技の数値 12、持ち物の欠落 22、特性の欠落 1 + 効果コードなし 42 (うち対戦に効く 31)。使用率 (Smogon 2026-09) で重みづけすると、持ち物の欄 6.5%、特性の欄 7.2%、個体 8.5%。
4. 常駐 Showdown (8100) は Reg M-C を配信している (`|formats|` に `[Gen 9 Champions] BSS Reg M-C,5e`。接続だけ、ログインなし)。
5. 無改変の Foul Play は、実行時に承認外のサイト 4 か所に取りに行く。
6. 判定: **現時点では B0 として固定できない**。

## 1. 版の固定

| 項目 | 値 |
|---|---|
| Foul Play | 8f541f80f91f960603673796559f796b10ccf0fb (2026-10-03)、タグなし、Python 3.11+ |
| Foul Play の依存 | requests 2.33.0、websockets 14.1、python-dateutil 2.8.0、poke-engine==0.0.48 (既定 feature は terastallization → bss で入れ直しが要る) |
| poke-engine | f7b147f13637ceecc1b0d1e9172ec232af4d45d0 (2026-10-11)、describe は v0.0.48-31-gf7b147f |
| 版の表記 | Cargo / pyproject とも 0.0.48。v0.0.48 の後に Champions のメガ追加 (e98de3f) と特性の修正多数 |
| features | gen1〜gen9、champions、bss=[champions]、terastallization=[gen9]、remove_low_chance_instructions。champions と gen9 は同時に指定できない |
| PyPI | 0.0.0〜0.0.48。wheel は全版なし。build_and_publish は maturin sdist のみ |

Champions 対応の実体:
- champions feature: 能力ポイントの既定 11、技の補正 34 件、まひ・こおり・ねむりの変更、特性の分岐 14 か所、遷移の分岐 27 か所。CHANGELOG の記載は「Reg M-B Champions data」。
- bss feature: 6→3 + 先頭のチームプレビュー 60 通り。
- メガ: 種 × 石の表 (HEAD 96 件 / v0.0.48 82 件)、1 チーム 1 回、行動は `<技>-mega`。
- Foul Play の GEN9CHAMPIONS: 能力ポイントの実数値計算 (max_ev 32)、champions 式の PP、再生力の交代回復なし。技の補正 47 件。
- レベル: details の `L50` から読む。

## 2. 対応範囲

範囲は 2 つ: regmc = I-01 の走査の名前、pslegal = Showdown の champions mod で isNonstandard でない名前。値はソースの静的読み取り (コンパイル結果ではない)。

| 範囲 | 種類 | n | 一致 | 不一致 | HEAD の内訳 | v0.0.48 の不一致 |
|---|---|---|---|---|---|---|
| regmc | 種 | 346 | 340 | 6 | メガの特性 4、メガの種族値 2 | 7 (名前の欠落 5、メガの表に無い 5) |
| regmc | 技 | 503 | 491 | 12 | poke-engine の威力 7・命中 4・タイプ 1、Foul Play の威力 3・PP 2 | 12 |
| regmc | 特性 | 215 | 172 | 43 | 名前の欠落 1、効果コードなし 42 | 44 |
| regmc | 持ち物 | 164 | 142 | 22 | 名前の欠落 22 (メガ石は全部ある) | 29 + 石 5 |
| pslegal | 種 | 390 | 384 | 6 | 合法なメガ 80 体は全部メガの表にある | 7 |
| pslegal | 技 | 514 | 503 | 11 | | 11 |
| pslegal | 特性 | 316 | 255 | 61 | | 63 |
| pslegal | 持ち物 | 166 | 144 | 22 | | 31 |

重大な不一致:
- メガのデータ (使用率、計 8.5%):

  | メガ | poke-engine | Showdown | 使用率 |
  |---|---|---|---|
  | golisopodmega | 特性 EMERGENCYEXIT | toughclaws | 3.4% |
  | lucariomegaz | 特性 ADAPTABILITY (AURAGUARD は名前が無い) | auraguard | 2.5% |
  | garchompmegaz | 特性 SANDFORCE | levitate | 1.5% |
  | absolmegaz | 特性 MAGICBOUNCE | sharpness | 0.7% |
  | starmiemega | 種族値 A140 | A100 | 0.3% |
  | drampamega | 種族値 D114 | D116 | 0.0% |

- 技 (技の欄の 1.9%): makeitrain 95/100 vs 120/95 (poke-engine)、tripleaxel 40 vs 20 (poke-engine)、crabhammer 95/90 vs 100/95 (poke-engine)、syrupbomb 90/85 vs 60/90 (poke-engine)、meteorassault 150 vs 170 (両方)、slash 70 vs 80 (両方)、swagger 命中 90 vs 85 (poke-engine)、acrobatics 110 vs 55 (Foul Play のデータ)、wish / strengthsap PP 10 vs 5 (Foul Play のデータ)、dragoncheer / growth (変化技なので影響は小さい)。Showdown を正解とはしていない (poke-engine の champions 補正は CHANGELOG で Reg M-B 向けとされ、M-B → M-C の変更なのか誤りなのかは未確認)。
- 持ち物の欠落 22 個 (持ち物の欄の 6.5%): lightclay 2.4%、terrainextender 1.0%、damprock 0.9%、redcard、scopelens、ejectbutton、mentalherb、leek、brightpowder、quickclaw、leppaberry、kingsrock、focusband、zoomlens、heatrock、icyrock、smoothrock、bindingband、bigroot、metronome、oranberry、persimberry。持ち物の表は u8 で 254/256。
- 特性 (特性の欄の 7.2%): 名前が無い auraguard 2.7%。対戦に効くのに効果コードが無い 31 個: stancechange 1.5%、imposter 0.6%、synchronize、electromorphosis、illusion、cursedbody、moody、stalwart、corrosion、parentalbond、angerpoint、cheekpouch、cudchew、curiousmedicine、cutecharm、earlybird、emergencyexit、forecast、harvest、klutz、merciless、mimicry、opportunist、quickdraw、ripen、rivalry、stall、steadfast、superluck、supersweetsyrup、unnerve。シングルでは効果が無いか情報だけ (11 個): anticipation、forewarn、frisk、runaway、pickup、healer、plus、minus、receiver、symbiosis、hospitality。「効果コードなし」は enum の定義以外で参照が 0 という静的な推定。
- 未知の名前は黙って既定値になる (poke-engine は NONE / UNKNOWNITEM、Foul Play は先頭一致の別種に置き換え)。E-inh-3 と同じ型の問題。

## 3. 動作確認

| 項目 | 結果 |
|---|---|
| 形式 (コード) | pass |
| 形式 (常駐サーバー) | pass (`|formats|` に `BSS Reg M-C,5e`) |
| 対戦 3 戦 | unexecuted (build 不可) |

- 実行時に取りに行く承認外のサイト: play.pokemonshowdown.com/action.php (localhost でもログインで取りに行く)、www.smogon.com/stats/<前月>/chaos/gen9championsbssregmc-0.json、data.foulplay.cc/gen9championsbssregmc/{pokemon_full_sets,replay_moves}.json、play.pokemonshowdown.com/data/sets/gen9championsbssregmc.json。
- Reg M-C のチームの見本は Foul Play に無い。
- `pip install -r foul-play/requirements.txt` は実行していない (maturin の版によっては cargo が無いと Rust を自動で取得するとされ、承認外の取得になるおそれがあるため)。

## 4. 速度

| 局面あたりの探索時間 | 結果 |
|---|---|
| 0.25 秒 | unexecuted (build 不可) |
| 1 秒 | unexecuted (build 不可) |
| 3 秒 | unexecuted (build 不可) |

- 設定の口: --search-time-ms (既定 100)、--search-parallelism、--search-threads、--team-preview-search-*。開発者の目安は約 7 秒/手、4 コア・16 GB 推奨。

## 5. 入出力
- 受け取るもの: Showdown のプロトコルと request JSON。Battle / Battler / Pokemon の状態として持つ。個体: HP、状態、ランク、揮発状態と継続ターン、技 (PP / disabled)、持ち物と特性 (それぞれ 不明 / 判明 / 除去)、タイプ、メガ、素早さの範囲。場: 天候・地形・TR と残りターン、壁。
- 相手の推定: 使用率と型の集合から候補を作り、素早さ・スカーフ・ダメージの逆算などで絞る。型・メガ・選出 3 体を尤度で抽選して複数の局面を探索する。プレビュー探索で得た相手の選出の傾向を抽選の重みに使う。
- poke-engine に渡す形: State (文字列化できる)。名前は Showdown の id の大文字。実数値・能力ポイント・タイプは呼び出し側が渡す。
- 探索の出力: MctsResult (選択肢ごとの visits と score)。最大の 75% 以上の手から比率で抽選する。
- 行動の形: `<技>`、`<技>-mega`、`switch <種>`、プレビューは `先頭,控え1,控え2`。Showdown へは /choose move … mega、/switch n、/team …|rqid。
- 再現性: rand::rng() で種を固定できない。iterations 指定はあるが CLI には無い。揺れを先に測る必要がある。
- 呼び出し口に要るもの: 観測からプロトコル相当への変換、名前の全件検査の関門、能力ポイントの単位の明示、相手の型の元データの差し替え口。

## 6. 判定
**できない (現時点)。** build 不可で、合法手・対戦の完走・速度が未確認のため。

固定に要る作業:
1. Rust の導入の承認 (rustup: static.rust-lang.org、crates.io)。HEAD を bss で build する: `pip install ../poke-engine/poke-engine-py --config-settings="build-args=--features poke-engine/bss --no-default-features"`。Docker で build する道もあるが、Docker Hub と crates.io の取得が要り、速度の条件も変わる。
2. 実行時の外部取得の扱い。localhost 用のログイン修正は修正版として固定する。型の元データは 承認 / 保存データを置く (cutoff 1500 で条件が違う) / 空 (B0 と呼べない) のどれか。
3. Reg M-C のチームを用意する。
4. 食い違いを無改変の限界として扱うか、修正版を固定するかを決める (計画書 §3.1: 候補 X の追加と同時にしない)。
5. その後に対戦 2〜3 戦・速度 3 条件・反復による揺れを測る。

## 7. 未確認
PyPI の sdist がタグ v0.0.48 と同一か、常駐 Showdown が読み込んでいるコードが d849b220 と同じか (形式一覧は一致)、tripleaxel の威力 40 が 3 回攻撃をまとめた結果なのか、M-B → M-C の数値の実機での正否。

## 1 行まとめ
用途: 操作の B0 → 対照: なし → 正しさ: 種・技の名前は全部ある。メガ 6・技 12・持ち物 22・特性 43 (対戦に効くもの 32) が食い違い、未知の名前は黙って既定値になる → 効果: 未測定 → 費用: cargo が要る、承認外のサイト 4 か所 → 採否: 保留 (固定できない) → 未確認範囲: 対戦・合法手・推定と選出の挙動・速度とメモリ・揺れ、sdist がタグと同一か、M-B → M-C の数値の実機での正否。
