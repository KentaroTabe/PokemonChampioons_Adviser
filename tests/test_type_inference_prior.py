"""タイプからの種の推測の事前の重み (advisor.infer.prior_weights) と、選出画面の推定の枠の見せ方 (guess_view) のテスト。

2026-10-06 第18回接続テスト:
- 候補の確率が規制 M-B の頃のままだった。使用率% は pokedb の上位ランカー構築の採用率で、新しいシーズンのオープンデータが
  出るまで前のシーズン (シーズン 5 = M-B) の値が使われる。今期のゲーム内順位 2 位の ボーマンダ が ドラゴン/ひこう の候補で
  0.9% (カイリュー 97.4%) になり、視覚照合なしで カイリュー と推定されていた。
  → 重みを「使用率% と、ゲーム内順位 r を使用率曲線の r 番目に読み替えた値の大きい方」にする (構築システムの merge_ranked と同じ定義)。
  9/29 以降の実戦で場に出た相手 延べ 97 体では、平均の対数尤度 −0.813 → −0.465、第一候補の的中 0.763 → 0.845。
- ほぼ確定の推定の枠にも候補のプルダウンが出て、選ぶことを求められているように見えた → ほぼ確定には候補を出さない。

    scripts/run_test.sh test_type_inference_prior
"""
from __future__ import annotations

from advisor.infer import guess_view, prior_weights
from champions_agent.config import INFER_PAST_USAGE_DECAY, INFER_USAGE_FLOOR, SELECTION_GUESS_SURE_PROB


def test_ingame_rank_lifts_species_missing_from_top_builds():
    # 実データの形 (スナップショット 57): ボーマンダ は上位構築に不在 (採用率は下限の 0.1) だが、ゲーム内順位は 2 位
    latest = [("Garchomp", 58.85, 1), ("Archaludon", 32.81, 8), ("Primarina", 27.08, 3),
              ("Dragonite", 11.46, 24), ("Salamence", 0.1, 2)]
    w = prior_weights(latest, [])
    # 使用率曲線 (降順) = [58.85, 32.81, 27.08, 11.46, 0.1]。順位 2 位 → 曲線の 2 番目
    assert w["salamence"] == 32.81, w
    assert w["garchomp"] == 58.85 and w["primarina"] == 27.08
    # 順位が使用率% より低い種は使用率% のまま (下げない): カイリュー 24 位 → 曲線の末尾 0.1 < 11.46
    assert w["dragonite"] == 11.46 and w["archaludon"] == 32.81
    # ドラゴン/ひこう の中では ボーマンダ が カイリュー より重くなる (以前は 0.1 対 11.46)
    assert w["salamence"] > w["dragonite"]
    print("test_ingame_rank_lifts_species_missing_from_top_builds OK")


def test_same_order_keeps_usage_and_missing_rank_falls_back():
    # 順位が使用率% の順と同じ (2026-09-18 以前のスナップショット) なら重みは使用率% と同じ
    latest = [("A", 40.0, 1), ("B", 30.0, 2), ("C", 5.0, 3)]
    assert prior_weights(latest, []) == {"a": 40.0, "b": 30.0, "c": 5.0}
    # 順位が無い (None) 種は使用率% のまま。使用率% の下限
    w = prior_weights([("A", 40.0, None), ("B", 0.0, None)], [])
    assert w == {"a": 40.0, "b": INFER_USAGE_FLOOR}, w
    print("test_same_order_keeps_usage_and_missing_rank_falls_back OK")


def test_mega_forms_merge_into_base_species():
    # メガ形態の行は基本種へ合算する (使用率% は和、順位は良い方)
    latest = [("Charizard", 10.0, 5), ("Charizard-Mega-Y", 3.0, 2), ("Garchomp", 50.0, 1), ("Hippowdon", 20.0, 3)]
    w = prior_weights(latest, [])
    assert set(w) == {"charizard", "garchomp", "hippowdon"}, w
    # 合算後の曲線 = [50, 20, 13]。リザードン は順位 2 位 (メガの行) → 曲線の 2 番目 20.0 > 合算の 13.0
    assert w["charizard"] == 20.0 and w["garchomp"] == 50.0 and w["hippowdon"] == 20.0, w
    print("test_mega_forms_merge_into_base_species OK")


