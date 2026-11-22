#!/usr/bin/env bash
# 在 Ubuntu 本机执行，生成 Linux 目录版，不能跨平台编译 Windows exe。
#
# **只使用系统 Python**：不创建、不复用任何虚拟环境，也不升级系统 pip。
# 早先的 venv 方案会与系统 site-packages 里的 requests / anyio 形成两套
# 版本，PyInstaller 分析时容易把 lazy-import 代理对象收进冻结包，表现为
# 运行时 "同步失败: 'lazy_import' object is not callable"。统一到系统环境
# 可以让依赖树唯一；同步层另有标准库 urllib 兜底，双保险。
#
# 资源（icons / fonts / Playwright 驱动 / PySide6 / hiddenimports）统一由
# MSRewardsAssistant.spec 管理；脚本只负责校验解释器、装依赖、触发构建。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[ERROR] This script must run on Ubuntu/Linux." >&2
  exit 1
fi

# PYTHON 可由外部指定系统解释器（例如 PYTHON=python3.13），默认 python3。
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || {
  echo "[ERROR] 找不到系统解释器 '$PY'；可用 PYTHON=... 指定。" >&2
  exit 1
}
SPEC="$ROOT/MSRewardsAssistant.spec"
[[ -f "$SPEC" ]] || { echo "[ERROR] 缺少 $SPEC" >&2; exit 1; }

if [[ -d "$ROOT/.venv-build-ubuntu" ]]; then
  echo "[NOTE] 发现遗留的 .venv-build-ubuntu 目录，本脚本不再使用它，可安全删除："
  echo "       rm -rf .venv-build-ubuntu"
fi

echo "[1/3] 使用系统 Python（不创建虚拟环境）..."
"$PY" -c 'import sys;print("[INFO] Interpreter:",sys.executable)'
echo "[INFO] Build Python: $("$PY" -c 'import sys;print("{}.{}".format(*sys.version_info[:2]))')"
if "$PY" -c 'import sys;sys.exit(0 if sys.version_info >= (3,15) else 1)'; then
  echo "[NOTE] Python 3.15+ 需要 PySide6 6.12+；requirements.txt 会自动选择。"
  echo "       该组合未做完整回归，建议用 Python 3.12/3.13 构建。"
fi

# 不升级系统 pip：系统环境的包管理器由发行版 / 用户自己维护。
echo "[2/3] 把运行时与打包依赖装进系统 Python（requirements-build.txt）..."
"$PY" -m pip install --disable-pip-version-check -r requirements-build.txt
"$PY" -c 'import importlib.metadata as m;print("[INFO] requests",m.version("requests"))'

echo "[3/3] 使用 MSRewardsAssistant.spec 构建 Linux 目录版..."
"$PY" -m PyInstaller --noconfirm --clean "$SPEC"

# PyInstaller 6.x 会把顶层 libshiboken6.abi3.so.6.9 做成指向 shiboken6/
# 子目录的 symlink；跨机器解包 / 不保留 symlink 的文件系统就会报
# "cannot open shared object file"。构建后统一复制成真实文件。
"$PY" "$ROOT/tools/normalize_bundle.py" "$ROOT/dist/MSRewardsAssistant"

BIN="$ROOT/dist/MSRewardsAssistant/MSRewardsAssistant"
[[ -x "$BIN" ]] || { echo "[ERROR] 构建产物缺失: $BIN" >&2; exit 1; }

# 防呆：shiboken6 动态库必须在 _internal 顶层是**真实文件**，否则打包二进制
# 启动会报 libshiboken6.abi3.so.6.9: cannot open shared object file。
# 不同 PyInstaller / shiboken6 版本对此的隐式处理不一致，宁可构建失败也不
# 交付一个看起来完整、实际起不来的目录。
INTERNAL="$ROOT/dist/MSRewardsAssistant/_internal"
SHIBOKEN_LIB=""
for f in "$INTERNAL"/libshiboken6*.so* "$INTERNAL"/shiboken6*.dll; do
  [[ -f "$f" && ! -L "$f" ]] || continue
  SHIBOKEN_LIB="$f"
  break
done
if [[ -z "$SHIBOKEN_LIB" ]]; then
  echo "[ERROR] $INTERNAL 顶层缺少真实的 shiboken6 动态库。" >&2
  echo "        PyInstaller hook 未收集 libshiboken6.abi3.so.6.9；" >&2
  echo "        请确认 MSRewardsAssistant.spec 的 SHIBOKEN_EXPLICIT 生效，" >&2
  echo "        或升级 PyInstaller / 重装 PySide6 后重建。" >&2
  exit 1
fi
if command -v ldd >/dev/null 2>&1; then
  MISSING="$(ldd "$INTERNAL/shiboken6/Shiboken.abi3.so" 2>/dev/null | grep -c 'not found' || true)"
  if [[ "$MISSING" != "0" ]]; then
    echo "[ERROR] Shiboken.abi3.so 仍有未解析依赖：" >&2
    ldd "$INTERNAL/shiboken6/Shiboken.abi3.so" 2>/dev/null | grep 'not found' >&2
    exit 1
  fi
fi
echo "[INFO] shiboken6 动态库校验通过: $(basename "$SHIBOKEN_LIB")"

echo "Build complete: $BIN"
echo "Keep the whole dist/MSRewardsAssistant directory together."
