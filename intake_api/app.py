import logging
from copy import deepcopy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from intake_api.auth import (
    TokenValidator,
    get_principal,
    require_write_access,
)
from intake_api.config import IntakeSettings
from intake_api.models import (
    IntakeRecord,
    IntakeRecordPage,
    IntakeRecordResponse,
    Principal,
    ProblemDetail,
    RequestStatus,
)
from intake_api.repository import (
    CosmosIntakeRepository,
    InvalidContinuationTokenError,
    IntakeRepository,
    RecordConflictError,
    RecordNotFoundError,
    RepositoryOperationError,
    RepositoryThrottledError,
    RecordPreconditionError,
    RepositoryUnavailableError,
)
from intake_api.service import IdempotencyKeyReuseError, IntakeService
from intake_api.validation import (
    IntakeValidationError,
    get_intake_schema,
)


logger = logging.getLogger(__name__)
PROBLEM_MEDIA_TYPE = "application/problem+json"
_PROBLEM_SCHEMA = {"$ref": "#/components/schemas/ProblemDetail"}
_UUID_PATTERN = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    "[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_ETAG_HEADER = {
    "description": (
        "Opaque entity tag for the returned representation. Send it in "
        "If-Match when replacing or submitting the draft."
    ),
    "schema": {"type": "string"},
}
_LOCATION_HEADER = {
    "description": "Relative URL of the created intake request.",
    "schema": {"type": "string", "format": "uri-reference"},
}
_RETRY_AFTER_HEADER = {
    "description": "Minimum number of seconds to wait before retrying.",
    "schema": {"type": "integer", "minimum": 1},
}
_WWW_AUTHENTICATE_HEADER = {
    "description": (
        "Authentication challenge for the Microsoft Entra bearer token."
    ),
    "schema": {"type": "string"},
}
_ERROR_CODE_HEADER = {
    "description": "Stable machine-readable error code.",
    "schema": {"type": "string"},
}
_CACHE_CONTROL_HEADER = {
    "description": "Caching directives for the authenticated representation.",
    "schema": {"type": "string"},
}


def _problem_response(
    description: str,
    *,
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_headers = {"x-ms-error-code": _ERROR_CODE_HEADER}
    if headers:
        response_headers.update(headers)
    return {
        "description": description,
        "content": {PROBLEM_MEDIA_TYPE: {"schema": _PROBLEM_SCHEMA}},
        "headers": response_headers,
    }


COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _problem_response("Invalid intake request"),
    401: _problem_response(
        "Authentication required",
        headers={"WWW-Authenticate": _WWW_AUTHENTICATE_HEADER},
    ),
    403: _problem_response("Insufficient permission"),
    404: _problem_response("Request not found"),
    409: _problem_response("Request state conflict"),
    412: _problem_response("ETag precondition failed"),
    428: _problem_response("If-Match is required"),
    429: _problem_response(
        "Persistence request throttled",
        headers={"Retry-After": _RETRY_AFTER_HEADER},
    ),
    500: _problem_response("Persistence operation failed"),
    503: _problem_response("Dependency unavailable"),
}


def _responses(
    *status_codes: int,
    success: dict[int, dict[str, Any]] | None = None,
) -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = dict(success or {})
    responses.update(
        {
            status_code: COMMON_ERROR_RESPONSES[status_code]
            for status_code in status_codes
        }
    )
    return responses


