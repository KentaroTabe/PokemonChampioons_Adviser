# 2026-09-15 構築提案の判定が候補に有利: 参照チームだけ選出モデルの適応が浅かった

## 1. サマリ

- 発生: 2026-09-07 (S8a/S8b を「チーム × 選出方策の変種」で測る手順に変えたとき)。
  検知: 2026-09-15 (rule_0913 の ablation で「チームそのものは参照より弱い (−0.093) が候補専用の選出モデルで
  +0.147 取り返している」と出たことから、参照にも同じ適応を与える対照実験で確認)。
  復旧: 2026-09-16 (手順の修正 S7b と ablation の基準方策の修正をコミット)。
- 影響: 測定段を通した run の判定 (封印 holdout の Δ と verdict) は、**候補は収束まで適応した選出モデルを使い、
  参照 (今の登録チーム) は 1,000 戦の軽い適応か適応なし**、という非対称な比較だった。rule_0913 では参照にも同じ
  適応を与えると参照が勝者を上回る (勝率 0.830 対 0.788)。
- 失われた生データ: なし。探索段の成果物、S7 の適応モデル、対戦記録、registry の artifact は無傷。
  失われたのは判定の解釈 (下記 4 章) と、非対称のまま回した測定の時間の一部。

## 2. 前提

構築提案 (`tools/team_build`, docs/TEAM_BUILDING_IMPLEMENTATION.md) は、探索段で作った候補チームを測定段
(S7〜S13) で「今の登録チーム = 参照」と同じ相手列で対戦させ、勝率差 Δ と信頼区間で PASS / FAIL / INCONCLUSIVE を出す。
助言の選出 (どの 3 体を出すか) は選出モデルが担い、チームごとに対戦を収集して微調整 (適応) すると勝率が上がる。
2026-09-07 の手順改訂で、候補は S8a で 1,000 戦の軽い適応 (cheap) → 生存した候補だけ S7 で収束まで (5,000〜17,000 戦) 適応し、
独立 fold の実測で checkpoint を選ぶ (fresh) ようになった。参照には S8a の cheap までしか与えず、
teampreview (相性順の固定規則) / generic (汎用モデル) / cheap の 3 変種から最善を選んで以降の参照にしていた。

## 3. 何が起きたか

1. `tools/team_build/pipeline.py` の S7 は生存候補だけを適応していた (修正前):

   ```python
   to_adapt = [a for a in cands if a.arm_id in survivors]
   ```

   参照の変種は S8a-2 で `screen_variants = ("teampreview", "generic", "cheap")` の 3 つだけ測り、最善を `ref_arm()` に固定。
2. rule_0913 (9/13〜9/14) の S8b で、候補の teampreview 変種は全て参照に負け (L24 −0.190 / L00 −0.100 / L05 −0.140)、
   同じ候補の fresh 変種は +0.042 前後。適応の上げ幅が候補側で +0.15〜+0.23 なのに、参照の cheap は teampreview より低かった
   (0.72 対 0.737)。ここで非対称に気付けたはずだった。
3. ablation (150 戦) は team −0.093 / pick +0.147 / action +0.013。「チームは弱いが選出モデルで勝つ」という分解だが、
   参照に同じ選出モデルの適応を与えていないので、pick の効果を参照側と比べられなかった。
4. 対照実験 `tools/team_build/reference_adapt` (9/15 21:35〜23:56): 参照を S7 と同じ手順で適応 (9,000 戦で収束、
   fold 2 の検証で n9000 を採用) し、勝者 / 参照 / 参照+適応 を SELECTION 階層の同一相手列 600 戦で対応比較した。

   | 腕 | 勝率 |
   |---|---|
   | 参照 (手順どおり = teampreview) | 0.757 |
   | 参照 + 適応 (fresh) | 0.830 |
   | 勝者 L24_C023 (fresh) | 0.788 |

   | 比較 | Δ | 信頼区間 | 状態 |
   |---|---|---|---|
   | 参照+適応 − 参照 | +0.073 | [+0.029, +0.117] | improved |
   | 勝者 − 参照+適応 | −0.042 | [−0.086, +0.002] | uncertain |
   | 勝者 − 参照 (手順どおりの再現) | +0.032 | [−0.016, +0.079] | uncertain (holdout +0.053 と整合) |

