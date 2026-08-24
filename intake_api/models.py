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

    id: str = Field(json_schema_extra={"format": "uuid"})
    tenant_id: str = Field(alias="tenantId")
    created_by: str = Field(alias="createdBy")
    status: RequestStatus
    schema_version: str = Field(alias="schemaVersion")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")
    intake: dict[str, Any]
    idempotency_key_hash: str | None = Field(
        default=None,
        alias="idempotencyKeyHash",
    )
    request_fingerprint: str | None = Field(
        default=None,
        alias="requestFingerprint",
    )
    etag: str | None = Field(default=None, exclude=True)


class IntakeRecordResponse(BaseModel):
    id: str = Field(
        description="Server-assigned intake request identifier.",
        json_schema_extra={
            "format": "uuid",
            "examples": ["01234567-89ab-4cde-8f01-23456789abcd"],
        },
    )
    status: RequestStatus
    schema_version: str = Field(
        alias="schemaVersion",
        description="Version of the intake payload schema used by the record.",
    )
    created_at: datetime = Field(
        alias="createdAt",
        description="Time the draft was created.",
    )
    updated_at: datetime = Field(
        alias="updatedAt",
        description="Time the record was last changed.",
    )
    submitted_at: datetime | None = Field(
        default=None,
        alias="submittedAt",
        description="Time the draft was submitted, or null while it is mutable.",
    )
    intake: dict[str, Any]

    @classmethod
    def from_record(cls, record: IntakeRecord) -> "IntakeRecordResponse":
        return cls.model_validate(record.model_dump(by_alias=True))


class IntakeRecordPage(BaseModel):
    items: list[IntakeRecordResponse]
    continuation_token: str | None = Field(
        default=None,
        alias="continuationToken",
        description=(
            "Opaque token for the next page. It is valid only for the same "
            "caller and filters and must not be parsed or modified."
        ),
    )


class ProblemDetail(BaseModel):
    type: str = Field(
        description="Stable URI identifying the problem category.",
        json_schema_extra={"format": "uri"},
    )
    title: str
    status: int
    detail: str
    instance: str | None = Field(
        default=None,
        description="URI reference identifying the request occurrence.",
        json_schema_extra={"format": "uri-reference"},
    )
