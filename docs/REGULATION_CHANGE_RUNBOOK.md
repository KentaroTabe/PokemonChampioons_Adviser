# レギュレーション変更ランブック (シーズン切替対応)

作成: 2026-08-04。

**2026-08-05 確定**: シーズンM-5 (8/5〜9/9 10:59) は**レギュレーションM-B続行**
(公式: news.pokemon-home.com/ja/page/803.html)。ルール変更なしのため
本ランブックの§1〜§2は不要、§3のシーズンデータ追従のみ。ベンチ基準は
据え置きで履歴の比較可能性は保たれる (training_changes.json に記録済み)。
**次のレギュレーション変更候補は 9/9 のシーズン切替** — その1週間前を
めどに本ランブックの§0を再実行する。
(M-A→M-B の前例: 新メガ38種+新アイテム追加で環境激変)。

前例のコード痕跡: `pokemon-showdown/data/mods/championsregma/` (M-A凍結版) と
`config/formats.ts` の Reg M-A/M-B エントリ。M-B化のときにやったことの再現が基本。

---

## 0. 変更内容の確定 (発表が出たら最初に)

- [ ] 新レギュレーション名 (M-B継続 / M-C)
- [ ] 追加/削除ポケモン一覧 (特に新メガシンカ)
- [ ] 追加アイテム (メガストーン等)
- [ ] ルール自体の変更有無 (Lv50/3体選出/メガ1回などの基本則)

情報源: ゲーム内お知らせ / 公式サイト / yakkun・gamewith等の攻略サイト。
pokedb詳細ページのスクレイピングは規約禁止 (opendataのみ可)。

## 1. シミュレータ (pokemon-showdown) — 影響が最大

- [ ] 現行モドを凍結: `data/mods/champions` → `data/mods/championsregmb` へ
      コピー (M-Aのとき同様。旧レギュでの再現・比較用)
- [ ] `data/mods/champions` に新要素を追加:
      species/formats-data (解禁フラグ) / items (メガストーン) /
      learnsets / moves。dexの正規IDと突き合わせ
      (**メガストーンIDのずれは過去に学習を止めた**: incidents 6-1)
- [ ] `config/formats.ts` に新フォーマット登録
      (例: `[Gen 9 Champions] BSS Reg M-C` → id `gen9championsbssregmc`)
- [ ] ビルド: showdown は dist を読むため再ビルドが必要
- [ ] 検証: `tools/check_mega_items` (requiredItemベース) と
      チームバリデーション数戦
- [ ] 新しいフォルムの接尾辞 (X/Y に Z が増えた M-C のように) が加わったら、石 → メガ後のフォルムの解決が
      表引き (`advisor/gimmick.stone_form_of`) を通っているか全経路 (構築の対面表・助言・表示名・環境) を確認する
      (2026-09-18 インシデント: Z 石が末尾 x/y の推定で通常メガに倒れていた)

## 2. 本体設定とデータ

- [ ] `champions_agent/config.py` の `TRAINING_BATTLE_FORMAT` を新idへ
- [ ] `champions_agent/data/champions_dex.json` に新種族/新技を反映
      (champions_dex_patch の適用件数が変わる)
- [ ] `advisor/data/dex.json` 更新 (`python -m advisor.data.fetch_dex`)
- [ ] Showdown 由来のキャッシュ (非参戦種 / シムの種 id / 使える持ち物) を `bash scripts/refresh_sim_caches.sh` で作り直してコミット
      (読み手はキャッシュを先に使い、Showdown と違うと `[warn]` を出す。一致だけなら `--check`。2026-10-06〜)
- [ ] `vision/data/jp_names.json` に新ポケモン/新メガストーンの日本語名
      (**欠落は過去に3件あった**: フラエッテナイト等)
- [ ] 種族アイコン: species_harvest が実戦から自動収穫するので事前作業は不要

## 3. チームプールとメタデータ (数日遅れで揃う)

- [ ] pokedb opendata の新シーズンファイル (`s{N}_single_ranked_teams.json`)。
      fetch_ranked_teams は最新から遡って探すので **シーズン番号の追加対応は
      不要のはず** (candidates が 12 まで見ることを確認済み)。
      公開されるまで旧シーズンのプールが使われ続ける点に注意
- [ ] 使用率スナップショット更新 (champions-singles)。新レギュ初週は
      サンプル薄 → メタ最頻セット・埋め込みの品質は数日待つ
- [ ] `tools/species_embedding --build` (日次進化ジョブが毎回やるので自動)
- [ ] 外部取り込みチーム (external): 旧レギュ記事由来。新レギュで違法に
      なる型が混ざる可能性 → バリデーション再実行、違法チームは除外

## 4. 測定基準の切断 (最重要・事故りやすい)

- [ ] **ベンチ履歴は新旧レギュで比較不能になる**。切替日を
      `training_changes.json` に記録 (compare_periods で前後を分けて読む)
- [ ] ベンチ相手プール (上位60構築・固定) は新シーズンのopendataが
      揃った時点で一度だけ切替え、以後固定。切替前後のベンチ値を
      両方測って段差を記録する
