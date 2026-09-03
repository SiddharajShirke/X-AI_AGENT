from cryptography.fernet import Fernet
import pytest

from app.services.crypto import CredentialCipher, CredentialError


def test_credentials_round_trip_without_plaintext_in_token():
    cipher = CredentialCipher(Fernet.generate_key().decode())
    token = cipher.encrypt({"api_key": "buffer-secret", "channel_id": "channel-1"})
    assert "buffer-secret" not in token
    assert cipher.decrypt(token) == {"api_key": "buffer-secret", "channel_id": "channel-1"}


def test_wrong_key_cannot_decrypt_credentials():
    token = CredentialCipher(Fernet.generate_key().decode()).encrypt({"token": "secret"})
    with pytest.raises(CredentialError, match="locked"):
        CredentialCipher(Fernet.generate_key().decode()).decrypt(token)
