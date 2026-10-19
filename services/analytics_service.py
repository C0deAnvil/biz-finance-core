from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from models import (
    CashFlowRecord,
    Customer,
    Department,
    Inventory,
    InventoryTransaction,
    LogisticsRecord,
    Material,
    OperationLog,
    PayableRecord,
    Product,
    ProductionMaterialUsage,
    ProductionPlan,
    ProductionTask,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReturn,
    QualityCostRecord,
    QualityDisposition,
    QualityInspection,
    QualityTask,
    ReceivableRecord,
    RefundRecord,
    Reimbursement,
    Role,
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    StockTakeItem,
    StockTakeTask,
    Supplier,
    SupplierCatalogItem,
    User,
    Warehouse,
)
from services.display import (
    payment_stage_label,
    payment_type_label,
    return_type_label,
    status_label,
    transaction_type_label,
)
from services.organization import (
    get_inventory_scope_warehouse_ids,
    get_scoped_user_ids,
    is_master_data_admin,
    is_system_admin,
)
from services.workflow import purchase_arrival_status


ALL_ACCESS_ROLE_CODES = {"manager", "chairman", "system_admin", "master_data_admin"}
MODULE_LABELS = {
    "sales": "销售分析",
    "purchase": "采购分析",
    "inventory": "库存分析",
    "production": "生产分析",
    "quality": "质检分析",
    "finance": "财务分析",
    "master": "主数据分析",
    "admin": "系统分析",
}

BUSINESS_MODULE_LABELS = {
    "sales": "销售",
    "purchase": "采购",
    "inventory": "库存",
    "production": "生产",
    "quality": "质检",
    "finance": "财务",
    "master": "主数据",
    "admin": "系统管理",
    "dashboard": "系统",
}

LEADERSHIP_METRIC_PRIORITY = {
    "sales_cost_profit_monthly": 1000,
    "inventory_turnover_rate_monthly": 990,
    "finance_cash_flow_monthly": 980,
    "finance_receivable_payable_monthly": 970,
    "logistics_delivery_fee_monthly": 965,
    "customer_new_monthly": 955,
    "reimbursement_category_monthly": 945,
    "inventory_amount_by_warehouse": 960,
    "finance_quality_cost_by_type": 950,
    "sales_amount_monthly": 940,
    "purchase_amount_monthly": 930,
    "refund_by_business_type": 920,
}


@dataclass(frozen=True)
class AnalyticsMetricDefinition:
    code: str
    name: str
    module: str
    description: str
    departments: tuple[str, ...]
    default_chart_type: str
    supported_chart_types: tuple[str, ...]
    unit: str = ""
    supports_date_range: bool = True
    supports_top_n: bool = False
    keywords: tuple[str, ...] = ()
    calculation_logic: str = ""

    @property
    def module_label(self) -> str:
        return MODULE_LABELS.get(self.module, self.module)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["module_label"] = self.module_label
        data["calculation_logic"] = self.calculation_logic or METRIC_CALCULATION_LOGICS.get(
            self.code,
            "计算逻辑：在当前筛选条件下，对相关业务记录按图表分类维度分组汇总。",
        )
        return data


METRIC_CALCULATION_LOGICS = {
    "sales_order_status_count": "订单数 = 按客户订单状态分组后的订单记录数量。",
    "sales_amount_monthly": "月销售额 = Σ 当月客户订单 total_amount。",
    "sales_top_customers_amount": "客户销售额 = Σ 该客户客户订单 total_amount，按金额倒序取 Top N。",
    "sales_top_products_quantity": "产品销量 = Σ 客户订单明细 quantity，按产品分组后倒序取 Top N。",
    "sales_payment_type_distribution": "订单数 = 按客户订单 payment_type 分组后的订单记录数量。",
    "sales_receipt_stage_amount": "阶段回款金额 = Σ 回款记录 amount，按收款阶段分组。",
    "sales_return_type_amount": "退换货金额 = Σ 销售退换货 refund_amount，按退货/换货类型分组。",
    "sales_cost_profit_monthly": "按发货出库时间确认收入与库存成本；待定价的发货只计收入，不伪造利润。包装出库计入成本；运包费用沿用订单现有口径。",
    "purchase_order_status_count": "采购单数 = 按采购订单状态分组后的采购订单记录数量。",
    "purchase_amount_monthly": "月采购额 = Σ 当月采购订单 total_amount。",
    "purchase_top_suppliers_amount": "供应商采购额 = Σ 该供应商采购订单 total_amount，按金额倒序取 Top N。",
    "purchase_arrival_status_distribution": "采购单数 = 按采购到货状态分组后的采购订单记录数量。",
    "purchase_payment_type_distribution": "采购单数 = 按采购订单 payment_type 分组后的采购订单记录数量。",
    "inventory_amount_by_warehouse": "仓库库存金额 = Σ 当前库存 quantity × moving_avg_cost，按仓库分组。",
    "inventory_item_type_amount": "库存类型金额 = Σ 当前库存 quantity × moving_avg_cost，按成品/原料分组。",
    "inventory_low_stock_by_warehouse": "低库存条目数 = 当前库存 quantity < 安全库存阈值的库存条目数量，按仓库分组。",
    "inventory_transaction_type_distribution": "流水次数 = 按库存流水 transaction_type 分组后的流水记录数量。",
    "stock_take_difference_by_warehouse": "盘点差异量 = Σ |实盘数量 - 账面数量|，按仓库分组。",
    "inventory_turnover_rate_monthly": "库存周转率 = 当月出库成本 ÷ 当月平均库存余额；平均库存余额 = (月初库存金额 + 月末库存金额) ÷ 2。",
    "logistics_delivery_fee_monthly": "月运费金额 = Σ 物流单送货费 delivery_fee，按开单月份分组。",
    "logistics_module_distribution": "物流记录数 = 按物流单所属业务模块分组后的记录数量。",
    "production_task_status_distribution": "生产任务数 = 按生产任务状态分组后的任务记录数量。",
    "production_output_top_products": "产品产出量 = Σ 生产任务完成数量，按产品分组后倒序取 Top N。",
    "production_material_usage_top_materials": "原料耗用量 = Σ 领料申请/出库数量，按原料分组后倒序取 Top N。",
    "production_plan_status_distribution": "生产计划数 = 按生产计划状态分组后的计划记录数量。",
    "quality_source_distribution": "质检任务数 = 按质检任务来源分组后的任务记录数量。",
    "quality_result_distribution": "质检任务数 = 按质检结果/处置状态分组后的任务记录数量。",
    "quality_grade_distribution": "质检等级数 = 按最近一次质检等级分组后的任务记录数量。",
    "quality_sample_consumption_monthly": "月耗样量 = Σ 当月质检样品消耗数量。",
    "finance_cash_flow_monthly": "月流入 = Σ incoming 资金流水；月流出 = Σ outgoing 资金流水；净流量 = 月流入 - 月流出。",
    "reimbursement_category_monthly": "月报销金额 = Σ 已出账报销 amount，按月份和费用大类分组。",
    "finance_cash_flow_by_business_type": "业务流水金额 = Σ 资金流水 amount，按业务类型分组。",
    "finance_receivable_payable_monthly": "月回款 = Σ 应收回款 amount；月付款 = Σ 应付付款 amount，按月份对比。",
    "finance_quality_cost_by_type": "质量成本 = Σ 质量成本记录 recognized_amount 或 estimated_amount，按处置类型分组。",
    "refund_by_business_type": "退款金额 = Σ 销售退款金额 + 采购退款金额，按退款业务类型分组。",
    "customer_new_monthly": "月新增客户量 = 按客户建档时间统计当月新增客户记录数量。",
    "product_fulfillment_mode_distribution": "产品数 = 按产品主数据履约模式分组后的产品记录数量。",
    "supplier_catalog_top": "供货名录覆盖数 = 供应商可供产品/原料名录记录数量，按供应商倒序取 Top N。",
    "user_role_distribution": "用户数 = 按角色分组后的启用用户记录数量。",
    "user_department_distribution": "用户数 = 按组织单元分组后的启用用户记录数量。",
    "operation_log_module_distribution": "操作日志数 = 按系统模块分组后的操作日志记录数量。",
}


