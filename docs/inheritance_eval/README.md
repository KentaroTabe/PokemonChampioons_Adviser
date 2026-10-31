# 引き継ぎ評価 (docs/INHERITANCE_EVALUATION_PLAN_20261031.md) の report の補完

- 測定の出力 (manifest.json / cases.jsonl / measurements.jsonl ほか) は `logs/inheritance_eval/<run_id>/<test_id>/` (gitignore) にあり、変更しない。
- report.md のうち、運用側 (Fable) や委託先が `logs/` に書けなかったもの (権限設定でディレクトリ単位に拒否) は、開発者判断 (2026-10-31、(b)) でここに置き PR に含める。
  各文書の冒頭に、後から補った解釈であることと照合した測定値を書く。
- `phaseA_20261031/`: I-00 (資産台帳、補完)、I-05 (型検査、補完)、I-01 (変換契約、完全版。§8〜§11 が logs 側に無い)。F-R と I-10 の report.md は logs 側にある。
- `phaseB_20261031/`: B0-check (操作の外部基準 B0 の第一候補 Foul Play + poke-engine の適合性・速度。委託先の報告の付録を配置)。
