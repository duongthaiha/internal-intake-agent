import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "intake-request.schema.json"
)
SCHEMA_VERSION = "1.0.0"


class IntakeValidationError(ValueError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@lru_cache(maxsize=1)
def get_intake_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
        return json.load(schema_file)


@lru_cache(maxsize=1)
def get_intake_validator() -> Draft202012Validator:
    schema = get_intake_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_intake(payload: dict[str, Any]) -> None:
    errors: list[ValidationError] = sorted(
        get_intake_validator().iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return

    error = errors[0]
    path = ".".join(str(part) for part in error.absolute_path)
    location = f" at '{path}'" if path else ""
    raise IntakeValidationError(
        f"Intake request is invalid{location}: {error.message}"
    )
