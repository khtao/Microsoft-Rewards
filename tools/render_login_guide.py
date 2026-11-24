# -*- coding: utf-8 -*-
"""离屏导出登录流程动图与登录对话框预览图。

    python tools/render_login_guide.py

产出 docs/previews/ 下：

* ``login_flow.gif``        —— 5 帧循环演示（真正的 GIF 动图，可放进文档）
* ``login_flow.png``        —— 动图首帧静态海报
* ``login_dialog.png``      —— 美化后的登录对话框（浅色）
* ``login_dialog_dark.png`` —— 深色主题下的同一对话框

界面里播放的是同一套 ``paint_guide()`` 绘制的 Qt 实时动画（不依赖 GIF
解码插件），这里只是把那些帧导出来给人看。全部离线，不启动浏览器。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "MS_REWARDS_HOME", tempfile.mkdtemp(prefix="hsa-login-preview-"))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PIL import Image  # noqa: E402

from rewards_assistant.ui.login_guide import (  # noqa: E402
    FRAME_COUNT, render_frame_image,
)
from rewards_assistant.ui.modern import theme  # noqa: E402

OUT = ROOT / "docs" / "previews"
OUT.mkdir(parents=True, exist_ok=True)

# 每帧 6 个采样：0.02/0.08/0.16 覆盖交叉淡入，后 3 个展示帧内微动
PHASES = (0.02, 0.08, 0.16, 0.35, 0.60, 0.85)
FRAME_MS = 150


def to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenMode.WriteOnly)
    img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return Image.open(io.BytesIO(data)).convert("RGB")


def export_gif(pal: dict) -> None:
    frames: list[Image.Image] = []
    for frame in range(FRAME_COUNT):
        for phase in PHASES:
            fading = phase < 0.16
            prev = (frame - 1) % FRAME_COUNT if fading else None
            blend = min(1.0, phase / 0.16) if fading else 1.0
            img = render_frame_image(frame, phase, pal,
                                     prev_frame=prev, blend=blend)
            frames.append(to_pil(img))
    gif = OUT / "login_flow.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, optimize=True, disposal=2)
    frames[0].save(OUT / "login_flow.png")
    print(f"  {gif.name}: {len(frames)} frames, {gif.stat().st_size:,} bytes")


def export_dialog(app: QApplication, scheme: str) -> None:
    from rewards_assistant.ui.login_dialog import LoginDialog

    theme.set_scheme(scheme)
    dlg = LoginDialog()
    # 定格在第 2 帧（浏览器里本人登录），截图内容稳定可复现
    dlg.guide.freeze(1, 0.45)
    dlg.show()
    app.processEvents()
    name = "login_dialog_dark.png" if scheme == "dark" else "login_dialog.png"
    path = OUT / name
    dlg.grab().save(str(path))
    print(f"  {name}: {dlg.width()}x{dlg.height()}, {path.stat().st_size:,} bytes")
    dlg.close()
    app.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    theme.set_scheme("light")
    export_gif(theme.current())
    export_dialog(app, "light")
    export_dialog(app, "dark")
    theme.set_scheme("light")
    print("login guide previews OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
