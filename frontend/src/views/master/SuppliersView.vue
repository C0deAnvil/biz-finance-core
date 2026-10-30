<template>
  <div class="page-stack">
    <div class="view-switcher">
      <div>
        <strong>供应商资料</strong>
        <span>在列表维护档案，在矩阵中快速比较供货能力与质检结果</span>
      </div>
      <div class="view-switcher-actions">
        <el-button :type="currentView === 'list' ? 'primary' : 'default'" @click="currentView = 'list'">供应商列表</el-button>
        <el-button :type="currentView === 'matrix' ? 'primary' : 'default'" @click="switchToMatrix">供货关系矩阵</el-button>
      </div>
    </div>

    <template v-if="currentView === 'list'">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">供应商总数</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">启用中</div><div class="summary-value">{{ enabledCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已关联采购单</div><div class="summary-value">{{ linkedCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">可供货名录数</div><div class="summary-value">{{ catalogCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="供应商基本信息" description="维护供应商基础信息，并管理该供应商可提供的产品或原料名录。">
      <template #actions>
        <el-input v-model="filters.keyword" clearable placeholder="搜索供应商 / 联系人 / 电话" class="w-260" />
        <el-select v-model="filters.status" clearable placeholder="状态" class="w-140">
          <el-option label="启用" value="enabled" />
          <el-option label="停用" value="disabled" />
        </el-select>
        <el-select v-model="filters.supplier_type" clearable placeholder="供应商类型" class="w-160">
          <el-option v-for="item in supplierTypes" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.tags" multiple collapse-tags collapse-tags-tooltip clearable placeholder="标签" class="w-200">
          <el-option v-for="tag in allTags" :key="tag" :label="tag" :value="tag" />
        </el-select>
        <el-button @click="load">刷新</el-button>
        <el-button @click="exportTable">导出</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新增供应商</el-button>
      </template>

      <el-table :data="pagedRows" stripe>
        <el-table-column prop="code" label="供应商编码" width="150" />
        <el-table-column prop="name" label="供应商名称" min-width="180" />
        <el-table-column prop="supplier_type_label" label="供应商类型" width="130">
          <template #default="scope">{{ scope.row.supplier_type_label || '-' }}</template>
        </el-table-column>
        <el-table-column prop="contact_name" label="联系人" width="140" />
        <el-table-column prop="phone" label="联系电话" width="160" />
        <el-table-column label="标签" min-width="200">
          <template #default="scope">
            <el-tag v-for="tag in scope.row.tags || []" :key="tag" size="small" type="info" class="tag-chip">{{ tag }}</el-tag>
            <span v-if="!(scope.row.tags || []).length">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="unified_social_credit_code" label="统一社会信用代码" min-width="190" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.unified_social_credit_code || '-' }}</template>
        </el-table-column>
        <el-table-column prop="purchase_order_count" label="采购单数" width="100" />
        <el-table-column prop="catalog_item_count" label="商品名录数" width="110" />
        <el-table-column label="状态" width="110">
          <template #default="scope">
            <el-tag :type="scope.row.status === 'enabled' ? 'success' : 'info'">{{ scope.row.status === 'enabled' ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="address" label="供应商地址" min-width="220" show-overflow-tooltip />
        <el-table-column label="开户行 / 账号" min-width="220" show-overflow-tooltip>
          <template #default="scope">{{ bankText(scope.row) }}</template>
        </el-table-column>
        <el-table-column label="官网" min-width="180" show-overflow-tooltip>
          <template #default="scope">
            <el-link v-if="scope.row.website" :href="normalizeUrl(scope.row.website)" target="_blank" type="primary">{{ scope.row.website }}</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="200" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.remark || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" :width="canManage ? 300 : 100" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openCatalog(scope.row)">详情</el-button>
            <template v-if="canManage">
              <el-button size="small" text type="primary" @click="openEdit(scope.row)">编辑</el-button>
              <el-button size="small" text @click="toggleStatus(scope.row)">{{ scope.row.status === 'enabled' ? '停用' : '启用' }}</el-button>
              <el-button size="small" text type="danger" @click="removeSupplier(scope.row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <AppPagination
        v-model:page="pagination.page"
        v-model:page-size="pagination.pageSize"
        :total="total"
        :page-sizes="pageSizes"
      />
    </PageSection>
    </template>

    <template v-else>
      <el-row :gutter="12" class="summary-row">
        <el-col :span="6"><el-card shadow="hover"><div class="summary-label">矩阵供应商</div><div class="summary-value">{{ matrixSummary.supplier_count || 0 }}</div></el-card></el-col>
        <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已关联品项</div><div class="summary-value">{{ matrixSummary.item_count || 0 }}</div></el-card></el-col>
        <el-col :span="6"><el-card shadow="hover"><div class="summary-label">合格供货关系</div><div class="summary-value summary-success">{{ matrixSummary.qualified_count || 0 }}</div></el-card></el-col>
        <el-col :span="6"><el-card shadow="hover"><div class="summary-label">待检 / 不合格</div><div class="summary-value summary-warning">{{ (matrixSummary.pending_count || 0) + (matrixSummary.unqualified_count || 0) }}</div></el-card></el-col>
      </el-row>

      <PageSection title="供应商供货关系矩阵" description="横向比较同一品项的供应商，纵向查看单个供应商的供货范围；颜色代表最新样品质检状态。">
        <template #actions>
          <el-select v-model="matrixFilters.itemType" class="w-140" placeholder="品类">
            <el-option label="全部品类" value="" />
            <el-option label="产品" value="product" />
            <el-option label="原料" value="material" />
            <el-option label="包装" value="packaging" />
          </el-select>
          <el-select v-model="matrixFilters.quality" clearable class="w-160" placeholder="质检状态">
            <el-option label="未送检" value="unsampled" />
            <el-option label="待收样 / 待质检" value="pending" />
            <el-option label="合格" value="qualified" />
            <el-option label="不合格" value="unqualified" />
          </el-select>
          <el-input v-model="matrixFilters.itemKeyword" clearable placeholder="搜索编码 / 名称 / 规格" class="w-260" />
          <el-input v-model="matrixFilters.supplierKeyword" clearable placeholder="筛选供应商列" class="w-200" />
          <el-button @click="resetMatrixFilters">重置</el-button>
          <el-button @click="loadMatrix(true)">刷新</el-button>
        </template>

        <div class="matrix-legend">
          <span><i class="legend-dot state-unsampled"></i>可供货 · 未送检</span>
          <span><i class="legend-dot state-pending"></i>待收样 / 待质检</span>
          <span><i class="legend-dot state-qualified"></i>质检合格</span>
          <span><i class="legend-dot state-unqualified"></i>质检不合格</span>
          <span class="matrix-hint">点击供应商名称查看详情；点击关系单元格查看记录与供价。</span>
        </div>

        <el-table v-loading="matrixLoading" :data="matrixRows" border stripe height="620" class="supplier-matrix">
          <el-table-column prop="item_type_label" label="品类" width="76" fixed="left">
            <template #default="scope"><el-tag size="small" :type="scope.row.item_type === 'product' ? 'primary' : scope.row.item_type === 'material' ? 'warning' : 'info'">{{ scope.row.item_type_label }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="item_code" label="编码" width="135" fixed="left" show-overflow-tooltip />
          <el-table-column prop="item_name" label="产品 / 原料 / 包装" width="190" fixed="left" show-overflow-tooltip />
          <el-table-column prop="specification" label="规格" width="135" fixed="left" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.specification || '-' }}</template>
          </el-table-column>
          <el-table-column v-for="supplier in matrixVisibleSuppliers" :key="supplier.id" :width="190">
            <template #header>
              <button type="button" class="supplier-header" @click="openCatalog(supplier)">
                <span>{{ supplier.name }}</span>
                <small>{{ supplier.code }} · 查看详情</small>
              </button>
            </template>
            <template #default="scope">
              <div
                v-if="matrixRelation(scope.row, supplier)"
                class="matrix-cell"
                :class="`state-${matrixRelation(scope.row, supplier).quality_state}`"
                @click="openMatrixRelation(scope.row, supplier, matrixRelation(scope.row, supplier))"
              >
                <div class="matrix-cell-title">{{ matrixStateText(matrixRelation(scope.row, supplier)) }}</div>
                <div v-if="matrixRelation(scope.row, supplier).latest_price != null" class="matrix-cell-price">最近供价 ¥{{ formatMoney(matrixRelation(scope.row, supplier).latest_price) }}</div>
                <button
                  v-if="matrixRelation(scope.row, supplier).latest_report_sample?.inspection_id"
                  type="button"
                  class="report-link"
                  @click.stop="openSampleReport(matrixRelation(scope.row, supplier).latest_report_sample)"
                >预览质检单</button>
              </div>
              <div v-else class="matrix-empty">—</div>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!matrixLoading && !matrixRows.length" description="当前筛选条件下没有供货关系" />
        <div class="matrix-footer">当前显示 {{ matrixRows.length }} 个品项 × {{ matrixVisibleSuppliers.length }} 家供应商；矩阵仅展示已维护供货名录的品项。</div>
      </PageSection>
    </template>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑供应商' : '新增供应商'" width="820px">
      <el-form :model="form" label-width="120px">
        <el-divider content-position="left">基础信息</el-divider>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="供应商名称" required><el-input v-model="form.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="供应商类型"><el-select v-model="form.supplier_type" clearable placeholder="请选择" style="width: 100%"><el-option v-for="item in supplierTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="状态"><el-select v-model="form.status" style="width: 100%"><el-option label="启用" value="enabled" /><el-option label="停用" value="disabled" /></el-select></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="统一社会信用代码"><el-input v-model="form.unified_social_credit_code" placeholder="18 位统一社会信用代码" /></el-form-item></el-col>
        </el-row>

        <el-divider content-position="left">联系方式</el-divider>
        <el-row :gutter="16">
          <el-col :span="12"><el-form-item label="联系人" required><el-input v-model="form.contact_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="联系电话" required><el-input v-model="form.phone" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="供应商地址"><el-input v-model="form.address" /></el-form-item>
        <el-form-item label="官网"><el-input v-model="form.website" placeholder="如：https://www.example.com" /></el-form-item>

        <el-divider content-position="left">收款账户</el-divider>
        <CounterpartyAccountsPanel
          v-if="editingId"
          owner-type="supplier"
          :owner-id="editingId"
          :owner-name="form.name"
          :editable="canManageAccounts"
          @changed="load"
        />
        <p v-else class="account-hint">请先保存供应商，再从“编辑”中添加收款账户。</p>

        <el-divider content-position="left">标签与备注</el-divider>
        <el-form-item label="标签">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            placeholder="可直接输入新标签后回车，如：长期合作 / 价格优 / 账期好"
            style="width: 100%"
          >
            <el-option v-for="tag in allTags" :key="tag" :label="tag" :value="tag" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="3" placeholder="合作历史、资质情况、注意事项等" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="catalogVisible" size="58%" :title="catalogSupplier ? `${catalogSupplier.name} - 可供货名录` : '供应商名录详情'">
      <div v-if="catalogSupplier" class="drawer-meta">
        <div><strong>供应商编码：</strong>{{ catalogSupplier.code }}</div>
        <div><strong>供应商类型：</strong>{{ catalogSupplier.supplier_type_label || '-' }}</div>
        <div><strong>联系人：</strong>{{ catalogSupplier.contact_name || '-' }}</div>
        <div><strong>联系电话：</strong>{{ catalogSupplier.phone || '-' }}</div>
        <div><strong>统一社会信用代码：</strong>{{ catalogSupplier.unified_social_credit_code || '-' }}</div>
        <div><strong>官网：</strong>
          <el-link v-if="catalogSupplier.website" :href="normalizeUrl(catalogSupplier.website)" target="_blank" type="primary">{{ catalogSupplier.website }}</el-link>
          <span v-else>-</span>
        </div>
        <div><strong>开户行名称：</strong>{{ catalogSupplier.bank_name || '-' }}</div>
        <div><strong>银行账号：</strong>{{ catalogSupplier.bank_account || '-' }}</div>
        <div class="drawer-meta-full"><strong>供应商地址：</strong>{{ catalogSupplier.address || '-' }}</div>
        <div class="drawer-meta-full">
          <strong>标签：</strong>
          <el-tag v-for="tag in catalogSupplier.tags || []" :key="tag" size="small" type="info" class="tag-chip">{{ tag }}</el-tag>
          <span v-if="!(catalogSupplier.tags || []).length">-</span>
        </div>
        <div class="drawer-meta-full"><strong>备注：</strong>{{ catalogSupplier.remark || '-' }}</div>
      </div>

      <CounterpartyAccountsPanel
        v-if="catalogSupplier"
        owner-type="supplier"
        :owner-id="catalogSupplier.id"
        :owner-name="catalogSupplier.name"
      />

      <div class="detail-toolbar">
        <span>可供货名录 · 样品质检矩阵</span>
        <div>
          <el-button v-if="canManageCatalog" size="small" @click="addCatalogRow">新增条目</el-button>
          <el-button v-if="canManageCatalog" size="small" type="primary" @click="saveCatalog">保存名录</el-button>
        </div>
      </div>

      <el-table :data="catalogRows" border>
        <el-table-column label="品类" width="120">
          <template #default="scope">
            <el-select v-model="scope.row.item_type" :disabled="!canManageCatalog" style="width: 100%" @change="handleCatalogTypeChange(scope.row)">
              <el-option label="产品" value="product" />
              <el-option label="原料" value="material" />
              <el-option label="包装" value="packaging" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="物料/产品" min-width="220">
          <template #default="scope">
            <el-select v-model="scope.row.target_id" filterable :disabled="!canManageCatalog" style="width: 100%">
              <el-option
                v-for="item in catalogOptions(scope.row)"
                :key="`${scope.row.item_type}-${item.id}`"
                :label="catalogOptionLabel(item, scope.row.item_type)"
                :value="item.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="规格" min-width="120">
          <template #default="scope">{{ catalogSpec(scope.row) }}</template>
        </el-table-column>
        <el-table-column label="最新样品质检" min-width="190">
          <template #default="scope">
            <template v-if="latestSample(scope.row)">
              <el-tag :type="sampleTagType(latestSample(scope.row))" size="small">{{ sampleStatusText(latestSample(scope.row)) }}</el-tag>
              <el-tag v-if="pendingSample(scope.row) && pendingSample(scope.row).id !== latestSample(scope.row).id" type="warning" size="small">新样品待处理</el-tag>
              <span class="sample-meta">{{ latestSample(scope.row).sample_no }}</span>
            </template>
            <span v-else class="sample-meta">未收样</span>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="180">
          <template #default="scope"><el-input v-model="scope.row.remark" :disabled="!canManageCatalog" /></template>
        </el-table-column>
        <el-table-column label="操作" width="270">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openPriceHistory(scope.row)">价格详情</el-button>
            <el-button size="small" text type="primary" @click="openSampleHistory(scope.row)">样品记录</el-button>
            <el-button v-if="canManageCatalog && scope.row.id" size="small" text type="success" @click="openSampleCreate(scope.row)">登记收样</el-button>
            <el-button v-if="canManageCatalog" size="small" text type="danger" @click="removeCatalogRow(scope.$index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!catalogRows.length" description="暂无可供货商品名录" class="mt-16" />
    </el-drawer>

    <el-dialog v-model="sampleCreateVisible" title="登记供应商待收样" width="600px">
      <el-form :model="sampleForm" label-width="105px">
        <el-form-item label="供应商"><el-input :model-value="catalogSupplier?.name || '-'" disabled /></el-form-item>
        <el-form-item label="送检品项"><el-input :model-value="sampleTarget ? catalogItemName(sampleTarget) : '-'" disabled /></el-form-item>
        <el-form-item label="接收仓库" required>
          <el-select v-model="sampleForm.warehouse_id" placeholder="选择负责收样的仓库" style="width: 100%">
            <el-option v-for="item in warehouses" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="样品数量" required><el-input-number v-model="sampleForm.quantity" :min="0.0001" :precision="4" style="width: 100%" /></el-form-item>
        <el-form-item label="批次号"><el-input v-model="sampleForm.batch_no" placeholder="留空则使用样品单号；库管收样时可修改" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="sampleForm.remark" type="textarea" :rows="3" placeholder="送样用途、检验要求等" /></el-form-item>
      </el-form>
      <p class="sample-meta">登记后由目标仓库库管接收、编码贴标并初筛；初筛合格才生成质检任务，不合格可填写原因直接结案。样品不计入可用库存。</p>
      <template #footer><el-button @click="sampleCreateVisible = false">取消</el-button><el-button type="primary" @click="submitSample">提交待收样</el-button></template>
    </el-dialog>

    <el-dialog v-model="sampleHistoryVisible" :title="sampleHistoryTitle" width="980px">
      <el-table :data="sampleHistoryRows" stripe>
        <el-table-column prop="sample_no" label="样品单号" width="145" />
        <el-table-column prop="warehouse_name" label="接收仓库" width="130" />
        <el-table-column prop="batch_no" label="批次号" min-width="130" />
        <el-table-column label="数量" width="110"><template #default="scope">{{ formatNumber(scope.row.quantity) }} {{ scope.row.unit }}</template></el-table-column>
        <el-table-column label="实收" width="110"><template #default="scope">{{ scope.row.received_quantity == null ? '-' : formatNumber(scope.row.received_quantity) }} {{ scope.row.received_quantity == null ? '' : scope.row.unit }}</template></el-table-column>
        <el-table-column prop="storage_location" label="存放位置" min-width="120" />
        <el-table-column prop="label_code" label="标签码" min-width="135"><template #default="scope">{{ scope.row.label_code || '-' }}</template></el-table-column>
        <el-table-column label="初筛" width="110"><template #default="scope"><el-tag :type="scope.row.preliminary_result === 'rejected' ? 'danger' : scope.row.preliminary_result === 'passed' ? 'success' : 'info'">{{ scope.row.preliminary_result === 'rejected' ? '不合格' : scope.row.preliminary_result === 'passed' ? '合格' : '待初筛' }}</el-tag></template></el-table-column>
        <el-table-column prop="preliminary_reason" label="初筛说明" min-width="180" show-overflow-tooltip><template #default="scope">{{ scope.row.preliminary_reason || '-' }}</template></el-table-column>
        <el-table-column label="质检结果" width="135"><template #default="scope"><el-tag :type="sampleTagType(scope.row)">{{ sampleStatusText(scope.row) }}</el-tag></template></el-table-column>
        <el-table-column prop="report_summary" label="报告摘要" min-width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="120"><template #default="scope"><el-button v-if="scope.row.inspection_id" text type="primary" @click="openSampleReport(scope.row)">查看报告</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!sampleHistoryRows.length" description="该品项暂无样品记录" />
    </el-dialog>

    <el-dialog v-model="sampleReportVisible" title="样品质检报告与附件" width="800px">
      <p>{{ sampleReport?.result_text || '-' }}</p>
      <p>报告摘要：{{ sampleReport?.report_summary || '-' }}</p>
      <AttachmentUploader v-if="sampleReport?.inspection_id" business-type="quality_inspection" :business-id="sampleReport.inspection_id" readonly />
    </el-dialog>

    <el-dialog v-model="priceHistoryVisible" :title="priceHistoryTitle" width="760px">
      <div v-if="priceHistorySummary" class="price-summary">
        <span>最新供价：{{ formatMoney(priceHistorySummary.latest_price) }}</span>
        <span>最新采购单：{{ priceHistorySummary.latest_order_no || '-' }}</span>
        <span>记录数：{{ priceHistoryRows.length }}</span>
      </div>
      <el-table :data="priceHistoryRows" stripe>
        <el-table-column prop="purchase_order_no" label="采购单号" width="160" />
        <el-table-column prop="quantity" label="采购数量" width="120">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="unit_price" label="历史单价" width="120">
          <template #default="scope">{{ formatMoney(scope.row.unit_price) }}</template>
        </el-table-column>
        <el-table-column prop="total_amount" label="金额" width="130">
          <template #default="scope">{{ formatMoney(scope.row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="采购时间" min-width="170" />
      </el-table>
      <el-empty v-if="!priceHistoryRows.length" description="暂无历史供价记录" class="mt-16" />
      <template #footer>
        <el-button @click="priceHistoryVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="matrixRelationVisible" title="供货关系详情" width="660px">
      <div v-if="matrixRelationDetail" class="relation-detail">
        <div><span>供应商</span><strong>{{ matrixRelationDetail.supplier.name }}</strong></div>
        <div><span>品项</span><strong>{{ matrixRelationDetail.item.item_code }} / {{ matrixRelationDetail.item.item_name }}</strong></div>
        <div><span>品类 / 规格</span><strong>{{ matrixRelationDetail.item.item_type_label }} / {{ matrixRelationDetail.item.specification || '-' }}</strong></div>
        <div><span>最新质检</span><strong>{{ matrixStateText(matrixRelationDetail.relation) }}</strong></div>
        <div><span>最近供价</span><strong>{{ matrixRelationDetail.relation.latest_price == null ? '暂无采购价格' : `¥${formatMoney(matrixRelationDetail.relation.latest_price)}` }}</strong></div>
        <div><span>最近采购单</span><strong>{{ matrixRelationDetail.relation.latest_order_no || '-' }}</strong></div>
        <div class="relation-detail-full"><span>供货备注</span><strong>{{ matrixRelationDetail.relation.remark || '-' }}</strong></div>
        <div v-if="matrixRelationDetail.relation.latest_report_sample" class="relation-detail-full"><span>质检摘要</span><strong>{{ matrixRelationDetail.relation.latest_report_sample.report_summary || matrixRelationDetail.relation.latest_report_sample.result_text || '-' }}</strong></div>
      </div>
      <template #footer>
        <el-button @click="openCatalog(matrixRelationDetail.supplier)">供应商详情</el-button>
        <el-button @click="openPriceHistoryFromMatrix">历史供价</el-button>
        <el-button v-if="matrixRelationDetail?.relation.latest_report_sample?.inspection_id" type="primary" @click="openSampleReport(matrixRelationDetail.relation.latest_report_sample)">预览质检单</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/link/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'

import ElButton from 'element-plus/es/components/button/index.mjs'
import ElCard from 'element-plus/es/components/card/index.mjs'
import ElCol from 'element-plus/es/components/col/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElLink from 'element-plus/es/components/link/index.mjs'
import ElRow from 'element-plus/es/components/row/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import { get, post, put, remove } from '../../api/client'
import AppPagination from '../../components/AppPagination.vue'
import AttachmentUploader from '../../components/AttachmentUploader.vue'
import CounterpartyAccountsPanel from '../../components/CounterpartyAccountsPanel.vue'
import PageSection from '../../components/PageSection.vue'
import { useLocalPagination } from '../../composables/useLocalPagination'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'
import { exportRowsToCsv } from '../../utils/export'

const auth = useAuthStore()
const pagePermission = usePagePermission()

const rows = ref([])
const products = ref([])
const materials = ref([])
const packagings = ref([])
const currentView = ref('list')
const dialogVisible = ref(false)
const catalogVisible = ref(false)
const editingId = ref(null)
const catalogSupplier = ref(null)
const filters = reactive({ keyword: '', status: '', supplier_type: '', tags: [] })
const supplierTypes = ref([])
const usedTags = ref([])

function emptyForm() {
  return {
    name: '',
    contact_name: '',
    phone: '',
    address: '',
    status: 'enabled',
    unified_social_credit_code: '',
    supplier_type: '',
    website: '',
    bank_name: '',
    bank_account: '',
    remark: '',
    tags: []
  }
}

const form = reactive(emptyForm())
const catalogRows = ref([])
const warehouses = ref([])
const samples = ref([])
const sampleTarget = ref(null)
const sampleCreateVisible = ref(false)
const sampleHistoryVisible = ref(false)
const sampleReportVisible = ref(false)
const sampleHistoryRows = ref([])
const sampleHistoryTitle = ref('样品记录')
const sampleReport = ref(null)
const sampleForm = reactive({ warehouse_id: null, quantity: 1, batch_no: '', remark: '' })
const priceHistoryVisible = ref(false)
const priceHistoryRows = ref([])
const priceHistoryTitle = ref('历史供价详情')
const priceHistorySummary = ref(null)
const matrixData = ref({ suppliers: [], items: [], relations: [], summary: {} })
const matrixLoading = ref(false)
const matrixLoaded = ref(false)
const matrixFilters = reactive({ itemType: '', quality: '', itemKeyword: '', supplierKeyword: '' })
const matrixRelationVisible = ref(false)
const matrixRelationDetail = ref(null)

const canManageAccounts = computed(() => ['purchase', 'master_data_admin', 'finance', 'finance_super', 'system_admin'].includes(auth.user?.role_code))
const canManage = computed(() => ['purchase', 'finance', 'finance_super', 'master_data_admin'].includes(auth.user?.role_code) && pagePermission.can('create'))
const canManageCatalog = computed(() => ['purchase', 'finance', 'finance_super', 'master_data_admin'].includes(auth.user?.role_code))

const allTags = computed(() => {
  const set = new Set(usedTags.value)
  rows.value.forEach((item) => (item.tags || []).forEach((tag) => set.add(tag)))
  return [...set].sort()
})

const filteredRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.value.filter((item) => {
    if (filters.status && item.status !== filters.status) return false
    if (filters.supplier_type && item.supplier_type !== filters.supplier_type) return false
    if (filters.tags.length) {
      const tags = item.tags || []
      if (!filters.tags.every((tag) => tags.includes(tag))) return false
    }
    if (!keyword) return true
    return [item.name, item.contact_name, item.phone, item.code, item.unified_social_credit_code, item.address, item.bank_name, item.bank_account, item.website, item.remark, ...(item.tags || [])]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const { pagination, total, pageSizes, pagedItems: pagedRows } = useLocalPagination(filteredRows)
const enabledCount = computed(() => filteredRows.value.filter((item) => item.status === 'enabled').length)
const linkedCount = computed(() => filteredRows.value.filter((item) => Number(item.purchase_order_count || 0) > 0).length)
const catalogCount = computed(() => filteredRows.value.reduce((total, item) => total + Number(item.catalog_item_count || 0), 0))
const matrixSummary = computed(() => matrixData.value.summary || {})
const matrixVisibleSuppliers = computed(() => {
  const keyword = matrixFilters.supplierKeyword.trim().toLowerCase()
  return (matrixData.value.suppliers || []).filter((supplier) => {
    if (!keyword) return true
    return [supplier.name, supplier.code, supplier.supplier_type_label, ...(supplier.tags || [])]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(keyword))
  })
})
const matrixRelationMap = computed(() => new Map(
  (matrixData.value.relations || []).map((relation) => [`${relation.item_key}:${relation.supplier_id}`, relation])
))
const matrixRows = computed(() => {
  const keyword = matrixFilters.itemKeyword.trim().toLowerCase()
  const visibleSupplierIds = new Set(matrixVisibleSuppliers.value.map((supplier) => supplier.id))
  return (matrixData.value.items || []).filter((item) => {
    if (matrixFilters.itemType && item.item_type !== matrixFilters.itemType) return false
    if (keyword && ![item.item_code, item.item_name, item.specification, item.item_type_label]
      .filter(Boolean).some((value) => String(value).toLowerCase().includes(keyword))) return false
    if (!matrixFilters.quality) return true
    return (matrixData.value.relations || []).some((relation) => (
      relation.item_key === item.key &&
      visibleSupplierIds.has(relation.supplier_id) &&
      relation.quality_state === matrixFilters.quality
    ))
  })
})

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
}

function normalizeUrl(url) {
  const value = String(url || '').trim()
  if (!value) return ''
  return /^https?:\/\//i.test(value) ? value : `https://${value}`
}

function bankText(row) {
  const parts = [row.bank_name, row.bank_account].filter(Boolean)
  return parts.length ? parts.join(' / ') : '-'
}

function buildPayload(source) {
  return {
    name: (source.name || '').trim(),
    contact_name: (source.contact_name || '').trim(),
    phone: (source.phone || '').trim(),
    address: (source.address || '').trim(),
    status: source.status || 'enabled',
    unified_social_credit_code: (source.unified_social_credit_code || '').trim() || null,
    supplier_type: source.supplier_type || null,
    website: (source.website || '').trim() || null,

    remark: (source.remark || '').trim() || null,
    tags: source.tags || []
  }
}

function normalizeCatalogRows(items = []) {
  return items.map((item) => ({
    id: item.id,
    item_type: item.item_type || (item.product_id ? 'product' : item.material_id ? 'material' : 'packaging'),
    target_id: item.product_id || item.material_id || item.packaging_config_id || item.item_id || item.target_id,
    product_id: item.product_id,
    material_id: item.material_id,
    packaging_config_id: item.packaging_config_id,
    item_name: item.item_name || item.product_name || item.material_name || item.packaging_name,
    item_code: item.item_code || item.product_code || item.material_code,
    latest_price: item.latest_price,
    latest_order_no: item.latest_order_no,
    latest_price_at: item.latest_price_at,
    remark: item.remark || ''
  }))
}

async function load() {
  rows.value = await get('/api/master-data/suppliers')
}

async function loadTagCatalog() {
  const data = await get('/api/master-data/supplier-tag-catalog')
  supplierTypes.value = data.supplier_types || []
  usedTags.value = data.used_tags || []
}

async function loadCatalogOptions() {
  if (!products.value.length) {
    products.value = await get('/api/master-data/products')
  }
  if (!materials.value.length) {
    materials.value = await get('/api/master-data/materials')
  }
  if (!packagings.value.length) {
    packagings.value = await get('/api/master-data/packaging-configs?active_only=true')
  }
}

async function loadMatrix(force = false) {
  if (matrixLoaded.value && !force) return
  matrixLoading.value = true
  try {
    matrixData.value = await get('/api/master-data/supplier-catalog-matrix', { force: true })
    matrixLoaded.value = true
  } catch (error) {
    ElMessage.error(error?.message || '供货关系矩阵加载失败')
  } finally {
    matrixLoading.value = false
  }
}

async function switchToMatrix() {
  currentView.value = 'matrix'
  await loadMatrix()
}

function resetMatrixFilters() {
  Object.assign(matrixFilters, { itemType: '', quality: '', itemKeyword: '', supplierKeyword: '' })
}

function matrixRelation(item, supplier) {
  return matrixRelationMap.value.get(`${item.key}:${supplier.id}`) || null
}

function matrixStateText(relation) {
  if (!relation) return '-'
  if (relation.quality_state === 'qualified') return `合格${relation.latest_sample?.grade ? ` · ${relation.latest_sample.grade}` : ''}`
  if (relation.quality_state === 'unqualified') return `不合格${relation.latest_sample?.grade ? ` · ${relation.latest_sample.grade}` : ''}`
  if (relation.latest_sample?.status === 'pending_receipt') return '待库管收样'
  if (relation.latest_sample?.status === 'pending_quality') return '待质检'
  return '可供货 · 未送检'
}

function openMatrixRelation(item, supplier, relation) {
  matrixRelationDetail.value = { item, supplier, relation }
  matrixRelationVisible.value = true
}

async function openPriceHistoryFromMatrix() {
  if (!matrixRelationDetail.value) return
  const { item, supplier, relation } = matrixRelationDetail.value
  catalogSupplier.value = supplier
  await openPriceHistory({
    item_type: item.item_type,
    target_id: item.item_id,
    item_name: item.item_name,
    latest_price: relation.latest_price,
    latest_order_no: relation.latest_order_no
  })
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name,
    contact_name: row.contact_name,
    phone: row.phone,
    address: row.address,
    status: row.status || 'enabled',
    unified_social_credit_code: row.unified_social_credit_code || '',
    supplier_type: row.supplier_type || '',
    website: row.website || '',
    bank_name: row.bank_name || '',
    bank_account: row.bank_account || '',
    remark: row.remark || '',
    tags: [...(row.tags || [])]
  })
  dialogVisible.value = true
}

