import pytest

from jarvis.security.policy import SecurityPolicy
from jarvis.security.risk import RiskLevel


@pytest.mark.parametrize(
    ("risk", "requires_confirmation"),
    [
        (RiskLevel.SAFE, False),
        (RiskLevel.LOW, False),
        (RiskLevel.MEDIUM, False),
        (RiskLevel.HIGH, False),
        (RiskLevel.CRITICAL, True),
    ],
)
def test_policy_only_confirms_critical(
    risk: RiskLevel,
    requires_confirmation: bool,
) -> None:
    policy = SecurityPolicy()

    assert policy.is_allowed(risk)
    assert policy.requires_confirmation(risk) is requires_confirmation
