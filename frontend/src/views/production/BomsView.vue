<template>
  <PageSection title="BOM 管理" description="维护成品配方、包装和标准耗用量。">
    <template #actions>
      <button class="plain-btn" type="button" :disabled="loading" @click="refreshAll">
        {{ loading ? '刷新中...' : '刷新' }}
      </button>
      <button v-if="canManage" class="primary-btn" type="button" @click="openCreate">新增 BOM</button>
    </template>

    <div v-if="feedback" :class="['feedback', feedbackType]">{{ feedback }}</div>

    <div class="table-wrap">
      <table class="bom-table">
        <thead>
          <tr>
            <th>成品</th>
            <th>包装</th>
            <th>kg/袋</th>
            <th>包装单价</th>
            <th>版本</th>
            <th>组成原料</th>
            <th v-if="canManage" class="operation-col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td :colspan="canManage ? 7 : 6" class="empty-cell">正在加载 BOM 数据...</td>
          </tr>
          <tr v-else-if="!pagedRows.length">
            <td :colspan="canManage ? 7 : 6" class="empty-cell">暂无 BOM 数据，请点击刷新重试。</td>
          </tr>
          <tr v-for="row in pagedRows" v-else :key="row.id">
            <td>{{ row.product_name || '-' }}</td>
            <td>{{ row.packaging_name || '-' }}</td>
            <td>{{ formatNumber(row.kg_per_bag) }}</td>
            <td>{{ formatMoney(row.packaging_unit_price) }}</td>
            <td>{{ row.version_no || '-' }}</td>
            <td>{{ formatBomItems(row) }}</td>
            <td v-if="canManage" class="operation-cell">
              <button class="link-btn" type="button" @click="editBom(row)">编辑</button>
              <button class="danger-link" type="button" @click="deleteBom(row)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pager">
      <span>共 {{ rows.length }} 条</span>
      <select v-model.number="pagination.pageSize" class="page-select">
        <option v-for="size in pageSizes" :key="size" :value="size">{{ size }} 条/页</option>
      </select>
      <button class="plain-btn small" type="button" :disabled="pagination.page <= 1" @click="pagination.page -= 1">上一页</button>
      <span>第 {{ pagination.page }} / {{ maxPage }} 页</span>
      <button class="plain-btn small" type="button" :disabled="pagination.page >= maxPage" @click="pagination.page += 1">下一页</button>
    </div>

    <div v-if="dialogVisible" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <div class="modal-header">
          <h3>{{ editingId ? '编辑 BOM' : '新增 BOM' }}</h3>
          <button class="close-btn" type="button" @click="closeDialog">×</button>
        </div>

        <div class="form-grid">
          <label>
            <span>成品</span>
            <el-select v-model="form.product_id" filterable clearable placeholder="输入产品编号、名称或规格搜索" :disabled="Boolean(editingId)" style="width: 100%">
              <el-option v-for="item in products" :key="item.id" :label="[item.product_code, item.name, item.specification || '无规格'].join(' - ')" :value="item.id" />
            </el-select>
          </label>
          <label>
            <span>包装</span>
            <select v-model.number="form.packaging_config_id">
              <option value="">请选择包装</option>
              <option v-for="item in packagingOptions" :key="item.id" :value="item.id">
                {{ item.packaging_name }} / {{ formatNumber(item.kg_per_bag) }}kg / {{ formatMoney(item.unit_price) }}
              </option>
            </select>
          </label>
          <label>
            <span>版本</span>
            <input v-model.trim="form.version_no" type="text" placeholder="如 v1" />
          </label>
          <label class="full-line">
            <span>备注</span>
            <input v-model.trim="form.notes" type="text" placeholder="可填写工艺或配方说明" />
          </label>
        </div>

        <div class="detail-title">
          <strong>原料明细</strong>
          <button class="plain-btn small" type="button" @click="addItem">新增原料</button>
        </div>

        <table class="bom-table compact">
          <thead>
            <tr>
              <th>原料</th>
              <th>标准耗用量</th>
              <th class="operation-col">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in form.items" :key="index">
              <td>
                <el-select v-model="item.material_id" filterable clearable placeholder="输入原料编号、名称或规格搜索" style="width: 100%">
                  <el-option v-for="material in materials" :key="material.id" :label="[material.material_code, material.name, material.specification || '无规格'].join(' - ')" :value="material.id" />
                </el-select>
              </td>
              <td>
                <input v-model.number="item.standard_quantity" type="number" min="0.01" step="0.01" />
              </td>
              <td class="operation-cell">
                <button class="danger-link" type="button" @click="removeItem(index)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="modal-footer">
          <button class="plain-btn" type="button" @click="closeDialog">取消</button>
          <button class="primary-btn" type="button" :disabled="saving" @click="submit">
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </PageSection>
</template>

<script setup>
import 'element-plus/es/components/select/style/css'

import { computed, onMounted, reactive, ref, watch } from 'vue'

