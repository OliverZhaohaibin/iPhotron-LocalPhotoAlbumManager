param(
    [string]$OsmAndWorkspaceRoot = "D:\python_code\maps_of_iPhoto",
    [string]$PythonVenv = "D:\python_code\iPhoto\.venv",
    [string]$PySide6Root = "",
    [string]$QtHeadersRoot = "",
    [string]$VcVarsBat = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    [string]$CMakeExe = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
    [string]$GitBashExe = "C:\Program Files\Git\bin\bash.exe",
    [string]$WorkspaceDrive = "O:",
    [ValidateSet("Debug", "Release", "RelWithDebInfo", "MinSizeRel")]
    [string]$BuildType = "Release",
    [ValidateRange(1, 64)]
    [int]$Jobs = [Math]::Max(1, [Environment]::ProcessorCount),
    [switch]$SkipLegacyProtobuf,
    [switch]$ConfigureOnly,
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"

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

function To-CMakePath {
    param([Parameter(Mandatory = $true)][string]$Value)
    return ($Value -replace '\\', '/')
}

function Resolve-QtHeadersRoot {
    param([string]$RequestedRoot)

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($RequestedRoot) {
        $candidates.Add($RequestedRoot) | Out-Null
    }
    if ($env:QT6_HEADERS_ROOT) {
        $candidates.Add($env:QT6_HEADERS_ROOT) | Out-Null
    }
    if ($env:QTDIR) {
        $candidates.Add((Join-Path $env:QTDIR 'include')) | Out-Null
    }
    $candidates.Add('C:\Qt\6.10.1\mingw_64\include') | Out-Null

    if (Test-Path 'C:\Qt') {
        Get-ChildItem -Path 'C:\Qt' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $includePath = Join-Path $_.FullName 'mingw_64\include'
                $candidates.Add($includePath) | Out-Null
            }
    }

    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        if (Test-Path (Join-Path $candidate 'QtCore\QtGlobal')) {
            return (Get-Item $candidate).FullName
        }
    }

    throw "Unable to locate Qt headers root. Pass -QtHeadersRoot with a Qt SDK include directory such as C:\Qt\6.10.1\mingw_64\include."
}

