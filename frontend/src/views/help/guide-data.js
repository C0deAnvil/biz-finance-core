export const roleGuides = [
  {
    key: 'sales', icon: '📝', name: '销售人员', color: '#17a673',
    summary: '从客户资料、报价和订单草稿开始，负责提交订单、跟踪审批、置货、发货和应收。',
    startPath: '/sales/orders', startLabel: '打开客户订单',
    checklist: [
      '先维护客户、产品、收货地址和收款方式，再保存销售订单草稿。',
      '草稿不会生成审批、置货或应收；只有点击“提交”才启动后续流程。',
      '在客户订单中查看审核与履约状态，在“订单全链时间轴”查看每个环节。',
      '被转为应收的订单，在“我的应收款”查看后续回款情况。',
      '发起退换货时填写明细并选择是否质检，审批后由质检和仓库继续处理。'
    ],
    pages: [['客户基本信息', '/customers'], ['产品基本信息', '/products'], ['客户订单', '/sales/orders'], ['我的应收款', '/sales/my-receivables'], ['销售退换货', '/sales/returns'], ['订单全链看板', '/flow-board']]
  },
  {
    key: 'purchase', icon: '🛒', name: '采购人员', color: '#e99b2f',
    summary: '承接仓库补货和生产/质检自主采购需求，完成采购下单、付款申请、到货与退货。',
    startPath: '/purchases/orders', startLabel: '打开采购订单',
    checklist: [
      '在采购订单页面查看待采购补货需求和生产/质检自主采购需求。',
      '转为采购订单后补充供应商、价格、目标仓库和预计到货时间。',
      '需要付款时提交采购付款申请；指定财务后只由指定人员处理。',
      '货物到达后登记采购到货；需要质检则先进入质检任务。',
      '质检放行后生成目标仓库的入库作业请求。'
    ],
    pages: [['供应商基本信息', '/suppliers'], ['供应商智能分析', '/supplier-ai'], ['采购订单', '/purchases/orders'], ['采购付款申请', '/purchases/settlements'], ['采购到货', '/purchases/receipts'], ['退货处理', '/purchases/returns']]
  },
  {
    key: 'inventory', icon: '📦', name: '仓库人员', color: '#2c80c5',
    summary: '处理置货、发货、入库、出库、库存转移、盘点、异常和补货需求。',
    startPath: '/inventory/queues', startLabel: '打开库存作业看板',
    checklist: [
      '先看库存作业看板：财务未通过时，置货按钮会优先提示财务条件。',
      '库存不足时由库管提交补货需求；销售订单不能绕过库管自动采购。',
      '库存作业请求统一处理采购入库、生产入库、领料出库、退货和移库。',
      '库存转移先由源仓出库，再按是否质检进入质检或目标仓入库。',
      '置货单可自动打印；扫码默认执行整单置货或整单发货。'
    ],
    pages: [['库存作业看板', '/inventory/queues'], ['库存状态', '/inventory/stocks'], ['库存转移', '/inventory/transfers'], ['库存盘点', '/inventory/stock-takes'], ['库存异常', '/inventory/exceptions'], ['库存物流', '/inventory/logistics']]
  },
  {
    key: 'production', icon: '🏗️', name: '生产人员', color: '#d85c4a',
    summary: '维护配方和生产计划，处理补货任务、领料、生产、质检与成品入库。',
    startPath: '/production/tasks', startLabel: '打开生产任务',
    checklist: [
      '生产前维护 BOM：成品、原料耗用、包装和版本必须准确。',
      '生产计划可转为生产任务，也可以直接新建生产任务。',
      '目标仓库和领料仓库默认工厂仓库，领料审批只到对应仓库库管。',
      '原料不足时可提交采购需求；也可以创建不依赖销售订单的自主需求。',
      '领料出库后组织生产，完工后按流程质检并申请成品入库。'
    ],
    pages: [['配方管理', '/production/boms'], ['生产计划', '/production/plans'], ['生产任务', '/production/tasks'], ['库存状态', '/inventory/stocks'], ['质检任务', '/quality/tasks']]
  },
  {
    key: 'quality', icon: '🔬', name: '质检人员', color: '#7b68b4',
    summary: '处理采购来料、生产完工、库存转移和供应商样品的检验与放行。',
    startPath: '/quality/tasks', startLabel: '打开质检任务',
    checklist: [
      '从质检任务查看任务来源、物品、仓库和关联单据。',
      '记录检验结果、结论和附件；不合格时说明原因。',
      '采购、生产和移库质检合格后，系统生成对应仓库的入库请求。',
      '供应商样品质检结果会回写供应商供货矩阵，支持后续选商。',
      '质检人员也可以提交原料、产品或其他耗材的自主采购需求。'
    ],
    pages: [['质检任务', '/quality/tasks'], ['供应商基本信息', '/suppliers'], ['采购订单', '/purchases/orders'], ['库存转移', '/inventory/transfers']]
  },
  {
    key: 'finance', icon: '💰', name: '财务人员', color: '#a76f2b',
    summary: '审核客户订单并确认收款，管理应收、采购付款、企业账户与资金流水。',
    startPath: '/sales/approvals', startLabel: '打开客户订单收款确认',
    checklist: [
      '客户订单提交后先进入客户订单收款确认，不应直接生成应收。',
      '全款订单通过表示已收款；定金订单通过后尾款进入应收。',
      '应收发货或“转为应收”会放行后续流程并生成应收记录。',
      '采购付款申请按指定财务分派；未指定时所有财务可处理。',
      '登记收付款时选择企业账户，资金流水与企业账户余额同步。'
    ],
    pages: [['客户订单收款确认', '/sales/approvals'], ['应收管理', '/finance/receivables'], ['应付管理', '/finance/payables'], ['收付款账户', '/finance/accounts'], ['资金流水', '/finance/cash-flows'], ['报销管理', '/finance/reimbursements']]
  },
  {
    key: 'manager', icon: '✅', name: '经理 / 董事长', color: '#7653ba',
    summary: '通过审批中心处理跨部门审批，并用全链看板和经营分析掌握风险与进度。',
    startPath: '/approvals', startLabel: '打开审批中心',
    checklist: [
      '审批中心只显示正式提交的单据；草稿不应生成审批。',
      '先看审批类型、订单编号、负责人、金额和备注，再进入详情。',
      '销售退货、库存异常等流程中，规定人员任意一人同意即可继续。',
      '订单全链看板用于定位卡点，状态事件明细用于追溯操作人和时间。',
      '统计分析用于经营汇总，具体单据事实仍以业务详情为准。'
    ],
    pages: [['审批中心', '/approvals'], ['订单全链看板', '/flow-board'], ['统计分析', '/analytics'], ['客户智能分析', '/customer-ai'], ['供应商智能分析', '/supplier-ai']]
  },
  {
    key: 'admin', icon: '⚙️', name: '系统管理员', color: '#536170',
    summary: '维护组织、账号、权限、打印设备和审计日志，保障人员只能看到有权处理的数据。',
    startPath: '/admin/users', startLabel: '打开用户管理',
    checklist: [
      '先建立部门、小组和账号，再分配岗位角色与数据范围。',
      '模块权限控制菜单入口；数据范围控制本人、小组、部门或全部数据。',
      '打印设备认证码只显示一次，泄露或设备丢失后立即停用。',
      '通过系统日志排查操作记录，通过设备记录排查自动打印调用。',
      '不要共享个人账号；岗位变化后及时调整权限。'
    ],
    pages: [['组织架构', '/admin/org-units'], ['用户管理', '/admin/users'], ['模块权限分配', '/admin/module-permissions'], ['打印设备与记录', '/admin/print-agents'], ['系统日志', '/admin/logs']]
  }
]

