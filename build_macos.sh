#!/usr/bin/env bash
# 在 macOS 本机执行，生成 Finder 里的 .app 应用包与可选的 .dmg 安装镜像。
# PyInstaller 不是交叉编译器，macOS 产物必须在 macOS 上构建。
#
# **只使用系统 Python**：不创建、不复用任何虚拟环境，也不升级系统 pip。
# 与 build_ubuntu.sh / build_windows.bat 保持同一套规则，避免依赖树出现
# 两套版本、把 lazy-import 代理对象收进冻结包。
#
# 图标、字体、Playwright 驱动、Qt 插件、hiddenimports 全部由
# MSRewardsAssistant.spec 管理；本脚本负责生成 .icns、触发构建、校验产物、
# 打 DMG。用到的 sips / iconutil / plutil / codesign / ditto / hdiutil 均为
# macOS 系统自带工具，不引入任何第三方打包依赖。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
用法: ./build_macos.sh [选项]

  （默认）        编译 .app，并用 hdiutil 生成带 Applications 链接的 .dmg
  --no-dmg        只生成 dist/MSRewardsAssistant.app，不打镜像
  --package-only  跳过编译，复用已有 dist 只重新封装 .dmg
  -h, --help      显示本说明

环境变量 PYTHON 可指定系统解释器（默认 python3），例如 PYTHON=python3.13。
EOF
}

fail() { echo "[ERROR] $*" >&2; exit 1; }

MAKE_DMG=1
PACKAGE_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-dmg)       MAKE_DMG=0 ;;
    --package-only) PACKAGE_ONLY=1 ;;
    -h|--help)      usage; exit 0 ;;
    *)              echo "[ERROR] 未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "本脚本必须在 macOS 上运行；PyInstaller 不能跨平台编译 macOS 应用包。"
fi

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || fail "找不到系统解释器 '$PY'；可用 PYTHON=... 指定。"
SPEC="$ROOT/MSRewardsAssistant.spec"
[[ -f "$SPEC" ]] || fail "缺少 $SPEC"
SRC_ICON="$ROOT/assets/icons/app_512.png"
[[ -s "$SRC_ICON" ]] || fail "缺少图标源文件 $SRC_ICON"

echo "[1/4] 使用系统 Python（不创建虚拟环境）..."
"$PY" -c 'import sys;print("[INFO] Interpreter:",sys.executable)'
echo "[INFO] Build Python: $("$PY" -c 'import sys;print("{}.{}".format(*sys.version_info[:2]))')"
if [[ "$PACKAGE_ONLY" = "1" ]]; then
  echo "[INFO] --package-only：跳过依赖安装与编译，复用已有 dist。"
else
  # 不升级系统 pip：系统环境的包管理器由用户自己维护。
  echo "[INFO] 把运行时与打包依赖装进系统 Python（requirements-build.txt）..."
  "$PY" -m pip install --disable-pip-version-check -r requirements-build.txt
  "$PY" -c 'import importlib.metadata as m;print("[INFO] requests",m.version("requests"))'
fi

ICON_DIR="$ROOT/.macos-build"
ICONSET="$ICON_DIR/AppIcon.iconset"
ICNS="$ICON_DIR/AppIcon.icns"

make_icns() {
  command -v sips >/dev/null 2>&1 || return 1
  command -v iconutil >/dev/null 2>&1 || return 1
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  local s d
  for s in 16 32 128 256 512; do
    d=$((s * 2))
    sips -z "$s" "$s" "$SRC_ICON" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null || return 1
    sips -z "$d" "$d" "$SRC_ICON" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null || return 1
  done
  iconutil -c icns "$ICONSET" -o "$ICNS" || return 1
  rm -rf "$ICONSET"
  [[ -s "$ICNS" ]]
}

echo "[2/4] 由 PNG 生成 .icns 应用图标（sips + iconutil，系统自带）..."
if [[ "$PACKAGE_ONLY" = "1" ]]; then
  echo "[INFO] --package-only：沿用已有 .app 内的图标。"
elif make_icns; then
  echo "[INFO] 应用图标: $ICNS"
else
  rm -f "$ICNS"
  rm -rf "$ICONSET"
  echo "[NOTE] 图标转换失败，BUNDLE 将使用 PyInstaller 默认应用图标。"
fi

ONEDIR="$ROOT/dist/MSRewardsAssistant"
APP="$ROOT/dist/MSRewardsAssistant.app"

