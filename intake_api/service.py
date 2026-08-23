from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException, status

from intake_api.config import IntakeSettings
from intake_api.models import IntakeRecord, Principal, RequestStatus
from intake_api.repository import IntakeRepository, RecordPage
from intake_api.validation import SCHEMA_VERSION, validate_intake


class IntakeService:
    def __init__(
        self,
        repository: IntakeRepository,
        settings: IntakeSettings,
    ) -> None:
        self._repository = repository
        self._settings = settings

    async def create(
        self, principal: Principal, intake: dict
    ) -> IntakeRecord:
        validate_intake(intake)
        now = datetime.now(UTC)
        record = IntakeRecord(
            id=str(uuid4()),
            tenantId=principal.tenant_id,
            createdBy=principal.subject_id,
            status=RequestStatus.DRAFT,
            schemaVersion=SCHEMA_VERSION,
            createdAt=now,
            updatedAt=now,
            intake=intake,
        )
        return await self._repository.create(record)

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
