from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RequestStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"


class Principal(BaseModel):
    tenant_id: str
    subject_id: str
    scopes: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()


class IntakeRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    tenant_id: str = Field(alias="tenantId")
    created_by: str = Field(alias="createdBy")
    status: RequestStatus
    schema_version: str = Field(alias="schemaVersion")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")
    intake: dict[str, Any]
    etag: str | None = Field(default=None, exclude=True)


class IntakeRecordResponse(BaseModel):
    id: str
    status: RequestStatus
    schema_version: str = Field(alias="schemaVersion")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")
    intake: dict[str, Any]

    @classmethod
    def from_record(cls, record: IntakeRecord) -> "IntakeRecordResponse":
        return cls.model_validate(record.model_dump(by_alias=True))


class IntakeRecordPage(BaseModel):
    items: list[IntakeRecordResponse]
    continuation_token: str | None = Field(
        default=None, alias="continuationToken"
    )


class ProblemDetail(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str | None = None

