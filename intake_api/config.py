import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class IntakeSettings:
    cosmos_endpoint: str
    cosmos_database_name: str
    cosmos_container_name: str
    entra_tenant_id: str
    entra_audience: str
    entra_issuer: str
    delegated_write_scope: str
    privileged_read_role: str
    privileged_write_role: str
    jwks_cache_seconds: int

    @classmethod
    def from_env(cls) -> "IntakeSettings":
        tenant_id = _required("INTAKE_ENTRA_TENANT_ID")
        return cls(
            cosmos_endpoint=_required("INTAKE_COSMOS_ENDPOINT"),
            cosmos_database_name=_required("INTAKE_COSMOS_DATABASE_NAME"),
            cosmos_container_name=_required("INTAKE_COSMOS_CONTAINER_NAME"),
            entra_tenant_id=tenant_id,
            entra_audience=_required("INTAKE_ENTRA_AUDIENCE"),
            entra_issuer=os.getenv(
                "INTAKE_ENTRA_ISSUER",
                f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            ).rstrip("/"),
            delegated_write_scope=os.getenv(
                "INTAKE_DELEGATED_WRITE_SCOPE", "Intake.ReadWrite"
            ),
            privileged_read_role=os.getenv(
                "INTAKE_PRIVILEGED_READ_ROLE", "Intake.Read.All"
            ),
            privileged_write_role=os.getenv(
                "INTAKE_PRIVILEGED_WRITE_ROLE", "Intake.ReadWrite.All"
            ),
            jwks_cache_seconds=_positive_int("INTAKE_JWKS_CACHE_SECONDS", 3600),
        )

