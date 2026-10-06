# 記事バンクの設計 (2026-10-06): ブログを「構築の実例と考え方の資料」として使う

## 0. 前提と判断待ち (先に読む)

**用途**: 構築提案 (一般的な相手の実例 / 似た構築の弱点) と選出予測 (条件付きの 3 体組)。**操縦 (技選択・交代) には接続しない。**
記事は上位プレイヤーの実例であって頻度の根拠ではない。記事の本数・順位から出現頻度を推定しない。記事 1 本 = 実戦 1 試合とは数えない。

**方針の変更 (2026-10-06 ユーザー指示)**: 本文を LLM に渡さない。6 体と型は**定型部分の決定的な解析**で取り出し、解説は**明確な記述だけ**を限定した
規則で構造化する。LLM に渡すのは構造化した結果と未確定項目の分類だけ (本文・根拠引用・自由記述の補足は渡さない)。ダメージ計算はこちらで
できるので、記事の計算結果は取り込まない。配分 (どう振っているか) が分かれば足りる。

**今回の実装範囲 (取得なしで動くところまで)**:
段階 A (今回): HTML → リンクつきの本文 → 個体の定型解析 → 解説の規則抽出 → 検査 → 本文を含まない記録 → 受け入れテスト (記事の例に沿った合成記事)。
段階 B (次): LLM に構造化データだけ渡して検査候補・役割仮説を出す (印は「モデルの推測」)。送信可否はホストごとの判断。
段階 C (M-C の材料が揃ってから): 相手プール・弱点検査・選出予測への接続 (§6〜§8。規則だけで処理でき、LLM は要らない)。

**判断待ち (取得を始める前にユーザーが決める)**:
1. 取得開始の時期 (10/5 の判断: 季節 M-6 が終わるか、pokedb の新シーズンのデータが出た時点で一覧を作り直してから)。
2. ホストごとの 2 項目: 取得可否 / 保存可能な範囲。本文を外部に送らなくなったので「外部 LLM への本文送信可否」は要らなくなった。
   段階 B で送るのは構造化した事実 (種・持ち物・配分・技の id と、主張の関係) だけなので、送信の判断は「派生データの送信」として別に残す
   (`host_policy.send_llm`、既定 unknown = 送らない)。未確認のホストは取得しない。
3. 辞書で解決できなかった名前 (技・持ち物の表記ゆれ等) を辞書の補修用にローカルに残すか。残すなら名前 1 語だけ
   (BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS 以下) で、記事の文は残さない。初期案は「残す (ローカルの `logs/articles/unresolved_names.jsonl` だけ、
   バンクの記録と LLM の入力には入れない)」。
4. 受け入れテストの素材: ユーザーが添付した記事の例そのものはコミットしない (第三者の本文)。同じ構造・同じ型の数値で解説文を書き直した
   合成記事 (`tests/test_article_parse.py` の `SYNTHETIC`) をテストに使い、添付の例は手元で解析して結果だけ報告する (§9 の末尾)。

**初期案の設定値 (検証前。champions_agent/config `BUILD_ARTICLE_*`)**: 個体の節 / 全体の節の見出し語、個体 6 体・技 4 つ、能力名の表、
252 表示と実数値のラベル、選出条件の語 → 述語、速度の修飾語、否定の接尾、LLM の入力 3,000 / 出力 2,000 トークン・呼び出し 2 回・
処理 20 記事 $3、記事由来の構築はプールの最大 10%、照合は確定した基本種で 4 体以上の一致、混合は α = 0 / 0.05 / 0.10 を並行して記録。

## 1. 用途ごとに使う情報

| 利用場面 | ブログから使う情報 | 使用率・実戦ログが担う情報 |
|---|---|---|
| 構築提案: 一般的な相手を知る | 実在する 6 体、型 (持ち物・性格・特性・配分・技)、役割分担 | どの種・並びをどの程度重視するか |
| 構築提案: 似た構築の弱点を知る | 筆者が明記した苦手な相手 (個体 / チーム全体)、技の採用目的、補完関係 | 現行環境で検査すべき優先度、再現するか (シミュレーション) |
| 選出 | 筆者が明記した条件付きの 3 体組 (先発は明記されたときだけ) | 実際の選出傾向と予測の検証 |
| 操縦 | 接続しない (「追い風しましょう」等の行動助言は捨てる) | 既存の技選択・交代判断 |

