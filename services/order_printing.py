"""PDF templates for customer sales orders and warehouse placement sheets."""

from decimal import Decimal
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import SalesOrder, order_type_style
from services.display import payment_type_label


FONT = "NotoSansSC"
pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansSC.ttf")))
PAGE_SIZE = landscape(A5)
INK = colors.HexColor("#263445")
GRID = colors.HexColor("#aeb9c7")


def _text(value, fallback="-"):
    return escape(str(value).strip()) if value is not None and str(value).strip() else fallback


def _paragraph(value, *, size=7.5, align=0):
    return Paragraph(
        _text(value),
        ParagraphStyle(
            name="cell",
            fontName=FONT,
            fontSize=size,
            leading=size + 4,
            textColor=INK,
            alignment=align,
        ),
    )


def _money(value):
    return f"{Decimal(value or 0):,.2f}"


def _qty(value):
    return f"{Decimal(value or 0):,.4f}".rstrip("0").rstrip(".") or "0"


def _date(value):
    return value.strftime("%Y-%m-%d") if value else "-"


def _field(label, value):
    return _paragraph(f"{label}：{value if value is not None else '-'}")


def _base_doc(buffer):
    return SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm,
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
    content = [[_paragraph(value, size=7, align=TA_CENTER) for value in headers]]
    content.extend([_paragraph(value, size=7) for value in row] for row in rows)
    table = Table(content, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(GRID)
    canvas.line(10 * mm, 7 * mm, PAGE_SIZE[0] - 10 * mm, 7 * mm)
    canvas.setFont(FONT, 7)
    canvas.drawRightString(PAGE_SIZE[0] - 10 * mm, 3 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def render_order_pdf(order: SalesOrder, *, kind: str) -> bytes:
    if kind not in {"sales", "placement"}:
        raise ValueError("unsupported print template")
    buffer = BytesIO()
    doc = _base_doc(buffer)
    width = PAGE_SIZE[0] - 20 * mm
    title = "销售单" if kind == "sales" else "置货单"
    styles = ParagraphStyle(name="title", fontName=FONT, fontSize=16, leading=21, alignment=TA_CENTER, textColor=INK)
    story = [Paragraph(title, styles), Spacer(1, 3 * mm)]
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
        story.append(_paragraph(f"订单总金额：¥{_money(order.total_amount)}", size=10, align=TA_RIGHT))
        story.append(Spacer(1, 2 * mm))
        account = order.receipt_account
        account_text = f"{account.account_name} / {account.bank_name} / {account.account_number}" if account else "-"
        story.append(_paragraph(f"收款账户：{account_text}"))
        story.append(_paragraph(f"收货人：{order.consignee or '-'}    电话：{order.consignee_phone or '-'}"))
    else:
        story.append(_paragraph("作业提示：请按订单明细核对包装与数量；异常或缺货请在系统中登记。", size=9))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
