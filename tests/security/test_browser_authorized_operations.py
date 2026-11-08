import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis.browser.models import (
    AuthorizedFillOperation,
    BrowserActionResult,
    BrowserNavigationResult,
    BrowserTab,
    PreparedFillOperation,
    PreparedNavigateOperation,
    PreparedTypeTextOperation,
    deserialize_prepared_browser_operation,
    deserialize_prepared_browser_operation_json,
    serialize_prepared_browser_operation,
)
from jarvis.browser.playwright_backend import PlaywrightBackend
from jarvis.browser.service import BrowserService
from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import Capability, CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.base import ToolPreparation
from jarvis.tools.browser import browser_tools
from jarvis.tools.executor import ToolExecutor


class RecordingAuthorizedBackend:
    def __init__(self) -> None:
        self.tabs = (
            BrowserTab(
                id="tab-a",
                url="https://authorized.example/start",
                title="Authorized",
                is_active=True,
            ),
            BrowserTab(
                id="tab-b",
                url="https://caller.example/start",
                title="Caller",
            ),
        )
        self.navigations: list[tuple[str, str]] = []
        self.types: list[tuple[str, str, str]] = []
        self.fills: list[tuple[str, str, str]] = []
        self.fail_navigation = False

    async def launch(self, headless: bool = False) -> None:
        pass

    async def close(self) -> None:
        pass

    async def list_tabs(self) -> tuple[BrowserTab, ...]:
        return self.tabs

    async def navigate(self, operation) -> BrowserNavigationResult:
        operation.require_executing()
        operation.claim_effect()
        if self.fail_navigation:
            return BrowserNavigationResult(
                url=operation.url,
                title="",
                success=False,
                error="blocked",
            )
        self.navigations.append((operation.tab_id, operation.url))
        return BrowserNavigationResult(
            url=operation.url,
            title="Authorized",
            success=True,
        )

    async def type_text(self, operation) -> BrowserActionResult:
        operation.require_executing()
        operation.claim_effect()
        self.types.append(
            (operation.tab_id, operation.selector, operation.text)
        )
        return BrowserActionResult(success=True, message="typed")

    async def fill(self, operation) -> BrowserActionResult:
        operation.require_executing()
        operation.claim_effect()
        self.fills.append(
            (operation.tab_id, operation.selector, operation.text)
        )
        return BrowserActionResult(success=True, message="filled")


class MockConfirmationProvider:
    def confirm_request(self, request):
        return True
    def confirm(self, action, target, risk):
        return True
    def clear_pending_confirmation(self):
        pass

def build_runtime(tmp_path: Path):  # type: ignore[no-untyped-def]
    backend = RecordingAuthorizedBackend()
    capability_policy = CapabilityPolicy({Capability.BROWSER: True})
    service = BrowserService(
        backend,  # type: ignore[arg-type]
        capability_policy,
        allowed_domains=["authorized.example", "caller.example"],
    )
    asyncio.run(service.launch())
    tools = {tool.name: tool for tool in browser_tools(service)}
    executor = ToolExecutor(
        SafetyManager(MockConfirmationProvider()),
        AuditLogger(tmp_path / "audit.log"),
        capability_policy=capability_policy,
    )
    return backend, tools, executor


def authorize_prepared(tool, prepared):  # type: ignore[no-untyped-def]
    safety = SafetyManager(MockConfirmationProvider())
    grant = safety.authorize_invocation(
        tool.name,
        prepared.target,
        tool.risk,
        prepared.binding_digest,
    )
    assert grant is not None
    receipt = safety.consume_grant_for_execution(
        grant,
        tool.name,
        prepared.target,
        preparation_digest=prepared.binding_digest,
    )
    assert receipt is not None
    return tool.bind_authorized(prepared, receipt)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "recording_name", "expected"),
    [
        (
            "browser_navigate",
            {"tab_id": "tab-a", "url": "https://authorized.example/a"},
            "navigations",
            ("tab-a", "https://authorized.example/a"),
        ),
        (
            "browser_type",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            "types",
            ("tab-a", "#authorized", "A"),
        ),
        (
            "browser_fill",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            "fills",
            ("tab-a", "#authorized", "A"),
        ),
    ],
)
def test_executor_sends_only_authorized_values_to_backend(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, str],
    recording_name: str,
    expected: tuple[str, ...],
) -> None:
    backend, tools, executor = build_runtime(tmp_path)

    result = executor.execute_with_result(tools[tool_name], **arguments)

    assert result.status == "success"
    assert getattr(backend, recording_name) == [expected]


