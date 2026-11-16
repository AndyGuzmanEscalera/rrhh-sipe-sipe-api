from contextlib import contextmanager
from hashlib import sha256
from sqlalchemy import text
from app.core.config import get_settings
from app.core.errors import problem
from app.biometric.api_client.client import BiometricApiClient
from app.biometric.api_client.config import ApiConfig
from app.biometric.api_client.errors import BiometricApiError


class BiometricService:
    def __init__(self, client=None):
        if client:
            self.client, self.config = client, client.config
            return
        settings = get_settings()
        try:
            self.config = ApiConfig(settings.biometric_api_url, settings.biometric_api_token.get_secret_value(),
                settings.biometric_api_timeout, settings.biometric_device_id)
        except ValueError:
            raise problem('biometric_invalid_configuration', 'Configure el cliente HTTPS biométrico.') from None
        self.client = BiometricApiClient(self.config)

    @contextmanager
    def device_lock(self, engine):
        key = int.from_bytes(sha256(self.config.url.encode()).digest()[:8], 'big', signed=True)
        with engine.connect() as connection:
            locked = connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key':key})
            connection.commit()
            if not locked:
                raise problem('biometric_busy', 'Existe una operación biométrica activa.', 409)
            try:
                yield
            finally:
                connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key':key})
                connection.commit()

    def status(self):
        health = self.client.health()
        try:
            info = self.client.get_device()
        except BiometricApiError as exc:
            if exc.code != 'biometric_device_unreachable':
                raise
            info = {}
        connected = bool(info.get('connected'))
        return dict(connected=connected, api_available=health.get('status')=='ok', device_connected=connected,
            device_id=self.config.device_id, model=info.get('device_name'), serial=info.get('serial_number'),
            platform=info.get('platform'), firmware=info.get('firmware_version'), users_count=None,
            transport=info.get('transport'))

    def download(self, start_date, end_date):
        return self.client.download_attendance(from_date=start_date.isoformat(), to_date=end_date.isoformat())

    def users(self):
        return self.client.get_users()
