from pathlib import Path
import os

import pytest

from scripts.windows_build_environment import isolated_build_environment, select_native_binaries


def test_unrelated_path_and_qt_overrides_do_not_leak_into_builder(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path/'unrelated-poppler'))
    monkeypatch.setenv('QT_PLUGIN_PATH', str(tmp_path/'foreign-qt'))
    monkeypatch.setenv('PYTHONHOME', str(tmp_path/'foreign-python'))
    env = isolated_build_environment(tmp_path/'venv', tmp_path/'src', tmp_path/'python', tmp_path/'Windows')
    assert 'unrelated-poppler' not in env['PATH']
    assert 'QT_PLUGIN_PATH' not in env and 'PYTHONHOME' not in env
    assert env['PYTHONPATH'] == str(tmp_path/'src')
    assert env['PATH'].split(os.pathsep)[0] == str(tmp_path/'venv/Scripts')


def test_native_collection_rejects_foreign_icu_instead_of_shipping_it(tmp_path):
    with pytest.raises(RuntimeError, match='outside pinned'):
        select_native_binaries([('icuuc.dll', str(tmp_path/'poppler/icuuc.dll'), 'BINARY')],
            venv=tmp_path/'venv', python=tmp_path/'python', windows=tmp_path/'Windows')


def test_os_icu_is_not_copied_and_pinned_native_extensions_remain(tmp_path):
    entries = [('icuuc.dll', str(tmp_path/'Windows/System32/icuuc.dll'), 'BINARY'),
               ('msvcp140.dll', str(tmp_path/'venv/msvcp140.dll'), 'BINARY'),
               ('QtCore.pyd', str(tmp_path/'venv/Lib/site-packages/QtCore.pyd'), 'EXTENSION'),
               ('python313.dll', str(tmp_path/'python/python313.dll'), 'BINARY')]
    assert select_native_binaries(entries, venv=tmp_path/'venv', python=tmp_path/'python',
                                  windows=tmp_path/'Windows') == entries[2:]


def test_wheel_private_hashed_runtime_is_not_confused_with_os_prerequisite(tmp_path):
    private = ('numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll',
               str(tmp_path/'venv/Lib/site-packages/numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll'), 'BINARY')
    assert select_native_binaries([private], venv=tmp_path/'venv', python=tmp_path/'python',
                                  windows=tmp_path/'Windows') == [private]


def test_unused_pdf_and_virtual_keyboard_plugins_do_not_pull_optional_stacks(tmp_path):
    names = ['qpdf.dll', 'Qt6Pdf.dll', 'qtvirtualkeyboardplugin.dll', 'Qt6VirtualKeyboard.dll', 'qwindows.dll']
    entries = [(name, str(tmp_path/'venv'/name), 'BINARY') for name in names]
    assert select_native_binaries(entries, venv=tmp_path/'venv', python=tmp_path/'python',
                                  windows=tmp_path/'Windows') == entries[-1:]