@pytest.mark.parametrize(
    ("tool_name", "authorized", "caller", "recording_name", "expected"),
    [
        (
            "browser_navigate",
            {"tab_id": "tab-a", "url": "https://authorized.example/a"},
            {"tab_id": "tab-b", "url": "https://caller.example/b"},
            "navigations",
            ("tab-a", "https://authorized.example/a"),
        ),
        (
            "browser_type",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            {"tab_id": "tab-b", "selector": "#caller", "text": "B"},
            "types",
            ("tab-a", "#authorized", "A"),
        ),
        (
            "browser_fill",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            {"tab_id": "tab-b", "selector": "#caller", "text": "B"},
            "fills",
            ("tab-a", "#authorized", "A"),
        ),
    ],
)
def test_caller_mutation_after_binding_cannot_change_backend_arguments(
    tmp_path: Path,
    tool_name: str,
    authorized: dict[str, str],
    caller: dict[str, str],
    recording_name: str,
    expected: tuple[str, ...],
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    tool = tools[tool_name]
    caller_values = dict(authorized)
    prepared = tool.prepare(**caller_values)
    bound = authorize_prepared(tool, prepared)
    assert bound is not None

    caller_values.clear()
    caller_values.update(caller)
    tool.execute_authorized(bound)

    assert getattr(backend, recording_name) == [expected]
    with pytest.raises(TypeError):
        tool.execute_authorized(bound, **caller_values)
    with pytest.raises(PermissionError, match="não autorizada|já foi utilizada"):
        tool.execute_authorized(bound)
    assert getattr(backend, recording_name) == [expected]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "recording_name"),
    [
        (
            "browser_navigate",
            {"tab_id": "tab-a", "url": "https://authorized.example/a"},
            "navigations",
        ),
        (
            "browser_type",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            "types",
        ),
        (
            "browser_fill",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            "fills",
        ),
    ],
)
def test_missing_or_malformed_authorization_fails_closed(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, str],
    recording_name: str,
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    tool = tools[tool_name]
    prepared = tool.prepare(**arguments)

    with pytest.raises(PermissionError, match="Comprovante"):
        tool.bind_authorized(prepared)
    with pytest.raises(PermissionError, match="autorização"):
        tool.execute_prepared(prepared, **arguments)
    with pytest.raises(PermissionError, match="Payload autorizado"):
        tool.execute_authorized(
            ToolPreparation(
                prepared.target,
                {"kind": tool_name},
                prepared.binding_digest,
            )
        )

    assert getattr(backend, recording_name) == []


