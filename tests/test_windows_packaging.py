import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DIAGNOSTIC_LAUNCHER = SCRIPTS / "run_packaged_diagnostic.ps1"


def test_native_inspector_preflight_precedes_staging_and_compilation():
    script = (SCRIPTS / "build_nuitka_windows.ps1").read_text(encoding="utf-8")
    preflight = script.index("--require-complete --check-native-inspector")
    guard = script.index("if ($LASTEXITCODE -ne 0)", preflight)
    assert "throw 'Recognition build preflight failed" in script[guard:script.index('$stagedModels = ""')]
    for later in ('$stagedModels = ""', "if ($RebuildNativeRuntime)", "& $PythonExe @pythonArguments"):
        assert preflight < guard < script.index(later)


@pytest.mark.parametrize("optimization", [None, "PYTHONOPTIMIZE", "-O"])
@pytest.mark.parametrize(
    ("from_selected_checkout", "profile_code", "error"),
    [
        pytest.param(True, "def configure(): pass\n", None, id="selected-source"),
        pytest.param(
            False,
            "def configure(): pass\n",
            "imported a different iPhoto checkout",
            id="stale-source",
        ),
        pytest.param(True, "", "no callable configure export", id="missing-configure"),
        pytest.param(True, "configure = None\n", "no callable configure export", id="noncallable"),
    ],
)
def test_source_preflight(
    tmp_path: Path,
    optimization: str | None,
    from_selected_checkout: bool,
    profile_code: str,
    error: str | None,
) -> None:
    script = (SCRIPTS / "build_nuitka_windows.ps1").read_text(encoding="utf-8")
    probe = re.search(r"\$sourceProbe = @'\n(.*?)\n'@", script, flags=re.DOTALL)
    assert probe is not None

    checkout = tmp_path / "fixture checkout"
    profile_path = Path("iPhoto/bootstrap/startup_profile.py")
    actual = checkout / profile_path
    actual.parent.mkdir(parents=True)
    (actual.parent / "__init__.py").touch()
    (actual.parent.parent / "__init__.py").touch()
    actual.write_text(profile_code, encoding="utf-8")
    expected = actual if from_selected_checkout else tmp_path / "other checkout" / profile_path

    env = {**os.environ, "PYTHONPATH": str(checkout)}
    env.pop("PYTHONOPTIMIZE", None)
    command = [sys.executable, "-B"]
    if optimization == "PYTHONOPTIMIZE":
        env["PYTHONOPTIMIZE"] = "1"
    elif optimization == "-O":
        command.append("-O")

    result = subprocess.run(
        [*command, "-c", probe[1], str(expected)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert f"Startup profile source: {actual.resolve()}" in result.stdout, result.stderr
    if error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert error in result.stderr


def test_diagnostic_launcher_uses_normal_window_with_redirection() -> None:
    script = DIAGNOSTIC_LAUNCHER.read_text(encoding="utf-8").replace("`\n", " ")
    launch = next(line.casefold() for line in script.splitlines() if " = Start-Process " in line)
    assert "-windowstyle" not in launch
    assert {"-redirectstandardoutput", "-redirectstandarderror", "-passthru", "-wait"} <= set(
        launch.split()
    )


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows GUI subsystem")
def test_diagnostic_launcher_collects_gui_process_logs(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("requires Windows PowerShell")

    # Build a tiny GUI-subsystem executable with Windows PowerShell's built-in compiler.
    driver = tmp_path / "launch_fixture.ps1"
    driver.write_text(
        r"""param([string]$Launcher, [string]$OutputDir)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'GUI fixture.exe'
Add-Type -OutputAssembly $exe -OutputType WindowsApplication -TypeDefinition @'
using System;
using System.IO;

public static class GuiFixture
{
    public static int Main()
    {
        string logDir = Environment.GetEnvironmentVariable("IPHOTO_LOG_DIR");
        string faultHandler = Environment.GetEnvironmentVariable("PYTHONFAULTHANDLER");
        File.WriteAllLines(Path.Combine(logDir, "iPhoto.log"), new string[] {
            Environment.CurrentDirectory, faultHandler
        });
        Console.WriteLine("fixture stdout");
        return 23;
    }
}
'@
$env:IPHOTO_LOG_DIR = 'prior-dir'
$env:PYTHONFAULTHANDLER = 'prior-value'
& $Launcher -Executable $exe -OutputDir $OutputDir
if ($env:IPHOTO_LOG_DIR -ne 'prior-dir' -or $env:PYTHONFAULTHANDLER -ne 'prior-value') {
    throw 'The launcher did not restore the caller environment.'
}
""",
        encoding="ascii",
    )
    output_dir = tmp_path / "diagnostic output"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(driver),
            str(DIAGNOSTIC_LAUNCHER),
            str(output_dir),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No stderr output was captured." in result.stdout
    (logs,) = output_dir.iterdir()
    assert (logs / "stdout.log").read_text(encoding="ascii").strip() == "fixture stdout"
    assert (logs / "stderr.log").stat().st_size == 0
    assert (logs / "exit-code.txt").read_text(encoding="ascii").strip() == "Exit code: 23"
    working_dir, fault_handler = (logs / "iPhoto.log").read_text(encoding="utf-8").splitlines()
    assert Path(working_dir).resolve() == tmp_path.resolve()
    assert fault_handler == "1"
