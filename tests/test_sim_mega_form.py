"""シミュレータの入力の誤り 2 件の修正 (2026-10-15 M3c、docs/ADVICE_DIAG_40_20261015.md §7) のテスト。

(1) メガ形態の判定: advisor-as-player (champions_agent.env.advisor_player) が種族 id に "mega" を含むかの部分一致で is_mega と
    mega_used を決めており、meganium (メガニウム) / yanmega (メガヤンマ) がメガ前からメガ扱い、その陣営の mega_used が対戦の
    最初から True だった。advisor.gimmick.is_mega_form (石の表のメガ後の種族 id) と poke-env のメガの記録に統一した。
(2) tools.check_advisor_player --models-dir: 以前は rl_bridge にしか効かず、相手の操縦 (rl) と選出の埋め込みは import 時の
    config.MODELS_DIR を読んでいた。import の前に環境変数 CHAMPIONS_MODELS_DIR を設定する。

poke-env の Battle は偽オブジェクト (tests/test_sim_legal_inputs と同じ作り) と、メガの記録の確認だけ実物 (poke_env.battle.Battle に
メッセージを流す)。使用率 DB・RL・Showdown には依存しない (--models-dir の確認は子プロセスで --print-models-dir、対戦しない)。

    python -m tests.test_sim_mega_form
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from champions_agent.config import SIM_INPUTS_VERSION
from champions_agent.env import advisor_player as AP
from tests.test_scoring_transition import resolver
from tests.test_sim_legal_inputs import _battle
from tests.test_sim_picked_roster import _pmon

REPO = Path(__file__).resolve().parent.parent

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


def test_mega_used_real_poke_env_battle():
    """実物の poke-env Battle: メガニウムを出しただけでは False、-mega の後に True (自分・相手を別々に)"""
    from poke_env.battle import Battle
    b = Battle("battle-mega-1", "me", logging.getLogger("test_sim_mega_form"), gen=9)
    b.player_role = "p1"

    def used():
        return AP.mega_used_of(b, list(b.team.values()), list(b.opponent_team.values()))

    b.parse_message(["", "switch", "p1a: Meganium", "Meganium, L50, F", "100/100"])
    b.parse_message(["", "switch", "p2a: Yanmega", "Yanmega, L50, F", "100/100"])
    assert used() == {"player": False, "opponent": False}, used()
    b.parse_message(["", "switch", "p2a: Lopunny", "Lopunny, L50, F", "100/100"])
    b.parse_message(["", "detailschange", "p2a: Lopunny", "Lopunny-Mega, L50, F"])
    b.parse_message(["", "-mega", "p2a: Lopunny", "Lopunny", "Lopunnite"])
    assert used() == {"player": False, "opponent": True}, used()
    b.parse_message(["", "switch", "p1a: Charizard", "Charizard, L50, F", "100/100"])
    b.parse_message(["", "detailschange", "p1a: Charizard", "Charizard-Mega-X, L50, F"])
    b.parse_message(["", "-mega", "p1a: Charizard", "Charizard", "Charizardite X"])
    assert used() == {"player": True, "opponent": True}, used()
    print("test_mega_used_real_poke_env_battle OK")


def test_inputs_version():
    assert SIM_INPUTS_VERSION == "picked-legal-mega-1015", SIM_INPUTS_VERSION
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


def _print_models_dir(td: str, env_value=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CHAMPIONS_MODELS_DIR"}
    if env_value is not None:
        env["CHAMPIONS_MODELS_DIR"] = env_value
    res = subprocess.run([sys.executable, "-m", "tools.check_advisor_player", "--print-models-dir", "--models-dir", td],
                         cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("{")][-1]
    return {"report": json.loads(line), "stderr": res.stderr}


def test_models_dir_subprocess():
    """--models-dir だけで起動した子プロセスで、config.MODELS_DIR・rl_bridge・相手の操縦 (policy_battle)・選出の埋め込みが
    引数の dir を読む。環境変数が違う値でも引数を優先して警告する"""
    with tempfile.TemporaryDirectory() as td:
        want = str(Path(td).resolve())
        out = _print_models_dir(td)
        assert set(out["report"].values()) == {want}, out["report"]
        out = _print_models_dir(td, env_value="/nonexistent/other")
        assert set(out["report"].values()) == {want}, out["report"]
        assert "引数を優先" in out["stderr"], out["stderr"][-500:]
    print("test_models_dir_subprocess OK")


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


if __name__ == "__main__":
    test_mon_entry_mega_form()
    test_battle_to_state_meganium_not_mega()
    test_mega_used_only_after_mega()
    test_mega_used_real_poke_env_battle()
    test_inputs_version()
    test_models_dir_env_pure()
    test_models_dir_subprocess()
    test_opp_policy_info()
