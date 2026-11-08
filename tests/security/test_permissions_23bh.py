import json
import os
from pathlib import Path

import pytest

from jarvis.security.permissions import PermissionStore
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.audit import AuditLogger
from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.create_file import CreateFileTool
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.move_file import MoveFileTool
from jarvis.tools.delete_file import DeleteFileTool


class Confirm(ConfirmationProvider):
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = 0

    def confirm(self, action, target, risk):
        self.calls += 1
        return self.allowed


def system(tmp_path):
    permissions = PermissionStore(tmp_path / 'admin' / 'permissions.json', (tmp_path,))
    guard = FileSystemGuard(tmp_path, permissions)
    confirmation = Confirm()
    executor = ToolExecutor(SafetyManager(confirmation), AuditLogger(tmp_path / 'audit.log'),
        CapabilityPolicy({Capability.FILESYSTEM_READ: True, Capability.FILESYSTEM_WRITE: True}), permissions=permissions)
    return permissions, guard, executor, confirmation


def rule(store, resource, mode='block', action='use', expires=None):
    return store.set_rule(resource, action, mode, expected_revision=store.read()[1], expires=expires)


@pytest.mark.parametrize('mode', ['block', 'ask', 'allow'])
def test_persistence_restart_and_origin(tmp_path, mode):
    p, _, _, _ = system(tmp_path)
    revision = rule(p, 'tool:read_file', mode)
    restarted = PermissionStore(p.path, (tmp_path,))
    assert restarted.decision('tool:read_file').mode == mode
    assert restarted.read()[1] == revision
    assert restarted.read()[0][0]['origin'] == 'user_admin'
    assert not list(p.path.parent.glob('.permissions-*.tmp'))


@pytest.mark.parametrize('bad', [b'', b'{', b'[]', b'{"schema":2,"rules":[]}',
    b'{"schema":true,"rules":[]}', b'{"schema":1,"schema":1,"rules":[]}',
    b'{"schema":1,"rules":[{"mode":"allow"}]}', b'{"schema":1,"rules":[],"grant_all":true}'])
def test_invalid_configuration_fail_closed(tmp_path, bad):
    p, guard, executor, _ = system(tmp_path)
    p.path.parent.mkdir()
    p.path.write_bytes(bad)
    assert not p.read()[2]
    assert executor.execute_with_result(CreateFileTool(guard), path='file.txt', content='private').status == 'security_blocked'
    assert not (tmp_path / 'file.txt').exists()
    with pytest.raises(PermissionError):
        rule(p, 'tool:read_file', 'allow')


def test_conflicting_deny_precedes_allow_parent_and_child(tmp_path):
    p, guard, executor, _ = system(tmp_path)
    target = tmp_path / 'protected'
    target.mkdir()
    (target / 'a.txt').write_text('private')
    rule(p, f'path:{target}', action='read')
    rule(p, f'path:{target / "a.txt"}', 'allow', 'read')
    assert executor.execute_with_result(ReadFileTool(guard), path='protected/a.txt').status == 'security_blocked'
    rules = p.read()[0]
    rules.append({**rules[0], 'mode': 'allow'})
    p.path.write_text(json.dumps({'schema': 1, 'rules': rules}))
    assert p.path_decision(target / 'a.txt', 'read') == 'block'


def test_scope_expansion_and_stale_write_are_rejected(tmp_path):
    p, _, _, _ = system(tmp_path)
    old = p.read()[1]
    rule(p, 'tool:read_file')
    with pytest.raises(PermissionError):
        p.set_rule('tool:read_file', 'use', 'allow', expected_revision=old)
    for resource, action in [('path:' + str(tmp_path.parent), 'read'), ('tool:shell', 'use'), ('*', 'use')]:
        with pytest.raises(ValueError):
            rule(p, resource, 'allow', action)


