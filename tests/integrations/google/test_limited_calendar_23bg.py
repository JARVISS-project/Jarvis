from copy import deepcopy
from datetime import datetime, timezone
import json
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from jarvis.config import JarvisConfig
from jarvis.harness import Harness
from jarvis.integrations.google.calendar.client import GoogleCalendarApiClient
from jarvis.integrations.google.calendar.google_provider import GoogleCalendarWriteProvider
from jarvis.integrations.google.calendar.limited_write import (
    AuthorizedCalendarWrite, LimitedCalendarCreateTool, LimitedCalendarUpdateTool,
)
from jarvis.integrations.google.calendar.operation_journal import CalendarOperationJournal
from jarvis.integrations.google.errors import (
    GoogleConflictError, GoogleNetworkError, GooglePermissionError, GoogleRateLimitError,
    GoogleRequestError, GoogleResourceNotFoundError, GoogleScopeError, GoogleWriteUnknownError,
)
from jarvis.memory.conversation import ConversationMemory
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.confirmation_session import ConfirmationSessionManager
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import ToolPreparation
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry

START = '2026-10-02T23:30:00-03:00'
END = '2026-10-03T00:30:00-03:00'
ZONE = 'America/Sao_Paulo'


class Api:
    def __init__(self):
        self.events = {}
        self.posts = self.patches = 0
        self.etags = []
        self.failure = None
        self.conflicts = []
        self.unverifiable = False
        self.corrupt = False
        self.acl = [{'role': 'owner', 'scope': {'type': 'user', 'value': 'private@example.com'}}]
        self.acl_etag = '"acl1"'

    def get(self, path, params):
        if path.endswith('/acl'):
            return {'items': deepcopy(self.acl), 'etag': self.acl_etag}
        if path.endswith('/events'):
            return {'items': deepcopy(self.conflicts)}
        if self.unverifiable and (self.posts or self.patches):
            raise GoogleNetworkError('read timeout')
        event = self.events.get(path.rsplit('/', 1)[-1])
        if event is None:
            raise GoogleResourceNotFoundError('404')
        value = deepcopy(event)
        if self.corrupt and (self.posts or self.patches):
            value['summary'] = 'Unexpected title'
        return value

    def post(self, path, body):
        self.posts += 1
        if self.failure == 'before':
            raise GoogleNetworkError('timeout before response')
        if isinstance(self.failure, Exception):
            raise self.failure
        event_id = body['id']
        if event_id in self.events:
            raise GoogleConflictError('409')
        event = {**deepcopy(body), 'etag': '"v1"', 'organizer': {'self': True}, 'status': 'confirmed'}
        self.events[event_id] = event
        if self.failure == 'lost':
            raise GoogleNetworkError('response lost')
        return deepcopy(event)

    def patch(self, path, body, *, etag):
        self.patches += 1
        self.etags.append(etag)
        event_id = path.rsplit('/', 1)[-1]
        if self.events[event_id]['etag'] != etag:
            raise GoogleConflictError('412')
        if isinstance(self.failure, Exception):
            raise self.failure
        self.events[event_id].update(deepcopy(body))
        self.events[event_id]['etag'] = '"v2"'
        if self.failure == 'lost':
            raise GoogleNetworkError('response lost')
        return deepcopy(self.events[event_id])

    def seed(self, **extra):
        self.events['event1'] = {'id': 'event1', 'summary': 'Original', 'etag': '"v1"',
                                'start': {'dateTime': START, 'timeZone': ZONE},
                                'end': {'dateTime': END, 'timeZone': ZONE},
                                'organizer': {'self': True}, 'status': 'confirmed', **extra}


class NeverLlm:
    def generate(self, **kwargs):
        raise AssertionError('private Calendar payload reached LLM')


def setup(tmp_path, *, write_enabled=True, existing_api=None):
    api = existing_api or Api()
    binding = ['account-a']
    calendars = {'teste': 'calendar-test'}
    provider = GoogleCalendarWriteProvider(api)
    create = LimitedCalendarCreateTool(provider, calendars, lambda: binding[0],
                                       journal=CalendarOperationJournal(tmp_path / 'operations.json'))
    update = LimitedCalendarUpdateTool(provider, calendars, lambda: binding[0])
    registry = ToolRegistry()
    registry.register(create)
    registry.register(update)
    capabilities = CapabilityPolicy({Capability.GOOGLE_CALENDAR_WRITE: write_enabled})
    audit = tmp_path / 'audit.jsonl'
    harness = Harness(NeverLlm(), registry, ToolExecutor(SafetyManager(), AuditLogger(audit), capabilities),
                      ConversationMemory(), config=JarvisConfig(settings_path=tmp_path / 'settings.json'))
    return harness, api, binding, create, update, audit


