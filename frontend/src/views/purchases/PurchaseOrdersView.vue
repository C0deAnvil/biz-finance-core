<template>
  <PageSection title="采购订单" description="支持采购建单、编辑作废、阈值审批与合同附件维护。新建时付款方式仅区分先到货后付款和先付款后到货。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
      <el-button v-if="canManageSettings" plain @click="thresholdVisible = true">采购金额阈值：{{ formatNumber(purchaseApprovalThreshold) }}</el-button>
      <el-button v-if="canManageOrders" type="primary" @click="openCreate">新增采购单</el-button>
    </template>

    <el-tabs v-model="activeStatusTab" class="status-tabs">
      <el-tab-pane :label="`执行中/待付款 (${activeOrderRows.length})`" name="active" />
      <el-tab-pane :label="`已完成 (${completedOrderRows.length})`" name="completed" />
    </el-tabs>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName">
      <el-table-column prop="order_no" label="采购单号" width="150">
        <template #default="scope">
          <span :style="scope.row.order_type_color ? { color: scope.row.order_type_color, fontWeight: 600 } : {}">{{ scope.row.order_no }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="supplier_name" label="供应商" min-width="180" />
      <el-table-column prop="operator_name" label="经办人" width="120" />
      <el-table-column prop="payment_type_label" label="付款方式" min-width="160" />
      <el-table-column prop="expected_arrival_date" label="预计到货日期" width="140"><template #default="scope">{{ scope.row.expected_arrival_date || '-' }}</template></el-table-column>
      <el-table-column prop="target_warehouse_name" label="目标仓库" width="130" />
      <el-table-column prop="status_label" label="状态" width="140">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="fund_status_label" label="结算状态" width="140" />
      <el-table-column prop="total_amount" label="总金额" width="130"><template #default="scope">{{ formatMoney(scope.row.total_amount) }}</template></el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="scope">
          <div class="row-actions">
            <el-button size="small" text type="primary" @click="viewOrder(scope.row.id)">查看</el-button>
            <el-button v-if="canConfirmPurchase(scope.row)" size="small" text type="success" @click="openPurchaseConfirm(scope.row)">确认采购</el-button>
            <el-button v-if="canManageOrders" size="small" text type="primary" @click="editOrder(scope.row)" :disabled="!canEdit(scope.row)">编辑</el-button>
            <el-button v-if="canManageOrders" size="small" text type="danger" @click="cancelOrder(scope.row)" :disabled="!canCancel(scope.row)">作废</el-button>
            <el-button v-if="canManageOrders" size="small" text type="success" @click="openAttachments(scope.row)">合同/发票</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
    <AppPagination
      v-model:page="pagination.page"
      v-model:page-size="pagination.pageSize"
      :total="total"
      :page-sizes="pageSizes"
      @change="handleOrderPaginationChange"
    />

    <PageSection v-if="canManageOrders" title="待采购补货需求" description="贸易型产品库存不足、生产领料原料库存不足时，会在此生成待采购需求。">
      <el-tabs v-model="demandStatusTab" class="status-tabs">
        <el-tab-pane :label="`待完成 (${pendingFulfillmentDemands.length})`" name="pending" />
        <el-tab-pane :label="`已完成 (${completedFulfillmentDemands.length})`" name="completed" />
      </el-tabs>
      <div class="demand-toolbar">
        <span class="demand-toolbar-tip">勾选多条需求可合并生成一个采购订单（相同原料/产品数量自动汇总）</span>
        <el-button v-if="demandStatusTab === 'pending'" size="small" type="primary" :disabled="!selectedDemands.length" @click="mergeDemands">合并转建采购单({{ selectedDemands.length }})</el-button>
      </div>
      <el-table :data="visibleFulfillmentDemands" stripe @selection-change="selectedDemands = $event">
        <el-table-column v-if="demandStatusTab === 'pending'" type="selection" width="45" />
        <el-table-column prop="demand_no" label="需求号" width="150">
          <template #default="scope">
            <span :style="scope.row.order_type_color ? { color: scope.row.order_type_color, fontWeight: 600 } : {}">{{ scope.row.demand_no }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="source_type" label="来源" width="140">
          <template #default="scope">{{ demandSourceLabel(scope.row) }}</template>
        </el-table-column>
        <el-table-column prop="submitted_by_name" label="提交人" width="110">
          <template #default="scope">{{ scope.row.submitted_by_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="sales_order_no" label="来源单号" width="160">
          <template #default="scope">{{ scope.row.sales_order_no || scope.row.production_task_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="item_name" label="产品/原料" min-width="180">
          <template #default="scope">{{ scope.row.item_name || scope.row.product_name || scope.row.material_name || '-' }}</template>
        </el-table-column>
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
        <el-table-column label="短缺等级" width="110">
          <template #default="scope">
            <el-tag v-if="scope.row.shortage_level === 'green'" type="success" effect="dark">绿·充足</el-tag>
            <el-tag v-else-if="scope.row.shortage_level === 'yellow'" type="warning" effect="dark">黄·部分不足</el-tag>
            <el-tag v-else-if="scope.row.shortage_level === 'red'" type="danger" effect="dark">红·完全不足</el-tag>
            <el-tag v-else type="info" effect="plain">-</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" width="100">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag>
          </template>
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
            <el-button size="small" text type="info" @click="viewDemand(scope.row)">详情</el-button>
            <el-button v-if="demandStatusTab === 'pending'" size="small" text type="primary" @click="prefillDemand(scope.row)">转建采购单</el-button>
            <el-button v-if="scope.row.status === 'pending' && scope.row.demand_type !== 'raw_material' && scope.row.source_type !== 'production_material_replenishment'" size="small" text type="danger" @click="cancelDemand(scope.row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑采购单' : (mergeMode ? '合并转建采购单' : '新增采购单')" width="980px">
      <el-alert v-if="activeDemand" type="warning" :closable="false" show-icon class="mb-12">
        <template #title>
          补货需求：{{ activeDemand.item_name || activeDemand.product_name || activeDemand.material_name || '-' }}，缺口 {{ formatNumber(activeDemand.shortage_quantity) }}；
          安全库存 {{ formatNumber(activeDemand.safety_stock_qty) }}；
          预计可用库存 {{ formatNumber(activeDemand.expected_available_quantity) }}
        </template>
      </el-alert>
      <el-form :model="form" label-width="90px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="供应商">
              <el-select v-model="form.supplier_id" filterable style="width: 100%" @change="handleSupplierChange">
                <el-option v-for="item in supplierOptionsForCurrentItems" :key="item.id" :label="supplierOptionLabel(item)" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="付款方式">
              <el-select v-model="form.payment_type" style="width: 100%">
                <el-option label="先到货后付款" value="先到货后付款" />
                <el-option label="先付款后到货" value="先付款后到货" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="经办人">
              <el-select v-model="form.operator_user_id" filterable style="width: 100%">
                <el-option v-for="item in purchaseUsers" :key="item.id" :label="`${item.full_name} / ${item.username}`" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="预计到货日期" required>
              <el-date-picker v-model="form.expected_arrival_date" type="date" value-format="YYYY-MM-DD" placeholder="请选择预计到货日期" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="目标仓库" required>
              <el-select v-model="form.target_warehouse_id" placeholder="选择采购到货仓库" style="width: 100%">
                <el-option v-for="warehouse in warehouses" :key="warehouse.id" :value="warehouse.id" :label="warehouse.name" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-alert
          v-if="supplierAiTip"
          :type="supplierAiTip.type"
          :closable="false"
          show-icon
          class="mb-12"
        >
          <template #title>{{ supplierAiTip.title }}</template>
          <div v-if="supplierAi" class="ai-summary">
            <div class="ai-summary-head">
              <span>供应商评级</span>
              <el-tag :type="supplierAi.analysis.grade_type">{{ supplierAi.analysis.predicted_grade }}级</el-tag>
              <span>当前风险</span>
              <el-tag :type="supplierAi.analysis.risk_type">{{ supplierAi.analysis.risk_label }}</el-tag>
              <span>BiLSTM时序风险</span>
              <el-tag :type="supplierAi.analysis.sequence_risk_type">{{ supplierAi.analysis.sequence_risk_label }}</el-tag>
              <span>趋势：{{ supplierAi.analysis.risk_trend || '平稳' }}</span>
            </div>
            <div class="ai-summary-body">
              <span>风险指数 {{ percent(supplierAi.analysis.risk_score) }}</span>
              <span>时序风险 {{ percent(supplierAi.analysis.sequence_risk_score) }}</span>
              <span>主要原因：{{ (supplierAi.analysis.risk_reasons || [])[0] || '当前履约表现整体稳定' }}</span>
            </div>
            <div class="ai-summary-body" v-if="supplierAi.analysis.action_suggestions?.length">
              <span>建议：{{ supplierAi.analysis.action_suggestions[0] }}</span>
            </div>
          </div>
        </el-alert>

        <div class="detail-toolbar">
          <span>采购明细</span>
          <el-button v-if="canManageOrders" size="small" @click="addItem">新增明细</el-button>
        </div>
        <el-table :data="form.items" border>
          <el-table-column label="产品/原料/包装" min-width="220">
            <template #default="scope">
              <el-select v-model="scope.row.type" style="width: 110px; margin-right: 8px" :disabled="!canManageOrders" @change="handlePurchaseItemTypeChange(scope.row)">
                <el-option label="产品" value="product" />
                <el-option label="原料" value="material" />
                <el-option label="包装" value="packaging" />
              </el-select>
              <el-select v-model="scope.row.target_id" filterable style="width: calc(100% - 118px)" :disabled="!canManageOrders" @change="() => handlePurchaseItemSelect(scope.row)">
                <el-option
                  v-for="item in itemOptions(scope.row)"
                  :key="item.id"
                  :label="item.name || item.packaging_name"
                  :value="item.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="规格" width="130">
            <template #default="scope">{{ itemSpec(scope.row) }}</template>
          </el-table-column>
          <el-table-column label="数量" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.quantity" :min="0.0001" :precision="4" style="width: 100%" :disabled="!canManageOrders" />
            </template>
          </el-table-column>
          <el-table-column label="袋数" width="120">
            <template #default="scope">{{ bagCount(scope.row) }}</template>
          </el-table-column>
          <el-table-column label="单价" width="140">
            <template #default="scope">
              <el-input-number v-model="scope.row.unit_price" :min="0.0001" :precision="4" style="width: 100%" :disabled="!canManageOrders" />
            </template>
          </el-table-column>
          <el-table-column label="最近供价" width="130">
            <template #default="scope">{{ latestCatalogPriceText(scope.row) }}</template>
          </el-table-column>
          <el-table-column v-if="canManageOrders" label="操作" width="90">
            <template #default="scope">
              <el-button size="small" text type="danger" @click="removeItem(scope.$index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button v-if="canManageOrders" type="primary" :loading="submitting" :disabled="submitting" @click="submit">提交</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="采购单详情" size="50%">
      <el-descriptions v-if="detail" :column="2" border>
        <el-descriptions-item label="采购单号">{{ detail.order_no }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ detail.supplier_name }}</el-descriptions-item>
        <el-descriptions-item label="经办人">{{ detail.operator_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status_label || statusLabel(detail.status) }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ detail.payment_type_label || paymentTypeLabel(detail.payment_type) }}</el-descriptions-item>
        <el-descriptions-item label="预计到货日期">{{ detail.expected_arrival_date || '-' }}</el-descriptions-item>
        <el-descriptions-item label="目标仓库">{{ detail.target_warehouse_name || '历史单未指定' }}</el-descriptions-item>
        <el-descriptions-item label="结算状态">{{ detail.fund_status_label || statusLabel(detail.fund_status) }}</el-descriptions-item>
        <el-descriptions-item label="流程进度" :span="2">
          <div class="flow-steps">
            <el-tooltip
              v-for="step in detail.flow_steps || []"
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
      </el-descriptions>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="product_name" label="产品" />
        <el-table-column prop="material_name" label="原料" />
        <el-table-column prop="packaging_name" label="包装" />
        <el-table-column prop="display_specification" label="规格" width="130" />
        <el-table-column prop="quantity" label="数量" />
        <el-table-column prop="bag_count" label="袋数" width="110" />
        <el-table-column prop="unit_price" label="单价"><template #default="scope">{{ formatMoney(scope.row.unit_price) }}</template></el-table-column>
        <el-table-column prop="received_quantity" label="已收货" />
      </el-table>
    </el-drawer>

    <el-drawer v-model="attachmentVisible" title="采购合同与发票附件" size="40%">
      <el-alert title="合同、发票上传后会同步通知财务和经理，便于后续付款审核与对账。" type="info" :closable="false" show-icon class="mb-12" />
      <AttachmentUploader v-if="attachmentOrder" :key="attachmentOrder.id" business-type="purchase_order" :business-id="attachmentOrder.id" :preset-types="['合同', '发票', '付款凭证', '到货单']" />
    </el-drawer>

    <el-dialog v-model="purchaseConfirmVisible" title="采购订单确认" width="760px">
      <el-alert type="success" :closable="false" show-icon class="mb-12">
        <template #title>该采购订单已审批通过，请采购员确认后开始采购。</template>
      </el-alert>
      <el-descriptions v-if="confirmDetail" :column="2" border>
        <el-descriptions-item label="采购单号">{{ confirmDetail.order_no }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ confirmDetail.supplier_name }}</el-descriptions-item>
        <el-descriptions-item label="经办人">{{ confirmDetail.operator_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ confirmDetail.payment_type_label || paymentTypeLabel(confirmDetail.payment_type) }}</el-descriptions-item>
        <el-descriptions-item label="订单金额">{{ formatMoney(confirmDetail.total_amount) }}</el-descriptions-item>
        <el-descriptions-item label="当前状态">{{ confirmDetail.status_label || statusLabel(confirmDetail.status) }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="confirmDetail?.items || []" stripe class="mt-16">
        <el-table-column prop="product_name" label="产品" />
        <el-table-column prop="material_name" label="原料" />
        <el-table-column prop="packaging_name" label="包装" />
        <el-table-column prop="quantity" label="数量" />
        <el-table-column prop="unit_price" label="单价"><template #default="scope">{{ formatMoney(scope.row.unit_price) }}</template></el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="purchaseConfirmVisible = false">稍后处理</el-button>
        <el-button type="primary" @click="submitPurchaseConfirm">确认开始采购</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="thresholdVisible" title="采购审批金额阈值" width="520px">
      <el-form label-width="130px">
        <el-form-item label="当前金额阈值">
          <el-input-number v-model="thresholdForm.amount" :min="0" :precision="4" style="width: 100%" :disabled="!canManageSettings" />
        </el-form-item>
        <el-alert title="采购订单总金额达到或超过该金额时，需要经理审批；审批通过后还需采购员确认才进入采购中。" type="info" :closable="false" show-icon />
      </el-form>
      <template #footer>
        <el-button @click="thresholdVisible = false">关闭</el-button>
        <el-button v-if="canManageSettings" type="primary" @click="saveThreshold">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="demandDetailVisible" title="补货需求详情" width="680px">
      <el-descriptions v-if="activeDemand" :column="2" border>
        <el-descriptions-item label="需求号">{{ activeDemand.demand_no }}</el-descriptions-item>
        <el-descriptions-item label="需求类型">{{ activeDemand.demand_type === 'raw_material' ? '生产原料补货' : '贸易产品补货' }}</el-descriptions-item>
        <el-descriptions-item label="来源单号">{{ activeDemand.sales_order_no || activeDemand.production_task_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="关联产品">{{ activeDemand.production_product_name || activeDemand.product_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="待采购物料">{{ activeDemand.item_name || activeDemand.material_name || activeDemand.product_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="缺口数量">{{ formatNumber(activeDemand.shortage_quantity) }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ activeDemand.status_label || statusLabel(activeDemand.status) }}</el-descriptions-item>
        <el-descriptions-item label="生成时间">{{ activeDemand.created_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="处理说明" :span="2">{{ activeDemand.process_note || '-' }}</el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button @click="demandDetailVisible = false">关闭</el-button>
        <el-button type="primary" @click="prefillDemand(activeDemand)">转建采购单</el-button>
      </template>
    </el-dialog>
  </PageSection>
  <DepartmentPurchaseRequests v-if="canManageOrders" purchase-mode />
</template>

<script setup>
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/progress/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tooltip/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElInput from 'element-plus/es/components/input/index.mjs'
import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElProgress from 'element-plus/es/components/progress/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post, put, remove } from '../../api/client'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import DepartmentPurchaseRequests from '../../components/DepartmentPurchaseRequests.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatNumber, paymentTypeLabel, statusLabel, statusType, formatMoney } from '../../utils/format'

const auth = useAuthStore()
const router = useRouter()
const { route, focusedId: focusedOrderId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const fulfillmentDemands = ref([])
const demandStatusTab = ref('pending')
const suppliers = ref([])
const userOptions = ref([])
const products = ref([])
const materials = ref([])
const packagingConfigs = ref([])
const warehouses = ref([])
const supplierCatalogItems = ref([])
const demandSupplierOptions = ref([])
const supplierAi = ref(null)
const supplierAiLoading = ref(false)
const dialogVisible = ref(false)
const detailVisible = ref(false)
const attachmentVisible = ref(false)
const demandDetailVisible = ref(false)
const purchaseConfirmVisible = ref(false)
const thresholdVisible = ref(false)
const detail = ref(null)
const confirmDetail = ref(null)
const attachmentOrder = ref(null)
const activeDemand = ref(null)
const selectedDemands = ref([])
const mergedDemandRows = ref([])
const mergeMode = ref(false)
const editingId = ref(null)
const submitting = ref(false)
const canManageOrders = computed(() => auth.user?.role_code === 'purchase')
const canManageSettings = computed(() => ['purchase', 'manager', 'system_admin', 'master_data_admin'].includes(auth.user?.role_code))
const purchaseUsers = computed(() => {
  const rows = userOptions.value.filter((item) => item.role_code === 'purchase')
  return rows.length ? rows : userOptions.value.filter((item) => item.id === auth.user?.id)
})
const purchaseApprovalThreshold = ref(0)
const activeStatusTab = ref('active')
const pendingFulfillmentDemands = computed(() => fulfillmentDemands.value.filter((item) => item.status !== 'converted'))
const completedFulfillmentDemands = computed(() => fulfillmentDemands.value.filter((item) => item.status === 'converted'))
const visibleFulfillmentDemands = computed(() => demandStatusTab.value === 'completed' ? completedFulfillmentDemands.value : pendingFulfillmentDemands.value)
const activeOrderRows = computed(() => activeStatusTab.value === 'active' ? rows.value : [])
const completedOrderRows = computed(() => activeStatusTab.value === 'completed' ? rows.value : [])
const visibleRows = computed(() => rows.value)

watch(demandStatusTab, () => {
  selectedDemands.value = []
})
const supplierAiTip = computed(() => {
  if (supplierAiLoading.value) {
    return { type: 'info', title: '正在读取供应商智能评价结果...' }
  }
  if (!supplierAi.value) return null
  const analysis = supplierAi.value.analysis || {}
  const supplierName = supplierAi.value.supplier?.name || '当前供应商'
  return {
    type: analysis.risk_type || analysis.grade_type || 'info',
    title: `${supplierName}：${analysis.predicted_grade || '-'}级，${analysis.risk_label || '暂无风险判断'}，BiLSTM预测${analysis.sequence_risk_label || '暂无结果'}`
  }
})
const supplierOptionsForCurrentItems = computed(() => (activeDemand.value || mergeMode.value) ? demandSupplierOptions.value : suppliers.value)
const mergeableDemandOptions = computed(() => (mergeMode.value ? demandSupplierOptions.value : []))
const pagination = reactive({ page: 1, pageSize: 10 })
const total = ref(0)
const pageSizes = [10, 20, 50, 100]
const pagedRows = rows

const emptyForm = () => ({
  supplier_id: null,
  operator_user_id: auth.user?.id || null,
  payment_type: '先付款后到货',
  expected_arrival_date: '',
  target_warehouse_id: warehouses.value.find((item) => item.code === 'LANGFANG_RAW' || item.code === 'WH-RM')?.id || null,
  fulfillment_demand_id: null,
  items: [{ type: 'material', target_id: null, quantity: 1, unit_price: 0 }]
})

const form = reactive(emptyForm())
const thresholdForm = reactive({ amount: 0 })

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

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
  supplierCatalogItems.value = []
  demandSupplierOptions.value = []
  supplierAi.value = null
  supplierAiLoading.value = false
  activeDemand.value = null
  mergedDemandRows.value = []
  mergeMode.value = false
  selectedDemands.value = []
}

function rowClassName({ row }) {
  return focusedOrderId.value === row.id ? 'is-focused-row' : ''
}

function canEdit(row) {
  return canManageOrders.value && (row.status === 'pending_approval' || row.status === 'rejected' || (row.status === 'purchasing' && !row.threshold_triggered))
}

function canCancel(row) {
  return canManageOrders.value && ['pending_approval', 'rejected', 'purchasing'].includes(row.status)
}

function canConfirmPurchase(row) {
  return canManageOrders.value && row.status === 'pending_purchase_confirm'
}

async function applyFocus() {
  const focusType = String(route.query.focusType || '')
  const focusId = Number(route.query.focusId || 0)
  if (focusId && (focusType === 'fulfillment_demand' || focusType === 'raw_material_replenishment' || focusType === 'low_stock_replenishment' || (focusType === 'sales_order_material_replenishment' || focusType === 'production_material_replenishment') || focusType === 'low_stock_product_material_replenishment')) {
    const matchedDemand = fulfillmentDemands.value.find((item) => {
      if (focusType === 'raw_material_replenishment') {
        return (item.raw_todo_ids || []).some((id) => Number(id) === focusId) || Number(item.raw_todo_id) === focusId || Number(item.usage_id) === focusId || String(item.id) === String(route.query.focusId || '')
      }
      if (focusType === 'low_stock_replenishment') {
        return (item.low_stock_todo_ids || []).some((id) => Number(id) === focusId) || Number(item.low_stock_todo_id) === focusId || String(item.id) === `low-${focusId}`
      }
      if ((focusType === 'sales_order_material_replenishment' || focusType === 'production_material_replenishment') || focusType === 'low_stock_product_material_replenishment') {
        return Number(item.replenishment_source_id) === focusId || Number(item.sales_material_todo_id) === focusId
      }
      return Number(item.id) === focusId
    })
    if (matchedDemand && !dialogVisible.value) {
      await prefillDemand(matchedDemand)
    }
    return
  }
  await applyRouteFocus(
    (focusId) => rows.value.find((item) => item.id === focusId),
    async (matched) => {
      if (String(route.query.focusType || '') === 'purchase_order_confirm') {
        if (canConfirmPurchase(matched)) {
          await openPurchaseConfirm(matched)
        }
      } else {
        await viewOrder(matched.id)
      }
    }
  )
}

async function clearFocusQuery() {
  const nextQuery = { ...route.query }
  delete nextQuery.focusId
  delete nextQuery.focusType
  await router.replace({ query: nextQuery })
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function addItem() {
  form.items.push({ type: 'material', target_id: null, quantity: 1, unit_price: 0 })
}

function removeItem(index) {
  form.items.splice(index, 1)
}

function currentItemSource(row) {
  if (row.type === 'product') return products.value.find((item) => item.id === row.target_id)
  if (row.type === 'material') return materials.value.find((item) => item.id === row.target_id)
  return packagingConfigs.value.find((item) => item.id === row.target_id)
}

function itemOptions(row) {
  if (!form.supplier_id) {
    return row.type === 'product' ? products.value : row.type === 'material' ? materials.value : packagingConfigs.value
  }
  if (row.type === 'packaging') return packagingConfigs.value
  if (!supplierCatalogItems.value.length) {
    return []
  }
  const allowedIds = new Set(
    supplierCatalogItems.value
      .filter((item) => item.item_type === row.type)
      .map((item) => row.type === 'product' ? item.product_id : item.material_id)
      .filter(Boolean)
  )
  const source = row.type === 'product' ? products.value : materials.value
  return source.filter((item) => allowedIds.has(item.id))
}

async function loadSupplierCatalog(supplierId) {
  supplierCatalogItems.value = []
  if (!supplierId) return
  const data = await get(`/api/master-data/suppliers/${supplierId}/catalog`)
  supplierCatalogItems.value = data.items || []
}

function resetInvalidItemsForSupplier() {
  form.items.forEach((item) => {
    if (item.target_id && !itemOptions(item).some((option) => option.id === item.target_id)) {
      item.target_id = null
      item.unit_price = 0
    }
  })
}

async function handleSupplierChange(supplierId) {
  await loadSupplierCatalog(supplierId)
  resetInvalidItemsForSupplier()
  await loadSupplierAi(supplierId)
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

async function loadSupplierAi(supplierId) {
  supplierAi.value = null
  if (!supplierId) return
  supplierAiLoading.value = true
  try {
    supplierAi.value = await get(`/api/supplier-ai/suppliers/${supplierId}`, { force: true })
  } catch {
    supplierAi.value = null
  } finally {
    supplierAiLoading.value = false
  }
}

function itemSpec(row) {
  if (row.type === 'packaging') return currentItemSource(row)?.specification_text || '-'
  return currentItemSource(row)?.specification || '-'
}

function currentCatalogItem(row) {
  if (row.type === 'packaging') return null
  return supplierCatalogItems.value.find((item) => {
    if (item.item_type !== row.type) return false
    return row.type === 'product' ? item.product_id === row.target_id : item.material_id === row.target_id
  })
}

function latestCatalogPriceText(row) {
  const price = currentCatalogItem(row)?.latest_price
  if (!price) return '-'
  return Number(price).toLocaleString('zh-CN', { minimumFractionDigits: 3, maximumFractionDigits: 4 })
}

function supplierOptionLabel(item) {
  const price = item.latest_price == null ? '暂无历史供价' : `最新供价 ${formatMoney(item.latest_price)}`
  return item.latest_order_no ? `${item.name}（${price}，${item.latest_order_no}）` : `${item.name}（${price}）`
}

function handlePurchaseItemTypeChange(row) {
  row.target_id = null
  row.unit_price = 0
}

function handlePurchaseItemSelect(row) {
  if (row.type === 'packaging') {
    row.unit_price = Number(currentItemSource(row)?.unit_price || 0)
    return
  }
  const latestPrice = currentCatalogItem(row)?.latest_price
  row.unit_price = latestPrice ? Number(latestPrice) : 0
}

function bagCount(row) {
  if (row.type === 'packaging') return '-'
  const kgPerBag = Number(currentItemSource(row)?.packaging?.kg_per_bag || 0)
  if (!kgPerBag || !row.quantity) return '-'
  return Math.ceil(Number(row.quantity) / kgPerBag)
}

async function load() {
  try {
    const data = await get(`/api/purchases/orders?tab=${activeStatusTab.value}&page=${pagination.page}&page_size=${pagination.pageSize}`)
    rows.value = data.items || []
    total.value = Number(data.pagination?.total || 0)
  } catch (error) {
    ElMessage.error(`采购订单加载失败：${error.message || '请稍后重试'}`)
    return
  }
  if (canManageOrders.value) {
    try {
      fulfillmentDemands.value = await get('/api/purchases/fulfillment-demands')
    } catch (error) {
      fulfillmentDemands.value = []
      ElMessage.error(`采购补货需求加载失败：${error.message || '请稍后重试'}`)
    }
  } else {
    fulfillmentDemands.value = []
  }
  await applyFocus()
}

async function handleOrderPaginationChange({ page, pageSize }) {
  pagination.page = page
  pagination.pageSize = pageSize
  await load()
}

function demandSourceLabel(row) {
  if (row?.source_type === 'low_stock_replenishment') return '安全库存补货'
  if (row?.source_type === 'production_material_replenishment') return '生产原料补货'
  if (row?.source_type === 'sales_order_material_replenishment') return '销售订单生产原料'
  if (row?.source_type === 'low_stock_product_material_replenishment') return '安全库存生产原料'
  return row?.demand_type === 'raw_material' ? '生产领料缺料' : '客户订单缺货'
}

async function loadSettings() {
  const settings = await get('/api/master-data/settings')
  purchaseApprovalThreshold.value = Number(settings.purchase_approval_amount_threshold || 0)
  thresholdForm.amount = purchaseApprovalThreshold.value
}

async function loadSupplierOptionsForDemand(row) {
  demandSupplierOptions.value = []
  if (!row) return
  const isRawDemand = row.demand_type === 'raw_material' || row.demand_type === 'low_stock_material' || row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material'
  if ((row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material') && row.source_items?.length) {
    const optionLists = await Promise.all(
      row.source_items
        .filter((item) => item.material_id)
        .map((item) => get(`/api/purchases/supplier-options?material_id=${item.material_id}`))
    )
    const commonSupplierIds = optionLists.reduce((common, options) => {
      const ids = new Set((options || []).map((item) => item.supplier_id))
      return common === null ? ids : new Set([...common].filter((id) => ids.has(id)))
    }, null)
    const firstOptions = optionLists[0] || []
    demandSupplierOptions.value = firstOptions
      .filter((item) => commonSupplierIds?.has(item.supplier_id))
      .map((item) => ({
        id: item.supplier_id,
        name: item.supplier_name,
        latest_price: item.latest_price,
        latest_order_no: item.latest_order_no
      }))
    if (demandSupplierOptions.value.length) {
      form.supplier_id = demandSupplierOptions.value[0].id
      await loadSupplierCatalog(form.supplier_id)
      await loadSupplierAi(form.supplier_id)
      form.items.forEach((item) => handlePurchaseItemSelect(item))
    }
    return
  }
  const query = isRawDemand ? `material_id=${row.material_id}` : `product_id=${row.product_id}`
  if ((!isRawDemand && !row.product_id) || (isRawDemand && !row.material_id)) return
  const options = await get(`/api/purchases/supplier-options?${query}`)
  demandSupplierOptions.value = (options || []).map((item) => ({
    id: item.supplier_id,
    name: item.supplier_name,
    latest_price: item.latest_price,
    latest_order_no: item.latest_order_no
  }))
  if (demandSupplierOptions.value.length) {
    form.supplier_id = demandSupplierOptions.value[0].id
    await loadSupplierCatalog(form.supplier_id)
    await loadSupplierAi(form.supplier_id)
    form.items.forEach((item) => handlePurchaseItemSelect(item))
  } else {
    form.supplier_id = null
    supplierCatalogItems.value = []
    supplierAi.value = null
    ElMessage.warning('当前补货商品未维护可供货供应商名录，请先在供应商名录中维护后再转建采购单')
  }
}

async function prefillDemand(row) {
  if (!row) return
  resetForm()
  activeDemand.value = row
  selectedDemands.value = [row]
  const isRawDemand = row.demand_type === 'raw_material' || row.demand_type === 'low_stock_material' || row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material'
  const isLowStockDemand = row.demand_type === 'low_stock_product' || row.demand_type === 'low_stock_material'
  // 生产人员提交的原料需求允许逐项转采购单。旧数据可能在同一 YL 下包含
  // 多种原料，此处也只回填采购人员当前点击的这一行。
  const sourceItems = row.source_type !== 'production_material_replenishment' && (row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material') && row.source_items?.length
    ? row.source_items
    : [row]
  form.items = sourceItems.map((item) => ({
    type: isRawDemand ? 'material' : 'product',
    target_id: isRawDemand ? item.material_id : item.product_id,
    quantity: Number(item.shortage_quantity || 1),
    unit_price: 0
  }))
  const targetCode = isRawDemand ? 'LANGFANG_RAW' : 'CANGZHOU_FINISHED'
  form.target_warehouse_id = warehouses.value.find((warehouse) => warehouse.code === targetCode)?.id || form.target_warehouse_id
  form.fulfillment_demand_id = (isRawDemand || isLowStockDemand) ? null : row.id
  mergeMode.value = false
  await loadSupplierOptionsForDemand(row)
  demandDetailVisible.value = false
  dialogVisible.value = true
}

function mergeableRow(row) {
  return row && row.source_type !== 'low_stock_replenishment'
}

async function mergeDemands() {
  // 同一 YL 会按原料显示多行，合并时每份来源只计一次。
  const uniqueSources = new Map()
  for (const row of selectedDemands.value.filter(mergeableRow)) {
    const source = demandSourcePayload(row)
    uniqueSources.set(`${source.source_type}:${source.source_id}`, row)
  }
  const rows = [...uniqueSources.values()]
  if (rows.length < 2) {
    ElMessage.warning('请至少勾选两条可合并的补货需求（安全库存补货暂不支持合并）')
    return
  }
  const independent = rows.filter(row => row.source_type === 'production_material_replenishment')
  if (independent.length && independent.length !== rows.length) {
    ElMessage.warning('独立原料补货需求请只与其他独立原料补货需求合并')
    return
  }
  resetForm()
  activeDemand.value = null
  mergedDemandRows.value = rows
  mergeMode.value = true
  const itemMap = new Map()
  for (const row of rows) {
    const isRaw = row.demand_type === 'raw_material' || row.demand_type === 'low_stock_material' || row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material'
    const sourceItems = (row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material') && row.source_items?.length
      ? row.source_items
      : [row]
    for (const item of sourceItems) {
      const key = `${isRaw ? 'm' : 'p'}-${isRaw ? item.material_id : item.product_id}`
      const qty = Number(item.shortage_quantity || 1)
      if (itemMap.has(key)) {
        const prev = itemMap.get(key)
        prev.quantity = Number(prev.quantity) + qty
      } else {
        itemMap.set(key, {
          type: isRaw ? 'material' : 'product',
          target_id: isRaw ? item.material_id : item.product_id,
          quantity: qty,
          unit_price: 0
        })
      }
    }
  }
  form.items = [...itemMap.values()]
  // 供应商:取所有需求共同维护了名录的供应商
  await loadSupplierOptionsForDemands(rows)
  dialogVisible.value = true
}

async function fetchDemandSuppliers(row) {
  const isRawDemand = row.demand_type === 'raw_material' || row.demand_type === 'low_stock_material' || row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material'
  if (row.source_type !== 'production_material_replenishment' && (row.demand_type === 'sales_order_raw_material' || row.demand_type === 'low_stock_product_raw_material') && row.source_items?.length) {
    const optionLists = await Promise.all(
      row.source_items
        .filter((item) => item.material_id)
        .map((item) => get(`/api/purchases/supplier-options?material_id=${item.material_id}`))
    )
    const commonSupplierIds = optionLists.reduce((common, options) => {
      const ids = new Set((options || []).map((item) => item.supplier_id))
      return common === null ? ids : new Set([...common].filter((id) => ids.has(id)))
    }, null)
    const firstOptions = optionLists[0] || []
    return commonSupplierIds
      ? firstOptions.filter((item) => commonSupplierIds.has(item.supplier_id))
      : []
  }
  const query = isRawDemand ? `material_id=${row.material_id}` : `product_id=${row.product_id}`
  if ((!isRawDemand && !row.product_id) || (isRawDemand && !row.material_id)) return []
  return (await get(`/api/purchases/supplier-options?${query}`)) || []
}

function demandSourcePayload(row) {
  // 多需求合并时,将列表行还原为后端补给来源(sales_order_material_replenishment 等)
  const sourceType = (row.source_type === 'sales_order_material_replenishment' || row.source_type === 'production_material_replenishment') || row.source_type === 'low_stock_product_material_replenishment'
    ? row.source_type
    : row.demand_type === 'raw_material' || row.demand_type === 'low_stock_material'
      ? 'raw_material_replenishment'
      : row.demand_type === 'low_stock_product'
        ? 'low_stock_replenishment'
        : 'fulfillment_demand'
  const sourceId = (row.source_type === 'sales_order_material_replenishment' || row.source_type === 'production_material_replenishment') || row.source_type === 'low_stock_product_material_replenishment'
    ? row.replenishment_source_id
    : row.demand_type === 'raw_material' || row.demand_type === 'low_stock_material'
      ? row.usage_id
      : row.low_stock_todo_id
        ? row.replenishment_source_id
        : row.id
  return { source_type: sourceType, source_id: sourceId }
}

async function loadSupplierOptionsForDemands(rows) {
  demandSupplierOptions.value = []
  let shared = null
  for (const row of rows) {
    const suppliers = await fetchDemandSuppliers(row)
    const ids = new Set(suppliers.map((item) => item.id))
    if (shared === null) {
      shared = ids
    } else {
      shared = new Set([...shared].filter((id) => ids.has(id)))
    }
  }
  if (shared && shared.size) {
    demandSupplierOptions.value = (await get('/api/master-data/suppliers')).filter((item) => shared.has(item.id))
  }
  if (!demandSupplierOptions.value.length) {
    ElMessage.warning('所选补货需求未找到共同可供货的供应商，请在供应商名录中维护')
    return
  }
  form.supplier_id = demandSupplierOptions.value[0].id
  await loadSupplierCatalog(form.supplier_id)
  await loadSupplierAi(form.supplier_id)
  form.items.forEach((item) => handlePurchaseItemSelect(item))
}

function viewDemand(row) {
  activeDemand.value = row
  demandDetailVisible.value = true
}

// 已移除"改数量":采购数量与单价在"转建采购单"弹窗里直接可改
// (form.items 的 quantity/unit_price 均可编辑,且已按需求量预填),
// 不需要另开一个只改数量的入口。后端端点保留,不影响历史调用。

const DEMAND_PROGRESS_TAGS = {
  pending: 'warning', purchasing: 'primary', ordered: 'primary', receiving: 'primary',
  pending_payment: 'danger', raw_purchasing: 'warning', producing: 'primary',
  pending_inbound: 'warning', completed: 'success', cancelled: 'info',
}
function demandProgressTag(status) {
  return DEMAND_PROGRESS_TAGS[status] || 'info'
}

async function cancelDemand(row) {
  const reason = window.prompt('请输入取消原因')
  if (!reason || reason.trim().length < 2) {
    ElMessage.warning('请填写有效的取消原因')
    return
  }
  await post(`/api/purchases/fulfillment-demands/${row.id}/cancel`, { reason: reason.trim() })
  ElMessage.success('补货需求已取消')
  await load()
}

async function submit() {
  if (submitting.value) return
  submitting.value = true
  try {
    if (!form.supplier_id) {
      ElMessage.warning('请选择供应商')
      return
    }
    if (!form.target_warehouse_id) {
      ElMessage.warning('请选择目标仓库')
      return
    }
    if (!form.expected_arrival_date) {
      ElMessage.warning('请选择预计到货日期')
      return
    }
    if (activeDemand.value && !demandSupplierOptions.value.length) {
      ElMessage.warning('当前补货商品未维护可供货供应商名录，不能直接从补货需求转建采购单')
      return
    }
    if (mergeMode.value && !mergeableDemandOptions.value.length) {
      ElMessage.warning('所选补货需求未维护共同可供货供应商名录，不能合并建单')
      return
    }
    await loadSupplierCatalog(form.supplier_id)
    if (form.items.some((item) => !item.target_id || !itemOptions(item).some((option) => option.id === item.target_id))) {
      ElMessage.warning('采购明细必须选择有效的产品、原料或包装；请确认该供应商已维护此物料的可供货名录')
      return
    }
    const payloadBase = {
    supplier_id: form.supplier_id,
    operator_user_id: form.operator_user_id || auth.user?.id,
    payment_type: form.payment_type,
    expected_arrival_date: form.expected_arrival_date,
    target_warehouse_id: form.target_warehouse_id,
    items: form.items.map((item) => ({
      product_id: item.type === 'product' ? item.target_id : null,
      material_id: item.type === 'material' ? item.target_id : null,
      packaging_config_id: item.type === 'packaging' ? item.target_id : null,
      quantity: item.quantity,
      unit_price: item.unit_price
    }))
  }
    let payload
    if (mergeMode.value) {
      payload = {
      ...payloadBase,
      replenishment_sources: mergedDemandRows.value.map((row) => demandSourcePayload(row))
    }
    } else {
      payload = {
      ...payloadBase,
      replenishment_source_type: (activeDemand.value?.source_type === 'sales_order_material_replenishment' || activeDemand.value?.source_type === 'production_material_replenishment') || activeDemand.value?.source_type === 'low_stock_product_material_replenishment'
        ? activeDemand.value.source_type
        : activeDemand.value?.demand_type === 'raw_material'
        ? 'raw_material_replenishment'
        : activeDemand.value?.low_stock_todo_id
          ? 'low_stock_replenishment'
          : form.fulfillment_demand_id
            ? 'fulfillment_demand'
            : null,
      replenishment_source_id: (activeDemand.value?.source_type === 'sales_order_material_replenishment' || activeDemand.value?.source_type === 'production_material_replenishment') || activeDemand.value?.source_type === 'low_stock_product_material_replenishment'
        ? activeDemand.value.replenishment_source_id
        : activeDemand.value?.demand_type === 'raw_material'
        ? activeDemand.value.usage_id
        : activeDemand.value?.low_stock_todo_id
          ? activeDemand.value.replenishment_source_id
          : form.fulfillment_demand_id || null
    }
  }

    if (editingId.value) {
      const result = await put(`/api/purchases/orders/${editingId.value}`, payload)
      ElMessage.success(`采购单已更新：${result.order_no}`)
    } else {
      const result = await post('/api/purchases/orders', payload)
      ElMessage.success(`采购单已创建：${result.order_no}`)
    }
    dialogVisible.value = false
    resetForm()
    if (route.query.focusId || route.query.focusType) {
      await router.replace({ path: '/purchases/orders', query: {} })
    }
    await load()
  } catch (error) {
    ElMessage.error(`采购订单提交失败：${error?.message || '请稍后重试'}`)
  } finally {
    submitting.value = false
  }
}

async function viewOrder(id) {
  detail.value = await get(`/api/purchases/orders/${id}`)
  detailVisible.value = true
}

async function openPurchaseConfirm(row) {
  confirmDetail.value = await get(`/api/purchases/orders/${row.id}`)
  purchaseConfirmVisible.value = true
}

async function submitPurchaseConfirm() {
  if (!confirmDetail.value?.id) return
  await post(`/api/purchases/orders/${confirmDetail.value.id}/confirm`, {})
  ElMessage.success('采购订单已确认，状态转为采购中')
  purchaseConfirmVisible.value = false
  confirmDetail.value = null
  await clearFocusQuery()
  await load()
}

async function saveThreshold() {
  const result = await put('/api/master-data/settings/purchase-approval-threshold', { amount: thresholdForm.amount })
  purchaseApprovalThreshold.value = Number(result.purchase_approval_amount_threshold || thresholdForm.amount || 0)
  ElMessage.success('采购审批金额阈值已更新')
  thresholdVisible.value = false
}

async function editOrder(row) {
  const order = await get(`/api/purchases/orders/${row.id}`)
  resetForm()
  editingId.value = row.id
  form.supplier_id = order.supplier_id
  form.operator_user_id = order.operator_user_id || auth.user?.id
  await loadSupplierCatalog(order.supplier_id)
  await loadSupplierAi(order.supplier_id)
  form.payment_type = order.payment_type
  form.expected_arrival_date = order.expected_arrival_date || ''
  form.target_warehouse_id = order.target_warehouse_id || form.target_warehouse_id
  form.items = (order.items || []).map((item) => ({
    type: item.product_id ? 'product' : item.material_id ? 'material' : 'packaging',
    target_id: item.product_id || item.material_id || item.packaging_config_id,
    quantity: item.quantity,
    unit_price: item.unit_price
  }))
  dialogVisible.value = true
}

async function cancelOrder(row) {
  await ElMessageBox.confirm(`确认作废采购单 ${row.order_no} 吗？`, '提示', { type: 'warning' })
  await remove(`/api/purchases/orders/${row.id}`)
  ElMessage.success('采购单已作废')
  await load()
}

function openAttachments(row) {
  attachmentOrder.value = row
  attachmentVisible.value = true
}

function exportTable() {
  exportRowsToCsv('purchase_orders', rows.value)
}

onMounted(async () => {
  try {
    await loadSettings()
    userOptions.value = await get('/api/admin/users/options')
    suppliers.value = await get('/api/master-data/suppliers')
    products.value = await get('/api/master-data/products')
    materials.value = await get('/api/master-data/materials')
    packagingConfigs.value = await get('/api/master-data/packaging-configs?entity_type=global&active_only=true')
    warehouses.value = await get('/api/master-data/warehouses')
    warehouses.value = warehouses.value.filter((item) => ['raw', 'finished'].includes(item.type))
    await load()
  } catch (error) {
    ElMessage.error(`采购页面初始化失败：${error.message || '请稍后重试'}`)
  }
})

watch(activeStatusTab, async () => {
  pagination.page = 1
  await load()
})

watch(
  () => form.supplier_id,
  async (supplierId, oldSupplierId) => {
    if (supplierId === oldSupplierId) return
    await loadSupplierCatalog(supplierId)
    resetInvalidItemsForSupplier()
  }
)

watch(
  () => [route.query.focusId, route.query.focusType],
  async () => {
    if (rows.value.length) {
      await applyFocus()
    }
  }
)
</script>

<style scoped>
.demand-related { font-size: 12px; color: #909399; line-height: 18px; }
.demand-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.demand-toolbar-tip { font-size: 12px; color: #909399; }
.status-tabs { margin-bottom: 12px; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.mt-16 { margin-top: 16px; }
.mb-12 { margin-bottom: 12px; }
.ai-summary { margin-top: 6px; line-height: 1.7; }
.ai-summary-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; font-weight: 600; }
.ai-summary-body { display: flex; flex-wrap: wrap; gap: 8px 16px; color: #526579; }
:deep(.is-focused-row) > td { background: #fff8e1 !important; }
.flow-steps { display: flex; flex-wrap: wrap; gap: 8px; }
.flow-step { padding: 4px 10px; border-radius: 999px; background: #f4f4f5; color: #909399; font-size: 12px; }
.flow-step.is-done { background: #f0f9eb; color: #67c23a; }
.flow-step.is-current { background: #ecf5ff; color: #409eff; font-weight: 600; }
.flow-tooltip-content { max-width: 280px; white-space: pre-line; line-height: 1.6; }

.demand-progress { display: flex; flex-direction: column; gap: 4px; }
.demand-progress-text { font-size: 12px; color: var(--el-text-color-regular); line-height: 1.4; }
.demand-progress-pending { color: var(--el-color-warning); margin-left: 4px; }
</style>