## 2. 取得・保存・版管理

- 一覧: `logs/articles/blogs.csv` (ユーザーが URL を足す) → `articles_ingest` の manifest (規制・形式・順位・robots)。URL が足されても
  そのホストの取得が承認済みとは扱わない。記事内リンクを辿って範囲を広げない。
- ホストの可否: `logs/articles/host_policy.json` = {host: {"fetch", "store", "send_llm", "checked_at", "note"}}。値は allow / unknown / deny。
  `article_bank.host_allowed(policy, host, purpose)` は purpose ("fetch" / "send_llm") が allow のときだけ通す (unknown は進めない)。
- 保存するもの (本文を含まない): 6 体の型 (id と数値)、筆者の主張 (関係の種類と対象 id)、選出規則、未確定項目の分類と参照 id、
  出典 URL・公開・更新・取得日時・本文のハッシュ、解析器と辞書の版、検査の警告、処理状態。
- 保存しないもの: HTML、本文、段落、文、根拠の引用。処理中のメモリだけで扱う。主張の出典は `source_ref` (個体番号 + 文番号、例 `m2:s3`)
  で示し、文そのものは残さない。全文を残さないので、解析のやり直しには再取得が要る (制約として記録)。
- 処理状態のキャッシュ: `logs/articles/state.jsonl` (url, body_hash, status, parser_version, warnings の数)。同じ URL + 同じ本文ハッシュは再処理しない。
- 記事中の指示めいた文言はデータとして扱う。解析は規則なので従う経路が無い。段階 B の LLM にも本文は渡らない。

## 3. 解析 (決定的。tools/team_build/article_parse)

### 3.1 入力の整形 (HTML → リンクつきの本文)

`html_to_marked_text(html)`: `<a>` を `[表示名](href)` に、見出し・段落・`<li>`・`<br>` を行に直し、script / style / nav を落とす。
以降の解析はこの「リンクつきの本文」(Markdown 風) だけを見る。受け入れテストの素材もこの形式。

### 3.2 個体の定型部分 (使用ポケモン)

個体の開始は太字や箇条書きではなく、次の組み合わせの行で検出する (`find_member_heads`):

`種族名 @ 持ち物 (性格) 特性` — 各要素はリンクの表示名か素の文字列。**辞書 (vision/data/jp_names.json、NameResolver の正規化キーで厳密一致) で
種族・持ち物・性格が解決できる行だけ**を個体の見出しにする。特性が解決できなければ警告つきで採る。相手への言及 (「相手の [ガブリアス]」) は
この形にならないので 6 体に混ざらない。見出しから次の見出し (または全体の節の見出し語) までをその個体のブロックとする。
「戦術と解説」「選出」等 (BUILD_ARTICLE_TEAM_SECTION_WORDS) 以降は全体の節。

- 種は **基本種 / 使用形態 / メガ石** を分けて保持する: 「メガラグラージ @ ラグラージナイト」→ base `swampert`、form `swampertmega`、stone `swampertite`。
  メガ候補が 2 体あれば 2 体とも保持し、対戦で実際にメガ化する個体は別の情報 (未知) として扱う。
  形態と石の整合 (図鑑 `requiredItem`) が取れなければ警告。石を持つ基本種表記 (「ラグラージ @ ラグラージナイト」) は form を石から補う (注記つき)。
- **リンクの番号で id を決めない。** 表示名と辞書を照合し、リンクに種族番号 (`n260m` の 260) があれば辞書の `num` と突き合わせて矛盾を
  注記 (`notes: link_num_mismatch`) に残す。注記は処理状態に影響しない (yakkun の番号は新しい種で全国図鑑番号とずれる: ブリジュラス n1023 vs 1018、
  サーフゴー n977 vs 1000)。例の記事ではラグラージナイトとムクホークナイトが同じ `item_s=200`。
