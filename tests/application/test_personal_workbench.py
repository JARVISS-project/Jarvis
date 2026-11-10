from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jarvis.application.personal import DesktopPreferences, PersonalOrganizer, PREFERENCES


def services(tmp_path):
    admin = SimpleNamespace(config=SimpleNamespace(settings_path=tmp_path / 'settings.json'),
                            permissions=SimpleNamespace(protected_paths=set()))
    return DesktopPreferences(admin), PersonalOrganizer(admin, lambda: datetime(2026, 11, 10, tzinfo=timezone.utc)), admin


def test_preferences_round_trip_protected_and_stale_revision_rejected(tmp_path):
    desktop, _, admin = services(tmp_path)
    initial = desktop.inventory()
    value = deepcopy(initial['preferences'])
    value.update(theme='light', density='compact', calendar_period='upcoming', calendar_view='table')
    value['shortcuts']['commands'] = 'Alt+K'
    result = desktop.save(value, initial['revision'])
    assert DesktopPreferences(admin).inventory() == result
    assert b'shortcuts' not in desktop.store.path.read_bytes()
    assert desktop.store.path.resolve() in admin.permissions.protected_paths
    with pytest.raises(PermissionError):
        desktop.save(initial['preferences'], initial['revision'])


@pytest.mark.parametrize('key', ['Ctrl+C', 'Ctrl+W', 'Alt+F4', 'Ctrl+Ctrl+K', 'Shift+Ctrl+K', 'K', 'Esc', 'Ctrl+1'])
def test_reserved_ambiguous_or_duplicate_shortcuts_never_write(tmp_path, key):
    desktop, _, _ = services(tmp_path)
    state = desktop.inventory()
    value = deepcopy(state['preferences'])
    value['shortcuts']['commands'] = key
    with pytest.raises(ValueError):
        desktop.save(value, state['revision'])
    assert not desktop.store.path.exists()


def test_personal_create_edit_complete_delete_restore_and_restart(tmp_path):
    _, organizer, admin = services(tmp_path)
    initial = organizer.inventory()
    created = organizer.save(None, 'Comprar material', 'task', None, 'high', initial['revision'])
    row = created['rows'][0]
    assert b'Comprar material' not in organizer.store.path.read_bytes()
    assert organizer.store.path.resolve() in admin.permissions.protected_paths
    edited = organizer.save(row['id'], 'Ligar para a oficina', 'reminder', '2026-11-11T10:00:00-03:00', 'normal', created['revision'])
    assert edited['rows'][0]['due'] == '2026-11-11T13:00:00+00:00'
    done = organizer.change(row['id'], 'done', edited['revision'])
    assert done['rows'][0]['done']
    removed = organizer.change(row['id'], 'delete', done['revision'])
    assert not removed['rows']
    restored = organizer.change(row['id'], 'restore', removed['revision'])
    assert restored['rows'][0]['done']
    assert PersonalOrganizer(admin).inventory()['rows'] == restored['rows']
    with pytest.raises(PermissionError):
        organizer.change(row['id'], 'delete', edited['revision'])
    assert organizer.inventory()['rows'] == restored['rows']


@pytest.mark.parametrize('title,kind,due', [('', 'task', None), ('\nComando', 'task', None),
    ('Lembrar', 'reminder', None), ('Lembrar', 'reminder', '2026-11-11T12:00:00'), ('a'*201, 'task', None)])
def test_invalid_personal_items_do_not_write(tmp_path, title, kind, due):
    _, organizer, _ = services(tmp_path)
    with pytest.raises(ValueError):
        organizer.save(None, title, kind, due, 'normal', organizer.inventory()['revision'])
    assert not organizer.store.path.exists()


def test_corrupted_state_not_overwritten(tmp_path):
    desktop, organizer, _ = services(tmp_path)
    for service in (desktop, organizer):
        service.store.path.write_bytes(b'not valid protected state')
        with pytest.raises(PermissionError):
            service.inventory()
        assert service.store.path.read_bytes() == b'not valid protected state'
