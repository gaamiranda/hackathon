"""Contract tests: fixtures validate, guardrail constraints hold, schemas export."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from procureai.domain.models import CONTRACT_MODELS, NormalizedQuote, ProcurementConfig

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND.parent / "data" / "fixtures"
sys.path.insert(0, str(BACKEND / "scripts"))
from export_schemas import export  # noqa: E402


@pytest.mark.parametrize("model", CONTRACT_MODELS, ids=lambda m: m.__name__)
def test_fixture_loads_into_model(model):
    path = FIXTURES / f"{model.__name__}.json"
    assert path.exists(), f"missing fixture {path}"
    instance = model.model_validate_json(path.read_text())
    # round trip: what we serialize must validate again
    model.model_validate_json(instance.model_dump_json())


def test_normalized_quote_requires_unit_price():
    data = json.loads((FIXTURES / "NormalizedQuote.json").read_text())
    del data["unit_price"]
    with pytest.raises(ValidationError) as exc:
        NormalizedQuote.model_validate(data)
    assert any(err["loc"] == ("unit_price",) for err in exc.value.errors())


def test_config_rejects_more_than_two_negotiation_rounds():
    data = json.loads((FIXTURES / "ProcurementConfig.json").read_text())
    data["negotiation"]["max_rounds"] = 3
    with pytest.raises(ValidationError):
        ProcurementConfig.model_validate(data)


def test_config_rejects_weights_not_summing_to_one():
    data = json.loads((FIXTURES / "ProcurementConfig.json").read_text())
    data["weights"]["price"] = 0.9
    with pytest.raises(ValidationError):
        ProcurementConfig.model_validate(data)


def test_export_schemas_writes_one_file_per_model(tmp_path):
    written = export(tmp_path)
    assert sorted(p.name for p in written) == sorted(f"{m.__name__}.json" for m in CONTRACT_MODELS)
    for p in written:
        schema = json.loads(p.read_text())
        assert schema["title"] == p.stem


def test_committed_schemas_are_up_to_date(tmp_path):
    committed = BACKEND / "procureai" / "domain" / "schema"
    for p in export(tmp_path):
        assert (committed / p.name).read_text() == p.read_text(), f"{p.name} stale: rerun scripts/export_schemas.py"
