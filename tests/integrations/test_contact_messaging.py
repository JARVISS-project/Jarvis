"""No browser or SMTP network: exact destinations, TLS and outcome semantics."""
from urllib.parse import parse_qs, urlsplit

import pytest

from jarvis.integrations.contact_messaging import ContactMessagingAdapter, MessageRejectedError


SMTP = {'host': 'smtp.example.invalid', 'port': 465, 'username': 'sender@example.invalid',
        'password': 'synthetic-password', 'sender': 'sender@example.invalid'}


def test_whatsapp_opens_exact_phone_and_encoded_draft_without_claiming_send():
    urls = []
    adapter = ContactMessagingAdapter(opener=lambda url, new: urls.append(url) or True)
    draft = {'channel': 'whatsapp', 'recipient': '+5511999999999', 'body': 'Olá & confirme? #texto', 'subject': 'Agenda'}
    result = adapter.send(draft, None)
    url = urlsplit(urls[0])
    assert (url.scheme, url.netloc, url.path) == ('https', 'wa.me', '/5511999999999')
    assert parse_qs(url.query)['text'] == [draft['body']]
    assert result['state'] == 'handed_off' and 'não confirma entrega' in result['message']
    with pytest.raises(ValueError):
        adapter.send({**draft, 'recipient': 'https://evil.invalid'}, None)


def test_smtp_exact_single_recipient_tls_and_no_secret_receipt():
    calls = []
    class Server:
        def __init__(self, host, port, **kwargs):
            assert host == SMTP['host'] and port == 465 and kwargs['context'].check_hostname and kwargs['timeout'] == 20
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def login(self, username, password):
            assert (username, password) == (SMTP['username'], SMTP['password'])
        def send_message(self, message, from_addr, to_addrs):
            calls.append((message, from_addr, to_addrs))
            return {}
    adapter = ContactMessagingAdapter(smtp_factory=Server)
    result = adapter.send({'channel': 'email', 'recipient': 'joao@example.invalid', 'body': 'Confirmamos?', 'subject': 'Compromisso'}, SMTP)
    assert result['state'] == 'accepted' and 'não confirma entrega' in result['message']
    assert calls[0][2] == ['joao@example.invalid'] and 'Bcc' not in calls[0][0]
    assert SMTP['password'] not in str(result)


def test_network_timeout_is_uncertain_and_not_retried():
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('secret-password')
    adapter = ContactMessagingAdapter(smtp_factory=fail)
    with pytest.raises(RuntimeError, match='incerto') as error:
        adapter.send({'channel': 'email', 'recipient': 'joao@example.invalid', 'body': 'Teste', 'subject': 'Agenda'}, SMTP)
    assert calls == [1] and 'secret-password' not in str(error.value)


def test_starttls_happens_before_credentials_or_message():
    steps = []
    class Server:
        def __init__(self, host, port, **kwargs):
            assert port == 587 and 'context' not in kwargs
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def ehlo(self):
            steps.append('ehlo')
        def starttls(self, context):
            assert context.check_hostname
            steps.append('tls')
        def login(self, *args):
            assert steps == ['ehlo', 'tls', 'ehlo']
            steps.append('login')
        def send_message(self, *args, **kwargs):
            assert steps[-1] == 'login'
            steps.append('message')
            return {}
    adapter = ContactMessagingAdapter(smtp_factory=Server)
    result = adapter.send({'channel': 'email', 'recipient': 'joao@example.invalid', 'body': 'Teste', 'subject': 'Agenda'}, {**SMTP, 'port': 587})
    assert result['state'] == 'accepted' and steps[-1] == 'message'