def create_command(**extra):
    return '/agenda criar ' + json.dumps({'calendario': 'teste', 'titulo': 'Título privado',
                                         'inicio': START, 'fim': END, 'timezone': ZONE, **extra})


def update_command(**extra):
    return '/agenda alterar ' + json.dumps({'calendario': 'teste', 'evento': 'event1',
                                           'titulo': 'Título privado', **extra})


def confirm(harness):
    session = harness.confirmation_sessions.current()
    assert session is not None
    return harness.processar_mensagem('sim', confirmation_session_id=session.id,
                                      confirmation_challenge_id=session.challenge_id)


def test_create_and_update_exactly_once_private_preview_and_audit(tmp_path):
    harness, api, binding, create, update, audit = setup(tmp_path)
    try:
        preview = harness.processar_mensagem(create_command())
        assert api.posts == 0 and 'teste' in preview and START in preview and END in preview and ZONE in preview
        assert 'verificado' in confirm(harness)
        assert api.posts == 1
        assert len(api.events) == 1
        api.seed()
        preview = harness.processar_mensagem(update_command())
        assert 'Anterior: Original' in preview and 'Título privado' in preview
        assert 'verificado' in confirm(harness)
        assert api.patches == 1 and api.etags == ['"v1"']
        assert api.events['event1']['start']['dateTime'] == START
        assert harness.memory.count() == 0
        content = audit.read_text()
        assert 'Título privado' not in content and 'calendar-test' not in content and 'event1' not in content
        assert 'Original' not in content
    finally:
        harness.close()


@pytest.mark.parametrize('change', [
    {'convidados': []}, {'recorrencia': 'daily'}, {'anexos': []}, {'descricao': 'private'},
    {'inicio': '02/10/26 14:00'}, {'inicio': '2026-10-02T14:00:00'},
    {'fim': START}, {'timezone': 'bad/timezone'},
    {'inicio': '2026-10-02T23:30:00+00:00'}, {'calendario': 'primary'},
])
def test_invalid_or_extra_arguments_have_zero_writes(tmp_path, change):
    harness, api, *_ = setup(tmp_path)
    try:
        harness.processar_mensagem(create_command(**change))
        assert harness.confirmation_sessions.current() is None
        assert api.posts == api.patches == 0
    finally:
        harness.close()


@pytest.mark.parametrize('message', [
    'Não crie evento na agenda', 'Crie um compromisso e altere outro na agenda',
    '/agenda criar {} /agenda alterar {}', '/agenda criar [{},{}]',
    '/agenda criar {"titulo":"x","titulo":"y"}',
])
def test_negation_multiple_intents_and_duplicate_json_fail_closed(tmp_path, message):
    harness, api, *_ = setup(tmp_path)
    try:
        response = harness.processar_mensagem(message)
        assert 'Use' in response or 'inválido' in response
        assert harness.confirmation_sessions.current() is None
        assert api.posts == api.patches == 0
    finally:
        harness.close()


def test_cancel_expiry_and_confirmation_replay(tmp_path):
    harness, api, *_ = setup(tmp_path)
    clock = [0.0]
    harness.confirmation_sessions.close()
    harness.confirmation_sessions = ConfirmationSessionManager(
        clock=lambda: clock[0], start_scheduler=False, cleanup=harness._cleanup_confirmation_session)
    try:
        harness.processar_mensagem(create_command())
        session = harness.confirmation_sessions.current()
        harness.processar_mensagem('cancelar', confirmation_session_id=session.id,
                                   confirmation_challenge_id=session.challenge_id)
        assert api.posts == 0
        harness.processar_mensagem(create_command())
        session = harness.confirmation_sessions.current()
        clock[0] = 31
        harness.processar_mensagem('sim', confirmation_session_id=session.id,
                                   confirmation_challenge_id=session.challenge_id)
        assert api.posts == 0
        harness.processar_mensagem(create_command())
        session = harness.confirmation_sessions.current()
        assert 'verificado' in confirm(harness)
        replay = harness.processar_mensagem('sim', confirmation_session_id=session.id,
                                            confirmation_challenge_id=session.challenge_id)
        assert api.posts == 1
    finally:
        harness.close()