function Import-VcVarsEnvironment {
    param([Parameter(Mandatory = $true)][string]$BatchFile)

    Assert-Exists $BatchFile
    $envDump = & cmd /s /c "call `"$BatchFile`" >nul && set"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to import Visual Studio environment from $BatchFile"
    }

    foreach ($line in $envDump) {
        if ($line -notmatch '^([^=]+)=(.*)$') {
            continue
        }
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

function Repair-SkiaMsvcCMake {
    param([Parameter(Mandatory = $true)][string]$SkiaCMakePath)

    Assert-Exists $SkiaCMakePath
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = [System.IO.File]::ReadAllText($SkiaCMakePath)
    $repaired = $content -replace 'COMPILE_DEFINITIONS\s+S\s+K_CPU_SSE_LEVEL=SK_CPU_SSE_LEVEL_SSSE3', "COMPILE_DEFINITIONS`r`n                    SK_CPU_SSE_LEVEL=SK_CPU_SSE_LEVEL_SSSE3"
    if ($repaired -ne $content) {
        [System.IO.File]::WriteAllText($SkiaCMakePath, $repaired, $utf8NoBom)
    }
}

function Repair-SkiaWindowsSdkCompatibility {
    param(
        [Parameter(Mandatory = $true)][string]$DWriteVersionHeaderPath,
        [Parameter(Mandatory = $true)][string]$WicSourcePath,
        [Parameter(Mandatory = $true)][string]$ScalerContextSourcePath
    )

    Assert-Exists $DWriteVersionHeaderPath
    Assert-Exists $WicSourcePath
    Assert-Exists $ScalerContextSourcePath

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $dwriteHeader = @"
/*
 * Copyright 2018 Google Inc.
 *
 * Use of this source code is governed by a BSD-style license that can be
 * found in the LICENSE file.
 */

#ifndef SkDWriteNTDDI_VERSION_DEFINED
#define SkDWriteNTDDI_VERSION_DEFINED

// More strictly, this header should be the first thing in a translation unit,
// since it adjusts the Windows SDK feature level before DWrite headers load.
#if defined(_WINDOWS_) || defined(DWRITE_3_H_INCLUDED)
#error Must include SkDWriteNTDDI_VERSION.h before any Windows or DWrite headers.
#endif

#include <sdkddkver.h>

#ifdef NTDDI_VERSION
#  undef NTDDI_VERSION
#endif
#ifdef _WIN32_WINNT
#  undef _WIN32_WINNT
#endif
#ifdef WINVER
#  undef WINVER
#endif

// Skia's DirectWrite backend uses IDWriteFontFace4/5, which are gated behind
// Windows 10 SDK feature levels. Pick the minimum level that exposes them.
#define NTDDI_VERSION NTDDI_WIN10_RS3
#define _WIN32_WINNT _WIN32_WINNT_WIN10
#define WINVER _WIN32_WINNT_WIN10

#endif
"@
    [System.IO.File]::WriteAllText($DWriteVersionHeaderPath, $dwriteHeader, $utf8NoBom)

    $wicContent = [System.IO.File]::ReadAllText($WicSourcePath)
    $wicGuidBlocks = @(
        @{
            Symbol = 'GUID_WICPixelFormat32bppRGB'
            Block = "#ifndef GUID_WICPixelFormat32bppRGB`r`nDEFINE_GUID(GUID_WICPixelFormat32bppRGB, 0xd98c6b95, 0x3efe, 0x47d6, 0xbb, 0x25, 0xeb, 0x17, 0x48, 0xab, 0x0c, 0xf1);`r`n#endif"
        },
        @{
            Symbol = 'GUID_WICPixelFormat64bppRGB'
            Block = "#ifndef GUID_WICPixelFormat64bppRGB`r`nDEFINE_GUID(GUID_WICPixelFormat64bppRGB, 0xa1182111, 0x186d, 0x4d42, 0xbc, 0x6a, 0x9c, 0x83, 0x03, 0xa8, 0xdf, 0xf9);`r`n#endif"
        },
        @{
            Symbol = 'GUID_WICPixelFormat96bppRGBFloat'
            Block = "#ifndef GUID_WICPixelFormat96bppRGBFloat`r`nDEFINE_GUID(GUID_WICPixelFormat96bppRGBFloat, 0xe3fed78f, 0xe8db, 0x4acf, 0x84, 0xc1, 0xe9, 0x7f, 0x61, 0x36, 0xb3, 0x27);`r`n#endif"
        }
    )
    foreach ($guidBlock in $wicGuidBlocks) {
        if ($wicContent -notmatch [regex]::Escape($guidBlock.Symbol)) {
            $wicContent = $wicContent.Replace('#include <wincodec.h>', "#include <wincodec.h>`r`n`r`n$($guidBlock.Block)")
        }
    }
    [System.IO.File]::WriteAllText($WicSourcePath, $wicContent, $utf8NoBom)

    $scalerContent = [System.IO.File]::ReadAllText($ScalerContextSourcePath)
    $repairedScalerContent = $scalerContent.Replace('GetGlyphImageFormats_(', 'GetGlyphImageFormats(')
    if ($repairedScalerContent -ne $scalerContent) {
        [System.IO.File]::WriteAllText($ScalerContextSourcePath, $repairedScalerContent, $utf8NoBom)
    }
}

function Repair-IcuMsvcCompatibility {
    param(
        [Parameter(Mandatory = $true)][string]$HeaderPath,
        [Parameter(Mandatory = $true)][string]$SourcePath
    )

    Assert-Exists $HeaderPath
    Assert-Exists $SourcePath

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    $headerContent = [System.IO.File]::ReadAllText($HeaderPath)
    $repairedHeaderContent = $headerContent.Replace('wchar_t *format', 'const wchar_t *format')
    if ($repairedHeaderContent -ne $headerContent) {
        [System.IO.File]::WriteAllText($HeaderPath, $repairedHeaderContent, $utf8NoBom)
    }

    $sourceContent = [System.IO.File]::ReadAllText($SourcePath)
    $repairedSourceContent = $sourceContent.Replace('wchar_t *fmt', 'const wchar_t *fmt')
    if ($repairedSourceContent -ne $sourceContent) {
        [System.IO.File]::WriteAllText($SourcePath, $repairedSourceContent, $utf8NoBom)
    }
}
function New-QtImportLibrary {
    param(
        [Parameter(Mandatory = $true)][string]$DllPath,
        [Parameter(Mandatory = $true)][string]$DefPath,
        [Parameter(Mandatory = $true)][string]$LibPath
    )

    $dllItem = Get-Item $DllPath
    if ((Test-Path $LibPath) -and ((Get-Item $LibPath).LastWriteTimeUtc -ge $dllItem.LastWriteTimeUtc)) {
        return
    }

    $dumpbinOutput = & dumpbin /exports $DllPath
    if ($LASTEXITCODE -ne 0) {
        throw "dumpbin /exports failed for $DllPath"
    }

    $exports = New-Object System.Collections.Generic.List[string]
    $inExports = $false
    foreach ($line in $dumpbinOutput) {
        if ($line -match '^\s*ordinal\s+hint\s+RVA\s+name\s*$') {
            $inExports = $true
            continue
        }
        if (-not $inExports) {
            continue
        }
        if ($line -match '^\s*Summary\s*$') {
            break
        }
        if ($line -match '^\s*\d+\s+[0-9A-F]+\s+[0-9A-F]+\s+(.+?)\s*$') {
            $exports.Add($Matches[1]) | Out-Null
        }
    }

    if ($exports.Count -eq 0) {
        throw "No exports were parsed from $DllPath"
    }

    $defContent = @(
        "LIBRARY $([IO.Path]::GetFileName($DllPath))"
        'EXPORTS'
    ) + ($exports | ForEach-Object { "    $_" })

    Set-Content -Path $DefPath -Value $defContent -Encoding ASCII
    & lib /nologo /machine:x64 "/def:$DefPath" "/out:$LibPath"
    if ($LASTEXITCODE -ne 0) {
        throw "lib.exe failed while creating $LibPath"
    }
}

function Copy-DirectoryDlls {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$DestinationDir
    )

    Get-ChildItem -Path $SourceDir -File -Filter *.dll | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $DestinationDir -Force
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $PySide6Root) {
    $PySide6Root = Join-Path $PythonVenv 'Lib\site-packages\PySide6'
}
$QtHeadersRoot = Resolve-QtHeadersRoot -RequestedRoot $QtHeadersRoot

$officialCppToolsRoot = Join-Path $projectRoot 'official_cpp_tools'
$qt5CoreShim = Join-Path $projectRoot 'cmake\Qt5Core'
$qt5NetworkShim = Join-Path $projectRoot 'cmake\Qt5Network'
$cmakeShimRoot = Join-Path $projectRoot 'cmake'
$qtImportLibRoot = Join-Path $projectRoot 'qt6-msvc-importlibs'
$qtImportDefRoot = Join-Path $qtImportLibRoot 'defs'

$workspaceRoot = [IO.Path]::GetFullPath($OsmAndWorkspaceRoot)
$workspaceDriveRoot = "$($WorkspaceDrive.TrimEnd(':')):\"
$shortBuildRoot = "${workspaceDriveRoot}build"
$shortBuildDir = "${workspaceDriveRoot}baked\amd64-windows-msvc.qt6.msvs"
$physicalBuildDir = Join-Path $workspaceRoot 'baked\amd64-windows-msvc.qt6.msvs'
$msvcOutputRoot = Join-Path $workspaceRoot 'binaries\windows\msvc-amd64\amd64'
$localDistDir = Join-Path $projectRoot 'dist-msvc'

Assert-Exists $workspaceRoot
Assert-Exists (Join-Path $workspaceRoot 'build')
Assert-Exists (Join-Path $workspaceRoot 'OsmAnd-core')
Assert-Exists (Join-Path $workspaceRoot 'OsmAnd-core-legacy')
Assert-Exists (Join-Path $workspaceRoot 'OsmAnd-resources')
Assert-Exists $projectRoot
Assert-Exists $officialCppToolsRoot
Assert-Exists $qt5CoreShim
Assert-Exists $qt5NetworkShim
Assert-Exists $cmakeShimRoot
Assert-Exists $PythonVenv
Assert-Exists $PySide6Root
Assert-Exists $QtHeadersRoot
Assert-Exists $VcVarsBat
Assert-Exists $CMakeExe
Assert-Exists $GitBashExe

$projectRootCMake = To-CMakePath $projectRoot
$pyside6RootCMake = To-CMakePath $PySide6Root
$qtHeadersRootCMake = To-CMakePath $QtHeadersRoot
$qtImportLibRootCMake = To-CMakePath $qtImportLibRoot
$cmakeShimRootCMake = To-CMakePath $cmakeShimRoot
$qt5CoreShimCMake = To-CMakePath $qt5CoreShim
$qt5NetworkShimCMake = To-CMakePath $qt5NetworkShim

New-Item -ItemType Directory -Force -Path (Join-Path $workspaceRoot 'tools') | Out-Null
New-Item -ItemType Directory -Force -Path $qtImportLibRoot | Out-Null
New-Item -ItemType Directory -Force -Path $qtImportDefRoot | Out-Null

Ensure-Junction -LinkPath (Join-Path $workspaceRoot 'core') -TargetPath (Join-Path $workspaceRoot 'OsmAnd-core')
Ensure-Junction -LinkPath (Join-Path $workspaceRoot 'core-legacy') -TargetPath (Join-Path $workspaceRoot 'OsmAnd-core-legacy')
Ensure-Junction -LinkPath (Join-Path $workspaceRoot 'resources') -TargetPath (Join-Path $workspaceRoot 'OsmAnd-resources')
Ensure-Junction -LinkPath (Join-Path $workspaceRoot 'tools\cpp-tools') -TargetPath $officialCppToolsRoot
Repair-SkiaMsvcCMake -SkiaCMakePath (Join-Path $workspaceRoot 'OsmAnd-core\externals\skia\CMakeLists.txt')
Repair-SkiaWindowsSdkCompatibility `
    -DWriteVersionHeaderPath (Join-Path $workspaceRoot 'OsmAnd-core\externals\skia\upstream.patched\src\utils\win\SkDWriteNTDDI_VERSION.h') `
    -WicSourcePath (Join-Path $workspaceRoot 'OsmAnd-core\externals\skia\upstream.patched\src\ports\SkImageGeneratorWIC.cpp') `
    -ScalerContextSourcePath (Join-Path $workspaceRoot 'OsmAnd-core\externals\skia\upstream.patched\src\ports\SkScalerContext_win_dw.cpp')

$boostBuildBat = Join-Path $workspaceRoot 'core\externals\boost\build.bat'
if (-not (Test-Path $boostBuildBat)) {
    @"
@echo off
bash --login "%~dp0build.sh" %*
"@ | Set-Content -Path $boostBuildBat -Encoding ASCII
}

& git config --global core.longpaths true

Ensure-SubstDrive -DriveName $WorkspaceDrive -TargetPath $workspaceRoot
Import-VcVarsEnvironment -BatchFile $VcVarsBat

$env:PATH = @(
    (Join-Path $PythonVenv 'Scripts'),
    'C:\Program Files\Git\bin',
    'C:\Program Files\Git\usr\bin',
    'C:\Program Files\Git\mingw64\bin',
    $env:PATH
) -join ';'
$env:CMAKE_BUILD_PARALLEL_LEVEL = [string]$Jobs

if (-not $SkipLegacyProtobuf) {
    $legacyProtobufScript = Join-Path $workspaceRoot 'core-legacy\externals\protobuf\configure.sh'
    Assert-Exists $legacyProtobufScript
    & $GitBashExe -lc "export PATH=/d/python_code/iPhoto/.venv/Scripts:`$PATH; cd /o/core-legacy/externals/protobuf && ./configure.sh"
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to prepare legacy protobuf.'
    }
}

