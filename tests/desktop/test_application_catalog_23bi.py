"""Real catalog/policy/executor; only discovery and OS effects are fake."""
from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from jarvis.desktop.application_catalog import ApplicationCatalog, DiscoveredApplication, executable_kind, file_identity, opaque_id
from jarvis.desktop.application_control import ApplicationControlService, ApplicationSettings, ApplicationChoiceRequired
from jarvis.desktop.application_windows import ApplicationWindow, MonitorArea, preferred_rectangle
from jarvis.desktop.application_discovery import WindowsApplicationDiscovery
from jarvis.security.permissions import PermissionStore, scoped_resource
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.audit import AuditLogger
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.application_catalog import application_catalog_tools
from tests.integration.test_administration_23bh import Confirm, NoLLM


def gui_binary(path, *, subsystem=2, dll=False):
    data = bytearray(256)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 64 + 22, 0x2000 if dll else 0x2)
    struct.pack_into('<H', data, 64 + 24, 0x10b)
    struct.pack_into('<H', data, 64 + 24 + 68, subsystem)
    path.write_bytes(data)
    return path


class Discovery:
    def __init__(self, entries):
        self.entries = entries

    def discover(self):
        return list(self.entries), []

    def revalidate(self, entry):
        return entry.fingerprint if entry in self.entries else ''

    def legacy_entries(self):
        return [entry for entry in self.entries if entry.legacy]


class Backend:
    def __init__(self):
        self.instances = []
        self.effects = []
        self.states = {}
        self.result = True
        self.launch_ready = True

    def windows(self, entry):
        return tuple(self.instances)

    def monitors(self):
        return [MonitorArea('primary', (-1920, 0, 0, 1040), True)]

    def launch(self, entry):
        self.effects.append(('open', entry.id))
        if self.launch_ready:
            self.instances = [ApplicationWindow(101, 202, 303, 404, 'class', 'PRIVATE TITLE')]

    def perform(self, action, instance, rectangle):
        self.effects.append((action, instance.identity()))
        if self.result:
            if action == 'close':
                self.instances.remove(instance)
            else:
                self.states[action] = rectangle

    def verified(self, action, instance, rectangle):
        return self.result and action in self.states and self.states[action] == rectangle


class Clock:
    time = 0.

    def now(self):
        return self.time

    def sleep(self, duration):
        self.time += duration


def system(tmp_path, legacy=None):
    entry = DiscoveredApplication(opaque_id('fake', 'stable'), 'PRIVATE APP', 'fake', 'stable', 'PRIVATE PATH', 'a' * 64, legacy=legacy)
    catalog = ApplicationCatalog(Discovery([entry]))
    permissions = PermissionStore(tmp_path / 'permissions.json', (tmp_path,))
    backend, clock = Backend(), Clock()
    service = ApplicationControlService(catalog, backend, permissions, ApplicationSettings(tmp_path / 'applications.json'), clock=clock.now, sleep=clock.sleep)
    permissions.application_control = service
    service.refresh()
    confirmation = Confirm()
    executor = ToolExecutor(SafetyManager(confirmation), AuditLogger(tmp_path / 'audit.log'), CapabilityPolicy({Capability.LOCAL_CONTROL: True}), permissions=permissions)
    tools = {tool.name: tool for tool in application_catalog_tools(service)}
    return entry, service, backend, permissions, executor, tools


def allow(service, entry, group, mode='allow'):
    service.change_permission(entry.id, entry.fingerprint, group, mode, service.permissions.read()[1])


def run(executor, tools, entry, action='open', **kwargs):
    group = action if action in {'open', 'status', 'close'} else 'window'
    return executor.execute(tools['app_catalog_' + group], application=entry.id, **({'action': action} if group == 'window' else {}), **kwargs)


@pytest.mark.parametrize('action', ['open', 'status', 'focus', 'minimize', 'maximize', 'restore', 'close'])
def test_discovery_never_authorizes(tmp_path, action):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class')]
    assert service.mode(entry, action).mode == 'block'
    result = run(executor, tools, entry, action)
    assert 'bloquead' in result.lower() and backend.effects == []


@pytest.mark.parametrize('mode', ['block', 'ask', 'allow'])
def test_scoped_permission_persists_and_remove_denies(tmp_path, mode):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open', mode)
    restarted = PermissionStore(permissions.path, (tmp_path,))
    assert restarted.decision(scoped_resource('app_catalog_open', 'application', entry.grant_key)).mode == mode
    service.change_permission(entry.id, entry.fingerprint, 'open', 'allow', permissions.read()[1], remove=True)
    assert service.mode(entry, 'open').mode == 'block'
    assert not list(tmp_path.glob('*.tmp'))


