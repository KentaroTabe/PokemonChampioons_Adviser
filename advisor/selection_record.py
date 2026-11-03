"""選出の助言の記録用の欄 (2026-10-07 段 0、docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2)。

対戦ログの selection_record 行 (選出の advice 行と advice_id で結ぶ) の 2 つの欄を作る。どちらも**記録だけ**で、表示する助言
(advice の中身・第一候補) は変えない (server は advice_update を送った後に計算して battle_log.on_selection_record で書く。表示を遅らせない)。

  candidates   : 方式ごとの選出候補を同じ形で並べる。
                 {"rule" | "deployed" | "general" | "registered" | "experiment" | "model_pick_real": entry or None,
                  "reasons": {方式: null の理由}, "primary": "model"|"rule", "used": model_pick.model (◎ に使った経路)}
                 entry = {"indices", "species", "names", "lead", "lead_index", "prob", "win_prob"}
                   prob: 方式が 3 体組の確率を出すときだけ (いまの方式はどれも出さないので null)。win_prob: 予測勝率
  opp_pick_pred: 相手の選出の予測 (combo_prior の全分布)。
                 {"slots": [{"slot", "species", "ja", "status": confirmed|guess|unknown, "pick_prob"}],
                  "combos": [{"slots", "species", "p"}] (判明している枠の 3 体組すべて。6 枠判明なら 20 通り),
                  "incomplete": 種の分からない枠がある, "n_known", "n_guess", "bank": バンクの版と締切時刻}

純粋関数 (build_candidates / method_entry / opp_pick_prediction) は tests/test_selection_record.py。
"""
from __future__ import annotations

from itertools import combinations
from typing import Callable, Optional

from champions_agent.config import BSS_PICK_COUNT, PARTY_SIZE

METHODS = ("rule", "deployed", "general", "registered", "experiment", "model_pick_real")


def _name(p: dict) -> str:
    return p.get("species_ja") or p.get("species_id") or "?"


def method_entry(indices: list, my_party: list, win_prob: Optional[float] = None, prob: Optional[float] = None,
                 lead_index: Optional[int] = None) -> dict:
    """方式の候補 1 つ (純粋)。indices は自分のパーティ (my_party) の枠番号で先発が先頭 (lead_index で明示もできる)"""
    idx = [int(i) for i in indices]
    party = my_party or []
    names = [_name(party[i]) if 0 <= i < len(party) else "?" for i in idx]
    species = [party[i].get("species_id") if 0 <= i < len(party) else None for i in idx]
    lead = lead_index if lead_index is not None else (idx[0] if idx else None)
    return {"indices": idx, "species": species, "names": names,
            "lead": (_name(party[lead]) if lead is not None and 0 <= lead < len(party) else None), "lead_index": lead,
            "prob": (round(float(prob), 4) if prob is not None else None),
            "win_prob": (round(float(win_prob), 4) if win_prob is not None else None)}


def _rule_recommend(advice: dict) -> Optional[list]:
    """相性の規則の推奨 (第一候補がモデルなら rule_recommend に退避されている)"""
    if not advice.get("ok"):
        return None
    if advice.get("primary") == "model":
        return advice.get("rule_recommend") or None
    return advice.get("recommend") or None


def build_candidates(advice: dict, my_party: list, model_results: dict, reasons: Optional[dict] = None) -> dict:
    """方式ごとの選出候補を同じ形に揃える (純粋)。
    model_results: {方式: (perm (my_species の座標), 予測勝率) or None}。my_species は species_id のある枠だけを並べたもの
    (advisor.selection.attach_model_pick と同じ座標)。reasons: {方式: 結果が無い理由}"""
    reasons = dict(reasons or {})
    idx_map = [i for i, p in enumerate(my_party or []) if p.get("species_id")]
    out: dict = {}
    rr = _rule_recommend(advice)
    if rr:
        lead = next((r.get("index") for r in rr if r.get("lead")), None)
        out["rule"] = method_entry([r.get("index") for r in rr], my_party, lead_index=lead)
    else:
        out["rule"] = None
        reasons.setdefault("rule", advice.get("reason") or "規則の推奨なし")
    for m in ("deployed", "general", "registered", "experiment"):
        got = (model_results or {}).get(m)
        if got:
            perm, wp = got
            out[m] = method_entry([idx_map[i] for i in perm if i < len(idx_map)], my_party, win_prob=wp)
        else:
            out[m] = None
            reasons.setdefault(m, "モデルの結果なし")
    mpr = advice.get("model_pick_real") or {}
    if mpr.get("names"):
        by_name = {_name(p): i for i, p in enumerate(my_party or [])}
        ids = [by_name.get(n) for n in mpr["names"]]
        if all(i is not None for i in ids):
            out["model_pick_real"] = method_entry(ids, my_party, win_prob=mpr.get("expected_win_prob"))
        else:
            out["model_pick_real"] = {"indices": None, "species": None, "names": list(mpr["names"]), "lead": mpr["names"][0],
                                      "lead_index": None, "prob": None,
                                      "win_prob": (round(float(mpr["expected_win_prob"]), 4)
                                                   if mpr.get("expected_win_prob") is not None else None)}
    else:
        out["model_pick_real"] = None
        reasons.setdefault("model_pick_real", "条件付きモデルかバンクの選出率が無い (前提を満たさない)")
    out["reasons"] = {k: v for k, v in reasons.items() if out.get(k) is None}
    out["primary"] = advice.get("primary")
    out["used"] = (advice.get("model_pick") or {}).get("model")
    return out


