<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">生产任务</div><div class="summary-value">{{ baseRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待特殊审批</div><div class="summary-value text-warning">{{ pendingSpecialCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待质检</div><div class="summary-value">{{ waitingQualityCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">返工任务</div><div class="summary-value">{{ reworkCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="生产任务" description="支持创建生产任务、提交特殊领用审批，并将已完工任务单独分区展示。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
        <el-button v-if="canManageTasks" @click="openStocks">查看库存</el-button>
        <el-button v-if="canManageTasks" @click="purchaseRequestsPanel?.openCreate()">提交采购需求</el-button>
        <el-button v-if="canManageTasks" type="primary" @click="openCreate">新增任务</el-button>
      </template>

      <el-tabs v-if="showTaskTabs" v-model="activeTaskTab" class="status-tabs">
        <el-tab-pane :label="`进行中 (${ongoingRows.length})`" name="ongoing" />
        <el-tab-pane :label="`已完工/已关闭 (${closedRows.length})`" name="closed" />
      </el-tabs>

      <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
        <el-table-column prop="task_no" label="任务号" width="150">
          <template #default="scope">
            <span :style="scope.row.order_type_color ? { color: scope.row.order_type_color, fontWeight: 600 } : {}">{{ scope.row.task_no }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column prop="source_type" label="来源" width="120">
          <template #default="scope">{{ sourceLabel(scope.row.source_type) }}</template>
        </el-table-column>
        <el-table-column prop="sales_order_no" label="来源订单" width="150" />
        <el-table-column prop="parent_task_no" label="来源父任务" width="150" />
        <el-table-column prop="planned_quantity" label="计划数量" width="110"><template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template></el-table-column>
        <el-table-column prop="produced_quantity" label="已产数量" width="110"><template #default="scope">{{ formatNumber(scope.row.produced_quantity) }}</template></el-table-column>
        <el-table-column prop="scrap_quantity" label="报废数量" width="110"><template #default="scope">{{ formatNumber(scope.row.scrap_quantity) }}</template></el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="batch_no" label="批次号" min-width="160" />
        <el-table-column label="操作" :width="canManageTasks ? 460 : 120" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="viewTask(scope.row)">详情</el-button>
            <el-button v-if="canRequestMaterial(scope.row)" size="small" type="warning" @click="openMaterialRequest(scope.row)">领料申请</el-button>
            <el-button
              v-if="canShowInboundAction(scope.row)"
              size="small"
              type="primary"
              @click="handleInboundAction(scope.row)"
            >
              {{ scope.row.status === 'material_issued' ? '提交入库申请' : '执行入库申请' }}
            </el-button>
            <el-button v-if="canManageTasks" size="small" type="danger" plain @click="openCancel(scope.row)" :disabled="!canCancel(scope.row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
        @change="handleTaskPaginationChange"
      />
    </PageSection>

    <PageSection v-if="canManageTasks" title="待生产补货需求" description="销售订单库存不足且产品为生产型时，会在此生成待处理需求。">
      <el-tabs v-model="demandStatusTab" class="status-tabs" @tab-change="handleDemandStatusChange">
        <el-tab-pane :label="`待完成 (${demandStatusTab === 'pending' ? demandTotal : ''})`" name="pending" />
        <el-tab-pane label="已完成" name="completed" />
      </el-tabs>
      <el-table :data="fulfillmentDemands" stripe>
        <el-table-column prop="demand_no" label="需求号" width="150">
          <template #default="scope">
            <span :style="scope.row.order_type_color ? { color: scope.row.order_type_color, fontWeight: 600 } : {}">{{ scope.row.demand_no }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="source_type" label="来源" width="130">
          <template #default="scope">{{ scope.row.source_type === 'low_stock_replenishment' ? '安全库存补货' : '客户订单缺货' }}</template>
        </el-table-column>
        <el-table-column prop="submitted_by_name" label="提交人" width="110">
          <template #default="scope">{{ scope.row.submitted_by_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="sales_order_no" label="客户订单号" width="160">
          <template #default="scope">{{ scope.row.sales_order_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="product_name" label="产品" min-width="180" />
        <el-table-column prop="shortage_quantity" label="缺口数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.shortage_quantity) }}</template>
        </el-table-column>
        <el-table-column label="补货进度" width="180">
          <template #default="scope">
            <div class="demand-progress">
              <el-progress
                :percentage="demandProgressPercent(scope.row)"
                :stroke-width="10"
                :show-text="false"
                :status="demandProgressPercent(scope.row) >= 100 ? 'success' : undefined"
              />
              <div class="demand-progress-text">
                已补 {{ formatNumber(scope.row.fulfilled_quantity || 0) }} / 共需 {{ formatNumber(demandRequired(scope.row)) }}
                <span v-if="demandPending(scope.row) > 0" class="demand-progress-pending">还差 {{ formatNumber(demandPending(scope.row)) }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="原料状况" width="110">
          <template #default="scope">
            <el-tag v-if="scope.row.shortage_level === 'green'" type="success" effect="dark">绿·原料充足</el-tag>
            <el-tag v-else-if="scope.row.shortage_level === 'yellow'" type="warning" effect="dark">黄·部分不足</el-tag>
            <el-tag v-else-if="scope.row.shortage_level === 'red'" type="danger" effect="dark">红·原料不足</el-tag>
            <el-tag v-else type="info" effect="plain">-</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最大可产" width="110">
          <template #default="scope">
            <span v-if="scope.row.max_producible_quantity != null">{{ formatNumber(scope.row.max_producible_quantity) }}</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="110">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="进度" width="120">
          <template #default="scope">
            <el-tag :type="demandProgressTag(scope.row.progress_status)" effect="dark" size="small">{{ scope.row.progress_label || '-' }}</el-tag>
            <div v-if="scope.row.related_no" class="demand-related">关联: {{ scope.row.related_no }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="process_note" label="处理说明" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="info" @click="openMaterialProgress(scope.row)">原料进度</el-button>
            <el-button v-if="demandStatusTab === 'pending'" size="small" text type="primary" @click="prefillDemand(scope.row)">转建任务</el-button>
            <el-button v-if="scope.row.status === 'pending'" size="small" text type="danger" @click="cancelDemand(scope.row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        :total="demandTotal"
        :page="demandPagination.page"
        :page-size="demandPagination.pageSize"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, prev, pager, next"
        @change="handleDemandPaginationChange"
      />
    </PageSection>

    <el-dialog v-model="materialProgressVisible" title="原料状况与补货确认" width="min(1100px, 95vw)">
      <template v-if="materialProgressData">
        <el-alert
          :type="materialProgressData.can_produce ? 'success' : 'warning'"
          :closable="false"
          show-icon
          class="mb-12"
        >
          <template #title>
            生产补货需求 {{ materialProgressData.demand_no }}：{{ materialProgressData.can_produce ? '原料已满足，可直接转生产任务' : '原料尚未全部满足，请先处理原料补货' }}
          </template>
        </el-alert>
        <el-table :data="materialProgressItems" size="small" border>
          <el-table-column prop="material_name" label="原料" min-width="150" />
          <el-table-column label="需求数量" width="90">
            <template #default="scope">{{ formatNumber(scope.row.required_quantity) }}</template>
          </el-table-column>
          <el-table-column label="库存可用" width="90">
            <template #default="scope">
              <el-tooltip :content="`实有 ${formatNumber(scope.row.stock_quantity)} − 库存占用 ${formatNumber(scope.row.stock_reserved_quantity)} − 生产未领用占用 ${formatNumber(scope.row.production_reserved_quantity)}`">
                <span>{{ formatNumber(scope.row.stock_available_quantity) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="已提交补货" width="100">
            <template #default="scope">{{ formatNumber(scope.row.submitted_quantity) }}</template>
          </el-table-column>
          <el-table-column label="已到货" width="90">
            <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
          </el-table-column>
          <el-table-column label="已提交未入库" width="110">
            <template #default="scope">{{ formatNumber(scope.row.outstanding_quantity) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="130">
            <template #default="scope">
              <el-tag :type="materialProgressTag(scope.row.progress)" size="small">{{ materialProgressLabel(scope.row) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="关联采购单" min-width="140">
            <template #default="scope">{{ scope.row.related_po_no || '-' }}</template>
          </el-table-column>
          <el-table-column v-if="!materialProgressData.can_produce" label="本次补货数量" width="170" fixed="right">
            <template #default="scope">
              <el-input-number
                v-model="scope.row.suggest_quantity"
                :min="0"
                :max="scope.row.shortage"
                :precision="2"
                size="small"
                style="width: 140px"
                :disabled="scope.row.shortage <= 0 || !materialProgressData.can_edit_replenishment"
              />
            </template>
          </el-table-column>
        </el-table>
        <div v-if="!materialProgressData.can_produce" class="material-progress-tip">
          <template v-if="materialProgressData.can_edit_replenishment">
            每次提交新建独立原料补货需求。可追加量 = 需求数量 − 库存可用 − 已提交未入库量；已有采购单不影响继续补足剩余缺口。
          </template>
          <template v-else>当前生产补货需求已处理，不能再追加原料补货。</template>
        </div>
        <el-table v-if="materialProgressData.replenishment_demands?.length" :data="materialProgressData.replenishment_demands" size="small" class="mb-12">
          <el-table-column prop="demand_no" label="已提交原料需求号" />
          <el-table-column label="补货明细" min-width="240">
            <template #default="scope">{{ scope.row.items.map(item => `${item.material_name} ${formatNumber(item.shortage_quantity)}`).join('；') }}</template>
          </el-table-column>
          <el-table-column label="关联采购单">
            <template #default="scope">{{ scope.row.purchase_order_no || '待采购处理' }}</template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button @click="materialProgressVisible = false">关闭</el-button>
        <el-button v-if="materialProgressData && !materialProgressData.can_produce" type="primary" :disabled="!materialProgressItems.some(item => Number(item.suggest_quantity) > 0)" :loading="materialProgressSubmitting" @click="submitMaterialProgressDemands">提交补货需求</el-button>
        <el-button v-if="materialProgressData?.can_produce" type="success" @click="closeMaterialProgressAndCreateTask">转建任务</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editDemandVisible" title="调整补货需求数量" width="440px">
      <el-form :model="editDemandForm" label-width="100px">
        <el-form-item label="需求号">
          <span>{{ editDemandForm.demand_no }}</span>
        </el-form-item>
        <el-form-item label="产品">
          <span>{{ editDemandForm.product_name }}</span>
        </el-form-item>
        <el-form-item label="缺口数量" required>
          <el-input-number v-model="editDemandForm.shortage_quantity" :min="0.01" :precision="2" style="width: 180px" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="editDemandForm.note" type="textarea" :rows="2" placeholder="调整原因(选填)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDemandVisible = false">取消</el-button>
        <el-button type="primary" :loading="editDemandSubmitting" @click="submitEditDemand">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createVisible" title="新增生产任务" width="980px">
      <el-alert v-if="activeDemand" type="warning" :closable="false" show-icon class="mb-12">
        <template #title>
          补货需求：{{ activeDemand.product_name || '-' }}，缺口 {{ formatNumber(activeDemand.shortage_quantity) }}；
          安全库存 {{ formatNumber(activeDemand.safety_stock_qty) }}；
          预计可用库存 {{ formatNumber(activeDemand.expected_available_quantity) }}
        </template>
      </el-alert>
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="任务来源">
          <el-select v-model="createForm.source_type" style="width: 100%" @change="handleSourceChange">
            <el-option label="手工排产" value="manual" />
            <el-option label="客户订单" value="sales_order" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="createForm.source_type === 'sales_order'" label="客户订单">
          <el-select v-model="createForm.sales_order_id" filterable style="width: 100%" @change="handleSalesOrderChange">
            <el-option v-for="item in salesOrders" :key="item.id" :label="`${item.order_no} / ${item.customer_name || '-'}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-table v-if="createForm.source_type === 'sales_order' && selectedSalesOrder?.items?.length" :data="selectedSalesOrder.items" border class="mb-12">
          <el-table-column prop="product_name" label="订单产品" min-width="160" />
          <el-table-column prop="display_specification" label="规格" width="120" />
          <el-table-column prop="quantity" label="订单数量" width="120"><template #default="scope">{{ formatNumber(scope.row.quantity) }}</template></el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100"><template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template></el-table-column>
        </el-table>
        <el-form-item label="产品">
          <el-select v-model="createForm.product_id" filterable placeholder="输入产品编号、名称或规格搜索" style="width: 100%" @change="syncPlannedQuantity">
            <el-option v-for="item in availableProducts" :key="item.id" :label="[item.product_code, item.name, item.specification || '无规格'].join(' - ')" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划数量"><el-input-number v-model="createForm.planned_quantity" :min="0.01" style="width: 100%" /></el-form-item>
        <el-form-item label="浮动比例"><el-input-number v-model="createForm.fluctuation_ratio" :min="0" :max="1" :step="0.01" style="width: 100%" /></el-form-item>
        <el-form-item label="目标仓库">
          <el-select v-model="createForm.warehouse_id" placeholder="默认廊坊工厂仓" style="width: 100%">
            <el-option v-for="item in finishedWarehouses" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="createForm.notes" /></el-form-item>
      </el-form>
      <el-alert v-if="createForm.product_id && !currentBom" type="warning" :closable="false" show-icon class="mb-12">
        <template #title>当前产品尚未配置 BOM，无法生成原料明细</template>
      </el-alert>
      <div class="detail-toolbar">
        <span>原料明细（按 BOM 自动生成）</span>
        <span class="toolbar-tip">可直接调整领料数量；超出浮动上限的行后续会自动进入特殊领料审批</span>
      </div>
      <el-table :data="createForm.material_usages" border>
        <el-table-column prop="material_name" label="原料" min-width="180" />
        <el-table-column prop="standard_quantity" label="BOM 单耗" width="110"><template #default="scope">{{ formatNumber(scope.row.standard_quantity) }}</template></el-table-column>
        <el-table-column prop="planned_quantity" label="计划耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template></el-table-column>
        <el-table-column prop="allowed_quantity" label="浮动上限" width="110"><template #default="scope">{{ formatNumber(scope.row.allowed_quantity) }}</template></el-table-column>
        <el-table-column label="申请领料" width="140"><template #default="scope"><el-input-number v-model="scope.row.requested_quantity" :min="0.01" :precision="2" style="width: 100%" /></template></el-table-column>
        <el-table-column label="原因" min-width="220"><template #default="scope"><el-input v-model="scope.row.reason" placeholder="超额领料请填写原因" /></template></el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="createTask">创建任务</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rawDemandConfirmVisible" title="原料不足" width="680px">
      <template v-if="rawDemandItems.length">
        <el-alert type="warning" :closable="false" show-icon class="mb-12">
          <template #title>
            {{ rawDemandMode === 'standalone' ? '当前原料库存无法支持生产。是否提交原料补货需求?' : '原料库存不足,本次仅能安排部分生产。是否对剩余部分提交原料补货需求?' }}
            数量默认等于剩余缺口,可修改。
          </template>
        </el-alert>
        <el-table :data="rawDemandItems" border>
          <el-table-column prop="material_name" label="原料" min-width="160" />
          <el-table-column label="需要数量" width="100">
            <template #default="scope">{{ formatNumber(scope.row.required_quantity) }}</template>
          </el-table-column>
          <el-table-column label="可用数量" width="100">
            <template #default="scope">{{ formatNumber(scope.row.available_quantity) }}</template>
          </el-table-column>
          <el-table-column label="缺口数量" width="100">
            <template #default="scope">{{ formatNumber(scope.row.shortage_quantity) }}</template>
          </el-table-column>
          <el-table-column label="补货数量" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.demand_quantity" :min="1" :precision="0" size="small" style="width: 120px" />
            </template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button v-if="rawDemandMode === 'with_task'" @click="createTaskDirect">仅创建任务,不提原料</el-button>
        <el-button @click="rawDemandConfirmVisible = false">取消</el-button>
        <el-button type="primary" :loading="rawDemandSubmitting" @click="confirmRawDemand">
          {{ rawDemandMode === 'standalone' ? '确认提原料补货' : '提交原料补货并创建任务' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="materialRequestVisible" title="领料申请" width="980px">
      <el-alert v-if="taskDetail?.raw_warehouse" type="info" :closable="false" show-icon class="mb-12">
        <template #title>出库仓库：{{ taskDetail.raw_warehouse.name }}</template>
      </el-alert>
      <div class="detail-toolbar">
        <span>领料明细</span>
        <span class="toolbar-tip">超出浮动上限会自动同步给经理和原料库管审批</span>
      </div>
      <el-table :data="materialRequestForm.material_usages" border>
        <el-table-column prop="material_name" label="原料" min-width="180" />
        <el-table-column prop="planned_quantity" label="计划耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template></el-table-column>
        <el-table-column prop="allowed_quantity" label="浮动上限" width="110"><template #default="scope">{{ formatNumber(scope.row.allowed_quantity) }}</template></el-table-column>
        <el-table-column prop="available_quantity" label="可用库存" width="110"><template #default="scope">{{ formatNumber(scope.row.available_quantity) }}</template></el-table-column>
        <el-table-column label="申请领料" width="140"><template #default="scope"><el-input-number v-model="scope.row.requested_quantity" :min="0.01" :precision="2" style="width: 100%" /></template></el-table-column>
        <el-table-column label="当前状态" width="130"><template #default="scope"><el-tag :type="statusType(scope.row.special_request_status === 'not_required' ? scope.row.inventory_approval_status : scope.row.special_request_status)">{{ scope.row.special_request_status === 'not_required' ? (scope.row.inventory_approval_status_label || statusLabel(scope.row.inventory_approval_status)) : (scope.row.special_request_status_label || statusLabel(scope.row.special_request_status)) }}</el-tag></template></el-table-column>
        <el-table-column label="原因" min-width="220"><template #default="scope"><el-input v-model="scope.row.reason" placeholder="超额领料请填写原因" /></template></el-table-column>
      </el-table>
      <template #footer>
        <div class="footer-actions">
          <div class="footer-tip" v-if="materialRequestOverLimitRows.length">当前有 {{ materialRequestOverLimitRows.length }} 条超额领料</div>
          <div>
            <el-button @click="materialRequestVisible = false">取消</el-button>
            <el-button type="primary" @click="submitMaterialRequest">提交申请</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="completeVisible" title="完工录入" width="980px">
      <el-alert v-if="taskDetail?.raw_warehouse" type="info" :closable="false" show-icon class="mb-12">
        <template #title>扣料仓库：{{ taskDetail.raw_warehouse.name }}</template>
      </el-alert>
      <el-form :model="completeForm" label-width="90px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="产出数量"><el-input-number v-model="completeForm.produced_quantity" :min="0.01" style="width: 100%" @change="syncScrapFromProduced" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="报废数量"><el-input-number v-model="completeForm.scrap_quantity" :min="0" style="width: 100%" @change="syncProducedFromScrap" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="完工后质检"><el-switch v-model="completeForm.requires_quality" active-text="需要质检" inactive-text="无需质检" /></el-form-item></el-col>
          <!-- 这里不该指定质检员:质检与入库由目标仓库的质检员/库管接手,
               后端建质检任务时已用建任务时选定的 task.warehouse_id
               (services/quality_workflow.py:114),所以此处只需展示归属仓库。 -->
          <el-col v-if="completeForm.requires_quality" :span="24">
            <el-form-item label="入库仓库">
              <span>{{ taskDetail?.warehouse?.name || taskDetail?.warehouse_name || '廊坊工厂仓' }}</span>
              <span class="toolbar-tip" style="margin-left: 8px">质检与入库由该仓库的质检员、库管完成</span>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="初步验收">
              <el-input
                v-model="completeForm.preliminary_acceptance_note"
                type="textarea"
                :rows="3"
                placeholder="记录完工产品外观、包装、数量初验、异常说明等，便于后续追溯"
              />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <div class="detail-toolbar">
        <span>原料实际耗用</span>
        <span class="toolbar-tip">超出浮动上限时请先提交特殊领用审批</span>
      </div>
      <el-table :data="completeForm.actual_usages" border>
        <el-table-column prop="material_name" label="原料" min-width="180" />
        <el-table-column prop="planned_quantity" label="计划耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template></el-table-column>
        <el-table-column prop="allowed_quantity" label="浮动上限" width="110"><template #default="scope">{{ formatNumber(scope.row.allowed_quantity) }}</template></el-table-column>
        <el-table-column prop="approved_quantity" label="已批耗用" width="110"><template #default="scope">{{ formatNumber(scope.row.approved_quantity) }}</template></el-table-column>
        <el-table-column prop="available_quantity" label="可用库存" width="110"><template #default="scope">{{ formatNumber(scope.row.available_quantity) }}</template></el-table-column>
        <el-table-column label="实际耗用" width="140"><template #default="scope"><el-input-number v-model="scope.row.requested_quantity" :min="0.01" :precision="2" style="width: 100%" /></template></el-table-column>
        <el-table-column label="审批状态" width="130"><template #default="scope"><el-tag :type="statusType(scope.row.special_request_status)">{{ scope.row.special_request_status_label || statusLabel(scope.row.special_request_status) }}</el-tag></template></el-table-column>
        <el-table-column label="原因" min-width="220"><template #default="scope"><el-input v-model="scope.row.reason" placeholder="超额领用必须填写原因" /></template></el-table-column>
      </el-table>
      <template #footer>
        <div class="footer-actions">
          <div class="footer-tip" v-if="overLimitRows.length">当前有 {{ overLimitRows.length }} 条超额耗用</div>
          <div>
            <el-button v-if="overLimitRows.length" type="warning" @click="submitSpecialRequest">提交特殊领用审批</el-button>
            <el-button @click="completeVisible = false">取消</el-button>
            <el-button type="primary" @click="completeTask">确认完工</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="cancelVisible" title="取消生产任务" width="520px">
      <el-form :model="cancelForm" label-width="90px">
        <el-form-item label="任务号">
          <el-input :model-value="activeTask?.task_no || ''" disabled />
        </el-form-item>
        <el-form-item label="取消原因">
          <el-input v-model="cancelForm.reason" type="textarea" :rows="4" placeholder="请输入取消或异常终止原因" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cancelVisible = false">取消</el-button>
        <el-button type="danger" @click="cancelTask">确认取消</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" size="54%" title="生产任务详情">
      <el-descriptions v-if="taskDetail" :column="2" border>
        <el-descriptions-item label="任务号">{{ taskDetail.task_no }}</el-descriptions-item>
        <el-descriptions-item label="产品">{{ taskDetail.product_name }}</el-descriptions-item>
        <el-descriptions-item label="来源">{{ sourceLabel(taskDetail.source_type) }}</el-descriptions-item>
        <el-descriptions-item label="父任务">{{ taskDetail.parent_task_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="客户订单">{{ taskDetail.sales_order?.order_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="订单客户">{{ taskDetail.sales_order?.customer_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="计划数量">{{ formatNumber(taskDetail.planned_quantity) }}</el-descriptions-item>
        <el-descriptions-item label="浮动比例">{{ formatNumber(Number(taskDetail.fluctuation_ratio || 0) * 100) }}%</el-descriptions-item>
        <el-descriptions-item label="状态">{{ statusLabel(taskDetail.status) }}</el-descriptions-item>
        <el-descriptions-item label="原料仓">{{ taskDetail.raw_warehouse?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="质检任务">{{ taskDetail.quality_task?.quality_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="质检状态">{{ taskDetail.quality_task?.status_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="目标入库仓">{{ taskDetail.finished_warehouse?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="完工记录人">{{ taskDetail.completion_recorded_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="完工时间">{{ taskDetail.completed_at ? formatDateTime(taskDetail.completed_at) : '-' }}</el-descriptions-item>
        <el-descriptions-item label="初步验收" :span="2">{{ taskDetail.preliminary_acceptance_note || '-' }}</el-descriptions-item>
        <el-descriptions-item label="流程进度" :span="2">
          <div class="flow-steps">
            <el-tooltip
              v-for="step in taskDetail.flow_steps || []"
              :key="step.key"
              effect="light"
              placement="top"
              :show-after="2000"
            >
              <template #content>
                <div class="flow-tooltip-content">{{ step.tooltip }}</div>
              </template>
              <span class="flow-step" :class="`is-${step.status || 'pending'}`">{{ step.label }}</span>
            </el-tooltip>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="备注" :span="2">{{ taskDetail.notes || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="taskDetail?.usages || []" stripe class="mt-16">
        <el-table-column prop="material_name" label="原料" min-width="180" />
        <el-table-column prop="planned_quantity" label="计划耗用" width="120"><template #default="scope">{{ formatNumber(scope.row.planned_quantity) }}</template></el-table-column>
        <el-table-column prop="allowed_quantity" label="浮动上限" width="120"><template #default="scope">{{ formatNumber(scope.row.allowed_quantity) }}</template></el-table-column>
        <el-table-column prop="approved_quantity" label="已批耗用" width="120"><template #default="scope">{{ formatNumber(scope.row.approved_quantity) }}</template></el-table-column>
        <el-table-column prop="actual_quantity" label="已出库/实际耗用" width="120"><template #default="scope">{{ formatNumber(scope.row.actual_quantity) }}</template></el-table-column>
        <el-table-column prop="special_request_status" label="审批状态" width="130"><template #default="scope"><el-tag :type="statusType(scope.row.special_request_status)">{{ scope.row.special_request_status_label || statusLabel(scope.row.special_request_status) }}</el-tag></template></el-table-column>
        <el-table-column label="库存审批" min-width="180"><template #default="scope">{{ scope.row.inventory_approval_status_label || statusLabel(scope.row.inventory_approval_status) }} / {{ scope.row.inventory_remark || '-' }}</template></el-table-column>
        <el-table-column label="库存审批时间" min-width="170"><template #default="scope">{{ scope.row.inventory_approved_at ? formatDateTime(scope.row.inventory_approved_at) : '-' }}</template></el-table-column>
        <el-table-column label="经理审批" min-width="180"><template #default="scope">{{ scope.row.manager_approval_status_label || statusLabel(scope.row.manager_approval_status) }} / {{ scope.row.manager_remark || '-' }}</template></el-table-column>
        <el-table-column label="经理审批时间" min-width="170"><template #default="scope">{{ scope.row.manager_approved_at ? formatDateTime(scope.row.manager_approved_at) : '-' }}</template></el-table-column>
        <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
      </el-table>
    </el-drawer>
    <DepartmentPurchaseRequests v-if="auth.isRole('production')" ref="purchaseRequestsPanel" />
  </div>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/switch/style/css'
import 'element-plus/es/components/progress/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tooltip/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElProgress from 'element-plus/es/components/progress/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { get, post, put } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import { useRouteFocus } from '../../composables/useRouteFocus'
import { useLocalPagination } from '../../composables/useLocalPagination'
import PageSection from '../../components/PageSection.vue'
import DepartmentPurchaseRequests from '../../components/DepartmentPurchaseRequests.vue'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const { route, focusedId: focusedTaskId, applyFocus: applyRouteFocus } = useRouteFocus()
const router = useRouter()
const rows = ref([])
const products = ref([])
const boms = ref([])
const salesOrders = ref([])
const selectedSalesOrder = ref(null)
const createVisible = ref(false)
const materialRequestVisible = ref(false)
const completeVisible = ref(false)
const cancelVisible = ref(false)
const detailVisible = ref(false)
const activeTask = ref(null)
const taskDetail = ref(null)
const fulfillmentDemands = ref([])
const activeDemand = ref(null)
const demandPagination = reactive({ page: 1, pageSize: 10 })
const demandTotal = ref(0)
const demandStatusTab = ref('pending')
const activeTaskTab = ref('ongoing')

const completedStatuses = ['completed', 'cancelled', 'scrapped', 'partially_scrapped', 'reworked']
const canManageTasks = computed(() => auth.user?.role_code === 'production' && pagePermission.can('create'))
const showTaskTabs = computed(() => !auth.isRole('quality'))
const createForm = reactive({ source_type: 'manual', sales_order_id: null, product_id: null, planned_quantity: 1, fluctuation_ratio: 0, notes: '', material_usages: [], fulfillment_demand_id: null, warehouse_id: null, create_raw_material_demands: false, raw_material_demands: [] })
const finishedWarehouses = ref([])
const purchaseRequestsPanel = ref(null)
const materialRequestForm = reactive({ material_usages: [] })
const completeForm = reactive({ produced_quantity: 0, scrap_quantity: 0, requires_quality: true, preliminary_acceptance_note: '', actual_usages: [] })
const cancelForm = reactive({ reason: '' })

const baseRows = computed(() => rows.value)
const ongoingRows = computed(() => rows.value)
const closedRows = computed(() => rows.value)
const pagination = reactive({ page: 1, pageSize: 10 })
const total = ref(0)
const pageSizes = [10, 20, 50, 100]
const pagedRows = rows
// 分批补货进度(后端 required/fulfilled/pending_quantity;老数据回退推算)
function demandRequired(row) {
  const required = Number(row?.required_quantity || 0)
  if (required > 0) return required
  return Number(row?.fulfilled_quantity || 0) + Number(row?.shortage_quantity || 0)
}

function demandPending(row) {
  const pending = row?.pending_quantity
  if (pending !== null && pending !== undefined) return Number(pending)
  return Math.max(demandRequired(row) - Number(row?.fulfilled_quantity || 0), 0)
}

function demandProgressPercent(row) {
  const required = demandRequired(row)
  if (required <= 0) return 0
  return Math.min(Math.max(Math.round((Number(row?.fulfilled_quantity || 0) / required) * 100), 0), 100)
}

function ensureVisible(predicate) {
  return rows.value.find(predicate) || null
}
const pendingSpecialCount = computed(() => baseRows.value.filter((item) => item.status === 'pending_special_approval').length)
const waitingQualityCount = computed(() => baseRows.value.filter((item) => item.status === 'waiting_quality').length)
const reworkCount = computed(() => baseRows.value.filter((item) => item.source_type === 'rework').length)
const currentBom = computed(() => boms.value.find((item) => item.product_id === createForm.product_id) || null)
const overLimitRows = computed(() => completeForm.actual_usages.filter((item) => Number(item.requested_quantity || 0) > Number(item.allowed_quantity || 0)))
const materialRequestOverLimitRows = computed(() =>
  materialRequestForm.material_usages.filter((item) => Number(item.requested_quantity || 0) > Number(item.allowed_quantity || 0))
)
const availableProducts = computed(() => {
  const productionProducts = products.value.filter((item) => item.fulfillment_mode === 'production')
  if (createForm.source_type !== 'sales_order') {
    return productionProducts
  }
  if (!selectedSalesOrder.value?.items?.length) {
    return []
  }
  const orderProductIds = new Set(selectedSalesOrder.value.items.map((item) => item.product_id))
  return productionProducts.filter((item) => orderProductIds.has(item.id))
})

function plannedQuantityForComplete() {
  return Number(activeTask.value?.planned_quantity || 0)
}

function syncScrapFromProduced() {
  const planned = plannedQuantityForComplete()
  completeForm.produced_quantity = Math.max(0, Math.min(Number(completeForm.produced_quantity || 0), planned))
  completeForm.scrap_quantity = Math.max(0, planned - Number(completeForm.produced_quantity || 0))
}

function syncProducedFromScrap() {
  const planned = plannedQuantityForComplete()
  completeForm.scrap_quantity = Math.max(0, Math.min(Number(completeForm.scrap_quantity || 0), planned))
  completeForm.produced_quantity = Math.max(0, planned - Number(completeForm.scrap_quantity || 0))
}

function resolveTaskTab(row) {
  return completedStatuses.includes(row?.status) ? 'closed' : 'ongoing'
}

function setActiveTaskTabByRow(row) {
  if (!showTaskTabs.value || !row) return
  activeTaskTab.value = resolveTaskTab(row)
}

function sourceLabel(sourceType) {
  return sourceType === 'sales_order' ? '销售订单' : sourceType === 'rework' ? '返工任务' : '手工排产'
}

function canCancel(row) {
  return !['waiting_quality', 'completed', 'scrapped', 'partially_scrapped', 'reworked', 'cancelled'].includes(row.status)
}

function canRequestMaterial(row) {
  return canManageTasks.value && ['pending_material', 'pending_material_approval', 'pending_special_approval'].includes(row.status)
}

function canCompleteTask(row) {
  return canManageTasks.value && row.status === 'material_issued'
}

function canFinishInbound(row) {
  return canManageTasks.value && row.can_finish_inbound
}

function canShowInboundAction(row) {
  return canManageTasks.value && (row.status === 'material_issued' || row.can_finish_inbound)
}

function resetCreateForm() {
  const factory = finishedWarehouses.value.find((item) => item.code === 'LANGFANG_RAW' || item.code === 'WH-RM')
  Object.assign(createForm, { source_type: 'manual', sales_order_id: null, product_id: null, planned_quantity: 1, fluctuation_ratio: 0, notes: '', material_usages: [], fulfillment_demand_id: null, warehouse_id: factory?.id || null, create_raw_material_demands: false, raw_material_demands: [] })
  selectedSalesOrder.value = null
  activeDemand.value = null
}

function openCreate() {
  resetCreateForm()
  createVisible.value = true
}

function openStocks() {
  router.push('/inventory/stocks')
}

function handleSourceChange(value) {
  if (value !== 'sales_order') {
    createForm.sales_order_id = null
    selectedSalesOrder.value = null
    buildCreateMaterialUsages()
    return
  }
  createForm.product_id = null
  createForm.planned_quantity = 1
}

async function handleSalesOrderChange(orderId) {
  if (!orderId) {
    selectedSalesOrder.value = null
    createForm.product_id = null
    createForm.planned_quantity = 1
    return
  }
  selectedSalesOrder.value = await get(`/api/sales/orders/${orderId}`)
  createForm.product_id = selectedSalesOrder.value?.items?.[0]?.product_id || null
  syncPlannedQuantity()
}

function syncPlannedQuantity() {
  if (createForm.source_type !== 'sales_order' || !selectedSalesOrder.value?.items?.length) return
  const matchedItem = selectedSalesOrder.value.items.find((item) => item.product_id === createForm.product_id)
  if (matchedItem) {
    createForm.planned_quantity = Number(matchedItem.quantity || 1)
  }
}

function buildCreateMaterialUsages() {
  if (!currentBom.value?.items?.length || !createForm.product_id) {
    createForm.material_usages = []
    return
  }
  const plannedQuantity = Number(createForm.planned_quantity || 0)
  const fluctuationRatio = Number(createForm.fluctuation_ratio || 0)
  createForm.material_usages = currentBom.value.items.map((item) => {
    const planned = Number(item.standard_quantity || 0) * plannedQuantity
    return {
      material_id: item.material_id,
      material_name: item.material_name,
      standard_quantity: Number(item.standard_quantity || 0),
      planned_quantity: planned,
      allowed_quantity: planned * (1 + fluctuationRatio),
      requested_quantity: planned,
      reason: ''
    }
  })
}

function rowClassName({ row }) {
  if (!focusedTaskId.value) return ''
  const focusType = String(route.query.focusType || '')
  if (focusType === 'quality_task') {
    return row.quality_task_id === focusedTaskId.value ? 'focused-row' : ''
  }
  return row.id === focusedTaskId.value ? 'focused-row' : ''
}

function canCreateDemandTask(row) {
  if (row?.max_producible_quantity === undefined || row?.max_producible_quantity === null) return true
  return Number(row.max_producible_quantity) > 0
}

async function applyFocusFromRoute() {
  const focusType = String(route.query.focusType || '')
  const focusId = Number(route.query.focusId || 0)
  if (focusType === 'fulfillment_demand' && focusId) {
    const matchedDemand = fulfillmentDemands.value.find((item) => Number(item.id) === focusId)
    if (matchedDemand && !createVisible.value) {
      prefillDemand(matchedDemand)
    }
    return
  }
  if (focusType === 'low_stock_replenishment' && focusId) {
    const matchedDemand = fulfillmentDemands.value.find((item) => Number(item.low_stock_todo_id) === focusId || String(item.id) === `low-${focusId}`)
    if (matchedDemand && !createVisible.value) {
      prefillDemand(matchedDemand)
    }
    return
  }
  await applyRouteFocus(
    (focusId) => {
      if (focusType === 'quality_task') {
        const matched = baseRows.value.find((item) => item.quality_task_id === focusId) || rows.value.find((item) => item.quality_task_id === focusId)
        if (matched) {
          setActiveTaskTabByRow(matched)
          ensureVisible((item) => item.id === matched.id)
        }
        return matched
      }
      const matched = baseRows.value.find((item) => item.id === focusId) || rows.value.find((item) => item.id === focusId)
      if (matched) {
        setActiveTaskTabByRow(matched)
        ensureVisible((item) => item.id === matched.id)
      }
      return matched
    },
    async (row) => {
      await viewTask(row)
    }
  )
}

async function load() {
  const taskStatus = showTaskTabs.value && activeTaskTab.value === 'closed' ? 'closed' : 'ongoing'
  const taskData = await get(`/api/production/tasks?task_status=${taskStatus}&page=${pagination.page}&page_size=${pagination.pageSize}`)
  rows.value = taskData.items || []
  total.value = Number(taskData.pagination?.total || 0)
  if (canManageTasks.value) {
    const demandData = await get(`/api/production/fulfillment-demands?status_group=${demandStatusTab.value}&page=${demandPagination.page}&page_size=${demandPagination.pageSize}`)
    fulfillmentDemands.value = demandData.items || demandData || []
    demandTotal.value = Number(demandData.pagination?.total || demandData.total || 0)
  } else {
    fulfillmentDemands.value = []
  }
  await applyFocusFromRoute()
}

async function handleTaskPaginationChange({ page, pageSize }) {
  pagination.page = page
  pagination.pageSize = pageSize
  await load()
}

async function handleDemandPaginationChange({ page, pageSize }) {
  demandPagination.page = page
  demandPagination.pageSize = pageSize
  await load()
}

async function handleDemandStatusChange() {
  demandPagination.page = 1
  await load()
}

async function loadTaskDetail(id) {
  taskDetail.value = await get(`/api/production/tasks/${id}`)
}

async function createTask() {
  if (!createForm.material_usages.length) {
    ElMessage.warning('请先选择已配置 BOM 的产品')
    return
  }
  // 转任务时原料不足(部分生产)→ 询问是否提交原料补货需求
  // 缺口按"剩余需求数量"计算:剩余部分 = 需求总量 - 本次生产量
  if (createForm.fulfillment_demand_id && activeDemand.value) {
    const demandShortage = Number(activeDemand.value.shortage_quantity || 0)
    const planned = Number(createForm.planned_quantity || 0)
    if (planned < demandShortage) {
      const remaining = demandShortage - planned
      try {
        const shortageRows = await get(`/api/production/raw-material-shortage?product_id=${createForm.product_id}&planned_quantity=${remaining}`)
        const shortageItems = (shortageRows || []).filter((it) => Number(it.shortage_quantity || 0) > 0)
        if (shortageItems.length) {
          openRawDemandConfirm('with_task', shortageItems)
          return
        }
      } catch (e) {
        // 查询失败不阻塞创建
      }
    }
  }
  await createTaskDirect()
}

async function createTaskDirect() {
  try {
    const result = await post('/api/production/tasks', {
      ...createForm,
      material_usages: createForm.material_usages.map((item) => ({
        material_id: item.material_id,
        requested_quantity: item.requested_quantity,
        reason: item.reason
      }))
    })
    try {
      if (activeDemand.value?.low_stock_todo_id) {
        await post(`/api/dashboard/todos/${activeDemand.value.low_stock_todo_id}/done`)
      }
    } catch (e) {
      // 待办已完成等并发场景不影响任务创建结果
      console.warn('low_stock todo done failed', e)
    }
    ElMessage.success(result?.task_no ? `已转生产任务，任务编号 ${result.task_no}` : '生产任务已创建')
    createVisible.value = false
    resetCreateForm()
    activeTaskTab.value = 'ongoing'
    focusedTaskId.value = null
    if (route.query.focusId || route.query.focusType) {
      router.replace({ path: '/production/tasks', query: {} })
    }
    await load()
  } catch (error) {
    ElMessage.error(error?.message || '创建生产任务失败')
  }
}

const rawDemandConfirmVisible = ref(false)
const rawDemandSubmitting = ref(false)
const rawDemandMode = ref('with_task')
const rawDemandItems = ref([])

function openRawDemandConfirm(mode, shortageItems) {
  rawDemandMode.value = mode
  rawDemandItems.value = (shortageItems || []).map((it) => ({
    material_id: it.material_id,
    material_name: it.material_name,
    required_quantity: Number(it.required_quantity || 0),
    available_quantity: Number(it.available_quantity || 0),
    shortage_quantity: Number(it.shortage_quantity || 0),
    demand_quantity: Number(it.shortage_quantity || 0),
  }))
  rawDemandConfirmVisible.value = true
}

async function confirmRawDemand() {
  if (rawDemandSubmitting.value) return
  const items = rawDemandItems.value
    .filter((it) => Number(it.demand_quantity || 0) > 0)
    .map((it) => ({ material_id: it.material_id, shortage_quantity: Number(it.demand_quantity || 0) }))
  if (!items.length) {
    ElMessage.warning('请填写补货数量')
    return
  }
  rawDemandSubmitting.value = true
  try {
    if (rawDemandMode.value === 'standalone') {
      const resp = await post(`/api/production/fulfillment-demands/${activeDemand.value.id}/raw-material-demands`, { items })
      ElMessage.success(resp?.msg || '原料补货需求已提交')
      rawDemandConfirmVisible.value = false
      await load()
    } else {
      createForm.create_raw_material_demands = true
      createForm.raw_material_demands = items
      rawDemandConfirmVisible.value = false
      await createTaskDirect()
    }
  } catch (e) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    rawDemandSubmitting.value = false
  }
}

function prefillDemand(row) {
  if (row.source_type === 'low_stock_replenishment' || String(row.id).startsWith('low-')) {
    prefillLowStockDemand(row)
    return
  }
  openMaterialProgress(row)
}

async function openMaterialProgress(row) {
  resetCreateForm()
  activeDemand.value = row
  materialProgressSubmitting.value = false
  try {
    const progress = await get(`/api/production/fulfillment-demands/${row.id}/material-progress`)
    materialProgressData.value = progress
    // 已入库数量已包含在库存中，到货但未入库的数量不可用于生产。
    materialProgressItems.value = (progress.items || []).map((item) => {
      const stockAvailable = Number(item.stock_available_quantity || 0)
      const required = Number(item.required_quantity || 0)
      const shortage = Number(item.remaining_replenishment_quantity || 0)
      return {
        ...item,
        shortage,
        suggest_quantity: progress.can_edit_replenishment ? shortage : 0,
        satisfied: required <= stockAvailable,
      }
    })
    materialProgressVisible.value = true
  } catch (e) {
    // 老数据无 BOM 等场景退回原逻辑
    prefillLowStockDemand(row)
  }
}

const materialProgressVisible = ref(false)
const materialProgressSubmitting = ref(false)
const materialProgressData = ref(null)
const materialProgressItems = ref([])

function materialProgressTag(progress) {
  return { pending: 'info', ordered: 'primary', arrived: 'warning', done: 'success' }[progress] || 'info'
}

function materialProgressLabel(item) {
  if (item.satisfied) return '已满足'
  if (Number(item.submitted_quantity || 0) > 0) return item.progress_label || '采购中'
  if (Number(item.suggest_quantity || 0) > 0) return '待提交补货'
  return item.progress_label || '待处理'
}

async function submitMaterialProgressDemands() {
  if (materialProgressSubmitting.value) return
  const items = materialProgressItems.value
    .filter((it) => Number(it.suggest_quantity || 0) > 0)
    .map((it) => ({ material_id: it.material_id, shortage_quantity: Number(it.suggest_quantity || 0) }))
  if (!items.length) {
    ElMessage.warning('请先填写需要补货的原料数量')
    return
  }
  materialProgressSubmitting.value = true
  try {
    const resp = await post(`/api/production/fulfillment-demands/${activeDemand.value.id}/raw-material-demands`, { items })
    ElMessage.success(resp?.msg || '原料补货需求已提交')
    materialProgressVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    materialProgressSubmitting.value = false
  }
}

function closeMaterialProgressAndCreateTask() {
  materialProgressVisible.value = false
  openCreateTaskDialog(activeDemand.value)
}

function prefillLowStockDemand(row) {
  resetCreateForm()
  activeDemand.value = row
  if (!canCreateDemandTask(row)) {
    // 原料完全不足:询问是否提交原料补货需求(需求标记待补料,原料到货后再转任务)
    const shortageQuantity = Number(row.shortage_quantity || 1)
    get(`/api/production/raw-material-shortage?product_id=${row.product_id}&planned_quantity=${shortageQuantity}`)
      .then((shortageRows) => {
        const shortageItems = (shortageRows || []).filter((it) => Number(it.shortage_quantity || 0) > 0)
        if (shortageItems.length) {
          openRawDemandConfirm('standalone', shortageItems)
        } else {
          ElMessage.warning('当前原料库存暂时无法支持生产，请先处理采购补货待办')
        }
      })
      .catch(() => ElMessage.warning('当前原料库存暂时无法支持生产，请先处理采购补货待办'))
    return
  }
  openCreateTaskDialog(row)
}

async function openCreateTaskDialog(row) {
  resetCreateForm()
  activeDemand.value = row
  createForm.source_type = row.sales_order_id ? 'sales_order' : 'manual'
  createForm.sales_order_id = row.sales_order_id || null
  // source_type 置为 sales_order 时必须同时载入订单详情:
  // availableProducts 在该模式下要用 selectedSalesOrder.items 过滤产品,
  // 不载入就恒为空数组 —— "产品"下拉显示 no data,任务根本建不了。
  if (createForm.sales_order_id) {
    try {
      selectedSalesOrder.value = await get(`/api/sales/orders/${createForm.sales_order_id}`)
    } catch (error) {
      // 订单详情取不到时退回手工排产,至少让全部产品可选,不要卡死流程
      selectedSalesOrder.value = null
      createForm.source_type = 'manual'
    }
  }
  createForm.product_id = row.product_id
  const shortageQuantity = Number(row.shortage_quantity || 1)
  // 计划数量默认取"补货需求量";原料不足时才压到最大可产。
  // 原先无条件 Math.min(缺口, 最大可产),而最大可产曾因取值 bug 恒为 0,
  // 结果默认值被 el-input-number 的 :min 顶成 0.01。
  const maxProducibleQuantity = Number(row.max_producible_quantity || 0)
  createForm.planned_quantity = maxProducibleQuantity > 0
    ? Math.min(shortageQuantity, maxProducibleQuantity)
    : shortageQuantity
  createForm.fulfillment_demand_id = row.source_type === 'low_stock_replenishment' ? null : row.id
  buildCreateMaterialUsages()
  createVisible.value = true
}

const editDemandVisible = ref(false)
const editDemandSubmitting = ref(false)
const editDemandForm = reactive({ id: null, demand_no: '', product_name: '', shortage_quantity: 0, note: '' })

const DEMAND_PROGRESS_TAGS = {
  pending: 'warning', purchasing: 'primary', ordered: 'primary', receiving: 'primary',
  pending_payment: 'danger', raw_purchasing: 'warning', producing: 'primary',
  pending_inbound: 'warning', completed: 'success', cancelled: 'info',
}
function demandProgressTag(status) {
  return DEMAND_PROGRESS_TAGS[status] || 'info'
}

function openEditDemand(row) {
  Object.assign(editDemandForm, {
    id: row.id,
    demand_no: row.demand_no,
    product_name: row.product_name,
    shortage_quantity: Number(row.shortage_quantity || 0),
    note: '',
  })
  editDemandVisible.value = true
}

async function submitEditDemand() {
  if (!editDemandForm.id || editDemandForm.shortage_quantity <= 0) {
    ElMessage.warning('请填写大于 0 的缺口数量')
    return
  }
  editDemandSubmitting.value = true
  try {
    await put(`/api/production/fulfillment-demands/${editDemandForm.id}`, {
      shortage_quantity: editDemandForm.shortage_quantity,
      note: editDemandForm.note || null,
    })
    ElMessage.success('补货需求数量已调整')
    editDemandVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '调整失败')
  } finally {
    editDemandSubmitting.value = false
  }
}

async function cancelDemand(row) {
  const reason = window.prompt('请输入取消原因')
  if (!reason || reason.trim().length < 2) {
    ElMessage.warning('请填写有效的取消原因')
    return
  }
  await post(`/api/production/fulfillment-demands/${row.id}/cancel`, { reason: reason.trim() })
  ElMessage.success('补货需求已取消')
  demandPagination.page = 1
  await load()
}

async function openMaterialRequest(row) {
  try {
    activeTask.value = row
    await loadTaskDetail(row.id)
    materialRequestForm.material_usages = (taskDetail.value?.usages || []).map((item) => ({
      material_id: item.material_id,
      material_name: item.material_name,
      planned_quantity: item.planned_quantity,
      allowed_quantity: item.allowed_quantity,
      available_quantity: item.available_quantity,
      inventory_approval_status: item.inventory_approval_status,
      inventory_approval_status_label: item.inventory_approval_status_label,
      special_request_status: item.special_request_status,
      special_request_status_label: item.special_request_status_label,
      requested_quantity: Number(item.requested_quantity || item.planned_quantity || 0),
      reason: item.reason || ''
    }))
    if (!materialRequestForm.material_usages.length) {
      ElMessage.warning('当前任务缺少 BOM 原料明细，无法发起领料申请')
      return
    }
    materialRequestVisible.value = true
  } catch (error) {
    ElMessage.error(error.message || '打开领料申请失败')
  }
}

async function viewTask(row) {
  activeTask.value = row
  setActiveTaskTabByRow(row)
  await loadTaskDetail(row.id)
  detailVisible.value = true
}

async function openComplete(row) {
  activeTask.value = row
  await loadTaskDetail(row.id)
  completeForm.produced_quantity = Number(row.planned_quantity || 0)
  completeForm.scrap_quantity = 0
  completeForm.requires_quality = true
  completeForm.preliminary_acceptance_note = taskDetail.value?.preliminary_acceptance_note || ''
  completeForm.actual_usages = (taskDetail.value?.usages || []).map((item) => ({
    material_id: item.material_id,
    material_name: item.material_name,
    planned_quantity: item.planned_quantity,
    allowed_quantity: item.allowed_quantity,
    approved_quantity: item.approved_quantity,
    available_quantity: item.available_quantity,
    special_request_status: item.special_request_status,
    requested_quantity: Number(item.approved_quantity || item.planned_quantity || 0),
    reason: item.reason || ''
  }))
  completeVisible.value = true
}

function openCancel(row) {
  activeTask.value = row
  cancelForm.reason = ''
  cancelVisible.value = true
}

async function submitSpecialRequest() {
  if (!overLimitRows.value.length) return
  await post(`/api/production/tasks/${activeTask.value.id}/special-requests`, {
    actual_usages: overLimitRows.value.map((item) => ({
      material_id: item.material_id,
      requested_quantity: item.requested_quantity,
      reason: item.reason
    }))
  })
  ElMessage.success('特殊领用审批已提交，请等待经理处理')
  await loadTaskDetail(activeTask.value.id)
  completeForm.actual_usages = (taskDetail.value?.usages || []).map((item) => ({
    material_id: item.material_id,
    material_name: item.material_name,
    planned_quantity: item.planned_quantity,
    allowed_quantity: item.allowed_quantity,
    approved_quantity: item.approved_quantity,
    available_quantity: item.available_quantity,
    special_request_status: item.special_request_status,
    requested_quantity: Number(item.requested_quantity || item.planned_quantity || 0),
    reason: item.reason || ''
  }))
  await load()
}

async function submitMaterialRequest() {
  try {
    const result = await post(`/api/production/tasks/${activeTask.value.id}/material-request`, {
      material_usages: materialRequestForm.material_usages.map((item) => ({
        material_id: item.material_id,
        requested_quantity: item.requested_quantity,
        reason: item.reason
      }))
    })
    ElMessage.success(result.special_count ? '领料申请已提交，超额明细已同步经理和原料库管审批' : '领料申请已提交，等待原料库审批')
    materialRequestVisible.value = false
    if (detailVisible.value && taskDetail.value?.id === activeTask.value.id) {
      await loadTaskDetail(activeTask.value.id)
    }
    await load()
  } catch (error) {
    ElMessage.error(error.message || '提交领料申请失败')
  }
}

async function completeTask() {
  await post(`/api/production/tasks/${activeTask.value.id}/complete`, {
    produced_quantity: completeForm.produced_quantity,
    scrap_quantity: completeForm.scrap_quantity,
    requires_quality: completeForm.requires_quality,
    preliminary_acceptance_note: completeForm.preliminary_acceptance_note,
    // 不需质检时不传指定质检员,避免无意义的指派
    actual_usages: completeForm.actual_usages.map((item) => ({
      material_id: item.material_id,
      requested_quantity: item.requested_quantity,
      reason: item.reason
    }))
  })
  ElMessage.success(completeForm.requires_quality ? '入库申请已提交并流转至质检' : '入库申请已提交，等待库管执行入库')
  completeVisible.value = false
  activeTaskTab.value = 'ongoing'
  await load()
}

async function finishInbound(row) {
  await post(`/api/production/tasks/${row.id}/inbound`, {})
  ElMessage.success('成品入库申请已提交，等待库管执行手工入库')
  if (detailVisible.value && taskDetail.value?.id === row.id) {
    await loadTaskDetail(row.id)
  }
  await load()
}

function handleInboundAction(row) {
  if (row.status === 'material_issued') {
    openComplete(row)
    return
  }
  finishInbound(row)
}

async function cancelTask() {
  await post(`/api/production/tasks/${activeTask.value.id}/cancel`, {
    reason: cancelForm.reason
  })
  ElMessage.success('生产任务已取消')
  cancelVisible.value = false
  activeTaskTab.value = 'closed'
  if (detailVisible.value && taskDetail.value?.id === activeTask.value.id) {
    await loadTaskDetail(activeTask.value.id)
  }
  await load()
}

watch(
  () => [route.query.focusId, route.query.focusType],
  async () => {
    await applyFocusFromRoute()
  }
)

watch(activeTaskTab, async () => {
  pagination.page = 1
  await load()
})

watch(
  () => [createForm.product_id, createForm.planned_quantity, createForm.fluctuation_ratio],
  () => {
    buildCreateMaterialUsages()
  }
)

/**
 * 生产成品目标仓库：默认廊坊工厂仓，也可选沧州仓库。
 *
 * 原先 onMounted 里直接调用 loadFinishedWarehouses(),但全文没有定义它 ——
 * Promise.all([...]) 在构造数组时就抛 ReferenceError,
 * 连带同一行的 load() 等调用都不执行,
 * 于是"目标仓库"和"指定质检员"两个下拉都是 no data。
 */
async function loadFinishedWarehouses() {
  try {
    const res = await get('/api/master-data/warehouses')
    const rows = Array.isArray(res) ? res : (res?.items || [])
    finishedWarehouses.value = rows.filter((item) => ['raw', 'finished'].includes(item.warehouse_type || item.type))
    const factory = finishedWarehouses.value.find((item) => item.code === 'LANGFANG_RAW' || item.code === 'WH-RM')
    if (factory && !createForm.warehouse_id) createForm.warehouse_id = factory.id
  } catch (error) {
    finishedWarehouses.value = []
  }
}

onMounted(async () => {
  const productData = await get('/api/master-data/products?active_only=true')
  products.value = Array.isArray(productData) ? productData : (productData?.items || [])
  boms.value = await get('/api/production/boms')
  // 该接口返回 {items, pagination} 对象:原先直接赋值会让下拉框显示两条 undefined。
  // 且默认 tab 只取草稿,生产要关联的是已审批在流转的单,故取 all。
  const orderData = await get('/api/sales/orders?tab=all&page=1&page_size=100')
  salesOrders.value = Array.isArray(orderData) ? orderData : (orderData.items || [])
  await Promise.all([loadFinishedWarehouses(), load()])
})
</script>

<style scoped>
.demand-related { font-size: 12px; color: #909399; line-height: 18px; }
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.text-warning { color: #e6a23c; }
.status-tabs { margin-bottom: 12px; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.toolbar-tip { font-size: 12px; color: #909399; font-weight: 400; }
.footer-actions { display: flex; align-items: center; justify-content: space-between; width: 100%; }
.footer-tip { color: #e6a23c; font-size: 13px; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
.focused-row > td { background: #f0f9eb !important; }
.flow-steps { display: flex; flex-wrap: wrap; gap: 8px; }
.flow-step { padding: 4px 10px; border-radius: 999px; background: #f4f4f5; color: #909399; font-size: 12px; }
.flow-step.is-done { background: #f0f9eb; color: #67c23a; }
.flow-step.is-current { background: #ecf5ff; color: #409eff; font-weight: 600; }
.flow-tooltip-content { max-width: 280px; white-space: pre-line; line-height: 1.6; }

@media (max-width: 768px) {
  .footer-actions,
  .detail-toolbar {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 8px;
  }

  .footer-actions > * {
    max-width: 100%;
  }

  .flow-steps {
    gap: 6px;
  }

  .flow-step {
    white-space: normal;
  }
}

.demand-progress { display: flex; flex-direction: column; gap: 4px; }
.demand-progress-text { font-size: 12px; color: var(--el-text-color-regular); line-height: 1.4; }
.demand-progress-pending { color: var(--el-color-warning); margin-left: 4px; }
</style>
