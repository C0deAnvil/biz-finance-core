<template>
  <PageSection :title="purchaseMode ? '生产/质检自主采购需求' : '自主采购需求'" description="无需关联客户订单；原料、产品及其他消耗物品都可提交。">
    <template #actions>
      <el-button @click="load">刷新</el-button>
      <el-button v-if="!purchaseMode" type="primary" @click="openCreate">提交采购需求</el-button>
    </template>
    <el-table :data="visibleRows" stripe>
      <el-table-column prop="request_no" label="需求号" width="160" />
      <el-table-column label="部门" width="90"><template #default="scope">{{ scope.row.department === 'quality' ? '质检' : '生产' }}</template></el-table-column>
      <el-table-column prop="submitted_by_name" label="提交人" width="110" />
      <el-table-column prop="item_name" label="物品" min-width="150" />
      <el-table-column prop="specification" label="规格" min-width="100" />
      <el-table-column label="需求数量" width="120"><template #default="scope">{{ formatNumber(scope.row.quantity) }} {{ scope.row.unit }}</template></el-table-column>
      <el-table-column prop="reason" label="用途/原因" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="150"><template #default="scope">{{ scope.row.status === 'pending' ? '待采购处理' : scope.row.status === 'ordered' ? '已关联采购单' : '已关闭（无采购单）' }}</template></el-table-column>
      <el-table-column prop="purchase_order_no" label="采购订单" width="160" />
      <el-table-column prop="process_note" label="处理说明" min-width="150" show-overflow-tooltip />
      <el-table-column v-if="purchaseMode" label="操作" width="100" fixed="right"><template #default="scope"><el-button v-if="scope.row.status === 'pending'" text type="primary" @click="openProcess(scope.row)">处理</el-button></template></el-table-column>
    </el-table>
  </PageSection>

  <el-dialog v-model="createVisible" title="提交自主采购需求" width="620px">
    <el-alert v-if="masterDataError" :title="`物品数据加载失败：${masterDataError}`" type="error" :closable="false" class="mb-12" />
    <el-button v-if="masterDataError" size="small" :loading="masterDataLoading" @click="loadMasterData">重试加载原料和产品</el-button>
    <el-form :model="form" label-width="95px">
      <el-form-item label="物品类型"><el-select v-model="form.item_type" style="width:100%" @change="resetItem"><el-option label="原料" value="material" /><el-option label="产品" value="product" /><el-option label="其他物品" value="other" /></el-select></el-form-item>
      <el-form-item v-if="form.item_type === 'material'" label="原料"><el-select v-model="form.material_id" filterable :loading="masterDataLoading" style="width:100%" placeholder="搜索原料名称或编号" @change="syncUnit"><el-option v-for="row in materials" :key="row.id" :value="row.id" :label="`${row.material_code} - ${row.name}`" /></el-select></el-form-item>
      <el-form-item v-else-if="form.item_type === 'product'" label="产品"><el-select v-model="form.product_id" filterable :loading="masterDataLoading" style="width:100%" placeholder="搜索产品名称或编号" @change="syncUnit"><el-option v-for="row in products" :key="row.id" :value="row.id" :label="`${row.product_code} - ${row.name}`" /></el-select></el-form-item>
      <el-form-item v-else label="物品名称"><el-input v-model="form.item_name" placeholder="例如：质检手套、试剂耗材" /></el-form-item>
      <el-form-item label="规格"><el-input v-model="form.specification" /></el-form-item>
      <el-form-item label="数量"><el-input-number v-model="form.quantity" :min="0.0001" :precision="4" style="width:100%" /></el-form-item>
      <el-form-item label="单位"><el-input v-model="form.unit" placeholder="kg、件、箱等" /></el-form-item>
      <el-form-item label="用途/原因"><el-input v-model="form.reason" type="textarea" :rows="3" placeholder="说明采购用途和需要时间" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="submitting" @click="submit">提交</el-button></template>
  </el-dialog>

  <el-dialog v-model="processVisible" title="处理采购需求" width="560px">
    <p>{{ activeRequest?.request_no }} · {{ activeRequest?.item_name }} · {{ formatNumber(activeRequest?.quantity) }} {{ activeRequest?.unit }}</p>
    <el-alert v-if="activeRequest?.item_type === 'other'" type="warning" :closable="false" title="其他物品暂不能直接录入现有采购单明细；如需关联采购单，请先建立对应主数据。" />
    <el-form label-width="95px">
      <el-form-item label="采购订单"><el-select v-model="processForm.purchase_order_id" clearable filterable style="width:100%" placeholder="可选；关联已创建的采购订单"><el-option v-for="order in orders" :key="order.id" :value="order.id" :label="order.order_no" /></el-select></el-form-item>
      <el-form-item label="处理说明"><el-input v-model="processForm.process_note" type="textarea" :rows="3" placeholder="不关联采购订单将关闭需求，必须填写原因" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="processVisible = false">取消</el-button><el-button type="primary" :loading="submitting" @click="process">确认处理</el-button></template>
  </el-dialog>