def test_species_only_in_past_snapshots_are_decayed():
    w = prior_weights([("Garchomp", 50.0, 1)], [("Scyther", 2.0), ("Scyther", 8.0), ("Garchomp", 66.0)])
    # 最新に無い種は過去の最大に減衰を掛けて残す。最新にある種は過去の値を使わない
    assert w["scyther"] == 8.0 * INFER_PAST_USAGE_DECAY and w["garchomp"] == 50.0, w
    print("test_species_only_in_past_snapshots_are_decayed OK")


def test_guess_view_hides_candidates_only_when_nearly_certain():
    sure_cands = [("garchomp", 1.0, "ガブリアス")]
    v = guess_view(sure_cands, "garchomp")
    assert v == {"sure": True, "candidates": []}, v
    # メガ後の id で入っていても同じ個体
    assert guess_view(sure_cands, "garchompmega")["sure"] is True
    # 候補が割れている推定には候補を出す (手で直せるように)
    split = [("salamence", 0.774, "ボーマンダ"), ("dragonite", 0.221, "カイリュー"), ("altaria", 0.005, "チルタリス")]
    v = guess_view(split, "salamence")
    assert v["sure"] is False and v["candidates"] == split, v
    # 閾値ちょうどは「ほぼ確定」
    edge = [("garchomp", SELECTION_GUESS_SURE_PROB, "ガブリアス"), ("flygon", 1 - SELECTION_GUESS_SURE_PROB, "フライゴン")]
    assert guess_view(edge, "garchomp")["sure"] is True
    assert guess_view(edge, "flygon")["sure"] is False
    # 視覚照合なしで採られる帯 (0.85 以上) でも、実測で外れのあった帯 (0.95 未満) には候補を出す
    # (第18回: みず/むし は グソクムシャ 95% だが、相手は オニシズクモ のことがあった)
    band = [("golisopod", 0.946, "グソクムシャ"), ("araquanid", 0.054, "オニシズクモ")]
    v = guess_view(band, "golisopod")
    assert v["sure"] is False and v["candidates"] == band, v
    # 推定した種が候補に無い / 種が無い → ほぼ確定ではない
    assert guess_view(sure_cands, "hippowdon") == {"sure": False, "candidates": sure_cands}
    assert guess_view(sure_cands, None)["sure"] is False
    assert guess_view([], "garchomp") == {"sure": False, "candidates": []}
    print("test_guess_view_hides_candidates_only_when_nearly_certain OK")


def test_inference_built_from_usage_db_is_normalized():
    """使用率 DB があれば、候補の確率は降順で合計 1 (DB の中身には依存しない)"""
    from advisor.infer import TypeInference
    inf = TypeInference()
    if not inf._index:
        print("test_inference_built_from_usage_db_is_normalized SKIP (使用率 DB が無い)")
        return
    key = max(inf._index, key=lambda k: len(inf._index[k]))
    from advisor.infer import _ja2en
    en2ja = {v: k for k, v in _ja2en().items()}
    cands = inf.candidates([en2ja.get(t, t) for t in key], top_k=8)
    assert cands and abs(sum(p for _s, p, _j in cands) - 1.0) < 1e-9, cands
    assert [p for _s, p, _j in cands] == sorted((p for _s, p, _j in cands), reverse=True)
    print("test_inference_built_from_usage_db_is_normalized OK")


def main() -> None:
    test_ingame_rank_lifts_species_missing_from_top_builds()
    test_same_order_keeps_usage_and_missing_rank_falls_back()
    test_mega_forms_merge_into_base_species()
    test_species_only_in_past_snapshots_are_decayed()
    test_guess_view_hides_candidates_only_when_nearly_certain()
    test_inference_built_from_usage_db_is_normalized()
    print("ALL OK")


if __name__ == "__main__":
    main()
