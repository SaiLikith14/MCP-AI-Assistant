import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, settings.backend_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
