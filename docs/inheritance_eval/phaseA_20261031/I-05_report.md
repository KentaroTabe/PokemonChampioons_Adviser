# I-05 型検査 (段階 A) — 旧 lint / 自動修理を合法セット集に当てた結果 (phaseA_20261031、補完した report)

> 配置の注記 (2026-10-31、開発者判断 (b)): 測定の出力 (manifest.json / cases.jsonl / measurements.jsonl / assets.jsonl / summary.json / stats.json) は `logs/inheritance_eval/phaseA_20261031/<test_id>/` (gitignore) にあり変更していない。report の補完は、運用側の `logs/` への書き込みが権限設定で拒否されたため、この docs 配下に置く (計画書 §9 の所定の場所とは別)。

> 注記 (2026-10-31): 本文は委託先 (実装担当のサブエージェント) の完了報告に含まれていたもので、委託先の .md 書き込みはハーネスに止められた。運用側 (Fable) が summary.json の totals (n 407、不合法通過 8、黙った変更 0、形態の破壊 0、修理で不合法 0、custom の変更 0、合法だが誤り 31、争点のメガ特性 62) と一致することを確認して配置した。判断書 (docs/INHERITANCE_PHASE_A_REVIEW_20261031.md §3 の 3) の補正: 旧 lint は合法性の検証器ではなく、現行の構築経路には別途 `validate_team_text` がある。ここでの 8/9 は「lint だけでは合法性を保証できない」証拠であり、構築経路が不合法を通すという意味ではない (実経路の試験は feat/inh-ci-tests)。

run_id phaseA_20261031。計画書 §5 I-05 の後半だけ (ダメージ計算・遷移の照合は段階 B)。コード 768bae44 + feat/inh-setlint (fc680574)。

## 条件
- 対象: set_lint.lint_set / gate_rejects、repair_candidate (= repair_set)、form_of / is_mega_stone、指定の型の経路 (sets.lint_gate(source=custom) / base_set(custom=…))。
- 合法性の参照: ローカル Showdown validate-team gen9championsbssregmc (BSS Reg M-C、mod champions、Flat Rules)、commit d849b220 (2026-09-09)。CLI だけ (サーバーは起動しない)。1 体ずつ検査、「6 体未満」の誤りだけ捨てた。最終候補はチーム全体でも検査。
- 修理: legal_item = sets.legal_item、補充の技 = chaos の技の重みの降順 (境界例は fixture の usage_moves)、learnset = None (base_set / lint_gate と同じ。role_sets / gen_sets は learnset を渡すので結果が違いうる)。
- セット集: 登録 30 (33 のうち 3 は技か能力ポイントが無く除外。観測事実 / diag)、run の最終候補 28 型 (5 run の勝者と alternatives 3、8 チーム 48 枠。生成物 / diag)、Smogon 2026-09 chaos 全 323 種 (持ち物 1 位 × 特性 1 位 × 技 上位 4 × 配分 1 位。合成結果 / diag。不合法 2 件は指標から除外)、境界例 26 (合法 17 / 不合法 9。dev)。

## 指標 (95% 区間は Clopper-Pearson)
| 指標 | 結果 | 区間 | 要件 |
|---|---|---|---|
| 不合法を門が通した件数 | 8/9 (境界例) | 0.52–1.00 | 0 件を満たさない |
| 実データに不合法な型があったか | 0/379 | — | 実データでは測れない |
| 修理の黙った変更 (理由か注記が無い変更) | 0/31 | 0–0.11 | 満たす |
| custom の型を lint_gate / base_set が変えた件数 | 0/407 | 0–0.009 | 満たす |
| 修理による形態・制約の破壊 (種・性別形態・メガ石) | 0/31 | 0–0.11 | 満たす |
| 修理の出力が不合法になった件数 | 0/31 | 0–0.11 | — |
| 合法だが lint が誤りにした型 | 31 (登録 2/30、Smogon 18/321、境界例 11/17、run 0/28) | 登録 0.01–0.22、Smogon 0.03–0.09 | 警告止まりの候補 |
| 最終候補チームの validate-team | 8/8 合法 | — | — |

