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

2026-10-31 モデルの実体の固定 (KNOWN_ISSUES E-inh-4、レビュー 2026-10-31 判断 4): 同じファイルに ADVICE_RL_POLICY (RL 方策の .zip の
パス)・ADVICE_RL_POLICY_SHA16 (任意)・ADVICE_SELECTION_MODEL (助言の選出モデルの .pt のパス)・ADVICE_SELECTION_MODEL_SHA16 (任意) も書ける。
規則はデータの固定と同じ (ファイル → 環境変数、矛盾・不正値はエラー、パスの無い sha16 はエラー)。書けば助言はそのファイルだけを読み、
無い・sha16 が違えば止める (advisor.data_pin.InputsPinError。ema → best → 無印 の順送り、登録チーム向け → 配布版 の退避をしない)。
書かなければ従来どおり (退避したことは version 行の runtime に残る)。advice_env.sh は --model-pin の結果のうち値のある鍵だけを
uvicorn のサブシェルに export し、設定元を ADVICE_MODEL_PIN_SOURCE に渡す。--model-pin はファイルがあること・sha16 の一致も起動の前に
確かめる。server も起動時に同じ検査をする (model_pin_startup_check)。写しは --freeze-data --with-models で作る (今読まれるはずの
RL 方策と、登録チームに使われるはずの選出モデルを写し、書く 4 行を足して出す):

    python -m tools.advice_mode --resolve --sensitivity --data-pin --model-pin [--file PATH] [--root DIR]   # advice_env.sh (13 行)
    python -m tools.advice_mode --freeze-data --with-models [--rl-policy ZIP] [--selection-model PT]       # 3 行 + 4 行

2026-10-31 版の固定の bundle (追加レビュー判断 2・4、advisor/pin_bundle.py): 上の写し (--freeze-data --with-models) は RL の zip と
選出の pt だけで、選出の埋め込み・meta と使用率 DB の中身は配備先を読み続ける。正式比較は --freeze-bundle で 1 つの dir に一組で
固定する (RL 方策、選出モデル + 埋め込み + meta + 条件付きモデル、使用率 DB の必要な snapshot を抽出した sqlite、バンク、登録、
manifest.json)。設定ファイルには ADVICE_PIN_BUNDLE=<dir> の 1 行だけを書く (個別の 7 行は manifest から埋まる。書くなら値を揃える。
違えば起動しない)。構築専用の選出モデルを別構築に適用する汎化試験は ADVICE_PIN_ALLOW_OTHER_TEAM=1 (既定は止める)。
advice_env.sh は --bundle-pin で両方を解決・検査して uvicorn にだけ export する。server は起動時に bundle 全体を検査する
(bundle_startup_check。不正なら起動しない)。

    python -m tools.advice_mode --freeze-bundle [--out-dir DIR] [--rl-policy ZIP] [--selection-model PT [--selection-kind general|team
        --selection-kind-basis "根拠" [--selection-team ID,...] [--selection-sets TEAM.txt]]] [--usage-db DB] [--usage-snapshot N]
        [--bank BANK] [--registry JSON]

2026-11-02 (レビュー判断 1・2): 選出モデルの種別は学習履歴 (モデルと同じ dir の <stem>_meta.json の provenance。meta の model_sha16 が
モデルと一致するときだけ) で決める。無ければ「不明」(formal_ok False = 正式比較に使わない、適用範囲未確認の診断)。明示は根拠が必須。
# 行に種別・根拠・型の前提 (fixed / none / unknown)・正式比較の可否が出る。
    python -m tools.advice_mode --resolve ... --bundle-pin [--file PATH] [--root DIR]   # advice_env.sh (末尾に 3 行)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

from champions_agent.config import (ADVICE_SCORING_MODES, PIN_ALLOW_OTHER_TEAM_ENV, PIN_BUNDLE_ENV, REAL_BANK_ENV, REAL_BANK_PATH,
                                    REAL_BANK_SHA_ENV, RL_POLICY_PIN_ENV, RL_POLICY_SHA_ENV, SELECTION_MODEL_PIN_ENV,
                                    SELECTION_MODEL_SHA_ENV, TRANSITION_SENSITIVITY_ENV, USAGE_SNAPSHOT_ENV)

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
# 2026-10-31 モデルの実体の固定 (E-inh-4): RL 方策と助言の選出モデルのファイル (パスと任意の sha16)
MODEL_RL_KEY = RL_POLICY_PIN_ENV                # "ADVICE_RL_POLICY"
MODEL_RL_SHA_KEY = RL_POLICY_SHA_ENV            # "ADVICE_RL_POLICY_SHA16"
MODEL_SEL_KEY = SELECTION_MODEL_PIN_ENV         # "ADVICE_SELECTION_MODEL"
MODEL_SEL_SHA_KEY = SELECTION_MODEL_SHA_ENV     # "ADVICE_SELECTION_MODEL_SHA16"
MODEL_PIN_KEYS = (MODEL_RL_KEY, MODEL_RL_SHA_KEY, MODEL_SEL_KEY, MODEL_SEL_SHA_KEY)
MODEL_SHA_OF = {MODEL_RL_SHA_KEY: MODEL_RL_KEY, MODEL_SEL_SHA_KEY: MODEL_SEL_KEY}   # sha16 の鍵 → そのパスの鍵
MODEL_PIN_SOURCE_KEY = "ADVICE_MODEL_PIN_SOURCE"
# 2026-10-31 版の固定の bundle: bundle の dir と、構築専用モデルを別構築に適用する汎化試験の明示
BUNDLE_KEY = PIN_BUNDLE_ENV                    # "ADVICE_PIN_BUNDLE"
ALLOW_OTHER_KEY = PIN_ALLOW_OTHER_TEAM_ENV     # "ADVICE_PIN_ALLOW_OTHER_TEAM" (0 / 1)
BUNDLE_KEYS = (BUNDLE_KEY, ALLOW_OTHER_KEY)
BUNDLE_SOURCE_KEY = "ADVICE_PIN_BUNDLE_SOURCE"
ALLOWED_KEYS = (ENV_KEY, SENS_KEY) + PIN_KEYS + MODEL_PIN_KEYS + BUNDLE_KEYS
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
    if key in MODEL_SHA_OF:
        if not _SHA16_RE.match(v.lower()):
            raise AdviceModeError(f"{where}: {key}={raw!r} は sha256 の先頭 16 桁 (16 進 16 文字) でない")
        return v.lower()
    if key in (MODEL_RL_KEY, MODEL_SEL_KEY):
        return v
    if key == BUNDLE_KEY:
        return v
    if key == ALLOW_OTHER_KEY:
        if v not in ("0", "1"):
            raise AdviceModeError(f"{where}: {key}={raw!r} は 0 / 1 のどちらかで書く")
        return "1" if v == "1" else None
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