async function openCatalog(row) {
  await loadCatalogOptions()
  const [detail, sampleRows] = await Promise.all([
    get(`/api/master-data/suppliers/${row.id}/catalog`),
    get(`/api/supplier-samples?supplier_id=${row.id}`, { force: true })
  ])
  catalogSupplier.value = detail.supplier
  catalogRows.value = normalizeCatalogRows(detail.items)
  samples.value = sampleRows || []
  catalogVisible.value = true
}

function samplesFor(row) {
  const targetId = Number(row.target_id || row.product_id || row.material_id || row.packaging_config_id)
  return samples.value.filter((sample) => sample.item_type === row.item_type && Number(sample.product_id || sample.material_id || sample.packaging_config_id) === targetId)
}

function latestSample(row) {
  const rows = samplesFor(row)
  return rows.find((sample) => ['completed', 'preliminary_rejected'].includes(sample.status)) || rows[0] || null
}

function pendingSample(row) {
  return samplesFor(row).find((sample) => !['completed', 'preliminary_rejected'].includes(sample.status)) || null
}

function sampleStatusText(row) {
  if (!row) return '未收样'
  if (row.status === 'pending_receipt') return '待库管收样'
  if (row.status === 'pending_quality') return '待质检'
  if (row.status === 'preliminary_rejected') return '初筛不合格'
  return row.is_passed ? `合格 · ${row.grade || '-'}` : `不合格 · ${row.grade || '-'}`
}

