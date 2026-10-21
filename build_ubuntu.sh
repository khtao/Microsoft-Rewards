#!/usr/bin/env bash
# 在 Ubuntu 本机执行，生成 Linux 目录版，不能跨平台编译 Windows exe。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[ERROR] This script must run on Ubuntu/Linux." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
if [[ ! -x .venv-build-ubuntu/bin/python ]]; then
  echo "[1/3] Creating Python virtual environment..."
  "$PYTHON" -m venv .venv-build-ubuntu
fi
PY="${ROOT}/.venv-build-ubuntu/bin/python"

echo "[2/3] Installing runtime and packaging dependencies..."
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements-build.txt

echo "[3/3] Building Ubuntu desktop application..."
extra=()
if [[ -d "$ROOT/fonts" ]]; then
  extra+=(--add-data "$ROOT/fonts:fonts")
fi
# 必须显式打包图标资源；否则源码运行正常，deb 里 Qt 会退回默认图标。
if [[ ! -f "$ROOT/assets/icons/app.svg" ]]; then
  echo "[ERROR] Missing application icon assets/icons/app.svg" >&2
  exit 1
fi
extra+=(--add-data "$ROOT/assets/icons:assets/icons")
"$PY" -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name MSRewardsAssistant --paths "$ROOT" \
  --collect-all playwright \
  "${extra[@]}" \
  rewards_assistant/__main__.py

echo "Build complete: $ROOT/dist/MSRewardsAssistant/MSRewardsAssistant"
echo "Keep the whole dist/MSRewardsAssistant directory together."
