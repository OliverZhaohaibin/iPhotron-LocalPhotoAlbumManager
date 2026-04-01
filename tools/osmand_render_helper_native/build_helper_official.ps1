param(
    [string]$OsmAndWorkspaceRoot = "",
    [string]$PythonVenv = "",
    [string]$QtRoot = "C:\Qt\6.10.1\mingw_64",
    [string]$MinGWRoot = "C:\Qt\Tools\mingw1310_64",
    [string]$CMakeExe = "C:\Qt\Tools\CMake_64\bin\cmake.exe",
    [string]$GitBashExe = "C:\Program Files\Git\bin\bash.exe",
    [string]$WorkspaceDrive = "O:",
    [ValidateSet("Debug", "Release", "RelWithDebInfo", "MinSizeRel")]
    [string]$BuildType = "Release",
    [ValidateRange(1, 64)]
    [int]$Jobs = 8,
    [switch]$SkipLegacyProtobuf,
    [switch]$ConfigureOnly,
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"

if (-not $OsmAndWorkspaceRoot) {
    throw "OsmAndWorkspaceRoot is required. Pass -OsmAndWorkspaceRoot <path-to-osmand-workspace>."
}
if (-not $PythonVenv) {
    throw "PythonVenv is required. Pass -PythonVenv <path-to-python-venv>."
}

if ($ConfigureOnly -and $BuildOnly) {
    throw "ConfigureOnly and BuildOnly can not be used together."
}

function Ensure-Junction {
    param(
        [Parameter(Mandatory = $true)][string]$LinkPath,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )

    if (Test-Path $LinkPath) {
        $item = Get-Item $LinkPath -Force
        if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Path already exists and is not a junction: $LinkPath"
        }
        return
    }

    New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null
}

function Ensure-SubstDrive {
    param(
        [Parameter(Mandatory = $true)][string]$DriveName,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )

    $normalizedDrive = $DriveName.TrimEnd(":").ToUpperInvariant() + ":"
    $escapedDrive = [regex]::Escape($normalizedDrive)
    $currentMappings = & cmd /c subst
    foreach ($line in $currentMappings) {
        if ($line -match "^${escapedDrive}\\: => (.+)$") {
            if ($Matches[1] -ieq $TargetPath) {
                return
            }
            throw "$normalizedDrive is already mapped to $($Matches[1])"
        }
    }

    & cmd /c "subst $normalizedDrive `"$TargetPath`""
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $normalizedDrive for $TargetPath"
    }
}

function Assert-Exists {
    param([Parameter(Mandatory = $true)][string]$PathToCheck)
    if (-not (Test-Path $PathToCheck)) {
        throw "Required path does not exist: $PathToCheck"
    }
}

function Copy-IfPresent {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationDir
    )

    if (-not (Test-Path $SourcePath)) {
        return
    }

    Copy-Item -Path $SourcePath -Destination $DestinationDir -Force
}

function To-CMakePath {
    param([Parameter(Mandatory = $true)][string]$Value)
    return ($Value -replace '\\', '/')
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$officialCppToolsRoot = Join-Path $projectRoot "official_cpp_tools"
$qt5CoreShim = Join-Path $projectRoot "cmake\Qt5Core"
$qt5NetworkShim = Join-Path $projectRoot "cmake\Qt5Network"
$toolchainFile = Join-Path $projectRoot "toolchains\amd64-windows-gcc.cmake"

$workspaceRoot = [IO.Path]::GetFullPath($OsmAndWorkspaceRoot)
$workspaceDriveRoot = "$($WorkspaceDrive.TrimEnd(':')):\"
$shortBuildRoot = "${workspaceDriveRoot}build"
$shortBuildDir = "${workspaceDriveRoot}baked\amd64-windows-gcc.qt6"
$physicalBuildDir = Join-Path $workspaceRoot "baked\amd64-windows-gcc.qt6"
$helperOutputDir = Join-Path $workspaceRoot "binaries\windows\gcc-amd64\$BuildType"
$helperOutputPath = Join-Path $helperOutputDir "osmand_render_helper.exe"
$nativeWidgetOutputPath = Join-Path $helperOutputDir "osmand_native_widget.dll"
$localDistDir = Join-Path $projectRoot "dist"

Assert-Exists $workspaceRoot
Assert-Exists (Join-Path $workspaceRoot "build")
Assert-Exists (Join-Path $workspaceRoot "OsmAnd-core")
Assert-Exists (Join-Path $workspaceRoot "OsmAnd-core-legacy")
Assert-Exists (Join-Path $workspaceRoot "OsmAnd-resources")
Assert-Exists $projectRoot
Assert-Exists $officialCppToolsRoot
Assert-Exists $qt5CoreShim
Assert-Exists $qt5NetworkShim
Assert-Exists $toolchainFile
Assert-Exists $PythonVenv
Assert-Exists $QtRoot
Assert-Exists $MinGWRoot
Assert-Exists $CMakeExe
Assert-Exists $GitBashExe

$projectRootCMake = To-CMakePath $projectRoot
$qtRootCMake = To-CMakePath $QtRoot
$mingwRootCMake = To-CMakePath $MinGWRoot
$toolchainFileCMake = To-CMakePath $toolchainFile
$qt5CoreShimCMake = To-CMakePath $qt5CoreShim
$qt5NetworkShimCMake = To-CMakePath $qt5NetworkShim

New-Item -ItemType Directory -Force -Path (Join-Path $workspaceRoot "tools") | Out-Null

Ensure-Junction -LinkPath (Join-Path $workspaceRoot "core") -TargetPath (Join-Path $workspaceRoot "OsmAnd-core")
Ensure-Junction -LinkPath (Join-Path $workspaceRoot "core-legacy") -TargetPath (Join-Path $workspaceRoot "OsmAnd-core-legacy")
Ensure-Junction -LinkPath (Join-Path $workspaceRoot "resources") -TargetPath (Join-Path $workspaceRoot "OsmAnd-resources")
Ensure-Junction -LinkPath (Join-Path $workspaceRoot "tools\cpp-tools") -TargetPath $officialCppToolsRoot

$boostBuildBat = Join-Path $workspaceRoot "core\externals\boost\build.bat"
if (-not (Test-Path $boostBuildBat)) {
    @"
@echo off
bash --login "%~dp0build.sh" %*
"@ | Set-Content -Path $boostBuildBat -Encoding ASCII
}

& git config --global core.longpaths true

Ensure-SubstDrive -DriveName $WorkspaceDrive -TargetPath $workspaceRoot

$env:PATH = @(
    (Join-Path $PythonVenv "Scripts"),
    (Join-Path $MinGWRoot "bin"),
    "C:\Program Files\Git\bin",
    "C:\Program Files\Git\usr\bin",
    "C:\Program Files\Git\mingw64\bin",
    $env:PATH
) -join ";"

if (-not $SkipLegacyProtobuf) {
    $legacyProtobufScript = Join-Path $workspaceRoot "core-legacy\externals\protobuf\configure.sh"
    Assert-Exists $legacyProtobufScript
    & $GitBashExe -lc "export PATH=/d/python_code/iPhoto/.venv/Scripts:/c/Qt/Tools/mingw1310_64/bin:`$PATH; cd /o/core-legacy/externals/protobuf && ./configure.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to prepare legacy protobuf."
    }
}

