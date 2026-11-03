# 登場時効果の共通化: 設計 (transition.py の拡張、2026-10-23)

計画: [docs/ADVICE_ACCURACY_PLAN_1009.md](ADVICE_ACCURACY_PLAN_1009.md) §8.36 (10/19 の 7 提案への回答で「登場時効果の全面共通化は別単位」)・§8.49 (設計文書は ② → ③ の順)。
背景: 10/19 の不具合 (3 体組の評価はメガ割当を反映した対面、初手の評価は素の姿) は 38d6eec9 で局所修正済み。
この文書は設計だけ。コードは変えていない。方針はレビューと開発者が決める。

## 0. 要点

- 今、**登場・交代・死に出し・メガを状態遷移として扱っているのは対戦側の `advisor/transition.py` だけ**。選出側 (`advisor/selection.py`) は
  transition を使わず、`advisor/endgame.duel_score` の 1v1 の撃ち合い (場の効果なし) と、メガ後の姿を自前で作る経路を持つ。
- transition の登場処理 (`apply_switch`) が解決するのは **設置技 (ステルスロック・まきびし・ねばねばネット) と揮発状態の初期化だけ**。
  登場時特性 (いかく・天候・フィールド)・メガ後の特性の開始効果 (メガリザードン Y の ひでり 等)・登場時の持ち物・かわりもの は、どの評価経路でも未対応。
- 提案は 2 段に分ける。
  - **段 E0 (この設計の本体、挙動不変)**: transition.py の中で出来事を関数として分け (登場 / 退場 / 通常交代 / 死に出し / 初手 / メガ)、
    1 ターンの前半の処理順を 1 か所に明示する。選出側の初手・メガ後の姿は transition の関数を参照する。数値・順位は変えない。
  - **段 E1 (別の大きさ、挙動が変わる)**: 登場時特性・天候・設置技以外の着地効果・持ち物を、E0 で作った登場の入口に効果ごとに足す。効果ごとに設定で切り替え、別の実験で採否を決める。
- 選出側に別の遷移エンジンは作らない。登場とメガシンカは 1 つの出来事にしない (今の `apply_switch` と `mega_evolve` の分け方を保つ)。

## 1. 現状

### 1.1 対戦側 (`advisor/transition.py`)

| 出来事 | どこで | 何をするか | Showdown との対応 (pokemon-showdown d849b2200) |
|---|---|---|---|
| 通常交代 | `resolve_turn` / `resolve_turn_branches` の冒頭で `apply_switch` (自分 → 相手の固定順) | 控えと場を入れ替え、`hazard_frac` (ステルスロック + 接地ならまきびし) を引く。ねばねばネットは接地した個体の素早さ −1。`mega_forms` / `volatiles` / `last_move` / `sub_hp` を空にする | 交代は `switch` (順位 103)。登場時の事象 (`runSwitch` → `fieldEvent('SwitchIn')`) は素早さ順 (sim/battle-actions.ts `runSwitch`) |
| 退場 | `apply_switch` の中 (場の個体を `(side.active, side.active_hp)` のまま控えに戻す) | **能力ランクは控えの MonView に残る** (下の 1.4) | `switchIn` で引っ込む個体は `clearVolatile` (ランクも消える) |
| 強制交代 | `drag_in` (ふきとばし・ほえる、M2e) | 引っ込む個体のランクを 0 にしてから `apply_switch` | `dragIn` → `switchIn(..., isDrag)`、gen 5 以降は `runSwitch` をその場で実行 |
| 交代技の繰り出し | `_act` の pivot (行 1568 付近) | 繰り出し先は `best_bench` (評価のための仮定) → `apply_switch` | `selfSwitch` |
| 死に出し | `resolve_turn` の末尾 / `_finish_branch` | ひんしの陣営は `best_bench` → `apply_switch` (行動しない) | ターン終わりの `switch` 要求 → 次の `instaswitch` (順位 3) |
| 死に出しの候補の比較 | `scoring_transition._score_core` の `cand.mode == "replace"` | `apply_switch` → `value` (相手は行動しない 0 ターン + V) | |
| メガシンカ | 交代の後、技の前に `mega_evolve` (**相手 → 自分の固定順**) | `gimmick.mega_view` で種族 id・タイプ・種族値・固定特性を差し替え、権利を消費。HP 割合・ランク・状態異常は保つ | `megaEvo` (順位 104、交代 103 の後)。`formeChange(isPermanent)` → `setAbility(..., isFromFormeChange=true)` で**新しい特性の Start 事象が走る** (sim/pokemon.ts formeChange、`runMegaEvo`)。設置技は受けない |
| 初手 (対戦開始の登場) | **transition には無い** | 対戦中の助言は観測した状態から始まる。初手の いかく は観測側 (`vision/events.py` の交代イベント、特性が確定した個体だけ `dex.switch_in_ability_effects` で確定適用) で状態に入る | `start` で両陣営の `switchIn` → まとめて `runSwitch` (素早さ順の SwitchIn。両方が天候を持てば遅い方の天候が残る) |

