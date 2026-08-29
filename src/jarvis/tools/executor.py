from collections.abc import Callable
from typing import Any

from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.security.risk import RiskLevel
from jarvis.security.confirmation_session import digest_arguments
from jarvis.tools.base import Tool
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.execution_observer import ToolExecutionObserver
from jarvis.tools.execution_verifier import ToolExecutionVerifier


class ToolExecutor:
    def __init__(
        self,
        safety_manager: SafetyManager,
        audit_logger: AuditLogger,
        capability_policy: CapabilityPolicy | None = None,
        observer: ToolExecutionObserver | None = None,
        confirmation_requester: Callable[..., ToolExecutionResult] | None = None,
        verifier: ToolExecutionVerifier | None = None,
    ) -> None:
        self.safety_manager = safety_manager
        self.audit_logger = audit_logger
        self.capability_policy = capability_policy
        self.observer = observer
        self.confirmation_requester = confirmation_requester
        self.verifier = verifier

    def execute(self, tool: Tool, **kwargs: Any) -> str:
        """Execute a tool while preserving the public string result."""
        return self.execute_with_result(tool, **kwargs).content

    def execute_with_result(
        self,
        tool: Tool,
        *,
        _interaction_id: str | None = None,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        target = "desconhecido"
        authorized = False
        execution_started = False

        try:
            if self.capability_policy is not None:
                self.capability_policy.require_tool(tool.name)
            preparation = tool.prepare(**kwargs)
            target = preparation.target
            tool.preflight_prepared(preparation, **kwargs)
            if (
                self.confirmation_requester is not None
                and self.safety_manager.policy.requires_confirmation(tool.risk)
            ):
                result = self.confirmation_requester(
                    tool,
                    target,
                    dict(kwargs),
                    _interaction_id,
                    preparation.binding_digest,
                )
                self.audit_logger.log(
                    tool_name=tool.name,
                    target=target,
                    authorized=False,
                    status=result.status,
                )
                return result
            authorize_invocation = getattr(
                self.safety_manager, "authorize_invocation", None
            )
            if tool.risk >= RiskLevel.MEDIUM:
                if not callable(authorize_invocation):
                    authorized = False
                else:
                    grant = authorize_invocation(tool.name, target, tool.risk)
                    authorized = grant is not None
                    if authorized:
                        authorized = self.safety_manager.consume_grant(
                            grant, tool.name, target
                        )
            else:
                # Compatibilidade apenas para doubles antigos de teste.
                authorized = self.safety_manager.authorize(
                    action=tool.name,
                    target=target,
                    risk=tool.risk,
                )

            if not authorized:
                result = "Ação não autorizada."
                self.audit_logger.log(
                    tool_name=tool.name,
                    target=target,
                    authorized=False,
                    status="denied",
                )
                return ToolExecutionResult(result, "denied", False)

            if self.verifier is not None:
                preparation = tool.refresh_prepared(preparation, **kwargs)
            self._notify_started(tool.name)
            execution_started = True
            verification_token = self._begin_verification(
                tool.name,
                target,
                preparation.context,
            )
            try:
                result = tool.execute_prepared(preparation, **kwargs)
            except Exception:
                self._cancel_verification(verification_token)
                raise
            visual_change = self._finish_verification(
                verification_token,
                tool.name,
                target,
                preparation.context,
            )
            execution_result = ToolExecutionResult(
                result,
                "success",
                True,
                visual_change,
            )
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=True,
                status="success",
            )
            self._notify_finished(tool.name, execution_result)
            return execution_result

        except PermissionError as error:
            result = f"Ação bloqueada por segurança: {error}"
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=authorized,
                status="security_blocked",
            )
            execution_result = ToolExecutionResult(
                result, "security_blocked", authorized
            )
            if execution_started:
                self._notify_finished(tool.name, execution_result)
            return execution_result

        except FileNotFoundError as error:
            result = f"Arquivo ou diretório não encontrado: {error}"
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=authorized,
                status="not_found",
            )
            execution_result = ToolExecutionResult(result, "not_found", authorized)
            if execution_started:
                self._notify_finished(tool.name, execution_result)
            return execution_result

        except Exception as error:
            result = f"Erro ao executar a Tool '{tool.name}': {error}"
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=authorized,
                status="error",
            )
            execution_result = ToolExecutionResult(result, "error", authorized)
            if execution_started:
                self._notify_finished(tool.name, execution_result)
            return execution_result

    def execute_with_grant(
        self,
        tool: Tool,
        grant: Any,
        interaction_id: str,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        target = "desconhecido"
        authorized = False
        execution_started = False
        try:
            if self.capability_policy is not None:
                self.capability_policy.require_tool(tool.name)
            preparation = tool.prepare(**kwargs)
            target = preparation.target
            tool.preflight_prepared(preparation, **kwargs)
            authorized = self.safety_manager.consume_grant(
                grant,
                tool.name,
                target,
                interaction_id,
                getattr(grant, "session_id", None),
                digest_arguments(kwargs)
                if getattr(grant, "arguments_digest", None) is not None
                else None,
                preparation.binding_digest,
            )
            if not authorized:
                self.audit_logger.log(
                    tool.name, target, False, "denied"
                )
                return ToolExecutionResult(
                    "Ação não autorizada.", "denied", False
                )
            if self.verifier is not None:
                preparation = tool.refresh_prepared(preparation, **kwargs)
            self._notify_started(tool.name)
            execution_started = True
            verification_token = self._begin_verification(
                tool.name,
                target,
                preparation.context,
            )
            try:
                result = tool.execute_prepared(preparation, **kwargs)
            except Exception:
                self._cancel_verification(verification_token)
                raise
            visual_change = self._finish_verification(
                verification_token,
                tool.name,
                target,
                preparation.context,
            )
            execution_result = ToolExecutionResult(
                result,
                "success",
                True,
                visual_change,
            )
            self.audit_logger.log(tool.name, target, True, "success")
            self._notify_finished(tool.name, execution_result)
            return execution_result
        except PermissionError as error:
            self.audit_logger.log(
                tool.name, target, authorized, "security_blocked"
            )
            execution_result = ToolExecutionResult(
                f"Ação bloqueada por segurança: {error}",
                "security_blocked",
                authorized,
            )
            if execution_started:
                self._notify_finished(tool.name, execution_result)
            return execution_result
        except Exception as error:
            self.audit_logger.log(tool.name, target, authorized, "error")
            execution_result = ToolExecutionResult(
                f"Erro ao executar a Tool '{tool.name}': {error}",
                "error",
                authorized,
            )
            if execution_started:
                self._notify_finished(tool.name, execution_result)
            return execution_result

    def _notify_started(self, tool_name: str) -> None:
        observer = self.observer
        if observer is None:
            return
        try:
            observer.started(tool_name)
        except Exception:
            # Observabilidade nunca deve alterar a autorização/execução.
            return

    def _begin_verification(
        self,
        tool_name: str,
        target: str,
        context: object | None,
    ):  # type: ignore[no-untyped-def]
        verifier = self.verifier
        if verifier is None:
            return None
        try:
            return verifier.before(tool_name, target, context)
        except Exception:
            return None

    def _finish_verification(
        self,
        token,  # type: ignore[no-untyped-def]
        tool_name: str,
        target: str,
        context: object | None,
    ) -> str | None:
        verifier = self.verifier
        if verifier is None or token is None:
            return None
        try:
            result = verifier.after(token, tool_name, target, context)
            return result if result in {"changed", "unchanged"} else None
        except Exception:
            self._cancel_verification(token)
            return None

    def _cancel_verification(self, token) -> None:  # type: ignore[no-untyped-def]
        verifier = self.verifier
        if verifier is None or token is None:
            return
        try:
            verifier.cancel(token)
        except Exception:
            return

    def _notify_finished(
        self,
        tool_name: str,
        result: ToolExecutionResult,
    ) -> None:
        observer = self.observer
        if observer is None:
            return
        try:
            observer.finished(tool_name, result)
        except Exception:
            # UI/telemetria quebrada não pode comprometer o Core.
            return