def resolve_model_pin(file_text: Optional[str], env: Optional[dict], label: str = LOCAL_FILE_REL) -> dict:
    """実機の起動時のモデルの固定を決める (純粋。規則は resolve_data_pin と同じ: 鍵ごとにファイル → 環境変数 → 無ければ未設定 = 従来どおり)。
    {"values": {鍵: 値 or None}, "sources": {鍵: file / env / None}, "source": file / env / file+env / default, "pinned"}。
    不正値・ファイルと環境変数の矛盾・パスの無い sha16 は AdviceModeError"""
    e = env or {}
    fvals = parse_local_env(file_text, label) if file_text is not None else {}
    values, sources = {}, {}
    for k in MODEL_PIN_KEYS:
        fv = check_pin_value(k, fvals.get(k), label)
        ev = check_pin_value(k, e.get(k), f"環境変数 {k}")
        if fv is not None and ev is not None and fv != ev:
            raise AdviceModeError(f"{label} の {k}={fv} と環境変数 {k}={ev} が矛盾している "
                                  f"(どちらかに揃えるか、環境変数を外して起動し直す)")
        values[k] = fv if fv is not None else ev
        sources[k] = "file" if fv is not None else "env" if ev is not None else None
    for sha_key, path_key in MODEL_SHA_OF.items():
        if values[sha_key] is not None and values[path_key] is None:
            raise AdviceModeError(f"{sha_key} だけが書かれている ({path_key} (ファイルのパス) と一緒に書く)")
    used = [s for s in ("file", "env") if s in sources.values()]
    return {"values": values, "sources": sources, "source": "+".join(used) if used else "default",
            "pinned": any(v is not None for v in values.values())}


def resolve_bundle_pin(file_text: Optional[str], env: Optional[dict], label: str = LOCAL_FILE_REL) -> dict:
    """実機の起動時の版の固定の bundle を決める (純粋。規則は resolve_data_pin と同じ: 鍵ごとにファイル → 環境変数 → 無ければ未設定)。
    {"values": {ADVICE_PIN_BUNDLE, ADVICE_PIN_ALLOW_OTHER_TEAM ("1" or None)}, "sources", "source", "pinned" (bundle があるか)}。
    不正値・ファイルと環境変数の矛盾・bundle の無い ADVICE_PIN_ALLOW_OTHER_TEAM=1 は AdviceModeError"""
    e = env or {}
    fvals = parse_local_env(file_text, label) if file_text is not None else {}
    values, sources = {}, {}
    for k in BUNDLE_KEYS:
        fv = check_pin_value(k, fvals.get(k), label)
        ev = check_pin_value(k, e.get(k), f"環境変数 {k}")
        if fv is not None and ev is not None and fv != ev:
            raise AdviceModeError(f"{label} の {k}={fv} と環境変数 {k}={ev} が矛盾している "
                                  f"(どちらかに揃えるか、環境変数を外して起動し直す)")
        values[k] = fv if fv is not None else ev
        sources[k] = "file" if fv is not None else "env" if ev is not None else None
    if values[ALLOW_OTHER_KEY] and not values[BUNDLE_KEY]:
        raise AdviceModeError(f"{ALLOW_OTHER_KEY}=1 だけが書かれている ({BUNDLE_KEY} (版の固定の bundle) と一緒に使う)")
    used = [s for s in ("file", "env") if s in sources.values()]
    return {"values": values, "sources": sources, "source": "+".join(used) if used else "default",
            "pinned": bool(values[BUNDLE_KEY])}


def bundle_values_text(values: Optional[dict]) -> str:
    """版の固定の bundle の値の文言 (純粋)"""
    if not values or not values.get(BUNDLE_KEY):
        return "なし (個別の固定の鍵だけ。無ければ従来どおり)"
    return (f"bundle {values[BUNDLE_KEY]}" +
            (" / 構築専用モデルの別構築への適用 (汎化試験) 許可" if values.get(ALLOW_OTHER_KEY) else ""))


def bundle_file_view(text: Optional[str], label: str = LOCAL_FILE_REL) -> dict:
    """表示用の版の固定の bundle の設定値 (純粋。例外を投げない)"""
    try:
        r = resolve_bundle_pin(text, {}, label)
        return {"path": label, "exists": text is not None, "values": r["values"], "pinned": r["pinned"], "error": None}
    except AdviceModeError as err:
        return {"path": label, "exists": text is not None, "values": None, "pinned": None, "error": str(err)}


