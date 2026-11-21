"""Silver data-quality rules: one definition, used by the pipeline and the tests.

Each rule is a SQL boolean expression over silver columns that must be TRUE for a
row to be kept. Every rule cites the EDA finding that justifies it
(notebooks/01_eda_yellow_2025_01.ipynb). Expressions use only operators that mean
the same in Spark SQL (ANSI mode) and DuckDB: comparisons, BETWEEN, arithmetic
without division by a column (no divide-by-zero under ANSI), IS [NOT] NULL, IN.

* ``DROP_RULES`` - violating rows are dropped (``@dp.expect_all_or_drop``).
* ``WARN_RULES`` - violations are counted in the pipeline event log but kept.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Rule:
    expression: str
    evidence: str


_DROP: dict[str, Rule] = {
    "timestamps_present": Rule(
        "pickup_datetime IS NOT NULL AND dropoff_datetime IS NOT NULL",
        "Every downstream feature and the target derive from the two timestamps.",
    ),
    "pickup_in_source_month": Rule(
        "pickup_datetime >= source_month AND pickup_datetime < source_month + INTERVAL 1 MONTH",
        "EDA s1: pickups spill into Dec 2024 and Feb 2025 in the January file.",
    ),
    "duration_1_to_120_min": Rule(
        "duration_min >= 1 AND duration_min <= 120",
        "EDA s3: <1 min is meter tests/cancellations; >120 min is meters left running "
        "(20 trips exceed a day). 60 min would cut legitimate airport trips.",
    ),
    "distance_positive_max_100_mi": Rule(
        "trip_distance > 0 AND trip_distance <= 100",
        "EDA s2/s2b: 2.6% zero distances; maximum is 276,424 miles.",
    ),
    "speed_at_most_80_mph": Rule(
        # distance / (duration / 60) <= 80, rearranged to avoid dividing by a column.
        "trip_distance * 60 <= 80 * duration_min",
        "EDA s4: implied speeds above 80 mph are physically implausible in NYC.",
    ),
    "location_ids_valid": Rule(
        "pickup_location_id BETWEEN 1 AND 265 AND dropoff_location_id BETWEEN 1 AND 265",
        "TLC zone IDs are 1-265; anything else is corrupt.",
    ),
}

_WARN: dict[str, Rule] = {
    "known_vendor": Rule(
        "vendor_id IN (1, 2, 6, 7)",
        "TLC data dictionary 2025: 1 CMT, 2 Curb, 6 Myle, 7 Helix.",
    ),
    "known_rate_code": Rule(
        "rate_code_id IS NULL OR rate_code_id IN (1, 2, 3, 4, 5, 6, 99)",
        "EDA s2: codes 1-6 and 99 (unknown) observed; NULL is the metadata-missing cohort.",
    ),
}

DROP_RULES: Final = MappingProxyType(_DROP)
WARN_RULES: Final = MappingProxyType(_WARN)


def as_expectations(rules: MappingProxyType[str, Rule]) -> dict[str, str]:
    """Shape expected by ``@dp.expect_all*``: ``{name: sql_expression}``."""
    return {name: rule.expression for name, rule in rules.items()}