$qtModules = @(
    'Qt6Core',
    'Qt6Network',
    'Qt6Gui',
    'Qt6Widgets',
    'Qt6OpenGL',
    'Qt6OpenGLWidgets'
)
foreach ($qtModule in $qtModules) {
    $dllPath = Join-Path $PySide6Root "$qtModule.dll"
    Assert-Exists $dllPath
    New-QtImportLibrary `
        -DllPath $dllPath `
        -DefPath (Join-Path $qtImportDefRoot "$qtModule.def") `
        -LibPath (Join-Path $qtImportLibRoot "$qtModule.lib")
}

$env:OSMAND_SYSTEM_QT = '1'
$env:PYSIDE6_ROOT = $PySide6Root
$env:QT6_HEADERS_ROOT = $QtHeadersRoot
$env:QT6_IMPORT_LIB_ROOT = $qtImportLibRoot

if (-not $BuildOnly) {
    if (Test-Path $physicalBuildDir) {
        Remove-Item -Recurse -Force $physicalBuildDir
    }

    & $CMakeExe `
        -S $shortBuildRoot `
        -B $shortBuildDir `
        -G 'Visual Studio 17 2022' `
        -A x64 `
        -T host=x64 `
        '-DCMAKE_TARGET_BUILD_TOOL=msvs' `
        '-DOSMAND_TARGET=amd64-windows-msvc' `
        '-DCMAKE_C_FLAGS=/FS' `
        '-DCMAKE_CXX_FLAGS=/Zc:__cplusplus /permissive- /EHsc /FS' `
        "-DCMAKE_PREFIX_PATH=$cmakeShimRootCMake" `
        "-DOSMAND_RENDER_HELPER_SOURCE_ROOT=$projectRootCMake" `
        "-DQt5Core_DIR=$qt5CoreShimCMake" `
        "-DQt5Network_DIR=$qt5NetworkShimCMake" `
        "-DPYSIDE6_ROOT=$pyside6RootCMake" `
        "-DQT6_HEADERS_ROOT=$qtHeadersRootCMake" `
        "-DQT6_IMPORT_LIB_ROOT=$qtImportLibRootCMake" `
        '-Dws2_32_LIBRARY=ws2_32' `
        '-Dgdi32_LIBRARY=gdi32' `
        '-Ddwrite_LIBRARY=dwrite'
    if ($LASTEXITCODE -ne 0) {
        throw "Official OsmAnd MSVC configure failed with exit code $LASTEXITCODE"
    }
}
elseif (-not (Test-Path $physicalBuildDir)) {
    throw "Build directory does not exist yet: $physicalBuildDir"
}

