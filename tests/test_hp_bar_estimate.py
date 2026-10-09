"""自分の HP のバー推定 (2026-10-09 ユーザー判断、KNOWN_ISSUES A1 の (3) 分母の OCR 落ち) の検証。

自分の HUD の分数が読めない ('13723'、'167'、'1595') か、読めても照合で捨てたフレームが続くとき、名前が場の個体と一致し、
最大 HP が既知で、バーの割合が連続 HP_BAR_ESTIMATE_STABLE_FRAMES 枚そろっていれば、既知の最大 HP × バーの割合を
推定値 (hp_estimated / hp_uncertain / hp_source="bar") として入れる。分数の実測が照合を通れば置き換えて印を消す。
推定だけではひんしを確定しない。

画像は tests/fixtures/hp_estimate/ の切り出し (原寸。manifest.json の offset で元の解像度の黒い画面に貼り戻す)。
frame_1791503228 (10/9 08:47:08): オオニューラ 4/159 が 100% のまま取りこぼされた例。バーの割合は約 4.4% (→ 約 7/159)。

使い方: python -m tests.test_hp_bar_estimate
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from champions_agent.config import HP_BAR_ESTIMATE_STABLE_FRAMES, HP_BAR_ESTIMATE_STABLE_TOL
from vision import my_hp_estimate as E
from vision import ocr, zones
from vision.state import BattleStateV2, PokemonState
from vision.zones import crop

FIX = Path(__file__).resolve().parent / "fixtures" / "hp_estimate"
NAME = "オオニューラ"
# 実戦で観測した、分数として使えない読み (10/9 の 2 戦: 分母や区切りの OCR 落ち)。
# '13723' と '167' は分数として読めない (fraction_unparsable)、'1595' は 15/95 と読めて最大 HP の照合で捨てる
BAD_TEXTS = ("13723", "167", "1595", "13723", "167", "1595")


def _frame(name: str = "my_low_4of159.png"):
    """切り出しを元の解像度の黒い画面の元の位置に貼り戻す (ゾーンの相対座標を元のフレームと同じにする)"""
    meta = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))[name]
    part = cv2.imread(str(FIX / name))
    assert part is not None, name
    w, h = meta["frame_size"]
    x0, y0 = meta["offset"]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = part
    return img


def _frame_empty_bar():
    """同じ HUD でバーの塗りを消したフレーム (バーの割合 0: ひんしの疑いの場面の代わり)"""
    img = _frame().copy()
    z = zones.BATTLE["my_hp_bar_track"]
    h, w = img.shape[:2]
    x0, y0, x1, y1 = int(z["x0"] * w), int(z["y0"] * h), int(z["x1"] * w), int(z["y1"] * h)
    img[y0 - 4:y1 + 4, x0 - 4:x1 + 4] = (40, 30, 30)
    return img


def _run(img, st, hp_text, expected_max=159, name_text=NAME, settle=True):
    """OCR の文字と型登録の理論値をモックして extract_my_hud を 1 回実行する (バーの割合は画像から実際に測る)。
    settle=True なら実行後に 600ms の安定条件を満たしたことにする (tests/test_hp_intake と同じ手当て)"""
    from vision import extractors
    from vision.normalize import NameResolver
    orig = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes

    def fake_read(_img, zone, **kw):
        if zone is zones.BATTLE["my_name"]:
            return name_text
        if zone is zones.BATTLE["my_hp_text"]:
            return hp_text
        return ""

    ocr.read_zone_text = fake_read
    extractors._expected_my_max = lambda m: expected_max
    extractors._my_legal_maxes = lambda: None
    try:
        extractors.extract_my_hud(img, st, _resolver(), bar_zone=None)
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = orig
    if settle:
        st.player.active()._hp_stable_since = 0.0


_RES = None


def _resolver():
    global _RES
    if _RES is None:
        from vision.normalize import NameResolver
        _RES = NameResolver()
    return _RES


def _state_with_me(ja=NAME, sid="sneasler", hp=100.0):
    st = BattleStateV2()
    st.battle_active = True
    mon = PokemonState(species_ja=ja, species_id=sid, display_name=ja)
    mon.hp_percent = hp
    st.player.party.append(mon)
    st.player.active_index = 0
    return st, mon


def _saved_counts():
    from vision import state as state_mod
    return dict(state_mod.HP_REJECT_COUNTS)


def _restore_counts(saved):
    from vision import state as state_mod
    state_mod.HP_REJECT_COUNTS.clear()
    state_mod.HP_REJECT_COUNTS.update(saved)


# ------------------------------------------------------------------ 純粋な計算
def test_pure_rules():
    """条件・安定判定・推定値の作り方 (純粋)"""
    assert HP_BAR_ESTIMATE_STABLE_FRAMES == 3 and HP_BAR_ESTIMATE_STABLE_TOL == 0.03
    ok = dict(species_known=True, name_matches=True, known_max=159, fraction_unusable=True, fainted=False, bar=0.044)
    assert E.estimate_block_reason(**ok) is None
    assert E.estimate_block_reason(**dict(ok, species_known=False)) == E.BLOCK_NO_SPECIES
    assert E.estimate_block_reason(**dict(ok, name_matches=False)) == E.BLOCK_NAME
    assert E.estimate_block_reason(**dict(ok, known_max=None)) == E.BLOCK_MAX_UNKNOWN
    assert E.estimate_block_reason(**dict(ok, fraction_unusable=False)) == E.BLOCK_FRACTION_OK
    assert E.estimate_block_reason(**dict(ok, fainted=True)) == E.BLOCK_FAINTED
    assert E.estimate_block_reason(**dict(ok, bar=None)) == E.BLOCK_NO_BAR

    t = E.push_bar(None, 0.5)
    t = E.push_bar(t, 0.51)
    assert E.stable_bar(t) is None                      # 2 枚ではそろわない
    t = E.push_bar(t, 0.52)
    assert abs(E.stable_bar(t) - 0.51) < 1e-9           # 3 枚の差 0.02 <= 0.03: 中央値
    t = E.push_bar(t, 0.60)
    assert len(t) == 3 and E.stable_bar(t) is None      # 差 0.09: 不安定 (古いものは捨てる)
    assert E.stable_bar([0.40, 0.43, 0.41]) is not None and E.stable_bar([0.40, 0.44, 0.41]) is None

    est = E.bar_estimate(0.0444, 159)
    assert est == {"pct": 4.4, "cur": 7, "max": 159, "bar": 0.044, "faint_suspect": False}, est
    z = E.bar_estimate(0.0, 159)
    assert z["cur"] == 1 and z["pct"] == 0.6 and z["faint_suspect"] is True, z   # 推定でひんしにしない (1 HP が下限)
    assert E.bar_estimate(1.0, 137)["cur"] == 137
    print("test_pure_rules OK")


def test_fixture_bar_ratio():
    """fixture のバーの割合は約 4.4% (正解 4/159 = 2.5% とは数 % ずれる。照合の許容内)"""
    from vision.extractors import my_bar_agrees
    bar = ocr.hp_bar_ratio(crop(_frame(), zones.BATTLE["my_hp_bar_track"]))
    assert bar is not None and 0.035 <= bar <= 0.055, bar
    assert my_bar_agrees(4, 159, bar)
    assert ocr.hp_bar_ratio(crop(_frame_empty_bar(), zones.BATTLE["my_hp_bar_track"])) == 0.0
    print("test_fixture_bar_ratio OK", round(float(bar), 4))


# ------------------------------------------------------------------ extract_my_hud の経路
def test_estimate_after_stable_bar_then_replaced_by_reading():
    """(a) 分数が読めない状態が続き、バーが安定したら推定 (約 7/159、印つき) が入る。
    (d) 安定前 (2 フレーム) では入らない。(b) その後に正しい分数 4/159 が読めたら置き換わり、印が消える"""
    saved = _saved_counts()
    try:
        img = _frame()
        st, mon = _state_with_me()
        # (d) 2 フレームでは入らない
        for txt in BAD_TEXTS[:2]:
            _run(img, st, txt)
        assert mon.hp_percent == 100.0 and not mon.hp_estimated, (mon.hp_percent, mon.hp_estimated)
        assert len(mon._my_bar_track) == 2
        # 3 フレーム目で列がそろい、_set_hp の従来の安定条件 (2 回の同じ値 + 600ms) を経て確定する
        for txt in BAD_TEXTS[2:4]:
            _run(img, st, txt)
        assert mon.hp_estimated is True and mon.hp_uncertain is True and mon.hp_source == "bar", vars(mon)
        assert (mon.hp_current, mon.hp_max) == (7, 159), (mon.hp_current, mon.hp_max)
        assert abs(mon.hp_percent - 4.4) < 0.15, mon.hp_percent
        assert mon.hp_read_ts is not None and mon.status != "fainted"
        rej = st.hp_reject["player"]
        assert rej["estimated_from_bar"] is True and rej["estimate"] == [7, 159], rej
        assert "faint_suspect" not in rej, rej
        ev = [e for e in st.events if e["source"] == "hp"]
        assert ev and ev[-1]["detail"]["source"] == "bar" and ev[-1]["detail"]["estimated"] is True, ev
        assert ev[-1]["detail"]["to"] == mon.hp_percent, ev[-1]
        # 推定が続く間は値を保つ (読めないフレームの続き)
        _run(img, st, BAD_TEXTS[4])
        assert mon.hp_estimated and mon.hp_current == 7

        # (b) 正しい分数が読めた: 実読みの安定条件 (従来の規則) を満たした時点で置き換え、印を消す
        _run(img, st, "4/159")
        assert mon.hp_estimated is True and mon.hp_current == 7, "推定の続きで実読みが 1 回では確定しない"
        assert mon._my_bar_track == []
        # 4/159 = 2.5% は 3% 以下なので、従来の規則で 3 回の同じ読みが要る (空バーの誤読の防御)
        _run(img, st, "4/159")
        assert mon.hp_estimated is True and mon.hp_current == 7
        _run(img, st, "4/159")
        assert (mon.hp_current, mon.hp_max) == (4, 159), (mon.hp_current, mon.hp_max)
        assert abs(mon.hp_percent - 2.5) < 0.1, mon.hp_percent
        assert mon.hp_estimated is False and mon.hp_uncertain is False and mon.hp_source is None
        json.dumps(st.to_dict())   # 対戦ログ (json.dumps、default なし) に書ける型であること
    finally:
        _restore_counts(saved)
    print("test_estimate_after_stable_bar_then_replaced_by_reading OK")


def test_empty_bar_is_not_faint():
    """(c) バーが 0 でも推定では fainted にならない (1 HP が下限、ひんしの疑いの印だけ)"""
    saved = _saved_counts()
    try:
        img = _frame_empty_bar()
        st, mon = _state_with_me(hp=30.0)
        mon.hp_current, mon.hp_max = 48, 159
        for txt in BAD_TEXTS:
            _run(img, st, txt)
        assert mon.status != "fainted", mon.status
        assert mon.hp_estimated and mon.hp_source == "bar", vars(mon)
        assert mon.hp_current == 1 and mon.hp_percent > 0, (mon.hp_current, mon.hp_percent)
        rej = st.hp_reject["player"]
        assert rej["estimated_from_bar"] is True and rej["faint_suspect"] is True, rej
        # ひんしの文言の裏付けが無いので、ほぼ 0% への低下のイベントは出ない (従来の規則)
        assert not [e for e in st.events if e["source"] == "hp"], st.events
        # 助言はひんしとみなさない (engine の my_fainted は status か hp_percent <= 0)
        assert not (mon.status == "fainted" or mon.hp_percent <= 0)
    finally:
        _restore_counts(saved)
    print("test_empty_bar_is_not_faint OK")


def test_no_estimate_without_correspondence():
    """(e) 名前が一致しない / 読めない、最大 HP が不明、ひんし確定済みでは入らない"""
    saved = _saved_counts()
    try:
        img = _frame()
        # 名前が別の種族: ロスター (6 体) 外の名前は adopt_my_hud_species が無視する → 場の個体と対応が取れない
        st, mon = _state_with_me()
        for ja, sid in (("ブリジュラス", "archaludon"), ("ボーマンダ", "salamence"), ("カメックス", "blastoise"),
                        ("ミミッキュ", "mimikyu"), ("サザンドラ", "hydreigon")):
            st.player.party.append(PokemonState(species_ja=ja, species_id=sid, display_name=ja))
        for txt in BAD_TEXTS:
            _run(img, st, txt, name_text="イエッサン")
        assert st.player.active_index == 0 and mon.species_ja == NAME
        assert mon.hp_percent == 100.0 and not mon.hp_estimated, vars(mon)
        # 名前が種族に解決できない (ニックネーム・崩れた読み)
        st, mon = _state_with_me()
        for txt in BAD_TEXTS:
            _run(img, st, txt, name_text="ヌヌヌヌ")
        assert mon.hp_percent == 100.0 and not mon.hp_estimated, vars(mon)
        # 名前が読めない
        st, mon = _state_with_me()
        for txt in BAD_TEXTS:
            _run(img, st, txt, name_text="")
        assert mon.hp_percent == 100.0 and not mon.hp_estimated
        # 名前が途中で一度読めない: 列は数え直す (連続 N フレームの条件)
        st, mon = _state_with_me()
        for i, txt in enumerate(BAD_TEXTS[:4]):
            _run(img, st, txt, name_text="" if i == 2 else NAME)
        assert mon.hp_percent == 100.0 and not mon.hp_estimated and len(mon._my_bar_track) == 1
        # 最大 HP が不明 (型登録の理論値なし・過去の実測なし)
        st, mon = _state_with_me()
        for txt in BAD_TEXTS:
            _run(img, st, txt, expected_max=None)
        assert mon.hp_percent == 100.0 and not mon.hp_estimated and mon.hp_max is None
        # 種族が未特定
        st, mon = _state_with_me(ja=None, sid=None)
        mon.display_name = None
        for txt in BAD_TEXTS:
            _run(img, st, txt, name_text="")
        assert not mon.hp_estimated
        # 過去に確定した実測の最大 HP があれば、型登録が無くても推定する
        st, mon = _state_with_me()
        mon.hp_current, mon.hp_max = 159, 159
        for txt in BAD_TEXTS[:4]:
            _run(img, st, txt, expected_max=None)
        assert mon.hp_estimated and (mon.hp_current, mon.hp_max) == (7, 159), vars(mon)
        # ひんし確定済み
        st, mon = _state_with_me(hp=0.0)
        mon.status = "fainted"
        for txt in BAD_TEXTS:
            _run(img, st, txt)
        assert mon.hp_percent == 0.0 and not mon.hp_estimated
        # 文字が無いフレーム (HUD の数字が出ていない) は推定の材料にしない
        st, mon = _state_with_me()
        for _ in range(6):
            _run(img, st, "")
        assert mon.hp_percent == 100.0 and not mon.hp_estimated
    finally:
        _restore_counts(saved)
    print("test_no_estimate_without_correspondence OK")


def test_unstable_bar_not_estimated():
    """バーの割合が許容 (0.03) を超えて揺れる間は入らない (被弾演出でバーが減っていく途中など)"""
    from vision import extractors
    saved = _saved_counts()
    orig = ocr.hp_bar_ratio
    seq = iter([0.80, 0.70, 0.60, 0.50, 0.40, 0.30])
    try:
        ocr.hp_bar_ratio = lambda _img: next(seq)
        st, mon = _state_with_me()
        for txt in BAD_TEXTS:
            _run(_frame(), st, txt)
        assert mon.hp_percent == 100.0 and not mon.hp_estimated, vars(mon)
        assert len(mon._my_bar_track) == 3   # 材料の列は残るが、差が許容を超えるのでそろわない
    finally:
        ocr.hp_bar_ratio = orig
        _restore_counts(saved)
    print("test_unstable_bar_not_estimated OK")


def test_set_hp_marks_kept_until_real_reading():
    """_set_hp: 推定で確定したときは印を立てたまま、実読みで確定したときだけ解除する"""
    from vision.extractors import _set_hp
    st, mon = _state_with_me()
    for _ in range(2):
        _set_hp(st, "player", mon, pct=50.3, cur=80, mx=159, estimated=True, source="bar")
        mon._hp_stable_since = 0.0
    assert mon.hp_percent == 50.3 and mon.hp_estimated and mon.hp_uncertain and mon.hp_source == "bar"
    # 推定の値が同じでも続けて印は残る
    _set_hp(st, "player", mon, pct=50.3, cur=80, mx=159, estimated=True, source="bar")
    assert mon.hp_estimated and mon.hp_uncertain
    # 推定の値は最大 HP の多数決に入れない
    assert not st.hp_max_votes, st.hp_max_votes
    # 実読み (分数) で確定すると解除
    for _ in range(2):
        _set_hp(st, "player", mon, cur=79, mx=159)
        mon._hp_stable_since = 0.0
    assert (mon.hp_current, mon.hp_max) == (79, 159) and not mon.hp_estimated and not mon.hp_uncertain
    assert mon.hp_source is None
    print("test_set_hp_marks_kept_until_real_reading OK")


# ------------------------------------------------------------------ 記録・助言
def test_log_rows_carry_source():
    """(f) hp 行に source / estimated、scene 行の state の個体に hp_estimated / hp_source、advice 行に context が残る。
    助言の行の state と digest には推定の印を足さない"""
    import battle_logger as BL
    from battle_logger import BattleLogger
    saved = _saved_counts()
    tmp = Path(tempfile.mkdtemp())
    try:
        img = _frame()
        st, mon = _state_with_me()
        st.scene = "command"
        st.opponent.party.append(PokemonState(species_ja="ガブリアス", species_id="garchomp", hp_percent=100.0))
        st.opponent.active_index = 0
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=None)
        lg.on_frame(st.to_dict(), [])
        for txt in BAD_TEXTS[:4]:
            _run(img, st, txt)
        assert mon.hp_estimated
        d = st.to_dict()
        assert d["player"]["party"][0]["hp_source"] == "bar" and d["player"]["party"][0]["hp_estimated"] is True
        st.scene = "battle_hud"
        lg.on_frame(st.to_dict(), [])
        aid = lg.on_advice({"ok": True, "best": None, "actions": []}, "battle", st.to_dict())
        assert aid
        # 実読みで置き換えた後の助言には context を付けない
        for _ in range(3):
            _run(img, st, "4/159")
        assert not mon.hp_estimated
        lg.on_advice({"ok": True, "best": None, "actions": []}, "battle", st.to_dict())
        lg.close()
        rows = [json.loads(line) for f in sorted(tmp.glob("*.jsonl"))
                for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        hp_rows = [r for r in rows if r["type"] == "hp"]
        assert hp_rows and hp_rows[0]["source"] == "bar" and hp_rows[0]["estimated"] is True, hp_rows
        assert hp_rows[0]["detail"]["estimated"] is True
        # 実読みの hp 行には印を付けない (既存の行の形のまま)
        assert all("estimated" not in r for r in hp_rows[1:]), hp_rows
        scenes = [r for r in rows if r["type"] == "scene"]
        first, after = scenes[0]["state"]["player"]["party"][0], scenes[-1]["state"]["player"]["party"][0]
        assert "hp_estimated" not in first and "hp_source" not in first, first
        assert after["hp_estimated"] is True and after["hp_source"] == "bar", after
        assert scenes[-1]["state"]["hp_reject"]["player"]["estimated_from_bar"] is True
        adv = [r for r in rows if r["type"] == "advice"]
        assert adv[0]["context"] == {"my_hp_estimated": True, "my_hp_source": "bar"}, adv[0]
        assert "context" not in adv[1], adv[1]
        assert "hp_estimated" not in adv[0]["state"]["player"]["party"][0], "助言の行の state は変えない"
        assert BL.my_hp_context_of(None) == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _restore_counts(saved)
    print("test_log_rows_carry_source OK")


def _mini_state(my_mon, opp_mon, bench=None):
    haz = {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False}
    scr = {"reflect": False, "light_screen": False, "aurora_veil": False}
    return {"field": {"weather": None, "terrain": None, "trick_room": False},
            "mega_used": {"player": False, "opponent": False},
            "player": {"active_index": 0, "tailwind": False, "hazards": dict(haz), "screens": dict(scr),
                       "party": [my_mon] + ([bench] if bench else [])},
            "opponent": {"active_index": 0, "tailwind": False, "hazards": dict(haz), "screens": dict(scr),
                         "party": [opp_mon]}}


def _mon(sid, ja, types, hp, cur, mx, moves=None, **kw):
    d = {"species_id": sid, "species_ja": ja, "types": types, "hp_percent": hp, "hp_current": cur, "hp_max": mx,
         "status": None, "boosts": {}, "ability_id": None, "item_id": None, "moves": moves or [], "revealed_moves": []}
    d.update(kw)
    return d


def test_engine_context_and_uncertain_penalty():
    """助言: 自分の HP が推定なら context に my_hp_estimated を残す (場の個体の採点はそのまま)。控えへの交代は既存の減点規則を
    維持する (2026-10-09 ユーザー判断 B): バー推定の控えも減点 15 の対象で、警告の文言は「バーからの概算」。
    交代の見逃しの控えは従来どおり減点され、文言も従来の「HP不明 (交代の見逃し…)」"""
    from advisor import engine
    from vision.normalize import NameResolver
    res = NameResolver()
    mv = [{"name_ja": "じゃれつく", "move_id": "playrough", "pp": 10, "max_pp": 10, "effectiveness": "neutral"}]
    me = _mon("mimikyu", "ミミッキュ", ["ゴースト", "フェアリー"], 4.4, 6, 131, mv)
    opp = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], 100.0, None, None)
    bench = _mon("hydreigon", "サザンドラ", ["あく", "ドラゴン"], 50.3, 85, 169)

    base = engine.evaluate_common(_mini_state(dict(me), dict(opp), dict(bench)), res)
    assert base["ok"] and "my_hp_estimated" not in base["context"], base["context"]
    est = engine.evaluate_common(_mini_state(dict(me, hp_estimated=True, hp_uncertain=True, hp_source="bar"),
                                             dict(opp), dict(bench)), res)
    assert est["context"]["my_hp_estimated"] is True and est["context"]["my_hp_source"] == "bar", est["context"]
    assert [(a["kind"], a["id"], a["score"]) for a in est["actions"]] == \
        [(a["kind"], a["id"], a["score"]) for a in base["actions"]]

    assert engine.UNCERTAIN_EXEMPT_BAR_ESTIMATE is False
    # 控えがバー推定の値 (hp_uncertain + hp_source="bar"): 減点 15、文言は「バーからの概算」
    b_est = engine.evaluate(_mini_state(dict(me), dict(opp),
                                        dict(bench, hp_estimated=True, hp_uncertain=True, hp_source="bar")), res)
    b_base = engine.evaluate(_mini_state(dict(me), dict(opp), dict(bench)), res)
    sw_e = next(a for a in b_est["actions"] if a["kind"] == "switch")
    sw_b = next(a for a in b_base["actions"] if a["kind"] == "switch")
    assert "バーからの概算" in sw_e["reason"] and "HP不明" not in sw_e["reason"], sw_e
    assert abs(sw_b["score"] - sw_e["score"] - engine.UNCERTAIN_SWITCH_PENALTY) < 1e-6, (sw_e, sw_b)
    # 交代の見逃し (hp_source なし) は従来どおり減点 15、文言も従来の「HP不明 (交代の見逃し…)」
    b_miss = engine.evaluate(_mini_state(dict(me), dict(opp), dict(bench, hp_uncertain=True)), res)
    sw_m = next(a for a in b_miss["actions"] if a["kind"] == "switch")
    assert "HP不明 (交代の見逃しあり" in sw_m["reason"] and "バーからの概算" not in sw_m["reason"], sw_m
    assert abs(sw_b["score"] - sw_m["score"] - engine.UNCERTAIN_SWITCH_PENALTY) < 1e-6, (sw_m, sw_b)
    print("test_engine_context_and_uncertain_penalty OK")


def test_missed_switch_overrides_bar_source():
    """バー推定の値を持つ個体で交代の見逃しが起きたら、hp_source を外して「不明」に戻す (減点の警告の文言が交代の見逃しになる)"""
    from vision.extractors import adopt_my_hud_species
    st = BattleStateV2()
    a = PokemonState(species_ja=NAME, species_id="sneasler", display_name=NAME, hp_percent=4.4,
                     hp_estimated=True, hp_uncertain=True, hp_source="bar")
    b = PokemonState(species_ja="イエッサン", species_id="indeedeef", display_name="イエッサン", hp_percent=100.0)
    st.player.party.extend([a, b])
    st.player.active_index = 0
    adopt_my_hud_species(st, ("イエッサン", "indeedeef"))
    assert st.player.active_index == 1 and a.hp_uncertain is True and a.hp_source is None
    print("test_missed_switch_overrides_bar_source OK")


def test_restore_and_merge_keep_source():
    """保存 (to_dict) → 復元 (restore_from_dict) で推定の印と出所が残る。対応待ちの個体を枠へ統合すると、推定の印と一緒に
    出所も移る (SideState.merge_pending_into)"""
    from champions_agent.config import PARTY_SIZE
    st, mon = _state_with_me()
    mon.hp_percent, mon.hp_current, mon.hp_max = 4.4, 7, 159
    mon.hp_estimated, mon.hp_uncertain, mon.hp_source, mon.hp_read_ts = True, True, "bar", 123.0
    d = json.loads(json.dumps(st.to_dict()))
    st2 = BattleStateV2()
    st2.restore_from_dict(d)
    m2 = st2.player.party[0]
    assert (m2.hp_estimated, m2.hp_uncertain, m2.hp_source, m2.hp_read_ts) == (True, True, "bar", 123.0), vars(m2)
    assert (m2.hp_current, m2.hp_max) == (7, 159)
    # 推定でない個体は出所なしのまま復元される (古い形の state: hp_source の欄が無くても読める)
    d["player"]["party"][0].pop("hp_source")
    d["player"]["party"][0]["hp_estimated"] = False
    st3 = BattleStateV2()
    st3.restore_from_dict(d)
    assert st3.player.party[0].hp_source is None and st3.player.party[0].hp_estimated is False

    # 統合: 末尾 (PARTY_SIZE 以降) の対応待ちの個体 → 枠 2
    side = BattleStateV2().opponent
    for i in range(PARTY_SIZE):
        side.party.append(PokemonState(species_ja=f"枠{i}", species_id=f"slot{i}"))
    src = PokemonState(species_ja=NAME, species_id="sneasler", hp_percent=4.4, hp_estimated=True,
                       hp_uncertain=True, hp_source="bar", pending=True)
    side.party.append(src)
    side.active_index = PARTY_SIZE
    side.merge_pending_into(PARTY_SIZE, 2, None)
    dst = side.party[2]
    assert len(side.party) == PARTY_SIZE and side.active_index == 2
    assert (dst.hp_percent, dst.hp_estimated, dst.hp_uncertain, dst.hp_source) == (4.4, True, True, "bar"), vars(dst)
    # 推定でない個体の統合では、枠の出所を書き換えない
    side2 = BattleStateV2().opponent
    for i in range(PARTY_SIZE):
        side2.party.append(PokemonState(species_ja=f"枠{i}", species_id=f"slot{i}"))
    side2.party.append(PokemonState(species_ja=NAME, species_id="sneasler", hp_percent=50.0, pending=True))
    side2.merge_pending_into(PARTY_SIZE, 1, None)
    assert side2.party[1].hp_source is None and side2.party[1].hp_estimated is False
    print("test_restore_and_merge_keep_source OK")


def test_max_hp_adoption_guards():
    """最大 HP の実測採用 (extract_my_hud の MY_MAX_ADOPT_READS、_my_max_adopted) の受入前の対処 (2026-10-09):
    桁分割で推測した分数 ('1595' → 15/95) と、種族の物理可能域に無い最大 HP は採用の数え上げに入れない。
    '/' で区切られた正しい読み (登録の理論値 161 と実測 181 が違う) は従来どおり採用する"""
    from tests.test_hp_intake import _frame as intake_frame
    from vision.extractors import MY_MAX_ADOPT_READS
    assert ocr.fraction_is_split_guess("1595") is True and ocr.parse_fraction("1595") == (15, 95)
    assert ocr.fraction_is_split_guess("4/159") is False
    assert ocr.fraction_is_split_guess("15/95") is False
    for txt in ("167", "13723", "", None):        # 分数として読めない
        assert ocr.fraction_is_split_guess(txt) is False, txt
    saved = _saved_counts()
    try:
        img = _frame()   # バー 0.044: 15/95 (0.158) とは照合の許容 (0.15) 内
        # (b) 桁分割の推測は数えない (種族未特定で物理可能域の検査が効かない場合でも)
        st, mon = _state_with_me(ja=None, sid=None)
        mon.display_name = None
        for _ in range(MY_MAX_ADOPT_READS + 2):
            _run(img, st, "1595", name_text="")
        assert getattr(mon, "_my_max_adopted", None) is None, mon._my_max_adopted
        assert mon._max_adopt_counts.get(95) == 0, mon._max_adopt_counts
        assert st.hp_reject["player"]["reason"] == "max_hp_mismatch"
        # (a) '/' のある読みでも、種族の物理可能域 (オオニューラ) に無い最大 HP 95 は数えない
        st, mon = _state_with_me()
        for _ in range(MY_MAX_ADOPT_READS + 2):
            _run(img, st, "15/95")
        assert getattr(mon, "_my_max_adopted", None) is None and mon._max_adopt_counts.get(95) == 0
        # 従来どおり: 登録の理論値 161 と違う実測 181/181 (満タンのバー) が続けば採用する
        full = intake_frame("my_full_137of137.png")
        st, mon = _state_with_me(ja="ムクホーク", sid="staraptor")
        for _ in range(MY_MAX_ADOPT_READS):
            _run(full, st, "181/181", expected_max=161, name_text="")
        assert mon._my_max_adopted == 181, getattr(mon, "_my_max_adopted", None)
        assert any(e.get("event") == "max_hp_mismatch_staraptor" for e in st.events), st.events
    finally:
        _restore_counts(saved)
    print("test_max_hp_adoption_guards OK")


if __name__ == "__main__":
    t0 = time.time()
    test_pure_rules()
    test_fixture_bar_ratio()
    test_estimate_after_stable_bar_then_replaced_by_reading()
    test_empty_bar_is_not_faint()
    test_no_estimate_without_correspondence()
    test_unstable_bar_not_estimated()
    test_set_hp_marks_kept_until_real_reading()
    test_log_rows_carry_source()
    test_engine_context_and_uncertain_penalty()
    test_missed_switch_overrides_bar_source()
    test_restore_and_merge_keep_source()
    test_max_hp_adoption_guards()
    print(f"\nALL OK ({time.time() - t0:.1f}s)")
