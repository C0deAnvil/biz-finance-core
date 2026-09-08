<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待付款订单</div><div class="summary-value">{{ summary.pending_order_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">付款记录</div><div class="summary-value">{{ summary.record_count || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待付总额</div><div class="summary-value">{{ formatNumber(totalRemainingAmount) }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已付总额</div><div class="summary-value">{{ formatNumber(totalPaidAmount) }}</div></el-card></el-col>
    </el-row>

    <div ref="pendingSectionRef">
      <PageSection title="待付款订单" description="仅展示仍未结清的采购订单与已登记付款记录。主流程仍由采购侧提交付款申请，当前页保留经理/财务在特殊场景下的直接登记能力。">
        <template #actions>
          <el-button @click="load">刷新</el-button>
          <el-button v-if="canRegisterPayment" type="success" plain @click="openMergedRegisterDialog">登记付款</el-button>
          <el-button @click="exportPendingOrders">导出待付款订单</el-button>
        </template>
        <el-row :gutter="12" class="filters-row">
          <el-col :span="8">
            <!-- 财务对账要按时间段查:原先本页只有弹窗里的"收付款日期"输入框,
                   列表本身无法按区间过滤。 -->
            <el-date-picker
                v-model="filters.dateRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                value-format="YYYY-MM-DD"
                style="width: 100%"
                @change="load"
            />
          </el-col>
        </el-row>


        <el-table :data="pagedPendingOrders" stripe :row-class-name="pendingRowClassName" @selection-change="handlePendingSelectionChange">
          <el-table-column v-if="canRegisterPayment" type="selection" width="52" />
          <el-table-column type="expand" width="52">
            <template #default="scope">
              <el-table :data="scope.row.items || []" border size="small">
                <el-table-column prop="item_code" label="编码" width="140" />
                <el-table-column prop="item_name" label="物料/产品" min-width="180" />
                <el-table-column prop="display_specification" label="规格" width="140" />
                <el-table-column prop="quantity" label="订单数量" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
                </el-table-column>
                <el-table-column prop="received_quantity" label="已到货" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.received_quantity) }}</template>
                </el-table-column>
                <el-table-column prop="unit_price" label="单价" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.unit_price) }}</template>
                </el-table-column>
                <el-table-column prop="total_amount" label="金额" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.total_amount) }}</template>
                </el-table-column>
              </el-table>
            </template>
          </el-table-column>
          <el-table-column prop="order_no" label="采购单号" width="160" />
          <el-table-column prop="supplier_name" label="供应商" min-width="180" />
          <el-table-column prop="status_label" label="采购状态" width="120">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="settlement_status_label" label="结算状态" width="120">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.settlement_status)">{{ scope.row.settlement_status_label || statusLabel(scope.row.settlement_status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="item_summary" label="订单明细摘要" min-width="220" show-overflow-tooltip />
          <el-table-column prop="total_amount" label="订单金额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
          </el-table-column>
          <el-table-column prop="paid_amount" label="已付金额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.paid_amount) }}</template>
          </el-table-column>
          <el-table-column prop="remaining_amount" label="待付金额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.remaining_amount) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="goToPurchaseOrder(scope.row.id)">查看订单</el-button>
            </template>
          </el-table-column>
        </el-table>
        <AppPagination
          v-model:page="pendingPagination.page"
          v-model:page-size="pendingPagination.pageSize"
          :total="pendingTotal"
          :page-sizes="pageSizes"
        />
      </PageSection>
    </div>

    <div ref="recordSectionRef">
      <PageSection title="付款申请协同台账" description="采购侧发起的付款申请、经理确认结果与流程完成状态统一在此留痕；主流程不变，当前页仅补充直接登记与合并登记的例外入口。">
        <el-table :data="pagedPaymentRequests" stripe :row-class-name="paymentRequestRowClassName">
          <el-table-column type="expand" width="52">
            <template #default="scope">
              <el-table :data="scope.row.items || []" border size="small">
                <el-table-column prop="item_code" label="编码" width="140" />
                <el-table-column prop="item_name" label="物料/产品" min-width="180" />
                <el-table-column prop="display_specification" label="规格" width="140" />
                <el-table-column prop="quantity" label="订单数量" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
                </el-table-column>
                <el-table-column prop="received_quantity" label="已到货" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.received_quantity) }}</template>
                </el-table-column>
                <el-table-column prop="unit_price" label="单价" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.unit_price) }}</template>
                </el-table-column>
                <el-table-column prop="total_amount" label="金额" width="120">
                  <template #default="innerScope">{{ formatNumber(innerScope.row.total_amount) }}</template>
                </el-table-column>
              </el-table>
            </template>
          </el-table-column>
          <el-table-column prop="merge_no" label="合并批次" width="140">
            <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
          </el-table-column>
          <el-table-column prop="request_no" label="申请单号" width="160" />
          <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
          <el-table-column prop="supplier_name" label="供应商" min-width="180" />
          <el-table-column prop="arrival_status_label" label="到货状态" width="120">
            <template #default="scope">
              <el-tag :type="scope.row.arrival_status === 'arrived' ? 'success' : 'warning'">{{ scope.row.arrival_status_label || statusLabel(scope.row.arrival_status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status_label" label="申请状态" width="120">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="order_amount" label="订单金额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.order_amount) }}</template>
          </el-table-column>
          <el-table-column prop="other_fee_amount" label="其他费用" width="120">
            <template #default="scope">{{ formatNumber(scope.row.other_fee_amount) }}</template>
          </el-table-column>
          <el-table-column prop="requested_amount" label="申请总额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.requested_amount) }}</template>
          </el-table-column>
          <el-table-column prop="actual_amount" label="实际出账" width="120">
            <template #default="scope">{{ formatNumber(scope.row.actual_amount) }}</template>
          </el-table-column>
          <el-table-column prop="requested_by_name" label="申请人" width="120" />
          <el-table-column prop="approved_by_name" label="审批人" width="120" />
          <el-table-column prop="created_at" label="申请时间" min-width="170">
            <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="payment_detail_remark_display" label="申请说明" min-width="220" show-overflow-tooltip />
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="goToPurchaseOrder(scope.row.purchase_order_id)">订单</el-button>
              <el-button
                v-if="canRegisterPayment && scope.row.status === 'pending_finance_payment'"
                size="small"
                text
                type="success"
                @click="openPaymentRegister(scope.row)"
              >
                登记出账
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <AppPagination
          v-model:page="requestPagination.page"
          v-model:page-size="requestPagination.pageSize"
          :total="requestTotal"
          :page-sizes="pageSizes"
        />
      </PageSection>
    </div>

    <div>
      <PageSection title="付款记录" description="付款按采购订单累计；小于订单总额记为部分结算，达到或超过订单总额记为已结算。">
        <template #actions>
          <el-button @click="exportRecords">导出付款记录</el-button>
        </template>

        <el-table :data="pagedRecords" stripe :row-class-name="recordRowClassName">
          <el-table-column prop="merge_no" label="合并批次" width="140">
            <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
          </el-table-column>
          <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
          <el-table-column prop="payment_type_label" label="付款方式" width="140" />
          <el-table-column prop="status_label" label="付款状态" width="120">
            <template #default="scope">
              <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="amount" label="付款金额" width="120">
            <template #default="scope">{{ formatNumber(scope.row.amount) }}</template>
          </el-table-column>
          <el-table-column prop="paid_at" label="付款时间" min-width="180">
            <template #default="scope">{{ formatDateTime(scope.row.paid_at) }}</template>
          </el-table-column>
          <el-table-column prop="paid_by_name" label="处理人" width="120" />
          <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.remark_display || formatRemark(scope.row.remark) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="goToPurchaseOrder(scope.row.purchase_order_id)">订单</el-button>
            </template>
          </el-table-column>
        </el-table>
        <AppPagination
          v-model:page="recordPagination.page"
          v-model:page-size="recordPagination.pageSize"
          :total="recordTotal"
          :page-sizes="pageSizes"
        />
      </PageSection>
    </div>

    <el-dialog v-model="paymentRegisterVisible" title="登记采购出账" width="760px">
      <el-descriptions v-if="activePaymentRequest" :column="2" border class="mb-16">
        <el-descriptions-item label="申请单号">{{ activePaymentRequest.request_no }}</el-descriptions-item>
        <el-descriptions-item label="采购订单">{{ activePaymentRequest.purchase_order_no }}</el-descriptions-item>
        <el-descriptions-item label="供应商">{{ activePaymentRequest.supplier_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="到货状态">{{ activePaymentRequest.arrival_status_label || statusLabel(activePaymentRequest.arrival_status) }}</el-descriptions-item>
        <el-descriptions-item label="订单金额">{{ formatNumber(activePaymentRequest.order_amount) }}</el-descriptions-item>
        <el-descriptions-item label="其他费用">{{ formatNumber(activePaymentRequest.other_fee_amount) }}</el-descriptions-item>
        <el-descriptions-item label="申请总额">{{ formatNumber(activePaymentRequest.requested_amount) }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ activePaymentRequest.requested_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="申请说明" :span="2">{{ activePaymentRequest.payment_detail_remark_display || activePaymentRequest.payment_detail_remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table v-if="activePaymentRequest?.items?.length" :data="activePaymentRequest.items" border size="small" class="mb-16">
        <el-table-column prop="item_code" label="编码" width="140" />
        <el-table-column prop="item_name" label="物料/产品" min-width="180" />
        <el-table-column prop="display_specification" label="规格" width="140" />
        <el-table-column prop="quantity" label="订单数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="received_quantity" label="已到货" width="120">
          <template #default="scope">{{ formatNumber(scope.row.received_quantity) }}</template>
        </el-table-column>
        <el-table-column prop="unit_price" label="单价" width="120">
          <template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" label="金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
      </el-table>
      <el-table v-if="activePaymentRequest?.attachments?.length" :data="activePaymentRequest.attachments" border size="small" class="mb-16">
        <el-table-column prop="attachment_type" label="附件类型" width="140" />
        <el-table-column prop="original_name" label="文件名" min-width="220" />
        <el-table-column label="操作" width="100">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openAttachment(scope.row.id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-form :model="paymentRegisterForm" label-width="110px">
        <el-form-item label="实际出账金额">
          <el-input-number v-model="paymentRegisterForm.actual_amount" :min="0.01" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="付款方式">
          <div class="method-select-line">
            <el-select v-model="paymentRegisterForm.payment_method" filterable allow-create default-first-option style="width: 100%" placeholder="请选择或新增付款方式">
              <el-option v-for="item in paymentMethodOptions" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <el-button @click="openPaymentMethodDialog">维护</el-button>
          </div>
        </el-form-item>
        <el-form-item label="付款时间">
          <el-date-picker v-model="paymentRegisterForm.paid_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handlePaymentFileChange(event, 'single')" />
          <el-button v-if="paymentRegisterForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(paymentRegisterForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="处理说明">
          <el-input v-model="paymentRegisterForm.remark" type="textarea" :rows="3" placeholder="可填写付款凭证、差异说明或审批意见" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="paymentRegisterVisible = false">取消</el-button>
        <DelayedActionButton type="primary" :loading="paymentRegisterSubmitting" @click="submitPaymentRegister">确认登记</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="mergedPaymentRequestVisible" title="确认合并付款申请" width="1040px">
      <el-descriptions v-if="mergedPaymentRequestSupplierName" :column="2" border class="mb-16">
        <el-descriptions-item label="供应商">{{ mergedPaymentRequestSupplierName }}</el-descriptions-item>
        <el-descriptions-item label="申请数量">{{ mergedPaymentRequestForm.items.length }}</el-descriptions-item>
        <el-descriptions-item label="申请总额">{{ formatNumber(mergedPaymentRequestRequestedTotal) }}</el-descriptions-item>
        <el-descriptions-item label="实际总额">{{ formatNumber(mergedPaymentRequestActualTotal) }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="mergedPaymentRequestForm.items" stripe border>
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="物料/产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="received_quantity" label="已到货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.received_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="request_no" label="申请单号" width="160" />
        <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
        <el-table-column prop="requested_amount" label="申请金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.requested_amount) }}</template>
        </el-table-column>
        <el-table-column prop="order_amount" label="订单金额" width="120">
          <template #default="scope">{{ formatNumber(scope.row.order_amount) }}</template>
        </el-table-column>
        <el-table-column prop="other_fee_amount" label="其他费用" width="120">
          <template #default="scope">{{ formatNumber(scope.row.other_fee_amount) }}</template>
        </el-table-column>
        <el-table-column label="实际出账" width="160">
          <template #default="scope">
            <el-input-number v-model="scope.row.actual_amount" :min="0.01" :precision="2" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="mergedPaymentRequestForm" label-width="100px">
        <el-form-item label="付款方式">
          <div class="method-select-line">
            <el-select v-model="mergedPaymentRequestForm.payment_method" filterable allow-create default-first-option style="width: 100%" placeholder="请选择或新增付款方式">
              <el-option v-for="item in paymentMethodOptions" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <el-button @click="openPaymentMethodDialog">维护</el-button>
          </div>
        </el-form-item>
        <el-form-item label="付款时间">
          <el-date-picker v-model="mergedPaymentRequestForm.paid_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handlePaymentFileChange(event, 'mergedRequest')" />
          <el-button v-if="mergedPaymentRequestForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(mergedPaymentRequestForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="处理说明">
          <el-input v-model="mergedPaymentRequestForm.remark" type="textarea" :rows="3" placeholder="可填写批次付款说明、差异说明或银行流水信息" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergedPaymentRequestVisible = false">取消</el-button>
        <DelayedActionButton type="primary" :loading="paymentRegisterSubmitting" @click="submitMergedPaymentRequestRegister">确认登记</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="mergedRegisterVisible" title="登记付款" width="980px">
      <el-descriptions v-if="mergedSupplierName" :column="2" border class="mb-16">
        <el-descriptions-item label="供应商">{{ mergedSupplierName }}</el-descriptions-item>
        <el-descriptions-item label="订单数量">{{ mergedRegisterForm.items.length }}</el-descriptions-item>
        <el-descriptions-item label="实付总额">{{ formatNumber(mergedActualAmount) }}</el-descriptions-item>
        <el-descriptions-item label="说明">支持同一供应商的单单或多单付款登记</el-descriptions-item>
      </el-descriptions>
      <el-table :data="mergedRegisterForm.items" stripe border>
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="物料/产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="received_quantity" label="已到货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.received_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="order_no" label="采购单号" width="160" />
        <el-table-column prop="supplier_name" label="供应商" min-width="180" />
        <el-table-column prop="item_summary" label="订单摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="remaining_amount" label="待付金额" width="130">
          <template #default="scope">{{ formatNumber(scope.row.remaining_amount) }}</template>
        </el-table-column>
        <el-table-column label="本次订单金额" width="150">
          <template #default="scope">
            <el-input-number v-model="scope.row.order_amount" :min="0" :max="scope.row.remaining_amount" :precision="2" style="width: 100%" />
          </template>
        </el-table-column>
        <el-table-column label="其他费用" width="140">
          <template #default="scope">
            <el-input-number v-model="scope.row.other_fee_amount" :min="0" :precision="2" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="mergedRegisterForm" label-width="100px">
        <el-form-item label="付款方式">
          <div class="method-select-line">
            <el-select v-model="mergedRegisterForm.payment_method" filterable allow-create default-first-option style="width: 100%" placeholder="请选择或新增付款方式">
              <el-option v-for="item in paymentMethodOptions" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <el-button @click="openPaymentMethodDialog">维护</el-button>
          </div>
        </el-form-item>
        <el-form-item label="付款时间">
          <el-date-picker v-model="mergedRegisterForm.paid_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handlePaymentFileChange(event, 'direct')" />
          <el-button v-if="mergedRegisterForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(mergedRegisterForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="处理说明">
          <el-input v-model="mergedRegisterForm.remark" type="textarea" :rows="3" placeholder="可填写合并付款凭证、银行流水说明等" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergedRegisterVisible = false">取消</el-button>
        <DelayedActionButton type="primary" :loading="paymentRegisterSubmitting" @click="submitMergedRegister">确认登记</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="paymentMethodVisible" title="付款方式维护" width="640px">
      <el-form :model="paymentMethodForm" inline>
        <el-form-item label="方式名称">
          <el-input v-model="paymentMethodForm.name" placeholder="如银行转账、现金、承兑等" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="savePaymentMethod">{{ paymentMethodForm.id ? '保存' : '新增' }}</el-button>
          <el-button @click="resetPaymentMethodForm">清空</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="paymentMethodRows" border>
        <el-table-column prop="name" label="付款方式" />
        <el-table-column prop="is_active" label="启用" width="90">
          <template #default="scope">{{ scope.row.is_active ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="editPaymentMethod(scope.row)">编辑</el-button>
            <el-button size="small" text type="danger" @click="deletePaymentMethod(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="paymentMethodVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDatePicker from 'element-plus/es/components/date-picker/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { get, post, put, remove, upload } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { parseFocusId } from '../../composables/useRouteFocus'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, formatRemark, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const pendingSectionRef = ref(null)
const recordSectionRef = ref(null)
const summary = reactive({})
const pendingOrders = ref([])
const paymentRequests = ref([])
const records = ref([])
const paymentMethodOptions = ref([])
const paymentMethodRows = ref([])
const paymentMethodVisible = ref(false)
const paymentMethodForm = reactive({ id: null, name: '' })
const focusedOrderId = ref(null)
const focusedRecordId = ref(null)
const focusedPaymentRequestId = ref(null)
const paymentRegisterVisible = ref(false)
const mergedPaymentRequestVisible = ref(false)
const mergedRegisterVisible = ref(false)
const activePaymentRequest = ref(null)
const selectedPendingOrders = ref([])
const paymentRegisterForm = reactive({
  actual_amount: 0,
  remark: '',
  payment_method: '',
  bank_receipt_attachment_id: null,
  paid_at: ''
})
const mergedRegisterForm = reactive({
  supplier_id: null,
  items: [],
  remark: '',
  payment_method: '',
  bank_receipt_attachment_id: null,
  paid_at: ''
})
const mergedPaymentRequestForm = reactive({
  items: [],
  remark: '',
  payment_method: '',
  bank_receipt_attachment_id: null,
  paid_at: ''
})
const paymentRegisterSubmitting = ref(false)
const selectedPaymentFiles = reactive({ single: null, mergedRequest: null, direct: null })
const canRegisterPayment = computed(() => ['finance', 'system_admin'].includes(auth.user?.role_code))
const totalRemainingAmount = computed(() => pendingOrders.value.reduce((sum, item) => sum + Number(item.remaining_amount || 0), 0))
const totalPaidAmount = computed(() => records.value.reduce((sum, item) => sum + Number(item.amount || 0), 0))
const mergedSupplierName = computed(() => mergedRegisterForm.items[0]?.supplier_name || '')
const mergedActualAmount = computed(() =>
  mergedRegisterForm.items.reduce((sum, item) => sum + Number(item.order_amount || 0) + Number(item.other_fee_amount || 0), 0)
)
const mergedPaymentRequestSupplierName = computed(() => mergedPaymentRequestForm.items[0]?.supplier_name || '')
const mergedPaymentRequestRequestedTotal = computed(() =>
  mergedPaymentRequestForm.items.reduce((sum, item) => sum + Number(item.requested_amount || 0), 0)
)
const mergedPaymentRequestActualTotal = computed(() =>
  mergedPaymentRequestForm.items.reduce((sum, item) => sum + Number(item.actual_amount || 0), 0)
)

const {
  pagination: pendingPagination,
  total: pendingTotal,
  pageSizes,
  pagedItems: pagedPendingOrders,
  ensureVisible: ensurePendingVisible
} = useLocalPagination(pendingOrders)
const {
  pagination: requestPagination,
  total: requestTotal,
  pagedItems: pagedPaymentRequests,
  ensureVisible: ensureRequestVisible
} = useLocalPagination(paymentRequests)
const {
  pagination: recordPagination,
  total: recordTotal,
  pagedItems: pagedRecords,
  ensureVisible: ensureRecordVisible
} = useLocalPagination(records)

function pendingRowClassName({ row }) {
  return focusedOrderId.value === row.id ? 'is-focused-row' : ''
}

function recordRowClassName({ row }) {
  return focusedRecordId.value === row.id ? 'is-focused-row' : ''
}

function paymentRequestRowClassName({ row }) {
  return focusedPaymentRequestId.value === row.id ? 'is-focused-row' : ''
}

async function scrollToSection(sectionRef) {
  await nextTick()
  const target = sectionRef.value?.$el || sectionRef.value
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

async function applyFocus() {
  focusedOrderId.value = null
  focusedRecordId.value = null
  focusedPaymentRequestId.value = null
  const focusId = parseFocusId(route.query.focusId)
  const focusType = String(route.query.focusType || '')
  if (!focusId) return
  if (focusType === 'purchase_payment_request' && paymentRequests.value.some((item) => item.id === focusId)) {
    focusedPaymentRequestId.value = focusId
    ensureRequestVisible((item) => item.id === focusId)
    await scrollToSection(recordSectionRef)
    const target = paymentRequests.value.find((item) => item.id === focusId)
    if (target && canRegisterPayment.value && target.status === 'pending_finance_payment') {
      openPaymentRegister(target)
    }
    return
  }
  if ((focusType === 'payable_record' || !focusType) && records.value.some((item) => item.id === focusId)) {
    focusedRecordId.value = focusId
    ensureRecordVisible((item) => item.id === focusId)
    await scrollToSection(recordSectionRef)
    return
  }
  if ((focusType === 'purchase_order' || !focusType) && pendingOrders.value.some((item) => item.id === focusId)) {
    focusedOrderId.value = focusId
    ensurePendingVisible((item) => item.id === focusId)
    await scrollToSection(pendingSectionRef)
  }
}

// 日期区间筛选(后端 /api/finance/payables 已支持 date_from/date_to)
const filters = reactive({ dateRange: [] })

function payablesUrl() {
  const params = new URLSearchParams()
  if (Array.isArray(filters.dateRange) && filters.dateRange.length === 2) {
    if (filters.dateRange[0]) params.set('date_from', filters.dateRange[0])
    if (filters.dateRange[1]) params.set('date_to', filters.dateRange[1])
  }
  const query = params.toString()
  return `/api/finance/payables${query ? `?${query}` : ''}`
}

async function load() {
  const [data, methodData] = await Promise.all([
    get(payablesUrl()),
    get('/api/finance/payment-method-options?method_type=payment')
  ])
  Object.assign(summary, data.summary || {})
  pendingOrders.value = data.pending_orders || []
  paymentRequests.value = data.payment_requests || []
  records.value = data.records || []
  paymentMethodRows.value = methodData
  paymentMethodOptions.value = methodData.filter((item) => item.is_active)
  selectedPendingOrders.value = []

  await applyFocus()
}

async function loadPaymentMethods() {
  const rows = await get('/api/finance/payment-method-options?method_type=payment')
  paymentMethodRows.value = rows
  paymentMethodOptions.value = rows.filter((item) => item.is_active)
}

function openPaymentMethodDialog() {
  resetPaymentMethodForm()
  paymentMethodVisible.value = true
}

function resetPaymentMethodForm() {
  paymentMethodForm.id = null
  paymentMethodForm.name = ''
}

function editPaymentMethod(row) {
  paymentMethodForm.id = row.id
  paymentMethodForm.name = row.name
}

async function savePaymentMethod() {
  const name = String(paymentMethodForm.name || '').trim()
  if (!name) {
    ElMessage.warning('请填写付款方式名称')
    return
  }
  if (paymentMethodForm.id) {
    await put(`/api/finance/payment-method-options/${paymentMethodForm.id}`, { name, is_active: true })
    ElMessage.success('付款方式已更新')
  } else {
    await post('/api/finance/payment-method-options', { method_type: 'payment', name })
    ElMessage.success('付款方式已新增')
  }
  resetPaymentMethodForm()
  await loadPaymentMethods()
}

async function deletePaymentMethod(row) {
  await remove(`/api/finance/payment-method-options/${row.id}`)
  ElMessage.success('付款方式已删除')
  await loadPaymentMethods()
}

function handlePendingSelectionChange(selection) {
  selectedPendingOrders.value = selection || []
}

function goToPurchaseOrder(orderId) {
  if (!orderId) return
  router.push({ path: '/purchases/orders', query: { focusId: String(orderId) } })
}

function openAttachment(id) {
  if (!id) return
  window.open(`/api/files/${id}`, '_blank')
}

function handlePaymentFileChange(event, target) {
  selectedPaymentFiles[target] = event.target.files?.[0] || null
}

async function uploadPaymentFileIfNeeded(target, businessType, businessId) {
  const file = selectedPaymentFiles[target]
  if (!file) return null
  const formData = new FormData()
  formData.append('business_type', businessType)
  formData.append('business_id', String(businessId || 0))
  formData.append('attachment_type', '银行回单')
  formData.append('file', file)
  const result = await upload('/api/files/upload', formData)
  selectedPaymentFiles[target] = null
  return result.id
}

function openPaymentRegister(row) {
  if (row.merge_no) {
    const relatedRows = paymentRequests.value.filter((item) => item.merge_no === row.merge_no && item.status === 'pending_finance_payment')
    if (relatedRows.length > 1) {
      mergedPaymentRequestForm.items = relatedRows.map((item) => ({
        id: item.id,
        request_no: item.request_no,
        purchase_order_id: item.purchase_order_id,
        purchase_order_no: item.purchase_order_no,
        supplier_name: item.supplier_name,
        requested_amount: Number(item.requested_amount || 0),
        order_amount: Number(item.order_amount || 0),
        other_fee_amount: Number(item.other_fee_amount || 0),
        actual_amount: Number(item.requested_amount || item.actual_amount || 0),
        items: item.items || []
      }))
      mergedPaymentRequestForm.remark = row.payment_detail_remark_display || row.payment_detail_remark || ''
      mergedPaymentRequestVisible.value = true
      return
    }
  }
  activePaymentRequest.value = row
  paymentRegisterForm.actual_amount = Number(row.requested_amount || row.actual_amount || 0)
  paymentRegisterForm.remark = row.payment_detail_remark_display || row.payment_detail_remark || ''
  paymentRegisterForm.payment_method = row.payment_method || ''
  paymentRegisterForm.bank_receipt_attachment_id = row.bank_receipt_attachment_id || null
  paymentRegisterForm.paid_at = row.paid_at || currentDateTime()
  selectedPaymentFiles.single = null
  paymentRegisterVisible.value = true
}

function openMergedRegisterDialog() {
  if (selectedPendingOrders.value.length < 1) {
    ElMessage.warning('请至少选择一张同一供应商的采购订单')
    return
  }
  const supplierIds = new Set(selectedPendingOrders.value.map((item) => item.supplier_id).filter(Boolean))
  if (supplierIds.size !== 1) {
    ElMessage.warning('合并付款仅支持同一供应商的采购订单')
    return
  }
  mergedRegisterForm.supplier_id = selectedPendingOrders.value[0].supplier_id
  mergedRegisterForm.items = selectedPendingOrders.value.map((item) => ({
    purchase_order_id: item.id,
    order_no: item.order_no,
    supplier_name: item.supplier_name,
    item_summary: item.item_summary,
    items: item.items || [],
    remaining_amount: Number(item.remaining_amount || 0),
    order_amount: Number(item.remaining_amount || 0),
    other_fee_amount: 0
  }))
  mergedRegisterForm.remark = ''
  mergedRegisterForm.payment_method = ''
  mergedRegisterForm.bank_receipt_attachment_id = null
  mergedRegisterForm.paid_at = currentDateTime()
  selectedPaymentFiles.direct = null
  mergedRegisterVisible.value = true
}

function currentDateTime() {
  const date = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

async function submitPaymentRegister() {
  if (paymentRegisterSubmitting.value) return
  if (!activePaymentRequest.value || Number(paymentRegisterForm.actual_amount || 0) <= 0) {
    ElMessage.warning('请填写实际出账金额')
    return
  }
  paymentRegisterSubmitting.value = true
  try {
    const uploadedId = await uploadPaymentFileIfNeeded('single', 'purchase_payment_request', activePaymentRequest.value.id)
    await post(`/api/finance/payables/payment-requests/${activePaymentRequest.value.id}/register`, {
      purchase_payment_request_id: activePaymentRequest.value.id,
      actual_amount: paymentRegisterForm.actual_amount,
      remark: paymentRegisterForm.remark,
      payment_method: paymentRegisterForm.payment_method || null,
      bank_receipt_attachment_id: uploadedId || paymentRegisterForm.bank_receipt_attachment_id || null,
      paid_at: paymentRegisterForm.paid_at || null
    })
    ElMessage.success('采购出账已登记')
    paymentRegisterVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '登记出账失败')
  } finally {
    paymentRegisterSubmitting.value = false
  }
}

async function submitMergedRegister() {
  if (paymentRegisterSubmitting.value) return
  if (mergedRegisterForm.items.length < 1 || !mergedRegisterForm.supplier_id) {
    ElMessage.warning('请先选择同一供应商的采购订单')
    return
  }
  paymentRegisterSubmitting.value = true
  try {
    const uploadedId = await uploadPaymentFileIfNeeded('direct', 'purchase_order', mergedRegisterForm.items[0]?.purchase_order_id)
    await post('/api/finance/payables/merged-register', {
      supplier_id: mergedRegisterForm.supplier_id,
      actual_amount: mergedActualAmount.value,
      remark: mergedRegisterForm.remark,
      payment_method: mergedRegisterForm.payment_method || null,
      bank_receipt_attachment_id: uploadedId || mergedRegisterForm.bank_receipt_attachment_id || null,
      paid_at: mergedRegisterForm.paid_at || null,
      items: mergedRegisterForm.items.map((item) => ({
        purchase_order_id: item.purchase_order_id,
        order_amount: Number(item.order_amount || 0),
        other_fee_amount: Number(item.other_fee_amount || 0)
      }))
    })
    ElMessage.success('合并付款已登记')
    mergedRegisterVisible.value = false
    mergedRegisterForm.supplier_id = null
    mergedRegisterForm.items = []
    mergedRegisterForm.remark = ''
    mergedRegisterForm.payment_method = ''
    mergedRegisterForm.bank_receipt_attachment_id = null
    mergedRegisterForm.paid_at = ''
    await load()
  } catch (error) {
    ElMessage.error(error.message || '合并付款登记失败')
  } finally {
    paymentRegisterSubmitting.value = false
  }
}

async function submitMergedPaymentRequestRegister() {
  if (paymentRegisterSubmitting.value) return
  if (!mergedPaymentRequestForm.items.length) {
    ElMessage.warning('当前没有可登记的付款申请')
    return
  }
  paymentRegisterSubmitting.value = true
  try {
    const uploadedId = await uploadPaymentFileIfNeeded('mergedRequest', 'purchase_payment_request', mergedPaymentRequestForm.items[0]?.id)
    await post('/api/finance/payables/payment-requests/merged-register', {
      remark: mergedPaymentRequestForm.remark,
      payment_method: mergedPaymentRequestForm.payment_method || null,
      bank_receipt_attachment_id: uploadedId || mergedPaymentRequestForm.bank_receipt_attachment_id || null,
      paid_at: mergedPaymentRequestForm.paid_at || null,
      items: mergedPaymentRequestForm.items.map((item) => ({
        purchase_payment_request_id: item.id,
        actual_amount: Number(item.actual_amount || 0)
      }))
    })
    ElMessage.success('合并付款申请已登记出账')
    mergedPaymentRequestVisible.value = false
    mergedPaymentRequestForm.items = []
    mergedPaymentRequestForm.remark = ''
    await load()
  } catch (error) {
    ElMessage.error(error.message || '合并付款申请登记失败')
  } finally {
    paymentRegisterSubmitting.value = false
  }
}

function exportPendingOrders() {
  exportRowsToCsv('finance_pending_payables', pendingOrders.value)
}

function exportRecords() {
  exportRowsToCsv('finance_payable_records', records.value)
}

onMounted(load)

watch(
  () => [route.query.focusId, route.query.focusType, route.query.mode],
  async () => {
    await load()
  }
)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.filters-row { margin-bottom: 16px; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.order-summary-card { margin-bottom: 16px; }
.mb-16 { margin-bottom: 16px; }
.mt-16 { margin-top: 16px; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
.method-select-line { display: flex; gap: 8px; width: 100%; }
.method-select-line .el-select { flex: 1; }
</style>
