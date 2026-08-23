import asyncio
import time
from collections.abc import Mapping
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWK

from intake_api.config import IntakeSettings
from intake_api.models import Principal


bearer_scheme = HTTPBearer(auto_error=False)


class TokenValidator:
    def __init__(
        self,
        settings: IntakeSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._keys: dict[str, PyJWK] = {}
        self._keys_expire_at = 0.0
        self._last_refresh_at = 0.0
        self._lock = asyncio.Lock()

    async def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise _unauthorized("Bearer token header is invalid.") from exc

        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise _unauthorized("Bearer token signing metadata is invalid.")

        key = await self._get_key(header["kid"])
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=["RS256"],
                audience=self._settings.entra_audience,
                issuer=self._settings.entra_issuer,
                options={"require": ["aud", "exp", "iat", "iss", "tid"]},
            )
        except InvalidTokenError as exc:
            raise _unauthorized("Bearer token validation failed.") from exc

        tenant_id = _claim_string(claims, "tid")
        if tenant_id.lower() != self._settings.entra_tenant_id.lower():
            raise _unauthorized("Bearer token tenant is not allowed.")

        subject_id = claims.get("oid") or claims.get("sub")
        if not isinstance(subject_id, str) or not subject_id:
            raise _unauthorized("Bearer token has no caller identity.")

        scopes = frozenset(str(claims.get("scp", "")).split())
        roles_value = claims.get("roles", [])
        roles = (
            frozenset(role for role in roles_value if isinstance(role, str))
            if isinstance(roles_value, list)
            else frozenset()
        )
        return Principal(
            tenant_id=tenant_id,
            subject_id=subject_id,
            scopes=scopes,
            roles=roles,
        )

    async def _get_key(self, key_id: str) -> PyJWK:
        now = time.monotonic()
        if now < self._keys_expire_at:
            key = self._keys.get(key_id)
            if key is not None:
                return key
            if now - self._last_refresh_at < 300:
                raise _unauthorized("Bearer token signing key is unknown.")

        async with self._lock:
            now = time.monotonic()
            if (
                now >= self._keys_expire_at
                or (
                    key_id not in self._keys
                    and now - self._last_refresh_at >= 300
                )
            ):
                await self._refresh_keys()
            key = self._keys.get(key_id)
            if key is None:
                raise _unauthorized("Bearer token signing key is unknown.")
            return key

    async def _refresh_keys(self) -> None:
        metadata_url = (
            f"{self._settings.entra_issuer}/"
            ".well-known/openid-configuration"
        )
        try:
            metadata_response = await self._http_client.get(metadata_url)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            jwks_uri = metadata.get("jwks_uri")
            if not isinstance(jwks_uri, str):
                raise ValueError("OpenID metadata does not contain jwks_uri.")
            keys_response = await self._http_client.get(jwks_uri)
            keys_response.raise_for_status()
            keys_document = keys_response.json()
            keys = keys_document.get("keys")
            if not isinstance(keys, list):
                raise ValueError("JWKS response does not contain keys.")
            parsed_keys = {
                key.key_id: key
                for value in keys
                if isinstance(value, Mapping)
                for key in [PyJWK.from_dict(dict(value))]
                if key.key_id
            }
        except (httpx.HTTPError, ValueError, InvalidTokenError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity provider metadata is unavailable.",
            ) from exc

        self._keys = parsed_keys
        self._last_refresh_at = time.monotonic()
        self._keys_expire_at = (
            self._last_refresh_at + self._settings.jwks_cache_seconds
        )


def _claim_string(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise _unauthorized(f"Bearer token claim '{name}' is invalid.")
    return value


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_token_validator(request: Request) -> TokenValidator:
    return request.app.state.token_validator


def get_settings(request: Request) -> IntakeSettings:
    return request.app.state.settings


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    validator: TokenValidator = Depends(get_token_validator),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("A bearer token is required.")
    return await validator.validate(credentials.credentials)


def require_write_access(
    principal: Principal = Depends(get_principal),
    settings: IntakeSettings = Depends(get_settings),
) -> Principal:
    if (
        settings.delegated_write_scope not in principal.scopes
        and settings.privileged_write_role not in principal.roles
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller is not permitted to modify intake requests.",
        )
    return principal
