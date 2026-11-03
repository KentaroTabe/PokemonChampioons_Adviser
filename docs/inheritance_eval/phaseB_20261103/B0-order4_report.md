# B0-order4: 修正版の順序 4 — へんしん・かわりものの状態管理と復元、観測後の入力、対戦と速度の再計測 (phaseB_20261103)

> 配置の注記 (2026-11-03、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録 (Opus の利用枠で一度中断し 20:39 に再開、中断前の差分なし)。測定の出力 (manifest.json / measurements.jsonl 1,407 行 (pass 1390 / fail 11 (順序 3 の .so で照合した修正前の証拠) / unexecuted 5 / excluded 1) / before_after.jsonl / engine_results.jsonl / gold/ / cargo/ / dynamic_regression/ / dynamic_before_order3so/ / fixed_bench/ / battles/ / fp_unit_tests/ / state_size_*.txt / so_*.sha256) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261103/B0-order4/`**。コードは poke-engine `regmc-fix` の commit ff8978d (状態表現) / 22a86cf (効果) / 9bad258 (rustfmt) / f1d12c9 (試験) (HEAD f1d12c9、push なし)、Foul Play `local-regmc` (未コミット: protocol.py、state.py、poke_engine_helpers.py、tests/test_local_regmc_order4.py)。差分と sha256 は `external/patches/order4.sha256` (poke-engine-regmc-fix-order4.diff 51add1c5…、foul-play-state-check-order4.diff aa78afc3… (順序 1・2・4 の全体)、foul-play-order4-incremental.diff 3d80e923…、b0-tools-order4.diff 75447c24…)。実時間 約 44 分。Rust 本体 約 690 行と試験 673 行。.so は 6c1edce3… → 4a8abe44…。

### A. 実装した項目
1. **状態表現 (gen 共通)**: Pokemon.transform_original に元の値 (saved、写した種族 (表示用)、能力値 5 つ、重さ、技 4 つと PP。+32 バイト)。元の種族・タイプ・特性は既存の id・base_types・base_ability が持つ。揮発状態 TRANSFORM を列挙の末尾に追加 (既存のビット位置は不変)。
2. **命令**: ChangeMove (技 id の差分)、ChangeWeight (0.1 kg 単位の差分)、SaveTransformOriginal / ClearTransformOriginal (互いに逆)。PP は既存の DecrementPP に負の量。どれも 4 バイトで Instruction のサイズは不変 (genx 8、gen1〜3 6)。
3. **serialize の版 2**: Pokemon は 30 欄 (27 番目にへんしんの欄)。版 1 (29 欄) は既定値で読み、それ以外の欄数は panic。古い .so が版 2 を読むと panic することを確認。
4. **効果** (参照は transformInto): 写すもの = タイプ、特性、能力値、重さ、技 (PP 5、空きの欄は PP 0、使えない状態は解く)、ランク 7 つ、急所ランクの揮発状態 (gen6 以降)。失敗 = 相手が倒れている・相手がへんしん中、gen5 以降は相手のみがわりと自分がへんしん中 (gen4 は 2 度目を許す)。かわりものは登場時にへんしんし、写した特性の登場時の効果 (いかく等) も出す。命令として出すので枝分かれは増えない。
5. **交代での復元**: 技・PP・能力値・重さ・タイプを戻し、特性は既存の処理で戻す。へんしん中に減った PP は捨てる。元の値が無いときはタイプだけ戻す。
6. **へんしん中のメガシンカ不可** (契約どおり。参照との差は G-1)。
7. **観測後の入力 (Foul Play)**: protocol.transform で元の値と写した能力値を保存 (request の自分の stats は元の値のまま)。poke_engine_helpers が transform_saved / original_* と写した相手の重さを渡す。switch_or_drag で保存した値に戻す (従来は技を []、能力値を既定の配分で計算し直していた。保存が無い局面は従来どおり)。
8. **照合の道具**: へんしん中の局面の組み立て、-transform でのランクの写し、restore の値と相手の能力値・タイプ・ランク、失敗するシナリオの判定。ケースの追加は `B0_ORDER4_CASES=1` のときだけ。

### B. 正しくなった挙動
| ケース | 順序 3 | 順序 4 | Showdown |
|---|---|---|---|
| 技 transform・かわりもの (B0-order1 の gold、回帰) | 欠落 | 一致 | — |
| 写した技・特性・能力値・PP・ランク・タイプ (新 gold 6 行) | 欠落 | 一致 | 一致 |
| へんしん後のダメージ (Tackle +2: 47、EQ: 98) | 例外 | 一致 | 43〜51 / 90〜106 |
| 交代で戻る (技・PP 11/19・能力値・特性・タイプ) | 例外 | 一致 | 一致 |
| かわりもので写したいかく (相手の攻撃 -1) | 欠落 | 一致 | 一致 |
| みがわり越しの失敗 | 一致 (効果が無いため) | 一致 | 失敗 |
| へんしん中のメガシンカ | — | 候補なし (契約どおり) | 可能 (レビュー待ち) |
順序 4 の範囲 (12 行): 一致 11・レビュー待ち 1。回帰: 一致 49・近似 1・未実施 3 (順序 3 は一致 47・欠落 2)。同じ照合を順序 3 の .so で回すと欠落 11・一致 1。gold は Showdown で 176 試行 (seed 起点 7000)、エラー・時間切れ 0。

### C. 検出して停止・記録する項目
serialize の欄数の不一致は panic。元の値の無いへんしんは Foul Play で警告を出し元の値を渡さず、poke-engine 側はタイプだけ戻し技や能力値を黙って 0 や NONE にしない。名前の検査の記録で残ったのは「名前のみ」の持ち物だけ。

### D. 関連試験
cargo test (f1d12c9) 全世代で失敗 0: bss 1057 (新規 17) / gen9 1047 / terastallization 1041 / gen8 1018 / gen7 1008 / gen6 1006 / gen5 970 / gen4 964 / gen3 27 / gen2 41 / gen1 44。新規はコピー範囲とコピーしないもの、空きの欄と使えない技、急所ランク、失敗 3 種と gen4 の 2 度目、かわりものと写したいかく、交代での復元、観測後の入力の形からの復元、メガ不可、版 2 と版 1 の読込、panic、重さの往復、全分岐の apply / reverse。Python: test_b0_dynamic 57/57 (既存 50 の期待値不変)、Foul Play `test_local_regmc_{state_check,order2,order4}` 26/26、対戦の単体 31/31・15/15。

### E. 性能
- 固定局面 (順序 4 / 順序 3): B0-battles の 8 局面は中央値 0.998 / 0.998 / 0.985 (30 万反復 / 1000 ms / 3000 ms)、RSS の増分の比 0.98〜1.01 (State +6.3%、6128 → 6512 B の影響は見えない)。state_00 (控えにかわりもの) だけ 0.73〜0.75、へんしん前の 2 局面は 0.66〜0.82 (generate_instructions 1 回は transform が splash より 1.3 µs 多いだけで、探索中にへんしんして木の形が変わる (技 1 → 4) ためと推定、未確認)。Python からの変換は 29 → 37 µs (1 判断に 1 回)。へんしん中の局面は 40 万反復/秒。
- 対戦 (regmc_smogon_only_2026-09、一時サーバー 8130、3 戦 3/3 完走、エラー・StateConversionError・時間切れ・自分の UNKNOWNITEM 0。かわりもののへんしんが 2 回、探索に渡したへんしん中の状態 16 件すべてに元の値):

| 条件 | 判断全体 p50 / p95 (秒) | 順序 2 | RSS の最大 (群 / 単体、MB) |
|---|---|---|---|
| 1000 ms | 1.27 / 1.35 | 1.31 / 1.39 | 529 / 198 (順序 2: 480 / 188) |
| 250 ms | 0.47 / 0.51 | 0.50 / 0.57 | — |
| 3000 ms | 3.39 / 3.47 | 3.38 / 3.49 | 596 / 493 (順序 2: 539 / 437) |
CPU 約 200%、swap 10.6 GB で一定。並走: 別プロジェクトの run_experiment.py (約 1 コア) と BTLEServer。

### F. 残件と次 (順序 5)
Foul Play の操作・BSS 選出との統合。相手のへんしん前の技が未観測なら元の技は NONE (推定の set で埋める経路)、相手の元の能力値は推定値のまま、写した技の PP は一律 5 (Showdown は min(5, 表の PP))、回避ランクのある相手では外れの分岐でもへんしんが入る (Showdown は必中)、技でへんしんしたときの特性の発動は登場時の処理全体を使う近似、こだわり・アンコールとへんしんの組み合わせ、試合開始時の先発のかわりもの、Side の揮発状態の読込で NONE が足される既存の不具合、上流の pytest、へんしん中のメガシンカ (G-1)。

### G. 懸念と対処・確認・続行条件
1. **へんしん中のメガシンカ**: 契約「できない」に対し、Showdown d849b220 は元の種族の石があればへんしん中でもメガシンカできる (Kangaskhan-Mega になった)。運用側の判断: 参照に合わせる方針だが、メタモンが他種のメガ石を持つ組み合わせの実機の合法性が未確認で稀なので、順序 5 の残件に低優先で回す (照合の行はレビュー待ちのまま)。
2. 判定の切替 `B0_ORDER4_CASES` は既定で無効 → 既定で有効にするなら B0-prep の一覧の更新を承認する (レビュー)。
3. メタモンを含む局面の反復/秒 0.66〜0.82 と対戦の RSS 約 1 割増 → 見直しの条件は統合後の対戦でかわりもの持ちの判断全体の p95 が 10% 超悪化。今回の p95 は順序 2 並み。

### 1 行まとめ
用途: 修正版の順序 4 → 対照: Showdown d849b220 (seed 7000 の新しい gold 176 試行) と順序 3 → 正しさ: へんしん 2 行が「欠落」から「正確に一致」、新規 10 行は一致 9・レビュー待ち 1、回帰で他の区分は不変、cargo test 全世代で失敗 0、観測後の入力も実対戦で元の値が入ることを確認 → 効果: 強さは評価していない → 費用: 実時間約 44 分、Rust 本体約 690 行と試験 673 行、State +6.3%、メタモン局面で反復/秒 0.66〜0.82 倍・他は 1.00 倍、対戦の p50/p95 は順序 2 並み → 採否: 保留 (へんしん中のメガシンカと判定の切替はレビュー待ち) → 未確認範囲: 相手の未観測の元の技、PP の表、上流の pytest、RSS の増分の原因。