def model_pin_values_text(values: Optional[dict]) -> str:
    """モデルの固定の値の文言 (純粋)"""
    if not values or not any(values.values()):
        return "なし (RL 方策は読み込み順 ema → best → 無印、選出モデルは 試用 Package → 登録チーム向け → 配布版)"
    parts = []
    for name, pk, sk in (("RL 方策", MODEL_RL_KEY, MODEL_RL_SHA_KEY), ("選出モデル", MODEL_SEL_KEY, MODEL_SEL_SHA_KEY)):
        if values.get(pk):
            parts.append(f"{name} {values[pk]}" + (f" (sha16 {values[sk]})" if values.get(sk) else ""))
        else:
            parts.append(f"{name} 固定なし")
    return " / ".join(parts)


def model_pin_file_view(text: Optional[str], label: str = LOCAL_FILE_REL) -> dict:
    """表示用のモデルの固定の設定値 (純粋。例外を投げない)。{"path", "exists", "values", "pinned", "error"}"""
    try:
        r = resolve_model_pin(text, {}, label)
        return {"path": label, "exists": text is not None, "values": r["values"], "pinned": r["pinned"], "error": None}
    except AdviceModeError as err:
        return {"path": label, "exists": text is not None, "values": None, "pinned": None, "error": str(err)}


def model_spec_of(values: dict) -> dict:
    """モデルの固定の値 → advisor.data_pin.model_spec_from_env の形 (純粋)"""
    return {"rl_policy": values.get(MODEL_RL_KEY), "rl_policy_sha16": values.get(MODEL_RL_SHA_KEY),
            "selection_model": values.get(MODEL_SEL_KEY), "selection_model_sha16": values.get(MODEL_SEL_SHA_KEY)}


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
    if (e.get(BUNDLE_KEY) or "").strip():
        return _bundle_part_line("データの固定", e, path)
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


def precheck_model_pin(values: dict, root: Path) -> Optional[dict]:
    """固定したモデルのファイルが読めることを起動の前に確かめる (副作用: 読み取り)。相対パスは root から。固定が無ければ None。
    ファイルが無い・sha16 が違う → AdviceModeError"""
    from advisor import data_pin as DP
    if not any(values.get(k) for k in MODEL_PIN_KEYS):
        return None
    try:
        return DP.verify_model_spec(model_spec_of(values), cwd=Path(root))
    except DP.InputsPinError as err:
        raise AdviceModeError(str(err))


def model_pin_startup_check(env: Optional[dict] = None, path: Path = LOCAL_FILE) -> str:
    """server.py の起動時のモデルの固定の検査と起動ログの 1 行 (2026-10-31 E-inh-4)。実効値は環境変数 (advice_env.sh が uvicorn にだけ
    渡す)。値が不正・ファイルが無い・sha16 が違う → SystemExit (別のファイルへ退避しない)"""
    from advisor import data_pin as DP
    e = os.environ if env is None else env
    if (e.get(BUNDLE_KEY) or "").strip():
        return _bundle_part_line("モデルの固定", e, path)
    try:
        vals = resolve_model_pin(None, e)["values"]
        verified = None
        if any(vals.values()):
            verified = DP.verify_model_spec(model_spec_of(vals))
    except (AdviceModeError, DP.InputsPinError) as err:
        raise SystemExit(f"[server] 起動を中止 (モデルの固定): {err}")
    try:
        view = model_pin_file_view(read_local_text(path))
    except OSError as err:
        view = {"path": str(path), "exists": True, "values": None, "pinned": None, "error": str(err)}
    src = (e.get(MODEL_PIN_SOURCE_KEY) or "").strip() or ("env" if any(vals.values()) else None)
    eff = model_pin_values_text(vals)
    if verified:
        got = [f"{k} sha16 {verified[k + '_sha16']}" for k in ("rl_policy", "selection_model") if k in verified]
        eff += f" [{', '.join(got)}]"
    conf = (f"読めない ({view['error']})" if view.get("error") else
            f"{model_pin_values_text(view['values'])} ({view['path']})" if view.get("pinned") else
            f"未設定 ({view['path']} {'に鍵なし' if view.get('exists') else 'なし'})")
    line = f"モデルの固定: 実効値 {eff} / 設定元 {src or '記録なし'} / 設定値 {conf}"
    if not view.get("error") and view.get("values") is not None:
        norm = lambda d: {k: (d.get(k) or "").lower() if k in MODEL_SHA_OF else (d.get(k) or "") for k in MODEL_PIN_KEYS}
        if norm(view["values"]) != norm(vals):
            line += " ⚠ 設定値と実効値が食い違う (advice_env.sh を通さずに起動したか、設定を変えて再起動していない)"
    return line


def _bundle_env_spec(e: dict) -> dict:
    """環境変数 → advisor.data_pin.verify_spec の spec (bundle と個別の鍵。使用率 DB は ADVICE_USAGE_DB)"""
    from champions_agent.config import USAGE_DB_ENV
    from advisor import data_pin as DP
    spec = dict(DP.spec_from_env(e))
    spec.update(DP.model_spec_from_env(e))
    spec["usage_db"] = (e.get(USAGE_DB_ENV) or "").strip() or None
    spec["bundle"] = (e.get(BUNDLE_KEY) or "").strip() or None
    return spec


