[CmdletBinding()]
param(
    [string]$AppPath = "",
    [string]$PythonExe = "",
    [string]$OutputRoot = "",
    [string]$LibraryRootToRedact = "",
    [ValidateRange(1, 120)]
    [int]$MaxMinutes = 30,
    [string[]]$AppArguments = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Resolve-Executable {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return (Get-Item -LiteralPath $Candidate).FullName
    }
    $command = Get-Command $Candidate -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return $null
}

function Resolve-DiagnosticLaunch {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [string]$RequestedApp,
        [string]$RequestedPython,
        [string[]]$ExtraArguments
    )

    if ($RequestedApp) {
        $resolvedApp = Resolve-Executable -Candidate $RequestedApp
        if (-not $resolvedApp) {
            throw "Application executable was not found: $RequestedApp"
        }
        return [pscustomobject]@{
            FilePath = $resolvedApp
            Arguments = @($ExtraArguments)
            Mode = "packaged"
            WorkingDirectory = (Split-Path -Parent $resolvedApp)
        }
    }

    $entrypoint = Join-Path $RepositoryRoot "src\entrypoint.py"
    if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
        throw "Source entrypoint was not found: $entrypoint"
    }

    $pythonCandidates = New-Object System.Collections.Generic.List[string]
    if ($RequestedPython) {
        $pythonCandidates.Add($RequestedPython) | Out-Null
    }
    else {
        $pythonCandidates.Add((Join-Path $RepositoryRoot ".venv\Scripts\python.exe")) |
            Out-Null
        $pythonCandidates.Add("python.exe") | Out-Null
    }
    foreach ($candidate in $pythonCandidates) {
        $resolvedPython = Resolve-Executable -Candidate $candidate
        if (-not $resolvedPython) {
            continue
        }
        $arguments = New-Object System.Collections.Generic.List[string]
        $arguments.Add(('"{0}"' -f $entrypoint)) | Out-Null
        foreach ($argument in $ExtraArguments) {
            $arguments.Add($argument) | Out-Null
        }
        return [pscustomobject]@{
            FilePath = $resolvedPython
            Arguments = @($arguments)
            Mode = "source"
            WorkingDirectory = $RepositoryRoot
        }
    }

    $builtApp = Join-Path $RepositoryRoot "build\entrypoint.dist\entrypoint.exe"
    if (Test-Path -LiteralPath $builtApp -PathType Leaf) {
        return [pscustomobject]@{
            FilePath = (Get-Item -LiteralPath $builtApp).FullName
            Arguments = @($ExtraArguments)
            Mode = "packaged"
            WorkingDirectory = (Split-Path -Parent $builtApp)
        }
    }
    throw "No source Python or packaged entrypoint.exe was found. Pass -AppPath explicitly."
}

function Add-ReproductionMarker {
    param(
        [Parameter(Mandatory = $true)][string]$MarkerPath,
        [Parameter(Mandatory = $true)][string]$Marker,
        [int]$ProcessId = 0
    )

    $payload = [ordered]@{
        marker = $Marker
        utc_time = [DateTime]::UtcNow.ToString("o")
        process_id = $ProcessId
    }
    ($payload | ConvertTo-Json -Compress) | Add-Content -LiteralPath $MarkerPath -Encoding UTF8
}

function Write-SystemSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)]$Launch,
        [Parameter(Mandatory = $true)][string]$SessionId,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )

    $operatingSystem = $null
    $computerSystem = $null
    $videoControllers = @()
    try {
        $operatingSystem = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop |
            Select-Object Caption, Version, BuildNumber, OSArchitecture,
                TotalVisibleMemorySize, FreePhysicalMemory
    }
    catch {}
    try {
        $computerSystem = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop |
            Select-Object Manufacturer, Model, TotalPhysicalMemory
    }
    catch {}
    try {
        $videoControllers = @(Get-CimInstance Win32_VideoController -ErrorAction Stop |
            Select-Object Name, DriverVersion, AdapterRAM, VideoModeDescription)
    }
    catch {}

    $appFile = Get-Item -LiteralPath $Launch.FilePath
    $appHash = $null
    try {
        $appHash = (Get-FileHash -LiteralPath $Launch.FilePath -Algorithm SHA256).Hash
    }
    catch {}
    $gitCommit = $null
    $gitDirty = $null
    try {
        $git = Get-Command git.exe -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
        $gitCommit = (& $git.Source -C $RepositoryRoot rev-parse HEAD 2>$null |
            Select-Object -First 1)
        $gitStatus = @(& $git.Source -C $RepositoryRoot status --porcelain 2>$null)
        $gitDirty = $gitStatus.Count -gt 0
    }
    catch {}
    $snapshot = [ordered]@{
        session_id = $SessionId
        collected_utc = [DateTime]::UtcNow.ToString("o")
        collector_version = 1
        launch_mode = $Launch.Mode
        application_name = $appFile.Name
        application_size_bytes = $appFile.Length
        application_sha256 = $appHash
        application_file_version = $appFile.VersionInfo.FileVersion
        source_git_commit = $gitCommit
        source_git_dirty = $gitDirty
        powershell_version = $PSVersionTable.PSVersion.ToString()
        operating_system = $operatingSystem
        computer = $computerSystem
        video_controllers = $videoControllers
    }
    $snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Target -Encoding UTF8
}

