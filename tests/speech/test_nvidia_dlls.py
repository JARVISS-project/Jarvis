from pathlib import Path

import pytest

from jarvis.speech import nvidia_dlls


class FakeHandle:
    pass


def test_registers_existing_nvidia_directories_and_keeps_handles(
    tmp_path: Path,
) -> None:
    nvidia_root = tmp_path / "Lib" / "site-packages" / "nvidia"
    expected = [
        nvidia_root / "cublas" / "bin",
        nvidia_root / "cudnn" / "bin",
        nvidia_root / "cuda_runtime" / "bin",
    ]
    for directory in expected:
        directory.mkdir(parents=True)

    handles: list[FakeHandle] = []
    calls: list[str] = []

    def add_directory(path: str) -> FakeHandle:
        calls.append(path)
        handle = FakeHandle()
        handles.append(handle)
        return handle

    nvidia_dlls._DLL_DIRECTORY_HANDLES.clear()
    registered = nvidia_dlls.register_nvidia_dll_directories(
        platform="win32",
        prefix=tmp_path,
        add_dll_directory=add_directory,
    )

    assert registered == tuple(expected)
    assert calls == [str(directory) for directory in expected]
    assert list(nvidia_dlls._DLL_DIRECTORY_HANDLES.values()) == handles
    assert nvidia_root / "cuda_nvrtc" / "bin" not in registered


def test_registration_is_idempotent(tmp_path: Path) -> None:
    directory = (
        tmp_path / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin"
    )
    directory.mkdir(parents=True)
    calls: list[str] = []

    nvidia_dlls._DLL_DIRECTORY_HANDLES.clear()
    for _ in range(2):
        nvidia_dlls.register_nvidia_dll_directories(
            platform="win32",
            prefix=tmp_path,
            add_dll_directory=lambda path: calls.append(path) or FakeHandle(),
        )

    assert calls == [str(directory)]


def test_does_nothing_outside_windows(tmp_path: Path) -> None:
    directory = (
        tmp_path / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin"
    )
    directory.mkdir(parents=True)
    calls: list[str] = []

    nvidia_dlls._DLL_DIRECTORY_HANDLES.clear()
    registered = nvidia_dlls.register_nvidia_dll_directories(
        platform="linux",
        prefix=tmp_path,
        add_dll_directory=lambda path: calls.append(path),
    )

    assert registered == ()
    assert calls == []


def test_discovers_nvidia_wheels_in_user_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_site = tmp_path / "user-site"
    directory = user_site / "nvidia" / "cublas" / "bin"
    directory.mkdir(parents=True)
    monkeypatch.setattr(nvidia_dlls.sys, "prefix", str(tmp_path / "python"))
    monkeypatch.setattr(nvidia_dlls.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(
        nvidia_dlls.site, "getusersitepackages", lambda: str(user_site),
    )
    nvidia_dlls._DLL_DIRECTORY_HANDLES.clear()

    registered = nvidia_dlls.register_nvidia_dll_directories(
        platform="win32", add_dll_directory=lambda _path: FakeHandle(),
    )

    assert registered == (directory,)
