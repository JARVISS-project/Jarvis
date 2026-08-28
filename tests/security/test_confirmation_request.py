from jarvis.security.confirmation import ConfirmationProvider
from jarvis.security.confirmation_request import ConfirmationRequest
from jarvis.security.risk import RiskLevel
from jarvis.security.safety_manager import SafetyManager


class RecordingProvider(ConfirmationProvider):
    def __init__(self) -> None:
        self.requests: list[ConfirmationRequest] = []

    def confirm(self, action: str, target: str, risk: RiskLevel) -> bool:
        raise AssertionError("SafetyManager deve enviar request tipado")

    def confirm_request(self, request: ConfirmationRequest) -> bool:
        self.requests.append(request)
        return True


def test_safety_manager_builds_combined_understanding_and_risk_prompt() -> None:
    provider = RecordingProvider()
    manager = SafetyManager(provider)
    assert manager.authorize("critical_action", "teste.txt", RiskLevel.CRITICAL)
    request = provider.requests[0]
    assert request.action == "critical_action"
    assert request.target == "teste.txt"
    assert request.risk is RiskLevel.CRITICAL
    assert request.prompt_text.startswith("Entendi.")


def test_safety_manager_does_not_prompt_through_high() -> None:
    provider = RecordingProvider()
    manager = SafetyManager(provider)

    for risk in (
        RiskLevel.SAFE,
        RiskLevel.LOW,
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
    ):
        grant = manager.authorize_invocation("action", "target", risk)
        assert grant is not None

    assert provider.requests == []
