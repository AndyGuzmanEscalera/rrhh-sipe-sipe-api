from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_env: str = 'development'
    database_url: SecretStr = SecretStr('')
    local_timezone: str = 'America/La_Paz'
    cors_allow_origins: str = ''
    correspondencia_api_url: str = ''
    correspondencia_api_token: SecretStr = SecretStr('')
    correspondencia_api_timeout: float = Field(default=30, gt=0, le=600)
    correspondencia_refresh_cookie_name: str = 'refresh_token'
    session_cookie_name: str = 'rrhh_refresh_token'
    session_cookie_path: str = '/auth'
    session_cookie_secure: bool = True
    session_cookie_samesite: str = 'lax'
    biometric_api_url: str = 'https://biometric.sipesipe.gob.bo'
    biometric_api_token: SecretStr = SecretStr('')
    biometric_api_timeout: float = Field(default=60, gt=0, le=600)
    biometric_device_id: str = 'alcaldia-central'
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = Field(default=900, ge=60)
    worker_poll_seconds: float = Field(default=5, ge=1)

    @model_validator(mode='after')
    def secure_cookie(self):
        if (self.app_env=='production' or self.session_cookie_samesite=='none') and not self.session_cookie_secure:
            raise ValueError('Secure cookies required')
        return self

    @field_validator('session_cookie_samesite')
    @classmethod
    def samesite(cls,value):
        if value not in {'lax','strict','none'}: raise ValueError('Invalid cookie SameSite')
        return value

    @field_validator('correspondencia_api_url', 'biometric_api_url')
    @classmethod
    def https_url(cls, value):
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('External API requires a credential-free HTTPS base URL')
        return value.rstrip('/')

    @field_validator('local_timezone')
    @classmethod
    def timezone_exists(cls, value):
        ZoneInfo(value)
        return value


@lru_cache
def get_settings():
    return Settings()