5. 付随して、ablation の基準方策 P0 も取り違えていた。`ablation.py` は P0 に「参照の選出方策のモデル」を渡していた:
   参照が cheap の run (rule_0910) では参照専用に適応したモデルを候補チームにも使い、参照が teampreview の run (rule_0913)
   では `selection_model=None` + `pick_policy="advisor"` になって「分布内なら production の選出モデル、分布外なら teampreview」
   に落ちていた (参照は分布内になり得るが候補は常に分布外)。

## 4. 被害の内訳

判定に使った参照が「浅い適応」だった run (実測値はそれぞれの `evaluation/summary.json`):

| run | 参照の変種 (S8a) | 勝者の変種 | holdout Δ [CI] | verdict | 対照実験 |
|---|---|---|---|---|---|
| improve_20260909_1155 | teampreview 0.737 | generic | +0.025 [−0.029, +0.079] n=600 | INCONCLUSIVE | 未実施 |
| rule_0909 | cheap 0.737 | teampreview | +0.170 [+0.070, +0.270] n=100 | PASS | 未実施 (勝者は適応なしなので非対称の向きは未確認) |
| rule_0910 | cheap 0.807 | fresh | +0.065 [+0.010, +0.120] n=600 | INCONCLUSIVE | **勝者 − 参照+適応 = +0.050 [+0.006, +0.094]** (9/15 23:57〜9/16 2:25: 参照 (cheap) 0.793 / 参照+適応 (16,000 戦、fold 2 検証 0.81) 0.792 / 勝者 0.842。参照の適応 −0.002 [−0.045, +0.042] で、この run は cheap の引きが良く非対称の影響は小さかった) |
| rule_0913 | teampreview 0.737 | fresh | +0.053 [−0.004, +0.111] n=600 | INCONCLUSIVE | **勝者 − 参照+適応 = −0.042 [−0.086, +0.002]** |

ablation の team / pick の値 (rule_0910 の team +0.093、rule_0913 の team −0.093) は P0 の取り違えで、
「同じ選出方策でのチームの差」として読めない (どちらの向きに偏ったかは run ごとに違い、未確認)。

無傷だった範囲:

- S8a の screening (全候補と参照を同じ 1,000 戦で cheap 適応して比較) は対称で、脱落の判断は影響を受けない。
- 候補どうしの順位 (S8b / S10) は同じ参照との差で並べているので、候補間の比較は影響を受けない。
- 封印 holdout の相手列は対照実験で使っていない (SELECTION 階層、seed は run の seed + 11)。
- 学習ループ・使用率 DB・チェックポイント・registry の artifact は無関係。
- chat_0907 (9/9、S8b まで) は参照の cheap 変種 0.897〜0.900 に全候補が負けており、参照を深く適応しても結論は変わらない。

## 5. 失われたもの

- 生データ: なし。
- 派生物: 上記 4 run の verdict は「浅い適応の参照との比較」として読み直す必要がある。rule_0913 は対照実験で
  「勝者は参照+適応に劣る (差は誤差の範囲だが負)」と分かった。rule_0910 は逆に「勝者が参照+適応より +0.050 [+0.006, +0.094]」で、
  優位が残った (参照の cheap 1 回の引きが fresh と同等だった回)。つまり非対称の影響は run ごとの cheap の引きで変わり、
  判定を一律に読み替えることはできない。rule_0909 / improve は未実施。
