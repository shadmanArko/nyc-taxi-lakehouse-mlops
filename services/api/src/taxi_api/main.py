"""FastAPI application.

Endpoints
---------
GET  /health   liveness: the process is up (used by Docker/compose health checks)
GET  /ready    readiness: a model is loaded and predictions can be served
POST /predict  validated prediction (contract: ``taxi_contracts.TripFeatures``)
"""

import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from taxi_api import __version__
from taxi_api.config import Settings, get_settings
from taxi_api.predictor import Predictor
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


def load_predictor(settings: Settings) -> Predictor | None:
    """Load the configured model. Returns None when no model is configured.

    Phase 5 replaces the body with an MLflow registry loader. Until then the API
    starts, reports healthy, and honestly reports that it is not ready.
    """
    if settings.model_uri:
        logger.warning("Model loading is not implemented yet; ignoring %s", settings.model_uri)
    return None


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


def create_app(predictor: Predictor | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the app. Inject ``predictor``/``settings`` in tests; production uses defaults."""
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        app.state.predictor = predictor if predictor is not None else load_predictor(resolved)
        yield

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