- [ ] `best_checkpoint --reset` で昇格記録をリセット (旧レギュ勝率との
      比較は無意味)
- [ ] 日次トラッキング (progress_tracking.jsonl) にも切替を明記

## 5. 学習資産の扱い

- [ ] チェックポイントは観測次元が同じ (388) なのでそのまま継続可。
      ただし新ポケモンは埋め込み未収録 (ゼロベクトル) で当面弱い
- [ ] 選出モデル: 埋め込み経由なので動くが、新種族には外挿。
      新シーズンのプールが揃ってから収集し直す (約30分で49,000件の実績)
- [ ] selfplayプール: 旧レギュのままでも対戦は成立するが、
      新プール到着後に世代交代を待つ (自然に入れ替わる)
- [ ] my_team: 新レギュでの合法性を確認。ユーザーがチームを変えたら
      「もっと見る」読み取り→ collect_selection myteam → 微調整の手順

## 6. アドバイザー (接続テスト系)

- [ ] OCR/画面解析はルール非依存 (変更不要見込み)。新ポケモンの
      名前解決だけ jp_names 更新に依存
- [ ] 新メガの種族値/タイプは dex 更新で自動反映
- [ ] deploy.sh (毎朝5時) で自動反映される

## 実施順序 (8/6当日)

1. 発表内容の確定 (§0) → 影響範囲の判定
   - **M-B継続なら §3と§4のシーズン切替のみ** (mod変更なし)
2. M-Cの場合: §1→§2 を実装、テスト (run_test.sh all + 検証対戦)
3. §4 の基準切断を記録
4. 学習再開 (config切替後、途中保存があるので気軽に再起動できる)
5. §3 は公開され次第 (数日以内)

---

## 2026-09-05 レギュレーションM-C: 確定情報と準備状況

情報源 (公式のみ): news.pokemon-home.com/ja/page/816.html (9/2 告知、
「詳細は後日」) と pokemon.com/us/news/get-ready-for-regulation-set-m-c-in-pokemon-champions
(9/2)。攻略サイト (yakkun 403 / gamewith 等) は未取得。

### 確定した内容 (§0)

- 名称: **レギュレーションM-C**。期間 **2026-09-09 (水) 11:00 JST 〜 2026-12-02 (水) 10:59 JST**
  (米国告知: 9/8 7:00 p.m. PDT 〜 12/1 5:59 p.m. PST)。
- 使用可能: M-A・M-B で使えたポケモンは全て継続 + **新規24種**。
  公式が例示したのは ゴリランダー / セグレイブ と、新メガシンカ
  メガボーマンダ・メガグソクムシャ・メガセグレイブ・**メガアブソルZ・メガガブリアスZ・メガルカリオZ**。
  **24種の完全リストは未公表** (ゲーム内「スカウト」で確認可、とのこと)。
- 基本則 (Lv50 / 3体選出 / メガ1回) の変更は告知に**無し**。
- 現行推奨構築 (final_team.json v2.1 / v3 候補) は全員継続使用可。

### 準備状況 (9/5 時点)

| 節 | 状態 | 備考 |
|---|---|---|
| §0 確定 | 一部 | 24種リストと新メガの種族値は未公表。9/9 以降にゲーム内で確認 |
| §1 シミュレータ | **上流待ち** | smogon/pokemon-showdown master (9/2) に M-C フォーマット無し。champions mod では golisopodmega/baxcaliburmega が `Future`、rillaboom が `Past` (=違法) のまま。Mega-Z 系の種族データは `data/pokedex.ts` に収録済み (ローカル 7/16 版にも12件あり)。champions mod の解禁フラグだけが未対応。確認は `bash scripts/showdown_upstream_check.sh`、反映は `bash scripts/showdown_update.sh` (上流に M-C が入ってから実行) |
| §2 本体設定 | 待機 | `TRAINING_BATTLE_FORMAT` は §1 完了後に `gen9championsbssregmc` へ。jp_names は ゴリランダー/グソクムシャ/セグレイブ/メガ各種 収録済み。Mega-Z の日本語名 (「メガ○○Z」) は上流IDが決まってから追加 |
| §3 プール | 自動 | pokedb は `USAGE_MIN_RANKED_TEAMS`=100 未満の新シーズンを採用しない (8/5 の再発防止)。cbd は季節切替直後に技データが薄くなるが、`META_THIN_MOVE_PCT` の引き継ぎ保護 (9/5 導入) で旧シーズンの健全な型が維持される |
| §4 基準切断 | 予定 | 9/9 11:00 JST を切断点として training_changes.json に記録済み。POOL_PIN / META_PIN は **新シーズン opendata が 100 構築を超えた日に一度だけ** 更新し再基準化 (前後のベンチ値を両方記録) |
| §5 学習資産 | 継続 | 観測次元 388 は不変。新24種は埋め込みゼロベクトル (弱い) → 新プール到着後に選出モデルを再収集 |
| §6 アドバイザー | dex更新待ち | 新メガの種族値/タイプは `python -m advisor.data.fetch_dex` で反映 (上流反映後) |

