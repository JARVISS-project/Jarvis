"""Scopes Google explícitos; novos serviços devem declarar os seus aqui."""

# OIDC is the least-privileged scope that proves user consent. No profile or
# e-mail claim is consumed or persisted in this foundation.
OPENID_SCOPE = "openid"
AUTHENTICATION_SCOPES = (OPENID_SCOPE,)

# Reserved for the next Calendar sprint. It is intentionally not requested by
# GoogleAuthManager today.
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
