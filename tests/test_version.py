# -*- coding: utf-8 -*-
"""版本号单一真源：2.2 在源码、README、三平台打包配置里保持一致。

``config.APP_VERSION`` 是真源；`.deb` / macOS Info.plist / CI 都动态读它，
只有 Inno Setup 与其校验脚本各存一份字面量，必须锁住不漂移。
"""
from __future__ import annotations

import re
from pathlib import Path

from rewards_assistant.config import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.2"
STALE = re.compile(r"(?<![0-9A-Za-z_.])2\.[01](?![0-9A-Za-z_.])")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text("utf-8")


def test_app_version_is_the_single_source():
    assert APP_VERSION == VERSION
    assert _read("README.md").startswith(f"# 积分活动管理助手 {VERSION}")


def test_every_platform_packaging_uses_the_same_version():
    literals = {
        "MSRewardsAssistant.spec": f"APP_VERSION = '{VERSION}'",
        "installer/setup.iss": f'#define MyAppVersion "{VERSION}"',
        "installer/verify_setup.ps1": f"$VersionExpected = '{VERSION}'",
        "build_macos.sh": f'echo "{VERSION}")',
        "docs/ARCHITECTURE.md": f"架构说明（{VERSION}）",
        "installer/README.md": f"Inno Setup，{VERSION}",
        "installer/verify_clean_vm.md": f"如何验证 {VERSION} 安装包",
    }
    for rel, expected in literals.items():
        assert expected in _read(rel), rel


def test_no_stale_version_string_in_docs_and_packaging():
    for rel in ("README.md", "docs/ARCHITECTURE.md",
                "installer/README.md", "installer/verify_clean_vm.md",
                "installer/setup.iss", "installer/verify_setup.ps1",
                "MSRewardsAssistant.spec"):
        assert not STALE.search(_read(rel)), rel


def test_readme_is_only_intro_and_install():
    """README 保持精简：除标题外只有「安装」一个二级章节。"""
    text = _read("README.md")
    headings = [line for line in text.splitlines()
                if line.startswith("## ")]
    assert headings == ["## 安装"], headings
    assert len(text.splitlines()) < 100
    for token in ("--instance", "多开", "MS_REWARDS_INSTANCE"):
        assert token in text, token
