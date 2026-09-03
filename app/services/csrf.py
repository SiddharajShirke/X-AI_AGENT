from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time


class CsrfProtector:
    def __init__(self, secret: str):
        if not secret:
            raise ValueError("CSRF secret is required")
        self._secret = secret.encode("utf-8")

    def issue(self, subject: str) -> str:
        timestamp = str(int(time.time()))
        message = f"{subject}:{timestamp}".encode("utf-8")
        digest = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        payload = f"{timestamp}:{digest}".encode("ascii")
        return base64.urlsafe_b64encode(payload).decode("ascii")

    def verify(
        self, token: str, subject: str, max_age_seconds: int = 43_200
    ) -> bool:
        try:
            decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("ascii")
            timestamp_text, supplied = decoded.split(":", 1)
            timestamp = int(timestamp_text)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        if abs(int(time.time()) - timestamp) > max_age_seconds:
            return False
        message = f"{subject}:{timestamp_text}".encode("utf-8")
        expected = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)
