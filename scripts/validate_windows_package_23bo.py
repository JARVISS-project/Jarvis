"""Real portable/install acceptance, only new temporary profile and test AppId."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from threading import Event
from uuid import uuid4

from jarvis.runtime.release import INSTALLER_FILENAME

ROOT = Path(__file__).resolve().parents[1]


def owned_runtime_pids(rows, executable, parent_pid):
    """Restrict failure cleanup to descendants of this test's own executable."""
    owned = {parent_pid}
    while True:
        added = {row['ProcessId'] for row in rows if row.get('ParentProcessId') in owned
                 and str(row.get('ExecutablePath', '')).casefold() == str(executable).casefold()}
        if added <= owned:
            return owned
        owned.update(added)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--voice', type=Path)
    parser.add_argument('--wav', type=Path)
    parser.add_argument('--stt-model', type=Path)
    parser.add_argument('--stt-device', choices=('cpu', 'cuda'), default='cuda')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--local-text', action='store_true', help='Test a neutral question against local Ollama only.')
    parser.add_argument('--visible-ui', action='store_true', help='Use the native Windows platform for visual acceptance.')
    parser.add_argument('--artifact-dir', type=Path, default=ROOT / 'dist/23by')
    args = parser.parse_args()
    temporary = Path(tempfile.mkdtemp(prefix='jarvis-23bo-acceptance-'))
    profile = temporary / 'profile'
    cwd = temporary / 'different-cwd'
    cwd.mkdir()
    source = args.artifact_dir.resolve()
    portable = temporary / 'portable'
    shutil.copytree(source / 'JARVIS', portable)
    executable = portable / 'JARVIS.exe'
    env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
    env.update(PATH=os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot'],
               QT_QPA_PLATFORM='windows' if args.visible_ui else 'offscreen')
    report = {'status': 'running', 'test_root': str(temporary), 'clean_machine': 'not available / pending', 'habitual_profile_modified': False}
    hidden = subprocess.STARTUPINFO()
    hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    def run(command, timeout=150):
        return subprocess.run([str(c) for c in command], cwd=cwd, env=env, timeout=timeout,
                              startupinfo=None if args.visible_ui else hidden,
                              creationflags=0 if args.visible_ui else subprocess.CREATE_NO_WINDOW)
    check = [executable, '--profile-root', profile, '--package-check']
    if args.voice: check += ['--check-voice', args.voice.resolve()]
    if args.wav: check += ['--check-wav', args.wav.resolve()]
    if args.stt_model: check += ['--check-stt-model', args.stt_model.resolve()]
    check += ['--check-stt-device', args.stt_device]
    print('Acceptance root:', temporary, flush=True)
    try:
        outcome = run(check)
    except OSError as error:
        report.update(status='blocked_before_process_start',
                      winerror=getattr(error, 'winerror', None),
                      process_started=False, packaged_runtime_validated=False)
        (temporary / 'acceptance.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8')
        print('Acceptance evidence:', temporary / 'acceptance.json', flush=True)
        raise
    report['package_check_exit'] = outcome.returncode
    if (profile / 'package-check.json').exists():
        report['packaged_check'] = json.loads((profile / 'package-check.json').read_text(encoding='utf-8'))
    if outcome.returncode != 0:
        (temporary / 'acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        raise RuntimeError('Packaged check failed; inspect temporary evidence')
    process = subprocess.Popen([str(executable), '--profile-root', str(profile), '--noninteractive'],
                               cwd=cwd, env=env, startupinfo=None if args.visible_ui else hidden,
                               creationflags=0 if args.visible_ui else subprocess.CREATE_NO_WINDOW)
    try:
        endpoint = profile / 'data/runtime/ambient-ui.json'
        deadline = time.monotonic() + 30
        while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        assert endpoint.exists() and process.poll() is None
        report['normal_runtime_ui_spawn'] = True
        if args.local_text:
            from jarvis.application.state import AgentStateBus
            from jarvis.application.state_ipc import LocalStateClient, StateEndpoint
            from jarvis.application.text_interaction import TextEventBus
            bus, ready, done, events = TextEventBus(), Event(), Event(), []
            def receive(event):
                events.append(event)
                if event.get('status') == 'ready': ready.set()
                if event.get('status') in {'done', 'error', 'confirmation', 'cancelled'} and event.get('type') == 'text': done.set()
            unsubscribe = bus.subscribe(receive)
            client = LocalStateClient(AgentStateBus(), StateEndpoint.read(endpoint), text_bus=bus)
            client.start()
            try:
                assert ready.wait(5)
                generation = next(event['generation'] for event in reversed(events) if event.get('status') == 'ready')
                interaction = uuid4().hex
                client.send_command({'command': 'text_submit', 'generation': generation,
                    'interaction': interaction, 'text': 'Explique em uma frase o que é uma variável em programação.'})
                completed = done.wait(60)
                report['local_text_events'] = [{'type': e.get('type'), 'status': e.get('status')}
                                               for e in events]
                assert completed, report['local_text_events']
                result = next(event for event in reversed(events) if event.get('interaction') == interaction
                              and event.get('status') in {'done', 'error', 'confirmation', 'cancelled'})
                (temporary / 'neutral-local-response.txt').write_text(result.get('text', ''), encoding='utf-8')
                assert result['status'] == 'done' and result.get('text', '').strip()
                report['local_text_response'] = {'status': 'verified', 'characters': len(result['text']),
                    'response_sha256': hashlib.sha256(result['text'].encode()).hexdigest(), 'external_api_calls': 0}
            finally:
                client.close()
                unsubscribe()
        duplicate = run([executable, '--profile-root', profile, '--noninteractive'], timeout=10)
        assert duplicate.returncode == 2
        report['second_instance_rejected'] = True
        stopper = run([executable, '--profile-root', profile, '--runtime-stop', '--noninteractive'], timeout=10)
        assert stopper.returncode == 0
        assert process.wait(timeout=20) == 0
        report['graceful_shutdown'] = True
    finally:
        if process.poll() is None:
            powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
            query = "Get-CimInstance Win32_Process -Filter \"Name='JARVIS.exe'\" | Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress"
            raw = subprocess.check_output([powershell, '-NoProfile', '-Command', query], text=True)
            rows = json.loads(raw) if raw.strip() else []
            owned = owned_runtime_pids(rows if isinstance(rows, list) else [rows], executable, process.pid)
            try:
                run([executable, '--profile-root', profile, '--runtime-stop', '--noninteractive'], timeout=10)
                process.wait(timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            cleanup_env = dict(os.environ, JARVIS_ACCEPTANCE_EXE=str(executable))
            for pid in owned - {process.pid}:
                command = (f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
                           "if ($p -and $p.ExecutablePath -eq $env:JARVIS_ACCEPTANCE_EXE) { Stop-Process -Id $p.ProcessId }")
                subprocess.run([powershell, '-NoProfile', '-Command', command], env=cleanup_env, check=True)
        (temporary / 'acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    # Explicitly identify only this copied executable and its own workers.
    query = "Get-CimInstance Win32_Process -Filter \"Name='JARVIS.exe'\" | Select-Object -ExpandProperty ExecutablePath"
    existing = subprocess.check_output(['powershell', '-NoProfile', '-Command', query], text=True, encoding='utf-8')
    assert str(executable).casefold() not in existing.casefold()
    report['zero_owned_processes_after_shutdown'] = True
    if args.install:
        installer = source / INSTALLER_FILENAME
        installed = temporary / 'installed'
        command = [installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART',
                   '/TestInstance=1', '/TestProfile=' + str(profile), '/DIR=' + str(installed)]
        settings = profile / 'data/settings.json'
        marker = profile / 'documents/retain-neutral.txt'
        marker.write_text('Dados neutros do perfil temporario, preservar.', encoding='utf-8')
        watched = [settings, marker, profile / 'data/permissions.json', profile / 'data/jarvis.db']
        before = {str(p.relative_to(profile)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
        assert run(command).returncode == 0
        assert (installed / 'JARVIS.exe').exists()
        assert run(command).returncode == 0
        assert before == {str(p.relative_to(profile)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
        report['install_reinstall_data_preserved'] = True
        assert run([installed / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']).returncode == 0
        assert not (installed / 'JARVIS.exe').exists()
        assert before == {str(p.relative_to(profile)): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
        report['uninstall_data_preserved'] = True
    report['status'] = 'passed'
    (temporary / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Acceptance evidence:', temporary / 'acceptance.json', flush=True)


if __name__ == '__main__':
    main()