export const workflows = [
  {
    key: 'sales', name: '销售订单主链', color: '#17a673', icon: '🧾',
    summary: '从销售正式提交开始，同时启动财务审核和置货准备，最终完成发货与回款。',
    nodes: [
      { title: '保存草稿', owner: '销售', status: '草稿', page: '/sales/orders', body: '可以反复编辑，不校验完整提交规则，也不会生成审批、置货或应收。', tip: '离开前先保存草稿，避免录入内容丢失。' },
      { title: '正式提交', owner: '销售', status: '已提交 / 待审批', page: '/sales/orders', body: '校验客户、明细、收款账户等必填项，并创建后续任务。低于标准价格时先进入改价审批。', tip: '提交以后才算业务流程正式开始。' },
      { title: '财务审核', owner: '财务', status: '待财务审核', page: '/sales/approvals', body: '核对订单金额、收款方式、收款账户和实际收款情况；可通过、驳回或转为应收。', tip: '指定财务时仅指定人员处理；未指定则所有财务可处理。' },
      { title: '置货准备', owner: '仓库', status: '待置货', page: '/inventory/queues', body: '订单提交后生成置货需求，但财务条件未满足时不能完成置货。库存不足由库管提交补货。', tip: '按钮灰色时先看财务提示，再看库存和包装提示。' },
      { title: '采购 / 生产补货', owner: '采购 / 生产', status: '补货中', page: '/flow-board', body: '根据产品类型和缺口形成采购或生产任务，入库完成后回到原置货需求。', tip: '补货需求必须由库管提交。' },
      { title: '完成置货', owner: '仓库', status: '已置货 / 待发货', page: '/inventory/queues', body: '核对商品、包装和数量后完成整单置货；可按钮操作或扫描置货码。', tip: '扫码默认全量置货，不支持分批数量输入。' },
      { title: '发货完结', owner: '仓库 / 销售', status: '已发货 / 已完成', page: '/inventory/queues', body: '上传或绑定发货凭证后完成发货。应收订单继续在应收管理中跟踪，收清后应收完结。', tip: '发货完成不一定代表应收已经收清。' }
    ]
  },
  {
    key: 'purchase', name: '采购与到货', color: '#e99b2f', icon: '🛒',
    summary: '需求转订单后完成付款、到货、可选质检和仓库入库。',
    nodes: [
      { title: '采购需求', owner: '库管 / 生产 / 质检', status: '待采购', page: '/purchases/orders', body: '来源可以是销售缺货补货，也可以是生产、质检自主采购需求。', tip: '需求的提交人和来源单据会被保留。' },
      { title: '采购订单', owner: '采购', status: '采购中', page: '/purchases/orders', body: '映射需求物品和数量，补充供应商、价格、目标仓库、预计到货时间。', tip: '默认采购方式为先付款后到货。' },
      { title: '付款申请', owner: '采购 → 财务', status: '待财务出账', page: '/purchases/settlements', body: '采购提交付款申请，财务选择企业付款账户并登记实际出账。', tip: '指定财务优先由指定人员处理。' },
      { title: '到货登记', owner: '采购 / 仓库', status: '已到货', page: '/purchases/receipts', body: '登记本次到货数量、批次和到货信息，可分批到货。', tip: '预计到货时间会显示在关联销售订单时间轴。' },
      { title: '质检（可选）', owner: '质检', status: '待质检', page: '/quality/tasks', body: '需要质检时先生成质检任务；不需要则直接准备入库。', tip: '质检不通过时不能生成正常入库。' },
      { title: '采购入库', owner: '目标仓库', status: '待入库 → 已入库', page: '/inventory/stocks', body: '库管在库存作业请求中执行入库，形成库存流水并更新移动平均成本。', tip: '入库仓库以采购订单目标仓库为准。' }
    ]
  },
  {
    key: 'production', name: '生产与入库', color: '#d85c4a', icon: '🏗️',
    summary: '生产计划或补货需求形成任务，经领料、生产、质检和成品入库完成。',
    nodes: [
      { title: '计划 / 补货', owner: '生产 / 仓库', status: '待生产', page: '/production/plans', body: '来源可以是生产计划、销售缺货补货或手工创建。', tip: '先确认产品已经维护有效 BOM。' },
      { title: '生产任务', owner: '生产', status: '待领料', page: '/production/tasks', body: '填写产品、计划数量、目标仓库和负责人，工厂仓库为默认目标。', tip: '生产任务可以继续拆分查看明细流程。' },
      { title: '领料审批', owner: '工厂仓库库管', status: '待领料审批', page: '/production/approvals', body: '按照 BOM 计算原料与包装，审批只发送到领料仓库对应库管。', tip: '审批无反应时应显示具体权限或状态原因。' },
      { title: '领料出库', owner: '工厂仓库库管', status: '待出库 → 已出库', page: '/inventory/stocks', body: '在库存作业请求中执行，形成出库流水并进入生产过程。', tip: '原料不足时由生产提交自主采购需求。' },
      { title: '生产完成', owner: '生产', status: '待质检', page: '/production/tasks', body: '登记实际生产数量和完成情况，按设置进入质检。', tip: '实际产量与计划差异应在备注中说明。' },
      { title: '质检与入库', owner: '质检 → 工厂仓库', status: '待质检 → 已入库', page: '/quality/tasks', body: '质检合格后生成成品入库请求，库管执行后任务完成。', tip: '所有动作会显示在关联销售订单时间轴。' }
    ]
  },
  {
    key: 'transfer', name: '库存转移', color: '#2c80c5', icon: '🔁',
    summary: '仓库间转移严格分为源仓出库、可选质检和目标仓入库。',
    nodes: [
      { title: '提交转移', owner: '发起人', status: '待出库', page: '/inventory/transfers', body: '选择源仓、目标仓、物品、数量以及是否需要质检。', tip: '提交不会直接改变两个仓库库存。' },
      { title: '源仓出库', owner: '源仓库管', status: '待出库 → 在途', page: '/inventory/stocks', body: '源仓在库存作业请求中执行出库，生成库存出库记录。', tip: '只有源仓负责人处理该作业。' },
      { title: '目标仓质检（可选）', owner: '目标仓质检', status: '待质检', page: '/quality/tasks', body: '勾选质检时，出库后生成目标仓质检任务。', tip: '未勾选质检则跳过此节点。' },
      { title: '目标仓入库', owner: '目标仓库管', status: '待入库 → 已完成', page: '/inventory/stocks', body: '质检通过或无需质检后生成入库请求，执行后完成转移。', tip: '转移成本随在途物品进入目标仓。' }
    ]
  },
  {
    key: 'return', name: '销售退换货', color: '#b45f5f', icon: '↩️',
    summary: '销售提交明细后，由财务、经理或董事长任意一人审批，再进入可选质检和仓库作业。',
    nodes: [
      { title: '提交退换货', owner: '销售', status: '待审批', page: '/sales/returns', body: '选择原订单，填写退换货明细、数量、原因和是否需要质检。', tip: '明细必须从已添加行读取，不能只停留在输入框。' },
      { title: '任一人审批', owner: '财务 / 经理 / 董事长', status: '待审批 → 已通过', page: '/approvals', body: '三类审批人中任意一人通过即可继续，驳回则退回申请。', tip: '详情中核对订单、明细、原因和金额影响。' },
      { title: '退货质检（可选）', owner: '质检', status: '待质检', page: '/quality/tasks', body: '需要质检时判断是否可重新入库以及不合格处置方式。', tip: '质检结论决定后续库存处理。' },
      { title: '仓库作业', owner: '指定仓库库管', status: '待入库 / 待出库', page: '/inventory/stocks', body: '退货通常进入沧州仓库；换货的补发部分生成相应出库作业。', tip: '执行后形成出入库记录。' }
    ]
  },
  {
    key: 'sample', name: '供应商样品', color: '#7b68b4', icon: '🧪',
    summary: '供应商可供物品申请收样，由仓库确认实物，再由质检形成供应商评价依据。',
    nodes: [
      { title: '申请收样', owner: '采购', status: '待收样', page: '/suppliers', body: '从供应商供货目录选择产品、原料或包装并创建样品接收任务。', tip: '样品必须关联供应商和具体物品。' },
      { title: '仓库确认收样', owner: '对应仓库库管', status: '已收样 / 待质检', page: '/inventory/stocks', body: '库管核对样品实物并确认接收，系统创建样品质检任务。', tip: '页面可通过 focusType=supplier_sample 定位任务。' },
      { title: '样品质检', owner: '质检', status: '质检中', page: '/quality/tasks', body: '上传质检结果、结论和附件。', tip: '不形成可售库存，重点保留质量证据。' },
      { title: '供应矩阵回写', owner: '采购 / 管理', status: '已完成', page: '/suppliers', body: '质检结论显示在供应商—物品矩阵中，支持按物品快速选择供应商。', tip: '点击矩阵单元格可查看供应商和质检单详情。' }
    ]
  }
]

