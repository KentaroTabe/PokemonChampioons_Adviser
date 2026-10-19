"""選出の初手評価がメガ割当を反映するテスト (2026-10-19 レビュー)。

3 体組の評価はメガ割当先にメガ後の姿 (matrix_mega) を使うが、初手の行は全員に素の姿 (matrix) を
使っていた。初手と理由文の対面も 3 体組の評価と同じ割当で引くことを確かめる。

環境に依存しない (2026-10-19 修正): 本体 (使用率 DB・登録チーム・実戦バンクあり) で、登録のブリジュラスの
ステルスロックが先発の設置ボーナスに入り、ワークツリー (どれも無し) と結果が変わった。
advise_selection が読む外部入力は _Isolated で全部固定し、その間の SQLite 接続は記録して 0 件を確かめる:
  - 対面スコア (_species_matchup_score) … 表
  - 型予測の技 (_predicted_attack_moves、SetPredictor 経由で DB) … 空 (表で採点するので使わない)
  - 自分の攻撃プロファイル (_my_attack_profile、SetPredictor.predict → _connect で DB) … 空
    (タイプ相性の退避にだけ使う。表で採点するので使わない)。DB ファイルがあると選出の入口で毎回開いていた
  - 型予測そのもの (selection.get_predictor) … 予測なしの代用 (上 2 つ以外の経路が増えても DB に届かない)
  - タイプからの種族推測 (get_inference、TypeInference が DB) … 候補なしの代用 (相手は種族判明で呼ばれない)
  - 相手のメガ候補の混入 (_with_mega_candidates、使用率経由) … そのまま
  - 実戦バンクの相手スロットの重み (real_prior.slot_weights / species_pick_prior) … 均等・事前分布なし
  - 登録チーム (my_team.get_my_build / get_my_moves) … 空の登録 (tests.test_advice_m1a._Patch)、
    加えて登録参照の無効化の環境変数 (MY_TEAM_DISABLED_ENV) を立てる
  - 自分の特性 (_own_ability、登録・固定特性の表) … None (天候の相乗を入れない)
  - 設置技持ちの判定 (_own_hazard_setter、画面の技・登録の技) … 個体の "hazard" 指定だけ (既定 False)
  - 登録技 (_own_registered_moves) … 空 (use_registered=False でも呼ばれない側を固定)

    python -m tests.test_first_pick_mega
"""
from __future__ import annotations

import os
import sqlite3
import traceback

import advisor.real_prior as RP
import advisor.selection as S
from champions_agent.config import MY_TEAM_DISABLED_ENV
from tests.test_advice_m1a import _Patch


def _mon(species_id, species_ja, item_id=None):
    return {"species_id": species_id, "species_ja": species_ja,
            "item_id": item_id, "ability_id": None,
            "is_picked": False, "types": [], "moves": []}


def _opp(species_id, species_ja):
    return {"species_id": species_id, "species_ja": species_ja, "types": []}


OPPS = [_opp("corviknight", "アーマーガア"), _opp("kingambit", "ドドゲザン")]


class _NoInference:
    """TypeInference の代用 (DB を読まない)。候補なし"""

    def candidates(self, _types_ja, *a, **kw):
        return []


class _NoPredictor:
    """SetPredictor の代用 (DB を読まない)。予測技なし"""

    def predict(self, _sid, *a, **kw):
        return {"moves": [], "items": [], "abilities": [], "spreads": []}


