# インシデント記録: シミュレータ (advisor-as-player) の自チームの型の登録が 1 戦ずれていた (直近に出したチームで登録)

## 1. サマリ

| 項目 | 内容 |
|---|---|
| 発生 | 2026-09-04 (advisor-as-player の評価経路の導入 cf9a01f2 から)。自チームを対戦ごとに引く経路 (`tools.check_advisor_player` を `--team-file` なしで動かす) で、対戦 k の選出・行動が対戦 k+1 のチームの型で計算されていた |
| 検知 | 2026-10-09 深夜。採点方式の比較を一般的なチーム (対戦ごとに引く経路) で行う道具を作る委託の中で、サブエージェントがコードの読みから疑いを報告し、別のサブエージェントが 3 戦の実測で確定した |
| 復旧 | 2026-10-09 深夜、feature/team-build d7869aff。teambuilder が出した順の本文を持ち、対戦の生成順 k で送った本文を登録する |
| 影響 | `--team-file` なしの advisor-as-player の測定: 2026-09-04〜05 の advisor baseline と P9 中間 / P9 / P9' (計 4,800 戦) の自分側の型。**訂正 (10/9 21 時台)**: 初版は P6-a / P6-b / P7 / P7b / P8 / P10 も影響ありと書いたが、これらは `tools.check_search_expert` (battle.team の実数値を直接使い、型の登録を通らない。当時の版 8786537a でも同じ) で測っており無傷。`--team-file` を渡す測定 (P1 の RL 加点の比較、構築 run の racing / holdout、team_candidates_measure.sh) は無傷 |
| 失われた生データ | なし。対戦の記録はそのまま。失われたのは「正しい型で計算した判定」で、再測定しないと復元できない |

## 2. 前提

- advisor-as-player は、助言エンジン (`advisor.engine.evaluate`) をそのままプレイヤーにしてローカル Showdown で対戦させ、助言の勝率を測る評価経路 (`champions_agent/env/advisor_player.py`、`tools/check_advisor_player.py`)。
  構築の測定や採点方式の比較は、同じ相手列・同じ seed で 2 条件を対にして測る (対応比較)。
- 助言エンジンは自分側の個体の努力値・性格・持ち物・技を「登録」から読む。実機では `config/my_team.json` (ユーザーの登録チーム)。シミュレータでは、対戦に送ったチームテキストを `register_team_text` で `_SIM_BUILDS` に入れ、`get_my_build` / `get_my_moves` のフックがそれを返す。
  シムの登録に種族が無ければ `config/my_team.json` の登録に落ち、そこにも無ければ「攻撃系 252」の仮定で計算する。
- 自チームの出所は 2 つ: `--team-file` (固定チーム。毎戦同じ本文) と、既定の RankedTeambuilder (使用率スナップショットの上位構築から seed で対戦ごとに引く)。

## 3. 何が起きたか

登録は「teambuilder が直近に出した本文 (`last_text`)」を使っていた (修正前):

```python
def _register_team(self):
    text = getattr(team_source, "last_text", None)
    if text and stats.get("_registered") != text:
        register_team_text(text, resolver)
        stats["_registered"] = text
```

poke-env の `Player._send_challenges` は、対戦 k の challenge (このとき `next_team` → `yield_team` が team k を出す) の直後に
`_battle_semaphore.acquire()` で待ち、semaphore は `_create_battle` で **対戦 k が作られた時点** で解放される。すると直ちに
対戦 k+1 の challenge が送られ、`yield_team` が team k+1 を出す。対戦 k の選出 (`teampreview`) と行動 (`choose_move`) はその後なので、
`last_text` は既に team k+1 になっている。

実測 (2026-10-09、`--opp-seed 20261009`、RankedTeambuilder、3 戦):

