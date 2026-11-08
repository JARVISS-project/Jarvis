from __future__ import annotations

from dataclasses import replace
import time

import pytest

from jarvis.safe_browser.policy import UrlPolicy
from jarvis.safe_browser.service import SafeBrowserService


class FakeLauncher:
    def __init__(self, policy: UrlPolicy) -> None:
        self.policy = policy
        self.token: str | None = "association-a"
        self.calls = []
        self.result = True
        self.error: Exception | None = None
        self.delay = 0.0

    def association_token(self): return self.token
    def launch(self, request):
        assert self.policy.is_validated(request)
        self.calls.append(request)
        if self.delay: time.sleep(self.delay)
        if self.error: raise self.error
        return self.result


@pytest.mark.parametrize("url", [
    "http://google.com/", "file:///x", "javascript:alert(1)", "data:text/plain,x",
    "vbscript:x", "ftp://google.com/", "mailto:a@example.com",
    "https://user:pass@google.com/", "https://localhost/", "https://127.0.0.1/",
    "https://10.0.0.1/", "https://google.com.evil.example/",
    "https://evil.google.com/", "https://xn--gogle-qmc.com/",
    "https://google.com\\@evil.example/", "https://google.com:444/",
    "https://google.com/%252e%252e/", "https://google.com/\r\nX:1",
    "https:///missing", "https://google.com/#fragment",
    "https://www.google.com/?q=extra", "https://github.com/extra",
    "https://www.google.com/search?q=ok%0d%0aInjected",
    "https://www.google.com/search?q=example.com",
    "https://www.google.com/search?q=ok&next=https%3A%2F%2Fevil.example",
])
def test_forbidden_urls_never_receive_validation_proof(url):
    with pytest.raises(ValueError):
        UrlPolicy().validate_candidate_for_test(url)


def test_safe_browser_destinations_are_registered_canonical_and_exact():
    policy = UrlPolicy()
    github = policy.destination("github", "token")
    assert github.canonical_url == "https://github.com/"
    policy.revalidate(github)
    with pytest.raises(ValueError, match="desconhecido"):
        policy.destination("git-hub", "token")


@pytest.mark.parametrize("term", [
    "", "x" * 161, "https://example.com", "www.example.com", "example.com",
    "cmd /c whoami", "powershell Get-Process", "abc%2520def", "linha\r\ninjetada",
    "texto\u202eambiguo",
])
def test_search_term_adversarials_are_rejected(term):
    with pytest.raises(ValueError):
        UrlPolicy().normalize_search_term(term)


def test_unicode_search_is_normalized_and_encoded_once():
    policy = UrlPolicy()
    request = policy.search("google", "cafe\u0301 com pão", "token")
    assert request.canonical_url == "https://www.google.com/search?q=caf%C3%A9+com+p%C3%A3o"
    policy.revalidate(request)


def test_safe_browser_status_and_launch_failures():
    policy = UrlPolicy(); launcher = FakeLauncher(policy); service = SafeBrowserService(launcher, policy, timeout=.01)
    launcher.token = None
    with pytest.raises(RuntimeError, match="indisponível"):
        service.prepare_destination("github")
    launcher.token = "association-a"; launcher.result = False
    with pytest.raises(RuntimeError, match="rejeitou"):
        service.execute(service.prepare_destination("github"))
    launcher.result = True; launcher.error = OSError("system")
    with pytest.raises(OSError, match="system"):
        service.execute(service.prepare_destination("github"))
    launcher.error = None; launcher.delay = .05
    with pytest.raises(TimeoutError):
        service.execute(service.prepare_destination("github"))
    time.sleep(.06)
    assert len(launcher.calls) == 3


def test_association_or_url_change_between_prepare_and_execute_is_blocked():
    policy = UrlPolicy(); launcher = FakeLauncher(policy); service = SafeBrowserService(launcher, policy)
    prepared = service.prepare_destination("github")
    launcher.token = "association-b"
    with pytest.raises(PermissionError, match="associação"):
        service.execute(prepared)
    assert launcher.calls == []
    launcher.token = "association-a"
    tampered_request = replace(prepared.request, canonical_url="https://evil.example/")
    with pytest.raises(PermissionError):
        service.execute(replace(prepared, request=tampered_request))
    assert launcher.calls == []
    search = service.prepare_search("google", "termo seguro")
    changed_search = replace(
        search,
        request=replace(
            search.request,
            canonical_url="https://www.google.com/search?q=outro+termo",
        ),
    )
    with pytest.raises(PermissionError, match="modificada"):
        service.execute(changed_search)
    assert launcher.calls == []
