"""NYC TLC taxi zones (official lookup table), so clients can show names, not IDs."""

import csv
from functools import lru_cache
from importlib.resources import files

from pydantic import BaseModel

# 264 "Unknown" and 265 "Outside of NYC" carry no location; the model never trains on them.
UNSERVED_ZONES = frozenset({264, 265})


class Zone(BaseModel):
    id: int
    borough: str
    zone: str
    service_zone: str


@lru_cache(maxsize=1)
def load_zones() -> tuple[Zone, ...]:
    """Source: https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"""
    text = files("taxi_api").joinpath("data/taxi_zones.csv").read_text(encoding="utf-8")
    rows = csv.DictReader(text.splitlines())
    zones = (
        Zone(
            id=int(row["LocationID"]),
            borough=row["Borough"],
            zone=row["Zone"],
            service_zone=row["service_zone"],
        )
        for row in rows
    )
    return tuple(
        sorted((z for z in zones if z.id not in UNSERVED_ZONES), key=lambda z: (z.borough, z.zone))
    )
