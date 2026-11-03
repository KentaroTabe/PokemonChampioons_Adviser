"""tests/test_sim_mega_form のうち poke-env が要る部分 (ローカル専用: scripts/run_test.sh の LOCAL_ONLY_TESTS。CI の最小依存 requirements-ci.txt に poke-env が無いため
scripts/ci_tests.sh から外した。2026-10-31)。

- 実物の poke-env の Battle (poke_env.battle.Battle にメッセージを流す) でのメガの記録とメガ後の形態 (3 本)
- tools.check_advisor_player --models-dir の子プロセス (--print-models-dir が相手の操縦 champions_agent.agent.policy_battle を
  import し、それが poke-env を読む)
偽オブジェクトでのメガ形態の判定・--models-dir の純粋な部分は tests/test_sim_mega_form (CI)。使用率 DB・RL・Showdown には依存しない
(子プロセスは対戦しない)。

    python -m tests.test_sim_mega_form_local
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from champions_agent.env import advisor_player as AP
from tests.test_scoring_transition import resolver

REPO = Path(__file__).resolve().parent.parent


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


def _real_battle():
    from poke_env.battle import Battle
    b = Battle("battle-mega-2", "me", logging.getLogger("test_sim_mega_form"), gen=9)
    b.player_role = "p1"
    return b


def _party(st, side):
    return [(m["species_id"], m["is_mega"]) for m in st[side]["party"]]


def test_opponent_mega_form_from_events():
    """相手のメガ後: poke-env の species は基本種のまま (detailschange / -mega は species を書き換えない) でも、状態の species_id は
    メガ形態・is_mega = True・形態の解決 (gimmick.resolve_form) の種族値がメガのもの。メガ前は基本種"""
    from advisor.dex import get_dex
    from advisor.gimmick import resolve_form
    b = _real_battle()
    b.parse_message(["", "switch", "p1a: Garchomp", "Garchomp, L50, F", "100/100"])
    b.parse_message(["", "switch", "p2a: Lopunny", "Lopunny, L50, F", "100/100"])
    st = AP.battle_to_state(b, resolver)
    assert _party(st, "opponent") == [("lopunny", False)], _party(st, "opponent")
    assert resolve_form(st["opponent"]["party"][0]).species == "lopunny"
    assert st["mega_used"] == {"player": False, "opponent": False}
    b.parse_message(["", "detailschange", "p2a: Lopunny", "Lopunny-Mega, L50, F"])
    b.parse_message(["", "-mega", "p2a: Lopunny", "Lopunny", "Lopunnite"])
    assert b.opponent_active_pokemon.species == "lopunny"        # poke-env 側は基本種のまま (この修正の前提)
    st = AP.battle_to_state(b, resolver)
    assert _party(st, "opponent") == [("lopunnymega", True)], _party(st, "opponent")
    form = resolve_form(st["opponent"]["party"][0])
    dex = get_dex()
    assert form.species == "lopunnymega" and form.is_mega
    assert dex.species(form.species)["baseStats"] == dex.species("lopunnymega")["baseStats"] != dex.species("lopunny")["baseStats"]
    assert st["mega_used"] == {"player": False, "opponent": True}
    # ターンをまたいでも (終わったターンの observations に入っても) 引ける
    b.parse_message(["", "turn", "2"])
    st = AP.battle_to_state(b, resolver)
    assert _party(st, "opponent") == [("lopunnymega", True)], _party(st, "opponent")
    # 交代で出し直すと poke-env の species もメガ形態 (details)。上書きは要らず同じ結果
    b.parse_message(["", "switch", "p2a: Meganium", "Meganium, L50, F", "100/100"])
    b.parse_message(["", "switch", "p2a: Lopunny", "Lopunny-Mega, L50, F", "80/100"])
    st = AP.battle_to_state(b, resolver)
    assert ("lopunnymega", True) in _party(st, "opponent") and ("meganium", False) in _party(st, "opponent"), _party(st, "opponent")
    print("test_opponent_mega_form_from_events OK")


def test_own_mega_form_from_events_and_stone():
    """自分のメガ後 (次の要求の details が来る前の決定) も記録からメガ形態。detailschange が無く -mega だけなら石から
    (charizardite x → charizardmegax)"""
    b = _real_battle()
    b.parse_message(["", "switch", "p1a: Charizard", "Charizard, L50, F", "100/100"])
    b.parse_message(["", "switch", "p2a: Yanmega", "Yanmega, L50, F", "100/100"])
    st = AP.battle_to_state(b, resolver)
    assert _party(st, "player") == [("charizard", False)] and _party(st, "opponent") == [("yanmega", False)]
    b.parse_message(["", "-mega", "p1a: Charizard", "Charizard", "Charizardite X"])
    st = AP.battle_to_state(b, resolver)
    assert _party(st, "player") == [("charizardmegax", True)], _party(st, "player")
    assert _party(st, "opponent") == [("yanmega", False)]
    assert st["mega_used"] == {"player": True, "opponent": False}, st["mega_used"]
    print("test_own_mega_form_from_events_and_stone OK")


def main():
    test_mega_used_real_poke_env_battle()
    test_models_dir_subprocess()
    test_opponent_mega_form_from_events()
    test_own_mega_form_from_events_and_stone()
    print("ALL OK")


if __name__ == "__main__":
    main()
