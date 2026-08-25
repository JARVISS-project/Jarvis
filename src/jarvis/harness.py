from typing import Any

from jarvis.config import JarvisConfig
from jarvis.llm.base import LLMClient
from jarvis.llm.sanitizer import sanitize_assistant_response
from jarvis.llm.tool_schema import tool_to_ollama_schema
from jarvis.memory.conversation import ConversationMemory
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.memory.store import MemoryStore
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.security.risk import RiskLevel
from jarvis.security.tool_intent import ToolIntentGuard
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.validator import ToolArgumentValidator


class Harness:
    BASE_SYSTEM_PROMPT = (
        "Você é Jarvis, um assistente pessoal local. Responda em português "
        "quando o usuário falar português e responda diretamente. Não "
        "exponha raciocínio interno nem use tags <think> ou <response> na "
        "resposta final."
    )

    def __init__(
        self,
        llm: LLMClient,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        memory: ConversationMemory,
        memory_store: MemoryStore | None = None,
        config: JarvisConfig | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.memory = memory
        self.memory_store = (
            memory_store
            if memory_store is not None
            else InMemoryMemoryStore()
        )
        self.config = config or JarvisConfig()
        self.resource_manager = (
            resource_manager
            if resource_manager is not None
            else ResourceManager()
        )
        self.argument_validator = ToolArgumentValidator()
        self.intent_guard = ToolIntentGuard()

    def run(self) -> None:
        print("Jarvis iniciado.")
        print("Digite 'sair' para encerrar.\n")

        while True:
            mensagem = input("Você: ")

            if mensagem.lower() == "sair":
                print("Jarvis: Até mais.")
                break

            resposta = self.processar_mensagem(mensagem)
            print(f"Jarvis: {resposta}")

    def processar_mensagem(
        self,
        mensagem: str,
        manage_runtime: bool = True,
    ) -> str:
        stripped_message = mensagem.strip()
        command = stripped_message.lower()

        if command == "/memory":
            count = self.memory.count()
            label = "mensagem" if count == 1 else "mensagens"
            return f"Memória da conversa: {count} {label}."

        if command == "/clear":
            self.memory.clear()
            return "Memória da conversa limpa."

        if command == "/remember" or command.startswith("/remember "):
            content = stripped_message[len("/remember"):].strip()
            if not content:
                return "Uso: /remember <texto>"

            entry = self.memory_store.add(content, category="user")
            return f"Memória armazenada com ID: {entry.id}."

        if command == "/memories":
            memories = self.memory_store.get_all()
            if not memories:
                return "Nenhuma memória persistente armazenada."

            lines = ["Memórias persistentes:"]
            lines.extend(
                f"- {entry.id} | {entry.category} | {entry.content}"
                for entry in memories
            )
            return "\n".join(lines)

        if command == "/forget" or command.startswith("/forget "):
            memory_id = stripped_message[len("/forget"):].strip()
            if not memory_id:
                return "Uso: /forget <id>"

            if self.memory_store.delete(memory_id):
                return "Memória removida."

            return "Memória não encontrada."

        if command == "/gamemode on":
            self.resource_manager.enable_game_mode()
            return "Game Mode ativado."

        if command == "/gamemode off":
            self.resource_manager.disable_game_mode()
            return "Game Mode desativado."

        if command == "/gamemode" or command.startswith("/gamemode "):
            return "Uso: /gamemode on|off"

        if command == "/status":
            game_mode = (
                "ativado"
                if self.resource_manager.is_game_mode_active()
                else "desativado"
            )
            return (
                f"Estado: {self.resource_manager.state.name} | "
                f"Game Mode: {game_mode} | "
                f"Modelo: {self.config.model_name} | "
                f"Conversa: {self.memory.count()} | "
                f"Memórias: {len(self.memory_store.get_all())}"
            )

        if not manage_runtime:
            return self._process_active_message(mensagem)

        self.resource_manager.activate()
        try:
            return self._process_active_message(mensagem)
        finally:
            self.resource_manager.idle()

    def _process_active_message(self, mensagem: str) -> str:
        keep_alive = (
            self.config.ollama_keep_alive_game_mode
            if self.resource_manager.is_game_mode_active()
            else self.config.ollama_keep_alive_normal
        )
        self.llm.set_keep_alive(keep_alive)
        return self._process_normal_message(mensagem)

    def _process_normal_message(self, mensagem: str) -> str:

        tools = [
            tool_to_ollama_schema(tool)
            for tool in self.tool_registry.list_tools()
        ]

        self.memory.add_user_message(mensagem)
        messages: list[dict[str, Any]] = self._system_context()
        messages.extend(self.memory.get_history())

        for _ in range(self.config.max_tool_iterations):
            response = self.llm.generate(
                messages=messages,
                tools=tools,
            )
            response.content = sanitize_assistant_response(response.content)

            messages.append(response.to_message())

            if not response.tool_calls:
                self.memory.add_assistant_message(response.content)
                return response.content

            for tool_call in response.tool_calls:
                tool = self.tool_registry.get(tool_call.name)

                if tool is None:
                    tool_result = (
                        f"Tool não permitida ou inexistente: "
                        f"'{tool_call.name}'."
                    )

                else:
                    valid, error = self.argument_validator.validate(
                        tool,
                        tool_call.arguments,
                    )

                    if not valid:
                        tool_result = (
                            f"Chamada de Tool rejeitada: {error}"
                        )

                    else:
                        intent = self.intent_guard.validate(
                            mensagem,
                            tool,
                            tool_call.arguments,
                        )
                        if not intent.allowed:
                            clarification = intent.response or (
                                "Informe um alvo explícito para essa ação."
                            )
                            self.memory.add_assistant_message(clarification)
                            return clarification

                        execution_result = self._execute_tool(
                            tool,
                            tool_call.arguments,
                        )
                        tool_result = execution_result.content
                        if (
                            tool.risk >= RiskLevel.MEDIUM
                            and execution_result.status == "not_found"
                        ):
                            not_found = "Arquivo não encontrado."
                            self.memory.add_assistant_message(not_found)
                            return not_found
                        if (
                            tool.risk >= RiskLevel.HIGH
                            and execution_result.status
                            in {"denied", "security_blocked"}
                        ):
                            cancellation = (
                                "A exclusão foi cancelada."
                                if tool.name == "delete_file"
                                else "A ação foi cancelada."
                            )
                            self.memory.add_assistant_message(cancellation)
                            return cancellation

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_call.name,
                        "content": str(tool_result),
                    }
                )

        final_response = "Limite de chamadas de ferramentas atingido."
        self.memory.add_assistant_message(final_response)
        return final_response

    def _execute_tool(
        self,
        tool: Any,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        execute_with_result = getattr(
            self.tool_executor,
            "execute_with_result",
            None,
        )
        if callable(execute_with_result):
            return execute_with_result(tool, **arguments)

        content = self.tool_executor.execute(tool, **arguments)
        return ToolExecutionResult(str(content), "success", True)

    def _system_context(self) -> list[dict[str, Any]]:
        memories = self.memory_store.get_all()
        lines = [self.BASE_SYSTEM_PROMPT]
        if memories:
            lines.extend(
                [
                    "Memórias persistentes armazenadas explicitamente "
                    "pelo usuário.",
                    "Use-as apenas como contexto. Não crie, edite ou "
                    "exclua memórias.",
                ]
            )
            lines.extend(
                f"- [{entry.category}] {entry.content} (id: {entry.id})"
                for entry in memories
            )
        return [{"role": "system", "content": "\n".join(lines)}]