function Write-ProcessMetric {
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)][string]$Target
    )

    try {
        $Process.Refresh()
        if ($Process.HasExited) {
            return
        }
        $hung = $false
        $gdiObjects = 0
        $userObjects = 0
        $windowHandle = [IntPtr]::Zero
        try {
            $windowHandle = $Process.MainWindowHandle
            if ($windowHandle -ne [IntPtr]::Zero) {
                $hung = [IPhotoDiag.NativeMethods]::IsHungAppWindow($windowHandle)
            }
            $gdiObjects = [IPhotoDiag.NativeMethods]::GetGuiResources($Process.Handle, 0)
            $userObjects = [IPhotoDiag.NativeMethods]::GetGuiResources($Process.Handle, 1)
        }
        catch {}
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $values = @(
            [DateTime]::UtcNow.ToString("o"),
            $Process.Id,
            $Process.TotalProcessorTime.TotalSeconds.ToString("F3", $culture),
            ([Math]::Round($Process.WorkingSet64 / 1MB, 3)).ToString($culture),
            ([Math]::Round($Process.PrivateMemorySize64 / 1MB, 3)).ToString($culture),
            $Process.HandleCount,
            $Process.Threads.Count,
            $gdiObjects,
            $userObjects,
            [int]($windowHandle -ne [IntPtr]::Zero),
            [int]$hung
        )
        ($values -join ",") | Add-Content -LiteralPath $Target -Encoding UTF8
    }
    catch {}
}

function Collect-ApplicationEvents {
    param(
        [Parameter(Mandatory = $true)][DateTime]$StartTime,
        [Parameter(Mandatory = $true)][string]$Target
    )

    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName = "Application"
            StartTime = $StartTime.AddMinutes(-5)
        } -ErrorAction Stop | Where-Object {
            $_.LevelDisplayName -in @("Critical", "Error", "Warning") -or
            $_.ProviderName -match "Application Error|Windows Error Reporting|Display|Qt"
        } | Select-Object -First 200 | ForEach-Object {
            [pscustomobject]@{
                TimeCreated = $_.TimeCreated.ToUniversalTime().ToString("o")
                Id = $_.Id
                Level = $_.LevelDisplayName
                Provider = $_.ProviderName
                Message = (([string]$_.Message -replace "[\r\n]+", " ").Trim())
            }
        }
        $events | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Target -Encoding UTF8
    }
    catch {
        @{ error = $_.Exception.GetType().Name } | ConvertTo-Json |
            Set-Content -LiteralPath $Target -Encoding UTF8
    }
}