def _bundle_part_line(name: str, e: dict, path: Path) -> str:
    """bundle を使うときのデータの固定・モデルの固定の起動ログの 1 行 (検査は bundle_startup_check がまとめて行う。値は manifest から)"""
    from advisor import data_pin as DP
    from advisor import pin_bundle as PB
    try:
        vals = PB.effective_spec(e)
    except DP.InputsPinError as err:
        raise SystemExit(f"[server] 起動を中止 ({name}): {err}")
    if name == "データの固定":
        eff = f"snapshot {vals['usage_snapshot']} (bundle の抽出 DB) / バンク {vals['bank_path']} (sha16 {vals['bank_sha16']})"
    else:
        eff = (f"RL 方策 {vals['rl_policy']} (sha16 {vals['rl_policy_sha16']}) / 選出モデル {vals['selection_model']} "
               f"(sha16 {vals['selection_model_sha16']})")
    return f"{name}: 実効値 {eff} [bundle {e.get(BUNDLE_KEY)} から。照合は版の固定 (bundle) の行]"


def precheck_bundle_pin(values: dict, root: Path, data_values: Optional[dict] = None, model_values: Optional[dict] = None,
                        env: Optional[dict] = None) -> Optional[dict]:
    """bundle 全体を起動の前に確かめる (副作用: 読み取り)。values = resolve_bundle_pin の values、data_values / model_values =
    resolve_data_pin / resolve_model_pin の values (個別の鍵。bundle と一致しなければエラー)。相対パスは root から。
    bundle が無ければ None。不足・sha 不一致・矛盾は AdviceModeError"""
    from advisor import data_pin as DP
    from champions_agent.config import USAGE_DB_ENV
    if not values.get(BUNDLE_KEY):
        return None
    e = os.environ if env is None else env
    dv, mv = data_values or {}, model_values or {}
    snap = dv.get(PIN_SNAPSHOT_KEY)
    spec = {"bundle": values[BUNDLE_KEY], "usage_db": (e.get(USAGE_DB_ENV) or "").strip() or None,
            "usage_snapshot": int(snap) if snap else None, "bank_path": dv.get(PIN_BANK_KEY), "bank_sha16": dv.get(PIN_SHA_KEY)}
    spec.update(model_spec_of(mv))
    try:
        return DP.verify_spec(spec, default_db=Path(root) / DP.DEFAULT_USAGE_DB.relative_to(DP.REPO),
                              live_bank=Path(root) / REAL_BANK_PATH, cwd=Path(root))
    except DP.InputsPinError as err:
        raise AdviceModeError(str(err))


def bundle_startup_check(env: Optional[dict] = None, path: Path = LOCAL_FILE) -> str:
    """server.py の起動時の版の固定の bundle の検査と起動ログの 1 行 (2026-10-31)。bundle 全体 (manifest の形、全ファイルの sha256、
    個別の鍵との整合、特徴量の版、登録) を確かめ、合わなければ SystemExit (配備先へ退避しない)。bundle が無ければ「なし」"""
    from advisor import data_pin as DP
    from advisor import pin_bundle as PB
    e = os.environ if env is None else env
    try:
        vals = resolve_bundle_pin(None, e)["values"]
        summary = None
        if vals[BUNDLE_KEY]:
            summary = DP.verify_spec(_bundle_env_spec(e), live_bank=DP.REPO / REAL_BANK_PATH)["bundle"]
            PB.allow_other_team(e)
    except (AdviceModeError, DP.InputsPinError) as err:
        raise SystemExit(f"[server] 起動を中止 (版の固定の bundle): {err}")
    try:
        view = bundle_file_view(read_local_text(path))
    except OSError as err:
        view = {"path": str(path), "exists": True, "values": None, "pinned": None, "error": str(err)}
    src = (e.get(BUNDLE_SOURCE_KEY) or "").strip() or ("env" if vals[BUNDLE_KEY] else None)
    eff = bundle_values_text(vals)
    if summary:
        eff = "bundle " + PB.describe(summary) + (" / 汎化試験の適用 許可" if vals[ALLOW_OTHER_KEY] else "")
    conf = (f"読めない ({view['error']})" if view.get("error") else
            f"{bundle_values_text(view['values'])} ({view['path']})" if view.get("pinned") else
            f"未設定 ({view['path']} {'に鍵なし' if view.get('exists') else 'なし'})")
    line = f"版の固定 (bundle): 実効値 {eff} / 設定元 {src or '記録なし'} / 設定値 {conf}"
    if not view.get("error") and view.get("values") is not None:
        norm = lambda d: {k: (d.get(k) or "") for k in BUNDLE_KEYS}
        if norm(view["values"]) != norm(vals):
            line += " ⚠ 設定値と実効値が食い違う (advice_env.sh を通さずに起動したか、設定を変えて再起動していない)"
    return line


def default_model_sources() -> dict:
    """--freeze-data --with-models の既定の写し元 (副作用: ファイルの有無・登録の読み取り): RL 方策は advisor.rl_bridge の読み込み順
    (固定しないとき) で読まれるはずのファイル、選出モデルは登録チーム (直近の選出ロスター) に使われるはずのファイル
    (selection_dispatch.advisor_model_choice、固定しないとき)。{"rl_policy": Path, "selection_model": Path, "selection_source": id}"""
    from advisor import rl_bridge as RB
    from advisor.versions import _registered_team
    from champions_agent.agent.selection_dispatch import _model_chain
    order = RB._policy_candidates(os.environ.get("RL_ADVICE_STYLE", "balance"), os.environ.get("RL_POLICY_SOURCE", "ema"))
    name, _fb = RB.policy_choice(order, lambda n: (RB.CKPT_DIR / n).exists())
    _sha, species = _registered_team()
    sel, src, _why = _model_chain(species or None, None, None)
    return {"rl_policy": RB.CKPT_DIR / name, "selection_model": Path(sel), "selection_source": src}


