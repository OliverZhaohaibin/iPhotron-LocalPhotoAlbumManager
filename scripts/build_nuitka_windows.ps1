param(
    [string]$PythonExe = "",
    [string]$OutputDir = "build",
    [ValidateRange(1, 64)]
    [int]$Jobs = [Math]::Max(1, [Environment]::ProcessorCount),
    [string]$IconPath = "",
    [ValidateSet("disable", "attach", "force")]
    [string]$ConsoleMode = "disable",
    [switch]$RebuildNativeRuntime,
    [switch]$SkipNativeRuntimeSync
)

$ErrorActionPreference = "Stop"

function Assert-Exists {
    param([Parameter(Mandatory = $true)][string]$PathToCheck)
    if (-not (Test-Path $PathToCheck)) {
        throw "Required path does not exist: $PathToCheck"
    }
}

function Sync-NativeRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$DestinationDir
    )

    $requiredFiles = @(
        'osmand_render_helper.exe',
        'osmand_native_widget.dll',
        'OsmAndCore_shared.dll',
        'OsmAndCoreTools_shared.dll'
    )

    foreach ($fileName in $requiredFiles) {
        Assert-Exists (Join-Path $SourceDir $fileName)
    }

    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    foreach ($fileName in $requiredFiles) {
        Copy-Item -LiteralPath (Join-Path $SourceDir $fileName) -Destination $DestinationDir -Force
    }
    Get-ChildItem -Path $SourceDir -Filter '*.dll' -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $DestinationDir -Force
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$srcRoot = Join-Path $repoRoot 'src'
$mainScript = Join-Path $srcRoot 'iPhoto\gui\main.py'
$nativeBuildScript = Join-Path $repoRoot 'tools\osmand_render_helper_native\build_native_widget_msvc.ps1'
$nativeDistDir = Join-Path $repoRoot 'tools\osmand_render_helper_native\dist-msvc'
$extensionBinDir = Join-Path $srcRoot 'maps\tiles\extension\bin'

Assert-Exists $repoRoot
Assert-Exists $srcRoot
Assert-Exists $mainScript
Assert-Exists $nativeBuildScript

if (-not $PythonExe) {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    }
    else {
        $PythonExe = 'python'
    }
}

if (-not $IconPath) {
    $defaultIcon = Join-Path $repoRoot 'logo_new.ico'
    if (Test-Path $defaultIcon) {
        $IconPath = $defaultIcon
    }
}

if ($RebuildNativeRuntime) {
    & $nativeBuildScript -BuildType Release -Jobs $Jobs
    if ($LASTEXITCODE -ne 0) {
        throw "Native runtime rebuild failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipNativeRuntimeSync) {
    Assert-Exists $nativeDistDir
    Sync-NativeRuntime -SourceDir $nativeDistDir -DestinationDir $extensionBinDir
    Write-Host "Synced native map runtime into: $extensionBinDir"
}

$arguments = @(
    '-m', 'nuitka',
    '--standalone',
    "--jobs=$Jobs",
    '--msvc=latest',
    '--lto=yes',
    '--follow-imports',
    '--python-flag=no_site',
    '--enable-plugin=pyside6',
    '--include-qt-plugins=qml,multimedia',
    "--windows-console-mode=$ConsoleMode",
    '--assume-yes-for-downloads',
    '--nofollow-import-to=numba',
    '--nofollow-import-to=llvmlite',
    '--nofollow-import-to=iPhoto.tests',
    '--nofollow-import-to=pytest',
    '--include-package=iPhoto',
    '--include-package=maps',
    '--include-package=OpenGL',
    '--include-package=OpenGL_accelerate',
    '--include-package=cv2',
    '--include-package=reverse_geocoder',
    "--output-dir=$OutputDir",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\schemas')=iPhoto/schemas",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\gui\ui\icon')=iPhoto/gui/ui/icon",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\gui\ui\qml')=iPhoto/gui/ui/qml",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\gl_image_viewer.frag')=iPhoto/gui/ui/widgets/gl_image_viewer.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\gl_image_viewer.vert')=iPhoto/gui/ui/widgets/gl_image_viewer.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.frag')=iPhoto/gui/ui/widgets/video_renderer.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.frag.qsb')=iPhoto/gui/ui/widgets/video_renderer.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.vert')=iPhoto/gui/ui/widgets/video_renderer.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.vert.qsb')=iPhoto/gui/ui/widgets/video_renderer.vert.qsb",
    "--include-data-dir=$(Join-Path $srcRoot 'maps\tiles')=maps/tiles",
    "--include-data-file=$(Join-Path $srcRoot 'maps\style.json')=maps/style.json",
    "--include-data-dir=$(Join-Path $srcRoot 'maps\map_widget\qml')=maps/map_widget/qml"
)

if ($IconPath) {
    Assert-Exists $IconPath
    $arguments += "--windows-icon-from-ico=$IconPath"
}

$arguments += $mainScript

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE"
}