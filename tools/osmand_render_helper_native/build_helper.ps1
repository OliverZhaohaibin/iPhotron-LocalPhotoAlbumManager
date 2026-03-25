param(
    [string]$QtRoot = "C:\Qt\6.10.1\mingw_64",
    [string]$MinGWRoot = "C:\Qt\Tools\mingw1310_64",
    [string]$OsmAndCoreSource = "D:\python_code\maps_of_iPhoto\OsmAnd-core",
    [string]$OsmAndCoreLegacySource = "D:\python_code\maps_of_iPhoto\OsmAnd-core-legacy",
    [string]$BuildType = "Release",
    [string]$CMakeExe = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
    [string]$Generator = "MinGW Makefiles"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$vendorRoot = Join-Path $projectRoot "vendor\osmand"
$buildRoot = Join-Path $projectRoot "build"

New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

$coreLink = Join-Path $vendorRoot "core"
$legacyLink = Join-Path $vendorRoot "core-legacy"

if (-not (Test-Path $coreLink)) {
    New-Item -ItemType Junction -Path $coreLink -Target $OsmAndCoreSource | Out-Null
}
if (-not (Test-Path $legacyLink)) {
    New-Item -ItemType Junction -Path $legacyLink -Target $OsmAndCoreLegacySource | Out-Null
}

$env:PATH = "$($QtRoot)\bin;$($MinGWRoot)\bin;$env:PATH"

if (-not (Test-Path $CMakeExe)) {
    throw "cmake.exe was not found at $CMakeExe"
}

& $CMakeExe `
    -S $projectRoot `
    -B $buildRoot `
    -G $Generator `
    "-DCMAKE_BUILD_TYPE=$BuildType" `
    "-DCMAKE_PREFIX_PATH=$QtRoot" `
    "-DCMAKE_C_COMPILER=$MinGWRoot\bin\gcc.exe" `
    "-DCMAKE_CXX_COMPILER=$MinGWRoot\bin\g++.exe" `
    "-DQT_ROOT=$QtRoot" `
    "-DMINGW_ROOT=$MinGWRoot" `
    "-DOSMAND_VENDOR_ROOT=$vendorRoot"
if ($LASTEXITCODE -ne 0) {
    throw "CMake configure failed with exit code $LASTEXITCODE"
}

& $CMakeExe --build $buildRoot --config $BuildType --target osmand_render_helper osmand_native_widget
if ($LASTEXITCODE -ne 0) {
    throw "CMake build failed with exit code $LASTEXITCODE"
}

Write-Host "Helper built at: $(Join-Path $projectRoot 'dist\osmand_render_helper.exe')"
if (Test-Path (Join-Path $projectRoot 'dist\osmand_native_widget.dll')) {
    Write-Host "Native widget built at: $(Join-Path $projectRoot 'dist\osmand_native_widget.dll')"
}
