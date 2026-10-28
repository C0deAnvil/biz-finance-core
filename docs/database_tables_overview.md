# 业财一体化系统数据库表简要说明

## 统计口径

- 当前 `models.py` 定义了 **90 张业务表**。
- 使用 Alembic 部署后，MySQL 通常还会有 1 张框架维护的 `alembic_version` 表，用来记录迁移版本。因此，未残留旧表的标准数据库通常共有 **91 张表**。
- 本文按当前代码模型整理。服务器如果保留了已停用的历史表，实际 `SHOW TABLES` 数量可能更多。
- “写入”包括新增、修改和状态流转；“读取”列出主要使用场景，不表示只有这些代码会查询该表。

## 1. 系统、权限与协同（15 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `departments` | 公司部门、组织层级 | 管理员维护组织架构 | 用户归属、数据范围、负责人筛选 |
| `roles` | 销售、财务、库管等角色 | 管理员维护角色 | 登录鉴权、菜单和操作权限判断 |
| `users` | 用户账号、姓名、部门、角色、启停状态 | 新增用户、编辑用户、调整岗位 | 登录、单据提交人/负责人/审批人显示 |
| `user_module_permissions` | 用户对模块和页面的个别权限 | 模块权限分配页面保存权限 | 菜单展示、页面访问和按钮权限校验 |
| `user_sessions` | 登录会话及有效期 | 登录、刷新令牌、退出登录 | 每次请求身份认证、会话失效检查 |
| `sequence_counters` | 按日期和业务类型维护单号流水 | 新建订单、任务、申请时递增 | 生成销售单号、采购单号、补货单号等 |
| `app_settings` | 系统级配置项 | 管理员修改阈值、流程参数等 | 建单、审批、打印等流程读取配置 |
| `todo_items` | 用户待办任务 | 单据提交、审批、质检、出入库等流程生成 | 首页待办、审批中心、各业务工作台 |
| `notifications` | 通知正文和通知类型 | 流程状态变化、审批结果、异常提醒 | 通知中心和弹窗提醒 |
| `notification_recipients` | 通知与接收用户的对应关系、已读状态 | 创建通知时为接收人生成记录 | 查询个人通知、标记已读 |
| `operation_logs` | 用户操作审计记录 | 新增、修改、审批、作废、出入库等关键操作 | 系统日志、问题追踪、责任追溯 |
| `attachments` | 合同、发票、回单、照片、质检附件等文件信息 | 上传或绑定业务附件 | 单据详情、质检结果、发货证明下载预览 |
| `document_status_snaps-hots` | 单据当前流程状态快照 | 订单和下游单据状态变化时同步 | 订单时间轴、流程看板快速读取当前状态 |
| `document_status_history` | 单据历史状态变更轨迹 | 每次状态变化时追加 | 状态事件明细、订单生命周期追溯 |
| `document_relations` | 销售、补货、采购、生产、质检等单据关联 | 生成下游单据或建立来源关系时写入 | 全链时间轴、关联单据跳转、流程追踪 |

