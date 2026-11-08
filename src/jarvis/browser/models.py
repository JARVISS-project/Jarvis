"""Modelos imutáveis para automação de navegador."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from threading import Lock
from typing import Any, Mapping, TypeAlias
from uuid import uuid4

from jarvis.security.authorization import AuthorizationReceipt


@dataclass(frozen=True, slots=True)
class BrowserTab:
    url: str
    title: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    is_active: bool = False


@dataclass(frozen=True, slots=True)
class BrowserElement:
    selector: str
    tag_name: str
    text: str | None = None
    attributes: dict[str, str] = field(default_factory=dict, repr=False)
    is_visible: bool = True
    is_enabled: bool = True
    bounding_box: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class BrowserPageSnapshot:
    url: str
    title: str
    tabs: tuple[BrowserTab, ...]
    selected_text: str | None = None
    viewport: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class BrowserNavigationResult:
    url: str
    title: str
    success: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    success: bool
    message: str
    element: BrowserElement | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserExtractResult:
    success: bool
    data: dict[str, Any] | list[Any] | str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedBrowserTarget:
    """Identidade preparada de um alvo no navegador."""
    public_target: str
    tab_id: str
    selector: str | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)

    def binding_identity(self) -> str:
        parts = [self.tab_id]
        if self.selector:
            parts.append(self.selector)
        if self.url:
            parts.append(self.url)
        return "|".join(parts)


class BrowserOperationKind(str, Enum):
    NAVIGATE = "NAVIGATE"
    TYPE_TEXT = "TYPE_TEXT"
    FILL = "FILL"


def _require_string(name: str, value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} deve ser uma string.")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} não pode ser vazio.")
    return value


def _operation_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedNavigateOperation:
    tab_id: str
    url: str

    def __post_init__(self) -> None:
        _require_string("tab_id", self.tab_id)
        _require_string("url", self.url)

    @property
    def kind(self) -> BrowserOperationKind:
        return BrowserOperationKind.NAVIGATE

    @property
    def tool_name(self) -> str:
        return "browser_navigate"

    @property
    def public_target(self) -> str:
        return f"browser:tab:{self.tab_id}:{self.url}"

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "kind": self.kind.value,
            "tab_id": self.tab_id,
            "url": self.url,
        }

    def binding_digest(self) -> str:
        return _operation_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class PreparedTypeTextOperation:
    tab_id: str
    page_url: str
    selector: str
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_string("tab_id", self.tab_id)
        _require_string("page_url", self.page_url)
        _require_string("selector", self.selector)
        _require_string("text", self.text, allow_empty=True)

    @property
    def kind(self) -> BrowserOperationKind:
        return BrowserOperationKind.TYPE_TEXT

    @property
    def tool_name(self) -> str:
        return "browser_type"

    @property
    def public_target(self) -> str:
        return f"browser:tab:{self.tab_id}:{self.page_url}:{self.selector}"

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "kind": self.kind.value,
            "tab_id": self.tab_id,
            "page_url": self.page_url,
            "selector": self.selector,
            "text": self.text,
        }

    def binding_digest(self) -> str:
        return _operation_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class PreparedFillOperation:
    tab_id: str
    page_url: str
    selector: str
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_string("tab_id", self.tab_id)
        _require_string("page_url", self.page_url)
        _require_string("selector", self.selector)
        _require_string("text", self.text, allow_empty=True)

    @property
    def kind(self) -> BrowserOperationKind:
        return BrowserOperationKind.FILL

    @property
    def tool_name(self) -> str:
        return "browser_fill"

    @property
    def public_target(self) -> str:
        return f"browser:tab:{self.tab_id}:{self.page_url}:{self.selector}"

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "kind": self.kind.value,
            "tab_id": self.tab_id,
            "page_url": self.page_url,
            "selector": self.selector,
            "text": self.text,
        }

    def binding_digest(self) -> str:
        return _operation_digest(self.to_dict())


PreparedBrowserOperation: TypeAlias = (
    PreparedNavigateOperation
    | PreparedTypeTextOperation
    | PreparedFillOperation
)


def deserialize_prepared_browser_operation(
    value: Mapping[str, object],
) -> PreparedBrowserOperation:
    if not isinstance(value, Mapping):
        raise ValueError("Operação preparada deve ser um objeto.")
    raw = dict(value)
    if type(raw.get("version")) is not int or raw.get("version") != 1:
        raise ValueError("Versão de operação preparada inválida.")
    kind = raw.get("kind")
    if type(kind) is not str:
        raise ValueError("Tipo de operação preparada ausente ou inválido.")
    if kind == BrowserOperationKind.NAVIGATE.value:
        if set(raw) != {"version", "kind", "tab_id", "url"}:
            raise ValueError("Campos de NAVIGATE inválidos.")
        return PreparedNavigateOperation(
            tab_id=_require_string("tab_id", raw["tab_id"]),
            url=_require_string("url", raw["url"]),
        )
    if kind in {
        BrowserOperationKind.TYPE_TEXT.value,
        BrowserOperationKind.FILL.value,
    }:
        if set(raw) != {
            "version",
            "kind",
            "tab_id",
            "page_url",
            "selector",
            "text",
        }:
            raise ValueError(f"Campos de {kind} inválidos.")
        operation_type = (
            PreparedTypeTextOperation
            if kind == BrowserOperationKind.TYPE_TEXT.value
            else PreparedFillOperation
        )
        return operation_type(
            tab_id=_require_string("tab_id", raw["tab_id"]),
            page_url=_require_string("page_url", raw["page_url"]),
            selector=_require_string("selector", raw["selector"]),
            text=_require_string("text", raw["text"], allow_empty=True),
        )
    raise ValueError("Tipo de operação preparada desconhecido.")


def serialize_prepared_browser_operation(
    operation: PreparedBrowserOperation,
) -> str:
    if not isinstance(
        operation,
        (
            PreparedNavigateOperation,
            PreparedTypeTextOperation,
            PreparedFillOperation,
        ),
    ):
        raise ValueError("Operação preparada inválida.")
    return json.dumps(
        operation.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_prepared_browser_operation_json(
    encoded: str,
) -> PreparedBrowserOperation:
    if type(encoded) is not str:
        raise ValueError("Operação serializada deve ser uma string.")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Operação serializada contém campo duplicado.")
            result[key] = item
        return result

    try:
        value = json.loads(encoded, object_pairs_hook=reject_duplicates)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Operação serializada inválida.") from error
    return deserialize_prepared_browser_operation(value)


_BROWSER_AUTHORITY = object()
_AUTHORIZED_OPERATION_REGISTRY: dict[int, "AuthorizedBrowserOperation"] = {}
_AUTHORIZED_OPERATION_REGISTRY_LOCK = Lock()


class _ExecutionState:
    def __init__(self) -> None:
        self._state = "ready"
        self._lock = Lock()

    def begin(self) -> None:
        with self._lock:
            if self._state != "ready":
                raise PermissionError("Operação autorizada já foi utilizada.")
            self._state = "executing"

    def require_executing(self) -> None:
        with self._lock:
            if self._state != "executing":
                raise PermissionError("Operação autorizada não está em execução.")

    def claim_effect(self) -> None:
        with self._lock:
            if self._state != "executing":
                raise PermissionError("Efeito autorizado já foi consumido.")
            self._state = "effect_claimed"

    def finish(self) -> None:
        with self._lock:
            self._state = "finished"


@dataclass(frozen=True, slots=True)
class AuthorizedNavigateOperation:
    operation: PreparedNavigateOperation
    tool_name: str
    target: str
    binding_digest: str
    _authority: object = field(repr=False, compare=False)
    _execution: _ExecutionState = field(
        default_factory=_ExecutionState, repr=False, compare=False
    )

    @property
    def tab_id(self) -> str:
        return self.operation.tab_id

    @property
    def url(self) -> str:
        return self.operation.url

    def require_valid(self) -> None:
        _require_authorized_operation(self, "browser_navigate")

    def begin_execution(self) -> None:
        self.require_valid()
        self._execution.begin()

    def require_executing(self) -> None:
        self.require_valid()
        self._execution.require_executing()

    def claim_effect(self) -> None:
        self.require_valid()
        self._execution.claim_effect()

    def finish_execution(self) -> None:
        self._execution.finish()
        _unregister_authorized_operation(self)


@dataclass(frozen=True, slots=True)
class AuthorizedTypeTextOperation:
    operation: PreparedTypeTextOperation
    tool_name: str
    target: str
    binding_digest: str
    _authority: object = field(repr=False, compare=False)
    _execution: _ExecutionState = field(
        default_factory=_ExecutionState, repr=False, compare=False
    )

    @property
    def tab_id(self) -> str:
        return self.operation.tab_id

    @property
    def selector(self) -> str:
        return self.operation.selector

    @property
    def text(self) -> str:
        return self.operation.text

    @property
    def page_url(self) -> str:
        return self.operation.page_url

    def require_valid(self) -> None:
        _require_authorized_operation(self, "browser_type")

    def begin_execution(self) -> None:
        self.require_valid()
        self._execution.begin()

    def require_executing(self) -> None:
        self.require_valid()
        self._execution.require_executing()

    def claim_effect(self) -> None:
        self.require_valid()
        self._execution.claim_effect()

    def finish_execution(self) -> None:
        self._execution.finish()
        _unregister_authorized_operation(self)


@dataclass(frozen=True, slots=True)
class AuthorizedFillOperation:
    operation: PreparedFillOperation
    tool_name: str
    target: str
    binding_digest: str
    _authority: object = field(repr=False, compare=False)
    _execution: _ExecutionState = field(
        default_factory=_ExecutionState, repr=False, compare=False
    )

    @property
    def tab_id(self) -> str:
        return self.operation.tab_id

    @property
    def selector(self) -> str:
        return self.operation.selector

    @property
    def text(self) -> str:
        return self.operation.text

    @property
    def page_url(self) -> str:
        return self.operation.page_url

    def require_valid(self) -> None:
        _require_authorized_operation(self, "browser_fill")

    def begin_execution(self) -> None:
        self.require_valid()
        self._execution.begin()

    def require_executing(self) -> None:
        self.require_valid()
        self._execution.require_executing()

    def claim_effect(self) -> None:
        self.require_valid()
        self._execution.claim_effect()

    def finish_execution(self) -> None:
        self._execution.finish()
        _unregister_authorized_operation(self)


AuthorizedBrowserOperation: TypeAlias = (
    AuthorizedNavigateOperation
    | AuthorizedTypeTextOperation
    | AuthorizedFillOperation
)


def _require_authorized_operation(
    authorized: AuthorizedBrowserOperation,
    expected_tool_name: str,
) -> None:
    operation = authorized.operation
    with _AUTHORIZED_OPERATION_REGISTRY_LOCK:
        registered = _AUTHORIZED_OPERATION_REGISTRY.get(id(authorized))
        if (
            registered is not authorized
            or authorized._authority is not _BROWSER_AUTHORITY
            or authorized.tool_name != expected_tool_name
            or operation.tool_name != expected_tool_name
            or authorized.target != operation.public_target
            or authorized.binding_digest != operation.binding_digest()
        ):
            raise PermissionError(
                "Operação do navegador não autorizada ou incompatível."
            )


def _register_authorized_operation(
    authorized: AuthorizedBrowserOperation,
) -> AuthorizedBrowserOperation:
    with _AUTHORIZED_OPERATION_REGISTRY_LOCK:
        _AUTHORIZED_OPERATION_REGISTRY[id(authorized)] = authorized
    return authorized


def _unregister_authorized_operation(
    authorized: AuthorizedBrowserOperation,
) -> None:
    with _AUTHORIZED_OPERATION_REGISTRY_LOCK:
        if _AUTHORIZED_OPERATION_REGISTRY.get(id(authorized)) is authorized:
            _AUTHORIZED_OPERATION_REGISTRY.pop(id(authorized), None)


def bind_authorized_browser_operation(
    operation: PreparedBrowserOperation,
    *,
    tool_name: str,
    target: str,
    binding_digest: str | None,
    authorization: AuthorizationReceipt | None,
) -> AuthorizedBrowserOperation:
    if (
        tool_name != operation.tool_name
        or target != operation.public_target
        or binding_digest is None
        or binding_digest != operation.binding_digest()
        or authorization is None
        or not authorization.claim(tool_name, target, binding_digest)
    ):
        raise PermissionError("Operação preparada não corresponde à autorização.")
    values = (
        operation,
        tool_name,
        target,
        binding_digest,
        _BROWSER_AUTHORITY,
        _ExecutionState(),
    )
    if isinstance(operation, PreparedNavigateOperation):
        return _register_authorized_operation(
            AuthorizedNavigateOperation(*values)
        )
    if isinstance(operation, PreparedTypeTextOperation):
        return _register_authorized_operation(
            AuthorizedTypeTextOperation(*values)
        )
    if isinstance(operation, PreparedFillOperation):
        return _register_authorized_operation(AuthorizedFillOperation(*values))
    raise PermissionError("Tipo de operação preparada não autorizado.")