def _metric_definitions() -> list[AnalyticsMetricDefinition]:
    return [
        AnalyticsMetricDefinition(
            code="sales_order_status_count",
            name="客户订单状态分布",
            module="sales",
            description="按客户订单状态统计单量，适合查看订单推进结构。",
            departments=("sales",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="单",
            keywords=("订单", "状态", "销售"),
        ),
        AnalyticsMetricDefinition(
            code="sales_amount_monthly",
            name="客户订单金额月度趋势",
            module="sales",
            description="按客户订单建单月份统计订单金额，用于观察销售趋势。",
            departments=("sales",),
            default_chart_type="line",
            supported_chart_types=("line", "area", "bar"),
            unit="元",
            keywords=("销售额", "月份", "趋势"),
        ),
        AnalyticsMetricDefinition(
            code="sales_top_customers_amount",
            name="客户销售额排名",
            module="sales",
            description="按客户汇总订单金额，默认显示金额最高的客户。",
            departments=("sales",),
            default_chart_type="hbar",
            supported_chart_types=("hbar", "bar", "pie", "donut"),
            unit="元",
            supports_top_n=True,
            keywords=("客户", "排行", "销售额"),
        ),
        AnalyticsMetricDefinition(
            code="sales_top_products_quantity",
            name="产品销售数量排名",
            module="sales",
            description="按产品汇总销售数量，适合观察主销产品。",
            departments=("sales",),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="数量",
            supports_top_n=True,
            keywords=("产品", "销量", "排行"),
        ),
        AnalyticsMetricDefinition(
            code="sales_payment_type_distribution",
            name="销售付款方式分布",
            module="sales",
            description="按客户订单付款方式统计订单量。",
            departments=("sales",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="单",
            keywords=("付款方式", "销售"),
        ),
        AnalyticsMetricDefinition(
            code="sales_receipt_stage_amount",
            name="销售回款阶段金额分布",
            module="sales",
            description="按定金、尾款、分期等阶段统计累计回款金额。",
            departments=("sales", "finance"),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            keywords=("回款", "阶段", "销售"),
        ),
        AnalyticsMetricDefinition(
            code="sales_return_type_amount",
            name="销售退换货金额分布",
            module="sales",
            description="按退货与换货类型统计销售退换货金额。",
            departments=("sales", "finance"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            keywords=("退货", "换货", "退款"),
        ),
        AnalyticsMetricDefinition(
            code="sales_cost_profit_monthly",
            name="销售收入/成本/利润月度对比",
            module="finance",
            description="按实际发货月份对比收入、库存成本与利润；待定价出库单独提示。",
            departments=("sales", "finance"),
            default_chart_type="bar",
            supported_chart_types=("bar", "line", "area"),
            unit="元",
            keywords=("利润", "成本", "收入", "经营分析"),
        ),
        AnalyticsMetricDefinition(
            code="purchase_order_status_count",
            name="采购订单状态分布",
            module="purchase",
            description="按采购订单状态统计单量，便于掌握采购推进情况。",
            departments=("purchase",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="单",
            keywords=("采购", "状态", "订单"),
        ),
        AnalyticsMetricDefinition(
            code="purchase_amount_monthly",
            name="采购金额月度趋势",
            module="purchase",
            description="按采购建单月份统计采购金额。",
            departments=("purchase",),
            default_chart_type="line",
            supported_chart_types=("line", "area", "bar"),
            unit="元",
            keywords=("采购额", "月份", "趋势"),
        ),
        AnalyticsMetricDefinition(
            code="purchase_top_suppliers_amount",
            name="供应商采购额排名",
            module="purchase",
            description="按供应商汇总采购金额，识别核心供应商。",
            departments=("purchase",),
            default_chart_type="hbar",
            supported_chart_types=("hbar", "bar", "pie", "donut"),
            unit="元",
            supports_top_n=True,
            keywords=("供应商", "采购额", "排行"),
        ),
        AnalyticsMetricDefinition(
            code="purchase_arrival_status_distribution",
            name="采购到货状态分布",
            module="purchase",
            description="按到货/未到货状态统计采购订单分布。",
            departments=("purchase", "inventory"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="单",
            keywords=("到货", "采购", "状态"),
        ),
        AnalyticsMetricDefinition(
            code="purchase_payment_type_distribution",
            name="采购付款方式分布",
            module="purchase",
            description="统计先到货后付款、先付款后到货等付款方式结构。",
            departments=("purchase", "finance"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="单",
            keywords=("付款方式", "采购"),
        ),
        AnalyticsMetricDefinition(
            code="inventory_amount_by_warehouse",
            name="仓库库存资金占用",
            module="inventory",
            description="按仓库汇总当前库存资金占用金额。",
            departments=("inventory", "finance"),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            supports_date_range=False,
            keywords=("库存", "仓库", "资金占用"),
        ),
        AnalyticsMetricDefinition(
            code="inventory_item_type_amount",
            name="库存类型资金占用分布",
            module="inventory",
            description="按成品与原料统计库存占用金额。",
            departments=("inventory", "finance"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            supports_date_range=False,
            keywords=("库存", "成品", "原料"),
        ),
        AnalyticsMetricDefinition(
            code="inventory_low_stock_by_warehouse",
            name="低库存预警分布",
            module="inventory",
            description="按仓库统计低于安全库存的条目数量。",
            departments=("inventory",),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="项",
            supports_date_range=False,
            keywords=("低库存", "预警", "仓库"),
        ),
        AnalyticsMetricDefinition(
            code="inventory_transaction_type_distribution",
            name="出入库业务类型分布",
            module="inventory",
            description="按出入库业务类型统计作业次数。",
            departments=("inventory",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="次",
            keywords=("出入库", "库存流水", "业务类型"),
        ),
        AnalyticsMetricDefinition(
            code="stock_take_difference_by_warehouse",
            name="盘点差异仓库分布",
            module="inventory",
            description="按仓库统计盘点绝对差异量，用于识别盘点异常集中区域。",
            departments=("inventory",),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="差异量",
            keywords=("盘点", "差异", "仓库"),
        ),
        AnalyticsMetricDefinition(
            code="inventory_turnover_rate_monthly",
            name="库存周转率月度趋势",
            module="finance",
            description="按月以销售出库与生产领料出库成本，除以平均库存余额，估算库存周转率。",
            departments=("inventory", "finance"),
            default_chart_type="line",
            supported_chart_types=("line", "bar", "area"),
            unit="次",
            keywords=("库存周转率", "周转", "库存", "成本"),
        ),
        AnalyticsMetricDefinition(
            code="logistics_delivery_fee_monthly",
            name="每月运费金额",
            module="inventory",
            description="按物流单开单日期统计每月送货费金额，用于观察运输费用变化。",
            departments=("sales", "purchase", "inventory", "finance"),
            default_chart_type="bar",
            supported_chart_types=("bar", "line", "area"),
            unit="元",
            keywords=("物流", "运费", "送货费", "月份"),
        ),
        AnalyticsMetricDefinition(
            code="logistics_module_distribution",
            name="物流记录业务分布",
            module="inventory",
            description="按销售、采购、库存模块统计物流记录数量。",
            departments=("sales", "purchase", "inventory"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="条",
            keywords=("物流", "模块"),
        ),
        AnalyticsMetricDefinition(
            code="production_task_status_distribution",
            name="生产任务状态分布",
            module="production",
            description="按生产任务状态统计任务量。",
            departments=("production",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="项",
            keywords=("生产", "任务", "状态"),
        ),
        AnalyticsMetricDefinition(
            code="production_output_top_products",
            name="生产产出产品排名",
            module="production",
            description="按产品汇总生产产出数量，识别高产出产品。",
            departments=("production",),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="数量",
            supports_top_n=True,
            keywords=("生产", "产出", "产品"),
        ),
        AnalyticsMetricDefinition(
            code="production_material_usage_top_materials",
            name="原料耗用排名",
            module="production",
            description="按原料汇总已申请/已出库耗用量。",
            departments=("production", "inventory"),
            default_chart_type="hbar",
            supported_chart_types=("hbar", "bar", "pie", "donut"),
            unit="数量",
            supports_top_n=True,
            keywords=("原料", "耗用", "领料"),
        ),
        AnalyticsMetricDefinition(
            code="production_plan_status_distribution",
            name="生产计划状态分布",
            module="production",
            description="按生产计划状态统计计划数量。",
            departments=("production",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="项",
            keywords=("生产计划", "状态"),
        ),
        AnalyticsMetricDefinition(
            code="quality_source_distribution",
            name="质检任务来源分布",
            module="quality",
            description="按采购来料与生产任务来源统计质检任务数量。",
            departments=("quality",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="项",
            keywords=("质检", "来源", "采购来料", "生产"),
        ),
        AnalyticsMetricDefinition(
            code="quality_result_distribution",
            name="质检结果分布",
            module="quality",
            description="按合格、返工、报废、待处理等结果统计质检任务。",
            departments=("quality",),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="项",
            keywords=("质检", "结果", "合格", "报废", "返工"),
        ),
        AnalyticsMetricDefinition(
            code="quality_grade_distribution",
            name="质检等级分布",
            module="quality",
            description="按质检等级统计最近一次检验结果。",
            departments=("quality",),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="项",
            keywords=("质检", "等级"),
        ),
        AnalyticsMetricDefinition(
            code="quality_sample_consumption_monthly",
            name="质检耗样月度趋势",
            module="quality",
            description="按月份统计质检过程中的样品消耗量。",
            departments=("quality", "inventory"),
            default_chart_type="line",
            supported_chart_types=("line", "area", "bar"),
            unit="数量",
            keywords=("耗样", "质检", "趋势"),
        ),
        AnalyticsMetricDefinition(
            code="finance_cash_flow_monthly",
            name="资金流入流出月度趋势",
            module="finance",
            description="按月份统计资金流入、流出及净流量。",
            departments=("finance",),
            default_chart_type="line",
            supported_chart_types=("line", "area", "bar"),
            unit="元",
            keywords=("资金", "流水", "净流量", "趋势"),
        ),
        AnalyticsMetricDefinition(
            code="reimbursement_category_monthly",
            name="报销费用分类月度统计",
            module="finance",
            description="按月份和费用大类统计已报销金额，用于分析管理费用、销售费用和财务费用变化。",
            departments=("finance",),
            default_chart_type="bar",
            supported_chart_types=("bar", "line", "area"),
            unit="元",
            keywords=("报销", "费用", "管理费用", "销售费用", "财务费用"),
        ),
        AnalyticsMetricDefinition(
            code="finance_cash_flow_by_business_type",
            name="资金流水业务分布",
            module="finance",
            description="按业务类型统计资金流水金额。",
            departments=("finance",),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            keywords=("资金流水", "业务类型"),
        ),
        AnalyticsMetricDefinition(
            code="finance_receivable_payable_monthly",
            name="应收应付款月度对比",
            module="finance",
            description="按月份对比回款金额与付款金额。",
            departments=("finance",),
            default_chart_type="bar",
            supported_chart_types=("bar", "line", "area"),
            unit="元",
            keywords=("应收", "应付", "对比", "月份"),
        ),
        AnalyticsMetricDefinition(
            code="finance_quality_cost_by_type",
            name="质量成本类型分布",
            module="finance",
            description="按返工、报废等处置类型统计质量成本。",
            departments=("finance", "quality"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            keywords=("质量成本", "返工", "报废"),
        ),
        AnalyticsMetricDefinition(
            code="refund_by_business_type",
            name="退款业务分布",
            module="finance",
            description="按销售退货退款与采购退货退款统计金额。",
            departments=("finance", "sales", "purchase"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="元",
            keywords=("退款", "销售退货", "采购退货"),
        ),
        AnalyticsMetricDefinition(
            code="customer_new_monthly",
            name="每月新增客户量",
            module="master",
            description="按客户建档时间统计每月新增客户数量，用于分析客户开发情况。",
            departments=("sales", "finance"),
            default_chart_type="bar",
            supported_chart_types=("bar", "line", "area"),
            unit="个",
            keywords=("客户", "新增客户", "建档", "月份"),
        ),
        AnalyticsMetricDefinition(
            code="product_fulfillment_mode_distribution",
            name="产品履约模式分布",
            module="master",
            description="按贸易型与生产型统计产品主数据结构。",
            departments=("sales", "purchase", "production", "inventory"),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="个",
            supports_date_range=False,
            keywords=("产品", "贸易型", "生产型"),
        ),
        AnalyticsMetricDefinition(
            code="supplier_catalog_top",
            name="供应商供货名录覆盖排名",
            module="master",
            description="按供应商统计其可提供的产品/原料名录数量。",
            departments=("purchase",),
            default_chart_type="hbar",
            supported_chart_types=("hbar", "bar", "pie", "donut"),
            unit="项",
            supports_date_range=False,
            supports_top_n=True,
            keywords=("供应商", "名录", "覆盖"),
        ),
        AnalyticsMetricDefinition(
            code="user_role_distribution",
            name="用户角色分布",
            module="admin",
            description="按角色统计系统用户数量。",
            departments=(),
            default_chart_type="donut",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="人",
            supports_date_range=False,
            keywords=("用户", "角色", "权限"),
        ),
        AnalyticsMetricDefinition(
            code="user_department_distribution",
            name="组织用户分布",
            module="admin",
            description="按组织单元统计用户数量。",
            departments=(),
            default_chart_type="hbar",
            supported_chart_types=("hbar", "bar", "pie", "donut"),
            unit="人",
            supports_date_range=False,
            supports_top_n=True,
            keywords=("用户", "部门", "组织"),
        ),
        AnalyticsMetricDefinition(
            code="operation_log_module_distribution",
            name="系统操作日志模块分布",
            module="admin",
            description="按系统模块统计操作日志量，用于观察系统使用重心。",
            departments=(),
            default_chart_type="bar",
            supported_chart_types=("bar", "hbar", "pie", "donut"),
            unit="次",
            keywords=("日志", "模块", "审计"),
        ),
    ]


METRIC_DEFINITION_MAP = {item.code: item for item in _metric_definitions()}


def _is_all_access_user(current_user: User) -> bool:
    role_code = current_user.role.code if current_user.role else ""
    return (
        is_system_admin(current_user)
        or is_master_data_admin(current_user)
        or role_code in ALL_ACCESS_ROLE_CODES
    )


def _float(value: Decimal | int | float | None) -> float:
    return float(Decimal(value or 0))


def _date_in_range(value: datetime | date | None, date_from: date | None, date_to: date | None) -> bool:
    if value is None:
        return date_from is None and date_to is None
    target_date = value if isinstance(value, date) and not isinstance(value, datetime) else value.date()
    if date_from and target_date < date_from:
        return False
    if date_to and target_date > date_to:
        return False
    return True


def _month_bucket(value: datetime | date | None) -> str:
    if value is None:
        return "未知"
    target_date = value if isinstance(value, date) and not isinstance(value, datetime) else value.date()
    return f"{target_date.year}-{target_date.month:02d}"


def _month_start(value: datetime | date | None) -> date:
    target_date = value if isinstance(value, date) and not isinstance(value, datetime) else (value.date() if value else date.today())
    return date(target_date.year, target_date.month, 1)


def _shift_month(month_value: date, offset: int) -> date:
    month_index = (month_value.year * 12 + month_value.month - 1) + offset
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _month_sequence(start_month: date, end_month: date) -> list[date]:
    if end_month < start_month:
        start_month, end_month = end_month, start_month
    result: list[date] = []
    cursor = start_month
    while cursor <= end_month:
        result.append(cursor)
        cursor = _shift_month(cursor, 1)
    return result


def _target_months(date_from: date | None, date_to: date | None, *, default_recent_months: int = 6) -> list[date]:
    today_month = _month_start(date.today())
    if date_from or date_to:
        start_month = _month_start(date_from or _shift_month(today_month, -(default_recent_months - 1)))
        end_month = _month_start(date_to or today_month)
    else:
        end_month = today_month
        start_month = _shift_month(end_month, -(default_recent_months - 1))
    return _month_sequence(start_month, end_month)


def _sales_report_datetime(row: SalesOrder) -> datetime | None:
    return row.completed_at or row.shipped_at or row.approved_at or row.created_at


def _leadership_priority(metric: AnalyticsMetricDefinition) -> int:
    score = LEADERSHIP_METRIC_PRIORITY.get(metric.code, 0)
    haystack = " ".join([metric.name, metric.description, *metric.keywords])
    if "利润" in haystack:
        score = max(score, 900)
    if "成本" in haystack:
        score = max(score, 890)
    if "周转" in haystack:
        score = max(score, 880)
    if "资金" in haystack or "现金" in haystack:
        score = max(score, 870)
    return score


def _rank_rows(bucket: dict[str, Decimal | int | float], top_n: int) -> list[dict]:
    rows = sorted(bucket.items(), key=lambda item: float(item[1] or 0), reverse=True)[:top_n]
    return [{"label": label or "未命名", "value": round(_float(value), 4)} for label, value in rows]


def _distribution_rows(bucket: dict[str, Decimal | int | float]) -> list[dict]:
    return [{"label": label or "未命名", "value": round(_float(value), 4)} for label, value in bucket.items()]


def _series_from_rows(rows: list[dict], series_name: str) -> tuple[list[str], list[dict]]:
    return [row["label"] for row in rows], [{"name": series_name, "values": [round(_float(row["value"]), 4) for row in rows]}]


def _single_series_result(
    metric: AnalyticsMetricDefinition,
    rows: list[dict],
    *,
    chart_type: str,
    summary: list[dict] | None = None,
    x_axis_label: str = "类别",
    y_axis_label: str | None = None,
) -> dict:
    categories, series = _series_from_rows(rows, metric.name)
    total_value = round(sum(_float(row["value"]) for row in rows), 4)
    count_value = len(rows)
    total_for_percent = total_value if total_value > 0 else 0
    table_rows = []
    for row in rows:
        percent = round((_float(row["value"]) / total_for_percent) * 100, 2) if total_for_percent else 0
        table_rows.append(
            {
                "label": row["label"],
                "value": round(_float(row["value"]), 4),
                "percentage": percent,
            }
        )
    return {
        "metric": metric.to_dict(),
        "chart_type": chart_type,
        "chart": {
            "categories": categories,
            "series": series,
            "rows": table_rows,
            "unit": metric.unit,
            "x_axis_label": x_axis_label,
            "y_axis_label": y_axis_label or metric.unit or "数值",
        },
        "summary": summary
        or [
            {"label": "合计", "value": total_value, "unit": metric.unit},
            {"label": "分类数", "value": count_value, "unit": "项"},
        ],
    }


def _multi_series_result(
    metric: AnalyticsMetricDefinition,
    categories: list[str],
    series: list[dict],
    *,
    chart_type: str,
    summary: list[dict] | None = None,
    x_axis_label: str = "类别",
    y_axis_label: str | None = None,
) -> dict:
    table_rows = []
    for index, category in enumerate(categories):
        row = {"label": category}
        for item in series:
            row[item["name"]] = round(_float(item["values"][index]), 4)
        table_rows.append(row)
    return {
        "metric": metric.to_dict(),
        "chart_type": chart_type,
        "chart": {
            "categories": categories,
            "series": series,
            "rows": table_rows,
            "unit": metric.unit,
            "x_axis_label": x_axis_label,
            "y_axis_label": y_axis_label or metric.unit or "数值",
        },
        "summary": summary or [],
    }


class AnalyticsContext:
    def __init__(self, db: Session, current_user: User, *, date_from: date | None, date_to: date | None, top_n: int):
        self.db = db
        self.current_user = current_user
        self.date_from = date_from
        self.date_to = date_to
        self.top_n = top_n

    @cached_property
    def sales_scope_user_ids(self) -> list[int] | None:
        return get_scoped_user_ids(self.db, self.current_user, "sales")

    @cached_property
    def purchase_scope_user_ids(self) -> list[int] | None:
        return get_scoped_user_ids(self.db, self.current_user, "purchase")

    @cached_property
    def production_scope_user_ids(self) -> list[int] | None:
        return get_scoped_user_ids(self.db, self.current_user, "production")

    @cached_property
    def inventory_scope_warehouse_ids(self) -> list[int] | None:
        return get_inventory_scope_warehouse_ids(self.db, self.current_user)

    @cached_property
    def sales_orders(self) -> list[SalesOrder]:
        query = (
            select(SalesOrder)
            .options(
                joinedload(SalesOrder.customer),
                joinedload(SalesOrder.items).joinedload(SalesOrderItem.product),
            )
            .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        )
        if self.sales_scope_user_ids is not None:
            query = query.where(SalesOrder.sales_user_id.in_(self.sales_scope_user_ids or [-1]))
        return self.db.execute(query).unique().scalars().all()

    @cached_property
    def sales_returns(self) -> list[SalesReturn]:
        order_ids = [row.id for row in self.sales_orders]
        if not order_ids:
            return []
        return self.db.execute(select(SalesReturn).where(SalesReturn.sales_order_id.in_(order_ids))).scalars().all()

    @cached_property
    def receivable_records(self) -> list[ReceivableRecord]:
        order_ids = [row.id for row in self.sales_orders]
        if not order_ids:
            return []
        return self.db.execute(select(ReceivableRecord).where(ReceivableRecord.sales_order_id.in_(order_ids))).scalars().all()

    @cached_property
    def purchase_orders(self) -> list[PurchaseOrder]:
        query = (
            select(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.supplier),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product),
                joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.material),
            )
            .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
        )
        if self.purchase_scope_user_ids is not None:
            query = query.where(PurchaseOrder.purchaser_user_id.in_(self.purchase_scope_user_ids or [-1]))
        return self.db.execute(query).unique().scalars().all()

    @cached_property
    def purchase_receipts(self) -> list[PurchaseReceipt]:
        order_ids = [row.id for row in self.purchase_orders]
        if not order_ids:
            return []
        query = select(PurchaseReceipt).where(PurchaseReceipt.purchase_order_id.in_(order_ids))
        if self.inventory_scope_warehouse_ids is not None and (self.current_user.role.code == "inventory"):
            query = query.where(PurchaseReceipt.warehouse_id.in_(self.inventory_scope_warehouse_ids or [-1]))
        return self.db.execute(query).scalars().all()

    @cached_property
    def purchase_returns(self) -> list[PurchaseReturn]:
        order_ids = [row.id for row in self.purchase_orders]
        if not order_ids:
            return []
        query = select(PurchaseReturn).where(PurchaseReturn.purchase_order_id.in_(order_ids))
        if self.inventory_scope_warehouse_ids is not None and (self.current_user.role.code == "inventory"):
            query = query.where(PurchaseReturn.warehouse_id.in_(self.inventory_scope_warehouse_ids or [-1]))
        return self.db.execute(query).scalars().all()

    @cached_property
    def payable_records(self) -> list[PayableRecord]:
        order_ids = [row.id for row in self.purchase_orders]
        if not order_ids:
            return []
        return self.db.execute(select(PayableRecord).where(PayableRecord.purchase_order_id.in_(order_ids))).scalars().all()

    @cached_property
    def inventories(self) -> list[Inventory]:
        query = (
            select(Inventory)
            .options(joinedload(Inventory.warehouse), joinedload(Inventory.product), joinedload(Inventory.material))
            .order_by(Inventory.updated_at.desc(), Inventory.id.desc())
        )
        if self.inventory_scope_warehouse_ids is not None:
            query = query.where(Inventory.warehouse_id.in_(self.inventory_scope_warehouse_ids or [-1]))
        return self.db.execute(query).scalars().all()

    @cached_property
    def inventory_transactions(self) -> list[InventoryTransaction]:
        query = select(InventoryTransaction).order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc())
        if self.inventory_scope_warehouse_ids is not None:
            query = query.where(InventoryTransaction.warehouse_id.in_(self.inventory_scope_warehouse_ids or [-1]))
        return self.db.execute(query).scalars().all()

    @cached_property
    def stock_take_tasks(self) -> list[StockTakeTask]:
        query = select(StockTakeTask).order_by(StockTakeTask.created_at.desc(), StockTakeTask.id.desc())
        if self.inventory_scope_warehouse_ids is not None:
            query = query.where(StockTakeTask.warehouse_id.in_(self.inventory_scope_warehouse_ids or [-1]))
        return self.db.execute(query).scalars().all()

    @cached_property
    def stock_take_items(self) -> list[StockTakeItem]:
        task_ids = [row.id for row in self.stock_take_tasks]
        if not task_ids:
            return []
        return self.db.execute(select(StockTakeItem).where(StockTakeItem.stock_take_task_id.in_(task_ids))).scalars().all()

    @cached_property
    def production_tasks(self) -> list[ProductionTask]:
        query = (
            select(ProductionTask)
            .options(
                joinedload(ProductionTask.product),
                joinedload(ProductionTask.usages).joinedload(ProductionMaterialUsage.material),
            )
            .order_by(ProductionTask.created_at.desc(), ProductionTask.id.desc())
        )
        if self.production_scope_user_ids is not None:
            query = query.where(ProductionTask.created_by_id.in_(self.production_scope_user_ids or [-1]))
        return self.db.execute(query).unique().scalars().all()

    @cached_property
    def production_plans(self) -> list[ProductionPlan]:
        query = select(ProductionPlan).options(joinedload(ProductionPlan.product)).order_by(ProductionPlan.created_at.desc(), ProductionPlan.id.desc())
        if self.production_scope_user_ids is not None:
            query = query.where(ProductionPlan.created_by_id.in_(self.production_scope_user_ids or [-1]))
        return self.db.execute(query).scalars().all()

    @cached_property
    def quality_tasks(self) -> list[QualityTask]:
        return self.db.execute(select(QualityTask).order_by(QualityTask.created_at.desc(), QualityTask.id.desc())).scalars().all()

    @cached_property
    def quality_inspections(self) -> list[QualityInspection]:
        task_ids = [row.id for row in self.quality_tasks]
        if not task_ids:
            return []
        return self.db.execute(
            select(QualityInspection).where(QualityInspection.quality_task_id.in_(task_ids)).order_by(QualityInspection.sequence_no.asc())
        ).scalars().all()

    @cached_property
    def quality_dispositions(self) -> list[QualityDisposition]:
        task_ids = [row.id for row in self.quality_tasks]
        if not task_ids:
            return []
        return self.db.execute(select(QualityDisposition).where(QualityDisposition.quality_task_id.in_(task_ids))).scalars().all()

    @cached_property
    def quality_cost_records(self) -> list[QualityCostRecord]:
        disposition_ids = [row.id for row in self.quality_dispositions]
        if not disposition_ids:
            return []
        return self.db.execute(select(QualityCostRecord).where(QualityCostRecord.quality_disposition_id.in_(disposition_ids))).scalars().all()

    @cached_property
    def cash_flows(self) -> list[CashFlowRecord]:
        return self.db.execute(select(CashFlowRecord).order_by(CashFlowRecord.happened_at.desc(), CashFlowRecord.id.desc())).scalars().all()

    @cached_property
    def refunds(self) -> list[RefundRecord]:
        sales_return_ids = [row.id for row in self.sales_returns]
        purchase_return_ids = [row.id for row in self.purchase_returns]
        if not sales_return_ids and not purchase_return_ids:
            return []
        query = select(RefundRecord)
        if sales_return_ids and purchase_return_ids:
            query = query.where(
                (RefundRecord.sales_return_id.in_(sales_return_ids))
                | (RefundRecord.purchase_return_id.in_(purchase_return_ids))
            )
        elif sales_return_ids:
            query = query.where(RefundRecord.sales_return_id.in_(sales_return_ids))
        else:
            query = query.where(RefundRecord.purchase_return_id.in_(purchase_return_ids))
        return self.db.execute(query).scalars().all()

    @cached_property
    def logistics_records(self) -> list[LogisticsRecord]:
        return self.db.execute(select(LogisticsRecord).order_by(LogisticsRecord.created_at.desc(), LogisticsRecord.id.desc())).scalars().all()

    @cached_property
    def reimbursements(self) -> list[Reimbursement]:
        return self.db.execute(select(Reimbursement).order_by(Reimbursement.occurred_at.desc(), Reimbursement.id.desc())).scalars().all()

    @cached_property
    def users(self) -> list[User]:
        return self.db.execute(
            select(User).options(joinedload(User.role), joinedload(User.department)).where(User.is_active.is_(True))
        ).scalars().all()

    @cached_property
    def roles(self) -> dict[int, Role]:
        return {row.id: row for row in self.db.execute(select(Role)).scalars().all()}

    @cached_property
    def departments(self) -> dict[int, Department]:
        return {row.id: row for row in self.db.execute(select(Department)).scalars().all()}

    @cached_property
    def warehouses(self) -> dict[int, Warehouse]:
        return {row.id: row for row in self.db.execute(select(Warehouse)).scalars().all()}

    @cached_property
    def products(self) -> list[Product]:
        return self.db.execute(select(Product).where(Product.is_active.is_(True))).scalars().all()

    @cached_property
    def supplier_catalog_items(self) -> list[SupplierCatalogItem]:
        return self.db.execute(
            select(SupplierCatalogItem).options(joinedload(SupplierCatalogItem.supplier))
        ).scalars().all()

    @cached_property
    def operation_logs(self) -> list[OperationLog]:
        return self.db.execute(select(OperationLog).order_by(OperationLog.created_at.desc(), OperationLog.id.desc())).scalars().all()


def _visible_metrics(current_user: User, *, keyword: str | None = None) -> list[AnalyticsMetricDefinition]:
    normalized = (keyword or "").strip().lower()
    visible = []
    role_code = current_user.role.code if current_user.role else ""
    # 超级角色继承基础角色可见性
    SUPER_BASE = {"inventory_super": "inventory", "quality_super": "quality", "finance_super": "finance"}
    visible_role_codes = {role_code, SUPER_BASE.get(role_code, "")} - {""}
    for metric in METRIC_DEFINITION_MAP.values():
        if _is_all_access_user(current_user):
            allowed = True
        else:
            allowed = bool(visible_role_codes.intersection(metric.departments))
        if not allowed:
            continue
        if normalized:
            haystack = " ".join(
                [
                    metric.code,
                    metric.name,
                    metric.description,
                    METRIC_CALCULATION_LOGICS.get(metric.code, ""),
                    metric.module_label,
                    *metric.keywords,
                ]
            ).lower()
            if normalized not in haystack:
                continue
        visible.append(metric)
    if _is_all_access_user(current_user):
        return sorted(visible, key=lambda item: (-_leadership_priority(item), item.module_label, item.name))
    return sorted(visible, key=lambda item: (item.module_label, item.name))


def list_accessible_metrics(current_user: User, *, keyword: str | None = None) -> list[dict]:
    return [item.to_dict() for item in _visible_metrics(current_user, keyword=keyword)]


def _metric_or_raise(metric_code: str, current_user: User) -> AnalyticsMetricDefinition:
    metric = METRIC_DEFINITION_MAP.get(metric_code)
    if metric is None:
        raise KeyError("指标不存在")
    if metric.code not in {item.code for item in _visible_metrics(current_user)}:
        raise PermissionError("无权查看该指标")
    return metric


def _sales_order_status_count(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    total_orders = 0
    for row in ctx.sales_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[status_label(row.status)] += 1
        total_orders += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(
        metric,
        rows,
        chart_type=chart_type,
        summary=[
            {"label": "订单总数", "value": total_orders, "unit": "单"},
            {"label": "状态数", "value": len(rows), "unit": "类"},
        ],
    )


def _sales_amount_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.sales_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(row.created_at)] += Decimal(row.total_amount or 0)
    categories = sorted(bucket.keys())
    values = [round(_float(bucket[item]), 4) for item in categories]
    return _multi_series_result(
        metric,
        categories,
        [{"name": "订单金额", "values": values}],
        chart_type=chart_type,
        summary=[
            {"label": "累计销售额", "value": round(sum(values), 4), "unit": "元"},
            {"label": "月份数", "value": len(categories), "unit": "月"},
        ],
        x_axis_label="月份",
        y_axis_label="销售额（元）",
    )


def _sales_top_customers_amount(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    order_count_bucket: dict[str, int] = defaultdict(int)
    for row in ctx.sales_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        customer_name = row.customer.name if row.customer else "未命名客户"
        bucket[customer_name] += Decimal(row.total_amount or 0)
        order_count_bucket[customer_name] += 1
    rows = _rank_rows(bucket, ctx.top_n)
    result = _single_series_result(
        metric,
        rows,
        chart_type=chart_type,
        summary=[
            {"label": "客户数", "value": len(bucket), "unit": "家"},
            {"label": "累计销售额", "value": round(sum(_float(value) for value in bucket.values()), 4), "unit": "元"},
        ],
        y_axis_label="销售额（元）",
    )
    for row in result["chart"]["rows"]:
        row["订单数"] = order_count_bucket.get(row["label"], 0)
    return result


def _sales_top_products_quantity(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for order in ctx.sales_orders:
        if not _date_in_range(order.created_at, ctx.date_from, ctx.date_to):
            continue
        for item in order.items:
            product_name = item.product.name if item.product else "未命名产品"
            bucket[product_name] += Decimal(item.quantity or 0)
    rows = _rank_rows(bucket, ctx.top_n)
    return _single_series_result(
        metric,
        rows,
        chart_type=chart_type,
        summary=[
            {"label": "产品数", "value": len(bucket), "unit": "种"},
            {"label": "累计销售数量", "value": round(sum(_float(value) for value in bucket.values()), 4), "unit": "数量"},
        ],
    )


def _sales_payment_type_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.sales_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[payment_type_label(row.payment_type, deposit_amount=row.deposit_amount, module="sales")] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "订单数", "value": sum(row["value"] for row in rows), "unit": "单"}])


def _sales_receipt_stage_amount(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.receivable_records:
        if not _date_in_range(row.received_at, ctx.date_from, ctx.date_to):
            continue
        bucket[payment_stage_label(row.payment_stage)] += Decimal(row.amount or 0)
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="回款金额（元）")


def _sales_return_type_amount(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.sales_returns:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[return_type_label(row.return_type)] += Decimal(row.refund_amount or 0)
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="金额（元）")


def _sales_cost_profit_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    months = _target_months(ctx.date_from, ctx.date_to)
    month_set = set(months)
    bucket: dict[date, dict[str, Decimal]] = defaultdict(lambda: {"sales": Decimal("0"), "cost": Decimal("0"), "profit": Decimal("0")})
    orders = {row.id: row for row in ctx.sales_orders}
    unknown_movements = 0
    if orders:
        movements = ctx.db.execute(select(InventoryTransaction).where(
            InventoryTransaction.related_type == "sales_order",
            InventoryTransaction.related_id.in_(list(orders)),
            InventoryTransaction.transaction_type.in_(("sales_outbound", "sales_packaging_outbound")),
        )).scalars().all()
        for movement in movements:
            if not _date_in_range(movement.created_at, ctx.date_from, ctx.date_to):
                continue
            month = _month_start(movement.created_at)
            if month not in month_set:
                continue
            order = orders[movement.related_id]
            shipped = -Decimal(movement.quantity or 0)
            specific_line = next((item for item in order.items if item.id == movement.sales_order_item_id), None)
            matching = [specific_line] if specific_line is not None else [item for item in order.items if item.product_id == movement.product_id]
            if movement.transaction_type == "sales_outbound" and shipped > 0:
                matching_quantity = sum((Decimal(item.quantity or 0) for item in matching), Decimal("0"))
                total_quantity = sum((Decimal(item.quantity or 0) for item in order.items), Decimal("0"))
                if matching_quantity > 0:
                    line_revenue = sum((Decimal(item.total_amount or 0) for item in matching), Decimal("0"))
                    bucket[month]["sales"] += shipped * line_revenue / matching_quantity
                if total_quantity > 0:
                    bucket[month]["sales"] += shipped * Decimal(order.transport_fee or 0) / total_quantity
            if movement.cost_status == "unpriced":
                unknown_movements += 1
                continue
            cost = -Decimal(movement.total_cost or 0)
            bucket[month]["cost"] += cost
            bucket[month]["profit"] += -cost
            if movement.transaction_type == "sales_outbound" and shipped > 0:
                matching_quantity = sum((Decimal(item.quantity or 0) for item in matching), Decimal("0"))
                total_quantity = sum((Decimal(item.quantity or 0) for item in order.items), Decimal("0"))
                if matching_quantity > 0:
                    line_revenue = sum((Decimal(item.total_amount or 0) for item in matching), Decimal("0"))
                    packaging_fee = sum((Decimal(item.packaging_fee or 0) for item in matching), Decimal("0"))
                    bucket[month]["profit"] += shipped * (line_revenue - packaging_fee) / matching_quantity
                # transport_fee is treated as a pass-through: order's existing profit
                # calculation subtracts the same amount it includes in revenue.
    categories = [_month_bucket(month) for month in months]
    sales_values = [round(_float(bucket[month]["sales"]), 4) for month in months]
    cost_values = [round(_float(bucket[month]["cost"]), 4) for month in months]
    profit_values = [round(_float(bucket[month]["profit"]), 4) for month in months]
    return _multi_series_result(
        metric,
        categories,
        [
            {"name": "销售收入", "values": sales_values},
            {"name": "已定价销售成本", "values": cost_values},
            {"name": "已定价销售利润", "values": profit_values},
        ],
        chart_type=chart_type,
        summary=[
            {"label": "累计收入", "value": round(sum(sales_values), 4), "unit": "元"},
            {"label": "累计成本", "value": round(sum(cost_values), 4), "unit": "元"},
            {"label": "已定价利润", "value": round(sum(profit_values), 4), "unit": "元"},
            {"label": "待定价出库笔数", "value": unknown_movements, "unit": "笔"},
        ],
        x_axis_label="月份",
        y_axis_label="金额（元）",
    )


def _purchase_order_status_count(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.purchase_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[status_label(row.status)] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "采购单数", "value": sum(row["value"] for row in rows), "unit": "单"}])


def _purchase_amount_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.purchase_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(row.created_at)] += Decimal(row.total_amount or 0)
    categories = sorted(bucket.keys())
    values = [round(_float(bucket[item]), 4) for item in categories]
    return _multi_series_result(
        metric,
        categories,
        [{"name": "采购金额", "values": values}],
        chart_type=chart_type,
        summary=[
            {"label": "累计采购额", "value": round(sum(values), 4), "unit": "元"},
            {"label": "月份数", "value": len(categories), "unit": "月"},
        ],
        x_axis_label="月份",
        y_axis_label="采购额（元）",
    )


def _purchase_top_suppliers_amount(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.purchase_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        supplier_name = row.supplier.name if row.supplier else "未命名供应商"
        bucket[supplier_name] += Decimal(row.total_amount or 0)
    rows = _rank_rows(bucket, ctx.top_n)
    return _single_series_result(
        metric,
        rows,
        chart_type=chart_type,
        summary=[
            {"label": "供应商数", "value": len(bucket), "unit": "家"},
            {"label": "累计采购额", "value": round(sum(_float(value) for value in bucket.values()), 4), "unit": "元"},
        ],
        y_axis_label="采购额（元）",
    )


def _purchase_arrival_status_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.purchase_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[status_label(purchase_arrival_status(ctx.db, row.id))] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "采购单数", "value": sum(row["value"] for row in rows), "unit": "单"}])


def _purchase_payment_type_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.purchase_orders:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[payment_type_label(row.payment_type, module="purchase")] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type)


def _inventory_amount_by_warehouse(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.inventories:
        warehouse_name = row.warehouse.name if row.warehouse else "未命名仓库"
        bucket[warehouse_name] += Decimal(row.cost_balance or 0)
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="库存金额（元）")


def _inventory_item_type_amount(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.inventories:
        key = "产品库存" if row.product_id else ("原料库存" if row.material_id else "包装库存")
        bucket[key] += Decimal(row.cost_balance or 0)
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="库存金额（元）")


def _inventory_low_stock_by_warehouse(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    total_warning = 0
    for row in ctx.inventories:
        item = row.product or row.material
        safety_stock = Decimal(getattr(item, "safety_stock_qty", 0) or 0)
        available_quantity = Decimal(row.quantity or 0) - Decimal(row.reserved_quantity or 0)
        if available_quantity >= safety_stock:
            continue
        warehouse_name = row.warehouse.name if row.warehouse else "未命名仓库"
        bucket[warehouse_name] += 1
        total_warning += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "低库存条目", "value": total_warning, "unit": "项"}])


def _inventory_transaction_type_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.inventory_transactions:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[transaction_type_label(row.transaction_type)] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "流水次数", "value": sum(row["value"] for row in rows), "unit": "次"}])