</template>

<script setup>
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'

import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import ElAlert from 'element-plus/es/components/alert/index.mjs'
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElInputNumber from 'element-plus/es/components/input-number/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import { get, post } from '../api/client'
import PageSection from './PageSection.vue'
import { formatNumber } from '../utils/format'

const props = defineProps({ purchaseMode: { type: Boolean, default: false } })
const rows = ref([])
const materials = ref([])
const products = ref([])
const orders = ref([])
const createVisible = ref(false)
const masterDataLoading = ref(false)
const masterDataError = ref('')
const processVisible = ref(false)
const submitting = ref(false)
const activeRequest = ref(null)
const form = reactive({ item_type: 'material', material_id: null, product_id: null, item_name: '', specification: '', quantity: 1, unit: 'kg', reason: '' })
const processForm = reactive({ purchase_order_id: null, process_note: '' })
const visibleRows = computed(() => props.purchaseMode ? rows.value : rows.value)

async function load() {
  try { rows.value = await get('/api/purchases/department-requests') }
  catch (error) { ElMessage.error(error?.message || '采购需求加载失败') }
}

function resetItem() { form.material_id = null; form.product_id = null; form.item_name = ''; form.unit = form.item_type === 'other' ? '件' : 'kg' }
function syncUnit() {
  const row = form.item_type === 'material' ? materials.value.find(x => x.id === form.material_id) : products.value.find(x => x.id === form.product_id)
  form.unit = row?.unit || 'kg'
}
async function loadMasterData() {
  masterDataLoading.value = true
  masterDataError.value = ''
  try {
    const [materialRows, productRows] = await Promise.all([get('/api/master-data/materials'), get('/api/master-data/products')])
    materials.value = (Array.isArray(materialRows) ? materialRows : materialRows?.items || []).filter(x => x.is_active !== false)
    products.value = (Array.isArray(productRows) ? productRows : productRows?.items || []).filter(x => x.is_active !== false)
  } catch (error) {
    masterDataError.value = error?.message || '请稍后重试'
    ElMessage.error(`原料和产品加载失败：${masterDataError.value}`)
  } finally {
    masterDataLoading.value = false
  }
}
async function openCreate() {
  Object.assign(form, { item_type: 'material', material_id: null, product_id: null, item_name: '', specification: '', quantity: 1, unit: 'kg', reason: '' })
  createVisible.value = true
  await loadMasterData()
}
async function submit() {
  if ((form.item_type === 'material' && !form.material_id) || (form.item_type === 'product' && !form.product_id) || (form.item_type === 'other' && !form.item_name.trim())) { ElMessage.warning('请填写采购物品'); return }
  if (!form.reason.trim() || !form.unit.trim()) { ElMessage.warning('请填写用途和单位'); return }
  submitting.value = true
  try {
    await post('/api/purchases/department-requests', { ...form })
    ElMessage.success('采购需求已提交给采购人员')
    createVisible.value = false
    await load()
  } catch (error) { ElMessage.error(error?.message || '提交失败') }
  finally { submitting.value = false }
}
async function openProcess(row) {
  activeRequest.value = row
  Object.assign(processForm, { purchase_order_id: null, process_note: '' })
  try {
    const data = await get('/api/purchases/orders?tab=all&page=1&page_size=100')
    orders.value = Array.isArray(data) ? data : data?.items || []
    processVisible.value = true
  } catch (error) { ElMessage.error(error?.message || '采购订单加载失败') }
}
async function process() {
  submitting.value = true
  try {
    await post(`/api/purchases/department-requests/${activeRequest.value.id}/process`, { ...processForm })
    ElMessage.success('采购需求已处理')
    processVisible.value = false
    await load()
  } catch (error) { ElMessage.error(error?.message || '处理失败') }
  finally { submitting.value = false }
}
onMounted(load)
defineExpose({ openCreate, load })
</script>
