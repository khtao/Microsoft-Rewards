"""统一桌面应用入口：python -m rewards_assistant，亦作为打包入口。"""

# 非安装模式下自启动会直接调用本文件；保证从任何工作目录都能找到包。
if not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rewards_assistant.app import main


if __name__ == "__main__":
    raise SystemExit(main())
