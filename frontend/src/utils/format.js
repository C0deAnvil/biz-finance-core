const STATUS_LABELS = {
  pending_material_outbound: '\u5f85\u9886\u6599\u51fa\u5e93',
  draft: '草稿',
  pending: '待处理',
  unrequested: '未申请拨款',
  pending_confirmation: '待确认',
  pending_price_approval: '待财务改价审批',
  pending_approval: '待审批',
  pending_purchase_confirm: '待采购确认',
  pending_shipment: '待发货',
  pending_material: '待领料',
  pending_material_approval: '待领料审批',
  pending_special_approval: '待特殊领用审批',
  pending_disposition: '待异常处理',
  pending_manager_approval: '待经理审批',
  pending_finance_approval: '待财务审批',
  pending_chairman_approval: '待董事长确认',
  pending_finance_check: '待财务核对',
  pending_finance_payment: '待财务出账',
  pending_completion: '待业务确认完成',
  pending_inbound: '待库管入库',
  receipt_abnormal: '收款异常',
  payment_pending: '待付款申请',
  receipt_pending: '待收款确认',
  payment_requested: '已提交付款申请',
  receipt_requested: '已提交收款确认',
  unpaid: '待付款',
  partial_settled: '部分结算',
  partially_completed: '部分完成',
  purchasing: '采购中',
  partial_received: '部分到货',
  fully_received: '全部到货',
  received: '已到货',
  inspecting: '质检中',
  waiting_quality: '待质检',
  ready_for_inbound: '待准许入库',
  inbound_pending: '待入库审批',
  submitted: '已提交',
  approved: '已通过',
  recognized: '已确认',
  completed: '已完成',
  material_issued: '已领料',
  rejected: '已驳回',
  cancelled: '已取消',
  shipped: '已发货',
  paid: '已收款',
  refunded: '已退款',
  returned: '已退货',
  settled: '已结算',
  rework_created: '已发起返工',
  rework_pending: '待返工',
  reworked: '返工完成',
  scrapped: '已报废',
  partially_scrapped: '部分报废',
  placed: '已置货',
  hidden: '已隐藏',
  waiting_approval: '待审批',
  not_required: '无需审批',
  enabled: '启用',
  disabled: '停用'
}

const PAYMENT_TYPE_LABELS = {
  advance: '先付定金后付尾款',
  cash: '全款发货',
  full_payment: '全款发货',
  installment: '货到付款/分期付款',
  bank_transfer: '银行转账',
  deposit: '先付定金后付尾款',
  deposit_then_balance: '先付定金后付尾款',
  prepayment: '预付款',
  先付定金后付尾款: '先付定金后付尾款',
  全款发货: '全款发货',
  '货到付款/分期付款': '货到付款/分期付款',
  银行转账: '银行转账',
  预付款: '预付款',
  先到货后付款: '先到货后付款',
  先付款后到货: '先付款后到货'
}

const ACTION_LABELS = {
  create_order: '创建销售订单',
  update_order: '更新销售订单',
  delete_order: '删除销售订单',
  approve_order: '审批销售订单',
  complete_order: '完成销售订单',
  create_return: '创建销售退换货',
  approve_return: '审批销售退换货',
  create_purchase_order: '创建采购订单',
  update_purchase_order: '更新采购订单',
  cancel_purchase_order: '取消采购订单',
  approve_purchase_order: '审批采购订单',
  create_purchase_return: '创建采购退货',
  submit_special_request: '提交特殊领用申请',
  submit_material_request: '提交领料申请',
  approve_special_request: '审批特殊领用申请',
  request_purchase_fund: '申请采购拨款',
  approve_purchase_fund: '审批采购拨款',
  settle_purchase_fund: '完成采购结算',
  collect_order_payment: '登记客户回款',
  submit_purchase_payment_request: '提交采购付款申请',
  approve_purchase_payment_request: '审批采购付款申请',
  complete_purchase_payment_request: '完成采购付款结算',
  submit_sales_receipt_request: '提交销售收款确认',
  approve_sales_receipt_request: '确认销售收款',
  create_inventory_operation_request: '创建库存作业请求',
  create_fulfillment_demand: '创建履约补货需求',
  complete_sales_order: '完成客户订单',
  complete_task: '完成生产任务',
  cancel_production_task: '取消生产任务',
  create_rework_task: '创建返工任务',
  create_scrap_record: '登记报废',
  save_stock_take_items: '保存盘点明细',
  approve_stock_take: '完成盘点入账',
  recognize_quality_cost: '确认质量成本',
  allow_quality_inbound: '质检准许入库',
  create_inbound_application: '提交质检入库申请',
  approve_inbound_application: '审批质检入库申请',
  complete_production_inbound: '完成生产入库',
  create_task: '创建质检任务'
}

const RETURN_TYPE_LABELS = {
  return: '退货',
  exchange: '换货'
}

const TRANSACTION_TYPE_LABELS = {
  purchase_inbound: '采购入库',
  purchase_return_outbound: '采购退货出库',
  sales_outbound: '销售出库',
  sales_return_inbound: '销售退货入库',
  production_material_outbound: '生产领料出库',
  production_material_return_inbound: '生产退料入库',
  quality_inbound: '质检入库',
  manual_inbound: '手工入库',
  manual_outbound: '手工出库',
  stock_take_adjustment: '盘点调整'
}