def model_sources(my_species: list) -> tuple:
    """各モデル方式のパス → ({方式: Path}, {方式: 無い理由})。副作用: ファイルの有無を見る"""
    from champions_agent.agent.selection_dispatch import (REGISTERED_DIR, deployed_model_path, experiment_package_model,
                                                          general_model_path, registered_team_model, team_key)
    paths, reasons = {}, {}
    for m, p in (("deployed", deployed_model_path()), ("general", general_model_path())):
        if p.exists():
            paths[m] = p
        else:
            reasons[m] = f"モデルのファイルが無い ({p.name})"
    reg = registered_team_model(my_species)
    if reg:
        paths["registered"] = reg["path"]
    elif len(set(my_species or [])) != PARTY_SIZE:
        reasons["registered"] = f"自分の 6 体が揃っていない ({len(set(my_species or []))} 体)"
    elif (REGISTERED_DIR / team_key(my_species) / "selection_model.pt").exists():
        reasons["registered"] = "登録チーム向けモデルの特徴量の版が違う"
    else:
        reasons["registered"] = f"未登録 (このチームの鍵 {team_key(my_species)} のモデルなし)"
    pkg = experiment_package_model()
    if pkg and pkg.get("species") and set(my_species or []) <= set(pkg["species"]):
        paths["experiment"] = pkg["path"]
    elif pkg:
        reasons["experiment"] = f"試用中 Package {pkg['package_id']} の 6 体と登録パーティが違う"
    else:
        reasons["experiment"] = "試用中 Package なし"
    return paths, reasons


def selection_candidates(advice: dict, my_party: list, opp_party: list, score_fn: Optional[Callable] = None) -> dict:
    """方式ごとの選出候補 (副作用: モデルの読み込み)。score_fn(my_species, opp_species, path) → [(perm, 予測勝率)] 降順"""
    idx = [i for i, p in enumerate(my_party or []) if p.get("species_id")]
    mine = [my_party[i]["species_id"] for i in idx]
    opp = [p.get("species_id") for p in (opp_party or []) if p.get("species_id")]
    if len(mine) < BSS_PICK_COUNT:
        return build_candidates(advice, my_party, {}, {m: f"自分の種が {len(mine)} 体しか分からない"
                                                       for m in ("deployed", "general", "registered", "experiment")})
    if score_fn is None:
        from champions_agent.agent.selection_dispatch import score_all
        score_fn = score_all
    paths, reasons = model_sources(mine)
    results = {}
    for m, path in paths.items():
        try:
            scored = score_fn(mine, opp, path)
        except Exception as e:
            scored = None
            reasons[m] = f"推論に失敗: {e!r}"[:120]
        if scored:
            perm, wp = scored[0]
            results[m] = (list(perm), float(wp))
        elif m not in reasons:
            reasons[m] = "モデルを読めない"
    return build_candidates(advice, my_party, results, reasons)


# ------------------------------------------------------------------ 相手の選出の予測
def opp_pick_prediction(opp_party: list, pick_prob_fn: Callable, bank: Optional[dict] = None,
                        prior_fn: Optional[Callable] = None) -> dict:
    """combo_prior の全分布 (純粋。pick_prob_fn(sid) → 実戦の選出率 or None、prior_fn は combo_prior と同じ引数)。
    相手の枠ごとに 確定 (confirmed) / 推定 (guess、選出画面の推定) / 不明 (unknown) を分け、種の分かる枠の 3 体組すべての確率を残す
    (上位だけだと圏外の正解の対数尤度が出せない)。種の分からない枠があれば incomplete=True (分布は分かる枠だけの上)"""
    if prior_fn is None:
        from champions_agent.agent.selection_model import combo_prior as prior_fn
    slots = []
    for i, p in enumerate((opp_party or [])[:PARTY_SIZE]):
        sid = p.get("species_id")
        status = "unknown" if not sid else ("guess" if p.get("species_guess") else "confirmed")
        pp = None
        if sid:
            try:
                pp = pick_prob_fn(sid)
            except Exception:
                pp = None
        slots.append({"slot": i, "species": sid, "ja": p.get("species_ja"), "status": status,
                      "pick_prob": (round(float(pp), 4) if pp is not None else None)})
    known = [s for s in slots if s["species"]]
    ids = [s["species"] for s in known]
    combos = []
    if len(ids) >= BSS_PICK_COUNT:
        cs = list(combinations(range(len(ids)), BSS_PICK_COUNT))
        q = prior_fn(ids, {s["species"]: s["pick_prob"] for s in known if s["pick_prob"] is not None}, cs)
        combos = [{"slots": [known[j]["slot"] for j in c], "species": [ids[j] for j in c], "p": round(float(w), 6)}
                  for c, w in zip(cs, q)]
    return {"slots": slots, "combos": combos, "incomplete": len(known) < PARTY_SIZE, "n_known": len(known),
            "n_guess": sum(1 for s in slots if s["status"] == "guess"), "method": "combo_prior", "bank": bank}


def opp_pick_pred_live(opp_party: list) -> dict:
    """実戦のバンク (advisor.real_prior) を読んで opp_pick_prediction を作る (副作用: バンクの読み込み)"""
    from advisor.real_prior import bank_version, load_bank, species_pick_prior
    bank = load_bank()
    return opp_pick_prediction(opp_party, lambda sid: species_pick_prior(sid, bank) if bank else None, bank_version())
