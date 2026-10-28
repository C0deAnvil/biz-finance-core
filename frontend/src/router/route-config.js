import routeManifest from '../shared/route-manifest.json'

export const APP_TITLE = '业财一体化系统'

const loaderMap = {
  '/help': () => import('../views/help/SystemGuideView.vue'),
  '/help': () => import('../views/help/SystemGuideView.vue'),
  '/help': () => import('../views/help/SystemGuideView.vue'),
  '/profile': () => import('../views/ProfileView.vue'),
  '/finance/accounts': () => import('../views/finance/AccountsView.vue'),
  '/dashboard': () => import('../views/dashboards/DashboardView.vue'),
  '/flow-board': () => import('../views/dashboards/OrderFlowBoardView.vue'),
  '/approvals': () => import('../views/approvals/ApprovalCenterView.vue'),
  '/customers': () => import('../views/master/CustomersView.vue'),
  '/suppliers': () => import('../views/master/SuppliersView.vue'),
  '/products': () => import('../views/master/ProductsView.vue'),
  '/materials': () => import('../views/master/MaterialsView.vue'),
  '/packaging-configs': () => import('../views/master/PackagingConfigsView.vue'),
  '/standard-prices': () => import('../views/master/StandardPricesView.vue'),
  '/sales/orders': () => import('../views/sales/SalesOrdersView.vue'),
  '/sales/my-receivables': () => import('../views/sales/MyReceivablesView.vue'),
  '/sales/order-timeline': () => import('../views/sales/OrderTimelineView.vue'),
  '/sales/settlements': () => import('../views/sales/SalesSettlementsView.vue'),
  '/sales/returns': () => import('../views/sales/SalesReturnsView.vue'),
  '/sales/approvals': () => import('../views/sales/SalesApprovalsView.vue'),
  '/analytics': () => import('../views/analytics/AnalyticsView.vue'),
  '/customer-ai': () => import('../views/analytics/CustomerAIView.vue'),
  '/supplier-ai': () => import('../views/analytics/SupplierAIView.vue'),
  '/sales/logistics': () => import('../views/logistics/LogisticsView.vue'),
  '/inventory/queues': () => import('../views/inventory/InventoryQueuesView.vue'),
  '/inventory/exceptions': () => import('../views/inventory/InventoryExceptionsView.vue'),
  '/inventory/transfers': () => import('../views/inventory/StockTransfersView.vue'),
  '/inventory/stocks': () => import('../views/inventory/InventoryStocksView.vue'),
  '/inventory/stock-takes': () => import('../views/inventory/StockTakeView.vue'),
  '/inventory/logistics': () => import('../views/logistics/LogisticsView.vue'),
  '/purchases/orders': () => import('../views/purchases/PurchaseOrdersView.vue'),
  '/purchases/settlements': () => import('../views/purchases/PurchaseSettlementsView.vue'),
  '/purchases/receipts': () => import('../views/purchases/PurchaseReceiptsView.vue'),
  '/purchases/returns': () => import('../views/purchases/PurchaseReturnsView.vue'),
  '/purchases/approvals': () => import('../views/purchases/PurchaseApprovalsView.vue'),
  '/purchases/logistics': () => import('../views/logistics/LogisticsView.vue'),
  '/production/boms': () => import('../views/production/BomsView.vue'),
  '/production/plans': () => import('../views/production/ProductionPlansView.vue'),
  '/production/tasks': () => import('../views/production/ProductionTasksView.vue'),
  '/notifications': () => import('../views/NotificationsView.vue'),
  '/production/approvals': () => import('../views/production/ProductionApprovalsView.vue'),
  '/quality/tasks': () => import('../views/quality/QualityTasksView.vue'),
  '/finance/receivables': () => import('../views/finance/ReceivablesView.vue'),
  '/finance/overview': () => import('../views/finance/FinanceOverviewView.vue'),
  '/finance/payables': () => import('../views/finance/PayablesView.vue'),
  '/finance/quality-costs': () => import('../views/finance/QualityCostsView.vue'),
  '/finance/reimbursements': () => import('../views/finance/ReimbursementsView.vue'),
  '/finance/cash-flows': () => import('../views/finance/CashFlowsView.vue'),
  '/finance/reports': () => import('../views/finance/ReportsView.vue'),
  '/admin/org-units': () => import('../views/admin/OrgUnitsView.vue'),
  '/admin/users': () => import('../views/admin/UsersView.vue'),
  '/admin/module-permissions': () => import('../views/admin/ModulePermissionsView.vue'),
  '/admin/print-agents': () => import('../views/admin/PrintAgentsView.vue'),
  '/admin/logs': () => import('../views/admin/LogsView.vue')
}

function normalizeManifestItem(item) {
  return {
    path: item.path,
    name: item.name,
    title: item.title,
    group: item.group || '',
    menuRoles: item.menu_roles || [],
    preloadRoles: item.preload_roles || [],
    warmers: (item.warmers || []).map((warmer) => ({
      url: warmer.url,
      ttlMs: warmer.ttl_ms
    }))
  }
}

export const routeRegistry = routeManifest
  .map((item) => ({ ...normalizeManifestItem(item), loader: loaderMap[item.path] }))
  .filter((item) => item.loader)

export const rolePreloadPaths = routeRegistry.reduce((acc, item) => {
  item.preloadRoles.forEach((roleCode) => {
    if (!acc[roleCode]) {
      acc[roleCode] = []
    }
    acc[roleCode].push(item.path)
  })
  return acc
}, {})

// 隐藏页面:菜单 menu_roles 为空,不进导航菜单,但允许直接访问
const hiddenPagePaths = new Set(
  routeRegistry.filter((item) => !item.menuRoles.length).map((item) => item.path)
)

export { hiddenPagePaths }

export const viewLoaders = {
  '/__layout': () => import('../layouts/MainLayout.vue'),
  '/login': () => import('../views/LoginView.vue'),
  ...Object.fromEntries(routeRegistry.map(({ path, loader }) => [path, loader]))
}

export const routeDataWarmers = Object.fromEntries(routeRegistry.map(({ path, warmers }) => [path, warmers]))

export function buildAppChildRoutes() {
  return routeRegistry.map(({ path, name, title }) => ({
    path: path.slice(1),
    name,
    component: viewLoaders[path],
    meta: { title }
  }))
}
