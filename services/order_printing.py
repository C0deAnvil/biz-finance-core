"""PDF templates for customer sales orders and warehouse placement sheets."""

import os
from decimal import Decimal
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.graphics.barcode.code128 import Code128
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import SalesOrder, order_type_style
from services.display import payment_type_label


FONT = "NotoSansSC"
FONT_BOLD = "NotoSansSC-Bold"
pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansSC.ttf")))
pdfmetrics.registerFont(TTFont(FONT_BOLD, str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansSC-Bold.ttf")))
pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)
PAGE_SIZE = landscape(A5)
INK = colors.black
GRID = colors.black
SCAN_CODE_FORMAT = os.getenv("YECAI_SCAN_CODE_FORMAT", "qr").strip().lower()


def _scan_code(value: str):
    """Render the warehouse action code, with Code128 kept as a rollback option."""
    if SCAN_CODE_FORMAT == "code128":
        return Code128(
            value,
            barWidth=0.18 * mm,
            barHeight=12 * mm,
            humanReadable=False,
        )

    # 24 mm is large enough for short action tokens while keeping common
    # two-line placement orders on one A5 landscape sheet.
    size = 24 * mm
    widget = QrCodeWidget(value, barLevel="H")
    left, bottom, right, top = widget.getBounds()
    width = right - left
    height = top - bottom
    drawing = Drawing(
        size,
        size,
        transform=[size / width, 0, 0, size / height, 0, 0],
    )
    drawing.add(widget)
    return drawing


def _text(value, fallback="-"):
    return escape(str(value).strip()) if value is not None and str(value).strip() else fallback


def _paragraph(value, *, size=9, align=0):
    return Paragraph(
        _text(value),
        ParagraphStyle(
            name="cell",
            fontName=FONT_BOLD,
            fontSize=size,
            leading=size + 3,
            textColor=INK,
            alignment=align,
        ),
    )


def _rich_paragraph(value, *, size=9, align=0):
    """Render trusted template markup; dynamic values must be escaped by caller."""
    return Paragraph(
        str(value),
        ParagraphStyle(
            name="rich-cell",
            fontName=FONT_BOLD,
            fontSize=size,
            leading=size + 3,
            textColor=INK,
            alignment=align,
        ),
    )


def _placement_paragraph(value, *, size=11, align=0, color=colors.black):
    """High-contrast text for warehouse printouts and low-quality printers."""
    return Paragraph(
        _text(value),
        ParagraphStyle(
            name="placement-cell",
            fontName=FONT_BOLD,
            fontSize=size,
            leading=size + 3,
            textColor=color,
            alignment=align,
        ),
    )


def _placement_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.black)
    canvas.setLineWidth(0.8)
    canvas.line(7 * mm, 7 * mm, PAGE_SIZE[0] - 7 * mm, 7 * mm)
    canvas.setFillColor(colors.black)
    canvas.setFont(FONT, 8)
    canvas.drawRightString(PAGE_SIZE[0] - 7 * mm, 3 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _money(value):
    value_text = f"{Decimal(value or 0):,.4f}"
    return value_text[:-1] if value_text.endswith("0") else value_text


def _qty(value):
    return f"{Decimal(value or 0):,.4f}".rstrip("0").rstrip(".") or "0"


def _date(value):
    return value.strftime("%Y-%m-%d") if value else "-"


def _field(label, value):
    return _paragraph(f"{label}：{value if value is not None else '-'}")


def _base_doc(buffer):
    return SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=7 * mm, rightMargin=7 * mm,
        topMargin=7 * mm, bottomMargin=8 * mm,
        title="业务单据", author="业务系统",
    )


