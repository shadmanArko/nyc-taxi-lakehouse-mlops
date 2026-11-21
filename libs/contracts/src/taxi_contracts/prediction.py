"""Prediction API contract (v1).

This is the request/response shape of the serving API's ``/predict`` endpoint.
The training pipeline must produce a model whose input matches ``TripFeatures``;
the serving API validates every request against it before calling the model.

Feature choice is provisional until EDA (Phase 2) and modeling (Phase 4)
confirm it. Changing a field is a contract change: bump ``CONTRACT_VERSION``
(and the package version) and regenerate the schema snapshot
(``UPDATE_CONTRACT_SNAPSHOT=1 uv run pytest libs/contracts``).
"""

from datetime import datetime
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, NaiveDatetime

CONTRACT_VERSION: Final = "1.0.0"

# NYC TLC taxi zone IDs: 1-263 are real zones, 264/265 are "Unknown"/"Outside NYC".
MIN_LOCATION_ID: Final = 1
MAX_LOCATION_ID: Final = 265

# TLC yellow-taxi trip records start in 2009; anything earlier is a client bug.
EARLIEST_PICKUP: Final = datetime(2009, 1, 1)  # noqa: DTZ001 - naive by design (NYC local)


def _plausible_pickup(value: datetime) -> datetime:
    if value < EARLIEST_PICKUP:
        msg = f"pickup_datetime must be on or after {EARLIEST_PICKUP.date()}"
        raise ValueError(msg)
    return value


LocationId = Annotated[
    int,
    Field(
        strict=True,  # no "161", 161.0 or true sneaking in through coercion
        ge=MIN_LOCATION_ID,
        le=MAX_LOCATION_ID,
    ),
]


class TripFeatures(BaseModel):
    """Everything known at the moment a trip starts (no leakage from the dropoff)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pickup_location_id: LocationId = Field(
        description="TLC taxi zone ID where the trip starts.",
        examples=[161],
    )
    dropoff_location_id: LocationId = Field(
        description="TLC taxi zone ID where the trip is expected to end.",
        examples=[237],
    )
    trip_distance: float = Field(
        gt=0,
        le=100,
        allow_inf_nan=False,
        description="Expected trip distance in miles.",
        examples=[2.4],
    )
    pickup_datetime: Annotated[NaiveDatetime, AfterValidator(_plausible_pickup)] = Field(
        description=(
            "Pickup time as NYC local wall-clock time, without a timezone offset "
            "(the TLC records timestamps this way). Offsets and Unix epochs are rejected "
            "so one instant can never reach the model as two different local hours."
        ),
        examples=["2025-01-15T08:30:00"],
    )


class PredictionResponse(BaseModel):
    """Prediction plus the lineage a caller needs to trust or debug it."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    predicted_duration_minutes: float = Field(ge=0, allow_inf_nan=False, examples=[14.2])
    model_name: str = Field(examples=["taxi-trip-duration"])
    model_version: str = Field(examples=["3"])
    contract_version: str = Field(default=CONTRACT_VERSION)
