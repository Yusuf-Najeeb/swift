
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from backend.config import Settings, get_settings

log = logging.getLogger("swift.auth")

_BEARER_PREFIX = "bearer "


def _bearer_credential(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not isinstance(authorization, str):
        return None
    raw = authorization.strip()
    if not raw:
        return None
    if raw[: len(_BEARER_PREFIX)].lower() != _BEARER_PREFIX:
        return None
    token = raw[len(_BEARER_PREFIX) :].strip()
    return token or None


async def require_api_bearer(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:

    expected = (settings.api_bearer_token or "").strip()
    if not expected:
        return
    got = _bearer_credential(request.headers.get("Authorization"))
    if got is None:
        log.debug("missing or non-bearer authorization for %s", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )
    a, b = got.encode("utf-8"), expected.encode("utf-8")
    if len(a) != len(b) or not hmac.compare_digest(a, b):
        log.debug("invalid api bearer for %s", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


__all__ = ["require_api_bearer"]
