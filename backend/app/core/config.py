from pydantic import PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn
    cors_origins: list[str] = []

    @field_validator("database_url")
    @classmethod
    def require_psycopg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use postgresql+psycopg")
        return value

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, values: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for value in values:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.path or parsed.query or parsed.fragment or "*" in value):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths")
        return values
