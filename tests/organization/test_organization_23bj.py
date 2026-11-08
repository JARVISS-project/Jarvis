import json
from pathlib import Path

import pytest

from jarvis.organization.service import OrganizationService, default_rules, valid_name, validate_rules
from jarvis.organization.windows import WindowsMoves
from jarvis.security.permissions import PermissionStore
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.audit import AuditLogger
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.organization import OrganizationExecuteTool, OrganizationUndoTool
from tests.integration.test_administration_23bh import Confirm


def system(tmp_path):
    root = tmp_path / 'exclusive-files'
    root.mkdir()
    permissions = PermissionStore(tmp_path / 'admin' / 'permissions.json', (tmp_path,))
    service = OrganizationService(tmp_path / 'private' / 'organization', permissions)
    selected = service.select(str(root))['selected']
    executor = ToolExecutor(SafetyManager(Confirm()), AuditLogger(tmp_path / 'audit.log'),
        CapabilityPolicy({Capability.FILESYSTEM_WRITE: True}), permissions=permissions)
    return root, selected, service, permissions, executor


def grant(service, selected, action='organization_execute', mode='allow'):
    service.grant(selected, action, mode, service.permissions.read()[1])


def test_preview_no_move_native_execute_content_preserved_replay_undo(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'report.pdf').write_bytes(b'original bytes')
    (root / 'unknown.foo').write_bytes(b'keep')
    grant(service, selected)
    grant(service, selected, 'organization_undo')
    plan = service.analyze(selected)
    assert len(plan['operations']) == 1 and len(plan['ignored']) == 1
    assert (root / 'report.pdf').read_bytes() == b'original bytes' and not (root / 'Documentos').exists()
    result = executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert json.loads(result)['completed'] == 1, result
    assert (root / 'Documentos/report.pdf').read_bytes() == b'original bytes' and not (root / 'report.pdf').exists()
    assert json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))['completed'] == 1
    assert service.index(selected, 'report')['rows'][0]['current'] == 'Documentos/report.pdf'
    result = executor.execute(OrganizationUndoTool(service), plan=plan['id'])
    assert json.loads(result)['undone'] == 1, result
    assert (root / 'report.pdf').read_bytes() == b'original bytes'
    assert (root / 'Documentos').exists()  # never remove user/preexisting directories
    assert all(private not in (tmp_path / 'audit.log').read_text() for private in ['report.pdf', 'exclusive-files', 'original bytes'])


@pytest.mark.parametrize('mode', ['block', 'ask', 'allow'])
def test_persistence_action_scope_and_direct_denial(tmp_path, mode):
    root, selected, service, permissions, executor = system(tmp_path)
    grant(service, selected, mode=mode)
    restarted = OrganizationService(service.store.directory, PermissionStore(permissions.path, (tmp_path,)))
    assert restarted.decision('organization_execute', selected).mode == mode
    assert restarted.decision('organization_undo', selected).mode == 'ask'
    (root / 'a.txt').write_text('private', encoding='utf-8')
    plan = service.analyze(selected)
    with pytest.raises(PermissionError):
        OrganizationExecuteTool(service).execute(plan=plan['id'])
    result = executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    if mode == 'block':
        assert (root / 'a.txt').exists() and not (root / 'Documentos').exists()
    else:
        assert json.loads(result)['completed'] == 1, result


@pytest.mark.parametrize('name', ['CON.txt', 'LPT1.pdf', 'aux', 'a:b.pdf', 'a?.pdf', 'trail. ', '..', 'x' * 201, 'a\nb.txt'])
def test_windows_names_rejected(name):
    with pytest.raises(ValueError):
        valid_name(name)