function sampleTagType(row) {
  if (row.status === 'preliminary_rejected') return 'danger'
  if (row.status === 'completed') return row.is_passed ? 'success' : 'danger'
  return 'warning'
}

async function openSampleCreate(row) {
  if (!row.id) {
    ElMessage.warning('请先保存名录条目，再登记收样')
    return
  }
  sampleTarget.value = row
  warehouses.value = await get('/api/master-data/warehouses')
  Object.assign(sampleForm, { warehouse_id: null, quantity: 1, batch_no: '', remark: '' })
  sampleCreateVisible.value = true
}

async function submitSample() {
  if (!catalogSupplier.value || !sampleTarget.value || !sampleForm.warehouse_id || Number(sampleForm.quantity) <= 0) {
    ElMessage.warning('请选择接收仓库并填写正确的样品数量')
    return
  }
  await post('/api/supplier-samples', {
    supplier_id: catalogSupplier.value.id,
    product_id: sampleTarget.value.item_type === 'product' ? sampleTarget.value.target_id : null,
    material_id: sampleTarget.value.item_type === 'material' ? sampleTarget.value.target_id : null,
    packaging_config_id: sampleTarget.value.item_type === 'packaging' ? sampleTarget.value.target_id : null,
    ...sampleForm
  })
  ElMessage.success('待收样任务已提交给目标仓库库管')
  sampleCreateVisible.value = false
  await openCatalog(catalogSupplier.value)
}

