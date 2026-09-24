<template>
  <div class="transfer-page">
    <PageSection title="库存转移" description="仓库间货物转移：源仓库出库 → 目标仓库质检 → 入库；质检不通过退回源仓库。出库与入库均可上传现场照片。">
      <template #actions>
        <el-select v-model="filters.status" clearable placeholder="状态" class="w-150">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button v-if="canCreate" type="primary" @click="openCreate">发起移库</el-button>
        <el-button @click="load">刷新</el-button>
      </template>

      <el-table :data="filteredRows" stripe border v-loading="loading">
        <el-table-column prop="transfer_no" label="移库单号" width="150" />
        <el-table-column label="移库路径" min-width="220">
          <template #default="scope">{{ scope.row.source_warehouse_name }} → {{ scope.row.target_warehouse_name }}</template>
        </el-table-column>
        <el-table-column prop="item_name" label="货品" min-width="150" />
        <el-table-column label="数量" width="100">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="created_by_name" label="发起人" width="110" />
        <el-table-column label="状态" width="120">
          <template #default="scope">
            <el-tag :type="statusTag(scope.row.status)" size="small">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="scope">
            <el-button size="small" text type="primary" @click="openDetail(scope.row)">详情</el-button>
            <el-button
              v-if="canOperate && scope.row.status === 'pending_outbound'"
              size="small"
              type="primary"
              @click="openAction(scope.row, 'outbound')"
            >出库</el-button>
            <el-button
              v-if="canQuality && scope.row.status === 'pending_quality'"
              size="small"
              type="success"
              @click="openQuality(scope.row)"
            >质检</el-button>
            <el-button
              v-if="canOperate && scope.row.status === 'pending_inbound'"
              size="small"
              type="warning"
              @click="openAction(scope.row, 'inbound')"
            >入库</el-button>
            <el-tooltip v-if="scope.row.status === 'returned'" :content="scope.row.reject_reason || '未填原因'">
              <el-tag type="danger" size="small">已退回</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !filteredRows.length" description="暂无移库单" />
    </PageSection>

    <!-- 发起移库 -->
    <el-dialog v-model="createVisible" title="发起移库" width="560px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="源仓库" required>
          <el-select v-model="createForm.source_warehouse_id" style="width:100%" @change="resetItems">
            <el-option v-for="w in warehouses" :key="w.id" :label="warehouseLabel(w)" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标仓库" required>
          <el-select v-model="createForm.target_warehouse_id" style="width:100%">
            <el-option
              v-for="w in warehouses"
              :key="w.id"
              :label="warehouseLabel(w)"
              :value="w.id"
              :disabled="w.id === createForm.source_warehouse_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="货品类型">
          <el-radio-group v-model="itemType" @change="resetItems">
            <el-radio value="product">商品</el-radio>
            <el-radio value="material">原料</el-radio>
            <el-radio value="packaging">包装</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="itemType === 'product'" label="商品" required>
          <el-select v-model="createForm.product_id" filterable style="width:100%" placeholder="按名称或编码搜索">
            <el-option v-for="p in products" :key="p.id" :label="`${p.product_code} / ${p.name}`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else-if="itemType === 'material'" label="原料" required>
          <el-select v-model="createForm.material_id" filterable style="width:100%" placeholder="按名称或编码搜索">
            <el-option v-for="m in materials" :key="m.id" :label="`${m.material_code} / ${m.name}`" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="包装" required>
          <el-select v-model="createForm.packaging_config_id" filterable style="width:100%" placeholder="按包装名称搜索">
            <el-option v-for="p in packagingConfigs" :key="p.id" :label="p.packaging_name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="移库数量" required>
          <el-input-number v-model="createForm.quantity" :min="0.01" :precision="2" :step="1" style="width:100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createForm.remark" type="textarea" :rows="2" placeholder="移库原因、注意事项等" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>

    <!-- 出库 / 入库：确认前可上传现场照片 -->
    <el-dialog v-model="actionVisible" :title="actionTitle" width="640px">
      <el-alert v-if="activeTransfer" type="info" :closable="false" show-icon class="mb-12">
        <template #title>
          {{ activeTransfer.transfer_no }}：{{ activeTransfer.source_warehouse_name }} → {{ activeTransfer.target_warehouse_name }}，
          {{ activeTransfer.item_name }} × {{ formatNumber(activeTransfer.quantity) }}
        </template>
      </el-alert>
      <PhotoAttachments
        v-if="activeTransfer"
        :key="`${actionMode}-${activeTransfer.id}`"
        business-type="stock_transfer"
        :business-id="activeTransfer.id"
        :attachment-type="actionMode === 'outbound' ? '出库照片' : '入库照片'"
        :title="actionMode === 'outbound' ? '出库照片' : '入库照片'"
      />
      <template #footer>
        <el-button @click="actionVisible = false">取消</el-button>
        <el-button type="primary" :loading="actionSubmitting" @click="submitAction">
          确认{{ actionMode === 'outbound' ? '出库' : '入库' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 质检 -->
    <el-dialog v-model="qualityVisible" title="移库质检" width="640px">
      <el-alert v-if="activeTransfer" type="info" :closable="false" show-icon class="mb-12">
        <template #title>
          {{ activeTransfer.transfer_no }}：{{ activeTransfer.source_warehouse_name }} → {{ activeTransfer.target_warehouse_name }}，
          {{ activeTransfer.item_name }} × {{ formatNumber(activeTransfer.quantity) }}
        </template>
      </el-alert>
      <el-form :model="qualityForm" label-width="90px">
        <el-form-item label="质检结果">
          <el-radio-group v-model="qualityForm.result">
            <el-radio value="passed">通过</el-radio>
            <el-radio value="failed">不通过</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="qualityForm.result === 'failed'" label="退回原因" required>
          <el-input v-model="qualityForm.reject_reason" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <PhotoAttachments
        v-if="activeTransfer"
        :key="`quality-${activeTransfer.id}`"
        business-type="stock_transfer"
        :business-id="activeTransfer.id"
        attachment-type="质检照片"
        title="质检照片"
      />
      <template #footer>
        <el-button @click="qualityVisible = false">取消</el-button>
        <el-button type="primary" :loading="qualitySubmitting" @click="submitQuality">提交</el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-drawer v-model="detailVisible" size="52%" :title="activeTransfer ? `移库单 ${activeTransfer.transfer_no}` : '移库详情'">
      <div v-if="activeTransfer" class="detail-meta">
        <div><strong>移库路径：</strong>{{ activeTransfer.source_warehouse_name }} → {{ activeTransfer.target_warehouse_name }}</div>
        <div><strong>货品：</strong>{{ activeTransfer.item_name }}</div>
        <div><strong>数量：</strong>{{ formatNumber(activeTransfer.quantity) }}</div>
        <div><strong>状态：</strong>{{ activeTransfer.status_label }}</div>
        <div><strong>发起人：</strong>{{ activeTransfer.created_by_name || '-' }}</div>
        <div><strong>出库时间：</strong>{{ shortTime(activeTransfer.outbound_at) }}</div>
        <div><strong>质检结果：</strong>{{ activeTransfer.quality_result || '-' }}</div>
        <div><strong>完成时间：</strong>{{ shortTime(activeTransfer.completed_at) }}</div>
        <div class="detail-full"><strong>备注：</strong>{{ activeTransfer.remark || '-' }}</div>
        <div v-if="activeTransfer.reject_reason" class="detail-full"><strong>退回原因：</strong>{{ activeTransfer.reject_reason }}</div>
      </div>

      <el-divider content-position="left">现场照片</el-divider>
      <PhotoAttachments
        v-if="activeTransfer"
        :key="`detail-out-${activeTransfer.id}`"
        business-type="stock_transfer"
        :business-id="activeTransfer.id"
        attachment-type="出库照片"
        title="出库照片"
        readonly
      />
      <PhotoAttachments
        v-if="activeTransfer"
        :key="`detail-qc-${activeTransfer.id}`"
        business-type="stock_transfer"
        :business-id="activeTransfer.id"
        attachment-type="质检照片"
        title="质检照片"
        readonly
        class="mt-16"
      />
      <PhotoAttachments
        v-if="activeTransfer"
        :key="`detail-in-${activeTransfer.id}`"
        business-type="stock_transfer"
        :business-id="activeTransfer.id"
        attachment-type="入库照片"
        title="入库照片"
        readonly
        class="mt-16"
      />
    </el-drawer>
  </div>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/drawer/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/tooltip/style/css'

import { ElMessage } from 'element-plus/es/components/message/index.mjs'

import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDivider from 'element-plus/es/components/divider/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
import ElEmpty from 'element-plus/es/components/empty/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElRadio, { ElRadioGroup } from 'element-plus/es/components/radio/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'

import { computed, onMounted, reactive, ref } from 'vue'

import PageSection from '../../components/PageSection.vue'
import PhotoAttachments from '../../components/PhotoAttachments.vue'
import { get, post } from '../../api/client'
import { useAuthStore } from '../../stores/auth'
import { formatNumber } from '../../utils/format'

const auth = useAuthStore()

const rows = ref([])
const warehouses = ref([])
const products = ref([])
const materials = ref([])
const packagingConfigs = ref([])
const loading = ref(false)

const createVisible = ref(false)
const creating = ref(false)
const itemType = ref('product')
// 注意：模板里用的就是 createForm，早前版本模板写成 form 导致对话框渲染即报错、点「发起移库」没反应
const createForm = reactive({
  source_warehouse_id: null,
  target_warehouse_id: null,
  product_id: null,
  material_id: null,
  packaging_config_id: null,
  quantity: 1,
  remark: ''
})

const actionVisible = ref(false)
const actionSubmitting = ref(false)
const actionMode = ref('outbound')

const qualityVisible = ref(false)
const qualitySubmitting = ref(false)
const qualityForm = reactive({ result: 'passed', reject_reason: '' })

const detailVisible = ref(false)
const activeTransfer = ref(null)

const filters = reactive({ status: '' })

const statusOptions = [
  { value: 'pending_outbound', label: '待出库' },
  { value: 'pending_quality', label: '待质检' },
  { value: 'pending_inbound', label: '待入库' },
  { value: 'completed', label: '已完成' },
  { value: 'returned', label: '已退回' }
]

const canCreate = computed(() => auth.isRole('inventory'))
const canOperate = computed(() => auth.isRole('inventory'))
const canQuality = computed(() => auth.isRole('quality') || auth.isRole('inventory'))

const actionTitle = computed(() => (actionMode.value === 'outbound' ? '确认出库' : '确认入库'))

const filteredRows = computed(() => {
  if (!filters.status) return rows.value
  return rows.value.filter((item) => item.status === filters.status)
})

function warehouseLabel(warehouse) {
  return warehouse.warehouse_type_label ? `${warehouse.name}（${warehouse.warehouse_type_label}）` : warehouse.name
}

function shortTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 16)
}

