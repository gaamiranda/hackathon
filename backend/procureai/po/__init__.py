"""Purchase order document rendering. The PurchaseOrder itself is built only by the orchestrator (G5)."""

from procureai.po.render import render_po_pdf

__all__ = ["render_po_pdf"]