## 2. 客户、供应商与主数据（17 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `customers` | 客户基本信息、联系人、地址、等级等 | 客户维护；销售单选择保存新地址 | 销售建单、客户分析、对账和应收查询 |
| `customer_tags` | 客户标签 | 客户维护、客户分类 | 客户筛选、营销和分析 |
| `customer_follow_ups` | 客户跟进记录和下次跟进时间 | 销售记录电话、邮件、拜访等 | 客户详情、销售跟进计划 |
| `customer_transfer_records` | 客户负责人转移记录 | 客户移交给其他销售人员 | 客户归属追溯、权限范围判断 |
| `customer_complaints` | 客诉、处理状态和结果 | 登记客户不满意、质量问题 | 客户详情、质量改进、经营分析 |
| `customer_inquiries` | 客户询盘和需求信息 | 登记询盘、AI/人工分析客户需求 | 客户智能分析、销售跟进 |
| `customer_ai_results` | 客户风险、价值等分析结果 | 执行客户智能分析时生成或更新 | 客户智能分析页面和经营决策 |
| `suppliers` | 供应商基本信息、联系人、账户等 | 供应商维护 | 采购建单、付款、供应商分析 |
| `supplier_tags` | 供应商标签 | 供应商维护和分类 | 供应商筛选、采购决策 |
| `supplier_ai_results` | 供应商评级和风险分析结果 | 运行供应商智能分析 | 采购选供应商、风险提示 |
| `supplier_catalog_items` | 供应商可提供的产品、原料、包装及报价关系 | 维护供应目录、采购记录回写价格 | 供应矩阵、采购建单筛选供应商和最近供价 |
| `supplier_samples` | 供应商样品、收样、质检和结果关联 | 申请样品、库管收样、质检完成 | 供应矩阵、供应商详情、样品质检结果 |
| `warehouses` | 仓库信息、类型、负责人范围 | 管理员维护仓库 | 库存、采购目标仓、生产领料、出入库权限 |
| `products` | 产品基本信息、产品规格、生产/贸易类型等 | 产品基本信息维护 | 销售、采购、生产、BOM、库存查询 |
| `materials` | 原料基本信息、单位、安全库存等 | 原料基本信息维护 | BOM、采购、生产领料、库存查询 |
| `packaging_configs` | 包装基本信息和包装规格 | 包装基本信息维护 | 销售包装选择、采购包装、包装库存 |
| `standard_price_change_logs` | 产品标准价格修改记录 | 财务或授权人员修改标准价 | 改价追溯、销售价格比较、审计 |

## 3. 库存、物流、打印与扫码（20 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `inventories` | 各仓库每种产品、原料、包装的数量、占用量和移动平均成本 | 采购/生产入库、销售/领料出库、移库、盘点调整 | 库存状态、置货判断、补货计算、成本和利润计算 |
| `inventory_batches` | 库存批次、批次数量和质检信息 | 入库时生成或更新批次 | 批次追溯、先进先出、质量查询 |
| `inventory_transactions` | 每次正式出入库的库存流水 | 完成采购入库、销售出库、领料、移库、调整时追加 | 库存出入库记录、审计、成本追溯 |
| `inventory_cost_adjustments` | 库存成本初始化和人工调整记录 | 初始化成本或授权调整移动平均成本 | 成本审计、利润核算依据 |
| `inventory_operation_requests` | 待处理的入库/出库作业请求及处理结果 | 采购到货、生产入库、销售发货、移库等生成请求 | 库存作业看板、库管审批和执行 |
| `inventory_exception_records` | 报损、破损、额外包装等库存异常主单 | 库管登记异常并提交审批 | 经理/董事长审批、异常记录查询 |
| `inventory_exception_items` | 库存异常涉及的具体物品和数量 | 新增库存异常明细 | 异常审批详情、审批后生成库存流水 |
| `inventory_dispatch_queues` | 销售订单置货和发货队列状态 | 销售单提交生成；置货、发货时更新 | 待置货、待发货列表和扫码条件判断 |
| `stock_transfers` | 仓库间库存转移申请及出库、质检、入库状态 | 发起移库，源仓出库，目的仓质检/入库 | 库存转移页面、库存作业请求 |
| `logistics_records` | 发货物流、承运和运单信息 | 销售发货或物流登记 | 物流跟踪、客户订单详情 |
| `stock_take_tasks` | 库存盘点任务 | 库管发起盘点 | 盘点页面、盘点状态查询 |
| `stock_take_items` | 盘点任务中的账面数、实盘数和差异 | 创建/执行盘点时写入 | 盘点差异、库存调整依据 |
| `placement_print_agents` | 本地自动打印客户端、认证码和状态 | 管理员创建设备；客户端心跳更新 | 打印客户端认证和设备管理 |
| `placement_print_jobs` | 置货单打印任务、领取和打印状态 | 新置货需求或手动打印时生成；客户端回报结果 | 自动打印队列、漏打补打、打印状态 |
| `placement_print_access_logs` | 打印接口调用时间、IP、设备等 | 打印客户端每次访问接口时记录 | 安全审计和认证码泄露排查 |
| `warehouse_scan_devices` | 仓库扫码设备及认证信息 | 管理员创建设备、更新设备状态 | 扫码客户端认证、设备管理 |
| `warehouse_scan_access_logs` | 扫码接口访问日志 | 客户端调用扫码接口时记录 | 安全审计、故障排查 |
| `warehouse_scan_events` | 每次置货码、发货码扫描及处理结果 | 扫码置货、扫码发货时追加 | 扫码历史、重复扫码判断、错误原因查询 |
| `shipment_proofs` | 发货照片和证明文件关联 | 扫码发货或人工发货时上传 | 发货记录、订单详情、争议追溯 |
| `sales_shipment_batches` | 销售订单发货批次和数量 | 完成发货时生成批次 | 发货历史、订单已发/未发数量统计 |

