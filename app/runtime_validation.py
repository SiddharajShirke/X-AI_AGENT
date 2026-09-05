from app.config import Settings


def deployment_warnings(settings: Settings) -> list[str]:
    """Return safe-to-log operator warnings for a live deployment."""
    if settings.demo_mode:
        return []

    warnings: list[str] = []
    if not settings.base_url.lower().startswith("https://"):
        warnings.append("BASE_URL must use stable HTTPS for Slack callbacks")
    if settings.admin_password == "change-me":
        warnings.append("ADMIN_PASSWORD still uses the unsafe default")
    if not settings.app_encryption_key:
        warnings.append("APP_ENCRYPTION_KEY is missing")
    if not settings.app_csrf_secret:
        warnings.append("APP_CSRF_SECRET is missing")
    return warnings