- 個体の見出しは文ではない: 「。」を含む行は見出しにしない (「ガブリアス@こだわりスカーフ(ようき)さめはだ はエースです。」は説明文)。
- 配分は 3 種類を別に保持し、相互に換算も二重登録もしない (`parse_stat_line`):
  能力ポイント (`HP:32 / 特攻:29 / 特防:2 / 素早:3`) / 252 表示 (`252表示:` を含む行) / 実数値 (`実数値:` の 6 つの数)。
  ラベルの無い配分の行は、全部が能力ポイントの上限 (BUILD_GEN_EV_POINT_CAP) 以下なら能力ポイント、超える値があれば 252 表示として扱い、
  `unlabeled` の警告を付ける。整合性 (ポイントの合計 = BUILD_GEN_POINT_BUDGET、252 表示との対応) は解析の後の検査で見て警告にする。
- 採用技は、**技名だけで構成された行** (リンクまたは `/`・`、` 区切り、全部が辞書の技に解決) のうちブロック内の最初の 1 行から取る。
  技の数が BUILD_ARTICLE_MOVES_PER_SET でなければ警告。説明文に出る技 (変更候補「ブレイズキックを入れた方がいい」、相手の技
  「さいきのいのり」) は採用技に足さない。2 行目以降の技一覧 (変更前後の併記) は `alt_move_lines` の数だけ記録する。
- 技の正当性 (learnset) は後段の検査 (`learnsets`) で警告にする。

### 3.3 解説の限定抽出 (主張 = 「誰についての、どんな関係か」)

個体ブロックの説明文と全体の節を文に分け、**限定した規則**だけで関係を取る。取れたものは全部「筆者の主張」(`basis = author_explicit`) で、
対戦上正しいかは別 (弱点検査・ダメージ計算へ渡す材料)。対象は辞書で解決した種 id / タイプ名 / 技 id だけ。文は残さない。
文中の実体は、リンクの表示名 → 「X タイプ」 → 味方の名前 (見出しの表示名・使用形態・基本種) → 辞書の種名 (3 文字以上、カタカナ語の
境界で。「ゴーストタイプ」の中の「ゴース」は拾わない) → その個体の採用技の名前、の順に辞書の厳密一致で解決する。
対象の直前の「Dは」「Sは」は自分の能力の指示なので相手の修飾とは扱わない。

| 規則 | 文の形 | 主張 |
|---|---|---|
| W1 | 個体ブロック内「X に弱い」(直後に否定の接尾が無い) | `weak_to`: 主体 = その個体、対象 = X (種・タイプ)。「X に弱い <味方名>」なら主体はその味方 |
| W2 | 全体の節「全体として X が重い」「X が重い」 | `weak_to`: 主体 = team |
| F1 | 「X に (圧倒的) 有利」「X はカモ」 (X が種 id) | `favorable_vs` |
| P1 | 「技 X は Y 対策」 (Y が種・タイプ) | `move_purpose`: 技 X の目的 = Y。Y が種・タイプでない (「詰み技対策」) なら未確定 `move_purpose_non_species_target` |
| S1 | 「(修飾) X 抜き」 | `speed_benchmark`: 対象 X、相手の型条件 = 修飾語 (BUILD_ARTICLE_TARGET_MOD_WORDS)。修飾が表に無ければ `mods_unparsed` |
| S2 | 「(修飾) X に上を取られ」 | `outsped_by`: 対象 X、条件 = 修飾語 |
| D1 | 「X の 技 Y 耐え」 | `survives`: 攻撃側 X、技 Y (解決できなければ null)。X の直前の修飾 (「B特化玉」「D特化」) は区切りが曖昧なので採らず `mods_ambiguous` |
| R1 | 「(唯一) X を (安定して) 見れる / 見られる / 受けられる」 | `handles`: 対象 X、`exclusive` = 「唯一」の有無 |
| C1 | 「X に弱い <味方 A> と <味方 B> を助け」 | `supports`: 主体 = その個体、味方 = A, B、相手 = X (W1 の明示主体と併せて A, B の weak_to も出す) |

