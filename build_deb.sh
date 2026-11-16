#!/usr/bin/env bash
# 构建 Debian/Ubuntu 安装包：先执行 PyInstaller，再由 dpkg-deb 封装。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != 'Linux' ]]; then
  echo '[错误] .deb 必须在 Linux 本机打包' >&2; exit 1
fi
command -v dpkg-deb >/dev/null || { echo '[错误] 需要安装 dpkg-dev 或 dpkg' >&2; exit 1; }
if [[ $# -gt 1 || ( $# -eq 1 && ${1:-} != '--package-only' ) ]]; then
  echo "用法: $0 [--package-only]" >&2; exit 2
fi
if [[ ${1:-} != '--package-only' ]]; then
  bash "$ROOT/build_ubuntu.sh"
fi
BIN="$ROOT/dist/MSRewardsAssistant/MSRewardsAssistant"
[[ -x "$BIN" ]] || { echo "[错误] 未找到已构建程序: $BIN" >&2; exit 1; }
# COLLECT 被中断时（如 CI 超时 / 进程被杀）可能只拷了部分文件，仍会留下可
# 执行的 EXE。检查两个必带资源，避免把残缺 dist 封成看似正常的 .deb。
DIST="$ROOT/dist/MSRewardsAssistant"
[[ -f "$DIST/_internal/base_library.zip" ]] || {
  echo "[错误] 构建不完整（缺 _internal/base_library.zip），请重新执行 build_ubuntu.sh" >&2
  exit 1
}
if [[ ! -f "$DIST/_internal/assets/icons/app.svg" && ! -f "$DIST/assets/icons/app.svg" ]]; then
  echo "[错误] 构建不完整（缺 assets/icons/app.svg），请重新执行 build_ubuntu.sh" >&2
  exit 1
fi
VERSION="$(PYTHONPATH="$ROOT" python3 - <<'PY'
from rewards_assistant.config import APP_VERSION
print(APP_VERSION)
PY
)"
ARCH="$(dpkg --print-architecture)"
OUT="$ROOT/deb-dist/ms-rewards-assistant_${VERSION}_${ARCH}.deb"
mkdir -p "$ROOT/deb-dist"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PKG="$TMP/package"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/ms-rewards-assistant" "$PKG/usr/bin" "$PKG/usr/share/applications"
[[ -s "$ROOT/assets/icons/app.svg" ]] || { echo "[错误] 缺少图标 SVG" >&2; exit 1; }
[[ -s "$ROOT/assets/icons/app_256.png" ]] || { echo "[错误] 缺少图标 PNG" >&2; exit 1; }
cp -a "$ROOT/dist/MSRewardsAssistant/." "$PKG/opt/ms-rewards-assistant/"
# freedesktop icon theme: 应用菜单、任务栏及通知区域均可查找到图标。
install -Dm644 "$ROOT/assets/icons/app.svg" \
  "$PKG/usr/share/icons/hicolor/scalable/apps/ms-rewards-assistant.svg"
for size in 16 24 32 48 64 128 256; do
  install -Dm644 "$ROOT/assets/icons/app_${size}.png" \
    "$PKG/usr/share/icons/hicolor/${size}x${size}/apps/ms-rewards-assistant.png"
done
install -Dm644 "$ROOT/assets/icons/app_256.png" \
  "$PKG/usr/share/pixmaps/ms-rewards-assistant.png"
# Qt 6 的 xcb 平台插件在运行时动态加载 libxcb-* 一族；缺任何一个都会报
# "Could not load the Qt platform plugin xcb"。libglib2.0-0 在 Ubuntu 24.04
# 已改名 ...-0t64，用 OR 兼容两种包名。
cat > "$PKG/DEBIAN/control" <<EOF
Package: ms-rewards-assistant
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: MS Rewards Assistant Project <noreply@localhost>
Depends: libglib2.0-0 | libglib2.0-0t64, libdbus-1-3 | libdbus-1-3t64, libfontconfig1, libfreetype6, libx11-6, libx11-xcb1, libxext6, libxi6, libxrender1, libxrandr2, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render0, libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xinerama0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0, libegl1, libgl1
Description: 积分活动管理助手（独立项目）
 Microsoft Rewards desktop task viewer, activity clicker and search helper.
EOF
cat > "$PKG/usr/bin/ms-rewards-assistant" <<'SHLAUNCH'
#!/bin/sh
exec /opt/ms-rewards-assistant/MSRewardsAssistant "$@"
SHLAUNCH
chmod 755 "$PKG/usr/bin/ms-rewards-assistant"
cat > "$PKG/usr/share/applications/ms-rewards-assistant.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=积分活动管理助手
Comment=非官方积分活动管理桌面程序
Exec=ms-rewards-assistant
Icon=ms-rewards-assistant
Terminal=false
Categories=Utility;Network;
StartupNotify=true
StartupWMClass=MSRewardsAssistant
DESKTOP
chmod 644 "$PKG/usr/share/applications/ms-rewards-assistant.desktop"
cat > "$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || true
fi
exit 0
POSTINST
chmod 755 "$PKG/DEBIAN/postinst"
dpkg-deb --root-owner-group --build "$PKG" "$OUT"
dpkg-deb --info "$OUT"
echo "[完成] $OUT"
