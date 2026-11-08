"""Fundação segura da integração Google Workspace."""

from jarvis.integrations.google.auth import GoogleAuthManager
from jarvis.integrations.google.models import ConnectionState, ConnectionStatus
from jarvis.integrations.google.scopes import AUTHENTICATION_SCOPES

__all__ = [
    "AUTHENTICATION_SCOPES",
    "ConnectionState",
    "ConnectionStatus",
    "GoogleAuthManager",
]
