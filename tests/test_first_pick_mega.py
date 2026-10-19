"""選出の初手評価がメガ割当を反映するテスト (2026-10-19 レビュー)。

3 体組の評価はメガ割当先にメガ後の姿 (matrix_mega) を使うが、初手の行は全員に素の姿 (matrix) を
使っていた。初手と理由文の対面も 3 体組の評価と同じ割当で引くことを確かめる。
対面スコアは表で差し替え、相手スロットの重み・相手のメガ候補の混入は中立にして決定的にする。

    python -m tests.test_first_pick_mega
"""
from __future__ import annotations

import advisor.real_prior as RP
import advisor.selection as S


def _mon(species_id, species_ja, item_id=None):
    return {"species_id": species_id, "species_ja": species_ja,
            "item_id": item_id, "ability_id": None,
            "is_picked": False, "types": [], "moves": []}


def _opp(species_id, species_ja):
    return {"species_id": species_id, "species_ja": species_ja, "types": []}


OPPS = [_opp("corviknight", "アーマーガア"), _opp("kingambit", "ドドゲザン")]


def _run(party, table, opps=None):
    """表 {(自分の種族ID, 相手の種族ID): score} で対面スコアを差し替えて advise_selection を回す。表に無い対面は 0"""
    saved = (S._species_matchup_score, S._with_mega_candidates, RP.slot_weights, RP.species_pick_prior)
    S._species_matchup_score = lambda mv, _mm, cv, _cm: table.get((mv.species_id, cv.species_id), 0.0)
    S._with_mega_candidates = lambda cands, _p, _s: cands
    RP.slot_weights = lambda priors: [1.0] * len(priors)
    RP.species_pick_prior = lambda sid: None
    try:
        state = {"selection_picked": 0,
                 "player": {"party": party},
                 "opponent": {"party": opps or OPPS}}
        return S.advise_selection(state, use_registered=False)
    finally:
        S._species_matchup_score, S._with_mega_candidates, RP.slot_weights, RP.species_pick_prior = saved


def _both(score):
    return {"corviknight": score, "kingambit": score}


def _table(rows):
    """{自分の種族ID: {相手の種族ID: score}} → 差し替え用の表"""
    return {(me, opp): v for me, d in rows.items() for opp, v in d.items()}


def _lead(advice):
    return next(r["name"] for r in advice["recommend"] if r["lead"])


def _assignees(advice):
    return [r["name"] for r in advice["recommend"] if r.get("mega_assign")]


def _picks(advice):
    return sorted(r["name"] for r in advice["recommend"])


# 6 体: ラグラージだけメガ石持ち。素の姿は不利、メガ後は有利 (メガ前後で優劣が逆転する)
ROWS_SWAP = {
    "swampert": _both(-1.0), "swampertmega": _both(1.5),
    "mimikyu": _both(0.6), "duraludon": _both(0.5),
    "rotomheat": _both(-0.5), "dragonite": _both(-0.5), "metagross": _both(-0.5),
}


def _party_swap(swampert_item):
    return [_mon("swampert", "ラグラージ", swampert_item),
            _mon("mimikyu", "ミミッキュ", "lifeorb"),
            _mon("duraludon", "ブリジュラス", "leftovers"),
            _mon("rotomheat", "ロトム", "sitrusberry"),
            _mon("dragonite", "カイリュー", "choiceband"),
            _mon("metagross", "メタグロス", "assaultvest")]


def test_non_mega_unchanged():
    """(a) メガ石なし・割当先なしでは、初手・3 体・説明は素の姿の行列だけで決まる (従来どおり)"""
    advice = _run(_party_swap("leftovers"), _table(ROWS_SWAP))
    assert advice["ok"], advice
    assert _assignees(advice) == [], advice["recommend"]
    # 3 体: ラグラージ (素の姿で不利) は入らず、ミミッキュ・ブリジュラス + 同点の 1 体 (組の列挙で先に出たもの)
    assert _picks(advice) == sorted(["ミミッキュ", "ブリジュラス", "ロトム"]), advice["recommend"]
    assert _lead(advice) == "ミミッキュ", advice["recommend"]
    assert advice["reason"] == ("ミミッキュ (アーマーガアに有利、ドドゲザンに有利) / "
                                "ブリジュラス (アーマーガアに有利、ドドゲザンに有利) / ロトム"), advice["reason"]
    print("test_non_mega_unchanged OK")