def freeze_models(out_dir: Path, rl_policy: Path, selection_model: Path) -> dict:
    """モデルの写しを作る (副作用: out_dir/rl_policy.zip・selection_model.pt を書く。設定ファイルは書き換えない)。
    複製の前後で元の sha256 が変わらず写しと一致することを確かめる (書き換え中なら RuntimeError)。out_dir は freeze_data の時刻の
    ディレクトリ (無ければ作る)。戻り値 {"rl_policy", "rl_policy_sha16", "selection_model", "selection_model_sha16", "lines" (4 行)}"""
    import hashlib
    import shutil
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    out = {}
    for kind, src, name in (("rl_policy", Path(rl_policy), "rl_policy.zip"),
                            ("selection_model", Path(selection_model), "selection_model.pt")):
        if not src.exists():
            raise FileNotFoundError(f"{kind} の写し元 {src} が無い")
        dst = d / name
        if dst.exists():
            raise FileExistsError(f"{dst} が既にある (上書きしない)")
        before = sha(src)
        shutil.copy2(src, dst)
        after, copied = sha(src), sha(dst)
        if not (before == after == copied):
            raise RuntimeError(f"{src} の複製の前後で sha256 が揃わない (書き換え中の可能性。もう一度実行する)")
        out[kind + "_src"] = str(src)
        out[kind] = str(dst.resolve())
        out[kind + "_sha16"] = copied[:16]
    out["lines"] = [f"{MODEL_RL_KEY}={out['rl_policy']}", f"{MODEL_RL_SHA_KEY}={out['rl_policy_sha16']}",
                    f"{MODEL_SEL_KEY}={out['selection_model']}", f"{MODEL_SEL_SHA_KEY}={out['selection_model_sha16']}"]
    return out


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


