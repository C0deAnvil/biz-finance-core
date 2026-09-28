<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">待收款订单</div><div class="summary-value">{{ receivableSummary.pending_order_count || 0 }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">待收回款总额</div><div class="summary-value">{{ formatNumber(pendingReceivableTotal) }}</div></el-card></el-col>
      <el-col :span="8"><el-card shadow="hover"><div class="summary-label">已收回款总额</div><div class="summary-value">{{ formatNumber(receivedReceivableTotal) }}</div></el-card></el-col>
    </el-row>

    <PageSection title="待回款客户订单" description="仅展示财务审批后已转为应收的客户订单与回款台账；刚提交、等待客户订单审批的单据不会出现在这里。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
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
      <el-table :data="pagedPendingOrders" stripe @selection-change="handlePendingSelectionChange">
        <el-table-column v-if="canRegisterReceipt" type="selection" width="52" />
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="packaging_name" label="包装" width="140" />
              <el-table-column prop="bag_count" label="袋数" width="100"><template #default="innerScope">{{ formatNumber(innerScope.row.bag_count) }}</template></el-table-column>
              <el-table-column prop="packaging_unit_price" label="包装单价" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_unit_price) }}</template></el-table-column>
              <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_fee) }}</template></el-table-column>
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="shipped_quantity" label="已发货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.shipped_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="order_no" label="客户订单号" width="160" />
        <el-table-column prop="customer_name" label="客户" min-width="180" />
        <el-table-column prop="status_label" label="订单状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="settlement_status_label" label="结算状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.settlement_status)">{{ scope.row.settlement_status_label || statusLabel(scope.row.settlement_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="item_summary" label="订单摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="total_amount" label="订单金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="receivable_amount" label="应收金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.receivable_amount) }}</template>
        </el-table-column>
        <el-table-column prop="received_amount" label="已收金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.received_amount) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="goToSalesOrder(scope.row.id)">详情</el-button>
            <el-button v-if="canRegisterReceipt" size="small" text type="success" @click="openDirectRegisterDialog([scope.row])">登记回款</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pendingOrderPagination.page"
        v-model:page-size="pendingOrderPagination.pageSize"
        :total="pendingOrderTotal"
        :page-sizes="pageSizes"
        @change="handlePendingPaginationChange"
      />
    </PageSection>

    <PageSection v-if="false" ref="receiptRequestSectionRef" title="收款确认协同台账" description="历史协同台账暂时隐藏。">
      <el-table :data="pagedReceiptRequests" stripe :row-class-name="receiptRequestRowClassName">
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="packaging_name" label="包装" width="140" />
              <el-table-column prop="bag_count" label="袋数" width="100"><template #default="innerScope">{{ formatNumber(innerScope.row.bag_count) }}</template></el-table-column>
              <el-table-column prop="packaging_unit_price" label="包装单价" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_unit_price) }}</template></el-table-column>
              <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_fee) }}</template></el-table-column>
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="shipped_quantity" label="已发货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.shipped_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="company_account_label" label="企业账户" min-width="240" show-overflow-tooltip />
      <el-table-column prop="request_no" label="申请单号" width="160" />
        <el-table-column prop="merge_no" label="合并批次" width="140">
          <template #default="scope">{{ scope.row.merge_no || '-' }}</template>
        </el-table-column>
        <el-table-column prop="sales_order_no" label="客户订单号" width="160" />
        <el-table-column prop="customer_name" label="客户" min-width="180" />
        <el-table-column prop="payment_type_label" label="付款方式" width="150" />
        <el-table-column prop="payment_stage_label" label="收款阶段" width="120" />
        <el-table-column prop="status_label" label="申请状态" width="120">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="requested_amount" label="申请金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.requested_amount) }}</template>
        </el-table-column>
        <el-table-column prop="actual_amount" label="确认金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.actual_amount) }}</template>
        </el-table-column>
        <el-table-column prop="installment_total" label="总期数" width="100" />
        <el-table-column prop="installment_index" label="当前期数" width="100" />
        <el-table-column prop="requested_by_name" label="申请人" width="120" />
        <el-table-column prop="approved_by_name" label="确认人" width="120" />
        <el-table-column prop="created_at" label="申请时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="remark_display" label="申请说明" min-width="220" show-overflow-tooltip />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="goToSalesOrder(scope.row.sales_order_id)">订单</el-button>
            <el-button
              v-if="canRegisterReceipt && ['pending_finance_check', 'pending_chairman_approval'].includes(scope.row.status)"
              size="small"
              text
              type="success"
              @click="openReceiptRegister(scope.row)"
            >
              确认入账
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="receiptRequestPagination.page"
        v-model:page-size="receiptRequestPagination.pageSize"
        :total="receiptRequestTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <PageSection title="已完成应收客户订单" description="应收金额已全部登记回款的订单会自动移至此处。">
      <el-table :data="completedOrders" stripe>
        <el-table-column prop="order_no" label="客户订单号" width="160" />
        <el-table-column prop="customer_name" label="客户" min-width="180" />
        <el-table-column prop="total_amount" label="订单金额" width="120"><template #default="scope">{{ formatMoney(scope.row.total_amount) }}</template></el-table-column>
        <el-table-column prop="receivable_amount" label="应收金额" width="120"><template #default="scope">{{ formatMoney(scope.row.receivable_amount) }}</template></el-table-column>
        <el-table-column prop="received_amount" label="已收金额" width="120"><template #default="scope">{{ formatMoney(scope.row.received_amount) }}</template></el-table-column>
        <el-table-column prop="completed_at" label="完成时间" min-width="170"><template #default="scope">{{ formatDateTime(scope.row.completed_at) }}</template></el-table-column>
        <el-table-column label="操作" width="100" fixed="right"><template #default="scope"><el-button size="small" text type="primary" @click="goToSalesOrder(scope.row.id)">详情</el-button></template></el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="receiptRegisterVisible" title="财务核对入账" width="760px">
      <el-descriptions v-if="activeReceiptRequest" :column="2" border class="mb-16">
        <el-descriptions-item label="申请单号">{{ activeReceiptRequest.request_no }}</el-descriptions-item>
        <el-descriptions-item label="客户订单">{{ activeReceiptRequest.sales_order_no }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ activeReceiptRequest.customer_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ activeReceiptRequest.payment_type_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="收款阶段">{{ activeReceiptRequest.payment_stage_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="申请金额">{{ formatMoney(activeReceiptRequest.requested_amount) }}</el-descriptions-item>
        <el-descriptions-item label="已确认金额">{{ formatMoney(activeReceiptRequest.actual_amount) }}</el-descriptions-item>
        <el-descriptions-item label="总期数">{{ activeReceiptRequest.installment_total || '-' }}</el-descriptions-item>
        <el-descriptions-item label="当前期数">{{ activeReceiptRequest.installment_index || '-' }}</el-descriptions-item>
        <el-descriptions-item label="申请说明" :span="2">{{ activeReceiptRequest.remark_display || activeReceiptRequest.remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-form :model="receiptRegisterForm" label-width="110px">
        <SettlementAccounts v-model:company-account-id="receiptRegisterForm.company_account_id" v-model:counterparty-account-id="receiptRegisterForm.counterparty_account_id" :party-id="activeReceiptRequest?.customer_id" :company-readonly="Boolean(activeReceiptRequest?.company_account_id)" :counterparty-readonly="Boolean(activeReceiptRequest?.counterparty_account_id)" direction="receipt" />
        <el-form-item label="付款方式">
          <el-select v-model="receiptRegisterForm.payment_type" style="width: 100%">
            <el-option v-for="item in PAYMENT_TYPE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="收款方式">
          <div class="method-select-line">
            <el-select v-model="receiptRegisterForm.receipt_method" filterable allow-create default-first-option style="width: 100%" placeholder="请选择或新增收款方式">
              <el-option v-for="item in receiptMethodOptions" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <el-button @click="openReceiptMethodDialog">维护</el-button>
          </div>
        </el-form-item>
        <el-form-item label="回款时间">
          <el-date-picker v-model="receiptRegisterForm.received_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handleReceiptFileChange(event, 'single')" />
          <el-button v-if="receiptRegisterForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(receiptRegisterForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="库存流转">
          <el-checkbox v-model="receiptRegisterForm.release_to_inventory">确认后同步至库存待发货</el-checkbox>
        </el-form-item>
        <el-table v-if="activeReceiptRequest?.items?.length" :data="activeReceiptRequest.items" border size="small" class="mb-16">
          <el-table-column prop="item_code" label="编码" width="140" />
          <el-table-column prop="item_name" label="产品" min-width="180" />
          <el-table-column prop="display_specification" label="规格" width="140" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="bag_count" label="袋数" width="100"><template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template></el-table-column>
          <el-table-column prop="packaging_unit_price" label="包装单价" width="110"><template #default="scope">{{ formatMoney(scope.row.packaging_unit_price) }}</template></el-table-column>
          <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="scope">{{ formatMoney(scope.row.packaging_fee) }}</template></el-table-column>
          <el-table-column prop="quantity" label="订单数量" width="120">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="shipped_quantity" label="已发货" width="120">
            <template #default="scope">{{ formatNumber(scope.row.shipped_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="unit_price" label="单价" width="120">
            <template #default="scope">{{ formatMoney(scope.row.unit_price) }}</template>
          </el-table-column>
          <el-table-column prop="total_amount" label="金额" width="120">
            <template #default="scope">{{ formatMoney(scope.row.total_amount) }}</template>
          </el-table-column>
        </el-table>
        <el-table v-if="activeReceiptRequest?.attachments?.length" :data="activeReceiptRequest.attachments" border size="small" class="mb-16">
          <el-table-column prop="attachment_type" label="附件类型" width="140" />
          <el-table-column prop="original_name" label="文件名" min-width="220" />
          <el-table-column label="操作" width="100">
            <template #default="scope">
              <el-button size="small" text type="primary" @click="openAttachment(scope.row.id)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-form-item label="实际入账金额">
          <el-input-number v-model="receiptRegisterForm.actual_amount" :min="0.0001" :precision="4" style="width: 100%" />
        </el-form-item>
        <el-form-item label="处理说明">
          <el-input v-model="receiptRegisterForm.remark" type="textarea" :rows="3" placeholder="可填写到账凭证、金额差异或确认意见" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="receiptRegisterVisible = false">取消</el-button>
        <DelayedActionButton type="primary" @click="submitReceiptRegister">确认入账</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="mergedReceiptRegisterVisible" title="确认合并收款" width="1040px">
      <el-descriptions v-if="mergedReceiptCustomerName" :column="2" border class="mb-16">
        <el-descriptions-item label="客户">{{ mergedReceiptCustomerName }}</el-descriptions-item>
        <el-descriptions-item label="申请数量">{{ mergedReceiptRegisterForm.items.length }}</el-descriptions-item>
        <el-descriptions-item label="申请总额">{{ formatNumber(mergedReceiptRequestedTotal) }}</el-descriptions-item>
        <el-descriptions-item label="实际总额">{{ formatNumber(mergedReceiptActualTotal) }}</el-descriptions-item>
      </el-descriptions>
      <el-form :model="mergedReceiptRegisterForm" label-width="100px" class="mb-16">
        <SettlementAccounts v-model:company-account-id="mergedReceiptRegisterForm.company_account_id" v-model:counterparty-account-id="mergedReceiptRegisterForm.counterparty_account_id" :party-id="mergedReceiptRegisterForm.items[0]?.customer_id" :company-readonly="Boolean(mergedReceiptRegisterForm.items[0]?.company_account_id)" :counterparty-readonly="Boolean(mergedReceiptRegisterForm.items[0]?.counterparty_account_id)" direction="receipt" />
        <el-form-item label="付款方式">
          <el-select v-model="mergedReceiptRegisterForm.payment_type" style="width: 100%">
            <el-option v-for="item in PAYMENT_TYPE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="收款方式">
          <div class="method-select-line">
            <el-select v-model="mergedReceiptRegisterForm.receipt_method" filterable allow-create default-first-option style="width: 100%" placeholder="请选择或新增收款方式">
              <el-option v-for="item in receiptMethodOptions" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <el-button @click="openReceiptMethodDialog">维护</el-button>
          </div>
        </el-form-item>
        <el-form-item label="回款时间">
          <el-date-picker v-model="mergedReceiptRegisterForm.received_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handleReceiptFileChange(event, 'merged')" />
          <el-button v-if="mergedReceiptRegisterForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(mergedReceiptRegisterForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="库存流转">
          <el-checkbox v-model="mergedReceiptRegisterForm.release_to_inventory">确认后同步至库存待发货</el-checkbox>
        </el-form-item>
      </el-form>
      <el-table :data="mergedReceiptRegisterForm.items" stripe border>
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="packaging_name" label="包装" width="140" />
              <el-table-column prop="bag_count" label="袋数" width="100"><template #default="innerScope">{{ formatNumber(innerScope.row.bag_count) }}</template></el-table-column>
              <el-table-column prop="packaging_unit_price" label="包装单价" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_unit_price) }}</template></el-table-column>
              <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_fee) }}</template></el-table-column>
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="shipped_quantity" label="已发货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.shipped_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="request_no" label="申请单号" width="160" />
        <el-table-column prop="sales_order_no" label="客户订单号" width="160" />
        <el-table-column prop="payment_stage_label" label="收款阶段" width="120" />
        <el-table-column prop="requested_amount" label="申请金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.requested_amount) }}</template>
        </el-table-column>
        <el-table-column label="实际入账" width="160">
          <template #default="scope">
            <el-input-number v-model="scope.row.actual_amount" :min="0.0001" :precision="4" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="mergedReceiptRegisterForm" label-width="100px">
        <el-form-item label="处理说明">
          <el-input v-model="mergedReceiptRegisterForm.remark" type="textarea" :rows="3" placeholder="可填写到账凭证、差异说明或合并确认说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergedReceiptRegisterVisible = false">取消</el-button>
        <DelayedActionButton type="primary" @click="submitMergedReceiptRegister">确认入账</DelayedActionButton>
      </template>
    </el-dialog>

    <el-dialog v-model="directRegisterVisible" title="登记回款" width="1040px">
      <el-descriptions v-if="directRegisterCustomerName" :column="2" border class="mb-16">
        <el-descriptions-item label="客户">{{ directRegisterCustomerName }}</el-descriptions-item>
        <el-descriptions-item label="订单数量">{{ directRegisterForm.items.length }}</el-descriptions-item>
        <el-descriptions-item label="登记总额">{{ formatNumber(directRegisterTotal) }}</el-descriptions-item>
        <el-descriptions-item label="说明">允许董事长或财务在应收管理中直接登记单笔或同客户多笔回款</el-descriptions-item>
      </el-descriptions>
      <el-form :model="directRegisterForm" label-width="100px" class="mb-16">
        <SettlementAccounts v-model:company-account-id="directRegisterForm.company_account_id" v-model:counterparty-account-id="directRegisterForm.counterparty_account_id" :party-id="directRegisterForm.customer_id" direction="receipt" />
        <el-form-item label="回款阶段">
          <el-select v-model="directRegisterForm.payment_stage" style="width: 100%">
            <el-option label="直接登记" value="direct" />
            <el-option label="定金" value="deposit" />
            <el-option label="尾款" value="balance" />
            <el-option label="到货后全款" value="cod_full" />
            <el-option label="发货前全款" value="pre_shipment_full" />
            <el-option label="分期款" value="installment" />
          </el-select>
        </el-form-item>
        <el-form-item label="收款方式">
          <div class="method-select-line">
            <el-select v-model="directRegisterForm.receipt_method" filterable allow-create default-first-option style="width: 100%" placeholder="请选择或新增收款方式">
              <el-option v-for="item in receiptMethodOptions" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <el-button @click="openReceiptMethodDialog">维护</el-button>
          </div>
        </el-form-item>
        <el-form-item label="回款时间">
          <el-date-picker v-model="directRegisterForm.received_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="银行回单">
          <input type="file" accept=".jpg,.jpeg,.png,.pdf,.xls,.xlsx" @change="(event) => handleReceiptFileChange(event, 'direct')" />
          <el-button v-if="directRegisterForm.bank_receipt_attachment_id" class="ml-8" text type="primary" @click="openAttachment(directRegisterForm.bank_receipt_attachment_id)">查看回单</el-button>
        </el-form-item>
        <el-form-item label="库存流转">
          <el-checkbox v-model="directRegisterForm.release_to_inventory">确认后同步至库存待发货</el-checkbox>
        </el-form-item>
      </el-form>
      <el-table :data="directRegisterForm.items" stripe border>
        <el-table-column type="expand" width="52">
          <template #default="scope">
            <el-table :data="scope.row.items || []" border size="small">
              <el-table-column prop="item_code" label="编码" width="140" />
              <el-table-column prop="item_name" label="产品" min-width="180" />
              <el-table-column prop="display_specification" label="规格" width="140" />
              <el-table-column prop="packaging_name" label="包装" width="140" />
              <el-table-column prop="bag_count" label="袋数" width="100"><template #default="innerScope">{{ formatNumber(innerScope.row.bag_count) }}</template></el-table-column>
              <el-table-column prop="packaging_unit_price" label="包装单价" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_unit_price) }}</template></el-table-column>
              <el-table-column prop="packaging_fee" label="包装费" width="110"><template #default="innerScope">{{ formatMoney(innerScope.row.packaging_fee) }}</template></el-table-column>
              <el-table-column prop="quantity" label="订单数量" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.quantity) }}</template>
              </el-table-column>
              <el-table-column prop="shipped_quantity" label="已发货" width="120">
                <template #default="innerScope">{{ formatNumber(innerScope.row.shipped_quantity) }}</template>
              </el-table-column>
              <el-table-column prop="unit_price" label="单价" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.unit_price) }}</template>
              </el-table-column>
              <el-table-column prop="total_amount" label="金额" width="120">
                <template #default="innerScope">{{ formatMoney(innerScope.row.total_amount) }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="order_no" label="客户订单号" width="160" />
        <el-table-column prop="remaining_amount" label="待收金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.remaining_amount) }}</template>
        </el-table-column>
        <el-table-column label="本次回款" width="160">
          <template #default="scope">
            <el-input-number v-model="scope.row.amount" :min="0.0001" :max="scope.row.remaining_amount" :precision="4" style="width: 100%" />
          </template>
        </el-table-column>
      </el-table>
      <el-form class="mt-16" :model="directRegisterForm" label-width="100px">
        <el-form-item label="处理说明">
          <el-input v-model="directRegisterForm.remark" type="textarea" :rows="3" placeholder="可填写到账凭证、银行流水或备注信息" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="directRegisterVisible = false">取消</el-button>
        <DelayedActionButton type="primary" @click="submitDirectRegister">确认登记</DelayedActionButton>
      </template>
    </el-dialog>

    <PageSection title="回款记录" description="支持按客户订单号、客户名称、回款时间和备注搜索。">
      <template #actions><el-input v-model="recordKeyword" clearable placeholder="搜索订单号、客户、时间或备注" style="width: 280px" /></template>
      <el-table :data="pagedRecordRows" stripe>
        <el-table-column prop="company_account_label" label="企业收款账户" min-width="240" show-overflow-tooltip />
        <el-table-column prop="counterparty_account_label" label="客户付款账户" min-width="240" show-overflow-tooltip />
        <el-table-column prop="sales_order_no" label="客户订单号" width="160" />
        <el-table-column prop="customer_name" label="客户" min-width="160" />
        <el-table-column prop="amount" label="回款金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="received_at" label="回款时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.received_at) }}</template>
        </el-table-column>
        <el-table-column prop="received_by_name" label="处理人" width="120" />
        <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.remark_display || formatRemark(scope.row.remark) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="goToSalesOrder(scope.row.sales_order_id)">订单</el-button>
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

    <PageSection title="采购退款" description="采购退货退款统一在应收管理登记和查看。">
      <template #actions>
        <el-button v-if="canCreateRefund" type="warning" @click="refundVisible = true">登记采购退款</el-button>
      </template>
      <el-table :data="pagedRefundRows" stripe>
        <el-table-column prop="purchase_return_no" label="退货单号" width="160" />
        <el-table-column prop="return_type_label" label="类型" width="100" />
        <el-table-column prop="amount" label="退款金额" width="120">
          <template #default="scope">{{ formatMoney(scope.row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="refunded_at" label="退款时间" min-width="180">
          <template #default="scope">{{ formatDateTime(scope.row.refunded_at) }}</template>
        </el-table-column>
        <el-table-column prop="processed_by_name" label="处理人" width="120" />
        <el-table-column prop="remark_display" label="备注" min-width="220" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.remark_display || formatRemark(scope.row.remark) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="goToPurchaseReturn(scope.row.purchase_return_id)">退货</el-button>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="refundPagination.page"
        v-model:page-size="refundPagination.pageSize"
        :total="refundTotal"
        :page-sizes="pageSizes"
      />
    </PageSection>

    <el-dialog v-model="refundVisible" title="登记采购退款" width="560px">
      <el-form :model="refundForm" label-width="100px">
        <el-form-item label="采购退货">
          <el-select v-model="refundForm.purchase_return_id" filterable style="width: 100%">
            <el-option v-for="item in purchaseReturns" :key="item.id" :label="`${item.return_no} / ${item.purchase_order_no || item.purchase_order_id}`" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="退款金额">
          <el-input-number v-model="refundForm.amount" :min="0.0001" :precision="4" style="width: 100%" />
        </el-form-item>
        <el-form-item label="退款时间">
          <el-date-picker v-model="refundForm.refunded_at" type="datetime" style="width: 100%" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="refundForm.remark" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="refundVisible = false">取消</el-button>
        <el-button type="warning" @click="submitRefund">提交</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="receiptMethodVisible" title="收款方式维护" width="640px">
      <el-form :model="receiptMethodForm" inline>
        <el-form-item label="方式名称">
          <el-input v-model="receiptMethodForm.name" placeholder="如银行转账、微信、支付宝、承兑等" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveReceiptMethod">{{ receiptMethodForm.id ? '保存' : '新增' }}</el-button>
          <el-button @click="resetReceiptMethodForm">清空</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="receiptMethodRows" border>
        <el-table-column prop="name" label="收款方式" />
        <el-table-column prop="is_active" label="启用" width="90">
          <template #default="scope">{{ scope.row.is_active ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="editReceiptMethod(scope.row)">编辑</el-button>
            <el-button size="small" text type="danger" @click="deleteReceiptMethod(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="receiptMethodVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/checkbox/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/date-picker/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCheckbox from 'element-plus/es/components/checkbox/index.mjs'
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

