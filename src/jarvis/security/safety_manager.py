from jarvis.security.confirmation import ConfirmationProvider, HumanConfirmation
from jarvis.security.policy import SecurityPolicy
from jarvis.security.risk import RiskLevel
from jarvis.security.confirmation_request import ConfirmationRequest
from jarvis.security.confirmation import security_prompt
from jarvis.security.authorization import (
    AuthorizationGrant,
    AuthorizationGrantStore,
)


class SafetyManager:
    def __init__(
        self,
        confirmation: ConfirmationProvider | None = None,
        grants: AuthorizationGrantStore | None = None,
    ) -> None:
        self.policy = SecurityPolicy()
        self.confirmation = confirmation or HumanConfirmation()
        self.grants = grants or AuthorizationGrantStore()

    def authorize_invocation(
        self,
        action: str,
        target: str,
        risk: RiskLevel,
    ) -> AuthorizationGrant | None:
        if not self.policy.is_allowed(risk):
            self.invalidate_authorizations()
            return None
        if not self.policy.requires_confirmation(risk):
            return self.grants.issue(action, target)
        confirmed = self.confirmation.confirm_request(
            ConfirmationRequest(
                action=action,
                target=target,
                risk=risk,
                prompt_text=security_prompt(action, target, risk=risk),
            )
        )
        if not confirmed:
            self.invalidate_authorizations()
            return None
        return self.grants.issue(action, target)

    def consume_grant(
        self,
        grant: AuthorizationGrant,
        action: str,
        target: str,
        interaction_id: str | None = None,
        session_id: str | None = None,
        arguments_digest: str | None = None,
        preparation_digest: str | None = None,
    ) -> bool:
        return self.grants.consume(
            grant,
            action,
            target,
            interaction_id,
            session_id,
            arguments_digest,
            preparation_digest,
        )

    def grant_explicit_authorization(
        self,
        action: str,
        target: str,
        risk: RiskLevel,
        interaction_id: str,
        session_id: str | None = None,
        arguments_digest: str | None = None,
        preparation_digest: str | None = None,
    ) -> AuthorizationGrant | None:
        if (
            not interaction_id
            or not self.policy.is_allowed(risk)
            or not self.policy.requires_confirmation(risk)
        ):
            return None
        return self.grants.issue(
            action,
            target,
            interaction_id,
            session_id,
            arguments_digest,
            preparation_digest,
        )

    def invalidate_session(self, session_id: str) -> None:
        self.grants.invalidate_session(session_id)

    def invalidate_authorizations(self) -> None:
        self.grants.invalidate_all()
        self.confirmation.clear_pending_confirmation()

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
            grant = self.authorize_invocation(action, target, risk)
            if grant is None:
                return False
            return self.consume_grant(grant, action, target)

        return True
