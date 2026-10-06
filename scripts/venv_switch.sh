#!/usr/bin/env bash
# .venv を別の venv (scripts/venv_rebuild.sh で作ったもの) へシンボリックリンクで切り替える / 元に戻す。
#   bash scripts/venv_switch.sh .venv312      # .venv → .venv39 に退避し、.venv を .venv312 へのリンクにする
#   bash scripts/venv_switch.sh --rollback    # リンクを外して .venv39 を .venv に戻す
# venv は中に絶対パスを持つので rename すると壊れる。リンクなら中のパスはそのまま有効 (docs/OPERATIONS.md「Python 環境」)。
# .venv の Python が動いている間は切り替えない (動いているプロセスが古い環境のファイルを掴んでいる)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OLD=".venv39"

running="$(pgrep -fl "\.venv/bin/python" || true)"
if [ -n "$running" ]; then
  echo "[venv_switch] .venv の Python が動いています。止めてから実行してください:"
  echo "$running"
  exit 1
fi

if [ "${1:-}" = "--rollback" ]; then
  if [ ! -d "$OLD" ]; then
    echo "[venv_switch] $OLD がありません (戻す先が無い)"
    exit 1
  fi
  if [ -L .venv ]; then
    rm .venv
  elif [ -e .venv ]; then
    echo "[venv_switch] .venv がリンクではありません。手で確認してください"
    exit 1
  fi
  mv "$OLD" .venv
  echo "[venv_switch] 戻しました: .venv = $(.venv/bin/python --version)"
  exit 0
fi

NEW="${1:?切り替え先の venv (例 .venv312)}"
if [ ! -x "$NEW/bin/python" ]; then
  echo "[venv_switch] $NEW/bin/python がありません"
  exit 1
fi
if [ -L .venv ]; then
  rm .venv
elif [ -d .venv ]; then
  if [ -e "$OLD" ]; then
    echo "[venv_switch] $OLD が既にあります。消すか名前を変えてから実行してください"
    exit 1
  fi
  mv .venv "$OLD"
  echo "[venv_switch] 旧環境を $OLD に退避しました"
fi
ln -s "$NEW" .venv
echo "[venv_switch] .venv → $NEW: $(.venv/bin/python --version)"
