"""Composição local da fundação Google."""

from pathlib import Path

from jarvis.integrations.google.auth import GoogleAuthManager
from jarvis.integrations.google.client import (
    GoogleOAuthFlowClient,
    load_google_credentials,
)
from jarvis.integrations.google.loopback import LoopbackOAuthReceiver
from jarvis.integrations.google.operation_lock import GoogleOperationLock
from jarvis.integrations.google.storage import (
    ProtectedFileCredentialStore,
    WindowsDPAPIProtector,
)


def build_google_auth_manager(
    credential_path: Path = Path("data/google/credentials.bin"),
) -> GoogleAuthManager:
    store = ProtectedFileCredentialStore(
        credential_path,
        WindowsDPAPIProtector(),
    )
    return GoogleAuthManager(
        store=store,
        oauth_flow_factory=GoogleOAuthFlowClient,
        credential_loader=load_google_credentials,
        callback_receiver_factory=LoopbackOAuthReceiver,
        operation_lock_factory=GoogleOperationLock,
    )
