param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$nativeDir = Join-Path $repoRoot "src\iPhoto\_native"
$buildDir = Join-Path $nativeDir "build\windows"
$sourcePath = Join-Path $nativeDir "scan_utils.c"
$outputPath = Join-Path $nativeDir "_scan_utils.dll"
$objectPath = Join-Path $buildDir "scan_utils.obj"
$importLibPath = Join-Path $buildDir "scan_utils.lib"
$pdbPath = Join-Path $buildDir "scan_utils.pdb"
$vcvarsPath = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"

if (-not (Test-Path $sourcePath)) {
    throw "Missing source file: $sourcePath"
}
if (-not (Test-Path $vcvarsPath)) {
    throw "Missing Visual Studio environment script: $vcvarsPath"
}

if ($Clean) {
    Remove-Item -Force -ErrorAction SilentlyContinue $outputPath, $objectPath, $importLibPath, $pdbPath
}

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$command = @(
    'call'
    ('"{0}"' -f $vcvarsPath)
    '>nul'
    '&&'
    'cl'
    '/nologo'
    '/O2'
    '/LD'
    '/utf-8'
    ('/I"{0}"' -f $nativeDir)
    ('/Fo"{0}"' -f $objectPath)
    ('/Fe"{0}"' -f $outputPath)
    ('"{0}"' -f $sourcePath)
    '/link'
    ('/IMPLIB:"{0}"' -f $importLibPath)
    ('/PDB:"{0}"' -f $pdbPath)
) -join ' '

cmd /c $command
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Built scan extension:"
Write-Host "  DLL: $outputPath"
Write-Host "  Import lib: $importLibPath"
Write-Host "  PDB: $pdbPath"
