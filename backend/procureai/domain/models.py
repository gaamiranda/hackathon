"""Domain contracts for ProcureAI (PLAN.md §4).

Pure data: no arithmetic, scoring, or LLM logic lives here. Money is Decimal,
currencies are ISO-4217 codes, all ids are strings. Frozen after T1 unless
PLAN.md records a change.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- #
# Shared scalar types
# --------------------------------------------------------------------------- #

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="ISO-4217 code")]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Ratio = Annotated[float, Field(ge=0.0, le=1.0)]
Percent = Annotated[Decimal, Field(ge=0, le=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class QuoteSource(StrEnum):
    PDF = "pdf"
    XLSX = "xlsx"
    EMAIL = "email"


class WorkflowState(StrEnum):
    CREATED = "CREATED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"  # resting state: all documents extracted, nothing pending (D15)
    NEEDS_HUMAN_EXTRACTION = "NEEDS_HUMAN_EXTRACTION"
    VALIDATING = "VALIDATING"
    CALC_MISMATCH = "CALC_MISMATCH"
    ENRICHING = "ENRICHING"
    SCORING = "SCORING"
    RECOMMENDED = "RECOMMENDED"
    NEGOTIATION_DRAFTED = "NEGOTIATION_DRAFTED"
    AWAITING_NEGOTIATION_APPROVAL = "AWAITING_NEGOTIATION_APPROVAL"
    NEGOTIATING = "NEGOTIATING"
    COUNTER_RECEIVED = "COUNTER_RECEIVED"
    RE_SCORING = "RE_SCORING"
    AWAITING_PO_APPROVAL = "AWAITING_PO_APPROVAL"
    PO_GENERATED = "PO_GENERATED"
    REPLANNING = "REPLANNING"


class EventActor(StrEnum):
    AGENT = "agent"
    ENGINE = "engine"
    HUMAN = "human"
    SUPPLIER = "supplier"


class NegotiationRole(StrEnum):
    BUYER = "buyer"  # drafted by the Negotiation Agent, sent only after human approval
    SUPPLIER = "supplier"  # simulated supplier reply


class NegotiationStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CLOSED = "closed"


# --------------------------------------------------------------------------- #
# Request + config
# --------------------------------------------------------------------------- #


class ProcurementRequest(StrictModel):
    id: str
    product: str
    quantity: int = Field(gt=0)
    required_by: date
    budget: Money
    currency: Currency
    created_at: datetime
    version: int = Field(default=1, ge=1, description="Increments on every interrupt")


class ScoringWeights(StrictModel):
    price: Ratio
    lead_time: Ratio
    reliability: Ratio
    risk: Ratio

    @model_validator(mode="after")
    def _sums_to_one(self) -> "ScoringWeights":
        total = self.price + self.lead_time + self.reliability + self.risk
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total}")
        return self


class Thresholds(StrictModel):
    max_defect_rate: Ratio = 0.05
    min_confidence: Ratio = 0.85
    tie_margin: float = Field(default=2.0, ge=0, description="Top-2 score gap that triggers escalation")


class NegotiationBoundaries(StrictModel):
    max_discount_ask_pct: Percent
    min_lead_time_days: int = Field(ge=0)
    max_rounds: int = Field(default=2, ge=1, le=2, description="Hard cap per supplier (G2)")


class ApprovalRequirements(StrictModel):
    negotiation_send: bool = True
    po_generation: bool = True


class ProcurementConfig(StrictModel):
    weights: ScoringWeights
    thresholds: Thresholds = Thresholds()
    negotiation: NegotiationBoundaries
    approvals: ApprovalRequirements = ApprovalRequirements()
    tax_rate_pct: Percent = Decimal("0")


# --------------------------------------------------------------------------- #
# Quotes
# --------------------------------------------------------------------------- #


class RawDocument(StrictModel):
    """A supplier document already reduced to plain text (parsed locally, never sent as binary)."""

    doc_id: str
    filename: str
    source: QuoteSource
    text: str


class NormalizedQuote(StrictModel):
    """Output of the Document Agent. Untrusted content, already structured."""

    quote_id: str
    supplier_id: str
    supplier_name: str
    source: QuoteSource
    unit_price: Money
    currency: Currency
    quantity_quoted: int = Field(gt=0)
    moq: int = Field(ge=0)
    lead_time_days: int = Field(ge=0)
    payment_terms: str | None = None
    shipping_cost: Money = Decimal("0")
    discount_pct: Percent = Decimal("0")
    validity_date: date | None = None
    capacity_units: int | None = Field(default=None, ge=0)
    llm_stated_total: Money | None = Field(
        default=None, description="Total as written in the document; only used for mismatch detection (G1)"
    )
    field_confidence: dict[str, Ratio] = Field(default_factory=dict)
    raw_excerpt: str = ""


class QuoteChecks(StrictModel):
    moq_ok: bool
    lead_time_ok: bool
    budget_ok: bool
    math_ok: bool
    capacity_ok: bool = True


class ValidatedQuote(NormalizedQuote):
    """NormalizedQuote plus engine-computed costs and checks."""

    subtotal: Money
    discount: Money
    shipping: Money
    pre_tax_total: Money
    tax: Money
    landed_cost: Money
    checks: QuoteChecks
    issues: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Supplier intelligence + scoring
# --------------------------------------------------------------------------- #


class SupplierProfile(StrictModel):
    supplier_id: str
    name: str
    on_time_rate: Ratio
    defect_rate: Ratio
    orders_completed: int = Field(ge=0)
    avg_lead_time_days: int = Field(ge=0)
    max_capacity_units: int = Field(ge=0)
    blacklisted: bool = False
    notes: str = ""


class Scorecard(StrictModel):
    supplier_id: str
    landed_cost: Money
    lead_time_days: int = Field(ge=0)
    reliability_score: Ratio
    risk_score: Ratio = Field(description="0 = no risk, 1 = maximum risk")
    total_score: float = Field(ge=0, le=100)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    eligible: bool
    ineligibility_reasons: list[str] = Field(default_factory=list)


class Escalation(StrictModel):
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class Recommendation(StrictModel):
    run_id: str
    request_version: int = Field(ge=1)
    ranked: list[str] = Field(description="supplier_ids, best first")
    recommended_supplier_id: str | None
    rationale: str
    change_explanation: str | None = None
    escalation: Escalation | None = None


# --------------------------------------------------------------------------- #
# Negotiation
# --------------------------------------------------------------------------- #


class NegotiationOffer(StrictModel):
    """Price and lead time only (D7)."""

    unit_price: Money
    lead_time_days: int = Field(ge=0)


class NegotiationTurn(StrictModel):
    role: NegotiationRole
    message: str
    offer: NegotiationOffer | None = None
    approved_by_human: bool = False
    ts: datetime


class NegotiationThread(StrictModel):
    run_id: str
    supplier_id: str
    turns: list[NegotiationTurn] = Field(default_factory=list)
    status: NegotiationStatus = NegotiationStatus.OPEN
    boundaries: NegotiationBoundaries


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #


class WorkflowEvent(StrictModel):
    run_id: str
    seq: int = Field(ge=0)
    ts: datetime
    actor: EventActor
    type: str
    state_before: WorkflowState | None = None
    state_after: WorkflowState | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


# --------------------------------------------------------------------------- #
# Purchase order
# --------------------------------------------------------------------------- #


class SupplierRef(StrictModel):
    supplier_id: str
    name: str


class PurchaseOrderLine(StrictModel):
    description: str
    quantity: int = Field(gt=0)
    unit_price: Money
    line_total: Money


class PurchaseOrderTotals(StrictModel):
    subtotal: Money
    discount: Money
    shipping: Money
    tax: Money
    total: Money


class PurchaseOrder(StrictModel):
    po_number: str
    run_id: str
    supplier: SupplierRef
    currency: Currency
    line_items: list[PurchaseOrderLine] = Field(min_length=1)
    totals: PurchaseOrderTotals
    approved_by: str
    approved_at: datetime


# --------------------------------------------------------------------------- #
# Run aggregate (workflow state owned by the backend)
# --------------------------------------------------------------------------- #


class PendingHumanKind(StrEnum):
    EXTRACTION = "extraction"  # low-confidence / missing critical fields
    CALC_MISMATCH = "calc_mismatch"  # stated total != computed total (G1)


class PendingHuman(StrictModel):
    kind: PendingHumanKind
    quote_ids: list[str]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Run(StrictModel):
    run_id: str
    request: ProcurementRequest
    config: ProcurementConfig
    state: WorkflowState = WorkflowState.CREATED
    documents: list[RawDocument] = Field(default_factory=list)
    quotes: list[NormalizedQuote] = Field(default_factory=list)
    validated: list[ValidatedQuote] = Field(default_factory=list)
    scorecards: list[Scorecard] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    pending_human: PendingHuman | None = None
    created_at: datetime
    updated_at: datetime


# Every top-level contract, in export/fixture order.
CONTRACT_MODELS: list[type[BaseModel]] = [
    ProcurementRequest,
    ProcurementConfig,
    NormalizedQuote,
    ValidatedQuote,
    SupplierProfile,
    Scorecard,
    Recommendation,
    NegotiationTurn,
    NegotiationThread,
    WorkflowEvent,
    PurchaseOrder,
    RawDocument,
    Run,
]
