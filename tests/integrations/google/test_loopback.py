import threading
from urllib.request import urlopen

import pytest

from jarvis.integrations.google.errors import GoogleAuthorizationTimeoutError
from jarvis.integrations.google.loopback import LoopbackOAuthReceiver


def test_loopback_receives_callback_without_echoing_code() -> None:
    receiver = LoopbackOAuthReceiver()
    response_body: list[str] = []

    def callback() -> None:
        with urlopen(
            receiver.redirect_uri + "?code=authorization-secret&state=expected",
            timeout=2,
        ) as response:
            response_body.append(response.read().decode("utf-8"))

    thread = threading.Thread(target=callback)
    thread.start()
    result = receiver.wait(2.0, None, "expected")
    thread.join(2.0)
    receiver.close()
    receiver.close()

    assert result.state == "expected"
    assert "authorization-secret" in result.authorization_response
    assert "authorization-secret" not in response_body[0]
    assert "Volte ao JARVIS para verificar a conexão" in response_body[0]


def test_loopback_timeout_is_bounded() -> None:
    with LoopbackOAuthReceiver() as receiver:
        with pytest.raises(GoogleAuthorizationTimeoutError):
            receiver.wait(0.1, None, "expected")


def test_invalid_state_is_ignored_until_valid_callback_arrives() -> None:
    receiver = LoopbackOAuthReceiver()

    def callbacks() -> None:
        with urlopen(receiver.redirect_uri + "?code=bad&state=attacker", timeout=2):
            pass
        with urlopen(receiver.redirect_uri + "?code=good&state=expected", timeout=2):
            pass

    thread = threading.Thread(target=callbacks)
    thread.start()
    result = receiver.wait(2.0, None, "expected")
    thread.join(2.0)
    receiver.close()

    assert "code=good" in result.authorization_response


def test_partial_local_request_cannot_disable_timeout() -> None:
    import socket
    from urllib.parse import urlsplit

    receiver = LoopbackOAuthReceiver()
    endpoint = urlsplit(receiver.redirect_uri)
    client = socket.create_connection((endpoint.hostname, endpoint.port), timeout=1)
    client.sendall(b"GET /oauth2/callback?")
    try:
        with pytest.raises(GoogleAuthorizationTimeoutError):
            receiver.wait(0.3, None, "expected")
    finally:
        client.close()
        receiver.close()
