"""Actual install/reinstall/uninstall, test AppId and temporary data only."""
import argparse
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import os

from jarvis.runtime.release import INSTALLER_FILENAME


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-dir', type=Path, default=root / 'dist/23by')
    parser.add_argument('--previous-installer', type=Path)
    args = parser.parse_args()
    temporary = Path(tempfile.mkdtemp(prefix='jarvis-23bo-install-'))
    profile = temporary / 'profile'
    program = temporary / 'program'
    for folder in ('data', 'google', 'models', 'logs'):
        (profile / folder).mkdir(parents=True)
        (profile / folder / 'retain-neutral.txt').write_text('Neutral temporary user data; preserve.', encoding='utf-8')
    watched = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in profile.rglob('*') if p.is_file()}
    installer = args.artifact_dir.resolve() / INSTALLER_FILENAME
    hidden = subprocess.STARTUPINFO()
    hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    def run(arguments):
        environment = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
        environment.update(PATH=os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot'],
                           QT_QPA_PLATFORM='offscreen')
        return subprocess.run([str(a) for a in arguments], cwd=temporary,
                              env=environment, timeout=120, startupinfo=hidden,
                              creationflags=subprocess.CREATE_NO_WINDOW).returncode
    report = {'status': 'pending', 'habitual_profile_changed': False,
              'production_appid_used': False, 'microphone_playback': False,
              'temporary_root': str(temporary)}
    output = temporary / 'installer-check.json'
    print('Installer acceptance root:', temporary, flush=True)
    command = [installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART',
               '/TestInstance=1', '/TestProfile=' + str(profile), '/DIR=' + str(program),
               '/LOG=' + str(temporary / 'install.log')]
    try:
        if args.previous_installer:
            previous = [args.previous_installer.resolve(), *command[1:]]
            report['previous_install_exit'] = run(previous)
            assert report['previous_install_exit'] == 0
        report['install_exit'] = run(command)
        assert report['install_exit'] == 0 and (program / 'JARVIS.exe').exists()
        manifest = json.loads((args.artifact_dir / 'manifest.json').read_text(encoding='utf-8'))
        expected = [entry for entry in manifest['artifacts'] if entry['path'].startswith('JARVIS/')]
        for entry in expected:
            path = program / Path(entry['path']).relative_to('JARVIS')
            assert path.stat().st_size == entry['bytes']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
        report['installed_files_verified'] = len(expected)
        report['installed_package_check_exit'] = run([
            program / 'JARVIS.exe', '--profile-root', profile, '--package-check'])
        assert report['installed_package_check_exit'] == 0
        report['installed_native_ui_verified'] = True
        watched = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in profile.rglob('*') if p.is_file()}
        report['reinstall_exit'] = run(command)
        assert report['reinstall_exit'] == 0
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in watched.items())
        report['reinstall_preserved_data'] = True
        report['uninstall_exit'] = run([program / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
        assert report['uninstall_exit'] == 0 and not (program / 'JARVIS.exe').exists()
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in watched.items())
        report['uninstall_preserved_data'] = True
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'blocked' if isinstance(error, OSError) else 'failed'
        report['error_type'] = type(error).__name__
        if isinstance(error, OSError):
            report['winerror'] = getattr(error, 'winerror', None)
        raise
    finally:
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Installer evidence:', output, flush=True)


if __name__ == '__main__':
    main()