import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { get, post, put, remove, upload } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import DelayedActionButton from '../../components/DelayedActionButton.vue'
import PageSection from '../../components/PageSection.vue'
import SettlementAccounts from '../../components/SettlementAccounts.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { parseFocusId } from '../../composables/useRouteFocus'
import { useAuthStore } from '../../stores/auth'
import { formatDateTime, formatNumber, formatRemark, statusLabel, statusType, formatMoney } from '../../utils/format'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const receiptRequestSectionRef = ref(null)
const receivableSummary = reactive({})
const pendingOrders = ref([])
const completedOrders = ref([])
const pendingOrderPagination = reactive({ page: 1, pageSize: 50 })
// 日期区间筛选(后端 /api/finance/receivables 已支持 date_from/date_to)
const filters = reactive({ dateRange: [] })

/** 把日期区间拼进查询串,空值不传。 */
function dateRangeParams() {
  const params = new URLSearchParams()
  if (Array.isArray(filters.dateRange) && filters.dateRange.length === 2) {
    if (filters.dateRange[0]) params.set('date_from', filters.dateRange[0])
    if (filters.dateRange[1]) params.set('date_to', filters.dateRange[1])
  }
  return params.toString()
}

function receivablesUrl() {
  const extra = dateRangeParams()
  return `/api/finance/receivables?pending_page=${pendingOrderPagination.page}&pending_page_size=${pendingOrderPagination.pageSize}${extra ? `&${extra}` : ''}`
}
const pendingOrderTotal = ref(0)
const receiptRequests = ref([])
const records = ref([])
const recordKeyword = ref('')
const refunds = ref([])
const purchaseReturns = ref([])
const receiptMethodOptions = ref([])
const receiptMethodRows = ref([])
const receiptMethodVisible = ref(false)
const receiptMethodForm = reactive({ id: null, name: '' })
const refundVisible = ref(false)
const receiptRegisterVisible = ref(false)
const mergedReceiptRegisterVisible = ref(false)
const directRegisterVisible = ref(false)
const activeReceiptRequest = ref(null)
const focusedReceiptRequestId = ref(null)
const selectedPendingOrders = ref([])
const refundForm = reactive({ purchase_return_id: null, amount: 0, refunded_at: currentDateTime(), remark: '' })
const receiptRegisterForm = reactive({
  company_account_id: null,
  counterparty_account_id: null,
  actual_amount: 0,
  remark: '',
  payment_type: '先付定金后付尾款',
  receipt_method: '',
  bank_receipt_attachment_id: null,
  received_at: '',
  release_to_inventory: false
})
const mergedReceiptRegisterForm = reactive({
  company_account_id: null,
  counterparty_account_id: null,
  items: [],
  remark: '',
  payment_type: '先付定金后付尾款',
  receipt_method: '',
  bank_receipt_attachment_id: null,
  received_at: '',
  release_to_inventory: false
})
const directRegisterForm = reactive({
  company_account_id: null,
  counterparty_account_id: null,
  customer_id: null,
  items: [],
  payment_stage: 'direct',
  remark: '',
  receipt_method: '',
  bank_receipt_attachment_id: null,
  received_at: '',
  release_to_inventory: false
})
const selectedReceiptFiles = reactive({ single: null, merged: null, direct: null })