@pytest.mark.parametrize('mutation', ['account', 'calendar', 'payload', 'etag', 'revoke'])
def test_confirmation_target_account_payload_version_and_policy_binding(tmp_path, mutation):
    harness, api, binding, create, update, _ = setup(tmp_path)
    api.seed()
    try:
        harness.processar_mensagem(update_command())
        session = harness.confirmation_sessions.current()
        if mutation == 'account':
            binding[0] = 'account-b'
        elif mutation == 'calendar':
            object.__setattr__(session.invocation, 'arguments', {**session.invocation.arguments, 'calendar_id': 'other'})
        elif mutation == 'payload':
            object.__setattr__(session.invocation, 'arguments', {**session.invocation.arguments, 'summary': 'Tampered'})
        elif mutation == 'etag':
            api.events['event1']['etag'] = '"v3"'
        else:
            update._policy_current = lambda: {}
        assert 'verificado' not in confirm(harness)
        assert api.posts == api.patches == 0
    finally:
        harness.close()


@pytest.mark.parametrize('extra', [
    {'recurrence': ['RRULE:FREQ=DAILY']}, {'recurringEventId': 'parent'},
    {'attendees': [{'email': 'private@example.com'}]}, {'attendeesOmitted': True},
    {'organizer': {'self': False}}, {'attachments': [{}]},
    {'extendedProperties': {'shared': {'x': 'y'}}}, {'eventType': 'fromGmail'},
])
def test_recurring_shared_or_non_simple_event_is_blocked(tmp_path, extra):
    harness, api, *_ = setup(tmp_path)
    api.seed(**extra)
    try:
        harness.processar_mensagem(update_command())
        assert harness.confirmation_sessions.current() is None
        assert api.posts == api.patches == 0
    finally:
        harness.close()


def test_selected_event_time_only_conflict_warning_and_day_change(tmp_path):
    harness, api, *_ = setup(tmp_path)
    api.seed()
    api.conflicts = [{'id': 'busy', 'status': 'confirmed'}, {'id': 'event1'}]
    new_start, new_end = '2026-10-03T23:30:00-03:00', '2026-10-04T00:30:00-03:00'
    values = {'calendario': 'teste', 'evento': 'event1', 'inicio': new_start,
              'fim': new_end, 'timezone': ZONE}
    try:
        preview = harness.processar_mensagem('/agenda alterar ' + json.dumps(values))
        assert '1 conflito' in preview and new_start in preview and new_end in preview
        assert 'verificado' in confirm(harness)
        assert api.patches == 1 and api.events['event1']['summary'] == 'Original'
    finally:
        harness.close()


@pytest.mark.parametrize('failure', [GooglePermissionError('403'), GoogleResourceNotFoundError('404'),
                                   GoogleConflictError('409/412'), GoogleRateLimitError('429'),
                                   GoogleScopeError('scope')])
def test_api_errors_do_not_claim_success_or_retry_blindly(tmp_path, failure):
    harness, api, *_ = setup(tmp_path)
    api.failure = failure
    try:
        harness.processar_mensagem(create_command())
        response = confirm(harness)
        assert 'verificado' not in response
        assert api.posts == 1 and api.patches == 0
    finally:
        harness.close()


@pytest.mark.parametrize('action', ['create', 'update'])
def test_successful_write_with_lost_response_reconciles_without_duplicates(tmp_path, action):
    harness, api, *_ = setup(tmp_path)
    api.seed()
    api.failure = 'lost'
    try:
        harness.processar_mensagem(create_command() if action == 'create' else update_command())
        assert 'verificado' in confirm(harness)
        assert (api.posts, api.patches) == ((1, 0) if action == 'create' else (0, 1))
    finally:
        harness.close()


@pytest.mark.parametrize('failure', ['unverifiable', 'corrupt'])
def test_failed_verification_is_pending_and_not_success(tmp_path, failure):
    harness, api, *_ = setup(tmp_path)
    setattr(api, failure, True)
    try:
        harness.processar_mensagem(create_command())
        response = confirm(harness)
        assert 'pendente de reconciliação' in response and 'verificado' not in response
        assert api.posts == 1
        # Restore read path and request the same prepared creation: same event ID.
        setattr(api, failure, False)
        harness.processar_mensagem(create_command())
        assert 'verificado' in confirm(harness)
        assert api.posts == 1 and len(api.events) == 1
    finally:
        harness.close()


