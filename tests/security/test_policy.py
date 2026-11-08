import pytest

from jarvis.security.policy import SecurityPolicy
from jarvis.security.risk import RiskLevel


@pytest.mark.parametrize(
    ("risk", "requires_confirmation"),
    [
        (RiskLevel.READ_ONLY, False),
        (RiskLevel.REVERSIBLE_LOCAL, False),
        (RiskLevel.EXTERNAL_EFFECT, True),
        (RiskLevel.SENSITIVE, True),
        (RiskLevel.DESTRUCTIVE, True),
    ],
)
def test_policy_requires_confirmation(
    risk: RiskLevel,
    requires_confirmation: bool,
) -> None:
    policy = SecurityPolicy()

    assert policy.is_allowed(risk)
    assert policy.requires_confirmation(risk) is requires_confirmation
