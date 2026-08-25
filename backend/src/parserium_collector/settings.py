from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DASHBOARD_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Parserium Collector"
    build_id: str = "dev"
    release_version: str = "0.1.0-dev"
    public_origin: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8080")
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    )
    storage_root: Path = Path("/var/lib/parserium-collector")
    static_root: Path = Path("/app/static")
    storage_reserve_bytes: int = 5 * 1024 * 1024 * 1024
    storage_reserve_ratio: float = 0.10
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "parserium_collector"
    db_user: str = "parserium_collector"
    db_password_file: Path = Path("/run/secrets/db_password")
    session_signing_secret_file: Path = Path("/run/secrets/session_signing_secret")
    pairing_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    session_idle_seconds: int = Field(default=86400, ge=300, le=604800)
    session_cookie_secure: bool = False
    expected_migration: str = "0002_local_sessions"
    worker_id: str = "worker-1"
    worker_heartbeat_seconds: float = 10.0
    worker_stale_seconds: float = 30.0
    firecrawl_base_url: AnyHttpUrl | None = None
    firecrawl_timeout_seconds: float = 5.0
    firecrawl_search_timeout_seconds: float = Field(default=45.0, gt=0, le=120)

    def database_url(self) -> str:
        if not self.db_password_file.is_file():
            raise ValueError("Database password file is missing.")
        password = self.db_password_file.read_text(encoding="utf-8").strip()
        if len(password) < 32:
            raise ValueError("Database password must contain at least 32 characters.")
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)

    def session_signing_secret(self) -> bytes:
        if not self.session_signing_secret_file.is_file():
            raise ValueError("Session signing secret file is missing.")
        secret = self.session_signing_secret_file.read_bytes().strip()
        if len(secret) < 32:
            raise ValueError("Session signing secret must contain at least 32 bytes.")
        return secret