def test_wrong_kind_wrong_target_and_forged_authority_fail_closed(
    tmp_path: Path,
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    type_prepared = tools["browser_type"].prepare(
        tab_id="tab-a", selector="#authorized", text="A"
    )
    type_authorized = authorize_prepared(
        tools["browser_type"], type_prepared
    )
    assert type_authorized is not None

    with pytest.raises(PermissionError, match="incompatível"):
        tools["browser_fill"].execute_authorized(type_authorized)
    with pytest.raises(PermissionError, match="alvo"):
        tools["browser_type"].execute_authorized(
            replace(type_authorized, target="browser:tab:tab-b:#authorized")
        )

    forged_operation = PreparedFillOperation(
        "tab-a",
        "https://authorized.example/start",
        "#authorized",
        "A",
    )
    forged = AuthorizedFillOperation(
        operation=forged_operation,
        tool_name="browser_fill",
        target=forged_operation.public_target,
        binding_digest=forged_operation.binding_digest(),
        _authority=object(),
    )
    with pytest.raises(PermissionError, match="não autorizada"):
        tools["browser_fill"].execute_authorized(
            ToolPreparation(
                forged.target,
                forged,
                forged.binding_digest,
            )
        )

    assert backend.types == []
    assert backend.fills == []


def test_copied_receipt_and_authorized_artifact_lose_authority(
    tmp_path: Path,
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    tool = tools["browser_fill"]
    prepared = tool.prepare(
        tab_id="tab-a", selector="#authorized", text="A"
    )
    safety = SafetyManager(MockConfirmationProvider())
    grant = safety.authorize_invocation(
        tool.name,
        prepared.target,
        tool.risk,
        prepared.binding_digest,
    )
    assert grant is not None
    receipt = safety.consume_grant_for_execution(
        grant,
        tool.name,
        prepared.target,
        preparation_digest=prepared.binding_digest,
    )
    assert receipt is not None

    with pytest.raises(PermissionError, match="autorização"):
        tool.bind_authorized(prepared, replace(receipt))

    authorized = tool.bind_authorized(prepared, receipt)
    assert authorized is not None
    copied_context = replace(authorized.context)
    with pytest.raises(PermissionError, match="não autorizada"):
        tool.execute_authorized(
            replace(authorized, context=copied_context)
        )

    assert backend.fills == []


def test_effect_claim_is_atomic_and_single_consumer(tmp_path: Path) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    tool = tools["browser_fill"]
    prepared = tool.prepare(
        tab_id="tab-a", selector="#authorized", text="A"
    )
    authorized = authorize_prepared(tool, prepared)
    assert authorized is not None
    operation = authorized.context
    operation.begin_execution()
    try:
        asyncio.run(tool.service.fill(operation))
        with pytest.raises(PermissionError, match="execução"):
            asyncio.run(tool.service.fill(operation))
    finally:
        operation.finish_execution()

    assert backend.fills == [("tab-a", "#authorized", "A")]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("browser_navigate", {"tab_id": "tab-a"}),
        ("browser_navigate", {"tab_id": "tab-a", "url": None}),
        ("browser_type", {"tab_id": "tab-a", "selector": "#a"}),
        ("browser_type", {"tab_id": "tab-a", "selector": None, "text": "A"}),
        ("browser_fill", {"tab_id": "tab-a", "text": "A"}),
        ("browser_fill", {"tab_id": "tab-a", "selector": "#a", "text": None}),
    ],
)
def test_missing_or_invalid_authorized_fields_never_reach_backend(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    backend, tools, executor = build_runtime(tmp_path)

    result = executor.execute_with_result(tools[tool_name], **arguments)

    assert result.status == "error"
    assert backend.navigations == []
    assert backend.types == []
    assert backend.fills == []


@pytest.mark.parametrize("tool_name", ["browser_navigate", "browser_type", "browser_fill"])
def test_unknown_caller_fields_are_rejected_before_authorization(
    tmp_path: Path,
    tool_name: str,
) -> None:
    backend, tools, executor = build_runtime(tmp_path)
    arguments: dict[str, object] = {"tab_id": "tab-a", "unexpected": "B"}
    if tool_name == "browser_navigate":
        arguments["url"] = "https://authorized.example/a"
    else:
        arguments.update(selector="#authorized", text="A")

    result = executor.execute_with_result(tools[tool_name], **arguments)

    assert result.status == "error"
    assert backend.navigations == []
    assert backend.types == []
    assert backend.fills == []


@pytest.mark.parametrize(
    "operation",
    [
        PreparedNavigateOperation("tab-a", "https://authorized.example/a"),
        PreparedTypeTextOperation(
            "tab-a",
            "https://authorized.example/start",
            "#authorized",
            "A",
        ),
        PreparedFillOperation(
            "tab-a",
            "https://authorized.example/start",
            "#authorized",
            "A",
        ),
    ],
)
def test_serialization_round_trip_preserves_kind_target_and_binding(
    operation,
) -> None:  # type: ignore[no-untyped-def]
    restored = deserialize_prepared_browser_operation_json(
        serialize_prepared_browser_operation(operation)
    )

    assert type(restored) is type(operation)
    assert restored == operation
    assert restored.public_target == operation.public_target
    assert restored.binding_digest() == operation.binding_digest()


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "kind": "UNKNOWN", "tab_id": "tab-a"},
        {"version": 1, "kind": "NAVIGATE", "tab_id": "tab-a"},
        {
            "version": 1,
            "kind": "NAVIGATE",
            "tab_id": "tab-a",
            "url": "https://authorized.example/a",
            "text": "confused",
        },
        {
            "version": 1,
            "kind": "TYPE_TEXT",
            "tab_id": "tab-a",
            "page_url": "https://authorized.example/start",
            "selector": "#a",
            "text": "A",
            "url": "https://caller.example/b",
        },
        {
            "version": True,
            "kind": "FILL",
            "tab_id": "tab-a",
            "page_url": "https://authorized.example/start",
            "selector": "#a",
            "text": "A",
        },
        {
            "version": 1,
            "kind": "FILL",
            "tab_id": ["tab-a"],
            "page_url": "https://authorized.example/start",
            "selector": "#a",
            "text": "A",
        },
    ],
)
def test_deserialization_rejects_partial_unknown_and_type_confused_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        deserialize_prepared_browser_operation(payload)


