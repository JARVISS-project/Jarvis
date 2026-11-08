"""Owned subprocess, real loopback/DPAPI/mutex/lifecycle; no audio or model."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
from uuid import uuid4

from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateClient, StateEndpoint
from jarvis.application.text_interaction import TextEventBus
from jarvis.runtime.health import RuntimeHealthStore


CHILD = '''
from pathlib import Path
import sys
from jarvis import __main__ as main
from jarvis.config import JarvisConfig
from jarvis.runtime import cli
from tests.integration.test_administration_23bh import build
root = Path(sys.argv[1])
profile = root / 'profile'
profile.mkdir(exist_ok=True)
config = JarvisConfig(settings_path=profile / 'settings.json')
config.tts_enabled = False
config.ambient_ui_endpoint_path = root / 'endpoint.json'
main._start_desktop_kill_switch = lambda config: (None, None)
main.build_harness = lambda *args, **kwargs: build(profile)[0]
main.build_voice_pipeline = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('voice'))
cli._runtime_paths = lambda: (root / 'instance.json', root / 'status.json', root / 'runtime.log')
cli.launch_ambient_ui = lambda *args, **kwargs: object()
cli.stop_spawned_ambient_ui = lambda process: None
cli._run_managed_runtime(cli.argument_parser().parse_args(['--ui']), config)
'''


def test_confirmed_stop_releases_instance_and_restart_rejects_old_confirmation(tmp_path):
    # The real guard is global on Windows; use an isolated name in owned children.
    child_source = CHILD.replace("cli._runtime_paths =", "import hashlib\nfrom jarvis.runtime.instance import SingleInstanceGuard\n"
        "cli.SingleInstanceGuard = lambda path: SingleInstanceGuard(path, mutex_name='Local\\\\JARVIS.23BX.' + hashlib.sha256(str(root).encode()).hexdigest())\n"
        "cli._runtime_paths =")
    environment = {**os.environ, 'PYTHONPATH': str(Path.cwd() / 'src'), 'PYTHONUTF8': '1'}
    old_token, previous_instance = None, None
    for iteration in range(2):
        process = subprocess.Popen([sys.executable, '-c', child_source, str(tmp_path)],
                                   env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        client = None
        try:
            deadline = time.monotonic() + 8
            health = RuntimeHealthStore(tmp_path / 'status.json')
            while time.monotonic() < deadline:
                current = health.read()
                # Windows venv python.exe may redirect to another owned PID.
                # The isolated metadata path and fresh instance ID bind this run.
                if (current and current.state == 'RUNNING' and current.instance_id != previous_instance
                        and (tmp_path / 'endpoint.json').exists()):
                    break
                assert process.poll() is None, process.communicate()[1].decode(errors='replace')
                time.sleep(.01)
            else:
                raise AssertionError('Owned runtime did not start')
            assert current.instance_id != previous_instance
            previous_instance = current.instance_id
            if iteration == 0:
                second = subprocess.run([sys.executable, '-c', child_source, str(tmp_path)],
                    env=environment, capture_output=True, timeout=8)
                assert second.returncode != 0
                assert health.read().instance_id == previous_instance
            bus, ready, completed, replies = TextEventBus(), Event(), Event(), []
            def receive(event):
                if event.get('status') == 'ready':
                    ready.set()
                if event.get('type') == 'admin':
                    replies.append(event)
                    completed.set()
            unsubscribe = bus.subscribe(receive)
            client = LocalStateClient(AgentStateBus(), StateEndpoint.read(tmp_path / 'endpoint.json'), text_bus=bus)
            client.start()
            assert ready.wait(3)
            def request(method, arguments):
                completed.clear()
                # Read the live ready event from the bus rather than guessing generation.
                client.send_command(dict(command='admin_request', generation=generations[-1],
                    interaction=uuid4().hex, method=method, arguments=arguments))
                assert completed.wait(3)
                return replies[-1]
            generations = []
            unsub_ready = bus.subscribe(lambda event: generations.append(event['generation'])
                if event.get('status') == 'ready' else None)
            assert generations
            if old_token is not None:
                assert request('runtime.stop', {'token': old_token})['status'] == 'error'
            prepared = request('runtime.prepare_stop', {})
            assert prepared['status'] == 'done'
            old_token = prepared['data']['token']
            assert process.poll() is None
            assert request('runtime.stop', {'token': old_token})['data'] == {'requested': True}
            assert process.wait(timeout=8) == 0
            assert health.read().state == 'STOPPED'
            assert not (tmp_path / 'instance.json').exists()
            assert not (tmp_path / 'endpoint.json').exists()
            assert not health.stop_path.exists()
            unsub_ready()
            unsubscribe()
        finally:
            if client is not None:
                client.close()
            if process.poll() is None:
                current = health.read()
                if current is not None:
                    health.request_stop(current.instance_id)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
            if process.poll() is None:
                # Only this test's own child, never the user's runtime.
                process.terminate()
                process.wait(timeout=5)
            stdout, stderr = process.communicate()
            (tmp_path / ('child-' + str(iteration) + '-stderr.log')).write_bytes(stderr)
