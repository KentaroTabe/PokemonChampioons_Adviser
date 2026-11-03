# B0-run: poke-engine (bss) の build と試験側の初回実行 (phaseB_20261102)

> 配置の注記 (2026-11-02、開発者判断 (b)): 本文は委託先 (サブエージェント) の完了報告の付録。測定の出力 (manifest.json / cases.jsonl / measurements.jsonl 95 行 (pass 62 / fail 17 / unexecuted 15、全体の判定は fail = 完了条件未達) / engine_results.jsonl / review_pending.json / repeat_results.jsonl / names_precheck.json / gold/ / after_cargo/) は **`../PokemonChampioons_Adviser-worktrees/external/logs/inheritance_eval/phaseB_20261102/B0-run/`** (リポジトリ外)。

- 対象: gen9championsbssregmc。正解側は Showdown d849b220 (gold は simulate-battle の CLI、31 シナリオを再生成して B0-dynamic と同一。一時サーバーは d849b220+patched、外向き 0 本の版)。試験側は poke-engine f7b147f1 を `--features poke-engine/bss --no-default-features` で build (features = bss, champions)。Foul Play 8f541f80 + local-regmc。
- 環境: cargo / rustc 1.99.1 (開発者が rustup で導入)、maturin 1.16.0、Python 3.12.15 (external/.venv)。取得したのは crates.io の 68 crate と PyPI の requests / python-dateutil だけ。外部データは取得していない。
- 実時間: 05:03〜05:17 UTC。build 32 秒、dynamic 330 秒 (gold の再生成 329 秒を含む)、repeat 16 秒、smoke 24 秒。

## 1. 段の結果
| 段 | 結果 | 内容 |
|---|---|---|
| check | pass | cargo / rustc 1.99.1、poke-engine HEAD f7b147f1 |
| build | pass | poke_engine の import 可 (32 秒) |
| dynamic | pass (段) | 46 チェックポイントを実行。区分は §2 |
| repeat | pass | 3 局面 × 5 回 |
| data | unexecuted | 外部データ 4 件が未配置 |
| smoke | pass | cutoff 1500 の代替で、ローカルログインと待受を確認 (Traceback なし)。能力の基準にはしない |
| battles / speed | unexecuted | 型集合が無い (FP_OFFLINE_DATA=1 の停止は外していない) |

## 2. 動的 25 ケースの区分 (46 チェックポイント)
**正確に一致 22 / 探索上の意図的な近似 0 / 観測後だけ対応 2 / 欠落・不具合 14 / 参照側も未確定 0 / レビュー待ち 8。** poke-engine の値は乱数 0.925 の 1 点、Showdown の値は急所を除いた乱数幅。

**正確に一致 (22)**: Salamence-Mega の aerilate (Double-Edge 149・反動 49・Body Slam 106)、Blastoise-Mega の launcher (Aura Sphere 114、Shell Smash のランク、Dark Pulse 113)、Lucario-Z への Surf 32、メガ後の Starmie の 2 ターン目 128、メガは 1 回だけ (2 体目にメガが出ない。使える局面では `-mega` が出る)、Make It Rain の特攻 -2、Crabhammer 86、Expanding Force 86 / Psychic 64、Terrain Pulse 46、Dire Claw の状態異常の分布とかるわざの先手、Steel Beam 95 と自傷 83 (Showdown は 84)、まひ 0.125・ねむり・こおり、ばけのかわ後の 2 発目 97。

**欠落・不具合 (14)**:
| ケース | poke-engine / Showdown | 原因の見立て |
|---|---|---|
| mega_golisopod_toughclaws | 86 / 102〜121 | メガの特性が emergencyexit (表) |
| mega_lucarioz_auraguard T1 (接触) | 65 / 30〜35 | auraguard が NONE に置換され効果がない |
| mega_garchompz_levitate | 70 / 無効 | 特性が sandforce (表) |
| mega_absolz_sharpness | 59 / 80〜95 | 特性が magicbounce (表) |
| mega_starmie_stats T1 | 163 / 118〜139 | 攻撃種族値 140 (Showdown は 100) |
| mega_timing_speed 行動順 | 後手 / 先手 (16/16) | メガの命令 (ChangeSpeed +22 → 189) は出るが、順番はメガ前の 167 < 169 で決まる |
| move_tripleaxel | 期待値 93.7 / 85.1 (差 10.1%) | 威力 40 の近似。閾値 10% をわずかに超える (近似とみなすかはレビュー) |
| move_makeitrain T1 / T2 | 67・34 / 78〜93・39〜46 | 威力 95 (Showdown は 120) |
| move_syrupbomb | 61 / 37〜45 | 威力 90 (Showdown は 60) |
| move_meteorassault / slash | 111・47 / 115〜136・49〜58 | 威力の表の差 |
| ext_terrainextender | 5 ターン / 8 | 持ち物が UNKNOWNITEM に置換され、Foul Play にも観測後の追跡が無い |
| unknown_name_silent | 実行時に 5 個が置換 | auraguard → NONE。lightclay / damprock / terrainextender / redcard → UNKNOWNITEM。例外は出ず、静的検査 (158 名) と一致 |

