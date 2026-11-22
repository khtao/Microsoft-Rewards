<#
verify_setup.ps1 - clean-system verification for MSRewardsAssistantSetup.exe

Runs on Windows PowerShell 5.1. NO extra modules, NO admin rights, NO network.
Copy ONLY this script (and optionally dist_manifest.json / the setup exe) into
a freshly snapshotted clean VM.

Typical one-shot run:

  powershell -ExecutionPolicy Bypass -NoProfile -File .\verify_setup.ps1 `
      -Mode Full `
      -SetupExe "C:\Users\Public\Desktop\MSRewardsAssistantSetup.exe" `
      -Manifest "C:\Users\Public\Desktop\dist_manifest.json"

Modes:
  Pre          assert the machine is clean (nothing of this app is present)
  Installed    assert install result: files, tree, shortcuts, registry, launch
  Uninstalled  assert uninstall result: program gone, shortcuts/registry gone,
               user data folder KEPT
  Manifest     build a SHA256 manifest of the BUILD machine's dist folder
  Full         Pre -> silent install -> Installed -> silent uninstall ->
               Uninstalled (one command, exit code 0 = all checks passed)

Use -SkipLaunch on Full if the GUI app must not be started (unattended CI).
Use -SourceDir / -ManifestOut with -Mode Manifest on the build machine.
#>
[CmdletBinding()]
param(
    [ValidateSet('Pre','Installed','Uninstalled','Manifest','Full')]
    [string]$Mode = 'Pre',
    [string]$SetupExe = '',
    [string]$SourceDir = '',
    [string]$Manifest = '',
    [string]$ManifestOut = '',
    [string]$ReportDir = '',
    [switch]$SkipLaunch
)

$ErrorActionPreference = 'Continue'
$AppName = 'MSRewardsAssistant'
$VersionExpected = '2.0'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\MSRewardsAssistant'
$DataDir = Join-Path $env:LOCALAPPDATA 'MSRewardsAssistant'
$Desktop = [Environment]::GetFolderPath('Desktop')
$Programs = [Environment]::GetFolderPath('Programs')
$Group = Join-Path $Programs $AppName
$DeskLnk = Join-Path $Desktop ($AppName + '.lnk')
$GroupAppLnk = Join-Path $Group ($AppName + '.lnk')
$UninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
$MainExe = Join-Path $InstallDir ($AppName + '.exe')
$UninsExe = Join-Path $InstallDir 'unins000.exe'
$SetupLog = Join-Path $env:TEMP 'mra_inno_install.log'
$UninsLog = Join-Path $env:TEMP 'mra_inno_uninstall.log'
if (-not $ReportDir) { $ReportDir = $env:TEMP }

$script:Results = New-Object System.Collections.ArrayList

function Add-Result {
    param([string]$Name, [bool]$OK, [string]$Detail)
    $mark = 'FAIL'
    if ($OK) { $mark = 'PASS' }
    $null = $script:Results.Add([pscustomobject]@{ Check = $Name; Result = $mark; Detail = $Detail })
    Write-Host ('[' + $mark + '] ' + $Name + ' -- ' + $Detail)
}
function Add-Info {
    param([string]$Name, [string]$Detail)
    Write-Host ('[INFO] ' + $Name + ' -- ' + $Detail)
}
function Get-LnkInfo {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut($Path)
        return [pscustomobject]@{
            TargetPath       = $lnk.TargetPath
            Arguments        = $lnk.Arguments
            IconLocation     = $lnk.IconLocation
            WorkingDirectory = $lnk.WorkingDirectory
        }
    } catch { return $null }
}
function Find-UninstallEntry {
    if (-not (Test-Path -LiteralPath $UninstallRoot)) { return $null }
    foreach ($k in Get-ChildItem -LiteralPath $UninstallRoot -ErrorAction SilentlyContinue) {
        $p = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
        if ($p -and $p.DisplayName -eq $AppName) { return $p }
    }
    return $null
}
function Get-FileList {
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath $Dir)) { return @() }
    return @(Get-ChildItem -LiteralPath $Dir -Recurse -File -ErrorAction SilentlyContinue)
}
function New-DistManifest {
    param([string]$Src, [string]$Out)
    if (-not (Test-Path -LiteralPath $Src)) { throw ('Source dir not found: ' + $Src) }
    $root = (Resolve-Path -LiteralPath $Src).Path.TrimEnd('\')
    $items = @()
    foreach ($f in (Get-ChildItem -LiteralPath $root -Recurse -File)) {
        $rel = $f.FullName.Substring($root.Length + 1)
        $h = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
        $items += [pscustomobject]@{ Path = $rel; Size = $f.Length; SHA256 = $h }
    }
    $json = ConvertTo-Json -InputObject @($items) -Depth 3
    Set-Content -LiteralPath $Out -Value $json -Encoding UTF8
    Write-Host ('[INFO] Manifest written: ' + $Out + ' (' + $items.Count + ' files)')
}
function Compare-DistManifest {
    param([string]$Mf, [string]$Dir)
    if (-not (Test-Path -LiteralPath $Mf)) {
        Add-Result 'Manifest file available' $false ('missing: ' + $Mf)
        return
    }
    Add-Result 'Manifest file available' $true $Mf
    $expected = @(Get-Content -LiteralPath $Mf -Raw | ConvertFrom-Json)
    $missing = @()
    $sizeBad = @()
    $hashBad = @()
    foreach ($e in $expected) {
        $p = Join-Path $Dir $e.Path
        if (-not (Test-Path -LiteralPath $p)) { $missing += $e.Path; continue }
        $f = Get-Item -LiteralPath $p
        if ($f.Length -ne [int64]$e.Size) { $sizeBad += $e.Path; continue }
        $h = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
        if ($h -ne $e.SHA256) { $hashBad += $e.Path }
    }
    $missDetail = 'missing=' + $missing.Count
    if ($missing.Count -gt 0) { $missDetail = $missDetail + ' e.g. ' + $missing[0] }
    Add-Result ('Manifest files present (' + $expected.Count + ')') ($missing.Count -eq 0) $missDetail
    Add-Result 'Manifest file sizes match' ($sizeBad.Count -eq 0) ('mismatched=' + $sizeBad.Count)
    Add-Result 'Manifest SHA256 hashes match' ($hashBad.Count -eq 0) ('mismatched=' + $hashBad.Count)
    if ($hashBad.Count -gt 0) { Add-Info 'First hash mismatch' $hashBad[0] }
    $extra = @()
    foreach ($f in (Get-FileList $Dir)) {
        $rel = $f.FullName.Substring($Dir.TrimEnd('\').Length + 1)
        $known = $false
        foreach ($e in $expected) { if ($e.Path -eq $rel) { $known = $true; break } }
        if (-not $known) { $extra += $rel }
    }
    Add-Info 'Extra files added by installer (expected: unins000.* / app.ico)' ($extra -join ', ')
}
function Test-CleanMachine {
    Add-Result 'Install folder absent' (-not (Test-Path -LiteralPath $InstallDir)) $InstallDir
    Add-Result 'Desktop shortcut absent' (-not (Test-Path -LiteralPath $DeskLnk)) $DeskLnk
    Add-Result 'Start Menu group absent' (-not (Test-Path -LiteralPath $Group)) $Group
    $u = Find-UninstallEntry
    Add-Result 'Uninstall registry entry absent' ($null -eq $u) $UninstallRoot
    $proc = @(Get-Process -Name $AppName -ErrorAction SilentlyContinue)
    Add-Result 'No running app process' ($proc.Count -eq 0) ('count=' + $proc.Count)
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { Add-Info 'python command' ('PRESENT (' + [string]$py.Source + ') - Win10/11 ships a Store alias stub, this alone does NOT mean Python is installed') }
    else { Add-Info 'python command' 'ABSENT - clean baseline for dependency testing' }
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    $inno = (Test-Path -LiteralPath "$env:ProgramFiles\Inno Setup 6")
    if ($pf86) { $inno = $inno -or (Test-Path -LiteralPath (Join-Path $pf86 'Inno Setup 6')) }
    Add-Result 'No Inno Setup installed (proves no build toolchain)' (-not $inno) 'Inno Setup 6'
    $vc = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64' -ErrorAction SilentlyContinue
    if ($vc) { Add-Info 'VC++ 2015-2022 x64 redist' 'PRESENT (not a clean baseline for dependency testing)' }
    else { Add-Info 'VC++ 2015-2022 x64 redist' 'NOT installed - a successful install here proves zero runtime dependency' }
}
function Test-Installed {
    Add-Result 'Install folder created' (Test-Path -LiteralPath $InstallDir) $InstallDir
    Add-Result 'Main executable present' (Test-Path -LiteralPath $MainExe) $MainExe
    $internal = Join-Path $InstallDir '_internal'
    Add-Result '_internal folder present (onedir tree preserved)' (Test-Path -LiteralPath $internal) $internal
    $files = Get-FileList $InstallDir
    $total = ($files | Measure-Object -Property Length -Sum).Sum
    $mb = [math]::Round(($total / 1MB), 1)
    Add-Result 'Installed file count' ($files.Count -gt 1) ([string]$files.Count + ' files, ' + [string]$mb + ' MB')
    $shib = @(Get-ChildItem -LiteralPath $internal -Recurse -File -Filter 'shiboken6*.dll' -ErrorAction SilentlyContinue)
    Add-Result 'shiboken6 DLL present (PySide6 runtime)' ($shib.Count -gt 0) ([string]$shib.Count + ' found')
    $qt = @(Get-ChildItem -LiteralPath $internal -Recurse -File -Filter 'qwindows.dll' -ErrorAction SilentlyContinue)
    Add-Result 'Qt windows platform plugin present' ($qt.Count -gt 0) ([string]$qt.Count + ' found')

    if ($Manifest) { Compare-DistManifest $Manifest $InstallDir }
    else { Add-Info 'Byte-level manifest comparison' 'SKIPPED (pass -Manifest to enable)' }

    Add-Result 'Desktop shortcut created' (Test-Path -LiteralPath $DeskLnk) $DeskLnk
    $dl = Get-LnkInfo $DeskLnk
    if ($dl) {
        Add-Result 'Desktop shortcut points to installed exe' ($dl.TargetPath -eq $MainExe) $dl.TargetPath
        Add-Result 'Desktop shortcut working dir' ($dl.WorkingDirectory -eq $InstallDir) $dl.WorkingDirectory
    } else { Add-Result 'Desktop shortcut readable' $false 'cannot open .lnk' }

    Add-Result 'Start Menu group created' (Test-Path -LiteralPath $Group) $Group
    Add-Result 'Start Menu app shortcut' (Test-Path -LiteralPath $GroupAppLnk) $GroupAppLnk
    $groupLnks = @(Get-ChildItem -LiteralPath $Group -Filter '*.lnk' -ErrorAction SilentlyContinue)
    Add-Result 'Start Menu has uninstall entry' ($groupLnks.Count -ge 2) ([string]$groupLnks.Count + ' shortcut(s)')

    $u = Find-UninstallEntry
    Add-Result 'Uninstall registry entry present (HKCU)' ($null -ne $u) $UninstallRoot
    if ($u) {
        Add-Result 'DisplayName matches' ($u.DisplayName -eq $AppName) ([string]$u.DisplayName)
        if ($u.DisplayVersion) {
            Add-Result 'DisplayVersion matches' ($u.DisplayVersion -eq $VersionExpected) ([string]$u.DisplayVersion)
        } else { Add-Info 'DisplayVersion' 'not written by this compiler version (informational only)' }
        if ($u.InstallLocation) {
            Add-Result 'InstallLocation points to install dir' ($u.InstallLocation -eq $InstallDir) ([string]$u.InstallLocation)
        } else { Add-Info 'InstallLocation' 'not written by this compiler version (informational only)' }
        Add-Result 'UninstallString present' (-not [string]::IsNullOrEmpty($u.UninstallString)) ([string]$u.UninstallString)
    }
    Add-Result 'Uninstaller exe present' (Test-Path -LiteralPath $UninsExe) $UninsExe

    if (-not $SkipLaunch) {
        Add-Info 'Launch test' 'starting main exe for 5 seconds...'
        try {
            $p = Start-Process -FilePath $MainExe -WorkingDirectory $InstallDir -PassThru
            Start-Sleep -Seconds 5
            $alive = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
            Add-Result 'Main program stays alive 5s (no missing DLL)' ($null -ne $alive) ('PID=' + $p.Id)
        } catch {
            Add-Result 'Main program starts' $false $_.Exception.Message
        }
        Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    } else { Add-Info 'Launch test' 'SKIPPED (-SkipLaunch)' }
}
function Test-Uninstalled {
    Add-Result 'Install folder removed' (-not (Test-Path -LiteralPath $InstallDir)) $InstallDir
    Add-Result 'Desktop shortcut removed' (-not (Test-Path -LiteralPath $DeskLnk)) $DeskLnk
    Add-Result 'Start Menu group removed' (-not (Test-Path -LiteralPath $Group)) $Group
    $u = Find-UninstallEntry
    Add-Result 'Uninstall registry entry removed' ($null -eq $u) $UninstallRoot
    $dataMsg = 'not present yet (app has not written data): ' + $DataDir
    if (Test-Path -LiteralPath $DataDir) { $dataMsg = 'KEPT: ' + $DataDir }
    Add-Info 'User data folder' $dataMsg
    $proc = @(Get-Process -Name $AppName -ErrorAction SilentlyContinue)
    Add-Result 'No leftover app process' ($proc.Count -eq 0) ('count=' + $proc.Count)
}
function Write-Report {
    param([string]$Tag)
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $csv = Join-Path $ReportDir ('mra_verify_' + $Tag + '_' + $stamp + '.csv')
    $script:Results | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
    $fail = @($script:Results | Where-Object { $_.Result -eq 'FAIL' }).Count
    $pass = @($script:Results | Where-Object { $_.Result -eq 'PASS' }).Count
    Write-Host ''
    Write-Host ('==== SUMMARY (' + $Tag + '): ' + $pass + ' PASS, ' + $fail + ' FAIL ====')
    Write-Host ('Report: ' + $csv)
    return $fail
}

# ---------------------------------------------------------------------------
$failCount = 0

if ($Mode -eq 'Manifest') {
    if (-not $SourceDir) { $SourceDir = Join-Path $PSScriptRoot '..\dist\MSRewardsAssistant' }
    if (-not $ManifestOut) { $ManifestOut = Join-Path $PSScriptRoot 'dist_manifest.json' }
    New-DistManifest $SourceDir $ManifestOut
    exit 0
}

if ($Mode -eq 'Pre') {
    Test-CleanMachine
    $failCount = Write-Report 'pre'
}
elseif ($Mode -eq 'Installed') {
    Test-Installed
    $failCount = Write-Report 'installed'
}
elseif ($Mode -eq 'Uninstalled') {
    Test-Uninstalled
    $failCount = Write-Report 'uninstalled'
}
elseif ($Mode -eq 'Full') {
    if (-not $SetupExe -or -not (Test-Path -LiteralPath $SetupExe)) {
        Write-Host '[ERROR] -SetupExe must point to the built MSRewardsAssistantSetup.exe'
        exit 2
    }
    $SetupExe = (Resolve-Path -LiteralPath $SetupExe).Path
    Add-Info 'Setup package' $SetupExe
    Write-Host ''
    Write-Host '==== PHASE 1: clean baseline ===='
    Test-CleanMachine

    Write-Host ''
    Write-Host '==== PHASE 2: silent install ===='
    $setupArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG="' + $SetupLog + '"'
    $p = Start-Process -FilePath $SetupExe -ArgumentList $setupArgs -Wait -PassThru
    Add-Result ('Silent setup exit code (0 = success)') ($p.ExitCode -eq 0) ('exit=' + $p.ExitCode)
    if (Test-Path -LiteralPath $SetupLog) {
        $lc = @(Get-Content -LiteralPath $SetupLog)
        Add-Info 'Inno Setup log' ($SetupLog + ' (' + $lc.Count + ' lines)')
    } else { Add-Info 'Inno Setup log' ('not found: ' + $SetupLog) }

    Write-Host ''
    Write-Host '==== PHASE 3: installed state ===='
    Test-Installed

    Write-Host ''
    Write-Host '==== PHASE 4: silent uninstall ===='
    if (Test-Path -LiteralPath $UninsExe) {
        $uninsArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="' + $UninsLog + '"'
        $up = Start-Process -FilePath $UninsExe -ArgumentList $uninsArgs -Wait -PassThru
        Add-Result 'Silent uninstall exit code' ($up.ExitCode -eq 0) ('exit=' + $up.ExitCode)
    } else {
        Add-Result 'Uninstaller available to run' $false $UninsExe
    }
    Start-Sleep -Seconds 3

    Write-Host ''
    Write-Host '==== PHASE 5: uninstalled state ===='
    Test-Uninstalled
    if (Test-Path -LiteralPath $InstallDir) {
        $left = @(Get-ChildItem -LiteralPath $InstallDir -Recurse -ErrorAction SilentlyContinue)
        Add-Info 'Leftover files in install folder' ([string]$left.Count + ' item(s) - usually caused by files the app wrote into {app}')
    }

    $failCount = Write-Report 'full'
}

if ($failCount -gt 0) {
    Write-Host ''
    Write-Host ('VERIFICATION FAILED: ' + $failCount + ' check(s) failed.')
    exit 1
}
Write-Host ''
Write-Host 'VERIFICATION PASSED'
exit 0
