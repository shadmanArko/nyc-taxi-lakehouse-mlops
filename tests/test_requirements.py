"""The requirements register must stay true: every trace points at real code and tests."""

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[1]
REGISTER: dict[str, Any] = yaml.safe_load((ROOT / "docs/requirements.yaml").read_text())
ALL = REGISTER["functional"] + REGISTER["non_functional"]


def _ids() -> list[str]:
    return [r["id"] for r in ALL]


def test_ids_are_unique_and_well_formed() -> None:
    ids = _ids()
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"(FR|NFR)-\d{2}", i) for i in ids)


@pytest.mark.parametrize("req", REGISTER["functional"], ids=lambda r: r["id"])
def test_functional_requirements_trace_to_code_and_tests(req: dict[str, Any]) -> None:
    assert req["owner"], "every functional requirement has an owning department"
    assert req["implemented_in"], "implemented_in is required"
    for path in req["implemented_in"]:
        assert (ROOT / path).exists(), f"{req['id']}: {path} does not exist"
    assert req["evidence"]


@pytest.mark.parametrize("req", ALL, ids=lambda r: r["id"])
def test_evidence_exists(req: dict[str, Any]) -> None:
    for item in req["evidence"]:
        path, _, test_name = item.partition("::")
        file = ROOT / path
        assert file.exists(), f"{req['id']}: {path} does not exist"
        if test_name:
            assert re.search(rf"^def {re.escape(test_name)}\(", file.read_text(), re.M), (
                f"{req['id']}: test {test_name} not found in {path}"
            )


def test_portal_copy_is_up_to_date() -> None:
    spec = importlib.util.spec_from_file_location("bps", ROOT / "tools/build_portal_static.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = (ROOT / "services/web/site/data/requirements.json").read_text()
    assert committed == module.render(), "run: uv run python tools/build_portal_static.py"


def test_portal_model_card_is_up_to_date() -> None:
    served = ROOT / "services/web/site/data/model-card.md"
    assert served.read_text() == (ROOT / "docs/model-card.md").read_text(), (
        "run: uv run python tools/build_portal_static.py"
    )
