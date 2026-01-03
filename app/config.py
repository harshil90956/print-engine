import os
from dataclasses import dataclass
from typing import Optional


def env(key: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        if required:
            raise RuntimeError(f"Missing required env var: {key}")
        return "" if default is None else str(default)
    return value


@dataclass(frozen=True)
class Settings:
    APP_ENV: str
    SERVICE_PORT: int
    INTERNAL_API_KEY: str
    S3_BUCKET: str
    S3_REGION: str
    S3_ENDPOINT: str
    S3_ACCESS_KEY_ID: str
    S3_SECRET_ACCESS_KEY: str


def load_settings() -> Settings:
    app_env = env("APP_ENV", default="development", required=False)
    service_port_raw = env("PORT", default=None, required=False) or env("SERVICE_PORT", default="9000", required=False)
    try:
        service_port = int(service_port_raw)
    except ValueError as e:
        raise RuntimeError("SERVICE_PORT must be an integer") from e

    internal_api_key = env("INTERNAL_API_KEY", required=True)

    return Settings(
        APP_ENV=app_env,
        SERVICE_PORT=service_port,
        INTERNAL_API_KEY=internal_api_key,
        S3_BUCKET=env("S3_BUCKET", required=True),
        S3_REGION=env("S3_REGION", required=True),
        S3_ENDPOINT=env("S3_ENDPOINT", default="", required=False),
        S3_ACCESS_KEY_ID=env("S3_ACCESS_KEY_ID", required=True),
        S3_SECRET_ACCESS_KEY=env("S3_SECRET_ACCESS_KEY", required=True),
    )