def test_collision_suffix_date_template_and_extension(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.JPG').write_bytes(b'photo')
    rules = {**default_rules(), 'date_group': True, 'rename': '{name}_{date}_{n}', 'collision': 'suffix'}
    service.rules(selected, rules)
    grant(service, selected)
    plan = service.analyze(selected)
    destination = root / plan['operations'][0]['destination']
    assert destination.suffix == '.JPG' and 'Imagens/' in destination.as_posix()
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b'existing')
    plan = service.analyze(selected)
    assert '(1).JPG' in plan['operations'][0]['destination']
    result = executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert json.loads(result)['completed'] == 1, result
    assert destination.read_bytes() == b'existing'


def test_changed_file_stale_plan_zero_effect(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'old')
    grant(service, selected)
    plan = service.analyze(selected)
    (root / 'a.txt').write_bytes(b'new')
    result = executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert 'desatualizado' in result and (root / 'a.txt').read_bytes() == b'new'
    assert not (root / 'Documentos').exists()


def test_undo_changed_or_original_occupied_never_overwrites(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'old')
    grant(service, selected)
    grant(service, selected, 'organization_undo')
    plan = service.analyze(selected)
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    (root / 'a.txt').write_bytes(b'new occupant')
    result = executor.execute(OrganizationUndoTool(service), plan=plan['id'])
    assert json.loads(result)['undone'] == 0
    assert (root / 'a.txt').read_bytes() == b'new occupant' and (root / 'Documentos/a.txt').read_bytes() == b'old'


def test_hardlink_and_protected_file_not_indexed(tmp_path):
    import os
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    os.link(root / 'a.txt', root / 'b.txt')
    (root / 'credentials.txt').write_bytes(b'secret')
    plan = service.analyze(selected)
    assert not plan['operations'] and len(plan['ignored']) == 3
    assert not service.index(selected)['rows']


def test_private_journal_tamper_is_not_authority(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    plan = service.analyze(selected)
    path = service.store.directory / ('plan-' + plan['id'] + '.json')
    value = json.loads(path.read_text(encoding='utf-8'))
    value['payload']['operations'][0]['destination'] = '../evil.txt'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(PermissionError):
        service.load(plan['id'])
    assert (root / 'a.txt').exists()


@pytest.mark.parametrize('action', ['revoke', 'cancel'])
def test_between_operations_revoke_cancel_and_replay(tmp_path, action):
    root, selected, service, permissions, executor = system(tmp_path)
    for name in ('a.txt', 'b.txt', 'c.txt'):
        (root / name).write_bytes(name.encode())
    grant(service, selected)
    grant(service, selected, 'organization_undo')
    plan = service.analyze(selected)
    service.after_operation = lambda result: grant(service, selected, mode='block') if action == 'revoke' else service.cancel()
    result = json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))
    assert result['completed'] == 1
    assert (root / 'b.txt').exists() and (root / 'c.txt').exists()
    assert result['cancelled'] == (action == 'cancel')
    if action == 'revoke':
        with pytest.raises(PermissionError):
            service.index(selected)
        grant(service, selected)
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert (root / 'b.txt').exists() and (root / 'c.txt').exists()
    service.after_operation = lambda _result: None
    assert json.loads(executor.execute(OrganizationUndoTool(service), plan=plan['id']))['undone'] == 1
    assert (root / 'a.txt').read_bytes() == b'a.txt'


@pytest.mark.parametrize('after_effect', [False, True])
def test_crash_restart_reconciles_without_repeating_moves(tmp_path, after_effect):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'preserved')
    grant(service, selected)
    grant(service, selected, 'organization_undo')
    plan = service.analyze(selected)
    native = service.backend.move
    def crash(root, source, destination, identity, check):
        if after_effect:
            native(root, source, destination, identity, check)
        else:
            check()
        raise SystemExit('simulated process termination')
    service.backend.move = crash
    with pytest.raises(SystemExit):
        executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert service.load(plan['id'])['operations'][0]['state'] == 'started'
    restarted = OrganizationService(service.store.directory, permissions)
    result = json.loads(executor.execute(OrganizationExecuteTool(restarted), plan=plan['id']))
    assert result['completed'] == int(after_effect)
    assert (root / ('Documentos/a.txt' if after_effect else 'a.txt')).read_bytes() == b'preserved'
    assert restarted.load(plan['id'])['operations'][0]['state'] == ('completed' if after_effect else 'failed')
    if after_effect:
        assert json.loads(executor.execute(OrganizationUndoTool(restarted), plan=plan['id']))['undone'] == 1