### 切替日 (9/9) の手順

1. `bash scripts/showdown_upstream_check.sh` で上流の M-C 対応を確認。未対応なら
   学習は M-B 内容のまま継続 (相手構築は旧シーズンのプールで成立する)。
2. 対応済みなら: 学習停止 (`scripts/stop_training.sh`) → `bash scripts/showdown_update.sh`
   → `TRAINING_BATTLE_FORMAT` 切替 → `champions_dex.json` / `dex.json` / `jp_names.json`
   更新 → `scripts/run_test.sh` の関連テスト → 学習再開。
3. `best_checkpoint --reset` と progress_tracking への切断明記 (§4)。
4. pokedb 新シーズンが 100 構築を超えたら POOL_PIN / META_PIN を更新して再基準化。

## 2026-09-11 レギュレーションM-C 切替の実施記録

- 9/9 時点で上流に無かった M-C は 9/9 夜に上流へ入った (812501ede "Add Champions Regulation M-C"、d849b2200 まで取り込み)。
  フォーマット id `gen9championsbssregmc` (mod champions)、M-B は `gen9championsbssregmb` (mod championsregmb) に退避。
- **上流は Node.js 22 以上を要求** (build と起動スクリプト、validate-team も同じ検査で拒否)。ローカルは node@20 のみで
  一度失敗 → 旧コミット f0327afad をブランチ `node20-hold` で保持して運用継続 → ユーザーが `brew install node` (v26.8.2)
  を導入 → master (d849b2200) に戻し `scripts/showdown_update.sh` でビルド → 旧サーバー (pid 114) を停止して
  `scripts/ensure_showdown.sh` で再起動。**サーバーを落とす前にビルドが通ることを確認する** (旧 dist で動く間は落とさない)。
- 解禁内容 (mod の formats-data 差分): 新規 24 種 (プクリン/ペルシアン/アローラペルシアン/ニャイキング/カモネギ/ネギガナイト/
  バリヤード/マルノーム/ボーマンダ/ゴーゴート/グソクムシャ/ゴリランダー/エースバーン/インテレオン/フォクスライ/ストリンダー/
  オトスパス/バチンウニ/イエッサン/オリーヴァ/セグレイブ/パーモット/イキリンコ/マフィティフ) + 新メガ 6 (メガアブソルZ/
  メガボーマンダ/メガガブリアスZ/メガルカリオZ/メガグソクムシャ (特性 かたいツメ に変更)/メガセグレイブ)。持ち物の解禁:
  サイコシード/グランドコート/ふうせん/ゴツゴツメット/レッドカード/だっしゅつボタン/エレキシード/グラスシード/ミストシード/
  ノーマルジュエル/しめつけバンド/ながねぎ (leek)。
- データ: `scripts/export_champions_dex.sh` (新設) で champions_dex.json を再書き出し (species 1518 / moves 938)。advisor/data/dex.json
  (PokeAPI) は新種・新メガ (Mega-Z 含む) を既に収録済み。jp_names: メガアブソル/メガガブリアス/メガルカリオが Z 形態 id に
  結び付いていた誤りを直し、メガ〇〇Z を追加 (06289114)。tools/check_mega_items: 問題なし (Rayquaza-Mega は従来どおり対象外)。
- 設定: `TRAINING_BATTLE_FORMAT = gen9championsbssregmc`。登録チーム (メタグロス/ミミッキュ/アシレーヌ/ムクホーク/ラグラージ/ドドゲザン)
  は M-C で合法 (validate-team)。テスト: test_my_team / test_team_build_sets / test_team_build_opponents /
  test_my_species_resolution (非参戦 1005 → 974 種) / test_team_build_rules 全緑。
- 切断: training_changes.json に 2026-09-11 01:25 の記録、`best_checkpoint --reset all` (balance 0.65 / offense 0.71 / cycle 0.61 をリセット)。
- 未了 (§3/§4): cbd の M-C 日次データは未着 (index の seasons は Current/M5/M4 で M5 = M-B)。pokedb の新シーズンも同様。
  新種の代表型が無い間は、構築では `--sets-file` (型の指定) / `--moves` (技の指定) で新種を候補に入れる。
  POOL_PIN/META_PIN の更新と再基準化は opendata が 100 構築を超えた日に実施する。
- 切替後に気づいた点: 上流の champions mod でメガメガニウムの特性が **Mega Sol** (自分の攻撃を常に晴れ扱い) になり、
  poke-env が "Unexpected effect 'MEGA_SOL'" の警告を 1 戦ごとに数行出す (Effect.UNKNOWN に落ちるだけで学習は継続)。
  既知の効果名 (`TRAIN_IGNORED_UNKNOWN_EFFECTS`) の警告は環境モジュールで抑止し、助言のダメージ計算に Mega Sol
  (ほのお 1.5 倍 / みず 0.5 倍) を入れた。学習は 01:26 に M-C で再開 (fps 266、既存チェックポイントから継続)。
