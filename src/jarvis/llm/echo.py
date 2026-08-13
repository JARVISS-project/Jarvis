from jarvis.llm.base import LLMClient


class EchoLLM(LLMClient):
    def generate(self, prompt: str) -> str:
        return f"Você disse: {prompt}"