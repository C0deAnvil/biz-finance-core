"""通用 Excel 导出接口:任意二维列表数据 → xlsx 文件流。

用法 POST /api/export/excel,payload:
{
  "sheet_name": "销售订单",
  "columns": [{"key": "order_no", "title": "单号"}, ...],
  "rows": [{"order_no": "XS...", ...}, ...]
}
返回 xlsx 二进制流(Content-Disposition 带 filename)。
"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from io import BytesIO
from pydantic import BaseModel, Field

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from database import get_db
from dependencies import get_current_user
from models import User
from sqlalchemy.orm import Session

router = APIRouter(prefix="/export", tags=["导出"])


class ExportColumn(BaseModel):
    key: str
    title: str


class ExportExcelRequest(BaseModel):
    sheet_name: str = Field(default="导出数据", max_length=40)
    filename: str = Field(default="export", max_length=80)
    columns: list[ExportColumn] = Field(min_length=1)
    rows: list[dict] = Field(default_factory=list)


def _cell_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


@router.post("/excel")
def export_excel(
    payload: ExportExcelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wb = Workbook()
    ws = wb.active
    ws.title = payload.sheet_name[:31] or "导出数据"

    header_fill = PatternFill(start_color="0F5C90", end_color="0F5C90", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    header_align = Alignment(horizontal="center", vertical="center")

    # 表头
    for col_idx, column in enumerate(payload.columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=column.title)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align

    # 数据行
    for row_idx, row_data in enumerate(payload.rows, start=2):
        for col_idx, column in enumerate(payload.columns, start=1):
            ws.cell(row=row_idx, column=col_idx, value=_cell_value(row_data.get(column.key)))

    # 列宽自适应(中文按双宽估算)
    for col_idx, column in enumerate(payload.columns, start=1):
        max_len = len(column.title)
        for row_data in payload.rows[:200]:
            value = row_data.get(column.key)
            if value is None:
                continue
            text = str(_cell_value(value))
            max_len = max(max_len, len(text))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len * 2 + 4, 60)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"{payload.filename or 'export'}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    # RFC 5987 编码,中文文件名可用
    from urllib.parse import quote
    disposition = f"attachment; filename={quote(filename)}"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )