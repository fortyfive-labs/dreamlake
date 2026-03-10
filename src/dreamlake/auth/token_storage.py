"""Token storage backends for dreamlake authentication."""

import json
from abc import ABC, abstractmethod
from base64 import urlsafe_b64decode
from pathlib import Path
from typing import Optional

from .exceptions import StorageError


class TokenStorage(ABC):
    """Abstract base class for token storage backends."""

    @abstractmethod
    def store(self, key: str, value: str) -> None:
        pass

    @abstractmethod
    def load(self, key: str) -> Optional[str]:
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        pass


class KeyringStorage(TokenStorage):
    """OS keyring storage (macOS Keychain, Windows Credential Manager, Linux Secret Service)."""

    SERVICE_NAME = "dreamlake"

    def __init__(self):
        try:
            import keyring
            self.keyring = keyring
        except ImportError:
            raise StorageError("keyring library not installed. Install with: pip install keyring")

    def store(self, key: str, value: str) -> None:
        try:
            self.keyring.set_password(self.SERVICE_NAME, key, value)
        except Exception as e:
            raise StorageError(f"Failed to store token in keyring: {e}")

    def load(self, key: str) -> Optional[str]:
        try:
            return self.keyring.get_password(self.SERVICE_NAME, key)
        except Exception as e:
            raise StorageError(f"Failed to load token from keyring: {e}")

    def delete(self, key: str) -> None:
        try:
            self.keyring.delete_password(self.SERVICE_NAME, key)
        except Exception:
            pass


class EncryptedFileStorage(TokenStorage):
    """Encrypted file storage using Fernet symmetric encryption."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.tokens_file = self.config_dir / "tokens.encrypted"
        self.key_file = self.config_dir / "encryption.key"

        try:
            from cryptography.fernet import Fernet
            self.Fernet = Fernet
        except ImportError:
            raise StorageError("cryptography library not installed. Install with: pip install cryptography")

        self.config_dir.mkdir(parents=True, exist_ok=True)

        if not self.key_file.exists():
            key = self.Fernet.generate_key()
            self.key_file.write_bytes(key)
            self.key_file.chmod(0o600)
        else:
            key = self.key_file.read_bytes()

        self.cipher = self.Fernet(key)

    def _load_all(self) -> dict:
        if not self.tokens_file.exists():
            return {}
        try:
            encrypted = self.tokens_file.read_bytes()
            decrypted = self.cipher.decrypt(encrypted)
            return json.loads(decrypted)
        except Exception as e:
            raise StorageError(f"Failed to decrypt tokens file: {e}")

    def _save_all(self, data: dict) -> None:
        try:
            plaintext = json.dumps(data).encode()
            encrypted = self.cipher.encrypt(plaintext)
            self.tokens_file.write_bytes(encrypted)
            self.tokens_file.chmod(0o600)
        except Exception as e:
            raise StorageError(f"Failed to encrypt tokens file: {e}")

    def store(self, key: str, value: str) -> None:
        all_tokens = self._load_all()
        all_tokens[key] = value
        self._save_all(all_tokens)

    def load(self, key: str) -> Optional[str]:
        return self._load_all().get(key)

    def delete(self, key: str) -> None:
        all_tokens = self._load_all()
        if key in all_tokens:
            del all_tokens[key]
            self._save_all(all_tokens)


class PlaintextFileStorage(TokenStorage):
    """Plaintext file storage (insecure — only for testing/fallback)."""

    _warning_shown = False

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.tokens_file = self.config_dir / "tokens.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        if not PlaintextFileStorage._warning_shown:
            try:
                from rich.console import Console
                Console().print(
                    "\n[bold red]WARNING: Storing tokens in plaintext![/bold red]\n"
                    "[yellow]Install keyring or cryptography for secure storage.[/yellow]\n"
                )
            except ImportError:
                print("WARNING: Storing tokens in plaintext! This is insecure.")
            PlaintextFileStorage._warning_shown = True

    def _load_all(self) -> dict:
        if not self.tokens_file.exists():
            return {}
        try:
            return json.loads(self.tokens_file.read_text())
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_all(self, data: dict) -> None:
        self.tokens_file.write_text(json.dumps(data, indent=2))
        self.tokens_file.chmod(0o600)

    def store(self, key: str, value: str) -> None:
        all_tokens = self._load_all()
        all_tokens[key] = value
        self._save_all(all_tokens)

    def load(self, key: str) -> Optional[str]:
        return self._load_all().get(key)

    def delete(self, key: str) -> None:
        all_tokens = self._load_all()
        if key in all_tokens:
            del all_tokens[key]
            self._save_all(all_tokens)


def get_token_storage(config_dir: Optional[Path] = None) -> TokenStorage:
    """Auto-detect and return the most secure available storage backend.

    Tries in order: KeyringStorage → EncryptedFileStorage → PlaintextFileStorage.
    """
    if config_dir is None:
        config_dir = Path.home() / ".dreamlake"

    try:
        return KeyringStorage()
    except (ImportError, StorageError):
        pass

    try:
        return EncryptedFileStorage(config_dir)
    except (ImportError, StorageError):
        pass

    return PlaintextFileStorage(config_dir)


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (for display only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return json.loads(urlsafe_b64decode(payload))
    except Exception:
        return {}
