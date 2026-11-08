from jarvis.security.risk import RiskLevel


class SecurityPolicy:
    def requires_confirmation(self, risk: RiskLevel) -> bool:
        return risk in (
            RiskLevel.EXTERNAL_EFFECT,
            RiskLevel.SENSITIVE,
            RiskLevel.DESTRUCTIVE,
        )

    def is_allowed(self, risk: RiskLevel) -> bool:
        # Unknown risk should be rejected, but enum enforcement guarantees known risks.
        # Fallback to false if somehow an unknown risk is passed
        return isinstance(risk, RiskLevel) and risk in RiskLevel