次は**展開しない**: 「大体物理アタッカーはカモ」「どんなポケモンも……耐えられる」のような対象が種でない広い表現 (→ 未確定 `broad_matchup_claim`)、
行動助言 (「追い風しましょう」「はたき落とすが安定」→ 捨てる。未確定にも入れない)、採用理由の感情表現 (「嫌いすぎて採用」→ 捨てる)。

### 3.4 選出規則 (明記された 3 体と未解決の記述を分ける)

全体の節で「相手に <条件> なら <味方名…>」の形だけを `author_selection_rule` にする:

```json
{"kind": "author_selection_rule",
 "condition": {"subject": "article_opponent", "predicate": "weather_control", "value": "present"},
 "selected_members": ["m4", "m5", "m6"], "lead": null, "recommendation": "preferred", "source_ref": "team:s1"}
```

- 条件の語は BUILD_ARTICLE_CONDITION_WORDS で述語に直す。表に無い条件は規則にしない (未確定 `selection_condition_unknown`)。
- `selected_members` は文中で解決できた**味方の個体 id** だけ。先発は「先発」「初手」と味方名が同じ文にあるときだけ入れ、それ以外は null。
- 「それ以外は雨パ」のように味方名が無い分岐は規則にせず、未確定 `selection_else_branch_members_unspecified` にする。
  「雨パ = 残りの 3 体」は有力な解釈だが筆者の明記ではないので、保存するなら `system_hypothesis` として別に持つ (段階 A では作らない)。
- `recommendation` は「必ず / 固定 / 確定」があれば required、無ければ preferred。「混ぜてもよい」の明記は `combinable = true`
  (無ければ null。2 組だけに制限しない)。選出確率は作らない。
- 予測で使うときは記事の筆者側を今回の相手側に写す: こちらの 6 体に天候操作があると判定できたときだけ、相手が m4〜m6 を選ぶ仮説を少し強める。
  判定できなければ「なし」の分岐へ進めない (§8)。

### 3.5 検査 (`article_bank.validate_record`) と処理状態

6 体・各 4 技、種 id が参戦種、基本種の重複なし、能力ポイントの合計 = BUILD_GEN_POINT_BUDGET と 1 能力 ≤ BUILD_GEN_EV_POINT_CAP、
252 表示と能力ポイントの対応 (8p − 4)、実数値は 6 つ、メガ形態と石の整合、選出規則の個体が 6 体に含まれる、技が learnset にある (警告)。
結果は status = ok / warnings / incomplete (6 体に満たない・技一覧が無い) / failed (個体見出しが無い)。初期標本は人が照合する
(完了条件: 項目別の正確さ・抽出率、「記載なし」「抽出失敗」「未確認」の区別)。

### 3.6 記録の形 (本文を含まない)

```
case: {case_id, record_kind: team|single_set (§3.7),
       source: {url_hash, host, fetched_at, published_at, updated_at, body_hash, publisher_kind, usage_evidence, synthetic},
       meta: {seasons, regulation, regulation_basis, regulation_history, rank, format},
       members: [{id: "m1", species_id (使用形態), base_species_id, mega_stone, item, nature, ability,
                  points: {hp..spe} | null, ev252: {...} | null, actual: [6] | null, moves: [4], warnings: [...]}],
       claims: [{kind, subject, object, object_kind: species|type|move, conditions: [...], basis: "author_explicit", source_ref}],
       selection_rules: [...], unresolved: [{category, source_ref}], counts: {...},
       versions: {parser, dictionary_hash, schema}, status}
```

文字列の値は id / 列挙値 / 参照 id だけ。`assert_no_prose(record)` が「かな・漢字を含む文字列が無い」ことを検査する (保存前と LLM 送信前の門)。

### 3.7 記録の種類と出典の 2 軸 (2026-10-06 ユーザー判断。`article_bank`)

- `record_kind` = `team` (6 体) / `single_set` (単体の型 1 体)。`parse_article(max_members=…)` と `build_record(record_kind=…)` で分ける
  (team は 6 体・各 4 技、single_set は 1 体・4 技で ok)。`validate_record` も個体の数を種類ごとに見て、基本種の重複は team だけ見る。
