"""Write one JSON Schema per contract model to procureai/domain/schema/.

Usage: cd backend && uv run python scripts/export_schemas.py
"""

import json
from pathlib import Path

from procureai.domain.models import CONTRACT_MODELS

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "procureai" / "domain" / "schema"


def export(target: Path = SCHEMA_DIR) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for model in CONTRACT_MODELS:
        path = target / f"{model.__name__}.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in export():
        print(p.relative_to(SCHEMA_DIR.parents[2]))