import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'

import { get, post, put, remove } from '../../api/client'
import PageSection from '../../components/PageSection.vue'
import { usePagePermission } from '../../composables/usePagePermission'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const pagePermission = usePagePermission()
const bomManageRoles = new Set(['production', 'system_admin', 'master_data_admin'])
const canManage = computed(() => bomManageRoles.has(auth.user?.role_code) && pagePermission.can('create'))

const rows = ref([])
const products = ref([])
const packagingOptions = ref([])
const materials = ref([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref(null)
const feedback = ref('')
const feedbackType = ref('info')
const pageSizes = [10, 20, 50, 100]
const pagination = reactive({
  page: 1,
  pageSize: 10
})

const maxPage = computed(() => Math.max(1, Math.ceil(rows.value.length / pagination.pageSize)))
const pagedRows = computed(() => {
  const start = (pagination.page - 1) * pagination.pageSize
  return rows.value.slice(start, start + pagination.pageSize)
})

const emptyForm = () => ({
  product_id: '',
  packaging_config_id: '',
  version_no: 'v1',
  notes: '',
  items: [{ material_id: '', standard_quantity: 1 }]
})

const form = reactive(emptyForm())

watch(
  () => rows.value.length,
  () => {
    pagination.page = 1
  }
)

watch(
  () => pagination.pageSize,
  () => {
    pagination.page = 1
  }
)

watch(maxPage, (value) => {
  if (pagination.page > value) {
    pagination.page = value
  }
})

function setFeedback(message, type = 'info') {
  feedback.value = message
  feedbackType.value = type
}

function clearFeedback() {
  feedback.value = ''
  feedbackType.value = 'info'
}

function formatNumber(value) {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return '-'
  return numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function formatMoney(value) {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) return '-'
  return `${numberValue.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`
}

function formatBomItems(row) {
  const items = Array.isArray(row?.items) ? row.items : []
  if (!items.length) return '-'
  return items.map((item) => `${item.material_name || '-'}(${formatNumber(item.standard_quantity)})`).join('、')
}

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
}

async function loadBaseData() {
  const [productRows, packagingRows, materialRows] = await Promise.all([
    get('/api/master-data/products?active_only=true', { force: true }),
    get('/api/master-data/packaging-configs?entity_type=global&active_only=true', { force: true }),
    get('/api/master-data/materials?active_only=true', { force: true })
  ])
  products.value = (Array.isArray(productRows) ? productRows : []).filter((item) => item.fulfillment_mode === 'production')
  packagingOptions.value = Array.isArray(packagingRows) ? packagingRows : []
  materials.value = Array.isArray(materialRows) ? materialRows : []
}

async function loadBoms() {
  const bomRows = await get('/api/production/boms', { force: true })
  rows.value = Array.isArray(bomRows) ? bomRows : []
}

async function refreshAll() {
  loading.value = true
  clearFeedback()
  try {
    await loadBaseData()
    await loadBoms()
    if (!rows.value.length) {
      setFeedback('当前没有 BOM 数据。', 'info')
    }
  } catch (error) {
    rows.value = []
    setFeedback(error.message || 'BOM 数据加载失败，请确认后端服务是否正常。', 'error')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  if (!canManage.value) {
    setFeedback('当前账号没有维护 BOM 的权限。', 'warning')
    return
  }
  if (!products.value.length || !packagingOptions.value.length || !materials.value.length) {
    setFeedback('产品、包装或原料基础数据未加载完成，请先点击刷新。', 'warning')
    return
  }
  resetForm()
  form.packaging_config_id = packagingOptions.value.find((item) => item.is_default)?.id || packagingOptions.value[0]?.id || ''
  dialogVisible.value = true
}

function editBom(row) {
  if (!canManage.value) return
  resetForm()
  editingId.value = row.id
  form.product_id = row.product_id || ''
  form.packaging_config_id = row.packaging_config_id || ''
  form.version_no = row.version_no || 'v1'
  form.notes = row.notes || ''
  form.items = (Array.isArray(row.items) ? row.items : []).map((item) => ({
    material_id: item.material_id || '',
    standard_quantity: Number(item.standard_quantity || 0)
  }))
  if (!form.items.length) {
    form.items = [{ material_id: '', standard_quantity: 1 }]
  }
  dialogVisible.value = true
}

function addItem() {
  form.items.push({ material_id: '', standard_quantity: 1 })
}

function removeItem(index) {
  form.items.splice(index, 1)
  if (!form.items.length) {
    addItem()
  }
}

function closeDialog() {
  dialogVisible.value = false
  saving.value = false
}

function validateForm() {
  if (!Number(form.product_id)) return '请选择成品'
  if (!Number(form.packaging_config_id)) return '请选择包装'
  const invalidItem = form.items.find((item) => !Number(item.material_id) || Number(item.standard_quantity || 0) <= 0)
  if (invalidItem) return '请完整填写原料和标准耗用量'
  return ''
}

async function submit() {
  const invalidMessage = validateForm()
  if (invalidMessage) {
    setFeedback(invalidMessage, 'warning')
    return
  }

  saving.value = true
  clearFeedback()
  const payload = {
    product_id: Number(form.product_id),
    packaging_config_id: Number(form.packaging_config_id),
    version_no: form.version_no || 'v1',
    notes: form.notes || '',
    items: form.items.map((item) => ({
      material_id: Number(item.material_id),
      standard_quantity: Number(item.standard_quantity)
    }))
  }

  try {
    if (editingId.value) {
      await put(`/api/production/boms/${editingId.value}`, payload)
      setFeedback('BOM 已更新。', 'success')
    } else {
      await post('/api/production/boms', payload)
      setFeedback('BOM 已保存。', 'success')
    }
    closeDialog()
    resetForm()
    await loadBoms()
  } catch (error) {
    setFeedback(error.message || 'BOM 保存失败。', 'error')
  } finally {
    saving.value = false
  }
}

async function deleteBom(row) {
  if (!canManage.value) return
  if (!window.confirm(`确认删除 ${row.product_name || ''} 的 BOM 吗？`)) return
  clearFeedback()
  try {
    await remove(`/api/production/boms/${row.id}`)
    setFeedback('BOM 已删除。', 'success')
    await loadBoms()
  } catch (error) {
    setFeedback(error.message || 'BOM 删除失败。', 'error')
  }
}

onMounted(refreshAll)
</script>

<style scoped>
.feedback {
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 14px;
}

.feedback.info {
  color: #1f4e79;
  background: #eaf5ff;
}

.feedback.success {
  color: #246b36;
  background: #ecf8ef;
}

.feedback.warning {
  color: #8a5a00;
  background: #fff7df;
}

.feedback.error {
  color: #9f2a2a;
  background: #fff0f0;
}

.table-wrap {
  overflow-x: auto;
}

.bom-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  color: #1f2f3d;
  background: #fff;
}

.bom-table th,
.bom-table td {
  padding: 11px 12px;
  border-bottom: 1px solid #e6eef5;
  text-align: left;
  vertical-align: top;
}

.bom-table th {
  background: #f6f9fc;
  color: #536575;
  font-weight: 700;
  white-space: nowrap;
}

.bom-table tr:hover td {
  background: #f9fcff;
}

.bom-table.compact {
  margin-top: 8px;
}

.bom-table select,
.bom-table input,
.form-grid select,
.form-grid input {
  width: 100%;
  box-sizing: border-box;
  height: 34px;
  border: 1px solid #cfd9e3;
  border-radius: 6px;
  padding: 0 8px;
  background: #fff;
  color: #1f2f3d;
}

.operation-col {
  width: 120px;
}

.operation-cell {
  white-space: nowrap;
}

.empty-cell {
  text-align: center !important;
  color: #8091a2;
  padding: 28px 12px !important;
}

.pager {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
  padding-top: 14px;
  color: #536575;
  font-size: 14px;
}

.page-select {
  height: 32px;
  border: 1px solid #cfd9e3;
  border-radius: 6px;
  background: #fff;
}

.primary-btn,
.plain-btn,
.link-btn,
.danger-link,
.close-btn {
  border: none;
  border-radius: 7px;
  cursor: pointer;
  font-size: 14px;
}

.primary-btn {
  padding: 8px 14px;
  color: #fff;
  background: #1f75cb;
}

.primary-btn:disabled,
.plain-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.plain-btn {
  padding: 7px 13px;
  color: #1f4e79;
  background: #eef6ff;
  border: 1px solid #cfe1f3;
}

.plain-btn.small {
  padding: 5px 10px;
}

.link-btn,
.danger-link {
  padding: 3px 6px;
  background: transparent;
}

.link-btn {
  color: #1f75cb;
}

.danger-link {
  color: #cf3b3b;
}

.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 32, 50, 0.42);
}

.modal {
  width: min(920px, calc(100vw - 48px));
  max-height: calc(100vh - 72px);
  overflow: auto;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(20, 43, 66, 0.24);
  padding: 18px;
}

.modal-header,
.modal-footer,
.detail-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.modal-header {
  margin-bottom: 14px;
}

.modal-header h3 {
  margin: 0;
  font-size: 18px;
}

.close-btn {
  width: 32px;
  height: 32px;
  background: #f2f6fa;
  color: #536575;
  font-size: 20px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 16px;
}

.form-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #536575;
  font-size: 14px;
}

.form-grid .full-line {
  grid-column: 1 / -1;
}

.detail-title {
  margin-top: 18px;
  margin-bottom: 8px;
}

.modal-footer {
  justify-content: flex-end;
  margin-top: 18px;
}

@media (max-width: 760px) {
  .form-grid {
    grid-template-columns: 1fr;
  }

  .pager {
    flex-wrap: wrap;
  }
}
</style>
