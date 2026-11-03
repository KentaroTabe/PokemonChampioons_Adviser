# B0-order2: 修正版の順序 2 — 持ち物の u16 化、はどうのぼうご、壁・天候・地形の延長、レッドカード、Syrup Bomb の継続 (phaseB_20261103)

> 配置の注記 (2026-11-03、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。測定の出力 (manifest.json / measurements.jsonl 1,313 行 (pass 1305 / fail 2 / excluded 3 / unexecuted 3) / before_after.jsonl / engine_results.jsonl / cargo/ / gate_check.jsonl / fixed_bench/ / battles/ / dynamic_default_judge/) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261103/B0-order2/`**。コードは poke-engine `regmc-fix` の commit e33caa9 (u16 と 22 件) / 4b49bfe (auraguard) / f81d45d (延長) / 4ce3483 (レッドカード) / 3b4599e (Syrup Bomb) / ef7825f (py の公開) / e2dc751 (試験) / fc92db3 (試験の世代の限定) (HEAD fc92db3、push なし)、Foul Play `local-regmc` (未コミット)。差分と sha256 は `external/patches/order2.sha256` (poke-engine-regmc-fix-order2.diff 6c154544…、foul-play-state-check-order2.diff 0d7d3f0a…、foul-play-order2-incremental.diff cd28cfae…)。計算時間 約 50 分。Rust の差分約 920 行と試験約 900 行。

### A. 実装した項目
1. **持ち物の列挙を u16 に** (`#[repr(u16)]`)。Reg M-C の 22 件を追加し、効果が実装済みの一覧 `IMPLEMENTED_ITEM_EFFECTS` (260 件) と名前のみの一覧 `NAME_ONLY_ITEMS` (15 件) を置き、列挙の全件がどちらか一方に入ることを試験で確認。`test_instruction_size` は genx で 6 → 8 バイトに更新 (コミットに理由)、gen1〜3 は 6 バイトのまま。
2. **auraguard** (champions): 接触技の威力を 0.5 倍、かたやぶり等では無視 (参照の現在の値。breakable は参照側も未確定)。Lucario-Mega-Z の特性にした。
3. **延長の持ち物**: 始めた側の持ち物で持続を 5 か 8 に。壁 3 種 = lightclay、天候 = damprock (雨) / heatrock (晴れ) / smoothrock (砂) / icyrock (雪・あられ)、フィールド 4 種 = terrainextender。入口は技、特性 (登場時)、すなはき・こぼれダネ。gen 共通 (gen4・5 は特性の天候が永続 (-1) で延長しない実装どおりの挙動。試験は gen6 以降に限定し、gen4・5 は永続のままを別の試験で確認)。
4. **レッドカード**: 技の追加効果や反動の後、分岐ごとに発動を判定し、既存の吹き飛ばしを持ち主側から呼んで攻撃側を無作為に交代。発動しない: 変化技・外れ・みがわり・どちらかが倒れた・交代先なし (持ち物も消費しない)・吹き飛ばし技・ちからずく。きゅうばん・ばんけん・ねをはる: 持ち物は消費し交代しない。とんぼがえり等では攻撃側の交代の選択を強制交代に置き換える。
5. **Syrup Bomb の継続**: `VolatileStatusDurations.syrupbomb` (下げた回数)。3 回素早さを下げ 4 回目のターン終了時に解ける。交代・技を出した側のひんしでも解ける。状態の文字列は 7 欄 (6 欄の旧形式も読める、それ以外は panic)。
6. **py への公開**: `VolatileStatusDurations(syrupbomb=)`、`implemented_item_effects()`、`name_only_items()`。
7. **Foul Play**: 名前の検査を「効果が実装済み」基準に (名前のみは記録して続行、古い .so で一覧を読めなければ「効果の一覧なし」として記録。一覧は poke-engine から読む)。`fieldstart` で始めた側が terrainextender なら 8 ターン ([of] または同じターンにその技を使った側で決める)。Syrup Bomb の回数を upkeep で数えてエンジンへ渡す。

### B. 正しくなった挙動 (順序 1 → 順序 2)
| ケース | 順序 1 | 順序 2 |
|---|---|---|
| unknown_name_silent | 置換 5 件 (欠落) | 0 件 |
| auraguard の Waterfall | 65 | 33 (参照 30〜35) |
| lightclay / damprock の持続 | 5 (観測後だけ対応) | 8 |
| terrainextender の持続 | 5 (欠落) | 8 |
| 延長の将来効果 search_only (T7/T8) | 36/36 | 17/17 (参照 16〜19) |
| 延長の将来効果 observed | 観測後だけ対応 | 正確に一致 (判定の切替 `B0_OBSERVED_AS_MATCH=1` を有効にした値) |
| redcard_sturdy の強制交代 | 0 | 1.0 (参照 16/16) |
| Syrup Bomb の素早さの低下 | [0,0,0,0,0] | [1,1,1,0,0] (参照 26/26) |
区分 (全 50 行、判定の切替 2 点 `B0_UNBOOST_AUTO=1` / `B0_OBSERVED_AS_MATCH=1` を有効): **正確に一致 47 / 近似 1 / 欠落 2 (へんしん、順序 4)**。既定の判定では 46 pass / 3 fail / 1 unexecuted (dynamic_default_judge/ に保存)。対戦では自分の持ち物の UNKNOWNITEM が 0 件 (B0-battles では 95 件)。

### C. 検出して停止・記録する項目
列挙に無い名前: 判明済みなら停止、推定値なら「推定の条件変更」として記録。名前のみの 15 件 (scopelens・quickclaw・brightpowder・mentalherb ほか) は判明済み・推定値とも記録して続行。対戦での記録: 名前のみ 11 件 (brightpowder 5、quickclaw 4、mentalherb 2、すべて相手の推定値)、StateConversionError 0 件。門の確認 48/48。

### D. 関連試験
cargo test --no-fail-fast (fc92db3) 全世代で失敗 0: bss 1008 (新規 34) / gen9 998 / terastallization 992 / gen8 969 / gen7 961 / gen6 959 / gen5 954 / gen4 950 / gen3 27 / gen2 41 / gen1 44。新規の試験は各効果の分岐を適用した後の値と取り消した後の serialize の一致 (apply / reverse の往復) を確認。Foul Play: `tests.test_local_regmc_state_check` + `test_local_regmc_order2` 20/20 (既存 11 + 新規 9)、`test_fp_local_patches` 15/15。b0_tools 単体 44 OK。build bss rc 0 (17 秒)、external/.venv の .so は 6c79241d… → b3c4e936…。

### E. 性能
**固定局面** (B0-battles の実際の局面 8 個、交互に別プロセスで 1 回ずつ、threads 1):
| 条件 | 反復/秒 中央値 (前 → 後) | 前後の比 (中央値、範囲) | RSS の増分 中央値 |
|---|---|---|---|
| 30 万反復 | 463k → 494k | 1.02 (0.97〜1.06) | 71.9 → 74.9 MB (×1.047) |
| 1000 ms | 503k → 501k | 0.99 | ×1.037 |
| 3000 ms (4 局面) | 531k → 530k | 0.99 | ×1.019 (最大 RSS 413 → 422 MB) |
命令型のサイズ増 (6 → 8 バイト) はメモリ 1.3 倍にはなっていない。

**対戦** (regmc_smogon_only_2026-09、一時サーバー 8130 (停止済み)、A 側の判断全体、全体 192 秒。B0-battles は u8 の .so):
| 条件 | p50 / p95 (s) | B0-battles p50 / p95 (s) | RSS 合計の最大 (MB) | B0-battles (MB) |
|---|---|---|---|---|
| 3 戦 (1000 ms) | 1.307 / 1.394 | 1.322 / 1.458 | 480 | 447 |
| 250 ms | 0.499 / 0.568 | 0.556 / 0.708 | 331 | 298 |
| 1000 ms | 1.279 / 1.346 | 1.331 / 1.607 | 362 | 278 |
| 3000 ms | 3.384 / 3.490 | 3.400 / 3.495 | 539 | 483 |
3 戦 3/3 完走、エラー・StateConversionError・Traceback 0、時間切れ 0。swap (Mac 全体) 約 11.1 GB で変化なし。各 1 戦で相手の局面も違うため RSS の差はばらつきと区別できない。計測中は別プロジェクトの run_experiment.py (約 1 コア) が並走。

### F. 残件と次に実装する内容
名前のみの 15 件の効果 (優先: quickclaw / brightpowder / mentalherb / focusband / scopelens)、ばけのかわとレッドカードの組み合わせ、とくせいなし・さしおさえ、Foul Play 側でフィールドが予定どおり終わらなかったときの相手の terrainextender の推定、Syrup Bomb の交代場面の gold。順序 3 (Triple Axel、おやこあい、バトルスイッチ)、順序 4 (へんしん)、順序 5 (PP の表、統合)。

### G. 懸念と対処・確認・続行条件
1. 既存の試験の例の名前の差し替え (lightclay 等が列挙に入ったため `safetygoggles` / `notarealability` に。assert の変更は推定値の期待値 1 か所で入力に合わせたもの) → 運用側で承認 (入力の差し替えで、アサーションの緩和ではない)。
2. observed の判定と unboost の自動判定 → 既定では無効。採否はレビュー。
3. 状態の文字列の形式の変更 (7 欄) → 旧形式との互換は試験済み。新しい .so の 7 欄は古い .so では読めない。
4. u16 の性能 → 圧縮は不要と判断。差分 i16 が要るのは統合後の長時間動作で RSS 1.1 倍超か劣化 10% 超が出た場合。

### 未確認
相手側の UNKNOWNITEM 40 件の内訳 (推定の条件変更 0 件なので未観測のまま渡した値と推定)、Syrup Bomb の交代場面の gold、各条件 1 戦のばらつき。

### 1 行まとめ
用途: 修正版の順序 2 → 対照: Showdown d849b220 の gold (順序 1 と同じ) と B0-battles → 正しさ: 対象の照合は全部一致 (区分 47 / 1 / 2)、cargo test 全世代通過、対戦 3/3 完走、自分の持ち物の UNKNOWNITEM 0 件 → 効果: 強さは評価していない → 費用: 計算約 50 分、Rust 約 920 行と試験約 900 行、反復/秒の比 0.97〜1.06、RSS +2〜5% → 採否: 保留 (順序 3 以降へ) → 未確認範囲: 相手側の UNKNOWNITEM の内訳、Syrup Bomb の交代場面の gold、各条件 1 戦のばらつき。
