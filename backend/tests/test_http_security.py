import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize("origin", ["*", "https://*.example.com", "https://example.com/path", "javascript:alert(1)"])
def test_cors_configuration_rejects_unsafe_origins(origin):
    with pytest.raises(ValidationError):
        Settings(CORS_ALLOWED_ORIGINS=origin)


def test_cors_denies_by_default_and_security_headers_are_present(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: Settings())
    client = TestClient(main.create_app())
    response = client.options("/api/v1/auth/me", headers={"Origin": "https://untrusted.example",
                                                       "Access-Control-Request-Method": "GET"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    client.close()


def test_only_explicit_frontend_origin_is_allowed(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: Settings(CORS_ALLOWED_ORIGINS="https://frontend.example"))
    client = TestClient(main.create_app())
    for origin, allowed in (("https://frontend.example", True), ("https://untrusted.example", False)):
        response = client.options("/api/v1/auth/me", headers={"Origin": origin,
                                                           "Access-Control-Request-Method": "GET",
                                                           "Access-Control-Request-Headers": "X-Auth-Token"})
        assert (response.status_code == 200) == allowed
        assert response.headers.get("Access-Control-Allow-Origin") == (origin if allowed else None)
        assert "Access-Control-Allow-Credentials" not in response.headers
    client.close()
