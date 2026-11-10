"""Private personal organization and desktop preferences, human UI only."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4

from jarvis.application.profile_state import ProtectedProfileState


SHORTCUTS = {'commands': 'Ctrl+K', 'home': 'Ctrl+1', 'calendar': 'Ctrl+2',
    'conversation': 'Ctrl+3', 'contacts': 'Ctrl+4', 'automations': 'Ctrl+5',
    'find': 'Ctrl+F', 'new_event': 'Ctrl+N', 'quick_task': 'Ctrl+Shift+N'}
PREFERENCES = {'theme': 'dark', 'density': 'comfortable', 'shortcuts': SHORTCUTS,
    'calendar_period': 'today', 'calendar_view': 'schedule', 'window_size': [1180, 760],
    'home_split': [580, 320]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class DesktopPreferences:
    def __init__(self, admin):
        self.store = ProtectedProfileState(admin.config.settings_path.parent / 'desktop-preferences.bin')
        admin.permissions.protected_paths.add(self.store.path.resolve())

    def inventory(self):
        value = self.store.read(PREFERENCES)
        self.validate(value)
        return {'preferences': value, 'revision': digest(value)}

    @staticmethod
    def validate(value):
        if not isinstance(value, dict) or set(value) != set(PREFERENCES):
            raise ValueError('Preferências inválidas.')
        if value['theme'] not in {'dark', 'light', 'system'} or value['density'] not in {'comfortable', 'compact'}:
            raise ValueError('Aparência inválida.')
        if value['calendar_period'] not in {'today', 'tomorrow', 'upcoming'} or value['calendar_view'] not in {'schedule', 'table'}:
            raise ValueError('Visualização inválida.')
        if not isinstance(value['shortcuts'], dict) or set(value['shortcuts']) != set(SHORTCUTS):
            raise ValueError('Atalhos inválidos.')
        used = set()
        reserved = {'Ctrl+C', 'Ctrl+V', 'Ctrl+X', 'Ctrl+A', 'Ctrl+Z', 'Ctrl+Y', 'Ctrl+Q', 'Ctrl+W', 'Alt+F4'}
        for sequence in value['shortcuts'].values():
            if not isinstance(sequence, str) or (sequence and not re.fullmatch(r'(?:(?:Ctrl|Alt|Shift)\+){1,3}(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))', sequence)):
                raise ValueError('Use uma combinação como Ctrl+K.')
            if sequence:
                modifiers = sequence.split('+')[:-1]
                if modifiers != [key for key in ('Ctrl', 'Alt', 'Shift') if key in modifiers]:
                    raise ValueError('Use modificadores únicos, na ordem Ctrl, Alt, Shift.')
            if sequence in reserved or sequence and sequence in used:
                raise ValueError('Atalho reservado ou repetido.')
            if sequence:
                used.add(sequence)
        for key, low, high in [('window_size', 620, 2600), ('home_split', 100, 4000)]:
            sizes = value[key]
            if not isinstance(sizes, list) or len(sizes) != 2 or any(type(n) is not int or not low <= n <= high for n in sizes):
                raise ValueError('Dimensões inválidas.')
        if value['window_size'][0] < 980:
            raise ValueError('A janela precisa ter ao menos 980 pixels de largura.')

    def save(self, preferences, revision):
        current = self.inventory()
        if current['revision'] != revision:
            raise PermissionError('Preferências mudaram; recarregue.')
        self.validate(preferences)
        self.store.write(deepcopy(preferences))
        return self.inventory()


class PersonalOrganizer:
    def __init__(self, admin, clock=None):
        self.store = ProtectedProfileState(admin.config.settings_path.parent / 'personal-organizer.bin')
        admin.permissions.protected_paths.add(self.store.path.resolve())
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def inventory(self, query=''):
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('Busca inválida.')
        items = self.store.read([])
        if not isinstance(items, list) or len(items) > 200:
            raise PermissionError('Organização pessoal indisponível.')
        for item in items:
            if (not isinstance(item, dict) or set(item) != {'id', 'title', 'kind', 'due', 'priority', 'done', 'deleted', 'updated'}
                    or not isinstance(item['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', item['id'])
                    or type(item['done']) is not bool or type(item['deleted']) is not bool):
                raise PermissionError('Item pessoal inválido.')
            self.validate(item['title'], item['kind'], item['due'], item['priority'])
        rows = [row for row in items if not row['deleted'] and query.casefold() in row['title'].casefold()]
        rows.sort(key=lambda row: (row['done'], row['due'] or '9999', row['priority'] != 'high', row['updated']))
        return {'rows': deepcopy(rows), 'revision': digest(items), 'now': self.clock().isoformat()}

    @staticmethod
    def validate(title, kind, due, priority):
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or any(ord(c) < 32 for c in title):
            raise ValueError('Escreva uma tarefa ou lembrete de até 200 caracteres.')
        if kind not in {'task', 'reminder'} or priority not in {'normal', 'high'}:
            raise ValueError('Tipo ou prioridade inválida.')
        if due is not None and (not isinstance(due, str) or datetime.fromisoformat(due).tzinfo is None):
            raise ValueError('Data precisa incluir fuso horário.')
        if kind == 'reminder' and due is None:
            raise ValueError('Escolha quando lembrar.')

    def save(self, id, title, kind, due, priority, revision):
        current = self.inventory()
        if current['revision'] != revision:
            raise PermissionError('A lista mudou; recarregue antes de salvar.')
        self.validate(title, kind, due, priority)
        items = self.store.read([])
        old = next((row for row in items if row['id'] == id and not row['deleted']), None)
        if id is not None and old is None:
            raise PermissionError('Item indisponível.')
        if id is None and len(items) >= 200:
            tombstone = next((item for item in items if item['deleted']), None)
            if tombstone is None:
                raise ValueError('Limite de 200 itens atingido.')
            items.remove(tombstone)
        if due is not None:
            due = datetime.fromisoformat(due).astimezone(timezone.utc).isoformat()
        row = {'id': id or uuid4().hex, 'title': title.strip(), 'kind': kind, 'due': due, 'priority': priority,
               'done': old['done'] if old else False, 'deleted': False, 'updated': self.clock().isoformat()}
        self.store.write([item for item in items if item['id'] != id] + [row])
        return self.inventory()

    def change(self, id, action, revision):
        if self.inventory()['revision'] != revision or action not in {'done', 'reopen', 'delete', 'restore'}:
            raise PermissionError('Item ou lista mudou; recarregue.')
        items = self.store.read([])
        row = next((item for item in items if item['id'] == id), None)
        if row is None or row['deleted'] and action != 'restore':
            raise PermissionError('Item indisponível.')
        if action in {'done', 'reopen'}:
            row['done'] = action == 'done'
        else:
            row['deleted'] = action == 'delete'
        row['updated'] = self.clock().isoformat()
        self.store.write(items)
        return self.inventory()