def _stock_take_difference_by_warehouse(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    warehouse_map = {row.id: row for row in ctx.stock_take_tasks}
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.stock_take_items:
        task = warehouse_map.get(row.stock_take_task_id)
        if task is None or not _date_in_range(task.created_at, ctx.date_from, ctx.date_to):
            continue
        warehouse_name = ctx.warehouses.get(task.warehouse_id).name if task.warehouse_id in ctx.warehouses else None
        if warehouse_name is None:
            warehouse_name = next((item.warehouse.name for item in ctx.inventories if item.warehouse_id == task.warehouse_id and item.warehouse), f"仓库{task.warehouse_id}")
        bucket[warehouse_name] += abs(Decimal(row.difference_quantity or 0))
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="差异量")


def _inventory_turnover_rate_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    target_months = _target_months(ctx.date_from, ctx.date_to)
    if not target_months:
        return _multi_series_result(metric, [], [], chart_type=chart_type)

    earliest_month = _shift_month(target_months[0], -1)
    latest_month = max(_month_start(date.today()), target_months[-1])
    all_months = _month_sequence(earliest_month, latest_month)
    target_month_set = set(target_months)
    outbound_transaction_types = {"sales_outbound", "production_material_outbound"}

    current_balance_map: dict[tuple[int, int, int, int], Decimal] = {}
    monthly_cost_delta: dict[tuple[int, int, int, int], dict[date, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    outbound_cost_bucket: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))

    for row in ctx.inventories:
        key = (int(row.warehouse_id or 0), int(row.product_id or 0), int(row.material_id or 0), int(row.packaging_config_id or 0))
        current_balance_map[key] = Decimal(row.cost_balance or 0)

    for row in ctx.inventory_transactions:
        key = (int(row.warehouse_id or 0), int(row.product_id or 0), int(row.material_id or 0), int(row.packaging_config_id or 0))
        month = _month_start(row.created_at)
        monthly_cost_delta[key][month] += Decimal(row.total_cost or 0)
        current_balance_map.setdefault(key, Decimal("0"))
        if month in target_month_set and row.transaction_type in outbound_transaction_types and Decimal(row.quantity or 0) < 0:
            outbound_cost_bucket[month] += abs(Decimal(row.total_cost or 0))

    closing_inventory_amount: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for key, current_balance in current_balance_map.items():
        balance = Decimal(current_balance or 0)
        for month in reversed(all_months):
            closing_inventory_amount[month] += balance
            balance -= monthly_cost_delta[key].get(month, Decimal("0"))

    categories = []
    rate_values: list[float] = []
    latest_rate = Decimal("0")
    average_inventory_total = Decimal("0")
    for month in target_months:
        closing_amount = closing_inventory_amount.get(month, Decimal("0"))
        opening_amount = closing_inventory_amount.get(_shift_month(month, -1), closing_amount)
        average_inventory_amount = (opening_amount + closing_amount) / Decimal("2") if (opening_amount or closing_amount) else Decimal("0")
        turnover_rate = (outbound_cost_bucket.get(month, Decimal("0")) / average_inventory_amount) if average_inventory_amount > 0 else Decimal("0")
        categories.append(_month_bucket(month))
        rate_values.append(round(_float(turnover_rate), 4))
        latest_rate = turnover_rate
        average_inventory_total += average_inventory_amount

    average_turnover_rate = (sum(Decimal(str(value)) for value in rate_values) / Decimal(len(rate_values))) if rate_values else Decimal("0")
    total_outbound_cost = sum(outbound_cost_bucket.get(month, Decimal("0")) for month in target_months)
    return _multi_series_result(
        metric,
        categories,
        [{"name": "库存周转率", "values": rate_values}],
        chart_type=chart_type,
        summary=[
            {"label": "最新周转率", "value": round(_float(latest_rate), 4), "unit": "次"},
            {"label": "平均周转率", "value": round(_float(average_turnover_rate), 4), "unit": "次"},
            {"label": "累计出库成本", "value": round(_float(total_outbound_cost), 4), "unit": "元"},
        ],
        x_axis_label="月份",
        y_axis_label="周转率（次）",
    )