const PAYMENT_STAGE_LABELS = {
  deposit: '定金',
  balance: '尾款',
  cod: '到货后全款',
  cod_full: '到货后全款',
  full: '发货前全款',
  pre_shipment_full: '发货前全款',
  installment: '分期款',
  mixed: '合并收款',
  direct: '直接登记'
}

const INVENTORY_REQUEST_CATEGORY_LABELS = {
  manual_inbound_request: '手工入库',
  manual_outbound_request: '手工出库',
  production_material_issue: '生产领料出库',
  production_finished_inbound: '生产完工入库申请',
  production_quality_inbound: '生产质检通过入库申请',
  purchase_receipt_inbound: '采购到货入库申请',
  purchase_quality_inbound: '采购质检通过入库申请',
  purchase_return_outbound: '退货处理出库',
  sales_shipment: '客户订单发货出库',
  sales_return_inbound: '销售退货回库'
}

function normalizeDateValue(value) {
  if (!value) return null
  if (value instanceof Date) return value
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return null
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(trimmed)) {
      return new Date(trimmed)
    }
    return new Date(trimmed)
  }
  return new Date(value)
}

export function formatNumber(value) {
  if (value == null || value === '') return '-'
  const number = Number(value)
  if (Number.isNaN(number)) return value
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

export function formatMoney(value) {
  if (value == null || value === '') return '-'
  // Computed UI totals are JS numbers and may contain binary floating-point tails.
  // Decimal strings from the API are kept intact instead of passing through Number.
  const text = typeof value === 'number' ? value.toFixed(4) : String(value).trim()
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) return value
  const negative = text.startsWith('-') ? '-' : ''
  const [integer, fraction = ''] = text.replace(/^-/, '').split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const decimals = fraction.length > 4 ? fraction : fraction.padEnd(3, '0')
  return `${negative}${grouped}.${decimals}`
}

export function formatDateTime(value) {
  const date = normalizeDateValue(value)
  if (!date || Number.isNaN(date.getTime())) return value || '-'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(date)
}

export function formatDate(value) {
  const date = normalizeDateValue(value)
  if (!date || Number.isNaN(date.getTime())) return value || '-'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(date)
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || '-'
}

export function paymentTypeLabel(paymentType) {
  if (paymentType == null || paymentType === '') return '-'
  const raw = String(paymentType).trim()
  const normalized = raw.toLowerCase()
  if (PAYMENT_TYPE_LABELS[raw]) return PAYMENT_TYPE_LABELS[raw]
  if (PAYMENT_TYPE_LABELS[normalized]) return PAYMENT_TYPE_LABELS[normalized]
  if (normalized.includes('install')) return PAYMENT_TYPE_LABELS.installment
  if (normalized.includes('cash')) return PAYMENT_TYPE_LABELS.cash
  if (normalized.includes('bank')) return PAYMENT_TYPE_LABELS.bank_transfer
  return raw
}

export function actionLabel(action) {
  return ACTION_LABELS[action] || action || '-'
}

export function returnTypeLabel(value) {
  return RETURN_TYPE_LABELS[value] || value || '退货'
}

export function transactionTypeLabel(value) {
  return TRANSACTION_TYPE_LABELS[value] || value || '-'
}

export function paymentStageLabel(value) {
  return PAYMENT_STAGE_LABELS[value] || value || '-'
}

export function inventoryRequestCategoryLabel(value) {
  return INVENTORY_REQUEST_CATEGORY_LABELS[value] || value || '-'
}

export function formatRemark(value) {
  if (value == null || value === '') return '-'
  const text = String(value).trim()
  const roundMatch = text.match(/^round(\d+)-(receive|pay|refund)-/i)
  if (roundMatch) {
    const [, round, type] = roundMatch
    if (type === 'receive') return `第 ${round} 期回款`
    if (type === 'pay') return `第 ${round} 期采购付款`
    if (type === 'refund') return `第 ${round} 期退款`
  }
  if (text.startsWith('finance-recognize')) return '财务确认质量成本'
  if (text.startsWith('finance-scrap')) return '财务确认报废成本'
  if (text === 'finance') return '财务处理'
  return text
}

export function statusType(status) {
  const map = {
    pending_material_outbound: 'primary',
    pending: 'warning',
    pending_confirmation: 'warning',
    pending_price_approval: 'warning',
    pending_approval: 'warning',
    pending_purchase_confirm: 'warning',
    pending_shipment: 'primary',
    pending_material: 'warning',
    pending_special_approval: 'warning',
    pending_chairman_approval: 'warning',
    pending_finance_check: 'warning',
    pending_finance_payment: 'warning',
    pending_completion: 'warning',
    pending_inbound: 'warning',
    receipt_abnormal: 'danger',
    payment_pending: 'warning',
    receipt_pending: 'warning',
    payment_requested: 'primary',
    receipt_requested: 'primary',
    pending_disposition: 'danger',
    unpaid: 'warning',
    partial_settled: 'warning',
    purchasing: 'primary',
    partial_received: 'primary',
    fully_received: 'success',
    received: 'success',
    inspecting: 'primary',
    waiting_quality: 'primary',
    ready_for_inbound: 'success',
    inbound_pending: 'primary',
    completed: 'success',
    approved: 'success',
    recognized: 'success',
    shipped: 'success',
    refunded: 'success',
    rework_created: 'warning',
    rework_pending: 'warning',
    rejected: 'danger',
    scrapped: 'danger',
    partially_scrapped: 'warning',
    cancelled: 'info',
    returned: 'warning',
    not_required: 'info',
    enabled: 'success',
    disabled: 'info'
  }
  return map[status] || 'info'
}