def test_collision_created_after_preview_and_changed_undo(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    grant(service, selected, 'organization_undo')
    plan = service.analyze(selected)
    (root / 'Documentos').mkdir()
    (root / 'Documentos/a.txt').write_bytes(b'occupant')
    result = json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))
    assert result['completed'] == 0
    assert (root / 'a.txt').read_bytes() == b'a' and (root / 'Documentos/a.txt').read_bytes() == b'occupant'
    (root / 'b.txt').write_bytes(b'b')
    plan = service.analyze(selected)
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    (root / 'Documentos/b.txt').write_bytes(b'changed')
    assert json.loads(executor.execute(OrganizationUndoTool(service), plan=plan['id']))['undone'] == 0
    assert not (root / 'b.txt').exists() and (root / 'Documentos/b.txt').read_bytes() == b'changed'


def test_file_in_use_is_ignored_and_handle_closed(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    path = root / 'a.txt'
    path.write_bytes(b'a')
    backend = service.backend
    handle = backend.kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    assert handle not in {None, -1, __import__('ctypes').c_void_p(-1).value}
    try:
        plan = service.analyze(selected)
        assert not plan['operations'] and len(plan['ignored']) == 1
    finally:
        backend.kernel.CloseHandle(handle)
    assert len(service.analyze(selected)['operations']) == 1


def test_recursive_explicit_scope_and_rules_identity(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'nested').mkdir()
    (root / 'nested/a.txt').write_bytes(b'a')
    assert not service.analyze(selected)['operations']
    service.rules(selected, {**default_rules(), 'recursive': True})
    grant(service, selected)
    plan = service.analyze(selected)
    assert plan['operations'][0]['source'] == 'nested/a.txt'
    result = json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))
    assert result['completed'] == 1 and (root / 'nested').exists()
    service.rules(selected, {**default_rules(), 'recursive': True, 'date_group': True})
    assert service.decision('organization_execute', selected).mode == 'ask'
    with pytest.raises(PermissionError):
        service.prepare(plan['id'])


def test_policy_revision_invalidates_preview_and_prepared_operation(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    plan = service.analyze(selected)
    grant(service, selected)
    with pytest.raises(PermissionError):
        service.prepare(plan['id'])
    plan = service.analyze(selected)
    prepared = service.prepare(plan['id'])
    service.cancel()
    with pytest.raises(PermissionError):
        service.revalidate(prepared)
    assert not (root / 'Documentos').exists()


def test_selected_root_path_policy_and_private_journal_guard(tmp_path):
    from jarvis.security.filesystem import FileSystemGuard
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    permissions.set_rule('path:' + str(root), 'write', 'block', expected_revision=permissions.read()[1])
    plan = service.analyze(selected)
    assert not plan['operations'] and (root / 'a.txt').exists()
    guard = FileSystemGuard(tmp_path, permissions)
    with pytest.raises(PermissionError):
        guard.resolve('private/organization/integrity.key')
    assert guard.resolve('exclusive-files/a.txt') == root / 'a.txt'
    with pytest.raises(PermissionError):
        service._guard(root, '../escape.txt')
    permissions.path.write_text('{invalid', encoding='utf-8')
    with pytest.raises(PermissionError):
        service.select(str(root))


@pytest.mark.parametrize('change', ['duplicate_extension', 'template', 'type'])
def test_rule_schema_rejects_expansion_and_invalid_names(change):
    rules = default_rules()
    if change == 'duplicate_extension':
        rules['extensions']['Imagens'].append('.txt')
    elif change == 'template':
        rules['rename'] = '../{name}'
    else:
        rules['recursive'] = 1
    with pytest.raises(ValueError):
        validate_rules(rules)


def test_junction_destination_and_recursive_escape_are_blocked(tmp_path):
    import _winapi
    root, selected, service, permissions, executor = system(tmp_path)
    outside = tmp_path / 'outside-exclusive'
    outside.mkdir()
    (outside / 'private.txt').write_bytes(b'outside')
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    plan = service.analyze(selected)
    _winapi.CreateJunction(str(outside), str(root / 'Documentos'))
    try:
        executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
        assert (root / 'a.txt').exists() and not (outside / 'a.txt').exists()
        service.rules(selected, {**default_rules(), 'recursive': True})
        assert not service.analyze(selected)['operations']
        with pytest.raises(PermissionError):
            service.select(str(root / 'Documentos'))
    finally:
        (root / 'Documentos').rmdir()  # remove only the exclusive test junction
    assert (outside / 'private.txt').read_bytes() == b'outside'


def test_mid_batch_mutation_failure_is_partial_and_never_retried(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    for name in ('a.txt', 'b.txt'):
        (root / name).write_bytes(b'original')
    grant(service, selected)
    plan = service.analyze(selected)
    service.after_operation = lambda _result: (root / 'b.txt').write_bytes(b'changed')
    result = json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))
    assert result['completed'] == 1 and result['operations'][1]['state'] == 'failed'
    assert (root / 'b.txt').read_bytes() == b'changed'
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert (root / 'b.txt').read_bytes() == b'changed'


