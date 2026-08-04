# レギュレーション変更ランブック (シーズン切替対応)

作成: 2026-08-04。直近の対象: 2026-08-06 の新シーズン
(シーズンM-4は8/5 10:59まで。レギュレーションが M-B のままか M-C になるかは
公式発表待ち。M-A→M-B の前例: 新メガ38種+新アイテム追加で環境激変)。

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

## 2. 本体設定とデータ

- [ ] `champions_agent/config.py` の `TRAINING_BATTLE_FORMAT` を新idへ
- [ ] `champions_agent/data/champions_dex.json` に新種族/新技を反映
      (champions_dex_patch の適用件数が変わる)
- [ ] `advisor/data/dex.json` 更新 (`python -m advisor.data.fetch_dex`)
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
