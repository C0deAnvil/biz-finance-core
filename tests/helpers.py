"""pytest 共享工具:唯一编码 + 流程前置数据/操作封装。

测试库 yecai_test 为会话级共享(seed 一次),每个测试使用唯一编码
(后缀 uuid)创建自己的单据,避免相互污染。
"""

import uuid


def uniq(prefix: str) -> str:
    """生成唯一编码,如 uniq('CP') -> 'CP7F3A91B2E'。"""
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


# ---------- 主数据 ----------

def create_supplier(client, login) -> int:
    login("purchase01")
    resp = client.post(
        "/api/master-data/suppliers",
        json={
            "name": uniq("供应商"),
            "contact_name": "测试联系人",
            "phone": "13800000000",
            "address": "测试地址",
        },
    )
    assert resp.status_code == 200, f"创建供应商失败: {resp.text}"
    return resp.json()["data"]["id"]


def create_trade_product(client, login, threshold_qty: float = 0) -> int:
    login("inventory01")
    resp = client.post(
        "/api/master-data/products",
        json={
            "product_code": uniq("CP-TRADE"),
            "name": uniq("贸易产品"),
            "price": 100,
            "fulfillment_mode": "trade",
            "purchase_threshold_qty": threshold_qty,
            "specification": "测试规格",
        },
    )
    assert resp.status_code == 200, f"创建贸易产品失败: {resp.text}"
    return resp.json()["data"]["id"]


def create_production_product(client, login, threshold_qty: float = 0) -> int:
    login("inventory01")
    resp = client.post(
        "/api/master-data/products",
        json={
            "product_code": uniq("CP-PROD"),
            "name": uniq("生产产品"),
            "price": 200,
            "fulfillment_mode": "production",
            "purchase_threshold_qty": threshold_qty,
            "specification": "生产测试规格",
        },
    )
    assert resp.status_code == 200, f"创建生产产品失败: {resp.text}"
    return resp.json()["data"]["id"]


def create_material(client, login) -> int:
    login("inventory01")
    resp = client.post(
        "/api/master-data/materials",
        json={"material_code": uniq("YL"), "name": uniq("测试原料"), "price": 20},
    )
    assert resp.status_code == 200, f"创建原料失败: {resp.text}"
    return resp.json()["data"]["id"]


def create_global_packaging(client, login) -> int:
    login("inventory01")
    resp = client.post(
        "/api/master-data/packaging-configs",
        json={
            "entity_type": "global",
            "entity_id": 0,
            "packaging_name": uniq("通用包装"),
            "kg_per_bag": 25,
            "unit_price": 5,
            "is_default": True,
            "is_active": True,
        },
    )
    assert resp.status_code == 200, f"创建通用包装失败: {resp.text}"
    return resp.json()["data"]["id"]


def create_bom(client, login, *, product_id: int, material_id: int, standard_quantity: float) -> None:
    packaging_id = create_global_packaging(client, login)
    # 包装配置 helper 会重登 inventory01,提交 BOM 前切回 production01
    login("production01")
    resp = client.post(
        "/api/production/boms",
        json={
            "product_id": product_id,
            "packaging_config_id": packaging_id,
            "version_no": "v1",
            "items": [{"material_id": material_id, "standard_quantity": standard_quantity}],
        },
    )
    assert resp.status_code == 200, f"创建 BOM 失败: {resp.text}"


def set_supplier_catalog(client, login, *, supplier_id: int, product_id: int | None = None, material_id: int | None = None) -> None:
    login("purchase01")
    items = []
    if product_id:
        items.append({"product_id": product_id})
    if material_id:
        items.append({"material_id": material_id})
    resp = client.put(f"/api/master-data/suppliers/{supplier_id}/catalog", json=items)
    assert resp.status_code == 200, f"维护供应商名录失败: {resp.text}"


def warehouse_map(client, login) -> dict[str, int]:
    login("inventory01")
    resp = client.get("/api/master-data/warehouses")
    assert resp.status_code == 200
    rows = resp.json().get("data") or []
    result = {}
    for row in rows:
        code = str(row.get("code") or "")
        if "FINISHED" in code.upper() or "成品" in str(row.get("name") or ""):
            result["finished"] = row["id"]
        elif "RAW" in code.upper() or "原料" in str(row.get("name") or ""):
            result["raw"] = row["id"]
    assert len(result) == 2, f"未找到成品/原料仓库: {rows}"
    return result


