# shellcheck shell=bash
# 実機 (アドバイザーの常駐) の採点方式を決める共通の読み込み (source して使う。2026-10-16 レビュー、方式 C、計画書 §8.27)。
#
#   . scripts/advice_env.sh
#   advice_env_resolve || exit 1    # 起動の前に検査 (何も起動しないうちに止める)。結果は ADVICE_ENV_MODE / ADVICE_ENV_SOURCE
#   ( advice_env_export; nohup bash -c "... uvicorn ..." & disown )   # uvicorn にだけ渡す
#   load_advice_env || exit 1       # 上の 2 つをまとめて (前景の起動 start_servers.sh 用)
#
# 規則は tools/advice_mode.py (python -m tools.advice_mode --resolve) にだけ書く:
#   config/advice.local.env (追跡しない) の ADVICE_SCORING_MODE → 無ければ環境変数 ADVICE_SCORING_MODE → 無ければ legacy。
#   使えない値・認めない鍵・ファイルと環境変数の矛盾はエラー (黙って片方を採用しない)。
# 決めた値は ADVICE_SCORING_MODE、設定元 (file / env / default) は ADVICE_SCORING_MODE_SOURCE に export する。
# 2026-10-18: transition の感度 (TRANSITION_SENSITIVITY、値 0 / 1) も同じ規則で決めて export する (設定元は
# TRANSITION_SENSITIVITY_SOURCE)。ファイルにも環境変数にも無ければ 0 (実機の既定 = 感度を計算しない。順位に使わない計算なので
# 候補・得点・順位は変わらない)。有効に戻すには config/advice.local.env に TRANSITION_SENSITIVITY=1 と書く。
# config.TRANSITION_SENSITIVITY_ENABLED の既定 (計算する) は変えないので、ここを通らない測定・診断は従来どおり。
# export は uvicorn を起動するサブシェルの中だけで行う (同じスクリプトが起こす学習ループ等に実機用の設定を持ち込まない。
# 測定の子プロセスは条件ごとの環境変数で明示する)。リポジトリのルートで source すること。
# 2026-10-23 データの固定 (正式群の実機、計画書 §8.49): ADVICE_USAGE_SNAPSHOT (使用率の snapshot id)・ADVICE_REAL_BANK (実戦バンクの
# 写しのパス)・ADVICE_REAL_BANK_SHA16 (任意、写しの sha16) も同じ規則で決める (python -m tools.advice_mode --resolve --data-pin)。
# 値のある鍵だけを uvicorn のサブシェルに export し、設定元は ADVICE_DATA_PIN_SOURCE (file / env / file+env / default)。
# どれも無ければ何も足さない (従来どおり DB の最新 snapshot・logs/real_opponents/bank.json)。不正値・矛盾に加え、指定 snapshot が
# DB に無い・写しが無い・sha16 が違う・元のバンクそのものを指すときも、何も起動・停止しないうちに止める。写しは
# python -m tools.advice_mode --freeze-data で作る (表示された 3 行を config/advice.local.env に書く)。
# 2026-10-31 モデルの実体の固定 (KNOWN_ISSUES E-inh-4): ADVICE_RL_POLICY / ADVICE_RL_POLICY_SHA16 (RL 方策) と ADVICE_SELECTION_MODEL /
# ADVICE_SELECTION_MODEL_SHA16 (助言の選出モデル) も同じ規則で決める (--model-pin)。値のある鍵だけを uvicorn のサブシェルに export し、
# 設定元は ADVICE_MODEL_PIN_SOURCE。どれも無ければ何も足さない (従来どおりの読み込み順。退避は version 行の runtime に残る)。
# ファイルが無い・sha16 が違うときは、何も起動・停止しないうちに止める。写しは --freeze-data --with-models で作る。
# 2026-10-31 版の固定の bundle (追加レビュー判断 2・4): ADVICE_PIN_BUNDLE (bundle の dir) と ADVICE_PIN_ALLOW_OTHER_TEAM (構築専用の
# 選出モデルを別構築に適用する汎化試験、1 のときだけ) も同じ規則で決める (--bundle-pin)。bundle があれば bundle 全体 (全ファイルの
# sha256、個別の鍵との整合) を何も起動・停止しないうちに確かめ、値のある鍵だけを uvicorn のサブシェルに export する (設定元は
# ADVICE_PIN_BUNDLE_SOURCE)。bundle は python -m tools.advice_mode --freeze-bundle で作る (表示された 1 行を書く)。

# 判定に使う python (launchd は PATH だけなので venv を直接指す。ADVICE_ENV_PYTHON で差し替え可 = テスト用)
_advice_env_python() {
  if [ -n "${ADVICE_ENV_PYTHON:-}" ]; then echo "$ADVICE_ENV_PYTHON"
  elif [ -x .venv/bin/python ]; then echo ".venv/bin/python"
  else echo "python3"
  fi
}

