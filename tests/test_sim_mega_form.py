"""シミュレータの入力の誤り 2 件の修正 (2026-10-15 M3c、docs/ADVICE_DIAG_40_20261015.md §7) のテスト。

(1) メガ形態の判定 (追加: メガ後の個体の形態は対戦の記録の detailschange / -mega から): advisor-as-player (champions_agent.env.advisor_player) が種族 id に "mega" を含むかの部分一致で is_mega と
    mega_used を決めており、meganium (メガニウム) / yanmega (メガヤンマ) がメガ前からメガ扱い、その陣営の mega_used が対戦の
    最初から True だった。advisor.gimmick.is_mega_form (石の表のメガ後の種族 id) と poke-env のメガの記録に統一した。
(2) tools.check_advisor_player --models-dir: 以前は rl_bridge にしか効かず、相手の操縦 (rl) と選出の埋め込みは import 時の
    config.MODELS_DIR を読んでいた。import の前に環境変数 CHAMPIONS_MODELS_DIR を設定する。

poke-env の Battle は偽オブジェクト (tests/test_sim_legal_inputs と同じ作り) と、メガの記録の確認だけ実物 (poke_env.battle.Battle に
メッセージを流す)。使用率 DB・RL・Showdown には依存しない (--models-dir の確認は子プロセスで --print-models-dir、対戦しない)。
実物の poke-env の Battle を使う 3 本と --models-dir の子プロセスの確認 (相手の操縦のモジュールが poke-env を読む) は
tests/test_sim_mega_form_local (ローカル専用、CI の最小依存に poke-env が無いため。2026-10-31)。

    python -m tests.test_sim_mega_form
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from champions_agent.config import SIM_INPUTS_VERSION
from champions_agent.env import advisor_player as AP
from tests.test_scoring_transition import resolver
from tests.test_sim_legal_inputs import _battle
from tests.test_sim_picked_roster import _pmon

_MEGANIUM_OWN = [("Meganium", "meganium", ("gigadrain", "leechseed", "synthesis", "dazzlinggleam")),
                 ("Garchomp", "garchomp", ("earthquake", "dragonclaw", "swordsdance", "stoneedge")),
                 ("Lopunny", "lopunny", ("fakeout", "closecombat", "return", "uturn")),
                 ("Primarina", "primarina", ("moonblast", "surf", "icebeam", "calmmind")),
                 ("Kingambit", "kingambit", ("kowtowcleave", "suckerpunch", "ironhead", "swordsdance")),
                 ("Gholdengo", "gholdengo", ("makeitrain", "shadowball", "nastyplot", "recover"))]
_MEGANIUM_PICKED = ("Meganium", "Garchomp", "Lopunny")


def _state(active="Meganium", own_species=None, opp_species="yanmega", used=False, opp_used=False):
    """自分はメガニウム入りの 6 体、相手は 1 体 (既定 yanmega)。own_species: {名前: 種族 id} で種族を差し替える (メガ後)。
    used / opp_used: poke-env のメガの記録 (battle.used_mega_evolve / opponent_used_mega_evolve)"""
    b = _battle(active=active, own=_MEGANIUM_OWN, picked=_MEGANIUM_PICKED)
    for name, sid in (own_species or {}).items():
        b.team[f"p1: {name}"].species = sid
    opp = _pmon(opp_species, ("bugbuzz", "airslash"), hp=0.7, item="unknown_item")
    b.opponent_active_pokemon = opp
    b.opponent_team = {"p2: Opp": opp}
    b.used_mega_evolve = used
    b.opponent_used_mega_evolve = opp_used
    return AP.battle_to_state(b, resolver)


def _is_mega(st, side, sid):
    return [m["is_mega"] for m in st[side]["party"] if m["species_id"] == sid]


def test_mon_entry_mega_form():
    """is_mega: meganium / yanmega はメガ扱いしない、charizardmegax / lopunnymega はメガ扱い"""
    for sid, want in (("meganium", False), ("yanmega", False), ("charizardmegax", True), ("charizardmegay", True),
                      ("lopunnymega", True), ("meganiummega", True)):
        e = AP._mon_entry(_pmon(sid, ("tackle",)), own=False, resolver=resolver, active=False)
        assert e["is_mega"] is want, (sid, e["is_mega"])
    print("test_mon_entry_mega_form OK")


def test_battle_to_state_meganium_not_mega():
    """メガニウム (自分) とメガヤンマ (相手) を出しても、メガ前なら is_mega も mega_used も False"""
    st = _state()
    assert st["mega_used"] == {"player": False, "opponent": False}, st["mega_used"]
    assert _is_mega(st, "player", "meganium") == [False]
    assert _is_mega(st, "opponent", "yanmega") == [False]
    print("test_battle_to_state_meganium_not_mega OK")


def test_mega_used_only_after_mega():
    """mega_used はメガの後だけ True: 種族がメガ形態になった (自分: 要求の details で lopunnymega) か、poke-env の記録
    (-mega で立つ used_mega_evolve / opponent_used_mega_evolve。相手の種族は poke-env では基本種のまま)"""
    st = _state(active="Lopunny", own_species={"Lopunny": "lopunnymega"})
    assert st["mega_used"] == {"player": True, "opponent": False}, st["mega_used"]
    assert _is_mega(st, "player", "lopunnymega") == [True]
    st = _state(used=True)
    assert st["mega_used"] == {"player": True, "opponent": False}, st["mega_used"]
    st = _state(opp_used=True)
    assert st["mega_used"] == {"player": False, "opponent": True}, st["mega_used"]
    st = _state(opp_species="charizardmegax")
    assert st["mega_used"] == {"player": False, "opponent": True}, st["mega_used"]
    assert _is_mega(st, "opponent", "charizardmegax") == [True]
    print("test_mega_used_only_after_mega OK")


def test_inputs_version():
    # 2026-10-16 E-holdout-1 (蘇生先の選択) で版を更新 (レビュー承認)
    assert SIM_INPUTS_VERSION == "picked-legal-mega-revive-1016", SIM_INPUTS_VERSION
    print("test_inputs_version OK")


def test_models_dir_env_pure():
    """--models-dir → 環境変数の値 (resolve 済み) と警告。環境変数が違えば引数を優先して警告"""
    from tools import check_advisor_player as C
    with tempfile.TemporaryDirectory() as td:
        want = str(Path(td).resolve())
        assert C.models_dir_env(["--battles", "3"], {}) == (None, None)
        assert C.models_dir_env(["--battles", "3", "--models-dir", td], {}) == (want, None)
        assert C.models_dir_env([f"--models-dir={td}"], {C.MODELS_DIR_ENV: want}) == (want, None)
        value, warn = C.models_dir_env(["--models-dir", td], {C.MODELS_DIR_ENV: "/nonexistent/other"})
        assert value == want and warn and "引数を優先" in warn, (value, warn)
        env = {C.MODELS_DIR_ENV: "/nonexistent/other"}
        assert C.apply_models_dir_env(["--models-dir", td], env) == want and env[C.MODELS_DIR_ENV] == want
        env = {}
        assert C.apply_models_dir_env(["--battles", "3"], env) is None and env == {}
    print("test_models_dir_env_pure OK")


def test_opp_policy_info():
    """結果 JSON の相手の操縦の方策: 読めたか (loaded) と読んだファイル。方策を持たない操縦者は None"""
    from tools.check_advisor_player import opp_policy_info
    pol = NS(model=object(), model_path=Path("/x/battle_policy_balance_ema.zip"), stats={"random_fallback": 0})
    info = opp_policy_info(NS(policy=pol))
    assert info == {"model_path": "/x/battle_policy_balance_ema.zip", "loaded": True, "stats": {"random_fallback": 0}}
    info = opp_policy_info(NS(policy=NS(model=None, model_path=None, stats={"load_failed": 1})))
    assert info["loaded"] is False and info["model_path"] is None
    assert opp_policy_info(NS()) is None
    print("test_opp_policy_info OK")


def test_mega_forms_seen_pure():
    """記録 (終わったターン + 今のターン) から {"p1: 名前": メガ形態}。メガ形態でない detailschange は数えない"""
    obs = {1: NS(events=[["", "detailschange", "p2a: Lopunny", "Lopunny-Mega, L50, F"]]),
           2: NS(events=[["", "detailschange", "p1a: Zygarde", "Zygarde-Complete, L50"]])}
    cur = NS(events=[["", "-mega", "p1a: Charizard", "Charizard", "Charizardite Y"]])
    b = NS(observations=obs, current_observation=cur, player_role="p1")
    assert AP.mega_forms_seen(b) == {"p2: Lopunny": "lopunnymega", "p1: Charizard": "charizardmegay"}, AP.mega_forms_seen(b)
    assert AP.mega_forms_seen(NS()) == {}
    print("test_mega_forms_seen_pure OK")


if __name__ == "__main__":
    test_mon_entry_mega_form()
    test_battle_to_state_meganium_not_mega()
    test_mega_used_only_after_mega()
    test_inputs_version()
    test_models_dir_env_pure()
    test_opp_policy_info()
    test_mega_forms_seen_pure()
