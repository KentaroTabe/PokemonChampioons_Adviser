"""アドバイス結果全体がJSONシリアライズ可能なことの検証。

実運用で gtheory の行列がタプルキーで json.dumps に失敗し、
アドバイス配信が止まった事象の再発防止。

使い方: python -m tests.test_advice_serializable
"""
import json

from advisor.damage import MonView
from advisor.dex import get_dex
from advisor.search import SimSide, search


def test_search_result_serializable():
    def _view(sid):
        sp = get_dex().species(sid)
        return MonView(species_id=sid, name_ja=sid, types=sp["types"],
                       base=sp["baseStats"], ev={"atk": 252, "spe": 252})
    me = SimSide(active=_view("swampert"), active_hp=1.0,
                 bench=[(_view("pelipper"), 1.0)])
    opp = SimSide(active=_view("garchomp"), active_hp=1.0)
    res = search(me, opp, ["earthquake", "waterfall"],
                 [("earthquake", 50), ("outrage", 30)])
    json.dumps(res)   # タプルキー等があればここで例外
    assert res["matrix"] and {"my", "opp", "v"} <= set(res["matrix"][0].keys())
    print("test_search_result_serializable OK")


def test_advise_full_serializable():
    from tests.test_advisor import make_state  # 既存のテスト状態を流用
    from advisor.service import Advisor
    adv = Advisor()
    result = adv.advise(make_state())
    json.dumps(result)
    print("test_advise_full_serializable OK")


if __name__ == "__main__":
    test_search_result_serializable()
    try:
        test_advise_full_serializable()
    except ImportError:
        print("(make_state未提供のためフル検証はスキップ)")
    print("ALL OK")