- 登場時特性の表は既にある: `advisor/data/boost_moves.json` の `ability_on_switch` (今は `intimidate: {atk: -1}` だけ)、
  `advisor/data/field_effects.json` の `sources.abilities` (天候 4・フィールド 4)、`advisor/data/ability_effects*.json` の
  `switch_in_boost` (いかく・supersweetsyrup)・`weather_set` (4)・`terrain_set` (3、mistysurge は未記載)。
  ただし `switch_in_ability_effects` の呼び出し元は `vision/events.py` だけで、advisor の評価経路 (transition / valuation / engine / selection) は参照していない
  (`git grep` で確認。`intimidate` を参照する advisor のコードは `advisor/rl_bridge.py` の観測の印だけ)。
- 結果として、transition 方式の採点で「いかく持ちへの交代」「天候特性持ちへの交代」を候補にしても、着地の効果は Q に入らない。legacy も同じ (engine に参照なし)。
- 天候・フィールドの変化は transition の対応表で「未対応 (印は付けない。段 D)」(transition.py の docstring)。

### 1.2 選出側 (`advisor/selection.py` `advise_selection`)

- 対面スコアは `_species_matchup_score` → `endgame.duel_score(my_view, 1.0, 技, cand_view, 1.0, 技)`。`fieldv` は渡していない (場の効果なし)。
  撃破ターン数の差 + 先手権 + 積み技を 1 回積む系列。transition も valuation も使わない。
- メガ: 石持ちは `_mega_species_id` (図鑑の `…mega` / 石 id の末尾 x・y で X/Y) でメガ後の種族を決め、`_own_view(p, mega_sid, ..., mega_ability)` で
  メガ後のビューを作り、`matrix_mega` に入れる。3 体組の評価は「割当先だけ `matrix_mega`」(`cell`)、38d6eec9 で初手と理由文も同じ割当で引く (`assigned`)。
- 相手のメガ候補は `_with_mega_candidates` → `gimmick.expected_mega` (使用率の石の事前分布)。ここは対戦側と共有済み。
- 天候: 3 体組への加点 `_weather_synergy_bonus` (`WEATHER_SYNERGY_BONUS` 1.4 / 恩恵役 1 体) だけ。対面の計算には天候が入らない。表は `_WEATHER_SETTERS` / `_WEATHER_ABUSERS` (モジュール内の定数)。
- 設置技: 先発の加点 `HAZARD_LEAD_BONUS` (1.0) だけ。
- いかく: 入らない。

### 1.3 選出側と対戦側の重複

| 事象 | 選出側 | 対戦側 | 差の有無 |
|---|---|---|---|
| メガ後の種族の決め方 | `selection._mega_species_id` (命名規則) | `gimmick.stone_table` / `mega_forms` (石の表) | 同じ結果になるかは**未確認** (`megastone` の汎用 id の扱いも別) |
| メガ後のビュー | `_own_view(p, mega_sid, ability=_own_ability(is_mega=True, item_id))` (登録の配分・性格・持ち物で作り直す) | `gimmick.mega_view(view, mega_sid)` (種族 id・タイプ・種族値・特性 `fixed_ability(mega_sid, is_mega=True) or view.ability` を差し替え、配分・性格・持ち物は元のビュー) | 特性の解決の引数 (`item_id`) が違う。値が一致するかは**未確認** |
| 天候特性の表 | `_WEATHER_SETTERS` (値 "sand" / "snow") | `field_effects.json` `sources.abilities` (値 "sandstorm" / "snow") | 表記が違う ("sand" と "sandstorm") |
| いかく | なし | なし (観測の側だけ `ability_on_switch`) | |
| 対面の評価 | `endgame.duel_score` | `valuation.matchup` (`ko_prob`・`first_prob`) | 登場時効果とは別の重複。この設計の範囲外 (6 章) |

