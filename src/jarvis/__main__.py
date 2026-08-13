def main() -> None:
    print("Jarvis iniciado.")
    print("Digite 'sair' para encerrar.")

    while True:
        mensagem = input("Você: ")

        if mensagem.lower() == "sair":
            print("Jarvis: Até mais.")
            break

        print(f"Jarvis: Você disse '{mensagem}'.")


if __name__ == "__main__":
    main()