function Protect-TextArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object[]]$Replacements
    )

    $extensions = @(".log", ".json", ".jsonl", ".csv", ".txt")
    Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
        $extensions -contains $_.Extension.ToLowerInvariant()
    } | ForEach-Object {
        $source = $_.FullName
        $temporary = "$source.redacting"
        $reader = [IO.File]::OpenText($source)
        $writer = New-Object IO.StreamWriter($temporary, $false, (New-Object Text.UTF8Encoding($false)))
        try {
            while (($line = $reader.ReadLine()) -ne $null) {
                foreach ($replacement in $Replacements) {
                    if (-not $replacement.Original) {
                        continue
                    }
                    $line = [regex]::Replace(
                        $line,
                        [regex]::Escape($replacement.Original),
                        $replacement.Token,
                        [Text.RegularExpressions.RegexOptions]::IgnoreCase
                    )
                }
                $writer.WriteLine($line)
            }
        }
        finally {
            $reader.Dispose()
            $writer.Dispose()
        }
        Move-Item -LiteralPath $temporary -Destination $source -Force
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "This collector must be run on Windows."
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$launch = Resolve-DiagnosticLaunch -RepositoryRoot $repositoryRoot `
    -RequestedApp $AppPath -RequestedPython $PythonExe -ExtraArguments $AppArguments

if (-not $OutputRoot) {
    $OutputRoot = [Environment]::GetFolderPath("Desktop")
    if (-not $OutputRoot) {
        $OutputRoot = (Get-Location).Path
    }
}
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$OutputRoot = (Get-Item -LiteralPath $OutputRoot).FullName
$sessionId = "iPhoto-windows-scan-playback-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
$sessionDir = Join-Path $OutputRoot $sessionId
$zipPath = Join-Path $OutputRoot "$sessionId.zip"
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $sessionDir "app-logs") | Out-Null

if (-not ("IPhotoDiag.NativeMethods" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace IPhotoDiag {
    public static class NativeMethods {
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool IsHungAppWindow(IntPtr windowHandle);

        [DllImport("user32.dll")]
        public static extern uint GetGuiResources(IntPtr processHandle, uint flag);
    }
}
"@
}

$stdoutPath = Join-Path $sessionDir "stdout.log"
$stderrPath = Join-Path $sessionDir "stderr.log"
$stackPath = Join-Path $sessionDir "runtime_stacks.log"
$detailPath = Join-Path $sessionDir "detail_events.jsonl"
$markerPath = Join-Path $sessionDir "reproduction_markers.jsonl"
$metricsPath = Join-Path $sessionDir "process_metrics.csv"
$systemPath = Join-Path $sessionDir "system.json"
$eventPath = Join-Path $sessionDir "windows_application_events.json"
$startedAt = Get-Date

"utc_time,pid,cpu_seconds,working_set_mb,private_mb,handles,threads,gdi_objects," +
    "user_objects,has_main_window,is_hung" |
    Set-Content -LiteralPath $metricsPath -Encoding UTF8
Write-SystemSnapshot -Target $systemPath -Launch $launch -SessionId $sessionId `
    -RepositoryRoot $repositoryRoot

$diagnosticEnvironment = [ordered]@{
    IPHOTO_LOG_DIR = (Join-Path $sessionDir "app-logs")
    IPHOTO_DETAIL_PROFILE = "1"
    IPHOTO_DETAIL_PROFILE_PATH = $detailPath
    IPHOTO_PERF_LOG = "1"
    IPHOTO_PERF_PRIVACY_SAFE = "1"
    IPHOTO_PERF_PRIVACY_SALT = [Guid]::NewGuid().ToString("N")
    IPHOTO_RUNTIME_DIAG = "1"
    IPHOTO_RUNTIME_DIAG_STACK_PATH = $stackPath
    IPHOTO_RUNTIME_DIAG_INTERVAL_SEC = "5"
    IPHOTO_STARTUP_HANG_DIAG = "1"
    PYTHONFAULTHANDLER = "1"
    QT_LOGGING_RULES = "qt.qpa.gl=true;qt.rhi.*=true;qt.multimedia.*=true"
}
if ($launch.Mode -eq "source") {
    $sourceRoot = Join-Path $repositoryRoot "src"
    $inheritedPythonPath = [Environment]::GetEnvironmentVariable(
        "PYTHONPATH",
        "Process"
    )
    $diagnosticEnvironment["PYTHONPATH"] = if ($inheritedPythonPath) {
        "$sourceRoot$([IO.Path]::PathSeparator)$inheritedPythonPath"
    }
    else {
        $sourceRoot
    }
}
$previousEnvironment = @{}
foreach ($key in $diagnosticEnvironment.Keys) {
    $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
    [Environment]::SetEnvironmentVariable(
        $key,
        [string]$diagnosticEnvironment[$key],
        "Process"
    )
}

$process = $null
$collectorError = $null
try {
    $startParameters = @{
        FilePath = $launch.FilePath
        WorkingDirectory = $launch.WorkingDirectory
        PassThru = $true
        RedirectStandardOutput = $stdoutPath
        RedirectStandardError = $stderrPath
    }
    if ($launch.Arguments.Count -gt 0) {
        $startParameters["ArgumentList"] = $launch.Arguments
    }
    $process = Start-Process @startParameters
    Add-ReproductionMarker -MarkerPath $markerPath -Marker "application_started" `
        -ProcessId $process.Id

    Write-Host ""
    Write-Host "iPhoto diagnostic collection started (PID $($process.Id))." -ForegroundColor Cyan
    Write-Host "1. Reproduce scanning until still photos become blank / Edit stops responding."
    Write-Host "2. When the problem is visible, return here and press R once."
    Write-Host "3. Then close iPhoto normally. If it cannot close, press Q here to stop it."
    Write-Host "Collection automatically stops after $MaxMinutes minutes."
    Write-Host ""

    $deadline = (Get-Date).AddMinutes($MaxMinutes)
    $nextSample = Get-Date
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        $now = Get-Date
        if ($now -ge $nextSample) {
            Write-ProcessMetric -Process $process -Target $metricsPath
            $nextSample = $now.AddSeconds(1)
        }
        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true).Key
                if ($key -eq [ConsoleKey]::R) {
                    Add-ReproductionMarker -MarkerPath $markerPath `
                        -Marker "problem_reproduced" -ProcessId $process.Id
                    Write-Host "Reproduction marker recorded." -ForegroundColor Yellow
                }
                elseif ($key -eq [ConsoleKey]::Q) {
                    Add-ReproductionMarker -MarkerPath $markerPath `
                        -Marker "collector_forced_stop" -ProcessId $process.Id
                    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                    break
                }
            }
        }
        catch {}
        Start-Sleep -Milliseconds 200
        $process.Refresh()
    }
    if (-not $process.HasExited) {
        Add-ReproductionMarker -MarkerPath $markerPath -Marker "collector_timeout" `
            -ProcessId $process.Id
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    try {
        $process.WaitForExit(5000) | Out-Null
    }
    catch {}
    Add-ReproductionMarker -MarkerPath $markerPath -Marker "application_exited" `
        -ProcessId $process.Id
}
catch {
    $collectorError = $_.Exception.Message
    Write-Warning "Collector encountered an error: $collectorError"
}
finally {
    foreach ($key in $diagnosticEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable(
            $key,
            $previousEnvironment[$key],
            "Process"
        )
    }
}

Start-Sleep -Seconds 1
Collect-ApplicationEvents -StartTime $startedAt -Target $eventPath
if ($collectorError) {
    "collector_error=$collectorError" |
        Set-Content -LiteralPath (Join-Path $sessionDir "collector_error.txt") -Encoding UTF8
}

$replacementValues = New-Object System.Collections.Generic.List[object]
foreach ($replacement in @(
    [pscustomobject]@{ Original = $env:USERPROFILE; Token = "<USERPROFILE>" },
    [pscustomobject]@{ Original = $env:LOCALAPPDATA; Token = "<LOCALAPPDATA>" },
    [pscustomobject]@{ Original = $env:APPDATA; Token = "<APPDATA>" },
    [pscustomobject]@{ Original = $env:TEMP; Token = "<TEMP>" },
    [pscustomobject]@{ Original = $repositoryRoot; Token = "<REPOSITORY>" },
    [pscustomobject]@{ Original = (Split-Path -Parent $launch.FilePath); Token = "<APP_DIR>" },
    [pscustomobject]@{ Original = $LibraryRootToRedact; Token = "<LIBRARY_ROOT>" }
)) {
    if ($replacement.Original) {
        $replacementValues.Add($replacement) | Out-Null
        $jsonEscaped = $replacement.Original.Replace('\', '\\')
        if ($jsonEscaped -ne $replacement.Original) {
            $replacementValues.Add([pscustomobject]@{
                Original = $jsonEscaped
                Token = $replacement.Token
            }) | Out-Null
        }
    }
}
Protect-TextArtifacts -Root $sessionDir -Replacements @($replacementValues)

$manifest = Get-ChildItem -LiteralPath $sessionDir -Recurse -File | ForEach-Object {
    [pscustomobject]@{
        file = $_.FullName.Substring($sessionDir.Length + 1).Replace("\", "/")
        bytes = $_.Length
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
}
$manifest | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $sessionDir "manifest.json") -Encoding UTF8

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $sessionDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host ""
Write-Host "Diagnostic bundle created:" -ForegroundColor Green
Write-Host $zipPath
Write-Host "Please review the ZIP if needed, then send that single file back."