def _logistics_module_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.logistics_records:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        module_label = BUSINESS_MODULE_LABELS.get(row.module, row.module or "其他")
        bucket[module_label] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "物流记录", "value": sum(row["value"] for row in rows), "unit": "条"}])



def _logistics_delivery_fee_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.logistics_records:
        target_date = row.bill_date or row.created_at
        if not _date_in_range(target_date, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(target_date)] += Decimal(row.delivery_fee or 0)
    categories = sorted(bucket.keys())
    values = [round(_float(bucket[item]), 4) for item in categories]
    return _multi_series_result(
        metric,
        categories,
        [{"name": "送货费", "values": values}],
        chart_type=chart_type,
        summary=[{"label": "运费合计", "value": round(sum(values), 4), "unit": "元"}],
        x_axis_label="月份",
        y_axis_label="送货费金额",
    )


def _customer_new_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    customer_rows = ctx.db.execute(select(Customer).order_by(Customer.created_at.desc())).scalars().all()
    role_code = ctx.current_user.role.code if ctx.current_user.role else ""
    if role_code == "sales" and ctx.sales_scope_user_ids is not None:
        customer_rows = [row for row in customer_rows if getattr(row, "owner_user_id", None) in ctx.sales_scope_user_ids]
    bucket: dict[str, int] = defaultdict(int)
    for row in customer_rows:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(row.created_at)] += 1
    categories = sorted(bucket.keys())
    values = [bucket[item] for item in categories]
    return _multi_series_result(
        metric,
        categories,
        [{"name": "新增客户", "values": values}],
        chart_type=chart_type,
        summary=[{"label": "新增客户总数", "value": sum(values), "unit": "个"}],
        x_axis_label="月份",
        y_axis_label="新增客户数量",
    )


