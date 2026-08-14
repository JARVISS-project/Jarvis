from jarvis.llm.base import LLMClient
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.llm.tool_schema import tool_to_ollama_schema
from jarvis.tools.validator import ToolArgumentValidator


class Harness:
    def __init__(
        self,
        llm: LLMClient,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.argument_validator = ToolArgumentValidator()

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

    def processar_mensagem(self, mensagem: str) -> str:
        tools = [
            tool_to_ollama_schema(tool)
            for tool in self.tool_registry.list_tools()
        ]

        messages = [
            {
                "role": "user",
                "content": mensagem,
            }
        ]

        max_tool_iterations = 5

        for _ in range(max_tool_iterations):
            response = self.llm.generate(
                messages=messages,
                tools=tools,
            )

            messages.append(response.to_message())

            if not response.tool_calls:
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
                        tool_result = self.tool_executor.execute(
                            tool,
                            **tool_call.arguments,
                        )

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_call.name,
                        "content": str(tool_result),
                    }
                )

        return "Limite de chamadas de ferramentas atingido."