- 出典は 2 軸を別項目にする: `source.publisher_kind` (誰が掲載したか: personal_blog / user_submission_site / editorial_site / unknown) と
  `source.usage_evidence` (使用実績の根拠: self_report / battle_log_confirmed / none / unknown)。編集部の記事でも実績のある構築を紹介する
  ことがあるので同じ軸にしない。無指定は unknown、表に無い値は ValueError。合成の記事は `source.synthetic = true`。
- `usable_for(record, purpose, regulation)`: parser_eval は常に可 (合成も可)。それ以外は 合成でない・status が ok / warnings・
  meta.regulation が既知で引数と一致 (指定しなければ不可) を満たし、weakness = team / single_set、selection = team、
  pool = team かつ usage_evidence ∈ BUILD_ARTICLE_POOL_EVIDENCE (self_report / battle_log_confirmed)。
- **ユーザー判断**: 編集部の推奨は初版ではプール本体に入れない。判定は publisher_kind ではなく usage_evidence で行う。
- `set_regulation(record, regulation, basis)`: `meta.regulation_basis` (article_text / site_tag / user_confirmed / manifest_season) と
  `meta.regulation_history` (旧値・新値・basis。本文なし) を残す。
- **ユーザー判断**: 合成の記事は `save_bank` / `load_bank` の両方で既定で拒否する (`allow_synthetic=True` のときだけ。一時ディレクトリへの
  保存でも許可は別に要る)。`save_bank` の本文の門は source / meta も含めた記録全体に掛ける。

### 3.8 記事専用の別名辞書 (`vision/data/article_aliases.json`、`article_aliases`)

- OCR 用の `jp_names.json` とは分離し、記事の解析だけが読む。entry = category / alias (記事の表記 1 語) / canonical / id /
  status (confirmed・candidate・rejected) / basis (known_transform・site_id_verified・llm_only・human) / source (host・site_key) / added / history。
  `ArticleDictionary` は厳密一致の表に無い表記を **confirmed だけ**で引く。`dictionary_version` に別名の件数 (とハッシュ) を含める。
- **ユーザー判断**: 往復一致 (`roundtrip_ok`: canonical が別名を含まない厳密一致で同じ id に解決する) は必須の検査だが、元の表記との対応は
  裏付けない (「地震」に「じならし」と正しい id を返しても通る) ので、自動確定の条件にはしない。
- 自動確定は元の表記との対応を裏付けられる 2 つだけ: **known_transform** (BUILD_ARTICLE_KNOWN_TRANSFORMS。「10万ボルト」→「10まんボルト」) と
  **site_id_verified** (リンクのサイト固有 id (BUILD_ARTICLE_SITE_ID_PATTERNS) が、別の記事で表示名の厳密一致 (別名を含まない) から単一の id に
  BUILD_ARTICLE_SITE_ID_MIN_CONFIRMATIONS 記事以上で対応。同じ key に別の id が出たら ambiguous にして以後使わない: yakkun の item_s=200)。
  観測は `parse_article` の `site_id_observations` (ローカル用) → `SiteIdStore` (`logs/articles/site_ids.json`)。
- **ユーザー判断**: LLM だけが根拠の別名は candidate (自動確定しない)。送るのは種別と表記だけ (名前 1 語 ≤ BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS の門)、
  1 回の処理で最大 50 語・1 呼び出し。往復一致を通らない対応は捨てて件数だけ数える。記事本文の LLM 入力 (§4) とは別の経路。
- 既存の confirmed / candidate と id が矛盾する対応は conflict として反映しない。人の確認は
  `python -m tools.team_build.article_aliases --list / --confirm / --reject` (history に basis = human)。記事ごとではなく、
  別名の対応を一度確認すれば次回から辞書で解決する。
- 未解決の名前を集めるのは今は技一覧らしい行のリンクと D1 の技名だけ。個体の見出しの種族・持ち物・性格の表記ゆれ (見出しとして
  認識されない) と特性の表記ゆれ (見出しは認識されるが名前を集めていない) は集まらない (収集の規則を足すかは判断待ち)。

