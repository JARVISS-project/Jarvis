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
    assert manager.authorize("delete_file", "teste.txt", RiskLevel.HIGH)
    request = provider.requests[0]
    assert request.action == "delete_file"
    assert request.target == "teste.txt"
    assert request.risk is RiskLevel.HIGH
    assert request.prompt_text.startswith("Entendi.")
    assert "excluir teste ponto txt" in request.prompt_text
