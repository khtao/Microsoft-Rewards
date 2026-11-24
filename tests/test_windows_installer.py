# -*- coding: utf-8 -*-
"""Windows 安装包（Inno Setup）安装体验的离线静态契约。

覆盖用户要求的四件事：
* 安装包与向导语言跟随系统（中文 / 英文），不弹语言选择框；
* 提供「为所有用户安装」选项，默认仍是当前用户级；
* 发现已安装版本先静默卸载再装，用户数据目录不碰；
* 完成页提供中英双语「打开」选项，静默安装不启动。

Linux CI 里无法跑 ISCC，这里用源码级断言锁住指令、脚本与编码；
真正的安装/升级行为由 installer/verify_setup.ps1 在干净 Windows 上验证。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "installer" / "setup.iss"
BAT = ROOT / "installer" / "build_inno.bat"
PS1 = ROOT / "installer" / "verify_setup.ps1"
EN = ROOT / "installer" / "Messages.en.isl"
ZH = ROOT / "installer" / "Messages.zh.isl"

ISS_TEXT = ISS.read_text("utf-8")


def _ascii_crlf(path: Path) -> bytes:
    data = path.read_bytes()
    assert all(b < 128 for b in data), f"{path.name} 必须保持纯 ASCII"
    assert data.count(b"\r\n") == data.count(b"\n"), f"{path.name} 必须是 CRLF"
    return data


def _section(name: str) -> str:
    marker = f"[{name}]"
    start = ISS_TEXT.index(marker)
    tail = ISS_TEXT[start + len(marker):]
    nxt = tail.find("\n[")
    return tail[:nxt] if nxt != -1 else tail


# ---------------------------------------------------------------- 语言跟随系统

def test_setup_is_still_pure_ascii_crlf_and_single_version_source():
    _ascii_crlf(ISS)
    assert '#define MyAppVersion "2.1"' in ISS_TEXT
    assert "AppId={{30E2D155-B39B-49B9-9072-C6494626574A}" in ISS_TEXT


def test_wizard_language_follows_system_without_dialog():
    setup = _section("Setup")
    assert "LanguageDetectionMethod=uilanguage" in setup
    assert "ShowLanguageDialog=no" in setup, "跟随系统，不弹语言选择框"
    langs = _section("Languages")
    assert 'Name: "english"' in langs and 'Name: "chinesesimplified"' in langs
    assert langs.index('Name: "english"') < langs.index('Name: "chinesesimplified"'), \
        "english 必须是第一语言，非中文系统才会回落英文"
    assert "#ifdef EnglishOnly" in langs and "#else" in langs
    assert 'MessagesFile: "compiler:Default.isl,Messages.en.isl"' in langs
    assert ('MessagesFile: "compiler:Languages\\ChineseSimplified.isl,'
            'Messages.zh.isl"') in langs


def test_custom_messages_are_two_utf8_no_bom_isl_files():
    for f in (EN, ZH):
        raw = f.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{f.name} 不能带 BOM"
        text = raw.decode("utf-8")          # 必须是合法 UTF-8
        assert "[CustomMessages]" in text
        assert "MRA_LaunchApp=" in text
    en, zh = EN.read_text("utf-8"), ZH.read_text("utf-8")
    assert all(ord(c) < 128 for c in en), "英文消息文件可保持纯 ASCII"
    assert any(ord(c) > 127 for c in zh), "中文消息文件必须含中文"
    assert "打开 MSRewardsAssistant" in zh
    assert "Open MSRewardsAssistant" in en


# ---------------------------------------------------------------- 为所有用户安装

def test_install_mode_dialog_but_default_is_per_user():
    setup = _section("Setup")
    assert "PrivilegesRequired=lowest" in setup, "默认当前用户级、不弹 UAC"
    assert "PrivilegesRequiredOverridesAllowed=dialog" in setup, \
        "dialog 提供为所有用户安装 / 仅为我安装，并启用 /ALLUSERS /CURRENTUSER"
    assert "UsePreviousPrivileges=no" in setup, \
        "每次交互安装都显示选择，不静默沿用旧安装模式"
    assert "DefaultDirName={autopf}\\{#MyAppName}" in setup, \
        "auto 常量同时适配 Program Files 与 LOCALAPPDATA\\Programs"
    assert "CloseApplications=yes" in setup
    assert "{autoprograms}" in _section("Icons")
    assert "{autodesktop}" in _section("Icons")


def test_build_batch_documents_both_install_modes():
    src = _ascii_crlf(BAT).decode("ascii")
    assert "Messages.en.isl" in src and "Messages.zh.isl" in src
    assert ":nomsg" in src, "缺消息文件必须报错，而不是编译出无本地化的包"
    assert "/ALLUSERS" in src and "/CURRENTUSER" in src
    assert "/LANG=english" in src


# ---------------------------------------------------------------- 先卸载旧版、保留数据

def test_upgrade_uninstalls_previous_version_before_copying_files():
    code = _section("Code")
    for token in ("function InitializeSetup(): Boolean;",
                  "procedure UninstallOldVersion();",
                  "function FindUninstaller(var Uninstaller: String): Boolean;",
                  "RegQueryStringValue(HKLM, Key, 'UninstallString', Uninstaller)",
                  "RegQueryStringValue(HKCU, Key, 'UninstallString', Uninstaller)",
                  "SetupSetting(\"AppId\")",   # 注册表键与 [Setup] 的 AppId 同源
                  "RemoveQuotes(Cmd)",
                  "Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)",
                  "ShellExec('runas'",         # HKLM 旧版需要提权时的回退
                  "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"):
        assert token in code, token
    init = code[code.index("function InitializeSetup(): Boolean;"):]
    assert init.index("UninstallOldVersion();") < init.index("Result := True;"), \
        "卸载必须发生在安装放行之前"


def test_uninstall_never_touches_user_data():
    code = _section("Code")
    assert "function UserDataDir(): String;" in code
    assert "DirExists(UserDataDir())" in code
    assert "User data preserved" in code
    # 卸载器只清 {app}；[Code] 不允许出现删除数据目录的动作
    assert "DelTree(" not in code and "RemoveDir(" not in code
    delete = _section("InstallDelete")
    assert delete.count('Name: "{app}"') == 1
    assert delete.count("Name:") == 1, \
        "[InstallDelete] 只能指向 {app}，不能指向用户数据目录"


def test_verify_script_checks_upgrade_keeps_user_data():
    src = _ascii_crlf(PS1).decode("ascii")
    assert "/CURRENTUSER" in src, "静默安装显式选定当前用户，抑制安装模式对话框"
    assert "upgrade_marker" in src
    assert "User data marker survives upgrade" in src
    assert "No duplicate install after upgrade" in src
    assert "User data folder KEPT after uninstall" in src


# ---------------------------------------------------------------- 装完打开选项

def test_finishing_page_has_localised_open_checkbox():
    run = _section("Run")
    assert 'Filename: "{app}\\{#MyAppExeName}"' in run
    assert 'Description: "{cm:MRA_LaunchApp}"' in run
    for flag in ("postinstall", "nowait", "skipifsilent"):
        assert flag in run, flag
    assert "unchecked" not in run, "完成页打开选项默认勾选"
    assert "shellexec" not in run


# ---------------------------------------------------------------- 文档同步

def test_docs_describe_all_four_installer_behaviours():
    docs = {
        "README.md": ROOT / "README.md",
        "ARCHITECTURE.md": ROOT / "docs" / "ARCHITECTURE.md",
        "installer/README.md": ROOT / "installer" / "README.md",
        "verify_clean_vm.md": ROOT / "installer" / "verify_clean_vm.md",
    }
    for name, path in docs.items():
        text = path.read_text("utf-8")
        assert "语言" in text or "language" in text.lower(), name
        assert "所有用户" in text or "all users" in text.lower(), name
        assert "打开" in text or "Open" in text, name
    installer_doc = docs["installer/README.md"].read_text("utf-8")
    assert "先卸载" in installer_doc and "用户数据" in installer_doc
    assert "Messages.en.isl" in installer_doc
    assert "UsePreviousPrivileges=no" in installer_doc
