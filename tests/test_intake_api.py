import json
import unittest
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from intake_api.app import create_app
from intake_api.config import IntakeSettings
from intake_api.models import IntakeRecord, Principal, RequestStatus
from intake_api.repository import (
    InvalidContinuationTokenError,
    RecordConflictError,
    RecordNotFoundError,
    RecordPage,
    RecordPreconditionError,
)


ROOT = Path(__file__).resolve().parent.parent


class FakeTokenValidator:
    async def validate(self, token: str) -> Principal:
        principals = {
            "owner": Principal(
                tenant_id="tenant-a",
                subject_id="owner-a",
                scopes=frozenset({"Intake.ReadWrite"}),
            ),
            "other": Principal(
                tenant_id="tenant-a",
                subject_id="owner-b",
                scopes=frozenset({"Intake.ReadWrite"}),
            ),
            "reviewer": Principal(
                tenant_id="tenant-a",
                subject_id="reviewer",
                roles=frozenset({"Intake.Read.All"}),
            ),
            "admin": Principal(
                tenant_id="tenant-a",
                subject_id="admin",
                roles=frozenset({"Intake.ReadWrite.All"}),
            ),
        }
        return principals[token]


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], IntakeRecord] = {}
        self.version = 0

    async def create(self, record: IntakeRecord) -> IntakeRecord:
        if (record.tenant_id, record.id) in self.items:
            raise RecordConflictError("Intake request already exists.")
        return self._store(record)

    async def get(self, tenant_id: str, request_id: str) -> IntakeRecord:
        try:
            return self.items[(tenant_id, request_id)]
        except KeyError as exc:
            raise RecordNotFoundError("Intake request was not found.") from exc

    async def list(
        self,
        tenant_id: str,
        *,
        created_by: str | None,
        request_status: RequestStatus | None,
        limit: int,
        continuation_token: str | None,
    ) -> RecordPage:
        records = [
            record
            for (record_tenant, _), record in self.items.items()
            if record_tenant == tenant_id
            and (created_by is None or record.created_by == created_by)
            and (
                request_status is None or record.status is request_status
            )
        ]
        try:
            offset = int(continuation_token or "0")
        except ValueError as exc:
            raise InvalidContinuationTokenError(
                "The continuation token is invalid for this request."
            ) from exc
        next_offset = offset + limit
        return RecordPage(
            items=records[offset:next_offset],
            continuation_token=(
                str(next_offset) if next_offset < len(records) else None
            ),
        )

    async def replace(
        self, record: IntakeRecord, etag: str
    ) -> IntakeRecord:
        current = await self.get(record.tenant_id, record.id)
        if current.etag != etag:
            raise RecordPreconditionError(
                "The intake request was modified by another caller."
            )
        return self._store(record)

    async def check_ready(self) -> None:
        return None

    def _store(self, record: IntakeRecord) -> IntakeRecord:
        self.version += 1
        stored = record.model_copy(update={"etag": f'"v{self.version}"'})
        self.items[(stored.tenant_id, stored.id)] = stored
        return stored


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


class IntakeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        application = create_app(
            settings=settings(),
            repository=self.repository,
            token_validator=FakeTokenValidator(),
        )
        self.client_context = TestClient(application)
        self.client = self.client_context.__enter__()
        with (ROOT / "examples" / "intake-request.example.json").open(
            encoding="utf-8"
        ) as example_file:
            self.payload = json.load(example_file)

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)

    @staticmethod
    def auth(caller: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {caller}"}

    def create(
        self,
        *,
        caller: str = "owner",
        idempotency_key: str | None = None,
        payload: dict | None = None,
    ) -> tuple[dict, str]:
        headers = self.auth(caller)
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        response = self.client.post(
            "/v1/intake-requests",
            headers=headers,
            json=self.payload if payload is None else payload,
        )
        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual(
            response.headers["location"],
            f"/v1/intake-requests/{response.json()['id']}",
        )
        return response.json(), response.headers["etag"]

    def test_create_get_replace_and_submit(self) -> None:
        created, etag = self.create()
        request_id = created["id"]

        read = self.client.get(
            f"/v1/intake-requests/{request_id}",
            headers=self.auth("owner"),
        )
        self.assertEqual(200, read.status_code)
        self.assertEqual(created, read.json())
        self.assertEqual(etag, read.headers["etag"])

        replacement = deepcopy(self.payload)
        replacement["title"] = "Updated innovation request"
        replaced = self.client.put(
            f"/v1/intake-requests/{request_id}",
            headers={**self.auth("owner"), "If-Match": etag},
            json=replacement,
        )
        self.assertEqual(200, replaced.status_code, replaced.text)
        self.assertEqual(
            "Updated innovation request", replaced.json()["intake"]["title"]
        )

        submitted = self.client.post(
            f"/v1/intake-requests/{request_id}/submit",
            headers={
                **self.auth("owner"),
                "If-Match": replaced.headers["etag"],
            },
        )
        self.assertEqual(200, submitted.status_code, submitted.text)
        self.assertEqual("submitted", submitted.json()["status"])
        self.assertIsNotNone(submitted.json()["submittedAt"])

        repeated = self.client.post(
            f"/v1/intake-requests/{request_id}/submit",
            headers={
                **self.auth("owner"),
                "If-Match": submitted.headers["etag"],
            },
        )
        self.assertEqual(200, repeated.status_code)
        self.assertEqual(submitted.json(), repeated.json())

        retry_after_unknown_outcome = self.client.post(
            f"/v1/intake-requests/{request_id}/submit",
            headers={
                **self.auth("owner"),
                "If-Match": replaced.headers["etag"],
            },
        )
        self.assertEqual(200, retry_after_unknown_outcome.status_code)
        self.assertEqual(submitted.json(), retry_after_unknown_outcome.json())

    def test_create_is_idempotent_when_key_is_supplied(self) -> None:
        first, first_etag = self.create(idempotency_key="retry-key-1")
        reordered_payload = dict(reversed(list(self.payload.items())))
        replayed, replayed_etag = self.create(
            idempotency_key="retry-key-1",
            payload=reordered_payload,
        )

        self.assertEqual(first, replayed)
        self.assertEqual(first_etag, replayed_etag)
        self.assertEqual(1, len(self.repository.items))
        UUID(first["id"])
        persisted = next(iter(self.repository.items.values()))
        self.assertIsNotNone(persisted.idempotency_key_hash)
        self.assertIsNotNone(persisted.request_fingerprint)
        self.assertNotIn("retry-key-1", persisted.model_dump_json())

    def test_idempotency_key_reuse_and_scoping(self) -> None:
        owner, _ = self.create(idempotency_key="retry-key-2")
        changed = deepcopy(self.payload)
        changed["title"] = "A different request using the same key"
        conflict = self.client.post(
            "/v1/intake-requests",
            headers={
                **self.auth("owner"),
                "Idempotency-Key": "retry-key-2",
            },
            json=changed,
        )
        self.assertEqual(409, conflict.status_code)
        self.assertEqual(
            "IdempotencyKeyReuse",
            conflict.headers["x-ms-error-code"],
        )
        self.assertEqual(
            "urn:internal-intake:problem:IdempotencyKeyReuse",
            conflict.json()["type"],
        )
        self.assertNotIn("retry-key-2", conflict.text)

        other, _ = self.create(
            caller="other",
            idempotency_key="retry-key-2",
        )
        self.assertNotEqual(owner["id"], other["id"])

        unkeyed_first, _ = self.create()
        unkeyed_second, _ = self.create()
        self.assertNotEqual(unkeyed_first["id"], unkeyed_second["id"])

        invalid = self.client.post(
            "/v1/intake-requests",
            headers={
                **self.auth("owner"),
                "Idempotency-Key": "contains whitespace",
            },
            json=self.payload,
        )
        self.assertEqual(400, invalid.status_code)
        self.assertEqual("InvalidRequest", invalid.headers["x-ms-error-code"])

    def test_rejects_invalid_payload_and_missing_or_stale_etag(self) -> None:
        invalid = self.client.post(
            "/v1/intake-requests",
            headers=self.auth("owner"),
            json={"title": "Too little"},
        )
        self.assertEqual(400, invalid.status_code)
        self.assertEqual(
            "application/problem+json",
            invalid.headers["content-type"],
        )
        self.assertEqual(
            "InvalidIntakeRequest",
            invalid.headers["x-ms-error-code"],
        )

        created, _ = self.create()
        request_id = created["id"]
        missing = self.client.put(
            f"/v1/intake-requests/{request_id}",
            headers=self.auth("owner"),
            json=self.payload,
        )
        self.assertEqual(428, missing.status_code)

        stale = self.client.put(
            f"/v1/intake-requests/{request_id}",
            headers={**self.auth("owner"), "If-Match": '"stale"'},
            json=self.payload,
        )
        self.assertEqual(412, stale.status_code)
        self.assertEqual(
            "PreconditionFailed",
            stale.headers["x-ms-error-code"],
        )

    def test_get_supports_if_none_match(self) -> None:
        created, etag = self.create()
        response = self.client.get(
            f"/v1/intake-requests/{created['id']}",
            headers={
                **self.auth("owner"),
                "If-None-Match": etag,
            },
        )
        self.assertEqual(304, response.status_code)
        self.assertEqual("", response.text)
        self.assertEqual(etag, response.headers["etag"])
        self.assertEqual(
            "private, no-cache",
            response.headers["cache-control"],
        )

    def test_owner_isolation_and_privileged_access(self) -> None:
        created, etag = self.create()
        request_id = created["id"]

        hidden = self.client.get(
            f"/v1/intake-requests/{request_id}",
            headers=self.auth("other"),
        )
        self.assertEqual(404, hidden.status_code)

        owner_list = self.client.get(
            "/v1/intake-requests", headers=self.auth("other")
        )
        self.assertEqual([], owner_list.json()["items"])

        reviewer_list = self.client.get(
            "/v1/intake-requests", headers=self.auth("reviewer")
        )
        self.assertEqual(1, len(reviewer_list.json()["items"]))

        forbidden = self.client.put(
            f"/v1/intake-requests/{request_id}",
            headers={**self.auth("reviewer"), "If-Match": etag},
            json=self.payload,
        )
        self.assertEqual(403, forbidden.status_code)

        allowed = self.client.put(
            f"/v1/intake-requests/{request_id}",
            headers={**self.auth("admin"), "If-Match": etag},
            json=self.payload,
        )
        self.assertEqual(200, allowed.status_code)

    def test_submitted_request_is_immutable(self) -> None:
        created, etag = self.create()
        request_id = created["id"]
        submitted = self.client.post(
            f"/v1/intake-requests/{request_id}/submit",
            headers={**self.auth("owner"), "If-Match": etag},
        )
        updated = self.client.put(
            f"/v1/intake-requests/{request_id}",
            headers={
                **self.auth("owner"),
                "If-Match": submitted.headers["etag"],
            },
            json=self.payload,
        )
        self.assertEqual(409, updated.status_code)

    def test_openapi_contract_is_mcp_ready(self) -> None:
        document = self.client.get("/openapi.json").json()
        operation_ids = {
            operation["operationId"]
            for path in document["paths"].values()
            for operation in path.values()
            if isinstance(operation, dict) and "operationId" in operation
        }
        self.assertEqual(
            {
                "create_intake_request",
                "get_intake_request",
                "list_intake_requests",
                "replace_intake_request",
                "submit_intake_request",
            },
            operation_ids,
        )
        intake_schema = document["components"]["schemas"]["IntakeRequest"]
        self.assertNotIn("$defs", intake_schema)
        self.assertNotIn("$schema", intake_schema)
        self.assertEqual(
            {"$ref": "#/components/schemas/IntakeRequest"},
            document["paths"]["/v1/intake-requests"]["post"]["requestBody"][
                "content"
            ]["application/json"]["schema"],
        )
        with (
            ROOT / "openapi" / "intake-api.openapi.json"
        ).open(encoding="utf-8") as contract_file:
            self.assertEqual(document, json.load(contract_file))
        self._assert_all_references_resolve(document)
        self.assertIn(
            "application/problem+json",
            document["paths"]["/v1/intake-requests"]["post"]["responses"][
                "400"
            ]["content"],
        )
        for path_item in document["paths"].values():
            for operation in path_item.values():
                if (
                    not isinstance(operation, dict)
                    or "operationId" not in operation
                ):
                    continue
                self.assertNotIn("422", operation["responses"])
                for response in operation["responses"].values():
                    if "application/problem+json" in response.get("content", {}):
                        self.assertIn("x-ms-error-code", response["headers"])

        create_operation = document["paths"]["/v1/intake-requests"]["post"]
        self.assertEqual(
            {"201", "400", "401", "403", "409", "429", "500", "503"},
            set(create_operation["responses"]),
        )
        self.assertEqual(
            {"ETag", "Location"},
            set(create_operation["responses"]["201"]["headers"]),
        )
        self.assertTrue(
            any(
                parameter["name"] == "Idempotency-Key"
                for parameter in create_operation["parameters"]
            )
        )

        get_operation = document["paths"][
            "/v1/intake-requests/{request_id}"
        ]["get"]
        self.assertIn("304", get_operation["responses"])
        request_id = next(
            parameter
            for parameter in get_operation["parameters"]
            if parameter["name"] == "request_id"
        )
        self.assertEqual("uuid", request_id["schema"]["format"])

        replace_operation = document["paths"][
            "/v1/intake-requests/{request_id}"
        ]["put"]
        if_match = next(
            parameter
            for parameter in replace_operation["parameters"]
            if parameter["name"] == "If-Match"
        )
        self.assertTrue(if_match["required"])
        self.assertEqual({"type": "string"}, if_match["schema"])
        submit = document["paths"][
            "/v1/intake-requests/{request_id}/submit"
        ]["post"]
        submit_if_match = next(
            parameter
            for parameter in submit["parameters"]
            if parameter["name"] == "If-Match"
        )
        self.assertTrue(submit_if_match["required"])

        response_schema = document["components"]["schemas"][
            "IntakeRecordResponse"
        ]
        self.assertEqual(
            {"$ref": "#/components/schemas/IntakeRequest"},
            response_schema["properties"]["intake"],
        )
        self.assertNotEqual(
            False,
            response_schema.get("additionalProperties"),
        )

    def test_list_paginates_with_opaque_continuation_token(self) -> None:
        self.create()
        self.create()
        first = self.client.get(
            "/v1/intake-requests?limit=1", headers=self.auth("owner")
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual(1, len(first.json()["items"]))
        token = first.json()["continuationToken"]
        self.assertIsNotNone(token)

        second = self.client.get(
            f"/v1/intake-requests?limit=1&continuationToken={token}",
            headers=self.auth("owner"),
        )
        self.assertEqual(200, second.status_code)
        self.assertEqual(1, len(second.json()["items"]))
        self.assertNotEqual(
            first.json()["items"][0]["id"], second.json()["items"][0]["id"]
        )
        self.assertIsNone(second.json()["continuationToken"])

        invalid = self.client.get(
            "/v1/intake-requests?continuationToken=modified",
            headers=self.auth("owner"),
        )
        self.assertEqual(400, invalid.status_code)
        self.assertEqual(
            "InvalidContinuationToken",
            invalid.headers["x-ms-error-code"],
        )

    def _assert_all_references_resolve(self, document: dict) -> None:
        def resolve(reference: str) -> object:
            self.assertTrue(reference.startswith("#/"), reference)
            value: object = document
            for part in reference[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                self.assertIsInstance(value, dict)
                value = value[part]
            return value

        def walk(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "$ref":
                        self.assertIsInstance(child, str)
                        resolve(child)
                    else:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(document)


if __name__ == "__main__":
    unittest.main()
