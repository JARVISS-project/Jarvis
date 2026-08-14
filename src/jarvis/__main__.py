from pathlib import Path

from jarvis.harness import Harness
from jarvis.llm.ollama_client import OllamaLLM
from jarvis.security.filesystem import FileSystemGuard
from jarvis.security.safety_manager import SafetyManager
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.list_files import ListFilesTool
from jarvis.tools.read_file import ReadFileTool
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.test_tool import TestTool


def main() -> None:
    # Modelo
    llm = OllamaLLM()

    # Segurança
    safety_manager = SafetyManager()
    tool_executor = ToolExecutor(safety_manager)

    filesystem = FileSystemGuard(
        base_directory=Path.cwd()
    )

    # Registro de Tools
    tool_registry = ToolRegistry()

    tool_registry.register(
        TestTool()
    )

    tool_registry.register(
        ListFilesTool(filesystem)
    )

    tool_registry.register(
        ReadFileTool(filesystem)
    )

    # Harness
    harness = Harness(
        llm=llm,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
    )

    harness.run()


if __name__ == "__main__":
    main()