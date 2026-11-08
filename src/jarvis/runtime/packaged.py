"""Windows desktop entry and isolated user directories, no alternate Tools path."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from jarvis.runtime.release import VERSION


def configure_profile(root: Path | None = None) -> Path:
    if root is None:
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise RuntimeError('LOCALAPPDATA indisponÃ­vel.')
        root = Path(local) / 'JARVIS' / 'Desktop'
    root = root.expanduser().resolve()
    program = Path(sys.executable).resolve().parent
    if root == program or program in root.parents:
        raise ValueError('Perfil deve ficar fora da pasta do programa.')
    for folder in ('data', 'logs', 'cache', 'models', 'documents', 'google', 'tmp'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    # Process-local isolation. Never consume the author's ambient Google path.
    os.environ['JARVIS_GOOGLE_CREDENTIAL_DIR'] = str(root / 'google')
    os.environ['JARVIS_PACKAGED_PROFILE'] = str(root)
    os.environ['HF_HOME'] = str(root / 'cache' / 'huggingface')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['TEMP'] = os.environ['TMP'] = str(root / 'tmp')
    os.chdir(root)
    settings = root / 'data' / 'settings.json'
    if not settings.exists():
        with settings.open('x', encoding='utf-8') as stream:
            json.dump({'assistant_address_mode': 'natural', 'user_display_name': '',
                       'agent_name': 'Jarvis', 'obsidian_vault': '',
                       'tts_enabled': False, 'tts_provider': 'piper',
                       'stt_profile': 'medium', 'stt_execution_mode': 'process',
                       'google_calendar_enabled': False, 'google_calendar_write_enabled': False,
                       'google_drive_read_enabled': False, 'google_docs_read_enabled': False,
                       'briefing_auto_enabled': False}, stream)
    # A fresh packaged profile requires explicit grants even for its document root.
    permissions = root / 'data' / 'permissions.json'
    if not permissions.exists():
        from jarvis.security.permissions import PermissionStore
        store = PermissionStore(permissions, (root / 'documents',))
        for action in ('read', 'write', 'delete'):
            rules, revision, valid = store.read()
            store.set_rule('path:' + str((root / 'documents').resolve()), action, 'block', expected_revision=revision)
        rules, revision, valid = store.read()
        store.set_rule('microphone.capture', 'use', 'block', expected_revision=revision)
    return root


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='JARVIS')
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--profile-root', type=Path)
    parser.add_argument('--ui-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--endpoint', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--runtime-stop', action='store_true')
    parser.add_argument('--voice', action='store_true', help='Ativar voz explicitamente; requer permissÃµes e modelos provisionados.')
    parser.add_argument('--package-check', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--noninteractive', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--check-wav', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--check-stt-model', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--check-voice', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--check-stt-device', choices=('cuda', 'cpu'), default='cuda', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        root = configure_profile(args.profile_root)
        if args.package_check:
            from jarvis.runtime.package_check import run_check
            run_check(root, args)
        elif args.ui_child:
            from jarvis.ui.__main__ import main as ui_main
            if args.diagnostic:
                os.environ['JARVIS_PACKAGED_DIAGNOSTIC'] = '1'
            ui_main(['--live', '--control-center', '--endpoint', str(args.endpoint)])
        else:
            from jarvis.runtime.cli import main as runtime_main
            if args.diagnostic:
                os.environ['JARVIS_PACKAGED_DIAGNOSTIC'] = '1'
            runtime_main(['--runtime-stop'] if args.runtime_stop else ['--ui', *(['--voice'] if args.voice else [])])
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            return
        message = str(error) if isinstance(error, (SystemExit, ValueError)) else type(error).__name__
        if sys.platform == 'win32' and not args.package_check and not args.noninteractive:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'JARVIS nÃ£o iniciou: ' + message + '\nUse o atalho DiagnÃ³stico ou consulte o manual.', 'JARVIS', 0x10)
        raise SystemExit(2) from error