function openSampleHistory(row) {
  sampleHistoryTitle.value = `${catalogSupplier.value?.name || ''} · ${catalogItemName(row)} · 样品记录`
  sampleHistoryRows.value = samplesFor(row)
  sampleHistoryVisible.value = true
}

function openSampleReport(row) {
  sampleReport.value = row
  sampleReportVisible.value = true
}

function addCatalogRow() {
  catalogRows.value.push({ item_type: 'product', target_id: null, remark: '' })
}

function handleCatalogTypeChange(row) {
  row.target_id = null
  row.item_name = ''
  row.item_code = ''
}

function catalogOptions(row) {
  if (row.item_type === 'product') return products.value
  if (row.item_type === 'material') return materials.value
  return packagings.value
}

function catalogOptionLabel(item, itemType) {
  if (itemType === 'product') return `${item.product_code} / ${item.name}`
  if (itemType === 'material') return `${item.material_code} / ${item.name}`
  return `BZ-${item.id} / ${item.packaging_name}`
}

function removeCatalogRow(index) {
  catalogRows.value.splice(index, 1)
}

function formatNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '-'
}

function formatMoney(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { minimumFractionDigits: 3, maximumFractionDigits: 4 }) : '-'
}

function catalogSpec(row) {
  const source = catalogOptions(row).find((item) => item.id === row.target_id)
  if (row.item_type === 'packaging') return source?.specification_text || (source?.kg_per_bag ? `${source.kg_per_bag} kg/件` : '-')
  return source?.specification || '-'
}

