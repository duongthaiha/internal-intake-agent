import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException, status

from intake_api.config import IntakeSettings
from intake_api.models import IntakeRecord, Principal, RequestStatus
from intake_api.repository import (
    IntakeRepository,
    RecordConflictError,
    RecordNotFoundError,
    RecordPage,
    RepositoryUnavailableError,
)
from intake_api.validation import SCHEMA_VERSION, validate_intake


_IDEMPOTENCY_NAMESPACE = UUID("c48c6a8b-43da-4cc1-b441-5b251c610ac1")


class IdempotencyKeyReuseError(RuntimeError):
    pass


class IntakeService:
    def __init__(
        self,
        repository: IntakeRepository,
        settings: IntakeSettings,
    ) -> None:
        self._repository = repository
        self._settings = settings

    async def create(
        self,
        principal: Principal,
        intake: dict,
        idempotency_key: str | None = None,
    ) -> IntakeRecord:
        validate_intake(intake)
        now = datetime.now(UTC)
        key_hash = (
            hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            if idempotency_key is not None
            else None
        )
        request_fingerprint = (
            _fingerprint_intake(intake) if key_hash is not None else None
        )
        record_id = (
            str(
                uuid5(
                    _IDEMPOTENCY_NAMESPACE,
                    "\0".join(
                        (
                            principal.tenant_id,
                            principal.subject_id,
                            key_hash,
                        )
                    ),
                )
            )
            if key_hash is not None
            else str(uuid4())
        )
        record = IntakeRecord(
            id=record_id,
            tenantId=principal.tenant_id,
            createdBy=principal.subject_id,
            status=RequestStatus.DRAFT,
            schemaVersion=SCHEMA_VERSION,
            createdAt=now,
            updatedAt=now,
            intake=intake,
            idempotencyKeyHash=key_hash,
            requestFingerprint=request_fingerprint,
        )
        try:
            return await self._repository.create(record)
        except RecordConflictError:
            if key_hash is None or request_fingerprint is None:
                raise
            existing = await self._read_idempotent_create(
                principal.tenant_id,
                record_id,
            )
            if (
                existing.created_by != principal.subject_id
                or existing.idempotency_key_hash != key_hash
                or existing.request_fingerprint != request_fingerprint
            ):
                raise IdempotencyKeyReuseError(
                    "The idempotency key was already used with a different "
                    "request."
                )
            return existing

    async def get(
        self, principal: Principal, request_id: str
    ) -> IntakeRecord:
        record = await self._repository.get(principal.tenant_id, request_id)
        self._authorize_read(principal, record)
        return record

    async def list(
        self,
        principal: Principal,
        *,
        request_status: RequestStatus | None,
        limit: int,
        continuation_token: str | None,
    ) -> RecordPage:
        privileged = self._can_read_all(principal)
        return await self._repository.list(
            principal.tenant_id,
            created_by=None if privileged else principal.subject_id,
            request_status=request_status,
            limit=limit,
            continuation_token=continuation_token,
        )

    async def replace(
        self,
        principal: Principal,
        request_id: str,
        intake: dict,
        etag: str,
    ) -> IntakeRecord:
        validate_intake(intake)
        current = await self.get(principal, request_id)
        self._authorize_write(principal, current)
        if current.status is not RequestStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Submitted intake requests cannot be changed.",
            )
        updated = current.model_copy(
            update={"intake": intake, "updated_at": datetime.now(UTC)}
        )
        return await self._repository.replace(updated, etag)

    async def submit(
        self,
        principal: Principal,
        request_id: str,
        etag: str,
    ) -> IntakeRecord:
        current = await self.get(principal, request_id)
        self._authorize_write(principal, current)
        if current.status is RequestStatus.SUBMITTED:
            return current
        validate_intake(current.intake)
        now = datetime.now(UTC)
        submitted = current.model_copy(
            update={
                "status": RequestStatus.SUBMITTED,
                "updated_at": now,
                "submitted_at": now,
            }
        )
        return await self._repository.replace(submitted, etag)

    def _authorize_read(
        self, principal: Principal, record: IntakeRecord
    ) -> None:
        if (
            record.created_by != principal.subject_id
            and not self._can_read_all(principal)
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Intake request was not found.",
            )

    def _authorize_write(
        self, principal: Principal, record: IntakeRecord
    ) -> None:
        if (
            record.created_by != principal.subject_id
            and self._settings.privileged_write_role not in principal.roles
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Intake request was not found.",
            )

    def _can_read_all(self, principal: Principal) -> bool:
        return bool(
            principal.roles
            & {
                self._settings.privileged_read_role,
                self._settings.privileged_write_role,
            }
        )

    async def _read_idempotent_create(
        self,
        tenant_id: str,
        request_id: str,
    ) -> IntakeRecord:
        for delay in (0.0, 0.05, 0.1, 0.2):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._repository.get(tenant_id, request_id)
            except RecordNotFoundError:
                continue
        raise RepositoryUnavailableError(
            "The idempotent create result was not readable after creation."
        )


def _fingerprint_intake(intake: dict) -> str:
    canonical = json.dumps(
        intake,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
