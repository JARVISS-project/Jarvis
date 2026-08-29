import sys
from pathlib import Path

import pytest

from jarvis.integrations.google.errors import GoogleOperationInProgressError
from jarvis.integrations.google.operation_lock import GoogleOperationLock


@pytest.mark.skipif(sys.platform != "win32", reason="mutex Win32")
def test_windows_google_operation_lock_is_exclusive_and_reusable(
    tmp_path: Path,
) -> None:
    name = rf"Local\JARVIS.GoogleOAuth.Test.{tmp_path.name}"
    first = GoogleOperationLock(tmp_path / "one.lock", name)
    second = GoogleOperationLock(tmp_path / "two.lock", name)

    first.acquire()
    try:
        with pytest.raises(GoogleOperationInProgressError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