## 4. 销售与补货（9 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `sales_orders` | 客户订单主信息、金额、收款方式、状态、负责人等 | 保存草稿、提交、收款确认、发货、完结 | 销售订单、财务确认、置货、应收、全链时间轴 |
| `sales_order_items` | 销售订单产品、规格、数量、价格、置货/发货数量 | 新增或编辑销售订单明细；置货发货时更新数量 | 订单详情、库存占用、补货计算、打印单据 |
| `sales_order_approvals` | 客户订单收款确认和驳回记录 | 财务确认收款、转应收或驳回 | 审批历史、时间轴、责任追溯 |
| `sales_order_price_change_logs` | 低于标准价的订单价格及改价说明 | 销售提交低价订单；收款确认时同步审批 | 收款确认弹窗、改价历史、审计 |
| `sales_returns` | 销售退货/换货主单和审批状态 | 销售提交退换货，财务/经理审批，质检/入库推进 | 退换货页面、审批中心、库存作业 |
| `sales_return_items` | 退换货产品及数量 | 新建退换货明细 | 退换货详情、质检和入库数量 |
| `sales_receipt_requests` | 历史销售收款确认协同记录 | 旧收款流程或兼容流程写入 | 历史收款协同和流程修复；新流程主要使用订单收款确认 |
| `fulfillment_demands` | 成品补货需求、缺口、已补量和处理状态 | 库管确认产品短缺后提交，或库管/生产手动提需求 | 采购/生产补货列表、进度计算、订单时间轴 |
| `material_replenishment_demands` | 生产人员确认提交的原料采购批次 | 生产人员根据生产需求提交原料缺口 | 待采购补货需求、采购订单来源、原料补货进度 |

## 5. 采购（8 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `purchase_requests` | 生产/质检自主采购需求 | 生产或质检人员提交独立采购需求；采购处理 | 自主采购需求列表、转建采购单 |
| `purchase_orders` | 采购订单主信息、供应商、目标仓、预计到货时间和状态 | 新建/编辑采购单、确认采购、到货和结算状态更新 | 采购订单、采购到货、付款申请、销售时间轴 |
| `purchase_order_items` | 采购产品/原料/包装、数量、单价、已到货量 | 采购建单；到货、退货时更新 | 采购详情、到货、库存成本、应付金额 |
| `purchase_order_approvals` | 采购订单审批记录 | 超过阈值或规定流程审批采购单 | 审批中心、采购订单历史 |
| `purchase_receipts` | 采购到货登记主单 | 库管确认采购到货 | 到货列表、质检任务、入库作业 |
| `purchase_receipt_items` | 每次到货的物品和数量 | 登记采购到货明细 | 到货详情、质检、入库数量 |
| `purchase_returns` | 采购退货记录 | 采购或库管发起供应商退货 | 采购退货列表、库存出库、供应商对账 |
| `purchase_payment_requests` | 采购付款申请、指定财务、审批和出账信息 | 采购提交付款申请，财务/董事长处理 | 应付管理、审批中心、付款台账 |

## 6. 生产（5 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `boms` | 产品的 BOM 主表 | 新增或修改生产配方 | 生产任务、原料需求和成本计算 |
| `bom_items` | BOM 中每种原料及标准用量 | 编辑 BOM 明细 | 领料计划、原料补货、生产成本 |
| `production_tasks` | 生产任务、目标数量、仓库、状态和负责人 | 计划转任务、手工建任务、生产过程更新 | 生产任务页面、质检、入库、订单时间轴 |
| `production_material_usages` | 生产任务计划领料量和实际领料量 | 创建任务生成用料；领料出库更新实际量 | 领料审批、原料缺口、生产成本 |
| `production_plans` | 生产计划及转任务状态 | 生产人员新建计划、转生产任务 | 生产计划页面和计划执行跟踪 |

