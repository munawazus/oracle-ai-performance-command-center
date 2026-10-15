from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.collectors.scheduler import start_collector_scheduler, stop_collector_scheduler
from app.config import get_settings
from app.exceptions import AppError, RateLimitedError
from app.logging_config import configure_logging, get_logger
from app.security.middleware import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def _cors_headers_for(request: Request) -> dict[str, str]:
    """A handler registered for the bare `Exception` type (handle_unexpected_error below) is
    routed by Starlette through ServerErrorMiddleware, which sits OUTSIDE - above - every
    middleware added via app.add_middleware(), including CORSMiddleware. Every other handler
    in this file is keyed to a specific exception type instead, which Starlette routes
    through ExceptionMiddleware (inside CORS) - confirmed empirically: only the bare-Exception
    handler's responses were missing CORS headers, live (a real unhandled exception surfaced
    to the browser as a generic CORS failure, masking the actual 500 - Master Plan Phase 27/
    QA Finding 06). This mirrors CORSMiddleware's own simple-response behavior: echo back the
    exact matching origin (never a wildcard, since credentials are allowed) rather than
    reimplementing full CORS semantics for what is deliberately a last-resort handler."""
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origins_list:
        return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Vary": "Origin"}
    return {}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    start_collector_scheduler()
    try:
        yield
    finally:
        stop_collector_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=_lifespan)

    # Middleware order matters: Starlette makes the LAST-added middleware the OUTERMOST layer,
    # so requests hit these top-to-bottom as listed and responses flow back bottom-to-top.
    # CORS must be outermost (added last) so even a 429/413 error response still carries CORS
    # headers - otherwise the browser reports a confusing generic network error instead of a
    # readable rate-limit/body-too-large message, and so it can short-circuit an OPTIONS
    # preflight before that request ever reaches the rate limiter.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(RateLimitMiddleware, limit=settings.api_rate_limit_per_minute, window_seconds=60)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RateLimitedError)
    async def handle_rate_limited_error(request: Request, exc: RateLimitedError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds is not None else {}
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message}, headers=headers)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # exc.errors() can embed the raw exception instance in "ctx" for custom validators -
        # drop it so nothing non-JSON-safe leaks out. Also drop "input": Pydantic v2 echoes
        # the raw submitted value there, which for a password/secret/token field that fails
        # validation (too long, wrong type, ...) would otherwise reflect the credential
        # itself back in a 422 response body - and from there into any error tracker or
        # proxy log that captures response bodies (Master Plan Phase 27 finding). Every
        # field is stripped uniformly rather than name-matching a "known secret fields"
        # list, since that list is exactly the kind of thing a future field addition
        # forgets to update.
        safe_errors = [{k: v for k, v in err.items() if k not in ("ctx", "input")} for err in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": jsonable_encoder(safe_errors)}
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", path=str(request.url.path), error_type=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An unexpected error occurred."},
            headers=_cors_headers_for(request),
        )

    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