### 3.9 構築ごとの配列 (unit) とバッチ処理 (`article_units`、`articles_process`)

- **ユーザー判断**: 1 ページに複数の構築・単体の型があれば、変換層の出力は区切り線つきの文章ではなく構築ごとの配列
  (unit = {kind, marked, meta, source})。解析は unit ごと。ホスト別の変換層 (`ADAPTERS`) は取得可否とページ構造を確認してから足し、
  無ければ `generic_units` (ページ全体を 1 つの team unit)。
- **ユーザー判断**: 文字コードは URL から決めない。HTTP ヘッダの charset → HTML の meta → UTF-8 (errors="replace") の順 (`decode_html`)。
  URL のパーセント表現は変えず、`normalize_url` は重複排除の鍵 (`url_hash`) にだけ使い、取得には元の URL を使う。
- 規制名: `regulation_from_text` は記事固有の記載 (題名・タグ) の M-A / M-B / M-C を規制 id にする (違う規制が 2 つ以上なら曖昧で None)。
  サイト共通のメニュー (「M-C 情報」等) は渡さない。
- `process_batch`: 入力の検査 (source / meta に本文なし) → ホストの門 (fetch が allow でなければ解析しない) と上限 (BUILD_ARTICLE_BATCH_MAX_ARTICLES
  ページ、本文の合計 BUILD_ARTICLE_BATCH_MAX_BODY_CHARS 文字。超えたページは deferred、本文をディスクへ退避しない) → 解析と観測 →
  自動確定 → LLM (send_llm が allow のホストの名前だけ、確認待ちの名前は送り直さない) → 新たに確定した別名に関係する unit だけメモリ上の本文で
  再解析 → 記録と本文の門 → 本文の破棄。処理状態は `logs/articles/state.jsonl` (本文なし)。取得 (HTTP) の関数は作らない (ホストの許可待ち)。

## 4. LLM の段 (段階 B。構造化データだけを渡す)

- 入力 (`article_bank.llm_payload(record)`): 6 体の型 (id と数値)、個体間の役割の材料 (主張)、選出規則、未確定項目の分類と参照 id。
  本文・引用・自由記述は含まない (`assert_no_prose` を通す)。入力の見積もりが BUILD_ARTICLE_LLM_MAX_INPUT_TOKENS を超えたら LLM の段を飛ばす。
- 任せる仕事: (1) 似た構築の検査候補の提案 (例: アシレーヌへの弱さ、キラフロル対応がサーフゴーに偏る、が提案中の構築にも当てはまるか)、
  (2) 型から役割・改善仮説の提案。出力は全部 `origin = model_inference` (「モデルの推測」) で、記事由来の主張 (`author_explicit`) に昇格させない。
- 呼び出しは 1 記事 BUILD_ARTICLE_LLM_MAX_CALLS 回まで、ツール無し (`BUILD_LLM_CLI_TOOLS = ""`)、出力は JSON Schema で検証し、
  参照できる id は入力にある個体 id / 種 id だけ。
- 相手プールへの登録と選出分布の作成に LLM は要らない (規則で処理)。

## 5. バンクの形式

1 事例 (case) = 同じ使用期間・規制における一貫した 6 体と型の使用例。転載・複数の記事での紹介は同一事例、細かな型変更は子版 (variant)。
記事や版の数で枠を増やさない。実戦の遭遇回数 `n` には入れず、`source = article`、`case_id`、採用枠の重みを別に持つ。
メガ型と非メガ型は型・役割の仮説として区別し、基本種との対応を残す。選出時点で不明な相手の持ち物やメガ形態を記事だけで確定させない。
保存先は `logs/articles/bank/<version>/cases.jsonl` + `manifest.json` (version = 内容のハッシュ、スキーマと解析器の版、件数、規制の内訳)。
読み出しは固定版を指定する (`load_bank`)。対戦中・run 中に取得や LLM の呼び出しは起きない。

## 6. 構築提案への接続 (段階 C。M-C の材料が揃ってから)

