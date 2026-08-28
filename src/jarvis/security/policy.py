from jarvis.security.risk import RiskLevel


class SecurityPolicy:
    def requires_confirmation(self, risk: RiskLevel) -> bool:
        return risk is RiskLevel.CRITICAL

    def is_allowed(self, risk: RiskLevel) -> bool:
        return risk <= RiskLevel.CRITICAL