def test_sha_detects_same_size_same_mtime_change(tmp_path):
    import os
    root, selected, service, permissions, executor = system(tmp_path)
    path = root / 'a.txt'
    path.write_bytes(b'old')
    grant(service, selected)
    plan = service.analyze(selected)
    timestamp = path.stat()
    path.write_bytes(b'new')
    os.utime(path, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    assert service.backend.inspect(path)['sha256'] != plan['operations'][0]['identity']['sha256']
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert path.read_bytes() == b'new' and not (root / 'Documentos').exists()


def test_operation_limits_and_names_case_collision(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    for number in range(129):
        (root / f'{number:03}.txt').write_bytes(b'a')
    plan = service.analyze(selected)
    assert len(plan['operations']) == 128 and len(plan['ignored']) == 1
    (root / 'Documentos').mkdir()
    (root / 'Documentos/000.TXT').write_bytes(b'existing')
    plan = service.analyze(selected)
    assert not any(row['source'] == '000.txt' for row in plan['operations'])
    assert (root / 'Documentos/000.TXT').read_bytes() == b'existing'


def test_no_replace_native_race_and_pinned_directory_cannot_change(tmp_path):
    import ctypes
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    (root / 'Documentos').mkdir()
    expected = service.backend.inspect(root / 'a.txt')
    def race():
        (root / 'Documentos/a.txt').write_bytes(b'raced')
        with pytest.raises(OSError):
            (root / 'Documentos').rename(root / 'replaced')
        # Opening the pinned directory for DELETE is refused.
        with pytest.raises(OSError):
            handle = service.backend._open(root / 'Documentos', 0x10000, 7)
            service.backend.kernel.CloseHandle(handle)
    with pytest.raises(OSError):
        service.backend.move(root, root / 'a.txt', root / 'Documentos/a.txt', expected, race)
    assert (root / 'a.txt').read_bytes() == b'a'
    assert (root / 'Documentos/a.txt').read_bytes() == b'raced'


def test_uncertain_verification_reconciles_only_by_identity(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    plan = service.analyze(selected)
    native = service.backend.move
    def lost_result(*args):
        native(*args)
        raise RuntimeError('response lost')
    service.backend.move = lost_result
    result = json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))
    assert result['completed'] == 1 and (root / 'Documentos/a.txt').read_bytes() == b'a'
    assert json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))['completed'] == 1