const PAYMENT_TYPE_OPTIONS = [
  { label: '先付定金后付尾款', value: '先付定金后付尾款' },
  { label: '全款发货', value: '全款发货' },
  { label: '货到付款/分期付款', value: '货到付款/分期付款' }
]

const canCreateRefund = computed(() => auth.isRole('finance'))
const canRegisterReceipt = computed(() => ['finance', 'system_admin'].includes(auth.user?.role_code))
const purchaseRefunds = computed(() => refunds.value.filter((item) => item.business_type === 'purchase_return'))
const pendingReceivableTotal = computed(() =>
  Number(receivableSummary.pending_amount_total || 0)
)
const receivedReceivableTotal = computed(() =>
  records.value
    .filter((item) => item.receivable_plan_id)
    .reduce((sum, item) => sum + Number(item.amount || 0), 0)
)
const mergedReceiptCustomerName = computed(() => mergedReceiptRegisterForm.items[0]?.customer_name || '')
const mergedReceiptRequestedTotal = computed(() => mergedReceiptRegisterForm.items.reduce((sum, item) => sum + Number(item.requested_amount || 0), 0))
const mergedReceiptActualTotal = computed(() => mergedReceiptRegisterForm.items.reduce((sum, item) => sum + Number(item.actual_amount || 0), 0))
const directRegisterCustomerName = computed(() => directRegisterForm.items[0]?.customer_name || '')
const directRegisterTotal = computed(() => directRegisterForm.items.reduce((sum, item) => sum + Number(item.amount || 0), 0))

