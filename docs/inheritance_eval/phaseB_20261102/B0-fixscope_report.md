# B0-fixscope: 修正版の範囲と費用の調査 (phaseB_20261102、コードは直さない)

> 配置の注記 (2026-11-02、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。manifest.json は `../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261102/B0-fixscope/`。ソースの静的な読み取りと Showdown の git 履歴の読み取りだけで、build・実行・外部アクセスはしていない。工数は変更する行の目安で、どれも静的な見積もり (未検証)。

- 対象: poke-engine f7b147f1 (features = bss → champions)、Foul Play 8f541f80、正解側は Showdown d849b220 の `data/mods/champions/` (M-C) とそれが継承する base。B0-check の照合表を再利用し、Showdown の mod の技の上書き 255 件 (うち数値・効果の上書き 64 件) と poke-engine の modifiers を突き合わせた。所要 約 40 分。

## 要点
- **技の表の誤り 3 件 (Make It Rain / Syrup Bomb / Crabhammer) は M-B と M-C の差ではなく転記の誤り**: `apply_champions_modifiers` (src/choices.rs:20703-20774) が命中 (95 / 90 / 95) を威力の欄に書いている。3 行の書き換え。
- **メガの特性 4 と Starmie-Mega の攻撃 140 は Showdown の後の修正 (7340ea497 9/1、812501ede 9/9 M-C、b555f4d8c) の取り込み漏れ**: src/genx/base_stats.rs の 1〜2 行ずつ。Champions 専用の種なので gen9 に影響しない。
- **メガシンカ時の行動順**: `run_mega_evolutions` → `modify_choice_priority` → `state.reverse_instructions` (メガの変化を戻す) → `moves_first` の順で、`moves_first` がメガ前の素早さを読む (generate_instructions.rs:4241-4250、`get_effective_speed` :2535)。Showdown は gen7 以降メガ後の素早さ。修正案 (a) 推す: reverse の前に両者の実効の素早さを求めて `moves_first` に渡す。10〜25 行、champions の cfg で閉じる。テスト 2〜3 本。
- **持ち物の列挙は `#[repr(u8)]` で 254/256 が使用済み**: 延長 3 + レッドカードで溢れる。regmc で黙って UNKNOWNITEM になる持ち物は 22 個 (lightclay, terrainextender, damprock, redcard, scopelens, ejectbutton, mentalherb, leek, brightpowder, quickclaw, leppaberry, kingsrock, heatrock, icyrock, smoothrock, focusband, zoomlens, metronome, bigroot, bindingband, oranberry, persimberry)。対応案: (i) u16 化 (Instruction が 6 → 8 バイト、サイズのテストが落ち探索木のメモリ約 1.3 倍)、(ii) 推す: u16 + 命令を差分 i16 の形に (6 バイトのまま。genx 32 か所 + gen2/gen3 に波及、80〜150 行)、(iii) 変種を潰して空きを作る (他 gen を壊すので推さない)。
- **へんしん / かわりもの**: TRANSFORM (choices.rs:18003) と IMPOSTER (abilities.rs:275) は名前だけで効果の分岐なし、揮発状態の列挙に TRANSFORM なし。Foul Play は観測後に能力値・技 (PP 5)・タイプ・ランク・特性を写す (protocol.py:2221-2257、交代時 :196-207 で戻す) ので、観測後の局面の評価はおおむね表現できる。探索中に実装するなら、技を入れ替える命令、交代で戻すための保存 (元の技の保存欄が要る)、揮発状態 TRANSFORM、登場時と技の効果、制約の処理で 300〜600 行 + テスト約 10 本 (仮説)。元の技の保存欄を Pokemon に持たせると全 gen の serialize と状態のサイズが変わり膨らむ。
- **ほかに大きくなりうる箇所**: バトルスイッチ (Aegislash、参照 0、中 60〜120 行)、おやこあい (Kangaskhan-Mega、参照 0)、イリュージョン (探索では表現できず FP の推定側の課題)、Syrup Bomb の継続 (持続の欄なし、小〜中)、Triple Axel の 1 発ずつの命中 (80〜150 行。Population Bomb も同じ近似)。延長の持ち物の持続は残りターンが state に既にあり、始めるときの 5/8 の選択だけ (小)。

## 1. 表データ (項目ごと)
| 対象 | 場所 | 正解 / 現状 | 種類 | 工数 | 検証ケース | 懸念 |
|---|---|---|---|---|---|---|
| Golisopod-Mega の特性 | base_stats.rs:635 | toughclaws / EMERGENCYEXIT | 表 | 1 行 | mega_golisopod_toughclaws | なし |
| Lucario-Mega-Z の特性 | base_stats.rs:665 | auraguard / ADAPTABILITY | 表 + 新しい効果 (§3) | 1 行 | mega_lucarioz_auraguard | 列挙の追加が先 |
| Garchomp-Mega-Z の特性 | base_stats.rs:659 | levitate / SANDFORCE | 表 | 1 行 | mega_garchompz_levitate | なし |
| Absol-Mega-Z の特性 | base_stats.rs:647 | sharpness / MAGICBOUNCE | 表 | 1 行 | mega_absolz_sharpness | slicing flag が欠けた技がある |
| Starmie-Mega の種族値 | base_stats.rs:93 (使われる側) と :504 (使われない写し) | 攻撃 100 / 140 | 表 | 2 行 | mega_starmie_stats | :93 を直さないと効かない |
| Drampa-Mega の特防 (範囲外) | base_stats.rs:100, :546 | 116 / 114 | 表 | 2 行 | 新設 | なし |
| Make It Rain | choices.rs:20737 | 威力 120・命中 95 / 威力 95・命中 100 | 表 (欄の誤り) | 1 行 | move_makeitrain | なし |
| Syrup Bomb | choices.rs:20760 | 威力 60・命中 90 / 威力 90・命中 85 | 表 (同上) | 1 行 | move_syrupbomb | 継続効果は別件 |
| Crabhammer | choices.rs:20719 | 威力 100・命中 95 / 威力 95・命中 90 | 表 (同上) | 1 行 | 確定数の境界のケース | B0-run で一致に見えた理由は未確認 (乱数 0.925 の 1 点では差が出なかった可能性) |
| Meteor Assault / Slash | modifiers に追加 | 170 / 150、80 / 70 | 表 (M-C) | 各 1 行 | 該当ケース | なし |
| Swagger | modifiers に追加 | 命中 85 / 90 | 表 | 1 行 | 命中率のケース | 共通の表を直すと gen7〜9 も変わるので上書きで閉じる |
| Growth のタイプ | modifiers に追加 | Grass / NORMAL | 表 | 1 行 | 不要の見込み | なし |
| Triple Axel | choices.rs:20597、generate_instructions.rs:2408-2412 | 20/40/60 で 1 発ずつ命中判定 / 威力 40 × 3 発で判定 1 回 | 分岐 (多段攻撃) | 近似なら 1 行 (威力約 35)、正しく直すなら 80〜150 行 | 期待値と命中回数の分布 | 威力 20 に書き換えると逆方向に外れる (0.9 × 60 = 54)。近似では「当たれば必ず 3 発」の誤りが残る |

範囲外で見つけた技の差 (regmc で使える): Freeze-Dry に champions では無い 10% こおりが残る、Dire Claw の slicing flag と Double Shock の punch flag が無い、Syrup Bomb の継続効果が無い。数値以外の上書き 64 件 (Encore / Disable / onDisableMove 系 / Milk Drink の target 等) は未照合で、全件照合を表の修正版に入れることを推す。

## 2. 既知の名前の黙った置換
- 経路: lib.rs:40-90 の `define_enum_with_from_str!` が知らない名前を `Ok(default)` で返す (Items = UNKNOWNITEM、Abilities = NONE、揮発状態 = NONE、PokemonName = NONE、Choices = NONE)。poke-engine-py の `Into<Pokemon>` (lib.rs:663-700) は `from_str(...).unwrap()` で失敗しないため検出されない。Foul Play は main.py:74 で `battle_to_poke_engine_state(b).to_string()` を呼び、その時点で置き換わっている。
- (a) 効果の実装: auraguard (abilities.rs の列挙・破壊可能の一覧・被弾側の補正、8〜12 行。poke-engine は補正を威力に掛け Showdown は最終ダメージに掛けるので丸めで 1 ずれうる。breakable は参照側も未確定)、lightclay (`generate_instructions_from_duration_side_conditions` :603-631 の 1 か所、5〜10 行)、damprock 等の岩 (天候を始める 10 か所、`weather_duration` を新設、20〜40 行)、terrainextender (フィールドを始める 9 か所、15〜30 行。Foul Play 側も観測後 5 ターン固定 (protocol.py:1433) なので 5〜10 行)、redcard (被弾後の処理、`get_instructions_from_drag` を攻撃側に向けて流用、40〜80 行 + テスト 4。交代技や多段技との順番に注意)。
- (b) 検出して記録・停止する門: **Foul Play 側 (推す)**: `battle_to_poke_engine_state` の直後 (main.py:74、bss.py:96) で `to_string()` → `State.from_string()` に往復させ、各ポケモンの id・item・ability・技・揮発状態を入力と照らし、既定値へ変わっていたら記録して例外で止める。Python 40〜60 行、再 build 不要。短所: 助言側から poke-engine を直接呼ぶ経路は守れない。poke-engine-py 側 (TryFrom / validate_state) は Rust 60〜100 行で再 build と API 変更が要る。from_str 自体を変えるのは io.rs やテストが default に依存するので推さない。

## 3. Showdown の champions mod との突き合わせ
mod の中身: abilities 12、conditions (まひ 1/8、ねむり 2〜3、こおり 3)、moves 255 (数値・効果の上書き 64)、items、scripts (getActionSpeed、メガの formeChange、clearVolatile、PP)。mod に無いものは base を継承 (auraguard、延長の持ち物、redcard、Swagger 85、メガの種族値・特性)。M-B の正解は championsregmb (`inherit: 'champions'`、差分は持ち物の解禁・習得技・PP 2 件)。出典として使える条件: 継承を解いた値で比べる、数値以外の上書きも照合する、Showdown 自身の TODO や仮の値 (auraguard の breakable、Z メガの特性は 9/1 まで仮) は「参照側も未確定」に分ける。修正版の基準は「d849b220 の値に合わせる」で一意。

## 4. 修正版の候補の範囲 (仮説)
| 範囲 | 中身 | 変更行の目安 | 期間の目安 | 直る B0-run の項目 |
|---|---|---|---|---|
| 小 | §1 の表 + 行動順 + FP 側の門 | Rust 30〜60 行 + Python 50 行 | 1 日以内 | 欠落 14 のうち約 10 (残りは黙らずに止まる) |
| 中 | 小 + auraguard + 持ち物の列挙 (ii) + 延長 4 種 (FP 側を含む) + レッドカード + Syrup Bomb | +200〜350 行 | 2〜4 日 | 欠落 13 + レビュー待ちの 4 件 |
| 大 | 中 + へんしん/かわりもの + バトルスイッチ + おやこあい + Triple Axel の 1 発ずつの命中 | +500〜900 行 | 1〜2 週 | 残り全部 |

B0 候補を見直すべき条件: 持ち物の命令のサイズを保てない ((ii) が 150 行超か性能の劣化 10% 超)、へんしんの元の技の保存欄を Pokemon に持たせる必要が出る、上書き 64 件の全件照合で効果の新規実装が 5 件を超える、gen9 共通のコードへの独自の差分を上流に戻せずに抱え続ける (小は上書きと cfg でほぼ閉じるが中以上は共通コードに入る)。

## 5. 開発者判断が要る設計の選択
持ち物の列挙を (i) u16 だけ / (ii) u16 + 差分 i16 (推す) / (iii)。門を Foul Play 側 (推す) か poke-engine-py 側か。Triple Axel を近似 (威力約 35) で済ませるか命中ごとに枝分かれさせるか。

## 未確認
build と照合、上書き 64 件の大半、Crabhammer の一致の理由、auraguard の breakable、へんしんの工数、u16 化の性能への影響、両者がメガシンカするときの順番 (Showdown と同じと見込むが未確認)。

## 1 行まとめ
用途: B0 修正版の範囲の見積もり → 対照: Showdown d849b220 の champions mod と base (git 履歴で変更時期も確認) → 正しさ: 技 3 件は転記の誤り、メガ 5 件は取り込み漏れ、行動順は reverse の後に判定、持ち物の列挙は 254/256 → 効果: 小の範囲で欠落 14 のうち約 10 が直る見込み (未検証) → 費用: 小 1 日、中 2〜4 日、大 1〜2 週 (仮説) → 採否: 保留 → 未確認範囲: build と照合、上書き 64 件の大半、Crabhammer の一致の理由、auraguard の breakable、へんしんの工数、u16 化の性能。