def test_missing_integrity_key_and_changed_root_fail_closed(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    plan = service.analyze(selected)
    root.rename(tmp_path / 'old-exclusive')
    root.mkdir()
    (root / 'a.txt').write_bytes(b'replacement')
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert (root / 'a.txt').read_bytes() == b'replacement' and not (root / 'Documentos').exists()
    (service.store.directory / 'integrity.key').unlink()
    with pytest.raises(PermissionError):
        OrganizationService(service.store.directory, permissions)


def test_reparse_mutation_during_native_move_denied_and_marker_cleaned(tmp_path):
    import ctypes
    import struct
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'Documentos').mkdir()
    substitute = ('\\??\\' + str(outside)).encode('utf-16-le')
    printable = str(outside).encode('utf-16-le')
    names = substitute + b'\x00\x00' + printable + b'\x00\x00'
    data = struct.pack('<LHHHHHH', 0xA0000003, 8 + len(names), 0, 0, len(substitute), len(substitute) + 2, len(printable)) + names
    k = service.backend.kernel
    k.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
    k.DeviceIoControl.restype = ctypes.c_int
    def mutate():
        handle = service.backend._open(root / 'Documentos', 0x40000000, 7)
        try:
            returned = ctypes.c_ulong()
            buffer = ctypes.create_string_buffer(data)
            assert not k.DeviceIoControl(handle, 0x900A4, buffer, len(data), None, 0, ctypes.byref(returned), None)
            assert ctypes.get_last_error() == 145  # ERROR_DIR_NOT_EMPTY
        finally:
            k.CloseHandle(handle)
    identity = service.backend.inspect(root / 'a.txt')
    service.backend.move(root, root / 'a.txt', root / 'Documentos/a.txt', identity, mutate)
    assert (root / 'Documentos/a.txt').read_bytes() == b'a' and not (outside / 'a.txt').exists()
    assert not list(root.rglob('.jarvis-*.tmp'))


def test_extra_arguments_and_disabled_capability_zero_effect(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    plan = service.analyze(selected)
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'], destination='../escape')
    assert not (root / 'Documentos').exists()
    executor.capability_policy = CapabilityPolicy({Capability.FILESYSTEM_WRITE: False})
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert (root / 'a.txt').exists() and not (root / 'Documentos').exists()


def test_failed_verification_remains_pending_without_blind_retry(tmp_path):
    root, selected, service, permissions, executor = system(tmp_path)
    (root / 'a.txt').write_bytes(b'a')
    grant(service, selected)
    plan = service.analyze(selected)
    native = service.backend.move
    def unverified(root, source, destination, identity, check):
        native(root, source, destination, identity, check)
        destination.write_bytes(b'changed after move')
        raise RuntimeError('lost verification')
    service.backend.move = unverified
    result = json.loads(executor.execute(OrganizationExecuteTool(service), plan=plan['id']))
    assert result['completed'] == 0 and result['operations'][0]['state'] == 'failed'
    assert 'revisão' in result['operations'][0]['error']
    executor.execute(OrganizationExecuteTool(service), plan=plan['id'])
    assert (root / 'Documentos/a.txt').read_bytes() == b'changed after move' and not (root / 'a.txt').exists()


def test_selected_outside_base_policy_survives_restart_without_tool_expansion(tmp_path):
    from jarvis.security.filesystem import FileSystemGuard
    root, selected, service, permissions, executor = system(tmp_path)
    unrelated = tmp_path / 'ordinary-base'
    unrelated.mkdir()
    permissions.roots = (unrelated,)
    selected = service.select(str(root))['selected']
    permissions.set_rule('path:' + str(root), 'write', 'block', expected_revision=permissions.read()[1])
    restarted_permissions = PermissionStore(permissions.path, (unrelated,))
    restarted = OrganizationService(service.store.directory, restarted_permissions)
    assert restarted_permissions.path_decision(root, 'write') == 'block'
    assert restarted.selected == selected
    with pytest.raises(PermissionError):
        FileSystemGuard(unrelated, restarted_permissions).resolve(str(root / 'a.txt'))
