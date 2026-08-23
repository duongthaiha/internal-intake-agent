import json
import os
import time
import unittest
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm

from intake_api.auth import TokenValidator
from intake_api.config import IntakeSettings


def settings() -> IntakeSettings:
    return IntakeSettings(
        cosmos_endpoint="https://example.documents.azure.com:443/",
        cosmos_database_name="intake",
        cosmos_container_name="intake-requests",
        entra_tenant_id="tenant-a",
        entra_audience="api://intake",
        entra_issuer="https://login.microsoftonline.com/tenant-a/v2.0",
        delegated_write_scope="Intake.ReadWrite",
        privileged_read_role="Intake.Read.All",
        privileged_write_role="Intake.ReadWrite.All",
        jwks_cache_seconds=3600,
    )


class TokenValidatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.request_count = 0
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        public_jwk = json.loads(
            RSAAlgorithm.to_jwk(self.private_key.public_key())
        )
        public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})

        def handler(request: httpx.Request) -> httpx.Response:
            self.request_count += 1
            if request.url.path.endswith("openid-configuration"):
                return httpx.Response(
                    200, json={"jwks_uri": "https://identity.example/keys"}
                )
            if request.url == httpx.URL("https://identity.example/keys"):
                return httpx.Response(200, json={"keys": [public_jwk]})
            return httpx.Response(404)

        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        self.validator = TokenValidator(settings(), self.client)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    def token(self, **overrides: object) -> str:
        now = int(time.time())
        claims = {
            "aud": "api://intake",
            "iss": "https://login.microsoftonline.com/tenant-a/v2.0",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "tid": "tenant-a",
            "oid": "caller-a",
            "scp": "Intake.ReadWrite",
            "roles": ["Intake.Read.All"],
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    async def test_validates_identity_scopes_and_roles(self) -> None:
        principal = await self.validator.validate(self.token())
        self.assertEqual("tenant-a", principal.tenant_id)
        self.assertEqual("caller-a", principal.subject_id)
        self.assertEqual(frozenset({"Intake.ReadWrite"}), principal.scopes)
        self.assertEqual(frozenset({"Intake.Read.All"}), principal.roles)

    async def test_rejects_wrong_audience_and_tenant(self) -> None:
        with self.assertRaises(HTTPException) as audience_error:
            await self.validator.validate(self.token(aud="api://other"))
        self.assertEqual(401, audience_error.exception.status_code)

        with self.assertRaises(HTTPException) as tenant_error:
            await self.validator.validate(self.token(tid="tenant-b"))
        self.assertEqual(401, tenant_error.exception.status_code)

    async def test_unknown_key_does_not_refresh_a_warm_jwks_cache(self) -> None:
        await self.validator.validate(self.token())
        initial_requests = self.request_count
        unknown_key_token = jwt.encode(
            {
                "aud": "api://intake",
                "iss": "https://login.microsoftonline.com/tenant-a/v2.0",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "tid": "tenant-a",
                "oid": "caller-a",
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": "unknown-key"},
        )
        with self.assertRaises(HTTPException) as error:
            await self.validator.validate(unknown_key_token)
        self.assertEqual(401, error.exception.status_code)
        self.assertEqual(initial_requests, self.request_count)


class IntakeSettingsTests(unittest.TestCase):
    def test_requires_explicit_service_boundaries(self) -> None:
        environment = {
            "INTAKE_COSMOS_ENDPOINT": "https://example.documents.azure.com:443/",
            "INTAKE_COSMOS_DATABASE_NAME": "intake",
            "INTAKE_COSMOS_CONTAINER_NAME": "intake-requests",
            "INTAKE_ENTRA_TENANT_ID": "tenant-a",
            "INTAKE_ENTRA_AUDIENCE": "api://intake",
        }
        with patch.dict(os.environ, environment, clear=True):
            resolved = IntakeSettings.from_env()
        self.assertEqual("tenant-a", resolved.entra_tenant_id)
        self.assertEqual("Intake.ReadWrite", resolved.delegated_write_scope)

        del environment["INTAKE_ENTRA_AUDIENCE"]
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                RuntimeError, "INTAKE_ENTRA_AUDIENCE is required"
            ):
                IntakeSettings.from_env()


if __name__ == "__main__":
    unittest.main()
