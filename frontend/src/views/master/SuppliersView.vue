<template>
  <div class="page-stack">
    <el-row :gutter="12" class="summary-row">
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">供应商总数</div><div class="summary-value">{{ filteredRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">启用中</div><div class="summary-value">{{ enabledCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">已关联采购单</div><div class="summary-value">{{ linkedCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="summary-label">可供货名录数</div><div class="summary-value">{{ catalogCount }}</div></el-card></el-col>
    </el-row>

    <PageSection title="供应商管理" description="维护供应商基础信息，并管理该供应商可提供的产品或原料名录。">
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
        <span>商品名录</span>
        <div>
          <el-button v-if="canManageCatalog" size="small" @click="addCatalogRow">新增条目</el-button>
          <el-button v-if="canManageCatalog" size="small" type="primary" @click="saveCatalog">保存名录</el-button>
        </div>
      </div>

      <el-table :data="catalogRows" border>
        <el-table-column label="品类" width="120">
          <template #default="scope">
            <el-select v-model="scope.row.item_type" :disabled="!canManageCatalog" style="width: 100%">
              <el-option label="产品" value="product" />
              <el-option label="原料" value="material" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="物料/产品" min-width="220">
          <template #default="scope">
            <el-select v-model="scope.row.target_id" filterable :disabled="!canManageCatalog" style="width: 100%">
              <el-option
                v-for="item in scope.row.item_type === 'product' ? products : materials"
                :key="`${scope.row.item_type}-${item.id}`"
                :label="scope.row.item_type === 'product' ? `${item.product_code} / ${item.name}` : `${item.material_code} / ${item.name}`"
                :value="item.id"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="规格" min-width="120">
          <template #default="scope">{{ catalogSpec(scope.row) }}</template>
        </el-table-column>
        <el-table-column label="备注" min-width="180">
          <template #default="scope"><el-input v-model="scope.row.remark" :disabled="!canManageCatalog" /></template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openPriceHistory(scope.row)">价格详情</el-button>
            <el-button v-if="canManageCatalog" size="small" text type="danger" @click="removeCatalogRow(scope.$index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!catalogRows.length" description="暂无可供货商品名录" class="mt-16" />
    </el-drawer>

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
const priceHistoryVisible = ref(false)
const priceHistoryRows = ref([])
const priceHistoryTitle = ref('历史供价详情')
const priceHistorySummary = ref(null)

const canManageAccounts = computed(() => ['purchase', 'master_data_admin', 'finance', 'finance_super', 'system_admin'].includes(auth.user?.role_code))
const canManage = computed(() => ['purchase', 'master_data_admin'].includes(auth.user?.role_code) && pagePermission.can('create'))
const canManageCatalog = computed(() => ['purchase', 'master_data_admin'].includes(auth.user?.role_code))

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
    item_type: item.item_type || (item.product_id ? 'product' : 'material'),
    target_id: item.product_id || item.material_id || item.item_id || item.target_id,
    product_id: item.product_id,
    material_id: item.material_id,
    item_name: item.item_name || item.product_name || item.material_name,
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
  const detail = await get(`/api/master-data/suppliers/${row.id}/catalog`)
  catalogSupplier.value = detail.supplier
  catalogRows.value = normalizeCatalogRows(detail.items)
  catalogVisible.value = true
}

function addCatalogRow() {
  catalogRows.value.push({ item_type: 'product', target_id: null, remark: '' })
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
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '-'
}

function catalogSpec(row) {
  const source = row.item_type === 'product'
    ? products.value.find((item) => item.id === row.target_id)
    : materials.value.find((item) => item.id === row.target_id)
  return source?.specification || '-'
}

function catalogItemName(row) {
  if (row.item_name) return row.item_name
  const source = row.item_type === 'product'
    ? products.value.find((item) => item.id === row.target_id)
    : materials.value.find((item) => item.id === row.target_id)
  return source?.name || '未选择条目'
}

async function openPriceHistory(row) {
  const targetId = row.target_id || row.product_id || row.material_id
  if (!catalogSupplier.value) {
    ElMessage.warning('请先打开供应商名录')
    return
  }
  if (!targetId) {
    ElMessage.warning('请先选择产品或原料')
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
    remark: item.remark || null
  }))
  await put(`/api/master-data/suppliers/${catalogSupplier.value.id}/catalog`, payload)
  ElMessage.success('供应商商品名录已更新')
  await openCatalog(catalogSupplier.value)
  await load()
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
.summary-row { margin-bottom: 0; }
.summary-label { color: #909399; margin-bottom: 8px; }
.summary-value { font-size: 28px; font-weight: 700; color: #0f5c90; }
.drawer-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; margin-bottom: 16px; color: #606266; }
.detail-toolbar { display: flex; justify-content: space-between; align-items: center; margin: 8px 0 12px; font-weight: 600; }
.price-summary { display: flex; flex-wrap: wrap; gap: 12px 24px; margin-bottom: 12px; color: #303133; font-weight: 600; }
.drawer-meta-full { grid-column: 1 / -1; }
.tag-chip { margin-right: 6px; margin-bottom: 4px; }
.account-hint { color: #909399; font-size: 13px; }
.w-260 { width: 260px; }
.w-200 { width: 200px; }
.w-160 { width: 160px; }
.w-140 { width: 140px; }
.mt-16 { margin-top: 16px; }
</style>
