from jarvis.security.confirmation import HumanConfirmation
from jarvis.security.policy import SecurityPolicy
from jarvis.security.risk import RiskLevel


class SafetyManager:
    def __init__(self) -> None:
        self.policy = SecurityPolicy()
        self.confirmation = HumanConfirmation()

    def authorize(
        self,
        action: str,
        target: str,
        risk: RiskLevel,
    ) -> bool:

        if not self.policy.is_allowed(risk):
            print("\n[AÇÃO BLOQUEADA]")
            print(f"Ação: {action}")
            print(f"Alvo: {target}")
            print(f"Risco: {risk.name}")
            return False

        if self.policy.requires_confirmation(risk):
            return self.confirmation.confirm(
                action=action,
                target=target,
                risk=risk,
            )

        return True