echo "[3/4] 使用 MSRewardsAssistant.spec 构建 macOS 应用包..."
if [[ "$PACKAGE_ONLY" = "0" ]]; then
  "$PY" -m PyInstaller --noconfirm --clean "$SPEC"
  # PyInstaller 6.x 会把顶层 shiboken6 动态库做成 symlink；复制成真实文件，
  # 保证 onedir 目录版在解包 / 跨机器分发时同样能加载。
  "$PY" "$ROOT/tools/normalize_bundle.py" "$ONEDIR"
fi

# --- 目录版守卫：与 Linux / Windows 同一套口径 -------------------------------
BIN="$ONEDIR/MSRewardsAssistant"
[[ -x "$BIN" ]] || fail "构建产物缺失: $BIN"
INTERNAL="$ONEDIR/_internal"
[[ -f "$INTERNAL/base_library.zip" ]] || fail "构建不完整（缺 _internal/base_library.zip）。"
SB_OK=0
for f in "$INTERNAL"/libshiboken6*.dylib "$INTERNAL"/libshiboken6*.so*; do
  [[ -f "$f" && ! -L "$f" ]] && { SB_OK=1; break; }
done
[[ "$SB_OK" = "1" ]] || fail "_internal 顶层缺少真实的 shiboken6 动态库。"

# --- .app 守卫：macOS 特有的 bundle 结构 -----------------------------------
[[ -d "$APP" ]] || fail "未生成应用包: $APP"
[[ -x "$APP/Contents/MacOS/MSRewardsAssistant" ]] || fail "应用包内缺少可执行文件。"
[[ -f "$APP/Contents/Info.plist" ]] || fail "应用包缺少 Contents/Info.plist。"
for tool in plutil codesign; do
  command -v "$tool" >/dev/null 2>&1 || fail "缺少系统工具 $tool，无法校验应用包。"
done
plutil -lint "$APP/Contents/Info.plist" >/dev/null || fail "Info.plist 格式无效。"
# BUNDLE 会把二进制放到 Contents/Frameworks、数据放到 Contents/Resources 并做
# 交叉链接，因此用 find 定位，不假设固定目录层级。
[[ -n "$(find "$APP/Contents" -name base_library.zip -print -quit)" ]] || \
  fail "应用包内缺少 base_library.zip，拒绝交付残缺包。"
[[ -n "$(find "$APP/Contents" -name libqcocoa.dylib -print -quit)" ]] || \
  fail "应用包内缺少 Qt cocoa 平台插件，无法在 macOS 显示窗口。"
[[ -n "$(find "$APP/Contents/Frameworks" -name 'libshiboken6*.dylib' -type f -print -quit)" ]] || \
  fail "应用包 Frameworks 内缺少真实的 shiboken6 动态库。"
# BUNDLE 构建时已做 ad-hoc 签名；这里再验一次，未签名的 bundle 会被 Gatekeeper 拦。
codesign --verify --deep --strict "$APP" || fail "应用包代码签名校验失败。"
echo "[INFO] .app 校验通过: $APP"

echo "[4/4] 封装安装镜像..."
if [[ "$MAKE_DMG" = "0" ]]; then
  echo "[INFO] --no-dmg：跳过镜像封装。"
else
  for tool in ditto hdiutil; do
    command -v "$tool" >/dev/null 2>&1 || fail "缺少系统工具 $tool，无法生成 .dmg。"
  done
  VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$APP/Contents/Info.plist" 2>/dev/null || echo "2.0")"
  ARCH="$(uname -m)"
  mkdir -p "$ROOT/macos-dist"
  OUT="$ROOT/macos-dist/MSRewardsAssistant_${VERSION}_${ARCH}.dmg"
  STAGE="$(mktemp -d)"
  trap 'rm -rf "$STAGE"' EXIT
  # ditto 保留 .app 内的 symlink、扩展属性与签名；cp -R 不能保证这些元数据。
  ditto "$APP" "$STAGE/MSRewardsAssistant.app"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "MSRewardsAssistant" -srcfolder "$STAGE" \
    -ov -format UDZO "$OUT"
  [[ -s "$OUT" ]] || fail "DMG 生成失败或为空: $OUT"
  echo "[完成] $OUT"
fi

echo "[完成] 应用包: $APP"
echo "[INFO] 架构: $(uname -m)（与构建机一致；不做 universal2 合并）。"
echo "[INFO] 生成的是 ad-hoc 签名、未公证的 app：从网络下载后若被 Gatekeeper 拦，"
echo "       右键「打开」或执行 xattr -dr com.apple.quarantine /Applications/MSRewardsAssistant.app"