CREATE_RESPONSES = _responses(
    400,
    401,
    403,
    409,
    429,
    500,
    503,
    success={
        201: {
            "description": "Intake request draft created or replayed.",
            "headers": {
                "ETag": _ETAG_HEADER,
                "Location": _LOCATION_HEADER,
            },
        }
    },
)
GET_RESPONSES = _responses(
    400,
    401,
    404,
    429,
    500,
    503,
    success={
        200: {
            "description": "Intake request returned.",
            "headers": {
                "ETag": _ETAG_HEADER,
                "Cache-Control": _CACHE_CONTROL_HEADER,
            },
        },
        304: {
            "description": "The representation matches If-None-Match.",
            "headers": {
                "ETag": _ETAG_HEADER,
                "Cache-Control": _CACHE_CONTROL_HEADER,
            },
        },
    },
)
LIST_RESPONSES = _responses(400, 401, 429, 500, 503)
REPLACE_RESPONSES = _responses(
    400,
    401,
    403,
    404,
    409,
    412,
    428,
    429,
    500,
    503,
    success={
        200: {
            "description": "Intake request draft replaced.",
            "headers": {"ETag": _ETAG_HEADER},
        }
    },
)
SUBMIT_RESPONSES = _responses(
    400,
    401,
    403,
    404,
    412,
    428,
    429,
    500,
    503,
    success={
        200: {
            "description": "Intake request submitted or already submitted.",
            "headers": {"ETag": _ETAG_HEADER},
        }
    },
)


def create_app(
    *,
    settings: IntakeSettings | None = None,
    repository: IntakeRepository | None = None,
    token_validator: TokenValidator | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or IntakeSettings.from_env()
        application.state.settings = resolved_settings

        owned_repository: CosmosIntakeRepository | None = None
        if repository is None:
            owned_repository = CosmosIntakeRepository.create_client(
                endpoint=resolved_settings.cosmos_endpoint,
                database_name=resolved_settings.cosmos_database_name,
                container_name=resolved_settings.cosmos_container_name,
            )
            application.state.repository = owned_repository
        else:
            application.state.repository = repository

        owned_http_client: httpx.AsyncClient | None = None
        if token_validator is None:
            owned_http_client = httpx.AsyncClient(timeout=10.0)
            application.state.token_validator = TokenValidator(
                resolved_settings, owned_http_client
            )
        else:
            application.state.token_validator = token_validator

        try:
            yield
        finally:
            if owned_http_client is not None:
                await owned_http_client.aclose()
            if owned_repository is not None:
                await owned_repository.close()

    application = FastAPI(
        title="Intake Request API",
        version="1.0.0",
        description=(
            "Creates and submits tenant-isolated innovation intake requests. "
            "Operation IDs are stable for Azure API Management MCP projection."
        ),
        lifespan=lifespan,
        contact={
            "name": "Internal Intake API",
            "url": "https://github.com/duongthaiha/internal-intake-agent",
        },
    )
    application.state.settings = settings
    application.state.repository = repository
    application.state.token_validator = token_validator
    _register_exception_handlers(application)
    _register_routes(application)
    _install_openapi_schema(application)
    return application


def _get_repository(request: Request) -> IntakeRepository:
    return request.app.state.repository


def _get_service(
    request: Request,
    repository: IntakeRepository = Depends(_get_repository),
) -> IntakeService:
    return IntakeService(repository, request.app.state.settings)


def _require_etag(if_match: str | None) -> str:
    if not if_match:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="The If-Match header is required.",
        )
    return if_match


def _response(record: IntakeRecord, response: Response) -> IntakeRecordResponse:
    if record.etag:
        response.headers["ETag"] = record.etag
    return IntakeRecordResponse.from_record(record)


