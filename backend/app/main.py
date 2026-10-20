import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.domain.errors import NotaryError
from app.repository import Repository
from app.worker import AnchorWorker

settings = get_settings()


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | list[Any] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    error["traceId"] = request.state.trace_id
    return JSONResponse(status_code=status_code, content={"error": error})


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = Repository(settings.database_file)
    await repository.initialize()
    worker = AnchorWorker(settings, repository)
    app.state.repository = repository
    app.state.worker = worker
    task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        worker.stop()
        await task


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="EMS Daily Manifest verification and blockchain anchoring service",
    lifespan=lifespan,
)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    request.state.trace_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    response = await call_next(request)
    response.headers["X-Trace-ID"] = request.state.trace_id
    return response


@app.exception_handler(NotaryError)
async def notary_error_handler(request: Request, error: NotaryError) -> JSONResponse:
    return error_response(
        request,
        error.status_code,
        error.code,
        error.message,
        error.details,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return error_response(
        request,
        400,
        "SCHEMA_INVALID",
        "Request body is not valid JSON for this endpoint",
        error.errors(),
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
        settings.mtls_fingerprint_header,
        settings.mtls_site_header,
    ],
)

app.include_router(router, prefix=settings.api_prefix)