function catalogItemName(row) {
  if (row.item_name) return row.item_name
  const source = catalogOptions(row).find((item) => item.id === row.target_id)
  return (row.item_type === 'packaging' ? source?.packaging_name : source?.name) || '未选择条目'
}

async function openPriceHistory(row) {
  const targetId = row.target_id || row.product_id || row.material_id || row.packaging_config_id
  if (!catalogSupplier.value) {
    ElMessage.warning('请先打开供应商名录')
    return
  }
  if (!targetId) {
    ElMessage.warning('请先选择产品、原料或包装')
    return
  }
  try {
    const query = new URLSearchParams({
      item_type: row.item_type || (row.product_id ? 'product' : 'material'),
      item_id: String(targetId)
    })
    const result = await get(`/api/master-data/suppliers/${catalogSupplier.value.id}/catalog-price-history?${query.toString()}`, { force: true })
    priceHistoryRows.value = result.history || []
    priceHistorySummary.value = {
      latest_price: priceHistoryRows.value[0]?.unit_price || 0,
      latest_order_no: priceHistoryRows.value[0]?.purchase_order_no || ''
    }
    priceHistoryTitle.value = `${catalogSupplier.value.name} - ${catalogItemName(row)} 历史供价`
    priceHistoryVisible.value = true
    if (!priceHistoryRows.value.length) {
      ElMessage.info('该名录条目暂无历史采购价格记录')
    }
  } catch (error) {
    priceHistoryRows.value = []
    priceHistorySummary.value = null
    priceHistoryTitle.value = `${catalogSupplier.value.name} - ${catalogItemName(row)} 历史供价`
    priceHistoryVisible.value = true
    ElMessage.error(error?.message || '历史供价加载失败')
  }
}