def _reimbursement_category_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    category_labels = {"management": "管理费用", "sales": "销售费用", "finance": "财务费用", "other": "其他"}
    bucket: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for row in ctx.reimbursements:
        if row.status != "paid":
            continue
        target_date = row.paid_at or row.occurred_at or row.created_at
        if not _date_in_range(target_date, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(target_date)][category_labels.get(row.expense_category, row.expense_category or "其他")] += Decimal(row.amount or 0)
    categories = sorted(bucket.keys())
    series_names = sorted({name for month_data in bucket.values() for name in month_data.keys()})
    series = [{"name": name, "values": [round(_float(bucket[month].get(name, 0)), 4) for month in categories]} for name in series_names]
    total = round(sum(sum(_float(value) for value in month_data.values()) for month_data in bucket.values()), 4)
    return _multi_series_result(
        metric,
        categories,
        series,
        chart_type=chart_type,
        summary=[{"label": "报销金额合计", "value": total, "unit": "元"}],
        x_axis_label="月份",
        y_axis_label="报销金额",
    )


def _production_task_status_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.production_tasks:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[status_label(row.status)] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "生产任务", "value": sum(row["value"] for row in rows), "unit": "项"}])


def _production_output_top_products(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.production_tasks:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        product_name = row.product.name if row.product else "未命名产品"
        bucket[product_name] += Decimal(row.produced_quantity or 0)
    rows = _rank_rows(bucket, ctx.top_n)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "累计产出", "value": round(sum(_float(value) for value in bucket.values()), 4), "unit": "数量"}])


