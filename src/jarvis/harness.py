from jarvis.llm.base import LLMClient


class Harness:

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

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
        return self.llm.generate(mensagem)