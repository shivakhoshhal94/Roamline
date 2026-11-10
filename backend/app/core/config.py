import httpx
from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn
    cors_origins: list[str] = []
    retrieval_model_path: str | None = None
    retrieval_threads: int = Field(default=2, ge=1, le=16)
    geoapify_api_key: SecretStr | None = None
    geoapify_min_interval_seconds: float = Field(default=1.0, ge=0.2, le=60)
    geoapify_connect_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    geoapify_read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    geoapify_places_read_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    geoapify_write_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    geoapify_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    candidate_min_population: int = Field(default=100_000, ge=1)
    candidate_max_count: int = Field(default=40, ge=1, le=500)
    feature_stale_after_days: int = Field(default=30, ge=1, le=365)

    place_resolution_stale_after_days: int = Field(default=30, ge=1, le=365)
    enrichment_lease_seconds: int = Field(default=180, ge=60, le=3600)

    @property
    def geoapify_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.geoapify_connect_timeout_seconds,
            read=self.geoapify_read_timeout_seconds,
            write=self.geoapify_write_timeout_seconds,
            pool=self.geoapify_pool_timeout_seconds,
        )

    @property
    def geoapify_places_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(**{**self.geoapify_timeout.as_dict(), "read": self.geoapify_places_read_timeout_seconds})

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