def test_allow_never_overrides_capability_and_confirmation(tmp_path):
    p, guard, executor, confirm = system(tmp_path)
    rule(p, 'tool:create_file', 'allow')
    confirm.allowed = False
    assert executor.execute_with_result(CreateFileTool(guard), path='one.txt', content='private').status == 'denied'
    assert confirm.calls == 1
    executor.capability_policy = CapabilityPolicy({Capability.FILESYSTEM_WRITE: False})
    assert executor.execute_with_result(CreateFileTool(guard), path='one.txt', content='private').status == 'security_blocked'
    assert not (tmp_path / 'one.txt').exists()


def test_ask_read_denied_and_exactly_one_confirmed_execution(tmp_path):
    p, guard, executor, confirm = system(tmp_path)
    (tmp_path / 'file.txt').write_text('literal private')
    rule(p, 'tool:read_file', 'ask')
    confirm.allowed = False
    assert executor.execute_with_result(ReadFileTool(guard), path='file.txt').status == 'security_blocked'
    confirm.allowed = True
    result = executor.execute_with_result(ReadFileTool(guard), path='file.txt')
    assert result.status == 'success' and result.content == 'literal private'
    assert confirm.calls == 2


def test_revocation_between_prepare_and_effect(tmp_path):
    p, guard, executor, _ = system(tmp_path)
    class Revoke:
        def started(self, name):
            rule(p, 'tool:create_file')
        def finished(self, name, result):
            pass
    executor.observer = Revoke()
    result = executor.execute_with_result(CreateFileTool(guard), path='private-title.txt', content='private-value')
    assert result.status == 'security_blocked'
    assert not (tmp_path / 'private-title.txt').exists()
    audit = (tmp_path / 'audit.log').read_text()
    assert 'private-title' not in audit and 'private-value' not in audit and str(tmp_path) not in audit


def test_path_delete_move_destination_and_descendant_listing(tmp_path):
    p, guard, executor, _ = system(tmp_path)
    (tmp_path / 'a.txt').write_text('private')
    secret = tmp_path / 'protected'
    secret.mkdir()
    rule(p, f'path:{secret}', action='write')
    assert executor.execute_with_result(MoveFileTool(guard), source='a.txt', destination='protected/a.txt').status == 'security_blocked'
    rule(p, f'path:{tmp_path / "a.txt"}', action='delete')
    assert executor.execute_with_result(DeleteFileTool(guard), path='a.txt').status == 'security_blocked'
    assert executor.execute_with_result(MoveFileTool(guard), source='a.txt', destination='b.txt').status == 'security_blocked'
    assert (tmp_path / 'a.txt').exists()
    rule(p, f'path:{secret}', action='read')
    assert 'protected' not in executor.execute_with_result(ListFilesTool(guard)).content


@pytest.mark.parametrize('name', ['browser_navigate', 'desktop_open_application', 'local_application_open', 'vision_extract_text', 'safe_web_search', 'obsidian_open_note', 'current_media', 'local_media_control', 'local_volume_control'])
def test_indirect_routes_disabled_with_path_restrictions(tmp_path, name):
    p, _, _, _ = system(tmp_path)
    rule(p, f'path:{tmp_path / "protected"}', action='read')
    tool = type('Boundary', (), {'name': name})()
    with pytest.raises(PermissionError, match='indireta'):
        p.check_tool(tool, {})


def test_junction_or_symlink_and_hardlink(tmp_path):
    p, guard, executor, _ = system(tmp_path)
    protected = tmp_path / 'protected'
    protected.mkdir()
    source = protected / 'secret.txt'
    source.write_text('private')
    link = tmp_path / 'alias'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(protected), str(link))
    else:
        link.symlink_to(protected, target_is_directory=True)
    os.link(source, tmp_path / 'hard.txt')
    rule(p, f'path:{protected}', action='read')
    for path in ('alias/secret.txt', 'hard.txt'):
        assert executor.execute_with_result(ReadFileTool(guard), path=path).status == 'security_blocked'
    assert 'private' not in executor.execute_with_result(ListFilesTool(guard)).content


