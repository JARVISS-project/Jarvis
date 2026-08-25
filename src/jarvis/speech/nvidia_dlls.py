import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


_NVIDIA_PACKAGES = ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")
_DLL_DIRECTORY_HANDLES: dict[Path, Any] = {}


def register_nvidia_dll_directories(
    *,
    platform: str | None = None,
    prefix: str | os.PathLike[str] | None = None,
    add_dll_directory: Callable[[str], Any] | None = None,
) -> tuple[Path, ...]:
    """Register NVIDIA wheel DLL directories for the current Windows Python."""
    current_platform = sys.platform if platform is None else platform
    if current_platform != "win32":
        return ()

    python_prefix = Path(sys.prefix if prefix is None else prefix)
    nvidia_root = python_prefix / "Lib" / "site-packages" / "nvidia"
    add_directory = add_dll_directory or os.add_dll_directory

    registered: list[Path] = []
    for package in _NVIDIA_PACKAGES:
        directory = nvidia_root / package / "bin"
        if not directory.is_dir():
            continue
        if directory not in _DLL_DIRECTORY_HANDLES:
            try:
                _DLL_DIRECTORY_HANDLES[directory] = add_directory(str(directory))
            except OSError:
                continue
        registered.append(directory)
    return tuple(registered)
