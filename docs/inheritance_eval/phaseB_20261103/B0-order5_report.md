# B0-order5: 修正版の順序 5 — Foul Play の操作・BSS 選出・推定経路の統合、残件の処理、実機性能、固定の準備 (phaseB_20261103)

> 配置の注記 (2026-11-04、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。測定の出力 (manifest.json / measurements.jsonl 136 行 (全件 pass、うち perf_run1_nocache は 1 回目の記録) / perf/ / perf_run1_nocache/ / cargo/ / fp_unit_tests/ / dynamic_*/ / volatile_candidates.jsonl / volatile_allowlist_proposal.txt ほか) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261103/B0-order5/`**。コードは poke-engine `regmc-fix` の commit fe2bd22 (運用側で委託先の差分をコミット、push なし)、Foul Play `local-regmc` (順序 1〜5 の全体が未コミット: 変更 fp/battle/{inference,protocol,state}.py、fp/search/{poke_engine_helpers,standard_battles}.py、fp/data/mods/gen9champions_move_mods.json、fp/local_regmc/state_check.py、新規 fp/local_regmc/{formes,transform_infer}.py、tests/test_local_regmc_order5.py)。b0_tools に freeze_b0.py / run_b0_order5_perf.sh / build_order5_outputs.py / order5_* (10 本) を追加、run_fp_decision_timed.py を修正 (|error| と最終ターンの記録、"|tie" が "|tier|" を拾う不具合)。固定の準備は `external/b0_fixed/` (conditions.json daf630d4… / known_approximations.json 02831d6e… / manifest.json 83f1e103… (dry-run))。差分と sha256 は `external/patches/order5.sha256` (poke-engine-regmc-fix-order5.diff fea3482a…、foul-play-state-check-order5.diff 357f6c3c… (順序 1〜5 の全体)、foul-play-order5-incremental.diff 01b588ca…、b0-tools-order5.diff 3bea54e8…)。.so は 4a8abe44… → d606853a…。実時間 約 48 分 (性能測定 655 + 661 秒)。運用側の確認: Foul Play の試験 44/44 と poke-engine の順序 5 の試験 5/5 を再実行して通過、一時サーバー 8130 の停止を確認。

### A. 統合した項目
1. **入口の契約**: 操作 (search)・BSS 選出 (bss_team_preview)・推定 (`poke_engine_get_damage_rolls`: 逆算のダメージと protocol.immune) の 3 経路を `checked_poke_engine_state` に集めた。照合に使う一覧 (効果実装済み・名前のみ) は poke-engine から読む。推定経路の known は `inference._do_check` が候補で埋める前の battle から作る。推定経路では、判明済みの未対応名で止め、止めない記録 (名前のみ・揮発状態・推定の条件変更) は同じ組につき 1 回だけ残す。同じ名前の組なら往復を省く (停止の判定の組はキャッシュしない)。同じ局面なら 3 経路で区分の件数が一致することと、検査の外から `battle_to_poke_engine_state(` を呼ぶ箇所が無いことを試験で確認。
2. **元の技の照合**: へんしん中の元の技 (`orig_move0`〜`orig_move3`) も往復で照合する。観測済みの名前は「判明済み」、推定で埋めた名前は「推定値」。
3. **許可リスト**: `FP_STATE_CHECK_VOLATILE_ALLOWLIST` で読み込む。指定したファイルが読めなければ止める (黙って空にしない)。載っている名前は strict でも止めない。既定は指定なし (許可しない)。

### B. 正しくなった挙動
| 項目 | 前 | 後 | 参照 |
|---|---|---|---|
| wish / strengthsap の PP (最大 PP) | 10 (12) | 5 (8) | champions moves.ts、calculatePP |
| 写した技の PP: revivalblessing | 5 | 1 | transformInto Math.min(5, pp) |
| champions の Fake Out / First Impression (行動した後) | 候補手に残る (効果なし) | 候補手に出さない | onDisableMove |
| Side の揮発状態の往復 | NONE が増える | 増えない | — |
| 自分のブレードフォルム (攻撃/防御/特攻/特防) | シールドの値 112/160/63/160 | 211/70/144/70 | gold の storedStats |
| 交代したブレードフォルム | 控えの名前・種族値がブレードのまま | シールドに戻る | formeChange は一時的 |
| 相手のかわりものの元の技 | NONE | 推定の set で埋める (実戦で 31 件) | — |
| 推定経路の名前の検査 | なし | 判明済みは停止、推定値は記録 | — |
PP の表: 順序 1 で差 36 件としたうち 34 件は、実行時の「20 を超える PP は 20」ですでに一致していた (順序 1 の照合はこの規則を当てていなかった)。514 技の実行時照合で PP・最大 PP・写した PP の差は 4 → 0。

### C. 検出して停止・記録する項目
推定経路でも判明済みの未対応名は StateConversionError で止める。へんしん中の元の技は、観測済みの未対応名は停止、推定の未対応名は推定の条件変更として記録。推定で埋めた元の技は「へんしんの元の技 (推定)」として記録。許可リストのファイルが読めない場合は止める。実戦 (2 回目) の記録: 名前のみの持ち物 88、推定の条件変更 8 (候補の持ち物 "nothing" など)、へんしんの元の技 (推定) 31、揮発状態 0、StateConversionError 0。

### D. 関連試験
cargo test (順序 5 の差分) 全世代で失敗 0: bss 1062 / gen9 1052 / terastallization 1046 / gen8 1023 / gen7 1013 / gen6 1011 / gen5 975 / gen4 969 / gen3 27 / gen2 41 / gen1 44。新規 5 件のうち修正に関わる 3 件は修正前 (f1d12c9) で落ちることを確認。Foul Play: `test_local_regmc_{state_check,order2,order4,order5}` 44/44 (新規 18)、`test_fp_local_patches` 15/15、`test_fp_declared_missing` 31/31。動的照合は前の順序から後退 0 (回帰: 一致 49・近似 1・未実施 3、順序 3 の照合: 一致 17・近似 1、順序 4 の照合: 一致 11・レビュー待ち 1)。

### E. 性能 (2 回目を本結果。A = regmc_speed、B は 250 ms 固定。一時サーバー 8130、データ構成 regmc_smogon_only_2026-09、Foul Play 同士。全 23 戦完走、不正な選択・時間切れ・例外・外部取得 0)
| 条件 | 戦数 | 判断全体 p50 / p95 (秒) | 選出 p50 | A の RSS 最大 (MB) | A+B の RSS 最大 | A の %CPU p50 | load |
|---|---|---|---|---|---|---|---|
| 250 ms | 2 | 0.49 / 0.60 | 0.90 | 160 | 328 | 10 | 4.5 |
| 1000 ms | 2 | 1.26 / 1.35 | 2.48 | 252 | 354 | 88 | 4.5〜5.3 |
| 3000 ms | 2 | 3.40 / 3.54 | 6.74 | 529 | 585 | 99 | 4.5〜5.1 |
| parallelism 2 (1000 ms) | 2 | 1.31 / 1.42 | 2.55 | 402 | 469 | 176 | 5.1〜5.6 |
| parallelism 4 (1000 ms) | 2 | 1.37 / 1.47 | 2.70 | 693 | 722 | 340 | 5.6〜6.8 |
| threads 2 (1000 ms) | 2 | 1.32 / 1.49 | 2.52 | 291 | 363 | 119 | 6.4〜8.2 |
| 連続 10 戦 (1000 ms) | 10 | 1.28 / 1.38 | 2.38 | 275 | 327 | 88 | 6.3〜8.2 |
- 連続 10 戦の推移: 判断全体 p50 は前半 1.277 / 後半 1.284 (傾き +0.0009 秒/戦)、RSS 最大は 1 戦ずつ 213〜275 MB で傾き −0.75 MB/戦 (劣化なし。判定の目安は委託先が置いた: 後半の p50 ≤ 前半の 1.1 倍、RSS の傾き < 5 MB/戦)。
- 1000 ms で 1 回の探索の反復数 p50 は 27.5 万、1 ターンの残り時間の最小は 55 秒。swap は計測前から約 11.7 / 12.3 GB で一定。並走: 別プロジェクトの run_experiment.py (約 1 コア)、BTLEServer (約 1 コア)、daily_scout ほか。
- 1 回目 (推定経路の検査の省略なし) は 1000 ms で p50 / p95 1.35 / 1.60 (判断全体と探索計算の差の p95 0.23 秒、2 回目は 0.07 秒)。省略で検査は 1 回あたり 391 → 109 µs。
- 各条件 2 戦なのでばらつきの区間は無い。強さは評価していない。

### F. 残件
`b0_fixed/known_approximations.json` (項目 32: 近似 13・未対応 12・レビュー待ち 3 ほか、順序 5 で直した 7 件は別枠)。主なもの: ひるんだ後の Fake Out は選べる (poke-engine は last_used_move を None に戻す)、ブレードフォルムの扱いは Aegislash だけ、上流の `forme_change` が stats に hp の欄を残す (探索には渡らない)、上流の pytest は venv に pytest が無く未実行、dragoncheer / typeadd の効果、Population Bomb、へんしん中のメガシンカ (順序 4 G-1)。揮発状態の候補 83 件のうち poke-engine が往復で落とすのは 12 件 (実戦で出たのは 0)。

### G. 固定の準備
`b0_tools/freeze_b0.py --dry-run` が `b0_fixed/manifest.json` に書く: poke-engine (HEAD、差分と patch の一致、main の f7b147f が不変、build の feature、.so の sha256)、Foul Play (HEAD、patches 3 つの sha256、HEAD + patches の木と作業ツリー・対戦用の写しの一致 101acd59…)、データ構成 (manifest と計算し直した sha256)、サーバー (d849b220+patched、clone の差分、外向き 0 本の確認 ad7b6d25…)、規制 (rule table を解決: 種 390・技 514・特性 316・持ち物 166)、設定の案 (1000 ms・parallelism 1・threads 1)、対応範囲 (判定の切替を有効にした値と既定の値の両方)、残る近似・試験・環境。`--dry-run` なしの実行は拒否して止まる (終了コード 2)。固定を妨げる点 7 件: poke-engine の未コミット (運用側で fe2bd22 にコミットして解消)、Foul Play の未コミット (HEAD 8f541f8 + patches で固定する案)、レビュー待ちの照合 2 件 (syrupbomb / へんしん中のメガシンカ)、残件のレビュー待ち 3 件 (transform_mega / volatile_allowlist_undecided / judge_switches)、conditions.json が draft。

### H. 懸念と対処・確認・続行条件
1. 推定経路の検査が判断全体を遅らせていた → 同じ名前の組の往復を省略 (指示書の外、性能の調整)。2 回目の p95 は順序 4 並み (1000 ms で 1.35 秒、順序 4 は 1.32 秒)。
2. parallelism 4 では A の RSS が 693 MB、%CPU 340。判断全体 1.37 秒で parallelism 1 との差は小さい。採るかどうかは強さの評価の後。
3. 続行条件: 開発者がコミット・許可リスト・設定・レビュー待ちを決め、conditions.json を fixed にしてから freeze を実行する。

### 1 行まとめ
用途: 修正版の順序 5 (統合・性能・固定の準備) → 対照: Showdown d849b220 (PP・規制・gold)、順序 2〜4 の照合と対戦 → 正しさ: 3 経路で検査が同じ、PP の差 0、照合の後退 0、cargo test 全世代で失敗 0、Foul Play の試験 44/44、46 戦完走で不正な選択・時間切れ 0 → 効果: 強さは評価していない → 費用: 実時間約 48 分、1000 ms で判断全体 p50 / p95 1.26 / 1.35 秒、RSS 最大 252 MB (parallelism 4 で 693 MB)、10 戦で劣化なし → 採否: 保留 (固定を妨げる点、開発者判断) → 未確認範囲: ばらつきの区間、上流の pytest、許可リストの採否、へんしん中のメガシンカ。
