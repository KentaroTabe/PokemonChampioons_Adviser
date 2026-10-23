"""正式群の実機で、助言が読む使用率 snapshot と実戦バンクを開始時の写しに固定するテスト (2026-10-23、計画書 §8.49)。

    python -m tests.test_live_data_pin

- server の起動時の検査 (tools.advice_mode.data_pin_startup_check): 指定 snapshot が DB に無い / 写しの sha16 が違う / 写しが無い /
  元のバンクそのもの / 値が不正 → SystemExit (最新へ黙って退避しない)。合っていれば起動ログの 1 行に設定値・実効値・設定元
- 助言側が実機の起動経路で固定を読むこと: 環境変数を持って起動したプロセスで、server と同じ順に import してから、型予測
  (advisor.sets)・タイプからの候補 (advisor.infer)・チャンピオンズの種の判定 (advisor.team_advice)・選出の事前分布
  (advisor.real_prior) が指定 snapshot (以下) と写しを読み、DB の最新 snapshot・元のバンクは読まない
- version 行の記録 (advisor.versions.runtime_versions): rules.usage_snapshot と runtime の usage_snapshot_source "pinned"、
  rules.real_bank の path / sha16 / pinned。固定しなければ従来どおり (db_latest、pinned False)
- --freeze-data: 最新 snapshot id と写し (sha16 が元と一致) を作り、advice.local.env に書く 3 行を出す (設定ファイルは書き換えない)
実データ (本体の使用率 DB・バンク) は読まない (合成のファイルだけ)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from advisor import data_pin as DP
from champions_agent.config import REAL_BANK_ENV, REAL_BANK_SHA_ENV, USAGE_DB_ENV, USAGE_SNAPSHOT_ENV
from tools import advice_mode as AM
from tests.test_inputs_pin import SNAP3, _add_snapshot, _bank, _make_db

REPO = Path(__file__).resolve().parent.parent


def _exit_msg(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except SystemExit as e:
        return str(e)
    raise AssertionError(f"SystemExit にならなかった: {fn.__name__}")


def _fixture(d: Path) -> dict:
    """合成の DB (snapshot 1・2・3) と元のバンク・写し (内容が違う)"""
    import sqlite3
    db = _make_db(d / "u.sqlite3")
    conn = sqlite3.connect(str(db))
    _add_snapshot(conn, 3, SNAP3)
    conn.commit()
    conn.close()
    live = _bank(d / "live_bank.json", 45)
    (d / "pinned").mkdir()
    copy = _bank(d / "pinned" / "bank.json", 20)
    return {"db": db, "live": live, "copy": copy, "sha": DP.sha16_bytes(copy.read_bytes())}


# ------------------------------------------------------------------ server の起動時の検査
def test_startup_check_errors_and_line():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fx = _fixture(d)
        local = d / "advice.local.env"
        base = {USAGE_DB_ENV: str(fx["db"])}
        # 固定なし: 従来どおり (検査しない)
        line = AM.data_pin_startup_check({}, local)
        assert "データの固定: 実効値 なし" in line and "設定元 記録なし" in line and "⚠" not in line, line
        # snapshot が DB に無い
        e = _exit_msg(AM.data_pin_startup_check, dict(base, **{USAGE_SNAPSHOT_ENV: "9"}), local)
        assert "起動を中止" in e and "snapshot 9" in e, e
        # 写しの sha16 が違う
        e = _exit_msg(AM.data_pin_startup_check, {REAL_BANK_ENV: str(fx["copy"]), REAL_BANK_SHA_ENV: "0" * 16}, local)
        assert "sha16" in e, e
        # 写しが無い
        e = _exit_msg(AM.data_pin_startup_check, {REAL_BANK_ENV: str(d / "none.json")}, local)
        assert "無い" in e, e
        # 値が不正 (sha16 の形・snapshot の形・sha16 だけ)
        assert "16 進" in _exit_msg(AM.data_pin_startup_check, {REAL_BANK_ENV: str(fx["copy"]), REAL_BANK_SHA_ENV: "xyz"}, local)
        assert "整数" in _exit_msg(AM.data_pin_startup_check, {USAGE_SNAPSHOT_ENV: "latest"}, local)
        assert "一緒に" in _exit_msg(AM.data_pin_startup_check, {REAL_BANK_SHA_ENV: fx["sha"]}, local)
        # 合っていれば起動ログの 1 行 (実効値・照合の結果・設定元・設定値)
        env = dict(base, **{USAGE_SNAPSHOT_ENV: "2", REAL_BANK_ENV: str(fx["copy"]), REAL_BANK_SHA_ENV: fx["sha"],
                            AM.PIN_SOURCE_KEY: "file"})
        local.write_text(f"{USAGE_SNAPSHOT_ENV}=2\n{REAL_BANK_ENV}={fx['copy']}\n{REAL_BANK_SHA_ENV}={fx['sha']}\n",
                         encoding="utf-8")
        line = AM.data_pin_startup_check(env, local)
        assert "実効値 snapshot 2" in line and "snapshot 2 は DB にある" in line and f"sha16 {fx['sha']} (指定と一致)" in line, line
        assert "設定元 file" in line and "⚠" not in line, line
        # sha16 を書かなければ実際の値を出す
        line = AM.data_pin_startup_check({REAL_BANK_ENV: str(fx["copy"])}, d / "none.env")
        assert f"sha16 {fx['sha']} (指定なし" in line and "設定元 env" in line, line
        # ファイルを書き換えたが再起動していない (実効値と食い違う) → 警告
        line = AM.data_pin_startup_check(dict(base, **{USAGE_SNAPSHOT_ENV: "1"}), local)
        assert "⚠ 設定値と実効値が食い違う" in line, line
    print("test_startup_check_errors_and_line OK")


def test_startup_check_refuses_live_bank():
    """ADVICE_REAL_BANK が元のバンク (対戦ごとに書き換わる logs/real_opponents/bank.json) なら起動しない"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fx = _fixture(d)
        try:
            DP.verify_spec({"bank_path": str(fx["live"])}, live_bank=fx["live"])
            raise AssertionError("元のバンクが通った")
        except DP.InputsPinError as e:
            assert "写し" in str(e)
        # 相対パスは cwd から
        got = DP.verify_spec({"bank_path": "pinned/bank.json", "bank_sha16": fx["sha"]}, live_bank=fx["live"], cwd=d)
        assert got["bank_sha16"] == fx["sha"] and got["bank_path"] == str(d / "pinned" / "bank.json")
        # 使用率: snapshot 以下の範囲は data_pin の規則 (指定 id が DB にあること)
        assert DP.verify_spec({"usage_db": str(fx["db"]), "usage_snapshot": 2})["usage_snapshot"] == 2
    print("test_startup_check_refuses_live_bank OK")