def test_denied_capability_insufficient_scope_and_forged_authorization(tmp_path):
    harness, api, binding, create, update, _ = setup(tmp_path, write_enabled=False)
    try:
        harness.processar_mensagem(create_command())
        assert harness.confirmation_sessions.current() is None and api.posts == 0
        def insufficient():
            raise GoogleScopeError('insufficient')
        create._grant_binding = insufficient
        with pytest.raises(GoogleScopeError):
            create.prepare(calendar_id='calendar-test', summary='Title', start=START, end=END, timezone=ZONE)
        with pytest.raises(PermissionError):
            create.execute(calendar_id='calendar-test')
        assert api.posts == api.patches == 0
    finally:
        harness.close()


@pytest.mark.parametrize('code,error_type', [(403, GooglePermissionError), (404, GoogleResourceNotFoundError),
                                         (409, GoogleConflictError), (412, GoogleConflictError),
                                         (429, GoogleRateLimitError), (400, GoogleRequestError)])
def test_http_error_mapping_and_no_notifications(monkeypatch, code, error_type):
    from jarvis.integrations.google.calendar import client as module
    requests = []
    def urlopen(request, timeout):
        requests.append(request)
        raise HTTPError(request.full_url, code, 'private HTTP details', {}, None)
    monkeypatch.setattr(module, 'urlopen', urlopen)
    api = GoogleCalendarApiClient(None)
    with pytest.raises(error_type):
        api._request_once('PATCH', '/calendars/test/events/id', {'sendUpdates': 'none'},
                          {'summary': 'Private'}, 'secret', '"v1"')
    assert 'sendUpdates=none' in requests[0].full_url
    assert requests[0].get_header('If-match') == '"v1"'


def test_direct_llm_style_update_id_cannot_replace_explicit_selection(tmp_path):
    harness, api, binding, create, update, _ = setup(tmp_path)
    api.seed()
    try:
        result = harness.tool_executor.execute_with_result(update, calendar_id='calendar-test', event_id='event1',
                                                           selection=uuid4().hex, summary='Injected')
        assert result.status != 'success' and api.patches == 0
        assert harness.confirmation_sessions.current() is None
    finally:
        harness.close()


def test_receipt_and_execution_artifact_are_unforgeable_and_one_use(tmp_path):
    harness, api, _, create, _, _ = setup(tmp_path)
    try:
        args = {'calendar_id': 'calendar-test', 'summary': 'Title', 'start': START, 'end': END, 'timezone': ZONE}
        prep = create.prepare(**args)
        forged = ToolPreparation(prep.target, AuthorizedCalendarWrite(prep.context), prep.binding_digest)
        with pytest.raises(PermissionError):
            create.execute_authorized(forged)
        safety = harness.tool_executor.safety_manager
        grant = safety.grants.issue(create.name, prep.target, preparation_digest=prep.binding_digest)
        receipt = safety.consume_grant_for_execution(grant, create.name, prep.target,
                                                     preparation_digest=prep.binding_digest)
        authorized = create.bind_authorized(prep, receipt)
        with pytest.raises(PermissionError):
            create.bind_authorized(prep, receipt)
        assert 'verificado' in create.execute_authorized(authorized)
        with pytest.raises(PermissionError):
            create.execute_authorized(authorized)
        assert api.posts == 1
    finally:
        harness.close()


def test_mutated_prepared_input_cannot_change_payload_after_authorization(tmp_path):
    harness, api, _, create, _, _ = setup(tmp_path)
    try:
        args = {'calendar_id': 'calendar-test', 'summary': 'Title', 'start': START, 'end': END, 'timezone': ZONE}
        prep = create.prepare(**args)
        safety = harness.tool_executor.safety_manager
        grant = safety.grants.issue(create.name, prep.target, preparation_digest=prep.binding_digest)
        receipt = safety.consume_grant_for_execution(grant, create.name, prep.target,
                                                     preparation_digest=prep.binding_digest)
        authorized = create.bind_authorized(prep, receipt)
        object.__setattr__(authorized.context.prepared.event, 'summary', 'Tampered after authorization')
        with pytest.raises(PermissionError):
            create.execute_authorized(authorized)
        assert api.posts == 0
    finally:
        harness.close()


