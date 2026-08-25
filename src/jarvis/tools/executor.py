from typing import Any

from jarvis.security.audit import AuditLogger
from jarvis.security.capabilities import CapabilityPolicy
from jarvis.security.safety_manager import SafetyManager
from jarvis.security.risk import RiskLevel
from jarvis.tools.base import Tool
from jarvis.tools.execution_result import ToolExecutionResult


class ToolExecutor:
    def __init__(
        self,
        safety_manager: SafetyManager,
        audit_logger: AuditLogger,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        self.safety_manager = safety_manager
        self.audit_logger = audit_logger
        self.capability_policy = capability_policy

    def execute(self, tool: Tool, **kwargs: Any) -> str:
        """Execute a tool while preserving the public string result."""
        return self.execute_with_result(tool, **kwargs).content

    def execute_with_result(
        self,
        tool: Tool,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        target = "desconhecido"
        authorized = False

        try:
            if self.capability_policy is not None:
                self.capability_policy.require_tool(tool.name)
            target = tool.get_target(**kwargs)
            tool.preflight(**kwargs)
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

            result = tool.execute(**kwargs)
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=True,
                status="success",
            )
            return ToolExecutionResult(result, "success", True)

        except PermissionError as error:
            result = f"Ação bloqueada por segurança: {error}"
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=authorized,
                status="security_blocked",
            )
            return ToolExecutionResult(result, "security_blocked", authorized)

        except FileNotFoundError as error:
            result = f"Arquivo ou diretório não encontrado: {error}"
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=authorized,
                status="not_found",
            )
            return ToolExecutionResult(result, "not_found", authorized)

        except Exception as error:
            result = f"Erro ao executar a Tool '{tool.name}': {error}"
            self.audit_logger.log(
                tool_name=tool.name,
                target=target,
                authorized=authorized,
                status="error",
            )
            return ToolExecutionResult(result, "error", authorized)

    def execute_with_grant(
        self,
        tool: Tool,
        grant: Any,
        interaction_id: str,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        target = "desconhecido"
        authorized = False
        try:
            if self.capability_policy is not None:
                self.capability_policy.require_tool(tool.name)
            target = tool.get_target(**kwargs)
            tool.preflight(**kwargs)
            authorized = self.safety_manager.consume_grant(
                grant, tool.name, target, interaction_id
            )
            if not authorized:
                self.audit_logger.log(
                    tool.name, target, False, "denied"
                )
                return ToolExecutionResult(
                    "Ação não autorizada.", "denied", False
                )
            result = tool.execute(**kwargs)
            self.audit_logger.log(tool.name, target, True, "success")
            return ToolExecutionResult(result, "success", True)
        except PermissionError as error:
            self.audit_logger.log(
                tool.name, target, authorized, "security_blocked"
            )
            return ToolExecutionResult(
                f"Ação bloqueada por segurança: {error}",
                "security_blocked",
                authorized,
            )
        except Exception as error:
            self.audit_logger.log(tool.name, target, authorized, "error")
            return ToolExecutionResult(
                f"Erro ao executar a Tool '{tool.name}': {error}",
                "error",
                authorized,
            )