def test_deserialization_rejects_duplicate_fields_and_does_not_grant_authority(
    tmp_path: Path,
) -> None:
    encoded = (
        '{"version":1,"kind":"FILL","tab_id":"tab-a",'
        '"selector":"#authorized","text":"A","text":"B"}'
    )
    with pytest.raises(ValueError, match="duplicado"):
        deserialize_prepared_browser_operation_json(encoded)

    backend, tools, _executor = build_runtime(tmp_path)
    restored = deserialize_prepared_browser_operation_json(
        serialize_prepared_browser_operation(
            PreparedFillOperation(
                "tab-a",
                "https://authorized.example/start",
                "#authorized",
                "A",
            )
        )
    )
    with pytest.raises(PermissionError, match="Payload autorizado"):
        tools["browser_fill"].execute_authorized(
            ToolPreparation(
                restored.public_target,
                restored,
                restored.binding_digest(),
            )
        )
    assert backend.fills == []


@pytest.mark.parametrize("tool_name", ["browser_type", "browser_fill"])
def test_route_change_after_authorization_fails_closed(
    tmp_path: Path,
    tool_name: str,
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    tool = tools[tool_name]
    prepared = tool.prepare(
        tab_id="tab-a", selector="#authorized", text="A"
    )
    authorized = authorize_prepared(tool, prepared)
    assert authorized is not None
    backend.tabs = (
        BrowserTab(
            id="tab-a",
            url="https://caller.example/changed",
            title="Changed",
            is_active=True,
        ),
        backend.tabs[1],
    )

    with pytest.raises(PermissionError, match="rota autorizada"):
        tool.execute_authorized(authorized)

    assert backend.types == []
    assert backend.fills == []


@pytest.mark.parametrize(
    "url",
    [
        "https://authorized.example.attacker.test/path",
        "https://authorized.example@attacker.test/path",
        "file:///C:/sensitive.txt",
        "javascript:alert(1)",
        "https://authorized.example/app#route",
    ],
)
def test_navigation_rejects_domain_confusion_before_authorization(
    tmp_path: Path,
    url: str,
) -> None:
    backend, tools, executor = build_runtime(tmp_path)

    result = executor.execute_with_result(
        tools["browser_navigate"], tab_id="tab-a", url=url
    )

    assert result.status == "security_blocked"
    assert backend.navigations == []


class PassingVerifier:
    def before(self, tool_name, target, context):  # type: ignore[no-untyped-def]
        return object()

    def after(self, token, tool_name, target, context):  # type: ignore[no-untyped-def]
        return "unchanged"

    def cancel(self, token):  # type: ignore[no-untyped-def]
        pass


@pytest.mark.parametrize(
    ("tool_name", "arguments", "recording_name"),
    [
        (
            "browser_navigate",
            {"tab_id": "tab-a", "url": "https://authorized.example/a"},
            "navigations",
        ),
        (
            "browser_type",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            "types",
        ),
        (
            "browser_fill",
            {"tab_id": "tab-a", "selector": "#authorized", "text": "A"},
            "fills",
        ),
    ],
)
def test_verifier_refresh_preserves_consumed_operation_binding(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, str],
    recording_name: str,
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    executor = ToolExecutor(
        SafetyManager(MockConfirmationProvider()),
        AuditLogger(tmp_path / "verified-audit.log"),
        capability_policy=CapabilityPolicy({Capability.BROWSER: True}),
        verifier=PassingVerifier(),  # type: ignore[arg-type]
    )

    result = executor.execute_with_result(tools[tool_name], **arguments)

    assert result.status == "success"
    assert len(getattr(backend, recording_name)) == 1


def test_changed_refresh_cannot_replace_consumed_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, tools, _executor = build_runtime(tmp_path)
    tool = tools["browser_navigate"]

    def replace_after_authorization(preparation, **kwargs):  # type: ignore[no-untyped-def]
        return tool.prepare(
            tab_id="tab-b", url="https://caller.example/replaced"
        )

    monkeypatch.setattr(tool, "refresh_prepared", replace_after_authorization)
    executor = ToolExecutor(
        SafetyManager(MockConfirmationProvider()),
        AuditLogger(tmp_path / "refresh-audit.log"),
        capability_policy=CapabilityPolicy({Capability.BROWSER: True}),
        verifier=PassingVerifier(),  # type: ignore[arg-type]
    )

    result = executor.execute_with_result(
        tool,
        tab_id="tab-a",
        url="https://authorized.example/a",
    )

    assert result.status == "security_blocked"
    assert backend.navigations == []


class FakeRoute:
    def __init__(self) -> None:
        self.aborted = False

    async def abort(self, error_code: str) -> None:
        self.aborted = True

    async def continue_(self) -> None:
        pass


class FakeNavigationRequest:
    def __init__(self, url: str, frame: object) -> None:
        self.url = url
        self.frame = frame

    def is_navigation_request(self) -> bool:
        return True


class RedirectingPage:
    def __init__(self) -> None:
        self.url = "https://authorized.example/start"
        self.main_frame = object()
        self.handler = None
        self.redirect_loaded = False

    async def route(self, pattern: str, handler) -> None:  # type: ignore[no-untyped-def]
        self.handler = handler

    async def unroute(self, pattern: str, handler) -> None:  # type: ignore[no-untyped-def]
        self.handler = None

    async def goto(self, url: str, wait_until: str) -> None:
        assert self.handler is not None
        initial = FakeRoute()
        await self.handler(
            initial,
            FakeNavigationRequest(url, self.main_frame),
        )
        assert not initial.aborted
        redirect = FakeRoute()
        await self.handler(
            redirect,
            FakeNavigationRequest(
                "https://caller.example/redirected",
                self.main_frame,
            ),
        )
        if redirect.aborted:
            raise RuntimeError("redirect blocked")
        self.redirect_loaded = True
        self.url = "https://caller.example/redirected"

    async def title(self) -> str:
        return "Redirected"


def test_playwright_blocks_wrong_target_redirect_before_loading(
    tmp_path: Path,
) -> None:
    _recording, tools, _executor = build_runtime(tmp_path)
    tool = tools["browser_navigate"]
    prepared = tool.prepare(
        tab_id="tab-a", url="https://authorized.example/a"
    )
    authorized = authorize_prepared(tool, prepared)
    assert authorized is not None
    operation = authorized.context
    page = RedirectingPage()
    backend = PlaywrightBackend()
    backend._tabs["tab-a"] = page

    operation.begin_execution()
    try:
        result = asyncio.run(backend.navigate(operation))
    finally:
        operation.finish_execution()

    assert not result.success
    assert not page.redirect_loaded
    assert page.url == "https://authorized.example/start"


class StablePage(RedirectingPage):
    async def goto(self, url: str, wait_until: str) -> None:
        assert self.handler is not None
        initial = FakeRoute()
        await self.handler(
            initial,
            FakeNavigationRequest(url, self.main_frame),
        )
        assert not initial.aborted
        self.url = url


def test_navigation_guard_remains_active_after_dom_content_loaded(
    tmp_path: Path,
) -> None:
    _recording, tools, _executor = build_runtime(tmp_path)
    tool = tools["browser_navigate"]
    prepared = tool.prepare(
        tab_id="tab-a", url="https://authorized.example/a"
    )
    authorized = authorize_prepared(tool, prepared)
    assert authorized is not None
    operation = authorized.context
    page = StablePage()
    backend = PlaywrightBackend()
    backend._tabs["tab-a"] = page

    operation.begin_execution()
    try:
        result = asyncio.run(backend.navigate(operation))
    finally:
        operation.finish_execution()
    assert result.success
    assert page.handler is not None

    delayed = FakeRoute()
    asyncio.run(
        page.handler(
            delayed,
            FakeNavigationRequest(
                "https://caller.example/after-domcontentloaded",
                page.main_frame,
            ),
        )
    )
    assert delayed.aborted


def test_backend_failure_is_not_a_successful_tool_execution(
    tmp_path: Path,
) -> None:
    backend, tools, executor = build_runtime(tmp_path)
    backend.fail_navigation = True

    result = executor.execute_with_result(
        tools["browser_navigate"],
        tab_id="tab-a",
        url="https://authorized.example/a",
    )

    assert result.status == "error"
    assert result.authorized
    assert backend.navigations == []
