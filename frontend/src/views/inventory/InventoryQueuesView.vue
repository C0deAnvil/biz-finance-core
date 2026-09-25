<template>
  <div class="queue-page">
    <el-row :gutter="16">
      <el-col :span="12">
        <PageSection title="待置货列表" description="销售订单待确认时显示，便于库存提前置货。">
          <template #actions>
            <el-input v-model="placementSearch" clearable placeholder="搜索订单号、负责人或产品" style="width: 260px" @keyup.enter="searchPlacement" @clear="searchPlacement" />
            <el-button @click="searchPlacement">搜索</el-button>
            <el-button type="primary" plain @click="openManualDemand">提补货需求</el-button>
            <el-button plain @click="openMyDemands">我的补货需求</el-button>
            <el-button @click="load">刷新</el-button>
          </template>
          <el-table :data="pagedPlacementRows" stripe :row-class-name="placementRowClass">
            <el-table-column prop="order_no" label="订单号" width="170">
              <template #default="scope">
                <el-button text type="primary" @click="openOrderDetail(scope.row)">{{ scope.row.order_no }}</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="order_type_label" label="类型" width="80">
              <template #default="scope">
                <el-tag :type="orderTypeStyle(scope.row.order_type).tagType" effect="light" size="small">{{ scope.row.order_type_label || orderTypeStyle(scope.row.order_type).label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="sales_user_name" label="负责人" width="140" show-overflow-tooltip />
            <el-table-column label="商品/置货进度" min-width="520">
              <template #default="scope">
                <div v-for="it in (scope.row.items || [])" :key="it.sales_order_item_id" class="place-progress">
                  <div class="place-progress-head">
                    <span class="place-prod">{{ it.product_name }}</span>
                    <span class="place-nums">已置 {{ formatNumber(it.placed_quantity) }} / 待置 {{ formatNumber(it.remaining_to_place) }} / 可用 {{ formatNumber(it.available_quantity) }}</span>
                  </div>
                  <div v-if="it.packaging_name" class="place-nums">
                    包装 {{ it.packaging_name }}：需 {{ formatNumber(it.packaging_required_quantity) }} / 可用 {{ formatNumber(it.packaging_available_quantity) }}
                  </div>
                  <div class="place-bar">
                    <div
                      class="place-bar-fill"
                      :class="`is-${it.item_status}`"
                      :style="{ width: barPercent(it) + '%' }"
                    ></div>
                  </div>
                </div>
                <span v-if="!scope.row.items?.length" class="text-muted">-</span>
              </template>
            </el-table-column>
            <el-table-column label="置货状态" width="110">
              <template #default="scope">
                <el-tag :type="stockStatusTag(scope.row.stock_status)" effect="dark" size="small">{{ stockStatusLabel(scope.row.stock_status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="期望发货日期" width="130">
              <template #default="scope">{{ scope.row.expected_delivery_date || '-' }}</template>
            </el-table-column>
            <el-table-column label="快捷操作" width="240" fixed="right" align="center">
              <template #default="scope">
                <div class="queue-action-stack">
                  <div class="queue-action-line">
                    <el-button v-if="canConfirmFinanceEdit(scope.row)" class="queue-main-action" size="small" type="warning" @click="openOrderDetail(scope.row)">改单详情</el-button>
                    <template v-else>
                      <el-tooltip v-if="scope.row.blocked_reason" :content="scope.row.blocked_reason" placement="top">
                        <span>
                          <el-button class="queue-main-action" size="small" type="info" disabled>已置货</el-button>
                        </span>
                      </el-tooltip>
                      <el-button v-else class="queue-main-action" size="small" type="success" @click="openPlacementDialog(scope.row)">已置货</el-button>
                      <!-- 缺货时即使置货按钮被禁用，也保留独立的补货入口。 -->
                      <el-button
                        v-if="rowShortages(scope.row).length"
                        class="queue-demand-action"
                        size="small"
                        type="warning"
                        plain
                        @click="openRowDemand(scope.row)"
                      >提补货需求</el-button>
                    </template>
                  </div>
                  <div class="queue-action-line">
                    <el-button class="queue-print-action" size="small" type="primary" plain @click="handlePrintPlacement(scope.row)">打印</el-button>
                    <el-button class="queue-print-action" size="small" plain @click="handleDownloadPlacement(scope.row)">下载</el-button>
                  </div>
                </div>
              </template>
            </el-table-column>
          </el-table>
          <AppPagination
            v-model:page="placementPagination.page"
            v-model:page-size="placementPagination.pageSize"
            :total="placementTotal"
            :page-sizes="pageSizes"
            @change="handlePlacementPaginationChange"
          />
        </PageSection>
      </el-col>

      <el-col :span="12">
        <PageSection title="待发货列表" description="订单进入待发货后显示；库管在此确认本次发货数量并直接完成出库。未全部发货的订单会继续保留在列表中。">
          <template #actions>
            <el-input v-model="shipmentSearch" clearable placeholder="搜索订单号、负责人或产品" style="width: 260px" @keyup.enter="searchShipment" @clear="searchShipment" />
            <el-button @click="searchShipment">搜索</el-button>
            <el-button @click="load">刷新</el-button>
          </template>
          <el-table :data="pagedShipmentRows" stripe>
            <el-table-column prop="order_no" label="订单号" width="170">
              <template #default="scope">
                <el-button text type="primary" @click="openOrderDetail(scope.row)">{{ scope.row.order_no }}</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="sales_user_name" label="负责人" width="140" show-overflow-tooltip />
            <el-table-column label="商品/发货进度" min-width="500">
              <template #default="scope">
                <div v-for="it in (scope.row.items || [])" :key="it.sales_order_item_id" class="place-progress">
                  <div class="place-progress-head">
                    <span class="place-prod">{{ it.product_name }}<span v-if="it.display_specification">（{{ it.display_specification }}）</span></span>
                    <span class="place-nums">已发 {{ formatNumber(it.shipped_quantity) }} / 待发 {{ formatNumber(it.remaining_quantity) }}</span>
                  </div>
                  <div v-if="it.packaging_name" class="place-nums">包装 {{ it.packaging_name }}<span v-if="it.kg_per_bag">，{{ formatNumber(it.kg_per_bag) }} kg/袋</span></div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag></template>
            </el-table-column>
            <el-table-column label="财务修改" width="110">
              <template #default="scope"><el-tag v-if="scope.row.finance_edit_pending" type="warning">待确认</el-tag><span v-else>-</span></template>
            </el-table-column>
            <el-table-column label="快捷操作" width="160" fixed="right" align="center">
              <template #default="scope">
                <el-button v-if="canConfirmFinanceEdit(scope.row)" class="queue-main-action" size="small" type="warning" @click="openOrderDetail(scope.row)">改单详情</el-button>
                <el-button v-else class="queue-main-action" size="small" type="primary" @click="openShipmentDialog(scope.row)">发货出库</el-button>
              </template>
            </el-table-column>
          </el-table>
          <AppPagination
            v-model:page="shipmentPagination.page"
            v-model:page-size="shipmentPagination.pageSize"
            :total="shipmentTotal"
            :page-sizes="pageSizes"
            @change="handleShipmentPaginationChange"
          />
        </PageSection>
      </el-col>
    </el-row>

    <PageSection v-if="false" title="客户订单看板" description="仅展示去除单价和金额后的客户订单摘要，便于库存追踪已发货与已完结订单。">
      <template #actions>
        <el-button @click="load">刷新</el-button>
      </template>
      <el-table :data="pagedSalesOrderRows" stripe>
        <el-table-column prop="order_no" label="订单号" width="160">
          <template #default="scope">
            <el-button text type="primary" @click="openOrderDetail(scope.row)">{{ scope.row.order_no }}</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="customer_name" label="客户" min-width="150" show-overflow-tooltip />
        <el-table-column prop="item_summary" label="商品摘要" min-width="220" show-overflow-tooltip />
        <el-table-column prop="item_count" label="明细数" width="90" />
        <el-table-column prop="status_label" label="状态" width="110">
          <template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ scope.row.status_label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="财务" width="110">
          <template #default="scope">
            <el-tag :type="dimTag(scope.row, 'finance').type" size="small" effect="light">{{ dimTag(scope.row, 'finance').label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生产" width="110">
          <template #default="scope">
            <el-tag :type="dimTag(scope.row, 'production').type" size="small" effect="light">{{ dimTag(scope.row, 'production').label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="质检" width="110">
          <template #default="scope">
            <el-tag :type="dimTag(scope.row, 'quality').type" size="small" effect="light">{{ dimTag(scope.row, 'quality').label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="finance_edit_pending" label="改单" width="100">
          <template #default="scope">
            <el-tag v-if="scope.row.finance_edit_pending" type="warning">待确认</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="170">
          <template #default="scope">{{ formatDateTime(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="salesOrderPagination.page"
        v-model:page-size="salesOrderPagination.pageSize"
        :total="salesOrderTotal"
        :page-sizes="pageSizes"
        @change="handleSalesOrderPaginationChange"
      />
    </PageSection>

    <el-dialog v-model="shipmentDialogVisible" title="客户订单发货出库" width="920px">
      <el-form v-if="activeShipment" :model="shipmentForm" label-width="100px">
        <el-row :gutter="16" class="shipment-head">
          <el-col :span="8"><span class="field-label">客户订单：</span>{{ activeShipment.order_no }}</el-col>
          <el-col :span="8"><span class="field-label">负责人：</span>{{ activeShipment.sales_user_name || '-' }}</el-col>
          <el-col :span="8"><span class="field-label">状态：</span>{{ statusLabel(activeShipment.order_status || activeShipment.status) }}</el-col>
        </el-row>
        <el-table :data="shipmentForm.items" border>
          <el-table-column prop="product_name" label="商品" min-width="180" />
          <el-table-column prop="product_code" label="编码" width="130" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="ordered_quantity" label="订单数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.ordered_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="shipped_quantity" label="累计已发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.shipped_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remaining_quantity" label="剩余待发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.remaining_quantity) }}</template>
          </el-table-column>
          <el-table-column label="本次发货" width="160">
            <template #default="scope">
              <el-input-number
                v-model="scope.row.ship_quantity"
                :min="0"
                :max="Number(scope.row.remaining_quantity || 0)"
                :precision="2"
                style="width: 100%"
              />
            </template>
          </el-table-column>
        </el-table>
        <el-form-item class="mt-16" label="发货说明">
          <el-input v-model="shipmentForm.remark" type="textarea" :rows="3" placeholder="可填写分批发货、库存不足或其他特殊说明" />
        </el-form-item>
      </el-form>
      <el-divider content-position="left">发货照片</el-divider>
      <PhotoAttachments
        v-if="shipmentOrderId"
        :key="`ship-${shipmentOrderId}`"
        business-type="sales_order"
        :business-id="shipmentOrderId"
        attachment-type="发货照片"
        title="发货照片"
      />
      <div class="shipment-photo-hint">照片会同步到客户订单详情与订单全链时间轴，销售可直接查看。</div>
      <template #footer>
        <el-button @click="shipmentDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="shipmentSubmitting" @click="submitShipment">确认出库并发货</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="manualDemandVisible" title="手动提补货需求" width="480px">
      <el-form :model="manualDemandForm" label-width="100px">
        <el-form-item label="产品" required>
          <el-select v-model="manualDemandForm.product_id" filterable placeholder="选择产品" style="width: 100%">
            <el-option v-for="p in productOptions" :key="p.id" :label="`${p.name}（${p.fulfillment_mode === 'trade' ? '贸易型' : '生产型'}）`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="缺口数量" required>
          <el-input-number v-model="manualDemandForm.shortage_quantity" :min="0.01" :precision="2" style="width: 180px" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="manualDemandForm.remark" type="textarea" :rows="2" placeholder="缺口原因(选填)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualDemandVisible = false">取消</el-button>
        <el-button type="primary" :loading="manualDemandSubmitting" @click="submitManualDemand">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="placementDialogVisible" title="分批次置货" width="760px">
      <template v-if="activePlacement">
        <el-alert
          v-if="activePlacement.blocked_reason"
          :title="activePlacement.blocked_reason"
          type="error"
          :closable="false"
          show-icon
          class="mb-12"
        />
        <el-form label-width="100px">
          <el-row :gutter="16" class="shipment-head">
            <el-col :span="8"><span class="field-label">客户订单：</span>{{ activePlacement.order_no }}</el-col>
          <el-col :span="8"><span class="field-label">负责人：</span>{{ activePlacement.sales_user_name || '-' }}</el-col>
            <el-col :span="8">
              <span class="field-label">置货状态：</span>
              <el-tag :type="stockStatusTag(activePlacement.stock_status)" effect="dark" size="small">{{ stockStatusLabel(activePlacement.stock_status) }}</el-tag>
            </el-col>
          </el-row>
          <el-table :data="placementForm.items" border class="mt-12">
            <el-table-column prop="product_name" label="商品" min-width="160" />
            <el-table-column prop="display_specification" label="规格" width="110" />
            <el-table-column label="待置货" width="90">
              <template #default="scope">{{ formatNumber(scope.row.remaining_to_place) }}</template>
            </el-table-column>
            <el-table-column label="已置货" width="90">
              <template #default="scope">{{ formatNumber(scope.row.placed_quantity) }}</template>
            </el-table-column>
            <el-table-column label="本单可置量" width="110">
              <template #header>
                <el-tooltip
                  content="本单已锁定未置的部分 + 仓库当前空闲量。同一产品有多条明细时该值已按行分配，不可相加理解"
                  placement="top"
                ><span>本单可置量</span></el-tooltip>
              </template>
              <template #default="scope">{{ formatNumber(scope.row.available_quantity) }}</template>
            </el-table-column>
            <el-table-column label="包装库存" min-width="150">
              <template #default="scope">
                <span v-if="scope.row.packaging_name">
                  {{ scope.row.packaging_name }}：需 {{ formatNumber(scope.row.packaging_required_quantity) }} / 可用 {{ formatNumber(scope.row.packaging_available_quantity) }}
                </span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="本次置货" width="140">
              <template #default="scope">
                <el-input-number
                  v-model="scope.row.place_qty"
                  :min="0"
                  :max="Math.min(scope.row.remaining_to_place, scope.row.available_quantity)"
                  :precision="0"
                  size="small"
                  style="width: 110px"
                />
              </template>
            </el-table-column>
          </el-table>
          <div v-if="placementShortage.length" class="shortage-tip mt-12">
            <el-alert type="warning" :closable="false" show-icon>
              <template #title>库存不足,缺口合计 {{ placementShortageTotal }}。确认置货后,系统将询问是否对缺口提补货需求。</template>
            </el-alert>
          </div>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="placementDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="placementSubmitting" @click="submitPlacement">确认置货</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="myDemandsVisible" title="我的补货需求" width="760px">
      <el-table :data="myDemands" stripe border>
        <el-table-column prop="demand_no" label="补货单号" width="140" />
        <el-table-column prop="product_name" label="产品" min-width="140" />
        <el-table-column label="补货进度" width="190">
          <template #default="scope">
            <!-- shortage_quantity 是"剩余待补量":需求 100 已补 10 时它是 90。
                 单看这个数看不出进度,所以按 已补/共需 展示。 -->
            <div class="demand-progress">
              <el-progress
                :percentage="demandProgressPercent(scope.row)"
                :stroke-width="10"
                :show-text="false"
                :status="demandProgressPercent(scope.row) >= 100 ? 'success' : undefined"
              />
              <div class="demand-progress-text">
                已补 {{ formatNumber(scope.row.fulfilled_quantity || 0) }} / 共需 {{ formatNumber(demandRequired(scope.row)) }}
                <span v-if="demandPending(scope.row) > 0" class="demand-progress-pending">
                  还差 {{ formatNumber(demandPending(scope.row)) }}
                </span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="fulfillment_mode_label" label="类型" width="80" />
        <el-table-column prop="sales_order_no" label="来源订单" width="140">
          <template #default="scope">{{ scope.row.sales_order_no || '手动' }}</template>
        </el-table-column>
        <el-table-column label="进度" width="120">
          <template #default="scope">
            <el-tag :type="demandProgressTag(scope.row.progress_status)" size="small">{{ scope.row.progress_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="related_no" label="关联单据" min-width="140">
          <template #default="scope">{{ scope.row.related_no || '-' }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="myDemandsVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="demandConfirmVisible" title="提补货需求" width="640px">
      <template v-if="demandConfirmItems.length">
        <el-alert type="info" :closable="false" show-icon class="mb-12">
          <template #title>订单 {{ demandConfirmOrderNo }} 存在库存缺口,是否对以下商品提补货需求?数量默认等于缺口,可修改。</template>
        </el-alert>
        <el-table :data="demandConfirmItems" border>
          <el-table-column prop="product_name" label="商品" min-width="160" />
          <el-table-column label="缺口数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.shortage_quantity) }}</template>
          </el-table-column>
          <el-table-column label="补货数量" width="150">
            <template #default="scope">
              <el-input-number v-model="scope.row.demand_quantity" :min="1" :precision="0" size="small" style="width: 120px" />
            </template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button @click="demandConfirmVisible = false">暂不提</el-button>
        <el-button type="primary" :loading="demandSubmitting" @click="submitDemandConfirm">确认提补货需求</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" title="订单详情" width="960px">
      <template v-if="orderDetail">
        <div v-if="orderDetail.finance_edit_snapshot" class="detail-toolbar">
          <span>原始订单详情</span>
        </div>
        <el-alert
          v-if="orderDetail.finance_edit_snapshot?.before_data?.legacy_snapshot_note"
          class="mb-12"
          type="warning"
          :closable="false"
          :title="orderDetail.finance_edit_snapshot.before_data.legacy_snapshot_note"
        />
        <el-descriptions v-if="orderDetail.finance_edit_snapshot" :column="2" border>
          <el-descriptions-item label="改单人">{{ orderDetail.finance_edit_snapshot.changed_by_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="改单时间">{{ formatDateTime(orderDetail.finance_edit_snapshot.changed_at) }}</el-descriptions-item>
          <el-descriptions-item label="改单说明" :span="2">{{ orderDetail.finance_edit_snapshot.content || '-' }}</el-descriptions-item>
          <el-descriptions-item label="负责人">{{ orderDetail.finance_edit_snapshot.before_data?.sales_user_name || orderDetail.sales_user_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="经办人">{{ orderDetail.finance_edit_snapshot.before_data?.operator_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="付款方式">{{ orderDetail.finance_edit_snapshot.before_data?.payment_type || '-' }}</el-descriptions-item>
          <el-descriptions-item label="收货人">{{ orderDetail.finance_edit_snapshot.before_data?.consignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ orderDetail.finance_edit_snapshot.before_data?.consignee_phone || '-' }}</el-descriptions-item>
          <el-descriptions-item label="送货地址" :span="2">{{ orderDetail.finance_edit_snapshot.before_data?.delivery_address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="整单备注" :span="2">{{ orderDetail.finance_edit_snapshot.before_data?.order_remark || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-table v-if="orderDetail.finance_edit_snapshot" :data="orderDetail.finance_edit_snapshot.before_data?.items || []" border class="mt-16">
          <el-table-column prop="product_name" label="商品" min-width="180" />
          <el-table-column prop="product_code" label="编码" width="130" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="quantity" label="订单数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
        </el-table>

        <div class="detail-toolbar mt-16">
          <span>修改后订单详情</span>
        </div>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="客户订单">{{ orderDetail.order_no }}</el-descriptions-item>
          <el-descriptions-item label="负责人">{{ orderDetail.sales_user_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="订单状态">{{ orderDetail.status_label || statusLabel(orderDetail.status) }}</el-descriptions-item>
          <el-descriptions-item label="付款方式">{{ orderDetail.payment_type_label || '-' }}</el-descriptions-item>
          <el-descriptions-item label="财务修改确认">{{ financeEditConfirmText(orderDetail) }}</el-descriptions-item>
          <el-descriptions-item label="收货人">{{ orderDetail.consignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="联系电话">{{ orderDetail.consignee_phone || '-' }}</el-descriptions-item>
          <el-descriptions-item label="送货地址" :span="2">{{ orderDetail.delivery_address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="期望发货日期">{{ orderDetail.expected_delivery_date || '-' }}</el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">{{ orderDetail.order_remark || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="orderDetail.items || []" border class="mt-16">
          <el-table-column prop="product_name" label="商品" min-width="180" />
          <el-table-column prop="display_specification" label="规格" width="130" />
          <el-table-column prop="packaging_name" label="包装" width="140" />
          <el-table-column prop="kg_per_bag" label="kg/袋" width="100">
            <template #default="scope">{{ formatNumber(scope.row.kg_per_bag) }}</template>
          </el-table-column>
          <el-table-column prop="bag_count" label="袋数" width="100">
            <template #default="scope">{{ formatNumber(scope.row.bag_count) }}</template>
          </el-table-column>
          <el-table-column prop="quantity" label="订单数量" width="110">
            <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
          </el-table-column>
          <el-table-column prop="shipped_quantity" label="累计已发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.shipped_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remaining_shipment_quantity" label="剩余待发" width="110">
            <template #default="scope">{{ formatNumber(scope.row.remaining_shipment_quantity) }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="明细备注" min-width="180" show-overflow-tooltip />
        </el-table>
        <el-divider content-position="left">发货照片</el-divider>
        <PhotoAttachments
          v-if="detailOrderId"
          :key="`detail-ship-${detailOrderId}`"
          business-type="sales_order"
          :business-id="detailOrderId"
          attachment-type="发货照片"
          title="发货照片"
          readonly
        />
      </template>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button v-if="canConfirmFinanceEdit(orderDetail)" type="warning" @click="confirmFinanceEdit(orderDetail)">确认财务修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { downloadOrderPdf, printOrderPdf } from '../../utils/printPdf'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/progress/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tooltip/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDescriptions, { ElDescriptionsItem } from 'element-plus/es/components/descriptions/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElProgress from 'element-plus/es/components/progress/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { get, post } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import PageSection from '../../components/PageSection.vue'
import PhotoAttachments from '../../components/PhotoAttachments.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { formatDateTime, formatNumber, statusLabel, statusType } from '../../utils/format'

const route = useRoute()
const queues = reactive({ placement: [], shipment: [], salesOrders: [] })
async function handlePrintPlacement(row) {
  try { await printOrderPdf('placement', row.order_id) } catch (error) { ElMessage.error(error.message || '打印置货单失败') }
}
async function handleDownloadPlacement(row) {
  try { await downloadOrderPdf('placement', row.order_id, row.order_no) } catch (error) { ElMessage.error(error.message || '下载置货单失败') }
}
const shipmentDialogVisible = ref(false)
const activeShipment = ref(null)
const detailVisible = ref(false)
const orderDetail = ref(null)
const shipmentForm = reactive({ items: [], remark: '' })
const shipmentSubmitting = ref(false)
const placementPagination = reactive({ page: 1, pageSize: 10 })
const shipmentPagination = reactive({ page: 1, pageSize: 10 })
const salesOrderPagination = reactive({ page: 1, pageSize: 10 })
const placementSearch = ref('')
const shipmentSearch = ref('')
const placementTotal = ref(0)
const shipmentTotal = ref(0)
const salesOrderTotal = ref(0)
const pageSizes = [10, 20, 50, 100]
function matchesQueueSearch(row, keyword) {
  const text = keyword.trim().toLowerCase()
  if (!text) return true
  const itemText = (row.items || []).map((item) => [item.product_name, item.product_code, item.display_specification, item.packaging_name].filter(Boolean).join(' ')).join(' ')
  return [row.order_no, row.sales_user_name, itemText].filter(Boolean).join(' ').toLowerCase().includes(text)
}

const pagedPlacementRows = computed(() => queues.placement.filter((row) => matchesQueueSearch(row, placementSearch.value)))
const pagedShipmentRows = computed(() => queues.shipment.filter((row) => matchesQueueSearch(row, shipmentSearch.value)))
const pagedSalesOrderRows = computed(() => queues.salesOrders)

async function load() {
  const query = new URLSearchParams({
    placement_page: String(placementPagination.page),
    placement_page_size: String(placementPagination.pageSize),
    shipment_page: String(shipmentPagination.page),
    shipment_page_size: String(shipmentPagination.pageSize),
    sales_order_page: String(salesOrderPagination.page),
    sales_order_page_size: String(salesOrderPagination.pageSize),
    placement_keyword: placementSearch.value.trim(),
    shipment_keyword: shipmentSearch.value.trim(),
  })
  const data = await get(`/api/inventory/queues?${query.toString()}`)
  queues.placement = data.placement || []
  queues.shipment = data.shipment || []
  queues.salesOrders = data.sales_orders || []
  placementTotal.value = Number(data.pagination?.placement?.total || 0)
  shipmentTotal.value = Number(data.pagination?.shipment?.total || 0)
  salesOrderTotal.value = Number(data.pagination?.sales_orders?.total || 0)
  applyFocus()
}

async function handlePlacementPaginationChange({ page, pageSize }) {
  placementPagination.page = page
  placementPagination.pageSize = pageSize
  await load()
}

async function searchPlacement() {
  placementPagination.page = 1
  await load()
}

async function handleShipmentPaginationChange({ page, pageSize }) {
  shipmentPagination.page = page
  shipmentPagination.pageSize = pageSize
  await load()
}

async function searchShipment() {
  shipmentPagination.page = 1
  await load()
}

async function handleSalesOrderPaginationChange({ page, pageSize }) {
  salesOrderPagination.page = page
  salesOrderPagination.pageSize = pageSize
  await load()
}

// 发货弹窗 / 订单详情 当前对应的客户订单 id，照片按订单归档
const shipmentOrderId = ref(null)
const detailOrderId = ref(null)

function resolveOrderId(row) {
  return row?.order_id ?? row?.id ?? null
}

async function openOrderDetail(row) {
  const orderId = resolveOrderId(row)
  if (!orderId) return
  try {
    orderDetail.value = await get(`/api/inventory/sales-orders/${orderId}`)
    detailOrderId.value = orderId
    detailVisible.value = true
  } catch (error) {
    ElMessage.error(error.message || '获取订单详情失败')
  }
}

const placementDialogVisible = ref(false)
const activePlacement = ref(null)
const placementForm = reactive({ items: [] })
const placementSubmitting = ref(false)

// 销售单据类型配置(与后端 models.py ORDER_TYPE_STYLES 保持一致)
const ORDER_TYPE_STYLES = {
  normal:    { label: '普通', color: '#909399', tagType: 'info' },
  urgent:    { label: '加急', color: '#F56C6C', tagType: 'danger' },
  custom:    { label: '定制', color: '#E6A23C', tagType: 'warning' },
  important: { label: '重要', color: '#F56C6C', tagType: 'danger' },
}
function orderTypeStyle(key) {
  return ORDER_TYPE_STYLES[key] || ORDER_TYPE_STYLES.normal
}

function stockStatusLabel(status) {
  return { green: '库存充足', yellow: '部分库存', red: '库存不足' }[status] || status || '-'
}

// 全链看板:维度状态标签(财务/生产/质检)
const DIM_TAG_COLORS = {
  finance: {
    paid: 'success', settled: 'success', completed: 'success', cleared: 'success',
    partially_received: 'warning', partial_paid: 'warning',
    receipt_pending: 'danger', pending_finance_check: 'danger', rejected: 'danger', pending: 'danger',
    not_required: 'info',
  },
  production: {
    completed: 'success', not_required: 'info',
    pending: 'danger', pending_material: 'warning', pending_material_approval: 'warning', pending_material_outbound: 'warning',
    material_issued: 'primary', in_production: 'primary', producing: 'primary', processing: 'primary',
    waiting_quality: 'warning', ready_for_inbound: 'warning', pending_inbound: 'warning',
    reworked: 'danger', rework_pending: 'danger', rework_created: 'danger',
    scrapped: 'danger', partially_scrapped: 'danger',
  },
  quality: {
    passed: 'success', approved: 'success', completed: 'success', qualified: 'success', done: 'success',
    not_required: 'info',
    pending: 'warning', waiting_quality: 'warning', inspecting: 'warning',
    failed: 'danger', rejected: 'danger', unqualified: 'danger',
  },
}
function dimOf(row, name) {
  return (row.dimensions || []).find((d) => d.dimension === name) || null
}
function dimTag(row, name) {
  const d = dimOf(row, name)
  if (!d) return { label: '-', type: 'info' }
  const stage = d.current_stage || d.status_code || ''
  let label = d.current_stage_label || d.status_label || stage || '-'
  const colors = DIM_TAG_COLORS[name] || {}
  // not_required 按维度显示更明确的文案
  if (stage === 'not_required') {
    label = name === 'finance' ? '无需收款' : name === 'production' ? '无需生产' : name === 'quality' ? '无需质检' : label
  }
  return { label, type: colors[stage] || 'info' }
}
function stockStatusTag(status) {
  return { green: 'success', yellow: 'warning', red: 'danger' }[status] || 'info'
}
function barPercent(it) {
  if (!it) return 0
  const total = Number(it.placed_quantity || 0) + Number(it.remaining_to_place || 0)
  if (total <= 0) return 0
  return Math.min(100, Math.round((Number(it.placed_quantity || 0) / total) * 100))
}
function placementRowClass({ row }) {
  if (!row) return ''
  return `placement-${row.stock_status || ''}`
}

const placementShortage = computed(() => {
  if (!activePlacement.value?.items) return []
  return activePlacement.value.items.filter((it) => Number(it.remaining_to_place || 0) > Number(it.available_quantity || 0) || Number(it.packaging_shortage_quantity || 0) > 0)
})
const placementShortageTotal = computed(() =>
  placementShortage.value.reduce((sum, it) => sum + Math.max(Number(it.remaining_to_place || 0) - Number(it.available_quantity || 0), 0), 0)
)

const manualDemandVisible = ref(false)
const manualDemandSubmitting = ref(false)
const manualDemandForm = reactive({ product_id: null, shortage_quantity: 1, remark: '' })
const productOptions = ref([])

async function openManualDemand() {
  if (!productOptions.value.length) {
    productOptions.value = (await get('/api/master-data/products')) || []
  }
  Object.assign(manualDemandForm, { product_id: null, shortage_quantity: 1, remark: '' })
  manualDemandVisible.value = true
}

async function submitManualDemand() {
  if (!manualDemandForm.product_id) {
    ElMessage.warning('请选择产品')
    return
  }
  if (!manualDemandForm.shortage_quantity || manualDemandForm.shortage_quantity <= 0) {
    ElMessage.warning('请填写大于 0 的缺口数量')
    return
  }
  manualDemandSubmitting.value = true
  try {
    const data = await post('/api/inventory/fulfillment-demands', {
      product_id: manualDemandForm.product_id,
      shortage_quantity: manualDemandForm.shortage_quantity,
      remark: manualDemandForm.remark || null,
    })
    // post() 返回的是解包后的 payload.data,拿不到 payload.msg;
    // 用返回体里的单号给出可追溯的提示
    ElMessage.success(data?.demand_no ? `补货需求 ${data.demand_no} 已提交` : '补货需求已提交')
    manualDemandVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    manualDemandSubmitting.value = false
  }
}

function openPlacementDialog(row) {
  activePlacement.value = row
  placementForm.items = (row.items || []).map((it) => ({
    ...it,
    place_qty: Math.min(Number(it.remaining_to_place || 0), Number(it.available_quantity || 0)),
  }))
  placementDialogVisible.value = true
}

async function submitPlacement() {
  if (placementSubmitting.value) return
  const items = placementForm.items
    .filter((it) => Number(it.place_qty || 0) > 0)
    .map((it) => ({ sales_order_item_id: it.sales_order_item_id, placed_quantity: Number(it.place_qty || 0) }))
  if (!items.length) {
    ElMessage.warning('请填写本次置货数量')
    return
  }
  placementSubmitting.value = true
  try {
    const resp = await post(`/api/inventory/queues/${activePlacement.value.queue_id}/placement`, { action: 'placed', items })
    ElMessage.success('置货成功')
    placementDialogVisible.value = false
    await load()
  } catch (e) {
    const data = e?.data || {}
    if (data?.code === 'finished_product_stock_shortage') {
      placementDialogVisible.value = false
      await load()
      openDemandConfirm(data.shortages || [])
    } else if (data?.code === 'placement_blocked') {
      ElMessage.error(data.message || '当前订单暂不能置货')
    } else {
      ElMessage.error(e?.message || '置货失败')
    }
  } finally {
    placementSubmitting.value = false
  }
}

const myDemandsVisible = ref(false)
const myDemands = ref([])

async function openMyDemands() {
  try {
    const rows = await get('/api/inventory/fulfillment-demands')
    myDemands.value = rows || []
    myDemandsVisible.value = true
  } catch (e) {
    ElMessage.error(e?.message || '加载失败')
  }
}

const DEMAND_PROGRESS_TAGS = {
  pending: 'warning', pending_material: 'warning', purchasing: 'primary', ordered: 'primary',
  receiving: 'primary', pending_payment: 'danger', raw_purchasing: 'warning', producing: 'primary',
  pending_inbound: 'warning', completed: 'success', cancelled: 'info',
}
// 补货进度:后端返回 required_quantity(原始需求)/fulfilled_quantity(已补)/pending_quantity(待补)。
// 老数据可能没有 required_quantity,此时退回 已补+剩余待补 推算,避免显示 0。
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
  const percent = (Number(row?.fulfilled_quantity || 0) / required) * 100
  return Math.min(Math.max(Math.round(percent), 0), 100)
}

function demandProgressTag(status) {
  return DEMAND_PROGRESS_TAGS[status] || 'info'
}

const demandConfirmVisible = ref(false)
const demandSubmitting = ref(false)
const demandConfirmOrderNo = ref('')
const demandConfirmItems = ref([])

/** 该订单当前存在缺口的明细(缺口>0 才需要补货)。 */
function rowShortages(row) {
  return (row?.items || []).filter((it) => Number(it.shortage_quantity || 0) > 0)
}

/**
 * 从待置货列表直接提补货需求。
 *
 * submitDemandConfirm 用 activePlacement.queue_id 提交,openDemandConfirm 用它取单号,
 * 所以这里要先把当前行设为 activePlacement,再复用同一个确认弹窗
 * (不新建第二套弹窗)。
 */
function openRowDemand(row) {
  const shortages = rowShortages(row)
  if (!shortages.length) {
    ElMessage.info('该订单当前没有缺口，无需补货')
    return
  }
  if (!row?.queue_id) {
    // 后端对没有置货队列行的订单会返回 queue_id: null,
    // 不挡住的话会拼出 /queues/undefined/... 这种请求
    ElMessage.warning('该订单尚未进入置货队列，无法提补货需求')
    return
  }
  activePlacement.value = row
  openDemandConfirm(shortages)
}

function openDemandConfirm(shortages) {
  const rows = (shortages || []).filter((it) => Number(it.shortage_quantity || 0) > 0)
  if (!rows.length) return
  demandConfirmOrderNo.value = activePlacement.value?.order_no || ''
  demandConfirmItems.value = rows.map((it) => ({
    product_id: it.product_id,
    product_name: it.product_name,
    shortage_quantity: Number(it.shortage_quantity || 0),
    demand_quantity: Number(it.shortage_quantity || 0),
  }))
  demandConfirmVisible.value = true
}

async function submitDemandConfirm() {
  if (demandSubmitting.value) return
  const items = demandConfirmItems.value
    .filter((it) => Number(it.demand_quantity || 0) > 0)
    .map((it) => ({ product_id: it.product_id, shortage_quantity: Number(it.demand_quantity || 0) }))
  if (!items.length) {
    ElMessage.warning('请填写补货数量')
    return
  }
  demandSubmitting.value = true
  try {
    const resp = await post(`/api/inventory/queues/${activePlacement.value.queue_id}/replenishment-demands`, { items })
    // client.js 的 post() 返回的是解包后的 payload.data,拿不到 payload.msg,
    // 所以 resp?.msg 恒为 undefined —— 这里用 data 里的计数自行拼提示。
    const newCount = Number(resp?.new_count || 0)
    const reusedCount = Number(resp?.reused_count || 0)
    const reusedNos = (resp?.demands || []).filter((d) => d.reused).map((d) => d.demand_no).join('、')
    if (newCount && reusedCount) {
      ElMessage.success(`已提 ${newCount} 条补货需求；另有 ${reusedCount} 条已存在未完成需求（${reusedNos}），未重复提交`)
    } else if (newCount) {
      ElMessage.success(`已提 ${newCount} 条补货需求`)
    } else if (reusedCount) {
      // 重复点击时必须说清"没有重复提交",否则库管以为又提了一单
      ElMessage.warning(`该订单已存在未完成的补货需求（${reusedNos}），未重复提交`)
    } else {
      ElMessage.info('当前没有需要补货的缺口')
    }
    demandConfirmVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(err?.message || '提补货需求失败')
  } finally {
    demandSubmitting.value = false
  }
}

async function markPlacement(row) {
  await post(`/api/inventory/queues/${row.queue_id}/placement`, { action: 'placed' })
  ElMessage.success('已标记为已置货')
  await load()
}


function canConfirmFinanceEdit(row) {
  return Boolean(row?.finance_edit_confirm_state?.pending)
}

async function confirmFinanceEdit(row) {
  const orderId = resolveOrderId(row)
  if (!orderId || !canConfirmFinanceEdit(row)) return
  try {
    const result = await post(`/api/sales/orders/${orderId}/finance-edit/confirm`, { note: '已确认财务修改' })
    ElMessage.success('财务修改已确认')
    const nextState = { ...(row.finance_edit_confirm_state || {}), pending: false }
    Object.assign(row, {
      finance_edit_confirm_state: nextState,
      finance_edit_pending: Boolean(result?.finance_edit_pending)
    })
    if (orderDetail.value?.id === orderId) {
      orderDetail.value = {
        ...orderDetail.value,
        finance_edit_pending: Boolean(result?.finance_edit_pending),
        finance_edit_confirm_state: nextState
      }
    }
    const patchRows = (items) => items.map((item) => resolveOrderId(item) === orderId ? { ...item, finance_edit_confirm_state: nextState, finance_edit_pending: Boolean(result?.finance_edit_pending) } : item)
    queues.placement = patchRows(queues.placement)
    queues.shipment = patchRows(queues.shipment)
    queues.salesOrders = patchRows(queues.salesOrders)
  } catch (error) {
    ElMessage.error(error.message || '确认财务修改失败')
  }
}

function applyFocus() {
  const focusId = Number(route.query.focusId || 0)
  const focusType = String(route.query.focusType || '')
  if (!focusId || focusType !== 'sales_order_finance_edit_inventory_confirm') return
  const target = [...queues.placement, ...queues.shipment, ...queues.salesOrders].find((item) => resolveOrderId(item) === focusId)
  if (target) {
    openOrderDetail(target)
  }
}

function buildShipmentItems(row) {
  return (row.items || [])
    .filter((item) => Number(item.remaining_quantity || 0) > 0)
    .map((item) => ({
      ...item,
      ship_quantity: Number(item.ship_quantity ?? item.remaining_quantity ?? 0)
    }))
}

async function openShipmentDialog(row) {
  const orderId = resolveOrderId(row)
  if (!orderId) {
    ElMessage.error('未找到客户订单，无法发货')
    return
  }
  try {
    const detail = await get(`/api/inventory/sales-orders/${orderId}`)
    shipmentOrderId.value = orderId
    activeShipment.value = row
    shipmentForm.items = buildShipmentItems({
      items: (detail.items || []).map((item) => ({
        ...item,
        sales_order_item_id: item.id,
        ordered_quantity: item.quantity,
        remaining_quantity: item.remaining_shipment_quantity,
        ship_quantity: item.remaining_shipment_quantity
      }))
    })
    shipmentForm.remark = ''
    if (!shipmentForm.items.length) {
      ElMessage.warning('当前客户订单没有可发货明细，请刷新后重试')
    }
    shipmentDialogVisible.value = true
  } catch (error) {
    ElMessage.error(error.message || '获取待发货订单失败')
  }
}

async function submitShipment() {
  if (!activeShipment.value || shipmentSubmitting.value) return
  const items = shipmentForm.items
    .filter((item) => Number(item.ship_quantity || 0) > 0)
    .map((item) => ({
      sales_order_item_id: item.sales_order_item_id,
      shipped_quantity: Number(item.ship_quantity || 0)
    }))
  if (!items.length) {
    ElMessage.warning('请至少填写一项本次发货数量')
    return
  }
  const orderId = resolveOrderId(activeShipment.value)
  if (!orderId) {
    ElMessage.error('未找到客户订单，无法发货')
    return
  }
  const targetUrl = activeShipment.value.queue_id
    ? `/api/inventory/queues/${activeShipment.value.queue_id}/shipment`
    : `/api/inventory/sales-orders/${orderId}/shipment`
  await doShipment(targetUrl, items, false)
}

async function doShipment(targetUrl, items, force) {
  shipmentSubmitting.value = true
  try {
    await post(targetUrl, {
      items,
      remark: shipmentForm.remark,
      force
    })
    ElMessage.success('客户订单发货出库已完成')
    shipmentDialogVisible.value = false
    await load()
  } catch (error) {
    const blocked = error?.data?.require_force ? (error.data.blocked_conditions || []) : []
    if (error?.code === 409 && blocked.length) {
      try {
        await ElMessageBox.confirm(
          `以下条件尚未满足：\n${blocked.map((c) => '· ' + c).join('\n')}\n\n确定仍要强制发货吗？`,
          '发货条件未满足',
          { confirmButtonText: '强制发货', cancelButtonText: '再等等', type: 'warning', confirmButtonClass: 'el-button--danger' }
        )
        await doShipment(targetUrl, items, true)
      } catch {
        // 用户取消强制发货
      }
    } else {
      ElMessage.error(error.message || '客户订单发货出库失败')
    }
  } finally {
    shipmentSubmitting.value = false
  }
}

function financeEditConfirmText(row) {
  if (!row?.finance_edit_pending) return '无待确认修改'
  return row.finance_edit_confirm_state?.pending ? '待当前账号确认' : '当前账号已确认，等待其他相关人员确认'
}

onMounted(load)

watch(() => [route.query.focusId, route.query.focusType], () => {
  applyFocus()
})
</script>

<style scoped>
.queue-page :deep(.el-table .cell) { white-space: nowrap; }
.queue-main-action { min-width: 84px; font-weight: 600; }
.queue-action-stack { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.queue-action-line { display: flex; align-items: center; justify-content: center; gap: 6px; }
.queue-action-line :deep(.el-button + .el-button) { margin-left: 0; }
.queue-print-action { min-width: 0; padding: 4px 8px; font-weight: 400; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.shipment-head { margin-bottom: 16px; color: #303133; }

/* 置货进度条 */
.place-progress { margin-bottom: 8px; }
.place-progress:last-child { margin-bottom: 0; }
.place-progress-head { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; line-height: 18px; }
.place-prod { font-weight: 600; color: #303133; }
.place-nums { color: #909399; white-space: nowrap; }
.place-bar { height: 8px; border-radius: 4px; background: #f0f2f5; overflow: hidden; margin-top: 2px; }
.place-bar-fill { height: 100%; border-radius: 4px; transition: width .3s; }
.place-bar-fill.is-enough { background: #67c23a; }
.place-bar-fill.is-partial { background: #e6a23c; }
.place-bar-fill.is-none { background: #f56c6c; }
.place-bar-fill.is-done { background: #67c23a; }

/* 置货状态行颜色(浅色底,不干扰文字) */
.placement-green { --el-table-tr-bg-color: #f0f9eb; }
.placement-yellow { --el-table-tr-bg-color: #fdf6ec; }
.placement-red { --el-table-tr-bg-color: #fef0f0; }

/* 库管需要同时查看完整进度，两个队列改为全宽上下排列。 */
.queue-page > .el-row { flex-direction: column; }
.queue-page > .el-row > .el-col { width: 100%; max-width: 100%; }
.queue-page > .el-row > .el-col:first-child { order: 2; margin-top: 16px; }
.queue-page > .el-row > .el-col:last-child { order: 1; }

.shortage-tip { display: block; }
.mt-12 { margin-top: 12px; }
.text-muted { color: #c0c4cc; }

.field-label { color: #909399; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }

@media (max-width: 768px) {
  .queue-page :deep(.el-table .cell) {
    white-space: normal !important;
  }

  .queue-page :deep(.el-table) {
    min-width: 620px;
  }

  .queue-page :deep(.el-table__inner-wrapper) {
    overflow-x: auto;
  }

  .queue-main-action {
    min-width: 88px;
    min-height: 36px;
    white-space: normal;
  }

  .shipment-head,
  .detail-toolbar {
    flex-wrap: wrap;
    gap: 8px;
  }
}

.demand-progress { display: flex; flex-direction: column; gap: 4px; }
.demand-progress-text { font-size: 12px; color: var(--el-text-color-regular); line-height: 1.4; }
.demand-progress-pending { color: var(--el-color-warning); margin-left: 4px; }
.shipment-photo-hint { margin-top: 8px; color: #909399; font-size: 12px; }
</style>