def _register_routes(application: FastAPI) -> None:
    @application.get(
        "/health/live",
        tags=["health"],
        operation_id="get_liveness",
        include_in_schema=False,
    )
    async def get_liveness() -> dict[str, str]:
        return {"status": "ok"}

    @application.get(
        "/health/ready",
        tags=["health"],
        operation_id="get_readiness",
        include_in_schema=False,
    )
    async def get_readiness(
        repository: IntakeRepository = Depends(_get_repository),
    ) -> dict[str, str]:
        await repository.check_ready()
        return {"status": "ready"}

    @application.post(
        "/v1/intake-requests",
        tags=["intake requests"],
        operation_id="create_intake_request",
        response_model=IntakeRecordResponse,
        status_code=status.HTTP_201_CREATED,
        responses=CREATE_RESPONSES,
        summary="Create an intake request draft",
        description=(
            "Creates a mutable draft owned by the authenticated caller. "
            "Idempotency-Key is optional; replaying the same validated payload "
            "with the same key returns the original resource, while reusing "
            "the key for another payload returns 409."
        ),
    )
    async def create_intake_request(
        response: Response,
        intake: dict[str, Any] = Body(...),
        idempotency_key: Annotated[
            str | None,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=255,
                pattern=r"^[\x21-\x7E]+$",
                description=(
                    "Opaque retry key scoped to the tenant and caller. A key "
                    "must never be reused for another payload."
                ),
            ),
        ] = None,
        principal: Principal = Depends(require_write_access),
        service: IntakeService = Depends(_get_service),
    ) -> IntakeRecordResponse:
        record = await service.create(principal, intake, idempotency_key)
        response.headers["Location"] = f"/v1/intake-requests/{record.id}"
        return _response(record, response)

    @application.get(
        "/v1/intake-requests/{request_id}",
        tags=["intake requests"],
        operation_id="get_intake_request",
        response_model=IntakeRecordResponse,
        responses=GET_RESPONSES,
        summary="Get an intake request",
        description=(
            "Returns a caller-authorized intake request. Unauthorized records "
            "are hidden as 404. If-None-Match can be used with a previous ETag "
            "to receive 304 when the representation is unchanged."
        ),
    )
    async def get_intake_request(
        request_id: Annotated[
            str,
            Path(
                description="Intake request UUID.",
                pattern=_UUID_PATTERN,
                json_schema_extra={"format": "uuid"},
            ),
        ],
        response: Response,
        if_none_match: Annotated[
            str | None,
            Header(
                alias="If-None-Match",
                description="ETag value from a previous GET response.",
            ),
        ] = None,
        principal: Principal = Depends(get_principal),
        service: IntakeService = Depends(_get_service),
    ) -> Any:
        record = await service.get(principal, request_id)
        if (
            record.etag
            and if_none_match
            and _etag_matches(if_none_match, record.etag)
        ):
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED,
                headers={
                    "ETag": record.etag,
                    "Cache-Control": "private, no-cache",
                },
            )
        response.headers["Cache-Control"] = "private, no-cache"
        return _response(record, response)

    @application.get(
        "/v1/intake-requests",
        tags=["intake requests"],
        operation_id="list_intake_requests",
        response_model=IntakeRecordPage,
        responses=LIST_RESPONSES,
        summary="List authorized intake requests",
        description=(
            "Lists the caller's records, or tenant records for a privileged "
            "reader, in descending update order. continuationToken is opaque "
            "and is valid only with the same caller and filters."
        ),
    )
    async def list_intake_requests(
        request_status: RequestStatus | None = Query(
            default=None, alias="status"
        ),
        limit: int = Query(default=25, ge=1, le=100),
        continuation_token: str | None = Query(
            default=None, alias="continuationToken"
        ),
        principal: Principal = Depends(get_principal),
        service: IntakeService = Depends(_get_service),
    ) -> IntakeRecordPage:
        page = await service.list(
            principal,
            request_status=request_status,
            limit=limit,
            continuation_token=continuation_token,
        )
        return IntakeRecordPage(
            items=[
                IntakeRecordResponse.from_record(item) for item in page.items
            ],
            continuationToken=page.continuation_token,
        )

    @application.put(
        "/v1/intake-requests/{request_id}",
        tags=["intake requests"],
        operation_id="replace_intake_request",
        response_model=IntakeRecordResponse,
        responses=REPLACE_RESPONSES,
        summary="Replace an intake request draft",
        description=(
            "Fully replaces an existing mutable draft. If-Match is required "
            "and must contain the latest ETag returned by create, get, replace, "
            "or submit."
        ),
    )
    async def replace_intake_request(
        request_id: Annotated[
            str,
            Path(
                description="Intake request UUID.",
                pattern=_UUID_PATTERN,
                json_schema_extra={"format": "uuid"},
            ),
        ],
        response: Response,
        intake: dict[str, Any] = Body(...),
        if_match: str | None = Header(
            default=None,
            alias="If-Match",
            description="Latest ETag for the intake request.",
        ),
        principal: Principal = Depends(require_write_access),
        service: IntakeService = Depends(_get_service),
    ) -> IntakeRecordResponse:
        record = await service.replace(
            principal, request_id, intake, _require_etag(if_match)
        )
        return _response(record, response)

    @application.post(
        "/v1/intake-requests/{request_id}/submit",
        tags=["intake requests"],
        operation_id="submit_intake_request",
        response_model=IntakeRecordResponse,
        responses=SUBMIT_RESPONSES,
        summary="Submit an intake request",
        description=(
            "Transitions a mutable draft to submitted. Repeating the operation "
            "after it succeeds returns the submitted representation. If-Match "
            "is required for the initial transition."
        ),
    )
    async def submit_intake_request(
        request_id: Annotated[
            str,
            Path(
                description="Intake request UUID.",
                pattern=_UUID_PATTERN,
                json_schema_extra={"format": "uuid"},
            ),
        ],
        response: Response,
        if_match: str | None = Header(
            default=None,
            alias="If-Match",
            description="Latest ETag for the intake request.",
        ),
        principal: Principal = Depends(require_write_access),
        service: IntakeService = Depends(_get_service),
    ) -> IntakeRecordResponse:
        record = await service.submit(
            principal, request_id, _require_etag(if_match)
        )
        return _response(record, response)


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(IntakeValidationError)
    async def intake_validation_handler(
        request: Request, exc: IntakeValidationError
    ) -> JSONResponse:
        return _problem(
            request,
            400,
            "Invalid intake request",
            exc.detail,
            "InvalidIntakeRequest",
        )

    @application.exception_handler(RecordNotFoundError)
    async def not_found_handler(
        request: Request, exc: RecordNotFoundError
    ) -> JSONResponse:
        return _problem(
            request,
            404,
            "Request not found",
            str(exc),
            "IntakeRequestNotFound",
        )

    @application.exception_handler(RecordConflictError)
    async def conflict_handler(
        request: Request, exc: RecordConflictError
    ) -> JSONResponse:
        return _problem(
            request,
            409,
            "Request conflict",
            str(exc),
            "IntakeRequestConflict",
        )

    @application.exception_handler(IdempotencyKeyReuseError)
    async def idempotency_key_reuse_handler(
        request: Request, exc: IdempotencyKeyReuseError
    ) -> JSONResponse:
        return _problem(
            request,
            409,
            "Idempotency key conflict",
            str(exc),
            "IdempotencyKeyReuse",
        )

    @application.exception_handler(RecordPreconditionError)
    async def precondition_handler(
        request: Request, exc: RecordPreconditionError
    ) -> JSONResponse:
        return _problem(
            request,
            412,
            "Precondition failed",
            str(exc),
            "PreconditionFailed",
        )

    @application.exception_handler(InvalidContinuationTokenError)
    async def invalid_continuation_token_handler(
        request: Request, exc: InvalidContinuationTokenError
    ) -> JSONResponse:
        return _problem(
            request,
            400,
            "Invalid continuation token",
            str(exc),
            "InvalidContinuationToken",
        )

    @application.exception_handler(RepositoryUnavailableError)
    async def unavailable_handler(
        request: Request, exc: RepositoryUnavailableError
    ) -> JSONResponse:
        logger.warning("Intake persistence dependency is unavailable.")
        return _problem(
            request,
            503,
            "Dependency unavailable",
            "Intake persistence is temporarily unavailable.",
            "PersistenceUnavailable",
        )

    @application.exception_handler(RepositoryThrottledError)
    async def throttled_handler(
        request: Request, exc: RepositoryThrottledError
    ) -> JSONResponse:
        response = _problem(
            request,
            429,
            "Persistence request throttled",
            "Intake persistence is temporarily throttled.",
            "PersistenceThrottled",
        )
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response

    @application.exception_handler(RepositoryOperationError)
    async def operation_error_handler(
        request: Request, exc: RepositoryOperationError
    ) -> JSONResponse:
        logger.error(
            "Cosmos DB rejected an intake persistence operation: %s", exc
        )
        return _problem(
            request,
            500,
            "Persistence operation failed",
            "The intake persistence operation could not be completed.",
            "PersistenceOperationFailed",
        )

    @application.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        title, error_code = {
            400: ("Invalid request", "InvalidRequest"),
            401: ("Authentication required", "AuthenticationRequired"),
            403: ("Forbidden", "AuthorizationFailed"),
            404: ("Request not found", "IntakeRequestNotFound"),
            409: ("Request conflict", "IntakeRequestConflict"),
            428: ("Precondition required", "PreconditionRequired"),
            503: ("Service unavailable", "ServiceUnavailable"),
        }.get(exc.status_code, ("Request failed", "RequestFailed"))
        response = _problem(
            request,
            exc.status_code,
            title,
            str(exc.detail),
            error_code,
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            request,
            400,
            "Invalid request",
            "Request parameters are invalid.",
            "InvalidRequest",
        )


