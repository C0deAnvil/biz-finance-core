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
        <el-button v-if="canManageTasks" type="primary" @click="openCreate">新增任务</el-button>
      </template>

      <el-tabs v-if="showTaskTabs" v-model="activeTaskTab" class="status-tabs">
        <el-tab-pane :label="`进行中 (${ongoingRows.length})`" name="ongoing" />
        <el-tab-pane :label="`已完工/已关闭 (${closedRows.length})`" name="closed" />
      </el-tabs>

      <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
        <el-table-column prop="task_no" label="任务号" width="150" />
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
      <el-table :data="fulfillmentDemands" stripe>
        <el-table-column prop="demand_no" label="需求号" width="150" />
        <el-table-column prop="source_type" label="来源" width="130">
          <template #default="scope">{{ scope.row.source_type === 'low_stock_replenishment' ? '安全库存补货' : '客户订单缺货' }}</template>
        </el-table-column>
        <el-table-column prop="sales_order_no" label="客户订单号" width="160">
          <template #default="scope">{{ scope.row.sales_order_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="product_name" label="产品" min-width="180" />
        <el-table-column prop="shortage_quantity" label="缺口数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.shortage_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="120">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="process_note" label="处理说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="prefillDemand(scope.row)">转建任务</el-button>
            <el-button size="small" text type="danger" @click="cancelDemand(scope.row)">取消创建</el-button>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

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
          <el-select v-model="createForm.product_id" style="width: 100%" @change="syncPlannedQuantity">
            <el-option v-for="item in availableProducts" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="计划数量"><el-input-number v-model="createForm.planned_quantity" :min="0.01" style="width: 100%" /></el-form-item>
        <el-form-item label="浮动比例"><el-input-number v-model="createForm.fluctuation_ratio" :min="0" :max="1" :step="0.01" style="width: 100%" /></el-form-item>
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
        <el-descriptions-item label="成品仓">{{ taskDetail.finished_warehouse?.name || '-' }}</el-descriptions-item>
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
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import { useRouteFocus } from '../../composables/useRouteFocus'
import { useLocalPagination } from '../../composables/useLocalPagination'
import PageSection from '../../components/PageSection.vue'
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
const activeTaskTab = ref('ongoing')

const completedStatuses = ['completed', 'cancelled', 'scrapped', 'partially_scrapped', 'reworked']
const canManageTasks = computed(() => auth.user?.role_code === 'production' && pagePermission.can('create'))
const showTaskTabs = computed(() => auth.user?.role_code !== 'quality')
const createForm = reactive({ source_type: 'manual', sales_order_id: null, product_id: null, planned_quantity: 1, fluctuation_ratio: 0, notes: '', material_usages: [], fulfillment_demand_id: null })
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
  if (createForm.source_type !== 'sales_order') {
    return products.value
  }
  if (!selectedSalesOrder.value?.items?.length) {
    return []
  }
  const orderProductIds = new Set(selectedSalesOrder.value.items.map((item) => item.product_id))
  return products.value.filter((item) => orderProductIds.has(item.id))
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
  Object.assign(createForm, { source_type: 'manual', sales_order_id: null, product_id: null, planned_quantity: 1, fluctuation_ratio: 0, notes: '', material_usages: [], fulfillment_demand_id: null })
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
    fulfillmentDemands.value = await get('/api/production/fulfillment-demands')
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

async function loadTaskDetail(id) {
  taskDetail.value = await get(`/api/production/tasks/${id}`)
}

async function createTask() {
  if (!createForm.material_usages.length) {
    ElMessage.warning('请先选择已配置 BOM 的产品')
    return
  }
  const result = await post('/api/production/tasks', {
    ...createForm,
    material_usages: createForm.material_usages.map((item) => ({
      material_id: item.material_id,
      requested_quantity: item.requested_quantity,
      reason: item.reason
    }))
  })
  if (createForm.fulfillment_demand_id) {
    await post(`/api/production/fulfillment-demands/${createForm.fulfillment_demand_id}/link-task/${result.id}`)
  } else if (activeDemand.value?.low_stock_todo_id) {
    await post(`/api/dashboard/todos/${activeDemand.value.low_stock_todo_id}/done`)
  }
  ElMessage.success('生产任务已创建')
  createVisible.value = false
  resetCreateForm()
  activeTaskTab.value = 'ongoing'
  focusedTaskId.value = null
  if (route.query.focusId || route.query.focusType) {
    router.replace({ path: '/production/tasks', query: {} })
  }
  await load()
}

function prefillDemand(row) {
  resetCreateForm()
  activeDemand.value = row
  createForm.product_id = row.product_id
  createForm.planned_quantity = Number(row.shortage_quantity || 1)
  createForm.fulfillment_demand_id = row.source_type === 'low_stock_replenishment' ? null : row.id
  buildCreateMaterialUsages()
  createVisible.value = true
}

async function cancelDemand(row) {
  const reason = window.prompt('请输入取消原因')
  if (!reason || reason.trim().length < 2) {
    ElMessage.warning('请填写有效的取消原因')
    return
  }
  await post(`/api/production/fulfillment-demands/${row.id}/cancel`, { reason: reason.trim() })
  ElMessage.success('补货需求已取消')
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

onMounted(async () => {
  products.value = await get('/api/master-data/products')
  boms.value = await get('/api/production/boms')
  salesOrders.value = await get('/api/sales/orders')
  await load()
})
</script>

<style scoped>
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
</style>