@pytest.mark.parametrize('name', ['permissions.json', 'settings.json', 'skills.json', 'jarvis.db', 'credentials_calendar_limited_write.bin'])
def test_llm_tools_cannot_edit_administrative_stores(tmp_path, name):
    _, guard, executor, _ = system(tmp_path)
    assert executor.execute_with_result(CreateFileTool(guard), path=name, content='malicious grant').status == 'security_blocked'
    assert not (tmp_path / name).exists()


def test_expiration_and_atomic_failure_preserve_previous_rule(tmp_path, monkeypatch):
    p, _, _, _ = system(tmp_path)
    rule(p, 'tool:read_file', 'block', expires='2000-01-01T00:00:00+00:00')
    assert p.decision('tool:read_file').mode == 'allow'
    before = p.path.read_bytes()
    def fail(*args):
        raise OSError('private path unavailable')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        rule(p, 'tool:read_file', 'block')
    assert p.path.read_bytes() == before and not list(p.path.parent.glob('.permissions-*'))


def test_administrative_hardlink_alias_blocked_without_path_restriction(tmp_path):
    p, guard, executor, _ = system(tmp_path)
    rule(p, 'tool:read_file', 'allow')
    os.link(p.path, tmp_path / 'innocent.json')
    assert executor.execute_with_result(ReadFileTool(guard), path='innocent.json').status == 'security_blocked'
    assert not p.has_path_restrictions()


def test_path_ask_content_and_internal_vault_writes_require_executor(tmp_path):
    from jarvis.integrations.obsidian.vault import ObsidianVault
    p, guard, executor, confirm = system(tmp_path)
    (tmp_path / 'note.md').write_text('private note')
    vault = ObsidianVault(tmp_path, guard)
    rule(p, f'path:{tmp_path}', 'ask', action='read')
    for call in (lambda: vault.read_note('note.md'), lambda: vault.list_notes(),
                 lambda: vault.search_notes('note'), lambda: ReadFileTool(guard).execute(path='note.md')):
        with pytest.raises(PermissionError, match='executor'):
            call()
    confirm.allowed = False
    assert executor.execute_with_result(ReadFileTool(guard), path='note.md').status == 'security_blocked'
    confirm.allowed = True
    assert executor.execute_with_result(ReadFileTool(guard), path='note.md').content == 'private note'
    rule(p, f'path:{tmp_path}', 'ask', action='write')
    with pytest.raises(PermissionError):
        vault.write_note('mirror.md', 'memory cannot authorize this')
    assert not (tmp_path / 'mirror.md').exists()


def test_confirmed_vault_search_cannot_gain_authority_from_blocked_list_helper(tmp_path):
    from jarvis.integrations.obsidian.vault import ObsidianVault
    from jarvis.tools.obsidian import ObsidianSearchNotesTool
    p, guard, executor, confirm = system(tmp_path)
    executor.capability_policy = CapabilityPolicy({Capability.OBSIDIAN_READ: True})
    vault = ObsidianVault(tmp_path, guard)
    (tmp_path / 'note.md').write_text('private note')
    rule(p, 'tool:obsidian_list_notes', 'block')
    rule(p, 'tool:obsidian_search_notes', 'ask')
    with pytest.raises(PermissionError):
        vault.search_notes('private')
    result = executor.execute_with_result(ObsidianSearchNotesTool(vault), query='private')
    assert result.status == 'success' and 'private note' in result.content
    assert confirm.calls == 1


def test_screen_permission_blocks_before_capture_boundary(tmp_path):
    p, _, _, _ = system(tmp_path)
    policy = CapabilityPolicy({Capability.VISION: True}, administrative=p)
    rule(p, 'capture.screen')
    with pytest.raises(PermissionError):
        policy.require(Capability.VISION)
