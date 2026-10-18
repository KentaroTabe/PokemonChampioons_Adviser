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
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

from champions_agent.config import ADVICE_SCORING_MODES, TRANSITION_SENSITIVITY_ENV

REPO = Path(__file__).resolve().parent.parent
ENV_KEY = "ADVICE_SCORING_MODE"
SOURCE_KEY = "ADVICE_SCORING_MODE_SOURCE"
SENS_KEY = TRANSITION_SENSITIVITY_ENV            # "TRANSITION_SENSITIVITY"
SENS_SOURCE_KEY = SENS_KEY + "_SOURCE"
SENS_VALUES = ("0", "1")                         # 0 = 感度を計算しない、1 = 計算する
SENS_DEFAULT = "0"                               # 実機の起動経路の既定 (ファイルにも環境変数にも無いとき)
ALLOWED_KEYS = (ENV_KEY, SENS_KEY)
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


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="実機の採点方式の設定 (config/advice.local.env)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--resolve", action="store_true", help="起動スクリプト用: ADVICE_SCORING_MODE と設定元を KEY=VALUE で出す")
    g.add_argument("--show", action="store_true", help="設定値を人が読む形で出す")
    ap.add_argument("--file", default=str(LOCAL_FILE), help="設定ファイル (既定 config/advice.local.env)")
    ap.add_argument("--sensitivity", action="store_true",
                    help="--resolve で TRANSITION_SENSITIVITY と設定元も出す (実機の既定 0。advice_env.sh が使う)")
    a = ap.parse_args(argv)
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
        return 0
    try:
        r = resolve(text, os.environ.get(ENV_KEY), label)
        rs = resolve_sensitivity(text, os.environ.get(SENS_KEY), label) if a.sensitivity else None
    except AdviceModeError as err:
        print(f"[advice_env] 起動を中止: {err}", file=sys.stderr)
        return 2
    print(f"{ENV_KEY}={r['mode']}")
    print(f"{SOURCE_KEY}={r['source']}")
    if rs is not None:
        print(f"{SENS_KEY}={rs['value']}")
        print(f"{SENS_SOURCE_KEY}={rs['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
