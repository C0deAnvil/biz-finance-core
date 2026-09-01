<template>
  <PageSection title="客户订单管理" description="支持新建、修改、删除待确认订单，并展示拆单父子关系。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button @click="exportTable">导出</el-button>
      <el-button v-if="canManageOrders" type="primary" @click="openCreate">新增订单</el-button>
    </template>

    <el-tabs v-model="activeStatusTab" class="status-tabs">
      <el-tab-pane :label="`审核中 (${reviewingRows.length})`" name="reviewing" />
      <el-tab-pane :label="`待发货 (${shippingRows.length})`" name="shipping" />
      <el-tab-pane :label="`已完成 (${completedRows.length})`" name="completed" />
    </el-tabs>

    <el-table :data="pagedRows" stripe :row-class-name="rowClassName" @selection-change="selection = $event">
      <el-table-column type="selection" width="55" />
      <el-table-column prop="order_no" label="订单号" width="150" />
      <el-table-column prop="split_phase" label="批次" width="80">
        <template #default="scope">第{{ scope.row.split_phase || 1 }}批</template>
      </el-table-column>
      <el-table-column prop="parent_order_no" label="母订单" width="150" />
      <el-table-column prop="customer_name" label="客户" min-width="160" />
      <el-table-column prop="sales_user" label="销售员" width="120" />
      <el-table-column prop="operator_name" label="经办人" width="120" />
      <el-table-column prop="payment_type_label" label="付款方式" min-width="180" />
      <el-table-column prop="total_amount" label="总金额" width="120">
        <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
      </el-table-column>
        <el-table-column prop="status" label="状态" width="140">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label || statusLabel(scope.row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="全链进度" min-width="240">
          <template #default="scope">
            <div class="mini-flow">
              <div
                v-for="(dim, idx) in (scope.row.flow_dims || [])"
                :key="dim.key"
                class="mini-node"
                :class="miniNodeClass(dim)"
                :title="dim.label + ': ' + (dim.stage || '未开始')"
              >
                <span>{{ dim.label }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="price_approval_status_label" label="改价审批" width="130">
          <template #default="scope">
            <el-tag v-if="scope.row.price_approval_status && scope.row.price_approval_status !== 'not_required'" :type="statusType(scope.row.price_approval_status)">
              {{ scope.row.price_approval_status_label || statusLabel(scope.row.price_approval_status) }}
            </el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
      <el-table-column prop="created_at" label="创建时间" min-width="160">
        <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="scope">
          <el-button size="small" text @click="viewOrder(scope.row.id)">查看</el-button>
          <el-button v-if="canManageOrders" size="small" text type="primary" @click="editOrder(scope.row)" :disabled="!canEditOrder(scope.row)">编辑</el-button>
          <el-button v-if="canFinanceEdit(scope.row)" size="small" text type="primary" @click="financeEditOrder(scope.row)">财务改单</el-button>
          <el-button v-if="canOpenFinanceEditDetail(scope.row)" size="small" text type="warning" @click="viewOrder(scope.row.id)">财务修改详情</el-button>
          <el-button v-if="canManageOrders" size="small" text type="danger" @click="deleteOrder(scope.row)" :disabled="scope.row.status !== 'pending_confirmation'">删除</el-button>
          <el-button v-if="canApprovePrice(scope.row)" size="small" text type="success" @click="openPriceApproval(scope.row)">改价审批</el-button>
          <el-button v-if="canManageOrders && !scope.row.is_completed_order" size="small" text type="warning" @click="completeOrder(scope.row)" :disabled="!scope.row.can_mark_completed">完结</el-button>
          <el-button v-if="canManageOrders" size="small" text type="success" @click="openAttachments(scope.row)">合同/发票</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑客户订单' : '新增客户订单'" width="980px">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="客户">
              <el-select v-model="form.customer_id" filterable style="width: 100%" @change="handleCustomerChange">
                <el-option v-for="item in customers" :key="item.id" :label="item.name" :value="item.id" />
                <el-option :label="'+ 新增客户'" :value="NEW_CUSTOMER_VALUE" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="付款方式">
              <el-select v-model="form.payment_type" style="width: 100%">
                <el-option label="先付定金后付尾款" value="先付定金后付尾款" />
                <el-option label="全款发货" value="全款发货" />
                <el-option label="货到付款/分期付款" value="货到付款/分期付款" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="经办人">
              <el-select v-model="form.operator_user_id" filterable style="width: 100%">
                <el-option v-for="item in salesUsers" :key="item.id" :label="`${item.full_name} / ${item.username}`" :value="item.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8"><el-form-item label="收货人"><el-input v-model="form.consignee" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="联系电话"><el-input v-model="form.consignee_phone" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="定金"><el-input-number v-model="form.deposit_amount" :min="0" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="收货地址"><el-input v-model="form.delivery_address" /></el-form-item></el-col>
          <el-col :span="6"><el-form-item label="运费"><el-input-number v-model="form.transport_fee" :min="0" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="24"><el-form-item label="整单备注"><el-input v-model="form.order_remark" type="textarea" :rows="2" placeholder="填写整张客户订单的补充说明" /></el-form-item></el-col>
        </el-row>

        <div class="order-total-bar">
          <span>商品金额：{{ formatNumber(orderGoodsAmount) }}</span>
          <span>包装费：{{ formatNumber(orderPackagingAmount) }}</span>
          <span>订单总额：{{ formatNumber(orderTotalAmount) }}</span>
        </div>

        <el-alert
          v-if="customerAiTip"
          :type="customerAiTip.type"
          :closable="false"
          show-icon
          class="mb-12"
        >
          <template #title>{{ customerAiTip.title }}</template>
          <div v-if="customerAi" class="ai-summary">
            <div class="ai-summary-head">
              <span>客户分群</span>
              <el-tag :type="customerAi.analysis.segment_type">{{ customerAi.analysis.segment_label }}</el-tag>
              <span>线索意向</span>
              <el-tag type="warning">{{ customerAi.analysis.lead_label }}</el-tag>
              <span>意向得分 {{ percent(customerAi.analysis.lead_score) }}</span>
            </div>
            <div class="ai-summary-body">
              <span>{{ customerAi.analysis.segment_reason || '暂无分群说明' }}</span>
              <span>近因 {{ customerAi.analysis.rfm?.recency ?? '-' }} 天</span>
              <span>购买频次 {{ customerAi.analysis.rfm?.frequency ?? '-' }}</span>
              <span>累计金额 {{ formatNumber(customerAi.analysis.rfm?.monetary) }}</span>
            </div>
          </div>
        </el-alert>

        <el-alert
          v-if="orderRecommendations.length"
          type="success"
          :closable="false"
          show-icon
          class="mb-12"
        >
          <template #title>
            <span>智能推荐：{{ orderRecommendations.map((item) => item.product_name).join('、') }}</span>
          </template>
          <div class="recommendation-list">
            <span v-for="item in orderRecommendations" :key="item.product_name">
              {{ item.product_name }}（置信度{{ percent(item.confidence) }}，提升度{{ item.lift }}）
            </span>
          </div>
        </el-alert>

        <el-alert
          v-if="stockShortageWarnings.length"
          type="warning"
          :closable="false"
          show-icon
          class="mb-12"
        >
          <template #title>
            库存不足：{{ stockShortageWarnings.map((item) => `${item.productName} 缺口 ${formatNumber(item.shortageQuantity)}`).join('；') }}
          </template>
          <div class="shortage-list">
            <span v-for="item in stockShortageWarnings" :key="item.productId">
              {{ item.productName }}：当前库存 {{ formatNumber(item.availableQuantity) }}，预计可用库存 {{ formatNumber(item.expectedAvailableQuantity) }}，本单预计占用库存 {{ formatNumber(item.requestedQuantity) }}，缺口 {{ formatNumber(item.shortageQuantity) }}
            </span>
          </div>
        </el-alert>

        <div class="detail-toolbar">
          <span>商品明细</span>
          <el-button v-if="canEditOrderForm" size="small" @click="addItem">新增明细</el-button>
        </div>
        <el-table :data="form.items" border>
          <el-table-column label="产品" min-width="200">
            <template #default="scope">
              <el-select v-model="scope.row.product_id" filterable style="width: 100%" :disabled="!canEditOrderForm" @change="() => handleProductSelect(scope.row)">
                <el-option v-for="item in products" :key="item.id" :label="item.name" :value="item.id" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="包装明细" min-width="220">
            <template #default="scope">
              <el-select v-model="scope.row.packaging_config_id" filterable style="width: 100%" :disabled="!canEditOrderForm || !scope.row.product_id" @change="() => handlePackagingSelect(scope.row)">
                <el-option
                  v-for="item in packagingOptionsForProduct(scope.row.product_id)"
                  :key="item.id"
                  :label="`${item.packaging_name} / ${formatNumber(item.kg_per_bag)}kg / ${formatNumber(item.unit_price)}元`"
                  :value="item.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="规格" width="130">
            <template #default="scope">{{ resolvePackagingRow(scope.row)?.packaging_name || productSpec(scope.row.product_id) }}</template>
          </el-table-column>
          <el-table-column label="数量(kg)" width="140">
            <template #default="scope"><el-input-number v-model="scope.row.quantity" :min="0.01" :precision="2" style="width: 100%" :disabled="!canEditOrderForm" /></template>
          </el-table-column>
          <el-table-column label="当前库存" width="120">
            <template #default="scope">{{ formatNumber(productAvailableQuantity(scope.row.product_id)) }}</template>
          </el-table-column>
          <el-table-column label="袋数" width="110">
            <template #default="scope">{{ packagingBagCount(scope.row) }}</template>
          </el-table-column>
          <el-table-column label="包装单价" width="120">
            <template #default="scope">{{ formatNumber(packagingUnitPrice(scope.row)) }}</template>
          </el-table-column>
          <el-table-column label="包装费" width="120">
            <template #default="scope">{{ formatNumber(packagingFee(scope.row)) }}</template>
          </el-table-column>
          <el-table-column label="单价" width="140">
            <template #default="scope"><el-input-number v-model="scope.row.unit_price" :min="0.01" :precision="2" style="width: 100%" :disabled="!canEditOrderForm" @change="() => handleSalesPriceChange(scope.row)" /></template>
          </el-table-column>
          <el-table-column label="标准价" width="120">
            <template #default="scope">{{ formatNumber(productStandardPrice(scope.row.product_id)) }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="160">
            <template #default="scope"><el-input v-model="scope.row.remark" :disabled="!canEditOrderForm" /></template>
          </el-table-column>
          <el-table-column label="改价备注" min-width="180">
            <template #default="scope">
              <el-input
                v-model="scope.row.price_change_remark"
                :disabled="!canManageOrders || !isSalesPriceChanged(scope.row)"
                placeholder="改价时必填"
              />
            </template>
          </el-table-column>
          <el-table-column v-if="canEditOrderForm" label="操作" width="100">
            <template #default="scope"><el-button size="small" text type="danger" @click="removeItem(scope.$index)">删除</el-button></template>
          </el-table-column>
        </el-table>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button v-if="canSubmitOrderForm" type="primary" :loading="dialogSubmitting" @click="submit">{{ financeEditing ? '提交财务修改' : '提交' }}</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="订单详情" size="50%">
      <el-descriptions v-if="detail?.finance_edit_snapshot" :column="2" border>
        <el-descriptions-item label="改单人">{{ detail.finance_edit_snapshot.changed_by_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="改单时间">{{ formatDateTime(detail.finance_edit_snapshot.changed_at) }}</el-descriptions-item>
        <el-descriptions-item label="改单说明" :span="2">{{ detail.finance_edit_snapshot.content || '-' }}</el-descriptions-item>
      </el-descriptions>

      <div v-if="detail?.finance_edit_snapshot" class="detail-toolbar mt-16">
        <span>原始订单详情</span>
      </div>
      <el-alert
        v-if="detail?.finance_edit_snapshot?.before_data?.legacy_snapshot_note"
        class="mb-12"
        type="warning"
        :closable="false"
        :title="detail.finance_edit_snapshot.before_data.legacy_snapshot_note"
      />
      <el-descriptions v-if="detail?.finance_edit_snapshot" :column="2" border>
        <el-descriptions-item label="客户">{{ detail.finance_edit_snapshot.before_data?.customer_name || detail.customer?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="经办人">{{ detail.finance_edit_snapshot.before_data?.operator_name || detail.operator_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ detail.finance_edit_snapshot.before_data?.payment_type || paymentTypeLabel(detail.payment_type) }}</el-descriptions-item>
        <el-descriptions-item label="收货人">{{ detail.finance_edit_snapshot.before_data?.consignee || '-' }}</el-descriptions-item>
        <el-descriptions-item label="联系电话">{{ detail.finance_edit_snapshot.before_data?.consignee_phone || '-' }}</el-descriptions-item>
        <el-descriptions-item label="收货地址" :span="2">{{ detail.finance_edit_snapshot.before_data?.delivery_address || '-' }}</el-descriptions-item>
        <el-descriptions-item label="定金">{{ formatNumber(detail.finance_edit_snapshot.before_data?.deposit_amount) }}</el-descriptions-item>
        <el-descriptions-item label="运费">{{ formatNumber(detail.finance_edit_snapshot.before_data?.transport_fee) }}</el-descriptions-item>
        <el-descriptions-item label="包装费（明细汇总）">{{ formatNumber(detail.finance_edit_snapshot.before_data?.packaging_fee) }}</el-descriptions-item>
        <el-descriptions-item label="整单备注" :span="2">{{ detail.finance_edit_snapshot.before_data?.order_remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table v-if="detail?.finance_edit_snapshot" :data="detail.finance_edit_snapshot.before_data?.items || []" border class="mt-16">
        <el-table-column prop="product_name" label="商品" min-width="180" />
        <el-table-column prop="packaging_name" label="包装" min-width="140" />
        <el-table-column prop="display_specification" label="规格" width="130" />
        <el-table-column prop="quantity" label="数量" width="110">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="bag_count" label="袋数" width="110">
          <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
        </el-table-column>
        <el-table-column prop="packaging_unit_price" label="包装单价" width="110">
          <template #default="scope">{{ formatNumber(scope.row.packaging_unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="packaging_fee" label="包装费" width="110">
          <template #default="scope">{{ formatNumber(scope.row.packaging_fee) }}</template>
        </el-table-column>
        <el-table-column prop="unit_price" label="单价" width="110">
          <template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" label="金额" width="110">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
      </el-table>

      <div class="detail-toolbar mt-16">
        <span>修改后订单详情</span>
      </div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="订单号">{{ detail.order_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.status_label || statusLabel(detail.status) }}</el-descriptions-item>
        <el-descriptions-item label="拆单组">{{ detail.split_group_no || '-' }}</el-descriptions-item>
        <el-descriptions-item label="批次">{{ `第${detail.split_phase || 1}批` }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ detail.customer?.name }}</el-descriptions-item>
        <el-descriptions-item label="经办人">{{ detail.operator_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="付款方式">{{ detail.payment_type_label || paymentTypeLabel(detail.payment_type) }}</el-descriptions-item>
        <el-descriptions-item label="改价审批">{{ detail.price_approval_status_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="财务修改确认">{{ financeEditConfirmText(detail) }}</el-descriptions-item>
        <el-descriptions-item label="收货人">{{ detail.consignee }}</el-descriptions-item>
        <el-descriptions-item label="联系电话">{{ detail.consignee_phone }}</el-descriptions-item>
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
        <el-descriptions-item label="收货地址" :span="2">{{ detail.delivery_address }}</el-descriptions-item>
        <el-descriptions-item label="整单备注" :span="2">{{ detail.order_remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table v-if="detail?.child_orders?.length" :data="detail.child_orders" stripe class="mt-16">
        <el-table-column prop="order_no" label="子订单号" width="150" />
        <el-table-column prop="split_phase" label="批次" width="80">
          <template #default="scope">第{{ scope.row.split_phase || 1 }}批</template>
        </el-table-column>
        <el-table-column prop="status_label" label="状态" min-width="140" />
      </el-table>
      <el-table :data="detail?.items || []" stripe class="mt-16">
        <el-table-column prop="product_name" label="产品" />
        <el-table-column prop="packaging_name" label="包装" width="140" />
        <el-table-column prop="display_specification" label="规格" width="130" />
        <el-table-column prop="quantity" label="数量">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="bag_count" label="袋数" width="110">
          <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
        </el-table-column>
        <el-table-column prop="packaging_unit_price" label="包装单价" width="110">
          <template #default="scope">{{ formatNumber(scope.row.packaging_unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="packaging_fee" label="包装费" width="110">
          <template #default="scope">{{ formatNumber(scope.row.packaging_fee) }}</template>
        </el-table-column>
        <el-table-column prop="unit_price" label="单价">
          <template #default="scope">{{ formatNumber(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" label="金额">
          <template #default="scope">{{ formatNumber(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="明细备注" min-width="160" show-overflow-tooltip />
      </el-table>
      <el-table v-if="detail?.price_change_logs?.length" :data="detail.price_change_logs" stripe class="mt-16">
        <el-table-column prop="product_name" label="改价产品" min-width="160" />
        <el-table-column prop="standard_price" label="标准价" width="110"><template #default="scope">{{ formatNumber(scope.row.standard_price) }}</template></el-table-column>
        <el-table-column prop="actual_price" label="订单价" width="110"><template #default="scope">{{ formatNumber(scope.row.actual_price) }}</template></el-table-column>
        <el-table-column prop="remark" label="改价备注" min-width="180" show-overflow-tooltip />
        <el-table-column prop="status_label" label="审批状态" width="120" />
      </el-table>
      <div class="drawer-actions mt-16">
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button v-if="detail && canConfirmFinanceEdit(detail)" type="warning" @click="confirmFinanceEdit(detail)">确认财务修改</el-button>
      </div>
    </el-drawer>

    <el-dialog v-model="priceApprovalVisible" title="销售改价审批" width="760px">
      <el-descriptions v-if="priceApprovalDetail" :column="2" border class="mb-12">
        <el-descriptions-item label="订单号">{{ priceApprovalDetail.order_no }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ priceApprovalDetail.customer?.name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="经办人">{{ priceApprovalDetail.operator_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ priceApprovalDetail.status_label || statusLabel(priceApprovalDetail.status) }}</el-descriptions-item>
        <el-descriptions-item label="整单备注" :span="2">{{ priceApprovalDetail.order_remark || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="priceApprovalDetail?.price_change_logs || []" border class="mb-12">
        <el-table-column prop="product_name" label="产品" min-width="160" />
        <el-table-column prop="standard_price" label="标准价" width="110"><template #default="scope">{{ formatNumber(scope.row.standard_price) }}</template></el-table-column>
        <el-table-column prop="actual_price" label="订单价" width="110"><template #default="scope">{{ formatNumber(scope.row.actual_price) }}</template></el-table-column>
        <el-table-column prop="remark" label="销售改价备注" min-width="180" />
      </el-table>
      <el-input v-model="priceApprovalForm.note" type="textarea" :rows="3" placeholder="可填写审批说明" />
      <template #footer>
        <el-button @click="priceApprovalVisible = false">取消</el-button>
        <el-button type="danger" @click="submitPriceApproval('rejected')">驳回</el-button>
        <el-button type="primary" @click="submitPriceApproval('approved')">通过</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="attachmentVisible" title="销售合同与发票附件" size="40%">
      <el-alert title="合同、发票上传后会同步通知财务和经理，便于后续入账审核与对账。" type="info" :closable="false" show-icon class="mb-12" />
      <AttachmentUploader v-if="attachmentOrder" business-type="sales_order" :business-id="attachmentOrder.id" :preset-types="['合同', '发票', '收款凭证', '发货单']" />
    </el-drawer>

    <el-dialog v-model="customerDialogVisible" title="新增客户" width="640px">
      <el-form :model="customerForm" label-width="90px">
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="客户名称"><el-input v-model="customerForm.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系人"><el-input v-model="customerForm.contact_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话"><el-input v-model="customerForm.phone" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="客户等级"><el-input v-model="customerForm.star_level" placeholder="可选" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="地址"><el-input v-model="customerForm.address" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="closeCustomerDialog">取消</el-button>
        <el-button type="primary" @click="submitCustomer">保存客户</el-button>
      </template>
    </el-dialog>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tabs/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tooltip/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTabs, { ElTabPane } from 'element-plus/es/components/tabs/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouteFocus } from '../../composables/useRouteFocus'

import { get, post, put, remove } from '../../api/client'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'
import { formatDateTime, formatNumber, paymentTypeLabel, statusLabel, statusType } from '../../utils/format'

const auth = useAuthStore()
const { route, focusedId: focusedOrderId, applyFocus: applyRouteFocus } = useRouteFocus()
const rows = ref([])
const customers = ref([])
const userOptions = ref([])
const products = ref([])
const packagingOptions = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const attachmentVisible = ref(false)
const customerDialogVisible = ref(false)
const detail = ref(null)
const attachmentOrder = ref(null)
const editingId = ref(null)
const financeEditing = ref(false)
const selection = ref([])
const dialogSubmitting = ref(false)
const priceApprovalVisible = ref(false)
const priceApprovalDetail = ref(null)
const priceApprovalForm = reactive({ note: '' })
const activeStatusTab = ref('reviewing')
const orderRecommendations = ref([])
const customerAi = ref(null)
const customerAiLoading = ref(false)
const canManageOrders = computed(() => auth.user?.role_code === 'sales')
const canFinanceEditOrders = computed(() => ['finance', 'system_admin'].includes(auth.user?.role_code))
const canFinanceApprovePrice = computed(() => ['finance', 'system_admin'].includes(auth.user?.role_code))
const canSubmitOrderForm = computed(() => canManageOrders.value || financeEditing.value)
const canEditOrderForm = computed(() => canManageOrders.value || financeEditing.value)
const salesUsers = computed(() => {
  const rows = userOptions.value.filter((item) => item.role_code === 'sales')
  return rows.length ? rows : userOptions.value.filter((item) => item.id === auth.user?.id)
})
const NEW_CUSTOMER_VALUE = '__create__'

const emptyForm = () => ({
  customer_id: null,
  operator_user_id: auth.user?.id || null,
  consignee: '',
  consignee_phone: '',
  delivery_address: '',
  payment_type: '先付定金后付尾款',
  deposit_amount: 0,
  transport_fee: 0,
  order_remark: '',
  items: [{ product_id: null, packaging_config_id: null, quantity: 1, unit_price: 0, remark: '', price_change_remark: '' }]
})

const form = reactive(emptyForm())
const customerForm = reactive({ name: '', contact_name: '', phone: '', address: '', star_level: '' })

function resolveStatusTab(row) {
  if (row?.status === 'completed' || row?.status === 'cancelled') return 'completed'
  if (['pending_shipment', 'shipped'].includes(row?.status)) return 'shipping'
  return 'reviewing'
}

const reviewingRows = computed(() => activeStatusTab.value === 'reviewing' ? rows.value : [])
const shippingRows = computed(() => activeStatusTab.value === 'shipping' ? rows.value : [])
const completedRows = computed(() => activeStatusTab.value === 'completed' ? rows.value : [])
const orderGoodsAmount = computed(() =>
  form.items.reduce((sum, item) => sum + Number(item.quantity || 0) * Number(item.unit_price || 0), 0)
)
const orderPackagingAmount = computed(() =>
  form.items.reduce((sum, item) => sum + packagingFee(item), 0)
)
const orderTotalAmount = computed(() => orderGoodsAmount.value + orderPackagingAmount.value + Number(form.transport_fee || 0))
const stockShortageWarnings = computed(() => {
  const quantityMap = new Map()
  for (const item of form.items) {
    const productId = Number(item.product_id || 0)
    if (!productId) continue
    quantityMap.set(productId, Number(quantityMap.get(productId) || 0) + Number(item.quantity || 0))
  }
  return [...quantityMap.entries()].map(([productId, requestedQuantity]) => {
    const product = selectedProduct(productId)
    const availableQuantity = Number(product?.stock?.available_quantity || 0)
    const expectedAvailableQuantity = Number(product?.stock?.expected_available_quantity ?? availableQuantity)
    const expectedOccupyQuantity = requestedQuantity
    const shortageQuantity = Math.max(requestedQuantity - availableQuantity, 0)
    return {
      productId,
      productName: product?.name || `产品#${productId}`,
      requestedQuantity,
      availableQuantity,
      expectedAvailableQuantity,
      expectedOccupyQuantity,
      shortageQuantity,
    }
  }).filter((item) => item.shortageQuantity > 0)
})
const customerAiTip = computed(() => {
  if (customerAiLoading.value) {
    return { type: 'info', title: '正在读取客户智能分群结果...' }
  }
  if (!customerAi.value) return null
  const analysis = customerAi.value.analysis || {}
  const customerName = customerAi.value.customer?.name || '当前客户'
  return {
    type: analysis.segment_type || 'info',
    title: `${customerName}：${analysis.segment_label || '暂无分群'}，${analysis.lead_label || '暂无意向判断'}`
  }
})
const activeRows = computed(() => {
  if (activeStatusTab.value === 'shipping') return shippingRows.value
  if (activeStatusTab.value === 'completed') return completedRows.value
  return reviewingRows.value
})

const pagination = reactive({ page: 1, pageSize: 10 })
const total = ref(0)
const pageSizes = [10, 20, 50, 100]
const pagedRows = rows
function ensureVisible(predicate) { return rows.value.find(predicate) || null }

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
  financeEditing.value = false
  orderRecommendations.value = []
  customerAi.value = null
  customerAiLoading.value = false
}

async function confirmStockShortageIfNeeded() {
  if (!stockShortageWarnings.value.length || editingId.value || financeEditing.value) return true
  const message = stockShortageWarnings.value.map((item) =>
    `${item.productName}：当前库存 ${formatNumber(item.availableQuantity)}，预计可用库存 ${formatNumber(item.expectedAvailableQuantity)}，本单预计占用库存 ${formatNumber(item.requestedQuantity)}，缺口 ${formatNumber(item.shortageQuantity)}`
  ).join('\n')
  try {
    await ElMessageBox.confirm(
      `${message}\n\n订单提交后，系统将按缺口自动生成采购/生产补货需求。是否继续提交？`,
      '商品库存不足',
      { type: 'warning', confirmButtonText: '继续提交', cancelButtonText: '返回修改' }
    )
    return true
  } catch {
    return false
  }
}

function resetCustomerForm() {
  Object.assign(customerForm, { name: '', contact_name: '', phone: '', address: '', star_level: '' })
}

async function loadBaseOptions() {
  userOptions.value = await get('/api/admin/users/options')
  customers.value = await get('/api/master-data/customers')
  products.value = await get('/api/master-data/products')
  packagingOptions.value = (await get('/api/master-data/packaging-configs?active_only=true')) || []
}

const FLOW_DONE = new Set(['completed', 'done', 'passed', 'paid', 'cleared', 'approved', 'shipped', 'not_required', 'settled', 'received', 'arrived', 'inbound'])
const FLOW_ACTIVE = new Set(['pending', 'in_progress', 'processing', 'waiting', 'partial', 'partial_paid', 'under_review', 'purchasing', 'producing', 'quality_pending'])

function miniNodeClass(dim) {
  if (FLOW_DONE.has(dim.stage)) return 'is-done'
  if (FLOW_ACTIVE.has(dim.stage)) return 'is-active'
  return 'is-pending'
}

function rowClassName({ row }) {
  return focusedOrderId.value === row.id ? 'is-focused-row' : ''
}

function setActiveStatusTabByRow(row) {
  if (!row) return
  activeStatusTab.value = resolveStatusTab(row)
}

function canEditOrder(row) {
  return ['pending_confirmation', 'pending_price_approval', 'rejected'].includes(row?.status)
}

function canApprovePrice(row) {
  return canFinanceApprovePrice.value && (row?.price_approval_status === 'pending' || row?.status === 'pending_price_approval')
}
function canFinanceEdit(row) {
  return canFinanceEditOrders.value && row && !['shipped', 'completed', 'cancelled'].includes(row.status)
}

function canConfirmFinanceEdit(row) {
  return auth.user?.role_code === 'sales' && Boolean(row?.finance_edit_confirm_state?.pending)
}

function canOpenFinanceEditDetail(row) {
  return auth.user?.role_code === 'sales' && Boolean(row?.finance_edit_confirm_state?.pending)
}


async function applyFocus() {
  await applyRouteFocus(
    (focusId) => {
      const matched = rows.value.find((item) => item.id === focusId)
      if (matched) {
        setActiveStatusTabByRow(matched)
        ensureVisible((item) => item.id === focusId)
      }
      return matched
    },
    async (matched) => {
      if (String(route.query.focusType || '') === 'sales_order_price_approval' && canApprovePrice(matched)) {
        await openPriceApproval(matched)
      } else {
        await viewOrder(matched.id)
      }
    }
  )
}

async function load() {
  const data = await get(`/api/sales/orders?tab=${activeStatusTab.value}&page=${pagination.page}&page_size=${pagination.pageSize}`)
  rows.value = data.items || []
  total.value = Number(data.pagination?.total || 0)
  await applyFocus()
}

async function handleOrderPaginationChange({ page, pageSize }) {
  pagination.page = page
  pagination.pageSize = pageSize
  await load()
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function closeCustomerDialog() {
  customerDialogVisible.value = false
  if (form.customer_id === NEW_CUSTOMER_VALUE) {
    form.customer_id = null
  }
}

function addItem() {
  form.items.push({ product_id: null, packaging_config_id: null, quantity: 1, unit_price: 0, remark: '', price_change_remark: '' })
}

function removeItem(index) {
  form.items.splice(index, 1)
}

function selectedProduct(productId) {
  return products.value.find((item) => item.id === productId) || null
}

function packagingOptionsForProduct(productId) {
  return packagingOptions.value.filter((item) => {
    if (!item.is_active) return false
    return item.entity_type === 'global'
  })
}

function selectedPackaging(productId, packagingConfigId) {
  const options = packagingOptionsForProduct(productId)
  if (packagingConfigId) {
    return options.find((item) => item.id === packagingConfigId) || null
  }
  return (
    options.find((item) => item.is_default)
    || options[0]
    || null
  )
}

function resolvePackagingRow(row) {
  return selectedPackaging(row?.product_id, row?.packaging_config_id)
}

function handleProductSelect(row) {
  if (!row) return
  const product = selectedProduct(row.product_id)
  row.unit_price = Number(product?.price || 0)
  row.price_change_remark = ''
  const packaging = selectedPackaging(row.product_id, row.packaging_config_id)
  row.packaging_config_id = packaging?.id || null
  loadOrderRecommendations()
}

function handlePackagingSelect(row) {
  const packaging = selectedPackaging(row.product_id, row.packaging_config_id)
  if (packaging) {
    row.packaging_config_id = packaging.id
  }
}

function productStandardPrice(productId) {
  return Number(selectedProduct(productId)?.price || 0)
}

function isSalesPriceChanged(row) {
  if (!row?.product_id) return false
  return Number(row.unit_price || 0).toFixed(2) !== Number(productStandardPrice(row.product_id) || 0).toFixed(2)
}

function handleSalesPriceChange(row) {
  if (!isSalesPriceChanged(row)) {
    row.price_change_remark = ''
  }
}

function productSpec(productId) {
  return selectedProduct(productId)?.specification || '-'
}

function productAvailableQuantity(productId) {
  return Number(selectedProduct(productId)?.stock?.available_quantity || 0)
}

function bagCount(productId, quantity) {
  const kgPerBag = Number(selectedPackaging(productId, null)?.kg_per_bag || selectedProduct(productId)?.packaging?.kg_per_bag || 0)
  if (!kgPerBag || !quantity) return '-'
  return Math.ceil(Number(quantity) / kgPerBag)
}

function packagingBagCount(row) {
  const packaging = resolvePackagingRow(row)
  const kgPerBag = Number(packaging?.kg_per_bag || 0)
  if (!kgPerBag || !row?.quantity) return '-'
  return Math.ceil(Number(row.quantity) / kgPerBag)
}

function packagingUnitPrice(row) {
  return Number(resolvePackagingRow(row)?.unit_price || 0)
}

function packagingFee(row) {
  const packaging = resolvePackagingRow(row)
  const kgPerBag = Number(packaging?.kg_per_bag || 0)
  const unitPrice = Number(packaging?.unit_price || 0)
  if (!kgPerBag || !row?.quantity) return 0
  return Math.ceil(Number(row.quantity || 0) / kgPerBag) * unitPrice
}

function fillCustomerContact(customerId) {
  const customer = customers.value.find((item) => item.id === customerId)
  if (!customer) return
  if (!form.consignee) form.consignee = customer.contact_name || ''
  if (!form.consignee_phone) form.consignee_phone = customer.phone || ''
  if (!form.delivery_address) form.delivery_address = customer.address || ''
}

function handleCustomerChange(value) {
  if (value === NEW_CUSTOMER_VALUE) {
    resetCustomerForm()
    customerAi.value = null
    orderRecommendations.value = []
    customerDialogVisible.value = true
    return
  }
  if (typeof value === 'number') {
    fillCustomerContact(value)
    loadCustomerAi(value)
    loadOrderRecommendations()
  }
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`
}

async function loadOrderRecommendations() {
  if (!form.customer_id || form.customer_id === NEW_CUSTOMER_VALUE) {
    orderRecommendations.value = []
    return
  }
  const productIds = [...new Set(form.items.map((item) => Number(item.product_id || 0)).filter(Boolean))]
  if (!productIds.length) {
    orderRecommendations.value = []
    return
  }
  try {
    const result = await post('/api/customer-ai/recommendations/order', {
      customer_id: Number(form.customer_id),
      product_ids: productIds
    })
    orderRecommendations.value = result.recommendations || []
  } catch {
    orderRecommendations.value = []
  }
}

async function loadCustomerAi(customerId) {
  customerAi.value = null
  if (!customerId || customerId === NEW_CUSTOMER_VALUE) return
  customerAiLoading.value = true
  try {
    customerAi.value = await get(`/api/customer-ai/customers/${customerId}`, { force: true })
  } catch {
    customerAi.value = null
  } finally {
    customerAiLoading.value = false
  }
}

async function submitCustomer() {
  if (!customerForm.name.trim() || !customerForm.contact_name.trim() || !customerForm.phone.trim() || !customerForm.address.trim()) {
    ElMessage.warning('请先填写完整的客户信息')
    return
  }
  try {
    const customer = await post('/api/master-data/customers', {
      ...customerForm,
      name: customerForm.name.trim(),
      contact_name: customerForm.contact_name.trim(),
      phone: customerForm.phone.trim(),
      address: customerForm.address.trim(),
      star_level: customerForm.star_level?.trim() || ''
    })
    customers.value = await get('/api/master-data/customers')
    form.customer_id = customer.id
    form.consignee = customer.contact_name || ''
    form.consignee_phone = customer.phone || ''
    form.delivery_address = customer.address || ''
    await loadCustomerAi(customer.id)
    customerDialogVisible.value = false
    resetCustomerForm()
    ElMessage.success('客户已新增并带入订单')
  } catch (error) {
    ElMessage.error(error.message || '新增客户失败')
  }
}

function validateOrderPayload() {
  if (!form.customer_id || form.customer_id === NEW_CUSTOMER_VALUE) {
    ElMessage.warning('请选择客户')
    return null
  }
  if (!String(form.consignee || '').trim()) {
    ElMessage.warning('请填写收货人')
    return null
  }
  if (!String(form.consignee_phone || '').trim()) {
    ElMessage.warning('请填写联系电话')
    return null
  }
  if (!String(form.delivery_address || '').trim()) {
    ElMessage.warning('请填写收货地址')
    return null
  }

  const items = form.items
    .map((item) => ({
      product_id: Number(item.product_id || 0),
      packaging_config_id: selectedPackaging(item.product_id, item.packaging_config_id)?.id || null,
      quantity: Number(item.quantity || 0),
      unit_price: Number(item.unit_price || 0),
      remark: String(item.remark || '').trim(),
      price_change_remark: String(item.price_change_remark || '').trim()
    }))
    .filter((item) => item.product_id || item.quantity || item.unit_price)

  if (!items.length) {
    ElMessage.warning('请至少填写一条商品明细')
    return null
  }

  for (const item of items) {
    if (!item.product_id) {
      ElMessage.warning('商品明细中存在未选择产品的行')
      return null
    }
    if (!item.packaging_config_id) {
      ElMessage.warning('商品明细中存在未选择包装的行')
      return null
    }
    if (item.quantity <= 0) {
      ElMessage.warning('商品数量必须大于 0')
      return null
    }
    if (item.unit_price <= 0) {
      ElMessage.warning('商品单价必须大于 0')
      return null
    }
    const standardPrice = productStandardPrice(item.product_id)
    if (!financeEditing.value && Number(item.unit_price || 0).toFixed(2) !== Number(standardPrice || 0).toFixed(2) && item.price_change_remark.length < 2) {
      ElMessage.warning('商品单价与标准价格不一致时，必须填写改价备注')
      return null
    }
  }

  return {
    customer_id: Number(form.customer_id),
    operator_user_id: form.operator_user_id || auth.user?.id,
    consignee: String(form.consignee || '').trim(),
    consignee_phone: String(form.consignee_phone || '').trim(),
    delivery_address: String(form.delivery_address || '').trim(),
    payment_type: form.payment_type,
    deposit_amount: Number(form.deposit_amount || 0),
    transport_fee: Number(form.transport_fee || 0),
    order_remark: String(form.order_remark || '').trim(),
    items
  }
}

async function submit() {
  if (dialogSubmitting.value) return
  const payload = validateOrderPayload()
  if (!payload) return
  if (!(await confirmStockShortageIfNeeded())) return

  dialogSubmitting.value = true
  try {
    if (editingId.value) {
      await put(financeEditing.value ? `/api/sales/orders/${editingId.value}/finance-edit` : `/api/sales/orders/${editingId.value}`, financeEditing.value ? { ...payload, edit_note: '财务发货前修改订单' } : payload)
      ElMessage.success('订单已更新')
    } else {
      const result = await post('/api/sales/orders', payload)
      if (result.replenishment_created) {
        const demandText = (result.replenishment_demands || []).map((item) =>
          `${item.product_name} 缺口 ${formatNumber(item.shortage_quantity)}，已生成${item.fulfillment_mode_label}补货需求 ${item.demand_no}`
        ).join('；')
        await ElMessageBox.alert(
          `订单 ${result.order_no} 已生成相关采购/生产补货需求。${result.shipment_deferred_by_shortage ? '\n当前库存不足以覆盖首批发货，订单已暂缓流转至发货，待补货完成后再推进。' : ''}${demandText ? `\n${demandText}` : ''}`,
          '补货需求已生成',
          { type: 'success', confirmButtonText: '知道了' }
        )
      }
      ElMessage.success(`订单已创建：${result.order_no}`)
    }
    dialogVisible.value = false
    resetForm()
    activeStatusTab.value = 'reviewing'
    await load()
  } catch (error) {
    ElMessage.error(error.message || '订单提交失败')
  } finally {
    dialogSubmitting.value = false
  }
}

async function viewOrder(id) {
  detail.value = await get(`/api/sales/orders/${id}`)
  detailVisible.value = true
}

async function openPriceApproval(row) {
  priceApprovalDetail.value = await get(`/api/sales/orders/${row.id}`)
  priceApprovalForm.note = ''
  priceApprovalVisible.value = true
}

async function submitPriceApproval(decision) {
  if (!priceApprovalDetail.value?.id) return
  await post(`/api/sales/orders/${priceApprovalDetail.value.id}/price-approval`, {
    decision,
    note: priceApprovalForm.note
  })
  ElMessage.success(decision === 'approved' ? '销售改价已审批通过' : '销售改价已驳回')
  priceApprovalVisible.value = false
  priceApprovalDetail.value = null
  await load()
}

async function financeEditOrder(row) {
  await editOrder(row)
  financeEditing.value = true
}

async function confirmFinanceEdit(row) {
  if (!row?.id || !canConfirmFinanceEdit(row)) return
  try {
    const result = await post(`/api/sales/orders/${row.id}/finance-edit/confirm`, { note: '?????????' })
    ElMessage.success('???????')
    const nextState = { ...(row.finance_edit_confirm_state || {}), pending: false }
    Object.assign(row, {
      finance_edit_confirm_state: nextState,
      finance_edit_pending: Boolean(result?.finance_edit_pending)
    })
    if (detail.value?.id === row.id) {
      detail.value = {
        ...detail.value,
        finance_edit_pending: Boolean(result?.finance_edit_pending),
        finance_edit_confirm_state: nextState
      }
    }
    rows.value = rows.value.map((item) => item.id === row.id ? { ...item, finance_edit_confirm_state: nextState, finance_edit_pending: Boolean(result?.finance_edit_pending) } : item)
  } catch (error) {
    ElMessage.error(error.message || '????????')
  }
}

async function editOrder(row) {
  const order = await get(`/api/sales/orders/${row.id}`)
  setActiveStatusTabByRow(row)
  resetForm()
  editingId.value = row.id
  Object.assign(form, {
    customer_id: order.customer.id,
    operator_user_id: order.operator_user_id || auth.user?.id,
    consignee: order.consignee,
    consignee_phone: order.consignee_phone,
    delivery_address: order.delivery_address,
    payment_type: order.payment_type,
    deposit_amount: order.deposit_amount,
    transport_fee: order.transport_fee,
    order_remark: order.order_remark || '',
    items: order.items.map((item) => ({
      product_id: item.product_id,
      packaging_config_id: selectedPackaging(item.product_id, item.packaging_config_id)?.id || null,
      quantity: item.quantity,
      unit_price: item.unit_price,
      remark: item.remark || '',
      price_change_remark: ''
    }))
  })
  await loadCustomerAi(order.customer.id)
  await loadOrderRecommendations()
  dialogVisible.value = true
}

async function deleteOrder(row) {
  await ElMessageBox.confirm(`确认删除订单 ${row.order_no} 吗？`, '提示', { type: 'warning' })
  await remove(`/api/sales/orders/${row.id}`)
  ElMessage.success('订单已删除')
  await load()
}

async function completeOrder(row) {
  await ElMessageBox.confirm(`确认将订单 ${row.order_no} 标记为已完成吗？`, '提示', { type: 'warning' })
  await post(`/api/sales/orders/${row.id}/complete`, {})
  ElMessage.success('订单已完结')
  if (detail.value?.id === row.id) {
    detail.value = await get(`/api/sales/orders/${row.id}`)
  }
  await load()
}

function openAttachments(row) {
  attachmentOrder.value = row
  attachmentVisible.value = true
}

function financeEditConfirmText(row) {
  if (!row?.finance_edit_pending) return '无待确认修改'
  return row.finance_edit_confirm_state?.pending ? '待当前账号确认' : '当前账号已确认，等待其他相关人员确认'
}

function exportTable() {
  exportRowsToCsv('sales_orders', rows.value)
}

onMounted(async () => {
  await loadBaseOptions()
  await load()
})

watch(activeStatusTab, async () => {
  pagination.page = 1
  await load()
})

watch(
  () => [route.query.focusId, route.query.focusType],
  async () => {
    if (rows.value.length) {
      await applyFocus()
    }
  }
)

watch(customerDialogVisible, (visible) => {
  if (!visible && form.customer_id === NEW_CUSTOMER_VALUE) {
    form.customer_id = null
  }
})

watch(
  () => form.items.map((item) => item.product_id).join(','),
  () => {
    loadOrderRecommendations()
  }
)

</script>

<style scoped>
.status-tabs { margin-bottom: 12px; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.drawer-actions { display: flex; justify-content: flex-end; gap: 12px; }
.mt-16 { margin-top: 16px; }
.mb-12 { margin-bottom: 12px; }
.order-total-bar { display: flex; justify-content: flex-end; gap: 24px; margin: 4px 0 12px; color: #303133; font-weight: 600; }
.recommendation-list { display: flex; flex-wrap: wrap; gap: 8px 16px; line-height: 1.6; }
.ai-summary { margin-top: 6px; line-height: 1.7; }
.ai-summary-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; font-weight: 600; }
.ai-summary-body { display: flex; flex-wrap: wrap; gap: 8px 16px; color: #526579; }
:deep(.is-focused-row) > td { background: #fff8e1 !important; }
.flow-steps { display: flex; flex-wrap: wrap; gap: 8px; }
.flow-step { padding: 4px 10px; border-radius: 999px; background: #f4f4f5; color: #909399; font-size: 12px; }
.flow-step.is-done { background: #f0f9eb; color: #67c23a; }
.flow-step.is-current { background: #ecf5ff; color: #409eff; font-weight: 600; }
.flow-tooltip-content { max-width: 280px; white-space: pre-line; line-height: 1.6; }

.mini-flow { display: flex; gap: 3px; flex-wrap: wrap; }
.mini-node { padding: 2px 6px; border-radius: 4px; font-size: 11px; line-height: 1.5; white-space: nowrap; }
.mini-node.is-done { background: #f0f9eb; color: #67C23A; }
.mini-node.is-active { background: #ecf5ff; color: #185f93; font-weight: 600; }
.mini-node.is-pending { background: #f4f4f5; color: #c0c4cc; }
</style>