export const statusGuide = [
  ['草稿', '只保存了录入内容', '原提交人', '继续编辑并正式提交；不会产生审批和下游任务', 'neutral'],
  ['待审批', '单据已正式提交，等待有权限人员决定', '审批中心中的负责人', '打开审批详情，核对原因、金额、备注和明细', 'warning'],
  ['待财务审核', '订单尚未确认收款或应收安排', '指定财务；未指定时所有财务', '通过、驳回或转为应收', 'warning'],
  ['待置货', '仓库已收到需求，但可能仍有财务、库存或包装前置条件', '发货仓库库管', '鼠标悬停灰色按钮查看首要阻塞原因', 'info'],
  ['补货中', '库存不足，采购或生产任务正在执行', '采购 / 生产 / 质检 / 仓库', '从订单全链时间轴查看卡在哪个子流程', 'info'],
  ['待出库', '已经生成库存作业，但库存尚未真正扣减', '对应源仓库库管', '在库存状态的“库存作业请求”执行', 'warning'],
  ['在途', '源仓已出库，目标仓尚未入库', '目标仓质检或库管', '等待质检或执行目标仓入库', 'info'],
  ['待质检', '业务已到质检节点，不能直接入库', '对应仓库/任务的质检员', '记录结果并提交结论', 'warning'],
  ['待入库', '质检通过或无需质检，等待库管增加库存', '目标仓库库管', '执行入库并核对批次、数量和成本', 'warning'],
  ['待发货', '置货完成，等待发货凭证与发货确认', '发货仓库库管', '扫描发货码或在页面完成发货', 'info'],
  ['应收中', '订单已放行但仍有货款未收', '财务；销售可查看本人订单', '登记后续回款，收清后转为已完成', 'danger'],
  ['已完成', '当前业务链已达到终态', '系统', '通常无需操作，可在已完成列表和流水中追溯', 'success'],
  ['已驳回 / 已退回', '审批不通过或任务需要补充资料', '原提交人', '查看驳回原因，修改后按页面允许的方式重新提交', 'danger']
]

