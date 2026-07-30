ADMIN_ACTOR_ID = "user-admin-001"
ADMIN_DISPLAY_NAME = "Atlas Admin"
BOOTSTRAP_ADMIN_EMAIL_ENV = "ATLAS_BOOTSTRAP_ADMIN_EMAIL"
BOOTSTRAP_ADMIN_PASSWORD_ENV = "ATLAS_BOOTSTRAP_ADMIN_PASSWORD"


class AdminBootstrapConfigurationError(ValueError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code
