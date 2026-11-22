; ===========================================================================
;  MSRewardsAssistant - Inno Setup 6 / 7 script
;
;  Deliberately PURE ASCII. All Chinese UI text lives in the official
;  ChineseSimplified.isl shipped with Inno Setup 6+, so there is no BOM,
;  no code page and no UTF-8-vs-ANSI issue in this file at all.
;
;  Build:  installer\build_inno.bat
;  Or:     double-click this file in "Inno Setup Compiler" and press Ctrl+F9
;
;  Layout (Source paths are relative to THIS script's directory):
;     installer\setup.iss
;     ..\dist\MSRewardsAssistant\        <- PyInstaller onedir output
;     ..\assets\icons\app.ico           <- installer + shortcut icon
;     ..\MSRewardsAssistantSetup.exe    <- OutputDir=..
; ===========================================================================
#define MyAppName "MSRewardsAssistant"
#define MyAppVersion "2.0"
#define MyAppPublisher "MS Rewards Assistant"
#define MyAppExeName "MSRewardsAssistant.exe"

[Setup]
; Fixed AppId so re-installs are recognised as upgrades and the uninstall
; entry stays stable across versions. Do NOT reuse for another application.
AppId={{30E2D155-B39B-49B9-9072-C6494626574A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

; Per-user install: no UAC, no Program Files, uninstall info goes to HKCU
; and therefore shows up in "Settings > Apps". User data lives in a DIFFERENT
; folder (%LOCALAPPDATA%\MSRewardsAssistant) and is never touched by uninstall.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest

; Keep the wizard short: no "choose folder", no "choose Start Menu group".
DisableDirPage=yes
DisableProgramGroupPage=yes
WizardStyle=modern

OutputDir=..
OutputBaseFilename=MSRewardsAssistantSetup
SetupIconFile=..\assets\icons\app.ico
Compression=lzma2
SolidCompression=yes

[Languages]
; Chinese is the default (official ChineseSimplified.isl, built into Inno
; Setup 6+). build_inno.bat passes /DEnglishOnly=1 when that file is missing,
; so a stripped-down compiler falls back to English instead of failing.
#ifdef EnglishOnly
Name: "english"; MessagesFile: "compiler:Default.isl"
#else
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
; No "unchecked" flag => the desktop shortcut is created AUTOMATICALLY.
; {cm:...} resolves through the language file, so this reads in Chinese too.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; One line packs the ENTIRE PyInstaller onedir tree and preserves _internal,
; assets\icons, platforms, etc. No CAB, no DDF, no flattening, no renaming.
Source: "..\dist\MSRewardsAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Optional standalone icon for the shortcuts (the exe already carries one).
Source: "..\assets\icons\app.ico"; DestDir: "{app}"; DestName: "app.ico"; Flags: ignoreversion skipifsourcedoesntexist

[InstallDelete]
; Runs BEFORE files are copied. Only {app} is cleared - never the user data
; folder (%LOCALAPPDATA%\MSRewardsAssistant).
Type: filesandordirs; Name: "{app}"

[Icons]
; Start Menu group (the "menu bar icon" Windows allows; Win10+ forbids
; programmatically pinning to the taskbar).
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0
Name: "{autoprograms}\{#MyAppName}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Tasks: desktopicon
