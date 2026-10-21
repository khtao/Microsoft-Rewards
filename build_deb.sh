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
cat > "$PKG/DEBIAN/control" <<EOF
Package: ms-rewards-assistant
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: MS Rewards Assistant Project <noreply@localhost>
Depends: libglib2.0-0, libx11-6, libxcb1, libxkbcommon-x11-0, libegl1, libgl1
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