- 相手の実例: バンクの事例を実在構築 (mixed) と同じ扱いでプールに入れるが、`source = article` を保ち、件数・評価重みとも
  BUILD_ARTICLE_POOL_SHARE_MAX (10%) を上限に、実戦由来の枠を優先して合成枠の一部を置き換える。重みは種・対の使用率と実戦記録を基準に
  決め、仮定として記録する。同じ記事を複製しても重みが増えないことを検査する。
- 季節の固定 (`season_pin`) に 記事バンクの版・ハッシュ・重み付け設定・生成したプールのハッシュ を加える。
- 似た構築の検索は 6 体の一致だけでなく エース・メガ枠・勝ち方・主要な役割 を使う。「重い相手」(W2) と個体の弱点 (W1) はまず S9 相当の
  弱点検査へ渡し、シミュレーションで再現したときだけ相方・型の探索候補に反映する。

## 7. 選出予測への接続 (段階 C)

- 照合: 相手の 6 体のうち**確定した種** (場に出た / 文言で確定。事前確率 0.95 以上の推定は「システムが採用した推定」として区別し、
  評価の正解には使わない) だけで数え、未確定の枠は一致にも不一致にも数えない。初版は 6 枠を判定できる場合だけ混合する。
  予測を出した時点の情報だけを使う (対戦後に確定した種を遡って使わない)。
- 条件: 基本種で BUILD_ARTICLE_MATCH_MIN_COMMON (4) 体以上一致、記事の 3 体が今回の相手 6 体に含まれる、軸・役割に矛盾が無い、
  「対○○では」の条件は自分の 6 体から確認できる (不明なら適用しない)。
- 混合: 予測分布 = (1 − α) × 既存分布 + α × 記事分布。α = 0 / 0.05 / 0.10 を並行して記録し、初期は判断に使わない。
  記事にない組を確率 0 にしない。先発は明記されたものだけ保持し、先発を扱えないモデルには混ぜない。

## 8. 検証 (用途別。改善したものから有効化)

| 対象 | 比較 | 導入の確認 |
|---|---|---|
| 解析 | 人が確認した記事との照合 | 誤抽出・見落とし・未確定の扱い。合成記事 (否定文・型変更前後) で 1 本にだけ合う規則になっていないか |
| 苦手相手の発見 | 記事が挙げた相手を固定条件で対戦 | 苦手が再現するか、改善候補が軽減するか |
| 構築提案全体 | 通常 run の予算内で記事あり / なしの枠を割り当て、共通の holdout (季節の固定で同じ) で評価。専用 run は組まない | 全体評価の改善。同じ候補を結果に合わせて直して同じ holdout で測り直さない |
| 選出予測 | 同じ対戦の実際の 3 体への対数尤度 | 改善・適用率・評価可能件数・不確実性 |

「3 体登場まで進んだ」と「3 体の正体が分かった」を分ける共通の判定 (`analyze_battles` と `real_opponents` で共用) を先に入れる。
場に出なかった個体を非選出とは扱わず、3 体組を確定できない対戦は部分観測として別に報告する。
照合条件や α を調整するデータと最終評価のデータは分け、評価対戦より後に公開・更新された記事は使わない。

## 9. 受け入れテスト (段階 A の完了条件。tests/test_article_parse.py)

記事の例に沿った合成記事 (6 体・雨パ + 天候対策 3 体) で:

1. 使用個体は 6 体、採用技は各 4 技。種 / 基本種 / メガ石 / 持ち物 / 性格 / 特性が辞書の id になる (リンク番号は使わない)。
2. 能力ポイント・252 表示・実数値を別に保持する (換算しない)。ポイントの合計 66 の検査が通る。
3. 説明文の技 (変更候補のブレイズキック、相手の技のさいきのいのり) が採用技に混入しない。
4. チーム全体の弱点 (アシレーヌ) と個体ごとの弱点 (ペリッパー → キラフロル、メガムクホーク → メガリザードン Y / アシレーヌ / ゴースト) を区別する。
   技の目的 (どくづき → メガメガニウム)、役割 (サーフゴーがキラフロルを唯一見る)、補完 (バンギラスがリザ Y に弱い 2 体を助ける)、
   速度調整 (無振りアーマーガア抜き / 最速アシレーヌ・ギルガルド抜き / 最速スカーフマスカーニャに抜かれる) が構造化される。
