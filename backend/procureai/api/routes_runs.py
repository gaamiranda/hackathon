"""REST + SSE over the Orchestrator (PLAN.md §6). No business logic here."""

import asyncio
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from procureai.api.deps import get_orchestrator, get_shutdown_event
from procureai.api.extract_text import UnsupportedDocument, to_text
from procureai.config.settings import get_settings
from procureai.domain.models import (
    Currency,
    Money,
    NegotiationOffer,
    NegotiationThread,
    ProcurementConfig,
    ProcurementRequest,
    PurchaseOrder,
    QuoteChecks,
    RawDocument,
    Run,
    ScoringWeights,
    WorkflowEvent,
)
from procureai.po import render_po_pdf
from procureai.workflow import Orchestrator, WorkflowError

router = APIRouter(prefix="/runs", tags=["runs"])
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "ProcurementConfig.json"
KEEPALIVE_SECONDS = 15


# ----------------------------------------------------------------------------- request/response models


class RequestInput(BaseModel):
    """ProcurementRequest without server-assigned fields."""

    product: str
    quantity: int = Field(gt=0)
    required_by: date
    budget: Money
    currency: Currency = "USD"


class CreateRunBody(BaseModel):
    request: RequestInput
    config: ProcurementConfig | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    run: Run


class RunSummary(BaseModel):
    run_id: str
    state: str
    product: str
    quantity: int
    created_at: datetime


class RunCounts(BaseModel):
    documents: int
    quotes: int
    events: int
    negotiations: int


class RunOverview(BaseModel):
    """Header strip of the War Room (T16): the few figures a viewer needs at a glance, derived from the Run."""

    run_id: str
    state: str
    product: str
    quantity: int
    version: int
    recommended_supplier_id: str | None
    recommended_name: str | None
    total_score: float | None
    landed_cost: Money | None
    pending_human_kind: str | None
    po_number: str | None
    counts: RunCounts


class ComparisonRow(BaseModel):
    """One supplier column of the manual comparison matrix (T17, G6): engine outputs only, never LLM text.
    Scores are null before SCORING; history rates are null when the supplier has no profile."""

    supplier_id: str
    supplier_name: str
    quote_id: str
    unit_price: Money
    currency: str
    quantity_quoted: int
    moq: int
    lead_time_days: int
    shipping_cost: Money
    discount_pct: str
    payment_terms: str | None
    capacity_units: int | None
    subtotal: Money
    discount: Money
    pre_tax_total: Money
    tax: Money
    landed_cost: Money
    checks: QuoteChecks
    issues: list[str]
    negotiated_offer: NegotiationOffer | None
    negotiated: bool
    eligible: bool | None
    ineligibility_reasons: list[str]
    total_score: float | None
    score_breakdown: dict[str, float] | None
    on_time_rate: float | None
    defect_rate: float | None


class Comparison(BaseModel):
    """GET /runs/{id}/comparison: the flat matrix the Compare tab renders with the LLM down. Rows follow the
    scorecard ranking once scored, validation order before."""

    run_id: str
    state: str
    request_version: int
    quantity: int
    budget: Money
    currency: str
    required_by: date
    weights: ScoringWeights
    recommended_supplier_id: str | None
    quotes: list[ComparisonRow]


class EmailDocumentBody(BaseModel):
    email_text: str
    filename: str = "email.txt"


class CorrectQuoteBody(BaseModel):
    patch: dict[str, Any]


class ConfirmMathBody(BaseModel):
    use_computed: bool


class ApproveNegotiationBody(BaseModel):
    message: str | None = Field(default=None, description="Edited draft; omit to send the agent's draft unchanged")


class InterruptBody(BaseModel):
    """Mid-workflow requirement change (D22); at least one of the three fields must differ from the current request."""

    quantity: int | None = Field(default=None, gt=0)
    budget: Money | None = None
    required_by: date | None = None
    reason: str = ""


class ApprovePoBody(BaseModel):
    approved_by: str = Field(min_length=1, description="Name of the person approving the final supplier (G5)")


class RejectPoBody(BaseModel):
    reason: str = ""


def default_config() -> ProcurementConfig:
    return ProcurementConfig.model_validate_json(DEFAULT_CONFIG_PATH.read_text())


# ----------------------------------------------------------------------------- helpers


