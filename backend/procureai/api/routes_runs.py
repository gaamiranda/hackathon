"""REST + SSE over the Orchestrator (PLAN.md §6). No business logic here."""

import asyncio
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from procureai.api.deps import get_orchestrator, get_shutdown_event
from procureai.api.extract_text import UnsupportedDocument, to_text
from procureai.config.settings import get_settings
from procureai.domain.models import (
    Currency,
    Money,
    NegotiationThread,
    ProcurementConfig,
    ProcurementRequest,
    RawDocument,
    Run,
    WorkflowEvent,
)
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


class EmailDocumentBody(BaseModel):
    email_text: str
    filename: str = "email.txt"


class CorrectQuoteBody(BaseModel):
    patch: dict[str, Any]


class ConfirmMathBody(BaseModel):
    use_computed: bool


class ApproveNegotiationBody(BaseModel):
    message: str | None = Field(default=None, description="Edited draft; omit to send the agent's draft unchanged")


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
