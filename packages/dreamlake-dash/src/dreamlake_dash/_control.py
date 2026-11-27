"""Minimal managed writer control plane; no umbrella SDK dependency."""
import httpx
from .managed import _login_provider, _authorization


class ManagedControlClient:
    def __init__(self):
        self._url, self._provider = _login_provider()

    @property
    def _token(self):
        try:
            return self._provider()
        except ValueError:
            return None

    def http(self):
        return httpx.Client(base_url=self._url, follow_redirects=False, timeout=30,
                            headers={"Authorization": _authorization(self._provider())})
