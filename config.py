from dotenv import load_dotenv
load_dotenv()   # 加载 .env 文件中的环境变量
import configparser
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _read_alembic_database_url() -> str:
    ini_path = BASE_DIR / "alembic.ini"
    if not ini_path.exists():
        return "sqlite:///./yecai.db"
    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-8")
    return parser.get("alembic", "sqlalchemy.url", fallback="sqlite:///./yecai.db")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "业财一体化系统")
    api_prefix: str = os.getenv("API_PREFIX", "/api")
    #database_url: str = os.getenv("DATABASE_URL", _read_alembic_database_url())
    database_url: str = os.getenv("DATABASE_URL") or _read_alembic_database_url()
    secret_key: str = os.getenv("SECRET_KEY", "change-this-secret-in-production")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "120"))
    refresh_token_minutes: int = int(os.getenv("REFRESH_TOKEN_MINUTES", str(60 * 24 * 7)))
    access_cookie_name: str = os.getenv("ACCESS_COOKIE_NAME", "yecai_access_token")
    refresh_cookie_name: str = os.getenv("REFRESH_COOKIE_NAME", "yecai_refresh_token")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
    cookie_samesite: str = os.getenv("COOKIE_SAMESITE", "lax")
    cookie_domain: str | None = os.getenv("COOKIE_DOMAIN") or None
    session_touch_interval_seconds: int = int(os.getenv("SESSION_TOUCH_INTERVAL_SECONDS", "300"))
    run_migrations_on_startup: bool = os.getenv("RUN_MIGRATIONS_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}
    upload_dir: Path = BASE_DIR / os.getenv("UPLOAD_DIR", "uploads")
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "8000"))
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    default_user_password: str = os.getenv("DEFAULT_USER_PASSWORD", "ChangeMe123!")
    trace_enabled: bool = os.getenv("TRACE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    slow_request_ms: int = int(os.getenv("SLOW_REQUEST_MS", "1000"))
    slow_sql_ms: int = int(os.getenv("SLOW_SQL_MS", "200"))
    trace_stage_log_ms: int = int(os.getenv("TRACE_STAGE_LOG_MS", "50"))
    trace_sql_max_length: int = int(os.getenv("TRACE_SQL_MAX_LENGTH", "2000"))
    trace_log_file: str = os.getenv("TRACE_LOG_FILE", str(BASE_DIR / "logs" / "yecai-trace.log"))

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
