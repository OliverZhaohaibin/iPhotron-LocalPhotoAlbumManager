param(
    [string]$PythonExe = "",
    [string]$OutputDir = "build",
    [ValidateRange(1, 64)]
    [int]$Jobs = [Math]::Max(1, [Environment]::ProcessorCount),
    [string]$IconPath = "",
    [ValidateSet("disable", "attach", "force")]
    [string]$ConsoleMode = "disable",
    [switch]$RebuildNativeRuntime,
    [switch]$SkipNativeRuntimeSync,
    [switch]$IncludeOptionalAssets
)

$ErrorActionPreference = "Stop"

function Assert-Exists {
    param([Parameter(Mandatory = $true)][string]$PathToCheck)
    if (-not (Test-Path $PathToCheck)) {
        throw "Required path does not exist: $PathToCheck"
    }
}

function Resolve-ExecutablePath {
    param([Parameter(Mandatory = $true)][string]$Executable)

    if (Test-Path -LiteralPath $Executable -PathType Leaf) {
        return (Get-Item -LiteralPath $Executable).FullName
    }

    $command = Get-Command $Executable -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }

    return $null
}

function Test-IsWindowsStorePythonAlias {
    param([Parameter(Mandatory = $true)][string]$Executable)

    return $Executable -match '(?i)[\\/]Microsoft[\\/]WindowsApps[\\/]python(?:3)?(?:\.exe)?$'
}

