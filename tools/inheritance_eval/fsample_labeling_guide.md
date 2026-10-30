# F (事実確認) のラベル作成の手順

対象: `cases.jsonl` の {N_CASES} 件 (選出 {N_SELECTION} / 行動 {N_TURN})。画像は `{FRAMES_DIR}/` の下 (各行の `frames` はこのディレクトリからの相対パス)。
計画書 `docs/INHERITANCE_EVALUATION_PLAN_20261031.md` §3.2・§4 に従う。**付けるのは事実だけ** (最善手・勝ちやすさは付けない)。

## 1 件あたりの手順

1. `frame_primary` の画像を開く。同じ決定の窓の他の画像 (`frames`) も見てよい。窓の外の画像・対戦ログの後の行は見ない。
2. `truth.scene` に画像の画面の種類を書く: `selection` / `command` / `move_select` / `watch` / `field_check` / `standby` / `battle_hud` / `field` / `result` / `other`
   (`vision/scenes.py` の名前)。決定画面でなければ (シーン判定の誤り) そのことが分かれば十分なので、`truth.state` は読めた分だけでよい。
3. `truth.state` に、画像から確認できた値を書く。書式:
   - `player.active` / `opponent.active`: 場の個体の種 (日本語名。フォルム・メガは「メガボーマンダ」のように)。
   - `player.party` / `opponent.party`: `[{"species", "hp", "status", "moves", "item", "picked"}]`。画像に出た個体だけ。
     - `hp`: 自分は `[現在値, 最大値]` (画面の数字)、相手は % (画面のバーまたは数字)。読めなければ `null` にして 4. の判読不能へ。
     - `moves`: 画面に出ている技名の列 (自分の技選択画面なら 4 つ)。`item`: 画面に出ている持ち物 (選出画面・様子見画面など)。
     - `picked`: 選出画面なら選んだ 3 体に `true`、選んでいない個体に `false`。それ以外の画面で分からなければ `null`。
   - `weather` / `terrain` / `trick_room` / `screens`: 画面のアイコン・文字で確認できたものだけ (無いことを確認できたら `"none"`)。
4. `truth.legal_actions` に、画面から選べる行動を書く: `[{"kind": "move" | "switch" | "mega" | "pick", "id": 日本語名}]`。
   技選択画面なら選べる技 (PP 切れ・こだわり等で選べないものは除き、理由を `notes` に)、交代は画面に出る控え。
   選出画面なら選べる個体 6 体。画面から確認できなければ `null` のまま、`notes` に理由。
5. `truth.unknown_fields` に、**本来隠れている情報** (ゲームの仕様でプレイヤーに見えない) を列挙する。例:
   `opponent.party[*].moves` (未使用の技) / `opponent.party[*].item` (未判明の持ち物) / `opponent.picks` (相手の選出) /
   `opponent.party[*].hp_raw` (相手の実数値) / `opponent.party[*].stats` (配分)。見えないことが正しいので、誤りにも判読不能にも数えない。
6. `truth.unreadable` に、**画面に出るはずだが読めない** ものを `[{"field": "<欄>", "kind": "<区分>", "note": ""}]` で書く (次の節)。
7. `labels_status` を `"labeled"` (全部確認) / `"partial"` (一部) にし、`labeler` (記入者) と `labeled_at` (記入日 `YYYY-MM-DD`) を書く。
   1 件に {MIN_TURN} 分 (選出は {MIN_SEL} 分) を超えそうなら `partial` で止めて次へ進む。

## 判読不能を捨てない規則

読めない値を推測で埋めない。空欄で捨てもしない。`truth.unreadable` に区分つきで残す (計画書 §4「判読不能を捨てず別区分にする」)。

| 区分 | 意味 |
|---|---|
{UNREADABLE_KINDS}

- 判読不能の決定も F から外さない。集計では「確認できた」「判読不能」「隠れている」を別々に数える。
- システムの記録 (`system_state`) と画像が違っても、画像を優先する。`system_state` を見て正解を決めない (当時のシステムの観測で、正解ではない)。

## 観測できた事実と隠れている情報を分ける規則

- `truth.state` には **その時刻の画面で確認できた値** だけを書く。後の画像・対戦の結末で判明した技・持ち物・選出・HP は書かない
  (計画書 §3.2: 後で判明した情報を過去の入力へ戻さない)。
- それ以前の画面に出ていた情報 (例: 前のターンに相手が使った技) は、同じ窓の画像から確認できなければ書かない。必要なら `notes` に「前の画面で判明」と書く。
- ゲームの仕様で見えない情報は `truth.unknown_fields` に入れる。「見えないはずなのにシステムが値を持っている」場合 (推定) は `notes` に書く。
- 助言 (`system_advice`) は参考で、正解ラベルにしない (計画書 §1: AI の推奨を正解ラベルにしない)。

## 順序と区分

- `split` が `dev` の行から始める。手順を変える必要が出たら dev のうちに直す。`confirm` は手順を固めてから記入し、記入後に手順を変えない。
- `past_data_diagnostic` が `true` の行は、開発で使った対戦の決定 (docs に対戦ログ名が出てくる)。確認の成績は「過去データ診断」と表示する。
- R (`r_cases.jsonl`) は修理・回帰専用。F と同じ書式で記入してよいが、F の集計には入れない。

## 所要の見積もり (未実測)

- 今の {N_CASES} 件: 選出 {N_SELECTION} × {MIN_SEL} 分 + 行動 {N_TURN} × {MIN_TURN} 分 = **{MINUTES} 分** (人のラベル・確認の予算 {BUDGET} 分、計画書 §7 段階 A)。
- 60 件に増やした場合の見込み: {MINUTES_60} 分。
- 1 件あたりの分は見積もり。最初の 5 件 (dev) で実測し、超えるなら `partial` の範囲 (例: 行動の決定は場の 2 体の HP・技・合法手だけ) を決め直す。
  予算を超えた分は未確認として次回へ分ける (計画書 §7)。