$env:OSMAND_SYSTEM_QT = "1"

if (-not $BuildOnly) {
    if (Test-Path $physicalBuildDir) {
        Remove-Item -Recurse -Force $physicalBuildDir
    }

    & $CMakeExe `
        -S $shortBuildRoot `
        -B $shortBuildDir `
        -G "MinGW Makefiles" `
        "-DCMAKE_BUILD_TYPE=$BuildType" `
        "-DCMAKE_MAKE_PROGRAM=$mingwRootCMake/bin/mingw32-make.exe" `
        "-DCMAKE_PREFIX_PATH=$qtRootCMake" `
        "-DCMAKE_TOOLCHAIN_FILE=$toolchainFileCMake" `
        "-DOSMAND_MINGW_ROOT=$mingwRootCMake" `
        "-DOSMAND_RENDER_HELPER_SOURCE_ROOT=$projectRootCMake" `
        "-DQt5Core_DIR=$qt5CoreShimCMake" `
        "-DQt5Network_DIR=$qt5NetworkShimCMake" `
        "-Dws2_32_LIBRARY=ws2_32" `
        "-Dgdi32_LIBRARY=gdi32" `
        "-Ddwrite_LIBRARY=dwrite"
    if ($LASTEXITCODE -ne 0) {
        throw "Official OsmAnd CMake configure failed with exit code $LASTEXITCODE"
    }
}
elseif (-not (Test-Path $physicalBuildDir)) {
    throw "Build directory does not exist yet: $physicalBuildDir"
}

if (-not $ConfigureOnly) {
    & $CMakeExe --build $shortBuildDir --target osmand_render_helper osmand_native_widget --config $BuildType --parallel $Jobs
    if ($LASTEXITCODE -ne 0) {
        throw "Official OsmAnd helper build failed with exit code $LASTEXITCODE"
    }

    Assert-Exists $helperOutputPath
    Copy-IfPresent -SourcePath (Join-Path $QtRoot "bin\\Qt6Core.dll") -DestinationDir $helperOutputDir
    Copy-IfPresent -SourcePath (Join-Path $QtRoot "bin\\Qt6Network.dll") -DestinationDir $helperOutputDir
    Copy-IfPresent -SourcePath (Join-Path $MinGWRoot "bin\\libgcc_s_seh-1.dll") -DestinationDir $helperOutputDir
    Copy-IfPresent -SourcePath (Join-Path $MinGWRoot "bin\\libstdc++-6.dll") -DestinationDir $helperOutputDir
    Copy-IfPresent -SourcePath (Join-Path $MinGWRoot "bin\\libwinpthread-1.dll") -DestinationDir $helperOutputDir

    New-Item -ItemType Directory -Force -Path $localDistDir | Out-Null
    Copy-Item -Path (Join-Path $helperOutputDir "osmand_render_helper.exe") -Destination $localDistDir -Force
    if (Test-Path $nativeWidgetOutputPath) {
        Copy-Item -Path $nativeWidgetOutputPath -Destination $localDistDir -Force
    }
    Get-ChildItem -Path $helperOutputDir -File -Filter *.dll | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $localDistDir -Force
    }
    if (-not (Test-Path (Join-Path $localDistDir "osmand_native_widget.dll")) -and
        -not (Test-Path (Join-Path $localDistDir "libosmand_native_widget.dll"))) {
        throw "Native widget DLL was not produced."
    }
}

Write-Host "Official helper built at: $helperOutputPath"
if (-not $ConfigureOnly) {
    Write-Host "Helper runtime mirrored to: $localDistDir"
}




