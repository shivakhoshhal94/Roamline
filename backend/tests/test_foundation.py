from unittest.mock import MagicMock, patch
import os
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from app.core.config import Settings
from app.domain.profile import TravelerProfile
from app.main import create_app

URL = "postgresql+psycopg://test:test@localhost:5432/test"


def settings(**kwargs):
    return Settings(_env_file=None, database_url=URL, **kwargs)


def test_lifespan_health_and_readiness():
    engine = MagicMock()
    with patch("app.main.create_db_engine", return_value=engine):
        with TestClient(create_app(settings())) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/ready").json() == {"status": "ready"}
            query = engine.connect.return_value.__enter__.return_value.execute.call_args.args[0]
            assert str(query) == "SELECT 1"
        engine.dispose.assert_called_once()


def test_database_failure_does_not_break_liveness():
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("query", {}, Exception("sensitive details"))
    with patch("app.main.create_db_engine", return_value=engine):
        with TestClient(create_app(settings())) as client:
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "Database unavailable"}
            assert client.get("/health").status_code == 200


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3000"]')
    config = Settings(_env_file=None)
    assert str(config.database_url) == URL
    assert config.cors_origins == ["http://localhost:3000"]


def test_database_url_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("url", ["sqlite:///test.db", "postgresql://test:test@localhost/test"])
def test_database_driver_validation(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


def test_cors_policy():
    with pytest.raises(ValidationError):
        settings(cors_origins=["*"])
    with TestClient(create_app(settings(cors_origins=["http://localhost:3000"]))) as client:
        allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
        denied = client.get("/health", headers={"Origin": "https://other.example"})
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "access-control-allow-origin" not in denied.headers


def test_profile_is_optional_and_validated():
    assert TravelerProfile().interests == []
    with pytest.raises(ValidationError):
        TravelerProfile(activity_level="extreme")
    with pytest.raises(ValidationError):
        TravelerProfile(diagnosis="not a travel preference")


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL for PostgreSQL integration")
def test_real_postgres_readiness():
    with TestClient(create_app(Settings(_env_file=None, database_url=os.environ["TEST_DATABASE_URL"]))) as client:
        assert client.get("/ready").status_code == 200