@pytest.mark.parametrize('acl', [
    [], [{'role': 'writer', 'scope': {'type': 'user'}}],
    [{'role': 'owner', 'scope': {'type': 'user'}}, {'role': 'reader', 'scope': {'type': 'user'}}],
    [{'role': 'owner', 'scope': {'type': 'user'}}, {'role': 'freeBusyReader', 'scope': {'type': 'domain'}}],
    [{'role': 'owner', 'scope': {'type': 'group'}}],
    [{'role': 'owner', 'scope': {'type': 'user'}}, {'role': 'reader', 'scope': {'type': 'default'}}],
])
def test_inherited_sharing_or_unknown_ownership_blocks_all_writes(tmp_path, acl):
    harness, api, *_ = setup(tmp_path)
    api.acl = acl
    try:
        harness.processar_mensagem(create_command())
        assert harness.confirmation_sessions.current() is None
        assert api.posts == api.patches == 0
    finally:
        harness.close()


def test_acl_version_changes_after_preview_require_new_confirmation(tmp_path):
    harness, api, *_ = setup(tmp_path)
    try:
        harness.processar_mensagem(create_command())
        api.acl_etag = '"acl2"'
        response = confirm(harness)
        assert 'verificado' not in response and api.posts == api.patches == 0
    finally:
        harness.close()


@pytest.mark.parametrize('action', ['create', 'update'])
def test_uncertain_write_survives_restart_and_reconciles_without_duplicate(tmp_path, action):
    harness, api, *_ = setup(tmp_path)
    api.seed()
    api.failure = 'lost'
    api.unverifiable = True
    command = create_command() if action == 'create' else update_command()
    try:
        harness.processar_mensagem(command)
        assert 'pendente de reconciliação' in confirm(harness)
    finally:
        harness.close()
    journal = tmp_path / 'operations.json'
    if action == 'create':
        content = journal.read_text()
        assert 'Título privado' not in content and START not in content and 'account-a' not in content
    api.unverifiable = False
    second, *_ = setup(tmp_path, existing_api=api)
    try:
        second.processar_mensagem(command)
        assert 'verificado' in confirm(second)
        assert (api.posts, api.patches) == ((1, 0) if action == 'create' else (0, 1))
        if action == 'create':
            assert json.loads(journal.read_text()) == {}
    finally:
        second.close()


def test_journal_corruption_blocks_before_any_write(tmp_path):
    (tmp_path / 'operations.json').write_text('{invalid')
    harness, api, *_ = setup(tmp_path)
    try:
        harness.processar_mensagem(create_command())
        assert harness.confirmation_sessions.current() is None and api.posts == 0
    finally:
        harness.close()


@pytest.mark.parametrize('values', [
    {'inicio': '2026-03-08T02:30:00-05:00', 'fim': '2026-03-08T03:30:00-04:00', 'timezone': 'America/New_York'},
    {'fim': '2026-10-04T00:30:00-03:00'},
])
def test_nonexistent_wall_time_and_excessive_duration_rejected(tmp_path, values):
    harness, api, *_ = setup(tmp_path)
    try:
        harness.processar_mensagem(create_command(**values))
        assert harness.confirmation_sessions.current() is None and api.posts == 0
    finally:
        harness.close()


def test_title_and_time_are_two_changes_and_missing_time_is_not_inferred(tmp_path):
    harness, api, *_ = setup(tmp_path)
    api.seed()
    try:
        harness.processar_mensagem(update_command(inicio=START, fim=END, timezone=ZONE))
        assert harness.confirmation_sessions.current() is None
        values = {'calendario': 'teste', 'evento': 'event1', 'inicio': START, 'timezone': ZONE}
        harness.processar_mensagem('/agenda alterar ' + json.dumps(values))
        assert harness.confirmation_sessions.current() is None and api.patches == 0
    finally:
        harness.close()


def test_limited_scopes_and_storage_keep_legacy_and_minimal_read_separate(monkeypatch, tmp_path):
    from jarvis.integrations.google import factory
    from jarvis.integrations.google.scopes import CALENDAR_LIMITED_WRITE_SCOPES, CALENDAR_MINIMAL_READ_SCOPES
    calls = []
    monkeypatch.setattr(factory, 'build_google_auth_manager', lambda path, **kwargs: calls.append((path, kwargs)))
    monkeypatch.setattr(factory, 'google_credential_dir', lambda: tmp_path)
    factory.build_google_calendar_limited_write_auth()
    factory.build_google_calendar_minimal_read_auth()
    assert calls[0][0].name == 'credentials_calendar_limited_write.bin'
    assert calls[1][0].name == 'credentials_calendar_read.bin'
    assert calls[0][1]['scopes'] == CALENDAR_LIMITED_WRITE_SCOPES
    assert calls[1][1]['scopes'] == CALENDAR_MINIMAL_READ_SCOPES
    assert 'https://www.googleapis.com/auth/calendar.acls.readonly' in calls[0][1]['scopes']
