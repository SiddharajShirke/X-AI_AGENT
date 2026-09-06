from app.config import Settings
from app.runtime_validation import deployment_warnings


def test_live_mode_warns_about_insecure_public_configuration():
    settings = Settings(
        app_mode="live",
        base_url="http://localhost:8000",
        admin_username="admin",
        admin_password="change-me",
        app_encryption_key="",
        app_csrf_secret="",
        database_path=":memory:",
        scheduler_enabled=False,
    )

    warnings = deployment_warnings(settings)

    assert any("HTTPS" in item for item in warnings)
    assert any("ADMIN_PASSWORD" in item for item in warnings)
    assert any("APP_ENCRYPTION_KEY" in item for item in warnings)
    assert any("APP_CSRF_SECRET" in item for item in warnings)


def test_demo_defaults_remain_credential_free():
    settings = Settings(
        app_mode="demo",
        database_path=":memory:",
        scheduler_enabled=False,
    )

    assert deployment_warnings(settings) == []


def test_buffer_live_capability_warns_even_when_app_mode_is_demo():
    settings = Settings(
        app_mode="demo",
        buffer_live_posting=True,
        base_url="http://localhost:8000",
        admin_password="change-me",
        app_encryption_key="",
        app_csrf_secret="",
        database_path=":memory:",
        scheduler_enabled=False,
    )

    warnings = deployment_warnings(settings)

    assert any("APP_MODE=demo" in item and "BUFFER_LIVE_POSTING=true" in item for item in warnings)
    assert any("HTTPS" in item for item in warnings)
    assert any("ADMIN_PASSWORD" in item for item in warnings)
    assert any("APP_ENCRYPTION_KEY" in item for item in warnings)
    assert any("APP_CSRF_SECRET" in item for item in warnings)