def _production_material_usage_top_materials(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for task in ctx.production_tasks:
        if not _date_in_range(task.created_at, ctx.date_from, ctx.date_to):
            continue
        for usage in task.usages:
            material_name = usage.material.name if usage.material else "未命名原料"
            bucket[material_name] += Decimal(usage.issued_quantity or usage.requested_quantity or usage.planned_quantity or 0)
    rows = _rank_rows(bucket, ctx.top_n)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "累计耗用", "value": round(sum(_float(value) for value in bucket.values()), 4), "unit": "数量"}])


def _production_plan_status_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.production_plans:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[status_label(row.status)] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type)


def _quality_source_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.quality_tasks:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        source_label = "采购来料" if row.source_category == "purchase" else "生产任务"
        bucket[source_label] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type)


def _quality_result_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    disposition_map = {row.quality_task_id: row for row in ctx.quality_dispositions}
    inspection_map: dict[int, list[QualityInspection]] = defaultdict(list)
    for row in ctx.quality_inspections:
        inspection_map[row.quality_task_id].append(row)
    bucket: dict[str, int] = defaultdict(int)
    for task in ctx.quality_tasks:
        if not _date_in_range(task.created_at, ctx.date_from, ctx.date_to):
            continue
        disposition = disposition_map.get(task.id)
        inspections = inspection_map.get(task.id, [])
        latest_inspection = inspections[-1] if inspections else None
        if disposition and disposition.disposition_type == "scrap":
            key = "报废"
        elif disposition and disposition.disposition_type == "rework":
            key = "返工"
        elif latest_inspection and latest_inspection.is_passed and task.status in {"ready_for_inbound", "completed"}:
            key = "合格"
        elif task.status == "pending_disposition":
            key = "待处置"
        else:
            key = "待检/处理中"
        bucket[key] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type)