| 対戦 k | Showdown に送った自チーム | 登録 (`_SIM_BUILDS`) に入っていた本文 | 6 体の内訳 |
|---|---|---|---|
| 0 | bellibolt / ceruledge / hippowdon / mawile / meganium / slowbro | team 1 (ドドゲザン / ドラパルト / バサギリ / フシギバナ / マフォクシー / マリルリ) | my_team.json の型 2、仮定 4 |
| 1 | azumarill / delphox / dragapult / kingambit / kleavor / venusaur | team 2 (ガブリアス / ギャラドス / ゲンガー / ハッサム / バシャーモ / フラエッテ) | my_team.json の型 1、仮定 5 |
| 2 | blaziken / floetteeternal / garchomp / gengar / gyarados / scizor | team 2 (一致) | 正しい型 6 |

選出の時点で自分の `yield_team` の回数は 2・3・3 で、最後の対戦だけ一致する。`--team-file` (毎戦同じ本文) の 2 戦は修正前から一致していた。

異常のサインは残っていた: 結果 JSON の `stats` には登録の型と仮定に落ちた個体の数が無く、対戦記録 (`team_preview.ours`) は送ったチームを正しく残していたので、
「送ったチーム」と「計算に使った型」を突き合わせる集計が無かった。実機と違いシムの自チームは毎戦変わるのに、登録の仕組みが「直近の 1 本」しか持たない設計だった。

## 4. 被害の内訳

同じ抽選列 (seed 20261009) で 600 戦を見積もった値 (対戦はしていない。3 戦の実測 (登録 3・仮定 9) と見積もりは一致):

| 区分 | 割合 |
|---|---|
| 違う本文の登録で計算した対戦 | 597 / 600 |
| 個体: 別の構築の同じ種族の型 | 14.3% |
| 個体: config/my_team.json の登録の型 | 43.6% |
| 個体: 仮定 (攻撃系 252、持ち物なし) | 41.6% |
| 個体: 正しい型 | 0.5% |

影響した測定 (`--team-file` なし、自チームを対戦ごとに引く経路):

| 測定 | 時期 | 記録 | 影響 |
|---|---|---|---|
| advisor baseline (`scripts/advisor_baseline.sh`) | 2026-09-04 | logs/advisor_verdict | 自分側の型が上の内訳でずれていた |
| P9 中間 (300 戦、A は advisor baseline と同じ走) | 2026-09-04 | logs/advisor_verdict | 同上。同じ seed の対応比較なので両腕とも同じずれ方だが、ずれた型での比較であり、正しい型での結論は未確認 |
| P9 (600 戦) / P9' (1,500 戦) (`scripts/p9_measure.sh`、探索値の採点統合) | 2026-09-04〜05 | logs/advisor_verdict、logs/p9_verdict | 同上 |

無傷だった範囲 (同じ粒度で):

| 測定 | 根拠 |
|---|---|
| P1 (2026-10-09、RL 加点 0 / ×5 / ×25) | `--team-file` で登録チームを固定。毎戦同じ本文なので登録は常に一致 |
| 構築 run の racing (S8〜S10)・封印 holdout・team_candidates_measure.sh | `racing.py` と同スクリプトは `--team-file` を渡す |
| P6-a / P6-b / P7 / P7b / P8 / P10 (logs/p6a_verdict 〜 p10_verdict) | `tools.check_search_expert` で測定。自分側は `battle.team` の実数値から MonView を作り、型の登録 (`register_team_text` / `get_my_build`) を通らない (当時の版 8786537a でも同じ。10/9 の棚卸しで確認) |
| 相手側のチーム | `SequenceTeambuilder.id_for_battle(k)` で対戦ごとの id を引いており、登録とは別の仕組み |
| 実機 (接続テスト) | 登録は config/my_team.json で、シムの登録 (`_SIM_BUILDS`) は使わない |
| RL の学習・ベンチ (`champions_agent.train`) | advisor-as-player を使わない |

勝率への影響は測っていない (未確認)。

## 5. 失われたもの