export const moduleGroups = [
  { name: '工作台与追踪', icon: '🧭', pages: [['首页', '/dashboard'], ['审批中心', '/approvals'], ['通知中心', '/notifications'], ['订单全链看板', '/flow-board'], ['订单全链时间轴', '/sales/order-timeline']] },
  { name: '主数据', icon: '🗂️', pages: [['客户基本信息', '/customers'], ['供应商基本信息', '/suppliers'], ['产品基本信息', '/products'], ['原料基本信息', '/materials'], ['包装基本信息', '/packaging-configs'], ['标准价格表', '/standard-prices']] },
  { name: '销售', icon: '📝', pages: [['客户订单', '/sales/orders'], ['我的应收款', '/sales/my-receivables'], ['销售退换货', '/sales/returns'], ['客户订单物流', '/sales/logistics']] },
  { name: '库存', icon: '📦', pages: [['库存作业看板', '/inventory/queues'], ['库存状态', '/inventory/stocks'], ['库存转移', '/inventory/transfers'], ['库存盘点', '/inventory/stock-takes'], ['库存异常', '/inventory/exceptions']] },
  { name: '采购', icon: '🛒', pages: [['采购订单', '/purchases/orders'], ['采购付款申请', '/purchases/settlements'], ['采购到货', '/purchases/receipts'], ['采购退货', '/purchases/returns']] },
  { name: '生产与质检', icon: '🏗️', pages: [['配方管理', '/production/boms'], ['生产计划', '/production/plans'], ['生产任务', '/production/tasks'], ['领料审批', '/production/approvals'], ['质检任务', '/quality/tasks']] },
  { name: '财务', icon: '💰', pages: [['客户订单收款确认', '/sales/approvals'], ['应收管理', '/finance/receivables'], ['应付管理', '/finance/payables'], ['收付款账户', '/finance/accounts'], ['资金流水', '/finance/cash-flows'], ['报销管理', '/finance/reimbursements']] },
  { name: '系统管理', icon: '⚙️', pages: [['组织架构', '/admin/org-units'], ['用户管理', '/admin/users'], ['模块权限分配', '/admin/module-permissions'], ['打印设备与记录', '/admin/print-agents'], ['系统日志', '/admin/logs']] }
]