def _problem(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    error_code: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={"x-ms-error-code": error_code},
        content={
            "type": f"urn:internal-intake:problem:{error_code}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
        },
    )


def _install_openapi_schema(application: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
            contact=application.contact,
        )
        schema["openapi"] = "3.1.0"
        _embed_intake_schema(schema)
        for path, method in (
            ("/v1/intake-requests", "post"),
            ("/v1/intake-requests/{request_id}", "put"),
        ):
            schema["paths"][path][method]["requestBody"]["content"][
                "application/json"
            ]["schema"] = {"$ref": "#/components/schemas/IntakeRequest"}
        schema["components"]["securitySchemes"] = {
            "MicrosoftEntraBearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Microsoft Entra ID access token.",
            }
        }
        for path_item in schema["paths"].values():
            for operation in path_item.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    operation["security"] = [{"MicrosoftEntraBearer": []}]
                    operation["responses"].pop("422", None)
                    for parameter in operation.get("parameters", []):
                        if parameter.get("name") == "If-Match":
                            parameter["required"] = True
                            parameter["schema"] = {"type": "string"}
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi


def _embed_intake_schema(openapi_schema: dict[str, Any]) -> None:
    intake_schema = deepcopy(get_intake_schema())
    definitions = intake_schema.pop("$defs", {})
    intake_schema.pop("$schema", None)
    intake_schema.pop("$id", None)
    intake_schema.pop("$comment", None)

    def rewrite_refs(value: Any) -> Any:
        if isinstance(value, dict):
            rewritten: dict[str, Any] = {}
            for key, child in value.items():
                if (
                    key == "$ref"
                    and isinstance(child, str)
                    and child.startswith("#/$defs/")
                ):
                    name = child.removeprefix("#/$defs/")
                    rewritten[key] = (
                        f"#/components/schemas/IntakeRequest_{name}"
                    )
                else:
                    rewritten[key] = rewrite_refs(child)
            return rewritten
        if isinstance(value, list):
            return [rewrite_refs(child) for child in value]
        return value

    schemas = openapi_schema["components"]["schemas"]
    schemas["ProblemDetail"] = ProblemDetail.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    schemas["IntakeRequest"] = rewrite_refs(intake_schema)
    schemas["IntakeRecordResponse"]["properties"]["intake"] = {
        "$ref": "#/components/schemas/IntakeRequest"
    }
    for name, definition in definitions.items():
        schemas[f"IntakeRequest_{name}"] = rewrite_refs(definition)


def _etag_matches(if_none_match: str, current_etag: str) -> bool:
    def normalize(value: str) -> str:
        value = value.strip()
        return value[2:] if value.startswith("W/") else value

    normalized_current = normalize(current_etag)
    return any(
        normalize(candidate) in {"*", normalized_current}
        for candidate in if_none_match.split(",")
    )


app = create_app()
