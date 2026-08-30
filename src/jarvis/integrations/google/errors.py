"""Erros tipados e deliberadamente sem payloads OAuth."""


class GoogleIntegrationError(RuntimeError):
    """Base segura para falhas da integração Google."""


class GoogleDependencyUnavailableError(GoogleIntegrationError):
    pass


class GoogleClientConfigurationError(GoogleIntegrationError):
    pass


class GoogleAuthorizationDeniedError(GoogleIntegrationError):
    pass


class GoogleAuthorizationTimeoutError(GoogleIntegrationError):
    pass


class GoogleAuthorizationCancelledError(GoogleIntegrationError):
    pass


class GoogleBrowserLaunchError(GoogleIntegrationError):
    pass


class GoogleOAuthStateError(GoogleIntegrationError):
    pass


class GoogleNetworkError(GoogleIntegrationError):
    pass


class GoogleResourceNotFoundError(GoogleIntegrationError):
    pass


class GoogleRateLimitError(GoogleIntegrationError):
    pass


class GooglePermissionError(GoogleIntegrationError):
    pass


class GoogleResponseError(GoogleIntegrationError):
    pass


class GoogleTokenRevokedError(GoogleIntegrationError):
    pass


class GoogleScopeError(GoogleIntegrationError):
    pass


class GoogleCredentialStorageError(GoogleIntegrationError):
    pass


class GoogleCredentialStorageCorruptError(GoogleCredentialStorageError):
    pass


class GoogleSecureStorageUnavailableError(GoogleCredentialStorageError):
    pass


class GoogleRevocationError(GoogleIntegrationError):
    pass


class GoogleOperationInProgressError(GoogleIntegrationError):
    pass
