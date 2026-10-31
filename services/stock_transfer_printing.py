"""Printable stock-transfer handover documents."""

from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import StockTransfer


FONT = "NotoSansSC"
FONT_BOLD = "NotoSansSC-Bold"
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
if FONT not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont(FONT, str(_FONT_DIR / "NotoSansSC.ttf")))
if FONT_BOLD not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_FONT_DIR / "NotoSansSC-Bold.ttf")))


def _p(value, size=10, align=0, bold=False):
    text = escape(str(value).strip()) if value is not None and str(value).strip() else "-"
    return Paragraph(text, ParagraphStyle(
        name="transfer", fontName=FONT_BOLD if bold else FONT, fontSize=size,
        leading=size + 4, textColor=colors.black, alignment=align,
    ))


def render_stock_transfer_pdf(transfer: StockTransfer, document_type: str) -> bytes:
    """Render an A4 outbound handover or inbound receipt with signature positions."""
    outbound = document_type == "outbound"
    title = "库存移库出库交接单" if outbound else "库存移库入库验收单"
    item = transfer.product or transfer.material or transfer.packaging_config
    item_name = getattr(item, "name", None) or getattr(item, "packaging_name", None) or "-"
    item_code = (
        getattr(item, "product_code", None)
        or getattr(item, "material_code", None)
        or (f"BZ-{transfer.packaging_config_id}" if transfer.packaging_config_id else "-")
    )
    item_type = "产品" if transfer.product else "原料" if transfer.material else "包装"
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [_p(title, 19, TA_CENTER, True), Spacer(1, 8 * mm)]
    meta = [
        [_p("移库单号", bold=True), _p(transfer.transfer_no), _p("制单日期", bold=True), _p(transfer.created_at.strftime("%Y-%m-%d") if transfer.created_at else "-")],
        [_p("源仓库", bold=True), _p(transfer.source_warehouse.name if transfer.source_warehouse else "-"), _p("目标仓库", bold=True), _p(transfer.target_warehouse.name if transfer.target_warehouse else "-")],
        [_p("运输人", bold=True), _p(transfer.carrier_name), _p("联系电话", bold=True), _p(transfer.carrier_phone)],
        [_p("车牌号", bold=True), _p(transfer.vehicle_plate_no), _p("是否质检", bold=True), _p("需要" if transfer.requires_quality else "不需要")],
    ]
    table = Table(meta, colWidths=[28 * mm, 61 * mm, 28 * mm, 61 * mm], rowHeights=11 * mm)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1.2, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [table, Spacer(1, 7 * mm)]
    details = [[_p("类型", bold=True), _p("编码", bold=True), _p("物品名称", bold=True), _p("数量", bold=True)], [_p(item_type), _p(item_code), _p(item_name), _p(transfer.quantity)]]
    item_table = Table(details, colWidths=[28 * mm, 45 * mm, 72 * mm, 33 * mm], rowHeights=[11 * mm, 16 * mm])
    item_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1.4, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, 0), "CENTER")]))
    story += [item_table, Spacer(1, 7 * mm), _p(f"备注：{transfer.remark or '-'}", 10), Spacer(1, 28 * mm)]
    labels = ["源仓库经办人签字", "承运人签字", "复核人签字"] if outbound else ["目标仓库经办人签字", "承运人签字", "质检员签字（如需）"]
    signatures = [[_p(label, 10, TA_CENTER, True) for label in labels], [_p("\n\n日期：      年   月   日") for _ in labels]]
    sign_table = Table(signatures, colWidths=[59 * mm] * 3, rowHeights=[12 * mm, 35 * mm])
    sign_table.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.2, colors.black), ("INNERGRID", (0, 0), (-1, -1), 1.0, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(sign_table)
    doc.build(story)
    return buffer.getvalue()
