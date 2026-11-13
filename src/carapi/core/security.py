import hashlib
import hmac
from collections.abc import Iterable

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = "X-API-Key"

_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, description="Required for writes")


def is_valid_key(candidate: str | None, keys: Iterable[str]) -> bool:
    if not candidate:
        return False
    # Check every key with constant-time compare; don't short-circuit.
    matched = False
    for key in keys:
        matched |= hmac.compare_digest(candidate.encode(), key.encode())
    return matched


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def require_api_key(request: Request, api_key: str | None = Security(_scheme)) -> str:
    keys: list[str] = request.app.state.settings.api_keys
    if not api_key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Missing {API_KEY_HEADER} header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not is_valid_key(api_key, keys):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key
