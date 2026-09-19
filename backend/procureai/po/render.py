"""One-page purchase order PDF (PLAN.md §1 step 9, T15).

Pure presentation: every figure comes from the PurchaseOrder the human approved; nothing is
recomputed here. Only a numbered, approved PO can be rendered.
"""

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from procureai.domain.models import ProcurementRequest, PurchaseOrder, SupplierProfile

BUYER = ("ProcureAI Demo Buyer", "Procurement War Room", "procurement@procureai.demo")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d1d5db")
ACCENT = colors.HexColor("#047857")


def _money(value: Decimal, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def _date(value: date | datetime) -> str:
    return value.strftime("%Y-%m-%d")


def render_po_pdf(po: PurchaseOrder, request: ProcurementRequest, supplier_profile: SupplierProfile | None) -> bytes:
    """Render an approved PurchaseOrder to PDF bytes (A4, one page)."""
    if po.po_number is None or po.approved_by is None or po.approved_at is None:
        raise ValueError("only an approved purchase order can be rendered")

    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=11, textColor=MUTED)
    label = ParagraphStyle("label", parent=small, fontName="Helvetica-Bold", textColor=MUTED, spaceAfter=2)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=INK)
    right = ParagraphStyle("right", parent=body, alignment=TA_RIGHT)
    right_bold = ParagraphStyle("right_bold", parent=right, fontName="Helvetica-Bold")
    footer = ParagraphStyle("footer", parent=small, alignment=TA_RIGHT)

    cur = po.currency
    line = po.line_items[0]

    def block(heading: str, lines: list[str]) -> list[Paragraph]:
        return [Paragraph(heading, label), *[Paragraph(text, body) for text in lines]]

    header = Table(
        [[
            [Paragraph("Purchase Order", title), Paragraph(f"Run {po.run_id} · request v{po.request_version}", small)],
            [Paragraph(f"<b>{po.po_number}</b>", right), Paragraph(f"Date {_date(po.approved_at)}", right),
             Paragraph("Status: approved", right)],
        ]],
        colWidths=[110 * mm, 60 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))

    supplier_lines = [f"<b>{po.supplier.name}</b>", f"Supplier id {po.supplier.supplier_id}"]
    if supplier_profile is not None:
        supplier_lines.append(f"{supplier_profile.orders_completed} completed orders on record · "
                              f"on-time {supplier_profile.on_time_rate:.0%}")
    parties = Table(
        [[block("BUYER", [f"<b>{BUYER[0]}</b>", BUYER[1], BUYER[2]]), block("SUPPLIER", supplier_lines)]],
        colWidths=[85 * mm, 85 * mm],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    items = Table(
        [
            ["Description", "Quantity", f"Unit price ({cur})", f"Line total ({cur})"],
            [Paragraph(line.description, body), f"{line.quantity:,}", f"{line.unit_price:,.2f}", f"{line.line_total:,.2f}"],
        ],
        colWidths=[80 * mm, 25 * mm, 32 * mm, 33 * mm],
    )
    items.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE), ("LINEBELOW", (0, -1), (-1, -1), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    t = po.totals
    totals_rows = [
        ["Subtotal", _money(t.subtotal, cur)],
        ["Discount", f"- {_money(t.discount, cur)}" if t.discount else _money(t.discount, cur)],
        ["Shipping", _money(t.shipping, cur)],
        ["Tax", _money(t.tax, cur)],
        ["Total (landed cost)", _money(t.total, cur)],
    ]
    totals = Table(totals_rows, colWidths=[45 * mm, 40 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5), ("TEXTCOLOR", (0, 0), (0, -2), MUTED),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    terms = [
        f"Payment terms: {po.payment_terms or 'as per supplier quote'}",
        f"Lead time: {po.lead_time_days} days" + (" (negotiated)" if po.negotiated else " (as quoted)"),
        f"Required by: {_date(request.required_by)}",
        f"Unit price {line.unit_price:,.2f} {cur}" + (" agreed in negotiation" if po.negotiated else " as quoted")
        + f"; totals computed by the ProcureAI engine at request v{po.request_version}.",
    ]

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=f"Purchase Order {po.po_number}", author=BUYER[0],
                            subject=f"{line.quantity} × {line.description} from {po.supplier.name}")
    story = [
        header, Spacer(1, 8 * mm),
        parties, Spacer(1, 8 * mm),
        Paragraph("LINE ITEMS", label), items, Spacer(1, 4 * mm),
        totals, Spacer(1, 8 * mm),
        Paragraph("TERMS", label), *[Paragraph(x, body) for x in terms], Spacer(1, 12 * mm),
        Paragraph(f"Generated after human approval by {po.approved_by} at {po.approved_at.strftime('%Y-%m-%d %H:%M:%S %Z').strip()}",
                  footer),
        Paragraph("Human gate G5: no agent can generate a purchase order; this document exists only because a person approved it.",
                  footer),
    ]
    doc.build(story)
    return buf.getvalue()
