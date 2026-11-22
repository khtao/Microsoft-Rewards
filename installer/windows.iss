; Inno Setup script for the Windows installer.
; Build through build_windows.bat, which passes /DAppVersion=<version>.

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

#define MyAppName "积分管理助手"
#define MyAppExeName "MSRewardsAssistant.exe"

[Setup]
AppId={{8A6B6C9D-5E4A-4C3D-9D1B-2F6A4A5B7C81}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher=Independent Project
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
DefaultDirName={localappdata}\Programs\MSRewardsAssistant
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=Microsoft-Rewards-Assistant-Setup
SetupIconFile=..\assets\icons\app.ico
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
SetupLogging=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
; The full PyInstaller onedir bundle includes Python runtime, Qt, Playwright
; driver, application code, and resources. Recursive copy keeps _internal intact.
Source: "..\dist\MSRewardsAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Desktop and Start Menu shortcuts are created automatically for the current user.
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName}\卸载{#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动{#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  AppRunValuePrefix = 'MSRewardsAssistant';

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ValueNames: TArrayOfString;
  I: Integer;
  ValueName: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Remove this app's default and named-instance auto-start entries so Windows
      does not try to launch an executable that has already been uninstalled. }
    if RegGetValueNames(HKCU, RunKey, ValueNames) then
    begin
      for I := 0 to GetArrayLength(ValueNames) - 1 do
      begin
        ValueName := ValueNames[I];
        if (CompareText(ValueName, AppRunValuePrefix) = 0) or
           (CompareText(Copy(ValueName, 1, Length(AppRunValuePrefix) + 1),
                        AppRunValuePrefix + '_') = 0) then
          RegDeleteValue(HKCU, RunKey, ValueName);
      end;
    end;
  end;
end;
