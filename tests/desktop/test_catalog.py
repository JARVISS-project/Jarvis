from pathlib import Path

import pytest

from jarvis.desktop.catalog import DesktopApplicationCatalog
from jarvis.desktop.models import DesktopApplication


def test_catalog_rejects_relative_or_non_executable_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absoluto"):
        DesktopApplicationCatalog(
            [DesktopApplication("bad", Path("relative.exe"))]
        )

    text_file = tmp_path / "thing.txt"
    with pytest.raises(ValueError, match=r"\.exe"):
        DesktopApplicationCatalog(
            [DesktopApplication("bad", text_file)]
        )


def test_catalog_alias_is_normalized(tmp_path: Path) -> None:
    executable = tmp_path / "calc.exe"
    executable.write_bytes(b"fake")
    catalog = DesktopApplicationCatalog(
        [DesktopApplication("calculator", executable)],
        {"Calculadora": "calculator"},
    )

    assert catalog.resolve("calculadora").executable == executable


@pytest.mark.parametrize(
    "process_name",
    ["", "relative\\evil.exe", "../evil.exe", "notepad.com"],
)
def test_catalog_rejects_invalid_process_identity(
    tmp_path: Path,
    process_name: str,
) -> None:
    executable = tmp_path / "notepad.exe"

    with pytest.raises(ValueError, match="Processo de verificação inválido"):
        DesktopApplicationCatalog(
            [DesktopApplication("notepad", executable, (process_name,))]
        )


def test_catalog_derives_process_identity_from_executable(tmp_path: Path) -> None:
    executable = tmp_path / "notepad.exe"
    executable.write_bytes(b"fake")
    catalog = DesktopApplicationCatalog(
        [DesktopApplication("notepad", executable)]
    )

    assert catalog.resolve("notepad").process_names == ("notepad.exe",)


def test_catalog_rejects_invalid_window_class(tmp_path: Path) -> None:
    executable = tmp_path / "notepad.exe"

    with pytest.raises(ValueError, match="Classe de janela inválida"):
        DesktopApplicationCatalog(
            [
                DesktopApplication(
                    "notepad",
                    executable,
                    ("notepad.exe",),
                    ("Notepad class with spaces",),
                )
            ]
        )
