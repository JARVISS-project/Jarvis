from pathlib import Path

from jarvis.runtime.packaged import VERSION
from jarvis.runtime.release import INSTALLER_FILENAME, WINDOWS_VERSION
from scripts import build_windows_23bo as builder


def test_windows_release_identity_matches_pe_installer_and_output_directory():
    root = Path(__file__).resolve().parents[2]
    pe = (root / 'packaging/windows/version.txt').read_text(encoding='utf-8')
    assert VERSION in pe and WINDOWS_VERSION in pe
    assert 'filevers=(' + WINDOWS_VERSION.replace('.', ',') + ')' in pe
    assert builder.VERSION == VERSION
    assert builder.WINDOWS_VERSION == WINDOWS_VERSION
    assert INSTALLER_FILENAME == 'JARVIS-Setup-' + VERSION + '.exe'
    assert builder.OUTPUT != root / 'dist/23bo'  # Preserve historical artifacts.
    installer = (root / 'packaging/windows/installer.iss').read_text(encoding='utf-8')
    assert 'AppVersion={#AppNumericVersion}' in installer
    assert 'OutputBaseFilename=JARVIS-Setup-{#AppRelease}' in installer
    assert 'SetupIconFile={#SetupIcon}' in installer
