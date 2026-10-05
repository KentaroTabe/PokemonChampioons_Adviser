"""レートの増減を「どの対戦の」勝敗に帰属するかのテスト (tools.battle_outcome.rate_inference と battle_logger)。

2026-10-06 第18回接続テスト: ランク画面のレートは対戦前の値で表示が始まり、数秒で対戦後の値に変わる。1 回だけ読めた値は
どちらか分からない (15 戦のうち 1 回だけ読めた 9 戦で、対戦前の値 5 / 対戦後の値 4)。旧規則は「前の対戦で読めた値との差」を
そのままこの対戦の勝敗にしていたので、対戦前の値どうしの差 (= 直前の対戦の増減) をこの対戦の勝敗と記録した
(2 戦目: 1 戦目の負けの −19.0 → 「負け」。保存されたフレームには WIN と写っていた)。

    scripts/run_test.sh test_rate_outcome_attribution
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from battle_logger import BattleLogger
from champions_agent.config import RATE_INFER_MAX_DELTA, RATE_INFER_MIN_DELTA
from tools.battle_outcome import OutcomeTracker, outcome_from_records, rate_inference


def _ri(reads, rate_open=None, fresh=True, post=False, prev=None):
    return rate_inference(reads, rate_open, fresh, post, prev, RATE_INFER_MIN_DELTA, RATE_INFER_MAX_DELTA)


def test_two_reads_in_one_battle_are_pre_and_post():
    # 同じ対戦の終了画面で 2 つ読めた = 対戦前 → 対戦後 (第18回の 9 戦目 / 11 戦目)
    assert _ri([1705.906, 1686.715]) == {"outcome": "loss", "from": 1705.906, "to": 1686.715}
    assert _ri([1698.933, 1685.152], rate_open=1686.715, fresh=False)["outcome"] == "loss"
    assert _ri([1600.0, 1608.0, 1617.5])["outcome"] == "win"          # 途中の値が挟まっても最初と最後
    # 小数の読み違い (差が下限未満) / 数字の誤読 (差が上限超) は増減に数えない
    assert _ri([1705.906, 1705.908]) is None
    assert _ri([1600.0, 2500.0]) is None
    print("test_two_reads_in_one_battle_are_pre_and_post OK")


def test_single_read_is_attributed_only_when_unambiguous():
    # 直前の対戦の後の値が分かっている → 1 つだけ読めた値は、この対戦の後の値 (第18回の 12 戦目)
    assert _ri([1668.228], rate_open=1685.152, post=True) == {"outcome": "loss", "from": 1685.152, "to": 1668.228}
    # 直前の対戦の勝敗が確定していて、差の向きが逆 → 直前の対戦の増減ではあり得ない
    assert _ri([1583.0], rate_open=1600.0, prev="win")["outcome"] == "loss"
    assert _ri([1617.0], rate_open=1600.0, prev="loss")["outcome"] == "win"
    # 差の向きが直前の対戦と同じ → 直前の対戦の増減かもしれない (第18回の 2 戦目: 1 戦目は負け、差は −19.0、2 戦目は勝ち)
    assert _ri([1682.807], rate_open=1701.762, prev="loss") is None
    assert _ri([1618.0], rate_open=1602.0, prev="win") is None
    # 直前の対戦の勝敗が不明・推定 → 帰属できない
    assert _ri([1618.0], rate_open=1602.0, prev=None) is None
    # rate_open が直前の対戦で読めた値でない (間に読めなかった対戦がある) → 帰属できない (後の値だと分かっていても)
    assert _ri([1618.0], rate_open=1602.0, fresh=False, post=True) is None
    assert _ri([1618.0], rate_open=1602.0, fresh=False, prev="loss") is None
    # 読めていない / 入る前の値が無い / 同じ値
    assert _ri([]) is None and _ri([1618.0]) is None and _ri([1602.0], rate_open=1602.0, post=True) is None
    print("test_single_read_is_attributed_only_when_unambiguous OK")


# ---- battle_logger を通した再現 ----
def _st(seq, scene="field", rate=None, outcome=None, my_f=0, opp_f=0):
    def party(n):
        return [{"status": "fainted" if i < n else None} for i in range(3)]
    st = {"scene": scene, "outcome": outcome, "turn": 1, "events": [], "field": {}, "selection_picked": None,
          "mega_used": {}, "battle_seq": seq,
          "player": {"active_index": None, "remaining": None, "hazards": {}, "party": party(my_f)},
          "opponent": {"active_index": None, "remaining": None, "hazards": {}, "party": party(opp_f)}}
    if rate is not None:
        st["last_rate"] = {"value": rate, "ts": time.time()}
    return st


def _rows(path: Path, typ: str) -> list:
    return [r for r in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()) if r.get("type") == typ]


def _battle_with_text(lg: BattleLogger, seq: int, outcome: str, rates: list) -> Path:
    """勝負の文言で勝敗が確定し、ランク画面で rates を順に読めた対戦"""
    lg.on_frame(_st(seq), [])
    lg.on_frame(_st(seq, outcome=outcome), ["battle_win" if outcome == "win" else "battle_lose"])
    path = lg._file
    for i, r in enumerate(rates):
        lg.on_frame(_st(seq, outcome=outcome, rate=r), ["battle_end_rank"] if i == 0 else [])
    return path


def test_previous_battles_delta_is_not_this_battles_outcome():
    """第18回の 1 → 2 戦目: どちらのランク画面でも対戦前の値だけが読めた。1 戦目の負けの差を 2 戦目の負けにしない"""
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        f1 = _battle_with_text(lg, 1, "loss", [1701.762])
        assert [r["outcome"] for r in _rows(f1, "outcome")] == ["loss"]
        lg.on_frame(_st(2, scene="selection", rate=1701.762), [])      # 2 戦目へ (レートの表示は残っている)
        f2 = lg._file
        assert f2 != f1
        lg.on_frame(_st(2, rate=1682.807), ["battle_end_rank"])        # 読めたのは対戦前の値 (= 1 戦目の後の値)
        outs = _rows(f2, "outcome")
        assert len(outs) == 1 and outs[0]["outcome"] == "unknown" and "inferred" not in outs[0], outs
        assert lg.outcome_info() == {"outcome": "unknown", "inferred": False, "basis": None, "basis_text": None}
        assert [r["value"] for r in _rows(f2, "rate")] == [1682.807]   # レートの行は従来どおり残る
        print("test_previous_battles_delta_is_not_this_battles_outcome OK")
    finally:
        shutil.rmtree(tmp)


def test_late_post_rate_revises_the_outcome():
    """第18回の 8 → 9 戦目: 9 戦目のランク画面の検出時に読めたのは対戦前の値で、対戦後の値は 34 秒後に読めた"""
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        _battle_with_text(lg, 8, "loss", [1724.041])
        lg.on_frame(_st(9, scene="selection", rate=1724.041), [])
        f9 = lg._file
        lg.on_frame(_st(9, rate=1705.906), ["battle_end_rank"])
        assert [r["outcome"] for r in _rows(f9, "outcome")] == ["unknown"]
        assert lg.pop_revision() is None
        lg.on_frame(_st(9, rate=1686.715), [])                          # 対戦後の値が読めた
        outs = _rows(f9, "outcome")
        assert len(outs) == 2 and outs[1] == dict(outs[1], outcome="loss", inferred=True, basis="rate",
                                                   basis_text="レート 1705.9 → 1686.7", revised_from="unknown"), outs
        rev = lg.pop_revision()
        assert rev and rev["outcome"] == "loss" and rev["revised_from"] == "unknown" and lg.pop_revision() is None
        assert lg.outcome_info()["basis_text"] == "レート 1705.9 → 1686.7"
        # 読み手は最後の outcome 行を採り、推定として扱う
        recs = [json.loads(x) for x in f9.read_text(encoding="utf-8").splitlines()]
        assert outcome_from_records(recs) == ("loss", True, False)
        # 同じ値をもう一度読んでも行は増えない
        lg.on_frame(_st(9, rate=1686.715), [])
        assert len(_rows(f9, "outcome")) == 2
        # 次の対戦: 2 つ読めた対戦の最後の値は「後の値」なので、1 つだけ読めた値の差をこの対戦の勝敗にできる
        lg.on_frame(_st(10, scene="selection", rate=1686.715), [])
        f10 = lg._file
        lg.on_frame(_st(10, rate=1698.933), ["battle_end_rank"])
        outs = _rows(f10, "outcome")
        assert len(outs) == 1 and outs[0]["outcome"] == "win" and outs[0]["inferred"] is True \
            and outs[0]["basis"] == "rate" and outs[0]["basis_text"] == "レート 1686.7 → 1698.9", outs
        print("test_late_post_rate_revises_the_outcome OK")
    finally:
        shutil.rmtree(tmp)


def test_post_rate_known_from_text_battle_with_two_reads():
    """第18回の 11 → 12 戦目: 11 戦目 (文言で負け) で 2 つ読めた → 12 戦目の 1 つだけの読みは対戦後の値"""
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        _battle_with_text(lg, 11, "loss", [1698.933, 1685.152])
        lg.on_frame(_st(12, scene="selection", rate=1685.152), [])
        f12 = lg._file
        lg.on_frame(_st(12, rate=1668.228), ["battle_end_rank"])
        outs = _rows(f12, "outcome")
        assert len(outs) == 1 and outs[0]["outcome"] == "loss" and outs[0]["basis_text"] == "レート 1685.2 → 1668.2", outs
        print("test_post_rate_known_from_text_battle_with_two_reads OK")
    finally:
        shutil.rmtree(tmp)


def test_opposite_direction_and_gap():
    tmp = Path(tempfile.mkdtemp())
    try:
        # 直前の対戦は文言で勝ち、この対戦の差は下向き → 直前の増減ではあり得ないので負け
        lg = BattleLogger(log_dir=tmp)
        _battle_with_text(lg, 1, "win", [1600.0])
        lg.on_frame(_st(2, scene="selection", rate=1600.0), [])
        f2 = lg._file
        lg.on_frame(_st(2, rate=1583.0), ["battle_end_rank"])
        outs = _rows(f2, "outcome")
        assert outs[0]["outcome"] == "loss" and outs[0]["basis"] == "rate", outs
        # 3 戦目はレートが読めず、4 戦目で 1 つ読めた → 間が空いたので帰属しない (3 戦目と 4 戦目の合計かもしれない)
        lg.on_frame(_st(3, scene="selection", rate=1583.0), [])
        lg.on_frame(_st(3, rate=1583.0), ["battle_end_rank"])
        lg.on_frame(_st(4, scene="selection", rate=1583.0), [])
        f4 = lg._file
        lg.on_frame(_st(4, rate=1601.0), ["battle_end_rank"])
        assert [r["outcome"] for r in _rows(f4, "outcome")] == ["unknown"]
        print("test_opposite_direction_and_gap OK")
    finally:
        shutil.rmtree(tmp)


def test_other_evidence_records_its_basis():
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp)
        lg.on_frame(_st(1), [])
        lg.on_frame(_st(1, my_f=1, opp_f=3), [])
        f1 = lg._file
        lg.on_frame(_st(1, my_f=1, opp_f=3), ["battle_end_rank"])
        o = _rows(f1, "outcome")[0]
        assert o["outcome"] == "win" and o["inferred"] is True and o["basis"] == "fainted" \
            and o["basis_text"] == "ひんしの数 自分 1 / 相手 3", o
        # 文言で確定した勝敗には推定の欄を付けない。後からレートが読めても動かさない
        lg.on_frame(_st(2, scene="selection"), [])
        f2 = _battle_with_text(lg, 2, "win", [1600.0, 1580.0])
        outs = _rows(f2, "outcome")
        assert outs == [dict(outs[0], outcome="win")] and "inferred" not in outs[0] and "basis" not in outs[0], outs
        assert lg.pop_revision() is None
        print("test_other_evidence_records_its_basis OK")
    finally:
        shutil.rmtree(tmp)


def test_reader_keeps_rank_screen_mark_across_revision_row():
    """推定の更新の行はランク画面の後に書かれる。その後の勝負の文言は次の対戦のもの (連結ログ) なので、訂正に使わない"""
    ot = OutcomeTracker()
    for d in [{"type": "outcome", "outcome": "unknown"},
              {"type": "events", "fired": ["battle_end_rank"]},
              {"type": "outcome", "outcome": "loss", "inferred": True, "basis": "rate", "revised_from": "unknown"},
              {"type": "events", "fired": ["battle_win"]}]:
        ot.feed(d)
    assert ot.result() == ("loss", True, False), ot.result()
    # 更新の行でなければ従来どおり (記録の後・ランク画面の前の文言は訂正の候補)
    assert outcome_from_records([{"type": "outcome", "outcome": "loss", "inferred": True},
                                 {"type": "events", "fired": ["battle_win"]}]) == ("win", False, True)
    print("test_reader_keeps_rank_screen_mark_across_revision_row OK")


def main() -> None:
    test_two_reads_in_one_battle_are_pre_and_post()
    test_single_read_is_attributed_only_when_unambiguous()
    test_previous_battles_delta_is_not_this_battles_outcome()
    test_late_post_rate_revises_the_outcome()
    test_post_rate_known_from_text_battle_with_two_reads()
    test_opposite_direction_and_gap()
    test_other_evidence_records_its_basis()
    test_reader_keeps_rank_screen_mark_across_revision_row()
    print("ALL OK")


if __name__ == "__main__":
    main()