def _git_info(root: Path) -> dict:
    """コードの版 (副作用: git の読み取り): commit と、追跡ファイルの未コミットの変更があるか"""
    import subprocess
    from tools.team_build.manifest import git_commit
    dirty = None
    try:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True,
                           timeout=20)
        dirty = bool(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        dirty = None
    return {"git_commit": git_commit(root), "dirty": dirty}


def _showdown_info(sd: Path) -> dict:
    """Showdown のローカル clone の版 (副作用: git の読み取り)。無ければ commit None と理由"""
    import subprocess
    sd = Path(sd)
    if not sd.exists():
        return {"path": str(sd), "commit": None, "error": "ディレクトリが無い"}
    try:
        r = subprocess.run(["git", "-C", str(sd), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20)
        return {"path": str(sd), "commit": r.stdout.strip() or None if r.returncode == 0 else None,
                "error": None if r.returncode == 0 else (r.stderr.strip()[:200] or "rev-parse 失敗")}
    except Exception as err:
        return {"path": str(sd), "commit": None, "error": repr(err)[:200]}


def selection_profile(model: Path, source_id: Optional[str], kind: Optional[str] = None, team: Optional[list] = None,
                      sets_text: Optional[str] = None, sets_source: Optional[str] = None,
                      kind_basis: Optional[str] = None) -> dict:
    """bundle の manifest の selection の素 (副作用: 由来の manifest の読み取り)。2026-11-02 (レビュー判断 1): 種別・6 体・型の前提は
    ここでは決めない (kind を渡さなければ None)。tools.inputs_pin.make_pin_bundle が写した meta の provenance (model_sha16 の一致) で
    決める (advisor.pin_bundle.finalize_selection)。名前 (試用 Package / 登録チーム向け / 配布版) からは決めない。
    kind を渡すのは明示 (--selection-kind) のときだけで、kind_basis (根拠の文) と一緒に残す。明示の team の型の前提は sets_text
    (Showdown 本文) を渡せば fixed、無ければ unknown。由来の manifest (登録チーム向け・試用 Package) は記録 (source_manifest・規制) にだけ使う"""
    import json as _json
    from champions_agent.agent import selection_dispatch as SD
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    src_manifest = None
    sid = str(source_id or "")
    try:
        if sid.startswith(SD.REGISTERED_PREFIX):
            mp = Path(model).parent / "manifest.json"
            src_manifest = _json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
        elif sid and sid != SD.PINNED_SOURCE and not sid.startswith("explicit:"):
            mp = SD.PACKAGES_DIR / sid / "manifest.json"
            src_manifest = _json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    except (OSError, ValueError):
        pass
    t = sorted({str(x).strip().lower() for x in (team or []) if str(x).strip()}) or None
    premise, psets, psrc = "unknown", None, None
    if kind == "team" and sets_text:
        from tools.party_improvements import parse_showdown_sets
        psets = {s_id: row for s_id, row in parse_showdown_sets(sets_text).items() if not t or s_id in t}
        premise, psrc = ("fixed", sets_source) if psets else ("unknown", None)
    from champions_agent.agent.selection_dispatch import features_version
    out = {"kind": kind, "team": t if kind == "team" else None, "sets_premise": premise, "premise_sets": psets if premise == "fixed" else None,
           "premise_source": psrc, "source_id": source_id, "source_path": str(Path(model).resolve()), "source_manifest": src_manifest,
           "features": features_version(), "regulation": (src_manifest or {}).get("regulation") or TRAINING_BATTLE_FORMAT}
    if kind is not None:
        out["kind_basis"] = (kind_basis or "").strip() or None
    return out


def default_bundle_sources() -> dict:
    """--freeze-bundle の既定の写し元 (副作用: ファイルの有無・登録の読み取り)。default_model_sources に加えて、選出の推論が読む
    埋め込み (特徴量の版のピン)、同じ dir の meta、条件付きモデル、登録 (config/my_team.json)"""
    from advisor.my_team import CONFIG_PATH
    from champions_agent.agent import selection_model as SM
    from champions_agent.agent.selection_dispatch import features_version
    out = default_model_sources()
    if features_version() == "v3":
        from champions_agent.agent.selection_features_v3 import V3_EMB_PIN_PATH
        out["selection_emb"] = V3_EMB_PIN_PATH
    else:
        out["selection_emb"] = SM.EMB_PIN_PATH
    out["selection_cond"] = SM.COND_MODEL_PATH if SM.COND_MODEL_PATH.exists() else None
    out["registry"] = CONFIG_PATH
    return out


def freeze_bundle(out_root: Path, *, rl_policy: Path, selection_model: Path, selection_emb: Path, selection_meta: Optional[Path],
                  selection_cond: Optional[Path], registry: Path, selection: dict, usage_db: Path, bank: Path,
                  usage_snapshot: Optional[int] = None, stamp: Optional[str] = None, showdown_dir: Optional[Path] = None) -> dict:
    """版の固定の bundle を out_root/<時刻>/ に作る (副作用: 書き込み。設定ファイルは書き換えない。tools.inputs_pin.make_pin_bundle)。
    戻り値 {"dir", "manifest", "lines" (advice.local.env に書く 1 行)}"""
    import time as _time
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.agent.selection_dispatch import features_version
    from tools.inputs_pin import make_pin_bundle
    stamp = stamp or _time.strftime("%Y%m%d_%H%M%S")
    d = Path(out_root) / stamp
    local = LOCAL_FILE
    cfg = {"selection_features": features_version(), "battle_format": TRAINING_BATTLE_FORMAT,
           "rl_advice_style": os.environ.get("RL_ADVICE_STYLE", "balance"), "rl_policy_source": os.environ.get("RL_POLICY_SOURCE", "ema"),
           "config_py_sha16": _sha16_file(REPO / "champions_agent" / "config.py"),
           "advice_local_env": {"path": str(local), "sha16": _sha16_file(local)}}
    man = make_pin_bundle(d, rl_policy=Path(rl_policy), selection_model=Path(selection_model), selection_emb=Path(selection_emb),
                          selection_meta=Path(selection_meta) if selection_meta else None,
                          selection_cond=Path(selection_cond) if selection_cond else None, registry=Path(registry),
                          selection=selection, src_db=Path(usage_db), bank=Path(bank), snapshot_id=usage_snapshot,
                          code=_git_info(REPO), config=cfg,
                          showdown=_showdown_info(Path(showdown_dir) if showdown_dir else REPO / "pokemon-showdown"),
                          regulation=TRAINING_BATTLE_FORMAT)
    return {"dir": str(d.resolve()), "manifest": man, "lines": [f"{BUNDLE_KEY}={d.resolve()}"]}


def _sha16_file(p: Path) -> Optional[str]:
    import hashlib
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def selection_kind_lines(sel: dict) -> list:
    """--freeze-bundle の # 行: 選出モデルの種別とその根拠、型の前提、正式比較の可否 (純粋。2026-11-02)"""
    from advisor import pin_bundle as PB
    kind = sel.get("kind")
    premise = PB.premise_of(sel)[0]
    st = PB.formal_status(sel)
    out = [f"#   選出モデルの種別: {PB.KIND_LABEL.get(kind, kind)}"
           + (f" 対応 6 体 {sel.get('team')}" if kind == "team" else "")
           + f" / 根拠 {sel.get('kind_source')}: {sel.get('kind_basis') or 'なし'}"
           + f" / 型の前提 {premise} ({PB.PREMISE_LABEL.get(premise, premise)})",
           "#   正式比較: " + ("可 (formal_ok True)" if st["ok"] else f"使わない — {PB.UNVERIFIED_LABEL} (formal_ok False): {st['reason']}")]
    mc = sel.get("meta_classification") or {}
    if sel.get("kind_source") == "explicit" and (mc.get("kind"), mc.get("team")) != (kind, sel.get("team")):
        out.append(f"#   注意: 明示した種別 {PB.KIND_LABEL.get(kind, kind)} が学習履歴の判定 {PB.KIND_LABEL.get(mc.get('kind'), mc.get('kind'))}"
                   f" ({mc.get('kind_basis')}) と違う")
    if kind == "team" and premise == "unknown":
        out.append("#   注意: 構築専用モデルの学習時の型の前提が分からない (型は照合しない。正式比較には使えない)")
    return out


def _main_freeze_bundle(a) -> int:
    from advisor import pin_bundle as PB
    from advisor.data_pin import DEFAULT_USAGE_DB
    try:
        # 種別は既定でモデルの学習履歴 (同じ dir の <stem>_meta.json の provenance と model_sha16) で決める。明示は根拠が必須 (2026-11-02)
        if a.selection_kind == "team" and not a.selection_team:
            raise ValueError("--selection-kind team には --selection-team (6 体の種族 id をカンマ区切り) も指定する")
        if a.selection_kind and not (a.selection_kind_basis or "").strip():
            raise ValueError("--selection-kind を明示するときは --selection-kind-basis \"<根拠の文>\" も指定する (種別の根拠を manifest に残す。"
                             "省くと学習履歴の meta で決め、無ければ「不明」)")
        if not a.selection_kind and (a.selection_team or a.selection_sets or a.selection_kind_basis):
            raise ValueError("--selection-team / --selection-sets / --selection-kind-basis は --selection-kind と一緒に指定する "
                             "(省いたときの種別・6 体・型の前提は学習履歴の meta で決める)")
        if a.selection_kind != "team" and a.selection_sets:
            raise ValueError("--selection-sets は --selection-kind team と一緒に指定する")
        srcs = default_bundle_sources() if not (a.rl_policy and a.selection_model and a.selection_emb and a.registry) else {}
        sel_model = Path(a.selection_model) if a.selection_model else Path(srcs["selection_model"])
        source_id = None if a.selection_model else srcs.get("selection_source")
        if a.selection_model:
            source_id = "explicit:" + str(sel_model.resolve())
        team = [x for x in (a.selection_team or "").split(",") if x.strip()] or None
        sets_text = Path(a.selection_sets).read_text(encoding="utf-8") if a.selection_sets else None
        selection = selection_profile(sel_model, source_id, a.selection_kind, team, sets_text,
                                      str(Path(a.selection_sets).resolve()) if a.selection_sets else None,
                                      kind_basis=a.selection_kind_basis)
        if selection["kind"] == "team" and len(selection.get("team") or []) != 6:
            raise ValueError(f"構築専用の選出モデルの 6 体が決まらない ({selection.get('team')})。--selection-team で 6 体を指定する")
        from champions_agent.agent.selection_model import provenance_meta_path
        meta = Path(a.selection_meta) if a.selection_meta else provenance_meta_path(sel_model)
        cond = Path(a.selection_cond) if a.selection_cond else srcs.get("selection_cond")
        r = freeze_bundle(Path(a.out_dir) if a.out_dir else REPO / PIN_FREEZE_DIR_REL,
                          rl_policy=Path(a.rl_policy) if a.rl_policy else srcs["rl_policy"], selection_model=sel_model,
                          selection_emb=Path(a.selection_emb) if a.selection_emb else srcs["selection_emb"],
                          selection_meta=meta if meta.exists() else None, selection_cond=cond,
                          registry=Path(a.registry) if a.registry else srcs["registry"], selection=selection,
                          usage_db=Path(a.usage_db) if a.usage_db else DEFAULT_USAGE_DB,
                          bank=Path(a.bank) if a.bank else REPO / REAL_BANK_PATH,
                          usage_snapshot=a.usage_snapshot, showdown_dir=Path(a.showdown_dir) if a.showdown_dir else None)
        summary = PB.verify_bundle(r["dir"], explicit={})
    except (OSError, RuntimeError, ValueError, KeyError) as err:
        print(f"[freeze-bundle] 失敗: {err}", file=sys.stderr)
        return 2
    except Exception as err:            # InputsPinError (作った bundle の照合) も止める
        print(f"[freeze-bundle] 失敗: {err}", file=sys.stderr)
        return 2
    man = r["manifest"]
    f = man["files"]
    print(f"# 版の固定の bundle: {PB.describe(summary)}")
    for k in PB.REQUIRED_FILES + PB.OPTIONAL_FILES:
        e = f.get(k)
        print(f"#   {k}: " + (f"{e.get('source') or e.get('source_db')} → {PB.FILES[k]} (sha16 {e['sha16']})" if e
                                 else "なし (記録 None)"))
    ub = f["usage_db"]
    print(f"#   使用率: snapshot {ub['snapshot_id']} (元の最新 {ub.get('latest_at_pin')}) の抽出、表 {len(ub['tables'])} 個")
    print(f"#   コード: {man['code'].get('git_commit')} (未コミットの変更 {man['code'].get('dirty')}) / Showdown: "
          f"{man['showdown'].get('commit') or ('不明: ' + str(man['showdown'].get('error')))} / 規制 {man.get('regulation')}")
    for ln in selection_kind_lines(man["selection"]):
        print(ln)
    if summary.get("set_diffs"):
        print(f"#   注意: 学習時の型と登録の型が違う ({len(summary['set_diffs'])} 件)。この構築では選出モデルを使わない: "
              + "; ".join(summary["set_diffs"][:4]))
    print(f"# 次の 1 行を {LOCAL_FILE_REL} に追加する (個別の固定の 7 行は書かない。書くなら値を bundle と揃える。正式群が終わったら消す):")
    for ln in r["lines"]:
        print(ln)
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="実機の採点方式の設定 (config/advice.local.env)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--resolve", action="store_true", help="起動スクリプト用: ADVICE_SCORING_MODE と設定元を KEY=VALUE で出す")
    g.add_argument("--show", action="store_true", help="設定値を人が読む形で出す")
    g.add_argument("--freeze-data", action="store_true",
                   help="正式群の開始時: 使用率の最新 snapshot id とバンクの写しを作り、advice.local.env に書く 3 行を出す (ファイルは書き換えない)")
    g.add_argument("--freeze-bundle", action="store_true",
                   help="版の固定の bundle (RL 方策・選出モデル + 埋め込み + meta・使用率 DB の抽出・バンク・登録・manifest) を作り、"
                        "advice.local.env に書く 1 行を出す (ファイルは書き換えない)")
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
    ap.add_argument("--model-pin", action="store_true",
                    help="--resolve でモデルの固定 (ADVICE_RL_POLICY / _SHA16、ADVICE_SELECTION_MODEL / _SHA16) と設定元も出し、"
                         "ファイルがあること・sha16 の一致を確かめる (advice_env.sh が使う)")
    ap.add_argument("--with-models", action="store_true",
                    help="--freeze-data で RL 方策と選出モデルも写し、書く 4 行を足して出す")
    ap.add_argument("--rl-policy", default=None, help="--with-models で写す RL 方策 (既定 今の読み込み順で読まれるはずのファイル)")
    ap.add_argument("--selection-model", default=None,
                    help="--with-models / --freeze-bundle で写す選出モデル (既定 登録チームに使われるはずのファイル)")
    ap.add_argument("--bundle-pin", action="store_true",
                    help="--resolve で版の固定の bundle (ADVICE_PIN_BUNDLE / ADVICE_PIN_ALLOW_OTHER_TEAM) と設定元も出し、bundle 全体を確かめる"
                         " (advice_env.sh が使う)")
    ap.add_argument("--selection-kind", choices=("general", "team"), default=None,
                    help="--freeze-bundle: 選出モデルの種別を明示する (--selection-kind-basis が必須)。省くとモデルと同じ dir の "
                         "<stem>_meta.json の学習履歴 (provenance、model_sha16 の一致) で決め、無ければ「不明」。名前では決めない")
    ap.add_argument("--selection-kind-basis", default=None,
                    help="--freeze-bundle: --selection-kind を明示するときの根拠の文 (必須。manifest の selection.kind_basis に残る)")
    ap.add_argument("--selection-team", default=None, help="--freeze-bundle: 構築専用モデルの 6 体 (種族 id のカンマ区切り)")
    ap.add_argument("--selection-sets", default=None, help="--freeze-bundle: 構築専用モデルの学習時の型 (Showdown 本文のファイル)")
    ap.add_argument("--selection-emb", default=None, help="--freeze-bundle: 埋め込み (既定 今の特徴量の版のピン)")
    ap.add_argument("--selection-meta", default=None, help="--freeze-bundle: 選出モデルの meta (既定 モデルと同じ dir の meta、無ければなし)")
    ap.add_argument("--selection-cond", default=None, help="--freeze-bundle: 条件付きモデル (既定 配備先の条件付きモデル、無ければなし)")
    ap.add_argument("--registry", default=None, help="--freeze-bundle: 登録 (既定 config/my_team.json)")
    ap.add_argument("--usage-snapshot", type=int, default=None, help="--freeze-bundle: 抽出する snapshot id (既定 DB の最新)")
    ap.add_argument("--showdown-dir", default=None, help="--freeze-bundle: 版を記録する Showdown の clone (既定 pokemon-showdown)")
    a = ap.parse_args(argv)
    if a.freeze_bundle:
        return _main_freeze_bundle(a)
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
        mr = None
        if a.with_models:
            try:
                srcs = {} if (a.rl_policy and a.selection_model) else default_model_sources()
                mr = freeze_models(Path(r["dir"]), Path(a.rl_policy) if a.rl_policy else srcs["rl_policy"],
                                   Path(a.selection_model) if a.selection_model else srcs["selection_model"])
            except (OSError, RuntimeError, ValueError) as err:
                print(f"[freeze-data] モデルの写しに失敗: {err} (データの写し {r['dir']} は作成済み)", file=sys.stderr)
                return 2
            print(f"# RL 方策: {mr['rl_policy_src']} → {mr['rl_policy']} (sha16 {mr['rl_policy_sha16']})")
            print(f"# 選出モデル: {mr['selection_model_src']} → {mr['selection_model']} (sha16 {mr['selection_model_sha16']})")
        n = len(r["lines"]) + (len(mr["lines"]) if mr else 0)
        print("# 注意: この写しは選出の埋め込み・meta と使用率 DB の中身を固定しない。正式比較は --freeze-bundle を使う")
        print(f"# 次の {n} 行を {LOCAL_FILE_REL} に追加する (方式の切替のときも残し、正式群が終わったら消す):")
        for ln in r["lines"] + (mr["lines"] if mr else []):
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
        mv = model_pin_file_view(text, label)
        print(f"モデルの固定の設定値: {('読めない (' + mv['error'] + ')') if mv['error'] else model_pin_values_text(mv['values'])}")
        bv = bundle_file_view(text, label)
        print(f"版の固定 (bundle) の設定値: {('読めない (' + bv['error'] + ')') if bv['error'] else bundle_values_text(bv['values'])}")
        if bv.get("pinned"):
            from advisor import data_pin as DP
            try:
                summ = precheck_bundle_pin(bv["values"], REPO, (pin_file_view(text, label).get("values") or {}),
                                           (mv.get("values") or {}), env={})
                from advisor import pin_bundle as PB
                print(f"  bundle の照合: 一致 ({PB.describe(summ['bundle'])})")
            except (AdviceModeError, DP.InputsPinError) as err:
                print(f"  bundle の照合: ⚠ 使えない ({err})")
        return 0
    try:
        r = resolve(text, os.environ.get(ENV_KEY), label)
        rs = resolve_sensitivity(text, os.environ.get(SENS_KEY), label) if a.sensitivity else None
        rp = None
        if a.data_pin:
            rp = resolve_data_pin(text, dict(os.environ), label)
        rm = None
        if a.model_pin:
            rm = resolve_model_pin(text, dict(os.environ), label)
        rb = None
        if a.bundle_pin:
            rb = resolve_bundle_pin(text, dict(os.environ), label)
        root = Path(a.root) if a.root else Path.cwd()
        if rb is not None and rb["pinned"]:
            # bundle があれば bundle 全体を確かめる (個別の鍵は bundle の値と一致すること。個別の検査は bundle の検査に含まれる)
            dv = rp["values"] if rp is not None else resolve_data_pin(text, dict(os.environ), label)["values"]
            mv = rm["values"] if rm is not None else resolve_model_pin(text, dict(os.environ), label)["values"]
            precheck_bundle_pin(rb["values"], root, dv, mv)
        else:
            if rp is not None:
                precheck_data_pin(rp["values"], root)
            if rm is not None:
                precheck_model_pin(rm["values"], root)
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
    if rm is not None:
        for k in MODEL_PIN_KEYS:
            print(f"{k}={rm['values'][k] or ''}")
        print(f"{MODEL_PIN_SOURCE_KEY}={rm['source']}")
    if rb is not None:
        for k in BUNDLE_KEYS:
            print(f"{k}={rb['values'][k] or ''}")
        print(f"{BUNDLE_SOURCE_KEY}={rb['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
