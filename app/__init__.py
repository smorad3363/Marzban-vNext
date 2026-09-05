import logging
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import ALLOWED_ORIGINS, DOCS, XRAY_SUBSCRIPTION_PATH

__version__ = "5.2.0"

app = FastAPI(
    title="Network Control API",
    description="Private network user, routing, and node management API",
    version=__version__,
    docs_url="/docs" if DOCS else None,
    redoc_url="/redoc" if DOCS else None,
)

scheduler = BackgroundScheduler(
    {"apscheduler.job_defaults.max_instances": 20}, timezone="UTC"
)
logger = logging.getLogger("uvicorn.error")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from app import dashboard, jobs, routers, telegram  # noqa
from app.routers import api_router  # noqa

app.include_router(api_router)


from app.utils.marzhelp_policy import (  # noqa: E402
    MarzhelpPolicyError,
    record_quota_rejection,
)
from app.utils.api_errors import (  # noqa: E402
    http_error_detail,
    internal_error_detail,
    request_id,
    safe_request_id,
    validation_error_detail,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = safe_request_id(
        request.headers.get("X-Request-ID"), uuid4().hex
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(MarzhelpPolicyError)
def marzhelp_policy_exception_handler(request: Request, exc: MarzhelpPolicyError):
    record_quota_rejection(exc)
    return JSONResponse(
        status_code=(
            status.HTTP_403_FORBIDDEN
            if exc.code == "device_limit_penalty_active"
            else status.HTTP_409_CONFLICT
        ),
        content={
            "detail": http_error_detail(
                status.HTTP_403_FORBIDDEN
                if exc.code == "device_limit_penalty_active"
                else status.HTTP_409_CONFLICT,
                {"code": exc.code, "message": str(exc)},
                request_id(request),
            )
        },
    )


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": http_error_detail(exc.status_code, exc.detail, request_id(request))},
        headers=exc.headers,
    )


@app.exception_handler(IntegrityError)
def database_conflict_handler(request: Request, exc: IntegrityError):
    logger.warning("Database conflict request_id=%s", request_id(request))
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": http_error_detail(
                status.HTTP_409_CONFLICT,
                {"code": "DATABASE_CONFLICT"},
                request_id(request),
            )
        },
    )


def use_route_names_as_operation_ids(app: FastAPI) -> None:
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.operation_id = route.name


use_route_names_as_operation_ids(app)


@app.on_event("startup")
def on_startup():
    paths = [f"{r.path}/" for r in app.routes]
    paths.append("/api/")
    if f"/{XRAY_SUBSCRIPTION_PATH}/" in paths:
        raise ValueError(
            f"you can't use /{XRAY_SUBSCRIPTION_PATH}/ as subscription path it reserved for {app.title}"
        )
    scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder(
            {"detail": validation_error_detail(exc.errors(), request_id(request))}
        ),
    )


@app.exception_handler(Exception)
def internal_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled API error request_id=%s", request_id(request), exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": internal_error_detail(request_id(request))},
    )