def test_open_exactly_once_reuses_existing_and_audit_private(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open')
    assert 'verificada' in run(executor, tools, entry)
    assert 'já aberto' in run(executor, tools, entry)
    assert backend.effects == [('open', entry.id)]
    audit = (tmp_path / 'audit.log').read_text()
    assert all(private not in audit for private in ['PRIVATE APP', 'PRIVATE PATH', 'PRIVATE TITLE'])
    with pytest.raises(PermissionError):
        service.execute(service.prepare(entry.id, 'open'))


@pytest.mark.parametrize('action', ['focus', 'minimize', 'maximize', 'restore', 'close'])
def test_multiple_windows_require_selection_and_one_effect(tmp_path, action):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'window' if action != 'close' else 'close')
    allow(service, entry, 'status')
    backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class'), ApplicationWindow(5, 6, 7, 8, 'class')]
    with pytest.raises(ApplicationChoiceRequired):
        service.prepare(entry.id, action)
    assert not backend.effects
    choices = json.loads(run(executor, tools, entry, 'status'))['windows']
    assert len(choices) == 2
    result = run(executor, tools, entry, action, reference=choices[1]['reference'])
    assert 'verificad' in result
    assert backend.effects == [(action, '5|6|7|8|class')]
    if action == 'close':
        assert len(backend.instances) == 1 and backend.instances[0].pid == 1


@pytest.mark.parametrize('mutation', ['birth', 'pid', 'hwnd', 'thread', 'class_name', 'restart', 'refresh', 'identity', 'revoke'])
def test_prepared_revalidates_every_binding(tmp_path, mutation):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'window')
    backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class')]
    prepared = service.prepare(entry.id, 'focus')
    if mutation in {'birth', 'pid', 'hwnd', 'thread'}:
        backend.instances = [replace(backend.instances[0], **{mutation: 99})]
    elif mutation == 'class_name':
        backend.instances = [replace(backend.instances[0], class_name='other')]
    elif mutation in {'restart', 'refresh'}:
        service.refresh()
    elif mutation == 'identity':
        service.catalog.discovery.entries = [replace(entry, fingerprint='b' * 64)]
        service.refresh()
    else:
        allow(service, entry, 'window', 'block')
    with pytest.raises((PermissionError, ValueError)):
        service.revalidate(prepared)
    assert backend.effects == []


@pytest.mark.parametrize('failure', ['launch', 'window', 'close'])
def test_unverified_result_explicit_failure_no_retry_or_force(tmp_path, failure):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    action = {'launch': 'open', 'window': 'focus', 'close': 'close'}[failure]
    allow(service, entry, 'open' if action == 'open' else 'close' if action == 'close' else 'window')
    backend.result = backend.launch_ready = False
    if action != 'open':
        backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class')]
    result = run(executor, tools, entry, action)
    assert 'não' in result and len(backend.effects) == 1


def test_legacy_concessions_preserved_without_new_window_authority(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path, 'spotify')
    assert service.mode(entry, 'open').mode == 'allow'
    assert service.mode(entry, 'focus').mode == 'allow'
    assert service.mode(entry, 'maximize').mode == 'block'
    service.change_permission(entry.id, entry.fingerprint, 'open', 'allow', permissions.read()[1], remove=True)
    assert service.mode(entry, 'open').mode == 'block'