def _quality_grade_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    inspection_map: dict[int, list[QualityInspection]] = defaultdict(list)
    for row in ctx.quality_inspections:
        inspection_map[row.quality_task_id].append(row)
    bucket: dict[str, int] = defaultdict(int)
    for task in ctx.quality_tasks:
        if not _date_in_range(task.created_at, ctx.date_from, ctx.date_to):
            continue
        inspections = inspection_map.get(task.id, [])
        if not inspections:
            bucket["未判级"] += 1
            continue
        bucket[inspections[-1].grade or "未判级"] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type)


def _quality_sample_consumption_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.quality_inspections:
        if not _date_in_range(row.inspected_at, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(row.inspected_at)] += Decimal(row.sample_consumption_qty or 0)
    categories = sorted(bucket.keys())
    values = [round(_float(bucket[item]), 4) for item in categories]
    return _multi_series_result(
        metric,
        categories,
        [{"name": "耗样量", "values": values}],
        chart_type=chart_type,
        summary=[{"label": "累计耗样量", "value": round(sum(values), 4), "unit": "数量"}],
        x_axis_label="月份",
        y_axis_label="耗样量",
    )


def _finance_cash_flow_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"incoming": Decimal("0"), "outgoing": Decimal("0")})
    for row in ctx.cash_flows:
        if not _date_in_range(row.happened_at, ctx.date_from, ctx.date_to):
            continue
        month = _month_bucket(row.happened_at)
        if row.flow_type == "incoming":
            bucket[month]["incoming"] += Decimal(row.amount or 0)
        else:
            bucket[month]["outgoing"] += Decimal(row.amount or 0)
    categories = sorted(bucket.keys())
    incoming = [round(_float(bucket[item]["incoming"]), 4) for item in categories]
    outgoing = [round(_float(bucket[item]["outgoing"]), 4) for item in categories]
    net = [round(incoming[index] - outgoing[index], 4) for index in range(len(categories))]
    return _multi_series_result(
        metric,
        categories,
        [
            {"name": "流入", "values": incoming},
            {"name": "流出", "values": outgoing},
            {"name": "净流量", "values": net},
        ],
        chart_type=chart_type,
        summary=[
            {"label": "资金流入", "value": round(sum(incoming), 4), "unit": "元"},
            {"label": "资金流出", "value": round(sum(outgoing), 4), "unit": "元"},
            {"label": "净流量", "value": round(sum(net), 4), "unit": "元"},
        ],
        x_axis_label="月份",
        y_axis_label="金额（元）",
    )