門を通った不合法 8 件 (旧 lint の誤り 0): E05 とつげきチョッキ / E12 しんかのきせき / E22 こだわりハチマキ (どれも Champions に無い)、E07 メガリザードン Y の形態に X の石、E13 ボーマンダ + キノコのほうし、E14 ガブリアス + ふゆう、E15 能力ポイント合計 96、E23 ミュウツー (禁止伝説)。E09 (技の重複) を落とせたのは「技 4 つ未満」に数えたための偶然。旧システムの合法性はチーム単位の validate_team_text (run.py / joint_stage.py / repair.py) と legal_item が担っており、lint の役割ではない。

## 戦術的な拒否・修理 (合法 31 件。すべて修理で残り、門で落としたものは 0)
nature_move 17 (例: ドラパルト おくびょう + ドラゴンアロー → せっかち、メガチルタリス いじっぱり + ハイパーボイス → やんちゃ、E02 ひかえめ + アクアジェットだけ → いじっぱり)、item:choice_status 7 (登録のロトム (スカーフ + おにび) とマスカーニャ (スカーフ + ちょうはつ) → たべのこし、Smogon ガブリアス・ヒートロトム ほか)、field_dup 2、few_moves 1、item:none / chesto_no_rest / type_no_move / acrobatics 各 1 (E18 はメガ石を残してアクロバットを外し、補充)。全件は summary.json の legal_but_lint_error に前後の型つき。
観察: NATURE_REPAIR の docstring は「+ を保つ」だが、ひかえめ ↔ いじっぱり の other では + 側も入れ替わる (E02、E10)。理由コード付きの変更なので「黙った変更」には数えていない。

## 争点
1. メガ石の型の特性欄にメガ後の特性が入るケース 62 件 (Smogon のメガ 61 + E25)。validate-team は通すが、実機の登録では基本種の特性しか選べないはず (未確認)。
2. Sirfetch'd のメテオアサルト: ローカル (2026-09-09) は「Gen 9 に無い技」と判定するが、2026-09 の ladder 統計では使われている。検証器が ladder より古い可能性 (未確認)。
3. Floette-Mega: chaos の名前から基本種を引くと floette (Past) になる。正しくは Floette-Eternal と思われ、名寄せの問題なので I-01。
4. E06 別の種のメガ石: validate-team は通すが、実機で持てるかは未確認。
5. チーム単位の規則は最終候補 8 チームだけで検査した。

## 費用
計算 77 秒 (validate-team 446 回を 4 並列で 60 秒)。外部アクセス・LLM・対戦はなし。

## 判定
- 合法性 → 外部実装に置換。旧 lint は合法性を見ない (8/9 を通す)。
- 戦術的な規則と自動修理 → 保留 (警告止まりの候補)。本人が使う合法な型 2 件と Smogon 上位の合法な型 18 件を誤りにしており、構築を改善する証拠も無い (I-07 で比べる必要)。黙った変更・形態の破壊・不合法化は 0。
- 名前の辞書・変換 → I-01 へ。境界例 fixture と道具 → データ保存。
- テスト: R1 が fail (上の 8 件)。R2・R3・純粋部品は合格。

## 未確認範囲
learnset / 使用率 DB を渡す修理の経路、pick_alternative / prefer_registered (注記なしで持ち物を登録のものにする) / resolve_item_clause / enforce_max_megas (I-07)、実機での合法性 (争点 1・4)、ladder と同じ版の Showdown での結果、過去の登録。

I-05 型検査: 用途 = 合法性と常識規則の門 → 対照 = validate-team (d849b220) → 正しさ = 不合法 8/9 を通す・黙った変更 0/31・custom の変更 0/407・形態の破壊 0/31 → 効果 = 合法な型 31 件を戦術的に修理、改善の証拠なし → 費用 = 77 秒 → 採否 = 合法性は外部実装に置換、戦術的な規則と修理は保留、名前の変換は I-01、fixture はデータ保存 → 未確認範囲 = learnset / 使用率 DB を渡す修理、I-07 の差し替え、実機の合法性、Showdown の版の差。