export const screenshots = [
  {
    key: 'production', title: '生产任务：任务、库存与补货入口', src: '/help/production-tasks.png',
    description: '上方看任务总量和操作入口，中间处理生产任务，下方跟踪由销售缺货触发的生产补货需求。',
    marks: [
      { n: 1, x: 88, y: 24, text: '查看库存、提交采购需求、新增任务' },
      { n: 2, x: 22, y: 36, text: '进行中与已完成分开查看' },
      { n: 3, x: 50, y: 74, text: '补货需求按待完成和已完成区分' }
    ]
  },
  {
    key: 'stocks', title: '库存状态：统一库存作业入口', src: '/help/inventory-stocks.png',
    description: '先查库存，再在统一的库存作业请求中完成入库或出库，底部流水用于审计追踪。',
    marks: [
      { n: 1, x: 76, y: 26, text: '按仓库、类型、编码或名称查询' },
      { n: 2, x: 52, y: 58, text: '采购、生产、领料、移库等都在这里执行' },
      { n: 3, x: 55, y: 88, text: '按时间、物料、原因、操作人查询流水' }
    ]
  },
  {
    key: 'approval', title: '审批中心：待办与历史分开', src: '/help/approvals.png',
    description: '审批卡片先说明类型和来源，详情中查看业务原因、明细和备注；处理后进入审批历史。',
    marks: [
      { n: 1, x: 79, y: 14, text: '顶部快速确认待审批数量与当前角色' },
      { n: 2, x: 60, y: 38, text: '只处理正式提交且分派给当前角色的单据' },
      { n: 3, x: 58, y: 72, text: '历史记录只读，可用于追溯' }
    ]
  },
  {
    key: 'bom', title: '配方管理：生产前的基础数据', src: '/help/boms.png',
    description: 'BOM 决定领料和成本计算。新增或编辑时确认成品、包装、kg/袋、版本和原料标准耗用。',
    marks: [
      { n: 1, x: 93, y: 14, text: '新增 BOM' },
      { n: 2, x: 54, y: 35, text: '核对包装、规格、版本和组成原料' },
      { n: 3, x: 92, y: 42, text: '编辑前确认没有误改其他产品配方' }
    ]
  }
]

export const quickAnswers = [
  ['为什么我看不到某个菜单？', '系统按角色和模块权限显示菜单；还可能受本人、小组、部门或全部数据范围限制。请先让管理员核对角色、模块权限和仓库/部门归属。'],
  ['为什么按钮是灰色的？', '灰色通常代表前置条件未满足。置货优先检查财务审核/收款，其次检查成品和包装库存；鼠标悬停应显示首要原因。'],
  ['为什么保存后没有进入后续流程？', '保存草稿只保存内容。必须点击“提交”并通过必填校验，才会生成审批、置货和其他下游任务。'],
  ['单据现在卡在哪里？', '先看列表状态，再打开“订单全链看板”定位大环节；点击订单进入“订单全链时间轴”查看每个任务的开始、结束、操作人和关联单据。'],
  ['提交后为什么我自己处理不了？', '任务会按角色、指定负责人和仓库归属分派。被指定给其他财务或其他仓库时，当前账号只能查看或完全不可见。'],
  ['操作没有反应怎么办？', '先等页面提示；再点击刷新并查看状态是否变化。仍无变化时记录单据编号、账号、时间、页面和点击动作交给管理员排查，不要连续重复提交。']
]