def _meta_table(rows, width):
    table = Table(rows, colWidths=[width * 0.42, width * 0.29, width * 0.29])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _grid_table(headers, rows, widths):
    content = [[_paragraph(value, size=8.5, align=TA_CENTER) for value in headers]]
    content.extend([_paragraph(value, size=8.5) for value in row] for row in rows)
    table = Table(content, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1.2, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.black)
    canvas.setLineWidth(0.8)
    canvas.line(7 * mm, 7 * mm, PAGE_SIZE[0] - 7 * mm, 7 * mm)
    canvas.setFillColor(colors.black)
    canvas.setFont(FONT_BOLD, 8)
    canvas.drawRightString(PAGE_SIZE[0] - 7 * mm, 3 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _placement_pdf(order: SalesOrder, *, placement_token: str, shipment_token: str, warehouse_name: str | None) -> bytes:
    """Large-font warehouse sheet with separate full-placement/full-shipment codes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=7 * mm, rightMargin=7 * mm,
        topMargin=6 * mm, bottomMargin=8 * mm,
        title=f"销售出库作业单 {order.order_no}", author="业务系统",
    )
    width = PAGE_SIZE[0] - 14 * mm
    story = []

    placement_barcode = _scan_code(f"YCP01{placement_token}")
    shipment_barcode = _scan_code(f"YCS01{shipment_token}")
    barcode_table = Table(
        [
            [_placement_paragraph("销售出库作业单", size=22, align=TA_CENTER), ""],
            [placement_barcode, shipment_barcode],
            [_placement_paragraph("置货码", size=13, align=TA_CENTER),
             _placement_paragraph("发货码", size=13, align=TA_CENTER)],
        ],
        colWidths=[width / 2, width / 2],
    )
    barcode_table.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("BOX", (0, 0), (-1, -1), 1.2, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 1.0, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (1, 0), 1),
        ("BOTTOMPADDING", (0, 0), (1, 0), 4),
    ]))
    story.extend([barcode_table, Spacer(1, 1.2 * mm)])

    style = order_type_style(order.order_type)
    expected = order.expected_delivery_date
    order_type_color = colors.HexColor("#D40000") if order.order_type == "urgent" else colors.black
    owner = (order.sales_user.full_name or order.sales_user.username) if order.sales_user else "-"
    meta = Table(
        [
            [_placement_paragraph("订单编号"), _placement_paragraph(order.order_no, size=12),
             _placement_paragraph("作业仓库"), _placement_paragraph(warehouse_name, size=12),
             _placement_paragraph(style["label"], size=13, align=TA_CENTER, color=order_type_color)],
            [_placement_paragraph("期望发货"), _placement_paragraph(_date(expected), size=12),
             _placement_paragraph("销售负责人"), _placement_paragraph(owner), ""],
            [_placement_paragraph("收货人/电话"), _placement_paragraph(f"{order.consignee or '-'}  {order.consignee_phone or '-'}"),
             _placement_paragraph("送货地址"), _placement_paragraph(order.delivery_address or "-", size=10.5), ""],
            [_placement_paragraph("销售备注"), _placement_paragraph(order.order_remark or "-"), "", "", ""],
        ],
        colWidths=[width * 0.10, width * 0.22, width * 0.10, width * 0.43, width * 0.15],
    )
    meta.setStyle(TableStyle([
        ("SPAN", (3, 2), (4, 2)),
        ("SPAN", (1, 3), (4, 3)),
        ("GRID", (0, 0), (-1, -1), 1.4, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([meta, Spacer(1, 1.5 * mm)])

    headers = ["产品编码", "产品名称 / 产品规格", "包装要求", "数量", "明细备注"]
    rows = []
    for item in order.items:
        product = item.product
        packaging = item.packaging_config
        specification = item.display_specification or (product.specification if product else None) or "-"
        pack_parts = [packaging.packaging_name if packaging else "-"]
        if item.kg_per_bag:
            pack_parts.append(f"{_qty(item.kg_per_bag)} kg/袋")
        if item.bag_count:
            pack_parts.append(f"{_qty(item.bag_count)} 袋")
        rows.append([
            product.product_code if product else "-",
            f"{product.name if product else '-'} / {specification}",
            " / ".join(pack_parts),
            f"{_qty(item.quantity)} kg",
            item.remark or "-",
        ])
    content = [[_placement_paragraph(value, size=11, align=TA_CENTER) for value in headers]]
    content.extend([_placement_paragraph(value, size=10.5) for value in row] for row in rows)
    item_table = Table(
        content,
        colWidths=[width * 0.13, width * 0.25, width * 0.19, width * 0.12, width * 0.31],
        repeatRows=1, hAlign="LEFT",
    )
    item_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1.2, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(item_table)
    doc.build(story, onFirstPage=_placement_footer, onLaterPages=_placement_footer)
    return buffer.getvalue()


def render_order_pdf(
    order: SalesOrder,
    *,
    kind: str,
    placement_token: str | None = None,
    shipment_token: str | None = None,
    warehouse_name: str | None = None,
) -> bytes:
    if kind not in {"sales", "placement"}:
        raise ValueError("unsupported print template")
    if kind == "placement":
        if not placement_token or not shipment_token:
            raise ValueError("placement and shipment scan tokens are required")
        return _placement_pdf(
            order,
            placement_token=placement_token,
            shipment_token=shipment_token,
            warehouse_name=warehouse_name,
        )
    buffer = BytesIO()
    doc = _base_doc(buffer)
    width = PAGE_SIZE[0] - 14 * mm
    title = "销售单" if kind == "sales" else "置货单"
    styles = ParagraphStyle(name="title", fontName=FONT_BOLD, fontSize=22, leading=27, alignment=TA_CENTER, textColor=colors.black)
    story = [Paragraph(title, styles), Spacer(1, 3 * mm)]
    if kind == "placement":
        # 纸面只包含任务标识。设备认证码由扫描终端保存，不能印在单据中。
        scan_code = f"PL:{order.id}"
        barcode = Code128(scan_code, barWidth=0.45 * mm, barHeight=10 * mm, humanReadable=False)
        barcode.hAlign = "CENTER"
        story.extend([
            barcode,
            Spacer(1, 1 * mm),
            _paragraph(f"置货任务码：{scan_code}（订单 {order.order_no}）", size=7, align=TA_CENTER),
            Spacer(1, 2 * mm),
        ])
    owner = (order.sales_user.full_name or order.sales_user.username) if order.sales_user else "-"
    operator = (order.operator_user.full_name or order.operator_user.username) if order.operator_user else owner
    common = [
        [_field("单据编号", order.order_no), _field("订单类型", order_type_style(order.order_type)["label"]), _field("录单日期", _date(order.created_at))],
        [_field("销售负责人", owner), _field("经办人", operator), _field("期望发货日期", _date(order.expected_delivery_date))],
    ]
    if kind == "sales":
        common.insert(0, [_field("购买单位", order.customer.name if order.customer else None), _field("收款方式", payment_type_label(order.payment_type, deposit_amount=order.deposit_amount, module="sales")), _field("收款账户", order.receipt_account.account_name if order.receipt_account else None)])
    else:
        common.insert(0, [_field("置货任务", order.order_no), _field("收货人", order.consignee), _field("联系电话", order.consignee_phone)])
    story.extend([_meta_table(common, width), Spacer(1, 2 * mm)])
    story.append(_paragraph(f"送货地址：{order.delivery_address or '-'}"))
    story.append(Spacer(1, 2 * mm))
    story.append(_paragraph(f"附加说明：{order.order_remark or '-'}"))
    story.append(Spacer(1, 4 * mm))
    if kind == "sales":
        headers = ["商品编号", "商品全名 / 规格", "产品包装", "单位", "数量", "单价", "金额", "配送要求"]
        weights = [0.09, 0.18, 0.16, 0.05, 0.08, 0.08, 0.1, 0.26]
    else:
        headers = ["商品编号", "商品全名 / 规格", "产品包装", "单位", "订单数量", "已置数量", "待置数量", "作业备注"]
        weights = [0.09, 0.18, 0.16, 0.05, 0.09, 0.09, 0.09, 0.25]
    widths = [width * weight for weight in weights]
    rows = []
    for item in order.items:
        product = item.product
        packaging = item.packaging_config
        pack = packaging.packaging_name if packaging else "-"
        if item.kg_per_bag:
            pack = f"{pack} / {_qty(item.kg_per_bag)} kg/袋"
        if item.bag_count:
            pack += f" / {_qty(item.bag_count)} 袋"
        name = product.name if product else "-"
        if item.display_specification:
            name += f" / {item.display_specification}"
        base = [product.product_code if product else "-", name, pack, "kg", _qty(item.quantity)]
        if kind == "sales":
            rows.append(base + [_money(item.unit_price), _money(item.total_amount), item.remark or "-"])
        else:
            remaining = max(Decimal(item.quantity or 0) - Decimal(item.placed_quantity or 0), Decimal("0"))
            rows.append(base + [_qty(item.placed_quantity), _qty(remaining), item.remark or "-"])
    story.append(_grid_table(headers, rows, widths))
    story.append(Spacer(1, 5 * mm))
    if kind == "sales":
        story.append(_paragraph(f"订单总金额：¥{_money(order.total_amount)}", size=12, align=TA_RIGHT))
        story.append(Spacer(1, 2 * mm))
        account = order.receipt_account
        account_text = f"{account.account_name} / {account.bank_name} / {account.account_number}" if account else "-"
        story.append(_paragraph(f"收款账户：{account_text}"))
        story.append(_paragraph(f"收货人：{order.consignee or '-'}    电话：{order.consignee_phone or '-'}"))
    else:
        story.append(_paragraph("作业提示：请按订单明细核对包装与数量；异常或缺货请在系统中登记。", size=9))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