def _finance_cash_flow_by_business_type(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.cash_flows:
        if not _date_in_range(row.happened_at, ctx.date_from, ctx.date_to):
            continue
        bucket[row.business_type or "未分类"] += Decimal(row.amount or 0)
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="金额（元）")


def _finance_receivable_payable_monthly(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"receivable": Decimal("0"), "payable": Decimal("0")})
    for row in ctx.receivable_records:
        if not _date_in_range(row.received_at, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(row.received_at)]["receivable"] += Decimal(row.amount or 0)
    for row in ctx.payable_records:
        if not _date_in_range(row.paid_at, ctx.date_from, ctx.date_to):
            continue
        bucket[_month_bucket(row.paid_at)]["payable"] += Decimal(row.amount or 0)
    categories = sorted(bucket.keys())
    receivable_values = [round(_float(bucket[item]["receivable"]), 4) for item in categories]
    payable_values = [round(_float(bucket[item]["payable"]), 4) for item in categories]
    return _multi_series_result(
        metric,
        categories,
        [
            {"name": "回款金额", "values": receivable_values},
            {"name": "付款金额", "values": payable_values},
        ],
        chart_type=chart_type,
        summary=[
            {"label": "累计回款", "value": round(sum(receivable_values), 4), "unit": "元"},
            {"label": "累计付款", "value": round(sum(payable_values), 4), "unit": "元"},
        ],
        x_axis_label="月份",
        y_axis_label="金额（元）",
    )


def _finance_quality_cost_by_type(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    disposition_map = {row.id: row for row in ctx.quality_dispositions}
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.quality_cost_records:
        disposition = disposition_map.get(row.quality_disposition_id)
        if disposition is None or not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        key = "返工成本" if disposition.disposition_type == "rework" else "报废成本" if disposition.disposition_type == "scrap" else disposition.disposition_type
        amount = Decimal(row.recognized_amount or row.estimated_amount or 0)
        bucket[key] += amount
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="成本金额（元）")


def _refund_by_business_type(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in ctx.refunds:
        if not _date_in_range(row.refunded_at, ctx.date_from, ctx.date_to):
            continue
        key = "销售退货退款" if row.sales_return_id else "采购退货退款"
        bucket[key] += Decimal(row.amount or 0)
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, y_axis_label="退款金额（元）")


def _product_fulfillment_mode_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.products:
        key = "贸易型" if row.fulfillment_mode == "trade" else "生产型"
        bucket[key] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "产品总数", "value": sum(row["value"] for row in rows), "unit": "个"}])


def _supplier_catalog_top(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.supplier_catalog_items:
        supplier_name = row.supplier.name if row.supplier else "未命名供应商"
        bucket[supplier_name] += 1
    rows = _rank_rows(bucket, ctx.top_n)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "供应商数", "value": len(bucket), "unit": "家"}])


def _user_role_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.users:
        role_name = row.role.name if row.role else "未分配角色"
        bucket[role_name] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "用户总数", "value": sum(row["value"] for row in rows), "unit": "人"}])


def _user_department_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.users:
        department_name = row.department.name if row.department else "未分配组织"
        bucket[department_name] += 1
    rows = _rank_rows(bucket, ctx.top_n)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "组织数", "value": len(bucket), "unit": "个"}])


def _operation_log_module_distribution(ctx: AnalyticsContext, metric: AnalyticsMetricDefinition, chart_type: str) -> dict:
    bucket: dict[str, int] = defaultdict(int)
    for row in ctx.operation_logs:
        if not _date_in_range(row.created_at, ctx.date_from, ctx.date_to):
            continue
        bucket[BUSINESS_MODULE_LABELS.get(row.module, row.module or "其他")] += 1
    rows = _distribution_rows(bucket)
    return _single_series_result(metric, rows, chart_type=chart_type, summary=[{"label": "日志总量", "value": sum(row["value"] for row in rows), "unit": "次"}])


METRIC_BUILDERS = {
    "sales_order_status_count": _sales_order_status_count,
    "sales_amount_monthly": _sales_amount_monthly,
    "sales_top_customers_amount": _sales_top_customers_amount,
    "sales_top_products_quantity": _sales_top_products_quantity,
    "sales_payment_type_distribution": _sales_payment_type_distribution,
    "sales_receipt_stage_amount": _sales_receipt_stage_amount,
    "sales_return_type_amount": _sales_return_type_amount,
    "sales_cost_profit_monthly": _sales_cost_profit_monthly,
    "purchase_order_status_count": _purchase_order_status_count,
    "purchase_amount_monthly": _purchase_amount_monthly,
    "purchase_top_suppliers_amount": _purchase_top_suppliers_amount,
    "purchase_arrival_status_distribution": _purchase_arrival_status_distribution,
    "purchase_payment_type_distribution": _purchase_payment_type_distribution,
    "inventory_amount_by_warehouse": _inventory_amount_by_warehouse,
    "inventory_item_type_amount": _inventory_item_type_amount,
    "inventory_low_stock_by_warehouse": _inventory_low_stock_by_warehouse,
    "inventory_transaction_type_distribution": _inventory_transaction_type_distribution,
    "stock_take_difference_by_warehouse": _stock_take_difference_by_warehouse,
    "inventory_turnover_rate_monthly": _inventory_turnover_rate_monthly,
    "logistics_module_distribution": _logistics_module_distribution,
    "logistics_delivery_fee_monthly": _logistics_delivery_fee_monthly,
    "production_task_status_distribution": _production_task_status_distribution,
    "production_output_top_products": _production_output_top_products,
    "production_material_usage_top_materials": _production_material_usage_top_materials,
    "production_plan_status_distribution": _production_plan_status_distribution,
    "quality_source_distribution": _quality_source_distribution,
    "quality_result_distribution": _quality_result_distribution,
    "quality_grade_distribution": _quality_grade_distribution,
    "quality_sample_consumption_monthly": _quality_sample_consumption_monthly,
    "finance_cash_flow_monthly": _finance_cash_flow_monthly,
    "finance_cash_flow_by_business_type": _finance_cash_flow_by_business_type,
    "reimbursement_category_monthly": _reimbursement_category_monthly,
    "finance_receivable_payable_monthly": _finance_receivable_payable_monthly,
    "finance_quality_cost_by_type": _finance_quality_cost_by_type,
    "refund_by_business_type": _refund_by_business_type,
    "customer_new_monthly": _customer_new_monthly,
    "product_fulfillment_mode_distribution": _product_fulfillment_mode_distribution,
    "supplier_catalog_top": _supplier_catalog_top,
    "user_role_distribution": _user_role_distribution,
    "user_department_distribution": _user_department_distribution,
    "operation_log_module_distribution": _operation_log_module_distribution,
}


def get_metric_detail(
    db: Session,
    current_user: User,
    *,
    metric_code: str,
    date_from: date | None = None,
    date_to: date | None = None,
    top_n: int = 8,
    chart_type: str | None = None,
) -> dict:
    metric = _metric_or_raise(metric_code, current_user)
    builder = METRIC_BUILDERS.get(metric.code)
    if builder is None:
        raise KeyError("指标尚未实现")
    active_chart_type = chart_type if chart_type in metric.supported_chart_types else metric.default_chart_type
    top_n = max(3, min(int(top_n or 8), 20))
    ctx = AnalyticsContext(db, current_user, date_from=date_from, date_to=date_to, top_n=top_n)
    result = builder(ctx, metric, active_chart_type)
    result["filters"] = {
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "top_n": top_n,
    }
    return result
