"""Deterministic scripted supplier (PLAN.md §5 simulate_supplier_reply, D17).

Replies come from data/supplier_personas.json, keyed by supplier_id and buyer round (1 or 2).
Reply text is untrusted supplier content: the workflow stores it verbatim and never reads
instructions from it (G4). Unknown suppliers fall back to the "default" persona, which rejects.
"""

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from procureai.domain.models import NegotiationOffer

PERSONAS_PATH = Path(__file__).resolve().parents[3] / "data" / "supplier_personas.json"
FALLBACK_PERSONA = "default"


class SupplierReply(BaseModel):
    reply_text: str
    offer: NegotiationOffer | None = None  # None = the supplier rejects the ask


class SupplierSim(Protocol):
    def reply(self, supplier_id: str, round: int) -> SupplierReply:
        """The supplier's answer to the buyer's `round`-th message (1-based)."""
        ...


class ScriptedSupplier:
    def __init__(self, personas_path: Path = PERSONAS_PATH) -> None:
        raw = json.loads(personas_path.read_text())
        self._personas: dict[str, dict[int, SupplierReply]] = {
            sid: {int(r): SupplierReply.model_validate(reply) for r, reply in persona["rounds"].items()}
            for sid, persona in raw.items()
            if not sid.startswith("_")
        }

    def reply(self, supplier_id: str, round: int) -> SupplierReply:
        persona = self._personas.get(supplier_id) or self._personas[FALLBACK_PERSONA]
        if round not in persona:
            raise KeyError(f"no scripted reply for {supplier_id!r} round {round}; known rounds {sorted(persona)}")
        return persona[round].model_copy(deep=True)
