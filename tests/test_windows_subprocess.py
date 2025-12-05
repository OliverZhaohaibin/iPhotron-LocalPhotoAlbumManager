
import subprocess
import pytest
from unittest.mock import patch

# Ensure we can import the modules
from iPhotos.src.iPhoto.utils.exiftool import get_metadata_batch
from iPhotos.src.iPhoto.utils.ffmpeg import probe_media

@pytest.fixture
def mock_windows_environment(monkeypatch):
    """Mocks a Windows environment including OS name and subprocess constants."""
    monkeypatch.setattr("os.name", "nt")

    # Mock STARTUPINFO and its constants if they don't exist
    if not hasattr(subprocess, "STARTUPINFO"):
        class MockStartupInfo:
            dwFlags = 0
            wShowWindow = 0
        monkeypatch.setattr(subprocess, "STARTUPINFO", MockStartupInfo, raising=False)

    if not hasattr(subprocess, "STARTF_USESHOWWINDOW"):
        monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001, raising=False)

    if not hasattr(subprocess, "SW_HIDE"):
        monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)

    if not hasattr(subprocess, "CREATE_NO_WINDOW"):
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

def test_exiftool_windows_startupinfo(mock_windows_environment):
    """Test that exiftool calls subprocess with STARTUPINFO on Windows."""
    with patch("subprocess.run") as mock_run, \
         patch("shutil.which", return_value="exiftool"):

        # Configure the mock to return valid JSON so the function proceeds
        mock_run.return_value.stdout = b'[{"SourceFile": "test.jpg"}]'
        mock_run.return_value.returncode = 0

        from pathlib import Path
        get_metadata_batch([Path("test.jpg")])

        # Verify call arguments
        assert mock_run.called
        args, kwargs = mock_run.call_args

        # Check startupinfo
        assert "startupinfo" in kwargs
        startupinfo = kwargs["startupinfo"]
        assert startupinfo is not None
        # On actual Windows these are attributes, on our mock they are attributes
        # Check if dwFlags has STARTF_USESHOWWINDOW bit set
        assert (startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        assert startupinfo.wShowWindow == subprocess.SW_HIDE

        # Check creationflags
        assert "creationflags" in kwargs
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW

def test_ffmpeg_windows_startupinfo(mock_windows_environment):
    """Test that ffmpeg calls subprocess with STARTUPINFO on Windows."""
    with patch("subprocess.run") as mock_run:

        # Configure mock
        mock_run.return_value.stdout = b'{}'
        mock_run.return_value.returncode = 0

        from pathlib import Path
        try:
            probe_media(Path("test.mp4"))
        except Exception:
            # We don't care if it fails to parse JSON, just want to check subprocess call
            pass

        assert mock_run.called
        args, kwargs = mock_run.call_args

        assert "startupinfo" in kwargs
        startupinfo = kwargs["startupinfo"]
        assert startupinfo is not None
        assert (startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        assert startupinfo.wShowWindow == subprocess.SW_HIDE

        # Check creationflags
        assert "creationflags" in kwargs
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