async function submit() {
  if (!form.name.trim() || !form.contact_name.trim() || !form.phone.trim()) {
    ElMessage.warning('请填写供应商名称、联系人和联系电话')
    return
  }
  const payload = buildPayload(form)
  if (editingId.value) {
    await put(`/api/master-data/suppliers/${editingId.value}`, payload)
    ElMessage.success('供应商已更新')
  } else {
    await post('/api/master-data/suppliers', payload)
    ElMessage.success('供应商已新增')
  }
  dialogVisible.value = false
  resetForm()
  await Promise.all([load(), loadTagCatalog()])
}

async function saveCatalog() {
  if (!catalogSupplier.value) return
  const payload = catalogRows.value.map((item) => ({
    product_id: item.item_type === 'product' ? item.target_id : null,
    material_id: item.item_type === 'material' ? item.target_id : null,
    packaging_config_id: item.item_type === 'packaging' ? item.target_id : null,
    remark: item.remark || null
  }))
  await put(`/api/master-data/suppliers/${catalogSupplier.value.id}/catalog`, payload)
  ElMessage.success('供应商商品名录已更新')
  await openCatalog(catalogSupplier.value)
  await load()
  matrixLoaded.value = false
}

async function toggleStatus(row) {
  await put(`/api/master-data/suppliers/${row.id}`, {
    ...buildPayload(row),
    status: row.status === 'enabled' ? 'disabled' : 'enabled'
  })
  ElMessage.success(`供应商已${row.status === 'enabled' ? '停用' : '启用'}`)
  await load()
}