# ------------------------------------------------------------------ 実機の起動経路で助言側が固定を読む
PROBE = r"""
import json, sys
# server.py と同じ順: 起動時の検査 → 助言エンジン・対戦ログ・鍵 (import 時には版を読まない)
from tools.advice_mode import data_pin_startup_check, server_startup_check
line = data_pin_startup_check()
import advisor.service, battle_logger, advice_key
import advisor.sets as S, advisor.infer as I, advisor.team_advice as T
db = sys.argv[1]
# 明示しない経路 (最新) が読む DB も合成の DB に向ける (固定が効いていなければ snapshot 3 = 最新を読む)
S.DB_PATH = I.DB_PATH = T.DB_PATH = __import__("pathlib").Path(db)
from advisor.sets import get_predictor
from advisor.infer import get_inference
from advisor.team_advice import champions_usable
from advisor.real_prior import load_bank, species_pick_prior
from advisor import versions as V
p = get_predictor()
pred = p.predict("garchomp")      # 型予測は最初の予測で DB に接続する
doc = V.runtime_versions(refresh=True)
print(json.dumps({"line": line, "snapshot": p._snapshot_id, "pred": pred,
                  "infer_snapshot": get_inference().snapshot_id,
                  "salamence_usable": champions_usable("salamence"), "garchomp_usable": champions_usable("garchomp"),
                  "prior": species_pick_prior("garchomp", load_bank()),
                  "usage_info": list(V.usage_snapshot_info()), "rules_snapshot": doc["rules"].get("usage_snapshot"),
                  "snapshot_source": doc["runtime"].get("usage_snapshot_source"), "real_bank": doc["rules"].get("real_bank")},
                 ensure_ascii=False, default=str))
"""