## 7. 质检（5 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `quality_tasks` | 采购、生产、退货、移库、样品等质检任务 | 到货/完工/退货/收样等流程按条件生成 | 质检任务列表、质检员待办 |
| `quality_inspections` | 检验项目、结果、结论和附件关联 | 质检员提交检验结果 | 质检详情、入库放行、供应商质量评价 |
| `quality_inbound_applications` | 质检合格后的入库申请 | 质检完成并申请入库 | 库管入库作业、生产/采购流程状态 |
| `quality_dispositions` | 不合格品处置决定 | 质检不合格后选择返工、退货、报废等 | 后续库存、采购退货和生产返工流程 |
| `quality_cost_records` | 质量损失和质量成本记录 | 质检、客诉或异常处理登记成本 | 质量成本分析和财务统计 |

## 8. 财务（10 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `financial_accounts` | 企业收付款账户、币种和余额等 | 财务维护账户；业务选择收付款账户 | 销售收款、采购付款、资金流水、账户余额 |
| `financial_account_transactions` | 企业账户余额变动明细 | 收款、付款、退款、报销等入账时追加 | 账户流水、余额核对、审计 |
| `receivable_plans` | 应收计划及计划金额 | 订单转应收、定金单生成尾款应收 | 待回款订单、应收金额和完成状态 |
| `receivable_records` | 实际客户回款记录 | 收款确认或财务登记后续回款 | 已收金额、应收余额、回款历史 |
| `payable_records` | 已形成的企业应付款 | 采购付款申请进入财务流程时生成或更新 | 应付管理、付款统计和对账 |
| `payment_method_options` | 历史付款方式配置 | 财务维护付款方式 | 旧付款表单兼容；当前企业账户是主要付款信息 |
| `reimbursements` | 报销申请、审批、付款凭证 | 员工提交报销，审批和财务出账 | 报销管理、审批中心、财务统计 |
| `business_matters` | 通用事务申请及财务审批信息 | 员工提交事务申请、负责人审批 | 审批中心、费用和事务跟踪 |
| `refund_records` | 销售退款、采购退款等退款记录 | 退货退款或差额退款时登记 | 退款台账、资金流水、订单结算 |
| `cash_flow_records` | 企业收入、支出等现金流业务记录 | 收款、付款、报销、退款等业务入账 | 资金流水页面、财务统计和决策 |

## 9. 报表（1 张）

| 表名 | 存储内容 | 主要写入场景 | 主要读取场景 |
| --- | --- | --- | --- |
| `report_snapshots` | 某一时间点的报表快照和汇总结果 | 生成或保存经营、财务报表时写入 | 历史报表查看、跨期比较 |

## 流程与主要表的关系速览

| 业务流程 | 主要写入表 | 主要读取表 |
| --- | --- | --- |
| 销售提交 → 收款确认 | `sales_orders`、`sales_order_items`、`sales_order_price_change_logs`、`sales_order_approvals` | `customers`、`products`、`standard_price_change_logs`、`financial_accounts` |
| 置货 → 补货 → 发货 | `inventory_dispatch_queues`、`fulfillment_demands`、`inventory_operation_requests`、`inventory_transactions`、`sales_shipment_batches` | `inventories`、`sales_order_items`、`receivable_plans`、`receivable_records` |
| 采购 → 到货 → 质检 → 入库 | `purchase_orders`、`purchase_order_items`、`purchase_receipts`、`quality_tasks`、`inventory_operation_requests` | `supplier_catalog_items`、`fulfillment_demands`、`inventories`、`financial_accounts` |
| 生产 → 领料 → 质检 → 入库 | `production_tasks`、`production_material_usages`、`quality_tasks`、`inventory_operation_requests` | `boms`、`bom_items`、`inventories`、`material_replenishment_demands` |
| 收款、应收与回款 | `sales_order_approvals`、`receivable_plans`、`receivable_records`、`financial_account_transactions`、`cash_flow_records` | `sales_orders`、`financial_accounts`、`receivable_plans` |
| 付款与应付 | `purchase_payment_requests`、`payable_records`、`financial_account_transactions`、`cash_flow_records` | `purchase_orders`、`financial_accounts`、`payable_records` |
| 库存移动平均成本 | `inventories`、`inventory_transactions`、`inventory_cost_adjustments` | `purchase_order_items`、`production_material_usages`、`inventory_transactions` |

