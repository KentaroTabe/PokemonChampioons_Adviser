"""実機 (アドバイザーの常駐) の採点方式の設定 (方式 C: 追跡しないローカル設定ファイル。2026-10-16 レビュー、計画書 §8.27)。

設定ファイル config/advice.local.env (.gitignore 済み。例は config/advice.local.env.example) に `ADVICE_SCORING_MODE=transition`
のように書く。読むのは起動スクリプト (scripts/advice_env.sh を source する start_connection_test.sh / start_all_nohup.sh /
start_servers.sh / deploy.sh) だけで、決めた値を uvicorn の環境変数 ADVICE_SCORING_MODE と、設定元を
ADVICE_SCORING_MODE_SOURCE (file / env / default) に渡す。

**champions_agent/config.py はこのファイルを読まない** (環境変数だけを見る)。測定の子プロセス (usefulness_p1 の
check_advisor_player、arc_remeasure、学習) には実機用の設定を自動で適用しない (条件ごとの環境変数で明示する)。
server.py と操作パネルは表示のためにだけ読む (実効値は環境変数のまま)。

規則 (シェルと Python で二重に書かない。advice_env.sh は `python -m tools.advice_mode --resolve` を呼ぶ):
- ファイル: `KEY=VALUE` の行。`#` で始まる行と空行は無視、値の両端の引用符 ("..." / '...') は外す。認める鍵は
  ADVICE_SCORING_MODE と TRANSITION_SENSITIVITY だけ (他の鍵・`=` の無い行・同じ鍵の重複はエラー)。空の値は未設定と同じ
- 値は前後の空白を除いて小文字にし、champions_agent.config.ADVICE_SCORING_MODES のどれかでなければエラー
- 解決: ファイルも環境変数も無し → legacy (default) / ファイルだけ → file / 環境変数だけ → env /
  両方あって一致 → file (次の再起動でもファイルの値が続くので) / 両方あって不一致 → エラー (黙って片方を採用しない)

    python -m tools.advice_mode --resolve [--file PATH]   # 起動スクリプト用: KEY=VALUE を 2 行出す。エラーは終了コード 2
    python -m tools.advice_mode --resolve --sensitivity [--file PATH]   # 感度の 2 行も足して 4 行 (advice_env.sh が使う)
    python -m tools.advice_mode --show [--file PATH]      # 今の設定値を人が読む形で出す

2026-10-18 transition の感度 (レビュー 2026-10-18、計画書 §8.34): 同じファイルに TRANSITION_SENSITIVITY (値 0 / 1) も書ける。
実機の起動経路 (advice_env.sh) は TRANSITION_SENSITIVITY を必ず export する: ファイル → 環境変数 → 無ければ 0 (**実機の既定は
感度を計算しない**。config.TRANSITION_SENSITIVITY_ENABLED の既定 (計算する) は変えない = 測定の子プロセス・診断ツール・テストは
従来どおり)。設定元 (file / env / default) は TRANSITION_SENSITIVITY_SOURCE。規則は ADVICE_SCORING_MODE と同じ (矛盾・不正値は
エラー、値は 0 / 1 だけ)。感度は順位に使わない計算 (本採点と分離済み) なので、切っても候補・得点・順位・退避は変わらない
(tools.real_accept --compare-sensitivity で照合)。

2026-10-23 データの固定 (正式群の実機、計画書 §8.49): 同じファイルに ADVICE_USAGE_SNAPSHOT (使用率の snapshot id、正の整数)・
ADVICE_REAL_BANK (実戦バンクの写しのパス)・ADVICE_REAL_BANK_SHA16 (任意。写しの sha256 の先頭 16 桁、あれば起動時に照合) も書ける。
鍵ごとの規則は ADVICE_SCORING_MODE と同じ (ファイル → 環境変数、矛盾・不正値はエラー)。ADVICE_REAL_BANK の無い ADVICE_REAL_BANK_SHA16 もエラー。
どれも無ければ従来どおり (DB の最新 snapshot・logs/real_opponents/bank.json)。advice_env.sh は --resolve --data-pin の結果のうち
値のある鍵だけを uvicorn のサブシェルに export し (学習・測定の子プロセスに漏らさない)、設定元を ADVICE_DATA_PIN_SOURCE に渡す。
--data-pin は固定したデータが読めること (snapshot が DB にある・写しがある・sha16 が一致・元のバンクそのものではない) も起動の前に
確かめる (deploy.sh が稼働中のアドバイザーを止める前に止まる)。server も起動時に同じ検査をし (data_pin_startup_check)、不正なら
起動しない。助言側は advisor.data_pin で同じ環境変数を読む。写しは --freeze-data で作る (設定ファイルは書き換えない):

    python -m tools.advice_mode --resolve --sensitivity --data-pin [--file PATH] [--root DIR]   # advice_env.sh が使う (8 行)
    python -m tools.advice_mode --freeze-data [--out-dir DIR] [--usage-db DB] [--bank BANK]     # 写しを作り、書く 3 行を出す
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

from champions_agent.config import (ADVICE_SCORING_MODES, REAL_BANK_ENV, REAL_BANK_PATH, REAL_BANK_SHA_ENV,
                                    TRANSITION_SENSITIVITY_ENV, USAGE_SNAPSHOT_ENV)

REPO = Path(__file__).resolve().parent.parent
ENV_KEY = "ADVICE_SCORING_MODE"
SOURCE_KEY = "ADVICE_SCORING_MODE_SOURCE"
SENS_KEY = TRANSITION_SENSITIVITY_ENV            # "TRANSITION_SENSITIVITY"
SENS_SOURCE_KEY = SENS_KEY + "_SOURCE"
SENS_VALUES = ("0", "1")                         # 0 = 感度を計算しない、1 = 計算する
SENS_DEFAULT = "0"                               # 実機の起動経路の既定 (ファイルにも環境変数にも無いとき)
# 2026-10-23 データの固定 (正式群の実機。計画書 §8.49): 助言が読む使用率の snapshot と実戦バンクの写し
PIN_SNAPSHOT_KEY = USAGE_SNAPSHOT_ENV           # "ADVICE_USAGE_SNAPSHOT" (整数)
PIN_BANK_KEY = REAL_BANK_ENV                    # "ADVICE_REAL_BANK" (写しのパス)
PIN_SHA_KEY = REAL_BANK_SHA_ENV                 # "ADVICE_REAL_BANK_SHA16" (任意。sha256 の先頭 16 桁)
PIN_KEYS = (PIN_SNAPSHOT_KEY, PIN_BANK_KEY, PIN_SHA_KEY)
PIN_SOURCE_KEY = "ADVICE_DATA_PIN_SOURCE"
PIN_FREEZE_DIR_REL = "logs/pinned_data"         # --freeze-data の既定の書き出し先 (時刻のディレクトリを作る)
ALLOWED_KEYS = (ENV_KEY, SENS_KEY) + PIN_KEYS
SOURCES = ("file", "env", "default")
DEFAULT_MODE = "legacy"
LOCAL_FILE_REL = "config/advice.local.env"
LOCAL_FILE = REPO / LOCAL_FILE_REL
_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class AdviceModeError(ValueError):
    """設定の誤り (起動を止める)"""


# ------------------------------------------------------------------ 純粋な部分
def normalize(raw: Optional[str]) -> Optional[str]:
    """値の正規化 (前後の空白を除いて小文字。空・None は None = 未設定)"""
    v = (raw or "").strip().lower()
    return v or None


def check_mode(raw: Optional[str], where: str, modes: tuple = ADVICE_SCORING_MODES) -> Optional[str]:
    """値を検証して正規化した値を返す (未設定は None)。modes に無い値は AdviceModeError"""
    v = normalize(raw)
    if v is not None and v not in modes:
        raise AdviceModeError(f"{where}: {ENV_KEY}={raw!r} は使えない値 (使える値: {', '.join(modes)})")
    return v


def parse_local_env(text: str, label: str = LOCAL_FILE_REL) -> dict:
    """設定ファイルの構文解析 (純粋)。{鍵: 値 (引用符を外した生の値)}。認めない鍵・構文の誤り・重複は AdviceModeError"""
    out: dict = {}
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE_RE.match(s)
        if not m:
            raise AdviceModeError(f"{label}:{n}: KEY=VALUE の形ではない行: {s!r}")
        key, val = m.group(1), m.group(2).strip()
        if key not in ALLOWED_KEYS:
            raise AdviceModeError(f"{label}:{n}: 認めない鍵 {key} (書けるのは {', '.join(ALLOWED_KEYS)} だけ)")
        if key in out:
            raise AdviceModeError(f"{label}:{n}: {key} が 2 回書かれている")
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def file_mode(text: Optional[str], label: str = LOCAL_FILE_REL, modes: tuple = ADVICE_SCORING_MODES) -> Optional[str]:
    """ファイルの内容 → 設定値 (純粋。ファイルが無い (text=None)・鍵が無い・空の値は None)。誤りは AdviceModeError"""
    if text is None:
        return None
    return check_mode(parse_local_env(text, label).get(ENV_KEY), label, modes)


def check_sens(raw: Optional[str], where: str) -> Optional[str]:
    """感度の値を検証する (純粋。前後の空白を除いた "0" / "1"、未設定・空は None)。それ以外は AdviceModeError"""
    v = (raw or "").strip()
    if not v:
        return None
    if v not in SENS_VALUES:
        raise AdviceModeError(f"{where}: {SENS_KEY}={raw!r} は使えない値 (使える値: {', '.join(SENS_VALUES)})")
    return v


def file_sens(text: Optional[str], label: str = LOCAL_FILE_REL) -> Optional[str]:
    """ファイルの内容 → 感度の設定値 (純粋。ファイルが無い・鍵が無い・空の値は None)。誤りは AdviceModeError"""
    if text is None:
        return None
    return check_sens(parse_local_env(text, label).get(SENS_KEY), label)


def resolve_sensitivity(file_text: Optional[str], env_value: Optional[str], label: str = LOCAL_FILE_REL) -> dict:
    """実機の起動時の感度の設定を決める (純粋。規則は resolve と同じ)。{"value" ("0" / "1"), "source" (file / env / default),
    "file_value", "env_value"}。どちらにも無ければ SENS_DEFAULT (実機の既定 = 計算しない)。矛盾・不正値は AdviceModeError"""
    fv = file_sens(file_text, label)
    ev = check_sens(env_value, f"環境変数 {SENS_KEY}")
    if fv is not None and ev is not None and fv != ev:
        raise AdviceModeError(f"{label} の {SENS_KEY}={fv} と環境変数 {SENS_KEY}={ev} が矛盾している "
                              f"(どちらかに揃えるか、環境変数を外して起動し直す)")
    if fv is not None:
        value, source = fv, "file"
    elif ev is not None:
        value, source = ev, "env"
    else:
        value, source = SENS_DEFAULT, "default"
    return {"value": value, "source": source, "file_value": fv, "env_value": ev}


def resolve(file_text: Optional[str], env_value: Optional[str], label: str = LOCAL_FILE_REL,
            modes: tuple = ADVICE_SCORING_MODES) -> dict:
    """起動時に使う採点方式を決める (純粋)。file_text = 設定ファイルの内容 (無ければ None)、env_value = 起動時の環境変数
    ADVICE_SCORING_MODE の生の値 (無ければ None)。{"mode", "source" (file / env / default), "file_mode", "env_mode"}。
    不正値・認めない鍵・ファイルと環境変数の矛盾は AdviceModeError"""
    fm = file_mode(file_text, label, modes)
    em = check_mode(env_value, f"環境変数 {ENV_KEY}", modes)
    if fm is not None and em is not None and fm != em:
        raise AdviceModeError(f"{label} の {ENV_KEY}={fm} と環境変数 {ENV_KEY}={em} が矛盾している "
                              f"(どちらかに揃えるか、環境変数を外して起動し直す)")
    if fm is not None:
        mode, source = fm, "file"
    elif em is not None:
        mode, source = em, "env"
    else:
        mode, source = DEFAULT_MODE, "default"
    return {"mode": mode, "source": source, "file_mode": fm, "env_mode": em}


def source_of_env(env: dict) -> str:
    """稼働中のプロセスの設定元 (純粋)。advice_env.sh が書いた ADVICE_SCORING_MODE_SOURCE があればそれ、
    無ければ (advice_env.sh を通さない起動) 環境変数 ADVICE_SCORING_MODE の有無で env / default"""
    src = (env.get(SOURCE_KEY) or "").strip()
    if src in SOURCES:
        return src
    return "env" if normalize(env.get(ENV_KEY)) else "default"


def file_view(text: Optional[str], label: str = LOCAL_FILE_REL, modes: tuple = ADVICE_SCORING_MODES) -> dict:
    """表示用のファイルの状態 (純粋。例外を投げない)。{"path", "exists", "mode" (未設定は None), "configured"
    (未設定なら legacy、読めなければ None), "error"}"""
    try:
        fm = file_mode(text, label, modes)
        return {"path": label, "exists": text is not None, "mode": fm, "configured": fm or DEFAULT_MODE, "error": None}
    except AdviceModeError as e:
        return {"path": label, "exists": text is not None, "mode": None, "configured": None, "error": str(e)}


def configured_text(view: dict) -> str:
    """設定値の文言 (純粋)"""
    if view.get("error"):
        return f"読めない ({view['error']})"
    if view.get("mode"):
        return f"{view['mode']} ({view['path']})"
    return f"未設定 = {DEFAULT_MODE} ({view['path']} {'に鍵なし' if view.get('exists') else 'なし'})"


def mismatch(view: dict, running_mode: Optional[str]) -> bool:
    """設定値 (ファイル) と稼働中の実効値が違うか (純粋。読めない設定・稼働していないときは False)"""
    return bool(running_mode) and view.get("configured") is not None and view["configured"] != running_mode


def sens_source_of_env(env: dict) -> Optional[str]:
    """稼働中のプロセスの感度の設定元 (純粋)。advice_env.sh が書いた TRANSITION_SENSITIVITY_SOURCE があればそれ、無ければ
    (advice_env.sh を通さない起動) 環境変数 TRANSITION_SENSITIVITY があれば env、無ければ None (config の既定 = 計算する)"""
    src = (env.get(SENS_SOURCE_KEY) or "").strip()
    if src in SOURCES:
        return src
    return "env" if (env.get(SENS_KEY) or "").strip() else None


def sens_file_view(text: Optional[str], label: str = LOCAL_FILE_REL) -> dict:
    """表示用の感度の設定値 (純粋。例外を投げない)。{"path", "exists", "value" (未設定は None), "configured" (未設定なら
    SENS_DEFAULT、読めなければ None), "error"}。configured は advice_env.sh を通して起動したときに使われる値"""
    try:
        fv = file_sens(text, label)
        return {"path": label, "exists": text is not None, "value": fv, "configured": fv or SENS_DEFAULT, "error": None}
    except AdviceModeError as e:
        return {"path": label, "exists": text is not None, "value": None, "configured": None, "error": str(e)}


def sens_onoff(v) -> Optional[str]:
    """感度の値の表示 ("1" / True → on、"0" / False → off、None → None)"""
    if v is None:
        return None
    return "on" if v in (True, "1") else "off"


def sens_startup_line(enabled: bool, source: Optional[str], view: dict) -> str:
    """server の起動ログの感度の 1 行 (純粋)。enabled = 稼働中の実効値 (config.TRANSITION_SENSITIVITY_ENABLED)、
    source = sens_source_of_env の戻り値、view = sens_file_view の戻り値"""
    conf = (f"読めない ({view['error']})" if view.get("error") else
            f"{view['value']} ({view['path']})" if view.get("value") else
            f"未設定 = {SENS_DEFAULT} ({view['path']} {'に鍵なし' if view.get('exists') else 'なし'})")
    line = (f"transition の感度: 実効値 {sens_onoff(enabled)} / 設定元 {source or '記録なし (config の既定)'} / "
            f"設定値 {conf}")
    if not view.get("error") and view.get("configured") is not None and sens_onoff(view["configured"]) != sens_onoff(enabled):
        line += f" ⚠ 設定値 {view['configured']} と実効値が食い違う (advice_env.sh を通さずに起動した可能性)"
    return line


def startup_line(effective: str, source: str, view: dict) -> str:
    """server の起動ログの 1 行 (純粋): 設定値 (ファイル)・稼働中の実効値・設定元"""
    line = f"採点方式: 実効値 {effective} / 設定元 {source} / 設定値 {configured_text(view)}"
    if view.get("error"):
        line += " ⚠ 設定ファイルが読めない (起動スクリプトはこの状態では止まる。advice_env.sh を通さずに起動した可能性)"
    elif mismatch(view, effective):
        line += f" ⚠ 設定値 {view['configured']} と実効値が食い違う (advice_env.sh を通さずに起動した可能性)"
    return line


# ------------------------------------------------------------------ データの固定 (2026-10-23、純粋)
_SNAP_RE = re.compile(r"^[0-9]+$")
_SHA16_RE = re.compile(r"^[0-9a-f]{16}$")


def check_pin_value(key: str, raw: Optional[str], where: str) -> Optional[str]:
    """データの固定の鍵の値を検証して正規化する (純粋。未設定・空は None)。
    ADVICE_USAGE_SNAPSHOT = 正の整数 / ADVICE_REAL_BANK = 空でないパス / ADVICE_REAL_BANK_SHA16 = 16 桁の 16 進 (小文字にする)。
    それ以外は AdviceModeError"""
    v = (raw or "").strip()
    if not v:
        return None
    if key == PIN_SNAPSHOT_KEY:
        if not _SNAP_RE.match(v) or int(v) <= 0:
            raise AdviceModeError(f"{where}: {key}={raw!r} は snapshot id (正の整数) でない")
        return str(int(v))
    if key == PIN_SHA_KEY:
        if not _SHA16_RE.match(v.lower()):
            raise AdviceModeError(f"{where}: {key}={raw!r} は sha256 の先頭 16 桁 (16 進 16 文字) でない")
        return v.lower()
    if key == PIN_BANK_KEY:
        return v
    raise AdviceModeError(f"{where}: {key} はデータの固定の鍵でない")


def resolve_data_pin(file_text: Optional[str], env: Optional[dict], label: str = LOCAL_FILE_REL) -> dict:
    """実機の起動時のデータの固定を決める (純粋。鍵ごとの規則は resolve と同じ: ファイル → 環境変数 → 無ければ未設定 = 最新)。
    {"values": {鍵: 値 or None}, "sources": {鍵: file / env / None}, "source": 設定元 (file / env / file+env / default),
    "pinned": どれかを固定したか}。不正値・ファイルと環境変数の矛盾・ADVICE_REAL_BANK の無い ADVICE_REAL_BANK_SHA16 は AdviceModeError"""
    e = env or {}
    fvals = parse_local_env(file_text, label) if file_text is not None else {}
    values, sources = {}, {}
    for k in PIN_KEYS:
        fv = check_pin_value(k, fvals.get(k), label)
        ev = check_pin_value(k, e.get(k), f"環境変数 {k}")
        if fv is not None and ev is not None and fv != ev:
            raise AdviceModeError(f"{label} の {k}={fv} と環境変数 {k}={ev} が矛盾している "
                                  f"(どちらかに揃えるか、環境変数を外して起動し直す)")
        values[k] = fv if fv is not None else ev
        sources[k] = "file" if fv is not None else "env" if ev is not None else None
    if values[PIN_SHA_KEY] is not None and values[PIN_BANK_KEY] is None:
        raise AdviceModeError(f"{PIN_SHA_KEY} だけが書かれている ({PIN_BANK_KEY} (写しのパス) と一緒に書く。"
                              f"元のバンク {REAL_BANK_PATH} は対戦ごとに書き換わる)")
    used = [s for s in ("file", "env") if s in sources.values()]
    return {"values": values, "sources": sources, "source": "+".join(used) if used else "default",
            "pinned": any(v is not None for v in values.values())}


def pin_file_view(text: Optional[str], label: str = LOCAL_FILE_REL) -> dict:
    """表示用のデータの固定の設定値 (純粋。例外を投げない)。{"path", "exists", "values" ({鍵: 値 or None}、読めなければ None),
    "pinned", "error"}"""
    try:
        r = resolve_data_pin(text, {}, label)
        return {"path": label, "exists": text is not None, "values": r["values"], "pinned": r["pinned"], "error": None}
    except AdviceModeError as err:
        return {"path": label, "exists": text is not None, "values": None, "pinned": None, "error": str(err)}


def pin_env_values(env: dict) -> dict:
    """稼働中のプロセスの環境変数のデータの固定の値 (純粋。前後の空白を除く。空は None。検証はしない = 表示用)"""
    return {k: ((env.get(k) or "").strip() or None) for k in PIN_KEYS}


def pin_source_of_env(env: dict) -> Optional[str]:
    """稼働中のプロセスのデータの固定の設定元 (純粋)。advice_env.sh が書いた ADVICE_DATA_PIN_SOURCE があればそれ、無ければ
    (advice_env.sh を通さない起動) 鍵があれば env、無ければ None"""
    src = (env.get(PIN_SOURCE_KEY) or "").strip()
    if src in ("file", "env", "file+env", "default"):
        return src
    return "env" if any(pin_env_values(env).values()) else None


def pin_values_text(values: Optional[dict]) -> str:
    """データの固定の値の文言 (純粋)"""
    if not values or not any(values.values()):
        return f"なし (使用率は DB の最新 snapshot、実戦バンクは {REAL_BANK_PATH})"
    parts = [f"snapshot {values.get(PIN_SNAPSHOT_KEY) or '最新'}",
             f"バンク {values.get(PIN_BANK_KEY) or REAL_BANK_PATH}"]
    if values.get(PIN_SHA_KEY):
        parts.append(f"sha16 {values[PIN_SHA_KEY]}")
    return " / ".join(parts)


def pin_mismatch(view: dict, running: Optional[dict]) -> bool:
    """設定値 (ファイル) と稼働中の実効値が違うか (純粋。読めない設定・稼働していないときは False)。sha16 は大小文字を区別しない"""
    if running is None or view.get("error") or view.get("values") is None:
        return False
    norm = lambda d: {k: ((d.get(k) or "").lower() if k == PIN_SHA_KEY else (d.get(k) or "")) for k in PIN_KEYS}
    return norm(view["values"]) != norm(running)


def pin_startup_line(effective: dict, source: Optional[str], view: dict, verified: Optional[dict]) -> str:
    """server の起動ログのデータの固定の 1 行 (純粋)。effective = pin_env_values (稼働中の実効値)、source = pin_source_of_env、
    view = pin_file_view、verified = advisor.data_pin.verify_spec の戻り値 (固定していなければ None)"""
    conf = (f"読めない ({view['error']})" if view.get("error") else
            f"{pin_values_text(view['values'])} ({view['path']})" if view.get("pinned") else
            f"未設定 ({view['path']} {'に鍵なし' if view.get('exists') else 'なし'})")
    eff = pin_values_text(effective)
    if verified:
        got = []
        if verified.get("usage_snapshot") is not None:
            got.append(f"snapshot {verified['usage_snapshot']} は DB にある")
        if verified.get("bank_path"):
            got.append(f"バンクの sha16 {verified['bank_sha16']}"
                       + (" (指定と一致)" if effective.get(PIN_SHA_KEY) else " (指定なし。この値を記録する)"))
        eff += f" [{', '.join(got)}]"
    line = f"データの固定: 実効値 {eff} / 設定元 {source or '記録なし'} / 設定値 {conf}"
    if view.get("error"):
        line += " ⚠ 設定ファイルが読めない (advice_env.sh を通さずに起動した可能性)"
    elif pin_mismatch(view, effective):
        line += " ⚠ 設定値と実効値が食い違う (advice_env.sh を通さずに起動したか、設定を変えて再起動していない)"
    return line


# ------------------------------------------------------------------ 副作用 (ファイル・環境変数)
def read_local_text(path: Path = LOCAL_FILE) -> Optional[str]:
    """設定ファイルの内容 (無ければ None)"""
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else None


def server_startup_check(env: Optional[dict] = None, path: Path = LOCAL_FILE) -> str:
    """server.py の起動時の検査と起動ログの 1 行。環境変数 ADVICE_SCORING_MODE が使えない値なら SystemExit
    (config.py は警告して legacy にするが、実機では黙って legacy で動かさない)。実効値は環境変数のまま (ファイルは表示だけ)"""
    e = os.environ if env is None else env
    try:
        effective = check_mode(e.get(ENV_KEY), f"環境変数 {ENV_KEY}") or DEFAULT_MODE
    except AdviceModeError as err:
        raise SystemExit(f"[server] 起動を中止: {err}")
    try:
        text = read_local_text(path)
    except OSError as err:
        text, view = None, {"path": str(path), "exists": True, "mode": None, "configured": None, "error": str(err)}
    else:
        view = file_view(text)
    return startup_line(effective, source_of_env(e), view)


def sens_startup_check(env: Optional[dict] = None, path: Path = LOCAL_FILE) -> str:
    """server.py の起動ログの感度の 1 行 (表示だけ。実効値は config.TRANSITION_SENSITIVITY_ENABLED = 環境変数で決まる)"""
    from champions_agent import config as _cfg
    e = os.environ if env is None else env
    try:
        view = sens_file_view(read_local_text(path))
    except OSError as err:
        view = {"path": str(path), "exists": True, "value": None, "configured": None, "error": str(err)}
    return sens_startup_line(bool(_cfg.TRANSITION_SENSITIVITY_ENABLED), sens_source_of_env(e), view)


def _pin_spec(values: dict, env: dict) -> dict:
    """データの固定の値 → advisor.data_pin の spec の形 (使用率 DB は環境変数 ADVICE_USAGE_DB があればそれ、無ければ既定)"""
    from champions_agent.config import USAGE_DB_ENV
    snap = values.get(PIN_SNAPSHOT_KEY)
    return {"usage_db": (env.get(USAGE_DB_ENV) or "").strip() or None, "usage_snapshot": int(snap) if snap else None,
            "bank_path": values.get(PIN_BANK_KEY), "bank_sha16": values.get(PIN_SHA_KEY)}


def precheck_data_pin(values: dict, root: Path, env: Optional[dict] = None) -> Optional[dict]:
    """固定したデータが読めることを起動の前に確かめる (副作用: 読み取り。deploy.sh が稼働中のアドバイザーを止める前に呼ぶ)。
    root = リポジトリのルート (起動スクリプトの cwd。相対パスと既定の使用率 DB・元のバンクはここから)。固定が無ければ None。
    snapshot が DB に無い・バンクが無い・sha16 が違う・元のバンクそのものを指す → AdviceModeError"""
    from advisor import data_pin as DP
    e = os.environ if env is None else env
    if not any(values.get(k) for k in PIN_KEYS):
        return None
    root = Path(root)
    try:
        return DP.verify_spec(_pin_spec(values, e), default_db=root / DP.DEFAULT_USAGE_DB.relative_to(DP.REPO),
                              live_bank=root / REAL_BANK_PATH, cwd=root)
    except DP.InputsPinError as err:
        raise AdviceModeError(str(err))


def data_pin_startup_check(env: Optional[dict] = None, path: Path = LOCAL_FILE) -> str:
    """server.py の起動時のデータの固定の検査と起動ログの 1 行 (2026-10-23)。実効値は環境変数 (advice_env.sh が uvicorn にだけ渡す)。
    値が不正・指定 snapshot が DB に無い・写しのバンクが無い / sha16 が違う・元のバンクそのものを指す → SystemExit
    (最新へ黙って退避しない)。助言側 (advisor.sets / infer / team_advice / real_prior / ev_infer) は advisor.data_pin で同じ環境変数を読む
    (ev_infer は 2026-10-30 から。ここで確かめる DB・snapshot と同じものを読むので、フレームごとの InputsPinError は起きない)"""
    from advisor import data_pin as DP
    e = os.environ if env is None else env
    try:
        vals = {k: check_pin_value(k, e.get(k), f"環境変数 {k}") for k in PIN_KEYS}
        if vals[PIN_SHA_KEY] is not None and vals[PIN_BANK_KEY] is None:
            raise AdviceModeError(f"環境変数 {PIN_SHA_KEY} だけがある ({PIN_BANK_KEY} と一緒に渡す)")
        spec = _pin_spec(vals, e)
        verified = None
        if spec["usage_db"] or spec["usage_snapshot"] is not None or spec["bank_path"]:
            verified = DP.verify_spec(spec, live_bank=DP.REPO / REAL_BANK_PATH)
    except (AdviceModeError, DP.InputsPinError) as err:
        raise SystemExit(f"[server] 起動を中止 (データの固定): {err}")
    try:
        view = pin_file_view(read_local_text(path))
    except OSError as err:
        view = {"path": str(path), "exists": True, "values": None, "pinned": None, "error": str(err)}
    return pin_startup_line(pin_env_values(e), pin_source_of_env(e), view, verified)


def freeze_data(out_root: Path, usage_db: Path, bank: Path, stamp: Optional[str] = None) -> dict:
    """正式群の開始時のデータの写しを作る (副作用: out_root/<時刻>/bank.json を書く。設定ファイルは書き換えない)。
    使用率は DB の最新 snapshot id を読むだけ (DB は読み取り専用で開く。snapshot 以下の行は DB に残るので写さない)。
    バンクは複製の前後で元の sha256 が変わらず写しと一致することを確かめる (書き換え中なら RuntimeError)。
    戻り値 {"usage_snapshot", "usage_db", "bank_src", "bank_path", "bank_sha16", "dir", "lines" (advice.local.env に書く 3 行)}"""
    import hashlib
    import shutil
    import sqlite3
    import time as _time
    usage_db, bank = Path(usage_db), Path(bank)
    if not usage_db.exists():
        raise FileNotFoundError(f"使用率 DB {usage_db} が無い")
    if not bank.exists():
        raise FileNotFoundError(f"実戦バンク {bank} が無い")
    conn = sqlite3.connect(f"file:{usage_db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"使用率 DB {usage_db} に snapshot が無い")
    stamp = stamp or _time.strftime("%Y%m%d_%H%M%S")
    d = Path(out_root) / stamp
    if d.exists():
        raise FileExistsError(f"{d} が既にある (上書きしない)")
    d.mkdir(parents=True)
    dst = d / "bank.json"
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    before = sha(bank)
    shutil.copy2(bank, dst)
    after, copied = sha(bank), sha(dst)
    if not (before == after == copied):
        raise RuntimeError(f"{bank} の複製の前後で sha256 が揃わない (書き換え中の可能性。もう一度実行する)")
    snap, path_s, sha16 = int(row[0]), str(dst.resolve()), copied[:16]
    return {"usage_snapshot": snap, "usage_db": str(usage_db), "bank_src": str(bank), "bank_path": path_s, "bank_sha16": sha16,
            "dir": str(d), "lines": [f"{PIN_SNAPSHOT_KEY}={snap}", f"{PIN_BANK_KEY}={path_s}", f"{PIN_SHA_KEY}={sha16}"]}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="実機の採点方式の設定 (config/advice.local.env)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--resolve", action="store_true", help="起動スクリプト用: ADVICE_SCORING_MODE と設定元を KEY=VALUE で出す")
    g.add_argument("--show", action="store_true", help="設定値を人が読む形で出す")
    g.add_argument("--freeze-data", action="store_true",
                   help="正式群の開始時: 使用率の最新 snapshot id とバンクの写しを作り、advice.local.env に書く 3 行を出す (ファイルは書き換えない)")
    ap.add_argument("--file", default=str(LOCAL_FILE), help="設定ファイル (既定 config/advice.local.env)")
    ap.add_argument("--sensitivity", action="store_true",
                    help="--resolve で TRANSITION_SENSITIVITY と設定元も出す (実機の既定 0。advice_env.sh が使う)")
    ap.add_argument("--data-pin", action="store_true",
                    help="--resolve でデータの固定 (ADVICE_USAGE_SNAPSHOT / ADVICE_REAL_BANK / ADVICE_REAL_BANK_SHA16) と設定元も出し、"
                         "固定したデータが読めることを確かめる (advice_env.sh が使う)")
    ap.add_argument("--root", default=None,
                    help="--data-pin の検査の基準 (リポジトリのルート。既定は今のディレクトリ = 起動スクリプトの cwd)")
    ap.add_argument("--out-dir", default=None, help=f"--freeze-data の書き出し先 (既定 {PIN_FREEZE_DIR_REL})")
    ap.add_argument("--usage-db", default=None, help="--freeze-data で snapshot id を読む使用率 DB (既定 このリポジトリの DB)")
    ap.add_argument("--bank", default=None, help=f"--freeze-data で写す実戦バンク (既定 このリポジトリの {REAL_BANK_PATH})")
    a = ap.parse_args(argv)
    if a.freeze_data:
        from advisor.data_pin import DEFAULT_USAGE_DB
        try:
            r = freeze_data(Path(a.out_dir) if a.out_dir else REPO / PIN_FREEZE_DIR_REL,
                            Path(a.usage_db) if a.usage_db else DEFAULT_USAGE_DB,
                            Path(a.bank) if a.bank else REPO / REAL_BANK_PATH)
        except (OSError, RuntimeError, ValueError) as err:
            print(f"[freeze-data] 失敗: {err}", file=sys.stderr)
            return 2
        print(f"# 使用率: {r['usage_db']} の最新 snapshot {r['usage_snapshot']}")
        print(f"# 実戦バンク: {r['bank_src']} → {r['bank_path']} (sha16 {r['bank_sha16']})")
        print(f"# 次の 3 行を {LOCAL_FILE_REL} に追加する (方式の切替のときも残し、正式群が終わったら消す):")
        for ln in r["lines"]:
            print(ln)
        return 0
    path = Path(a.file)
    label = LOCAL_FILE_REL if path.resolve() == LOCAL_FILE.resolve() else str(path)
    try:
        text = read_local_text(path)
    except OSError as err:
        print(f"[advice_env] {label} を読めない: {err}", file=sys.stderr)
        return 2
    if a.show:
        print(f"設定値: {configured_text(file_view(text, label))}")
        sv = sens_file_view(text, label)
        print(f"感度の設定値: {sv['error'] and ('読めない (' + sv['error'] + ')') or sv['configured']}")
        pv = pin_file_view(text, label)
        print(f"データの固定の設定値: {('読めない (' + pv['error'] + ')') if pv['error'] else pin_values_text(pv['values'])}")
        return 0
    try:
        r = resolve(text, os.environ.get(ENV_KEY), label)
        rs = resolve_sensitivity(text, os.environ.get(SENS_KEY), label) if a.sensitivity else None
        rp = None
        if a.data_pin:
            rp = resolve_data_pin(text, dict(os.environ), label)
            precheck_data_pin(rp["values"], Path(a.root) if a.root else Path.cwd())
    except AdviceModeError as err:
        print(f"[advice_env] 起動を中止: {err}", file=sys.stderr)
        return 2
    print(f"{ENV_KEY}={r['mode']}")
    print(f"{SOURCE_KEY}={r['source']}")
    if rs is not None:
        print(f"{SENS_KEY}={rs['value']}")
        print(f"{SENS_SOURCE_KEY}={rs['source']}")
    if rp is not None:
        for k in PIN_KEYS:
            print(f"{k}={rp['values'][k] or ''}")
        print(f"{PIN_SOURCE_KEY}={rp['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
