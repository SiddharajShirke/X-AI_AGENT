import json

from cryptography.fernet import Fernet, InvalidToken


class CredentialError(RuntimeError):
    pass


class CredentialCipher:
    def __init__(self, key: str):
        if not key.strip():
            raise CredentialError("Integration credentials are locked: APP_ENCRYPTION_KEY is missing")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise CredentialError("Integration credentials are locked: APP_ENCRYPTION_KEY is invalid") from exc

    def encrypt(self, credentials: dict[str, str]) -> str:
        payload = json.dumps(credentials, sort_keys=True, separators=(",", ":")).encode()
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, encrypted: str) -> dict[str, str]:
        try:
            value = json.loads(self._fernet.decrypt(encrypted.encode("ascii")))
        except (InvalidToken, ValueError, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise CredentialError("Integration credentials are locked: encryption key mismatch") from exc
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise CredentialError("Integration credentials are locked: invalid encrypted payload")
        return value