def _probe(env_extra: dict, db: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in (USAGE_DB_ENV, USAGE_SNAPSHOT_ENV, REAL_BANK_ENV, REAL_BANK_SHA_ENV, AM.PIN_SOURCE_KEY)}
    env.update(env_extra)
    env["PYTHONPATH"] = str(REPO)
    out = subprocess.run([sys.executable, "-B", "-c", PROBE, str(db)], cwd=str(REPO), env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_live_process_reads_pin():
    """環境変数を持って起動したプロセス (advice_env.sh → uvicorn と同じ) で、server と同じ順に import した後も、助言側の 4 つの読み手が
    指定 snapshot (以下) と写しを読む。version 行には rules.usage_snapshot = 指定、出どころ "pinned"、rules.real_bank (写しの sha16)"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fx = _fixture(d)
        pinned = _probe({USAGE_DB_ENV: str(fx["db"]), USAGE_SNAPSHOT_ENV: "2", REAL_BANK_ENV: str(fx["copy"]),
                         REAL_BANK_SHA_ENV: fx["sha"], AM.PIN_SOURCE_KEY: "file"}, fx["db"])
        assert pinned["snapshot"] == 2 and pinned["infer_snapshot"] == 2, pinned
        assert pinned["pred"]["moves"][0][0] == "earthquake", pinned["pred"]           # snapshot 3 なら spikes
        assert pinned["salamence_usable"] is False and pinned["garchomp_usable"] is True  # ボーマンダは snapshot 3 で初めて現れる
        assert abs(pinned["prior"] - 0.4) < 1e-9, pinned["prior"]                        # 写し (picked 20 / appear 50)
        assert pinned["usage_info"] == [2, "pinned"] and pinned["rules_snapshot"] == 2 and pinned["snapshot_source"] == "pinned"
        assert pinned["real_bank"] == {"path": str(fx["copy"]), "sha16": fx["sha"], "pinned": True}, pinned["real_bank"]
        assert "snapshot 2 は DB にある" in pinned["line"]
        # 固定しない (従来どおり): 最新 snapshot 3 と元のバンク (ここでは元のバンクの場所に写しと違う内容を置けないので、
        # 使用率だけを比べる。バンクは pinned False)
        latest = _probe({USAGE_DB_ENV: str(fx["db"])}, fx["db"])
        assert latest["snapshot"] == 3 and latest["infer_snapshot"] == 3 and latest["salamence_usable"] is True, latest
        assert latest["pred"]["moves"][0][0] == "spikes"
        assert latest["usage_info"] == [3, "pinned"]       # ADVICE_USAGE_DB だけの明示も pinned (data_pin の既存の規則)
        assert (latest["real_bank"] or {}).get("pinned") in (False, None), latest["real_bank"]
    print("test_live_process_reads_pin OK")


# ------------------------------------------------------------------ version 行
def test_version_row_records_pin():
    """summarize_versions の rules.real_bank は識別 (digest) に入らない。usage_snapshot_info は固定していれば出どころ pinned"""
    from advisor import versions as V
    args = ("pkg", "/m/sel.pt", "s1", "pkg", ["a"], ["blastoise"], "/m/rl.zip", "r1", True, None, "team1", "g", "d", "e", "v1")
    reg = {"カメックス": {"性格": "ひかえめ"}}
    d0 = V.summarize_versions(*args, team_registry=reg, usage_snapshot=2, usage_snapshot_source="pinned")
    d1 = V.summarize_versions(*args, team_registry=reg, usage_snapshot=2, usage_snapshot_source="pinned",
                              real_bank={"path": "/p/bank.json", "sha16": "a" * 16, "pinned": True})
    assert d1["rules"]["real_bank"] == {"path": "/p/bank.json", "sha16": "a" * 16, "pinned": True}
    assert "real_bank" not in d0["rules"] and d1["version_id"] == d0["version_id"]
    assert d1["runtime"]["usage_snapshot_source"] == "pinned" and d1["rules"]["usage_snapshot"] == 2
    with tempfile.TemporaryDirectory() as td:
        fx = _fixture(Path(td))
        import advisor.sets as S
        try:
            DP.set_override(usage_db=fx["db"], usage_snapshot=1, bank_path=fx["copy"], bank_sha16=fx["sha"])
            assert V.usage_snapshot_info() == (1, "pinned")
            S.get_predictor().predict("garchomp")                                     # 型予測が接続した後も "pinned"
            assert V.usage_snapshot_info() == (1, "pinned")
            assert V.real_bank_info() == {"path": str(fx["copy"]), "sha16": fx["sha"], "pinned": True}
            DP.set_override(bank_path=fx["copy"], bank_sha16="0" * 16)
            bad = V.real_bank_info()
            assert bad["sha16"] is None and "sha16" in bad["error"]                   # 記録のために助言を止めない
        finally:
            DP.clear_override()
    print("test_version_row_records_pin OK")


# ------------------------------------------------------------------ --freeze-data
def test_freeze_data():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fx = _fixture(d)
        out = d / "out"
        r = AM.freeze_data(out, fx["db"], fx["live"], stamp="20261023_120000")
        want_sha = DP.sha16_bytes(fx["live"].read_bytes())
        dst = out / "20261023_120000" / "bank.json"
        assert r["usage_snapshot"] == 3 and r["bank_sha16"] == want_sha and dst.read_bytes() == fx["live"].read_bytes()
        assert r["lines"] == [f"ADVICE_USAGE_SNAPSHOT=3", f"ADVICE_REAL_BANK={dst.resolve()}", f"ADVICE_REAL_BANK_SHA16={want_sha}"]
        # 3 行はそのまま設定ファイルに書けて、検査を通る
        text = "\n".join(r["lines"]) + "\n"
        rp = AM.resolve_data_pin(text, {})
        assert rp["source"] == "file" and rp["values"]["ADVICE_USAGE_SNAPSHOT"] == "3"
        got = AM.precheck_data_pin(rp["values"], d, env={USAGE_DB_ENV: str(fx["db"])})
        assert got["usage_snapshot"] == 3 and got["bank_sha16"] == want_sha
        # 同じ時刻のディレクトリは上書きしない
        try:
            AM.freeze_data(out, fx["db"], fx["live"], stamp="20261023_120000")
            raise AssertionError("上書きした")
        except FileExistsError:
            pass
        # CLI: 書き出し先を引数で向ける。設定ファイルは書き換えない
        local = d / "advice.local.env"
        local.write_text("ADVICE_SCORING_MODE=legacy\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(REPO))
        cp = subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--freeze-data", "--out-dir", str(out),
                             "--usage-db", str(fx["db"]), "--bank", str(fx["live"]), "--file", str(local)],
                            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
        assert cp.returncode == 0, cp.stderr
        lines = [ln for ln in cp.stdout.splitlines() if not ln.startswith("#")]
        assert len(lines) == 3 and lines[0] == "ADVICE_USAGE_SNAPSHOT=3" and lines[2] == f"ADVICE_REAL_BANK_SHA16={want_sha}", cp.stdout
        assert local.read_text(encoding="utf-8") == "ADVICE_SCORING_MODE=legacy\n"
        made = Path(lines[1].split("=", 1)[1])
        assert made.parent.parent == out.resolve() and made.read_bytes() == fx["live"].read_bytes()
        bad = subprocess.run([sys.executable, "-B", "-m", "tools.advice_mode", "--freeze-data", "--out-dir", str(out),
                              "--usage-db", str(d / "none.sqlite3"), "--bank", str(fx["live"])],
                             cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
        assert bad.returncode == 2 and "失敗" in bad.stderr, bad
    print("test_freeze_data OK")


def main() -> None:
    test_startup_check_errors_and_line()
    test_startup_check_refuses_live_bank()
    test_live_process_reads_pin()
    test_version_row_records_pin()
    test_freeze_data()
    print("ALL OK")


if __name__ == "__main__":
    main()