function Resolve-BuildPython {
    param(
        [string]$RequestedPython,
        [Parameter(Mandatory = $true)][string]$RepositoryRoot
    )

    $candidates = New-Object System.Collections.Generic.List[object]
    if ($RequestedPython) {
        $candidates.Add([pscustomobject]@{
            Label = '-PythonExe'
            Executable = $RequestedPython
            PrefixArgs = @()
        }) | Out-Null
    }
    else {
        $repositoryParent = Split-Path -Parent $RepositoryRoot
        $candidates.Add([pscustomobject]@{
            Label = 'repository virtual environment'
            Executable = (Join-Path $RepositoryRoot '.venv\Scripts\python.exe')
            PrefixArgs = @()
        }) | Out-Null
        $candidates.Add([pscustomobject]@{
            Label = 'parent virtual environment'
            Executable = (Join-Path $repositoryParent '.venv\Scripts\python.exe')
            PrefixArgs = @()
        }) | Out-Null
        $candidates.Add([pscustomobject]@{
            Label = 'Python launcher (3.12)'
            Executable = 'py.exe'
            PrefixArgs = @('-3.12')
        }) | Out-Null
        $candidates.Add([pscustomobject]@{
            Label = 'PATH python.exe'
            Executable = 'python.exe'
            PrefixArgs = @()
        }) | Out-Null
    }

    # Windows PowerShell 5.1 rebuilds the native command line before invoking
    # python.exe. Keep this probe on one line and use only Python single-quoted
    # strings so embedded double quotes cannot be stripped during that step.
    $probeScript = "import importlib.util, sys; v=sys.version.split()[0]; required=('nuitka','exiftool','pillow_heif','_pillow_heif'); missing=[name for name in required if importlib.util.find_spec(name) is None]; ok=sys.version_info >= (3, 12) and not missing; print(sys.executable); print(v); raise SystemExit(0 if ok else 'Python 3.12+ with Nuitka, PyExifTool, and pillow-heif installed is required; missing: '+','.join(missing))"
    $attempts = New-Object System.Collections.Generic.List[string]

    foreach ($candidate in $candidates) {
        $resolvedExecutable = Resolve-ExecutablePath -Executable $candidate.Executable
        if (-not $resolvedExecutable) {
            $attempts.Add("$($candidate.Label): executable not found") | Out-Null
            continue
        }
        if (Test-IsWindowsStorePythonAlias -Executable $resolvedExecutable) {
            $attempts.Add("$($candidate.Label): rejected Microsoft Store App Execution Alias $resolvedExecutable") | Out-Null
            continue
        }

        [string[]]$prefixArgs = $candidate.PrefixArgs
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            # A rejected candidate is expected to write to stderr. Do not let
            # Windows PowerShell convert that output into a terminating
            # NativeCommandError; capture it and continue to the next candidate.
            $ErrorActionPreference = 'Continue'
            $probeOutput = @(& $resolvedExecutable @prefixArgs -c $probeScript 2>&1)
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($probeExitCode -eq 0 -and $probeOutput.Count -ge 2) {
            return [pscustomobject]@{
                Executable = $resolvedExecutable
                PrefixArgs = $prefixArgs
                PythonPath = [string]$probeOutput[$probeOutput.Count - 2]
                Version = [string]$probeOutput[$probeOutput.Count - 1]
            }
        }

        $reason = ($probeOutput | ForEach-Object { $_.ToString() }) -join ' '
        if (-not $reason) {
            $reason = "probe exited with code $probeExitCode"
        }
        $attempts.Add("$($candidate.Label): $reason") | Out-Null
    }

    $attemptSummary = $attempts -join [Environment]::NewLine
    throw @"
Unable to locate a usable Python 3.12+ interpreter with Nuitka, PyExifTool, and pillow-heif installed.
$attemptSummary
Create a virtual environment and install Nuitka, or pass an explicit interpreter, for example:
  powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 -PythonExe "D:\python_code\iPhoto\.venv\Scripts\python.exe"
"@
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
Set-Location -LiteralPath $repoRoot
$srcRoot = Join-Path $repoRoot 'src'
$mainScript = Join-Path $srcRoot 'entrypoint.py'
$nativeBuildScript = Join-Path $repoRoot 'tools\osmand_render_helper_native\build_native_widget_msvc.ps1'
$nativeDistDir = Join-Path $repoRoot 'tools\osmand_render_helper_native\dist-msvc'
$extensionBinDir = Join-Path $srcRoot 'maps\tiles\extension\bin'
$faceModelDir = Join-Path $srcRoot 'extension\models'
$defaultIcon = Join-Path $repoRoot 'docs\picture\logo_new.ico'

Assert-Exists $repoRoot
Assert-Exists $srcRoot
Assert-Exists $mainScript
Assert-Exists $nativeBuildScript
if ($IncludeOptionalAssets) {
    Assert-Exists $faceModelDir
}

if (-not $IconPath) {
    $IconPath = $defaultIcon
}
elseif (-not [IO.Path]::IsPathRooted($IconPath)) {
    $IconPath = Join-Path $repoRoot $IconPath
}
Assert-Exists $IconPath
$IconPath = (Get-Item -LiteralPath $IconPath).FullName
if ([IO.Path]::GetExtension($IconPath) -ine '.ico') {
    throw "Windows Nuitka icon must be an .ico file: $IconPath"
}

$pythonInvocation = Resolve-BuildPython -RequestedPython $PythonExe -RepositoryRoot $repoRoot
$PythonExe = $pythonInvocation.Executable
[string[]]$pythonPrefixArgs = $pythonInvocation.PrefixArgs

if (-not [IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir = Join-Path $repoRoot $OutputDir
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = (Get-Item -LiteralPath $OutputDir).FullName
$compilationReport = Join-Path $OutputDir 'nuitka-compilation-report.xml'

Write-Host "Python interpreter: $($pythonInvocation.PythonPath)"
Write-Host "Python version: $($pythonInvocation.Version)"
Write-Host "Nuitka output directory: $OutputDir"
Write-Host "Application icon: $IconPath"

if ($RebuildNativeRuntime) {
    & $nativeBuildScript -BuildType Release -Jobs $Jobs
    if ($LASTEXITCODE -ne 0) {
        throw "Native runtime rebuild failed with exit code $LASTEXITCODE"
    }
}

if ($IncludeOptionalAssets -and -not $SkipNativeRuntimeSync) {
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
    '--include-qt-plugins=qml,multimedia,platforms',
    "--windows-console-mode=$ConsoleMode",
    '--assume-yes-for-downloads',
    "--report=$compilationReport",
    '--nofollow-import-to=numba',
    '--nofollow-import-to=llvmlite',
    '--nofollow-import-to=albumentations',
    '--nofollow-import-to=albucore',
    '--nofollow-import-to=pydantic',
    '--nofollow-import-to=pydantic_core',
    '--nofollow-import-to=typing_inspection',
    # People only uses InsightFace detection, recognition, and face alignment.
    # Exclude the unused Face3D tree, which can otherwise be discovered from
    # both a shadow source directory and site-packages by Nuitka.
    '--nofollow-import-to=insightface.thirdparty.face3d',
    '--nofollow-import-to=iPhoto.tests',
    '--nofollow-import-to=pytest',
    # Keep dynamically resolved compatibility exports available. Nuitka does
    # not infer the module names assembled by package-level __getattr__ hooks.
    '--include-package=iPhoto',
    '--include-package=maps',
    '--include-package=OpenGL',
    '--include-package=OpenGL_accelerate',
    '--include-package=cv2',
    '--include-package=reverse_geocoder',
    '--include-package=insightface',
    # PyExifTool is imported indirectly; freeze its Python package explicitly.
    '--include-package=exiftool',
    # pillow-heif is registered through a lazy import, so include both its
    # Python package and native extension explicitly.
    '--include-package=pillow_heif',
    '--include-module=_pillow_heif',
    '--noinclude-data-files=torch/include',
    "--output-dir=$OutputDir",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\resources\i18n')=iPhoto/resources/i18n",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\pets\model_manifest.json')=iPhoto/pets/model_manifest.json",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\schemas')=iPhoto/schemas",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\gui\ui\icon')=iPhoto/gui/ui/icon",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\gui\ui\qml')=iPhoto/gui/ui/qml",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\gl_image_viewer.frag')=iPhoto/gui/ui/widgets/gl_image_viewer.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\gl_image_viewer.vert')=iPhoto/gui/ui/widgets/gl_image_viewer.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.frag')=iPhoto/gui/ui/widgets/image_viewer_rhi.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.frag.qsb')=iPhoto/gui/ui/widgets/image_viewer_rhi.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.vert')=iPhoto/gui/ui/widgets/image_viewer_rhi.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.vert.qsb')=iPhoto/gui/ui/widgets/image_viewer_rhi.vert.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.frag')=iPhoto/gui/ui/widgets/image_viewer_overlay.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.frag.qsb')=iPhoto/gui/ui/widgets/image_viewer_overlay.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.vert')=iPhoto/gui/ui/widgets/image_viewer_overlay.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.vert.qsb')=iPhoto/gui/ui/widgets/image_viewer_overlay.vert.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.frag')=iPhoto/gui/ui/widgets/video_renderer.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.frag.qsb')=iPhoto/gui/ui/widgets/video_renderer.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.vert')=iPhoto/gui/ui/widgets/video_renderer.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.vert.qsb')=iPhoto/gui/ui/widgets/video_renderer.vert.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'maps\style.json')=maps/style.json",
    "--include-data-dir=$(Join-Path $srcRoot 'maps\map_widget\qml')=maps/map_widget/qml"
)

if ($IncludeOptionalAssets) {
    $arguments += "--include-data-dir=$faceModelDir=extension/models"
    $arguments += "--include-data-dir=$(Join-Path $srcRoot 'maps\tiles')=maps/tiles"
}

$arguments += "--windows-icon-from-ico=$IconPath"

$arguments += $mainScript

$pythonArguments = @($pythonPrefixArgs) + $arguments
& $PythonExe @pythonArguments
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE"
}

$builtExecutable = Join-Path $OutputDir 'entrypoint.dist\entrypoint.exe'
Assert-Exists $builtExecutable
$manifestTool = Join-Path $repoRoot 'tools\build_manifest.py'
$manifestOutput = Join-Path $OutputDir 'build-manifest.json'
$manifestArguments = @(
    $manifestTool,
    '--root', $repoRoot,
    '--artifact', $builtExecutable,
    '--build-driver', $PSCommandPath,
    '--build-flag', 'profile=windows',
    '--build-flag', "jobs=$Jobs",
    '--build-flag', "console=$ConsoleMode",
    '--build-flag', "optional_assets=$([bool]$IncludeOptionalAssets)",
    '--native-runtime', $extensionBinDir,
    '--asset', (Join-Path $srcRoot 'maps\tiles'),
    '--asset', (Join-Path $srcRoot 'iPhoto\resources\i18n'),
    '--output', $manifestOutput
)
& $PythonExe @pythonPrefixArgs @manifestArguments
if ($LASTEXITCODE -ne 0) {
    throw "Build manifest generation failed with exit code $LASTEXITCODE"
}
Write-Host "Build manifest: $manifestOutput"