advice_env_resolve() {
  local out py line
  py="$(_advice_env_python)"
  ADVICE_ENV_MODE=""; ADVICE_ENV_SOURCE=""; ADVICE_ENV_SENS=""; ADVICE_ENV_SENS_SOURCE=""
  ADVICE_ENV_PIN_SNAP=""; ADVICE_ENV_PIN_BANK=""; ADVICE_ENV_PIN_SHA=""; ADVICE_ENV_PIN_SOURCE=""
  ADVICE_ENV_RL=""; ADVICE_ENV_RL_SHA=""; ADVICE_ENV_SEL=""; ADVICE_ENV_SEL_SHA=""; ADVICE_ENV_MODEL_SOURCE=""
  ADVICE_ENV_BUNDLE=""; ADVICE_ENV_ALLOW_OTHER=""; ADVICE_ENV_BUNDLE_SOURCE=""
  # ファイルはこのリポジトリのルート (source する側が cd 済み) からの相対で渡す
  if ! out="$("$py" -m tools.advice_mode --resolve --sensitivity --data-pin --model-pin --bundle-pin --file config/advice.local.env)"; then
    echo "採点方式・データの固定・モデルの固定・版の固定 (bundle) の設定が不正なので起動しません (config/advice.local.env / 環境変数 ADVICE_SCORING_MODE・TRANSITION_SENSITIVITY・ADVICE_USAGE_SNAPSHOT・ADVICE_REAL_BANK・ADVICE_REAL_BANK_SHA16・ADVICE_RL_POLICY・ADVICE_SELECTION_MODEL (と各 _SHA16)・ADVICE_PIN_BUNDLE・ADVICE_PIN_ALLOW_OTHER_TEAM を確認)" >&2
    return 1
  fi
  while IFS= read -r line; do
    case "$line" in
      ADVICE_SCORING_MODE=*) ADVICE_ENV_MODE="${line#ADVICE_SCORING_MODE=}";;
      ADVICE_SCORING_MODE_SOURCE=*) ADVICE_ENV_SOURCE="${line#ADVICE_SCORING_MODE_SOURCE=}";;
      TRANSITION_SENSITIVITY=*) ADVICE_ENV_SENS="${line#TRANSITION_SENSITIVITY=}";;
      TRANSITION_SENSITIVITY_SOURCE=*) ADVICE_ENV_SENS_SOURCE="${line#TRANSITION_SENSITIVITY_SOURCE=}";;
      ADVICE_USAGE_SNAPSHOT=*) ADVICE_ENV_PIN_SNAP="${line#ADVICE_USAGE_SNAPSHOT=}";;
      ADVICE_REAL_BANK=*) ADVICE_ENV_PIN_BANK="${line#ADVICE_REAL_BANK=}";;
      ADVICE_REAL_BANK_SHA16=*) ADVICE_ENV_PIN_SHA="${line#ADVICE_REAL_BANK_SHA16=}";;
      ADVICE_DATA_PIN_SOURCE=*) ADVICE_ENV_PIN_SOURCE="${line#ADVICE_DATA_PIN_SOURCE=}";;
      ADVICE_RL_POLICY=*) ADVICE_ENV_RL="${line#ADVICE_RL_POLICY=}";;
      ADVICE_RL_POLICY_SHA16=*) ADVICE_ENV_RL_SHA="${line#ADVICE_RL_POLICY_SHA16=}";;
      ADVICE_SELECTION_MODEL=*) ADVICE_ENV_SEL="${line#ADVICE_SELECTION_MODEL=}";;
      ADVICE_SELECTION_MODEL_SHA16=*) ADVICE_ENV_SEL_SHA="${line#ADVICE_SELECTION_MODEL_SHA16=}";;
      ADVICE_MODEL_PIN_SOURCE=*) ADVICE_ENV_MODEL_SOURCE="${line#ADVICE_MODEL_PIN_SOURCE=}";;
      ADVICE_PIN_BUNDLE=*) ADVICE_ENV_BUNDLE="${line#ADVICE_PIN_BUNDLE=}";;
      ADVICE_PIN_ALLOW_OTHER_TEAM=*) ADVICE_ENV_ALLOW_OTHER="${line#ADVICE_PIN_ALLOW_OTHER_TEAM=}";;
      ADVICE_PIN_BUNDLE_SOURCE=*) ADVICE_ENV_BUNDLE_SOURCE="${line#ADVICE_PIN_BUNDLE_SOURCE=}";;
    esac
  done <<< "$out"
  if [ -z "$ADVICE_ENV_MODE" ] || [ -z "$ADVICE_ENV_SOURCE" ] || [ -z "$ADVICE_ENV_SENS" ] || [ -z "$ADVICE_ENV_SENS_SOURCE" ] \
     || [ -z "$ADVICE_ENV_PIN_SOURCE" ] || [ -z "$ADVICE_ENV_MODEL_SOURCE" ] || [ -z "$ADVICE_ENV_BUNDLE_SOURCE" ]; then
    echo "採点方式を決められませんでした (tools.advice_mode --resolve の出力: $out)" >&2
    return 1
  fi
  echo "採点方式: $ADVICE_ENV_MODE (設定元 $ADVICE_ENV_SOURCE)"
  echo "transition の感度: TRANSITION_SENSITIVITY=$ADVICE_ENV_SENS (設定元 $ADVICE_ENV_SENS_SOURCE)"
  if [ "$ADVICE_ENV_PIN_SOURCE" = default ]; then
    echo "データの固定: なし (使用率は DB の最新 snapshot、実戦バンクは logs/real_opponents/bank.json)"
  else
    echo "データの固定: snapshot ${ADVICE_ENV_PIN_SNAP:-最新} / バンク ${ADVICE_ENV_PIN_BANK:-logs/real_opponents/bank.json}${ADVICE_ENV_PIN_SHA:+ (sha16 $ADVICE_ENV_PIN_SHA)} (設定元 $ADVICE_ENV_PIN_SOURCE)"
  fi
  if [ "$ADVICE_ENV_MODEL_SOURCE" = default ]; then
    echo "モデルの固定: なし (RL 方策は読み込み順、選出モデルは 試用 Package → 登録チーム向け → 配布版)"
  else
    echo "モデルの固定: RL 方策 ${ADVICE_ENV_RL:-固定なし}${ADVICE_ENV_RL_SHA:+ (sha16 $ADVICE_ENV_RL_SHA)} / 選出モデル ${ADVICE_ENV_SEL:-固定なし}${ADVICE_ENV_SEL_SHA:+ (sha16 $ADVICE_ENV_SEL_SHA)} (設定元 $ADVICE_ENV_MODEL_SOURCE)"
  fi
  if [ -n "$ADVICE_ENV_BUNDLE" ]; then
    echo "版の固定 (bundle): $ADVICE_ENV_BUNDLE (RL 方策・選出モデル・埋め込み・meta・使用率 DB・バンク・登録をすべて bundle から読む。照合済み)${ADVICE_ENV_ALLOW_OTHER:+ / 構築専用モデルの別構築への適用 (汎化試験) 許可} (設定元 $ADVICE_ENV_BUNDLE_SOURCE)"
  fi
}

