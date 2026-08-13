from jarvis.harness import Harness
from jarvis.llm.ollama_client import OllamaLLM


def main() -> None:
    llm = OllamaLLM()
    harness = Harness(llm)
    harness.run()


if __name__ == "__main__":
    main()