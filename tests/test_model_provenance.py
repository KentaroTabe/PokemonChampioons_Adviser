"""既存の選出モデルの学習履歴 (provenance) の復元 (2026-11-02 開発者判断 4、tools/inheritance_eval/reconstruct_provenance.py・
advisor/pin_bundle.reconstructed_evidence_status / classify_from_meta の reconstructed) のテスト。

- (a) 証拠の強さ: training_log は強い、hash_identity は参照先自身に training_log か trained の meta があるときだけ強い
  (参照先が不明なら弱い)、
  mtime だけ・run_manifest だけは弱い (種別は「不明」のまま)
- (b) classify_from_meta: 復元で証拠が強ければ kind_source "reconstructed"、弱ければ unknown。formal_status は reconstructed を
  meta と同じに扱う (構築専用で型の前提 unknown は False)。source が無い記録は trained とみなす
- (c) 記録の解析と保存行の照合 (時刻・パス・直後の時刻と mtime)
- (d) 合成の本体ツリーで: 汎用モデル自身に training_log の一致があれば、それと同一の Package も hash_identity で general・
  formal_ok 可。汎用モデル自身に記録が無ければ、汎用モデルも同一の Package も不明 (置き場の実体が自分自身と同一は証拠にしない)。
  参照先に学習コードが書いた trained の meta があれば同一 sha の Package は強い証拠 (formal_ok 可)、参照先の meta が記録から復元した
  弱いものなら不可。
  training_log 一致 → 種別が決まる、mtime だけ → 不明、run_manifest だけ → 不明、保存行のパスが同じでも mtime が離れていれば不明
- (e) 復元した meta が classify_from_meta と bundle (manifest の selection) で読める
- (f) dry-run は本体に書かない (--out は本体のモデルの dir に置けない)、--apply は同じ sha のモデルの隣にだけ書く
合成のファイルだけを使う (本体のモデル・記録は読まない)。

    python -m tests.test_model_provenance
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path

from advisor import pin_bundle as PB
from tests.test_model_kind import _freeze_meta, _man
from tests.test_pin_bundle import PIN_ENV_KEYS, TEAM, _raises, _sources, _write_model
from tools.inheritance_eval import reconstruct_provenance as RP

NOW = "2026-11-02 12:00:00"
TOL = 120.0
JA = {"アーマーガア": "corviknight", "カバルドン": "hippowdon", "ミミッキュ": "mimikyu", "ペリッパー": "pelipper",
      "ウォッシュロトム": "rotomwash", "ラグラージ": "swampert"}


def _resolve(names):
    return [JA[n] for n in names] if all(n in JA for n in names) else None


def _ts(mo, d, h, mi, s, y=2026) -> float:
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


def _ev(*kinds, ref=("training_log",)) -> list:
    """証拠の要素。hash_identity には参照先自身の証拠 ref_evidence (ref。None なら鍵を付けない) を付ける"""
    out = []
    for k in kinds:
        e = {"kind": k, "detail": f"{k} の記述", "path": None}
        if k == "hash_identity" and ref is not None:
            e["ref_evidence"] = list(ref)
        out.append(e)
    return out


def _rprov(kind="general", team=None, premise="none", evidence=None, **kw) -> dict:
    p = {"kind": kind, "finetune_team": team, "sets_premise": premise, "sets": kw.pop("sets", None), "general_model_sha16": "g" * 16,
         "trained_at": "2026-08-30 17:43:15", "data_path": None, "code_commit": None, "source": "reconstructed",
         "evidence": _ev("hash_identity", "mtime") if evidence is None else evidence, "reconstructed_at": NOW,
         "reconstructed_by": RP.RECONSTRUCTED_BY}
    p.update(kw)
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------ (a) 証拠の強さ
def test_evidence_strength():
    for kinds, ok in ((("hash_identity",), True), (("training_log", "mtime"), True), (("mtime",), False),
                      (("run_manifest",), False), (("run_manifest", "mtime"), False), ((), False), (("mystery",), False)):
        st = PB.reconstructed_evidence_status({"evidence": _ev(*kinds)})
        assert st["ok"] is ok, (kinds, st)
        assert (st["reason"] is None) is ok
    # 参照先が不明なら hash_identity は弱い証拠 (ref_evidence が空・training_log が無い・鍵が無い)
    for ref in ((), ("mtime",), ("run_manifest", "mtime"), None):
        st = PB.reconstructed_evidence_status({"evidence": _ev("hash_identity", "mtime", ref=ref)})
        assert st["ok"] is False and st["strong"] == [] and "参照先" in st["reason"], (ref, st)
    st = PB.reconstructed_evidence_status({"evidence": _ev("hash_identity", "mtime", ref=("trained_meta",))})
    assert st["ok"] is True and st["strong"] == ["hash_identity"]          # 参照先が trained の meta なら強い
    st = PB.reconstructed_evidence_status({"evidence": _ev("hash_identity", "training_log", ref=())})
    assert st["ok"] is True and st["strong"] == ["training_log"]           # training_log が自分にあれば強い
    st = PB.reconstructed_evidence_status({"evidence": _ev("mtime", "hash_identity", "mtime")})
    assert st["kinds"] == ["mtime", "hash_identity"] and st["strong"] == ["hash_identity"]
    assert "弱い" in PB.reconstructed_evidence_status({"evidence": _ev("mtime")})["reason"]
    assert "無い" in PB.reconstructed_evidence_status({})["reason"]
    assert set(PB.STRONG_EVIDENCE_KINDS) == {"hash_identity", "training_log"} and set(PB.STRONG_EVIDENCE_KINDS) < set(PB.EVIDENCE_KINDS)
    print("test_evidence_strength OK")


# ------------------------------------------------------------------ (b) classify_from_meta の reconstructed
def test_classify_reconstructed():
    sha = "cd" * 32
    meta = lambda prov, s=sha[:16]: {"model_sha16": s, "provenance": prov}
    g = PB.classify_from_meta(meta(_rprov()), sha, "m_meta.json")
    assert (g["kind"], g["sets_premise"], g["kind_source"]) == ("general", "none", "reconstructed"), g
    assert "reconstructed" in g["kind_basis"] and "hash_identity" in g["kind_basis"] and "m_meta.json" in g["kind_basis"]
    assert g["provenance"]["source"] == "reconstructed" and g["provenance"]["evidence_kinds"] == ["hash_identity", "mtime"]
    assert PB.formal_status(g) == {"ok": True, "reason": None}
    # 参照先が不明の hash_identity だけ → 不明
    wh = PB.classify_from_meta(meta(_rprov(evidence=_ev("hash_identity", "run_manifest", "mtime", ref=()))), sha)
    assert (wh["kind"], wh["kind_source"]) == ("unknown", "unknown") and "参照先" in wh["kind_basis"], wh
    t = PB.classify_from_meta(meta(_rprov(evidence=_ev("training_log"))), sha)
    assert (t["kind"], t["kind_source"]) == ("general", "reconstructed") and PB.formal_status(t)["ok"] is True
    # 弱い証拠だけ → 不明 (kind_source unknown、理由に「復元」)
    for kinds in (("mtime",), ("run_manifest", "mtime")):
        u = PB.classify_from_meta(meta(_rprov(evidence=_ev(*kinds))), sha)
        assert (u["kind"], u["kind_source"]) == ("unknown", "unknown") and "復元" in u["kind_basis"] and "弱い" in u["kind_basis"], u
        assert PB.formal_status(u)["ok"] is False
    # 構築専用 + 型の前提 unknown (記録に型が無い) → 種別は team だが formal_ok False
    tm = PB.classify_from_meta(meta(_rprov("team_finetuned", TEAM, "unknown", evidence=_ev("training_log"))), sha)
    assert (tm["kind"], tm["team"], tm["sets_premise"], tm["kind_source"]) == ("team", sorted(TEAM), "unknown", "reconstructed")
    assert PB.formal_status(tm)["ok"] is False and "型の前提が不明" in PB.formal_status(tm)["reason"]
    # 読めない source・sha の不一致は不明
    assert PB.classify_from_meta(meta(_rprov(source="guessed")), sha)["kind"] == "unknown"
    assert PB.classify_from_meta(meta(_rprov(), "0" * 16), sha)["kind"] == "unknown"
    # source が無い学習時の記録は trained (kind_source meta)
    from champions_agent.train.train_selection import build_provenance
    tr = PB.classify_from_meta(meta(build_provenance("general", None, "none", None, "g" * 16, "/d.npz", "c1", "t")), sha)
    assert (tr["kind"], tr["kind_source"], tr["provenance"]["source"]) == ("general", "meta", "trained")
    old = dict(build_provenance("general", None, "none", None, "g" * 16, "/d.npz", "c1", "t"))
    old.pop("source")                                                      # 2026-11-02 より前の形 (source なし) も trained
    tr0 = PB.classify_from_meta(meta(old), sha)
    assert (tr0["kind"], tr0["kind_source"], tr0["provenance"]["source"]) == ("general", "meta", "trained")
    # manifest の selection: reconstructed は明示扱いにならない・形の検査を通る
    sel = PB.finalize_selection({"kind": None, "features": "v1"}, g)
    assert sel["kind_source"] == "reconstructed" and sel["formal_ok"] is True
    sel2 = PB.finalize_selection({"kind": "general", "kind_source": "reconstructed", "features": "v1"}, g)
    assert sel2["kind_source"] == "reconstructed" and sel2["kind_basis"] == g["kind_basis"]
    assert "reconstructed" in PB.KIND_SOURCES
    print("test_classify_reconstructed OK")


# ------------------------------------------------------------------ (c) 記録の解析と照合
LOG = """===== 再収集開始: 07-31 20:03:50 =====
--- ラウンド 1/2: 20:03:50 ---
[collect_selection] 今回4000件 / 累計29000件 (213s) → /x/selection_data.npz
完了: 20:26:25
--- 学習: 汎用+配布 ---
[train_selection] データ 52000件 / チーム271種
  epoch 50/300 train=0.2331 val=0.2899