advice_env_export() {
  if [ -z "${ADVICE_ENV_MODE:-}" ] || [ -z "${ADVICE_ENV_SOURCE:-}" ] || [ -z "${ADVICE_ENV_SENS:-}" ] \
     || [ -z "${ADVICE_ENV_SENS_SOURCE:-}" ]; then
    echo "advice_env_export: 先に advice_env_resolve を呼ぶこと" >&2
    return 1
  fi
  export ADVICE_SCORING_MODE="$ADVICE_ENV_MODE"
  export ADVICE_SCORING_MODE_SOURCE="$ADVICE_ENV_SOURCE"
  export TRANSITION_SENSITIVITY="$ADVICE_ENV_SENS"
  export TRANSITION_SENSITIVITY_SOURCE="$ADVICE_ENV_SENS_SOURCE"
  # データの固定: 値のある鍵だけ (無い鍵は足さない = 従来どおり最新)
  [ -n "${ADVICE_ENV_PIN_SNAP:-}" ] && export ADVICE_USAGE_SNAPSHOT="$ADVICE_ENV_PIN_SNAP"
  [ -n "${ADVICE_ENV_PIN_BANK:-}" ] && export ADVICE_REAL_BANK="$ADVICE_ENV_PIN_BANK"
  [ -n "${ADVICE_ENV_PIN_SHA:-}" ] && export ADVICE_REAL_BANK_SHA16="$ADVICE_ENV_PIN_SHA"
  export ADVICE_DATA_PIN_SOURCE="${ADVICE_ENV_PIN_SOURCE:-default}"
  # モデルの固定 (2026-10-31): 値のある鍵だけ (無い鍵は足さない = 従来どおりの読み込み順)
  [ -n "${ADVICE_ENV_RL:-}" ] && export ADVICE_RL_POLICY="$ADVICE_ENV_RL"
  [ -n "${ADVICE_ENV_RL_SHA:-}" ] && export ADVICE_RL_POLICY_SHA16="$ADVICE_ENV_RL_SHA"
  [ -n "${ADVICE_ENV_SEL:-}" ] && export ADVICE_SELECTION_MODEL="$ADVICE_ENV_SEL"
  [ -n "${ADVICE_ENV_SEL_SHA:-}" ] && export ADVICE_SELECTION_MODEL_SHA16="$ADVICE_ENV_SEL_SHA"
  export ADVICE_MODEL_PIN_SOURCE="${ADVICE_ENV_MODEL_SOURCE:-default}"
  # 版の固定の bundle (2026-10-31): 値のある鍵だけ
  [ -n "${ADVICE_ENV_BUNDLE:-}" ] && export ADVICE_PIN_BUNDLE="$ADVICE_ENV_BUNDLE"
  [ -n "${ADVICE_ENV_ALLOW_OTHER:-}" ] && export ADVICE_PIN_ALLOW_OTHER_TEAM="$ADVICE_ENV_ALLOW_OTHER"
  export ADVICE_PIN_BUNDLE_SOURCE="${ADVICE_ENV_BUNDLE_SOURCE:-default}"
  return 0
}

load_advice_env() {
  advice_env_resolve && advice_env_export
}
