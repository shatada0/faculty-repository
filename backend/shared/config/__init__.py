"""Настройки из окружения и секретов compose (/run/secrets): класс на подсистему, экземпляры на уровне модуля."""

from datetime import timedelta
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

SECRETS_DIR = Path("/run/secrets")


def _settings(prefix: str) -> SettingsConfigDict:
    # Переменная окружения важнее файла секрета: так сервис запускается и вне compose (тесты, локально).
    secrets = str(SECRETS_DIR) if SECRETS_DIR.is_dir() else None
    return SettingsConfigDict(env_prefix=prefix, secrets_dir=secrets, extra="ignore")


class AppConfig(BaseSettings):
    model_config = _settings("")

    service_name: str = "app"
    site_domain: str = "localhost"
    crossref_mailto: str = ""


class DatabaseConfig(BaseSettings):
    model_config = _settings("DB_")

    host: str = "postgres"
    port: int = 5432
    name: str = "app"
    user: str = "app"
    password: str = ""  # секрет db_password

    @property
    def url(self) -> URL:
        return URL.create("postgresql+asyncpg", self.user, self.password, self.host, self.port, self.name)


class RabbitConfig(BaseSettings):
    model_config = _settings("AMQP_")

    host: str = "rabbitmq"
    port: int = 5672
    user: str = "app"
    password: str = ""  # секрет amqp_password

    @property
    def url(self) -> str:
        return f"amqp://{self.user}:{self.password}@{self.host}:{self.port}/"


class S3Config(BaseSettings):
    model_config = _settings("S3_")

    endpoint: str = "http://minio:9000"
    public_url: str = "https://files.localhost"  # хост, который видит браузер: на нём считается подпись
    bucket: str = "app"
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""  # секрет s3_secret_key


class AuthConfig(BaseSettings):
    model_config = _settings("AUTH_")

    cookie_name: str = "__Host-session"  # в dev по http — session_token и cookie_secure=false
    cookie_secure: bool = True
    ttl_hours: int = 8
    allowed_origins: list[str] = ["https://localhost"]
    keys_dir: Path = SECRETS_DIR  # jwt_private (только auth_service) и jwt_public

    @model_validator(mode="after")
    def _host_prefix_needs_secure(self) -> Self:
        # браузер молча выбросит __Host-куку без Secure: вход вернёт 200, а сессии не будет
        if self.cookie_name.startswith("__Host-") and not self.cookie_secure:
            raise ValueError(
                "кука __Host-… требует AUTH_COOKIE_SECURE=true; для dev по http — AUTH_COOKIE_NAME=session_token"
            )
        return self

    @property
    def ttl(self) -> timedelta:
        return timedelta(hours=self.ttl_hours)


class LLMConfig(BaseSettings):
    model_config = _settings("LLM_")

    url: str = "http://llm:8000"
    model: str = ""
    timeout_seconds: float = 120
    concurrency: int = 4  # сколько параллельных запросов тянет LLM-сервер


app_config = AppConfig()
database_config = DatabaseConfig()
rabbit_config = RabbitConfig()
s3_config = S3Config()
auth_config = AuthConfig()
llm_config = LLMConfig()

MB = 1024 * 1024
# Лимиты загрузки: их отдаёт /api/upload-limits и по ним же проверяет сервер. nginx держит те же maxBytes.
UPLOAD_LIMITS = {
    "archive": {"accept": [".zip", ".pdf"], "maxBytes": 500 * MB},  # на всю загрузку: PDF и zip вперемешку
    "table": {"accept": [".xlsx", ".csv"], "maxBytes": 10 * MB},
    "workFile": {"accept": [".pdf", ".pptx", ".docx"], "maxBytes": 100 * MB},
    "material": {"accept": [".pdf", ".pptx", ".docx", ".zip"], "maxBytes": 50 * MB},
}
