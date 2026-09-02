<template>
  <div class="transfer-page">
    <PageSection title="移库管理" description="仓库间货物转移:源仓库出库,目标仓库质检后入库;质检不通过退回源仓库。">
      <template #actions>
        <el-button v-if="canCreate" type="primary" @click="openCreate">发起移库</el-button>
        <el-button @click="load">刷新</el-button>
      </template>
      <el-table :data="rows" stripe border>
        <el-table-column prop="transfer_no" label="移库单号" width="140" />
        <el-table-column label="移库路径" min-width="220">
          <template #default="scope">{{ scope.row.source_warehouse_name }} → {{ scope.row.target_warehouse_name }}</template>
        </el-table-column>
        <el-table-column prop="item_name" label="货品" min-width="140" />
        <el-table-column prop="quantity" label="数量" width="90">
          <template #default="scope">{{ formatNumber(scope.row.quantity) }}</template>
        </el-table-column>
        <el-table-column prop="created_by_name" label="发起人" width="110" />
        <el-table-column label="状态" width="110">
          <template #default="scope">
            <el-tag :type="statusTag(scope.row.status)" size="small">{{ scope.row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280">
          <template #default="scope">
            <el-button
              v-if="canOperate(scope.row) && scope.row.status === 'pending_outbound'"
              size="small"
              type="primary"
              @click="doOutbound(scope.row)"
            >
              出库
            </el-button>
            <el-button
              v-if="canQuality && scope.row.status === 'pending_quality'"
              size="small"
              type="success"
              @click="openQuality(scope.row)"
            >
              质检
            </el-button>
            <el-button
              v-if="canOperate(scope.row) && scope.row.status === 'pending_inbound'"
              size="small"
              type="warning"
              @click="doInbound(scope.row)"
            >
              入库
            </el-button>
            <el-tag v-if="scope.row.status === 'returned'" type="danger" size="small">已退回:{{ scope.row.reject_reason || '未填原因' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </PageSection>

    <el-dialog v-model="createVisible" title="发起移库" width="520px">
      <el-form label-width="90px">
        <el-form-item label="源仓库">
          <el-select v-model="form.source_warehouse_id" @change="resetItems" style="width:100%">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标仓库">
          <el-select v-model="form.target_warehouse_id" style="width:100%">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="货品类型">
          <el-radio-group v-model="itemType">
            <el-radio label="product">商品</el-radio>
            <el-radio label="material">原料</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="itemType === 'product'" label="商品">
          <el-select v-model="form.product_id" filterable style="width:100%">
            <el-option v-for="p in products" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="原料">
          <el-select v-model="form.material_id" filterable style="width:100%">
            <el-option v-for="m in materials" :key="m.id" :label="m.name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="移库数量">
          <el-input-number v-model="form.quantity" :min="0.01" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qualityVisible" title="移库质检" width="480px">
      <el-alert v-if="activeTransfer" type="info" :closable="false" show-icon class="mb-12">
        <template #title>移库单 {{ activeTransfer.transfer_no }}:{{ activeTransfer.source_warehouse_name }} → {{ activeTransfer.target_warehouse_name }},{{ activeTransfer.item_name }} × {{ formatNumber(activeTransfer.quantity) }}</template>
      </el-alert>
      <el-form label-width="90px">
        <el-form-item label="质检结果">
          <el-radio-group v-model="qualityForm.result">
            <el-radio value="passed">通过</el-radio>
            <el-radio value="failed">不通过</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="qualityForm.result === 'failed'" label="退回原因">
          <el-input v-model="qualityForm.reject_reason" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="qualityVisible = false">取消</el-button>
        <el-button type="primary" :loading="qualitySubmitting" @click="submitQuality">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

import PageSection from '../../components/PageSection.vue'
import { ElMessage } from 'element-plus'
import { get, post } from '../../api/client'
import { useAuthStore } from '../../stores/auth'
import { formatNumber } from '../../utils/format'

const auth = useAuthStore()
const rows = ref([])
const warehouses = ref([])
const products = ref([])
const materials = ref([])
const createVisible = ref(false)
const creating = ref(false)
const qualityVisible = ref(false)
const qualitySubmitting = ref(false)
const activeTransfer = ref(null)
const itemType = ref('product')

const canCreate = computed(() => auth.isRole('inventory'))
const canQuality = computed(() => auth.isRole('quality') || auth.isRole('inventory'))

const createForm = ref({ source_warehouse_id: null, target_warehouse_id: null, product_id: null, material_id: null, quantity: 1, remark: '' })
const qualityForm = ref({ result: 'passed', reject_reason: '' })

function resetItems() {
  createForm.value.product_id = null
  createForm.value.material_id = null
}

function canOperate(row) {
  return auth.isRole('inventory')
}

function statusTag(status) {
  const map = { pending_outbound: 'warning', pending_quality: 'primary', pending_inbound: 'warning', completed: 'success', returned: 'danger' }
  return map[status] || 'info'
}

async function load() {
  try {
    rows.value = await get('/api/inventory/transfers')
  } catch (e) {
    ElMessage.error(e?.message || '加载失败')
  }
}

async function loadOptions() {
  if (!warehouses.value.length) {
    warehouses.value = await get('/api/master-data/warehouses')
  }
  if (!products.value.length) {
    products.value = await get('/api/products')
  }
  if (!materials.value.length) {
    materials.value = await get('/api/materials')
  }
}

function openCreate() {
  loadOptions()
  createForm.value = { source_warehouse_id: null, target_warehouse_id: null, product_id: null, material_id: null, quantity: 1, remark: '' }
  itemType.value = 'product'
  createVisible.value = true
}

async function submitCreate() {
  const form = createForm.value
  if (!form.source_warehouse_id || !form.target_warehouse_id || Number(form.quantity || 0) <= 0) {
    ElMessage.warning('请填写完整移库信息')
    return
  }
  if (itemType.value === 'product' && !form.product_id) {
    ElMessage.warning('请选择商品')
    return
  }
  if (itemType.value === 'material' && !form.material_id) {
    ElMessage.warning('请选择原料')
    return
  }
  creating.value = true
  try {
    const payload = {
      source_warehouse_id: form.source_warehouse_id,
      target_warehouse_id: form.target_warehouse_id,
      product_id: itemType.value === 'product' ? form.product_id : null,
      material_id: itemType.value === 'material' ? form.material_id : null,
      quantity: Number(form.quantity),
      remark: form.remark || null
    }
    const resp = await post('/api/inventory/transfers', payload)
    ElMessage.success(resp?.msg || '移库单已创建')
    createVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '创建失败')
  } finally {
    creating.value = false
  }
}

async function doOutbound(row) {
  try {
    const resp = await post(`/api/inventory/transfers/${row.id}/outbound`)
    ElMessage.success(resp?.msg || '已出库')
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '出库失败')
  }
}

function openQuality(row) {
  activeTransfer.value = row
  qualityForm.value = { result: 'passed', reject_reason: '' }
  qualityVisible.value = true
}

async function submitQuality() {
  if (qualityForm.value.result === 'failed' && !qualityForm.value.reject_reason?.trim()) {
    ElMessage.warning('请填写退回原因')
    return
  }
  qualitySubmitting.value = true
  try {
    const resp = await post(`/api/inventory/transfers/${activeTransfer.value.id}/quality`, {
      result: qualityForm.value.result,
      reject_reason: qualityForm.value.reject_reason || null
    })
    ElMessage.success(resp?.msg || '质检已提交')
    qualityVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '质检提交失败')
  } finally {
    qualitySubmitting.value = false
  }
}

async function doInbound(row) {
  try {
    const resp = await post(`/api/inventory/transfers/${row.id}/inbound`)
    ElMessage.success(resp?.msg || '已入库')
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '入库失败')
  }
}

onMounted(load)
</script>