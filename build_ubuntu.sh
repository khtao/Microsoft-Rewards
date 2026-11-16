#!/usr/bin/env bash
# 在 Ubuntu 本机执行，生成 Linux 目录版，不能跨平台编译 Windows exe。
#
# 资源（icons / fonts / Playwright 驱动 / PySide6 / hiddenimports）统一由
# MSRewardsAssistant.spec 管理；脚本只负责准备解释器、装依赖、触发构建。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[ERROR] This script must run on Ubuntu/Linux." >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
SPEC="$ROOT/MSRewardsAssistant.spec"
[[ -f "$SPEC" ]] || { echo "[ERROR] 缺少 $SPEC" >&2; exit 1; }

PY="${ROOT}/.venv-build-ubuntu/bin/python"
USE_VENV=1
if [[ -x "$PY" ]]; then
  echo "[1/3] 复用已有构建虚拟环境 .venv-build-ubuntu"
elif "${PYTHON}" -m venv .venv-build-ubuntu >/dev/null 2>&1; then
  echo "[1/3] 已创建构建虚拟环境 .venv-build-ubuntu"
else
  # 精简容器 / CI 常缺 python3-venv（ensurepip）。此时直接用系统解释器，
  # 不升级系统 pip，避免破坏发行版自带的包管理。
  rm -rf .venv-build-ubuntu
  PY="${PYTHON}"
  USE_VENV=0
  echo "[1/3] python3-venv 不可用，回退系统解释器 ${PYTHON}"
fi

echo "[INFO] Build Python: $("$PY" -c 'import sys;print("{}.{}".format(*sys.version_info[:2]))')"
if "$PY" -c 'import sys;sys.exit(0 if sys.version_info >= (3,15) else 1)'; then
  echo "[NOTE] Python 3.15+ 需要 PySide6 6.12+；requirements.txt 会自动选择。"
  echo "       该组合未做完整回归，建议用 Python 3.12/3.13 构建。"
fi

echo "[2/3] 安装运行时与打包依赖（requirements-build.txt）..."
if [[ $USE_VENV -eq 1 ]]; then
  "$PY" -m pip install --upgrade pip
fi
"$PY" -m pip install -r requirements-build.txt

echo "[3/3] 使用 MSRewardsAssistant.spec 构建 Linux 目录版..."
"$PY" -m PyInstaller --noconfirm --clean "$SPEC"

BIN="$ROOT/dist/MSRewardsAssistant/MSRewardsAssistant"
[[ -x "$BIN" ]] || { echo "[ERROR] 构建产物缺失: $BIN" >&2; exit 1; }
echo "Build complete: $BIN"
echo "Keep the whole dist/MSRewardsAssistant directory together."