const pagedPendingOrders = pendingOrders
const { pagination: receiptRequestPagination, total: receiptRequestTotal, pagedItems: pagedReceiptRequests, ensureVisible: ensureReceiptRequestVisible } = useLocalPagination(receiptRequests)
const pageSizes = [10, 20, 50, 100]
const filteredRecords = computed(() => {
  const keyword = recordKeyword.value.trim().toLowerCase()
  if (!keyword) return records.value
  return records.value.filter((row) => [row.sales_order_no, row.customer_name, row.received_at, row.remark_display, row.remark]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(keyword)))
})
const { pagination: recordPagination, total: recordTotal, pagedItems: pagedRecordRows } = useLocalPagination(filteredRecords)
const { pagination: refundPagination, total: refundTotal, pagedItems: pagedRefundRows } = useLocalPagination(purchaseRefunds)

function currentDateTime() {
  const date = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function resetRefundForm() {
  refundForm.purchase_return_id = null
  refundForm.amount = 0
  refundForm.refunded_at = currentDateTime()
  refundForm.remark = ''
}

function receiptRequestRowClassName({ row }) {
  return focusedReceiptRequestId.value === row.id ? 'is-focused-row' : ''
}

function handlePendingSelectionChange(selection) {
  selectedPendingOrders.value = selection || []
}

async function scrollToReceiptRequests() {
  await nextTick()
  const target = receiptRequestSectionRef.value?.$el || receiptRequestSectionRef.value
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

async function applyFocus() {
  focusedReceiptRequestId.value = null
  const focusId = parseFocusId(route.query.focusId)
  const focusType = String(route.query.focusType || '')
  if (!focusId) return
  if (focusType === 'sales_order_approval') {
    return
  }
  if (focusType === 'sales_receipt_request') {
    const target = receiptRequests.value.find((item) => item.id === focusId)
    if (!target) return
    focusedReceiptRequestId.value = focusId
    ensureReceiptRequestVisible((item) => item.id === focusId)
    await scrollToReceiptRequests()
    if (canRegisterReceipt.value && ['pending_finance_check', 'pending_chairman_approval'].includes(target.status)) {
      openReceiptRegister(target)
    }
  }
}

async function loadReceivables() {
  const receivableData = await get(receivablesUrl())
  Object.assign(receivableSummary, receivableData.summary || {})
  pendingOrders.value = receivableData.pending_orders || []
  completedOrders.value = receivableData.completed_orders || []
  pendingOrderTotal.value = Number(receivableData.pending_orders_pagination?.total || receivableData.summary?.pending_order_count || 0)
  receiptRequests.value = receivableData.receipt_requests || []
  records.value = receivableData.records || []
  await applyFocus()
}

async function handlePendingPaginationChange({ page, pageSize }) {
  pendingOrderPagination.page = page
  pendingOrderPagination.pageSize = pageSize
  await loadReceivables()
}

async function load() {
  const [receivableData, refundData, methodData] = await Promise.all([
    get(receivablesUrl()),
    get('/api/finance/refunds'),
    get('/api/finance/payment-method-options?method_type=receipt')
  ])
  Object.assign(receivableSummary, receivableData.summary || {})
  pendingOrders.value = receivableData.pending_orders || []
  completedOrders.value = receivableData.completed_orders || []
  pendingOrderTotal.value = Number(receivableData.pending_orders_pagination?.total || receivableData.summary?.pending_order_count || 0)
  receiptRequests.value = receivableData.receipt_requests || []
  records.value = receivableData.records || []
  refunds.value = refundData || []
  receiptMethodRows.value = methodData
  receiptMethodOptions.value = methodData.filter((item) => item.is_active)
  purchaseReturns.value = await get('/api/purchases/returns')
  await applyFocus()
}

async function loadReceiptMethods() {
  const rows = await get('/api/finance/payment-method-options?method_type=receipt')
  receiptMethodRows.value = rows
  receiptMethodOptions.value = rows.filter((item) => item.is_active)
}

function openReceiptMethodDialog() {
  resetReceiptMethodForm()
  receiptMethodVisible.value = true
}

function resetReceiptMethodForm() {
  receiptMethodForm.id = null
  receiptMethodForm.name = ''
}

function editReceiptMethod(row) {
  receiptMethodForm.id = row.id
  receiptMethodForm.name = row.name
}

async function saveReceiptMethod() {
  const name = String(receiptMethodForm.name || '').trim()
  if (!name) {
    ElMessage.warning('请填写收款方式名称')
    return
  }
  if (receiptMethodForm.id) {
    await put(`/api/finance/payment-method-options/${receiptMethodForm.id}`, { name, is_active: true })
    ElMessage.success('收款方式已更新')
  } else {
    await post('/api/finance/payment-method-options', { method_type: 'receipt', name })
    ElMessage.success('收款方式已新增')
  }
  resetReceiptMethodForm()
  await loadReceiptMethods()
}

async function deleteReceiptMethod(row) {
  await remove(`/api/finance/payment-method-options/${row.id}`)
  ElMessage.success('收款方式已删除')
  await loadReceiptMethods()
}

async function submitRefund() {
  if (!refundForm.purchase_return_id || Number(refundForm.amount || 0) <= 0 || !refundForm.refunded_at) {
    ElMessage.warning('请填写完整的退款信息')
    return
  }
  await post('/api/finance/refunds', {
    business_type: 'purchase_return',
    purchase_return_id: refundForm.purchase_return_id,
    amount: refundForm.amount,
    refunded_at: refundForm.refunded_at,
    remark: refundForm.remark
  })
  ElMessage.success('采购退款已登记')
  refundVisible.value = false
  resetRefundForm()
  await load()
}

function goToSalesOrder(orderId) {
  if (!orderId) return
  router.push({ path: '/sales/orders', query: { focusId: String(orderId) } })
}

function openAttachment(id) {
  if (!id) return
  window.open(`/api/files/${id}`, '_blank')
}

function handleReceiptFileChange(event, target) {
  selectedReceiptFiles[target] = event.target.files?.[0] || null
}

async function uploadReceiptFileIfNeeded(target, businessType, businessId) {
  const file = selectedReceiptFiles[target]
  if (!file) return null
  const formData = new FormData()
  formData.append('business_type', businessType)
  formData.append('business_id', String(businessId || 0))
  formData.append('attachment_type', '银行回单')
  formData.append('file', file)
  const result = await upload('/api/files/upload', formData)
  selectedReceiptFiles[target] = null
  return result.id
}

function openReceiptRegister(row) {
  if (row.merge_no) {
    const relatedRows = receiptRequests.value.filter((item) => item.merge_no === row.merge_no && ['pending_finance_check', 'pending_chairman_approval'].includes(item.status))
    if (relatedRows.length > 1) {
      mergedReceiptRegisterForm.items = relatedRows.map((item) => ({
        id: item.id,
        request_no: item.request_no,
        sales_order_id: item.sales_order_id,
        sales_order_no: item.sales_order_no,
        customer_id: item.customer_id,
        company_account_id: item.company_account_id,
        counterparty_account_id: item.counterparty_account_id,
        customer_name: item.customer_name,
        payment_stage: item.payment_stage,
        payment_stage_label: item.payment_stage_label,
        requested_amount: Number(item.requested_amount || 0),
        actual_amount: Number(item.requested_amount || item.actual_amount || 0),
        items: item.items || []
      }))
      mergedReceiptRegisterForm.remark = row.remark_display || row.remark || ''
      mergedReceiptRegisterForm.payment_type = row.payment_type || '先付定金后付尾款'
      mergedReceiptRegisterForm.receipt_method = row.receipt_method || ''
      mergedReceiptRegisterForm.company_account_id = row.company_account_id || null
      mergedReceiptRegisterForm.counterparty_account_id = row.counterparty_account_id || null
      mergedReceiptRegisterForm.bank_receipt_attachment_id = row.bank_receipt_attachment_id || null
      mergedReceiptRegisterForm.received_at = row.receipt_at || ''
      mergedReceiptRegisterForm.release_to_inventory = false
      mergedReceiptRegisterVisible.value = true
      return
    }
  }
  activeReceiptRequest.value = row
  receiptRegisterForm.actual_amount = Number(row.requested_amount || row.actual_amount || 0)
  receiptRegisterForm.remark = row.remark_display || row.remark || ''
  receiptRegisterForm.payment_type = row.payment_type || '先付定金后付尾款'
  receiptRegisterForm.receipt_method = row.receipt_method || ''
  receiptRegisterForm.company_account_id = row.company_account_id || null
  receiptRegisterForm.counterparty_account_id = row.counterparty_account_id || null
  receiptRegisterForm.bank_receipt_attachment_id = row.bank_receipt_attachment_id || null
  receiptRegisterForm.received_at = row.receipt_at || ''
  receiptRegisterForm.release_to_inventory = false
  receiptRegisterVisible.value = true
}

function openDirectRegisterDialog(rows = selectedPendingOrders.value) {
  if (!rows.length) {
    ElMessage.warning('请至少选择一张同一客户的客户订单')
    return
  }
  const customerKeys = new Set(
    rows
      .map((item) => item.customer_id ?? item.customer_name ?? null)
      .filter((item) => item !== null && item !== undefined && item !== '')
  )
  if (customerKeys.size !== 1) {
    ElMessage.warning('仅支持同一客户的客户订单合并登记回款')
    return
  }
  directRegisterForm.customer_id = rows[0]?.customer_id ?? null
  directRegisterForm.items = rows.map((item) => ({
    sales_order_id: item.id,
    order_no: item.order_no,
    customer_id: item.customer_id,
        company_account_id: item.company_account_id,
        counterparty_account_id: item.counterparty_account_id,
        customer_name: item.customer_name,
    remaining_amount: Number(item.receivable_amount || 0) - Number(item.received_amount || 0),
    amount: Number(item.receivable_amount || 0) - Number(item.received_amount || 0),
    items: item.items || []
  }))
  directRegisterForm.payment_stage = 'direct'
  directRegisterForm.remark = ''
  directRegisterForm.receipt_method = ''
  directRegisterForm.company_account_id = null
  directRegisterForm.counterparty_account_id = null
  directRegisterForm.bank_receipt_attachment_id = null
  directRegisterForm.received_at = currentDateTime()
  directRegisterForm.release_to_inventory = false
  selectedReceiptFiles.direct = null
  directRegisterVisible.value = true
}

async function submitReceiptRegister() {
  if (!receiptRegisterForm.company_account_id) { ElMessage.warning('请选择企业收付款账户'); return }
  if (!activeReceiptRequest.value || Number(receiptRegisterForm.actual_amount || 0) <= 0) {
    ElMessage.warning('请填写实际入账金额')
    return
  }
  try {
    const uploadedId = await uploadReceiptFileIfNeeded('single', 'sales_receipt_request', activeReceiptRequest.value.id)
    await post(`/api/finance/receivables/receipt-requests/${activeReceiptRequest.value.id}/register`, {
      sales_receipt_request_id: activeReceiptRequest.value.id,
      actual_amount: receiptRegisterForm.actual_amount,
      company_account_id: receiptRegisterForm.company_account_id,
      counterparty_account_id: receiptRegisterForm.counterparty_account_id,
      remark: receiptRegisterForm.remark,
      payment_type: receiptRegisterForm.payment_type,
      receipt_method: receiptRegisterForm.receipt_method || null,
      bank_receipt_attachment_id: uploadedId || receiptRegisterForm.bank_receipt_attachment_id || null,
      received_at: receiptRegisterForm.received_at || null,
      release_to_inventory: receiptRegisterForm.release_to_inventory
    })
    ElMessage.success('客户入账已确认')
    receiptRegisterVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '客户入账登记失败')
  }
}

async function submitMergedReceiptRegister() {
  if (!mergedReceiptRegisterForm.company_account_id) { ElMessage.warning('请选择企业收付款账户'); return }
  if (!mergedReceiptRegisterForm.items.length) {
    ElMessage.warning('当前没有可确认的收款申请')
    return
  }
  try {
    const uploadedId = await uploadReceiptFileIfNeeded('merged', 'sales_receipt_request', mergedReceiptRegisterForm.items[0]?.id)
    await post('/api/finance/receivables/receipt-requests/merged-register', {
      payment_type: mergedReceiptRegisterForm.payment_type,
      company_account_id: mergedReceiptRegisterForm.company_account_id,
      counterparty_account_id: mergedReceiptRegisterForm.counterparty_account_id,
      remark: mergedReceiptRegisterForm.remark,
      receipt_method: mergedReceiptRegisterForm.receipt_method || null,
      bank_receipt_attachment_id: uploadedId || mergedReceiptRegisterForm.bank_receipt_attachment_id || null,
      received_at: mergedReceiptRegisterForm.received_at || null,
      release_to_inventory: mergedReceiptRegisterForm.release_to_inventory,
      items: mergedReceiptRegisterForm.items.map((item) => ({
        sales_receipt_request_id: item.id,
        actual_amount: Number(item.actual_amount || 0)
      }))
    })
    ElMessage.success('合并收款已确认')
    mergedReceiptRegisterVisible.value = false
    mergedReceiptRegisterForm.items = []
    await load()
  } catch (error) {
    ElMessage.error(error.message || '合并收款登记失败')
  }
}

async function submitDirectRegister() {
  if (!directRegisterForm.company_account_id) { ElMessage.warning('请选择企业收付款账户'); return }
  if (!directRegisterForm.items.length) {
    ElMessage.warning('请先选择客户订单')
    return
  }
  try {
    const uploadedId = await uploadReceiptFileIfNeeded('direct', 'sales_order', directRegisterForm.items[0]?.sales_order_id)
    await post('/api/finance/receivables/direct-register', {
      customer_id: directRegisterForm.customer_id,
      payment_stage: directRegisterForm.payment_stage,
      company_account_id: directRegisterForm.company_account_id,
      counterparty_account_id: directRegisterForm.counterparty_account_id,
      remark: directRegisterForm.remark,
      receipt_method: directRegisterForm.receipt_method || null,
      bank_receipt_attachment_id: uploadedId || directRegisterForm.bank_receipt_attachment_id || null,
      received_at: directRegisterForm.received_at || null,
      release_to_inventory: directRegisterForm.release_to_inventory,
      items: directRegisterForm.items.map((item) => ({
        sales_order_id: item.sales_order_id,
        amount: Number(item.amount || 0)
      }))
    })
    ElMessage.success('客户回款已登记')
    directRegisterVisible.value = false
    directRegisterForm.customer_id = null
    directRegisterForm.items = []
    directRegisterForm.receipt_method = ''
    directRegisterForm.company_account_id = null
    directRegisterForm.counterparty_account_id = null
    directRegisterForm.bank_receipt_attachment_id = null
    directRegisterForm.received_at = ''
    directRegisterForm.release_to_inventory = false
    await load()
  } catch (error) {
    ElMessage.error(error.message || '客户回款登记失败')
  }
}

function goToPurchaseReturn(returnId) {
  if (!returnId) return
  router.push({ path: '/purchases/returns', query: { focusId: String(returnId) } })
}

onMounted(load)
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.summary-row { margin-bottom: 0; }
.filters-row { margin-bottom: 16px; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.mb-16 { margin-bottom: 16px; }
.page-stack :deep(.is-focused-row) > td { background: #fff8e1 !important; }
.method-select-line { display: flex; gap: 8px; width: 100%; }
.method-select-line .el-select { flex: 1; }
</style>