def test_lead_uses_mega_row_for_assignee():
    """(b)(c) メガ前後で優劣が逆転する局面: 3 体と割当先は 3 体組の評価のまま、初手は割当先のメガ後の姿で選ぶ。
    従来 (全員を素の姿) ならミミッキュ (1.2 > −2.0) が初手、割当反映ではラグラージ (3.0) が初手"""
    advice = _run(_party_swap("swampertite"), _table(ROWS_SWAP))
    assert advice["ok"], advice
    assert _picks(advice) == sorted(["ラグラージ", "ミミッキュ", "ブリジュラス"]), advice["recommend"]
    assert _assignees(advice) == ["ラグラージ"], advice["recommend"]
    assert _lead(advice) == "ラグラージ", advice["recommend"]
    # 説明の対面も割当先はメガ後の姿で引く (素の姿なら −1.0 で「有利」が付かない)
    assert advice["reason"].startswith("ラグラージ (アーマーガアに有利、ドドゲザンに有利)"), advice["reason"]
    print("test_lead_uses_mega_row_for_assignee OK")


def test_only_assignee_uses_mega_row():
    """(d) メガ石持ちが 2 体いるとき、メガ後の姿で評価するのは割当先だけ。
    割当先 = ガブリアス (相補性で選ばれる)。ラグラージはメガ後なら 3.4 で最高だが、割当外なので素の姿 (−2.0)。
    初手: ガブリアス (メガ後 2.0) > カイリュー (1.9) > ラグラージ (素の姿 −2.0)。
    従来 (全員素の姿) ならカイリュー、全員メガ後ならラグラージになる"""
    rows = {
        "swampert": _both(-1.0),
        "swampertmega": {"corviknight": 2.9, "kingambit": 0.5},
        "garchomp": {"corviknight": 0.0, "kingambit": -1.0},
        "garchompmega": {"corviknight": 0.0, "kingambit": 2.0},
        "dragonite": {"corviknight": 1.9, "kingambit": 0.0},
    }
    party = [_mon("swampert", "ラグラージ", "swampertite"),
             _mon("garchomp", "ガブリアス", "garchompite"),
             _mon("dragonite", "カイリュー", "choiceband")]
    advice = _run(party, _table(rows))
    assert advice["ok"], advice
    assert _assignees(advice) == ["ガブリアス"], advice["recommend"]
    assert _lead(advice) == "ガブリアス", advice["recommend"]
    # 割当外のメガ石持ちの説明は素の姿 (全対面 −1.0 → 「有利」なし)
    assert " / ラグラージ / " in f" / {advice['reason']} / ", advice["reason"]
    # メガ枠の表示は割当先が先頭
    assert advice["mega_picks"][0] == "ガブリアス", advice["mega_picks"]
    print("test_only_assignee_uses_mega_row OK")


def test_unknown_mega_form_falls_back():
    """(e) メガ石持ちでもメガ後の形態が図鑑に無い (汎用の megastone で種に対応する形態が無い) ときは、
    割当先になっても素の姿の行列に退避して落ちない"""
    assert S._mega_species_id("mimikyu", "megastone") is None
    rows = {"mimikyu": _both(-0.2), "duraludon": _both(0.5), "rotomheat": _both(0.3)}
    party = [_mon("mimikyu", "ミミッキュ", "megastone"),
             _mon("duraludon", "ブリジュラス", "leftovers"),
             _mon("rotomheat", "ロトム", "sitrusberry")]
    advice = _run(party, _table(rows))
    assert advice["ok"], advice
    assert _assignees(advice) == ["ミミッキュ"], advice["recommend"]
    assert _lead(advice) == "ブリジュラス", advice["recommend"]
    assert "ミミッキュ" in advice["reason"] and "ミミッキュ (" not in advice["reason"], advice["reason"]
    print("test_unknown_mega_form_falls_back OK")


if __name__ == "__main__":
    test_non_mega_unchanged()
    test_lead_uses_mega_row_for_assignee()
    test_only_assignee_uses_mega_row()
    test_unknown_mega_form_falls_back()
    print("ALL OK")