5. 天候操作の条件つき 3 体 (m4, m5, m6) は取れるが、雨側の 3 体・先発・選出確率は作らない (それ以外の分岐は未確定の分類になる)。
6. 本文と引用が記録と LLM の入力に混入しない (`assert_no_prose`)。
7. 否定文 (「X に弱いわけではない」)、相手への言及の行 (性格の無い「相手の X @ 持ち物」)、技一覧の 2 行目 (変更前後) を含む合成例でも
   採用技・6 体・主張が崩れない。HTML → リンクつき本文の変換の小テスト。

添付の例そのものは手元で解析して、上の 1〜6 と同じ結果になることを報告する (コミットしない)。

**添付の例の解析結果 (2026-10-06、手元)**: 個体 6 (各 4 技、ポイント合計 66、252 表示の対応と実数値の再計算が 6 体とも一致、検査の問題 0)、
主張 20 (ペリッパー→キラフロル / メガムクホーク→リザ Y・アシレーヌ・ゴースト / どくづき→メガメガニウム / サーフゴーがキラフロルを唯一見る /
バンギラスが m4・m5 をリザ Y に対して助ける / 速度調整 3 件 / 抜かれる 1 件 / 耐え 3 件 / 有利 3 件 / 全体→アシレーヌ)、選出規則 1
(天候操作 → m4・m5・m6、先発 null、preferred、混ぜてよい)、未確定 6 (地震 (漢字) が辞書に無い / 「B特化玉」「D特化」の曖昧な修飾 2 /
広い表現 2 / それ以外の分岐)、本文の混入なし、LLM 入力の見積もり 6.6k 文字 (ASCII の JSON)。
ブレイズキック・さいきのいのりは採用技に入らない。注記: ブリジュラスとサーフゴーのリンク番号が辞書と不一致 (yakkun の番号)。

## 10. 実装の段取り (段階 A。2026-10-06 実装済み)

1. `tools/team_build/article_parse.py` (純粋関数): `html_to_marked_text` / `tokenize_links` / `section_kind` / `parse_member_head` /
   `parse_stat_line` / `parse_moves_line` / `split_sentences` / `entities_of` / `extract_claims` / `extract_selection_rules` / `parse_article`。
2. `tools/team_build/article_bank.py`: `build_record` / `validate_record` (`expected_actual` で実数値を再計算) / `assert_no_prose` / `llm_payload` /
   `host_allowed` / `save_bank` / `load_bank`。
3. `tests/test_article_parse.py` (合成記事 `SYNTHETIC` を内包。否定文・相手への言及・技一覧の 2 行目・リンク番号の矛盾・7 体目・先発の明記・
   条件の否定・未知の条件)。`scripts/ci_tests.sh` に追加。
4. 添付の例を手元で解析 (scratchpad)、結果は §9 の末尾。辞書の表記ゆれ (「10まんボルト」は表では全角「１０まんボルト」: NFKC 正規化で一致) を確認。
5. 次 (段階 B 以降、未実装): 取得 (`articles_fetch`: manifest + host_policy → HTML → `html_to_marked_text` → `parse_article` → `build_record`、
   本文はメモリだけ)、未解決の名前のローカル記録、LLM の段 (`llm_payload` → 検査候補・役割仮説)、§6〜§8 の接続。
6. 追補 (2026-10-06 ユーザー判断、§3.7〜§3.9): 記録の種類と出典の 2 軸・`usable_for`・`set_regulation`・合成の門 (`article_bank`)、
   記事専用の別名辞書 (`article_aliases`)、unit・文字コード・規制名 (`article_units`)、バッチ処理の骨格 (`articles_process`、HTTP なし)。
   テストは `test_article_bank` / `test_article_aliases` / `test_article_units` / `test_articles_process`。
