from __future__ import annotations

from urllib.parse import urlsplit


def validated_public_url(value: object) -> str:
    """Return a display-safe public HTTPS URL or an empty string."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        return ""
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        parsed.port
    except ValueError:
        return ""
    return candidate
