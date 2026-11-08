"""Deterministic, private composition of executor-authorized read sources."""
from contextlib import closing
from datetime import datetime, time
import re
import sqlite3
import unicodedata
from zoneinfo import ZoneInfo

from jarvis.memory.models import MemoryProvenance, MemorySensitivity
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool


def normalize(value: str) -> str:
    value = unicodedata.normalize('NFKD', value.casefold())
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', ''.join(
        char for char in value if not unicodedata.combining(char))).split())


REQUESTS = {'resumo do dia', 'o que tenho para fazer hoje', 'me de a introducao'}


class LocalBriefingMemoryTool(Tool):
    name = 'local_briefing_memory'
    description = 'Lê somente tarefas/lembretes explícitos consentidos da memória local.'
    risk = RiskLevel.READ_ONLY
    parameters = {'type': 'object', 'properties': {}, 'additionalProperties': False}

    def __init__(self, store, timezone: str, clock=None):
        self.store = store
        self.timezone = ZoneInfo(timezone)
        self.clock = clock or (lambda: datetime.now(self.timezone))

    def get_target(self, **kwargs):
        return 'local_briefing:explicit_memory'

    def preflight(self, **kwargs):
        if kwargs:
            raise ValueError('Argumentos extras não permitidos.')

    def execute(self, **kwargs):
        self.preflight(**kwargs)
        today = self.clock().astimezone(self.timezone).date()
        sections = {'Tarefas': [], 'Lembretes': [], 'Próximos prazos': []}
        seen = set()
        stale = 0
        try:
            entries = self.store.get_all()
        except Exception:
            raise RuntimeError('Memória local indisponível; detalhes privados omitidos.') from None
        for entry in entries:
            category = normalize(entry.category)
            if (category not in {'tarefa', 'task', 'lembrete', 'reminder', 'prazo', 'deadline'}
                    or not entry.consent_given
                    or entry.provenance is not MemoryProvenance.USER_EXPLICIT
                    or entry.sensitivity is MemorySensitivity.SENSITIVE):
                continue
            if entry.is_expired():
                stale += 1
                continue
            content = ' '.join(entry.content.split())[:300]
            key = (category, content)
            if key in seen:
                continue
            seen.add(key)
            due = entry.metadata.get('due_date')
            try:
                due = datetime.strptime(due, '%Y-%m-%d').date() if due else None
            except (ValueError, TypeError):
                due = None
            label = 'Próximos prazos' if category in {'prazo', 'deadline'} else (
                'Tarefas' if category in {'tarefa', 'task'} else 'Lembretes')
            if label == 'Próximos prazos' and due is None:
                continue
            if due and due > today and label != 'Próximos prazos':
                label = 'Próximos prazos'
            suffix = f' (prazo {due:%d/%m/%Y})' if due else ' (sem prazo registrado)'
            if len(sections[label]) < 10:
                sections[label].append('- ' + content + suffix)
        lines = []
        for label, items in sections.items():
            lines.append(label + ':')
            lines.extend(items or ['Nenhum registro explícito consentido disponível.'])
        if stale:
            lines.append('Registros desatualizados/expirados omitidos.')
        return '\n'.join(lines)


class DailyBriefing:
    def __init__(self, config, clock=None):
        self.config = config
        self.timezone = ZoneInfo(config.briefing_timezone)
        self.clock = clock or (lambda: datetime.now(self.timezone))

    def _marker(self, reset=False):
        path = self.config.briefing_marker_path
        path.parent.mkdir(parents=True, exist_ok=True)
        now = self.clock().astimezone(self.timezone)
        key = now.date().isoformat() + '|' + self.config.briefing_timezone
        with closing(sqlite3.connect(path, timeout=1.)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS briefing_marker (id INTEGER PRIMARY KEY, day TEXT)')
            db.execute('BEGIN IMMEDIATE')
            if reset:
                db.execute('DELETE FROM briefing_marker')
                return True
            row = db.execute('SELECT day FROM briefing_marker WHERE id=1').fetchone()
            if row and row[0] == key:
                return False
            db.execute('INSERT OR REPLACE INTO briefing_marker VALUES (1, ?)', (key,))
        return True

    def auto_due(self):
        if not self.config.briefing_auto_enabled:
            return False
        now = self.clock().astimezone(self.timezone).time().replace(tzinfo=None)
        start = time.fromisoformat(self.config.briefing_quiet_start)
        end = time.fromisoformat(self.config.briefing_quiet_end)
        quiet = start <= now < end if start < end else now >= start or now < end
        if start != end and quiet:
            return False
        try:
            return self._marker()
        except (OSError, sqlite3.Error):
            return False  # Fail closed: no automatic delivery without durable dedup.

    def render(self, harness, response_mode='text'):
        now = self.clock().astimezone(self.timezone)
        greeting = 'Bom dia' if now.hour < 12 else ('Boa tarde' if now.hour < 18 else 'Boa noite')
        lines = [f'{greeting}. Hoje é {now:%d/%m/%Y} ({self.config.briefing_timezone}).']
        tool = harness.tool_registry.get('google_calendar_list_events')
        lines.append('Agenda:')
        if not self.config.google_calendar_enabled or tool is None:
            lines.append('Indisponível/desconectada; não posso afirmar que o dia está livre.')
        else:
            try:
                calendar_id = tool.resolve_calendar(self.config.briefing_calendar_alias)
                result = harness._execute_tool(tool, {'period': 'today', 'calendar_id': calendar_id})
                lines.append('\n'.join(dict.fromkeys(result.content.splitlines())) if result.status == 'success' else
                             'Indisponível/erro de acesso; nenhum compromisso confirmado.')
            except (PermissionError, AttributeError):
                lines.append('Calendário não autorizado para este briefing.')
        local = harness._execute_tool(harness._briefing_memory_tool, {})
        lines.append(local.content if local.status == 'success' else 'Memória local indisponível.')
        lines.append('Resultados delegados: fonte confiável não disponível.')
        # Keep empty-source notices in each section; deduplicate agenda lines only.
        # No external instructions are interpreted.
        text = '\n'.join(lines)[:6500]
        harness.last_briefing_text = text
        harness.last_response_private_calendar = True
        if response_mode == 'voice':
            agenda = lines[2].splitlines()
            task = next((line for line in local.content.splitlines() if line.startswith('- ')),
                        'Sem tarefas/lembretes explícitos disponíveis.') if local.status == 'success' else 'Memória indisponível.'
            short = '\n'.join([lines[0], 'Agenda:', *agenda[:2], task])[:370]
            return short + ' Tarefas e detalhes disponíveis na interface ou por texto.'
        return text

    def dispatch(self, harness, message, response_mode):
        value = normalize(message)
        if ('resumo do dia' in value or 'introducao' in value
                or 'o que tenho para fazer hoje' in value):
            if value not in REQUESTS:
                return 'Peça apenas um briefing, sem negação ou outra intenção.'
            if not self.config.briefing_enabled:
                return 'Briefing desligado.'
            if self.config.briefing_auto_enabled:
                try:
                    self._marker()
                except (OSError, sqlite3.Error):
                    pass
            return self.render(harness, response_mode)
        return None
