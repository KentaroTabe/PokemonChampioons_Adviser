# 引き継ぎ評価 (docs/INHERITANCE_EVALUATION_PLAN_20261031.md) の report の補完

- 測定の出力 (manifest.json / cases.jsonl / measurements.jsonl ほか) は `logs/inheritance_eval/<run_id>/<test_id>/` (gitignore) にあり、変更しない。
- report.md のうち、運用側 (Fable) や委託先が `logs/` に書けなかったもの (権限設定でディレクトリ単位に拒否) は、開発者判断 (2026-10-31、(b)) でここに置き PR に含める。
  各文書の冒頭に、後から補った解釈であることと照合した測定値を書く。
- `phaseA_20261031/`: I-00 (資産台帳、補完)、I-05 (型検査、補完)、I-01 (変換契約、完全版。§8〜§11 が logs 側に無い)。F-R と I-10 の report.md は logs 側にある。
- `phaseB_20261031/`: B0-check (操作の外部基準 B0 の第一候補 Foul Play + poke-engine の適合性・速度) と B0-prep (動的確認の準備: 外部データの固定の試行、ローカル専用ログイン、Reg M-C のチーム、計測の手順。測定の出力は権限の拒否でリポジトリ外 `../PokemonChampioons_Adviser-worktrees/external/logs/...` にある)。どちらも委託先の報告の付録を配置。
- `phaseB_20261102/`: B0-dynamic (動的照合の実行基盤と Showdown 側の正解 25 ケース・31 シナリオ。cargo 無しのため試験側は未実施。一時サーバーの起動時の外向き通信を発見。委託先の報告の付録を配置)。
- `phaseB_20261102/B0-run_report.md`: poke-engine (bss) の build と試験側の初回実行 (46 チェックポイント: 一致 22 / 観測後だけ対応 2 / 欠落 14 / レビュー待ち 8。MCTS の揺れは小さい。対戦・速度は外部データ未配置で未実施)。
- `phaseB_20261102/B0-eval-fix_report.md`: 比較処理の修正後の再評価 (Triple Axel は急所の条件を揃えて差 9.18% / 7.36% で閾値内だが命中回数の分布は区間外、レビュー待ち 7 件を確定、区分は 24 / 1 / 3 / 17 / 0 / 1、「一致」は許容差込み)。
- `phaseB_20261102/B0-fixscope_report.md`: 修正版の範囲と費用の調査 (技 3 件は転記の誤り、メガ 5 件は取り込み漏れ、行動順は reverse の後の判定、持ち物の列挙 u8 254/256、へんしん系 300〜600 行の仮説。範囲 小 / 中 / 大 と見直しの条件)。
- `phaseB_20261103/`: LOCAL_SETUP_report (Codex: 外部データの取得と常駐 8100 の適用)、B0-battles_report (データ構成 regmc_smogon_only_2026-09、3 戦完走、判断 p50 0.56 / 1.33 / 3.40 秒、RSS 最大 483 MB、自分の持ち物 4 種が UNKNOWNITEM)。
- `phaseB_20261103/B0-order1_report.md`: 修正版の順序 1 (技 13・メガ 5 の表、メガシンカ時の行動順、Foul Play 側の名前・状態の検査、対応状況の表)。動的照合 一致 24 → 38、欠落 17 → 7、cargo test 全世代通過。コードは external の poke-engine regmc-fix と foul-play local-regmc (patches/ に差分と sha)。
