
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.iPhoto.utils.exiftool import get_metadata_batch

def test_get_metadata_batch_uses_posix_paths():
    """Verify that paths are written to the argument file using POSIX style (forward slashes)."""

    # Create a mock path that behaves like a Windows path
    mock_path = MagicMock(spec=Path)
    mock_path.__str__.return_value = "D:\\folder\\file.jpg"
    mock_path.as_posix.return_value = "D:/folder/file.jpg"

    # We need to ensure that the temp file content is checked before it is deleted.
    # We can do this by inspecting the file inside the mock for subprocess.run

    def check_arg_file(*args, **kwargs):
        # The command is the first argument
        cmd = args[0]
        # The last argument is the path to the temp file
        arg_file_path = cmd[-1]

        with open(arg_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # This is where we verify the fix.
        # If the code uses str(path), it will be "D:\\folder\\file.jpg"
        # If the code uses path.as_posix(), it will be "D:/folder/file.jpg"
        if "D:/folder/file.jpg" not in content:
            raise AssertionError(f"Argument file content incorrect. Found:\n{content}")

        if "D:\\folder\\file.jpg" in content:
             raise AssertionError(f"Argument file contains backslashes. Found:\n{content}")

        return subprocess.CompletedProcess(args, 0, stdout=b"[]", stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/exiftool"), \
         patch("subprocess.run", side_effect=check_arg_file) as mock_run:

        get_metadata_batch([mock_path])

        assert mock_run.called
