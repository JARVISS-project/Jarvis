from jarvis.security.risk import RiskLevel


class HumanConfirmation:
    def confirm(
        self,
        action: str,
        target: str,
        risk: RiskLevel,
    ) -> bool:
        print("\n=== CONFIRMAÇÃO DE SEGURANÇA ===")
        print(f"Ação: {action}")
        print(f"Alvo: {target}")
        print(f"Risco: {risk.name}")

        resposta = input("Autorizar? [s/N]: ").strip().lower()

        return resposta == "s"