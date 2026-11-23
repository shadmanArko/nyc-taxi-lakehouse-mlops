"""FastAPI application.

Endpoints
---------
GET  /health   liveness: the process is up (used by Docker/compose health checks)
GET  /ready    readiness: a model is loaded and predictions can be served
GET  /zones    NYC taxi zones (id, borough, name) for clients
POST /predict  validated prediction (contract: ``taxi_contracts.TripFeatures``)

With ``TAXI_API_MODEL_URI=models:/<name>@<alias>`` the API serves that registry alias and
re-checks it every ``TAXI_API_MODEL_REFRESH_SECONDS``: a newly promoted champion goes live
without a restart. If MLflow is unreachable the API still starts, reports not ready, and
keeps retrying.
"""

import asyncio
import contextlib
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from taxi_api import __version__
from taxi_api.config import Settings, get_settings
from taxi_api.model_manager import ModelManager
from taxi_api.predictor import ModelCache, ModelRef, Predictor
from taxi_api.zones import Zone, load_zones
from taxi_contracts import CONTRACT_VERSION, PredictionResponse, TripFeatures

logger = logging.getLogger("taxi_api")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = __version__
    contract_version: str = CONTRACT_VERSION


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    model_name: str
    model_version: str


class ErrorResponse(BaseModel):
    detail: str


NO_MODEL: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "No model is loaded"}
}


def configure_logging(level: str) -> None:
    """Give the app logger its own handler; uvicorn only configures its own loggers."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)


async def keep_model_fresh(app: FastAPI, manager: ModelManager, interval: float) -> None:
    """Background loop: (re)load the alias's model; never crash the API over it."""
    try:
        if await asyncio.to_thread(manager.warm_start):
            app.state.predictor = manager.current
    except Exception:
        logger.exception("warm start from the local model cache failed")
    while True:
        try:
            if await asyncio.to_thread(manager.refresh):
                app.state.predictor = manager.current
        except Exception:
            logger.exception(
                "model refresh failed; still serving %s",
                getattr(manager.current, "model_version", "nothing"),
            )
        await asyncio.sleep(interval)


def get_predictor(request: Request) -> Predictor:
    """The loaded model, or 503 if there is none."""
    loaded: Predictor | None = getattr(request.app.state, "predictor", None)
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No model is loaded yet.",
        )
    return loaded


ReadyPredictor = Annotated[Predictor, Depends(get_predictor)]


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """422 with type/loc/msg only.

    FastAPI's default handler echoes the offending input back, which fails to
    serialise for NaN/Infinity (a 500 any client could trigger) and can reflect
    arbitrary payloads into responses and logs.
    """
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - registered for this type only
    errors: list[dict[str, Any]] = [
        {k: v for k, v in error.items() if k not in {"input", "ctx", "url"}}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(errors)},
    )


def create_app(
    predictor: Predictor | None = None,
    settings: Settings | None = None,
    manager: ModelManager | None = None,
) -> FastAPI:
    """Build the app. Inject ``predictor``/``manager``/``settings`` in tests."""
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        refresher: asyncio.Task[None] | None = None
        if predictor is not None:
            app.state.predictor = predictor
        else:
            live = manager
            if live is None and resolved.model_uri:
                cache = ModelCache(resolved.model_cache_dir) if resolved.model_cache_dir else None
                live = ModelManager(ModelRef.parse(resolved.model_uri), cache=cache)
            if live is not None:
                refresher = asyncio.create_task(
                    keep_model_fresh(app, live, resolved.model_refresh_seconds)
                )
        yield
        if refresher is not None:
            refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresher

    app = FastAPI(
        title="NYC Taxi Trip Duration API",
        version=__version__,
        summary="Predicts NYC yellow taxi trip duration in minutes.",
        lifespan=lifespan,
    )
    app.state.predictor = None  # defined even before lifespan runs
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.get("/health", tags=["ops"])
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/zones", tags=["reference"])
    def zones(response: Response) -> list[Zone]:
        response.headers["Cache-Control"] = "public, max-age=86400"
        return list(load_zones())

    @app.get("/ready", tags=["ops"], responses=NO_MODEL)
    def ready(model: ReadyPredictor) -> ReadyResponse:
        return ReadyResponse(model_name=model.model_name, model_version=model.model_version)

    @app.post(
        "/predict",
        tags=["prediction"],
        responses={
            **NO_MODEL,
            500: {"model": ErrorResponse, "description": "The model produced an invalid value"},
        },
    )
    def predict(features: TripFeatures, request: Request) -> PredictionResponse:
        # Resolved here, not as a dependency: FastAPI runs dependencies before body
        # validation, which would turn a client's bad input (422) into a 503.
        model = get_predictor(request)
        minutes = model.predict(features)
        if not math.isfinite(minutes):
            logger.error("Model %s:%s returned %r", model.model_name, model.model_version, minutes)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The model produced an invalid prediction.",
            )
        return PredictionResponse(
            predicted_duration_minutes=max(minutes, 0.0),
            model_name=model.model_name,
            model_version=model.model_version,
        )

    return app


app = create_app()
