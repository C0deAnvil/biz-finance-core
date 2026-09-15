"""区分人工确认的原料补货与旧版自动生成的原料缺口待办。"""

from decimal import Decimal


def confirmed_material_quantities(description: str, payload: dict) -> dict[int, Decimal]:
    if "confirmed_material_quantities" in payload:
        # 新记录显式记录逐项确认量；未确认的旧原料不能因其他原料提交而被确认。
        quantities = payload["confirmed_material_quantities"]
        return {int(key): Decimal(str(value)) for key, value in quantities.items()}
    # 兼容旧版人工提交入口的固定文案；客户订单自动缺口待办不算人工提交。
    if description.startswith("生产任务 "):
        return {
            int(item["material_id"]): Decimal(str(item.get("shortage_quantity") or 0))
            for item in payload.get("items", []) if item.get("material_id")
        }
    return {}