async function removeSupplier(row) {
  await ElMessageBox.confirm(`确认删除供应商“${row.name}”吗？若存在业务引用将自动改为停用。`, '删除确认', { type: 'warning' })
  const result = await remove(`/api/master-data/suppliers/${row.id}`)
  ElMessage.success(result?.disabled ? '供应商已停用' : '供应商已删除')
  await load()
}

function exportTable() {
  exportRowsToCsv('suppliers', filteredRows.value.map((item) => ({
    供应商编码: item.code,
    供应商名称: item.name,
    供应商类型: item.supplier_type_label || '',
    状态: item.status === 'enabled' ? '启用' : '停用',
    统一社会信用代码: item.unified_social_credit_code || '',
    联系人: item.contact_name,
    联系电话: item.phone,
    供应商地址: item.address || '',
    官网: item.website || '',
    开户行名称: item.bank_name || '',
    银行账号: item.bank_account || '',
    标签: (item.tags || []).join('、'),
    采购单数: item.purchase_order_count,
    商品名录数: item.catalog_item_count,
    备注: item.remark || ''
  })))
}

onMounted(async () => {
  await Promise.all([load(), loadTagCatalog()])
})
</script>

<style scoped>
.page-stack { display: flex; flex-direction: column; gap: 16px; }
.view-switcher { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 18px; border: 1px solid #d9e6f2; border-radius: 10px; background: linear-gradient(135deg, #f4f9fd 0%, #fff 70%); }
.view-switcher > div:first-child { display: flex; flex-direction: column; gap: 4px; }
.view-switcher strong { color: #163a59; font-size: 17px; }
.view-switcher span { color: #708399; font-size: 13px; }
.view-switcher-actions { display: flex; flex-shrink: 0; }
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.summary-success { color: #23845b; }
.summary-warning { color: #c57a12; }
.matrix-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 18px; padding: 4px 2px 14px; color: #526579; font-size: 13px; }
.matrix-legend > span { display: inline-flex; align-items: center; gap: 6px; }
.legend-dot { width: 11px; height: 11px; border-radius: 3px; border: 1px solid rgba(0, 0, 0, .08); }
.matrix-hint { margin-left: auto; color: #8a99a8; }
.supplier-matrix :deep(.el-table__header th) { background: #f5f8fb; }
.supplier-header { width: 100%; padding: 4px 2px; border: 0; background: transparent; color: #185d8f; text-align: left; cursor: pointer; }
.supplier-header span, .supplier-header small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.supplier-header span { font-weight: 700; }
.supplier-header small { margin-top: 3px; color: #91a0ae; font-weight: 400; }
.matrix-cell { min-height: 62px; margin: -8px -12px; padding: 9px 12px; border-left: 4px solid transparent; cursor: pointer; transition: filter .15s ease, transform .15s ease; }
.matrix-cell:hover { filter: brightness(.97); transform: translateY(-1px); }
.matrix-cell-title { font-weight: 700; line-height: 1.4; }
.matrix-cell-price { margin-top: 4px; color: #53677a; font-size: 12px; }
.report-link { margin-top: 4px; padding: 0; border: 0; background: transparent; color: #176ea5; font-size: 12px; cursor: pointer; }
.state-unsampled { background: #edf6ff; border-color: #409eff; color: #245f8d; }
.state-pending { background: #fff6e4; border-color: #e6a23c; color: #9a650d; }
.state-qualified { background: #edf9f2; border-color: #38a169; color: #23734c; }
.state-unqualified { background: #fff0f0; border-color: #e35d6a; color: #a23741; }
.matrix-empty { color: #c7d0d9; text-align: center; }
.matrix-footer { padding-top: 12px; color: #8a99a8; font-size: 13px; text-align: right; }
.relation-detail { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.relation-detail > div { display: flex; flex-direction: column; gap: 5px; padding: 12px; border-radius: 7px; background: #f7f9fb; }
.relation-detail span { color: #8493a2; font-size: 12px; }
.relation-detail strong { color: #263b4f; line-height: 1.6; }
.relation-detail-full { grid-column: 1 / -1; }
.drawer-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; margin-bottom: 16px; color: #606266; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.price-summary { display: flex; flex-wrap: wrap; gap: 12px 24px; margin-bottom: 12px; color: #303133; font-weight: 600; }
.drawer-meta-full { grid-column: 1 / -1; }
.tag-chip { margin-right: 6px; margin-bottom: 4px; }
.account-hint { color: #909399; font-size: 13px; }
.sample-meta { color: #909399; font-size: 12px; margin-left: 6px; }
.w-260 { width: 260px; }
.w-200 { width: 200px; }
.w-160 { width: 160px; }
.w-140 { width: 140px; }
.mt-16 { margin-top: 16px; }
@media (max-width: 900px) {
  .view-switcher { align-items: flex-start; flex-direction: column; }
  .matrix-hint { width: 100%; margin-left: 0; }
}
</style>