- 時間: 非対称のまま回した測定 (rule_0909 7.7 時間、rule_0910 9.3 時間、rule_0913 26.4 時間) のうち、
  S8b 以降の参照との比較の部分。S8a / S7 の成果物 (適応モデル・checkpoint) はそのまま使える。

## 6. なぜ防げなかったか

- 9/9 の screen_margin 分析で「S7 の収束適応は cheap 1,000 戦を上回らない (uplift 平均 −0.034)」と出ており、
  参照を cheap で止めても不利にならないと考えた。しかしその分析は候補側の 3 チームだけで、cheap 1 回の引きは
  同じチームでも ±0.05〜0.10 ぶれる (同じ 9/9 の記録)。参照の cheap が teampreview より低く出た rule_0913 は、その引きが悪かった回。
- 気付けたはずのサイン: rule_0913 の S8b で候補の fresh − teampreview が +0.15〜+0.23 なのに、参照の cheap − teampreview
  が −0.017 だったこと (run.log の `after 300` 行に両方ある)。上げ幅の非対称をそのまま読めば、参照にも fresh が必要と分かった。
- ablation の P0 は「参照の選出方策」と書いてあり、それが候補チームでは別の意味 (分布外 → teampreview) になることを
  誰も確かめていなかった。テストは racing の状態遷移だけで、腕の構成は検査していなかった。

## 7. 復旧手順

1. 対照実験 (rule_0913): `bash scripts/team_build_reference_adapt.sh rule_0913 --parallel 3` → 3 章 4 項の結果。
2. 同じ対照実験を rule_0910 でも起動 (`bash scripts/team_build_reference_adapt.sh rule_0910 --parallel 3`)。
3. 手順の修正 (8 章) を入れ、以降の run は参照も S7 で適応する。既存 run の再測定は行わない
   (rule_0913 の結論は対照実験で得た。他は対照実験ツールで個別に確かめられる)。

## 8. 恒久対策

- `tools/team_build/pipeline.py` S7b: 参照も S7 の適応リストに入れ (advisors/reference/、候補と並列)、fresh 変種を
  S8a-2 と同じ相手列で測り、teampreview / generic / cheap / fresh の最善を以降の参照にする。config
  `BUILD_REFERENCE_FULL_ADAPT`=True、run.py `--reference-adapt on|off`。summary.json の `reference_variant.fresh` に記録。
- `tools/team_build/ablation.py`: P0 を明示的に teampreview (チームに依らない) にし、参照の選出方策の効果を
  `pick_reference` (T0PrA0 − T0P0A0) として別に出す。Total = holdout と同じ比較 (T1P1A0 − T0PrA0)。恒等的に 0 だった
  interaction は出さない。
- `tools/team_build/reference_adapt.py` + `scripts/team_build_reference_adapt.sh`: 終わった run に後から対照実験を行う。
- 回帰テスト: `tests/test_team_build_ablation.py` (腕の構成: 参照が teampreview のとき T0PrA0 が無い、P0 が両チームとも
  teampreview、分解の恒等式)、`tests/test_team_build_pipeline.py` (fresh を加えた参照の変種選び)、
  `tests/test_team_build_reference_adapt.py` (対照実験の腕と要約)。

## 9. 教訓 / 未対応の課題

- **候補に与えた処理は参照にも同じだけ与える。** 適応・検証・checkpoint 選択のどれか一つでも参照だけ省くと、
  差は「チームの差」ではなく「処理の差」になる。
- ablation の基準方策は「チームに依らないもの」でなければ、チームを変えた瞬間に別の方策になる。
- 未対応: rule_0909 の PASS (+0.17、100 戦で確定) と improve_20260909_1155 は対照実験を行っていない。
  production の選出モデル (登録チームで微調整済み) を参照の変種に入れるかは未決 (S7b の fresh がその代わりになる)。
  S7b は run あたり参照 1 チーム分の適応 (約 1 時間、候補と並列) を追加する。
