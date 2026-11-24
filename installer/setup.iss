; ===========================================================================
;  MSRewardsAssistant - Inno Setup 6 / 7 script
;
;  Deliberately PURE ASCII. All Chinese UI text lives in the official
;  ChineseSimplified.isl shipped with Inno Setup 6+, plus our own UTF-8
;  Messages.zh.isl. No BOM, no code page, no UTF-8-vs-ANSI issue in this file.
;
;  Build:  installer\build_inno.bat
;  Or:     double-click this file in "Inno Setup Compiler" and press Ctrl+F9
;
;  Layout (Source paths are relative to THIS script's directory):
;     installer\setup.iss
;     installer\Messages.en.isl / Messages.zh.isl   <- custom messages
;     ..\dist\MSRewardsAssistant\        <- PyInstaller onedir output
;     ..\assets\icons\app.ico           <- installer + shortcut icon
;     ..\MSRewardsAssistantSetup.exe    <- OutputDir=..
; ===========================================================================
#define MyAppName "MSRewardsAssistant"
#define MyAppVersion "2.2"
#define MyAppPublisher "MS Rewards Assistant"
#define MyAppExeName "MSRewardsAssistant.exe"
#define MyAppDataDir "MSRewardsAssistant"

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

; {autopf} maps to Program Files in "all users" mode and to
; %LOCALAPPDATA%\Programs in "just for me" mode. Both modes therefore get a
; correct install folder without hard-coding a per-user path.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; Default = current user (no UAC). 'dialog' adds the "Install for all users /
; Just for me" choice to the wizard, and enables /ALLUSERS + /CURRENTUSER for
; unattended installs. UsePreviousPrivileges=no keeps the choice visible on
; every interactive run instead of silently reusing a previous install mode.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UsePreviousPrivileges=no

; Language follows the system UI language: zh-CN -> Chinese, anything else
; -> English (english is listed first, so it is the fallback). No language
; selection dialog: the user never has to pick a language manually.
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=no

; Close a running MSRewardsAssistant.exe before replacing its files; this also
; lets the silent uninstall of an older version proceed without file locks.
CloseApplications=yes

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
; english FIRST = fallback on non-Chinese systems. Custom messages are layered
; on top of the official .isl files via the comma-separated MessagesFile list.
#ifdef EnglishOnly
Name: "english"; MessagesFile: "compiler:Default.isl,Messages.en.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl,Messages.en.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl,Messages.zh.isl"
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
; Runs BEFORE files are copied. Only {app} is cleared - NEVER the user data
; folder (%LOCALAPPDATA%\MSRewardsAssistant), which is owned by the app.
Type: filesandordirs; Name: "{app}"

[Icons]
; {autoprograms} / {autodesktop} automatically become common (all users) or
; user (just for me) folders depending on the chosen install mode.
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0
Name: "{autoprograms}\{#MyAppName}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Tasks: desktopicon

[Run]
; "Open MSRewardsAssistant" checkbox on the finishing page. Localised via the
; custom message files; skipifsilent keeps unattended installs from launching
; a GUI app (verify_setup.ps1 / CI stay headless).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:MRA_LaunchApp}"; Flags: postinstall nowait skipifsilent

[Code]
// ---------------------------------------------------------------------------
// Upgrade: uninstall a previous installation FIRST, then install fresh.
//
// User data is written by the application itself into
// %LOCALAPPDATA%\MSRewardsAssistant. The Inno Setup uninstaller only removes
// the files it installed under {app}, the shortcuts it created and its own
// registry entry, so that folder is never touched. We still log its state
// before/after the uninstall so "keep the data" is an explicit contract.
// ---------------------------------------------------------------------------

function UserDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#MyAppDataDir}');
end;

function FindUninstaller(var Uninstaller: String): Boolean;
var
  Key: String;
begin
  // Same AppId as [Setup]; check both hives (per-user and all-users installs).
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
         '{#emit SetupSetting("AppId")}' + '_is1';
  Result := RegQueryStringValue(HKLM, Key, 'UninstallString', Uninstaller);
  if not Result then
    Result := RegQueryStringValue(HKCU, Key, 'UninstallString', Uninstaller);
end;

procedure UninstallOldVersion();
var
  Cmd, Params: String;
  ResultCode: Integer;
  HadData: Boolean;
begin
  if not FindUninstaller(Cmd) then
  begin
    Log('No previous installation found; fresh install.');
    exit;
  end;

  HadData := DirExists(UserDataDir());
  Log('Previous installation found: uninstalling it before installing ' +
      '{#MyAppVersion}. User data stays in ' + UserDataDir());
  Cmd := RemoveQuotes(Cmd);
  Params := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-';

  // Wait for the uninstaller to finish: it deletes {app}, so copying new
  // files while it runs would race with it.
  if Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and
     (ResultCode = 0) then
  begin
    Log('Previous version uninstalled (exit code 0).');
  end
  else
  begin
    // An all-users (HKLM) install may need elevation to uninstall. 'runas'
    // triggers UAC only in this fallback case.
    if ShellExec('runas', Cmd, Params, '', SW_HIDE,
                 ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
      Log('Previous version uninstalled with elevation.')
    else
      Log('WARNING: previous version could not be uninstalled; continuing ' +
          'with an in-place upgrade (Inno Setup still replaces old files).');
  end;

  if HadData and not DirExists(UserDataDir()) then
    Log('WARNING: user data folder disappeared during uninstall!')
  else
    Log('User data preserved: ' + UserDataDir());
end;

function InitializeSetup(): Boolean;
begin
  // Earliest possible point, before the wizard and before any file is copied.
  // Silent, no UI, no language-dependent messages needed here.
  UninstallOldVersion();
  Result := True;
end;
