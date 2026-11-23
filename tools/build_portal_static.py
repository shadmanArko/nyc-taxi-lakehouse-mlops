"""Render the portal's static data files from docs/ (requirements register, model card).

    uv run python tools/build_portal_static.py

tests/test_requirements.py fails if the committed JSON is out of date.
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "docs/requirements.yaml"
TARGET = ROOT / "services/web/site/data/requirements.json"
MODEL_CARD = ROOT / "docs/model-card.md"
MODEL_CARD_TARGET = ROOT / "services/web/site/data/model-card.md"


def render() -> str:
    return json.dumps(yaml.safe_load(SOURCE.read_text()), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render())
    MODEL_CARD_TARGET.write_text(MODEL_CARD.read_text())
    for path in (TARGET, MODEL_CARD_TARGET):
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