async def _read_documents(request: Request) -> list[RawDocument]:
    """Accept multipart (files[] and/or email_text+filename form fields) or JSON {email_text, filename}."""
    settings = get_settings()
    raw: list[tuple[str, bytes]] = []
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            body = EmailDocumentBody.model_validate(await request.json())
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors()) from exc
        raw.append((body.filename, body.email_text.encode()))
    else:
        form = await request.form()
        for upload in form.getlist("files"):
            if hasattr(upload, "read"):
                raw.append((upload.filename or "upload", await upload.read()))
        if form.get("email_text"):
            raw.append((str(form.get("filename") or "email.txt"), str(form.get("email_text")).encode()))
    if not raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no documents: send files[] or {email_text, filename}")

    docs: list[RawDocument] = []
    for filename, data in raw:
        try:
            source, text = to_text(filename, data)
        except UnsupportedDocument as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
        except Exception as exc:  # corrupt pdf/xlsx
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"could not read {filename}: {exc}") from exc
        if len(text) > settings.MAX_DOCUMENT_CHARS:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{filename}: extracted text is {len(text)} chars; limit is {settings.MAX_DOCUMENT_CHARS}",
            )
        docs.append(RawDocument(doc_id=f"doc-{uuid4().hex[:8]}", filename=filename, source=source, text=text))
    return docs


def _sse(event: WorkflowEvent) -> str:
    # No `event:` line: browsers then deliver everything via EventSource.onmessage; `type` is inside the JSON.
    return f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"


# ----------------------------------------------------------------------------- routes


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateRunResponse)
def create_run(body: CreateRunBody, orch: Orchestrator = Depends(get_orchestrator)) -> CreateRunResponse:
    request = ProcurementRequest(
        id=f"req-{uuid4().hex[:8]}",
        created_at=datetime.now(timezone.utc),
        version=1,
        **body.request.model_dump(),
    )
    run = orch.create_run(request, body.config or default_config())
    return CreateRunResponse(run_id=run.run_id, run=run)


@router.get("", response_model=list[RunSummary])
def list_runs(orch: Orchestrator = Depends(get_orchestrator)) -> list[RunSummary]:
    return [
        RunSummary(run_id=r.run_id, state=r.state, product=r.request.product, quantity=r.request.quantity, created_at=r.created_at)
        for r in orch.store.list_runs()
    ]


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    return orch.store.get(run_id)


