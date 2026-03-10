"""DreamLake configuration management (~/.dreamlake/config.json)."""

import json
from pathlib import Path
from typing import Any, Optional


class Config:
    """Manages persistent configuration stored in ~/.dreamlake/config.json."""

    DEFAULT_CONFIG_DIR = Path.home() / ".dreamlake"

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir else self.DEFAULT_CONFIG_DIR
        self.config_file = self.config_dir / "config.json"
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        """Load config from disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if self.config_file.exists():
            try:
                self._data = json.loads(self.config_file.read_text())
            except (json.JSONDecodeError, IOError):
                self._data = {}

    def save(self) -> None:
        """Persist config to disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(self._data, indent=2))
        self.config_file.chmod(0o600)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    @property
    def device_secret(self) -> Optional[str]:
        return self._data.get("device_secret")

    @property
    def remote_url(self) -> Optional[str]:
        return self._data.get("remote_url")

    @property
    def config_dir_path(self) -> Path:
        return self.config_dir


# Module-level singleton
config = Config()
