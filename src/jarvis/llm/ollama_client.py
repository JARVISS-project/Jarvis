from ollama import Client

from jarvis.llm.base import LLMClient


class OllamaLLM(LLMClient):
    def __init__(self, model: str = "qwen3:4b") -> None:
        self.model = model
        self.client = Client(host="http://localhost:11434")

    def generate(self, prompt: str) -> str:
        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        return response.message.content