def raw_material_inbound(client, login, *, material_id: int, quantity: float, warehouse_id: int) -> None:
    # 手工出入库受仓库范围限制,用全范围超级库管
    login("inventory_super01")
    resp = client.post(
        "/api/inventory/manual-operations",
        json={
            "warehouse_id": warehouse_id,
            "operation_type": "inbound",
            "remark": "pytest 原料入库",
            "items": [{"material_id": material_id, "batch_no": uniq("PC"), "quantity": quantity, "unit_cost": 20}],
        },
    )
    assert resp.status_code == 200, f"原料手工入库失败: {resp.text}"


def stock_of(client, login, *, warehouse_id: int, product_id: int | None = None, material_id: int | None = None) -> float:
    login("inventory_super01")
    params = [("warehouse_id", warehouse_id)]
    if product_id:
        params.append(("product_id", product_id))
    if material_id:
        params.append(("material_id", material_id))
    resp = client.get("/api/inventory/stocks", params=params)
    assert resp.status_code == 200, f"查询库存失败: {resp.text}"
    rows = resp.json().get("data") or []
    return float(sum(float((row.get("quantity") or 0)) for row in rows))


# ---------- 采购 ----------

def supplier_account_for_order(client, login, order_id: int) -> int:
    login("purchase01")
    order = client.get(f"/api/purchases/orders/{order_id}").json()["data"]
    supplier_id = order["supplier_id"]
    response = client.get(f"/api/finance/accounts?owner_type=supplier&supplier_id={supplier_id}")
    assert response.status_code == 200, response.text
    if response.json()["data"]:
        return response.json()["data"][0]["id"]
    response = client.post("/api/finance/accounts", json={
        "owner_type": "supplier", "supplier_id": supplier_id, "account_name": "测试供应商",
        "bank_name": "测试银行", "account_number": uniq("BANK"),
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def create_company_account(client, login) -> int:
    login("finance01")
    response = client.post("/api/finance/accounts", json={
        "owner_type": "company", "account_name": "测试企业", "bank_name": "测试银行", "account_number": uniq("BANK"),
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]

def create_purchase_order(client, login, *, supplier_id: int, product_id: int, quantity: float, unit_price: float) -> dict:
    login("purchase01")
    resp = client.post(
        "/api/purchases/orders",
        json={
            "supplier_id": supplier_id,
            "payment_type": "银行转账",
            "items": [{"product_id": product_id, "quantity": quantity, "unit_price": unit_price}],
        },
    )
    assert resp.status_code == 200, f"创建采购订单失败: {resp.text}"
    order_id = resp.json().get("data", {}).get("id")
    # 创建接口只返回 id/order_no,状态与明细从详情取
    return get_purchase_order(client, login, order_id)


def get_purchase_order(client, login, order_id: int) -> dict:
    login("purchase01")
    resp = client.get(f"/api/purchases/orders/{order_id}")
    assert resp.status_code == 200, f"查询采购订单失败: {resp.text}"
    return resp.json().get("data") or {}


def approve_purchase_order(client, login, *, order_id: int, decision: str = "approved") -> dict:
    login("manager01")
    resp = client.post(f"/api/purchases/orders/{order_id}/approve", json={"decision": decision, "note": "pytest 审批"})
    assert resp.status_code == 200, f"采购订单审批失败: {resp.text}"
    return resp.json()


def confirm_purchase_order(client, login, *, order_id: int) -> dict:
    login("purchase01")
    resp = client.post(f"/api/purchases/orders/{order_id}/confirm")
    assert resp.status_code == 200, f"采购订单确认失败: {resp.text}"
    return resp.json()


def create_purchase_receipt(
    client, login, *, purchase_order_id: int, order_item_id: int, warehouse_id: int, received_quantity: float, requires_quality: bool
) -> dict:
    login("purchase01")
    resp = client.post(
        "/api/purchases/receipts",
        json={
            "purchase_order_id": purchase_order_id,
            "warehouse_id": warehouse_id,
            "requires_quality": requires_quality,
            "remark": "pytest 到货",
            "items": [
                {
                    "purchase_order_item_id": order_item_id,
                    "received_quantity": received_quantity,
                    "batch_no": uniq("PC"),
                    "unit_cost": 10,
                }
            ],
        },
    )
    assert resp.status_code == 200, f"登记到货失败: {resp.text}"
    receipt_id = resp.json().get("data", {}).get("id")
    # 到货单返回字段以详情为准
    login("purchase01")
    detail_resp = client.get(f"/api/purchases/receipts/{receipt_id}")
    assert detail_resp.status_code == 200, f"查询到货单失败: {detail_resp.text}"
    return detail_resp.json().get("data") or {}


def find_quality_task_by_batch(client, login, *, batch_no: str, source_type: str | None = None) -> dict:
    login("quality01")
    resp = client.get("/api/quality/tasks")
    assert resp.status_code == 200, f"质检任务列表失败: {resp.text}"
    rows = resp.json().get("data") or []
    for row in rows:
        if str(row.get("batch_no") or "") == batch_no and (source_type is None or row.get("source_type") == source_type):
            return row
    raise AssertionError(f"未找到批次 {batch_no} 的质检任务,现有关联批次: {[r.get('batch_no') for r in rows[:10]]}")


def submit_inspection(client, login, *, quality_task_id: int, sequence_no: int, grade: str, quantity: float, passed: bool = True) -> dict:
    login("quality01")
    resp = client.post(
        "/api/quality/inspections",
        json={
            "quality_task_id": quality_task_id,
            "sequence_no": sequence_no,
            "result_text": "pytest 质检记录",
            "grade": grade,
            "is_passed": passed,
            "inspected_quantity": quantity,
            "sample_consumption_qty": 0,
            "report_summary": "pytest 报告",
        },
    )
    assert resp.status_code == 200, f"录入质检结果失败: {resp.text}"
    return resp.json().get("data") or {}


def allow_quality_inbound(client, login, *, quality_task_id: int) -> dict:
    login("quality01")
    resp = client.post(f"/api/quality/tasks/{quality_task_id}/allow-inbound", json={"remark": "pytest 准许入库"})
    assert resp.status_code == 200, f"准许入库失败: {resp.text}"
    return resp.json().get("data") or {}


def find_operation_request(client, login, *, category: str, source_id: int, statuses: set[str] | None = None) -> dict:
    login("inventory_super01")
    resp = client.get("/api/inventory/operation-requests")
    assert resp.status_code == 200, f"库存作业列表失败: {resp.text}"
    rows = resp.json().get("data") or []
    for row in rows:
        if row.get("request_category") == category and int(row.get("source_id") or 0) == source_id:
            if statuses is None or row.get("status") in statuses:
                return row
    raise AssertionError(f"未找到作业 category={category} source_id={source_id} statuses={statuses}")


def approve_operation_request(client, login, *, request_id: int) -> None:
    login("manager01")
    resp = client.post(f"/api/inventory/operation-requests/{request_id}/approve", json={"decision": "approved", "note": "pytest 经理审批"})
    assert resp.status_code == 200, f"库存作业经理审批失败: {resp.text}"


def complete_operation_request(client, login, *, request_id: int, items: list[dict]) -> None:
    # 作业受仓库范围限制,用全范围超级库管
    login("inventory_super01")
    resp = client.post(
        f"/api/inventory/operation-requests/{request_id}/complete",
        json={"items": items, "handled_remark": "pytest 执行完成", "adjust_reason": None, "quantity_difference_remark": None},
    )
    assert resp.status_code == 200, f"执行库存作业失败: {resp.text}"


def request_operation_items(operation_request: dict) -> list[dict]:
    """把作业请求的原始明细转成 complete 接口的 items 载荷。"""
    return [
        {
            "product_id": item.get("product_id"),
            "material_id": item.get("material_id"),
            "batch_no": item.get("batch_no"),
            "quantity": float(item.get("quantity") or 0),
            "unit_cost": float(item.get("unit_cost") or 0),
        }
        for item in (operation_request.get("original_items") or [])
    ]


# 1x1 透明 PNG(满足文件规则:png 签名)
MINI_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049"
    "454e44ae426082"
)


def upload_attachment(client, login, *, business_type: str, business_id: int, attachment_type: str = "bank_receipt") -> int:
    """上传附件(测试用 1x1 PNG),返回附件 id。"""
    login("finance01")
    resp = client.post(
        "/api/files/upload",
        data={
            "business_type": business_type,
            "business_id": business_id,
            "attachment_type": attachment_type,
        },
        files={"file": ("bank_receipt.png", MINI_PNG, "image/png")},
    )
    assert resp.status_code == 200, f"上传附件失败: {resp.text}"
    return resp.json()["data"]["id"]