function statusTag(status) {
  const map = {
    pending_outbound: 'warning',
    pending_quality: 'primary',
    pending_inbound: 'warning',
    completed: 'success',
    returned: 'danger'
  }
  return map[status] || 'info'
}

function resetItems() {
  createForm.product_id = null
  createForm.material_id = null
  createForm.packaging_config_id = null
}

async function load() {
  loading.value = true
  try {
    rows.value = await get('/api/inventory/transfers', { force: true })
  } catch (error) {
    ElMessage.error(error?.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  try {
    // 这三个接口都在 master-data 前缀下，早前版本写成 /api/products、/api/materials 会 404
    if (!warehouses.value.length) warehouses.value = await get('/api/master-data/warehouses')
    if (!products.value.length) products.value = await get('/api/master-data/products')
    if (!materials.value.length) materials.value = await get('/api/master-data/materials')
    if (!packagingConfigs.value.length) packagingConfigs.value = await get('/api/master-data/packaging-configs?entity_type=global&active_only=true')
  } catch (error) {
    ElMessage.error(error?.message || '加载仓库或货品失败')
  }
}

async function openCreate() {
  Object.assign(createForm, {
    source_warehouse_id: null,
    target_warehouse_id: null,
    product_id: null,
    material_id: null,
    packaging_config_id: null,
    quantity: 1,
    remark: ''
  })
  itemType.value = 'product'
  createVisible.value = true
  await loadOptions()
}

async function submitCreate() {
  if (!createForm.source_warehouse_id || !createForm.target_warehouse_id) {
    ElMessage.warning('请选择源仓库和目标仓库')
    return
  }
  if (createForm.source_warehouse_id === createForm.target_warehouse_id) {
    ElMessage.warning('源仓库和目标仓库不能相同')
    return
  }
  if (Number(createForm.quantity || 0) <= 0) {
    ElMessage.warning('移库数量必须大于 0')
    return
  }
  if (itemType.value === 'product' && !createForm.product_id) {
    ElMessage.warning('请选择商品')
    return
  }
  if (itemType.value === 'material' && !createForm.material_id) {
    ElMessage.warning('请选择原料')
    return
  }
  if (itemType.value === 'packaging' && !createForm.packaging_config_id) {
    ElMessage.warning('请选择包装')
    return
  }
  creating.value = true
  try {
    await post('/api/inventory/transfers', {
      source_warehouse_id: createForm.source_warehouse_id,
      target_warehouse_id: createForm.target_warehouse_id,
      product_id: itemType.value === 'product' ? createForm.product_id : null,
      material_id: itemType.value === 'material' ? createForm.material_id : null,
      packaging_config_id: itemType.value === 'packaging' ? createForm.packaging_config_id : null,
      quantity: Number(createForm.quantity),
      remark: createForm.remark || null
    })
    ElMessage.success('移库单已创建，请到「待出库」处理')
    createVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

function openAction(row, mode) {
  activeTransfer.value = row
  actionMode.value = mode
  actionVisible.value = true
}

async function submitAction() {
  if (!activeTransfer.value) return
  actionSubmitting.value = true
  try {
    await post(`/api/inventory/transfers/${activeTransfer.value.id}/${actionMode.value}`, {})
    ElMessage.success(actionMode.value === 'outbound' ? '已出库，等待目标仓库质检' : '已入库，移库完成')
    actionVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error?.message || '操作失败')
  } finally {
    actionSubmitting.value = false
  }
}

function openQuality(row) {
  activeTransfer.value = row
  qualityForm.result = 'passed'
  qualityForm.reject_reason = ''
  qualityVisible.value = true
}

async function submitQuality() {
  if (!activeTransfer.value) return
  if (qualityForm.result === 'failed' && !qualityForm.reject_reason.trim()) {
    ElMessage.warning('请填写退回原因')
    return
  }
  qualitySubmitting.value = true
  try {
    await post(`/api/inventory/transfers/${activeTransfer.value.id}/quality`, {
      result: qualityForm.result,
      reject_reason: qualityForm.reject_reason.trim() || null
    })
    ElMessage.success(qualityForm.result === 'passed' ? '质检通过，等待入库' : '已退回源仓库')
    qualityVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error?.message || '质检提交失败')
  } finally {
    qualitySubmitting.value = false
  }
}

function openDetail(row) {
  activeTransfer.value = row
  detailVisible.value = true
}

onMounted(load)
</script>

<style scoped>
.transfer-page { display: flex; flex-direction: column; gap: 16px; }
.mb-12 { margin-bottom: 12px; }
.mt-16 { margin-top: 16px; }
.w-150 { width: 150px; }
.detail-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
  color: #606266;
  margin-bottom: 8px;
}
.detail-full { grid-column: 1 / -1; }
</style>