[train_selection] 汎用モデル保存: {gen}
[my_team] 登録13体から現在の6体を推定: アーマーガア / カバルドン / ミミッキュ / ペリッパー / ウォッシュロトム / ラグラージ
  my_teamで微調整: 8000件 / 保持2000件 MSE 0.1748 → 0.1587
[train_selection] 保存: {dist}
--- 学習: 条件付きモデル (読み合い用) ---
完了: 20:40:00
[train_selection] データ 19676件 (相手の実選出3体に条件付け)
[train_selection] 条件付きモデル保存: {cond}
===== 再収集完了: 07-31 21:12:31 =====
"""

LOG_SKIP = """===== 選出データ更新: 08-03 23:59:50 =====
[train_selection] データ 49000件
  微調整をとばします (my_teamのデータが0件と少ない。bash scripts/collect_selection.sh 2 2500 myteam で収集する)
[train_selection] 保存: {dist}
完了: 00:00:10
"""


def test_parse_and_match():
    ev = RP.parse_training_log(LOG.format(gen="/r/g.pt", dist="/r/d.pt", cond="/r/c.pt"), _ts(7, 31, 22, 0, 0), "logs/x.log")
    assert [(e["label"], e["path"]) for e in ev] == [("汎用モデル", "/r/g.pt"), ("", "/r/d.pt"), ("条件付きモデル", "/r/c.pt")]
    g, d, c = ev
    assert g["ts_before"] == _ts(7, 31, 20, 26, 25) and g["ts_after"] == _ts(7, 31, 20, 40, 0) and g["finetune"] is None
    assert d["finetune"] is True and d["team_names"][0] == "アーマーガア" and len(d["team_names"]) == 6
    assert c["ts_before"] == _ts(7, 31, 20, 40, 0) and c["ts_after"] == _ts(7, 31, 21, 12, 31) and not c["ts_after_is_log_mtime"]
    # 照合: パス・直前の時刻 ≤ mtime・直後の時刻との差が許容以内
    assert RP.match_save_events(ev, "/r/c.pt", _ts(7, 31, 21, 12, 31), TOL) == [c]
    assert RP.match_save_events(ev, "/r/c.pt", _ts(7, 31, 21, 11, 0), TOL) == [c]
    assert RP.match_save_events(ev, "/r/c.pt", _ts(7, 31, 20, 58, 0), TOL) == []          # 直後の時刻から 14 分以上前
    assert RP.match_save_events(ev, "/r/c.pt", _ts(8, 30, 17, 43, 15), TOL) == []         # 後で上書きされた
    assert RP.match_save_events(ev, "/r/other.pt", _ts(7, 31, 21, 12, 31), TOL) == []
    # 種別: 汎用・条件付き → general、配布版 + 微調整 → team_finetuned (前提 unknown)、6 体を直せなければ 6 体なし
    assert RP.kind_from_save(g)["kind"] == "general" and RP.kind_from_save(c)["sets_premise"] == "none"
    k = RP.kind_from_save(d, _resolve)
    assert (k["kind"], k["team"], k["sets_premise"]) == ("team_finetuned", sorted(JA.values()), "unknown"), k
    assert RP.kind_from_save(d, None)["team"] is None
    # 日付をまたぐ・保存行の後に時刻が無い・微調整を見送った
    sk = RP.parse_training_log(LOG_SKIP.format(dist="/r/d.pt"), _ts(8, 4, 1, 0, 0))
    assert sk[0]["finetune"] is False and sk[0]["ts_after"] == _ts(8, 4, 0, 0, 10) and RP.kind_from_save(sk[0])["kind"] == "general"
    tail = RP.parse_training_log("===== 開始: 08-03 10:00:00 =====\n[train_selection] 汎用モデル保存: /r/g.pt\n", _ts(8, 3, 10, 5, 0))
    assert tail[0]["ts_after"] == _ts(8, 3, 10, 5, 0) and tail[0]["ts_after_is_log_mtime"]
    nofine = RP.parse_training_log("10-01 10:00:00\n[train_selection] データ 1件\n[train_selection] 保存: /r/d.pt\n",
                                   _ts(10, 1, 10, 1, 0))
    assert RP.kind_from_save(nofine[0])["kind"] is None                      # 微調整の有無が分からない配布版は決めない
    print("test_parse_and_match OK")


# ------------------------------------------------------------------ (d) 合成の本体ツリー
def _tree(root: Path, general_logged: bool = False) -> dict:
    """合成の本体ツリー: 汎用・その写しの Package・条件付き (記録あり)・配布版 (記録あり、微調整)・v2 (mtime だけ)・
    別の Package (manifest だけ)・sandbox (保存行はあるが mtime が離れている)。記録のパスは実パス (/var → /private/var)。
    general_logged なら汎用モデルの保存行が記録にあり mtime も一致する (無ければ汎用モデルの由来は不明)"""
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    md = root / RP.MODELS_REL
    md.mkdir(parents=True)
    gen = md / "selection_model_general.pt"
    gen.write_bytes(b"general-weights")
    pk = root / RP.PACKAGE_REL / "package-aaaa" / "advisor_policy"
    pk.mkdir(parents=True)
    (pk / "selection_model.pt").write_bytes(b"general-weights")
    (pk.parent / "manifest.json").write_text(json.dumps({"selection_model_sha256": _sha(gen), "pick_variant": "generic"}), "utf-8")
    pk2 = root / RP.PACKAGE_REL / "package-bbbb" / "advisor_policy"
    pk2.mkdir(parents=True)
    (pk2 / "selection_model.pt").write_bytes(b"adapted-weights")
    (pk2.parent / "manifest.json").write_text(json.dumps({"selection_model_sha256": _sha(pk2 / "selection_model.pt"),
                                                          "pick_policy": "advisor"}), "utf-8")
    cond, dist, v2 = md / "selection_model_cond.pt", md / "selection_model.pt", md / "selection_model_v2_general.pt"
    cond.write_bytes(b"cond-weights")
    dist.write_bytes(b"dist-weights")
    (md / "selection_model_meta.json").write_text(json.dumps({"teams": [["a"]], "n_samples": 5}), "utf-8")
    v2.write_bytes(b"v2-weights")
    sb = root / RP.SANDBOX_REL
    sb.mkdir(parents=True)
    sbg = sb / "selection_model_general.pt"
    sbg.write_bytes(b"sandbox-general")
    logs = root / "logs"
    log = logs / "recollect_selection.log"
    log.write_text(LOG.format(gen=gen if general_logged else root / "nowhere.pt", dist=dist, cond=cond), "utf-8")
    os.utime(gen, (_ts(7, 31, 20, 39, 45),) * 2)                        # 汎用の保存行の直後の時刻 20:40:00 の 15 秒前
    os.utime(log, (_ts(7, 31, 22, 0, 0),) * 2)
    os.utime(cond, (_ts(7, 31, 21, 12, 31),) * 2)
    os.utime(dist, (_ts(7, 31, 20, 39, 50),) * 2)                       # 配布版の保存行の直後の時刻 20:40:00 の 10 秒前
    sblog = logs / "selection_refresh" / "run.log"
    sblog.parent.mkdir(parents=True, exist_ok=True)
    sblog.write_text(f"===== 選出データ更新: 08-03 16:44:29 =====\n完了: 17:40:48\n[train_selection] データ 1件\n"
                     f"[train_selection] 汎用モデル保存: {sbg}\n===== 完了: 08-03 18:25:49 =====\n", "utf-8")
    os.utime(sblog, (_ts(8, 3, 18, 25, 49),) * 2)
    os.utime(sbg, (_ts(8, 3, 18, 11, 22),) * 2)                          # 直後の時刻から 867 秒前 (別の書き込み)
    return {"gen": gen, "pk": pk / "selection_model.pt", "pk2": pk2 / "selection_model.pt", "cond": cond, "dist": dist, "v2": v2,
            "sbg": sbg}


def _by_rel(results: list, root: Path, p: Path) -> dict:
    return next(r for r in results if r["rel"] == str(p.relative_to(root)))


def test_reconstruct_tree():
    # 汎用モデル自身に記録が無い → 汎用モデルも、それと同一の Package も不明 (置き場の実体そのものは証拠にしない)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "main"
        f = _tree(root)
        rs = RP.reconstruct(root, TOL, NOW, resolve_names=_resolve)
        r = lambda k: _by_rel(rs, root.resolve(), f[k].resolve())
        g = r("gen")
        assert g["status"] == "unknown" and [e["kind"] for e in g["evidence"]] == ["run_manifest", "mtime"], g
        assert "run_manifest, mtime だけ" in g["reason"] and g["formal"]["ok"] is False
        pk = r("pk")
        assert pk["status"] == "unknown" and pk["classification"]["kind"] == "unknown" and pk["formal"]["ok"] is False, pk
        hi = next(e for e in pk["evidence"] if e["kind"] == "hash_identity")
        assert hi["ref_evidence"] == [] and "参照先" in pk["reason"] and "参照先不明" in RP.markdown(rs, str(root), NOW)
        assert r("cond")["formal"]["ok"] is True
        assert "復元 2 個 / formal_ok の見込み True 1 個" in RP.markdown(rs, str(root), NOW)
    # 参照先 (汎用モデル) に学習コードが書いた trained の meta → 同一 sha の Package は hash_identity (trained_meta) で general・formal_ok 可。
    # 参照先の meta が記録から復元した弱いもの → 不可。復元した強いもの (training_log) → 可
    from champions_agent.agent.selection_model import provenance_meta_path
    from champions_agent.train.train_selection import build_provenance, write_model_meta
    def _rec(gen: Path, *kinds) -> None:
        doc = RP.proposed_meta(None, _sha(gen), _rprov(evidence=_ev(*kinds)))
        provenance_meta_path(gen).write_text(json.dumps(doc), "utf-8")

    cases = (("trained", lambda gen: write_model_meta(gen, build_provenance("general", None, "none", None, None, "/d.npz", "c1", "t"))),
             ("weak", lambda gen: _rec(gen, "mtime")),
             ("strong", lambda gen: _rec(gen, "training_log", "mtime")))
    for name, write in cases:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "main"
            f = _tree(root)
            write(f["gen"])
            rs = RP.reconstruct(root, TOL, NOW, resolve_names=_resolve)
            g = _by_rel(rs, root.resolve(), f["gen"].resolve())
            pk = _by_rel(rs, root.resolve(), f["pk"].resolve())
            hi = next(e for e in pk["evidence"] if e["kind"] == "hash_identity")
            if name == "trained":
                assert g["status"] == "trained" and g["classification"]["kind_source"] == "meta", g
                assert hi["ref_evidence"] == ["trained_meta"] and pk["status"] == "reconstructed", pk
                assert pk["classification"]["kind"] == "general" and pk["formal"]["ok"] is True
            elif name == "weak":
                assert g["status"] == "unknown" and g["classification"]["kind"] == "unknown", g
                assert hi["ref_evidence"] == [] and pk["status"] == "unknown" and pk["formal"]["ok"] is False, pk
            else:
                assert g["status"] == "existing_reconstructed" and g["classification"]["kind_source"] == "reconstructed", g
                assert hi["ref_evidence"] == ["training_log"] and pk["formal"]["ok"] is True, pk
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "main"
        f = _tree(root, general_logged=True)
        rs = RP.reconstruct(root, TOL, NOW, resolve_names=_resolve)
        assert len(rs) == 7, [r["rel"] for r in rs]
        r = lambda k: _by_rel(rs, root.resolve(), f[k].resolve())
        # 汎用モデル自身は training_log で general、それと同一の Package は hash_identity (参照先は training_log) で general
        g = r("gen")
        assert g["status"] == "reconstructed" and [e["kind"] for e in g["evidence"]] == ["training_log", "run_manifest", "mtime"], g
        x = r("pk")
        assert x["status"] == "reconstructed" and x["classification"]["kind"] == "general", x
        assert x["classification"]["kind_source"] == "reconstructed" and x["classification"]["sets_premise"] == "none"
        assert x["formal"]["ok"] is True and x["evidence"][0]["kind"] == "hash_identity"
        assert x["evidence"][0]["ref_evidence"] == ["training_log"]
        assert g["formal"]["ok"] is True and g["classification"]["kind"] == "general"
        assert any(e["kind"] == "run_manifest" and "このモデルの Package" in e["detail"] for e in r("pk")["evidence"])
        # training_log 一致 → 種別が決まる (条件付き = general、配布版 = 微調整あり → team_finetuned・前提 unknown)
        c = r("cond")
        assert c["status"] == "reconstructed" and c["classification"]["kind"] == "general" and c["formal"]["ok"] is True
        assert [e["kind"] for e in c["evidence"]] == ["training_log", "mtime"] and "21:12:31" in c["evidence"][0]["detail"]
        d = r("dist")
        assert d["status"] == "reconstructed" and d["provenance"]["kind"] == "team_finetuned", d
        assert (d["classification"]["kind"], d["classification"]["team"], d["classification"]["sets_premise"]) == \
            ("team", sorted(JA.values()), "unknown") and d["formal"]["ok"] is False
        assert any("既存の meta (provenance なし)" in n for n in d["notes"])
        # mtime だけ → 不明、run_manifest だけ → 不明、保存行のパスが同じでも mtime が離れている → 不明
        for k, why in (("v2", "mtime だけ"), ("pk2", "run_manifest, mtime だけ"), ("sbg", "mtime だけ")):
            x = r(k)
            assert x["status"] == "unknown" and x["provenance"] is None and why in x["reason"], (k, x)
            assert x["classification"]["kind"] == "unknown" and x["formal"]["ok"] is False
        md = RP.markdown(rs, str(root), NOW)
        assert "| モデル | 区分 | sha16 |" in md and "**hash_identity**" in md and "**training_log**" in md
        assert f"復元 4 個 / formal_ok の見込み True 3 個" in md, md.splitlines()[:5]
    # 1 モデルの復元 (純粋): 配布版の 6 体を辞書で直す
    m = {"rel": "m/selection_model.pt", "abs": "/r/d.pt", "sha256": "ee" * 32, "mtime": _ts(7, 31, 20, 39, 50), "size": 1,
         "role": "配布版", "meta": None, "meta_exists": False}
    ev = RP.parse_training_log(LOG.format(gen="/r/g.pt", dist="/r/d.pt", cond="/r/c.pt"), _ts(7, 31, 22, 0, 0), "logs/x.log")
    x = RP.reconstruct_one(m, None, ev, [], TOL, NOW, _resolve)
    assert x["classification"]["kind"] == "team" and x["classification"]["team"] == sorted(JA.values())
    assert x["classification"]["sets_premise"] == "unknown" and x["formal"]["ok"] is False
    # 既に学習時の provenance がある meta は復元しない (trained のまま)
    from champions_agent.train.train_selection import build_provenance
    m2 = dict(m, meta={"model_sha16": m["sha256"][:16],
                       "provenance": build_provenance("general", None, "none", None, None, "/d", "c", "t")}, meta_exists=True)
    y = RP.reconstruct_one(m2, None, ev, [], TOL, NOW, _resolve)
    assert y["status"] == "trained" and y["provenance"] is None and y["classification"]["kind_source"] == "meta"
    print("test_reconstruct_tree OK")


# ------------------------------------------------------------------ (e) 復元した meta が bundle で読める
def test_reconstructed_meta_in_bundle():
    for k in PIN_ENV_KEYS:
        os.environ.pop(k, None)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = _sources(d)
        src["selection_meta"].unlink()
        gm = src["selection_model"].with_name("selection_model_general.pt")
        _write_model(gm, 5)
        from champions_agent.agent.selection_model import provenance_meta_path
        mp = provenance_meta_path(gm)
        mp.write_text(json.dumps(RP.proposed_meta(None, _sha(gm), _rprov())), encoding="utf-8")
        b = _freeze_meta(d, src, "reconstructed_general", model=gm)
        s = _man(b)["selection"]
        assert (s["kind"], s["sets_premise"], s["kind_source"], s["formal_ok"]) == ("general", "none", "reconstructed", True), s
        assert s["meta_classification"]["provenance"]["source"] == "reconstructed"
        assert PB.verify_bundle(b, {})["formal"] == {"ok": True, "reason": None}
        assert PB.check_bundle_manifest(_man(b)) == []
        # 弱い証拠だけの復元 → 不明・formal_ok False
        mp.write_text(json.dumps(RP.proposed_meta(None, _sha(gm), _rprov(evidence=_ev("mtime")))), encoding="utf-8")
        s2 = _man(_freeze_meta(d, src, "reconstructed_weak", model=gm))["selection"]
        assert (s2["kind"], s2["kind_source"], s2["formal_ok"]) == ("unknown", "unknown", False) and "弱い" in s2["kind_basis"]
        # 既存の meta (provenance なし) の鍵は残す
        doc = RP.proposed_meta({"teams": [["a"]], "n_samples": 3}, _sha(gm), _rprov())
        assert doc["teams"] == [["a"]] and doc["model_sha16"] == _sha(gm)[:16] and doc["provenance"]["source"] == "reconstructed"
    print("test_reconstructed_meta_in_bundle OK")


# ------------------------------------------------------------------ (f) dry-run は本体に書かない / --apply
def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, _sha(p)) for p in sorted(root.rglob("*")) if p.is_file()}


def test_dry_run_and_apply():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "main"
        f = _tree(root, general_logged=True)
        before = _snapshot(root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert RP.main(["--root", str(root)]) == 0
        assert "| モデル |" in buf.getvalue() and _snapshot(root) == before
        out = Path(td) / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            RP.main(["--root", str(root), "--out", str(out)])
        assert _snapshot(root) == before                                      # 本体には書かない
        assert (out / "report.md").exists() and json.loads((out / "results.json").read_text("utf-8"))
        prop = out / "proposed" / RP.MODELS_REL / "selection_model_general_meta.json"
        pd = json.loads(prop.read_text("utf-8"))
        assert PB.classify_from_meta(pd, _sha(f["gen"]))["kind_source"] == "reconstructed"
        assert not (out / "proposed" / RP.MODELS_REL / "selection_model_v2_general_meta.json").exists()   # 不明は案を書かない
        for bad in (root / RP.MODELS_REL / "o", root / "logs" / "registry" / "o"):
            _raises(SystemExit, RP.main, ["--root", str(root), "--out", str(bad)])
        assert _snapshot(root) == before
        # --apply: 写しのツリーへ。同じ sha のモデルの隣にだけ書く、registry 配下は --apply-registry のときだけ
        import shutil
        dest = Path(td) / "copy"
        shutil.copytree(root, dest)
        (dest / RP.MODELS_REL / "selection_model_cond.pt").write_bytes(b"changed")           # sha が違う → 書かない
        with contextlib.redirect_stdout(io.StringIO()):
            RP.main(["--root", str(root), "--apply", str(dest)])
        gmeta = dest / RP.MODELS_REL / "selection_model_general_meta.json"
        assert PB.classify_from_meta(json.loads(gmeta.read_text("utf-8")), _sha(f["gen"]))["kind"] == "general"
        assert not (dest / RP.MODELS_REL / "selection_model_cond_meta.json").exists()
        pmeta = dest / RP.PACKAGE_REL / "package-aaaa" / "advisor_policy" / "selection_model_meta.json"
        assert not pmeta.exists() and _snapshot(root) == before
        dmeta = json.loads((dest / RP.MODELS_REL / "selection_model_meta.json").read_text("utf-8"))
        assert dmeta.get("teams") == [["a"]]                                     # 既存の鍵は残す (配布版は復元できれば追記)
        with contextlib.redirect_stdout(io.StringIO()):
            RP.main(["--root", str(root), "--apply", str(dest), "--apply-registry"])
        assert PB.classify_from_meta(json.loads(pmeta.read_text("utf-8")), _sha(f["pk"]))["kind"] == "general"
        # 既に provenance がある meta は書き換えない
        g1 = gmeta.read_text("utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            RP.main(["--root", str(root), "--apply", str(dest)])
        assert gmeta.read_text("utf-8") == g1
    print("test_dry_run_and_apply OK")


def main() -> None:
    for k in PIN_ENV_KEYS:
        os.environ.pop(k, None)
    test_evidence_strength()
    test_classify_reconstructed()
    test_parse_and_match()
    test_reconstruct_tree()
    test_reconstructed_meta_in_bundle()
    test_dry_run_and_apply()
    print("ALL OK")


if __name__ == "__main__":
    main()
