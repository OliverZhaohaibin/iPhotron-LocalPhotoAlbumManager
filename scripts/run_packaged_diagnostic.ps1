param(
    [string]$Executable = (Join-Path (Split-Path -Parent $PSScriptRoot) 'build\entrypoint.dist\entrypoint.exe'),
    [string]$OutputDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'build\packaged-diagnostics')
)

$ErrorActionPreference = 'Stop'
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$logDirectory = Join-Path $OutputDir (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logDirectory = (Resolve-Path -LiteralPath $logDirectory).Path
$stdoutLog = Join-Path $logDirectory 'stdout.log'
$stderrLog = Join-Path $logDirectory 'stderr.log'
$previousLogDirectory = $env:IPHOTO_LOG_DIR
$previousFaultHandler = $env:PYTHONFAULTHANDLER
try {
    $env:IPHOTO_LOG_DIR = $logDirectory
    $env:PYTHONFAULTHANDLER = '1'
    Write-Host "Diagnostic logs: $logDirectory"
    $process = Start-Process -FilePath $executablePath `
        -WorkingDirectory (Split-Path -Parent $executablePath) `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog `
        -PassThru -Wait
    $exitCode = $process.ExitCode
    "Exit code: $exitCode" | Set-Content -LiteralPath (Join-Path $logDirectory 'exit-code.txt')
    Write-Host "Exit code: $exitCode"
    $stderrFile = Get-Item -LiteralPath $stderrLog -ErrorAction SilentlyContinue
    if ($stderrFile -and $stderrFile.Length -gt 0) {
        Get-Content -LiteralPath $stderrLog -Tail 80
    } else {
        Write-Host 'No stderr output was captured. Check iPhoto.log if present, stdout.log, and exit-code.txt for errors.'
    }
} finally {
    $env:IPHOTO_LOG_DIR = $previousLogDirectory
    $env:PYTHONFAULTHANDLER = $previousFaultHandler
}
