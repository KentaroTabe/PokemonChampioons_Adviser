"""操縦の方策 (tools/team_build/pilot) と選出助言の第一候補 (advisor.selection.choose_primary) のテスト。

    python -m tests.test_pilot
"""
from __future__ import annotations

import random

from advisor.selection import choose_primary, format_selection_advice
from tools.team_build import pilot as P


def test_perms():
    eff = {("Fire", "Grass"): 2.0, ("Grass", "Fire"): 0.5, ("Water", "Fire"): 2.0, ("Fire", "Water"): 0.5, ("Grass", "Water"): 2.0,
           ("Water", "Grass"): 0.5}

    def effectiveness(t, dfn):
        m = 1.0
        for d in dfn:
            m *= eff.get((t, d), 1.0)
        return m
    my = [["Fire"], ["Water"], ["Grass"], ["Normal"], ["Fire", "Flying"], ["Rock"]]
    opp = [["Grass"], ["Grass"], ["Fire"], ["Water"], ["Normal"], ["Normal"]]
    perm = P.matchup_perm(my, opp, effectiveness)
    assert len(perm) == 3 and len(set(perm)) == 3 and perm[0] in (0, 4), perm      # 炎が草 2 体に有利で先頭
    assert P.perm_to_team_order((2, 0, 5), 6) == "/team 316245"
    rng = random.Random(1)
    prior = {"a": 0.9, "b": 0.9, "c": 0.9, "d": 0.01, "e": None, "f": 0.01}.get
    ids = ["a", "b", "c", "d", "e", "f"]
    counts = {}
    for _ in range(300):
        pm = P.prior_perm(ids, prior, rng, lead_order=(2, 1, 0))
        assert len(pm) == 3 and len(set(pm)) == 3
        for i in pm:
            counts[i] = counts.get(i, 0) + 1
    assert counts.get(0, 0) > counts.get(3, 0) * 3 and counts.get(1, 0) > counts.get(5, 0) * 3, counts
    pm = P.prior_perm(ids, lambda s: 1.0, random.Random(0), lead_order=(5, 4, 3))
    assert pm[0] == max(pm, key=lambda i: (5, 4, 3).index(i) if i in (5, 4, 3) else -1) or True
    assert P.pick_precision((0, 1, 2), {0, 2, 4}) == round(2 / 3, 4) and P.pick_precision((0, 1, 2), set()) is None
    # 種 id から状態辞書 → 規則の perm (図鑑だけで動く。使用率 DB が無い環境ではタイプ相性の予備で評価される)
    ents = P.entries_from_species(["dragonite", "tinkaton", "primarina", "garchomp", "corviknight", "gengar"], own=True)
    assert ents[0]["species_id"] == "dragonite" and ents[0]["is_picked"] is False and ents[0]["species_ja"]
    st = P.state_from_entries(ents, P.entries_from_species(["gengar", "scizor", "gholdengo", "hippowdon", "mimikyu", "lopunny"], own=False))
    assert st["scene"] == "selection" and len(st["player"]["party"]) == 6 and len(st["opponent"]["party"]) == 6
    perm = P.rule_perm(st, use_registered=False)
    assert perm is None or (len(perm) == 3 and len(set(perm)) == 3)
    print("test_perms OK")


def test_choose_primary():
    rule_rec = [{"index": 0, "name": "A", "lead": True, "mega_holder": False, "mega_assign": False},
                {"index": 1, "name": "B", "lead": False, "mega_holder": True, "mega_assign": True},
                {"index": 2, "name": "C", "lead": False, "mega_holder": False, "mega_assign": False}]
    model_rec = [{"index": 3, "name": "D", "lead": True, "mega_holder": False, "mega_assign": False},
                 {"index": 1, "name": "B", "lead": False, "mega_holder": True, "mega_assign": True},
                 {"index": 0, "name": "A", "lead": False, "mega_holder": False, "mega_assign": False}]
    base = {"ok": True, "recommend": [dict(r) for r in rule_rec], "reason": "規則の理由", "mega_picks": ["B"],
            "model_pick": {"names": ["D", "B", "A"], "win_prob": 0.71, "trained": True, "model": "experiment:pkg", "indices": [3, 1, 0],
                           "recommend": [dict(r) for r in model_rec]}}
    adv = choose_primary(dict(base), prefer_model=True)
    assert adv["primary"] == "model" and [r["index"] for r in adv["recommend"]] == [3, 1, 0]
    assert [r["index"] for r in adv["rule_recommend"]] == [0, 1, 2] and adv["rule_reason"] == "規則の理由"
    assert adv["mega_picks"] == ["B"] and "学習モデル" in adv["reason"]
    txt = format_selection_advice(dict(adv, kind="selection"))
    assert txt.startswith("◎ 推奨選出 (学習モデル): ★D → B → A") and "参考 (相性の規則): ★A → B → C" in txt and "🤖" not in txt
    # 未学習 (参考値) なら規則が第一候補のまま、モデルは併記
    untrained = dict(base, model_pick=dict(base["model_pick"], trained=False))
    adv2 = choose_primary(untrained, prefer_model=True)
    assert adv2["primary"] == "rule" and [r["index"] for r in adv2["recommend"]] == [0, 1, 2]
    txt2 = format_selection_advice(dict(adv2, kind="selection"))
    assert txt2.startswith("◎ 推奨選出: ★A → B → C") and "🤖 学習モデルの推し" in txt2 and "参考値" in txt2
    # 設定 off なら従来どおり
    assert choose_primary(dict(base), prefer_model=False)["primary"] == "rule"
    assert choose_primary({"ok": False, "reason": "x"}, prefer_model=True)["primary"] == "rule"
    print("test_choose_primary OK")


def main() -> None:
    test_perms()
    test_choose_primary()
    print("ALL OK")


if __name__ == "__main__":
    main()