### 1.4 気づいた既存の挙動 (この設計では変えない)

- `apply_switch` は引っ込む個体を `(side.active, side.active_hp)` のまま控えに戻すので、**通常交代で能力ランクが控えに残る**。
  `drag_in` だけは「引っ込んだ個体の能力ランクは 0」にしている。Showdown では通常交代でもランクは消える。その個体が再び出る経路
  (`best_bench` → `apply_switch`、V の後続) で影響し得る。E0 では挙動不変を優先して直さない (6 章の論点)。
- 交代とメガの同時処理の順は固定 (交代は自分 → 相手、メガは相手 → 自分)。今は登場時の効果が設置技だけなので順で結果は変わらない。
  E1 で天候・いかく を入れると順が結果に効く (6 章)。

## 2. 提案

### 2.1 段 E0: 出来事の関数の分離と処理順の明示 (挙動不変)

transition.py に次の純粋関数を置く (名前は案)。どれも入力を書き換えず、新しい `SimSide` (と場) を返す。

| 関数 | 中身 (E0) | 今の対応 |
|---|---|---|
| `leave(side) -> SimSide` | 退場。E0 では何もしない (ランクは今どおり控えに残す)。退場時の効果 (さいせいりょく 等、`heal_on_switch`) を足す場所 | `apply_switch` の前半 |
| `enter(side, idx, opp, fieldv, *, kind) -> EntryResult` | 登場 1 回。順序: (1) 場に置く (`mega_forms` / `volatiles` / `last_move` / `sub_hp` の初期化) → (2) 着地の設置技 (`hazard_frac`、ねばねばネット) → (3) 登場時特性 → (4) 登場時の持ち物。E0 では (3)(4) は空のフック。`kind` は "lead" / "switch" / "replace" / "pivot" / "drag" (記録と E1 の条件分岐用) | `apply_switch` の後半 |
| `switch(side, idx, opp, fieldv)` | 通常交代 = `leave` + `enter(kind="switch")` | `apply_switch` |
| `replace_fainted(side, idx, opp, fieldv)` | 死に出し = `enter(kind="replace")` (ひんしの個体に退場時の効果は無い) | 末尾の `best_bench` → `apply_switch` |
| `lead_entry(me, opp, fieldv)` | 初手 = 両陣営の `enter(kind="lead")`。E1 では両方の (3) を素早さ順に解決する (Showdown の `start` → `runSwitch`) | なし (新規) |
| `mega_evolve(side, ...)` | メガ = 行動。姿の差し替えと権利の消費 → (3') 新しい特性の開始効果のフック (E0 では空)。設置技・持ち物の着地は通らない | `mega_evolve` (そのまま) |
| `pre_move_phase(me, opp, my_act, opp_act, fields)` | 1 ターンの前半を 1 か所に: 交代 (今の固定順) → メガ (今の固定順)。`resolve_turn` と `resolve_turn_branches` の冒頭の重複をここに寄せる | 2 関数の冒頭 |

- `EntryResult` は `side`・`opp` (いかく 等で相手が変わるため)・`field` (天候・フィールドが変わるため)・`events`・`unmodeled` (E0 で解決しなかった登場時効果の印) を持つ。
- **`unmodeled` は `TurnResult.unsupported` / `unsupported_by` に入れない**。入れると `scoring_transition._score_core` が自分の候補の未対応で退避し、
  既存の退避率と順位が変わる (挙動の変更)。E0 では `TurnResult` に別の欄 (例 `entry_unmodeled`) を足して記録だけにする。退避に使うかは E1 の判断。
- `apply_switch` / `drag_in` は名前と戻り値を残し、中身を `switch` / `leave` + `enter(kind="drag")` の合成にする (`advisor/search.py`・
  `advisor/scoring_transition.py`・`tests/test_transition.py` が直接呼ぶため)。
- 場 (`FieldView`) は今、陣営ごとに `my_field` / `opp_field` で外から渡され遷移で変わらない。E0 では `enter` は場をそのまま返す。

### 2.2 選出側が同じ関数を参照する (E0)

- 初手の行: 各 (自分の個体, 相手の候補) について `SimSide` を 2 つ作り `lead_entry` を通した後のビュー (と場) で `duel_score` を呼ぶ。
  E0 では `lead_entry` がビューを変えないので値は今と同じ。E1 で いかく・天候が入ると、選出側にも自動で効く。
- メガ後の行 (`matrix_mega`): 割当先は「`lead_entry` の後に `mega_evolve` (行動)」の順で作る。メガ後のビューの作り方を
  `gimmick.mega_view` (transition の `mega_evolve` が使うもの) に寄せ、`selection._mega_species_id` / `_own_view(mega_sid, ...)` の自前の経路を
  `gimmick` の石の表に寄せる。**値が今と一致するかは未確認**なので、一致の確認 (4 章の受入 2) を先に行い、違えば E0 では寄せずに差を報告する
  (寄せると選出の結果が変わり、E0 の「挙動不変」を満たさないため)。
- `_mega_species_id` と `_own_view` はテスト (`tests/test_selection_advice.py`・`tests/test_first_pick_mega.py`) が直接呼ぶので、関数は残す (中身を寄せるだけ)。
- 天候の加点 (`_weather_synergy_bonus`) と先発の設置加点は E0 では残す。E1 で天候を対面に入れるときに、二重に数えないよう加点を外すかを決める (6 章)。
- 選出側の対面評価 (`duel_score`) は変えない (valuation に寄せるのは別単位)。

### 2.3 段 E1: 登場時効果の追加 (別の大きさ、効果ごと)

E0 の `enter` の (3)(4) と `mega_evolve` の (3') に効果を足す。各効果は config の切替 (既定 off) と
`advisor/data/transition_effects.json` の表 (または既存の `boost_moves.json` `ability_on_switch` / `field_effects.json` `sources`) で持ち、
効果ごとに 4 章の実験を通してから既定を変える。

| 候補 | 表の有無 | 注意 |
|---|---|---|
| いかく (相手の攻撃 −1) | `ability_on_switch` にある | 対象側の特性 (どんかん・きもったま・ミラーアーマー 等) と まけんき・かちき の反応。相手側は推定特性に適用するか (観測側は確定特性だけ適用している) |
| 天候・フィールドの設置 (登場時特性) | `field_effects.json` `sources.abilities` | 場を陣営ごとに外から渡す今の形を変える必要がある。天候の残りターンは状態に無い。両陣営が同時に出る初手は素早さ順 |
| メガ後の特性の開始効果 (ひでり 等) | 同上 | メガは行動なので設置技を通らない。開始の順は Showdown では素早さ順 (megaEvo 同士) |
| かわりもの | なし | `docs/FALLBACK_DIAG_20261020.md` 案 A (状態の写し) と同じ出来事。置く場所 (状態 / engine / transition) の判断と揃える |
| 登場時の持ち物 (ふうせん 等) | `item_effects.json` の欄は `on_damaging_move` / `activation` だけで登場時の項目は無い。ふうせんは `advisor/damage.py` の接地判定で持っている間の効果だけ扱う | 登場時の表示 (ふうせんで浮いている) は観測の側 |

## 3. 変えないもの

- `resolve_turn` (探索 `advisor/search.py` が使う単一分岐) と `resolve_turn_branches` の結果 (E0)。
- 継続方策 `best_bench` (死に出し・交代技の繰り出し先の仮定)。
- 選出の 3 体の選び方、メガの割当先の決め方、初手の決め方 (38d6eec9 の規則: 割当先はメガ前提)、天候・設置の加点 (E0)。
- `scoring_transition` の退避の規則 (登場時効果の印で退避しない)。採点の式・V の式。
- 観測側 (`vision/events.py`) の いかく の確定適用。対戦ログのスキーマ (足すなら欄の追加だけで、既存ログの読み取り互換を保つ)。
- 既存テストの期待値。

## 4. 受入条件と実験

### 4.1 受入条件 (E0)

1. **非メガ局面の不変**: 38d6eec9 の 45 局面 (10/5 以降、登録参照なし / あり) からメガ石を外した局面 (38d6eec9 のコミットメッセージでは 92 件) で、
   選出の推奨 (3 体・先発・順・理由文・`mega_picks`) と `matrix` の全要素が変更前と一致。
2. **3 体と割当先の不変**: 45 局面 × 登録参照なし / あり で、3 体・割当先・先発が変更前 (38d6eec9 適用後) と一致し、`matrix_mega` の全要素が一致。
   メガ後のビューを `gimmick.mega_view` に寄せた結果が一致しない局面があれば、寄せる変更は E0 から外して差を報告する。
3. **既存の transition テストの不変**: `tests/test_transition.py` (`def test_` 19 本)・`test_scoring_transition.py` (7)・`test_advice_m2c.py` (12)・
   `test_advice_m2d.py` (21)・`test_advice_m2e.py` (13)・`test_transition_profile.py` (5) が期待値を変えずに全緑。選出側も
   `test_first_pick_mega.py` (5)・`test_selection_advice.py` (11)・`test_selection_matrix.py` (6)・`test_selection_v2.py` (4)・`test_selection_v3.py` (2)。
4. **選出側が同じ関数を参照する**: 新しいテストで、`transition.lead_entry` / `transition.mega_evolve` を差し替えると選出の `matrix` / `matrix_mega` が
   変わることを確かめる (参照していることの確認)。`selection.py` にメガ後の姿の自前の作り方が残っていないこと。
5. **登場の関数の単体テスト** (新規 `tests/test_entry_effects.py` の案): `switch` = `apply_switch` の結果の一致 (設置技・ねばねばネット・揮発状態)、
   `drag_in` のランク 0、`replace_fainted` が行動しない、`mega_evolve` が設置技を受けない、`entry_unmodeled` が `unsupported_by` に入らない、入力を書き換えない。

### 4.2 実験の形

| 実験 | 対象 | 比べるもの | 判定 |
|---|---|---|---|
| (a) 固定局面での順位の一致 (E0) | 既存テストの局面 + 実戦の advice 行 (版 2 の state、`tools.real_accept` / `tools.advice_replay` の復元) | transition 方式の候補・順位・q・退避理由、legacy の第一候補 | 全件一致。違いが 1 件でもあれば E0 の不具合として扱う |
| (b) 選出 45 局面の再評価 (E0) | 38d6eec9 の 45 局面 × 登録参照なし / あり、メガ石を外した 92 件 | 3 体・割当先・先発・理由文、`matrix` / `matrix_mega` の差の最大値 | 全件一致 (差の最大値 0) |
| (c) 効果ごとの影響 (E1、効果ごと) | (b) と同じ局面 + 実戦の advice 行 | 先発・3 体・第一候補が変わった件数と内訳 | 記録して開発者が判断 (変わること自体は E1 の目的) |
| (d) 勝率の比較 (E1、採否) | シム (`docs/USEFULNESS_VERIFICATION_PLAN_1007.md` §0.4 の search fold B) | 効果あり / なしの対応比較 | 同計画 §0.5 の規則 (MDE 0.05、600 戦 / 条件、k 本の補正) |

- 45 局面の抽出手順はリポジトリに無い (38d6eec9 のコミットメッセージに件数だけ。選出の advice 行は `advice` / `kind` / `t` / `type` の 4 欄で state を持たないので、
  局面は scene 行から作ったと見られる。**未確認**)。(b) の前に、局面の抽出を tools/ のスクリプトにして局面の一覧 (対戦ファイル・時刻) を残す。

## 5. 必要なデータと費用の見積もり

- データ: 対戦ログ (`logs/battles/*.jsonl`、読み取りのみ。選出の advice 行は 271 ファイルに 1565 行、うち 10/5 以降のファイル 50)、
  登録チーム (`config/my_team.json`、登録参照ありの局面用)、使用率 DB (選出の相手の技の予測。比較の前後で同じ snapshot に固定する: `advisor.data_pin`)、
  実戦バンク (選出の相手スロットの重み。同じく固定)。E1 では効果の表 (2.3)。
- 触るファイル (E0):
  - `advisor/transition.py`: 2.1 の関数 (`leave` / `enter` / `switch` / `replace_fainted` / `lead_entry` / `pre_move_phase`、`EntryResult`、`TurnResult.entry_unmodeled`)。
    `resolve_turn` / `resolve_turn_branches` / `_finish_branch` / pivot / `drag_in` を新しい関数の呼び出しに置き換え。
  - `advisor/selection.py`: 初手の行・`matrix_mega` を `lead_entry` / `mega_evolve` 経由に。`_mega_species_id` / `_own_view` の中身を寄せる (受入 2 次第)。
  - `advisor/gimmick.py`: 変更なしの見込み (`mega_view` を使うだけ)。
  - `advisor/scoring_transition.py`・`advisor/search.py`: `apply_switch` を残すので変更なしの見込み。
  - テスト: 新規 `tests/test_entry_effects.py`、`scripts/ci_tests.sh` への追加。45 局面の再評価のスクリプト (tools/)。
- 規模の目安 (見積もり、実装前の推定): transition.py 100〜150 行、selection.py 30〜60 行、テスト 150〜250 行。テストの実行は 4.1 の 3 に挙げた 11 本 + 新規 1 本。
- E1 は効果ごとに、表・切替・単体テスト・(c)(d) の実験が要る。天候・フィールドは場の持ち方 (陣営ごとの `FieldView` を外から渡す形) を変えるので、ほかの効果より大きい。

## 6. 残す論点

1. **同時処理の順**: 交代 (自分 → 相手) とメガ (相手 → 自分) の固定順を、Showdown の素早さ順にするか。E0 では結果が変わらないので固定順のまま、E1 で天候・いかくを入れる前に決める。
2. **通常交代で能力ランクが控えに残る** (1.4)。Showdown と違う。直すと transition の結果が変わる (既存の挙動の変更) ので、別の変更として判断が要る。
3. **選出側の対面評価の二重化**: 選出は `duel_score`、対戦は `valuation.matchup`。登場時効果を共通にしても、対面の評価式は別のまま。寄せるかは別単位。
4. **天候の加点と天候の対面への反映の二重計上**: E1 で天候を対面に入れるなら、`_weather_synergy_bonus` を外すか残すか。
5. **相手の推定特性への適用**: 観測側は「確定特性だけ」いかく を適用している (誤適用の害が大きいため)。評価側で相手の推定特性 (使用率の第一) に登場時効果を適用するか、確率で分岐するか。
6. **かわりもの**: `docs/FALLBACK_DIAG_20261020.md` 案 A の置き場所 (A1 状態 / A2 engine / A3 transition) の判断と、`enter` の登場時特性の扱いを揃える。
7. **割当先がメガを切らずに先発する選択肢**: 38d6eec9 で「扱わない」と決めた。`lead_entry` → `mega_evolve` の順にすると表現はできるが、E0 では足さない。
8. 天候特性の表の表記ゆれ (`selection._WEATHER_SETTERS` の "sand" と `field_effects.json` の "sandstorm")。表を 1 つにするかは E1 で決める。
9. 死に出しの繰り出し先 `best_bench` は評価のための仮定のまま (登場時効果が入ると「いかく持ちを死に出しに使う」価値が best_bench の静的評価に入らない)。

## 7. 未確認

- `selection._mega_species_id` と `gimmick` の石の表が全種で同じメガ後の種族を返すか、`_own_view(mega_sid, ...)` と `gimmick.mega_view` の特性・持ち物が一致するか。
- 38d6eec9 の 45 局面・92 件の抽出手順と局面の一覧。
- チャンピオンズの実機で、初手の登場時効果の順 (素早さ順) とメガ後の特性の開始の順が Showdown と同じか。