def test_invalid_rules_and_global_block_override_allow(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open')
    permissions.set_rule('tool:app_catalog_open', 'use', 'block', expected_revision=permissions.read()[1])
    assert service.mode(entry, 'open').mode == 'block'
    permissions.path.write_text('{"schema":99,"rules":[]}', encoding='utf-8')
    assert service.mode(entry, 'open').mode == 'block'
    assert backend.effects == []


@pytest.mark.parametrize('extra', [{'path': 'evil.exe'}, {'arguments': '-x'}, {'application': 'evil.exe'}, {'reference': 'bad'}, {'action': 'close'}])
def test_malicious_or_extra_arguments_have_zero_effect(tmp_path, extra):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open')
    args = {'application': entry.id, **extra}
    result = executor.execute(tools['app_catalog_open'], **args)
    assert backend.effects == [] and result


def test_preferences_clamp_missing_monitor_alias_collision_and_binding(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    preference = {'monitor': 'disconnected', 'x': 100, 'y': 100, 'width': 80, 'height': 80}
    service.set_preferences(entry.id, entry.fingerprint, preference, ['my app'])
    assert service.catalog.match('my app', service.settings.read()['aliases']) == [entry]
    rectangle, fallback = preferred_rectangle(preference, backend.monitors())
    assert rectangle == (-1536, 208, 1536, 832) and fallback
    allow(service, entry, 'window')
    backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class')]
    prepared = service.prepare(entry.id, 'place')
    assert 'Monitor preferido indisponível' in run(executor, tools, entry, 'place')
    assert backend.states['place'] == rectangle
    service.set_preferences(entry.id, entry.fingerprint, {**preference, 'x': 0}, ['my app'])
    with pytest.raises(PermissionError):
        service.revalidate(prepared)
    other = replace(entry, id=opaque_id('fake', 'other'), name='other')
    service.catalog.discovery.entries.append(other)
    service.refresh()
    with pytest.raises(ValueError):
        service.set_preferences(other.id, other.fingerprint, preference, ['my app'])


@pytest.mark.parametrize('name,subsystem,dll,kind', [('app.exe', 2, False, 'application'), ('app.exe', 3, False, 'command_line'), ('app.exe', 2, True, 'unsupported'), ('setup.exe', 2, False, 'installer'), ('powershell.exe', 2, False, 'command_line'), ('app.cmd', 2, False, 'unsupported')])
def test_pe_classification(tmp_path, name, subsystem, dll, kind):
    assert executable_kind(gui_binary(tmp_path / name, subsystem=subsystem, dll=dll)) == kind


def test_shortcut_args_tamper_and_destination_identity(tmp_path, monkeypatch):
    discovery = WindowsApplicationDiscovery()
    target = gui_binary(tmp_path / 'app.exe')
    shortcut = tmp_path / 'app.lnk'
    shortcut.write_bytes(b'original')
    import jarvis.desktop.application_discovery as module
    monkeypatch.setattr(module, 'shell_link', lambda path: (str(target), ''))
    entry = discovery._win32('app', 'start_menu', str(shortcut), str(target))
    assert entry.available and discovery.revalidate(entry) == entry.fingerprint
    shortcut.write_bytes(b'tampered')
    assert discovery.revalidate(entry) != entry.fingerprint
    shortcut.write_bytes(b'original')
    monkeypatch.setattr(module, 'shell_link', lambda path: (str(target), '--script evil'))
    assert discovery.revalidate(entry) != entry.fingerprint
    assert not discovery._win32('app', 'start_menu', str(shortcut), str(target), '--script evil').available
    before = file_identity(target)
    target.write_bytes(target.read_bytes() + b'changed')
    assert file_identity(target) != before


@pytest.mark.parametrize('target', ['"C:\\app.exe"', 'C:\\app.exe --evil', '\\\\server\\app.exe', 'evil.cmd', 'C:\\app.exe\n'])
def test_unsafe_destination_unavailable(target):
    assert not WindowsApplicationDiscovery()._win32('app', 'app_paths', 'locator', target).available


def test_same_destination_explicit_deny_overrides_other_discovery_grant(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    other = replace(entry, id=opaque_id('fake', 'alias'), name='alternate discovery')
    service.catalog.discovery.entries.append(other)
    service.refresh()
    allow(service, other, 'open')
    assert service.mode(other, 'open').mode == 'allow'
    allow(service, entry, 'open', 'block')
    assert service.mode(other, 'open').mode == 'block'
    run(executor, tools, other)
    assert not backend.effects


def test_authorization_bound_to_changed_identity_and_capability(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open')
    new_entry = replace(entry, fingerprint='b' * 64)
    service.catalog.discovery.entries = [new_entry]
    service.refresh()
    assert service.mode(new_entry, 'open').mode == 'block'
    with pytest.raises(PermissionError):
        allow(service, entry, 'open')
    allow(service, new_entry, 'open')
    executor.capability_policy = CapabilityPolicy({Capability.LOCAL_CONTROL: False})
    run(executor, tools, new_entry)
    assert not backend.effects


def test_close_receipt_cannot_replay_or_execute_directly(tmp_path):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'close')
    backend.instances = [ApplicationWindow(1, 2, 3, 4, 'class')]
    tool = tools['app_catalog_close']
    prepared = tool.prepare(application=entry.id)
    with pytest.raises(PermissionError):
        tool.bind_authorized(prepared)
    with pytest.raises(PermissionError):
        tool.execute_authorized(prepared)
    with pytest.raises(PermissionError):
        tool.execute(application=entry.id)
    assert not backend.effects


def test_prepared_payload_change_and_revoke_in_preflight_zero_effect(tmp_path, monkeypatch):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open')
    tool = tools['app_catalog_open']
    prepared = tool.prepare(application=entry.id)
    with pytest.raises(PermissionError):
        tool.preflight_prepared(prepared, application=opaque_id('fake', 'other'))
    original = tool.preflight_prepared
    def revoke(preparation, **kwargs):
        original(preparation, **kwargs)
        allow(service, entry, 'open', 'block')
    monkeypatch.setattr(tool, 'preflight_prepared', revoke)
    run(executor, tools, entry)
    assert not backend.effects


@pytest.mark.parametrize('value', ['{"schema":1,"schema":1,"preferences":{},"aliases":{},"legacy_bindings":{}}', '{"schema":1,"preferences":{},"aliases":{"x":"C:\\\\evil.exe"},"legacy_bindings":{}}', '{"schema":1,"preferences":{},"aliases":{},"legacy_bindings":{"spotify":"bad"}}'])
def test_settings_invalid_fail_closed(tmp_path, value):
    entry, service, backend, permissions, executor, tools = system(tmp_path)
    allow(service, entry, 'open')
    service.settings.path.write_text(value, encoding='utf-8')
    with pytest.raises(ValueError):
        service.prepare(entry.id, 'open')
    assert not backend.effects


def test_native_identity_does_not_match_title_or_basename(tmp_path):
    from jarvis.desktop.application_windows import WindowsApplicationBackend
    from jarvis.desktop.models import DesktopWindow
    from types import SimpleNamespace
    executable = gui_binary(tmp_path / 'app.exe')
    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    other = gui_binary(other_dir / 'app.exe')
    entry = WindowsApplicationDiscovery()._win32('app', 'legacy_allowlist', 'stable', str(executable))
    windows = [DesktopWindow(1, 'same title', 11, 'class'), DesktopWindow(2, 'same title', 22, 'class')]
    backend = WindowsApplicationBackend.__new__(WindowsApplicationBackend)
    backend.desktop = SimpleNamespace(list_windows=lambda: windows)
    backend.user = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd, pointer: 99)
    backend._process = lambda pid: (str(executable if pid == 11 else other), 123, '', '')
    observed = backend.windows(entry)
    assert len(observed) == 1 and observed[0].pid == 11
    package = replace(entry, kind='packaged', target='family!app', locator='package_v1')
    backend._process = lambda pid: (str(executable), 123, 'family!app', 'package_v1' if pid == 11 else 'package_v2')
    assert [window.pid for window in backend.windows(package)] == [11]


def test_native_window_style_and_dpi_cleanup_no_effect_when_unsupported():
    from jarvis.desktop.application_windows import WindowsApplicationBackend
    from types import SimpleNamespace
    calls = []
    backend = WindowsApplicationBackend.__new__(WindowsApplicationBackend)
    backend.user = SimpleNamespace(GetWindowLongPtrW=lambda hwnd, key: 0,
        ShowWindow=lambda *args: calls.append(('show', args)),
        SetThreadDpiAwarenessContext=lambda value: calls.append(('dpi', value)) or 17)
    window = ApplicationWindow(1, 2, 3, 4, 'class')
    for action in ['minimize', 'maximize', 'place']:
        with pytest.raises(RuntimeError):
            backend.perform(action, window, (0, 0, 100, 100))
    assert not calls
    with pytest.raises(RuntimeError):
        with backend._dpi_coordinates():
            raise RuntimeError('test failure')
    assert len(calls) == 2 and calls[-1] == ('dpi', 17)


@pytest.mark.parametrize('hresult', [0, 1, -2147417850])
def test_com_apartment_balanced_or_fail_closed(monkeypatch, hresult):
    from types import SimpleNamespace
    import jarvis.desktop.application_discovery as module
    calls = []
    initialize = lambda *args: hresult
    ole = SimpleNamespace(CoInitializeEx=initialize, CoUninitialize=lambda: calls.append('uninitialize'))
    monkeypatch.setattr(module.ctypes, 'OleDLL', lambda name: ole)
    if hresult < 0:
        with pytest.raises(RuntimeError):
            with module.com_apartment():
                pytest.fail('incompatible apartment cannot execute')
        assert calls == []
    else:
        with pytest.raises(ValueError):
            with module.com_apartment():
                raise ValueError('failure inside apartment')
        assert calls == ['uninitialize']
