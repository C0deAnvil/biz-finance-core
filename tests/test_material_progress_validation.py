import pytest
from fastapi import HTTPException

from services.material_replenishment_service import _material_rows


def test_material_rows_accepts_normal_history_rows():
    rows = [{"material_id": 1, "shortage_quantity": "10.5"}]

    assert _material_rows(rows, document_no="YL001", field_name="原料明细") == rows


@pytest.mark.parametrize("value", [{"material_id": 1}, "broken", 1])
def test_material_rows_returns_readable_business_error_for_invalid_container(value):
    with pytest.raises(HTTPException) as exc_info:
        _material_rows(value, document_no="YL001", field_name="原料明细")

    assert exc_info.value.status_code == 409
    assert "YL001" in str(exc_info.value.detail)
    assert "格式异常" in str(exc_info.value.detail)


def test_material_rows_returns_readable_business_error_for_invalid_row():
    with pytest.raises(HTTPException) as exc_info:
        _material_rows([None], document_no="YL001", field_name="原料明细")

    assert exc_info.value.status_code == 409
    assert "第 1 行" in str(exc_info.value.detail)
