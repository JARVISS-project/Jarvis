import os
import site
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

    roots = (
        [Path(prefix) / "Lib" / "site-packages"]
        if prefix is not None
        else [Path(sys.prefix) / "Lib" / "site-packages"]
        + [Path(value) for value in site.getsitepackages()]
        + [Path(site.getusersitepackages())]
    )
    add_directory = add_dll_directory or os.add_dll_directory

    registered: list[Path] = []
    for root in dict.fromkeys(roots):
        nvidia_root = root / "nvidia"
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