@router.get("/{run_id}/summary", response_model=RunOverview)
def get_run_overview(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> RunOverview:
    """Compact view for the War Room header (T16). Nothing here is computed: every figure is copied from the Run."""
    run = orch.store.get(run_id)
    top = run.recommendation.recommended_supplier_id if run.recommendation else None
    card = next((c for c in run.scorecards if c.supplier_id == top), None) if top else None
    quote = next((q for q in run.quotes if q.supplier_id == top), None) if top else None
    return RunOverview(
        run_id=run.run_id,
        state=run.state,
        product=run.request.product,
        quantity=run.request.quantity,
        version=run.request.version,
        recommended_supplier_id=top,
        recommended_name=quote.supplier_name if quote else None,
        total_score=card.total_score if card else None,
        landed_cost=card.landed_cost if card else None,
        pending_human_kind=run.pending_human.kind if run.pending_human else None,
        po_number=run.purchase_order.po_number if run.purchase_order else None,
        counts=RunCounts(
            documents=len(run.documents),
            quotes=len(run.quotes),
            events=len(orch.store.events(run.run_id)),
            negotiations=len(run.negotiations),
        ),
    )


@router.get("/{run_id}/comparison", response_model=Comparison)
def get_comparison(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> Comparison:
    """Manual comparison data (T17, G6): every figure the engine validated and scored, per supplier, with nothing
    computed here and no LLM text. 409 not_validated until the engine has run (VALIDATING onward)."""
    run = orch.store.get(run_id)
    if not run.validated:
        raise WorkflowError("not_validated", f"run {run_id!r} has no validated quotes yet; upload documents and evaluate first")
    cards = {c.supplier_id: c for c in run.scorecards}
    order = [c.supplier_id for c in run.scorecards] or [v.supplier_id for v in run.validated]
    by_supplier = {v.supplier_id: v for v in run.validated}
    rows: list[ComparisonRow] = []
    for sid in order:
        v = by_supplier.get(sid)
        if v is None:
            continue
        card = cards.get(sid)
        profile = orch.agents["supplier_intel"].get_profile(sid)  # history lookup, never an LLM
        rows.append(ComparisonRow(
            supplier_id=sid, supplier_name=v.supplier_name, quote_id=v.quote_id,
            unit_price=v.unit_price, currency=v.currency, quantity_quoted=v.quantity_quoted, moq=v.moq,
            lead_time_days=v.lead_time_days, shipping_cost=v.shipping_cost, discount_pct=str(v.discount_pct),
            payment_terms=v.payment_terms, capacity_units=v.capacity_units,
            subtotal=v.subtotal, discount=v.discount, pre_tax_total=v.pre_tax_total, tax=v.tax, landed_cost=v.landed_cost,
            checks=v.checks, issues=v.issues, negotiated_offer=v.negotiated_offer, negotiated=v.negotiated,
            eligible=card.eligible if card else None,
            ineligibility_reasons=card.ineligibility_reasons if card else [],
            total_score=card.total_score if card else None,
            score_breakdown=card.score_breakdown if card else None,
            on_time_rate=profile.on_time_rate if profile else None,
            defect_rate=profile.defect_rate if profile else None,
        ))
    return Comparison(
        run_id=run.run_id, state=run.state, request_version=run.request.version,
        quantity=run.request.quantity, budget=run.request.budget, currency=run.request.currency,
        required_by=run.request.required_by, weights=run.config.weights,
        recommended_supplier_id=run.recommendation.recommended_supplier_id if run.recommendation else None,
        quotes=rows,
    )


@router.post("/{run_id}/documents", response_model=Run)
async def add_documents(run_id: str, request: Request, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    orch.store.get(run_id)  # 404 before parsing uploads
    docs = await _read_documents(request)
    return await asyncio.to_thread(orch.add_documents, run_id, docs)


@router.post("/{run_id}/documents/{doc_id}/replace", response_model=Run)
async def replace_document(run_id: str, doc_id: str, request: Request, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    orch.store.get(run_id)
    docs = await _read_documents(request)
    if len(docs) != 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "replace takes exactly one document")
    return await asyncio.to_thread(orch.replace_document, run_id, doc_id, docs[0])


@router.post("/{run_id}/evaluate", response_model=Run)
def evaluate(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    return orch.run_evaluation(run_id)


@router.post("/{run_id}/quotes/{quote_id}/correct", response_model=Run)
def correct_quote(run_id: str, quote_id: str, body: CorrectQuoteBody, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    try:
        return orch.correct_quote(run_id, quote_id, body.patch)
    except ValueError as exc:  # pydantic validation of the patch
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/{run_id}/quotes/{quote_id}/confirm-math", response_model=Run)
def confirm_math(run_id: str, quote_id: str, body: ConfirmMathBody, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    return orch.confirm_quote_math(run_id, quote_id, body.use_computed)


@router.post("/{run_id}/negotiate", response_model=Run)
def negotiate(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    """RECOMMENDED → agent drafts for the top eligible supplier → AWAITING_NEGOTIATION_APPROVAL (G5)."""
    return orch.start_negotiation(run_id)


@router.post("/{run_id}/negotiation/{supplier_id}/approve", response_model=Run)
def approve_negotiation(run_id: str, supplier_id: str, body: ApproveNegotiationBody | None = None,
                        orch: Orchestrator = Depends(get_orchestrator)) -> Run | JSONResponse:
    """Human gate: send the pending draft (or an edited message, re-filtered) to the simulated supplier.
    An edit that fails the outbound policy filter → 422 {code: policy_violation, violations: [...]}."""
    message = body.message if body else None
    try:
        return orch.approve_negotiation(run_id, supplier_id, message)
    except WorkflowError as exc:
        if exc.code != "policy_violation":
            raise
        # The orchestrator joins the filter's violations with "; " (see approve_negotiation).
        violations = [v for v in exc.message.split("; ") if v]
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            content={"code": exc.code, "message": exc.message, "violations": violations})


@router.post("/{run_id}/interrupt", response_model=Run)
def interrupt(run_id: str, body: InterruptBody, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    """Requirement change → REPLANNING → re-validate / re-enrich / re-score the existing quotes → RECOMMENDED
    with run.replan_impact. 409 illegal_transition outside RECOMMENDED | EXTRACTED | AWAITING_NEGOTIATION_APPROVAL,
    409 no_change when nothing differs."""
    return orch.interrupt(run_id, quantity=body.quantity, budget=body.budget, required_by=body.required_by,
                          reason=body.reason)


@router.post("/{run_id}/request-po", response_model=Run)
def request_po(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    """RECOMMENDED → AWAITING_PO_APPROVAL with run.po_preview (409 no_recommendation | supplier_ineligible)."""
    return orch.request_po(run_id)


@router.post("/{run_id}/approve-po", response_model=Run)
def approve_po(run_id: str, body: ApprovePoBody, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    """Human gate G5: AWAITING_PO_APPROVAL → PO_GENERATED with run.purchase_order (409 totals_changed if the
    engine's figures moved since the preview). The only path that creates a purchase order."""
    return orch.approve_po(run_id, body.approved_by)


@router.post("/{run_id}/reject-po", response_model=Run)
def reject_po(run_id: str, body: RejectPoBody | None = None, orch: Orchestrator = Depends(get_orchestrator)) -> Run:
    """AWAITING_PO_APPROVAL → RECOMMENDED; the preview is dropped."""
    return orch.reject_po(run_id, body.reason if body else "")


def _purchase_order(orch: Orchestrator, run_id: str) -> PurchaseOrder:
    po = orch.store.get(run_id).purchase_order
    if po is None:
        raise WorkflowError("po_not_found", f"run {run_id!r} has no generated purchase order yet")
    return po


@router.get("/{run_id}/po", response_model=PurchaseOrder)
def get_po(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> PurchaseOrder:
    """The generated purchase order; 404 until approve_po has run."""
    return _purchase_order(orch, run_id)


@router.get("/{run_id}/po.pdf", response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def get_po_pdf(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> Response:
    """The generated purchase order as a one-page PDF; 404 until approve_po has run."""
    run = orch.store.get(run_id)
    po = _purchase_order(orch, run_id)
    profile = orch.agents["supplier_intel"].get_profile(po.supplier.supplier_id)
    pdf = render_po_pdf(po, run.request, profile)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{po.po_number}.pdf"'})


@router.get("/{run_id}/negotiations", response_model=dict[str, NegotiationThread])
def list_negotiations(run_id: str, orch: Orchestrator = Depends(get_orchestrator)) -> dict[str, NegotiationThread]:
    return orch.store.get(run_id).negotiations


@router.get("/{run_id}/events", response_model=list[WorkflowEvent])
def list_events(run_id: str, since: int = Query(-1, description="return events with seq > since"),
                orch: Orchestrator = Depends(get_orchestrator)) -> list[WorkflowEvent]:
    orch.store.get(run_id)
    return orch.store.events(run_id, after_seq=since)


@router.get("/{run_id}/events/stream")
async def stream_events(run_id: str, request: Request, since: int = Query(-1),
                        follow: bool = Query(True, description="false = close after replaying stored events"),
                        orch: Orchestrator = Depends(get_orchestrator),
                        shutdown: asyncio.Event = Depends(get_shutdown_event)) -> StreamingResponse:
    """SSE: replay events with seq > since, then (follow=true) live events with `: keepalive` every 15 s.
    Returns as soon as the client disconnects or the app shuts down (an open stream must not block uvicorn)."""
    orch.store.get(run_id)
    loop = asyncio.get_running_loop()

    async def generate():
        # Subscribe first, then replay, so nothing emitted in between is lost; dedupe by seq.
        queue, unsubscribe = orch.events.queue(run_id, loop)
        last = since
        try:
            for event in orch.store.events(run_id, after_seq=since):
                last = event.seq
                yield _sse(event)
            while follow and not shutdown.is_set() and not await request.is_disconnected():
                getter = asyncio.ensure_future(queue.get())
                stopper = asyncio.ensure_future(shutdown.wait())
                done, _ = await asyncio.wait({getter, stopper}, timeout=KEEPALIVE_SECONDS,
                                             return_when=asyncio.FIRST_COMPLETED)
                stopper.cancel()
                if getter not in done:
                    getter.cancel()
                    if not done:  # timeout, not shutdown
                        yield ": keepalive\n\n"
                    continue
                event = getter.result()
                if event.seq > last:
                    last = event.seq
                    yield _sse(event)
        finally:
            unsubscribe()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