- 生データ: なし。対戦記録と結果 JSON はそのまま。
- 派生物: 2026-09-04〜05 の advisor-as-player の判定 3 本 (P9 中間 / P9 / P9'。いずれも棄却) と advisor baseline は「ずれた型での対応比較」になっている。正しい型での判定は再測定が要る (合計 4,800 戦、見積もり 4.85 時間。`tools/arc_remeasure.py`)。P6-a (採用した葉評価) を含む他の 6 本は影響なし。
- 時間: 当時の測定 (約 1 夜分) と、今回の検証 (8 戦、約 1 時間)。

## 6. なぜ防げなかったか

- 登録の仕組みを `--team-file` (毎戦同じ) で作って先に検証し、対戦ごとに引く経路でも同じ仕組みが動くと仮定した。poke-env が次の対戦の challenge を先に送ることをコードで確認していなかった。
- 気付けたはずのサイン: (1) 結果 JSON の `stats` に「登録の型 / 仮定に落ちた個体数」が無く、ずれても静かだった。相手側は `id_for_battle(k)` で対戦ごとに引く設計にしていたのに、自分側だけ「直近」で済ませていた非対称。(2) 対戦記録の `team_preview.ours` と助言の `state` の自分側の型を突き合わせる検査が無かった。
- 対応比較は両腕が同じずれ方をするため、勝率の差では異常が見えない。

## 7. 復旧手順 (2026-10-09 実施)

コードの修正 (feature/team-build d7869aff) の後、同じ 3 戦 (`--opp-seed 20261009`) を再実行し、3 戦とも送ったチームと登録が一致 (18 体すべてシムの登録の型) することを確認した。
過去の判定の再測定はしていない (§9)。

## 8. 恒久対策

- `tools/check_advisor_player.py`: teambuilder (定数 / RankedTeambuilder のラッパー) に出した順の本文 `texts` と `text_for_battle(k)` を持たせる。
- `champions_agent/env/advisor_player.py`: `_register_team(battle)` は対戦の生成順 k (`self.battles` の並び) で送った本文を登録する (`team_text_for_battle` / `register_team_for_battle`、純粋)。本文の種族と `battle.team` の種族を照合し、合わなければ k に近い順に種族の合う本文を探す。それでも合わなければ k 番目を使い、`stats` の `team_reg_species` / `team_reg_unverified` に数える (正常時は出ない = 異常のサインを結果 JSON に残す)。
- 回帰テスト: `tests/test_advisor_player.py` に 4 本 (対戦ごとに違うチームを出す teambuilder の模擬で、登録が各対戦のチームに一致する / 種族の照合 / 定数の経路 / last_text だけの旧 teambuilder の互換)。
- 記録: `docs/KNOWN_ISSUES.md` C 節に 1 行。

## 9. 教訓 / 未対応の課題

- 「直近の 1 本」を状態として持つ設計は、非同期に次が始まる経路で必ずずれる。対戦ごとの id や順番で引く (相手側と同じ) 形にする。
- 対応比較は両腕が同じ誤りを共有すると差では見えない。入力の正しさ (送ったものと計算に使ったものの一致) を結果に数えて残す。
- 未対応: (1) 選出計画の相手の系統 (`opp_source.last_id`、racing の `--selection-plan` と `tools/collect_selection_data.py`) も同じ仕組みで、3 戦の実測では対戦 0 だけずれた (タイミング依存)。`id_for_battle(k)` に替えれば直るが既存の挙動の変更なので未着手。(2) メガ後の自分の個体は `get_my_build(species_ja)` がメガ名で引くため、シムの登録に当たらず仮定の型になる (修正前後とも)。(3) P9 系 3 本の再測定は 2026-10-09 ユーザー判断で実施 (道具 `tools/arc_remeasure.py`、結果はこの報告に追記する)。当時のピン・コード・venv とは違う「現行条件での再測定」で、純粋な「型のずれの影響」だけを分離する測定ではない。(4) 勝率への影響は未測定。
