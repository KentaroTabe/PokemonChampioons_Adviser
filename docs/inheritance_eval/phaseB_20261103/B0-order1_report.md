# B0-order1: 修正版の順序 1 — 表データ、名前・状態変換の検査、メガシンカ時の行動順 (phaseB_20261103)

> 配置の注記 (2026-11-03、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録 (差し戻し 1 回の反映を含む)。測定の出力 (manifest.json / measurements.jsonl 1,099 行 (pass 1083 / fail 14 / unexecuted 1 / excluded 1) / cases.jsonl / before_after.jsonl / engine_results.jsonl / coverage_table.md・.jsonl / coverage_summary.json / gate_check.jsonl / cargo_test_*.log / gold/) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261103/B0-order1/`**。コードは poke-engine の clone のブランチ `regmc-fix` (元 f7b147f1、main は無改変。commit b26b4c4 表 / 3ee8adc 行動順 / bdd5a4a 試験 / 56d33b6 上流の期待値の更新、push なし) と Foul Play の `local-regmc` (未コミット)。差分は `external/patches/poke-engine-regmc-fix-order1.diff` (sha256 c3f910c6…) と `foul-play-state-check-order1.diff` (sha256 9861e8e9…)、一覧 `patches/order1.sha256`。計算時間 約 25 分。Rust の差分約 680 行 (試験込み)、Python 約 490 行。

## A. 実装した項目
1. **技の表** (`apply_champions_modifiers`): Make It Rain 命中 95 (威力 120)、Syrup Bomb 命中 90 (威力 60)、Crabhammer 命中 95 (威力 100) (命中が威力の欄に入っていた転記の誤り)、Meteor Assault 170、Slash 80、Dragon Hammer 100、Swagger 命中 85 (上書きで閉じた)、Growth を Grass、Freeze-Dry の secondaries を外す (champions mod の `secondary: undefined`)、Dire Claw / Crush Claw / Metal Claw に slicing、Double Shock に punch、Dragon Cheer に sound (全件照合で見つかった分を含む 13 件)。
2. **メガの表** (`base_stats.rs`): Golisopod-Mega toughclaws、Garchomp-Mega-Z levitate、Absol-Mega-Z sharpness、Starmie-Mega 攻撃 100 (:93 と mega_evolve_target の両方)、Drampa-Mega 特防 116 (両方)。Lucario-Mega-Z は auraguard の列挙が無いので ADAPTABILITY のまま (コメントで順序 2 と明記)。
3. **メガシンカ時の行動順**: `moves_first_with_speeds` を新設し、`generate_instructions_from_move_pair` で reverse の前にメガ後の素早さを求めて渡す (メガシンカがあるターンだけ)。`run_mega_evolutions` はトリックルーム下で遅い側を先にする。どちらも champions の cfg で閉じ、他の世代は元の経路のまま。
4. **名前・状態変換の検査 (Foul Play 側の共通関数)** `fp/local_regmc/state_check.py`: `checked_state_string(b, known, path, index)` で to_string → from_string を往復させ、種族・持ち物・特性・base_ability・技・揮発状態の各値を「一致 / 既定値そのもの / 未観測 / 判明済みだが未対応 / 推定の条件変更」に分ける。判明済みかどうかは推定で埋める前の Battle から得る (`known_values_from_battle`)。操作 (`fp/search/main.py`) と BSS 選出 (`bss.py`) の両経路から呼ぶ。既定: 種族・持ち物・特性・技は判明済みで未対応なら logger と `FP_STATE_CHECK_LOG` (JSONL) に記録して StateConversionError で停止 (`FP_STATE_CHECK_MODE=record` で記録して続行)、推定値なら記録して続行、**揮発状態は記録して続行が既定** (`FP_STATE_CHECK_VOLATILE_MODE=strict` で停止。実戦ログで名前の一覧ができるまで)。
5. **対応状況の表** (coverage_table.md): 技の上書き 64 件 + 持ち物 22 件を「参照仕様 / 入力で表現可能 / 探索内の効果 / 観測後の追跡 / 検証ケース / 次の変更」で整理。
6. **上流の試験の期待値の更新** (開発者承認): tests/test_battle_mechanics.rs:19121 の Absol-Mega-Z の特性 MAGICBOUNCE → SHARPNESS (M-C 前の仮の値 (Showdown b555f4d8c で修正) に基づいていたため参照に合わせた)。

## B. 正しくなった挙動 (チェックポイントの前後、B0-eval-fix → B0-order1)
- 欠落 → 正確に一致 (10 件): Golisopod-Mega (86 → 111、参照 102〜121)、Garchomp-Mega-Z の地面技 (70 → 無効)、Absol-Mega-Z X-Scissor (59 → 87、参照 80〜95)、Starmie-Mega T1 (163 → 128、参照 118〜139)、mega_timing_speed の先手 (p2 → p1)、Make It Rain T1 (67 → 86) と T2 (34 → 42)、Syrup Bomb (61 → 41、参照 37〜45)、Meteor Assault (111 → 125)、Slash (47 → 53)。
- 新規 4 件 (両者メガ・両者メガ + トリックルーム。gold は Showdown で再生成、先にメガシンカしたのは p2 16/16、先手は p1 16/16) はすべて正確に一致。
- Crabhammer は前後とも乱数幅内 (86 → 91、参照 84〜99)。値の直接照合で威力・命中を確認。
- 区分 (50 行): **正確に一致 38、近似 1、観測後だけ対応 3、欠落 7、レビュー待ち 1** (B0-eval-fix は 24 / 1 / 3 / 17 / 1)。
- 静的な照合: メガの表 96 件中 95 件一致 (差は Lucario-Mega-Z の auraguard)、`base_stats()` 105/105、Reg M-C で使える技 514 件の威力・命中・タイプ・分類・優先度の差は 8 → 2 (Dragon Cheer の威力欄は変化技で影響なし、Triple Axel は順序 3)。

## C. 検出して停止する項目 (効果の実装ではない)
持ち物 22 件 (lightclay、terrainextender、damprock、redcard、scopelens ほか)、特性 auraguard、技 nihillight (Reg M-C では使用不可)。判明済みなら StateConversionError、相手の推定値なら記録して続行。gate_check 46/46 (持ち物 22 × (判明済み = 停止 / 推定値 = 記録) + auraguard + 相手が公開した redcard)。

## D. 対応状況の表の集計 (旧チェックポイント数とは別の数え方)
- 技 64 件: 修復 13、一致 42、近似 2 (Fake Out / First Impression: 選べるが効果なし)、検出して停止 1 (nihillight)、未対応 3 (Belch、wish と strengthsap の Foul Play 側 PP)、未照合 3 (Disable、Encore、Stuff Cheeks)。
- 持ち物 22 件: すべて「検出して停止」(次の変更は順序 2)。

## E. 関連試験
- `cargo test --no-fail-fast`: bss 974 / 0 (上流の期待値の更新後)、gen9 968/0、terastallization 962/0、gen8 939/0、gen7 931/0、gen6 929/0、gen5 925/0、gen4 921/0、gen3 27/0、gen2 41/0、gen1 44/0。新規 `tests/test_champions_order1.rs` 18 件通過。修正を外すと行動順の試験 5 件が落ちる。
- build: bss で rc 0 (18 秒)。external/.venv の poke_engine は regmc-fix 版に入れ替え (.so sha256 06587dce… → 6c79241d…)。
- dynamic_cases: 単体 43 OK (+2)。Foul Play: `tests.test_local_regmc_state_check` 11 OK (既存 10 + 揮発状態は記録・持ち物は停止の 1 件)、`test_fp_local_patches` 15/15。
- measurements の fail 14 件: 動的照合 10 (順序 2〜4 の残件)、メガの表 2 行 (auraguard)、全技の数値照合 1 (Dragon Cheer・Triple Axel)、Foul Play の PP 1 (順序 5)。

## F. 性能
対戦での効果は未測定。対戦は順序 2 の後 (順序 1 の .so と名前検査では、対戦用チームの判明済みの持ち物 4 種で仕様どおり停止する)。B0-battles の計測 (無改変の .so) はそのまま有効。

## G. 残件と次に実装する内容
- 順序 2: 持ち物の u16 化と 22 件の効果、auraguard (列挙・効果。breakable は参照側も未確定)、延長 (壁・天候・地形)、レッドカード、Syrup Bomb の継続。追加の提案: Belch / Stuff Cheeks / Disable / Encore の効果の照合、技 nihillight。
- 順序 3: Triple Axel (威力欄 40 と 1 発ずつの命中)。
- 順序 4: へんしん・かわりもの。
- 順序 5: Foul Play の PP の表 (上限 20 の規則 + wish / strengthsap)、Fake Out 系の候補手の合法性、推定経路 (`poke_engine_get_damage_rolls`) への検査の追加、揮発状態の厳格さの扱い (実戦ログの名前の一覧)。

## H. 懸念と対処・確認・続行条件
1. 上流の試験の期待値 → 開発者承認で更新済み (974/0)。
2. 対戦の段が名前検査で止まる → 順序 2 の後に走らせる (決定)。
3. 揮発状態での停止 → 記録して続行を既定にし、`FP_STATE_CHECK_LOG` の集計で名前の一覧を作ってから扱いを決める。
4. 共通コードへの変更 (generate_instructions.rs の関数の分割) → cfg の外の挙動は元のまま、gen1〜9 と terastallization の試験は全件通過。

## 未確認
修正前 engine での新シナリオ (両者メガ) の照合 (Rust の試験で修正前に落ちることは確認済み)、Foul Play の pytest の試験群 (venv に pytest 無し)、揮発状態の停止の実発生、対戦・速度 (順序 2 の後)。Meteor Assault と Dragon Hammer は Reg M-C では isNonstandard Past (使用不可) だが表は直してある。

## 1 行まとめ
用途: B0 修正版の順序 1 (表・行動順・名前の検査) → 対照: Showdown d849b220 の champions mod と継承元 (gold 25 + 再生成 1、静的に 514 技・96 メガ) → 正しさ: 動的照合の一致 24 → 38、欠落 17 → 7 (残りは順序 2〜4)、名前の置換は FP 側で停止または記録 (46/46)、cargo test 全世代通過 → 効果: 対戦での効果は未測定 → 費用: 計算約 25 分、Rust 約 680 行、Python 約 490 行 → 採否: 保留 (順序 2 以降へ) → 未確認範囲: 対戦・速度、揮発状態の停止の実発生、FP の pytest 群。
