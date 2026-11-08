"""Pinned isolated Windows build. No habitual venv/profile reads or binary uploads."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv

from jarvis.runtime.release import VERSION, WINDOWS_VERSION

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.windows_build_environment import isolated_build_environment

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'data' / 'packaging-public'
OUTPUT = ROOT / 'dist' / '23by'


def run(arguments: list[str]) -> None:
    environment = isolated_build_environment(WORK / 'venv', ROOT / 'src',
                                             Path(sys.base_prefix), Path(os.environ['SystemRoot']))
    subprocess.run(arguments, cwd=ROOT, env=environment, check=True)


def prepare() -> None:
    if sys.version_info[:3] != (3, 13, 14) or sys.maxsize <= 2**32:
        raise RuntimeError('Use CPython 3.13.14 x64 conforme o lock.')
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / 'venv'
    if not (target / 'Scripts/python.exe').exists():
        venv.create(target, with_pip=True)
    run([str(target / 'Scripts/python.exe'), '-m', 'pip', 'install', '--require-hashes',
         '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
         '-r', str(ROOT / 'requirements/windows-py313/package.lock')])


def license_destination(directory: Path, name: str, relative: Path) -> Path:
    """Keep distinct wheel notices distinct, without trusting parent traversal."""
    parts = relative.parts
    if relative.anchor or '..' in parts:
        raise ValueError('License path outside wheel rejected')
    return directory / name / relative


def assets() -> None:
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    import struct
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    QSvgRenderer(str(ROOT / 'packaging/windows/jarvis.svg')).render(painter)
    painter.end()
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    data = bytes(payload)
    (WORK / 'jarvis.ico').write_bytes(struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(data), 22) + data)
    licenses = WORK / 'licenses'
    licenses.mkdir(exist_ok=True)
    project_license = licenses / 'JARVIS'
    project_license.mkdir(exist_ok=True)
    (project_license / 'LICENSE').write_bytes((ROOT / 'LICENSE').read_bytes())
    (project_license / 'THIRD_PARTY_NOTICES.md').write_bytes((ROOT / 'THIRD_PARTY_NOTICES.md').read_bytes())
    upstream = licenses / 'upstream'
    upstream.mkdir(exist_ok=True)
    for pin in json.loads((ROOT / 'packaging/windows/licenses.lock.json').read_text(encoding='utf-8')):
        target = upstream / pin['name']
        if not target.exists():
            target.write_bytes(urllib.request.urlopen(pin['url'], timeout=30).read())
        if hashlib.sha256(target.read_bytes()).hexdigest() != pin['sha256']:
            raise RuntimeError('Upstream license integrity failed')
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        (licenses / 'CPython-LICENSE.txt').write_bytes(python_license.read_bytes())
    inventory = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        files = []
        for relative in distribution.files or []:
            if not any(word in str(relative).casefold() for word in ('license', 'copying', 'notice')):
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file() and source.suffix.lower() in {'.txt', '.md', '.rst', ''} and source.stat().st_size < 2_000_000:
                wheel_path = Path(relative)
                if '..' in wheel_path.parts:
                    # Piper's RECORD points to venv/COPYING. Anchor this installed
                    # notice to the isolated venv, never to a destination parent.
                    wheel_path = source.resolve().relative_to((WORK / 'venv').resolve())
                target = license_destination(licenses, name, wheel_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                files.append(str(target.relative_to(licenses)))
        inventory.append({'name': name, 'version': distribution.version,
                          'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'), 'files': files})
    (licenses / 'inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    (licenses / 'README.txt').write_text('JARVIS: GPL-3.0-or-later, see JARVIS/LICENSE. Third-party components retain their original licenses. Qt is dynamically linked; replacement/relinking permitted. Piper GPL-3.0. Corresponding-source archives and build instructions must accompany the public binary release. No models, CUDA/NVIDIA DLLs or author data included. Sources: https://github.com/OHF-Voice/piper1-gpl ; https://download.qt.io/official_releases/QtForPython/ ; https://github.com/PyAV-Org/PyAV .', encoding='utf-8')


def compiler() -> Path:
    pin = json.loads((ROOT / 'packaging/windows/toolchain.json').read_text(encoding='utf-8'))['inno']
    installer = WORK / ('innosetup-' + pin['version'] + '.exe')
    if not installer.exists():
        with urllib.request.urlopen(pin['url'], timeout=30) as response:
            installer.write_bytes(response.read())
    if hashlib.sha256(installer.read_bytes()).hexdigest() != pin['sha256']:
        raise RuntimeError('Inno installer integrity failed')
    destination = WORK / 'compiler'
    executable = destination / 'ISCC.exe'
    if not executable.exists():
        run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/CURRENTUSER',
             '/NOICONS', '/DIR=' + str(destination)])
    return executable


def manifest() -> None:
    artifacts = []
    for path in sorted(OUTPUT.rglob('*')):
        if path.is_file() and path.name != 'manifest.json':
            artifacts.append({'path': path.relative_to(OUTPUT).as_posix(), 'bytes': path.stat().st_size,
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    source_hash = hashlib.sha256()
    for path in sorted((ROOT / 'src/jarvis').rglob('*.py')):
        source_hash.update(path.relative_to(ROOT).as_posix().encode())
        source_hash.update(path.read_bytes())
    report = {'version': VERSION, 'python': sys.version.split()[0],
              'source_commit': source_commit, 'source_tree_sha256': source_hash.hexdigest(),
              'build_environment': 'isolated package.lock; no development venv',
              'signed': False, 'clean_machine_gate': 'pending', 'artifacts': artifacts}
    (OUTPUT / 'manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def main() -> None:
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--assets', action='store_true')
    parser.add_argument('--installer', action='store_true')
    parser.add_argument('--manifest', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUTPUT)
    args = parser.parse_args()
    OUTPUT = args.output_dir.resolve()
    if args.prepare:
        prepare()
    elif args.assets:
        assets()
    elif args.installer:
        run([str(compiler()), '/DSourceDir=' + str(OUTPUT / 'JARVIS'),
             '/DOutputDir=' + str(OUTPUT), '/DAppRelease=' + VERSION,
             '/DAppNumericVersion=' + WINDOWS_VERSION, '/DSetupIcon=' + str(WORK / 'jarvis.ico'),
             str(ROOT / 'packaging/windows/installer.iss')])
        manifest()
    elif args.manifest:
        manifest()
    else:
        run([str(WORK / 'venv/Scripts/python.exe'), str(__file__), '--assets',
             '--output-dir', str(OUTPUT)])
        run([str(WORK / 'venv/Scripts/python.exe'), '-m', 'PyInstaller', '--noconfirm',
             '--distpath', str(OUTPUT), '--workpath', str(WORK / 'build' / OUTPUT.name),
             str(ROOT / 'packaging/windows/jarvis.spec')])
        manifest()


if __name__ == '__main__':
    main()