if (-not $ConfigureOnly) {
    & $CMakeExe --build $shortBuildDir --target osmand_render_helper osmand_native_widget --config $BuildType --parallel $Jobs -- /m:$Jobs /nodeReuse:false /p:UseMultiToolTask=true /p:CL_MPCount=$Jobs /p:BuildInParallel=true
    if ($LASTEXITCODE -ne 0) {
        throw "Official OsmAnd helper/native widget build failed with exit code $LASTEXITCODE"
    }

    $widgetCandidates = Get-ChildItem -Path (Join-Path $workspaceRoot 'binaries\windows\msvc-amd64') -Recurse -Filter osmand_native_widget.dll -File |
        Sort-Object FullName
    if (-not $widgetCandidates) {
        throw "Native widget DLL was not produced under $msvcOutputRoot"
    }

    $widgetOutput = $widgetCandidates |
        Where-Object { $_.FullName -match [regex]::Escape("\\$BuildType\\") } |
        Select-Object -First 1
    if (-not $widgetOutput) {
        $widgetOutput = $widgetCandidates | Select-Object -First 1
    }

    $helperCandidates = Get-ChildItem -Path (Join-Path $workspaceRoot 'binaries\windows\msvc-amd64') -Recurse -Filter osmand_render_helper.exe -File |
        Sort-Object FullName
    if (-not $helperCandidates) {
        throw "Helper executable was not produced under $msvcOutputRoot"
    }

    $helperOutput = $helperCandidates |
        Where-Object { $_.FullName -match [regex]::Escape("\\$BuildType\\") } |
        Select-Object -First 1
    if (-not $helperOutput) {
        $helperOutput = $helperCandidates | Select-Object -First 1
    }

    $binaryOutputDir = $widgetOutput.Directory.FullName
    if ($helperOutput.Directory.FullName -ne $binaryOutputDir) {
        throw "Helper and native widget were produced in different directories: $($helperOutput.Directory.FullName) vs $binaryOutputDir"
    }
    New-Item -ItemType Directory -Force -Path $localDistDir | Out-Null
    Copy-Item -Path $helperOutput.FullName -Destination $localDistDir -Force
    Copy-DirectoryDlls -SourceDir $binaryOutputDir -DestinationDir $localDistDir

    $widgetDistPath = Join-Path $localDistDir 'osmand_native_widget.dll'
    Assert-Exists $widgetDistPath
    $helperDistPath = Join-Path $localDistDir 'osmand_render_helper.exe'
    Assert-Exists $helperDistPath
}

Write-Host "MSVC native widget output root: $msvcOutputRoot"
Write-Host "Using Qt headers root: $QtHeadersRoot"
Write-Host "Build parallelism: $Jobs"
if (-not $ConfigureOnly) {
    Write-Host "Native widget runtime mirrored to: $localDistDir"
    Write-Host "Helper runtime mirrored to: $localDistDir"
}