**観測後だけ対応 (2)**: lightclay の壁と damprock の雨。探索中は 5 ターン、Showdown は 8 ターン (Foul Play が観測後に補う)。

**レビュー待ち (8)** (命令列と Showdown の値を並べたもの。全文は review_pending.json): Syrup Bomb の継続 (poke-engine は T1 で ApplyVolatileStatus のみ、Showdown は T1〜T3 に毎ターン -1。2 ターン目以降は未追跡) / がんじょう (Damage 166 で 1 残る、一致に見える) / レッドカード (交代の命令なし。UNKNOWNITEM に置換) / 延長の将来効果 search_only (壁は T5 で切れ T7・T8 が 36、Showdown は 16〜19。探索だけでは延長を誤る) / 延長の将来効果 observed (残り 7 を入れると T7・T8 が 17、T9 が 36 で一致) / かわりもの (交代だけでへんしんの命令なし) / へんしん (技) (命令列が空) / ばけのかわ (FormeChange と Damage 20、一致に見える)。

## 3. 揺れ (MCTS 1 秒、threads 1、5 回)
| 局面 | 最善手 (5 回) | 最善手の visits 割合 | total_visits (平均・CV) |
|---|---|---|---|
| メガボーマンダの初手 | bodyslam-mega 5/5 | 0.773 ± 0.018 | 497,800・2.2% |
| 行動順の局面 | switch blastoise 5/5 | 0.995 ± 0.0001 | 475,200・3.1% |
| 壁の途中 (T2) | spiritbreak 5/5 | 0.568 ± 0.001 | 520,400・1.6% |
約 50 万反復/秒 (1 スレッド)。同じ局面で最善手は揺れない。

## 4. 対戦の完走・時間・メモリ
unexecuted。外部データ 4 件が data_pinned/ に無い。1 決定の p50 / p95、RSS、CPU は未測定。

## 5. 初回実行で直した箇所 (試験の道具 dynamic_cases.py だけ、6 点)
交代の選択の文字列 (`switch X` → 種族 id。直す前は 3 チェックポイントが ValueError)、状態異常の名前の対応、自傷 `Heal <side>: -N` の扱い、天候・フィールドの持続の読み方、自動判定しない 6 種を「レビュー待ち」に、unknown_name_silent の実行時の確認。poke-engine と Foul Play の側は直していない。

## 6. 残る修正の規模の見積もり
- 表データ (小): メガの特性 4、Starmie-Mega の種族値、技 5 の威力・効果。Triple Axel は表の書き換えだけでは直らない (内部表現)。
- 効果の新規実装 (中): 特性 auraguard、持ち物 4 (延長 3 は持続 5 → 8 の分岐、レッドカードは強制交代)。持ち物の列挙にも追加が要る。
- 探索の中核 (中): メガシンカしたターンにメガ後の素早さで行動順を決める。
- 大規模: へんしん / かわりもの (命令が出ず、状態表現の改修が要る見込み)。
- 確認の残り: Syrup Bomb の継続。がんじょうとばけのかわは一致に見える (レビューで確定)。延長の 2 件は観測後の値を入れれば正しく進む。
大規模な効果実装が要るのはへんしん系だけと見込む。ほかは表の修正と小〜中規模の効果追加。

## 7. 未解決
- `pin_data.py --help` が IndexError で落ちる (何も書かずに終わる。使い方の表示の不備)。
- MCTS の結果は交代を `switch X` と表すが generate_instructions は `X` しか受けない (API の不揃い。Foul Play 側の扱いは未確認)。

## 1 行まとめ
用途: B0 の操作エンジンの動的確認 → 対照: Showdown d849b220 (gold は 31/31 で再現) → 正しさ: 46 チェックポイント中、一致 22・観測後だけ対応 2・欠落 14・レビュー待ち 8。名前 5 個が黙って置換される → 効果: 対戦は未測定。MCTS は約 50 万反復/秒で揺れは小さい → 費用: build 32 秒、照合 330 秒 → 採否: 保留 → 未確認範囲: 対戦 3 戦、速度・メモリ、外部データ 4 件、レビュー待ち 8 件の区分、Syrup Bomb の継続。