class _Isolated:
    """advise_selection の外部入力を固定する (テストの間だけ)。table: {(自分の種族ID, 相手の種族ID): score}、表に無い対面は 0。
    SQLite の接続は開かせずに記録する (self.db_calls。0 件であることを呼び出し側で確かめる)"""

    def __init__(self, table):
        self.table = table
        self.db_calls = []

    def __enter__(self):
        table = self.table
        self.patches = [
            (S, "_species_matchup_score", lambda mv, _mm, cv, _cm: table.get((mv.species_id, cv.species_id), 0.0)),
            (S, "_predicted_attack_moves", lambda _sid, limit=6: []),
            (S, "_my_attack_profile", lambda _sid: []),
            (S, "get_predictor", lambda: _NoPredictor()),
            (S, "get_inference", lambda: _NoInference()),
            (S, "_with_mega_candidates", lambda cands, _p, _s: cands),
            (S, "_own_ability", lambda _p, _sid, is_mega=False, item_id="": None),
            (S, "_own_hazard_setter", lambda p: bool(p.get("hazard"))),
            (S, "_own_registered_moves", lambda _ja: []),
            (RP, "slot_weights", lambda priors: [1.0] * len(priors)),
            (RP, "species_pick_prior", lambda _sid: None),
            (sqlite3, "connect", self._no_db),
        ]
        self.saved = [(obj, name, getattr(obj, name)) for obj, name, _ in self.patches]
        for obj, name, value in self.patches:
            setattr(obj, name, value)
        self.env = os.environ.get(MY_TEAM_DISABLED_ENV)
        os.environ[MY_TEAM_DISABLED_ENV] = "1"
        self.team = _Patch(builds={}, moves={})
        self.team.__enter__()
        return self

    def _no_db(self, database, *a, **kw):
        # 呼び出し元のスタックも残す (呼び出し側が例外を握りつぶして続行するため、どこから開いたかを assert で示す)
        self.db_calls.append(f"{database}\n" + "".join(traceback.format_stack(limit=8)[:-1]))
        raise sqlite3.OperationalError("test_first_pick_mega: DB は読まない")

    def __exit__(self, *a):
        self.team.__exit__(*a)
        if self.env is None:
            os.environ.pop(MY_TEAM_DISABLED_ENV, None)
        else:
            os.environ[MY_TEAM_DISABLED_ENV] = self.env
        for obj, name, value in self.saved:
            setattr(obj, name, value)


def _run(party, table, opps=None):
    """外部入力を固定して advise_selection を回す (登録は使わない)。DB への接続が 1 件でもあれば失敗"""
    state = {"selection_picked": 0,
             "player": {"party": party},
             "opponent": {"party": opps or OPPS}}
    with _Isolated(table) as iso:
        advice = S.advise_selection(state, use_registered=False)
    assert iso.db_calls == [], "DB を開こうとした:\n" + "\n".join(iso.db_calls)
    return advice


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


def test_environment_inputs_ignored():
    """本体で落ちた経路の再現: 登録にブリジュラスのステルスロック (と全員にあめふらし) がある環境でも、
    _Isolated の下では登録を読まず、(a) と同じ結果になる。登録参照の無効化の環境変数も外した状態で確かめる"""
    import advisor.my_team as MT
    hostile = {ja: {"技": ["ステルスロック"], "特性": "あめふらし"}
               for ja in ("ラグラージ", "ミミッキュ", "ブリジュラス", "ロトム", "カイリュー", "メタグロス")}
    saved = (MT._load, MT._load_file, os.environ.get(MY_TEAM_DISABLED_ENV))
    MT._load = MT._load_file = lambda: hostile
    os.environ.pop(MY_TEAM_DISABLED_ENV, None)
    try:
        # 固定しなければ登録の技で設置役と判定される (本体での失敗の原因)
        assert S._own_hazard_setter(_mon("duraludon", "ブリジュラス")) is True
        advice = _run(_party_swap("leftovers"), _table(ROWS_SWAP))
    finally:
        MT._load, MT._load_file = saved[0], saved[1]
        if saved[2] is not None:
            os.environ[MY_TEAM_DISABLED_ENV] = saved[2]
    assert _lead(advice) == "ミミッキュ", advice["recommend"]
    assert _picks(advice) == sorted(["ミミッキュ", "ブリジュラス", "ロトム"]), advice["recommend"]
    assert advice["synergy"] is None, advice["synergy"]
    print("test_environment_inputs_ignored OK")


if __name__ == "__main__":
    test_non_mega_unchanged()
    test_lead_uses_mega_row_for_assignee()
    test_only_assignee_uses_mega_row()
    test_unknown_mega_form_falls_back()
    test_environment_inputs_ignored()
    print("ALL OK")
